"""Comprehensive unit and E2E regression tests for DAV-334:
Research Manager Claim Evidence Coverage & Adoption Hard Gate.

Covers:
1. Claim evidence aggregation (counts, coverage ratio, verified/unsupported/contradicted/unavailable).
2. Deterministic decision rules:
   - Full verified (coverage=1.0, 0 contradicted, 0 unavailable) -> adopt
   - Mixed evidence (coverage>=0.67, 0 contradicted, 0 unavailable) -> partial
   - Low coverage (coverage<0.67) or 100% unsupported -> reject
   - Contradicted / Source unavailable -> reject
3. Manager verdict consistency hard gate:
   - Full verified in adopted_claim_ids -> PASS
   - Mixed evidence in partially_adopted_claims + excluded_evidence recorded -> PASS
   - Mixed evidence directly in adopted_claim_ids -> FAIL (fail-closed)
   - Mixed evidence in partially_adopted_claims but prose claiming '证据充分' -> FAIL
   - Unsupported / Contradicted / Unavailable claim in adopted or partial list -> FAIL
4. Research Manager node integration and prompt injection with verification badges.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.utils.evidence_verifier import (
    DECISION_ADOPT,
    DECISION_PARTIAL,
    DECISION_REJECT,
    EvidenceFactualTruthEvaluator,
    STATUS_CONTRADICTED,
    STATUS_SOURCE_UNAVAILABLE,
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
    aggregate_claim_evidence,
    extract_and_validate_manager_verdict,
    format_claims_with_verification_for_prompt,
)


async def _fake_stream(text: str):
    from types import SimpleNamespace
    yield SimpleNamespace(content=text)


def _make_e2e_debate_state():
    round_messages = [
        {"message_index": 1, "debate_round": 1, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": [], "target_claim_ids": [], "new_claim_ids": ["INV-1"]},
        {"message_index": 2, "debate_round": 1, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-1"], "target_claim_ids": ["INV-1"], "new_claim_ids": ["INV-2"]},
        {"message_index": 3, "debate_round": 2, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-2"], "target_claim_ids": ["INV-2"], "new_claim_ids": ["INV-3"]},
        {"message_index": 4, "debate_round": 2, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-3"], "target_claim_ids": ["INV-3"], "new_claim_ids": ["INV-4"]},
        {"message_index": 5, "debate_round": 3, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-4"], "target_claim_ids": ["INV-4"], "new_claim_ids": ["INV-5"]},
        {"message_index": 6, "debate_round": 3, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-5"], "target_claim_ids": ["INV-5"], "new_claim_ids": ["INV-6"]},
    ]
    claims = [
        # INV-1: 100% verified (2 verified, 0 unverified)
        {
            "claim_id": "INV-1",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "在手订单充足与营收高增",
            "evidence": ["营收同比增长30%", "在手订单增长50%"],
            "confidence": 0.85,
        },
        # INV-2: source unavailable (fatal hallucination)
        {
            "claim_id": "INV-2",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "海外指数重挫压制估值",
            "evidence": ["global_indices重挫3%"],
            "confidence": 0.70,
        },
        # INV-3: 100% verified
        {
            "claim_id": "INV-3",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "均线多头排列且央行降息",
            "evidence": ["央行降息25bp", "均线多头排列"],
            "confidence": 0.88,
        },
        # INV-4: contradicted (factual contradiction)
        {
            "claim_id": "INV-4",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "毛利率严重下滑",
            "evidence": ["毛利率暴跌至10%"],
            "confidence": 0.75,
        },
        # INV-5: mixed evidence (2 verified, 1 unsupported -> coverage = 2/3 = 66.7% >= 67%)
        {
            "claim_id": "INV-5",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": "主力持续增持与市场情绪高涨",
            "evidence": ["主力净流入5.2亿元", "情绪报告看多占比65%", "某机构私下调研看好翻倍"],
            "confidence": 0.90,
        },
        # INV-6: low coverage (1 verified, 2 unsupported -> coverage = 1/3 = 33.3% < 67%)
        {
            "claim_id": "INV-6",
            "speaker": "Bear Analyst",
            "speaker_key": "Bear",
            "stance": "bearish",
            "claim": "新进入者价格战加剧",
            "evidence": ["行业新政落地", "新进入者产能扩张500万吨", "行业整体出现300亿元亏损"],
            "confidence": 0.72,
        },
    ]
    return {
        "macro_report": "宏观报告：央行降息25bp，流动性维持宽松，M2增速10.5%。",
        "market_report": "市场技术报告：突破20.0元关键阻力位，均线多头排列。",
        "sentiment_report": "情绪报告：情绪报告看多占比65%，散户存在分歧。",
        "news_report": "新闻报告：行业新政落地，在手订单增长50%。",
        "fundamentals_report": "基本面报告：营收同比增长30%，毛利率达到28.5%，在手订单充足。",
        "smart_money_report": "主力资金报告：主力净流入5.2亿元，超大单积极建仓吸筹。",
        "volume_price_report": "量价报告：放量长阳突破整理平台，成交量放大1.5倍。",
        "market_data_context": {
            "analysis_baseline_date": "2026-08-22",
            "trade_date": "2026-08-22",
            "source_provenance": {
                "stock_data": {"status": "available", "as_of": "2026-08-22"},
                "tushare_daily": {"status": "available", "as_of": "2026-08-22"},
                "global_indices": {"status": "failed", "reason": "connection timeout"},
            },
            "data_failure_ledger": [
                {"source": "global_indices", "status": "failed", "reason": "接口超时"},
            ],
            "data_gaps": ["global_indices数据缺失"],
        },
        "investment_debate_state": {
            "history": "辩论历史记录",
            "bull_history": "多头发言",
            "bear_history": "空头发言",
            "current_speaker": "Bear",
            "current_response": "空头最后发言",
            "count": 6,
            "claims": claims,
            "round_messages": round_messages,
            "focus_claim_ids": ["INV-1"],
            "open_claim_ids": ["INV-1", "INV-2", "INV-3", "INV-4", "INV-5", "INV-6"],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": ["INV-1"],
            "round_summary": "多空激辩总结",
            "round_goal": "达成裁决",
            "claim_counter": 6,
        },
        "fund_flow_consensus_guard": {
            "blocked": False,
            "direction_allowed": True,
            "status": "consensus",
        },
        "trade_date": "2026-08-22",
        "horizon": "medium",
    }


class TestClaimEvidenceAggregation:
    """Test claim-level aggregation of evidence verification counts, coverage, and decisions."""

    def test_aggregate_all_verified_claim(self):
        claims = [
            {
                "claim_id": "INV-1",
                "evidence": ["营收同比增长30%", "在手订单增长50%"],
            }
        ]
        verifications = [
            {"claim_id": "INV-1", "raw": "营收同比增长30%", "status": STATUS_VERIFIED, "is_fatal": False},
            {"claim_id": "INV-1", "raw": "在手订单增长50%", "status": STATUS_VERIFIED, "is_fatal": False},
        ]
        summary = aggregate_claim_evidence(claims=claims, claims_verification=verifications)
        assert "INV-1" in summary
        s = summary["INV-1"]
        assert s["counts"]["total"] == 2
        assert s["counts"]["verified"] == 2
        assert s["counts"]["unsupported"] == 0
        assert s["counts"]["contradicted"] == 0
        assert s["counts"]["source_unavailable"] == 0
        assert s["coverage"] == 1.0
        assert s["decision"] == DECISION_ADOPT
        assert len(s["excluded_evidence"]) == 0
        assert len(s["verified_evidence"]) == 2

    def test_aggregate_mixed_evidence_claim_passes_threshold(self):
        claims = [
            {
                "claim_id": "INV-5",
                "evidence": ["主力净流入5.2亿元", "情绪报告看多占比65%", "某机构私下调研看好翻倍"],
            }
        ]
        verifications = [
            {"claim_id": "INV-5", "raw": "主力净流入5.2亿元", "status": STATUS_VERIFIED, "is_fatal": False},
            {"claim_id": "INV-5", "raw": "情绪报告看多占比65%", "status": STATUS_VERIFIED, "is_fatal": False},
            {"claim_id": "INV-5", "raw": "某机构私下调研看好翻倍", "status": STATUS_UNSUPPORTED, "is_fatal": False},
        ]
        summary = aggregate_claim_evidence(claims=claims, claims_verification=verifications)
        assert "INV-5" in summary
        s = summary["INV-5"]
        assert s["counts"]["total"] == 3
        assert s["counts"]["verified"] == 2
        assert s["counts"]["unsupported"] == 1
        assert pytest.approx(s["coverage"], 0.01) == 0.67
        assert s["decision"] == DECISION_PARTIAL
        assert s["excluded_evidence"] == ["某机构私下调研看好翻倍"]
        assert len(s["verified_evidence"]) == 2

    def test_aggregate_low_coverage_mixed_claim_rejected(self):
        claims = [
            {
                "claim_id": "INV-6",
                "evidence": ["行业新政落地", "新进入者降价50%", "产能严重过剩100%"],
            }
        ]
        verifications = [
            {"claim_id": "INV-6", "raw": "行业新政落地", "status": STATUS_VERIFIED, "is_fatal": False},
            {"claim_id": "INV-6", "raw": "新进入者降价50%", "status": STATUS_UNSUPPORTED, "is_fatal": False},
            {"claim_id": "INV-6", "raw": "产能严重过剩100%", "status": STATUS_UNSUPPORTED, "is_fatal": False},
        ]
        summary = aggregate_claim_evidence(claims=claims, claims_verification=verifications)
        assert "INV-6" in summary
        s = summary["INV-6"]
        assert s["counts"]["total"] == 3
        assert s["counts"]["verified"] == 1
        assert s["counts"]["unsupported"] == 2
        assert pytest.approx(s["coverage"], 0.01) == 0.33
        assert s["decision"] == DECISION_REJECT
        assert "覆盖率不足" in s["reason"]

    def test_aggregate_contradicted_claim_rejected(self):
        claims = [
            {
                "claim_id": "INV-4",
                "evidence": ["毛利率达到28.5%", "毛利率暴跌至10%"],
            }
        ]
        verifications = [
            {"claim_id": "INV-4", "raw": "毛利率达到28.5%", "status": STATUS_VERIFIED, "is_fatal": False},
            {"claim_id": "INV-4", "raw": "毛利率暴跌至10%", "status": STATUS_CONTRADICTED, "is_fatal": False},
        ]
        summary = aggregate_claim_evidence(claims=claims, claims_verification=verifications)
        assert "INV-4" in summary
        s = summary["INV-4"]
        assert s["counts"]["contradicted"] == 1
        assert s["decision"] == DECISION_REJECT
        assert "矛盾" in s["reason"] or "冲突" in s["reason"]

    def test_aggregate_unavailable_source_claim_rejected(self):
        claims = [
            {
                "claim_id": "INV-2",
                "evidence": ["global_indices重挫3%"],
            }
        ]
        verifications = [
            {"claim_id": "INV-2", "raw": "global_indices重挫3%", "status": STATUS_SOURCE_UNAVAILABLE, "is_fatal": True},
        ]
        summary = aggregate_claim_evidence(claims=claims, claims_verification=verifications)
        assert "INV-2" in summary
        s = summary["INV-2"]
        assert s["counts"]["source_unavailable"] == 1
        assert s["decision"] == DECISION_REJECT
        assert "严重幻觉" in s["reason"] or "不可用" in s["reason"]

    def test_aggregate_fully_unsupported_claim_rejected(self):
        claims = [
            {
                "claim_id": "INV-7",
                "evidence": ["传闻某客户退单"],
            }
        ]
        verifications = [
            {"claim_id": "INV-7", "raw": "传闻某客户退单", "status": STATUS_UNSUPPORTED, "is_fatal": False},
        ]
        summary = aggregate_claim_evidence(claims=claims, claims_verification=verifications)
        assert "INV-7" in summary
        s = summary["INV-7"]
        assert s["counts"]["verified"] == 0
        assert s["coverage"] == 0.0
        assert s["decision"] == DECISION_REJECT


class TestManagerVerdictConsistencyHardGateCoverage:
    """Test consistency hard gate on claim adoption rules."""

    def test_full_verified_adoption_and_partial_mixed_adoption_passes(self):
        state = _make_e2e_debate_state()
        evaluator = EvidenceFactualTruthEvaluator()
        claims_verification = evaluator.evaluate_claims(
            claims=state["investment_debate_state"]["claims"],
            seven_reports={
                "macro_report": state["macro_report"],
                "market_report": state["market_report"],
                "sentiment_report": state["sentiment_report"],
                "news_report": state["news_report"],
                "fundamentals_report": state["fundamentals_report"],
                "smart_money_report": state["smart_money_report"],
                "volume_price_report": state["volume_price_report"],
            },
            market_data_context=state["market_data_context"],
            analysis_baseline_date="2026-08-22",
        )

        raw_output = """【投研经理裁决报告】
