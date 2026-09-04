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

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        if self.canonical_event_id is None:
            res.pop("canonical_event_id", None)
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

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["evidences"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in self.evidences]
        if self.canonical_event_id is None:
            res.pop("canonical_event_id", None)
        return res


def cninfo_record_to_evidence(
    record: Any,
    default_entity: str = "",
) -> NewsEvidence | None:
    """Convert a CninfoDisclosureRecord into NewsEvidence, copying canonical_event_id verbatim.

    Enforces contract (C-05c / DAV-625):
    - Only copy CninfoDisclosureRecord.canonical_event_id verbatim.
    - Strictly forbids inventing canonical_event_id from title or hash.
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
    else:
        title = getattr(record, "title", "")
        pub = getattr(record, "announced_at", getattr(record, "published_at", ""))
        source = getattr(record, "source_type", getattr(record, "source", "cninfo"))
        url = getattr(record, "url", None)
        entity = getattr(record, "symbol", getattr(record, "entity", default_entity))
        canonical_id = getattr(record, "canonical_event_id", None)
        raw_dict = record.to_dict() if hasattr(record, "to_dict") else None

    if not pub or not title:
        return None

    pub_dt = parse_datetime_or_none(pub)
    if pub_dt is None:
        return None
    formatted_pub = pub_dt.strftime("%Y-%m-%d %H:%M:%S")

    canonical_id_str = str(canonical_id).strip() if canonical_id else None

    return NewsEvidence(
        title=str(title).strip(),
        published_at=formatted_pub,
        source=str(source).strip(),
        entity=str(entity or default_entity).strip(),
        url=normalize_url(url),
        canonical_event_id=canonical_id_str,
        raw_item=raw_dict,
    )


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
    """
    cutoff_dt = parse_cutoff_datetime(cutoff)
    cutoff_str = cutoff.strftime("%Y-%m-%d") if isinstance(cutoff, datetime) else str(cutoff or "")
    window_str = str(window or DEFAULT_NEWS_WINDOW)

    # 1. Collect and inspect cninfo envelopes
    all_envelopes: list[Any] = []
    if cninfo_envelopes:
        all_envelopes.extend(cninfo_envelopes)
    if envelopes:
        all_envelopes.extend(envelopes)

    raw_items_list: list[Any] = []
    for item in items_or_evidences:
        if (hasattr(item, "status") and hasattr(item, "records")) or (
            isinstance(item, dict)
            and "status" in item
            and "records" in item
            and ("source_type" in item or "disclaimer" in item or "is_confirmed_empty" in item)
        ):
            all_envelopes.append(item)
        else:
            raw_items_list.append(item)

    detected_sources: list[str] = []
    cninfo_manifest_items: list[str] = []
    failure_gaps: list[dict[str, Any]] = []
    cninfo_confirmed_empty = False
    has_provider_failure = False

    for env in all_envelopes:
        env_status = getattr(env, "status", None) if not isinstance(env, dict) else env.get("status")
        env_src = getattr(env, "source_type", None) if not isinstance(env, dict) else env.get("source_type")
        env_src = str(env_src).strip() if env_src else "cninfo_announcement"
        if env_src not in detected_sources:
            detected_sources.append(env_src)

        if env_status == "ok":
            recs = getattr(env, "records", []) if not isinstance(env, dict) else env.get("records", [])
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

        elif env_status == "provider_failure":
            has_provider_failure = True
            err = getattr(env, "error", "") if not isinstance(env, dict) else env.get("error", "")
            failure_gaps.append({
                "source": env_src,
                "theme": env_src,
                "item": env_src,
                "status": "provider_failure",
                "reason": err or "provider_failure",
                "message": f"{env_src}：巨潮数据拉取异常（{err or 'provider_failure'}），不可验证（异常非空表，不得推断无相关记录）",
            })

        elif env_status == "confirmed_empty":
            cninfo_confirmed_empty = True

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
        )
        valid_evidences.append(evidence)

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

        if not is_hit:
            suspected_gaps.append({
                "item": manifest_item,
                "theme": manifest_item,
                "status": "unverified_or_not_found",
                "message": f"{manifest_item}：{GAP_UNVERIFIABLE_MESSAGE}",
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
