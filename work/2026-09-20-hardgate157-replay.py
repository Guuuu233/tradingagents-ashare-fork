# -*- coding: utf-8 -*-
"""157 条 manager_consistency_hard_gate ABSTAIN 的只读离线复放（trunk 3907c73 代码）。
输入全部取自 result_data 冻结产物；不改库、不写文件到仓内。"""
import sqlite3, json, sys, collections, re
sys.path.insert(0, "/Users/davidliu/Documents/TradingAgents-AShare")
from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator, extract_and_validate_manager_verdict,
)
from tradingagents.agents.managers.research_manager import (
    validate_manager_expectation_revision_consumption,
)
from tradingagents.agents.utils.decision_status import status_from_manager_verdict

DB = "file:data/tradingagents.db?mode=ro"
WARN = "\n\n[系统硬闸告警]"
REPORT_KEYS = ["macro_report","market_report","sentiment_report","news_report",
               "fundamentals_report","smart_money_report","volume_price_report"]

def pick_deb(data):
    cands = []
    for holder in (data, data.get("short_term") or {}):
        ids = holder.get("investment_debate_state") if isinstance(holder, dict) else None
        if isinstance(ids, dict): cands.append(ids)
    best, score = None, -1
    for ids in cands:
        s = len(ids.get("claims") or []) + (10 if ids.get("judge_decision") else 0) + (5 if ids.get("manager_verdict") else 0)
        if s > score: best, score = ids, s
    return best or {}

def clean_raw(t):
    t = t or ""
    i = t.find(WARN)
    return t[:i] if i >= 0 else t

def find_sha(o):
    if isinstance(o, dict):
        if "generated_by_commit_sha" in o: return str(o["generated_by_commit_sha"])[:7]
        for v in o.values():
            r = find_sha(v)
            if r: return r
    elif isinstance(o, list):
        for v in o:
            r = find_sha(v)
            if r: return r
    return None

def family(mv):
    fcs = [str(x) for x in (mv.get("failed_checks") or [])]
    if not fcs: return "dedup_family(无failed_checks)"
    if any("E-04" in x for x in fcs): return "E-04"
    if any("覆盖率" in x for x in fcs): return "coverage"
    return "other:" + fcs[0][:40]

conn = sqlite3.connect(DB, uri=True)
conn.execute("PRAGMA query_only=ON")
rows = conn.execute("""select id, symbol, trade_date, result_data from reports
  where status='completed' and analysis_status='ABSTAIN'
    and result_data like '%manager_consistency_hard_gate%'""").fetchall()
print(f"目标 {len(rows)} 条", file=sys.stderr)

evaluator = EvidenceFactualTruthEvaluator()
outcome = collections.Counter(); fam_flip = collections.Counter(); sha_flip = collections.Counter()
errors = []; flipped_ids = []; stayed_ids = []

for rid, sym, td, rd in rows:
    try:
        data = json.loads(rd) if isinstance(rd, str) else rd
        deb = pick_deb(data)
        mv_old = data.get("manager_verdict") or deb.get("manager_verdict") or {}
        fam = family(mv_old)
        sha = find_sha(data) or "?"
        claims = deb.get("claims") or []
        seven = {k: (data.get(k) or "") for k in REPORT_KEYS}
        mdc = data.get("market_data_context") or deb.get("market_data_context") or {}
        baseline = data.get("analysis_baseline_date") or td
        raw = clean_raw(deb.get("judge_decision") or deb.get("current_response") or "")

        cv = evaluator.evaluate_claims(claims=claims, seven_reports=seven,
                                       market_data_context=mdc,
                                       analysis_baseline_date=baseline,
                                       social_data_context=None)
        mv2 = extract_and_validate_manager_verdict(
            raw_response=raw, claims_verification=cv, claims=claims,
            challenges=None, challenges_verification=None, market_data_context=mdc)
        er = mv_old.get("expectation_revision") or {}
        er_ok, er_viol = validate_manager_expectation_revision_consumption(
            manager_verdict=mv2, raw_response=raw, expectation_revisions=er,
            claims=claims, seven_reports=seven)
        if not er_ok:
            mv2["consistency_check_passed"] = False
            mv2.setdefault("failed_checks", [])
            mv2["failed_checks"] = list(mv2["failed_checks"]) + [str(v) for v in er_viol]

        ds2 = status_from_manager_verdict(mv2, investment_debate_state=deb,
                                        claims_verification=cv, claims=claims,
                                        market_data_context=mdc)
        st2 = getattr(ds2, "analysis_status", str(ds2))
        rc2 = list(getattr(ds2, "reason_codes", []) or [])
        hg2 = any("manager_consistency_hard_gate" in x for x in rc2)
        key = f"{st2}{'|hard_gate' if hg2 else ''}"
        outcome[key] += 1
        tag = "STAY" if hg2 else "FLIP"
        (flipped_ids if not hg2 else stayed_ids).append(rid[:8])
        fam_flip[(fam, tag)] += 1
        sha_flip[(sha, tag)] += 1
    except Exception as e:
        outcome["ERROR"] += 1
        errors.append((rid[:8], repr(e)[:160]))

print("\n========== 复放结果 ==========")
print("=== 复放后状态分布 ===")
for k, v in outcome.most_common(): print(f"  {v:4d}  {k}")
print("\n=== 按家族 × 翻转 ===")
for (fam, tag), v in sorted(fam_flip.items()): print(f"  {v:4d}  {fam}  {tag}")
print("\n=== 按生成 SHA × 翻转 ===")
for (sha, tag), v in sorted(sha_flip.items()): print(f"  {v:4d}  {sha}  {tag}")
print("\n=== 异常明细 ===")
for e in errors[:12]: print("  ", e)
print("\nFLIP 样例:", flipped_ids[:12])
print("STAY 样例:", stayed_ids[:12])
