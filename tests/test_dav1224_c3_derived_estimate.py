"""DAV-1224 C3 derived_estimate 语义角色测试（GREEN 基线，对应 W2）。

derived 判定绑定到数字本身（同子句、前 25 字内估值算术强词）；弱词不触发；
真报价保护；derived_estimate 不承担 decision-driving 问责、不可执行、
不参与跨口径混用判定、保留 lineage。
"""

from __future__ import annotations

import pytest

from tradingagents.agents.utils.price_ref_registry import (
    PRICE_BASIS_DERIVED_ESTIMATE, audit_price_ref_registry)
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


class TestC3DerivedEstimateRole:
    def test_pe_derived_value_marked_derived_estimate(self):
        # PB/PE 推导的估值锚 → derived_estimate，不伪装 vendor_qfq
        refs, g, _ = run({"fundamentals_report": "按 12 倍 PE 测算，对应股价 27.14 元。"})
        r = [r for r in refs if abs(r["value"] - 27.14) <= TOL]
        assert r and r[0]["basis"] == PRICE_BASIS_DERIVED_ESTIMATE
        assert r[0]["provenance"].startswith("role:derived_estimate|")

    def test_weak_words_do_not_trigger_derived(self):
        # 「假设/悲观/底线」弱词不得单独触发 derived
        refs, g, _ = run({"investment_plan": "悲观情景假设下，底线止损 30.00 元。"})
        r = [r for r in refs if abs(r["value"] - 30.0) <= TOL]
        assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE

    def test_derived_estimate_not_decision_driving(self):
        refs, g, _ = run({"fundamentals_report": "按 12 倍 PE 测算，对应股价 27.14 元。"})
        assert not any(
            v["kind"].startswith("decision_driving") and "27.14" in v["detail"]
            for v in g["violations"]
        )

    def test_derived_estimate_cannot_back_executable(self):
        # derived 值出现在 decision field 的可执行词位 → wrong_basis（derived 非 qfq）
        refs, g, _ = run({"final_trade_decision": "按 15 倍 PE 测算，目标价 41.00 元。"})
        assert any(
            v["kind"] == "executable_level_wrong_basis" for v in g["violations"]
        )

    def test_derived_estimate_exempt_from_as_of_gap(self):
        refs, g, st = run({"fundamentals_report": "按 12 倍 PE 测算，对应股价 27.14 元。"})
        assert not any(
            gap["kind"] == "missing_as_of" and "27.14" in gap["detail"]
            for gap in st["price_basis_gaps"]
        )

    def test_declared_basis_ref_not_derated(self):
        refs, g, _ = run({"investment_plan": "按前复权口径测算目标价 45.00 元，折算得出。"})
        r = [r for r in refs if abs(r["value"] - 45.0) <= TOL]
        assert r and r[0]["basis"] == "vendor_qfq"


# ---------------------------------------------------------------------------
# C4 — 共享可执行价位解析
# ---------------------------------------------------------------------------
