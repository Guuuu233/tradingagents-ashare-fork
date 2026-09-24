"""DAV-1255 可执行价位抽取器误读日期/指标周期/金额（C4 补漏，E1–E4）。

真实原句 fixture：
- 「硬性止损位：**7月31日 最低 34.45 元**」→ 34.45
- 「目标价：**50 日 SMA 37.36 元**」→ 37.36
- 「第一反弹目标位：**8月13日 VWMA 51.49 元**」→ 51.49
- 「止损……8月19日盘面散户小单净买入12.62亿元」→ 不产生可执行价位
反例保持：「止损位 45 元」→ 45；「目标价 1500」→ 1500；
「止损位 2026-08-06 收盘 35.91」按现有年份规则 → 35.91。
"""

from __future__ import annotations

import pytest

from tradingagents.agents.utils.price_ref_registry import (
    audit_price_ref_registry,
    extract_executable_levels,
)
from tradingagents.agents.utils.price_basis_gate import evaluate_price_basis_gate


TOL = 5e-3


def _vals(text: str):
    return [v for v, _s, _e in extract_executable_levels(text)]


def _has(vals, v):
    return any(abs(x - v) <= TOL for x in vals)


class TestDatePeriodAmountSkip:
    def test_date_prefix_stop_level(self):
        vals = _vals("硬性止损位：**7月31日 最低 34.45 元**")
        assert _has(vals, 34.45)
        assert not _has(vals, 7) and not _has(vals, 31)

    def test_indicator_period_target(self):
        vals = _vals("目标价：**50 日 SMA 37.36 元**")
        assert _has(vals, 37.36)
        assert not _has(vals, 50)

    def test_date_prefix_vwma_target(self):
        vals = _vals("第一反弹目标位：**8月13日 VWMA 51.49 元**")
        assert _has(vals, 51.49)
        assert not _has(vals, 8) and not _has(vals, 13)

    def test_amount_not_a_level(self):
        vals = _vals("止损……8月19日盘面散户小单净买入12.62亿元")
        assert not _has(vals, 12.62)
        assert not _has(vals, 8) and not _has(vals, 19)
        assert vals == []

    # ---- 反例（必须保持） ----
    def test_plain_stop_level(self):
        assert _has(_vals("止损位 45 元"), 45.0)

    def test_plain_target_number(self):
        assert _has(_vals("目标价 1500"), 1500.0)

    def test_full_date_year_rule(self):
        # 现有年份规则：2026-08-06 各段被 _is_false_level 过滤，
        # 跳过后继续取同窗数值 → 35.91。
        vals = _vals("止损位 2026-08-06 收盘 35.91")
        assert vals == [35.91]


class TestNoFalseUnbackedViolation:
    """gate 与 registry 同步生效：日期/周期/金额不再报 unbacked。"""

    def _run(self, text):
        st = {"trade_date": "2026-08-14", "final_trade_decision": text}
        audit_price_ref_registry(st)
        return evaluate_price_basis_gate(st)

    def test_no_unbacked_for_date_period_amount(self):
        g = self._run(
            "硬性止损位：**7月31日 最低 34.45 元**；"
            "目标价：**50 日 SMA 37.36 元**。"
        )
        false_hits = [
            v for v in g["violations"]
            if v["kind"] == "unbacked_executable_level"
            and any(s in v["detail"] for s in (" 7", " 31", " 50"))
        ]
        assert not false_hits
