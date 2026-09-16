import time
import pytest
from unittest.mock import MagicMock, patch

from tradingagents.dataflows.providers.cn_baostock_provider import CnBaoStockProvider
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.providers.base import ProviderResourcePolicy


def test_baostock_unreachable_server_raises_not_implemented_immediately():
    """When baostock probe connection fails or is refused, CnBaoStockProvider raises NotImplementedError fast."""
    provider = CnBaoStockProvider()
    t0 = time.perf_counter()
    with pytest.raises(NotImplementedError, match="baostock server unreachable"):
        provider.get_stock_data("600519", "2026-03-30", "2026-03-31")
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5


def test_baostock_login_failure_raises_not_implemented_immediately():
    """When baostock login fails, CnBaoStockProvider raises NotImplementedError fast."""
    provider = CnBaoStockProvider()
    fake_bs = MagicMock()
    fake_lg = MagicMock()
    fake_lg.error_code = "10002007"
    fake_lg.error_msg = "网络接收错误。"
    fake_bs.login.return_value = fake_lg

    t0 = time.perf_counter()
    with patch.object(provider, "_probe_server", return_value=None), \
         patch.object(provider, "_bs", return_value=fake_bs):
        with pytest.raises(NotImplementedError, match="baostock login failed: 网络接收错误。"):
            provider.get_stock_data("600519", "2026-03-30", "2026-03-31")
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5


def test_baostock_login_exception_raises_not_implemented_immediately():
    """When baostock login raises exception, it raises NotImplementedError fast."""
    provider = CnBaoStockProvider()
    fake_bs = MagicMock()
    fake_bs.login.side_effect = RuntimeError("Socket connection refused")

    t0 = time.perf_counter()
    with patch.object(provider, "_probe_server", return_value=None), \
         patch.object(provider, "_bs", return_value=fake_bs):
        with pytest.raises(NotImplementedError, match="baostock login error"):
            provider.get_stock_data("600519", "2026-03-30", "2026-03-31")
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5


def test_baostock_query_failure_raises_not_implemented_immediately():
    """When baostock query fails, it raises NotImplementedError fast."""
    provider = CnBaoStockProvider()
    fake_bs = MagicMock()
    fake_lg = MagicMock()
    fake_lg.error_code = "0"
    fake_bs.login.return_value = fake_lg
    fake_rs = MagicMock()
    fake_rs.error_code = "10004011"
    fake_rs.error_msg = "股票代码无效"
    fake_bs.query_history_k_data_plus.return_value = fake_rs

    t0 = time.perf_counter()
    with patch.object(provider, "_probe_server", return_value=None), \
         patch.object(provider, "_bs", return_value=fake_bs):
        with pytest.raises(NotImplementedError, match="baostock query failed"):
            provider.get_stock_data("600519", "2026-03-30", "2026-03-31")
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5


def test_route_to_vendor_baostock_fallback_fast():
    """route_to_vendor falls back immediately without retrying when baostock login fails."""
    t0 = time.perf_counter()
    with patch("tradingagents.dataflows.interface._resolve_vendor_chain", return_value=["cn_baostock"]):
        with pytest.raises(RuntimeError, match="No available vendor"):
            route_to_vendor("get_stock_data", "600519", "2026-03-30", "2026-03-31")
    elapsed = time.perf_counter() - t0
    # cn_baostock fallback must be instant, well under 0.5s (previously 90.0s)
    assert elapsed < 0.5


def test_connection_refused_error_falls_back_immediately():
    """ConnectionRefusedError falls back immediately without consuming retry loops."""
    calls = []

    class DummyRefusedProvider:
        def get_stock_data(self, symbol, start_date, end_date):
            calls.append(1)
            raise ConnectionRefusedError("Connection refused by target host")

    with patch("tradingagents.dataflows.interface._registry") as mock_reg, \
         patch("tradingagents.dataflows.interface._resolve_vendor_chain", return_value=["dummy_refused"]):
        mock_reg.get.return_value = DummyRefusedProvider()
        mock_reg.resource_policy.return_value = ProviderResourcePolicy(
            timeout_seconds=5.0,
            max_retries=2,
            max_concurrency=1,
        )
        t0 = time.perf_counter()
        with pytest.raises(RuntimeError, match="No available vendor"):
            route_to_vendor("get_stock_data", "600519", "2026-03-30", "2026-03-31")
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5
        # Must only call ONCE (no retries)
        assert len(calls) == 1
