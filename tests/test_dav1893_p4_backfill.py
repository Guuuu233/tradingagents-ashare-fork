"""DAV-1893 (DAV-1885 P4 复审返修): backfill measurement write path must not
write the plaintext column under ``compressed_only``.

Reproduces 🔴-1: ``_rd_update`` used to default ``plaintext=True``, so the
``_write_row`` call site (which never passed the flag) always dual-wrote the
plaintext column — the P4 mode was bypassed for script writes. The fix
flips the default to ``plaintext=False`` and drops the redundant
``_row_plaintext_cleared`` row-content probe; mode is decided solely by
``report_storage_mode()``.

Must run in a fresh process — ``REPORT_STORAGE_MODE`` is a process-level
switch; invoke e.g.
``REPORT_STORAGE_MODE=compressed_only
  python -m pytest tests/test_dav1893_p4_backfill.py``.
"""

import json
import os
import sqlite3
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("REPORT_STORAGE_MODE", "plaintext") != "compressed_only",
    reason="requires REPORT_STORAGE_MODE=compressed_only",
)

from scripts import backfill_tplus5_shadow as cli  # noqa: E402
from tradingagents.storage import compressed_json as cj  # noqa: E402

CAL = [date(2026, 8, 3) + timedelta(days=i) for i in range(12)
       if (date(2026, 8, 3) + timedelta(days=i)).weekday() < 5]


def _unit():
    return {
        "claims": [{"claim_id": "c", "claim": "evidence"}],
        "manager_verdict": {"winner": "bull", "entry": "999"},
        "analysis_status": "VALID", "trade_action": "BUY",
        "decision_model_version": "decision_model.v1",
        "evidence_contract_version": "evidence_contract.v2",
        "price_basis_version": "price_basis.vendor_qfq",
        "price_ref_contract_version": "price_ref.v1",
        "shadow_credit_metrics": {"other": 1, "t_plus_5_direction_hit": None},
        "feature_flags": {"credit_weighting_enabled": False},
    }


def _report():
    s, m = _unit(), _unit()
    return {"id": "one", "symbol": "600519.SH", "trade_date": "2026-08-03",
            "status": "completed", "result_data": {
                "opaque": "原文", "short_term": s, "medium_term": m}}


def _series():
    return {"source": "fixture.daily", "adjustment": "qfq",
            "fetched_on": "2026-08-15", "bars": {
                "2026-08-03": {"open": 10., "close": 10.},
                "2026-08-04": {"open": 10., "close": 10.},
                "2026-08-07": {"open": 10., "close": 10.},
                "2026-08-10": {"open": 11., "close": 11.},
                "2026-08-11": {"open": 11., "close": 11.}}}


def _make_db(tmp_path, rep):
    """B-6b-migrated schema: plaintext + zst shadow columns."""
    p = tmp_path / "copy.db"
    c = sqlite3.connect(p)
    c.execute(
        "CREATE TABLE reports(id TEXT PRIMARY KEY, symbol TEXT,"
        " trade_date TEXT, status TEXT, result_data TEXT,"
        " result_data_zst BLOB, result_data_zst_len INTEGER,"
        " updated_at TEXT)")
    raw = json.dumps(rep["result_data"], ensure_ascii=False)
    zst = cj.compress_json_bytes(raw.encode("utf-8"))
    c.execute(
        "INSERT INTO reports VALUES(?,?,?,?,?,?,?,?)",
        (rep["id"], rep["symbol"], rep["trade_date"], rep["status"],
         raw, zst, len(raw.encode("utf-8")), "old"))
    c.commit()
    c.close()
    return p


