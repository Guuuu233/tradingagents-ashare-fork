"""DAV-1277 全球指数取数韧性测试：

1. Tushare 串行超时→整批丢弃修复为有界并发 + 内部总时限 + 部分结果保留；
2. ≥1 个有效指数即返回部分结果 markdown，全败才 VendorFail；
3. 新浪 int_*/b_* 海外快照自带日期早于分析日 >3 个交易日一律拒收。
"""

import time
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from tradingagents.dataflows.providers.tushare_provider import TushareProvider
from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
from tradingagents.dataflows.vendor_result import VendorFail


# 各标的符合值域防线（GLOBAL_INDICES_REASONABLE_RANGES）的收盘价
_PLAUSIBLE_CLOSE = {
    "SPX": 5600.0,
    "IXIC": 18000.0,
    "DJI": 41000.0,
    "HSI": 17500.0,
    "HKTECH": 3900.0,
    "N225": 38000.0,
    "KS11": 2600.0,
    "GDAXI": 18500.0,
    "FCHI": 7300.0,
    "FTSE": 8300.0,
}


def _valid_df(ts_code: str) -> pd.DataFrame:
    close = _PLAUSIBLE_CLOSE.get(ts_code, 5600.0)
    return pd.DataFrame(
        {
            "ts_code": [ts_code] * 3,
            "trade_date": ["20260908", "20260909", "20260910"],
            "close": [close - 20.0, close - 10.0, close],
        }
    )


def _mock_query_factory(sleep_codes=(), sleep_s=0.0, ok_codes=("SPX",)):
    def _mock(api_name, ts_code=None, as_of=None, **kwargs):
        if ts_code in sleep_codes:
            time.sleep(sleep_s)
            return None, "timeout", "simulated gateway read timeout"
        if ts_code in ok_codes:
            return _valid_df(ts_code), None, None
        return None, "empty_rows", "empty"

    return _mock


