"""Operational T+5 contract: catch root-only writes and collateral JSON changes."""
import copy
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from scripts import backfill_tplus5_shadow as cli

CAL = [date(2026, 8, 3) + timedelta(days=i) for i in range(12)
       if (date(2026, 8, 3) + timedelta(days=i)).weekday() < 5]


def unit(winner):
    return {
        "claims": [{"claim_id": "c", "claim": "evidence"}],
        "manager_verdict": {"winner": winner, "entry": "999"},
        "analysis_status": "VALID", "trade_action": "BUY",
        "decision_model_version": "decision_model.v1",
        "evidence_contract_version": "evidence_contract.v2",
        "price_basis_version": "price_basis.vendor_qfq",
        "price_ref_contract_version": "price_ref.v1",
        "shadow_credit_metrics": {"other": 1, "t_plus_5_direction_hit": None},
        "feature_flags": {"credit_weighting_enabled": False},
    }


def report():
    s, m = unit("bull"), unit("bear")
    return {"id": "one", "symbol": "600519.SH", "trade_date": "2026-08-03",
            "status": "completed", "result_data": {
                "opaque": "原文", "short_term": s, "medium_term": m,
                "horizons": {"short": copy.deepcopy(s), "medium": copy.deepcopy(m)}}}


def series():
    return {"source": "fixture.daily", "adjustment": "qfq",
            "fetched_on": "2026-08-15", "bars": {
                "2026-08-03": {"open": 10., "close": 10.},
                "2026-08-04": {"open": 10., "close": 10.},
                "2026-08-07": {"open": 10., "close": 10.},
                "2026-08-10": {"open": 11., "close": 11.},
                "2026-08-11": {"open": 11., "close": 11.}}}


def backfill(rep, data=None, as_of="2026-08-15"):
    return cli.backfill_report(rep, as_of=as_of, calendar=CAL,
                               fetch_series=lambda *args: series() if data is None else data)


def test_each_horizon_uses_own_winner_and_mirror_and_keeps_cohort():
    rep = report()
    updated, stats = backfill(rep)
    for h, expected in (("short", True), ("medium", False)):
        u = updated["result_data"][h + "_term"]
        assert u["shadow_credit_metrics"]["t_plus_5_direction_hit"] is expected
        assert u["t_plus_5_direction_hit"] is expected
        assert u["price_basis_version"] == "price_basis.vendor_qfq"
        assert u["manager_verdict"]["entry"] == "999"
        assert updated["result_data"]["horizons"][h] == u
        assert stats[h]["evaluated_count"] == 1
        assert u["t_plus_5_provenance"] == {
            "source": "fixture.daily", "adjustment": "qfq", "fetched_on": "2026-08-15",
            "entry_date": "2026-08-04", "exit_date": "2026-08-10",
            "entry_price": 10., "exit_price": 11.}
    assert cli.without_tplus5(rep["result_data"]) == cli.without_tplus5(updated["result_data"])


@pytest.mark.parametrize("missing", ["source", "adjustment", "fetched_on"])
def test_incomplete_provenance_never_reuses_old_price(missing):
    rep = report()
    rep["result_data"]["short_term"]["t_plus_5_price"] = 100.
    data = series(); del data[missing]
    updated, stats = backfill(rep, data)
    assert stats["short"]["data_missing_count"] == 1
    u = updated["result_data"]["short_term"]
    assert u["t_plus_5_direction_hit"] is None
    assert u["t_plus_5_evaluated"] is False
    assert u["t_plus_5_status"] == "data_missing"


def test_pending_does_not_fetch():
    def fail(*args):
        pytest.fail("pending unit fetched prices")
    updated, stats = cli.backfill_report(report(), as_of="2026-08-07", calendar=CAL, fetch_series=fail)
    assert stats["short"]["pending_due_count"] == 1
    assert updated["result_data"]["short_term"]["t_plus_5_direction_hit"] is None


def test_bilateral_hole_is_suspension_but_truncation_is_missing():
    data = series(); del data["bars"]["2026-08-10"]
    updated, stats = backfill(report(), data)
    assert stats["short"]["suspension_count"] == 1
    assert stats["short"]["due_count"] == 0
    data["bars"].pop("2026-08-11")
    updated, stats = backfill(report(), data)
    assert stats["short"]["data_missing_count"] == 1
    assert stats["short"]["due_count"] == 1


