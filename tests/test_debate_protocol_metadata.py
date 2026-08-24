"""Tests for debate protocol metadata, feature flags, and backward compatible serialization (P1-M1)."""

import copy
import json
from unittest.mock import MagicMock, patch
import pytest
from tradingagents.agents.utils.agent_states import (
    DEFAULT_FEATURE_FLAGS,
    DEFAULT_PROTOCOL_METADATA,
    InvestDebateState,
    get_protocol_metadata,
    normalize_protocol_metadata,
)
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph
from api.services.report_service import canonicalize_report_result_data


def test_default_feature_flags():
    """Verify exact default feature flags specified in v2 P1-M."""
    assert DEFAULT_FEATURE_FLAGS == {
        "v2_debate_enabled": False,
        "shadow_credit_enabled": True,
        "credit_weighting_enabled": False,
    }


def test_default_protocol_metadata():
    """Verify exact default protocol metadata structure."""
    expected = {
        "protocol_version": "v1_legacy",
        "protocol_stage": "opening",
        "tiebreak_skipped": False,
        "debate_degenerate": False,
        "data_utilization_metrics": {},
        "challenge_verification": [],
        "shadow_credit_metrics": {},
        "feature_flags": {
            "v2_debate_enabled": False,
            "shadow_credit_enabled": True,
            "credit_weighting_enabled": False,
        },
    }
    assert DEFAULT_PROTOCOL_METADATA == expected


def test_legacy_report_missing_fields_reads_as_v1_legacy():
    """When an old report/state has no protocol fields, it must read as v1_legacy."""
    legacy_result_data = {
        "market_report": "市场报告",
        "investment_debate_state": {
            "history": "辩论历史",
            "claims": [{"claim_id": "INV-1", "claim": "看多理由"}],
        },
    }
    meta = get_protocol_metadata(legacy_result_data)
    assert meta["protocol_version"] == "v1_legacy"
    assert meta["protocol_stage"] == "opening"
    assert meta["tiebreak_skipped"] is False
    assert meta["debate_degenerate"] is False
    assert meta["data_utilization_metrics"] == {}
    assert meta["challenge_verification"] == []
    assert meta["shadow_credit_metrics"] == {}
    assert meta["feature_flags"] == {
        "v2_debate_enabled": False,
        "shadow_credit_enabled": True,
        "credit_weighting_enabled": False,
    }


def test_legacy_debate_state_missing_fields_reads_as_v1_legacy():
    """When investment_debate_state has missing fields, normalize_protocol_metadata fills defaults."""
    legacy_debate_state = {
        "bull_history": "多方发言",
        "bear_history": "空方发言",
        "count": 6,
    }
    meta = normalize_protocol_metadata(legacy_debate_state)
    assert meta["protocol_version"] == "v1_legacy"
    assert meta["protocol_stage"] == "opening"
    assert meta["tiebreak_skipped"] is False
    assert meta["debate_degenerate"] is False
    assert meta["feature_flags"]["v2_debate_enabled"] is False


def test_v2_structured_disagreement_fields_preserved():
    """V2 structured disagreement fields are preserved without loss during normalization and serialization."""
    v2_data = {
        "protocol_version": "v2_structured_disagreement",
        "protocol_stage": "challenge",
        "tiebreak_skipped": True,
        "debate_degenerate": False,
        "data_utilization_metrics": {
            "seven_reports_rate": 0.75,
            "macro_rate": 0.60,
        },
        "challenge_verification": [
            {"target_claim_id": "INV-1", "severity": "fatal", "status": "verified"}
        ],
        "shadow_credit_metrics": {
            "bull_verified_rate": 0.85,
            "bear_verified_rate": 0.80,
        },
        "feature_flags": {
            "v2_debate_enabled": True,
            "shadow_credit_enabled": True,
            "credit_weighting_enabled": False,
        },
    }
    meta = normalize_protocol_metadata(v2_data)
    assert meta["protocol_version"] == "v2_structured_disagreement"
    assert meta["protocol_stage"] == "challenge"
    assert meta["tiebreak_skipped"] is True
    assert meta["debate_degenerate"] is False
    assert meta["data_utilization_metrics"] == {
        "seven_reports_rate": 0.75,
        "macro_rate": 0.60,
    }
    assert meta["challenge_verification"] == [
        {"target_claim_id": "INV-1", "severity": "fatal", "status": "verified"}
    ]
    assert meta["shadow_credit_metrics"] == {
        "bull_verified_rate": 0.85,
        "bear_verified_rate": 0.80,
    }
    assert meta["feature_flags"] == {
        "v2_debate_enabled": True,
        "shadow_credit_enabled": True,
        "credit_weighting_enabled": False,
    }


