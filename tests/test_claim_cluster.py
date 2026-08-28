"""Unit and integration tests for P0-4b: Claim evidence clustering and vote deduplication.

Covers:
1. Foxconn Nail Case: 3 price-derived reports (Market, Volume-Price, Smart Money) referencing
   the same close price shock, volume, and daily return collapse into 1 single cluster.
   Directional weight must use independent_cluster_count (1 cluster), NOT analyst_count (3 votes, 60%).
2. Independent Fundamentals: 4th report referencing verifiable revenue / net profit forms a 2nd cluster.
3. Unsupported / Narrative claim: Claim with no clusterable observable data points gets status unsupported
   and is excluded from independent_cluster_count and verified_evidence_count.
4. Same Cluster Same Direction Vote Ceiling: Multiple same-direction claims in the same cluster cast at most 1 vote.
5. Deterministic Cluster ID: Stable hash / ID generation is reproducible and lockable.
6. Prompt & Manager State Integration: Prompt specifies cluster deduplication while retaining DAV-336 7 analysts.
"""
from __future__ import annotations

import pytest

from tradingagents.agents.utils.claim_cluster import (
    CLUSTER_TYPE_FUNDAMENTALS,
    CLUSTER_TYPE_MACRO_POLICY,
    CLUSTER_TYPE_PRICE_SHOCK,
    CLUSTER_TYPE_SENTIMENT_NEWS,
    CLUSTER_TYPE_UNSUPPORTED,
    assign_claim_cluster,
    compute_cluster_id,
    extract_evidence_ids,
    tally_cluster_votes,
)
from tradingagents.prompts import get_prompt
from tradingagents.prompts.catalog import ZH_PROMPTS, EN_PROMPTS


