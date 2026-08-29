"""Unit tests for Social as-of eligibility guards (Task 5 / B5 / D-008).

Specifications:
- docs/social_data/implementation_plan.md Task 5, §5.1, §5.2, D-008
- DECISIONS.md D-008
- Timezone-aware date/datetime comparisons (no string comparison).
- Historical day cutoff: 23:59:59.999999 Asia/Shanghai (serialized to equivalent UTC).
- Current day cutoff: actual now_cn() (cannot be preset to day end).
- Future / invalid as_of: refused with exact reason code (no silent fallback).
- Content eligibility: window_start <= published_at <= cutoff AND first_seen_at <= cutoff.
- Metrics eligibility: candidate snapshot is MAX(snapshot_at) <= cutoff. Likes after cutoff do NOT leak.
- Ingestion time independence: ingest_at > cutoff NEVER disqualifies historical records.
- Late-discovered content (后补抓取): first_seen_at > cutoff must be excluded.
"""

import json
import sqlite3
import pytest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tradingagents.dataflows.social.archive_schema import (
    ARCHIVE_SCHEMA_VERSION,
    init_archive_db,
)
from tradingagents.dataflows.social.contracts import (
    REASON_OBSERVED_AFTER_CUTOFF_EXCLUDED,
    REASON_SOCIAL_FUTURE_AS_OF,
    REASON_SOCIAL_INVALID_AS_OF,
    REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
    SocialMetrics,
    SocialRawRecordV1,
    SocialStatus,
    SourceRef,
    compute_content_hash,
    compute_metrics_hash,
)
from tradingagents.dataflows.social.provider import (
    SocialArchiveProvider,
    SocialFetchResult,
    check_content_eligibility,
    compute_as_of_cutoff,
    parse_iso_datetime,
    select_candidate_snapshot,
)


CN_TZ = ZoneInfo("Asia/Shanghai")


# ============================================================================
# 1. As-Of Parsing and Cutoff Computation Tests (D-008)
# ============================================================================

def test_parse_iso_datetime_utc_aware():
    """All parsed datetimes must be timezone-aware with UTC tzinfo."""
    dt_z = parse_iso_datetime("2026-08-26T03:12:11Z")
    assert dt_z is not None
    assert dt_z.tzinfo is not None
    assert dt_z.utcoffset() == timedelta(0)
    assert dt_z.year == 2026 and dt_z.month == 8 and dt_z.day == 26 and dt_z.hour == 3

    # Shanghai offset +08:00
    dt_cst = parse_iso_datetime("2026-08-26T11:12:11+08:00")
    assert dt_cst is not None
    assert dt_cst.utcoffset() == timedelta(0)
    assert dt_cst.hour == 3  # 11:12:11 CST == 03:12:11 UTC

    # Millis timestamp
    dt_ts = parse_iso_datetime(1787713931000)
    assert dt_ts == dt_z

    # Invalid / None
    assert parse_iso_datetime(None) is None
    assert parse_iso_datetime("") is None
    assert parse_iso_datetime("invalid") is None


def test_invalid_as_of_formats_rejected():
    """Invalid or empty as_of strings must raise ValueError with REASON_SOCIAL_INVALID_AS_OF."""
    now_frozen = datetime(2026, 8, 27, 10, 0, 0, tzinfo=CN_TZ)

    with pytest.raises(ValueError, match=REASON_SOCIAL_INVALID_AS_OF):
        compute_as_of_cutoff("", now=now_frozen)

    with pytest.raises(ValueError, match=REASON_SOCIAL_INVALID_AS_OF):
        compute_as_of_cutoff(None, now=now_frozen)

    with pytest.raises(ValueError, match=REASON_SOCIAL_INVALID_AS_OF):
        compute_as_of_cutoff("not-a-date", now=now_frozen)

    with pytest.raises(ValueError, match=REASON_SOCIAL_INVALID_AS_OF):
        compute_as_of_cutoff("2026-13-45", now=now_frozen)


