"""DAV-1108 stage A: 营业成本 vs 营业总成本 field-semantics regression tests.

Covers:
- canonical alias split (营业总成本 / operating_expenses must NOT alias 营业成本)
- independent parsing of both cost fields (no total-cost masquerading as COGS)
- explicit gap marking when 营业成本 is absent
- deterministic consistency rules: 营业总成本 >= 营业成本,
  毛利率 ≈ 1 - 营业成本/营业收入
- assembly-layer caliber guard notes (income_statement_cost_field_notes)
"""

import json
import os

import pandas as pd
import pytest

from tradingagents.agents.utils.financial_period_compliance import (
    KIND_COST_FIELD_INCONSISTENT,
    KIND_GROSS_MARGIN_INCONSISTENT,
    _match_canonical_field,
    check_financial_period_compliance,
    parse_financial_statement_inputs,
)
from tradingagents.dataflows.utils import income_statement_cost_field_notes


FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__),
    "fixtures",
    "financial_cost_fields",
    "cost_field_cases.json",
)


def _income_md(rows: list[dict]) -> str:
    """Build a markdown income statement from row dicts (raw yuan values)."""
    cols = ["报告日"] + [c for c in rows[0] if c != "报告日"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


# ── Canonical alias split ────────────────────────────────────────────────────


class TestCanonicalAliasSplit:
    def test_total_cost_is_own_canonical(self):
        assert _match_canonical_field("income_statement", "营业总成本") == "营业总成本"

    def test_cost_is_own_canonical(self):
        assert _match_canonical_field("income_statement", "营业成本") == "营业成本"

    def test_english_fields_split(self):
        assert (
            _match_canonical_field("income_statement", "operating_expenses")
            == "营业总成本"
        )
        assert (
            _match_canonical_field("income_statement", "operating_costs")
            == "营业成本"
        )

    def test_gross_margin_canonical(self):
        assert _match_canonical_field("income_statement", "毛利率") == "毛利率"
        assert _match_canonical_field("income_statement", "销售毛利率") == "毛利率"


# ── Independent field parsing (恒瑞 real-report fixture) ────────────────────


class TestIndependentCostFieldParsing:
    def test_both_fields_parse_independently(self):
        # 恒瑞 600276 2026Q1 real values: 营收81.4057亿/营业成本10.9095亿/营业总成本56.4293亿
        md = _income_md([
            {"报告日": "20260331", "营业收入": "8140570000",
             "营业成本": "1090950000", "营业总成本": "5642930000"},
        ])
        ok, reason, parsed = parse_financial_statement_inputs(
            {"income_statement": md}
        )
        assert ok, reason
        nf = parsed["statements"]["income_statement"][0]["_numeric_fields"]
        assert nf["营业成本"] == pytest.approx(1090950000.0)
        assert nf["营业总成本"] == pytest.approx(5642930000.0)
        # total cost must NOT be bound under the 营业成本 canonical name
        assert nf["营业成本"] != nf["营业总成本"]

    def test_missing_cost_field_marks_gap(self):
        md = _income_md([
            {"报告日": "20260331", "营业收入": "8140570000",
             "营业总成本": "5642930000", "净利润": "1900000000"},
        ])
        ok, reason, parsed = parse_financial_statement_inputs(
            {"income_statement": md}
        )
        assert ok, reason
        nf = parsed["statements"]["income_statement"][0]["_numeric_fields"]
        # 营业总成本 value must not be aliased into 营业成本
        assert "营业成本" not in nf
        assert nf["营业总成本"] == pytest.approx(5642930000.0)
        gaps = parsed["field_gaps"]
        assert any(
            g["missing_field"] == "营业成本" and g["present_field"] == "营业总成本"
            for g in gaps
        )

    def test_gap_surfaces_in_compliance_result(self):
        md = _income_md([
            {"报告日": "20260331", "营业收入": "8140570000",
             "营业总成本": "5642930000"},
        ])
        res = check_financial_period_compliance(
            "本报告不含财务字段引用。", {"income_statement": md}
        )
        assert "field_gaps" in res
        assert any(g["missing_field"] == "营业成本" for g in res["field_gaps"])


# ── Deterministic consistency rules ─────────────────────────────────────────


class TestCostFieldConsistency:
    def test_inverted_total_cost_violation(self):
        md = _income_md([
            {"报告日": "20260331", "营业收入": "100000000",
             "营业成本": "80000000", "营业总成本": "70000000"},
        ])
        res = check_financial_period_compliance("", {"income_statement": md})
        kinds = [v["kind"] for v in res["violations"]]
        assert KIND_COST_FIELD_INCONSISTENT in kinds

    def test_gross_margin_consistent_clean(self):
        # 京东方 2026Q1: rev 510.01亿 cost 430.47亿 GM 15.59% (1-430.47/510.01=0.15594)
        md = _income_md([
            {"报告日": "20260331", "营业收入": "51001000000",
             "营业成本": "43047000000", "毛利率": "15.59"},
        ])
        res = check_financial_period_compliance("", {"income_statement": md})
        assert KIND_GROSS_MARGIN_INCONSISTENT not in [
            v["kind"] for v in res["violations"]
        ]

    def test_gross_margin_from_total_cost_is_violation(self):
        # 恒瑞 mislabel scenario: 用营业总成本推毛利 → 1-56.4293/81.4057 ≈ 30.68%
        # 与真实毛利率 (1-10.9095/81.4057 ≈ 86.60%) 矛盾 → violation
        md = _income_md([
            {"报告日": "20260331", "营业收入": "8140570000",
             "营业成本": "1090950000", "营业总成本": "5642930000",
             "毛利率": "30.68"},
        ])
        res = check_financial_period_compliance("", {"income_statement": md})
        kinds = [v["kind"] for v in res["violations"]]
        assert KIND_GROSS_MARGIN_INCONSISTENT in kinds


# ── Assembly-layer caliber guard ─────────────────────────────────────────────


class TestIncomeCostFieldNotes:
    def test_missing_cost_column_emits_gap_note(self):
        df = pd.DataFrame([
            {"报告日": "20260331", "营业总收入": 8140570000.0,
             "营业总成本": 5642930000.0},
        ])
        note = income_statement_cost_field_notes(df)
        assert "营业成本" in note and "≠" in note
        assert "缺失" in note

    def test_english_columns_gap_note(self):
        df = pd.DataFrame([
            {"period_end": "2026-03-31", "operating_revenue": 8140570000.0,
             "operating_expenses": 5642930000.0},
        ])
        note = income_statement_cost_field_notes(df)
        assert "缺失" in note

    def test_inverted_columns_emit_warning(self):
        df = pd.DataFrame([
            {"报告日": "20260331", "营业成本": 80000000.0,
             "营业总成本": 70000000.0},
        ])
        note = income_statement_cost_field_notes(df)
        assert "口径告警" in note

    def test_normal_columns_no_note(self):
        df = pd.DataFrame([
            {"报告日": "20260331", "营业成本": 1090950000.0,
             "营业总成本": 5642930000.0},
        ])
        assert income_statement_cost_field_notes(df) == ""


# ── Fixture-driven regression (real-report + synthetic flagged) ──────────────


def _fixture_cases():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)["cases"]


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda c: c["ticker"])
def test_fixture_cost_fields_consistent(case):
    row = {"报告日": case["period"], "营业收入": str(case["营业收入"])}
    if case.get("营业成本") is not None:
        row["营业成本"] = str(case["营业成本"])
    if case.get("营业总成本") is not None:
        row["营业总成本"] = str(case["营业总成本"])
    if case.get("毛利率") is not None:
        row["毛利率"] = str(case["毛利率"])
    md = _income_md([row])
    ok, reason, parsed = parse_financial_statement_inputs(
        {"income_statement": md}
    )
    assert ok, reason
    nf = parsed["statements"]["income_statement"][0]["_numeric_fields"]
    if case.get("营业成本") is not None:
        assert nf["营业成本"] == pytest.approx(float(case["营业成本"]))
    if case.get("营业总成本") is not None:
        assert nf["营业总成本"] == pytest.approx(float(case["营业总成本"]))
        # the two canonical fields stay independent
        if case.get("营业成本") is not None:
            assert nf["营业成本"] != nf["营业总成本"]

    res = check_financial_period_compliance("", {"income_statement": md})
    if case["expect"] == "clean":
        bad_kinds = {KIND_COST_FIELD_INCONSISTENT, KIND_GROSS_MARGIN_INCONSISTENT}
        assert not [v for v in res["violations"] if v["kind"] in bad_kinds], (
            f"{case['ticker']} {case['name']}: unexpected violation {res['violations']}"
        )
