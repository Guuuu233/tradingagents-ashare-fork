import pytest

from tradingagents.agents.utils.evidence_verifier import (
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
    EvidenceFactualTruthEvaluator,
)


@pytest.fixture
def evaluator():
    return EvidenceFactualTruthEvaluator()


def test_unsupported_policy_clause_is_not_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="国家医保支持创新药实行DRG除外支付免计定额",
        seven_reports={"macro_report": "医保支持创新药发展，支付端政策保持稳定。"},
    )

    assert result["status"] == STATUS_UNSUPPORTED


def test_hypothetical_clause_is_not_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="假设生息资产收益率下调10bp，税后利润减少93.75亿元",
        seven_reports={
            "fundamentals_report": "生息资产综合收益率下调10bps，税后利润影响约-93.75亿元。"
        },
    )

    assert result["status"] == STATUS_UNSUPPORTED


def test_bare_numeric_token_is_not_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="90%",
        seven_reports={"macro_report": "评估基准日：2026-11-23，流动性覆盖率达到90%。"},
    )

    assert result["status"] == STATUS_UNSUPPORTED


def test_numeric_token_with_metric_is_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="流动性覆盖率达到90%",
        seven_reports={"macro_report": "评估基准日：2026-11-23，流动性覆盖率达到90%。"},
    )

    assert result["status"] == STATUS_VERIFIED


def test_judgment_word_is_not_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="11-20与11-23天量对倒2.78亿股形成高位筹码套牢峰",
        seven_reports={
            "volume_price_report": "11-20与11-23成交量分别为1.52亿股与1.26亿股，高位换手剧烈放大。"
        },
    )

    assert result["status"] == STATUS_UNSUPPORTED


def test_bullish_stance_cannot_use_net_outflow_as_verified_evidence(evaluator):
    result = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-6",
                "stance": "bullish",
                "evidence": ["5日主力累计净流出4.67亿占流通市值比仅0.03%"],
            }
        ],
        seven_reports={"smart_money_report": "5日累计主力净额为-4.67亿元。"},
    )[0]

    assert result["status"] == STATUS_UNSUPPORTED
    assert result["details"] == "资金流方向与论点 stance 相反，不作为已核实事实"


def test_dav2479_r3_601225_82aef95e_dividend_spread_blocked(evaluator):
    """r3 必修回归 1：601225 82aef95e —— 报告原文含若/测算/推算，且同句兄弟子句同源降级。"""
    ev_str = "宏观与基本面报告：5年期LPR3.50%，按60%分红率测算预期股息率达5.18%-5.41%，息差达168-191bps"
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-2", "stance": "bullish", "evidence": [ev_str]}],
        seven_reports={
            "macro_report": "当前5年期LPR为3.50%，处于历史低位。",
            "news_report": (
                "- **分红与股息率推演**：若按照公司常规 60% 现金分红比例测算，对应年度分红总额约为 135-141 亿元；"
                "以当前 10月27日 收盘 26.88 元股价（总市值约 2606 亿元）推算，"
                "**预期股息率（Dividend Yield）约为 5.18% - 5.41%**。"
                "在 5 年期 LPR 为 3.50% 的低利率背景下，息差优势达 168-191 bps，中线具备强配置吸引力。"
            ),
        },
    )
    by_raw = {it["raw"]: it for it in items}
    assert by_raw["5年期LPR3.50%"]["status"] == STATUS_VERIFIED
    assert by_raw["按60%分红率测算预期股息率达5.18%-5.41%"]["status"] == STATUS_UNSUPPORTED
    assert by_raw["息差达168-191bps"]["status"] == STATUS_UNSUPPORTED
    assert by_raw["息差达168-191bps"]["details"]

    # 即使报告原文另行写息差且不含标记词，同一论点句中标记词后的同源兄弟子句亦须跟着降级
    items_sibling = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-2", "stance": "bullish", "evidence": [ev_str]}],
        seven_reports={
            "macro_report": "当前5年期LPR为3.50%，处于历史低位。",
            "news_report": "在 5 年期 LPR 为 3.50% 的低利率背景下，息差优势达 168-191 bps。",
        },
    )
    by_raw_sib = {it["raw"]: it for it in items_sibling}
    assert by_raw_sib["5年期LPR3.50%"]["status"] == STATUS_VERIFIED
    assert by_raw_sib["息差达168-191bps"]["status"] == STATUS_UNSUPPORTED
    assert "同源兄弟子句" in by_raw_sib["息差达168-191bps"]["details"]


def test_dav2479_r3_601899_b1f58806_aisc_estimate_blocked(evaluator):
    """r3 必修回归 2：601899 b1f58806 短线 —— 原文命中句含『预计』，不得标 verified。"""
    ev_str = "COMEX金站上4103美元，布油跌破97美元促AISC成本下降1.5-2%"
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-2", "stance": "bullish", "evidence": [ev_str]}],
        seven_reports={
            "macro_report": (
                "- 金价（COMEX 4103美元/盎司）处于历史顶级区间。\n"
                "- **能源成本下行**：布伦特原油自高位回撤至96.53美元/桶（单日-1.42%，5日-7.33%）"
                "→ 矿山开采能耗与物流柴油成本占采矿原矿成本约15%-25% "
                "→ 预计单克金与单吨铜的综合维持成本（AISC）承压减轻约1.5-2.0个百分点。"
            ),
            "fundamentals_report": (
                "阶段一宏观数据显示布伦特原油自高位回撤至96.53美元/桶（20日下跌9.05%），"
                "显著缓解矿山开采能耗与运输成本压力，预计驱动矿山克金与吨铜AISC（综合维持成本）边际优化1.5-2.0个百分点。"
            ),
        },
    )
    by_raw = {it["raw"]: it for it in items}
    assert by_raw["COMEX金站上4103美元"]["status"] == STATUS_VERIFIED
    assert by_raw["布油跌破97美元促AISC成本下降1.5-2%"]["status"] == STATUS_UNSUPPORTED
    assert "预计" in by_raw["布油跌破97美元促AISC成本下降1.5-2%"]["details"]


