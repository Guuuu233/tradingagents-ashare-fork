"""D-064: direction-aware confirmation, using only synthetic claim ledgers."""

from copy import deepcopy

import pytest

from tradingagents.agents.utils.decision_status import (
    evaluate_confirmation_state,
    status_from_manager_verdict,
)
from tradingagents.agents.utils.evidence_verifier import compute_direction_basis


def _summary(**overrides):
    result = {
        "decision": "adopt", "semantic_decision": "adopt",
        "counts": {"total": 1, "verified": 1}, "coverage": 1.0,
        "is_fatal": False,
    }
    result.update(overrides)
    return result


def _fixture(direction="看多", **claim_overrides):
    claims = [
        {"claim_id": "CORE", "stance": "bullish" if direction in {"看多", "BUY"} else "bearish"},
        {"claim_id": "P", "stance": "bullish", **claim_overrides},
    ]
    summary = {"CORE": _summary(), "P": _summary(decision="partial", semantic_decision="partial_threshold")}
    manager = {
        "direction": direction, "winner": "bull", "consistency_check_passed": True,
        "adopted_claim_ids": ["CORE"], "partially_adopted_claims": ["P"],
        "rejected_claim_ids": [], "direction_basis": {"status": "ledgered"},
    }
    kwargs = {"focus_claim_ids": ["CORE"], "claim_evidence_summary": summary, "claims": claims}
    return manager, kwargs


@pytest.mark.parametrize("direction,stance,action", [
    ("看多", "bullish", "BUY"), ("BUY", "bullish", "BUY"),
    ("看空", "bearish", "SELL"), ("SELL", "bearish", "SELL"),
])
def test_same_direction_partial_released_without_mutating_ledger(direction, stance, action):
    manager, kwargs = _fixture(direction, stance=stance)
    before = deepcopy((manager, kwargs))
    result = status_from_manager_verdict(manager, **kwargs)
    assert (result.confirmation_state, result.trade_action) == ("CONFIRMED", action)
    assert "released_same_direction_partial:P" in result.reason_codes
    assert not any(c.startswith("partially_adopted_claims:") for c in result.reason_codes)
    assert (manager, kwargs) == before


@pytest.mark.parametrize("direction,stance", [
    ("看多", "bearish"), ("看空", "bullish"), ("看多", "neutral"),
    ("看多", None), ("看多", ""), ("看多", "unrecognized"),
    ("中性", "bullish"), ("N/A", "bullish"), (None, "bullish"),
])
def test_other_partial_remains_blocking(direction, stance):
    manager, kwargs = _fixture(direction, stance=stance)
    result = status_from_manager_verdict(manager, **kwargs)
    assert (result.confirmation_state, result.trade_action) == ("PARTIAL", "WAIT")
    assert "partially_adopted_claims:P" in result.reason_codes
    assert not any(c.startswith("released_same_direction_partial:") for c in result.reason_codes)


@pytest.mark.parametrize("direction,stance", [("看多", "bearish"), ("看空", "bullish")])
def test_adopted_opposite_factual_blocks(direction, stance):
    manager, kwargs = _fixture(direction, stance=stance)
    manager.update(adopted_claim_ids=["CORE", "P"], partially_adopted_claims=[])
    kwargs["claim_evidence_summary"]["P"] = _summary()
    result = status_from_manager_verdict(manager, **kwargs)
    assert (result.confirmation_state, result.trade_action) == ("PARTIAL", "WAIT")
    assert "adopted_opposite_direction_claims:P" in result.reason_codes


@pytest.mark.parametrize("classification", ["summary", "observation", "hypothesis"])
def test_adopted_opposite_observation_hypothesis_not_blocking(classification):
    manager, kwargs = _fixture(stance="bearish")
    manager.update(adopted_claim_ids=["CORE", "P"], partially_adopted_claims=[])
    kwargs["claim_evidence_summary"]["P"] = _summary()
    if classification == "summary":
        kwargs["claim_evidence_summary"]["P"]["is_observation_or_hypothesis"] = True
    else:
        kwargs["claims"][1]["claim_type"] = classification
    result = status_from_manager_verdict(manager, **kwargs)
    assert (result.confirmation_state, result.trade_action) == ("CONFIRMED", "BUY")
    assert not any(c.startswith("adopted_opposite_direction_claims:") for c in result.reason_codes)


@pytest.mark.parametrize("stance", [None, "neutral", "unknown"])
def test_adopted_undetermined_does_not_trigger_opposite_rule(stance):
    manager, kwargs = _fixture(stance=stance)
    manager.update(adopted_claim_ids=["CORE", "P"], partially_adopted_claims=[])
    kwargs["claim_evidence_summary"]["P"] = _summary()
    result = status_from_manager_verdict(manager, **kwargs)
    assert (result.confirmation_state, result.trade_action) == ("CONFIRMED", "BUY")
    assert not any(c.startswith("adopted_opposite_direction_claims:") for c in result.reason_codes)


