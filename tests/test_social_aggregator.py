"""Unit and integration tests for Social Classifier and Aggregator (Task 6 / B6).

Specification:
- docs/social_data/implementation_plan.md Task 6, §5.1, §5.4, §5.5, D-008
- Pure offline fixtures; NO real network; NO @pytest.mark.asyncio.
"""

from datetime import datetime, timezone
import pytest

from tradingagents.dataflows.social.classifier import (
    LEXICON_VERSION,
    StanceClassificationResult,
    StanceClassifier,
    StanceHit,
    classify_text,
)
from tradingagents.dataflows.social.aggregator import (
    HALF_LIFE_DAYS,
    MAX_PLATFORM_SHARE,
    SocialSentimentAggregator,
    aggregate_sentiment_bundle,
    compute_deterministic_bundle_id,
    compute_interaction_multiplier,
    compute_time_decay,
)
from tradingagents.dataflows.social.contracts import (
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_INSUFFICIENT_COVERAGE,
    REASON_SOCIAL_PLATFORM_PARTIAL,
    SentimentBundleV1,
    SocialMetrics,
    SocialRawRecordV1,
    SocialStatus,
    SourceRef,
    compute_content_hash,
    compute_metrics_hash,
)
from tradingagents.dataflows.social.provider import SocialFetchResult


# ============================================================================
# Helpers to construct mock SocialRawRecordV1
# ============================================================================

def make_record(
    record_id: str,
    platform: str = "xhs",
    record_type: str = "post",
    title: str = "",
    text: str = "",
    published_at: str = "2026-08-26T10:00:00Z",
    snapshot_at: str = "2026-08-26T11:00:00Z",
    ingest_at: str = "2026-08-26T12:00:00Z",
    first_seen_at: str = "2026-08-26T10:30:00Z",
    author_id_hash: str = "sha256:author_001",
    likes: int = 10,
    comments: int = 2,
) -> SocialRawRecordV1:
    metrics = SocialMetrics(likes=likes, comments=comments, shares=None, collects=None, views=None)
    content_h = compute_content_hash(title, text)
    metrics_h = compute_metrics_hash(metrics)
    source_ref = SourceRef("mediacrawler", "commit1", f"{platform}_table", record_id)

    return SocialRawRecordV1(
        record_id=record_id,
        snapshot_id=f"snap:{record_id}",
        record_type=record_type,
        platform=platform,
        native_id=record_id.split(":")[-1],
        root_post_record_id=record_id if record_type == "post" else f"{platform}:post:root",
        published_at=published_at,
        first_seen_at=first_seen_at,
        snapshot_at=snapshot_at,
        ingest_at=ingest_at,
        title=title,
        text=text,
        author_id_hash=author_id_hash,
        metrics=metrics,
        content_hash=content_h,
        metrics_hash=metrics_h,
        ingest_run_id="run-001",
        source_ref=source_ref,
    )


# ============================================================================
# 1. Classifier Tests (Stance, Negation, Unknown ≠ Neutral, Empty)
# ============================================================================

def test_classifier_basic_polarities():
    """Test standard bullish, bearish, and neutral classification."""
    r_bull = classify_text("寒武纪今天大涨突破！")
    assert r_bull.stance == "bullish"
    assert r_bull.bullish_hits > 0
    assert r_bull.bearish_hits == 0

    r_bear = classify_text("今天全线崩溃，主力资金流出，大跌跳水！")
    assert r_bear.stance == "bearish"
    assert r_bear.bearish_hits > 0
    assert r_bull.lexicon_version == LEXICON_VERSION

    r_neu = classify_text("股价在箱体震荡整理，维持窄幅震荡，保持观望。")
    assert r_neu.stance == "neutral"
    assert r_neu.neutral_hits > 0
    assert r_neu.bullish_hits == 0
    assert r_neu.bearish_hits == 0


