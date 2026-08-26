"""Unit tests for MediaCrawler importer (Task 3 / B3).

Specifications:
- docs/social_data/implementation_plan.md Task 3 + §3.2, §3.3, §4.1, §4.2, D-008
- work/2026-08-27-unified-final-plan.md Phase 8 / B3
"""

import json
import sqlite3
import pytest
from datetime import datetime, timezone

from tradingagents.dataflows.social.archive_schema import init_archive_db
from tradingagents.dataflows.social.contracts import (
    SocialRawRecordV1,
    SocialMetrics,
    compute_content_hash,
    compute_metrics_hash,
)
from tradingagents.dataflows.social.mediacrawler_importer import (
    MediaCrawlerImporter,
    parse_crawler_timestamp,
    parse_metric_number,
    clean_canonical_url,
    compute_author_id_hash,
    compute_schema_fingerprint,
)
from tests.social_fixtures import (
    init_mediacrawler_db,
    populate_sample_mediacrawler_data,
    MEDIACRAWLER_XHS_NOTE_SCHEMA,
    MEDIACRAWLER_XHS_NOTE_COMMENT_SCHEMA,
    MEDIACRAWLER_DOUYIN_AWEME_SCHEMA,
    MEDIACRAWLER_DOUYIN_AWEME_COMMENT_SCHEMA,
)


# ============================================================================
# 1. Helper Function Unit Tests
# ============================================================================

def test_parse_crawler_timestamp_seconds_and_millis():
    """§3.3: abs(val) >= 10^12 is ms, < 10^12 is s. Return ISO 8601 UTC string."""
    # Milliseconds: 1787713931000 ms -> 2026-08-26T03:12:11Z
    dt_str_ms = parse_crawler_timestamp(1787713931000)
    assert dt_str_ms == "2026-08-26T03:12:11Z"

    # Seconds: 1787713931 s -> 2026-08-26T03:12:11Z
    dt_str_s = parse_crawler_timestamp(1787713931)
    assert dt_str_s == "2026-08-26T03:12:11Z"

    # String integer input
    assert parse_crawler_timestamp("1787713931000") == "2026-08-26T03:12:11Z"
    assert parse_crawler_timestamp("1787713931") == "2026-08-26T03:12:11Z"


def test_parse_crawler_timestamp_invalid_and_zero():
    """§3.3: 0, negative, invalid, out of range (>2100) are treated as missing (None)."""
    assert parse_crawler_timestamp(0) is None
    assert parse_crawler_timestamp("0") is None
    assert parse_crawler_timestamp(-1000) is None
    assert parse_crawler_timestamp(None) is None
    assert parse_crawler_timestamp("") is None
    assert parse_crawler_timestamp("invalid_time") is None
    # Year > 2100 (e.g. 5000000000 s)
    assert parse_crawler_timestamp(5000000000) is None


def test_parse_metric_number():
    """§3.3 / §4.1: Unknown numbers must be None, not 0. Literal '0' is 0."""
    assert parse_metric_number("123") == 123
    assert parse_metric_number(123) == 123
    assert parse_metric_number("0") == 0
    assert parse_metric_number(0) == 0
    assert parse_metric_number("") is None
    assert parse_metric_number(None) is None
    assert parse_metric_number("N/A") is None
    assert parse_metric_number(-5) is None  # negative interaction count invalid


def test_clean_canonical_url():
    """§4.1: Strip tracking tokens and query parameters (e.g. xsec_token)."""
    raw_url = "https://www.xiaohongshu.com/explore/note_65abc01?xsec_token=AB12345&xsec_source=pc_share"
    clean = clean_canonical_url(raw_url)
    assert clean == "https://www.xiaohongshu.com/explore/note_65abc01"

    dy_url = "https://www.douyin.com/video/123456?utm_source=copy&utm_medium=android"
    assert clean_canonical_url(dy_url) == "https://www.douyin.com/video/123456"

    assert clean_canonical_url(None) is None
    assert clean_canonical_url("") is None


