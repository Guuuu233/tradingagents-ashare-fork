import unittest
import pandas as pd
import pytest

from tradingagents.dataflows.macro_market_utils import (
    calculate_series_metrics,
    build_global_indices_markdown,
)
from tradingagents.dataflows.interface import route_to_vendor


class TestGlobalIndicesContractRED(unittest.TestCase):
    """RED 用例：验证全球指数契约 G1-G8 在当前代码基线上的表现。

    在当前基线上，以下测试应当失败（RED），证明缺陷真实存在。
    """

    def test_g1_spx_100x_value_fails_closed_without_division(self):
        """G1: 100 倍样本带标的身份 (ts_code=SPX + 值 767,728) 应落入【数据缺失】，不得自动除以 100。"""
        df = pd.DataFrame({
            "date": ["2026-08-24", "2026-08-25"],
            "close": [765000.0, 767728.0],
        })
        # 传入 instrument="SPX" 或 instrument contract 进行值域校验
        res = calculate_series_metrics(df, "2026-08-25", instrument="SPX")
        # 期望：异常值被拒绝，返回 None（落入数据缺失），而不是放行 767728 也不是自动 /100 变成 7677.28
        assert res is None, f"Expected None for 100x SPX value 767728, but got {res}"

    def test_g2_actual_as_of_not_later_than_requested(self):
        """G2: 任意查询返回项 actual_as_of 不得晚于请求日。"""
        df = pd.DataFrame({
            "date": ["2026-09-08", "2026-09-09"],
            "close": [5500.0, 5520.0],
        })
        # 查询 2026-09-08，结果日期不得晚于 2026-09-08
        res = calculate_series_metrics(df, "2026-09-08")
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
        # 必须显式标记 partial 并说明 6/10 成功
        assert "【数据状态】partial" in md or "部分成功 (6/10)" in md or "6/10 项成功" in md
        assert "缺失项" in md

    def test_g5_tushare_provider_registered_and_maps_hktech(self):
        """G5: Tushare provider 正式注册，且恒生科技映射为 HKTECH（而非 HSTECH）。"""
        from tradingagents.dataflows.providers.registry import build_default_registry
        reg = build_default_registry()
        ts_provider = reg.get("tushare")
        assert ts_provider is not None, "tushare provider must be registered in default registry"
        # 检查其代码映射
        assert hasattr(ts_provider, "TARGET_SYMBOLS")
        symbols_dict = {name: ts_code for name, ts_code, _disp in ts_provider.TARGET_SYMBOLS}
        assert symbols_dict.get("恒生科技指数") == "HKTECH"
        assert "HSTECH" not in symbols_dict.values()

    def test_g6_fresh_series_passes_gate(self):
        """G6: 正常新鲜序列仍成功返回，不得因加闸误杀。"""
        df = pd.DataFrame({
            "date": ["2026-09-09", "2026-09-10"],
            "close": [5500.0, 5550.0],
        })
        res = calculate_series_metrics(df, "2026-09-10", max_stale_business_days=3)
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
        # 表格中每一行必须包含各自的 actual_as_of 和 source
        lines = [line.strip() for line in md.splitlines() if line.strip().startswith("|")]
        # 寻找标普500所在行和日经225所在行
        spx_line = next((l for l in lines if "标普500" in l), None)
        nikkei_line = next((l for l in lines if "日经225" in l), None)
        assert spx_line is not None and "2026-09-10" in spx_line
        assert nikkei_line is not None and "2026-09-09" in nikkei_line

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
