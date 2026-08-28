"""Tests for P0-5b: confirmation_state hard gate and WAIT/NO_TRADE action routing.

Ensures that:
1. Unresolved focus/core claims with no verified evidence -> confirmation_state=UNRESOLVED, trade_action=WAIT.
2. Partial core claims verified -> confirmation_state=PARTIAL, trade_action=WAIT.
3. All core claims verified without fatal conflict -> confirmation_state=CONFIRMED, trade_action follows verdict mapping.
4. Fund flow guard / consistency hard gate take precedence over confirmation (ABSTAIN/NO_TRADE preserved).
5. Goertek minimal nail: simulation of unconfirmed core disagreement -> manager terminal status is WAIT,
   investment plan is WAIT/NO_TRADE and Trader does not generate buy execution plan.
6. winner=tie with unconfirmed claims -> WAIT (not eligible Neutral/HOLD trade action).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.trader.trader import create_trader
from tradingagents.agents.utils.agent_states import PROTOCOL_VERSION_V2_STRUCTURED
from tradingagents.agents.utils.decision_status import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_NO_TRADE,
    ACTION_SELL,
    ACTION_WAIT,
    ANALYSIS_ABSTAIN,
    ANALYSIS_INVALID_RUN,
    ANALYSIS_VALID,
    CONFIRM_CONFIRMED,
    CONFIRM_PARTIAL,
    CONFIRM_UNRESOLVED,
    DIRECTION_BEAR,
    DIRECTION_BULL,
    DIRECTION_NA,
    DIRECTION_NEUTRAL,
    DecisionStatus,
    apply_decision_status_to_result,
    is_calibration_eligible,
    is_non_executable_status,
    status_from_manager_verdict,
)


def _base_manager_state(**overrides):
    claims = [
        {
            "claim_id": "CLM-1",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "歌尔声学新产线良率已达98%且订单饱满",
            "evidence": ["产线良率报告", "订单排产表"],
            "confidence": 0.85,
            "battlefield": "fundamentals",
        },
        {
            "claim_id": "CLM-2",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "海外大客户三季度砍单20%导致产能闲置",
            "evidence": ["供应链传闻", "海外券商研报"],
            "confidence": 0.80,
            "battlefield": "macro_policy",
        },
    ]
    round_messages = [
        {
            "message_index": 1,
            "stage": "opening",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "parse_status": "valid",
            "accepted": True,
            "responded_claim_ids": [],
            "target_claim_ids": [],
            "new_claim_ids": ["CLM-1"],
            "information_gain_score": 1.0,
        },
        {
            "message_index": 2,
            "stage": "opening",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "parse_status": "valid",
            "accepted": True,
            "responded_claim_ids": [],
            "target_claim_ids": [],
            "new_claim_ids": ["CLM-2"],
            "information_gain_score": 1.0,
        },
        {
            "message_index": 3,
            "stage": "challenge",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "parse_status": "valid",
            "accepted": True,
            "responded_claim_ids": ["CLM-2"],
            "target_claim_ids": ["CLM-2"],
            "new_claim_ids": [],
            "challenges": [{"target_claim_id": "CLM-2", "weakest_point": "传闻未经官方证实", "evidence": ["未收到调整通知"], "severity": "major"}],
            "information_gain_score": 0.9,
        },
        {
            "message_index": 4,
            "stage": "challenge",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "parse_status": "valid",
            "accepted": True,
            "responded_claim_ids": ["CLM-1"],
            "target_claim_ids": ["CLM-1"],
            "new_claim_ids": [],
            "challenges": [{"target_claim_id": "CLM-1", "weakest_point": "爬坡期良率波动大", "evidence": ["历史良率仅85%"], "severity": "major"}],
            "information_gain_score": 0.88,
        },
    ]
    state = {
        "macro_report": "宏观经济报告：政策支持，流动性充裕。",
        "market_report": "市场技术报告：均线多头排列，量能稳步放大。",
        "sentiment_report": "市场情绪报告：情绪适度乐观，无过热迹象。",
        "news_report": "新闻舆情报告：行业订单增加，供应链恢复正常。",
        "fundamentals_report": "基本面分析报告：营收稳步增长，现金流充沛。",
        "smart_money_report": "主力资金报告：大单与超大单呈现净流入。",
        "volume_price_report": "量价分析报告：放量突破重要阻力位，形态健康。",
        "market_data_context": {
            "analysis_baseline_date": "2026-05-28",
            "source_provenance": {
                "stock_data": {"status": "available", "as_of": "2026-05-28"},
            },
        },
        "investment_debate_state": {
            "history": "多空双方围绕核心增长假设展开辩论。",
            "bull_history": "多头主张订单交付超预期。",
            "bear_history": "空头质疑产品良率与下半年砍单风险。",
            "current_speaker": "",
            "current_response": "",
            "count": 4,
            "claims": claims,
            "round_messages": round_messages,
            "challenges": [
                {"speaker_key": "Bull", "target_claim_id": "CLM-2", "message_index": 3},
                {"speaker_key": "Bear", "target_claim_id": "CLM-1", "message_index": 4},
            ],
            "focus_claim_ids": ["CLM-1", "CLM-2"],
            "open_claim_ids": ["CLM-1", "CLM-2"],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": ["CLM-1", "CLM-2"],
            "round_summary": "多空双方在良率与砍单核心分歧上未达成一致。",
            "round_goal": "核实新产线良率与砍单传闻",
            "claim_counter": 2,
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "feature_flags": {"v2_debate_enabled": True},
            "tiebreak_skipped": True,
        },
        "fund_flow_consensus_guard": {
            "blocked": False,
            "direction_allowed": True,
            "status": "consensus",
        },
        "trade_date": "2026-05-28",
        "horizon": "medium",
        "data_gaps": [],
    }
    state.update(overrides)
    return state


def _mock_llm(verdict_text: str):
    llm = MagicMock()
    calls = {"n": 0}

    async def _astream(*_a, **_k):
        calls["n"] += 1
        yield MagicMock(content=verdict_text)

    llm.astream = _astream
    return llm, calls


# ── Unit Tests for status_from_manager_verdict with confirmation ───────────────


def test_status_from_manager_verdict_unresolved_focus_claims_wait():
    """Unresolved focus claims without verified evidence -> UNRESOLVED + WAIT."""
    mv = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 60,
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    # Both CLM-1 and CLM-2 are unresolved with no verified evidence (unsupported)
    claims_verification = [
        {"claim_id": "CLM-1", "status": "unsupported", "raw": "产线良率报告未获证实"},
        {"claim_id": "CLM-2", "status": "unsupported", "raw": "海外砍单传闻未获证实"},
    ]
    status = status_from_manager_verdict(
        mv,
        focus_claim_ids=["CLM-1", "CLM-2"],
        unresolved_claim_ids=["CLM-1", "CLM-2"],
        claims_verification=claims_verification,
    )
    assert status.analysis_status == ANALYSIS_VALID
    assert status.confirmation_state == CONFIRM_UNRESOLVED
    assert status.trade_action == ACTION_WAIT
    assert status.direction == DIRECTION_BULL
    assert is_non_executable_status(status) is True
    assert is_calibration_eligible(status) is False


def test_status_from_manager_verdict_partial_focus_claims_wait():
    """Partial focus claims verified, others unverified -> PARTIAL + WAIT."""
    mv = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 50,
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    # CLM-1 is verified, CLM-2 is unsupported
    claims_verification = [
        {"claim_id": "CLM-1", "status": "verified", "raw": "产线良率98%已由基本面报告证实"},
        {"claim_id": "CLM-2", "status": "unsupported", "raw": "砍单传闻缺乏数据支撑"},
    ]
    claim_evidence_summary = {
        "CLM-1": {
            "counts": {"total": 1, "verified": 1, "unsupported": 0, "contradicted": 0, "source_unavailable": 0},
            "coverage": 1.0,
            "decision": "adopt",
        },
        "CLM-2": {
            "counts": {"total": 1, "verified": 0, "unsupported": 1, "contradicted": 0, "source_unavailable": 0},
            "coverage": 0.0,
            "decision": "reject",
        },
    }
    status = status_from_manager_verdict(
        mv,
        focus_claim_ids=["CLM-1", "CLM-2"],
        claims_verification=claims_verification,
        claim_evidence_summary=claim_evidence_summary,
    )
    assert status.confirmation_state == CONFIRM_PARTIAL
    assert status.trade_action == ACTION_WAIT
    assert status.direction == DIRECTION_BULL
    assert is_non_executable_status(status) is True


def test_status_from_manager_verdict_all_core_verified_confirmed():
    """All core focus claims verified without fatal conflict -> CONFIRMED + BUY."""
    mv = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 60,
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    claims_verification = [
        {"claim_id": "CLM-1", "status": "verified", "raw": "良率数据已证实"},
        {"claim_id": "CLM-2", "status": "verified", "raw": "砍单传闻已被澄清辟谣公告证实"},
    ]
    claim_evidence_summary = {
        "CLM-1": {
            "counts": {"total": 1, "verified": 1, "unsupported": 0, "contradicted": 0, "source_unavailable": 0},
            "coverage": 1.0,
            "decision": "adopt",
        },
        "CLM-2": {
            "counts": {"total": 1, "verified": 1, "unsupported": 0, "contradicted": 0, "source_unavailable": 0},
            "coverage": 1.0,
            "decision": "adopt",
        },
    }
    status = status_from_manager_verdict(
        mv,
        focus_claim_ids=["CLM-1", "CLM-2"],
        claims_verification=claims_verification,
        claim_evidence_summary=claim_evidence_summary,
    )
    assert status.confirmation_state == CONFIRM_CONFIRMED
    assert status.trade_action == ACTION_BUY
    assert status.direction == DIRECTION_BULL
    assert is_non_executable_status(status) is False


def test_status_from_manager_verdict_contradicted_core_claim_unresolved():
    """Core claim has contradicted/fatal evidence -> UNRESOLVED + WAIT."""
    mv = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 60,
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    claims_verification = [
        {"claim_id": "CLM-1", "status": "verified", "raw": "产线良率已证实"},
        {"claim_id": "CLM-2", "status": "contradicted", "raw": "公告显示砍单事实成立，多头假设被证伪", "is_fatal": True},
    ]
    status = status_from_manager_verdict(
        mv,
        focus_claim_ids=["CLM-1", "CLM-2"],
        claims_verification=claims_verification,
    )
    assert status.confirmation_state == CONFIRM_UNRESOLVED
    assert status.trade_action == ACTION_WAIT


def test_status_from_manager_verdict_tie_with_unresolved_claims_is_wait():
    """winner=tie with unresolved claims must be WAIT, not Neutral+HOLD."""
    mv = {
        "direction": "中性",
        "winner": "tie",
        "position_pct": 0,
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    claims_verification = [
        {"claim_id": "CLM-1", "status": "unsupported", "raw": "无证据"},
    ]
    status = status_from_manager_verdict(
        mv,
        focus_claim_ids=["CLM-1"],
        claims_verification=claims_verification,
    )
    assert status.confirmation_state == CONFIRM_UNRESOLVED
    assert status.trade_action == ACTION_WAIT
    assert status.trade_action != ACTION_HOLD
    assert is_non_executable_status(status) is True


def test_status_from_manager_verdict_tie_with_all_confirmed_is_hold():
    """winner=tie with no unconfirmed disputes -> CONFIRMED + HOLD."""
    mv = {
        "direction": "中性",
        "winner": "tie",
        "position_pct": 0,
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    status = status_from_manager_verdict(
        mv,
        focus_claim_ids=[],
        unresolved_claim_ids=[],
        claims_verification=[],
    )
    assert status.confirmation_state == CONFIRM_CONFIRMED
    assert status.trade_action == ACTION_HOLD


def test_prior_gates_take_precedence_over_confirmation():
    """Upstream INVALID_RUN and ABSTAIN are not downgraded to CONFIRMED."""
    # 1. Consistency failure -> ABSTAIN / NO_TRADE
    mv_inconsistent = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 60,
        "consistency_check_passed": False,
        "failed_checks": ["stop_loss_missing"],
    }
    st1 = status_from_manager_verdict(
        mv_inconsistent,
        focus_claim_ids=[],  # even if clean claims
    )
    assert st1.analysis_status == ANALYSIS_ABSTAIN
    assert st1.trade_action == ACTION_NO_TRADE
    assert st1.confirmation_state == CONFIRM_UNRESOLVED

    # 2. Upstream PARTIAL failure -> PARTIAL / NO_TRADE
    st2 = status_from_manager_verdict(
        {"direction": "看多", "winner": "bull", "consistency_check_passed": True},
        prior_analysis_status="PARTIAL",
    )
    assert st2.analysis_status == "PARTIAL"
    assert st2.trade_action == ACTION_NO_TRADE


# ── Integration: Goertek Nail & Research Manager Node ─────────────────────────


def test_goertek_nail_unconfirmed_core_disagreement_emits_wait():
    """Goertek sample: unconfirmed core disagreement -> manager emits WAIT and Trader does not buy."""
    state = _base_manager_state()

    # The LLM generates a bullish text verdict, but the debate has unverified focus claims
    verdict_text = """
