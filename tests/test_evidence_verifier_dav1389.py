"""DAV-1389 证据核验双向修复第二段单测（合成夹具，不含 vendor 原文）。

漏认向（总控核准的五类）：
  区间端点同值同指标（0.8%~1.1% 的 0.8%）、符号谓词归一（口径②，
  「侵蚀1.1%」≡-1.1%）、变动量基期绑定不对称（「较2025年同期增长
  34.90%」「由52.56%降至」）、报告原文写出的测算/情景值（口径①，
  「约为6.90亿」）、资金流粒度合计派生值重算（大单0.59+中单1.20=1.79亿）。
误认向（总控核准的收严）：
  时间跨度/日期区间不得作命中来源（6-12个月、8月底的「8」）、
  百分比区间端点跨量纲巧合（15-16倍PE 撞 15%~20%分位）、
  abs() 绝对值等价（25.31% 撞 -0.24%）、跨单位舍入等价
  （2.55pct 撞章节「3」）、行首/行内枚举序号（赔率3:1 撞 「3. 政策」）、
  双侧无锚点裸整数同值（不算命中）、双实体不一致跨主体巧合。
每个根因独立 RED→GREEN。
"""
import pytest

from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator,
    STATUS_CONTRADICTED,
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
    extract_bound_numbers,
)


@pytest.fixture
def evaluator():
    return EvidenceFactualTruthEvaluator()


def _status(ev, raw, reports, claim_text=None):
    items = ev._verify_evidence_or_decompose(
        raw, reports, {}, None, "T-1", None, claim_text=claim_text
    )
    return {i.get("raw"): i.get("status") for i in items}


def _pipeline(ev, raw, report, claim=""):
    """Use the delivery pipeline, including its second strict verification pass."""
    claims = [{"claim_id": "T-1", "claim": claim, "evidence": [raw]}]
    reports = {"fundamentals_report": report}
    items = ev.evaluate_claims(claims, reports)
    summary = ev.aggregate_claim_evidence(
        claims=claims, claims_verification=items, seven_reports=reports,
    )["T-1"]
    return items, summary


@pytest.mark.parametrize("raw, report, targets", [
    ("2000亿级带息负债置换每下行25bp年化直接增厚净利约3.7-4.1亿元",
     "- 公司带息负债总额超2000亿元。\n"
     "- 综合融资成本每下行25个基点（BP），增厚归母净利润约3.75亿-4.12亿元。",
     ["2000亿", "25bp"]),
    ("宏观报告显示铜季涨9.98%侵蚀毛利0.8%-1.1%折合16-22亿",
     "- 铜价季度上涨9.98%。\n- 成本侵蚀毛利率约0.8%~1.1%，潜在毛利压制规模约16亿~22亿元。",
     ["22亿"]),
    ("新闻报告显示新增研发支出3-5亿压低利润率0.8-1.2pct",
     "- 预计新增临床带来年化约3~5亿元研发支出增量，压制营业利润率约0.8~1.2个百分点。",
     ["1.2pct"]),
    ("新闻报告显示二季度超360家机构减仓",
     "- 中报持股数据显示二季度持股减少机构超过360家。", ["360家"]),
    ("成本上升5%即削减营业利润20.85%估值承载力脆弱",
     "- 成本上涨10%会损害利润。\n"
     "- 若综合采购成本上涨5%，营业利润下行20.85%。", ["5%", "20.85%"]),
    ("宏观报告美债破5.18%致海外公用龙头单月暴跌8%-9%",
     "- 美债收益率5.180%。\n- 海外公用事业巨头单月跌幅达8%-9%。", ["8%"]),
    ("09-24温和放量32%收光头阳线(28.36元)",
     "- 成交量回升至95.02万手（量比1.1，较前一日71.71万手增幅超32%）；收盘28.36元。", ["32%"]),
    ("金属价格联动机制保障H1毛利率23.93%与232亿FCF",
     "- 毛利率保持23.93%。\n"
     "- 自由现金流（FCF）：扣除投资活动现金369.29亿元，公司仍录得约232.88亿元的正向自由现金流。", ["232亿"]),
    ("澳洲大储25-30%高毛利与百元油价拉大TCO优势",
     "- 海外储能业务毛利率通常维持在25%-30%区间。", ["25"]),
    ("2026H1扣非归母净利同比下滑25.31%且费用率攀升至15.73%",
     "- 扣非归母净利润195.95亿元，同比下滑25.31%（2025H1为262.35亿元）。\n"
     "- 费用率攀升至15.73%。", ["25.31%"]),
    ("10年美债升至5.18%致摩根大通汇丰单月跌3%-5%",
     "- 10年期美债收益率升至5.180%。\n- 摩根大通、汇丰单月跌幅超3%-5%。", ["5.18%", "3%", "-5%"]),
    ("10年美债升至5.18%摩根大通汇丰跌3%-5%且极端压力位0.82倍PB对应37.20元",
     "- 10年期美债收益率升至5.180%。\n- 摩根大通、汇丰单月跌幅超3%-5%。\n"
     "- 极端压力测试下PB约0.80-0.82倍，对应股价支撑36.30-37.20元。", ["5.18%", "3%", "-5%"]),
], ids=["R4_S07", "R4_S08", "R4_S09", "R4_S11", "R4_S16", "R4_S21",
        "R4_S22", "R4_S24", "R4_S26", "R4_S27", "R4_S28", "R4_S29"])
def test_r4_misrejects_through_aggregate(evaluator, raw, report, targets):
    items, summary = _pipeline(evaluator, raw, report)
    verified = summary["verified_evidence"]
    for target in targets:
        assert any(target in fact for fact in verified), (target, items, summary)
    assert summary["contradicted_evidence"] == [], (items, summary)
    assert not any(i["status"] == STATUS_CONTRADICTED for i in items)


