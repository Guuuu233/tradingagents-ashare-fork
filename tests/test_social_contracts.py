"""Unit tests for social data contracts and archive SQLite schema (Task 2 / B2).

Specifications:
- docs/social_data/implementation_plan.md Task 2 + §3-§4 + D-008
- work/2026-08-27-unified-final-plan.md Phase 8 / B2
"""

import json
import sqlite3
import pytest
from dataclasses import asdict

from tradingagents.dataflows.social.contracts import (
    VALID_SOCIAL_STATUSES,
    SocialStatus,
    SocialMetrics,
    SourceRef,
    EntityMention,
    SocialRawRecordV1,
    SocialAttention,
    SocialSentiment,
    SentimentBundleV1,
    SocialDataContext,
    compute_content_hash,
    compute_metrics_hash,
    create_empty_sentiment_bundle,
    create_default_social_data_context,
    REASON_SOCIAL_INSUFFICIENT_COVERAGE,
    REASON_SOCIAL_PLATFORM_PARTIAL,
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_NOT_APPLICABLE,
    REASON_SOCIAL_INVALID_AS_OF,
    REASON_SOCIAL_FUTURE_AS_OF,
    REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
    REASON_SOCIAL_ARCHIVE_MISSING,
    REASON_SOCIAL_SCHEMA_MISMATCH,
    REASON_SOCIAL_ARCHIVE_LOCKED,
)
from tradingagents.dataflows.social.archive_schema import (
    SCHEMA_SQL,
    ARCHIVE_SCHEMA_VERSION,
    TABLES,
    INDEXES,
    init_archive_db,
    verify_archive_schema,
)


# ============================================================================
# 1. Status Enumeration Tests
# ============================================================================

def test_seven_social_statuses():
    """Requirement 1: Exactly seven valid statuses."""
    expected = {
        "available",
        "partial",
        "empty",
        "refused",
        "failed",
        "timeout",
        "not_applicable",
    }
    assert VALID_SOCIAL_STATUSES == expected
    assert {s.value for s in SocialStatus} == expected


def test_status_validation():
    """Test status validation helper and enum access."""
    assert SocialStatus.AVAILABLE.value == "available"
    assert SocialStatus.PARTIAL.value == "partial"
    assert SocialStatus.EMPTY.value == "empty"
    assert SocialStatus.REFUSED.value == "refused"
    assert SocialStatus.FAILED.value == "failed"
    assert SocialStatus.TIMEOUT.value == "timeout"
    assert SocialStatus.NOT_APPLICABLE.value == "not_applicable"

    with pytest.raises(ValueError):
        SocialStatus("unknown_status")


# ============================================================================
# 2. Time Field Names and Forbidden Alias Tests (D-008)
# ============================================================================

def test_time_field_names_and_no_forbidden_aliases():
    """Requirement 2 & 4: Time fields must be published_at / source_updated_at /

    first_seen_at / snapshot_at / ingest_at.
    Contracts must NOT contain forbidden aliases (content_observed_at, metric_observed_at).
    """
    raw_fields = SocialRawRecordV1.__dataclass_fields__
    assert "published_at" in raw_fields
    assert "source_updated_at" in raw_fields
    assert "first_seen_at" in raw_fields
    assert "snapshot_at" in raw_fields
    assert "ingest_at" in raw_fields

    # Forbidden aliases check
    assert "content_observed_at" not in raw_fields
    assert "metric_observed_at" not in raw_fields
    assert "add_ts" not in raw_fields
    assert "last_modify_ts" not in raw_fields


def test_sentiment_bundle_time_fields():
    """Test SentimentBundleV1 time fields."""
    bundle_fields = SentimentBundleV1.__dataclass_fields__
    assert "requested_as_of" in bundle_fields
    assert "cutoff_at" in bundle_fields
    assert "content_as_of" in bundle_fields
    assert "metric_as_of" in bundle_fields
    # ingest_at must NOT be a field in SentimentBundleV1 as an as-of qualifier
    assert "ingest_as_of" not in bundle_fields


