#!/usr/bin/env python3
"""D-066 Phase-1 exit progress sheet (read-only, zero-model, zero-provider).

DAV-1507 — prints per-criterion readings for A1..A4 and B1..B3 together with
the gap to each frozen threshold (docs/plans/phase1-exit/exit-criteria-20261002.md).

Scope and calibers (all frozen in this file, no guessing):

* Sample start: ``--start`` (default ``2026-10-08``, the first daily-batch day
  after the holiday). Readings are additionally broken down so the controller
  can see pre-start rows that share the same cohort.
* Account: the fixed analysis account (same UUID used by work/daily-batch and
  work/b1-monitor).
* Horizon tier labels inside JSON payloads are written as ``horizon:short`` /
  ``horizon:medium`` (DAV-1459 caliber). Bare ``short``/``medium`` only appear
  as unit keys in the ``horizons``/``*_term`` maps — never as cohort labels.
* A2 "qualified clean": per-horizon unit with ``analysis_status == 'VALID'``
  and ``decision in {BUY, SELL}`` (post-gate executable rows; ABSTAIN/WAIT/
  INVALID_RUN/not_applicable do not count). Denominator = all units whose
  parent report is ``completed`` and the unit is not ``not_requested``.
* A3 (D-006): a debate "session" = one horizon unit with a populated
  ``investment_debate_state``. A session is *effective* when it has >= 3
  opening-stage claims AND >= 1 verified evidence item. Bull/bear side counts
  use ``claims[].speaker_key`` (Bull/Bear). "Verified evidence" per side =
  items in ``evidence_verification`` whose ``claim_id`` belongs to that side's
  claims AND ``status == 'verified'``. The last-50-sessions ratio is computed
  over effective sessions ordered by report ``created_at``.
* A4: ``reports.status == 'completed'`` share of submitted (completed+failed)
  reports; T+5 completeness = share of horizon units whose
  ``shadow_credit_metrics.t_plus_5_status`` is non-null among units whose
  ``trade_date`` is at least 5 trading days before ``--as-of`` (mature units;
  needs akshare's bundled calendar if present, else pandas bdate_range).
* B1: contract live when >= 1 unit exposes ``forecast.contract == 'b1.v1'``
  (the field written by DAV-1431). This script only reports coverage.
* B2: consecutive ``b1.v1`` units with ``forecast.status != 'valid'`` or a
  failed ``price_plan_check`` counted as invalid; threshold <= 5% over >= 100
  consecutive completed units. (Readings only — the contract is not deployed
  until 2026-10-09, so before that the sheet reports N/A.)
* B3: a unit is traceable when ``input_snapshot.hash`` AND ``served_models``
  are both present at report top-level (cf7bda3 schema; per-unit copies are
  also accepted).

A1 does NOT audit; it only lists the executable rows (VALID+BUY/SELL) that the
controller must audit one by one, plus an audit-manifest CSV when --csv-dir is
given. 假 clean 判定永远是总控的事。

Usage (locked interpreter, per 铁律):
  env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
      scripts/dav1507_exit_progress.py \
      --db /path/to/copy-of-tradingagents.db [--as-of 2026-10-08] \
      [--start 2026-10-08] [--calendar-json .../calendar.json] \
      [--ids ids.json] [--csv-dir outdir]

The DB path must exist; the file is opened ``mode=ro`` + ``PRAGMA query_only``.
No network, no provider, no model calls. Exit code is always 0 unless the
input is unusable (then 2); this is a dashboard, not a gate.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import Counter, OrderedDict
from datetime import date, datetime
from pathlib import Path

ACCOUNT = "429163f7-50b6-4982-8bdf-96ae99506843"
DEFAULT_START = "2026-10-08"
EXECUTABLE_ACTIONS = {"BUY", "SELL"}
B1_CONTRACT = "b1.v1"
B1_PRICE_FAILURES = {"violation", "missing_required", "unverifiable"}

# A4 T+5 maturity: a unit needs trade_date + 5 trading days <= as_of.
T5_LAG_DAYS = 5

HORIZON_LABEL = {"short": "horizon:short", "medium": "horizon:medium", "single": "horizon:single"}


# --------------------------------------------------------------------------- #
# DB layer                                                                     #
# --------------------------------------------------------------------------- #

def open_ro(path: str) -> sqlite3.Connection:
    db = Path(path).resolve()
    if not db.is_file():
        raise SystemExit(f"[exit 2] database does not exist: {db}")
    uri = f"file:{db}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def load_reports(con, account, start=None, end=None, ids=None):
    cols = "id, user_id, symbol, trade_date, status, created_at, result_data"
    where, params = ["user_id=?", "json_valid(result_data)"], [account]
    if start:
        where.append("trade_date >= ?")
        params.append(start)
    if end:
        where.append("trade_date <= ?")
        params.append(end)
    if ids is not None:
        if not ids:
            return []
        where.append("id IN (" + ",".join("?" for _ in ids) + ")")
        params.extend(ids)
    sql = f"SELECT {cols} FROM reports WHERE {' AND '.join(where)} ORDER BY created_at, id"
    out = []
    for rid, uid, sym, td, status, created, rd in con.execute(sql, params):
        try:
            payload = json.loads(rd)
        except Exception:
            payload = None
        out.append({
            "id": rid, "user_id": uid, "symbol": sym, "trade_date": td,
            "status": status, "created_at": created, "result_data": payload or {},
        })
    return out


def status_counts_all_time(con, account):
    sql = "SELECT status, COUNT(*) FROM reports WHERE user_id=? GROUP BY status"
    return dict(con.execute(sql, (account,)))


# --------------------------------------------------------------------------- #
# Unit iteration                                                               #
# --------------------------------------------------------------------------- #

def horizon_units(report):
    """Yield (horizon_key, unit_dict) for each requested horizon unit.

    Prefers the ``horizons`` mapping (DAV-1301+). Falls back to short_term /
    medium_term blocks, then to a single legacy top-level unit.
    """
    rd = report.get("result_data") or {}
    hz = rd.get("horizons")
    if isinstance(hz, dict) and hz:
        for key in sorted(hz):
            unit = hz[key]
            if isinstance(unit, dict):
                yield key, unit
        return
    seen_term = False
    for key, block in (("short", "short_term"), ("medium", "medium_term")):
        unit = rd.get(block)
        if isinstance(unit, dict) and unit:
            seen_term = True
            yield key, unit
    if seen_term:
        return
    # legacy single-horizon row
    if rd.get("decision") is not None or rd.get("analysis_status") is not None:
        yield rd.get("horizon") or "single", rd


def unit_label(key):
    return HORIZON_LABEL.get(key, f"horizon:{key}")


def unit_not_requested(unit):
    return unit.get("status") == "not_requested"


def unit_failed(report, unit):
    return report["status"] == "failed" or unit.get("status") == "failed"


def unit_analysis_status(unit):
    return unit.get("analysis_status")


def unit_decision(unit):
    return unit.get("decision") or unit.get("trade_action")


def unit_executable(unit):
    return (
        unit_analysis_status(unit) == "VALID"
        and (unit.get("trade_action") or unit.get("decision")) in EXECUTABLE_ACTIONS
    )


def t5_status_of(report, unit):
    """t_plus_5_status from the unit or report-level shadow_credit_metrics.

    In older rows the T+5 shadow writer stored it under report.result_data
    .shadow_credit_metrics (and duplicated under investment_debate_state);
    newer dual-horizon rows have not been written yet — those read as missing.
    Note: legacy rows carry a *single* t_plus_5_status at report level even
    when the report fans out into two horizon units; count it once per unit
    that shares the carrier (conservative: the T+5 writer evaluated the whole
    report, both horizons).
    """
    carriers = [unit, report.get("result_data") or {}]
    # legacy single rows keep it under short_term/investment_debate_state too
    rd = report.get("result_data") or {}
    for extra in ("short_term", "medium_term"):
        blk = rd.get(extra)
        if isinstance(blk, dict):
            carriers.append(blk)
    for carrier in carriers:
        scm = carrier.get("shadow_credit_metrics")
        if isinstance(scm, dict) and scm.get("t_plus_5_status"):
            return scm["t_plus_5_status"]
        if carrier.get("t_plus_5_status"):
            return carrier["t_plus_5_status"]
        ids = carrier.get("investment_debate_state")
        if isinstance(ids, dict):
            scm = ids.get("shadow_credit_metrics")
            if isinstance(scm, dict) and scm.get("t_plus_5_status"):
                return scm["t_plus_5_status"]
            if ids.get("t_plus_5_status"):
                return ids["t_plus_5_status"]
    return None


def forecast_of(unit):
    f = unit.get("forecast")
    return f if isinstance(f, dict) else {}


def price_plan_check_of(unit):
    c = unit.get("price_plan_check")
    return c if isinstance(c, dict) else {}


# --------------------------------------------------------------------------- #
# Criteria                                                                     #
# --------------------------------------------------------------------------- #

def metric_a1(units_rows):
    """List executable rows awaiting controller audit. No judgement."""
    rows = [r for r in units_rows if r["executable"]]
    return {
        "threshold": "假 clean = 0（总控逐档审计；一旦出现即回退并清零）",
        "executable_units": len(rows),
        "audit_manifest": [
            {
                "report_id": r["report_id"],
                "symbol": r["symbol"],
                "trade_date": r["trade_date"],
                "horizon": r["horizon_label"],
                "decision": r["decision"],
                "direction": r["direction"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "note": "本脚本不判定假 clean；上表为待总控逐档审计清单",
    }


def metric_a2(units_rows, window_days=10):
    """Rolling qualified-clean rate over all in-scope completed units."""
    complete = [r for r in units_rows if r["completed"] and not r["not_requested"]]
    clean = [r for r in complete if r["qualified_clean"]]
    rate = (len(clean) / len(complete) * 100) if complete else None
    # rolling: group by trade_date, take last `window_days` dates
    by_date = OrderedDict()
    for r in complete:
        by_date.setdefault(r["trade_date"], []).append(r)
    dates = sorted(by_date)
    tail = dates[-window_days:] if dates else []
    tail_units = [r for d in tail for r in by_date[d]]
    tail_clean = [r for r in tail_units if r["qualified_clean"]]
    tail_rate = (len(tail_clean) / len(tail_units) * 100) if tail_units else None
    return {
        "threshold": "滚动 10 个交易日合格 clean 率 ≥ 4.2%（48 档/日）",
        "window_days": window_days,
        "all_scope": {
            "completed_units": len(complete),
            "qualified_clean": len(clean),
            "rate_pct": round(rate, 3) if rate is not None else None,
            "gap_to_4.2": round(rate - 4.2, 3) if rate is not None else None,
        },
        "rolling_window": {
            "dates": tail,
            "days": len(tail),
            "completed_units": len(tail_units),
            "qualified_clean": len(tail_clean),
            "rate_pct": round(tail_rate, 3) if tail_rate is not None else None,
            "gap_to_4.2": round(tail_rate - 4.2, 3) if tail_rate is not None else None,
            "meets": (tail_rate >= 4.2) if tail_rate is not None else None,
        },
        "direction_watch": {
            "buy": sum(1 for r in clean if r["decision"] == "BUY"),
            "sell": sum(1 for r in clean if r["decision"] == "SELL"),
        },
    }


def _debate_session(report, horizon_key, unit):
    ids = unit.get("investment_debate_state")
    if not isinstance(ids, dict):
        return None
    claims = ids.get("claims") or []
    ev = unit.get("evidence_verification") or []
    # verified evidence item count per claim_id
    verified_by_claim = Counter()
    for item in ev:
        if not isinstance(item, dict):
            continue
        cid = item.get("claim_id")
        if cid and item.get("status") == "verified":
            verified_by_claim[cid] += 1
    opening = [c for c in claims if c.get("stage") == "opening"]
    side_of = {"Bull": "bull", "Bear": "bear"}
    side_counts = Counter()
    verified_per_side = Counter()
    for c in claims:
        side = side_of.get(c.get("speaker_key"))
        if side:
            side_counts[side] += 1
            verified_per_side[side] += verified_by_claim.get(c.get("claim_id"), 0)
    effective = len(opening) >= 3 and sum(verified_per_side.values()) >= 1
    return {
        "report_id": report["id"],
        "created_at": report["created_at"],
        "trade_date": report["trade_date"],
        "horizon": horizon_key,
        "claims_total": len(claims),
        "opening_claims": len(opening),
        "bull_claims": side_counts.get("bull", 0),
        "bear_claims": side_counts.get("bear", 0),
        "bull_verified": verified_per_side.get("bull", 0),
        "bear_verified": verified_per_side.get("bear", 0),
        "effective": effective,
    }


def metric_a3(sessions, window=50):
    eff = [s for s in sessions if s["effective"]]
    bull_sessions = sum(1 for s in eff if s["bull_claims"] > 0)
    bear_sessions = sum(1 for s in eff if s["bear_claims"] > 0)
    # side counts per session: a session counts toward the side it fielded claims for
    bull_verified = sum(s["bull_verified"] for s in eff)
    bear_verified = sum(s["bear_verified"] for s in eff)
    tail = eff[-window:]
    tail_sides = [s for s in tail if (s["bull_claims"] > 0) != (s["bear_claims"] > 0)]
    # D-006's [40%,60%] applies to the share of bull-vs-bear effective sessions.
    # Sessions almost always field both sides; fall back to per-session stance
    # majority when both sides present, counting the session for the larger side.
    def dominant_side(s):
        if s["bull_claims"] > s["bear_claims"]:
            return "bull"
        if s["bear_claims"] > s["bull_claims"]:
            return "bear"
        return "tie"
    dom = Counter(dominant_side(s) for s in tail)
    denom = dom["bull"] + dom["bear"]
    bull_share = (dom["bull"] / denom * 100) if denom else None
    return {
        "threshold": "多方、空方有效场次各 ≥ 25；最近 50 场多空比例 ∈ [40%,60%]；各侧已核实论点 ≥ 100 条",
        "definition_note": "有效场次 = ≥3 个 opening 战场且 ≥1 条已核实论点；侧归属按 speaker_key",
        "sessions_total": len(sessions),
        "effective_sessions": len(eff),
        "sessions_with_bull_claims": bull_sessions,
        "sessions_with_bear_claims": bear_sessions,
        "gap_sessions_each_side": {
            "bull": max(0, 25 - bull_sessions),
            "bear": max(0, 25 - bear_sessions),
        },
        "verified_evidence": {
            "bull": bull_verified,
            "bear": bear_verified,
            "gap_each_side": {
                "bull": max(0, 100 - bull_verified),
                "bear": max(0, 100 - bear_verified),
            },
        },
        "last_50_effective": {
            "sessions": len(tail),
            "dominant_side_distribution": dict(dom),
            "bull_share_pct": round(bull_share, 2) if bull_share is not None else None,
            "ratio_in_[40,60]": (40.0 <= bull_share <= 60.0) if bull_share is not None else None,
        },
        "caliber_caveat": "多空场次指辩论双方；绝大多数双档会话两侧都有论点，按主导侧计数，口径待总控确认",
    }


def metric_a4(reports, units_rows, trade_dates, as_of):
    submitted = [r for r in reports if r["status"] in ("completed", "failed")]
    completed = [r for r in reports if r["status"] == "completed"]
    completion_rate = (len(completed) / len(submitted) * 100) if submitted else None

    mature_cutoff = None
    if trade_dates:
        before = [d for d in trade_dates if d <= as_of]
        if len(before) > T5_LAG_DAYS:
            mature_cutoff = before[-(T5_LAG_DAYS + 1)]  # date T such that T+5 <= as_of
    mature_units, t5_done, t5_reports, t5_reports_done = [], 0, set(), set()
    for r in units_rows:
        if not r["completed"] or r["not_requested"]:
            continue
        if mature_cutoff and r["trade_date"] > mature_cutoff:
            continue
        mature_units.append(r)
        if t5_status_of(r["report"], r["unit"]):
            t5_done += 1
            t5_reports_done.add(r["report_id"])
        t5_reports.add(r["report_id"])
    t5_rate = (t5_done / len(mature_units) * 100) if mature_units else None
    return {
        "threshold": "completed ≥ 95%；T+5 完整率 ≥ 95%（成熟样本 = trade_date 距 as_of ≥ 5 个交易日）",
        "as_of": as_of,
        "mature_cutoff": mature_cutoff,
        "submitted": len(submitted),
        "completed": len(completed),
        "failed": len(submitted) - len(completed),
        "completion_rate_pct": round(completion_rate, 3) if completion_rate is not None else None,
        "completion_gap_to_95": round(completion_rate - 95.0, 3) if completion_rate is not None else None,
        "mature_units": len(mature_units),
        "mature_reports": len(t5_reports),
        "t5_present": t5_done,
        "t5_reports_present": len(t5_reports_done),
        "t5_report_rate_pct": round(len(t5_reports_done) / len(t5_reports) * 100, 3) if t5_reports else None,
        "t5_rate_pct": round(t5_rate, 3) if t5_rate is not None else None,
        "t5_gap_to_95": round(t5_rate - 95.0, 3) if t5_rate is not None else None,
    }


def metric_b1(units_rows):
    total = len(units_rows)
    with_forecast = [r for r in units_rows if forecast_of(r["unit"])]
    b1 = [r for r in with_forecast if forecast_of(r["unit"]).get("contract") == B1_CONTRACT]
    return {
        "threshold": "B1 概率与价位输出契约上线（forecast.contract == 'b1.v1'，DAV-1431 字段）",
        "units_total": total,
        "units_with_forecast": len(with_forecast),
        "units_b1_v1": len(b1),
        "coverage_pct": round(len(b1) / total * 100, 3) if total else None,
        "live": len(b1) > 0,
        "note": "契约预计随 10-09 部署窗口上线；此前 N/A 属预期",
    }


def metric_b2(units_rows):
    rows = [r for r in units_rows
            if r["completed"] and not r["not_requested"]
            and forecast_of(r["unit"]).get("contract") == B1_CONTRACT]
    invalid = []
    for r in rows:
        f = forecast_of(r["unit"])
        c = price_plan_check_of(r["unit"])
        bad = f.get("status") != "valid" or c.get("status") in B1_PRICE_FAILURES
        if bad:
            invalid.append(r)
    rate = (len(invalid) / len(rows) * 100) if rows else None
    return {
        "threshold": "新契约下连续 ≥ 100 次运行无效率 ≤ 5%（forecast.status != valid 或 price_plan_check 失败即无效）",
        "b1_v1_units_completed": len(rows),
        "invalid": len(invalid),
        "invalid_pct": round(rate, 3) if rate is not None else None,
        "sample_gap_to_100": max(0, 100 - len(rows)),
        "meets": (rate <= 5.0) if (rate is not None and len(rows) >= 100) else None,
    }


def metric_b3(reports):
    total = len(reports)
    ok = 0
    missing = []
    for r in reports:
        rd = r["result_data"]
        snap = rd.get("input_snapshot") or {}
        served = rd.get("served_models")
        if snap.get("hash") and served:
            ok += 1
        else:
            missing.append(r["id"])
    return {
        "threshold": "每次运行可追溯 input_snapshot.hash 与 served_models（cf7bda3 契约）",
        "reports_total": total,
        "traceable": ok,
        "coverage_pct": round(ok / total * 100, 3) if total else None,
        "gap": total - ok,
        "missing_report_ids_sample": missing[:20],
    }


# --------------------------------------------------------------------------- #
# Calendar                                                                     #
# --------------------------------------------------------------------------- #

def load_trade_dates(args):
    """akshare bundled calendar (same file daily-batch uses) or pandas bdays."""
    cal = args.calendar_json
    if not cal:
        guess = Path.home() / "Documents/TradingAgents-AShare/.venv310/lib/python3.10/site-packages/akshare/file_fold/calendar.json"
        if guess.is_file():
            cal = str(guess)
    if cal and Path(cal).is_file():
        raw = json.loads(Path(cal).read_text())
        return sorted(f"{str(s)[:4]}-{str(s)[4:6]}-{str(s)[6:8]}" if "-" not in str(s) else str(s) for s in raw)
    try:
        import pandas as pd
        return [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-01", "2027-12-31")]
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def build_unit_rows(reports):
    rows = []
    for rep in reports:
        for hkey, unit in horizon_units(rep):
            nr = unit_not_requested(unit)
            row = {
                "report_id": rep["id"],
                "symbol": rep["symbol"],
                "trade_date": rep["trade_date"],
                "created_at": rep["created_at"],
                "report_status": rep["status"],
                "horizon": hkey,
                "horizon_label": unit_label(hkey),
                "report": rep,
                "unit": unit,
                "not_requested": nr,
                "completed": rep["status"] == "completed" and unit.get("status") != "failed",
                "failed": unit_failed(rep, unit),
                "analysis_status": unit_analysis_status(unit),
                "decision": (unit.get("trade_action") or unit.get("decision")),
                "direction": unit.get("direction"),
                "executable": unit_executable(unit),
                "qualified_clean": unit_executable(unit),  # post-gate VALID+BUY/SELL
            }
            rows.append(row)
    return rows


def render_markdown(summary, a1, a2, a3, a4, b1, b2, b3, meta):
    p = []
    p.append(f"# D-066 第一阶段出口进度表 — {meta['as_of']}（样本起算 {meta['start']}）")
    p.append("")
    p.append(f"- 库：`{meta['db']}`；账户：固定分析账户 `{ACCOUNT[:8]}…`；口径：只读、零模型")
    p.append(f"- 范围内报告 {meta['reports']} 份，档位单元 {meta['units']} 条（标签口径 horizon:short / horizon:medium）")
    p.append("")
    p.append("| # | 门槛 | 当前读数 | 距门槛 | 状态 |")
    p.append("|---|------|----------|--------|------|")

    def row(idx, thresh, reading, gap, status):
        p.append(f"| {idx} | {thresh} | {reading} | {gap} | {status} |")

    row("A1", "假 clean = 0",
        f"待审计可执行档 {a1['executable_units']} 条",
        "—（总控逐档判定）",
        "清单已出" if a1["executable_units"] else "无可执行档")
    rw = a2["rolling_window"]
    row("A2", "滚动 10 日合格 clean ≥ 4.2%",
        f"{rw['rate_pct']}%（{rw['qualified_clean']}/{rw['completed_units']}，{rw['days']} 日）" if rw["rate_pct"] is not None else "无样本",
        f"{rw['gap_to_4.2']:+.3f}pp" if rw["gap_to_4.2"] is not None else "—",
        "✅" if rw["meets"] else ("❌" if rw["meets"] is False else "N/A"))
    row("A3", "多空各 ≥25 场；近 50 场比例∈[40,60]；各侧已核实 ≥100",
        f"有效场次 {a3['effective_sessions']}（多 {a3['sessions_with_bull_claims']} / 空 {a3['sessions_with_bear_claims']}）；已核实 多 {a3['verified_evidence']['bull']} / 空 {a3['verified_evidence']['bear']}",
        f"场次差 {a3['gap_sessions_each_side']}；论点差 {a3['verified_evidence']['gap_each_side']}",
        "口径待确认")
    row("A4", "completed ≥ 95%；T+5 完整率 ≥ 95%",
        f"completed {a4['completion_rate_pct']}%（{a4['completed']}/{a4['submitted']}）；T+5 {a4['t5_rate_pct']}%（{a4['t5_present']}/{a4['mature_units']} 成熟档）" if a4["completion_rate_pct"] is not None else "无样本",
        f"完成率 {a4['completion_gap_to_95']:+.2f}pp；T+5 {a4['t5_gap_to_95']:+.2f}pp" if a4["completion_rate_pct"] is not None else "—",
        "✅/❌ 见读数" if a4["completion_rate_pct"] is not None else "N/A")
    row("B1", "b1.v1 契约上线",
        f"{b1['units_b1_v1']}/{b1['units_total']} 档带 b1.v1 forecast",
        "未上线" if not b1["live"] else "—",
        "✅" if b1["live"] else "N/A（10-09 部署窗口）")
    row("B2", "连续 ≥100 次运行无效率 ≤5%",
        f"{b2['b1_v1_units_completed']} 次 b1.v1 运行，无效 {b2['invalid']}（{b2['invalid_pct']}%）" if b2["b1_v1_units_completed"] else "无 b1.v1 样本",
        f"样本差 {b2['sample_gap_to_100']}" if b2["b1_v1_units_completed"] else "—",
        "✅" if b2["meets"] else ("❌" if b2["meets"] is False else "N/A"))
    row("B3", "运行可追溯 input hash + served_models",
        f"{b3['traceable']}/{b3['reports_total']} 份可追溯（{b3['coverage_pct']}%）",
        f"缺 {b3['gap']} 份",
        "✅" if b3["gap"] == 0 and b3["reports_total"] else "❌")
    p.append("")
    p.append("## A1 待审计可执行档清单")
    if a1["audit_manifest"]:
        p.append("| report_id | symbol | trade_date | 档位 | 动作 | 方向 | created_at |")
        p.append("|---|---|---|---|---|---|---|")
        for r in a1["audit_manifest"]:
            p.append(f"| `{r['report_id']}` | {r['symbol']} | {r['trade_date']} | {r['horizon']} | {r['decision']} | {r['direction']} | {r['created_at']} |")
    else:
        p.append("（范围内无 VALID+BUY/SELL 档）")
    p.append("")
    p.append(f"> 口径注记：{a3['caliber_caveat']}。A4 成熟截止 {a4['mature_cutoff']}。B1 契约 10-09 才部署，B2 之前 N/A 属预期。")
    return "\n".join(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="path to a COPY/snapshot of tradingagents.db (opened ro)")
    ap.add_argument("--account", default=ACCOUNT, help="fixed analysis account uuid")
    ap.add_argument("--start", default=DEFAULT_START, help="sample start trade_date (default 2026-10-08)")
    ap.add_argument("--end", help="optional last trade_date")
    ap.add_argument("--as-of", default=date.today().isoformat(), help="reading date (default today)")
    ap.add_argument("--ids", help="JSON file with an explicit list of report ids")
    ap.add_argument("--calendar-json", help="path to akshare file_fold/calendar.json")
    ap.add_argument("--window-days", type=int, default=10, help="A2 rolling window (default 10)")
    ap.add_argument("--all-history", action="store_true",
                    help="also compute A2/A4 over the whole account history (cohort comparison)")
    ap.add_argument("--csv-dir", help="optional dir for A1 audit manifest + per-unit CSV")
    ap.add_argument("--json", action="store_true", help="emit raw JSON instead of markdown")
    ap.add_argument("--self-test", action="store_true", help="run in-memory checks and exit")
    args = ap.parse_args()

    if args.self_test:
        _self_test()
        return
    if not args.db:
        ap.error("--db is required (unless --self-test)")

    ids = None
    if args.ids:
        ids = json.loads(Path(args.ids).read_text())
        if not isinstance(ids, list) or any(not isinstance(v, str) for v in ids):
            ap.error("--ids must be a JSON array of report-id strings")

    con = open_ro(args.db)
    reports = load_reports(con, args.account, start=args.start, end=args.end, ids=ids)
    units_rows = build_unit_rows(reports)
    sessions = []
    for rep in reports:
        for hkey, unit in horizon_units(rep):
            s = _debate_session(rep, hkey, unit)
            if s:
                sessions.append(s)
    sessions.sort(key=lambda s: (s["created_at"], s["report_id"]))

    trade_dates = load_trade_dates(args)

    a1 = metric_a1(units_rows)
    a2 = metric_a2(units_rows, window_days=args.window_days)
    a3 = metric_a3(sessions)
    a4 = metric_a4(reports, units_rows, trade_dates, args.as_of)
    b1 = metric_b1(units_rows)
    b2 = metric_b2(units_rows)
    b3 = metric_b3(reports)

    all_hist = None
    if args.all_history:
        hist = load_reports(con, args.account, start=None, end=args.end, ids=None)
        hist_units = build_unit_rows(hist)
        all_hist = {
            "reports": len(hist),
            "units": len(hist_units),
            "a2": metric_a2(hist_units, window_days=args.window_days),
            "a4": metric_a4(hist, hist_units, trade_dates, args.as_of),
            "status_counts": status_counts_all_time(con, args.account),
        }

    meta = {
        "db": str(Path(args.db).resolve()),
        "account": args.account,
        "start": args.start,
        "end": args.end,
        "as_of": args.as_of,
        "reports": len(reports),
        "units": len(units_rows),
        "calendar": "akshare" if args.calendar_json else ("bundled" if trade_dates else "unavailable"),
    }
    summary = {
        "meta": meta,
        "A1": a1, "A2": a2, "A3": a3, "A4": a4,
        "B1": b1, "B2": b2, "B3": b3,
        "all_history_side_reading": all_hist,
    }

    if args.csv_dir:
        outdir = Path(args.csv_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        if a1["audit_manifest"]:
            with (outdir / "a1_audit_manifest.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(a1["audit_manifest"][0]))
                w.writeheader()
                w.writerows(a1["audit_manifest"])
        with (outdir / "units.csv").open("w", newline="", encoding="utf-8") as f:
            cols = ["report_id", "symbol", "trade_date", "horizon_label", "report_status",
                    "analysis_status", "decision", "direction", "qualified_clean", "failed"]
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in units_rows:
                w.writerow({c: r[c] for c in cols})

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(summary, a1, a2, a3, a4, b1, b2, b3, meta))


# --------------------------------------------------------------------------- #
# Self test                                                                    #
# --------------------------------------------------------------------------- #

def _self_test():
    u_valid_buy = {"status": "completed", "analysis_status": "VALID", "decision": "BUY",
                   "trade_action": "BUY", "direction": "BULL",
                   "investment_debate_state": {"claims": [
                       {"claim_id": "C1", "speaker_key": "Bull", "stage": "opening"},
                       {"claim_id": "C2", "speaker_key": "Bull", "stage": "opening"},
                       {"claim_id": "C3", "speaker_key": "Bear", "stage": "opening"},
                   ]},
                   "evidence_verification": [{"claim_id": "C1", "status": "verified"}],
                   "shadow_credit_metrics": {"t_plus_5_status": "done"},
                   "forecast": {"contract": "b1.v1", "status": "valid", "p_rel_t10": 60},
                   "price_plan_check": {"status": "ok", "original_executable": True, "original_action": "BUY"}}
    u_abstain = {"status": "completed", "analysis_status": "ABSTAIN", "decision": "WAIT"}
    reports = [
        {"id": "r1", "user_id": ACCOUNT, "symbol": "X", "trade_date": "2026-10-08",
         "status": "completed", "created_at": "2026-10-08 01:00",
         "result_data": {"horizons": {"short": u_valid_buy, "medium": u_abstain},
                         "input_snapshot": {"hash": "abc"}, "served_models": {"m": 1}}},
        {"id": "r2", "user_id": ACCOUNT, "symbol": "Y", "trade_date": "2026-10-08",
         "status": "failed", "created_at": "2026-10-08 02:00",
         "result_data": {"horizons": {"short": {"status": "failed"}, "medium": {"status": "failed"}}}},
    ]
    rows = build_unit_rows(reports)
    assert len(rows) == 4
    a1 = metric_a1(rows)
    assert a1["executable_units"] == 1 and a1["audit_manifest"][0]["horizon"] == "horizon:short"
    a2 = metric_a2(rows)
    assert a2["all_scope"]["completed_units"] == 2 and a2["all_scope"]["qualified_clean"] == 1
    sess = [s for rep in reports for hk, u in horizon_units(rep) for s in [_debate_session(rep, hk, u)] if s]
    a3 = metric_a3(sess)
    assert a3["effective_sessions"] == 1 and a3["verified_evidence"]["bull"] == 1
    b1 = metric_b1(rows)
    assert b1["units_b1_v1"] == 1 and b1["live"]
    b2 = metric_b2(rows)
    assert b2["b1_v1_units_completed"] == 1 and b2["invalid"] == 0
    b3 = metric_b3(reports)
    assert b3["traceable"] == 1 and b3["gap"] == 1
    print(json.dumps({"self_test": "passed", "units": len(rows), "model_calls": 0, "provider_calls": 0}))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
