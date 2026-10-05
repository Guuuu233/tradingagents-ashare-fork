"""Data fetch layer for P1 study (DAV-1478).

Pulls every dataset the study needs into the off-repo cache. All heavy pulls
are resumable: daily/daily_basic are stored per trade_date so an interrupted
run only re-pulls missing days. index_member_all is per symbol.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .client import TushareClient, _CACHE_ROOT, _load_manifest, _save_manifest


def _dates(d1: str, d2: str) -> str:
    return d1.replace("-", ""), d2.replace("-", "")


def fetch_trade_cal(client: TushareClient, start: str, end: str) -> pd.DataFrame:
    s, e = _dates(start, end)
    df = client.call(
        "trade_cal",
        params={"exchange": "SSE", "start_date": s, "end_date": e},
        fields="cal_date,is_open",
    )
    df = df[df["is_open"] == 1].copy()
    df["trade_date"] = pd.to_datetime(df["cal_date"]).dt.date.astype(str)
    return df[["trade_date"]].sort_values("trade_date").reset_index(drop=True)


def fetch_stock_basic(client: TushareClient) -> pd.DataFrame:
    frames = []
    for status in ("L", "D", "P"):
        df = client.call(
            "stock_basic",
            params={"list_status": status},
            fields="ts_code,name,market,exchange,list_date,delist_date,list_status",
            paginate=True,
        )
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    out.to_pickle(_CACHE_ROOT / "stock_basic.pkl")
    return out


def fetch_index_classify(client: TushareClient) -> pd.DataFrame:
    df = client.call(
        "index_classify",
        params={"level": "L1", "src": "SW2021"},
        fields="index_code,industry_name,level",
    )
    df.to_pickle(_CACHE_ROOT / "index_classify.pkl")
    return df


def fetch_sw_daily(client: TushareClient, codes: List[str], start: str, end: str) -> pd.DataFrame:
    s, e = _dates(start, end)
    frames = []
    for code in codes:
        df = client.call(
            "sw_daily",
            params={"ts_code": code, "start_date": s, "end_date": e},
            fields="ts_code,trade_date,open,high,low,close,vol",
            paginate=True,
        )
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    out.to_pickle(_CACHE_ROOT / "sw_daily.pkl")
    return out


def _day_cache_path(api: str, trade_date: str) -> Path:
    d = _CACHE_ROOT / f"{api}_by_day"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{trade_date}.pkl"


def fetch_daily_by_day(
    client: TushareClient,
    api: str,
    fields: str,
    trade_dates: List[str],
    progress_every: int = 100,
) -> None:
    """Pull a market-wide api (daily / daily_basic) per trade_date with resume."""
    done = 0
    for td in trade_dates:
        ymd = td.replace("-", "")
        fpath = _day_cache_path(api, td)
        if fpath.exists():
            done += 1
            continue
        df = client.call(
            api,
            params={"trade_date": ymd},
            fields=fields,
            paginate=True,
        )
        df.to_pickle(fpath)
        done += 1
        if progress_every and done % progress_every == 0:
            print(f"[fetch] {api} {done}/{len(trade_dates)} days", flush=True)


def consolidate_daily(api: str, trade_dates: List[str]) -> pd.DataFrame:
    frames = []
    for td in trade_dates:
        fpath = _day_cache_path(api, td)
        if fpath.exists():
            frames.append(pd.read_pickle(fpath))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    out.to_pickle(_CACHE_ROOT / f"{api}_all.pkl")
    return out


def fetch_namechange(client: TushareClient) -> pd.DataFrame:
    df = client.call(
        "namechange",
        fields="ts_code,name,start_date,end_date,change_reason",
        paginate=True,
    )
    df.to_pickle(_CACHE_ROOT / "namechange.pkl")
    return df


def fetch_index_weight(client: TushareClient, index_code: str, start: str, end: str) -> pd.DataFrame:
    """Pull every monthly index_weight snapshot for index_code in [start,end]."""
    s, e = _dates(start, end)
    df = client.call(
        "index_weight",
        params={"index_code": index_code, "start_date": s, "end_date": e},
        fields="index_code,trade_date,con_code,weight",
        paginate=True,
    )
    df.to_pickle(_CACHE_ROOT / f"index_weight_{index_code}.pkl")
    return df


def fetch_index_member_all(client: TushareClient, symbols: List[str]) -> pd.DataFrame:
    """Pull per-symbol SW2021 L1 membership intervals (Y and N batches).

    Mirrors resolve_sw_l1_benchmark's two-batch logic: query is_new=Y and
    is_new=N so historical (exited) memberships are included. Results per
    symbol are cached; resumable.
    """
    out_dir = _CACHE_ROOT / "index_member_all"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    pending = []
    for sym in symbols:
        f = out_dir / f"{sym}.pkl"
        if f.exists():
            rows.append(pd.read_pickle(f))
        else:
            pending.append(sym)
    for i, sym in enumerate(pending):
        frames = []
        for is_new in ("Y", "N"):
            df = client.call(
                "index_member_all",
                params={"ts_code": sym, "is_new": is_new},
                fields="ts_code,l1_code,l1_name,in_date,out_date,is_new",
            )
            frames.append(df)
        one = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            columns=["ts_code", "l1_code", "l1_name", "in_date", "out_date", "is_new"]
        )
        one["ts_code"] = sym
        one.to_pickle(out_dir / f"{sym}.pkl")
        rows.append(one)
        if (i + 1) % 200 == 0:
            print(f"[fetch] index_member_all {i+1}/{len(pending)} symbols", flush=True)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    out.to_pickle(_CACHE_ROOT / "index_member_all_all.pkl")
    return out


def fetch_adj_factor(client: TushareClient, symbols: List[str]) -> pd.DataFrame:
    """Pull per-symbol adj_factor over the full study window; resumable."""
    out_dir = _CACHE_ROOT / "adj_factor"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    pending = []
    for sym in symbols:
        f = out_dir / f"{sym}.pkl"
        if f.exists():
            rows.append(pd.read_pickle(f))
        else:
            pending.append(sym)
    for i, sym in enumerate(pending):
        df = client.call(
            "adj_factor",
            params={"ts_code": sym},
            fields="ts_code,trade_date,adj_factor",
            paginate=True,
        )
        df.to_pickle(out_dir / f"{sym}.pkl")
        rows.append(df)
        if (i + 1) % 200 == 0:
            print(f"[fetch] adj_factor {i+1}/{len(pending)} symbols", flush=True)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    out.to_pickle(_CACHE_ROOT / "adj_factor_all.pkl")
    return out
