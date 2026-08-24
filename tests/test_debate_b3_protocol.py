"""Tests for P1-B / Stage 3 (B3): Tiebreak routing, manager prompt parameterization, dispute map & degenerate detection."""

from __future__ import annotations

from typing import Any, Mapping
import pytest

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
    get_protocol_metadata,
    is_v2_debate_enabled,
)
from tradingagents.agents.utils.debate_utils import (
    DebateProtocolError,
    detect_debate_degenerate,
    update_debate_state_with_payload,
    validate_debate_response,
)
from tradingagents.agents.utils.evidence_verifier import (
    DECISION_ADOPT,
    DECISION_PARTIAL,
    DECISION_REJECT,
    EvidenceFactualTruthEvaluator,
    extract_and_validate_manager_verdict,
    format_battlefield_coverage,
    format_challenge_verification_summary,
    format_challenges_for_prompt,
    normalize_winner,
)
from tradingagents.graph.conditional_logic import ConditionalLogic, should_enter_tiebreak
from tradingagents.prompts import get_prompt


def _build_v2_opening_completed_state() -> dict[str, Any]:
    """Helper to build v2 state with 2 opening messages completed."""
    claims = [
        {
            "claim_id": "INV-1",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "主力净流入1.29亿元，超大单持续吸筹",
            "evidence": ["东财主力净流入1.29亿元"],
            "confidence": 0.85,
            "status": "open",
            "round_index": 1,
            "debate_round": 1,
            "message_index": 1,
            "stage": "opening",
            "battlefield": "capital_flow",
        },
        {
            "claim_id": "INV-2",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "技术面突破20日均线，成交量温和放大",
            "evidence": ["收盘价站上20日均线20.5元"],
            "confidence": 0.80,
            "status": "open",
            "round_index": 1,
            "debate_round": 1,
            "message_index": 1,
            "stage": "opening",
            "battlefield": "price_volume",
        },
        {
            "claim_id": "INV-3",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "三季报扣非净利润增长25%，基本面稳健",
            "evidence": ["前三季度扣非净利15.2亿元同比增25%"],
            "confidence": 0.82,
            "status": "open",
            "round_index": 1,
            "debate_round": 1,
            "message_index": 1,
            "stage": "opening",
            "battlefield": "fundamentals",
        },
        {
            "claim_id": "INV-4",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "全单资金净流出2.46亿元，大单持续抛压",
            "evidence": ["同花顺全单净流出2.46亿元"],
            "confidence": 0.80,
            "status": "open",
            "round_index": 2,
            "debate_round": 1,
            "message_index": 2,
            "stage": "opening",
            "battlefield": "capital_flow",
        },
        {
            "claim_id": "INV-5",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "情绪面高位分歧加剧，行业龙虎榜机构净卖出",
            "evidence": ["龙虎榜机构席位净卖出8200万元"],
            "confidence": 0.78,
            "status": "open",
            "round_index": 2,
            "debate_round": 1,
            "message_index": 2,
            "stage": "opening",
            "battlefield": "sentiment_theme",
        },
        {
            "claim_id": "INV-6",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "行业监管新规征求意见，宏观合规成本上升",
            "evidence": ["行业监管新规征求意见稿发布"],
            "confidence": 0.75,
            "status": "open",
            "round_index": 2,
            "debate_round": 1,
            "message_index": 2,
            "stage": "opening",
            "battlefield": "macro_policy",
        },
    ]
    round_messages = [
        {
            "message_index": 1,
            "debate_round": 1,
            "stage": "opening",
            "protocol_stage": "opening",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "cleaned_prose": "多头独立立论",
            "parse_status": "valid",
            "accepted": True,
            "new_claim_ids": ["INV-1", "INV-2", "INV-3"],
            "responded_claim_ids": [],
            "target_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "round_summary": "多头完成独立立论",
            "self_win_prob": 0.85,
        },
        {
            "message_index": 2,
            "debate_round": 1,
            "stage": "opening",
            "protocol_stage": "opening",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "cleaned_prose": "空头独立立论",
            "parse_status": "valid",
            "accepted": True,
            "new_claim_ids": ["INV-4", "INV-5", "INV-6"],
            "responded_claim_ids": [],
            "target_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "round_summary": "空头完成独立立论",
            "self_win_prob": 0.80,
        },
    ]
    return {
        "history": "Bull Analyst: 多头立论\nBear Analyst: 空头立论",
        "bull_history": "Bull Analyst: 多头立论",
        "bear_history": "Bear Analyst: 空头立论",
        "current_speaker": "Bear",
        "count": 2,
        "claims": claims,
        "claim_counter": 6,
        "challenges": [],
        "challenge_counter": 0,
        "open_claim_ids": [f"INV-{i}" for i in range(1, 7)],
        "resolved_claim_ids": [],
        "unresolved_claim_ids": [f"INV-{i}" for i in range(1, 7)],
        "focus_claim_ids": ["INV-4", "INV-1"],
        "round_summary": "双盲立论阶段结束",
        "round_messages": round_messages,
        "protocol_stage": "challenge",
        "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
        "feature_flags": {
            "v2_debate_enabled": True,
            "shadow_credit_enabled": True,
            "credit_weighting_enabled": False,
        },
        "tiebreak_skipped": False,
        "debate_degenerate": False,
        "belief_trajectory": [
            {"stage": "opening", "message_index": 1, "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "self_win_prob": 0.85, "debate_round": 1},
            {"stage": "opening", "message_index": 2, "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "self_win_prob": 0.80, "debate_round": 1},
        ],
    }


def _build_v2_challenge_completed_state() -> dict[str, Any]:
    """Helper to build v2 state with 4 messages completed (opening x2 + challenge x2)."""
    state = _build_v2_opening_completed_state()
    # Add msg 3 (Bull challenge)
    bull_challenge = {
        "challenge_id": "CH-1",
        "speaker": "Bull Analyst",
        "speaker_key": "Bull",
        "stance": "bullish",
        "target_claim_id": "INV-4",
        "weakest_point": "空头只看全单净流出，忽略了主力超大单分歧",
        "evidence": ["东财主力净流入1.29亿元"],
        "severity": "major",
        "status": "open",
        "evidence_status": "verified",
        "message_index": 3,
        "debate_round": 2,
        "stage": "challenge",
    }
    # Add msg 4 (Bear challenge)
    bear_challenge = {
        "challenge_id": "CH-2",
        "speaker": "Bear Analyst",
        "speaker_key": "Bear",
        "stance": "bearish",
        "target_claim_id": "INV-1",
        "weakest_point": "多头忽略全单净流出背离风险",
        "evidence": ["同花顺全单净流出2.46亿元"],
        "severity": "major",
        "status": "open",
        "evidence_status": "verified",
        "message_index": 4,
        "debate_round": 2,
        "stage": "challenge",
    }
    state["challenges"] = [bull_challenge, bear_challenge]
    state["challenge_counter"] = 2
    state["count"] = 4
    state["current_speaker"] = "Bear"
    state["protocol_stage"] = "tiebreak"

    msg3 = {
        "message_index": 3,
        "debate_round": 2,
        "stage": "challenge",
        "protocol_stage": "challenge",
        "speaker": "Bull Analyst",
        "speaker_key": "Bull",
        "cleaned_prose": "多头交叉盘问反驳",
        "parse_status": "valid",
        "accepted": True,
        "responded_claim_ids": ["INV-4"],
        "new_claim_ids": [],
        "target_claim_ids": ["INV-4"],
        "resolved_claim_ids": [],
        "unresolved_claim_ids": ["INV-4"],
        "challenge_ids": ["CH-1"],
        "self_win_prob": 0.85,
        "round_summary": "多头盘问空头资金流",
    }
    msg4 = {
        "message_index": 4,
        "debate_round": 2,
        "stage": "challenge",
        "protocol_stage": "challenge",
        "speaker": "Bear Analyst",
        "speaker_key": "Bear",
        "cleaned_prose": "空头交叉盘问反驳",
        "parse_status": "valid",
        "accepted": True,
        "responded_claim_ids": ["INV-1"],
        "new_claim_ids": [],
        "target_claim_ids": ["INV-1"],
        "resolved_claim_ids": [],
        "unresolved_claim_ids": ["INV-1"],
        "challenge_ids": ["CH-2"],
        "self_win_prob": 0.80,
        "round_summary": "空头盘问多头资金流",
    }
    state["round_messages"].extend([msg3, msg4])
    state["belief_trajectory"].extend([
        {"stage": "challenge", "message_index": 3, "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "self_win_prob": 0.85, "debate_round": 2},
        {"stage": "challenge", "message_index": 4, "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "self_win_prob": 0.80, "debate_round": 2},
    ])
    return state


# ══════════════════════════════════════════════════════════════════════════════
# 1. B3.1 Tiebreak Routing & Conditional Logic Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestB31TiebreakRouting:
    """Test conditional_logic.should_continue_debate across v1 and v2 stages."""

    def test_legacy_mode_routes_exactly_six_messages(self):
        """In v1 legacy mode, debate alternates between Bull/Bear until count >= 6."""
        logic = ConditionalLogic(max_debate_rounds=3)
        assert logic.should_continue_debate({"investment_debate_state": {"count": 0, "current_speaker": "Bull"}}) == "Bear Researcher"
        assert logic.should_continue_debate({"investment_debate_state": {"count": 1, "current_speaker": "Bear"}}) == "Bull Researcher"
        assert logic.should_continue_debate({"investment_debate_state": {"count": 2, "current_speaker": "Bull"}}) == "Bear Researcher"
        assert logic.should_continue_debate({"investment_debate_state": {"count": 3, "current_speaker": "Bear"}}) == "Bull Researcher"
        assert logic.should_continue_debate({"investment_debate_state": {"count": 4, "current_speaker": "Bull"}}) == "Bear Researcher"
        assert logic.should_continue_debate({"investment_debate_state": {"count": 5, "current_speaker": "Bear"}}) == "Bull Researcher"
        assert logic.should_continue_debate({"investment_debate_state": {"count": 6, "current_speaker": "Bull"}}) == "Research Manager"

    def test_v2_opening_stage_routing(self):
        """In v2 opening stage, count=0 -> Bull, count=1 -> Bear, count=2 -> Bull (for challenge)."""
        logic = ConditionalLogic(max_debate_rounds=3)
        s0 = {"investment_debate_state": {"protocol_version": PROTOCOL_VERSION_V2_STRUCTURED, "protocol_stage": "opening", "count": 0}}
        s1 = {"investment_debate_state": {"protocol_version": PROTOCOL_VERSION_V2_STRUCTURED, "protocol_stage": "opening", "count": 1, "current_speaker": "Bull"}}
        s2 = {"investment_debate_state": {"protocol_version": PROTOCOL_VERSION_V2_STRUCTURED, "protocol_stage": "opening", "count": 2, "current_speaker": "Bear"}}

        assert logic.should_continue_debate(s0) == "Bull Researcher"
        assert logic.should_continue_debate(s1) == "Bear Researcher"
        assert logic.should_continue_debate(s2) == "Bull Researcher"

    def test_v2_challenge_stage_routing_to_bear(self):
        """In v2 challenge stage, count=3 (Bull challenge done) routes to Bear Researcher."""
        logic = ConditionalLogic(max_debate_rounds=3)
        s3 = {
            "investment_debate_state": {
                "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
                "protocol_stage": "challenge",
                "count": 3,
                "current_speaker": "Bull",
            }
        }
        assert logic.should_continue_debate(s3) == "Bear Researcher"

    def test_v2_challenge_completed_without_tiebreak_routes_directly_to_manager(self):
        """When challenge stage completes (count=4) and no tiebreak is required, route directly to Research Manager and set tiebreak_skipped=True."""
        logic = ConditionalLogic(max_debate_rounds=3)
        inv_state = _build_v2_challenge_completed_state()
        state = {"investment_debate_state": inv_state}

        # Default state has no explicit tiebreak request
        route = logic.should_continue_debate(state)
        assert route == "Research Manager", f"Expected Research Manager, got {route}"
        assert inv_state["tiebreak_skipped"] is True, "State must set tiebreak_skipped=True"
        assert inv_state["protocol_stage"] == "manager", "State protocol_stage must be set to 'manager'"

    def test_v2_challenge_completed_with_tiebreak_required_routes_to_bull(self):
        """When challenge completes and requires_tiebreak=True, route to Bull Researcher for tiebreak (msg 5)."""
        logic = ConditionalLogic(max_debate_rounds=3)
        inv_state = _build_v2_challenge_completed_state()
        inv_state["requires_tiebreak"] = True
        state = {"investment_debate_state": inv_state}

        route = logic.should_continue_debate(state)
        assert route == "Bull Researcher", f"Expected Bull Researcher for tiebreak msg 5, got {route}"
        assert inv_state["tiebreak_skipped"] is False, "State must have tiebreak_skipped=False"
        assert inv_state["protocol_stage"] == "tiebreak", "State protocol_stage must be 'tiebreak'"

    def test_v2_tiebreak_stage_routing_progression(self):
        """In tiebreak stage: count=4 -> Bull, count=5 -> Bear, count=6 -> Research Manager."""
        logic = ConditionalLogic(max_debate_rounds=3)
        s4 = {"investment_debate_state": {"protocol_version": PROTOCOL_VERSION_V2_STRUCTURED, "protocol_stage": "tiebreak", "count": 4, "requires_tiebreak": True}}
        s5 = {"investment_debate_state": {"protocol_version": PROTOCOL_VERSION_V2_STRUCTURED, "protocol_stage": "tiebreak", "count": 5, "current_speaker": "Bull"}}
        s6 = {"investment_debate_state": {"protocol_version": PROTOCOL_VERSION_V2_STRUCTURED, "protocol_stage": "tiebreak", "count": 6, "current_speaker": "Bear"}}

        assert logic.should_continue_debate(s4) == "Bull Researcher"
        assert logic.should_continue_debate(s5) == "Bear Researcher"
        assert logic.should_continue_debate(s6) == "Research Manager"

    def test_fail_closed_on_blocked_debate_state(self):
        """When debate state is blocked, should_continue_debate must raise DebateProtocolError."""
        logic = ConditionalLogic(max_debate_rounds=3)
        blocked_state = {
            "investment_debate_state": {
                "blocked": True,
                "parse_status": "invalid_protocol",
                "block_reason": "协议校验失败",
            }
        }
        with pytest.raises(DebateProtocolError, match="Debate state is blocked"):
            logic.should_continue_debate(blocked_state)


# ══════════════════════════════════════════════════════════════════════════════
# 2. B3.2 Manager Prompt Parameterization & De-hardcoding Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestB32ManagerPromptParameterization:
    """Test that research manager prompt dynamically reflects actual messages and stages."""

    def test_zh_prompt_has_no_hardcoded_three_rounds_and_formats_successfully(self):
        """zh research_manager_prompt must not hardcode '基于3轮辩论严格执行' and format with actual params."""
        raw_prompt = get_prompt("research_manager_prompt", config={"prompt_language": "zh"})
        assert "基于3轮辩论严格执行" not in raw_prompt, "Prompt must not contain hardcoded '基于3轮辩论严格执行'"
        assert "{actual_message_count}" in raw_prompt
        assert "{actual_stages_desc}" in raw_prompt
        assert "{tiebreak_status_desc}" in raw_prompt
        assert "{challenges_text}" in raw_prompt
        assert "{challenge_verification_text}" in raw_prompt
        assert "{battlefield_coverage_text}" in raw_prompt

        formatted = raw_prompt.format(
            past_memory_str="无历史经验",
            provenance_context="基准日期: 2026-08-21",
            history="多空历史",
            smart_money_report="主力资金流入1.29亿",
            volume_price_report="放量站上均线",
            sentiment_report="情绪乐观",
            market_evidence_summary="技术面突破",
            news_evidence_summary="行业景气度上升",
            fundamentals_evidence_summary="扣非净利增长25%",
            macro_evidence_line="宏观证据: 货币政策宽松",
            claims_text="INV-1: 主力吸筹",
            unresolved_claims_text="无未决 claim",
            round_summary="辩论完成",
            actual_message_count=4,
            actual_stages_desc="覆盖阶段: opening, challenge",
            tiebreak_status_desc="已跳过加赛(证据足以裁决)",
            challenges_text="CH-1: 盘问全单流出",
            challenge_verification_text="Verified=1",
            battlefield_coverage_text="多头覆盖: 3/5 | 空头覆盖: 3/5",
            custom_prompt_before_data="",
            custom_prompt_after_data="",
        )
        assert "基于实际 4 次发言" in formatted
        assert "已跳过加赛" in formatted
        assert "CH-1: 盘问全单流出" in formatted

    def test_en_prompt_formats_successfully_with_parameterized_fields(self):
        """en research_manager_prompt formats successfully with parameterized fields."""
        raw_prompt = get_prompt("research_manager_prompt", config={"prompt_language": "en"})
        assert "{actual_message_count}" in raw_prompt
        assert "{actual_stages_desc}" in raw_prompt
        assert "{tiebreak_status_desc}" in raw_prompt

        formatted = raw_prompt.format(
            past_memory_str="None",
            provenance_context="Baseline: 2026-08-21",
            history="Debate history",
            smart_money_report="Inflow 1.29B",
            volume_price_report="Above MA20",
            sentiment_report="Bullish",
            market_evidence_summary="Tech breakout",
            news_evidence_summary="Industry policy",
            fundamentals_evidence_summary="Net profit +25%",
            macro_evidence_line="Macro: easing",
            claims_text="INV-1: Institutional accumulation",
            unresolved_claims_text="None",
            round_summary="Debate complete",
            actual_message_count=4,
            actual_stages_desc="stages: opening, challenge",
            tiebreak_status_desc="tiebreak skipped",
            challenges_text="CH-1: challenged outflow",
            challenge_verification_text="Verified=1",
            battlefield_coverage_text="Bull: 3/5 | Bear: 3/5",
            custom_prompt_before_data="",
            custom_prompt_after_data="",
        )
        assert "actual 4 messages" in formatted
        assert "tiebreak skipped" in formatted

    def test_format_challenges_for_prompt_helper(self):
        """format_challenges_for_prompt produces clean human-readable list with badges."""
        challenges = [
            {
                "challenge_id": "CH-1",
                "speaker": "Bull Analyst",
                "target_claim_id": "INV-4",
                "weakest_point": "忽略主力超大单分歧",
                "evidence": ["东财主力净流入1.29亿元"],
                "severity": "major",
                "status": "open",
            }
        ]
        verifications = [
            {
                "challenge_id": "CH-1",
                "evidence_status": "verified",
            }
        ]
        res = format_challenges_for_prompt(challenges, challenge_verification=verifications)
        assert "CH-1" in res
        assert "Bull Analyst 攻击对手 INV-4" in res
        assert "【证据核验: verified】" in res

    def test_format_battlefield_coverage_helper(self):
        """format_battlefield_coverage correctly aggregates and counts battlefields."""
        claims = [
            {"speaker_key": "Bull", "battlefield": "capital_flow"},
            {"speaker_key": "Bull", "battlefield": "price_volume"},
            {"speaker_key": "Bull", "battlefield": "fundamentals"},
            {"speaker_key": "Bear", "battlefield": "capital_flow"},
            {"speaker_key": "Bear", "battlefield": "macro_policy"},
            {"speaker_key": "Bear", "battlefield": "sentiment_theme"},
        ]
        res = format_battlefield_coverage(claims)
        assert "多头覆盖战场 (3/5)" in res
        assert "空头覆盖战场 (3/5)" in res


# ══════════════════════════════════════════════════════════════════════════════
# 3. B3.3 Dispute Map & Fatal Challenge Consistency Gate Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestB33DisputeMapAndChallengeConsistency:
    """Test dispute map extraction and fatal challenge consistency rules in manager verdict."""

    def test_dispute_map_extracted_and_normalized(self):
        """MANAGER_VERDICT containing dispute_map is extracted and normalized."""
        raw_response = (
            "研究总监正式报告正文...\n\n"
            "<!-- MANAGER_VERDICT: {\n"
            '  "winner": "bull",\n'
            '  "direction": "看多",\n'
            '  "reason": "多头证据扎实闭环",\n'
            '  "position_pct": 60,\n'
            '  "entry": "20.5",\n'
            '  "target": "25.0",\n'
            '  "stop_loss": "19.0",\n'
            '  "adopted_claim_ids": ["INV-1"],\n'
            '  "rejected_claim_ids": [],\n'
            '  "dispute_map": [\n'
            "    {\n"
            '      "data_point": "主力资金净流入1.29亿/全单净流出2.46亿",\n'
            '      "bull_interpretation": "主力机构吸筹",\n'
            '      "bear_interpretation": "散户与外资出逃",\n'
            '      "evidence_decision": "主力资金数据可信度更高",\n'
            '      "winner": "bull"\n'
            "    }\n"
            "  ]\n"
            "} -->"
        )
        claims = [
            {"claim_id": "INV-1", "speaker_key": "Bull", "evidence": ["东财主力净流入1.29亿元"]}
        ]
        claims_verification = [
            {"claim_id": "INV-1", "status": "verified", "raw": "东财主力净流入1.29亿元"}
        ]

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw_response,
            claims_verification=claims_verification,
            claims=claims,
        )

        assert verdict["consistency_check_passed"] is True, f"Failed checks: {verdict['failed_checks']}"
        assert len(verdict["dispute_map"]) == 1
        d0 = verdict["dispute_map"][0]
        assert d0["data_point"] == "主力资金净流入1.29亿/全单净流出2.46亿"
        assert d0["winner"] == "bull"

    def test_unverified_fatal_challenge_cannot_reject_verified_claim(self):
        """An unverified fatal challenge (evidence unsupported) cannot reject a 100% verified claim."""
        raw_response = (
            "研究总监正式报告...\n\n"
            "<!-- MANAGER_VERDICT: {\n"
            '  "winner": "bear",\n'
            '  "direction": "看空",\n'
            '  "reason": "采信空头致命盘问",\n'
            '  "position_pct": 10,\n'
            '  "adopted_claim_ids": [],\n'
            '  "rejected_claim_ids": ["INV-1"]\n'
            "} -->"
        )
        claims = [
            {"claim_id": "INV-1", "speaker_key": "Bull", "evidence": ["东财主力净流入1.29亿元"]}
        ]
        claims_verification = [
            {"claim_id": "INV-1", "status": "verified", "raw": "东财主力净流入1.29亿元"}
        ]
        # Fatal challenge whose evidence is UNSUPPORTED
        challenges = [
            {
                "challenge_id": "CH-1",
                "target_claim_id": "INV-1",
                "severity": "fatal",
                "status": "open",
                "evidence_status": "unsupported",
            }
        ]
        challenges_verification = [
            {
                "challenge_id": "CH-1",
                "target_claim_id": "INV-1",
                "severity": "fatal",
                "evidence_status": "unsupported",
            }
        ]

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw_response,
            claims_verification=claims_verification,
            claims=claims,
            challenges=challenges,
            challenges_verification=challenges_verification,
        )

        assert verdict["consistency_check_passed"] is False
        assert any("未经验证的 fatal challenge" in err for err in verdict["failed_checks"])

    def test_contradicted_fatal_challenge_must_be_rejected(self):
        """A fatal challenge with contradicted evidence must be rejected, not adopted."""
        raw_response = (
            "研究总监报告...\n\n"
            "<!-- MANAGER_VERDICT: {\n"
            '  "winner": "bear",\n'
            '  "direction": "看空",\n'
            '  "reason": "采纳冲突盘问",\n'
            '  "adopted_challenge_ids": ["CH-1"],\n'
            '  "adopted_claim_ids": []\n'
            "} -->"
        )
        challenges = [
            {
                "challenge_id": "CH-1",
                "target_claim_id": "INV-1",
                "severity": "fatal",
                "status": "adopted",
                "evidence_status": "contradicted",
            }
        ]
        challenges_verification = [
            {
                "challenge_id": "CH-1",
                "target_claim_id": "INV-1",
                "severity": "fatal",
                "evidence_status": "contradicted",
            }
        ]

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw_response,
            claims=[],
            challenges=challenges,
            challenges_verification=challenges_verification,
        )

        assert verdict["consistency_check_passed"] is False
        assert any("存在事实冲突的 fatal challenge" in err for err in verdict["failed_checks"])


# ══════════════════════════════════════════════════════════════════════════════
# 4. B3.4 Belief Trajectory & Degenerate Detection Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestB34BeliefTrajectoryAndDegenerateDetection:
    """Test belief trajectory recording and stationary debate degenerate detection."""

    def test_update_debate_state_records_belief_trajectory(self):
        """Every valid message in v2 appends a new belief trajectory entry."""
        state = _build_v2_opening_completed_state()
        msg3_raw = (
            "多头盘问\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-4"],\n'
            '  "new_claims": [],\n'
            '  "challenges": [\n'
            '    {\n'
            '      "target_claim_id": "INV-4",\n'
            '      "weakest_point": "空头全单净流出未区分主力散户",\n'
            '      "evidence": ["东财主力净流入1.29亿元"],\n'
            '      "severity": "major"\n'
            '    }\n'
            '  ],\n'
            '  "self_win_prob": 0.70,\n'
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-4"],\n'
            '  "next_focus_claim_ids": ["INV-4"],\n'
            '  "round_summary": "多头进攻",\n'
            '  "round_goal": "击穿空头资金流"\n'
            "} -->"
        )

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=msg3_raw,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        trajectory = new_state.get("belief_trajectory", [])
        assert len(trajectory) == 3, f"Expected 3 trajectory entries, got {len(trajectory)}"
        latest = trajectory[-1]
        assert latest["message_index"] == 3
        assert latest["speaker_key"] == "Bull"
        assert latest["self_win_prob"] == 0.70
        assert latest["stage"] == "challenge"

    def test_detect_debate_degenerate_true_when_win_probabilities_unmoved(self):
        """When both Bull and Bear win probabilities remain unchanged across stages, debate_degenerate is True."""
        state = _build_v2_challenge_completed_state()
        # In _build_v2_challenge_completed_state:
        # Bull probs: [0.85, 0.85] -> delta = 0.0
        # Bear probs: [0.80, 0.80] -> delta = 0.0
        assert detect_debate_degenerate(state) is True

    def test_detect_debate_degenerate_false_when_win_probabilities_adjust(self):
        """When either side adjusts win probability in response to challenge, debate_degenerate is False."""
        state = _build_v2_challenge_completed_state()
        # Modify Bull msg 3 probability to show movement
        state["belief_trajectory"][2]["self_win_prob"] = 0.65
        assert detect_debate_degenerate(state) is False

    def test_legacy_mode_always_returns_degenerate_false(self):
        """In legacy v1 mode, debate_degenerate is always False."""
        legacy_state = {
            "protocol_version": PROTOCOL_VERSION_V1_LEGACY,
            "belief_trajectory": [
                {"speaker_key": "Bull", "self_win_prob": 0.85},
                {"speaker_key": "Bull", "self_win_prob": 0.85},
                {"speaker_key": "Bear", "self_win_prob": 0.80},
                {"speaker_key": "Bear", "self_win_prob": 0.80},
            ],
        }
        assert detect_debate_degenerate(legacy_state) is False