def test_dav2479_r3_controls_600309_78082241_and_603259_8024b0a0_preserved(evaluator):
    """正例对照：603259 8024b0a0 与 600309 78082241 的独立真实事实不得被误杀。"""
    items_8024 = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-2",
                "stance": "bullish",
                "evidence": [
                    "基本面报告测算现价对应2026年动态PE不足17倍，货币资金371.34亿，资产负债率仅22.13%"
                ],
            }
        ],
        seven_reports={
            "fundamentals_report": (
                "- 资产负债结构：截至2026-09-30，总负债277.02亿元，资产负债率仅22.13%。\n"
                "- 资本与现金储备：货币资金高达371.34亿元。\n"
                "- 估值水平：2026年动态PE不足17倍。"
            )
        },
    )
    by_raw_8024 = {it["raw"]: it for it in items_8024}
    assert by_raw_8024["测算现价对应2026年动态PE不足17倍"]["status"] == STATUS_UNSUPPORTED
    assert by_raw_8024["货币资金371.34亿"]["status"] == STATUS_VERIFIED
    assert by_raw_8024["资产负债率仅22.13%"]["status"] == STATUS_VERIFIED

    items_7808 = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-1",
                "stance": "bullish",
                "evidence": [
                    "根据新闻分析师报告，110万吨MDI装置9月29日复产单日增供3000吨，Q4预计增毛利7.2-8.0亿元"
                ],
            }
        ],
        seven_reports={
            "news_report": (
                "- 烟台110万吨MDI装置9月29日顺利复产，单日增供3000吨。\n"
                "- 按当前价差测算，Q4预计增厚毛利 7.2-8.0 亿元。"
            )
        },
    )
    by_raw_7808 = {it["raw"]: it for it in items_7808}
    assert by_raw_7808["110万吨MDI装置9月29日复产单日增供3000吨"]["status"] == STATUS_VERIFIED
    assert by_raw_7808["Q4预计增毛利7.2-8.0亿元"]["status"] == STATUS_UNSUPPORTED


def test_dav2479_r3_600900_07e4d559_ema10_preserved_and_forecast_rejected(evaluator):
    """600900 07e4d559：中文逗号前接数字（EMA10，破位28.75...）须正常拆句，保留真实事实跌破EMA10。"""
    items = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-10",
                "stance": "bearish",
                "evidence": ["技术报告现价折价VWMA29.61且跌破EMA10，破位28.75将打开向下至28.00空间"],
            }
        ],
        seven_reports={
            "market_report": (
                "- 20日VWMA 位于 29.61 元，现价折价于 VWMA。\n"
                "- 短线放量跌破EMA10与布林中轨。\n"
                "- 极端情景下若连续两日破位28.75元，将打开向下至28.00元空间。"
            )
        },
    )
    by_raw = {it["raw"]: it for it in items}
    assert by_raw["现价折价VWMA29.61"]["status"] == STATUS_VERIFIED
    assert by_raw["跌破EMA10"]["status"] == STATUS_VERIFIED
    assert by_raw["破位28.75将打开向下至28.00空间"]["status"] == STATUS_UNSUPPORTED


def test_dav2479_r3_indented_sensitivity_subbullet_blocked(evaluator):
    """600036 73142344 WEAK 档：父级冒号条目含『推算/假设』时，缩进子条目敏感性数字不得标 verified。"""
    items = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-10",
                "stance": "bearish",
                "evidence": ["基本面报告显示生息资产综合收益率下调10bp税后利减93.75亿元"],
            }
        ],
        seven_reports={
            "fundamentals_report": (
                "- **净息差（NIM）敏感度**：根据招行资产负债结构推算，假设全行生息资产规模约 12.5 万亿元：\n"
                "  * **生息资产综合收益率下调 10 bps** → 利息收入减少约 125 亿元，税后利润影响约 **-93.75 亿元**。"
            )
        },
    )
    assert len(items) == 1
    assert items[0]["status"] == STATUS_UNSUPPORTED
    assert "报告原文命中句含假设/推算词" in items[0]["details"]


@pytest.mark.parametrize("token", ["2026", "5", "90%", "12.8%", "7.32", "21.3倍", "10bps", "10BP", "-1.82亿", "6.2"])
def test_bare_numeric_tokens_including_bps_are_rejected(evaluator, token):
    result = evaluator.evaluate_single_evidence(
        raw_evidence=token,
        seven_reports={"macro_report": f"评估报告提到指标数值为 {token}，主力净流出 -1.82亿元，利率下调10bps。"},
    )
    assert result["status"] == STATUS_UNSUPPORTED


def test_atomic_decomposition_does_not_verify_bare_tokens(evaluator):
    items = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-2",
                "stance": "bullish",
                "evidence": ["根据基本面报告，当前PE-TTM仅21.3倍处于过去5年25%分位以下"],
            }
        ],
        seven_reports={
            "fundamentals_report": "当前估值PE-TTM为21.3倍，过去5年分位约25%，但未确认触底。",
        },
    )
    by_raw = {item["raw"]: item["status"] for item in items}
    # DAV-2479 r5：「当前PE-TTM仅21.3倍」局部片段含指标名 PE，不属裸 token；
    # 「过去5年」「25%分位」前方无指标名/日期，仍按裸 token 拦截。
    assert by_raw.get("21.3倍") == STATUS_VERIFIED, items
    assert by_raw.get("25%") == STATUS_UNSUPPORTED, items
    assert by_raw.get("5", STATUS_UNSUPPORTED) == STATUS_UNSUPPORTED, items


def test_b_class_fixed_wordlist_has_no_report_estimate_escape_hatch(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="测算生息资产收益率下调10bp，税后利润减少93.75亿元",
        seven_reports={
            "fundamentals_report": "测算生息资产综合收益率下调10bps，税后利润影响约-93.75亿元。"
        },
    )
    assert result["status"] == STATUS_UNSUPPORTED


def test_e_class_does_not_false_reject_financial_cashflows_or_cross_sector_siphon(evaluator):
    bull_capex = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-1", "stance": "bullish", "evidence": ["Capex流出大幅收窄45.92%"]}],
        seven_reports={"fundamentals_report": "资本开支（Capex）流出大幅收窄45.92%。"},
    )[0]
    assert bull_capex["status"] == STATUS_VERIFIED

    bear_financing = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-11", "stance": "bearish", "evidence": ["2026H1筹资现金净流入达197.17亿元"]}],
        seven_reports={"fundamentals_report": "2026H1筹资活动现金净流入达197.17亿元。"},
    )[0]
    assert bear_financing["status"] == STATUS_VERIFIED

    bear_siphon = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-4", "stance": "bearish", "evidence": ["宏观报告：主力净流入银行41.26亿，基建板块存量资金遭严重分流抽血"]}],
        seven_reports={"macro_report": "主力净流入银行41.26亿，基建板块存量资金遭严重分流抽血。"},
    )[0]
    assert bear_siphon["status"] == STATUS_VERIFIED


def test_e_class_compound_clause_preserves_non_flow_fact(evaluator):
    items = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-2",
                "stance": "bullish",
                "evidence": ["股价位于200日SMA 5.37元及50日SMA 5.20元上方，大单净流出仅占流通市值0.0327%"],
            }
        ],
        seven_reports={
            "market_report": "当前股价位于200日SMA 5.37元及50日SMA 5.20元上方。",
            # DAV-2479 r8：报告同句带弱化词时按 §二.1 豁免（见 r8 用例）；本用例
            # 保留「报告无弱化词」的 e 类拆句覆盖。
            "smart_money_report": "大单净流出占流通市值0.0327%。",
        },
    )
    by_raw = {item["raw"]: item["status"] for item in items}
    assert by_raw.get("股价位于200日SMA 5.37元及50日SMA 5.20元上方") == STATUS_VERIFIED
    assert by_raw.get("大单净流出仅占流通市值0.0327%") == STATUS_UNSUPPORTED


