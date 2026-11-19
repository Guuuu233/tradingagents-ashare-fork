"""DAV-2463: T+5 daily auto runner — idempotency fix and the signed safety gates."""
import copy
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from scripts import backfill_tplus5_shadow as core
from scripts import t5_auto_runner as runner
from tests.test_tplus5_operational_backfill import CAL, make_db, make_db_multi, report, series

TODAY = date(2026, 8, 14)
SECRET = "SECRET-TOKEN-VALUE-9f3"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET)
    monkeypatch.setattr(core, "load_calendar", lambda: CAL)
    monkeypatch.setattr(core, "check_runtime_guard", lambda *a: {"allowed": True, "reason": "idle"})
    return {"run_dir": tmp_path / "ext" / "t5", "watch_dir": tmp_path / "watch"}


def go(env, db, fetch, **over):
    kwargs = dict(db_path=str(db), run_dir=env["run_dir"], watch_dir=env["watch_dir"],
                  max_seconds=3600, today=TODAY, mount_check=lambda p: None,
                  calendar_loader=lambda: CAL, fetcher=fetch)
    kwargs.update(over)
    return runner.run(**kwargs)


def alerts(env):
    return sorted(p.name for p in env["watch_dir"].glob("ALERT-t5auto-*")) if env["watch_dir"].exists() else []


def stored(db):
    return sqlite3.connect(db).execute("SELECT id, result_data FROM reports ORDER BY id").fetchall()


def series_on(day, close=11.0):
    data = copy.deepcopy(series())
    data["fetched_on"] = day
    data["bars"]["2026-08-10"]["close"] = close
    return data


def backfill(tmp_path, db, name, data):
    return core.run_backfill(db_path=str(db), as_of="2026-08-14", pre_export_dir=str(tmp_path / "x"),
                             audit_log=str(tmp_path / (name + ".jsonl")), fetch_series=lambda *a: data)


# --- 前置修复：fetched_on 单独变化不得写库 ---------------------------------

def test_fetched_on_only_change_is_not_written_and_second_run_is_idempotent(tmp_path, env):
    db = make_db(tmp_path, report())
    first = backfill(tmp_path, db, "a1", series_on("2026-08-14"))
    assert first["changed_rows"] == 1
    snapshot = stored(db)
    second = backfill(tmp_path, db, "a2", series_on("2026-08-17"))
    assert second["changed_rows"] == 0
    assert stored(db) == snapshot
    prov = json.loads(snapshot[0][1])["short_term"]["t_plus_5_provenance"]
    assert prov["fetched_on"] == "2026-08-14"


def test_real_result_change_still_updates_row(tmp_path, env):
    db = make_db(tmp_path, report())
    backfill(tmp_path, db, "a1", series_on("2026-08-14"))
    again = backfill(tmp_path, db, "a2", series_on("2026-08-17", close=12.0))
    assert again["changed_rows"] == 1
    unit = json.loads(stored(db)[0][1])["short_term"]
    assert unit["t_plus_5_price"] == 12.0 and unit["t_plus_5_provenance"]["fetched_on"] == "2026-08-17"


# --- 正常运行与运行记录 -----------------------------------------------------

def test_successful_run_writes_record_pre_export_audit_and_no_credential(tmp_path, env):
    db = make_db(tmp_path, report())
    code, record = go(env, db, lambda *a: series_on("2026-08-14"))
    assert code == 0 and record["status"] == "success"
    assert record["counts"]["changed_rows"] == 1 and record["counts"]["new_mature_units"] == 2
    assert record["scan_range"]["trade_date_min"] == "2026-08-03" and record["scan_range"]["sample_count"] == 1
    assert len(record["pre_export"]["sha256"]) == 64
    run_dir = env["run_dir"]
    assert (run_dir / "records" / f"run-{record['run_id']}.json").is_file()
    assert record["audit_log"].startswith(str(run_dir / "audit"))
    assert record["pre_export"]["path"].startswith(str(run_dir / "pre-export"))
    assert runner.last_success_date(run_dir) == "2026-08-14"
    for path in run_dir.rglob("*"):
        if path.is_file() and path.suffix != ".zst":
            assert SECRET not in path.read_text(errors="ignore")


