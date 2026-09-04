"""Structured CNINFO (巨潮资讯) announcement and IR survey metadata ingestion.

Implements C-05a & C-05b requirements:
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
- Single announcement/IR content qualification and hashing (C-05b):
  - Retain official adjunctUrl from hisAnnouncement/query response without inventing
    static.cninfo.com.cn/finalpage/... formulas.
  - Contract fields: content_status ('hashed' | 'unavailable' | 'not_attempted'),
    content_sha256 (64-char hex or None).
  - Strict verification: bytes must start with magic %PDF; sha256 computed on bytes.
  - content_bytes never written to logs/prompt/test goldens.
  - Missing announcementId: canonical_event_id is None, cannot be hashed.
  - 403 / Timeout / non-2xx / KeyError -> content_status=unavailable / provider_failure,
    never confirmed_empty.
  - Cutoff enforcement: announced_at <= cutoff required for qualification.
  - Existence semantics: title metadata proves existence only; hash != extracted net profit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
import hashlib
import logging
import re
from typing import Any, Mapping
import urllib.parse

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# Source types
SOURCE_TYPE_ANNOUNCEMENT = "cninfo_announcement"
SOURCE_TYPE_IR_SURVEY = "cninfo_ir_survey"

# Query envelope statuses
STATUS_OK = "ok"
STATUS_CONFIRMED_EMPTY = "confirmed_empty"
STATUS_PROVIDER_FAILURE = "provider_failure"

# Content qualification statuses (C-05b)
CONTENT_STATUS_HASHED = "hashed"
CONTENT_STATUS_UNAVAILABLE = "unavailable"
CONTENT_STATUS_NOT_ATTEMPTED = "not_attempted"

_VALID_CONTENT_STATUSES = {
    CONTENT_STATUS_HASHED,
    CONTENT_STATUS_UNAVAILABLE,
    CONTENT_STATUS_NOT_ATTEMPTED,
}

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
        adjunct_url: 官方附件/PDF 下载 URL；无官方字段则为 None，严禁编造公式.
        content_status: 'hashed' | 'unavailable' | 'not_attempted'.
        content_sha256: 64-char hex SHA256 (仅 hashed 时有效)；否则 None.
    """

    symbol: str
    title: str
    announced_at: str
    url: str | None
    source_type: str
    cutoff_eligible: bool
    announcement_id: str | None
    canonical_event_id: str | None
    adjunct_url: str | None = None
    content_status: str = CONTENT_STATUS_NOT_ATTEMPTED
    content_sha256: str | None = None

    def qualify_content(
        self,
        *,
        content_bytes: bytes | None = None,
        fetch_fn: Any | None = None,
        session: Any | None = None,
        timeout: float = 10.0,
        cutoff: str | None = None,
    ) -> CninfoDisclosureRecord:
        return qualify_cninfo_content(
            self,
            content_bytes=content_bytes,
            fetch_fn=fetch_fn,
            session=session,
            timeout=timeout,
            cutoff=cutoff,
        )

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


def resolve_adjunct_url(raw_adjunct: Any) -> str | None:
    """Normalize and resolve raw adjunct URL to full URL without inventing formulas.

    Strict rule: Only accepts non-empty string or official relative path from
    cninfo hisAnnouncement/query response (e.g. 'finalpage/2026-08-01/123.PDF').
    Never invents static.cninfo.com.cn/finalpage/... formulas from scratch.
    """
    if raw_adjunct is None or pd.isna(raw_adjunct):
        return None
    text = str(raw_adjunct).strip()
    if not text or text.lower() in ("none", "null", "nan", ""):
        return None
    if text.startswith(("http://", "https://")):
        return text
    # Official relative path from cninfo hisAnnouncement/query e.g. finalpage/2026-08-01/123.PDF
    if text.startswith("/"):
        return f"http://static.cninfo.com.cn{text}"
    return f"http://static.cninfo.com.cn/{text}"


