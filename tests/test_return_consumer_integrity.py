"""Tests for return consumer data integrity (DAV-935 / DAV-936).

Verifies daily bar integrity contract across return and calibration consumer boundaries:
1. Canonical CSV with '#' metadata headers is correctly parsed across all consumers.
2. Identical duplicate daily bars fold deterministically without changing results.
3. Conflicting bars on key fields fail closed (explicitly unavailable) regardless of row order.
4. Conflicts are never masked by drop_duplicates, iloc[0], iloc[-1], or date switching.
5. Normal results, short window rejection, zero/suspension semantics, and price basis routing are preserved.
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from api.services.backtest_service import (
    PRICE_BASIS_RAW,
    PRICE_BASIS_UNSPECIFIED,
    PRICE_BASIS_VENDOR_QFQ,
    _get_price_after,
    _get_price_on,
)
from api.services.calibration_service import (
    _get_price_after_strict,
    _resolve_outcome,
)
from tradingagents.dataflows.return_labels import (
    OutcomeStatus,
    resolve_horizon_return_label,
)
from tradingagents.dataflows.trade_calendar import DuplicateBarConflictError
from tradingagents.eval.v03_return_measure import DailyBar, VendorPriceDataProvider


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures & Sample CSV Builders
# ─────────────────────────────────────────────────────────────────────────────

CANONICAL_COMMENT_HEADER = """# Stock data for 600519.SH from 2024-01-01 to 2024-01-10
# Total records: 5

Date,Open,High,Low,Close,Volume
2024-01-02,100.0,105.0,98.0,101.0,10000
2024-01-03,101.0,106.0,100.0,103.0,12000
2024-01-04,103.0,107.0,102.0,105.0,11000
2024-01-05,105.0,108.0,104.0,107.0,15000
2024-01-08,107.0,110.0,106.0,109.0,14000
"""

FIXED_TRADING_DAYS = [
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08",
    "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15",
    "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19", "2024-01-22",
    "2024-01-23", "2024-01-24", "2024-01-25", "2024-01-26", "2024-01-29",
]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Canonical '#' Metadata Comment Header Parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestMetadataCommentHeaderParsing:
    """Ensure '#' metadata header lines from production providers are properly ignored."""

    def test_calibration_get_price_after_strict_parses_header(self):
        """_get_price_after_strict must not return None when CSV starts with '#' comments."""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=CANONICAL_COMMENT_HEADER):
            # base_date=2024-01-02, hold_days=2 -> target date is 2024-01-04 (Close=105.0)
            price = _get_price_after_strict("600519", "2024-01-02", 2)
            assert price == pytest.approx(105.0)

    def test_backtest_get_price_after_parses_header(self):
        """_get_price_after must parse canonical CSV with '#' headers."""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=CANONICAL_COMMENT_HEADER):
            price = _get_price_after(
                "600519", "2024-01-02", 2, trading_days=FIXED_TRADING_DAYS
            )
            assert price == pytest.approx(105.0)

    def test_backtest_get_price_on_parses_header(self):
        """_get_price_on must parse canonical CSV with '#' headers."""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=CANONICAL_COMMENT_HEADER):
            price = _get_price_on("600519", "2024-01-04")
            assert price == pytest.approx(105.0)

    def test_backtest_get_price_on_never_uses_future_bars(self):
        """_get_price_on must filter out bars strictly after requested date (Fact 5)."""
        future_leaking_csv = """Date,Close
2024-01-02,100.0
2024-01-03,102.0
2024-01-04,105.0
2024-01-05,999.0
2024-01-08,888.0
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=future_leaking_csv):
            # Requested date is 2024-01-04; must return 105.0 and not future prices 999.0 or 888.0
            price = _get_price_on("600519", "2024-01-04")
            assert price == pytest.approx(105.0)

            # If all bars in response are strictly after requested date, return None
            price_past = _get_price_on("600519", "2024-01-01")
            assert price_past is None

    def test_v03_provider_fetch_bar_parses_header(self):
        """VendorPriceDataProvider._fetch_bar must parse canonical CSV with '#' headers."""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=CANONICAL_COMMENT_HEADER):
            provider = VendorPriceDataProvider()
            bar = provider.get_bar("600519", "2024-01-04")
            assert bar is not None
            assert bar.close == pytest.approx(105.0)
            assert bar.open == pytest.approx(103.0)
            assert bar.volume == pytest.approx(11000.0)
            assert not bar.is_suspended

    def test_return_labels_parses_header(self):
        """resolve_horizon_return_label must parse canonical CSV with '#' headers."""
        long_csv = CANONICAL_COMMENT_HEADER + "\n".join(
            f"{d},100.0,105.0,99.0,102.0,1000" for d in FIXED_TRADING_DAYS[5:]
        )
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=long_csv):
            res = resolve_horizon_return_label(
                symbol="600519",
                signal_date="2024-01-02",
                horizon="short",
                trading_days=FIXED_TRADING_DAYS,
                as_of="2024-01-29",
                as_of_market_closed=True,
            )
            assert res["outcome_status"] == OutcomeStatus.EVALUATED_OK.value
            assert res["entry_signal_price"] == pytest.approx(101.0)
            assert res["entry_executable_price"] == pytest.approx(101.0)  # T+1 Open