@pytest.mark.parametrize("text, expected", [
    ("10年美债收益率5.18%", ["5.18%"]),
    ("美债10年期收益率5.18%", ["5.18%"]),
    ("9月次地量萎缩76%", ["76%"]),
    ("2026 H1扣非同比下滑25.31%", ["25.31%"]),
    ("Q3新增利润15-25%", ["15", "25%"]),
    ("#### 3. 政策催化", []),
    ("09-24成交1866万股", ["1866万股"]),
    ("连续4日主力净流入", ["4日"]),
])
def test_r4_labels_not_facts_but_durations_are(text, expected):
    assert [b.raw for b in extract_bound_numbers(text)] == expected


def test_r4_all_subclauses_still_decompose(evaluator):
    raw = "营业总支出同比增7.88%超营收增速4.83%，销售净利率下滑1.19%"
    items, summary = _pipeline(evaluator, raw,
        "- 营业总支出（营业总成本）同比上升7.88%。\n- 营收同比增长4.83%。")
    for target in ("7.88%", "4.83%"):
        assert any(target in fact for fact in summary["verified_evidence"]), items
    assert "1.19%" in summary["unsupported_evidence"]


@pytest.mark.parametrize("raw, report", [
    ("10年美债5.18%与原油破百使5.5%股息率丧失风险溢价", "美债收益率5.18%，布油100.21美元。"),
    ("ATR降至0.92且现价距布林下轨68.83元仅1.2%", "ATR降至0.92，布林下轨68.83元。"),
    ("主力占流通盘82.3%", "主力占流通盘0.012%。"),
    ("扣现PE16.5倍", "扣非PE19倍。"),
    ("成交超百亿", "成交额5亿元。"),
    ("Q3新增利润15-25%", "Q3营收增长7%。"),
])
def test_r4_unsupported_parents_not_promoted(evaluator, raw, report):
    items, summary = _pipeline(evaluator, raw, report)
    assert summary["unsupported_evidence"] or summary["contradicted_evidence"], items
    assert raw not in summary["verified_evidence"]


def test_r5_atomic_odds_not_borrowed_from_unrelated_ratios(evaluator):
    raw = "存货增30%至189亿元，真实盈亏比劣于1:0.3"
    items, summary = _pipeline(evaluator, raw,
        "- 存货同比增长30%至189亿元。\n- 比值1，位置0.3。")
    assert "0.3" not in summary["verified_evidence"], items
    assert any("0.3" in s for s in summary["unsupported_evidence"]), summary


def test_r5_report_stated_odds_with_same_metric(evaluator):
    raw = "风险收益比1:0.3"
    items, summary = _pipeline(evaluator, raw, "- 风险收益比测算约1:0.3。")
    assert any("0.3" in s for s in summary["verified_evidence"]), items


def test_r5_decimal_pe_is_not_a_rounded_range_endpoint(evaluator):
    raw = "在手净现金1848亿元且扣现PE仅16.5倍具备极强反脆弱性"
    items, summary = _pipeline(evaluator, raw,
        "- 在手净现金1848亿元。\n- 极端估值底线PE为15-16倍。")
    assert any("16.5" in s for s in summary["unsupported_evidence"]), items
    assert not any("16.5" in s for s in summary["verified_evidence"])


def test_r5_cash_adjusted_pe_not_plain_pe(evaluator):
    items, summary = _pipeline(evaluator, "在手现金1848亿元且扣现PE16.5倍",
        "- 在手现金1848亿元。\n- 静态PE16.5倍。")
    assert any("16.5" in s for s in summary["unsupported_evidence"]), items


# R6 synthetic cases deliberately use invented values, not vendor report text.
@pytest.mark.parametrize("raw, report, targets", [
    ("期间费用率降1.8pct至8.2%", "样例期间费用率为8.2%，同比下降1.8个百分点。", ["1.8", "8.2"]),
    ("期间费用率升1.7pct", "样例期间费用率上升1.7个百分点。", ["1.7"]),
    ("扣非降幅仅7.4%", "样例扣非净利润同比-7.4%。", ["7.4"]),
    ("筹资净流入81.2亿元", "样例筹资活动现金流净流入81.2亿元。", ["81.2"]),
    ("现金流净额-17.3亿", "样例现金流净额-17.3亿元，较2024年同期改善。", ["17.3"]),
    ("亏损18亿且ASP提振4%-6%", "样例净亏损18亿元。\n样例ASP预期提升4%-6%。", ["4%", "6%"]),
    ("成本升4%将削减13.4%利润", "样例成本增加4%，营业利润下行13.4%。", ["13.4"]),
    ("超大单流出-2亿小单接盘+1.6亿", "样例超大单流出-2亿元，小单净流入+1.6亿元。", ["+1.6"]),
    ("极端支撑0.42-0.48xPB", "样例PB支撑测算0.42x~0.48x。", ["0.42", "0.48"]),
    ("72.5反弹目标", "样例第二目标价72.5元。", ["72.5"]),
    ("69.5止损", "样例止损位为69.5元。", ["69.5"]),
    ("成交800万股仅为前日40%", "样例成交量800万股，仅为前日巨量的40%。", ["40%"]),
])
def test_r6_local_context_and_stated_values(evaluator, raw, report, targets):
    items, summary = _pipeline(evaluator, raw, report)
    for target in targets:
        assert any(target in f for f in summary["verified_evidence"]), (target, items, summary)


def test_r6_delta_to_level_not_a_range():
    bns = extract_bound_numbers("期间费用率下降1.8pct至8.2%")
    assert len(bns) == 2
    assert all(b.range_span is None for b in bns)


@pytest.mark.parametrize("text", [
    "时滞2-4个月", "传导需2至4个月", "具备2-4月时滞",
    "在2-4月传导至成本", "以2-4月的时滞体现", "传导时滞约4个月", "时滞4个月以上",
])
def test_r6_transmission_window_not_atoms(text):
    assert extract_bound_numbers(text) == []


def test_r6_ratio_is_one_atom():
    bns = extract_bound_numbers("赔率优于1:1.7")
    assert [b.raw for b in bns] == ["1:1.7"]


