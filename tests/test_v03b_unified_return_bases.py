"""V-03b unified return-basis tests (DAV-1479 / phase2-midterm plan §4 P2).

Covers, with fully synthetic market fixtures only (no vendor data, D-040):
- entry = T+1 open, target = N-th trading day from the SIGNAL date;
- prediction basis does NOT roll on target suspension (typed status);
- execution basis rolls to the first tradable exit within max_roll;
- limit-up locked T+1 -> unexecutable entry;
- SW L1 industry benchmark leg synced on both windows; y_rel = 1[R_stock - R_SW > 0];
- research basis (T close -> target close) is stored separately, never mixed;
- cohort_tag: backfill vs live classification;
- hold_days kwarg deprecation maps to eval_offset_from_signal = hold_days + 1;
- return_labels calendar window parity: canonical eval_offset override does
  not change canonical behaviour for short/medium.
"""
from __future__ import annotations

import warnings
from typing import Dict, Optional

import pytest

from tradingagents.dataflows.return_labels import (
    OutcomeStatus,
    resolve_horizon_calendar_window,
)
from tradingagents.eval.v03_return_measure import (
    DailyBar,
    DictPriceDataProvider,
    MeasurementOutcomeStatus,
    V03ReturnMeasureEngine,
)


# ---------------------------------------------------------------------------
# Synthetic calendar & market
# ---------------------------------------------------------------------------

# 20 consecutive trading days (Mon–Fri blocks), signal at index 3.
TRADE_DATES = [
    "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06",
    "2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13",
    "2026-03-16", "2026-03-17", "2026-03-18", "2026-03-19", "2026-03-20",
    "2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27",
]

SIGNAL_DATE = "2026-03-04"      # idx 2
ENTRY_DATE = "2026-03-05"       # T+1
TARGET_DATE = "2026-03-12"      # T+6 from signal (idx 8)
ROLL1 = "2026-03-13"
ROLL2 = "2026-03-16"


def _bar(d: str, open_: float, close: float, **kw) -> DailyBar:
    return DailyBar(
        date=d,
        open=open_,
        high=kw.pop("high", max(open_, close)),
        low=kw.pop("low", min(open_, close)),
        close=close,
        volume=kw.pop("volume", 100000.0),
        is_suspended=kw.pop("is_suspended", False),
        **kw,
    )


def _provider_with(bars_by_symbol: Dict[str, Dict[str, DailyBar]]) -> DictPriceDataProvider:
    prov = DictPriceDataProvider(
        trade_dates=TRADE_DATES,
        listing_dates={s: "2000-01-01" for s in bars_by_symbol},
    )
    for sym, bars in bars_by_symbol.items():
        for b in bars.values():
            prov.add_bar(sym, b)
    return prov


def _report(**over) -> dict:
    base = {
        "id": "r1",
        "user_id": "429163f7-50b6-4982-8bdf-96ae99506843",
        "symbol": "600519.SH",
        "trade_date": SIGNAL_DATE,
        "status": "completed",
        "decision": "BUY",
        "direction": "偏多",
        "created_at": f"{SIGNAL_DATE} 16:30:00",
    }
    base.update(over)
    return base


def _engine(provider, **kw) -> V03ReturnMeasureEngine:
    kw.setdefault("eval_offset_from_signal", 6)
    kw.setdefault("horizon_key", "legacy")  # offset 6 + short roll policy
    kw.setdefault("forward_oos_end_date", None)
    kw.setdefault("sw_daily_fetcher", lambda code, s, e: {})
    return V03ReturnMeasureEngine(price_provider=provider, **kw)


# ---------------------------------------------------------------------------
# 1. Dates & entry/exit semantics
# ---------------------------------------------------------------------------


