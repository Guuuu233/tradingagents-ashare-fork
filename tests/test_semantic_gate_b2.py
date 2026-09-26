"""DAV-1193 (1141-B2): Semantic Coverage Gate 正式接入 claim decision。

覆盖本卡新增阻塞性必修与正式门禁契约：

🟡-1 grounded causal 假阳修复（RED）：
- “扣非增20%印证高胜率”：报告仅同段出现“高胜率…扣非高增长…”但无同句
  因果关系陈述 → 不得 grounded；
- “公司回购70亿元构筑铁底”：verified 只有回购事实、没有“构筑铁底”关系
  → 不得 strict supported；
- 前提锚点只取该 proposition 自身，不得并入其它命题名词。

🟡-2 verifiable==0 / non_factual_only 正式语义（RED）：
- “后市值得关注，静待验证” → semantic_coverage=None +
  semantic_decision=non_factual_only，不得伪装 1.0/adopt；
- “建议警惕商誉减值风险” → 降档可审计命题，不得全归 non_factual；
- 纯修辞 claim 进 adopted/partial → consistency fail；进 rejected → PASS。

三、正式 decision/gate：
- legacy evidence=100% 但 semantic_decision=reject 的 claim 被全额采纳
  → consistency fail（000895 INV-2 型缺口正式闭合）；
- semantic reject 进 partially_adopted → fail（不偷升）；
- semantic adopt + legacy adopt → PASS；
- decision_status 下游消费正式 semantic decision（ rejected 审计不再把
  semantic-reject 误判为 deterministic adopt）。
"""
from __future__ import annotations

from tradingagents.agents.utils.decision_status import (
    CONFIRM_CONFIRMED,
    CONFIRM_UNRESOLVED,
    evaluate_confirmation_state,
)
from tradingagents.agents.utils.evidence_verifier import (
    STATUS_VERIFIED,
    aggregate_claim_evidence,
    audit_claim_semantic_coverage,
    decompose_claim_propositions,
    extract_and_validate_manager_verdict,
)

INV2_CLAIM = "食安利空已定价超6天，Q1扣非增20%与高股息构筑估值底"
INV2_VERIFIED = [
    "2026Q1扣非净利润同比增长20.24%",
    "高股息分红方案，账面现金56.97亿元",
]


# ────────────────────────── 🟡-1 grounded causal 假阳修复 ──────────────────

def test_red1_paragraph_cooccurrence_not_grounded():
    """RED：报告仅同段出现“高胜率…扣非高增长…”但无同句因果关系 → 不得 grounded。"""
    res = audit_claim_semantic_coverage(
        "扣非增20%印证高胜率",
        verified_evidence=["扣非净利润同比增长20%"],
        report_fields={
            "sentiment_report": "该股具备高胜率特征，赔率俱佳。扣非增长20%，业绩持续向好。",
        },
        claim_id="INV-R1",
    )
    causal = [p for p in res["proposition_audit"] if p["kind"] == "interpretation_causal"]
    assert causal, "claim 应拆出 interpretation_causal 命题"
    assert causal[0]["support_status"] == "unsupported"
    assert causal[0]["support_kind"] != "grounded_interpretation"
    assert res["semantic_decision"] != "adopt"


def test_red2_premise_fact_only_not_strict_supported():
    """RED：verified 只有回购事实、没有“构筑铁底”关系 → 不得 strict supported。"""
    res = audit_claim_semantic_coverage(
        "公司回购70亿元构筑铁底",
        verified_evidence=["公司累计回购70亿元，彰显信心"],
        report_fields={"sentiment_report": "公司累计回购70亿元。"},
        claim_id="INV-R2",
    )
    causal = [p for p in res["proposition_audit"] if p["kind"] == "interpretation_causal"]
    assert causal
    # “回购”是前提名词不是关系谓词——strict 不得因前提名词命中判 supported
    assert causal[0]["support_status"] == "unsupported"
    assert causal[0]["support_kind"] == "none"
    assert res["semantic_decision"] == "reject"


def test_grounded_positive_same_sentence_relation_kept():
    """GREEN 保留：同句关系陈述 + 前提链 verified → grounded_interpretation。"""
    res = audit_claim_semantic_coverage(
        "Q1扣非增20%与回购70亿构筑估值底",
        verified_evidence=["2026Q1扣非净利润同比增长20.24%，派现70亿元"],
        report_fields={"sentiment_report": "扣非增长20%与70亿回购构筑估值底，彰显管理层信心。"},
    )
    causal = [p for p in res["proposition_audit"] if p["kind"] == "interpretation_causal"]
    assert causal[0]["support_status"] == "supported_available"
    assert causal[0]["support_kind"] == "grounded_interpretation"
    assert res["semantic_decision"] == "adopt"


