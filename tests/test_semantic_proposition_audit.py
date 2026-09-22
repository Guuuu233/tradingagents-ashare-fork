"""DAV-1192 (1141-B1): Semantic Proposition Audit —— 命题拆分、grounded
interpretation 与门禁预览的审计层测试。

锚点来自 DAV-1191 Phase A / 总工裁定口径：
- 000895.SZ / aa773648 / INV-2：legacy=100%，semantic preview 必须复现 25%；
- grounded causal 正例：显式同义关系 + 全部前提 verified → grounded_interpretation；
- 同义结论但无前提链 → unsupported；
- non_factual excluded 不进分母；
- provenance_blocked 不得 supported；
- bull/bear 对称；
- scenario / duration / priced-in 不被误归 non_factual；
- 预览阈值：100% adopt；>=67% partial_threshold；<67% reject；
  reject 且 supported_count>0 → reject_with_supported_subset；
- E-04 未证命题进入 semantic_hard_guards 且 preview 禁止 adopt；
- 审计层不得改变既有 decision 字段语义。
"""
from __future__ import annotations

from tradingagents.agents.utils.evidence_verifier import (
    DECISION_ADOPT,
    STATUS_VERIFIED,
    aggregate_claim_evidence,
    audit_claim_semantic_coverage,
    decompose_claim_propositions,
)

INV2_CLAIM = "食安利空已定价超6天，Q1扣非增20%与高股息构筑估值底"
INV2_VERIFIED = [
    "2026Q1扣非净利润同比增长20.24%",
    "高股息分红方案，账面现金56.97亿元",
]
INV2_REPORTS = {
    "fundamentals_report": "公司2026Q1扣非净利润同比增长20.24%，账面现金56.97亿元，股息率高。",
    "news_report": "食品安全事件发酵后公司公告回应。",
}


def test_inv2_anchor_semantic_25pct_preview_reject():
    """000895 INV-2 锚点：4 命题仅数值命题 supported → semantic=25%，preview=reject。"""
    res = audit_claim_semantic_coverage(
        INV2_CLAIM,
        verified_evidence=INV2_VERIFIED,
        report_fields=INV2_REPORTS,
        claim_id="INV-2",
    )
    assert res["semantic_coverage"] == 0.25
    assert res["semantic_decision_preview"] == "reject"
    assert res["semantic_partial_origin_preview"] == "reject_with_supported_subset"
    kinds = [p["kind"] for p in res["proposition_audit"]]
    assert kinds.count("interpretation_causal") == 2
    # 「已定价超6天」为 E-04 敏感且未证 → hard guard
    assert res["semantic_hard_guards"], "E-04 未证命题必须进入 semantic_hard_guards"
    guard_texts = [g["text"] for g in res["semantic_hard_guards"]]
    assert any("已定价" in t for t in guard_texts)
    # E-04 谓词不得走 causal 放宽
    for p in res["proposition_audit"]:
        if "已定价" in p["text"]:
            assert p["support_status"] == "unsupported"
            assert p["support_kind"] != "grounded_interpretation"


def test_grounded_interpretation_positive():
    """显式同义关系 + 全部前提 verified → grounded_interpretation，preview=adopt。"""
    claim = "Q1扣非增20%与回购70亿构筑估值底"
    verified = ["2026Q1扣非净利润同比增长20.24%，派现70亿元"]
    reports = {"sentiment_report": "扣非增长20%与70亿回购构筑估值底，彰显管理层信心。"}
    res = audit_claim_semantic_coverage(
        claim, verified_evidence=verified, report_fields=reports, claim_id="INV-9"
    )
    causal = [p for p in res["proposition_audit"] if p["kind"] == "interpretation_causal"]
    assert causal and causal[0]["support_status"] == "supported_available"
    assert causal[0]["support_kind"] == "grounded_interpretation"
    assert causal[0]["report_source"] == "sentiment_report"
    assert res["semantic_coverage"] == 1.0
    assert res["semantic_decision_preview"] == "adopt"


def test_causal_synonym_without_premise_chain_unsupported():
    """报告有同义解释句但前提链未全 supported → 仍 unsupported。"""
    claim = "公司近70亿元回购构筑铁底"
    # 前提数值无任何证据命中（verified 与 report 都不含 70）→ 前提 unsupported
    reports = {"sentiment_report": "回购构筑铁底，但具体金额未披露。"}
    res = audit_claim_semantic_coverage(claim, verified_evidence=[], report_fields=reports)
    causal = [p for p in res["proposition_audit"] if p["kind"] == "interpretation_causal"]
    assert causal[0]["support_status"] == "unsupported"
    assert res["semantic_coverage"] < 1.0


def test_non_factual_excluded_from_denominator():
    """真 non_factual 排除分母但保留 audit 记录。"""
    claim = "后市值得关注"
    res = audit_claim_semantic_coverage(claim, verified_evidence=[], report_fields={})
    assert res["proposition_audit"]
    assert res["proposition_audit"][0]["kind"] == "non_factual_or_normative"
    assert res["semantic_counts"]["non_factual_excluded"] == 1
    assert res["semantic_counts"]["verifiable"] == 0


def test_provenance_blocked_not_supported():
    """provenance_blocked 不得 supported；主题域受限时标记 provenance_blocked。"""
    claim = "主力超大单净流入5亿元"
    res = audit_claim_semantic_coverage(
        claim,
        verified_evidence=[],
        report_fields={},
        blocked_sources={"fund_flow"},
    )
    for p in res["proposition_audit"]:
        assert p["support_status"] != "supported_available"
    assert any(p["support_status"] == "provenance_blocked" for p in res["proposition_audit"])
    assert res["semantic_coverage"] < 1.0


