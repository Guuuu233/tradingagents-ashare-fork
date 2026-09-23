"""DAV-1224 C1 抽取假阳性过滤测试（GREEN 基线，对应 W1 第一部分）。

非价格单位与语境（亿/万/%/倍/股/日/月/年/日期/列表序号/JSON 块/每股财务）、
外币与境外标的报价，一律不生成 A 股价格坐标；禁止正则回溯截断。
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


class TestC1ExtractionGuards:
    def test_market_cap_yi_not_extracted(self):
        # 「900亿元」不得回溯截断成 90.0（市值/金额非价格）
        refs, g, _ = run({"fundamentals_report": "按12倍PE测算，市值支撑位在900亿元。"})
        assert not has_val(refs, 90.0)
        assert not has_val(refs, 900.0)

    def test_ema_day_fragment_not_extracted(self):
        # 「10日 EMA（933 元）」中的 10 不得抽成 1.0
        refs, g, _ = run({"market_report": "现价持续承压于 10日 EMA（933 元）。"})
        assert not has_val(refs, 1.0)
        assert has_val(refs, 933.0)

    def test_date_year_not_extracted(self):
        # 「（2026-08-14）」不得抽成 2026.0
        refs, g, _ = run({"market_report": "收盘（2026-08-14）46.30 元，支撑位 44.00 元。"})
        assert not has_val(refs, 2026.0)
        assert has_val(refs, 46.3) and has_val(refs, 44.0)

    def test_percent_multiple_shares_not_extracted(self):
        refs, g, _ = run({"news_report": "单日下跌 4.90%，换手率放大 2.0倍，成交 3.5亿股。"})
        assert not has_val(refs, 4.9) and not has_val(refs, 2.0) and not has_val(refs, 3.5)

    def test_number_space_unit_not_extracted(self):
        refs, g, _ = run({"news_report": "融资余额 120 亿元，近 5 日净买入。"})
        assert not has_val(refs, 120.0) and not has_val(refs, 5.0)

    def test_foreign_quote_not_extracted(self):
        # 外币/境外语境句内所有数字均不产生 A 股 price_ref
        refs, g, _ = run({"macro_report": "美股收于 579.27 美元，WTI 原油 68.5 美元。"})
        assert not refs
        # 干净句中的 A 股报价不受影响（语境判定在子句级）
        refs, g, _ = run({"macro_report": "美股收于 579.27 美元。现价 35.20 元。"})
        assert has_val(refs, 35.2) and not has_val(refs, 579.27)

    def test_per_share_financial_not_extracted(self):
        refs, g, _ = run({"fundamentals_report": "每股净资产 12.30 元，EPS 约 1.8 元，支撑位 33.00 元。"})
        assert not has_val(refs, 12.3) and not has_val(refs, 1.8)
        assert has_val(refs, 33.0)

    def test_json_blob_numbers_not_extracted(self):
        blob = 'MANAGER_VERDICT {"reason": "x", "confidence": 0.72, "winner": "bull"}'
        refs, g, _ = run({"final_trade_decision": f"{blob}"})
        assert not has_val(refs, 0.72)



# ---------------------------------------------------------------------------
# C2 — typed disclosure 与转换语义
# ---------------------------------------------------------------------------
