# -*- coding: utf-8 -*-
"""DAV-1192 (1141-B1) 存量 replay 门：用产品代码中的 semantic audit 对
DAV-1191 同口径 615 条 adopted claims 离线重算，复现敏感性复算门值。

只读活库 mode=ro；无模型、无网络、不写库。预期门值：
- natural relaxed 缺口 250/582 = 43.0%
- frozen stress 缺口 17/33 = 51.5%
- bull/bear 缺口约 48.3% vs 31.9%
- 100% 与 80% preview 判定相同
- natural preview: adopt 332 / partial_threshold 4 / reject 246
- natural E-04-sensitive 25 claims，其中 21 条 E-04 为唯一缺口
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
from tradingagents.storage.compressed_json import (  # noqa: E402
    decode_result_data,
    result_data_select_expr,
)
from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
    SEM_PREVIEW_ADOPT,
    SEM_PREVIEW_PARTIAL,
    SEM_PREVIEW_REJECT,
    SEM_SUPPORT_NON_FACTUAL,
    SEM_SUPPORT_SUPPORTED,
    audit_claim_semantic_coverage,
)

DB = "file:/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db?mode=ro"
REPORT_FIELDS = [
    "market_report", "sentiment_report", "news_report", "fundamentals_report",
    "macro_report", "smart_money_report", "volume_price_report", "game_theory_report",
]


def main():
    from tradingagents.storage.compressed_json import decode_result_data
    con = sqlite3.connect(DB, uri=True)
    cur = con.cursor()
    # B-6b: json_extract paths resolve in Python after decode — compressed
    # BLOBs can't be json_extract'ed in SQL. result_data_select_expr keeps
    # legacy plaintext-only schemas working in the same query (B-6d).
    rd_expr = result_data_select_expr(con)
    rows = cur.execute(
        f"""
      SELECT id, symbol, trade_date, created_at,
        {rd_expr} AS rd,
        market_report, sentiment_report, news_report, fundamentals_report,
        macro_report, smart_money_report, volume_price_report, game_theory_report
      FROM reports WHERE status='completed'
        AND {rd_expr} IS NOT NULL
        ORDER BY id
        """
    ).fetchall()

    def _field(rd, *path):
        cur = rd
        for p in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur

    def _cdict(c):
        """Deterministic Counter print: keys sorted (B-6d byte-equality gate).

        ``dict(counter)`` iterates in insertion order, which follows the scan
        order; sorted keys make the report byte-stable across storage
        variants and across runs of the same DB.
        """
        return dict(sorted(c.items()))

    # B2/B3 冻结批识别（Phase A 同款：同 commit + 同分钟窗口 >=5 份）
    batches = defaultdict(list)
    decoded = {}
    for r in rows:
        d = decode_result_data(r[4])
        if not isinstance(d, dict):
            continue
        if not _field(d, "manager_verdict", "claim_evidence_summary"):
            continue
        decoded[r[0]] = (r, d)
        batches[(d.get("generated_by_commit_sha"), (r[3] or "")[:16])].append(r[0])
    frozen_rids = set()
    for (sha, minute), rids in batches.items():
        if sha and len(rids) >= 5 and minute >= "2026-09-20":
            frozen_rids.update(rids)

    out = []
    for r in rows:
        if r[0] not in decoded:
            continue
        r, d = decoded[r[0]]
        rid, sym, td, created = r[0], r[1], r[2], r[3]
        sha = d.get("generated_by_commit_sha")
        claims = _field(d, "investment_debate_state", "claims") or []
        summ = _field(d, "manager_verdict", "claim_evidence_summary") or {}
        adopted = set(_field(d, "manager_verdict", "adopted_claim_ids") or [])
        prov = _field(d, "market_data_context", "source_provenance") or {}
        blocked = {
            str(k) for k, v in (prov.items() if isinstance(prov, dict) else [])
            if isinstance(v, dict) and (
                str(v.get("provenance_status", "")).lower() in ("refused", "blocked")
                or str(v.get("status", "")).lower() in ("refused", "failed", "unavailable")
            )
        }
        fields = dict(zip(REPORT_FIELDS, r[5:]))
        fields = {k: v for k, v in fields.items() if v}
        cmap = {c.get("claim_id"): c for c in claims}
        for cid in adopted:
            cs = summ.get(cid) or {}
            c = cmap.get(cid, {})
            text = c.get("claim") or cs.get("claim") or ""
            if not text:
                continue
            verified = cs.get("verified_evidence") or c.get("verified_evidence") or []
            audit = audit_claim_semantic_coverage(
                text,
                verified_evidence=verified,
                report_fields=fields,
                blocked_sources=blocked,
                claim_id=cid,
            )
            out.append({
                "report_id": rid, "symbol": sym, "claim_id": cid, "claim": text,
                "stance": cs.get("stance"), "sha": sha,
                "decision_model": d.get("decision_model_version") or "unversioned",
                "evidence_contract": d.get("evidence_contract_version") or "unversioned",
                "frozen_batch": rid in frozen_rids,
                "semantic_coverage": audit["semantic_coverage"],
                "preview": audit["semantic_decision_preview"],
                "origin": audit["semantic_partial_origin_preview"],
                "hard_guards": audit["semantic_hard_guards"],
                "counts": audit["semantic_counts"],
                "audit": audit["proposition_audit"],
            })

    print(f"adopted claims replayed: {len(out)}")
    coh = {
        "natural": [r for r in out if not r["frozen_batch"]],
        "frozen": [r for r in out if r["frozen_batch"]],
    }
    for name, rs in coh.items():
        # DAV-1193：semantic_coverage 可能为 None（non_factual_only），视为缺口
        gaps = [r for r in rs if (r["semantic_coverage"] or 0.0) < 1.0]
        bs = Counter(r["stance"] for r in gaps)
        tot = Counter(r["stance"] for r in rs)
        print(f"{name}: gap {len(gaps)}/{len(rs)} ({len(gaps)/max(1,len(rs))*100:.1f}%) "
              f"bull {bs['bullish']}/{tot['bullish']} "
              f"({bs['bullish']/max(1,tot['bullish'])*100:.1f}%) "
              f"bear {bs['bearish']}/{tot['bearish']} "
              f"({bs['bearish']/max(1,tot['bearish'])*100:.1f}%)")
        pv = Counter(r["preview"] for r in rs)
        print(f"  preview: {_cdict(pv)}")
        # DAV-1193 B2 迁移矩阵：全部存量均为 legacy adopted，新 semantic
        # decision 分布即 adopt→X 迁移；按 cohort 与 stance/commit 分层
        mig = Counter(f"adopt->{r['preview']}" for r in rs)
        print(f"  migration adopt->*: {_cdict(mig)}")
        mig_stance = defaultdict(Counter)
        mig_sha = defaultdict(Counter)
        for r in rs:
            mig_stance[r["stance"]][r["preview"]] += 1
            mig_sha[(r["sha"] or "unknown")[:8]][r["preview"]] += 1
        for st, c in sorted(mig_stance.items()):
            print(f"    stance={st}: {_cdict(c)}")
        for sha, c in sorted(mig_sha.items()):
            print(f"    commit={sha}: {_cdict(c)}")
        mig_model = defaultdict(Counter)
        mig_contract = defaultdict(Counter)
        for r in rs:
            mig_model[r["decision_model"]][r["preview"]] += 1
            mig_contract[r["evidence_contract"]][r["preview"]] += 1
        for m, c in sorted(mig_model.items()):
            print(f"    model={m}: {_cdict(c)}")
        for ec, c in sorted(mig_contract.items()):
            print(f"    contract={ec}: {_cdict(c)}")
        # 80% 阈值对比：coverage>=0.8 → adopt（检查与 100% 是否同判定）
        def gate80(r):
            cov = r["semantic_coverage"]
            if cov is None:
                return "non_factual_only"
            return "adopt" if cov >= 0.8 else (
                "partial" if cov >= 0.67 else "reject")
        g80 = Counter(gate80(r) for r in rs)
        print(f"  thr=80% gate: {_cdict(g80)}")
        # E-04 sensitive
        e4 = [r for r in rs if r["hard_guards"]]
        uniq = 0
        for r in e4:
            unsup = [p for p in r["audit"]
                     if p["support_status"] not in (SEM_SUPPORT_SUPPORTED, SEM_SUPPORT_NON_FACTUAL)]
            if all(p["e04_sensitive"] for p in unsup):
                uniq += 1
        print(f"  E-04-sensitive claims: {len(e4)} (unique-gap {uniq})")

    # INV-2 锚点（B-6d: sort — `adopted` 是 set，跨进程哈希序随机，
    # 不排序则同一库两次运行打印序也不同，逐字节验收无从判定）
    for r in sorted(out, key=lambda x: (x["report_id"], x["claim_id"])):
        if r["report_id"].startswith("aa773648") or "已定价超6天" in r["claim"]:
            cov = r["semantic_coverage"]
            cov_txt = f"{cov:.0%}" if isinstance(cov, (int, float)) else "n/a"
            print(f"ANCHOR {r['report_id'][:8]} {r['claim_id']} sem={cov_txt} "
                  f"preview={r['preview']} claim={r['claim'][:50]}")


if __name__ == "__main__":
    main()
