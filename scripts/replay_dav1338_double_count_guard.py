# -*- coding: utf-8 -*-
"""DAV-1338 零 LLM 离线重放 v2（返工口径）：候选 double_count_guard 对存量档位重算。

只读活库 mode=ro；无模型、无网络、不写库。

口径（按总控 09-26 打回要求修正）：
- 守卫前账本一律取研究经理原始输出 MANAGER_VERDICT 机读块：
  对 investment_debate_state.judge_decision（缺失时退回该档 investment_plan）
  跑产品函数 extract_and_validate_manager_verdict，原汁原味复现
  adopted / partially_adopted / rejected / excluded_evidence 与提取期一致性检查。
  绝不用「落库 adopted ∪ 旧审计 excluded」兜底——那会误把经理否决/未采纳
  论点塞回 adopted。
- 完整链路与产品代码同序：extract → （claim_evidence_summary 取落库值）
  → 候选 apply_manager_double_count_guard → refresh_direction_basis
  → refresh_evidence_basis → validate_manager_expectation_revision_consumption
  → status_from_manager_verdict。
- 语料 = 09-25 以来全部档位 + 全库 ABSTAIN 档（DAV-1261 语料）；
  MANAGER_VERDICT 解析失败/缺失的档位单列，不计入分布。
- 翻转逐例列出：原始 adopted、守卫剔除、最终 adopted、同向已核实论点。
"""
import copy
import json
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, ".")
from tradingagents.agents.managers.research_manager import (  # noqa: E402
    apply_manager_double_count_guard,
    validate_manager_expectation_revision_consumption,
)
from tradingagents.agents.utils.claim_cluster import (  # noqa: E402
    _normalize_stance,
    tally_cluster_votes,
)
from tradingagents.agents.utils.decision_status import (  # noqa: E402
    status_from_manager_verdict,
)
from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
    extract_and_validate_manager_verdict,
    refresh_direction_basis,
    refresh_evidence_basis,
)

DB = "file:/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db?mode=ro"
SINCE = "2026-09-25"
HORIZONS = ("short_term", "medium_term")
SEVEN_REPORT_FIELDS = [
    "macro_report", "market_report", "sentiment_report", "news_report",
    "fundamentals_report", "smart_money_report", "volume_price_report",
]


def _same_dir_support(verdict, claims):
    """终态账本中支撑 winner 方向的同向已核实论点 (claim_id, stance, verified)。"""
    winner = str(verdict.get("winner") or "").lower()
    adopted = set(verdict.get("adopted_claim_ids") or [])
    summary = verdict.get("claim_evidence_summary") or {}
    out = []
    for c in claims or []:
        cid = c.get("claim_id")
        if cid not in adopted:
            continue
        stance = _normalize_stance(c.get("stance"), c.get("speaker") or c.get("speaker_key"))
        if winner == "bull" and stance != "bull":
            continue
        if winner == "bear" and stance != "bear":
            continue
        info = summary.get(cid) or {}
        verified = ((info.get("counts") or {}).get("verified") or 0) > 0
        out.append([cid, stance, verified])
    return out


