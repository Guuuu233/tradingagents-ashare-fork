"""D-068 / DAV-1421：双档共用一套分析师报告。

覆盖：
- VERDICT 双档机读块（directions/reasons）的解析与按档取值，含无效路径；
- 证据摘要链按档取方向前缀；
- 社交方向硬门、返修一致性签名对双档块的处理；
- 邮件服务按档提取；
- 图拆分：analysts 阶段只跑分析师+完整性门、downstream 阶段无分析师节点；
- 共享阶段一次运行 + 下游按档注入后分析师调用次数为 7（不是 14）。
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from tradingagents.agents.utils.agent_states import (
    extract_verdict,
    parse_verdict_block,
    resolve_verdict_direction,
    resolve_verdict_reason,
    verdict_label,
)
from tradingagents.agents.utils.evidence_summary import (
    build_evidence_summary,
    build_seven_source_evidence_summary,
    extract_verdict_direction,
)
from tradingagents.graph.report_quality_gate import (
    check_forbidden_social_directional_claims,
)
from tradingagents.agents.utils.price_ref_revision import conclusion_signature
from api.services.email_report_service import _extract_verdict as email_extract_verdict
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.setup import GraphSetup
from tests.test_two_stage_analyst_topology import (
    _make_graph_setup,
    _make_mock_factories,
    _TopologyRecordingWorkflow,
)


SINGLE_BLOCK = '正文。<!-- VERDICT: {"direction": "偏多", "reason": "量能走强"} -->'
DUAL_BLOCK = (
    '正文。<!-- VERDICT: {"directions": {"short": "偏多", "medium": "中性"}, '
    '"reasons": {"short": "短线量能强", "medium": "基本面平淡"}, '
    '"confidence": "高"} -->'
)
DUAL_BLOCK_EN = (
    'Body. <!-- VERDICT: {"directions": {"short": "BULLISH", "medium": "NEUTRAL"}, '
    '"reasons": {"short": "tech strong", "medium": "flat"}, "confidence": "high"} -->'
)


class TestVerdictParsing:
    def test_single_block_unchanged(self):
        assert extract_verdict(SINGLE_BLOCK) == ("偏多", "中")
        assert extract_verdict(SINGLE_BLOCK, horizon="short") == ("偏多", "中")

    def test_dual_block_per_horizon(self):
        assert extract_verdict(DUAL_BLOCK, horizon="short") == ("偏多", "高")
        assert extract_verdict(DUAL_BLOCK, horizon="medium") == ("中性", "高")

    def test_dual_block_dual_horizon_returns_dict(self):
        verdict, confidence = extract_verdict(DUAL_BLOCK, horizon="dual")
        assert verdict == {"short": "偏多", "medium": "中性"}
        assert confidence == "高"
        assert verdict_label(verdict) == "短线偏多 / 中线中性"

    def test_dual_block_missing_horizon_is_invalid(self):
        # 缺档/不借档/不默认中性：按现行无效路径返回
        assert extract_verdict(DUAL_BLOCK, horizon=None) == ("中性", "低")
        bad = '<!-- VERDICT: {"directions": {"short": "偏多"}, "reasons": {"short": "x", "medium": "y"}} -->'
        assert extract_verdict(bad, horizon="medium") == ("中性", "低")
        assert extract_verdict(bad, horizon="dual") == ("中性", "低")

    def test_dual_block_illegal_value_is_invalid(self):
        bad = '<!-- VERDICT: {"directions": {"short": "随便", "medium": "中性"}, "reasons": {"short": "x", "medium": "y"}} -->'
        assert extract_verdict(bad, horizon="short") == ("中性", "低")
        # medium 取值合法 → 按本档正常返回（无 confidence 键默认 "中"）
        assert extract_verdict(bad, horizon="medium") == ("中性", "中")

    def test_missing_block_unchanged(self):
        assert extract_verdict("没有机读块") == ("中性", "低")
        assert extract_verdict("没有机读块", horizon="short") == ("中性", "低")

    def test_resolve_helpers(self):
        payload = parse_verdict_block(DUAL_BLOCK)
        assert resolve_verdict_direction(payload, "short") == "偏多"
        assert resolve_verdict_direction(payload, "medium") == "中性"
        assert resolve_verdict_direction(payload, None) == ""
        assert resolve_verdict_reason(payload, "medium") == "基本面平淡"
        single = parse_verdict_block(SINGLE_BLOCK)
        assert resolve_verdict_direction(single, "short") == "偏多"
        assert resolve_verdict_reason(single, "medium") == "量能走强"


class TestEvidenceSummaryHorizon:
    REPORT = "营收 120 亿元，同比 +15%。\n结论倾向偏多。\n" + DUAL_BLOCK

    def test_extract_verdict_direction_dual(self):
        assert extract_verdict_direction(self.REPORT, horizon="short") == "偏多"
        assert extract_verdict_direction(self.REPORT, horizon="medium") == "中性"
        assert extract_verdict_direction(self.REPORT) == ""

    def test_extract_verdict_direction_single(self):
        rep = "营收 120 亿元。\n" + SINGLE_BLOCK
        assert extract_verdict_direction(rep, horizon="short") == "偏多"
        assert extract_verdict_direction(rep, horizon="medium") == "偏多"

    def test_seven_source_summary_per_horizon(self):
        reports = {"market_report": self.REPORT}
        short_txt = build_seven_source_evidence_summary(reports, horizon="short")
        medium_txt = build_seven_source_evidence_summary(reports, horizon="medium")
        assert "[分析师结论：偏多]" in short_txt
        assert "[分析师结论：中性]" in medium_txt
        # 未给 horizon：不借用任一档方向
        none_txt = build_seven_source_evidence_summary(reports)
        assert "偏多" not in none_txt.split("]")[0] or "分析师结论" not in none_txt


class TestGuards:
    def test_social_gate_dual_directional(self):
        assert check_forbidden_social_directional_claims("正文\n" + DUAL_BLOCK)
        neutral = '<!-- VERDICT: {"directions": {"short": "中性", "medium": "中性"}, "reasons": {"short": "x", "medium": "y"}} -->'
        assert check_forbidden_social_directional_claims("正文\n" + neutral) == []

    def test_conclusion_signature_dual(self):
        sig = conclusion_signature("正文\n" + DUAL_BLOCK)
        assert sig["verdict"] == {"directions": {"short": "偏多", "medium": "中性"}}
        single = conclusion_signature("正文\n" + SINGLE_BLOCK)
        assert single["verdict"] == {"direction": "偏多"}


class TestEmailService:
    def test_extract_verdict_dual(self):
        v = email_extract_verdict("正文 " + DUAL_BLOCK, horizon="short")
        assert v == {"direction": "偏多", "reason": "短线量能强"}
        v = email_extract_verdict("正文 " + DUAL_BLOCK, horizon="medium")
        assert v == {"direction": "中性", "reason": "基本面平淡"}
        assert email_extract_verdict("正文 " + DUAL_BLOCK) is None
        assert email_extract_verdict("正文 " + DUAL_BLOCK_EN, horizon="short")["direction"] == "看多"

    def test_extract_verdict_single(self):
        v = email_extract_verdict("正文 " + SINGLE_BLOCK, horizon="short")
        assert v["direction"] == "偏多"


class TestGraphStages:
    def _setup(self):
        return _make_graph_setup()

    def test_analyst_stage_graph_ends_after_gate(self):
        setup = self._setup()
        factories = _make_mock_factories()
        analysts = ["macro", "market", "social", "fundamentals", "news", "smart_money", "volume_price"]
        with patch("tradingagents.graph.setup._load_agent_factories", return_value=factories), \
             patch("tradingagents.graph.setup.StateGraph", _TopologyRecordingWorkflow):
            compiled = setup.setup_graph(analysts, stage="analysts")
        # 完整性门直收 END；无通往 Bull Researcher 的条件边
        gate_edges = [e for e in compiled["edges"] if e[0] == "Run Integrity Gate"]
        assert ("Run Integrity Gate", "__end__") in gate_edges or any(
            e[1] in ("END", "__end__") for e in gate_edges
        )
        assert not any(
            c[0] == "Run Integrity Gate" for c in compiled["conditional_edges"]
        )

    def test_downstream_stage_has_no_analyst_nodes(self):
        setup = self._setup()
        factories = _make_mock_factories()
        with patch("tradingagents.graph.setup._load_agent_factories", return_value=factories), \
             patch("tradingagents.graph.setup.StateGraph", _TopologyRecordingWorkflow):
            compiled = setup.setup_graph([], stage="downstream")
        assert not any(n.endswith("Analyst Done") or n.startswith("tools_") for n in compiled["nodes"])
        # START 直进完整性门
        assert any(
            e[0] == "__start__" and e[1] == "Run Integrity Gate"
            for e in compiled["edges"]
        ) or any(
            e[0] == "START" and e[1] == "Run Integrity Gate" for e in compiled["edges"]
        )

    def test_full_stage_still_requires_analysts(self):
        setup = self._setup()
        with pytest.raises(ValueError):
            setup.setup_graph([], stage="full")


class TestSharedAnalystStageRunsOnce:
    """7 位分析师在共享阶段各调用一次（不是两档各一次共 14 次）。"""

    def test_shared_stage_invokes_each_analyst_once(self):
        setup = _make_graph_setup()
        factories = _make_mock_factories()
        calls = {"count": 0}
        analysts = ["macro", "market", "social", "fundamentals", "news", "smart_money", "volume_price"]

        for key in list(factories):
            if key.startswith("create_") and "analyst" in key:
                orig = factories[key]
                def mk(o):
                    def factory(*a, **kw):
                        node = o(*a, **kw)
                        async def counting_node(state, *aa, **kk):
                            calls["count"] += 1
                            res = node(state) if callable(node) else {}
                            if asyncio.iscoroutine(res):
                                res = await res
                            return res
                        return counting_node
                    return factory
                factories[key] = mk(orig)

        with patch("tradingagents.graph.setup._load_agent_factories", return_value=factories):
            compiled = setup.setup_graph(analysts, stage="analysts")

        initial_state = {
            "messages": [HumanMessage(content="start")],
            "company_of_interest": "600519",
            "trade_date": "2026-07-31",
            "macro_report": "", "market_report": "", "sentiment_report": "",
            "fundamentals_report": "", "news_report": "", "smart_money_report": "",
            "volume_price_report": "",
            "investment_debate_state": {"count": 0, "current_speaker": "", "history": ""},
            "risk_debate_state": {"count": 0, "latest_speaker": "", "history": ""},
            "risk_feedback_state": {"retry_count": 0, "max_retries": 1, "revision_required": False},
        }
        asyncio.run(compiled.ainvoke(initial_state))
        assert calls["count"] == 7