def test_future_as_of_rejected_without_silent_fallback():
    """Future as_of relative to Asia/Shanghai today must raise REASON_SOCIAL_FUTURE_AS_OF."""
    now_frozen = datetime(2026, 8, 27, 10, 0, 0, tzinfo=CN_TZ)

    # Tomorrow
    with pytest.raises(ValueError, match=REASON_SOCIAL_FUTURE_AS_OF):
        compute_as_of_cutoff("2026-08-28", now=now_frozen)

    # Far future
    with pytest.raises(ValueError, match=REASON_SOCIAL_FUTURE_AS_OF):
        compute_as_of_cutoff("2029-01-01", now=now_frozen)


def test_historical_cutoff_is_shanghai_day_end_utc():
    """Historical date cutoff must be 23:59:59.999999 in Asia/Shanghai (15:59:59.999999 UTC).

    Window start must be 00:00:00.000000 in Asia/Shanghai of (as_of - lookback_days).
    """
    now_frozen = datetime(2026, 8, 27, 10, 0, 0, tzinfo=CN_TZ)

    # Query for historical day 2026-08-26 (lookback 7 days)
    w_start_utc, cutoff_utc, cutoff_cn = compute_as_of_cutoff("2026-08-26", lookback_days=7, now=now_frozen)

    # Shanghai cutoff
    assert cutoff_cn.year == 2026 and cutoff_cn.month == 8 and cutoff_cn.day == 26
    assert cutoff_cn.hour == 23 and cutoff_cn.minute == 59 and cutoff_cn.second == 59
    assert cutoff_cn.microsecond == 999999
    assert cutoff_cn.tzinfo == CN_TZ

    # UTC equivalent: 2026-08-26 15:59:59.999999
    assert cutoff_utc.year == 2026 and cutoff_utc.month == 8 and cutoff_utc.day == 26
    assert cutoff_utc.hour == 15 and cutoff_utc.minute == 59 and cutoff_utc.second == 59
    assert cutoff_utc.microsecond == 999999

    # Window start: 7 days before (2026-08-19 00:00:00 CST -> 2026-08-18 16:00:00 UTC)
    assert w_start_utc.year == 2026 and w_start_utc.month == 8 and w_start_utc.day == 18
    assert w_start_utc.hour == 16 and w_start_utc.minute == 0 and w_start_utc.second == 0


def test_current_day_cutoff_uses_actual_now():
    """Current day cutoff must use actual now_cn(), NEVER preset to 23:59:59.999999."""
    now_frozen = datetime(2026, 8, 27, 14, 30, 15, 123456, tzinfo=CN_TZ)

    w_start_utc, cutoff_utc, cutoff_cn = compute_as_of_cutoff("2026-08-27", lookback_days=7, now=now_frozen)

    # Cutoff must equal the frozen now
    assert cutoff_cn == now_frozen
    assert cutoff_utc == now_frozen.astimezone(timezone.utc)


# ============================================================================
# 2. Content Eligibility Unit Tests (check_content_eligibility)
# ============================================================================

def test_content_eligibility_standard_valid_post():
    """Post published within window and first seen <= cutoff is eligible."""
    cutoff_utc = datetime(2026, 8, 26, 15, 59, 59, 999999, tzinfo=timezone.utc)
    w_start_utc = datetime(2026, 8, 19, 16, 0, 0, tzinfo=timezone.utc)

    snap = {
        "platform": "xhs",
        "published_at": "2026-08-26T03:12:11Z",
        "first_seen_at": "2026-08-26T04:00:00Z",
        "snapshot_at": "2026-08-26T06:00:00Z",
        "ingest_at": "2026-08-27T08:00:00Z",  # Ingest after cutoff must NOT affect eligibility
    }
    is_ok, reason = check_content_eligibility(snap, w_start_utc, cutoff_utc)
    assert is_ok is True
    assert reason is None


