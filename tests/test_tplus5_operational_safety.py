"""Safety regressions: actual SQLite transaction and transport behavior."""
import copy
import hashlib
import json
import sqlite3
import sys

import pytest

from scripts import backfill_tplus5_shadow as cli
from tests.test_tplus5_operational_backfill import report, series, make_db, CAL


def test_multiple_rows_do_not_hold_a_read_lock_during_write(tmp_path, monkeypatch):
    p = make_db(tmp_path, report())
    conn = sqlite3.connect(p)
    row = conn.execute('select * from reports').fetchone()
    conn.execute('insert into reports values(?,?,?,?,?,?)', ('two', *row[1:]))
    conn.commit();conn.close()
    monkeypatch.setenv("TUSHARE_TOKEN", "test_stub")
    monkeypatch.setattr(cli, 'fetch_price_series', lambda *a: series())
    monkeypatch.setattr(cli, 'load_calendar', lambda: CAL)
    # DAV-1903: copy_rehearsal still runs the shared write guard (it must
    # never be a silent bypass); stub the runtime evidence for this copy.
    monkeypatch.setattr(cli, 'check_runtime_guard', lambda *a: {"allowed": True, "reason": "idle"})
    r = cli.run_backfill(db_path=str(p), as_of='2026-08-15', copy_rehearsal=True, audit_log=str(tmp_path/'journal'))
    assert r['changed_rows'] == 2
    assert r['write_conflicts'] == 0


def test_post_write_trigger_corruption_rolls_back_and_counts_mismatch(tmp_path, monkeypatch):
    p = make_db(tmp_path, report())
    conn = sqlite3.connect(p)
    original = conn.execute('select result_data from reports').fetchone()[0]
    conn.execute("create trigger corrupt after update on reports begin update reports set result_data=json_set(new.result_data,'$.opaque','corruption') where id=new.id; end")
    conn.commit();conn.close()
    monkeypatch.setenv("TUSHARE_TOKEN", "test_stub")
    monkeypatch.setattr(cli, 'fetch_price_series', lambda *a: series())
    monkeypatch.setattr(cli, 'load_calendar', lambda: CAL)
    monkeypatch.setattr(cli, 'check_runtime_guard', lambda *a: {"allowed": True, "reason": "idle"})
    r = cli.run_backfill(db_path=str(p), as_of='2026-08-15', copy_rehearsal=True, audit_log=str(tmp_path/'journal'))
    assert r['guard_mismatches'] == 1 and r['changed_rows'] == 0
    assert sqlite3.connect(p).execute('select result_data from reports').fetchone()[0] == original


def test_journal_has_absent_fields_for_reversible_delta(tmp_path, monkeypatch):
    p = make_db(tmp_path, report())
    monkeypatch.setenv("TUSHARE_TOKEN", "test_stub")
    monkeypatch.setattr(cli, 'fetch_price_series', lambda *a: series())
    monkeypatch.setattr(cli, 'load_calendar', lambda: CAL)
    monkeypatch.setattr(cli, 'check_runtime_guard', lambda *a: {"allowed": True, "reason": "idle"})
    log = tmp_path/'journal'
    cli.run_backfill(db_path=str(p), as_of='2026-08-15', copy_rehearsal=True, audit_log=str(log))
    prepared = json.loads(log.read_text().splitlines()[0])
    changes = prepared['old_values']
    assert any(x['path'] == ['short_term','t_plus_5_price'] and x['existed'] is False for x in changes)
    assert any(x['path'] == ['short_term','shadow_credit_metrics','t_plus_5_direction_hit'] and x['existed'] is True and x['value'] is None for x in changes)


