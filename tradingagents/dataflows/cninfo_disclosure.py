"""Structured CNINFO (巨潮资讯) announcement and IR survey metadata ingestion.

Implements C-05a requirements:
- Data source reuse: AKShare stock_zh_a_disclosure_report_cninfo and
  stock_zh_a_disclosure_relation_cninfo.
- Canonical event ID: extracted ONLY from native announcementId or URL query.
  Strictly forbids pseudo-hashes or source_hash substitution.
- Strict timestamp parsing: invalid or missing 公告时间 causes the row to be
  discarded with warning logging; no wall-clock/today fallbacks.
- Strict status classification: ok / confirmed_empty / provider_failure.
  Adapter crashes (e.g. KeyError on empty category) are classified as
  provider_failure, never confirmed_empty.
- Title-level metadata ONLY proves event existence; cannot be used to infer
  financial or operational conclusions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
import logging
import re
from typing import Any, Mapping
import urllib.parse

import pandas as pd

logger = logging.getLogger(__name__)

# Source types
SOURCE_TYPE_ANNOUNCEMENT = "cninfo_announcement"
SOURCE_TYPE_IR_SURVEY = "cninfo_ir_survey"

# Query envelope statuses
STATUS_OK = "ok"
STATUS_CONFIRMED_EMPTY = "confirmed_empty"
STATUS_PROVIDER_FAILURE = "provider_failure"

# Disclaimer semantics: Title metadata only proves event existence
DISCLAIMER_TEXT = (
    "巨潮标题级元数据仅用于证明公告/IR事件的存在性，不得据此下财务或经营结论。"
)

_REQUIRED_COLUMNS = {"代码", "公告标题", "公告时间"}
_CST_TZ = timezone(timedelta(hours=8))


@dataclass
class CninfoDisclosureRecord:
    """Structured record for CNINFO announcement or IR survey metadata.

    Attributes:
        symbol: 证券代码，按列名 `代码`.
        title: `公告标题`.
        announced_at: `公告时间`，严格解析为标准格式字符串.
        url: `公告链接`，规范化；缺失为 None.
        source_type: 'cninfo_announcement' 或 'cninfo_ir_survey'.
        cutoff_eligible: announced_at <= cutoff（纯日期含当日 23:59:59.999999）.
        announcement_id: 原生 ID 字符串；缺失为 None.
        canonical_event_id: 有原生 ID 则为 'cninfo:{announcementId}'；否则 None.
    """

    symbol: str
    title: str
    announced_at: str
    url: str | None
    source_type: str
    cutoff_eligible: bool
    announcement_id: str | None
    canonical_event_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CninfoDisclosureEnvelope:
    """Query envelope separating query status from record payload.

    Attributes:
        status: 'ok', 'confirmed_empty', or 'provider_failure'.
        records: list of structured disclosure records.
        error: error details on failure, otherwise None.
        source_type: data source identifier.
        disclaimer: explicit notice that metadata only proves event existence.
    """

    status: str
    records: list[CninfoDisclosureRecord] = field(default_factory=list)
    error: str | None = None
    source_type: str | None = None
    disclaimer: str = DISCLAIMER_TEXT

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def is_confirmed_empty(self) -> bool:
        return self.status == STATUS_CONFIRMED_EMPTY

    @property
    def is_failure(self) -> bool:
        return self.status == STATUS_PROVIDER_FAILURE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "records": [asdict(r) for r in self.records],
            "error": self.error,
            "source_type": self.source_type,
            "disclaimer": self.disclaimer,
        }


def normalize_url(raw_url: Any) -> str | None:
    """Normalize raw URL string, returning None if missing or invalid."""
    if raw_url is None or pd.isna(raw_url):
        return None
    url_str = str(raw_url).strip()
    if not url_str or url_str.lower() in ("none", "null", "nan", ""):
        return None
    return url_str


def extract_announcement_id(
    row: Mapping[str, Any] | pd.Series,
    url: str | None = None,
) -> str | None:
    """Extract native announcementId from row columns or URL query parameters.

    Strict rule: canonical ID can ONLY be extracted from native 'announcementId'
    column or normalized URL query parameter 'announcementId'. Never synthesizes
    title hash, source_hash, or orgId.
    """
    # 1. Direct column if present in row
    for col in ("announcementId", "announcement_id"):
        if col in row:
            raw_id = row[col]
            if raw_id is not None and not pd.isna(raw_id):
                val = str(raw_id).strip()
                if val and val.lower() not in ("none", "null", "nan"):
                    return val

    # 2. Extract from URL query parameter 'announcementId'
    target_url = url
    if not target_url:
        for col in ("公告链接", "url", "link"):
            if col in row:
                candidate = row[col]
                if candidate is not None and not pd.isna(candidate):
                    target_url = str(candidate).strip()
                    break

    if target_url and isinstance(target_url, str):
        try:
            parsed = urllib.parse.urlparse(target_url)
            qs = urllib.parse.parse_qs(parsed.query)
            if "announcementId" in qs and qs["announcementId"]:
                val = qs["announcementId"][0].strip()
                if val and val.lower() not in ("none", "null", "nan"):
                    return val
        except Exception:
            pass

    return None


def parse_announced_at(val: Any) -> tuple[datetime, str] | tuple[None, None]:
    """Strictly parse 公告时间 into naive datetime and formatted string.

    Never falls back to today or wall-clock time. Returns (None, None) on failure.
    """
    if val is None or pd.isna(val):
        return None, None

    if isinstance(val, datetime):
        dt = val
        if dt.tzinfo is not None:
            dt = dt.astimezone(_CST_TZ).replace(tzinfo=None)
        fmt = "%Y-%m-%d %H:%M:%S" if (dt.hour or dt.minute or dt.second or dt.microsecond) else "%Y-%m-%d"
        return dt, dt.strftime(fmt)

    text = str(val).strip()
    if not text or text.lower() in ("none", "null", "nan", "nat", "未知", "unknown"):
        return None, None

    # Standard explicit datetime formats
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%Y年%m月%d日 %H:%M:%S",
        "%Y年%m月%d日 %H:%M",
        "%Y年%m月%d日",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            out_fmt = "%Y-%m-%d %H:%M:%S" if (" " in text or ":" in text) else "%Y-%m-%d"
            return dt, dt.strftime(out_fmt)
        except ValueError:
            continue

    # Try ISO fromisoformat
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(_CST_TZ).replace(tzinfo=None)
        out_fmt = "%Y-%m-%d %H:%M:%S" if (dt.hour or dt.minute or dt.second or dt.microsecond) else "%Y-%m-%d"
        return dt, dt.strftime(out_fmt)
    except Exception:
        pass

    return None, None


def parse_cutoff_datetime(cutoff: Any) -> datetime | None:
    """Parse cutoff date or datetime string into a boundary datetime.

    If cutoff is a pure date, the boundary includes the entire day (23:59:59.999999).
    """
    if cutoff is None:
        return None

    if isinstance(cutoff, datetime):
        if cutoff.tzinfo is not None:
            cutoff = cutoff.astimezone(_CST_TZ).replace(tzinfo=None)
        return cutoff

    if hasattr(cutoff, "year") and hasattr(cutoff, "month") and hasattr(cutoff, "day") and not hasattr(cutoff, "hour"):
        return datetime.combine(cutoff, time.max)

    text = str(cutoff).strip()
    if not text or text.lower() in ("none", "null", "nan"):
        return None

    # 8-digit date YYYYMMDD
    if re.match(r"^\d{8}$", text):
        try:
            d = datetime.strptime(text, "%Y%m%d").date()
            return datetime.combine(d, time.max)
        except ValueError:
            return None

    # Pure date format YYYY-MM-DD, YYYY/MM/DD, YYYY年MM月DD日
    if re.match(r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?$", text):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
            try:
                d = datetime.strptime(text, fmt).date()
                return datetime.combine(d, time.max)
            except ValueError:
                continue

    # Date with time
    dt, _ = parse_announced_at(text)
    return dt


def build_cninfo_record(
    row: Mapping[str, Any] | pd.Series,
    source_type: str,
    cutoff: str | None = None,
) -> CninfoDisclosureRecord | None:
    """Build a CninfoDisclosureRecord from a DataFrame row.

    Returns None if essential columns or timestamp are unparseable.
    """
    if "代码" not in row or "公告标题" not in row or "公告时间" not in row:
        logger.warning("Skipping row missing essential columns: %s", row)
        return None

    symbol = str(row["代码"]).strip()
    title = str(row["公告标题"]).strip()

    # Strict announced_at parsing: discard row if invalid
    raw_time = row["公告时间"]
    announced_dt, announced_str = parse_announced_at(raw_time)
    if announced_dt is None or announced_str is None:
        logger.warning(
            "Discarding row due to missing/unparseable 公告时间: raw=%r, symbol=%s, title=%s",
            raw_time,
            symbol,
            title,
        )
        return None

    # URL normalization
    raw_url = row.get("公告链接") if "公告链接" in row else row.get("url")
    url = normalize_url(raw_url)

    # Native announcement ID extraction
    announcement_id = extract_announcement_id(row, url=url)
    canonical_event_id = f"cninfo:{announcement_id}" if announcement_id else None

    # Cutoff eligibility check
    if cutoff is None:
        cutoff_eligible = True
    else:
        cutoff_dt = parse_cutoff_datetime(cutoff)
        if cutoff_dt is not None:
            cutoff_eligible = (announced_dt <= cutoff_dt)
        else:
            cutoff_eligible = False

    return CninfoDisclosureRecord(
        symbol=symbol,
        title=title,
        announced_at=announced_str,
        url=url,
        source_type=source_type,
        cutoff_eligible=cutoff_eligible,
        announcement_id=announcement_id,
        canonical_event_id=canonical_event_id,
    )


def parse_cninfo_disclosure_df(
    df: Any,
    source_type: str,
    cutoff: str | None = None,
) -> CninfoDisclosureEnvelope:
    """Parse raw AKShare disclosure DataFrame into CninfoDisclosureEnvelope.

    Classifies result into:
    - 'ok': successful query with parsed records.
    - 'confirmed_empty': DataFrame returned with all expected columns but zero rows.
    - 'provider_failure': missing columns, None input, or all rows unparseable.
    """
    if df is None:
        return CninfoDisclosureEnvelope(
            status=STATUS_PROVIDER_FAILURE,
            records=[],
            error="AKShare returned None",
            source_type=source_type,
        )

    if not isinstance(df, pd.DataFrame):
        return CninfoDisclosureEnvelope(
            status=STATUS_PROVIDER_FAILURE,
            records=[],
            error=f"Expected pd.DataFrame, got {type(df).__name__}",
            source_type=source_type,
        )

    # Check required columns
    missing_cols = _REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        return CninfoDisclosureEnvelope(
            status=STATUS_PROVIDER_FAILURE,
            records=[],
            error=f"DataFrame missing required columns: {sorted(missing_cols)}",
            source_type=source_type,
        )

    # Confirmed empty: expected schema present and empty
    if df.empty:
        return CninfoDisclosureEnvelope(
            status=STATUS_CONFIRMED_EMPTY,
            records=[],
            source_type=source_type,
        )

    # Parse each row by column name (no positional slicing)
    records: list[CninfoDisclosureRecord] = []
    for _, row in df.iterrows():
        rec = build_cninfo_record(row, source_type=source_type, cutoff=cutoff)
        if rec is not None:
            records.append(rec)

    # If df had rows but all were dropped due to timestamp corruption, report failure
    if not records and len(df) > 0:
        return CninfoDisclosureEnvelope(
            status=STATUS_PROVIDER_FAILURE,
            records=[],
            error="All rows failed 公告时间 parsing; unverifiable data",
            source_type=source_type,
        )

    return CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=records,
        source_type=source_type,
    )
