"""Tests for authenticated read-only social data status API (Task 12).

Specification:
- docs/social_data/implementation_plan.md Task 12 & §8
- GET /v1/social-data/status: authenticated, read-only
- Returns: mode, schema_version, status, recent_successful_run, platform_coverage, reason_codes
- Forbids returning post content, comment content, raw texts, cookies, API keys or credentials.
"""

import os
import sqlite3
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from api.database import UserDB
from api.main import app
from api.services import auth_service, social_data_service
from tests.social_fixtures import init_mediacrawler_db, populate_sample_mediacrawler_data
from tradingagents.dataflows.social.archive_schema import init_archive_db
from tradingagents.dataflows.social.mediacrawler_importer import MediaCrawlerImporter


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """Generate authentication headers with a test JWT token."""
    # Test client using standard test token or bearer token
    # Create or use default test user
    from api.database import get_db_ctx
    with get_db_ctx() as db:
        user = auth_service.get_or_create_default_user(db)
        token = auth_service.create_access_token(user)
    return {"Authorization": f"Bearer {token}"}


def test_social_data_status_api_requires_auth(client):
    """GET /v1/social-data/status must require authentication."""
    # When invalid authorization header is provided
    response = client.get(
        "/v1/social-data/status",
        headers={"Authorization": "Bearer invalid_token_12345"},
    )
    assert response.status_code == 401


def test_social_data_status_api_authenticated_returns_metadata(client, auth_headers):
    """GET /v1/social-data/status returns structured metadata for authenticated users."""
    response = client.get("/v1/social-data/status", headers=auth_headers)
    assert response.status_code == 200

    data = response.json()
    assert "mode" in data
    assert "schema_version" in data
    assert "status" in data
    assert "platform_coverage" in data
    assert "reason_codes" in data or "error_codes" in data

    # Verify mode is one of valid modes
    assert data["mode"] in ("disabled", "shadow", "active")
    assert isinstance(data["platform_coverage"], dict)


def test_social_data_status_api_forbids_post_contents_and_secrets(client, auth_headers):
    """Status API must NEVER return post text, comments, cookies, or secrets."""
    response = client.get("/v1/social-data/status", headers=auth_headers)
    assert response.status_code == 200

    raw_text = response.text.lower()

    # Forbidden fields/contents
    forbidden_keys = (
        "post_text",
        "comment_text",
        "raw_text",
        "raw_posts",
        "cookie",
        "cookies",
        "secret_key",
        "api_key",
        "password",
        "xsec_token",
    )

    data = response.json()
    for key in forbidden_keys:
        assert key not in data, f"Forbidden key '{key}' found in top-level status response"

    def _check_no_forbidden_keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert str(k).lower() not in forbidden_keys, f"Forbidden key '{k}' found in nested response"
                _check_no_forbidden_keys(v)
        elif isinstance(obj, list):
            for item in obj:
                _check_no_forbidden_keys(item)

    _check_no_forbidden_keys(data)


def test_social_data_service_status_aggregation():
    """SocialDataService.get_status aggregates metadata deterministically."""
    status_data = social_data_service.get_social_data_status()

    assert isinstance(status_data, dict)
    assert "mode" in status_data
    assert "schema_version" in status_data
    assert "status" in status_data
    assert "platform_coverage" in status_data
    assert "recent_successful_run" in status_data
    assert "crawler_status" in status_data
    assert "ingestion_status" in status_data
    assert "freshness" in status_data
    assert "analysis_availability" in status_data


