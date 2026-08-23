"""Tests for debate protocol metadata, feature flags, and backward compatible serialization (P1-M1)."""

import json
import pytest
from tradingagents.agents.utils.agent_states import (
    DEFAULT_FEATURE_FLAGS,
    DEFAULT_PROTOCOL_METADATA,
    InvestDebateState,
    get_protocol_metadata,
    normalize_protocol_metadata,
)
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
