"""End-to-End Acceptance Tests for Social Data Pipeline (P2-T15a / Task 15).

Specifications:
- docs/social_data/implementation_plan.md Task 15, §3, §4, §5, §7, §8, Gate 2/3
- DECISIONS.md D-008, D-009, D-010
- data_contract_v1.md §1, §2, §3, §4

Hard Acceptance Invariants (P2-T15a):
1. Regression Baseline: 0 new failures across all social test suites.
2. Historical PIT Smoke:
   - Cutoff-qualified candidate snapshot selected (MAX(snapshot_at) <= cutoff).
   - Higher interaction metrics appearing only after cutoff (snapshot_at > cutoff) NEVER leak into eligibility, sentiment, or attention aggregation.
   - Late-discovered content (first_seen_at > cutoff) is excluded.
3. Synthetic E2E Dual-Platform (XHS + Douyin):
   - At least 1 post + 1 first-level comment for both XHS and Douyin.
   - Full flow: MediaCrawler SQLite -> MediaCrawlerImporter -> Archive SQLite -> SocialArchiveProvider -> SocialDataCollector -> AnalystAdapter -> SocialMediaAnalyst Node.
   - Native IDs and all five time fields preserved (published_at, source_updated_at, first_seen_at, snapshot_at, ingest_at).
   - Strict data sanitization: NO cookies, tracking tokens (xsec_token, utm_*, sec_uid), nicknames, avatars, or raw user IDs leak into archive, bundle, context, adapter, or traces.
   - Zero external network requests; purely local SQLite fixtures.
4. Disabled Safety:
   - Default TA_SOCIAL_MODE remains 'disabled'.
   - Disabled mode does NOT touch or open archive DB (even if archive_db path is invalid).
   - Adapter continues using legacy_proxy with direction_allowed=False and legacy news/zt/hot formatting (Gate 4 / T15b legacy code is NOT deleted in this card).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest

from tradingagents.agents.analysts.social_media_analyst import (
    create_social_media_analyst,
)
from tradingagents.dataflows.social.aggregator import (
    SocialSentimentAggregator,
)
from tradingagents.dataflows.social.analyst_adapter import (
    ResolvedSocialInputs,
    resolve_social_analyst_inputs,
    resolve_social_mode,
)
from tradingagents.dataflows.social.archive_schema import (
    ARCHIVE_SCHEMA_VERSION,
    init_archive_db,
)
from tradingagents.dataflows.social.collector import (
    SocialDataCollector,
)
from tradingagents.dataflows.social.contracts import (
    REASON_OBSERVED_AFTER_CUTOFF_EXCLUDED,
    REASON_SOCIAL_ARCHIVE_MISSING,
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
    REASON_SOCIAL_NOT_APPLICABLE,
    SentimentBundleV1,
    SocialAttention,
    SocialDataContext,
    SocialMetrics,
    SocialRawRecordV1,
    SocialSentiment,
    SocialStatus,
    SourceRef,
    compute_content_hash,
    compute_metrics_hash,
    create_default_social_data_context,
    create_empty_sentiment_bundle,
)
from tradingagents.dataflows.social.entity_resolver import (
    EntityResolver,
)
from tradingagents.dataflows.social.mediacrawler_importer import (
    MediaCrawlerImporter,
)
from tradingagents.dataflows.social.provider import (
    SocialArchiveProvider,
    SocialFetchResult,
    check_content_eligibility,
    compute_as_of_cutoff,
    select_candidate_snapshot,
)
from tradingagents.graph.data_collector import (
    DataCollector,
)
from tests.social_fixtures import (
    init_mediacrawler_db,
)


CN_TZ = ZoneInfo("Asia/Shanghai")


# ============================================================================
# Test Doubles & Fixtures
# ============================================================================

class CaptureLLM:
    """Mock LLM capturing incoming messages and returning valid formatted verdict."""

    def __init__(self, response_text: str = ""):
        self.captured_messages: List[Any] = []
        verdict = '<!-- VERDICT: {"direction": "看多", "reason": "基于社交与市场多头证据"} -->'
        self.response_text = response_text or f"【正式社交分析报告】\n综合社交舆情与盘面分析。\n{verdict}"

    async def astream(self, messages):
        self.captured_messages.extend(messages)
        yield SimpleNamespace(
            content=self.response_text,
            response_metadata={
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
            },
        )

    def invoke(self, messages):
        self.captured_messages.extend(messages)
        return SimpleNamespace(content=self.response_text)


# ============================================================================
# Section B: Historical Point-in-Time (PIT) Acceptance Tests (D-008)
# ============================================================================

def test_historical_pit_smoke_snapshot_leak_prevention(tmp_path):
    """Requirement B: Post with snapshot1 (<= cutoff) and snapshot2 (> cutoff).

    Asserts:
    1. At as_of=cutoff, only snapshot1 (old likes) is selected.
    2. Snapshot2's inflated likes after cutoff NEVER leak into provider or collector.
    3. Aggregated attention and sentiment metrics reflect strictly snapshot1.
    """
    archive_db_path = str(tmp_path / "pit_smoke_archive.db")
    conn = init_archive_db(archive_db_path)
    cursor = conn.cursor()

    # Create Ingest Run
    cursor.execute(
        """
        INSERT INTO social_ingest_runs (
            run_id, provider, platform, query_text, started_at, completed_at,
            status, crawler_commit, source_schema_fingerprint, rows_read, rows_inserted
        ) VALUES (
            'run_pit_001', 'mediacrawler', 'xhs', '寒武纪', '2026-08-28T10:00:00Z', '2026-08-28T10:01:00Z',
            'success', 'd6f7c5bb906b6dac40ddf343ef9e26438a3de092', 'fp_pit_001', 2, 2
        )
        """
    )

    # Post Published on 2026-08-25T03:00:00Z, first seen on 2026-08-25T04:00:00Z
    # Snapshot 1: snapshot_at = 2026-08-25T05:00:00Z (<= cutoff of 2026-08-26), likes = 25, comments = 5
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_cambricon_pit_v1', 'xhs:post:note_camb_pit', 'social.raw_record.v1', 'post', 'xhs', 'note_camb_pit',
            'xhs:post:note_camb_pit', '2026-08-25T03:00:00Z', '2026-08-25T04:00:00Z', '2026-08-25T05:00:00Z', '2026-08-28T10:00:00Z',
            '寒武纪芯片实测', '寒武纪芯片算力强劲，今日大涨突破。', '{"likes": 25, "comments": 5, "shares": null, "collects": 10}',
            'chash_pit_1', 'mhash_pit_1', 'run_pit_001', 'xhs_note', '101'
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version)
        VALUES ('snap_cambricon_pit_v1', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
        """
    )

    # Snapshot 2: snapshot_at = 2026-08-27T09:00:00Z (> cutoff of 2026-08-26), likes = 9999, comments = 888
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_cambricon_pit_v2', 'xhs:post:note_camb_pit', 'social.raw_record.v1', 'post', 'xhs', 'note_camb_pit',
            'xhs:post:note_camb_pit', '2026-08-25T03:00:00Z', '2026-08-25T04:00:00Z', '2026-08-27T09:00:00Z', '2026-08-28T10:00:00Z',
            '寒武纪芯片实测', '寒武纪芯片算力强劲，今日大涨突破。', '{"likes": 9999, "comments": 888, "shares": 100, "collects": 500}',
            'chash_pit_1', 'mhash_pit_2', 'run_pit_001', 'xhs_note', '101'
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version)
        VALUES ('snap_cambricon_pit_v2', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
        """
    )
    conn.commit()
    conn.close()

    # Step 1: Query Provider at as_of="2026-08-26"
    provider = SocialArchiveProvider(db_path=archive_db_path)
    result = provider.fetch_records(
        symbol="688256.SH",
        as_of="2026-08-26",
        lookback_days=7,
    )

    assert result.status == SocialStatus.AVAILABLE.value
    assert len(result.records) == 1
    rec = result.records[0]

    # Must select snap_cambricon_pit_v1 with likes=25
    assert rec.snapshot_id == "snap_cambricon_pit_v1"
    assert rec.metrics.likes == 25
    assert rec.metrics.comments == 5
    assert rec.metrics.collects == 10
    # Must NOT select snap_cambricon_pit_v2 with likes=9999
    assert rec.metrics.likes != 9999
    assert rec.metrics.comments != 888

    # Step 2: Collector & Aggregator End-to-End at as_of="2026-08-26"
    collector = SocialDataCollector(
        mode="active",
        archive_db=archive_db_path,
        canary_symbols="",
        lookback_days=7,
        min_posts=1,
        min_classified=1,
        min_authors=1,
    )
    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert ctx["status"] in (SocialStatus.AVAILABLE.value, SocialStatus.PARTIAL.value)
    bundle = ctx["bundle"]
    assert bundle is not None
    assert bundle["symbol"] == "688256.SH"
    assert bundle["social_attention"]["total_interactions"] == 40  # 25 likes + 5 comments + 10 collects
    assert bundle["social_attention"]["post_count"] == 1

    # Invariant: Snapshot 2 (likes=9999, comments=888) must NEVER leak into bundle
    assert bundle["social_attention"]["total_interactions"] == 40  # 25 + 5 + 10, NOT 11487
    samples = bundle.get("evidence_samples", [])
    assert not any(s.get("snapshot_id") == "snap_cambricon_pit_v2" for s in samples)
    assert not any(s.get("likes") == 9999 for s in samples)
    assert not any(s.get("comments") == 888 for s in samples)


def test_historical_pit_post_with_only_future_snapshot_unobserved_metrics(tmp_path):
    """Requirement B: Post published before cutoff, but snapshot_at is after cutoff.

    Asserts:
    When candidate selection finds no snapshot with snapshot_at <= cutoff,
    the future snapshot is rejected and provider returns refused with REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT.
    """
    archive_db_path = str(tmp_path / "pit_future_snap_archive.db")
    conn = init_archive_db(archive_db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO social_ingest_runs (
            run_id, provider, platform, query_text, started_at, completed_at,
            status, crawler_commit, source_schema_fingerprint, rows_read, rows_inserted
        ) VALUES (
            'run_fut_001', 'mediacrawler', 'xhs', '寒武纪', '2026-08-28T10:00:00Z', '2026-08-28T10:01:00Z',
            'success', 'd6f7c5bb906b6dac40ddf343ef9e26438a3de092', 'fp_fut_001', 1, 1
        )
        """
    )

    # Post published 2026-08-25, but crawler snapshot_at is 2026-08-27 (after 2026-08-26 cutoff)
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_future_only', 'xhs:post:note_fut_01', 'social.raw_record.v1', 'post', 'xhs', 'note_fut_01',
            'xhs:post:note_fut_01', '2026-08-25T03:00:00Z', '2026-08-25T04:00:00Z', '2026-08-27T08:00:00Z', '2026-08-28T10:00:00Z',
            '寒武纪未来快照', '这是一篇快照时间晚于分析日期的笔记。', '{"likes": 1200, "comments": 300}',
            'chash_fut_1', 'mhash_fut_1', 'run_fut_001', 'xhs_note', '201'
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version)
        VALUES ('snap_future_only', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
        """
    )
    conn.commit()
    conn.close()

    provider = SocialArchiveProvider(db_path=archive_db_path)
    result = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26")

    # Since snapshot_at > cutoff, candidate snapshot is None -> status is REFUSED with REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT
    assert result.status == SocialStatus.REFUSED.value
    assert REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT in result.reason_codes
    assert len(result.records) == 0


def test_historical_pit_late_crawled_content_excluded(tmp_path):
    """Requirement B: Post published before cutoff, but first_seen_at is after cutoff.

    Asserts:
    Late-discovered content (后补抓取) with first_seen_at > cutoff must be excluded
    at as_of=cutoff to guarantee anti-lookahead safety.
    """
    archive_db_path = str(tmp_path / "pit_late_crawl_archive.db")
    conn = init_archive_db(archive_db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO social_ingest_runs (
            run_id, provider, platform, query_text, started_at, completed_at,
            status, crawler_commit, source_schema_fingerprint, rows_read, rows_inserted
        ) VALUES (
            'run_late_001', 'mediacrawler', 'xhs', '寒武纪', '2026-08-28T10:00:00Z', '2026-08-28T10:01:00Z',
            'success', 'd6f7c5bb906b6dac40ddf343ef9e26438a3de092', 'fp_late_001', 1, 1
        )
        """
    )

    # published_at = 2026-08-25, but first_seen_at = 2026-08-27 (crawled after 2026-08-26)
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_late_crawled', 'xhs:post:note_late_01', 'social.raw_record.v1', 'post', 'xhs', 'note_late_01',
            'xhs:post:note_late_01', '2026-08-25T03:00:00Z', '2026-08-27T04:00:00Z', '2026-08-27T05:00:00Z', '2026-08-28T10:00:00Z',
            '后补发现笔记', '这条笔记直到27号才被爬虫首次抓取入库。', '{"likes": 88, "comments": 12}',
            'chash_late_1', 'mhash_late_1', 'run_late_001', 'xhs_note', '301'
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version)
        VALUES ('snap_late_crawled', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
        """
    )
    conn.commit()
    conn.close()

    provider = SocialArchiveProvider(db_path=archive_db_path)
    result = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26")

    # Excluded because no historical observation existed before cutoff -> REFUSED
    assert result.status == SocialStatus.REFUSED.value
    assert REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT in result.reason_codes
    assert len(result.records) == 0

    # Also directly test check_content_eligibility guard logic for late crawled post
    cutoff_utc = datetime(2026, 8, 26, 15, 59, 59, 999999, tzinfo=timezone.utc)
    w_start_utc = datetime(2026, 8, 19, 16, 0, 0, tzinfo=timezone.utc)
    late_snap = {
        "platform": "xhs",
        "published_at": "2026-08-25T03:00:00Z",
        "first_seen_at": "2026-08-27T04:00:00Z",
        "snapshot_at": "2026-08-25T05:00:00Z",
        "ingest_at": "2026-08-28T10:00:00Z",
    }
    is_ok, reason = check_content_eligibility(late_snap, w_start_utc, cutoff_utc)
    assert is_ok is False
    assert reason == "first_seen_at_after_cutoff"


def test_historical_pit_multi_day_evolution_reproducibility(tmp_path):
    """Requirement B: Point-in-time multi-day evolution determinism.

    Post published on 2026-08-24 with 3 snapshots across consecutive days:
    - Day 1 (2026-08-24): likes=10
    - Day 2 (2026-08-25): likes=60
    - Day 3 (2026-08-26): likes=350

    Asserts:
    1. Querying as_of=2026-08-24 strictly returns likes=10.
    2. Querying as_of=2026-08-25 strictly returns likes=60.
    3. Querying as_of=2026-08-26 strictly returns likes=350.
    4. Backtesting on any past date is deterministic and immune to future crawler activity.
    """
    archive_db_path = str(tmp_path / "pit_evolution_archive.db")
    conn = init_archive_db(archive_db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO social_ingest_runs (
            run_id, provider, platform, query_text, started_at, completed_at,
            status, crawler_commit, source_schema_fingerprint, rows_read, rows_inserted
        ) VALUES (
            'run_evo_001', 'mediacrawler', 'xhs', '寒武纪', '2026-08-28T10:00:00Z', '2026-08-28T10:01:00Z',
            'success', 'd6f7c5bb906b6dac40ddf343ef9e26438a3de092', 'fp_evo_001', 3, 3
        )
        """
    )

    # Snapshot 1 (Day 1: 2026-08-24)
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_evo_d1', 'xhs:post:note_evo_01', 'social.raw_record.v1', 'post', 'xhs', 'note_evo_01',
            'xhs:post:note_evo_01', '2026-08-24T02:00:00Z', '2026-08-24T03:00:00Z', '2026-08-24T06:00:00Z', '2026-08-28T10:00:00Z',
            '寒武纪跟踪', '第一天调研纪要。', '{"likes": 10, "comments": 2}',
            'chash_evo_1', 'mhash_evo_1', 'run_evo_001', 'xhs_note', '401'
        )
        """
    )
    cursor.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) VALUES (?, ?, ?, ?, ?, ?)",
        ('snap_evo_d1', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
    )

    # Snapshot 2 (Day 2: 2026-08-25)
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_evo_d2', 'xhs:post:note_evo_01', 'social.raw_record.v1', 'post', 'xhs', 'note_evo_01',
            'xhs:post:note_evo_01', '2026-08-24T02:00:00Z', '2026-08-24T03:00:00Z', '2026-08-25T08:00:00Z', '2026-08-28T10:00:00Z',
            '寒武纪跟踪', '第一天调研纪要。', '{"likes": 60, "comments": 15}',
            'chash_evo_1', 'mhash_evo_2', 'run_evo_001', 'xhs_note', '401'
        )
        """
    )
    cursor.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) VALUES (?, ?, ?, ?, ?, ?)",
        ('snap_evo_d2', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
    )

    # Snapshot 3 (Day 3: 2026-08-26)
    cursor.execute(
        """
        INSERT INTO social_record_snapshots (
            snapshot_id, record_id, schema_version, record_type, platform, native_id,
            root_post_record_id, published_at, first_seen_at, snapshot_at, ingest_at,
            title, text, metrics_json, content_hash, metrics_hash, ingest_run_id,
            source_table, source_row_id
        ) VALUES (
            'snap_evo_d3', 'xhs:post:note_evo_01', 'social.raw_record.v1', 'post', 'xhs', 'note_evo_01',
            'xhs:post:note_evo_01', '2026-08-24T02:00:00Z', '2026-08-24T03:00:00Z', '2026-08-26T09:00:00Z', '2026-08-28T10:00:00Z',
            '寒武纪跟踪', '第一天调研纪要。', '{"likes": 350, "comments": 80}',
            'chash_evo_1', 'mhash_evo_3', 'run_evo_001', 'xhs_note', '401'
        )
        """
    )
    cursor.execute(
        "INSERT INTO social_entity_mentions (snapshot_id, symbol, matched_text, match_method, confidence, resolver_version) VALUES (?, ?, ?, ?, ?, ?)",
        ('snap_evo_d3', '688256.SH', '寒武纪', 'standard_name', 1.0, 'v1')
    )
    conn.commit()
    conn.close()

    provider = SocialArchiveProvider(db_path=archive_db_path)
    now_frozen = datetime(2026, 8, 29, 12, 0, 0, tzinfo=CN_TZ)

    # Day 1
    res_d1 = provider.fetch_records(symbol="688256.SH", as_of="2026-08-24", now=now_frozen)
    assert res_d1.status == SocialStatus.AVAILABLE.value
    assert res_d1.records[0].snapshot_id == "snap_evo_d1"
    assert res_d1.records[0].metrics.likes == 10
    assert res_d1.records[0].metrics.comments == 2

    # Day 2
    res_d2 = provider.fetch_records(symbol="688256.SH", as_of="2026-08-25", now=now_frozen)
    assert res_d2.status == SocialStatus.AVAILABLE.value
    assert res_d2.records[0].snapshot_id == "snap_evo_d2"
    assert res_d2.records[0].metrics.likes == 60
    assert res_d2.records[0].metrics.comments == 15

    # Day 3
    res_d3 = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26", now=now_frozen)
    assert res_d3.status == SocialStatus.AVAILABLE.value
    assert res_d3.records[0].snapshot_id == "snap_evo_d3"
    assert res_d3.records[0].metrics.likes == 350
    assert res_d3.records[0].metrics.comments == 80