def test_r6_positive_range_has_no_negative_leg():
    bns = extract_bound_numbers("ASP提升4%-6%")
    assert [b.val for b in bns] == [4.0, 6.0]
    assert [b.raw for b in bns] == ["4%", "6%"]


def test_r6_local_metric_not_borrowed(evaluator):
    items, summary = _pipeline(evaluator, "现金流18亿且筹资净流入7亿",
        "样例现金流18亿元。\n样例自由现金流7亿元。")
    assert any("7" in f for f in summary["unsupported_evidence"]), items


def test_r6_named_target_not_an_arbitrary_price(evaluator):
    items, summary = _pipeline(evaluator, "72.5反弹目标", "样例历史最高价72.5元。")
    assert summary["unsupported_evidence"], items


def test_r6_decline_verbs_and_no_negative_debt_noise(evaluator):
    items, summary = _pipeline(evaluator, "净利回撤6%", "样例净利润同比-6%。")
    assert summary["verified_evidence"], items
    from tradingagents.agents.utils.evidence_verifier import _sign_contextualize
    bn = extract_bound_numbers("负债25亿元")[0]
    assert _sign_contextualize(bn, "负债25亿元").val == 25e8


def test_r6_clock_not_a_reported_odds(evaluator):
    items, summary = _pipeline(evaluator, "盈亏比1:3", "样例于09:27封板。")
    assert summary["unsupported_evidence"], items


def test_r6_precise_percent_not_coarsely_rounded(evaluator):
    items, summary = _pipeline(evaluator, "股息率5.5%", "样例历史股息率6%。")
    assert summary["unsupported_evidence"], items


def test_r6_inner_subject_and_forward_year(evaluator):
    items, summary = _pipeline(evaluator, "2026H1筹资净流入81.2亿元",
        "样例自由现金流欠佳，依赖筹资活动（2026H1筹资净流入81.2亿元）维持现金。")
    assert summary["verified_evidence"], items
    items, summary = _pipeline(evaluator, "9月24日成交仅为前日40%",
        "样例成交量仅为前日巨量的40%，是2026年以来地量。")
    assert summary["verified_evidence"], items


def test_r6_named_qualitative_shape(evaluator):
    items, summary = _pipeline(evaluator, "均线长多短敛形态稳固", "样例均线系统呈长多短敛结构。")
    assert summary["verified_evidence"], items


def test_r6_fragment_binding_retains_own_year(evaluator):
    items, summary = _pipeline(evaluator,
        "2026H1期间费用率1.7%且2025H1毛利率9.2%",
        "样例2025H1期间费用率1.7%。\n样例2025H1毛利率9.2%。")
    bad = next(i for i in items if i["raw"] == "1.7%")
    assert bad["fact_binding"]["metric"] == "期间费用率"
    assert bad["fact_binding"]["period"] == "2026H1"
    assert "1.7%" in summary["unsupported_evidence"]


def test_r6_adjacent_negative_word_but_not_debt():
    from tradingagents.agents.utils.evidence_verifier import _sign_contextualize
    bn = extract_bound_numbers("现金流负13亿元")[0]
    assert _sign_contextualize(bn, "现金流负13亿元").val == -13e8


def test_r6_window_alone_does_not_enter_coverage(evaluator):
    assert evaluator._verify_evidence_or_decompose(
        "时滞2-4个月", {}, {}, None, "T-1", None,
    ) == []


def test_r6_pure_calendar_label_not_unsupported_atom(evaluator):
    assert evaluator._verify_evidence_or_decompose(
        "2026H1", {}, {}, None, "T-1", None,
    ) == []


def test_r6_stated_window_not_a_verified_fact(evaluator):
    assert evaluator._verify_evidence_or_decompose(
        "传导需2-4个月", {"news_report": "样例传导需2-4个月。"}, {}, None, "T-1", None,
    ) == []


def test_r6_shape_does_not_prove_own_prediction(evaluator):
    items, summary = _pipeline(evaluator, "均线长多短敛因此必然暴涨",
        "样例均线系统呈长多短敛结构。")
    assert summary["unsupported_evidence"], items


def test_r6_explicit_pe_suffix_overrides_generic_valuation(evaluator):
    items, summary = _pipeline(evaluator, "悲观估值中枢10-12倍PE即240-280元",
        "样例压力测试PE取10-12倍，对应价位240-280元。")
    for target in ("10", "12倍"):
        assert any(target in f for f in summary["verified_evidence"]), items


def test_r6_reported_ratio_retains_period(evaluator):
    items, summary = _pipeline(evaluator, "2026H1风险收益比1:3",
        "样例2025H1风险收益比1:3。")
    assert summary["unsupported_evidence"], items


def test_r6_reported_ratio_retains_subject():
    bns = extract_bound_numbers("甲公司风险收益比1:3")
    assert bns[0].entity is not None


def test_r6_ratio_cannot_borrow_another_named_subject(evaluator):
    items, summary = _pipeline(evaluator, "样例甲公司风险收益比1:3",
        "样例乙公司风险收益比1:3。")
    assert summary["unsupported_evidence"], items


# R7 cases use invented numbers and independently constructed report sentences.
@pytest.mark.parametrize("text", ["2028中报", "2028年中报", "2028年二季报"])
def test_r7_report_year_label_not_a_fact(text):
    assert extract_bound_numbers(text) == []


def test_r7_repricing_lag_not_numeric_facts():
    assert extract_bound_numbers("负债存款降息滞后2-4月无法对冲重定价压力") == []


