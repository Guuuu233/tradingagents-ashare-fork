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
