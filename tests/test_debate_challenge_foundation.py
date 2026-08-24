"""Unit tests for P1-B/B2-C1 Challenge Foundation: schema, sanitizer, Propagator initial state, and typeddict declarations."""

import copy
import math
import pytest

from tradingagents.agents.utils import agent_states
from tradingagents.agents.utils.agent_states import (
    InvestDebateState,
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
)
from tradingagents.agents.utils.debate_utils import (
    _sanitize_machine_payload,
)
from tradingagents.graph.propagation import Propagator


class TestPropagatorChallengeFoundationInitialState:
    """Requirement 1: Propagator initial state and deepcopy isolation for challenge fields."""

    def test_propagator_v2_initial_state_contains_challenge_fields(self):
        propagator = Propagator()
        state = propagator.create_initial_state(
            company_name="600519.SH",
            trade_date="2026-08-20",
            runtime_config={"v2_debate_enabled": True},
        )
        inv_state = state["investment_debate_state"]
        assert "challenges" in inv_state, "Propagator v2 initial state must contain 'challenges'"
        assert isinstance(inv_state["challenges"], list)
        assert len(inv_state["challenges"]) == 0
        assert inv_state.get("challenge_counter") == 0, "Propagator v2 initial state must contain challenge_counter=0"
        assert "challenge_verification" in inv_state
        assert isinstance(inv_state["challenge_verification"], list)
        assert len(inv_state["challenge_verification"]) == 0

    def test_propagator_v1_legacy_initial_state_contains_challenge_containers(self):
        propagator = Propagator()
        state = propagator.create_initial_state(
            company_name="600519.SH",
            trade_date="2026-08-20",
        )
        inv_state = state["investment_debate_state"]
        assert "challenges" in inv_state, "Propagator v1 initial state must contain 'challenges'"
        assert isinstance(inv_state["challenges"], list)
        assert len(inv_state["challenges"]) == 0
        assert inv_state.get("challenge_counter") == 0
        assert "challenge_verification" in inv_state
        assert isinstance(inv_state["challenge_verification"], list)
        assert len(inv_state["challenge_verification"]) == 0

    def test_propagator_state_deepcopy_isolation(self):
        propagator = Propagator()
        state1 = propagator.create_initial_state(
            company_name="600519.SH",
            trade_date="2026-08-20",
            runtime_config={"v2_debate_enabled": True},
        )
        state2 = propagator.create_initial_state(
            company_name="600519.SH",
            trade_date="2026-08-20",
            runtime_config={"v2_debate_enabled": True},
        )

        assert "challenges" in state1["investment_debate_state"]
        state1["investment_debate_state"]["challenges"].append({
            "challenge_id": "CH-1",
            "target_claim_id": "INV-4",
            "weakest_point": "现金流假设漏洞",
            "evidence": ["三季报预收款增加45%"],
            "severity": "major",
        })
        state1["investment_debate_state"]["challenge_verification"].append({
            "challenge_id": "CH-1",
            "status": "verified",
        })

        assert len(state2["investment_debate_state"]["challenges"]) == 0
        assert len(state2["investment_debate_state"]["challenge_verification"]) == 0


class TestChallengeTypedDictDeclarations:
    """Requirement 2: InvestDebateState and Challenge TypedDict field declarations."""

    def test_challenge_typeddict_declaration_and_fields(self):
        challenge_cls = getattr(agent_states, "Challenge", None)
        assert challenge_cls is not None, "agent_states module must declare 'Challenge' TypedDict"
        annotations = getattr(challenge_cls, "__annotations__", {})
        expected_fields = {
            "challenge_id",
            "speaker",
            "speaker_key",
            "stance",
            "target_claim_id",
            "weakest_point",
            "evidence",
            "severity",
            "status",
            "message_index",
            "debate_round",
            "stage",
            "evidence_status",
        }
        for field in expected_fields:
            assert field in annotations, f"Challenge TypedDict missing field '{field}'"

    def test_invest_debate_state_challenge_fields(self):
        annotations = getattr(InvestDebateState, "__annotations__", {})
        assert "challenges" in annotations, "InvestDebateState missing 'challenges'"
        assert "challenge_counter" in annotations, "InvestDebateState missing 'challenge_counter'"
        assert "challenge_verification" in annotations, "InvestDebateState missing 'challenge_verification'"


