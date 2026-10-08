#!/usr/bin/env python3
"""DAV-1705 管线试跑，非成绩 — build_ledger_chunked 快照日期口径测试（F-01）.

断言：默认 snapshot_date 等于 Asia/Shanghai 本地日期（与生产
`daily_snapshot_ledger.cmd_run` 的 `date.today().isoformat()` 同口径），
特别是在 UTC 日期与本地日期不同的时刻（本地 07:30，UTC 为前一日 23:30）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