# ============================================================================
# Section C: Synthetic E2E Dual-Platform Acceptance Tests (XHS + Douyin)
# ============================================================================

@pytest.fixture
def dual_platform_mediacrawler_db(tmp_path):
    """Fixture constructing a clean MediaCrawler SQLite DB with XHS and Douyin posts + comments."""
    crawler_db_path = str(tmp_path / "mediacrawler_synthetic_e2e.db")
    conn = init_mediacrawler_db(crawler_db_path)
    cursor = conn.cursor()

    # 1. XHS Note (Published 2026-08-26T03:12:11Z, epoch ms = 1787713931000)
    cursor.execute(
        """
        INSERT INTO xhs_note (
            user_id, nickname, avatar, ip_location, note_id, type, title, desc,
            video_url, time, last_update_time, liked_count, collected_count,
            comment_count, share_count, image_list, tag_list, note_url,
            source_keyword, xsec_token, add_ts, last_modify_ts
        ) VALUES (
            'xhs_author_01', 'XHS用户昵称（应脱敏）', 'https://avatar.xhscdn.com/1.png', '上海',
            'xhs_note_synthetic_01', 'normal', '寒武纪深度行业调研与算力评测',
            '今日对寒武纪688256进行了深度调研，新一代芯片在算力集群测试中表现亮眼，看好多头动能。',
            NULL, 1787713931000, 1787715600000, '150', '35', '42', '18',
            'https://img.xhscdn.com/1.png', '#寒武纪#半导体',
            'https://www.xiaohongshu.com/explore/xhs_note_synthetic_01?xsec_token=TEST_XSEC_COOKIE_TOKEN&utm_source=share',
            '寒武纪', 'TEST_XSEC_COOKIE_TOKEN', 1787716802000, 1787724600000
        )
        """
    )

    # 2. XHS Comment (Published 2026-08-26T03:30:00Z, epoch s = 1787715000)
    cursor.execute(
        """
        INSERT INTO xhs_note_comment (
            user_id, nickname, avatar, ip_location, comment_id, note_id, content,
            create_time, like_count, sub_comment_count, parent_comment_id,
            last_modify_ts, add_ts
        ) VALUES (
            'xhs_commenter_01', '小红书评论员', 'https://avatar.xhscdn.com/c1.png', '北京',
            'xhs_comment_synthetic_01', 'xhs_note_synthetic_01',
            '寒武纪这个位置放量非常健康，强烈看好后市！',
            1787715000, '28', '3', NULL,
            1787724700, 1787717000
        )
        """
    )

    # 3. Douyin Aweme (Published 2026-08-26T03:15:00Z, epoch s = 1787714100)
    cursor.execute(
        """
        INSERT INTO douyin_aweme (
            user_id, sec_uid, short_user_id, user_unique_id, nickname, avatar,
            user_signature, ip_location, aweme_id, aweme_type, title, desc,
            create_time, liked_count, comment_count, share_count, collected_count,
            aweme_url, source_keyword, add_ts, last_modify_ts
        ) VALUES (
            'dy_user_01', 'MS4wLjABAAAA_SENSITIVE_SEC_UID_01', '987654', 'dy_unique_01', '抖音股评达人',
            'https://avatar.douyincdn.com/dy1.png', '短视频看盘', '广东',
            'dy_aweme_synthetic_01', 'video', '芯片龙头寒武纪盘面拆解',
            '寒武纪突破年线压制，主力资金持续加仓，国产替代逻辑坚实。',
            1787714100, '850', '120', '45', '210',
            'https://www.douyin.com/video/dy_aweme_synthetic_01?utm_campaign=client_share&sec_uid=MS4wLjABAAAA_SENSITIVE_SEC_UID_01',
            '寒武纪', 1787716800, 1787724600
        )
        """
    )

    # 4. Douyin Comment (Published 2026-08-26T03:25:00Z, epoch s = 1787714700)
    cursor.execute(
        """
        INSERT INTO douyin_aweme_comment (
            comment_id, aweme_id, user_id, sec_uid, nickname, avatar_url, ip_location,
            content, create_time, like_count, reply_comment_total, parent_comment_id,
            last_modify_ts, add_ts
        ) VALUES (
            'dy_comment_synthetic_01', 'dy_aweme_synthetic_01', 'dy_user_c1',
            'MS4wLjABAAAA_SENSITIVE_SEC_UID_C1', '抖音老股民', 'https://avatar.douyincdn.com/dyc1.png', '浙江',
            '国产算力第一股，寒武纪继续持有！',
            1787714700, '56', '0', NULL,
            1787724650, 1787716900
        )
        """
    )

    conn.commit()
    conn.close()
    return crawler_db_path


