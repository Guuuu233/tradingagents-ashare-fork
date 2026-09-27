# -*- coding: utf-8 -*-
"""DAV-1351 零 LLM 离线重放：研究经理硬门三类账本归类纠正前后对比。

只读活库 mode=ro；无模型、无网络、不写库。

口径与 DAV-1338/1343 重放完全一致（总控认可口径，勿退回旧口径）：
- 守卫前账本一律取研究经理原始输出 MANAGER_VERDICT 机读块：
  对 investment_debate_state.judge_decision（缺失时退回 investment_plan）
  跑 extract_and_validate_manager_verdict 复现原始裁决账本；解析失败单列。
- 链路与产品同序：extract →（claim_evidence_summary 取落库值兜底）→
  apply_manager_double_count_guard → refresh_direction_basis →
  refresh_evidence_basis → validate_manager_expectation_revision_consumption
  → status_from_manager_verdict。
- 语料 = 09-25 以来全部档位 + 全库 ABSTAIN 档。

用法（锁定解释器，基线/候选各跑一次后逐档对账）：

    env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
        scripts/replay_dav1351_ledger_normalization.py > work/dav1351/replay_<tag>.jsonl \
        2> work/dav1351/replay_<tag>.stderr.log

每档一行 JSONL：stored/recomputed(status、action、direction_basis)、
ledger_normalizations（三类纠正审计记录）、failed_checks、
adopted/partial/rejected/basis_from_rejected 最终账本、excluded_evidence。
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
            # nested decision_status 只保留真正先于经理账本链路的终态
            # （INVALID_RUN / DATA_ERROR）。落库 ABSTAIN/VALID 本身是
            # status_from_manager_verdict 对旧账本算出的终态——原样回填会让
            # 「nested ABSTAIN wins」分支短路重算，永远看不到翻转。
            _nested = mv.get("decision_status")
            if isinstance(_nested, dict) and _nested.get("analysis_status") in {
                "INVALID_RUN", "DATA_ERROR",
            }:
                verdict["decision_status"] = copy.deepcopy(_nested)
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

            rec = {
                "report_id": rid, "symbol": sym, "trade_date": td,
                "horizon": h, "created_at": created,
                "ledger_source": source,
                "stored": {"status": status_old,
                           "action": mv.get("trade_action") or t.get("trade_action")},
                "recomputed": {
                    "status": st.analysis_status,
                    "action": st.trade_action,
                    "confirmation": st.confirmation_state,
                    "reason_codes": list(st.reason_codes or []),
                    "consistency_check_passed": verdict.get("consistency_check_passed"),
                    "failed_checks": list(verdict.get("failed_checks") or []),
                    "direction_basis": (verdict.get("direction_basis") or {}).get("status"),
                    "ledger_normalizations": list(
                        verdict.get("ledger_normalizations") or []),
                    "adopted": list(verdict.get("adopted_claim_ids") or []),
                    "partial": list(verdict.get("partially_adopted_claims") or []),
                    "rejected": list(verdict.get("rejected_claim_ids") or []),
                    "basis_from_rejected": list(
                        verdict.get("basis_from_rejected_claim_ids") or []),
                    "evidence_basis": verdict.get("evidence_basis"),
                },
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    sys.stderr.write(
        f"tiers evaluated: {n_tiers} | MANAGER_VERDICT parse failures: {len(parse_failures)}\n")
    for p in parse_failures:
        sys.stderr.write(json.dumps(p, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
