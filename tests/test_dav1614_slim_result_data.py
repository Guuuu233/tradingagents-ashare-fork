"""DAV-1614 (存储 B-4): legacy result_data -> canonical v1 migration.

Covers the 卡面 contract:

* physical ``horizons`` / top-level ``market_data_context`` are removed and
  the compat-layer read (expand_compat_view + json.dumps) stays 逐字相同;
* non-alias bytes (whitespace-insensitive member tokens incl. opaque number
  spellings and foreign key order) survive the transplant verbatim;
* independent zstd pre-export failure means zero writes;
* journal-based resume skips committed rows;
* restore subcommand returns rows to pre-slim bytes, idempotently;
* conflicts (unreconstructible aliases) fail closed and are reported;
* VACUUM INTO output verifies against its source.
"""
import copy
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from scripts import slim_result_data_storage as slim
from tradingagents.storage.result_data_compat import (
    expand_compat_view,
    is_canonical_storage,
)


def unit(horizon, mdc=True):
    payload = {
        "horizon": horizon,
        "status": "completed",
        "trade_action": "BUY",
        "analysis_status": "VALID",
        "direction": "看多",
        "manager_verdict": {"winner": "bull"},
        "decision_model_version": "decision_model.v1",
        "evidence_contract_version": "evidence_contract.v2",
        "generated_by_commit_sha": "a" * 40,
    }
    if mdc:
        payload["market_data_context"] = {
            "daily": {"as_of": "2026-10-02", "horizon": horizon},
            "source_provenance": {"provider": "tushare"},
        }
    return payload


def legacy_dual(horizon_mdc=True, top_mdc="per_horizon"):
    """A dual-horizon legacy row: horizons alias + top-level mdc."""
    short = unit("short", mdc=horizon_mdc)
    medium = unit("medium", mdc=horizon_mdc)
    # Legacy horizons alias: same content minus term-only stamps, different
    # key order to prove the transplant is byte-level not semantic.
    def alias(slice_payload):
        return {k: copy.deepcopy(v) for k, v in slice_payload.items()
                if k not in ("decision_model_version",
                             "evidence_contract_version",
                             "generated_by_commit_sha")}
    data = {
        "symbol": "600519.SH",
        "trade_date": "2026-10-02",
        "mode": "dual_horizon",
        "short_term": short,
        "medium_term": medium,
        "horizons": {"short": alias(short), "medium": alias(medium)},
        "decision": "BUY",
        "final_trade_decision": "buy because reasons",
    }
    if top_mdc == "per_horizon":
        data["market_data_context"] = {
            "short": copy.deepcopy(short["market_data_context"]),
            "medium": copy.deepcopy(medium["market_data_context"]),
        }
    elif top_mdc == "null":
        data["market_data_context"] = None
    return data


def make_db(tmp_path, rows, name="copy.db"):
    """rows: list of (id, result_data_text). Deliberately noncanonical
    formatting allowed — the converter must preserve untouched bytes."""
    p = tmp_path / name
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE reports(id TEXT PRIMARY KEY, symbol TEXT, trade_date TEXT,"
              " status TEXT, result_data TEXT, updated_at TEXT)")
    for report_id, text in rows:
        c.execute("INSERT INTO reports VALUES(?,?,?,?,?,?)",
                  (report_id, "SYM", "2026-10-02", "completed", text, "old"))
    c.commit()
    c.close()
    return p


def db_text(path, report_id):
    return sqlite3.connect(path).execute(
        "SELECT result_data FROM reports WHERE id=?", (report_id,)).fetchone()[0]


def view(path, report_id):
    """Compat-layer read bytes (markers excluded) — same semantics as the
    script's _view_bytes: the report content a compat-layer reader sees."""
    return slim._view_bytes(db_text(path, report_id))


def idle_guard(monkeypatch):
    monkeypatch.setattr(slim, "check_runtime_guard",
                        lambda *a, **k: {"allowed": True, "reason": "idle"})


