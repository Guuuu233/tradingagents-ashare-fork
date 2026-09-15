import pandas as pd
import numpy as np
import pytest

from tradingagents.dataflows.macro_market_utils import (
    calculate_series_metrics,
    build_cn_indices_markdown,
    build_global_indices_markdown,
    build_major_assets_markdown,
)
from tradingagents.dataflows.providers.industry_linkage_provider import (
    IndustryLinkageProvider,
)


def test_calculate_series_metrics_strictly_truncates_at_as_of_date():
    # Construct a daily series spanning 2026-08-01 to 2026-08-20
    dates = pd.date_range("2026-08-01", "2026-08-20", freq="D")
    prices = [100.0 + i * 2.0 for i in range(len(dates))]
    df = pd.DataFrame({"Date": dates, "Close": prices, "Volume": [1000] * len(dates)})

    # Request as of 2026-08-10 (any bar after 08-10 is future lookahead)
    metrics = calculate_series_metrics(df, "2026-08-10", price_col="Close")

    assert metrics is not None
    assert metrics["as_of"] == "2026-08-10"
    # 2026-08-10 is index 9 (100 + 9*2 = 118.0)
    assert metrics["latest_close"] == 118.0
    assert metrics["bars_count"] == 10
    # Future price (e.g. 2026-08-20 with price 138.0) must NOT leak into metrics
    assert metrics["latest_close"] < 120.0


def test_calculate_series_metrics_handles_chinese_columns_and_dedupes():
    df = pd.DataFrame({
        "日期": ["2026-08-01", "2026-08-02", "2026-08-02", "2026-08-03"],
        "收盘": [3000.0, 3050.0, 3050.0, 3100.0],
        "成交量": [5000, 6000, 6500, 7000],
    })

    metrics = calculate_series_metrics(df, "2026-08-03")
    assert metrics is not None
    assert metrics["as_of"] == "2026-08-03"
    assert metrics["latest_close"] == 3100.0
    # Deduplicated: 3 unique daily dates
    assert metrics["bars_count"] == 3
    assert metrics["change_1d_pct"] > 0


def test_calculate_series_metrics_duplicate_contract_is_order_independent():
    same_value_rows = [
        ("2026-08-01", 100.0),
        ("2026-08-02", 105.0),
        ("2026-08-02", 105),
        ("2026-08-03", 110.0),
    ]
    same_value_frames = [
        pd.DataFrame(same_value_rows, columns=["date", "close"]),
        pd.DataFrame(
            [same_value_rows[0], same_value_rows[2], same_value_rows[1], same_value_rows[3]],
            columns=["date", "close"],
        ),
    ]

    same_value_metrics = [
        calculate_series_metrics(frame, "2026-08-03") for frame in same_value_frames
    ]

    assert same_value_metrics[0] == same_value_metrics[1]
    assert same_value_metrics[0]["bars_count"] == 3

    conflict_rows = same_value_rows.copy()
    conflict_rows[2] = ("2026-08-02", 205.0)
    conflict_frames = [
        pd.DataFrame(conflict_rows, columns=["date", "close"]),
        pd.DataFrame(
            [conflict_rows[0], conflict_rows[2], conflict_rows[1], conflict_rows[3]],
            columns=["date", "close"],
        ),
    ]

    assert all(
        calculate_series_metrics(frame, "2026-08-03") is None
        for frame in conflict_frames
    )


def test_calculate_series_metrics_checks_duplicates_within_cutoff_only():
    in_scope_conflict = pd.DataFrame({
        "date": ["2026-08-01", "2026-08-02", "2026-08-02", "2026-08-04"],
        "close": [100.0, 105.0, 205.0, 999.0],
    })
    future_conflict = pd.DataFrame({
        "date": ["2026-08-01", "2026-08-02", "2026-08-04", "2026-08-04"],
        "close": [100.0, 105.0, 120.0, 220.0],
    })

    assert calculate_series_metrics(in_scope_conflict, "2026-08-03") is None
    metrics = calculate_series_metrics(future_conflict, "2026-08-03")
    assert metrics is not None
    assert metrics["as_of"] == "2026-08-02"
    assert metrics["latest_close"] == 105.0


def test_calculate_series_metrics_preserves_zero_and_alias_semantics():
    df = pd.DataFrame({
        "trade_date": ["2026-08-01", "2026-08-01", "2026-08-02"],
        "value": [0, 0.0, 10.0],
    })

    metrics = calculate_series_metrics(df, "2026-08-02", price_col="missing")

    assert metrics is not None
    assert metrics["bars_count"] == 2
    assert metrics["latest_close"] == 10.0
    assert metrics["change_1d_pct"] == 0.0