# ─────────────────────────────────────────────────────────────────────────────
# 2. Identical Duplicate Daily Bars Folding (Order-Independent)
# ─────────────────────────────────────────────────────────────────────────────

class TestIdenticalDuplicateFolding:
    """Ensure identical duplicate rows fold deterministically and do not alter prices."""

    def test_backtest_price_after_identical_duplicates_order_independent(self):
        csv_order1 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,101,1000
2024-01-03,101,106,100,103,1000
2024-01-03,101,106,100,103,1000
2024-01-04,103,107,102,105,1000
"""
        csv_order2 = """Date,Open,High,Low,Close,Volume
2024-01-04,103,107,102,105,1000
2024-01-03,101,106,100,103,1000
2024-01-03,101,106,100,103,1000
2024-01-02,100,105,98,101,1000
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv_order1):
            p1 = _get_price_after("600519", "2024-01-02", 1, trading_days=FIXED_TRADING_DAYS)
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv_order2):
            p2 = _get_price_after("600519", "2024-01-02", 1, trading_days=FIXED_TRADING_DAYS)

        assert p1 == pytest.approx(103.0)
        assert p2 == pytest.approx(103.0)
        assert p1 == p2

    def test_backtest_price_on_identical_duplicates_order_independent(self):
        csv1 = """Date,Close
2024-01-02,100.0
2024-01-03,105.0
2024-01-03,105.0
"""
        csv2 = """Date,Close
2024-01-03,105.0
2024-01-03,105.0
2024-01-02,100.0
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv1):
            p1 = _get_price_on("600519", "2024-01-03")
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv2):
            p2 = _get_price_on("600519", "2024-01-03")

        assert p1 == pytest.approx(105.0)
        assert p2 == pytest.approx(105.0)

    def test_calibration_price_after_strict_identical_duplicates(self):
        csv = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,101,1000
2024-01-03,101,106,100,103,1000
2024-01-03,101,106,100,103,1000
2024-01-04,103,107,102,105,1000
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv):
            price = _get_price_after_strict("600519", "2024-01-02", 2)
            assert price == pytest.approx(105.0)

    def test_v03_provider_stock_identical_duplicates(self):
        csv = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,101,1000
2024-01-02,100,105,98,101,1000
2024-01-03,101,106,100,103,1000
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv):
            provider = VendorPriceDataProvider()
            bar = provider.get_bar("600519", "2024-01-02")
            assert bar is not None
            assert bar.close == pytest.approx(101.0)
            assert bar.open == pytest.approx(100.0)

    def test_v03_provider_csi300_identical_duplicates(self):
        mock_df = pd.DataFrame([
            {"date": "2024-01-02", "open": 3300.0, "high": 3350.0, "low": 3280.0, "close": 3320.0, "amount": 5e8},
            {"date": "2024-01-02", "open": 3300.0, "high": 3350.0, "low": 3280.0, "close": 3320.0, "amount": 5e8},
        ])
        with patch("akshare.stock_zh_index_daily_tx", return_value=mock_df):
            provider = VendorPriceDataProvider()
            bar = provider.get_bar("000300.SH", "2024-01-02")
            assert bar is not None
            assert bar.close == pytest.approx(3320.0)
            assert bar.open == pytest.approx(3300.0)

    def test_return_labels_identical_duplicates_order_independent(self):
        csv1 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,99,102,1000
2024-01-02,100,105,99,102,1000
""" + "\n".join(f"{d},100,105,99,102,1000" for d in FIXED_TRADING_DAYS[1:])
        csv2 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,99,102,1000