第一步审查：INV-1全部核验通过，标证据充分；INV-5为混合证据，仅采纳主力净流入与看多情绪等verified子结论，标部分支持，未验证传闻予以剔除；INV-2/4/6予以驳回。
多头胜出。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "多头证据扎实", "position_pct": 60, "entry": "20.0", "target": "25.0", "stop_loss": "19.0", "upside": 25.0, "downside": 5.0, "odds": 5.0, "adopted_claim_ids": ["INV-1", "INV-3"], "partially_adopted_claims": ["INV-5"], "rejected_claim_ids": ["INV-2", "INV-4", "INV-6"], "excluded_evidence": ["某机构私下调研看好翻倍"]} -->
<!-- VERDICT: {"direction": "看多", "reason": "多头营收与资金扎实"} -->"""

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw_output,
            claims_verification=claims_verification,
            claims=state["investment_debate_state"]["claims"],
        )
        assert verdict["consistency_check_passed"] is True
        assert verdict["adopted_claim_ids"] == ["INV-1", "INV-3"]
        assert verdict["partially_adopted_claims"] == ["INV-5"]
        assert "某机构私下调研看好翻倍" in verdict["excluded_evidence"]
        assert len(verdict["failed_checks"]) == 0

    def test_mixed_evidence_in_adopted_claim_ids_fails_gate(self):
        state = _make_e2e_debate_state()
        evaluator = EvidenceFactualTruthEvaluator()
        claims_verification = evaluator.evaluate_claims(
            claims=state["investment_debate_state"]["claims"],
            seven_reports={
                "macro_report": state["macro_report"],
                "market_report": state["market_report"],
                "sentiment_report": state["sentiment_report"],
                "news_report": state["news_report"],
                "fundamentals_report": state["fundamentals_report"],
                "smart_money_report": state["smart_money_report"],
                "volume_price_report": state["volume_price_report"],
            },
            market_data_context=state["market_data_context"],
            analysis_baseline_date="2026-08-22",
        )

        # Violation: INV-5 is mixed evidence, but LLM put it directly in adopted_claim_ids
        raw_output = """【投研经理裁决报告】
