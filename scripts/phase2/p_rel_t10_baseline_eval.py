#!/usr/bin/env python3
"""DAV-1883 — p_rel_t10 probability-ranking baseline (M1 签收口径 + D-075 增量).

Reads the P4 forward ledger (``work/phase2-ledger/forward_ledger.jsonl``,
fixed account only) plus the DAV-1875 short-horizon expiry labels
(``work/phase2-labels/labels_t10.jsonl``) and evaluates the current
``p_rel_t10`` probability ranking under the signed-off M1 criteria:

* daily Spearman rank IC  — average-rank ties, equal-weight day summary,
  real date gaps kept, rank-degenerate days listed separately (M1 签收
  items 1, 3);
* quintile hi-lo diff     — boundary ties by §3.2 fractional weights
  (签收 item 3);
* reliability             — fixed 10 bins [1–10]…[91–99] (签收 item 7);
* Newey-West HAC          — primary band 60, robustness 120, 95% two-sided
  (签收 item 5);
* conclusion grade A–D    — C requires >=60 valid days AND both NW60/NW120
  lower bounds > 0 (签收 item 6); never auto-D;
* **D-075 increment**     — paired daily ``IC_model − IC_base`` on the same
  sample & same days, NW60/NW120 CI on the diff; the base rate is the
  shrunken SW2021-L1 industry T+10 relative-win-rate (m=200 shrinkage to
  the market mean, W=0 expanding window, DAV-1677 engine re-derived on the
  T+10 label panel) — D-075 fixes m200/W0 as the primary baseline, other
  grids are never substituted ex post.

Ledger p_rel_t10 read node (M1 签收 item 2 — 三处都要核到):
  record → ``horizons.short`` → ``forecast.p_rel_t10``
  (the slice's ``forecast`` dict is the ``parse_horizon_forecast`` output
  {contract, p_rel_t10, status, benchmark, node}; ``manager_verdict.
  forecast_input.p_rel_t10`` inside the same slice is the fallback when
  ``forecast`` is absent). Only ``status == "valid"`` ints 1–99 enter the
  formal queue — malformed/missing/node_failed count in the funnel, never
  filled.

Label leg (DAV-1875 ``labels_t10.jsonl``):
  r = ``sw_excess_pct_prediction`` / 100   (stock − SW L1, T+1 open →
       target close, prediction basis, NO roll — the contract window of
       p_rel_t10);
  y = ``y_rel_prediction``                 (r > 0);
  l1 = ``sw.index_code``                   (PIT membership at signal T);
  matured rows only = ``prediction.status == "evaluated"`` AND r non-null —
  ``未到期`` / ``suspension`` / ``unexecutable_entry`` / typed-status rows
  carry no r and count as immature in the funnel (not zero, not dropped
  silently).

Dedup (签收 item 4): same (symbol, signal_date, version_key) keeps the
earliest-``created_at`` completed run — fixed ex ante, no ex-post picking.
Ledger ``version_key`` is reconstructed as
``commit:<generated_by_commit_sha[:12]>:<evidence_contract_version>``
(falling back to run_identity SHA then "unversioned"); a multi-version
input fails closed unless ``--version-key`` names one queue (D-075/DAV-1682
single-version rule — cross-version comparison belongs to M3).

Fail-close: the trading calendar must resolve (``--calendar-file`` or the
live ``require_cn_trade_dates`` route) — unavailable → exit 2; an
unparseable ledger or label line aborts the run (never partially trusted).
Peak memory: ledger and labels are streamed line by line; the base-rate
engine is O(#matured_labels + #signal_days × #industries), no signal×label
cross-product — far under the 4 GiB budget.

Usage:
  python scripts/phase2/p_rel_t10_baseline_eval.py run \
      --ledger work/phase2-ledger/forward_ledger.jsonl \
      --labels work/phase2-labels/labels_t10.jsonl \
      [--calendar-file PATH] [--version-key KEY] [--out-dir DIR]
  python scripts/phase2/p_rel_t10_baseline_eval.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
# Sibling imports keep the M1 primitives + ledger helpers + base-rate
# engine single-sourced with their production modules (DAV-1480 / 1677 /
# 1678 / 1875).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_snapshot_ledger as dsl          # noqa: E402  (DAV-1480)
import expiry_label_pipeline as elp          # noqa: E402  (DAV-1875)
import m1_eval as m1                         # noqa: E402  (DAV-1678)
from m1_base_rate import build_pit_base_rates  # noqa: E402 (DAV-1677)

PIPELINE_VERSION = "p_rel_t10_baseline_eval.v1"
DEFAULT_USER_ID = dsl.DEFAULT_USER_ID
DEFAULT_LEDGER = dsl.DEFAULT_LEDGER_DIR / dsl.LEDGER_FILE
DEFAULT_LABELS = elp.DEFAULT_OUT_DIR / elp.LABEL_FILE_NAMES["short"]
DEFAULT_OUT_DIR = REPO_ROOT / "work" / "phase2-p_rel_t10_baseline"

# D-075 primary baseline grid (fixed; other (m, W) never substituted ex
# post — they are emitted as sensitivity only).
BASE_M = 200
BASE_W = 0
SENSITIVITY_MS = (50, 200, 500)
SENSITIVITY_WS = (0, 250, 500)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _parse_d(raw: Any) -> Optional[str]:
    return elp._parse_signal_date(raw)


# ---------------------------------------------------------------------------
# 1. ledger -> per-record p_rel_t10 frame
# ---------------------------------------------------------------------------

def _record_version_key(record: Mapping[str, Any]) -> str:
    """M1 queue key from the ledger `versions` block.

    commit SHA (generated_by_commit_sha, else run_identity.commit_sha) +
    contract version (evidence_contract_version / protocol_version) — the
    two fields the ledger seals per run; serving-model / prompt-hash
    granularity collapses into run_identity when no commit is recorded.
    """
    v = record.get("versions") or {}
    commit = v.get("generated_by_commit_sha")
    if isinstance(commit, str) and commit:
        base = "commit:" + commit[:12]
    else:
        ri = v.get("run_identity")
        c = ri.get("commit_sha") if isinstance(ri, dict) else None
        if isinstance(c, str) and c:
            base = "commit:" + c[:12]
        elif isinstance(ri, str) and ri:
            base = "run_identity:" + ri[:16]
        else:
            base = "unversioned"
    cv = (v.get("evidence_contract_version")
          or v.get("protocol_version")
          or "novc")
    return f"{base}:{cv}"


def _p_rel_t10(record: Mapping[str, Any]) -> tuple[Optional[int], str]:
    """Read node: horizons.short.forecast.p_rel_t10 (M1 签收 item 2).

    Returns (p_int, status) where status mirrors parse_horizon_forecast:
    "valid" (int 1–99) / "missing" / "malformed" / "node_failed".
    """
    hz = (record.get("horizons") or {}).get("short") or {}
    fc = hz.get("forecast")
    if isinstance(fc, dict):
        status = str(fc.get("status") or "")
        p = fc.get("p_rel_t10")
        if status == "valid" and isinstance(p, (int, float)) \
                and not isinstance(p, bool):
            return int(p), "valid"
        return None, status or "missing"
    # Fallback: the same slice's manager_verdict.forecast_input (the read
    # node's upstream twin, in case an older ledger line sealed before the
    # `forecast` dict was persisted into horizons.short).
    mv = hz.get("manager_verdict") if isinstance(hz.get("manager_verdict"), dict) else {}
    fi = mv.get("forecast_input") if isinstance(mv.get("forecast_input"), dict) else {}
    if fi.get("node_ran") is not True:
        return None, "node_failed"
    p = fi.get("p_rel_t10")
    if p is None:
        return None, "missing"
    if isinstance(p, bool) or not isinstance(p, (int, float)) \
            or not 1 <= p <= 99:
        return None, "malformed"
    if isinstance(p, float) and not p.is_integer():
        return None, "malformed"
    return int(p), "valid"


def load_ledger_signals(
    ledger_path: Path,
    labels: Mapping[tuple[str, str], Mapping[str, Any]],
    user_id: str,
) -> pd.DataFrame:
    """Stream the ledger; one signal row per P4-account ledger line.

    Only short-horizon matured labels supply `r`; every ledger line still
    lands in the frame so the funnel counts the honest denominator
    (无标签 / 未到期 = excl_immature_label downstream).
    """
    rows: list[dict[str, Any]] = []
    for _lineno, rec in elp._iter_ledger_records(ledger_path):
        sig = _parse_d(rec.get("signal_date") or rec.get("trade_date"))
        sym = rec.get("symbol")
        p, p_status = _p_rel_t10(rec)
        lab = labels.get((str(sym), sig)) if (sym is not None and sig) else None
        r = None
        if lab is not None and lab.get("prediction", {}).get("status") == "evaluated":
            v = lab.get("sw_excess_pct_prediction")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                r = float(v) / 100.0
        rows.append({
            "signal_date": sig,
            "symbol": str(sym) if sym is not None else None,
            "run_id": rec.get("report_id"),
            "created_at": rec.get("created_at") or rec.get("sealed_at"),
            "timing_class": rec.get("timing_class"),
            "input_pit_status": rec.get("input_pit_status"),
            "completed": rec.get("status") == "completed",
            "user_id": rec.get("user_id"),
            "version_key": _record_version_key(rec),
            "p_rel_t10": p,
            "_p_status": p_status,
            "r": r,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["signal_date"] = (
            pd.to_datetime(df["signal_date"]).dt.strftime("%Y%m%d"))
        # canonical names m1_eval.admit() expects: `prob` -> q, `r` already set
        df["prob"] = df["p_rel_t10"]
    return df


# ---------------------------------------------------------------------------
# 2. labels -> (symbol, signal_date) map + T+10 base-rate panel
# ---------------------------------------------------------------------------

def load_labels(path: Path) -> dict[tuple[str, str], Mapping[str, Any]]:
    """(symbol, signal_date) -> label row (short/T+10 file only)."""
    if not path.exists():
        raise elp.PipelineError(f"labels file not found: {path}")
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise elp.PipelineError(
                    f"label file {path} line {lineno} unreadable: {exc}") from exc
            if obj.get("horizon") != "short":
                continue
            sig = _parse_d(obj.get("signal_date"))
            sym = obj.get("symbol")
            if sym and sig:
                out[(str(sym), sig)] = obj
    return out


def build_base_panel(labels: Mapping[tuple[str, str], Mapping[str, Any]]
                     ) -> pd.DataFrame:
    """PIT base-rate panel from matured T+10 labels (DAV-1677 engine input).

    Columns: signal_date (T), mat (label maturity = target_date), l1 (SW
    index code at T), y (y_rel_prediction). Only evaluated prediction legs
    with a non-null y and a readable industry code feed the panel — the
    rest stay out of the base rate the same way they stay out of the
    formal queue.
    """
    rows = []
    for (sym, sig), lab in labels.items():
        if (lab.get("prediction") or {}).get("status") != "evaluated":
            continue
        y = lab.get("y_rel_prediction")
        l1 = ((lab.get("sw") or {}).get("index_code")) or ""
        mat = _parse_d(lab.get("target_date"))
        if y is None or not l1 or mat is None:
            continue
        rows.append({"signal_date": sig, "mat": mat, "l1": str(l1),
                     "y": float(bool(y))})
    return pd.DataFrame(rows, columns=["signal_date", "mat", "l1", "y"])


def base_q_map(labels, m: int = BASE_M, W: int = BASE_W) -> dict:
    """(signal_date, l1) -> shrunken base rate p_m{m} for the T+10 panel."""
    panel = build_base_panel(labels)
    if panel.empty:
        return {}
    base, _summary = build_pit_base_rates(
        panel, shrink_ms=(m,), window_days=(W,), min_cell_n=1)
    col = f"p_m{m}"
    sub = base.loc[base["W"] == W, ["signal_date", "l1", col]]
    return {(str(d), str(l1)): float(q)
            for d, l1, q in zip(sub["signal_date"], sub["l1"], sub[col])}


# ---------------------------------------------------------------------------
# 3. D-075 paired increment
# ---------------------------------------------------------------------------

def paired_increment(formal: pd.DataFrame,
                     labels: Mapping[tuple[str, str], Mapping[str, Any]],
                     qmap: Mapping[tuple[str, str], float],
                     days_index: list[str]) -> dict:
    """Daily IC_model − IC_base on the same cross-sections + NW60/120 CI.

    The paired set per day = formal rows whose (signal_date, l1) cell has a
    computable base rate. Days failing the n>=10 / non-degenerate rules on
    EITHER side stay NaN in the diff series (real gaps kept — never
    compressed, 签收 item 3).
    """
    d = formal.copy()
    d["_ymd"] = pd.to_datetime(d["signal_date"]).dt.strftime("%Y-%m-%d")
    # day_index entries are YYYYMMDD (DayRow.date); map to the ISO keys used
    # by `labels` / ic_model / ic_base.
    def _iso(ymd: str) -> str:
        s = str(ymd)
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if "-" not in s else s
    key = list(zip(d["_ymd"],
                   [(labels.get((s, t)) or {}).get("sw", {}).get("index_code") or ""
                    for s, t in zip(d["symbol"], d["_ymd"])]))
    d["q_base"] = [qmap.get(k, np.nan) for k in key]

    ic_model: dict[str, float] = {}
    ic_base: dict[str, float] = {}
    for day, g in d.groupby("_ymd", sort=True):
        ok = g.dropna(subset=["q", "r"])
        if len(ok) < m1.MIN_DAILY_N:
            continue
        ic_m, dm = m1.spearman_ic(ok["q"].to_numpy(float),
                                  ok["r"].to_numpy(float))
        base_ok = ok.dropna(subset=["q_base"])
        ic_b = np.nan
        db = True
        if len(base_ok) >= m1.MIN_DAILY_N:
            ic_b, db = m1.spearman_ic(base_ok["q_base"].to_numpy(float),
                                      base_ok["r"].to_numpy(float))
        if not dm:
            ic_model[day] = ic_m
        if not db:
            ic_base[day] = ic_b

    # a diff day is valid only when BOTH legs evaluated that day
    paired_valid = sorted(d for d in ic_model if d in ic_base)
    diff_series = np.array([ic_model[d] - ic_base[d] for d in paired_valid])

    days_iso = [_iso(day) for day in days_index]
    paired_set = set(paired_valid)
    slot_series = np.array([ic_model[di] - ic_base[di]
                            if di in paired_set else np.nan
                            for di in days_iso])
    mean60, lo60, hi60, n60 = m1.newey_west_ci(slot_series, m1.NW_PRIMARY)
    _, lo120, _, _ = m1.newey_west_ci(slot_series, m1.NW_ROBUST)

    return {
        "paired_days": len(paired_valid),
        "model_ic_mean": float(np.nanmean(list(ic_model.values())))
        if ic_model else np.nan,
        "base_ic_mean": float(np.nanmean(list(ic_base.values())))
        if ic_base else np.nan,
        "diff_mean": mean60,
        "nw60": {"lo": lo60, "hi": hi60, "n_slots": n60},
        "nw120_lo": lo120,
        "verdict": ("C" if (len(paired_valid) >= m1.C_GRADE_MIN_DAYS
                            and lo60 > 0 and lo120 > 0)
                    else "B" if len(paired_valid) >= 1 else "A"),
        "note": "D-075 增量判定：NW60/NW120 下界都 >0 且配对日>=60 才能宣称"
                "超出基率；主基准固定 m200/W0，其余参数格仅敏感性展示。",
    }


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run(ledger_path: Path, labels_path: Path, out_dir: Path,
        *, version_key: Optional[str] = None,
        calendar_path: Optional[Path] = None,
        user_id: str = DEFAULT_USER_ID) -> dict:
    t0 = time.time()
    log(f"loading labels {labels_path}")
    labels = load_labels(labels_path)
    log(f"{len(labels)} matured short labels")
    df = load_ledger_signals(ledger_path, labels, user_id)
    log(f"ledger rows {len(df)}")

    # single-version queue (fail-close multi-version, D-075/DAV-1682)
    df = m1.select_version(df, version_key)
    formal, funnel = m1.admit(df, user_id=user_id)
    funnel["version_key"] = version_key or (
        str(df["version_key"].iloc[0])
        if "version_key" in df.columns and len(df) else "default")
    log(f"formal rows {len(formal)} / {len(df)}")

    calendar = elp._load_calendar(
        Path(calendar_path) if calendar_path else None)
    cal_ymd = [d.replace("-", "") for d in calendar]
    days = m1.eval_days(formal, calendar=cal_ymd)
    rel = m1.reliability_table(formal) if len(formal) else []
    verdict = m1.grade(days, funnel)

    # D-075: paired increment vs m200/W0 base rate
    qmap = base_q_map(labels, m=BASE_M, W=BASE_W)
    days_index = [vars(x)["date"] for x in days]
    increment = paired_increment(formal, labels, qmap, days_index)

    # sensitivity grid (other (m, W) cells; descriptive only, never a
    # substitute for the fixed m200/W0 primary baseline)
    sensitivity = {}
    panel = build_base_panel(labels)
    if not panel.empty:
        base, _ = build_pit_base_rates(
            panel, shrink_ms=SENSITIVITY_MS,
            window_days=SENSITIVITY_WS, min_cell_n=1)
        for W in SENSITIVITY_WS:
            for mm in SENSITIVITY_MS:
                if (mm, W) == (BASE_M, BASE_W):
                    continue
                col = f"p_m{mm}"
                sub = base.loc[base["W"] == W, ["signal_date", "l1", col]]
                qm = {(str(a), str(b)): float(c)
                      for a, b, c in zip(sub["signal_date"], sub["l1"], sub[col])}
                inc = paired_increment(formal, labels, qm, days_index)
                sensitivity[f"m{mm}_W{W}"] = {
                    "paired_days": inc["paired_days"],
                    "base_ic_mean": inc["base_ic_mean"],
                    "diff_mean": inc["diff_mean"],
                    "nw60_lo": inc["nw60"]["lo"],
                    "nw120_lo": inc["nw120_lo"],
                    "verdict": inc["verdict"],
                }

    out = {
        "pipeline": PIPELINE_VERSION,
        "input": {"ledger": str(ledger_path), "labels": str(labels_path)},
        "days": [vars(x) for x in days],
        "reliability": rel,
        "verdict": verdict,
        "increment_m200_W0": increment,
        "sensitivity": sensitivity,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    days_df = pd.DataFrame(
        [vars(x) for x in days],
        columns=["date", "n_t", "status", "ic", "quintile_diff",
                 "quintile_tied_n", "brier"])
    days_df.to_parquet(out_dir / "day_series.parquet", index=False)
    (out_dir / "verdict.json").write_text(
        json.dumps({"model": verdict, "increment_m200_W0": increment,
                    "sensitivity": sensitivity},
                   ensure_ascii=False, indent=2))
    (out_dir / "reliability.json").write_text(
        json.dumps(rel, ensure_ascii=False, indent=2))
    (out_dir / "run.json").write_text(json.dumps({
        "pipeline": PIPELINE_VERSION,
        "elapsed_s": round(time.time() - t0, 2),
        "peak_rss_gb": round(m1.peak_rss_gb(), 3),
        "ledger_rows": len(df), "formal_rows": len(formal),
        "labels": len(labels), "version_key": funnel["version_key"],
        "user_id": user_id,
        "funnel": funnel,
    }, ensure_ascii=False, indent=2))
    log(f"grade={verdict['grade']} valid_days={verdict['valid_days']} "
        f"increment={increment['verdict']} "
        f"(paired_days={increment['paired_days']}) -> {out_dir}")
    return out


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _ledger_rec(rid: str, symbol: str, day: str, p: Optional[int],
                *, user: str = DEFAULT_USER_ID, tc: str = "F0",
                pit: str = "VERIFIED", created: str = "2026-01-05T09:00",
                commit: str = "abc123def456") -> dict:
    rec = {
        "ledger_version": 1, "report_id": rid, "user_id": user,
        "symbol": symbol, "signal_date": day, "created_at": created,
        "status": "completed", "timing_class": tc,
        "input_pit_status": pit, "backfilled": False,
        "versions": {"generated_by_commit_sha": commit,
                     "evidence_contract_version": "v1"},
        "horizons": {"short": {"forecast": {"status": "valid" if p else "missing",
                                          "p_rel_t10": p}},
                     "medium": {}},
    }
    return rec


def _label(sym: str, day: str, r_pct: float, l1: str = "801010.SI") -> dict:
    return {"horizon": "short", "symbol": sym, "signal_date": day,
            "target_date": day,
            "prediction": {"status": "evaluated", "return_pct": r_pct},
            "sw": {"index_code": l1,
                   "prediction": {"status": "ok", "return_pct": 0.0},
                   "execution": {"status": "ok"}},
            "sw_excess_pct_prediction": r_pct,
            "y_rel_prediction": bool(r_pct > 0)}


def selftest(tmp: Path) -> dict:
    import tempfile
    rng = np.random.default_rng(7)
    checks: dict[str, Any] = {}

    # 80 days x 20 stocks in 4 industries: q monotone in r -> IC≈1.
    # Industry base rates drift over time so the base IC is non-degenerate
    # and the paired increment is computable (not the flat-0.5 degenerate).
    ledger = tmp / "forward_ledger.jsonl"
    lab_f = tmp / "labels_t10.jsonl"
    days = pd.bdate_range("2026-01-05", periods=80).strftime("%Y-%m-%d")
    l1s = ["801010.SI", "801020.SI", "801030.SI", "801040.SI"]
    recs, labs = [], []
    for di, day in enumerate(days):
        drift = 0.02 * np.sin(di / 9.0)          # industry win-rate drifts
        for i in range(20):
            l1 = l1s[i % 4]
            q = 5 + i * 4
            # r monotone in q plus an industry drift common to that l1 cell
            r = 0.5 * i - 5.0 + drift * 10 + float(rng.normal(0, 0.3))
            recs.append(_ledger_rec(f"r{di}_{i}", f"S{i:03d}", day, q,
                                    created=f"{day}T09:00"))
            labs.append(_label(f"S{i:03d}", day, r, l1=l1))
    with open(ledger, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    with open(lab_f, "w") as fh:
        for l in labs:
            fh.write(json.dumps(l) + "\n")
    cal = tmp / "cal.txt"
    cal.write_text("\n".join(days) + "\n")

    out = run(ledger, lab_f, tmp / "out", calendar_path=cal)
    checks["grade"] = out["verdict"]["grade"]
    checks["inc_verdict"] = out["increment_m200_W0"]["verdict"]
    checks["inc_diff_mean"] = out["increment_m200_W0"]["diff_mean"]
    checks["valid_days"] = out["verdict"]["valid_days"]
    checks["mean_ic"] = out["verdict"]["mean_ic"]
    checks["paired_days"] = out["increment_m200_W0"]["paired_days"]
    checks["base_ic_mean"] = out["increment_m200_W0"]["base_ic_mean"]
    assert checks["grade"] in ("B", "C"), checks
    assert checks["mean_ic"] > 0.9, checks
    # base rate now varies per (day, industry): paired days must be >0 and
    # the diff series finite; model IC (monotone) should exceed the base's
    # industry-mean IC on the same cross-sections.
    assert checks["paired_days"] > 0
    assert np.isfinite(checks["inc_diff_mean"])
    assert checks["mean_ic"] > checks["base_ic_mean"]
    assert checks["paired_days"] <= checks["valid_days"]
    print(f"[selftest] PASS {checks}", flush=True)
    return checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command")
    r = sub.add_parser("run", help="evaluate the p_rel_t10 ranking baseline")
    r.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    r.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    r.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    r.add_argument("--version-key", default=None)
    r.add_argument("--calendar-file", default=None,
                   help="trading calendar (one date per line); default live")
    r.add_argument("--user-id", default=DEFAULT_USER_ID)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        import tempfile
        selftest(Path(tempfile.mkdtemp(prefix="prel10_")))
        return 0
    if a.command == "run":
        try:
            run(a.ledger, a.labels, a.out_dir,
                version_key=a.version_key,
                calendar_path=a.calendar_file, user_id=a.user_id)
            return 0
        except elp.PipelineError as exc:
            print(f"FAIL-CLOSE: {exc}", file=sys.stderr)
            return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
