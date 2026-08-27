"""D-009 P0-1: decision_status vocabulary and calibration eligibility."""

from __future__ import annotations

from tradingagents.agents.utils.decision_status import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_NO_TRADE,
    ACTION_WAIT,
    ANALYSIS_ABSTAIN,
    ANALYSIS_INVALID_RUN,
    ANALYSIS_VALID,
    DIRECTION_BULL,
    DIRECTION_NA,
    DIRECTION_NEUTRAL,
    abstain_status,
    apply_decision_status_to_result,
    invalid_run_status,
    is_calibration_eligible,
    valid_status,
)
from tradingagents.agents.utils.run_integrity import fund_flow_guard_abstain_status


def test_invalid_run_status_shape():
    status = invalid_run_status(
        failure_class="DATA_ERROR",
        reason_codes=["analyst_upstream_7_of_7_failed"],
    )
    assert status.analysis_status == ANALYSIS_INVALID_RUN
    assert status.failure_class == "DATA_ERROR"
    assert status.direction == DIRECTION_NA
    assert status.trade_action == ACTION_NO_TRADE
    assert status.confidence is None
    assert status.probability is None


def test_abstain_is_not_neutral_hold_view():
    status = abstain_status(reason_codes=["fund_flow_guard:blocked"])
    assert status.analysis_status == ANALYSIS_ABSTAIN
    assert status.direction == DIRECTION_NA
    assert status.trade_action == ACTION_NO_TRADE
    assert status.direction != DIRECTION_NEUTRAL
    assert status.trade_action != ACTION_HOLD


def test_fund_flow_guard_maps_to_abstain():
    status = fund_flow_guard_abstain_status(
        {"blocked": True, "direction_allowed": False, "status": "blocked"}
    )
    assert status.analysis_status == ANALYSIS_ABSTAIN
    assert status.trade_action == ACTION_NO_TRADE
    assert any("fund_flow_guard" in c for c in status.reason_codes)


def test_valid_buy_is_calibration_eligible():
    result = apply_decision_status_to_result(
        {},
        valid_status(
            direction=DIRECTION_BULL,
            trade_action=ACTION_BUY,
            confidence=70,
            probability=0.65,
        ),
    )
    result["probability"] = 0.65  # apply keeps confidence from status when VALID
    assert result["analysis_status"] == ANALYSIS_VALID
    assert is_calibration_eligible(result) is True


def test_invalid_and_abstain_and_wait_excluded_from_calibration():
    invalid = apply_decision_status_to_result(
        {"probability": 0.5},
        invalid_run_status(failure_class="DATA_ERROR"),
    )
    assert is_calibration_eligible(invalid) is False

    abstain = apply_decision_status_to_result(
        {"probability": 0.5},
        abstain_status(),
    )
    assert is_calibration_eligible(abstain) is False

    wait = apply_decision_status_to_result(
        {"probability": 0.5, "analysis_status": ANALYSIS_VALID},
        valid_status(
            direction=DIRECTION_NA,
            trade_action=ACTION_WAIT,
            probability=0.5,
        ),
    )
    # WAIT is non-directional even if analysis_status VALID
    wait["analysis_status"] = ANALYSIS_VALID
    wait["trade_action"] = ACTION_WAIT
    wait["probability"] = 0.5
    assert is_calibration_eligible(wait) is False


def test_legacy_row_without_analysis_status_is_excluded():
    assert is_calibration_eligible({"probability": 0.6}) is False
    assert is_calibration_eligible({"probability": None}) is False
    assert is_calibration_eligible(
        {"probability": 0.6, "trade_action": ACTION_NO_TRADE}
    ) is False
    assert is_calibration_eligible(
        {
            "analysis_status": ANALYSIS_VALID,
            "trade_action": ACTION_BUY,
            "probability": 0.6,
        }
    ) is True


def test_consistency_hard_gate_maps_to_abstain_not_valid_buy():
    from tradingagents.agents.utils.decision_status import status_from_manager_verdict

    status = status_from_manager_verdict(
        {
            "direction": "看多",
            "winner": "bull",
            "position_pct": 60,
            "consistency_check_passed": False,
            "failed_checks": ["stop_loss_missing"],
        }
    )
    assert status.analysis_status == ANALYSIS_ABSTAIN
    assert status.trade_action == ACTION_NO_TRADE
    assert status.risk_status == "BLOCKED"
    assert status.direction == DIRECTION_NA


def test_risk_reject_overwrites_upstream_valid_buy():
    from tradingagents.agents.utils.decision_status import (
        status_from_risk_verdict,
        valid_status,
    )

    upstream = valid_status(direction="BULL", trade_action=ACTION_BUY, probability=0.7)
    status = status_from_risk_verdict(upstream=upstream, risk_verdict="reject")
    assert status.analysis_status == ANALYSIS_ABSTAIN
    assert status.trade_action == ACTION_NO_TRADE
    assert status.risk_status == "BLOCKED"
