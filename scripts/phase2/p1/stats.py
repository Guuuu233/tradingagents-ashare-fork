"""Statistics for P1 study (DAV-1478). Pure numpy/pandas; no scipy/statsmodels."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Correlation helpers (no scipy)
# ---------------------------------------------------------------------------

def _rankdata(a: np.ndarray) -> np.ndarray:
    """Average ranks, ties share mid-rank (matches scipy.stats.rankdata)."""
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1)
    # average ties
    sorted_a = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sorted_a[j + 1] == sorted_a[i]:
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
    return float(np.corrcoef(x[m], y[m])[0, 1])


def zscore(s: pd.Series) -> pd.Series:
    mu, sd = s.mean(), s.std()
    if sd == 0 or pd.isna(sd):
        return pd.Series(np.nan, index=s.index)
    return (s - mu) / sd


# ---------------------------------------------------------------------------
# D1: same-day pairwise correlation of r_rel within a group
# ---------------------------------------------------------------------------

def same_day_paircorr(
    labels: pd.DataFrame,
    group_col: Optional[str] = None,
    min_n: int = 2,
) -> pd.DataFrame:
    """Per signal_day, mean of z_i*z_j over unordered pairs within group.

    z is the cross-sectional z-score of r_rel on that day (within group if
    group_col set, else whole day). Returns per-day rho and pair counts.
    """
    df = labels[labels["outcome"] == "evaluated_ok"].copy()
    keys = ["signal_date"] + ([group_col] if group_col else [])
    recs = []
    for _, g in df.groupby(keys):
        if len(g) < min_n:
            continue
        z = zscore(g["r_rel"]).dropna()
        if len(z) < min_n:
            continue
        # mean of z_i*z_j over unordered pairs = ( (sum z)^2 - sum z^2 ) / (n(n-1))
        s, s2, n = z.sum(), (z ** 2).sum(), len(z)
        rho = (s * s - s2) / (n * (n - 1))
        recs.append({
            "signal_date": g["signal_date"].iloc[0],
            **({group_col: g[group_col].iloc[0]} if group_col else {}),
            "n": n, "rho": rho, "pairs": n * (n - 1) // 2,
        })
    return pd.DataFrame(recs)


# ---------------------------------------------------------------------------
# D2: daily cross-sectional statistic series + autocorrelation
# ---------------------------------------------------------------------------

def daily_ic(labels: pd.DataFrame, score_col: str) -> pd.DataFrame:
    """Spearman IC of score_col vs r_rel per signal_day."""
    df = labels[labels["outcome"] == "evaluated_ok"].copy()
    rows = []
    for d, g in df.groupby("signal_date"):
        ic = spearman(g[score_col].to_numpy(dtype=float), g["r_rel"].to_numpy(dtype=float))
        q = g[score_col].rank(pct=True)
        hi = g.loc[q >= 0.8, "r_rel"].mean()
        lo = g.loc[q <= 0.2, "r_rel"].mean()
        rows.append({
            "signal_date": d, "ic": ic, "hi_lo": (hi - lo) if pd.notna(hi) and pd.notna(lo) else np.nan,
            "n": len(g), "n_score_nonnull": int(g[score_col].notna().sum()),
            "frac_tied_score": float((g[score_col].duplicated()).mean()),
        })
    return pd.DataFrame(rows)


def acf(series: np.ndarray, max_lag: int) -> np.ndarray:
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < max_lag + 2:
        return np.full(max_lag + 1, np.nan)
    x = x - x.mean()
    denom = np.dot(x, x)
    out = np.empty(max_lag + 1)
    out[0] = 1.0
    for k in range(1, max_lag + 1):
        out[k] = np.dot(x[:-k], x[k:]) / denom
    return out


def newey_west_se(series: np.ndarray, max_lag: int) -> float:
    """HAC standard error of the mean with Bartlett kernel, lag = max_lag."""
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return np.nan
    x = x - x.mean()
    gamma0 = np.dot(x, x) / n
    s = gamma0
    for k in range(1, min(max_lag, n - 1) + 1):
        w = 1 - k / (max_lag + 1)
        s += 2 * w * np.dot(x[:-k], x[k:]) / n
    var_mean = s / n
    return float(np.sqrt(max(var_mean, 0)))


def ljung_box(series: np.ndarray, lags: Sequence[int]) -> Dict[int, float]:
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    maxlag = max(lags)
    if n < maxlag + 2:
        maxlag = n - 2
    a = acf(x, maxlag)
    out = {}
    for h in lags:
        if h > maxlag or n <= h:
            out[h] = np.nan
            continue
        out[h] = float(n * (n + 2) * sum(a[k] ** 2 / (n - k) for k in range(1, h + 1) if not np.isnan(a[k])))
    return out


def effective_n(series: np.ndarray, max_lag: Optional[int] = None) -> float:
    """Long-run variance correction: n_eff = n / (1 + 2*sum rho_k)."""
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return np.nan
    L = max_lag if max_lag is not None else int(np.sqrt(n))
    a = acf(x, L)
    rho_sum = 1 + 2 * np.nansum(a[1:])
    return float(n / rho_sum) if rho_sum > 0 else np.nan


# ---------------------------------------------------------------------------
# D3: style factors + neutralisation
# ---------------------------------------------------------------------------

def add_style_factors(panel: pd.DataFrame) -> pd.DataFrame:
    """Compute size, value, momentum, vol from daily_basic / price history.

    Expects panel columns incl: ts_code, signal_date, plus factor source cols
    already merged (log_mv, pe_ttm_inv, pb_inv, mom, vol120). All factors
    cross-sectionally z-scored per signal_date.
    """
    df = panel.copy()
    for col in ("log_mv", "pe_ttm_inv", "pb_inv", "mom", "vol120"):
        if col in df.columns:
            df[f"z_{col}"] = df.groupby("signal_date")[col].transform(zscore)
    return df


def style_residualise(labels: pd.DataFrame, factor_cols: List[str]) -> pd.DataFrame:
    """Per-day OLS residual of r_rel on z-scored factors (cross-sectional)."""
    df = labels[labels["outcome"] == "evaluated_ok"].copy()
    resid = pd.Series(np.nan, index=df.index)
    for d, g in df.groupby("signal_date"):
        X = g[factor_cols].to_numpy(dtype=float)
        y = g["r_rel"].to_numpy(dtype=float)
        mask = ~np.isnan(y) & ~np.isnan(X).any(axis=1)
        if mask.sum() < len(factor_cols) + 2:
            continue
        Xd = np.column_stack([np.ones(mask.sum()), X[mask]])
        try:
            beta, *_ = np.linalg.lstsq(Xd, y[mask], rcond=None)
        except np.linalg.LinAlgError:
            continue
        r2 = 1 - ((y[mask] - Xd @ beta) ** 2).sum() / max(((y[mask] - y[mask].mean()) ** 2).sum(), 1e-12)
        resid.loc[g.index[mask]] = y[mask] - Xd @ beta
        df.loc[g.index[mask], "r2_day"] = r2
    df["r_rel_resid"] = resid
    return df


# ---------------------------------------------------------------------------
# D5: industry-stratified sampling + parametric cost frontier
# ---------------------------------------------------------------------------

def stratified_sample(g: pd.DataFrame, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Industry-stratified random sample of n rows (quota prop to industry size)."""
    if len(g) <= n:
        return g
    counts = g["l1_code"].value_counts()
    quota = (counts / counts.sum() * n).astype(int)
    # round-robin the leftover slots
    leftover = n - quota.sum()
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
        sub = g[g["l1_code"] == code]
        parts.append(sub.sample(min(k, len(sub)), random_state=rng.integers(1 << 31)))
    return pd.concat(parts) if parts else g.iloc[:0]


def cost_frontier_rows(n_list, per_report_tokens=1.3e6, single_tier_share=0.5,
                       reports_per_day=1, concurrency=4, minutes_per_report=25):
    """Parametric cost model; no fabricated currency. Returns rows per N.

    token model: dual-tier ~1.3M token/report (gemini historical ref).
    Single-tier usage parameterised as single_tier_share * 1.3M.
    Fees expressed in token units, not CNY (price inputs left for ops).
    Latency = N * minutes_per_report / concurrency (minutes).
    """
    rows = []
    for n in n_list:
        dual_tok = n * reports_per_day * per_report_tokens
        single_tok = n * reports_per_day * per_report_tokens * single_tier_share
        rows.append({
            "N": n,
            "tokens_per_day_dual_ref": dual_tok,
            "tokens_per_day_single_est": single_tok,
            "latency_min_at_concurrency_4": n * minutes_per_report / concurrency,
        })
    return rows