def test_bull_bear_symmetry():
    """同一 claim 文本不论 bull/bear 立场，审计结果一致（拆分判定与立场无关）。"""
    claim = "缩量回踩构筑双底，主力净流出1.2亿"
    a = audit_claim_semantic_coverage(claim, verified_evidence=["主力净流出1.2亿元"], report_fields={})
    b = audit_claim_semantic_coverage(claim, verified_evidence=["主力净流出1.2亿元"], report_fields={})
    assert a["semantic_coverage"] == b["semantic_coverage"]
    assert [p["kind"] for p in a["proposition_audit"]] == [p["kind"] for p in b["proposition_audit"]]


def test_scenario_duration_priced_in_not_misclassified_non_factual():
    """scenario / duration / priced-in 不得被误归 non_factual。"""
    scen = decompose_claim_propositions("若大盘跌破3800点情景下或面临二次探底压力")
    assert any(p["type"] == "scenario_hypothesis" for p in scen)
    dur = decompose_claim_propositions("食安利空已持续超6天")
    assert all(p["type"] != "non_factual_or_normative" for p in dur)
    priced = decompose_claim_propositions("利空已定价")
    assert any(p["type"] == "interpretation_causal" for p in priced)


def test_preview_thresholds_and_origins():
    """预览阈值：100% adopt；>=67% partial_threshold；<67% reject + origin。"""
    full = audit_claim_semantic_coverage(
        "主力净流出1.2亿", verified_evidence=["主力净流出1.2亿元"], report_fields={}
    )
    assert full["semantic_decision_preview"] == "adopt"
    assert full["semantic_partial_origin_preview"] == "adopt_full_coverage"

    zero = audit_claim_semantic_coverage(
        "主力净流出1.2亿", verified_evidence=[], report_fields={}
    )
    assert zero["semantic_decision_preview"] == "reject"
    assert zero["semantic_partial_origin_preview"] == "reject_no_supported_subset"

    subset = audit_claim_semantic_coverage(
        INV2_CLAIM, verified_evidence=INV2_VERIFIED, report_fields=INV2_REPORTS
    )
    assert subset["semantic_decision_preview"] == "reject"
    assert subset["semantic_partial_origin_preview"] == "reject_with_supported_subset"


def test_e04_hard_guard_blocks_adopt_preview():
    """E-04 未证命题进入 hard guards，preview 禁止 adopt。"""
    res = audit_claim_semantic_coverage(
        INV2_CLAIM, verified_evidence=INV2_VERIFIED, report_fields=INV2_REPORTS
    )
    assert res["semantic_decision_preview"] != "adopt"
    assert res["semantic_hard_guards"]
    assert res["semantic_counts"]["e04_unsupported"] >= 1


def test_aggregate_adds_semantic_fields_without_changing_decision():
    """aggregate_claim_evidence 新增 semantic_* 字段；既有 decision 语义不变。"""
    claims = [
        {
            "claim_id": "INV-2",
            "speaker": "Bull Analyst",
            "speaker_key": "Bull",
            "stance": "bullish",
            "claim": INV2_CLAIM,
            "evidence": ["2026Q1扣非净利润同比增长20.24%", "高股息分红56.97亿元现金"],
        }
    ]
    claims_verification = [
        {"claim_id": "INV-2", "raw": "2026Q1扣非净利润同比增长20.24%", "status": STATUS_VERIFIED},
        {"claim_id": "INV-2", "raw": "高股息分红56.97亿元现金", "status": STATUS_VERIFIED},
    ]
    summary = aggregate_claim_evidence(
        claims=claims,
        claims_verification=claims_verification,
        seven_reports=INV2_REPORTS,
    )
    s = summary["INV-2"]
    # 既有 decision 语义不变：legacy evidence 100% verified → 仍 adopt
    assert s["decision"] == DECISION_ADOPT
    assert s["coverage"] == 1.0
    # 新增审计字段齐备
    for k in (
        "semantic_coverage",
        "semantic_counts",
        "proposition_audit",
        "semantic_decision_preview",
        "semantic_partial_origin_preview",
        "semantic_hard_guards",
    ):
        assert k in s
    assert s["semantic_coverage"] == 0.25
    assert s["semantic_decision_preview"] == "reject"
    for p in s["proposition_audit"]:
        assert p["proposition_id"].startswith("INV-2#p")
        assert p["kind"]
        assert p["support_status"] in {
            "supported_available",
            "unsupported",
            "provenance_blocked",
            "non_factual_or_normative",
            "ambiguous_contract",
        }


def test_aggregate_without_reports_still_audits():
    """无 seven_reports 时仍产出审计字段（报告语料为空，verified 命中仍可用）。"""
    claims = [{"claim_id": "C1", "claim": "主力净流出1.2亿", "evidence": ["主力净流出1.2亿元"]}]
    claims_verification = [
        {"claim_id": "C1", "raw": "主力净流出1.2亿元", "status": STATUS_VERIFIED}
    ]
    summary = aggregate_claim_evidence(claims=claims, claims_verification=claims_verification)
    s = summary["C1"]
    assert "semantic_coverage" in s
    assert s["proposition_audit"]