class TestClaimClusterCore:
    """Core clustering and deterministic ID tests."""

    def test_cluster_id_is_stable_and_deterministic(self):
        """Cluster ID must be deterministic, reproducible, and lockable across runs."""
        cid1 = compute_cluster_id(
            cluster_type=CLUSTER_TYPE_PRICE_SHOCK,
            symbol="601138.SH",
            date="2026-08-22",
        )
        cid2 = compute_cluster_id(
            cluster_type=CLUSTER_TYPE_PRICE_SHOCK,
            symbol="601138.SH",
            date="2026-08-22",
        )
        assert cid1 == cid2
        assert "price_shock" in cid1 or "601138" in cid1

        cid_fund = compute_cluster_id(
            cluster_type=CLUSTER_TYPE_FUNDAMENTALS,
            symbol="601138.SH",
            date="2026-Q2",
        )
        assert cid1 != cid_fund

    def test_foxconn_nail_case_three_price_derived_reports_collapse_to_one_cluster(self):
        """Acceptance Nail: 3 price-derived reports (Market, Volume-Price, Smart Money)

        on 工业富联 referencing same close price, volume, and daily pct change must form
        exactly 1 cluster. When evaluated alongside 2 bear reports (forming 1 bear cluster),
        the bull weight must be 1/(1+1)=50%, NOT 3/5=60%.
        """
        symbol = "601138.SH"
        trade_date = "2026-08-22"

        # 3 Bull analysts quoting price-derived metrics for 工业富联 on 2026-08-22
        market_claim = {
            "claim_id": "INV-1",
            "speaker": "Market Analyst",
            "speaker_key": "market",
            "stance": "bullish",
            "claim": "工业富联放量突破22.5元收盘价阻力位",
            "evidence": ["收盘价22.5元", "突破20日均线"],
            "confidence": 0.85,
        }
        volume_price_claim = {
            "claim_id": "INV-2",
            "speaker": "Volume Price Analyst",
            "speaker_key": "volume_price",
            "stance": "bullish",
            "claim": "成交量放大1.5倍且当日涨幅5.2%",
            "evidence": ["成交量放大1.5倍", "当日涨幅5.2%"],
            "confidence": 0.88,
        }
        smart_money_claim = {
            "claim_id": "INV-3",
            "speaker": "Smart Money Analyst",
            "speaker_key": "smart_money",
            "stance": "bullish",
            "claim": "主力资金放量净流入且长阳收盘",
            "evidence": ["主力资金净流入5.2亿元", "收盘价22.5元"],
            "confidence": 0.80,
        }

        # 2 Bear analysts quoting a distinct fundamental risk cluster
        bear_fund_claim = {
            "claim_id": "INV-4",
            "speaker": "Fundamentals Analyst",
            "speaker_key": "fundamentals",
            "stance": "bearish",
            "claim": "应收账款周转率下降且毛利率承压",
            "evidence": ["应收账款周转天数增加15天", "毛利率同比下滑2.1%"],
            "confidence": 0.75,
        }
        bear_macro_claim = {
            "claim_id": "INV-5",
            "speaker": "Macro Analyst",
            "speaker_key": "macro",
            "stance": "bearish",
            "claim": "行业产能扩张导致毛利率下滑",
            "evidence": ["毛利率同比下滑2.1%"],
            "confidence": 0.70,
        }

        claims = [
            market_claim,
            volume_price_claim,
            smart_money_claim,
            bear_fund_claim,
            bear_macro_claim,
        ]

        metrics = tally_cluster_votes(
            claims=claims,
            symbol=symbol,
            trade_date=trade_date,
        )

        # 1. analyst_count must be 5 (explanatory only)
        assert metrics["analyst_count"] == 5

        # 2. Bull side: 3 price-derived reports collapse into 1 cluster
        assert metrics["direction_cluster_counts"]["bull"] == 1
        assert metrics["bull_cluster_count"] == 1

        # 3. Bear side: 2 fundamental reports collapse into 1 cluster
        assert metrics["direction_cluster_counts"]["bear"] == 1
        assert metrics["bear_cluster_count"] == 1

        # 4. Total independent clusters must be 2 (price_shock + fundamentals)
        assert metrics["independent_cluster_count"] == 2

        # 5. Bull direction weight MUST be 1 / 2 = 50%, strictly NOT 3 / 5 = 60%!
        assert metrics["cluster_weights"]["bull"] == 0.50
        assert metrics["cluster_weights"]["bull"] != 0.60
        assert metrics["cluster_weights"]["bear"] == 0.50

    def test_independent_fundamentals_forms_distinct_cluster(self):
        """4th report referencing verifiable revenue / profit forms an independent cluster."""
        symbol = "601138.SH"
        trade_date = "2026-08-22"

        # 3 price-derived reports
        c1 = {
            "claim_id": "INV-1",
            "speaker": "Market Analyst",
            "stance": "bullish",
            "claim": "突破22.5元收盘价阻力位",
            "evidence": ["收盘价22.5元", "日涨幅5.2%"],
        }
        c2 = {
            "claim_id": "INV-2",
            "speaker": "Volume Price Analyst",
            "stance": "bullish",
            "claim": "放量成交量放大1.5倍",
            "evidence": ["成交量放大1.5倍"],
        }
        c3 = {
            "claim_id": "INV-3",
            "speaker": "Smart Money Analyst",
            "stance": "bullish",
            "claim": "主力资金净流入",
            "evidence": ["主力资金净流入5.2亿元"],
        }

        # 4th report with independent fundamental revenue/profit
        c4 = {
            "claim_id": "INV-4",
            "speaker": "Fundamentals Analyst",
            "stance": "bullish",
            "claim": "中报营收同比增长30%且净利润超预期",
            "evidence": ["营收同比增长30%", "净利润50亿元"],
        }

        metrics = tally_cluster_votes(
            claims=[c1, c2, c3, c4],
            symbol=symbol,
            trade_date=trade_date,
        )

        assert metrics["analyst_count"] == 4
        assert metrics["independent_cluster_count"] == 2
        assert metrics["bull_cluster_count"] == 2
        # Verified evidence count covers all valid evidence items across the clusters
        assert metrics["verified_evidence_count"] >= 4

    def test_unsupported_narrative_claim_excluded_from_clusters(self):
        """Claim with only narrative / no clusterable data points is unsupported and excluded."""
        symbol = "601138.SH"
        trade_date = "2026-08-22"

        valid_claim = {
            "claim_id": "INV-1",
            "speaker": "Market Analyst",
            "stance": "bullish",
            "claim": "收盘价站上22.5元",
            "evidence": ["收盘价22.5元"],
        }

        narrative_claim = {
            "claim_id": "INV-2",
            "speaker": "Retail Debater",
            "stance": "bullish",
            "claim": "主力意图明显，后市坚定看多，走势非常好看，强烈推荐",
            "evidence": ["团队实力强大", "后市可期"],
        }

        empty_ev_claim = {
            "claim_id": "INV-3",
            "speaker": "Casual Debater",
            "stance": "bearish",
            "claim": "感觉要跌",
            "evidence": [],
        }

        enriched_narrative = assign_claim_cluster(
            narrative_claim,
            symbol=symbol,
            date=trade_date,
        )
        assert enriched_narrative["cluster_status"] == "unsupported"
        assert enriched_narrative["cluster_id"] is None
        assert len(enriched_narrative["evidence_ids"]) == 0

        metrics = tally_cluster_votes(
            claims=[valid_claim, narrative_claim, empty_ev_claim],
            symbol=symbol,
            trade_date=trade_date,
        )

        assert metrics["independent_cluster_count"] == 1
        assert metrics["bull_cluster_count"] == 1
        assert metrics["bear_cluster_count"] == 0
        assert "INV-2" in metrics["unsupported_claim_ids"]
        assert "INV-3" in metrics["unsupported_claim_ids"]

    def test_same_cluster_same_direction_at_most_one_vote(self):
        """Multiple claims in the same cluster on the same direction yield at most 1 vote."""
        symbol = "601138.SH"
        trade_date = "2026-08-22"

        claims = [
            {
                "claim_id": f"INV-{i}",
                "speaker": f"Analyst {i}",
                "stance": "bullish",
                "claim": f"价格冲击观点 {i}",
                "evidence": ["收盘价22.5元", "成交量放大1.5倍"],
            }
            for i in range(1, 6)
        ]

        metrics = tally_cluster_votes(
            claims=claims,
            symbol=symbol,
            trade_date=trade_date,
        )

        assert metrics["analyst_count"] == 5
        assert metrics["independent_cluster_count"] == 1
        assert metrics["bull_cluster_count"] == 1
        assert metrics["cluster_weights"]["bull"] == 1.0


