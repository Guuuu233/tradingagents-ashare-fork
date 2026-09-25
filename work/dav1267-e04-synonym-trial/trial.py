#!/usr/bin/env python3
"""DAV-1267 — E-04 同义词词表在 DAV-1261 语料上的命中/误伤试算（只读，零 LLM）。

    env -u PYTHONPATH PYTHONPATH=<repo_root> \\
        /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \\
        work/dav1267-e04-synonym-trial/trial.py \\
        --db /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db \\
        --account 429163f7-50b6-4982-8bdf-96ae99506843 \\
        --since 2026-08-26 \\
        --postfix-cutoff "2026-09-25 01:05:19" \\
        --states <dav1249 states 目录> \\
        --out work/dav1267-e04-synonym-trial/out

口径：与守卫相同的 full_text（剥系统文案后的 judge_decision + reason +
investment_plan）。对每个词表项统计：
  - doc_freq：含该词的经理文本份数（按 group 分列）；
  - near_hit_docs：E-04 命中（hit_collector 有记录）且该词出现在命中句
    ±160 字窗口内的份数——返修时这些位置被改写的概率最高，词若常见
    则 near_hit 规则有误伤风险；
  - 另统计「该词在 E-04 文档中的命中率」与「在非 E-04/通过文档中的
    命中率」供人工判断词表风险。
不写生产库、不改代码行为。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dav1261-abstain-diag"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import diag  # noqa: E402

from tradingagents.agents.utils.e04_revision import (  # noqa: E402
    E04_SYNONYM_TERMS, _SYNONYM_WINDOW)


def full_text_of(rec: dict) -> str:
    raw = diag.manager_raw_text(rec)
    reason = str(rec["mv"].get("reason") or "")
    plan = str(rec["mv"].get("investment_plan") or "")
    return "\n".join(t for t in (raw, reason, plan) if t)


def hit_spans_of(rec: dict, validate) -> list:
    raw = diag.manager_raw_text(rec)
    mv = dict(rec["mv"])
    hits: list = []
    validate(
        manager_verdict=mv,
        raw_response=raw,
        expectation_revisions=rec["mv"].get("expectation_revision"),
        claims=rec["deb"].get("claims") or [],
        seven_reports=diag.seven_reports_of(rec["rd"]),
        hit_collector=hits,
    )
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--account",
                    default="429163f7-50b6-4982-8bdf-96ae99506843")
    ap.add_argument("--since", default="2026-08-26")
    ap.add_argument("--postfix-cutoff", default="2026-09-25 01:05:19")
    ap.add_argument("--states", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    records = diag.load_db_records(args.db, args.account, args.since,
                                   args.postfix_cutoff)
    if args.states:
        records += diag.load_state_records(args.states)

    from tradingagents.agents.managers.research_manager import (
        validate_manager_expectation_revision_consumption as validate,
    )

    per_term = {t: Counter() for t in E04_SYNONYM_TERMS}
    e04_docs = 0
    near_hit_docs = {t: 0 for t in E04_SYNONYM_TERMS}
    e04_term_docs = {t: 0 for t in E04_SYNONYM_TERMS}
    detail_rows = []

    for rec in records:
        ft = full_text_of(rec)
        hits = hit_spans_of(rec, validate)
        is_e04 = bool(hits)
        if is_e04:
            e04_docs += 1
        for term in E04_SYNONYM_TERMS:
            occ = []
            idx = ft.find(term)
            while idx >= 0:
                occ.append((idx, idx + len(term)))
                idx = ft.find(term, idx + 1)
            if not occ:
                continue
            per_term[term][f"{rec['group']}|all"] += 1
            if is_e04:
                e04_term_docs[term] += 1
                near = any(
                    hs - _SYNONYM_WINDOW <= t0 and t1 <= he + _SYNONYM_WINDOW + (t1 - t0)
                    for (t0, t1) in occ
                    for h in hits
                    for hs, he in [tuple(h.get("span") or (0, 0))]
                    if h.get("span")
                )
                if near:
                    near_hit_docs[term] += 1
                    detail_rows.append({
                        "id": rec["id"], "term": term,
                        "group": rec["group"],
                        "violation": hits[0].get("violation", "")[:80],
                    })

    out = {
        "params": vars(args),
        "synonym_window": _SYNONYM_WINDOW,
        "corpus_docs": len(records),
        "e04_hit_docs": e04_docs,
        "doc_freq_by_group": {t: dict(c) for t, c in per_term.items()},
        "term_docs_within_e04_docs": e04_term_docs,
        "near_hit_docs_within_e04_docs": near_hit_docs,
        "near_hit_detail": detail_rows[:50],
    }
    Path(args.out, "synonym_trial.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: out[k] for k in (
        "corpus_docs", "e04_hit_docs", "doc_freq_by_group",
        "term_docs_within_e04_docs", "near_hit_docs_within_e04_docs")},
        ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