def test_rule_a_comma_concatenation_cannot_bypass_unsupported_clause(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="医保支持创新药发展，国家医保实行DRG除外支付免计定额",
        seven_reports={"macro_report": "医保支持创新药发展，支付端政策保持稳定。"},
    )
    assert result["status"] == STATUS_UNSUPPORTED


def test_rule_a_case_insensitive_uppercase_metric_terms(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="FDA批准核心ADC创新药临床进展",
        seven_reports={"news_report": "公司核心ADC创新药获得FDA临床批准进展。"},
    )
    assert result["status"] == STATUS_VERIFIED


def test_600036_8e7b8f88_bullish_stance_rejects_large_order_net_outflow(evaluator):
    result = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-2",
                "stance": "bullish",
                "evidence": ["个股大单净流出占流通市值比仅-0.0328%属常态换手非恐慌撤离"],
            }
        ],
        seven_reports={
            "smart_money_report": "个股大单净流出3.30亿元，大单净流出占流通市值比约-0.0328%。"
        },
    )[0]

    assert result["status"] == STATUS_UNSUPPORTED
    assert result["details"] == "资金流方向与论点 stance 相反，不作为已核实事实"


def test_bullish_stance_cannot_use_matching_net_outflow_as_verified_evidence(evaluator):
    result = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-6",
                "stance": "bullish",
                "evidence": ["5日主力累计净流出4.67亿占流通市值比仅0.03%"],
            }
        ],
        seven_reports={
            "smart_money_report": (
                "5日累计主力净额为-4.67亿元。从5日累计净流出4.67亿元相对于公司数千亿市值的比例"
                "（单日流出仅占流通市值0.03%）来看，抛售偏向流动性再配置。"
            )
        },
    )[0]

    assert result["status"] == STATUS_UNSUPPORTED
    # DAV-2479 r8：报告为「单日」口径、证据为「5日累计」，同值同弱化但窗口错配；
    # 按总控 11-27 判定（行 17）撤回理由改记口径错配，不再记 e 类 stance 冲突。
    assert result["details"] == "资金流数值与报告同值同弱化，但统计窗口不一致（口径错配），不作为已核实事实"


from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
    _is_fund_flow_stance_conflict,
    _main_force_flow_directions,
    _report_line_hypothesis_marker,
)


@pytest.mark.parametrize(
    "stance, text",
    [
        ("bullish", "主力资金报告：大盘大跌下大单逆市净流入1.337亿元散户小单流出1.71亿元"),
        ("bullish", "超大单与大单净流入1.89亿元小单净流出1.54亿元"),
        ("bullish", "东财主力净买2.34亿且超大单达1.94亿，中小单流出2.34亿"),
        ("bullish", "小单流出3.21亿换手健康"),
        ("bullish", "中单净流出1.14亿元系良性换手"),
        ("bullish", "通信板块遭流出近80亿而中移动逆市获净流入超5000万避险属性突出"),
        ("bullish", "投资净流出收窄25.4%，资产负债率70.96%较2024年末下降3.68pct"),
        ("bearish", "东财大单净流出1.10亿元小单净流入0.98亿元"),
        ("bearish", "小单净流入4.22亿筹码严重发散"),
        ("bearish", "东财口径超大单及大单净流出-13.91亿而小单净流入+10.54亿"),
    ],
)
def test_dav2479_r4_main_vs_retail_parallel_flow_is_not_a_stance_conflict(stance, text):
    assert _is_fund_flow_stance_conflict(text, stance) is False


@pytest.mark.parametrize(
    "stance, text",
    [
        ("bullish", "主力净流出占流通市值仅-0.0436%"),
        ("bullish", "5日主力累计净流出4.67亿占流通市值比仅0.03%"),
        ("bullish", "大单净流出占流通市值比仅-0.0328%属常态换手非恐慌撤离"),
        ("bullish", "超大单大单共振流出5.28亿与中单流入5.28亿形成换手对冲"),
        ("bullish", "医疗服务与生物制品板块逆势净流入超8.8亿元，主力净流出仅占市值0.0026%"),
        ("bearish", "主力净流入3.37亿仅占流通市值0.0597%"),
        ("bearish", "东财超大单流入2.46亿被中单流出2.20亿对冲"),
    ],
)
def test_dav2479_r4_main_force_wash_still_a_stance_conflict(stance, text):
    assert _is_fund_flow_stance_conflict(text, stance) is True


def test_dav2479_r4_flow_subject_attribution_is_nearest_preceding_subject():
    assert _main_force_flow_directions("大单逆市净流入1.337亿元散户小单流出1.71亿元") == ["in"]
    assert _main_force_flow_directions("中单流出1.83亿割肉但超大单仅流出0.059亿大单微幅流入") == ["out", "in"]
    assert _main_force_flow_directions("主力净流出2亿") == ["out"]
    assert _main_force_flow_directions("宏观报告显示板块资金前十无煤炭而券商软件大额净流入") == []
    assert _is_fund_flow_stance_conflict("宏观报告显示板块资金前十无煤炭而券商软件大额净流入", "bearish") is False


def test_dav2479_r4_main_vs_retail_pair_verified_but_main_wash_blocked(evaluator):
    report = (
        "- 10月14日大盘大跌下大单逆市净流入1.337亿元，散户小单流出1.71亿元。\n"
        "- 主力净流出占流通市值比仅-0.0362%。"
    )
    items = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-6",
                "stance": "bullish",
                "evidence": [
                    "大盘大跌下大单逆市净流入1.337亿元散户小单流出1.71亿元",
                    "主力净流出占流通市值比仅-0.0362%",
                ],
            }
        ],
        seven_reports={"smart_money_report": report},
    )
    by_raw = {it["raw"]: it for it in items}
    assert by_raw["大盘大跌下大单逆市净流入1.337亿元散户小单流出1.71亿元"]["status"] == STATUS_VERIFIED
    # DAV-2479 r8 §二.1：报告同句以「仅」弱化同一数值 -0.0362%，按写死豁免不判 e；
    # 报告无弱化词时仍判 e（下方对照）。
    assert by_raw["主力净流出占流通市值比仅-0.0362%"]["status"] == STATUS_VERIFIED
    blocked = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-6", "stance": "bullish", "evidence": ["主力净流出占流通市值比仅-0.0362%"]}],
        seven_reports={"smart_money_report": "- 主力净流出占流通市值比为-0.0362%，抛压延续。"},
    )
    assert blocked and all(it["status"] == STATUS_UNSUPPORTED for it in blocked), blocked
    assert all("资金流方向与论点 stance 相反" in it["details"] for it in blocked), blocked


_A810_LINE = (
    "- **收入规模与增长**：2026Q1营业总收入1,291.31亿元，较2025Q1的847.05亿元同比增长52.45%，"
    "环比2025Q4（以单季测算）保持韧性，主要由储能电芯全球放量拉动。"
)


