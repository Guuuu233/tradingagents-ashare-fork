"""Tests for financial report period_kind classification and cutoff header integration.

D-009 / audit plan §P0-3a contract tests.
"""

from __future__ import annotations

from datetime import date
import pytest

from tradingagents.dataflows.financial_announce import (
    FinancialPeriodKind,
    classify_financial_period_kind,
    financial_cutoff_header,
    resolve_effective_announce_date,
)


# ── classify_financial_period_kind unit tests ────────────────────────


@pytest.mark.parametrize(
    "period, expected_kind, expected_label",
    [
        ("20240331", "first_quarter", "2024Q1"),
        ("20240630", "half_year_cumulative", "2024H1"),
        ("20240930", "nine_month_cumulative", "2024Q3"),
        ("20241231", "annual_cumulative", "2024A"),
    ],
)
def test_classify_income_periods(period, expected_kind, expected_label):
    res = classify_financial_period_kind(period, "income")
    assert isinstance(res, FinancialPeriodKind)
    assert res.period_kind == expected_kind
    assert res.reported_period_label == expected_label
    assert res.derivation_formula == "not_derived"
    assert "Q2" not in res.reported_period_label
    assert "Q2" not in res.period_kind


@pytest.mark.parametrize(
    "period, expected_kind, expected_label",
    [
        ("20240331", "first_quarter", "2024Q1"),
        ("20240630", "half_year_cumulative", "2024H1"),
        ("20240930", "nine_month_cumulative", "2024Q3"),
        ("20241231", "annual_cumulative", "2024A"),
    ],
)
def test_classify_cashflow_periods(period, expected_kind, expected_label):
    res = classify_financial_period_kind(period, "cashflow")
    assert isinstance(res, FinancialPeriodKind)
    assert res.period_kind == expected_kind
    assert res.reported_period_label == expected_label
    assert res.derivation_formula == "not_derived"
    assert "Q2" not in res.reported_period_label
    assert "Q2" not in res.period_kind


@pytest.mark.parametrize(
    "period, expected_label",
    [
        ("20240331", "2024Q1"),
        ("20240630", "2024H1"),
        ("20240930", "2024Q3"),
        ("20241231", "2024A"),
    ],
)
def test_classify_balance_periods(period, expected_label):
    res = classify_financial_period_kind(period, "balance")
    assert isinstance(res, FinancialPeriodKind)
    assert res.period_kind == "period_end_stock"
    assert res.reported_period_label == expected_label
    assert res.derivation_formula == "not_derived"
    assert "Q2" not in res.reported_period_label
    assert "cumulative" not in res.period_kind


def test_classify_invalid_inputs():
    res1 = classify_financial_period_kind("20240501", "income")
    assert res1.period_kind == "unknown"
    assert res1.derivation_formula == "not_derived"

    res2 = classify_financial_period_kind("20240630", "invalid_statement")
    assert res2.period_kind == "unknown"
    assert res2.derivation_formula == "not_derived"

    res3 = classify_financial_period_kind(None, "income")
    assert res3.period_kind == "unknown"
    assert res3.derivation_formula == "not_derived"

    res4 = classify_financial_period_kind("invalid_date", "income")
    assert res4.period_kind == "unknown"
    assert res4.derivation_formula == "not_derived"


# ── financial_cutoff_header tests with statement_kind ─────────────────


def test_financial_cutoff_header_preserves_legacy_when_statement_kind_none():
    eff = resolve_effective_announce_date("20240630", ["20240809"])
    header = financial_cutoff_header(eff, "2024-08-20")
    assert "财务数据截至 2024H1" in header
    assert "生效公告日 2024-08-09" in header
    assert "period_kind=" not in header


def test_financial_cutoff_header_with_statement_kind_income():
    eff = resolve_effective_announce_date("20240630", ["20240809"])
    header = financial_cutoff_header(eff, "2024-08-20", statement_kind="income")
    assert "财务数据截至 2024H1" in header
    assert "生效公告日 2024-08-09" in header
    assert "reported_period_label=2024H1" in header
    assert "period_kind=half_year_cumulative" in header
    assert "derivation_formula=not_derived" in header
    assert ("不是 Q2" in header or "不是Q2" in header or "禁止当作 Q2" in header or "禁止当作Q2" in header)
    assert "2024Q2" not in header


def test_financial_cutoff_header_with_statement_kind_balance():
    eff = resolve_effective_announce_date("20240630", ["20240809"])
    header = financial_cutoff_header(eff, "2024-08-20", statement_kind="balance")
    assert "财务数据截至 2024H1" in header
    assert "period_kind=period_end_stock" in header
    assert "reported_period_label=2024H1" in header
    assert "derivation_formula=not_derived" in header
    assert "期末点值" in header


def test_financial_cutoff_header_with_statement_kind_cashflow():
    eff = resolve_effective_announce_date("20240630", ["20240809"])
    header = financial_cutoff_header(eff, "2024-08-20", statement_kind="cashflow")
    assert "财务数据截至 2024H1" in header
    assert "period_kind=half_year_cumulative" in header
    assert "reported_period_label=2024H1" in header
    assert "derivation_formula=not_derived" in header
    assert ("不是 Q2" in header or "不是Q2" in header or "禁止当作 Q2" in header or "禁止当作Q2" in header)
    assert "2024Q2" not in header


def test_financial_cutoff_header_none_latest_with_statement_kind():
    header = financial_cutoff_header(None, "2024-08-20", statement_kind="income")
    assert header == "【财务数据】在 2024-08-20 及之前无已公开报告期"