def extract_adjunct_url(
    row: Mapping[str, Any] | pd.Series,
    url: str | None = None,
) -> str | None:
    """Extract official adjunct/PDF URL from row fields or verified direct link.

    Strict rule: Never invents static.cninfo.com.cn/finalpage/... formulas.
    URL must come from response field (adjunctUrl) or verified attachment in URL.
    """
    for col in ("adjunctUrl", "adjunct_url", "pdf_url", "adjunct", "附件链接"):
        if col in row:
            val = resolve_adjunct_url(row[col])
            if val:
                return val

    # Check if URL itself is a verified direct attachment link ending with PDF
    if url and isinstance(url, str):
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.path.lower().endswith((".pdf", ".doc", ".docx")):
                return url
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

    # Adjunct URL extraction (from row field, never fabricated)
    adjunct_url = extract_adjunct_url(row, url=url)

    # Cutoff eligibility check
    if cutoff is None:
        cutoff_eligible = True
    else:
        cutoff_dt = parse_cutoff_datetime(cutoff)
        if cutoff_dt is not None:
            cutoff_eligible = (announced_dt <= cutoff_dt)
        else:
            cutoff_eligible = False

    # Content status handling
    raw_status = row.get("content_status", CONTENT_STATUS_NOT_ATTEMPTED) if "content_status" in row else CONTENT_STATUS_NOT_ATTEMPTED
    status_str = str(raw_status).strip() if raw_status is not None and not pd.isna(raw_status) else CONTENT_STATUS_NOT_ATTEMPTED
    content_status = status_str if status_str in _VALID_CONTENT_STATUSES else CONTENT_STATUS_NOT_ATTEMPTED

    raw_sha = row.get("content_sha256", None) if "content_sha256" in row else None
    content_sha256 = str(raw_sha).strip() if raw_sha is not None and not pd.isna(raw_sha) else None
    if content_status != CONTENT_STATUS_HASHED:
        content_sha256 = None

    return CninfoDisclosureRecord(
        symbol=symbol,
        title=title,
        announced_at=announced_str,
        url=url,
        source_type=source_type,
        cutoff_eligible=cutoff_eligible,
        announcement_id=announcement_id,
        canonical_event_id=canonical_event_id,
        adjunct_url=adjunct_url,
        content_status=content_status,
        content_sha256=content_sha256,
    )


CNINFO_CATEGORY_DICT: dict[str, str] = {
    "年报": "category_ndbg_szsh",
    "半年报": "category_bndbg_szsh",
    "一季报": "category_yjdbg_szsh",
    "三季报": "category_sjdbg_szsh",
    "业绩预告": "category_yjygjxz_szsh",
    "权益分派": "category_qyfpxzcs_szsh",
    "董事会": "category_dshgg_szsh",
    "监事会": "category_jshgg_szsh",
    "股东大会": "category_gddh_szsh",
    "日常经营": "category_rcjy_szsh",
    "公司治理": "category_gszl_szsh",
    "中介报告": "category_zj_szsh",
    "首发": "category_sf_szsh",
    "增发": "category_zf_szsh",
    "股权激励": "category_gqjl_szsh",
    "配股": "category_pg_szsh",
    "解禁": "category_jj_szsh",
    "公司债": "category_gszq_szsh",
    "可转债": "category_kzzq_szsh",
    "其他融资": "category_qtrz_szsh",
    "股权变动": "category_gqbd_szsh",
    "补充更正": "category_bcgz_szsh",
    "澄清致歉": "category_cqdq_szsh",
    "风险提示": "category_fxts_szsh",
    "特别处理和退市": "category_tbclts_szsh",
    "退市整理期": "category_tszlq_szsh",
}


