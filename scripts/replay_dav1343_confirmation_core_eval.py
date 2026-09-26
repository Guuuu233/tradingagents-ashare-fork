# -*- coding: utf-8 -*-
"""DAV-1343 零 LLM 离线重放：确认规则 core_eval 排除「经理已否决且核验非 adopt」焦点论点。

只读活库 mode=ro；无模型、无网络、不写库。

口径与 DAV-1338 重放 v2 完全一致（总控认可的正确口径）：
- 守卫前账本一律取研究经理原始输出 MANAGER_VERDICT 机读块：
  对 investment_debate_state.judge_decision（缺失时退回 investment_plan）
  跑 extract_and_validate_manager_verdict 复现原始裁决账本。
- 链路与产品同序：extract →（claim_evidence_summary 取落库值）→
  apply_manager_double_count_guard → refresh_direction_basis →
  refresh_evidence_basis → validate_manager_expectation_revision_consumption
  → status_from_manager_verdict。
- 语料 = 09-25 以来全部档位 + 全库 ABSTAIN 档；MANAGER_VERDICT 解析
  失败/缺失的档位单列。

输出：每档一行 JSONL，含落库（现行生产）终态、本代码重算终态、
reason_codes、以及每个焦点论点的经理裁决归属与核验结论——
分别在主干基线（1338 单独上线）与候选代码上运行后做逐档对账。
"""
import json
import sqlite3
import sys
import copy

sys.path.insert(0, ".")
from tradingagents.agents.managers.research_manager import (  # noqa: E402
    apply_manager_double_count_guard,
    validate_manager_expectation_revision_consumption,
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


def _mgr_bucket(cid, verdict):
    if cid in set(verdict.get("adopted_claim_ids") or []):
        return "adopted"
    if cid in set(verdict.get("partially_adopted_claims") or []):
        return "partial"
    if cid in set(verdict.get("rejected_claim_ids") or []):
        return "rejected"
    return "unadjudicated"


def main():
    con = sqlite3.connect(DB, uri=True)
    rows = con.execute(
        "SELECT id, symbol, trade_date, created_at, result_data "
        "FROM reports WHERE status='completed' AND result_data IS NOT NULL"
    ).fetchall()

    out = sys.stdout
    n_tiers = 0
    parse_failures = []

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

            raw = ids.get("judge_decision")
            source = "judge_decision"
            if not isinstance(raw, str) or "MANAGER_VERDICT" not in raw:
                alt = t.get("investment_plan")
                if isinstance(alt, str) and "MANAGER_VERDICT" in alt:
                    raw, source = alt, "investment_plan"
            if not isinstance(raw, str) or "MANAGER_VERDICT" not in raw:
                parse_failures.append({"report_id": rid, "symbol": sym,
                                       "trade_date": td, "horizon": h,
                                       "reason": "no MANAGER_VERDICT block"})
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
                parse_failures.append({"report_id": rid, "symbol": sym,
                                       "trade_date": td, "horizon": h,
                                       "reason": f"extract error: {e!r}"})
                continue
            orig_adopted = list(verdict.get("adopted_claim_ids") or [])
            if not orig_adopted and any(
                    "未提取到有效的研究总监结构化裁决机读块" in str(f)
                    for f in (verdict.get("failed_checks") or [])):
                parse_failures.append({"report_id": rid, "symbol": sym,
                                       "trade_date": td, "horizon": h,
                                       "reason": "MANAGER_VERDICT payload empty"})
                continue
            n_tiers += 1

            ccm_stored = ids.get("claim_cluster_metrics") or {}
            reports7 = {k: t.get(k) for k in SEVEN_REPORT_FIELDS if t.get(k)}
            from tradingagents.agents.utils.claim_cluster import tally_cluster_votes
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

            verdict["claim_evidence_summary"] = (
                verdict.get("claim_evidence_summary")
                or mv.get("claim_evidence_summary") or {})
            if mv.get("decision_status"):
                verdict["decision_status"] = copy.deepcopy(mv["decision_status"])
            metrics_new, verdict, _ = apply_manager_double_count_guard(
                claim_cluster_metrics=pre_metrics,
                expectation_revisions=(ids.get("expectation_revision")
                                       or mv.get("expectation_revision") or {}),
                claims=claims,
                manager_verdict=verdict,
            )
            refresh_direction_basis(verdict, claims=claims)
            refresh_evidence_basis(verdict)
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

            summary = verdict.get("claim_evidence_summary") or {}
            focus = []
            for cid in (ids.get("focus_claim_ids") or ids.get("unresolved_claim_ids") or []):
                cid = str(cid).strip()
                if not cid:
                    continue
                sm = summary.get(cid) or {}
                focus.append({
                    "claim_id": cid,
                    "mgr": _mgr_bucket(cid, verdict),
                    "decision": sm.get("decision"),
                    "semantic_decision": sm.get("semantic_decision"),
                    "counts": sm.get("counts"),
                    "is_fatal": sm.get("is_fatal"),
                })

            rec = {
                "report_id": rid, "symbol": sym, "trade_date": td,
                "horizon": h, "created_at": created,
                "ledger_source": source,
                "stored": {"status": status_old,
                           "action": mv.get("trade_action") or t.get("trade_action")},
                "recomputed": {"status": st.analysis_status,
                               "action": st.trade_action,
                               "confirmation": st.confirmation_state,
                               "reason_codes": list(st.reason_codes or [])},
                "focus_claims": focus,
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    sys.stderr.write(
        f"tiers evaluated: {n_tiers} | MANAGER_VERDICT parse failures: {len(parse_failures)}\n")
    for p in parse_failures:
        sys.stderr.write(json.dumps(p, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
