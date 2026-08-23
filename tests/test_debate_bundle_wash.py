"""Unit and E2E regression tests for DAV-346:
P0 patch: Abolish bundled wash in debate information gain gate (per-claim duplicate rejection).

Requirements:
1. When a turn outputs multiple new_claims where some are duplicates (e.g., repeating historical same-side claims/evidence)
   and at least one is valid:
   - The message is accepted (parse_status='valid', accepted=True).
   - Duplicate claims are REJECTED per-claim and MUST NOT enter state['claims'] or open_claim_ids.
   - Duplicate claim IDs (e.g., 'INV-9') are recorded in duplicate_claim_ids of round_messages.
   - Valid claims (e.g., 'INV-10') are admitted into state['claims'] and open_claim_ids.
   - round_messages['new_claim_ids'] contains only the admitted valid claim IDs.
2. When ALL new_claims in a turn are duplicates:
   - The entire message is rejected (parse_status='invalid_protocol', accepted=False).
3. 000333 golden replay scenario:
   - Bull round 5 message with INV-9 (clone of INV-1/INV-5 evidence) + INV-10 (valid new claim).
   - INV-9 is stripped from claims; INV-10 is stored.
4. Downstream Research Manager validation passes cleanly with no duplicate claims in claims ledger.
"""
from __future__ import annotations

import json
import pytest

from tradingagents.agents.utils.debate_utils import (
    compute_claim_similarity,
    extract_new_evidence_count,
    update_debate_state_with_payload,
    validate_debate_preconditions,
    validate_debate_response,
)