def test_dav2479_r4_trailing_estimate_clause_does_not_taint_preceding_fact():
    first_fact = _A810_LINE.index("1,291.31")
    growth = _A810_LINE.index("52.45%")
    trailing = _A810_LINE.index("环比")
    assert _report_line_hypothesis_marker(_A810_LINE, [first_fact]) is None
    assert _report_line_hypothesis_marker(_A810_LINE, [growth]) is None
    assert _report_line_hypothesis_marker(_A810_LINE, [trailing]) == "测算"


def test_dav2479_r4_estimate_before_number_or_in_same_clause_still_taints():
    pre = "- 预计单克金综合维持成本（AISC）承压减轻约1.5-2.0个百分点（中等正相关，时滞1-2个月）。"
    assert _report_line_hypothesis_marker(pre, [pre.index("1.5-2.0")]) == "预计"
    annotated = "净额占流通市值比约0.0249%（基于东财主力净额与流通市值测算）。"
    assert _report_line_hypothesis_marker(annotated, [annotated.index("0.0249")]) == "测算"


def test_dav2479_r4_a810f964_preceding_fact_verified_despite_trailing_estimate(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="2026Q1营业总收入1291.31亿元",
        seven_reports={"fundamentals_report": _A810_LINE},
    )
    assert result["status"] == STATUS_VERIFIED
    assert result["matched_role"] == "fundamentals_report"


def test_dav2479_r4_partial_clean_hit_not_dropped_by_hypothesis_hit_elsewhere(evaluator):
    seven = {
        "fundamentals_report": (
            "- 2026H1毛利率为18.85%，同比提升0.84个百分点。\n"
            "- 反向传导：若带动成本下行10%，毛利率有望提升约1.8~2.2个百分点。"
        )
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="H1毛利率升至18.85%且毛利率提升2.2个百分点", seven_reports=seven
    )
    coverage = res.get("fact_coverage") or {}
    assert any("18.85" in fact for fact in coverage.get("verified_facts", [])), res
    items = evaluator._verify_evidence_or_decompose(
        "H1毛利率升至18.85%且毛利率提升2.2个百分点", seven, None, None, "T-1", None
    )
    assert len(items) >= 2
    assert not all("报告原文命中句含假设/推算词" in str(it.get("details")) for it in items)


def test_dav2479_r4_derived_aggregate_atom_carries_role_and_readable_label(evaluator):
    report = (
        "- 东财分单结构：超大单+0.2756亿、大单+0.6961亿、中单-0.0705亿、小单-0.9011亿，四档零和闭合。\n"
        "- 中小单合计净流出0.9716亿元。"
    )
    items = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-3",
                "stance": "bearish",
                "evidence": ["东财分单中单-0.0705亿小单-0.9011亿，中小单合计净流出0.9716亿"],
            }
        ],
        seven_reports={"smart_money_report": report},
    )
    derived = [it for it in items if "+" in str(it["raw"]) and it["status"] == STATUS_VERIFIED]
    for it in derived:
        assert it["matched_role"] == "smart_money_report"
        assert "合计(" in it["details"]


_R4_FUNDAMENTALS_REPORT = (
    "3. **现金流恶化风险**：连续两个季度经营现金流录得负数（2026H1合计净流出46.89亿元），需密切警惕。\n"
    "- 存货升至226.58亿元，若终端动销不及预期将击穿价格带。\n"
    "- 2026年上半年销售毛利率约为 18.85%（营业收入3448.15亿元）。"
)


@pytest.mark.parametrize(
    "raw",
    [
        "2026H1经营现金流净流出46.89亿元",
        "存货攀升至226.58亿元创历史新高",
        "上半年毛利率逆势提升至18.85%",
    ],
)
def test_dav2479_r4_single_number_metric_clause_not_withdrawn_as_bare_token(evaluator, raw):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-8", "stance": "bearish", "evidence": [raw]}],
        seven_reports={"fundamentals_report": _R4_FUNDAMENTALS_REPORT},
        market_data_context={"data_gaps": ["fundamentals"]},
    )

    assert len(items) == 1, items
    assert items[0]["status"] == STATUS_VERIFIED, items
    assert items[0]["matched_role"] == "fundamentals_report"
    assert raw in items[0]["details"]


def test_dav2479_r4_single_number_clause_without_metric_still_blocked(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-8", "stance": "bearish", "evidence": ["上半年提升至18.85%"]}],
        seven_reports={"fundamentals_report": "销售净利率 18.85%。"},
        market_data_context={"data_gaps": ["fundamentals"]},
    )

    assert all(it["status"] != STATUS_VERIFIED for it in items), items


def test_dav2479_r4_single_number_clause_with_metric_alias_not_withdrawn(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-2", "stance": "bullish", "evidence": ["2026H1投资活动现金流净流出同比骤减69.31%"]}],
        seven_reports={"fundamentals_report": "2026H1投资活动现金流净流出同比骤减69.31%，Capex大幅缩减。"},
        market_data_context={"data_gaps": ["fundamentals"]},
    )

    assert len(items) == 1, items
    assert items[0]["status"] == STATUS_VERIFIED, items


def test_dav2479_r4_directional_volume_word_anchors_single_number_clause(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-6", "stance": "bullish", "evidence": ["11-23缩量至3813万股收十字星"]}],
        seven_reports={"market_report": "11-23缩量至3813万股，收十字星。"},
    )

    assert len(items) == 1, items
    assert items[0]["status"] == STATUS_VERIFIED, items


# ── DAV-2479 r5：裸 token 按原子所属子句锚点判定 ─────────────────────────

from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
    BoundNumber,
    _fact_has_clause_anchor,
    _is_fund_flow_stance_conflict,
    extract_bound_numbers,
)


def _clause_coverage(clause):
    bns = extract_bound_numbers(clause)
    return {
        "total": len(bns),
        "bindings": [
            {"clause": clause, "number": {k: getattr(b, k) for k in BoundNumber.__slots__}}
            for b in bns
        ],
    }