def test_industry_series_metrics_duplicate_contract_and_cutoff():
    provider = IndustryLinkageProvider()
    dates = pd.date_range("2026-05-01", periods=65, freq="D")
    base = pd.DataFrame({"Date": dates, "Adj Close": range(100, 165)})
    duplicate = base.iloc[[22]].copy()
    duplicate["Adj Close"] = 122.0
    same_value_frames = [
        pd.concat([base.iloc[:23], duplicate, base.iloc[23:]], ignore_index=True),
        pd.concat([base.iloc[:22], duplicate, base.iloc[22:]], ignore_index=True),
    ]

    same_value_metrics = [
        provider._calculate_series_metrics(
            frame,
            as_of="2026-07-04",
            price_col="Adj Close",
            date_col="Date",
        )
        for frame in same_value_frames
    ]

    assert same_value_metrics[0] == same_value_metrics[1]
    assert same_value_metrics[0]["current_value"] == 164.0
    assert same_value_metrics[0]["mom_change"] is not None
    assert same_value_metrics[0]["qoq_change"] is not None

    conflict = duplicate.copy()
    conflict["Adj Close"] = 222.0
    conflict_frames = [
        pd.concat([base.iloc[:23], conflict, base.iloc[23:]], ignore_index=True),
        pd.concat([base.iloc[:22], conflict, base.iloc[22:]], ignore_index=True),
    ]
    assert all(
        provider._calculate_series_metrics(
            frame,
            as_of="2026-07-04",
            price_col="Adj Close",
            date_col="Date",
        ) is None
        for frame in conflict_frames
    )

    future_conflict = pd.DataFrame({
        "date": ["2026-08-01", "2026-08-02", "2026-08-04", "2026-08-04"],
        "close": [0, 10.0, 20.0, 30.0],
    })
    future_metrics = provider._calculate_series_metrics(
        future_conflict, as_of="2026-08-03"
    )
    assert future_metrics is not None
    assert future_metrics["current_value"] == 10.0
    assert future_metrics["mom_change"] is None


def test_calculate_series_metrics_returns_none_on_empty_or_invalid():
    assert calculate_series_metrics(None, "2026-08-10") is None
    assert calculate_series_metrics(pd.DataFrame(), "2026-08-10") is None

    # No dates <= as_of
    df = pd.DataFrame({
        "date": ["2026-08-15", "2026-08-16"],
        "close": [100.0, 101.0],
    })
    assert calculate_series_metrics(df, "2026-08-10") is None

    # Invalid as_of
    assert calculate_series_metrics(df, "invalid-date") is None

    invalid_dates = pd.DataFrame({
        "date": ["not-a-date", None],
        "close": [100.0, 101.0],
    })
    assert calculate_series_metrics(invalid_dates, "2026-08-10") is None


def test_build_cn_indices_markdown_formats_table_and_handles_failures():
    items = {
        "上证指数": {
            "code": "000001.SH",
            "as_of": "2026-08-11",
            "latest_close": 3150.25,
            "change_1d_pct": 0.45,
            "change_5d_pct": 1.20,
            "change_20d_pct": 3.50,
            "trend_desc": "短期偏强(均线上行)",
        },
        "深证成指": {
            "code": "399001.SZ",
            "as_of": "2026-08-11",
            "latest_close": 10500.80,
            "change_1d_pct": -0.15,
            "change_5d_pct": 0.80,
            "change_20d_pct": 2.10,
            "trend_desc": "震荡整理",
        },
    }

    md = build_cn_indices_markdown(items, "2026-08-11", source="cn_akshare")
    assert "## 国内核心大盘指数行情" in md
    assert "【数据日期】2026-08-11" in md
    assert "上证指数" in md
    assert "3150.25" in md
    assert "+0.45%" in md
    assert "-0.15%" in md
    assert "市场综合环境与大盘广度分析" in md


def test_build_cn_indices_markdown_returns_explicit_failure_when_empty():
    md = build_cn_indices_markdown({}, "2026-08-11", source="cn_akshare")
    assert md.startswith("【数据获取失败】国内核心大盘指数")


def test_build_global_indices_markdown_formats_cross_market_view():
    items = {
        "标普500": {
            "code": "^GSPC",
            "as_of": "2026-08-11",
            "latest_close": 5450.20,
            "change_1d_pct": 0.35,
            "change_5d_pct": 1.10,
            "change_20d_pct": 2.80,
            "trend_desc": "多头排列(强势上涨)",
        },
        "恒生指数": {
            "code": "^HSI",
            "as_of": "2026-08-11",
            "latest_close": 17500.00,
            "change_1d_pct": -0.25,
            "change_5d_pct": -0.50,
            "change_20d_pct": 1.20,
            "trend_desc": "震荡整理",
        },
    }

    md = build_global_indices_markdown(items, "2026-08-11", source="yfinance")
    assert "## 全球核心市场指数行情" in md
    assert "【数据日期】2026-08-11" in md
    assert "标普500" in md
    assert "5450.20" in md
    assert "跨市场宏观联动观察" in md


def test_build_major_assets_markdown_formats_commodities_and_yields():
    items = {
        "COMEX黄金": {
            "code": "GC=F",
            "category": "贵金属",
            "as_of": "2026-08-11",
            "latest_close": 2450.50,
            "change_1d_pct": 0.60,
            "change_5d_pct": 1.80,
            "change_20d_pct": 3.40,
            "macro_signal": "避险情绪 / 实际利率映射",
        },
        "美债10年期收益率": {
            "code": "^TNX",
            "category": "主权债券",
            "unit": "%",
            "as_of": "2026-08-11",
            "latest_close": 4.255,
            "change_1d_pct": -0.80,
            "change_5d_pct": -2.10,
            "change_20d_pct": -4.50,
            "macro_signal": "无风险利率锚 / 资产折现率",
        },
    }

    md = build_major_assets_markdown(items, "2026-08-11", source="yfinance")
    assert "## 全球大类资产与宏观大宗商品" in md
    assert "【数据日期】2026-08-11" in md
    assert "COMEX黄金" in md
    assert "2450.50" in md
    assert "4.255%" in md
    assert "宏观大类资产传导机制与情景评估" in md
