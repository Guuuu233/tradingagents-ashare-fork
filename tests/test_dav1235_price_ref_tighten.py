"""DAV-1235 price-ref 精度收紧测试（fixture 取报告 4390ddfd 原句与冻结语料原句）。

D-042 修正规格：
P1′ derived_estimate 只认「估值公式」：
  (a) 估值乘数/模型词（PE/PB/PS/PEG/EV-EBITDA/市盈率/市净率/市销率/DCF/贴现/
      股息率 + 「估值至/到/为/在 N 倍」「N 倍估值/市盈率」）出现在数值前
      60 字内、同一句（。；！？换行切分）；
  (b) 计算关联三条任一——关联词（对应/折合/折算/隐含/测算得/×/乘以）在
      数值子句或紧邻前一子句；「N 倍/给予…倍」在数值子句；每股基数与乘数
      同子句；叙事词（估值修复/测算/估算/市值/公允/安全边际）不单独触发。
P2′ 两级否决：硬否决 VWMA/VWAP/MA N/均线/布林/BOLL（60 字窗内一律否决）；
  绑定否决 现价/收盘/开盘/涨停/跌停/前高/前低（仅当直接修饰该数值）。
  支撑/压力/阻力/平台/箱体/最高/最低/底线移出否决表。
P3 差额（空间/回撤/滑点/价差/差价/涨跌 N 元）不登记为价格坐标。
P4 非本股价格（批发价/出厂价/零售价/指导价/终端价/散瓶/整箱/吨价）不登记。
P5′ 紧跟「倍」的数值（含 1.8~2.0倍 / 12-14 倍区间写法）与「N 板」不登记。
"""

from __future__ import annotations

import pytest

from tradingagents.agents.utils.price_ref_registry import (
    PRICE_BASIS_DERIVED_ESTIMATE,
    PRICE_REF_SOURCE_KEY,
    audit_price_ref_registry,
)
from tradingagents.agents.utils.price_basis_gate import evaluate_price_basis_gate


TOL = 5e-3


def run(reports, td="2026-09-23", source=None):
    st = {"trade_date": td}
    st.update(reports)
    if source is not None:
        st[PRICE_REF_SOURCE_KEY] = source
    audit_price_ref_registry(st)
    return st["price_refs"], evaluate_price_basis_gate(st), st


def ref_by_value(refs, v):
    return [r for r in refs if abs(r["value"] - v) <= TOL]


# --- 4390ddfd 原句 fixture（pr-id 为生产登记号，仅作注释对照） ----------------

# pr-113（正确判定，应保持 derived_estimate）
S_PR113 = ("基准情景下（年化净利约 800 亿元，EPS 约 63.7 元），"
           "20倍 PE 对应 1274 元，现价 1251.24 元已贴近基准估值下沿")
# pr-118（误判：情景价位，「估值」二字不得单独触发）
S_PR118 = "| 市场风格回流低估值核心资产，估值修复驱动股价反抽至 1350-1400 元上方"
# pr-127（误判：技术指标坐标，「测算」叙事词不得触发；VWMA 属 P2 豁免）
S_PR127 = ("- **VWMA 关键基准位**：当前 VWMA 测算参考值为 **1281.96 元**"
           "（成交量加权价格参考带，非主力持仓成本）")
# pr-156 / pr-157（差额：上行空间/下行回撤，不登记为价格坐标）
S_PR156_157 = ("- 赔率测算：上行空间约 14.31 元，下行回撤达 30.04 元，"
               "下行风险与上行收益比为 2.10:1，做多赔率显著劣势")
# pr-200（差额：滑点）
S_PR200 = "大单抛盘若集中在 5 分钟内执行极易造成分时瞬间打穿 3-5 元滑点"
# pr-189（非本股价格：散瓶/整箱批发参考价）
S_PR189 = ("若飞天茅台散瓶/整箱批发参考价在此期间跌破关键心理防线"
           "（如 2000 元关口），将直接打破渠道蓄水池的心理防线，"
           "诱发中小二批商与黄牛的恐慌性低价抛售")
# pr-116（非价格计量：「最高4板」连板数，不得登记为价格 4.0）
S_PR116 = ("| **盘面涨停池生态** | 结构性投机活跃 | 题材分化期（最高4板） | "
           "1-3 天短线脉冲 | 题材投机为主，对大盘蓝筹无直接溢价外溢 |")
