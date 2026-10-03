"""DAV-1440: unsupported directions use the existing ABSTAIN hard-gate path."""
from copy import deepcopy

import pytest

from tradingagents.agents.utils.decision_status import (
    apply_decision_status_to_result,
    status_from_manager_verdict,
    status_from_risk_verdict,
)


def _fixture(direction="看多", adopted=None, partial=None, basis=None):
    claims = [{"claim_id": "SAME", "stance": "bullish" if direction == "看多" else "bearish"},
              {"claim_id": "OPPOSITE", "stance": "bearish" if direction == "看多" else "bullish"}]
    summary = {c["claim_id"]: {"decision": "adopt", "semantic_decision": "adopt",
                              "counts": {"total": 1, "verified": 1}, "coverage": 1.0,
                              "is_fatal": False, "stance": c["stance"]} for c in claims}
    manager = {"direction": direction, "winner": "bull" if direction == "看多" else "bear",
               "consistency_check_passed": True, "adopted_claim_ids": adopted or [],
               "partially_adopted_claims": partial or [], "rejected_claim_ids": [],
               "direction_basis": basis, "confidence": 75, "probability": 0.7}
    kwargs = {"claims": claims, "claim_evidence_summary": summary,
              "focus_claim_ids": list(adopted or []), "unresolved_claim_ids": []}
    # No unadjudicated/fatal claims may accidentally satisfy an older hard gate.
    used = set(adopted or []) | set(partial or [])
    kwargs["claims"] = [c for c in claims if c["claim_id"] in used]
    kwargs["claim_evidence_summary"] = {cid: s for cid, s in summary.items() if cid in used}
    return manager, kwargs


def _assert_unsupported_abstain(result):
    assert (result.analysis_status, result.direction, result.trade_action) == ("ABSTAIN", "N/A", "NO_TRADE")
    assert result.risk_status == "BLOCKED"
    assert result.confirmation_state == "UNRESOLVED"
    assert result.confidence is None and result.probability is None
    assert "manager_consistency_hard_gate" in result.reason_codes
    assert "direction_without_adjudicated_support" in result.reason_codes


@pytest.mark.parametrize("direction", ["看多", "看空"])
@pytest.mark.parametrize("adopted,partial", [([], []), (["OPPOSITE"], []),
                                           ([], ["OPPOSITE"]), (["OPPOSITE"], ["SAME"])])
def test_empty_same_direction_basis_abstains_even_with_adopted_or_partial(direction, adopted, partial):
    manager, kwargs = _fixture(direction, adopted, partial,
                               {"status": "unledgered", "same_direction_claims": []})
    before = deepcopy((manager, kwargs))
    _assert_unsupported_abstain(status_from_manager_verdict(manager, **kwargs))
    assert (manager, kwargs) == before


def test_no_adjudicated_support_alone_without_basis_abstains():
    manager, kwargs = _fixture()
    result = status_from_manager_verdict(manager, **kwargs)
    _assert_unsupported_abstain(result)
    assert "no_adjudicated_support" in result.reason_codes


@pytest.mark.parametrize("code_source", ["manager", "nested"])
def test_existing_no_support_reason_alone_dominates_even_a_ledgered_basis(code_source):
    manager, kwargs = _fixture(adopted=["SAME"],
                               basis={"status": "ledgered", "same_direction_claims": [{"claim_id": "SAME", "source": "adopted"}]})
    if code_source == "manager":
        manager["reason_codes"] = ["no_adjudicated_support"]
    else:
        manager["decision_status"] = {"analysis_status": "VALID", "direction": "BULL",
                                      "trade_action": "WAIT", "reason_codes": ["no_adjudicated_support"]}
    result = status_from_manager_verdict(manager, **kwargs)
    _assert_unsupported_abstain(result)
    assert "no_adjudicated_support" in result.reason_codes


