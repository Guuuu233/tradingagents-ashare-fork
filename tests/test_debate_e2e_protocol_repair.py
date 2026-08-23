"""E2E and unit integration tests for DAV-332:
- Mock LLM retry on first bad machine block -> count only increments by 1, 1 accepted message, 2 attempts.
- Consecutive bad machine blocks -> DebateProtocolError raised, horizon failed, manager not invoked.
- Full 6-round successful fixture: Bull/Bear 3 each, subsequent 5 messages respond to & target opponent, claims on both sides.
- Research Manager pre-gate fail-closed on missing messages, invalid parse_status, unequal counts, missing opponent claims.
- Research Manager consistency check rejects nonexistent claim IDs.
- Chinese and English prompt mirror testing for target_claim_ids and round rules.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.utils.debate_utils import (
    DebateProtocolError,
    update_debate_state_with_payload,
    validate_debate_response,
)
from tradingagents.agents.utils.evidence_verifier import extract_and_validate_manager_verdict
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.prompts.en import PROMPTS as EN_PROMPTS
from tradingagents.prompts.zh import PROMPTS as ZH_PROMPTS


async def _fake_stream(text: str):
    yield SimpleNamespace(content=text)


def _make_base_state():
    return {
        "macro_report": "宏观报告：流动性维持宽松，M2增速10.5%。",
        "market_report": "市场报告：突破20.0元关键阻力位，均线多头排列。",
        "sentiment_report": "情绪报告：市场情绪看多占比65%。",
        "news_report": "新闻报告：行业新政落地，新产品在手订单增长50%。",
        "fundamentals_report": "基本面报告：营收同比增长30%，毛利率达到28.5%。",
        "smart_money_report": "主力资金报告：主力净流入5.2亿元，积极建仓。",
        "volume_price_report": "量价报告：放量长阳突破整理平台。",
        "market_data_context": {
            "analysis_baseline_date": "2026-08-22",
            "trade_date": "2026-08-22",
            "source_provenance": {},
            "data_failure_ledger": [],
            "data_gaps": [],
        },
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_speaker": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
            "claims": [],
            "round_messages": [],
            "attempts": [],
            "focus_claim_ids": [],
            "open_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "round_summary": "",
            "round_goal": "建立核心多空 claims",
            "claim_counter": 0,
        },
        "fund_flow_consensus_guard": {
            "blocked": False,
            "direction_allowed": True,
            "status": "consensus",
        },
        "trade_date": "2026-08-22",
        "horizon": "medium",
    }


def _build_valid_six_round_state():
    state = _make_base_state()
    round_messages = [
        {"message_index": 1, "debate_round": 1, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": [], "target_claim_ids": [], "new_claim_ids": ["INV-1"]},
        {"message_index": 2, "debate_round": 1, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-1"], "target_claim_ids": ["INV-1"], "new_claim_ids": ["INV-2"]},
        {"message_index": 3, "debate_round": 2, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-2"], "target_claim_ids": ["INV-2"], "new_claim_ids": ["INV-3"]},
        {"message_index": 4, "debate_round": 2, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-3"], "target_claim_ids": ["INV-3"], "new_claim_ids": ["INV-4"]},
        {"message_index": 5, "debate_round": 3, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-4"], "target_claim_ids": ["INV-4"], "new_claim_ids": ["INV-5"]},
        {"message_index": 6, "debate_round": 3, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-5"], "target_claim_ids": ["INV-5"], "new_claim_ids": ["INV-6"]},
    ]
    claims = [
        {"claim_id": "INV-1", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "营收高增", "evidence": ["营收同比增长30%"], "confidence": 0.85},
        {"claim_id": "INV-2", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "估值透支", "evidence": ["PE处于高位"], "confidence": 0.80},
        {"claim_id": "INV-3", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "订单放量", "evidence": ["在手订单增长50%"], "confidence": 0.90},
        {"claim_id": "INV-4", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "原料涨价", "evidence": ["上游成本上升"], "confidence": 0.75},
        {"claim_id": "INV-5", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "长协锁价", "evidence": ["主力净流入5.2亿元"], "confidence": 0.88},
        {"claim_id": "INV-6", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "竞争加剧", "evidence": ["价格战苗头"], "confidence": 0.78},
    ]
    state["investment_debate_state"]["count"] = 6
    state["investment_debate_state"]["current_speaker"] = "Bear"
    state["investment_debate_state"]["claims"] = claims
    state["investment_debate_state"]["round_messages"] = round_messages
    state["investment_debate_state"]["open_claim_ids"] = [c["claim_id"] for c in claims]
    state["investment_debate_state"]["claim_counter"] = 6
    return state


class TestMockLLMRetryAndRejection:
    def test_mock_llm_first_bad_second_valid_increments_count_by_one(self):
        """Mock LLM: 首次坏块、第二次合法 → count只增1、accepted一条、attempt两条."""
        state = _make_base_state()
        state["investment_debate_state"]["count"] = 1
        state["investment_debate_state"]["current_speaker"] = "Bull"
        state["investment_debate_state"]["claims"] = [
            {"claim_id": "INV-1", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "多头立论", "evidence": ["营收同比增长30%"], "confidence": 0.85, "status": "open"}
        ]
        state["investment_debate_state"]["round_messages"] = [
            {"message_index": 1, "debate_round": 1, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True}
        ]

        attempt_prompts = []
        call_count = 0

        # Attempt 1 has no target_claim_ids -> invalid_protocol in Round 2
        bad_response = (
            "空头发言。\n"
            '<!-- DEBATE_STATE: {"responded_claim_ids": ["INV-1"], "new_claims": [{"claim": "空头反驳", "evidence": ["e"], "confidence": 0.75, "target_claim_ids": []}]} -->'
        )
        # Attempt 2 has target_claim_ids -> valid
        good_response = (
            "空头发言。\n"
            '<!-- DEBATE_STATE: {"responded_claim_ids": ["INV-1"], "new_claims": [{"claim": "空头反驳", "evidence": ["e"], "confidence": 0.75, "target_claim_ids": ["INV-1"]}], "resolved_claim_ids": [], "unresolved_claim_ids": ["INV-1"], "next_focus_claim_ids": ["INV-1"], "round_summary": "空头立论", "round_goal": "立论"} -->'
        )

        def mock_astream(prompt):
            nonlocal call_count
            call_count += 1
            attempt_prompts.append(prompt)
            if call_count == 1:
                return _fake_stream(bad_response)
            return _fake_stream(good_response)

        mock_llm = MagicMock()
        mock_llm.astream = MagicMock(side_effect=mock_astream)
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        bear_node = create_bear_researcher(mock_llm, memory)
        res = asyncio.run(bear_node(state))

        assert call_count == 2
        inv_state = res["investment_debate_state"]
        # count 只增加 1 (from 1 to 2)
        assert inv_state["count"] == 2
        # accepted 消息恰好 1 条来自本轮 (总共 2 条 accepted)
        accepted_msgs = [m for m in inv_state["round_messages"] if m.get("accepted") is True]
        assert len(accepted_msgs) == 2
        # attempts 记录了 2 条尝试
        assert len(inv_state["attempts"]) == 2
        assert inv_state["attempts"][0]["accepted"] is False
        assert inv_state["attempts"][0]["parse_status"] == "invalid_protocol"
        assert inv_state["attempts"][1]["accepted"] is True
        assert inv_state["attempts"][1]["parse_status"] == "valid"

    def test_mock_llm_consecutive_bad_blocks_fails_horizon(self):
        """连续两次坏块 → 抛出 DebateProtocolError，阻止 Research Manager 调用."""
        state = _make_base_state()
        state["investment_debate_state"]["count"] = 1
        state["investment_debate_state"]["claims"] = [
            {"claim_id": "INV-1", "speaker_key": "Bull", "stance": "bullish"}
        ]

        mock_llm = MagicMock()
        mock_llm.astream = MagicMock(return_value=_fake_stream("纯文本，没有机器块。"))
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        bear_node = create_bear_researcher(mock_llm, memory)

        with pytest.raises(DebateProtocolError) as exc_info:
            asyncio.run(bear_node(state))

        assert exc_info.value.message_index == 2
        assert exc_info.value.speaker == "Bear Analyst"
        assert len(exc_info.value.attempts) == 2


class TestResearchManagerHardGateAndClaimsValidation:
    def test_six_round_successful_fixture_passes_manager_gate(self):
        """6轮成功fixture：Bull/Bear各3，后5轮回应+target，claims双方均存在，Manager成功裁决."""
        state = _build_valid_six_round_state()

        manager_output = """【投研经理正式裁决】