class TestPromptSemanticsRegression:
    """Prompt requirement tests for P0-4b."""

    def test_chinese_prompt_cluster_dedup_and_dav336_seven_analysts(self):
        """Chinese prompt must retain all 7 analysts individually (DAV-336) and specify cluster dedup."""
        prompt = ZH_PROMPTS["research_manager_prompt"]

        # 1. All 7 analysts must be listed
        analysts = [
            "宏观板块",
            "市场（技术面）",
            "舆情（情绪）",
            "新闻",
            "基本面",
            "主力资金",
            "量价",
        ]
        for analyst in analysts:
            assert analyst in prompt, f"Missing analyst in prompt: {analyst}"

        # 2. Cluster deduplication requirement
        assert "cluster_id" in prompt or "cluster" in prompt
        assert "analyst_count" in prompt or "人头" in prompt
        assert "独立权重" in prompt or "去重" in prompt

    def test_english_prompt_cluster_tally_replaced(self):
        """English prompt must not say 'Tally analyst verdicts and compute bull/bear ratio'."""
        prompt = EN_PROMPTS["research_manager_prompt"]
        assert "Tally analyst verdicts and compute bull/bear ratio" not in prompt
        assert "cluster" in prompt.lower()


class TestIntegrationDebateAndResearchManager:
    """Integration tests verifying debate state normalization and research manager integration."""

    def test_debate_state_claim_normalization_preserves_evidence_and_cluster_ids(self):
        from tradingagents.agents.utils.debate_utils import update_debate_state_with_payload

        state = {
            "claims": [],
            "open_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "round_messages": [],
            "count": 0,
            "claim_counter": 0,
            "history": "",
        }

        # Simulate debater output with machine block
        raw_response = (
            "立论发言正文\n\n"
            '<!-- DEBATE_STATE: {"responded_claim_ids": [], "new_claims": [{"claim": "放量突破收盘价阻力位", "evidence": ["收盘价22.5元", "成交量放大1.5倍"], "confidence": 0.85, "battlefield": "price_volume", "target_claim_ids": []}], "resolved_claim_ids": [], "unresolved_claim_ids": [], "next_focus_claim_ids": [], "round_summary": "首轮立论", "round_goal": "建立核心优势"} -->'
        )

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            speaker_label="Bull Analyst",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert len(new_state["claims"]) == 1
        claim = new_state["claims"][0]
        assert claim["claim_id"] == "INV-1"
        assert "cluster_id" in claim
        assert claim["cluster_id"] is not None
        assert "price_shock" in claim["cluster_id"]
        assert len(claim["evidence_ids"]) == 2

    @pytest.mark.asyncio
    async def test_research_manager_node_produces_claim_cluster_metrics(self):
        from unittest.mock import MagicMock
        from tradingagents.agents.managers.research_manager import create_research_manager

        async def _fake_stream(text: str):
            from types import SimpleNamespace
            yield SimpleNamespace(content=text)

        mock_llm = MagicMock()
        mock_llm.astream.return_value = _fake_stream(
            "研究总监正式报告正文\n\n"
            '<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "突破确认", "position_pct": 30, "entry": "22.5", "target": "25.0", "stop_loss": "21.0", "upside": 11.0, "downside": 6.0, "odds": 1.8, "adopted_claim_ids": ["INV-1"], "partially_adopted_claims": [], "rejected_claim_ids": [], "excluded_evidence": [], "dispute_map": []} -->'
            '\n<!-- VERDICT: {"direction": "看多", "reason": "突破确认"} -->'
        )
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        manager_node = create_research_manager(mock_llm, mock_memory)

        state = {
            "symbol": "601138.SH",
            "trade_date": "2026-08-22",
            "market_data_context": {
                "symbol": "601138.SH",
                "analysis_baseline_date": "2026-08-22",
                "trade_date": "2026-08-22",
                "source_provenance": {
                    "stock_data": {"status": "available", "as_of": "2026-08-22"},
                },
            },
            "investment_debate_state": {
                "history": "辩论记录",
                "claims": [
                    {
                        "claim_id": "INV-1",
                        "speaker": "Bull Analyst",
                        "speaker_key": "Bull",
                        "stance": "bullish",
                        "claim": "收盘价站上22.5元阻力位",
                        "evidence": ["收盘价22.5元"],
                        "confidence": 0.85,
                    }
                ],
                "unresolved_claim_ids": [],
                "count": 2,
            },
            "market_report": "市场技术报告：收盘价22.5元，突破阻力位。",
            "volume_price_report": "量价报告：放量突破。",
            "smart_money_report": "主力资金报告：主力资金净流入5.2亿元。",
            "fund_flow_consensus_guard": {
                "blocked": False,
                "direction_allowed": True,
                "status": "selected",
            },
        }

        result = await manager_node(state)
        debate_state = result["investment_debate_state"]

        assert "claim_cluster_metrics" in debate_state
        metrics = debate_state["claim_cluster_metrics"]
        assert metrics["independent_cluster_count"] >= 1
        assert debate_state["independent_cluster_count"] == metrics["independent_cluster_count"]
        assert debate_state["analyst_count"] >= 1
        assert debate_state["verified_evidence_count"] >= 1

