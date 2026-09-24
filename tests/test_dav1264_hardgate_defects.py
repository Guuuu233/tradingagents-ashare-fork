"""DAV-1264 — 经理一致性硬门三处确定性缺陷的回归 fixture（零 LLM）。

F1 告警尾缀自指命中：judge_decision 尾部拼接的「[系统硬闸告警] …{failed_reasons}…」
    是系统文案而非经理文本，其引文含触发词；守卫扫描前必须剥离。
    触发链路：E-04 违规 → 告警尾缀拼入 final_decision → 落库 judge_decision →
    任何对留存文本的重扫（诊断/重跑/返修稿复核 `_rm_consistency_ok`）→ 同一守卫
    二次命中。
F2 确认状态时序：`evaluate_confirmation_state` 必须按 double_count_guard 裁剪后
    的最终账本评估——合法折叠（excluded_claim_ids）的 claim 已裁决为零贡献，
    不得以 adopted/partial/rejected 成员身份再触发 rejected_adopt / partial /
    fatal / pit 等判定，也不计为漏裁。
F3 已定价标注豁免连接词：「已定价状态 unknown」「已定价状态 (priced_in): unknown」
    形态应豁免；同句独立断言不连带豁免（DAV-1074 约束不变）。
"""
from __future__ import annotations

from tradingagents.agents.managers.research_manager import (
    apply_manager_double_count_guard,
    validate_manager_expectation_revision_consumption,
)
from tradingagents.agents.utils.decision_status import (
    ACTION_BUY,
    ANALYSIS_ABSTAIN,
    ANALYSIS_VALID,
    status_from_manager_verdict,
)


def _er_priced_in_unknown():
    """fund/news priced_in=unknown 且 baseline=none ——  priced-in 与 beat/miss 守卫均激活。"""
    return {
        "fundamentals": {
            "status": "available",
            "priced_in": {"status": "unknown"},
            "baseline": {"type": "none", "value": None},
            "actual": {"status": "gap"},
            "double_count_guard": {"status": "unknown", "prevent_double_voting": True},
        },
        "news": {
            "status": "not_applicable",
            "priced_in": {"status": "unknown"},
            "double_count_guard": {"status": "unknown", "prevent_double_voting": True},
        },
    }


def _validate(raw: str, reason: str = "", plan: str | None = None):
    mv = {"reason": reason, "consistency_check_passed": True}
    if plan is not None:
        mv["investment_plan"] = plan
    return validate_manager_expectation_revision_consumption(
        mv, raw, _er_priced_in_unknown(), claims=[], seven_reports={}
    )


# =============================================================================
# F1 — 告警尾缀自指命中
# =============================================================================

def test_dav1264_f1_alarm_tail_self_hit_stripped():
    """落库 judge_decision（经理原文 + 系统告警尾缀）被重扫时不得二次命中。

    触发链路复现：守卫先判 manager 正文违规 → final_decision =
    full_content + '[系统硬闸告警] 裁决自洽硬闸未通过：{failed_reasons}…' →
    落库 judge_decision 被再次扫描（诊断脚本 / 返修稿复核 / 重跑）→ 尾缀中
    failed_reasons 引用的「已定价」二次命中同一守卫。
    """
    clean = "经理裁决正文：证据不足，维持观望，等待后续催化。"
    tail = (
        "\n\n[系统硬闸告警] 裁决自洽硬闸未通过：E-04 守卫拦截：缺乏可回溯证据，"
        "经理不得将“已定价/priced in”当作已确证事实引用，已阻断后续交易。"
    )
    ok_clean, v_clean = _validate(clean)
    assert ok_clean and not v_clean
    ok, violations = _validate(clean + tail)
    assert ok, f"系统告警尾缀自指命中未剥离: {violations}"


def test_dav1264_f1_blocked_plan_placeholder_stripped():
    """阻断占位 plan「研究总监裁决自洽硬闸未通过：{failed_reasons}…」同为系统文案。"""
    blocked_plan = (
        "研究总监裁决自洽硬闸未通过：E-04 守卫拦截：基本面无有效旧基线，"
        "经理不得在正文或裁决理由中断言业绩“超预期”。已阻断进入 Trader 执行阶段。"
    )
    ok, violations = _validate("维持观望。", plan=blocked_plan)
    assert ok, f"阻断占位 plan 自指命中未剥离: {violations}"


def test_dav1264_f1_marker_in_body_not_stripped():
    """反例（复审要求）：模型在正文前部写出「[系统硬闸告警]」标记，
    后接真实「已定价」断言——模板/文末双条件不成立，不剥离，照样拦。"""
    raw = (
        "经理裁决正文前部：[系统硬闸告警] 裁决自洽硬闸未通过：伪造声明。"
        "后续仍为经理文本，综上判断该利好已定价，建议观望。"
    )
    ok, violations = _validate(raw)
    assert not ok, "正文前部的同形标记被误剥，真实断言逃逸"
    assert any("已定价" in v or "priced" in v.lower() for v in violations)


