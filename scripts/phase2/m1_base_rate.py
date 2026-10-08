#!/usr/bin/env python3
"""DAV-1677 — M1 基率模块：p_base_rel_t40（申万一级 T+40 行业相对跑赢历史基率，向全市场收缩）.

Reads the DAV-1547 year-partitioned chunks (data/phase2/chunks/year=YYYY/
data.parquet) READ-ONLY — never modifies or recomputes them. Label semantics
are inherited untouched: r_stock is the vendor-QFQ leg, r_sw the same-window
SW L1 index leg (T+1 open -> actual-exit close), y_rel = 1[r_stock - r_sw > 0].

What this module emits
----------------------
For every (signal_date T, sw_l1_code) cell present in the chunks:

  p_base_rel_t40(T, j) = (s + m·p_mkt) / (n + m)   shrunken win-rate,
      s    = # {i in industry j, actual_exit_date <= T, y_rel_i = 1}  (wins)
      n    = # {i in industry j, actual_exit_date <= T}               (matured)
      m    = shrinkage strength toward the market base rate (parameter)
      p_mkt(T) = market matured win-rate, actual_exit_date <= T

  Equivalently the Beta-Binomial posterior mean with a Beta(m·p_mkt,
  m·(1-p_mkt)) prior centred on the market base rate. m is the
  pseudo-count (prior sample size): m=0 -> raw industry rate; larger m ->
  harder pull to the market mean.

Point-in-time discipline (the M1 forward-looking rule):
  a sample contributes to the base rate at signal date T only if its label
  was ALREADY KNOWABLE at T — actual_exit_date <= T, i.e. the T+40 window
  had fully closed on or before the signal date (labels maturing on T count:
  the signal is generated after that day's close). P1's
  `base_rate_expanding.parquet` instead accumulated by maturation day up
  to an as_of run date; this module re-derives the PIT series per signal
  day, so no signal day ever sees a label that matures after it.
  Industry-unassigned rows (sw_l1_code empty) still feed the market prior
  (they carry real y_rel information) but get no industry line.

Window parameter W (maturity-days lookback):
  Only samples matured within the trailing W maturity days before T count.
  W=0 (default) = expanding window, all history; W>0 = rolling window over
  maturation days — answers "how much does the base rate drift / how does
  shrinkage interact with recency". This is a *maturity-day* window: a label
  counts if T - actual_exit_date <= W. The set of distinct maturity dates is
  the observed exit calendar, not a synthetic business-day grid.

Sensitivity grid (card requirement 2): shrinkage m x window W -> per-cell
p_base_rel_t40; summary table of industry dispersion (std of shrunken rate),
coverage (cells with n_j>0), and mean pull distance |p_shrunk - p_raw|.

Resource contract: single process, measured peak RSS must stay <= 6 GB.
Two passes only — one slim column read per year partition, chunked; the
PIT expansion is done per signal day with running counters, no signal x
label cross-product is materialised.

Usage:
  python scripts/phase2/m1_base_rate.py \
      --chunks-dir <path>/data/phase2/chunks \
      --out-dir data/phase2/m1_base_rate \
      [--shrink-m 50,200,500] [--window-days 0,250,500] \
      [--min-cell-n 1]
  python scripts/phase2/m1_base_rate.py --selftest
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

CHUNKS_DEFAULT = Path(
    "/Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/"
    "dav-1547-cfa02881b296/workdir/tradingagents-ashare-fork/data/phase2/chunks")
OUT_DEFAULT = Path("data/phase2/m1_base_rate")

EVAL_OK = "evaluated_ok"
# only these columns are ever read from a partition — nothing wider
COLS = ["signal_date", "sw_l1_code", "actual_exit_date",
        "outcome_status", "universe_ok", "y_rel"]

DEFAULT_SHRINK_M = (50, 200, 500)
DEFAULT_WINDOW_DAYS = (0, 250, 500)   # 0 = expanding


def peak_rss_gb() -> float:
    val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return val / (1024 ** 3) if val > 10 ** 6 else val / (1024 ** 2)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------
def load_panel(chunks_dir: Path) -> pd.DataFrame:
    """Stream every year partition, keep only evaluated&in-universe rows and
    the six columns needed; return one slim DataFrame.

    A row contributes to the base rate only when it is a realised label:
    outcome_status == evaluated_ok AND universe_ok AND y_rel non-null.
    pending_due / data_missing / unexecutable / suspension stay out of the
    denominator entirely (they are not matured labels), matching the
    P1 D4 numerator/denominator convention.
    """
    parts = sorted(chunks_dir.glob("year=*/data.parquet"))
    if not parts:
        raise FileNotFoundError(f"no year=*/data.parquet under {chunks_dir}")
    frames = []
    for pq_path in parts:
        df = pd.read_parquet(pq_path, columns=COLS)
        ok = df[(df["outcome_status"] == EVAL_OK) & df["universe_ok"]]
        ok = ok[ok["y_rel"].notna() & ok["actual_exit_date"].notna()
                & (ok["actual_exit_date"].astype(str).str.len() == 8)]
        frames.append(ok[["signal_date", "sw_l1_code",
                          "actual_exit_date", "y_rel"]])
        del df, ok
    panel = pd.concat(frames, ignore_index=True)
    panel["signal_date"] = panel["signal_date"].astype(str)
    panel["mat"] = panel["actual_exit_date"].astype(str)
    panel["l1"] = panel["sw_l1_code"].fillna("").astype(str)
    panel["y"] = panel["y_rel"].astype(np.float64)
    return panel[["signal_date", "mat", "l1", "y"]]


# --------------------------------------------------------------------------
# PIT expanding / rolling base-rate engine
# --------------------------------------------------------------------------
def build_pit_base_rates(panel: pd.DataFrame,
                         shrink_ms: tuple[int, ...],
                         window_days: tuple[int, ...],
                         min_cell_n: int = 1) -> tuple[pd.DataFrame, dict]:
    """Per (signal_date T, industry j) shrunken base rate, strictly matured
    labels only (actual_exit_date <= T, trailing W maturity days).

    Implementation: sort matured labels by maturation day, walk signal days
    in order, maintain running (n, wins) counters per industry and for the
    market. For W>0 additionally keep FIFO queues of matured (day, l1, y) to
    expire labels that fall out of the trailing window. No (signal x label)
    materialisation — O(#matured_labels + #signal_days x #industries).
    """
    sig_days = np.sort(panel["signal_date"].unique())
    l1_codes = sorted(c for c in panel["l1"].unique() if c)

    # matured labels grouped by maturation day (only matured rows can enter)
    matured = panel[panel["mat"] != ""].sort_values("mat")
    by_mat = {d: g for d, g in matured.groupby("mat", sort=True)}
    mat_days = np.array(sorted(by_mat.keys()))

    # calendar of distinct observed maturity days for window indexing
    # (window is in maturity days; map each mat day to its position)
    out_rows = []
    summary = {"signal_days": len(sig_days),
               "industries": len(l1_codes),
               "matured_labels": len(matured),
               "configs": []}

    for W in window_days:
        # running counters
        run_n = defaultdict(int)
        run_s = defaultdict(float)
        mkt_n = 0
        mkt_s = 0.0
        # FIFO of matured (mat_day, l1, y) inside the current window
        fifo: list[tuple[str, str, float]] = []
        fifo_head = 0
        mi = 0  # pointer into mat_days
        n_cells = 0
        pull_abs_sum = 0.0
        shrunk_std_acc = []

        for T in sig_days:
            # absorb every label matured on or before T
            while mi < len(mat_days) and mat_days[mi] <= T:
                g = by_mat[mat_days[mi]]
                for l1, yv in zip(g["l1"].to_numpy(), g["y"].to_numpy()):
                    run_n[l1] += 1
                    run_s[l1] += float(yv)
                    mkt_n += 1
                    mkt_s += float(yv)
                    if W > 0:
                        fifo.append((mat_days[mi], l1, float(yv)))
                mi += 1
            # expire labels older than the trailing W maturity days
            if W > 0:
                cutoff = _shift_yyyymmdd(T, -W)
                while fifo_head < len(fifo) and fifo[fifo_head][0] < cutoff:
                    _, l1, yv = fifo[fifo_head]
                    run_n[l1] -= 1
                    run_s[l1] -= yv
                    mkt_n -= 1
                    mkt_s -= yv
                    fifo_head += 1
                if fifo_head > 4096 and fifo_head * 2 > len(fifo):
                    del fifo[:fifo_head]
                    fifo_head = 0

            p_mkt = mkt_s / mkt_n if mkt_n else np.nan
            for l1 in l1_codes:
                n = run_n.get(l1, 0)
                s = run_s.get(l1, 0.0)
                p_raw = s / n if n else np.nan
                rec = {"signal_date": T, "l1": l1, "n": n,
                       "p_mkt": p_mkt, "p_raw": p_raw, "W": W}
                for m in shrink_ms:
                    if n >= min_cell_n and p_mkt == p_mkt:
                        rec[f"p_m{m}"] = (s + m * p_mkt) / (n + m)
                    else:
                        rec[f"p_m{m}"] = np.nan
                out_rows.append(rec)
                n_cells += 1
            # summary accumulators (per config)
            if p_mkt == p_mkt:
                day_pull = []
                day_shrunk = []
                m_ref = shrink_ms[len(shrink_ms) // 2]
                for l1 in l1_codes:
                    n = run_n.get(l1, 0)
                    s = run_s.get(l1, 0.0)
                    if n:
                        p_raw = s / n
                        p_shrunk = (s + m_ref * p_mkt) / (n + m_ref)
                        day_pull.append(abs(p_shrunk - p_raw))
                        day_shrunk.append(p_shrunk)
                if day_pull:
                    pull_abs_sum += float(np.mean(day_pull))
                if len(day_shrunk) > 1:
                    shrunk_std_acc.append(float(np.std(day_shrunk)))

        summary["configs"].append({
            "W": W,
            "cells": n_cells,
            "mean_pull_abs_median_m": pull_abs_sum / max(len(sig_days), 1),
            "mean_industry_std_median_m": (
                float(np.mean(shrunk_std_acc)) if shrunk_std_acc else np.nan),
        })
        log(f"W={W}: done {len(sig_days)} signal days, cells={n_cells:,}")

    df = pd.DataFrame(out_rows)
    return df, summary


def _shift_yyyymmdd(d: str, days: int) -> str:
    """Shift a YYYYMMDD string by `days` calendar days (negative = back)."""
    t = pd.Timestamp(str(d))
    return (t + pd.Timedelta(days=days)).strftime("%Y%m%d")


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def write_report(df: pd.DataFrame, summary: dict, out_dir: Path,
                 shrink_ms, window_days) -> Path:
    lines = [
        "# M1 基率模块敏感性报告（DAV-1677）",
        "",
        "口径：`p_base_rel_t40(T,j)` = 行业 j 在信号日 T 之前**已严格成熟**",
        "（`actual_exit_date <= T`，即 T+40 窗口在信号日当日或更早收盘）标签",
        "的跑赢比例，向全市场均值做 Beta–Binomial 收缩：",
        "`p = (n_j·p̄_j + m·p̄_mkt) / (n_j + m)`，`m` 为先验伪计数（收缩强度）。",
        "行业归属缺失行计入全市场先验、不产行业线；未成熟/缺失标签完全不进分母。",
        "",
        "## 参数网格",
        f"- 收缩强度 m ∈ {list(shrink_ms)}（m=0 即不收缩，仅留原始行业率）",
        f"- 成熟窗口 W ∈ {list(window_days)} 个自然日回看（0=扩窗/全历史）",
        "",
        "## 覆盖与结构",
        f"- 信号日 {summary['signal_days']}，行业 {summary['industries']}，",
        f"  成熟标签 {summary['matured_labels']:,}（evaluated_ok & universe_ok & y_rel 非空）",
    ]
    for cfg in summary["configs"]:
        lines.append(
            f"- W={cfg['W']}: 单元数 {cfg['cells']:,}，"
            f"中位 m 档平均行业间 std {cfg['mean_industry_std_median_m']:.4f}，"
            f"平均收缩位移 |p_shrunk−p_raw| {cfg['mean_pull_abs_median_m']:.5f}")
    lines += [
        "",
        "## 读法",
        "- 同一 `signal_date` 内 31 个一级行业的 `p_m{m}` 即该日 `p_base_rel_t40` 横截面；",
        "  供 M1 契约另存（不给模型看）。",
        "- m 越大行业线越贴全市场均值；W>0 时成熟样本滚动出窗，行业间 std 上行、",
        "  收缩位移放大属预期（有效样本变少，先验占比上升）。",
        "- 前视纪律：T 日只用到 `actual_exit_date <= T` 的标签；信号日 T 的 T+40",
        "  结果与任何更晚成熟样本一律不可见。",
    ]
    path = out_dir / "m1-base-rate-report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# selftest — synthetic panel with a known ground truth
# --------------------------------------------------------------------------
def selftest() -> int:
    rng = np.random.default_rng(1677)
    # 3 industries x 40 signal days; industry B strong, C weak, A ~ market.
    # Labels mature +14 days so that even the last signal days have history.
    sig = np.array([f"2024{(d//20)+1:02d}{(d%20)+1:02d}" for d in range(40)])
    rows = []
    for T in sig:
        for l1, p in (("A", 0.50), ("B", 0.80), ("C", 0.20)):
            for _ in range(30):
                y = float(rng.random() < p)
                rows.append({"signal_date": T, "l1": l1, "y": y,
                             "mat": _shift_yyyymmdd(T, 14)})
    panel = pd.DataFrame(rows)
    df, _ = build_pit_base_rates(panel, shrink_ms=(0, 50, 500),
                                 window_days=(0,))
    df = df[df["n"] > 0]                      # only cells with matured history
    g = df.groupby("l1")[["p_m0", "p_m500"]].mean()
    ok = True
    # raw rates recover truth
    ok &= abs(g.loc["B", "p_m0"] - 0.80) < 0.03
    ok &= abs(g.loc["C", "p_m0"] - 0.20) < 0.03
    # shrinkage pulls toward market mean (~0.5)
    ok &= g.loc["B", "p_m500"] < g.loc["B", "p_m0"]
    ok &= g.loc["C", "p_m500"] > g.loc["C", "p_m0"]
    # PIT: at signal day T, n only counts labels matured on or before T
    for T in sig[:10]:
        for l1 in ("A", "B", "C"):
            truth = panel[(panel["l1"] == l1) & (panel["mat"] <= T)]
            got = df[(df["signal_date"] == T) & (df["l1"] == l1)]["n"]
            ok &= (len(truth) == 0 and len(got) == 0) or \
                  (len(got) and int(got.iloc[0]) == len(truth))
    print(f"selftest {'PASS' if ok else 'FAIL'}: "
          f"B raw={g.loc['B','p_m0']:.3f} C raw={g.loc['C','p_m0']:.3f} "
          f"B m500={g.loc['B','p_m500']:.3f}")
    return 0 if ok else 1


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks-dir", type=Path, default=CHUNKS_DEFAULT)
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--shrink-m", type=str, default=",".join(map(str, DEFAULT_SHRINK_M)))
    ap.add_argument("--window-days", type=str,
                    default=",".join(map(str, DEFAULT_WINDOW_DAYS)))
    ap.add_argument("--min-cell-n", type=int, default=1)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    shrink_ms = tuple(int(x) for x in args.shrink_m.split(",") if x != "")
    window_days = tuple(int(x) for x in args.window_days.split(",") if x != "")
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    log(f"loading panel from {args.chunks_dir}")
    panel = load_panel(args.chunks_dir)
    log(f"panel {len(panel):,} rows; rss={peak_rss_gb():.2f}GiB")

    df, summary = build_pit_base_rates(panel, shrink_ms, window_days,
                                       args.min_cell_n)
    pq_out = out_dir / "p_base_rel_t40.parquet"
    df.to_parquet(pq_out, index=False)
    log(f"wrote {pq_out} ({len(df):,} rows); rss={peak_rss_gb():.2f}GiB")

    report = write_report(df, summary, out_dir, shrink_ms, window_days)
    run = {"script": "scripts/phase2/m1_base_rate.py",
           "shrink_m": list(shrink_ms), "window_days": list(window_days),
           "min_cell_n": args.min_cell_n,
           "peak_rss_gb": peak_rss_gb(), "elapsed_s": time.time() - t0,
           "outputs": [str(pq_out), str(report)]}
    (out_dir / "run.json").write_text(json.dumps(run, indent=2))
    log(f"done in {time.time()-t0:.1f}s; report={report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
