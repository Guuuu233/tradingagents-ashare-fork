"""Safety regressions: actual SQLite transaction and transport behavior."""
import copy
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
    monkeypatch.setattr(cli, 'fetch_price_series', lambda *a: series())
    monkeypatch.setattr(cli, 'load_calendar', lambda: CAL)
    monkeypatch.setattr(cli, 'check_runtime_guard', lambda *a: {"allowed": True, "reason": "idle"})
    r = cli.run_backfill(db_path=str(p), as_of='2026-08-15', copy_rehearsal=True, audit_log=str(tmp_path/'journal'))
    assert r['guard_mismatches'] == 1 and r['changed_rows'] == 0
    assert sqlite3.connect(p).execute('select result_data from reports').fetchone()[0] == original


def test_journal_has_absent_fields_for_reversible_delta(tmp_path, monkeypatch):
    p = make_db(tmp_path, report())
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
    raise AssertionError("authorized run reached a fetch/compute/write stage")


@pytest.mark.parametrize("token", [None, "", "   "])
def test_authorized_run_without_tushare_token_fails_closed(tmp_path, monkeypatch, token):
    """DAV-2045: --production-authorized with a missing/empty TUSHARE_TOKEN
    refuses BEFORE any fetch, computation or write. The DAV-1853 first run
    had exactly this shape and still stamped ``data_missing`` on 1011 rows."""
    p = make_db(tmp_path, report())
    before = sqlite3.connect(p).execute("SELECT * FROM reports").fetchall()
    if token is None:
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("TUSHARE_TOKEN", token)
    # Any stage past the credential gate fails loudly.
    monkeypatch.setattr(cli, "fetch_price_series", _must_not_be_called)
    monkeypatch.setattr(cli, "load_calendar", _must_not_be_called)
    monkeypatch.setattr(cli, "check_runtime_guard", _must_not_be_called)
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        cli.run_backfill(db_path=str(p), as_of="2026-08-15",
                         production_authorized=True,
                         audit_log=str(tmp_path / "journal"))
    # 0 rows written: the table is byte-identical.
    assert sqlite3.connect(p).execute("SELECT * FROM reports").fetchall() == before


def test_authorized_cli_without_token_exits_nonzero_and_writes_nothing(tmp_path, monkeypatch):
    """CLI path: main() surfaces the refusal as a non-zero exit and the
    database stays byte-identical (DAV-2045 acceptance shape)."""
    p = make_db(tmp_path, report())
    before = sqlite3.connect(p).execute("SELECT * FROM reports").fetchall()
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    # Keep the test hermetic: a real repo-root .env must not re-seed a token.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(cli, "fetch_price_series", _must_not_be_called)
    monkeypatch.setattr(cli, "load_calendar", _must_not_be_called)
    monkeypatch.setattr(cli, "check_runtime_guard", _must_not_be_called)
    monkeypatch.setattr(sys, "argv", [
        "backfill_tplus5_shadow", "--production-authorized",
        "--db-path", str(p), "--as-of", "2026-08-15",
        "--audit-log", str(tmp_path / "journal")])
    assert cli.main() == 1
    assert sqlite3.connect(p).execute("SELECT * FROM reports").fetchall() == before


def test_authorized_run_with_token_is_unchanged(tmp_path, monkeypatch):
    """The credentialed authorized path is unaffected by the new gate."""
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


# ---------------------------------------------------------------------------
# DAV-2047 rework: the gate covered only run_backfill(production_authorized=True)
# while restore_from_export and the main() dispatch path stayed open. The gate
# now covers EVERY authorized entry point, while non-authorized runs are
# deliberately left untouched (offline tooling must work without a token).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, "", "   "])
def test_authorized_restore_without_tushare_token_fails_closed(tmp_path, monkeypatch, token):
    """DAV-2047: restore_from_export(production_authorized=True) without a
    token dies BEFORE the export file is read and BEFORE the DB is opened
    writable — 0 rows written, 0 bytes of the export consumed."""
    p = make_db(tmp_path, report())
    before = sqlite3.connect(p).execute("SELECT * FROM reports").fetchall()
    export_file = tmp_path / "export.jsonl.zst"
    if token is None:
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("TUSHARE_TOKEN", token)
    monkeypatch.setattr(cli, "_load_pre_export", _must_not_be_called)
    monkeypatch.setattr(cli, "check_runtime_guard", _must_not_be_called)
    monkeypatch.setattr(cli, "_open_db", _must_not_be_called)
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        cli.restore_from_export(str(p), str(export_file),
                                production_authorized=True)
    assert sqlite3.connect(p).execute("SELECT * FROM reports").fetchall() == before


def test_authorized_restore_cli_without_token_exits_nonzero_and_writes_nothing(tmp_path, monkeypatch):
    """DAV-2047: `main()` refuses before subcommand dispatch — an
    authorized `restore` CLI call exits 1 without ever opening the export
    file or the DB writable."""
    p = make_db(tmp_path, report())
    before = sqlite3.connect(p).execute("SELECT * FROM reports").fetchall()
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_load_pre_export", _must_not_be_called)
    monkeypatch.setattr(cli, "check_runtime_guard", _must_not_be_called)
    monkeypatch.setattr(cli, "_open_db", _must_not_be_called)
    monkeypatch.setattr(sys, "argv", [
        "backfill_tplus5_shadow", "restore",
        "--db-path", str(p), "--export-file", str(tmp_path / "export.jsonl.zst"),
        "--production-authorized"])
    assert cli.main() == 1
    assert sqlite3.connect(p).execute("SELECT * FROM reports").fetchall() == before


@pytest.mark.parametrize("entry", ["run_backfill", "restore_from_export"])
def test_non_authorized_entry_points_without_token_still_reach_their_stage(tmp_path, monkeypatch, entry):
    """DAV-2047 scope: the token gate binds ONLY --production-authorized.
    Non-authorized calls must still get PAST the credential check (they
    may then be stopped by their own later guards, not by the token gate).
    This pins the review's scope boundary: no token => refused applies to
    authorized paths only."""
    p = make_db(tmp_path, report())
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    if entry == "run_backfill":
        monkeypatch.setattr(cli, "load_calendar", lambda: CAL)
        monkeypatch.setattr(cli, "fetch_price_series", lambda *args: series())
        monkeypatch.setattr(cli, "check_runtime_guard",
                            lambda *args: {"allowed": True, "reason": "idle"})
        # Reaches the write stage and completes without a token gate error.
        r = cli.run_backfill(db_path=str(p), as_of="2026-08-15",
                             audit_log=str(tmp_path / "journal"))
        assert r["changed_rows"] == 1
    else:
        monkeypatch.setattr(cli, "_load_pre_export", _must_not_be_called)
        # No RuntimeError from the token gate — the failure happens later
        # inside _load_pre_export (which we make fail with AssertionError).
        with pytest.raises(AssertionError):
            cli.restore_from_export(str(p), str(tmp_path / "missing.zst"))


def test_authorized_restore_with_token_proceeds_past_the_gate(tmp_path, monkeypatch):
    """Authorized restore WITH a token is not blocked by the new check —
    it proceeds to read the export file (the next stage, which we stub to
    prove the gate was passed)."""
    p = make_db(tmp_path, report())
    monkeypatch.setenv("TUSHARE_TOKEN", "test_token_value")
    monkeypatch.setattr(cli, "_load_pre_export", _must_not_be_called)
    with pytest.raises(AssertionError):
        cli.restore_from_export(str(p), str(tmp_path / "export.jsonl.zst"),
                                production_authorized=True)
