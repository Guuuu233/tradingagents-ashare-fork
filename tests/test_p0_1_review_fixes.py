"""Regression tests for D-009 P0-1 review findings."""

from __future__ import annotations

from tradingagents.agents.utils.decision_status import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_NO_TRADE,
    ANALYSIS_INVALID_RUN,
    ANALYSIS_PARTIAL,
    ANALYSIS_VALID,
    DIRECTION_NA,
    aggregate_horizon_decision_statuses,
    db_direction_from_canonical,
    invalid_run_status,
    valid_status,
)
from tradingagents.agents.utils.run_integrity import (
    create_run_integrity_gate,
    evaluate_run_integrity,
    is_failed_analyst_report,
)
from tradingagents.graph.conditional_logic import ConditionalLogic


_OK = "市场技术报告：突破阻力位，均线多头排列，成交量温和放大，趋势明确。补充说明：盘中曾有【数据获取失败】提示但已切换备用源完成分析。"
_FAIL = "分析报告生成失败：Error code: 502"


def test_long_report_with_local_data_gap_is_not_failed():
    failed, reason = is_failed_analyst_report(_OK)
    assert failed is False
    assert reason is None


def test_whole_report_502_wrapper_is_failed():
    failed, reason = is_failed_analyst_report(_FAIL)
    assert failed is True
    assert reason and reason.startswith("prefix:")


def test_partial_failures_emit_partial_status():
    reports = {
        "market_report": _FAIL,
        "sentiment_report": _OK,
        "news_report": _OK,
        "fundamentals_report": _OK,
        "macro_report": _OK,
        "smart_money_report": _OK,
        "volume_price_report": _OK,
    }
    integrity = evaluate_run_integrity(reports)
    assert integrity.all_required_failed is False
    assert integrity.failed_required_count == 1
    assert integrity.analysis_status == ANALYSIS_PARTIAL
    assert integrity.decision_status["trade_action"] == ACTION_NO_TRADE


def test_integrity_gate_zero_downstream_llm_on_full_graph_slice():
    """Mini-graph: after analysts, INVALID_RUN must END with zero Bull/Bear calls."""
    import asyncio
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    class _S(TypedDict, total=False):
        market_report: str
        sentiment_report: str
        news_report: str
        fundamentals_report: str
        macro_report: str
        smart_money_report: str
        volume_price_report: str
        investment_debate_state: dict
        integrity_route: str
        analysis_status: str
        trade_action: str
        decision_status: dict
        run_integrity: dict
        investment_plan: str
        trader_investment_plan: str
        final_trade_decision: str
        manager_verdict: dict

    calls = {"bull": 0, "bear": 0}

    def bull(state):
        calls["bull"] += 1
        return {}

    def bear(state):
        calls["bear"] += 1
        return {}

    logic = ConditionalLogic()
    g = StateGraph(_S)
    g.add_node("Run Integrity Gate", create_run_integrity_gate())
    g.add_node("Bull Researcher", bull)
    g.add_node("Bear Researcher", bear)
    g.add_edge(START, "Run Integrity Gate")
    g.add_conditional_edges(
        "Run Integrity Gate",
        logic.should_continue_after_integrity,
        {"Bull Researcher": "Bull Researcher", "END": END},
    )
    g.add_edge("Bull Researcher", "Bear Researcher")
    g.add_edge("Bear Researcher", END)
    app = g.compile()

    failed_state = {
        "market_report": _FAIL,
        "sentiment_report": _FAIL,
        "news_report": _FAIL,
        "fundamentals_report": _FAIL,
        "macro_report": _FAIL,
        "smart_money_report": _FAIL,
        "volume_price_report": _FAIL,
        "investment_debate_state": {},
    }
    out = app.invoke(failed_state)
    assert calls["bull"] == 0
    assert calls["bear"] == 0
    assert out.get("analysis_status") == ANALYSIS_INVALID_RUN
    assert out.get("trade_action") == ACTION_NO_TRADE


def test_integrity_gate_routes_to_bull_when_reports_ok():
    state = {
        "market_report": _OK,
        "sentiment_report": _OK,
        "news_report": _OK,
        "fundamentals_report": _OK,
        "macro_report": _OK,
        "smart_money_report": _OK,
        "volume_price_report": _OK,
        "investment_debate_state": {},
    }
    out = create_run_integrity_gate()(state)
    assert out["integrity_route"] == "Bull Researcher"
    logic = ConditionalLogic()
    assert logic.should_continue_after_integrity({**state, **out}) == "Bull Researcher"


