"""Tests for DAV-946: Historical fundamentals and insider transactions must not fall back to date-blind providers.

Covers contracts:
1. get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement, get_insider_transactions
   under historical curr_date must not call or accept date-blind yfinance / Alpha Vantage.
2. When preceding vendors fail, route_to_vendor must return an explicit data gap refusal,
   not snapshot / future rows, not confirmed empty, not zero-filled.
3. Valid historical providers continue to succeed according to typed vendor semantics.
4. Live / current date path preserves existing behavior; missing/invalid/future dates trigger bus refusal.
5. curr_date passing, method signatures, and typed VendorRefuse semantics are consistent.
6. Legitimate zero/negative values and confirmed empty (VendorEmpty) are not classified as missing/gaps.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.dataflows import interface as iface
from tradingagents.dataflows.trade_calendar import (
    cn_today_str,
    is_historical_analysis_date,
    now_cn,
)
from tradingagents.dataflows.vendor_result import (
    VendorEmpty,
    VendorFail,
    VendorOk,
    VendorRefuse,
)
from tradingagents.dataflows.providers.base import (
    BaseMarketDataProvider,
    ProviderResourcePolicy,
)
from tradingagents.dataflows.providers.yfinance_provider import YFinanceProvider
from tradingagents.dataflows.providers.alpha_vantage_provider import AlphaVantageProvider
from tradingagents.dataflows import y_finance
from tradingagents.dataflows import alpha_vantage_news


HISTORICAL_DATE = "2024-01-02"
FAST_POLICY = ProviderResourcePolicy(
    timeout_seconds=1.0, max_retries=0, max_concurrency=2
)


class _MockProvider:
    """Configurable mock provider for router tests."""

    def __init__(self, name: str, handlers: dict[str, object] | None = None):
        self._name = name
        self._handlers = handlers or {}

    @property
    def name(self) -> str:
        return self._name

    def __getattr__(self, item: str):
        if item in self._handlers:
            return self._handlers[item]
        raise AttributeError(f"{self._name} has no handler for {item}")


class _MockRegistry:
    def __init__(self, providers: dict[str, object]):
        self._providers = providers

    def list_names(self) -> list[str]:
        return list(self._providers.keys())

    def get(self, name: str) -> object | None:
        return self._providers.get(name)

    def resource_policy(self, name: str) -> ProviderResourcePolicy:
        return FAST_POLICY


# ── Contract 1 & 2: Controlled reproduction scenario ─────────────────────────


@pytest.mark.parametrize(
    "method,args,kwargs",
    [
        ("get_fundamentals", ("600519",), {"curr_date": HISTORICAL_DATE}),
        (
            "get_balance_sheet",
            ("600519", "quarterly"),
            {"curr_date": HISTORICAL_DATE},
        ),
        (
            "get_cashflow",
            ("600519", "quarterly"),
            {"curr_date": HISTORICAL_DATE},
        ),
        (
            "get_income_statement",
            ("600519", "quarterly"),
            {"curr_date": HISTORICAL_DATE},
        ),
        (
            "get_insider_transactions",
            ("600519",),
            {"curr_date": HISTORICAL_DATE},
        ),
    ],
)
def test_historical_router_never_calls_date_blind_providers_on_pre_failure(
    method, args, kwargs
):
    """When all preceding historical vendors fail, router must NOT call yfinance or Alpha Vantage,

    and must return an explicit data gap refusal string.
    """
    # Preceding providers fail with controlled VendorFail
    fuyao = _MockProvider(
        "cn_fuyao", {method: MagicMock(return_value=VendorFail("fuyao down"))}
    )
    akshare = _MockProvider(
        "cn_akshare", {method: MagicMock(return_value=VendorFail("ak down"))}
    )
    baostock = _MockProvider(
        "cn_baostock",
        {
            method: MagicMock(
                side_effect=NotImplementedError("baostock not supported")
            )
        },
    )
    investoday = _MockProvider(
        "cn_investoday",
        {method: MagicMock(return_value=VendorFail("investoday down"))},
    )

    # Date-blind providers set up to throw if called
    yf_mock = MagicMock(return_value="Snapshot PE: 30")
    av_mock = MagicMock(return_value='{"ReportDate": "2026-06-30"}')
    yfinance = _MockProvider("yfinance", {method: yf_mock})
    alpha_vantage = _MockProvider("alpha_vantage", {method: av_mock})

    providers = {
        "cn_fuyao": fuyao,
        "cn_akshare": akshare,
        "cn_baostock": baostock,
        "cn_investoday": investoday,
        "yfinance": yfinance,
        "alpha_vantage": alpha_vantage,
    }
    registry = _MockRegistry(providers)

    with patch.object(iface, "_registry", registry), patch.object(
        iface,
        "get_vendor",
        return_value="cn_fuyao,cn_akshare,cn_baostock,cn_investoday,yfinance",
    ):
        result = iface.route_to_vendor(method, *args, **kwargs)

    # 1. Assert date-blind providers were NEVER called
    yf_mock.assert_not_called()
    av_mock.assert_not_called()

    # 2. Assert return value is an explicit data gap refusal string
    assert isinstance(result, str)
    assert result.startswith("【数据获取失败】")
    assert "本项不可用" in result
    assert "不得回退到日期盲供应商" in result or "yfinance" in result
    # Must not contain snapshot or future content
    assert "Snapshot PE: 30" not in result
    assert "2026-06-30" not in result


# ── Contract 1 & 5: Direct provider calls return VendorRefuse ─────────────────


@pytest.mark.parametrize(
    "method,args,kwargs",
    [
        ("get_fundamentals", ("AAPL",), {"curr_date": HISTORICAL_DATE}),
        (
            "get_balance_sheet",
            ("AAPL", "quarterly"),
            {"curr_date": HISTORICAL_DATE},
        ),
        ("get_cashflow", ("AAPL", "quarterly"), {"curr_date": HISTORICAL_DATE}),
        (
            "get_income_statement",
            ("AAPL", "quarterly"),
            {"curr_date": HISTORICAL_DATE},
        ),
        ("get_insider_transactions", ("AAPL",), {"curr_date": HISTORICAL_DATE}),
    ],
)
def test_yfinance_provider_direct_call_refuses_historical(method, args, kwargs):
    """Direct calls to YFinanceProvider with historical curr_date must return VendorRefuse

    without calling the underlying yfinance library or API.
    """
    provider = YFinanceProvider()
    with patch("yfinance.Ticker") as mock_ticker:
        fn = getattr(provider, method)
        result = fn(*args, **kwargs)
        mock_ticker.assert_not_called()

    assert isinstance(result, VendorRefuse)
    assert result.to_prompt().startswith("【数据获取失败】")
    assert "本项不可用" in result.to_prompt()


@pytest.mark.parametrize(
    "method,args,kwargs",
    [
        ("get_fundamentals", ("IBM",), {"curr_date": HISTORICAL_DATE}),
        (
            "get_balance_sheet",
            ("IBM", "quarterly"),
            {"curr_date": HISTORICAL_DATE},
        ),
        ("get_cashflow", ("IBM", "quarterly"), {"curr_date": HISTORICAL_DATE}),
        (
            "get_income_statement",
            ("IBM", "quarterly"),
            {"curr_date": HISTORICAL_DATE},
        ),
        ("get_insider_transactions", ("IBM",), {"curr_date": HISTORICAL_DATE}),
    ],
)
def test_alpha_vantage_provider_direct_call_refuses_historical(
    method, args, kwargs
):
    """Direct calls to AlphaVantageProvider with historical curr_date must return VendorRefuse

    without calling the underlying API.
    """
    provider = AlphaVantageProvider()
    with patch(
        "tradingagents.dataflows.alpha_vantage_common._make_api_request"
    ) as mock_req:
        fn = getattr(provider, method)
        result = fn(*args, **kwargs)
        mock_req.assert_not_called()

    assert isinstance(result, VendorRefuse)
    assert result.to_prompt().startswith("【数据获取失败】")
    assert "本项不可用" in result.to_prompt()


# ── Contract 3: Valid historical peers continue to succeed ─────────────────────


def test_valid_historical_peer_succeeds_even_when_yfinance_in_chain():
    """A historical-capable peer in the chain succeeds normally and is not blocked."""
    peer_mock = MagicMock(
        return_value="## Fundamentals for 600519\nPE: 25.4\nReport Period: 2023Q4"
    )
    fuyao = _MockProvider("cn_fuyao", {"get_fundamentals": peer_mock})
    yf_mock = MagicMock(return_value="Snapshot PE: 30")
    yfinance = _MockProvider("yfinance", {"get_fundamentals": yf_mock})

    registry = _MockRegistry({"cn_fuyao": fuyao, "yfinance": yfinance})

    with patch.object(iface, "_registry", registry), patch.object(
        iface, "get_vendor", return_value="cn_fuyao,yfinance"
    ):
        out = iface.route_to_vendor(
            "get_fundamentals", "600519", curr_date=HISTORICAL_DATE
        )

    assert "PE: 25.4" in out
    peer_mock.assert_called_once_with("600519", curr_date=HISTORICAL_DATE)
    yf_mock.assert_not_called()


def test_custom_registered_historical_vendor_succeeds():
    """Any custom provider with historical capability succeeds without being blocked."""
    custom_mock = MagicMock(return_value="## Balance Sheet\nTotal Assets: 1000000")
    custom = _MockProvider("custom_vendor", {"get_balance_sheet": custom_mock})
    yf_mock = MagicMock(return_value="Snapshot Balance Sheet")
    yfinance = _MockProvider("yfinance", {"get_balance_sheet": yf_mock})

    registry = _MockRegistry({"custom_vendor": custom, "yfinance": yfinance})

    with patch.object(iface, "_registry", registry), patch.object(
        iface, "get_vendor", return_value="custom_vendor,yfinance"
    ):
        out = iface.route_to_vendor(
            "get_balance_sheet",
            "600519",
            "quarterly",
            curr_date=HISTORICAL_DATE,
        )

    assert "Total Assets: 1000000" in out
    custom_mock.assert_called_once()
    yf_mock.assert_not_called()


# ── Contract 4: Live/current date path and date-bus refusals ────────────────────


def test_live_current_date_allows_yfinance():
    """On current date (today), live yfinance path functions normally."""
    today = cn_today_str()
    yf_mock = MagicMock(return_value="## Fundamentals for 600519\nLive PE: 28.5")
    yfinance = _MockProvider("yfinance", {"get_fundamentals": yf_mock})
    registry = _MockRegistry({"yfinance": yfinance})

    with patch.object(iface, "_registry", registry), patch.object(
        iface, "get_vendor", return_value="yfinance"
    ):
        out = iface.route_to_vendor(
            "get_fundamentals", "600519", curr_date=today
        )

    assert "Live PE: 28.5" in out
    yf_mock.assert_called_once_with("600519", curr_date=today)


@pytest.mark.parametrize(
    "invalid_date,expected_phrase",
    [
        (None, "缺少分析日期"),
        ("", "缺少分析日期"),
        ("invalid-date-format", "分析日期无法解析"),
        ("2099-01-01", "晚于当前日期"),
    ],
)
def test_missing_invalid_future_date_bus_refusal(invalid_date, expected_phrase):
    """Missing, unparseable, or future analysis dates trigger bus refusal before calling providers."""
    yf_mock = MagicMock()
    yfinance = _MockProvider("yfinance", {"get_fundamentals": yf_mock})
    registry = _MockRegistry({"yfinance": yfinance})

    with patch.object(iface, "_registry", registry):
        out = iface.route_to_vendor(
            "get_fundamentals", "600519", curr_date=invalid_date
        )

    assert out.startswith("【数据获取失败】")
    assert expected_phrase in out
    yf_mock.assert_not_called()


# ── Contract 5: Method signatures and curr_date forwarding ────────────────────


def test_insider_transactions_signatures_accept_curr_date():
    """Verify get_insider_transactions in y_finance and alpha_vantage_news accept curr_date."""
    yfin_sig = inspect.signature(y_finance.get_insider_transactions)
    assert "curr_date" in yfin_sig.parameters

    av_sig = inspect.signature(alpha_vantage_news.get_insider_transactions)
    assert "curr_date" in av_sig.parameters


def test_insider_transactions_curr_date_forwarding():
    """Verify YFinanceProvider and AlphaVantageProvider forward curr_date on live calls."""
    today = cn_today_str()

    yf_provider = YFinanceProvider()
    with patch(
        "tradingagents.dataflows.providers.yfinance_provider.get_yfinance_insider_transactions",
        return_value="insider data",
    ) as mock_yf:
        out = yf_provider.get_insider_transactions("AAPL", curr_date=today)
        mock_yf.assert_called_once_with("AAPL", curr_date=today)

    av_provider = AlphaVantageProvider()
    with patch(
        "tradingagents.dataflows.providers.alpha_vantage_provider.get_alpha_vantage_insider_transactions",
        return_value="insider data",
    ) as mock_av:
        out = av_provider.get_insider_transactions("AAPL", curr_date=today)
        mock_av.assert_called_once_with("AAPL", curr_date=today)


# ── Contract 6: Legitimate zeros, negatives, and confirmed empty ───────────────


def test_legitimate_zero_and_negative_values_preserved():
    """Legitimate negative/zero values from historical providers must NOT be converted to data gaps."""
    negative_financials = (
        "## Income Statement for 600519\n"
        "Net Income: -5000000.0\n"
        "Operating Margin: 0.00%\n"
        "Dividend Yield: 0.00"
    )
    peer_mock = MagicMock(return_value=VendorOk(negative_financials))
    fuyao = _MockProvider("cn_fuyao", {"get_income_statement": peer_mock})
    registry = _MockRegistry({"cn_fuyao": fuyao})

    with patch.object(iface, "_registry", registry), patch.object(
        iface, "get_vendor", return_value="cn_fuyao"
    ):
        out = iface.route_to_vendor(
            "get_income_statement",
            "600519",
            "quarterly",
            curr_date=HISTORICAL_DATE,
        )

    assert "Net Income: -5000000.0" in out
    assert "Operating Margin: 0.00%" in out
    assert "【数据获取失败】" not in out


def test_confirmed_empty_distinct_from_refusal():
    """VendorEmpty represents confirmed zero events/records and must stop chain without gap refusal."""
    empty_msg = "【查询成功】该标的在历史期间确认无内部人交易记录（确认无数据）。"
    peer_mock = MagicMock(return_value=VendorEmpty(empty_msg))
    fuyao = _MockProvider("cn_fuyao", {"get_insider_transactions": peer_mock})
    yf_mock = MagicMock(return_value="Snapshot insider data")
    yfinance = _MockProvider("yfinance", {"get_insider_transactions": yf_mock})

    registry = _MockRegistry({"cn_fuyao": fuyao, "yfinance": yfinance})

    with patch.object(iface, "_registry", registry), patch.object(
        iface, "get_vendor", return_value="cn_fuyao,yfinance"
    ):
        out = iface.route_to_vendor(
            "get_insider_transactions", "600519", curr_date=HISTORICAL_DATE
        )

    assert out == empty_msg
    assert "【数据获取失败】" not in out
    yf_mock.assert_not_called()