# ============================================================================
# 3. SocialRawRecordV1 Contract Tests
# ============================================================================

def test_social_raw_record_v1_construction():
    """Test valid SocialRawRecordV1 creation and serialization."""
    metrics = SocialMetrics(
        likes=123,
        comments=45,
        shares=None,
        collects=20,
        views=None,
    )
    source_ref = SourceRef(
        provider="mediacrawler",
        crawler_commit="d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
        source_table="xhs_note",
        source_row_id="12345",
    )
    c_hash = compute_content_hash("寒武纪还能不能追", "正文内容...")
    m_hash = compute_metrics_hash(metrics.to_dict())

    record = SocialRawRecordV1(
        record_id="xhs:post:65abc",
        snapshot_id=f"sha256:{c_hash[:16]}_{m_hash[:16]}",
        record_type="post",
        platform="xhs",
        native_id="65abc",
        parent_record_id=None,
        root_post_record_id="xhs:post:65abc",
        published_at="2026-08-26T03:12:11Z",
        source_updated_at="2026-08-26T03:40:00Z",
        first_seen_at="2026-08-26T04:00:02Z",
        snapshot_at="2026-08-26T06:10:00Z",
        ingest_at="2026-08-26T06:15:00Z",
        title="寒武纪还能不能追",
        text="正文内容...",
        canonical_url="https://www.xiaohongshu.com/explore/65abc",
        author_id_hash="sha256:author123",
        source_keyword="寒武纪",
        entities=[],
        metrics=metrics,
        content_hash=c_hash,
        metrics_hash=m_hash,
        ingest_run_id="run-uuid-001",
        source_ref=source_ref,
    )

    assert record.schema_version == "social.raw_record.v1"
    d = record.to_dict()
    assert d["schema_version"] == "social.raw_record.v1"
    assert d["record_id"] == "xhs:post:65abc"
    assert d["metrics"]["likes"] == 123
    assert d["metrics"]["shares"] is None

    # Round trip from dict
    restored = SocialRawRecordV1.from_dict(d)
    assert restored.record_id == record.record_id
    assert restored.metrics.likes == 123
    assert restored.metrics.shares is None
    assert restored.source_ref.source_table == "xhs_note"


def test_social_raw_record_empty_text_allowed():
    """Empty text is allowed in SocialRawRecordV1 (contributes to attention, not sentiment)."""
    record = SocialRawRecordV1(
        record_id="dy:post:aweme1",
        snapshot_id="sha256:snap1",
        record_type="post",
        platform="dy",
        native_id="aweme1",
        root_post_record_id="dy:post:aweme1",
        published_at="2026-08-26T03:12:11Z",
        first_seen_at="2026-08-26T04:00:02Z",
        snapshot_at="2026-08-26T06:10:00Z",
        ingest_at="2026-08-26T06:15:00Z",
        title=None,
        text="",  # Empty text
        metrics=SocialMetrics(likes=10, comments=0, shares=None, collects=None, views=None),
        content_hash=compute_content_hash(None, ""),
        metrics_hash=compute_metrics_hash({"likes": 10, "comments": 0}),
        ingest_run_id="run-1",
        source_ref=SourceRef("mediacrawler", "commit1", "douyin_aweme", "1"),
    )
    assert record.text == ""
    assert record.source_updated_at is None


