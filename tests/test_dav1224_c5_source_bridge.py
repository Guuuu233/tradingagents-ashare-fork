"""DAV-1224 C5 严格 source-backed 桥接 + 坏锚/入口一致性测试（对应 W4）。

仅字段级身份（指标名/日期+OHLC/涨跌停）且值精确相等才桥接为 vendor_qfq；
仅数值相等不得桥接；数据取自本次运行 state（PRICE_REF_SOURCE_KEY）。
"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.utils.price_ref_registry import (
    PRICE_BASIS_DERIVED_ESTIMATE,
    PRICE_REF_SOURCE_KEY,
    MarketDataPoolError,
    attach_price_ref_source,
    audit_price_ref_registry,
    build_market_data_pool,
    build_price_ref_registry,
    named_field_hits,
    parse_stock_data_text,
)
from tradingagents.agents.utils.price_basis_gate import (
    evaluate_price_basis_gate,
    finalize_price_ref_state,
)


TOL = 5e-3


def run(reports, td="2026-08-14", source=None):
    st = {"trade_date": td}
    st.update(reports)
    if source is not None:
        st[PRICE_REF_SOURCE_KEY] = source
    audit_price_ref_registry(st)
    return st["price_refs"], evaluate_price_basis_gate(st), st


def kinds(gate):
    return [v["kind"] for v in gate["violations"]]


def values(refs):
    return [r["value"] for r in refs]


def has_val(refs, v):
    return any(abs(r["value"] - v) <= TOL for r in refs)


STOCK_DATA = (
    "# Stock data for 600893.SH\n"
    "# price_basis: vendor_qfq\n"
    "date,low,close,volume,open,high\n"
    "2026-08-05,35.00,35.50,1000.0,35.10,35.80\n"
    "2026-08-06,35.20,35.91,1200.0,35.40,36.10\n"
)
INDICATORS = {"close_10_ema": 35.37, "close_50_sma": 37.36, "vwma": 34.86,
              "boll_ub": 36.89, "rsi": 50.1}


@pytest.fixture
def market_source():
    return {
        "stock_data": STOCK_DATA,
        "indicators": dict(INDICATORS),
        "price_basis": "vendor_qfq",
        "symbol": "600893.SH",
        "trade_date": "2026-08-06",
    }


class TestC5SourceBackedBridge:
    def test_named_indicator_bridges(self, market_source):
        # 明确指标名 + 精确同值 → pool_bridge:indicators.close_10_ema
        refs, g, st = run(
            {"investment_plan": "跌破 10日 EMA 35.37 元需减仓防守。"},
            td="2026-08-06", source=market_source,
        )
        r = [r for r in refs if abs(r["value"] - 35.37) <= TOL]
        assert r and r[0]["basis"] == "vendor_qfq"
        assert r[0]["provenance"].startswith("pool_bridge:indicators.close_10_ema")

    def test_date_ohlc_bridges(self, market_source):
        refs, g, st = run(
            {"news_report": "2026-08-06 收盘价 35.91 元创近期新高。"},
            td="2026-08-06", source=market_source,
        )
        r = [r for r in refs if abs(r["value"] - 35.91) <= TOL]
        assert r and r[0]["basis"] == "vendor_qfq"
        assert "stock_data.2026-08-06.close" in (r[0].get("bridge_fields") or [])

    def test_limit_field_bridges(self, market_source):
        # 涨停语义 + computed limit 字段（35.91*1.1=39.50）
        refs, g, st = run(
            {"news_report": "明日涨停价约 39.50 元。"},
            td="2026-08-06", source=market_source,
        )
        r = [r for r in refs if abs(r["value"] - 39.50) <= TOL]
        assert r and r[0]["basis"] == "vendor_qfq"
        assert any(f.startswith("derived.limit_up") for f in r[0].get("bridge_fields") or [])

    def test_bare_value_equality_does_not_bridge(self, market_source):
        # 仅数值相等、context 不指名字段 → 不得继承 vendor_qfq
        refs, g, st = run(
            {"investment_plan": "模型测算目标价 35.37 元。"},
            td="2026-08-06", source=market_source,
        )
        r = [r for r in refs if abs(r["value"] - 35.37) <= TOL]
        assert r and not (r[0]["provenance"] or "").startswith("pool_bridge")

    def test_quote_protection_with_pool(self, market_source):
        # [C3] 真报价保护：报价语义 + 本运行具名字段同值 → 不降格 derived
        refs, g, _ = run(
            {"market_report": "按 PE 估算区间上沿，现价 35.91 元站上 10 日 EMA。"},
            td="2026-08-06", source=market_source,
        )
        r = [r for r in refs if abs(r["value"] - 35.91) <= TOL]
        assert r and r[0]["basis"] != PRICE_BASIS_DERIVED_ESTIMATE

    def test_no_source_no_bridge(self):
        refs, g, _ = run({"investment_plan": "跌破 10日 EMA 35.37 元需减仓防守。"})
        r = [r for r in refs if abs(r["value"] - 35.37) <= TOL]
        assert r and r[0]["basis"] != "vendor_qfq" or not r

    def test_header_parsing_and_fail_closed(self):
        # 列序非 canonical 仍按 header 取列；缺列 fail-closed
        header, bars, basis = parse_stock_data_text(STOCK_DATA, "600893.SH")
        assert bars[-1]["close"] == 35.91 and bars[-1]["open"] == 35.40
        bad = "date,open,high,low,volume\n2026-08-06,35.4,36.1,35.2,100.0\n"
        with pytest.raises(MarketDataPoolError):
            parse_stock_data_text(bad, "600893.SH")

    def test_attach_source_state_key(self):
        st = {"trade_date": "2026-08-06",
              "instrument_context": {"symbol": "600893.SH"}}
        assert attach_price_ref_source(st, {"stock_data": STOCK_DATA,
                                            "indicators": INDICATORS,
                                            "price_basis": "vendor_qfq"})
        src = st[PRICE_REF_SOURCE_KEY]
        assert src["symbol"] == "600893.SH" and src["trade_date"] == "2026-08-06"
        assert not attach_price_ref_source(st, {"indicators": INDICATORS})


# ---------------------------------------------------------------------------
# true-positive 锚点（v1 必须继续拦）
# ---------------------------------------------------------------------------

class TestTruePositiveAnchors:
    def test_b188060f_fixture_blocked(self):
        refs, g, _ = run({
            "news_report": "今日大宗交易成交 78.61 元，较当日收盘 84.03 元折价 6.45%。",
            "trader_investment_plan": "下行风险较大，第一下行目标 78.61 元（大宗折价锚位）。",
        }, td="2026-05-22")
        assert g["status"] == "blocked"

    def test_unbacked_executable_still_blocked(self):
        refs, g, _ = run({"final_trade_decision": "目标价 55.55 元，止损 50.00 元。"})
        assert g["status"] == "blocked"
        assert any(
            v["kind"] in ("unbacked_executable_level", "executable_level_wrong_basis")
            for v in g["violations"]
        )

    def test_audit_error_fails_closed(self):
        st = {"trade_date": "2026-08-14", "market_report": object()}
        with patch(
            "tradingagents.agents.utils.price_ref_registry.build_price_ref_registry",
            side_effect=RuntimeError("boom"),
        ):
            audit_price_ref_registry(st)
        g = evaluate_price_basis_gate(st)
        assert g["status"] == "blocked"
        assert any(v["kind"] == "audit_unavailable" for v in g["violations"])


# ---------------------------------------------------------------------------
# 入口一致性（验收4）：三入口同一套 audit/gate + C5 源挂接
# ---------------------------------------------------------------------------

class TestEntryPointParity:
    def test_finalize_attaches_market_source(self):
        # propagate / streaming / medium 三入口统一走
        # finalize_price_ref_state(state, market_source)
        st = {
            "trade_date": "2026-08-06",
            "instrument_context": {"symbol": "600893.SH"},
            "investment_plan": "跌破 10日 EMA 35.37 元需减仓防守。",
        }
        finalize_price_ref_state(st, {"stock_data": STOCK_DATA,
                                      "indicators": dict(INDICATORS),
                                      "price_basis": "vendor_qfq"})
        assert PRICE_REF_SOURCE_KEY in st
        bridged = [r for r in st["price_refs"]
                   if (r.get("provenance") or "").startswith("pool_bridge:")]
        assert bridged, "market_source 未参与桥接"

    def test_horizon_result_forwards_market_source(self):
        # 双周期入口：_build_horizon_result 第三参数透传 collected pool
        with patch("tradingagents.graph.trading_graph.create_llm_client"), \
             patch("tradingagents.graph.trading_graph.FinancialSituationMemory"), \
             patch("tradingagents.graph.trading_graph.GraphSetup"), \
             patch("tradingagents.graph.trading_graph.ConditionalLogic"), \
             patch("tradingagents.graph.trading_graph.Propagator"), \
             patch("tradingagents.graph.trading_graph.Reflector"), \
             patch("tradingagents.graph.trading_graph.SignalProcessor"), \
             patch("tradingagents.graph.trading_graph.set_config"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            ta = TradingAgentsGraph.__new__(TradingAgentsGraph)
            ta.debug = False
            ta.config = {}
            ta.callbacks = []
            ta.log_states_dict = {}
            ta.quick_thinking_llm = MagicMock()
            ta.data_collector = MagicMock()
            ta.propagator = MagicMock()
            ta.graph = MagicMock()
            ta.signal_processor = MagicMock()
        state = {
            "trade_date": "2026-08-06",
            "instrument_context": {"symbol": "600893.SH"},
            "investment_plan": "跌破 10日 EMA 35.37 元需减仓防守。",
            "final_trade_decision": "观望",
        }
        result = ta._build_horizon_result(
            "short", deepcopy(state),
            {"stock_data": STOCK_DATA, "indicators": dict(INDICATORS),
             "price_basis": "vendor_qfq"},
        )
        assert result["price_basis_gate"]["status"] in ("pass", "blocked")
        bridged = [r for r in (result.get("price_refs") or [])
                   if (r.get("provenance") or "").startswith("pool_bridge:")]
        assert bridged, "双周期入口未把 market_source 传给 audit"

    def test_streaming_entry_runs_same_audit_gate(self):
        # streaming 入口：finalize 调用形态与 propagate 一致（state + collected_pool）
        st = {
            "trade_date": "2026-08-06",
            "instrument_context": {"symbol": "600893.SH"},
            "trader_investment_plan": "第一下行目标 39.50 元（涨停参照）。",
        }
        gate = finalize_price_ref_state(st, {"stock_data": STOCK_DATA,
                                             "indicators": dict(INDICATORS),
                                             "price_basis": "vendor_qfq"})
        assert gate["contract_version"] == "price_ref.v1"
        assert gate["status"] in ("pass", "blocked")
        assert "price_refs" in st and "price_basis_validation" in st