@pytest.mark.parametrize("raw, report, targets", [
    ("构建资产支出3.1亿", "样例购建固定资产等投资现金流支出3.1亿元。", ["3.1"]),
    ("净现比0.52", "样例经营现金流对净利润的保障倍数0.52倍。", ["0.52"]),
    ("持股降至18.6%", "样例机构合计持股占总股本18.6%。", ["18.6"]),
    ("开支占OCF比重13.4%", "样例资本开支占经营现金流比重13.4%。", ["13.4"]),
    ("回购锁定11.8倍PE估值底", "样例动态PE约11.8倍。", ["11.8"]),
    ("70.6反弹目标", "样例中期强阻70.6元。", ["70.6"]),
    ("下探25.3至21.2元", "样例关键防守位25.3-25.4元。", ["25.3"]),
    ("极端估值底980-1020元支撑", "样例估值测算支撑位980-1020元。", ["980", "1020"]),
    ("跌幅达3.4%", "样例价格下挫-3.4%。", ["3.4"]),
    ("潜在下行-8.6%至-18.2%", "样例测算潜在最大回撤空间为-18.2%。", ["18.2"]),
])
def test_r7_reviewed_numeric_families(evaluator, raw, report, targets):
    items, summary = _pipeline(evaluator, raw, report)
    for target in targets:
        assert any(target in f for f in summary["verified_evidence"]), (target, items)


def test_r7_price_endpoints_do_not_borrow_the_supported_other_end(evaluator):
    items, summary = _pipeline(evaluator, "下探25.3至21.2元", "样例关键防守位25.3-25.4元。")
    assert any("25.3" in f for f in summary["verified_evidence"]), items
    assert any("21.2" in f for f in summary["unsupported_evidence"]), items


def test_r7_ratio_kind_not_cash_amount_or_growth(evaluator):
    items, summary = _pipeline(evaluator, "持股比例18.6%", "样例净利同比增长18.6%。")
    assert summary["unsupported_evidence"], items
    items, summary = _pipeline(evaluator, "开支占OCF比重13.4%", "样例经营现金流同比增长13.4%。")
    assert summary["unsupported_evidence"], items


def test_r7_financial_half_up_precision():
    from tradingagents.agents.utils.evidence_verifier import _is_rounding_equivalent
    ev = extract_bound_numbers("ROE2.68%")[0]
    line = extract_bound_numbers("ROE2.675%")[0]
    assert _is_rounding_equivalent(ev, line)


def test_r7_comparison_baseline_not_bound_to_current_period(evaluator):
    items, summary = _pipeline(evaluator, "2028H1 ROE从7.24%降至6.3%",
        "样例2028H1 ROE为6.3%（2027H1为7.235%）。")
    assert any("7.24" in f for f in summary["verified_evidence"]), items


@pytest.mark.parametrize("raw, report", [
    ("9月LPR按兵不动且三峡高频入库流量缺失",
     "样例9月LPR利率按兵不动。\n样例三峡入库流量月度高频缺失。"),
    ("收全天最低", "样例收盘位于全天极低位。"),
])
def test_r7_stated_qualitative_facts(evaluator, raw, report):
    items, summary = _pipeline(evaluator, raw, report)
    assert summary["verified_evidence"] and not summary["unsupported_evidence"], items


def test_r7_qualitative_anchor_does_not_prove_extra_judgment(evaluator):
    items, summary = _pipeline(evaluator, "三峡高频入库流量缺失必然导致净利崩塌",
        "样例三峡入库流量月度高频缺失。")
    assert summary["unsupported_evidence"], items
    items, summary = _pipeline(evaluator, "8月LPR按兵不动", "样例9月LPR利率按兵不动。")
    assert summary["unsupported_evidence"], items


def test_r7_rounding_uses_normalized_unit_precision(evaluator):
    items, summary = _pipeline(evaluator, "融资成本下行25bp", "样例融资成本下行13个基点。")
    assert summary["unsupported_evidence"], items
    items, summary = _pipeline(evaluator, "风险收益比1:3", "样例风险收益比1:2。")
    assert summary["unsupported_evidence"], items


def test_r7_stated_level_not_any_money_or_price(evaluator):
    items, summary = _pipeline(evaluator, "目标价70.6元", "样例历史最高价70.6元。")
    assert summary["unsupported_evidence"], items
    items, summary = _pipeline(evaluator, "止损价70.6元", "样例反弹目标70.6元。")
    assert summary["unsupported_evidence"], items


def test_r7_derived_distance_not_an_index_decline(evaluator):
    raw = "现价71.12元距前低69.34元仅-2.2%而50日线目标达+4.49%"
    distance = next(b for b in extract_bound_numbers(raw) if b.raw == "-2.2%")
    assert distance.metric == "价格距离"
    items, summary = _pipeline(evaluator, raw, "样例中证500跌2.15%。")
    assert any("-2.2%" in f for f in summary["unsupported_evidence"]), items


def test_r7_named_window_clause_is_not_a_separate_fact(evaluator):
    assert evaluator._verify_evidence_or_decompose(
        "存款挂牌降息存在2-4月时滞", {}, {}, None, "T-1", None,
    ) == []


def test_r7_lag_record_cannot_prove_inserted_prediction(evaluator):
    items, summary = _pipeline(evaluator,
        "负债存款降息滞后公司必亏无法对冲重定价压力",
        "样例存款成本降幅滞后于资产端降息。")
    assert summary["unsupported_evidence"], items


# R8: metric ownership and presentation parsing; values are synthetic.
@pytest.mark.parametrize("raw, expected", [
    ("成本上涨24%毛利率仅降2.6%", [(24, "成本"), (2.6, "毛利率")]),
    ("PE达18.7倍PB达2.1倍", [(18.7, "pe"), (2.1, "pb")]),
    ("PB仅0.82倍PE仅9.7倍", [(0.82, "pb"), (9.7, "pe")]),
    ("估值下轨12~13倍PE", [(12, "pe"), (13, "pe")]),
])
def test_r8_metric_belongs_to_its_own_number(raw, expected):
    bns = extract_bound_numbers(raw)
    assert [(abs(b.val), b.metric) for b in bns] == expected


