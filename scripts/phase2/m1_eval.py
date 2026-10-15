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
  sealed_at                  : seal timestamp — the DAV-1991 封存时点门 field.
                               The formal queue only takes rows sealed
                               STRICTLY before the signal day's next trading
                               day 09:30 Asia/Shanghai; late-sealed /
                               backfilled rows are counted as excl_late_seal
                               and stay in the funnel diagnostics only.
                               Time-zone口径 (总控 10-13 令): sealed_at /
                               seal_basis_at / created_at are timezone-NAIVE
                               UTC (ledger tail "2026-10-12 11:45:05" =
                               Beijing 19:45) — parsed as UTC, converted to
                               Asia/Shanghai, compared with T+1 09:30 in
                               that zone; never read as local time.
                               Needs --calendar to resolve T+1 (a weekday
                               fallback is refused); frames without a
                               sealed_at column predate the gate and are
                               reported as seal_gate_applied=false.
                               Shape (DAV-2004 / DAV-1999 review 🟢 S-3):
                               only the forms daily_snapshot_ledger._parse_dt
                               accepts (leading YYYY-MM-DD) are read; anything
                               else counts as seal_unparseable and fails
                               closed rather than being guessed into a
                               UTC-midnight instant. A signal day that the
                               calendar cannot resolve a T+1 for — the
                               calendar ends first, OR the signal day precedes
                               the calendar's first day — is EXCLUDED from the
                               formal queue and counted as
                               excl_seal_t1_unresolved (DAV-2007 review M-2 /
                               总控 10-13 令 rework): _classify_timing's F0
                               for the same case is the ledger's classification
                               口径, not the formal score's admission rule —
                               a record whose T+1 entry open cannot be
                               resolved cannot prove it was sealed before it.
  p_rel_t40 / prob / q       : model probability, int 1–99 (or float 0–1 /
                               0–100; normalised to q in [0,1])
  r_rel / rel_return / r     : realised stock-minus-SW-index return over the
                               T+1 open → T+40 close window (decimal)
  completed / status         : completion flag for dedup (default complete)

Every row failing a formal-queue rule is counted in the funnel report with
its exclusion reason — nothing is silently dropped (spec §2.3: report the
denominator before AND after exclusions).

封存时点门 (DAV-1991, 总控 10-13 令): the formal queue re-derives the timing
rule from the record's own `sealed_at` instead of trusting the frame's
`timing_class` label — the label is ledger-produced data that may be absent
(the default is F0) or stale, and a mislabeled F0 row is exactly the
look-ahead leak the order closes. `sealed_at` must be strictly earlier than
the T+1 trading-day open 09:30 (Asia/Shanghai).

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

**Single-version rule (签收 item 4 / review DAV-1682 🔴):** the main day
series is exactly one version queue. If the input frame carries more than
one `version_key` the run fails closed unless `--version-key` names the one
to use. Cross-version comparison is a separate paired channel (M3) — never
mixed into the same IC series.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import resource
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Sibling module for the DAV-2097③ pit_reattest lookup contract.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_reattest  # noqa: E402

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
# Fixed P4 account (controller order 2026-10-09): ledger-derived frames are
# filtered to this user_id; other accounts count into excl_wrong_user.
DEFAULT_USER_ID = "429163f7-50b6-4982-8bdf-96ae99506843"
# DAV-1991 封存时点门 (总控 10-13 令): the formal queue only takes records
# sealed strictly before the T+1 trading-day open 09:30 (Asia/Shanghai).
SEAL_DEADLINE_HHMM = "09:30"   # T+1 trading-day open, same value as the ledger's
                               # MARKET_OPEN_HHMM in daily_snapshot_ledger.py
SH_TZ = "Asia/Shanghai"        # 总控口径: sealed_at/seal_basis_at/created_at 都是
                               # 无时区标记的 UTC，按 UTC 解析后转本时区比较


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
    "user_id": ["user_id", "user", "account"],
}


def _pick(df: pd.DataFrame, canon: str) -> str | None:
    for c in _COL_CANDIDATES[canon]:
        if c in df.columns:
            return c
    return None


def _next_trading_day(calendar: list[str], after: str) -> str | None:
    """First calendar trading day strictly after ``after`` (both YYYYMMDD).

    Same strict-index rule as
    tradingagents/eval/trade_execution.py::next_trading_day: a signal date
    that is not itself in the calendar snaps to the first calendar day after
    it (e.g. a weekend signal date resolves to Monday). None when the
    calendar ends before ``after`` — the T+1 entry day is then unresolvable.

    DAV-2004 (DAV-1999 review 🟡 M-1): ``bisect_right == 0`` — the signal date
    falls BEFORE the calendar's first day — also returns None, so the T+1 day
    is reported unresolvable rather than snapped onto ``calendar[0]``. This
    mirrors scripts/phase2/daily_snapshot_ledger.py::_t_plus_n, whose
    ``idx = bisect_right(dates, base) - 1; if idx < 0 …: return None`` makes
    the same case unresolvable. trade_execution.py has no such guard, hence
    the difference: there the first calendar entry is the right answer, here
    it would hand the gate a real deadline derived from the wrong trading day.

    DAV-2007 review M-2 (总控 10-13 令 rework): a None from either branch is
    an EXCLUSION, not an admission. The ledger's _classify_timing classifies
    the same record F0 because its job is to label what the ledger saw; the
    formal score's job is to admit only what can be PROVEN forward-sealed. A
    record whose T+1 entry open is unresolvable cannot be proven sealed
    before it, so admit() drops it (``excl_seal_t1_unresolved``).
    """
    idx = bisect.bisect_right(calendar, str(after))
    if idx == 0:
        return None
    return calendar[idx] if idx < len(calendar) else None