""" + "\n".join(f"{d},100,105,99,102,1000" for d in FIXED_TRADING_DAYS[1:])
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv1):
            r1 = resolve_horizon_return_label(
                symbol="600519", signal_date="2024-01-02", horizon="short",
                trading_days=FIXED_TRADING_DAYS, as_of="2024-01-29", as_of_market_closed=True,
            )
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv2):
            r2 = resolve_horizon_return_label(
                symbol="600519", signal_date="2024-01-02", horizon="short",
                trading_days=FIXED_TRADING_DAYS, as_of="2024-01-29", as_of_market_closed=True,
            )
        assert r1["entry_signal_price"] == pytest.approx(102.0)
        assert r2["entry_signal_price"] == pytest.approx(102.0)
        assert r1["outcome_status"] == r2["outcome_status"] == OutcomeStatus.EVALUATED_OK.value


# ─────────────────────────────────────────────────────────────────────────────
# 3. Conflicting Daily Bars Rejection (Fail-Closed, Order-Independent)
# ─────────────────────────────────────────────────────────────────────────────

class TestConflictingDailyBarsRejection:
    """Ensure conflicting bars fail closed (return None / provider_failure) and never silently pick one."""

    def test_backtest_price_after_conflicting_close_refused(self):
        """Swapping conflicting rows must NOT change price; both orders must return None."""
        csv_order1 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,101,1000
2024-01-03,101,106,100,103,1000
2024-01-03,101,106,100,999,1000
2024-01-04,103,107,102,105,1000
"""
        csv_order2 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,101,1000
2024-01-03,101,106,100,999,1000
2024-01-03,101,106,100,103,1000
2024-01-04,103,107,102,105,1000
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv_order1):
            p1 = _get_price_after("600519", "2024-01-02", 1, trading_days=FIXED_TRADING_DAYS)
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv_order2):
            p2 = _get_price_after("600519", "2024-01-02", 1, trading_days=FIXED_TRADING_DAYS)

        assert p1 is None
        assert p2 is None

    def test_backtest_price_after_conflict_on_non_target_date_refused(self):
        """Conflict on intermediate date must not be masked by jumping to target date."""
        csv = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,101,1000
2024-01-02,100,105,98,202,1000
2024-01-03,101,106,100,103,1000
2024-01-04,103,107,102,105,1000
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv):
            # Target is 2024-01-04 (hold_days=2), but 2024-01-02 has conflict
            price = _get_price_after("600519", "2024-01-02", 2, trading_days=FIXED_TRADING_DAYS)
            assert price is None

    def test_backtest_price_on_conflicting_close_refused(self):
        """_get_price_on with conflicting Close must fail closed (return None)."""
        csv1 = """Date,Close
2024-01-02,100.0
2024-01-02,200.0
"""
        csv2 = """Date,Close
2024-01-02,200.0
2024-01-02,100.0
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv1):
            p1 = _get_price_on("600519", "2024-01-02")
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv2):
            p2 = _get_price_on("600519", "2024-01-02")

        assert p1 is None
        assert p2 is None

    def test_calibration_price_after_strict_conflicting_close_refused(self):
        """_get_price_after_strict must return None when conflict exists."""
        csv1 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,101,1000
2024-01-03,101,106,100,103,1000
2024-01-03,101,106,100,999,1000
2024-01-04,103,107,102,105,1000
"""
        csv2 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,101,1000
2024-01-03,101,106,100,999,1000
2024-01-03,101,106,100,103,1000
2024-01-04,103,107,102,105,1000
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv1):
            p1 = _get_price_after_strict("600519", "2024-01-02", 2)
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv2):
            p2 = _get_price_after_strict("600519", "2024-01-02", 2)

        assert p1 is None
        assert p2 is None

    def test_v03_provider_stock_conflicting_ohlcv_refused(self):
        """VendorPriceDataProvider._fetch_bar must return None on conflicting OHLCV."""
        csv1 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,101,1000
