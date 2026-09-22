"""Unit tests for the PriceRef registry & bypass audit layer (DAV-1198 / DAV-1142-B1).

Covers the card anchors:
- b188060f pattern: disclosure raw 78.61 vs technical qfq 81.19 → basis mismatch captured.
- typed disclosures (大宗/增持/回购/减持/发行/龙虎榜) → deterministic raw/pit_raw.
- untyped news prices never default to raw.
- missing basis / missing as_of land in price_basis_gaps.
- raw→qfq conversion missing factor provenance or factor_as_of > cutoff → preview invalid.
- bypass guarantee: audit never mutates decision fields.
"""

from tradingagents.agents.utils.price_ref_registry import (
    CANONICAL_PRICE_BASES,
    DISCLOSURE_TYPE_BASIS,
    audit_price_ref_registry,
    build_price_ref_registry,
)
from tradingagents.dataflows.providers.cn_akshare_provider import (
    PRICE_BASIS_PIT_RAW,
    PRICE_BASIS_RAW,
    PRICE_BASIS_UNSPECIFIED,
    PRICE_BASIS_VENDOR_QFQ,
)


def _refs_by_value(result, value):
    return [r for r in result["price_refs"] if r["value"] == value]


# ---------------------------------------------------------------------------
# Anchor: b188060f — disclosure raw vs vendor qfq mismatch
# ---------------------------------------------------------------------------


def test_b188060f_anchor_disclosure_raw_vs_qfq_mismatch():
    reports = {
        "volume_price_report": "现价 81.19 元，均线呈多头排列。",
        "news_report": (
            "今日大宗交易成交 78.61 元，较当日收盘 84.03 元折价 6.45%。"
            "该价格对现价 81.19 形成向下锚。"
        ),
    }
    result = build_price_ref_registry(reports, cutoff="2026-05-22")

    r7861 = _refs_by_value(result, 78.61)
    assert r7861, "78.61 must be registered"
    assert r7861[0]["basis"] == PRICE_BASIS_RAW
    assert r7861[0]["disclosure_type"] == "block_trade"

    # 现价 81.19 in a technical report is vendor_qfq.
    tech = _refs_by_value(result, 81.19)
    assert any(r["basis"] == PRICE_BASIS_VENDOR_QFQ for r in tech)

    mismatch_findings = [
        f for f in result["validation"]["findings"] if f["kind"] == "basis_mismatch"
    ]
    assert mismatch_findings, "raw vs qfq mismatch must be captured by the audit"
    assert result["validation"]["status"] in ("invalid", "gaps_present")
    assert result["validation"]["preview_only"] is True


# ---------------------------------------------------------------------------
# Typed disclosure positives
# ---------------------------------------------------------------------------


def test_typed_disclosures_deterministic_basis():
    cases = {
        "大宗交易": PRICE_BASIS_RAW,
        "龙虎榜": PRICE_BASIS_RAW,
        "增持": PRICE_BASIS_PIT_RAW,
        "减持": PRICE_BASIS_PIT_RAW,
        "回购": PRICE_BASIS_PIT_RAW,
        "发行": PRICE_BASIS_PIT_RAW,
        "定增": PRICE_BASIS_PIT_RAW,
    }
    for kw, expected_basis in cases.items():
        reports = {"news_report": f"公司披露{kw}价格 10.50 元。"}
        result = build_price_ref_registry(reports, cutoff="2026-05-22")
        refs = _refs_by_value(result, 10.50)
        assert refs, f"{kw} price must be registered"
        assert refs[0]["basis"] == expected_basis, kw
        assert refs[0]["provenance"].startswith("typed_disclosure"), kw


def test_untyped_news_price_not_default_raw():
    reports = {"news_report": "某媒体报道目标价 20.30 元，市场热议。"}
    result = build_price_ref_registry(reports, cutoff="2026-05-22")
    refs = _refs_by_value(result, 20.30)
    assert refs
    assert refs[0]["basis"] == PRICE_BASIS_UNSPECIFIED
    gap_kinds = {g["kind"] for g in result["price_basis_gaps"]}
    assert "missing_basis" in gap_kinds


# ---------------------------------------------------------------------------
# Model prices cannot self-certify; registry back-reference inheritance
# ---------------------------------------------------------------------------


def test_model_price_backref_inherits_qfq_and_new_price_unspecified():
    reports = {
        "market_report": "现价 81.19 元。",
        "investment_plan": "建议关注现价 81.19 附近走势，并以 66.66 元作为心理关口。",
    }
    result = build_price_ref_registry(reports, cutoff="2026-05-22")

    plan_8119 = [
        r for r in _refs_by_value(result, 81.19) if r["source"] == "investment_plan"
    ]
    assert plan_8119 and plan_8119[0]["basis"] == PRICE_BASIS_VENDOR_QFQ
    assert plan_8119[0]["provenance"] == "registry_backref:vendor_qfq"

    new_price = _refs_by_value(result, 66.66)
    assert new_price and new_price[0]["basis"] == PRICE_BASIS_UNSPECIFIED


