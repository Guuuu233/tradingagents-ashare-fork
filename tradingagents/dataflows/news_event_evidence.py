"""Structured news event evidence, EventCluster deduplication, and coverage tracking.

Implements D-009 / P1-1 requirements:
- NewsEvidence: strict publication timestamp verification (no ingest/today fallback).
- D-008 time tier semantics: published_at is content time; first_seen_at is audit/archive only.
- EventCluster: deterministic clustering & deduplication across entity/theme/time window.
- event_coverage: verifiable coverage tracking with fail-closed gap semantics
  (suspected_gaps must state "未检索到/不可验证", strictly forbidding "确认无相关新闻").
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
import hashlib
import json
import logging
import re
from typing import Any, Iterable, Mapping, Sequence
import urllib.parse

logger = logging.getLogger(__name__)

DEFAULT_NEWS_WINDOW = "14天"
GAP_UNVERIFIABLE_MESSAGE = "未检索到/不可验证"
RECALL_STATUS_UNKNOWN = "unknown"
RECALL_STATUS_PARTIAL_VS_MANIFEST = "partial_vs_manifest"
RECALL_STATUS_PROVIDER_FAILURE = "provider_failure"
LEGACY_DEFAULT_THEMES = ("跨市场", "财报", "行业政策", "公司治理", "重大合同")

_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "跨市场": ("跨市场", "美股", "港股", "汇率", "关税", "外盘", "全球", "海外", "a50", "nasdaq", "标普"),
    "财报": ("财报", "业绩", "中报", "年报", "一季报", "三季报", "净利润", "营业收入", "营收", "扭亏", "预告", "快报", "分红"),
    "行业政策": ("政策", "工信部", "发改委", "国务院", "证监会", "补贴", "产业", "规划", "新政", "监管", "指导意见"),
    "公司治理": ("减持", "增持", "回购", "质押", "解禁", "立案", "问询函", "违规", "高管", "董事会", "监事会"),
    "重大合同": ("中标", "合同", "订单", "协议", "签约", "重大合作", "战略合作", "采购", "供货"),
    "产品与业务": ("新品", "投产", "获批", "发布", "项目", "技术突破", "产能", "量产", "研发"),
}

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M:%S",
    "%Y年%m月%d日 %H:%M",
    "%Y年%m月%d日",
)


def parse_datetime_or_none(value: Any) -> datetime | None:
    """Strictly parse datetime from string/number/datetime; returns None on failure.

    No today/now/ingest fallback is allowed.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    text = str(value).strip()
    if not text or text.lower() in ("none", "null", "nan", "未知", "unknown", "nat"):
        return None

    # Try standard datetime formats
    for fmt in _DATETIME_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            continue

    # Try ISO fromisoformat
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        pass

    return None


def parse_cutoff_datetime(cutoff: Any) -> datetime | None:
    """Parse cutoff date or datetime string into a boundary datetime."""
    if cutoff is None:
        return None
    if isinstance(cutoff, datetime):
        if cutoff.tzinfo is not None:
            return cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        return cutoff
    text = str(cutoff).strip()
    if not text:
        return None

    # If cutoff is pure YYYY-MM-DD date, include entire date (up to 23:59:59.999999)
    if re.match(r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?$", text):
        base_dt = parse_datetime_or_none(text)
        if base_dt is not None:
            return datetime.combine(base_dt.date(), time.max)

    return parse_datetime_or_none(text)


def infer_theme_from_text(title: str, summary: str = "") -> str:
    """Infer news theme from title and summary content."""
    combined = f"{title} {summary}".lower()
    for theme, keywords in _THEME_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return theme
    return "综合新闻"


def normalize_url(url: Any) -> str | None:
    """Normalize news URL: trim, remove fragment. Returns None on failure or missing.

    Strictly forbids filling default URL or empty string.
    """
    if url is None or isinstance(url, bool):
        return None
    text = str(url).strip()
    if not text or text.lower() in ("none", "null", "nan", "unknown", "未知"):
        return None
    try:
        parsed = urllib.parse.urlsplit(text)
        _ = parsed.port
        cleaned = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
        ).strip()
        if not cleaned:
            return None
        return cleaned
    except Exception:
        return None


def extract_url_from_raw(raw_dict: Mapping[str, Any]) -> str | None:
    """Extract raw URL from news item dictionary using known column names.

    Returns None if missing, empty, or nan.
    """
    for key in ("url", "链接", "link", "新闻链接", "news_url", "URL", "Link"):
        val = raw_dict.get(key)
        if val is not None and not isinstance(val, bool):
            text = str(val).strip()
            if text and text.lower() not in ("none", "null", "nan", "unknown", "未知"):
                return text
    return None


def normalize_title_for_dedupe(title: str) -> str:
    """Normalize news title for fuzzy matching and deduplication."""
    t = str(title or "").strip().lower()
    # Remove leading source prefixes like "东方财富：", "【证券时报】"
    t = re.sub(r"^【[^】]+】", "", t)
    t = re.sub(r"^[^\s：:]+[：:]\s*", "", t)
    # Remove trailing updates like "（更新）", "(更新)"
    t = re.sub(r"[\(（]更新[\)）]", "", t)
    # Remove punctuation and whitespace
    t = re.sub(r"[^\w一-龥]+", "", t)
    return t


def compute_source_hash(
    source: str,
    title: str,
    published_at: str,
    summary: str = "",
    url: str | None = None,
) -> str:
    """Compute deterministic hash for source verification and deduplication.

    When normalized URL is provided, aligns cross-source hash (determined by URL, DAV-612).
    When URL is absent, preserves legacy title/source/published_at/summary behavior.
    """
    norm_url = normalize_url(url)
    if norm_url:
        raw = f"url:{norm_url}"
    else:
        raw = f"{str(source).strip()}:{str(title).strip()}:{str(published_at).strip()}:{str(summary).strip()[:100]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]



# Collateral source types
SOURCE_TYPE_TUSHARE_FORECAST = "tushare_forecast"
SOURCE_TYPE_TUSHARE_REPURCHASE = "tushare_repurchase"
SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE = "tushare_disclosure_date"
COLLATERAL_SOURCE_TYPES = (
    SOURCE_TYPE_TUSHARE_FORECAST,
    SOURCE_TYPE_TUSHARE_REPURCHASE,
    SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
)

FORECAST_TITLE_KEYWORDS = ("预告", "业绩", "中报", "年报")
REPURCHASE_TITLE_KEYWORDS = ("回购", "股份变动")
DISCLOSURE_DATE_TITLE_KEYWORDS = (
    "定期报告披露日期",
    "变更披露日期",
    "披露日期",
    "预约披露",
    "变更定期报告披露日期",
    "定期报告预约披露",
    "定期报告披露时间",
)


