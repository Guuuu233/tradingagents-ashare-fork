"""Unit tests for MediaCrawler run guards and controlled ingestion runner (Task 13 / §3.1 / D-008).

Specifications:
- docs/social_data/implementation_plan.md Task 13, §3.1, §3.2, §3.3, §4.1, D-008
- Enforces save_option=sqlite (rejects JSONL / others with non-zero exit).
- Enforces loopback host constraint (127.0.0.1 / localhost only).
- Enforces single-task concurrency lock (rejects concurrent second run).
- Pins MediaCrawler commit (default d6f7c5bb906b6dac40ddf343ef9e26438a3de092).
- Default comments=True, sub_comments=False.
- Post-run SQLite target table verification.
"""

import os
import sqlite3
import pytest

from scripts.run_social_ingestion import (
    DEFAULT_CRAWLER_HOST,
    DEFAULT_ENABLE_COMMENTS,
    DEFAULT_ENABLE_SUB_COMMENTS,
    DEFAULT_SAVE_OPTION,
    IngestionLock,
    is_loopback_host,
    main as runner_cli_main,
    parse_args,
    run_social_ingestion,
    validate_crawler_host,
    validate_save_option,
    validate_source_db_tables,
)
from tests.social_fixtures import (
    init_mediacrawler_db,
    populate_sample_mediacrawler_data,
)


# ============================================================================
# 1. Host and Save Option Guard Tests
# ============================================================================

def test_validate_save_option_strictly_sqlite():
    """save_option must be 'sqlite' (case-insensitive). Reject jsonl, csv, etc."""
    # Valid
    validate_save_option("sqlite")
    validate_save_option("SQLITE")
    validate_save_option(" sqlite ")

    # Invalid options must raise ValueError
    with pytest.raises(ValueError, match="MediaCrawler must be run with save_option='sqlite'"):
        validate_save_option("jsonl")

    with pytest.raises(ValueError, match="MediaCrawler must be run with save_option='sqlite'"):
        validate_save_option("csv")

    with pytest.raises(ValueError, match="MediaCrawler must be run with save_option='sqlite'"):
        validate_save_option("")


def test_validate_crawler_host_loopback_only():
    """Crawler host must be loopback (127.0.0.1 / localhost). Reject non-loopback hosts."""
    # Valid loopback
    validate_crawler_host("127.0.0.1")
    validate_crawler_host("localhost")

    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("localhost") is True

    # Invalid non-loopback hosts must raise ValueError
    with pytest.raises(ValueError, match="Security Violation"):
        validate_crawler_host("192.168.1.100")

    with pytest.raises(ValueError, match="Security Violation"):
        validate_crawler_host("0.0.0.0")

    with pytest.raises(ValueError, match="Security Violation"):
        validate_crawler_host("example.com")


# ============================================================================
# 2. Concurrency Lock Tests
# ============================================================================

def test_concurrency_lock_rejects_second_concurrent_run(tmp_path):
    """When an ingestion task is running, a second concurrent task must be rejected."""
    lock_file = str(tmp_path / "test_ingestion.lock")

    lock1 = IngestionLock(lock_file)
    assert lock1.acquire() is True

    # Second lock attempt must fail
    lock2 = IngestionLock(lock_file)
    assert lock2.acquire() is False

    with pytest.raises(RuntimeError, match="Concurrency Conflict"):
        with lock2:
            pass

    # Release first lock
    lock1.release()

    # Now second lock can acquire
    assert lock2.acquire() is True
    lock2.release()


# ============================================================================
# 3. Default Flags and Constants Tests
# ============================================================================

def test_default_constants_and_flags():
    """Verify standard default constants."""
    assert DEFAULT_SAVE_OPTION == "sqlite"
    assert DEFAULT_ENABLE_COMMENTS is True
    assert DEFAULT_ENABLE_SUB_COMMENTS is False
    assert DEFAULT_CRAWLER_HOST == "127.0.0.1"