def test_classifier_negation_flips_polarity():
    """Requirement 7: Negation word within 3 Chinese characters flips polarity."""
    # Bullish -> Bearish
    r1 = classify_text("寒武纪今天并没有大涨")
    assert r1.stance == "bearish"
    assert any(h.is_negated and h.original_polarity == "bullish" and h.effective_polarity == "bearish" for h in r1.hits)

    r2 = classify_text("绝对不会买入")
    assert r2.stance == "bearish"
    assert r2.hits[0].is_negated is True
    assert r2.hits[0].negation_word == "绝对不会"

    # Bearish -> Bullish
    r3 = classify_text("不会大跌")
    assert r3.stance == "bullish"
    assert any(h.is_negated and h.original_polarity == "bearish" and h.effective_polarity == "bullish" for h in r3.hits)

    r4 = classify_text("并没有大跌")
    assert r4.stance == "bullish"

    # Distance > 3 characters does NOT flip polarity
    # e.g. "不是因为其他原因，今天大涨" -> negation '不是' is >3 chars before '大涨'
    r5 = classify_text("不是因为其他原因，今天大涨")
    assert r5.stance == "bullish"
    assert any(not h.is_negated and h.effective_polarity == "bullish" for h in r5.hits)


def test_classifier_unknown_not_equal_to_neutral():
    """Requirement 8: Unrecognized text is 'unknown', NOT 'neutral'."""
    r_unknown = classify_text("公司发布了2026年半年度报告及董事会决议公告")
    assert r_unknown.stance == "unknown"
    assert r_unknown.stance != "neutral"
    assert len(r_unknown.hits) == 0

    r_random = classify_text("今天天气真好，去公园散步了。")
    assert r_random.stance == "unknown"


def test_classifier_empty_text():
    """Requirement 1: Empty text produces stance='unknown' and is_empty=True."""
    r_empty1 = classify_text("")
    assert r_empty1.stance == "unknown"
    assert r_empty1.is_empty is True

    r_empty2 = classify_text("   \n\t  ")
    assert r_empty2.stance == "unknown"
    assert r_empty2.is_empty is True


def test_classifier_mixed_stance():
    """Text with both bullish and bearish keywords becomes 'mixed'."""
    r_mixed = classify_text("短期来看大涨突破，但长期可能有大跌破位风险")
    assert r_mixed.stance == "mixed"
    assert r_mixed.bullish_hits > 0
    assert r_mixed.bearish_hits > 0


# ============================================================================
# 2. Time Decay and Interaction Multipliers
# ============================================================================

def test_time_decay_by_published_at():
    """Requirement 2: Half-life 3.5 days based on published_at."""
    cutoff = datetime(2026, 8, 26, 15, 59, 59, tzinfo=timezone.utc)

    # 0 days age -> decay = 1.0
    p0 = datetime(2026, 8, 26, 15, 59, 59, tzinfo=timezone.utc)
    assert pytest.approx(compute_time_decay(p0, cutoff), rel=1e-3) == 1.0

    # 3.5 days age -> decay = 0.5
    p_half = datetime(2026, 8, 23, 3, 59, 59, tzinfo=timezone.utc)
    assert pytest.approx(compute_time_decay(p_half, cutoff), rel=1e-3) == 0.5

    # 7.0 days age -> decay = 0.25
    p_double = datetime(2026, 8, 19, 15, 59, 59, tzinfo=timezone.utc)
    assert pytest.approx(compute_time_decay(p_double, cutoff), rel=1e-3) == 0.25


def test_interaction_multiplier_likes():
    """Requirement 6: Interaction weight multiplier <= 1.5."""
    assert compute_interaction_multiplier(None) == 1.0
    assert compute_interaction_multiplier(0) == 1.0
    assert compute_interaction_multiplier(-5) == 1.0

    m10 = compute_interaction_multiplier(10)
    assert 1.0 < m10 < 1.5

    m1000 = compute_interaction_multiplier(1000)
    assert pytest.approx(m1000, rel=1e-2) == 1.5

    m10000 = compute_interaction_multiplier(100000)
    assert m10000 == 1.5