@pytest.mark.parametrize("clause, expected", [
    ("2026Q1营收1291.31亿元暴增52.45%存货周转仅75天海外大储锁定订单",
     {"1291.31亿元": True, "52.45%": True, "75天": True}),
    ("碳酸锂季跌34%单车省1400元H1毛利率升至18.85%",
     {"34%": True, "1400元": True, "18.85%": True}),
    ("铁路新签腰斩47.5%未来将削减61亿毛利", {"47.5%": True, "61亿": True}),
    ("能耗成本升10%毛利降至23.7%", {"10%": True, "23.7%": True}),
    ("2026H1投资现金流净额-452.94亿元同比失血剧增17倍",
     {"-452.94亿元": True, "17倍": True}),
    ("负营运资本-391.9亿元主要由无息应付款项构成", {"-391.9亿元": True}),
    ("半导体吸金236亿首板超百家", {"236亿": True}),
    ("04-29沉淀2.53亿股巨量套牢断层", {"2.53亿股": True}),
    ("底部启动量达83.6万手巨量确认反转", {"83.6万": True}),
    ("11-04滞涨沉淀1.45亿股套牢盘", {"1.45亿股": True}),
    ("海外发那科月跌12.8%全球机械估值中枢坍塌", {"12.8%": True}),
    ("HRS-1596海外临床资金全部由诺和诺德承担", {"-1596": False}),
    ("i茅台常态化投放6款次新飞天增厚利润", {"6": False}),
    ("捷荣技术6连板激活消费电子热度", {"6": False}),
    ("上半年提升至18.85%", {"18.85%": False}),
    ("当前PE-TTM仅21.3倍处于过去5年25%分位以下", {"21.3倍": True, "5": False, "25%": True}),
    ("东财-1.82亿仅占流通市值0.022%为噪声级", {"-1.82亿": True, "0.022%": True}),
    ("2026前三季营收同比+28.87%与存货增速+28.88%同步",
     {"2026": True, "+28.87%": True, "+28.88%": True}),
])
def test_dav2479_r5_fact_clause_anchor(clause, expected):
    fc = _clause_coverage(clause)
    got = {b["number"]["raw"]: _fact_has_clause_anchor(fc, b["number"]["raw"], clause) for b in fc["bindings"]}
    assert got == expected


def test_dav2479_r5_multi_number_clause_atoms_not_bare_token(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{
            "claim_id": "INV-8",
            "stance": "bullish",
            "evidence": ["《基本面报告》2026Q1营收1291.31亿元暴增52.45%存货周转仅75天海外大储锁定订单"],
        }],
        seven_reports={
            "fundamentals_report": (
                "- **收入规模与增长**：2026Q1营业总收入1,291.31亿元，较2025Q1的847.05亿元"
                "同比增长52.45%，环比2025Q4（以单季测算）保持韧性，主要由储能电芯全球放量拉动。"
            ),
        },
    )
    by_raw = {it["raw"]: it for it in items}
    for raw in ("1291.31亿元", "52.45%"):
        assert by_raw[raw]["status"] == STATUS_VERIFIED, items
        assert by_raw[raw]["matched_role"] == "fundamentals_report", items
        assert by_raw[raw]["clause_anchored"] is True, items
    assert by_raw["75天"]["status"] == STATUS_UNSUPPORTED, items


def test_dav2479_r5_bare_atoms_in_anchored_clause_still_blocked(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-1", "stance": "bullish", "evidence": ["HRS-1596海外临床资金全部由诺和诺德承担"]}],
        seven_reports={"news_report": "恒瑞医药与诺和诺德就 HRS-1596 达成独家许可协议。"},
    )
    assert all(it["status"] != STATUS_VERIFIED for it in items if it["raw"] == "-1596"), items


@pytest.mark.parametrize("text, stance, directions, conflict", [
    ("大单中单合计净流入1.072亿元承接超大单流出", "bullish", ["in", "out"], False),
    ("主力资金报告显示大单与中单合计净流入2.77亿完全吸收超大单流出", "bullish", ["in", "out"], False),
    ("主力资金报告显示东财大单与中单合计净流入达0.9371亿超越超大单流出量0.8782亿", "bullish", ["in", "out"], False),
    ("大单中单合计净流入8480万而超大单仅流出2056万占成交1.43%", "bullish", ["in", "out"], False),
    ("主力资金报告显示同花顺全口径流入4.14亿系中小单接盘散户化且VWMA在78.47元重压", "bearish", [], False),
    ("资金报告11-20大单中单合计净流出4303.87万元", "bullish", ["out"], True),
    ("主力资金报告中单净流入11.78亿与超大单流出对冲", "bullish", ["out"], True),
])
def test_dav2479_r5_combined_and_post_qualified_flow_subjects(text, stance, directions, conflict):
    assert _main_force_flow_directions(text) == directions
    assert _is_fund_flow_stance_conflict(text, stance) is conflict


# ── DAV-2479 r6（总控 2026-11-26 23:07 令）：名称内数字写死表 + 分句锚点 ──


@pytest.mark.parametrize("clause, expected", [
    ("科创50 20日-22.20%", {"50": True, "-22.20%": True}),
    ("沪深300跌3.2%", {"3.2%": True}),
    ("M8季度跌5%", {"8": True, "5%": True}),
    ("2026前三季度营收增29.01%存货增26.53%锁定旺季", {"2026": True, "29.01%": True, "26.53%": True}),
    ("硅料跌至31385元/吨", {"31385元": True}),
    ("致密料现货35890元", {"35890元": True}),
    ("HRS-1596海外临床资金全部由诺和诺德承担", {"-1596": False}),
    ("i茅台常态化投放6款次新飞天增厚利润", {"6": False}),
    ("上半年提升至18.85%", {"18.85%": False}),
    ("下探至26.51元", {"26.51元": False}),
])
def test_dav2479_r6_name_number_and_subclause_anchor(clause, expected):
    fc = _clause_coverage(clause)
    got = {b["number"]["raw"]: _fact_has_clause_anchor(fc, b["number"]["raw"], clause) for b in fc["bindings"]}
    assert got == expected


def test_dav2479_r6_index_name_number_not_withdrawn(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-10", "stance": "bearish", "evidence": ["宏观报告显示科创50 20日-22.20%成长风格退潮"]}],
        seven_reports={"macro_report": "- 科创50：20日涨跌幅 -22.20%，成长风格显著退潮。"},
    )
    assert all("裸数字/单位 token" not in str(it.get("details") or "") for it in items), items


def test_dav2479_r6_year_period_anchor_restores_a1_fact(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-1", "stance": "bullish", "evidence": ["2026前三季度营收增29.01%存货增26.53%锁定旺季"]}],
        seven_reports={"fundamentals_report": "2026年前三季度营业收入同比增长29.01%；存货较年初增长26.53%。"},
    )
    assert all("裸数字/单位 token" not in str(it.get("details") or "") for it in items), items
    assert any(it["status"] == STATUS_VERIFIED for it in items), items


# ── DAV-2479 r7（总控 2026-11-27 01:16 令）：确定性截断 + 两条点名误杀 ──

from tradingagents.agents.utils.evidence_verifier import _cut_bare_entity_token  # noqa: E402


def test_dav2479_r7_bare_entity_cut_uses_earliest_position():
    assert _cut_bare_entity_token("大金增长同比") == "大金"
    assert _cut_bare_entity_token("大金同比增长") == "大金"
    assert _cut_bare_entity_token("大金单月跌") == "大金"
    assert _cut_bare_entity_token("大金") == "大金"


@pytest.mark.parametrize("clause, expected", [
    ("点心债票息仅2.46%低于负债存量成本致利差倒挂", {"2.46%": True}),
    ("当前20日均量约8,900万股（量价报告）", {"8900万股": True}),
])
def test_dav2479_r7_named_false_rejects_anchor(clause, expected):
    fc = _clause_coverage(clause)
    got = {b["number"]["raw"]: _fact_has_clause_anchor(fc, b["number"]["raw"], clause) for b in fc["bindings"]}
    assert got == expected


