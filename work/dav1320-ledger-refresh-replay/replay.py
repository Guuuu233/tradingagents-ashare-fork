#!/usr/bin/env python3
"""DAV-1320 — 零 LLM 重放：一致性检查改用守卫裁剪后最终账本的验收重算（只读）。

用法（锁定解释器）：

    env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \\
        work/dav1320-ledger-refresh-replay/replay.py \\
        --db /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db \\
        --corpus work/hist-bench-20260926/audit-corpus-hardgate.txt \\
        --account 429163f7-50b6-4982-8bdf-96ae99506843 \\
        --abstain-since 2026-08-26 \\
        --out work/dav1320-ledger-refresh-replay/out

逻辑：逐档取落库 manager_verdict（守卫裁剪后的最终账本 + 裁剪前账本算的
failed_checks）。由 excluded_evidence 中 reason 以 "double_count_guard" 开头
的条目还原守卫前 adopted 账本，分别用 _claim_ledger_consistency_checks 在
前/后账本上重算 Check6+Check7，多重集替换后得到候选 failed_checks：
    final = (persisted_failed − old_ledger_checks) + new_ledger_checks
与账本无关的失败项原样保留。不写库、不调用 LLM/供应商。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from typing import Any, Mapping

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dav1261-abstain-diag"))

from diag import classify_failed_check  # noqa: E402
from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
    _claim_ledger_consistency_checks,
)

HKEY = {"short_term": "short_term", "medium_term": "medium_term"}
CLAIM_ID_RE = re.compile(r"\b([A-Z]+-\d+)\b")
GUARD_REASON_PREFIX = "double_count_guard"


def _str_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def load_report(con: sqlite3.Connection, rid: str) -> Mapping[str, Any] | None:
    row = con.execute(
        "SELECT created_at, result_data FROM reports WHERE id=?", (rid,)
    ).fetchone()
    if not row:
        return None
    return {"created_at": row[0], "rd": json.loads(row[1])}


def hz_of(rd: Mapping[str, Any], horizon: str) -> Mapping[str, Any]:
    hz = rd.get(HKEY.get(horizon, horizon))
    return hz if isinstance(hz, Mapping) else {}


def verdict_parts(rd: Mapping[str, Any], horizon: str) -> dict:
    """抽取重放所需字段。优先 horizon 子结构，回落顶层。"""
    hz = hz_of(rd, horizon)
    deb = hz.get("investment_debate_state") or rd.get("investment_debate_state") or {}
    if not isinstance(deb, Mapping):
        deb = {}
    mv = hz.get("manager_verdict") or deb.get("manager_verdict") or rd.get("manager_verdict") or {}
    if not isinstance(mv, Mapping):
        mv = {}
    ds = hz.get("decision_status") or rd.get("decision_status") or {}
    if not isinstance(ds, Mapping):
        ds = {}
    return {
        "mv": mv,
        "ds": ds,
        "deb": deb,
        "analysis_status": ds.get("analysis_status") or hz.get("analysis_status") or rd.get("analysis_status"),
        "claims": deb.get("claims") or [],
        "claims_verification": deb.get("claims_verification") or rd.get("claims_verification") or [],
    }


def replay_one(parts: dict) -> dict:
    """对单档做守卫前后账本重算，返回翻转明细。"""
    mv = parts["mv"]
    claims = parts["claims"]
    cv = parts["claims_verification"]
    ces = mv.get("claim_evidence_summary") or {}

    post_adopted = _str_list(mv.get("adopted_claim_ids"))
    post_partial = _str_list(mv.get("partially_adopted_claims"))
    post_rejected = _str_list(mv.get("rejected_claim_ids"))

    # 还原守卫前账本：守卫把剔除论点记入 excluded_evidence（reason 前缀）
    guarded_out: list[str] = []
    for e in mv.get("excluded_evidence") or []:
        if isinstance(e, Mapping) and str(e.get("reason") or "").startswith(GUARD_REASON_PREFIX):
            cid = str(e.get("claim_id") or "").strip()
            if cid:
                guarded_out.append(cid)
    pre_adopted = post_adopted + [c for c in guarded_out if c not in post_adopted]

    kw = dict(claims=claims, claim_evidence_summary=ces,
              claims_verification=cv, prose="")
    old_checks = _claim_ledger_consistency_checks(
        adopted_claim_ids=pre_adopted,
        partially_adopted_claims=post_partial,
        rejected_claim_ids=post_rejected,
        **kw,
    )
    new_checks = _claim_ledger_consistency_checks(
        adopted_claim_ids=post_adopted,
        partially_adopted_claims=post_partial,
        rejected_claim_ids=post_rejected,
        **kw,
    )

    persisted_failed = _str_list(mv.get("failed_checks"))
    remaining = list(persisted_failed)
    removed: list[str] = []
    for item in old_checks:
        try:
            remaining.remove(item)
            removed.append(item)
        except ValueError:
            pass
    final = remaining + new_checks

    old_set = Counter(old_checks)
    new_set = Counter(new_checks)
    vanished = list((old_set - new_set).elements())
    appeared = list((new_set - old_set).elements())

    return {
        "guarded_out_claim_ids": guarded_out,
        "old_ledger_checks": old_checks,
        "new_ledger_checks": new_checks,
        "vanished_checks": vanished,
        "appeared_checks": appeared,
        "persisted_passed": bool(mv.get("consistency_check_passed", True)),
        "candidate_passed": len(final) == 0,
        "candidate_failed_checks": final,
        "vanished_claim_ids": sorted({
            cid for item in vanished for cid in CLAIM_ID_RE.findall(item)
        }),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--abstain-since", default="2026-08-26")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    results: list[dict] = []

    # ── 47 档硬门语料 ──
    for line in open(args.corpus, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rid, horizon, source = line.split(":")
        rec = load_report(con, rid)
        if not rec:
            results.append({"corpus": "hardgate47", "id": f"{rid}:{horizon}",
                            "error": "report_not_found"})
            continue
        parts = verdict_parts(rec["rd"], horizon)
        if not parts["mv"]:
            results.append({"corpus": "hardgate47", "id": f"{rid}:{horizon}",
                            "error": "no_manager_verdict"})
            continue
        r = replay_one(parts)
        r.update({"corpus": "hardgate47", "id": f"{rid}:{horizon}",
                  "source": source, "created_at": rec["created_at"],
                  "analysis_status": parts["analysis_status"]})
        results.append(r)

    # ── DAV-1261 历史 ABSTAIN 语料（DB 层，与 diag.py 同口径） ──
    rows = con.execute(
        "SELECT id, created_at, result_data FROM reports "
        "WHERE user_id=? AND status='completed' AND created_at>? ORDER BY created_at",
        (args.account, args.abstain_since),
    ).fetchall()
    corpus_ids = {r["id"].split(":")[0] for r in results}
    n_abstain = 0
    for rid, ca, rd_raw in rows:
        if rid in corpus_ids:
            continue
        rd = json.loads(rd_raw)
        for horizon in ("short_term", "medium_term"):
            parts = verdict_parts(rd, horizon)
            if not parts["mv"] or parts["analysis_status"] != "ABSTAIN":
                continue
            # 只重放走 consistency 门的档（failed_checks 非空）
            if not parts["mv"].get("failed_checks"):
                continue
            n_abstain += 1
            r = replay_one(parts)
            r.update({"corpus": "dav1261_abstain_db", "id": f"{rid}:{horizon}",
                      "created_at": ca, "analysis_status": "ABSTAIN"})
            results.append(r)
    con.close()

    flips = [r for r in results
             if not r.get("error") and not r["persisted_passed"] and r["candidate_passed"]]
    still = [r for r in results
             if not r.get("error") and not r["persisted_passed"] and not r["candidate_passed"]]

    summary = {
        "total": len(results),
        "abstain_db_records": n_abstain,
        "persisted_blocked": sum(1 for r in results if not r.get("error") and not r["persisted_passed"]),
        "flip_abstain_to_valid": len(flips),
        "still_blocked": len(still),
        "errors": sum(1 for r in results if r.get("error")),
        "flips": [
            {"id": r["id"], "corpus": r["corpus"],
             "guarded_out": r["guarded_out_claim_ids"],
             "vanished_checks": r["vanished_checks"],
             "vanished_claim_ids": r["vanished_claim_ids"],
             "appeared_checks": r["appeared_checks"],
             "vanished_cats": sorted({classify_failed_check(c) for c in r["vanished_checks"]})}
            for r in flips
        ],
        "still_blocked_ids": [r["id"] for r in still],
        "records": results,
    }
    out_path = os.path.join(args.out, "summary.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"total={summary['total']} blocked={summary['persisted_blocked']} "
          f"flips={summary['flip_abstain_to_valid']} still={summary['still_blocked']} "
          f"errors={summary['errors']} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