# ============================================================================
# 3. Aggregation & Coverage Threshold Tests (§5.4)
# ============================================================================

def test_empty_text_zero_direction_weight_but_counts_attention():
    """Requirement 1: Empty text does not count towards direction, but counts towards attention."""
    cutoff_iso = "2026-08-26T15:59:59Z"
    records = []

    # 2 non-empty posts + 1 empty post
    records.append(make_record("xhs:post:1", platform="xhs", title="寒武纪大涨", text="看多加仓", author_id_hash="sha:a1"))
    records.append(make_record("dy:post:2", platform="dy", title="寒武纪突破", text="买入做多", author_id_hash="sha:a2"))
    records.append(make_record("xhs:post:3", platform="xhs", title="", text="", author_id_hash="sha:a3"))  # empty text

    bundle = aggregate_sentiment_bundle(
        records=records,
        symbol="688256.SH",
        as_of="2026-08-26",
        cutoff_at=cutoff_iso,
        min_posts=3,
        min_classified=2,
        min_authors=2,
    )

    # Attention counts all 3 posts
    assert bundle.social_attention.post_count == 3
    # Directional counts only classified non-empty
    assert bundle.social_sentiment.bullish_count == 2
    assert bundle.social_sentiment.insufficient_count == 1


def test_insufficient_coverage_yields_insufficient_and_null_score():
    """Requirement 4: Below threshold -> score=None, label='insufficient', direction_allowed=False,

    reason='social_insufficient_coverage', NEVER 0.0/neutral.
    """
    records = [
        make_record(f"xhs:post:{i}", platform="xhs", title="大涨", text="看多", author_id_hash=f"sha:auth_{i}")
        for i in range(2)  # only 2 posts < min_posts=3
    ] + [
        make_record(f"dy:post:{i}", platform="dy", title="大涨", text="看多", author_id_hash=f"sha:auth_{i+2}")
        for i in range(2)
    ]

    bundle = aggregate_sentiment_bundle(
        records=records,
        symbol="688256.SH",
        as_of="2026-08-26",
        min_posts=5,  # 4 < 5
        min_classified=20,
        min_authors=10,
    )

    assert bundle.status == "partial"
    assert bundle.direction_allowed is False
    assert bundle.social_sentiment.score is None
    assert bundle.social_sentiment.label == "insufficient"
    assert REASON_SOCIAL_INSUFFICIENT_COVERAGE in bundle.reason_codes


def test_single_platform_partial_status():
    """Requirement 5: Single platform data -> partial status, direction_allowed=False."""
    # Build 25 posts from 15 authors on XHS only (sufficient coverage on XHS alone, but no DY)
    records = [
        make_record(f"xhs:post:{i}", platform="xhs", title="大涨", text="买入看多", author_id_hash=f"sha:auth_{i % 12}")
        for i in range(25)
    ]

    bundle = aggregate_sentiment_bundle(
        records=records,
        symbol="688256.SH",
        as_of="2026-08-26",
        min_posts=3,
        min_classified=20,
        min_authors=10,
    )

    assert bundle.status == "partial"
    assert bundle.direction_allowed is False
    assert bundle.social_sentiment.score is None
    assert bundle.social_sentiment.label == "insufficient"
    assert REASON_SOCIAL_PLATFORM_PARTIAL in bundle.reason_codes