# DAV-2004 (DAV-1999 review 🟢 S-3): ``format="mixed"`` guesses per element
# and would happily read "01/06/2026", "20260106" or a bare date as a UTC
# midnight instant, so a stamp that cannot prove a seal time could enter the
# formal queue. daily_snapshot_ledger.py::_parse_dt is narrower — it only
# accepts a value whose first ten characters are YYYY-MM-DD (naive
# "YYYY-MM-DD HH:MM:SS[.ffffff]" read as UTC, or ISO-8601 with T / numeric
# offset / Z). Apply the same shape whitelist here and leave everything else
# NaT, so it lands in ``seal_unparseable`` and fails closed like any other
# unparseable seal time.
_SEAL_SHAPE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _sealed_at_sh(s: pd.Series) -> pd.Series:
    """Parse a ``sealed_at`` column into tz-aware Asia/Shanghai instants.

    NaT for missing values, for values the ledger's _parse_dt would not
    accept (the whitelist above is a conservative subset of _parse_dt:
    it additionally rejects leading whitespace and non-zero-padded dates
    like "2026-1-6", whose strptime _parse_dt still reads), and for
    anything else unparseable — all of which the gate treats as "cannot
    prove forward sealing" (fail closed).
    """
    shape_ok = s.astype(str).str.match(_SEAL_SHAPE_RE) | s.isna()
    return pd.to_datetime(s.where(shape_ok), format="mixed",
                          errors="coerce", utc=True).dt.tz_convert(SH_TZ)


def _seal_deadline_cn(signal_dates: pd.Series,
                       calendar: list[str]) -> pd.Series:
    """Per-row seal deadline: the T+1 trading day at 09:30 Asia/Shanghai as
    a tz-aware Asia/Shanghai instant. NaT where the calendar cannot resolve
    the T+1 trading day.

    总控 10-13 令时区口径: the deadline lives in Asia/Shanghai and the
    record's seal time is converted into the same zone before comparing, so
    the gate can never compare against a local-time misreading of the stamp.

    DAV-2004 (DAV-1999 review 🟢 S-4): ``admit()`` is a public entry point
    (the tests call it directly), so ``signal_date`` is not guaranteed to be
    the canonical YYYYMMDD string ``load_frame`` produces. A datetime-typed
    column ``astype(str)``s to "2026-01-05 00:00:00", which sorts before every
    calendar day: before M-1 that silently snapped the whole batch onto the
    calendar's first day, and after M-1 it would make every row
    ``excl_seal_t1_unresolved`` and drop them all.
    Re-normalise through the same ``to_datetime(...).dt.strftime("%Y%m%d")``
    step ``load_frame`` uses, so
    the gate reads the same day key as the day series.
    """
    as_str = pd.to_datetime(signal_dates.astype(str), format="mixed",
                            errors="coerce").dt.strftime("%Y%m%d")
    day_of = {s: _next_trading_day(calendar, s) for s in as_str.unique()}
    days = pd.to_datetime(as_str.map(day_of), format="%Y%m%d",
                          errors="coerce")
    hh, mm = int(SEAL_DEADLINE_HHMM[:2]), int(SEAL_DEADLINE_HHMM[3:])
    return days.dt.tz_localize(SH_TZ) + pd.Timedelta(hours=hh, minutes=mm)


def _load_reattest_index(path: Path | None) -> dict | None:
    """report_id -> adjudicated verdict from pit_reattest.jsonl (DAV-2097③).

    ``None`` in → ``None`` out (no sidecar configured). A configured but
    unreadable file fails closed — a half-trusted re-attestation index is
    worse than none.
    """
    if path is None:
        return None
    if not Path(path).exists():
        raise ValueError(f"reattest file not found: {path}")
    return pit_reattest.load_reattest_index(Path(path))


def _norm_prob_col(s: pd.Series) -> pd.Series:
    """Normalise a probability column to q∈[0,1], column-level: per-value
    scaling is wrong because p=1 is legal on BOTH the 1–99 contract scale
    and the 0–1 scale. If any value exceeds 1 the whole column is treated
    as the integer-percent contract scale and divided by 100.
    Returns (q_series, n_bad) — n_bad counts unparseable/out-of-range rows
    so the caller can surface mixed-scale columns in the funnel."""
    raw = pd.to_numeric(s, errors="coerce")
    n_bad = int(raw.isna().sum())
    v = raw.astype(float)
    vmax = v.max(skipna=True)
    if pd.isna(vmax):
        return v, n_bad
    if vmax <= 1.0:
        return v, n_bad
    if vmax <= 100.0:
        return v / 100.0, n_bad
    out = v.where(v <= 1.0, np.nan)   # out-of-range garbage → NaN
    return out, n_bad + int(out.isna().sum() - raw.isna().sum())


