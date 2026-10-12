"""return_labels contract-preservation proof (DAV-1479 acceptance).

The card requires: ``return_labels.py`` only GAINS an interface
(``eval_offset`` override) and does NOT change existing semantics. These tests
lock the pre-change behaviour with golden expected values: any semantic drift
in the default code path fails here even if the diff looks small.
"""
from __future__ import annotations

import pytest

from tradingagents.dataflows.return_labels import (
    OutcomeStatus,
    resolve_horizon_calendar_window,
    resolve_horizon_return_label,
)

TRADE_DATES = [
    "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06",
    "2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13",
    "2026-03-16", "2026-03-17", "2026-03-18", "2026-03-19", "2026-03-20",
    "2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27",
    "2026-03-30", "2026-03-31", "2026-04-01", "2026-04-02", "2026-04-03",
    "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10", "2026-04-13",
    "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-17", "2026-04-20",
    "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24", "2026-04-27",
    "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-06", "2026-05-07",
    "2026-05-08", "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14",
]


def _window(**kw):
    return resolve_horizon_calendar_window(
        signal_date="2026-03-04",
        trading_days=TRADE_DATES,
        as_of="2026-05-14",
        as_of_market_closed=True,
        **kw,
    )


class TestCalendarWindowGoldenSemantics:
    """Canonical offsets (short=10, medium=40) must not drift."""

    def test_short_window_default(self):
        w = _window(horizon="short")
        assert w.executable_entry_date == "2026-03-05"
        assert w.target_calendar_date == "2026-03-18"  # T+10 from signal
        assert w.max_roll_days == 2
        assert w.roll_candidate_dates == ("2026-03-19", "2026-03-20")
        assert w.eval_offset == 10
        assert w.is_due is True

    def test_medium_window_default(self):
        w = _window(horizon="medium")
        assert w.target_calendar_date == "2026-04-30"  # T+40 from signal
        assert w.max_roll_days == 5
        assert w.roll_candidate_dates == (
            "2026-05-06", "2026-05-07", "2026-05-08", "2026-05-11", "2026-05-12",
        )
        assert w.eval_offset == 40

    def test_explicit_canonical_offset_is_identical(self):
        """eval_offset=canonical must equal the default call field-by-field."""
        assert _window(horizon="short") == _window(horizon="short", eval_offset=10)
        assert _window(horizon="medium") == _window(horizon="medium", eval_offset=40)

    def test_invalid_eval_offset_rejected(self):
        for bad in (0, -1, 2.5, True, "10"):
            with pytest.raises(ValueError):
                _window(horizon="short", eval_offset=bad)


def _label(**kw):
    return resolve_horizon_return_label(
        symbol="600519.SH",
        signal_date="2026-03-04",
        horizon="short",
        trading_days=TRADE_DATES,
        as_of="2026-05-14",
        as_of_market_closed=True,
        **kw,
    )


class TestReturnLabelGoldenSemantics:
    """Existing label-resolution behaviour must be field-identical."""

    def _bars(self):
        return {
            "2026-03-04": {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e5},
            "2026-03-05": {"open": 110.0, "high": 112.0, "low": 109.0, "close": 111.0, "volume": 1e5},
            "2026-03-18": {"open": 120.0, "high": 122.0, "low": 119.0, "close": 121.0, "volume": 1e5},
        }

    def test_default_evaluated_label(self):
        res = _label(bar_data=self._bars())
        assert res["outcome_status"] == OutcomeStatus.EVALUATED_OK.value
        assert res["executable_entry_date"] == "2026-03-05"
        assert res["target_calendar_date"] == "2026-03-18"
        assert res["actual_exit_date"] == "2026-03-18"
        assert res["entry_price"] == 110.0
        assert res["exit_price"] == 121.0
        assert res["entry_signal_price"] == 101.0
        assert res["roll_days_used"] == 0
        assert res["return_pct"] == pytest.approx(10.0, abs=1e-4)
        assert res["is_direction_hit"] is True
        assert res["evaluation_eligible"] is True

    def test_label_result_keys_unchanged(self):
        res = _label(bar_data=self._bars())
        assert set(res.keys()) == {
            "symbol", "horizon", "profile_id", "price_basis", "return_type",
            "entry_date", "executable_entry_date", "target_calendar_date",
            "actual_exit_date", "roll_days_used", "entry_signal_price",
            "entry_executable_price", "entry_price", "exit_price",
            "cash_dividend_total", "split_ratio_total", "return_pct",
            "outcome_status", "is_direction_hit", "evaluation_eligible",
        }

    def test_limit_up_locked_entry_unchanged(self):
        bars = self._bars()
        bars["2026-03-05"] = {
            "open": 110.0, "high": 110.0, "low": 110.0, "close": 110.0,
            "volume": 1e5, "limit_up": 110.0,
        }
        res = _label(bar_data=bars, direction="BUY")
        assert res["outcome_status"] == OutcomeStatus.UNEXECUTABLE_ENTRY.value
        assert res["entry_price"] is None and res["return_pct"] is None

    def test_suspension_roll_unchanged(self):
        bars = self._bars()
        # target day suspended; first roll candidate tradable
        bars["2026-03-18"] = {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0.0, "is_suspended": True}
        bars["2026-03-19"] = {"open": 125.0, "high": 126.0, "low": 124.0, "close": 125.5, "volume": 1e5}
        res = _label(bar_data=bars)
        assert res["outcome_status"] == OutcomeStatus.EVALUATED_OK.value
        assert res["actual_exit_date"] == "2026-03-19"
        assert res["roll_days_used"] == 1
        assert res["exit_price"] == 125.5

    def test_eval_offset_override_changes_only_target(self):
        """New interface: override shifts target/roll anchors; entry unchanged."""
        res = _label(bar_data=self._bars(), eval_offset=6)
        assert res["target_calendar_date"] == "2026-03-12"  # T+6, not T+10
        assert res["executable_entry_date"] == "2026-03-05"
        # target bar missing in fixture -> data_missing (not evaluated)
        assert res["outcome_status"] == OutcomeStatus.DATA_MISSING.value