def test_status_proves_four_dimensions_independent(tmp_path, monkeypatch):
    """Prove that crawler success, ingestion success, freshness, and analysis availability are independent.

    1. TA_SOCIAL_MODE=disabled does NOT mean ingestion must stop: crawler and import can be
       successful and fresh while analysis availability is disabled.
    2. Merely creating/touching an empty archive file must NOT return operational in active mode.
    3. When archive contains valid snapshots, active mode returns operational.
    """
    archive_db = str(tmp_path / "social_archive.db")
    init_archive_db(archive_db)

    # -------------------------------------------------------------------------
    # Case 1: TA_SOCIAL_MODE=disabled with populated archive DB
    # -------------------------------------------------------------------------
    # Populate mock data into archive
    source_db = str(tmp_path / "source.db")
    s_conn = init_mediacrawler_db(source_db)
    populate_sample_mediacrawler_data(s_conn)
    s_conn.close()

    importer = MediaCrawlerImporter(archive_db=archive_db, crawler_commit="d6f7c5bb906b6dac40ddf343ef9e26438a3de092")
    importer.import_records(source_db=source_db, platform="xhs", query_text="寒武纪")

    monkeypatch.setenv("TA_SOCIAL_MODE", "disabled")
    monkeypatch.setenv("TA_SOCIAL_ARCHIVE_DB", archive_db)

    status_disabled = social_data_service.get_social_data_status()

    # Crawler status & ingestion status reflect real imported data
    assert status_disabled["mode"] == "disabled"
    assert status_disabled["status"] == "disabled"
    assert status_disabled["crawler_status"]["status"] == "success"
    assert status_disabled["ingestion_status"]["status"] == "completed"
    assert status_disabled["ingestion_status"]["rows_inserted"] > 0
    assert status_disabled["freshness"]["snapshot_count"] > 0
    # Downstream analysis is disabled, proving separation of ingestion from consumption
    assert status_disabled["analysis_availability"]["mode"] == "disabled"
    assert status_disabled["analysis_availability"]["available"] is False
    assert status_disabled["analysis_availability"]["status"] == "disabled"

    # -------------------------------------------------------------------------
    # Case 2: TA_SOCIAL_MODE=active with EMPTY archive DB (file exists, 0 snapshots)
    # -------------------------------------------------------------------------
    empty_archive_db = str(tmp_path / "empty_archive.db")
    init_archive_db(empty_archive_db)  # initialized schema, but 0 snapshots and 0 runs

    monkeypatch.setenv("TA_SOCIAL_MODE", "active")
    monkeypatch.setenv("TA_SOCIAL_ARCHIVE_DB", empty_archive_db)

    status_empty = social_data_service.get_social_data_status()

    # CONTRACT 2: Forbidden to return operational merely because archive file exists!
    assert status_empty["status"] != "operational"
    assert status_empty["status"] == "degraded"
    assert "social_archive_empty" in status_empty["reason_codes"]
    assert status_empty["analysis_availability"]["available"] is False
    assert status_empty["freshness"]["snapshot_count"] == 0

    # -------------------------------------------------------------------------
    # Case 3: TA_SOCIAL_MODE=active with populated archive DB (fresh data)
    # -------------------------------------------------------------------------
    monkeypatch.setenv("TA_SOCIAL_MODE", "active")
    monkeypatch.setenv("TA_SOCIAL_ARCHIVE_DB", archive_db)
    monkeypatch.setenv("TA_SOCIAL_LOOKBACK_DAYS", "14")  # 14 days covers 2026-08-26 to 2026-09-05

    status_active = social_data_service.get_social_data_status()

    assert status_active["status"] == "operational"
    assert status_active["freshness"]["status"] == "fresh"
    assert status_active["analysis_availability"]["available"] is True
    assert status_active["analysis_availability"]["status"] == "operational"
    assert status_active["ingestion_status"]["rows_inserted"] > 0

    # -------------------------------------------------------------------------
    # Case 4: TA_SOCIAL_MODE=active but archive data is STALE (> lookback window)
    # -------------------------------------------------------------------------
    monkeypatch.setenv("TA_SOCIAL_LOOKBACK_DAYS", "3")  # 3 days makes 10-day-old data stale

    status_stale = social_data_service.get_social_data_status()

    assert status_stale["status"] == "degraded"
    assert status_stale["freshness"]["status"] == "stale"
    assert "social_archive_stale" in status_stale["reason_codes"]
    assert status_stale["analysis_availability"]["status"] == "degraded"


def test_status_reads_persistent_ingest_runs_table(tmp_path, monkeypatch):
    """Verify get_social_data_status reads from social_ingest_runs rather than memory-only cache."""
    archive_db = str(tmp_path / "persistent_archive.db")
    conn = init_archive_db(archive_db)

    # Insert a real ingest run into social_ingest_runs
    conn.execute(
        "INSERT INTO social_ingest_runs ("
        "run_id, provider, platform, query_text, started_at, completed_at, "
        "status, crawler_commit, source_schema_fingerprint, rows_read, rows_inserted, rows_rejected"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "run_audit_001", "mediacrawler", "xhs", "688256",
            "2026-08-30T10:00:00Z", "2026-08-30T10:01:30Z",
            "completed", "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
            "fp_sample", 42, 38, 4
        )
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("TA_SOCIAL_MODE", "disabled")
    monkeypatch.setenv("TA_SOCIAL_ARCHIVE_DB", archive_db)

    # Clear memory cache to prove status reads from DB table
    social_data_service._recent_run_cache = None

    status_result = social_data_service.get_social_data_status()

    # Verify ingestion_status and recent_successful_run are populated from DB table
    assert status_result["ingestion_status"]["run_id"] == "run_audit_001"
    assert status_result["ingestion_status"]["rows_read"] == 42
    assert status_result["ingestion_status"]["rows_inserted"] == 38
    assert status_result["ingestion_status"]["rows_rejected"] == 4

    rec = status_result["recent_successful_run"]
    assert rec is not None
    assert rec["run_id"] == "run_audit_001"
    assert rec["symbol"] == "688256"
    assert rec["post_count"] == 38