def load_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".parquet", ".pq"):
        df = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
    elif path.suffix.lower() == ".jsonl":
        df = pd.read_json(path, lines=True)
    else:
        raise ValueError(f"unsupported input format: {path.suffix}")

    # DAV-1991: `sealed_at` is the gate's seal-time field and is also a
    # created_at alias, so a frame carrying ONLY sealed_at would have it
    # renamed onto created_at below and lose the column the gate reads.
    # Keep a copy under its own name. 口径 note: on the 1651-row production
    # ledger sealed_at == seal_basis_at for every row, so the two are
    # equivalent and sealed_at is the one carried into the eval frame.
    seal_alias_only = ("sealed_at" in df.columns
                       and "created_at" not in df.columns)
    ren = {}
    for canon in _COL_CANDIDATES:
        src = _pick(df, canon)
        if src is not None and src != canon:
            ren[src] = canon
    df = df.rename(columns=ren)
    if seal_alias_only:
        df["sealed_at"] = df["created_at"]

    # required fields
    for req in ("signal_date", "symbol", "prob", "rel_return"):
        if req not in df.columns:
            raise ValueError(f"input missing required column for '{req}' "
                             f"(aliases: {_COL_CANDIDATES[req]})")

    df["signal_date"] = pd.to_datetime(df["signal_date"]).dt.strftime("%Y%m%d")
    df["q"], df["_n_bad_prob"] = _norm_prob_col(df["prob"])
    df["r"] = pd.to_numeric(df["rel_return"], errors="coerce")
    df = _ensure_defaults(df)
    return df


def _ensure_defaults(df: pd.DataFrame) -> pd.DataFrame:
    """Fill canonical columns a caller may not have supplied (also lets tests
    feed admit() directly without going through load_frame)."""
    df["q"], df["_n_bad_prob"] = _norm_prob_col(df["q"]) \
        if "q" in df.columns else _norm_prob_col(df["prob"])
    if "_n_bad_prob" not in df.columns:
        df["_n_bad_prob"] = df["q"].isna().astype(int)
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

