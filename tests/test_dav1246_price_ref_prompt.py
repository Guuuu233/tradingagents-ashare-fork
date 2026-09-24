"""DAV-1246 A 部分单元测试 — 零 LLM 抽取补漏。

A1 影线长度不登记（两类写法 + 两条保护性反例 fixture）；
A2 C5 别名「牛熊线」→ close_200_sma。
（B 部分提示词注入按总控裁决不合入，对应测试不在本提交。）
"""

from __future__ import annotations

import pytest

from tradingagents.agents.utils.price_ref_registry import (
    build_market_data_pool,
    build_price_ref_registry,
    named_field_hits,
)

STOCK_DATA = (
    "# Stock data for 002475.SZ\n"
    "# price_basis: vendor_qfq\n"
    "date,low,close,volume,open,high\n"
    "2026-08-10,55.00,57.50,1000.0,55.30,57.86\n"
    "2026-08-11,54.52,55.90,1100.0,57.60,56.23\n"
    "2026-08-12,55.30,56.80,1200.0,55.90,57.58\n"
    "2026-08-13,56.05,57.90,1300.0,56.70,58.75\n"
    "2026-08-14,55.80,56.58,1400.0,56.60,56.88\n"
)

INDICATORS = {
    "close_10_ema": 56.73,
    "close_50_sma": 62.46,
    "close_200_sma": 59.38,
    "vwma": 56.95,
    "boll_ub": 63.05,
    "boll": 57.93,
    "boll_lb": 52.81,
    "atr": 3.26,
    "rsi": 44.58,
    "macd": -1.85,
}

SYMBOL = "002475.SZ"
TRADE_DATE = "2026-08-14"
SOURCE = {"stock_data": STOCK_DATA, "indicators": dict(INDICATORS)}


def _refs(text, field="news_report", source=None):
    pool = None
    if source is not None:
        pool = build_market_data_pool(source, SYMBOL, TRADE_DATE)
    return build_price_ref_registry({field: text}, cutoff=TRADE_DATE, pool=pool)


# ---------------------------------------------------------------------------
# A1 — 影线长度不登记
# ---------------------------------------------------------------------------

class TestA1ShadowLengthNotRegistered:
    @pytest.mark.parametrize("text", [
        "K线留下 0.49 元的上影线",
        "上影线留有 0.40 元的下影线",
        "形成 0.41 元的上影线",
        "引发 1.79 元的下影线",
        "长达 0.57 元的上影线",
        "上影线达 1.53 元",
        "下影线长约 11.90 元",
        "上影线长度约 0.20 元",
        "留下了 20.26 元的长下影线",
        "回落留下 0.49 元上影线",
    ])
    def test_shadow_length_values_not_registered(self, text):
        refs = _refs(text)["price_refs"]
        assert refs == [], f"影线长度不应登记: {text} -> {[r['value'] for r in refs]}"

    def test_shadow_level_left_behind_still_registered(self):
        """「留下的 35.36 元上影线供应」— 35.36 是真实价位坐标，必须登记。"""
        refs = _refs("回落后留下的 35.36 元上影线供应沉重")["price_refs"]
        assert any(abs(r["value"] - 35.36) <= 5e-3 for r in refs)

    def test_shadow_low_point_still_registered(self):
        """「跌破 835.00 元下影线低点」— 835.00 是坐标价，必须登记。"""
        refs = _refs("若跌破 835.00 元下影线低点则转弱")["price_refs"]
        assert any(abs(r["value"] - 835.00) <= 5e-3 for r in refs)


# ---------------------------------------------------------------------------
# A2 — 牛熊线 → close_200_sma 桥接
# ---------------------------------------------------------------------------

class TestA2NiuxiongAlias:
    def test_niuxiong_named_field_hit(self):
        pool = build_market_data_pool(SOURCE, SYMBOL, TRADE_DATE)
        hits = named_field_hits("牛熊线 59.38 元", 59.38, pool)
        assert "indicators.close_200_sma" in hits

    def test_niuxiong_bridges_to_vendor_qfq(self):
        result = _refs("牛熊线 59.38 元构成中期支撑", source=SOURCE)
        ref = next(r for r in result["price_refs"]
                   if abs(r["value"] - 59.38) <= 5e-3)
        assert ref["basis"] == "vendor_qfq"
        assert "close_200_sma" in (ref.get("bridge_fields") or [""])[0]

    def test_niuxiong_value_mismatch_no_bridge(self):
        result = _refs("牛熊线 60.00 元构成中期支撑", source=SOURCE)
        ref = next(r for r in result["price_refs"]
                   if abs(r["value"] - 60.00) <= 5e-3)
        assert ref["basis"] != "vendor_qfq" or "pool_bridge" not in ref["provenance"]