@dataclass(frozen=True)
class CollateralRecord:
    """Read-only structured collateral evidence from private gateway.

    Enforces DAV-632 / C-05 contract:
    - canonical_event_id is ALWAYS None (strictly forbids inventing cninfo ID).
    - collateral_id carries a dedicated vendor namespace.
    """

    symbol: str
    ann_date: str                     # YYYY-MM-DD
    source_type: str                  # 'tushare_forecast' | 'tushare_repurchase' | 'tushare_disclosure_date'
    collateral_id: str                # e.g. "tushare:forecast:000001.SZ:20250120:20241231"
    payload: dict[str, Any] = field(default_factory=dict)
    canonical_event_id: None = None   # MUST be None

    def __post_init__(self):
        if self.canonical_event_id is not None:
            raise ValueError(
                "CollateralRecord strictly forbids canonical_event_id; MUST be None"
            )
        norm_date = parse_datetime_or_none(self.ann_date)
        if norm_date:
            object.__setattr__(self, "ann_date", norm_date.strftime("%Y-%m-%d"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CollateralRecord:
        d = dict(data)
        symbol = str(d.get("symbol") or d.get("ts_code") or "").strip()
        ann_date = str(d.get("ann_date") or "").strip()
        source_type = str(d.get("source_type") or "").strip()
        collateral_id = str(d.get("collateral_id") or "").strip()
        if not collateral_id and source_type:
            short_src = source_type.replace("tushare_", "")
            collateral_id = f"tushare:{short_src}:{symbol}:{ann_date}"
        payload = d.get("payload")
        if not isinstance(payload, dict):
            payload = {
                k: v for k, v in d.items()
                if k not in (
                    "symbol", "ts_code", "ann_date", "source_type",
                    "collateral_id", "canonical_event_id"
                )
            }
        return cls(
            symbol=symbol,
            ann_date=ann_date,
            source_type=source_type,
            collateral_id=collateral_id,
            payload=payload,
            canonical_event_id=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ann_date": self.ann_date,
            "source_type": self.source_type,
            "collateral_id": self.collateral_id,
            "payload": dict(self.payload) if isinstance(self.payload, dict) else self.payload,
            "canonical_event_id": None,
        }


@dataclass
class CollateralEnvelope:
    """Query envelope for private gateway collateral data stream.

    Enforces C-05 contract:
    - Separates query status from record payload.
    - is_confirmed_empty is ALWAYS False (strictly forbids setting True).
    """

    status: str                       # 'ok' | 'collateral_empty' | 'provider_failure' | 'schema_drift'
    source_type: str                  # 'tushare_forecast' | 'tushare_repurchase' | 'tushare_disclosure_date'
    records: list[CollateralRecord] = field(default_factory=list)
    error: str | None = None
    category: str | None = None
    disclaimer: str = "结构化旁证仅供决策参考，不替代法定巨潮主源，不得据此断定确认无公告。"

    @property
    def is_confirmed_empty(self) -> bool:
        return False

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_type": self.source_type,
            "records": [r.to_dict() for r in self.records],
            "error": self.error,
            "category": self.category,
            "disclaimer": self.disclaimer,
            "is_confirmed_empty": False,
        }


def normalize_symbol(symbol: Any) -> str:
    """Normalize A-share stock symbol by stripping whitespace and market suffixes/prefixes."""
    if not symbol:
        return ""
    s = str(symbol).strip().upper()
    s = re.sub(r"\.(SH|SZ|BJ|OF)$", "", s)
    m = re.match(r"^(?:SH|SZ|BJ)?(\d{6})$", s)
    if m:
        return m.group(1)
    return s


def check_theme_match(title: str, source_type: str) -> bool:
    """Check whether primary CNINFO announcement title matches collateral source type.

    Implements §3.3.2 soft alignment theme matching:
    - tushare_forecast: title hits 预告/业绩/中报/年报
    - tushare_repurchase: title hits 回购/股份变动
    - tushare_disclosure_date: title hits 定期报告披露日期/变更披露日期
    """
    if not title or not source_type:
        return False
    t = str(title).strip()
    src = str(source_type).strip()

    if src == SOURCE_TYPE_TUSHARE_FORECAST:
        return any(kw in t for kw in FORECAST_TITLE_KEYWORDS)
    elif src == SOURCE_TYPE_TUSHARE_REPURCHASE:
        return any(kw in t for kw in REPURCHASE_TITLE_KEYWORDS)
    elif src == SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE:
        if any(kw in t for kw in DISCLOSURE_DATE_TITLE_KEYWORDS):
            return True
        if "定期报告" in t and ("披露" in t or "预约" in t):
            return True
        return False
    return False


def check_soft_alignment(
    primary: Any,
    collateral: CollateralRecord | Mapping[str, Any],
    tolerance_days: int = 1,
) -> bool:
    """Check whether primary CNINFO evidence and collateral record satisfy soft alignment.

    Three elements of soft alignment (§3.3.2):
    1. Symbol alignment: normalize_symbol(primary.symbol) == normalize_symbol(collateral.symbol).
    2. Date tolerance window: |primary.announced_at.date - collateral.ann_date| <= tolerance_days (default 1).
    3. Theme feature alignment: check_theme_match(primary.title, collateral.source_type).
    """
    # 1. Symbol alignment
    p_symbol = (
        getattr(primary, "entity", None)
        or getattr(primary, "symbol", None)
        or (primary.get("entity") if isinstance(primary, dict) else None)
        or (primary.get("symbol") if isinstance(primary, dict) else None)
        or ""
    )
    c_symbol = (
        getattr(collateral, "symbol", None)
        or (collateral.get("symbol") if isinstance(collateral, dict) else None)
        or (collateral.get("ts_code") if isinstance(collateral, dict) else None)
        or ""
    )
    if not normalize_symbol(p_symbol) or not normalize_symbol(c_symbol):
        return False
    if normalize_symbol(p_symbol) != normalize_symbol(c_symbol):
        return False

    # 2. Date tolerance window
    p_pub = (
        getattr(primary, "published_at", None)
        or getattr(primary, "announced_at", None)
        or (primary.get("published_at") if isinstance(primary, dict) else None)
        or (primary.get("announced_at") if isinstance(primary, dict) else None)
        or ""
    )
    p_dt = parse_datetime_or_none(p_pub)
    if p_dt is None:
        return False

    c_ann = (
        getattr(collateral, "ann_date", None)
        or (collateral.get("ann_date") if isinstance(collateral, dict) else None)
        or ""
    )
    c_dt = parse_datetime_or_none(c_ann)
    if c_dt is None:
        return False

    days_diff = abs((p_dt.date() - c_dt.date()).days)
    if days_diff > tolerance_days:
        return False

    # 3. Theme feature alignment
    p_title = (
        getattr(primary, "title", None)
        or (primary.get("title") if isinstance(primary, dict) else None)
        or ""
    )
    c_src = (
        getattr(collateral, "source_type", None)
        or (collateral.get("source_type") if isinstance(collateral, dict) else None)
        or ""
    )
    if not check_theme_match(p_title, c_src):
        return False

    return True


def attach_collateral_to_evidence(
    primary: NewsEvidence,
    collateral: CollateralRecord | Mapping[str, Any],
    tolerance_days: int = 1,
) -> bool:
    """Soft-align and attach a CollateralRecord to primary NewsEvidence.

    Returns True if aligned and attached; False otherwise.
    Strictly preserves primary.canonical_event_id.
    """
    col = collateral if isinstance(collateral, CollateralRecord) else CollateralRecord.from_dict(collateral)
    if check_soft_alignment(primary, col, tolerance_days=tolerance_days):
        primary.attach_collateral(col)
        return True
    return False


def attach_collaterals_to_evidences(
    evidences: Sequence[NewsEvidence],
    collaterals: Sequence[CollateralRecord | Mapping[str, Any]],
    tolerance_days: int = 1,
    retain_unattached: bool = True,
    default_entity: str = "",
) -> tuple[list[NewsEvidence], list[CollateralRecord]]:
    """Attach collateral records to matching primary NewsEvidence items.

    Enforces:
    1. Three soft alignment elements: symbol, date within tolerance_days, theme match.
    2. Preserves canonical_event_id on primary and strictly None on collateral.
    3. Retains unattached collaterals as independent NewsEvidence with '[结构化旁证]' label
       when retain_unattached=True.
    """
    ev_list = list(evidences)
    col_objs = [
        c if isinstance(c, CollateralRecord) else CollateralRecord.from_dict(c)
        for c in collaterals
    ]

    unattached: list[CollateralRecord] = []

    for col in col_objs:
        matched = False
        for ev in ev_list:
            if check_soft_alignment(ev, col, tolerance_days=tolerance_days):
                ev.attach_collateral(col)
                matched = True
                break
        if not matched:
            unattached.append(col)

    if retain_unattached:
        for u in unattached:
            ind_ev = collateral_record_to_evidence(u, default_entity=default_entity or u.symbol)
            ev_list.append(ind_ev)

    return ev_list, unattached


def attach_collaterals_to_envelope(
    envelope: Any,
    collaterals: Sequence[CollateralRecord | Mapping[str, Any]],
    tolerance_days: int = 1,
) -> tuple[Any, list[CollateralRecord]]:
    """Attach collateral records to records in a CninfoDisclosureEnvelope.

    Enforces Contract 1 & 5:
    - Primary record's canonical_event_id is strictly preserved.
    - Collateral's canonical_event_id is strictly None.
    """
    col_objs = [
        c if isinstance(c, CollateralRecord) else CollateralRecord.from_dict(c)
        for c in collaterals
    ]
    recs = getattr(envelope, "records", []) if hasattr(envelope, "records") else (
        envelope.get("records", []) if isinstance(envelope, dict) else []
    )
    unattached: list[CollateralRecord] = []
    attached_cols: list[CollateralRecord] = []

    for col in col_objs:
        matched = False
        for rec in recs:
            if check_soft_alignment(rec, col, tolerance_days=tolerance_days):
                if hasattr(rec, "collateral_records"):
                    if not any(c.collateral_id == col.collateral_id for c in rec.collateral_records):
                        rec.collateral_records.append(col)
                else:
                    setattr(rec, "collateral_records", [col])
                matched = True
                attached_cols.append(col)
                break
        if not matched:
            unattached.append(col)

    if hasattr(envelope, "collateral_records"):
        envelope.collateral_records.extend(attached_cols)
    else:
        try:
            setattr(envelope, "collateral_records", attached_cols)
        except Exception:
            pass

    return envelope, unattached


def collateral_record_to_evidence(
    record: CollateralRecord | Mapping[str, Any],
    default_entity: str = "",
) -> NewsEvidence:
    """Convert an unattached CollateralRecord into an independent NewsEvidence.

    Enforces Contract 3:
    - canonical_event_id is strictly None.
    - Title/label MUST explicitly indicate '[结构化旁证]'.
    - Must NOT masquerade as an official full announcement.
    """
    rec = record if isinstance(record, CollateralRecord) else CollateralRecord.from_dict(record)
    source_type = rec.source_type
    ann_date = rec.ann_date
    symbol = rec.symbol or default_entity
    payload = rec.payload or {}

    # Label per Contract 3: MUST include [结构化旁证]
    if source_type == SOURCE_TYPE_TUSHARE_FORECAST:
        fc_type = payload.get("type") or ""
        summary_str = payload.get("summary") or ""
        type_str = f"（{fc_type}）" if fc_type else ""
        if summary_str:
            detail = f"业绩预告{type_str}：{summary_str}"
        else:
            detail = f"业绩预告{type_str}"
        title = f"[结构化旁证] {symbol} {detail}"
        theme = "财报"
    elif source_type == SOURCE_TYPE_TUSHARE_REPURCHASE:
        proc = payload.get("proc") or ""
        proc_str = f"（{proc}）" if proc else ""
        title = f"[结构化旁证] {symbol} 股票回购{proc_str}"
        theme = "公司治理"
    elif source_type == SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE:
        pre_date = payload.get("pre_date") or ""
        pre_str = f"（预约披露日：{pre_date}）" if pre_date else ""
        title = f"[结构化旁证] {symbol} 定期报告披露计划{pre_str}"
        theme = "财报"
    else:
        title = f"[结构化旁证] {symbol} {source_type}"
        theme = "综合新闻"

    formatted_pub = f"{ann_date} 00:00:00" if len(ann_date) == 10 else ann_date

    return NewsEvidence(
        title=title,
        published_at=formatted_pub,
        source=source_type,
        summary=json.dumps(payload, ensure_ascii=False) if payload else "",
        entity=symbol,
        theme=theme,
        canonical_event_id=None,  # STRICTLY None
        collateral_records=[rec],
        raw_item=rec.to_dict(),
    )


def fetch_tushare_collaterals(
    provider: Any,
    symbol: str,
    as_of: str | None = None,
    ann_date: str | None = None,
) -> tuple[list[CollateralRecord], list[dict[str, Any]], list[CollateralEnvelope]]:
    """Fetch forecast, repurchase, and disclosure_date collaterals via existing provider methods.

    Reuses existing CnAkshareProvider._fetch_tushare_* methods; strictly forbids new HTTP client.

    Returns:
        (collateral_records, failure_gaps, envelopes)
    """
    records: list[CollateralRecord] = []
    gaps: list[dict[str, Any]] = []
    envelopes: list[CollateralEnvelope] = []

    # 1. Forecast
    if hasattr(provider, "_fetch_tushare_forecast"):
        fc_recs, fc_err, fc_cat = provider._fetch_tushare_forecast(
            symbol=symbol, as_of=as_of, ann_date=ann_date
        )
        if fc_cat in ("provider_failure", "permission_denied"):
            gaps.append({
                "source": SOURCE_TYPE_TUSHARE_FORECAST,
                "theme": "财报",
                "item": SOURCE_TYPE_TUSHARE_FORECAST,
                "status": "provider_failure",
                "reason": fc_err or "provider_failure",
                "message": f"{SOURCE_TYPE_TUSHARE_FORECAST}：私有网关调用权限拒绝或异常（{fc_err or 'provider_failure'}），不可验证（异常非空表，不得推断无相关记录）",
            })
            envelopes.append(CollateralEnvelope(
                status="provider_failure",
                source_type=SOURCE_TYPE_TUSHARE_FORECAST,
                records=[],
                error=fc_err,
                category=fc_cat,
            ))
        elif fc_cat == "schema_drift":
            gaps.append({
                "source": SOURCE_TYPE_TUSHARE_FORECAST,
                "theme": "财报",
                "item": SOURCE_TYPE_TUSHARE_FORECAST,
                "status": "schema_drift",
                "reason": fc_err or "schema_drift",
                "message": f"{SOURCE_TYPE_TUSHARE_FORECAST}：返回数据缺少必要日期字段（{fc_err or 'schema_drift'}），该源判定为不可验证并丢弃",
            })
            envelopes.append(CollateralEnvelope(
                status="schema_drift",
                source_type=SOURCE_TYPE_TUSHARE_FORECAST,
                records=[],
                error=fc_err,
                category=fc_cat,
            ))
        elif fc_cat == "collateral_empty":
            envelopes.append(CollateralEnvelope(
                status="collateral_empty",
                source_type=SOURCE_TYPE_TUSHARE_FORECAST,
                records=[],
                error=fc_err,
                category=fc_cat,
            ))
        else:
            fc_obj_recs = [CollateralRecord.from_dict(r) for r in fc_recs]
            records.extend(fc_obj_recs)
            envelopes.append(CollateralEnvelope(
                status="ok",
                source_type=SOURCE_TYPE_TUSHARE_FORECAST,
                records=fc_obj_recs,
            ))

    # 2. Repurchase
    if hasattr(provider, "_fetch_tushare_repurchase"):
        rp_recs, rp_err, rp_cat = provider._fetch_tushare_repurchase(
            symbol=symbol, as_of=as_of, ann_date=ann_date
        )
        if rp_cat in ("provider_failure", "permission_denied"):
            gaps.append({
                "source": SOURCE_TYPE_TUSHARE_REPURCHASE,
                "theme": "公司治理",
                "item": SOURCE_TYPE_TUSHARE_REPURCHASE,
                "status": "provider_failure",
                "reason": rp_err or "provider_failure",
                "message": f"{SOURCE_TYPE_TUSHARE_REPURCHASE}：私有网关调用权限拒绝或异常（{rp_err or 'provider_failure'}），不可验证（异常非空表，不得推断无相关记录）",
            })
            envelopes.append(CollateralEnvelope(
                status="provider_failure",
                source_type=SOURCE_TYPE_TUSHARE_REPURCHASE,
                records=[],
                error=rp_err,
                category=rp_cat,
            ))
        elif rp_cat == "schema_drift":
            gaps.append({
                "source": SOURCE_TYPE_TUSHARE_REPURCHASE,
                "theme": "公司治理",
                "item": SOURCE_TYPE_TUSHARE_REPURCHASE,
                "status": "schema_drift",
                "reason": rp_err or "schema_drift",
                "message": f"{SOURCE_TYPE_TUSHARE_REPURCHASE}：返回数据缺少必要日期字段（{rp_err or 'schema_drift'}），该源判定为不可验证并丢弃",
            })
            envelopes.append(CollateralEnvelope(
                status="schema_drift",
                source_type=SOURCE_TYPE_TUSHARE_REPURCHASE,
                records=[],
                error=rp_err,
                category=rp_cat,
            ))
        elif rp_cat == "collateral_empty":
            envelopes.append(CollateralEnvelope(
                status="collateral_empty",
                source_type=SOURCE_TYPE_TUSHARE_REPURCHASE,
                records=[],
                error=rp_err,
                category=rp_cat,
            ))
        else:
            rp_obj_recs = [CollateralRecord.from_dict(r) for r in rp_recs]
            records.extend(rp_obj_recs)
            envelopes.append(CollateralEnvelope(
                status="ok",
                source_type=SOURCE_TYPE_TUSHARE_REPURCHASE,
                records=rp_obj_recs,
            ))

    # 3. Disclosure date
    if hasattr(provider, "_fetch_tushare_disclosure_date"):
        dd_recs, dd_err, dd_cat = provider._fetch_tushare_disclosure_date(
            symbol=symbol, as_of=as_of, ann_date=ann_date
        )
        if dd_cat in ("provider_failure", "permission_denied"):
            gaps.append({
                "source": SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
                "theme": "财报",
                "item": SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
                "status": "provider_failure",
                "reason": dd_err or "provider_failure",
                "message": f"{SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE}：私有网关调用权限拒绝或异常（{dd_err or 'provider_failure'}），不可验证（异常非空表，不得推断无相关记录）",
            })
            envelopes.append(CollateralEnvelope(
                status="provider_failure",
                source_type=SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
                records=[],
                error=dd_err,
                category=dd_cat,
            ))
        elif dd_cat == "schema_drift":
            gaps.append({
                "source": SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
                "theme": "财报",
                "item": SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
                "status": "schema_drift",
                "reason": dd_err or "schema_drift",
                "message": f"{SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE}：返回数据缺少必要日期字段（{dd_err or 'schema_drift'}），该源判定为不可验证并丢弃",
            })
            envelopes.append(CollateralEnvelope(
                status="schema_drift",
                source_type=SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
                records=[],
                error=dd_err,
                category=dd_cat,
            ))
        elif dd_cat == "collateral_empty":
            envelopes.append(CollateralEnvelope(
                status="collateral_empty",
                source_type=SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
                records=[],
                error=dd_err,
                category=dd_cat,
            ))
        else:
            dd_obj_recs = [CollateralRecord.from_dict(r) for r in dd_recs]
            records.extend(dd_obj_recs)
            envelopes.append(CollateralEnvelope(
                status="ok",
                source_type=SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
                records=dd_obj_recs,
            ))

    return records, gaps, envelopes


def fetch_and_attach_tushare_collaterals(
    provider: Any,
    symbol: str,
    evidences: Sequence[NewsEvidence],
    as_of: str | None = None,
    ann_date: str | None = None,
    tolerance_days: int = 1,
    retain_unattached: bool = True,
) -> tuple[list[NewsEvidence], list[CollateralRecord], list[dict[str, Any]], list[CollateralEnvelope]]:
    """Fetch Tushare collateral records and attach them to NewsEvidences in a single workflow.

    Returns:
        (updated_evidences, unattached_collaterals, failure_gaps, envelopes)
    """
    records, gaps, envelopes = fetch_tushare_collaterals(
        provider, symbol=symbol, as_of=as_of, ann_date=ann_date
    )
    updated_evs, unattached = attach_collaterals_to_evidences(
        evidences, records, tolerance_days=tolerance_days, retain_unattached=retain_unattached, default_entity=symbol
    )
    return updated_evs, unattached, gaps, envelopes


@dataclass
class NewsEvidence:
    """Structured news event evidence item."""

    title: str
    published_at: str
    source: str = ""
    source_hash: str = ""
    summary: str = ""
    entity: str = ""
    theme: str = ""
    first_seen_at: str | None = None
    direct_impact: str | None = None
    transmission_chain: list[str] | str | None = None
    expected_lag: str | None = None
    public_before_cutoff: bool = False
    cluster_id: str | None = None
    url: str | None = None
    raw_item: dict[str, Any] | None = None
    canonical_event_id: str | None = None
    collateral_records: list[CollateralRecord] = field(default_factory=list)

    def __post_init__(self):
        self.title = str(self.title or "").strip()
        self.entity = str(self.entity or "").strip()
        self.theme = str(self.theme or "").strip()
        if not self.theme and self.title:
            self.theme = infer_theme_from_text(self.title, self.summary)
        if self.url is not None:
            self.url = normalize_url(self.url)
        if self.canonical_event_id is not None:
            self.canonical_event_id = str(self.canonical_event_id).strip() or None
        if not self.source_hash:
            self.source_hash = compute_source_hash(
                self.source, self.title, self.published_at, self.summary, url=self.url
            )
        norm_collaterals: list[CollateralRecord] = []
        for c in (self.collateral_records or []):
            if isinstance(c, CollateralRecord):
                norm_collaterals.append(c)
            elif isinstance(c, Mapping):
                norm_collaterals.append(CollateralRecord.from_dict(c))
        self.collateral_records = norm_collaterals

    @property
    def collaterals(self) -> list[CollateralRecord]:
        return self.collateral_records

    @property
    def collateral_evidences(self) -> list[CollateralRecord]:
        return self.collateral_records

    def attach_collateral(self, record: CollateralRecord | Mapping[str, Any]) -> None:
        """Attach a CollateralRecord to this primary NewsEvidence without altering canonical_event_id."""
        rec = record if isinstance(record, CollateralRecord) else CollateralRecord.from_dict(record)
        if not any(c.collateral_id == rec.collateral_id for c in self.collateral_records):
            self.collateral_records.append(rec)

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        if self.canonical_event_id is None:
            res.pop("canonical_event_id", None)
        if not self.collateral_records:
            res.pop("collateral_records", None)
        else:
            res["collateral_records"] = [
                r.to_dict() if hasattr(r, "to_dict") else dict(r)
                for r in self.collateral_records
            ]
            res["collaterals"] = res["collateral_records"]
            res["collateral_evidences"] = res["collateral_records"]
        return res


@dataclass
class EventCluster:
    """Clustered news events grouped by entity/theme and semantic similarity."""

    cluster_id: str
    theme: str
    entity: str
    title: str
    earliest_published_at: str
    latest_published_at: str
    evidence_count: int
    evidences: list[NewsEvidence] = field(default_factory=list)
    source_hashes: list[str] = field(default_factory=list)
    summary: str = ""
    canonical_event_id: str | None = None
    collateral_records: list[CollateralRecord] = field(default_factory=list)

    @property
    def collaterals(self) -> list[CollateralRecord]:
        return self.collateral_records

    @property
    def collateral_evidences(self) -> list[CollateralRecord]:
        return self.collateral_records

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["evidences"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in self.evidences]
        if self.canonical_event_id is None:
            res.pop("canonical_event_id", None)
        if not self.collateral_records:
            res.pop("collateral_records", None)
        else:
            res["collateral_records"] = [
                r.to_dict() if hasattr(r, "to_dict") else dict(r)
                for r in self.collateral_records
            ]
            res["collaterals"] = res["collateral_records"]
            res["collateral_evidences"] = res["collateral_records"]
        return res


def cninfo_record_to_evidence(
    record: Any,
    default_entity: str = "",
) -> NewsEvidence | None:
    """Convert a CninfoDisclosureRecord into NewsEvidence, copying canonical_event_id verbatim.

    Enforces contract (C-05c / DAV-625):
    - Only copy CninfoDisclosureRecord.canonical_event_id verbatim.
    - Strictly forbids inventing canonical_event_id from title or hash.
    - Seamlessly carries over attached collateral_records.
    """
    if record is None:
        return None
    if isinstance(record, dict):
        raw_dict = dict(record)
        title = raw_dict.get("title", raw_dict.get("公告标题", ""))
        pub = raw_dict.get("announced_at", raw_dict.get("published_at", raw_dict.get("公告时间", "")))
        source = raw_dict.get("source_type", raw_dict.get("source", "cninfo"))
        url = raw_dict.get("url", raw_dict.get("公告链接"))
        entity = raw_dict.get("symbol", raw_dict.get("代码", default_entity))
        canonical_id = raw_dict.get("canonical_event_id")
        col_records = raw_dict.get("collateral_records", [])
    else:
        title = getattr(record, "title", "")
        pub = getattr(record, "announced_at", getattr(record, "published_at", ""))
        source = getattr(record, "source_type", getattr(record, "source", "cninfo"))
        url = getattr(record, "url", None)
        entity = getattr(record, "symbol", getattr(record, "entity", default_entity))
        canonical_id = getattr(record, "canonical_event_id", None)
        col_records = getattr(record, "collateral_records", [])
        raw_dict = record.to_dict() if hasattr(record, "to_dict") else None

    if not pub or not title:
        return None

    pub_dt = parse_datetime_or_none(pub)
    if pub_dt is None:
        return None
    formatted_pub = pub_dt.strftime("%Y-%m-%d %H:%M:%S")

    canonical_id_str = str(canonical_id).strip() if canonical_id else None

    ev = NewsEvidence(
        title=str(title).strip(),
        published_at=formatted_pub,
        source=str(source).strip(),
        entity=str(entity or default_entity).strip(),
        url=normalize_url(url),
        canonical_event_id=canonical_id_str,
        raw_item=raw_dict,
    )
    if col_records:
        for c in col_records:
            ev.attach_collateral(c)
    return ev



def cluster_news_evidences(
    evidences: Sequence[NewsEvidence],
    time_window_days: int = 3,
) -> list[EventCluster]:
    """Group NewsEvidence instances into deduplicated EventCluster objects.

    Enforces C-05c / DAV-625 contracts:
    - Same non-empty canonical_event_id must merge into the same cluster regardless
      of title, URL, or source.
    - Differing canonical_event_ids must never merge into the same cluster (title fuzzy
      matching is strictly forbidden).
    - If only one side has canonical_event_id, it may merge via URL / source_hash / title,
      without inventing an ID for the other side.
    """
    if not evidences:
        return []

    # Partition: evidences with canonical_event_id first to establish stable anchor clusters,
    # with stable ordering preserved within each group.
    sorted_evidences = sorted(
        evidences,
        key=lambda e: 0 if getattr(e, "canonical_event_id", None) else 1,
    )

    clusters: list[list[NewsEvidence]] = []

    for ev in sorted_evidences:
        matched_cluster = None
        ev_cid = ev.canonical_event_id.strip() if getattr(ev, "canonical_event_id", None) else None
        ev_norm_title = normalize_title_for_dedupe(ev.title)
        ev_dt = parse_datetime_or_none(ev.published_at)

        # Priority 1: If ev has canonical_event_id, must merge with any cluster sharing that exact id.
        # Contract 3: 两边都有非空且相等的 canonical_event_id 时必须归入同一簇，即使标题/URL/来源不同。
        if ev_cid:
            for cluster_evs in clusters:
                if any(getattr(e, "canonical_event_id", None) == ev_cid for e in cluster_evs):
                    matched_cluster = cluster_evs
                    break

        # Priority 2: Fall back to existing URL / source_hash / semantic matching rules
        if matched_cluster is None:
            for cluster_evs in clusters:
                cluster_cids = {
                    getattr(e, "canonical_event_id", None)
                    for e in cluster_evs
                    if getattr(e, "canonical_event_id", None)
                }
                # Contract 3: 不等的 id 不得因标题模糊匹配并成一簇。
                # If both sides have canonical_event_id and they are unequal, strictly forbid merging.
                if ev_cid and cluster_cids and ev_cid not in cluster_cids:
                    continue

                rep = cluster_evs[0]
                rep_norm_title = normalize_title_for_dedupe(rep.title)
                rep_dt = parse_datetime_or_none(rep.published_at)

                # Match criteria:
                # 1. URL match: both have non-empty normalized URL and they are equal
                ev_norm_url = normalize_url(ev.url) if ev.url else None
                same_url = bool(
                    ev_norm_url
                    and any(
                        normalize_url(e.url) == ev_norm_url
                        for e in cluster_evs
                        if e.url
                    )
                )
                if same_url:
                    matched_cluster = cluster_evs
                    break

                # 2. Same source_hash
                same_hash = bool(
                    ev.source_hash
                    and any(ev.source_hash == e.source_hash for e in cluster_evs if e.source_hash)
                )

                # 3. Or same entity & theme AND (same normalized title OR high title overlap)
                same_entity = (ev.entity == rep.entity) if (ev.entity and rep.entity) else True
                same_theme = (ev.theme == rep.theme) if (ev.theme and rep.theme) else True

                time_close = True
                if ev_dt and rep_dt:
                    time_close = abs((ev_dt - rep_dt).total_seconds()) <= (time_window_days * 86400)

                title_matches = False
                if ev_norm_title and rep_norm_title:
                    if ev_norm_title == rep_norm_title:
                        title_matches = True
                    elif (ev_norm_title in rep_norm_title or rep_norm_title in ev_norm_title) and time_close:
                        title_matches = True

                if same_hash or (
                    same_entity and same_theme and title_matches and time_close
                ):
                    matched_cluster = cluster_evs
                    break

        if matched_cluster is not None:
            matched_cluster.append(ev)
        else:
            clusters.append([ev])

    event_clusters: list[EventCluster] = []
    for idx, cluster_evs in enumerate(clusters):
        rep = cluster_evs[0]
        theme = rep.theme or "综合新闻"
        entity = rep.entity or "default_entity"

        published_dates = [
            e.published_at for e in cluster_evs if e.published_at
        ]
        earliest_pub = min(published_dates) if published_dates else ""
        latest_pub = max(published_dates) if published_dates else ""

        # Deterministic cluster_id
        cluster_hash = hashlib.sha256(
            f"{theme}:{entity}:{earliest_pub}:{normalize_title_for_dedupe(rep.title)}".encode("utf-8")
        ).hexdigest()[:12]
        cluster_id = f"cluster_{theme}_{cluster_hash}"

        # Assign cluster_id to each evidence item
        for e in cluster_evs:
            e.cluster_id = cluster_id

        # Contract 5: EventCluster 可回显该 id（有则带上，无则字段缺省/None）
        cluster_canonical_id = next(
            (e.canonical_event_id for e in cluster_evs if getattr(e, "canonical_event_id", None)),
            None,
        )

        cluster_collaterals: list[CollateralRecord] = []
        for e in cluster_evs:
            for c in getattr(e, "collateral_records", []):
                if not any(x.collateral_id == c.collateral_id for x in cluster_collaterals):
                    cluster_collaterals.append(c)

        event_cluster = EventCluster(
            cluster_id=cluster_id,
            theme=theme,
            entity=entity,
            title=rep.title,
            earliest_published_at=earliest_pub,
            latest_published_at=latest_pub,
            evidence_count=len(cluster_evs),
            evidences=cluster_evs,
            source_hashes=list({e.source_hash for e in cluster_evs if e.source_hash}),
            summary=rep.summary,
            canonical_event_id=cluster_canonical_id,
            collateral_records=cluster_collaterals,
        )
        event_clusters.append(event_cluster)

    return event_clusters


def build_news_event_coverage(
    items_or_evidences: Iterable[Mapping[str, Any] | NewsEvidence | Any],
    requested_themes: Sequence[str] | None = None,
    query_manifest: Sequence[str] | None = None,
    cutoff: str | datetime | None = None,
    window: str | int = DEFAULT_NEWS_WINDOW,
    default_entity: str = "",
    cninfo_envelopes: Sequence[Any] | None = None,
    envelopes: Sequence[Any] | None = None,
    source_manifest: Sequence[str] | None = None,
    collateral_records: Sequence[CollateralRecord | Mapping[str, Any]] | None = None,
    collaterals: Sequence[CollateralRecord | Mapping[str, Any]] | None = None,
    collateral_envelopes: Sequence[Any] | None = None,
    provider: Any | None = None,
) -> dict[str, Any]:
    """Build structured event_coverage and qualification dictionary from news items.

    Enforces:
    - Qualification rules: items with unparseable published_at are rejected (unverifiable).
    - Lookahead prevention: items with published_at > cutoff are rejected (future).
    - Deduplication: near-duplicate items form a single EventCluster.
    - Suspected gaps: requested themes with 0 hits are documented with
      '未检索到/不可验证', strictly forbidding '确认无相关新闻'.
    - Recall honesty (DAV-608 & C-05d):
      - recall_status is 'unknown' and manifest [] when no manifest/cninfo provided;
      - 'partial_vs_manifest' when manifest/themes or cninfo records provided;
      - 'provider_failure' when cninfo provider failure occurs (strictly forbidden from
        being classified as confirmed_empty);
      - source_manifest only echoes actually queried/checked sources (e.g. cninfo_announcement);
      - Never fabricates default five themes.
    - Collateral attachment (C-05 Slice 5 / DAV-646):
      - Soft-aligns structured collateral records to primary CNINFO evidences.
      - Retains unattached collaterals as independent NewsEvidence labeled '[结构化旁证]'.
      - Records provider_failure as gap; collateral_empty strictly forbids '确认无公告'.
    """
    cutoff_dt = parse_cutoff_datetime(cutoff)
    cutoff_str = cutoff.strftime("%Y-%m-%d") if isinstance(cutoff, datetime) else str(cutoff or "")
    window_str = str(window or DEFAULT_NEWS_WINDOW)

    # 1. Collect and inspect cninfo and collateral envelopes
    all_envelopes: list[Any] = []
    if cninfo_envelopes:
        all_envelopes.extend(cninfo_envelopes)
    if envelopes:
        all_envelopes.extend(envelopes)
    if collateral_envelopes:
        all_envelopes.extend(collateral_envelopes)

    collected_collaterals: list[CollateralRecord] = []
    if collateral_records:
        for c in collateral_records:
            collected_collaterals.append(
                c if isinstance(c, CollateralRecord) else CollateralRecord.from_dict(c)
            )
    if collaterals:
        for c in collaterals:
            collected_collaterals.append(
                c if isinstance(c, CollateralRecord) else CollateralRecord.from_dict(c)
            )

    detected_sources: list[str] = []
    cninfo_manifest_items: list[str] = []
    failure_gaps: list[dict[str, Any]] = []
    cninfo_confirmed_empty = False
    has_provider_failure = False

    # Optional provider collateral pre-fetch
    if provider is not None and default_entity:
        p_recs, p_gaps, p_envs = fetch_tushare_collaterals(
            provider, symbol=default_entity, as_of=cutoff_str
        )
        collected_collaterals.extend(p_recs)
        failure_gaps.extend(p_gaps)
        all_envelopes.extend(p_envs)

    raw_items_list: list[Any] = []
    for item in items_or_evidences:
        if isinstance(item, CollateralRecord) or (
            isinstance(item, dict)
            and item.get("source_type") in COLLATERAL_SOURCE_TYPES
            and ("collateral_id" in item or "ann_date" in item)
        ):
            col_rec = item if isinstance(item, CollateralRecord) else CollateralRecord.from_dict(item)
            collected_collaterals.append(col_rec)
        elif (hasattr(item, "status") and hasattr(item, "records")) or (
            isinstance(item, dict)
            and "status" in item
            and "records" in item
            and ("source_type" in item or "disclaimer" in item or "is_confirmed_empty" in item)
        ):
            all_envelopes.append(item)
        else:
            raw_items_list.append(item)

    for env in all_envelopes:
        env_status = getattr(env, "status", None) if not isinstance(env, dict) else env.get("status")
        env_src = getattr(env, "source_type", None) if not isinstance(env, dict) else env.get("source_type")
        env_src = str(env_src).strip() if env_src else "cninfo_announcement"
        if env_src not in detected_sources:
            detected_sources.append(env_src)

        is_collateral_env = env_src in COLLATERAL_SOURCE_TYPES or isinstance(env, CollateralEnvelope)

        if env_status == "ok":
            recs = getattr(env, "records", []) if not isinstance(env, dict) else env.get("records", [])
            if is_collateral_env:
                for rec in recs:
                    col_rec = rec if isinstance(rec, CollateralRecord) else CollateralRecord.from_dict(rec)
                    if not any(c.collateral_id == col_rec.collateral_id for c in collected_collaterals):
                        collected_collaterals.append(col_rec)
            else:
                for rec in recs:
                    cid = getattr(rec, "canonical_event_id", None) if not isinstance(rec, dict) else rec.get("canonical_event_id")
                    title = getattr(rec, "title", "") if not isinstance(rec, dict) else rec.get("title", "")
                    id_or_title = (str(cid).strip() if cid else "") or (str(title).strip() if title else "")
                    if id_or_title and id_or_title not in cninfo_manifest_items:
                        cninfo_manifest_items.append(id_or_title)

                    rec_already_in_raw = any(
                        (hasattr(x, "canonical_event_id") and getattr(x, "canonical_event_id", None) == cid and cid is not None)
                        or (isinstance(x, dict) and x.get("canonical_event_id") == cid and cid is not None)
                        for x in raw_items_list
                    )
                    if not rec_already_in_raw:
                        raw_items_list.append(rec)

        elif env_status in ("provider_failure", "permission_denied"):
            has_provider_failure = True
            err = getattr(env, "error", "") if not isinstance(env, dict) else env.get("error", "")
            if is_collateral_env:
                theme = "公司治理" if "repurchase" in env_src else "财报"
                msg = f"{env_src}：私有网关调用权限拒绝或异常（{err or 'provider_failure'}），不可验证（异常非空表，不得推断无相关记录）"
            else:
                theme = env_src
                msg = f"{env_src}：巨潮数据拉取异常（{err or 'provider_failure'}），不可验证（异常非空表，不得推断无相关记录）"
            if not any(g.get("source") == env_src and g.get("status") == "provider_failure" for g in failure_gaps):
                failure_gaps.append({
                    "source": env_src,
                    "theme": theme,
                    "item": env_src,
                    "status": "provider_failure",
                    "reason": err or "provider_failure",
                    "message": msg,
                })

        elif env_status == "schema_drift":
            has_provider_failure = True
            err = getattr(env, "error", "") if not isinstance(env, dict) else env.get("error", "")
            theme = "公司治理" if "repurchase" in env_src else "财报"
            if not any(g.get("source") == env_src and g.get("status") == "schema_drift" for g in failure_gaps):
                failure_gaps.append({
                    "source": env_src,
                    "theme": theme,
                    "item": env_src,
                    "status": "schema_drift",
                    "reason": err or "schema_drift",
                    "message": f"{env_src}：返回数据缺少必要日期字段（{err or 'ann_date'}），该源判定为不可验证并丢弃",
                })

        elif env_status == "confirmed_empty":
            if not is_collateral_env:
                cninfo_confirmed_empty = True

        elif env_status == "collateral_empty":
            # Contract 4: Never treat collateral empty as confirmed_empty
            pass

    for c in collected_collaterals:
        if c.source_type and c.source_type not in detected_sources:
            detected_sources.append(c.source_type)

    valid_evidences: list[NewsEvidence] = []
    unverifiable_items: list[dict[str, Any]] = []
    future_rejected_items: list[dict[str, Any]] = []

    for item in raw_items_list:
        if isinstance(item, NewsEvidence):
            raw_title = item.title
            raw_pub = item.published_at
            raw_first_seen = item.first_seen_at
            raw_src = item.source
            raw_summary = item.summary
            raw_entity = item.entity or default_entity
            raw_theme = item.theme
            raw_dict = item.to_dict()
            raw_url = item.url
            if raw_url is None and item.raw_item:
                raw_url = extract_url_from_raw(item.raw_item)
            raw_canonical_event_id = item.canonical_event_id
            item_collaterals = list(item.collateral_records)
        elif hasattr(item, "canonical_event_id") and (hasattr(item, "announced_at") or hasattr(item, "announcement_id")):
            raw_dict = item.to_dict() if hasattr(item, "to_dict") else asdict(item)
            raw_title = getattr(item, "title", "")
            raw_pub = getattr(item, "announced_at", "")
            raw_first_seen = None
            raw_src = getattr(item, "source_type", "cninfo_announcement")
            raw_summary = getattr(item, "summary", "")
            raw_entity = getattr(item, "symbol", default_entity) or default_entity
            raw_theme = getattr(item, "theme", "")
            raw_url = getattr(item, "url", None)
            raw_canonical_event_id = getattr(item, "canonical_event_id", None)
            item_collaterals = list(getattr(item, "collateral_records", []))
        else:
            raw_dict = dict(item or {})
            raw_title = raw_dict.get("title", raw_dict.get("新闻标题", raw_dict.get("标题", "")))
            raw_pub = raw_dict.get("published_at", raw_dict.get("发布时间", raw_dict.get("date", raw_dict.get("announced_at", None))))
            raw_first_seen = raw_dict.get("first_seen_at", raw_dict.get("抓取时间", None))
            raw_src = raw_dict.get("source", raw_dict.get("文章来源", raw_dict.get("来源", raw_dict.get("source_type", ""))))
            raw_summary = raw_dict.get("summary", raw_dict.get("新闻内容", raw_dict.get("内容", "")))
            raw_entity = raw_dict.get("entity", raw_dict.get("标的", raw_dict.get("symbol", default_entity)))
            raw_theme = raw_dict.get("theme", raw_dict.get("主题", ""))
            raw_url = extract_url_from_raw(raw_dict)
            raw_canonical_event_id = raw_dict.get("canonical_event_id")
            item_collaterals = list(raw_dict.get("collateral_records", []))

        # Track cninfo source and manifest items from records/evidences (C-05d)
        is_cninfo_item = bool(
            raw_canonical_event_id
            or (raw_src and any(k in str(raw_src).lower() for k in ("cninfo", "巨潮")))
            or hasattr(item, "announced_at")
            or (isinstance(item, dict) and "announced_at" in item)
        )
        if is_cninfo_item:
            clean_src = str(raw_src).strip() if raw_src else "cninfo_announcement"
            if clean_src not in detected_sources:
                detected_sources.append(clean_src)
            cid_or_title = (str(raw_canonical_event_id).strip() if raw_canonical_event_id else "") or (str(raw_title).strip() if raw_title else "")
            if cid_or_title and cid_or_title not in cninfo_manifest_items:
                cninfo_manifest_items.append(cid_or_title)

        # 1. Strict published_at verification
        pub_dt = parse_datetime_or_none(raw_pub)
        if pub_dt is None:
            # Missing or unparseable published_at -> unverifiable.
            # D-008: first_seen_at must NOT substitute published_at.
            unverifiable_items.append({
                "title": raw_title,
                "raw_published_at": raw_pub,
                "first_seen_at": raw_first_seen,
                "source": raw_src,
                "reason": "发布时间缺失或无法解析（禁止使用抓取时间或当前日期回填）",
                "raw_item": raw_dict,
            })
            continue

        # Formatted string for published_at
        formatted_pub = pub_dt.strftime("%Y-%m-%d %H:%M:%S")

        # 2. Cutoff qualification
        if cutoff_dt is not None and pub_dt > cutoff_dt:
            # Future news after cutoff -> reject
            future_rejected_items.append({
                "title": raw_title,
                "published_at": formatted_pub,
                "cutoff": cutoff_str,
                "reason": f"发布时间（{formatted_pub}）晚于分析截断日（{cutoff_str}），未来事件不可见",
                "raw_item": raw_dict,
            })
            continue

        evidence = NewsEvidence(
            title=raw_title,
            published_at=formatted_pub,
            source=raw_src,
            summary=raw_summary,
            entity=raw_entity,
            theme=raw_theme,
            first_seen_at=str(raw_first_seen) if raw_first_seen else None,
            public_before_cutoff=True,
            url=normalize_url(raw_url),
            raw_item=raw_dict,
            canonical_event_id=str(raw_canonical_event_id).strip() if raw_canonical_event_id else None,
            collateral_records=item_collaterals,
        )
        valid_evidences.append(evidence)

    # Soft alignment: attach collaterals to valid_evidences
    attached_count = 0
    independent_count = 0
    if collected_collaterals:
        attached_evs, unattached_cols = attach_collaterals_to_evidences(
            valid_evidences, collected_collaterals, tolerance_days=1, retain_unattached=False
        )
        attached_count = len(collected_collaterals) - len(unattached_cols)
        # Contract 3: unattached collaterals retain independently
        for u in unattached_cols:
            u_pub = parse_datetime_or_none(u.ann_date)
            if u_pub is None:
                unverifiable_items.append({
                    "title": f"[结构化旁证] {u.symbol} {u.source_type}",
                    "raw_published_at": u.ann_date,
                    "reason": "发布时间缺失或无法解析（禁止使用抓取时间或当前日期回填）",
                    "raw_item": u.to_dict(),
                })
            elif cutoff_dt is not None and u_pub > cutoff_dt:
                future_rejected_items.append({
                    "title": f"[结构化旁证] {u.symbol} {u.source_type}",
                    "published_at": u.ann_date,
                    "cutoff": cutoff_str,
                    "reason": f"公告时间（{u.ann_date}）晚于分析截断日（{cutoff_str}），未来事件不可见",
                    "raw_item": u.to_dict(),
                })
            else:
                ind_ev = collateral_record_to_evidence(u, default_entity=default_entity or u.symbol)
                valid_evidences.append(ind_ev)
                independent_count += 1

    # 3. Deduplicate and cluster valid evidences
    clusters = cluster_news_evidences(valid_evidences)
    hit_cluster_ids = [c.cluster_id for c in clusters]
    hit_count = len(clusters)

    # 4. Manifest list evaluation (DAV-608 & C-05d: no fabrication of default 5 themes)
    manifest_list: list[str] = []
    if query_manifest is not None:
        manifest_list.extend(query_manifest)
    elif requested_themes is not None:
        manifest_list.extend(requested_themes)

    for m in cninfo_manifest_items:
        if m not in manifest_list:
            manifest_list.append(m)

    # Determine recall_status
    if manifest_list:
        recall_status = RECALL_STATUS_PARTIAL_VS_MANIFEST
    elif has_provider_failure:
        recall_status = RECALL_STATUS_PROVIDER_FAILURE
    else:
        recall_status = RECALL_STATUS_UNKNOWN

    # Determine source_manifest
    if source_manifest is not None:
        source_manifest_list = list(source_manifest)
    else:
        source_manifest_list = list(dict.fromkeys(detected_sources))

    # Evaluate suspected_gaps and recall_gap
    suspected_gaps: list[dict[str, Any]] = []

    for manifest_item in manifest_list:
        is_hit = False
        # 1. By canonical_event_id
        for c in clusters:
            if c.canonical_event_id and c.canonical_event_id == manifest_item:
                is_hit = True
                break
            if any(getattr(e, "canonical_event_id", None) == manifest_item for e in c.evidences):
                is_hit = True
                break
        # 2. By title
        if not is_hit:
            norm_manifest = normalize_title_for_dedupe(manifest_item)
            for c in clusters:
                if manifest_item == c.title or (norm_manifest and norm_manifest == normalize_title_for_dedupe(c.title)):
                    is_hit = True
                    break
        # 3. By theme
        if not is_hit:
            for c in clusters:
                if c.theme == manifest_item or manifest_item in c.theme or manifest_item in c.title or manifest_item in c.summary:
                    is_hit = True
                    break
        # 4. By collateral source_type or collateral_id
        if not is_hit:
            for c in clusters:
                if any(
                    col.source_type == manifest_item or col.collateral_id == manifest_item
                    for col in getattr(c, "collateral_records", [])
                ):
                    is_hit = True
                    break

        if not is_hit:
            # Check if this item corresponds to an empty collateral table
            is_col_empty = any(
                (getattr(env, "source_type", None) == manifest_item or getattr(env, "source", None) == manifest_item)
                and getattr(env, "status", None) == "collateral_empty"
                for env in all_envelopes
            )
            if is_col_empty:
                gap_msg = f"{manifest_item}：未检索到结构化旁证记录（注：仅代表私有网关特定专题表无记录，不可据此推断上市公司无相关公告）"
            else:
                gap_msg = f"{manifest_item}：{GAP_UNVERIFIABLE_MESSAGE}"

            suspected_gaps.append({
                "item": manifest_item,
                "theme": manifest_item,
                "status": "unverified_or_not_found",
                "message": gap_msg,
                "reason": GAP_UNVERIFIABLE_MESSAGE,
            })

    for fg in failure_gaps:
        if fg not in suspected_gaps:
            suspected_gaps.append(fg)

    recall_gap = list(suspected_gaps)

    cninfo_status = None
    if has_provider_failure:
        cninfo_status = "provider_failure"
    elif cninfo_confirmed_empty:
        cninfo_status = "confirmed_empty"
    elif cninfo_manifest_items:
        cninfo_status = "ok"

    return {
        "cutoff": cutoff_str,
        "window": window_str,
        "recall_status": recall_status,
        "query_manifest": manifest_list,
        "requested_themes": manifest_list,
        "source_manifest": source_manifest_list,
        "hit_count": hit_count,
        "hit_cluster_ids": hit_cluster_ids,
        "unverifiable_count": len(unverifiable_items),
        "future_rejected_count": len(future_rejected_items),
        "valid_evidence_count": len(valid_evidences),
        "suspected_gaps": suspected_gaps,
        "recall_gap": recall_gap,
        "recall_gaps": recall_gap,
        "has_gap": len(suspected_gaps) > 0,
        "is_confirmed_empty": False,
        "cninfo_status": cninfo_status,
        "collateral_records": [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in collected_collaterals],
        "attached_collateral_count": attached_count,
        "independent_collateral_count": independent_count,
        "clusters": [c.to_dict() for c in clusters],
        "unverifiable_items": unverifiable_items,
        "future_rejected_items": future_rejected_items,
    }


def parse_news_markdown_to_evidences(
    markdown_text: str,
    default_entity: str = "",
) -> tuple[list[NewsEvidence], list[dict[str, Any]]]:
    """Parse vendor markdown news text (### title [发布时间：...] (source: ...)) into NewsEvidence."""
    if not markdown_text or not isinstance(markdown_text, str):
        return [], []

    evidences: list[NewsEvidence] = []
    unparseable: list[dict[str, Any]] = []

    # Split by ### headers
    blocks = re.split(r"(?m)^###\s+", markdown_text)

    header_re = re.compile(
        r"^(?P<title>.+?)"
        r"(?:\s*\[发布时间：(?P<pub>[^\]]+)\])?"
        r"(?:\s*\(source:\s*(?P<src>[^\)]+)\))?"
        r"\s*$",
        re.MULTILINE,
    )

    for block in blocks:
        block = block.strip()
        if not block or block.startswith("## "):
            continue

        lines = block.splitlines()
        first_line = lines[0].strip()
        body = "\n".join(lines[1:]).strip()

        m = header_re.match(first_line)
        if not m:
            unparseable.append({
                "raw_header": first_line,
                "body": body,
                "reason": "新闻标题行格式不符合规范",
            })
            continue

        title = m.group("title").strip()
        raw_pub = m.group("pub")
        source = m.group("src") or ""

        pub_dt = parse_datetime_or_none(raw_pub)
        if pub_dt is None:
            unparseable.append({
                "title": title,
                "raw_published_at": raw_pub,
                "source": source,
                "body": body,
                "reason": "缺少或无法解析发布时间",
            })
            continue

        formatted_pub = pub_dt.strftime("%Y-%m-%d %H:%M:%S")
        url_match = re.search(r"(?im)^\s*(?:link|url|链接|新闻链接)\s*[:：]\s*(?P<url>\S+)", body)
        raw_url = url_match.group("url") if url_match else None
        evidence = NewsEvidence(
            title=title,
            published_at=formatted_pub,
            source=source,
            summary=body,
            entity=default_entity,
            url=normalize_url(raw_url),
        )
        evidences.append(evidence)

    return evidences, unparseable


def format_event_coverage_summary(coverage: Mapping[str, Any]) -> str:
    """Format compact, prompt-injectable news event coverage summary."""
    cutoff = coverage.get("cutoff", "")
    window = coverage.get("window", DEFAULT_NEWS_WINDOW)
    recall_status = coverage.get("recall_status", RECALL_STATUS_UNKNOWN)
    manifest = coverage.get("query_manifest") or coverage.get("requested_themes") or []
    themes_str = ", ".join(manifest) if manifest else "未指定（无应查清单）"
    sources = coverage.get("source_manifest") or []
    hit_count = coverage.get("hit_count", 0)
    unverifiable_count = coverage.get("unverifiable_count", 0)
    future_count = coverage.get("future_rejected_count", 0)
    gaps = coverage.get("suspected_gaps") or coverage.get("recall_gap") or []

    if recall_status == RECALL_STATUS_UNKNOWN:
        recall_explanation = "召回完整性未知；未提供应查清单，仅证明时间资格"
    elif recall_status == RECALL_STATUS_PROVIDER_FAILURE:
        recall_explanation = "数据源拉取失败/异常，不可验证"
    else:
        recall_explanation = "仅对比调用方声明清单/已知公告，非全市场核验"

    lines = [
        "【新闻事件结构化覆盖度（event_coverage）】",
        f"- 截断基准日（cutoff）：{cutoff}（观察窗口：{window}）",
        f"- 召回完整性（recall_status）：{recall_status}（{recall_explanation}）",
    ]
    if sources:
        lines.append(f"- 实际查验数据源（source_manifest）：{', '.join(sources)}")
    lines.extend([
        f"- 重点覆盖主题（query_manifest）：{themes_str}",
        f"- 命中有效事件簇：{hit_count} 个",
    ])

    if unverifiable_count > 0:
        lines.append(
            f"- 不可验证条目：{unverifiable_count} 条（缺少发布时间/时间解析失败，已剔除，禁止作为方向证据）"
        )
    if future_count > 0:
        lines.append(
            f"- 截断后未来事件：{future_count} 条（晚于截断日，已防窥探过滤）"
        )

    if coverage.get("cninfo_status") == "confirmed_empty":
        lines.append("- 巨潮资讯检索结果：官方披露为空（注：仅代表该数据源在此区间无披露，媒体新闻缺失不等于无公告，不可外推）")

    if coverage.get("collateral_records"):
        total_c = len(coverage["collateral_records"])
        att_c = coverage.get("attached_collateral_count", 0)
        ind_c = coverage.get("independent_collateral_count", 0)
        lines.append(
            f"- 结构化旁证挂载（collateral_records）：共 {total_c} 条（主源挂载 {att_c} 条，独立留存 {ind_c} 条；标签：[结构化旁证]）"
        )

    if gaps:
        lines.append("- 潜在数据缺口（suspected_gaps）：")
        for g in gaps:
            msg = g.get("message")
            if not msg:
                item_name = g.get("item") or g.get("theme") or g.get("source") or "未知项"
                status_str = g.get("status", "")
                reason = g.get("reason", GAP_UNVERIFIABLE_MESSAGE)
                msg = f"{item_name}：{reason}（状态：{status_str}）"
            suffix = "（注：未检索到不等于无相关事件，不可验证项不得作为利多/利空依据）" if "注：" not in msg else ""
            lines.append(f"  * {msg}{suffix}")
    else:
        if recall_status == RECALL_STATUS_UNKNOWN:
            lines.append("- 潜在数据缺口：未知（未提供应查清单，不作缺口假设）")
        else:
            lines.append("- 潜在数据缺口：清单内条目均已检索到对应事件（注：仅限声明清单，非全市场核验）")

    return "\n".join(lines)
