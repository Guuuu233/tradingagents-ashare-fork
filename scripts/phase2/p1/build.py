"""Build universe, labels, and factor panel for P1 study (DAV-1478).

Label definition (prediction basis, per P2 ruling):
  entry   = open  at T+1 (trading_days[signal_idx+1])
  exit    = close at T+40 (trading_days[signal_idx+40]); no execution roll
  r_stock = P_exit / P_entry - 1   (vendor_qfq adjusted)
  r_rel   = r_stock - r_SW          (same-window SW L1 index return)
  y_rel   = 1[r_rel > 0]

Typed missing follows OutcomeStatus semantics:
  pending_due / suspension / unexecutable_entry / data_missing / provider_failure
All computation vectorised via pandas merge on (ts_code, trade_date) triples.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


_BJ_RE = re.compile(r"^(8|4|920)\d{3,}\.BJ$|\.BJ$")


def is_bj(symbol: str) -> bool:
    return bool(_BJ_RE.match(symbol))


# ---------------------------------------------------------------------------
# Universe / PIT filters
# ---------------------------------------------------------------------------

def build_stock_meta(stock_basic: pd.DataFrame) -> pd.DataFrame:
    df = stock_basic.copy()
    df = df[df["ts_code"].str.endswith((".SH", ".SZ"))]
    df = df[~df["ts_code"].apply(is_bj)]
    df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
    df["delist_date"] = pd.to_datetime(df["delist_date"], errors="coerce")
    return df.reset_index(drop=True)


def build_st_lookup(namechange: pd.DataFrame) -> Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp]]]:
    nc = namechange.copy()
    nc["is_st_name"] = nc["name"].str.contains("ST", case=False, na=False)
    st = nc[nc["is_st_name"]].copy()
    st["start"] = pd.to_datetime(st["start_date"], errors="coerce")
    st["end"] = pd.to_datetime(st["end_date"], errors="coerce")
    lut: Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp]]] = {}
    for sym, sub in st.groupby("ts_code"):
        lut[sym] = [(r["start"], r["end"]) for _, r in sub.iterrows()]
    return lut


def st_flag_frame(st_lut) -> pd.DataFrame:
    """Expand ST lut into an interval table for vectorised merge."""
    rows = [(s, i[0], i[1]) for s, lst in st_lut.items() for i in lst]
    if not rows:
        return pd.DataFrame(columns=["ts_code", "st_start", "st_end"])
    df = pd.DataFrame(rows, columns=["ts_code", "st_start", "st_end"])
    df["st_end"] = df["st_end"].fillna(pd.Timestamp("2262-01-01"))
    return df


def membership_frame(members: pd.DataFrame) -> pd.DataFrame:
    m = members.copy()
    m["in_d"] = pd.to_datetime(m["in_date"], errors="coerce")
    m["out_d"] = pd.to_datetime(m["out_date"], errors="coerce").fillna(pd.Timestamp("2262-01-01"))
    return m[["ts_code", "l1_code", "l1_name", "in_d", "out_d"]].dropna(subset=["in_d"])


def _interval_join(sd: pd.DataFrame, iv: pd.DataFrame, iv_cols) -> pd.DataFrame:
    """Join (ts_code, day_ts) rows to interval rows containing that day.

    Returns a DataFrame aligned to sd with one row per (row, matching interval).
    Caller de-duplicates ambiguous multi-interval hits.
    """
    a = sd.reset_index(drop=True).copy()
    a["_row"] = a.index
    b = a.merge(iv, on="ts_code", how="left")
    mask = (b["in_d"] <= b["day_ts"]) & (b["day_ts"] < b["out_d"])
    return b[mask].copy()


def build_index_weight_lut(index_weight: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    return {c: s.sort_values("trade_date").reset_index(drop=True) for c, s in index_weight.groupby("index_code")}


def index_members_at(iw_df: pd.DataFrame, day_str: str) -> set:
    sub = iw_df[iw_df["trade_date"] <= day_str]
    if sub.empty:
        return set()
    return set(sub.loc[sub["trade_date"] == sub["trade_date"].max(), "con_code"])


# ---------------------------------------------------------------------------
# Labels (vectorised via merge)
# ---------------------------------------------------------------------------

def build_label_frame(
    daily: pd.DataFrame,
    trading_days: List[str],
    sw_close: pd.DataFrame,   # index trade_date, columns l1_code -> close
    adj_df: pd.DataFrame,     # ts_code, trade_date, adj_factor
    member_iv: pd.DataFrame,  # interval table from membership_frame()
    st_iv: pd.DataFrame,      # interval table from st_flag_frame()
    stock_meta: pd.DataFrame,
    horizon: int = 40,
) -> pd.DataFrame:
    """Per (ts_code, signal_date) labels incl. typed missing (never dropped)."""
    idx = {d: i for i, d in enumerate(trading_days)}
    n = len(trading_days)
    signal_days = trading_days[: n - horizon - 1]

    daily = daily.copy()
    daily["trade_date"] = daily["trade_date"].astype(str)

    # universe of (symbol, signal_day): every stock trading on signal day
    # plus stocks that delisted but existed then -> use all symbols present any day
    uni = daily[["ts_code", "trade_date"]].copy()
    uni = uni[uni["trade_date"].isin(signal_days)]
    uni["i"] = uni["trade_date"].map(idx)
    uni["t1"] = uni["i"].map(lambda k: trading_days[k + 1])
    uni["t40"] = uni["i"].map(lambda k: trading_days[k + horizon])

    # join entry bar (T+1) and exit bar (T+40)
    e = daily.rename(columns={
        "trade_date": "t1", "open": "e_open", "high": "e_high",
        "low": "e_low", "close": "e_close", "vol": "e_vol",
    })[["ts_code", "t1", "e_open", "e_high", "e_low", "e_close", "e_vol"]]
    uni = uni.merge(e, on=["ts_code", "t1"], how="left")

    x = daily.rename(columns={
        "trade_date": "t40", "close": "x_close",
    })[["ts_code", "t40", "x_close"]]
    uni = uni.merge(x, on=["ts_code", "t40"], how="left")

    # adj factors at T, T+1, T+40
    for tag, col in (("t", "trade_date"), ("e", "t1"), ("x", "t40")):
        a = adj_df.rename(columns={"trade_date": col, "adj_factor": f"a_{tag}"})
        uni = uni.merge(a[["ts_code", col, f"a_{tag}"]], on=["ts_code", col], how="left")

    # SW L1 via interval join (in_d <= day < out_d); ambiguous -> missing
    uni["day_ts"] = pd.to_datetime(uni["trade_date"])
    m = _interval_join(uni[["ts_code", "day_ts"]], member_iv, None)
    amb = m.groupby("_row")["l1_code"].nunique()
    m = m.sort_values("_row").drop_duplicates("_row", keep="first")
    uni["_row"] = uni.index
    uni = uni.merge(m[["_row", "l1_code", "l1_name"]], on="_row", how="left")
    uni["l1_amb"] = uni["_row"].map(amb).fillna(0)
    uni.loc[uni["l1_amb"] > 1, "l1_code"] = None

    # sw close at T+1 / T+40 per l1 (wide -> long merge)
    sw_long = sw_close.copy()
    sw_long.index = sw_long.index.astype(str)
    sw_long = sw_long.reset_index().rename(columns={sw_close.index.name or "index": "d"})
    sw_e = sw_long.melt(id_vars="d", var_name="l1_code", value_name="sw_e").rename(columns={"d": "t1"})
    sw_x = sw_long.melt(id_vars="d", var_name="l1_code", value_name="sw_x").rename(columns={"d": "t40"})
    uni = uni.merge(sw_e, on=["l1_code", "t1"], how="left")
    uni = uni.merge(sw_x, on=["l1_code", "t40"], how="left")

    # outcome + returns
    def _status(r):
        if pd.isna(r["e_open"]) or r["e_open"] <= 0:
            return "data_missing"
        if pd.notna(r["e_vol"]) and r["e_vol"] == 0:
            return "unexecutable_entry"
        if pd.notna(r["e_high"]) and pd.notna(r["e_low"]) and r["e_high"] == r["e_low"] == r["e_open"]:
            return "unexecutable_entry"
        if pd.isna(r["x_close"]) or r["x_close"] <= 0:
            return "data_missing"
        if pd.isna(r["l1_code"]):
            return "data_missing"
        if pd.isna(r["sw_e"]) or pd.isna(r["sw_x"]):
            return "provider_failure"
        for c in ("a_t", "a_e", "a_x"):
            if pd.isna(r[c]) or r[c] == 0:
                return "data_missing"
        return "evaluated_ok"

    uni["outcome"] = uni.apply(_status, axis=1)
    ok = uni["outcome"] == "evaluated_ok"
    uni.loc[ok, "r_stock"] = (
        uni.loc[ok, "x_close"] * (uni.loc[ok, "a_t"] / uni.loc[ok, "a_x"])
    ) / (uni.loc[ok, "e_open"] * (uni.loc[ok, "a_t"] / uni.loc[ok, "a_e"])) - 1.0
    uni.loc[ok, "r_sw"] = uni.loc[ok, "sw_x"] / uni.loc[ok, "sw_e"] - 1.0
    uni["r_rel"] = uni["r_stock"] - uni["r_sw"]
    uni["y_rel"] = (uni["r_rel"] > 0).astype("Int64")

    uni = uni.rename(columns={"trade_date": "signal_date", "t1": "entry_date", "t40": "exit_date"})
    keep = ["ts_code", "signal_date", "entry_date", "exit_date", "r_stock", "r_sw",
            "r_rel", "y_rel", "l1_code", "l1_name", "outcome",
            "a_t", "a_e", "a_x", "e_open", "e_high", "e_low", "e_vol", "x_close"]
    return uni[[c for c in keep if c in uni.columns]].reset_index(drop=True)