def attach_adjunct_url_to_df(
    df: pd.DataFrame,
    raw_announcements: list[dict[str, Any]],
) -> pd.DataFrame:
    """Attach official adjunctUrl to DataFrame rows matching announcementId or title.

    Strict rule (C-05b):
    Only populates adjunctUrl from official field present in raw_announcements.
    If adjunctUrl is missing from the query response, it remains None.
    Never invents static.cninfo.com.cn/finalpage/... formulas.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or not raw_announcements:
        return df

    # Build lookup by announcementId and title
    id_map: dict[str, str] = {}
    title_map: dict[tuple[str, str], str] = {}

    for item in raw_announcements:
        if not isinstance(item, dict):
            continue
        raw_adj = item.get("adjunctUrl") or item.get("adjunct_url") or item.get("pdf_url")
        if raw_adj is not None and not pd.isna(raw_adj):
            adj_str = str(raw_adj).strip()
            if adj_str and adj_str.lower() not in ("none", "null", "nan", ""):
                # 1. Map by announcementId
                aid = item.get("announcementId") or item.get("announcement_id") or item.get("id")
                if aid is not None and not pd.isna(aid):
                    aid_str = str(aid).strip()
                    if aid_str:
                        id_map[aid_str] = adj_str
                # 2. Map by (secCode, announcementTitle)
                code = str(item.get("secCode") or item.get("code") or "").strip()
                title = str(item.get("announcementTitle") or item.get("title") or "").strip()
                if title:
                    title_map[(code, title)] = adj_str
                    if code:
                        title_map[("", title)] = adj_str

    if not id_map and not title_map:
        return df

    df = df.copy()
    if "adjunctUrl" not in df.columns:
        df["adjunctUrl"] = None

    for idx, row in df.iterrows():
        current_val = row.get("adjunctUrl")
        if current_val is not None and not pd.isna(current_val) and str(current_val).strip():
            continue

        aid = extract_announcement_id(row)
        matched_adj = None
        if aid and aid in id_map:
            matched_adj = id_map[aid]
        else:
            row_code = str(row.get("代码") or "").strip()
            row_title = str(row.get("公告标题") or "").strip()
            if (row_code, row_title) in title_map:
                matched_adj = title_map[(row_code, row_title)]
            elif ("", row_title) in title_map:
                matched_adj = title_map[("", row_title)]

        if matched_adj:
            df.at[idx, "adjunctUrl"] = matched_adj

    return df


def query_cninfo_raw_announcements(
    symbol: str = "",
    market: str = "沪深京",
    keyword: str = "",
    category: str = "",
    start_date: str = "",
    end_date: str = "",
    tab_name: str = "fulltext",
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """Query cninfo hisAnnouncement/query endpoint for raw announcement dicts with adjunctUrl.

    Provides the underlying query JSON when AKShare 1.18.30 drops adjunctUrl or when
    underlying endpoint is mocked in testing.
    """
    column_map = {
        "沪深京": "szse",
        "港股": "hke",
        "三板": "third",
        "基金": "fund",
        "债券": "bond",
        "监管": "regulator",
        "预披露": "pre_disclosure",
    }
    col = column_map.get(market, "szse")
    stock_item = symbol
    cat_item = CNINFO_CATEGORY_DICT.get(category, category)

    if start_date and len(start_date) == 8:
        se_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}~{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
    else:
        se_date = f"{start_date}~{end_date}" if (start_date or end_date) else ""

    payload = {
        "pageNum": "1",
        "pageSize": "30",
        "column": col,
        "tabName": tab_name,
        "plate": "",
        "stock": stock_item,
        "searchkey": keyword,
        "secid": "",
        "category": cat_item,
        "trade": "",
        "seDate": se_date,
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    try:
        r = requests.post(url, data=payload, timeout=timeout)
        data = r.json() if hasattr(r, "json") else None
        if callable(data):
            data = data()
        if isinstance(data, dict):
            anns = data.get("announcements")
            if isinstance(anns, list):
                return anns
    except Exception as exc:
        logger.debug("query_cninfo_raw_announcements request failed: %s", exc)
    return []


def parse_cninfo_disclosure_df(
    df: Any,
    source_type: str,
    cutoff: str | None = None,
    raw_announcements: list[dict[str, Any]] | None = None,
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

    # Attach raw adjunctUrl if supplied
    if raw_announcements:
        df = attach_adjunct_url_to_df(df, raw_announcements)

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


def qualify_cninfo_content(
    target: CninfoDisclosureRecord | CninfoDisclosureEnvelope,
    *,
    record_index: int = 0,
    content_bytes: bytes | None = None,
    fetch_fn: Any | None = None,
    session: Any | None = None,
    timeout: float = 10.0,
    cutoff: str | None = None,
) -> CninfoDisclosureRecord | CninfoDisclosureEnvelope:
    """Qualify content eligibility for a single announcement/IR record and compute SHA256.

    Strict rules (C-05b):
    1. Missing native announcementId: canonical_event_id is None, content_status=unavailable,
       cannot enter hashed.
    2. Missing adjunct/PDF URL: content_status=unavailable; never invents static.cninfo URL formulas.
    3. announced_at > cutoff: content_status=unavailable.
    4. 403 / Timeout / HTTP non-2xx / KeyError: content_status=unavailable, never confirmed_empty.
    5. Non-PDF bytes (missing %PDF magic header): content_status=unavailable.
    6. Valid %PDF bytes + announcementId + adjunctUrl: content_status=hashed,
       content_sha256=hashlib.sha256(bytes).hexdigest() (64 hex characters).
    7. content_bytes are NEVER stored in record, logs, or prompt.
    """
    if isinstance(target, CninfoDisclosureEnvelope):
        if target.records and 0 <= record_index < len(target.records):
            qualify_cninfo_content(
                target.records[record_index],
                content_bytes=content_bytes,
                fetch_fn=fetch_fn,
                session=session,
                timeout=timeout,
                cutoff=cutoff,
            )
        return target

    record = target

    # Rule 1: Missing native announcementId
    if not record.announcement_id or not record.canonical_event_id:
        record.content_status = CONTENT_STATUS_UNAVAILABLE
        record.content_sha256 = None
        return record

    # Rule 2: Cutoff eligibility
    effective_cutoff = cutoff
    if effective_cutoff is not None:
        cutoff_dt = parse_cutoff_datetime(effective_cutoff)
        announced_dt, _ = parse_announced_at(record.announced_at)
        if cutoff_dt is not None and announced_dt is not None:
            if announced_dt > cutoff_dt:
                record.cutoff_eligible = False
                record.content_status = CONTENT_STATUS_UNAVAILABLE
                record.content_sha256 = None
                return record
    elif not record.cutoff_eligible:
        record.content_status = CONTENT_STATUS_UNAVAILABLE
        record.content_sha256 = None
        return record

    # Rule 3: Missing adjunct_url (no formula fabrication)
    if not record.adjunct_url:
        record.content_status = CONTENT_STATUS_UNAVAILABLE
        record.content_sha256 = None
        return record

    # Rule 4: Fetch bytes
    raw_bytes: bytes | None = None
    if content_bytes is not None:
        raw_bytes = content_bytes
    elif fetch_fn is not None:
        try:
            raw_bytes = fetch_fn(record.adjunct_url)
        except Exception as exc:
            logger.warning(
                "PDF fetch_fn failed (%s: %s) for url=%s",
                type(exc).__name__,
                exc,
                record.adjunct_url,
            )
            record.content_status = CONTENT_STATUS_UNAVAILABLE
            record.content_sha256 = None
            return record
    else:
        try:
            import requests
            s = session or requests
            resp = s.get(
                record.adjunct_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=timeout,
            )
            if resp.status_code != 200:
                logger.warning(
                    "PDF download returned HTTP %s for url=%s",
                    resp.status_code,
                    record.adjunct_url,
                )
                record.content_status = CONTENT_STATUS_UNAVAILABLE
                record.content_sha256 = None
                return record
            raw_bytes = resp.content
        except Exception as exc:
            logger.warning(
                "PDF download failed (%s: %s) for url=%s",
                type(exc).__name__,
                exc,
                record.adjunct_url,
            )
            record.content_status = CONTENT_STATUS_UNAVAILABLE
            record.content_sha256 = None
            return record

    # Rule 5: Non-PDF check (magic header %PDF)
    if not raw_bytes or not raw_bytes.startswith(b"%PDF"):
        logger.warning(
            "Content for url=%s does not start with magic %%PDF",
            record.adjunct_url,
        )
        record.content_status = CONTENT_STATUS_UNAVAILABLE
        record.content_sha256 = None
        return record

    # Rule 6: Success -> hashed, sha256 64-hex
    record.content_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    record.content_status = CONTENT_STATUS_HASHED
    return record


qualify_and_hash_cninfo_content = qualify_cninfo_content
