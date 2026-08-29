"""Integration tests for DataCollector social wiring and market_attention (P2-T8).

Specifications:
- docs/social_data/implementation_plan.md Task 8, §1, §3, §8
- DECISIONS.md D-008, D-009, D-010

Hard constraints:
1. Social collection occurs AFTER _fetch_all with independent timeout.
2. Social collector/calls MUST NOT appear in _fetch_all tasks or market ThreadPoolExecutor.
3. pool["news"] and pool["social_data_context"] are strictly independent keys.
4. market_data_context["market_attention"] contains zt_pool and hot_stocks with status and as_of.
5. Social exceptions/timeouts do NOT discard market cache or throw unhandled errors.
"""

from __future__ import annotations

import contextlib
import copy
import inspect
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
)
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.dataflows.social.collector import SocialDataCollector
from tradingagents.dataflows.social.contracts import (
    REASON_SOCIAL_ARCHIVE_LOCKED,
    REASON_SOCIAL_ARCHIVE_MISSING,
    REASON_SOCIAL_NOT_APPLICABLE,
    SentimentBundleV1,
    SocialDataContext,
    SocialStatus,
    create_default_social_data_context,
    create_empty_sentiment_bundle,
)
from tradingagents.graph.data_collector import (
    DataCollector,
    _fetch_all,
    make_cache_key,
)


def _make_stub_market_pool(ticker: str = "600519", trade_date: str = "2026-08-11") -> Dict[str, Any]:
    """Create a minimal stub market pool returned by _fetch_all."""
    return {
        "stock_data": f"# as-of: {trade_date}\ndate,open,high,low,close,volume\n{trade_date},100,105,99,102,1000",
        "indicators": {"rsi": 50.0, "macd": 0.5},
        "news": f"## {ticker} 新闻（2026-08-05 至 {trade_date}；最新发布时间：{trade_date} 10:00:00）：利好频传",
        "global_news": f"## 全球新闻（最新发布时间：{trade_date} 09:00:00）：市场平稳",
        "zt_pool": f"涨停池（{trade_date}，同花顺）：共 15 只涨停",
        "hot_stocks": "【数据获取失败】雪球热搜：仅支持当日快照，无法用于历史日期分析",
        "market_data_context": {
            "analysis_baseline_date": trade_date,
            "daily": {"as_of": trade_date},
            "source_provenance": {
                "news": {"status": "available", "as_of": trade_date, "requested_as_of": trade_date},
                "zt_pool": {"status": "available", "as_of": trade_date, "requested_as_of": trade_date},
                "hot_stocks": {
                    "status": "refused",
                    "as_of": None,
                    "requested_as_of": trade_date,
                    "gap": "【数据获取失败】hot_stocks：快照拒绝",
                    "gap_class": "structural",
                },
            },
            "data_failure_ledger": [],
            "market_attention": {
                "zt_pool": {
                    "status": "available",
                    "as_of": trade_date,
                    "requested_as_of": trade_date,
                    "raw": f"涨停池（{trade_date}，同花顺）：共 15 只涨停",
                },
                "hot_stocks": {
                    "status": "refused",
                    "as_of": None,
                    "requested_as_of": trade_date,
                    "raw": "【数据获取失败】雪球热搜：仅支持当日快照，无法用于历史日期分析",
                    "gap": "【数据获取失败】hot_stocks：快照拒绝",
                    "gap_class": "structural",
                },
            },
        },
    }


# ============================================================================
# 1. Injection & Default Behavior
# ============================================================================

def test_data_collector_init_injection():
    """DataCollector accepts optional social_collector and defaults to SocialDataCollector."""
    collector_default = DataCollector()
    assert hasattr(collector_default, "social_collector")
    assert isinstance(collector_default.social_collector, SocialDataCollector)

    custom_stub = MagicMock()
    collector_custom = DataCollector(social_collector=custom_stub)
    assert collector_custom.social_collector is custom_stub


