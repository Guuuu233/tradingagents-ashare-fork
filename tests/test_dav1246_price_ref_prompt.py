"""DAV-1246 单元测试 — A 部分抽取补漏 + B 部分可引用价位表/价格书写规则。

A1 影线长度不登记（两类写法 + 两条保护性反例 fixture）；
A2 C5 别名「牛熊线」→ close_200_sma；
B1 价位表逐行与 pool 一致且可经 named_field_hits 桥接、注入角色清单精确、
   pool 缺失走降级文案；
B2 zh/en 规则同义（同值、同条数、关键语义对应）；
B3 result_data 版本戳常量为 price_ref_prompt.v1。
"""

from __future__ import annotations

import inspect
import re

import pytest

from tradingagents.agents.utils.price_ref_registry import (
    build_market_data_pool,
    build_price_ref_registry,
    named_field_hits,
)
from tradingagents.agents.utils.price_ref_prompt import (
    PRICE_REF_PROMPT_ROLES,
    PRICE_REF_PROMPT_STATE_KEY,
    PRICE_REF_PROMPT_VERSION,
    attach_price_ref_prompt_block,
    build_price_ref_prompt_block,
    build_price_ref_table,
    price_ref_prompt_suffix,
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


# ---------------------------------------------------------------------------
# B1 — 可引用价位表
# ---------------------------------------------------------------------------

class TestB1PriceTable:
    def test_every_row_matches_pool_and_bridges(self):
        pool = build_market_data_pool(SOURCE, SYMBOL, TRADE_DATE)
        rows = build_price_ref_table(SOURCE, SYMBOL, TRADE_DATE)
        assert rows, "价位表不应为空"
        for row in rows:
            hits = named_field_hits(row["context"], row["value"], pool)
            assert hits, f"行不可桥接: {row}"
            # 值与具名字段一致
            matched = any(
                abs(nv.value - row["value"]) <= 5e-3 for nv in pool.named_values
            )
            assert matched, f"行值不在 pool: {row}"

    def test_table_contents_cover_required_groups(self):
        rows = build_price_ref_table(SOURCE, SYMBOL, TRADE_DATE)
        labels = [r["label"] for r in rows]
        # 最新交易日 OHLC
        for word in ("开盘", "最高", "最低", "收盘"):
            assert f"8月14日 {word}" in labels
        # 近 5 日高低（去重后 4 个更早交易日）
        for d in ("8月10日", "8月11日", "8月12日", "8月13日"):
            assert f"{d} 最高" in labels and f"{d} 最低" in labels
        # 指标
        for label in ("10 日 EMA", "50 日 SMA", "200 日 SMA", "VWMA",
                      "布林上轨", "布林中轨", "布林下轨"):
            assert label in labels
        # 涨跌停（主板±10%：56.58 → 62.24 / 50.92）
        assert "涨停价" in labels and "跌停价" in labels
        assert abs(next(r["value"] for r in rows if r["label"] == "涨停价") - 62.24) <= 5e-3
        assert abs(next(r["value"] for r in rows if r["label"] == "跌停价") - 50.92) <= 5e-3

    def test_limit_ratio_gem_20pct(self):
        src = dict(SOURCE)
        src["stock_data"] = STOCK_DATA.replace("002475.SZ", "300274.SZ")
        rows = build_price_ref_table(src, "300274.SZ", TRADE_DATE)
        limit_up = next(r["value"] for r in rows if r["label"] == "涨停价")
        assert abs(limit_up - round(56.58 * 1.20, 2)) <= 5e-3

    def test_pool_missing_degraded_text(self):
        block = build_price_ref_prompt_block({"stock_data": ""}, SYMBOL, TRADE_DATE)
        assert "本次无可引用价位表" in block["zh"]
        assert "No reference price table" in block["en"]
        block2 = build_price_ref_prompt_block(None, SYMBOL, TRADE_DATE)
        assert "本次无可引用价位表" in block2["zh"]

    def test_attach_block_into_state(self):
        state = {"trade_date": TRADE_DATE, "company_of_interest": SYMBOL}
        assert attach_price_ref_prompt_block(state, SOURCE)
        block = state[PRICE_REF_PROMPT_STATE_KEY]
        assert "可引用价位表" in block["zh"] and "price table" in block["en"].lower()

    def test_suffix_selects_language(self):
        state = {"trade_date": TRADE_DATE, "company_of_interest": SYMBOL}
        attach_price_ref_prompt_block(state, SOURCE)
        zh = price_ref_prompt_suffix(state, {"prompt_language": "zh"})
        en = price_ref_prompt_suffix(state, {"prompt_language": "en"})
        assert "可引用价位表" in zh
        assert "Reference price table" in en
        assert price_ref_prompt_suffix({}, {"prompt_language": "zh"}) == ""
        assert price_ref_prompt_suffix(None) == ""


# ---------------------------------------------------------------------------
# B2 — zh/en 同义
# ---------------------------------------------------------------------------

class TestB2ZhEnSynonymy:
    def test_same_values_in_both_languages(self):
        block = build_price_ref_prompt_block(SOURCE, SYMBOL, TRADE_DATE)
        num = re.compile(r"\d+(?:\.\d+)?")
        zh_vals = sorted(num.findall(block["zh"].split("【价格书写规则】")[0]))
        en_vals = sorted(num.findall(block["en"].split("[Price-writing rules]")[0]))
        assert zh_vals == en_vals

    def test_same_rule_count(self):
        block = build_price_ref_prompt_block(SOURCE, SYMBOL, TRADE_DATE)
        zh_rules = re.findall(r"^\d\.", block["zh"], re.M)
        en_rules = re.findall(r"^\d\.", block["en"], re.M)
        assert len(zh_rules) == len(en_rules) == 6

    def test_key_semantics_present_in_en(self):
        en = build_price_ref_prompt_block(SOURCE, SYMBOL, TRADE_DATE)["en"]
        for phrase in ("verbatim", "rounding", "PE", "stop-loss",
                       "vendor_qfq", "price table"):
            assert phrase in en


# ---------------------------------------------------------------------------
# B3 — 版本戳 + 注入角色清单
# ---------------------------------------------------------------------------

class TestB3VersionAndRoleList:
    def test_version_constant(self):
        assert PRICE_REF_PROMPT_VERSION == "price_ref_prompt.v1"

    def test_role_list_exact(self):
        expected = {
            "fundamentals", "news", "macro", "smart_money", "social",
            "research_manager", "trader",
            "aggressive_debator", "conservative_debator", "neutral_debator",
            "risk_manager",
        }
        assert set(PRICE_REF_PROMPT_ROLES) == expected
        assert "market" not in expected and "volume_price" not in expected

    def test_injected_modules_reference_suffix(self):
        """注入角色清单：10 个模块引用 price_ref_prompt_suffix；
        market/volume_price 分析师不得注入。"""
        import importlib
        injected = {
            "fundamentals": "tradingagents.agents.analysts.fundamentals_analyst",
            "news": "tradingagents.agents.analysts.news_analyst",
            "macro": "tradingagents.agents.analysts.macro_analyst",
            "smart_money": "tradingagents.agents.analysts.smart_money_analyst",
            "social": "tradingagents.agents.analysts.social_media_analyst",
            "research_manager": "tradingagents.agents.managers.research_manager",
            "trader": "tradingagents.agents.trader.trader",
            "aggressive_debator": "tradingagents.agents.risk_mgmt.aggressive_debator",
            "conservative_debator": "tradingagents.agents.risk_mgmt.conservative_debator",
            "neutral_debator": "tradingagents.agents.risk_mgmt.neutral_debator",
            "risk_manager": "tradingagents.agents.managers.risk_manager",
        }
        for role, modname in injected.items():
            src = inspect.getsource(importlib.import_module(modname))
            assert "price_ref_prompt_suffix(state" in src, f"{role} 未注入"

        for modname in (
            "tradingagents.agents.analysts.market_analyst",
            "tradingagents.agents.analysts.volume_price_analyst",
        ):
            src = inspect.getsource(importlib.import_module(modname))
            assert "price_ref_prompt_suffix" not in src, f"{modname} 不应注入"


# ---------------------------------------------------------------------------
# 节点级注入验证（真实 node + mock llm 捕获提示词）
# ---------------------------------------------------------------------------

def _block_state(**overrides):
    state = {
        "trade_date": TRADE_DATE,
        "company_of_interest": SYMBOL,
        "market_report": "M", "sentiment_report": "S", "news_report": "N",
        "fundamentals_report": "F", "macro_report": "G",
        "smart_money_report": "SM", "volume_price_report": "V",
        "trader_investment_plan": "plan",
        "investment_plan": "plan",
        "horizon": "short",
        "user_intent": None,
        "risk_debate_state": {
            "history": "", "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "current_conservative_response": "",
            "current_aggressive_response": "", "current_neutral_response": "",
            "claims": [], "focus_claim_ids": [], "unresolved_claim_ids": [],
            "round_summary": "", "round_goal": "g", "count": 0,
        },
        "risk_feedback_state": {},
        "fund_flow_consensus_guard": {
            "blocked": False, "direction_allowed": True, "status": "consensus",
            "consensus": {"field": "r0_net", "field_category": "main_force",
                          "status": "consensus", "data_conflict": False,
                          "hard_guard": {"blocked": False}, "raw_values": [1]},
            "validation": {"status": "not_checked", "structured": {"status": "available"},
                           "model": {}, "hard_guard": {"blocked": False}},
            "reason": "ok",
        },
    }
    attach_price_ref_prompt_block(state, SOURCE)
    state.update(overrides)
    return state


class _MockChunk:
    def __init__(self, content):
        self.content = content


class TestNodeInjection:
    def test_macro_analyst_injects(self):
        import asyncio
        from unittest.mock import MagicMock
        from tradingagents.agents.analysts.macro_analyst import create_macro_analyst

        captured = []

        async def fake_astream(messages):
            captured.append(messages)
            yield _MockChunk("宏观分析")

        llm = MagicMock()
        llm.astream = fake_astream
        node = create_macro_analyst(llm, data_collector=None)
        asyncio.run(node(_block_state()))
        sysmsg = captured[0][0].content
        assert "可引用价位表" in sysmsg and "8月14日 收盘" in sysmsg

    def test_aggressive_debator_injects(self):
        import asyncio
        from unittest.mock import MagicMock
        from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator

        captured = []

        async def fake_astream(prompt, **kw):
            captured.append(prompt)
            yield _MockChunk("response")

        llm = MagicMock()
        llm.astream = fake_astream
        node = create_aggressive_debator(llm)
        asyncio.run(node(_block_state()))
        assert "可引用价位表" in captured[0]