# pr-191（不变量：跌停字段级桥接必须保留）
S_PR191 = ("- **涨跌停板**：现价 1251.24 元距跌停板（1126.12 元）空间达 10.00%，"
           "无极端流动性锁死风险")


class TestP1DerivedEstimateOnlyFormula:
    def test_pe_formula_still_derived(self):
        # 20倍 PE 对应 1274 元：同子句 (a) PE + (b) 倍/对应 → derived_estimate
        refs, _, _ = run({"fundamentals_report": S_PR113})
        r = ref_by_value(refs, 1274.0)
        assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE

    def test_narrative_valuation_word_not_derived(self):
        # pr-118：「估值修复」是叙事词，无乘数/计算关联 → 不得判 derived
        refs, _, _ = run({"macro_report": S_PR118})
        r = ref_by_value(refs, 1400.0)
        assert r, "1400 元仍应登记为 price_ref"
        assert r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE

    def test_cross_clause_formula_still_derived(self):
        # P1′：模型词在数值前 60 字内同一句即可（可跨子句），关联词在本子句
        refs, _, _ = run(
            {"fundamentals_report": "按 12 倍 PE 测算，对应股价 27.14 元。"})
        r = ref_by_value(refs, 27.14)
        assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE

    def test_same_clause_formula_derived(self):
        # 同子句 (a)+(b) 齐备 → derived
        refs, _, _ = run(
            {"fundamentals_report": "按 12 倍 PE 测算对应股价 27.14 元。"})
        r = ref_by_value(refs, 27.14)
        assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE

    def test_narrative_words_alone_never_derived(self):
        # 估值中枢/估值底/测算/估算/市值/公允/安全边际 单独出现不触发
        text = ("估值中枢上移至 1300 元；测算底部约 1200 元；"
                "公允价值 1280 元；安全边际对应 1100 元。")
        refs, _, _ = run({"macro_report": text})
        for v in (1300.0, 1200.0, 1280.0):
            r = ref_by_value(refs, v)
            assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE, v