@pytest.mark.parametrize("raw, report, targets", [
    ("成本上升4%即削减利润17.6%", "样例成本上涨9%将改变利润。\n样例成本上涨4%，营业利润下行17.6%。", ["4%", "17.6%"]),
    ("中报利润760亿", "样例归母净利润760.4亿元。", ["760"]),
    ("流动资产840亿覆盖620亿流动负债", "样例流动负债承压：流动负债达620.3亿元，流动资产840.2亿元。", ["620"]),
    ("DOL达1.6将拉低税前利润18.4%", "样例经营杠杆系数（DOL）为1.6。\n样例营业利润变动-18.4%（对应税前利润下降20亿元）。", ["1.6", "18.4"]),
    ("净息差收窄3bp", "样例净息差（NIM）边际收窄约3bp。", ["3bp"]),
    ("ASP跌16%", "样例综合售价下降16%。", ["16%"]),
])
def test_r8_bound_pipeline_values(evaluator, raw, report, targets):
    items, summary = _pipeline(evaluator, raw, report)
    for target in targets:
        assert any(target in f for f in summary["verified_evidence"]), (target, items)
    assert not summary["contradicted_evidence"], items


def test_r8_latex_retains_sign_and_money_unit(evaluator):
    bns = extract_bound_numbers("样例大单净额 $-1.203$ 亿元。")
    assert bns[0].unit == "元" and bns[0].val == pytest.approx(-1.203e8)
    items, summary = _pipeline(evaluator, "大单净流出1.2亿且小单流入0.61亿",
        "样例大单净流出 $-1.203$ 亿元。\n样例小单净流入 $+0.607$ 亿元。")
    assert not summary["unsupported_evidence"], items


def test_r8_stated_limit_up_count_but_not_added_judgment(evaluator):
    items, summary = _pipeline(evaluator, "全市场47只涨停，避险资金必被虹吸",
        "样例涨停总数：共47只涨停。")
    assert any("47" in f for f in summary["verified_evidence"]), items
    assert summary["unsupported_evidence"], items
    assert "全市场47只涨停，避险资金必被虹吸" not in summary["verified_evidence"]


def test_r8_count_not_a_bare_integer_coincidence(evaluator):
    items, summary = _pipeline(evaluator, "47", "样例共47只涨停。")
    assert summary["unsupported_evidence"], items


@pytest.mark.parametrize("raw, report", [
    ("10日OBV下行", "样例当前10日OBV趋势持续下降。"),
    ("高频动销数据缺失", "样例终端动销缺乏最新统计指标，绝对增量无法精确测算。"),
])
def test_r8_closed_qualitative_records(evaluator, raw, report):
    items, summary = _pipeline(evaluator, raw, report)
    assert summary["verified_evidence"] and not summary["unsupported_evidence"], items


def test_r8_no_metric_or_predicate_borrowing(evaluator):
    items, summary = _pipeline(evaluator, "净息差收窄3bp", "样例LPR下行3bp。")
    assert summary["unsupported_evidence"], items
    items, summary = _pipeline(evaluator, "高频动销数据缺失必然净利崩塌", "样例动销数据缺失。")
    assert summary["unsupported_evidence"], items


# ── 漏认向 ─────────────────────────────────────────────────────────────


def test_range_endpoint_same_metric_verified(evaluator):
    """证据原子是报告区间端点 + 同指标锚点可桥接（毛利→毛利率）→ verified。"""
    assert _status(
        evaluator,
        "宏观报告显示布油破百与铜季涨9.98%将滞后侵蚀毛利0.8%-1.1%折合16-22亿",
        {"news_report": "- **财务指标映射**：未完全转嫁的成本将侵蚀毛利率约 **0.8% ~ 1.1%**。"},
        claim_text="3816亿占款加剧脆弱且大宗侵蚀毛利",
    )["0.8%"] == STATUS_VERIFIED


def test_sign_predicate_normalizes_negative(evaluator):
    """口径②：方向谓词「侵蚀…1.1%」显式表降方向，-1.1% 原子获验。"""
    assert _status(
        evaluator,
        "宏观报告显示布油破百与铜季涨9.98%将滞后侵蚀毛利0.8%-1.1%折合16-22亿",
        {"news_report": "- **财务指标映射**：未完全转嫁的成本将侵蚀毛利率约 **0.8% ~ 1.1%**。"},
        claim_text="3816亿占款加剧脆弱且大宗侵蚀毛利",
    )["-1.1%"] == STATUS_VERIFIED


def test_period_base_reference_yoy(evaluator):
    """「较2025年同期增长34.90%」——数字绑基期 2025，证据侧绑 2026H1，
    行内两期共存属绑定伪影，不得判期间分歧。"""
    res = _status(
        evaluator,
        "2026H1存货达1899.91亿同比增长34.90%存减值风险",
        {"fundamentals_report": "- 截至2026年6月30日，存货规模达1,899.91亿元，较2025年底的1,384.21亿元增长37.26%，较2025年同期（1,408.39亿元）增长34.90%。"},
        claim_text="存货逼近1900亿且费用率攀升",
    )
    # 整句获验或原子获验皆可——关键是 34.90% 不再被期间门击落
    assert STATUS_VERIFIED in res.values()


def test_period_base_reference_from_to(evaluator):
    """「净利率由52.56%降至50.75%」——52.56 绑基期 2025H1。"""
    res = _status(
        evaluator,
        "基本面报告：2026H1销售净利率由52.56%下滑至50.75%",
        {"fundamentals_report": "- 销售净利率由 2025H1 的 52.56% 降至 2026H1 的 50.75%。"},
        claim_text="净利率降至50.75%暴露成本杠杆反噬",
    )
    assert STATUS_VERIFIED in res.values()


def test_report_stated_scenario_estimate(evaluator):
    """口径①：报告原文写出的测算值「约为6.90亿元」算有据，scenario→actual
    门不拦。"""
    res = _status(
        evaluator,
        "基本面报告测算2026Q2归母净利骤降至6.90亿元(Q1为80.63亿)",
        {"fundamentals_report": "- 2026H1 归母净利润为 87.53 亿元（2026Q1 为 80.63 亿元），2026Q2 单季归母净利润约为 6.90 亿元。"},
        claim_text="Q2净利仅6.9亿且费用率升至24.6%击穿估值底",
    )
    assert STATUS_VERIFIED in res.values()