# ---------------------------------------------------------------------------
# Gaps: missing basis / missing as_of
# ---------------------------------------------------------------------------


def test_missing_basis_and_missing_as_of_gaps():
    reports = {"news_report": "减持价格 12.34 元。"}
    result = build_price_ref_registry(reports, cutoff="2026-05-22")
    ref = _refs_by_value(result, 12.34)[0]
    kinds = {(g["kind"], g["ref_id"]) for g in result["price_basis_gaps"]}
    # pit_raw basis assigned, but no date → missing_as_of only.
    assert ("missing_as_of", ref["ref_id"]) in kinds
    assert ("missing_basis", ref["ref_id"]) not in kinds


def test_disclosure_date_in_sentence_supplies_as_of():
    reports = {"news_report": "2026-05-21 大宗交易成交 78.61 元。"}
    result = build_price_ref_registry(reports, cutoff="2026-05-22")
    ref = _refs_by_value(result, 78.61)[0]
    assert ref["as_of"] == "2026-05-21"
    assert not [
        g for g in result["price_basis_gaps"]
        if g["kind"] == "missing_as_of" and g["ref_id"] == ref["ref_id"]
    ]


# ---------------------------------------------------------------------------
# Derived / conversion lineage
# ---------------------------------------------------------------------------


def test_derived_price_keeps_lineage_and_derived_not_a_basis():
    reports = {
        "investment_plan": "大宗交易 78.61 元，按复权因子 0.97 换算为前复权约 76.25 元。",
    }
    result = build_price_ref_registry(reports, cutoff="2026-05-22")
    derived = _refs_by_value(result, 76.25)
    assert derived
    d = derived[0]
    assert d["basis"] in CANONICAL_PRICE_BASES
    assert d["basis"] != "derived"
    assert d["provenance"].startswith("derived:")
    assert d["derived_from"], "derived_from lineage must be recorded"
    conv = d["conversion"]
    assert conv["factor"] == 0.97


def test_conversion_missing_factor_previews_invalid():
    reports = {
        "investment_plan": "大宗交易 78.61 元，换算为前复权约 76.25 元。",
    }
    result = build_price_ref_registry(reports, cutoff="2026-05-22")
    assert result["validation"]["status"] == "invalid"
    kinds = {f["kind"] for f in result["validation"]["findings"]}
    assert "invalid_conversion" in kinds


def test_conversion_factor_as_of_after_cutoff_previews_invalid():
    reports = {
        "investment_plan": "2026-06-01 大宗交易 78.61 元，按复权因子 0.97 换算为前复权约 76.25 元。",
    }
    result = build_price_ref_registry(reports, cutoff="2026-05-22")
    assert result["validation"]["status"] == "invalid"
    reasons = " ".join(
        f["detail"] for f in result["validation"]["findings"]
        if f["kind"] == "invalid_conversion"
    )
    assert "cutoff" in reasons or "factor_as_of" in reasons


def test_conversion_with_factor_and_date_within_cutoff_ok():
    reports = {
        "investment_plan": "2026-05-20 大宗交易 78.61 元，按复权因子 0.97 换算为前复权约 76.25 元。",
    }
    result = build_price_ref_registry(reports, cutoff="2026-05-22")
    assert not [
        f for f in result["validation"]["findings"] if f["kind"] == "invalid_conversion"
    ]


# ---------------------------------------------------------------------------
# Bypass guarantees / state integration
# ---------------------------------------------------------------------------


def test_audit_attaches_side_channel_and_never_mutates_decision_fields():
    state = {
        "trade_date": "2026-05-22",
        "market_report": "现价 81.19 元。",
        "news_report": "大宗交易成交 78.61 元。",
        "final_trade_decision": "SELL 现价 81.19",
        "investment_plan": "keep",
    }
    before_decision = state["final_trade_decision"]
    before_plan = state["investment_plan"]
    result = audit_price_ref_registry(state)

    assert state["final_trade_decision"] == before_decision
    assert state["investment_plan"] == before_plan
    assert state["price_refs"] == result["price_refs"]
    assert state["price_basis_gaps"] == result["price_basis_gaps"]
    assert state["price_basis_validation"] == result["validation"]
    assert any(r["basis"] == PRICE_BASIS_RAW for r in result["price_refs"])


def test_audit_tolerates_empty_or_malformed_state():
    assert audit_price_ref_registry({})["price_refs"] == []
    assert audit_price_ref_registry(None)["price_refs"] == []
    state = {"market_report": None, "news_report": 123}
    result = audit_price_ref_registry(state)
    assert result["validation"]["status"] == "ok"


def test_same_sentence_mixed_basis_flagged():
    # qfq 现价 vs explicit 不复权 raw in one sentence.
    reports = {
        "market_report": "现价 81.19 元。",
        "investment_plan": "现价 81.19 元，而未复权价为 84.03 元。",
    }
    result = build_price_ref_registry(reports, cutoff="2026-05-22")
    mixed = [
        f for f in result["validation"]["findings"]
        if f["kind"] == "basis_mismatch" and f["rule"] == "same_sentence_mixed_basis"
    ]
    assert mixed
