"""D-009 P0-1: research_manager short-circuits 7/7 failures to INVALID_RUN/NO_TRADE."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.utils.decision_status import (
    ACTION_NO_TRADE,
    ANALYSIS_ABSTAIN,
    ANALYSIS_INVALID_RUN,
    DIRECTION_NA,
)


_FAIL = "分析报告生成失败：Error code: 502"
_OK = "市场技术报告：突破阻力位，均线多头排列，成交量温和放大，趋势明确。"


def _base_state(**overrides):
    state = {
        "macro_report": _OK,
        "market_report": _OK,
        "sentiment_report": _OK,
        "news_report": _OK,
        "fundamentals_report": _OK,
        "smart_money_report": _OK,
        "volume_price_report": _OK,
        "market_data_context": {
            "analysis_baseline_date": "2026-05-06",
            "source_provenance": {
                "stock_data": {"status": "available", "as_of": "2026-05-06"},
            },
        },
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_speaker": "",
            "current_response": "",
            "count": 0,
            "claims": [],
            "round_messages": [],
            "focus_claim_ids": [],
            "open_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "round_summary": "",
            "round_goal": "",
            "claim_counter": 0,
        },
        "fund_flow_consensus_guard": {
            "blocked": False,
            "direction_allowed": True,
            "status": "consensus",
        },
        "trade_date": "2026-05-06",
        "horizon": "medium",
        "data_gaps": [],
    }
    state.update(overrides)
    return state


def _run_manager(state):
    llm = MagicMock()
    calls = {"n": 0}

    async def _astream(*_a, **_k):
        calls["n"] += 1
        yield MagicMock(content="SHOULD_NOT_REACH")

    llm.astream = _astream
    memory = MagicMock()
    memory.get_memories.return_value = []
    node = create_research_manager(llm, memory)
    return asyncio.run(node(state)), calls


def test_seven_of_seven_failures_short_circuit_before_llm():
    state = _base_state(
        macro_report=_FAIL,
        market_report=_FAIL,
        sentiment_report=_FAIL,
        news_report=_FAIL,
        fundamentals_report=_FAIL,
        smart_money_report=_FAIL,
        volume_price_report=_FAIL,
    )
    result, calls = _run_manager(state)
    assert calls["n"] == 0
    assert result["analysis_status"] == ANALYSIS_INVALID_RUN
    assert result["trade_action"] == ACTION_NO_TRADE
    assert result["decision_status"]["direction"] == DIRECTION_NA
    assert result["manager_verdict"]["direction"] == DIRECTION_NA
    assert result["manager_verdict"]["winner"] == "tie"
    assert "INVALID_RUN" in result["investment_plan"] or "NO_TRADE" in result["investment_plan"]
    assert result["manager_verdict"]["consistency_check_passed"] is False
    assert result["run_integrity"]["all_required_failed"] is True
    # Must not look like a Neutral market view with confidence
    assert result["manager_verdict"].get("position_pct") == 0
    assert result["manager_verdict"].get("upside") is None


def test_fund_flow_block_is_abstain_not_neutral():
    state = _base_state(
        fund_flow_consensus_guard={
            "blocked": True,
            "direction_allowed": False,
            "status": "blocked",
        }
    )
    result, calls = _run_manager(state)
    assert calls["n"] == 0
    assert result["analysis_status"] == ANALYSIS_ABSTAIN
    assert result["trade_action"] == ACTION_NO_TRADE
    assert result["manager_verdict"]["direction"] == DIRECTION_NA
    assert "ABSTAIN" in result["investment_plan"]
    assert "Neutral/HOLD" in result["investment_plan"] or "不是 Neutral" in result["investment_plan"]


def test_trader_short_circuits_on_invalid_run_status():
    import asyncio
    from unittest.mock import MagicMock

    from tradingagents.agents.trader.trader import create_trader
    from tradingagents.agents.utils.decision_status import invalid_run_status

    status = invalid_run_status(failure_class="DATA_ERROR").to_dict()
    state = {
        "company_of_interest": "300433.SZ",
        "investment_plan": "INVALID_RUN NO_TRADE",
        "trader_investment_plan": "",
        "market_report": "x",
        "sentiment_report": "x",
        "news_report": "x",
        "fundamentals_report": "x",
        "risk_feedback_state": {},
        "fund_flow_consensus_guard": {
            "blocked": False,
            "direction_allowed": True,
            "status": "consensus",
        },
        "decision_status": status,
        "analysis_status": status["analysis_status"],
        "trade_action": status["trade_action"],
        "instrument_context": {},
        "market_context": {},
        "user_context": {},
    }
    llm = MagicMock()
    calls = {"n": 0}

    async def _astream(*_a, **_k):
        calls["n"] += 1
        yield MagicMock(content="BUY")

    llm.astream = _astream
    memory = MagicMock()
    memory.get_memories.return_value = []
    node = create_trader(llm, memory)
    result = asyncio.run(node(state))
    assert calls["n"] == 0
    assert "NO_TRADE" in result["trader_investment_plan"]
    assert result["analysis_status"] == ANALYSIS_INVALID_RUN
    assert result["trade_action"] == ACTION_NO_TRADE


def test_risk_manager_short_circuits_on_abstain_status():
    import asyncio
    from unittest.mock import MagicMock

    from tradingagents.agents.managers.risk_manager import create_risk_manager
    from tradingagents.agents.utils.decision_status import abstain_status

    status = abstain_status().to_dict()
    state = {
        "company_of_interest": "300433.SZ",
        "trader_investment_plan": "伪造的买入计划",
        "market_report": "x",
        "news_report": "x",
        "fundamentals_report": "x",
        "sentiment_report": "x",
        "risk_debate_state": {
            "history": "",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "latest_speaker": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": "",
            "count": 0,
            "claims": [],
            "focus_claim_ids": [],
            "open_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "round_summary": "",
            "round_goal": "",
            "claim_counter": 0,
        },
        "risk_feedback_state": {},
        "fund_flow_consensus_guard": {
            "blocked": False,
            "direction_allowed": True,
            "status": "consensus",
        },
        "decision_status": status,
        "analysis_status": status["analysis_status"],
        "trade_action": status["trade_action"],
        "instrument_context": {},
        "market_context": {},
        "user_context": {},
    }
    llm = MagicMock()
    calls = {"n": 0}

    async def _astream(*_a, **_k):
        calls["n"] += 1
        yield MagicMock(content="批准买入")

    llm.astream = _astream
    memory = MagicMock()
    memory.get_memories.return_value = []
    node = create_risk_manager(llm, memory)
    result = asyncio.run(node(state))
    assert calls["n"] == 0
    assert "NO_TRADE" in result["final_trade_decision"]
    assert result["risk_feedback_state"]["latest_risk_verdict"] == "blocked"
    assert result["analysis_status"] == ANALYSIS_ABSTAIN
    assert result["trade_action"] == ACTION_NO_TRADE
    assert result["risk_status"] == "BLOCKED"


def test_consistency_fail_status_blocks_trader_llm():
    """Hard-gate failure must stamp ABSTAIN/NO_TRADE so Trader astream is never called."""
    import asyncio
    from unittest.mock import MagicMock

    from tradingagents.agents.trader.trader import create_trader
    from tradingagents.agents.utils.decision_status import status_from_manager_verdict

    status = status_from_manager_verdict(
        {
            "direction": "看多",
            "winner": "bull",
            "consistency_check_passed": False,
            "failed_checks":["仓位与方向冲突"],
        }
    )
    assert status.analysis_status == ANALYSIS_ABSTAIN
    assert status.trade_action == ACTION_NO_TRADE

    state = {
        "company_of_interest": "300433.SZ",
        "investment_plan": "研究总监裁决自洽硬闸未通过",
        "trader_investment_plan": "",
        "market_report": "x",
        "sentiment_report": "x",
        "news_report": "x",
        "fundamentals_report": "x",
        "risk_feedback_state": {},
        "fund_flow_consensus_guard": {
            "blocked": False,
            "direction_allowed": True,
            "status": "consensus",
        },
        "decision_status": status.to_dict(),
        "analysis_status": status.analysis_status,
        "trade_action": status.trade_action,
        "instrument_context": {},
        "market_context": {},
        "user_context": {},
    }
    llm = MagicMock()
    calls = {"n": 0}

    async def _astream(*_a, **_k):
        calls["n"] += 1
        yield MagicMock(content="BUY")

    llm.astream = _astream
    memory = MagicMock()
    memory.get_memories.return_value = []
    node = create_trader(llm, memory)
    result = asyncio.run(node(state))
    assert calls["n"] == 0
    assert "NO_TRADE" in result["trader_investment_plan"]