@pytest.mark.parametrize("winner,close,expected", [("bull", 11, True), ("bear", 9, True), ("tie", 10.2, True), ("tie", 11, False)])
def test_single_report_same_winner_definition(winner, close, expected):
    rep = report(); rep["result_data"] = unit(winner)
    data = series(); data["bars"]["2026-08-10"]["close"] = close
    updated, stats = backfill(rep, data)
    assert updated["result_data"]["t_plus_5_direction_hit"] is expected
    assert updated["result_data"]["manager_verdict"] == rep["result_data"]["manager_verdict"]


def make_db(tmp_path, rep=None):
    p = tmp_path / "copy.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE reports(id TEXT PRIMARY KEY,symbol TEXT,trade_date TEXT,status TEXT,result_data TEXT,updated_at TEXT)")
    if rep:
        # Deliberately noncanonical formatting: guard must preserve these bytes.
        text = json.dumps(rep["result_data"], ensure_ascii=False, indent=3)
        c.execute("INSERT INTO reports VALUES(?,?,?,?,?,?)", (rep["id"],rep["symbol"],rep["trade_date"],rep["status"],text,"old"))
    c.commit();c.close();return p


def test_empty_explicit_database_errors_and_never_migrates(tmp_path):
    p = make_db(tmp_path)
    schema = sqlite3.connect(p).execute("SELECT sql FROM sqlite_master").fetchall()
    with pytest.raises(RuntimeError, match="completed"):
        cli.load_raw_reports(db_path=str(p))
    assert sqlite3.connect(p).execute("SELECT sql FROM sqlite_master").fetchall() == schema


def test_byte_guard_rejects_non_measurement_changes_and_preserves_whitespace():
    before = '{ "opaque" : 1e+02, "short_term": {"shadow_credit_metrics": {"other": 2}} }'
    obj = json.loads(before)
    obj["short_term"]["shadow_credit_metrics"]["t_plus_5_direction_hit"] = False
    after = cli.patch_tplus5_json(before, obj)
    assert '"opaque" : 1e+02' in after
    assert cli.non_tplus5_bytes(before) == cli.non_tplus5_bytes(after)
    obj["opaque"] = 99
    with pytest.raises(ValueError, match="non.T\+5"):
        cli.patch_tplus5_json(before, obj)


def test_writer_only_changes_measurements_and_is_idempotent(tmp_path, monkeypatch):
    p = make_db(tmp_path, report()); log = tmp_path / "rollback.jsonl"
    monkeypatch.setattr(cli, "fetch_price_series", lambda *args: series())
    monkeypatch.setattr(cli, "load_calendar", lambda: CAL)
    monkeypatch.setattr(cli, "check_runtime_guard", lambda *args: {"allowed": True,"reason": "idle"})
    before = sqlite3.connect(p).execute("SELECT * FROM reports").fetchone()
    first = cli.run_backfill(db_path=str(p), as_of="2026-08-15", audit_log=str(log))
    after = sqlite3.connect(p).execute("SELECT * FROM reports").fetchone()
    assert before[:4] == after[:4] and before[-1] == after[-1] == "old"
    assert cli.non_tplus5_bytes(before[4]) == cli.non_tplus5_bytes(after[4])
    assert first["changed_rows"] == 1 and first["guard_mismatches"] == 0
    assert json.loads(log.read_text().splitlines()[-1])["report_id"] == "one"
    second = cli.run_backfill(db_path=str(p), as_of="2026-08-15", audit_log=str(log))
    assert second["changed_rows"] == 0


def test_busy_guard_prevents_all_writes_and_fetches(tmp_path, monkeypatch):
    p = make_db(tmp_path, report())
    monkeypatch.setattr(cli, "check_runtime_guard", lambda *args: {"allowed":False,"reason":"busy","running_count":1})
    before = p.read_bytes()
    result = cli.run_backfill(db_path=str(p), as_of="2026-08-15")
    assert result["skipped"] is True
    assert p.read_bytes() == before


def test_health_timeout_is_busy_not_dead(tmp_path, monkeypatch):
    import requests
    p = make_db(tmp_path, report())
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout()))
    guard = cli.check_runtime_guard(str(p), "http://127.0.0.1:8000")
    assert guard == {"allowed": False, "reason": "health_busy_timeout"}