def test_collect_disabled_mode_returns_not_applicable():
    """In default disabled mode, social_data_context has not_applicable status."""
    collector = DataCollector()  # default mode=disabled
    stub_pool = _make_stub_market_pool()

    with patch("tradingagents.graph.data_collector._fetch_all", return_value=copy.deepcopy(stub_pool)):
        result = collector.collect("600519.SH", "2026-08-11")

    assert "social_data_context" in result
    social_ctx = result["social_data_context"]
    assert social_ctx["status"] == SocialStatus.NOT_APPLICABLE.value
    assert social_ctx["mode"] == "disabled"
    assert social_ctx["direction_allowed"] is False
    assert REASON_SOCIAL_NOT_APPLICABLE in social_ctx["reason_codes"]
    assert "news" in result
    assert "利好频传" in result["news"]


# ============================================================================
# 2. Stub Success & Separation of Keys
# ============================================================================

def test_collect_with_stub_success_independent_news_and_social():
    """When social collector returns context, it is stored in social_data_context and news is unchanged."""
    stub_social_collector = MagicMock()
    custom_bundle = SentimentBundleV1(
        status=SocialStatus.AVAILABLE.value,
        requested_as_of="2026-08-11",
        cutoff_at="2026-08-11T23:59:59Z",
        direction_allowed=True,
        symbol="600519.SH",
        reason_codes=[],
    )
    expected_social_ctx = create_default_social_data_context(
        status=SocialStatus.AVAILABLE.value,
        mode="active",
        requested_as_of="2026-08-11",
        bundle=custom_bundle,
        source_provenance={
            "social_archive": {
                "status": "available",
                "content_as_of": "2026-08-11T08:00:00Z",
                "metric_as_of": "2026-08-11T08:00:00Z",
                "provider": "archive_sqlite",
            }
        },
        data_failure_ledger=[],
    )
    stub_social_collector.collect.return_value = expected_social_ctx

    collector = DataCollector(social_collector=stub_social_collector)
    stub_pool = _make_stub_market_pool()

    with patch("tradingagents.graph.data_collector._fetch_all", return_value=copy.deepcopy(stub_pool)):
        result = collector.collect("600519.SH", "2026-08-11")

    stub_social_collector.collect.assert_called_once_with("600519.SH", "2026-08-11")
    assert result["social_data_context"] == expected_social_ctx
    # pool["news"] is strictly untouched by social data
    assert result["news"] == stub_pool["news"]
    assert "social_data_context" not in result["news"]


# ============================================================================
# 3. Exception & Timeout Isolation
# ============================================================================

def test_collect_with_social_exception_does_not_crash_market_fetch():
    """When social collection raises an unhandled exception, market cache is saved and social context is failed."""
    stub_social_collector = MagicMock()
    stub_social_collector.collect.side_effect = RuntimeError("Database disk I/O failure")
    stub_social_collector.mode = "active"

    collector = DataCollector(social_collector=stub_social_collector)
    stub_pool = _make_stub_market_pool()

    with patch("tradingagents.graph.data_collector._fetch_all", return_value=copy.deepcopy(stub_pool)):
        result = collector.collect("600519.SH", "2026-08-11")

    # Does not crash
    assert "stock_data" in result
    assert "indicators" in result
    assert "news" in result
    assert "social_data_context" in result

    social_ctx = result["social_data_context"]
    assert social_ctx["status"] == SocialStatus.FAILED.value
    assert social_ctx["direction_allowed"] is False
    assert REASON_SOCIAL_ARCHIVE_MISSING in social_ctx["reason_codes"]
    assert len(social_ctx["data_failure_ledger"]) > 0
    assert social_ctx["data_failure_ledger"][0]["status"] == "failed"


def test_collect_with_social_timeout_does_not_crash_and_produces_timeout_context():
    """When social collection hangs/times out, DataCollector isolates it and returns timeout context."""
    def hanging_collect(*args: Any, **kwargs: Any) -> SocialDataContext:
        time.sleep(2.0)
        return create_default_social_data_context(status="available", mode="active")

    stub_social_collector = MagicMock()
    stub_social_collector.collect.side_effect = hanging_collect
    stub_social_collector.mode = "active"
    stub_social_collector.fetch_timeout = 0.05  # 50ms short timeout

    collector = DataCollector(social_collector=stub_social_collector)
    stub_pool = _make_stub_market_pool()

    with patch("tradingagents.graph.data_collector._fetch_all", return_value=copy.deepcopy(stub_pool)):
        start = time.time()
        result = collector.collect("600519.SH", "2026-08-11")
        elapsed = time.time() - start

    # Should time out quickly (~50ms - 200ms) without waiting full 2s
    assert elapsed < 1.0
    assert "social_data_context" in result
    social_ctx = result["social_data_context"]
    assert social_ctx["status"] == SocialStatus.TIMEOUT.value
    assert social_ctx["direction_allowed"] is False
    assert REASON_SOCIAL_ARCHIVE_LOCKED in social_ctx["reason_codes"]
    assert len(social_ctx["data_failure_ledger"]) > 0
    assert social_ctx["data_failure_ledger"][0]["status"] == "timeout"


