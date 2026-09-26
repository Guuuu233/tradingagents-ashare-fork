#!/usr/bin/env python3
"""DAV-1311 卷宗渲染：把 dossier json 打印成可审读的紧凑文本。"""
import json, sys, glob, os

def show(p):
    e = json.load(open(p, encoding="utf-8"))
    print("=" * 100)
    print(f"{e['report_id']}:{e['horizon']}:{e['source']}  {e['symbol']} {e['trade_date']}  status={e['analysis_status']} dir={e['direction']} mv_dir={e['mv_direction']} winner={e['mv_winner']}")
    print("-- hit_categories --")
    for h in e["hit_categories"]:
        print(f"  [{h['cat']}] {h['raw'][:240]}")
    print(f"-- adopted={e['adopted_claim_ids']} partial={e['partially_adopted_claims']} rejected={e['rejected_claim_ids']}")
    if e.get("basis_from_rejected_claim_ids"):
        print(f"-- basis_from_rejected: {e['basis_from_rejected_claim_ids']}")
    eb = e.get("evidence_basis") or {}
    for it in (eb.get("items") or []):
        print(f"  eb[{it.get('claim_id')}] src={it.get('source')} verified={it.get('verified_subfacts')}")
    print("-- mv_reason:", (e.get("mv_reason") or "")[:600])
    if e.get("expectation_revision"):
        print("-- expectation_revision:", str(e["expectation_revision"])[:500])
    for cid, c in (e.get("claims") or {}).items():
        print(f"  CLAIM {cid} stance={c['stance']} cov={c['coverage']} sem={c['semantic_decision']} origin={c.get('semantic_partial_origin')} pit={c['pit_failed']} fatal={c['is_fatal']}")
        print(f"    text: {c['claim']}")
        if c.get("unsupported_evidence"):
            print(f"    UNSUPPORTED: {c['unsupported_evidence']}")
        if c.get("contradicted_evidence"):
            print(f"    CONTRA: {c['contradicted_evidence']}")
        if c.get("semantic_hard_guards"):
            print(f"    guards: {c['semantic_hard_guards']}")
        for pa in c.get("proposition_audit") or []:
            print(f"    prop {pa['pid']} {pa['support_status']} :: {pa['reason']} refs={pa['evidence_refs']}")
    tail = e.get("judge_decision_tail") or ""
    if tail:
        print("-- judge_tail:", tail[-1200:])

paths = sys.argv[1:] or sorted(glob.glob(os.path.join(os.path.dirname(__file__), "dossiers", "*.json")))
for p in paths:
    if os.path.basename(p) == "index.json":
        continue
    show(p)
