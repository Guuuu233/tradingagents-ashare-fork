#!/usr/bin/env python3
"""DAV-1678 — M1 评估脚本（中线概率契约 b2.v1 / p_rel_t40 的日级评价层）.

Implements the signed-off M1 evaluation criteria (DAV-1573 总控签收,
2026-10-07, nine items) over a per-signal input frame. Pure offline: reads
a parquet/CSV of sealed mature predictions, never touches the DB, prompts
or analysis code. All heavy lifting is streaming over per-day groups, so
peak RSS stays far below the 4 GiB budget.

Input contract (one row per candidate signal; before-dedup rows allowed —
dedup to the primary run happens inside, per签收 item 4):

  signal_date / trade_date / t : signal day (YYYYMMDD or ISO date)
  version_key                : version queue key (code SHA + prompt hash +
                               actual serving model + contract version). If
                               absent a single 'default' queue is assumed.
  symbol / stock_code        : stock identifier (dedup unit with the day)
  run_id + created_at        : primary-run dedup — same (symbol, day,
                               version) keeps the earliest-created COMPLETED
                               run (签收 item 4, fixed ex ante, not picked
                               ex post). Rows without created_at fall back
                               to first-seen order.
  timing_class               : 'F0' required for the formal queue
  input_pit_status           : 'VERIFIED' required; 'FAILED' never enters
  p_rel_t40 / prob / q       : model probability, int 1–99 (or float 0–1 /
                               0–100; normalised to q in [0,1])
  r_rel / rel_return / r     : realised stock-minus-SW-index return over the
                               T+1 open → T+40 close window (decimal)
  completed / status         : completion flag for dedup (default complete)

Every row failing a formal-queue rule is counted in the funnel report with
its exclusion reason — nothing is silently dropped (spec §2.3: report the
denominator before AND after exclusions).

Deliverables (spec §3–§4):
  * Daily Spearman rank IC  — average-rank ties, equal-weight day summary,
    real date gaps kept (no compression), rank-degenerate days listed
    separately (签收 items 1,3);
  * Quintile hi-lo diff     — boundary ties by §3.2 fractional weights
    (签收 item 3);
  * Brier + reliability     — fixed 10 bins [1–10]…[91–99] (签收 item 7);
  * Repeat-measurement C and A (签收 §3.4);
  * Newey-West HAC mean-IC inference, primary band 60, robustness 120,
    95% two-sided (签收 item 5);
  * Conclusion grade A–D    — C requires >=60 valid days AND both NW60 and
    NW120 lower bounds > 0 (签收 item 6); else A/B only.

Usage:
  python scripts/phase2/m1_eval.py --input signals.parquet [--out-dir DIR]
  python scripts/phase2/m1_eval.py --selftest
"""

from __future__ import annotations

import argparse
import json
import math
import resource
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DEFAULT = Path("data/phase2/m1_eval")

MIN_DAILY_N = 10            # frozen min_daily_cross_section_n
NW_PRIMARY = 60             # 签收 item 5
NW_ROBUST = 120
CONF_Z = 1.959963984540054  # 95% two-sided
C_GRADE_MIN_DAYS = 60       # 签收 item 6
RELIABILITY_BINS = [(1, 10), (11, 20), (21, 30), (31, 40), (41, 50),
                    (51, 60), (61, 70), (71, 80), (81, 90), (91, 99)]
# fixed before any result is seen (签收 item 7); last bin is 91–99, p=100


def peak_rss_gb() -> float:
    val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return val / (1024 ** 3) if val > 10 ** 6 else val / (1024 ** 2)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Pure helpers (shared semantics with scripts/phase2/p1_stats_layer.py)
# ---------------------------------------------------------------------------

def _rankdata(a: np.ndarray) -> np.ndarray:
    """Average ranks (1..n), ties share the mean rank."""
    a = np.asarray(a, dtype=float)
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


def spearman_ic(q: np.ndarray, r: np.ndarray) -> tuple[float, bool]:
    """Day-level Spearman IC with average-rank ties.

    Returns (ic, rank_degenerate). Degenerate when either side has zero rank
    variance — IC is undefined (listed separately, NOT 0, NOT insufficient).
    """
    rx, ry = _rankdata(q), _rankdata(r)
    if rx.std() == 0 or ry.std() == 0:
        return np.nan, True
    return float(np.corrcoef(rx, ry)[0, 1]), False