@pytest.mark.parametrize("direction,expected", [("看多", "BUY"), ("看空", "SELL")])
def test_same_direction_adopted_positive_is_unchanged(direction, expected):
    manager, kwargs = _fixture(direction, ["SAME"], [],
                               {"status": "ledgered", "same_direction_claims": [{"claim_id": "SAME", "source": "adopted"}]})
    result = status_from_manager_verdict(manager, **kwargs)
    assert (result.analysis_status, result.trade_action, result.confirmation_state) == ("VALID", expected, "CONFIRMED")
    assert (result.confidence, result.probability) == (75, 0.7)
    assert "direction_without_adjudicated_support" not in result.reason_codes


@pytest.mark.parametrize("basis", [None, {}, {"status": "unledgered"},
                                    {"status": "unledgered", "same_direction_claims": None},
                                    {"status": "unknown", "same_direction_claims": []},
                                    {"status": "unledgered", "same_direction_claims": ["SAME"]}])
def test_missing_null_unknown_or_nonempty_basis_does_not_expand_new_gate(basis):
    manager, kwargs = _fixture(adopted=["SAME"], basis=basis)
    result = status_from_manager_verdict(manager, **kwargs)
    assert result.analysis_status == "VALID"
    assert "direction_without_adjudicated_support" not in result.reason_codes


def test_real_same_direction_partial_only_is_not_the_empty_basis_condition():
    manager, kwargs = _fixture(partial=["SAME"],
                               basis={"status": "partial_only", "same_direction_claims": [{"claim_id": "SAME", "source": "partial"}]})
    result = status_from_manager_verdict(manager, **kwargs)
    assert (result.analysis_status, result.trade_action) == ("VALID", "WAIT")
    assert "direction_without_adjudicated_support" not in result.reason_codes


def test_stored_manager_basis_has_precedence_over_debate_or_raw_claims():
    manager, kwargs = _fixture(adopted=["OPPOSITE"],
                               basis={"status": "unledgered", "same_direction_claims": []})
    kwargs["investment_debate_state"] = {"manager_verdict": {"direction_basis": {"status": "ledgered", "same_direction_claims": ["SAME"]}}}
    _assert_unsupported_abstain(status_from_manager_verdict(manager, **kwargs))


def test_debate_manager_basis_fallback_is_supported():
    manager, kwargs = _fixture(adopted=["OPPOSITE"])
    kwargs["investment_debate_state"] = {"manager_verdict": {"direction_basis": {"status": "unledgered", "same_direction_claims": []}}}
    _assert_unsupported_abstain(status_from_manager_verdict(manager, **kwargs))


def test_numeric_cleanup_keeps_independent_forecast_and_risk_cannot_reenable():
    manager, kwargs = _fixture()
    result = status_from_manager_verdict(manager, **kwargs)
    _assert_unsupported_abstain(result)
    payload = {"target_price": 120.0, "stop_loss_price": 90.0, "confidence": 75,
               "probability": 0.7, "forecast": {"p_rel_t10": 0.62}}
    apply_decision_status_to_result(payload, result)
    for key in ("target_price", "stop_loss_price", "confidence", "probability"):
        assert payload[key] is None
    assert payload["forecast"] == {"p_rel_t10": 0.62}
    risk = status_from_risk_verdict(upstream=result, risk_verdict="approve")
    assert (risk.analysis_status, risk.direction, risk.trade_action) == ("ABSTAIN", "N/A", "NO_TRADE")


def test_no_support_neutral_is_not_a_valid_balanced_view():
    manager, kwargs = _fixture()
    manager.update(direction="中性", winner="tie")
    _assert_unsupported_abstain(status_from_manager_verdict(manager, **kwargs))


@pytest.mark.parametrize("analysis", ["INVALID_RUN", "ABSTAIN"])
def test_prior_nonvalid_terminal_keeps_precedence(analysis):
    manager, kwargs = _fixture()
    old = {"analysis_status": analysis, "direction": "N/A", "trade_action": "NO_TRADE",
           "reason_codes": ["upstream_guard"], "risk_status": "BLOCKED"}
    manager["decision_status"] = old
    result = status_from_manager_verdict(manager, **kwargs)
    assert result.analysis_status == analysis
    assert result.reason_codes == ["upstream_guard"]
