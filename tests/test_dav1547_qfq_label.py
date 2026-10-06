"""DAV-1547 rework gates: vendor_qfq primary basis + index-leg same-window entry open.

1. A synthetic 10-for-10 (adj_factor doubles mid-window) must leave the adjusted
   r_stock unaffected by the ex-date price halving — raw price return shows the
   fake -50% drop, adjusted does not.
2. An SW industry row missing the entry-date open must yield r_sw = NaN (missing),
   never a silent close-based fallback.

Pure offline tests; no cache, no network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import scripts.phase2.build_phase2_chunks as bc


def _ctx_for(cal, bars, adj, sw_close=None, sw_open=None, industry=None):
    """Build the minimal ctx dict process_symbol/assemble needs for one symbol."""
    cal_arr = np.array(cal)
    cal_pos = {d: i for i, d in enumerate(cal)}
    return dict(
        cal_days=cal, cal_arr=cal_arr, cal_pos=cal_pos, cal_index=cal_arr,
        st_intervals={}, industry_pit=industry or {},
        index_weights={"hs300": {}, "zz500": {}},
        idx_snap_dates={"hs300": np.array([]), "zz500": np.array([])},
        sw_close=sw_close or {}, sw_open=sw_open or {},
        list_date_map={}, market_map={},
    ), bars, adj


def test_adjusted_return_ignores_10for10_split():
    """10送10 mid-window: raw return shows a fake crash, qfq return must not."""
    code = "000001.SZ"
    # 60 calendar days: signal at idx 10, entry 11, exit at 10+40=50.
    cal = [f"2024{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(60)]
    sig_i, entry_i, exit_i = 10, 11, 50
    ex_i = 30  # 10-for-10 takes effect between idx 30 and 31

    # raw close halves at ex-date (20 -> 10); qfq factor doubles (1 -> 2).
    raw_close = np.array([20.0] * (ex_i + 1) + [10.0] * (60 - ex_i - 1))
    raw_open = raw_close.copy()
    adj_factor = np.array([1.0] * (ex_i + 1) + [2.0] * (60 - ex_i - 1))

    dates = np.array(cal)
    df_code = pd.DataFrame({
        "ts_code": code, "trade_date": dates,
        "open": raw_open, "high": raw_close, "low": raw_close,
        "close": raw_close, "pre_close": np.r_[np.nan, raw_close[:-1]],
        "vol": np.full(60, 1e6),
    })

    ctx, _, _ = _ctx_for(
        cal, None, None,
        sw_close={"801010.SI": {d: 100.0 for d in cal}},
        sw_open={"801010.SI": {d: 100.0 for d in cal}},
        industry={code: (np.array(["801010.SI"] * 60), np.array(["银行"] * 60))},
    )

    res = bc.process_symbol(code, df_code, cal, ctx["cal_arr"], ctx["cal_pos"],
                            ctx["st_intervals"], ctx["industry_pit"],
                            ctx["idx_snap_dates"], ctx["index_weights"],
                            ctx["sw_close"], ctx["sw_open"])
    assert res is not None
    (dates_out, st, swc, swn, i3, i5, ed, td, aed, ru,
     ep, xc, r_raw, r_sw, status) = res

    sig_mask = dates_out == cal[sig_i]
    assert sig_mask.sum() == 1
    i = int(np.where(sig_mask)[0][0])

    assert status[i] == "evaluated_ok"
    assert ed[i] == cal[entry_i] and aed[i] == cal[exit_i]
    # raw basis reports the fake -50% crash
    assert r_raw[i] == pytest.approx(10.0 / 20.0 - 1.0)

    # downstream adjusted math (mirrors assemble_year)
    adj_entry = adj_factor[entry_i]
    adj_exit = adj_factor[exit_i]
    r_adj = (xc[i] * adj_exit) / (ep[i] * adj_entry) - 1.0
    assert r_adj == pytest.approx(0.0)  # 10*2 / (20*1) - 1 = 0 -> split neutralised
    assert r_adj != pytest.approx(r_raw[i])


def test_missing_index_open_yields_nan_rsw():
    """sw_daily lacks the entry-date open -> r_sw must be NaN (missing)."""
    code = "600000.SH"
    cal = [f"2024{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(60)]
    sig_i, entry_i, exit_i = 5, 6, 45

    df_code = pd.DataFrame({
        "ts_code": code, "trade_date": np.array(cal),
        "open": np.full(60, 10.0), "high": np.full(60, 10.0),
        "low": np.full(60, 10.0), "close": np.full(60, 10.0),
        "pre_close": np.r_[np.nan, np.full(59, 10.0)],
        "vol": np.full(60, 1e6),
    })

    sw_close = {"801010.SI": {d: 100.0 for d in cal}}
    # open present everywhere EXCEPT the entry date
    sw_open = {"801010.SI": {d: 100.0 for d in cal if d != cal[entry_i]}}

    ctx, _, _ = _ctx_for(
        cal, None, None, sw_close=sw_close, sw_open=sw_open,
        industry={code: (np.array(["801010.SI"] * 60), np.array(["银行"] * 60))},
    )
    res = bc.process_symbol(code, df_code, cal, ctx["cal_arr"], ctx["cal_pos"],
                            ctx["st_intervals"], ctx["industry_pit"],
                            ctx["idx_snap_dates"], ctx["index_weights"],
                            ctx["sw_close"], ctx["sw_open"])
    assert res is not None
    (dates_out, *_rest, r_raw, r_sw, status) = res
    i = int(np.where(dates_out == cal[sig_i])[0][0])
    assert status[i] == "evaluated_ok"
    assert not np.isnan(r_raw[i])
    assert np.isnan(r_sw[i])          # missing open -> missing, not close fallback