def test_fetcher_joins_factors_and_uses_one_anchor_for_both_ends(monkeypatch):
    f = cli.PriceSeriesFetcher(min_interval=0)
    def query(api, sym, start, end):
        if api == 'daily':
            return [{'ts_code':sym,'trade_date':'20260804','open':20.,'close':20.},
                    {'ts_code':sym,'trade_date':'20260810','open':11.,'close':11.}]
        return [{'ts_code':sym,'trade_date':'20260804','adj_factor':1.},
                {'ts_code':sym,'trade_date':'20260810','adj_factor':2.}]
    monkeypatch.setattr(f, '_query', query)
    data = f('600519.SH','2026-08-03','2026-08-11')
    assert data['bars']['2026-08-04']['open'] == 10.
    assert data['bars']['2026-08-10']['close'] == 11.
    assert data['source'] == 'tushare.daily+adj_factor'


def test_guard_never_treats_health_without_runtime_count_as_idle(tmp_path, monkeypatch):
    import requests
    p = make_db(tmp_path, report())
    class Response:
        status_code = 200
        def json(self):
            # report_storage_mode included so the probe reaches the
            # active_analysis_count clause under test (DAV-1885 mode check
            # fires first on a bare {'status':'ok'} payload).
            return {'status': 'ok',
                    'report_storage_mode': cli.report_storage_mode()}
    monkeypatch.setattr(requests.Session,'get',lambda *a,**k: Response())
    assert cli.check_runtime_guard(str(p),'http://localhost')['reason'] == 'active_analysis_count_unknown'


def test_guard_blocks_when_health_reports_no_storage_mode(tmp_path, monkeypatch):
    """A healthz payload without report_storage_mode fails closed as
    'report_storage_mode_unknown' — the mode drift guard never trusts a
    service that cannot say which column layout it writes."""
    import requests
    p = make_db(tmp_path, report())
    class Response:
        status_code = 200
        def json(self):
            return {'status': 'ok', 'active_analysis_count': 0}
    monkeypatch.setattr(requests.Session,'get',lambda *a,**k: Response())
    assert cli.check_runtime_guard(str(p),'http://localhost')['reason'] == 'report_storage_mode_unknown'


def test_guard_busy_runtime_count_blocks_even_when_report_not_saved(tmp_path, monkeypatch):
    import requests
    p = make_db(tmp_path, report())
    class Response:
        status_code = 200
        def json(self):
            return {'status': 'ok', 'active_analysis_count': 1,
                    'report_storage_mode': cli.report_storage_mode()}
    monkeypatch.setattr(requests.Session,'get',lambda *a,**k: Response())
    assert cli.check_runtime_guard(str(p),'http://localhost')['allowed'] is False


def _must_not_be_called(*args, **kwargs):
    raise AssertionError("credential gate must fire before fetch/compute/write")


def _no_credentials(monkeypatch):
    """Simulate a credential-less operator shell: no token env and, for the
    CLI path, a .env that cannot re-seed one (DAV-2045 hermeticity)."""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)


@pytest.mark.parametrize("authorized", [False, True])
def test_run_backfill_without_token_fails_closed_authorized_or_not(
        tmp_path, monkeypatch, authorized):
    """DAV-2045 返修（总控裁定）：会拉行情/计算 T+5 的运行，不管有没有
    --production-authorized，TUSHARE_TOKEN 为空就在进入计算前拒绝——
    库表逐字节不变（0 行写入），且绝不触达 fetch/calendar/guard。"""
    p = make_db(tmp_path, report())
    before = sqlite3.connect(p).execute("SELECT * FROM reports").fetchall()
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(cli, "fetch_price_series", _must_not_be_called)
    monkeypatch.setattr(cli, "load_calendar", _must_not_be_called)
    monkeypatch.setattr(cli, "check_runtime_guard", _must_not_be_called)
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        cli.run_backfill(db_path=str(p), as_of="2026-08-15",
                         production_authorized=authorized,
                         audit_log=str(tmp_path / "journal"))
    assert sqlite3.connect(p).execute("SELECT * FROM reports").fetchall() == before


def test_dry_run_without_token_also_fails_closed(tmp_path, monkeypatch):
    """dry-run 同样会拉行情/进入计算（只是不落库），凭据门必须一样拦截。"""
    p = make_db(tmp_path, report())
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(cli, "fetch_price_series", _must_not_be_called)
    monkeypatch.setattr(cli, "load_calendar", _must_not_be_called)
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        cli.run_backfill(db_path=str(p), as_of="2026-08-15", dry_run=True)