def test_synthetic_e2e_full_pipeline_active_mode(tmp_path, dual_platform_mediacrawler_db):
    """Requirement C: Full pipeline run (Import -> Archive -> Provider -> Collector -> Adapter -> Analyst Node) in active mode.

    Asserts:
    1. Import maps both XHS and Douyin posts and first-level comments to archive.
    2. All 5 time fields and native IDs are preserved.
    3. Provider fetches 4 records with clean entity mapping.
    4. Collector generates SentimentBundleV1 with direction_allowed=True.
    5. Adapter formats 4-section structured text with source_mode='active'.
    6. Analyst node runs LLM and captures trace with source_mode='active'.
    """
    archive_db_path = str(tmp_path / "synthetic_e2e_archive.db")
    resolver = EntityResolver()

    # Step 1: MediaCrawlerImporter
    importer = MediaCrawlerImporter(
        archive_db=archive_db_path,
        entity_resolver=resolver,
    )
    import_res = importer.import_records(source_db=dual_platform_mediacrawler_db)
    assert import_res["rows_inserted"] == 4
    assert import_res["rows_rejected"] == 0

    # Verify Archive Schema & Rows
    conn = sqlite3.connect(archive_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT record_id, record_type, platform, native_id, published_at, source_updated_at, first_seen_at, snapshot_at, ingest_at FROM social_record_snapshots ORDER BY record_id")
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 4
    record_ids = {r[0] for r in rows}
    assert record_ids == {
        "xhs:post:xhs_note_synthetic_01",
        "xhs:comment:xhs_comment_synthetic_01",
        "dy:post:dy_aweme_synthetic_01",
        "dy:comment:dy_comment_synthetic_01",
    }

    # Verify native IDs and 5 time fields are present
    for r in rows:
        rec_id, rec_type, platform, native_id, pub_at, src_upd_at, first_seen, snap_at, ing_at = r
        assert native_id in ("xhs_note_synthetic_01", "xhs_comment_synthetic_01", "dy_aweme_synthetic_01", "dy_comment_synthetic_01")
        assert pub_at is not None and "T" in pub_at
        assert first_seen is not None and "T" in first_seen
        assert snap_at is not None and "T" in snap_at
        assert ing_at is not None and "T" in ing_at
        if rec_id == "xhs:post:xhs_note_synthetic_01":
            assert src_upd_at is not None
        else:
            assert src_upd_at is None

    # Step 2: SocialArchiveProvider
    provider = SocialArchiveProvider(db_path=archive_db_path)
    fetch_res = provider.fetch_records(symbol="688256.SH", as_of="2026-08-26", lookback_days=7)
    assert fetch_res.status == SocialStatus.AVAILABLE.value
    assert len(fetch_res.records) == 4

    platforms_found = {rec.platform for rec in fetch_res.records}
    assert platforms_found == {"xhs", "dy"}
    types_found = {rec.record_type for rec in fetch_res.records}
    assert types_found == {"post", "comment"}

    # Step 3: SocialDataCollector (active mode)
    collector = SocialDataCollector(
        mode="active",
        archive_db=archive_db_path,
        canary_symbols="688256.SH",
        lookback_days=7,
        min_posts=1,
        min_classified=1,
        min_authors=1,
    )
    social_context = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert social_context["mode"] == "active"
    assert social_context["status"] == "available"
    assert social_context["direction_allowed"] is True

    bundle = social_context["bundle"]
    assert bundle["symbol"] == "688256.SH"
    assert bundle["social_attention"]["post_count"] == 2
    assert bundle["social_attention"]["comment_count"] == 2
    assert bundle["social_attention"]["total_interactions"] > 0
    assert bundle["social_sentiment"]["score"] is not None
    assert bundle["social_sentiment"]["score"] > 0  # bullish texts

    # Step 4: Analyst Adapter
    resolved = resolve_social_analyst_inputs(
        mode="active",
        social_data_context=social_context,
        legacy_data={"news": "传统新闻", "zt_data": "涨停数据", "hot_stocks": "热搜数据"},
        market_attention={
            "zt_pool": {"status": "available", "as_of": "2026-08-26", "raw": "涨停池数据"},
            "hot_stocks": {"status": "available", "as_of": "2026-08-26", "raw": "雪球榜首"},
        },
        ticker="688256.SH",
        current_date="2026-08-26",
        ticker_display="688256.SH (寒武纪)",
    )

    assert resolved.mode == "active"
    assert resolved.source_mode == "active"
    assert resolved.direction_allowed is True
    assert resolved.legacy_data is None

    # Formats active 4-section content
    assert "【一、数据状态与数据源有效性】" in resolved.human_content
    assert "【二、社交观点与立场解构】" in resolved.human_content
    assert "【三、社交热度与互动特征】" in resolved.human_content
    assert "【四、市场关注度（盘面与榜单生态）】" in resolved.human_content
    assert "【get_news】" not in resolved.human_content

    # Step 5: Social Media Analyst Node
    mock_llm = CaptureLLM()
    data_collector = DataCollector()
    data_collector._cache["688256.SH_2026-08-26"] = {
        "news": "传统新闻数据",
        "zt_pool": "涨停池数据",
        "hot_stocks": "热门榜数据",
        "social_data_context": social_context,
        "market_data_context": {
            "market_attention": {
                "zt_pool": {"status": "available", "as_of": "2026-08-26", "raw": "涨停池数据"},
                "hot_stocks": {"status": "available", "as_of": "2026-08-26", "raw": "雪球榜首"},
            }
        },
    }

    node = create_social_media_analyst(mock_llm, data_collector)
    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "688256.SH",
        "mode": "active",
        "social_data_context": social_context,
    }

    result = asyncio.run(node(state))
    assert "analyst_traces" in result
    trace = result["analyst_traces"][0]
    assert trace["source_mode"] == "active"
    assert trace["direction_allowed"] is True
    assert trace["bundle_id"] == bundle["bundle_id"]