def test_full_available_bundle_with_dual_platform():
    """Sufficient coverage on both platforms -> available, score float, direction_allowed=True."""
    # 15 distinct XHS records from 8 authors
    xhs_records = [
        make_record(f"xhs:post:{i}", platform="xhs", title=f"寒武纪大涨{i}", text=f"看多突破{i}", author_id_hash=f"sha:xhs_auth_{i % 8}")
        for i in range(15)
    ]
    # 15 distinct DY records from 8 authors
    dy_records = [
        make_record(f"dy:post:{i}", platform="dy", title=f"寒武纪大涨{i}", text=f"买入做多{i}", author_id_hash=f"sha:dy_auth_{i % 8}")
        for i in range(15)
    ]
    all_records = xhs_records + dy_records

    bundle = aggregate_sentiment_bundle(
        records=all_records,
        symbol="688256.SH",
        as_of="2026-08-26",
        min_posts=3,
        min_classified=20,
        min_authors=10,
    )

    assert bundle.status == "available"
    assert bundle.direction_allowed is True
    assert isinstance(bundle.social_sentiment.score, float)
    assert bundle.social_sentiment.score > 0.0
    assert bundle.social_sentiment.label == "bullish"
    assert bundle.social_sentiment.is_calibrated_probability is False
    assert bundle.content_as_of is not None
    assert bundle.metric_as_of is not None


def test_bundle_determinism_same_input():
    """Requirement 6: Same input twice yields identical bundle_id and score."""
    records1 = [
        make_record(f"xhs:post:{i}", platform="xhs", title="大涨", text="买入看多", author_id_hash=f"sha:a_{i%6}")
        for i in range(12)
    ] + [
        make_record(f"dy:post:{i}", platform="dy", title="突破", text="加仓做多", author_id_hash=f"sha:b_{i%6}")
        for i in range(12)
    ]

    bundle1 = aggregate_sentiment_bundle(
        records=records1,
        symbol="688256.SH",
        as_of="2026-08-26",
        min_posts=3,
        min_classified=20,
        min_authors=10,
    )

    bundle2 = aggregate_sentiment_bundle(
        records=records1,
        symbol="688256.SH",
        as_of="2026-08-26",
        min_posts=3,
        min_classified=20,
        min_authors=10,
    )

    assert bundle1.bundle_id == bundle2.bundle_id
    assert bundle1.social_sentiment.score == bundle2.social_sentiment.score
    assert bundle1.to_dict() == bundle2.to_dict()


def test_text_hash_deduplication_and_author_capping():
    """Test text deduplication (duplicate content hash gets 1 weight) and max 5 per author."""
    # 10 identical duplicate posts from author1
    dup_records = [
        make_record(f"xhs:post:dup_{i}", platform="xhs", title="相同标题", text="相同正文大涨", author_id_hash="sha:author1")
        for i in range(10)
    ]
    # 7 distinct posts from author2
    author2_records = [
        make_record(f"dy:post:a2_{i}", platform="dy", title=f"标题{i}", text=f"正文大涨{i}", author_id_hash="sha:author2")
        for i in range(7)
    ]

    aggregator = SocialSentimentAggregator(max_per_author=5)
    bundle = aggregator.aggregate(
        records=dup_records + author2_records,
        symbol="688256.SH",
        as_of="2026-08-26",
    )

    # Duplicate posts should be deduped to 1
    # Author2 posts should be capped at 5
    # Total selected posts = 1 + 5 = 6
    assert bundle.social_attention.post_count == 6


def test_platform_share_capped_at_65_percent():
    """Test single platform final weight share capped at 65% when both platforms present."""
    # 50 XHS posts vs 3 DY posts
    xhs_records = [
        make_record(f"xhs:post:{i}", platform="xhs", title=f"XHS标题{i}", text="看多大涨", author_id_hash=f"sha:x_{i%10}")
        for i in range(30)
    ]
    dy_records = [
        make_record(f"dy:post:{i}", platform="dy", title=f"DY标题{i}", text="看空大跌", author_id_hash=f"sha:d_{i}")
        for i in range(3)
    ]

    bundle = aggregate_sentiment_bundle(
        records=xhs_records + dy_records,
        symbol="688256.SH",
        as_of="2026-08-26",
        min_posts=3,
        min_classified=10,
        min_authors=5,
    )

    # Check breakdown platform share
    assert bundle.platform_breakdown["xhs"]["weight_share"] <= 0.6501
    assert bundle.platform_breakdown["dy"]["weight_share"] >= 0.3499


