"""SW2021 L1 industry benchmark leg unit tests (DAV-1479 P2 second step).

Synthetic fixtures only — no Tushare access. Verifies:
- window return math (open on start, close on end);
- typed failure statuses (missing start/end bar, invalid price, fetcher error);
- membership resolved strictly at signal_date via the injected resolver
  (production binding = resolve_sw_l1_benchmark, DAV-1453 口径A);
- conflicting/mismatched vendor rows fail closed;
- y_rel inputs never fabricated.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.dataflows.sw_benchmark import (
    SwIndexWindowReturn,
    compute_sw_index_window_return,
    resolve_sw_benchmark_leg,
    default_sw_daily_fetcher,
    _SW_DAILY_MEMO,
)


def _fetcher(bars):
    def fetch(code: str, start: str, end: str):
        return {d: b for d, b in bars.items() if start <= d <= end}
    return fetch


def _membership(code="801010.SI", name="合成行业"):
    return ({"src": "SW2021", "level": "L1", "code": code, "name": name}, None)


class TestComputeSwIndexWindowReturn:
    def test_ok_return_math(self):
        bars = {
            "2026-03-05": {"open": 200.0, "high": 201.0, "low": 199.0, "close": 200.5},
            "2026-03-12": {"open": 210.0, "high": 211.0, "low": 209.0, "close": 210.0},
        }
        r = compute_sw_index_window_return(
            index_code="801010.SI", index_name="x",
            window_start="2026-03-05", window_end="2026-03-12",
            sw_daily_fetcher=_fetcher(bars),
        )
        assert r.status == "ok"
        # entry side uses OPEN(200), exit side uses CLOSE(210) => +5%
        assert r.index_entry_price == 200.0
        assert r.index_exit_price == 210.0
        assert r.r_sw == pytest.approx(0.05, abs=1e-6)
        assert r.window_start == "2026-03-05" and r.window_end == "2026-03-12"

    def test_missing_start_or_end_typed(self):
        bars = {"2026-03-12": {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}}
        r = compute_sw_index_window_return(
            index_code="c", index_name="n",
            window_start="2026-03-05", window_end="2026-03-12",
            sw_daily_fetcher=_fetcher(bars),
        )
        assert r.status == "sw_daily_missing_start" and r.r_sw is None
        r2 = compute_sw_index_window_return(
            index_code="c", index_name="n",
            window_start="2026-03-12", window_end="2026-03-13",
            sw_daily_fetcher=_fetcher(bars),
        )
        assert r2.status == "sw_daily_missing_end"

    def test_fetcher_error_is_typed(self):
        def boom(code, s, e):
            raise ValueError("sw_daily_unavailable")
        r = compute_sw_index_window_return(
            index_code="c", index_name="n",
            window_start="2026-03-05", window_end="2026-03-12",
            sw_daily_fetcher=boom,
        )
        assert r.status == "sw_daily_unavailable"
        assert r.gap_note and "申万" in r.gap_note

    def test_invalid_window_rejected(self):
        r = compute_sw_index_window_return(
            index_code="c", index_name="n",
            window_start="2026-03-12", window_end="2026-03-05",  # end < start
            sw_daily_fetcher=_fetcher({}),
        )
        assert r.status == "window_invalid"

    def test_nonpositive_price_fails_closed(self):
        bars = {
            "2026-03-05": {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
            "2026-03-12": {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
        }
        r = compute_sw_index_window_return(
            index_code="c", index_name="n",
            window_start="2026-03-05", window_end="2026-03-12",
            sw_daily_fetcher=_fetcher(bars),
        )
        assert r.status == "sw_daily_price_invalid" and r.r_sw is None


class TestResolveSwBenchmarkLeg:
    def test_both_windows_filled(self):
        bars = {
            "2026-03-05": {"open": 200.0, "high": 1, "low": 1, "close": 200.5},
            "2026-03-12": {"open": 210.0, "high": 1, "low": 1, "close": 210.0},
            "2026-03-13": {"open": 220.0, "high": 1, "low": 1, "close": 220.0},
        }
        leg = resolve_sw_benchmark_leg(
            symbol="600519.SH", signal_date="2026-03-04",
            prediction_window=("2026-03-05", "2026-03-12"),
            execution_window=("2026-03-05", "2026-03-13"),
            sw_daily_fetcher=_fetcher(bars),
            membership_resolver=lambda s, d: _membership(),
        )
        assert leg["membership_status"] == "ok"
        assert leg["index_code"] == "801010.SI"
        assert leg["prediction"]["status"] == "ok"
        assert leg["prediction"]["window_end"] == "2026-03-12"
        assert leg["execution"]["status"] == "ok"
        assert leg["execution"]["window_end"] == "2026-03-13"
        # distinct exits -> distinct returns (window sync check)
        assert leg["prediction"]["r_sw"] != leg["execution"]["r_sw"]

    def test_membership_gap_returns_typed_leg(self):
        leg = resolve_sw_benchmark_leg(
            symbol="600519.SH", signal_date="2026-03-04",
            prediction_window=("2026-03-05", "2026-03-12"),
            execution_window=("2026-03-05", "2026-03-13"),
            membership_resolver=lambda s, d: (None, "【数据获取失败】申万一级行业基准：x"),
        )
        assert leg["membership_status"] == "gap"
        assert leg["prediction"] is None and leg["execution"] is None
        assert leg["membership_gap_note"].startswith("【数据获取失败】")

    def test_resolver_exception_is_gap_not_crash(self):
        def boom(s, d):
            raise RuntimeError("network gone")
        leg = resolve_sw_benchmark_leg(
            symbol="600519.SH", signal_date="2026-03-04",
            prediction_window=("2026-03-05", "2026-03-12"),
            membership_resolver=boom,
        )
        assert leg["membership_status"] == "gap"

    def test_membership_resolved_at_signal_date(self):
        seen = {}

        def resolver(symbol, date):
            seen["symbol"], seen["date"] = symbol, date
            return _membership()

        resolve_sw_benchmark_leg(
            symbol="600519.SH", signal_date="2026-03-04",
            execution_window=("2026-03-05", "2026-03-13"),
            sw_daily_fetcher=_fetcher({}),
            membership_resolver=resolver,
        )
        assert seen == {"symbol": "600519.SH", "date": "2026-03-04"}


class TestDefaultSwDailyFetcher:
    """default_sw_daily_fetcher against a stubbed _query_tushare_api."""

    def setup_method(self):
        _SW_DAILY_MEMO.clear()

    def test_happy_path_and_schema_guard(self, monkeypatch):
        import tradingagents.dataflows.sw_benchmark as mod

        df = pd.DataFrame([
            {"ts_code": "801010.SI", "trade_date": "20260305", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05},
            {"ts_code": "801010.SI", "trade_date": "2026-03-12", "open": 2.0, "high": 2.1, "low": 1.9, "close": 2.05},
        ])
        monkeypatch.setattr(mod, "_query_tushare_api", lambda *a, **k: (df, None, None))
        out = default_sw_daily_fetcher("801010.SI", "2026-03-05", "2026-03-12")
        assert set(out.keys()) == {"2026-03-05", "2026-03-12"}
        assert out["2026-03-05"]["open"] == 1.0 and out["2026-03-12"]["close"] == 2.05

    def test_symbol_mismatch_fails_closed(self, monkeypatch):
        import tradingagents.dataflows.sw_benchmark as mod

        df = pd.DataFrame([
            {"ts_code": "801011.SI", "trade_date": "20260305", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
        ])
        monkeypatch.setattr(mod, "_query_tushare_api", lambda *a, **k: (df, None, None))
        with pytest.raises(ValueError, match="sw_daily_symbol_mismatch"):
            default_sw_daily_fetcher("801010.SI", "2026-03-05", "2026-03-12")

    def test_provider_error_raises_typed(self, monkeypatch):
        import tradingagents.dataflows.sw_benchmark as mod

        monkeypatch.setattr(mod, "_query_tushare_api", lambda *a, **k: (None, "rate_limited", "secret note"))
        with pytest.raises(ValueError, match="sw_daily_unavailable"):
            default_sw_daily_fetcher("801010.SI", "2026-03-05", "2026-03-12")

    def test_conflicting_duplicate_rows_fail_closed(self, monkeypatch):
        import tradingagents.dataflows.sw_benchmark as mod

        df = pd.DataFrame([
            {"ts_code": "801010.SI", "trade_date": "20260305", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
            {"ts_code": "801010.SI", "trade_date": "20260305", "open": 9.9, "high": 9.9, "low": 9.9, "close": 9.9},
        ])
        monkeypatch.setattr(mod, "_query_tushare_api", lambda *a, **k: (df, None, None))
        with pytest.raises(ValueError, match="sw_daily_conflicting_rows"):
            default_sw_daily_fetcher("801010.SI", "2026-03-05", "2026-03-12")