def test_compute_author_id_hash():
    """§4.1: Hash user_id into sha256:..., return None if missing."""
    h = compute_author_id_hash("xhs_user_001")
    assert h is not None
    assert h.startswith("sha256:")

    assert compute_author_id_hash(None) is None
    assert compute_author_id_hash("") is None


# ============================================================================
# 2. Importer Full Pipeline Tests
# ============================================================================

def test_importer_imports_all_four_tables():
    """Requirement: Import xhs_note, xhs_note_comment, douyin_aweme, douyin_aweme_comment."""
    source_conn = sqlite3.connect(":memory:")
    init_mediacrawler_db(source_conn)
    populate_sample_mediacrawler_data(source_conn)

    archive_conn = sqlite3.connect(":memory:")
    init_archive_db(archive_conn)

    importer = MediaCrawlerImporter(
        archive_db=archive_conn,
        crawler_commit="d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
    )

    result = importer.import_from_db(source_db=source_conn, query_text="寒武纪")

    # In sample data:
    # xhs_note: 2 valid, 1 invalid (missing time=0) -> 2 inserted, 1 rejected
    # xhs_note_comment: 1 valid -> 1 inserted
    # douyin_aweme: 1 valid -> 1 inserted
    # douyin_aweme_comment: 1 valid -> 1 inserted
    # Total read: 6, total inserted: 5, total rejected: 1
    assert result["rows_read"] == 6
    assert result["rows_inserted"] == 5
    assert result["rows_rejected"] == 1
    assert result["status"] == "completed"

    # Verify social_ingest_runs row
    cursor = archive_conn.cursor()
    cursor.execute("SELECT run_id, provider, status, rows_read, rows_inserted, rows_rejected, crawler_commit FROM social_ingest_runs")
    run_row = cursor.fetchone()
    assert run_row is not None
    assert run_row[1] == "mediacrawler"
    assert run_row[2] == "completed"
    assert run_row[3] == 6
    assert run_row[4] == 5
    assert run_row[5] == 1
    assert run_row[6] == "d6f7c5bb906b6dac40ddf343ef9e26438a3de092"

    # Verify records in social_record_snapshots
    cursor.execute("SELECT record_id, record_type, platform, native_id, root_post_record_id, parent_record_id, published_at, source_updated_at, first_seen_at, snapshot_at, title, text, canonical_url, author_id_hash, metrics_json FROM social_record_snapshots ORDER BY record_id ASC")
    rows = cursor.fetchall()
    assert len(rows) == 5

    # Check XHS Note 1
    xhs_post = next(r for r in rows if r[0] == "xhs:post:note_65abc01")
    assert xhs_post[1] == "post"
    assert xhs_post[2] == "xhs"
    assert xhs_post[3] == "note_65abc01"
    assert xhs_post[4] == "xhs:post:note_65abc01"
    assert xhs_post[5] is None
    assert xhs_post[6] == "2026-08-26T03:12:11Z"
    assert xhs_post[7] == "2026-08-26T03:40:00Z"
    assert xhs_post[8] == "2026-08-26T04:00:02Z"
    assert xhs_post[9] == "2026-08-26T06:10:00Z"
    assert xhs_post[10] == "寒武纪深度解析与展望"
    assert "主力资金" in xhs_post[11]
    assert xhs_post[12] == "https://www.xiaohongshu.com/explore/note_65abc01"  # query stripped
    assert xhs_post[13] is not None
    m1 = json.loads(xhs_post[14])
    assert m1["likes"] == 123
    assert m1["collects"] == 20
    assert m1["comments"] == 45
    assert m1["shares"] == 5

    # Check XHS Comment
    xhs_comm = next(r for r in rows if r[0] == "xhs:comment:comment_xhs_001")
    assert xhs_comm[1] == "comment"
    assert xhs_comm[2] == "xhs"
    assert xhs_comm[4] == "xhs:post:note_65abc01"
    assert xhs_comm[7] is None  # source_updated_at is null for comment

    # Check Douyin Aweme
    dy_post = next(r for r in rows if r[0] == "dy:post:aweme_789001")
    assert dy_post[1] == "post"
    assert dy_post[2] == "dy"
    assert dy_post[4] == "dy:post:aweme_789001"
    assert dy_post[7] is None  # source_updated_at is null for dy

    # Check Douyin Comment
    dy_comm = next(r for r in rows if r[0] == "dy:comment:dy_comment_001")
    assert dy_comm[1] == "comment"
    assert dy_comm[2] == "dy"
    assert dy_comm[4] == "dy:post:aweme_789001"


