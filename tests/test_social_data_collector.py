"""Unit and integration tests for SocialDataCollector and Rollout Modes (Task 7 / §7 / D-008).

Specifications:
- docs/social_data/implementation_plan.md Task 7, §5.5, §7, §8, D-008
- DECISIONS.md D-008, D-009, D-010
- Pure offline fixtures; NO real network; NO @pytest.mark.asyncio.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo
import pytest

from tradingagents.dataflows.social.archive_schema import init_archive_db
from tradingagents.dataflows.social.collector import (
    SocialDataCollector,
    build_social_failure_ledger,
)
from tradingagents.dataflows.social.contracts import (
    REASON_OBSERVED_AFTER_CUTOFF_EXCLUDED,
    REASON_SOCIAL_ARCHIVE_LOCKED,
    REASON_SOCIAL_ARCHIVE_MISSING,
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_FUTURE_AS_OF,
    REASON_SOCIAL_INSUFFICIENT_COVERAGE,
    REASON_SOCIAL_INVALID_AS_OF,
    REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
    REASON_SOCIAL_NOT_APPLICABLE,
    REASON_SOCIAL_PLATFORM_PARTIAL,
    SentimentBundleV1,
    SocialDataContext,
    SocialMetrics,
    SocialRawRecordV1,
    SocialStatus,
    SourceRef,
    compute_content_hash,
    compute_metrics_hash,
)
from tradingagents.dataflows.social.entity_resolver import EntityResolver
from tradingagents.dataflows.social.mediacrawler_importer import MediaCrawlerImporter
from tradingagents.dataflows.social.provider import (
    SocialArchiveProvider,
    SocialDataProvider,
    SocialFetchResult,
)
from tradingagents.dataflows.social.registry import (
    SocialDataProviderRegistry,
    build_default_social_registry,
)
from tradingagents.default_config import DEFAULT_CONFIG
from tests.social_fixtures import (
    init_mediacrawler_db,
    populate_sample_mediacrawler_data,
)

CN_TZ = ZoneInfo("Asia/Shanghai")


# ============================================================================
# Mock / Spy Provider for Testing
# ============================================================================

class SpySocialProvider:
    """Spy provider to verify whether fetch_records is called and inspect arguments."""

    name: str = "archive_sqlite"

    def __init__(self, return_result: Optional[SocialFetchResult] = None, timeout_ms: Optional[int] = None):
        self.call_count = 0
        self.last_call_args: Dict[str, Any] = {}
        self.return_result = return_result
        self.timeout_ms = timeout_ms

    def fetch_records(
        self,
        symbol: str,
        as_of: str,
        lookback_days: int = 7,
        platforms: Optional[Sequence[str]] = None,
        max_posts: Optional[int] = None,
        max_comments: Optional[int] = None,
        now: Optional[datetime] = None,
        **kwargs: Any,
    ) -> SocialFetchResult:
        self.call_count += 1
        self.last_call_args = {
            "symbol": symbol,
            "as_of": as_of,
            "lookback_days": lookback_days,
            "platforms": platforms,
            "max_posts": max_posts,
            "max_comments": max_comments,
            "now": now,
            "kwargs": kwargs,
        }
        if self.return_result is not None:
            return self.return_result
        return SocialFetchResult(
            status=SocialStatus.EMPTY.value,
            requested_as_of=as_of,
            reason_codes=[REASON_SOCIAL_EMPTY],
        )


@pytest.fixture
def sample_archive_db(tmp_path):
    """Fixture creating an archive DB with imported records for 688256.SH."""
    crawler_db_path = str(tmp_path / "mediacrawler.db")
    archive_db_path = str(tmp_path / "social_archive.db")

    c_conn = init_mediacrawler_db(crawler_db_path)
    populate_sample_mediacrawler_data(c_conn)
    c_conn.close()

    resolver = EntityResolver()
    importer = MediaCrawlerImporter(
        archive_db=archive_db_path,
        crawler_commit="d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
        entity_resolver=resolver,
    )
    result = importer.import_records(source_db=crawler_db_path)
    assert result["rows_inserted"] > 0
    if hasattr(importer, "archive_conn") and importer.archive_conn:
        importer.archive_conn.close()
    return archive_db_path


# ============================================================================
# 1. Default and Disabled Mode Tests
# ============================================================================

def test_default_mode_is_disabled(monkeypatch):
    """Requirement A: Default TA_SOCIAL_MODE is disabled."""
    monkeypatch.delenv("TA_SOCIAL_MODE", raising=False)
    collector = SocialDataCollector()
    assert collector.mode == "disabled"
    assert collector.provider_name == "archive_sqlite"
    assert collector.archive_db == ""
    assert collector.platforms == ["xhs", "dy"]
    assert collector.lookback_days == 7
    assert collector.max_posts == 100
    assert collector.max_comments == 300
    assert collector.min_posts == 3
    assert collector.min_classified == 20
    assert collector.min_authors == 10
    assert collector.evidence_limit == 20
    assert collector.fetch_timeout == 5.0


def test_disabled_mode_does_not_call_provider():
    """Requirement C1: disabled mode returns not_applicable and NEVER accesses DB / provider."""
    spy = SpySocialProvider()
    collector = SocialDataCollector(
        mode="disabled",
        archive_db="/tmp/fake_archive.db",
        custom_provider=spy,
    )

    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert spy.call_count == 0
    assert ctx["status"] == "not_applicable"
    assert ctx["mode"] == "disabled"
    assert ctx["direction_allowed"] is False
    assert REASON_SOCIAL_NOT_APPLICABLE in ctx["reason_codes"]
    assert ctx["bundle"]["status"] == "not_applicable"
    assert ctx["bundle"]["direction_allowed"] is False
    assert ctx["data_failure_ledger"] == []


# ============================================================================
# 2. Archive DB Path Validation Tests (shadow / active)
# ============================================================================

def test_empty_archive_db_path_fails_typed():
    """Requirement C2: empty TA_SOCIAL_ARCHIVE_DB in shadow/active returns typed failed."""
    for test_mode in ["shadow", "active"]:
        collector = SocialDataCollector(mode=test_mode, archive_db="")
        ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

        assert ctx["status"] == "failed"
        assert ctx["mode"] == test_mode
        assert ctx["direction_allowed"] is False
        assert REASON_SOCIAL_ARCHIVE_MISSING in ctx["reason_codes"]
        assert ctx["bundle"]["status"] == "failed"
        assert len(ctx["data_failure_ledger"]) == 1
        ledger_entry = ctx["data_failure_ledger"][0]
        assert ledger_entry["source"] == "social_archive"
        assert ledger_entry["status"] == "failed"
        assert ledger_entry["reason_code"] == REASON_SOCIAL_ARCHIVE_MISSING
        assert ledger_entry["gap_class"] == "operational"


def test_relative_archive_db_path_rejected():
    """Requirement C2: relative path in shadow/active is rejected and returns failed."""
    collector = SocialDataCollector(
        mode="active",
        archive_db="data/relative_social_archive.db",
    )
    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert ctx["status"] == "failed"
    assert REASON_SOCIAL_ARCHIVE_MISSING in ctx["reason_codes"]
    assert ctx["direction_allowed"] is False
    assert len(ctx["data_failure_ledger"]) >= 1


def test_nonexistent_absolute_path_returns_failed():
    """Requirement C2: absolute path but missing file returns failed missing."""
    collector = SocialDataCollector(
        mode="active",
        archive_db="/tmp/non_existent_archive_20260829_xyz.db",
    )
    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert ctx["status"] == "failed"
    assert REASON_SOCIAL_ARCHIVE_MISSING in ctx["reason_codes"]
    assert ctx["direction_allowed"] is False


# ============================================================================
# 3. Non-A-share Symbol Tests
# ============================================================================

def test_non_a_share_symbol_returns_not_applicable():
    """Requirement C3: non-A-share symbol returns not_applicable without querying DB."""
    spy = SpySocialProvider()
    collector = SocialDataCollector(
        mode="active",
        archive_db="/tmp/some_abs_db.db",
        custom_provider=spy,
    )

    for invalid_sym in ["AAPL", "BTC-USD", "INVALID", "", "GOOGL"]:
        ctx = collector.collect(symbol=invalid_sym, as_of="2026-08-26")
        assert ctx["status"] == "not_applicable"
        assert ctx["direction_allowed"] is False
        assert REASON_SOCIAL_NOT_APPLICABLE in ctx["reason_codes"]
        assert ctx["data_failure_ledger"] == []

    assert spy.call_count == 0


# ============================================================================
# 4. Canary Gating Tests
# ============================================================================

def test_canary_symbol_gating(sample_archive_db):
    """Requirement C4: when canary symbols configured, unlisted symbol MUST NOT silently be active."""
    collector = SocialDataCollector(
        mode="active",
        archive_db=sample_archive_db,
        canary_symbols="600519.SH, 000001.SZ",
    )

    # 688256.SH is NOT in canary whitelist -> MUST NOT be active
    ctx_blocked = collector.collect(symbol="688256.SH", as_of="2026-08-26")
    assert ctx_blocked["direction_allowed"] is False
    assert ctx_blocked["status"] == "not_applicable"

    # 600519.SH IS in canary whitelist -> allowed to run collection
    ctx_allowed = collector.collect(symbol="600519.SH", as_of="2026-08-26")
    # Since sample DB has records for 688256 but not 600519, 600519 returns empty (valid execution)
    assert ctx_allowed["status"] in ("empty", "available", "partial")


# ============================================================================
# 5. Success Path & Rollout Modes (shadow / active)
# ============================================================================

def test_active_mode_success_path_with_fixture(sample_archive_db):
    """Requirement C2 / 6: successful active collection returns SocialDataContext with populated bundle."""
    collector = SocialDataCollector(
        mode="active",
        archive_db=sample_archive_db,
        lookback_days=7,
        min_posts=1,
        min_classified=1,
        min_authors=1,
    )

    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert ctx["status"] in ("available", "partial")
    assert ctx["mode"] == "active"
    assert ctx["requested_as_of"] == "2026-08-26"
    assert isinstance(ctx["bundle"], dict)
    assert ctx["bundle"]["symbol"] == "688256.SH"
    assert ctx["source_provenance"]["social_archive"]["provider"] == "archive_sqlite"
    assert ctx["data_failure_ledger"] == []


def test_shadow_mode_success_path(sample_archive_db):
    """Requirement C2: shadow mode executes collection and records mode='shadow'."""
    collector = SocialDataCollector(
        mode="shadow",
        archive_db=sample_archive_db,
        lookback_days=7,
        min_posts=1,
        min_classified=1,
        min_authors=1,
    )

    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert ctx["mode"] == "shadow"
    assert ctx["status"] in ("available", "partial")
    assert isinstance(ctx["bundle"], dict)
    assert ctx["data_failure_ledger"] == []


# ============================================================================
# 6. Timeout and Provider Error Handling Tests
# ============================================================================

def test_fetch_timeout_configuration():
    """Requirement C5 / 7: fetch_timeout is converted to timeout_ms for provider registry."""
    collector = SocialDataCollector(fetch_timeout=3)
    assert collector.fetch_timeout == 3.0
    assert collector.fetch_timeout_ms == 3000

    provider = collector.registry.get("archive_sqlite")
    assert isinstance(provider, SocialArchiveProvider)
    assert provider._timeout_ms == 3000


def test_timeout_maps_to_operational_failure_ledger(sample_archive_db):
    """Requirement C5: lock timeout returns status=timeout with operational failure ledger."""
    timeout_result = SocialFetchResult(
        status=SocialStatus.TIMEOUT.value,
        requested_as_of="2026-08-26",
        reason_codes=[REASON_SOCIAL_ARCHIVE_LOCKED],
    )
    spy = SpySocialProvider(return_result=timeout_result)

    collector = SocialDataCollector(
        mode="active",
        archive_db=sample_archive_db,
        custom_provider=spy,
    )

    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert ctx["status"] == "timeout"
    assert ctx["direction_allowed"] is False
    assert REASON_SOCIAL_ARCHIVE_LOCKED in ctx["reason_codes"]
    assert len(ctx["data_failure_ledger"]) == 1
    assert ctx["data_failure_ledger"][0]["status"] == "timeout"
    assert ctx["data_failure_ledger"][0]["reason_code"] == REASON_SOCIAL_ARCHIVE_LOCKED
    assert ctx["data_failure_ledger"][0]["gap_class"] == "operational"


def test_refused_maps_to_structural_failure_ledger(sample_archive_db):
    """Requirement §5.5: future as_of refusal maps to structural failure ledger."""
    refused_result = SocialFetchResult(
        status=SocialStatus.REFUSED.value,
        requested_as_of="2099-01-01",
        reason_codes=[REASON_SOCIAL_FUTURE_AS_OF],
    )
    spy = SpySocialProvider(return_result=refused_result)

    collector = SocialDataCollector(
        mode="active",
        archive_db=sample_archive_db,
        custom_provider=spy,
    )

    ctx = collector.collect(symbol="688256.SH", as_of="2099-01-01")

    assert ctx["status"] == "refused"
    assert ctx["direction_allowed"] is False
    assert len(ctx["data_failure_ledger"]) == 1
    assert ctx["data_failure_ledger"][0]["status"] == "refused"
    assert ctx["data_failure_ledger"][0]["gap_class"] == "structural"


# ============================================================================
# 7. Config and DefaultConfig Integration Tests
# ============================================================================

def test_default_config_integration():
    """Verify DEFAULT_CONFIG has social block and SocialDataCollector reads it."""
    assert "social" in DEFAULT_CONFIG
    social_cfg = DEFAULT_CONFIG["social"]
    assert social_cfg["mode"] == "disabled"
    assert social_cfg["provider"] == "archive_sqlite"
    assert social_cfg["archive_db"] == ""
    assert social_cfg["platforms"] == "xhs,dy"
    assert social_cfg["lookback_days"] == 7
    assert social_cfg["max_posts"] == 100
    assert social_cfg["max_comments"] == 300
    assert social_cfg["min_posts"] == 3
    assert social_cfg["min_classified"] == 20
    assert social_cfg["min_authors"] == 10
    assert social_cfg["evidence_limit"] == 20
    assert social_cfg["canary_symbols"] == ""
    assert social_cfg["fetch_timeout"] == 5

    collector = SocialDataCollector(config=DEFAULT_CONFIG)
    assert collector.mode == "disabled"
    assert collector.provider_name == "archive_sqlite"
    assert collector.archive_db == ""
    assert collector.lookback_days == 7


def test_custom_dict_config_override():
    """Verify custom config dict overrides default settings."""
    cfg = {
        "social": {
            "mode": "shadow",
            "provider": "archive_sqlite",
            "archive_db": "/var/data/social.db",
            "platforms": "xhs",
            "lookback_days": 14,
            "max_posts": 50,
            "max_comments": 150,
            "min_posts": 5,
            "min_classified": 30,
            "min_authors": 15,
            "evidence_limit": 10,
            "canary_symbols": "600519.SH",
            "fetch_timeout": 10,
        }
    }
    collector = SocialDataCollector(config=cfg)
    assert collector.mode == "shadow"
    assert collector.archive_db == "/var/data/social.db"
    assert collector.platforms == ["xhs"]
    assert collector.lookback_days == 14
    assert collector.max_posts == 50
    assert collector.max_comments == 150
    assert collector.min_posts == 5
    assert collector.min_classified == 30
    assert collector.min_authors == 15
    assert collector.evidence_limit == 10
    assert collector.canary_symbols == {"600519.SH"}
    assert collector.fetch_timeout == 10.0


# ============================================================================
# 8. Safe Logging Test
# ============================================================================

def test_safe_logging(sample_archive_db, caplog):
    """Requirement C6: logging only includes symbol/date/counts/status/elapsed."""
    collector = SocialDataCollector(
        mode="active",
        archive_db=sample_archive_db,
    )

    with caplog.at_level(logging.INFO):
        collector.collect(symbol="688256.SH", as_of="2026-08-26")

    # Assert log message contains symbol and status
    log_text = caplog.text
    assert "688256.SH" in log_text
    assert "SocialDataCollector.collect" in log_text
    # Sensitive tokens/cookies must not be in log
    assert "cookie" not in log_text.lower()
    assert "token" not in log_text.lower()


# ============================================================================
# 9. Lookback Empty Window & Failure Ledger Tests (P2 Retro H1)
# ============================================================================

def test_collector_lookback_empty_window_produces_no_failure_ledger_entry(tmp_path):
    """When candidates exist in archive with snapshot_at <= cutoff but published_at is outside lookback window:

    Collector must return status='empty', bundle.status='empty', and data_failure_ledger must be empty (NO structural gap).
    """
    db_path = str(tmp_path / "lookback_collector_archive.db")
    conn = init_archive_db(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO social_ingest_runs (
            run_id, provider, platform, query_text, started_at, completed_at,
            status, crawler_commit, source_schema_fingerprint, rows_read, rows_inserted
        ) VALUES (
            'run_old', 'mediacrawler', 'xhs', '寒武纪', '2026-08-01T10:00:00Z', '2026-08-01T10:01:00Z',
            'success', 'd6f7c5bb906b6dac40ddf343ef9e26438a3de092', 'fp_001', 1, 1
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_old_1', 'xhs:post:note_old_1', 'social.raw_record.v1', 'post', 'xhs', 'note_old_1',
            'xhs:post:note_old_1', '2026-07-20T03:00:00Z', '2026-07-20T04:00:00Z', '2026-07-20T05:00:00Z', '2026-08-01T10:00:00Z',
            '寒武纪旧帖', '正文内容', '{"likes": 10}', 'c_hash_old', 'm_hash_old', 'run_old',
            'xhs_note', '1'
        )
        """
    )
    cursor.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) VALUES (?, ?, ?, ?, ?, ?)",
        ('snap_old_1', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
    )
    conn.commit()
    conn.close()

    collector = SocialDataCollector(
        mode="active",
        archive_db=db_path,
        lookback_days=7,
    )
    now_frozen = datetime(2026, 8, 29, 12, 0, 0, tzinfo=CN_TZ)

    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26", now=now_frozen)

    assert ctx["status"] == "empty"
    assert ctx["direction_allowed"] is False
    assert "social_empty" in ctx["reason_codes"]
    assert "observed_after_cutoff_excluded" not in ctx["reason_codes"]
    assert ctx["bundle"]["status"] == "empty"
    assert ctx["data_failure_ledger"] == []