def test_dav2479_r7_coupon_rate_fact_verified(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-3", "stance": "bearish", "evidence": ["点心债票息仅2.46%低于负债存量成本致利差倒挂"]}],
        seven_reports={"news_report": (
            "- **2026-08-18**【跨市场流动性/险资出海】：国家电网海外投资在香港完成149亿元离岸人民币债券（点心债）发行，"
            "票息2.46%，创央企单笔离岸人民币债发行新高；险资通过“南向通”首次认购该批离岸人民币资产。"
        )},
    )
    assert all("裸数字/单位 token" not in str(it.get("details") or "") for it in items), items
    assert any(it["status"] == STATUS_VERIFIED for it in items), items


def test_dav2479_r7_thousands_separator_volume_verified(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-13", "stance": "bearish", "evidence": ["当前20日均量约8,900万股（量价报告）"]}],
        seven_reports={"volume_price_report": (
            "以输入数据自算：近20个交易日（2026-08-07至09-04）日均成交量约 **8,900万股**；"
            "近5个交易日（08-31至09-04）均量约 **9,790万股（1.10倍）**，OBV（10日）趋势为**上升**。"
            "以下\"量比\"均以20日均量为基准，属相对量能口径，非绝对值判断。"
        )},
    )
    assert all("裸数字/单位 token" not in str(it.get("details") or "") for it in items), items
    assert any(it["status"] == STATUS_VERIFIED for it in items), items


# ── DAV-2479 r8（总控 2026-11-27 05:57 令）：e 类弱化词豁免 + 同实体取整匹配 ──

from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
    _is_unexempted_fund_flow_stance_conflict,
    _rounding_supported_roles,
)

_R8_FBA21DA2_SMART_MONEY = (
    "  - 【中线视角】：大单净流出占流通市值比仅为 -0.0327%，相对规模极小，未出现恐慌性清仓痕迹。"
    "中线维度上，高油价带来的资源勘探盈利与 0.78 倍 PB 估值底线，使得本次大单流出更偏向短线高贝塔资金的阶段性套现，"
    "并未颠覆中线价值底仓资金的持股结构，预期时滞需 1-3 个月逐步消化。"
)
_R8_F8BAB114_SMART_MONEY = (
    "  - 【中线视角】：近 5 日主力净流出累计 1.70 亿元，占神华庞大流通市值的比重不足万分之零点七（-0.007%），"
    "表明此类流出属于次级反弹中的交易性仓位微调，绝非战略性中线底仓撤离。"
)
_R8_8E7B8F88_SMART_MONEY = (
    "  - 【中线视角】：尽管大单口径流出，但净额占流通市值比仅为 -0.0328%，属于震荡市常态化换手，并未见恐慌性规模出逃。"
    "结合中单与小单的被动吸纳，筹码向中线均衡带收敛。"
)
_R8_AEA465D0_FUNDAMENTALS = (
    "- **指标与现状**：美股光伏标杆 First Solar（FSLR）报 150.98 美元（月环比 -12.12%，季度环比 -29.47%），"
    "微型逆变器龙头 Enphase Energy（ENPH）报 22.66 美元（月环比 -26.02%，季度环比 -40.93%）。"
)


@pytest.mark.parametrize("evidence, report", [
    ("大单净流出仅占流通市值0.0327%", _R8_FBA21DA2_SMART_MONEY),
    ("5日主力流出仅占流通市值0.007%", _R8_F8BAB114_SMART_MONEY),
    ("个股大单净流出占流通市值比仅-0.0328%属常态换手非恐慌撤离", _R8_8E7B8F88_SMART_MONEY),
])
def test_dav2479_r8_weakened_flow_restated_is_exempt(evidence, report):
    assert not _is_unexempted_fund_flow_stance_conflict(evidence, "bullish", {"smart_money_report": report})


@pytest.mark.parametrize("evidence, report", [
    ("大单净流出占流通市值0.0327%", _R8_FBA21DA2_SMART_MONEY),
    ("大单净流出仅占流通市值0.05%", _R8_FBA21DA2_SMART_MONEY),
    ("大单净流出仅占流通市值0.0327%", "- 大单净流出占流通市值比为 -0.0327%，抛压明显。"),
    ("5日主力累计净流出4.67亿占流通市值比仅0.03%", "- 主力资金单日流出仅占流通市值 0.03%，抛压有限。"),
])
def test_dav2479_r8_weakening_exemption_requires_same_value_and_wording(evidence, report):
    assert _is_unexempted_fund_flow_stance_conflict(evidence, "bullish", {"smart_money_report": report})


@pytest.mark.parametrize("cid, evidence", [
    ("INV-2", "大单净流出仅占流通市值0.0327%，未出现恐慌性清仓"),
    ("INV-6", "5日主力流出仅占流通市值0.007%"),
    ("INV-2", "个股大单净流出占流通市值比仅-0.0328%属常态换手非恐慌撤离"),
])
def test_dav2479_r8_weakened_flow_named_rows_verified(evaluator, cid, evidence):
    reports = {"smart_money_report": "\n".join(
        (_R8_FBA21DA2_SMART_MONEY, _R8_F8BAB114_SMART_MONEY, _R8_8E7B8F88_SMART_MONEY)
    )}
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": cid, "stance": "bullish", "evidence": [evidence]}],
        seven_reports=reports,
    )
    assert all("stance 相反" not in str(it.get("details") or "") for it in items), items


def test_dav2479_r8_rounding_match_same_entity_same_metric(evaluator):
    reports = {"fundamentals_report": _R8_AEA465D0_FUNDAMENTALS}
    assert _rounding_supported_roles("海外龙头FSLR与ENPH月跌12%至26%压制估值", reports) == ["fundamentals_report"]
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-12", "stance": "bearish", "evidence": ["海外龙头FSLR与ENPH月跌12%至26%压制估值"]}],
        seven_reports=reports,
    )
    assert items and all(it["status"] == STATUS_VERIFIED for it in items), items


@pytest.mark.parametrize("segment, expected", [
    ("海外龙头FSLR与ENPH月跌13%至26%压制估值", None),
    ("海外龙头FSLR与ENPH季跌12%至26%压制估值", None),
    ("海外龙头ENPH与FSLR月跌12%至26%压制估值", None),
    ("海外龙头FSLR与ENPH月涨12%至26%", None),
    ("FSLR月跌12%", ["fundamentals_report"]),
])
def test_dav2479_r8_rounding_match_rejects_other_entity_metric_or_value(segment, expected):
    reports = {"fundamentals_report": _R8_AEA465D0_FUNDAMENTALS}
    assert _rounding_supported_roles(segment, reports) == expected


def test_dav2479_r8_rounding_match_skips_hypothesis_line():
    reports = {"macro_report": "- 情景测算：若 FSLR 月环比 -12.12%，ENPH 月环比 -26.02%。"}
    assert _rounding_supported_roles("FSLR与ENPH月跌12%至26%", reports) is None