def test_social_raw_record_validation():
    """Validation checks on platform, record_type, and required time fields."""
    metrics = SocialMetrics(likes=0, comments=0, shares=None, collects=None, views=None)
    ref = SourceRef("mediacrawler", "c1", "t1", "1")

    # Invalid platform
    with pytest.raises(ValueError, match="Invalid platform"):
        SocialRawRecordV1(
            record_id="wb:post:1",
            snapshot_id="s1",
            record_type="post",
            platform="weibo",  # invalid
            native_id="1",
            root_post_record_id="wb:post:1",
            published_at="2026-08-26T00:00:00Z",
            first_seen_at="2026-08-26T00:00:00Z",
            snapshot_at="2026-08-26T00:00:00Z",
            ingest_at="2026-08-26T00:00:00Z",
            metrics=metrics,
            content_hash="h1",
            metrics_hash="h2",
            ingest_run_id="r1",
            source_ref=ref,
        ).validate()

    # Invalid record_type
    with pytest.raises(ValueError, match="Invalid record_type"):
        SocialRawRecordV1(
            record_id="xhs:story:1",
            snapshot_id="s1",
            record_type="story",  # invalid
            platform="xhs",
            native_id="1",
            root_post_record_id="xhs:story:1",
            published_at="2026-08-26T00:00:00Z",
            first_seen_at="2026-08-26T00:00:00Z",
            snapshot_at="2026-08-26T00:00:00Z",
            ingest_at="2026-08-26T00:00:00Z",
            metrics=metrics,
            content_hash="h1",
            metrics_hash="h2",
            ingest_run_id="r1",
            source_ref=ref,
        ).validate()


def test_hash_computation_determinism():
    """Hash computation must be deterministic and distinguish content vs metrics."""
    h1 = compute_content_hash("Title", "Body")
    h2 = compute_content_hash("Title", "Body")
    assert h1 == h2

    h3 = compute_content_hash("Title2", "Body")
    assert h1 != h3

    m1 = compute_metrics_hash({"likes": 10, "comments": 5, "shares": None, "collects": None, "views": None})
    m2 = compute_metrics_hash({"likes": 10, "comments": 5, "shares": None, "collects": None, "views": None})
    assert m1 == m2

    m3 = compute_metrics_hash({"likes": 11, "comments": 5, "shares": None, "collects": None, "views": None})
    assert m1 != m3


# ============================================================================
# 4. SentimentBundleV1 & Status Semantics Tests (§5)
# ============================================================================

def test_sentiment_bundle_v1_available():
    """SentimentBundleV1 with available status and direction_allowed=True."""
    bundle = SentimentBundleV1(
        schema_version="social.sentiment_bundle.v1",
        status="available",
        requested_as_of="2026-08-26",
        cutoff_at="2026-08-26T15:59:59Z",
        content_as_of="2026-08-26T03:12:11Z",
        metric_as_of="2026-08-26T06:10:00Z",
        direction_allowed=True,
        reason_codes=[],
        symbol="688256.SH",
        bundle_id="bundle-12345",
        social_attention=SocialAttention(
            post_count=15,
            comment_count=45,
            author_count=20,
            total_interactions=500,
        ),
        social_sentiment=SocialSentiment(
            score=0.65,
            label="bullish",
            bullish_count=25,
            bearish_count=5,
            neutral_count=10,
            insufficient_count=0,
            is_calibrated_probability=False,
        ),
    )

    d = bundle.to_dict()
    assert d["schema_version"] == "social.sentiment_bundle.v1"
    assert d["status"] == "available"
    assert d["direction_allowed"] is True
    assert d["social_sentiment"]["score"] == 0.65
    assert d["social_sentiment"]["is_calibrated_probability"] is False

    restored = SentimentBundleV1.from_dict(d)
    assert restored.status == "available"
    assert restored.direction_allowed is True
    assert restored.social_sentiment.score == 0.65


def test_sentiment_bundle_v1_non_available_rules():
    """§5.1: empty/refused/failed/timeout/not_applicable or insufficient coverage

    must have score=None, label='insufficient', direction_allowed=False.
    """
    for non_avail_status in ["empty", "refused", "failed", "timeout", "not_applicable", "partial"]:
        bundle = create_empty_sentiment_bundle(
            status=non_avail_status,
            requested_as_of="2026-08-26",
            cutoff_at="2026-08-26T15:59:59Z",
            reason_codes=[f"social_{non_avail_status}"],
        )
        assert bundle.status == non_avail_status
        assert bundle.direction_allowed is False
        assert bundle.social_sentiment.score is None
        assert bundle.social_sentiment.label == "insufficient"
        assert bundle.content_as_of is None
        assert bundle.metric_as_of is None


