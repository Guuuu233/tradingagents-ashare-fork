"""Tests for social data context propagation and horizon result building (Task 9).

Specification:
- docs/social_data/implementation_plan.md Task 9, §5.5, §8
- D-008, D-009, D-010
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tradingagents.agents.utils.agent_states import AgentState, TraceItem
from tradingagents.dataflows.social.contracts import (
    REASON_SOCIAL_ARCHIVE_LOCKED,
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_INSUFFICIENT_COVERAGE,
    REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
    SocialStatus,
    create_default_social_data_context,
)
from tradingagents.graph.data_collector import DataCollector
from tradingagents.graph.propagation import Propagator


def _make_mock_graph_instance():
    """Return a lightweight TradingAgentsGraph without real LLM/tool setup."""
    with patch("tradingagents.graph.trading_graph.create_llm_client"), \
         patch("tradingagents.graph.trading_graph.FinancialSituationMemory"), \
         patch("tradingagents.graph.trading_graph.GraphSetup"), \
         patch("tradingagents.graph.trading_graph.ConditionalLogic"), \
         patch("tradingagents.graph.trading_graph.Propagator"), \
         patch("tradingagents.graph.trading_graph.Reflector"), \
         patch("tradingagents.graph.trading_graph.SignalProcessor"), \
         patch("tradingagents.graph.trading_graph.set_config"):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        ta = TradingAgentsGraph.__new__(TradingAgentsGraph)
        ta.debug = False
        ta.config = {}
        ta.callbacks = []
        ta.ticker = None
        ta.log_states_dict = {}
        ta.quick_thinking_llm = MagicMock()
        ta.data_collector = DataCollector()
        ta.propagator = Propagator()
        ta.graph = MagicMock()
        ta.signal_processor = MagicMock()
        return ta


def test_agent_state_and_trace_item_types():
    """AgentState has social_data_context and TraceItem has social audit fields."""
    trace_item: TraceItem = {
        "agent": "social_media_analyst",
        "horizon": "short",
        "data_window": "7d",
        "key_finding": "社交情绪向好",
        "verdict": "看多",
        "confidence": "中",
        "source_status": "available",
        "source_mode": "active",
        "bundle_id": "sha256:abc123",
        "direction_allowed": True,
        "reason_codes": [],
        "evidence_refs": ["xhs:post:123"],
    }
    assert trace_item["source_status"] == "available"
    assert trace_item["direction_allowed"] is True
    assert trace_item["evidence_refs"] == ["xhs:post:123"]


def test_create_initial_state_default_social_data_context():
    """Propagator.create_initial_state populates valid default social_data_context."""
    p = Propagator()
    state = p.create_initial_state("600519.SH", "2026-08-26")
    assert "social_data_context" in state
    social_ctx = state["social_data_context"]
    assert isinstance(social_ctx, dict)
    assert social_ctx["status"] == SocialStatus.NOT_APPLICABLE.value
    assert social_ctx["mode"] == "disabled"
    assert social_ctx["direction_allowed"] is False
    assert social_ctx["reason_codes"] == []
    assert social_ctx["bundle"] is None
    assert social_ctx["data_failure_ledger"] == []
    assert social_ctx["requested_as_of"] == "2026-08-26"


def test_create_initial_state_with_custom_social_data_context():
    """Propagator.create_initial_state preserves custom social_data_context passed in."""
    custom_ctx = create_default_social_data_context(
        status=SocialStatus.AVAILABLE.value,
        mode="active",
        requested_as_of="2026-08-26",
        bundle={"schema_version": "social.sentiment_bundle.v1", "direction_allowed": True},
    )
    p = Propagator()
    state = p.create_initial_state(
        "600519.SH",
        "2026-08-26",
        social_data_context=custom_ctx,
    )
    assert state["social_data_context"] == custom_ctx
    assert state["social_data_context"]["status"] == "available"
    assert state["social_data_context"]["mode"] == "active"
    assert state["social_data_context"]["direction_allowed"] is True


def test_build_horizon_result_preserves_social_data_context():
    """TradingAgentsGraph._build_horizon_result preserves social_data_context in result."""
    ta = _make_mock_graph_instance()
    social_ctx = create_default_social_data_context(
        status=SocialStatus.AVAILABLE.value,
        mode="active",
        requested_as_of="2026-08-26",
    )
    state = {
        "horizon": "short",
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-26",
        "market_data_context": {},
        "social_data_context": social_ctx,
        "final_trade_decision": "买入",
    }
    result = ta._build_horizon_result("short", state)
    assert "social_data_context" in result
    assert result["social_data_context"] == social_ctx


def test_build_horizon_result_merges_market_and_social_failure_ledger_into_data_gaps():
    """_build_horizon_result merges market and social failure ledger items into data_gaps."""
    ta = _make_mock_graph_instance()
    market_ctx = {
        "data_failure_ledger": [
            {
                "source": "daily_bars",
                "status": "failed",
                "gap": "【数据获取失败】行情日K数据",
            }
        ]
    }
    social_ctx = {
        "data_failure_ledger": [
            {
                "source": "social_archive",
                "status": "timeout",
                "reason_code": REASON_SOCIAL_ARCHIVE_LOCKED,
                "gap": f"【数据获取失败】社交归档：{REASON_SOCIAL_ARCHIVE_LOCKED}",
            },
            {
                "source": "social_archive",
                "status": "refused",
                "reason_code": REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
                "gap": f"【数据获取失败】社交归档：{REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT}",
            },
        ]
    }
    state = {
        "horizon": "short",
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-26",
        "market_data_context": market_ctx,
        "social_data_context": social_ctx,
    }
    result = ta._build_horizon_result("short", state)
    assert len(result["data_gaps"]) == 3
    assert "【数据获取失败】行情日K数据" in result["data_gaps"]
    assert f"【数据获取失败】社交归档：{REASON_SOCIAL_ARCHIVE_LOCKED}" in result["data_gaps"]
    assert f"【数据获取失败】社交归档：{REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT}" in result["data_gaps"]


def test_build_horizon_result_does_not_add_empty_or_insufficient_to_failed_data_gaps():
    """Empty, insufficient, or not_applicable statuses MUST NOT enter data_gaps as failures (§5.5)."""
    ta = _make_mock_graph_instance()
    social_ctx = {
        "status": SocialStatus.EMPTY.value,
        "reason_codes": [REASON_SOCIAL_EMPTY],
        "data_failure_ledger": [
            # Even if an erroneous entry with non-failure status is present, it must be filtered out
            {
                "source": "social_archive",
                "status": "empty",
                "reason_code": REASON_SOCIAL_EMPTY,
                "gap": "社交数据为空",
            },
            {
                "source": "social_archive",
                "status": "not_applicable",
                "reason_code": "social_not_applicable",
                "gap": "社交功能关闭",
            },
            {
                "source": "social_archive",
                "status": "partial",
                "reason_code": REASON_SOCIAL_INSUFFICIENT_COVERAGE,
                "gap": "社交覆盖不足",
            },
        ],
    }
    state = {
        "horizon": "short",
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-26",
        "market_data_context": {"data_failure_ledger": []},
        "social_data_context": social_ctx,
    }
    result = ta._build_horizon_result("short", state)
    assert result["data_gaps"] == []


def test_propagate_sync_injects_social_data_context():
    """TradingAgentsGraph.propagate extracts social_data_context and passes to initial state."""
    ta = _make_mock_graph_instance()
    social_ctx = create_default_social_data_context(
        status=SocialStatus.AVAILABLE.value,
        mode="active",
        requested_as_of="2026-08-26",
    )
    ta.data_collector.collect = MagicMock(
        return_value={
            "market_data_context": {"daily": {"as_of": "2026-08-26"}},
            "social_data_context": social_ctx,
        }
    )
    captured_state = {}

    def _capture(state, **_kwargs):
        captured_state.update(state)
        return {
            "company_of_interest": "600519.SH",
            "trade_date": "2026-08-26",
            "final_trade_decision": "买入",
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "investment_debate_state": {},
            "risk_debate_state": {},
            "trader_investment_plan": "",
            "market_data_context": state.get("market_data_context"),
            "social_data_context": state.get("social_data_context"),
        }

    ta.graph.invoke = MagicMock(side_effect=_capture)
    ta._log_state = MagicMock()
    ta.process_signal = MagicMock(return_value="BUY")

    final_state, _ = ta.propagate("600519.SH", "2026-08-26")
    assert captured_state["social_data_context"] == social_ctx
    assert final_state["social_data_context"] == social_ctx


def test_propagate_async_injects_and_returns_social_data_context():
    """TradingAgentsGraph.propagate_async extracts social_data_context and returns in payload."""
    ta = _make_mock_graph_instance()
    social_ctx = create_default_social_data_context(
        status=SocialStatus.AVAILABLE.value,
        mode="active",
        requested_as_of="2026-08-26",
    )
    ta.data_collector.collect = MagicMock(
        return_value={
            "market_data_context": {"daily": {"as_of": "2026-08-26"}},
            "social_data_context": social_ctx,
        }
    )
    ta.data_collector.evict = MagicMock()

    captured_state = {}

    async def _capture_async(state, **_kwargs):
        captured_state.update(state)
        return {
            "company_of_interest": "600519.SH",
            "trade_date": "2026-08-26",
            "horizon": state["horizon"],
            "final_trade_decision": "买入",
            "investment_plan": "",
            "trader_investment_plan": "",
            "analyst_traces": [],
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "macro_report": "",
            "smart_money_report": "",
            "volume_price_report": "",
            "market_data_context": state.get("market_data_context"),
            "social_data_context": state.get("social_data_context"),
        }

    ta.graph.ainvoke = AsyncMock(side_effect=_capture_async)

    result = asyncio.run(ta.propagate_async("600519.SH", "2026-08-26"))

    assert captured_state["social_data_context"] == social_ctx
    assert result["social_data_context"] == social_ctx
    assert result["short_term"]["social_data_context"] == social_ctx


def test_log_state_includes_safe_social_data_context_summary():
    """M4: _log_state records safe summary of social_data_context (mode, status, direction_allowed, reason counts).

    Strictly forbids raw text/content or sensitive cookie fields.
    """
    ta = _make_mock_graph_instance()
    social_ctx = {
        "mode": "active",
        "status": "available",
        "requested_as_of": "2026-08-26",
        "direction_allowed": True,
        "reason_codes": ["code_1", "code_2"],
        "bundle": {
            "bundle_id": "sha256:bundle123",
            "evidence_summary": [
                {"record_id": "r1", "text": "敏感正文内容不应入日志"},
                {"record_id": "r2", "text": "另一条正文内容"},
            ],
            "cookie": "sensitive_session_cookie=abcdef",
        },
        "data_failure_ledger": [{"source": "s1", "status": "failed"}],
    }
    final_state = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-26",
        "market_report": "m",
        "sentiment_report": "s",
        "news_report": "n",
        "fundamentals_report": "f",
        "investment_debate_state": {
            "bull_history": "", "bear_history": "", "history": "",
            "current_response": "", "judge_decision": "",
        },
        "trader_investment_plan": "",
        "risk_debate_state": {
            "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "history": "", "judge_decision": "",
        },
        "investment_plan": "",
        "final_trade_decision": "BUY",
        "social_data_context": social_ctx,
    }

    ta._log_state("2026-08-26", final_state)
    logged = ta.log_states_dict.get("2026-08-26")
    assert logged is not None
    assert "social_data_context" in logged

    summary = logged["social_data_context"]
    assert summary["mode"] == "active"
    assert summary["status"] == "available"
    assert summary["requested_as_of"] == "2026-08-26"
    assert summary["direction_allowed"] is True
    assert summary["reason_count"] == 2
    assert summary["reason_codes"] == ["code_1", "code_2"]
    assert summary["ledger_count"] == 1
    assert summary["evidence_count"] == 2
    assert summary["bundle_id"] == "sha256:bundle123"

    # Verify no raw body text or cookies in the logged summary
    summary_str = str(summary)
    assert "敏感正文内容不应入日志" not in summary_str
    assert "sensitive_session_cookie" not in summary_str
