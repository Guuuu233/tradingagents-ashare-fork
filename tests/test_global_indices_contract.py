import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from tradingagents.dataflows.macro_market_utils import (
    calculate_series_metrics,
    build_global_indices_markdown,
)
from tradingagents.dataflows.providers.tushare_provider import TushareProvider
from tradingagents.dataflows.providers.registry import build_default_registry
from tradingagents.dataflows.interface import route_to_vendor


class TestGlobalIndicesContract(unittest.TestCase):
    """全球指数契约全覆盖测试套件 (G1-G8 及相关链路契约)。"""

    def test_g1_spx_100x_value_fails_closed_without_division(self):
        """G1: 100 倍样本带标的身份 (ts_code=SPX + 值 767,728) 应落入【数据缺失】，不得自动除以 100。"""
        df = pd.DataFrame({
            "date": ["2026-08-24", "2026-08-25"],
            "close": [765000.0, 767728.0],
        })
        # 传入 instrument="SPX" 进行值域校验
        res = calculate_series_metrics(df, "2026-08-25", instrument="SPX")
        # 期望：异常值被拒绝，返回 None（落入数据缺失），绝不放行 767728 也绝不盲目除以 100 变成 7677.28
        assert res is None, f"Expected None for 100x SPX value 767728, but got {res}"

    def test_g2_actual_as_of_not_later_than_requested(self):
        """G2: 任意查询返回项 actual_as_of 不得晚于请求日。"""
        df = pd.DataFrame({
            "date": ["2026-09-08", "2026-09-09"],
            "close": [5500.0, 5520.0],
        })
        # 查询 2026-09-08，结果日期不得晚于 2026-09-08
        res = calculate_series_metrics(df, "2026-09-08", max_stale_business_days=3)
        assert res is not None
        assert res["as_of"] <= "2026-09-08"

    def test_g3_stale_series_rejected_by_freshness_gate(self):
        """G3: 序列末尾停在 08-21，以 09-10 查询（超出新鲜度阈值），拒绝并落入【数据缺失】。"""
        df = pd.DataFrame({
            "date": ["2026-08-20", "2026-08-21"],
            "close": [44900.0, 44946.64],
        })
        # 境外指数新鲜度阈值建议 2-3 个交易日，08-21 到 09-10 相差 14 个交易日，必须拒绝
        res = calculate_series_metrics(df, "2026-09-10", max_stale_business_days=3)
        assert res is None, f"Expected stale series ending at 08-21 to be rejected for 09-10, got {res}"

    def test_g4_partial_success_marked_partial(self):
        """G4: 10 项中仅 6 项成功，标 partial 并记录缺失项，不得标 verified。"""
        items = {
            "标普500": {"code": "SPX", "latest_close": 5600.0, "as_of": "2026-09-10", "source": "tushare"},
            "纳斯达克综合": {"code": "IXIC", "latest_close": 18000.0, "as_of": "2026-09-10", "source": "tushare"},
            "道琼斯": {"code": "DJI", "latest_close": 41000.0, "as_of": "2026-09-10", "source": "tushare"},
            "恒生指数": {"code": "HSI", "latest_close": 17500.0, "as_of": "2026-09-10", "source": "tushare"},
            "恒生科技指数": {"code": "HKTECH", "latest_close": 3500.0, "as_of": "2026-09-10", "source": "tushare"},
            "日经225": {"code": "N225", "latest_close": 38000.0, "as_of": "2026-09-10", "source": "tushare"},
            "韩国KOSPI": None,
            "德国DAX": None,
            "法国CAC40": None,
            "英国富时100": None,
        }
        md = build_global_indices_markdown(items, "2026-09-10", source="tushare")
        # 必须显式标记 partial 并说明 6/10 成功，且不得标 verified (完整有效)
        assert "【数据状态】partial (部分成功 6/10)" in md
        assert "verified (完整有效)" not in md
        assert "【缺失项】韩国KOSPI, 德国DAX, 法国CAC40, 英国富时100" in md

    def test_g5_tushare_provider_registered_and_maps_hktech(self):
        """G5: Tushare provider 正式注册，且恒生科技映射为 HKTECH（而非 HSTECH）。"""
        reg = build_default_registry()
        ts_provider = reg.get("tushare")
        assert ts_provider is not None, "tushare provider must be registered in default registry"
        # 检查其代码映射
        assert hasattr(ts_provider, "TARGET_SYMBOLS")
        symbols_dict = {name: ts_code for name, ts_code, _disp in ts_provider.TARGET_SYMBOLS}
        assert symbols_dict.get("恒生科技指数") == "HKTECH"
        assert "HSTECH" not in symbols_dict.values()
        assert symbols_dict.get("道琼斯") == "DJI"

    def test_g6_fresh_series_passes_gate(self):
        """G6: 正常新鲜序列仍成功返回，不得因加闸误杀。"""
        df = pd.DataFrame({
            "date": ["2026-09-09", "2026-09-10"],
            "close": [5500.0, 5550.0],
        })
        res = calculate_series_metrics(df, "2026-09-10", instrument="SPX", max_stale_business_days=3)
        assert res is not None
        assert res["latest_close"] == 5550.0
        assert res["as_of"] == "2026-09-10"

    def test_g7_markdown_renders_per_index_as_of_and_source(self):
        """G7: 渲染层：逐指数显示各自 as_of 与 source，不得只显示聚合最大值。"""
        items = {
            "标普500": {
                "code": "SPX",
                "latest_close": 5600.0,
                "change_1d_pct": 0.5,
                "as_of": "2026-09-10",
                "source": "tushare",
            },
            "日经225": {
                "code": "N225",
                "latest_close": 38000.0,
                "change_1d_pct": -0.2,
                "as_of": "2026-09-09",
                "source": "tushare",
            },
        }
        md = build_global_indices_markdown(items, "2026-09-10", source="tushare")
        lines = [line.strip() for line in md.splitlines() if line.strip().startswith("|")]
        spx_line = next((l for l in lines if "标普500" in l), None)
        nikkei_line = next((l for l in lines if "日经225" in l), None)
        assert spx_line is not None and "2026-09-10" in spx_line and "tushare" in spx_line
        assert nikkei_line is not None and "2026-09-09" in nikkei_line and "tushare" in nikkei_line

    def test_g8_fixture_distinct_dates_and_values(self):
        """G8: 人为构造两个不同日期、不同值的 fixture，两个基准日返回各自对应的值。"""
        df = pd.DataFrame({
            "date": ["2026-09-08", "2026-09-09", "2026-09-10"],
            "close": [5500.0, 5550.0, 5600.0],
        })
        res_0908 = calculate_series_metrics(df, "2026-09-08", max_stale_business_days=3)
        res_0910 = calculate_series_metrics(df, "2026-09-10", max_stale_business_days=3)
        assert res_0908 is not None and res_0908["latest_close"] == 5500.0
        assert res_0908["as_of"] == "2026-09-08"
        assert res_0910 is not None and res_0910["latest_close"] == 5600.0
        assert res_0910["as_of"] == "2026-09-10"

    def test_major_assets_not_regressed_by_tushare_macro_chain_switch(self):
        """契约 8: macro_market_data 配置切换为 'tushare,cn_akshare' 后，get_major_assets 不退化。"""
        # TushareProvider 未实现 get_major_assets（抛 NotImplementedError），路由自然回退到 cn_akshare
        mock_ak = MagicMock()
        dates = ["2026-08-08", "2026-08-09", "2026-08-10"]
        df_futures = pd.DataFrame({
            "date": dates,
            "close": [2400.0, 2420.0, 2450.0],
            "open": [2400.0] * 3,
            "high": [2460.0] * 3,
            "low": [2390.0] * 3,
            "volume": [100] * 3,
        })
        mock_ak.futures_foreign_hist.return_value = df_futures
        df_bond = pd.DataFrame({"日期": dates, "美国国债收益率10年": [4.30, 4.28, 4.25]})
        mock_ak.bond_zh_us_rate.return_value = df_bond

        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        CnAkshareProvider.clear_macro_cache()
        with patch.object(CnAkshareProvider, "_ak", return_value=mock_ak):
            res = route_to_vendor("get_major_assets", "2026-08-10")

        assert "## 全球大类资产与宏观大宗商品" in res
        assert "COMEX黄金" in res
        assert "2450.00" in res
        assert "4.250%" in res
        assert "来源：cn_akshare" in res

    def test_tushare_provider_fetches_and_builds_markdown(self):
        """测试 TushareProvider 端到端拉取与渲染（Mock API 响应）。"""
        provider = TushareProvider()
        def mock_query(api_name, ts_code=None, as_of=None, **kwargs):
            if ts_code == "SPX":
                df = pd.DataFrame({
                    "ts_code": ["SPX"] * 3,
                    "trade_date": ["20260908", "20260909", "20260910"],
                    "close": [5500.0, 5550.0, 5600.0],
                })
                return df, None, None
            if ts_code == "HKTECH":
                df = pd.DataFrame({
                    "ts_code": ["HKTECH"] * 3,
                    "trade_date": ["20260908", "20260909", "20260910"],
                    "close": [3800.0, 3850.0, 3900.0],
                })
                return df, None, None
            # 其余返回 None
            return None, "empty_rows", "empty"

        with patch("tradingagents.dataflows.providers.tushare_provider._get_tushare_token", return_value="dummy_token"), \
             patch("tradingagents.dataflows.providers.tushare_provider._query_tushare_api", side_effect=mock_query):
            res_md = provider.get_global_indices(curr_date="2026-09-10")

        assert "## 全球核心市场指数行情" in res_md
        assert "【数据状态】partial (部分成功 2/10)" in res_md
        assert "标普500" in res_md
        assert "5600.00" in res_md
        assert "恒生科技指数" in res_md
        assert "3900.00" in res_md
        assert "HKTECH" in res_md
        assert "来源：tushare" in res_md

    def test_tushare_missing_token_falls_back_to_akshare(self):
        """🔴-1: Tushare Token 未配置时，TushareProvider 返回 VendorFail，链路自动 fallback 到 cn_akshare。"""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        CnAkshareProvider.clear_macro_cache()

        dates = ["2026-08-08", "2026-08-09", "2026-08-10"]
        df_hist = pd.DataFrame({
            "date": dates,
            "close": [5400.0, 5420.0, 5450.0],
            "open": [5400.0] * 3,
            "high": [5460.0] * 3,
            "low": [5390.0] * 3,
            "volume": [1000] * 3,
        })
        mock_ak = MagicMock()
        mock_ak.index_global_hist_em.return_value = df_hist

        with patch("tradingagents.dataflows.providers.tushare_provider._get_tushare_token", return_value=""), \
             patch.object(CnAkshareProvider, "_ak", return_value=mock_ak):
            res = route_to_vendor("get_global_indices", "2026-08-10")

        # 必须由 cn_akshare 成功兜底响应，而不是被 tushare 终止性报错截断
        assert "## 全球核心市场指数行情" in res
        assert "来源：cn_akshare" in res
        assert "5450.00" in res

    def test_tushare_all_fail_falls_back_to_akshare(self):
        """🔴-1: Tushare 接口调用全部失败时，返回 VendorFail，链路自动 fallback 到 cn_akshare。"""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        CnAkshareProvider.clear_macro_cache()

        dates = ["2026-08-08", "2026-08-09", "2026-08-10"]
        df_hist = pd.DataFrame({
            "date": dates,
            "close": [5400.0, 5420.0, 5450.0],
            "open": [5400.0] * 3,
            "high": [5460.0] * 3,
            "low": [5390.0] * 3,
            "volume": [1000] * 3,
        })
        mock_ak = MagicMock()
        mock_ak.index_global_hist_em.return_value = df_hist

        with patch("tradingagents.dataflows.providers.tushare_provider._get_tushare_token", return_value="dummy_token"), \
             patch("tradingagents.dataflows.providers.tushare_provider._query_tushare_api", return_value=(None, "timeout", "network timeout")), \
             patch.object(CnAkshareProvider, "_ak", return_value=mock_ak):
            res = route_to_vendor("get_global_indices", "2026-08-10")

        assert "## 全球核心市场指数行情" in res
        assert "来源：cn_akshare" in res
        assert "5450.00" in res

    def test_akshare_snapshot_range_guard_rejects_100x_value(self):
        """🟡-1: AkShare 快照链路（如 Sina / EM）也受标的值域区间防线约束，拒绝 100 倍畸变值。"""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        mock_ak = MagicMock()
        mock_ak.index_global_hist_em.side_effect = RuntimeError("hist failed")
        mock_ak.stock_hk_index_daily_em.side_effect = RuntimeError("hist failed")

        # 构造包含 100 倍畸变值的 Sina snapshot (标普 767728.0)
        mock_sina_data = {
            "标普500": {
                "name": "标普500",
                "code": ".INX",
                "latest_close": 767728.0,  # 100x 畸变值
                "change_1d_pct": 0.50,
                "as_of": "2026-08-21",
                "source": "sina_hq",
            },
            "日经225": {
                "name": "日经225",
                "code": "N225",
                "latest_close": 38000.0,  # 正常值
                "change_1d_pct": -0.20,
                "as_of": "2026-08-21",
                "source": "sina_hq",
            },
        }

        with patch.object(provider, "_fetch_global_indices_sina_hq", return_value=mock_sina_data), \
             patch.object(provider, "_fetch_global_indices_em_ulist", return_value={}), \
             patch.object(provider, "_ak", return_value=mock_ak):
            res_md = provider.get_global_indices(curr_date="2026-08-21")

        # 标普 500 的畸变值被拒绝，显示为【数据缺失】，而日经 225 正常保留
        assert "38000.00" in res_md
        assert "767728" not in res_md
        lines = [l.strip() for l in res_md.splitlines() if l.strip().startswith("|")]
        spx_line = next((l for l in lines if "标普500" in l), None)
        assert spx_line is not None and "【数据缺失】" in spx_line