多头胜出。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "采纳全部多头论点", "position_pct": 60, "entry": "20.0", "target": "25.0", "stop_loss": "19.0", "adopted_claim_ids": ["INV-1", "INV-5"], "partially_adopted_claims": [], "rejected_claim_ids": ["INV-2", "INV-4", "INV-6"]} -->"""

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw_output,
            claims_verification=claims_verification,
            claims=state["investment_debate_state"]["claims"],
        )
        assert verdict["consistency_check_passed"] is False
        assert any("全额采纳了含未核实混合证据的 claim: INV-5" in err for err in verdict["failed_checks"])

    def test_prose_marking_mixed_claim_as_sufficient_evidence_fails_gate(self):
        state = _make_e2e_debate_state()
        evaluator = EvidenceFactualTruthEvaluator()
        claims_verification = evaluator.evaluate_claims(
            claims=state["investment_debate_state"]["claims"],
            seven_reports={
                "macro_report": state["macro_report"],
                "market_report": state["market_report"],
                "sentiment_report": state["sentiment_report"],
                "news_report": state["news_report"],
                "fundamentals_report": state["fundamentals_report"],
                "smart_money_report": state["smart_money_report"],
                "volume_price_report": state["volume_price_report"],
            },
            market_data_context=state["market_data_context"],
            analysis_baseline_date="2026-08-22",
        )

        # Violation: Prose states INV-5 is '证据充分' even though machine block recorded partial
        raw_output = """【投研经理裁决报告】
