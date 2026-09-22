#!/usr/bin/env python3
"""DAV-1148 离线重放：冻结集 evidence_verification 双向迁移矩阵 + 停线判定。

仅对 work/golden_replay_1140e/frozen_reports.jsonl 执行，零库访问、零网络、零模型。
对每条历史核验记录按 (claim_id, raw) 对齐新重放结果，输出：
- 正向迁移：contradicted→verified/unsupported、unsupported→verified
- 反向退化：verified→contradicted/unsupported
- 残余 contradicted 的结构化维度标签（停线判定输入）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(OUT_DIR))

from replay_env import (  # noqa: E402
    classify_no_match,
    collect_run_identity,
    index_by_claim,
)

from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
    EvidenceFactualTruthEvaluator,
)

DATA = OUT_DIR / "frozen_reports.jsonl"
OUT = OUT_DIR / "replay_migration_report.json"

METRIC_RE = re.compile(r"指标 '([^']+)' 数据冲突|关键词 '([^']+)' 数据冲突")


def label_record(rec: dict, claim_lookup: dict) -> dict:
    """为残余 contradicted 记录抽取维度标签 (metric, entity, role, basis, period)。"""
    details = str(rec.get("details", ""))
    m = METRIC_RE.search(details)
    metric = (m.group(1) or m.group(2)) if m else None
    raw = str(rec.get("raw", ""))
    claim = claim_lookup.get(rec.get("claim_id"), {})
    return {
        "claim_id": rec.get("claim_id"),
        "speaker": claim.get("speaker_key") or claim.get("speaker"),
        "metric": metric,
        "role": rec.get("matched_role"),
        "source": rec.get("matched_source"),
        "basis": "scenario" if any(
            w in raw for w in ("压力测试", "情景", "假设", "测算", "若", "悲观", "乐观")
        ) else "actual",
        "period": next(
            (p for p in ("2026H1", "2026Q1", "2026Q2", "2025", "2024", "TTM", "年化", "单季")
             if p in raw),
            None,
        ),
        "raw": raw[:160],
        "details": details[:200],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", default=None,
                    help="baseline git 引用（默认 HEAD^），仅作身份记录")
    args = ap.parse_args()
    run_identity = collect_run_identity(
        REPO_ROOT, baseline_ref=args.baseline, corpus_path=DATA,
        manifest_path=DATA,
    )

    evaluator = EvidenceFactualTruthEvaluator()
    matrix = Counter()           # (old_status, new_status) -> count
    residual_contra = []         # 重放后仍 contradicted 的记录
    unmatched = []               # 历史记录在新输出中找不到对应条目
    per_report_skips = []
    n_reports = 0
    n_records = 0

    with open(DATA, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            n_reports += 1
            stored = rec["stored_evidence_verification"]
            if not stored:
                continue
            claims = rec["claims"] or []
            try:
                new_evs = evaluator.evaluate_claims(
                    claims=claims,
                    seven_reports=rec["seven_reports"],
                    market_data_context=rec.get("market_data_context"),
                    analysis_baseline_date=rec.get("analysis_baseline_date"),
                )
            except Exception as e:  # noqa: BLE001
                per_report_skips.append({"report_id": rec["report_id"], "error": repr(e)[:200]})
                continue
            claim_lookup = {c.get("claim_id"): c for c in claims if isinstance(c, dict)}
            new_map = {}
            for ev in new_evs or []:
                if isinstance(ev, dict):
                    new_map.setdefault((ev.get("claim_id"), str(ev.get("raw", ""))), ev)
            new_by_claim = index_by_claim(new_evs)
            for old in stored:
                if not isinstance(old, dict) or not old.get("status"):
                    continue
                n_records += 1
                key = (old.get("claim_id"), str(old.get("raw", "")))
                new = new_map.get(key)
                new_status = new.get("status") if new else None
                if new is None:
                    unmatched.append({"report_id": rec["report_id"], "claim_id": key[0],
                                      "raw": key[1][:160], "old_status": old.get("status"),
                                      "cause": classify_no_match(old, new_by_claim)})
                    new_status = "NO_MATCH"
                matrix[(old.get("status"), new_status)] += 1
                if new_status == "contradicted":
                    lbl = label_record(new, claim_lookup)
                    lbl["report_id"] = rec["report_id"]
                    lbl["symbol"] = rec["symbol"]
                    lbl["old_status"] = old.get("status")
                    residual_contra.append(lbl)

    report = {
        "run_identity": run_identity,
        "reports_replayed": n_reports,
        "records_compared": n_records,
        "reports_skipped_error": per_report_skips,
        "migration_matrix": {
            f"{a}->{b}": c for (a, b), c in sorted(matrix.items())
        },
        "unmatched_records": unmatched,
        "residual_contradicted": residual_contra,
        "residual_contradicted_count": len(residual_contra),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "residual_contradicted"
                      and k != "unmatched_records"}, ensure_ascii=False, indent=2))
    print(f"unmatched={len(unmatched)} residual_contra={len(residual_contra)}")


if __name__ == "__main__":
    main()