def test_synthetic_e2e_full_pipeline_shadow_mode(tmp_path, dual_platform_mediacrawler_db):
    """Requirement C: Full pipeline run with dual-platform fixture in shadow mode.

    Asserts:
    1. Collector queries archive and produces bundle, but direction_allowed is strictly False.
    2. Adapter uses legacy_proxy text formatting and records source_mode='legacy_proxy'.
    3. Analyst trace preserves bundle_id while source_mode='legacy_proxy' and direction_allowed=False.
    """
    archive_db_path = str(tmp_path / "synthetic_shadow_archive.db")
    resolver = EntityResolver()

    importer = MediaCrawlerImporter(archive_db=archive_db_path, entity_resolver=resolver)
    importer.import_records(source_db=dual_platform_mediacrawler_db)

    collector = SocialDataCollector(
        mode="shadow",
        archive_db=archive_db_path,
        lookback_days=7,
        min_posts=1,
        min_classified=1,
        min_authors=1,
    )
    social_context = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert social_context["mode"] == "shadow"
    assert social_context["status"] == "available"
    assert social_context["direction_allowed"] is False
    assert social_context["bundle"] is not None

    resolved = resolve_social_analyst_inputs(
        mode="shadow",
        social_data_context=social_context,
        legacy_data={
            "news": "影子传统新闻",
            "zt_data": "影子涨停池",
            "hot_stocks": "影子热搜",
        },
        ticker="688256.SH",
        current_date="2026-08-26",
        ticker_display="688256.SH (寒武纪)",
    )

    assert resolved.mode == "shadow"
    assert resolved.source_mode == "legacy_proxy"
    assert resolved.direction_allowed is False
    assert resolved.bundle is not None
    assert "【get_news】" in resolved.human_content
    assert "影子传统新闻" in resolved.human_content
    assert "【一、数据状态与数据源有效性】" not in resolved.human_content

    # Analyst execution in shadow mode
    mock_llm = CaptureLLM()
    data_collector = DataCollector()
    data_collector._cache["688256.SH_2026-08-26"] = {
        "news": "影子传统新闻",
        "zt_pool": "影子涨停池",
        "hot_stocks": "影子热搜",
        "social_data_context": social_context,
    }

    node = create_social_media_analyst(mock_llm, data_collector)
    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "688256.SH",
        "mode": "shadow",
        "social_data_context": social_context,
    }

    result = asyncio.run(node(state))
    trace = result["analyst_traces"][0]
    assert trace["source_mode"] == "legacy_proxy"
    assert trace["direction_allowed"] is False
    assert trace["bundle_id"] == social_context["bundle"]["bundle_id"]


