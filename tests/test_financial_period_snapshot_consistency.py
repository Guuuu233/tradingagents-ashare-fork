"""DAV-1108 stage B: cross-statement report-period consistency tests.

Covers:
- latest_public_period spanning all statements in the effective-announce map
- stale_period_cutoff_note emitted when a table's latest period trails the
  newest disclosed period (中芯 688981 case: income H1 vs balance Q1)
- compliance layer period_snapshot_mismatch violation when parsed statements
  disagree on the newest visible period
"""

import pytest

from tradingagents.dataflows.financial_announce import (
    latest_public_period,
    resolve_effective_announce_date,
    stale_period_cutoff_note,
)
from tradingagents.agents.utils.financial_period_compliance import (
    KIND_PERIOD_SNAPSHOT_MISMATCH,
    check_financial_period_compliance,
)


def _eff(period: str, ann: str):
    return resolve_effective_announce_date(period, [ann])


def _table(rows: list[dict]) -> str:
    """Build a markdown financial table from row dicts."""
    cols = ["报告日"] + [c for c in rows[0] if c != "报告日"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


# ── latest_public_period / stale note ────────────────────────────────────────


class TestLatestPublicPeriod:
    def test_spans_all_statements(self):
        eff_map = {
            "20260331": _eff("20260331", "20260428"),
            "20260630": _eff("20260630", "20260813"),
        }
        assert latest_public_period(eff_map, "2026-08-20") == "20260630"

    def test_respects_cutoff(self):
        eff_map = {
            "20260331": _eff("20260331", "20260428"),
            "20260630": _eff("20260630", "20260831"),
        }
        assert latest_public_period(eff_map, "2026-07-01") == "20260331"

    def test_empty_map(self):
        assert latest_public_period({}, "2026-08-20") is None


class TestStalePeriodCutoffNote:
    def test_stale_table_emits_note(self):
        # 中芯案: balance sheet latest=Q1 while H1 already public elsewhere
        latest = _eff("20260331", "20260428")
        note = stale_period_cutoff_note(latest, "20260630", "2026-08-20")
        assert "期间提示" in note
        assert "H1" in note or "0630" in note
        assert "数据缺失" in note
        assert "禁止隐式回退" in note

    def test_aligned_table_no_note(self):
        latest = _eff("20260630", "20260813")
        assert stale_period_cutoff_note(latest, "20260630", "2026-08-20") == ""

    def test_no_global_period_no_note(self):
        latest = _eff("20260331", "20260428")
        assert stale_period_cutoff_note(latest, None, "2026-08-20") == ""


# ── Compliance-layer cross-statement consistency ─────────────────────────────


class TestPeriodSnapshotMismatch:
    def test_stale_balance_sheet_flagged(self):
        # 中芯实锤场景: income H1 已公开, balance 停 Q1
        income_md = _table([
            {"报告日": "20260630", "营业收入": "100000000", "净利润": "20000000"},
            {"报告日": "20260331", "营业收入": "50000000", "净利润": "9000000"},
        ])
        balance_md = _table([
            {"报告日": "20260331", "存货": "27142000000", "资产总计": "200000000000"},
        ])
        res = check_financial_period_compliance(
            "报告引用财务数据。",
            {"income_statement": income_md, "balance_sheet": balance_md},
        )
        mismatches = [
            v for v in res["violations"]
            if v["kind"] == KIND_PERIOD_SNAPSHOT_MISMATCH
        ]
        assert mismatches, f"expected period_snapshot_mismatch, got {res}"
        assert all(v["statement"] == "balance_sheet" for v in mismatches)

    def test_aligned_periods_clean(self):
        income_md = _table([
            {"报告日": "20260630", "营业收入": "100000000"},
            {"报告日": "20260331", "营业收入": "50000000"},
        ])
        balance_md = _table([
            {"报告日": "20260630", "存货": "10000000"},
            {"报告日": "20260331", "存货": "9000000"},
        ])
        res = check_financial_period_compliance(
            "报告引用财务数据。",
            {"income_statement": income_md, "balance_sheet": balance_md},
        )
        assert not [
            v for v in res["violations"]
            if v["kind"] == KIND_PERIOD_SNAPSHOT_MISMATCH
        ]

    def test_single_statement_no_mismatch(self):
        income_md = _table([
            {"报告日": "20260331", "营业收入": "50000000"},
        ])
        res = check_financial_period_compliance(
            "报告引用财务数据。", {"income_statement": income_md}
        )
        assert not [
            v for v in res["violations"]
            if v["kind"] == KIND_PERIOD_SNAPSHOT_MISMATCH
        ]