经过逐条核验，INV-5证据充分，多头全面胜出。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "多头胜", "position_pct": 60, "entry": "20.0", "target": "25.0", "stop_loss": "19.0", "adopted_claim_ids": ["INV-1"], "partially_adopted_claims": ["INV-5"], "rejected_claim_ids": ["INV-2", "INV-4", "INV-6"]} -->"""

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw_output,
            claims_verification=claims_verification,
            claims=state["investment_debate_state"]["claims"],
        )
        assert verdict["consistency_check_passed"] is False
        assert any("正文将未完全核实的 claim INV-5" in err and "证据充分" in err for err in verdict["failed_checks"])

    def test_adopting_contradicted_or_unavailable_claim_fails_gate(self):
        state = _make_e2e_debate_state()
        evaluator = EvidenceFactualTruthEvaluator()
        claims_verification = evaluator.evaluate_claims(
            claims=state["investment_debate_state"]["claims"],
            seven_reports={
                "macro_report": state["macro_report"],
                "market_report": state["market_report"],
                "sentiment_report": state["sentiment_report"],
                "news_report": state["news_report"],
                "fundamentals_report": state["fundamentals_report"],
                "smart_money_report": state["smart_money_report"],
                "volume_price_report": state["volume_price_report"],
            },
            market_data_context=state["market_data_context"],
            analysis_baseline_date="2026-08-22",
        )

        # Adopting contradicted INV-4
        raw_output = """【投研经理裁决报告】