def test_compressed_only_default_write_clears_plaintext(tmp_path, monkeypatch):
    """🔴-1: _write_row → _rd_update default call must NULL the plaintext
    column under compressed_only (previously it always dual-wrote)."""
    rep = _report()
    p = _make_db(tmp_path, rep)
    monkeypatch.setenv("TUSHARE_TOKEN", "test_stub")
    monkeypatch.setattr(cli, "fetch_price_series", lambda *args: _series())
    monkeypatch.setattr(cli, "load_calendar", lambda: CAL)
    monkeypatch.setattr(cli, "check_runtime_guard",
                        lambda *a: {"allowed": True, "reason": "idle"})

    res = cli.run_backfill(db_path=str(p), as_of="2026-08-15",
                           audit_log=str(tmp_path / "audit.jsonl"),
                           pre_export_dir=str(tmp_path / "exports"))
    assert res["changed_rows"] == 1

    conn = sqlite3.connect(p)
    plain, zst, zlen = conn.execute(
        "SELECT result_data, result_data_zst, result_data_zst_len"
        " FROM reports WHERE id='one'").fetchone()
    conn.close()

    # P4: measurement write leaves plaintext NULL; zst is authoritative.
    assert plain is None
    stored = json.loads(cj.decode_frame_bytes(bytes(zst)).decode("utf-8"))
    assert stored["short_term"]["t_plus_5_direction_hit"] is True
    assert stored["opaque"] == "原文"
    assert zlen == len(cj.decode_frame_bytes(bytes(zst)))


def test_compressed_only_restore_refills_plaintext(tmp_path, monkeypatch):
    """restore path passes plaintext=True explicitly — still dual-writes
    under compressed_only (P4→P3 rollback must refill cleared rows)."""
    rep = _report()
    p = _make_db(tmp_path, rep)
    monkeypatch.setenv("TUSHARE_TOKEN", "test_stub")
    monkeypatch.setattr(cli, "fetch_price_series", lambda *args: _series())
    monkeypatch.setattr(cli, "load_calendar", lambda: CAL)
    monkeypatch.setattr(cli, "check_runtime_guard",
                        lambda *a: {"allowed": True, "reason": "idle"})
    before_plain = sqlite3.connect(p).execute(
        "SELECT result_data FROM reports WHERE id='one'").fetchone()[0]

    res = cli.run_backfill(db_path=str(p), as_of="2026-08-15",
                           pre_export_dir=str(tmp_path / "exports"))
    assert res["changed_rows"] == 1
    assert sqlite3.connect(p).execute(
        "SELECT result_data FROM reports WHERE id='one'").fetchone()[0] is None

    res2 = cli.restore_from_export(str(p), res["pre_export"]["path"],
                                   sha256=res["pre_export"]["sha256"])
    assert res2["restored_rows"] == 1

    conn = sqlite3.connect(p)
    plain, zst = conn.execute(
        "SELECT result_data, result_data_zst FROM reports WHERE id='one'"
    ).fetchone()
    conn.close()
    # plaintext refilled byte-identically with the pre-export image.
    assert plain == before_plain
    assert cj.decode_frame_bytes(bytes(zst)).decode("utf-8") == plain


def test_compressed_only_rd_update_explicit_plaintext(tmp_path):
    """Unit-level: _rd_update signature default is plaintext=False."""
    rep = _report()
    p = _make_db(tmp_path, rep)
    conn = sqlite3.connect(p)
    try:
        new_text = json.dumps({**rep["result_data"], "k": 2})
        # Default call — the _write_row call site form.
        cli._rd_update(conn, new_text, "one")
        conn.commit()
        plain, zst = conn.execute(
            "SELECT result_data, result_data_zst FROM reports WHERE id='one'"
        ).fetchone()
        assert plain is None
        assert cj.decode_frame_bytes(bytes(zst)).decode("utf-8") == new_text
        # Explicit restore-form call still writes plaintext.
        cli._rd_update(conn, rep["result_data"] and
                       json.dumps(rep["result_data"]), "one", plaintext=True)
        conn.commit()
        plain = conn.execute(
            "SELECT result_data FROM reports WHERE id='one'").fetchone()[0]
        assert plain == json.dumps(rep["result_data"])
    finally:
        conn.close()