def test_derived_sum_flow_components(evaluator):
    """口径①第二款：大单+中单合计1.79亿（0.59+1.20）重算在容差内。"""
    assert _status(
        evaluator,
        "大中单合计承接1.79亿",
        {"smart_money_report": "- 大单及中单出现被动挂单撮合承接（东财大单 +0.59 亿、中单 +1.20 亿）；"},
        claim_text="0.94PB深度破净大单流出占流通盘仅万分之1",
    )["大中单合计承接1.79亿"] == STATUS_VERIFIED


def test_full_coverage_atoms_not_dropped(evaluator):
    """整句未获验但全部原子已命中时必须原子化——不能让「uf 清空」反而
    丢分（ae6a83e1 INV-11 回归夹具）。"""
    items = _status(
        evaluator,
        "宏观报告显示布油破百与铜季涨9.98%将滞后侵蚀毛利0.8%-1.1%折合16-22亿",
        {"news_report": "- LME铜价达14646.50美元/吨（季度环比+9.98%）；布伦特原油达100.21美元/桶。\n- **财务指标映射**：未完全转嫁的成本将侵蚀毛利率约 **0.8% ~ 1.1%**，潜在毛利压制规模约16-22亿元。"},
        claim_text="大宗侵蚀毛利",
    )
    # 整条获验与原子获验均保留事实，不要求固定的输出粒度。
    for target in ("0.8%", "9.98%"):
        assert any(target in raw and status == STATUS_VERIFIED for raw, status in items.items())


# ── DAV-1397 总控标注的 9 例机制性误拒（修复后须 verified）─────────────


@pytest.mark.parametrize(
    "raw, report, claim",
    [
        ("基本面报告显示2026H1经营现金流-58.18亿失血剧增且存货达189.04亿环比大增30%",
         "- 经营活动产生的现金流量净额为 -58.18 亿元，存货 189.04 亿元环比增长 30%。",
         "经营现金流失血与被动累库共振"),
        ("2026H1经营现金流净额同比增17.28%达373.35亿元",
         "- 经营活动现金流量净额 373.35 亿元，同比增 17.28%。",
         "经营造血充沛托底"),
        ("2026H1综合毛利率由52.33%降至50.95%，ROE下滑至6.71%",
         "- 2026H1 年化 ROE 为 6.71%，综合毛利率 50.95%（2025H1 为 52.33%）。",
         "盈利质量与资本回报双降"),
        ("2026H1归母净利925.85亿完成去年68.7%且现金流3680亿",
         "- 中报净利润达到 925.85 亿元，完成上年度 68.7%，在手现金 3680 亿元。",
         "利润体量与现金储备构成估值底"),
        ("2026H1存货达1308.19亿同比增81.01%远超营收增速54.80%",
         "- 2026H1营业总收入同比增长 54.80%，存货 1308.19 亿元同比增 81.01%。",
         "存货增速超营收揭示被动累库"),
        ("中线上行目标138-145元对回撤底线113.6元赔率超3:1",
         "- 于 9 月 15 日探至低点 113.60 元后展开超跌反弹。",
         "地量探底后中线赔率达3:1"),
        ("基本面报告显示2026H1自由现金流实质为-44.57亿元",
         "- 2026H1 自由现金流为 -44.57 亿元。",
         "现金转化能力恶化"),
        ("主力报告东财超大单净流出-5.04亿",
         "- 超大单净流入 -5.04 亿元，小单 +3.70 亿。",
         "超大单净流出散户接盘"),
        ("推演注入15-25亿元流动性，资产负债率可阶段性压降0.3-0.5%",
         "- 推演注入 15-25 亿元流动性，资产负债率可阶段性压降 0.3-0.5%。",
         "注资推演改善偿债能力"),
    ],
    ids=[
        "S16_-58.18亿", "S18_373.35亿", "S21_ROE6.71", "S24_925.85亿",
        "S04_54.80", "S02_113.6元", "S14_-44.57亿", "S13_-5.04亿", "S25_推演注资",
    ],
)
def test_dav1397_misrejects_now_verified(evaluator, raw, report, claim):
    """bn 门全过 + 命中行含原子指标词时不得被子句级关键词门击落。"""
    res = _status(
        evaluator, raw,
        {"fundamentals_report": report,
         "market_report": report,
         "smart_money_report": report},
        claim_text=claim,
    )
    assert STATUS_VERIFIED in res.values(), res


def test_dav1397_bare_15_unsupported(evaluator):
    """总控裁决：无单位无指标的裸「15」维持 unsupported；带指标且绑定
    一致（15倍PE）才可核实。"""
    res = _status(
        evaluator, "估值下修空间约15",
        {"macro_report": "- 悲观情景下 PE 中枢或测试 15 倍。"},
    )
    assert res.get("15") != STATUS_VERIFIED
    res2 = _status(
        evaluator, "悲观情景PE中枢或下探15倍",
        {"macro_report": "- 悲观情景下 PE 中枢或测试 15 倍。"},
    )
    assert STATUS_VERIFIED in res2.values()


# ── DAV-1397 总控判定的正确击落（修复后须仍 unsupported）──────────────


