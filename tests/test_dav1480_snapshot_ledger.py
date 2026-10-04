"""DAV-1480: unit tests for scripts/phase2/daily_snapshot_ledger.py.

Offline only — no DB, no akshare. Records are built from synthetic sqlite
rows + injected trade calendars.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "phase2" / "daily_snapshot_ledger.py"

spec = importlib.util.spec_from_file_location("daily_snapshot_ledger", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["daily_snapshot_ledger"] = mod
spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# helpers


def _cal(start="2026-07-01", days=400) -> list[date]:
    """Simple Mon-Fri calendar good enough for unit tests."""
    out = []
    d = date.fromisoformat(start)
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return out


class Row(dict):
    pass


def _row(**kw):
    base = dict(
        id="r1", user_id="u1", symbol="600519.SH", industry="白酒",
        trade_date="2026-07-28", status="completed", analysis_status="VALID",
        decision="BUY", direction="看多", probability=60,
        trade_action="BUY", risk_status="APPROVED",
        final_trade_decision="ok",
        created_at="2026-07-28 15:43:57.730577",
        updated_at="2026-07-28 15:43:57.730577",
    )
    base.update(kw)
    return Row(base)


def _rd(**kw):
    base = {
        "input_snapshot": {"algo": "sha256", "hash": "abc",
                           "keys": ["stock_data", "news"]},
        "market_data_context": {
            "short": {"source_provenance": {
                "stock_data": {"requested_as_of": "2026-07-28",
                               "actual_as_of": "2026-07-28",
                               "provenance_status": "verified"},
                "news": {"requested_as_of": "2026-07-28",
                         "actual_as_of": "2026-07-27",
                         "provenance_status": "verified"},
            }},
        },
        "horizons": {"short": {"direction": "BULL", "probability": 60},
                     "medium": {"direction": "BEAR"}},
        "run_identity": {"commit_sha": "x" * 40},
        "served_models": {"by_role": {}},
    }
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


SEALED = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)   # before T+1 open


# ---------------------------------------------------------------------------
# timing_class


def test_f0_when_sealed_before_entry_open():
    rec = mod.build_record(_row(), _rd(), SEALED, "2026-07-28", _cal())
    assert rec["timing_class"] == "F0"
    assert rec["timing_evidence"]["entry_trading_day"] == "2026-07-29"


def test_f1_when_sealed_after_entry_before_outcome():
    sealed = datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)
    rec = mod.build_record(_row(), _rd(), sealed, "2026-08-05", _cal())
    assert rec["timing_class"] == "F1"


def test_h_when_sealed_at_or_after_outcome_close():
    # T+40 close is far in the future; use a short calendar + seal far ahead.
    sealed = datetime(2027, 6, 1, 8, 0, tzinfo=timezone.utc)
    rec = mod.build_record(_row(), _rd(), sealed, "2027-06-01", _cal())
    assert rec["timing_class"] == "H"


def test_f0_when_calendar_does_not_reach_entry():
    short_cal = _cal("2026-07-01", 20)  # ends 2026-07-28
    rec = mod.build_record(_row(), _rd(), SEALED, "2026-07-28", short_cal)
    assert rec["timing_class"] == "F0"


def test_seal_basis_prefers_later_created_at():
    # created_at AFTER snapshot time => seal_basis = created_at.
    row = _row(created_at="2026-08-10 10:00:00")
    rec = mod.build_record(row, _rd(), SEALED, "2026-07-28", _cal())
    assert rec["seal_basis_at"].startswith("2026-08-10")
    assert rec["timing_class"] == "F1"


# ---------------------------------------------------------------------------
# input_pit_status


def test_pit_verified_when_all_sources_verified():
    rec = mod.build_record(_row(), _rd(), SEALED, "2026-07-28", _cal())
    assert rec["input_pit_status"] == "VERIFIED"
    assert rec["input_pit_evidence"]["failed_sources"] == []


def test_pit_failed_on_future_actual_as_of():
    rd = json.loads(_rd())
    sp = rd["market_data_context"]["short"]["source_provenance"]
    sp["news"]["actual_as_of"] = "2026-07-29"   # after requested 07-28
    rec = mod.build_record(_row(), json.dumps(rd), SEALED, "2026-07-28", _cal())
    assert rec["input_pit_status"] == "FAILED"


def test_pit_failed_on_future_provenance_flag():
    rd = json.loads(_rd())
    sp = rd["market_data_context"]["short"]["source_provenance"]
    sp["news"]["provenance_status"] = "future"
    rec = mod.build_record(_row(), json.dumps(rd), SEALED, "2026-07-28", _cal())
    assert rec["input_pit_status"] == "FAILED"


def test_pit_unverified_when_timestamp_missing():
    rd = json.loads(_rd())
    sp = rd["market_data_context"]["short"]["source_provenance"]
    sp["news"] = {"requested_as_of": "2026-07-28", "actual_as_of": None,
                  "status": "available", "provenance_status": "unverified"}
    rec = mod.build_record(_row(), json.dumps(rd), SEALED, "2026-07-28", _cal())
    assert rec["input_pit_status"] == "UNVERIFIED"


def test_pit_unverified_when_provenance_absent_for_expected_source():
    rd = json.loads(_rd())
    rd["input_snapshot"]["keys"] = ["stock_data", "news", "margin_trading"]
    rec = mod.build_record(_row(), json.dumps(rd), SEALED, "2026-07-28", _cal())
    assert rec["input_pit_status"] == "UNVERIFIED"
    assert any(u["source"] == "margin_trading"
               for u in rec["input_pit_evidence"]["unverified_sources"])


def test_pit_exempts_structural_no_timestamp_sources():
    rd = json.loads(_rd())
    rd["input_snapshot"]["keys"] = ["stock_data", "news", "vpa_context",
                                    "event_coverage", "cninfo_announcements"]
    rec = mod.build_record(_row(), json.dumps(rd), SEALED, "2026-07-28", _cal())
    assert rec["input_pit_status"] == "VERIFIED"
    assert set(rec["input_pit_evidence"]["exempt_sources"]) == {
        "vpa_context", "event_coverage", "cninfo_announcements"}


def test_pit_unverified_when_mdc_missing():
    rec = mod.build_record(_row(), json.dumps({"horizons": {}}),
                           SEALED, "2026-07-28", _cal())
    assert rec["input_pit_status"] == "UNVERIFIED"


def test_pit_unverified_when_result_data_is_json_null():
    rec = mod.build_record(_row(), "null", SEALED, "2026-07-28", _cal())
    assert rec["input_pit_status"] == "UNVERIFIED"


def test_horizon_fields_fall_back_to_top_level_for_old_rows():
    rd = json.dumps({"direction": "看空", "decision": "SELL",
                     "risk_status": "APPROVED"}, ensure_ascii=False)
    rec = mod.build_record(_row(), rd, SEALED, "2026-07-28", _cal())
    assert rec["horizons"]["short"]["direction"] == "看空"
    assert rec["horizons"]["short"]["decision"] == "SELL"


# ---------------------------------------------------------------------------
# record hash + chain verify


def _ledger(tmp_path, records):
    ledger = tmp_path / "forward_ledger.jsonl"
    prev = "GENESIS"
    with open(ledger, "w", encoding="utf-8") as fh:
        for rec in records:
            chain = hashlib.sha256(
                (prev + rec["record_sha256"]).encode()).hexdigest()
            fh.write(json.dumps({"record": rec, "prev_hash": prev,
                                 "chain_hash": chain}, ensure_ascii=False) + "\n")
            prev = chain
    return ledger


def test_verify_ok(tmp_path):
    r1 = mod.build_record(_row(id="a"), _rd(), SEALED, "d", _cal())
    r2 = mod.build_record(_row(id="b"), _rd(), SEALED, "d", _cal())
    _ledger(tmp_path, [r1, r2])
    ns = type("NS", (), {"ledger_dir": str(tmp_path)})
    assert mod.cmd_verify(ns) == 0


def test_verify_detects_tampered_record(tmp_path):
    r1 = mod.build_record(_row(id="a"), _rd(), SEALED, "d", _cal())
    r2 = mod.build_record(_row(id="b"), _rd(), SEALED, "d", _cal())
    ledger = _ledger(tmp_path, [r1, r2])
    # flip a field inside record 1 without re-hashing
    lines = ledger.read_text("utf-8").splitlines()
    obj = json.loads(lines[0])
    obj["record"]["direction_top"] = "篡改"
    lines[0] = json.dumps(obj, ensure_ascii=False)
    ledger.write_text("\n".join(lines) + "\n", "utf-8")
    ns = type("NS", (), {"ledger_dir": str(tmp_path)})
    assert mod.cmd_verify(ns) == 1


def test_verify_detects_chain_break(tmp_path):
    r1 = mod.build_record(_row(id="a"), _rd(), SEALED, "d", _cal())
    r2 = mod.build_record(_row(id="b"), _rd(), SEALED, "d", _cal())
    ledger = _ledger(tmp_path, [r1, r2])
    lines = ledger.read_text("utf-8").splitlines()
    obj = json.loads(lines[1])
    obj["prev_hash"] = "deadbeef"
    lines[1] = json.dumps(obj, ensure_ascii=False)
    ledger.write_text("\n".join(lines) + "\n", "utf-8")
    ns = type("NS", (), {"ledger_dir": str(tmp_path)})
    assert mod.cmd_verify(ns) == 1


# ---------------------------------------------------------------------------
# baseline reconcile


def test_baseline_match_and_mismatch(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE reports (id TEXT, result_data TEXT)")
    good = json.dumps({"a": 1})
    bad = json.dumps({"b": 2})
    con.execute("INSERT INTO reports VALUES ('ok', ?)", (good,))
    con.execute("INSERT INTO reports VALUES ('changed', ?)", (bad,))
    con.commit(); con.close()

    snap = tmp_path / "snap.jsonl"
    snap.write_text(
        json.dumps({"id": "ok",
                    "result_data_sha256": hashlib.sha256(good.encode()).hexdigest()}) + "\n" +
        json.dumps({"id": "changed",
                    "result_data_sha256": hashlib.sha256(b'{"b": 9}').hexdigest()}) + "\n" +
        json.dumps({"id": "gone", "result_data_sha256": "x"}) + "\n",
        "utf-8")
    ns = type("NS", (), {"snapshot_jsonl": str(snap), "db": str(db),
                         "ledger_dir": None})
    assert mod.cmd_baseline(ns) == 1  # 1 mismatch + 1 absent


def test_baseline_all_match(tmp_path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE reports (id TEXT, result_data TEXT)")
    good = json.dumps({"a": 1})
    con.execute("INSERT INTO reports VALUES ('ok', ?)", (good,))
    con.commit(); con.close()
    snap = tmp_path / "snap.jsonl"
    snap.write_text(json.dumps(
        {"id": "ok",
         "result_data_sha256": hashlib.sha256(good.encode()).hexdigest()}) + "\n",
        "utf-8")
    ns = type("NS", (), {"snapshot_jsonl": str(snap), "db": str(db),
                         "ledger_dir": None})
    assert mod.cmd_baseline(ns) == 0