def test_eval_offset_from_signal_dates_and_entry_open():
    """T+N anchors on the SIGNAL date: target = T+6, entry = T+1 open."""
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 110.0, 112.0),
            TARGET_DATE: _bar(TARGET_DATE, 120.0, 121.0),
        }
    }
    eng = _engine(_provider_with(bars))
    rec = eng.measure_sample(_report())
    assert rec.outcome_status == MeasurementOutcomeStatus.EVALUATED.value
    assert rec.entry_date == ENTRY_DATE
    assert rec.target_calendar_date == TARGET_DATE
    assert rec.exit_date == TARGET_DATE
    assert rec.entry_price == 110.0  # T+1 OPEN, not T close
    assert rec.exit_price == 121.0
    assert rec.roll_days_used == 0


def test_prediction_basis_no_roll_on_target_suspension():
    """Prediction basis: target suspended -> typed 'target_suspended', never rolled.

    Execution basis may still settle via roll (covered by next test); here the
    exit is also suspended so both stay typed.
    """
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 110.0, 112.0),
            TARGET_DATE: _bar(TARGET_DATE, 0.0, 0.0, volume=0.0, is_suspended=True),
            ROLL1: _bar(ROLL1, 0.0, 0.0, volume=0.0, is_suspended=True),
            ROLL2: _bar(ROLL2, 0.0, 0.0, volume=0.0, is_suspended=True),
            "2026-03-17": _bar("2026-03-17", 130.0, 131.0),  # post-window proof of suspension
        }
    }
    eng = _engine(_provider_with(bars))
    rec = eng.measure_sample(_report())
    # execution window exhausted -> suspension typed missing
    assert rec.outcome_status == MeasurementOutcomeStatus.TYPED_MISSING.value
    assert rec.missing_reason == "exit_suspension_window_exhausted"
    # prediction basis records its own typed status without roll
    assert rec.prediction_outcome_status == "target_suspended"
    assert rec.prediction_return is None


def test_execution_basis_rolls_to_first_tradable_exit():
    """Execution basis: target suspended -> roll to next tradable day (incl. costs)."""
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 110.0, 112.0),
            TARGET_DATE: _bar(TARGET_DATE, 0.0, 0.0, volume=0.0, is_suspended=True),
            ROLL1: _bar(ROLL1, 130.0, 132.0),  # first tradable exit
        }
    }
    eng = _engine(_provider_with(bars))
    rec = eng.measure_sample(_report())
    assert rec.outcome_status == MeasurementOutcomeStatus.EVALUATED.value
    assert rec.exit_date == ROLL1
    assert rec.exit_price == 132.0
    assert rec.roll_days_used == 1
    # prediction basis stays typed on the standard (unrolled) window
    assert rec.prediction_outcome_status == "target_suspended"
    assert rec.prediction_return is None


def test_limit_up_locked_entry_is_unexecutable():
    """T+1 locked at limit-up (open==high==low==limit_up) -> untradable."""
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: DailyBar(
                date=ENTRY_DATE, open=110.0, high=110.0, low=110.0, close=110.0,
                volume=100.0, limit_up=110.0,
            ),
            TARGET_DATE: _bar(TARGET_DATE, 120.0, 121.0),
        }
    }
    eng = _engine(_provider_with(bars))
    rec = eng.measure_sample(_report())
    assert rec.outcome_status == MeasurementOutcomeStatus.UNTRADABLE.value
    assert rec.untradable_reason == "limit_up_locked"
    assert rec.entry_price is None and rec.net_return is None


# ---------------------------------------------------------------------------
# 2. Research basis isolation
# ---------------------------------------------------------------------------


