#!/usr/bin/env python3
"""DAV-1148 最终验收重放：verifier-corpus-20260919 冻结语料双向迁移矩阵 + 停线判定。

总工裁定口径：
- 只读冻结集 work/verifier-corpus-20260919/（114 contradicted + 24 b2b3），不查活库；
- 五分类迁移统计 + NO_MATCH 单列；
- 残余 contradicted 输出 (metric, entity, semantic_role, basis, period) 机读标签；
- 黄金 4 票（000768/000333/000063/002142）在 b2b3 语料内同路径端到端重放。
零网络、零模型、零库访问。
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
    SEVEN_REPORT_KEYS,
    EvidenceFactualTruthEvaluator,
)

DEFAULT_CORPUS = REPO_ROOT / "work" / "verifier-corpus-20260919"
OUT = OUT_DIR / "replay_corpus_20260919_report.json"

GOLDEN = {
    "000768.SZ": ["股价 22.76 元不得绑净利润", "18.81 万户户数不得绑两融", "营收同比不得与净利同比互打"],
    "000333.SZ": ["美的不得与奥克斯跨实体互判", "压力测试 335 亿不得与实际 445 亿互判"],
    "000063.SZ": ["换手 1.11% 不得与 20% 阈值互判", "INV-10 跨报告聚合应恢复 verified"],
    "002142.SZ": ["单季 ROE 3.595% 不得与年化互判"],
}

METRIC_RE = re.compile(r"指标 '([^']+)' 数据冲突|关键词 '([^']+)' 数据冲突")
ENTITY_RE = re.compile(r"(?:实体|主体)[:：]?\s*'?([^'，,\s]+)")


def label_record(rec: dict, claim_lookup: dict) -> dict:
    """残余 contradicted 机读标签: (metric, entity, semantic_role, basis, period)。"""
    details = str(rec.get("details", ""))
    m = METRIC_RE.search(details)
    metric = (m.group(1) or m.group(2)) if m else None
    e = ENTITY_RE.search(details)
    entity = e.group(1) if e else None
    raw = str(rec.get("raw", ""))
    claim = claim_lookup.get(rec.get("claim_id"), {})
    return {
        "claim_id": rec.get("claim_id"),
        "speaker": claim.get("speaker_key") or claim.get("speaker"),
        "metric": metric,
        "entity": entity,
        "semantic_role": rec.get("matched_role"),
        "source": rec.get("matched_source"),
        "basis": "scenario" if any(
            w in raw for w in ("压力测试", "情景", "假设", "测算", "若", "悲观", "乐观", "敏感性")
        ) else "actual",
        "period": next(
            (p for p in ("2026H1", "2026Q1", "2026Q2", "2026Q3", "2025", "2024",
                         "TTM", "年化", "单季", "月度", "季度")
             if p in raw),
            None,
        ),
        "is_fatal": rec.get("is_fatal"),
        "raw": raw[:200],
        "details": details[:240],
    }


def replay_file(evaluator, path: Path):
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)
    rd = blob["result_data"]
    ids = rd.get("investment_debate_state") or {}
    claims = ids.get("claims") or []
    stored = rd.get("evidence_verification") or []
    seven_reports = {k: str(rd.get(k, "") or "") for k in SEVEN_REPORT_KEYS}
    new_evs = evaluator.evaluate_claims(
        claims=claims,
        seven_reports=seven_reports,
        market_data_context=rd.get("market_data_context"),
        analysis_baseline_date=rd.get("analysis_baseline_date"),
        social_data_context=rd.get("social_data_context"),
    )
    claim_lookup = {c.get("claim_id"): c for c in claims if isinstance(c, dict)}
    new_map = {}
    for ev in new_evs or []:
        if isinstance(ev, dict):
            new_map.setdefault((ev.get("claim_id"), str(ev.get("raw", ""))), ev)
    rows = []
    for old in stored:
        if not isinstance(old, dict) or not old.get("status"):
            continue
        key = (old.get("claim_id"), str(old.get("raw", "")))
        new = new_map.get(key)
        rows.append({
            "old": old,
            "new": new,
            "new_status": new.get("status") if new else "NO_MATCH",
        })
    return blob, claims, claim_lookup, new_evs, rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", default=None,
                    help="baseline git 引用（默认 HEAD^，即直接父提交），仅作身份记录")
    ap.add_argument("--corpus", default=None,
                    help="冻结语料目录（默认 work/verifier-corpus-20260919，"
                         "用于在其他 checkout 的 worktree 中复跑）")
    args = ap.parse_args()

    corpus = Path(args.corpus).resolve() if args.corpus else DEFAULT_CORPUS
    run_identity = collect_run_identity(
        REPO_ROOT,
        baseline_ref=args.baseline,
        corpus_path=corpus,
        manifest_path=corpus / "MANIFEST.json",
    )

    evaluator = EvidenceFactualTruthEvaluator()
    manifest = json.loads((corpus / "MANIFEST.json").read_text(encoding="utf-8"))

    matrix = Counter()
    residual_contra = []      # 重放后仍 contradicted
    unmatched = []            # 历史记录在新输出中找不到对应条目（NO_MATCH 归因）
    per_report = []
    errors = []
    golden_reports = {}

    for corpus_name, cinfo in manifest["corpus"].items():
        for rinfo in cinfo["reports"]:
            path = corpus / rinfo["file"]
            blob = None
            try:
                blob, claims, claim_lookup, new_evs, rows = replay_file(evaluator, path)
            except Exception as exc:  # noqa: BLE001
                errors.append({"file": rinfo["file"], "error": repr(exc)[:300]})
                continue
            rid = blob["report_id"]
            sym = blob["symbol"]
            n_new_contra = sum(1 for e in new_evs if e.get("status") == "contradicted")
            new_by_claim = index_by_claim(new_evs)
            report_matrix = Counter()
            for row in rows:
                report_matrix[(row["old"].get("status"), row["new_status"])] += 1
                if row["new_status"] == "NO_MATCH":
                    unmatched.append({
                        "corpus": corpus_name, "file": rinfo["file"],
                        "report_id": rid, "symbol": sym,
                        "claim_id": row["old"].get("claim_id"),
                        "raw": str(row["old"].get("raw", ""))[:160],
                        "old_status": row["old"].get("status"),
                        "cause": classify_no_match(row["old"], new_by_claim),
                    })
            per_report.append({
                "corpus": corpus_name, "file": rinfo["file"], "report_id": rid,
                "symbol": sym, "trade_date": blob.get("trade_date"),
                "stored_records": len(rows),
                "new_records": len(new_evs or []),
                "new_contradicted": n_new_contra,
                "matrix": {
                    f"{o}->{n}": c for (o, n), c in sorted(report_matrix.items())
                },
            })
            if sym in GOLDEN:
                golden_reports.setdefault(sym, []).append(
                    {"file": rinfo["file"], "report_id": rid,
                     "new_evs": new_evs, "claim_lookup": claim_lookup})
            stored_keys = {
                (e.get("claim_id"), str(e.get("raw", "")))
                for e in (blob["result_data"].get("evidence_verification") or [])
                if isinstance(e, dict)
            }
            for row in rows:
                old_status = row["old"].get("status")
                matrix[(old_status, row["new_status"])] += 1
            for e in new_evs or []:
                if e.get("status") != "contradicted":
                    continue
                key = (e.get("claim_id"), str(e.get("raw", "")))
                lab = label_record(e, claim_lookup)
                lab.update({"corpus": corpus_name, "file": rinfo["file"],
                            "report_id": rid, "symbol": sym,
                            "matched_stored": key in stored_keys})
                residual_contra.append(lab)

    report = {
        "run_identity": run_identity,
        "corpus_dir": str(corpus.relative_to(REPO_ROOT)) if corpus.is_relative_to(REPO_ROOT) else str(corpus),
        "manifest_sha": {k: v.get("manifest_sha256") for k, v in manifest["corpus"].items()},
        "reports_replayed": len(per_report),
        "errors": errors,
        "migration_matrix": {
            f"{o}->{n}": c for (o, n), c in sorted(matrix.items())
        },
        "unmatched_records": unmatched,
        "unmatched_count": len(unmatched),
        "residual_contradicted": residual_contra,
        "per_report": per_report,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"reports={len(per_report)} errors={len(errors)}")
    print("=== migration matrix ===")
    for k, v in sorted(matrix.items()):
        print(f"  {k[0]} -> {k[1]}: {v}")
    print(f"residual contradicted: {len(residual_contra)}  NO_MATCH: {len(unmatched)}")
    print(f"HEAD={run_identity['candidate_head_sha'][:12]} "
          f"baseline({run_identity['baseline_ref']})={str(run_identity['baseline_sha'])[:12]} "
          f"dirty={run_identity['worktree_dirty']}")
    # golden per-symbol summary
    for sym, reps in golden_reports.items():
        for rep in reps:
            contra = [e for e in rep["new_evs"] if e.get("status") == "contradicted"]
            print(f"GOLDEN {sym} {rep['file']}: new_contradicted={len(contra)}")


if __name__ == "__main__":
    main()