def test_empty_desc_allowed_in_archive():
    """§4.1 / Task 3: Empty text is allowed to be archived."""
    source_conn = sqlite3.connect(":memory:")
    init_mediacrawler_db(source_conn)
    populate_sample_mediacrawler_data(source_conn)

    archive_conn = sqlite3.connect(":memory:")
    init_archive_db(archive_conn)

    importer = MediaCrawlerImporter(archive_conn)
    importer.import_from_db(source_conn)

    cursor = archive_conn.cursor()
    cursor.execute("SELECT text FROM social_record_snapshots WHERE record_id = 'xhs:post:note_65abc02'")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == ""


def test_no_sensitive_fields_in_archive():
    """Privacy: nickname, avatar, xsec_token, video_url, image_list must NOT be saved."""
    source_conn = sqlite3.connect(":memory:")
    init_mediacrawler_db(source_conn)
    populate_sample_mediacrawler_data(source_conn)

    archive_conn = sqlite3.connect(":memory:")
    init_archive_db(archive_conn)

    importer = MediaCrawlerImporter(archive_conn)
    importer.import_from_db(source_conn)

    # Check column names of social_record_snapshots table
    cursor = archive_conn.cursor()
    cursor.execute("PRAGMA table_info(social_record_snapshots)")
    cols = {r[1] for r in cursor.fetchall()}
    assert "nickname" not in cols
    assert "avatar" not in cols
    assert "xsec_token" not in cols
    assert "video_url" not in cols
    assert "image_list" not in cols

    # Verify no raw sensitive tokens in DB values
    cursor.execute("SELECT * FROM social_record_snapshots WHERE record_id = 'xhs:post:note_65abc01'")
    full_row_str = str(cursor.fetchone())
    assert "小红书老股民" not in full_row_str  # nickname
    assert "AB12345" not in full_row_str  # xsec_token
    assert "https://avatar/1.png" not in full_row_str  # avatar


# ============================================================================
# 3. Append-Only and Idempotency Tests
# ============================================================================

def test_importer_idempotent_no_duplicate_or_update():
    """Task 3: Re-importing identical source records results in rows_inserted=0.

    Existing snapshot rows remain unchanged; no UPDATE is issued.
    """
    source_conn = sqlite3.connect(":memory:")
    init_mediacrawler_db(source_conn)
    populate_sample_mediacrawler_data(source_conn)

    archive_conn = sqlite3.connect(":memory:")
    init_archive_db(archive_conn)

    importer = MediaCrawlerImporter(archive_conn)

    # Run 1
    res1 = importer.import_from_db(source_conn)
    assert res1["rows_inserted"] == 5

    cursor = archive_conn.cursor()
    cursor.execute("SELECT snapshot_id, metrics_json, ingest_at FROM social_record_snapshots WHERE record_id = 'xhs:post:note_65abc01'")
    snap_before = cursor.fetchone()

    # Run 2 (identical source data)
    res2 = importer.import_from_db(source_conn)
    assert res2["rows_inserted"] == 0
    assert res2["rows_read"] == 6
    assert res2["rows_rejected"] == 1

    cursor.execute("SELECT snapshot_id, metrics_json, ingest_at FROM social_record_snapshots WHERE record_id = 'xhs:post:note_65abc01'")
    snap_after = cursor.fetchone()

    # Assert exactly same snapshot row
    assert snap_before == snap_after

    cursor.execute("SELECT COUNT(*) FROM social_record_snapshots")
    assert cursor.fetchone()[0] == 5


