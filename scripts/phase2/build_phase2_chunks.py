#!/usr/bin/env python3
"""DAV-1547 — build year-partitioned (month on overflow) T+40 research parquet chunks.

Reads the phase2 cache (~/Documents/TradingAgents-AShare-cache/phase2, READ-ONLY)
and writes one row per (ts_code, signal trade_date) sample eligible under the
V-03a-aligned universe filter to data/phase2/chunks/year=YYYY[/month=MM]/data.parquet.

Design constraints implemented (per issue card):
- Chunked by signal year; if a year's row count exceeds --max-rows-per-partition
  the year is written as month partitions. Peak RSS guard: --max-rss-gb aborts.
- Column pruning: daily bars load only (ts_code, trade_date, open, high, low,
  close, pre_close, vol); daily_basic loads (ts_code, trade_date, pe_ttm, pb,
  total_mv, turnover_rate); adj_factor per-symbol files load only ADJ_COLS.
  The three consolidated *_all.pkl files are never opened.
- Interval marking without a signal x window merge: entry/exit dates and the
  suspension/roll logic are resolved with per-symbol np.searchsorted against
  that symbol's traded-date vector plus the global open-day calendar.
- Momentum / volatility via per-symbol rolling on a (ts_code, trade_date)-sorted
  frame — no date x market wide table is materialized.
- Universe: A-share ordinary stocks (drop .BJ / 北交所), PIT ST/*ST exclusion via
  namechange intervals, listing-age >= 60 trading days, entry-day tradability
  encoded in outcome_status. Delisted names included (survivorship-free).
- Resumable: a partition whose data.parquet + stat.json exist is skipped.

Label semantics (mirrors tradingagents/dataflows/return_labels.py, BUY leg):
- entry = first trading day after signal (T+1), filled at its open.
- T+1 bar absent on an open day + bilateral evidence (signal close>0 and T+2
  bar present with close>0 & vol>0) -> unexecutable_entry; bar absent without
  that evidence -> data_missing; bar present but zero-vol/zero-open or
  one-word limit-up -> unexecutable_entry.
- exit = first tradable close among target T+40 and up to 5 roll candidates;
  none -> suspension if post-window bar exists (bilateral evidence), else
  data_missing.
- limit prices derived as pre_close * (1 +/- limit_rate); rate = 5% while ST,
  else board default (10% main, 20% ChiNext/STAR, 30% BSE — BSE is filtered).
- r_stock = (exit_close*adj_exit)/(entry_open*adj_entry) - 1 on VENDOR-QFQ
  adjusted prices (primary basis; raw-price return kept as r_stock_raw).
  Rows without an adj_factor at entry or exit get r_stock=NaN, never a raw
  fallback. r_sw = SW L1 T+1 OPEN -> actual_exit close, i.e. the same holding
  window as the stock leg; missing index open -> r_sw=NaN.
  y_rel = 1[r_stock - r_sw > 0].

Usage:
    python scripts/phase2/build_phase2_chunks.py [--years 2019,2020] [--months M]
        [--out-dir data/phase2] [--force]
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path(os.environ.get("PHASE2_CACHE_DIR",
                                "~/Documents/TradingAgents-AShare-cache/phase2")).expanduser()
OUT_DIR_DEFAULT = Path("data/phase2")

EVAL_OFFSET = 40          # signal_idx + 40 -> target calendar date
MAX_ROLL = 5              # medium-horizon roll candidates (HORIZON_MAX_ROLL_DAYS)
MIN_LIST_AGE = 60         # trading days since list_date
MOM_WINDOW = 40           # momentum lookback (trading days ending at signal date)
VOL_WINDOW = 40           # volatility window (daily returns std)
LOOKBACK_PAD = 10         # extra lookback days buffer

LIMIT_RATE_MAIN = 0.10
LIMIT_RATE_GEM_STAR = 0.20
LIMIT_RATE_BSE = 0.30
LIMIT_RATE_ST = 0.05

DAILY_COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol"]
BASIC_COLS = ["ts_code", "trade_date", "pe_ttm", "pb", "total_mv", "turnover_rate"]
ADJ_COLS = ["ts_code", "trade_date", "adj_factor"]

OUT_COLUMNS = [
    "ts_code", "signal_date", "year", "month",
    "in_hs300", "in_zz500", "sw_l1_code", "sw_l1_name",
    "is_st", "list_age_days", "universe_ok",
    "entry_date", "target_exit_date", "actual_exit_date", "roll_days_used",
    "entry_open", "exit_close", "r_stock", "r_stock_raw", "r_sw", "r_rel", "y_rel",
    "outcome_status",
    "pe_ttm", "pb", "total_mv", "turnover_rate",
    "mom_40", "vol_40", "ret_1d",
    "adj_factor_entry", "adj_factor_exit",
]


def peak_rss_gb() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF)
    val = ru.ru_maxrss
    # macOS reports bytes, Linux reports KB
    return val / (1024 ** 3) if val > 10 ** 6 else val / (1024 ** 2)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Cache loading helpers (read-only)
# ---------------------------------------------------------------------------

def load_calendar(cache: Path) -> list[str]:
    api = cache / "api_cache"
    cals = sorted(api.glob("trade_cal_*.pkl"))
    if not cals:
        raise FileNotFoundError("trade_cal pkl missing in api_cache")
    df = pd.read_pickle(cals[0])
    return sorted(df.loc[df["is_open"] == 1, "cal_date"].tolist())


def load_index_member(cache: Path) -> pd.DataFrame:
    d = cache / "index_member_all"
    frames = [pd.read_pickle(f) for f in sorted(d.glob("*.pkl"))]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["ts_code", "l1_code", "l1_name", "in_date", "out_date", "is_new"])


def load_daily_dir(cache: Path, sub: str, cols: list[str], dates: list[str]) -> pd.DataFrame:
    frames = []
    d = cache / sub
    for dt in dates:
        f = d / f"{dt[:4]}-{dt[4:6]}-{dt[6:]}.pkl"
        if f.exists():
            df = pd.read_pickle(f)
            frames.append(df[[c for c in cols if c in df.columns]])
    if not frames:
        return pd.DataFrame(columns=cols)
    return pd.concat(frames, ignore_index=True)


def load_adj_for_symbols(cache: Path, symbols, dates_lo: str, dates_hi: str) -> pd.DataFrame:
    d = cache / "adj_factor"
    frames = []
    for s in symbols:
        f = d / f"{s}.pkl"
        if not f.exists():
            continue
        df = pd.read_pickle(f)
        df = df.loc[(df["trade_date"] >= dates_lo) & (df["trade_date"] <= dates_hi), ADJ_COLS]
        if len(df):
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=ADJ_COLS)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Static reference structures
# ---------------------------------------------------------------------------

def build_st_intervals(namechange: pd.DataFrame) -> dict[str, list[tuple[str, str]]]:
    nc = namechange.copy()
    nc["is_st_name"] = nc["name"].str.contains("ST", na=False)
    out: dict[str, list[tuple[str, str]]] = {}
    for row in nc.loc[nc["is_st_name"]].itertuples():
        start = row.start_date if isinstance(row.start_date, str) and row.start_date else "00000000"
        end = row.end_date if isinstance(row.end_date, str) and row.end_date else "99999999"
        out.setdefault(row.ts_code, []).append((start, end))
    return out


def st_flag_for_dates(code: str, dates: np.ndarray, intervals: dict) -> np.ndarray:
    iv = intervals.get(code)
    if not iv:
        return np.zeros(len(dates), dtype=bool)
    flag = np.zeros(len(dates), dtype=bool)
    for s, e in iv:
        flag |= (dates >= s) & (dates <= e)
    return flag


def pit_industry(member: pd.DataFrame, cal_days: list[str]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """ts_code -> (l1_code_per_calday, l1_name_per_calday), indexed by calendar position."""
    cal = np.array(cal_days)
    result = {}
    for code, sub in member.groupby("ts_code"):
        l1 = np.full(len(cal), "", dtype=object)
        nm = np.full(len(cal), "", dtype=object)
        for r in sub.itertuples():
            lo = r.in_date if isinstance(r.in_date, str) and r.in_date else "00000000"
            hi = r.out_date if isinstance(r.out_date, str) and r.out_date else "99999999"
            mask = (cal >= lo) & (cal < hi)
            l1[mask] = r.l1_code
            nm[mask] = r.l1_name
        result[code] = (l1, nm)
    return result


def limit_rate_for(code: str, st_flags: np.ndarray) -> np.ndarray:
    if code.endswith(".BJ"):
        base = LIMIT_RATE_BSE
    elif code[:3] in ("300", "301", "302") or code[:3] in ("688", "689"):
        base = LIMIT_RATE_GEM_STAR
    else:
        base = LIMIT_RATE_MAIN
    rates = np.full(len(st_flags), base)
    rates[st_flags] = LIMIT_RATE_ST
    return rates


def index_member_lut(snap: dict[str, set[str]], code: str, snap_idx: np.ndarray) -> np.ndarray:
    """snap_idx: per-row index into sorted snapshot dates (-1 = before first).
    Returns bool array; uses a per-symbol LUT over ~200 unique snapshots."""
    flag = np.zeros(len(snap_idx), dtype=bool)
    if not snap:
        return flag
    snap_dates = np.array(sorted(snap.keys()))
    valid = snap_idx >= 0
    if not valid.any():
        return flag
    uniq = np.unique(snap_idx[valid])
    lut = {u: (code in snap[snap_dates[u]]) for u in uniq}
    idx = snap_idx[valid]
    flag[valid] = np.fromiter((lut[i] for i in idx), dtype=bool, count=len(idx))
    return flag


def adj_factor_at(adj_map: dict, codes: np.ndarray, dates: np.ndarray) -> np.ndarray:
    """Latest adj_factor at-or-before each (code,date); NaN when no factor exists."""
    out = np.full(len(dates), np.nan)
    order = np.argsort(codes, kind="stable")
    codes_s = codes[order]
    bounds = np.flatnonzero(np.r_[True, codes_s[1:] != codes_s[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        code = codes_s[a]
        sub = adj_map.get(code)
        if sub is None:
            continue
        idx = order[a:b]
        dvals = dates[idx]
        valid = np.array([isinstance(x, str) and len(x) == 8 and x != "" for x in dvals])
        if not valid.any():
            continue
        dd = sub["trade_date"].to_numpy()
        ff = sub["adj_factor"].to_numpy(dtype=float)
        dv = np.where(valid, dvals.astype(object), "99999999")
        pos = np.clip(np.searchsorted(dd, dv, side="right") - 1, 0, len(dd) - 1)
        out[idx] = np.where(valid, ff[pos], np.nan)
    return out


# ---------------------------------------------------------------------------
# Per-symbol window resolution
# ---------------------------------------------------------------------------

def process_symbol(code: str,
                   df_code: pd.DataFrame,
                   cal_days: list[str],
                   cal_arr: np.ndarray,
                   cal_pos: dict[str, int],
                   st_intervals: dict,
                   industry_pit: dict,
                   idx_snap_dates: dict,
                   index_weights: dict,
                   sw_close: dict,
                   sw_open: dict) -> tuple | None:
    """Vectorized T+40 label resolution for one symbol.

    df_code must be date-sorted and contain lookback + signal rows.
    Returns a tuple of arrays aligned to df_code rows (signal filtering is
    done by the caller), or None if unusable.
    """
    if df_code.empty:
        return None

    dates = df_code["trade_date"].to_numpy()
    n = len(dates)
    open_p = df_code["open"].to_numpy(dtype=float)
    high_p = df_code["high"].to_numpy(dtype=float)
    low_p = df_code["low"].to_numpy(dtype=float)
    close_p = df_code["close"].to_numpy(dtype=float)
    pre_close = df_code["pre_close"].to_numpy(dtype=float)
    vol = df_code["vol"].to_numpy(dtype=float)

    sig_pos = np.array([cal_pos[d] for d in dates], dtype=np.int64)
    n_cal = len(cal_days)
    entry_pos = sig_pos + 1
    target_pos = sig_pos + EVAL_OFFSET
    valid_horizon = (target_pos < n_cal)

    dates_set = set(dates.tolist())

    def _bar_idx(date_arr):
        """Position in this symbol's date vector for each calendar date (-1 if no bar)."""
        pos = np.searchsorted(dates, date_arr)
        pos_c = np.clip(pos, 0, n - 1)
        return np.where((pos < n) & (dates[pos_c] == date_arr), pos, -1)

    entry_dates = cal_arr[np.clip(entry_pos, 0, n_cal - 1)]
    entry_j = _bar_idx(entry_dates)
    has_entry_bar = entry_j >= 0

    t2_dates = cal_arr[np.clip(sig_pos + 2, 0, n_cal - 1)]
    t2_j = _bar_idx(t2_dates)
    has_t2 = t2_j >= 0
    t2_ok = np.zeros(n, dtype=bool)
    t2_ok[has_t2] = (close_p[t2_j[has_t2]] > 0) & (vol[t2_j[has_t2]] > 0)
    bilateral_entry = (close_p > 0) & has_t2 & t2_ok

    entry_open = np.full(n, np.nan)
    entry_vol = np.zeros(n)
    entry_high = np.full(n, np.nan)
    entry_low = np.full(n, np.nan)
    entry_pre = np.full(n, np.nan)
    entry_open[has_entry_bar] = open_p[entry_j[has_entry_bar]]
    entry_vol[has_entry_bar] = vol[entry_j[has_entry_bar]]
    entry_high[has_entry_bar] = high_p[entry_j[has_entry_bar]]
    entry_low[has_entry_bar] = low_p[entry_j[has_entry_bar]]
    entry_pre[has_entry_bar] = pre_close[entry_j[has_entry_bar]]

    st_flags = st_flag_for_dates(code, dates, st_intervals)
    limit_rates = limit_rate_for(code, st_flags)

    entry_lup = entry_pre * (1 + limit_rates)
    oneword_up = (has_entry_bar & ~np.isnan(entry_lup)
                  & (entry_open >= entry_lup)
                  & (entry_high == entry_low)
                  & (entry_high == entry_lup))

    unexec_nobar = ~has_entry_bar & bilateral_entry
    unexec_bar = has_entry_bar & ((entry_vol <= 0) | (entry_open <= 0) | oneword_up)
    data_missing_entry = ~has_entry_bar & ~bilateral_entry
    entry_ok = has_entry_bar & (entry_vol > 0) & (entry_open > 0) & ~oneword_up
    entry_price = np.where(entry_ok, entry_open, np.nan)

    # exit: per-row scan of target + up to MAX_ROLL candidates (bounded: <=6)
    actual_exit_date = np.array([""] * n, dtype=object)
    exit_close = np.full(n, np.nan)
    roll_used = np.zeros(n, dtype=np.int64)
    cand_idx = np.stack([_bar_idx(cal_arr[np.clip(target_pos + r, 0, n_cal - 1)])
                         for r in range(MAX_ROLL + 1)], axis=1)  # n x (R+1)
    for i in np.where(entry_ok & valid_horizon)[0]:
        for r in range(MAX_ROLL + 1):
            p = target_pos[i] + r
            if p >= n_cal:
                break
            j = cand_idx[i, r]
            if j < 0:
                continue  # suspended/missing on that calendar day -> roll
            if vol[j] <= 0 or close_p[j] <= 0:
                continue
            ldown = pre_close[j] * (1 - limit_rates[i])
            if open_p[j] <= ldown and high_p[j] == low_p[j] == ldown:
                continue  # one-word limit-down: cannot sell
            actual_exit_date[i] = cal_arr[p]
            exit_close[i] = close_p[j]
            roll_used[i] = r
            break

    has_exit = exit_close > 0

    after_pos = np.clip(target_pos + MAX_ROLL + 1, 0, n_cal - 1)
    after_j = _bar_idx(cal_arr[after_pos])
    after_ok = np.zeros(n, dtype=bool)
    m = (after_j >= 0) & (target_pos + MAX_ROLL + 1 < n_cal)
    after_ok[m] = (close_p[after_j[m]] > 0) & (vol[after_j[m]] > 0)

    status = np.array(["pending_due"] * n, dtype=object)
    mvh = valid_horizon
    status[mvh & data_missing_entry] = "data_missing"
    status[mvh & (unexec_nobar | unexec_bar)] = "unexecutable_entry"
    status[mvh & entry_ok & has_exit] = "evaluated_ok"
    status[mvh & entry_ok & ~has_exit & after_ok] = "suspension"
    status[mvh & entry_ok & ~has_exit & ~after_ok] = "data_missing"

    r_stock_raw = np.where(has_exit & entry_ok, exit_close / entry_price - 1.0, np.nan)

    # PIT industry + SW index return over [entry_date -> actual_exit_date]
    # (same window as the stock leg; index leg uses T+1 OPEN like the stock).
    sw_code = np.array([""] * n, dtype=object)
    sw_name = np.array([""] * n, dtype=object)
    r_sw = np.full(n, np.nan)
    ind = industry_pit.get(code)
    if ind is not None:
        l1_arr, nm_arr = ind
        okp = sig_pos < len(l1_arr)
        sw_code[okp] = l1_arr[sig_pos[okp]]
        sw_name[okp] = nm_arr[sig_pos[okp]]
        rows = np.where(has_exit & entry_ok & (sw_code != "") & has_entry_bar)[0]
        for i in rows:
            sd = sw_close.get(sw_code[i])
            so = sw_open.get(sw_code[i])
            if sd is None or so is None:
                continue
            s0 = so.get(entry_dates[i])   # SW index open at the stock's entry date
            s1 = sd.get(actual_exit_date[i])
            if s0 and s1:
                r_sw[i] = s1 / s0 - 1.0

    snap_dates300 = idx_snap_dates["hs300"]
    snap_dates500 = idx_snap_dates["zz500"]
    in300 = index_member_lut(index_weights["hs300"], code,
                             np.searchsorted(snap_dates300, dates, side="right") - 1)
    in500 = index_member_lut(index_weights["zz500"], code,
                             np.searchsorted(snap_dates500, dates, side="right") - 1)

    target_dates = cal_arr[np.clip(target_pos, 0, n_cal - 1)]

    # r_rel / y_rel are computed downstream in assemble_year AFTER the
    # adjusted (vendor_qfq) r_stock is derived — never from raw here.
    return (dates, st_flags, sw_code, sw_name, in300, in500,
            entry_dates, target_dates, actual_exit_date, roll_used,
            entry_price, exit_close, r_stock_raw, r_sw, status)


