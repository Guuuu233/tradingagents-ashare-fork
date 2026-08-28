"""Unit and integration tests for DAV-333:
Debate Information Gain and Anti-Repetition Hard Gate.

Requirements:
1. Normalized ngram / text similarity with threshold 0.82 and new evidence detection.
2. Exact duplicate claim fails validation (RED -> GREEN).
3. Synonymous paraphrase with same evidence fails validation (RED -> GREEN).
4. New claim with new numerical data and causal chain / new evidence passes (GREEN).
5. round_messages and attempts record information_gain_score, duplicate_claim_ids, duplicate_claims, new_evidence_count.
6. Researcher retry mechanism: 1st attempt duplicate, 2nd attempt new claim -> count increments by 1, 1 accepted message, 2 attempts.
7. Consecutive 2 duplicate attempts -> raises DebateProtocolError.
8. Research Manager pre-gate fail-closed on cross-round same-side duplicate claims with zero LLM calls.
9. 6-round valid fixture with genuine information gain passes pre-gate.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.utils.debate_utils import (
    DebateProtocolError,
    compute_claim_similarity,
    extract_new_evidence_count,
    update_debate_state_with_payload,
    validate_debate_preconditions,
    validate_debate_response,
)


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


def test_normalized_ngram_and_text_similarity():
    """Test normalized ngram similarity and threshold >= 0.82 for duplicate detection."""
    t1 = "8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高"
    t2 = "8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高"
    t3 = "8/21地量反弹是买盘枯竭，在50日均线压制下破位风险非常高"
    t3_reorder = "在50日均线压制下破位风险极高，8/21地量反弹系买盘枯竭"
    t4 = "铜价上涨侵蚀毛利且海外需求承压，极端情景下杀至4.86元"
    t5 = "铜价上涨侵蚀毛利率且海外需求承压，极端情景下可能下杀至4.86元"
    t6 = "Q3毛利率大幅提升至32.5%，海外出货量达120万台"

    # Exact match must be 1.0
    assert compute_claim_similarity(t1, t2) >= 0.99
    # Paraphrase / synonymous light edit must be >= 0.82
    assert compute_claim_similarity(t1, t3) >= 0.82
    assert compute_claim_similarity(t1, t3_reorder) >= 0.82
    assert compute_claim_similarity(t4, t5) >= 0.82

    # Distinct claims must be < 0.82
    assert compute_claim_similarity(t1, t4) < 0.82
    assert compute_claim_similarity(t1, t6) < 0.82
    assert compute_claim_similarity(t4, t6) < 0.82

    # English tests
    en1 = "Revenue grew 30% YoY with strong operating leverage"
    en2 = "Revenue grew 30% YoY with solid operating leverage"
    en3 = "Severe downside risk due to escalating raw material costs"
    assert compute_claim_similarity(en1, en2) >= 0.82
    assert compute_claim_similarity(en1, en3) < 0.82


def test_duplicate_claim_exact_fails_validation():
    """Round 2 duplicate claim with identical wording to Round 1 same-side claim fails validation."""
    state = {
        "count": 2,
        "claims": [
            {
                "claim_id": "INV-1",
                "speaker": "Bull Analyst",
                "speaker_key": "Bull",
                "stance": "bullish",
                "claim": "8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高",
                "evidence": ["8/21缩量成交仅1200万股"],
                "confidence": 0.85,
                "status": "open",
            },
            {
                "claim_id": "INV-2",
                "speaker": "Bear Analyst",
                "speaker_key": "Bear",
                "stance": "bearish",
                "claim": "铜价上涨侵蚀毛利且海外需求承压",
                "evidence": ["铜价创季度新高"],
                "confidence": 0.80,
                "status": "open",
            },
        ],
    }

    # Bull at message_index 3 repeats INV-1 verbatim
    response_exact_duplicate = (
        "多头论述：我们坚持前期判断。\n\n"
        "<!-- DEBATE_STATE: {\n"
        '  "responded_claim_ids": ["INV-2"],\n'
        '  "new_claims": [\n'
        "    {\n"
        '      "claim": "8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高",\n'
        '      "evidence": ["8/21缩量成交仅1200万股"],\n'
        '      "confidence": 0.85,\n'
        '      "target_claim_ids": ["INV-2"]\n'
        "    }\n"
        "  ],\n"
        '  "round_summary": "多头重复前期观点"\n'
        "} -->"
    )

    is_valid, parse_status, error_detail, payload = validate_debate_response(
        state=state,
        raw_response=response_exact_duplicate,
        speaker_key="Bull",
        stance="bullish",
        marker="DEBATE_STATE",
        domain="investment",
    )

    assert not is_valid
    assert parse_status == "invalid_protocol"
    assert "重复" in error_detail or "信息增量" in error_detail or "duplicate" in error_detail.lower()


def test_synonym_claim_same_evidence_fails_validation():
    """Round 2 claim with synonymous rewording and same evidence fails validation."""
    state = {
        "count": 3,
        "claims": [
            {
                "claim_id": "INV-1",
                "speaker": "Bull Analyst",
                "speaker_key": "Bull",
                "stance": "bullish",
                "claim": "在手订单高增50%支撑业绩",
                "evidence": ["在手订单增长50%"],
                "confidence": 0.90,
                "status": "open",
            },
            {
                "claim_id": "INV-2",
                "speaker": "Bear Analyst",
                "speaker_key": "Bear",
                "stance": "bearish",
                "claim": "铜价上涨侵蚀毛利且海外需求承压，极端情景下杀至4.86元",
                "evidence": ["铜价创季度新高，极端情景4.86元"],
                "confidence": 0.82,
                "status": "open",
            },
            {
                "claim_id": "INV-3",
                "speaker": "Bull Analyst",
                "speaker_key": "Bull",
                "stance": "bullish",
                "claim": "新产能投产带动规模效应降低单位成本",
                "evidence": ["Q3新增两条产线已达产"],
                "confidence": 0.88,
                "status": "open",
            },
        ],
    }

    # Bear at message_index 4 paraphrases INV-2 with identical evidence
    response_paraphrase = (
        "空头论述：再次强调铜价成本压力。\n\n"
        "<!-- DEBATE_STATE: {\n"
        '  "responded_claim_ids": ["INV-3"],\n'
        '  "new_claims": [\n'
        "    {\n"
        '      "claim": "铜价上涨侵蚀毛利率且海外需求承压，极端情景下可能下杀至4.86元",\n'
        '      "evidence": ["铜价创季度新高，极端情景4.86元"],\n'
        '      "confidence": 0.82,\n'
        '      "target_claim_ids": ["INV-3"]\n'
        "    }\n"
        "  ],\n"
        '  "round_summary": "空头重申成本下杀"\n'
        "} -->"
    )

    is_valid, parse_status, error_detail, payload = validate_debate_response(
        state=state,
        raw_response=response_paraphrase,
        speaker_key="Bear",
        stance="bearish",
        marker="DEBATE_STATE",
        domain="investment",
    )

    assert not is_valid
    assert parse_status == "invalid_protocol"
    assert "重复" in error_detail or "信息增量" in error_detail or "duplicate" in error_detail.lower()


def test_new_claim_with_numerical_data_and_new_evidence_passes():
    """Round 2 claim introducing new causal chain and numerical evidence passes validation."""
    state = {
        "count": 2,
        "claims": [
            {
                "claim_id": "INV-1",
                "speaker": "Bull Analyst",
                "speaker_key": "Bull",
                "stance": "bullish",
                "claim": "8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高",
                "evidence": ["8/21缩量成交仅1200万股"],
                "confidence": 0.85,
                "status": "open",
            },
            {
                "claim_id": "INV-2",
                "speaker": "Bear Analyst",
                "speaker_key": "Bear",
                "stance": "bearish",
                "claim": "铜价上涨侵蚀毛利且海外需求承压",
                "evidence": ["铜价创季度新高"],
                "confidence": 0.80,
                "status": "open",
            },
        ],
    }

    # Bull at message_index 3 introduces genuine new claim with new numerical evidence and causal chain
    response_genuine_new = (
        "多头论述：Q3长协订单已锁定原材料成本，海外出货量大增35%。\n\n"
        "<!-- DEBATE_STATE: {\n"
        '  "responded_claim_ids": ["INV-2"],\n'
        '  "new_claims": [\n'
        "    {\n"
        '      "claim": "Q3长协锁价比例达80%有效抵御铜价上涨，海外高端出货增长35%带动综合毛利率回升至31.2%",\n'
        '      "evidence": ["80%原材料签署年度固定价长协", "海外出货量达150万台同比增长35%"],\n'
        '      "confidence": 0.89,\n'
        '      "target_claim_ids": ["INV-2"]\n'
        "    }\n"
        "  ],\n"
        '  "round_summary": "多头提出长协锁价与出货量双重利好"\n'
        "} -->"
    )

    is_valid, parse_status, error_detail, payload = validate_debate_response(
        state=state,
        raw_response=response_genuine_new,
        speaker_key="Bull",
        stance="bullish",
        marker="DEBATE_STATE",
        domain="investment",
    )

    assert is_valid
    assert parse_status == "valid"
    assert error_detail == ""
    assert payload is not None


def test_round_messages_and_attempts_record_information_gain_fields():
    """update_debate_state_with_payload records information_gain_score, duplicate_claim_ids, new_evidence_count."""
    state = {
        "count": 2,
        "claims": [
            {
                "claim_id": "INV-1",
                "speaker": "Bull Analyst",
                "speaker_key": "Bull",
                "stance": "bullish",
                "claim": "营收同比增长30%",
                "evidence": ["财报营收增长30%"],
                "confidence": 0.85,
                "status": "open",
            },
            {
                "claim_id": "INV-2",
                "speaker": "Bear Analyst",
                "speaker_key": "Bear",
                "stance": "bearish",
                "claim": "应收账款周转天数拉长至95天",
                "evidence": ["应收账款95天"],
                "confidence": 0.80,
                "status": "open",
            },
        ],
        "round_messages": [],
        "attempts": [],
    }

    raw_response = (
        "多头论述：经营性现金流大幅改善120%。\n\n"
        "<!-- DEBATE_STATE: {\n"
        '  "responded_claim_ids": ["INV-2"],\n'
        '  "new_claims": [\n'
        "    {\n"
        '      "claim": "经营现金流净额达6.8亿元同比增长120%，账期结构显著优化",\n'
        '      "evidence": ["经营现金流净额6.8亿元", "前五大客户回款率98%"],\n'
        '      "confidence": 0.90,\n'
        '      "target_claim_ids": ["INV-2"]\n'
        "    }\n"
        "  ],\n"
        '  "round_summary": "多头现金流增量事实"\n'
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

    round_msgs = new_state.get("round_messages", [])
    assert len(round_msgs) == 1
    last_msg = round_msgs[0]
    assert "information_gain_score" in last_msg
    assert isinstance(last_msg["information_gain_score"], (int, float))
    assert last_msg["information_gain_score"] > 0
    assert "new_evidence_count" in last_msg
    assert last_msg["new_evidence_count"] >= 1
    assert "duplicate_claim_ids" in last_msg or "duplicate_claims" in last_msg


def test_researcher_retry_on_duplicate_claim():
    """Bull researcher retries on first duplicate claim and succeeds on second attempt."""
    state = _make_base_state()
    state["investment_debate_state"]["count"] = 2
    state["investment_debate_state"]["claims"] = [
        {
            "claim_id": "INV-1",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高",
            "evidence": ["8/21缩量成交仅1200万股"],
            "confidence": 0.85,
            "status": "open",
        },
        {
            "claim_id": "INV-2",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "铜价上涨侵蚀毛利且海外需求承压",
            "evidence": ["铜价创季度新高"],
            "confidence": 0.80,
            "status": "open",
        },
    ]

    bad_resp = (
        "多头论述：我们再次重申均线破位风险。\n\n"
        "<!-- DEBATE_STATE: {\n"
        '  "responded_claim_ids": ["INV-2"],\n'
        '  "new_claims": [\n'
        "    {\n"
        '      "claim": "8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高",\n'
        '      "evidence": ["8/21缩量成交仅1200万股"],\n'
        '      "confidence": 0.85,\n'
        '      "target_claim_ids": ["INV-2"]\n'
        "    }\n"
        "  ],\n"
        '  "round_summary": "多头重复观点"\n'
        "} -->"
    )

    good_resp = (
        "多头论述：Q3长协锁价覆盖80%原材料需求，海外高端出货增长35%。\n\n"
        "<!-- DEBATE_STATE: {\n"
        '  "responded_claim_ids": ["INV-2"],\n'
        '  "new_claims": [\n'
        "    {\n"
        '      "claim": "长协锁价覆盖80%原材料需求，海外高端出货增长35%抵御成本上涨",\n'
        '      "evidence": ["长协锁价80%", "海外出货增长35%"],\n'
        '      "confidence": 0.90,\n'
        '      "target_claim_ids": ["INV-2"]\n'
        "    }\n"
        "  ],\n"
        '  "round_summary": "多头提出锁价与出货增量事实"\n'
        "} -->"
    )

    call_count = 0

    def fake_astream(prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _fake_stream(bad_resp)
        return _fake_stream(good_resp)

    mock_llm = MagicMock()
    mock_llm.astream = fake_astream
    mock_memory = MagicMock()
    mock_memory.get_memories.return_value = []

    bull_node = create_bull_researcher(mock_llm, mock_memory)
    res = asyncio.run(bull_node(state))

    assert "investment_debate_state" in res
    deb_state = res["investment_debate_state"]
    # Count should increment from 2 to 3 (only +1)
    assert deb_state["count"] == 3
    # 1 accepted message in round_messages
    accepted = [m for m in deb_state.get("round_messages", []) if m.get("accepted", True)]
    assert len(accepted) == 1
    # 2 attempts tracked in attempts trace
    attempts = deb_state.get("attempts", [])
    assert len(attempts) == 2
    assert attempts[0]["accepted"] is False
    assert attempts[1]["accepted"] is True


def test_consecutive_duplicate_claims_raise_debate_protocol_error():
    """Consecutive 2 duplicate attempts raise DebateProtocolError and block."""
    state = _make_base_state()
    state["investment_debate_state"]["count"] = 2
    state["investment_debate_state"]["claims"] = [
        {
            "claim_id": "INV-1",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高",
            "evidence": ["8/21缩量成交仅1200万股"],
            "confidence": 0.85,
            "status": "open",
        },
        {
            "claim_id": "INV-2",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "铜价上涨侵蚀毛利且海外需求承压",
            "evidence": ["铜价创季度新高"],
            "confidence": 0.80,
            "status": "open",
        },
    ]

    duplicate_resp = (
        "多头论述：重复前期论据。\n\n"
        "<!-- DEBATE_STATE: {\n"
        '  "responded_claim_ids": ["INV-2"],\n'
        '  "new_claims": [\n'
        "    {\n"
        '      "claim": "8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高",\n'
        '      "evidence": ["8/21缩量成交仅1200万股"],\n'
        '      "confidence": 0.85,\n'
        '      "target_claim_ids": ["INV-2"]\n'
        "    }\n"
        "  ],\n"
        '  "round_summary": "多头重复观点"\n'
        "} -->"
    )

    mock_llm = MagicMock()
    mock_llm.astream = lambda prompt: _fake_stream(duplicate_resp)
    mock_memory = MagicMock()
    mock_memory.get_memories.return_value = []

    bull_node = create_bull_researcher(mock_llm, mock_memory)

    with pytest.raises(DebateProtocolError) as exc_info:
        asyncio.run(bull_node(state))

    assert "Debate protocol validation failed" in str(exc_info.value)
    assert exc_info.value.speaker == "Bull Analyst"
    assert exc_info.value.message_index == 3


def test_research_manager_pre_gate_blocks_on_duplicate_accepted_messages_with_zero_llm_calls():
    """Research manager pre-gate blocks immediately when duplicate accepted messages exist, LLM calls = 0."""
    state = _make_base_state()
    # 6 messages accepted, but Bear messages in Round 2 and Round 3 have duplicate claims (sim = 1.0)
    round_messages = [
        {"message_index": 1, "debate_round": 1, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": [], "target_claim_ids": [], "new_claim_ids": ["INV-1"], "information_gain_score": 1.0},
        {"message_index": 2, "debate_round": 1, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-1"], "target_claim_ids": ["INV-1"], "new_claim_ids": ["INV-2"], "information_gain_score": 1.0},
        {"message_index": 3, "debate_round": 2, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-2"], "target_claim_ids": ["INV-2"], "new_claim_ids": ["INV-3"], "information_gain_score": 0.85},
        {"message_index": 4, "debate_round": 2, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-3"], "target_claim_ids": ["INV-3"], "new_claim_ids": ["INV-4"], "information_gain_score": 0.82},
        {"message_index": 5, "debate_round": 3, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-4"], "target_claim_ids": ["INV-4"], "new_claim_ids": ["INV-5"], "information_gain_score": 0.90},
        # Message 6 duplicates Message 4's claim!
        {"message_index": 6, "debate_round": 3, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-5"], "target_claim_ids": ["INV-5"], "new_claim_ids": ["INV-6"], "information_gain_score": 0.0},
    ]
    claims = [
        {"claim_id": "INV-1", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "营收高增30%", "evidence": ["营收同比增长30%"], "confidence": 0.85},
        {"claim_id": "INV-2", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "估值透支严重", "evidence": ["PE处于高位"], "confidence": 0.80},
        {"claim_id": "INV-3", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "订单放量50%", "evidence": ["在手订单增长50%"], "confidence": 0.90},
        # INV-4 and INV-6 are exact duplicates!
        {"claim_id": "INV-4", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "铜价上涨侵蚀毛利且海外需求承压，极端情景下杀至4.86元", "evidence": ["上游成本上升", "极端情景4.86元"], "confidence": 0.75},
        {"claim_id": "INV-5", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "长协锁价80%", "evidence": ["主力净流入5.2亿元"], "confidence": 0.88},
        {"claim_id": "INV-6", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "铜价上涨侵蚀毛利且海外需求承压，极端情景下杀至4.86元", "evidence": ["上游成本上升", "极端情景4.86元"], "confidence": 0.78},
    ]
    state["investment_debate_state"]["count"] = 6
    state["investment_debate_state"]["current_speaker"] = "Bear"
    state["investment_debate_state"]["round_messages"] = round_messages
    state["investment_debate_state"]["claims"] = claims

    llm_call_count = 0

    def fake_astream(prompt):
        nonlocal llm_call_count
        llm_call_count += 1
        return _fake_stream("Manager verdict output")

    mock_llm = MagicMock()
    mock_llm.astream = fake_astream
    mock_memory = MagicMock()
    mock_memory.get_memories.return_value = []

    manager_node = create_research_manager(mock_llm, mock_memory)
    result = asyncio.run(manager_node(state))

    # 1. LLM must NOT be called (LLM call count == 0)
    assert llm_call_count == 0

    # 2. Gate failed and blocked plan returned
    assert "manager_verdict" in result
    assert result["manager_verdict"]["consistency_check_passed"] is False
    assert result["manager_verdict"]["direction"] in ("中性", "N/A", "NA")
    assert any("信息增量" in err or "重复" in err or "相似度" in err for err in result["manager_verdict"]["failed_checks"])
    assert "硬闸未通过" in result["investment_plan"]


def test_valid_six_round_fixture_with_information_gain_passes_pre_gate():
    """6-round valid fixture with genuine information gain passes pre-gate."""
    state = _make_base_state()
    round_messages = [
        {"message_index": 1, "debate_round": 1, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": [], "target_claim_ids": [], "new_claim_ids": ["INV-1"], "information_gain_score": 1.0, "new_evidence_count": 1},
        {"message_index": 2, "debate_round": 1, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-1"], "target_claim_ids": ["INV-1"], "new_claim_ids": ["INV-2"], "information_gain_score": 1.0, "new_evidence_count": 1},
        {"message_index": 3, "debate_round": 2, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-2"], "target_claim_ids": ["INV-2"], "new_claim_ids": ["INV-3"], "information_gain_score": 0.85, "new_evidence_count": 1},
        {"message_index": 4, "debate_round": 2, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-3"], "target_claim_ids": ["INV-3"], "new_claim_ids": ["INV-4"], "information_gain_score": 0.82, "new_evidence_count": 1},
        {"message_index": 5, "debate_round": 3, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-4"], "target_claim_ids": ["INV-4"], "new_claim_ids": ["INV-5"], "information_gain_score": 0.90, "new_evidence_count": 1},
        {"message_index": 6, "debate_round": 3, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-5"], "target_claim_ids": ["INV-5"], "new_claim_ids": ["INV-6"], "information_gain_score": 0.88, "new_evidence_count": 1},
    ]
    claims = [
        {"claim_id": "INV-1", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "营收同比增长30%", "evidence": ["营收同比增长30%"], "confidence": 0.85},
        {"claim_id": "INV-2", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "动态PE达到68倍处于历史90分位估值偏高", "evidence": ["动态PE 68倍"], "confidence": 0.80},
        {"claim_id": "INV-3", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "在手订单高增50%且产能利用率达98%", "evidence": ["在手订单增长50%", "产能利用率98%"], "confidence": 0.90},
        {"claim_id": "INV-4", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "上游铜价大涨18%侵蚀毛利率3.5个百分点", "evidence": ["铜价上涨18%"], "confidence": 0.75},
        {"claim_id": "INV-5", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "签署80%原材料年度锁价长协并获主力净流入5.2亿元", "evidence": ["长协锁价80%", "主力净流入5.2亿元"], "confidence": 0.88},
        {"claim_id": "INV-6", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "海外关税壁垒提升且同业扩产引发降价促销风险", "evidence": ["海外关税政策调整", "同业新增产能投放"], "confidence": 0.78},
    ]
    state["investment_debate_state"]["count"] = 6
    state["investment_debate_state"]["round_messages"] = round_messages
    state["investment_debate_state"]["claims"] = claims

    errors = validate_debate_preconditions(state["investment_debate_state"], claims=claims)
    assert errors == []


def _v2_four_round_skipped_tiebreak_state():
    from tradingagents.agents.utils.agent_states import PROTOCOL_VERSION_V2_STRUCTURED

    claims = [
        {"claim_id": "INV-1", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "主力资金连续净流入1.2亿", "evidence": ["主力净流入1.2亿"], "confidence": 0.85, "battlefield": "capital_flow"},
        {"claim_id": "INV-2", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "行业景气政策密集落地", "evidence": ["产业政策周内3项"], "confidence": 0.80, "battlefield": "sentiment_theme"},
        {"claim_id": "INV-3", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "放量突破60日线", "evidence": ["突破60日线放量12%"], "confidence": 0.78, "battlefield": "price_volume"},
        {"claim_id": "INV-4", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "经营现金流同比下滑30%", "evidence": ["经营现金流-30%"], "confidence": 0.82, "battlefield": "fundamentals"},
        {"claim_id": "INV-5", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "出口交货值同比下降", "evidence": ["出口交货值回落"], "confidence": 0.75, "battlefield": "macro_policy"},
        {"claim_id": "INV-6", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "高位换手率超过25%", "evidence": ["换手率25%"], "confidence": 0.70, "battlefield": "capital_flow"},
    ]
    round_messages = [
        {"message_index": 1, "stage": "opening", "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": [], "target_claim_ids": [], "new_claim_ids": ["INV-1", "INV-2", "INV-3"], "information_gain_score": 1.0},
        {"message_index": 2, "stage": "opening", "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": [], "target_claim_ids": [], "new_claim_ids": ["INV-4", "INV-5", "INV-6"], "information_gain_score": 1.0},
        {"message_index": 3, "stage": "challenge", "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-4"], "target_claim_ids": ["INV-4"], "new_claim_ids": [], "challenges": [{"target_claim_id": "INV-4", "weakest_point": "忽略预收款", "evidence": ["预收款+45%"], "severity": "major"}], "information_gain_score": 0.9},
        {"message_index": 4, "stage": "challenge", "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-1"], "target_claim_ids": ["INV-1"], "new_claim_ids": [], "challenges": [{"target_claim_id": "INV-1", "weakest_point": "净流入不可持续", "evidence": ["近5日转净流出"], "severity": "major"}], "information_gain_score": 0.88},
    ]
    return {
        "count": 4,
        "round_messages": round_messages,
        "claims": claims,
        "challenges": [
            {"speaker_key": "Bull", "target_claim_id": "INV-4", "message_index": 3},
            {"speaker_key": "Bear", "target_claim_id": "INV-1", "message_index": 4},
        ],
        "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
        "feature_flags": {"v2_debate_enabled": True},
        "tiebreak_skipped": True,
    }


def test_v2_four_round_skipped_tiebreak_passes_manager_pre_gate():
    """v2 Opening+Challenge with tiebreak skipped is 4 messages, not legacy 6."""
    debate_state = _v2_four_round_skipped_tiebreak_state()
    errors = validate_debate_preconditions(debate_state, claims=debate_state["claims"])
    assert errors == []


def test_v2_four_round_without_challenges_still_fail_closed():
    debate_state = _v2_four_round_skipped_tiebreak_state()
    debate_state["challenges"] = []
    debate_state["round_messages"][2]["challenges"] = []
    debate_state["round_messages"][3]["challenges"] = []
    debate_state["round_messages"][2]["responded_claim_ids"] = []
    debate_state["round_messages"][3]["responded_claim_ids"] = []
    debate_state["round_messages"][2]["target_claim_ids"] = []
    debate_state["round_messages"][3]["target_claim_ids"] = []
    errors = validate_debate_preconditions(debate_state, claims=debate_state["claims"])
    assert errors
    assert any("challenge" in err.lower() or "盘问" in err for err in errors)


def test_legacy_four_round_still_fails_six_round_pre_gate():
    debate_state = _v2_four_round_skipped_tiebreak_state()
    debate_state["protocol_version"] = "v1_legacy"
    debate_state["feature_flags"] = {"v2_debate_enabled": False}
    debate_state["v2_debate_enabled"] = False
    errors = validate_debate_preconditions(debate_state, claims=debate_state["claims"])
    assert errors
    assert any("6" in err for err in errors)


def test_research_manager_success_path_preserves_v2_protocol_metadata():
    """Success-path manager return must keep v2 protocol fields for persistence.

    Live 000858/000063/000651 reports dropped protocol_version/feature_flags/
    challenges on the manager rewrite, so get_protocol_metadata defaulted to v1.
    """
    from tradingagents.agents.utils.agent_states import (
        PROTOCOL_VERSION_V2_STRUCTURED,
        get_protocol_metadata,
    )

    state = _make_base_state()
    debate_state = _v2_four_round_skipped_tiebreak_state()
    debate_state["history"] = "辩论历史"
    debate_state["bull_history"] = "多头历史"
    debate_state["bear_history"] = "空头历史"
    debate_state["current_speaker"] = "Bear"
    debate_state["unresolved_claim_ids"] = ["INV-1", "INV-4"]
    debate_state["round_summary"] = "Opening 与 Challenge 已完成"
    state["investment_debate_state"] = debate_state

    verdict_body = (
        "研究总监裁决正文\n"
        "<!-- MANAGER_VERDICT: {"
        '"winner": "bear", "direction": "偏空", "reason": "现金流与估值证据更硬",'
        '"position_pct": 0, "entry": null, "target": "31.5", "stop_loss": "32.8",'
        '"upside": 5.0, "downside": 12.0, "odds": 0.4,'
        '"adopted_claim_ids": ["INV-4", "INV-5", "INV-6"],'
        '"partially_adopted_claims": [],'
        '"rejected_claim_ids": ["INV-1", "INV-2", "INV-3"],'
        '"excluded_evidence": [],'
        '"dispute_map": [{'
        '"data_point": "经营现金流同比下滑30%",'
        '"bull_interpretation": "短期波动",'
        '"bear_interpretation": "盈利质量恶化",'
        '"evidence_decision": "财报现金流更可信",'
        '"winner": "bear"}]'
        "} -->"
    )

    mock_llm = MagicMock()
    mock_llm.astream = lambda prompt: _fake_stream(verdict_body)
    mock_memory = MagicMock()
    mock_memory.get_memories.return_value = []

    manager_node = create_research_manager(mock_llm, mock_memory)
    result = asyncio.run(manager_node(state))

    inv = result["investment_debate_state"]
    assert inv.get("protocol_version") == PROTOCOL_VERSION_V2_STRUCTURED
    assert inv.get("feature_flags", {}).get("v2_debate_enabled") is True
    assert inv.get("tiebreak_skipped") is True
    assert inv.get("challenges")
    assert inv.get("count") == 4
    assert len(inv.get("round_messages") or []) == 4

    meta = get_protocol_metadata({"investment_debate_state": inv})
    assert meta["protocol_version"] == PROTOCOL_VERSION_V2_STRUCTURED
    assert meta["feature_flags"]["v2_debate_enabled"] is True
    assert meta["tiebreak_skipped"] is True