def test_content_eligibility_late_discovered_post_excluded():
    """Post published early but first crawled after cutoff (后补抓取) MUST be excluded."""
    cutoff_utc = datetime(2026, 8, 26, 15, 59, 59, 999999, tzinfo=timezone.utc)
    w_start_utc = datetime(2026, 8, 19, 16, 0, 0, tzinfo=timezone.utc)

    snap = {
        "platform": "xhs",
        "published_at": "2026-08-25T10:00:00Z",  # Published before cutoff
        "first_seen_at": "2026-08-27T01:00:00Z",  # First seen AFTER cutoff
        "snapshot_at": "2026-08-27T02:00:00Z",
        "ingest_at": "2026-08-27T03:00:00Z",
    }
    is_ok, reason = check_content_eligibility(snap, w_start_utc, cutoff_utc)
    assert is_ok is False
    assert reason == "first_seen_at_after_cutoff"


def test_content_eligibility_published_outside_window_excluded():
    """Post published before window_start or after cutoff must be excluded."""
    cutoff_utc = datetime(2026, 8, 26, 15, 59, 59, 999999, tzinfo=timezone.utc)
    w_start_utc = datetime(2026, 8, 19, 16, 0, 0, tzinfo=timezone.utc)

    # 1. Before window
    snap_old = {
        "platform": "xhs",
        "published_at": "2026-08-10T10:00:00Z",
        "first_seen_at": "2026-08-10T11:00:00Z",
        "snapshot_at": "2026-08-10T12:00:00Z",
    }
    is_ok, reason = check_content_eligibility(snap_old, w_start_utc, cutoff_utc)
    assert is_ok is False
    assert reason == "published_at_outside_window"

    # 2. After cutoff
    snap_future = {
        "platform": "xhs",
        "published_at": "2026-08-27T01:00:00Z",
        "first_seen_at": "2026-08-27T01:00:00Z",
        "snapshot_at": "2026-08-27T02:00:00Z",
    }
    is_ok, reason = check_content_eligibility(snap_future, w_start_utc, cutoff_utc)
    assert is_ok is False
    assert reason == "published_at_outside_window"


def test_content_eligibility_source_updated_at_untrusted_vs_trusted():
    """source_updated_at does NOT disqualify when untrusted (default); disqualifies only when trusted."""
    cutoff_utc = datetime(2026, 8, 26, 15, 59, 59, 999999, tzinfo=timezone.utc)
    w_start_utc = datetime(2026, 8, 19, 16, 0, 0, tzinfo=timezone.utc)

    snap = {
        "platform": "xhs",
        "published_at": "2026-08-26T03:00:00Z",
        "source_updated_at": "2026-08-27T01:00:00Z",  # Updated after cutoff
        "first_seen_at": "2026-08-26T04:00:00Z",
        "snapshot_at": "2026-08-26T05:00:00Z",
    }

    # Untrusted (default): MUST BE ELIGIBLE
    is_ok_untrusted, _ = check_content_eligibility(
        snap, w_start_utc, cutoff_utc, xhs_last_update_time_trusted=False
    )
    assert is_ok_untrusted is True

    # Trusted: MUST BE EXCLUDED
    is_ok_trusted, reason = check_content_eligibility(
        snap, w_start_utc, cutoff_utc, xhs_last_update_time_trusted=True
    )
    assert is_ok_trusted is False
    assert reason == "source_updated_at_after_cutoff"


# ============================================================================
# 3. Snapshot Selection & Metrics Eligibility Unit Tests
# ============================================================================

def test_select_candidate_snapshot_picks_max_le_cutoff():
    """Multiple snapshots for same record: select snapshot with MAX snapshot_at <= cutoff."""
    cutoff_utc = datetime(2026, 8, 26, 15, 59, 59, 999999, tzinfo=timezone.utc)

    snap1 = {"snapshot_id": "s1", "snapshot_at": "2026-08-25T10:00:00Z", "likes": 10}
    snap2 = {"snapshot_id": "s2", "snapshot_at": "2026-08-26T08:00:00Z", "likes": 50}
    snap3 = {"snapshot_id": "s3", "snapshot_at": "2026-08-27T08:00:00Z", "likes": 500}  # After cutoff

    candidate = select_candidate_snapshot([snap1, snap2, snap3], cutoff_utc)
    assert candidate is not None
    assert candidate["snapshot_id"] == "s2"
    assert candidate["likes"] == 50  # Likes=500 from s3 must NOT be selected