def test_content_and_metric_as_of_timestamps():
    """Requirement 11: content_as_of and metric_as_of are derived correctly, never using ingest_at."""
    records = [
        make_record(
            "xhs:post:1",
            platform="xhs",
            published_at="2026-08-25T08:00:00Z",
            snapshot_at="2026-08-25T09:00:00Z",
            ingest_at="2026-08-26T18:00:00Z",  # ingest_at is much later
            author_id_hash="sha:a1",
        ),
        make_record(
            "dy:post:2",
            platform="dy",
            published_at="2026-08-26T04:00:00Z",
            snapshot_at="2026-08-26T05:30:00Z",
            ingest_at="2026-08-26T19:00:00Z",
            author_id_hash="sha:a2",
        ),
    ]

    bundle = aggregate_sentiment_bundle(
        records=records,
        symbol="688256.SH",
        as_of="2026-08-26",
    )

    assert bundle.content_as_of == "2026-08-26T04:00:00Z"
    assert bundle.metric_as_of == "2026-08-26T05:30:00Z"
    assert bundle.content_as_of != "2026-08-26T19:00:00Z"
    assert bundle.metric_as_of != "2026-08-26T19:00:00Z"


def test_provider_status_mapping_to_empty_bundle():
    """Requirement 13: provider failed/refused/timeout/empty mapped to empty bundle."""
    fetch_res_failed = SocialFetchResult(
        status="failed",
        requested_as_of="2026-08-26",
        cutoff_at="2026-08-26T15:59:59Z",
        reason_codes=["social_archive_missing"],
    )

    b_failed = aggregate_sentiment_bundle(fetch_res_failed, symbol="688256.SH")
    assert b_failed.status == "failed"
    assert b_failed.direction_allowed is False
    assert b_failed.social_sentiment.score is None
    assert b_failed.social_sentiment.label == "insufficient"
    assert "social_archive_missing" in b_failed.reason_codes

    fetch_res_empty = SocialFetchResult(
        status="empty",
        requested_as_of="2026-08-26",
        cutoff_at="2026-08-26T15:59:59Z",
        reason_codes=["social_empty"],
    )
    b_empty = aggregate_sentiment_bundle(fetch_res_empty, symbol="688256.SH")
    assert b_empty.status == "empty"
    assert b_empty.direction_allowed is False