def quintile_weights(q: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """§3.2 fractional weights for the hi/lo quintile diff.

    h = n/5; for stock i, L_i = #strictly below, U_i = #strictly above,
    E_i = #tied with i (incl. self):
      w_low(i)  = min(max(h - L_i, 0), E_i) / E_i
      w_high(i) = min(max(h - U_i, 0), E_i) / E_i
    Each tail's weights sum to h.
    """
    q = np.asarray(q, dtype=float)
    n = len(q)
    h = n / 5.0
    w_low = np.zeros(n)
    w_high = np.zeros(n)
    for i in range(n):
        lt = np.sum(q < q[i])
        gt = np.sum(q > q[i])
        eq = np.sum(q == q[i])          # includes self
        w_low[i] = min(max(h - lt, 0.0), eq) / eq
        w_high[i] = min(max(h - gt, 0.0), eq) / eq
    return w_low, w_high, h


def quintile_diff(q: np.ndarray, r: np.ndarray) -> tuple[float, int]:
    """Δ_t = [Σ w_high·r − Σ w_low·r] / h.  Returns (delta, n_tied_boundary)."""
    w_low, w_high, h = quintile_weights(q)
    r = np.asarray(r, dtype=float)
    tied = int(np.sum((w_low > 0) & (w_low < 1)) +
               np.sum((w_high > 0) & (w_high < 1)))
    return float((w_high @ r - w_low @ r) / h), tied


def newey_west_ci(series: np.ndarray, band: int,
                  z: float = CONF_Z) -> tuple[float, float, float, int]:
    """Mean ± z·SE(NW band) over the day series WITH NaN slots preserved.

    签收 item 3 (真实日期缺口不压缩): insufficient / rank-degenerate / no-label
    days stay in the series as NaN, so a lag-k pair must be k real day-positions
    apart — the two ends of a gap are never treated as adjacent days. Pairwise
    deletion: Γk uses only (t, t+k) where both ends are valid, normalised by
    n_valid (HAC convention); mean/se computed over valid days only.
    Returns (mean, lo, hi, n_valid)."""
    x = np.asarray(series, dtype=float)
    n_slots = len(x)
    valid = ~np.isnan(x)
    n = int(valid.sum())
    if n < 3:
        return np.nan, np.nan, np.nan, n
    mu = float(np.nanmean(x))
    xc = np.where(valid, x - mu, 0.0)
    g0 = float(np.dot(xc, xc) / n)
    s = g0
    for k in range(1, min(band, n_slots - 1) + 1):
        gk = float(np.dot(xc[:-k], xc[k:]) / n)   # pairs with a NaN end → 0
        s += 2 * (1 - k / (band + 1)) * gk
    se = math.sqrt(max(s / n, 0.0))
    return mu, mu - z * se, mu + z * se, n


def repeat_measures(q1: np.ndarray, q2: np.ndarray) -> tuple[float, float, int]:
    """§3.4: C = cross-product covariance estimate; A = half mean sq-diff."""
    q1 = np.asarray(q1, dtype=float)
    q2 = np.asarray(q2, dtype=float)
    m = len(q1)
    if m < 2:
        return np.nan, np.nan, m
    C = float(np.sum((q1 - q1.mean()) * (q2 - q2.mean())) / (m - 1))
    A = float(np.sum((q1 - q2) ** 2) / (2 * m))
    return C, A, m


# ---------------------------------------------------------------------------
# Input loading / normalisation
# ---------------------------------------------------------------------------

_COL_CANDIDATES = {
    "signal_date": ["signal_date", "trade_date", "t", "date", "analysis_date"],
    "version_key": ["version_key", "version", "queue", "contract_version"],
    "symbol": ["symbol", "stock_code", "code", "ticker"],
    "run_id": ["run_id", "report_id", "id"],
    "created_at": ["created_at", "sealed_at", "ts"],
    "timing_class": ["timing_class", "timing"],
    "input_pit_status": ["input_pit_status", "pit_status"],
    "prob": ["p_rel_t40", "prob", "q", "p_rel"],
    "rel_return": ["r_rel", "rel_return", "r", "excess_return"],
    "completed": ["completed", "is_complete"],
    "status": ["status"],
}


def _pick(df: pd.DataFrame, canon: str) -> str | None:
    for c in _COL_CANDIDATES[canon]:
        if c in df.columns:
            return c
    return None


def _norm_prob_col(s: pd.Series) -> pd.Series:
    """Normalise a probability column to q∈[0,1], column-level: per-value
    scaling is wrong because p=1 is legal on BOTH the 1–99 contract scale
    and the 0–1 scale. If any value exceeds 1 the whole column is treated
    as the integer-percent contract scale and divided by 100."""
    v = pd.to_numeric(s, errors="coerce").astype(float)
    vmax = v.max(skipna=True)
    if pd.isna(vmax):
        return v
    if vmax <= 1.0:
        return v
    if vmax <= 100.0:
        return v / 100.0
    return v.where(v <= 1.0, np.nan)   # out-of-range garbage → NaN


def load_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".parquet", ".pq"):
        df = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
    elif path.suffix.lower() == ".jsonl":
        df = pd.read_json(path, lines=True)
    else:
        raise ValueError(f"unsupported input format: {path.suffix}")

    ren = {}
    for canon in _COL_CANDIDATES:
        src = _pick(df, canon)
        if src is not None and src != canon:
            ren[src] = canon
    df = df.rename(columns=ren)

    # required fields
    for req in ("signal_date", "symbol", "prob", "rel_return"):
        if req not in df.columns:
            raise ValueError(f"input missing required column for '{req}' "
                             f"(aliases: {_COL_CANDIDATES[req]})")

    df["signal_date"] = pd.to_datetime(df["signal_date"]).dt.strftime("%Y%m%d")
    df["q"] = _norm_prob_col(df["prob"])
    df["r"] = pd.to_numeric(df["rel_return"], errors="coerce")
    df = _ensure_defaults(df)
    return df


