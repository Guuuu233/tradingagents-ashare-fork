"""Tests for social downstream deterministic gates (Task 12).

Covers:
- research_manager: structured social context injection & direction_allowed guard
- evidence_verifier: social failure ledger -> unavailable sources & insufficient score guard
- report_quality_gate: indeterminate / unavailable markers for insufficient social data
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator,
    STATUS_CONTRADICTED,
    STATUS_SOURCE_UNAVAILABLE,
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
)
from tradingagents.graph.report_quality_gate import (
    evaluate_role_depth,
    evaluate_social_depth,
)


# ============================================================================
# A. research_manager Tests
# ============================================================================

def _make_valid_debate_state():
    round_messages = [
        {"message_index": 1, "debate_round": 1, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": [], "target_claim_ids": [], "new_claim_ids": ["INV-1"]},
        {"message_index": 2, "debate_round": 1, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-1"], "target_claim_ids": ["INV-1"], "new_claim_ids": ["INV-2"]},
        {"message_index": 3, "debate_round": 2, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-2"], "target_claim_ids": ["INV-2"], "new_claim_ids": ["INV-3"]},
        {"message_index": 4, "debate_round": 2, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-3"], "target_claim_ids": ["INV-3"], "new_claim_ids": ["INV-4"]},
        {"message_index": 5, "debate_round": 3, "speaker": "Bull Analyst", "speaker_key": "Bull", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-4"], "target_claim_ids": ["INV-4"], "new_claim_ids": ["INV-5"]},
        {"message_index": 6, "debate_round": 3, "speaker": "Bear Analyst", "speaker_key": "Bear", "parse_status": "valid", "accepted": True, "responded_claim_ids": ["INV-5"], "target_claim_ids": ["INV-5"], "new_claim_ids": ["INV-6"]},
    ]
    claims = [
        {"claim_id": "INV-1", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "多头观点1", "evidence": ["技术面突破"], "confidence": 0.85},
        {"claim_id": "INV-2", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "空头观点1", "evidence": ["MACD背离"], "confidence": 0.80},
        {"claim_id": "INV-3", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "多头观点2", "evidence": ["基本面好转"], "confidence": 0.90},
        {"claim_id": "INV-4", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "空头观点2", "evidence": ["行业增速下滑"], "confidence": 0.75},
        {"claim_id": "INV-5", "speaker": "Bull Analyst", "speaker_key": "Bull", "stance": "bullish", "claim": "多头观点3", "evidence": ["资金净流入"], "confidence": 0.88},
        {"claim_id": "INV-6", "speaker": "Bear Analyst", "speaker_key": "Bear", "stance": "bearish", "claim": "空头观点3", "evidence": ["新闻偏空"], "confidence": 0.78},
    ]
    return {
        "history": "辩论完整历史",
        "bear_history": "",
        "bull_history": "",
        "current_speaker": "Bear",
        "current_response": "",
        "count": 6,
        "claims": claims,
        "round_messages": round_messages,
        "focus_claim_ids": ["INV-6"],
        "open_claim_ids": ["INV-6"],
        "resolved_claim_ids": ["INV-1", "INV-2"],
        "unresolved_claim_ids": ["INV-5", "INV-6"],
        "round_summary": "第3轮收官",
        "round_goal": "收敛结论",
        "claim_counter": 6,
    }


def test_research_manager_injects_structured_social_context():
    """research_manager prompt must inject compact structured social status."""
    captured_prompts = []

    class FakeLLM:
        async def astream(self, prompt):
            captured_prompts.append(prompt)
            yield MagicMock(content="<!-- VERDICT: {\"direction\": \"中性\", \"reason\": \"测试\"} -->")

    fake_memory = MagicMock()
    fake_memory.get_memories.return_value = []

    manager_node = create_research_manager(FakeLLM(), fake_memory)

    state = {
        "trade_date": "2026-08-26",
        "market_report": "市场技术面正常",
        "sentiment_report": "社交媒体情绪分析正常",
        "news_report": "新闻正常",
        "fundamentals_report": "基本面正常",
        "macro_report": "宏观正常",
        "smart_money_report": "主力资金正常",
        "volume_price_report": "量价正常",
        "fund_flow_consensus_guard": {"blocked": False, "direction_allowed": True, "status": "passed"},
        "market_data_context": {"analysis_baseline_date": "2026-08-26"},
        "social_data_context": {
            "status": "available",
            "mode": "active",
            "requested_as_of": "2026-08-26",
            "direction_allowed": True,
            "reason_codes": [],
            "bundle": {
                "bundle_id": "sha256:socialbundle123",
                "direction_allowed": True,
            },
            "data_failure_ledger": [],
        },
        "investment_debate_state": _make_valid_debate_state(),
    }

    res = asyncio.run(manager_node(state))
    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]

    # Verify structured social context is injected
    assert "social_data_context" in prompt or "社交数据状态" in prompt
    assert "mode=active" in prompt or "active" in prompt
    assert "status=available" in prompt or "available" in prompt
    assert "direction_allowed=True" in prompt or "direction_allowed=true" in prompt or "允许方向判断" in prompt
    assert "sha256:socialbundle123" in prompt


def test_research_manager_direction_not_allowed_guard():
    """When direction_allowed=False, research_manager prompt must explicitly forbid using social score for direction."""
    captured_prompts = []

    class FakeLLM:
        async def astream(self, prompt):
            captured_prompts.append(prompt)
            yield MagicMock(content="<!-- VERDICT: {\"direction\": \"中性\", \"reason\": \"测试\"} -->")

    fake_memory = MagicMock()
    fake_memory.get_memories.return_value = []

    manager_node = create_research_manager(FakeLLM(), fake_memory)

    state = {
        "trade_date": "2026-08-26",
        "market_report": "市场技术面正常",
        "sentiment_report": "社交方向不可判断，数据不足",
        "news_report": "新闻正常",
        "fundamentals_report": "基本面正常",
        "macro_report": "宏观正常",
        "smart_money_report": "主力资金正常",
        "volume_price_report": "量价正常",
        "fund_flow_consensus_guard": {"blocked": False, "direction_allowed": True, "status": "passed"},
        "market_data_context": {"analysis_baseline_date": "2026-08-26"},
        "social_data_context": {
            "status": "insufficient",
            "mode": "active",
            "requested_as_of": "2026-08-26",
            "direction_allowed": False,
            "reason_codes": ["social_insufficient_coverage"],
            "bundle": {
                "bundle_id": "sha256:insufficientbundle",
                "direction_allowed": False,
            },
            "data_failure_ledger": [],
        },
        "investment_debate_state": _make_valid_debate_state(),
    }

    res = asyncio.run(manager_node(state))
    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]

    # Must contain explicit prohibition on social directional inference
    assert "direction_allowed=False" in prompt or "direction_allowed=false" in prompt or "禁止" in prompt
    assert any(phrase in prompt for phrase in ("严禁将社交", "禁止把社交", "不得把社交", "禁止将社交分数", "严禁将社交分数"))


# ============================================================================
# B. evidence_verifier Tests
# ============================================================================

def test_evidence_verifier_social_ledger_unavailable_sources():
    """Evidence referring to failed social sources must be flagged as source_unavailable fatal hallucination."""
    evaluator = EvidenceFactualTruthEvaluator()

    market_data_context = {"analysis_baseline_date": "2026-08-26"}
    social_data_context = {
        "status": "failed",
        "data_failure_ledger": [
            {
                "source": "social_archive",
                "status": "failed",
                "reason": "social_archive_missing",
                "gap": "【数据获取失败】social_archive：social_archive_missing",
            }
        ]
    }
    seven_reports = {
        "sentiment_report": "社交媒体归档不可用，数据获取失败。",
    }

    res = evaluator.evaluate_single_evidence(
        raw_evidence="根据 social_archive 社交舆情数据，散户热度高涨",
        seven_reports=seven_reports,
        market_data_context=market_data_context,
        social_data_context=social_data_context,
    )

    assert res["status"] == STATUS_SOURCE_UNAVAILABLE
    assert res["is_fatal"] is True
    assert "social_archive" in str(res["matched_source"])


def test_evidence_verifier_insufficient_social_score_cannot_be_verified():
    """When direction_allowed=False or social is insufficient, social score cannot be verified as true fact."""
    evaluator = EvidenceFactualTruthEvaluator()

    market_data_context = {"analysis_baseline_date": "2026-08-26"}
    social_data_context = {
        "status": "insufficient",
        "direction_allowed": False,
        "reason_codes": ["social_insufficient_coverage"],
        "bundle": {
            "direction_allowed": False,
            "social_sentiment": {"score": None, "label": "insufficient"},
        }
    }
    seven_reports = {
        "sentiment_report": "社交舆情样本量不足（发帖数2），社交方向不可判断，情绪得分无法有效计算。",
    }

    # Claim asserts a directional social sentiment score
    res = evaluator.evaluate_single_evidence(
        raw_evidence="社交舆情情绪得分达到0.85，散户极度看多",
        seven_reports=seven_reports,
        market_data_context=market_data_context,
        social_data_context=social_data_context,
    )

    assert res["status"] in (STATUS_UNSUPPORTED, STATUS_SOURCE_UNAVAILABLE, STATUS_CONTRADICTED)
    assert res["status"] != STATUS_VERIFIED


def test_evidence_verifier_mode_status_separation_l4():
    """L4: Verifier must separate mode from status and reject directional claims across disabled/shadow modes."""
    evaluator = EvidenceFactualTruthEvaluator()
    market_data_context = {"analysis_baseline_date": "2026-08-26"}
    seven_reports = {
        "sentiment_report": "舆情数据处于影子模式，未开启方向推断。",
    }

    # Shadow mode with valid status 'available' but direction_allowed=False
    social_data_context_shadow = {
        "mode": "shadow",
        "status": "available",
        "direction_allowed": False,
        "bundle": {
            "direction_allowed": False,
            "social_sentiment": {"score": 0.8, "label": "bullish"},
        },
    }

    res_shadow = evaluator.evaluate_single_evidence(
        raw_evidence="社交舆情情绪得分达到0.80，散户看多",
        seven_reports=seven_reports,
        market_data_context=market_data_context,
        social_data_context=social_data_context_shadow,
    )
    assert res_shadow["status"] == STATUS_UNSUPPORTED

    # Disabled mode with status 'not_applicable'
    social_data_context_disabled = {
        "mode": "disabled",
        "status": "not_applicable",
        "direction_allowed": False,
    }

    res_disabled = evaluator.evaluate_single_evidence(
        raw_evidence="社交舆情情绪得分高涨，多头狂热",
        seven_reports=seven_reports,
        market_data_context=market_data_context,
        social_data_context=social_data_context_disabled,
    )
    assert res_disabled["status"] == STATUS_UNSUPPORTED



# ============================================================================
# C. report_quality_gate Tests
# ============================================================================

def test_quality_gate_social_depth_legacy_disabled_and_shadow_modes():
    """In disabled or shadow modes (legacy proxy), sentiment report does not require '不可判断' to pass."""
    legacy_report = "市场情绪整体乐观，投资者情绪偏多，交投较为活跃。"

    # 1. Disabled mode
    passed_dis, score_dis, failed_dis, _ = evaluate_social_depth(
        legacy_report,
        social_data_context={"mode": "disabled", "status": "not_applicable"},
    )
    assert passed_dis is True
    assert score_dis >= 0.75
    assert not failed_dis

    # 2. Shadow mode
    passed_sh, score_sh, failed_sh, _ = evaluate_social_depth(
        legacy_report,
        social_data_context={"mode": "shadow", "status": "available"},
    )
    assert passed_sh is True
    assert score_sh >= 0.75
    assert not failed_sh


def test_quality_gate_social_depth_on_insufficient_data():
    """When social data is active & insufficient, quality gate requires indeterminate markers without forcing direction metrics."""
    social_data_context = {
        "mode": "active",
        "status": "insufficient",
        "direction_allowed": False,
        "reason_codes": ["social_insufficient_coverage"],
    }

    # Valid report acknowledging data insufficiency
    valid_report = (
        "【数据状态】社交媒体数据样本不足（有效发帖2篇，评论15条），覆盖率未达最低门槛。\n"
        "【社交方向】社交方向不可判断，数据不足。\n"
        "【市场关注度】涨停池连板高度4板，短线资金活跃度尚可。\n"
        "【反身性分析】由于样本不足，暂不作散户情绪极端性推断，保持中性观察。"
    )

    passed, score, failed_dims, reason_str = evaluate_social_depth(
        valid_report,
        social_data_context=social_data_context,
    )
    assert passed is True
    assert score >= 0.75
    assert not failed_dims

    # Invalid report claiming strong directional sentiment without noting insufficiency
    invalid_report = (
        "散户极度狂热，发帖全部看多，情绪高潮，建议立即买入！"
    )

    passed_inv, score_inv, failed_dims_inv, reason_str_inv = evaluate_social_depth(
        invalid_report,
        social_data_context=social_data_context,
    )
    assert passed_inv is False
    assert "indeterminate_or_missing_marker" in failed_dims_inv or "insufficient_acknowledged" in failed_dims_inv


def test_quality_gate_evaluate_role_depth_dispatch_social():
    """evaluate_role_depth dispatches correctly to social / sentiment depth evaluator."""
    social_data_context = {
        "mode": "active",
        "status": "available",
        "direction_allowed": True,
    }
    report = (
        "【数据状态】数据覆盖充足，小红书与抖音共获取有效帖子45篇，评论180条。\n"
        "【社交观点】看多比例65%，主要集中于新产品放量预期，看空比例15%。\n"
        "【社交热度】发帖量环比增长30%，互动活跃，但遵循热度不等于利多原则。\n"
        "【市场关注度】涨停池连板梯队处于3板分歧阶段，雪球热度排名第12位。\n"
        "【反身性推演】情绪处于发酵加速阶段，未见极端贪婪高潮，短期具备持续性。"
    )
    state = {"social_data_context": social_data_context}

    passed, score, failed_dims, reason_str = evaluate_role_depth(
        "social_media_analyst",
        report,
        state=state,
    )
    assert passed is True
    assert score >= 0.75


def test_apply_report_quality_gate_includes_sentiment_report():
    """apply_report_quality_gate evaluates sentiment_report in depth check and records failures."""
    from tradingagents.graph.report_quality_gate import apply_report_quality_gate

    # State with failing active social report (missing indeterminate marker when insufficient)
    state = {
        "macro_report": "宏观经济稳步复苏，货币政策传导机制畅通，外盘联动平稳，全球核心指数保持上行。",
        "fundamentals_report": "公司营收同比增长15%，净利润毛利率稳定，产业链上游议价权强，原材料涨价敏感性测算可控。",
        "news_report": "根据公告披露，公司近期签订重大战略合作协议，直接提振主营业务，向上下游产业链传导，预计在下季度财报节点兑现。",
        "volume_price_report": "近5日成交量放量突破均线阻力位，量价配合良好，多头供需占优，若后续站稳30元防守位则趋势确认，结合宏观与情绪面共振。",
        "sentiment_report": "散户极度狂热，发帖全部看多，情绪高潮！",
        "social_data_context": {
            "mode": "active",
            "status": "insufficient",
            "direction_allowed": False,
            "reason_codes": ["social_insufficient_coverage"],
        },
        "market_data_context": {
            "source_provenance": {"global_indices": {"status": "available"}},
            "data_failure_ledger": [],
        },
    }

    apply_report_quality_gate(state)
    ledger = state["market_data_context"]["data_failure_ledger"]
    sentiment_failures = [e for e in ledger if isinstance(e, dict) and e.get("role") in ("sentiment", "social")]
    assert len(sentiment_failures) >= 1
    assert sentiment_failures[0]["status"] == "failed"