空头胜出。
<!-- MANAGER_VERDICT: {"winner": "bear", "direction": "看空", "reason": "采纳空头毛利下滑", "position_pct": 0, "adopted_claim_ids": ["INV-4"], "rejected_claim_ids": []} -->"""

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw_output,
            claims_verification=claims_verification,
            claims=state["investment_debate_state"]["claims"],
        )
        assert verdict["consistency_check_passed"] is False
        assert any("存在事实冲突" in err or "矛盾" in err for err in verdict["failed_checks"])

    def test_adopting_low_coverage_claim_fails_gate(self):
        state = _make_e2e_debate_state()
        evaluator = EvidenceFactualTruthEvaluator()
        claims_verification = evaluator.evaluate_claims(
            claims=state["investment_debate_state"]["claims"],
            seven_reports={
                "macro_report": state["macro_report"],
                "market_report": state["market_report"],
                "sentiment_report": state["sentiment_report"],
                "news_report": state["news_report"],
                "fundamentals_report": state["fundamentals_report"],
                "smart_money_report": state["smart_money_report"],
                "volume_price_report": state["volume_price_report"],
            },
            market_data_context=state["market_data_context"],
            analysis_baseline_date="2026-08-22",
        )

        # Adopting INV-6 (coverage 33.3%)
        raw_output = """【投研经理裁决报告】
空头胜出。
<!-- MANAGER_VERDICT: {"winner": "bear", "direction": "看空", "reason": "采纳空头竞争论点", "position_pct": 0, "adopted_claim_ids": ["INV-6"], "rejected_claim_ids": []} -->"""

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw_output,
            claims_verification=claims_verification,
            claims=state["investment_debate_state"]["claims"],
        )
        assert verdict["consistency_check_passed"] is False
        assert any("证据覆盖率不足" in err for err in verdict["failed_checks"])


class TestPromptFormatAndVerificationPresentation:
    """Test format_claims_with_verification_for_prompt produces accurate badges and text."""

    def test_format_claims_with_verification_output(self):
        claims = [
            {"claim_id": "INV-1", "speaker": "Bull", "stance": "bullish", "claim": "营收高增", "evidence": ["营收30%"]},
            {"claim_id": "INV-5", "speaker": "Bull", "stance": "bullish", "claim": "主力增持", "evidence": ["流入5亿", "产能95%", "传闻"]},
        ]
        verifications = [
            {"claim_id": "INV-1", "raw": "营收30%", "status": STATUS_VERIFIED, "matched_role": "fundamentals_report"},
            {"claim_id": "INV-5", "raw": "流入5亿", "status": STATUS_VERIFIED, "matched_role": "smart_money_report"},
            {"claim_id": "INV-5", "raw": "产能95%", "status": STATUS_VERIFIED, "matched_role": "fundamentals_report"},
            {"claim_id": "INV-5", "raw": "传闻", "status": STATUS_UNSUPPORTED, "details": "未找到支撑"},
        ]
        text = format_claims_with_verification_for_prompt(claims=claims, claims_verification=verifications)
        assert "INV-1" in text
        assert "【证据充分 / 全Verified】" in text
        assert "覆盖率=100.0%" in text
        assert "[VERIFIED / 真实核验] 营收30%" in text

        assert "INV-5" in text
        assert "【部分支持 / 混合证据(仅采纳Verified子结论)】" in text
        assert "[UNSUPPORTED / 未获支撑] 传闻" in text
        assert "严禁作为采纳依据，必须剔除" in text


class TestResearchManagerIntegrationWithEvidenceGate:
    """Integration test for create_research_manager with claim evidence gate."""

    def test_research_manager_runs_and_passes_valid_adjudication(self):
        state = _make_e2e_debate_state()

        llm_response = """【研究总监裁决报告】