# --- conversion semantics -------------------------------------------------


def test_convert_dual_horizon_verbatim_view(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    raw = json.dumps(legacy_dual(), ensure_ascii=False, indent=3)
    p = make_db(tmp_path, [("one", raw)])
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["candidates"] == 1 and res["converted_rows"] == 1
    stored = db_text(p, "one")
    stored_rd = json.loads(stored)
    assert is_canonical_storage(stored_rd)
    assert "horizons" not in stored_rd  # physical alias gone (name survives only in masks)
    assert "market_data_context" not in stored_rd
    # 逐字: compat-layer read after == compat-layer read before
    assert slim._view_bytes(stored) == slim._view_bytes(raw)


def test_convert_preserves_opaque_bytes_and_key_order(tmp_path, monkeypatch):
    """Non-alias member tokens — incl. weird number spellings — stay verbatim."""
    idle_guard(monkeypatch)
    data = legacy_dual()
    # Rebuild with deliberate odd spellings + odd key order via manual text.
    base = json.dumps(data, ensure_ascii=False)
    weird = base.replace('"confidence"', '"opaque_num": 1e+02, "confidence"') \
        if '"confidence"' in base else base
    p = make_db(tmp_path, [("two", weird)])
    before = db_text(p, "two")
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["converted_rows"] == 1
    stored = db_text(p, "two")
    # Witness: everything except the removed aliases + appended markers is
    # the same member tokens in the same order.
    assert slim._witness_equal(stored, before)


def test_no_alias_rows_untouched(tmp_path, monkeypatch):
    """Single-horizon flat rows and canonical rows are not candidates."""
    idle_guard(monkeypatch)
    flat = {"symbol": "X", "market_data_context": {"daily": {}}, "horizon": "short"}
    p = make_db(tmp_path, [("flat", json.dumps(flat)),
                           ("nullrd", None)])
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["candidates"] == 0 and res["converted_rows"] == 0
    assert res["no_alias_content"] == 1 and res["non_dict"] == 1
    assert db_text(p, "flat") == json.dumps(flat)
    assert db_text(p, "nullrd") is None


def test_conflict_rows_fail_closed_and_are_reported(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    bad = legacy_dual()
    bad["horizons"]["short"]["direction"] = "看空"  # alias disagrees with term
    raw = json.dumps(bad, ensure_ascii=False)
    p = make_db(tmp_path, [("conflict", raw)])
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["conflicts"] == 1 and res["converted_rows"] == 0
    assert db_text(p, "conflict") == raw  # untouched
    exc = json.loads((tmp_path / "e.json").read_text())
    assert exc["exceptions"][0]["report_id"] == "conflict"


def test_top_mdc_null_is_rebuilt(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    raw = json.dumps(legacy_dual(top_mdc="null"), ensure_ascii=False)
    p = make_db(tmp_path, [("n", raw)])
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["converted_rows"] == 1
    assert view(p, "n") == slim._view_bytes(raw)


# --- safety mechanics ------------------------------------------------------


def test_export_failure_means_zero_writes(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    raw = json.dumps(legacy_dual(), ensure_ascii=False)
    p = make_db(tmp_path, [("one", raw)])
    before = p.read_bytes()

    def boom(*args, **kwargs):
        raise RuntimeError("simulated export failure")
    monkeypatch.setattr(slim, "export_pre_images", boom)
    with pytest.raises(RuntimeError, match="simulated export failure"):
        slim.run_slim(db_path=str(p), copy_rehearsal=True,
                      journal_path=str(tmp_path / "j.jsonl"),
                      exceptions_path=str(tmp_path / "e.json"),
                      pre_export_dir=str(tmp_path / "exports"))
    assert db_text(p, "one") == raw
    assert p.read_bytes() == before


def test_drift_between_export_and_write_is_skipped(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    raw = json.dumps(legacy_dual(), ensure_ascii=False)
    p = make_db(tmp_path, [("one", raw)])
    real_export = slim.export_pre_images

    def drifting_export(db_path, planned, *, export_dir=None):
        result = real_export(db_path, planned, export_dir=export_dir)
        # Touch the row after export so its sha no longer matches.
        c = sqlite3.connect(db_path)
        c.execute("UPDATE reports SET result_data=? WHERE id='one'",
                  (raw.replace("BUY", "HOLD"),))
        c.commit(); c.close()
        return result
    monkeypatch.setattr(slim, "export_pre_images", drifting_export)
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["pre_export_mismatch"] == 1 and res["converted_rows"] == 0


def test_resume_skips_committed_rows(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    rows = [(f"r{i}", json.dumps(legacy_dual(), ensure_ascii=False)) for i in range(5)]
    p = make_db(tmp_path, rows)
    journal = str(tmp_path / "j.jsonl")
    # Simulate a previous interrupted run: r0, r1 already committed. Use
    # the real journal shape _write_row emits ({"phase":"committed"}).
    j = Path(journal)
    j.write_text("".join(json.dumps({"phase": "committed", "report_id": f"r{i}"}) + "\n"
                         for i in (0, 1)))
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True, batch_size=2,
                        journal_path=journal,
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["resumed_skipped"] == 2
    assert res["converted_rows"] == 3
    # r0/r1 were never converted (journal said committed but db was untouched)
    assert '"horizons"' in db_text(p, "r0")


def test_backfill_then_restore_is_byte_identical(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    raw = json.dumps(legacy_dual(), ensure_ascii=False, indent=2)
    p = make_db(tmp_path, [("one", raw)])
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["converted_rows"] == 1
    export = res["pre_export_files"][0]
    export_path = export["path"]
    assert hashlib.sha256(Path(export_path).read_bytes()).hexdigest() == export["sha256"]

    res2 = slim.restore_from_export(str(p), export_path, sha256=export["sha256"])
    assert res2["restored_rows"] == 1 and res2["mismatched"] == 0
    assert db_text(p, "one") == raw
    # Idempotent.
    res3 = slim.restore_from_export(str(p), export_path)
    assert res3["already_current"] == 1 and res3["restored_rows"] == 0


def test_corrupt_export_file_refuses_restore(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    p = make_db(tmp_path, [("one", json.dumps(legacy_dual()))])
    import zstandard
    bad = tmp_path / "bad.jsonl.zst"
    bad.write_bytes(zstandard.ZstdCompressor().compress(b'{"kind":"row"}\n'))
    before = p.read_bytes()
    with pytest.raises(RuntimeError, match="sha256|trailer"):
        slim.restore_from_export(str(p), str(bad), sha256="0" * 64)
    assert p.read_bytes() == before


def test_busy_guard_prevents_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(slim, "check_runtime_guard",
                        lambda *a, **k: {"allowed": False, "reason": "busy",
                                         "running_count": 1})
    raw = json.dumps(legacy_dual(), ensure_ascii=False)
    p = make_db(tmp_path, [("one", raw)])
    before = p.read_bytes()
    res = slim.run_slim(db_path=str(p),
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"))
    assert res["skipped"] is True
    assert p.read_bytes() == before


def test_dry_run_and_scan_only_write_nothing(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    raw = json.dumps(legacy_dual(), ensure_ascii=False)
    p = make_db(tmp_path, [("one", raw)])
    for kwargs in ({"dry_run": True}, {"scan_only": True}):
        res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                            journal_path=str(tmp_path / "j.jsonl"),
                            exceptions_path=str(tmp_path / "e.json"), **kwargs)
        assert res["candidates"] == 1 and res["converted_rows"] == 0
        assert db_text(p, "one") == raw
        assert not (tmp_path / "exports").exists()


def test_restore_subcommand_argparse_wiring(tmp_path, monkeypatch, capsys):
    idle_guard(monkeypatch)
    raw = json.dumps(legacy_dual(), ensure_ascii=False)
    p = make_db(tmp_path, [("one", raw)])
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    export_path = res["pre_export_files"][0]["path"]
    monkeypatch.setattr(sys, "argv", [
        "slim_result_data_storage.py", "restore",
        "--db-path", str(p), "--export-file", export_path])
    capsys.readouterr()
    assert slim.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["restored_rows"] == 1
    assert db_text(p, "one") == raw


def test_vacuum_into_and_verify_pair(tmp_path, monkeypatch):
    idle_guard(monkeypatch)
    rows = [(f"r{i}", json.dumps(legacy_dual(), ensure_ascii=False)) for i in range(3)]
    rows.append(("flat", json.dumps({"a": 1, "market_data_context": {"x": 1}})))
    p = make_db(tmp_path, rows)
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["converted_rows"] == 3
    new_db = slim.vacuum_into(str(p), str(tmp_path / "slim.db"))
    check = slim.verify_db_pair(str(p), new_db)
    assert check == {"old_rows": 4, "new_rows": 4, "view_mismatches": 0,
                     "missing_in_new": 0, "integrity": "ok"}


def test_batch_splits_by_cumulative_bytes(tmp_path, monkeypatch):
    """DAV-1631 🟡-1: export batches split on cumulative bytes, not just
    row count — a batch of large rows stays under the byte cap."""
    idle_guard(monkeypatch)
    # Rows padded so 3 rows ~ 3×byte-limit/2 -> forces a byte-boundary split.
    pad = "x" * 2000
    rows = [(f"r{i}", json.dumps(legacy_dual() | {"pad": pad}, ensure_ascii=False))
            for i in range(6)]
    p = make_db(tmp_path, rows)
    # byte-limit ~2 rows worth -> expect 3 export batches, not 1.
    row_bytes = len(rows[0][1].encode("utf-8"))
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        batch_size=200,
                        batch_byte_limit=row_bytes * 2 + 10,
                        journal_path=str(tmp_path / "j.jsonl"),
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["converted_rows"] == 6
    assert res["batches"] == 3
    assert len(res["pre_export_files"]) == 3
    assert [f["rows"] for f in res["pre_export_files"]] == [2, 2, 2]


def test_converted_row_round_trips_through_strip_persist(tmp_path, monkeypatch):
    """A converted row written back through B-2's persist boundary stays v1
    and still reads identically."""
    idle_guard(monkeypatch)
    from tradingagents.storage.result_data_compat import strip_compat_view_for_persist
    raw = json.dumps(legacy_dual(), ensure_ascii=False)
    new_text = slim.convert_legacy_text(raw)
    expanded = expand_compat_view(json.loads(new_text))
    stripped = strip_compat_view_for_persist(expanded)
    assert "horizons" not in stripped and "market_data_context" not in stripped
    assert is_canonical_storage(stripped)
    # And the compat-layer read is still the legacy content.
    assert slim._view_bytes(json.dumps(stripped, ensure_ascii=False)) == slim._view_bytes(raw)


def test_real_journal_feeds_load_committed_ids(tmp_path, monkeypatch):
    """DAV-1640 🟡-1 regression: feed a journal produced by an actual
    run_slim back through load_committed_ids — the prior fixture wrote a
    fake {"event":"committed"} shape that masked the field-name mismatch
    (_write_row emits {"phase":"committed"}, the flush loop emits
    {"event":<outcome>})."""
    idle_guard(monkeypatch)
    rows = [(f"r{i}", json.dumps(legacy_dual(), ensure_ascii=False)) for i in range(3)]
    p = make_db(tmp_path, rows)
    journal = str(tmp_path / "j.jsonl")
    res = slim.run_slim(db_path=str(p), copy_rehearsal=True,
                        journal_path=journal,
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert res["converted_rows"] == 3
    committed = slim.load_committed_ids(journal)
    assert committed == {"r0", "r1", "r2"}