def test_collect_with_futures_timeout_error_direct():
    """Explicitly verify concurrent.futures.TimeoutError (FuturesTimeoutError) maps to status=timeout."""
    stub_social_collector = MagicMock()
    stub_social_collector.collect.side_effect = FuturesTimeoutError("Direct future timeout")
    stub_social_collector.mode = "active"

    collector = DataCollector(social_collector=stub_social_collector)
    stub_pool = _make_stub_market_pool()

    with patch("tradingagents.graph.data_collector._fetch_all", return_value=copy.deepcopy(stub_pool)):
        result = collector.collect("600519.SH", "2026-08-11")

    assert "social_data_context" in result
    social_ctx = result["social_data_context"]
    assert social_ctx["status"] == SocialStatus.TIMEOUT.value
    assert social_ctx["direction_allowed"] is False
    assert REASON_SOCIAL_ARCHIVE_LOCKED in social_ctx["reason_codes"]
    assert len(social_ctx["data_failure_ledger"]) > 0
    assert social_ctx["data_failure_ledger"][0]["status"] == "timeout"


# ============================================================================
# 4. Strict Non-Pollution of _fetch_all tasks and ThreadPoolExecutor
# ============================================================================

def test_fetch_all_tasks_dict_contains_no_social_keys():
    """_fetch_all must NOT have any social tasks in its tasks dictionary."""
    source_code = inspect.getsource(_fetch_all)
    assert "SocialDataCollector" not in source_code
    assert "social_data" not in source_code
    assert "social_collector" not in source_code


def test_fetch_all_threadpool_does_not_submit_social_tasks():
    """ThreadPoolExecutor inside _fetch_all must not receive any social functions."""
    submitted_callables = []

    original_submit = ThreadPoolExecutor.submit

    def spy_submit(self, fn, *args, **kwargs):
        name = getattr(fn, "__name__", str(fn))
        submitted_callables.append(name)
        return original_submit(self, fn, *args, **kwargs)

    tool_patches = [
        patch("tradingagents.graph.data_collector.get_stock_data", return_value="date,open,high,low,close,volume\n2026-08-11,1,1,1,1,10"),
        patch("tradingagents.graph.data_collector.get_cn_indices", return_value="cn"),
        patch("tradingagents.graph.data_collector.get_global_indices", return_value="gi"),
        patch("tradingagents.graph.data_collector.get_major_assets", return_value="ma"),
        patch("tradingagents.graph.data_collector.get_news", return_value="news"),
        patch("tradingagents.graph.data_collector.get_global_news", return_value="gn"),
        patch("tradingagents.graph.data_collector.get_board_fund_flow", return_value="bff"),
        patch("tradingagents.graph.data_collector.get_individual_fund_flow", return_value="iff"),
        patch("tradingagents.graph.data_collector.get_lhb_detail", return_value="lhb"),
        patch("tradingagents.graph.data_collector.get_insider_transactions", return_value="it"),
        patch("tradingagents.graph.data_collector.get_zt_pool", return_value="zt"),
        patch("tradingagents.graph.data_collector.get_hot_stocks_xq", return_value="hs"),
        patch("tradingagents.graph.data_collector.get_restricted_release", return_value="rr"),
        patch("tradingagents.graph.data_collector.get_share_pledge", return_value="sp"),
        patch("tradingagents.graph.data_collector.get_earnings_forecast", return_value="ef"),
        patch("tradingagents.graph.data_collector.get_shareholder_count", return_value="sc"),
        patch("tradingagents.graph.data_collector.get_margin_trading", return_value="mt"),
        patch("tradingagents.graph.data_collector.get_northbound_flow", return_value="nf"),
        patch("tradingagents.graph.data_collector.get_fundamentals", return_value="f"),
        patch("tradingagents.graph.data_collector.get_balance_sheet", return_value="bs"),
        patch("tradingagents.graph.data_collector.get_cashflow", return_value="cf"),
        patch("tradingagents.graph.data_collector.get_income_statement", return_value="is"),
    ]

    with patch.object(ThreadPoolExecutor, "submit", new=spy_submit), contextlib.ExitStack() as stack:
        for p in tool_patches:
            stack.enter_context(p)
        collector = DataCollector()
        collector.collect("600519.SH", "2026-08-11")

    # Verify that tasks were indeed submitted to ThreadPoolExecutor and none is social
    assert len(submitted_callables) > 0
    for name in submitted_callables:
        assert "social" not in name.lower()


