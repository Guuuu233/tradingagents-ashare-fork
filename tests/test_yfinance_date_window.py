"""Tests for DAV-953: yfinance historical output must re-validate the request window.

get_YFin_data_online must filter vendor-returned rows to the inclusive
[start_date, end_date] window instead of trusting the supplier's start/end
parameters. All tests use a fake Ticker.history — no real network / yfinance.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from tradingagents.dataflows import y_finance


SYMBOL = "AAPL"


def _make_history_frame(dates: list[str]) -> pd.DataFrame:
    idx = pd.to_datetime(dates)
    n = len(dates)
    return pd.DataFrame(
        {
            "Open": [1.0] * n,
            "High": [2.0] * n,
            "Low": [0.5] * n,
            "Close": [1.5] * n,
            "Adj Close": [1.4] * n,
            "Volume": [100] * n,
        },
        index=idx,
    )


def _fake_ticker(dates: list[str]):
    frame = _make_history_frame(dates)
    ticker = SimpleNamespace()
    ticker.history = lambda *args, **kwargs: frame.copy()
    return ticker


def _run(dates: list[str], start: str, end: str) -> str:
    with patch.object(y_finance.yf, "Ticker", return_value=_fake_ticker(dates)):
        return y_finance.get_YFin_data_online(SYMBOL, start, end)


def _csv_dates(output: str) -> list[str]:
    lines = [ln for ln in output.splitlines() if ln and not ln.startswith("#")]
    # first non-# line is the CSV header
    return [ln.split(",")[0] for ln in lines[1:]]


class TestYFinanceDateWindow:
    def test_rows_above_end_date_are_dropped(self):
        out = _run(["2026-07-01", "2026-07-30", "2026-07-31"], "2026-07-01", "2026-07-30")
        assert _csv_dates(out) == ["2026-07-01", "2026-07-30"]
        assert "2026-07-31" not in out

    def test_rows_below_start_date_are_dropped(self):
        out = _run(["2026-06-30", "2026-07-01", "2026-07-30"], "2026-07-01", "2026-07-30")
        assert _csv_dates(out) == ["2026-07-01", "2026-07-30"]
        assert "2026-06-30" not in out

    def test_both_boundaries_inclusive(self):
        out = _run(["2026-07-01", "2026-07-15", "2026-07-30"], "2026-07-01", "2026-07-30")
        assert _csv_dates(out) == ["2026-07-01", "2026-07-15", "2026-07-30"]
        assert out.startswith(f"# {SYMBOL} 股票数据（2026-07-01 至 2026-07-30）")
        assert "# 记录总数：3" in out

    def test_reversed_window_raises(self):
        with pytest.raises(ValueError):
            _run(["2026-07-01"], "2026-07-30", "2026-07-01")

    @pytest.mark.parametrize(
        "start,end",
        [("not-a-date", "2026-07-30"), ("2026-07-01", "2026/07/30"), ("2026-13-01", "2026-07-30")],
    )
    def test_invalid_dates_raise(self, start, end):
        with pytest.raises(ValueError):
            _run(["2026-07-01"], start, end)

    def test_empty_vendor_result_returns_no_data_message(self):
        out = _run([], "2026-07-01", "2026-07-30")
        assert out == f"No data found for symbol '{SYMBOL}' between 2026-07-01 and 2026-07-30"

    def test_all_rows_outside_window_returns_no_data_message(self):
        out = _run(["2026-06-30", "2026-07-31"], "2026-07-01", "2026-07-30")
        assert out == f"No data found for symbol '{SYMBOL}' between 2026-07-01 and 2026-07-30"

    def test_tz_aware_index_is_filtered(self):
        dates = ["2026-06-30", "2026-07-01", "2026-07-30"]
        idx = pd.to_datetime(dates).tz_localize("America/New_York")
        frame = _make_history_frame(dates)
        frame.index = idx
        ticker = SimpleNamespace()
        ticker.history = lambda *args, **kwargs: frame.copy()
        with patch.object(y_finance.yf, "Ticker", return_value=ticker):
            out = y_finance.get_YFin_data_online(SYMBOL, "2026-07-01", "2026-07-30")
        assert _csv_dates(out) == ["2026-07-01", "2026-07-30"]
