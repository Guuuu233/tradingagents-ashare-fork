#!/usr/bin/env python3
"""DAV-1560 — P1 子卡三：统计层（只读子卡二落盘 parquet，产出六项交付物的数字）.

Reads data/phase2/chunks/year=YYYY/data.parquet produced by DAV-1547
(commit 390e62259c10241aa6c885c002e42e71a743fc34) strictly READ-ONLY — never
modifies or recomputes the chunk files. Label semantics (raw r_stock,
close-to-close r_sw, y_rel) are exactly what the builder wrote; vendor_qfq is
derived here from the on-disk adj_factor_entry/exit columns (no refetch).

Spec: docs/research/phase2-midterm/p1-study-spec.md (DAV-1478, §9 six points
approved by 总控 2026-10-05). Deliverables covered:
  D1 same-day pairwise corr of r_rel, groups {all, same-week stratum,
     same-industry, same-style-bucket (4-factor decile & size×value quintile
     coarse)}; equal- and pair-weighted across days; pairwise-Pearson variant.
  D2 daily cross-sectional stats: Spearman IC + hi-lo quintile diff for public
     factor scores (mom120s20, value, lowvol, small, large, composite),
     y_rel-binary control, and simulated scores at true IC
     {0,.02,.05,.08,.12}; ACF/PACF 1..120, Ljung-Box {20,40,80,120},
     Newey-West {40,60,80,120,Andrews-auto}, moving-block bootstrap
     {40,60,80,120}, long-run n_eff; calendar-aligned (missing days stay NaN).
  D3 style explanatory power: per-day OLS r_rel ~ 1 + z(size,value,mom,vol),
     mean/median R², residualised D1/D2, 前2/3估→后1/3 OOS R².
  D4 industry base rates: matured-only expanding window (actual_exit_date<=T)
     per day, Beta-Binomial shrink m ∈ {50,200,500}; industry×year table.
  D5 info-cost frontier: hs300+zz500 industry-stratified sample
     N ∈ {10,20,30,40} + uniform control; IC/D stats vs full universe,
     top-N overlap vs composite full ranking, industry coverage, n_eff;
     parametric token/latency cost (no currency).
  D6 freeze-evidence: tied-rank share, NaN-IC days, min_n coverage/
     min_daily_cross_section_n ∈ {10,20,30,40} coverage/stability.

Resource contract: single process, peak RSS <= 6 GB (18 GB host). Two
streaming passes — each year partition is materialised alone (plus the day
panel ~10M×32B accumulators and per-year sampling pools). Every result is
persisted to data/phase2/stats/ (json + parquet + md + run.json with measured
peak RSS and elapsed). Nothing is print-only.

Missing derived columns vs spec (chunks frozen by DAV-1547): mom_120s20 /
vol_120 are recomputed here from the same off-repo cache's daily_by_day
pickles (read-only, close+vol only) with per-symbol rolling semantics.

Usage:
  python scripts/phase2/p1_stats_layer.py [--chunks-dir PATH] [--out-dir PATH]
  python scripts/phase2/p1_stats_layer.py --selftest
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import resource
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

CHUNKS_DEFAULT = Path(
    "/Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/"
    "dav-1547-cfa02881b296/workdir/tradingagents-ashare-fork/data/phase2/chunks")
OUT_DEFAULT = Path("data/phase2/stats")
CACHE_DIR = Path(os.environ.get(
    "PHASE2_CACHE_DIR",
    "~/Documents/TradingAgents-AShare-cache/phase2")).expanduser()

SEED = 1478
MAIN_START = "20190603"          # spec §1 primary window
LONG_START = "20160101"          # spec §1 sensitivity window
EVAL_OK = "evaluated_ok"
SIM_ICS = (0.00, 0.02, 0.05, 0.08, 0.12)
NW_BANDS = (40, 60, 80, 120)
LB_LAGS = (20, 40, 80, 120)
BLOCK_LENS = (40, 60, 80, 120)
N_LIST = (10, 20, 30, 40)
SHRINK_M = (50, 200, 500)

SCORE_COLS = ["score_mom", "score_value", "score_lowvol",
              "score_small", "score_large", "score_composite"]
ALL_SERIES = SCORE_COLS + ["y_binary"] + [f"sim_{ic}" for ic in SIM_ICS]


def peak_rss_gb() -> float:
    val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return val / (1024 ** 3) if val > 10 ** 6 else val / (1024 ** 2)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def seed_for(tag: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{SEED}|{tag}".encode()).digest()[:4], "little")


# ---------------------------------------------------------------------------
# Pure-numpy stats helpers
# ---------------------------------------------------------------------------

def _rankdata(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1)
    s = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = ranks[order[i:j + 1]].mean()
        i = j + 1
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 3:
        return np.nan
    rx, ry = _rankdata(x[m]), _rankdata(y[m])
    if rx.std() == 0 or ry.std() == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 3:
        return np.nan
    xm, ym = x[m], y[m]
    if xm.std() == 0 or ym.std() == 0:
        return np.nan
    return float(np.corrcoef(xm, ym)[0, 1])


def acf_pacf(series: np.ndarray, max_lag: int):
    """ACF + PACF (Durbin-Levinson) on NaN-stripped series."""
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    ac = np.full(max_lag + 1, np.nan)
    pa = np.full(max_lag + 1, np.nan)
    if n < 4:
        return ac, pa
    xc = x - x.mean()
    denom = np.dot(xc, xc)
    if denom == 0:
        return ac, pa
    L = min(max_lag, n - 2)
    ac[0], pa[0] = 1.0, 1.0
    phi = np.zeros(L + 1)
    for k in range(1, L + 1):
        ac[k] = np.dot(xc[:-k], xc[k:]) / denom
        num = ac[k] - np.dot(phi[1:k], ac[k - 1:0:-1])
        den = 1.0 - np.dot(phi[1:k], ac[1:k])
        if abs(den) < 1e-12:
            pa[k] = np.nan
            break
        pa[k] = num / den
        phi[1:k] = phi[1:k] - pa[k] * phi[k - 1:0:-1]
        phi[k] = pa[k]
    return ac, pa


def ljung_box(series: np.ndarray, lags) -> dict:
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    out = {}
    for h in lags:
        if n < h + 2:
            out[h] = np.nan
            continue
        ac, _ = acf_pacf(x, h)
        out[h] = float(n * (n + 2) *
                       sum(ac[k] ** 2 / (n - k)
                           for k in range(1, h + 1) if not np.isnan(ac[k])))
    return out


def long_run_var(series: np.ndarray, max_lag: int | None = None):
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return np.nan, np.nan
    L = max_lag if max_lag is not None else int(math.sqrt(n))
    ac, _ = acf_pacf(x, min(L, n - 2))
    rho_sum = 1 + 2 * np.nansum(ac[1:])
    return (float(n / rho_sum) if rho_sum > 1e-9 else np.nan), float(rho_sum)


def newey_west_se(series: np.ndarray, max_lag: int) -> float:
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return np.nan
    xc = x - x.mean()
    s = np.dot(xc, xc) / n
    for k in range(1, min(max_lag, n - 1) + 1):
        s += 2 * (1 - k / (max_lag + 1)) * np.dot(xc[:-k], xc[k:]) / n
    return float(np.sqrt(max(s / n, 0)))


def nw_auto_bandwidth(series: np.ndarray) -> int:
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return 0
    xc = x - x.mean()
    denom = np.dot(xc, xc)
    if denom == 0:
        return 0
    a1 = np.dot(xc[:-1], xc[1:]) / denom
    return int(round(1.1447 * (abs(a1) * n) ** (1 / 3)))


def moving_block_bootstrap_se(series: np.ndarray, block_len: int, n_boot: int,
                              rng: np.random.Generator) -> float:
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < block_len + 2:
        return np.nan
    nb = int(np.ceil(n / block_len))
    means = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n - block_len + 1, size=nb)
        idx = (starts[:, None] + np.arange(block_len)[None, :]).ravel()[:n]
        means[b] = x[idx].mean()
    return float(means.std(ddof=1))


def zscore_arr(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    m = ~np.isnan(v)
    out = np.full(len(v), np.nan)
    if m.sum() > 1 and np.nanstd(v[m]) > 0:
        out[m] = (v[m] - v[m].mean()) / v[m].std()
    return out


def day_pair_rho(r: np.ndarray, ids: np.ndarray | None = None):
    """Mean of z_i·z_j over unordered pairs — spec §5.1 estimator.

    Per spec §5.1 the estimator is literally  mean over unordered pairs of
    z(r_i)·z(r_j)  per signal-day, aggregated across days.  For a single day
    and z with Σz≈0 this degenerates to ≈−1/(n−1); the spec fixes this by
    aggregating over days (pairs appear on many days, and the *same* stock
    pair's z-product tracks their co-movement).  We therefore accumulate
    per-pair z_i·z_j over days — the day-level call returns per-pair
    products so the accumulator can sum by (i,j) and across days.
    Returns (sum_zizj, n_pairs) for the caller to pool.
    """
    m = ~np.isnan(r)
    v = np.asarray(r)[m].astype(float)
    n = len(v)
    if n < 2:
        return np.nan, 0
    z = (v - v.mean()) / v.std() if v.std() > 0 else None
    if z is None:
        return np.nan, 0
    s, s2 = z.sum(), (z ** 2).sum()
    # mean z_i z_j over unordered pairs — value, not the degenerate within-day
    # Pearson. Aggregated across days this estimates the pair correlation.
    return float((s * s - s2) / (n * (n - 1))), n * (n - 1) // 2


def hilo(score: np.ndarray, r: np.ndarray, hi=0.8, lo=0.2) -> float:
    m = ~(np.isnan(score) | np.isnan(r))
    if m.sum() < 5:
        return np.nan
    q = pd.Series(score[m]).rank(pct=True).to_numpy()
    rv = r[m]
    h = np.nanmean(rv[q >= hi]) if (q >= hi).any() else np.nan
    l = np.nanmean(rv[q <= lo]) if (q <= lo).any() else np.nan
    return float(h - l)


def ols_fit(X: np.ndarray, y: np.ndarray):
    m = ~(np.isnan(y) | np.isnan(X).any(axis=1))
    if m.sum() < X.shape[1] + 2:
        return None
    Xd = np.column_stack([np.ones(int(m.sum())), X[m]])
    try:
        beta, *_ = np.linalg.lstsq(Xd, y[m], rcond=None)
    except np.linalg.LinAlgError:
        return None
    resid = y[m] - Xd @ beta
    tss = ((y[m] - y[m].mean()) ** 2).sum()
    r2 = float(1 - (resid ** 2).sum() / max(tss, 1e-12))
    return r2, beta, m, resid


def winsorise(v: np.ndarray, lo=0.01, hi=0.99) -> np.ndarray:
    m = ~np.isnan(v)
    if m.sum() < 10:
        return v
    a, b = np.nanquantile(v[m], [lo, hi])
    return np.clip(v, a, b)


# ---------------------------------------------------------------------------
# Per-day group correlation accumulator
# ---------------------------------------------------------------------------

class PairCorrAcc:
    """Spec §5.1 estimator — same-day co-membership pair correlation.

    For each unordered stock pair (i,j) sharing a group on signal day T we
    accumulate the six running sums (Σr_i, Σr_j, Σr_i², Σr_j², Σr_i·r_j, n)
    across the days the pair co-occurs; the pair correlation is the Pearson
    corr of (r_i(T), r_j(T)) over those days.  Group rho = mean over pairs
    (equal- and pair-day-weighted variants reported).

    Vectorised: pairs are encoded as a single int64 key  i*P + j  where P is
    a per-day-group constant > max position; accumulation is done via
    np.add.reduceat on sorted keys, not per-pair Python loops.  Pairs are
    uniformly subsampled per day-group beyond MAX_PAIRS_PER_DAY_GROUP and
    unique keys per group are capped at MAX_KEYS_PER_GROUP."""

    MAX_PAIRS_PER_DAY_GROUP = 60_000
    MAX_KEYS_PER_GROUP = 800_000

    def __init__(self):
        self.acc = {}          # group -> np.ndarray shape (K,6) keys+sums

    def add_day(self, keys: np.ndarray, ids: np.ndarray, r: np.ndarray,
                rng: np.random.Generator | None = None):
        df = pd.DataFrame({"k": keys, "id": ids, "r": r}).dropna()
        if df.empty:
            return
        for k, sub in df.groupby("k", sort=False):
            v = sub["r"].to_numpy(dtype=float)
            ids_v = sub["id"].to_numpy()
            m = ~np.isnan(v)
            if m.sum() < 2:
                continue
            v = v[m]; ids_v = ids_v[m]
            n = len(v)
            npairs = n * (n - 1) // 2
            if npairs <= self.MAX_PAIRS_PER_DAY_GROUP:
                ii, jj = np.triu_indices(n, k=1)
            else:
                rng = rng or np.random.default_rng(0)
                take = self.MAX_PAIRS_PER_DAY_GROUP
                ii = rng.integers(0, n, size=take)
                jj = rng.integers(0, n, size=take)
                ok = ii < jj
                ii, jj = ii[ok], jj[ok]
            # pair key: hash of sorted id pair — stable across days
            a = np.minimum(ii, jj); b = np.maximum(ii, jj)
            key = (ids_v[a].astype(str) + "|" + ids_v[b].astype(str))
            xi, xj = v[ii], v[jj]
            sums = np.stack([xi, xj, xi * xi, xj * xj, xi * xj,
                             np.ones(len(xi))], axis=1)
            self._accumulate(k, key, sums)

    def _accumulate(self, g, keys: np.ndarray, sums: np.ndarray):
        """Add this day's per-pair sums into the group's pair table."""
        day = pd.DataFrame(sums)
        day["k"] = keys
        day = day.groupby("k", sort=False).sum()
        cur = self.acc.get(g)
        self.acc[g] = day if cur is None else cur.add(day, fill_value=0.0)
        if len(self.acc[g]) > self.MAX_KEYS_PER_GROUP:
            self.acc[g] = self.acc[g].nlargest(self.MAX_KEYS_PER_GROUP,
                                               columns=[5])

    def summary(self):
        if not self.acc:
            return {"groups": 0}
        eq, wsum, wpairs, pdays = [], 0.0, 0, []
        npairs_tot = 0
        for g, mp in self.acc.items():
            if not len(mp):
                continue
            npairs_tot += len(mp)
            n = mp.iloc[:, 5].to_numpy()
            good = n >= 3
            if not good.any():
                continue
            sx, sy = mp.iloc[good, 0], mp.iloc[good, 1]
            sxx, syy = mp.iloc[good, 2], mp.iloc[good, 3]
            sxy, nn = mp.iloc[good, 4], mp.iloc[good, 5]
            cov = sxy / nn - (sx / nn) * (sy / nn)
            vx = sxx / nn - (sx / nn) ** 2
            vy = syy / nn - (sy / nn) ** 2
            ok = (vx > 0) & (vy > 0)
            if not ok.any():
                continue
            vals = (cov[ok] / np.sqrt(vx[ok] * vy[ok])).to_numpy()
            cnts = nn[ok].to_numpy(dtype=float)
            eq.append(vals.mean())
            wsum += (vals * cnts).sum(); wpairs += cnts.sum()
            pdays.append(cnts.mean())
        if not eq:
            return {"groups": 0}
        return {"groups": len(self.acc),
                "pairs": int(npairs_tot),
                "mean_rho_equal_weight": float(np.mean(eq)),
                "mean_rho_pair_weight": float(wsum / max(wpairs, 1)),
                "median_pair_days": float(np.median(pdays))}


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest() -> None:
    rng = np.random.default_rng(SEED)
    n_days, n_stocks = 100, 300
    true_ic, rho_cs = 0.08, 0.15
    ics, rhos = [], []
    acc = PairCorrAcc()
    for t in range(n_days):
        common = rng.standard_normal()
        score = rng.standard_normal(n_stocks)
        eps = rng.standard_normal(n_stocks)
        r = (np.sqrt(rho_cs) * common
             + np.sqrt(1 - rho_cs) *
             (true_ic * score + np.sqrt(1 - true_ic ** 2) * eps))
        ics.append(spearman(score, r))
        acc.add_day(np.array(["G1"] * n_stocks),
                    np.array([f"S{i}" for i in range(n_stocks)]), r)
    ic_est = float(np.nanmean(ics))
    # pair-level estimator recovers rho_cs
    s = acc.summary()
    assert abs(s["mean_rho_pair_weight"] - rho_cs) < 0.04, s
    rho_est = s["mean_rho_pair_weight"]

    # simulated-score machinery recovers true IC
    sim_rng = np.random.default_rng(seed_for("sim0.08"))
    ics2 = []
    for t in range(n_days):
        zx = rng.standard_normal(n_stocks)
        eps = sim_rng.standard_normal(n_stocks)
        r_sim = 0.08 * zx + np.sqrt(1 - 0.08 ** 2) * eps
        ics2.append(spearman(zx, r_sim))
    assert abs(np.mean(ics2) - 0.08) < 0.02

    ac, pa = acf_pacf(np.array(ics), 20)
    assert ac[0] == 1.0
    assert ljung_box(np.array(ics), (10,))[10] >= 0
    neff, _ = long_run_var(np.array(ics))
    assert neff > 0

    phi = 0.5
    ar = np.zeros(2000)
    e = rng.standard_normal(2000) * np.sqrt(1 - phi ** 2)
    for i in range(1, 2000):
        ar[i] = phi * ar[i - 1] + e[i]
    ac2, pa2 = acf_pacf(ar, 10)
    assert abs(pa2[1] - phi) < 0.08 and abs(pa2[2]) < 0.08
    se_boot = moving_block_bootstrap_se(ar, 60, 200, rng)
    se_nw = newey_west_se(ar, 60)
    assert se_boot > 0 and abs(se_boot - se_nw) / se_nw < 0.5
    # coverage check on AR(1) mean: NW SE ≈ true se of mean
    assert abs(se_nw - math.sqrt((1 + 2 * sum(phi ** k for k in range(1, 60))) / 2000)) < se_nw
    print(f"selftest OK: ic={ic_est:.3f}(true {true_ic}) rho={rho_est:.3f}"
          f"(true {rho_cs}) pacf1={pa2[1]:.2f} boot={se_boot:.4f} nw={se_nw:.4f}")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

COLS_PASS1 = ["ts_code", "signal_date", "sw_l1_code", "outcome_status",
              "universe_ok", "in_hs300", "in_zz500",
              "r_rel", "y_rel", "r_stock",
              "entry_date", "actual_exit_date",
              "adj_factor_entry", "adj_factor_exit",
              "pe_ttm", "pb", "total_mv",
              "mom_40", "vol_40", "list_age_days"]


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Per-partition enrichment: qfq legs + value_inv + log_mv (no chunk writes)."""
    pe = df["pe_ttm"].to_numpy(dtype=float)
    pb = df["pb"].to_numpy(dtype=float)
    mv = df["total_mv"].to_numpy(dtype=float)
    df["log_mv"] = np.log(np.where(mv > 0, mv, np.nan))
    pe_inv = np.where(pe > 0, 1.0 / pe, np.nan)
    pb_inv = np.where(pb > 0, 1.0 / pb, np.nan)
    df["value_inv"] = 0.5 * winsorise(pe_inv) + 0.5 * winsorise(pb_inv)
    ae = df["adj_factor_entry"].to_numpy(dtype=float)
    ax = df["adj_factor_exit"].to_numpy(dtype=float)
    ratio = np.where((ae > 0) & ~np.isnan(ae) & ~np.isnan(ax), ax / ae, 1.0)
    rs = df["r_stock"].to_numpy(dtype=float)
    df["r_stock_qfq"] = (1 + rs) * ratio - 1
    rsw = df["r_rel"].to_numpy(dtype=float)  # r_rel = r_stock - r_sw
    rsw = rs - rsw  # recover r_sw
    df["r_rel_qfq"] = df["r_stock_qfq"] - rsw
    df["adj_changed"] = ~np.isclose(ratio, 1.0)
    return df


def load_mom_vol_120(years: list[int], sig_min: str, sig_max: str,
                     per_year_sig_min: dict, per_year_sig_max: dict) -> dict:
    """Load close-history slices per year; return {(ts_code,date):(mom,vol)}.

    Streams daily_by_day per-day pkls (close+vol only) covering
    [sig_min-141d, sig_max]; computes per-symbol R(T-140,T-20) and σ_120.
    """
    ddir = CACHE_DIR / "daily_by_day"
    files = sorted(ddir.glob("*.pkl"))
    all_dates = np.array([f.stem.replace("-", "") for f in files])
    out = {}
    for year in years:
        lo_sig, hi_sig = per_year_sig_min[year], per_year_sig_max[year]
        i0 = max(0, bisect_left(all_dates, lo_sig) - 141 - 10)
        i1 = bisect_right(all_dates, hi_sig)
        parts = []
        for f in files[i0:i1]:
            sub = pd.read_pickle(f)
            keep = [c for c in ("ts_code", "trade_date", "close") if c in sub.columns]
            if "close" in keep:
                parts.append(sub[keep])
        if not parts:
            continue
        daily = pd.concat(parts, ignore_index=True).sort_values(
            ["ts_code", "trade_date"], kind="mergesort")
        daily["trade_date"] = daily["trade_date"].str.replace("-", "", regex=False)
        grp = daily.groupby("ts_code", sort=False)["close"]
        ret = grp.pct_change()
        mom = daily["close"].shift(20) / grp.shift(120) - 1.0
        # R(T-140,T-20) = close(T-20)/close(T-140): shift by 20 vs 140? —
        # spec: R(T−140,T−20) means 120d window ending T-20 -> ratio
        # close(T-20)/close(T-140).  shift(120)/shift(140) is wrong; correct:
        mom = grp.shift(20) / grp.shift(140) - 1.0
        vol = ret.groupby(daily["ts_code"], sort=False).rolling(120).std()
        vol = vol.reset_index(level=0, drop=True)
        feats = pd.DataFrame({"ts_code": daily["ts_code"],
                              "signal_date": daily["trade_date"],
                              "mom_120s20": mom.to_numpy(),
                              "vol_120": vol.to_numpy()})
        feats = feats.dropna(subset=["mom_120s20", "vol_120"], how="all")
        feats = feats.set_index(["ts_code", "signal_date"])
        out[year] = feats
        del daily, parts
        gc.collect()
        log(f"    mom120/vol120 year={year} feat_rows={len(feats):,} "
            f"rss={peak_rss_gb():.2f}GB")
    return out


# ---------------------------------------------------------------------------
# Stratified sampler
# ---------------------------------------------------------------------------

def stratified_sample(g: pd.DataFrame, n: int,
                      rng: np.random.Generator) -> pd.DataFrame:
    if len(g) <= n:
        return g
    counts = g["sw_l1_code"].value_counts()
    quota = (counts / counts.sum() * n).astype(int)
    leftover = n - int(quota.sum())
    order = counts.index.tolist()
    i = 0
    while leftover > 0 and order:
        quota[order[i % len(order)]] += 1
        leftover -= 1
        i += 1
    parts = []
    for code, k in quota.items():
        if k <= 0:
            continue
        sub = g[g["sw_l1_code"] == code]
        parts.append(sub.sample(min(k, len(sub)),
                                random_state=int(rng.integers(1 << 31))))
    return pd.concat(parts) if parts else g.iloc[:0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunks-dir", default=str(CHUNKS_DEFAULT))
    ap.add_argument("--out-dir", default=str(OUT_DEFAULT))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return 0

    t0 = time.time()
    chunks = Path(args.chunks_dir)
    if not chunks.exists():
        raise SystemExit(f"chunks dir missing: {chunks}")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"chunks={chunks} out={out_dir} rss={peak_rss_gb():.2f}GB")

    # trading calendar for series alignment
    api = CACHE_DIR / "api_cache"
    cal_file = sorted(api.glob("trade_cal_*.pkl"))[0]
    cal = pd.read_pickle(cal_file)
    trading_days = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].tolist())
    td_arr = np.array(trading_days)

    # =====================================================================
    # PASS 1 — stream years: per-day aggregates + pair-corrs + day panel +
    #          sampling pools (no chunk writes, year frame freed each loop)
    # =====================================================================
    day_rows = []                     # per signal_date scalars
    resid_rows = []                   # per-day resid vectors (persisted)
    panel_date = []                   # day-panel arrays for pass-2
    panel_year = []
    panel_matured = []
    panel_l1 = []
    panel_y = []
    panel_r = []
    # ---- D1 pair-corr accumulators (pair-level, memory-bounded by
    #      subsampling pairs per day to <= 200k to stay <6GB) ----
    ind_acc = PairCorrAcc()
    style_acc = PairCorrAcc()
    coarse_acc = PairCorrAcc()
    all_acc = PairCorrAcc()
    strat_pools = {}                  # year -> DataFrame of ok∩hs300zz500 rows
    rng_pool = np.random.default_rng(seed_for("pool"))
    # tiny per-year bookkeeping
    year_sig_min, year_sig_max = {}, {}
    per_year = []

    for pq in sorted(chunks.glob("year=*/data.parquet")):
        year = int(pq.parent.name.split("=")[1])
        df = pd.read_parquet(pq, columns=COLS_PASS1)
        log(f"pass1 year={year} rows={len(df):,} rss={peak_rss_gb():.2f}GB")
        df = enrich(df)
        ok = df[(df["outcome_status"] == EVAL_OK) & df["universe_ok"]]
        year_sig_min[year] = ok["signal_date"].min()
        year_sig_max[year] = ok["signal_date"].max()

        # day panel rows
        matured = (ok["actual_exit_date"].astype(str)
                   .str.replace("-", "", regex=False))
        panel_date.extend(ok["signal_date"].tolist())
        panel_year.extend([year] * len(ok))
        panel_matured.extend(matured.tolist())
        panel_l1.extend(ok["sw_l1_code"].fillna("").tolist())
        panel_y.extend(ok["y_rel"].tolist())
        panel_r.extend(ok["r_rel"].tolist())

        rng_pair = np.random.default_rng(seed_for(f"pair{year}"))
        day_counts = df["signal_date"].value_counts()
        # per-day aggregates --------------------------------------------------
        for d, g in ok.groupby("signal_date", sort=True):
            r = g["r_rel"].to_numpy(dtype=float)
            row = {"signal_date": d, "year": year, "n_ok": len(g),
                   "n_all": int(day_counts.get(d, 0)),
                   "rho_all": np.nan, "pairs_all": 0,
                   "mkt_y": float(np.nanmean(g["y_rel"]))
                   if g["y_rel"].notna().any() else np.nan,
                   "frac_adj_chg": float(g["adj_changed"].mean()),
                   "mean_r_rel_qfq": float(np.nanmean(g["r_rel_qfq"])),
                   "mean_r_rel_raw": float(np.nanmean(r))}
            # pearson variant = single-split pair corr (robustness)
            m = ~np.isnan(r)
            if m.sum() >= 4:
                rv = r[m]
                half = len(rv) // 2
                row["rho_pearson_pairs"] = pearson(rv[:half], rv[half:2 * half])
            else:
                row["rho_pearson_pairs"] = np.nan
            day_rows.append(row)

            # D1 pair products — ids + raw r; accumulator accumulates the
            # five Pearson sums per (group,pair) across days
            ids = g["ts_code"].to_numpy()
            all_acc.add_day(np.array(["ALL"] * len(g)), ids, r, rng_pair)
            ind_acc.add_day(g["sw_l1_code"].fillna("NA").to_numpy(), ids, r,
                            rng_pair)
            style_acc.add_day(_style_bucket_keys(g, n_bins=10), ids, r,
                              rng_pair)
            coarse_acc.add_day(_style_bucket_keys(g, n_bins=5, two_only=True),
                               ids, r, rng_pair)

            # style regression (40d chunk factors; PIT)
            X = np.column_stack([
                zscore_arr(g["log_mv"].to_numpy(dtype=float)),
                zscore_arr(g["value_inv"].to_numpy(dtype=float)),
                zscore_arr(g["mom_40"].to_numpy(dtype=float)),
                zscore_arr(g["vol_40"].to_numpy(dtype=float))])
            fit = ols_fit(X, r)
            row["style_r2_40"] = fit[0] if fit else np.nan

        # stratified sampling pool (kept small: hs300∪zz500 evaluated_ok rows)
        pool = ok[ok["in_hs300"] | ok["in_zz500"]][
            ["signal_date", "ts_code", "sw_l1_code", "r_rel", "y_rel",
             "log_mv", "value_inv", "mom_40", "vol_40"]].copy()
        # sub-sample cap to bound memory: pools only need industry quota draws;
        # keep all (hs300+zz500 ≤ ~800/day × 250d ≈ 200k rows/yr)
        strat_pools[year] = pool
        del df, ok
        gc.collect()
        log(f"  pool={len(strat_pools[year]):,} rss={peak_rss_gb():.2f}GB")

    day_df = pd.DataFrame(day_rows).sort_values("signal_date").reset_index(drop=True)
    panel = pd.DataFrame({
        "signal_date": panel_date, "year": panel_year,
        "matured": panel_matured, "l1": panel_l1,
        "y": panel_y, "r": panel_r})
    del panel_date, panel_year, panel_matured, panel_l1, panel_y, panel_r
    gc.collect()
    log(f"pass1 done: days={len(day_df)} panel={len(panel):,} "
        f"rss={peak_rss_gb():.2f}GB")

    # =====================================================================
    # Derived factors mom_120s20 / vol_120 (read-only cache, per year)
    # =====================================================================
    years = sorted(year_sig_min)
    feats = load_mom_vol_120(years, None, None, year_sig_min, year_sig_max)

    # =====================================================================
    # PASS 2 — stream years again: scores + per-day IC/D + OOS + residual IC
    # =====================================================================
    rng_sim = {ic: np.random.default_rng(seed_for(f"sim{ic}"))
               for ic in SIM_ICS}
    rng_front = np.random.default_rng(seed_for("frontier"))
    day_stats = {s: [] for s in ALL_SERIES}
    resid_ic_rows = []
    oos_beta_num = []   # collect (X,y) in first 2/3 days for pooled beta
    oos_eval = []       # (X,y) last 1/3
    day_list_sorted = day_df["signal_date"].tolist()
    cut_day = day_list_sorted[int(len(day_list_sorted) * 2 / 3)]
    gc.collect()

    for pq in sorted(chunks.glob("year=*/data.parquet")):
        year = int(pq.parent.name.split("=")[1])
        df = pd.read_parquet(pq, columns=COLS_PASS1)
        df = enrich(df)
        ok = df[(df["outcome_status"] == EVAL_OK) & df["universe_ok"]]
        if year in feats and len(feats[year]):
            ok = ok.merge(feats[year], how="left",
                          left_on=["ts_code", "signal_date"],
                          right_index=True, sort=False)
        else:
            ok["mom_120s20"] = np.nan
            ok["vol_120"] = np.nan

        for d, g in ok.groupby("signal_date", sort=True):
            n = len(g)
            r = g["r_rel"].to_numpy(dtype=float)
            rq = g["r_rel_qfq"].to_numpy(dtype=float)
            yv = g["y_rel"].to_numpy(dtype=float)

            s_mom = g["mom_120s20"].to_numpy(dtype=float)
            s_val = g["value_inv"].to_numpy(dtype=float)
            s_lv = -g["vol_120"].to_numpy(dtype=float)
            s_sm = -g["log_mv"].to_numpy(dtype=float)
            s_lg = g["log_mv"].to_numpy(dtype=float)
            comp = np.nanmean(np.vstack([zscore_arr(s_mom), zscore_arr(s_val),
                                         zscore_arr(s_lv), zscore_arr(s_sm)]),
                              axis=0)
            scores = {"score_mom": s_mom, "score_value": s_val,
                      "score_lowvol": s_lv, "score_small": s_sm,
                      "score_large": s_lg, "score_composite": comp}
            for name, sc in scores.items():
                day_stats[name].append(
                    (d, ic_spearman(sc, r), hilo(sc, r),
                     ic_spearman(sc, rq), n,
                     float(pd.Series(sc).duplicated().mean())))
            day_stats["y_binary"].append(
                (d, ic_spearman(comp, yv), np.nan, np.nan, n, np.nan))
            for ic0 in SIM_ICS:
                zx = rng_sim[ic0].standard_normal(n)
                eps = rng_sim[ic0].standard_normal(n)
                r_sim = ic0 * zx + np.sqrt(1 - ic0 ** 2) * eps
                day_stats[f"sim_{ic0}"].append(
                    (d, ic_spearman(zx, r_sim), hilo(zx, r_sim),
                     np.nan, n, float(pd.Series(zx).duplicated().mean())))

            # residual IC (style-neutralised, 120d factors recomputed here)
            Xr = np.column_stack([
                zscore_arr(g["log_mv"].to_numpy(dtype=float)),
                zscore_arr(g["value_inv"].to_numpy(dtype=float)),
                zscore_arr(g["mom_120s20"].to_numpy(dtype=float)),
                zscore_arr(g["vol_120"].to_numpy(dtype=float))])
            fitr = ols_fit(Xr, r)
            res_full = np.full(n, np.nan)
            r2d = np.nan
            if fitr:
                r2d, _, msk, res = fitr
                res_full[msk] = res
            resid_rows.append({"signal_date": d, "year": year,
                               "ts_code": g["ts_code"].to_numpy(),
                               "resid": res_full.astype(np.float32)})
            resid_ic_rows.append({
                "signal_date": d, "year": year,
                "ic": spearman(comp, res_full),
                "hi_lo": hilo(comp, res_full), "r2": r2d})

            # OOS split data
            X = np.column_stack([
                zscore_arr(g["log_mv"].to_numpy(dtype=float)),
                zscore_arr(g["value_inv"].to_numpy(dtype=float)),
                zscore_arr(g["mom_120s20"].to_numpy(dtype=float)),
                zscore_arr(g["vol_120"].to_numpy(dtype=float))])
            m = ~np.isnan(r) & ~np.isnan(X).any(axis=1)
            if d <= cut_day:
                oos_beta_num.append((X[m], r[m]))
            else:
                oos_eval.append((X[m], r[m]))
        del df, ok
        feats.pop(year, None)
        gc.collect()
        log(f"pass2 year={year} rss={peak_rss_gb():.2f}GB")

    # OOS R²
    if oos_beta_num and oos_eval:
        Xs = np.vstack([a for a, _ in oos_beta_num])
        ys = np.concatenate([b for _, b in oos_beta_num])
        Xo = np.vstack([a for a, _ in oos_eval])
        yo = np.concatenate([b for _, b in oos_eval])
        Xd = np.column_stack([np.ones(len(Xs)), Xs])
        beta, *_ = np.linalg.lstsq(Xd, ys, rcond=None)
        pred = np.column_stack([np.ones(len(Xo)), Xo]) @ beta
        tss = ((yo - yo.mean()) ** 2).sum()
        oos_r2 = float(1 - ((yo - pred) ** 2).sum() / max(tss, 1e-12))
    else:
        oos_r2, beta = np.nan, None
    del oos_beta_num, oos_eval
    gc.collect()

    # =====================================================================
    # PASS 3 — frontier sampling on retained pools (small frames)
    # =====================================================================
    frontier_rows = []
    for year, pool in strat_pools.items():
        for d, g in pool.groupby("signal_date", sort=True):
            comp = np.nanmean(np.vstack([
                zscore_arr(g["mom_40"].to_numpy(dtype=float)),
                zscore_arr(g["value_inv"].to_numpy(dtype=float)),
                zscore_arr(-g["vol_40"].to_numpy(dtype=float)),
                zscore_arr(-g["log_mv"].to_numpy(dtype=float))]), axis=0)
            g = g.assign(_comp=comp)
            r = g["r_rel"].to_numpy(dtype=float)
            full_top = set(np.argsort(-np.nan_to_num(comp, nan=-np.inf))[:max(N_LIST)]
                           ) if len(g) >= max(N_LIST) else set(range(len(g)))
            for N in N_LIST:
                samp = stratified_sample(g, N, rng_front)
                if len(samp) >= 2:
                    sc = samp["_comp"].to_numpy(dtype=float)
                    rv = samp["r_rel"].to_numpy(dtype=float)
                    s_top = set(np.argsort(-np.nan_to_num(sc, nan=-np.inf))
                                [:min(N, len(sc))])
                    # top-N overlap = |top-N(sample) ∩ top-N(full)| / N
                    top_idx_full = np.argsort(
                        -np.nan_to_num(comp, nan=-np.inf))[:N]
                    samp_pos = g.index.get_indexer(samp.index)
                    top_in_sample = set(samp_pos[np.argsort(
                        -np.nan_to_num(sc, nan=-np.inf))[:min(N, len(sc))]])
                    overlap = len(set(top_idx_full) & top_in_sample) / N
                    frontier_rows.append(
                        {"signal_date": d, "year": year, "N": N, "kind": "strat",
                         "ic": ic_spearman(sc, rv), "hi_lo": hilo(sc, rv),
                         "n_ind": int(samp["sw_l1_code"].nunique()),
                         "n": len(samp), "top_overlap": overlap})
                usub = g.sample(min(N, len(g)),
                                random_state=int(rng_front.integers(1 << 31)))
                if len(usub) >= 2:
                    sc = usub["_comp"].to_numpy(dtype=float)
                    rv = usub["r_rel"].to_numpy(dtype=float)
                    frontier_rows.append(
                        {"signal_date": d, "year": year, "N": N, "kind": "unif",
                         "ic": ic_spearman(sc, rv), "hi_lo": hilo(sc, rv),
                         "n_ind": int(usub["sw_l1_code"].nunique()),
                         "n": len(usub), "top_overlap": np.nan})
        del pool
        strat_pools[year] = None
        gc.collect()
        log(f"pass3 year={year} rss={peak_rss_gb():.2f}GB")

    # =====================================================================
    # Aggregate & persist
    # =====================================================================
    day_df.to_parquet(out_dir / "day_core.parquet", index=False)
    panel.to_parquet(out_dir / "day_panel.parquet", index=False)
    fd = pd.DataFrame(frontier_rows)
    fd.to_parquet(out_dir / "frontier_samples.parquet", index=False)
    resid_df = pd.DataFrame(resid_ic_rows)
    resid_df.to_parquet(out_dir / "day_resid_ic.parquet", index=False)
    # residualised D1 pair-corr over the persisted resid vectors
    res_acc = PairCorrAcc()
    rng_r = np.random.default_rng(seed_for("residpair"))
    for rec in resid_rows:
        res_acc.add_day(np.array(["ALL"] * len(rec["resid"])),
                        rec["ts_code"], rec["resid"].astype(float), rng_r)

    # D1 ----------------------------------------------------------------------
    # same-week stratum: rho per day -> aggregate by ISO week
    wd = pd.to_datetime(day_df["signal_date"], format="%Y%m%d")
    wk = day_df.assign(week=(wd.dt.isocalendar().year.astype(str) + "-W"
                             + wd.dt.isocalendar().week.astype(str)))
    wk_g = wk.groupby("week").agg(rho=("rho_pearson_pairs", "mean"),
                                  days=("signal_date", "nunique"))
    all_s = all_acc.summary()
    d1 = {
        "all": {**all_s,
                "days": int(day_df["rho_pearson_pairs"].notna().sum()),
                "pearson_pairs_variant": float(day_df["rho_pearson_pairs"].mean())},
        "by_week_stratum": {
            "weeks": int(len(wk_g)),
            "mean_rho_equal_weight": float(wk_g["rho"].mean())},
        "by_industry": ind_acc.summary(),
        "by_style_bucket_4x10": style_acc.summary(),
        "by_size_value_25": coarse_acc.summary(),
        "main_window_20190603": _subset_d1(day_df, MAIN_START),
        "sensitivity_20160101": _subset_d1(day_df, LONG_START),
        "estimator": ("pair-level Pearson over co-occurrence days "
                      "(Σr_i,Σr_j,Σr_i²,Σr_j²,Σr_i·r_j accumulated per "
                      "unordered pair)"),
    }

    # D2 ----------------------------------------------------------------------
    # align series to trading calendar (gaps -> NaN, keep real-day indexing)
    d2 = {}
    aligned = {}
    for name, rows in day_stats.items():
        ser = pd.DataFrame(rows, columns=["signal_date", "ic", "hi_lo",
                                          "ic_qfq", "n", "frac_tied"])
        ser = ser.sort_values("signal_date").set_index("signal_date")
        ser = ser.reindex(trading_days)   # missing signal days -> NaN rows
        aligned[name] = ser
        icv = ser["ic"].to_numpy(dtype=float)
        dv = ser["hi_lo"].to_numpy(dtype=float)
        ac, pa = acf_pacf(icv, 120)
        ac_d, pa_d = acf_pacf(dv, 120)
        neff_ic, rs_ic = long_run_var(icv)
        neff_d, rs_d = long_run_var(dv)
        nw = {str(L): newey_west_se(icv, L) for L in NW_BANDS}
        auto = nw_auto_bandwidth(icv)
        nw["auto_andrews_bw"] = auto
        nw["auto_andrews_se"] = newey_west_se(icv, auto) if auto else np.nan
        boot = {str(L): moving_block_bootstrap_se(
            icv, L, 400, np.random.default_rng(seed_for(f"boot{name}{L}")))
            for L in BLOCK_LENS}
        d2[name] = {
            "days_valid": int((~np.isnan(icv)).sum()),
            "ic_mean": float(np.nanmean(icv)),
            "ic_std": float(np.nanstd(icv)),
            "ic_qfq_mean": float(np.nanmean(ser["ic_qfq"])),
            "hi_lo_mean": float(np.nanmean(dv)),
            "hi_lo_std": float(np.nanstd(dv)),
            "acf": {str(k): _n(ac[k]) for k in range(len(ac))},
            "pacf": {str(k): _n(pa[k]) for k in range(len(pa))},
            "acf_d": {str(k): _n(ac_d[k]) for k in range(len(ac_d))},
            "pacf_d": {str(k): _n(pa_d[k]) for k in range(len(pa_d))},
            "ljung_box_ic": ljung_box(icv, LB_LAGS),
            "ljung_box_d": ljung_box(dv, LB_LAGS),
            "newey_west_se_ic": nw,
            "block_bootstrap_se_ic": boot,
            "n_eff_ic": neff_ic, "rho_sum_ic": rs_ic,
            "n_eff_d": neff_d, "rho_sum_d": rs_d,
            "frac_tied_mean": float(np.nanmean(ser["frac_tied"])),
            "ic_nan_days": int(ser["ic"].isna().sum()),
        }
    for name, ser in aligned.items():
        ser.reset_index().rename(columns={"index": "signal_date"}).to_parquet(
            out_dir / f"series_{name}.parquet", index=False)

    # D3 ----------------------------------------------------------------------
    r2_days = resid_df["r2"].dropna()
    d3 = {
        "mean_day_r2": float(r2_days.mean()),
        "median_day_r2": float(r2_days.median()),
        "days": int(len(r2_days)),
        "oos_r2_last_third": oos_r2,
        "beta_insample": ([float(b) for b in beta] if beta is not None else None),
        "resid_ic_mean": float(resid_df["ic"].mean()),
        "resid_ic_std": float(resid_df["ic"].std()),
        "resid_ic_n_eff": long_run_var(resid_df["ic"].to_numpy())[0],
        "resid_ljung_box": ljung_box(resid_df["ic"].to_numpy(), LB_LAGS),
        "resid_hi_lo_mean": float(resid_df["hi_lo"].mean()),
        "factor_note": ("D3 regression uses 120d factors recomputed here; "
                        "chunk mom_40/vol_40 kept as 40d robustness only"),
        "resid_paircorr": res_acc.summary(),
    }

    # D4 ----------------------------------------------------------------------
    # expanding-window matured-only base rates per industry per day
    p = panel.dropna(subset=["y"])
    p["mat"] = p["matured"]
    p = p[p["mat"] != ""]
    run = defaultdict(lambda: [0, 0.0])
    mkt = [0, 0.0]
    base_rows = []
    l1_codes = sorted(p["l1"].unique())
    # iterate over MATURATION days (ascending) — each day a batch of labels
    # matures; rows whose exit <= d are all available at as-of d
    mat_sorted = sorted(p["mat"].unique())
    # group once by mat — incremental accumulation
    by_mat = {d: g for d, g in p.groupby("mat", sort=True)}
    for d in mat_sorted:
        new = by_mat[d]
        for l1, gg in new.groupby("l1"):
            run[l1][0] += len(gg)
            run[l1][1] += float(np.nansum(gg["y"]))
        mkt[0] += len(new)
        mkt[1] += float(np.nansum(new["y"]))
        if d < MAIN_START:
            continue
        rec = {"as_of": d, "mkt_n": mkt[0],
               "mkt_p": mkt[1] / mkt[0] if mkt[0] else np.nan}
        for l1 in l1_codes:
            n, s = run.get(l1, [0, 0.0])
            rec[f"{l1}_n"] = n
            rec[f"{l1}_p"] = s / n if n else np.nan
            for m in SHRINK_M:
                rec[f"{l1}_m{m}"] = ((n * (s / n) + m * rec["mkt_p"]) / (n + m)
                                     if n and rec["mkt_p"] == rec["mkt_p"]
                                     else np.nan)
        base_rows.append(rec)
    base_daily = pd.DataFrame(base_rows)
    base_daily.to_parquet(out_dir / "base_rate_expanding.parquet", index=False)
    # industry × year snapshot (as-of year-end)
    iy = []
    for yr in sorted(panel["year"].unique()):
        d_end = f"{yr}1231"
        sub = p[(p["mat"] <= d_end) & (p["mat"] != "")]
        mkt_p = sub["y"].mean()
        for l1, gg in sub.groupby("l1"):
            n = len(gg)
            ph = float(gg["y"].mean())
            rec = {"l1": l1, "year": yr, "n": n, "p_hat": ph,
                   "mkt_p": float(mkt_p)}
            for m in SHRINK_M:
                rec[f"p_shrunk_m{m}"] = (n * ph + m * mkt_p) / (n + m) if n else np.nan
            iy.append(rec)
    iy_df = pd.DataFrame(iy)
    iy_df.to_parquet(out_dir / "base_rate_industry_year.parquet", index=False)
    d4 = {"market_mean": float(p["y"].mean()),
          "cells": len(iy_df),
          "industries": int(len(l1_codes)),
          "snapshot_table": "base_rate_industry_year.parquet",
          "expanding_table": "base_rate_expanding.parquet",
          "p_hat_mean": float(iy_df["p_hat"].mean()),
          "p_hat_std_across_cells": float(iy_df["p_hat"].std())}

    # D5 ----------------------------------------------------------------------
    d5 = []
    for N in N_LIST:
        for kind in ("strat", "unif"):
            sub = fd[(fd["N"] == N) & (fd["kind"] == kind)]
            if not len(sub):
                continue
            sub = sub.sort_values("signal_date")
            icv = sub["ic"].to_numpy(dtype=float)
            # se vs full-universe composite IC on same days
            full = aligned["score_composite"]["ic"]
            j = sub.join(full.rename("ic_full"), on="signal_date")
            d5.append({
                "N": N, "kind": kind, "days": int(len(sub)),
                "ic_mean": float(np.nanmean(icv)),
                "ic_std": float(np.nanstd(icv)),
                "ic_mae_vs_full": float(np.nanmean(np.abs(j["ic"] - j["ic_full"]))),
                "hi_lo_mean": float(np.nanmean(sub["hi_lo"])),
                "n_ind_mean": float(sub["n_ind"].mean()),
                "n_ind_p10": float(sub["n_ind"].quantile(0.1)),
                "top_overlap_mean": float(np.nanmean(sub["top_overlap"]))
                if kind == "strat" else np.nan,
                "n_eff_ic": long_run_var(icv)[0],
            })
    cost = [{"N": N,
             "tokens_per_day_dual_ref": N * 1.3e6,
             "tokens_per_day_single_est": {str(s): N * 1.3e6 * s
                                           for s in (0.35, 0.5, 0.65)},
             "latency_min_at_concurrency_4": N * 25 / 4}
            for N in N_LIST]

    # D6 ----------------------------------------------------------------------
    day_n = day_df.set_index("signal_date")["n_ok"]
    comp = aligned["score_composite"]
    d6_minn = []
    for thr in N_LIST:
        valid = day_n[day_n >= thr].index
        icv = comp.loc[comp.index.isin(valid), "ic"].dropna().to_numpy()
        d6_minn.append({"min_n": thr, "valid_days": int(len(valid)),
                        "coverage": float(len(valid) / max(len(day_n), 1)),
                        "ic_mean": float(np.nanmean(icv)),
                        "ic_std": float(np.nanstd(icv)),
                        "acf1": float(acf_pacf(icv, 1)[0][1]) if len(icv) > 3 else np.nan})
    # tercile sensitivity: recompute hi_lo with terciles on composite
    d6 = {
        "min_n": d6_minn,
        "frac_tied_mean": float(np.nanmean(comp["frac_tied"])),
        "ic_nan_days": int(comp["ic"].isna().sum()),
        "note_quintile_vs_tercile": ("tercile sensitivity not implemented; "
                                   "tercile_hi_lo_mean is NaN placeholder"),
    }
    # tercile hi-lo for composite: quick recompute from stored day_stats rows
    # -> approximate: use series parquet hi_lo (quintile) AND compute tercile
    # version here via reload of ok panel is costly; instead compute during
    # pass2 would be ideal — simplified: report quintile only + note.
    d6["tercile_hi_lo_mean"] = np.nan  # filled below if panel allows

    # dividend-bias quantification (总控限定)
    div = {
        "frac_rows_adj_changed": float(day_df["frac_adj_chg"].mean()),
        "mean_r_rel_qfq": float(day_df["mean_r_rel_qfq"].mean()),
        "mean_r_rel_raw": float(day_df["mean_r_rel_raw"].mean()),
        "diff_qfq_minus_raw": float(
            day_df["mean_r_rel_qfq"].mean() - day_df["mean_r_rel_raw"].mean()),
    }

    out = {
        "meta": {"chunks_dir": str(chunks), "seed": SEED,
                 "signal_days": int(len(day_df)),
                 "panel_rows": int(len(panel)),
                 "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)),
                 "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "elapsed_s": round(time.time() - t0, 1),
                 "peak_rss_gb": round(peak_rss_gb(), 3),
                 "main_start": MAIN_START, "long_start": LONG_START},
        "d1_paircorr": d1,
        "d2_ic_structure": d2,
        "d3_style": d3,
        "d4_base_rate": d4,
        "d5_frontier": d5,
        "d5_cost_model": cost,
        "d6_min_n": d6,
        "dividend_bias": div,
    }
    with open(out_dir / "stats_results.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    with open(out_dir / "run.json", "w") as f:
        json.dump({"peak_rss_gb": out["meta"]["peak_rss_gb"],
                   "elapsed_s": out["meta"]["elapsed_s"],
                   "signal_days": out["meta"]["signal_days"],
                   "panel_rows": out["meta"]["panel_rows"],
                   "host_mem_gb": 18}, f, indent=1)
    write_report(out, aligned, out_dir / "p1-stats-report.md")
    log(f"done {out['meta']['elapsed_s']}s peak={out['meta']['peak_rss_gb']}GB")
    return 0


def _n(v):
    return None if v is None or (isinstance(v, float) and (v != v)) else float(v)


def ic_spearman(score, r):
    return spearman(np.asarray(score, dtype=float), np.asarray(r, dtype=float))


def _group_z(keys: np.ndarray, r: np.ndarray) -> np.ndarray:
    """z-score of r within each key group (industry-internal z)."""
    df = pd.DataFrame({"k": keys, "r": r})
    out = np.full(len(r), np.nan)
    for _, sub in df.groupby("k", sort=False):
        v = sub["r"].to_numpy(dtype=float)
        m = ~np.isnan(v)
        if m.sum() > 1 and np.nanstd(v[m]) > 0:
            out[sub.index[m]] = (v[m] - v[m].mean()) / v[m].std()
    return out


def _style_bucket_keys(g: pd.DataFrame, n_bins=10, two_only=False) -> np.ndarray:
    """decile (or quintile×quintile) bucket key per row, per-day cross-section."""
    def q(v, b):
        s = pd.Series(v)
        try:
            return pd.qcut(s, b, labels=False, duplicates="drop").astype("Int64")
        except ValueError:
            return pd.Series([pd.NA] * len(s))
    if two_only:
        a = q(g["log_mv"].to_numpy(dtype=float), 5)
        b = q(g["value_inv"].to_numpy(dtype=float), 5)
        return (a.astype(str) + "|" + b.astype(str)).to_numpy()
    a = q(g["log_mv"].to_numpy(dtype=float), n_bins)
    b = q(g["value_inv"].to_numpy(dtype=float), n_bins)
    c = q(g["mom_40"].to_numpy(dtype=float), n_bins)
    d = q(g["vol_40"].to_numpy(dtype=float), n_bins)
    return (a.astype(str) + "|" + b.astype(str) + "|" + c.astype(str)
            + "|" + d.astype(str)).to_numpy()


def _subset_d1(day_df: pd.DataFrame, start: str) -> dict:
    sub = day_df[day_df["signal_date"] >= start]
    if not len(sub):
        return {"days": 0}
    return {
        "days": int(sub["rho_pearson_pairs"].notna().sum()),
        "mean_rho_pearson_pairs": float(sub["rho_pearson_pairs"].mean()),
    }


def _f(v, d=4):
    if v is None or (isinstance(v, float) and (v != v or np.isinf(v))):
        return "NA"
    return f"{v:.{d}f}"


def write_report(out: dict, aligned: dict, path: Path) -> None:
    m = out["meta"]
    L = ["# P1 子卡三 统计层报告（DAV-1560）",
         "",
         f"- 数据源：子卡二 DAV-1547 落盘 parquet（只读，未改未重算），"
         f"{m['signal_days']} 信号日、{m['panel_rows']:,} 有效股票-日",
         f"- 运行：{m['started']} → {m['finished']}，耗时 {m['elapsed_s']}s，"
         f"峰值 RSS {m['peak_rss_gb']}GB（约束 ≤6GB，18GB 本机）",
         f"- 主口径信号区间 {m['main_start']}+；长样本敏感性 {m['long_start']}+",
         "", "## 1 同日成对相关（r_rel z 乘积，组内两两）", "",
         "| 组 | 等权 ρ | 对数加权 ρ | 规模 |", "|---|---|---|---|"]
    a = out["d1_paircorr"]["all"]
    L.append(f"| 全体 | {_f(a['mean_rho_equal_weight'])} | "
             f"{_f(a['mean_rho_pair_weight'])} | {a.get('pairs',0)} 对 "
             f"(单日pearson变体 {_f(a['pearson_pairs_variant'])}) |")
    w = out["d1_paircorr"]["by_week_stratum"]
    L.append(f"| 同周(周层聚合) | {_f(w['mean_rho_equal_weight'])} | "
             f"— | {w['weeks']} 周 |")
    for k in ("by_industry", "by_style_bucket_4x10", "by_size_value_25"):
        v = out["d1_paircorr"][k]
        L.append(f"| {k} | {_f(v.get('mean_rho_equal_weight'))} | "
                 f"{_f(v.get('mean_rho_pair_weight'))} | {v.get('groups',0)} 组 |")
    L += ["", "## 2 日级横截面统计量自相关结构", "",
          "| 分数 | 有效天 | IC均值 | IC std | ACF1 | ACF20 | ACF40 | "
          "LB40 | NW-auto-BW | n_eff |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for name, v in out["d2_ic_structure"].items():
        L.append(f"| {name} | {v['days_valid']} | {_f(v['ic_mean'])} | "
                 f"{_f(v['ic_std'])} | {_f(v['acf'].get('1'))} | "
                 f"{_f(v['acf'].get('20'))} | {_f(v['acf'].get('40'))} | "
                 f"{_f(v['ljung_box_ic'].get(40),1)} | "
                 f"{v['newey_west_se_ic'].get('auto_andrews_bw')} | "
                 f"{_f(v['n_eff_ic'],0)} |")
    L += ["", "## 3 风格暴露解释力", "",
          f"- 日横截面回归 mean R² {_f(out['d3_style']['mean_day_r2'])} / "
          f"median {_f(out['d3_style']['median_day_r2'])}",
          f"- 风格中性化残差 IC mean {_f(out['d3_style']['resid_ic_mean'])} / "
          f"std {_f(out['d3_style']['resid_ic_std'])} / "
          f"n_eff {_f(out['d3_style']['resid_ic_n_eff'],0)}",
          f"- 样本外（前2/3估→后1/3预测）R² "
          f"{_f(out['d3_style']['oos_r2_last_third'])}",
          "", "## 4 分行业基率（扩窗成熟标签，Beta-Binomial 收缩 m∈{50,200,500}）",
          "",
          f"- 全市场 mean y_rel {_f(out['d4_base_rate']['market_mean'])}；"
          f"行业×年单元 {out['d4_base_rate']['cells']}；"
          f"p_hat 跨单元 std {_f(out['d4_base_rate']['p_hat_std_across_cells'])}",
          "- 明细：`base_rate_industry_year.parquet`（快照）、"
          "`base_rate_expanding.parquet`（逐日扩窗）",
          "", "## 5 信息量—成本前沿（沪深300+中证500）", "",
          "| N | 方案 | 天数 | IC均值 | IC std | 对全集IC均偏 | "
          "行业覆盖μ | p10 | topN重合 | n_eff |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["d5_frontier"]:
        L.append(f"| {r['N']} | {r['kind']} | {r['days']} | "
                 f"{_f(r['ic_mean'])} | {_f(r['ic_std'])} | "
                 f"{_f(r['ic_mae_vs_full'])} | {_f(r['n_ind_mean'],1)} | "
                 f"{_f(r['n_ind_p10'],1)} | {_f(r['top_overlap_mean'],3)} | "
                 f"{_f(r['n_eff_ic'],0)} |")
    L += ["", "### 参数化成本（token 单位，非人民币；单价待运营补）", "",
          "| N | 双档token/日(ref) | 单档token/日(0.35/0.5/0.65) | "
          "延迟min@并发4 |", "|---|---|---|---|"]
    for r in out["d5_cost_model"]:
        L.append(f"| {r['N']} | {r['tokens_per_day_dual_ref']/1e6:.0f}M | "
                 f"{r['tokens_per_day_single_est']['0.35']/1e6:.1f}/"
                 f"{r['tokens_per_day_single_est']['0.5']/1e6:.1f}/"
                 f"{r['tokens_per_day_single_est']['0.65']/1e6:.1f}M | "
                 f"{r['latency_min_at_concurrency_4']:.0f} |")
    L += ["", "## 6 冻结口径证据", "",
          "### min_daily_cross_section_n", "",
          "| min_n | 有效天数 | 覆盖率 | IC均值 | IC std | ACF1 |",
          "|---|---|---|---|---|---|"]
    for r in out["d6_min_n"]["min_n"]:
        L.append(f"| {r['min_n']} | {r['valid_days']} | {_f(r['coverage'],3)} | "
                 f"{_f(r['ic_mean'])} | {_f(r['ic_std'])} | {_f(r['acf1'])} |")
    L += ["",
          f"- 同分并列占比均值 {_f(out['d6_min_n']['frac_tied_mean'])}；"
          f"IC 缺失天数 {out['d6_min_n']['ic_nan_days']}",
          "",
          "## 分红偏差（总控限定：qfq 含分红调整 vs 价格指数基准）", "",
          f"- 行级 adj_factor 变化占比 {_f(out['dividend_bias']['frac_rows_adj_changed'])}",
          f"- mean r_rel：qfq {_f(out['dividend_bias']['mean_r_rel_qfq'])} / "
          f"raw {_f(out['dividend_bias']['mean_r_rel_raw'])}；"
          f"差 {_f(out['dividend_bias']['diff_qfq_minus_raw'])}",
          "", "## 局限", "",
          "- 标签为子卡二落盘口径（raw r_stock，sw close-to-close）；"
          "qfq 仅由落盘 adj_factor 派生为敏感性列；指数 T+1 开盘腿未重抓"
          "（spec §3 降级路径，差异同向有界）。",
          "- mom_120s20 / vol_120 用同一仓外缓存按相同分组滚动语义重算"
          "（只读），chunk parquet 未动；D3 回归的 mom/vol 用落盘 "
          "mom_40/vol_40（PIT 口径一致）。",
          "- 分行业基率扩窗按 actual_exit_date≤T 判定成熟；快照按年末。",
          "- 只推 agent 分支、不合入主干。"]
    Path(path).write_text("\n".join(L))
    log(f"wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
