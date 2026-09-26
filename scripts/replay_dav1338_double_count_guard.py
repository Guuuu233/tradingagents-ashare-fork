# -*- coding: utf-8 -*-
"""DAV-1338 零 LLM 离线重放：候选 double_count_guard 对存量档位的重算。

只读活库 mode=ro；无模型、无网络、不写库。

口径：
- 语料 = 09-25 以来全部档位 + 全库 ABSTAIN 档位（DAV-1261 语料）。
- 每档：用存储的 claims / expectation_revision / relation_graph 先以
  tally_cluster_votes 重建守卫前的簇指标，把守卫前账本（stored adopted ∪
  旧审计 excluded_claim_ids，按 claims 原顺序）喂给候选
  apply_manager_double_count_guard，再 refresh_direction_basis 与
  status_from_manager_verdict 重算终态。
- 前后对比：守卫剔除条数、direction_basis.status、一致性失败项
  （stored failed_checks 中引用被剔除论点的条目计为「随账本修复消除」）、
  analysis_status / trade_action 翻转清单。
"""
import copy
import json
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, ".")
from tradingagents.agents.managers.research_manager import (  # noqa: E402
    apply_manager_double_count_guard,
)
from tradingagents.agents.utils.claim_cluster import (  # noqa: E402
    _normalize_stance,
    tally_cluster_votes,
)
from tradingagents.agents.utils.decision_status import (  # noqa: E402
    status_from_manager_verdict,
)
from tradingagents.agents.utils.evidence_verifier import (  # noqa: E402
    refresh_direction_basis,
)

DB = "file:/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db?mode=ro"
SINCE = "2026-09-25"
HORIZONS = ("short_term", "medium_term")
REPORT_FIELDS = [
    "market_report", "sentiment_report", "news_report", "fundamentals_report",
    "macro_report", "smart_money_report", "volume_price_report", "game_theory_report",
]


def _guard_excluded_ids(mv):
    audit = ((mv.get("double_count_guard_audit") or {})
             if isinstance(mv.get("double_count_guard_audit"), dict) else {})
    ids = list(audit.get("excluded_claim_ids") or [])
    for e in mv.get("excluded_evidence") or []:
        if isinstance(e, dict) and "double_count_guard" in str(e.get("reason") or ""):
            cid = e.get("claim_id")
            if cid and cid not in ids:
                ids.append(cid)
    return ids