# ---------------------------------------------------------------------------
# Chunk assembly
# ---------------------------------------------------------------------------

def assemble_year(year: int, months: list[int], args, ctx) -> list[dict]:
    cal_days, cal_arr, cal_pos = ctx["cal_days"], ctx["cal_arr"], ctx["cal_pos"]

    if months:
        year_dates = [d for d in cal_days
                      if int(d[:4]) == year and int(d[4:6]) in months]
    else:
        year_dates = [d for d in cal_days if int(d[:4]) == year]
    if not year_dates:
        return []

    first_sig_idx = cal_pos[year_dates[0]]
    last_sig_idx = cal_pos[year_dates[-1]]
    lb_start = cal_days[max(0, first_sig_idx - MOM_WINDOW - LOOKBACK_PAD)]
    # bars must cover the full exit window of the LAST signal (T+40 + roll + 1)
    # otherwise exits fall off the loaded frame -> spurious data_missing.
    fwd_end_idx = min(last_sig_idx + EVAL_OFFSET + MAX_ROLL + 1, len(cal_days) - 1)
    fwd_end = cal_days[fwd_end_idx]
    load_dates = [d for d in cal_days if lb_start <= d <= fwd_end]

    log(f"  {year} months={months or 'all'}: loading {len(load_dates)} daily files "
        f"(signals {year_dates[0]}..{year_dates[-1]}, lookback {lb_start}, "
        f"exit coverage through {fwd_end})")
    daily = load_daily_dir(CACHE_DIR, "daily_by_day", DAILY_COLS, load_dates)
    basic = load_daily_dir(CACHE_DIR, "daily_basic_by_day", BASIC_COLS, year_dates)
    log(f"  daily rows={len(daily):,} basic rows={len(basic):,} rss={peak_rss_gb():.2f}GB")
    if daily.empty:
        return []

    daily = daily.sort_values(["ts_code", "trade_date"], kind="mergesort").reset_index(drop=True)

    # per-symbol rolling features on the sorted frame (vectorized groupby paths)
    codes_arr = daily["ts_code"].to_numpy()
    grp = daily.groupby("ts_code", sort=False)
    daily["ret_1d"] = grp["close"].pct_change()
    daily["mom_40"] = daily["close"] / grp["close"].shift(MOM_WINDOW) - 1.0
    daily["vol_40"] = grp["ret_1d"].rolling(VOL_WINDOW).std().reset_index(level=0, drop=True)

    signal_set = set(year_dates)
    is_signal = daily["trade_date"].isin(signal_set).to_numpy()
    sig_codes = pd.unique(daily.loc[is_signal, "ts_code"]).tolist()
    log(f"  symbols with signals: {len(sig_codes)}")

    # slice map: contiguous (ts_code) blocks after stable sort
    bounds = np.flatnonzero(np.r_[True, codes_arr[1:] != codes_arr[:-1], True])
    code_slice = {codes_arr[a]: slice(int(a), int(b))
                  for a, b in zip(bounds[:-1], bounds[1:])}

    basic_groups = dict(tuple(basic.groupby("ts_code"))) if not basic.empty else {}

    frames: list[pd.DataFrame] = []
    for k, code in enumerate(sig_codes):
        sl = code_slice.get(code)
        if sl is None:
            continue
        df_code = daily.iloc[sl]
        res = process_symbol(code, df_code, cal_days, cal_arr, cal_pos,
                             ctx["st_intervals"], ctx["industry_pit"],
                             ctx["idx_snap_dates"], ctx["index_weights"],
                             ctx["sw_close"], ctx["sw_open"])
        if res is None:
            continue
        (dates, st_flags, sw_code, sw_name, in300, in500,
         entry_dates, target_dates, actual_exit_date, roll_used,
         entry_price, exit_close, r_stock_raw, r_sw, status) = res

        sig_mask = np.array([d in signal_set for d in dates])
        idx = np.where(sig_mask)[0]
        if not len(idx):
            continue

        frame = pd.DataFrame({
            "ts_code": code,
            "signal_date": dates[idx],
            "in_hs300": in300[idx],
            "in_zz500": in500[idx],
            "sw_l1_code": sw_code[idx],
            "sw_l1_name": sw_name[idx],
            "is_st": st_flags[idx],
            "entry_date": entry_dates[idx],
            "target_exit_date": target_dates[idx],
            "actual_exit_date": actual_exit_date[idx],
            "roll_days_used": roll_used[idx],
            "entry_open": entry_price[idx],
            "exit_close": exit_close[idx],
            "r_stock_raw": r_stock_raw[idx],
            "r_sw": r_sw[idx],
            "outcome_status": status[idx],
        })
        frame["year"] = year
        frame["month"] = frame["signal_date"].str[4:6].astype(int)

        ld = ctx["list_date_map"].get(code)
        if isinstance(ld, str) and ld:
            sig_idx = np.searchsorted(cal_arr, dates[idx])
            if ld < cal_days[0]:
                # list predates cached calendar: in-window trading days plus a
                # weekday-count estimate of the pre-window segment. Overestimates
                # true trading days by the holiday count -> the >=60 filter is
                # slightly looser for stocks listed in the ~3 months before the
                # window (documented limitation, bias direction: admits a few
                # marginally-new stocks in late 2015 only).
                prior = np.busday_count(ld, cal_days[0])
                frame["list_age_days"] = sig_idx + prior
            else:
                frame["list_age_days"] = sig_idx - np.searchsorted(cal_arr, ld)
        else:
            frame["list_age_days"] = np.nan
        frame["universe_ok"] = (
            ~frame["is_st"]
            & (frame["list_age_days"] >= MIN_LIST_AGE)
            & (not code.endswith(".BJ"))
            & (ctx["market_map"].get(code, "") != "北交所")
        )

        bsub = basic_groups.get(code)
        if bsub is not None and not bsub.empty:
            bmap = bsub.set_index("trade_date")
            for col in ("pe_ttm", "pb", "total_mv", "turnover_rate"):
                frame[col] = frame["signal_date"].map(bmap[col])
        else:
            for col in ("pe_ttm", "pb", "total_mv", "turnover_rate"):
                frame[col] = np.nan

        fcols = df_code.loc[df_code["trade_date"].isin(signal_set),
                            ["trade_date", "mom_40", "vol_40", "ret_1d"]]
        frame = frame.merge(fcols, left_on="signal_date", right_on="trade_date",
                            how="left").drop(columns=["trade_date"])
        frames.append(frame)

        if (k + 1) % 1000 == 0:
            log(f"    {k + 1}/{len(sig_codes)} symbols rss={peak_rss_gb():.2f}GB")

    if not frames:
        return []
    allf = pd.concat(frames, ignore_index=True)
    del frames, daily, basic
    gc.collect()

    # adj factors at entry & exit dates (for downstream PIT-adjusted analysis)
    need_lo = allf["entry_date"].min()
    need_hi = allf["actual_exit_date"].replace("", "99999999").max()
    adj = load_adj_for_symbols(CACHE_DIR, sig_codes, need_lo, need_hi)
    if not adj.empty:
        adj = adj.sort_values(["ts_code", "trade_date"])
        adj_map = {s: sub for s, sub in adj.groupby("ts_code")}
        allf["adj_factor_entry"] = adj_factor_at(
            adj_map, allf["ts_code"].to_numpy(), allf["entry_date"].to_numpy())
        exit_dates = allf["actual_exit_date"].to_numpy()
        allf["adj_factor_exit"] = adj_factor_at(
            adj_map, allf["ts_code"].to_numpy(), exit_dates)
    else:
        allf["adj_factor_entry"] = np.nan
        allf["adj_factor_exit"] = np.nan

    # Primary return basis = vendor_qfq: scale entry open and exit close by the
    # adj_factor valid at each date, then take the ratio. Rows without both
    # factors stay NaN — no raw-price fallback (raw kept in r_stock_raw only).
    entry_adj = allf["entry_open"] * allf["adj_factor_entry"]
    exit_adj = allf["exit_close"] * allf["adj_factor_exit"]
    allf["r_stock"] = np.where(
        (allf["outcome_status"] == "evaluated_ok")
        & entry_adj.notna() & exit_adj.notna() & (entry_adj > 0),
        exit_adj / entry_adj - 1.0, np.nan)
    allf["r_rel"] = np.where(allf["r_stock"].notna() & allf["r_sw"].notna(),
                             allf["r_stock"] - allf["r_sw"], np.nan)
    allf["y_rel"] = np.where(allf["r_rel"].notna(),
                             (allf["r_rel"] > 0).astype(float), np.nan)

    stats = []
    if months:
        groups = [(m, allf.loc[allf["month"] == m]) for m in months]
    elif len(allf) > args.max_rows_per_partition:
        groups = list(allf.groupby("month"))
        log(f"  year={year} rows={len(allf):,} > {args.max_rows_per_partition:,} -> month partitions")
    else:
        groups = [(None, allf)]

    for m, df_part in groups:
        if df_part is None or df_part.empty:
            continue
        stats.append(write_partition(df_part, year, m, args))
    return stats


