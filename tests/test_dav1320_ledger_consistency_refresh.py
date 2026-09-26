"""DAV-1320 — 研究经理一致性检查改用守卫裁剪后的最终账本（零 LLM）。

缺陷：extract_and_validate_manager_verdict 在守卫裁剪前的原始账本上计算
consistency_check_passed / failed_checks；apply_manager_double_count_guard
之后把重复计入的论点剔除进 excluded_evidence，指向已剔除论点的失败项过期，
导致 ABSTAIN 误拦。

修复：守卫裁剪后用 refresh_ledger_consistency_checks 在最终账本上重算账本
相关检查（Check 6 存在性 + Check 7 coverage/consistency）并替换旧结果；
与账本无关的检查保持原样。
"""
from __future__ import annotations

from tradingagents.agents.utils.evidence_verifier import (
    DECISION_ADOPT,
    DECISION_PARTIAL,
    refresh_ledger_consistency_checks,
)


def _summary_mixed(cid: str) -> dict:
    """coverage<1 且 decision=partial → adopt 时触发「含未核实混合证据」失败。"""
    return {
        cid: {
            "counts": {"verified": 2, "total": 3, "contradicted": 0, "source_unavailable": 0},
            "coverage": 2 / 3 + 0.01,
            "decision": DECISION_PARTIAL,
            "is_observation_or_hypothesis": False,
            "semantic_decision": None,
            "excluded_evidence": [],
        }
    }


def _summary_clean(cid: str) -> dict:
    return {
        cid: {
            "counts": {"verified": 3, "total": 3, "contradicted": 0, "source_unavailable": 0},
            "coverage": 1.0,
            "decision": DECISION_ADOPT,
            "is_observation_or_hypothesis": False,
            "semantic_decision": None,
            "excluded_evidence": [],
        }
    }


def _claims(*cids: str) -> list[dict]:
    return [{"claim_id": cid, "claim_text": f"claim {cid}"} for cid in cids]


def _verdict(adopted, partial=None, rejected=None, summary=None, failed=None):
    return {
        "adopted_claim_ids": list(adopted),
        "partially_adopted_claims": list(partial or []),
        "rejected_claim_ids": list(rejected or []),
        "excluded_evidence": [],
        "claim_evidence_summary": summary or {},
        "consistency_check_passed": not failed,
        "failed_checks": list(failed or []),
    }


class TestRefreshLedgerConsistencyChecks:
    def test_stale_only_failures_cleared(self):
        """过期项（所指论点已被守卫剔除出最终账本）→ 重算后通过。"""
        claims = _claims("INV-1", "INV-3")
        summary = {**_summary_clean("INV-1"), **_summary_mixed("INV-3")}
        # 守卫前账本：adopted=[INV-1, INV-3]，INV-3 混合证据被全额采纳 → 失败
        pre_failed = [
            "裁决全额采纳了含未核实混合证据的 claim: INV-3 "
            f"(coverage={2/3+0.01:.1%})，混合证据仅允许记录于 partially_adopted_claims 并剔除未验证项"
        ]
        verdict = _verdict(
            adopted=["INV-1"],  # 守卫后 INV-3 已被剔除
            summary=summary,
            failed=pre_failed,
        )
        refresh_ledger_consistency_checks(
            verdict,
            pre_guard_adopted_claim_ids=["INV-1", "INV-3"],
            pre_guard_partially_adopted_claims=[],
            pre_guard_rejected_claim_ids=[],
            claims=claims,
            raw_response="多头胜。理由充分。",
        )
        assert verdict["failed_checks"] == []
        assert verdict["consistency_check_passed"] is True

    def test_real_violation_still_blocked(self):
        """过期项 + 真实违规并存 → 过期项消失、真实违规照拦。"""
        claims = _claims("INV-1", "INV-3")
        summary = {**_summary_mixed("INV-1"), **_summary_mixed("INV-3")}
        cov_txt = f"{2/3+0.01:.1%}"
        pre_failed = [
            f"裁决全额采纳了含未核实混合证据的 claim: INV-1 (coverage={cov_txt})，混合证据仅允许记录于 partially_adopted_claims 并剔除未验证项",
            f"裁决全额采纳了含未核实混合证据的 claim: INV-3 (coverage={cov_txt})，混合证据仅允许记录于 partially_adopted_claims 并剔除未验证项",
        ]
        verdict = _verdict(
            adopted=["INV-1"],  # INV-3 被守卫剔除，INV-1 仍在账本且仍违规
            summary=summary,
            failed=pre_failed,
        )
        refresh_ledger_consistency_checks(
            verdict,
            pre_guard_adopted_claim_ids=["INV-1", "INV-3"],
            pre_guard_partially_adopted_claims=[],
            pre_guard_rejected_claim_ids=[],
            claims=claims,
            raw_response="多头胜。",
        )
        assert verdict["consistency_check_passed"] is False
        assert verdict["failed_checks"] == [
            f"裁决全额采纳了含未核实混合证据的 claim: INV-1 (coverage={cov_txt})，混合证据仅允许记录于 partially_adopted_claims 并剔除未验证项"
        ]

    def test_non_ledger_failures_preserved(self):
        """与账本无关的失败项（如正文与机读块矛盾）在重算后保留。"""
        claims = _claims("INV-1", "INV-3")
        summary = {**_summary_clean("INV-1"), **_summary_mixed("INV-3")}
        cov_txt = f"{2/3+0.01:.1%}"
        non_ledger = "正文明确判定空头胜，但机读块为多头胜(bull)，正文与机读裁决严重矛盾"
        pre_failed = [
            non_ledger,
            f"裁决全额采纳了含未核实混合证据的 claim: INV-3 (coverage={cov_txt})，混合证据仅允许记录于 partially_adopted_claims 并剔除未验证项",
        ]
        verdict = _verdict(
            adopted=["INV-1"],
            summary=summary,
            failed=pre_failed,
        )
        refresh_ledger_consistency_checks(
            verdict,
            pre_guard_adopted_claim_ids=["INV-1", "INV-3"],
            pre_guard_partially_adopted_claims=[],
            pre_guard_rejected_claim_ids=[],
            claims=claims,
            raw_response="多头胜。",
        )
        assert verdict["consistency_check_passed"] is False
        assert verdict["failed_checks"] == [non_ledger]

    def test_idempotent_when_guard_removed_nothing(self):
        """守卫未剔除任何论点时，重算结果与原结果完全一致（幂等）。"""
        claims = _claims("INV-1")
        summary = _summary_clean("INV-1")
        verdict = _verdict(adopted=["INV-1"], summary=summary, failed=[])
        refresh_ledger_consistency_checks(
            verdict,
            pre_guard_adopted_claim_ids=["INV-1"],
            pre_guard_partially_adopted_claims=[],
            pre_guard_rejected_claim_ids=[],
            claims=claims,
            raw_response="多头胜。",
        )
        assert verdict["failed_checks"] == []
        assert verdict["consistency_check_passed"] is True