def test_second_run_changes_nothing_even_when_evaluated_units_are_rechecked(tmp_path, env):
    db = make_db(tmp_path, report())
    go(env, db, lambda *a: series_on("2026-08-14"))
    snapshot = stored(db)
    for recheck in (False, True):
        code, record = go(env, db, lambda *a: series_on("2026-08-17"), recheck_evaluated=recheck)
        assert code == 0 and record["counts"]["changed_rows"] == 0
        assert stored(db) == snapshot
    assert record["counts"]["write_cap"] == 0


def test_pending_unstamped_row_is_not_written(tmp_path, env):
    db = make_db(tmp_path, report())
    before = stored(db)
    code, record = go(env, db, lambda *a: pytest.fail("must not fetch"), today=date(2026, 8, 7))
    assert code == 0 and record["counts"]["planned_rows"] == 0
    assert stored(db) == before


# --- 安全闸 1：已评结果不可改写 ---------------------------------------------

def test_rewriting_an_evaluated_result_aborts_whole_run_and_lists_rows(tmp_path, env):
    db = make_db(tmp_path, report())
    go(env, db, lambda *a: series_on("2026-08-14"))
    snapshot = stored(db)
    code, record = go(env, db, lambda *a: series_on("2026-08-17", close=9.0), recheck_evaluated=True)
    assert code == 1 and record["status"] == "aborted" and record["reason"] == "evaluated_result_rewrite"
    rows = record["detail"]["rows"]
    assert {(r["report_id"], r["horizon"]) for r in rows} == {("one", "short"), ("one", "medium")}
    assert any("t_plus_5_price" in key for key in rows[0]["changed"])
    assert stored(db) == snapshot
    assert len(alerts(env)) == 1 and "evaluated_result_rewrite" in alerts(env)[0]
    assert "one" in next(env["watch_dir"].glob("ALERT-t5auto-*")).read_text()


# --- 安全闸 2：单次写入上限 --------------------------------------------------

def _with_status(status, **extra):
    rep = report()
    for horizon in ("short_term", "medium_term"):
        rep["result_data"][horizon].update(t_plus_5_status=status, is_t_plus_5_due=False, **extra)
    return rep


def test_suspension_turning_evaluated_is_within_cap_and_not_aborted(tmp_path, env):
    db = make_db(tmp_path, _with_status("suspension"))
    code, record = go(env, db, lambda *a: series_on("2026-08-14"))
    assert code == 0 and record["status"] == "success"
    assert record["counts"]["suspension_rechecked_units"] == 2
    assert record["counts"]["write_cap"] == 2 and record["counts"]["changed_rows"] == 1
    unit = json.loads(stored(db)[0][1])["short_term"]
    assert unit["t_plus_5_status"] == "due_and_evaluated"
    assert not alerts(env)


def test_write_cap_exceeded_aborts_before_any_write(tmp_path, env):
    db = make_db(tmp_path, _with_status("legacy_unknown_status"))
    before = stored(db)
    code, record = go(env, db, lambda *a: series_on("2026-08-14"))
    assert code == 1 and record["reason"] == "write_cap_exceeded"
    assert record["detail"]["planned_rows"] == 1 and record["detail"]["cap"] == 0
    assert stored(db) == before


# --- 拒绝率 / 锁 / 守卫 ------------------------------------------------------

class CountingFetcher:
    def __init__(self, rejected_per_call=0):
        self.request_count = 0
        self.rejected_count = 0
        self.rejected_per_call = rejected_per_call

    def __call__(self, *args):
        self.request_count += 2
        self.rejected_count += self.rejected_per_call
        if self.rejected_per_call:
            return {"bars": {}, "missing_reason": "provider_rejected"}
        return series_on("2026-08-14")


def test_provider_reject_ratio_over_five_percent_aborts(tmp_path, env):
    db = make_db(tmp_path, report())
    before = stored(db)
    code, record = go(env, db, CountingFetcher(rejected_per_call=1))
    assert code == 1 and record["reason"] == "provider_reject_ratio_exceeded"
    assert record["detail"]["limit"] == 0.05 and record["detail"]["ratio"] == 0.5
    assert stored(db) == before and alerts(env)


def test_reject_ratio_within_limit_passes(tmp_path, env):
    db = make_db(tmp_path, report())
    code, record = go(env, db, CountingFetcher())
    assert code == 0 and record["counts"]["provider_rejected"] == 0