def test_graph_wires_integrity_gate_before_bull():
    # Inspect setup_graph source wiring without compiling LLM tool graphs.
    import inspect
    from tradingagents.graph import setup as setup_mod

    src = inspect.getsource(setup_mod.GraphSetup.setup_graph)
    assert 'workflow.add_node("Run Integrity Gate"' in src or "Run Integrity Gate" in src
    assert 'workflow.add_edge(phase2_dones, "Run Integrity Gate")' in src
    assert 'workflow.add_edge(phase2_dones, "Bull Researcher")' not in src
    assert "should_continue_after_integrity" in src


def test_dual_horizon_aggregation_three_modes():
    invalid = {
        "status": "completed",
        "decision_status": invalid_run_status().to_dict(),
        "analysis_status": ANALYSIS_INVALID_RUN,
        "trade_action": ACTION_NO_TRADE,
        "direction": DIRECTION_NA,
    }
    valid = {
        "status": "completed",
        "decision_status": valid_status(direction="BULL", trade_action=ACTION_BUY).to_dict(),
        "analysis_status": ANALYSIS_VALID,
        "trade_action": ACTION_BUY,
        "direction": "BULL",
    }
    all_invalid = aggregate_horizon_decision_statuses(
        {"short": invalid, "medium": invalid},
        requested_horizons=["short", "medium"],
    )
    assert all_invalid["aggregation"] == "all_invalid"
    assert all_invalid["trade_action"] == ACTION_NO_TRADE

    mixed = aggregate_horizon_decision_statuses(
        {"short": valid, "medium": invalid},
        requested_horizons=["short", "medium"],
    )
    assert mixed["aggregation"] == "mixed"
    assert mixed["analysis_status"] == ANALYSIS_PARTIAL
    assert mixed["trade_action"] == ACTION_NO_TRADE

    all_valid = aggregate_horizon_decision_statuses(
        {"short": valid, "medium": valid},
        requested_horizons=["short", "medium"],
    )
    assert all_valid["aggregation"] == "all_valid"
    assert all_valid["analysis_status"] == ANALYSIS_VALID


def test_db_direction_not_neutral_for_invalid():
    direction = db_direction_from_canonical(
        {
            "analysis_status": ANALYSIS_INVALID_RUN,
            "trade_action": ACTION_NO_TRADE,
            "direction": DIRECTION_NA,
        },
        fallback="中性",
    )
    assert direction == DIRECTION_NA
    direction_valid = db_direction_from_canonical(
        {
            "analysis_status": ANALYSIS_VALID,
            "trade_action": ACTION_HOLD,
            "direction": "NEUTRAL",
        },
        fallback=None,
    )
    assert direction_valid == "中性"


def test_db_direction_preserves_bull_on_risk_reject_no_trade():
    """D-009: BULL + Risk BLOCKED -> keep BULL direction, trade_action NO_TRADE."""
    direction = db_direction_from_canonical(
        {
            "analysis_status": ANALYSIS_VALID,
            "trade_action": ACTION_NO_TRADE,
            "direction": "BULL",
            "risk_status": "BLOCKED",
        },
        fallback=None,
    )
    assert direction == "看多"


