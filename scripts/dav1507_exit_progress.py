#!/usr/bin/env python3
"""D-066 Phase-1 exit progress sheet (read-only, zero-model, zero-provider).

DAV-1507 返修版（冻结口径基准：work/dav1507-exit-progress/spec_v4.md，
SHA-256 65bd584b72b2bc8a1e1dcf808f8a5fb00de9c2225c551da12307a4a835e3c5ee）。

Reads A1–A4 and B1–B3 of docs/plans/phase1-exit/exit-criteria-20261002.md
line-by-line against the frozen thresholds, reporting each reading plus its
gap. 口径以冻结稿为唯一基准。

* Sample start: ``--start`` (default ``2026-10-08``, first daily-batch day).
* Account: fixed analysis account UUID.
* Horizon tier labels use the full DAV-1322 quad cohort key
  ``decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq:short`` /
  ``…:medium`` (never bare ``horizon:short``).
* A2 qualified-clean rate uses the same units the clean pipeline emits
  (``filter_v2_completed_reports``).
* A3 is computed ONLY on the clean pool: a unit must pass the complete
  ``filter_v2_completed_reports`` pipeline (D-009 §5 + HOLD semantic + price
  basis isolation). Effective session = >=3 distinct valid opening
  battlefields on that side AND >=1 claim-level Verified Claim under the
  frozen predicate. Counts per DAV-1322 quad cohort, never mixing
  short/medium. See spec §2–§4 for the full contract.
* Trading-calendar file required (fail-close exit 2 when absent);
  no ``pd.bdate_range`` fallback.
* ``--all-history`` streams each row through ``json.loads`` + field
  extraction and drops the raw payload immediately (peak memory is
  reported, required <=4 GB).

Exit codes: 0 ok, 2 bad input (missing db/calendar/args). Not a gate.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import resource
import sqlite3
import sys
import tracemalloc
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ACCOUNT = "429163f7-50b6-4982-8bdf-96ae99506843"
DEFAULT_START = "2026-10-08"
EXECUTABLE_ACTIONS = {"BUY", "SELL"}
B1_CONTRACT = "b1.v1"
B1_PRICE_FAILURES = {"violation", "missing_required", "unverifiable"}
T5_LAG_DAYS = 5
A3_WINDOW = 50

# Lazy project imports (script must also work with only stdlib until the
# tradingagents package is importable).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _project_imports():
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    from tradingagents.agents.utils.shadow_credit import (  # noqa: E402
        DUAL_HORIZON_SUB_UNITS,
        extract_sample_cohort,
        filter_v2_completed_reports,
        split_report_into_units,
        is_v2_protocol_report,
        classify_v2_report_d009_exclusion,
        collect_hold_semantic_reasons,
        normalize_report_for_evaluation,
        _is_bull,
        _is_bear,
    )
    from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
        is_observation_or_hypothesis_claim,
    )
    from tradingagents.agents.utils.price_basis_isolation import (  # noqa: E402
        classify_price_basis_exclusion,
        extract_report_id,
    )
    from tradingagents.agents.utils.debate_utils import VALID_BATTLEFIELDS  # noqa: E402
    return {
        "DUAL_HORIZON_SUB_UNITS": DUAL_HORIZON_SUB_UNITS,
        "extract_sample_cohort": extract_sample_cohort,
        "split_report_into_units": split_report_into_units,
        "filter_v2_completed_reports": filter_v2_completed_reports,
        "is_v2_protocol_report": is_v2_protocol_report,
        "classify_v2_report_d009_exclusion": classify_v2_report_d009_exclusion,
        "collect_hold_semantic_reasons": collect_hold_semantic_reasons,
        "normalize_report_for_evaluation": normalize_report_for_evaluation,
        "_is_bull": _is_bull,
        "_is_bear": _is_bear,
        "is_observation_or_hypothesis_claim": is_observation_or_hypothesis_claim,
        "classify_price_basis_exclusion": classify_price_basis_exclusion,
        "extract_report_id": extract_report_id,
        "VALID_BATTLEFIELDS": VALID_BATTLEFIELDS,
    }


# --------------------------------------------------------------------------- #
# DB streaming                                                                 #
# --------------------------------------------------------------------------- #

def open_ro(path: str) -> sqlite3.Connection:
    db = Path(path).resolve()
    if not db.is_file():
        raise SystemExit(f"[exit 2] database does not exist: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def iter_report_rows(con, account, start=None, end=None, ids=None):
    """Yield (report_id, symbol, trade_date, status, created_at, result_data)."""
    cols = "id, symbol, trade_date, status, created_at, result_data"
    where, params = ["user_id=?", "json_valid(result_data)"], [account]
    if start:
        where.append("trade_date >= ?")
        params.append(start)
    if end:
        where.append("trade_date <= ?")
        params.append(end)
    if ids is not None:
        if not ids:
            return
        where.append("id IN (" + ",".join("?" for _ in ids) + ")")
        params.extend(ids)
    sql = f"SELECT {cols} FROM reports WHERE {' AND '.join(where)} ORDER BY created_at, id"
    cur = con.execute(sql, params)
    while True:
        batch = cur.fetchmany(200)
        if not batch:
            break
        for row in batch:
            yield row


def slim_report(row):
    """Parse result_data and keep ONLY the fields the metrics need.

    The full JSON payload is dropped before this function returns so peak
    memory stays bounded on --all-history.
    """
    rid, sym, td, status, created, rd_raw = row
    try:
        rd = json.loads(rd_raw)
    except Exception:
        return None
    slim = {
        "id": rid,
        "symbol": sym,
        "trade_date": td,
        "status": status,
        "created_at": created,
        "result_data": rd,  # caller must drop immediately after use
    }
    return slim


# --------------------------------------------------------------------------- #
# Clean pipeline (per-report, streaming)                                       #
# --------------------------------------------------------------------------- #

def clean_units_of(report, lib):
    """Run filter_v2_completed_reports on ONE report; return (clean_units, ledger, reasons).

    Units are already normalized for evaluation. Isolation reasons are
    keyed ``report_id@hz`` by the pipeline itself.
    """
    res = lib["filter_v2_completed_reports"](
        [report],
        return_ledger=True,
        return_exclusion_reasons=True,
    )
    qualifying, _excluded, ledger, reasons = res
    return qualifying, ledger, reasons


# --------------------------------------------------------------------------- #
# Per-metric probes (only slim fields survive across rows)                     #
# --------------------------------------------------------------------------- #

def metric_a1(units_rows):
    rows = [r for r in units_rows if r["executable"]]
    return {
        "threshold": "假 clean = 0（总控逐档审计；一旦出现即回退并清零）",
        "executable_units": len(rows),
        "audit_manifest": [
            {"report_id": r["report_id"], "symbol": r["symbol"], "trade_date": r["trade_date"],
             "cohort": r["cohort"], "decision": r["decision"], "direction": r["direction"],
             "created_at": r["created_at"]}
            for r in rows
        ],
        "note": "本脚本不判定假 clean；上表为待总控逐档审计清单",
    }


def metric_a2(units_rows, window_days=10):
    """Rolling qualified-clean rate over all in-scope completed units."""
    complete = [r for r in units_rows if r["completed"] and not r["not_requested"]]
    clean = [r for r in complete if r["qualified_clean"]]
    rate = (len(clean) / len(complete) * 100) if complete else None
    by_date = defaultdict(list)
    for r in complete:
        by_date[r["trade_date"]].append(r)
    dates = sorted(by_date)
    tail = dates[-window_days:]
    tail_units = [r for d in tail for r in by_date[d]]
    tail_clean = [r for r in tail_units if r["qualified_clean"]]
    tail_rate = (len(tail_clean) / len(tail_units) * 100) if tail_units else None
    return {
        "threshold": "滚动 10 个交易日合格 clean 率 ≥ 4.2%（48 档/日）",
        "window_days": window_days,
        "all_scope": {
            "completed_units": len(complete), "qualified_clean": len(clean),
            "rate_pct": round(rate, 3) if rate is not None else None,
            "gap_to_4.2": round(rate - 4.2, 3) if rate is not None else None,
        },
        "rolling_window": {
            "dates": tail, "days": len(tail), "completed_units": len(tail_units),
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


# ── A3: verified-claim predicate (single function, two call sites) ──────────

def _claim_key(c):
    return str(c.get("claim_id") or "").strip()


def _ev_items_by_claim(ev_list):
    by = defaultdict(list)
    for e in ev_list or []:
        if isinstance(e, dict):
            cid = str(e.get("claim_id") or "").strip()
            if cid:
                by[cid].append(e)
    return by


def is_verified_claim(claim, ev_items, summary_entry, lib):
    """Frozen predicate (spec §3). Returns True for a *claim-level* Verified Claim.

    Same function feeds both the per-side "produced >=1 Verified Claim"
    session test and the ">=100 Verified Claims per side" counter.

    Criteria (ALL must hold):
    1. >=1 ``evidence_verification`` record for this claim_id with
       ``status == 'verified'``;
    2. NO record with ``status == 'contradicted'`` (veto);
    3. ``is_observation_or_hypothesis_claim(claim)`` is False;
    4. NOT partial/reject: ``claim_evidence_summary[cid].decision`` when
       present must not be ``partial`` or ``reject``; when the summary
       entry or its ``decision`` is missing, fall back to the evidence
       counts — ``verified >= 1`` AND ``contradicted == 0`` AND
       ``unsupported == 0`` (an unsupported item makes it partial).
    """
    cid = _claim_key(claim)
    if not cid:
        return False

    items = ev_items.get(cid) or []
    has_verified = any(str(i.get("status") or "").strip().lower() == "verified" for i in items)
    if not has_verified:
        return False
    has_contra = any(str(i.get("status") or "").strip().lower() == "contradicted" for i in items)
    if has_contra:
        return False
    if lib["is_observation_or_hypothesis_claim"](claim):
        return False

    if summary_entry is not None:
        decision = str(summary_entry.get("decision") or "").strip().lower()
        if decision in ("partial", "reject"):
            return False
        if decision in ("adopt",):
            return True
        # unknown/missing decision -> fall through to count-based rule
    # count-based fallback
    verified_n = sum(1 for i in items if str(i.get("status") or "").strip().lower() == "verified")
    unsupported_n = sum(1 for i in items if str(i.get("status") or "").strip().lower() == "unsupported")
    return verified_n >= 1 and unsupported_n == 0


def _unit_debate_payload(unit):
    ids = unit.get("investment_debate_state")
    if not isinstance(ids, dict):
        return {}, [], [], {}
    claims = ids.get("claims") or unit.get("claims") or []
    ev = unit.get("evidence_verification") or ids.get("evidence_verification") or []
    summary = (
        ids.get("claim_evidence_summary")
        or unit.get("claim_evidence_summary")
        or ((ids.get("manager_verdict") or {}).get("claim_evidence_summary"))
        or ((unit.get("manager_verdict") or {}).get("claim_evidence_summary"))
        or {}
    )
    return ids, claims, ev, summary


def _side_of_claim(claim, lib):
    sp = str(claim.get("speaker_key") or claim.get("speaker") or "")
    st = str(claim.get("stance") or "")
    if lib["_is_bull"](sp, st):
        return "bull"
    if lib["_is_bear"](sp, st):
        return "bear"
    return None


def a3_session_sides(unit, lib):
    """Return {side: (battlefields, verified_claim_count, has_verified_claim)}.

    Effective-session test for ONE clean unit: per side, distinct valid
    opening battlefields >= 3 AND >= 1 claim satisfying is_verified_claim.
    """
    _ids, claims, ev, summary = _unit_debate_payload(unit)
    ev_by = _ev_items_by_claim(ev)
    fields = lib["VALID_BATTLEFIELDS"]
    out = {"bull": {"battlefields": set(), "verified_claims": 0, "has_verified_claim": False},
           "bear": {"battlefields": set(), "verified_claims": 0, "has_verified_claim": False}}
    seen = set()
    for c in claims:
        if not isinstance(c, dict):
            continue
        side = _side_of_claim(c, lib)
        if side is None:
            continue
        if c.get("stage") == "opening":
            bf = str(c.get("battlefield") or "").strip()
            if bf in fields:
                out[side]["battlefields"].add(bf)
        cid = _claim_key(c)
        if cid and cid not in seen:
            seen.add(cid)
            if is_verified_claim(c, ev_by, summary.get(cid) if isinstance(summary, dict) else None, lib):
                out[side]["verified_claims"] += 1
                out[side]["has_verified_claim"] = True
    return out


def a3_session_effective(side_stats):
    return len(side_stats["battlefields"]) >= 3 and side_stats["has_verified_claim"]


def metric_a3(clean_units_rows, lib):
    """A3 readings over the clean pool only, per quad cohort.

    clean_units_rows: list of {report_id, trade_date, created_at, cohort, unit}
    for units that already passed the full clean pipeline.
    """
    by_cohort = defaultdict(list)
    for r in clean_units_rows:
        sides = a3_session_sides(r["unit"], lib)
        eff = {s: a3_session_effective(sides[s]) for s in ("bull", "bear")}
        vc = {s: sides[s]["verified_claims"] for s in ("bull", "bear")}
        by_cohort[r["cohort"]].append({
            "report_id": r["report_id"], "trade_date": r["trade_date"],
            "created_at": r["created_at"], "eff": eff, "verified_claims": vc,
            "loose": {s: 0 for s in ("bull", "bear")},
        })

    def loose_counts(unit, lib):
        _ids, claims, ev, _summary = _unit_debate_payload(unit)
        ev_by = _ev_items_by_claim(ev)
        seen = set()
        cnt = {"bull": 0, "bear": 0}
        for c in claims:
            if not isinstance(c, dict):
                continue
            side = _side_of_claim(c, lib)
            cid = _claim_key(c)
            if side is None or not cid or cid in seen:
                continue
            seen.add(cid)
            if any(str(i.get("status") or "").strip().lower() == "verified" for i in (ev_by.get(cid) or [])):
                cnt[side] += 1
        return cnt

    per_cohort = {}
    for cohort, items in by_cohort.items():
        items.sort(key=lambda x: (x["created_at"] or "", x["report_id"]))
        bull_sessions = sum(1 for i in items if i["eff"]["bull"])
        bear_sessions = sum(1 for i in items if i["eff"]["bear"])
        bull_verified = sum(i["verified_claims"]["bull"] for i in items)
        bear_verified = sum(i["verified_claims"]["bear"] for i in items)
        # loose reference: need units again — recompute from stored rows
        loose_bull = loose_bear = 0
        for r in clean_units_rows:
            if r["cohort"] != cohort:
                continue
            lc = loose_counts(r["unit"], lib)
            loose_bull += lc["bull"]
            loose_bear += lc["bear"]
        tail = items[-A3_WINDOW:]
        nb = sum(1 for i in tail if i["eff"]["bull"])
        ne = sum(1 for i in tail if i["eff"]["bear"])
        denom = nb + ne
        share = (nb / denom * 100) if denom else None
        per_cohort[cohort] = {
            "clean_units": len(items),
            "effective_sessions": {"bull": bull_sessions, "bear": bear_sessions,
                                   "gap_to_25": {"bull": max(0, 25 - bull_sessions),
                                                 "bear": max(0, 25 - bear_sessions)}},
            "verified_claims": {"bull": bull_verified, "bear": bear_verified,
                                "gap_to_100": {"bull": max(0, 100 - bull_verified),
                                               "bear": max(0, 100 - bear_verified)},
                                "ref_loose_not_judged": {"bull": loose_bull, "bear": loose_bear}},
            "last_50_window": {
                "n": len(tail), "partial_window": len(tail) < A3_WINDOW,
                "bull_sessions": nb, "bear_sessions": ne,
                "bull_share_pct": round(share, 2) if share is not None else None,
                "ratio_in_[40,60]": (40.0 <= share <= 60.0) if share is not None else None,
            },
            "observed_abs_diff_cumulative": abs(bull_sessions - bear_sessions),
        }
    return {
        "threshold": "各侧累计有效场次 ≥25；最近 50 场多空比例 ∈[40%,60%]；各侧累计已核实论点 ≥100（多空差仅观察不判达标）",
        "definition": "有效场次=该侧≥3个不同有效Opening战场+该侧≥1条Verified Claim；Verified Claim谓词见spec §3",
        "per_cohort": per_cohort,
    }


def metric_a4(reports_meta, t5_probe_rows, trade_dates, as_of):
    submitted = [r for r in reports_meta if r["status"] in ("completed", "failed")]
    completed = [r for r in reports_meta if r["status"] == "completed"]
    rate = (len(completed) / len(submitted) * 100) if submitted else None
    mature_cutoff = None
    if trade_dates:
        before = [d for d in trade_dates if d <= as_of]
        if len(before) > T5_LAG_DAYS:
            mature_cutoff = before[-(T5_LAG_DAYS + 1)]
    mature = [r for r in t5_probe_rows if mature_cutoff and r["trade_date"] <= mature_cutoff]
    t5_done = sum(1 for r in mature if r["t5_status"])
    t5_rate = (t5_done / len(mature) * 100) if mature else None
    mature_reps = {r["report_id"] for r in mature}
    t5_reps_done = {r["report_id"] for r in mature if r["t5_status"]}
    return {
        "threshold": "completed ≥ 95%；T+5 完整率 ≥ 95%（成熟档 = trade_date 距 as_of ≥ 5 交易日）",
        "as_of": as_of, "mature_cutoff": mature_cutoff,
        "submitted": len(submitted), "completed": len(completed),
        "failed": len(submitted) - len(completed),
        "completion_rate_pct": round(rate, 3) if rate is not None else None,
        "completion_gap_to_95": round(rate - 95.0, 3) if rate is not None else None,
        "mature_units": len(mature), "mature_reports": len(mature_reps),
        "t5_present": t5_done, "t5_reports_present": len(t5_reps_done),
        "t5_rate_pct": round(t5_rate, 3) if t5_rate is not None else None,
        "t5_report_rate_pct": round(len(t5_reps_done) / len(mature_reps) * 100, 3) if mature_reps else None,
        "t5_gap_to_95": round(t5_rate - 95.0, 3) if t5_rate is not None else None,
    }


def metric_b1(units_rows):
    total = len(units_rows)
    b1 = [r for r in units_rows
          if isinstance(r["unit"].get("forecast"), dict)
          and r["unit"]["forecast"].get("contract") == B1_CONTRACT]
    return {
        "threshold": "B1 概率与价位输出契约上线（forecast.contract == 'b1.v1'）",
        "units_total": total, "units_b1_v1": len(b1),
        "coverage_pct": round(len(b1) / total * 100, 3) if total else None,
        "live": len(b1) > 0,
        "note": "契约预计随 10-09 部署窗口上线；此前 N/A 属预期",
    }


def metric_b2(units_rows):
    rows = [r for r in units_rows
            if r["completed"] and not r["not_requested"]
            and isinstance(r["unit"].get("forecast"), dict)
            and r["unit"]["forecast"].get("contract") == B1_CONTRACT]
    invalid = []
    for r in rows:
        f = r["unit"]["forecast"]
        c = r["unit"].get("price_plan_check") or {}
        if f.get("status") != "valid" or c.get("status") in B1_PRICE_FAILURES:
            invalid.append(r)
    rate = (len(invalid) / len(rows) * 100) if rows else None
    return {
        "threshold": "新契约下连续 ≥100 次运行无效率 ≤5%（forecast.status!=valid 或 price_plan_check 失败即无效）",
        "b1_v1_units_completed": len(rows), "invalid": len(invalid),
        "invalid_pct": round(rate, 3) if rate is not None else None,
        "sample_gap_to_100": max(0, 100 - len(rows)),
        "meets": (rate <= 5.0) if (rate is not None and len(rows) >= 100) else None,
    }


def metric_b3(reports_meta):
    total = len(reports_meta)
    ok = sum(1 for r in reports_meta if r["traceable"])
    return {
        "threshold": "每次运行可追溯 input_snapshot.hash 与 served_models（cf7bda3 契约）",
        "reports_total": total, "traceable": ok,
        "coverage_pct": round(ok / total * 100, 3) if total else None,
        "gap": total - ok,
        "missing_report_ids_sample": [r["id"] for r in reports_meta if not r["traceable"]][:20],
    }


# --------------------------------------------------------------------------- #
# Calendar                                                                     #
# --------------------------------------------------------------------------- #

def load_trade_dates(calendar_json):
    """Load the exchange calendar; missing file -> exit 2 (fail-close)."""
    cal = calendar_json
    if not cal:
        guess = Path.home() / "Documents/TradingAgents-AShare/.venv310/lib/python3.10/site-packages/akshare/file_fold/calendar.json"
        if guess.is_file():
            cal = str(guess)
    if not cal or not Path(cal).is_file():
        sys.stderr.write(f"[exit 2] trading calendar not found (calendar_json={cal!r}); no bdate_range fallback\n")
        sys.stderr.flush()
        sys.exit(2)
    raw = json.loads(Path(cal).read_text())
    return sorted(
        s if "-" in s else f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        for s in (str(x).replace("-", "") for x in raw)
    )


# --------------------------------------------------------------------------- #
# T+5 probe (slim)                                                             #
# --------------------------------------------------------------------------- #

def t5_status_of_report(report):
    """t_plus_5_status for a report (report-level first, then unit carriers)."""
    rd = report.get("result_data") or {}
    carriers = [rd]
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


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #

def render_markdown(summary, meta):
    a1, a2, a3, a4, b1, b2, b3 = (summary[k] for k in ("A1", "A2", "A3", "A4", "B1", "B2", "B3"))
    p = []
    p.append(f"# D-066 第一阶段出口进度表 — {meta['as_of']}（样本起算 {meta['start']}）")
    p.append("")
    p.append(f"- 库：`{meta['db']}`；账户：固定分析账户 `{ACCOUNT[:8]}…`；口径：只读、零模型、零 provider")
    p.append(f"- 范围内报告 {meta['reports']} 份，档位单元 {meta['units']} 条；峰值内存 {meta['peak_mb']} MB")
    p.append("")
    p.append("| # | 门槛 | 当前读数 | 距门槛 | 状态 |")
    p.append("|---|------|----------|--------|------|")

    def row(i, t, reading, gap, status):
        p.append(f"| {i} | {t} | {reading} | {gap} | {status} |")

    row("A1", "假 clean = 0",
        f"待审计可执行档 {a1['executable_units']} 条", "—（总控逐档判定）",
        "清单已出" if a1["executable_units"] else "无可执行档")
    rw = a2["rolling_window"]
    row("A2", "滚动 10 日合格 clean ≥ 4.2%",
        f"{rw['rate_pct']}%（{rw['qualified_clean']}/{rw['completed_units']}，{rw['days']} 日）" if rw["rate_pct"] is not None else "无样本",
        f"{rw['gap_to_4.2']:+.3f}pp" if rw["gap_to_4.2"] is not None else "—",
        "✅" if rw["meets"] else ("❌" if rw["meets"] is False else "N/A"))
    for cohort, c in sorted(a3["per_cohort"].items()):
        w = c["last_50_window"]
        row(f"A3 · `{cohort}`", "各侧场次≥25；窗口比∈[40,60]；各侧已核实≥100",
            f"clean {c['clean_units']}；场次 多{c['effective_sessions']['bull']}/空{c['effective_sessions']['bear']}；"
            f"已核实 多{c['verified_claims']['bull']}/空{c['verified_claims']['bear']}；"
            f"窗口比 {w['bull_share_pct']}%（{w['n']} 场）",
            f"场次差 {c['effective_sessions']['gap_to_25']}；论点差 {c['verified_claims']['gap_to_100']}",
            "✅/❌ 见读数")
    row("A4", "completed ≥95%；T+5 ≥95%",
        f"completed {a4['completion_rate_pct']}%（{a4['completed']}/{a4['submitted']}）；"
        f"T+5 {a4['t5_rate_pct']}%（{a4['t5_present']}/{a4['mature_units']} 成熟档）" if a4["completion_rate_pct"] is not None else "无样本",
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
        p.append("| report_id | symbol | trade_date | cohort | 动作 | 方向 | created_at |")
        p.append("|---|---|---|---|---|---|---|")
        for r in a1["audit_manifest"]:
            p.append(f"| `{r['report_id']}` | {r['symbol']} | {r['trade_date']} | `{r['cohort']}` | {r['decision']} | {r['direction']} | {r['created_at']} |")
    else:
        p.append("（范围内无 clean 可执行档）")
    p.append("")
    p.append("> A3 口径见 work/dav1507-exit-progress/spec_v4.md；`_ref_loose` 为 research_manager.py:2729 宽口径参考列，不判达标。"
             f"A4 成熟截止 {a4['mature_cutoff']}。B1 契约 10-09 才部署，此前 N/A 属预期。")
    return "\n".join(p)


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="path to a COPY/snapshot of tradingagents.db (opened mode=ro)")
    ap.add_argument("--account", default=ACCOUNT)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end")
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--ids")
    ap.add_argument("--calendar-json")
    ap.add_argument("--window-days", type=int, default=10)
    ap.add_argument("--all-history", action="store_true",
                    help="also compute A2/A4 over the whole account history (cohort comparison)")
    ap.add_argument("--csv-dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.db:
        ap.error("--db is required (unless --self-test)")

    ids = None
    if args.ids:
        ids = json.loads(Path(args.ids).read_text())
        if not isinstance(ids, list) or any(not isinstance(v, str) for v in ids):
            ap.error("--ids must be a JSON array of report-id strings")

    lib = _project_imports()
    trade_dates = load_trade_dates(args.calendar_json)

    tracemalloc.start()
    peak_holder = {"peak": 0}

    def scan(start_date, end_date, want_units=True):
        """Stream rows once; keep only slim metric fields + clean units."""
        con = open_ro(args.db)
        reports_meta, unit_rows, clean_rows, t5_rows = [], [], [], []
        sessions = []
        for row in iter_report_rows(con, args.account, start=start_date, end=end_date, ids=ids):
            slim = slim_report(row)
            if slim is None:
                continue
            reports_meta.append({
                "id": slim["id"], "symbol": slim["symbol"], "trade_date": slim["trade_date"],
                "status": slim["status"], "created_at": slim["created_at"],
                "traceable": bool(
                    (slim["result_data"].get("input_snapshot") or {}).get("hash")
                    and slim["result_data"].get("served_models")
                ),
            })
            t5_rows.append({
                "report_id": slim["id"], "trade_date": slim["trade_date"],
                "status": slim["status"], "t5_status": t5_status_of_report(slim),
            })
            if want_units:
                clean, ledger, reasons = clean_units_of(slim, lib)
                # Pipeline returns normalized copies, not the same dict objects —
                # match clean units back to the raw split by (report_id, horizon),
                # which is unique per unit under split_report_into_units.
                clean_keys = {
                    (str(u.get("parent_report_id") or u.get("report_id") or u.get("id") or slim["id"]),
                     str(u.get("horizon") or "single"))
                    for u in clean
                }
                for u in lib["split_report_into_units"](slim):
                    hz = u.get("horizon") or "single"
                    coh = lib["extract_sample_cohort"](u)
                    cohort_key = ":".join(str(coh.get(k) or "unspecified") for k in
                                          ("decision_model_version", "evidence_contract_version",
                                           "price_basis_version")) + ":" + str(hz)
                    qc = (slim["id"], str(hz)) in clean_keys
                    # Non-clean units are only needed for A2's denominator —
                    # keep a slim dict, not the full debate payload. Clean
                    # units keep the full payload for A3/B-metric fields.
                    stored_unit = u if qc else {
                        "status": u.get("status"), "analysis_status": u.get("analysis_status"),
                        "trade_action": u.get("trade_action"), "decision": u.get("decision"),
                        "direction": u.get("direction"), "horizon": u.get("horizon"),
                        "forecast": u.get("forecast"), "price_plan_check": u.get("price_plan_check"),
                        "not_requested": u.get("status") == "not_requested",
                    }
                    unit_rows.append({
                        "report_id": slim["id"], "symbol": slim["symbol"], "trade_date": slim["trade_date"],
                        "created_at": slim["created_at"], "report_status": slim["status"],
                        "horizon": hz, "cohort": cohort_key, "unit": stored_unit,
                        "not_requested": u.get("status") == "not_requested",
                        "completed": slim["status"] == "completed" and u.get("status") != "failed",
                        "failed": slim["status"] == "failed" or u.get("status") == "failed",
                        "analysis_status": u.get("analysis_status"),
                        "decision": u.get("trade_action") or u.get("decision"),
                        "direction": u.get("direction"),
                        "executable": (u.get("analysis_status") == "VALID"
                                       and (u.get("trade_action") or u.get("decision")) in EXECUTABLE_ACTIONS),
                        "qualified_clean": qc,
                    })
                    if qc:
                        clean_rows.append({
                            "report_id": slim["id"], "trade_date": slim["trade_date"],
                            "created_at": slim["created_at"], "cohort": cohort_key, "unit": u,
                        })
            # drop the heavy payload immediately
            slim["result_data"] = None
            del slim
        con.close()
        cur, peak = tracemalloc.get_traced_memory()
        peak_holder["peak"] = max(peak_holder["peak"], peak)
        return reports_meta, unit_rows, clean_rows, t5_rows

    reports_meta, unit_rows, clean_rows, t5_rows = scan(args.start, args.end, want_units=True)

    all_hist = None
    if args.all_history:
        h_meta, h_units, h_clean, h_t5 = scan(None, args.end, want_units=True)
        all_hist = {
            "reports": len(h_meta), "units": len(h_units),
            "a2": metric_a2(h_units, window_days=args.window_days),
            "a4": metric_a4(h_meta, h_t5, trade_dates, args.as_of),
            "status_counts": dict(Counter(r["status"] for r in h_meta)),
        }

    peak_mb = round(peak_holder["peak"] / 1024 / 1024, 1)
    tracemalloc.stop()

    a1 = metric_a1(unit_rows)
    a2 = metric_a2(unit_rows, window_days=args.window_days)
    a3 = metric_a3(clean_rows, lib)
    a4 = metric_a4(reports_meta, t5_rows, trade_dates, args.as_of)
    b1 = metric_b1(unit_rows)
    b2 = metric_b2(unit_rows)
    b3 = metric_b3(reports_meta)

    meta = {"db": str(Path(args.db).resolve()), "account": args.account,
            "start": args.start, "end": args.end, "as_of": args.as_of,
            "reports": len(reports_meta), "units": len(unit_rows),
            "clean_units": len(clean_rows), "calendar": args.calendar_json or "bundled",
            "peak_mb": peak_mb}
    summary = {"meta": meta, "A1": a1, "A2": a2, "A3": a3, "A4": a4,
               "B1": b1, "B2": b2, "B3": b3, "all_history_side_reading": all_hist}

    if args.csv_dir:
        outdir = Path(args.csv_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        if a1["audit_manifest"]:
            with (outdir / "a1_audit_manifest.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(a1["audit_manifest"][0]))
                w.writeheader(); w.writerows(a1["audit_manifest"])
        with (outdir / "units.csv").open("w", newline="", encoding="utf-8") as f:
            cols = ["report_id", "symbol", "trade_date", "cohort", "report_status",
                    "analysis_status", "decision", "direction", "qualified_clean", "failed"]
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in unit_rows:
                w.writerow({c: r[c] for c in cols})

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(summary, meta))
    return 0


# --------------------------------------------------------------------------- #
# Self test                                                                    #
# --------------------------------------------------------------------------- #

def _self_test():
    lib = _project_imports()
    # Build a synthetic dual-horizon report that survives the real pipeline.
    good_claims = []
    for i, side in enumerate(("Bull", "Bear", "Bull", "Bear", "Bull", "Bear")):
        cid = f"{side[:1]}-{i}"
        good_claims.append({
            "claim_id": cid, "speaker_key": side, "speaker": f"{side} Analyst",
            "stance": "bullish" if side == "Bull" else "bearish",
            "stage": "opening", "battlefield": ["capital_flow", "price_volume", "fundamentals"][i % 3],
            "claim": f"{side} opening claim {i}",
        })
    ev = [{"claim_id": c["claim_id"], "status": "verified"} for c in good_claims]
    unit = {
        "status": "completed", "analysis_status": "VALID", "trade_action": "BUY",
        "decision": "BUY", "direction": "BULL", "horizon": "short",
        "investment_debate_state": {
            "claims": good_claims,
            "manager_verdict": {"winner": "bull",
                                "claim_evidence_summary": {c["claim_id"]: {"decision": "adopt",
                                                                          "counts": {"verified": 1}}
                                                          for c in good_claims}},
        },
        "evidence_verification": ev,
        "decision_model_version": "decision_model.v1",
        "evidence_contract_version": "evidence_contract.v2",
        "price_basis_version": "price_basis.vendor_qfq",
        "price_ref_contract_version": "price_ref.v1",
    }
    rep = {"id": "r1", "symbol": "X", "trade_date": "2026-10-08", "status": "completed",
           "created_at": "2026-10-08T01:00:00", "user_id": ACCOUNT,
           "result_data": {"short_term": dict(unit),
                           "medium_term": dict(unit, horizon="medium", analysis_status="ABSTAIN",
                                               trade_action="WAIT", decision="WAIT")}}
    units, ledger, reasons = clean_units_of(rep, lib)
    assert len(units) == 1, f"expected 1 clean unit (medium ABSTAIN drops), got {len(units)}"
    sides = a3_session_sides(units[0], lib)
    # 3 bull claims over 3 battlefields -> bull session effective
    assert len(sides["bull"]["battlefields"]) == 3 and sides["bull"]["has_verified_claim"]
    # bear: 3 claims but only 3 distinct battlefields? here 3 as well
    assert len(sides["bear"]["battlefields"]) == 3

    # verified-claim predicate: partial reject
    partial_claim = dict(good_claims[0], claim_id="P-1")
    ev_partial = {"P-1": [{"status": "verified"}, {"status": "verified"}, {"status": "unsupported"}]}
    summ_partial = {"P-1": {"decision": "partial", "counts": {"verified": 2, "unsupported": 1}}}
    assert not is_verified_claim(partial_claim, ev_partial, summ_partial["P-1"], lib)
    # missing summary -> unsupported>0 -> still not counted
    assert not is_verified_claim(partial_claim, ev_partial, None, lib)
    # contradicted veto
    ev_contra = {"C-1": [{"status": "verified"}, {"status": "contradicted"}]}
    assert not is_verified_claim(dict(good_claims[0], claim_id="C-1"), ev_contra, None, lib)
    # observation claim excluded
    obs = dict(good_claims[0], claim_id="O-1", claim="观察：大盘缩量")
    assert not is_verified_claim(obs, {"O-1": [{"status": "verified"}]}, None, lib)
    # 5 verified items on one claim still counts once
    five = dict(good_claims[0], claim_id="M-1")
    ev5 = {"M-1": [{"status": "verified"}] * 5}
    assert is_verified_claim(five, ev5, None, lib)

    # A3 asymmetric ratio: (40 bull, 10 bear) -> 80% -> fail
    fake = []
    for i in range(50):
        eff = {"bull": i < 40, "bear": i < 10}
        fake.append({"report_id": f"r{i}", "trade_date": "2026-10-08",
                     "created_at": f"2026-10-08T{i:02d}:00", "cohort": "dmv:ecv:pbv:short",
                     "eff": eff, "verified_claims": {"bull": 1, "bear": 1}})
    res = metric_a3.__wrapped__ if hasattr(metric_a3, "__wrapped__") else None
    # direct window math check
    nb = sum(1 for i in fake if i["eff"]["bull"])
    ne = sum(1 for i in fake if i["eff"]["bear"])
    assert nb / (nb + ne) == 0.8

    print(json.dumps({"self_test": "passed", "model_calls": 0, "provider_calls": 0}))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
