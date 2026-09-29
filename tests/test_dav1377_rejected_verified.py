"""DAV-1377: a manager's rejection of verified facts is not a report-wide failure."""

import pytest

from tradingagents.agents.utils.decision_status import (
    evaluate_confirmation_state,
    status_from_manager_verdict,
)


def _verified(**overrides):
    summary = {
        "counts": {"total": 1, "verified": 1, "contradicted": 0, "source_unavailable": 0},
        "coverage": 1.0,
        "decision": "adopt",
        "semantic_decision": "adopt",
    }
    summary.update(overrides)
    return summary


def _manager(**overrides):
    verdict = {
        "direction": "看多", "winner": "bull", "position_pct": 50,
        "consistency_check_passed": True, "failed_checks": [],
        "adopted_claim_ids": ["OK"], "partially_adopted_claims": [],
        "rejected_claim_ids": ["RJ"],
        "direction_basis": {"status": "ledgered"},
    }
    verdict.update(overrides)
    return verdict


def test_semantic_partial_rejection_does_not_get_legacy_adopt_reason():
    # Legacy says adopt, but ledger's semantic bucket is partial.
    summary = {"OK": _verified(), "RJ": _verified(semantic_decision="partial_threshold")}
    state, codes = evaluate_confirmation_state(
        focus_claim_ids=["OK"], claim_evidence_summary=summary,
        adopted_claim_ids=["OK"], rejected_claim_ids=["RJ"],
    )
    assert state == "PARTIAL"
    assert "rejected_partial_claims:RJ" in codes
    assert not any(code.startswith("verdict_consistency_rejected_adopt:") for code in codes)
    status = status_from_manager_verdict(_manager(), focus_claim_ids=["OK"], claim_evidence_summary=summary)
    assert (status.analysis_status, status.trade_action) == ("VALID", "WAIT")


def test_non_factual_rejection_is_not_marked_verified():
    summary = {"OK": _verified(), "RJ": _verified(semantic_decision="non_factual_only")}
    status = status_from_manager_verdict(_manager(), focus_claim_ids=["OK"], claim_evidence_summary=summary)
    assert (status.analysis_status, status.trade_action, status.confirmation_state) == ("VALID", "BUY", "CONFIRMED")
    assert not any(code.startswith("verdict_consistency_rejected_adopt:") for code in status.reason_codes)


@pytest.mark.parametrize("pit_as_verification", [False, True])
def test_rejected_eligible_and_adopted_pit_still_hard_gates(pit_as_verification):
    summary = {"OK": _verified(), "RJ": _verified()}
    verification = None
    if pit_as_verification:
        verification = [{"claim_id": "OK", "status": "verified", "pit_failed": True}]
    else:
        summary["OK"]["pit_failed"] = True
    status = status_from_manager_verdict(
        _manager(), focus_claim_ids=["OK"],
        claim_evidence_summary=summary, claims_verification=verification,
    )
    assert (status.analysis_status, status.trade_action) == ("ABSTAIN", "NO_TRADE")
    assert "manager_consistency_hard_gate" in status.reason_codes
    assert "pit_failed_adopted_claims:OK" in status.reason_codes
    assert "verdict_consistency_rejected_adopt:RJ" in status.reason_codes


def test_rejected_eligible_and_unadjudicated_adopt_still_hard_gates():
    summary = {"OK": _verified(), "RJ": _verified(), "OMITTED": _verified()}
    status = status_from_manager_verdict(_manager(), focus_claim_ids=["OK"], claim_evidence_summary=summary)
    assert (status.analysis_status, status.trade_action) == ("ABSTAIN", "NO_TRADE")
    assert "unadjudicated_material_claims_adopt:OMITTED" in status.reason_codes
    assert "verdict_consistency_rejected_adopt:RJ" in status.reason_codes