综合审阅6轮辩论：多头在手订单与长协锁价逻辑闭环，证据充分。空头提出的原料成本风险已被长协对冲。判定多头胜出。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "多头在手订单充足且长协锁价有效", "position_pct": 60, "entry": "20.0-20.5", "target": "25.0", "stop_loss": "19.0", "upside": 25.0, "downside": 5.0, "odds": 5.0, "adopted_claim_ids": ["INV-1", "INV-3"], "rejected_claim_ids": ["INV-2"]} -->
<!-- VERDICT: {"direction": "看多", "reason": "多头逻辑闭环"} -->"""

        mock_llm = MagicMock()
        mock_llm.astream = MagicMock(side_effect=lambda prompt: _fake_stream(manager_output))
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        rm_node = create_research_manager(mock_llm, memory)
        res = asyncio.run(rm_node(state))

        verdict = res["manager_verdict"]
        assert verdict["consistency_check_passed"] is True
        assert verdict["winner"] == "bull"
        assert verdict["adopted_claim_ids"] == ["INV-1", "INV-3"]
        assert "裁决自洽硬闸未通过" not in res["investment_plan"]

    def test_manager_pre_gate_fails_when_missing_bear_messages(self):
        """manager前置缺Bear/invalid消息必须红."""
        state = _build_valid_six_round_state()
        # Make message 6 an invalid_protocol message
        state["investment_debate_state"]["round_messages"][5] = {
            "message_index": 6,
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "parse_status": "invalid_protocol",
            "accepted": False,
        }

        mock_llm = MagicMock()
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        rm_node = create_research_manager(mock_llm, memory)
        res = asyncio.run(rm_node(state))

        mock_llm.astream.assert_not_called()
        verdict = res["manager_verdict"]
        assert verdict["consistency_check_passed"] is False
        assert any("有效辩论轮次不足6次" in err for err in verdict["failed_checks"])
        assert "辩论前置硬闸未通过" in res["investment_plan"]

    def test_manager_pre_gate_fails_when_subsequent_round_missing_target(self):
        """manager前置后5轮未target对手必须红."""
        state = _build_valid_six_round_state()
        # Message 2 has empty target_claim_ids
        state["investment_debate_state"]["round_messages"][1]["target_claim_ids"] = []

        mock_llm = MagicMock()
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        rm_node = create_research_manager(mock_llm, memory)
        res = asyncio.run(rm_node(state))

        mock_llm.astream.assert_not_called()
        verdict = res["manager_verdict"]
        assert verdict["consistency_check_passed"] is False
        assert any("未在 target_claim_ids 中针对对手 claim" in err for err in verdict["failed_checks"])
        assert "辩论前置硬闸未通过" in res["investment_plan"]

    def test_manager_verdict_rejects_nonexistent_claim_id(self):
        """manager不得采纳不存在的 claim ID."""
        raw_output = """【投研经理裁决】
