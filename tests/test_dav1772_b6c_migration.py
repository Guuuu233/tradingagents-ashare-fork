"""DAV-1772 (存储 B-6c): raw-bytes zstd migration script tests.

Covers the 卡面 contract:
* raw-bytes compression (no json round-trip) + in-transaction
  decompress == stored-bytes verification;
* 'null' rows never migrated; export failure means zero writes;
* journal-based resume skips committed rows; rollback restores the
  exact pre-convert state (incl. P5b-cleared rows);
* restore-plaintext writes decompressed bytes back, idempotently;
* clear-plaintext (P5b) refuses without --allow-clear, backs up first
  (.backup() + zstd), verifies per-row before clearing;
* verify subcommand reports per-row decode readback对照 + the
  reserialize-mismatch diagnostic;
* vacuum-into output verifies against its source.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from scripts import b6c_compress_migration as mig


def make_db(tmp_path, rows, name="copy.db"):
    """rows: list of (id, result_data_text or None)."""
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


def col(path, report_id):
    row = sqlite3.connect(path).execute(
        "SELECT result_data, result_data_zst, result_data_zst_len FROM reports WHERE id=?",
        (report_id,)).fetchone()
    return row[0], (None if row[1] is None else bytes(row[1])), row[2]


def idle(monkeypatch):
    monkeypatch.setattr(mig, "check_runtime_guard",
                        lambda *a, **k: {"allowed": True, "reason": "idle"})
    monkeypatch.setattr(mig, "_refuse_production_unless_signed",
                        lambda *a, **k: None)


def run_convert(p, tmp_path, **kw):
    kw.setdefault("copy_rehearsal", True)
    kw.setdefault("journal_path", str(tmp_path / "j.jsonl"))
    kw.setdefault("exceptions_path", str(tmp_path / "e.json"))
    kw.setdefault("pre_export_dir", str(tmp_path / "exports"))
    return mig.run_convert(db_path=str(p), **kw)


# --- raw-bytes contract ------------------------------------------------------


def test_convert_raw_bytes_verbatim(tmp_path, monkeypatch):
    """stored bytes with odd spacing/escapes compress verbatim (no re-serialize)."""
    idle(monkeypatch)
    raw = '{"b": 1 ,"a":  "x\\u00e9y"  , "n": 1e+02}'
    p = make_db(tmp_path, [("one", raw)])
    res = run_convert(p, tmp_path)
    assert res["candidates"] == 1 and res["converted_rows"] == 1
    stored, zst, zlen = col(p, "one")
    assert stored == raw  # 明文 untouched by convert
    assert zst is not None and zst[:4] == b"\x28\xb5\x2f\xfd"  # zstd magic
    assert zlen == len(raw.encode("utf-8"))
    assert mig.decompress_raw(zst) == raw.encode("utf-8")
    assert res["reserialize_mismatch_rows"] == 1  # 诊断口径: 与重序列化不一致


def test_convert_canonical_json_reports_zero_reserialize_mismatch(tmp_path, monkeypatch):
    idle(monkeypatch)
    raw = json.dumps({"a": 1, "b": [1, 2]})  # json.dumps output == canonical
    p = make_db(tmp_path, [("one", raw)])
    res = run_convert(p, tmp_path)
    assert res["converted_rows"] == 1
    assert res["reserialize_mismatch_rows"] == 0


def test_null_and_none_rows_never_migrated(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("lit", "null"), ("none", None), ("real", '{"a":1}')])
    res = run_convert(p, tmp_path)
    assert res["null_literal_rows"] == 1 and res["null_rows"] == 1
    assert res["candidates"] == 1 and res["converted_rows"] == 1
    stored, zst, _ = col(p, "lit")
    assert stored == "null" and zst is None
    v = mig.verify_compressed(str(p))
    assert v["ok"] and v["verified_ok"] == 1
    assert v["null_literal_rows"] == 1 and v["null_rows"] == 1


def test_verify_catches_corrupt_zst(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    run_convert(p, tmp_path)
    c = sqlite3.connect(p)
    c.execute("UPDATE reports SET result_data_zst=? WHERE id='one'",
              (sqlite3.Binary(b"not-a-frame"),))
    c.commit()
    c.close()
    v = mig.verify_compressed(str(p))
    assert not v["ok"] and v["mismatch_count"] == 1


# --- safety: export / drift / resume -----------------------------------------


def test_export_failure_means_zero_writes(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}'), ("two", '{"b": 2}')])
    monkeypatch.setattr(mig, "export_pre_images",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    try:
        run_convert(p, tmp_path)
    except RuntimeError:
        pass
    _, zst1, _ = col(p, "one")
    _, zst2, _ = col(p, "two")
    assert zst1 is None and zst2 is None


def test_drift_between_export_and_write_is_skipped(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    orig_write = mig._write_row

    def drifting_write(conn, report_id, audit, exported_sha=None):
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE reports SET result_data=? WHERE id=?",
                     ('{"a": 999}', report_id))
        conn.commit()
        return orig_write(conn, report_id, audit, exported_sha=exported_sha)

    monkeypatch.setattr(mig, "_write_row", drifting_write)
    res = run_convert(p, tmp_path)
    assert res["pre_export_mismatch"] == 1 and res["converted_rows"] == 0


def test_resume_skips_committed_rows(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [(f"r{i}", json.dumps({"i": i})) for i in range(4)])
    j = tmp_path / "j.jsonl"
    run_convert(p, tmp_path, journal_path=str(j),
                exceptions_path=str(tmp_path / "e.json"),
                pre_export_dir=str(tmp_path / "exports"))
    # Second run with the same journal: everything resumes-skipped.
    res2 = mig.run_convert(db_path=str(p), copy_rehearsal=True,
                           journal_path=str(j),
                           exceptions_path=str(tmp_path / "e2.json"),
                           pre_export_dir=str(tmp_path / "exports2"))
    assert res2["converted_rows"] == 0
    assert res2["resumed_skipped"] == 4


def test_resume_reports_full_run_byte_totals(tmp_path, monkeypatch):
    idle(monkeypatch)
    raws = [(f"r{i}", json.dumps({"i": i, "pad": "z" * 200})) for i in range(3)]
    p = make_db(tmp_path, raws)
    j = str(tmp_path / "j.jsonl")
    first = run_convert(p, tmp_path, journal_path=j,
                        exceptions_path=str(tmp_path / "e.json"),
                        pre_export_dir=str(tmp_path / "exports"))
    assert first["converted_rows"] == 3 and first["plain_bytes"] > 0
    second = mig.run_convert(db_path=str(p), copy_rehearsal=True,
                             journal_path=j,
                             exceptions_path=str(tmp_path / "e2.json"),
                             pre_export_dir=str(tmp_path / "exports2"))
    assert second["resumed_skipped"] == 3
    assert second["plain_bytes"] == first["plain_bytes"]
    assert second["zst_bytes"] == first["zst_bytes"]


def test_dry_run_and_scan_only_write_nothing(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    for mode in ({"dry_run": True}, {"scan_only": True}):
        res = run_convert(p, tmp_path, journal_path=str(tmp_path / "jd.jsonl"),
                          exceptions_path=str(tmp_path / "ed.json"),
                          pre_export_dir=str(tmp_path / "exd"), **mode)
        assert res["converted_rows"] == 0
    _, zst, _ = col(p, "one")
    assert zst is None


# --- rollback / restore-plaintext / clear-plaintext ---------------------------


def test_convert_then_rollback_is_byte_identical(tmp_path, monkeypatch):
    idle(monkeypatch)
    raw = '{"b": 1 ,"a":  "xy"  }'
    p = make_db(tmp_path, [("one", raw), ("two", json.dumps({"k": "v"}))])
    exp_dir = tmp_path / "exports"
    run_convert(p, tmp_path, pre_export_dir=str(exp_dir))
    export_file = next(exp_dir.glob("*.jsonl.zst"))
    res = mig.rollback_from_export(str(p), str(export_file), copy_rehearsal=True)
    assert res["rolled_back"] == 2
    assert col(p, "one")[0] == raw and col(p, "one")[1] is None
    # Idempotent second run.
    res2 = mig.rollback_from_export(str(p), str(export_file), copy_rehearsal=True)
    assert res2["already_clean"] == 2


def test_rollback_restores_p5b_cleared_plaintext(tmp_path, monkeypatch):
    idle(monkeypatch)
    raw = '{"a": 1, "b": [1,2,3]}'
    p = make_db(tmp_path, [("one", raw)])
    exp_dir = tmp_path / "exports"
    run_convert(p, tmp_path, pre_export_dir=str(exp_dir))
    export_file = next(exp_dir.glob("*.jsonl.zst"))
    mig.clear_plaintext(str(p), backup_dir=str(tmp_path / "bk"),
                        copy_rehearsal=True, allow_clear=True)
    assert col(p, "one")[0] is None  # 明文 cleared
    res = mig.rollback_from_export(str(p), str(export_file), copy_rehearsal=True)
    assert res["restored_plaintext"] == 1
    assert col(p, "one")[0] == raw and col(p, "one")[1] is None


def test_rollback_conflict_keeps_foreign_write(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    exp_dir = tmp_path / "exports"
    run_convert(p, tmp_path, pre_export_dir=str(exp_dir))
    export_file = next(exp_dir.glob("*.jsonl.zst"))
    c = sqlite3.connect(p)
    c.execute("UPDATE reports SET result_data=? WHERE id='one'", ('{"a": 2}',))
    c.commit()
    c.close()
    res = mig.rollback_from_export(str(p), str(export_file), copy_rehearsal=True)
    assert res["conflicts"] == 1
    assert col(p, "one")[0] == '{"a": 2}' and col(p, "one")[1] is not None


def test_corrupt_export_file_refuses_rollback(tmp_path, monkeypatch):
    import zstandard as _z
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    bad = tmp_path / "bad.jsonl.zst"
    bad.write_bytes(_z.ZstdCompressor(level=3).compress(b'{"kind":"row"}\n'))
    try:
        mig.rollback_from_export(str(p), str(bad), copy_rehearsal=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("corrupt export must refuse rollback")


def test_restore_plaintext_round_trip(tmp_path, monkeypatch):
    idle(monkeypatch)
    raws = {f"r{i}": json.dumps({"i": i, "pad": "x" * 100}) for i in range(3)}
    p = make_db(tmp_path, list(raws.items()))
    run_convert(p, tmp_path)
    clr = mig.clear_plaintext(str(p), backup_dir=str(tmp_path / "bk"),
                              copy_rehearsal=True, allow_clear=True)
    assert clr["cleared_rows"] == 3
    assert clr["backup"]["backup_db"] and Path(clr["backup"]["backup_db"]).is_file()
    assert Path(clr["backup"]["backup_zst"]).is_file()
    rp = mig.restore_plaintext(str(p), copy_rehearsal=True)
    assert rp["restored_rows"] == 3
    for rid, raw in raws.items():
        assert col(p, rid)[0] == raw
    # Idempotent rerun.
    assert mig.restore_plaintext(str(p), copy_rehearsal=True)["restored_rows"] == 0


def test_clear_plaintext_requires_allow_clear(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    run_convert(p, tmp_path)
    try:
        mig.clear_plaintext(str(p), backup_dir=str(tmp_path / "bk"),
                            copy_rehearsal=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("clear-plaintext must require --allow-clear")
    assert col(p, "one")[0] == '{"a": 1}'


def test_clear_plaintext_keeps_plaintext_on_bad_zst(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    run_convert(p, tmp_path)
    c = sqlite3.connect(p)
    c.execute("UPDATE reports SET result_data_zst=? WHERE id='one'",
              (sqlite3.Binary(b"junk"),))
    c.commit()
    c.close()
    res = mig.clear_plaintext(str(p), backup_dir=str(tmp_path / "bk"),
                              copy_rehearsal=True, allow_clear=True)
    assert res["cleared_rows"] == 0 and res["failed"] == 1
    assert col(p, "one")[0] == '{"a": 1}'  # 明文 kept — never a loss path


def test_p5b_backup_retention_prunes_old(tmp_path, monkeypatch):
    import time
    idle(monkeypatch)
    bk = tmp_path / "bk"
    bk.mkdir()
    old = bk / (mig.BACKUP_PREFIX + "20000101T000000.db")
    old.write_bytes(b"old")
    ancient = old.stat().st_mtime
    os_utime_old = (ancient - 40 * 86400, ancient - 40 * 86400)
    import os as _os
    _os.utime(old, os_utime_old)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    mig.ensure_columns(str(p))
    out = mig._p5b_backup(str(p), backup_dir=bk)
    assert not old.exists() and out["pruned"] == [old.name]
    assert Path(out["backup_db"]).is_file()


def test_batch_splits_by_cumulative_bytes(tmp_path, monkeypatch):
    idle(monkeypatch)
    rows = [(f"r{i}", json.dumps({"i": i, "pad": "y" * 5000})) for i in range(6)]
    p = make_db(tmp_path, rows)
    res = run_convert(p, tmp_path, batch_size=100, batch_byte_limit=8000)
    assert res["converted_rows"] == 6 and res["batches"] >= 3


def test_busy_guard_prevents_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(mig, "check_runtime_guard",
                        lambda *a, **k: {"allowed": False, "reason": "busy"})
    monkeypatch.setattr(mig, "_refuse_production_unless_signed",
                        lambda *a, **k: None)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    # copy_rehearsal=False -> guard enforced (run_convert defaults it True).
    res = run_convert(p, tmp_path, copy_rehearsal=False)
    assert res.get("skipped") and res["converted_rows"] == 0
    _, zst, _ = col(p, "one")
    assert zst is None


def test_export_preimage_subcommand(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}'), ("lit", "null")])
    res = mig.run_export_preimage(str(p), export_dir=str(tmp_path / "ex"))
    assert res["rows"] == 1 and Path(res["path"]).is_file()
    _, zst, _ = col(p, "one")
    assert zst is None  # no writes


def test_verify_pair_after_vacuum(tmp_path, monkeypatch):
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": [1,2]}'), ("two", '{"b": "x"}')])
    run_convert(p, tmp_path)
    new_db = str(tmp_path / "slim.db")
    mig.vacuum_into(str(p), new_db)
    check = mig.verify_db_pair(str(p), new_db)
    assert check["integrity"] == "ok"
    assert check["old_rows"] == check["new_rows"] == 2
    assert check["view_mismatches"] == 0 and check["missing_in_new"] == 0


def test_verify_cli_exit_codes(tmp_path, monkeypatch):
    import sys
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    # Unmigrated row: plain verify passes; --require-complete fails.
    monkeypatch.setattr(sys, "argv", ["b6c", "verify", "--db-path", str(p)])
    assert mig.main() == 0
    monkeypatch.setattr(sys, "argv",
                          ["b6c", "verify", "--db-path", str(p), "--require-complete"])
    assert mig.main() == 1
    # After convert: --require-complete passes.
    run_convert(p, tmp_path)
    monkeypatch.setattr(sys, "argv",
                          ["b6c", "verify", "--db-path", str(p), "--require-complete"])
    assert mig.main() == 0


# ===========================================================================
# 复审 DAV-1799 返修回归 (R1 / R2 / M1 / M2 / M3 / M4 / M5)
# 全部走真实代码路径; CLI 用例走 main() 的真实 argparse 解析, 不只调函数。
# ===========================================================================


def _run_cli(monkeypatch, argv, capture):
    """Invoke main() with a real argv, recording the kwargs each subcommand
    receives so flag-placement regressions (R1) are observable."""
    import sys
    monkeypatch.setattr(sys, "argv", ["b6c"] + argv)
    return mig.main()


def test_cli_flags_before_subcommand_are_not_reset(tmp_path, monkeypatch):
    """R1: argparse copied subparser defaults over the already-parsed global
    value, silently turning --copy-rehearsal back into False and --vacuum-into
    back into None. Exercise the real CLI parser in both flag positions."""
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    seen = {}

    def fake_clear(db_path, **kw):
        seen.update(kw)
        return {"cleared_rows": 1, "failed": 0, "failures": []}

    monkeypatch.setattr(mig, "clear_plaintext", fake_clear)

    # Flags BEFORE the subcommand (the reviewer's repro shape).
    assert _run_cli(monkeypatch, [
        "--copy-rehearsal", "--production-authorized",
        "--vacuum-into", str(tmp_path / "v.db"),
        "clear-plaintext", "--db-path", str(p), "--allow-clear",
    ], None) == 0
    assert seen["copy_rehearsal"] is True
    assert seen["production_authorized"] is True
    assert seen["vacuum_into_path"] == str(tmp_path / "v.db")
    assert seen["allow_clear"] is True

    # Flags AFTER the subcommand must keep working too.
    seen.clear()
    assert _run_cli(monkeypatch, [
        "clear-plaintext", "--db-path", str(p), "--allow-clear",
        "--copy-rehearsal", "--vacuum-into", str(tmp_path / "w.db"),
    ], None) == 0
    assert seen["copy_rehearsal"] is True
    assert seen["vacuum_into_path"] == str(tmp_path / "w.db")


def test_cli_production_authorized_flag_survives_subcommand(tmp_path, monkeypatch):
    """R1 second half: the production write gate is the flag most dangerous to
    lose, so pin it for rollback / restore-plaintext too."""
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    seen = {}

    monkeypatch.setattr(mig, "rollback_from_export",
                        lambda db_path, export_file, **kw:
                            seen.update(kw) or {"rolled_back": 1})
    _run_cli(monkeypatch, ["--copy-rehearsal", "rollback", "--db-path", str(p),
                           "--export-file", "x.jsonl"], None)
    assert seen["copy_rehearsal"] is True
    assert seen["production_authorized"] is False

    monkeypatch.setattr(mig, "restore_plaintext",
                        lambda db_path, **kw:
                            seen.update(kw) or {"restored_rows": 0, "failed": 0})
    _run_cli(monkeypatch, ["restore-plaintext", "--db-path", str(p),
                           "--production-authorized"], None)
    assert seen["production_authorized"] is True


def test_cli_missing_db_path_exits_2(tmp_path, monkeypatch):
    """--db-path is enforced after the merge (a required=True parent would
    reject the legal post-subcommand spelling)."""
    import pytest
    with pytest.raises(SystemExit) as excinfo:
        _run_cli(monkeypatch, ["clear-plaintext", "--allow-clear"], None)
    assert excinfo.value.code == 2


def test_verify_detects_lost_blob_after_plaintext_cleared(tmp_path, monkeypatch):
    """R2: after P5b the zst blob is the only copy. A row whose blob vanished
    must NOT be counted as a healthy NULL row — --require-complete used to
    report ok=True on silent data loss."""
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}'), ("two", '{"b": 2}'),
                           ("none", None)])
    run_convert(p, tmp_path)
    mig.clear_plaintext(str(p), allow_clear=True, copy_rehearsal=True,
                        backup_dir=str(tmp_path / "bk"))
    # Simulate storage-layer corruption / a bad UPDATE: blob gone, zst_len kept.
    conn = sqlite3.connect(p)
    conn.execute("UPDATE reports SET result_data_zst=NULL WHERE id='one'")
    conn.commit()
    conn.close()
    res = mig.verify_compressed(str(p), require_complete=True)
    assert res["ok"] is False
    assert res["lost_blob_count"] == 1
    assert res["lost_blobs"][0]["report_id"] == "one"
    assert res["null_rows"] == 1  # the genuinely NULL row is still a NULL row


def test_clear_plaintext_keeps_plaintext_when_zst_len_drifted(tmp_path, monkeypatch):
    """M1: clear is an irreversible delete of明文, so its admission check must
    be at least as strict as verify's. verify flags zst_len drift as red;
    clear must then keep the明文 instead of erasing it."""
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}'), ("two", '{"b": 2}')])
    run_convert(p, tmp_path)
    conn = sqlite3.connect(p)
    conn.execute("UPDATE reports SET result_data_zst_len=999999 WHERE id='one'")
    conn.commit()
    conn.close()
    assert mig.verify_compressed(str(p))["ok"] is False  # verify already red
    res = mig.clear_plaintext(str(p), allow_clear=True, copy_rehearsal=True,
                              backup_dir=str(tmp_path / "bk"))
    assert res["cleared_rows"] == 1        # only the healthy row
    assert res["failed"] == 1
    assert "zst_len drift" in res["failures"][0]["error"]
    assert col(p, "one")[0] is not None    # 明文 of the drifted row preserved


def test_clear_plaintext_early_returns_when_nothing_to_clear(tmp_path, monkeypatch):
    """0 待清行 must not trigger a multi-GB backup (复审 🟢)."""
    idle(monkeypatch)
    p = make_db(tmp_path, [("none", None)])
    res = mig.clear_plaintext(str(p), allow_clear=True, copy_rehearsal=True,
                              backup_dir=str(tmp_path / "bk"))
    assert res.get("skipped") is True and res["backup"] is None
    assert not (tmp_path / "bk").exists() or not list((tmp_path / "bk").iterdir())


def test_blob_row_is_bucketed_not_fatal(tmp_path, monkeypatch):
    """M2: a BLOB result_data (storage-level undecodable bytes) must not abort
    the entire convert round; other rows still migrate and the bad row stays
    visible in the exceptions report."""
    idle(monkeypatch)
    p = tmp_path / "copy.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE reports(id TEXT PRIMARY KEY, symbol TEXT,"
                 " trade_date TEXT, status TEXT, result_data TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO reports VALUES('good','SYM','2026-10-02','completed',?,'old')",
                 (json.dumps({"a": 1}),))
    conn.execute("INSERT INTO reports VALUES('blob','SYM','2026-10-02','completed',X'7D7B','old')")
    conn.commit()
    conn.close()
    res = run_convert(p, tmp_path)
    assert res["converted_rows"] == 1        # healthy row migrated
    assert res["non_text_rows"] == 1         # BLOB row bucketed
    assert mig.verify_compressed(str(p), require_complete=True)["unmigrated"] == 1


def test_operational_error_classification_is_narrow():
    """M3: only real lock contention may be classified as a concurrency
    conflict; readonly/disk errors must not masquerade as retryable."""
    import sqlite3
    assert mig._is_lock_conflict(
        sqlite3.OperationalError("database is locked")) is True
    assert mig._is_lock_conflict(
        sqlite3.OperationalError("database table is busy")) is True
    assert mig._is_lock_conflict(
        sqlite3.OperationalError("attempt to write a readonly database")) is False
    assert mig._is_lock_conflict(
        sqlite3.OperationalError("no such table: reports")) is False


def test_convert_exit_code_nonzero_when_rows_not_migrated(tmp_path, monkeypatch):
    """M4: release scripts read only the exit code; pre_export_mismatch /
    missing / non_text rows must not exit 0 (复审 M4)."""
    import sys
    idle(monkeypatch)
    p = tmp_path / "copy.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE reports(id TEXT PRIMARY KEY, symbol TEXT,"
                 " trade_date TEXT, status TEXT, result_data TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO reports VALUES('blob','SYM','2026-10-02','completed',X'7D7B','old')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(sys, "argv",
                        ["b6c", "convert", "--db-path", str(p),
                         "--copy-rehearsal",
                         "--journal-path", str(tmp_path / "j.jsonl"),
                         "--exceptions-path", str(tmp_path / "e.json"),
                         "--pre-export-dir", str(tmp_path / "ex")])
    assert mig.main() == 1  # the BLOB row stayed unmigrated


def test_production_gate_blocks_unauthorized_and_pre_earliest(tmp_path, monkeypatch):
    """M5: the production write double gate had zero assertions. B-4's twin
    gate used `<=` where B-6c uses `<`; that boundary must be pinned or the
    next script regresses to it."""
    prod = tmp_path / "prod.db"
    make_db(tmp_path, [("one", '{"a": 1}')], name="prod.db")
    # Point _is_production at our fixture.
    monkeypatch.setattr(mig, "_is_production", lambda path: Path(path) == prod)
    # Not authorized -> refuse.
    with pytest.raises(RuntimeError):
        mig._refuse_production_unless_signed(str(prod), production_authorized=False)
    # Authorized but before earliest -> refuse.
    def fake_now_before():
        return datetime(2026, 10, 15, 12, 0)
    monkeypatch.setattr(mig, "now_cn", fake_now_before)
    with pytest.raises(RuntimeError):
        mig._refuse_production_unless_signed(str(prod), production_authorized=True)
    # Authorized on the earliest date itself -> allowed (boundary is exclusive).
    monkeypatch.setattr(mig, "now_cn", lambda: datetime(2026, 10, 16, 0, 0))
    mig._refuse_production_unless_signed(str(prod), production_authorized=True)


def test_is_production_recognises_same_file_and_never_raises(tmp_path, monkeypatch):
    """M5: _is_production must recognise the production library through a
    symlink/hardlink even when the canonical spelling is absent, and must not
    propagate OSError from samefile when either side is missing."""
    prod = tmp_path / "prod.db"
    make_db(tmp_path, [("one", '{"a": 1}')], name="prod.db")
    link = tmp_path / "link.db"
    link.symlink_to(prod)

    real = mig.Path("/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db")
    monkeypatch.setattr(mig, "Path", lambda p: prod if p == str(real) else Path(p))
    # Same file reached through a symlink is still production.
    assert mig._is_production(str(link)) is True
    # A genuinely unrelated file is not — and must not raise.
    other = tmp_path / "other.db"
    make_db(tmp_path, [("two", '{"a": 1}')], name="other.db")
    assert mig._is_production(str(other)) is False
    # Missing target: samefile raises OSError; must fail closed, not propagate.
    assert mig._is_production(str(tmp_path / "does-not-exist.db")) is False


def test_copy_rehearsal_never_relaxes_the_production_gate(tmp_path, monkeypatch):
    """R1 collateral risk: --copy-rehearsal must only relax the runtime
    (healthz / analysis-count) guard. The production authorization gate is
    enforced unconditionally before it in every write subcommand, so a lost or
    forged flag can never unlock a production write."""
    idle_calls = []

    def forbidden(*a, **kw):
        raise RuntimeError("production B-6c write forbidden")

    for fn in ("run_convert", "rollback_from_export",
               "restore_plaintext", "clear_plaintext"):
        monkeypatch.setattr(mig, "_refuse_production_unless_signed", forbidden)
        monkeypatch.setattr(mig, "_is_production", lambda path: True)
        monkeypatch.setattr(mig, "check_runtime_guard",
                            lambda *a, **k: idle_calls.append(a) or
                            {"allowed": True, "reason": "idle"})
        p = make_db(tmp_path, [("one", '{"a": 1}')], name=f"{fn}.db")
        kwargs = {"copy_rehearsal": True, "production_authorized": False}
        args = (str(p),)
        if fn == "rollback_from_export":
            args = (str(p), "x.jsonl")
        elif fn == "clear_plaintext":
            kwargs["allow_clear"] = True
        elif fn == "run_convert":
            args = ()
            kwargs["db_path"] = str(p)
            kwargs["journal_path"] = str(tmp_path / f"{fn}-j.jsonl")
        with pytest.raises(RuntimeError, match="production"):
            getattr(mig, fn)(*args, **kwargs)
    assert idle_calls == []  # the healthz guard was never reached


def test_cli_convert_runs_end_to_end_without_unrelated_flags(tmp_path, monkeypatch):
    """The shared option parent means `convert` also parses flags meant for
    other subcommands. Dispatch must whitelist its own kwargs instead of
    **args, or an unrelated flag crashes the run with TypeError/KeyError."""
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}'), ("null", "null")])
    import sys
    monkeypatch.setattr(sys, "argv",
                        ["b6c", "--copy-rehearsal", "convert",
                         "--db-path", str(p),
                         "--journal-path", str(tmp_path / "j.jsonl"),
                         "--exceptions-path", str(tmp_path / "e.json"),
                         "--pre-export-dir", str(tmp_path / "ex"),
                         "--batch-size", "10"])
    assert mig.main() == 0
    assert col(p, "one")[1] is not None            # compressed
    assert col(p, "null")[1] is None                # 'null' never migrated


def test_cli_convert_scan_only_returns_zero(tmp_path, monkeypatch):
    """--scan-only parses on the convert path and reports without writing."""
    idle(monkeypatch)
    p = make_db(tmp_path, [("one", '{"a": 1}')])
    import sys
    monkeypatch.setattr(sys, "argv",
                        ["b6c", "convert", "--db-path", str(p),
                         "--copy-rehearsal", "--scan-only",
                         "--journal-path", str(tmp_path / "j.jsonl")])
    assert mig.main() == 0
    assert col(p, "one")[1] is None                 # nothing written
