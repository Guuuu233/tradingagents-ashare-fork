#!/usr/bin/env python3
"""DAV-1705 管线试跑，非成绩 — build_ledger_chunked 快照日期口径测试（F-01）.

断言：默认 snapshot_date 等于 Asia/Shanghai 本地日期（与生产
`daily_snapshot_ledger.cmd_run` 的 `date.today().isoformat()` 同口径），
特别是在 UTC 日期与本地日期不同的时刻（本地 07:30，UTC 为前一日 23:30）。
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "work" / "dav1705_pipeline"))

import build_ledger_chunked as blc
import run_shadow_trial as rst
import tradingagents.dataflows.trade_calendar as tc


def test_default_snapshot_date_is_shanghai_day_when_utc_differs(monkeypatch):
    """F-01：本地 2026-10-09 07:30（UTC 2026-10-08 23:30）默认取本地日."""
    fixed_utc = datetime(2026, 10, 8, 23, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        tc, "now_cn",
        lambda: fixed_utc.astimezone(tc.CN_TZ))
    got = blc._default_snapshot_date()
    assert got == "2026-10-09"
    assert got != fixed_utc.date().isoformat()
    assert got == tc.cn_today_str()


def test_default_snapshot_date_matches_production_helper(monkeypatch):
    """复用证明：解析器输出恒等于生产侧同一函数 cn_today_str()."""
    fixed_utc = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        tc, "now_cn",
        lambda: fixed_utc.astimezone(tc.CN_TZ))
    assert blc._default_snapshot_date() == tc.cn_today_str() == "2026-05-06"


def _write_trade_cal_pkl(path: Path, rows: list[tuple[str, int]]) -> None:
    import pandas as pd

    pd.DataFrame(rows, columns=["cal_date", "is_open"]).to_pickle(path)


def test_resolve_calendar_prefers_explicit_file(tmp_path):
    """显式文件优先：三级来源第一级."""
    cal = tmp_path / "cal.txt"
    cal.write_text("20260930\n20261009\n", encoding="utf-8")
    days, src = rst.resolve_calendar(cal, tmp_path)
    assert days == ["2026-09-30", "2026-10-09"]
    assert src.startswith("file:")


def test_resolve_calendar_falls_back_to_local_cache(tmp_path, monkeypatch):
    """无 token 时跳过 Tushare，读本机缓存覆盖最广的一份."""
    import tradingagents.dataflows.providers.industry_linkage_provider as ilp

    monkeypatch.setattr(ilp, "_get_tushare_token", lambda: "")
    api = tmp_path / "api_cache"
    api.mkdir()
    _write_trade_cal_pkl(api / "trade_cal_narrow.pkl",
                           [("20200102", 1), ("20200103", 0)])
    _write_trade_cal_pkl(api / "trade_cal_wide.pkl",
                           [("20190102", 1), ("20200102", 1),
                            ("20200103", 1)])
    days, src = rst.resolve_calendar(None, tmp_path)
    assert days == ["2019-01-02", "2020-01-02", "2020-01-03"]
    assert src.startswith("cache:")


def test_resolve_calendar_tushare_path(monkeypatch, tmp_path):
    """Tushare 在线路径：mock 请求函数，验证取数与 is_open 过滤."""
    import pandas as pd
    import tradingagents.dataflows.providers.industry_linkage_provider as ilp

    def fake_query(api_name, fields=None, params=None, **kw):
        assert api_name == "trade_cal"
        assert params["exchange"] == "SSE"
        return (pd.DataFrame([("20261009", 1), ("20261010", 0)],
                             columns=["cal_date", "is_open"]), None, None)

    monkeypatch.setattr(ilp, "_query_tushare_api", fake_query)
    days, src = rst.resolve_calendar(None, tmp_path)
    assert days == ["2026-10-09"]
    assert src == "tushare:trade_cal/SSE"


def test_resolve_calendar_all_missing_raises(tmp_path, monkeypatch):
    """三级全缺 fail-close：绝不退化成自然日口径."""
    import tradingagents.dataflows.providers.industry_linkage_provider as ilp

    monkeypatch.setattr(ilp, "_get_tushare_token", lambda: "")
    try:
        rst.resolve_calendar(None, tmp_path)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")


# ---------------------------------------------------------------------------
# DAV-1740: fixed-account filter (read side)


def test_shadow_default_user_id_is_fixed_account():
    assert rst.DEFAULT_USER_ID == "429163f7-50b6-4982-8bdf-96ae99506843"


def test_shadow_filter_by_user_keeps_fixed_only():
    recs = [
        {"report_id": "a", "user_id": rst.DEFAULT_USER_ID},
        {"report_id": "b", "user_id": "local-default-user"},
        {"report_id": "c"},
    ]
    kept, n = rst.filter_by_user(recs)
    assert n == 2
    assert [r["report_id"] for r in kept] == ["a"]


def _shadow_rec(rid, sig, user_id=None):
    return {
        "record": {
            "report_id": rid,
            "user_id": user_id if user_id is not None else rst.DEFAULT_USER_ID,
            "symbol": "600519.SH",
            "signal_date": sig,
            "horizons": {"medium": {"direction": "看多"}},
        }
    }


def test_shadow_main_filters_by_user_and_counts(tmp_path):
    """main() filters ledger by account before windowing; counts exposed."""
    import json

    led = tmp_path / "ledger.jsonl"
    recs = [
        _shadow_rec("keep1", "2026-09-30"),
        _shadow_rec("keep2", "2026-09-30"),
        _shadow_rec("skip1", "2026-09-30", user_id="local-default-user"),
    ]
    led.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                           for r in recs), encoding="utf-8")
    cal = tmp_path / "cal.txt"
    cal.write_text("20260930\n20261009\n20261012\n", encoding="utf-8")
    out = tmp_path / "out"
    rc = rst.main(["t1", "--ledger", str(led), "--calendar", str(cal),
                   "--cache-dir", str(tmp_path / "cache"),
                   "--out-dir", str(out)])
    assert rc == 0
    payload = json.loads((out / "shadow_trial-t1.json").read_text("utf-8"))
    assert payload["ledger_lines"] == 3
    assert payload["excluded_user_n"] == 1
    assert payload["filtered_ledger_lines"] == 2
    assert payload["user_id"] == rst.DEFAULT_USER_ID
    assert payload["primary"]["n_records"] == 2


# ---------------------------------------------------------------------------
# DAV-1747: chunked builder fixed-account filter (seal side, mirrors DAV-1740)


def _chunked_db(path, rows):
    import sqlite3

    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE reports (id TEXT, user_id TEXT, symbol TEXT,"
        " industry TEXT, trade_date TEXT, status TEXT, analysis_status TEXT,"
        " decision TEXT, direction TEXT, probability REAL, trade_action TEXT,"
        " risk_status TEXT, final_trade_decision TEXT, created_at TEXT,"
        " updated_at TEXT, result_data TEXT)")
    for r in rows:
        con.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.get("id"), r.get("user_id", blc.DEFAULT_USER_ID),
             "600519.SH", "白酒", "2026-07-28",
             r.get("status", "completed"), "VALID", "BUY", "看多", 60,
             "BUY", "APPROVED", "ok",
             r.get("created_at", "2026-07-28 15:43:57"),
             "2026-07-28 15:43:57", "{}"))
    con.commit()
    con.close()


def _chunked_cal(start="2026-07-01", days=400):
    out = []
    d = date.fromisoformat(start)
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_chunked_build_filters_by_user_and_counts(tmp_path, monkeypatch,
                                                  capsys):
    """非固定账户 completed 报告不入账；过滤条数计入统计输出."""
    import json

    assert blc.DEFAULT_USER_ID == "429163f7-50b6-4982-8bdf-96ae99506843"
    monkeypatch.setattr(blc.dsl, "_load_trade_dates", _chunked_cal)
    db = tmp_path / "t.db"
    _chunked_db(db, [
        {"id": "keep1"},
        {"id": "keep2"},
        {"id": "skip1", "user_id": "local-default-user"},
        # 他户 running 报告不得延期固定账户的封存（blocking 口径同生产）。
        {"id": "other-run", "user_id": "local-default-user",
         "status": "running", "created_at": "2026-07-29 10:00:00"},
    ])
    ledger_dir = tmp_path / "led"
    ns = type("NS", (), {"db": str(db), "ledger_dir": str(ledger_dir),
                         "date": "2026-08-01", "force": False})()
    assert blc.cmd_build(ns) == 0
    out = capsys.readouterr().out
    assert "skipped_user=1" in out
    lines = (ledger_dir / "forward_ledger.jsonl").read_text(
        "utf-8").splitlines()
    assert len(lines) == 2
    recs = [json.loads(ln)["record"] for ln in lines]
    assert {r["report_id"] for r in recs} == {"keep1", "keep2"}
    assert {r["user_id"] for r in recs} == {blc.DEFAULT_USER_ID}
    state = json.loads((ledger_dir / blc.dsl.STATE_FILE).read_text("utf-8"))
    assert state["last_appended"] == 2
    assert state["skipped_user_n"] == 1
