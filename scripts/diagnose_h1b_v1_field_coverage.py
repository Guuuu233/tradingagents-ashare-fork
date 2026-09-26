#!/usr/bin/env python3
"""DAV-1310 (D-046): H1b 样本资格与生产报告格式脱节诊断脚本（只读）。

诊断目标：对 v1_legacy 生产报告逐项核查 H1b 管线
（filter_v2_completed_reports / verify_h1b_gates 七项门槛 / 信用加权）
所读取的每个字段是否存在等价来源，并量化 Stage 2/3/3.5/4 各段
在 v1 样本上的真实吞吐。

只读约束：
- SQLite 一律以 ``file:...?mode=ro`` URI 打开，绝不写库、不做 schema 迁移；
- 报告只输出字段名 / 路径 / 计数 / 布尔命中，不落任何报告正文或供应商原始数据（D-040）；
- 不修改任何产品代码与配置，不调用真实模型 / 供应商。

用法：

    python scripts/diagnose_h1b_v1_field_coverage.py \
        --db /path/to/tradingagents.db \
        [--since 2026-09-19] \
        [--id-manifest work/hist-bench-20260926/frozen-20260926.json] \
        [--output-json work/dav1310-v1-field-coverage.json]
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional

# Ensure project root in sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tradingagents.agents.utils.shadow_credit import (  # noqa: E402
    calculate_shadow_credit_metrics,
    classify_v2_report_d009_exclusion,
    collect_hold_semantic_reasons,
    extract_report_analysis_status_and_action,
    extract_report_industry,
    extract_sample_cohort,
    filter_v2_completed_reports,
    is_qualifying_v2_report,
)
from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
    is_daily_ohlcv_unavailable,
)
from tradingagents.agents.utils.price_basis_isolation import (  # noqa: E402
    classify_price_basis_exclusion,
    extract_report_id,
)

HORIZON_KEYS = ("short_term", "medium_term")


# ── DB loading (strictly read-only, no ORM / no schema migration) ─────────────

def load_reports_ro(db_path: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load completed reports from SQLite via mode=ro URI (no writes, no migrations)."""
    abs_path = os.path.abspath(db_path)
    uri = f"file:{abs_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM reports WHERE status='completed'"
        params: tuple = ()
        if since:
            sql += " AND created_at >= ?"
            params = (since,)
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    reports: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        rd = d.get("result_data")
        if isinstance(rd, str):
            try:
                d["result_data"] = json.loads(rd)
            except Exception:
                d["result_data"] = None
        reports.append(d)
    return reports


def load_id_manifest(path: str) -> List[str]:
    """Load frozen report_id list (JSON list of objects with report_id, or list of str)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ids: List[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, Mapping) and item.get("report_id"):
                ids.append(str(item["report_id"]))
    elif isinstance(data, Mapping):
        for key in ("report_ids", "samples", "frozen"):
            if isinstance(data.get(key), list):
                for item in data[key]:
                    if isinstance(item, str):
                        ids.append(item)
                    elif isinstance(item, Mapping) and item.get("report_id"):
                        ids.append(str(item["report_id"]))
    return ids


# ── Horizon unpacking ─────────────────────────────────────────────────────────

def iter_units(report: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Yield evaluation units: the top-level report, plus each horizon sub-report.

    Dual-horizon packaged v1 reports nest the debate payload under
    ``result_data.short_term`` / ``result_data.medium_term``. Each unit inherits
    row-level columns (id/symbol/trade_date/industry/created_at) so downstream
    field probes see the same surface the pipeline would see on a single-horizon
    report.
    """
    rd = report.get("result_data")
    if not isinstance(rd, Mapping):
        rd = {}

    inherited = {
        "id": report.get("id"),
        "report_id": report.get("report_id") or report.get("id"),
        "symbol": report.get("symbol") or rd.get("symbol"),
        "trade_date": report.get("trade_date") or rd.get("trade_date"),
        "industry": report.get("industry"),
        "status": report.get("status", "completed"),
        "created_at": report.get("created_at").isoformat()
        if hasattr(report.get("created_at"), "isoformat")
        else report.get("created_at"),
        "analysis_status": report.get("analysis_status"),
        "trade_action": report.get("trade_action"),
        "decision": report.get("decision"),
        "direction": report.get("direction"),
    }

    units: List[Dict[str, Any]] = []
    top_unit = {**rd, **{k: v for k, v in inherited.items() if v is not None}}
    top_unit["_horizon_label"] = "top"
    units.append(top_unit)

    for h in HORIZON_KEYS:
        sub = rd.get(h)
        if isinstance(sub, Mapping):
            unit = {**sub, **{k: v for k, v in inherited.items() if v is not None}}
            unit["_horizon_label"] = h
            # keep a reference to parent result_data for cohort/version fields
            # the sub-report may not re-stamp
            for k in (
                "decision_model_version",
                "evidence_contract_version",
                "price_basis_version",
                "price_ref_contract_version",
                "generated_by_commit_sha",
            ):
                if unit.get(k) is None and rd.get(k) is not None:
                    unit[k] = rd[k]
            units.append(unit)
    return units