@pytest.mark.parametrize(
    "raw, report, bad",
    [
        # S01/S29：裸「1.3」无锚点，对侧量比锚定不许越绑
        ("1.3", "- 成交 3,123,935 股（量比 1.3，温和放量）。", "1.3"),
        # S27：裸「8%」无锚点，不得撞宏观报告任意百分数
        ("8%", "- 美债 10 年期收益率单日上行至 5.180%，单日 +1.37%。", "8%"),
        # S03：日期碎片「9」
        ("9月中旬起跌", "- 9 月 16 日放量中阴破位，9 月 17 日缩量。", "9"),
        # S22：裸「8」
        ("8", "- 上涨家数 8 家，下跌家数 4120 家。", "8"),
        # S05：报告是「连续5日」，原子的「4日/5.4851亿」不实
        ("主力资金连续4日净流入累计达5.4851亿元",
         "- 连续 5 日主力净流入，特大单净买入超 2 亿元。", "5.4851亿元"),
        # S09：量比 0.9 属 09-21，原子锚定 9月23-24日 → 期间不一致
        ("9月23-24日量比0.6-0.9",
         "- 09-21 量比 0.9，09-24 量比 0.7。", "0.9"),
        # S28：「紧邻0.618支撑118元」为推演判断非报告字面
        ("收盘119.98元紧邻波段0.618支撑118元",
         "- 反弹高点触及 125.61 元，9 月中旬在 113.60 元触底。", "119.98元"),
    ],
    ids=[
        "S01_裸1.3", "S27_裸8pct", "S03_日期9", "S22_裸8",
        "S05_4日vs5日", "S09_量比期间", "S28_推演支撑",
    ],
)
def test_dav1397_correct_kills_stay_unsupported(evaluator, raw, report, bad):
    res = _status(
        evaluator, raw,
        {"fundamentals_report": report,
         "market_report": report,
         "smart_money_report": report,
         "volume_price_report": report,
         "sentiment_report": report,
         "macro_report": report,
         "news_report": report},
    )
    assert res.get(bad) != STATUS_VERIFIED, res


# ── 误认向 ─────────────────────────────────────────────────────────────


def test_range_atom_survives_date_fragment_in_same_clause(evaluator):
    """DAV-1397：同句日期碎片（9月23-24日）不得击落句内取值域区间原子
    （量比0.6-0.9）——时域排除只对原子自身区间片段生效。"""
    res = _status(
        evaluator, "9月23-24日量比0.6-0.9呈卖压衰竭候选",
        {"volume_price_report": "- 09-23 量比 0.6，09-24 量比 0.9，连续两日极度缩量。"},
    )
    assert STATUS_VERIFIED in res.values()


def test_time_span_not_hit_source(evaluator):
    """时间区间「6-12个月」不得作数字命中来源。"""
    res = _status(
        evaluator, "中线目标6个月内修复至68元",
        {"macro_report": "- 估值修复周期通常为 6-12 个月。"},
    )
    assert all(s == STATUS_UNSUPPORTED for s in res.values())


def test_percentile_range_not_pe(evaluator):
    """「向15-16倍PE寻底」不得撞「估值分位数 15%~20%」的区间端点。"""
    res = _status(
        evaluator, "压制估值向15-16倍PE寻底",
        {"fundamentals_report": "- TTM PE约为 18.7 - 19.0 倍，处于公司过去十年历史估值分位数的 15%~20% 偏低区间。"},
    )
    assert res.get("15") != STATUS_VERIFIED


def test_abs_value_equivalence_removed(evaluator):
    """abs() 绝对值等价已移除：25.31% 不得撞 -0.24%。"""
    res = _status(
        evaluator, "扣非利润下降25.31%",
        {"fundamentals_report": "- 扣非净利率环比微降 -0.24%，整体平稳。"},
    )
    assert res.get("25.31%") != STATUS_VERIFIED


def test_cross_unit_rounding_removed(evaluator):
    """2.55pct 不得舍入撞上章节序号「3」。"""
    res = _status(
        evaluator, "期间费用率上升2.55pct",
        {"fundamentals_report": "## 3. 费用结构分析\n期间费用率维持高位。"},
    )
    assert res.get("2.55pct") != STATUS_VERIFIED


def test_month_fragment_masked(evaluator):
    """「8月底」的裸「8」按日期碎片掩码，不得抽成数字原子。"""
    res = _status(
        evaluator, "8月底回购下限71.01元",
        {"news_report": "- 8月行情转弱，板块跌8%。"},
    )
    assert "8" not in res or res["8"] == STATUS_UNSUPPORTED


def test_heading_ordinal_masked(evaluator):
    """「#### 3. 政策催化」的小节序号不是事实数字；「赔率3:1」的「3」
    不得借其获验。"""
    res = _status(
        evaluator, "向上修复空间15-20%对应赔率3:1",
        {"macro_report": "#### 3. 政策催化传导链路\n- 悲观情景下大盘杀估值。"},
    )
    assert res.get("3") != STATUS_VERIFIED


def test_unanchored_bare_integer_pair_rejected(evaluator):
    """双侧全裸（无指标/实体/期间/量纲）的同值整数不算命中。"""
    from tradingagents.agents.utils.evidence_verifier import (
        _is_bound_num_match, extract_bound_numbers,
    )
    e = extract_bound_numbers("3")[0]
    l = extract_bound_numbers("3")[0]
    assert not _is_bound_num_match(e, l)


def test_entity_mismatch_blocks(evaluator):
    """「赔率3:1」的「3」（实体：向上修复空间）不得撞「3连板」（实体：首板）。"""
    res = _status(
        evaluator, "前低113.6元支撑强劲，向上修复空间15-20%对应赔率3:1",
        {"sentiment_report": "- 梯队分布：首板 38 只，2连板 8 只，3连板 1 只。"},
    )
    assert res.get("3") != STATUS_VERIFIED


def test_coincidence_numbers_stay_unsupported(evaluator):
    """巧合真无据锚：「51」「38%」在报告中无对应事实，保持 unsupported。"""
    res = _status(
        evaluator, "主力流出差额收窄至51",
        {"smart_money_report": "- 主力净流出 12.69 亿元，散户净流入 8.33 亿元。"},
    )
    assert res.get("51") != STATUS_VERIFIED