def test_grounded_anchor_not_borrowed_from_sibling_propositions():
    """前提锚点只取该 proposition 自身：其它子句的名词不得并入。

    clause1 causal 自身锚点={扣非,20}；clause2 事实命题贡献名词「订单」。
    报告关系句只有「高胜率…订单…」——若锚点混入 clause2 名词则误 grounded；
    本卡收紧后同句必须有该命题自身锚点（扣非/20）才成立。
    """
    res = audit_claim_semantic_coverage(
        "扣非增20%印证高胜率，订单流失30%",
        verified_evidence=["扣非净利润同比增长20%", "订单流失30%"],
        report_fields={
            "sentiment_report": "该股高胜率与订单流失显著相关。扣非增长20%，业绩向好。",
        },
    )
    causal = [
        p for p in res["proposition_audit"]
        if p["kind"] == "interpretation_causal" and "高胜率" in p["text"]
    ]
    assert causal
    assert causal[0]["support_status"] == "unsupported"
    assert causal[0]["support_kind"] != "grounded_interpretation"


# ────────────────────── 🟡-2 verifiable==0 / non_factual_only ──────────────

def test_red_pure_rhetoric_non_factual_only_no_adopt():
    """RED：“后市值得关注，静待验证” → non_factual_only，coverage=None。"""
    res = audit_claim_semantic_coverage(
        "后市值得关注，静待验证", verified_evidence=[], report_fields={}
    )
    assert res["semantic_counts"]["verifiable"] == 0
    assert res["semantic_coverage"] is None, "verifiable==0 不得伪装 coverage=1.0"
    assert res["semantic_decision"] == "non_factual_only"
    assert res["semantic_partial_origin"] == "non_factual_only"
    assert res["semantic_decision_preview"] == "non_factual_only"


def test_red_norm_risk_warning_not_non_factual():
    """RED：“建议警惕商誉减值风险” → 降档可审计命题，不得全归 non_factual。"""
    kinds = [p["type"] for p in decompose_claim_propositions("建议警惕商誉减值风险")]
    assert "non_factual_or_normative" not in kinds
    res = audit_claim_semantic_coverage("建议警惕商誉减值风险", verified_evidence=[], report_fields={})
    assert res["semantic_counts"]["verifiable"] >= 1
    assert res["semantic_coverage"] is not None
    assert res["semantic_decision"] != "non_factual_only"
    assert res["semantic_decision"] != "adopt"


def test_non_factual_claim_in_rejected_ledger_passes_gate():
    """纯修辞 claim 进 rejected → 合法排除路径，gate PASS。"""
    claims = [{"claim_id": "INV-NF", "speaker": "Bull", "speaker_key": "Bull",
               "stance": "bullish", "claim": "后市值得关注，静待验证", "evidence": ["后市展望"]}]
    ver = [{"claim_id": "INV-NF", "raw": "后市展望", "status": STATUS_VERIFIED}]
    raw = """裁决正文。
<!-- MANAGER_VERDICT: {"winner": "tie", "direction": "中性", "reason": "证据不足", "position_pct": 0, "adopted_claim_ids": [], "partially_adopted_claims": [], "rejected_claim_ids": ["INV-NF"]} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert v["consistency_check_passed"] is True, v["failed_checks"]


# ─────────────────────────── 三、正式 decision/gate ─────────────────────────

def _inv2_claims_and_ver():
    claims = [{
        "claim_id": "INV-2", "speaker": "Bull", "speaker_key": "Bull",
        "stance": "bullish", "claim": INV2_CLAIM,
        "evidence": INV2_VERIFIED,
    }]
    ver = [
        {"claim_id": "INV-2", "raw": INV2_VERIFIED[0], "status": STATUS_VERIFIED},
        {"claim_id": "INV-2", "raw": INV2_VERIFIED[1], "status": STATUS_VERIFIED},
    ]
    return claims, ver


def test_gate_adopt_semantic_reject_claim_fails():
    """INV-2 型：legacy 证据 100% verified 但 semantic=25% reject → adopt 即 fail。"""
    claims, ver = _inv2_claims_and_ver()
    raw = """裁决正文：多头胜。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "基本面扎实", "position_pct": 40, "entry": "10.0", "target": "12.0", "stop_loss": "9.0", "adopted_claim_ids": ["INV-2"], "partially_adopted_claims": [], "rejected_claim_ids": []} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert v["consistency_check_passed"] is False
    assert any("semantic_decision=reject" in e and "INV-2" in e for e in v["failed_checks"])