def test_importer_append_only_on_metrics_change():
    """Task 3: When interaction metrics change (e.g. likes 123 -> 500),

    importer inserts a new snapshot row with new snapshot_at without modifying old snapshot row.
    """
    source_conn = sqlite3.connect(":memory:")
    init_mediacrawler_db(source_conn)
    populate_sample_mediacrawler_data(source_conn)

    archive_conn = sqlite3.connect(":memory:")
    init_archive_db(archive_conn)

    importer = MediaCrawlerImporter(archive_conn)
    importer.import_from_db(source_conn)

    # Simulate MediaCrawler update-in-place on xhs_note table:
    # note_65abc01 gains likes (123 -> 500) and new last_modify_ts (1787730000000)
    source_cursor = source_conn.cursor()
    source_cursor.execute(
        """
        UPDATE xhs_note
        SET liked_count = '500',
            last_modify_ts = 1787730000000
        WHERE note_id = 'note_65abc01'
        """
    )
    source_conn.commit()

    # Run 2: Import updated source DB
    res2 = importer.import_from_db(source_conn)
    assert res2["rows_inserted"] == 1  # only the updated note is inserted as a new snapshot

    # Verify social_record_snapshots has 2 snapshots for note_65abc01
    cursor = archive_conn.cursor()
    cursor.execute(
        """
        SELECT snapshot_id, snapshot_at, metrics_json
        FROM social_record_snapshots
        WHERE record_id = 'xhs:post:note_65abc01'
        ORDER BY snapshot_at ASC
        """
    )
    snaps = cursor.fetchall()
    assert len(snaps) == 2

    # Old snapshot row has likes=123
    assert json.loads(snaps[0][2])["likes"] == 123
    # New snapshot row has likes=500
    assert json.loads(snaps[1][2])["likes"] == 500
    assert snaps[1][1] == "2026-08-26T07:40:00Z"


# ============================================================================
# 4. Timestamp Validation & Fail-Closed Tests
# ============================================================================

def test_missing_required_timestamps_rejected_no_backfill():
    """§3.3 / Task 3: Missing published_at / add_ts / last_modify_ts must be rejected.

    Must NOT backfill with now() or ingest_at.
    """
    source_conn = sqlite3.connect(":memory:")
    init_mediacrawler_db(source_conn)
    cursor = source_conn.cursor()

    # Note with missing add_ts (0)
    cursor.execute(
        """
        INSERT INTO xhs_note (
            note_id, title, desc, time, last_update_time, add_ts, last_modify_ts
        ) VALUES (
            'note_missing_add_ts', 'T1', 'D1', 1787713931000, 0, 0, 1787724600000
        )
        """
    )
    # Note with missing last_modify_ts (None)
    cursor.execute(
        """
        INSERT INTO xhs_note (
            note_id, title, desc, time, last_update_time, add_ts, last_modify_ts
        ) VALUES (
            'note_missing_mod_ts', 'T2', 'D2', 1787713931000, 0, 1787716802000, NULL
        )
        """
    )
    # Note with missing published_at (time=0)
    cursor.execute(
        """
        INSERT INTO xhs_note (
            note_id, title, desc, time, last_update_time, add_ts, last_modify_ts
        ) VALUES (
            'note_missing_time', 'T3', 'D3', 0, 0, 1787716802000, 1787724600000
        )
        """
    )
    source_conn.commit()

    archive_conn = sqlite3.connect(":memory:")
    init_archive_db(archive_conn)

    importer = MediaCrawlerImporter(archive_conn)
    result = importer.import_from_db(source_conn)

    assert result["rows_read"] == 3
    assert result["rows_inserted"] == 0
    assert result["rows_rejected"] == 3


