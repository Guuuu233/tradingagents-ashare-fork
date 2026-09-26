"""DAV-1224 C2 typed disclosure 与转换语义测试（GREEN 基线，对应 W1 第二部分）。

判定对象是「该值是否该类披露价」；「折合/折算」估值算术不构成口径转换。
"""

from __future__ import annotations

from tradingagents.agents.utils.price_ref_registry import audit_price_ref_registry
from tradingagents.agents.utils.price_basis_gate import evaluate_price_basis_gate


TOL = 5e-3


def run(reports, td="2026-08-14"):
    st = {"trade_date": td}
    st.update(reports)
    audit_price_ref_registry(st)
    return st["price_refs"], evaluate_price_basis_gate(st), st


def kinds(gate):
    return [v["kind"] for v in gate["violations"]]


def values(refs):
    return [r["value"] for r in refs]


def has_val(refs, v):
    return any(abs(r["value"] - v) <= TOL for r in refs)


class TestC2TypedDisclosureAndConversion:
    def test_commodity_dazong_not_block_trade(self):
        refs, g, _ = run({"news_report": "原油及大宗涨价推高成本，现价 50.20 元。"})
        assert not any(r.get("disclosure_type") == "block_trade" for r in refs)

    def test_cross_word_fa_hang_not_issuance(self):
        refs, g, _ = run({"news_report": "突发行业利好发酵，现价 50.20 元。"})
        assert not any(r.get("disclosure_type") == "issuance" for r in refs)

    def test_note_issuance_not_disclosure(self):
        refs, g, _ = run({"news_report": "公司中期票据发行完成，发行价 100.00 元。"})
        assert not any(r.get("disclosure_type") == "issuance" for r in refs)

    def test_position_decrease_not_shareholder_disclosure(self):
        refs, g, _ = run({"trader_investment_plan": "建议逢高减持仓位至 50%，减持价位 45.00 元附近挂单。"})
        assert not any(r.get("disclosure_type") == "shareholder_decrease" for r in refs)

    def test_reverse_repurchase_not_repurchase(self):
        refs, g, _ = run({"macro_report": "央行逆回购操作 7 天期利率 1.80%，规模 500 亿元。"})
        assert not any(r.get("disclosure_type") == "repurchase" for r in refs)

    def test_coordinate_price_in_repurchase_context_not_disclosure(self):
        refs, g, _ = run({"news_report": "回购为股价提供 30.50 元安全垫，站稳关口。"})
        assert not any(r.get("disclosure_type") == "repurchase" for r in refs)

    def test_other_symbol_issuance_price(self):
        from tradingagents.agents.utils.price_ref_registry import classify_typed_disclosure
        out = classify_typed_disclosure(
            {"context": "新股华电新能开启申购，发行价 3.18 元。",
             "disclosure_type": "issuance", "value": 3.18},
            stock_name="航发动力",
        )
        assert out["verdict"] == "false" and out["subtype"] == "other_symbol"

    def test_block_trade_sentence_not_auto_typed(self):
        # [DAV-1321 N3] 逐值判定：「今日大宗交易成交 78.61 元」中 78.61
        # 正是大宗披露成交价 → raw + disclosure_type=block_trade；该值是
        # 真披露价不再误判 unspecified（坐标位/报价侧数字仍不挂）。
        refs, g, _ = run({"news_report": "今日大宗交易成交 78.61 元，较收盘折价 6.45%。"})
        r = [r for r in refs if abs(r["value"] - 78.61) <= TOL]
        assert r and r[0]["basis"] == "raw"
        assert r[0].get("disclosure_type") == "block_trade"

    def test_valuation_zhe_suan_not_conversion(self):
        # 「折合每股」估值算术 → 不再登记 conversion，不产出 invalid_conversion
        refs, g, _ = run({"investment_plan": "大宗交易 78.61 元，折合每股支撑区间约为18.15-19.36元。"})
        assert "invalid_conversion" not in kinds(g)
        assert not any(r.get("conversion") for r in refs)

    def test_real_conversion_semantics_still_recorded(self):
        # 复权语义 + 换算动词 + 无 factor provenance → invalid_conversion 仍然成立
        refs, g, _ = run({"investment_plan": "按前复权口径折算目标价 45.00 元。"})
        assert any(
            r.get("conversion") and r["conversion"]["factor"] is None for r in refs
        )
        assert "invalid_conversion" in kinds(g)