def test_parse_args_defaults_and_overrides():
    """Verify CLI argument defaults and flag toggles with mandatory --crawler-commit."""
    args = parse_args([
        "--platform", "xhs",
        "--query", "寒武纪",
        "--source-db", "/tmp/source.db",
        "--crawler-commit", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
    ])
    assert args.save_option == "sqlite"
    assert args.crawler_host == "127.0.0.1"
    assert args.crawler_commit == "d6f7c5bb906b6dac40ddf343ef9e26438a3de092"
    assert args.enable_comments is True
    assert args.enable_sub_comments is False
    assert args.auto_import is False

    # Overrides
    args_overrides = parse_args([
        "--platform", "xhs",
        "--query", "寒武纪",
        "--source-db", "/tmp/source.db",
        "--crawler-commit", "custom_sha_12345",
        "--no-enable-comments",
        "--enable-sub-comments",
        "--auto-import",
    ])
    assert args_overrides.crawler_commit == "custom_sha_12345"
    assert args_overrides.enable_comments is False
    assert args_overrides.enable_sub_comments is True
    assert args_overrides.auto_import is True

    # Omitting --crawler-commit must raise SystemExit (mandatory CLI argument)
    with pytest.raises(SystemExit):
        parse_args([
            "--platform", "xhs",
            "--query", "寒武纪",
            "--source-db", "/tmp/source.db",
        ])


# ============================================================================
# 4. Source DB Table Verification Tests
# ============================================================================

def test_validate_source_db_tables_guard(tmp_path):
    """Verify that source database contains required platform tables."""
    # 1. Non-existent file
    with pytest.raises(FileNotFoundError):
        validate_source_db_tables(str(tmp_path / "nonexistent.db"), "xhs")

    # 2. Database with missing tables
    empty_db = str(tmp_path / "empty.db")
    conn = sqlite3.connect(empty_db)
    conn.execute("CREATE TABLE dummy (id INT)")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="Missing required table 'xhs_note'"):
        validate_source_db_tables(empty_db, "xhs")

    # 3. Database with valid tables
    valid_db = str(tmp_path / "valid.db")
    conn_valid = sqlite3.connect(valid_db)
    init_mediacrawler_db(conn_valid)
    conn_valid.close()

    validate_source_db_tables(valid_db, "xhs", enable_comments=True)


# ============================================================================
# 5. Ingestion Runner and CLI Main Tests
# ============================================================================

def test_run_social_ingestion_success_and_auto_import(tmp_path):
    """End-to-end controlled ingestion execution with schema verification and auto-import."""
    source_db = str(tmp_path / "mediacrawler_source.db")
    archive_db = str(tmp_path / "social_archive.db")
    lock_file = str(tmp_path / "run.lock")

    conn = sqlite3.connect(source_db)
    init_mediacrawler_db(conn)
    populate_sample_mediacrawler_data(conn)
    conn.close()

    result = run_social_ingestion(
        platform="xhs",
        query="寒武纪",
        source_db=source_db,
        crawler_commit="d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
        archive_db=archive_db,
        save_option="sqlite",
        crawler_host="127.0.0.1",
        enable_comments=True,
        enable_sub_comments=False,
        lock_file=lock_file,
        auto_import=True,
    )

    assert result["status"] == "success"
    assert result["import_summary"] is not None
    assert result["import_summary"]["status"] == "completed"
    assert result["import_summary"]["rows_inserted"] > 0


def test_runner_cli_main_rejections(tmp_path, capsys):
    """Runner CLI main returns exit code 1 when safety constraints are violated."""
    source_db = str(tmp_path / "mediacrawler_source.db")
    conn = sqlite3.connect(source_db)
    init_mediacrawler_db(conn)
    conn.close()

    # 1. Non-sqlite save-option rejected
    code_save = runner_cli_main([
        "--platform", "xhs",
        "--query", "寒武纪",
        "--source-db", source_db,
        "--crawler-commit", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
        "--save-option", "jsonl",
    ])
    assert code_save == 1
    captured = capsys.readouterr()
    assert "save_option='sqlite'" in captured.err

    # 2. Non-loopback host rejected
    code_host = runner_cli_main([
        "--platform", "xhs",
        "--query", "寒武纪",
        "--source-db", source_db,
        "--crawler-commit", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
        "--crawler-host", "192.168.1.50",
    ])
    assert code_host == 1
    captured = capsys.readouterr()
    assert "Security Violation" in captured.err

    # 3. Missing --crawler-commit rejected
    with pytest.raises(SystemExit):
        runner_cli_main([
            "--platform", "xhs",
            "--query", "寒武纪",
            "--source-db", source_db,
        ])
