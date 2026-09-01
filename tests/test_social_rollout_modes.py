"""Unit and integration tests for P2-T14 & P2-T15b (Gate 4): shadow and canary rollout guards and legacy proxy removal.

Verifies the rollout mode boundaries:
1. disabled: does NOT call provider or open archive DB; returns not_applicable with source_mode='disabled' and direction_allowed=False, without news fallback.
2. shadow: collector queries archive and populates bundle; adapter formats 4-section structured text with direction_allowed=False; source_mode='shadow'.
3. active + canary whitelist:
   - Canary hit: allowed into active archive collection path.
   - Canary miss: falls back to non-active (status=not_applicable, mode=disabled, direction_allowed=False); provider never called; never silently active.
   - Canary empty: all valid A-share symbols allowed into active collection.
4. active insufficient / empty / failed: adapter produces explicit gap notices without falling back to news/zt/hot; direction_allowed=False.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
from tradingagents.dataflows.social.analyst_adapter import (
    ResolvedSocialInputs,
    resolve_social_analyst_inputs,
    resolve_social_mode,
)
from tradingagents.dataflows.social.archive_schema import init_archive_db
from tradingagents.dataflows.social.collector import (
    SocialDataCollector,
    build_social_failure_ledger,
)
from tradingagents.dataflows.social.contracts import (
    REASON_SOCIAL_ARCHIVE_MISSING,
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_INSUFFICIENT_COVERAGE,
    REASON_SOCIAL_NOT_APPLICABLE,
    SentimentBundleV1,
    SocialAttention,
    SocialDataContext,
    SocialMetrics,
    SocialRawRecordV1,
    SocialSentiment,
    SocialStatus,
    create_default_social_data_context,
    create_empty_sentiment_bundle,
)
from tradingagents.dataflows.social.entity_resolver import EntityResolver
from tradingagents.dataflows.social.mediacrawler_importer import MediaCrawlerImporter
from tradingagents.dataflows.social.provider import SocialFetchResult
from tradingagents.graph.data_collector import DataCollector
from tests.social_fixtures import (
    init_mediacrawler_db,
    populate_sample_mediacrawler_data,
)

CN_TZ = ZoneInfo("Asia/Shanghai")


# ============================================================================
# Helpers & Mocks
# ============================================================================

class SpySocialProvider:
    """Spy provider to track calls and verify provider isolation."""

    name: str = "archive_sqlite"

    def __init__(self, return_result: Optional[SocialFetchResult] = None):
        self.call_count = 0
        self.last_call_args: Dict[str, Any] = {}
        self.return_result = return_result

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
        }
        if self.return_result is not None:
            return self.return_result
        return SocialFetchResult(
            status=SocialStatus.EMPTY.value,
            requested_as_of=as_of,
            reason_codes=[REASON_SOCIAL_EMPTY],
        )


class CaptureLLM:
    """Mock LLM that captures all input messages and yields a valid response."""

    def __init__(self, response_text: str = ""):
        self.captured_messages = []
        verdict = '<!-- VERDICT: {"direction": "中性", "reason": "基于输入判断"} -->'
        self.response_text = response_text or f"【正式分析报告】\n基于证据分析。\n{verdict}"

    async def astream(self, messages):
        self.captured_messages.extend(messages)
        yield SimpleNamespace(
            content=self.response_text,
            response_metadata={"finish_reason": "stop", "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}},
        )

    def invoke(self, messages):
        self.captured_messages.extend(messages)
        return SimpleNamespace(content=self.response_text)


@pytest.fixture
def populated_archive_db(tmp_path):
    """Fixture creating an archive DB with sample records for 688256.SH."""
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


def _make_sample_bundle(
    symbol: str = "688256.SH",
    as_of: str = "2026-08-26",
    status: str = "available",
    direction_allowed: bool = True,
    score: Optional[float] = 0.5,
    label: str = "bullish",
) -> SentimentBundleV1:
    """Create a sample SentimentBundleV1 for testing."""
    return SentimentBundleV1(
        status=status,
        requested_as_of=as_of,
        cutoff_at=f"{as_of}T15:59:59Z",
        content_as_of=f"{as_of}T08:30:00Z",
        metric_as_of=f"{as_of}T15:00:00Z",
        direction_allowed=direction_allowed,
        reason_codes=[] if status == "available" else [f"social_{status}"],
        symbol=symbol,
        bundle_id="sha256:rollout_test_bundle_hash",
        social_attention=SocialAttention(
            post_count=10,
            comment_count=50,
            author_count=30,
            total_interactions=2000,
            interaction_velocity=10.0,
        ),
        social_sentiment=SocialSentiment(
            score=score,
            label=label,
            bullish_count=8,
            bearish_count=1,
            neutral_count=1,
            insufficient_count=0,
            is_calibrated_probability=False,
        ),
        evidence_samples=[
            {
                "record_id": "xhs:post:sample_rollout_1",
                "platform": "xhs",
                "record_type": "post",
                "published_at": f"{as_of}T08:00:00Z",
                "title": "寒武纪产品讨论",
                "text": "寒武纪芯片性能讨论与测试反馈",
                "stance_label": "bullish",
            }
        ],
        platform_breakdown={
            "xhs": {"post_count": 8, "comment_count": 40},
            "douyin": {"post_count": 2, "comment_count": 10},
        },
    )


# ============================================================================
# 1. Contract A: disabled Mode Tests
# ============================================================================

def test_disabled_mode_collector_does_not_call_provider_or_touch_db():
    """Contract A: disabled mode returns not_applicable, direction_allowed=False, never calls provider."""
    spy = SpySocialProvider()
    collector = SocialDataCollector(
        mode="disabled",
        archive_db="/path/does/not/exist/fake.db",
        custom_provider=spy,
    )

    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert spy.call_count == 0, "Provider must NOT be called in disabled mode"
    assert ctx["status"] == "not_applicable"
    assert ctx["mode"] == "disabled"
    assert ctx["direction_allowed"] is False
    assert REASON_SOCIAL_NOT_APPLICABLE in ctx["reason_codes"]
    assert ctx["bundle"]["status"] == "not_applicable"
    assert ctx["bundle"]["direction_allowed"] is False
    assert ctx["data_failure_ledger"] == []


def test_disabled_mode_adapter_returns_not_applicable():
    """Contract A: adapter in disabled mode returns 4-section not_applicable text with source_mode='disabled'."""
    resolved = resolve_social_analyst_inputs(
        mode="disabled",
        ticker="600519",
        current_date="2026-08-26",
        ticker_display="600519 (贵州茅台)",
    )

    assert resolved.mode == "disabled"
    assert resolved.source_mode == "disabled"
    assert resolved.source_status == "not_applicable"
    assert resolved.direction_allowed is False
    assert "【get_news】" not in resolved.human_content
    assert "【一、数据状态与数据源有效性】" in resolved.human_content
    assert "社交归档状态：not_applicable" in resolved.human_content
    assert "【社交方向不可判断】" in resolved.human_content


def test_disabled_mode_end_to_end_analyst_trace():
    """Contract A: analyst node in disabled mode records source_mode='disabled'."""
    collector = DataCollector()
    collector._cache["600519_2026-08-26"] = {
        "news": "传统新闻数据",
        "zt_pool": "涨停池数据",
        "hot_stocks": "热门榜数据",
    }

    mock_llm = CaptureLLM()
    node = create_social_media_analyst(mock_llm, collector)

    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "600519",
        "mode": "disabled",
    }

    result = asyncio.run(node(state))
    assert "analyst_traces" in result
    trace = result["analyst_traces"][0]
    assert trace["source_mode"] == "disabled"
    assert trace["source_status"] == "not_applicable"
    assert trace["direction_allowed"] is False

    human_msg = [m for m in mock_llm.captured_messages if m.__class__.__name__ == "HumanMessage"][0]
    assert "【get_news】" not in human_msg.content
    assert "传统新闻数据" not in human_msg.content
    assert "【一、数据状态与数据源有效性】" in human_msg.content


# ============================================================================
# 2. Contract B: shadow Mode Tests (Gate 4 Contract)
# ============================================================================

def test_shadow_mode_collector_generates_bundle_with_direction_disallowed(populated_archive_db):
    """Contract B: shadow mode queries archive, generates bundle, but direction_allowed is strictly False."""
    collector = SocialDataCollector(
        mode="shadow",
        archive_db=populated_archive_db,
        lookback_days=7,
        min_posts=1,
        min_classified=1,
        min_authors=1,
    )

    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert ctx["mode"] == "shadow"
    assert ctx["status"] in ("available", "partial")
    assert ctx["direction_allowed"] is False, "direction_allowed must be False in shadow mode"
    assert isinstance(ctx["bundle"], dict)
    assert ctx["bundle"]["symbol"] == "688256.SH"
    assert ctx["data_failure_ledger"] == []


def test_shadow_mode_adapter_preserves_bundle_and_traces_shadow():
    """Contract B: shadow mode adapter returns structured bundle text and source_mode='shadow'."""
    bundle = _make_sample_bundle(symbol="688256.SH", as_of="2026-08-26", direction_allowed=True)
    social_data_context: SocialDataContext = {
        "status": "available",
        "mode": "shadow",
        "requested_as_of": "2026-08-26",
        "direction_allowed": False,
        "reason_codes": [],
        "bundle": bundle.to_dict(),
        "source_provenance": {},
        "data_failure_ledger": [],
    }

    resolved = resolve_social_analyst_inputs(
        mode="shadow",
        social_data_context=social_data_context,
        ticker="688256.SH",
        current_date="2026-08-26",
        ticker_display="688256.SH (寒武纪)",
    )

    assert resolved.mode == "shadow"
    assert resolved.source_mode == "shadow"
    assert resolved.direction_allowed is False, "Bundle must not enter direction in shadow mode"
    assert resolved.bundle is not None
    assert resolved.bundle["symbol"] == "688256.SH"
    assert resolved.bundle_id == "sha256:rollout_test_bundle_hash"

    # Human content in Gate 4 shadow mode uses structured 4 sections with direction_allowed=False
    assert "【get_news】" not in resolved.human_content
    assert "【一、数据状态与数据源有效性】" in resolved.human_content
    assert "允许方向推断 (direction_allowed)：否 (False)" in resolved.human_content


def test_shadow_mode_end_to_end_analyst_trace_and_message():
    """Contract B: analyst node in shadow mode records source_mode='shadow' and direction_allowed=False."""
    bundle = _make_sample_bundle(symbol="688256.SH", as_of="2026-08-26")
    social_data_context: SocialDataContext = {
        "status": "available",
        "mode": "shadow",
        "requested_as_of": "2026-08-26",
        "direction_allowed": False,
        "reason_codes": [],
        "bundle": bundle.to_dict(),
        "source_provenance": {},
        "data_failure_ledger": [],
    }

    collector = DataCollector()
    collector._cache["688256.SH_2026-08-26"] = {
        "news": "影子传统新闻",
        "zt_pool": "影子涨停池",
        "hot_stocks": "影子热门股",
        "social_data_context": social_data_context,
    }

    mock_llm = CaptureLLM()
    node = create_social_media_analyst(mock_llm, collector)

    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "688256.SH",
        "mode": "shadow",
        "social_data_context": social_data_context,
    }

    result = asyncio.run(node(state))
    trace = result["analyst_traces"][0]
    assert trace["source_mode"] == "shadow"
    assert trace["direction_allowed"] is False
    assert trace["bundle_id"] == "sha256:rollout_test_bundle_hash"

    human_msg = [m for m in mock_llm.captured_messages if m.__class__.__name__ == "HumanMessage"][0]
    assert "【get_news】" not in human_msg.content
    assert "影子传统新闻" not in human_msg.content
    assert "【一、数据状态与数据源有效性】" in human_msg.content
    assert "允许方向推断 (direction_allowed)：否 (False)" in human_msg.content


# ============================================================================
# 3. Contract C: active Mode and Canary Whitelist Tests
# ============================================================================

def test_active_canary_unlisted_symbol_must_not_silently_be_active(populated_archive_db):
    """Contract C: when canary symbols configured, unlisted symbol falls back to non-active and direction_allowed=False."""
    spy = SpySocialProvider()
    collector = SocialDataCollector(
        mode="active",
        archive_db=populated_archive_db,
        canary_symbols="600519.SH, 000001.SZ",
        custom_provider=spy,
    )

    # 688256.SH is NOT in canary list
    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert spy.call_count == 0, "Provider must NOT be called for unlisted canary symbol"
    assert ctx["direction_allowed"] is False
    assert ctx["status"] == "not_applicable"
    assert ctx["mode"] == "disabled"
    assert REASON_SOCIAL_NOT_APPLICABLE in ctx["reason_codes"]
    assert ctx["bundle"]["status"] == "not_applicable"
    assert ctx["bundle"]["direction_allowed"] is False


def test_active_canary_listed_symbol_enters_active_collection(populated_archive_db):
    """Contract C: listed canary symbol is allowed to enter active archive collection."""
    collector = SocialDataCollector(
        mode="active",
        archive_db=populated_archive_db,
        canary_symbols="688256.SH, 600519.SH",
        lookback_days=7,
        min_posts=1,
        min_classified=1,
        min_authors=1,
    )

    # 688256.SH IS in canary list
    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")

    assert ctx["mode"] == "active"
    assert ctx["status"] in ("available", "partial")
    assert isinstance(ctx["bundle"], dict)
    assert ctx["bundle"]["symbol"] == "688256.SH"


def test_active_empty_canary_allows_all_valid_symbols(populated_archive_db):
    """Contract C: empty canary_symbols allows all valid A-share symbols into active collection."""
    collector = SocialDataCollector(
        mode="active",
        archive_db=populated_archive_db,
        canary_symbols="",  # empty means all allowed
        lookback_days=7,
        min_posts=1,
        min_classified=1,
        min_authors=1,
    )

    ctx = collector.collect(symbol="688256.SH", as_of="2026-08-26")
    assert ctx["mode"] == "active"
    assert ctx["status"] in ("available", "partial")


# ============================================================================
# 4. Contract D: active Insufficient / Empty / Failed Never Falls Back to Legacy
# ============================================================================

@pytest.mark.parametrize(
    "status,reason_code",
    [
        ("empty", REASON_SOCIAL_EMPTY),
        ("insufficient", REASON_SOCIAL_INSUFFICIENT_COVERAGE),
        ("failed", REASON_SOCIAL_ARCHIVE_MISSING),
    ],
)
def test_active_mode_non_available_adapter_has_no_legacy_fallback(status, reason_code):
    """Contract D: active mode with empty/insufficient/failed data MUST NOT fall back to legacy news/zt/hot."""
    b_obj = create_empty_sentiment_bundle(
        status="empty" if status == "insufficient" else status,
        requested_as_of="2026-08-26",
        cutoff_at="2026-08-26T15:59:59Z",
        reason_codes=[reason_code],
        symbol="688256.SH",
    )

    social_data_context: SocialDataContext = {
        "status": status,
        "mode": "active",
        "requested_as_of": "2026-08-26",
        "direction_allowed": False,
        "reason_codes": [reason_code],
        "bundle": b_obj.to_dict(),
        "source_provenance": {},
        "data_failure_ledger": [],
    }

    resolved = resolve_social_analyst_inputs(
        mode="active",
        social_data_context=social_data_context,
        pool={
            "news": "【Pool新闻】禁止泄露！",
            "zt_pool": "【Pool涨停】禁止泄露！",
            "hot_stocks": "【Pool热搜】禁止泄露！",
        },
        market_attention={
            "zt_pool": {"status": "available", "as_of": "2026-08-26", "raw": "盘面涨停50家"},
            "hot_stocks": {"status": "available", "as_of": "2026-08-26", "raw": "雪球榜首"},
        },
        ticker="688256.SH",
        current_date="2026-08-26",
        ticker_display="688256.SH (寒武纪)",
    )

    assert resolved.mode == "active"
    assert resolved.source_mode == "active"
    assert resolved.direction_allowed is False

    # Human content MUST contain structured 4 sections and explicit gap notice
    assert "【一、数据状态与数据源有效性】" in resolved.human_content
    assert "【社交方向不可判断】" in resolved.human_content
    assert "【四、市场关注度（盘面与榜单生态）】" in resolved.human_content

    # Strict isolation: NO legacy news / pool news / 【get_news】 block
    assert "【get_news】" not in resolved.human_content
    assert "【Pool新闻】" not in resolved.human_content


def test_active_mode_end_to_end_empty_bundle_has_no_legacy_news():
    """Contract D: analyst node in active mode with empty bundle formats explicit gap without legacy news."""
    collector = DataCollector()
    collector._cache["688256.SH_2026-08-26"] = {
        "news": "新闻数据（ACTIVE 禁止读取）",
        "social_data_context": {
            "status": "empty",
            "mode": "active",
            "requested_as_of": "2026-08-26",
            "direction_allowed": False,
            "reason_codes": [REASON_SOCIAL_EMPTY],
            "bundle": None,
            "source_provenance": {},
            "data_failure_ledger": [],
        },
        "market_data_context": {
            "market_attention": {
                "zt_pool": {"status": "available", "as_of": "2026-08-26", "raw": "涨停板"},
                "hot_stocks": {"status": "available", "as_of": "2026-08-26", "raw": "热门股"},
            }
        },
    }

    mock_llm = CaptureLLM()
    node = create_social_media_analyst(mock_llm, collector)

    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "688256.SH",
        "mode": "active",
    }

    result = asyncio.run(node(state))
    trace = result["analyst_traces"][0]
    assert trace["source_mode"] == "active"
    assert trace["direction_allowed"] is False

    human_msg = [m for m in mock_llm.captured_messages if m.__class__.__name__ == "HumanMessage"][0]
    assert "【社交方向不可判断】" in human_msg.content
    assert "新闻数据（ACTIVE 禁止读取）" not in human_msg.content
    assert "【get_news】" not in human_msg.content


def test_gate4_matrix_t_h4_legacy_symbols_deleted_and_disabled_is_not_applicable():
    """Matrix T-H4: Gate 4 verification - legacy symbols deleted and disabled=not_applicable."""
    import inspect
    import tradingagents.dataflows.social.analyst_adapter as adapter_module
    from tradingagents.dataflows.social.analyst_adapter import (
        ResolvedSocialInputs,
        resolve_social_analyst_inputs,
    )

    # 1. Verify legacy_proxy does NOT appear in ResolvedSocialInputs field annotations or defaults
    resolved = resolve_social_analyst_inputs(mode="disabled", ticker="600519", current_date="2026-08-26")
    assert resolved.source_mode != "legacy_proxy"
    assert resolved.source_status == "not_applicable"
    assert resolved.direction_allowed is False

    # 2. Verify adapter module source code has 0 occurrences of 'legacy_proxy'
    adapter_src = inspect.getsource(adapter_module)
    assert "legacy_proxy" not in adapter_src, "T-H4 violation: legacy_proxy symbol still exists in analyst_adapter"

    # 3. Disabled mode does not contain news sentinel or fallback
    assert "【get_news】" not in resolved.human_content
    assert "【社交方向不可判断】" in resolved.human_content

