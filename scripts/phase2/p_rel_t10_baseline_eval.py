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
import bisect
import hashlib
import json
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
# Sibling imports keep the M1 primitives + ledger helpers + base-rate
# engine single-sourced with their production modules (DAV-1480 / 1677 /
# 1678 / 1875).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_snapshot_ledger as dsl
import expiry_label_pipeline as elp
import m1_eval as m1
import pit_reattest
from m1_base_rate import build_pit_base_rates, load_panel

PIPELINE_VERSION = "p_rel_t10_baseline_eval.v1"

# DAV-2052 🟡-3: date-only sealed_at ("YYYY-MM-DD", no time part). m1_eval's
# _sealed_at_sh reads it as 00:00 UTC — admitted like any on-time seal — so
# this is a funnel DISCLOSURE count only, kept in sync with the pre-33b1f5b
# implementation's seal_dateonly_fallback key (production _fmt_dt always
# writes full timestamps, making it unreachable in production). The correct
# admit semantics for date-only stamps is an m1_eval caliber decision
# tracked on the DAV-1991 line — NOT changed here.
_DATEONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
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


def _parse_d(raw: Any) -> str | None:
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


def _p_rel_t10(record: Mapping[str, Any]) -> tuple[int | None, str]:
    """Read node: horizons.short.forecast.p_rel_t10 (M1 签收 item 2).

    Returns (p_int, status) where status mirrors parse_horizon_forecast:
    "valid" (int 1–99) / "missing" / "malformed" / "node_failed".
    """
    hz = (record.get("horizons") or {}).get("short") or {}
    fc = hz.get("forecast")
    if isinstance(fc, dict):
        status = str(fc.get("status") or "")
        p = fc.get("p_rel_t10")
        # Enforce the same 1–99 contract as parse_horizon_forecast — never
        # trust a "valid" status blindly: a 0/100/60.5/"60"/True must be
        # re-typed malformed and funnel out, not coerced into the queue.
        if status == "valid":
            if (isinstance(p, bool)
                    or not isinstance(p, (int, float))
                    or not 1 <= p <= 99
                    or (isinstance(p, float) and not p.is_integer())):
                return None, "malformed"
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
    reattest_index: Optional[Mapping[str, dict]] = None,
) -> pd.DataFrame:
    """Stream the ledger; one signal row per P4-account ledger line.

    Only short-horizon matured labels supply `r`; every ledger line still
    lands in the frame so the funnel counts the honest denominator
    (无标签 / 未到期 = excl_immature_label downstream).

    ``reattest_index`` (DAV-2097③, 总控取值顺序): ledger VERIFIED/FAILED
    stand; UNVERIFIED consults the pit_reattest.jsonl index
    (report_id -> adjudicated verdict). The ledger's own status is kept
    in ``_ledger_input_pit_status`` for disclosure.
    """
    rows: list[dict[str, Any]] = []
    for _lineno, rec in elp._iter_ledger_records(ledger_path):
        sig = _parse_d(rec.get("signal_date") or rec.get("trade_date"))
        sym = rec.get("symbol")
        p, p_status = _p_rel_t10(rec)
        lab = labels.get((str(sym), sig)) if (sym is not None and sig) else None
        r = None
        # DAV-2025 🟡-2: `.get(key, {})` returns None (not {}) when the key
        # exists with an explicit JSON null — guard with `or {}` so a
        # `prediction: null` label row routes to excl_immature_label instead
        # of crashing the run with AttributeError.
        if lab is not None and (lab.get("prediction") or {}).get("status") == "evaluated":
            v = lab.get("sw_excess_pct_prediction")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                r = float(v) / 100.0
        pit = rec.get("input_pit_status")
        eff_pit = pit_reattest.resolve_effective_pit(
            pit, rec.get("report_id"), reattest_index)
        rows.append({
            "signal_date": sig,
            "symbol": str(sym) if sym is not None else None,
            "run_id": rec.get("report_id"),
            "created_at": rec.get("created_at") or rec.get("sealed_at"),
            "sealed_at": rec.get("sealed_at") or rec.get("seal_basis_at"),
            "timing_class": rec.get("timing_class"),
            "input_pit_status": eff_pit,
            "_ledger_input_pit_status": pit,
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
# 2b. seal-time gate —— 统一走 m1_eval 的 DAV-1991 实现（总控 10-14 令）。
# 本文件不再自养解析/判门：m1.admit(calendar=...) 内置 sealed_at < 信号日
# 次一交易日 09:30 Asia/Shanghai 的封存时点门，时区口径与解析细节以它为准。
# ---------------------------------------------------------------------------


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


def build_base_panel(chunks_dir: Path) -> pd.DataFrame:
    """PIT base-rate panel from the P1 all-market T+10 partitions.

    D-075/DAV-1890 fix: the comparison baseline is the WHOLE-MARKET history
    (P1 分区, matured rows only), not the P4 ledger's own ~24/day labels.
    Reuses m1_base_rate.load_panel — same column contract and the same
    matured filter (outcome_status==evaluated_ok & universe_ok & y_rel
    non-null & actual_exit_date present, i.e. actual_exit_date <= T).
    Columns: signal_date (T), mat (label maturity = actual_exit_date),
    l1 (SW index code at T), y.
    """
    panel = load_panel(Path(chunks_dir))
    # load_panel keeps signal_date as the partition's YYYYMMDD string; the
    # label map / paired join keys are ISO YYYY-MM-DD — normalise once here.
    if not panel.empty:
        sd = panel["signal_date"].astype(str).str.replace("-", "", regex=False)
        panel["signal_date"] = (sd.str[:4] + "-" + sd.str[4:6]
                                + "-" + sd.str[6:8])
        md = panel["mat"].astype(str).str.replace("-", "", regex=False)
        panel["mat"] = (md.str[:4] + "-" + md.str[4:6] + "-" + md.str[6:8])
    return panel


def _chunks_manifest_sha(chunks_dir: Path) -> str | None:
    """sha256 of the partition set manifest (stats.json) when present — the
    report's 基率来源 fingerprint."""
    p = Path(chunks_dir).parent / "stats.json"
    if not p.exists():
        # tolerate being pointed at the parent dir that already holds stats.json
        p = Path(chunks_dir) / "stats.json"
    if not p.exists():
        return None
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _panel_n_distribution(panel: pd.DataFrame) -> dict:
    """Per-industry per-day matured-count distribution (report's 面板规模)."""
    if panel.empty:
        return {"industries": 0, "signal_days": 0}
    grp = panel.groupby(["signal_date", "l1"]).size()
    per_l1 = grp.groupby("l1").agg(["mean", "min", "max", "count"])
    return {
        "industries": int(panel["l1"].nunique()),
        "signal_days": int(panel["signal_date"].nunique()),
        "matured_labels": len(panel),
        "n_per_cell_mean": float(grp.mean()),
        "n_per_cell_min": int(grp.min()),
        "n_per_cell_max": int(grp.max()),
        "per_industry": {
            str(l1): {"mean_n": float(r["mean"]), "min_n": int(r["min"]),
                      "max_n": int(r["max"]), "days": int(r["count"])}
            for l1, r in per_l1.iterrows()
        },
    }


def base_q_map(panel: pd.DataFrame, m: int = BASE_M, W: int = BASE_W) -> dict:
    """(signal_date, l1) -> shrunken base rate p_m{m} for the T+10 panel."""
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
    computable base rate — BOTH legs are computed on that identical subset
    (DAV-2025 🟡-1: before this fix the model leg used the day's full
    cross-section while the base leg used only the cell-covered subset, so
    `diff` mixed two samples and the "same cross-sections" contract did not
    hold whenever some rows had no base rate). Days failing the n>=10 /
    non-degenerate rules on EITHER side stay NaN in the diff series (real
    gaps kept — never compressed, 签收 item 3). Rows with q & r but no
    q_base are dropped from BOTH legs and reported separately in
    ``no_base_rows`` (总控 10-13 漏斗单列); ``paired_day_n`` records the
    shared cross-section size of each paired day.
    """
    d = formal.copy()
    d["_ymd"] = pd.to_datetime(d["signal_date"]).dt.strftime("%Y-%m-%d")
    # day_index entries are YYYYMMDD (DayRow.date); map to the ISO keys used
    # by `labels` / ic_model / ic_base.
    def _iso(ymd: str) -> str:
        s = str(ymd)
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if "-" not in s else s
    # DAV-2025 🟡-2: explicit JSON null on `sw` (`"sw": null`) must degrade
    # to l1="" (no base rate found -> NaN q_base -> dropped from the paired
    # subset), not AttributeError out of `.get("index_code")`.
    key = list(zip(d["_ymd"],
                   [((labels.get((s, t)) or {}).get("sw") or {}).get("index_code") or ""
                    for s, t in zip(d["symbol"], d["_ymd"])]))
    d["q_base"] = [qmap.get(k, np.nan) for k in key]

    ic_model: dict[str, float] = {}
    ic_base: dict[str, float] = {}
    paired_n: dict[str, int] = {}          # per-day paired cross-section size
    total_model_rows = 0                   # 总控 10-13: rows with q & r
    total_paired_rows = 0                  # 总控 10-13: rows also with q_base
    for day, g in d.groupby("_ymd", sort=True):
        ok = g.dropna(subset=["q", "r"])
        total_model_rows += int(len(ok))
        # DAV-2025 🟡-1 / 总控 10-13 按交易日配对: only records carrying BOTH
        # p_rel_t10 (q) and a cell base rate (q_base) enter the day's cross-
        # section; BOTH IC legs are computed on that identical subset, and
        # a day whose paired subset falls under MIN_DAILY_N never counts as
        # a valid paired day (缺基率的记录两侧都不用).
        paired = ok.dropna(subset=["q_base"])
        total_paired_rows += int(len(paired))
        if len(paired) < m1.MIN_DAILY_N:
            continue
        paired_n[day] = int(len(paired))
        ic_m, dm = m1.spearman_ic(paired["q"].to_numpy(float),
                                  paired["r"].to_numpy(float))
        ic_b, db = m1.spearman_ic(paired["q_base"].to_numpy(float),
                                  paired["r"].to_numpy(float))
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
        # 总控 10-13 funnel 单列: 缺基率的记录两侧都不用, 行数单列计数。
        # model_rows = 同时有 q & r 的记录数; paired_rows = 再叠加有基率
        # (q_base) 的记录数; no_base_rows = 两者之差 (membership gap /
        # sw.index_code 空 / 该日该行业 P1 面板无成熟标签)。
        "model_rows": total_model_rows,
        "paired_rows": total_paired_rows,
        "no_base_rows": int(total_model_rows - total_paired_rows),
        # 每个配对日两腿共用的横截面大小 (same-sample 自证: 两侧 n 相等).
        "paired_day_n": {day: paired_n[day] for day in paired_valid},
        "min_paired_n": (min(paired_n.values()) if paired_n else 0),
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
        *, version_key: str | None = None,
        calendar_path: Path | None = None,
        base_chunks: Path | None = None,
        user_id: str = DEFAULT_USER_ID,
        reattest_path: Path | None = None) -> dict:
    t0 = time.time()
    log(f"loading labels {labels_path}")
    labels = load_labels(labels_path)
    log(f"{len(labels)} matured short labels")
    reattest_index = None
    if reattest_path is not None:
        rp = Path(reattest_path)
        if not rp.exists():
            raise elp.PipelineError(f"reattest file not found: {rp}")
        reattest_index = pit_reattest.load_reattest_index(rp)
        log(f"reattest index: {len(reattest_index)} adjudicated line(s)")
    df = load_ledger_signals(ledger_path, labels, user_id,
                             reattest_index=reattest_index)
    ledger_rows_input = len(df)
    log(f"ledger rows {ledger_rows_input}")

    # single-version queue (fail-close multi-version, D-075/DAV-1682)
    df = m1.select_version(df, version_key)
    post_version_rows = len(df)

    # 封存时点门统一为 m1_eval 的 DAV-1991 实现（总控 10-14 令）——本卡不再
    # 自养一套 _sealed_at_utc/apply_seal_gate，改为把交易日历传进
    # m1.admit(calendar=...) 由它判 sealed_at < T+1 09:30 Asia/Shanghai。
    # 门前的 select_version 分流仍在 funnel 里单列 excl_version。
    #
    # DAV-2052 🟡-1 产物内守恒式（审计对账无需外部数据）:
    #   ledger_rows         = excl_version + admit_input_rows
    #   admit_input_rows    = post_gate_rows(=funnel.formal_rows)
    #       + excl_wrong_user + excl_pit_failed + excl_non_f0
    #       + excl_unverified + excl_late_seal + excl_not_completed
    #       + excl_bad_prob + excl_immature_label + excl_dup_run
    #   seal_gate.input_rows（进入封存门的行数）= post_gate_rows
    #       + excl_late_seal + excl_not_completed + excl_bad_prob
    #       + excl_immature_label + excl_dup_run
    # 注意: m1 把「晚封 | sealed 解析失败 | T+1 截止不可解析」单掩码全计入
    # excl_late_seal — excl_seal_t1_unresolved / seal_unparseable /
    # excl_seal_unresolvable 都是它的子集披露，绝不再相加（相加即重复
    # 计数，旧守恒式的 bug）。
    calendar_iso = elp._load_calendar(
        Path(calendar_path) if calendar_path else None)
    cal_ymd = [d.replace("-", "") for d in calendar_iso]
    pre_admit_ids = set(df["run_id"]) if "run_id" in df.columns else set()

    formal, funnel = m1.admit(df, user_id=user_id, calendar=cal_ymd)

    # seal_gate 块（DAV-1993 🟡 M-1）: 口径 + 被分流 run_id 落盘，verdict.model
    # 与 run.json 顶层各一份同值。计数取自 m1.admit 的 funnel：
    #   excl_late_seal          = 晚封 + 不可判定（seal/signal 解析失败或
    #                           T+1 不可解析）合计 — m1 将它们都 fail-closed
    #                           排进该桶；
    #   excl_seal_t1_unresolved = T+1 截止无法解析的子集（signal 非交易日 /
    #                           早于日历首日 / 日历先尽）；
    #   seal_unparseable        = sealed_at 解析失败的子集。
    # 兼容口径别名：excl_seal_late(=excl_late_seal 总桶) /
    #   excl_seal_unresolvable(=t1_unresolved+unparseable，不可判定子集)。
    post_admit_ids = set(formal["run_id"]) if "run_id" in formal.columns else set()
    # DAV-2052 🟡-2: run_id 可含 None（账本行缺 report_id）——裸 sorted()
    # 对 str/None 混排直接 TypeError，uncaught 崩出而不走 fail-close。
    # 与 select_version 的 <NaN> 处理同族：先 str() 再排，保证落盘确定性。
    admit_excluded_ids = sorted(str(x) for x in pre_admit_ids - post_admit_ids)
    # 分桶 run_id（产物用，供事后复核）：重放 m1.admit 门内判定——先按
    # admit 顺序剔除 seal 门之前已被排除的行（wrong_user / pit_failed /
    # non_f0 / unverified），剩余行中 sealed>=entry → 晚封桶；sealed/entry
    # NaT → 不可判定桶。复用 m1 的 _sealed_at_sh/_seal_deadline_cn，不自养
    # 解析口径。
    d0 = df.copy()
    if "user_id" in d0.columns and user_id:
        d0 = d0[d0["user_id"].astype(str) == str(user_id)]
    d0 = d0[d0["input_pit_status"].astype(str).str.upper() != "FAILED"]
    d0 = d0[d0["timing_class"].astype(str).str.upper() == "F0"]
    d0 = d0[d0["input_pit_status"].astype(str).str.upper() == "VERIFIED"]
    rid = d0["run_id"] if "run_id" in d0.columns else pd.Series([], dtype=str)
    sealed_sh = m1._sealed_at_sh(d0["sealed_at"]) if "sealed_at" in d0.columns \
        else pd.Series(pd.NaT, index=d0.index)
    entry_cn = m1._seal_deadline_cn(d0["signal_date"], cal_ymd)
    late_ids = sorted(str(r) for r, m in zip(rid, sealed_sh.notna()
                        & entry_cn.notna() & (sealed_sh >= entry_cn)) if m)
    unres_ids = sorted(str(r) for r, m in zip(
        rid, sealed_sh.isna() | entry_cn.isna()) if m)
    # DAV-2052 🟡-3: 披露回填 — m1 的 _sealed_at_sh 把裸 "YYYY-MM-DD" 读成
    # 00:00 UTC（按准点收），与 33b1f5b 前自养实现的准入行为一致；此键仅
    # 记数不改判，入 seal_gate 块与 funnel 两处（date-only 戳该排除还是
    # 读成午夜属 m1_eval 口径决策，DAV-1991 线裁定，本卡不擅动）。
    n_dateonly = int(d0["sealed_at"].astype(str).str.strip()
                     .str.match(_DATEONLY_RE).sum()) \
        if "sealed_at" in d0.columns else 0
    seal_gate = {
        "field": "sealed_at",
        "field_equivalence_note":
            "sealed_at == seal_basis_at 于本账本实测同值；"
            "seal_basis_at = max(created_at, sealed_at)，补录行可能不等，"
            "本实现取 sealed_at",
        "threshold":
            "signal_date 次一交易日 09:30 Asia/Shanghai"
            "（严格小于，sealed < entry_open；实现=m1_eval DAV-1991 门）",
        "impl": "m1_eval.admit(calendar=cal_ymd)",
        # DAV-2052 🟡-1: input_rows 恢复为「进入封存门的行数」——通过
        # 门前四桶（wrong_user/pit_failed/non_f0/unverified）后由封存门
        # 判别的行数 len(d0)；admit_input_rows 另列 admit 入口行数
        # (=post_version_rows) 供全链路对账。守恒式见上方块注释。
        "input_rows": int(len(d0)),
        "admit_input_rows": int(post_version_rows),
        "excl_seal_late": int(funnel["excl_late_seal"]),
        "seal_late_ids": late_ids,
        # excl_late_seal 总桶已含不可判定子集（m1 单掩码）— 自证标记。
        "excl_seal_late_includes_unresolvable": True,
        "excl_seal_unresolvable": int(
            funnel["excl_seal_t1_unresolved"] + funnel["seal_unparseable"]),
        "seal_unresolvable_ids": unres_ids,
        "seal_unparseable": int(funnel["seal_unparseable"]),
        "excl_seal_t1_unresolved": int(funnel["excl_seal_t1_unresolved"]),
        "seal_dateonly_fallback": n_dateonly,
        # DAV-2052 🟡-1/🟢-1: admit 全量排除集（含封存门前后所有桶），非
        # 仅封存门分流 — 命名写清口径。
        "admit_excluded_ids": admit_excluded_ids,
    }
    funnel["excl_seal_late"] = seal_gate["excl_seal_late"]
    funnel["excl_seal_unresolvable"] = seal_gate["excl_seal_unresolvable"]
    funnel["excl_version"] = int(ledger_rows_input - post_version_rows)
    funnel["seal_dateonly_fallback"] = n_dateonly
    funnel["version_key"] = version_key or (
        str(df["version_key"].iloc[0])
        if "version_key" in df.columns and len(df) else "default")
    log(f"formal rows {len(formal)} / {seal_gate['input_rows']} "
        f"(seal_late={seal_gate['excl_seal_late']} "
        f"seal_unresolvable={seal_gate['excl_seal_unresolvable']})")

    seal_gate_block = dict(seal_gate)
    seal_gate_block["post_gate_rows"] = int(len(formal))

    days = m1.eval_days(formal, calendar=cal_ymd)
    rel = m1.reliability_table(formal) if len(formal) else []
    verdict = m1.grade(days, funnel)
    verdict["seal_gate"] = seal_gate_block

    # D-075: paired increment vs the WHOLE-MARKET m200/W0 base rate.
    # The base panel comes from P1 T+10 partitions (--base-chunks /
    # build-base), never from the P4 ledger's own labels (DAV-1890 fix).
    base_source: dict = {}
    if base_chunks is not None:
        panel = build_base_panel(Path(base_chunks))
        base_source = {
            "kind": "p1_t10_partitions",
            "chunks_dir": str(base_chunks),
            "manifest_sha256": _chunks_manifest_sha(Path(base_chunks)),
            "panel": _panel_n_distribution(panel),
        }
    else:
        panel = pd.DataFrame(columns=["signal_date", "mat", "l1", "y"])
        base_source = {"kind": "none", "note":
                       "no --base-chunks given — D-075 增量不可判定"}
    qmap = base_q_map(panel, m=BASE_M, W=BASE_W)
    days_index = [vars(x)["date"] for x in days]
    increment = paired_increment(formal, labels, qmap, days_index)
    # 总控 10-13: 缺基率记录两侧都不用、漏斗单列 — 同步进主 funnel 落盘，
    # 使 run.json 顶层即可对账，不依赖 increment 块。
    funnel["paired_no_base_rows"] = increment["no_base_rows"]

    # sensitivity grid (other (m, W) cells on the SAME all-market panel;
    # descriptive only, never a substitute for the fixed m200/W0 primary)
    sensitivity = {}
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
        "base_source": base_source,
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
                    "base_source": base_source,
                    "sensitivity": sensitivity},
                   ensure_ascii=False, indent=2))
    (out_dir / "reliability.json").write_text(
        json.dumps(rel, ensure_ascii=False, indent=2))
    (out_dir / "run.json").write_text(json.dumps({
        "pipeline": PIPELINE_VERSION,
        "elapsed_s": round(time.time() - t0, 2),
        "peak_rss_gb": round(m1.peak_rss_gb(), 3),
        "ledger_rows": ledger_rows_input,
        "formal_rows": len(formal),
        "seal_gate": seal_gate_block,
        "labels": len(labels), "version_key": funnel["version_key"],
        "user_id": user_id,
        "base_source_kind": base_source.get("kind"),
        "funnel": funnel,
    }, ensure_ascii=False, indent=2))
    log(f"grade={verdict['grade']} valid_days={verdict['valid_days']} "
        f"increment={increment['verdict']} "
        f"(paired_days={increment['paired_days']}) -> {out_dir}")
    return out


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _ledger_rec(rid: str, symbol: str, day: str, p: int | None,
                *, user: str = DEFAULT_USER_ID, tc: str = "F0",
                pit: str = "VERIFIED", created: str = "2026-01-05T09:00",
                sealed_at: str | None = None,
                commit: str = "abc123def456") -> dict:
    rec = {
        "ledger_version": 1, "report_id": rid, "user_id": user,
        "symbol": symbol, "signal_date": day, "created_at": created,
        "sealed_at": sealed_at,
        "status": "completed", "timing_class": tc,
        "input_pit_status": pit, "backfilled": False,
        "versions": {"generated_by_commit_sha": commit,
                     "evidence_contract_version": "v1"},
        "horizons": {"short": {"forecast": {"status": "valid" if p else "missing",
                                          "p_rel_t10": p}},
                     "medium": {}},
    }
    return rec


# ---------------------------------------------------------------------------
# P1 all-market T+10 partition builder (DAV-1883 rework: D-075 全市场基率)
# ---------------------------------------------------------------------------

def build_base_t10(out_dir: Path, *, years: str = "", months: str = "",
                   max_rows: int = 1_500_000, max_rss_gb: float = 6.0,
                   force: bool = False) -> int:
    """Build P1 T+10 partitions reusing the DAV-1547 pipeline semantics.

    Same caliber as build_phase2_chunks.py (vendor_qfq, T+1 open entry,
    SW2021-L1 same-window leg, suspension/one-word-limit roll, matured
    actual_exit_date<=T) but the SHORT horizon profile: EVAL_OFFSET=10,
    MAX_ROLL=2 (HORIZON_PROFILE_V1["short"]). The production script reads
    both as module globals, so we import it and override — the whitelisted
    file itself is never edited. Writes the same year=/month= layout plus a
    stats.json manifest under --out-dir.

    Peak memory is bounded by the parent's --max-rss-gb guard (default
    6 GiB build budget). Resumable + --force like the parent.
    """
    import build_phase2_chunks as b1547
    from tradingagents.dataflows.return_labels import HORIZON_MAX_ROLL_DAYS
    from tradingagents.graph.horizon_profile import HORIZON_PROFILE_V1

    b1547.EVAL_OFFSET = int(HORIZON_PROFILE_V1["short"]["primary_eval_offset"])
    b1547.MAX_ROLL = int(HORIZON_MAX_ROLL_DAYS["short"])
    import sys as _s
    _s.argv = ["build_phase2_chunks.py", "--out-dir", str(out_dir)]
    if years:
        _s.argv += ["--years", str(years)]
    if months:
        _s.argv += ["--months", str(months)]
    _s.argv += ["--max-rows-per-partition", str(max_rows)]
    _s.argv += ["--max-rss-gb", str(max_rss_gb)]
    if force:
        _s.argv += ["--force"]
    return b1547.main()


def _label(sym: str, day: str, r_pct: float, l1: str = "801010.SI") -> dict:
    return {"horizon": "short", "symbol": sym, "signal_date": day,
            "target_date": day,
            "prediction": {"status": "evaluated", "return_pct": r_pct},
            "sw": {"index_code": l1,
                   "prediction": {"status": "ok", "return_pct": 0.0},
                   "execution": {"status": "ok"}},
            "sw_excess_pct_prediction": r_pct,
            "y_rel_prediction": bool(r_pct > 0)}


def _write_p1_chunks(chunks_dir: Path, panel_rows: list[dict]) -> None:
    """Write synthetic P1 partitions (year=YYYY/data.parquet) carrying the
    six columns m1_base_rate.load_panel reads."""
    df = pd.DataFrame(panel_rows)
    df["year"] = df["signal_date"].astype(str).str[:4]
    for year, g in df.groupby("year"):
        d = Path(chunks_dir) / f"year={year}"
        d.mkdir(parents=True, exist_ok=True)
        g.drop(columns="year").to_parquet(d / "data.parquet", index=False)


def _p1_row(ts_code: str, signal_date: str, l1: str, y: float,
            exit_date: str | None = None) -> dict:
    """One matured P1 T+10 label row (load_panel column contract)."""
    return {"signal_date": signal_date, "sw_l1_code": l1,
            "actual_exit_date": exit_date or signal_date,
            "outcome_status": "evaluated_ok", "universe_ok": True,
            "y_rel": y}


def selftest(tmp: Path) -> dict:
    rng = np.random.default_rng(7)
    checks: dict[str, Any] = {}

    # 80 days x 20 stocks in 4 industries: q monotone in r -> IC≈1.
    # The all-market T+10 base-rate panel is built from synthetic P1
    # partitions (industry win-rates drift over time so the base IC is
    # non-degenerate) — NOT from the P4 ledger's own labels.
    ledger = tmp / "forward_ledger.jsonl"
    lab_f = tmp / "labels_t10.jsonl"
    chunks = tmp / "chunks"
    days = pd.bdate_range("2026-01-05", periods=80).strftime("%Y-%m-%d")
    l1s = ["801010.SI", "801020.SI", "801030.SI", "801040.SI"]
    recs, labs, panel_rows = [], [], []
    for di, day in enumerate(days):
        drift = 0.02 * np.sin(di / 9.0)          # industry win-rate drifts
        for i in range(20):
            l1 = l1s[i % 4]
            q = 5 + i * 4
            # r monotone in q plus an industry drift common to that l1 cell
            r = 0.5 * i - 5.0 + drift * 10 + float(rng.normal(0, 0.3))
            recs.append(_ledger_rec(f"r{di}_{i}", f"S{i:03d}", day, q,
                                    created=f"{day}T09:00",
                                    sealed_at=f"{day} 08:00:00.000000"))
            labs.append(_label(f"S{i:03d}", day, r, l1=l1))
    # all-market panel: many more rows per (day, industry) than the
            # 20-symbol ledger sample — base must come from THIS, not ledger
            ymd = day.replace("-", "")
            for k in range(60):
                yr = 1.0 if rng.random() < (0.5 + drift * (1 + i % 4)) else 0.0
                panel_rows.append(_p1_row(f"M{k:04d}", ymd,
                                          l1, yr, exit_date=ymd))
    with open(ledger, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    with open(lab_f, "w") as fh:
        fh.writelines(json.dumps(l) + "\n" for l in labs)
    _write_p1_chunks(chunks, panel_rows)
    (chunks / "stats.json").write_text(json.dumps({"partitions": "synthetic"}))
    cal = tmp / "cal.txt"
    cal.write_text("\n".join(days) + "\n")

    out = run(ledger, lab_f, tmp / "out", calendar_path=cal,
              base_chunks=chunks)
    checks["grade"] = out["verdict"]["grade"]
    checks["inc_verdict"] = out["increment_m200_W0"]["verdict"]
    checks["inc_diff_mean"] = out["increment_m200_W0"]["diff_mean"]
    checks["valid_days"] = out["verdict"]["valid_days"]
    checks["mean_ic"] = out["verdict"]["mean_ic"]
    checks["paired_days"] = out["increment_m200_W0"]["paired_days"]
    checks["base_ic_mean"] = out["increment_m200_W0"]["base_ic_mean"]
    checks["base_kind"] = out["base_source"]["kind"]
    assert checks["grade"] in ("B", "C"), checks
    assert checks["mean_ic"] > 0.9, checks
    assert checks["paired_days"] > 0
    assert np.isfinite(checks["inc_diff_mean"])
    assert checks["mean_ic"] > checks["base_ic_mean"]
    assert checks["paired_days"] <= checks["valid_days"]
    # L-8: valid_days 钉死 — 合成日历末日 T+1 不可解析被 seal_gate 分流,
    # 上游统计量若漂移此处必须红(复审 10-12 实测 79; 该值非本轮改动)。
    assert checks["valid_days"] == 79, checks
    assert checks["base_kind"] == "p1_t10_partitions"
    print(f"[selftest] PASS {checks}", flush=True)
    return checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command")
    r = sub.add_parser("run", help="evaluate the p_rel_t10 ranking baseline")
    r.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    r.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    r.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    r.add_argument("--version-key", default=None)
    r.add_argument("--calendar-file", default=None,
                   help="trading calendar (one date per line); default live")
    r.add_argument("--base-chunks", type=Path, default=None,
                   help="P1 all-market T+10 partition dir (year=*/data.parquet); "
                        "omit -> D-075 增量不可判定 (base_source=none)")
    r.add_argument("--user-id", default=DEFAULT_USER_ID)
    r.add_argument("--pit-reattest", type=Path, default=None,
                   help="optional pit_reattest.jsonl (DAV-2097③): ledger "
                        "input_pit_status=UNVERIFIED rows take the sidecar's "
                        "adjudicated verdict; VERIFIED/FAILED stay ledger-final")
    b = sub.add_parser("build-base",
                       help="build P1 all-market T+10 partitions (1547 caliber, "
                            "offset=10 roll=2) into --out-dir/chunks_t10")
    b.add_argument("--out-dir", type=Path, default=Path("data/phase2_t10"))
    b.add_argument("--years", default="", help="comma list; default all")
    b.add_argument("--months", default="", help="comma list")
    b.add_argument("--max-rows-per-partition", type=int, default=1_500_000)
    b.add_argument("--max-rss-gb", type=float, default=6.0)
    b.add_argument("--force", action="store_true")
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
                calendar_path=a.calendar_file,
                base_chunks=a.base_chunks, user_id=a.user_id,
                reattest_path=a.pit_reattest)
            return 0
        except elp.PipelineError as exc:
            print(f"FAIL-CLOSE: {exc}", file=sys.stderr)
            return 2
    if a.command == "build-base":
        return build_base_t10(a.out_dir, years=a.years,
                              months=a.months,
                              max_rows=a.max_rows_per_partition,
                              max_rss_gb=a.max_rss_gb, force=a.force)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
