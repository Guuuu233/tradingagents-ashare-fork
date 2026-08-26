"""Social data contracts and dataclass definitions (Task 2 / B2).

Specification:
- docs/social_data/implementation_plan.md §3-§5, §8, D-008
- work/2026-08-27-unified-final-plan.md Phase 8 / B2

Time Semantics (D-008):
- published_at: Platform source content publication timestamp (XHS note time / DY create_time).
- source_updated_at: Platform source update timestamp (XHS last_update_time; null for DY / comments).
- first_seen_at: MediaCrawler crawler first-seen timestamp (add_ts).
- snapshot_at: MediaCrawler crawler row last-modified timestamp (last_modify_ts).
- ingest_at: TradingAgents archive ingestion clock (UTC). For audit only; NEVER used for eligibility.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, TypedDict


# ============================================================================
# Status Enumeration (§5.1)
# ============================================================================

class SocialStatus(str, Enum):
    """Seven valid social sentiment / archive query statuses."""

    AVAILABLE = "available"
    PARTIAL = "partial"
    EMPTY = "empty"
    REFUSED = "refused"
    FAILED = "failed"
    TIMEOUT = "timeout"
    NOT_APPLICABLE = "not_applicable"


VALID_SOCIAL_STATUSES: Set[str] = {s.value for s in SocialStatus}


# ============================================================================
# Reason Code Constants (§5.5)
# ============================================================================

REASON_SOCIAL_INSUFFICIENT_COVERAGE = "social_insufficient_coverage"
REASON_SOCIAL_PLATFORM_PARTIAL = "social_platform_partial"
REASON_SOCIAL_EMPTY = "social_empty"
REASON_SOCIAL_NOT_APPLICABLE = "social_not_applicable"
REASON_SOCIAL_INVALID_AS_OF = "social_invalid_as_of"
REASON_SOCIAL_FUTURE_AS_OF = "social_future_as_of"
REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT = "social_no_historical_snapshot"
REASON_SOCIAL_ARCHIVE_MISSING = "social_archive_missing"
REASON_SOCIAL_SCHEMA_MISMATCH = "social_schema_mismatch"
REASON_SOCIAL_ARCHIVE_LOCKED = "social_archive_locked"
REASON_OBSERVED_AFTER_CUTOFF_EXCLUDED = "observed_after_cutoff_excluded"


# ============================================================================
# Helper Hash Functions
# ============================================================================

def compute_content_hash(title: Optional[str], text: str) -> str:
    """Compute SHA-256 hash for content (title + text).

    Empty or None title is normalized to empty string.
    """
    normalized_title = (title or "").strip()
    normalized_text = (text or "").strip()
    payload = f"{normalized_title}\n{normalized_text}".encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def compute_metrics_hash(metrics: Dict[str, Any] | SocialMetrics) -> str:
    """Compute deterministic SHA-256 hash for interaction metrics.

    Unknown numbers must remain None and are serialized canonically.
    """
    if isinstance(metrics, SocialMetrics):
        m_dict = metrics.to_dict()
    elif isinstance(metrics, dict):
        m_dict = {
            "likes": metrics.get("likes"),
            "comments": metrics.get("comments"),
            "shares": metrics.get("shares"),
            "collects": metrics.get("collects"),
            "views": metrics.get("views"),
        }
    else:
        m_dict = {"likes": None, "comments": None, "shares": None, "collects": None, "views": None}

    canonical_json = json.dumps(m_dict, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()}"


# ============================================================================
# Raw Record Contract (§4.1)
# ============================================================================

@dataclass
class SocialMetrics:
    """Interaction metrics. Unknown numbers must be None, not 0 (§4.1)."""

    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    collects: Optional[int] = None
    views: Optional[int] = None

    def to_dict(self) -> Dict[str, Optional[int]]:
        return {
            "likes": self.likes,
            "comments": self.comments,
            "shares": self.shares,
            "collects": self.collects,
            "views": self.views,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> SocialMetrics:
        if not d:
            return cls()
        return cls(
            likes=d.get("likes"),
            comments=d.get("comments"),
            shares=d.get("shares"),
            collects=d.get("collects"),
            views=d.get("views"),
        )


@dataclass
class SourceRef:
    """Provenance pointer to the upstream source table and row."""

    provider: str
    crawler_commit: str
    source_table: str
    source_row_id: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "provider": self.provider,
            "crawler_commit": self.crawler_commit,
            "source_table": self.source_table,
            "source_row_id": self.source_row_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> SourceRef:
        return cls(
            provider=str(d.get("provider", "")),
            crawler_commit=str(d.get("crawler_commit", "")),
            source_table=str(d.get("source_table", "")),
            source_row_id=str(d.get("source_row_id", "")),
        )


@dataclass
class EntityMention:
    """Stock entity recognition result linked to a record snapshot."""

    symbol: str
    matched_text: str
    match_method: str
    confidence: float
    resolver_version: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "matched_text": self.matched_text,
            "match_method": self.match_method,
            "confidence": self.confidence,
            "resolver_version": self.resolver_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> EntityMention:
        return cls(
            symbol=str(d.get("symbol", "")),
            matched_text=str(d.get("matched_text", "")),
            match_method=str(d.get("match_method", "")),
            confidence=float(d.get("confidence", 0.0)),
            resolver_version=str(d.get("resolver_version", "")),
        )


@dataclass
class SocialRawRecordV1:
    """Raw social media record snapshot contract (§4.1).

    Time fields:
    - published_at: Platform source publication timestamp.
    - source_updated_at: Platform source update timestamp (optional).
    - first_seen_at: MediaCrawler crawler row creation timestamp (add_ts).
    - snapshot_at: MediaCrawler crawler row modification timestamp (last_modify_ts).
    - ingest_at: TradingAgents archive ingestion timestamp (for audit only).
    """

    record_id: str
    snapshot_id: str
    record_type: str  # 'post' | 'comment'
    platform: str  # 'xhs' | 'dy'
    native_id: str
    root_post_record_id: str
    published_at: str
    first_seen_at: str
    snapshot_at: str
    ingest_at: str
    metrics: SocialMetrics
    content_hash: str
    metrics_hash: str
    ingest_run_id: str
    source_ref: SourceRef
    schema_version: str = "social.raw_record.v1"
    parent_record_id: Optional[str] = None
    source_updated_at: Optional[str] = None
    title: Optional[str] = None
    text: str = ""
    canonical_url: Optional[str] = None
    author_id_hash: Optional[str] = None
    source_keyword: Optional[str] = None
    entities: List[EntityMention] = field(default_factory=list)

    def validate(self) -> None:
        """Validate required fields, platform, and record_type constraints."""
        if self.platform not in ("xhs", "dy"):
            raise ValueError(f"Invalid platform: '{self.platform}', expected 'xhs' or 'dy'")
        if self.record_type not in ("post", "comment"):
            raise ValueError(f"Invalid record_type: '{self.record_type}', expected 'post' or 'comment'")
        if not self.record_id:
            raise ValueError("record_id cannot be empty")
        if not self.snapshot_id:
            raise ValueError("snapshot_id cannot be empty")
        if not self.root_post_record_id:
            raise ValueError("root_post_record_id cannot be empty")
        if not self.published_at:
            raise ValueError("published_at cannot be empty")
        if not self.first_seen_at:
            raise ValueError("first_seen_at cannot be empty")
        if not self.snapshot_at:
            raise ValueError("snapshot_at cannot be empty")
        if not self.ingest_at:
            raise ValueError("ingest_at cannot be empty")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "snapshot_id": self.snapshot_id,
            "record_type": self.record_type,
            "platform": self.platform,
            "native_id": self.native_id,
            "parent_record_id": self.parent_record_id,
            "root_post_record_id": self.root_post_record_id,
            "published_at": self.published_at,
            "source_updated_at": self.source_updated_at,
            "first_seen_at": self.first_seen_at,
            "snapshot_at": self.snapshot_at,
            "ingest_at": self.ingest_at,
            "title": self.title,
            "text": self.text,
            "canonical_url": self.canonical_url,
            "author_id_hash": self.author_id_hash,
            "source_keyword": self.source_keyword,
            "entities": [e.to_dict() for e in self.entities],
            "metrics": self.metrics.to_dict(),
            "content_hash": self.content_hash,
            "metrics_hash": self.metrics_hash,
            "ingest_run_id": self.ingest_run_id,
            "source_ref": self.source_ref.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> SocialRawRecordV1:
        metrics = SocialMetrics.from_dict(d.get("metrics"))
        source_ref = SourceRef.from_dict(d.get("source_ref", {}))
        entities = [EntityMention.from_dict(e) for e in d.get("entities", [])]

        return cls(
            schema_version=d.get("schema_version", "social.raw_record.v1"),
            record_id=d["record_id"],
            snapshot_id=d["snapshot_id"],
            record_type=d["record_type"],
            platform=d["platform"],
            native_id=d["native_id"],
            parent_record_id=d.get("parent_record_id"),
            root_post_record_id=d["root_post_record_id"],
            published_at=d["published_at"],
            source_updated_at=d.get("source_updated_at"),
            first_seen_at=d["first_seen_at"],
            snapshot_at=d["snapshot_at"],
            ingest_at=d["ingest_at"],
            title=d.get("title"),
            text=d.get("text", ""),
            canonical_url=d.get("canonical_url"),
            author_id_hash=d.get("author_id_hash"),
            source_keyword=d.get("source_keyword"),
            entities=entities,
            metrics=metrics,
            content_hash=d["content_hash"],
            metrics_hash=d["metrics_hash"],
            ingest_run_id=d["ingest_run_id"],
            source_ref=source_ref,
        )


# ============================================================================
# Aggregated Sentiment Bundle Contract (§5)
# ============================================================================

@dataclass
class SocialAttention:
    """Social attention and volume metrics (§5.4, §6)."""

    post_count: int = 0
    comment_count: int = 0
    author_count: int = 0
    total_interactions: int = 0
    interaction_velocity: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "post_count": self.post_count,
            "comment_count": self.comment_count,
            "author_count": self.author_count,
            "total_interactions": self.total_interactions,
            "interaction_velocity": self.interaction_velocity,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> SocialAttention:
        if not d:
            return cls()
        return cls(
            post_count=int(d.get("post_count", 0)),
            comment_count=int(d.get("comment_count", 0)),
            author_count=int(d.get("author_count", 0)),
            total_interactions=int(d.get("total_interactions", 0)),
            interaction_velocity=d.get("interaction_velocity"),
        )


@dataclass
class SocialSentiment:
    """Directional sentiment score and counts (§5.1, §5.4)."""

    score: Optional[float] = None
    label: str = "insufficient"  # 'bullish' | 'bearish' | 'neutral' | 'mixed' | 'insufficient'
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    insufficient_count: int = 0
    is_calibrated_probability: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "label": self.label,
            "bullish_count": self.bullish_count,
            "bearish_count": self.bearish_count,
            "neutral_count": self.neutral_count,
            "insufficient_count": self.insufficient_count,
            "is_calibrated_probability": self.is_calibrated_probability,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> SocialSentiment:
        if not d:
            return cls()
        return cls(
            score=d.get("score"),
            label=str(d.get("label", "insufficient")),
            bullish_count=int(d.get("bullish_count", 0)),
            bearish_count=int(d.get("bearish_count", 0)),
            neutral_count=int(d.get("neutral_count", 0)),
            insufficient_count=int(d.get("insufficient_count", 0)),
            is_calibrated_probability=bool(d.get("is_calibrated_probability", False)),
        )


@dataclass
class SentimentBundleV1:
    """Aggregated deterministic sentiment bundle contract (§5).

    Status semantics (§5.1):
    - available: threshold reached, direction_allowed=True, score is float.
    - partial / empty / refused / failed / timeout / not_applicable:
      score=None, label='insufficient', direction_allowed=False.
    """

    status: str
    requested_as_of: str
    cutoff_at: str
    schema_version: str = "social.sentiment_bundle.v1"
    content_as_of: Optional[str] = None
    metric_as_of: Optional[str] = None
    direction_allowed: bool = False
    reason_codes: List[str] = field(default_factory=list)
    symbol: Optional[str] = None
    bundle_id: Optional[str] = None
    social_attention: Optional[SocialAttention] = None
    social_sentiment: Optional[SocialSentiment] = None
    evidence_samples: List[Dict[str, Any]] = field(default_factory=list)
    platform_breakdown: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "requested_as_of": self.requested_as_of,
            "cutoff_at": self.cutoff_at,
            "content_as_of": self.content_as_of,
            "metric_as_of": self.metric_as_of,
            "direction_allowed": self.direction_allowed,
            "reason_codes": list(self.reason_codes),
            "symbol": self.symbol,
            "bundle_id": self.bundle_id,
            "social_attention": self.social_attention.to_dict() if self.social_attention else None,
            "social_sentiment": self.social_sentiment.to_dict() if self.social_sentiment else None,
            "evidence_samples": list(self.evidence_samples),
            "platform_breakdown": dict(self.platform_breakdown),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> SentimentBundleV1:
        att = SocialAttention.from_dict(d.get("social_attention")) if d.get("social_attention") else None
        sent = SocialSentiment.from_dict(d.get("social_sentiment")) if d.get("social_sentiment") else None

        return cls(
            schema_version=d.get("schema_version", "social.sentiment_bundle.v1"),
            status=d["status"],
            requested_as_of=d["requested_as_of"],
            cutoff_at=d["cutoff_at"],
            content_as_of=d.get("content_as_of"),
            metric_as_of=d.get("metric_as_of"),
            direction_allowed=bool(d.get("direction_allowed", False)),
            reason_codes=list(d.get("reason_codes", [])),
            symbol=d.get("symbol"),
            bundle_id=d.get("bundle_id"),
            social_attention=att,
            social_sentiment=sent,
            evidence_samples=list(d.get("evidence_samples", [])),
            platform_breakdown=dict(d.get("platform_breakdown", {})),
        )


def create_empty_sentiment_bundle(
    status: str,
    requested_as_of: str,
    cutoff_at: str,
    reason_codes: Optional[List[str]] = None,
    symbol: Optional[str] = None,
) -> SentimentBundleV1:
    """Create a standardized SentimentBundleV1 for non-available / empty / error outcomes."""
    if status not in VALID_SOCIAL_STATUSES:
        raise ValueError(f"Invalid social status: '{status}'")

    return SentimentBundleV1(
        schema_version="social.sentiment_bundle.v1",
        status=status,
        requested_as_of=requested_as_of,
        cutoff_at=cutoff_at,
        content_as_of=None,
        metric_as_of=None,
        direction_allowed=False,
        reason_codes=list(reason_codes or []),
        symbol=symbol,
        bundle_id=None,
        social_attention=SocialAttention(),
        social_sentiment=SocialSentiment(
            score=None,
            label="insufficient",
            bullish_count=0,
            bearish_count=0,
            neutral_count=0,
            insufficient_count=0,
            is_calibrated_probability=False,
        ),
        evidence_samples=[],
        platform_breakdown={},
    )


# ============================================================================
# Social Data Context (§8)
# ============================================================================

class SocialDataContext(TypedDict):
    """Runtime social evidence context passed across graph states and API (§8)."""

    status: str
    mode: str  # 'disabled' | 'shadow' | 'active'
    requested_as_of: str
    direction_allowed: bool
    reason_codes: List[str]
    bundle: Optional[Dict[str, Any]]
    source_provenance: Dict[str, Any]
    data_failure_ledger: List[Dict[str, Any]]


def create_default_social_data_context(
    status: str = "not_applicable",
    mode: str = "disabled",
    requested_as_of: str = "",
    reason_codes: Optional[List[str]] = None,
    bundle: Optional[SentimentBundleV1 | Dict[str, Any]] = None,
    source_provenance: Optional[Dict[str, Any]] = None,
    data_failure_ledger: Optional[List[Dict[str, Any]]] = None,
) -> SocialDataContext:
    """Create a SocialDataContext dictionary matching §8 contract."""
    b_dict = None
    if isinstance(bundle, SentimentBundleV1):
        b_dict = bundle.to_dict()
    elif isinstance(bundle, dict):
        b_dict = bundle

    direction_allowed = False
    if b_dict is not None:
        direction_allowed = bool(b_dict.get("direction_allowed", False))

    return {
        "status": status,
        "mode": mode,
        "requested_as_of": requested_as_of,
        "direction_allowed": direction_allowed,
        "reason_codes": list(reason_codes or []),
        "bundle": b_dict,
        "source_provenance": dict(source_provenance or {}),
        "data_failure_ledger": list(data_failure_ledger or []),
    }