@pytest.mark.parametrize("in_summary", [False, True])
@pytest.mark.parametrize("speaker,stance,expected", [("Bull", "bearish", "CONFIRMED"), ("Bear", "bullish", "PARTIAL")])
def test_speaker_precedence_matches_direction_basis(in_summary, speaker, stance, expected):
    manager, kwargs = _fixture(stance=stance)
    source = kwargs["claim_evidence_summary"]["P"] if in_summary else kwargs["claims"][1]
    source["speaker_key"] = speaker
    basis, _ = compute_direction_basis("bull", ["CORE"], ["P"], kwargs["claim_evidence_summary"], kwargs["claims"])
    p_is_same = {"claim_id": "P", "source": "partial"} in basis["same_direction_claims"]
    result = status_from_manager_verdict(manager, **kwargs)
    assert p_is_same == (expected == "CONFIRMED")
    assert result.confirmation_state == expected


@pytest.mark.parametrize("bucket", ["partial", "adopted"])
@pytest.mark.parametrize("pass_none", [False, True])
def test_omitted_or_none_direction_preserves_legacy(bucket, pass_none):
    manager, kwargs = _fixture(stance="bullish" if bucket == "partial" else "bearish")
    if bucket == "adopted":
        manager.update(adopted_claim_ids=["CORE", "P"], partially_adopted_claims=[])
        kwargs["claim_evidence_summary"]["P"] = _summary()
    optional = {"manager_direction": None} if pass_none else {}
    result = evaluate_confirmation_state(
        **kwargs, adopted_claim_ids=manager["adopted_claim_ids"],
        partially_adopted_claims=manager["partially_adopted_claims"], rejected_claim_ids=[], **optional,
    )
    assert result == (("PARTIAL", ["partially_adopted_claims:P"]) if bucket == "partial" else
                      ("CONFIRMED", ["all_core_claims_verified:CORE"]))


@pytest.mark.parametrize("guard,code,state", [
    ("fatal", "fatal_partially_adopted_claims:P", "UNRESOLVED"),
    ("pit", "pit_failed_partially_adopted_claims:P", "UNRESOLVED"),
    ("adopted_partial", "partial_adopted_claims:P", "PARTIAL"),
    ("rejected_partial", "rejected_partial_claims:P", "PARTIAL"),
    ("unadjudicated_partial", "unadjudicated_partial_claims:P", "PARTIAL"),
    ("core_partial", "partial_core_claims:verified=CORE;unverified=P", "PARTIAL"),
    ("partial_only", "partial_only_support", "PARTIAL"),
])
def test_other_confirmation_guards_unchanged(guard, code, state):
    manager, kwargs = _fixture()
    if guard == "fatal":
        kwargs["claim_evidence_summary"]["P"]["is_fatal"] = True
    elif guard == "pit":
        kwargs["claim_evidence_summary"]["P"]["pit_failed"] = True
    elif guard == "adopted_partial":
        manager.update(adopted_claim_ids=["CORE", "P"], partially_adopted_claims=[])
    elif guard == "rejected_partial":
        manager.update(rejected_claim_ids=["P"], partially_adopted_claims=[])
    elif guard == "unadjudicated_partial":
        manager["partially_adopted_claims"] = []
    elif guard == "core_partial":
        kwargs["focus_claim_ids"] = ["CORE", "P"]
    elif guard == "partial_only":
        manager["adopted_claim_ids"] = []
        kwargs["focus_claim_ids"] = []
        kwargs["claims"] = kwargs["claims"][1:]
        kwargs["claim_evidence_summary"].pop("CORE")
    result, codes = evaluate_confirmation_state(
        **kwargs, manager_direction="看多", adopted_claim_ids=manager["adopted_claim_ids"],
        partially_adopted_claims=manager["partially_adopted_claims"], rejected_claim_ids=manager["rejected_claim_ids"],
    )
    assert result == state
    assert code in codes


def test_mixed_partial_audits_only_released_ids_in_sorted_order():
    manager, kwargs = _fixture()
    kwargs["claims"] += [{"claim_id": "Z", "stance": "bullish"}, {"claim_id": "R", "stance": "bearish"}]
    kwargs["claim_evidence_summary"].update(Z=_summary(), R=_summary())
    manager["partially_adopted_claims"] = ["Z", "R", "P"]
    result = status_from_manager_verdict(manager, **kwargs)
    assert result.confirmation_state == "PARTIAL"
    assert "partially_adopted_claims:R" in result.reason_codes
    assert "released_same_direction_partial:P,Z" in result.reason_codes


def test_same_direction_release_does_not_bypass_direction_basis_wait():
    manager, kwargs = _fixture()
    manager["direction_basis"] = {"status": "partial_only"}
    result = status_from_manager_verdict(manager, **kwargs)
    assert (result.confirmation_state, result.trade_action) == ("CONFIRMED", "WAIT")
    assert "direction_basis_not_ledgered:partial_only" in result.reason_codes


def test_legit_excluded_adopted_opposite_is_zero_contribution():
    manager, kwargs = _fixture(stance="bearish")
    manager.update(adopted_claim_ids=["CORE", "P"], partially_adopted_claims=[],
                   claim_cluster_metrics={"double_count_guard_audit": {"excluded_claim_ids": ["P"]}})
    kwargs["claim_evidence_summary"]["P"] = _summary()
    result = status_from_manager_verdict(manager, **kwargs)
    assert (result.confirmation_state, result.trade_action) == ("CONFIRMED", "BUY")
    assert not any(c.startswith("adopted_opposite_direction_claims:") for c in result.reason_codes)