def admit(df: pd.DataFrame,
          user_id: str | None = DEFAULT_USER_ID,
          calendar: list[str] | None = None,
          reattest_index: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Apply §2.3 rules in order; every exclusion increments a funnel count.

    Rows are filtered BEFORE dedup so a stray incomplete run can never leak
    into the formal queue (review DAV-1682 🟡-2): `completed=False` is an
    explicit funnel exclusion, not merely a sort-after in dedup.

    `calendar` (sorted YYYYMMDD list) feeds the DAV-1991 封存时点门: when the
    frame carries a `sealed_at` column the formal queue additionally requires
    sealed_at < T+1 trading-day 09:30 (Asia/Shanghai), and the run fails
    closed without a calendar rather than approximating the T+1 day.

    `reattest_index` (DAV-2097③, 总控 10-15 令): when given, the effective
    input_pit_status is resolved per row BEFORE the FAILED/VERIFIED gates —
    ledger VERIFIED/FAILED stand as-is; ledger UNVERIFIED consults the
    pit_reattest.jsonl index (report_id -> adjudicated verdict). The
    overlay is counted in ``funnel["pit_reattest_applied"]`` and the
    original column is preserved as ``_ledger_input_pit_status`` on the
    working copy (never mutates the caller's frame)."""
    df = _ensure_defaults(df)
    funnel = {"input_rows": len(df)}
    d = df.copy()

    # --- pit_reattest overlay (DAV-2097③) -------------------------------
    # Resolve the effective pit status once, before the FAILED/VERIFIED
    # gates: VERIFIED/FAILED are ledger-final (the sealed record was
    # classified under rules that already had verdicts worth trusting);
    # UNVERIFIED rows may have been re-adjudicated by the sidecar file.
    funnel["pit_reattest_applied"] = 0
    if reattest_index is not None and "run_id" in d.columns:
        orig = d["input_pit_status"].astype(str).str.upper()
        need = ~orig.isin(["VERIFIED", "FAILED"])
        resolved = d["run_id"].map(
            lambda rid: (reattest_index.get(str(rid)) or {}).get(
                "input_pit_status"))
        overlay = resolved.fillna("").astype(str).str.upper()
        apply_mask = need & overlay.isin(["VERIFIED", "FAILED", "UNVERIFIED"])
        if apply_mask.any():
            d["_ledger_input_pit_status"] = d["input_pit_status"]
            d.loc[apply_mask, "input_pit_status"] = overlay[apply_mask]
            funnel["pit_reattest_applied"] = int(apply_mask.sum())

    def _count(mask, key):
        funnel[key] = int(mask.sum())
        return d[~mask]

    # DAV-1740: fixed-account filter first (ledger account hygiene).
    # Frames without a user_id column predate the filter — no exclusion.
    if "user_id" in d.columns and user_id:
        m = d["user_id"].astype(str) != str(user_id)
        d = _count(m, "excl_wrong_user")
    else:
        funnel["excl_wrong_user"] = 0

    # pit FAILED never enters (签收 §6: input_pit_status=FAILED 一律不进)
    m = d["input_pit_status"].astype(str).str.upper() == "FAILED"
    d = _count(m, "excl_pit_failed")
    # formal queue = F0 + VERIFIED only
    m = d["timing_class"].astype(str).str.upper() != "F0"
    d = _count(m, "excl_non_f0")
    m = d["input_pit_status"].astype(str).str.upper() != "VERIFIED"
    d = _count(m, "excl_unverified")
    # --- 封存时点门 (DAV-1991, 总控 10-13 令) ---
    # Re-derive the timing rule from the record's own seal timestamp rather
    # than trusting the timing_class label above: the label may be absent
    # (_ensure_defaults defaults it to F0) or stale, and a mislabeled F0 row
    # is exactly the look-ahead leak the 10-13 令 closes. Rule: sealed_at is
    # STRICTLY before the T+1 trading-day open 09:30 Asia/Shanghai. 口径:
    # sealed_at is the ledger's seal timestamp written as UTC (the _fmt_dt
    # convention); on the 1651-row production ledger sealed_at ==
    # seal_basis_at for every row, so the two are equivalent and sealed_at is
    # the field carried into the eval frame. Late-sealed / backfilled records
    # are counted here and stay in diagnostics only.
    #
    # 总控时区口径: sealed_at / seal_basis_at / created_at are all
    # timezone-NAIVE UTC (the ledger tail line "2026-10-12 11:45:05" is
    # Beijing 19:45). Parse them as UTC, convert to Asia/Shanghai, and
    # compare with the T+1 trading-day 09:30 in the SAME zone — the stamp is
    # never read as local time.
    gate_on = "sealed_at" in d.columns
    funnel["seal_gate_applied"] = bool(gate_on)
    if gate_on:
        if not calendar:
            raise ValueError(
                "input carries a sealed_at column but no trading calendar was "
                "provided; the DAV-1991 seal-time gate must resolve the T+1 "
                "trading day and refuses a weekday fallback — pass --calendar "
                "(one YYYYMMDD per line)")
        # format="mixed": the ledger writes "YYYY-MM-DD HH:MM:SS.ffffff" (UTC,
        # naive) while explicit-offset ISO strings also occur — parse per
        # element, coerce the unparseable to NaT, and read naive strings as
        # UTC (utc=True) before converting them to Asia/Shanghai. The shape
        # whitelist keeps the guesser from inventing an instant out of a
        # non-ledger form (DAV-1999 🟢 S-3, see _sealed_at_sh).
        sealed = _sealed_at_sh(d["sealed_at"])
        entry = _seal_deadline_cn(d["signal_date"], calendar)
        funnel["seal_unparseable"] = int(sealed.isna().sum())
        # DAV-2007 review M-2 / 总控 10-13 令 rework: a row whose T+1
        # deadline cannot be resolved is EXCLUDED, not admitted.
        # _classify_timing's F0 for this case is the ledger's classification
        # 口径, NOT the formal score's admission rule: the ledger may label a
        # record F0 because it cannot see the calendar, while the formal
        # score must not take a record it cannot prove was sealed before its
        # entry open. Admitting them is a look-ahead leak in exactly the
        # population the gate exists for — a late-July signal sealed in the
        # 10-04 backfill while the calendar does not cover July has no
        # deadline, no proof, and used to sail into the formal queue.
        funnel["excl_seal_t1_unresolved"] = int(entry.isna().sum())
        # An unparseable seal time cannot prove forward sealing either → fail
        # closed and count it with the gate's exclusions.
        late = sealed.notna() & entry.notna() & (sealed >= entry)
        d = _count(late | sealed.isna() | entry.isna(), "excl_late_seal")
    else:
        funnel["seal_unparseable"] = 0
        funnel["excl_seal_t1_unresolved"] = 0
        funnel["excl_late_seal"] = 0
    # incomplete runs never reach dedup (review 🟡-2). None/NaN = flag absent
    # → treated complete; only an explicit False/'false'/'0' marks incomplete.
    comp = d["completed"]
    m = comp.notna() & ~comp.astype(str).str.lower().isin(
        ["true", "1", "completed", "complete", "done", "ok", "yes"])
    d = _count(m, "excl_not_completed")
    # legal probability int 1–99 → q in (0,1]; 0/out-of-range rejected.
    # bad_prob_unparseable is the column-level unparseable count (DAV-1729
    # fix): _n_bad_prob is a scalar broadcast onto every row by
    # _ensure_defaults, so summing the post-filter column would multiply the
    # scalar by the surviving row count. Read the scalar before filtering —
    # it is the number of input rows whose prob could not be parsed (those
    # rows are the ones excl_bad_prob drops / counts).
    n_bad_prob = int(d["_n_bad_prob"].iloc[0]) \
        if "_n_bad_prob" in d.columns and len(d) else 0
    m = d["q"].isna() | (d["q"] <= 0)
    d = _count(m, "excl_bad_prob")
    funnel["bad_prob_unparseable"] = n_bad_prob
    # mature label required
    m = d["r"].isna()
    d = _count(m, "excl_immature_label")

    # --- primary-run dedup (签收 item 4): same symbol+day+version →
    # earliest-created completed run wins; fixed ex ante ---
    d["_created_sort"] = pd.to_datetime(d["created_at"], errors="coerce")
    d["_seq"] = np.arange(len(d))
    # No 'completed' key here by design (DAV-1701 🟡-1): the
    # excl_not_completed filter already removed incomplete rows, but the
    # surviving 'completed' values keep their original spellings
    # (True/'true'/'1'/'completed'/... — all pass the filter). Sorting on
    # them would NOT be constant: spelling order would override created_at
    # and elect a later-created run. The contract is earliest-created
    # wins, so created_at is the first tie-breaker, then input order.
    d = d.sort_values(
        ["signal_date", "version_key", "symbol", "_created_sort", "_seq"],
        ascending=[True, True, True, True, True],
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


def eval_days(d: pd.DataFrame,
              calendar: list[str] | None = None) -> list[DayRow]:
    """Per-day evaluation on ONE version queue (caller guarantees it).

    Day slots come from `calendar` (sorted YYYYMMDD list) when given: every
    calendar day in the input's min..max span gets a slot, so days with zero
    signals stay as no-data NaN slots and NW lags never bridge the gap
    (签收 item 3 + review 🟡-1). With calendar=None the slot index is the
    union of observed signal dates — i.e. the input frame is assumed to cover
    all accountable days; a whole missing day is then invisible (documented).
    """
    by_day: dict[str, list[DayRow]] = {}
    for (day), g in d.groupby("signal_date", sort=True):
        n_t = len(g)
        if n_t < MIN_DAILY_N:
            by_day[day] = DayRow(day, n_t, "insufficient")
            continue
        q = g["q"].to_numpy(float)
        r = g["r"].to_numpy(float)
        ic, degen = spearman_ic(q, r)
        if degen:
            by_day[day] = DayRow(day, n_t, "rank_degenerate")
            continue
        diff, tied = quintile_diff(q, r)
        brier = float(np.mean((q - (r > 0).astype(float)) ** 2))
        by_day[day] = DayRow(day, n_t, "ok", ic, diff, tied, brier)

    if calendar:
        if not by_day:
            # every row was funnel-excluded: emit an empty day series rather
            # than crashing on min()/max() of an empty dict — grade() then
            # reports a grade-A empty report.
            return []
        lo, hi = min(by_day), max(by_day)
        idx = [c for c in calendar if lo <= c <= hi]
        return [by_day.get(c, DayRow(c, 0, "no_data")) for c in idx]
    return [by_day[c] for c in sorted(by_day)]


def reliability_table(d: pd.DataFrame) -> list[dict]:
    """Fixed 10 bins [1–10]…[91–99] on the integer p (q*100), only ok days.
    Vectorised: one groupby pass builds the ok-day mask, then a merge keeps
    only those days — no per-row apply (review 🟢-4)."""
    d = d.copy()
    d["p_int"] = np.clip(np.round(d["q"] * 100).astype("Int64"), 1, 99)
    d["y"] = (d["r"] > 0).astype(float)

    def _ok(g: pd.DataFrame) -> bool:
        if len(g) < MIN_DAILY_N:
            return False
        _, degen = spearman_ic(g["q"].to_numpy(float),
                               g["r"].to_numpy(float))
        return not degen

    ok = (d.groupby("signal_date", sort=False)[["q", "r"]]
            .apply(lambda g: _ok(g))
            .rename("_ok"))
    d = d.merge(ok, left_on="signal_date", right_index=True)
    d = d[d["_ok"]]
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
        "no_data_days": sum(1 for x in days if x.status == "no_data"),
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


def select_version(df: pd.DataFrame,
                   version_key: str | None = None) -> pd.DataFrame:
    """Return the single-version sub-frame for the main day series.

    签收 item 4 + review DAV-1682 🔴: the main IC series is ONE version
    queue. If the frame holds several `version_key` values the caller MUST
    name one via --version-key; otherwise this fails closed instead of
    silently mixing cross-sections across versions into one series (which
    would double-count valid_days and corrupt NW lags).

    DAV-1716: an explicitly passed --version-key is validated against the
    frame's key list even when the frame is single-version — a typo used to
    be silently accepted (returning every row) and then written back into
    run.json as the provenance label."""
    if "version_key" not in df.columns:
        return df
    # NaN counts as its own key — a NaN-version residue must not slip the
    # multi-version fail-close (DAV-1692 🟢-1): dropna() would ignore it,
    # letting 'NaN×15 + v2×15' pass as single-version and inflate n_t.
    # Compare on a stringified key so typed (e.g. int) keys match the CLI
    # string; missing keys map to a literal "<NaN>" display name that can
    # be selected the same way (a real "<NaN>" string key is indistinct
    # from missing by design — both mean 'no valid version').
    raw = df["version_key"]
    vk = raw.astype("string").fillna("<NaN>")
    # Collision guard (DAV-1701 🟡-2): stringification must not merge
    # distinct raw keys (e.g. int 2 vs str "2") — that would silently
    # bypass fail-close. Any display key fed by >1 raw (type, value)
    # identity fails closed before the single-key fast path below.
    raw_id = raw.map(
        lambda v: ("<NaN>", "") if pd.isna(v)
        else (type(v).__name__, str(v)))
    coll = pd.DataFrame({"vk": vk.tolist(), "raw": raw_id.tolist()})
    ndistinct = coll.groupby("vk")["raw"].nunique()
    bad = ndistinct[ndistinct > 1]
    if len(bad):
        detail = {k: sorted(set(coll.loc[coll["vk"] == k, "raw"]))
                  for k in bad.index.tolist()}
        raise ValueError(
            f"version_key values collide after stringification {detail}; "
            "refusing to merge distinct raw keys — normalise the input "
            "column to a single dtype first")
    keys = sorted(vk.unique().tolist())
    # Membership check BEFORE the single-key fast path (DAV-1716): on a
    # single-version frame a typo'd --version-key previously fell through
    # the `len(keys) <= 1` early return, got accepted, and then landed in
    # run.json's version_key as a false provenance label. Fail closed.
    if version_key is not None and version_key not in keys:
        raise ValueError(
            f"--version-key {version_key!r} not in input {keys}")
    if len(keys) <= 1:
        return df
    if version_key is None:
        raise ValueError(
            f"input frame carries {len(keys)} version_key values {keys}; "
            "the main day series is single-version — pass --version-key "
            "to select one (cross-version comparison belongs to the "
            "paired channel, never the same IC series)")
    return df[vk == version_key].copy()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

# DAV-2011 (DAV-2007 review 🟡 M-2): the calendar file is documented as one
# YYYYMMDD per line (the ``--calendar`` help and scripts/phase2/README.md),
# but ``pd.read_csv``'s default ``header=0`` consumed the first line of a
# headerless file as a column name — a 4-day headerless calendar loaded as
# 3 days, and the lost first day changes the T+1 resolution (hence the
# formal queue and the IC) for boundary signal days. Read raw lines
# instead: blank lines are cosmetic, one leading ``date`` header (the repo
# test convention) is recognised explicitly, and any other non-YYYYMMDD
# line fails closed rather than being silently dropped — the calendar
# feeds the DAV-1991 seal gate, so a dropped day is a wrong deadline.
_DAY_TOKEN_RE = re.compile(r"^\d{8}$")


def _load_calendar(path: Path | None) -> list[str] | None:
    if path is None:
        return None
    days = [ln.strip()
            for ln in path.read_text(encoding="utf-8-sig").splitlines()]
    days = [ln for ln in days if ln]
    if not days:
        raise ValueError(f"calendar file {path} carries no YYYYMMDD line")
    if days[0].lower() == "date":
        days = days[1:]
        if not days:
            raise ValueError(
                f"calendar file {path} carries no YYYYMMDD line")
    bad = [d for d in days if not _DAY_TOKEN_RE.match(d)]
    if bad:
        raise ValueError(
            f"calendar file {path} holds {len(bad)} line(s) that are not "
            f"YYYYMMDD (first: {bad[0]!r}); the calendar is one YYYYMMDD "
            "per line, optionally preceded by a single 'date' header")
    # keep the pre-fix to_datetime round-trip so an 8-digit token that is
    # not a real date (e.g. 20260230) still fails closed
    parsed = pd.to_datetime(days, format="%Y%m%d")
    return sorted(parsed.strftime("%Y%m%d").tolist())


def run(input_path: Path, out_dir: Path,
        pairs_path: Path | None = None,
        version_key: str | None = None,
        calendar_path: Path | None = None,
        user_id: str | None = DEFAULT_USER_ID,
        reattest_path: Path | None = None) -> dict:
    t0 = time.time()
    log(f"loading {input_path}")
    df = load_frame(input_path)
    df = select_version(df, version_key)          # fail-close multi-version
    # loaded once: the DAV-1991 gate and the day-slot calendar share it
    calendar = _load_calendar(calendar_path)
    # DAV-2097③: optional pit_reattest.jsonl sidecar (总控取值顺序:
    # 账本 VERIFIED/FAILED 为准, UNVERIFIED 查补判文件).
    reattest_index = _load_reattest_index(reattest_path)
    formal, funnel = admit(df, user_id=user_id, calendar=calendar,
                           reattest_index=reattest_index)
    funnel["version_key"] = version_key or \
        (str(df["version_key"].iloc[0])
         if "version_key" in df.columns and len(df) else "default")
    log(f"formal rows {len(formal)} / input {len(df)}")
    days = eval_days(formal, calendar=calendar)
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
    # Explicit columns (DAV-1701 🟡-4): an empty day list must still yield
    # a (0, 7) frame with the README schema, not a (0, 0) schemaless one.
    days_df = pd.DataFrame(
        [vars(x) for x in days],
        columns=["date", "n_t", "status", "ic", "quintile_diff",
                 "quintile_tied_n", "brier"])
    days_df.to_parquet(out_dir / "m1_day_series.parquet", index=False)
    pd.DataFrame(rel).to_json(out_dir / "m1_reliability.json",
                              orient="records", force_ascii=False, indent=2)
    (out_dir / "m1_verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2))
    (out_dir / "run.json").write_text(json.dumps({
        "elapsed_s": round(time.time() - t0, 2),
        "peak_rss_gb": round(peak_rss_gb(), 3),
        "input_rows": len(df), "formal_rows": len(formal),
        "version_key": funnel.get("version_key"),
        "user_id": user_id,
        "seal_gate_applied": funnel.get("seal_gate_applied", False),
        # DAV-2004 (DAV-1999 review 🟢 S-1): run.json is the artefact ops
        # reads first, so the gate's two "cannot prove forward sealing"
        # counters travel here too. Without them a run whose gate is on but
        # whose T+1 resolution or seal-stamp parsing failed wholesale would
        # look perfectly clean (they used to live only in verdict.funnel).
        # DAV-2007 review M-2: T+1-unresolvable rows are now EXCLUDED, so the
        # counter is named excl_seal_t1_unresolved like the other exclusion
        # counters it feeds.
        "seal_unparseable": funnel.get("seal_unparseable", 0),
        "excl_seal_t1_unresolved": funnel.get("excl_seal_t1_unresolved", 0),
        "excl_late_seal": funnel.get("excl_late_seal", 0),
        "excl_wrong_user": funnel.get("excl_wrong_user", 0),
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

    # 11) multi-version input fails closed; --version-key selects one
    rows = _mk_day("20260110", range(1, 21), np.linspace(0, 0.1, 20))
    rows += _mk_day("20260110", range(1, 21), np.linspace(0, 0.1, 20),
                    vkey="v2", start_idx=50)
    mv = pd.DataFrame(rows)
    try:
        select_version(mv)
        checks["multiversion_failclose"] = False
    except ValueError:
        checks["multiversion_failclose"] = True
    one = select_version(mv, "v2")
    days = eval_days(one)
    assert len(days) == 1 and days[0].n_t == 20 and days[0].status == "ok"
    checks["single_version_days"] = len(days)

    # 12) incomplete run never reaches formal queue
    rows = _mk_day("20260111", range(1, 21), np.linspace(0, 0.1, 20))
    rows.append({"signal_date": "20260111", "version_key": "v1",
                 "symbol": "INCOMPLETE", "q": 0.5, "r": 0.01,
                 "timing_class": "F0", "input_pit_status": "VERIFIED",
                 "completed": False})
    d, fun = admit(pd.DataFrame(rows))
    assert fun["excl_not_completed"] == 1 and len(d) == 20
    checks["not_completed_excl"] = fun["excl_not_completed"]

    # 13) calendar slots: a zero-signal day becomes no_data, kept in series
    d, _ = admit(pd.DataFrame(
        _mk_day("20260105", range(1, 21), np.linspace(0, 0.1, 20)) +
        _mk_day("20260109", range(1, 21), np.linspace(0, 0.1, 20),
                start_idx=30)))
    cal = ["20260105", "20260106", "20260107", "20260108", "20260109"]
    days = eval_days(d, calendar=cal)
    assert [x.status for x in days] == ["ok", "no_data", "no_data",
                                        "no_data", "ok"]
    checks["calendar_slots"] = len(days)

    # 14) DAV-1991 封存时点门 (总控 10-13 令): a row sealed strictly before
    # the T+1 trading-day open 09:30 Asia/Shanghai stays in the formal
    # queue; sealed at/after that instant (incl. backfills) only reaches the
    # funnel, and the gate fails closed without a calendar.
    cal = ["20260105", "20260106", "20260107", "20260108"]
    rows = _mk_day("20260105", range(1, 21), np.linspace(0, 0.1, 20))
    for r in rows:
        r["sealed_at"] = "2026-01-06 01:29:59.999999"   # 09:29:59.999999 CST
    d, fun = admit(pd.DataFrame(rows), calendar=cal)
    assert len(d) == 20 and fun["excl_late_seal"] == 0, fun
    rows.append({"signal_date": "20260105", "version_key": "v1",
                 "symbol": "LATE_AT_OPEN", "q": 0.5, "r": 0.01,
                 "timing_class": "F0", "input_pit_status": "VERIFIED",
                 "sealed_at": "2026-01-06 01:30:00.000000"})   # exactly open
    rows.append({"signal_date": "20260105", "version_key": "v1",
                 "symbol": "BACKFILL", "q": 0.5, "r": 0.01,
                 "timing_class": "F0", "input_pit_status": "VERIFIED",
                 "created_at": "2026-01-05 08:00:00.000000",
                 "sealed_at": "2026-01-08 10:00:00.000000"})
    d, fun = admit(pd.DataFrame(rows), calendar=cal)
    assert len(d) == 20 and fun["excl_late_seal"] == 2, fun
    try:
        admit(pd.DataFrame(rows), calendar=None)
        checks["seal_gate_needs_calendar"] = False
    except ValueError:
        checks["seal_gate_needs_calendar"] = True
    assert checks["seal_gate_needs_calendar"]
    checks["seal_gate_late_excluded"] = fun["excl_late_seal"]

    # 15) DAV-2004 — the DAV-1999 review's M-1 / S-3 / S-4 follow-ups on the
    # same gate (all "when the inputs cannot be resolved, say so" discipline):
    #   M-1  a signal day EARLIER than the calendar's first day is the same
    #        unresolvable-T+1 case as a calendar that ends first — 账本
    #        _t_plus_n's ``idx < 0`` makes it None and _classify_timing labels
    #        the record F0, but the formal score EXCLUDES it (DAV-2007 review
    #        M-2: a record with no resolvable T+1 open cannot be proven
    #        forward-sealed). Snapping it onto calendar[0] would invent a
    #        deadline from the wrong trading day.
    #   S-3  a sealed_at the ledger's _parse_dt would not accept cannot prove
    #        a seal time → seal_unparseable → excluded, never guessed into a
    #        UTC-midnight instant by format="mixed".
    #   S-4  a datetime-typed signal_date resolves the same T+1 day as the
    #        YYYYMMDD string form instead of silently snapping to calendar[0].
    early_cal = ["20260105", "20260106", "20260107", "20260108"]
    rows = _mk_day("20260102", range(1, 21), np.linspace(0, 0.1, 20))
    for r in rows:
        r["sealed_at"] = "2026-01-02 09:00:00.000000"
    d, fun = admit(pd.DataFrame(rows), calendar=early_cal)
    assert fun["excl_seal_t1_unresolved"] == 20, fun
    assert fun["excl_late_seal"] == 20 and len(d) == 0, fun
    checks["seal_t1_before_first_calendar_day_excluded"] = \
        fun["excl_seal_t1_unresolved"]

    for bad in ("20260106", "2026/01/06", "01/06/2026", "2026-1-6"):
        rows = _mk_day("20260105", range(1, 21), np.linspace(0, 0.1, 20))
        for r in rows:
            r["sealed_at"] = bad
        d, fun = admit(pd.DataFrame(rows), calendar=early_cal)
        assert fun["seal_unparseable"] == 20, (bad, fun)
        assert fun["excl_late_seal"] == 20 and len(d) == 0, (bad, fun)
    checks["seal_shape_whitelist"] = True

    rows = _mk_day("20260105", range(1, 21), np.linspace(0, 0.1, 20))
    for r in rows:
        r["sealed_at"] = "2026-01-06 01:00:00.000000"   # 09:00 CST → forward
        r["signal_date"] = datetime(2026, 1, 5)
    d, fun = admit(pd.DataFrame(rows), calendar=early_cal)
    assert fun["excl_seal_t1_unresolved"] == 0, fun
    assert fun["excl_late_seal"] == 0 and len(d) == 20, fun
    checks["seal_datetime_signal_date_normalised"] = True

    log("selftest PASS: " + json.dumps(checks, default=str))
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path)
    ap.add_argument("--pairs", type=Path, default=None,
                    help="optional repeat-measurement frame (q1,q2,pair_id)")
    ap.add_argument("--version-key", default=None,
                    help="version queue to evaluate; required when the input "
                         "frame holds more than one version_key")
    ap.add_argument("--calendar", type=Path, default=None,
                    help="trading-calendar file (one YYYYMMDD per line, "
                         "optionally preceded by a single 'date' header); "
                         "days in the input's span with no signal stay as "
                         "no_data NaN slots instead of compressing. "
                         "REQUIRED when the input frame carries a "
                         "sealed_at column: the DAV-1991 seal-time gate "
                         "must resolve the T+1 trading day and fails "
                         "closed without a calendar rather than "
                         "approximating it with a weekday fallback "
                         "(DAV-1999 review 🟢 S-2)")
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--user-id", default=DEFAULT_USER_ID,
                    help="only evaluate rows of this user_id "
                         "(default: fixed P4 account; frames without a "
                         "user_id column skip this filter)")
    ap.add_argument("--pit-reattest", type=Path, default=None,
                    help="optional pit_reattest.jsonl (DAV-2097③): rows "
                         "whose ledger input_pit_status is UNVERIFIED take "
                         "the sidecar's adjudicated verdict; VERIFIED/FAILED "
                         "stay ledger-final. File must exist when given.")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if a.input is None:
        ap.error("--input required (or --selftest)")
    run(a.input, a.out_dir, a.pairs, a.version_key, a.calendar,
        user_id=a.user_id, reattest_path=a.pit_reattest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