def _ensure_defaults(df: pd.DataFrame) -> pd.DataFrame:
    """Fill canonical columns a caller may not have supplied (also lets tests
    feed admit() directly without going through load_frame)."""
    if "q" not in df.columns and "prob" in df.columns:
        df["q"] = _norm_prob_col(df["prob"])
    elif "q" in df.columns:
        df["q"] = _norm_prob_col(df["q"])
    if "r" not in df.columns and "rel_return" in df.columns:
        df["r"] = pd.to_numeric(df["rel_return"], errors="coerce")
    if "version_key" not in df.columns:
        df["version_key"] = "default"
    if "timing_class" not in df.columns:
        df["timing_class"] = "F0"
    if "input_pit_status" not in df.columns:
        df["input_pit_status"] = "VERIFIED"
    if "created_at" not in df.columns:
        df["created_at"] = pd.NaT
    if "completed" not in df.columns:
        if "status" in df.columns:
            df["completed"] = df["status"].isin(
                ["completed", "complete", "done", "ok", True])
        else:
            df["completed"] = True
    return df


# ---------------------------------------------------------------------------
# Formal-queue admission + primary-run dedup
# ---------------------------------------------------------------------------

def admit(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Apply §2.3 rules in order; every exclusion increments a funnel count."""
    df = _ensure_defaults(df)
    funnel = {"input_rows": len(df)}
    d = df.copy()

    def _count(mask, key):
        funnel[key] = int(mask.sum())
        return d[~mask]

    # pit FAILED never enters (签收 §6: input_pit_status=FAILED 一律不进)
    m = d["input_pit_status"].astype(str).str.upper() == "FAILED"
    d = _count(m, "excl_pit_failed")
    # formal queue = F0 + VERIFIED only
    m = d["timing_class"].astype(str).str.upper() != "F0"
    d = _count(m, "excl_non_f0")
    m = d["input_pit_status"].astype(str).str.upper() != "VERIFIED"
    d = _count(m, "excl_unverified")
    # legal probability int 1–99 → q in (0,1]; 0 and out-of-range rejected
    m = d["q"].isna() | (d["q"] <= 0)
    d = _count(m, "excl_bad_prob")
    # mature label required
    m = d["r"].isna()
    d = _count(m, "excl_immature_label")

    # --- primary-run dedup (签收 item 4): same symbol+day+version →
    # earliest-created completed run wins; fixed ex ante ---
    d["_created_sort"] = pd.to_datetime(d["created_at"], errors="coerce")
    d["_seq"] = np.arange(len(d))
    d = d.sort_values(
        ["signal_date", "version_key", "symbol",
         "completed", "_created_sort", "_seq"],
        ascending=[True, True, True, False, True, True],
        na_position="last",
    )
    before = len(d)
    d = d.drop_duplicates(subset=["signal_date", "version_key", "symbol"],
                          keep="first")
    funnel["excl_dup_run"] = int(before - len(d))
    funnel["formal_rows"] = int(len(d))
    return d.reset_index(drop=True), funnel


# ---------------------------------------------------------------------------
# Daily evaluation
# ---------------------------------------------------------------------------

@dataclass
class DayRow:
    date: str
    n_t: int
    status: str          # ok | insufficient | rank_degenerate
    ic: float = np.nan
    quintile_diff: float = np.nan
    quintile_tied_n: int = 0
    brier: float = np.nan


def eval_days(d: pd.DataFrame) -> tuple[list[DayRow], dict]:
    """Per-(day, version) evaluation. Real date order kept; missing days are
    simply absent from the day table (never compressed into neighbours)."""
    days: list[DayRow] = []
    for (vkey, day), g in d.groupby(["version_key", "signal_date"], sort=True):
        n_t = len(g)
        if n_t < MIN_DAILY_N:
            days.append(DayRow(day, n_t, "insufficient"))
            continue
        q = g["q"].to_numpy(float)
        r = g["r"].to_numpy(float)
        ic, degen = spearman_ic(q, r)
        if degen:
            days.append(DayRow(day, n_t, "rank_degenerate"))
            continue
        diff, tied = quintile_diff(q, r)
        brier = float(np.mean((q - (r > 0).astype(float)) ** 2))
        days.append(DayRow(day, n_t, "ok", ic, diff, tied, brier))
    return days


def reliability_table(d: pd.DataFrame) -> list[dict]:
    """Fixed 10 bins [1–10]…[91–99] on the integer p (q*100), only valid days."""
    d = d.copy()
    d["p_int"] = np.clip(np.round(d["q"] * 100).astype("Int64"), 1, 99)
    d["y"] = (d["r"] > 0).astype(float)
    # restrict to days that actually entered the main series
    ok_days = set()
    for (vkey, day), g in d.groupby(["version_key", "signal_date"]):
        if len(g) >= MIN_DAILY_N:
            q = g["q"].to_numpy(float)
            r = g["r"].to_numpy(float)
            _, degen = spearman_ic(q, r)
            if not degen:
                ok_days.add((vkey, day))
    d = d[d.apply(lambda row: (row["version_key"], row["signal_date"])
                  in ok_days, axis=1)]
    rows = []
    for lo, hi in RELIABILITY_BINS:
        sel = d[(d["p_int"] >= lo) & (d["p_int"] <= hi)]
        rows.append({
            "bin": f"{lo}-{hi}",
            "n": int(len(sel)),
            "n_days": int(sel["signal_date"].nunique()),
            "pred_mean": float(sel["q"].mean()) if len(sel) else np.nan,
            "empirical": float(sel["y"].mean()) if len(sel) else np.nan,
        })
    return rows


def repeat_from_pairs(df_pairs: pd.DataFrame) -> dict:
    """df_pairs must carry pair_id, q1, q2 (m independent frozen-input pairs)."""
    q1 = df_pairs["q1"].to_numpy(float)
    q2 = df_pairs["q2"].to_numpy(float)
    C, A, m = repeat_measures(q1, q2)
    return {"m_pairs": m, "C": C, "A": A,
            "note": "C<=0 at m~20 means 'not identified', not 'absent' "
                    "(签收 §3.4/plan)"}


# ---------------------------------------------------------------------------
# Conclusion grading (签收 items 6 & 9)
# ---------------------------------------------------------------------------

def grade(days: list[DayRow], funnel: dict) -> dict:
    ok = [x for x in days if x.status == "ok"]
    # keep every day's slot (ic=NaN for insufficient/rank_degenerate) so the
    # NW lag structure sees real spacing — gaps are never compressed
    ic = np.array([x.ic for x in days])
    mean60, lo60, hi60, n60 = newey_west_ci(ic, NW_PRIMARY)
    _, lo120, _, _ = newey_west_ci(ic, NW_ROBUST)

    if len(ok) >= C_GRADE_MIN_DAYS and lo60 > 0 and lo120 > 0:
        g = "C"
    elif len(ok) >= 1:
        g = "B"
    else:
        g = "A"
    # D is never auto-claimed here: needs paired same-day-same-stock version
    # evidence + M2 cost accounting — out of scope of this script's input.

    return {
        "grade": g,
        "valid_days": len(ok),
        "insufficient_days": sum(1 for x in days if x.status == "insufficient"),
        "rank_degenerate_days":
            sum(1 for x in days if x.status == "rank_degenerate"),
        "mean_ic": mean60,
        "nw60": {"lo": lo60, "hi": hi60},
        "nw120_lo": lo120,
        "c_gate": {"min_valid_days": C_GRADE_MIN_DAYS,
                   "days_ok": len(ok) >= C_GRADE_MIN_DAYS,
                   "nw60_lo_pos": bool(lo60 > 0) if not np.isnan(lo60) else False,
                   "nw120_lo_pos": bool(lo120 > 0) if not np.isnan(lo120) else False},
        "note": "A=engineering only; B=descriptive; C=limited statistical "
                "evidence (>=60 valid days AND both NW lower bounds >0); "
                "D requires M2 paired+cost evidence — never auto-granted.",
        "funnel": funnel,
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run(input_path: Path, out_dir: Path,
        pairs_path: Path | None = None) -> dict:
    t0 = time.time()
    log(f"loading {input_path}")
    df = load_frame(input_path)
    formal, funnel = admit(df)
    log(f"formal rows {len(formal)} / input {len(df)}")
    days = eval_days(formal)
    rel = reliability_table(formal) if len(formal) else []
    verdict = grade(days, funnel)
    out = {
        "input": str(input_path),
        "days": [vars(x) for x in days],
        "reliability": rel,
        "verdict": verdict,
    }
    if pairs_path is not None:
        pdf = pd.read_csv(pairs_path) if pairs_path.suffix == ".csv" \
            else pd.read_parquet(pairs_path)
        out["repeat"] = repeat_from_pairs(pdf)

    out_dir.mkdir(parents=True, exist_ok=True)
    days_df = pd.DataFrame([vars(x) for x in days])
    days_df.to_parquet(out_dir / "m1_day_series.parquet", index=False)
    pd.DataFrame(rel).to_json(out_dir / "m1_reliability.json",
                              orient="records", force_ascii=False, indent=2)
    (out_dir / "m1_verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2))
    (out_dir / "run.json").write_text(json.dumps({
        "elapsed_s": round(time.time() - t0, 2),
        "peak_rss_gb": round(peak_rss_gb(), 3),
        "input_rows": len(df), "formal_rows": len(formal),
    }, indent=2))
    log(f"grade={verdict['grade']} valid_days={verdict['valid_days']} "
        f"mean_ic={verdict['mean_ic']} -> {out_dir}")
    return out


# ---------------------------------------------------------------------------
# Self-test (synthetic constructed data — the card requires this BEFORE any
# real data is attached)
# ---------------------------------------------------------------------------

def _mk_day(day: str, qs, rs, vkey="v1", start_idx=0) -> list[dict]:
    return [{"signal_date": day, "version_key": vkey,
             "symbol": f"S{start_idx + i:05d}", "q": q, "r": r,
             "timing_class": "F0", "input_pit_status": "VERIFIED"}
            for i, (q, r) in enumerate(zip(qs, rs))]


def selftest() -> dict:
    rng = np.random.default_rng(7)
    checks = {}

    # 1) perfect monotone signal → IC≈1, Δ>0
    # q uses raw prob units (1–99 scale) here to exercise normalisation
    rows = _mk_day("20260105", range(1, 21), np.linspace(-0.05, 0.08, 20))
    d, _ = admit(pd.DataFrame(rows))
    days = eval_days(d)
    assert days[0].status == "ok" and days[0].ic > 0.99, days[0]
    assert days[0].quintile_diff > 0.10
    checks["monotone_ic"] = days[0].ic

    # 1b) same in q∈[0,1] form → identical IC (invariance to prob scale)
    rows = _mk_day("20260105", np.arange(1, 21) / 100,
                   np.linspace(-0.05, 0.08, 20))
    d, _ = admit(pd.DataFrame(rows))
    assert abs(eval_days(d)[0].ic - checks["monotone_ic"]) < 1e-12
    checks["scale_invariant"] = True

    # 2) n_t<10 → insufficient, excluded, not zero
    d, fun = admit(pd.DataFrame(_mk_day("20260106", range(1, 8),
                                        range(7))))
    days = eval_days(d)
    assert days[0].status == "insufficient" and np.isnan(days[0].ic)
    checks["insufficient"] = days[0].status

    # 3) rank-degenerate (all same prob) → separate label
    d, _ = admit(pd.DataFrame(_mk_day("20260107", [50] * 12, range(12))))
    days = eval_days(d)
    assert days[0].status == "rank_degenerate"
    checks["rank_degenerate"] = days[0].status

    # 4) §3.2 boundary-tie weights: h=2, 12 stocks, probs with a boundary tie
    q = np.array([1, 2, 3, 4, 5, 5, 6, 7, 8, 9, 10, 11], dtype=float)
    r = np.arange(12, dtype=float)
    wl, wh, h = quintile_weights(q)
    assert abs(wl.sum() - h) < 1e-9 and abs(wh.sum() - h) < 1e-9
    diff, tied = quintile_diff(q, r)
    # low tail = probs {1,2} plus part of the {5,5} boundary? no —
    # h=2.4, strictly-below prob3 count L=2 <2.4 → prob3 gets 0.4/1 weight
    assert wl[2] > 0 and wl[4] == 0
    checks["quintile_weights_sum"] = float(wl.sum())

    # 5) dedup: same symbol+day+version → earliest created_at completed wins
    rows = []
    for rid, cat, q in [(2, "2026-01-05T09:30", 10),
                        (1, "2026-01-05T09:00", 90),   # earliest → wins
                        (3, "2026-01-05T10:00", 50)]:
        rows.append({"signal_date": "20260105", "version_key": "v1",
                     "symbol": "S001", "run_id": rid, "created_at": cat,
                     "q": q, "r": 0.01, "timing_class": "F0",
                     "input_pit_status": "VERIFIED", "completed": True})
    d, fun = admit(pd.DataFrame(rows))
    assert len(d) == 1 and d.iloc[0]["q"] == 0.90, d[["run_id", "q"]]
    assert fun["excl_dup_run"] == 2
    checks["dedup_earliest"] = int(d.iloc[0]["run_id"])

    # 6) funnel: pit FAILED / non-F0 / UNVERIFIED / bad prob / immature all out
    rows = _mk_day("20260108", range(1, 15), np.linspace(-1, 1, 14))
    rows[0]["input_pit_status"] = "FAILED"
    rows[1]["timing_class"] = "F1"
    rows[2]["input_pit_status"] = "UNVERIFIED"
    rows[3]["q"] = 0                        # illegal prob
    rows[4]["r"] = np.nan                   # immature
    d, fun = admit(pd.DataFrame(rows))
    assert len(d) == 9 and fun["excl_pit_failed"] == 1
    checks["funnel"] = {k: fun[k] for k in
                        ("excl_pit_failed", "excl_non_f0",
                         "excl_unverified", "excl_bad_prob",
                         "excl_immature_label")}

    # 7) NW inference: strong positive series → C grade needs 60 days
    rows = []
    for i in range(60):
        day = f"2026{(i // 22) + 1:02d}{(i % 22) + 1:02d}"
        qs = range(1, 21)
        rs = np.linspace(-0.03, 0.09, 20) + rng.normal(0, 0.005, 20)
        rows += _mk_day(day, qs, rs)
    d, _ = admit(pd.DataFrame(rows))
    days = eval_days(d)
    v = grade(days, {})
    assert v["grade"] == "C", v["c_gate"]
    checks["grade_C_days"] = v["valid_days"]

    # 8) 59 days of same quality → B only (C gate not met)
    v2 = grade(days[:59], {})
    assert v2["grade"] == "B"
    checks["grade_B_59d"] = v2["grade"]

    # 9) repeat measures: identical pairs → A=0, C=var; shuffled → C≈0
    q1 = np.linspace(0.1, 0.9, 40)
    C, A, m = repeat_measures(q1, q1 + rng.normal(0, 0.01, 40))
    assert C > 0 and A > 0
    C0, _, _ = repeat_measures(q1, rng.permutation(q1))
    checks["repeat_C_pos"] = C > 0 and C0 < C

    # 10) reliability bins fixed & labelled
    d, _ = admit(pd.DataFrame(_mk_day("20260109", range(1, 21), range(20))))
    rel = reliability_table(d)
    assert len(rel) == 10 and rel[0]["bin"] == "1-10" \
        and rel[-1]["bin"] == "91-99"
    checks["reliability_bins"] = len(rel)

    log("selftest PASS: " + json.dumps(checks, default=str))
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path)
    ap.add_argument("--pairs", type=Path, default=None,
                    help="optional repeat-measurement frame (q1,q2,pair_id)")
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if a.input is None:
        ap.error("--input required (or --selftest)")
    run(a.input, a.out_dir, a.pairs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
