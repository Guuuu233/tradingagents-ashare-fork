import sqlite3, json, sys, collections
sys.path.insert(0, "/Users/davidliu/Documents/TradingAgents-AShare")
from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator, extract_and_validate_manager_verdict,
)
from tradingagents.agents.managers.research_manager import (
    validate_manager_expectation_revision_consumption,
)
from tradingagents.agents.utils.decision_status import status_from_manager_verdict

WARN = "\n\n[系统硬闸告警]"
RK = ["macro_report","market_report","sentiment_report","news_report",
      "fundamentals_report","smart_money_report","volume_price_report"]
def pick_deb(data):
    best,score=None,-1
    for h in (data, data.get("short_term") or {}):
        ids = h.get("investment_debate_state") if isinstance(h,dict) else None
        if isinstance(ids,dict):
            s=len(ids.get("claims") or [])+(10 if ids.get("judge_decision") else 0)+(5 if ids.get("manager_verdict") else 0)
            if s>score: best,score=ids,s
    return best or {}
conn = sqlite3.connect("file:data/tradingagents.db?mode=ro", uri=True)
conn.execute("PRAGMA query_only=ON")
rows = conn.execute("""select id, result_data from reports
  where status='completed' and analysis_status='ABSTAIN'
    and result_data like '%manager_consistency_hard_gate%'""").fetchall()
ev = EvidenceFactualTruthEvaluator()
cat = collections.Counter(); detail=[]
for rid, rd in rows:
    try:
        data = json.loads(rd) if isinstance(rd,str) else rd
        deb = pick_deb(data)
        mv_old = data.get("manager_verdict") or deb.get("manager_verdict") or {}
        old_fc = set(str(x)[:60] for x in (mv_old.get("failed_checks") or []))
        claims = deb.get("claims") or []
        seven = {k:(data.get(k) or "") for k in RK}
        mdc = data.get("market_data_context") or {}
        raw = (deb.get("judge_decision") or "")
        i = raw.find(WARN);  raw = raw[:i] if i>=0 else raw
        cv = ev.evaluate_claims(claims=claims, seven_reports=seven, market_data_context=mdc,
                                analysis_baseline_date=data.get("trade_date"), social_data_context=None)
        mv2 = extract_and_validate_manager_verdict(raw_response=raw, claims_verification=cv,
            claims=claims, challenges=None, challenges_verification=None, market_data_context=mdc)
        er_ok, er_viol = validate_manager_expectation_revision_consumption(
            manager_verdict=mv2, raw_response=raw,
            expectation_revisions=mv_old.get("expectation_revision") or {},
            claims=claims, seven_reports=seven)
        new_fc = set(str(x)[:60] for x in (mv2.get("failed_checks") or [])) | set(str(x)[:60] for x in er_viol)
        if not er_ok:
            mv2["consistency_check_passed"]=False
            mv2["failed_checks"]=list(mv2.get("failed_checks") or [])+[str(v) for v in er_viol]
        ds2 = status_from_manager_verdict(mv2, investment_debate_state=deb,
                                        claims_verification=cv, claims=claims, market_data_context=mdc)
        st2=getattr(ds2,"analysis_status",str(ds2)); rc2=[str(x) for x in (getattr(ds2,"reason_codes",[]) or [])]
        hg = any("manager_consistency_hard_gate" in x for x in rc2)
        if not hg:
            cat[f"FLIP->{st2}"] += 1
            detail.append((rid[:8],"FLIP",st2,rc2[:3],old_fc,new_fc))
        else:
            same = bool(old_fc & new_fc)
            which = [x for x in rc2 if x!="manager_consistency_hard_gate"][:3]
            tag = "STAY_同因" if same else "STAY_异因"
            cat[tag] += 1
            detail.append((rid[:8],tag,which,old_fc,new_fc))
    except Exception as e:
        cat["ERROR"]+=1; detail.append((rid[:8],"ERROR",repr(e)[:120],set(),set()))
print("=== 归类 ===")
for k,v in cat.most_common(): print(f"  {v:4d}  {k}")
print("\n=== STAY_异因 样例（旧因消失但新闸接住）===")
for d in [x for x in detail if x[1]=="STAY_异因"][:8]:
    print(" ",d[0],"新codes:",d[2]); print("    旧fc:",list(d[3])[:2]); print("    新fc:",list(d[4])[:2])
print("\n=== FLIP 样例 ===")
for d in [x for x in detail if x[1]=="FLIP"][:9]:
    print(" ",d[0],"->",d[2],d[3]); print("    旧fc:",list(d[4])[:2])
