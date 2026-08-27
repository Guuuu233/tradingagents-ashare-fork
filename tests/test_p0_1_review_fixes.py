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
