"""Unit tests for Social Archive Provider and Registry (Task 5 / B5).

Specifications:
- docs/social_data/implementation_plan.md Task 5, §7
- DECISIONS.md D-008, D-009, D-010
- Protocol compliance: SocialDataProvider (only name + fetch_records)
- Read-only connection: file:<path>?mode=ro, PRAGMA query_only=ON, PRAGMA busy_timeout
- Failure semantics: archive missing, schema mismatch, lock timeout, empty
"""

import json
import os
import sqlite3
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tradingagents.dataflows.social.archive_schema import (
    ARCHIVE_SCHEMA_VERSION,
    init_archive_db,
)
from tradingagents.dataflows.social.contracts import (
    REASON_SOCIAL_ARCHIVE_LOCKED,
    REASON_SOCIAL_ARCHIVE_MISSING,
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_SCHEMA_MISMATCH,
    SocialRawRecordV1,
    SocialStatus,
)
from tradingagents.dataflows.social.provider import (
    SocialArchiveProvider,
    SocialDataProvider,
    SocialFetchResult,
)
from tradingagents.dataflows.social.registry import (
    SocialDataProviderRegistry,
    build_default_social_registry,
)
from tests.social_fixtures import (
    init_mediacrawler_db,
    populate_sample_mediacrawler_data,
)
from tradingagents.dataflows.social.mediacrawler_importer import MediaCrawlerImporter
from tradingagents.dataflows.social.entity_resolver import EntityResolver


CN_TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def sample_archive_db(tmp_path):
    """Create and populate a sample archive SQLite DB from mock MediaCrawler data."""
    crawler_db_path = str(tmp_path / "mediacrawler.db")
    archive_db_path = str(tmp_path / "social_archive.db")

    c_conn = init_mediacrawler_db(crawler_db_path)
    populate_sample_mediacrawler_data(c_conn)
    c_conn.close()

    resolver = EntityResolver()
    importer = MediaCrawlerImporter(
        archive_db=archive_db_path,
        entity_resolver=resolver,
    )
    result = importer.import_records(source_db=crawler_db_path)
    assert result["rows_inserted"] > 0
    if hasattr(importer, "archive_conn") and importer.archive_conn:
        importer.archive_conn.close()
    return archive_db_path


# ============================================================================
# 1. Protocol and Independence Tests
# ============================================================================

def test_social_provider_protocol_compliance(sample_archive_db):
    """Protocol only requires `name` and `fetch_records`.

    Must NOT inherit from BaseMarketDataProvider.
    Must NOT be registered into market data providers registry.
    """
    provider = SocialArchiveProvider(db_path=sample_archive_db)
    assert isinstance(provider, SocialDataProvider)
    assert hasattr(provider, "name")
    assert hasattr(provider, "fetch_records")
    assert provider.name == "archive_sqlite"

    # Check non-inheritance from BaseMarketDataProvider
    from tradingagents.dataflows.providers.base import BaseMarketDataProvider
    assert not isinstance(provider, BaseMarketDataProvider)
    assert not issubclass(SocialArchiveProvider, BaseMarketDataProvider)

    # Check market registry isolation
    from tradingagents.dataflows.providers.registry import build_default_registry
    market_reg = build_default_registry()
    assert "archive_sqlite" not in market_reg.list_names()
    assert market_reg.get("archive_sqlite") is None


# ============================================================================
# 2. Social Registry Tests
# ============================================================================

def test_social_registry_register_and_lookup(sample_archive_db):
    """Test SocialDataProviderRegistry registration, retrieval, and listing."""
    registry = SocialDataProviderRegistry()
    provider = SocialArchiveProvider(db_path=sample_archive_db)

    registry.register(provider)
    assert registry.get("archive_sqlite") is provider
    assert registry.get("nonexistent") is None
    assert registry.list_names() == ["archive_sqlite"]


def test_build_default_social_registry(sample_archive_db):
    """Test build_default_social_registry returns populated registry."""
    registry = build_default_social_registry(archive_db_path=sample_archive_db)
    assert isinstance(registry, SocialDataProviderRegistry)
    provider = registry.get("archive_sqlite")
    assert provider is not None
    assert isinstance(provider, SocialArchiveProvider)
    assert provider.db_path == sample_archive_db


# ============================================================================
# 3. Read-Only Connection and PRAGMA Tests
# ============================================================================

def test_provider_readonly_connection_rejects_writes(sample_archive_db):
    """Provider opens SQLite as mode=ro and PRAGMA query_only=ON.

    Attempting write operations must raise sqlite3.OperationalError.
    """
    provider = SocialArchiveProvider(db_path=sample_archive_db)
    conn = provider._get_readonly_connection(sample_archive_db)

    # Verify query_only is ON
    cursor = conn.cursor()
    cursor.execute("PRAGMA query_only;")
    assert cursor.fetchone()[0] == 1

    # Attempting write must fail
    with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
        cursor.execute("INSERT INTO social_archive_meta (key, value) VALUES ('test_k', 'test_v')")

    with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
        cursor.execute("DELETE FROM social_record_snapshots")

    conn.close()