def test_database_locked_stops_without_retry_and_alarms(tmp_path, env, monkeypatch):
    reps = []
    for index in range(2):
        rep = report()
        rep["id"] = f"r{index}"
        reps.append(rep)
    db = make_db_multi(tmp_path, reps)
    calls = []

    def locked(*args, **kwargs):
        calls.append(args[1])
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(core, "_write_row", locked)
    code, record = go(env, db, lambda *a: series_on("2026-08-14"))
    assert code == 1 and record["reason"] == "database_locked"
    assert len(calls) == 1
    assert alerts(env)


def test_busy_service_skips_and_alarms_without_fetching(tmp_path, env, monkeypatch):
    db = make_db(tmp_path, report())
    monkeypatch.setattr(core, "check_runtime_guard",
                        lambda *a: {"allowed": False, "reason": "busy", "running_count": 2})
    code, record = go(env, db, lambda *a: pytest.fail("must not fetch"))
    assert code == runner.EXIT_SKIPPED_ALARM and record["status"] == "skipped"
    assert record["reason"] == "guard:busy" and alerts(env)


# --- 交易日 / 外置盘 / 资源 --------------------------------------------------

def test_non_trading_day_is_skipped_with_record_and_no_alarm(tmp_path, env):
    db = make_db(tmp_path, report())
    code, record = go(env, db, lambda *a: pytest.fail("must not fetch"), today=date(2026, 8, 15),
                      calendar_loader=lambda: CAL + [date(2026, 8, 17)],
                      run_backfill_fn=lambda **kw: pytest.fail("must not run"))
    assert code == 0 and record["status"] == "skipped" and record["reason"] == "non_trading_day"
    assert alerts(env) == []
    assert runner.load_records(env["run_dir"])[0]["status"] == "skipped"
    assert runner.last_success_date(env["run_dir"]) is None


def test_today_beyond_calendar_end_fails_instead_of_guessing(tmp_path, env):
    db = make_db(tmp_path, report())
    code, record = go(env, db, lambda *a: pytest.fail("must not fetch"), today=date(2026, 8, 20))
    assert code == 1 and record["reason"] == "calendar_out_of_range" and alerts(env)


def test_unmounted_external_volume_fails_and_writes_nothing_internal(tmp_path, env):
    db = make_db(tmp_path, report())
    for bad in ("/Volumes/__t5_missing_volume__/ta-t5-auto", str(tmp_path / "internal")):
        code, record = go(env, db, lambda *a: pytest.fail("must not fetch"), run_dir=bad,
                          mount_check=runner.require_external_volume)
        assert code == runner.EXIT_CONFIG and record is None
    assert not (tmp_path / "internal").exists()
    assert not Path("/Volumes/__t5_missing_volume__").exists()


def test_memory_and_duration_limits_abort(tmp_path, env):
    db = make_db(tmp_path, report())
    code, record = go(env, db, lambda *a: series_on("2026-08-14"), rss_reader=lambda: 7 * 1024 ** 3)
    assert code == 1 and record["reason"] == "memory_limit_exceeded"
    ticks = iter(range(0, 100000, 4000))
    code, record = go(env, db, lambda *a: series_on("2026-08-14"), clock=lambda: next(ticks), max_seconds=3600)
    assert code == 1 and record["reason"] == "duration_limit_exceeded"


def _clean_result(**over):
    result = {"changed_rows": 0, "sample_count": 1, "missing_units": [], "scan_range": {},
              "gate_facts": {"planned_rows": 0, "write_cap": 0}}
    result.update(over)
    return result


def test_memory_over_limit_at_finish_is_aborted_not_success(tmp_path, env):
    db = make_db(tmp_path, report())
    code, record = go(env, db, lambda *a: None, rss_reader=lambda: 7 * 1024 ** 3,
                      run_backfill_fn=lambda **kw: _clean_result())
    assert code == 1 and record["status"] == "aborted" and record["reason"] == "memory_limit_exceeded"
    on_disk = runner.load_records(env["run_dir"])[0]
    assert on_disk["status"] == "aborted"
    assert runner.last_success_date(env["run_dir"]) is None
    assert any("memory_limit_exceeded" in name for name in alerts(env))


