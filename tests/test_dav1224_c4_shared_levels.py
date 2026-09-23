"""DAV-1224 C4 共享可执行价位解析测试（GREEN 基线，对应 W3）。

registry 与 gate 共用同一词表与解析/过滤函数；区间两端都登记；
列表序号/百分比/数量/日期伪价位过滤。
"""

from __future__ import annotations

import pytest

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


class TestC4SharedExecutableParser:
    @pytest.mark.parametrize("anchor", [
        "目标价", "目标位", "止盈", "止损", "入场", "进场",
        "买入价", "卖出价", "建仓价", "开仓价", "出场价", "加仓价", "减仓价",
    ])
    def test_anchor_value_registered_as_ref(self, anchor):
        refs, g, _ = run({"investment_plan": f"{anchor} 42.50 元。"})
        assert has_val(refs, 42.5), f"{anchor} 未登记 ref"

    def test_range_both_ends_registered(self):
        # 「35.20 - 35.50元」两端都要登记（v1 F2 修复）
        refs, g, _ = run({"investment_plan": "建议入场区间：35.20 - 35.50元，止损 34.00 元。"})
        assert has_val(refs, 35.2) and has_val(refs, 35.5)
        assert has_val(refs, 34.0)

    def test_markdown_list_ordinal_not_executable(self):
        refs, g, _ = run({"final_trade_decision": "触发后执行止损。\n2. **宏观**：加息预期升温。"})
        assert not any(
            v["kind"] == "unbacked_executable_level" and "2.0" in v["detail"]
            for v in g["violations"]
        )

    def test_false_levels_filtered(self):
        # 百分比/数量/日期不作 executable level
        refs, g, _ = run({"final_trade_decision": "止损回撤控制在 -4.90%，减持 2.0亿股。"})
        assert not any(
            v["kind"] == "unbacked_executable_level" and ("4.9" in v["detail"] or "2.0" in v["detail"])
            for v in g["violations"]
        )

    def test_gate_and_registry_share_vocabulary(self):
        # gate 能识别的 level，registry 必有登记机会：无 provenance 时是
        # wrong_basis/unbacked 的真实 blocker，而非词表分叉的假 blocker
        refs, g, _ = run({"investment_plan": "建议入场区间：35.20 元，止损 34.00 元。"})
        assert has_val(refs, 35.2)
        # 35.20 已登记但 basis unspecified → wrong_basis（真实 blocker）
        assert "executable_level_wrong_basis" in kinds(g)
        assert not any(
            v["kind"] == "unbacked_executable_level" and "35.2" in v["detail"]
            for v in g["violations"]
        )


# ---------------------------------------------------------------------------
# C5 — 严格 source-backed 桥接
# ---------------------------------------------------------------------------

