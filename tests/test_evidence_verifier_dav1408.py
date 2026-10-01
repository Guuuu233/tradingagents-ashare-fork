"""DAV-1408: proportion identity, non-factual labels and integer truncation.

Synthetic reports exercise extraction and both verification/aggregation passes.
"""
import pytest

from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator,
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
    _extract_numbers_and_units,
    _is_rounding_equivalent,
    _is_bound_num_match,
    _sem_extract_nums,
    extract_bound_numbers,
)


def pipeline(raw, report):
    evaluator = EvidenceFactualTruthEvaluator()
    claims = [{"claim_id": "INV-6", "claim": raw, "evidence": [raw]}]
    reports = {"smart_money_report": report}
    items = evaluator.evaluate_claims(claims, reports)
    summary = evaluator.aggregate_claim_evidence(
        claims=claims, claims_verification=items, seven_reports=reports,
    )["INV-6"]
    return items, summary


@pytest.mark.parametrize("raw", [
    "大单流出占流通盘仅0.034%", "超大单流出占流通盘仅0.034%",
    "大单净额占流通市值比0.034%",
])
def test_component_does_not_borrow_main_flow_ratio(raw):
    report = "东财主力净流出占流通市值比（net_to_circ_mv）为-0.0341%。"
    items, summary = pipeline(raw, report)
    assert summary["verified_evidence"] == [], items
    assert summary["unsupported_evidence"], items
    assert summary["contradicted_evidence"] == [], items


@pytest.mark.parametrize("raw, report", [
    ("主力流出占流通盘0.034%", "主力净额占流通市值比（net_to_circ_mv）为-0.0341%。"),
    ("流出占流通盘0.034%", "主力净额占流通市值比（net_to_circ_mv）为-0.0341%。"),
    ("大单流出占流通盘0.0171%", "东财大单净流出占流通市值比0.0171%。"),
    ("超大单流出占流通盘0.0171%", "东财超大单净流出占流通市值比0.0171%。"),
    ("超大单与大单合计流出占流通盘0.034%", "主力净额占流通市值比0.0341%。"),
    ("存货占流动资产41.2%", "存货在总流动资产中占比升至41.2%。"),
])
def test_same_numerator_and_unspecified_main_flow_still_verify(raw, report):
    items, summary = pipeline(raw, report)
    assert summary["verified_evidence"] and not summary["unsupported_evidence"], items


@pytest.mark.parametrize("raw, report", [
    ("存货占流动资产41.2%", "应收账款占流动资产41.2%。"),
    ("大单流出占流通盘0.034%", "超大单净流出占流通市值比0.034%。"),
    ("主力流出占流通盘0.034%", "大单净流出占流通市值比0.034%。"),
    ("流动负债占总资产88%", "流动负债占总负债88%。"),
])
def test_proportion_wrong_numerator_or_denominator_rejected(raw, report):
    items, summary = pipeline(raw, report)
    assert not summary["verified_evidence"], items


@pytest.mark.parametrize("label", [
    "9-24", "9-4", "09-24", "2026预测", "2026 预测", "2026E", "2026F",
    "IFRS 9", "IFRS9", "IAS 36", "国际财务报告准则第9号", "企业会计准则第22号",
])
def test_label_digits_do_not_enter_numeric_extractors(label):
    text = label + "，现金流58.18亿元"
    assert [b.raw for b in extract_bound_numbers(text)] == ["58.18亿元"]
    assert [b[2] for b in _extract_numbers_and_units(text)] == ["58.18亿元"]
    assert _sem_extract_nums(text) == {"58.18"}


@pytest.mark.parametrize("raw", ["9-24", "2026E", "IFRS 9"])
def test_label_alone_has_no_fact_coverage(raw):
    evaluator = EvidenceFactualTruthEvaluator()
    assert evaluator._verify_evidence_or_decompose(raw, {}, {}, None, "T-1", None) == []


@pytest.mark.parametrize("text, expected", [
    ("回购9-24元", ["9", "24元"]),
    ("净利润2026亿元", ["2026亿元"]),
    ("PE9倍", ["9倍"]),
    ("现金流增长9%", ["9%"]),
    ("连续4日主力净流入", ["4日"]),
    ("产能1000GW", ["1000GW"]),
])
def test_real_values_are_not_masked_as_labels(text, expected):
    assert [b.raw for b in extract_bound_numbers(text)] == expected