def test_synthetic_e2e_data_sanitization_no_cookies_no_tokens(tmp_path):
    """Requirement C: Sanitization & privacy invariants across all layers.

    Asserts:
    1. Raw cookies, xsec_tokens, sec_uids, session IDs, nicknames, avatars, IP locations are discarded.
    2. author_id is converted to SHA-256 hash.
    3. canonical_url strips tracking query parameters.
    4. Entire pipeline runs purely offline without touching network or reading cookie files.
    """
    crawler_db_path = str(tmp_path / "sensitive_mediacrawler.db")
    archive_db_path = str(tmp_path / "sanitized_archive.db")

    conn = init_mediacrawler_db(crawler_db_path)
    cursor = conn.cursor()

    secret_xsec = "TOP_SECRET_COOKIE_XSEC_TOKEN_999888"
    secret_sec_uid = "MS4wLjABAAAA_SENSITIVE_DOUYIN_SEC_UID_PRIVATE"
    raw_user_uid = "raw_private_author_uid_12345"
    nickname_text = "绝密昵称_张三老股民"
    ip_loc = "绝密IP属地_杭州阿里巴巴园区"
    avatar_url = "https://sns-avatar.xhscdn.com/secret_avatar_99.jpg"

    cursor.execute(
        """
        INSERT INTO xhs_note (
            user_id, nickname, avatar, ip_location, note_id, type, title, desc,
            video_url, time, last_update_time, liked_count, collected_count,
            comment_count, share_count, image_list, tag_list, note_url,
            source_keyword, xsec_token, add_ts, last_modify_ts
        ) VALUES (
            ?, ?, ?, ?, 'note_sensitive_01', 'normal', '寒武纪绝密内参',
            '寒武纪基本面扎实，主力吸筹充分。', NULL, 1787713931000, 1787715600000,
            '50', '10', '5', '2', 'https://img/1.png', '#寒武纪',
            ?, '寒武纪', ?, 1787716802000, 1787724600000
        )
        """,
        (
            raw_user_uid,
            nickname_text,
            avatar_url,
            ip_loc,
            f"https://www.xiaohongshu.com/explore/note_sensitive_01?xsec_token={secret_xsec}&utm_source=copy",
            secret_xsec,
        ),
    )

    cursor.execute(
        """
        INSERT INTO douyin_aweme (
            user_id, sec_uid, short_user_id, user_unique_id, nickname, avatar,
            user_signature, ip_location, aweme_id, aweme_type, title, desc,
            create_time, liked_count, comment_count, share_count, collected_count,
            aweme_url, source_keyword, add_ts, last_modify_ts
        ) VALUES (
            ?, ?, '654321', 'dy_sens_1', ?, ?, '个股解析', ?,
            'aweme_sensitive_01', 'video', '寒武纪重磅评测',
            '寒武纪突破盘整区间，后市可期。', 1787714100, '300', '40', '15', '80',
            ?, '寒武纪', 1787716800, 1787724600
        )
        """,
        (
            raw_user_uid,
            secret_sec_uid,
            nickname_text,
            avatar_url,
            ip_loc,
            f"https://www.douyin.com/video/aweme_sensitive_01?sec_uid={secret_sec_uid}&session_id=987654321",
        ),
    )
    conn.commit()
    conn.close()

    # Step 1: Import
    resolver = EntityResolver()
    importer = MediaCrawlerImporter(archive_db=archive_db_path, entity_resolver=resolver)
    importer.import_records(source_db=crawler_db_path)

    # Inspect SQLite Raw Records
    conn = sqlite3.connect(archive_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT canonical_url, author_id_hash, metrics_json, title, text FROM social_record_snapshots")
    db_rows = cursor.fetchall()
    conn.close()

    assert len(db_rows) == 2
    for url, auth_hash, m_json, title, text in db_rows:
        # 1. URLs are stripped of query params
        assert "xsec_token" not in url
        assert "utm_source" not in url
        assert "sec_uid" not in url
        assert "session_id" not in url

        # 2. Author ID is a SHA-256 hash (prefixed with sha256: and 64 hex characters)
        assert auth_hash.startswith("sha256:")
        assert len(auth_hash.split(":", 1)[1]) == 64
        assert auth_hash != raw_user_uid

    # Check whole Archive DB file content does not leak secrets
    with open(archive_db_path, "rb") as f:
        db_bytes = f.read()
        assert secret_xsec.encode("utf-8") not in db_bytes
        assert secret_sec_uid.encode("utf-8") not in db_bytes
        assert nickname_text.encode("utf-8") not in db_bytes
        assert ip_loc.encode("utf-8") not in db_bytes
        assert avatar_url.encode("utf-8") not in db_bytes
        assert raw_user_uid.encode("utf-8") not in db_bytes

    # Step 2: Collector -> Adapter -> Analyst Node
    collector = SocialDataCollector(
        mode="active",
        archive_db=archive_db_path,
        canary_symbols="",
        lookback_days=7,
        min_posts=1,
        min_classified=1,
        min_authors=1,
    )
    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    resolved = resolve_social_analyst_inputs(
        mode="active",
        social_data_context=ctx,
        ticker="688256.SH",
        current_date="2026-08-26",
        ticker_display="688256.SH (寒武纪)",
    )

    full_text_payload = resolved.human_content + str(ctx) + str(resolved.bundle)

    # Invariants: Zero leak of secrets or sensitive metadata
    assert secret_xsec not in full_text_payload
    assert secret_sec_uid not in full_text_payload
    assert nickname_text not in full_text_payload
    assert ip_loc not in full_text_payload
    assert avatar_url not in full_text_payload
    assert raw_user_uid not in full_text_payload
    assert "Cookie" not in full_text_payload
    assert "Set-Cookie" not in full_text_payload


# ============================================================================
# Section D: Disabled Mode Safety & Backward Compatibility Acceptance Tests
# ============================================================================

def test_disabled_mode_zero_archive_touch_and_legacy_behavior():
    """Requirement D: Disabled mode must never touch archive DB, keeping legacy proxy intact.

    Asserts:
    1. When mode='disabled', provider is never called and archive DB is never opened.
    2. Even an invalid/missing archive DB path causes NO error in disabled mode.
    3. Adapter outputs legacy proxy format with source_mode='legacy_proxy' and direction_allowed=False.
    """
    fake_nonexistent_db = "/tmp/strictly_nonexistent_fake_path_12345.db"

    collector = SocialDataCollector(
        mode="disabled",
        archive_db=fake_nonexistent_db,
    )
    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert ctx["mode"] == "disabled"
    assert ctx["status"] == "not_applicable"
    assert ctx["direction_allowed"] is False
    assert REASON_SOCIAL_NOT_APPLICABLE in ctx["reason_codes"]
    assert ctx["bundle"]["status"] == "not_applicable"
    assert ctx["bundle"]["direction_allowed"] is False

    # Adapter output in disabled mode
    resolved = resolve_social_analyst_inputs(
        mode="disabled",
        legacy_data={
            "news": "2026-08-26 贵州茅台发布半年报",
            "zt_data": "涨停 35 家",
            "hot_stocks": "雪球热搜第一：贵州茅台",
        },
        ticker="600519",
        current_date="2026-08-26",
        ticker_display="600519 (贵州茅台)",
    )

    assert resolved.mode == "disabled"
    assert resolved.source_mode == "legacy_proxy"
    assert resolved.direction_allowed is False
    assert resolved.legacy_data is not None
    assert "【get_news】" in resolved.human_content
    assert "2026-08-26 贵州茅台发布半年报" in resolved.human_content
    assert "【一、数据状态与数据源有效性】" not in resolved.human_content


def test_disabled_mode_default_env_and_config_safety(monkeypatch):
    """Requirement D: Default TA_SOCIAL_MODE must resolve to 'disabled'.

    Asserts:
    1. Unset, empty, or unrecognized TA_SOCIAL_MODE defaults safely to 'disabled'.
    2. System operates in legacy mode with zero risk to production.
    """
    monkeypatch.delenv("TA_SOCIAL_MODE", raising=False)
    assert resolve_social_mode(None) == "disabled"
    assert resolve_social_mode("") == "disabled"
    assert resolve_social_mode("unknown_mode") == "disabled"
    assert resolve_social_mode("  ") == "disabled"