# ---------------------------------------------------------------------------
# C3 — derived_estimate 语义角色
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# DAV-1321 复审③ — N3 逐值判定收紧回归（总控抽查的五个误标样例 + 保持样例）
# ---------------------------------------------------------------------------


class TestDav1321PerValueMislabels:
    """披露口径只挂披露事项本身的价格；同句技术位/显式 qfq/行情高低点不挂。"""

    def _basis_of(self, reports, value):
        refs, _, _ = run(reports)
        r = [x for x in refs if abs(x["value"] - value) <= TOL]
        return r

    def test_yearline_in_blocktrade_sentence_not_raw(self):
        # 28f6a1e0: 「大宗辅料涨价」句内触发 dtype=block_trade，但 39.21 是
        # 年线（200日均线）——技术位，不是大宗成交价。
        refs = self._basis_of(
            {"investment_plan":
             "相反，上游大宗辅料涨价向成本端传导迅速，且 108.08 亿元的高额两融"
             "余额在遭遇年线（39.21 元）受阻回落时，平仓风险更具短线传导即时性"},
            39.21)
        assert refs and all(r.get("disclosure_type") is None for r in refs)
        assert all(r["basis"] != "raw" for r in refs)

    def test_sma_value_in_blocktrade_sentence_not_raw(self):
        # af25901e: 「close_50_sma 117.31元」是均线值。
        refs = self._basis_of(
            {"news_report":
             "大宗交易折价8.25%成交，恰好与中长期均线汇聚位"
             "（close_200_sma 115.87元、close_50_sma 117.31元）重叠"},
            117.31)
        assert refs and all(r["basis"] != "raw" for r in refs)

    def test_explicitly_qfq_labeled_value_not_raw(self):
        # ebc41de2: 「26.92 元（qfq）」显式 qfq，且句子在声明不可直接比较。
        refs = self._basis_of(
            {"final_trade_decision":
             "大宗交易成交价（pit_raw）严禁与 26.92 元（qfq）直接进行折溢价"
             "计算作为短线抛压参考，亦严禁跨坐标推导支撑线"},
            26.92)
        assert refs and all(r["basis"] == "vendor_qfq" for r in refs)

    def test_prior_low_in_repurchase_sentence_not_pit_raw(self):
        # f088d66a: 「06-29 前期低点 34.31 元」是行情低点。
        refs = self._basis_of(
            {"volume_price_report":
             "**中线强防守底线**：**34.31 - 34.80 元**（06-29 前期低点 34.31 元"
             "与公司回购均价平台 34.80 元）"},
            34.31)
        assert refs and all(r["basis"] != "pit_raw" for r in refs)

    def test_breakdown_price_in_decrease_sentence_not_pit_raw(self):
        # fdc9352b: 「股价放量击穿 1268 元」是技术位，不是减持均价。
        refs = self._basis_of(
            {"volume_price_report":
             "若市场流动性进一步向成长题材倾斜，大资金持续减持，股价放量击穿 "
             "1268 元且收盘无法收回，跌势将向 1250 元整数关口延伸"},
            1268.0)
        assert refs and all(r["basis"] != "pit_raw" for r in refs)

    def test_genuine_disclosure_prices_still_attached(self):
        # 保持样例：成交均价/回购均价仍挂披露口径
        refs = self._basis_of(
            {"news_report": "中芯国际今日发生一笔大宗交易，成交均价 115.00 元，"
                            "较当日收盘价 125.34 元大幅折价 8.25%"},
            115.00)
        assert refs and refs[0]["basis"] == "raw" \
            and refs[0]["disclosure_type"] == "block_trade"
        refs = self._basis_of(
            {"news_report": "公司公告回购价格区间为 46.02 - 46.06 元/股"},
            46.02)
        assert refs and refs[0]["basis"] == "pit_raw" \
            and refs[0]["disclosure_type"] == "repurchase"
