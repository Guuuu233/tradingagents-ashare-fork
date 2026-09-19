"""DAV-1099 卡 3：Tushare 财报三表接入契约测试。

覆盖红线：
- 重试机制：瞬时错误退避重试，非瞬时错误立即 fail-closed；
- 弹性超时：财报请求使用 (connect=3s, read=12s) 拆分超时；
- PIT 边界：严格按 ann_date <= curr_date 过滤；
- 重复报告期：完全相同行折叠，同报告期不同值 fail-closed【数据缺失】；
- Fail-Closed 降级：任何失败返回 VendorFail，绝不抛出未捕获异常。
"""
import unittest
from unittest.mock import patch

import pandas as pd

import tradingagents.dataflows.providers.tushare_provider as tp
from tradingagents.dataflows.providers.tushare_provider import TushareProvider
from tradingagents.dataflows.vendor_result import VendorFail


def _income_row(end_date, ann_date, revenue, report_type="1", f_ann_date=None):
    return {
        "ts_code": "600036.SH",
        "ann_date": ann_date,
        "f_ann_date": f_ann_date or ann_date,
        "end_date": end_date,
        "report_type": report_type,
        "update_flag": "1",
        "basic_eps": 1.5,
        "total_revenue": revenue,
        "revenue": revenue,
        "operate_profit": revenue * 0.4,
        "total_profit": revenue * 0.4,
        "n_income": revenue * 0.35,
        "n_income_attr_p": revenue * 0.35,
    }


def _df(rows):
    return pd.DataFrame(rows)


class _PatchBase(unittest.TestCase):
    def setUp(self):
        self.provider = TushareProvider()
        self.patches = [
            patch.object(tp, "_get_tushare_token", return_value="fake-token"),
            patch.object(tp, "_FINANCIAL_RETRY_BACKOFF_S", 0),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])