# ══════════════════════════════════════════════════════════════════════════════
# 5. B3.5 Full Four-Message and Six-Message Protocol Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestB35FullProtocolIntegration:
    """Test end-to-end multi-message v2 flows with skipped vs executed tiebreak."""

    def test_four_message_v2_flow_with_skipped_tiebreak(self):
        """4 messages in v2 with skipped tiebreak produce canonical protocol metadata."""
        state = _build_v2_challenge_completed_state()
        logic = ConditionalLogic(max_debate_rounds=3)

        next_node = logic.should_continue_debate({"investment_debate_state": state})
        assert next_node == "Research Manager"
        assert state["tiebreak_skipped"] is True
        assert state["protocol_stage"] == "manager"

        meta = get_protocol_metadata(state)
        assert meta["protocol_version"] == PROTOCOL_VERSION_V2_STRUCTURED
        assert meta["tiebreak_skipped"] is True
        assert meta["debate_degenerate"] is True

    def test_six_message_v2_flow_with_executed_tiebreak(self):
        """6 messages in v2 with executed tiebreak produce tiebreak_skipped=False."""
        state = _build_v2_challenge_completed_state()
        state["requires_tiebreak"] = True
        logic = ConditionalLogic(max_debate_rounds=3)

        # After msg 4 -> Bull Researcher for tiebreak msg 5
        assert logic.should_continue_debate({"investment_debate_state": state}) == "Bull Researcher"
        assert state["tiebreak_skipped"] is False
        assert state["protocol_stage"] == "tiebreak"

        # Simulate msg 5 completed
        state["count"] = 5
        state["current_speaker"] = "Bull"
        state["round_messages"].append({"message_index": 5, "stage": "tiebreak", "parse_status": "valid", "accepted": True, "speaker": "Bull"})
        assert logic.should_continue_debate({"investment_debate_state": state}) == "Bear Researcher"

        # Simulate msg 6 completed
        state["count"] = 6
        state["current_speaker"] = "Bear"
        state["round_messages"].append({"message_index": 6, "stage": "tiebreak", "parse_status": "valid", "accepted": True, "speaker": "Bear"})
        assert logic.should_continue_debate({"investment_debate_state": state}) == "Research Manager"
        assert state["protocol_stage"] == "manager"