def test_select_candidate_snapshot_none_when_all_after_cutoff():
    """When all snapshots are after cutoff, candidate selection returns None."""
    cutoff_utc = datetime(2026, 8, 26, 15, 59, 59, 999999, tzinfo=timezone.utc)

    snap1 = {"snapshot_id": "s1", "snapshot_at": "2026-08-27T01:00:00Z"}
    snap2 = {"snapshot_id": "s2", "snapshot_at": "2026-08-27T10:00:00Z"}

    candidate = select_candidate_snapshot([snap1, snap2], cutoff_utc)
    assert candidate is None


# ============================================================================
# 4. End-to-End Provider As-Of Guard Integration Tests
# ============================================================================

@pytest.fixture
def custom_archive_db(tmp_path):
    """Fixture creating an archive DB for custom as-of scenario testing."""
    db_path = str(tmp_path / "as_of_test_archive.db")
    conn = init_archive_db(db_path)
    cursor = conn.cursor()

    # Ingest Run
    cursor.execute(
        """
        INSERT INTO social_ingest_runs (
            run_id, provider, platform, query_text, started_at, completed_at,
            status, crawler_commit, source_schema_fingerprint, rows_read, rows_inserted
        ) VALUES (
            'run_001', 'mediacrawler', 'xhs', '寒武纪', '2026-08-29T10:00:00Z', '2026-08-29T10:01:00Z',
            'success', 'd6f7c5bb906b6dac40ddf343ef9e26438a3de092', 'fp_001', 10, 10
        )
        """
    )

    # 1. Multi-snapshot post for 688256.SH
    # Snapshot A (on 2026-08-25, likes=10)
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_cambricon_v1', 'xhs:post:note_camb_1', 'social.raw_record.v1', 'post', 'xhs', 'note_camb_1',
            'xhs:post:note_camb_1', '2026-08-25T03:00:00Z', '2026-08-25T04:00:00Z', '2026-08-25T05:00:00Z', '2026-08-29T10:00:00Z',
            '寒武纪分析', '正文内容', '{"likes": 10, "comments": 2}', 'c_hash1', 'm_hash1', 'run_001',
            'xhs_note', '1'
        )
        """
    )
    cursor.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) VALUES (?, ?, ?, ?, ?, ?)",
        ('snap_cambricon_v1', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
    )

    # Snapshot B (on 2026-08-26, likes=50)
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_cambricon_v2', 'xhs:post:note_camb_1', 'social.raw_record.v1', 'post', 'xhs', 'note_camb_1',
            'xhs:post:note_camb_1', '2026-08-25T03:00:00Z', '2026-08-25T04:00:00Z', '2026-08-26T08:00:00Z', '2026-08-29T10:00:00Z',
            '寒武纪分析', '正文内容', '{"likes": 50, "comments": 10}', 'c_hash1', 'm_hash2', 'run_001',
            'xhs_note', '1'
        )
        """
    )
    cursor.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) VALUES (?, ?, ?, ?, ?, ?)",
        ('snap_cambricon_v2', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
    )

    # Snapshot C (on 2026-08-27, likes=500)
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_cambricon_v3', 'xhs:post:note_camb_1', 'social.raw_record.v1', 'post', 'xhs', 'note_camb_1',
            'xhs:post:note_camb_1', '2026-08-25T03:00:00Z', '2026-08-25T04:00:00Z', '2026-08-27T08:00:00Z', '2026-08-29T10:00:00Z',
            '寒武纪分析', '正文内容', '{"likes": 500, "comments": 100}', 'c_hash1', 'm_hash3', 'run_001',
            'xhs_note', '1'
        )
        """
    )
    cursor.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) VALUES (?, ?, ?, ?, ?, ?)",
        ('snap_cambricon_v3', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
    )

    # 2. Late-crawled post (first_seen on 2026-08-27) for 600519.SH
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_maotai_late', 'xhs:post:note_mao_1', 'social.raw_record.v1', 'post', 'xhs', 'note_mao_1',
            'xhs:post:note_mao_1', '2026-08-25T03:00:00Z', '2026-08-27T04:00:00Z', '2026-08-27T05:00:00Z', '2026-08-29T10:00:00Z',
            '茅台分析', '正文内容', '{"likes": 20}', 'c_hash_m', 'm_hash_m', 'run_001',
            'xhs_note', '2'
        )
        """
    )
    cursor.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) VALUES (?, ?, ?, ?, ?, ?)",
        ('snap_maotai_late', '600519.SH', '茅台', 'unique_alias', 0.95, 'v1')
    )

    conn.commit()
    conn.close()
    return db_path