class TestTushareGlobalIndicesConcurrency(unittest.TestCase):
    def test_serial_timeout_scenario_returns_partial_results(self):
        """串行超时场景修复：个别指数超时不再整批丢弃，已拿到的部分结果照常返回。

        旧行为：IXIC/HKTECH 两个读超时把串行总耗时推出 vendor 30s 预算，
        上层 future.result(timeout) 抛 TimeoutError，8 个已得指数全部丢弃。
        新行为：provider 内部总时限内完成的直接入账，超时标的落【数据缺失】。
        """
        provider = TushareProvider()
        mock = _mock_query_factory(
            sleep_codes=("IXIC", "HKTECH"),
            sleep_s=3.0,
            ok_codes=("SPX", "DJI", "HSI", "N225", "KS11", "GDAXI", "FCHI", "FTSE"),
        )
        start = time.monotonic()
        with patch(
            "tradingagents.dataflows.providers.tushare_provider._get_tushare_token",
            return_value="dummy_token",
        ), patch(
            "tradingagents.dataflows.providers.tushare_provider._query_tushare_api",
            side_effect=mock,
        ), patch(
            "tradingagents.dataflows.providers.tushare_provider._GLOBAL_INDICES_BUDGET_S",
            0.5,
        ):
            res = provider.get_global_indices(curr_date="2026-09-10")
        elapsed = time.monotonic() - start

        assert not isinstance(res, VendorFail), f"expected partial markdown, got {res}"
        assert "## 全球核心市场指数行情" in res
        assert "【数据状态】partial (部分成功 8/10)" in res
        assert "5600.00" in res  # SPX
        assert "41000.00" in res  # DJI
        # 总耗时收在内部时限内（0.5s 预算 + 收尾余量），绝不因后台慢请求阻塞
        assert elapsed < 3.0, f"provider call took {elapsed:.2f}s, budget not enforced"

    def test_single_valid_index_returns_partial_not_vendorfail(self):
        """≥1 个有效即返回部分结果 markdown（缺项标【数据缺失】），不得 VendorFail。"""
        provider = TushareProvider()
        mock = _mock_query_factory(ok_codes=("KS11",))
        with patch(
            "tradingagents.dataflows.providers.tushare_provider._get_tushare_token",
            return_value="dummy_token",
        ), patch(
            "tradingagents.dataflows.providers.tushare_provider._query_tushare_api",
            side_effect=mock,
        ):
            res = provider.get_global_indices(curr_date="2026-09-10")

        assert not isinstance(res, VendorFail)
        assert "【数据状态】partial (部分成功 1/10)" in res
        assert "韩国KOSPI" in res
        assert "2600.00" in res
        assert "【数据缺失】" in res

    def test_all_fail_returns_vendorfail(self):
        """全部指数失败时仍返回 VendorFail，路由继续回落 cn_akshare。"""
        provider = TushareProvider()
        with patch(
            "tradingagents.dataflows.providers.tushare_provider._get_tushare_token",
            return_value="dummy_token",
        ), patch(
            "tradingagents.dataflows.providers.tushare_provider._query_tushare_api",
            return_value=(None, "timeout", "network timeout"),
        ):
            res = provider.get_global_indices(curr_date="2026-09-10")
        assert isinstance(res, VendorFail)

    def test_concurrency_bound_respected(self):
        """并发上限 = policy max_concurrency(4)：同时在跑的 index_global 调用不超过 4。"""
        provider = TushareProvider()
        in_flight = 0
        max_in_flight = 0

        def _mock(api_name, ts_code=None, as_of=None, **kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            time.sleep(0.05)
            in_flight -= 1
            return _valid_df(ts_code or "SPX"), None, None

        with patch(
            "tradingagents.dataflows.providers.tushare_provider._get_tushare_token",
            return_value="dummy_token",
        ), patch(
            "tradingagents.dataflows.providers.tushare_provider._query_tushare_api",
            side_effect=_mock,
        ):
            res = provider.get_global_indices(curr_date="2026-09-10")

        assert not isinstance(res, VendorFail)
        assert "【数据状态】verified" in res or "partial" in res
        assert max_in_flight <= 4, f"concurrency bound violated: {max_in_flight}"


class TestSinaIntSymbolsDisabled(unittest.TestCase):
    """总控裁定：int_* 报文无日期字段，整体停用，一律不进入结果、不得合成日期。"""

    def test_int_symbols_never_enter_results_live_mode(self):
        """live（非历史）模式下 int_* 陈旧旧值不得入账——这正是生产失真路径。"""
        provider = CnAkshareProvider()
        response_text = (
            'var hq_str_int_sp500="标普指数,6643.70,38.98,0.59";\n'
            'var hq_str_int_nasdaq="纳斯达克,22484.07,99.37,0.44";\n'
            'var hq_str_int_dji="道琼斯,46247.29,299.97,0.65";\n'
            'var hq_str_int_nikkei="日经指数,44946.64,-408.35,-0.90";\n'
            'var hq_str_b_FTSE="富时100指数,10816.5600,68.40,0.64,,,2026-09-25,23:35:00";\n'
        )
        mock_resp = MagicMock(text=response_text, encoding="gbk")

        with patch("requests.get", return_value=mock_resp), patch(
            "tradingagents.dataflows.providers.cn_akshare_provider.is_historical_analysis_date",
            return_value=False,
        ), patch(
            "tradingagents.dataflows.providers.cn_akshare_provider._get_latest_us_session_date",
            return_value="2026-09-25",
        ):
            snapshots = provider._fetch_global_indices_sina_hq(curr_date="2026-09-25")

        for idx in ("标普500", "纳斯达克综合", "道琼斯", "日经225"):
            assert idx not in snapshots, f"{idx} must not enter results (int_* disabled)"
        assert "英国富时100" in snapshots

    def test_b_dax_two_dates_picks_last_timed_quote_datetime(self):
        """b_DAX 真实报文格式 fixture：同时含裸日期 '2025-09-26'（静态字段，无
        时间跟随）与 '2026-08-22 00:00:35'（行情时间戳）。口径：取报文中最后一
        组「日期+时间」对——带时间的日期是逐秒变化的行情时间戳（CST 00:00:35
        ≈ 柏林 08-21 18:00，与 DAX 收盘后快照一致），无时间的裸日期不参与
        as_of。因此 as_of = 2026-08-21（Europe/Berlin），而非 2025-09-26。"""
        provider = CnAkshareProvider()
        response_text = (
            'var hq_str_b_DAX="德国DAX指数,26136.5605,153.52,0.59,9/26/2025,'
            "2025-09-26,2026-08-22,00:00:35,25998.9492,25983.0391,26166.2500,"
            '25969.5195,0";\n'
        )
        mock_resp = MagicMock(text=response_text, encoding="gbk")

        with patch("requests.get", return_value=mock_resp):
            snapshots = provider._fetch_global_indices_sina_hq(curr_date="2026-08-22")

        assert snapshots["德国DAX"]["as_of"] == "2026-08-21"
        assert snapshots["德国DAX"]["latest_close"] == 26136.5605


class TestSinaStaleGlobalSnapshotRejection(unittest.TestCase):
    """新浪 int_*/b_* 海外快照自带日期早于分析日 >3 个交易日一律拒收。"""

    def test_stale_b_symbol_rejected_fresh_kept(self):
        """b_DAX 停在 2026-08-10（距 08-21 共 9 个工作日）→ 拒收；
        b_FTSE 自带 2026-08-21 → 保留。"""
        provider = CnAkshareProvider()
        response_text = (
            'var hq_str_b_DAX="德国DAX指数,26136.5605,153.52,0.59,,,2026-08-10,16:35:00";\n'
            'var hq_str_b_FTSE="富时100指数,10816.5600,68.40,0.64,,,2026-08-21,23:35:00";\n'
        )
        mock_resp = MagicMock(text=response_text, encoding="gbk")

        with patch("requests.get", return_value=mock_resp):
            snapshots = provider._fetch_global_indices_sina_hq(curr_date="2026-08-21")

        assert "德国DAX" not in snapshots, "stale b_DAX snapshot must be rejected"
        assert snapshots["英国富时100"]["as_of"] == "2026-08-21"

    def test_boundary_three_business_days_kept(self):
        """边界：自带日期距分析日恰好 3 个工作日（08-18→08-21）→ 不拒收。"""
        provider = CnAkshareProvider()
        response_text = (
            'var hq_str_b_CAC="法国CAC40指数,8123.41,30.0,0.37,,,2026-08-18,17:30:00";\n'
        )
        mock_resp = MagicMock(text=response_text, encoding="gbk")

        with patch("requests.get", return_value=mock_resp):
            snapshots = provider._fetch_global_indices_sina_hq(curr_date="2026-08-21")

        assert "法国CAC40" in snapshots, "snapshot exactly 3 business days stale must be kept"
        assert snapshots["法国CAC40"]["as_of"] == "2026-08-18"

    def test_four_business_days_stale_rejected(self):
        """自带日期距分析日 4 个工作日（08-17→08-21）→ 拒收。"""
        provider = CnAkshareProvider()
        response_text = (
            'var hq_str_b_KOSPI="韩国KOSPI,7080.92,10.0,0.14,,,2026-08-17,15:30:00";\n'
        )
        mock_resp = MagicMock(text=response_text, encoding="gbk")

        with patch("requests.get", return_value=mock_resp):
            snapshots = provider._fetch_global_indices_sina_hq(curr_date="2026-08-21")

        assert "韩国KOSPI" not in snapshots

    def test_undated_b_symbol_rejected_even_in_live_mode(self):
        """b_* 报文无日期字段时不得合成 curr_date 绕过陈旧拒收（live 模式同拒）。"""
        provider = CnAkshareProvider()
        response_text = (
            'var hq_str_b_DAX="德国DAX指数,26136.5605,153.52,0.59,,";\n'
        )
        mock_resp = MagicMock(text=response_text, encoding="gbk")

        with patch("requests.get", return_value=mock_resp), patch(
            "tradingagents.dataflows.providers.cn_akshare_provider.is_historical_analysis_date",
            return_value=False,
        ):
            snapshots = provider._fetch_global_indices_sina_hq(curr_date="2026-09-25")

        assert "德国DAX" not in snapshots
