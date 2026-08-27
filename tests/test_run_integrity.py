"""D-009 P0-1: RunIntegrity — 0/7 … 7/7 analyst failure detection."""

from __future__ import annotations

from tradingagents.agents.utils.decision_status import (
    ACTION_NO_TRADE,
    ANALYSIS_DATA_ERROR,
    ANALYSIS_INVALID_RUN,
    DIRECTION_NA,
    apply_decision_status_to_result,
    is_calibration_eligible,
)
from tradingagents.agents.utils.run_integrity import (
    DEFAULT_REQUIRED_ANALYSTS,
    evaluate_run_integrity,
    evaluate_state_integrity,
    is_failed_analyst_report,
    resolve_decision_status_for_result,
)


_OK = "市场技术报告：突破阻力位，均线多头排列，成交量温和放大。"
_FAIL_502 = "分析报告生成失败：Error code: 502"


def _seven(*, failed: int = 0, body_ok: str = _OK, body_fail: str = _FAIL_502) -> dict:
    keys = [
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "macro_report",
        "smart_money_report",
        "volume_price_report",
    ]
    reports = {}
    for i, key in enumerate(keys):
        reports[key] = body_fail if i < failed else body_ok
    return reports


def test_failure_markers_detect_502_and_empty():
    assert is_failed_analyst_report(_FAIL_502)[0] is True
    assert is_failed_analyst_report("")[0] is True
    assert is_failed_analyst_report(None)[0] is True
    assert is_failed_analyst_report("本项不可用：上游超时")[0] is True
    assert is_failed_analyst_report(_OK)[0] is False
    # Short stubs without failure keywords are not auto-failed (compat with unit fixtures).
    assert is_failed_analyst_report("M")[0] is False


def test_zero_of_seven_failed_is_not_invalid():
    integrity = evaluate_run_integrity(_seven(failed=0))
    assert integrity.failed_required_count == 0
    assert integrity.all_required_failed is False
    assert integrity.analysis_status is None
    assert integrity.decision_status is None
    assert integrity.required_count == 7
    assert set(integrity.required_analysts) == set(DEFAULT_REQUIRED_ANALYSTS)


def test_partial_failures_do_not_trigger_invalid_run():
    for n in (1, 3, 6):
        integrity = evaluate_run_integrity(_seven(failed=n))
        assert integrity.failed_required_count == n
        assert integrity.all_required_failed is False
        assert integrity.analysis_status == "PARTIAL"
        assert integrity.decision_status is not None
        assert integrity.decision_status["trade_action"] == "NO_TRADE"


def test_seven_of_seven_502_is_invalid_run_no_trade():
    integrity = evaluate_run_integrity(_seven(failed=7))
    assert integrity.all_required_failed is True
    assert integrity.failed_required_count == 7
    assert integrity.analysis_status == ANALYSIS_INVALID_RUN
    assert integrity.failure_class == ANALYSIS_DATA_ERROR
    ds = integrity.decision_status
    assert ds is not None
    assert ds["analysis_status"] == ANALYSIS_INVALID_RUN
    assert ds["failure_class"] == ANALYSIS_DATA_ERROR
    assert ds["direction"] == DIRECTION_NA
    assert ds["trade_action"] == ACTION_NO_TRADE
    assert ds["confidence"] is None
    assert ds["probability"] is None
    assert any("7_of_7" in code for code in integrity.reason_codes)


def test_all_empty_reports_are_invalid_run():
    integrity = evaluate_run_integrity(_seven(failed=7, body_fail=""))
    assert integrity.all_required_failed is True
    assert integrity.decision_status["trade_action"] == ACTION_NO_TRADE


def test_apply_status_nulls_fabricated_numbers():
    result = {
        **_seven(failed=7),
        "decision": "HOLD",
        "direction": "NEUTRAL",
        "confidence": 25,
        "probability": 0.55,
        "target_price": 28.6,
        "stop_loss_price": 24.8,
        "upside": 0.12,
        "downside": 0.08,
    }
    integrity = evaluate_run_integrity(result)
    apply_decision_status_to_result(result, integrity.decision_status)
    assert result["analysis_status"] == ANALYSIS_INVALID_RUN
    assert result["trade_action"] == ACTION_NO_TRADE
    assert result["decision"] == ACTION_NO_TRADE
    assert result["direction"] == DIRECTION_NA
    assert result["confidence"] is None
    assert result["probability"] is None
    assert result["target_price"] is None
    assert result["stop_loss_price"] is None
    assert is_calibration_eligible(result) is False


def test_resolve_decision_status_recomputes_from_reports():
    result = {
        **_seven(failed=7),
        "decision": "HOLD",
        "confidence": 25,
    }
    status = resolve_decision_status_for_result(result)
    assert status is not None
    assert status.analysis_status == ANALYSIS_INVALID_RUN
    assert status.trade_action == ACTION_NO_TRADE


def test_resolve_prefers_integrity_over_stale_buy_status():
    """7/7 failures must beat a leftover BUY decision_status on the payload."""
    result = {
        **_seven(failed=7),
        "decision_status": {
            "analysis_status": "VALID",
            "direction": "BULL",
            "trade_action": "BUY",
            "risk_status": "OK",
        },
        "decision": "BUY",
        "confidence": 80,
    }
    status = resolve_decision_status_for_result(result)
    assert status is not None
    assert status.analysis_status == ANALYSIS_INVALID_RUN
    assert status.trade_action == ACTION_NO_TRADE


def test_evaluate_state_integrity_uses_selected_analysts():
    state = {
        "selected_analysts": ["market", "news"],
        "market_report": _FAIL_502,
        "news_report": _FAIL_502,
        "sentiment_report": _OK,  # not required → ignored
    }
    integrity = evaluate_state_integrity(state)
    assert integrity.required_count == 2
    assert integrity.all_required_failed is True
    assert set(integrity.failed_required) == {"market", "news"}


def test_manifest_marks_failed_reports_not_passed():
    from tradingagents.agents.utils.debate_utils import build_debate_report_manifest

    manifest = build_debate_report_manifest(_seven(failed=7))
    assert all(item["passed"] is False for item in manifest.values())
    assert all(item["mode"] == "failed" for item in manifest.values())