def test_canonicalize_report_result_data_roundtrip_with_protocol_fields():
    """canonicalize_report_result_data preserves protocol metadata at DB/API boundary."""
    raw_result_data = {
        "market_report": "市场分析报告正文",
        "protocol_version": "v2_structured_disagreement",
        "protocol_stage": "manager",
        "tiebreak_skipped": False,
        "debate_degenerate": False,
        "data_utilization_metrics": {"numerator": 15, "denominator": 20, "rate": 0.75},
        "challenge_verification": [
            {"challenge_id": "CHAL-1", "target_claim_id": "INV-2", "status": "verified"}
        ],
        "shadow_credit_metrics": {"bull_score": 85.5, "bear_score": 82.0},
        "feature_flags": {
            "v2_debate_enabled": False,
            "shadow_credit_enabled": True,
            "credit_weighting_enabled": False,
        },
        "investment_debate_state": {
            "protocol_version": "v2_structured_disagreement",
            "protocol_stage": "manager",
            "tiebreak_skipped": False,
            "debate_degenerate": False,
            "history": "辩论内容",
            "claims": [],
        },
    }
    canonical = canonicalize_report_result_data(raw_result_data)
    assert canonical is not None
    assert canonical["protocol_version"] == "v2_structured_disagreement"
    assert canonical["protocol_stage"] == "manager"
    assert canonical["tiebreak_skipped"] is False
    assert canonical["data_utilization_metrics"] == {"numerator": 15, "denominator": 20, "rate": 0.75}
    assert canonical["challenge_verification"] == [
        {"challenge_id": "CHAL-1", "target_claim_id": "INV-2", "status": "verified"}
    ]
    assert canonical["shadow_credit_metrics"] == {"bull_score": 85.5, "bear_score": 82.0}
    assert canonical["feature_flags"]["v2_debate_enabled"] is False

    # JSON serialization roundtrip test
    dumped = json.dumps(canonical, ensure_ascii=False)
    loaded = json.loads(dumped)
    assert loaded["protocol_version"] == "v2_structured_disagreement"
    assert loaded["feature_flags"] == {
        "v2_debate_enabled": False,
        "shadow_credit_enabled": True,
        "credit_weighting_enabled": False,
    }


def test_invest_debate_state_typeddict_typing():
    """Verify InvestDebateState TypedDict supports the new fields."""
    state: InvestDebateState = {
        "bull_history": "多方",
        "bear_history": "空方",
        "history": "历史",
        "current_speaker": "Bull",
        "current_response": "回应",
        "bull_initial": "",
        "bear_initial": "",
        "bull_rebuttal": "",
        "bear_rebuttal": "",
        "judge_decision": "",
        "count": 2,
        "claims": [],
        "round_messages": [],
        "focus_claim_ids": [],
        "open_claim_ids": [],
        "resolved_claim_ids": [],
        "unresolved_claim_ids": [],
        "round_summary": "",
        "round_goal": "",
        "claim_counter": 0,
        "manager_verdict": {},
        "evidence_verification": [],
        "report_manifest": {},
        "attempts": [],
        "blocked": False,
        "parse_status": "ok",
        "block_reason": "",
        "protocol_version": "v1_legacy",
        "protocol_stage": "opening",
        "tiebreak_skipped": False,
        "debate_degenerate": False,
        "data_utilization_metrics": {},
        "challenge_verification": [],
        "shadow_credit_metrics": {},
        "feature_flags": DEFAULT_FEATURE_FLAGS.copy(),
    }
    assert state["protocol_version"] == "v1_legacy"
    assert state["feature_flags"]["v2_debate_enabled"] is False


def test_propagator_create_initial_state_has_default_protocol_metadata():
    """RED 1: Propagator.create_initial_state must populate default protocol metadata on investment_debate_state."""
    propagator = Propagator()
    state = propagator.create_initial_state("601318.SH", "2026-08-21")
    inv = state["investment_debate_state"]

    assert inv.get("protocol_version") == "v1_legacy"
    assert inv.get("protocol_stage") == "opening"
    assert inv.get("tiebreak_skipped") is False
    assert inv.get("debate_degenerate") is False
    assert inv.get("data_utilization_metrics") == {}
    assert inv.get("challenge_verification") == []
    assert inv.get("shadow_credit_metrics") == {}
    assert inv.get("feature_flags") == {
        "v2_debate_enabled": False,
        "shadow_credit_enabled": True,
        "credit_weighting_enabled": False,
    }

    # Verify deepcopy isolation: mutating inv's nested dicts must not affect DEFAULT_PROTOCOL_METADATA
    inv["feature_flags"]["v2_debate_enabled"] = True
    assert DEFAULT_PROTOCOL_METADATA["feature_flags"]["v2_debate_enabled"] is False
    inv["data_utilization_metrics"]["test"] = 123
    assert DEFAULT_PROTOCOL_METADATA["data_utilization_metrics"] == {}


