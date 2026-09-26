"""DAV-1315: rejected debate attempts must not leak machine-block markup into
persisted debate state.

Production failure pattern (gpt-6-luna): a researcher attempt emits a duplicated
DEBATE_STATE machine block; the per-attempt protocol check rejects it and the
node retries successfully. The rejected attempt's verbatim ``raw_response`` was
persisted into ``investment_debate_state.attempts`` /
``round_messages[].attempts`` inside ``result_data`` — and
``report_service.validate_report_machine_blocks`` hard-fails on any string with
>1 same-tag opening, silently dropping a fully computed report.

These fixtures reconstruct that pattern: attempt 1 duplicated/malformed,
attempt 2 valid. The resulting debate state must (a) record the rejected attempt
per protocol and (b) survive ``validate_report_machine_blocks``.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

from api.services import report_service
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.utils.debate_utils import update_debate_state_with_payload


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


_EXISTING_CLAIMS = [
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

_VALID_PAYLOAD = (
    '{\n'
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
    "}"
)

# Luna failure pattern: the model repeats the machine block twice in one reply.
_DUPLICATED_BLOCK_RESP = (
    "多头论述：重申锁价与出货逻辑。\n\n"
    f"<!-- DEBATE_STATE: {_VALID_PAYLOAD} -->\n\n"
    "补充：以下为重述机读块。\n\n"
    f"<!-- DEBATE_STATE: {_VALID_PAYLOAD} -->"
)

_GOOD_RESP = (
    "多头论述：Q3长协锁价覆盖80%原材料需求，海外高端出货增长35%。\n\n"
    f"<!-- DEBATE_STATE: {_VALID_PAYLOAD} -->"
)


def _debate_opening_count(text: str) -> int:
    return len(re.findall(r"<!--\s*DEBATE_STATE", text or ""))


def test_rejected_attempt_raw_is_quarantined_in_persisted_state():
    """Attempt 1 emits a duplicated DEBATE_STATE block (rejected), attempt 2
    succeeds. The persisted debate state must pass
    ``validate_report_machine_blocks`` and still record the rejected attempt."""
    state = _make_base_state()
    state["investment_debate_state"]["count"] = 2
    state["investment_debate_state"]["claims"] = [dict(c) for c in _EXISTING_CLAIMS]

    call_count = 0

    def fake_astream(prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _fake_stream(_DUPLICATED_BLOCK_RESP)
        return _fake_stream(_GOOD_RESP)

    mock_llm = MagicMock()
    mock_llm.astream = fake_astream
    mock_memory = MagicMock()
    mock_memory.get_memories.return_value = []

    bull_node = create_bull_researcher(mock_llm, mock_memory)
    res = asyncio.run(bull_node(state))

    deb_state = res["investment_debate_state"]
    attempts = deb_state.get("attempts", [])
    assert len(attempts) == 2
    rejected, accepted = attempts[0], attempts[1]

    # Protocol still records the rejected attempt (round invalid + retried).
    assert rejected["accepted"] is False
    assert rejected["parse_status"] == "invalid"
    assert accepted["accepted"] is True

    # The persisted rejected text carries no machine-block markup.
    assert _debate_opening_count(rejected.get("raw_response", "")) == 0
    assert rejected.get("raw_response_quarantined") is True
    assert "多头论述" in rejected["raw_response"]

    # Accepted attempt retains its single verbatim machine block.
    assert _debate_opening_count(accepted.get("raw_response", "")) == 1

    # The whole state is persistable: no string trips the duplicate-block gate.
    report_service.validate_report_machine_blocks(
        {"investment_debate_state": deb_state}
    )

    # round_messages mirrors of attempts are sanitized the same way.
    for msg in deb_state.get("round_messages", []):
        for att in msg.get("attempts", []) or []:
            if att.get("accepted"):
                assert _debate_opening_count(att.get("raw_response", "")) <= 1
            else:
                assert _debate_opening_count(att.get("raw_response", "")) == 0


def test_rejected_attempt_malformed_json_block_is_quarantined():
    """Second production pattern (report 5d770c47): a rejected attempt whose
    block contains invalid JSON must also not reach persisted state — the
    persistence validator would raise 'contains invalid JSON'."""
    malformed_resp = (
        "多头论述：锁价逻辑。\n\n"
        '<!-- DEBATE_STATE: {"new_claims": [invalid json} -->'
    )
    state = _make_base_state()
    state["investment_debate_state"]["count"] = 2
    state["investment_debate_state"]["claims"] = [dict(c) for c in _EXISTING_CLAIMS]

    call_count = 0

    def fake_astream(prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _fake_stream(malformed_resp)
        return _fake_stream(_GOOD_RESP)

    mock_llm = MagicMock()
    mock_llm.astream = fake_astream
    mock_memory = MagicMock()
    mock_memory.get_memories.return_value = []

    bull_node = create_bull_researcher(mock_llm, mock_memory)
    res = asyncio.run(bull_node(state))

    deb_state = res["investment_debate_state"]
    report_service.validate_report_machine_blocks(
        {"investment_debate_state": deb_state}
    )
    rejected = deb_state["attempts"][0]
    assert rejected["accepted"] is False
    assert _debate_opening_count(rejected.get("raw_response", "")) == 0


def test_update_debate_state_with_payload_sanitizes_rejected_attempts():
    """Direct state-update path: a caller-supplied attempts trace containing a
    rejected raw_response with duplicated blocks is sanitized before merge."""
    state = _make_base_state()["investment_debate_state"]
    state["count"] = 2
    state["claims"] = [dict(c) for c in _EXISTING_CLAIMS]

    attempts_trace = [
        {
            "attempt_index": 1,
            "message_index": 3,
            "debate_round": 2,
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "parse_status": "invalid",
            "error_detail": "DEBATE_STATE machine block duplicated",
            "raw_response": _DUPLICATED_BLOCK_RESP,
            "accepted": False,
        },
        {
            "attempt_index": 2,
            "message_index": 3,
            "debate_round": 2,
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "parse_status": "valid",
            "error_detail": "",
            "raw_response": _GOOD_RESP,
            "accepted": True,
        },
    ]

    new_state = update_debate_state_with_payload(
        state=state,
        raw_response=_GOOD_RESP,
        speaker_label="Bull Analyst",
        speaker_key="Bull",
        stance="bullish",
        history_key="bull_history",
        marker="DEBATE_STATE",
        claim_prefix="INV",
        domain="investment",
        speaker_field="current_speaker",
        attempts=attempts_trace,
    )

    report_service.validate_report_machine_blocks(
        {"investment_debate_state": new_state}
    )
    stored = new_state["attempts"]
    assert stored[0]["accepted"] is False
    assert _debate_opening_count(stored[0]["raw_response"]) == 0
    assert stored[0]["raw_response_quarantined"] is True
    assert _debate_opening_count(stored[1]["raw_response"]) == 1