def test_gate_semantic_reject_in_partial_fails_no_stealth_upgrade():
    """semantic reject（含 supported 子集）进 partially_adopted → fail，不偷升。"""
    claims, ver = _inv2_claims_and_ver()
    raw = """裁决正文。
<!-- MANAGER_VERDICT: {"winner": "tie", "direction": "中性", "reason": "部分采纳", "position_pct": 0, "adopted_claim_ids": [], "partially_adopted_claims": ["INV-2"], "rejected_claim_ids": []} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert v["consistency_check_passed"] is False
    assert any("semantic_decision=reject" in e and "INV-2" in e for e in v["failed_checks"])


def test_gate_non_factual_in_adopted_fails():
    """纯修辞 claim 被 manager 当 adopted → deterministic gate fail。"""
    claims = [{"claim_id": "INV-NF", "speaker": "Bull", "speaker_key": "Bull",
               "stance": "bullish", "claim": "后市值得关注，静待验证", "evidence": ["后市展望"]}]
    ver = [{"claim_id": "INV-NF", "raw": "后市展望", "status": STATUS_VERIFIED}]
    raw = """裁决正文。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "修辞采纳", "position_pct": 20, "entry": "10.0", "target": "11.0", "stop_loss": "9.0", "adopted_claim_ids": ["INV-NF"], "partially_adopted_claims": [], "rejected_claim_ids": []} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert v["consistency_check_passed"] is False
    assert any("non_factual_only" in e and "INV-NF" in e for e in v["failed_checks"])


def test_gate_semantic_adopt_legacy_adopt_passes():
    """GREEN：legacy adopt + semantic adopt → adopted 合法。"""
    claims = [{"claim_id": "INV-1", "speaker": "Bull", "speaker_key": "Bull",
               "stance": "bullish", "claim": "主力净流出1.2亿", "evidence": ["主力净流出1.2亿元"]}]
    ver = [{"claim_id": "INV-1", "raw": "主力净流出1.2亿元", "status": STATUS_VERIFIED}]
    raw = """裁决正文：空头胜。
<!-- MANAGER_VERDICT: {"winner": "bear", "direction": "看空", "reason": "资金流出", "position_pct": 10, "adopted_claim_ids": ["INV-1"], "partially_adopted_claims": [], "rejected_claim_ids": []} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert v["consistency_check_passed"] is True, v["failed_checks"]


def test_partial_claim_unsupported_propositions_enter_excluded():
    """partial_threshold claim 被部分采纳时，未证实质命题必须进 excluded_evidence。"""
    claims = [{
        "claim_id": "INV-3", "speaker": "Bull", "speaker_key": "Bull",
        "stance": "bullish",
        "claim": "Q1扣非增20%与回购70亿构筑估值底",
        "evidence": ["2026Q1扣非净利润同比增长20.24%", "派现70亿元"],
    }]
    ver = [
        {"claim_id": "INV-3", "raw": "2026Q1扣非净利润同比增长20.24%", "status": STATUS_VERIFIED},
        {"claim_id": "INV-3", "raw": "派现70亿元", "status": STATUS_VERIFIED},
    ]
    # 无报告文本 → causal 未证 → semantic=2/3 partial_threshold
    raw = """裁决正文。
<!-- MANAGER_VERDICT: {"winner": "tie", "direction": "中性", "reason": "部分采纳", "position_pct": 0, "adopted_claim_ids": [], "partially_adopted_claims": ["INV-3"], "rejected_claim_ids": []} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    s = v["claim_evidence_summary"]["INV-3"]
    assert s["semantic_decision"] == "partial_threshold"
    assert v["consistency_check_passed"] is True, v["failed_checks"]
    assert any("未证实质命题" in e and "构筑估值底" in e for e in v["excluded_evidence"])


# ──────────────── 下游 decision_status 消费正式 semantic decision ────────────

def test_confirmation_gate_consumes_semantic_decision_not_legacy():
    """decision_status 不得退回 legacy coverage 推断：
    legacy adopt + semantic reject 的 claim 被 manager rejected 属合法裁决，
    不再误报 verdict_consistency_rejected_adopt。"""
    claims, ver = _inv2_claims_and_ver()
    summary = aggregate_claim_evidence(claims=claims, claims_verification=ver)
    assert summary["INV-2"]["decision"] == "adopt"          # legacy 仍 100%
    assert summary["INV-2"]["semantic_decision"] == "reject"  # 正式判定 reject

    state, codes = evaluate_confirmation_state(
        claim_evidence_summary=summary,
        claims=claims,
        claims_verification=ver,
        adopted_claim_ids=[],
        partially_adopted_claims=[],
        rejected_claim_ids=["INV-2"],
    )
    assert not any("verdict_consistency_rejected_adopt" in c for c in codes)
    # DAV-1349：唯一论点被合法否决、经理零采纳零部分采纳 → 不得 CONFIRMED。
    assert state == CONFIRM_UNRESOLVED
    assert "no_adjudicated_support" in codes