def write_partition(df: pd.DataFrame, year: int, month, args) -> dict:
    df = df.reindex(columns=OUT_COLUMNS)
    if month is None:
        part_dir = Path(args.out_dir) / "chunks" / f"year={year}"
        tag = f"year={year}"
    else:
        part_dir = Path(args.out_dir) / "chunks" / f"year={year}" / f"month={int(month):02d}"
        tag = f"year={year}/month={int(month):02d}"
    part_dir.mkdir(parents=True, exist_ok=True)
    pq = part_dir / "data.parquet"
    df.to_parquet(pq, index=False, engine="pyarrow", compression="zstd")
    oc = df["outcome_status"]
    stat = {
        "partition": tag,
        "rows": len(df),
        "symbols": int(df["ts_code"].nunique()),
        "signal_date_min": str(df["signal_date"].min()),
        "signal_date_max": str(df["signal_date"].max()),
        "evaluated_ok": int((oc == "evaluated_ok").sum()),
        "unexecutable_entry": int((oc == "unexecutable_entry").sum()),
        "suspension": int((oc == "suspension").sum()),
        "data_missing": int((oc == "data_missing").sum()),
        "pending_due": int((oc == "pending_due").sum()),
        "y_rel_nonnull": int(df["y_rel"].notna().sum()),
        "universe_ok_rows": int(df["universe_ok"].sum()),
        "parquet": str(pq),
        "peak_rss_gb": round(peak_rss_gb(), 3),
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (part_dir / "stat.json").write_text(json.dumps(stat, ensure_ascii=False, indent=1))
    log(f"  wrote {tag}: rows={stat['rows']:,} symbols={stat['symbols']} "
        f"ok={stat['evaluated_ok']:,} rss={stat['peak_rss_gb']}GB")
    return stat


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT))
    ap.add_argument("--years", default="", help="comma list; default all calendar years")
    ap.add_argument("--months", default="", help="comma list; restrict to these months")
    ap.add_argument("--max-rows-per-partition", type=int, default=1_500_000)
    ap.add_argument("--max-rss-gb", type=float, default=6.0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    out_root = Path(args.out_dir)
    (out_root / "chunks").mkdir(parents=True, exist_ok=True)

    log(f"cache={CACHE_DIR} out={out_root}")
    cal_days = load_calendar(CACHE_DIR)
    cal_arr = np.array(cal_days)
    cal_pos = {d: i for i, d in enumerate(cal_days)}
    log(f"calendar: {len(cal_days)} open days {cal_days[0]}..{cal_days[-1]}")

    stock_basic = pd.read_pickle(CACHE_DIR / "stock_basic.pkl")
    list_date_map = dict(zip(stock_basic["ts_code"], stock_basic["list_date"]))
    market_map = dict(zip(stock_basic["ts_code"], stock_basic["market"]))
    st_intervals = build_st_intervals(pd.read_pickle(CACHE_DIR / "namechange.pkl"))
    industry_pit = pit_industry(load_index_member(CACHE_DIR), cal_days)
    sw = pd.read_pickle(CACHE_DIR / "sw_daily.pkl")
    sw_close = {c: dict(zip(s["trade_date"], s["close"])) for c, s in sw.groupby("ts_code")}
    sw_open = {c: dict(zip(s["trade_date"], s["open"])) for c, s in sw.groupby("ts_code")}
    w300 = pd.read_pickle(CACHE_DIR / "index_weight_000300.SH.pkl")
    w500 = pd.read_pickle(CACHE_DIR / "index_weight_000905.SH.pkl")
    index_weights = {
        "hs300": {d: set(g["con_code"]) for d, g in w300.groupby("trade_date")},
        "zz500": {d: set(g["con_code"]) for d, g in w500.groupby("trade_date")},
    }
    idx_snap_dates = {"hs300": np.array(sorted(index_weights["hs300"].keys())),
                      "zz500": np.array(sorted(index_weights["zz500"].keys()))}
    log(f"static refs loaded rss={peak_rss_gb():.2f}GB")

    ctx = dict(cal_days=cal_days, cal_arr=cal_arr, cal_pos=cal_pos,
               st_intervals=st_intervals, industry_pit=industry_pit,
               index_weights=index_weights, idx_snap_dates=idx_snap_dates,
               sw_close=sw_close, sw_open=sw_open,
               list_date_map=list_date_map, market_map=market_map)

    years = ([int(y) for y in args.years.split(",") if y] if args.years
             else sorted({int(d[:4]) for d in cal_days}))
    months = [int(m) for m in args.months.split(",") if m] if args.months else []

    stats_path = out_root / "stats.json"
    if stats_path.exists() and not args.force:
        all_stats = json.loads(stats_path.read_text())
    else:
        all_stats = {"partitions": [], "build_started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "calendar_days": len(cal_days)}
    all_stats.setdefault("partitions", [])
    done = {p["partition"] for p in all_stats["partitions"]}
    existing = {p["partition"] for p in all_stats["partitions"]}

    def existing_partition_tags(y: int) -> set[str]:
        base = Path(args.out_dir) / "chunks"
        tags = set()
        if (base / f"year={y}" / "data.parquet").exists():
            tags.add(f"year={y}")
        for d in (base / f"year={y}").glob("month=*/data.parquet") if (base / f"year={y}").exists() else []:
            tags.add(f"year={y}/month={d.parent.name.split('=')[1]}")
        return tags

    for year in years:
        have = existing_partition_tags(year)
        if months:
            want = {f"year={year}/month={m:02d}" for m in months}
            todo = want - have
            if not todo and not args.force:
                log(f"skip {year} months={months} (done)")
                continue
            run_months = months if args.force else sorted({int(t.split("month=")[1]) for t in todo})
        else:
            if have and not args.force:
                log(f"skip year={year} (existing partitions: {sorted(have)})")
                continue
            run_months = []
        try:
            stats = assemble_year(year, run_months, args, ctx)
        except MemoryError:
            log(f"year={year} MemoryError -> retry per-month")
            gc.collect()
            stats = []
            for m in sorted({int(d[4:6]) for d in cal_days if int(d[:4]) == year}):
                stats.extend(assemble_year(year, [m], args, ctx))
        if peak_rss_gb() > args.max_rss_gb:
            log(f"ABORT: peak rss {peak_rss_gb():.2f}GB > {args.max_rss_gb}GB")
            return 2
        for s in stats:
            if s["partition"] in existing:
                all_stats["partitions"] = [p for p in all_stats["partitions"]
                                           if p["partition"] != s["partition"]]
            all_stats["partitions"].append(s)
            existing.add(s["partition"])
        all_stats["elapsed_s"] = round(time.time() - t0, 1)
        all_stats["peak_rss_gb"] = round(peak_rss_gb(), 3)
        stats_path.write_text(json.dumps(all_stats, ensure_ascii=False, indent=1))
        gc.collect()

    all_stats["build_finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    all_stats["peak_rss_gb"] = round(peak_rss_gb(), 3)
    stats_path.write_text(json.dumps(all_stats, ensure_ascii=False, indent=1))
    log(f"done in {time.time() - t0:.0f}s peak_rss={peak_rss_gb():.2f}GB "
        f"partitions={len(all_stats['partitions'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