class TestDebateStateSanitizerChallengeFoundation:
    """Requirement 3, 4, 5: DEBATE_STATE sanitizer retention, filtering, and structural validation."""

    def test_sanitizer_preserves_challenges_and_self_win_prob(self, caplog):
        payload = {
            "challenges": [{
                "challenge_id": "CH-1",
                "target_claim_id": "INV-4",
                "weakest_point": "空头忽略合同负债增加45%",
                "evidence": ["三季报预收款与合同负债达35亿元"],
                "severity": "major",
                "unknown_nested_field": "should_be_ignored",
            }],
            "self_win_prob": 0.72,
            "new_claims": [],
            "responded_claim_ids": ["INV-4"],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": ["INV-4"],
            "next_focus_claim_ids": ["INV-4"],
            "round_summary": "多头盘问空头应收账款逻辑",
            "round_goal": "击穿空头现金流假设",
        }

        with caplog.at_level("WARNING"):
            result = _sanitize_machine_payload(payload, "DEBATE_STATE")

        assert result is not None
        assert "challenges" in result
        assert len(result["challenges"]) == 1
        ch = result["challenges"][0]
        assert ch["challenge_id"] == "CH-1"
        assert ch["target_claim_id"] == "INV-4"
        assert ch["weakest_point"] == "空头忽略合同负债增加45%"
        assert ch["evidence"] == ["三季报预收款与合同负债达35亿元"]
        assert ch["severity"] == "major"
        assert "unknown_nested_field" not in ch
        assert result.get("self_win_prob") == 0.72

        # Verify top-level challenges and self_win_prob are not reported as unknown_fields
        warnings = [record.message for record in caplog.records]
        assert not any("unknown structured fields ignored: challenges" in w for w in warnings)
        assert not any("unknown structured fields ignored: self_win_prob" in w for w in warnings)

    def test_sanitizer_challenge_without_optional_challenge_id(self):
        payload = {
            "challenges": [{
                "target_claim_id": "INV-4",
                "weakest_point": "漏洞分析",
                "evidence": ["证据1"],
                "severity": "minor",
            }],
            "self_win_prob": 0.65,
        }
        result = _sanitize_machine_payload(payload, "DEBATE_STATE")
        assert result is not None
        assert len(result["challenges"]) == 1
        ch = result["challenges"][0]
        assert ch["target_claim_id"] == "INV-4"
        assert ch["weakest_point"] == "漏洞分析"
        assert ch["evidence"] == ["证据1"]
        assert ch["severity"] == "minor"
        assert "challenge_id" not in ch

    def test_sanitizer_rejects_non_array_challenges(self):
        bad_payloads = [
            {"challenges": "not_an_array"},
            {"challenges": 123},
            {"challenges": {"target_claim_id": "INV-1"}},
            {"challenges": True},
        ]
        for p in bad_payloads:
            res = _sanitize_machine_payload(p, "DEBATE_STATE")
            assert res is None, f"Expected None for challenges={p['challenges']}"

    def test_sanitizer_rejects_non_object_challenge_item(self):
        bad_payloads = [
            {"challenges": ["not_an_object"]},
            {"challenges": [123]},
            {"challenges": [True]},
            {"challenges": [None]},
        ]
        for p in bad_payloads:
            res = _sanitize_machine_payload(p, "DEBATE_STATE")
            assert res is None, f"Expected None for challenges={p['challenges']}"

    def test_sanitizer_evidence_validation(self):
        # List of strings is valid
        res_list = _sanitize_machine_payload(
            {"challenges": [{"target_claim_id": "INV-1", "weakest_point": "w", "evidence": ["e1", "e2"], "severity": "minor"}]},
            "DEBATE_STATE",
        )
        assert res_list is not None
        assert res_list["challenges"][0]["evidence"] == ["e1", "e2"]

        # String is normalized to single-item list
        res_str = _sanitize_machine_payload(
            {"challenges": [{"target_claim_id": "INV-1", "weakest_point": "w", "evidence": "single evidence", "severity": "minor"}]},
            "DEBATE_STATE",
        )
        assert res_str is not None
        assert res_str["challenges"][0]["evidence"] == ["single evidence"]

        # None / omitted defaults to empty list
        res_none = _sanitize_machine_payload(
            {"challenges": [{"target_claim_id": "INV-1", "weakest_point": "w", "severity": "minor"}]},
            "DEBATE_STATE",
        )
        assert res_none is not None
        assert res_none["challenges"][0]["evidence"] == []

        # Non-list, non-string (int, bool, dict) must return None (typed invalid)
        bad_evidences = [123, True, False, {"invalid": "object"}]
        for bad_ev in bad_evidences:
            res_bad = _sanitize_machine_payload(
                {"challenges": [{"target_claim_id": "INV-1", "weakest_point": "w", "evidence": bad_ev, "severity": "minor"}]},
                "DEBATE_STATE",
            )
            assert res_bad is None, f"Expected None for evidence={bad_ev}"

    def test_sanitizer_self_win_prob_valid_values(self):
        valid_cases = [
            (0, 0.0),
            (1, 1.0),
            (0.0, 0.0),
            (1.0, 1.0),
            (0.72, 0.72),
            (0.5, 0.5),
        ]
        for input_val, expected_val in valid_cases:
            res = _sanitize_machine_payload({"self_win_prob": input_val}, "DEBATE_STATE")
            assert res is not None
            assert res["self_win_prob"] == expected_val
            assert isinstance(res["self_win_prob"], float)

    def test_sanitizer_self_win_prob_invalid_values_rejected(self):
        invalid_cases = [
            True,           # bool must not be silently cast to 1.0
            False,          # bool must not be silently cast to 0.0
            "0.72",         # string must not be accepted
            "72%",          # percentage string
            1.5,            # out of bounds (> 1.0)
            -0.1,           # out of bounds (< 0.0)
            100,            # out of bounds (> 1.0)
            float("nan"),   # non-finite
            float("inf"),   # non-finite
            float("-inf"),  # non-finite
        ]
        for bad_val in invalid_cases:
            res = _sanitize_machine_payload({"self_win_prob": bad_val}, "DEBATE_STATE")
            assert res is None, f"Expected None for self_win_prob={bad_val}"

    def test_legacy_payload_without_challenge_fields_parses_identically(self):
        legacy_payload = {
            "new_claims": [{
                "claim": "主力资金净流入1.2亿",
                "evidence": ["主力净流入1.2亿元"],
                "confidence": 0.85,
                "target_claim_ids": [],
                "battlefield": "capital_flow",
            }],
            "responded_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "next_focus_claim_ids": [],
            "round_summary": "多头立论",
            "round_goal": "建立核心立论",
        }
        res = _sanitize_machine_payload(legacy_payload, "DEBATE_STATE")
        assert res is not None
        assert res["challenges"] == []
        assert "self_win_prob" not in res or res.get("self_win_prob") is None
        assert len(res["new_claims"]) == 1
        assert res["new_claims"][0]["claim"] == "主力资金净流入1.2亿"
        assert res["new_claims"][0]["confidence"] == 0.85
