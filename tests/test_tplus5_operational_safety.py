"""Safety regressions: actual SQLite transaction and transport behavior."""
import copy
import json
import sqlite3

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
    r = cli.run_backfill(db_path=str(p), as_of='2026-08-15', copy_rehearsal=True, audit_log=str(tmp_path/'journal'))
    assert r['guard_mismatches'] == 1 and r['changed_rows'] == 0
    assert sqlite3.connect(p).execute('select result_data from reports').fetchone()[0] == original


def test_journal_has_absent_fields_for_reversible_delta(tmp_path, monkeypatch):
    p = make_db(tmp_path, report())
    monkeypatch.setattr(cli, 'fetch_price_series', lambda *a: series())
    monkeypatch.setattr(cli, 'load_calendar', lambda: CAL)
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
        def json(self):return {'status':'ok'}
    monkeypatch.setattr(requests.Session,'get',lambda *a,**k: Response())
    assert cli.check_runtime_guard(str(p),'http://localhost')['reason'] == 'active_analysis_count_unknown'


def test_guard_busy_runtime_count_blocks_even_when_report_not_saved(tmp_path, monkeypatch):
    import requests
    p = make_db(tmp_path, report())
    class Response:
        status_code = 200
        def json(self):return {'status':'ok','active_analysis_count':1}
    monkeypatch.setattr(requests.Session,'get',lambda *a,**k: Response())
    assert cli.check_runtime_guard(str(p),'http://localhost')['allowed'] is False