def test_social_data_context_structure():
    """Test SocialDataContext creation and default helper (§8)."""
    ctx = create_default_social_data_context(
        status="not_applicable",
        mode="disabled",
        requested_as_of="2026-08-26",
        reason_codes=[REASON_SOCIAL_NOT_APPLICABLE],
    )
    assert ctx["status"] == "not_applicable"
    assert ctx["mode"] == "disabled"
    assert ctx["direction_allowed"] is False
    assert ctx["bundle"] is None
    assert isinstance(ctx["data_failure_ledger"], list)
    assert isinstance(ctx["source_provenance"], dict)


# ============================================================================
# 5. Archive SQLite Schema Tests (§4.2)
# ============================================================================

def test_archive_schema_ddl_and_init():
    """Test init_archive_db creating all tables, indexes, and metadata."""
    conn = sqlite3.connect(":memory:")
    init_archive_db(conn)

    # Verify tables exist
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in cursor.fetchall()}
    assert existing_tables.issuperset({
        "social_archive_meta",
        "social_ingest_runs",
        "social_record_snapshots",
        "social_entity_mentions",
    })

    # Verify indexes exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
    existing_indexes = {row[0] for row in cursor.fetchall()}
    assert existing_indexes.issuperset({
        "idx_social_snapshot_cutoff",
        "idx_social_record_history",
        "idx_social_entity_symbol",
        "idx_social_run_coverage",
    })

    # Verify meta initialization (§4.2 / §3.4: xhs_last_update_time_trusted=false)
    cursor.execute("SELECT key, value FROM social_archive_meta")
    meta_dict = dict(cursor.fetchall())
    assert meta_dict["schema_version"] == "1"
    assert meta_dict["xhs_last_update_time_trusted"] == "false"

    # Schema verification helper
    assert verify_archive_schema(conn) is True