def test_build_horizon_result_mounts_protocol_metadata_and_metrics():
    """RED 2: TradingAgentsGraph._build_horizon_result mounts canonical protocol metadata and calculated metrics."""
    with patch("tradingagents.graph.trading_graph.create_llm_client"), \
         patch("tradingagents.graph.trading_graph.FinancialSituationMemory"), \
         patch("tradingagents.graph.trading_graph.GraphSetup"), \
         patch("tradingagents.graph.trading_graph.ConditionalLogic"), \
         patch("tradingagents.graph.trading_graph.Propagator"), \
         patch("tradingagents.graph.trading_graph.Reflector"), \
         patch("tradingagents.graph.trading_graph.SignalProcessor"), \
         patch("tradingagents.graph.trading_graph.set_config"):
        ta = TradingAgentsGraph.__new__(TradingAgentsGraph)
        ta.debug = False
        ta.config = {}
        ta.callbacks = []
        ta.ticker = None
        ta.log_states_dict = {}

    initial_inv_state = Propagator().create_initial_state("601318.SH", "2026-08-21")["investment_debate_state"]
    initial_inv_state.update({
        "history": "辩论内容",
        "bull_history": "多头发言",
        "bear_history": "空头发言",
        "current_speaker": "Bull",
        "current_response": "多头观点",
        "judge_decision": "多头胜",
        "count": 4,
        "claims": [
            {"claim_id": "INV-1", "speaker_key": "Bull", "evidence": ["营收 150 亿", "毛利率 25%"]},
        ],
        "round_messages": [],
        "focus_claim_ids": [],
        "open_claim_ids": [],
        "resolved_claim_ids": ["INV-1"],
        "unresolved_claim_ids": [],
        "round_summary": "第一轮总结",
        "round_goal": "多空辩论",
        "claim_counter": 1,
        "manager_verdict": {
            "claim_evidence_summary": {
                "INV-1": {"speaker_key": "Bull", "counts": {"total": 2, "verified": 2}},
            }
        },
    })

    final_state = {
        "company_of_interest": "601318.SH",
        "trade_date": "2026-08-21",
        "horizon": "short",
        "market_report": "支撑位 50.0 元",
        "fundamentals_report": "营业收入 150 亿，毛利率 25%",
        "macro_report": "GDP 预期 3.5%，通过产业链传导与外围联动。",
        "sentiment_report": "",
        "news_report": "",
        "smart_money_report": "",
        "volume_price_report": "",
        "final_trade_decision": "买入",
        "investment_plan": "投资计划",
        "trader_investment_plan": "交易计划",
        "market_data_context": {"data_failure_ledger": []},
        "investment_debate_state": copy.deepcopy(initial_inv_state),
        "manager_verdict": {
            "claim_evidence_summary": {
                "INV-1": {"speaker_key": "Bull", "counts": {"total": 2, "verified": 2}},
            }
        },
    }

    # Snapshot of final_state before call to verify observer pure function (no mutation)
    final_state_before = copy.deepcopy(final_state)

    result = ta._build_horizon_result("short", final_state)

    # 1. Verify observer pure function: final_state investment_debate_state was NOT mutated
    assert final_state["investment_debate_state"] == final_state_before["investment_debate_state"]

    # 2. Verify top-level and normalized metadata
    meta = get_protocol_metadata(result)
    assert meta["protocol_version"] == "v1_legacy"
    assert meta["protocol_stage"] == "opening"
    assert meta["tiebreak_skipped"] is False
    assert meta["debate_degenerate"] is False
    assert meta["feature_flags"] == {
        "v2_debate_enabled": False,
        "shadow_credit_enabled": True,
        "credit_weighting_enabled": False,
    }
    assert result.get("protocol_version") == "v1_legacy"
    assert result.get("feature_flags") == {
        "v2_debate_enabled": False,
        "shadow_credit_enabled": True,
        "credit_weighting_enabled": False,
    }

    # 3. Verify data_utilization_metrics output from real pure function
    metrics = result.get("data_utilization_metrics")
    assert isinstance(metrics, dict)
    assert "seven_reports_utilization" in metrics
    assert metrics["seven_reports_utilization"]["status"] == "valid"
    assert metrics["seven_reports_utilization"]["numerator"] >= 1
    assert metrics["seven_reports_utilization"]["denominator"] >= 1
    assert metrics["seven_reports_utilization"]["rate"] is not None
    assert metrics["seven_reports_utilization"]["version"] == "v1_legacy"

    # 4. Verify legacy challenge status is legacy_no_data
    challenge_m = metrics.get("challenge_metrics")
    assert challenge_m is not None
    assert challenge_m["challenge_count"]["status"] == "legacy_no_data"
    assert challenge_m["challenge_adoption_rate"]["status"] == "legacy_no_data"

    # 5. Verify existing fields, count, history are preserved verbatim
    inv_res = result.get("investment_debate_state")
    assert inv_res is not None
    assert inv_res["count"] == 4
    assert inv_res["bull_history"] == "多头发言"
    assert inv_res["bear_history"] == "空头发言"
    assert inv_res["judge_decision"] == "多头胜"
    assert result["final_trade_decision"] == "买入"