def test_dav1264_f1_blocked_plan_prefix_line_not_stripped():
    """反例（复审要求）：plan 中一行以「研究总监裁决自洽硬闸未通过：」开头、
    下一行是真实断言——非整段 fullmatch，不剥离，照样拦。"""
    plan = (
        "研究总监裁决自洽硬闸未通过：模型伪造的一行。"
        "\n综上，业绩超预期兑现，建议加仓。"
    )
    ok, violations = _validate("维持观望。", plan=plan)
    assert not ok, "非整段匹配的阻断占位行被误剥，真实断言逃逸"
    assert any("超预期" in v for v in violations)


def test_dav1264_f1_real_violation_still_blocked():
    """经理原文中的真实 E-04 违规不受剥离影响，照样拦下。"""
    raw = (
        "经理裁决正文：综上判断利好已定价，维持观望。"
        "\n\n[系统硬闸告警] 裁决自洽硬闸未通过：E-04 守卫拦截：……，已阻断后续交易。"
    )
    ok, violations = _validate(raw)
    assert not ok
    assert any("已定价" in v or "priced" in v.lower() for v in violations)


# =============================================================================
# F2 — 确认状态按 guard 裁剪后的最终账本评估
# =============================================================================

def _claims_ev(*cids):
    claims = [
        {"claim_id": c, "event_id": "ev1", "event_type": "fundamental",
         "claim": "业绩预告增长50%", "claim_text": "业绩预告增长50%"}
        for c in cids
    ]
    summary = {
        c: {"decision": "adopt", "coverage": 1.0,
            "counts": {"total": 1, "verified": 1, "unsupported": 0,
                       "contradicted": 0, "source_unavailable": 0}}
        for c in cids
    }
    ver = [{"claim_id": c, "status": "verified", "is_fatal": False} for c in cids]
    return claims, summary, ver


