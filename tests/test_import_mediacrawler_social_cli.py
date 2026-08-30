"""Unit and CLI tests for import_mediacrawler_social.py (Task 13 / §3.1 / D-008).

Specifications:
- docs/social_data/implementation_plan.md Task 13, §3.1, §3.3, §4.1, §4.2, D-008
- Enforces mandatory CLI arguments: --source-db, --archive-db, --platform, --query, --crawler-commit
- Validates successful and failed import execution paths
- Ensures post and comment content are not leaked to stdout/stderr
"""

import os
import sqlite3
import subprocess
import sys
import pytest

from scripts.import_mediacrawler_social import main as import_cli_main
from tests.social_fixtures import (
    init_mediacrawler_db,
    populate_sample_mediacrawler_data,
)


@pytest.fixture
def tmp_source_and_archive(tmp_path):
    """Create a temporary source MediaCrawler DB and archive DB."""
    source_db_path = str(tmp_path / "mediacrawler_source.db")
    archive_db_path = str(tmp_path / "social_archive.db")

    conn = sqlite3.connect(source_db_path)
    init_mediacrawler_db(conn)
    populate_sample_mediacrawler_data(conn)
    conn.close()

    return source_db_path, archive_db_path


def test_import_cli_missing_required_args_fails():
    """All 5 CLI arguments are mandatory. Omitting any must fail with non-zero exit."""
    # 1. Missing all arguments
    with pytest.raises(SystemExit) as exc_info:
        import_cli_main([])
    assert exc_info.value.code != 0

    # 2. Missing --crawler-commit
    with pytest.raises(SystemExit) as exc_info:
        import_cli_main([
            "--source-db", "/tmp/nonexistent.db",
            "--archive-db", "/tmp/archive.db",
            "--platform", "xhs",
            "--query", "寒武纪",
        ])
    assert exc_info.value.code != 0

    # 3. Missing --source-db
    with pytest.raises(SystemExit) as exc_info:
        import_cli_main([
            "--archive-db", "/tmp/archive.db",
            "--platform", "xhs",
            "--query", "寒武纪",
            "--crawler-commit", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
        ])
    assert exc_info.value.code != 0


def test_import_cli_nonexistent_source_db_fails(tmp_path, capsys):
    """Nonexistent source database path returns exit code 1 with clear error."""
    archive_db = str(tmp_path / "archive.db")
    exit_code = import_cli_main([
        "--source-db", "/tmp/definitely_nonexistent_12345.db",
        "--archive-db", archive_db,
        "--platform", "xhs",
        "--query", "寒武纪",
        "--crawler-commit", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
    ])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Source database does not exist" in captured.err


def test_import_cli_unsupported_platform_fails(tmp_source_and_archive, capsys):
    """Unsupported platform string fails with exit code 1."""
    source_db, archive_db = tmp_source_and_archive
    exit_code = import_cli_main([
        "--source-db", source_db,
        "--archive-db", archive_db,
        "--platform", "unsupported_platform",
        "--query", "寒武纪",
        "--crawler-commit", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
    ])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Unsupported platform" in captured.err


def test_import_cli_successful_import_and_summary(tmp_source_and_archive, capsys):
    """Valid parameters successfully import records and print summary without leaking text."""
    source_db, archive_db = tmp_source_and_archive
    exit_code = import_cli_main([
        "--source-db", source_db,
        "--archive-db", archive_db,
        "--platform", "xhs",
        "--query", "寒武纪",
        "--crawler-commit", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "MediaCrawler Social Ingest Summary" in captured.out
    assert "Status:         completed" in captured.out
    assert "Platform:       xhs" in captured.out
    assert "Query:          寒武纪" in captured.out
    assert "Rows Inserted:" in captured.out

    # Verify rows in archive DB
    conn = sqlite3.connect(archive_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM social_record_snapshots")
    count = cur.fetchone()[0]
    assert count > 0

    cur.execute("SELECT status FROM social_ingest_runs")
    run_status = cur.fetchone()[0]
    assert run_status == "completed"
    conn.close()


def test_import_cli_does_not_print_post_text_at_default_level(tmp_path, capsys):
    """CLI summary must not output post body text or comment content."""
    source_db = str(tmp_path / "secret_source.db")
    archive_db = str(tmp_path / "archive.db")

    secret_phrase = "SECRET_DISCLOSURE_SENSITIVE_TEXT_999"
    conn = sqlite3.connect(source_db)
    init_mediacrawler_db(conn)
    conn.execute(
        """
        INSERT INTO xhs_note (
            note_id, title, desc, time, add_ts, last_modify_ts, liked_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "note_secret_01",
            "Title Secret",
            secret_phrase,
            1787713931,
            1787714000,
            1787714000,
            "10",
        ),
    )
    conn.commit()
    conn.close()

    exit_code = import_cli_main([
        "--source-db", source_db,
        "--archive-db", archive_db,
        "--platform", "xhs",
        "--query", "寒武纪",
        "--crawler-commit", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert secret_phrase not in captured.out
    assert secret_phrase not in captured.err


def test_import_cli_schema_mismatch_fails(tmp_path, capsys):
    """Source database missing required columns exits non-zero with schema mismatch error."""
    source_db = str(tmp_path / "corrupt_schema.db")
    archive_db = str(tmp_path / "archive.db")

    conn = sqlite3.connect(source_db)
    # Missing required add_ts, last_modify_ts
    conn.execute("CREATE TABLE xhs_note (id INTEGER PRIMARY KEY, note_id TEXT, time INTEGER)")
    conn.commit()
    conn.close()

    exit_code = import_cli_main([
        "--source-db", source_db,
        "--archive-db", archive_db,
        "--platform", "xhs",
        "--query", "寒武纪",
        "--crawler-commit", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
    ])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "social_schema_mismatch" in captured.out or "social_schema_mismatch" in captured.err