def test_trader_invokes_llm_on_risk_revise():
    """When Risk Judge returns revise, Trader must call LLM (astream >= 1)."""
    import asyncio
    from unittest.mock import MagicMock

    from tradingagents.agents.trader.trader import create_trader
    from tradingagents.agents.utils.decision_status import (
        status_from_risk_verdict,
        valid_status,
    )

    upstream = valid_status(direction="BULL", trade_action=ACTION_BUY, confidence=80, probability=0.75)
    revised_status = status_from_risk_verdict(upstream=upstream, risk_verdict="revise", retry_count=1, max_retries=1)

    state = {
        "company_of_interest": "300433.SZ",
        "investment_plan": "原投资计划",
        "trader_investment_plan": "原交易员计划",
        "market_report": "市场报告",
        "sentiment_report": "情绪报告",
        "news_report": "新闻报告",
        "fundamentals_report": "基本面报告",
        "risk_feedback_state": {
            "latest_risk_verdict": "revise",
            "revision_required": True,
            "retry_count": 1,
            "max_retries": 1,
            "hard_constraints": ["降低仓位至20%以内"],
        },
        "fund_flow_consensus_guard": {
            "blocked": False,
            "direction_allowed": True,
            "status": "consensus",
        },
        "decision_status": revised_status.to_dict(),
        "analysis_status": revised_status.analysis_status,
        "trade_action": revised_status.trade_action,
        "risk_status": revised_status.risk_status,
        "direction": revised_status.direction,
        "instrument_context": {},
        "market_context": {},
        "user_context": {},
    }
    llm = MagicMock()
    calls = {"n": 0}

    async def _astream(*_a, **_k):
        calls["n"] += 1
        yield MagicMock(content="修改后的交易计划：仓位降至15%")

    llm.astream = _astream
    memory = MagicMock()
    memory.get_memories.return_value = []
    node = create_trader(llm, memory)
    result = asyncio.run(node(state))
    assert calls["n"] >= 1
    assert "修改后的交易计划" in result["trader_investment_plan"]


def test_should_revise_after_risk_judge_routing_and_non_executable_defense():
    """should_revise returns Trader for valid revise, but END if canonical is non-executable."""
    logic = ConditionalLogic()

    # 1. Valid revise -> Trader
    valid_state = {
        "analysis_status": ANALYSIS_VALID,
        "trade_action": ACTION_BUY,
        "direction": "BULL",
        "risk_status": "ELEVATED",
        "risk_feedback_state": {
            "revision_required": True,
            "retry_count": 1,
            "max_retries": 1,
        },
    }
    assert logic.should_revise_after_risk_judge(valid_state) == "Trader"

    # 2. Non-executable status -> must return END even if revision_required is True
    non_exec_state = {
        "analysis_status": "ABSTAIN",
        "trade_action": ACTION_NO_TRADE,
        "direction": DIRECTION_NA,
        "risk_status": "BLOCKED",
        "risk_feedback_state": {
            "revision_required": True,
            "retry_count": 1,
            "max_retries": 1,
        },
    }
    assert logic.should_revise_after_risk_judge(non_exec_state) == "END"


def test_should_revise_after_risk_judge_guards_retry_exhaustion():
    """Consecutive retries exhausted (retry_count > max_retries) -> must return END."""
    logic = ConditionalLogic()
    exhausted_state = {
        "analysis_status": ANALYSIS_VALID,
        "trade_action": ACTION_BUY,
        "direction": "BULL",
        "risk_status": "ELEVATED",
        "risk_feedback_state": {
            "revision_required": True,
            "retry_count": 2,
            "max_retries": 1,
        },
    }
    assert logic.should_revise_after_risk_judge(exhausted_state) == "END"


def test_trader_conditional_routing_skips_risk_debate_analysts_on_non_executable():
    """When status is non-executable or fund_flow is blocked, Trader routes to Risk Judge (skips Aggressive Analyst)."""
    logic = ConditionalLogic()

    # Valid executable state -> routes to Aggressive Analyst
    exec_state = {
        "analysis_status": ANALYSIS_VALID,
        "trade_action": ACTION_BUY,
        "direction": "BULL",
        "fund_flow_consensus_guard": {"blocked": False, "direction_allowed": True},
    }
    assert logic.should_continue_after_trader(exec_state) == "Aggressive Analyst"

    # Non-executable state -> routes to Risk Judge
    non_exec_state = {
        "analysis_status": "ABSTAIN",
        "trade_action": ACTION_NO_TRADE,
        "direction": DIRECTION_NA,
        "fund_flow_consensus_guard": {"blocked": False, "direction_allowed": True},
    }
    assert logic.should_continue_after_trader(non_exec_state) == "Risk Judge"

    # Fund flow blocked -> routes to Risk Judge
    ff_blocked_state = {
        "analysis_status": ANALYSIS_VALID,
        "trade_action": ACTION_BUY,
        "fund_flow_consensus_guard": {"blocked": True, "direction_allowed": False},
    }
    assert logic.should_continue_after_trader(ff_blocked_state) == "Risk Judge"
