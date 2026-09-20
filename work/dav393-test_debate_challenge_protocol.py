"""Tests for P1-B/B2 Challenge: Cross-examination contract, validation, storage, and evidence verification (DAV-393)."""

import asyncio
import copy
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tradingagents.agents.utils.agent_states import (
    DEFAULT_FEATURE_FLAGS,
    DEFAULT_PROTOCOL_METADATA,
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
    get_protocol_metadata,
    is_v2_debate_enabled,
)
from tradingagents.agents.utils.debate_utils import (
    VALID_BATTLEFIELDS,
    compute_claim_similarity,
    render_debate_prompt,
    update_debate_state_with_payload,
    validate_debate_response,
)
from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator,
    STATUS_CONTRADICTED,
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
)
from tradingagents.graph.propagation import Propagator


def _create_v2_opening_state():
    """Helper to create state after Bull and Bear opening messages (message_index=2, count=2)."""
    bull_claims = [
        {"claim_id": "INV-1", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "主力资金净流入1.2亿", "evidence": ["主力净流入1.2亿元"], "confidence": 0.85, "battlefield": "capital_flow", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open"},
        {"claim_id": "INV-2", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "行业政策高度景气支持", "evidence": ["宏观扶持政策落地"], "confidence": 0.80, "battlefield": "sentiment_theme", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open"},
        {"claim_id": "INV-3", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "放量突破60日均线多头", "evidence": ["突破60日线放量20%"], "confidence": 0.78, "battlefield": "price_volume", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open"},
    ]
    bear_claims = [
        {"claim_id": "INV-4", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "应收账款恶化现金流承压", "evidence": ["财报显示经营现金流下滑30%"], "confidence": 0.82, "battlefield": "fundamentals", "debate_round": 1, "message_index": 2, "stage": "opening", "status": "open"},
        {"claim_id": "INV-5", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "宏观外需降温出口面临逆风", "evidence": ["出口增速下滑至2%"], "confidence": 0.75, "battlefield": "macro_policy", "debate_round": 1, "message_index": 2, "stage": "opening", "status": "open"},
        {"claim_id": "INV-6", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "北向资金单周持续净流出", "evidence": ["北向单周净流出15亿元"], "confidence": 0.79, "battlefield": "capital_flow", "debate_round": 1, "message_index": 2, "stage": "opening", "status": "open"},
    ]
    return {
        "count": 2,
        "claims": bull_claims + bear_claims,
        "claim_counter": 6,
        "challenges": [],
        "challenge_counter": 0,
        "round_messages": [
            {"message_index": 1, "debate_round": 1, "stage": "opening", "speaker_key": "Bull", "new_claim_ids": ["INV-1", "INV-2", "INV-3"]},
            {"message_index": 2, "debate_round": 1, "stage": "opening", "speaker_key": "Bear", "new_claim_ids": ["INV-4", "INV-5", "INV-6"]},
        ],
        "open_claim_ids": ["INV-1", "INV-2", "INV-3", "INV-4", "INV-5", "INV-6"],
        "resolved_claim_ids": [],
        "unresolved_claim_ids": [],
        "focus_claim_ids": ["INV-4", "INV-1"],
        "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
        "protocol_stage": "challenge",
        "feature_flags": {"v2_debate_enabled": True},
    }


class TestC1ChallengeSchemaAndStateStorage:
    """C1: Challenge schema, unique ID generation (CH-1, CH-2), attempt isolation, and state storage."""

    def test_initial_state_has_challenges_and_counter(self):
        """Propagator initial state contains empty challenges list and challenge_counter=0."""
        propagator = Propagator()
        state = propagator.create_initial_state(
            company_name="600519.SH",
            trade_date="2026-08-20",
            runtime_config={"v2_debate_enabled": True},
        )
        inv_state = state["investment_debate_state"]
        assert "challenges" in inv_state
        assert isinstance(inv_state["challenges"], list)
        assert len(inv_state["challenges"]) == 0
        assert inv_state.get("challenge_counter") == 0

    def test_valid_challenge_ingestion_and_id_allocation(self):
        """Bull message 3 ingests valid challenge, generates CH-1, and Bear message 4 generates CH-2."""
        state = _create_v2_opening_state()

        # Bull message 3 payload targeting Bear claim INV-4
        bull_raw = (
            "多头盘问反驳正文：空头所指出的应收账款恶化忽略了下游大客户的季度集中结算规律。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-4",\n'
            '    "weakest_point": "空头忽略了下游大客户三季度集中结算与合同负债同比增加45%的确定性",\n'
            '    "evidence": ["三季报预收款与合同负债达35亿元同比增加45%"],\n'
            '    "severity": "major"\n'
            '  }],\n'
            '  "self_win_prob": 0.72,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-4"],\n'
            '  "next_focus_claim_ids": ["INV-4"],\n'
            '  "round_summary": "多头反驳空头应收账款逻辑",\n'
            '  "round_goal": "击穿空头现金流假设"\n'
            "} -->"
        )

        is_valid, parse_status, error_detail, payload = validate_debate_response(
            state=state,
            raw_response=bull_raw,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is True
        assert parse_status == "valid"

        state_after_bull = update_debate_state_with_payload(
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

        assert state_after_bull["count"] == 3
        assert state_after_bull["challenge_counter"] == 1
        assert len(state_after_bull["challenges"]) == 1
        ch1 = state_after_bull["challenges"][0]
        assert ch1["challenge_id"] == "CH-1"
        assert ch1["speaker_key"] == "Bull"
        assert ch1["target_claim_id"] == "INV-4"
        assert ch1["severity"] == "major"
        assert ch1["status"] == "open"
        assert ch1["message_index"] == 3
        assert ch1["debate_round"] == 2
        assert ch1["stage"] == "challenge"

        msg3 = state_after_bull["round_messages"][-1]
        assert msg3["message_index"] == 3
        assert msg3["debate_round"] == 2
        assert msg3["stage"] == "challenge"
        assert msg3["challenge_ids"] == ["CH-1"]
        assert msg3["self_win_prob"] == 0.72

        # Bear message 4 payload targeting Bull claim INV-1
        bear_raw = (
            "空头盘问反驳正文：多头所谓主力资金流入纯属日内游资对倒拉高出货。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-1",\n'
            '    "weakest_point": "主力净流入属于龙虎榜游资对倒席位，机构与外资在连续5日减仓净流出",\n'
            '    "evidence": ["龙虎榜显示前五席位卖出占比62%且机构净卖出2.3亿"],\n'
            '    "severity": "fatal"\n'
            '  }],\n'
            '  "self_win_prob": 0.68,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-1"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-1"],\n'
            '  "next_focus_claim_ids": ["INV-1"],\n'
            '  "round_summary": "空头穿透多头主力筹码虚假买盘",\n'
            '  "round_goal": "击穿多头资金面假设"\n'
            "} -->"
        )

        is_valid_bear, parse_status_bear, error_bear, payload_bear = validate_debate_response(
            state=state_after_bull,
            raw_response=bear_raw,
            speaker_key="Bear",
            stance="bearish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid_bear is True
        assert parse_status_bear == "valid"

        state_after_bear = update_debate_state_with_payload(
            state=state_after_bull,
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

        assert state_after_bear["count"] == 4
        assert state_after_bear["challenge_counter"] == 2
        assert len(state_after_bear["challenges"]) == 2
        ch2 = state_after_bear["challenges"][1]
        assert ch2["challenge_id"] == "CH-2"
        assert ch2["speaker_key"] == "Bear"
        assert ch2["target_claim_id"] == "INV-1"
        assert ch2["severity"] == "fatal"

        msg4 = state_after_bear["round_messages"][-1]
        assert msg4["message_index"] == 4
        assert msg4["debate_round"] == 2
        assert msg4["stage"] == "challenge"
        assert msg4["challenge_ids"] == ["CH-2"]
        assert msg4["self_win_prob"] == 0.68

    def test_challenge_id_not_consumed_on_failed_attempt(self):
        """Failed attempt at message_index=3 does not advance challenge_counter or state count."""
        state = _create_v2_opening_state()

        # Malformed / invalid response
        bad_response = "正文无机读块"
        bad_state = update_debate_state_with_payload(
            state=state,
            raw_response=bad_response,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert bad_state["count"] == 2
        assert bad_state.get("challenge_counter", 0) == 0
        assert len(bad_state.get("challenges", [])) == 0
        assert bad_state["blocked"] is True

        # Now valid retry response
        valid_response = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-4",\n'
            '    "weakest_point": "空头忽略合同负债与预收款",\n'
            '    "evidence": ["合同负债同比增加45%"],\n'
            '    "severity": "major"\n'
            '  }],\n'
            '  "self_win_prob": 0.70,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "重试成功",\n'
            '  "round_goal": "完成盘问"\n'
            "} -->"
        )
        recovered_state = update_debate_state_with_payload(
            state=state,
            raw_response=valid_response,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )
        assert recovered_state["count"] == 3
        assert recovered_state["challenge_counter"] == 1
        assert recovered_state["challenges"][0]["challenge_id"] == "CH-1"

    def test_duplicate_challenge_rejected(self):
        """Duplicate challenge (same speaker + target_claim + similar weakest_point) is rejected."""
        state = _create_v2_opening_state()
        state["challenges"] = [{
            "challenge_id": "CH-1",
            "speaker_key": "Bull",
            "target_claim_id": "INV-4",
            "weakest_point": "空头忽略了下游大客户三季度集中结算与合同负债增加",
            "evidence": ["合同负债同比增加45%"],
            "severity": "major",
        }]
        state["challenge_counter"] = 1

        # Attempt to add exact duplicate challenge
        dup_raw = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-4",\n'
            '    "weakest_point": "空头忽略了下游大客户三季度集中结算与合同负债增加",\n'
            '    "evidence": ["合同负债同比增加45%"],\n'
            '    "severity": "major"\n'
            '  }],\n'
            '  "self_win_prob": 0.70,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "重复盘问",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )
        is_valid, parse_status, error_detail, payload = validate_debate_response(
            state=state,
            raw_response=dup_raw,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "重复" in error_detail or "duplicate" in error_detail.lower()


class TestC2ChallengeProtocolHardGates:
    """C2: Challenge stage validation rules and hard gates."""

    def test_challenge_stage_rejects_non_empty_new_claims(self):
        """new_claims must be strictly empty [] during challenge stage."""
        state = _create_v2_opening_state()

        raw = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-4",\n'
            '    "weakest_point": "空头忽略合同负债",\n'
            '    "evidence": ["预收款增加"],\n'
            '    "severity": "major"\n'
            '  }],\n'
            '  "self_win_prob": 0.70,\n'
            '  "new_claims": [{"claim": "新增多头立论", "evidence": ["新证据"], "confidence": 0.80, "target_claim_ids": ["INV-4"]}],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "违规新增立论",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "new_claims" in error_detail

    def test_challenge_stage_requires_at_least_one_challenge(self):
        """challenges field must not be empty."""
        state = _create_v2_opening_state()

        raw = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [],\n'
            '  "self_win_prob": 0.70,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "无challenge",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "challenge" in error_detail.lower()

    def test_challenge_targeting_self_claim_is_rejected(self):
        """Bull targeting Bull claim INV-1 is rejected."""
        state = _create_v2_opening_state()

        raw = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-1",\n'
            '    "weakest_point": "试图攻击己方claim",\n'
            '    "evidence": ["证据"],\n'
            '    "severity": "major"\n'
            '  }],\n'
            '  "self_win_prob": 0.70,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-1"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "攻击己方",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "己方" in error_detail or "self" in error_detail.lower()

    def test_challenge_targeting_nonexistent_claim_is_rejected(self):
        """Targeting non-existent claim ID is rejected."""
        state = _create_v2_opening_state()

        raw = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-999",\n'
            '    "weakest_point": "目标不存在",\n'
            '    "evidence": ["证据"],\n'
            '    "severity": "major"\n'
            '  }],\n'
            '  "self_win_prob": 0.70,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-999"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "目标不存在",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "INV-999" in error_detail

    def test_challenge_severity_must_be_valid_enum(self):
        """severity must be fatal / major / minor."""
        state = _create_v2_opening_state()

        raw = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-4",\n'
            '    "weakest_point": "错误级别",\n'
            '    "evidence": ["证据"],\n'
            '    "severity": "critical_catastrophic"\n'
            '  }],\n'
            '  "self_win_prob": 0.70,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "非法枚举",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "severity" in error_detail.lower()

    def test_challenge_self_win_prob_required_and_bounded(self):
        """self_win_prob must be a finite number between 0.0 and 1.0."""
        state = _create_v2_opening_state()

        # Missing self_win_prob
        raw_missing = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-4",\n'
            '    "weakest_point": "描述",\n'
            '    "evidence": ["证据"],\n'
            '    "severity": "major"\n'
            '  }],\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "缺失胜率",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_missing,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "self_win_prob" in error_detail

        # Out of bounds (> 1.0)
        raw_oob = raw_missing.replace('"new_claims": []', '"self_win_prob": 1.5, "new_claims": []')
        is_valid_oob, parse_status_oob, error_detail_oob, _ = validate_debate_response(
            state=state,
            raw_response=raw_oob,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid_oob is False
        assert parse_status_oob == "invalid_protocol"
        assert "self_win_prob" in error_detail_oob

    def test_state_advances_to_tiebreak_after_both_challenges(self):
        """Bull message 3 keeps stage=challenge; Bear message 4 advances protocol_stage to tiebreak."""
        state = _create_v2_opening_state()

        bull_raw = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-4",\n'
            '    "weakest_point": "描述",\n'
            '    "evidence": ["证据"],\n'
            '    "severity": "major"\n'
            '  }],\n'
            '  "self_win_prob": 0.70,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "多头盘问",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )
        state_after_bull = update_debate_state_with_payload(
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
        assert state_after_bull["protocol_stage"] == "challenge"

        bear_raw = (
            "正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-1",\n'
            '    "weakest_point": "描述",\n'
            '    "evidence": ["证据"],\n'
            '    "severity": "fatal"\n'
            '  }],\n'
            '  "self_win_prob": 0.65,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-1"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "空头盘问",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )
        state_after_bear = update_debate_state_with_payload(
            state=state_after_bull,
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
        assert state_after_bear["protocol_stage"] == "tiebreak"


class TestC3ChallengePromptAndRetryCapture:
    """C3: Challenge prompt formatting (opponents claims only) and Attempt 2 retry handling."""

    def test_render_debate_prompt_challenge_stage_zh(self):
        """render_debate_prompt with is_challenge_stage=True replaces stage framework with challenge contract."""
        from tradingagents.prompts import get_prompt
        from tradingagents.dataflows.config import get_config

        cfg = get_config()
        raw_prompt = get_prompt("bull_prompt", config=cfg)
        rendered = render_debate_prompt(
            raw_prompt,
            is_opening_stage=False,
            is_challenge_stage=True,
            language="zh",
        )
        assert "Challenge 阶段" in rendered or "交叉盘问" in rendered
        assert '"challenges":' in rendered
        assert '"self_win_prob":' in rendered
        assert "new_claims" in rendered

    def test_render_debate_prompt_challenge_stage_en(self):
        """render_debate_prompt in English mirrors challenge stage contract."""
        from tradingagents.prompts.en import PROMPTS

        raw_prompt = PROMPTS["bull_prompt"]
        rendered = render_debate_prompt(
            raw_prompt,
            is_opening_stage=False,
            is_challenge_stage=True,
            language="en",
        )
        assert "Challenge" in rendered
        assert '"challenges":' in rendered
        assert '"self_win_prob":' in rendered

    @pytest.mark.asyncio
    async def test_researcher_node_challenge_stage_filters_claims_and_handles_retry(self):
        """Bull researcher in challenge stage shows only opponent's open claims and executes challenge retry on attempt 2."""
        from tradingagents.agents.researchers.bull_researcher import create_bull_researcher

        state = {
            "investment_debate_state": _create_v2_opening_state(),
            "macro_report": "宏观数据",
            "market_report": "市场数据",
            "sentiment_report": "情绪数据",
            "news_report": "新闻数据",
            "fundamentals_report": "基本面数据",
            "smart_money_report": "资金数据",
            "volume_price_report": "量价数据",
        }

        # Attempt 1 returns legacy format with new_claims; Attempt 2 returns valid challenge format
        attempt1_response = (
            "错误格式\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "new_claims": [{"claim": "违规新增立论", "evidence": ["证据"], "confidence": 0.8, "target_claim_ids": ["INV-4"]}],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": []\n'
            "} -->"
        )
        attempt2_response = (
            "合规盘问正文\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "challenges": [{\n'
            '    "target_claim_id": "INV-4",\n'
            '    "weakest_point": "空头现金流假设存在漏洞",\n'
            '    "evidence": ["三季报预收款35亿"],\n'
            '    "severity": "major"\n'
            '  }],\n'
            '  "self_win_prob": 0.75,\n'
            '  "new_claims": [],\n'
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "盘问成功",\n'
            '  "round_goal": "目标"\n'
            "} -->"
        )

        mock_llm = MagicMock()
        mock_llm.model_name = "test-model"

        async def fake_astream(prompt_text):
            if "协议重试警告" in prompt_text:
                # Attempt 2: verify prompt explicitly mentions challenge rules and lists opponent claims
                assert "Challenge" in prompt_text or "challenge" in prompt_text or "盘问" in prompt_text
                assert "new_claims 必须严格为空数组" in prompt_text or "new_claims" in prompt_text
                yield attempt2_response
            else:
                # Attempt 1: verify prompt presents opponent claims (INV-4..6) but not Bull's own claims
                assert "INV-4" in prompt_text
                yield attempt1_response

        mock_llm.astream = fake_astream
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        bull_node = create_bull_researcher(mock_llm, mock_memory)
        res = await bull_node(state)

        new_inv_state = res["investment_debate_state"]
        assert new_inv_state["count"] == 3
        assert len(new_inv_state["challenges"]) == 1
        assert new_inv_state["challenges"][0]["challenge_id"] == "CH-1"
        assert len(new_inv_state["attempts"]) >= 2
        assert new_inv_state["attempts"][0]["accepted"] is False
        assert new_inv_state["attempts"][1]["accepted"] is True


class TestC4VerifierMappingAndFatalStates:
    """C4: Challenge evidence verification and fatal 3-state evaluation."""

    def test_challenge_evidence_verification_mapping(self):
        """Challenge evidence is evaluated using EvidenceFactualTruthEvaluator and mapped to challenge_verification."""
        from tradingagents.agents.utils.evidence_verifier import evaluate_challenges

        challenges = [
            {
                "challenge_id": "CH-1",
                "speaker_key": "Bull",
                "target_claim_id": "INV-4",
                "weakest_point": "空头忽略合同负债增长",
                "evidence": ["三季报预收款与合同负债达35亿元同比增加45%"],
                "severity": "major",
                "status": "open",
            },
            {
                "challenge_id": "CH-2",
                "speaker_key": "Bear",
                "target_claim_id": "INV-1",
                "weakest_point": "主力资金净流入属于游资对倒席位",
                "evidence": ["未在报告中出现的虚假数据源指标9999亿元"],
                "severity": "fatal",
                "status": "open",
            },
        ]
        seven_reports = {
            "fundamentals_report": "公司三季报预收款与合同负债达35亿元，同比增加45%，在手订单充沛。",
            "smart_money_report": "主力资金净流入1.2亿元，游资活跃。",
        }

        ver_list, updated_challenges = evaluate_challenges(
            challenges=challenges,
            seven_reports=seven_reports,
            market_data_context={},
            analysis_baseline_date="2026-08-20",
        )

        assert len(ver_list) == 2
        # CH-1 verified
        assert ver_list[0]["challenge_id"] == "CH-1"
        assert ver_list[0]["status"] == STATUS_VERIFIED
        assert updated_challenges[0]["evidence_status"] == STATUS_VERIFIED
        assert updated_challenges[0]["status"] == "open"

        # CH-2 unsupported
        assert ver_list[1]["challenge_id"] == "CH-2"
        assert ver_list[1]["status"] == STATUS_UNSUPPORTED
        assert updated_challenges[1]["evidence_status"] == STATUS_UNSUPPORTED
        assert updated_challenges[1]["status"] == "open"

    def test_fatal_contradicted_challenge_is_rejected(self):
        """Contradicted fatal challenge gets evidence_status=contradicted and status=rejected."""
        from tradingagents.agents.utils.evidence_verifier import evaluate_challenges

        challenges = [
            {
                "challenge_id": "CH-1",
                "speaker_key": "Bear",
                "target_claim_id": "INV-1",
                "weakest_point": "主力净流入数据完全造假",
                "evidence": ["主力资金净流出90亿元"],  # Report has 1.2亿元净流入
                "severity": "fatal",
                "status": "open",
            }
        ]
        seven_reports = {
            "smart_money_report": "主力资金净流入1.2亿元，大单买入明显。",
        }

        ver_list, updated_challenges = evaluate_challenges(
            challenges=challenges,
            seven_reports=seven_reports,
            market_data_context={},
            analysis_baseline_date="2026-08-20",
        )

        assert len(ver_list) == 1
        assert ver_list[0]["status"] == STATUS_CONTRADICTED
        assert updated_challenges[0]["evidence_status"] == STATUS_CONTRADICTED
        assert updated_challenges[0]["status"] == "rejected"
