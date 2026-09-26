#!/usr/bin/env python3
"""DAV-1311 — manager_consistency_hard_gate 精确率审计：语料抽取器（只读）。

用法（锁定解释器）：

    env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \\
        work/dav1311-hardgate-audit/extract.py \\
        --db /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db \\
        --corpus work/hist-bench-20260926/audit-corpus-hardgate.txt \\
        --out work/dav1311-hardgate-audit/dossiers

产出：每档位一个 <rid>_<horizon>.json 卷宗 + index.json（含分类结果）。
只读 DB（mode=ro），不写库、不跑分析、不调用任何 LLM/供应商。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from typing import Any, Mapping, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dav1261-abstain-diag"))
from diag import classify_code, classify_failed_check  # noqa: E402

HKEY = {"short_term": "short_term", "medium_term": "medium_term"}
CLAIM_ID_RE = re.compile(r"\b([A-Z]+-\d+)\b")


def _trunc(s: Any, n: int) -> Any:
    if isinstance(s, str) and len(s) > n:
        return s[:n] + f"…[+{len(s) - n}ch]"
    return s


def claim_brief(cid: str, ces_item: Optional[Mapping], claims_by_id: Mapping) -> dict:
    c = claims_by_id.get(cid) or {}
    s = ces_item if isinstance(ces_item, Mapping) else {}
    pa = []
    for p in s.get("proposition_audit") or []:
        if isinstance(p, Mapping):
            pa.append({
                "pid": p.get("proposition_id"),
                "kind": p.get("kind"),
                "support_status": p.get("support_status"),
                "reason": _trunc(p.get("reason"), 200),
                "evidence_refs": p.get("evidence_refs"),
            })
    return {
        "claim_id": cid,
        "stance": s.get("stance") or c.get("stance"),
        "claim": _trunc(s.get("claim") or c.get("claim"), 220),
        "status": c.get("status"),
        "coverage": s.get("coverage"),
        "decision": s.get("decision"),
        "semantic_decision": s.get("semantic_decision"),
        "semantic_partial_origin": s.get("semantic_partial_origin"),
        "semantic_hard_guards": s.get("semantic_hard_guards"),
        "counts": s.get("counts"),
        "verified_evidence": [_trunc(x, 160) for x in (s.get("verified_evidence") or [])],
        "unsupported_evidence": [_trunc(x, 160) for x in (s.get("unsupported_evidence") or [])],
        "contradicted_evidence": [_trunc(x, 160) for x in (s.get("contradicted_evidence") or [])],
        "evidence": [_trunc(x, 160) for x in (c.get("evidence") or [])],
        "pit_failed": s.get("pit_failed"),
        "is_fatal": s.get("is_fatal"),
        "reason": _trunc(s.get("reason"), 300),
        "proposition_audit": pa,
    }


def extract_entry(rid: str, horizon: str, source: str, rd: Mapping[str, Any],
                  created_at: str) -> dict:
    hz = rd.get(HKEY.get(horizon, horizon)) or {}
    if not isinstance(hz, Mapping):
        hz = {}
    deb = hz.get("investment_debate_state") or {}
    if not isinstance(deb, Mapping):
        deb = {}
    mv = hz.get("manager_verdict") or deb.get("manager_verdict") or {}
    if not isinstance(mv, Mapping):
        mv = {}
    ds = hz.get("decision_status") or {}
    if not isinstance(ds, Mapping):
        ds = {}
    ces = mv.get("claim_evidence_summary") or deb.get("claim_evidence_summary") or {}
    if not isinstance(ces, Mapping):
        ces = {}
    claims = deb.get("claims") or mv.get("claims") or []
    claims_by_id = {str(c.get("claim_id")): c for c in claims
                    if isinstance(c, Mapping) and c.get("claim_id")}

    failed = [str(x) for x in (mv.get("failed_checks") or []) if x]
    ds_failed = [str(x) for x in (ds.get("failed_checks") or []) if x]
    codes = [str(c) for c in (ds.get("reason_codes") or [])]
    hits = []
    for fc in failed + ds_failed:
        hits.append({"raw": fc, "cat": classify_failed_check(fc)})
    for c in codes:
        cat = classify_code(c)
        if cat:
            hits.append({"raw": c, "cat": cat})

    involved = set()
    for h in hits:
        involved.update(CLAIM_ID_RE.findall(h["raw"]))
    adopted = [str(x) for x in (mv.get("adopted_claim_ids") or [])]
    partial = [str(x) for x in (mv.get("partially_adopted_claims") or [])]
    rejected = [str(x) for x in (mv.get("rejected_claim_ids") or [])]
    involved.update(adopted)
    involved.update(partial)

    return {
        "report_id": rid,
        "horizon": horizon,
        "source": source,
        "created_at": created_at,
        "symbol": hz.get("company_of_interest") or rd.get("symbol"),
        "trade_date": hz.get("trade_date") or rd.get("trade_date"),
        "analysis_status": ds.get("analysis_status") or hz.get("analysis_status"),
        "direction": ds.get("direction"),
        "ds_reason_codes": codes,
        "ds_failed_checks": ds_failed,
        "mv_consistency_passed": mv.get("consistency_check_passed"),
        "mv_failed_checks": failed,
        "mv_direction": mv.get("direction"),
        "mv_winner": mv.get("winner"),
        "mv_reason": _trunc(mv.get("reason"), 900),
        "mv_position_pct": mv.get("position_pct"),
        "adopted_claim_ids": adopted,
        "partially_adopted_claims": partial,
        "rejected_claim_ids": rejected,
        "basis_from_rejected_claim_ids": mv.get("basis_from_rejected_claim_ids"),
        "evidence_basis": mv.get("evidence_basis"),
        "evidence_relation_status": mv.get("evidence_relation_status"),
        "evidence_relation_reason": _trunc(mv.get("evidence_relation_reason"), 400),
        "expectation_revision": _trunc(mv.get("expectation_revision"), 600),
        "warnings": [_trunc(w, 200) for w in (mv.get("warnings") or [])[:8]],
        "hit_categories": hits,
        "claims": {cid: claim_brief(cid, ces.get(cid), claims_by_id)
                   for cid in sorted(involved)},
        "judge_decision_tail": _trunc(str(deb.get("judge_decision") or "")[-2500:], 2600),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    entries = []
    for line in open(args.corpus, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rid, horizon, source = line.split(":")
        entries.append((rid, horizon, source))

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    os.makedirs(args.out, exist_ok=True)
    index = []
    for rid, horizon, source in entries:
        row = con.execute(
            "SELECT created_at, result_data FROM reports WHERE id=?", (rid,)
        ).fetchone()
        if not row:
            index.append({"report_id": rid, "horizon": horizon, "source": source,
                          "error": "report_not_found"})
            continue
        created_at, rd_raw = row
        rd = json.loads(rd_raw)
        e = extract_entry(rid, horizon, source, rd, created_at)
        fname = f"{rid}_{horizon}.json"
        with open(os.path.join(args.out, fname), "w", encoding="utf-8") as f:
            json.dump(e, f, ensure_ascii=False, indent=1)
        index.append({
            "report_id": rid, "horizon": horizon, "source": source,
            "file": fname, "symbol": e["symbol"], "trade_date": e["trade_date"],
            "cats": sorted({h["cat"] for h in e["hit_categories"]}),
            "n_checks": len(e["hit_categories"]),
        })
    con.close()
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(index)} dossiers -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