def test_schema_mismatch_fails_closed():
    """§3.1 / Task 3: Unrecognized schema / missing required columns fails closed."""
    bad_source_conn = sqlite3.connect(":memory:")
    bad_source_conn.execute("CREATE TABLE xhs_note (id INTEGER PRIMARY KEY, random_col TEXT)")
    bad_source_conn.execute("INSERT INTO xhs_note (random_col) VALUES ('foo')")
    bad_source_conn.commit()

    archive_conn = sqlite3.connect(":memory:")
    init_archive_db(archive_conn)

    importer = MediaCrawlerImporter(archive_conn)
    result = importer.import_from_db(bad_source_conn)

    assert result["status"] == "failed"
    assert result["error_code"] == "social_schema_mismatch"


def test_secondary_comment_parent_record_id():
    """Test secondary comments having correct parent_record_id vs root_post_record_id."""
    source_conn = sqlite3.connect(":memory:")
    init_mediacrawler_db(source_conn)
    cursor = source_conn.cursor()

    # Note
    cursor.execute(
        """
        INSERT INTO xhs_note (note_id, title, desc, time, last_update_time, add_ts, last_modify_ts)
        VALUES ('note_100', 'Title', 'Desc', 1787713931000, 0, 1787716802000, 1787724600000)
        """
    )
    # Root comment
    cursor.execute(
        """
        INSERT INTO xhs_note_comment (comment_id, note_id, content, create_time, add_ts, last_modify_ts, parent_comment_id)
        VALUES ('comm_root', 'note_100', 'Root comment', 1787714000, 1787717000, 1787724700, NULL)
        """
    )
    # Sub comment replying to comm_root
    cursor.execute(
        """
        INSERT INTO xhs_note_comment (comment_id, note_id, content, create_time, add_ts, last_modify_ts, parent_comment_id)
        VALUES ('comm_sub', 'note_100', 'Sub reply', 1787714100, 1787717050, 1787724750, 'comm_root')
        """
    )
    source_conn.commit()

    archive_conn = sqlite3.connect(":memory:")
    init_archive_db(archive_conn)

    importer = MediaCrawlerImporter(archive_conn)
    importer.import_from_db(source_conn)

    cursor = archive_conn.cursor()
    cursor.execute("SELECT record_id, root_post_record_id, parent_record_id FROM social_record_snapshots WHERE record_id IN ('xhs:comment:comm_root', 'xhs:comment:comm_sub') ORDER BY record_id ASC")
    rows = cursor.fetchall()
    assert len(rows) == 2

    root_comm = next(r for r in rows if r[0] == "xhs:comment:comm_root")
    assert root_comm[1] == "xhs:post:note_100"
    assert root_comm[2] is None

    sub_comm = next(r for r in rows if r[0] == "xhs:comment:comm_sub")
    assert sub_comm[1] == "xhs:post:note_100"
    assert sub_comm[2] == "xhs:comment:comm_root"


def test_importer_platform_filtering(tmp_path):
    """Test importing with specific platforms filter ('xhs' or 'dy') using file-based DBs."""
    source_db_path = str(tmp_path / "source_mediacrawler.db")
    archive_db_path = str(tmp_path / "target_archive.db")

    s_conn = init_mediacrawler_db(source_db_path)
    populate_sample_mediacrawler_data(s_conn)
    s_conn.close()

    importer = MediaCrawlerImporter(archive_db_path)

    # Import XHS only
    res = importer.import_from_db(source_db=source_db_path, platforms=["xhs"])
    assert res["status"] == "completed"
    assert res["rows_inserted"] == 3  # 2 xhs notes + 1 xhs comment

    # Verify only xhs records are in archive
    arch_conn = sqlite3.connect(archive_db_path)
    cursor = arch_conn.cursor()
    cursor.execute("SELECT DISTINCT platform FROM social_record_snapshots")
    platforms = [r[0] for r in cursor.fetchall()]
    assert platforms == ["xhs"]
    arch_conn.close()