# ============================================================================
# 5. market_attention in market_data_context
# ============================================================================

def test_market_attention_structure_in_market_data_context():
    """_fetch_all constructs market_data_context.market_attention with zt_pool and hot_stocks."""
    # Test with real _fetch_all helpers
    from tradingagents.graph.data_collector import _build_market_attention

    # Case A: Available zt_pool, Refused hot_stocks
    results = {
        "zt_pool": "涨停池（2026-08-11，同花顺）：共 10 只",
        "hot_stocks": "【数据获取失败】雪球热搜：仅支持当日快照，无法用于历史日期分析",
    }
    source_provenance = {
        "zt_pool": {"status": "available", "as_of": "2026-08-11", "requested_as_of": "2026-08-11"},
        "hot_stocks": {
            "status": "refused",
            "as_of": None,
            "requested_as_of": "2026-08-11",
            "gap": "【数据获取失败】hot_stocks：快照拒绝",
            "gap_class": "structural",
        },
    }

    attention = _build_market_attention(results, source_provenance, "2026-08-11")
    assert "zt_pool" in attention
    assert "hot_stocks" in attention

    zt = attention["zt_pool"]
    assert zt["status"] == "available"
    assert zt["as_of"] == "2026-08-11"
    assert "涨停池" in str(zt.get("raw"))

    hs = attention["hot_stocks"]
    assert hs["status"] == "refused"
    assert hs["as_of"] is None
    assert "gap" in hs
    assert hs["gap_class"] == "structural"


def test_market_attention_missing_sources_fail_closed():
    """Missing or failed zt_pool and hot_stocks must have explicit status and gap, never silent empty dict."""
    from tradingagents.graph.data_collector import _build_market_attention

    results = {}
    source_provenance = {}

    attention = _build_market_attention(results, source_provenance, "2026-08-11")
    assert "zt_pool" in attention
    assert "hot_stocks" in attention
    assert attention["zt_pool"]["status"] in ("unavailable", "failed")
    assert "gap" in attention["zt_pool"]
    assert attention["hot_stocks"]["status"] in ("unavailable", "failed")
    assert "gap" in attention["hot_stocks"]


# ============================================================================
# 6. Cache Hit Semantics
# ============================================================================

def test_cache_hit_preserves_social_data_context_and_market_attention():
    """On cache hit, deepcopy contains both social_data_context and market_attention."""
    stub_social_collector = MagicMock()
    stub_social_collector.collect.return_value = create_default_social_data_context(
        status="available", mode="active", requested_as_of="2026-08-11"
    )

    collector = DataCollector(social_collector=stub_social_collector)
    stub_pool = _make_stub_market_pool()

    with patch("tradingagents.graph.data_collector._fetch_all", return_value=copy.deepcopy(stub_pool)) as mock_fetch:
        result1 = collector.collect("600519.SH", "2026-08-11")
        result2 = collector.collect("600519.SH", "2026-08-11")

    assert mock_fetch.call_count == 1
    assert stub_social_collector.collect.call_count == 1
    assert "social_data_context" in result2
    assert result2["social_data_context"]["status"] == "available"
    assert "market_attention" in result2["market_data_context"]

    # Defensive copy check
    result2["social_data_context"]["status"] = "mutated"
    cached = collector.get("600519.SH", "2026-08-11")
    assert cached["social_data_context"]["status"] == "available"