def test_provider_as_of_selects_correct_historical_snapshot_metrics(custom_archive_db):
    """On as_of=2026-08-26, must select snap_cambricon_v2 (likes=50), excluding v3 (likes=500)."""
    provider = SocialArchiveProvider(db_path=custom_archive_db)
    now_frozen = datetime(2026, 8, 29, 12, 0, 0, tzinfo=CN_TZ)

    # 1. As-of 2026-08-25: should get v1 (likes=10)
    res_25 = provider.fetch_records(symbol="688256.SH", as_of="2026-08-25", now=now_frozen)
    assert res_25.status == SocialStatus.AVAILABLE.value
    assert len(res_25.records) == 1
    assert res_25.records[0].snapshot_id == "snap_cambricon_v1"
    assert res_25.records[0].metrics.likes == 10

    # 2. As-of 2026-08-26: should get v2 (likes=50)
    res_26 = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26", now=now_frozen)
    assert res_26.status == SocialStatus.AVAILABLE.value
    assert len(res_26.records) == 1
    assert res_26.records[0].snapshot_id == "snap_cambricon_v2"
    assert res_26.records[0].metrics.likes == 50

    # 3. As-of 2026-08-27: should get v3 (likes=500)
    res_27 = provider.fetch_records(symbol="688256.SH", as_of="2026-08-27", now=now_frozen)
    assert res_27.status == SocialStatus.AVAILABLE.value
    assert len(res_27.records) == 1
    assert res_27.records[0].snapshot_id == "snap_cambricon_v3"
    assert res_27.records[0].metrics.likes == 500


def test_provider_as_of_excludes_late_crawled_post(custom_archive_db):
    """On as_of=2026-08-26, late crawled post (first_seen on 2026-08-27) must be excluded."""
    provider = SocialArchiveProvider(db_path=custom_archive_db)
    now_frozen = datetime(2026, 8, 29, 12, 0, 0, tzinfo=CN_TZ)

    # 600519.SH was first seen on 2026-08-27. For as_of=2026-08-26, it must be refused
    res = provider.fetch_records(symbol="600519.SH", as_of="2026-08-26", now=now_frozen)
    assert res.status == SocialStatus.REFUSED.value
    assert REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT in res.reason_codes
    assert len(res.records) == 0


def test_provider_ingest_at_future_does_not_disqualify(custom_archive_db):
    """In archive DB, ingest_at is 2026-08-29 (future).

    For as_of=2026-08-25, the record MUST still be eligible and returned.
    """
    provider = SocialArchiveProvider(db_path=custom_archive_db)
    now_frozen = datetime(2026, 8, 29, 12, 0, 0, tzinfo=CN_TZ)

    res = provider.fetch_records(symbol="688256.SH", as_of="2026-08-25", now=now_frozen)
    assert res.status == SocialStatus.AVAILABLE.value
    assert len(res.records) == 1
    # ingest_at is in 2026-08-29
    assert "2026-08-29" in res.records[0].ingest_at
