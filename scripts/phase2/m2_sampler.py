#!/usr/bin/env python3
"""DAV-1679 — M2 sampler: industry-stratified daily pick list (name list only).

Builds the M2 前瞻模拟实验室 measurement-batch universe (plan v1.0 §5):

  - Pool: HS300 + ZZ500 constituents *as of* the signal date T. Membership is
    point-in-time: the `index_weight` snapshot with the latest
    trade_date <= T is used (per index, independently).
  - Exclusions on T:
      * ST / *ST — point-in-time via `namechange` name intervals
        (name contains "ST" covering that date);
      * listing age < 1 calendar year — `stock_basic.list_date` must satisfy
        list_date <= T - 365 calendar days;
      * suspended — the stock must have a tradeable daily bar on T, defined
        as a row in `daily_by_day/<T>.pkl` with vol > 0 and open > 0
        (matches the V-03a / build_phase2_chunks entry-tradability caliber).
  - Sampling: SW2021 L1 industry-stratified random sample of N names.
    Per-stratum quota is proportional to stratum size (floor + largest-
    remainder rounding; remainder ties broken by stratum code ascending).
    With a fixed seed and fixed inputs the output is fully deterministic —
    a manifest hash of the eligible frame is reported so a changed input
    can never be mistaken for the same draw.
  - Output is a name list (JSON / CSV). No analysis, no model calls, no
    order intents — "只出名单，不触发任何分析".

Reads the phase2 cache READ-ONLY (env PHASE2_CACHE_DIR, default
~/Documents/TradingAgents-AShare-cache/phase2). Peak RSS stays far below the
4 GiB single-process budget: only per-day bars for the requested dates plus
the small lookup tables are loaded.

Usage:
    python scripts/phase2/m2_sampler.py --date 20251231 --n 20 --seed 42 \
        --out work/m2_sampler/picks-20251231.json

    # reproducibility check — must be byte-identical
    python scripts/phase2/m2_sampler.py --date 20251231 --n 20 --seed 42 \
        --out /tmp/a.json && \
    python scripts/phase2/m2_sampler.py --date 20251231 --n 20 --seed 42 \
        --out /tmp/b.json && cmp /tmp/a.json /tmp/b.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path(os.environ.get("PHASE2_CACHE_DIR",
                                "~/Documents/TradingAgents-AShare-cache/phase2")).expanduser()

IDX_FILES = {
    "hs300": "index_weight_000300.SH.pkl",
    "zz500": "index_weight_000905.SH.pkl",
}

# listing-age exclusion: list_date <= T - LIST_AGE_YEARS*365 calendar days
LIST_AGE_DAYS = 365


# ---------------------------------------------------------------------------
# Cache loading helpers (read-only) — mirrors build_phase2_chunks.py calibers
# ---------------------------------------------------------------------------

def load_index_weight_snapshot(cache: Path, index_key: str, date: str) -> pd.DataFrame:
    """Latest index_weight snapshot with trade_date <= date."""
    df = pd.read_pickle(cache / IDX_FILES[index_key])
    dates = df.loc[df["trade_date"] <= date, "trade_date"]
    if dates.empty:
        raise ValueError(f"{index_key}: no index_weight snapshot on or before {date}")
    snap_date = dates.max()
    snap = df.loc[df["trade_date"] == snap_date, ["con_code", "trade_date"]].copy()
    snap.columns = ["ts_code", "idx_snap_date"]
    return snap


def load_sw_l1_pit(cache: Path, codes: list[str], date: str) -> dict[str, tuple[str, str]]:
    """Point-in-time SW2021 L1 (in_date <= T < out_date) for the given codes."""
    member = pd.read_pickle(cache / "index_member_all_all.pkl")
    member = member[member["ts_code"].isin(codes)]
    in_ok = member["in_date"].fillna("").astype(str) <= date
    out = member["out_date"].fillna("")
    out_ok = (out == "") | (out.astype(str) > date)
    active = member[in_ok & out_ok]
    out_map: dict[str, tuple[str, str]] = {}
    for row in active.itertuples(index=False):
        # A symbol with two live L1 rows is a data anomaly; keep first and flag.
        if row.ts_code in out_map:
            continue
        out_map[row.ts_code] = (str(row.l1_code), str(row.l1_name))
    return out_map


def build_st_intervals(namechange: pd.DataFrame) -> dict[str, list[tuple[str, str]]]:
    """ts_code -> sorted name intervals [(start_date, end_date, name)].

    The cached namechange extract has duplicate rows (same ts_code +
    start_date + name, and even conflicting end_date variants). The reliable
    signal is the ROW-WISE name at each interval: end_date=None means the
    name is current only if no later start_date supersedes it, so we keep
    every distinct interval (deduplicated) and let is_st_on pick the
    latest-starting interval covering T.
    """
    nc = namechange.drop_duplicates(subset=["ts_code", "name", "start_date", "end_date"])
    nc = nc.copy()
    nc["end_d"] = nc["end_date"].fillna("99999999").astype(str)
    nc["start_d"] = nc["start_date"].fillna("00000000").astype(str)
    intervals: dict[str, list[tuple[str, str, str]]] = {}
    for row in nc.itertuples(index=False):
        intervals.setdefault(str(row.ts_code), []).append(
            (row.start_d, row.end_d, str(row.name)))
    for code in intervals:
        intervals[code].sort()
    return intervals


def is_st_on(code: str, date: str, st_intervals: dict[str, list[tuple[str, str, str]]]) -> bool:
    """ST/*ST at `date` per the latest namechange interval that has started.

    Pick the interval with the greatest start_date <= T; if that interval's
    end_date > T its name is the name in force at T. An end_date<=T interval
    can't be current (a later-named row must exist, else data gap).
    """
    rows = [r for r in st_intervals.get(code, ()) if r[0] <= date]
    if not rows:
        return False
    _s, end, name = rows[-1]
    if end <= date:
        # last-started interval already ended -> name after it unknown/normal
        return False
    return "ST" in name.upper()


def load_tradable_on(cache: Path, date: str) -> set[str]:
    """Symbols with a tradeable bar on `date` (row present, vol>0, open>0).

    Returns the universe of *tradable* codes; absence of the day file means the
    date is not a trading day at all.
    """
    f = cache / "daily_by_day" / f"{date[:4]}-{date[4:6]}-{date[6:8]}.pkl"
    if not f.exists():
        raise FileNotFoundError(
            f"no daily bar file for {date}: {f.name} — not a trading day in cache")
    df = pd.read_pickle(f)
    ok = df[(df["vol"].fillna(0) > 0) & (df["open"].fillna(0) > 0)]
    return set(ok["ts_code"])


# ---------------------------------------------------------------------------
# Universe + stratified sampling
# ---------------------------------------------------------------------------

def eligible_universe(cache: Path, date: str) -> tuple[pd.DataFrame, dict]:
    """HS300 ∪ ZZ500 members at T minus ST / listing<1y / suspended.

    Returns (eligible DataFrame, stats dict). Columns:
    ts_code, in_hs300, in_zz500, sw_l1_code, sw_l1_name.
    """
    s300 = load_index_weight_snapshot(cache, "hs300", date)
    s500 = load_index_weight_snapshot(cache, "zz500", date)
    snap_dates = {"hs300": s300["idx_snap_date"].iloc[0],
                  "zz500": s500["idx_snap_date"].iloc[0]}

    pool = pd.DataFrame(
        {"ts_code": sorted(set(s300["ts_code"]) | set(s500["ts_code"]))})
    pool["in_hs300"] = pool["ts_code"].isin(set(s300["ts_code"]))
    pool["in_zz500"] = pool["ts_code"].isin(set(s500["ts_code"]))

    # --- exclusions ---------------------------------------------------------
    sb = pd.read_pickle(cache / "stock_basic.pkl")
    list_date = dict(zip(sb["ts_code"], sb["list_date"].astype(str)))
    cutoff = (pd.Timestamp(date) - pd.Timedelta(days=LIST_AGE_DAYS)).strftime("%Y%m%d")
    too_young = {c for c in pool["ts_code"]
                 if list_date.get(c, "99999999") > cutoff or list_date.get(c) in ("", "nan", "None")}

    st_intervals = build_st_intervals(pd.read_pickle(cache / "namechange.pkl"))
    st_now = {c for c in pool["ts_code"] if is_st_on(c, date, st_intervals)}

    tradable = load_tradable_on(cache, date)
    suspended = {c for c in pool["ts_code"] if c not in tradable}

    sw = load_sw_l1_pit(cache, pool["ts_code"].tolist(), date)
    pool["sw_l1_code"] = pool["ts_code"].map(lambda c: sw.get(c, ("", ""))[0])
    pool["sw_l1_name"] = pool["ts_code"].map(lambda c: sw.get(c, ("", ""))[1])
    no_industry = set(pool.loc[pool["sw_l1_code"] == "", "ts_code"])

    keep = ~pool["ts_code"].isin(st_now | too_young | suspended | no_industry)
    eligible = pool[keep].reset_index(drop=True)

    stats = {
        "pool_size": int(len(pool)),
        "index_snapshot_dates": snap_dates,
        "excluded": {
            "st": sorted(st_now & set(pool["ts_code"])),
            "listing_lt_1y": sorted(too_young),
            "suspended": sorted(suspended),
            "no_sw_l1": sorted(no_industry),
        },
        "excluded_counts": {
            "st": len(st_now & set(pool["ts_code"])),
            "listing_lt_1y": len(too_young),
            "suspended": len(suspended),
            "no_sw_l1": len(no_industry),
        },
        "eligible": int(len(eligible)),
    }
    return eligible, stats


def stratified_sample(g: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """SW-L1 proportional stratified sample (floor + largest remainder).

    Deterministic under a fixed seed: quotas are computed from stratum sizes
    only (floor + largest remainder, ties broken by stratum code ascending),
    and each stratum draw uses a Generator seeded from (seed, stratum
    code) — independent of frame ordering and of the other strata.
    """
    if len(g) <= n:
        return g.sort_values("ts_code").reset_index(drop=True)
    counts = g["sw_l1_code"].value_counts().sort_index()
    raw = counts / counts.sum() * n
    quota = raw.astype(int)
    leftover = int(n - quota.sum())
    # largest-remainder, ties broken by stratum code ascending (deterministic)
    rem = (raw - quota).rename("remainder").reset_index()
    rem = rem.sort_values(by=["remainder", "sw_l1_code"], ascending=[False, True])
    for code in rem["sw_l1_code"].iloc[:leftover]:
        quota[code] += 1
    parts = []
    for code, k in quota.items():
        if k <= 0:
            continue
        sub = g[g["sw_l1_code"] == code].sort_values("ts_code")
        take = min(int(k), len(sub))
        # derive a per-stratum seed so quotas/shuffling stay reproducible
        seed_int = int.from_bytes(
            hashlib.sha256(f"m2-sampler|{code}".encode()).digest()[:8], "big")
        child = np.random.default_rng(seed ^ seed_int)
        idx = child.choice(len(sub), size=take, replace=False)
        parts.append(sub.iloc[sorted(idx)])
    if not parts:
        return g.iloc[:0]
    return pd.concat(parts).sort_values("ts_code").reset_index(drop=True)


def frame_digest(df: pd.DataFrame) -> str:
    """Stable digest of the eligible universe — same input => same digest."""
    h = hashlib.sha256()
    h.update("|".join(df["ts_code"]).encode())
    h.update("|".join(df["sw_l1_code"]).encode())
    return h.hexdigest()


def run(cache: Path, date: str, n: int, seed: int) -> dict:
    eligible, stats = eligible_universe(cache, date)
    picks = stratified_sample(eligible, n, seed)
    return {
        "trade_date": date,
        "n_requested": n,
        "n_picked": int(len(picks)),
        "seed": seed,
        "eligible_digest": frame_digest(eligible),
        **stats,
        "picks": picks.to_dict("records"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", required=True, help="signal trade_date YYYYMMDD")
    ap.add_argument("--n", type=int, required=True, help="sample size N")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", help="write JSON result to this path")
    ap.add_argument("--csv", help="also write a CSV name list to this path")
    ap.add_argument("--cache-dir", default=str(CACHE_DIR))
    args = ap.parse_args()

    if len(args.date) != 8 or not args.date.isdigit():
        raise SystemExit(f"--date must be YYYYMMDD, got {args.date!r}")
    if args.n <= 0:
        raise SystemExit("--n must be positive")

    cache = Path(args.cache_dir).expanduser()
    result = run(cache, args.date, args.n, args.seed)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    if args.csv:
        csv = Path(args.csv)
        csv.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(result["picks"]).to_csv(csv, index=False)
    if not args.out and not args.csv:
        print(payload)
    else:
        ex = result["excluded_counts"]
        print(f"[m2-sampler] date={args.date} pool={result['pool_size']} "
              f"eligible={result['eligible']} picked={result['n_picked']}/{args.n} "
              f"excl(st={ex['st']} young={ex['listing_lt_1y']} susp={ex['suspended']} "
              f"noL1={ex['no_sw_l1']}) digest={result['eligible_digest'][:12]}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