### 辩论裁决与总结方案
【多空辩论五步深度裁决】
多头关于新产线良率的主张具有进攻性，空头关于砍单的质疑缺乏一手数据。
综合评定：多头胜。
建议仓位：50%，止损位：28.50元。
<!-- VERDICT: {"direction": "看多", "winner": "bull", "reason": "看好新产线交付", "position_pct": 50, "entry": "30.00", "target": "35.00", "stop_loss": "28.50", "confidence": 75, "probability": 0.70} -->
"""
    llm, calls = _mock_llm(verdict_text)
    memory = MagicMock()
    memory.get_memories.return_value = []
    manager_node = create_research_manager(llm, memory)

    manager_res = asyncio.run(manager_node(state))

    # Assertions on Research Manager output
    assert manager_res["analysis_status"] == ANALYSIS_VALID
    assert manager_res["confirmation_state"] == CONFIRM_UNRESOLVED
    assert manager_res["trade_action"] == ACTION_WAIT
    assert manager_res["decision_status"]["trade_action"] == ACTION_WAIT
    assert manager_res["decision_status"]["confirmation_state"] == CONFIRM_UNRESOLVED

    # Now pass state to Trader node: Trader must short-circuit and NOT generate buy orders
    trader_state = {
        "company_of_interest": "002241.SZ",
        "investment_plan": manager_res["investment_plan"],
        "trader_investment_plan": "",
        "market_report": state["market_report"],
        "sentiment_report": state["sentiment_report"],
        "news_report": state["news_report"],
        "fundamentals_report": state["fundamentals_report"],
        "risk_feedback_state": {},
        "fund_flow_consensus_guard": state["fund_flow_consensus_guard"],
        "decision_status": manager_res["decision_status"],
        "analysis_status": manager_res["analysis_status"],
        "trade_action": manager_res["trade_action"],
        "confirmation_state": manager_res["confirmation_state"],
        "instrument_context": {},
        "market_context": {},
        "user_context": {},
    }
    trader_llm, trader_calls = _mock_llm("次日开仓买入 50% 仓位")
    trader_node = create_trader(trader_llm, memory)
    trader_res = asyncio.run(trader_node(trader_state))

    assert trader_calls["n"] == 0, "Trader LLM must not be invoked when confirmation_state is UNRESOLVED / WAIT"
    assert "观望" in trader_res["trader_investment_plan"] or "WAIT" in trader_res["trader_investment_plan"] or "NO_TRADE" in trader_res["trader_investment_plan"]
    assert "买入 50%" not in trader_res["trader_investment_plan"]
    assert "次日开仓" not in trader_res["trader_investment_plan"]


def test_apply_decision_status_with_wait_strips_targets():
    """apply_decision_status_to_result on WAIT must strip price targets and set confirmation_state."""
    raw = {
        "target_price": 35.0,
        "stop_loss_price": 28.5,
        "confidence": 75,
        "probability": 0.7,
        "numeric_ranges": ["28.5-35.0"],
    }
    st = DecisionStatus(
        analysis_status=ANALYSIS_VALID,
        direction=DIRECTION_BULL,
        trade_action=ACTION_WAIT,
        risk_status="OK",
        confirmation_state=CONFIRM_UNRESOLVED,
    )
    result = apply_decision_status_to_result(raw, st)
    assert result["trade_action"] == ACTION_WAIT
    assert result["confirmation_state"] == CONFIRM_UNRESOLVED
    assert result["target_price"] is None
    assert result["stop_loss_price"] is None
    assert result["confidence"] is None
    assert result["probability"] is None
    assert result["numeric_ranges"] == []