_R8_DETERMINISM_SCRIPT = """
import json
from tradingagents.agents.utils.evidence_verifier import EvidenceFactualTruthEvaluator
cases = [
    ("均线死叉压制", {"market_report": "短期均线形成死叉，空头压制明显。"}),
    ("冲高回落且成交量放大", {"volume_price_report": "盘中冲高回落，成交量明显放大。"}),
    ("现金流承压", {"macro_report": "企业现金流与现金储备承压。"}),
]
out = []
for raw, reports in cases:
    r = EvidenceFactualTruthEvaluator().evaluate_single_evidence(raw_evidence=raw, seven_reports=reports)
    out.append([r["status"], r.get("details")])
print("DAV2479_DET" + json.dumps(out, ensure_ascii=False))
"""


def test_dav2479_r8_qualitative_match_details_independent_of_hash_seed():
    import os
    import subprocess
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outputs = []
    for seed in ("0", "1", "2", "20261127"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-c", _R8_DETERMINISM_SCRIPT],
            cwd=repo_root, env=env, capture_output=True, text=True, timeout=300,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("DAV2479_DET")]
        assert line, proc.stdout[-2000:]
        outputs.append(line[-1])
    assert len(set(outputs)) == 1, outputs
    assert "均线, 死叉" in outputs[0] and "冲高, 回落, 成交量" in outputs[0] and "现金, 现金流" in outputs[0]


_R8_E185_REPORTS = {
    "market_report": "10月21日成交量仅2618万股，量能显著萎缩。",
    "smart_money_report": "当日大单流出占流通市值比仅约 0.027%，相对规模极小，并未出现恐慌性出逃。",
}


def test_dav2479_r8_exempted_flow_clause_does_not_block_compound_decomposition(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-6", "stance": "bullish", "evidence": ["10月21日成交仅2618万股且主力净流出占市值比仅0.027%"]}],
        seven_reports=_R8_E185_REPORTS,
    )
    by_raw = {str(it.get("raw")): it["status"] for it in items}
    assert by_raw.get("10月21日成交仅2618万股") == STATUS_VERIFIED, items
    assert all("且" not in str(it.get("raw")) for it in items), items


def test_dav2479_r8_flow_window_ignores_calendar_dates_and_moving_averages():
    from tradingagents.agents.utils.evidence_verifier import _flow_windows

    assert _flow_windows("10月21日主力净流出仅0.027%") == []
    assert _flow_windows("站稳10日线且5日净流出仅0.85亿") == ["5日"]
    assert _flow_windows("20日均量0.78倍") == []
    assert _flow_windows("2026-11-04主力流出") == []
    assert _flow_windows("单日流出仅占流通市值0.03%") == ["1日"]
    assert _flow_windows("近 5 日主力累计净流出仅 0.85 亿元") == ["5日"]


def test_dav2479_r8_calendar_date_does_not_create_window_mismatch():
    from tradingagents.agents.utils.evidence_verifier import (
        _is_unexempted_fund_flow_stance_conflict,
        _report_restates_weakened_value,
    )

    evidence = "10月21日大单流出占流通市值比仅0.027%"
    assert _report_restates_weakened_value(evidence, _R8_E185_REPORTS) == "match"
    assert _is_unexempted_fund_flow_stance_conflict(evidence, "bullish", _R8_E185_REPORTS) is False


def test_dav2479_r8_real_window_mismatch_still_rejected(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-6", "stance": "bullish", "evidence": ["5日主力累计净流出4.67亿占流通市值比仅0.03%"]}],
        seven_reports={"smart_money_report": "单日流出仅占流通市值 0.03%，5日主力累计净流出 4.67 亿元。"},
    )
    assert items and all(it["status"] != STATUS_VERIFIED for it in items), items


# ── DAV-2479 r12（总控 2026-11-28 02:44 裁定）：保守方案，均线补验（ma_position_match）已整条移除 ──
# 均线方位类 evidence 回到 r9 之前的判定；以下用例只校验「不再被均线补验放行」，不再调用已删除的内部函数。


_R9_F8BAB114_REPORTS = {
    "market_report": (
        "- **What**：现价 50.82 元 > 10 EMA（50.18）> 50 SMA（48.40）> 200 SMA（44.59）。短期均线低位勾头向上，中长期均线维持标准的多头排列态势。\n"
        "- **What Next**：若能持续守在 10 EMA 上方运行，将逐步带动短期均线向上发散；若失守 10 EMA，则将再次回踩 50 SMA。\n"
        "| **VWMA** | 49.80 元 | 现价运行于 VWMA 之上 | 短期持仓成本上移，下方存在密集承接 |"
    ),
    "smart_money_report": "- **空间位置关系**：现价（50.82 元）位于 VWMA（49.80 元）上方 **+2.05%**，表明近期成交筹码的重心处于现价下方，VWMA 构成多方动态技术防守参考带。",
    "fundamentals_report": "- **中国神华（601088.SH）**：收盘价 50.82 元，TTM PE 约 18.2 倍，PB（LF）约 2.48 倍，近1年股价处于多头趋势慢牛上行通道（现价 > 50日SMA 48.40元 > 200日SMA 44.59元）。",
}


def _r9_items(evaluator, evidence, reports, stance="bullish"):
    return evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-6", "stance": stance, "evidence": [evidence]}],
        seven_reports=reports,
    )


def _r9_statuses(evaluator, evidence, reports, stance="bullish"):
    return {str(it.get("raw")): it["status"] for it in _r9_items(evaluator, evidence, reports, stance)}


def test_dav2479_r12_ma_position_path_removed():
    from tradingagents.agents.utils import evidence_verifier as ev

    for name in (
        "_apply_ma_position_matches", "_ma_position_supported_roles", "_ma_position_report_support",
        "_ma_positions_in_line", "_ma_affirmative_clause", "_ma_sentence_has_blocking_scope", "_ma_key",
        "_MA_POSITION_EVIDENCE_RE", "_MA_SAFE_REPORT_LABELS", "_MA_SENTENCE_BLOCKING_RE",
        "_MA_POSITION_DETAIL", "_MA_REPORT_CONDITION_RE",
    ):
        assert not hasattr(ev, name), name


def test_dav2479_r12_f8bab114_ma_position_now_unsupported(evaluator):
    # 保守方案，均线补验已移除：r9 唯一放行的真实档（f8bab114 中线 INV-6）不再 verified。
    items = _r9_items(evaluator, "5日主力流出仅占流通市值0.007%且稳居50SMA/VWMA上方", _R9_F8BAB114_REPORTS)
    statuses = {str(it.get("raw")): it["status"] for it in items}
    assert statuses.get("稳居50SMA/VWMA上方") == STATUS_UNSUPPORTED, statuses
    assert not any("ma_position_match" in str(it.get("details")) for it in items), items