def test_likes_snapshot_cutoff_isolation_end_to_end(tmp_path):
    """Requirement 3: likes in post-cutoff snapshot must NOT affect the historical as_of bundle."""
    import sqlite3
    from tradingagents.dataflows.social.archive_schema import init_archive_db
    from tradingagents.dataflows.social.provider import SocialArchiveProvider

    db_path = str(tmp_path / "test_cutoff_isolation.db")
    init_archive_db(db_path)

    conn = sqlite3.connect(db_path)
    # Insert run
    conn.execute(
        "INSERT INTO social_ingest_runs (run_id, provider, platform, query_text, started_at, completed_at, status, crawler_commit, source_schema_fingerprint, rows_read, rows_inserted, rows_rejected) "
        "VALUES ('run1', 'mediacrawler', 'xhs', '688256', '2026-08-26T00:00:00Z', '2026-08-26T00:05:00Z', 'completed', 'commit1', 'fp1', 2, 2, 0)"
    )
    # Snapshot 1: before cutoff on 2026-08-25 with 20 likes
    conn.execute(
        "INSERT INTO social_record_snapshots (snapshot_id, record_id, schema_version, record_type, platform, native_id, root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at, title, text, metrics_json, content_hash, metrics_hash, ingest_run_id, source_table, source_row_id) "
        "VALUES ('s1', 'xhs:post:1', 'social.raw_record.v1', 'post', 'xhs', '1', 'xhs:post:1', '2026-08-25T08:00:00Z', '2026-08-25T08:30:00Z', '2026-08-25T09:00:00Z', '2026-08-25T09:05:00Z', '寒武纪大涨', '看多加仓', '{\"likes\": 20, \"comments\": 0, \"shares\": null, \"collects\": null, \"views\": null}', 'ch1', 'mh1', 'run1', 'xhs_note', '1')"
    )
    # Snapshot 2: after cutoff on 2026-08-28 with 99999 likes (inflated)
    conn.execute(
        "INSERT INTO social_record_snapshots (snapshot_id, record_id, schema_version, record_type, platform, native_id, root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at, title, text, metrics_json, content_hash, metrics_hash, ingest_run_id, source_table, source_row_id) "
        "VALUES ('s2', 'xhs:post:1', 'social.raw_record.v1', 'post', 'xhs', '1', 'xhs:post:1', '2026-08-25T08:00:00Z', '2026-08-25T08:30:00Z', '2026-08-28T09:00:00Z', '2026-08-28T09:05:00Z', '寒武纪大涨', '看多加仓', '{\"likes\": 99999, \"comments\": 0, \"shares\": null, \"collects\": null, \"views\": null}', 'ch1', 'mh2', 'run1', 'xhs_note', '1')"
    )
    # Add entity mention
    conn.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) "
        "VALUES ('s1', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1'), ('s2', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')"
    )
    conn.commit()
    conn.close()

    # Query with as_of 2026-08-26 (historical day)
    provider = SocialArchiveProvider(db_path=db_path)
    fetch_res = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26")

    assert len(fetch_res.records) == 1
    selected_record = fetch_res.records[0]
    assert selected_record.snapshot_id == "s1"
    assert selected_record.metrics.likes == 20
    assert selected_record.snapshot_at == "2026-08-25T09:00:00Z"

    bundle = aggregate_sentiment_bundle(fetch_res, symbol="688256.SH")
    assert bundle.metric_as_of == "2026-08-25T09:00:00Z"
    assert bundle.evidence_samples[0]["likes"] == 20


def test_tie_breaker_deterministic_ordering():
    """Requirement 8: Stable tie-breaker is record_id ascending."""
    records = [
        make_record("xhs:post:b", platform="xhs", title="标题", text="看多大涨", published_at="2026-08-26T10:00:00Z", author_id_hash="sha:a1"),
        make_record("xhs:post:a", platform="xhs", title="标题2", text="看多大涨", published_at="2026-08-26T10:00:00Z", author_id_hash="sha:a2"),
        make_record("dy:post:c", platform="dy", title="标题3", text="看多大涨", published_at="2026-08-26T10:00:00Z", author_id_hash="sha:a3"),
    ]

    bundle1 = aggregate_sentiment_bundle(records, symbol="688256.SH", as_of="2026-08-26")
    bundle2 = aggregate_sentiment_bundle(list(reversed(records)), symbol="688256.SH", as_of="2026-08-26")

    assert bundle1.bundle_id == bundle2.bundle_id
    assert [e["record_id"] for e in bundle1.evidence_samples] == [e["record_id"] for e in bundle2.evidence_samples]


def test_aggregator_zero_rows_after_deduplication_marked_empty():
    """M7: When records are filtered/capped/deduplicated to 0 selected rows, bundle must be empty (not partial)."""
    # 2 duplicate records
    rec1 = make_record("xhs:post:1", platform="xhs", title="同文", text="同文", author_id_hash="sha:a1")
    rec2 = make_record("xhs:post:2", platform="xhs", title="同文", text="同文", author_id_hash="sha:a1")

    # If max_posts=0, max_comments=0, 0 records will be selected from the input list
    agg = SocialSentimentAggregator(max_posts=0, max_comments=0)
    bundle = agg.aggregate(
        records=[rec1, rec2],
        symbol="688256.SH",
        as_of="2026-08-26",
    )

    assert bundle.status == SocialStatus.EMPTY.value
    assert bundle.direction_allowed is False
    assert bundle.social_sentiment.score is None
    assert bundle.social_sentiment.label == "insufficient"
    assert REASON_SOCIAL_EMPTY in bundle.reason_codes
    assert bundle.social_attention.post_count == 0
    assert bundle.social_attention.comment_count == 0

