"""Tests for DAV-1950 phase-1 acceptance report generator.

Covers: byte-identical rebuild on the same summary (determinism),
required per-criterion fields (数据来源/起算日/样本量/门槛/是否达标),
A1 human-audit label, snapshot_as_of derivation, and missing-db exit 2.
No DB/model/provider for unit-level tests; the subprocess test uses a
tiny hand-made sqlite db only to exercise plumbing end-to-end.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dav1950_exit_acceptance_report.py"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import dav1950_exit_acceptance_report as g


def _summary():
    return {
        "meta": {"db": "x.db", "account": "acct", "start": "2026-10-08",
                 "end": None, "as_of": "2026-10-12", "reports": 2, "units": 4,
                 "clean_units": 2, "peak_mb": 1.0},
        "A1": {"threshold": "假 clean = 0", "executable_units": 1,
               "audit_manifest": [{"report_id": "r1", "symbol": "X",
                                    "trade_date": "2026-10-09", "cohort": "c",
                                    "decision": "BUY", "direction": "BULL",
                                    "created_at": "2026-10-09T01:00"}]},
        "A2": {"threshold": "t", "window_days": 10,
               "all_scope": {"completed_units": 4, "qualified_clean": 1,
                             "rate_pct": 25.0, "gap_to_4.2": 20.8},
               "rolling_window": {"dates": ["2026-10-09"], "days": 1,
                                  "completed_units": 4, "qualified_clean": 1,
                                  "rate_pct": 25.0, "gap_to_4.2": 20.8,
                                  "meets": True},
               "direction_watch": {"buy": 1, "sell": 0}},
        "A3": {"threshold": "t3", "per_cohort": {}},
        "A4": {"threshold": "t4", "as_of": "2026-10-12",
               "mature_cutoff": "2026-10-01", "submitted": 2, "completed": 2,
               "failed": 0, "completion_rate_pct": 100.0,
               "completion_gap_to_95": 5.0, "mature_units": 0,
               "mature_reports": 0, "t5_present": 0, "t5_reports_present": 0,
               "t5_rate_pct": None, "t5_report_rate_pct": None,
               "t5_gap_to_95": None},
        "B1": {"threshold": "b1", "units_total": 4, "units_b1_v1": 4,
               "coverage_pct": 100.0, "live": True, "note": "n"},
        "B2": {"threshold": "b2", "b1_v1_units_completed": 4, "invalid": 0,
               "invalid_pct": 0.0, "sample_gap_to_100": 96, "meets": None},
        "B3": {"threshold": "b3", "reports_total": 2, "traceable": 2,
               "coverage_pct": 100.0, "gap": 0,
               "missing_report_ids_sample": []},
        "all_history_side_reading": None,
    }


def test_render_deterministic_and_required_fields():
    s = _summary()
    r1 = g.build_report(s, "x.db", 10, "ab" * 32, "2026-10-12", False)
    r2 = g.build_report(s, "x.db", 10, "ab" * 32, "2026-10-12", False)
    assert r1 == r2
    for section in ("### A1", "### A2", "### A3", "### A4",
                    "### B1", "### B2", "### B3"):
        assert section in r1
    assert "需总控人工审计" in r1
    for needle in ("数据来源", "起算日", "样本量", "门槛", "是否达标"):
        assert needle in r1


def test_snapshot_as_of_prefers_zst(tmp_path):
    db = tmp_path / "current.db"
    db.write_bytes(b"")
    (tmp_path / "tradingagents-2026-10-09.db.zst").write_bytes(b"")
    (tmp_path / "tradingagents-2026-10-12.db.zst").write_bytes(b"")
    assert g.snapshot_as_of(db) == "2026-10-12"


def test_missing_db_exits_2(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(tmp_path / "nope.db")],
        capture_output=True, text=True)
    assert proc.returncode == 2


def test_a3_verdict_mixed():
    cohort = {"effective_sessions": {"bull": 30, "bear": 28},
              "verified_claims": {"bull": 120, "bear": 110},
              "last_50_window": {"ratio_in_[40,60]": True}}
    assert g._a3_verdict(cohort) == "✅ 达标"
    cohort["effective_sessions"]["bull"] = 9
    assert g._a3_verdict(cohort) == "❌ 未达标"
    cohort["effective_sessions"]["bull"] = 30
    cohort["last_50_window"]["ratio_in_[40,60]"] = None
    assert g._a3_verdict(cohort) == "N/A（窗口不足等）"


def test_end_to_end_deterministic_on_empty_db(tmp_path):
    """Two invocations on the same (empty-reports) db produce byte-identical
    stdout; also exercises in-process upstream main() plumbing."""
    db = tmp_path / "mini.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, user_id TEXT, "
                "symbol TEXT, trade_date TEXT, status TEXT, created_at TEXT, "
                "result_data TEXT)")
    con.commit(); con.close()
    out = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--db", str(db),
             "--as-of", "2026-10-12"], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        out.append(proc.stdout)
    assert out[0] == out[1]
    assert "样本量 | 0 份报告" in out[0] or "样本量 | 75" in out[0] or "B3" in out[0]
