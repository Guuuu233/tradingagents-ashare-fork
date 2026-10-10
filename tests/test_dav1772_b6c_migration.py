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
from pathlib import Path

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