@pytest.mark.parametrize("raw, report", [
    ("现金流58亿元", "现金流58.18亿元"),
    ("现金流-58亿元", "现金流-58.18亿元"),
    ("现金流8亿元", "现金流8.91亿元"),
    ("现金流-8亿元", "现金流-8.91亿元"),
    ("股价8元", "股价8.91元"),
    ("ROE8%", "ROE8.91%"),
    ("现金流80000万元", "现金流8.000091亿元"),
])
def test_integer_truncation_uses_evidence_display_unit(raw, report):
    ev_bn = extract_bound_numbers(raw)[0]
    l_bn = extract_bound_numbers(report)[0]
    assert _is_rounding_equivalent(ev_bn, l_bn)
    items, summary = pipeline(raw, "报告记录" + report + "。")
    assert summary["verified_evidence"] and not summary["contradicted_evidence"], items


@pytest.mark.parametrize("raw, report", [
    ("现金流8.91亿元", "现金流8亿元"),
    ("现金流8.9亿元", "现金流9.81亿元"),
    ("现金流-8亿元", "现金流8.91亿元"),
    ("现金流8亿元", "现金流9.01亿元"),
    ("融资成本下行25bp", "融资成本下行13bp"),
    ("股息率5.5%", "股息率6%"),
    ("风险收益比1:3", "风险收益比1:2"),
])
def test_truncation_does_not_relax_precision_sign_or_dimension(raw, report):
    ev_bn, l_bn = extract_bound_numbers(raw)[0], extract_bound_numbers(report)[0]
    assert not _is_rounding_equivalent(ev_bn, l_bn)


def test_truncation_does_not_approve_wrong_metric():
    items, summary = pipeline("现金流8亿元", "营收8.91亿元。")
    assert not summary["verified_evidence"], items


@pytest.mark.parametrize("report_numerator, expected", [("大单", True), ("主力", False)])
def test_ratio_range_endpoints_keep_numerator_identity(report_numerator, expected):
    raw = "大单流出占流通盘0.03%-0.04%"
    report = report_numerator + "净流出占流通市值比0.03%~0.04%"
    evidence_bns, report_bns = extract_bound_numbers(raw), extract_bound_numbers(report)
    assert [b.proportion for b in evidence_bns] == [("大单", "流通市值")] * 2
    for ev_bn, l_bn in zip(evidence_bns, report_bns):
        assert _is_bound_num_match(ev_bn, l_bn, ev_text=raw, l_text=report) is expected


@pytest.mark.parametrize("text", ["布林上轨12.03", "收盘价10.12", "股价9.24元"])
def test_semantic_extractor_preserves_real_decimal_price(text):
    assert len(_sem_extract_nums(text)) == 1


@pytest.mark.parametrize("raw, report", [
    ("主力净额占流通市值-0.0237%", "净额占流通市值比（net_to_circ_mv）：以东财主力净额计算，为-0.0237%。"),
    ("主力净额占流通市值比0.0249%", "净额占流通市值比约0.0249%（基于东财主力净额与流通市值测算）。"),
    ("主力净流出占流通市值比-0.0613%", "东方财富主力净额占流通市值比（net_to_circ_mv）：约为-0.0613%。"),
    ("主力净流出5.48亿元仅占流通市值-0.0361%", "主力净流出5.48亿元。\n净额占流通市值比（net_to_circ_mv）为-0.0361%。"),
    ("东财主力净流出0.81亿占流通市值-0.0146%", "东财主力净流出0.81亿元。\n净额占流通市值比为-0.0146%。"),
    ("主力净流出占流通市值比仅-0.061%", "净额占流通市值比（net_to_circ_mv）为-0.0613%。"),
    ("主力净流出仅占流通市值-0.022%", "主力净额占流通市值比为-0.0223%。"),
    ("主力净流出占流通市值比仅-0.225%", "净额占流通市值比（net_to_circ_mv）为-0.225%。"),
    ("扣非净利润765.39亿占比99.66%", "扣除非经常性损益后净利润765.39亿元（占归母净利润的99.66%）。"),
    ("扣非净利765.39亿占99.66%", "扣除非经常性损益后净利润765.39亿元（占归母净利润的99.66%）。"),
    ("扣非净利446.64亿占归母99.68%", "扣非净利润446.64亿元，占归母净利润比重达99.68%。"),
    ("存货达1901.91亿元占总资产20.38%", "存货1901.91亿元，占资产总额20.38%。"),
    ("毛利率86.13%成本占比仅13.87%", "毛利率86.13%。\n营业成本占营业收入比重仅为13.87%。"),
    ("折旧占成本超32%", "设备折旧占晶圆制造成本超32%。"),
    ("闪充折旧仅占利润0.5%", "季度设备折旧支出有限（占半年度净利约0.5%）。"),
])
def test_r2_reported_main_and_ordinary_ratios_restore(raw, report):
    items, summary = pipeline(raw, report)
    assert summary["verified_evidence"] and not summary["unsupported_evidence"], items


