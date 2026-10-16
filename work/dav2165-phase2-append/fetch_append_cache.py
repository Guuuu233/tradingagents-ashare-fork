#!/usr/bin/env python3
"""DAV-2165 — append-only phase2 cache overlay through 2026-10-16 (Tushare read-only).

Builds an overlay cache dir at --overlay that mirrors the frozen cache
~/Documents/TradingAgents-AShare-cache/phase2 (via symlinks / merged files) and
adds only the data needed for signal dates 2026-01-01 .. --through (default
2026-10-16). The frozen cache is NEVER written to.

Truncates the trading calendar at --through on purpose: signal dates whose T+40
exit falls after the last bar day must land in `pending_due`, not
`data_missing`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(os.path.expanduser("~/Documents/TradingAgents-AShare"))
sys.path.insert(0, str(REPO))

for _line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        if _k.strip().startswith("TUSHARE"):
            os.environ[_k.strip()] = _v.strip()

import pandas as pd  # noqa: E402

from tradingagents.dataflows.providers.industry_linkage_provider import (  # noqa: E402
    _query_tushare_api as query,
)

BASE_CACHE = Path(os.path.expanduser(
    os.environ.get("PHASE2_CACHE_DIR", "~/Documents/TradingAgents-AShare-cache/phase2")))

RETRY = 4
BACKOFF = 3.0
JOURNAL = Path("fetch_journal.jsonl")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def jrec(**kw) -> None:
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(kw, ensure_ascii=False) + "\n")


def api(api_name: str, params: dict, fields: str) -> pd.DataFrame | None:
    """Query with retries; returns None only when the gateway reports empty."""
    delay = 2.0
    for attempt in range(1, RETRY + 1):
        df, err, note = query(api_name, params=params, fields=fields)
        if err is None:
            return df
        if err == "empty_rows":
            return None
        log(f"  retry {attempt}/{RETRY} {api_name} {params} err={err} {note}")
        if attempt == RETRY:
            raise RuntimeError(f"{api_name} {params} failed: {err} {note}")
        time.sleep(delay)
        delay *= 2
    return None


def merge_on_key(old: pd.DataFrame, new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Append `new` on top of the frozen `old`, deduping on the full key.

    The key must carry every column that distinguishes two rows. Dropping
    `end_date` from the namechange key silently deleted 29,627 historical rows
    whose only difference was `end_date` (DAV-2169), which in turn halved the
    ST interval count and mislabelled `is_st` / LIMIT_RATE_ST on the 2026
    partition. m2_sampler already dedupes on the 4-column key; keep both in sync.
    """
    if new is None or new.empty:
        return old
    old = old.drop_duplicates(subset=keys, keep="last")
    new = new.drop_duplicates(subset=keys, keep="last")
    return pd.concat([old, new], ignore_index=True).sort_values(keys).reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--through", default="20261016")
    ap.add_argument("--overlay", default="overlay_cache")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()

    through = args.through
    ov = Path(args.overlay).resolve()
    ov.mkdir(parents=True, exist_ok=True)
    JOURNAL.write_text("", encoding="utf-8")

    # ---- 1. trading calendar (truncated at --through) -----------------------
    log("trade_cal SSE 20151001-20261231")
    cal = api("trade_cal", {"exchange": "SSE", "start_date": "20151001",
                            "end_date": "20261231"}, "cal_date,is_open")
    if cal is None or cal.empty:
        raise RuntimeError("trade_cal empty")
    cal["cal_date"] = cal["cal_date"].astype(str)
    open_days = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].tolist())
    cached_days = sorted(p.stem.replace("-", "") for p in (BASE_CACHE / "daily_by_day").glob("*.pkl"))
    new_days = [d for d in open_days if d > cached_days[-1] and d <= through]
    log(f"calendar open days={len(open_days)} cached_max={cached_days[-1]} "
        f"new_days_to_fetch={len(new_days)} ({new_days[:2]}..{new_days[-1:]})")
    (ov / "api_cache").mkdir(exist_ok=True)
    cal_keep = cal.loc[cal["cal_date"].isin([d for d in open_days if d <= through]) |
                        (cal["is_open"] == 0)].copy()
    cal_keep = cal_keep.loc[cal_keep["cal_date"] <= through].reset_index(drop=True)
    # name sorts before the frozen trade_cal_* files: build_phase2_chunks.load_calendar
    # takes sorted(glob)[0], so the truncated calendar must win.
    cal_path = ov / "api_cache" / "trade_cal_0_dav2165.pkl"
    cal_keep.to_pickle(cal_path)
    jrec(kind="trade_cal", rows=len(cal_keep), max_date=through, path=str(cal_path))

    # ---- 2. daily / daily_basic / adj_factor per new day --------------------
    for sub, api_name, fields in (
        ("daily_by_day", "daily", "ts_code,trade_date,open,high,low,close,pre_close,vol,amount"),
        ("daily_basic_by_day", "daily_basic",
         "ts_code,trade_date,pe_ttm,pb,total_mv,circ_mv,turnover_rate"),
    ):
        d = ov / sub
        d.mkdir(exist_ok=True)
        for f in sorted((BASE_CACHE / sub).glob("*.pkl")):
            dst = d / f.name
            if not dst.exists():
                dst.symlink_to(f)
        for i, day in enumerate(new_days, 1):
            df = api(api_name, {"trade_date": day}, fields)
            if df is None or df.empty:
                log(f"  {sub} {day} EMPTY -> fail-closed")
                raise RuntimeError(f"{api_name} {day} empty")
            df = df[df["ts_code"].str.endswith((".SH", ".SZ", ".BJ"))]
            if api_name == "daily":
                df = df[["ts_code", "trade_date", "open", "high", "low", "close",
                         "pre_close", "vol"]]
            else:
                df = df[["ts_code", "trade_date", "pe_ttm", "pb", "total_mv", "turnover_rate"]]
            iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            df.to_pickle(d / f"{iso}.pkl")
            jrec(kind=api_name, trade_date=day, rows=len(df), path=str(d / f"{iso}.pkl"))
            if i % 20 == 0 or i == len(new_days):
                log(f"  {sub} {i}/{len(new_days)} last={day} rows={len(df)}")
            time.sleep(args.sleep)

    # ---- 3. sw_daily L1 indices (31) ---------------------------------------
    idx_class = pd.read_pickle(BASE_CACHE / "index_classify.pkl")
    codes = sorted(idx_class["index_code"].astype(str).unique())
    log(f"sw_daily {len(codes)} L1 indices")
    sw_old = pd.read_pickle(BASE_CACHE / "sw_daily.pkl")
    sw_new = None
    for c in codes:
        df = api("sw_daily", {"ts_code": c, "start_date": "20260101", "end_date": through},
                 "ts_code,trade_date,open,high,low,close,vol")
        if df is not None and not df.empty:
            sw_new = df if sw_new is None else pd.concat([sw_new, df], ignore_index=True)
        jrec(kind="sw_daily", ts_code=c, rows=0 if df is None else len(df))
        time.sleep(args.sleep)
    if sw_new is None or sw_new.empty:
        raise RuntimeError("sw_daily fetch empty")
    sw_new["ts_code"] = sw_new["ts_code"].astype(str)
    sw_new["trade_date"] = sw_new["trade_date"].astype(str)
    sw_old = sw_old.copy()
    sw_old["trade_date"] = sw_old["trade_date"].astype(str)
    sw_all = pd.concat([sw_old, sw_new], ignore_index=True)
    sw_all = sw_all.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    sw_all = sw_all.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    sw_all.to_pickle(ov / "sw_daily.pkl")
    log(f"  sw_daily merged rows={len(sw_old)}->{len(sw_all)} max={sw_all['trade_date'].max()}")

    # ---- 4. index weights (month-end snapshots in 2026) --------------------
    ym = sorted({d[:6] for d in new_days})
    for fname, code in (("index_weight_000300.SH.pkl", "000300.SH"),
                        ("index_weight_000905.SH.pkl", "000905.SH")):
        old = pd.read_pickle(BASE_CACHE / fname)
        parts = []
        for m in ym:
            days = [d for d in new_days if d.startswith(m)]
            if not days:
                continue
            df = api("index_weight", {"index_code": code, "trade_date": days[-1]},
                     "index_code,trade_date,con_code,weight")
            if df is not None and not df.empty:
                parts.append(df)
            jrec(kind="index_weight", index_code=code, trade_date=days[-1],
                 rows=0 if df is None else len(df))
            time.sleep(args.sleep)
        if parts:
            new = pd.concat(parts, ignore_index=True)
            new["trade_date"] = new["trade_date"].astype(str)
            old = old.copy()
            old["trade_date"] = old["trade_date"].astype(str)
            allw = pd.concat([old, new], ignore_index=True)
            allw = allw.drop_duplicates(subset=["index_code", "trade_date", "con_code"],
                                        keep="last").sort_values(
                ["index_code", "trade_date"]).reset_index(drop=True)
            allw.to_pickle(ov / fname)
            log(f"  {code} snapshots added={sorted(set(new['trade_date']))} "
                f"rows={len(old)}->{len(allw)}")

    # ---- 5. stock_basic / namechange merged refresh ------------------------
    sb_parts = [pd.read_pickle(BASE_CACHE / "stock_basic.pkl")]
    for status in ("L", "D"):
        df = api("stock_basic", {"list_status": status},
                 "ts_code,name,market,exchange,list_date,delist_date,list_status")
        if df is None or df.empty:
            continue
        df["list_date"] = df["list_date"].astype(str)
        jrec(kind="stock_basic", list_status=status, rows=len(df),
             max_list_date=df["list_date"].max())
        sb_parts.append(df)
        time.sleep(args.sleep)
    sb = pd.concat(sb_parts, ignore_index=True)
    sb = sb.drop_duplicates(subset=["ts_code"], keep="last").reset_index(drop=True)
    sb.to_pickle(ov / "stock_basic.pkl")
    log(f"  stock_basic rows={len(sb)} max_list_date={sb['list_date'].max()}")

    nc_old = pd.read_pickle(BASE_CACHE / "namechange.pkl")
    nc_new = api("namechange", {"start_date": "20260101", "end_date": through},
                 "ts_code,name,start_date,end_date,change_reason")
    # 4-column key, matching m2_sampler.build_st_intervals: (ts_code, name,
    # start_date, end_date). A 3-column key is lossy — see merge_on_key docstring.
    nc = merge_on_key(nc_old, nc_new, ["ts_code", "name", "start_date", "end_date"])
    nc.to_pickle(ov / "namechange.pkl")
    jrec(kind="namechange", new_rows=0 if nc_new is None else len(nc_new), merged=len(nc))
    log(f"  namechange rows={len(nc_old)}->{len(nc)} "
        f"new_max_start={None if nc_new is None else nc_new['start_date'].max()}")

    # ---- 6. index_member_all: mirror (no incremental source available) -----
    im = ov / "index_member_all"
    im.mkdir(exist_ok=True)
    for f in sorted((BASE_CACHE / "index_member_all").glob("*.pkl")):
        dst = im / f.name
        if not dst.exists():
            dst.symlink_to(f)
    jrec(kind="index_member_all", mode="symlink_mirror", files=len(list(im.glob("*.pkl"))))

    # ---- 7. adj_factor merged per symbol -----------------------------------
    adj_dir = ov / "adj_factor"
    adj_dir.mkdir(exist_ok=True)
    new_adj_days = [d for d in new_days]
    per_symbol: dict[str, list[pd.DataFrame]] = {}
    for i, day in enumerate(new_adj_days, 1):
        df = api("adj_factor", {"trade_date": day}, "ts_code,trade_date,adj_factor")
        if df is None or df.empty:
            continue
        df["trade_date"] = df["trade_date"].astype(str)
        for code, sub in df.groupby("ts_code"):
            per_symbol.setdefault(str(code), []).append(sub[["ts_code", "trade_date", "adj_factor"]])
        jrec(kind="adj_factor", trade_date=day, rows=len(df))
        if i % 40 == 0 or i == len(new_adj_days):
            log(f"  adj_factor {i}/{len(new_adj_days)} symbols={len(per_symbol)}")
        time.sleep(args.sleep)
    n_merged = 0
    for code, parts in per_symbol.items():
        src = BASE_CACHE / "adj_factor" / f"{code}.pkl"
        old = pd.read_pickle(src) if src.exists() else None
        new = pd.concat(parts, ignore_index=True)
        if old is not None and len(old.columns):
            old = old[["ts_code", "trade_date", "adj_factor"]].copy()
            old["trade_date"] = old["trade_date"].astype(str)
            new = pd.concat([old, new], ignore_index=True)
        new = new.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        new = new.sort_values("trade_date").reset_index(drop=True)
        new.to_pickle(adj_dir / f"{code}.pkl")
        n_merged += 1
    for f in sorted((BASE_CACHE / "adj_factor").glob("*.pkl")):
        dst = adj_dir / f.name
        if not dst.exists():
            dst.symlink_to(f)
    log(f"  adj_factor merged={n_merged} symlinked={len(list(adj_dir.glob('*.pkl'))) - n_merged}")

    log(f"overlay ready: {ov}")
    return 0


if __name__ == "__main__":
    sys.exit(main())