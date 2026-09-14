"""Offline regression and integrity test suite for K-line API (/v1/market/kline)

Covers:
1. Normal CSVs and commented headers parsed into existing candles format.
2. Identical duplicate dates collapsed deterministically.
3. Conflicting duplicate dates on OHLC or Volume rejected explicitly (unavailable).
4. Order-independence: vendor row order does not select one conflicting candle over another.
5. Legal zero volume (volume=0) and zero change (open=close) preserved without being treated as missing.
6. Illegal required prices handled per existing failure semantics (dropped or rejected).
7. FastAPI endpoint /v1/market/kline returns existing unavailable error (404 no kline data) on conflict.
8. Chinese index symbol pathway preserved.
9. Index DataFrame path (_normalize_kline_df) identical duplicate folding and conflict rejection (fail-closed).
"""

from unittest.mock import patch
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app, _parse_stock_csv, _normalize_kline_df, _is_cn_index_symbol


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestKlineApiIntegrity:
    """Requirement 1: Normal CSV and comment headers parsing."""

    def test_normal_csv_with_comments_and_whitespace(self):
        raw = (
            "# source: mock_vendor\n"
            "# timestamp: 2026-01-05T00:00:00\n"
            " Date , Open , High , Low , Close , Volume \n"
            "2026-01-05, 10.0, 11.0, 9.0, 10.5, 1000.0\n"
            "2026-01-06, 10.5, 12.0, 10.0, 11.5, 1500.0\n"
        )
        candles = _parse_stock_csv(raw)
        assert len(candles) == 2
        assert candles[0] == {
            "date": "2026-01-05",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
        }
        assert candles[1] == {
            "date": "2026-01-06",
            "open": 10.5,
            "high": 12.0,
            "low": 10.0,
            "close": 11.5,
            "volume": 1500.0,
        }

    def test_ascending_date_ordering_preserved(self):
        raw = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-08,12.0,13.0,11.5,12.5,2000\n"
            "2026-01-05,10.0,11.0,9.0,10.5,1000\n"
            "2026-01-07,11.0,12.0,10.5,11.8,1200\n"
        )
        candles = _parse_stock_csv(raw)
        assert [c["date"] for c in candles] == ["2026-01-05", "2026-01-07", "2026-01-08"]

    """Requirement 2: Duplicate date folding and conflict rejection for stock CSV."""

    def test_identical_duplicate_dates_collapsed(self):
        raw = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.5,1000.0\n"
            "2026-01-05,10.0,11.0,9.0,10.5,1000.0\n"
            "2026-01-06,10.5,12.0,10.0,11.5,1500.0\n"
        )
        candles = _parse_stock_csv(raw)
        assert len(candles) == 2
        assert candles[0]["date"] == "2026-01-05"
        assert candles[0]["close"] == 10.5
        assert candles[0]["volume"] == 1000.0
        assert candles[1]["date"] == "2026-01-06"

    def test_identical_duplicate_dates_without_volume_collapsed(self):
        raw = (
            "Date,Open,High,Low,Close\n"
            "2026-01-05,10.0,11.0,9.0,10.5\n"
            "2026-01-05,10.0,11.0,9.0,10.5\n"
        )
        candles = _parse_stock_csv(raw)
        assert len(candles) == 1
        assert candles[0]["date"] == "2026-01-05"
        assert candles[0]["volume"] is None

    def test_identical_duplicate_dates_with_nan_volume_collapsed(self):
        raw = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.5,\n"
            "2026-01-05,10.0,11.0,9.0,10.5,\n"
        )
        candles = _parse_stock_csv(raw)
        assert len(candles) == 1
        assert candles[0]["date"] == "2026-01-05"
        assert candles[0]["volume"] is None

    def test_conflicting_duplicate_ohlc_rejected(self):
        # Conflict on Close (10.2 vs 10.8)
        raw_a = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.2,1000\n"
            "2026-01-05,10.0,11.0,9.0,10.8,1000\n"
        )
        assert _parse_stock_csv(raw_a) == []

        # Order reversed must also reject (not order dependent)
        raw_b = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.8,1000\n"
            "2026-01-05,10.0,11.0,9.0,10.2,1000\n"
        )
        assert _parse_stock_csv(raw_b) == []

    def test_conflicting_duplicate_open_high_low_rejected(self):
        for col, val1, val2 in [
            ("Open", "10.0", "10.5"),
            ("High", "11.0", "11.5"),
            ("Low", "9.0", "9.2"),
        ]:
            raw = (
                "Date,Open,High,Low,Close,Volume\n"
                f"2026-01-05,{val1 if col == 'Open' else '10.0'},{val1 if col == 'High' else '11.0'},{val1 if col == 'Low' else '9.0'},10.5,1000\n"
                f"2026-01-05,{val2 if col == 'Open' else '10.0'},{val2 if col == 'High' else '11.0'},{val2 if col == 'Low' else '9.0'},10.5,1000\n"
            )
            assert _parse_stock_csv(raw) == [], f"Failed to reject conflict in {col}"

    def test_conflicting_duplicate_volume_rejected(self):
        # Case A: Differing positive volumes
        raw_vol_diff = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.5,1000\n"
            "2026-01-05,10.0,11.0,9.0,10.5,2000\n"
        )
        assert _parse_stock_csv(raw_vol_diff) == []

        # Case B: One row has volume, one row has NaN
        raw_vol_nan = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.5,1000\n"
            "2026-01-05,10.0,11.0,9.0,10.5,\n"
        )
        assert _parse_stock_csv(raw_vol_nan) == []

        # Case C: Zero volume vs positive volume
        raw_vol_zero_pos = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.5,0\n"
            "2026-01-05,10.0,11.0,9.0,10.5,1000\n"
        )
        assert _parse_stock_csv(raw_vol_zero_pos) == []

        # Case D: Zero volume vs NaN
        raw_vol_zero_nan = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.5,0\n"
            "2026-01-05,10.0,11.0,9.0,10.5,\n"
        )
        assert _parse_stock_csv(raw_vol_zero_nan) == []

    def test_conflict_in_multi_day_series_rejects_entire_response(self):
        raw = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-02,9.5,10.0,9.0,9.8,800\n"
            "2026-01-05,10.0,11.0,9.0,10.2,1000\n"
            "2026-01-05,10.0,11.0,9.0,10.8,1000\n"
            "2026-01-06,10.5,11.5,10.0,11.0,1200\n"
        )
        assert _parse_stock_csv(raw) == []

    """Requirement 3: Zero volume/zero change and illegal prices."""

    def test_legal_zero_volume_preserved(self):
        raw = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.5,0\n"
            "2026-01-06,10.5,12.0,10.0,11.5,0.0\n"
        )
        candles = _parse_stock_csv(raw)
        assert len(candles) == 2
        assert candles[0]["volume"] == 0.0
        assert candles[1]["volume"] == 0.0

    def test_legal_zero_change_fields_preserved(self):
        # Flat price day (limit up/down or suspension)
        raw = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,10.0,10.0,10.0,0\n"
        )
        candles = _parse_stock_csv(raw)
        assert len(candles) == 1
        assert candles[0] == {
            "date": "2026-01-05",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 0.0,
        }

    def test_illegal_required_prices_failure_semantics(self):
        # Missing required column
        raw_missing_col = (
            "Date,Open,High,Low\n"
            "2026-01-05,10.0,11.0,9.0\n"
        )
        assert _parse_stock_csv(raw_missing_col) == []

        # Missing Date column
        raw_no_date = (
            "Open,High,Low,Close,Volume\n"
            "10.0,11.0,9.0,10.5,1000\n"
        )
        assert _parse_stock_csv(raw_no_date) == []

        # All rows have non-numeric price
        raw_bad_price = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,invalid,11.0,9.0,10.5,1000\n"
        )
        assert _parse_stock_csv(raw_bad_price) == []

        # Empty or comments only
        assert _parse_stock_csv("") == []
        assert _parse_stock_csv("# only comments\n# nothing else\n") == []

    """Requirement 4: Kline API endpoint integration with TestClient."""

    def test_kline_endpoint_normal_data_returns_200(self, client):
        raw_csv = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.5,1000.0\n"
        )
        with patch("api.main.route_to_vendor", return_value=raw_csv):
            resp = client.get("/v1/market/kline?symbol=600519.SH&start_date=2026-01-01&end_date=2026-01-10")
            assert resp.status_code == 200
            data = resp.json()
            assert data["symbol"] == "600519.SH"
            assert len(data["candles"]) == 1
            assert data["candles"][0]["close"] == 10.5

    def test_kline_endpoint_conflicting_duplicates_returns_404(self, client):
        raw_conflict = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.2,1000\n"
            "2026-01-05,10.0,11.0,9.0,10.8,1000\n"
        )
        with patch("api.main.route_to_vendor", return_value=raw_conflict):
            resp = client.get("/v1/market/kline?symbol=600519.SH&start_date=2026-01-01&end_date=2026-01-10")
            assert resp.status_code == 404
            assert resp.json()["detail"] == "no kline data"

    def test_kline_endpoint_identical_duplicates_returns_200_collapsed(self, client):
        raw_dup = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,10.0,11.0,9.0,10.5,1000.0\n"
            "2026-01-05,10.0,11.0,9.0,10.5,1000.0\n"
        )
        with patch("api.main.route_to_vendor", return_value=raw_dup):
            resp = client.get("/v1/market/kline?symbol=600519.SH&start_date=2026-01-01&end_date=2026-01-10")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["candles"]) == 1
            assert data["candles"][0]["date"] == "2026-01-05"

    def test_index_symbol_routing_preserved(self):
        assert _is_cn_index_symbol("000001.SH") is True
        assert _is_cn_index_symbol("399001.SZ") is True
        assert _is_cn_index_symbol("600519.SH") is False

    """Requirement 5: Index DataFrame path folding and conflict rejection."""

    def test_normalize_kline_df_identical_duplicates_collapsed(self):
        df_dup = pd.DataFrame({
            "日期": ["2026-01-05", "2026-01-05"],
            "开盘": [3000.0, 3000.0],
            "最高": [3050.0, 3050.0],
            "最低": [2980.0, 2980.0],
            "收盘": [3020.0, 3020.0],
            "成交量": [10000.0, 10000.0],
        })
        norm = _normalize_kline_df(df_dup)
        assert len(norm) == 1
        assert norm.iloc[0]["Close"] == 3020.0

    def test_normalize_kline_df_conflicts_rejected_as_empty(self):
        # Conflicting close price on the same date
        df_conflict = pd.DataFrame({
            "日期": ["2026-01-05", "2026-01-05"],
            "开盘": [3000.0, 3000.0],
            "最高": [3050.0, 3050.0],
            "最低": [2980.0, 2980.0],
            "收盘": [3020.0, 3035.0],
            "成交量": [10000.0, 10000.0],
        })
        norm = _normalize_kline_df(df_conflict)
        assert norm.empty

    def test_normalize_kline_df_volume_conflicts_rejected(self):
        df_conflict_vol = pd.DataFrame({
            "日期": ["2026-01-05", "2026-01-05"],
            "开盘": [3000.0, 3000.0],
            "最高": [3050.0, 3050.0],
            "最低": [2980.0, 2980.0],
            "收盘": [3020.0, 3020.0],
            "成交量": [10000.0, 20000.0],
        })
        norm = _normalize_kline_df(df_conflict_vol)
        assert norm.empty

    def test_kline_endpoint_index_conflicting_duplicates_returns_404(self, client):
        with patch("api.main._fetch_index_kline", return_value=[]):
            resp = client.get("/v1/market/kline?symbol=000001.SH&start_date=2026-01-01&end_date=2026-01-10")
            assert resp.status_code == 404
            assert resp.json()["detail"] == "no kline data"
