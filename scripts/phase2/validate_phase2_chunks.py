#!/usr/bin/env python3
"""DAV-1547 — validate built parquet chunks against a brute-force label resolver.

Reads each partition's data.parquet, re-derives outcome_status / entry / exit /
r_stock / r_sw / y_rel for a sample of rows with a naive per-row implementation
of return_labels.py BUY-leg semantics, and reports mismatches.

Usage:
    python scripts/phase2/validate_phase2_chunks.py [--sample-per-partition 400]
        [--out-dir data/phase2] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path(os.environ.get("PHASE2_CACHE_DIR",
                                "~/Documents/TradingAgents-AShare-cache/phase2")).expanduser()
EVAL_OFFSET = 40
MAX_ROLL = 5

LIMIT_RATE_MAIN = 0.10
LIMIT_RATE_GEM_STAR = 0.20
LIMIT_RATE_BSE = 0.30
LIMIT_RATE_ST = 0.05


def board_rate(code: str, is_st: bool) -> float:
    if is_st:
        return LIMIT_RATE_ST
    if code.endswith(".BJ"):
        return LIMIT_RATE_BSE
    if code[:3] in ("300", "301", "302") or code[:3] in ("688", "689"):
        return LIMIT_RATE_GEM_STAR
    return LIMIT_RATE_MAIN


def brute_label(code: str, signal_date: str, cal: list[str], bars: dict[str, dict],
                is_st: bool, sw_open_by_code: dict, sw_close_by_code: dict,
                sw_code: str, adj_map: dict[str, dict] | None):
    """Naive per-row resolver mirroring return_labels.py (BUY leg)."""
    if signal_date not in cal:
        return dict(outcome_status="invalid_signal")
    i = cal.index(signal_date)
    if i + EVAL_OFFSET >= len(cal):
        return dict(outcome_status="pending_due")
    entry_d = cal[i + 1]
    target_d = cal[i + EVAL_OFFSET]

    def bar(d):
        return bars.get(d)

    entry_bar = bar(entry_d)
    sig_bar = bar(signal_date)

    if entry_bar is None:
        has_pre = sig_bar is not None and sig_bar["close"] > 0
        has_sub = False
        if i + 2 < len(cal):
            t2 = bar(cal[i + 2])
            if t2 is not None and t2["close"] > 0 and t2["vol"] > 0:
                has_sub = True
        st = "unexecutable_entry" if (has_pre and has_sub) else "data_missing"
        return dict(outcome_status=st, entry_date=entry_d,
                    target_exit_date=target_d, actual_exit_date="",
                    roll_days_used=0, entry_open=np.nan, exit_close=np.nan)

    rate = board_rate(code, is_st)
    lup = entry_bar["pre_close"] * (1 + rate)
    oneword_up = (entry_bar["open"] >= lup and entry_bar["high"] == entry_bar["low"] == lup)
    if entry_bar["vol"] <= 0 or entry_bar["open"] <= 0 or oneword_up:
        return dict(outcome_status="unexecutable_entry", entry_date=entry_d,
                    target_exit_date=target_d, actual_exit_date="",
                    roll_days_used=0, entry_open=np.nan, exit_close=np.nan)

    entry_open = entry_bar["open"]

    actual_exit, exit_close, roll = "", np.nan, 0
    for r in range(MAX_ROLL + 1):
        p = i + EVAL_OFFSET + r
        if p >= len(cal):
            break
        d = cal[p]
        b = bar(d)
        if b is None:
            continue
        if b["vol"] <= 0 or b["close"] <= 0:
            continue
        ldown = b["pre_close"] * (1 - rate)
        if b["open"] <= ldown and b["high"] == b["low"] == ldown:
            continue
        actual_exit, exit_close, roll = d, b["close"], r
        break

    if not actual_exit:
        has_after = False
        p = i + EVAL_OFFSET + MAX_ROLL + 1
        if p < len(cal):
            b = bar(cal[p])
            if b is not None and b["close"] > 0 and b["vol"] > 0:
                has_after = True
        st = "suspension" if has_after else "data_missing"
        return dict(outcome_status=st, entry_date=entry_d,
                    target_exit_date=target_d, actual_exit_date="",
                    roll_days_used=0, entry_open=entry_open, exit_close=np.nan)

    r_stock_raw = exit_close / entry_open - 1.0
    # vendor_qfq primary basis
    def _adj(d):
        if not d or adj_map is None:
            return np.nan
        f = adj_map.get(d)
        if f is not None:
            return f
        prior = [k for k in adj_map if k <= d]
        return adj_map[max(prior)] if prior else np.nan
    ae, ax = _adj(entry_d), _adj(actual_exit)
    r_stock = (exit_close * ax) / (entry_open * ae) - 1.0 \
        if (ae == ae and ax == ax and entry_open * ae > 0) else np.nan
    # index leg: entry-date open -> exit close (same window as the stock)
    so = sw_open_by_code.get(sw_code, {})
    sd = sw_close_by_code.get(sw_code, {})
    s0, s1 = so.get(entry_d), sd.get(actual_exit)
    r_sw = (s1 / s0 - 1.0) if (s0 and s1) else np.nan
    r_rel = r_stock - r_sw if (not np.isnan(r_stock) and not np.isnan(r_sw)) else np.nan
    return dict(outcome_status="evaluated_ok", entry_date=entry_d,
                target_exit_date=target_d, actual_exit_date=actual_exit,
                roll_days_used=roll, entry_open=entry_open, exit_close=exit_close,
                r_stock=r_stock, r_stock_raw=r_stock_raw, r_sw=r_sw, r_rel=r_rel,
                y_rel=float(r_rel > 0) if not np.isnan(r_rel) else np.nan)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data/phase2")
    ap.add_argument("--sample-per-partition", type=int, default=400)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    # calendar
    api = CACHE_DIR / "api_cache"
    cal_df = pd.read_pickle(sorted(api.glob("trade_cal_*.pkl"))[0])
    cal = sorted(cal_df.loc[cal_df["is_open"] == 1, "cal_date"].tolist())

    # sw closes
    sw = pd.read_pickle(CACHE_DIR / "sw_daily.pkl")
    sw_close = {c: dict(zip(s["trade_date"], s["close"])) for c, s in sw.groupby("ts_code")}
    sw_open = {c: dict(zip(s["trade_date"], s["open"])) for c, s in sw.groupby("ts_code")}

    # namechange ST intervals (to reconstruct is_st per sample date)
    nc = pd.read_pickle(CACHE_DIR / "namechange.pkl")

    def is_st_at(code, d):
        rows = nc[nc["ts_code"] == code]
        for r in rows.itertuples():
            if "ST" in str(r.name):
                s = r.start_date or "00000000"
                e = r.end_date if isinstance(r.end_date, str) and r.end_date else "99999999"
                if s <= d <= e:
                    return True
        return False

    # Collect the set of symbols needed by the sample first, then make ONE pass
    # over daily_by_day keeping only those symbols' bars (bounded memory).
    need_codes: set[str] = set()
    sample_rows: dict[str, pd.DataFrame] = {}
    chunks = sorted(Path(args.out_dir, "chunks").rglob("data.parquet"))
    assert chunks, "no parquet partitions found"
    rng = np.random.default_rng(args.seed)
    for pq in chunks:
        df = pd.read_parquet(pq)
        n = min(args.sample_per_partition, len(df))
        idx = rng.choice(len(df), size=n, replace=False)
        sub = df.iloc[idx]
        sample_rows[str(pq)] = sub
        need_codes.update(sub["ts_code"].unique().tolist())

    print(f"loading bars for {len(need_codes)} sampled symbols ...", flush=True)
    all_bars: dict[str, dict[str, dict]] = {}
    d = CACHE_DIR / "daily_by_day"
    for f in sorted(d.glob("*.pkl")):
        df = pd.read_pickle(f)
        sub = df[df["ts_code"].isin(need_codes)]
        for r in sub.itertuples():
            all_bars.setdefault(r.ts_code, {})[r.trade_date] = dict(
                open=r.open, high=r.high, low=r.low, close=r.close,
                pre_close=r.pre_close, vol=r.vol)
    print(f"bar index built: {len(all_bars)} symbols", flush=True)

    def bars_for(code: str) -> dict[str, dict]:
        return all_bars.get(code, {})

    # adj_factor per needed symbol
    adj_cache: dict[str, dict] = {}
    def adj_for(code: str) -> dict:
        if code not in adj_cache:
            f = CACHE_DIR / "adj_factor" / f"{code}.pkl"
            adj_cache[code] = (dict(zip(pd.read_pickle(f)["trade_date"],
                                      pd.read_pickle(f)["adj_factor"]))
                               if f.exists() else {})
        return adj_cache[code]

    report = {"partitions": {}, "mismatches": []}
    total_bad = 0
    for pq in chunks:
        tag = str(pq.relative_to(args.out_dir))
        sub = sample_rows[str(pq)]
        n = len(sub)
        bad = 0
        checked = 0
        for _, row in sub.iterrows():
            if row["outcome_status"] == "pending_due":
                continue
            code = row["ts_code"]
            ref = brute_label(code, row["signal_date"], cal, bars_for(code),
                              is_st_at(code, row["signal_date"]), sw_open,
                              sw_close, row["sw_l1_code"], adj_for(code))
            checked += 1
            for field in ("outcome_status", "entry_date", "actual_exit_date",
                          "roll_days_used"):
                if str(ref.get(field, "")) != str(row[field]):
                    bad += 1
                    report["mismatches"].append({
                        "partition": tag, "ts_code": code,
                        "signal_date": row["signal_date"], "field": field,
                        "got": str(row[field]), "ref": str(ref.get(field))})
                    break
            else:
                for field in ("entry_open", "exit_close", "r_stock",
                              "r_stock_raw", "r_sw", "r_rel", "y_rel"):
                    g, r = row[field], ref.get(field, np.nan)
                    if pd.isna(g) and pd.isna(r):
                        continue
                    if pd.isna(g) != pd.isna(r) or not np.isclose(g, r, atol=1e-9):
                        bad += 1
                        report["mismatches"].append({
                            "partition": tag, "ts_code": code,
                            "signal_date": row["signal_date"], "field": field,
                            "got": g, "ref": r})
                        break
        report["partitions"][tag] = {"sampled": int(n), "checked": checked,
                                     "mismatches": bad}
        total_bad += bad
        print(f"{tag}: sampled={n} checked={checked} mismatches={bad}", flush=True)

    # stats.json consistency
    stats_p = Path(args.out_dir) / "stats.json"
    if stats_p.exists():
        stats = json.loads(stats_p.read_text())
        report["stats_partitions"] = len(stats.get("partitions", []))
        for p in stats.get("partitions", []):
            pq = Path(p["parquet"])
            if pq.exists():
                rows = len(pd.read_parquet(pq, columns=["ts_code"]))
                if rows != p["rows"]:
                    report["mismatches"].append({"partition": p["partition"],
                                                 "field": "stats_rows",
                                                 "got": rows, "ref": p["rows"]})
                    total_bad += 1

    report["total_mismatches"] = total_bad
    out = Path(args.out_dir) / "validation_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"total mismatches: {total_bad} -> {out}")
    return 0 if total_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