@pytest.mark.parametrize("authorized", [False, True])
def test_cli_compute_without_token_exits_nonzero_and_writes_nothing(
        tmp_path, monkeypatch, authorized):
    """CLI 路径：main() 把拒绝映射为非零退出，库表逐字节不变。门不在
    main() 入口，只在 run_backfill 内、进入计算前生效。"""
    p = make_db(tmp_path, report())
    before = sqlite3.connect(p).execute("SELECT * FROM reports").fetchall()
    _no_credentials(monkeypatch)
    monkeypatch.setattr(cli, "fetch_price_series", _must_not_be_called)
    monkeypatch.setattr(cli, "load_calendar", _must_not_be_called)
    monkeypatch.setattr(cli, "check_runtime_guard", _must_not_be_called)
    argv = ["backfill_tplus5_shadow", "--db-path", str(p),
            "--as-of", "2026-08-15",
            "--audit-log", str(tmp_path / "journal")]
    if authorized:
        argv.insert(1, "--production-authorized")
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 1
    assert sqlite3.connect(p).execute("SELECT * FROM reports").fetchall() == before


def test_restore_subcommand_without_token_still_restores(tmp_path, monkeypatch):
    """豁免面（总控裁定）：restore 不拉行情，无凭据也必须能完成回退，
    且结果与前像逐字节相同。门不在 main() 入口做进程级拦截。"""
    p = make_db(tmp_path, report())
    pre_image = sqlite3.connect(p).execute(
        "SELECT result_data FROM reports WHERE id='one'").fetchone()[0]
    sha = hashlib.sha256(pre_image.encode("utf-8")).hexdigest()
    export = cli.export_pre_images(
        str(p), [{"id": "one", "sha256": sha}], export_dir=tmp_path / "exp")
    # 把行改成“已测量”状态：delta 只能含测量键，否则 _restore_pre_image 拒恢复。
    row = {"id": "one", "symbol": "600519.SH", "trade_date": "2026-08-03",
           "status": "completed",
           "result_data": json.loads(pre_image)}
    updated_row, _ = cli.backfill_report(
        row, as_of="2026-08-15", calendar=CAL,
        fetch_series=lambda *a: series())
    delta = cli._measurement_delta(json.loads(pre_image), updated_row["result_data"])
    mutated_obj = cli._apply_measurement_delta(json.loads(pre_image), delta)
    conn = sqlite3.connect(p)
    conn.execute("UPDATE reports SET result_data=? WHERE id='one'",
                 (json.dumps(mutated_obj, ensure_ascii=False, indent=3),))
    conn.commit(); conn.close()
    _no_credentials(monkeypatch)
    monkeypatch.setattr(cli, "check_runtime_guard",
                        lambda *a, **k: {"allowed": True, "reason": "idle"})
    monkeypatch.setattr(sys, "argv", [
        "backfill_tplus5_shadow", "restore", "--db-path", str(p),
        "--export-file", export["path"]])
    assert cli.main() == 0
    restored = sqlite3.connect(p).execute(
        "SELECT result_data FROM reports WHERE id='one'").fetchone()[0]
    assert restored == pre_image


def test_run_backfill_with_token_is_unchanged(tmp_path, monkeypatch):
    """有凭据路径（含 authorized）不受新增门影响。"""
    p = make_db(tmp_path, report())
    monkeypatch.setenv("TUSHARE_TOKEN", "test_token_value")
    monkeypatch.setattr(cli, "fetch_price_series", lambda *args: series())
    monkeypatch.setattr(cli, "load_calendar", lambda: CAL)
    monkeypatch.setattr(cli, "check_runtime_guard",
                        lambda *args: {"allowed": True, "reason": "idle"})
    r = cli.run_backfill(db_path=str(p), as_of="2026-08-15",
                         production_authorized=True,
                         audit_log=str(tmp_path / "journal"))
    assert r["changed_rows"] == 1 and r["guard_mismatches"] == 0