class TestFinancialRetryAndTimeout(_PatchBase):
    def test_transient_timeout_retried_then_success(self):
        """瞬时 timeout 必须退避重试至少 1 次，第二轮成功即返回数据。"""
        good = _df([_income_row("20250630", "20250801", 100.0)])
        calls = {"n": 0}

        def fake(api_name, ts_code=None, as_of=None, fields=None, params=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return None, "timeout", "read timed out"
            return good, None, None

        with patch.object(tp, "_query_tushare_api", side_effect=fake):
            result = self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert calls["n"] == 2
        assert not isinstance(result, VendorFail)
        assert "Income Statement" in result

    def test_non_retryable_error_fails_immediately(self):
        """403/api_error 等非瞬时错误不得重试，立即 VendorFail。"""
        calls = {"n": 0}

        def fake(**kwargs):
            calls["n"] += 1
            return None, "403", "permission denied"

        with patch.object(tp, "_query_tushare_api", side_effect=fake):
            result = self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert calls["n"] == 1
        assert isinstance(result, VendorFail)

    def test_persistent_timeout_returns_vendorfail(self):
        """两轮均超时 → VendorFail（链路降级备用源），不得抛异常。"""
        with patch.object(
            tp, "_query_tushare_api", return_value=(None, "timeout", "t/o")
        ):
            result = self.provider.get_balance_sheet("600036.SH", curr_date="2025-09-01")
        assert isinstance(result, VendorFail)

    def test_split_timeout_applied(self):
        """财报请求必须使用 (connect=3s, read=12s) 拆分超时。"""
        captured = {}

        def fake(api_name, ts_code=None, as_of=None, fields=None, params=None, timeout=None):
            captured["timeout"] = timeout
            return _df([_income_row("20250630", "20250801", 1.0)]), None, None

        with patch.object(tp, "_query_tushare_api", side_effect=fake):
            self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert captured["timeout"] == (3.0, 12.0)


class TestDuplicatePeriodContract(_PatchBase):
    def test_identical_duplicate_rows_collapsed(self):
        """完全相同的重复行可折叠，报告期只出现一次。"""
        row = _income_row("20250630", "20250801", 100.0)
        df = _df([row, dict(row), _income_row("20250331", "20250420", 80.0)])
        with patch.object(tp, "_query_tushare_api", return_value=(df, None, None)):
            result = self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert not isinstance(result, VendorFail)
        assert result.count("| 2025-06-30") == 1  # 表内仅一行（标题行另有一次属正常）
        assert "【数据缺失】" not in result

    def test_same_period_different_values_fail_closed(self):
        """RED 用例：同报告期不同值必须 fail-closed 为【数据缺失】。"""
        df = _df([
            _income_row("20250630", "20250801", 100.0),
            _income_row("20250630", "20250801", 999.0),  # 同期不同值冲突
            _income_row("20250331", "20250420", 80.0),
        ])
        with patch.object(tp, "_query_tushare_api", return_value=(df, None, None)):
            result = self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert "【数据缺失】" in result
        assert "999" not in result  # 冲突值不得泄漏进输出

    def test_all_periods_conflicting_returns_vendorfail(self):
        """全部报告期冲突 → VendorFail 降级备用源。"""
        df = _df([
            _income_row("20250630", "20250801", 100.0),
            _income_row("20250630", "20250801", 200.0),
        ])
        with patch.object(tp, "_query_tushare_api", return_value=(df, None, None)):
            result = self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert isinstance(result, VendorFail)


class TestPITBoundary(_PatchBase):
    def test_rows_announced_after_as_of_excluded(self):
        """公告日晚于基准日的行必须被 PIT 过滤（防前视偏差）。"""
        df = _df([
            _income_row("20250630", "20250801", 100.0),   # 公告日 <= 基准日，可见
            _income_row("20250930", "20251030", 120.0),   # 公告日 > 基准日，不可见
        ])
        with patch.object(tp, "_query_tushare_api", return_value=(df, None, None)):
            result = self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert not isinstance(result, VendorFail)
        assert "2025-09-30" not in result
        assert "2025-06-30" in result

    def test_missing_ann_date_rows_excluded(self):
        """公告日缺失的行无法验证 PIT，必须剔除；剔除后无数据则 VendorFail。"""
        df = _df([
            {**_income_row("20250630", "20250801", 100.0), "ann_date": None,
             "f_ann_date": None},
        ])
        df.loc[0, "f_ann_date"] = None
        with patch.object(tp, "_query_tushare_api", return_value=(df, None, None)):
            result = self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert isinstance(result, VendorFail)


class TestFailClosedGuards(_PatchBase):
    def test_missing_token_vendorfail(self):
        for p in self.patches:
            p.stop()
        with patch.object(tp, "_get_tushare_token", return_value=""):
            result = self.provider.get_cashflow("600036", curr_date="2025-09-01")
        assert isinstance(result, VendorFail)
        self.patches = [
            patch.object(tp, "_get_tushare_token", return_value="fake-token"),
        ]
        self.patches[0].start()

    def test_missing_curr_date_vendorfail(self):
        result = self.provider.get_income_statement("600036", curr_date=None)
        assert isinstance(result, VendorFail)

    def test_non_ashare_ticker_vendorfail(self):
        result = self.provider.get_income_statement("AAPL", curr_date="2025-09-01")
        assert isinstance(result, VendorFail)

    def test_unexpected_exception_fail_closed(self):
        """底层抛未预期异常也必须 fail-closed，不得向上传播。"""
        with patch.object(tp, "_query_tushare_api", side_effect=RuntimeError("boom")):
            result = self.provider.get_balance_sheet("600036", curr_date="2025-09-01")
        assert isinstance(result, VendorFail)

    def test_ticker_normalization(self):
        captured = {}

        def fake(api_name, ts_code=None, **kwargs):
            captured["ts_code"] = ts_code
            return _df([_income_row("20250630", "20250801", 1.0)]), None, None

        with patch.object(tp, "_query_tushare_api", side_effect=fake):
            self.provider.get_income_statement("000001", curr_date="2025-09-01")
        assert captured["ts_code"] == "000001.SZ"


class TestFreqAndRouting(_PatchBase):
    def test_annual_freq_filters_q_reports(self):
        """annual 频率只保留 12-31 年报报告期。"""
        df = _df([
            _income_row("20250630", "20250801", 100.0),
            _income_row("20241231", "20250315", 300.0),
        ])
        with patch.object(tp, "_query_tushare_api", return_value=(df, None, None)):
            result = self.provider.get_income_statement(
                "600036", freq="annual", curr_date="2025-09-01"
            )
        assert not isinstance(result, VendorFail)
        assert "2024-12-31" in result
        assert "2025-06-30" not in result

    def test_fundamental_data_chain_head_is_tushare(self):
        from tradingagents.default_config import DEFAULT_CONFIG
        chain = DEFAULT_CONFIG["data_vendors"]["fundamental_data"]
        assert chain.split(",")[0] == "tushare"

    def test_route_to_vendor_falls_back_on_tushare_fail(self):
        """路由层集成：tushare VendorFail 后链路继续到 cn_fuyao 等备用源。"""
        from tradingagents.dataflows.interface import _resolve_vendor_chain
        chain = _resolve_vendor_chain(
            "get_income_statement",
            "tushare,cn_fuyao,cn_akshare,cn_baostock,cn_investoday,yfinance",
        )
        assert chain[0] == "tushare"
        assert "cn_akshare" in chain


if __name__ == "__main__":
    unittest.main()
