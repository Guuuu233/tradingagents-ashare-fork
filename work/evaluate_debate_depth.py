#!/usr/bin/env python3
"""Deterministic audit of persisted investment debates. No LLM calls."""
from __future__ import annotations
import argparse, json, re, sqlite3
from collections import defaultdict
from difflib import SequenceMatcher

ANALYST_FIELDS = {
    "macro": "macro_report", "market": "market_report", "sentiment": "sentiment_report",
    "news": "news_report", "fundamentals": "fundamentals_report",
    "smart_money": "smart_money_report", "volume_price": "volume_price_report",
}

def normalize(s: str) -> str:
    return re.sub(r"\s+", "", s or "")

def overlap_evidence(evidence: str, reports: dict[str, str]) -> list[str]:
    # Evidence provenance is considered text-supported only when a distinctive
    # 6+ character/numeric fragment is present in an analyst report.
    chunks = [x for x in re.split(r"[，。；、：:()（）\s]+", evidence or "") if len(x) >= 4]
    hits=[]
    for role,text in reports.items():
        nt=normalize(text)
        if any(normalize(x) in nt for x in chunks if len(normalize(x)) >= 4): hits.append(role)
    return hits

def audit(report: sqlite3.Row) -> dict:
    rd=json.loads(report["result_data"] or "{}")
    state=rd.get("investment_debate_state") or {}
    claims=state.get("claims") or []
    reports={k:rd.get(v) or "" for k,v in ANALYST_FIELDS.items()}
    rounds=defaultdict(list)
    for c in claims: rounds[int(c.get("round_index") or 0)].append(c)
    round_rows=[]
    previous_ids=set()
    for idx in sorted(rounds):
        cs=rounds[idx]; speaker=cs[0].get("speaker_key") if cs else ""
        targets=sorted({x for c in cs for x in (c.get("target_claim_ids") or [])})
        evidences=[str(e) for c in cs for e in (c.get("evidence") or [])]
        support=[{"evidence":e,"roles":overlap_evidence(e,reports)} for e in evidences]
        round_rows.append({
            "round_index":idx,"speaker":speaker,"claim_count":len(cs),
            "claim_ids":[c.get("claim_id") for c in cs],"target_claim_ids":targets,
            "targets_prior_claim":bool(set(targets)&previous_ids),
            "evidence_count":len(evidences),
            "evidence_supported_count":sum(bool(x["roles"]) for x in support),
            "unsupported_evidence":[x["evidence"] for x in support if not x["roles"]],
        })
        previous_ids.update(c.get("claim_id") for c in cs if c.get("claim_id"))
    similarities=[]
    for sp in ("Bull","Bear"):
        arr=[c for c in claims if c.get("speaker_key")==sp]
        for i in range(len(arr)):
            for j in range(i):
                similarities.append({"speaker":sp,"a":arr[j].get("claim"),"b":arr[i].get("claim"),
                    "ratio":round(SequenceMatcher(None,arr[j].get("claim", ""),arr[i].get("claim", "")).ratio(),3)})
    manager=state.get("judge_decision") or ""
    manager_role_mentions={role:manager.count(label) for role,label in {
        "macro":"宏观","market":"市场","sentiment":"情绪","news":"新闻",
        "fundamentals":"基本面","smart_money":"主力","volume_price":"量价"}.items()}
    round_messages=state.get("round_messages") or []
    manager_verdict=state.get("manager_verdict") or rd.get("manager_verdict") or {}
    evidence_verification=state.get("evidence_verification") or rd.get("evidence_verification") or []
    report_manifest=state.get("report_manifest") or rd.get("report_manifest") or {}
    later_messages=round_messages[1:]
    protocol_valid=(
        len(round_messages)==int(state.get("count") or 0)
        and all(m.get("parse_status")=="valid" for m in round_messages)
        and all(m.get("responded_claim_ids") for m in later_messages)
        and all(m.get("target_claim_ids") for m in later_messages)
    )
    return {
        "report_id":report["id"],"symbol":report["symbol"],"status":report["status"],
        "message_count":state.get("count"),"claim_count":len(claims),"rounds":round_rows,
        "round_messages_count":len(round_messages),"protocol_valid":protocol_valid,
        "round_message_parse_statuses":[m.get("parse_status") for m in round_messages],
        "max_same_side_claim_similarity":max([x["ratio"] for x in similarities],default=0),
        "high_similarity_pairs":[x for x in similarities if x["ratio"]>=0.75],
        "all_rounds_have_new_claims":all(x["claim_count"]>0 for x in round_rows),
        "later_rounds_machine_target_prior_claim":all(x["targets_prior_claim"] for x in round_rows[2:]) if len(round_rows)>2 else False,
        "all_evidence_text_supported":all(not x["unsupported_evidence"] for x in round_rows),
        "evidence_verification_count":len(evidence_verification),
        "fatal_evidence_count":sum(bool(x.get("is_fatal")) for x in evidence_verification if isinstance(x,dict)),
        "manager_verdict":manager_verdict,
        "manager_consistency_passed":manager_verdict.get("consistency_check_passed") if isinstance(manager_verdict,dict) else None,
        "report_manifest_roles":sorted(report_manifest.keys()) if isinstance(report_manifest,dict) else [],
        "manager_role_mentions":manager_role_mentions,
        "manager_mentions_all_seven":all(v>0 for v in manager_role_mentions.values()),
        "manager_evidence_labels":{k:manager.count(k) for k in ["证据充分","证据薄弱","证据缺失"]},
        "manager_outcome_labels":{k:manager.count(k) for k in ["多头胜","空头胜","势均力敌"]},
    }

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("report_ids",nargs="+"); ap.add_argument("--db",default="data/tradingagents.db")
    a=ap.parse_args(); c=sqlite3.connect(f"file:{a.db}?mode=ro",uri=True); c.row_factory=sqlite3.Row
    out=[]
    for rid in a.report_ids:
        r=c.execute("select id,symbol,status,result_data from reports where id=?",(rid,)).fetchone()
        if not r: raise SystemExit(f"missing report {rid}")
        out.append(audit(r))
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