def test_archive_schema_append_only_constraints():
    """Test append-only constraints on social_record_snapshots:

    - UNIQUE(record_id, content_hash, metrics_hash) prevents duplicate identical snapshot
    - Inserting changed metrics creates a second snapshot with same record_id without updating first row.
    """
    conn = sqlite3.connect(":memory:")
    init_archive_db(conn)
    cursor = conn.cursor()

    # 1. Insert ingest run
    cursor.execute(
        """
        INSERT INTO social_ingest_runs (
            run_id, provider, platform, query_text, started_at, completed_at,
            status, crawler_commit, source_schema_fingerprint, rows_read, rows_inserted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run-1", "mediacrawler", "xhs", "寒武纪", "2026-08-26T06:00:00Z",
            "2026-08-26T06:05:00Z", "completed", "d6f7c5b", "fp1", 1, 1,
        ),
    )

    # 2. Insert first snapshot
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform,
            native_id, parent_record_id, root_post_record_id,
            published_at, source_updated_at, first_seen_at, snapshot_at, ingest_at,
            title, text, canonical_url, author_id_hash, source_keyword,
            metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "snap-1", "xhs:post:note1", "social.raw_record.v1", "post", "xhs",
            "note1", None, "xhs:post:note1",
            "2026-08-26T03:00:00Z", None, "2026-08-26T03:05:00Z", "2026-08-26T03:05:00Z", "2026-08-26T06:05:00Z",
            "Note 1", "Body 1", "https://xhs/note1", "auth1", "寒武纪",
            json.dumps({"likes": 10, "comments": 2}), "c_hash_1", "m_hash_1", "run-1",
            "xhs_note", "1",
        ),
    )
    conn.commit()

    # Duplicate insertion of identical record_id, content_hash, metrics_hash must fail UNIQUE constraint
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            """
            INSERT INTO social_record_snapshots (
                snapshot_id, record_id, schema_version, record_type, platform,
                native_id, parent_record_id, root_post_record_id,
                published_at, source_updated_at, first_seen_at, snapshot_at, ingest_at,
                title, text, canonical_url, author_id_hash, source_keyword,
                metrics_json, content_hash, metrics_hash, ingest_run_id,
                source_table, source_row_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snap-duplicate", "xhs:post:note1", "social.raw_record.v1", "post", "xhs",
                "note1", None, "xhs:post:note1",
                "2026-08-26T03:00:00Z", None, "2026-08-26T03:05:00Z", "2026-08-26T03:05:00Z", "2026-08-26T06:05:00Z",
                "Note 1", "Body 1", "https://xhs/note1", "auth1", "寒武纪",
                json.dumps({"likes": 10, "comments": 2}), "c_hash_1", "m_hash_1", "run-1",
                "xhs_note", "1",
            ),
        )
    conn.rollback()

    # 3. When likes change (e.g. likes: 10 -> 25), a new snapshot row is added
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform,
            native_id, parent_record_id, root_post_record_id,
            published_at, source_updated_at, first_seen_at, snapshot_at, ingest_at,
            title, text, canonical_url, author_id_hash, source_keyword,
            metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "snap-2", "xhs:post:note1", "social.raw_record.v1", "post", "xhs",
            "note1", None, "xhs:post:note1",
            "2026-08-26T03:00:00Z", None, "2026-08-26T03:05:00Z", "2026-08-26T08:00:00Z", "2026-08-26T08:05:00Z",
            "Note 1", "Body 1", "https://xhs/note1", "auth1", "寒武纪",
            json.dumps({"likes": 25, "comments": 2}), "c_hash_1", "m_hash_2", "run-1",
            "xhs_note", "1",
        ),
    )
    conn.commit()

    # Assert 2 snapshot rows exist for the same record_id, and snap-1 has unchanged metrics
    cursor.execute("SELECT snapshot_id, metrics_json, snapshot_at FROM social_record_snapshots WHERE record_id = 'xhs:post:note1' ORDER BY snapshot_at ASC")
    rows = cursor.fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "snap-1"
    assert json.loads(rows[0][1])["likes"] == 10
    assert rows[1][0] == "snap-2"
    assert json.loads(rows[1][1])["likes"] == 25


def test_entity_mention_contract_and_table():
    """Test EntityMention dataclass and social_entity_mentions table constraints."""
    mention = EntityMention(
        symbol="688256.SH",
        matched_text="寒武纪",
        match_method="exact_name",
        confidence=1.0,
        resolver_version="v1",
    )
    d = mention.to_dict()
    assert d["symbol"] == "688256.SH"
    assert d["confidence"] == 1.0

    restored = EntityMention.from_dict(d)
    assert restored.symbol == mention.symbol
    assert restored.matched_text == mention.matched_text

    # Test DB insertion with FK
    conn = sqlite3.connect(":memory:")
    init_archive_db(conn)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO social_ingest_runs (run_id, provider, platform, query_text, started_at, status, crawler_commit, source_schema_fingerprint) VALUES ('r1', 'p1', 'xhs', 'q1', '2026-08-26', 'ok', 'c1', 'fp1')"
    )
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            metrics_json, content_hash, metrics_hash, ingest_run_id, source_table, source_row_id
        ) VALUES ('s1', 'xhs:post:1', 'v1', 'post', 'xhs', '1', 'xhs:post:1', '2026-08-26', '2026-08-26', '2026-08-26', '2026-08-26', '{}', 'ch', 'mh', 'r1', 't1', '1')
        """
    )
    cursor.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) VALUES (?, ?, ?, ?, ?, ?)",
        ("s1", mention.symbol, mention.matched_text, mention.match_method, mention.confidence, mention.resolver_version),
    )
    conn.commit()

    cursor.execute("SELECT symbol, confidence FROM social_entity_mentions WHERE snapshot_id = 's1'")
    row = cursor.fetchone()
    assert row == ("688256.SH", 1.0)