def test_research_basis_t_close_to_target_close():
    """research_* uses T close -> target close; does not mix with execution."""
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 110.0, 112.0),
            TARGET_DATE: _bar(TARGET_DATE, 120.0, 121.0),
        }
    }
    eng = _engine(_provider_with(bars))
    rec = eng.measure_sample(_report())
    assert rec.research_outcome_status == "evaluated"
    assert rec.research_entry_price == 101.0   # T close
    assert rec.research_exit_price == 121.0    # target close
    assert rec.research_return == pytest.approx((121.0 - 101.0) / 101.0, abs=1e-6)
    # Execution basis unaffected (T+1 open -> target close, with costs)
    assert rec.entry_price == 110.0
    assert rec.net_return is not None
    assert rec.prediction_return == pytest.approx((121.0 - 110.0) / 110.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 3. SW industry benchmark leg & y_rel
# ---------------------------------------------------------------------------


def _sw_fetcher(entry_px: float, exit_px: float):
    def fetch(code: str, start: str, end: str):
        return {
            start: {"open": entry_px, "high": entry_px, "low": entry_px, "close": entry_px},
            end: {"open": exit_px, "high": exit_px, "low": exit_px, "close": exit_px},
        }
    return fetch


def _membership_ok(symbol, date):
    return ({"src": "SW2021", "level": "L1", "code": "801999.SI", "name": "合成行业"}, None)


def test_sw_leg_synced_on_prediction_and_execution_windows():
    """SW leg computed for both windows; execution window synced to actual exit."""
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 110.0, 112.0),
            TARGET_DATE: _bar(TARGET_DATE, 0.0, 0.0, volume=0.0, is_suspended=True),
            ROLL1: _bar(ROLL1, 130.0, 132.0),
        }
    }
    # index: open 200 -> close 210 => +5%
    eng = _engine(
        _provider_with(bars),
        sw_daily_fetcher=_sw_fetcher(200.0, 210.0),
        sw_membership_resolver=_membership_ok,
    )
    rec = eng.measure_sample(_report())
    assert rec.outcome_status == MeasurementOutcomeStatus.EVALUATED.value
    assert rec.sw_index_code == "801999.SI"
    # prediction window was NOT evaluated (target suspended) -> no pred leg
    assert rec.prediction_return is None
    assert rec.y_rel is None
    # execution leg over [ENTRY_DATE, ROLL1]
    assert rec.sw_execution_status == "ok"
    assert rec.sw_execution_window_start == ENTRY_DATE
    assert rec.sw_execution_window_end == ROLL1
    assert rec.sw_execution_window_return == pytest.approx(0.05, abs=1e-6)


def test_y_rel_true_when_stock_beats_sw():
    """y_rel = 1[R_stock − R_SW > 0] on the prediction window."""
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 100.0, 101.0),
            TARGET_DATE: _bar(TARGET_DATE, 110.0, 112.0),  # +12% pred return
        }
    }
    # index +5% over same window -> stock beats -> y_rel True
    eng = _engine(
        _provider_with(bars),
        sw_daily_fetcher=_sw_fetcher(200.0, 210.0),
        sw_membership_resolver=_membership_ok,
    )
    rec = eng.measure_sample(_report())
    assert rec.prediction_return == pytest.approx(0.12, abs=1e-6)
    assert rec.sw_prediction_window_return == pytest.approx(0.05, abs=1e-6)
    assert rec.y_rel is True
    assert rec.r_stock_prediction == rec.prediction_return


def test_y_rel_false_when_stock_lags_sw():
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 100.0, 101.0),
            TARGET_DATE: _bar(TARGET_DATE, 102.0, 103.0),  # +3%
        }
    }
    # index +8% -> stock lags -> y_rel False (boundary: strictly > 0)
    eng = _engine(
        _provider_with(bars),
        sw_daily_fetcher=_sw_fetcher(200.0, 216.0),
        sw_membership_resolver=_membership_ok,
    )
    rec = eng.measure_sample(_report())
    assert rec.y_rel is False