def main():
    con = sqlite3.connect(DB, uri=True)
    rows = con.execute(
        "SELECT id, symbol, trade_date, created_at, result_data "
        "FROM reports WHERE status='completed' AND result_data IS NOT NULL"
    ).fetchall()

    dist_before, dist_after = Counter(), Counter()
    basis_before, basis_after = Counter(), Counter()
    checks_before = checks_after = 0
    flips = []
    parse_failures = []
    n_tiers = 0

    for rid, sym, td, created, rd in rows:
        try:
            d = json.loads(rd)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        for h in HORIZONS:
            t = d.get(h)
            if not isinstance(t, dict):
                continue
            ids = t.get("investment_debate_state") or {}
            mv = t.get("manager_verdict") or {}
            claims = ids.get("claims") or []
            if not isinstance(ids, dict) or not isinstance(mv, dict) or not claims:
                continue
            status_old = mv.get("analysis_status") or t.get("analysis_status")
            in_scope = (created or "") >= SINCE or status_old == "ABSTAIN"
            if not in_scope:
                continue

            # ── 守卫前账本：原始 MANAGER_VERDICT 机读块 ──
            raw = ids.get("judge_decision")
            source = "judge_decision"
            if not isinstance(raw, str) or "MANAGER_VERDICT" not in raw:
                alt = t.get("investment_plan")
                if isinstance(alt, str) and "MANAGER_VERDICT" in alt:
                    raw, source = alt, "investment_plan"
            if not isinstance(raw, str) or "MANAGER_VERDICT" not in raw:
                parse_failures.append({
                    "report_id": rid, "symbol": sym, "trade_date": td,
                    "horizon": h, "reason": "no MANAGER_VERDICT block",
                })
                continue
            try:
                verdict = extract_and_validate_manager_verdict(
                    raw_response=raw,
                    claims_verification=ids.get("evidence_verification"),
                    claims=claims,
                    challenges=ids.get("challenges"),
                    challenges_verification=ids.get("challenge_verification"),
                    market_data_context=t.get("market_data_context"),
                    seven_reports={k: t.get(k) for k in SEVEN_REPORT_FIELDS if t.get(k)},
                )
            except Exception as e:  # noqa: BLE001
                parse_failures.append({
                    "report_id": rid, "symbol": sym, "trade_date": td,
                    "horizon": h, "reason": f"extract error: {e!r}",
                })
                continue
            orig_adopted = list(verdict.get("adopted_claim_ids") or [])
            if not orig_adopted and any(
                    "未提取到有效的研究总监结构化裁决机读块" in str(f)
                    for f in (verdict.get("failed_checks") or [])):
                parse_failures.append({
                    "report_id": rid, "symbol": sym, "trade_date": td,
                    "horizon": h, "reason": "MANAGER_VERDICT payload empty",
                })
                continue
            n_tiers += 1

            # ── before（落库值）──
            ccm_stored = ids.get("claim_cluster_metrics") or {}
            old_audit = (ccm_stored.get("double_count_guard_audit")
                         or mv.get("double_count_guard_audit") or {})
            old_blocked = int(old_audit.get("blocked_duplicate_votes") or 0)
            dist_before[old_blocked] += 1
            basis_before[str((mv.get("direction_basis") or {}).get("status"))] += 1
            old_failed = list(mv.get("failed_checks") or [])
            checks_before += len(old_failed)
            action_old = mv.get("trade_action") or t.get("trade_action")

            # ── 守卫前簇指标重建（与运行时同一函数、同输入）──
            reports7 = {k: t.get(k) for k in SEVEN_REPORT_FIELDS if t.get(k)}
            try:
                pre_metrics = tally_cluster_votes(
                    claims=claims,
                    reports=reports7,
                    claims_verification=ids.get("evidence_verification"),
                    symbol=d.get("symbol") or sym,
                    trade_date=t.get("trade_date") or td,
                    horizon=t.get("horizon") or h.replace("_term", ""),
                    relation_graph=ids.get("evidence_relation_graph"),
                    relation_graph_status=ids.get("evidence_relation_status"),
                    relation_graph_reason=ids.get("evidence_relation_reason"),
                )
            except Exception:
                pre_metrics = dict(ccm_stored)

            # ── 候选链路（与产品代码同序）──
            verdict["claim_evidence_summary"] = (
                verdict.get("claim_evidence_summary")
                or mv.get("claim_evidence_summary") or {})
            # 前置门控终态（nested decision_status：INVALID/DATA_ERROR/ABSTAIN
            # 优先于经理账本路径）从落库原样保留，与产品语义一致。
            if mv.get("decision_status"):
                verdict["decision_status"] = copy.deepcopy(mv["decision_status"])
            metrics_new, verdict, _ = apply_manager_double_count_guard(
                claim_cluster_metrics=pre_metrics,
                expectation_revisions=(ids.get("expectation_revision")
                                       or mv.get("expectation_revision") or {}),
                claims=claims,
                manager_verdict=verdict,
            )
            new_audit = metrics_new.get("double_count_guard_audit") or {}
            new_blocked = int(new_audit.get("blocked_duplicate_votes") or 0)
            dist_after[new_blocked] += 1

            refresh_direction_basis(verdict, claims=claims)
            refresh_evidence_basis(verdict)
            basis_after[str((verdict.get("direction_basis") or {}).get("status"))] += 1

            er_ok, er_viol = validate_manager_expectation_revision_consumption(
                manager_verdict=verdict,
                raw_response=raw,
                expectation_revisions=(ids.get("expectation_revision")
                                       or mv.get("expectation_revision") or {}),
                claims=claims,
                seven_reports=reports7,
            )
            if not er_ok:
                verdict["consistency_check_passed"] = False
                verdict.setdefault("failed_checks", [])
                verdict["failed_checks"].extend(er_viol)
            checks_after += len(verdict.get("failed_checks") or [])

            st = status_from_manager_verdict(
                verdict,
                investment_debate_state={**ids, "claim_cluster_metrics": metrics_new},
                claims_verification=ids.get("evidence_verification"),
                claim_evidence_summary=verdict.get("claim_evidence_summary"),
                focus_claim_ids=ids.get("focus_claim_ids"),
                unresolved_claim_ids=ids.get("unresolved_claim_ids"),
                claims=claims,
                market_data_context=t.get("market_data_context"),
            )
            status_new = st.analysis_status
            action_new = st.trade_action

            if (status_old, action_old) != (status_new, action_new):
                flips.append({
                    "report_id": rid, "symbol": sym, "trade_date": td,
                    "horizon": h, "created_at": created,
                    "ledger_source": source,
                    "before": f"{status_old}/{action_old}",
                    "after": f"{status_new}/{action_new}",
                    "orig_adopted": orig_adopted,
                    "orig_partial": verdict.get("partially_adopted_claims"),
                    "orig_rejected": verdict.get("rejected_claim_ids"),
                    "guard_excluded": sorted(new_audit.get("excluded_claim_ids") or []),
                    "final_adopted": verdict.get("adopted_claim_ids"),
                    "blocked_before": old_blocked, "blocked_after": new_blocked,
                    "basis_before": (mv.get("direction_basis") or {}).get("status"),
                    "basis_after": (verdict.get("direction_basis") or {}).get("status"),
                    "failed_before": old_failed,
                    "failed_after": list(verdict.get("failed_checks") or []),
                    "same_dir_support": _same_dir_support(verdict, claims),
                    "reason_codes": list(st.reason_codes or []),
                })

    print(f"== DAV-1338 replay v2 ==  tiers evaluated: {n_tiers} "
          f"| MANAGER_VERDICT parse failures: {len(parse_failures)}")
    print("\n-- 守卫剔除条数分布 (blocked -> tier count) --")
    print("before:", dict(sorted(dist_before.items())))
    print("after :", dict(sorted(dist_after.items())))
    print("\n-- direction_basis.status 分布 --")
    print("before:", dict(basis_before.most_common()))
    print("after :", dict(basis_after.most_common()))
    print("\n-- 一致性失败项 --")
    print(f"before total: {checks_before} | after: {checks_after}")
    print("\n-- MANAGER_VERDICT 解析失败档位（单列，未计入分布）--")
    for p in parse_failures:
        print(json.dumps(p, ensure_ascii=False))
    print("\n-- analysis_status/trade_action 翻转清单 --")
    if not flips:
        print("(无翻转)")
    for f in flips:
        print(json.dumps(f, ensure_ascii=False))


if __name__ == "__main__":
    main()