def _same_dir_support(verdict, claims):
    """返回终态账本中支撑 winner 方向的同向已核实论点 id 列表。"""
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
        out.append((cid, stance, verified))
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
    n_tiers = n_corpus = 0

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
            n_corpus += 1

            ccm_stored = ids.get("claim_cluster_metrics") or {}
            old_audit = (ccm_stored.get("double_count_guard_audit")
                         or mv.get("double_count_guard_audit") or {})
            old_blocked = int(old_audit.get("blocked_duplicate_votes") or 0)
            old_excluded = list(dict.fromkeys(
                list(old_audit.get("excluded_claim_ids") or [])
                + _guard_excluded_ids(mv)))
            dist_before[old_blocked] += 1

            # 重建守卫前账本与簇指标
            claims_order = [c.get("claim_id") for c in claims if isinstance(c, dict)]
            adopted_old = list(mv.get("adopted_claim_ids") or [])
            pre_adopted = [cid for cid in claims_order
                           if cid in set(adopted_old) | set(old_excluded)]
            for cid in adopted_old + old_excluded:  # 兜底：不在 claims 里的保留原序
                if cid not in pre_adopted:
                    pre_adopted.append(cid)
            pre_excluded = [
                e for e in (mv.get("excluded_evidence") or [])
                if not (isinstance(e, dict)
                        and ("double_count_guard" in str(e.get("reason") or "")
                             or e.get("claim_id") in set(old_excluded)))
            ]
            reports = {k: t.get(k) for k in REPORT_FIELDS if t.get(k)}
            try:
                pre_metrics = tally_cluster_votes(
                    claims=claims,
                    reports=reports,
                    claims_verification=ids.get("evidence_verification"),
                    symbol=d.get("symbol") or sym,
                    trade_date=t.get("trade_date") or td,
                    horizon=t.get("horizon") or h.replace("_term", ""),
                    relation_graph=ids.get("evidence_relation_graph"),
                    relation_graph_status=ids.get("evidence_relation_status"),
                    relation_graph_reason=ids.get("evidence_relation_reason"),
                )
            except Exception:
                pre_metrics = dict(ids.get("claim_cluster_metrics") or {})

            verdict = copy.deepcopy(mv)
            verdict["adopted_claim_ids"] = pre_adopted
            verdict["excluded_evidence"] = pre_excluded
            verdict.pop("double_count_guard_audit", None)

            er = ids.get("expectation_revision") or mv.get("expectation_revision") or {}
            metrics_new, verdict, _ = apply_manager_double_count_guard(
                claim_cluster_metrics=pre_metrics,
                expectation_revisions=er,
                claims=claims,
                manager_verdict=verdict,
            )
            new_audit = metrics_new.get("double_count_guard_audit") or {}
            new_blocked = int(new_audit.get("blocked_duplicate_votes") or 0)
            dist_after[new_blocked] += 1

            basis_before[str((mv.get("direction_basis") or {}).get("status"))] += 1
            refresh_direction_basis(verdict, claims=claims)
            basis_after[str((verdict.get("direction_basis") or {}).get("status"))] += 1

            old_failed = list(mv.get("failed_checks") or [])
            checks_before += len(old_failed)
            # 一致性失败项重算口径：旧失败项中引用「旧守卫剔除、候选恢复采纳」
            # 论点的属于过期账本失败，候选账本下不再产生；其余失败项保留。
            restored_ids = set(old_excluded) - set(new_audit.get("excluded_claim_ids") or [])
            still_failed = [f for f in old_failed
                            if not any(str(cid) in str(f) for cid in restored_ids)]
            checks_after += len(still_failed)
            excluded_new = set(new_audit.get("excluded_claim_ids") or [])

            st = status_from_manager_verdict(
                verdict,
                investment_debate_state={**ids, "claim_cluster_metrics": metrics_new},
                claims_verification=ids.get("evidence_verification"),
                claim_evidence_summary=mv.get("claim_evidence_summary"),
                focus_claim_ids=ids.get("focus_claim_ids"),
                unresolved_claim_ids=ids.get("unresolved_claim_ids"),
                claims=claims,
                market_data_context=t.get("market_data_context"),
            )
            status_new = st.analysis_status
            action_old = mv.get("trade_action") or t.get("trade_action")
            action_new = st.trade_action
            n_tiers += 1

            if (status_old, action_old) != (status_new, action_new):
                flips.append({
                    "report_id": rid, "symbol": sym, "trade_date": td,
                    "horizon": h, "created_at": created,
                    "before": f"{status_old}/{action_old}",
                    "after": f"{status_new}/{action_new}",
                    "blocked_before": old_blocked, "blocked_after": new_blocked,
                    "basis_before": (mv.get("direction_basis") or {}).get("status"),
                    "basis_after": (verdict.get("direction_basis") or {}).get("status"),
                    "adopted_before": adopted_old,
                    "adopted_after": verdict.get("adopted_claim_ids"),
                    "excluded_new": sorted(excluded_new),
                    "same_dir_support": _same_dir_support(verdict, claims),
                    "reason_codes": list(st.reason_codes or []),
                })

    print(f"== DAV-1338 replay ==  tiers evaluated: {n_tiers} (corpus rows {n_corpus})")
    print("\n-- 守卫剔除条数分布 (blocked -> tier count) --")
    print("before:", dict(sorted(dist_before.items())))
    print("after :", dict(sorted(dist_after.items())))
    print("\n-- direction_basis.status 分布 --")
    print("before:", dict(basis_before.most_common()))
    print("after :", dict(basis_after.most_common()))
    print("\n-- 一致性失败项 --")
    print(f"before total: {checks_before} | after(剔除过期账本项后): {checks_after}")
    print("\n-- analysis_status/trade_action 翻转清单 --")
    if not flips:
        print("(无翻转)")
    for f in flips:
        print(json.dumps(f, ensure_ascii=False))


if __name__ == "__main__":
    main()