def test_export_failure_means_zero_writes(tmp_path, monkeypatch):
    """DAV-1544: if the independent pre-export fails, no UPDATE may run."""
    p = make_db(tmp_path, report())
    monkeypatch.setattr(cli, "fetch_price_series", lambda *args: series())
    monkeypatch.setattr(cli, "load_calendar", lambda: CAL)
    monkeypatch.setattr(cli, "check_runtime_guard", lambda *a: {"allowed": True, "reason": "idle"})
    before_row = sqlite3.connect(p).execute("SELECT result_data FROM reports WHERE id='one'").fetchone()[0]
    before_bytes = p.read_bytes()

    def boom(*args, **kwargs):
        raise RuntimeError("simulated export failure")
    monkeypatch.setattr(cli, "export_pre_images", boom)

    with pytest.raises(RuntimeError, match="simulated export failure"):
        cli.run_backfill(db_path=str(p), as_of="2026-08-15",
                         pre_export_dir=str(tmp_path / "exports"))
    after_row = sqlite3.connect(p).execute("SELECT result_data FROM reports WHERE id='one'").fetchone()[0]
    assert after_row == before_row
    assert p.read_bytes() == before_bytes


def test_backfill_then_restore_is_byte_identical(tmp_path, monkeypatch):
    """DAV-1544: restore subcommand returns involved rows to pre-backfill bytes."""
    import hashlib
    p = make_db(tmp_path, report())
    monkeypatch.setattr(cli, "fetch_price_series", lambda *args: series())
    monkeypatch.setattr(cli, "load_calendar", lambda: CAL)
    monkeypatch.setattr(cli, "check_runtime_guard", lambda *a: {"allowed": True, "reason": "idle"})
    before_row = sqlite3.connect(p).execute(
        "SELECT result_data FROM reports WHERE id='one'").fetchone()[0]

    res = cli.run_backfill(db_path=str(p), as_of="2026-08-15",
                           pre_export_dir=str(tmp_path / "exports"))
    assert res["changed_rows"] == 1
    export = res["pre_export"]
    export_path = export["path"]
    assert Path(export_path).is_file()
    # sha256 recorded in the result matches the on-disk compressed file
    assert hashlib.sha256(Path(export_path).read_bytes()).hexdigest() == export["sha256"]

    backfilled_row = sqlite3.connect(p).execute(
        "SELECT result_data FROM reports WHERE id='one'").fetchone()[0]
    assert backfilled_row != before_row

    res2 = cli.restore_from_export(str(p), export_path, sha256=export["sha256"])
    assert res2["restored_rows"] == 1 and res2["mismatched"] == 0
    restored_row = sqlite3.connect(p).execute(
        "SELECT result_data FROM reports WHERE id='one'").fetchone()[0]
    assert restored_row == before_row

    # Idempotent: restoring again reports already_current, no rewrite.
    res3 = cli.restore_from_export(str(p), export_path)
    assert res3["already_current"] == 1 and res3["restored_rows"] == 0


def test_corrupt_export_file_refuses_restore(tmp_path, monkeypatch):
    """A tampered pre-export file (sha mismatch) must refuse to restore."""
    p = make_db(tmp_path, report())
    monkeypatch.setattr(cli, "check_runtime_guard", lambda *a: {"allowed": True, "reason": "idle"})
    bad = tmp_path / "bad.jsonl.zst"
    import zstandard
    bad.write_bytes(zstandard.ZstdCompressor().compress(b'{"kind":"row"}\n'))
    before = sqlite3.connect(p).execute("SELECT * FROM reports").fetchall()
    with pytest.raises(RuntimeError, match="sha256|trailer"):
        cli.restore_from_export(str(p), str(bad), sha256="0" * 64)
    after = sqlite3.connect(p).execute("SELECT * FROM reports").fetchall()
    assert after == before


def test_restore_subcommand_argparse_wiring(tmp_path, monkeypatch, capsys):
    """DAV-1544: `main()` must route the restore subcommand correctly."""
    p = make_db(tmp_path, report())
    monkeypatch.setattr(cli, "fetch_price_series", lambda *args: series())
    monkeypatch.setattr(cli, "load_calendar", lambda: CAL)
    monkeypatch.setattr(cli, "check_runtime_guard", lambda *a: {"allowed": True, "reason": "idle"})
    before_row = sqlite3.connect(p).execute(
        "SELECT result_data FROM reports WHERE id='one'").fetchone()[0]

    res = cli.run_backfill(db_path=str(p), as_of="2026-08-15",
                           pre_export_dir=str(tmp_path / "exports"))
    assert res["changed_rows"] == 1
    export_path = res["pre_export"]["path"]

    monkeypatch.setattr(sys, "argv", [
        "backfill_tplus5_shadow.py", "restore",
        "--db-path", str(p), "--export-file", export_path,
    ])
    capsys.readouterr()  # drop run_backfill output
    assert cli.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["restored_rows"] == 1
    restored_row = sqlite3.connect(p).execute(
        "SELECT result_data FROM reports WHERE id='one'").fetchone()[0]
    assert restored_row == before_row
