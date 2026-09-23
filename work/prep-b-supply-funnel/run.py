#!/usr/bin/env python3
"""DAV-1227 / D-037 准备线 B：样本供给漏斗与 clean 可达性只读诊断。

只读诊断，不施工：
  1. 对真实账户的 completed 报告做漏斗 raw → v2 → D-009 → Stage 3.5 → Stage 4 → clean，
     同时给出 production view（3d9c414 口径：无 Stage 3.5）与 trunk view
     （9d03c89 口径：含 DAV-1139 Stage 3.5 HOLD 语义隔离），按周/入口/cohort 分解。
  2. ABSTAIN / WAIT / NO_TRADE / INVALID_RUN / DATA_ERROR 的 reason_codes Top-N，
     归类：数据缺口类 / 守卫门禁类（资金流 guard、manager consistency hard gate、
     E-04、price_basis_gate）/ 证据核验类 / 其他。
  3. E-04 型 ABSTAIN 按 DAV-1112 四类（裸断言/条件情景/降权启发式/有依据定价）
     在 trunk 9d03c89 代码上做 occurrence 级复放分类。
  4. 每 100 份 completed 分析预计 clean 产出与单 cohort 60 的差距。

用法（在仓库根目录）：
    env -u PYTHONPATH /path/to/.venv310/bin/python work/prep-b-supply-funnel/run.py \
        --db work/prep-b-supply-funnel/snapshot.db

  --db 指向生产库的 .backup() 副本（脚本只用 mode=ro&immutable=1 打开，绝不写）。
  也可用 --db "file:/path/to/tradingagents.db?mode=ro" 读活库快照。

输出：stdout 摘要 + --out 目录下 report.md / report.json。
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tradingagents.agents.utils.shadow_credit import (  # noqa: E402
    classify_v2_report_d009_exclusion,
    collect_hold_semantic_reasons,
    extract_report_analysis_status_and_action,
    extract_sample_cohort,
    is_legacy_unversioned_sample,
    is_v2_protocol_report,
)
from tradingagents.agents.utils.price_basis_isolation import (  # noqa: E402
    classify_price_basis_exclusion,
)
import tradingagents.agents.managers.research_manager as rm  # noqa: E402

EVENT_TYPE_FUNDAMENTAL = rm.EVENT_TYPE_FUNDAMENTAL
EVENT_TYPE_EVENT = rm.EVENT_TYPE_EVENT
EVENT_TYPE_NONE = rm.EVENT_TYPE_NONE
PRICED_IN_SUPPORTED = rm.PRICED_IN_SUPPORTED

DEFAULT_USER_ID = "429163f7-50b6-4982-8bdf-96ae99506843"

JSON_COLS = (
    "result_data", "risk_items", "key_metrics", "analyst_traces",
    "data_gaps", "falsification_conditions",
)
SCALAR_COLS = (
    "id", "user_id", "symbol", "trade_date", "industry", "decision", "direction",
    "confidence", "probability", "target_price", "stop_loss_price",
    "analysis_status", "trade_action", "risk_status", "not_applicable",
    "market_report", "sentiment_report", "news_report", "fundamentals_report",
    "macro_report", "smart_money_report", "volume_price_report",
    "game_theory_report", "investment_plan", "trader_investment_plan",
    "final_trade_decision", "created_at", "updated_at", "status",
)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_reports(db: str, user_id: str) -> list[dict[str, Any]]:
    """Read-only load of completed reports for one user, mimicking ReportDB.to_dict()."""
    if db.startswith("file:"):
        uri = db if "mode=ro" in db else db + ("&" if "?" in db else "?") + "mode=ro"
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    try:
        cols = ", ".join(SCALAR_COLS + JSON_COLS)
        rows = con.execute(
            f"SELECT {cols} FROM reports WHERE user_id=? AND status='completed'",
            (user_id,),
        ).fetchall()
    finally:
        con.close()
    reports = []
    for row in rows:
        rec = dict(zip(SCALAR_COLS + JSON_COLS, row))
        for c in JSON_COLS:
            v = rec.get(c)
            if isinstance(v, str) and v:
                try:
                    rec[c] = json.loads(v)
                except json.JSONDecodeError:
                    rec[c] = None
            elif v is None and c in ("data_gaps", "falsification_conditions"):
                rec[c] = []
        reports.append(rec)
    return reports


# ── 维度分解 ─────────────────────────────────────────────────────────────────

def _res_data(r: Mapping[str, Any]) -> Mapping[str, Any]:
    rd = r.get("result_data")
    return rd if isinstance(rd, Mapping) else {}


def report_week(r: Mapping[str, Any]) -> str:
    raw = r.get("created_at") or ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def report_entry(r: Mapping[str, Any]) -> str:
    rd = _res_data(r)
    wc = rd.get("workflow_context")
    if isinstance(wc, Mapping) and wc.get("request_source"):
        return str(wc["request_source"])
    return "unknown(no workflow_context)"


def report_cohort(r: Mapping[str, Any]) -> str:
    if is_legacy_unversioned_sample(r):
        return "legacy_unversioned"
    co = extract_sample_cohort(r)
    return ":".join(co.get(k) or "∅" for k in (
        "decision_model_version", "evidence_contract_version", "price_basis_version"))


def get_reason_codes(r: Mapping[str, Any]) -> list[str]:
    rd = _res_data(r)
    ds = r.get("decision_status") if isinstance(r.get("decision_status"), Mapping) else (
        rd.get("decision_status") if isinstance(rd.get("decision_status"), Mapping) else {})
    codes = ds.get("reason_codes") or rd.get("reason_codes") or r.get("reason_codes") or []
    return [str(c) for c in codes if c]


def get_failed_checks(r: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    rd = _res_data(r)
    for inv in (rd.get("investment_debate_state"), r.get("investment_debate_state")):
        if isinstance(inv, Mapping):
            mv = inv.get("manager_verdict")
            if isinstance(mv, Mapping):
                out += [str(x) for x in (mv.get("failed_checks") or []) if x]
    mv = rd.get("manager_verdict") or r.get("manager_verdict")
    if isinstance(mv, Mapping):
        out += [str(x) for x in (mv.get("failed_checks") or []) if x]
    ds = rd.get("decision_status")
    if isinstance(ds, Mapping):
        out += [str(x) for x in (ds.get("failed_checks") or []) if x]
    return out


# ── reason code 归类 ─────────────────────────────────────────────────────────

def classify_reason_code(code: str) -> str:
    c = code
    if c.startswith("E-04") or "E-04 守卫拦截" in c:
        return "guard:e04_priced_in_beat_miss"
    if c.startswith("fund_flow_guard") or c == "fund_flow_consensus_guard":
        return "guard:fund_flow"
    if c == "manager_consistency_hard_gate" or c.startswith("manager_consistency"):
        return "guard:manager_consistency_hard_gate"
    if "price_basis" in c:
        return "guard:price_basis_gate"
    if c.startswith(("unadjudicated_material_claims", "verdict_consistency",
                     "all_core_claims_verified", "unverified_core_claims",
                     "fatal_core_claims", "fatal_contradicted_claims",
                     "partial_core_claims", "audited_rejected_claims",
                     "partially_adopted_claims", "challenge_", "claim_")):
        return "evidence_verification"
    if c.startswith(("analyst_upstream", "data_unavailable", "data_gap",
                     "missing_", "no_data")) or "分析报告生成失败" in c \
            or c.endswith(":prefix") or ":prefix:" in c:
        return "data_gap"
    if any(ord(ch) > 0x2FFF for ch in c):  # legacy 中文叙述句混入 reason_codes（D-1093 前）
        return "legacy_narrative(verifier)"
    return "other"


GUARD_FIXES = {
    "guard:e04_priced_in_beat_miss": (
        "09-19 后已修：DAV-1110 条件作用域、DAV-1135(35c33de) 名词性存在否定豁免"
        "（仅 trunk）；DAV-1093 起拦截细节从 reason_codes 归位 failed_checks"),
    "guard:fund_flow": "09-19 后已修：DAV-1138(9d03c89) moneyflow failure taxonomy（仅 trunk）",
    "guard:price_basis_gate": "DAV-1199/1200/1207/1211 已在 3d9c414 上线（两视图同口径）",
}


# ── E-04 四类复放（trunk 9d03c89 occurrence 级） ──────────────────────────────

_PI_PATTERNS_ZH = (
    "已充分定价", "已完全定价", "市场已定价", "已基本定价",
    "股价已完全反映", "股价已充分反应", "市场已完全反映",
    "完全定价", "充分定价", "已被市场消化", "已被充分消化", "已被完全消化",
)
_BM_KWS_ZH = (
    "超预期", "超出预期", "超越预期", "好于预期", "优于预期", "高于预期",
    "不及预期", "低于预期", "未达预期", "差于预期", "弱于预期", "逊于预期", "落后于预期",
)
_EN_BEAT_RE = re.compile(
    r"\b(?:beat|beats|beating|exceed|exceeded|exceeds|exceeding|surpass|surpassed|surpasses|surpassing|above|better than|higher than|ahead of)\s+(?:(?:all\s+)?(?:market|analyst|street|wall\s+street|consensus|earnings)\s+)?(?:expectations?|consensus|estimates?|forecasts?|expected)\b|\b(?:earnings|profit|revenue)\s+beat\b",
    re.IGNORECASE,
)
_EN_MISS_RE = re.compile(
    r"\b(?:fell short|falls short|fall short)(?:\s+of\b(?:\s+(?:(?:all\s+)?(?:market|analyst|street|wall\s+street|consensus|earnings)\s+)?(?:expectations?|consensus|estimates?|forecasts?|expected))?)?\b|"
    r"\b(?:missed?|misses|missing|below|worse than|lower than|lagged|behind)\s+(?:(?:all\s+)?(?:market|analyst|street|wall\s+street|consensus|earnings)\s+)?(?:expectations?|consensus|estimates?|forecasts?|expected)\b|"
    r"\b(?:earnings|profit|revenue)\s+miss\b",
    re.IGNORECASE,
)


def _pi_occurrences(text: str) -> list[tuple[int, int]]:
    occ = []
    for pat in _PI_PATTERNS_ZH:
        idx = text.find(pat)
        while idx >= 0:
            occ.append((idx, idx + len(pat)))
            idx = text.find(pat, idx + 1)
    for m in re.finditer(r"已定价|已在股价中反映|已反映在股价中", text):
        occ.append((m.start(), m.end()))
    for m in re.finditer(
        r"(?:already\s+|fully\s+|largely\s+|mostly\s+|market\s+has\s+)?priced\s*[- ]?in\b",
        text, re.IGNORECASE):
        occ.append((m.start(), m.end()))
    for m in re.finditer(r"\b(?:fully|largely)\s+discounted\b", text, re.IGNORECASE):
        occ.append((m.start(), m.end()))
    for m in re.finditer(
        r"\b(?:fully|largely)\s+reflected\s+in\s+(?:the\s+)?(?:stock\s+)?price\b",
        text, re.IGNORECASE):
        occ.append((m.start(), m.end()))
    return occ


def _bm_occurrences(text: str) -> list[tuple[int, int, str]]:
    occ = []
    for kw in _BM_KWS_ZH:
        idx = text.find(kw)
        while idx >= 0:
            occ.append((idx, idx + len(kw), kw))
            idx = text.find(kw, idx + 1)
    occ.sort(key=lambda o: (o[0], -(o[1] - o[0])))
    dedup = []
    for o in occ:
        if dedup and o[0] < dedup[-1][1]:
            continue
        dedup.append(o)
    for m in _EN_BEAT_RE.finditer(text):
        dedup.append((m.start(), m.end(), m.group(0)))
    for m in _EN_MISS_RE.finditer(text):
        dedup.append((m.start(), m.end(), m.group(0)))
    dedup.sort(key=lambda o: o[0])
    return dedup


def _norm_er(exp: Any) -> Mapping[str, Any]:
    """Same normalization as validate_manager_expectation_revision_consumption."""
    if isinstance(exp, list):
        fund_er = news_er = None
        for item in exp:
            if isinstance(item, Mapping):
                if item.get("event_type") == EVENT_TYPE_FUNDAMENTAL:
                    fund_er = item
                elif item.get("event_type") in (EVENT_TYPE_EVENT, EVENT_TYPE_NONE):
                    news_er = item
        return {"fundamentals": fund_er or {}, "news": news_er or {}}
    if isinstance(exp, Mapping):
        return exp
    return {}


def _classify_pi_hit(text: str, s: int, e: int, claim_index) -> str:
    """One priced-in occurrence under trunk code → trunk outcome class."""
    if rm._is_claim_quotation(text, s, e, claim_index, rm._PI_QUOTE_KW):
        return "exempt:quotation"
    sl = s
    while sl > 0 and text[sl - 1] not in rm._PRICED_IN_SENTENCE_BREAKS:
        sl -= 1
    sr = e
    while sr < len(text) and text[sr] not in rm._PRICED_IN_SENTENCE_BREAKS:
        sr += 1
    sent = text[sl:sr]
    if any(m.start() <= s - sl < m.end() for m in rm._PI_ANNOTATION.finditer(sent)):
        return "exempt:unknown_annotation"   # 降权启发式（明示状态未知）
    if not rm._is_priced_in_assertion(text, s, e):
        if rm._in_conditional_clause(text, s, e):
            return "exempt:conditional"       # 条件情景
        return "exempt:negated_or_rejected"
    sentence = rm._sentence_span(text, s, e)
    if rm._has_traceable_pricing_basis(sentence) and rm._is_downweighting_pricing(sentence):
        return "exempt:downweight_with_basis"  # 降权启发式（trunk 已放行）
    if rm._has_traceable_pricing_basis(sentence):
        return "blocked:evidenced_pricing"     # 有依据定价（事实断言，仍拦）
    return "blocked:bare_assertion"            # 裸断言（仍拦）


def _classify_bm_hit(text: str, s: int, e: int, claim_index) -> str:
    if rm._is_claim_quotation(text, s, e, claim_index, rm._BM_QUOTE_KW):
        return "exempt:quotation"
    if not rm._is_beat_miss_assertion(text, s, e):
        n = len(text)
        right = e
        while right < n and not rm._is_clause_break(text, right):
            right += 1
        if rm._BEAT_MISS_NOUN_SUFFIX.match(text[e:right]):
            return "exempt:noun_phrase"
        if rm._is_absence_negation_zh(text, s, e):
            return "exempt:absence_negation"   # DAV-1135 名词性存在否定（仅 trunk）
        if rm._in_conditional_clause(text, s, e):
            return "exempt:conditional"
        return "exempt:negated_or_rejected"
    return "blocked:bare_assertion"


def replay_e04_report(r: Mapping[str, Any]) -> dict[str, Any]:
    """Replay E-04 occurrence classification for one report on trunk code."""
    rd = _res_data(r)
    inv = rd.get("investment_debate_state") if isinstance(rd.get("investment_debate_state"), Mapping) else (
        r.get("investment_debate_state") if isinstance(r.get("investment_debate_state"), Mapping) else {})

    hits: list[dict[str, Any]] = []

    def _one_horizon(mv: Mapping[str, Any], inv_st: Mapping[str, Any], tag: str) -> None:
        exp = _norm_er(inv_st.get("expectation_revision") or mv.get("expectation_revision"))
        fund_er = exp.get("fundamentals") or {}
        news_er = exp.get("news") or {}
        fund_pi = (fund_er.get("priced_in") or {}).get("status", "unknown")
        news_pi = (news_er.get("priced_in") or {}).get("status", "unknown")
        fund_base_type = (fund_er.get("baseline") or {}).get("type", "none")
        fund_base_val = (fund_er.get("baseline") or {}).get("value")
        claims = inv_st.get("claims") or rd.get("claims") or []
        claim_index = rm._claim_quote_index(claims)
        # E-04 校验的 full_text = raw_response + verdict.reason + verdict.investment_plan。
        # 经理 raw_response 落库为 judge_decision（=current_response 镜像）。
        texts = []
        for key in ("judge_decision", "current_response"):
            v = inv_st.get(key)
            if isinstance(v, str) and v and v not in texts:
                texts.append(v)
        texts.append(str(mv.get("reason") or ""))
        if mv.get("investment_plan"):
            texts.append(str(mv.get("investment_plan")))
        full_text = "\n".join(t for t in texts if t)
        if not full_text:
            return
        if fund_pi != PRICED_IN_SUPPORTED and news_pi != PRICED_IN_SUPPORTED:
            for s, e in _pi_occurrences(full_text):
                hits.append({"horizon": tag, "kind": "priced_in",
                             "span": full_text[max(0, s - 20):e + 20],
                             "outcome": _classify_pi_hit(full_text, s, e, claim_index)})
        if fund_base_type == "none" or fund_base_val is None:
            for s, e, kw in _bm_occurrences(full_text):
                hits.append({"horizon": tag, "kind": "beat_miss",
                             "span": full_text[max(0, s - 20):e + 20],
                             "outcome": _classify_bm_hit(full_text, s, e, claim_index)})

    seen = False
    for tag in ("short_term", "medium_term"):
        sub = rd.get(tag)
        if isinstance(sub, Mapping):
            sub_inv = sub.get("investment_debate_state") if isinstance(sub.get("investment_debate_state"), Mapping) else {}
            mv = sub.get("manager_verdict") or sub_inv.get("manager_verdict")
            if isinstance(mv, Mapping) and mv:
                seen = True
                _one_horizon(mv, sub_inv, tag)
    if not seen:
        mv = inv.get("manager_verdict") or rd.get("manager_verdict")
        if isinstance(mv, Mapping):
            _one_horizon(mv, inv, "single")

    # 报告级归类：仍被拦的 hit 决定类；全部豁免则按主豁免形态
    blocked = [h["outcome"] for h in hits if h["outcome"].startswith("blocked:")]
    if any(o == "blocked:bare_assertion" for o in blocked):
        klass = "1_bare_assertion(裸断言)"
    elif any(o == "blocked:evidenced_pricing" for o in blocked):
        klass = "4_evidenced_pricing(有依据定价)"
    elif hits:
        exempts = [h["outcome"] for h in hits]
        if any(o in ("exempt:unknown_annotation", "exempt:downweight_with_basis") for o in exempts):
            klass = "3_downweight_heuristic(降权启发式,trunk放行)"
        elif any(o == "exempt:conditional" for o in exempts):
            klass = "2_conditional_scenario(条件情景,trunk放行)"
        else:
            klass = "0_exempt_other(引述/否定/名词短语,trunk放行)"
    else:
        klass = "9_no_hit_reproduced(存储文本未复现命中)"
    return {
        "report_id": r.get("id"), "symbol": r.get("symbol"),
        "trade_date": r.get("trade_date"), "class": klass,
        "hits": hits,
    }


# ── 主流程 ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).parent / "snapshot.db"))
    ap.add_argument("--user-id", default=DEFAULT_USER_ID)
    ap.add_argument("--out", default=str(Path(__file__).parent))
    args = ap.parse_args()

    snap_sha = None
    if not args.db.startswith("file:") and Path(args.db).exists():
        snap_sha = sha256_of(args.db)
    snapshot_time = datetime.now(timezone.utc).isoformat(timespec="seconds")

    reports = load_reports(args.db, args.user_id)

    # ── 漏斗（双视图） ──
    d009_cats = ("abstain", "wait", "no_trade", "invalid_run", "data_error", "legacy_null")
    funnel = {
        "production": collections.Counter(), "trunk": collections.Counter(),
    }
    by_week = collections.defaultdict(lambda: collections.Counter())
    by_entry = collections.defaultdict(lambda: collections.Counter())
    by_cohort_clean = {"production": collections.Counter(), "trunk": collections.Counter()}
    excluded_records: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    clean_ids = {"production": [], "trunk": []}

    for r in reports:
        wk, entry = report_week(r), report_entry(r)
        for view in funnel:
            funnel[view]["raw"] += 1
        by_week[wk]["raw"] += 1
        by_entry[entry]["raw"] += 1
        if not is_v2_protocol_report(r):
            for view in funnel:
                funnel[view]["non_v2"] += 1
            by_week[wk]["non_v2"] += 1
            by_entry[entry]["non_v2"] += 1
            continue
        for view in funnel:
            funnel[view]["v2"] += 1
        by_week[wk]["v2"] += 1
        by_entry[entry]["v2"] += 1
        cat = classify_v2_report_d009_exclusion(r)
        if cat is not None:
            for view in funnel:
                funnel[view][f"d009_{cat}"] += 1
            by_week[wk][f"d009_{cat}"] += 1
            by_entry[entry][f"d009_{cat}"] += 1
            excluded_records[cat].append(r)
            continue
        for view in funnel:
            funnel[view]["d009_eligible"] += 1
        by_week[wk]["d009_eligible"] += 1
        by_entry[entry]["d009_eligible"] += 1

        hold_reasons = collect_hold_semantic_reasons(r)      # Stage 3.5（trunk）
        pb_reason = classify_price_basis_exclusion(r)        # Stage 4（两视图同）

        # production view：无 Stage 3.5
        if pb_reason is None:
            funnel["production"]["clean"] += 1
            clean_ids["production"].append(r.get("id"))
            by_week[wk]["prod_clean"] += 1
            by_entry[entry]["prod_clean"] += 1
            by_cohort_clean["production"][report_cohort(r)] += 1
        else:
            funnel["production"][pb_reason] += 1
        # trunk view：Stage 3.5 + Stage 4
        if hold_reasons:
            for hr in hold_reasons:
                funnel["trunk"][hr] += 1
        if pb_reason is not None:
            funnel["trunk"][pb_reason] += 1
        if not hold_reasons and pb_reason is None:
            funnel["trunk"]["clean"] += 1
            clean_ids["trunk"].append(r.get("id"))
            by_week[wk]["trunk_clean"] += 1
            by_entry[entry]["trunk_clean"] += 1
            by_cohort_clean["trunk"][report_cohort(r)] += 1

    # ── reason_codes Top-N ──
    rc_out: dict[str, Any] = {}
    for cat in ("abstain", "wait", "no_trade", "invalid_run", "data_error"):
        recs = excluded_records.get(cat, [])
        code_counter: collections.Counter = collections.Counter()
        cat_counter: collections.Counter = collections.Counter()
        for r in recs:
            for c in dict.fromkeys(get_reason_codes(r)):
                code_counter[c[:120]] += 1
                cat_counter[classify_reason_code(c)] += 1
            for c in dict.fromkeys(get_failed_checks(r)):
                if "E-04" in c:
                    code_counter["failed_checks:E-04 守卫拦截"] += 1
                    cat_counter["guard:e04_priced_in_beat_miss"] += 1
        rc_out[cat] = {
            "reports": len(recs),
            "category_totals": dict(cat_counter.most_common()),
            "category_fix_notes": {k: v for k, v in GUARD_FIXES.items() if k in cat_counter},
            "top_codes": code_counter.most_common(20),
        }

    # ── E-04 型 ABSTAIN 复放 ──
    e04_reports = []
    for r in excluded_records.get("abstain", []):
        codes = get_reason_codes(r) + get_failed_checks(r)
        if any("E-04" in c for c in codes):
            e04_reports.append(r)
    e04_results = [replay_e04_report(r) for r in e04_reports]
    e04_class_counts = collections.Counter(x["class"] for x in e04_results)
    e04_hit_outcomes = collections.Counter(
        h["outcome"] for x in e04_results for h in x["hits"])

    # ── 每 100 份估算 ──
    raw = len(reports)
    est = {}
    for view in ("production", "trunk"):
        rate = funnel[view]["clean"] / raw * 100 if raw else 0.0
        est[view] = {
            "clean_per_100_completed": round(rate, 2),
            "reports_needed_for_60_clean": (int(60 / (rate / 100) + 0.9999) if rate > 0 else None),
        }
    # cohort 细分（clean 池）
    v1_prod = sum(v for k, v in by_cohort_clean["production"].items() if "legacy" not in k)
    v1_trunk = sum(v for k, v in by_cohort_clean["trunk"].items() if "legacy" not in k)
    est["cohort_note"] = {
        "production_clean_by_cohort": dict(by_cohort_clean["production"]),
        "trunk_clean_by_cohort": dict(by_cohort_clean["trunk"]),
        "v1_cohort_clean": {"production": v1_prod, "trunk": v1_trunk},
        "h1b_threshold_single_cohort": 60,
    }

    result = {
        "meta": {
            "db": args.db, "db_sha256": snap_sha, "snapshot_time_utc": snapshot_time,
            "user_id": args.user_id,
            "code_sha_trunk": "9d03c899e689687cc8c96e2544f8452455d393f9",
            "code_sha_production": "3d9c41495b748121032e038cf2ae85afeb64248d",
            "views": "production=无Stage3.5; trunk=Stage3.5(DAV-1139)+Stage4",
        },
        "funnel": {v: dict(funnel[v]) for v in funnel},
        "by_week": {k: dict(v) for k, v in sorted(by_week.items())},
        "by_entry": {k: dict(v) for k, v in sorted(by_entry.items())},
        "reason_codes": rc_out,
        "e04_replay": {
            "e04_abstain_reports": len(e04_reports),
            "class_counts": dict(e04_class_counts.most_common()),
            "hit_outcomes": dict(e04_hit_outcomes.most_common()),
            "details": e04_results,
        },
        "estimates": est,
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    md = render_md(result)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


def render_md(res: Mapping[str, Any]) -> str:
    L = []
    m = res["meta"]
    L.append("# DAV-1227 样本供给漏斗只读诊断\n")
    L.append(f"- 库快照：`{m['db']}` sha256=`{m['db_sha256']}` 回读时刻 {m['snapshot_time_utc']}")
    L.append(f"- 账户 `{m['user_id']}` completed 报告；production=`{m['code_sha_production'][:7]}` / trunk=`{m['code_sha_trunk'][:7]}`\n")
    for view, name in (("production", "production view"), ("trunk", "trunk view")):
        f = res["funnel"][view]
        L.append(f"## {name} 漏斗")
        L.append("```")
        L.append(f"raw {f.get('raw',0)} → v2 {f.get('v2',0)} → D-009 eligible {f.get('d009_eligible',0)} → clean {f.get('clean',0)}")
        L.append("D-009 排除: " + ", ".join(
            f"{k.replace('d009_','')}={v}" for k, v in sorted(f.items()) if k.startswith("d009_") and k != "d009_eligible"))
        L.append("隔离: " + ", ".join(
            f"{k}={v}" for k, v in sorted(f.items())
            if k.startswith(("hold_", "price_basis_"))))
        L.append("```\n")
    L.append("## 按周分解（raw→v2→eligible→prod_clean/trunk_clean）")
    L.append("| week | raw | v2 | eligible | prod_clean | trunk_clean |")
    L.append("|---|---|---|---|---|---|")
    for wk, c in res["by_week"].items():
        L.append(f"| {wk} | {c.get('raw',0)} | {c.get('v2',0)} | {c.get('d009_eligible',0)} | {c.get('prod_clean',0)} | {c.get('trunk_clean',0)} |")
    L.append("\n## 按入口分解")
    L.append("| entry | raw | v2 | eligible | prod_clean | trunk_clean |")
    L.append("|---|---|---|---|---|---|")
    for e, c in res["by_entry"].items():
        L.append(f"| {e} | {c.get('raw',0)} | {c.get('v2',0)} | {c.get('d009_eligible',0)} | {c.get('prod_clean',0)} | {c.get('trunk_clean',0)} |")
    L.append("\n## clean cohort 分解")
    L.append("```json")
    L.append(json.dumps(res["estimates"]["cohort_note"], ensure_ascii=False, indent=2))
    L.append("```")
    L.append("\n## reason_codes Top-N（按 D-009 排除类）")
    for cat, blk in res["reason_codes"].items():
        L.append(f"### {cat}（{blk['reports']} 份）")
        L.append("归类合计: " + json.dumps(blk["category_totals"], ensure_ascii=False))
        for k, n in blk["top_codes"][:12]:
            L.append(f"- {n}× `{k}`")
        for k, note in blk["category_fix_notes"].items():
            L.append(f"  - 注 {k}: {note}")
    L.append("\n## E-04 型 ABSTAIN 四类复放（trunk 9d03c89）")
    e4 = res["e04_replay"]
    L.append(f"E-04 ABSTAIN 报告 {e4['e04_abstain_reports']} 份；报告级分类：")
    L.append("```json\n" + json.dumps(e4["class_counts"], ensure_ascii=False, indent=2) + "\n```")
    L.append("occurrence 级 outcome：```json\n" + json.dumps(e4["hit_outcomes"], ensure_ascii=False, indent=2) + "\n```")
    L.append("\n## 每 100 份估算")
    L.append("```json\n" + json.dumps(res["estimates"], ensure_ascii=False, indent=2) + "\n```")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