@pytest.mark.parametrize("evidence", ["稳居50SMA上方", "站稳50日SMA上方", "位于VWMA之上", "运行于200 SMA上方", "稳居10 EMA上方"])
def test_dav2479_r12_ma_position_variants_unsupported(evaluator, evidence):
    # 保守方案，均线补验已移除：原 r9 正向变体回到 unsupported。
    items = _r9_items(evaluator, evidence, _R9_F8BAB114_REPORTS)
    assert {str(it.get("raw")): it["status"] for it in items}.get(evidence) == STATUS_UNSUPPORTED, items
    assert not any("ma_position_match" in str(it.get("details")) for it in items), items


def test_dav2479_r9_ma_position_opposite_direction_rejected(evaluator):
    reports = {"market_report": "- **What**：现价 46.20 元 < 50 SMA（48.40）< 200 SMA（49.10）。"}
    assert _r9_statuses(evaluator, "稳居50SMA上方", reports).get("稳居50SMA上方") != STATUS_VERIFIED


def test_dav2479_r9_ma_position_period_mismatch_rejected(evaluator):
    reports = {"market_report": "- **What**：现价 50.82 元 > 20 SMA（49.10）。"}
    assert _r9_statuses(evaluator, "稳居50SMA上方", reports).get("稳居50SMA上方") != STATUS_VERIFIED


def test_dav2479_r9_ma_position_conditional_only_rejected(evaluator):
    reports = {
        "market_report": (
            "- **What Next**：若能持续守在 10 EMA 上方运行，将逐步带动短期均线向上发散；若失守 10 EMA，则将再次回踩 50 SMA。\n"
            "- 若现价运行于 10 EMA 上方，则短线结构修复。\n"
            "- 一旦现价 > 10 EMA（50.18），将确认反弹。"
        )
    }
    assert _r9_statuses(evaluator, "稳居10 EMA上方", reports).get("稳居10 EMA上方") != STATUS_VERIFIED


def test_dav2479_r9_ma_position_multi_average_missing_one_rejected(evaluator):
    reports = {"market_report": "- **What**：现价 50.82 元 > 10 EMA（50.18）> 50 SMA（48.40）> 200 SMA（44.59）。"}
    statuses = _r9_statuses(evaluator, "5日主力流出仅占流通市值0.007%且稳居50SMA/VWMA上方", reports)
    assert statuses.get("稳居50SMA/VWMA上方") != STATUS_VERIFIED, statuses


@pytest.mark.parametrize("source_line", [
    "并非现价 > 50 SMA（48.40）",
    "收盘价并非位于50 SMA上方",
    "仅在放量突破时才有现价 > 50 SMA（48.40）",
    "只有站稳后才能确认现价 > 50 SMA（48.40）",
    "收盘价不位于50 SMA上方",
])
def test_dav2479_r10_ma_negated_or_conditional_source_rejected(evaluator, source_line):
    reports = {"market_report": source_line}
    assert _r9_statuses(evaluator, "稳居50SMA上方", reports).get("稳居50SMA上方") != STATUS_VERIFIED


def test_dav2479_r10_ma_unavailable_source_not_used(evaluator):
    items = evaluator.evaluate_claims(
        claims=[{"claim_id": "INV-6", "stance": "bullish", "evidence": ["稳居50SMA上方"]}],
        seven_reports={"market_report": "现价 > 50 SMA（48.40）"},
        market_data_context={"data_gaps": ["stock_data"]},
    )
    assert items and all(it["status"] != STATUS_VERIFIED for it in items), items


_R11_REVIEW_COUNTEREXAMPLES = [
    "仅在放量突破时，现价 > 50 SMA（48.40）。",
    "并非如下结论，现价 > 50 SMA（48.40）。",
    "假定结论：现价 > 50 SMA（48.40）。",
    "对倒判断：现价 > 50 SMA（48.40）。",
]


@pytest.mark.parametrize("source_line", _R11_REVIEW_COUNTEREXAMPLES)
def test_dav2479_r11_review_counterexample_unsupported(evaluator, source_line):
    reports = {"market_report": source_line}
    assert _r9_statuses(evaluator, "稳居50SMA上方", reports).get("稳居50SMA上方") == STATUS_UNSUPPORTED


_R12_PARENT_HEADING_COUNTEREXAMPLES = [
    "- **仅在放量突破时**：\n  - 现价 > 50 SMA（48.40）。",
    "- **并非如下结论**：\n  - 现价 > 50 SMA（48.40）。",
    "- **假定结论**：\n  - 现价 > 50 SMA（48.40）。",
]


@pytest.mark.parametrize("source_text", _R12_PARENT_HEADING_COUNTEREXAMPLES)
def test_dav2479_r12_parent_heading_counterexample_unsupported(evaluator, source_text):
    reports = {"market_report": source_text}
    assert _r9_statuses(evaluator, "稳居50SMA上方", reports).get("稳居50SMA上方") == STATUS_UNSUPPORTED


@pytest.mark.parametrize("source_line", [
    "- **趋势维度**：现价 50.82 元 > 50 SMA（48.40）> 200 SMA（44.59）。",
    "现价 > 50 SMA（48.40）。",
])
def test_dav2479_r11_review_positive_now_unsupported(evaluator, source_line):
    # 保守方案，均线补验已移除：原 r11 两个正例改为断言 unsupported。
    reports = {"market_report": source_line}
    assert _r9_statuses(evaluator, "稳居50SMA上方", reports).get("稳居50SMA上方") == STATUS_UNSUPPORTED


@pytest.mark.parametrize("source_line", [
    "只有站稳后，收盘价位于50 SMA上方。",
    "- **What**：若放量突破，现价 > 50 SMA（48.40）。",
])
def test_dav2479_r11_ma_scope_prefix_with_comma_rejected(evaluator, source_line):
    reports = {"market_report": source_line}
    assert _r9_statuses(evaluator, "稳居50SMA上方", reports).get("稳居50SMA上方") != STATUS_VERIFIED


@pytest.mark.parametrize("source_line", [
    "市场传闻：现价 > 50 SMA（48.40）。",
])
def test_dav2479_r11_ma_unlisted_label_rejected(evaluator, source_line):
    reports = {"market_report": source_line}
    assert _r9_statuses(evaluator, "稳居50SMA上方", reports).get("稳居50SMA上方") != STATUS_VERIFIED


@pytest.mark.parametrize("source_line", [
    "现价 > 50 SMA（48.40）",
    "- **What**：现价 50.82 元 > 50 SMA（48.40）> 200 SMA（44.59）。",
    "收盘价位于50 SMA上方",
    "- **趋势维度**：收盘价 79.92元 > 50日SMA（48.40元）。",
    "- **均线系统**：现价位于50 SMA上方。",
    "现价 > 50 SMA（48.40），短期均线低位勾头向上。",
])
def test_dav2479_r12_former_ma_affirmative_sources_unsupported(evaluator, source_line):
    # 保守方案，均线补验已移除：原 r10/r11 正向来源句不再支撑均线方位 evidence。
    reports = {"market_report": source_line}
    assert _r9_statuses(evaluator, "稳居50SMA上方", reports).get("稳居50SMA上方") == STATUS_UNSUPPORTED