def test_sw_leg_gap_is_typed_not_fatal():
    """Membership/price gap -> typed sw_* status, sample still measures."""
    def fetch_boom(code, s, e):
        raise ValueError("sw_daily_unavailable")

    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 110.0, 112.0),
            TARGET_DATE: _bar(TARGET_DATE, 120.0, 121.0),
        }
    }
    eng = _engine(
        _provider_with(bars),
        sw_daily_fetcher=fetch_boom,
        sw_membership_resolver=_membership_ok,
    )
    rec = eng.measure_sample(_report())
    assert rec.outcome_status == MeasurementOutcomeStatus.EVALUATED.value
    assert rec.sw_execution_status == "sw_daily_unavailable"
    assert rec.sw_execution_window_return is None
    assert rec.y_rel is None


def test_sw_membership_gap_is_typed_not_fatal():
    """Membership gap (industry unresolved at T) -> typed leg, sample still measures."""
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 110.0, 112.0),
            TARGET_DATE: _bar(TARGET_DATE, 120.0, 121.0),
        }
    }
    eng = _engine(
        _provider_with(bars),
        sw_membership_resolver=lambda s, d: (None, "【数据获取失败】申万一级行业基准：membership_missing_or_ambiguous"),
    )
    rec = eng.measure_sample(_report())
    assert rec.outcome_status == MeasurementOutcomeStatus.EVALUATED.value
    assert rec.sw_index_code is None
    assert rec.sw_membership_gap is not None
    assert rec.y_rel is None


# ---------------------------------------------------------------------------


def test_cohort_tag_backfill_vs_live():
    bars = {
        "600519.SH": {
            SIGNAL_DATE: _bar(SIGNAL_DATE, 100.0, 101.0),
            ENTRY_DATE: _bar(ENTRY_DATE, 110.0, 112.0),
            TARGET_DATE: _bar(TARGET_DATE, 120.0, 121.0),
        }
    }
    eng = _engine(_provider_with(bars))
    live = eng.measure_sample(_report(created_at="2026-03-04 16:30:00"))
    assert live.cohort_tag == "live"
    backfill = eng.measure_sample(_report(created_at="2026-10-02 09:00:00"))
    assert backfill.cohort_tag == "backfill"
    boundary = eng.measure_sample(_report(created_at="2026-03-06 09:00:00"))
    assert boundary.cohort_tag == "live"  # lag == threshold stays live


def test_hold_days_kwarg_deprecated_maps_to_offset():
    prov = _provider_with({})
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        eng = V03ReturnMeasureEngine(price_provider=prov, hold_days=5)
    assert any(issubclass(x.category, DeprecationWarning) for x in w)
    assert eng.eval_offset_from_signal == 6


def test_horizon_key_offset_mismatch_rejected():
    prov = _provider_with({})
    with pytest.raises(ValueError):
        V03ReturnMeasureEngine(
            price_provider=prov, horizon_key="medium", eval_offset_from_signal=6
        )


# ---------------------------------------------------------------------------
# 5. return_labels contract: eval_offset override doesn't alter canonical path
# ---------------------------------------------------------------------------


def test_return_labels_default_offset_unchanged():
    """Without eval_offset, window resolution is byte-identical canonical T+N."""
    w_default = resolve_horizon_calendar_window(
        signal_date="2026-03-04",
        horizon="short",
        trading_days=TRADE_DATES,
        as_of="2026-03-27",
        as_of_market_closed=True,
    )
    w_override = resolve_horizon_calendar_window(
        signal_date="2026-03-04",
        horizon="short",
        trading_days=TRADE_DATES,
        as_of="2026-03-27",
        as_of_market_closed=True,
        eval_offset=10,  # canonical short offset — must equal default
    )
    assert w_default == w_override
    assert w_default.target_calendar_date == "2026-03-18"  # idx 2 + 10

    w6 = resolve_horizon_calendar_window(
        signal_date="2026-03-04",
        horizon="short",
        trading_days=TRADE_DATES,
        as_of="2026-03-27",
        as_of_market_closed=True,
        eval_offset=6,
    )
    assert w6.target_calendar_date == "2026-03-12"  # idx 2 + 6
    assert w6.roll_candidate_dates == ("2026-03-13", "2026-03-16")