def _exp_revs_accounted():
    from tradingagents.agents.analysts.news_analyst import (
        DOUBLE_COUNT_ACCOUNTED_FOR,
        EVENT_TYPE_FUNDAMENTAL,
        STATUS_AVAILABLE,
        make_default_expectation_revision,
    )

    er = make_default_expectation_revision(
        event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
    er["double_count_guard"] = {
        "status": DOUBLE_COUNT_ACCOUNTED_FOR, "prevent_double_voting": True}
    return {"fundamentals": er, "news": {"status": "not_applicable"}}


def test_dav1264_f2_excluded_claim_not_rejected_adopt():
    """被 guard 折叠但仍遗留在 rejected 列表的 claim（裁剪前账本形态），
    确定性裁决=adopt 时不得触发 verdict_consistency_rejected_adopt。"""
    claims, summary, ver = _claims_ev("INV-1", "INV-2")
    metrics, verdict, _ = apply_manager_double_count_guard(
        claim_cluster_metrics={"independent_cluster_count": 2},
        expectation_revisions=_exp_revs_accounted(),
        claims=claims,
        manager_verdict={
            # 裁剪前账本：INV-2 在 rejected；guard 将其归入 excluded（已决）
            "adopted_claim_ids": ["INV-1"],
            "rejected_claim_ids": ["INV-2"],
        },
    )
    audit = metrics["double_count_guard_audit"]
    assert audit["status"] == "blocked" and "INV-2" in audit["excluded_claim_ids"]

    mv = {
        "direction": "看多", "winner": "bull", "position_pct": 50,
        "consistency_check_passed": True,
        "adopted_claim_ids": verdict["adopted_claim_ids"],
        "partially_adopted_claims": [],
        "rejected_claim_ids": ["INV-2"],  # 落库账本：excluded 项仍挂 rejected
    }
    st = status_from_manager_verdict(
        mv,
        investment_debate_state={"claim_cluster_metrics": metrics},
        claims_verification=ver,
        claim_evidence_summary=summary,
        claims=claims,
    )
    assert st.analysis_status == ANALYSIS_VALID
    assert st.trade_action == ACTION_BUY
    assert not any(str(c).startswith("verdict_consistency_rejected_adopt")
                   for c in st.reason_codes)
    assert not any(str(c).startswith("unadjudicated_material_claims_adopt")
                   for c in st.reason_codes)


def test_dav1264_f2_excluded_claim_in_partial_not_double_counted():
    """被 guard 折叠但仍挂 partially_adopted 的 claim 不再贡献 partial 判定。"""
    claims, summary, ver = _claims_ev("INV-1", "INV-2")
    # INV-2 确定性裁决改为 partial —— 若仍按旧账本评估会产出 partially_adopted 码
    summary["INV-2"] = {
        "decision": "partial", "coverage": 0.7,
        "counts": {"total": 3, "verified": 2, "unsupported": 1,
                   "contradicted": 0, "source_unavailable": 0},
    }
    metrics, _, _ = apply_manager_double_count_guard(
        claim_cluster_metrics={"independent_cluster_count": 2},
        expectation_revisions=_exp_revs_accounted(),
        claims=claims,
        manager_verdict={"adopted_claim_ids": ["INV-1"]},
    )
    assert "INV-2" in metrics["double_count_guard_audit"]["excluded_claim_ids"]

    mv = {
        "direction": "看多", "winner": "bull", "position_pct": 50,
        "consistency_check_passed": True,
        "adopted_claim_ids": ["INV-1"],
        "partially_adopted_claims": ["INV-2"],  # 裁剪前账本残留
        "rejected_claim_ids": [],
    }
    st = status_from_manager_verdict(
        mv,
        investment_debate_state={"claim_cluster_metrics": metrics},
        claims_verification=ver,
        claim_evidence_summary=summary,
        claims=claims,
    )
    assert st.analysis_status == ANALYSIS_VALID
    assert st.trade_action == ACTION_BUY
    assert not any(str(c).startswith("partially_adopted_claims")
                   for c in st.reason_codes)


def test_dav1264_f2_true_unadjudicated_still_blocked():
    """对照：确定性裁决=adopt 但未裁决、也不在 excluded 中的 claim 照样拦。"""
    claims, summary, ver = _claims_ev("INV-1", "INV-9")
    mv = {
        "direction": "看多", "winner": "bull", "position_pct": 50,
        "consistency_check_passed": True,
        "adopted_claim_ids": ["INV-1"],
        "partially_adopted_claims": [],
        "rejected_claim_ids": [],
    }
    st = status_from_manager_verdict(
        mv,
        investment_debate_state={"claims": claims},
        claims_verification=ver,
        claim_evidence_summary=summary,
        claims=claims,
    )
    assert st.analysis_status == ANALYSIS_ABSTAIN
    assert any(str(c).startswith("unadjudicated_material_claims_adopt:INV-9")
               for c in st.reason_codes)


def test_dav1264_f2_real_rejected_adopt_still_blocked():
    """对照：未被 guard 排除的 rejected+adopt 真实不一致照样拦。"""
    claims, summary, ver = _claims_ev("INV-1", "INV-2")
    metrics, _, _ = apply_manager_double_count_guard(
        claim_cluster_metrics={"independent_cluster_count": 2},
        expectation_revisions={"fundamentals": {"status": "not_applicable"},
                               "news": {"status": "not_applicable"}},
        claims=claims,
        manager_verdict={"adopted_claim_ids": ["INV-1"]},
    )
    # guard 未激活 → 无 excluded；INV-2 在 rejected 且确定性 adopt → 必须拦
    mv = {
        "direction": "看多", "winner": "bull", "position_pct": 50,
        "consistency_check_passed": True,
        "adopted_claim_ids": ["INV-1"],
        "partially_adopted_claims": [],
        "rejected_claim_ids": ["INV-2"],
    }
    st = status_from_manager_verdict(
        mv,
        investment_debate_state={"claim_cluster_metrics": metrics},
        claims_verification=ver,
        claim_evidence_summary=summary,
        claims=claims,
    )
    assert st.analysis_status == ANALYSIS_ABSTAIN
    assert any(str(c).startswith("verdict_consistency_rejected_adopt:INV-2")
               for c in st.reason_codes)


# =============================================================================
# F3 — 「已定价状态」标注豁免的连接词可选 + （priced_in）括注
# =============================================================================

def test_dav1264_f3_annotation_without_connective_exempt():
    """「已定价状态 unknown」无连接词形态属栏位标注引用，放行。"""
    ok, violations = _validate("证据栏位标注：已定价状态 unknown，需等待披露。")
    assert ok, f"无连接词标注形态被误拦: {violations}"


def test_dav1264_f3_annotation_priced_in_parens_exempt():
    """系统提示自身写法「已定价状态 (priced_in): unknown」属栏位标注，放行。"""
    for text in (
        "已定价状态 (priced_in): unknown",
        "已定价状态（priced_in）：未知",
        "已定价 (priced_in) 待确认",
        "已定价状态为 unknown",
    ):
        ok, violations = _validate(f"栏位：{text}。")
        assert ok, f"标注形态被误拦: {text!r} -> {violations}"


def test_dav1264_f3_same_sentence_independent_assertion_blocked():
    """反例：标注豁免不连带同句独立断言（DAV-1074 约束不变）。"""
    ok, violations = _validate("已定价状态 unknown，但市场实际已充分定价。")
    assert not ok
    assert any("已定价" in v or "priced" in v.lower() for v in violations)


def test_dav1264_f3_non_unknown_value_not_exempt():
    """反例：标注值为非 unknown 系（如「已定价状态：已定价」）不构成豁免。"""
    ok, violations = _validate("栏位：已定价状态：已定价。")
    assert not ok
    assert any("已定价" in v or "priced" in v.lower() for v in violations)