# ============================================================================
# 4. Failure Semantics Tests (Missing DB / Schema Mismatch / Lock Timeout)
# ============================================================================

def test_provider_missing_archive_db_failure(tmp_path):
    """Missing archive DB file must return status=failed, reason=social_archive_missing."""
    missing_path = str(tmp_path / "non_existent_archive.db")
    provider = SocialArchiveProvider(db_path=missing_path)

    res = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26")
    assert isinstance(res, SocialFetchResult)
    assert res.status == SocialStatus.FAILED.value
    assert REASON_SOCIAL_ARCHIVE_MISSING in res.reason_codes
    assert len(res.records) == 0


def test_provider_empty_db_path_env_missing(monkeypatch):
    """When db_path is None and TA_SOCIAL_ARCHIVE_DB is unset, return missing failure."""
    monkeypatch.delenv("TA_SOCIAL_ARCHIVE_DB", raising=False)
    provider = SocialArchiveProvider(db_path=None)

    res = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26")
    assert res.status == SocialStatus.FAILED.value
    assert REASON_SOCIAL_ARCHIVE_MISSING in res.reason_codes


def test_provider_schema_mismatch_failure(tmp_path):
    """Archive DB missing required schema tables returns status=failed, reason=social_schema_mismatch."""
    corrupted_db_path = str(tmp_path / "corrupted_archive.db")
    conn = sqlite3.connect(corrupted_db_path)
    conn.execute("CREATE TABLE dummy_table (id INTEGER PRIMARY KEY);")
    conn.commit()
    conn.close()

    provider = SocialArchiveProvider(db_path=corrupted_db_path)
    res = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26")
    assert res.status == SocialStatus.FAILED.value
    assert REASON_SOCIAL_SCHEMA_MISMATCH in res.reason_codes


def test_provider_busy_lock_timeout_failure(sample_archive_db):
    """When archive DB is locked exclusively and timeout expires, return status=timeout."""
    # Open exclusive write lock on archive DB
    lock_conn = sqlite3.connect(sample_archive_db, timeout=1.0)
    lock_conn.execute("BEGIN EXCLUSIVE")

    try:
        # Use short timeout (50ms)
        provider = SocialArchiveProvider(db_path=sample_archive_db, timeout_ms=50)
        res = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26")
        assert res.status == SocialStatus.TIMEOUT.value
        assert REASON_SOCIAL_ARCHIVE_LOCKED in res.reason_codes
    finally:
        lock_conn.rollback()
        lock_conn.close()


def test_provider_empty_records_for_unmentioned_symbol(sample_archive_db):
    """When symbol has no records in archive, return status=empty, reason=social_empty."""
    provider = SocialArchiveProvider(db_path=sample_archive_db)
    res = provider.fetch_records(symbol="000001.SZ", as_of="2026-08-26")
    assert res.status == SocialStatus.EMPTY.value
    assert REASON_SOCIAL_EMPTY in res.reason_codes
    assert len(res.records) == 0


# ============================================================================
# 5. Successful Fetch and Filtering Tests
# ============================================================================

def test_provider_successful_fetch_and_contract_validation(sample_archive_db):
    """Fetch records for 688256.SH on 2026-08-26 and verify records and attributes."""
    provider = SocialArchiveProvider(db_path=sample_archive_db)
    res = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26")

    assert res.status == SocialStatus.AVAILABLE.value
    assert res.reason_codes == []
    assert res.requested_as_of == "2026-08-26"
    assert res.cutoff_at is not None
    assert res.window_start is not None
    assert len(res.records) > 0

    # Test iterating over SocialFetchResult
    for record in res:
        assert isinstance(record, SocialRawRecordV1)
        record.validate()
        assert record.platform in ("xhs", "dy")
        assert record.record_type in ("post", "comment")
        assert record.content_hash.startswith("sha256:")
        assert record.metrics_hash.startswith("sha256:")
        assert record.source_ref.provider == "mediacrawler"


def test_provider_platform_filtering(sample_archive_db):
    """Fetch records filtering by platform."""
    provider = SocialArchiveProvider(db_path=sample_archive_db)

    res_xhs = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26", platforms=["xhs"])
    assert res_xhs.status == SocialStatus.AVAILABLE.value
    assert all(r.platform == "xhs" for r in res_xhs.records)

    res_dy = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26", platforms=["dy"])
    assert res_dy.status == SocialStatus.AVAILABLE.value
    assert all(r.platform == "dy" for r in res_dy.records)


def test_provider_max_posts_and_comments_limits(sample_archive_db):
    """Fetch records with max_posts and max_comments limits."""
    provider = SocialArchiveProvider(db_path=sample_archive_db)

    res = provider.fetch_records(
        symbol="688256.SH",
        as_of="2026-08-26",
        max_posts=1,
        max_comments=1,
    )
    assert res.status == SocialStatus.AVAILABLE.value
    posts = [r for r in res.records if r.record_type == "post"]
    comments = [r for r in res.records if r.record_type == "comment"]
    assert len(posts) <= 1
    assert len(comments) <= 1