2024-01-02,100,105,98,999,1000
"""
        csv2 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,98,999,1000
2024-01-02,100,105,98,101,1000
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv1):
            b1 = VendorPriceDataProvider().get_bar("600519", "2024-01-02")
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv2):
            b2 = VendorPriceDataProvider().get_bar("600519", "2024-01-02")

        assert b1 is None
        assert b2 is None

    def test_v03_provider_csi300_conflicting_ohlcv_refused(self):
        """VendorPriceDataProvider._fetch_benchmark_bar must return None on conflicting CSI300 rows."""
        mock_df1 = pd.DataFrame([
            {"date": "2024-01-02", "open": 3300.0, "high": 3350.0, "low": 3280.0, "close": 3320.0, "amount": 5e8},
            {"date": "2024-01-02", "open": 3300.0, "high": 3350.0, "low": 3280.0, "close": 3999.0, "amount": 5e8},
        ])
        mock_df2 = pd.DataFrame([
            {"date": "2024-01-02", "open": 3300.0, "high": 3350.0, "low": 3280.0, "close": 3999.0, "amount": 5e8},
            {"date": "2024-01-02", "open": 3300.0, "high": 3350.0, "low": 3280.0, "close": 3320.0, "amount": 5e8},
        ])
        with patch("akshare.stock_zh_index_daily_tx", return_value=mock_df1):
            b1 = VendorPriceDataProvider().get_bar("000300.SH", "2024-01-02")
        with patch("akshare.stock_zh_index_daily_tx", return_value=mock_df2):
            b2 = VendorPriceDataProvider().get_bar("000300.SH", "2024-01-02")

        assert b1 is None
        assert b2 is None

    def test_return_labels_conflicting_ohlcv_returns_provider_failure(self):
        """resolve_horizon_return_label must return provider_failure when CSV has conflicting rows."""
        csv1 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,99,102,1000
2024-01-02,100,105,99,999,1000
""" + "\n".join(f"{d},100,105,99,102,1000" for d in FIXED_TRADING_DAYS[1:])
        csv2 = """Date,Open,High,Low,Close,Volume
2024-01-02,100,105,99,999,1000
2024-01-02,100,105,99,102,1000
""" + "\n".join(f"{d},100,105,99,102,1000" for d in FIXED_TRADING_DAYS[1:])

        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv1):
            r1 = resolve_horizon_return_label(
                symbol="600519", signal_date="2024-01-02", horizon="short",
                trading_days=FIXED_TRADING_DAYS, as_of="2024-01-29", as_of_market_closed=True,
            )
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=csv2):
            r2 = resolve_horizon_return_label(
                symbol="600519", signal_date="2024-01-02", horizon="short",
                trading_days=FIXED_TRADING_DAYS, as_of="2024-01-29", as_of_market_closed=True,
            )

        assert r1["outcome_status"] == OutcomeStatus.PROVIDER_FAILURE.value
        assert r2["outcome_status"] == OutcomeStatus.PROVIDER_FAILURE.value
        assert not r1["evaluation_eligible"]
        assert not r2["evaluation_eligible"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. Existing Semantics Preservation
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingSemanticsPreserved:
    """Ensure short window rejection, zero/suspension semantics, and price basis routing remain intact."""

    def test_short_window_rejection(self):
        """Consumers must refuse truncated hold windows."""
        short_csv = """Date,Close
2024-01-02,100.0
2024-01-03,101.0
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=short_csv):
            # hold_days=5 requires 5 trading days after 2024-01-02
            assert _get_price_after("600519", "2024-01-02", 5, trading_days=FIXED_TRADING_DAYS) is None
            assert _get_price_after_strict("600519", "2024-01-02", 5) is None

    def test_zero_or_negative_price_refusal(self):
        """Legal zero or negative price must return None in price fetchers."""
        zero_csv = """Date,Close
2024-01-02,0.0
2024-01-03,0.0
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=zero_csv):
            assert _get_price_after("600519", "2024-01-02", 1, trading_days=FIXED_TRADING_DAYS) is None
            assert _get_price_after_strict("600519", "2024-01-02", 1) is None

    def test_v03_provider_suspension_detection(self):
        """VendorPriceDataProvider must flag suspension when volume=0 or open=close=0."""
        susp_csv = """Date,Open,High,Low,Close,Volume
2024-01-02,100.0,105.0,98.0,101.0,0.0
"""
        with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=susp_csv):
            provider = VendorPriceDataProvider()
            bar = provider.get_bar("600519", "2024-01-02")
            assert bar is not None
            assert bar.is_suspended is True

    def test_price_basis_isolation_and_fail_closed(self):
        """Unsupported price basis must fail closed (return None) without calling vendor."""
        assert _get_price_after("600519", "2024-01-02", 5, price_basis="invalid_basis") is None
        assert _get_price_after("600519", "2024-01-02", 5, price_basis=PRICE_BASIS_UNSPECIFIED) is None
        assert _get_price_on("600519", "2024-01-02", price_basis="invalid_basis") is None

    def test_raw_price_basis_routes_to_akshare_raw(self):
        """PRICE_BASIS_RAW routes to cn_akshare with price_basis='raw'."""
        mock_prov = MagicMock()
        mock_prov.get_stock_data.return_value = CANONICAL_COMMENT_HEADER

        with patch("tradingagents.dataflows.interface._registry.get", return_value=mock_prov):
            price = _get_price_after("600519", "2024-01-02", 2, price_basis=PRICE_BASIS_RAW, trading_days=FIXED_TRADING_DAYS)
            assert price == pytest.approx(105.0)
            mock_prov.get_stock_data.assert_called_once()
            _, kwargs = mock_prov.get_stock_data.call_args
            assert kwargs.get("price_basis") == PRICE_BASIS_RAW