判定多头胜出。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "采纳不存在claim", "position_pct": 60, "entry": "20.0", "target": "25.0", "stop_loss": "19.0", "adopted_claim_ids": ["INV-999"], "rejected_claim_ids": []} -->"""

        claims = [
            {"claim_id": "INV-1", "speaker_key": "Bull", "stance": "bullish"},
            {"claim_id": "INV-2", "speaker_key": "Bear", "stance": "bearish"},
        ]
        verdict = extract_and_validate_manager_verdict(raw_output, claims=claims)
        assert verdict["consistency_check_passed"] is False
        assert any("不存在的 claim ID: INV-999" in err for err in verdict["failed_checks"])


class TestPromptContractsChineseAndEnglish:
    def test_chinese_prompt_has_target_claim_ids_in_machine_block(self):
        bull_prompt = ZH_PROMPTS["bull_prompt"]
        bear_prompt = ZH_PROMPTS["bear_prompt"]

        assert '"target_claim_ids": ["INV-2"]' in bull_prompt or '"target_claim_ids": ["INV-1"]' in bull_prompt
        assert '"target_claim_ids": ["INV-1"]' in bear_prompt

        assert "第1次发言（多头首轮立论）：responded_claim_ids 为空数组 []，每个 new_claim 的 target_claim_ids 为空数组 []" in bull_prompt
        assert "第2至第6次发言（攻防反驳）：responded_claim_ids 必须包含所回应的对手未解决 claim ID" in bull_prompt
        assert "第2至第6次发言（攻防反驳）：responded_claim_ids 必须包含所回应的对手未解决 claim ID" in bear_prompt

    def test_english_prompt_has_target_claim_ids_in_machine_block(self):
        bull_prompt = EN_PROMPTS["bull_prompt"]
        bear_prompt = EN_PROMPTS["bear_prompt"]

        assert '"target_claim_ids": ["INV-2"]' in bull_prompt or '"target_claim_ids": ["INV-1"]' in bull_prompt
        assert '"target_claim_ids": ["INV-1"]' in bear_prompt

        assert "Message 1 (Bull Round 1): responded_claim_ids is [], target_claim_ids is []" in bull_prompt
        assert "Messages 2-6 (Rebuttals): responded_claim_ids must contain opponent claim ID" in bull_prompt
        assert "Messages 2-6 (Rebuttals): responded_claim_ids must contain opponent claim ID" in bear_prompt


class TestConditionalLogicRoutingGate:
    def test_conditional_logic_blocks_on_protocol_blocked_state(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        state = {
            "investment_debate_state": {
                "count": 2,
                "blocked": True,
                "parse_status": "invalid_protocol",
                "block_reason": "协议错误",
            }
        }
        with pytest.raises(DebateProtocolError):
            logic.should_continue_debate(state)

    def test_conditional_logic_advances_to_research_manager_only_on_valid_six_messages(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        state = {
            "investment_debate_state": {
                "count": 6,
                "current_speaker": "Bear",
                "round_messages": [
                    {"message_index": i, "parse_status": "valid", "accepted": True}
                    for i in range(1, 7)
                ],
            }
        }
        next_node = logic.should_continue_debate(state)
        assert next_node == "Research Manager"

    def test_conditional_logic_alternates_bull_and_bear(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        state1 = {
            "investment_debate_state": {
                "count": 1,
                "current_speaker": "Bull",
                "round_messages": [{"message_index": 1, "parse_status": "valid", "accepted": True}],
            }
        }
        assert logic.should_continue_debate(state1) == "Bear Researcher"

        state2 = {
            "investment_debate_state": {
                "count": 2,
                "current_speaker": "Bear",
                "round_messages": [
                    {"message_index": 1, "parse_status": "valid", "accepted": True},
                    {"message_index": 2, "parse_status": "valid", "accepted": True},
                ],
            }
        }
        assert logic.should_continue_debate(state2) == "Bull Researcher"
