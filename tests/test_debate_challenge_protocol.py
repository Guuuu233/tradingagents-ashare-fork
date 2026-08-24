"""Unit tests for P1-B/B2-C2 Challenge Protocol: Hard Gates, Ledger, and Stage Progression (DAV-405)."""

import copy
import pytest

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
)
from tradingagents.agents.utils.debate_utils import (
    validate_debate_response,
    update_debate_state_with_payload,
)


def _build_opening_completed_v2_state() -> dict:
    """Helper to build standard v2 state after Bull (msg1) and Bear (msg2) opening stage."""
    bull_claims = [
        {"claim_id": "INV-1", "speaker_key": "Bull", "speaker": "Bull Analyst", "stance": "bullish", "claim": "主力资金持续净流入", "evidence": ["主力资金净流入1.2亿"], "confidence": 0.85, "battlefield": "capital_flow", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open", "target_claim_ids": []},
        {"claim_id": "INV-2", "speaker_key": "Bull", "speaker": "Bull Analyst", "stance": "bullish", "claim": "行业题材景气度高", "evidence": ["宏观政策密集落地"], "confidence": 0.80, "battlefield": "sentiment_theme", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open", "target_claim_ids": []},
        {"claim_id": "INV-3", "speaker_key": "Bull", "speaker": "Bull Analyst", "stance": "bullish", "claim": "量价突破均线多头", "evidence": ["放量突破60日线"], "confidence": 0.78, "battlefield": "price_volume", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open", "target_claim_ids": []},
    ]
    bear_claims = [
        {"claim_id": "INV-4", "speaker_key": "Bear", "speaker": "Bear Analyst", "stance": "bearish", "claim": "应收账款恶化现金流承压", "evidence": ["经营现金流同比下滑30%"], "confidence": 0.82, "battlefield": "fundamentals", "debate_round": 1, "message_index": 2, "stage": "opening", "status": "open", "target_claim_ids": []},
        {"claim_id": "INV-5", "speaker_key": "Bear", "speaker": "Bear Analyst", "stance": "bearish", "claim": "外需降温出口面临逆风", "evidence": ["出口交货值同比下降"], "confidence": 0.75, "battlefield": "macro_policy", "debate_round": 1, "message_index": 2, "stage": "opening", "status": "open", "target_claim_ids": []},
        {"claim_id": "INV-6", "speaker_key": "Bear", "speaker": "Bear Analyst", "stance": "bearish", "claim": "高位筹码松动获利盘兑现", "evidence": ["高位换手率超过25%"], "confidence": 0.70, "battlefield": "capital_flow", "debate_round": 1, "message_index": 2, "stage": "opening", "status": "open", "target_claim_ids": []},
    ]
    all_claims = bull_claims + bear_claims
    return {
        "count": 2,
        "claims": all_claims,
        "claim_counter": 6,
        "challenges": [],
        "challenge_counter": 0,
        "challenge_verification": [],
        "open_claim_ids": [f"INV-{i}" for i in range(1, 7)],
        "resolved_claim_ids": [],
        "unresolved_claim_ids": [],
        "round_messages": [
            {
                "message_index": 1,
                "debate_round": 1,
                "stage": "opening",
                "protocol_stage": "opening",
                "speaker": "Bull Analyst",
                "speaker_key": "Bull",
                "new_claim_ids": ["INV-1", "INV-2", "INV-3"],
                "responded_claim_ids": [],
                "target_claim_ids": [],
                "parse_status": "valid",
                "accepted": True,
            },
            {
                "message_index": 2,
                "debate_round": 1,
                "stage": "opening",
                "protocol_stage": "opening",
                "speaker": "Bear Analyst",
                "speaker_key": "Bear",
                "new_claim_ids": ["INV-4", "INV-5", "INV-6"],
                "responded_claim_ids": [],
                "target_claim_ids": [],
                "parse_status": "valid",
                "accepted": True,
            },
        ],
        "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
        "protocol_stage": "challenge",
        "feature_flags": {"v2_debate_enabled": True},
    }


class TestC21StageBranchingAndBaseContract:
    """C2.1: Stage branching and base contract for Challenge stage."""

    def test_v2_challenge_valid_payload_bypasses_legacy_check_c(self):
        """Bull message 3 in v2 challenge stage bypasses legacy Check C and validates successfully."""
        state = _build_opening_completed_v2_state()

        raw_response = (
            "多头盘问反驳：针对空头INV-4的现金流假设提出质疑。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-4",\n'
            '      "weakest_point": "空头忽略了三季度预收款和合同负债大增45%的事实",\n'
            '      "evidence": ["三季报预收款及合同负债达到35亿元，同比+45%"],\n'
            '      "severity": "major"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.75,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-4"],\n'
            '  "next_focus_claim_ids": ["INV-4"],\n'
            '  "round_summary": "多头盘问空头现金流漏洞",\n'
            '  "round_goal": "击穿空头核心立论"\n'
            "} -->"
        )

        is_valid, parse_status, error_detail, payload = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )

        assert is_valid is True, f"Validation failed with error: {error_detail}"
        assert parse_status == "valid"
        assert error_detail == ""
        assert payload is not None
        assert payload.get("self_win_prob") == 0.75
        assert len(payload.get("challenges", [])) == 1

    def test_v2_challenge_state_update_records_stage_and_round(self):
        """Bull message 3 update records stage=challenge and debate_round=2 in round_messages."""
        state = _build_opening_completed_v2_state()

        raw_response = (
            "多头盘问反驳：针对空头INV-4的现金流假设提出质疑。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-4",\n'
            '      "weakest_point": "空头忽略了三季度预收款和合同负债大增45%的事实",\n'
            '      "evidence": ["三季报预收款及合同负债达到35亿元，同比+45%"],\n'
            '      "severity": "major"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.75,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-4"],\n'
            '  "next_focus_claim_ids": ["INV-4"],\n'
            '  "round_summary": "多头盘问空头现金流漏洞",\n'
            '  "round_goal": "击穿空头核心立论"\n'
            "} -->"
        )

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=raw_response,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert new_state["count"] == 3
        assert len(new_state["round_messages"]) == 3
        msg3 = new_state["round_messages"][2]
        assert msg3["message_index"] == 3
        assert msg3["debate_round"] == 2
        assert msg3["stage"] == "challenge"
        assert msg3["protocol_stage"] == "challenge"
        assert msg3["self_win_prob"] == 0.75


class TestC22ChallengeHardGates:
    """C2.2: Hard gates for Challenge stage."""

    def _base_valid_challenge_payload(self) -> dict:
        return {
            "responded_claim_ids": ["INV-4"],
            "new_claims": [],
            "challenges": [
                {
                    "target_claim_id": "INV-4",
                    "weakest_point": "空头忽略三季度预收款和合同负债大增45%的事实",
                    "evidence": ["三季报预收款及合同负债达到35亿元，同比+45%"],
                    "severity": "major",
                }
            ],
            "self_win_prob": 0.75,
            "resolved_claim_ids": [],
            "unresolved_claim_ids": ["INV-4"],
            "next_focus_claim_ids": ["INV-4"],
            "round_summary": "多头盘问空头现金流漏洞",
            "round_goal": "击穿空头核心立论",
        }

    def test_challenge_rejects_non_empty_new_claims(self):
        """new_claims must be strictly empty [] in challenge stage."""
        state = _build_opening_completed_v2_state()
        payload = self._base_valid_challenge_payload()
        payload["new_claims"] = [
            {
                "claim": "多头新增无关立论",
                "evidence": ["新证据"],
                "confidence": 0.8,
                "battlefield": "capital_flow",
                "target_claim_ids": [],
            }
        ]
        raw_response = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')

        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "new_claims" in error_detail

    def test_challenge_rejects_empty_challenges(self):
        """challenges must contain at least 1 item."""
        state = _build_opening_completed_v2_state()
        payload = self._base_valid_challenge_payload()
        payload["challenges"] = []
        raw_response = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')

        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "challenges" in error_detail

    def test_challenge_target_claim_must_exist(self):
        """target_claim_id must exist in state claims ledger."""
        state = _build_opening_completed_v2_state()
        payload = self._base_valid_challenge_payload()
        payload["challenges"][0]["target_claim_id"] = "INV-999"
        payload["responded_claim_ids"] = ["INV-999"]
        raw_response = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')

        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "INV-999" in error_detail
        assert "target_claim_id" in error_detail

    def test_challenge_target_claim_must_belong_to_opponent(self):
        """target_claim_id must belong to opponent (Bull cannot challenge Bull's own claim)."""
        state = _build_opening_completed_v2_state()
        payload = self._base_valid_challenge_payload()
        payload["challenges"][0]["target_claim_id"] = "INV-1"
        payload["responded_claim_ids"] = ["INV-1"]
        raw_response = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')

        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "INV-1" in error_detail
        assert "对手" in error_detail or "opponent" in error_detail.lower()

    def test_challenge_target_claim_cannot_be_resolved(self):
        """target_claim_id must not be resolved."""
        state = _build_opening_completed_v2_state()
        # Mark INV-4 as resolved in state
        for c in state["claims"]:
            if c["claim_id"] == "INV-4":
                c["status"] = "resolved"

        payload = self._base_valid_challenge_payload()
        raw_response = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')

        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "INV-4" in error_detail
        assert "resolved" in error_detail

    def test_challenge_weakest_point_non_empty_and_max_500_chars(self):
        """weakest_point must be non-empty and <= 500 characters."""
        state = _build_opening_completed_v2_state()

        # Empty weakest_point
        payload_empty = self._base_valid_challenge_payload()
        payload_empty["challenges"][0]["weakest_point"] = ""
        raw_empty = f"<!-- DEBATE_STATE: {copy.deepcopy(payload_empty)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_empty,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "weakest_point" in error_detail

        # Exceeds 500 characters
        payload_501 = self._base_valid_challenge_payload()
        payload_501["challenges"][0]["weakest_point"] = "弱点" * 251  # 502 chars
        raw_501 = f"<!-- DEBATE_STATE: {copy.deepcopy(payload_501)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_501,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "weakest_point" in error_detail
        assert "500" in error_detail

        # Exactly 500 characters is valid
        payload_500 = self._base_valid_challenge_payload()
        payload_500["challenges"][0]["weakest_point"] = "弱" * 500
        raw_500 = f"<!-- DEBATE_STATE: {copy.deepcopy(payload_500)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_500,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is True

    def test_challenge_evidence_at_least_one_non_empty(self):
        """evidence must contain at least 1 non-empty item."""
        state = _build_opening_completed_v2_state()

        # Empty list
        payload_empty = self._base_valid_challenge_payload()
        payload_empty["challenges"][0]["evidence"] = []
        raw_empty = f"<!-- DEBATE_STATE: {copy.deepcopy(payload_empty)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_empty,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "evidence" in error_detail

        # List with only whitespace strings
        payload_ws = self._base_valid_challenge_payload()
        payload_ws["challenges"][0]["evidence"] = ["", "   "]
        raw_ws = f"<!-- DEBATE_STATE: {copy.deepcopy(payload_ws)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_ws,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "evidence" in error_detail

    def test_challenge_severity_only_fatal_major_minor(self):
        """severity must strictly be fatal, major, or minor."""
        state = _build_opening_completed_v2_state()

        for invalid_sev in ["critical", "high", "medium", "low", "", "fatal_flaw"]:
            payload = self._base_valid_challenge_payload()
            payload["challenges"][0]["severity"] = invalid_sev
            raw_resp = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')
            is_valid, parse_status, error_detail, _ = validate_debate_response(
                state=state,
                raw_response=raw_resp,
                speaker_key="Bull",
                stance="bullish",
                marker="DEBATE_STATE",
                domain="investment",
            )
            assert is_valid is False
            assert parse_status == "invalid_protocol"
            assert "severity" in error_detail

        for valid_sev in ["fatal", "major", "minor"]:
            payload = self._base_valid_challenge_payload()
            payload["challenges"][0]["severity"] = valid_sev
            raw_resp = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')
            is_valid, parse_status, error_detail, _ = validate_debate_response(
                state=state,
                raw_response=raw_resp,
                speaker_key="Bull",
                stance="bullish",
                marker="DEBATE_STATE",
                domain="investment",
            )
            assert is_valid is True, f"Expected severity '{valid_sev}' to be valid"

    def test_challenge_self_win_prob_required_and_bounded(self):
        """self_win_prob must be provided as a finite float in 0..1."""
        state = _build_opening_completed_v2_state()

        # Missing self_win_prob
        payload = self._base_valid_challenge_payload()
        del payload["self_win_prob"]
        raw_resp = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_resp,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "self_win_prob" in error_detail

    def test_challenge_responded_claim_ids_must_contain_target(self):
        """responded_claim_ids must contain all target_claim_ids challenged."""
        state = _build_opening_completed_v2_state()

        # Missing target_claim_id in responded_claim_ids (has unrelated opponent claim)
        payload = self._base_valid_challenge_payload()
        payload["challenges"][0]["target_claim_id"] = "INV-4"
        payload["responded_claim_ids"] = ["INV-5"]  # Does not contain INV-4
        raw_resp = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_resp,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "responded_claim_ids" in error_detail
        assert "INV-4" in error_detail

        # Empty responded_claim_ids
        payload_empty = self._base_valid_challenge_payload()
        payload_empty["responded_claim_ids"] = []
        raw_empty = f"<!-- DEBATE_STATE: {copy.deepcopy(payload_empty)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_empty,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "responded_claim_ids" in error_detail

    def test_challenge_resolved_claim_ids_cannot_resolve_opponent(self):
        """Bull cannot resolve opponent Bear claims in challenge stage."""
        state = _build_opening_completed_v2_state()
        payload = self._base_valid_challenge_payload()
        payload["resolved_claim_ids"] = ["INV-4"]
        raw_resp = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_resp,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "INV-4" in error_detail
        assert "阵营权限" in error_detail

    def test_challenge_rejects_duplicate_weakest_point_in_same_payload(self):
        """Same speaker challenging same target with identical/high-similarity weakest_point is rejected as duplicate."""
        state = _build_opening_completed_v2_state()
        payload = self._base_valid_challenge_payload()
        payload["challenges"] = [
            {
                "target_claim_id": "INV-4",
                "weakest_point": "空头忽略三季度预收款和合同负债大增45%的事实",
                "evidence": ["三季报预收款及合同负债达到35亿元，同比+45%"],
                "severity": "major",
            },
            {
                "target_claim_id": "INV-4",
                "weakest_point": "空头完全忽略了三季度预收款以及合同负债大增45%的客观事实",
                "evidence": ["三季报预收款35亿元"],
                "severity": "major",
            },
        ]
        payload["responded_claim_ids"] = ["INV-4"]
        raw_resp = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_resp,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "重复" in error_detail or "duplicate" in error_detail.lower() or "0.82" in error_detail

    def test_challenge_rejects_duplicate_weakest_point_against_historical(self):
        """Same speaker repeating challenge on same target from prior round is rejected."""
        state = _build_opening_completed_v2_state()
        state["challenges"] = [
            {
                "challenge_id": "CH-1",
                "speaker_key": "Bull",
                "speaker": "Bull Analyst",
                "target_claim_id": "INV-4",
                "weakest_point": "现金流假设漏洞，未计入预收账款大幅增加",
                "evidence": ["预收增加45%"],
                "severity": "major",
                "status": "open",
            }
        ]
        payload = self._base_valid_challenge_payload()
        payload["challenges"] = [
            {
                "target_claim_id": "INV-4",
                "weakest_point": "现金流假设漏洞，未计入预收账款大幅增加",
                "evidence": ["三季报预收款35亿元"],
                "severity": "major",
            }
        ]
        raw_resp = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_resp,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "重复" in error_detail or "duplicate" in error_detail.lower() or "0.82" in error_detail

    def test_challenge_allows_distinct_weakest_points_on_same_target(self):
        """Distinct substantive weakest points on same target are accepted."""
        state = _build_opening_completed_v2_state()
        payload = self._base_valid_challenge_payload()
        payload["challenges"] = [
            {
                "target_claim_id": "INV-4",
                "weakest_point": "空头忽略预收与合同负债增加45%",
                "evidence": ["三季报合同负债达到35亿元"],
                "severity": "major",
            },
            {
                "target_claim_id": "INV-4",
                "weakest_point": "应收账款周转天数从45天缩短到28天，回款效率实质提升",
                "evidence": ["三季报周转天数28天"],
                "severity": "minor",
            },
        ]
        payload["responded_claim_ids"] = ["INV-4"]
        raw_resp = f"<!-- DEBATE_STATE: {copy.deepcopy(payload)} -->".replace("'", '"')
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_resp,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is True, f"Expected distinct challenges to be valid, got: {error_detail}"


class TestC23ChallengeIdAndLedgerPersistence:
    """C2.3: Challenge ID allocation, ledger persistence, round_message fields, and attempt isolation."""

    def test_bull_msg3_and_bear_msg4_allocate_ch_ids_and_persist_to_ledger(self):
        """Bull msg3 allocates CH-1, Bear msg4 allocates CH-2; challenge objects and round_messages persisted."""
        state = _build_opening_completed_v2_state()

        # Step 1: Bull Message 3 (Challenge)
        bull_raw = (
            "多头盘问反驳：针对空头INV-4提出质疑。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-4",\n'
            '      "weakest_point": "空头忽略三季度预收款和合同负债大增45%的事实",\n'
            '      "evidence": ["三季报预收款及合同负债达到35亿元，同比+45%"],\n'
            '      "severity": "major"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.75,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-4"],\n'
            '  "next_focus_claim_ids": ["INV-4"],\n'
            '  "round_summary": "多头盘问空头现金流漏洞",\n'
            '  "round_goal": "击穿空头核心立论"\n'
            "} -->"
        )

        state_after_msg3 = update_debate_state_with_payload(
            state=state,
            raw_response=bull_raw,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert state_after_msg3["count"] == 3
        assert state_after_msg3["challenge_counter"] == 1
        assert len(state_after_msg3["challenges"]) == 1

        ch1 = state_after_msg3["challenges"][0]
        assert ch1["challenge_id"] == "CH-1"
        assert ch1["speaker"] == "Bull Analyst"
        assert ch1["speaker_key"] == "Bull"
        assert ch1["stance"] == "bullish"
        assert ch1["target_claim_id"] == "INV-4"
        assert ch1["weakest_point"] == "空头忽略三季度预收款和合同负债大增45%的事实"
        assert ch1["evidence"] == ["三季报预收款及合同负债达到35亿元，同比+45%"]
        assert ch1["severity"] == "major"
        assert ch1["status"] == "open"
        assert ch1["evidence_status"] == "unverified"
        assert ch1["message_index"] == 3
        assert ch1["debate_round"] == 2
        assert ch1["stage"] == "challenge"

        # Check round_message 3
        msg3 = state_after_msg3["round_messages"][2]
        assert msg3["message_index"] == 3
        assert msg3["debate_round"] == 2
        assert msg3["stage"] == "challenge"
        assert msg3["protocol_stage"] == "challenge"
        assert msg3["challenge_ids"] == ["CH-1"]
        assert msg3["self_win_prob"] == 0.75
        assert msg3["new_claim_ids"] == []
        assert msg3["responded_claim_ids"] == ["INV-4"]

        # Verify claims ledger is not polluted with new INV claims
        assert len(state_after_msg3["claims"]) == 6
        assert state_after_msg3["claim_counter"] == 6

        # Step 2: Bear Message 4 (Challenge)
        bear_raw = (
            "空头盘问反驳：针对多头INV-1主力资金提出质疑。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-1"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-1",\n'
            '      "weakest_point": "多头混淆超大单与对倒假资金流，尾盘大单存在诱多派发迹象",\n'
            '      "evidence": ["尾盘15分钟大单成交集中在卖二卖三且主力净主动买入为负"],\n'
            '      "severity": "fatal"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.68,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-1"],\n'
            '  "next_focus_claim_ids": ["INV-1"],\n'
            '  "round_summary": "空头盘问多头资金流漏洞",\n'
            '  "round_goal": "击穿多头资金立论"\n'
            "} -->"
        )

        state_after_msg4 = update_debate_state_with_payload(
            state=state_after_msg3,
            raw_response=bear_raw,
            speaker_label="Bear Analyst",
            speaker_key="Bear",
            stance="bearish",
            history_key="bear_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert state_after_msg4["count"] == 4
        assert state_after_msg4["challenge_counter"] == 2
        assert len(state_after_msg4["challenges"]) == 2

        ch2 = state_after_msg4["challenges"][1]
        assert ch2["challenge_id"] == "CH-2"
        assert ch2["speaker"] == "Bear Analyst"
        assert ch2["speaker_key"] == "Bear"
        assert ch2["stance"] == "bearish"
        assert ch2["target_claim_id"] == "INV-1"
        assert ch2["weakest_point"] == "多头混淆超大单与对倒假资金流，尾盘大单存在诱多派发迹象"
        assert ch2["evidence"] == ["尾盘15分钟大单成交集中在卖二卖三且主力净主动买入为负"]
        assert ch2["severity"] == "fatal"
        assert ch2["status"] == "open"
        assert ch2["evidence_status"] == "unverified"
        assert ch2["message_index"] == 4
        assert ch2["debate_round"] == 2
        assert ch2["stage"] == "challenge"

        # Check round_message 4
        msg4 = state_after_msg4["round_messages"][3]
        assert msg4["message_index"] == 4
        assert msg4["debate_round"] == 2
        assert msg4["stage"] == "challenge"
        assert msg4["challenge_ids"] == ["CH-2"]
        assert msg4["self_win_prob"] == 0.68
        assert msg4["new_claim_ids"] == []

        # Verify target claims did not become resolved or rejected
        for c in state_after_msg4["claims"]:
            assert c["status"] != "resolved"
            assert c["status"] != "rejected"

    def test_invalid_attempt_does_not_advance_count_or_consume_ch_id(self):
        """Failed/invalid challenge attempt records unaccepted attempt without advancing count, stage, or consuming CH ID."""
        state = _build_opening_completed_v2_state()

        # Invalid response: empty weakest_point
        bad_raw = (
            "多头无效发言\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-4",\n'
            '      "weakest_point": "",\n'
            '      "evidence": ["证据"],\n'
            '      "severity": "major"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.75\n'
            "} -->"
        )

        state_after_bad = update_debate_state_with_payload(
            state=state,
            raw_response=bad_raw,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert state_after_bad["count"] == 2
        assert state_after_bad["protocol_stage"] == "challenge"
        assert state_after_bad["challenge_counter"] == 0
        assert len(state_after_bad["challenges"]) == 0
        assert state_after_bad["blocked"] is True
        assert state_after_bad["parse_status"] == "invalid_protocol"
        assert "weakest_point" in state_after_bad["block_reason"]

        # Verify unaccepted attempt record was added
        assert len(state_after_bad["round_messages"]) == 3
        attempt_msg = state_after_bad["round_messages"][2]
        assert attempt_msg["accepted"] is False
        assert attempt_msg["parse_status"] == "invalid_protocol"


class TestC24StageProgression:
    """C2.4: Stage progression through opening -> challenge -> tiebreak."""

    def test_full_four_message_progression_opening_to_tiebreak(self):
        """Sequential 4 messages in v2 correctly transition protocol_stage: opening -> challenge -> tiebreak."""
        # Initial fresh v2 state
        state = {
            "count": 0,
            "claims": [],
            "claim_counter": 0,
            "challenges": [],
            "challenge_counter": 0,
            "challenge_verification": [],
            "open_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "round_messages": [],
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": True},
        }

        # Step 1: Bull Opening (msg 1)
        bull_open = (
            "多头立论\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "主力持续流入", "evidence": ["流入1.2亿"], "confidence": 0.85, "battlefield": "capital_flow", "target_claim_ids": []},\n'
            '    {"claim": "题材情绪景气", "evidence": ["政策支持"], "confidence": 0.80, "battlefield": "sentiment_theme", "target_claim_ids": []},\n'
            '    {"claim": "突破均线多头", "evidence": ["放量突破"], "confidence": 0.78, "battlefield": "price_volume", "target_claim_ids": []}\n'
            '  ],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "多头开国立论",\n'
            '  "round_goal": "建立多头核心立论"\n'
            "} -->"
        )
        s1 = update_debate_state_with_payload(
            state=state,
            raw_response=bull_open,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )
        assert s1["count"] == 1
        assert s1["protocol_stage"] == "opening"
        assert s1["round_messages"][0]["stage"] == "opening"
        assert s1["round_messages"][0]["debate_round"] == 1

        # Step 2: Bear Opening (msg 2)
        bear_open = (
            "空头立论\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "应收账款恶化", "evidence": ["现金流下滑"], "confidence": 0.82, "battlefield": "fundamentals", "target_claim_ids": []},\n'
            '    {"claim": "外需面临逆风", "evidence": ["出口下滑"], "confidence": 0.75, "battlefield": "macro_policy", "target_claim_ids": []},\n'
            '    {"claim": "高位筹码松动", "evidence": ["换手率高"], "confidence": 0.70, "battlefield": "capital_flow", "target_claim_ids": []}\n'
            '  ],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "空头开国立论",\n'
            '  "round_goal": "建立空头核心立论"\n'
            "} -->"
        )
        s2 = update_debate_state_with_payload(
            state=s1,
            raw_response=bear_open,
            speaker_label="Bear Analyst",
            speaker_key="Bear",
            stance="bearish",
            history_key="bear_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )
        assert s2["count"] == 2
        assert s2["protocol_stage"] == "challenge"
        assert s2["round_messages"][1]["stage"] == "opening"
        assert s2["round_messages"][1]["debate_round"] == 1

        # Step 3: Bull Challenge (msg 3)
        bull_ch = (
            "多头盘问反驳\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-4",\n'
            '      "weakest_point": "空头忽略预收与合同负债增加45%",\n'
            '      "evidence": ["三季报合同负债达到35亿元"],\n'
            '      "severity": "major"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.75,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-4"],\n'
            '  "next_focus_claim_ids": ["INV-4"],\n'
            '  "round_summary": "多头盘问空头",\n'
            '  "round_goal": "击穿空头现金流"\n'
            "} -->"
        )
        s3 = update_debate_state_with_payload(
            state=s2,
            raw_response=bull_ch,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )
        assert s3["count"] == 3
        assert s3["protocol_stage"] == "challenge", "Authoritative state must still be challenge after Bull msg 3"
        assert s3["round_messages"][2]["stage"] == "challenge"
        assert s3["round_messages"][2]["debate_round"] == 2
        assert s3["challenges"][0]["stage"] == "challenge"
        assert s3["challenges"][0]["challenge_id"] == "CH-1"

        # Step 4: Bear Challenge (msg 4)
        bear_ch = (
            "空头盘问反驳\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-1"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-1",\n'
            '      "weakest_point": "多头超大单流入实为尾盘对倒诱多",\n'
            '      "evidence": ["主力净主动买入为负1.5亿"],\n'
            '      "severity": "fatal"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.65,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-1"],\n'
            '  "next_focus_claim_ids": ["INV-1"],\n'
            '  "round_summary": "空头盘问多头",\n'
            '  "round_goal": "击穿多头资金流"\n'
            "} -->"
        )
        s4 = update_debate_state_with_payload(
            state=s3,
            raw_response=bear_ch,
            speaker_label="Bear Analyst",
            speaker_key="Bear",
            stance="bearish",
            history_key="bear_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )
        assert s4["count"] == 4
        assert s4["protocol_stage"] == "tiebreak", "Authoritative state must transition to tiebreak after Bear msg 4"
        assert s4["round_messages"][3]["stage"] == "challenge", "msg 4 round_message stage must remain challenge"
        assert s4["round_messages"][3]["debate_round"] == 2
        assert s4["challenges"][1]["stage"] == "challenge", "CH-2 stage must remain challenge"
        assert s4["challenges"][1]["challenge_id"] == "CH-2"


class TestC25AuthoritativeStageRecovery:
    """C2.5: Authoritative protocol_stage recovery and priority over count/message_index."""

    def test_explicit_tiebreak_stage_with_stale_count_does_not_treat_challenge_as_valid(self):
        """v2 state with explicit protocol_stage='tiebreak' and count=2 must not validate challenge payload as challenge action."""
        state = _build_opening_completed_v2_state()
        state["protocol_stage"] = "tiebreak"
        state["count"] = 2  # message_index = 3

        challenge_payload = (
            "多头盘问反驳：针对空头INV-4提出质疑。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-4",\n'
            '      "weakest_point": "空头忽略三季度预收款和合同负债大增45%的事实",\n'
            '      "evidence": ["三季报预收款及合同负债达到35亿元，同比+45%"],\n'
            '      "severity": "major"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.75,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-4"],\n'
            '  "next_focus_claim_ids": ["INV-4"],\n'
            '  "round_summary": "多头盘问空头现金流漏洞",\n'
            '  "round_goal": "击穿空头核心立论"\n'
            "} -->"
        )

        is_valid, parse_status, error_detail, payload = validate_debate_response(
            state=state,
            raw_response=challenge_payload,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )

        # In tiebreak stage, Challenge payload (with empty new_claims) must not be accepted under challenge rules
        assert is_valid is False, "Explicit tiebreak stage must not treat challenge payload as valid challenge action"
        assert parse_status == "invalid_protocol"

    def test_explicit_manager_stage_with_stale_count_preserves_manager_stage_on_unaccepted_attempt(self):
        """v2 state with explicit protocol_stage='manager' and count=2 must preserve manager stage on unaccepted attempt."""
        state = _build_opening_completed_v2_state()
        state["protocol_stage"] = "manager"
        state["count"] = 2  # message_index = 3

        # Invalid/missing response
        bad_raw = "无效格式发言，无结构化JSON标记"

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=bad_raw,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert new_state["count"] == 2
        assert new_state["protocol_stage"] == "manager", "Authoritative state protocol_stage must remain 'manager', not 'challenge'"
        assert len(new_state["round_messages"]) == 3
        attempt = new_state["round_messages"][2]
        assert attempt["stage"] == "manager", f"Attempt stage must be 'manager', got '{attempt.get('stage')}'"
        assert attempt["protocol_stage"] == "manager", f"Attempt protocol_stage must be 'manager', got '{attempt.get('protocol_stage')}'"
        assert attempt["accepted"] is False

    def test_explicit_manager_stage_with_challenge_payload_does_not_override_stage_to_challenge(self):
        """v2 state with explicit protocol_stage='manager' and count=2 handling challenge payload must not write challenge stage."""
        state = _build_opening_completed_v2_state()
        state["protocol_stage"] = "manager"
        state["count"] = 2  # message_index = 3

        challenge_raw = (
            "多头盘问反驳\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-4",\n'
            '      "weakest_point": "空头忽略预收与合同负债增加45%",\n'
            '      "evidence": ["三季报合同负债达到35亿元"],\n'
            '      "severity": "major"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.75,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-4"]\n'
            "} -->"
        )

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=challenge_raw,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert new_state["protocol_stage"] == "manager", "State protocol_stage must stay 'manager'"
        attempt = new_state["round_messages"][2]
        assert attempt["stage"] == "manager", f"Attempt stage must be 'manager', got '{attempt.get('stage')}'"
        assert attempt["protocol_stage"] == "manager", f"Attempt protocol_stage must be 'manager', got '{attempt.get('protocol_stage')}'"

    def test_missing_protocol_stage_fallback_infers_stage_from_message_index(self):
        """When protocol_stage is not present in state, stage is inferred from message_index."""
        state = _build_opening_completed_v2_state()
        del state["protocol_stage"]
        state["count"] = 2  # message_index = 3 -> challenge

        challenge_raw = (
            "多头盘问反驳\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-4",\n'
            '      "weakest_point": "空头忽略预收与合同负债增加45%",\n'
            '      "evidence": ["三季报合同负债达到35亿元"],\n'
            '      "severity": "major"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.75,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-4"],\n'
            '  "next_focus_claim_ids": ["INV-4"],\n'
            '  "round_summary": "多头盘问空头",\n'
            '  "round_goal": "击穿空头现金流"\n'
            "} -->"
        )

        is_valid, parse_status, error_detail, payload = validate_debate_response(
            state=state,
            raw_response=challenge_raw,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is True
        assert parse_status == "valid"

