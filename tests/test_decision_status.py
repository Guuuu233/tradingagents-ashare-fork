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
        is_non_executable_status,
        status_from_risk_verdict,
        valid_status,
    )

    upstream = valid_status(direction=DIRECTION_BULL, trade_action=ACTION_BUY, probability=0.7)
    status = status_from_risk_verdict(upstream=upstream, risk_verdict="reject")
    # Contract: reject preserves upstream direction, trade_action=NO_TRADE, risk_status=BLOCKED, analysis_status=VALID
    assert status.analysis_status == ANALYSIS_VALID
    assert status.direction == DIRECTION_BULL
    assert status.trade_action == ACTION_NO_TRADE
    assert status.risk_status == "BLOCKED"
    assert is_non_executable_status(status) is True


def test_risk_revise_preserves_valid_upstream_and_allows_trader_llm():
    from tradingagents.agents.utils.decision_status import (
        is_non_executable_status,
        status_from_risk_verdict,
        valid_status,
    )

    upstream = valid_status(direction=DIRECTION_BULL, trade_action=ACTION_BUY, confidence=80, probability=0.75)
    status = status_from_risk_verdict(upstream=upstream, risk_verdict="revise", retry_count=1, max_retries=1)
    assert status.analysis_status == ANALYSIS_VALID
    assert status.direction == DIRECTION_BULL
    assert status.trade_action == ACTION_BUY
    assert status.risk_status == "ELEVATED"
    assert is_non_executable_status(status) is False


def test_risk_revise_exhausted_retries_becomes_no_trade_blocked():
    from tradingagents.agents.utils.decision_status import (
        is_non_executable_status,
        status_from_risk_verdict,
        valid_status,
    )

    upstream = valid_status(direction=DIRECTION_BULL, trade_action=ACTION_BUY, confidence=80, probability=0.75)
    status = status_from_risk_verdict(upstream=upstream, risk_verdict="revise", retry_count=2, max_retries=1)
    assert status.trade_action == ACTION_NO_TRADE
    assert status.risk_status == "BLOCKED"
    assert is_non_executable_status(status) is True


def test_risk_pass_preserves_valid_upstream():
    from tradingagents.agents.utils.decision_status import (
        is_non_executable_status,
        status_from_risk_verdict,
        valid_status,
    )

    upstream = valid_status(direction=DIRECTION_BULL, trade_action=ACTION_BUY, confidence=80, probability=0.75)
    status = status_from_risk_verdict(upstream=upstream, risk_verdict="pass")
    assert status.analysis_status == ANALYSIS_VALID
    assert status.direction == DIRECTION_BULL
    assert status.trade_action == ACTION_BUY
    assert status.risk_status == "OK"
    assert is_non_executable_status(status) is False


def test_risk_verdict_on_non_executable_upstream_stays_non_executable():
    from tradingagents.agents.utils.decision_status import (
        is_non_executable_status,
        status_from_risk_verdict,
    )

    upstream = abstain_status()
    status = status_from_risk_verdict(upstream=upstream, risk_verdict="revise")
    assert status.analysis_status == ANALYSIS_ABSTAIN
    assert status.trade_action == ACTION_NO_TRADE
    assert status.risk_status == "BLOCKED"
    assert is_non_executable_status(status) is True


def test_resolve_soft_returns_abstain_not_valid_neutral_hold():
    from tradingagents.agents.utils.decision_status import (
        is_non_executable_status,
        resolve_soft,
    )

    # 1. 7/7 reports OK but lacks decision_status -> must ABSTAIN (not VALID/NEUTRAL/HOLD)
    ok_report = "市场技术报告：突破阻力位，均线多头排列，成交量温和放大，趋势明确。"
    seven_ok = {
        "macro_report": ok_report,
        "market_report": ok_report,
        "sentiment_report": ok_report,
        "news_report": ok_report,
        "fundamentals_report": ok_report,
        "smart_money_report": ok_report,
        "volume_price_report": ok_report,
    }
    status_with_report = resolve_soft(seven_ok)
    assert status_with_report.analysis_status == ANALYSIS_ABSTAIN
    assert status_with_report.direction == DIRECTION_NA
    assert status_with_report.trade_action == ACTION_NO_TRADE
    assert status_with_report.analysis_status != ANALYSIS_VALID
    assert status_with_report.trade_action != ACTION_HOLD
    assert is_non_executable_status(status_with_report) is True

    # 2. Empty horizon payload -> INVALID_RUN (also non-executable)
    status_empty = resolve_soft({})
    assert status_empty.analysis_status in {ANALYSIS_ABSTAIN, ANALYSIS_INVALID_RUN}
    assert status_empty.trade_action == ACTION_NO_TRADE
    assert status_empty.analysis_status != ANALYSIS_VALID
    assert status_empty.trade_action != ACTION_HOLD
    assert is_non_executable_status(status_empty) is True