def _make_historical_debate_state_round5():
    """Create debate state corresponding to 000333 round 5 precondition (8 historical claims)."""
    claims = [
        {
            "claim_id": "INV-1",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "地量供给测试确立双底，主力大单逆势吸筹酝酿短线突破",
            "evidence": [
                "08-21日成交量萎缩至1802.1万股创地量且量比0.6，82.39元双底确立",
                "主力大单逆势净流入1.1134亿元，中单流出1.6449亿元，筹码加速收敛",
            ],
            "confidence": 0.85,
            "status": "open",
            "round_index": 1,
            "target_claim_ids": [],
        },
        {
            "claim_id": "INV-2",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "70亿回购构筑强安全底座，以旧换新与出海提供不对称赔率",
            "evidence": [
                "截至7月底累计回购69.73亿元(均价79.79元，上限87.71元)，Q1自由现金流达123.86亿元",
                "2025年海外营收1959.48亿元占比42.7%，以旧换新绿色补贴15-20%拉动高能效ASP",
            ],
            "confidence": 0.88,
            "status": "open",
            "round_index": 1,
            "target_claim_ids": [],
        },
        {
            "claim_id": "INV-3",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "86元阻力压制且地量显现买力枯竭",
            "evidence": [
                "08-21冲高85.99元大幅回落收带长上影中阴线",
                "全单净流出1.71亿元且融券卖出超5000万，量比仅0.6显示买盘枯竭",
            ],
            "confidence": 0.80,
            "status": "open",
            "round_index": 2,
            "target_claim_ids": ["INV-1"],
        },
        {
            "claim_id": "INV-4",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "回购动能耗尽且高铜价侵蚀Q3毛利",
            "evidence": [
                "69.73亿回购均价79.79元低于现价5.6%已充分定价",
                "LME铜价达14181美元高位压制毛利0.3-0.5pct，大金单月跌15.58%映射外需分化",
            ],
            "confidence": 0.82,
            "status": "open",
            "round_index": 2,
            "target_claim_ids": ["INV-2"],
        },
        {
            "claim_id": "INV-5",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "大单逆势吸筹确立地量洗盘尾声，突破中轨在即",
            "evidence": [
                "主力大单08-21净流入1.1134亿元，成交量1802万股创地量量比0.6",
                "82.39元双底确立，紧贴20日VWMA84.50元筹码收敛",
            ],
            "confidence": 0.86,
            "status": "open",
            "round_index": 3,
            "target_claim_ids": ["INV-3"],
        },
        {
            "claim_id": "INV-6",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "垂直整合与高端化对冲成本，全球布局构筑不对称底座",
            "evidence": [
                "以旧换新补贴15-20%拉升ASP覆盖铜价扰动，单季FCF达123.86亿元",
                "惠而浦单月涨8.64%验证外需补库，极端情景净利底线310-330亿元",
            ],
            "confidence": 0.87,
            "status": "open",
            "round_index": 3,
            "target_claim_ids": ["INV-4"],
        },
        {
            "claim_id": "INV-7",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "中轨长上影假突破显现买力衰竭",
            "evidence": [
                "08-21冲高85.99元遇阻收于日内低点84.30元(位置0.03)",
                "全单流出1.71亿元且成交仅1802万股量比0.6证实无需求上攻",
            ],
            "confidence": 0.81,
            "status": "open",
            "round_index": 4,
            "target_claim_ids": ["INV-5"],
        },
        {
            "claim_id": "INV-8",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "应收攀升叠加高铜价预警毛利挤压",
            "evidence": [
                "2026Q1应收账款达525.37亿元同比增7.95%远超营收增速2.55%",
                "LME铜价达14181美元高位压制毛利0.3-0.5pct且大金单月暴跌15.58%",
            ],
            "confidence": 0.83,
            "status": "open",
            "round_index": 4,
            "target_claim_ids": ["INV-6"],
        },
    ]

    round_messages = [
        {"message_index": 1, "debate_round": 1, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": [], "target_claim_ids": [], "new_claim_ids": ["INV-1", "INV-2"], "duplicate_claim_ids": []},
        {"message_index": 2, "debate_round": 1, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-1", "INV-2"], "target_claim_ids": ["INV-1", "INV-2"], "new_claim_ids": ["INV-3", "INV-4"], "duplicate_claim_ids": []},
        {"message_index": 3, "debate_round": 2, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-3", "INV-4"], "target_claim_ids": ["INV-3", "INV-4"], "new_claim_ids": ["INV-5", "INV-6"], "duplicate_claim_ids": []},
        {"message_index": 4, "debate_round": 2, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-5", "INV-6"], "target_claim_ids": ["INV-5", "INV-6"], "new_claim_ids": ["INV-7", "INV-8"], "duplicate_claim_ids": []},
    ]

    return {
        "count": 4,
        "current_speaker": "Bear",
        "claims": claims,
        "round_messages": round_messages,
        "open_claim_ids": [c["claim_id"] for c in claims],
        "resolved_claim_ids": [],
        "unresolved_claim_ids": [],
        "claim_counter": 8,
        "history": "",
        "bull_history": "",
        "bear_history": "",
    }


class TestBundleWashPerClaimRejection:
    def test_golden_000333_round5_bundle_wash_rejects_duplicate_claim(self):
        """000333 Round 5 replay:

        Candidate 1 (INV-9): '地量洗盘确立双底，大单吸筹蓄势突破中轨' with 100% cloned evidence from INV-1 -> duplicate.
        Candidate 2 (INV-10): '一体化与出海对冲成本，近70亿回购构筑铁底' with new factual evidence -> valid.

        Expected:
        - Message is accepted (since Candidate 2 is valid).
        - INV-9 is REJECTED and MUST NOT be in state['claims'] or open_claim_ids.
        - INV-10 is ADMITTED into state['claims'] and open_claim_ids.
        - duplicate_claim_ids in round_messages contains 'INV-9'.
        - new_claim_ids in round_messages contains ONLY 'INV-10'.
        """
        state = _make_historical_debate_state_round5()

        # Bull message 5 with 1 duplicate claim + 1 valid claim
        raw_response_round5 = (
            "多头论述：\n"
            "1. 地量供给测试确立底部结构，主力吸筹明确。\n"
            "2. 针对空头提出的铜价成本，Q1单季自由现金流123.86亿元与以旧换新补贴提供了极高安全垫。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-7", "INV-8"],\n'
            '  "new_claims": [\n'
            "    {\n"
            '      "claim": "地量洗盘确立双底，大单吸筹蓄势突破中轨",\n'
            '      "evidence": [\n'
            '        "08-21日成交量萎缩至1802.1万股创地量量比0.6，82.39元双底确立",\n'
            '        "主力大单逆势净流入1.1134亿元，中单流出1.6449亿元，筹码加速收敛"\n'
            "      ],\n"
            '      "confidence": 0.88,\n'
            '      "target_claim_ids": ["INV-7"]\n'
            "    },\n"
            "    {\n"
            '      "claim": "一体化与出海对冲成本，近70亿回购构筑铁底",\n'
            '      "evidence": [\n'
            '        "2026Q1单季自由现金流达123.86亿元，应收周转率2.82次营运健康",\n'
            '        "以旧换新补贴15-20%拉升ASP覆盖铜价扰动，惠而浦单月涨8.64%印证外需"\n'
            "      ],\n"
            '      "confidence": 0.85,\n'
            '      "target_claim_ids": ["INV-8"]\n'
            "    }\n"
            "  ],\n"
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-7", "INV-8"],\n'
            '  "next_focus_claim_ids": ["INV-8"],\n'
            '  "round_summary": "多头重申底座并引入Q1现金流与周转率新事实",\n'
            '  "round_goal": "击破空头成本与应收质疑"\n'
            "} -->"
        )

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=raw_response_round5,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert new_state["count"] == 5
        last_msg = new_state["round_messages"][-1]
        assert last_msg["accepted"] is True
        assert last_msg["parse_status"] == "valid"

        # Check claims: INV-9 must NOT be in claims!
        all_claim_ids = [c["claim_id"] for c in new_state["claims"]]
        assert "INV-9" not in all_claim_ids, f"INV-9 was admitted to claims! Found: {all_claim_ids}"
        assert "INV-10" in all_claim_ids, f"INV-10 was missing from claims! Found: {all_claim_ids}"

        # open_claim_ids check
        assert "INV-9" not in new_state["open_claim_ids"]
        assert "INV-10" in new_state["open_claim_ids"]

        # duplicate_claim_ids check
        assert "INV-9" in last_msg["duplicate_claim_ids"]
        assert "INV-10" not in last_msg["duplicate_claim_ids"]

        # new_claim_ids check: only the valid new claim ID is in new_claim_ids
        assert last_msg["new_claim_ids"] == ["INV-10"]

        # duplicate_claims text recorded
        assert any("地量洗盘" in text for text in last_msg["duplicate_claims"])

    def test_all_claims_duplicate_fails_validation_fail_closed(self):
        """Negative case: If all new_claims are duplicates, whole message is rejected."""
        state = _make_historical_debate_state_round5()

        # Both new claims are duplicates of historical Bull claims
        raw_response_all_dups = (
            "多头论述：我们继续坚持此前的两点论述。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-7", "INV-8"],\n'
            '  "new_claims": [\n'
            "    {\n"
            '      "claim": "地量洗盘确立双底，大单吸筹蓄势突破中轨",\n'
            '      "evidence": [\n'
            '        "08-21日成交量萎缩至1802.1万股创地量量比0.6，82.39元双底确立",\n'
            '        "主力大单逆势净流入1.1134亿元，中单流出1.6449亿元，筹码加速收敛"\n'
            "      ],\n"
            '      "confidence": 0.88,\n'
            '      "target_claim_ids": ["INV-7"]\n'
            "    },\n"
            "    {\n"
            '      "claim": "大单逆势吸筹确立地量洗盘尾声，突破中轨在即",\n'
            '      "evidence": [\n'
            '        "主力大单08-21净流入1.1134亿元，成交量1802万股创地量量比0.6",\n'
            '        "82.39元双底确立，紧贴20日VWMA84.50元筹码收敛"\n'
            "      ],\n"
            '      "confidence": 0.86,\n'
            '      "target_claim_ids": ["INV-8"]\n'
            "    }\n"
            "  ],\n"
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-7", "INV-8"],\n'
            '  "next_focus_claim_ids": ["INV-8"],\n'
            '  "round_summary": "多头全部复读",\n'
            '  "round_goal": "复读"\n'
            "} -->"
        )

        is_valid, parse_status, error_detail, payload = validate_debate_response(
            state=state,
            raw_response=raw_response_all_dups,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )

        assert not is_valid
        assert parse_status == "invalid_protocol"
        assert "信息增量" in error_detail or "重复" in error_detail

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=raw_response_all_dups,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        # Count does not increment on blocked/rejected turn
        assert new_state["count"] == 4
        assert len(new_state["claims"]) == 8
        assert new_state["round_messages"][-1]["accepted"] is False

    def test_multi_claim_bundle_with_two_duplicates_and_one_valid(self):
        """3 candidate claims: 2 duplicates + 1 valid -> 2 rejected with duplicate IDs, 1 admitted."""
        state = _make_historical_debate_state_round5()

        raw_response_3claims = (
            "多头论述：\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-7", "INV-8"],\n'
            '  "new_claims": [\n'
            "    {\n"
            '      "claim": "地量洗盘确立双底，大单吸筹蓄势突破中轨",\n'
            '      "evidence": ["08-21日成交量萎缩至1802.1万股创地量量比0.6，82.39元双底确立"],\n'
            '      "confidence": 0.88,\n'
            '      "target_claim_ids": ["INV-7"]\n'
            "    },\n"
            "    {\n"
            '      "claim": "大单逆势吸筹确立地量洗盘尾声，突破中轨在即",\n'
            '      "evidence": ["82.39元双底确立，紧贴20日VWMA84.50元筹码收敛"],\n'
            '      "confidence": 0.86,\n'
            '      "target_claim_ids": ["INV-7"]\n'
            "    },\n"
            "    {\n"
            '      "claim": "研发专利转化率提升至85%开辟新增长曲线",\n'
            '      "evidence": ["2026上半年新增授权发明专利1420件同比增28%"],\n'
            '      "confidence": 0.91,\n'
            '      "target_claim_ids": ["INV-8"]\n'
            "    }\n"
            "  ],\n"
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": ["INV-7", "INV-8"],\n'
            '  "next_focus_claim_ids": ["INV-8"],\n'
            '  "round_summary": "多头引入专利新事实",\n'
            '  "round_goal": "立新论点"\n'
            "} -->"
        )

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=raw_response_3claims,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert new_state["count"] == 5
        last_msg = new_state["round_messages"][-1]
        assert last_msg["accepted"] is True

        all_claim_ids = [c["claim_id"] for c in new_state["claims"]]
        # INV-9 and INV-10 are duplicates -> rejected
        assert "INV-9" not in all_claim_ids
        assert "INV-10" not in all_claim_ids
        # INV-11 is the valid one -> admitted
        assert "INV-11" in all_claim_ids

        assert set(last_msg["duplicate_claim_ids"]) == {"INV-9", "INV-10"}
        assert last_msg["new_claim_ids"] == ["INV-11"]

    def test_research_manager_pregate_clean_after_bundle_wash_repair(self):
        """Research Manager pre-gate must pass cleanly when bundle wash duplicate claims are excluded from claims."""
        state = _make_historical_debate_state_round5()

        # Step 1: Bull Round 5 with 1 dup (INV-9) and 1 valid (INV-10)
        raw_bull_5 = (
            "多头发言。\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-7", "INV-8"],\n'
            '  "new_claims": [\n'
            '    {"claim": "地量洗盘确立双底，大单吸筹蓄势突破中轨", "evidence": ["08-21日成交量萎缩至1802.1万股创地量量比0.6，82.39元双底确立"], "confidence": 0.88, "target_claim_ids": ["INV-7"]},\n'
            '    {"claim": "一体化与出海对冲成本，近70亿回购构筑铁底", "evidence": ["2026Q1单季自由现金流达123.86亿元"], "confidence": 0.85, "target_claim_ids": ["INV-8"]}\n'
            "  ],\n"
            '  "resolved_claim_ids": [], "unresolved_claim_ids": ["INV-7", "INV-8"], "next_focus_claim_ids": ["INV-8"], "round_summary": "s", "round_goal": "g"\n'
            "} -->"
        )
        state_after_bull5 = update_debate_state_with_payload(
            state=state,
            raw_response=raw_bull_5,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        # Step 2: Bear Round 6 with valid claims targeting INV-10
        raw_bear_6 = (
            "空头收尾发言。\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-10"],\n'
            '  "new_claims": [\n'
            '    {"claim": "欧洲反补贴调查升级将削弱海外利润率", "evidence": ["欧盟拟对家电反补贴征税8-12%"], "confidence": 0.84, "target_claim_ids": ["INV-10"]}\n'
            "  ],\n"
            '  "resolved_claim_ids": [], "unresolved_claim_ids": ["INV-10"], "next_focus_claim_ids": ["INV-10"], "round_summary": "s6", "round_goal": "g6"\n'
            "} -->"
        )
        final_state = update_debate_state_with_payload(
            state=state_after_bull5,
            raw_response=raw_bear_6,
            speaker_label="Bear Analyst",
            speaker_key="Bear",
            stance="bearish",
            history_key="bear_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert final_state["count"] == 6
        # Pre-gate validation on final state claims
        gate_errors = validate_debate_preconditions(final_state, claims=final_state["claims"])
        assert gate_errors == [], f"Research Manager pre-gate failed: {gate_errors}"

    def test_offline_full_replay_000333_golden_data(self):
        """Full replay of the 6 rounds from 000333 golden dataset.
        Verifies that clone INV-9 is blocked from claims, INV-10 is stored,
        and all 6 rounds complete with clean claim ledger.
        """
        raw_turns = [
            # Round 1: Bull
            (
                "Bull Analyst", "Bull", "bullish", "bull_history",
                "多头立论：地量供给测试与回购构筑安全垫。\n"
                '<!-- DEBATE_STATE: {"responded_claim_ids": [], "new_claims": [{"claim": "地量供给测试确立双底，主力大单逆势吸筹酝酿短线突破", "evidence": ["08-21日成交量萎缩至1802.1万股创地量且量比0.6，82.39元双底确立", "主力大单逆势净流入1.1134亿元，中单流出1.6449亿元，筹码加速收敛"], "confidence": 0.85, "target_claim_ids": []}, {"claim": "70亿回购构筑强安全底座，以旧换新与出海提供不对称赔率", "evidence": ["截至7月底累计回购69.73亿元(均价79.79元，上限87.71元)，Q1自由现金流达123.86亿元", "2025年海外营收1959.48亿元占比42.7%，以旧换新绿色补贴15-20%拉动高能效ASP"], "confidence": 0.88, "target_claim_ids": []}], "resolved_claim_ids": [], "unresolved_claim_ids": [], "next_focus_claim_ids": [], "round_summary": "多头立论", "round_goal": "立论"} -->'
            ),
            # Round 2: Bear
            (
                "Bear Analyst", "Bear", "bearish", "bear_history",
                "空头反驳：阻力压制与铜价侵蚀。\n"
                '<!-- DEBATE_STATE: {"responded_claim_ids": ["INV-1", "INV-2"], "new_claims": [{"claim": "86元阻力压制且地量显现买力枯竭", "evidence": ["08-21冲高85.99元大幅回落收带长上影中阴线", "全单净流出1.71亿元且融券卖出超5000万，量比仅0.6显示买盘枯竭"], "confidence": 0.80, "target_claim_ids": ["INV-1"]}, {"claim": "回购动能耗尽且高铜价侵蚀Q3毛利", "evidence": ["69.73亿回购均价79.79元低于现价5.6%已充分定价", "LME铜价达14181美元高位压制毛利0.3-0.5pct，大金单月跌15.58%映射外需分化"], "confidence": 0.82, "target_claim_ids": ["INV-2"]}], "resolved_claim_ids": [], "unresolved_claim_ids": ["INV-1", "INV-2"], "next_focus_claim_ids": ["INV-1"], "round_summary": "空头反驳", "round_goal": "反驳"} -->'
            ),
            # Round 3: Bull
            (
                "Bull Analyst", "Bull", "bullish", "bull_history",
                "多头深化：突破中轨在即与全球布局对冲。\n"
                '<!-- DEBATE_STATE: {"responded_claim_ids": ["INV-3", "INV-4"], "new_claims": [{"claim": "大单逆势吸筹确立地量洗盘尾声，突破中轨在即", "evidence": ["主力大单08-21净流入1.1134亿元，成交量1802万股创地量量比0.6", "82.39元双底确立，紧贴20日VWMA84.50元筹码收敛"], "confidence": 0.86, "target_claim_ids": ["INV-3"]}, {"claim": "垂直整合与高端化对冲成本，全球布局构筑不对称底座", "evidence": ["以旧换新补贴15-20%拉升ASP覆盖铜价扰动，单季FCF达123.86亿元", "惠而浦单月涨8.64%验证外需补库，极端情景净利底线310-330亿元"], "confidence": 0.87, "target_claim_ids": ["INV-4"]}], "resolved_claim_ids": [], "unresolved_claim_ids": ["INV-3", "INV-4"], "next_focus_claim_ids": ["INV-3"], "round_summary": "多头深化", "round_goal": "深化"} -->'
            ),
            # Round 4: Bear
            (
                "Bear Analyst", "Bear", "bearish", "bear_history",
                "空头深化：中轨假突破与应收账款毛利挤压。\n"
                '<!-- DEBATE_STATE: {"responded_claim_ids": ["INV-5", "INV-6"], "new_claims": [{"claim": "中轨长上影假突破显现买力衰竭", "evidence": ["08-21冲高85.99元遇阻收于日内低点84.30元(位置0.03)", "全单流出1.71亿元且成交仅1802万股量比0.6证实无需求上攻"], "confidence": 0.81, "target_claim_ids": ["INV-5"]}, {"claim": "应收攀升叠加高铜价预警毛利挤压", "evidence": ["2026Q1应收账款达525.37亿元同比增7.95%远超营收增速2.55%", "LME铜价达14181美元高位压制毛利0.3-0.5pct且大金单月暴跌15.58%"], "confidence": 0.83, "target_claim_ids": ["INV-6"]}], "resolved_claim_ids": [], "unresolved_claim_ids": ["INV-5", "INV-6"], "next_focus_claim_ids": ["INV-5"], "round_summary": "空头深化", "round_goal": "深化"} -->'
            ),
            # Round 5: Bull (Contains INV-9 duplicate of INV-1/5 + INV-10 valid)
            (
                "Bull Analyst", "Bull", "bullish", "bull_history",
                "多头决战：\n"
                '<!-- DEBATE_STATE: {"responded_claim_ids": ["INV-7", "INV-8"], "new_claims": [{"claim": "地量洗盘确立双底，大单吸筹蓄势突破中轨", "evidence": ["08-21日成交量萎缩至1802.1万股创地量量比0.6，82.39元双底确立", "主力大单逆势净流入1.1134亿元，中单流出1.6449亿元，筹码加速收敛"], "confidence": 0.73, "target_claim_ids": ["INV-7"]}, {"claim": "一体化与出海对冲成本，近70亿回购构筑铁底", "evidence": ["2026Q1单季自由现金流达123.86亿元，应收周转率2.82次营运健康", "以旧换新补贴15-20%拉升ASP覆盖铜价扰动，惠而浦单月涨8.64%印证外需"], "confidence": 0.71, "target_claim_ids": ["INV-8"]}], "resolved_claim_ids": ["INV-5", "INV-6"], "unresolved_claim_ids": ["INV-7", "INV-8"], "next_focus_claim_ids": ["INV-7", "INV-8"], "round_summary": "多头决战", "round_goal": "决战"} -->'
            ),
            # Round 6: Bear
            (
                "Bear Analyst", "Bear", "bearish", "bear_history",
                "空头决战：\n"
                '<!-- DEBATE_STATE: {"responded_claim_ids": ["INV-10"], "new_claims": [{"claim": "冲高回落收最低点确立无需求破位且海外反补贴升级", "evidence": ["欧盟拟对家电反补贴征税8-12%", "全单净流出1.71亿元且成交量1802万股创地量量比0.6证实买力枯竭"], "confidence": 0.75, "target_claim_ids": ["INV-10"]}], "resolved_claim_ids": [], "unresolved_claim_ids": ["INV-10"], "next_focus_claim_ids": ["INV-10"], "round_summary": "空头决战", "round_goal": "决战"} -->'
            ),
        ]

        curr_state = {
            "count": 0,
            "current_speaker": "",
            "claims": [],
            "round_messages": [],
            "open_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "claim_counter": 0,
            "history": "",
            "bull_history": "",
            "bear_history": "",
        }

        for spk_label, spk_key, stance, hist_key, raw_resp in raw_turns:
            curr_state = update_debate_state_with_payload(
                state=curr_state,
                raw_response=raw_resp,
                speaker_label=spk_label,
                speaker_key=spk_key,
                stance=stance,
                history_key=hist_key,
                marker="DEBATE_STATE",
                claim_prefix="INV",
                domain="investment",
                speaker_field="current_speaker",
            )

        assert curr_state["count"] == 6
        claim_ids = [c["claim_id"] for c in curr_state["claims"]]
        # Verify INV-9 was rejected and NOT in claims
        assert "INV-9" not in claim_ids
        # Verify INV-10 is in claims
        assert "INV-10" in claim_ids
        # Verify Round 5 message metadata
        r5_msg = curr_state["round_messages"][4]
        assert r5_msg["duplicate_claim_ids"] == ["INV-9"]
        assert r5_msg["new_claim_ids"] == ["INV-10"]

        # Pre-gate consistency
        errors = validate_debate_preconditions(curr_state, claims=curr_state["claims"])
        assert errors == []
