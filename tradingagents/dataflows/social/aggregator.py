"""Deterministic Social Sentiment Aggregator and Bundle Generator (Task 6 / B6).

Specification:
- docs/social_data/implementation_plan.md Task 6, §5.1, §5.4, §5.5, D-008
- Output: SentimentBundleV1

Rules (§5.4):
1. Text hash deduplication; duplicate groups have at most 1 unit of weight.
2. Max 5 records per author (anonymous bucket handled stably).
3. Priority: recent posts first, then comments; default caps 100 posts / 300 comments.
4. Base weights: post = 1.5, comment = 1.0; empty text direction weight = 0.0.
5. Time decay: half-life 3.5 days based on published_at (NOT snapshot_at).
6. Metrics multiplier: likes increase weight up to 1.5x.
7. Platform balance: single platform max 65% when both present; single platform data -> partial + direction_allowed=False.
8. Deterministic tie-breaker: record_id ascending.
9. Minimum coverage thresholds: posts >= 3, classified >= 20, authors >= 10.
   Under-threshold -> status=partial/empty, score=None, label='insufficient', direction_allowed=False, reason='social_insufficient_coverage'.
10. Deterministic bundle_id based on canonical inputs and selected record IDs.
11. content_as_of = max(published_at), metric_as_of = max(snapshot_at). Never use ingest_at.
12. is_calibrated_probability = False.
13. Map provider failed/refused/timeout/empty to empty SentimentBundleV1.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from tradingagents.dataflows.social.classifier import (
    StanceClassificationResult,
    StanceClassifier,
    classify_text,
)
from tradingagents.dataflows.social.contracts import (
    REASON_SOCIAL_ARCHIVE_LOCKED,
    REASON_SOCIAL_ARCHIVE_MISSING,
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_FUTURE_AS_OF,
    REASON_SOCIAL_INSUFFICIENT_COVERAGE,
    REASON_SOCIAL_INVALID_AS_OF,
    REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
    REASON_SOCIAL_NOT_APPLICABLE,
    REASON_SOCIAL_PLATFORM_PARTIAL,
    REASON_SOCIAL_SCHEMA_MISMATCH,
    SentimentBundleV1,
    SocialAttention,
    SocialRawRecordV1,
    SocialSentiment,
    SocialStatus,
    compute_content_hash,
    create_empty_sentiment_bundle,
)
from tradingagents.dataflows.social.provider import (
    SocialFetchResult,
    compute_as_of_cutoff,
    parse_iso_datetime,
)

HALF_LIFE_DAYS: float = 3.5
LN2: float = math.log(2.0)
MAX_PLATFORM_SHARE: float = 0.65
MAX_LIKES_MULTIPLIER: float = 1.5


def compute_time_decay(
    published_dt: Optional[datetime],
    cutoff_dt: datetime,
    half_life_days: float = HALF_LIFE_DAYS,
) -> float:
    """Compute exponential time decay factor based on published_at and cutoff_at (Rule 5).

    Formula: 0.5 ** (delta_days / half_life_days) = exp(-ln(2) * delta_days / half_life_days).
    """
    if published_dt is None:
        return 0.0

    delta_seconds = max(0.0, (cutoff_dt - published_dt).total_seconds())
    delta_days = delta_seconds / 86400.0
    return math.exp(-LN2 * delta_days / half_life_days)


def compute_interaction_multiplier(likes: Optional[int]) -> float:
    """Compute interaction multiplier from likes, capped at 1.5x (Rule 6).

    Formula: 1.0 + 0.5 * (ln(1 + likes) / ln(1001)), clamped to [1.0, 1.5].
    """
    if likes is None or likes <= 0:
        return 1.0

    ratio = math.log(1.0 + float(likes)) / math.log(1001.0)
    mult = 1.0 + 0.5 * min(1.0, ratio)
    return min(MAX_LIKES_MULTIPLIER, max(1.0, mult))


def compute_deterministic_bundle_id(
    symbol: Optional[str],
    requested_as_of: str,
    cutoff_at: str,
    status: str,
    direction_allowed: bool,
    score: Optional[float],
    selected_record_ids: Sequence[str],
) -> str:
    """Generate a deterministic bundle_id from canonical fields and selected record IDs (Rule 10)."""
    payload = {
        "schema_version": "social.sentiment_bundle.v1",
        "symbol": symbol or "",
        "requested_as_of": requested_as_of,
        "cutoff_at": cutoff_at,
        "status": status,
        "direction_allowed": direction_allowed,
        "score": f"{score:.6f}" if score is not None else None,
        "record_ids": sorted(list(selected_record_ids)),
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()[:16]
    clean_sym = (symbol or "any").replace(".", "_")
    return f"sb1_{clean_sym}_{requested_as_of}_{digest}"


class SocialSentimentAggregator:
    """Aggregates qualified social media records into a deterministic SentimentBundleV1."""

    def __init__(
        self,
        lookback_days: int = 7,
        max_posts: int = 100,
        max_comments: int = 300,
        max_per_author: int = 5,
        min_posts: int = 3,
        min_classified: int = 20,
        min_authors: int = 10,
        evidence_limit: int = 20,
        classifier: Optional[StanceClassifier] = None,
    ):
        self.lookback_days = lookback_days
        self.max_posts = max_posts
        self.max_comments = max_comments
        self.max_per_author = max_per_author
        self.min_posts = min_posts
        self.min_classified = min_classified
        self.min_authors = min_authors
        self.evidence_limit = evidence_limit
        self.classifier = classifier or StanceClassifier()

    def aggregate(
        self,
        records: Sequence[SocialRawRecordV1],
        symbol: Optional[str] = None,
        as_of: str = "",
        cutoff_at: Optional[str] = None,
        now: Optional[datetime] = None,
        provider_status: Optional[str] = None,
        provider_reason_codes: Optional[List[str]] = None,
        platforms: Optional[Sequence[str]] = None,
    ) -> SentimentBundleV1:
        """Aggregate qualified records into SentimentBundleV1."""
        # 1. Parse and validate as_of and cutoff
        try:
            _, cutoff_utc, _ = compute_as_of_cutoff(
                as_of=as_of,
                lookback_days=self.lookback_days,
                now=now,
            )
        except ValueError as exc:
            reason = str(exc)
            cutoff_iso = cutoff_at or ""
            return create_empty_sentiment_bundle(
                status=SocialStatus.REFUSED.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                reason_codes=[reason],
                symbol=symbol,
            )

        cutoff_iso = (
            cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            if cutoff_utc.microsecond == 0
            else cutoff_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )

        # 2. Check provider-level error / refusal status
        if provider_status in (
            SocialStatus.FAILED.value,
            SocialStatus.REFUSED.value,
            SocialStatus.TIMEOUT.value,
            SocialStatus.NOT_APPLICABLE.value,
        ):
            return create_empty_sentiment_bundle(
                status=provider_status,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                reason_codes=list(provider_reason_codes or []),
                symbol=symbol,
            )

        if not records or len(records) == 0:
            reasons = list(provider_reason_codes or [REASON_SOCIAL_EMPTY])
            if REASON_SOCIAL_EMPTY not in reasons:
                reasons.append(REASON_SOCIAL_EMPTY)
            return create_empty_sentiment_bundle(
                status=SocialStatus.EMPTY.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                reason_codes=reasons,
                symbol=symbol,
            )

        # 3. Sort records stably: posts and comments separated
        # Priority: published_at desc, tie-breaker: record_id asc (Rules 3 & 8)
        posts: List[SocialRawRecordV1] = []
        comments: List[SocialRawRecordV1] = []

        for r in records:
            if r.record_type == "post":
                posts.append(r)
            else:
                comments.append(r)

        def sort_key(rec: SocialRawRecordV1):
            p_dt = parse_iso_datetime(rec.published_at)
            ts = p_dt.timestamp() if p_dt else 0.0
            return (-ts, rec.record_id)

        posts.sort(key=sort_key)
        comments.sort(key=sort_key)

        # 4. Deduplicate by content hash and cap per author (Rules 1 & 2)
        seen_content_hashes: Set[str] = set()
        author_counts: Dict[str, int] = {}

        selected_records: List[SocialRawRecordV1] = []
        selected_posts_count = 0
        selected_comments_count = 0

        # Process posts up to max_posts
        for post in posts:
            if selected_posts_count >= self.max_posts:
                break
            c_hash = post.content_hash or compute_content_hash(post.title, post.text)
            if c_hash in seen_content_hashes:
                continue

            author_key = post.author_id_hash if post.author_id_hash else "__anonymous__"
            if author_counts.get(author_key, 0) >= self.max_per_author:
                continue

            seen_content_hashes.add(c_hash)
            author_counts[author_key] = author_counts.get(author_key, 0) + 1
            selected_records.append(post)
            selected_posts_count += 1

        # Process comments up to max_comments
        for comment in comments:
            if selected_comments_count >= self.max_comments:
                break
            c_hash = comment.content_hash or compute_content_hash(comment.title, comment.text)
            if c_hash in seen_content_hashes:
                continue

            author_key = comment.author_id_hash if comment.author_id_hash else "__anonymous__"
            if author_counts.get(author_key, 0) >= self.max_per_author:
                continue

            seen_content_hashes.add(c_hash)
            author_counts[author_key] = author_counts.get(author_key, 0) + 1
            selected_records.append(comment)
            selected_comments_count += 1

        # 5. Classify records and calculate raw weights
        classified_items: List[Dict[str, Any]] = []
        platform_records: Dict[str, List[Dict[str, Any]]] = {"xhs": [], "dy": []}

        content_as_of_dt: Optional[datetime] = None
        metric_as_of_dt: Optional[datetime] = None

        total_interactions = 0

        for rec in selected_records:
            full_text = f"{rec.title or ''}\n{rec.text or ''}".strip()
            cls_res = self.classifier.classify(full_text)

            p_dt = parse_iso_datetime(rec.published_at)
            s_dt = parse_iso_datetime(rec.snapshot_at)

            # Track content_as_of (max published_at) and metric_as_of (max snapshot_at)
            if p_dt is not None:
                if content_as_of_dt is None or p_dt > content_as_of_dt:
                    content_as_of_dt = p_dt

            # Sum interactions
            likes_val = rec.metrics.likes if rec.metrics and rec.metrics.likes is not None else None
            comments_val = rec.metrics.comments if rec.metrics and rec.metrics.comments is not None else 0
            shares_val = rec.metrics.shares if rec.metrics and rec.metrics.shares is not None else 0
            collects_val = rec.metrics.collects if rec.metrics and rec.metrics.collects is not None else 0

            rec_interactions = (likes_val or 0) + (comments_val or 0) + (shares_val or 0) + (collects_val or 0)
            total_interactions += rec_interactions

            has_valid_metrics = likes_val is not None and likes_val >= 0
            if has_valid_metrics and s_dt is not None:
                if metric_as_of_dt is None or s_dt > metric_as_of_dt:
                    metric_as_of_dt = s_dt

            # Weights (Rules 4, 5, 6)
            is_empty_text = not bool(full_text.strip())
            if is_empty_text:
                base_weight = 0.0
            else:
                base_weight = 1.5 if rec.record_type == "post" else 1.0

            time_decay = compute_time_decay(p_dt, cutoff_utc, self.half_life_days)
            interaction_mult = compute_interaction_multiplier(likes_val) if has_valid_metrics else 1.0

            raw_weight = base_weight * time_decay * interaction_mult

            item = {
                "record": rec,
                "classification": cls_res,
                "raw_weight": raw_weight,
                "base_weight": base_weight,
                "time_decay": time_decay,
                "interaction_mult": interaction_mult,
                "is_empty": is_empty_text,
            }
            classified_items.append(item)
            if rec.platform in platform_records:
                platform_records[rec.platform].append(item)
            else:
                platform_records[rec.platform] = [item]

        # 6. Check platform presence and balance weights (Rule 7)
        present_platforms = [p for p, items in platform_records.items() if len(items) > 0]
        is_single_platform = len(present_platforms) == 1

        # Calculate platform raw weights
        platform_raw_weights: Dict[str, float] = {}
        for p, items in platform_records.items():
            platform_raw_weights[p] = sum(i["raw_weight"] for i in items)

        total_raw_weight = sum(platform_raw_weights.values())

        # Platform multiplier to cap single platform at 65% when multiple platforms present
        platform_multipliers: Dict[str, float] = {p: 1.0 for p in platform_records}
        if len(present_platforms) >= 2 and total_raw_weight > 0:
            for p in present_platforms:
                share = platform_raw_weights[p] / total_raw_weight
                if share > MAX_PLATFORM_SHARE:
                    other_weight = total_raw_weight - platform_raw_weights[p]
                    if other_weight > 0 and platform_raw_weights[p] > 0:
                        target_weight = (MAX_PLATFORM_SHARE / (1.0 - MAX_PLATFORM_SHARE)) * other_weight
                        platform_multipliers[p] = target_weight / platform_raw_weights[p]

        # Apply platform multipliers to get effective weights
        for item in classified_items:
            p = item["record"].platform
            item["effective_weight"] = item["raw_weight"] * platform_multipliers.get(p, 1.0)

        # 7. Check coverage thresholds (Rule 9)
        # Minimum: posts >= 3, classified >= 20, distinct authors >= 10
        total_posts = selected_posts_count
        classified_records = [
            i for i in classified_items
            if not i["is_empty"] and i["classification"].stance in ("bullish", "bearish", "neutral", "mixed")
        ]
        classified_count = len(classified_records)

        # Count distinct author keys across selected records
        distinct_authors_set: Set[str] = set()
        anon_records_count = 0
        for i in classified_items:
            auth = i["record"].author_id_hash
            if auth:
                distinct_authors_set.add(auth)
            else:
                anon_records_count += 1
        # Anonymous author count: if present, count 1 or anon count
        author_count_total = len(distinct_authors_set) + (1 if anon_records_count > 0 else 0)

        is_coverage_sufficient = (
            total_posts >= self.min_posts
            and classified_count >= self.min_classified
            and author_count_total >= self.min_authors
        )

        # 8. Determine final status, direction_allowed, and reason_codes
        reason_codes: List[str] = []
        if is_single_platform:
            reason_codes.append(REASON_SOCIAL_PLATFORM_PARTIAL)

        if not is_coverage_sufficient:
            reason_codes.append(REASON_SOCIAL_INSUFFICIENT_COVERAGE)

        # If both platforms present and coverage sufficient -> AVAILABLE
        if not is_single_platform and is_coverage_sufficient:
            status = SocialStatus.AVAILABLE.value
            direction_allowed = True
        else:
            status = SocialStatus.PARTIAL.value
            direction_allowed = False

        # 9. Compute directional score and stance counts
        bullish_cnt = sum(1 for i in classified_items if i["classification"].stance == "bullish")
        bearish_cnt = sum(1 for i in classified_items if i["classification"].stance == "bearish")
        neutral_cnt = sum(1 for i in classified_items if i["classification"].stance == "neutral")
        insufficient_cnt = sum(
            1 for i in classified_items
            if i["is_empty"] or i["classification"].stance == "unknown"
        )

        final_score: Optional[float] = None
        final_label: str = "insufficient"

        if direction_allowed:
            # Weighted stance sum
            stance_values = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "mixed": 0.0}
            weighted_sum = sum(
                item["effective_weight"] * stance_values.get(item["classification"].stance, 0.0)
                for item in classified_records
            )
            total_eff_weight = sum(item["effective_weight"] for item in classified_records)

            if total_eff_weight > 0:
                raw_score = weighted_sum / total_eff_weight
                final_score = round(max(-1.0, min(1.0, raw_score)), 4)
            else:
                final_score = 0.0

            if final_score >= 0.15:
                final_label = "bullish"
            elif final_score <= -0.15:
                final_label = "bearish"
            elif bullish_cnt > 0 and bearish_cnt > 0:
                final_label = "mixed"
            else:
                final_label = "neutral"

        # 10. Format time as_of strings (Rule 11)
        content_as_of_iso = (
            content_as_of_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            if content_as_of_dt
            else None
        )
        metric_as_of_iso = (
            metric_as_of_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            if metric_as_of_dt
            else None
        )

        # 11. Build SocialAttention and SocialSentiment
        interaction_velocity = round(total_interactions / max(1.0, float(self.lookback_days)), 2)

        social_attention = SocialAttention(
            post_count=selected_posts_count,
            comment_count=selected_comments_count,
            author_count=author_count_total,
            total_interactions=total_interactions,
            interaction_velocity=interaction_velocity,
        )

        social_sentiment = SocialSentiment(
            score=final_score,
            label=final_label,
            bullish_count=bullish_cnt,
            bearish_count=bearish_cnt,
            neutral_count=neutral_cnt,
            insufficient_count=insufficient_cnt,
            is_calibrated_probability=False,
        )

        # 12. Build evidence samples (top N by effective weight, tie-breaker record_id)
        sorted_evidence = sorted(
            classified_items,
            key=lambda x: (-x["effective_weight"], x["record"].record_id),
        )
        evidence_samples: List[Dict[str, Any]] = []
        for item in sorted_evidence[: self.evidence_limit]:
            rec = item["record"]
            cls_res = item["classification"]
            evidence_samples.append({
                "record_id": rec.record_id,
                "platform": rec.platform,
                "record_type": rec.record_type,
                "published_at": rec.published_at,
                "author_id_hash": rec.author_id_hash,
                "title": rec.title,
                "text_snippet": (rec.text or "")[:120],
                "stance": cls_res.stance,
                "weight": round(item["effective_weight"], 4),
                "likes": rec.metrics.likes if rec.metrics else None,
                "hit_keywords": [h.keyword for h in cls_res.hits],
            })

        # 13. Build platform breakdown
        platform_breakdown: Dict[str, Any] = {}
        total_effective_weight = sum(item["effective_weight"] for item in classified_items)
        for p, items in platform_records.items():
            p_eff_weight = sum(i["effective_weight"] for i in items)
            p_share = round(p_eff_weight / total_effective_weight, 4) if total_effective_weight > 0 else 0.0
            platform_breakdown[p] = {
                "post_count": sum(1 for i in items if i["record"].record_type == "post"),
                "comment_count": sum(1 for i in items if i["record"].record_type == "comment"),
                "author_count": len(set(i["record"].author_id_hash for i in items if i["record"].author_id_hash)),
                "classified_count": sum(
                    1 for i in items
                    if not i["is_empty"] and i["classification"].stance in ("bullish", "bearish", "neutral", "mixed")
                ),
                "total_interactions": sum(
                    (i["record"].metrics.likes or 0) for i in items if i["record"].metrics
                ),
                "effective_weight": round(p_eff_weight, 4),
                "weight_share": p_share,
            }

        # 14. Compute deterministic bundle_id (Rule 10)
        selected_record_ids = [r.record_id for r in selected_records]
        bundle_id = compute_deterministic_bundle_id(
            symbol=symbol,
            requested_as_of=as_of,
            cutoff_at=cutoff_iso,
            status=status,
            direction_allowed=direction_allowed,
            score=final_score,
            selected_record_ids=selected_record_ids,
        )

        return SentimentBundleV1(
            schema_version="social.sentiment_bundle.v1",
            status=status,
            requested_as_of=as_of,
            cutoff_at=cutoff_iso,
            content_as_of=content_as_of_iso,
            metric_as_of=metric_as_of_iso,
            direction_allowed=direction_allowed,
            reason_codes=reason_codes,
            symbol=symbol,
            bundle_id=bundle_id,
            social_attention=social_attention,
            social_sentiment=social_sentiment,
            evidence_samples=evidence_samples,
            platform_breakdown=platform_breakdown,
        )

    @property
    def half_life_days(self) -> float:
        return HALF_LIFE_DAYS


def aggregate_sentiment_bundle(
    records: Union[Sequence[SocialRawRecordV1], SocialFetchResult],
    symbol: Optional[str] = None,
    as_of: str = "",
    cutoff_at: Optional[str] = None,
    lookback_days: int = 7,
    max_posts: int = 100,
    max_comments: int = 300,
    max_per_author: int = 5,
    min_posts: int = 3,
    min_classified: int = 20,
    min_authors: int = 10,
    evidence_limit: int = 20,
    classifier: Optional[StanceClassifier] = None,
    now: Optional[datetime] = None,
    platforms: Optional[Sequence[str]] = None,
) -> SentimentBundleV1:
    """Convenience function to aggregate records or fetch result into SentimentBundleV1."""
    aggregator = SocialSentimentAggregator(
        lookback_days=lookback_days,
        max_posts=max_posts,
        max_comments=max_comments,
        max_per_author=max_per_author,
        min_posts=min_posts,
        min_classified=min_classified,
        min_authors=min_authors,
        evidence_limit=evidence_limit,
        classifier=classifier,
    )

    provider_status = None
    provider_reasons = None
    if isinstance(records, SocialFetchResult):
        provider_status = records.status
        provider_reasons = records.reason_codes
        cutoff_at = cutoff_at or records.cutoff_at
        as_of = as_of or records.requested_as_of
        raw_records = records.records
    else:
        raw_records = list(records)

    return aggregator.aggregate(
        records=raw_records,
        symbol=symbol,
        as_of=as_of,
        cutoff_at=cutoff_at,
        now=now,
        provider_status=provider_status,
        provider_reason_codes=provider_reasons,
        platforms=platforms,
    )