def test_entity_noise_verbatim_rescued(evaluator):
    """DAV-1395 必修1：双侧实体均绑但一侧是抽取噪声（「而流动」残片）且
    指标皆未绑、数值逐字相等——不否决。"""
    res = _status(
        evaluator, "净营运资本为-647.82亿元",
        {"fundamentals_report": "- 流动资产 4355.93 亿元，而流动负债为 5003.75 亿元，净营运资本为 -647.82 亿元。"},
    )
    assert STATUS_VERIFIED in res.values()


def test_identical_range_no_anchor_verified(evaluator):
    """DAV-1395 必修2：双侧 range_span 逐字相同且单位相同，即使双侧皆无
    指标/实体锚点也视为绑定一致（「月跌9%-11%」撞表格逐字同区间）。"""
    res = _status(
        evaluator, "海外烈酒巨头月跌9%-11%",
        {"macro_report": "| 悲观情景 | 海外烈酒巨头月跌9%-11%，估值中枢下移 |"},
    )
    assert STATUS_VERIFIED in res.values()


def test_entity_mismatch_different_value_still_blocked(evaluator):
    """免否决仅限逐字相等：实体不一致且数值不同仍否决（防噪声实体放行
    异值）。"""
    res = _status(
        evaluator, "前低113.6元支撑强劲，向上修复空间对应赔率3:1",
        {"sentiment_report": "- 梯队分布：首板 38 只，2连板 8 只，5连板 1 只。"},
    )
    assert res.get("3") != STATUS_VERIFIED


def test_buyback_floor_hits_reported_price_range(evaluator):
    """总控真值：「跌破回购底价71.01元」应走同指标（回购）区间端点路径
    命中报告写明的回购价格区间 71.01~85.25 元。"""
    res = _status(
        evaluator, "现价69.67元跌破回购底价71.01元",
        {"macro_report": "- 上市公司公告已耗资 11.01 亿元回购股份 1470.74 万股（价格区间 71.01~85.25 元/股）。"},
    )
    assert STATUS_VERIFIED in res.values()


def test_scenario_unwritten_param_still_blocked(evaluator):
    """证据自行代入的情景参数（报告未写出）仍不得用实绩行佐证——口径①
    只放行报告原文写出的情景/测算值。"""
    res = _status(
        evaluator, "悲观情景下净利润降至45亿元",
        {"fundamentals_report": "- 2026H1 归母净利润 45.02 亿元，同比持平。"},
    )
    assert all(s != STATUS_VERIFIED for s in res.values())


def test_r8_verified_numbers_do_not_carry_unextracted_magnitude(evaluator):
    items, summary = _pipeline(evaluator, "中报利润760亿与现金流1200亿托底且渠道改革降本百亿利好持续释放",
        "样例归母净利润760.4亿元，经营现金流1200.3亿元。")
    assert any("百亿" in f for f in summary["unsupported_evidence"]), items


def test_r8_judgment_clause_not_verified_by_two_generic_keywords(evaluator):
    items, summary = _pipeline(evaluator, "高估值易遭流动性虹吸", "样例小盘题材对流动性形成虹吸，估值分化。")
    assert summary["unsupported_evidence"], items


@pytest.mark.parametrize("text", ["收复50SMA与中轨", "站上20EMA后回落", "突破30WMA并企稳"])
def test_r9_joined_ma_window_not_a_fact(text):
    assert extract_bound_numbers(text) == []


def test_r9_energy_units_are_not_bare_integer_anchors(evaluator):
    bns = extract_bound_numbers("海外2GWh储能订单")
    assert bns[0].unit == "Wh" and bns[0].val == 2e9
    items, summary = _pipeline(evaluator, "海外2GWh储能订单", "样例海外2GWh储能项目已签约。")
    assert summary["verified_evidence"], items
    items, summary = _pipeline(evaluator, "海外2GWh储能订单", "样例额定功率2GW。")
    assert summary["unsupported_evidence"], items


def test_r9_project_profit_upper_endpoint(evaluator):
    items, summary = _pipeline(evaluator, "海外2GWh订单对应利润不足8.2亿",
        "样例2GWh合同预计增厚公司毛利6.1亿至8.2亿元。")
    assert any("8.2" in f for f in summary["verified_evidence"]), items
    items, summary = _pipeline(evaluator, "净利润不足8.2亿", "样例预计公司毛利6.1亿至8.2亿元。")
    assert summary["unsupported_evidence"], items


def test_r9_symmetric_rate_cut_requires_both_reported_sides(evaluator):
    raw = "12BP对称降息净利润影响小于1%"
    report = "样例资产端生息收益率下行12BP。\n样例存款利率和资金成本跟随调降12BP，对净利润影响小于1%。"
    items, summary = _pipeline(evaluator, raw, report)
    assert any("12BP" in f for f in summary["verified_evidence"]), items
    items, summary = _pipeline(evaluator, raw, report.replace("跟随调降12BP", "跟随调降6BP"))
    assert any("12BP" in f for f in summary["unsupported_evidence"]), items


def test_r9_relative_dispersion_is_not_pe(evaluator):
    bns = extract_bound_numbers("样例系统判定relative dispersion达1.27")
    assert bns[0].metric == "离散度"
    items, summary = _pipeline(evaluator, "多源离散度1.27", "样例relative dispersion达1.27。")
    assert summary["verified_evidence"], items


def test_r9_english_metric_not_part_of_another_word():
    bns = extract_bound_numbers("样例Supernode海外2GWh项目")
    assert all(b.metric != "pe" for b in bns)


def test_r9_refund_percentage_not_rebound_space(evaluator):
    raw = "潜在下行-8.6%至-18.2%数倍于反弹空间1%"
    items, summary = _pipeline(evaluator, raw, "样例定金上限1%。\n样例测算潜在回撤空间-18.2%。")
    assert any("1%" == f for f in summary["unsupported_evidence"]), items
    assert not any("1%" == f for f in summary["verified_evidence"])


def test_r9_no_new_fraction_percentage_conversion(evaluator):
    items, summary = _pipeline(evaluator, "上影线比例60%", "样例上影线比例0.60。")
    assert summary["unsupported_evidence"], items