# ── Field probes ──────────────────────────────────────────────────────────────

def _get_in(obj: Mapping[str, Any], *path: str) -> Any:
    cur: Any = obj
    for p in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(p)
    return cur


def _first(obj: Mapping[str, Any], paths: List[List[str]]) -> Any:
    for p in paths:
        v = _get_in(obj, *p)
        if v is not None and (not isinstance(v, str) or v.strip()):
            return v
    return None


def _maps(unit: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    """Resolve the canonical nested maps the pipeline consults."""
    rd = unit.get("result_data") if isinstance(unit.get("result_data"), Mapping) else {}
    inv = unit.get("investment_debate_state")
    if not isinstance(inv, Mapping):
        inv = rd.get("investment_debate_state") if isinstance(rd.get("investment_debate_state"), Mapping) else {}
    mv = (
        unit.get("manager_verdict")
        or rd.get("manager_verdict")
        or inv.get("manager_verdict")
        or {}
    )
    if not isinstance(mv, Mapping):
        mv = {}
    return {"res_data": rd, "inv": inv, "mv": mv}


def probe_h1b_fields(unit: Mapping[str, Any]) -> Dict[str, Any]:
    """Probe every field the H1b pipeline reads; return presence hit map.

    Key: logical field name. Value: the v1 path where it was found, or None.
    """
    m = _maps(unit)
    rd, inv, mv = m["res_data"], m["inv"], m["mv"]

    out: Dict[str, Any] = {}

    def mark(name: str, val: Any, path: str) -> None:
        out[name] = path if val is not None else None

    # Stage 2 — qualification
    mark("status", unit.get("status"), "status")
    pv = _first(unit, [["protocol_version"], ["investment_debate_state", "protocol_version"]])
    mark("protocol_version", pv, "protocol_version|investment_debate_state.protocol_version")
    winner = _first(unit, [["manager_verdict", "winner"], ["investment_debate_state", "manager_verdict", "winner"], ["debate_winner"]])
    mark("manager_verdict.winner", winner, "manager_verdict.winner|investment_debate_state.manager_verdict.winner|debate_winner")
    ces = _first(unit, [["manager_verdict", "claim_evidence_summary"], ["investment_debate_state", "manager_verdict", "claim_evidence_summary"], ["investment_debate_state", "claim_evidence_summary"]])
    mark("manager_verdict.claim_evidence_summary", ces, "manager_verdict.claim_evidence_summary")
    ccp = _first(unit, [["manager_verdict", "consistency_check_passed"], ["investment_debate_state", "manager_verdict", "consistency_check_passed"]])
    mark("manager_verdict.consistency_check_passed", ccp, "manager_verdict.consistency_check_passed")
    claims = _first(unit, [["investment_debate_state", "claims"], ["claims"], ["result_data", "claims"]])
    mark("claims", claims, "investment_debate_state.claims|claims")

    # Stage 3 — D-009 §5
    st_val, act_val = extract_report_analysis_status_and_action(unit)
    mark("analysis_status", st_val, "analysis_status|result_data.analysis_status|decision_status.analysis_status")
    mark("trade_action", act_val, "trade_action|result_data.trade_action|decision_status.trade_action")

    # Stage 3.5 — HOLD semantic isolation
    mark("manager_verdict.ohlcv_gate_applied", mv.get("ohlcv_gate_applied") if "ohlcv_gate_applied" in mv else None, "manager_verdict.ohlcv_gate_applied")
    mark("manager_verdict.fund_flow_dispute_gate_applied", mv.get("fund_flow_dispute_gate_applied") if "fund_flow_dispute_gate_applied" in mv else None, "manager_verdict.fund_flow_dispute_gate_applied")
    mdc = _first(unit, [["market_data_context"], ["result_data", "market_data_context"], ["investment_debate_state", "market_data_context"]])
    mark("market_data_context", mdc, "market_data_context|result_data.market_data_context")
    if isinstance(mdc, Mapping):
        out["market_data_context.ohlcv_unavailable"] = "market_data_context.source_provenance.stock_data" if is_daily_ohlcv_unavailable(mdc) else None

    # Stage 4 — price-basis isolation (contract-era fields + report id)
    mark("report_id", extract_report_id(unit), "id|report_id")
    cohort = extract_sample_cohort(unit)
    for k in ("decision_model_version", "evidence_contract_version", "price_basis_version", "generated_by_commit_sha"):
        mark(k, cohort.get(k), f"{k} (top|result_data|inv_state|metadata)")
    prcv = _first(unit, [["price_ref_contract_version"], ["result_data", "price_ref_contract_version"], ["metadata", "price_ref_contract_version"]])
    mark("price_ref_contract_version", prcv, "price_ref_contract_version|result_data.price_ref_contract_version|metadata")

    # Gate D1 — N
    mark("symbol", unit.get("symbol") or unit.get("ticker"), "symbol|ticker")
    mark("industry", extract_report_industry(unit), "industry|sector|instrument_context|market_data_context.industry_linkage|data_collection_provenance|quadrant_1|short_term|medium_term")

    # Gate D2 — side
    direction = _first(unit, [["manager_verdict", "direction"], ["direction"]])
    mark("manager_verdict.direction", direction, "manager_verdict.direction|direction")

    # Gate D3 — time
    mark("trade_date", _first(unit, [["trade_date"], ["date"], ["created_at"]]), "trade_date|date|created_at")
    mark("market_regime", _first(unit, [["market_regime"], ["regime"]]), "market_regime|regime")

    # Gate D4 — T+5 completeness
    mark("t_plus_5_status", _first(unit, [["t_plus_5_status"], ["result_data", "t_plus_5_status"], ["shadow_credit_metrics", "t_plus_5_status"], ["investment_debate_state", "t_plus_5_status"]]), "t_plus_5_status|shadow_credit_metrics.t_plus_5_status")
    mark("t_plus_5_date", _first(unit, [["t_plus_5_date"], ["result_data", "t_plus_5_date"], ["shadow_credit_metrics", "t_plus_5_date"], ["investment_debate_state", "t_plus_5_date"]]), "t_plus_5_date|shadow_credit_metrics.t_plus_5_date")
    mark("is_t_plus_5_due", _first(unit, [["is_t_plus_5_due"], ["result_data", "is_t_plus_5_due"]]), "is_t_plus_5_due")
    mark("is_suspended", _first(unit, [["is_suspended"], ["suspension"], ["result_data", "is_suspended"]]), "is_suspended|suspension")
    mark("is_in_flight", _first(unit, [["is_in_flight"], ["result_data", "is_in_flight"]]), "is_in_flight")
    mark("t_plus_5_price", _first(unit, [["t_plus_5_price"], ["investment_debate_state", "t_plus_5_price"], ["shadow_credit_metrics", "t_plus_5_price"]]), "t_plus_5_price")
    mark("t_plus_5_direction_hit", _first(unit, [["t_plus_5_direction_hit"], ["shadow_credit_metrics", "t_plus_5_direction_hit"]]), "t_plus_5_direction_hit|shadow_credit_metrics.t_plus_5_direction_hit")
    mark("t_plus_1_open", _first(unit, [["t_plus_1_open"], ["investment_debate_state", "t_plus_1_open"]]), "t_plus_1_open")
    mark("entry_price_source", unit.get("entry_price_source"), "entry_price_source")
    mark("entry_price_or_target", _first(unit, [["manager_verdict", "entry"], ["entry_price"], ["target_price"], ["investment_debate_state", "manager_verdict", "entry"]]), "manager_verdict.entry|entry_price|target_price")

    # Gate D6 — bias freeze inputs
    scm = unit.get("shadow_credit_metrics")
    if not isinstance(scm, Mapping):
        scm = rd.get("shadow_credit_metrics") if isinstance(rd.get("shadow_credit_metrics"), Mapping) else None
    mark("shadow_credit_metrics", scm, "shadow_credit_metrics|result_data.shadow_credit_metrics")
    if isinstance(scm, Mapping):
        for k in ("bull_verified_rate", "bear_verified_rate", "bull_challenge_adoption_rate", "bear_challenge_adoption_rate", "manager_consistency_gate_triggered"):
            mark(f"shadow_credit_metrics.{k}", scm.get(k), f"shadow_credit_metrics.{k}")
    ch = _first(unit, [["investment_debate_state", "challenges"], ["challenges"]])
    mark("challenges", ch, "investment_debate_state.challenges|challenges")
    adopted = mv.get("adopted_challenge_ids")
    mark("manager_verdict.adopted_challenge_ids", adopted, "manager_verdict.adopted_challenge_ids")
    rm = _first(unit, [["investment_debate_state", "round_messages"], ["round_messages"]])
    mark("round_messages", rm, "investment_debate_state.round_messages|round_messages")
    mid = _first(unit, [["model_id_by_stance"], ["investment_debate_state", "model_id_by_stance"], ["role_models"]])
    mark("model_id_by_stance", mid, "model_id_by_stance|investment_debate_state.model_id_by_stance|role_models")

    # Seven analyst report texts (analyst utilization denominators)
    for rk in ("macro_report", "market_report", "sentiment_report", "news_report", "fundamentals_report", "smart_money_report", "volume_price_report"):
        v = _first(unit, [[rk], ["result_data", rk], ["investment_debate_state", rk]])
        mark(rk, v, rk)

    return out


# ── Pipeline measurement on unpacked units ────────────────────────────────────

def measure_units(units: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run each pipeline stage against unpacked units; return ledger counts."""
    ledger = Counter()
    field_hits: Dict[str, Counter] = {}
    cohort_keys = Counter()
    horizon_dist = Counter()

    for u in units:
        horizon_dist[u.get("_horizon_label", "?")] += 1
        hits = probe_h1b_fields(u)
        for name, path in hits.items():
            if name not in field_hits:
                field_hits[name] = Counter()
            field_hits[name][path if path else "<missing>"] += 1
        ck = extract_sample_cohort(u)
        cohort_keys[f"{ck.get('decision_model_version')}:{ck.get('evidence_contract_version')}:{ck.get('price_basis_version')}"] += 1

        if not is_qualifying_v2_report(u):
            ledger["non_v2_excluded"] += 1
            continue
        ledger["qualifying_v2_count"] += 1
        cat = classify_v2_report_d009_exclusion(u)
        if cat:
            ledger["d009_excluded"] += 1
            ledger[f"d009.{cat}"] += 1
            continue
        ledger["eligible_count"] += 1
        hold = collect_hold_semantic_reasons(u)
        pb = classify_price_basis_exclusion(u)
        for h in hold:
            ledger[f"hold.{h}"] += 1
        if hold:
            ledger["hold_semantic_isolated"] += 1
        if pb:
            ledger[f"price_basis.{pb}"] += 1
            ledger["price_basis_isolated"] += 1
        if not hold and not pb:
            ledger["clean_count"] += 1

    return {
        "unit_count": len(units),
        "horizon_distribution": dict(horizon_dist),
        "cohort_key_distribution": dict(cohort_keys),
        "pipeline_ledger_on_units": dict(ledger),
        "field_presence": {
            name: dict(paths) for name, paths in sorted(field_hits.items())
        },
    }


def measure_reports_as_is(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run the production filter path verbatim on raw to_dict-shaped reports."""
    shaped = []
    for r in reports:
        d = dict(r)
        d.setdefault("report_id", d.get("id"))
        shaped.append(d)
    q, exc, led = filter_v2_completed_reports(shaped, return_ledger=True)
    return {
        "raw_count": led.get("raw_count"),
        "as_is_pipeline_ledger": led,
        "d009_excluded_counts": exc,
        "clean_report_ids_count": len(q),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="DAV-1310 H1b v1 field-coverage diagnostic (read-only)")
    ap.add_argument("--db", required=True, help="SQLite DB path (opened via mode=ro URI)")
    ap.add_argument("--since", default=None, help="Only reports with created_at >= this date (YYYY-MM-DD)")
    ap.add_argument("--id-manifest", default=None, help="Frozen manifest JSON listing report_ids to restrict to")
    ap.add_argument("--output-json", default=None, help="Write structured result JSON to this path")
    args = ap.parse_args()

    reports = load_reports_ro(args.db, since=args.since)
    manifest_ids: Optional[set] = None
    if args.id_manifest:
        manifest_ids = set(load_id_manifest(args.id_manifest))
        reports = [r for r in reports if r.get("id") in manifest_ids]

    as_is = measure_reports_as_is(reports)

    units: List[Dict[str, Any]] = []
    for r in reports:
        units.extend(iter_units(r))

    unpacked = measure_units(units)

    result = {
        "task_id": "DAV-1310",
        "db_path": os.path.basename(args.db),
        "since": args.since,
        "id_manifest": os.path.basename(args.id_manifest) if args.id_manifest else None,
        "reports_loaded": len(reports),
        "as_is": as_is,
        "horizon_unpacked": unpacked,
    }

    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.output_json:
        out_dir = os.path.dirname(args.output_json)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n[产物] 已写入 {args.output_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