@pytest.mark.parametrize("raw, report", [
    ("大单流出占流通盘0.034%", "净额占流通市值比（net_to_circ_mv）为-0.0341%。"),
    ("大单流出占流通市值0.034%", "净额占流通市值比：以东财主力净额计算，为-0.0341%。"),
    ("超大单流出占流通市值0.0126%", "净额占流通市值比为-0.0126%（基于东财主力净額测算）。"),
    ("大单流出占流通盘0.018%", "主力净额占流通市值比（net_to_circ_mv）为-0.018%。"),
    ("大单流出占流通盘0.034%", "净额占流通市值比为-0.0341%，大单净流出0.5亿元。"),
    ("主力流出占流通盘0.45%", "主力净额占流通市值比为-0.018%。"),
    ("大单流出占流通盘0.034%", "主力净额占流通市值比（net_to_circ_mv）为-0.0341%。\n| 相对规模 | 占流通市值-0.034% | 微幅流出 |"),
    ("超大单流出占流通市值0.0126%", "主力净额占流通市值比为-0.0126%（即-0.000126）。"),
    ("大单流入占流通市值0.02%", "净额占流通市值比（net_to_circ_mv）约0.0200%（0.00019999）。"),
])
def test_r2_wrong_component_or_unreported_value_still_rejected(raw, report):
    items, summary = pipeline(raw, report)
    assert not summary["verified_evidence"], items


@pytest.mark.parametrize("raw, report", [
    ("同一支撑二次测试破位概率系统性高于首次，多头误把已被验证当作永远有效",
     "支撑区域存在破位风险。\n多头概率较高。\n首次测试已结束。"),
    ("1-3月同一支撑二次测试破位概率系统性高于首次，多头误把已被验证当作永远有效",
     "支撑区域存在破位风险。\n多头概率较高。\n首次测试已结束。"),
    ("铜价坚挺反映低库存供给刚性现实", "铜价维持高位，全球矿业具有供给刚性。"),
    ("1-3月内盈利底与估值底双重压缩是正确方法", "盈利改善，估值回升。"),
    ("1-3月观察窗的黄金定价权在快变量", "黄金价格与定价因素有关。"),
])
def test_r2_removed_labels_do_not_prove_unwritten_judgments(raw, report):
    items, summary = pipeline(raw, report)
    assert not summary["verified_evidence"], items


def test_r2_actual_reported_percent_point_difference_kept():
    items, summary = pipeline("扣非与净利增速相差16.84%", "扣非与净利增速相差16.84个百分点。")
    assert summary["verified_evidence"] and not summary["contradicted_evidence"], items


def test_component_ratio_is_not_rescued_by_atomic_recheck():
    raw = "主力净流出5.2754亿且大单流出占流通盘仅0.034%"
    report = "主力净流出5.2754亿元。\n主力净额占流通市值比为-0.0341%。"
    items, summary = pipeline(raw, report)
    assert "0.034%" in summary["unsupported_evidence"], items
    assert any("5.2754" in fact for fact in summary["verified_evidence"]), items
    assert next(i for i in items if i["raw"] == "0.034%")["status"] == STATUS_UNSUPPORTED