各分析师观点穿透：
1. 证据审查：INV-1与INV-3全Verified，标证据充分；INV-5为混合证据，仅采纳主力增持与情绪结论，标部分支持；INV-2/4/6驳回。
2. 传导路径：降息与订单饱满支持多头。
3. 裁决结论：多头全面胜出。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "多头证据扎实", "position_pct": 60, "entry": "20.0", "target": "25.0", "stop_loss": "19.0", "upside": 25.0, "downside": 5.0, "odds": 5.0, "adopted_claim_ids": ["INV-1", "INV-3"], "partially_adopted_claims": ["INV-5"], "rejected_claim_ids": ["INV-2", "INV-4", "INV-6"], "excluded_evidence": ["某机构私下调研看好翻倍"]} -->
<!-- VERDICT: {"direction": "看多", "reason": "多头证据扎实"} -->"""

        mock_llm = MagicMock()
        mock_llm.astream = MagicMock(side_effect=lambda prompt: _fake_stream(llm_response))
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        rm_node = create_research_manager(mock_llm, memory)
        result = asyncio.run(rm_node(state))

        assert result["manager_verdict"]["consistency_check_passed"] is True
        assert result["manager_verdict"]["winner"] == "bull"
        assert result["manager_verdict"]["adopted_claim_ids"] == ["INV-1", "INV-3"]
        assert result["manager_verdict"]["partially_adopted_claims"] == ["INV-5"]
        assert "某机构私下调研看好翻倍" in result["manager_verdict"]["excluded_evidence"]
        assert "INV-1" in result["manager_verdict"]["claim_evidence_summary"]
        assert result["manager_verdict"]["claim_evidence_summary"]["INV-1"]["decision"] == DECISION_ADOPT
        assert result["manager_verdict"]["claim_evidence_summary"]["INV-5"]["decision"] == DECISION_PARTIAL
        assert result["manager_verdict"]["claim_evidence_summary"]["INV-4"]["decision"] == DECISION_REJECT

    def test_research_manager_blocks_when_llm_violates_claim_coverage_gate(self):
        state = _make_e2e_debate_state()

        # Violation: Adopts unverified/low-coverage INV-6 and mixed INV-5 in adopted_claim_ids
        bad_response = """【研究总监裁决报告】
裁决结论发生冲突。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "违规采纳", "position_pct": 60, "entry": "20.0", "target": "25.0", "stop_loss": "19.0", "adopted_claim_ids": ["INV-5", "INV-6"], "partially_adopted_claims": []} -->"""

        mock_llm = MagicMock()
        mock_llm.astream = MagicMock(side_effect=lambda prompt: _fake_stream(bad_response))
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        rm_node = create_research_manager(mock_llm, memory)
        result = asyncio.run(rm_node(state))

        assert result["manager_verdict"]["consistency_check_passed"] is False
        assert "研究总监裁决自洽硬闸未通过" in result["investment_plan"]
        assert "已阻断进入 Trader 执行阶段" in result["investment_plan"]
