"""Unit and integration tests for Task 11: social analyst input separation and adapter.

Verifies:
1. Fake LLM captures messages; NEWS and SOCIAL sentinels do not leak into each other.
2. Active mode ONLY consumes social_data_context + market_attention; no 【get_news】 and no get_news tool calls.
3. Disabled mode uses legacy_proxy without using bundle for direction.
4. Shadow mode retains bundle while keeping legacy text inputs.
5. Missing/insufficient data formats explicit gap notice with direction_allowed=False.
6. Adapter resolution and prompt formatter 4-section structure and anti-hallucination guardrails.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
from tradingagents.dataflows.social.analyst_adapter import (
    ResolvedSocialInputs,
    resolve_social_analyst_inputs,
    resolve_social_mode,
)
from tradingagents.dataflows.social.contracts import (
    SentimentBundleV1,
    SocialAttention,
    SocialDataContext,
    SocialSentiment,
    create_empty_sentiment_bundle,
)
from tradingagents.dataflows.social.prompt_formatter import (
    format_social_analyst_prompt,
    format_social_sections,
)
from tradingagents.graph.data_collector import DataCollector


class CaptureLLM:
    """Mock LLM that captures all input messages and yields a valid response."""

    def __init__(self, response_text: str = ""):
        self.captured_messages = []
        verdict = '<!-- VERDICT: {"direction": "中性", "reason": "基于客观输入判断"} -->'
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


def _make_active_social_bundle(
    symbol: str = "600519",
    as_of: str = "2026-08-26",
    sentinel_text: str = "SOCIAL_SENTINEL_ALPHA_99999",
) -> SentimentBundleV1:
    """Construct a populated available SentimentBundleV1 for testing."""
    return SentimentBundleV1(
        status="available",
        requested_as_of=as_of,
        cutoff_at=f"{as_of}T15:59:59Z",
        content_as_of=f"{as_of}T08:30:00Z",
        metric_as_of=f"{as_of}T15:00:00Z",
        direction_allowed=True,
        reason_codes=[],
        symbol=symbol,
        bundle_id="sha256:testbundle123456",
        social_attention=SocialAttention(
            post_count=15,
            comment_count=60,
            author_count=50,
            total_interactions=3500,
            interaction_velocity=12.5,
        ),
        social_sentiment=SocialSentiment(
            score=0.45,
            label="bullish",
            bullish_count=10,
            bearish_count=2,
            neutral_count=3,
            insufficient_count=0,
            is_calibrated_probability=False,
        ),
        evidence_samples=[
            {
                "record_id": "xhs:post:sample1",
                "platform": "xhs",
                "record_type": "post",
                "published_at": f"{as_of}T08:00:00Z",
                "title": "茅台三季报讨论",
                "text": sentinel_text,
                "stance_label": "bullish",
            }
        ],
        platform_breakdown={
            "xhs": {"post_count": 10, "comment_count": 40},
            "douyin": {"post_count": 5, "comment_count": 20},
        },
    )


# ============================================================================
# 1. Sentinel Separation Tests (NEWS vs SOCIAL mutual non-leakage)
# ============================================================================


def test_social_analyst_does_not_leak_news_sentinel_in_active_mode():
    """Active social media analyst must NOT contain NEWS_SENTINEL in its messages."""
    news_sentinel = "NEWS_SENTINEL_STRICT_ISOLATION_11111"
    social_sentinel = "SOCIAL_SENTINEL_ACTIVE_BODY_22222"

    bundle = _make_active_social_bundle(sentinel_text=social_sentinel)
    social_data_context: SocialDataContext = {
        "status": "available",
        "mode": "active",
        "requested_as_of": "2026-08-26",
        "direction_allowed": True,
        "reason_codes": [],
        "bundle": bundle.to_dict(),
        "source_provenance": {},
        "data_failure_ledger": [],
    }

    collector = DataCollector()
    collector._cache["600519_2026-08-26"] = {
        "news": f"【重要公司公告】{news_sentinel} 公司签订大单",
        "global_news": f"【宏观快讯】{news_sentinel} 央行发布流动性指引",
        "social_data_context": social_data_context,
        "market_data_context": {
            "market_attention": {
                "zt_pool": {"status": "available", "as_of": "2026-08-26", "raw": "涨停50家"},
                "hot_stocks": {"status": "available", "as_of": "2026-08-26", "raw": "雪球热搜前五"},
            }
        },
    }

    mock_llm = CaptureLLM()
    node = create_social_media_analyst(mock_llm, collector)

    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "600519",
        "mode": "active",
        "social_data_context": social_data_context,
    }

    result = asyncio.run(node(state))

    assert "sentiment_report" in result
    all_content = " ".join(getattr(m, "content", "") for m in mock_llm.captured_messages)

    # NEWS sentinel must NOT leak to social analyst
    assert news_sentinel not in all_content, "NEWS sentinel leaked into social analyst prompt!"

    # SOCIAL sentinel MUST be present
    assert social_sentinel in all_content, "SOCIAL sentinel missing from social analyst prompt!"

    # 【get_news】 must NOT be in active social messages
    assert "【get_news】" not in all_content


def test_news_analyst_does_not_leak_social_sentinel():
    """News analyst must NOT contain SOCIAL_SENTINEL in its messages."""
    news_sentinel = "NEWS_SENTINEL_EXCLUSIVE_33333"
    social_sentinel = "SOCIAL_SENTINEL_EXCLUSIVE_44444"

    bundle = _make_active_social_bundle(sentinel_text=social_sentinel)
    social_data_context: SocialDataContext = {
        "status": "available",
        "mode": "active",
        "requested_as_of": "2026-08-26",
        "direction_allowed": True,
        "reason_codes": [],
        "bundle": bundle.to_dict(),
        "source_provenance": {},
        "data_failure_ledger": [],
    }

    collector = DataCollector()
    collector._cache["600519_2026-08-26"] = {
        "news": f"【公司要闻】{news_sentinel} 营收突破预期",
        "global_news": "【国际经贸】全球贸易数据发布",
        "social_data_context": social_data_context,
    }

    mock_llm = CaptureLLM()
    node = create_news_analyst(mock_llm, collector)

    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "600519",
        "social_data_context": social_data_context,
    }

    result = asyncio.run(node(state))

    assert "news_report" in result
    all_content = " ".join(getattr(m, "content", "") for m in mock_llm.captured_messages)

    # SOCIAL sentinel must NOT leak to news analyst
    assert social_sentinel not in all_content, "SOCIAL sentinel leaked into news analyst prompt!"

    # NEWS sentinel MUST be present in news analyst
    assert news_sentinel in all_content, "NEWS sentinel missing from news analyst prompt!"


# ============================================================================
# 2. Active Mode Structure & Traces Tests
# ============================================================================


def test_social_analyst_active_mode_four_sections_and_traces():
    """Active mode generates four sections and populates complete TraceItem."""
    bundle = _make_active_social_bundle(symbol="600519", as_of="2026-08-26")
    social_data_context: SocialDataContext = {
        "status": "available",
        "mode": "active",
        "requested_as_of": "2026-08-26",
        "direction_allowed": True,
        "reason_codes": [],
        "bundle": bundle.to_dict(),
        "source_provenance": {},
        "data_failure_ledger": [],
    }

    collector = DataCollector()
    collector._cache["600519_2026-08-26"] = {
        "social_data_context": social_data_context,
        "market_data_context": {
            "market_attention": {
                "zt_pool": {"status": "available", "as_of": "2026-08-26", "raw": "连板最高5板"},
                "hot_stocks": {"status": "available", "as_of": "2026-08-26", "raw": "雪球关注榜第一"},
            }
        },
    }

    mock_llm = CaptureLLM()
    node = create_social_media_analyst(mock_llm, collector)

    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "600519",
        "mode": "active",
    }

    result = asyncio.run(node(state))

    # Trace audit assertions
    assert "analyst_traces" in result
    assert len(result["analyst_traces"]) == 1
    trace = result["analyst_traces"][0]
    assert trace["agent"] == "social_media_analyst"
    assert trace["source_mode"] == "active"
    assert trace["source_status"] == "available"
    assert trace["bundle_id"] == "sha256:testbundle123456"
    assert trace["direction_allowed"] is True
    assert trace["evidence_refs"] == ["xhs:post:sample1"]

    # Check 4 sections in human message
    human_msg = [m for m in mock_llm.captured_messages if getattr(m, "type", "") == "human" or m.__class__.__name__ == "HumanMessage"][0]
    content = human_msg.content
    assert "【一、数据状态与数据源有效性】" in content
    assert "【二、社交观点与立场解构】" in content
    assert "【三、社交热度与互动特征】" in content
    assert "【四、市场关注度（盘面与榜单生态）】" in content
    assert "热度≠看多" in content
    assert "非校准概率" in content


def test_social_analyst_active_mode_missing_bundle_fails_closed():
    """Active mode with missing/empty social context creates explicit gap without falling back to news."""
    collector = DataCollector()
    collector._cache["600519_2026-08-26"] = {
        "news": "新闻数据（禁止读取）",
        "market_data_context": {
            "market_attention": {
                "zt_pool": {"status": "available", "as_of": "2026-08-26", "raw": "涨停数据"},
                "hot_stocks": {"status": "available", "as_of": "2026-08-26", "raw": "热搜数据"},
            }
        },
    }

    mock_llm = CaptureLLM()
    node = create_social_media_analyst(mock_llm, collector)

    # Explicitly active mode but empty social_data_context
    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "600519",
        "mode": "active",
        "social_data_context": {
            "status": "empty",
            "mode": "active",
            "requested_as_of": "2026-08-26",
            "direction_allowed": False,
            "reason_codes": ["social_empty"],
            "bundle": None,
            "source_provenance": {},
            "data_failure_ledger": [],
        },
    }

    result = asyncio.run(node(state))
    trace = result["analyst_traces"][0]
    assert trace["source_mode"] == "active"
    assert trace["source_status"] == "empty"
    assert trace["direction_allowed"] is False

    human_msg = [m for m in mock_llm.captured_messages if m.__class__.__name__ == "HumanMessage"][0]
    content = human_msg.content
    assert "【社交方向不可判断】" in content
    assert "新闻数据（禁止读取）" not in content
    assert "【get_news】" not in content


# ============================================================================
# 3. Disabled & Shadow Mode Tests
# ============================================================================


def test_social_analyst_disabled_mode_uses_legacy_proxy():
    """Disabled mode returns legacy news/zt/hot and traces source_mode='legacy_proxy'."""
    collector = DataCollector()
    collector._cache["600519_2026-08-26"] = {
        "news": "2026-08-26 贵州茅台发布半年报公告",
        "zt_pool": "涨停家数 30 家",
        "hot_stocks": "雪球热搜第一：贵州茅台",
    }

    mock_llm = CaptureLLM()
    node = create_social_media_analyst(mock_llm, collector)

    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "600519",
        "mode": "disabled",
    }

    result = asyncio.run(node(state))
    trace = result["analyst_traces"][0]
    assert trace["source_mode"] == "legacy_proxy"

    human_msg = [m for m in mock_llm.captured_messages if m.__class__.__name__ == "HumanMessage"][0]
    content = human_msg.content
    assert "【get_news】" in content
    assert "2026-08-26 贵州茅台发布半年报公告" in content
    assert "【涨停池数据】" in content
    assert "【雪球热门股票】" in content


def test_social_analyst_shadow_mode_holds_bundle_without_directional_impact():
    """Shadow mode holds bundle in metadata but uses legacy format and sets direction_allowed=False."""
    bundle = _make_active_social_bundle(symbol="600519", as_of="2026-08-26")
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
    collector._cache["600519_2026-08-26"] = {
        "news": "影子模式传统新闻",
        "zt_pool": "影子模式涨停池",
        "hot_stocks": "影子模式雪球榜",
        "social_data_context": social_data_context,
    }

    mock_llm = CaptureLLM()
    node = create_social_media_analyst(mock_llm, collector)

    state = {
        "trade_date": "2026-08-26",
        "company_of_interest": "600519",
        "mode": "shadow",
        "social_data_context": social_data_context,
    }

    result = asyncio.run(node(state))
    trace = result["analyst_traces"][0]
    assert trace["source_mode"] == "legacy_proxy"
    assert trace["direction_allowed"] is False
    assert trace["bundle_id"] == "sha256:testbundle123456"

    human_msg = [m for m in mock_llm.captured_messages if m.__class__.__name__ == "HumanMessage"][0]
    content = human_msg.content
    # In shadow mode, human message uses legacy proxy format
    assert "【get_news】" in content
    assert "影子模式传统新闻" in content


# ============================================================================
# 4. Adapter & Formatter Unit Tests
# ============================================================================


def test_resolve_social_mode_precedence():
    """Verify precedence: explicit param > context > config > env > default."""
    # Explicit param wins
    assert resolve_social_mode("active", {"mode": "disabled"}) == "active"
    assert resolve_social_mode("DISABLED") == "disabled"
    assert resolve_social_mode("SHADOW") == "shadow"

    # Context wins over config/env
    assert resolve_social_mode(None, {"mode": "active"}, {"TA_SOCIAL_MODE": "disabled"}) == "active"

    # Config wins over default
    assert resolve_social_mode(None, None, {"TA_SOCIAL_MODE": "shadow"}) == "shadow"
    assert resolve_social_mode(None, None, {"social_mode": "active"}) == "active"

    # Default is disabled
    assert resolve_social_mode(None, None, None) == "disabled"


def test_prompt_formatter_discipline_and_sections():
    """Prompt formatter output includes all 4 sections and strict guardrails."""
    bundle = _make_active_social_bundle(symbol="000001", as_of="2026-08-20")
    formatted = format_social_analyst_prompt(
        bundle=bundle,
        market_attention={
            "zt_pool": {"status": "available", "as_of": "2026-08-20", "raw": "zt raw"},
            "hot_stocks": {"status": "unavailable", "gap": "【数据获取失败】hot_stocks：无数据"},
        },
        ticker_display="000001 (平安银行)",
        current_date="2026-08-20",
    )

    assert "【一、数据状态与数据源有效性】" in formatted
    assert "【二、社交观点与立场解构】" in formatted
    assert "【三、社交热度与互动特征】" in formatted
    assert "【四、市场关注度（盘面与榜单生态）】" in formatted
    assert "热度≠看多" in formatted
    assert "is_calibrated_probability=False" in formatted
    assert "000001 (平安银行)" in formatted
    assert "zt raw" in formatted
    assert "【数据获取失败】hot_stocks：无数据" in formatted


def test_adapter_and_formatter_cutoff_and_reason_code_l1():
    """L1: Adapter and formatter must not fabricate hardcoded 15:59:59Z or ad-hoc reasons."""
    from tradingagents.dataflows.social.contracts import REASON_SOCIAL_EMPTY

    # 1. Adapter active mode with missing bundle and valid date
    res_valid = resolve_social_analyst_inputs(
        mode="active",
        social_data_context=None,
        ticker="600519",
        current_date="2026-08-26",
    )
    assert res_valid.source_status == "empty"
    assert res_valid.reason_codes == [REASON_SOCIAL_EMPTY]
    assert "social_empty_context" not in res_valid.reason_codes
    assert res_valid.bundle is not None
    assert res_valid.bundle["cutoff_at"] == "2026-08-26T15:59:59.999999Z"

    # 2. Adapter active mode with invalid date falls back to unknown
    res_invalid = resolve_social_analyst_inputs(
        mode="active",
        social_data_context=None,
        ticker="600519",
        current_date="invalid-date",
    )
    assert res_invalid.bundle["cutoff_at"] == "unknown"
    assert res_invalid.reason_codes == [REASON_SOCIAL_EMPTY]

    # 3. Prompt formatter with missing cutoff_at in bundle
    formatted_valid = format_social_sections(
        bundle={"status": "empty"},
        current_date="2026-08-26",
    )
    assert "截断时间 2026-08-26T15:59:59.999999Z" in formatted_valid

    formatted_unknown = format_social_sections(
        bundle={"status": "empty"},
        current_date="invalid-date",
    )
    assert "截断时间 unknown" in formatted_unknown