def test_unwritable_run_dir_raises_run_failed_alert(tmp_path, env):
    import os
    locked = tmp_path / "readonly"
    locked.mkdir()
    os.chmod(locked, 0o555)
    try:
        db = make_db(tmp_path, report())
        code, record = go(env, db, lambda *a: pytest.fail("must not fetch"), run_dir=locked / "t5")
    finally:
        os.chmod(locked, 0o755)
    assert code == 1 and record["status"] == "failed" and record["reason"] == "PermissionError"
    assert any("run_failed" in name for name in alerts(env))


def test_run_id_has_microseconds_and_matches_audit_and_pre_export_naming(tmp_path, env):
    import re
    db = make_db(tmp_path, report())
    code, record = go(env, db, lambda *a: series_on("2026-08-14"))
    assert re.fullmatch(r"\d{8}T\d{6}-\d{6}-\d+", record["run_id"])
    assert Path(record["audit_log"]).name == record["run_id"] + ".jsonl"
    assert re.fullmatch(r"\d{8}T\d{6}-\d{6}\.jsonl\.zst", Path(record["pre_export"]["path"]).name)


# --- data_missing 重试上限 / 守望陈旧 ----------------------------------------

def test_data_missing_retried_until_limit_then_listed_in_alarm(tmp_path, env):
    db = make_db(tmp_path, report())
    calls = []

    def missing(*args):
        calls.append(1)
        return {"bars": {}, "missing_reason": "provider_shape"}
    go(env, db, missing, max_retry_attempts=2)
    go(env, db, missing, max_retry_attempts=2)
    assert len(calls) == 4 and not alerts(env)
    code, record = go(env, db, missing, max_retry_attempts=2)
    assert code == 0 and len(calls) == 4
    assert record["counts"]["retry_exceeded_units"] == 2
    assert "retry_limit_exceeded" in alerts(env)[0]


def test_stale_success_is_measured_in_trading_days(tmp_path, env):
    run_dir = env["run_dir"]
    (run_dir / "records").mkdir(parents=True)
    old = {"schema": runner.RECORD_SCHEMA, "status": "success", "trading_day": "2026-08-10"}
    (run_dir / "records" / "run-a.json").write_text(json.dumps(old))
    status = runner.check_last_success(run_dir, today=date(2026, 8, 12), calendar_loader=lambda: CAL)
    assert status["trading_days_lag"] == 2 and status["stale"] is True
    status = runner.check_last_success(run_dir, today=date(2026, 8, 11), calendar_loader=lambda: CAL)
    assert status["trading_days_lag"] == 1 and status["stale"] is False
    db = make_db(tmp_path, report())
    go(env, db, lambda *a: series_on("2026-08-14"))
    assert any("last_success_stale" in name for name in alerts(env))


def test_plist_draft_is_fixed_checkout_external_disk_and_off_peak():
    import plistlib
    import re
    text = (Path(__file__).resolve().parents[1] / "releases/launchd/com.tradingagents.t5-auto.plist").read_text()
    assert set(re.findall(r"\{\{(\w+)\}\}", text)) == {"RELEASE_DIR"}
    data = plistlib.loads(text.replace("{{RELEASE_DIR}}", "/Users/x/ta-releases/t5-auto/" + "a" * 40)
                          .encode())
    args = data["ProgramArguments"]
    assert args[args.index("--max-seconds") + 1] == "2825"
    assert "--recheck-evaluated" not in args
    assert data["RunAtLoad"] is False and data["Label"] == "com.tradingagents.t5-auto"
    assert "/Users/x/ta-releases/t5-auto/" + "a" * 40 + "/scripts/t5_auto_runner.py" in args
    assert not any("ta-work" in str(value) for value in args + [data["StandardOutPath"], data["StandardErrorPath"]])
    assert data["StandardOutPath"].startswith("/Volumes/") and data["StandardErrorPath"].startswith("/Volumes/")
    assert args[args.index("--run-dir") + 1].startswith("/Volumes/")
    assert {(d["Hour"], d["Minute"]) for d in data["StartCalendarInterval"]} == {(22, 30)}
    assert sorted(d["Weekday"] for d in data["StartCalendarInterval"]) == [1, 2, 3, 4, 5]
    assert "SECRET" not in text and "TOKEN" not in text.upper().replace("TOKENS", "")