class TestP2IndicatorCoordinateVeto:
    def test_vwma_coordinate_not_derived(self):
        # pr-127：VWMA + 「测算」 → P2′ 硬否决（60 字窗内）
        refs, _, _ = run({"smart_money_report": S_PR127})
        r = ref_by_value(refs, 1281.96)
        assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE

    def test_hard_veto_indicator_word(self):
        # 布林下轨在 60 字窗内 → 硬否决（即便同句有 PE×倍）
        refs, _, _ = run(
            {"smart_money_report": "20 倍 PE 对应布林下轨支撑位 1238.53 元。"})
        r = ref_by_value(refs, 1238.53)
        assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE

    def test_bound_veto_only_when_modifying(self):
        # 「现价」直接修饰 1251.24 → 绑定否决；但不影响同句的估值公式
        refs, _, _ = run({"fundamentals_report": S_PR113})
        assert ref_by_value(refs, 1274.0)[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE
        r = ref_by_value(refs, 1251.24)
        assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE

    def test_support_no_longer_veto_word(self):
        # 「支撑」移出否决表：真估值公式写到支撑位仍判 derived（P1′ 把关）
        refs, _, _ = run(
            {"investment_plan": "按 20 倍 PE 对应支撑位 48.00 元。"})
        r = ref_by_value(refs, 48.0)
        assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE

    def test_counter_example_pe_without_calc_link(self):
        # 反例：PE 30 倍在 60 字内但无数值计算关联 → 1200 不得判 derived
        refs, _, _ = run(
            {"investment_plan": "PE 30 倍，处于历史高位，支撑位 1200 元。"})
        r = ref_by_value(refs, 1200.0)
        assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE

    def test_vwma_bridges_via_c5_when_pool_matches(self):
        # P2 后续路径：unspecified → C5 指名字段同值 → vendor_qfq
        source = {
            "stock_data": ("# price_basis: vendor_qfq\n"
                           "date,open,high,low,close\n"
                           "2026-09-23,1260.0,1266.0,1248.0,1251.24\n"),
            "indicators": {"vwma": 1281.96},
            "price_basis": "vendor_qfq",
            "symbol": "600519.SH",
            "trade_date": "2026-09-23",
        }
        refs, _, _ = run({"smart_money_report": S_PR127}, source=source)
        r = ref_by_value(refs, 1281.96)
        assert r and r[0]["basis"] == "vendor_qfq"
        assert (r[0]["provenance"] or "").startswith("pool_bridge:indicators.vwma")


class TestP3DifferenceNotPrice:
    def test_upside_downside_amounts_not_registered(self):
        # pr-156/157：上行空间 14.31 / 下行回撤 30.04 是差额，不登记
        refs, g, _ = run({"investment_plan": S_PR156_157})
        assert not ref_by_value(refs, 14.31)
        assert not ref_by_value(refs, 30.04)
        assert not g["violations"]

    def test_slippage_amount_not_registered(self):
        # pr-200：3-5 元滑点 → 5.0 不登记为价格
        refs, g, _ = run({"final_trade_decision": S_PR200})
        assert not ref_by_value(refs, 5.0)

    def test_rise_fall_amount_not_registered(self):
        # 「涨/跌 N 元」为差额；但「跌至/跌破 N 元」是坐标，仍须登记
        refs, _, _ = run(
            {"investment_plan": "短线反弹涨 18 元后回落；若跌破 1238.53 元止损。"})
        assert not ref_by_value(refs, 18.0)
        assert ref_by_value(refs, 1238.53)


class TestP4NonStockPrice:
    def test_commodity_price_not_registered(self):
        # pr-189：散瓶/整箱批发参考价 2000 元不是本股价格坐标
        refs, g, _ = run({"final_trade_decision": S_PR189})
        assert not ref_by_value(refs, 2000.0)
        assert not any("2000" in v["detail"] for v in g["violations"])


class TestP5BoardCountNotPrice:
    def test_limit_board_count_not_registered(self):
        # pr-116：「最高4板」是连板数，不是价格 4.0
        refs, _, _ = run({"sentiment_report": S_PR116})
        assert not ref_by_value(refs, 4.0)


# --- D-042 冻结语料原句（总控裁定的正当估值公式） --------------------------

# s05__r2：给予…倍 PE + 对应 + 折合，跨子句公式
S_S05R2 = ("- **估值支撑底线**：在 28 亿元的极限悲观净利润假设下，"
           "给予白马防御底线 18 倍 PE，对应极限防御市值约 504 亿元，"
           "折合股价防御底线在 38~40 元区间")
# s05__r4：（12-14倍 PE）推演 + 对应当前股价折算
S_S05R4 = ("- 按历史极端低位估值（12-14倍 PE）推演，市值底部支撑位在"
           "300-390亿元之间（对应当前股价折算底线支撑在38-42元区间），"
           "极端回撤空间有限，资产具备强反脆弱性")
# s03__r3：18-20倍PE + 对应 + 折合每股股价支撑位
S_S03R3 = ("- 在利润大幅滑落至190亿元基准下，市场给予制造业防守性估值中枢"
           "18-20倍PE，对应合理市值区间约3,420-3,800亿元，"
           "折合每股股价支撑位约在17.2-19.1元")
# s01__r4：历史PB最低点 + 对应支撑位；1.8~2.0倍 不得登记
S_S01R4 = ("历史PB最低点在1.8~2.0倍左右，对应极端悲观股价支撑位约为 "
           "**27.15 ~ 30.16 元**")
# s10__r3：「估值至 25 倍」乘数写法 + 对应支撑位
S_S10R3 = "若市场悲观情绪杀估值至 25 倍，对应极限估值支撑位在 6.00 - 6.50 元区间"
# s10__r4：PE 在前但数值子句与前一子句均无计算关联 → 不豁免（有意的保守结果）
S_S10R4 = ("* 对应 2026-08-21 现价 8.33 元（总股本 93.26 亿股，测算总市值约 "
           "776.8 亿元），极限极端情景下 PE 估值将被动推升至 57.3 倍，"
           "在此极端假设下，估值防守底线在 5.50 - 6.00 元区间")


class TestD042CorpusFixtures:
    def test_s05r2_derived(self):
        refs, _, _ = run({"fundamentals_report": S_S05R2})
        # 「38~40 元区间」仅 40 元被抽取（38 无锚词，既有抽取行为不变）
        r = ref_by_value(refs, 40.0)
        assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE
        for v in (38.0,):
            for x in ref_by_value(refs, v):
                assert x["basis"] == PRICE_BASIS_DERIVED_ESTIMATE

    def test_s05r4_derived(self):
        refs, _, _ = run({"fundamentals_report": S_S05R4})
        for v in (38.0, 42.0):
            r = ref_by_value(refs, v)
            assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE, v

    def test_s03r3_derived(self):
        refs, _, _ = run({"fundamentals_report": S_S03R3})
        for v in (17.2, 19.1):
            r = ref_by_value(refs, v)
            assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE, v

    def test_s01r4_derived_and_multiple_not_registered(self):
        refs, _, _ = run({"fundamentals_report": S_S01R4})
        for v in (27.15, 30.16):
            r = ref_by_value(refs, v)
            assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE, v
        # P5′：「1.8~2.0倍」「2.0倍」是倍数，不登记为价格
        assert not ref_by_value(refs, 1.8)
        assert not ref_by_value(refs, 2.0)

    def test_s10r3_derived(self):
        refs, _, _ = run({"macro_report": S_S10R3})
        for v in (6.0, 6.5):
            r = ref_by_value(refs, v)
            assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE, v

    def test_s10r4_not_derived(self):
        # 计算关联不明确，按修正规格不豁免（有意的保守结果）；
        # 「5.50 - 6.00 元」仅 6.00 被抽取
        refs, _, _ = run({"fundamentals_report": S_S10R4})
        r = ref_by_value(refs, 6.0)
        assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE
        for x in ref_by_value(refs, 5.5):
            assert x["basis"] != PRICE_BASIS_DERIVED_ESTIMATE


# --- D-042 二次修正：计算关联词必须在数值所在子句、数值之前（删「前一子句」） ---

# s01__r4：34.40 是技术颈线，只是借用前一子句的「对应」
S_S01R4_NECK = ("在宏观滞胀或海外脱钩极端情景下，航发动力依托 402.10 亿元归母净资产"
                "与垄断总装地位，抗压测底线坚固（极值 PB 1.8 倍对应 27.15 元，"
                "短线颈线 34.40 元具备极强支撑）")
# s01__r2：35.91 是现价，括号内无数值前的关联词
S_S01R2_SPOT = "（较当前35.91元具备约16%-24%的潜在缓冲空间）"
# s01__r3：「对应」出现在数值之后，32-35 元是价格区间而非公式结果
S_S01R3_RANGE = "PE(TTM) 在底部 32-35 元附近对应历史估值百分位低于 20%"


class TestD042PrevClauseCounterexamples:
    def test_neckline_not_derived(self):
        # 34.40 的技术颈线不得借用前一子句「对应」被判 derived；
        # 同句 27.15 的估值公式仍判 derived
        refs, _, _ = run({"fundamentals_report": S_S01R4_NECK})
        r = ref_by_value(refs, 34.40)
        assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE
        r = ref_by_value(refs, 27.15)
        assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE

    def test_spot_price_not_derived(self):
        refs, _, _ = run({"investment_plan": S_S01R2_SPOT})
        r = ref_by_value(refs, 35.91)
        assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE

    def test_calc_word_after_number_not_derived(self):
        # 「对应」在数值之后 → 不满足「数值之前」，35 元区间不判 derived
        refs, _, _ = run({"fundamentals_report": S_S01R3_RANGE})
        for v in (35.0,):
            r = ref_by_value(refs, v)
            assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE


class TestInvariants:
    def test_limit_down_bridge_preserved(self):
        # pr-191 不变量：跌停语义 + 字段级同值 → pool_bridge 保留
        source = {
            "stock_data": ("# price_basis: vendor_qfq\n"
                           "date,open,high,low,close\n"
                           "2026-09-23,1260.0,1266.0,1248.0,1251.24\n"),
            "indicators": {},
            "price_basis": "vendor_qfq",
            "symbol": "600519.SH",
            "trade_date": "2026-09-23",
        }
        refs, _, _ = run({"final_trade_decision": S_PR191}, source=source)
        r = ref_by_value(refs, 1126.12)
        assert r and r[0]["basis"] == "vendor_qfq"
        assert any(f.startswith("derived.limit_down")
                   for f in r[0].get("bridge_fields") or [])
