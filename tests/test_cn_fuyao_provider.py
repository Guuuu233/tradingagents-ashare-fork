"""Tests for 同花顺 (THS) fuyao.aicubes.cn provider + route wiring."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.dataflows import interface as iface
from tradingagents.dataflows.providers.base import ProviderResourcePolicy
from tradingagents.dataflows.providers.cn_fuyao_provider import (
    CnFuyaoProvider,
    FuyaoApiError,
)
from tradingagents.dataflows.trade_calendar import CN_TZ, DateDataUnavailable
from tradingagents.dataflows.vendor_result import VendorEmpty, VendorFail, VendorRefuse

FAST_POLICY = ProviderResourcePolicy(timeout_seconds=1.0, max_retries=0, max_concurrency=2)


def _mock_json_response(body: dict):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = body
    return mock_resp


def _ok_payload(item=None, extra_data=None):
    data = dict(extra_data or {})
    if item is not None:
        data["item"] = item
    return {"code": 0, "message": "success", "request_id": "r1", "data": data}


# ── thscode normalization ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519", "600519.SH"),
        ("600519.SH", "600519.SH"),
        ("600519.SS", "600519.SH"),
        ("SH600519", "600519.SH"),
        ("sh600519", "600519.SH"),
        ("000001.SZ", "000001.SZ"),
        ("300033", "300033.SZ"),
        ("430047", "430047.BJ"),
        ("688981", "688981.SH"),
        ("", None),
        ("INVALID", None),
        ("AAPL", None),
    ],
)
def test_normalize_thscode(raw, expected):
    assert CnFuyaoProvider._normalize_thscode(raw) == expected


# ── 行情快照 ──────────────────────────────────────────────────────────


def test_get_realtime_quotes_maps_fields_and_batches():
    body = _ok_payload(
        item=[
            {
                "thscode": "600519.SH",
                "ticker": "600519",
                "volume": 3098875,
                "turnover": 3937375200,
                "last_price": 1277.8,
                "price_change": 21.8,
                "price_change_ratio_pct": 1.735669,
                "open_price": 1252.08,
                "high_price": 1282,
                "low_price": 1250.21,
                "prev_price": 1256,
            }
        ],
        extra_data={"timestamp": 1784275991000},
    )
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="test-key"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ) as mock_get:
        out = provider.get_realtime_quotes(["600519.SH", "600519.SS"])

    mock_get.assert_called_once()
    call_kw = mock_get.call_args[1]
    assert call_kw["headers"] == {"X-api-key": "test-key"}
    assert call_kw["params"]["thscodes"] == "600519.SH"

    data = json.loads(out)
    assert "600519.SH" in data
    q = data["600519.SH"]
    assert q["price"] == 1277.8
    assert q["previous_close"] == 1256
    assert q["change"] == pytest.approx(21.8)
    assert q["change_pct"] == pytest.approx(1.735669)
    assert q["open"] == 1252.08
    assert q["high"] == 1282
    assert q["low"] == 1250.21
    assert q["volume"] == 3098875.0
    assert q["amount"] == 3937375200.0
    assert q["quote_time"] == "2026-07-17"
    assert q["source"] == "fuyao"


def test_get_realtime_quotes_no_api_key():
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value=""):
        with pytest.raises(NotImplementedError, match="API Key"):
            provider.get_realtime_quotes(["600519.SH"])


def test_get_realtime_quotes_empty_symbols():
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"):
        assert json.loads(provider.get_realtime_quotes([])) == {}
        assert json.loads(provider.get_realtime_quotes(["", "INVALID"])) == {}


def test_get_realtime_quotes_key_error_maps_to_not_implemented():
    body = {"code": 2001, "message": "invalid key", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="bad"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        with pytest.raises(NotImplementedError, match="API Key"):
            provider.get_realtime_quotes(["600519.SH"])


# ── 历史 K 线 ─────────────────────────────────────────────────────────


def test_get_stock_data_csv_from_historical():
    body = _ok_payload(
        item=[
            {
                "date_ms": CnFuyaoProvider._date_to_ms("2025-01-02"),
                "open_price": 10.0,
                "high_price": 11.0,
                "low_price": 9.5,
                "close_price": 10.5,
                "volume": 10000.0,
                "turnover": 105000.0,
            },
            {
                "date_ms": CnFuyaoProvider._date_to_ms("2025-01-03"),
                "open_price": 10.5,
                "high_price": 12.0,
                "low_price": 10.4,
                "close_price": 11.0,
                "volume": 12000.0,
                "turnover": 130000.0,
            },
        ]
    )
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ) as mock_get:
        out = provider.get_stock_data("600519.SH", "2025-01-02", "2025-01-03")

    assert "# Stock data for 600519.SH" in out
    assert "2025-01-02" in out
    assert "Open,High,Low,Close,Volume" in out
    call_kw = mock_get.call_args[1]
    assert call_kw["params"]["thscode"] == "600519.SH"
    assert call_kw["params"]["interval"] == "1d"
    assert call_kw["params"]["adjust"] == "forward"


def test_get_stock_data_10y_window_guard():
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"):
        with pytest.raises(ValueError, match="10 年"):
            provider.get_stock_data("600519.SH", "2010-01-01", "2026-01-01")


def test_get_stock_data_3001_maps_to_vendor_empty():
    body = {"code": 3001, "message": "标的不存在", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_stock_data("999999.SH", "2025-01-01", "2025-01-31")
    assert isinstance(out, VendorEmpty)
    assert "标的不存在" in out.message


# ── 三大报表 / 财务指标 ───────────────────────────────────────────────


_FUYAO_FINANCIAL_FIXTURE = (
    Path(__file__).parent / "fixtures" / "cn_fuyao" / "600873_sh_financial_reports.json"
)


def _fuyao_fixture_items(statement_kind):
    payload = json.loads(_FUYAO_FINANCIAL_FIXTURE.read_text(encoding="utf-8"))
    return payload["financials"][statement_kind]


def test_fuyao_financial_rows_expose_period_and_report_dates():
    provider = CnFuyaoProvider()
    body = _ok_payload(item=_fuyao_fixture_items("cashflow"))
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_cashflow("600873.SH", "quarterly", "2026-09-10")

    assert "2026-08-15" in out  # report_date_ms, not the request cutoff
    assert "2026-06-30" in out  # period_end_ms
    assert "分析日 2026-09-10" in out
    assert "请求截止日 2026-09-10" not in out
    assert "period_kind=half_year_cumulative" in out
    from tradingagents.graph.data_collector import _extract_source_as_of

    assert _extract_source_as_of(out, "2026-09-10") == "2026-08-15"
    assert "period_kind" in out and "period_end" in out and "fiscal_period" in out
    assert "（单季度" not in out
    assert "禁止把 H1 累计当作 Q2 单季使用" in out
    assert "derivation_formula=H1-Q1" in out


def test_fuyao_financial_period_kinds_cover_all_report_ends():
    def row(period, report_date):
        month_day = {"Q1": "0331", "Q2": "0630", "Q3": "0930", "Q4": "1231"}[period]
        period_end = f"2026{month_day}"
        return {
            "fiscal_year": 2026,
            "fiscal_period": period,
            "report_date_ms": CnFuyaoProvider._date_to_ms(report_date),
            "period_end_ms": CnFuyaoProvider._date_to_ms(
                f"2026-{month_day[:2]}-{month_day[2:]}"
            ),
            "currency": "CNY",
            "operating_income": 100.0,
        }

    provider = CnFuyaoProvider()
    body = _ok_payload(
        item=[
            row("Q1", "2026-04-20"),
            row("Q2", "2026-08-15"),
            row("Q3", "2026-10-20"),
            row("Q4", "2027-03-20"),
        ]
    )
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        income = provider.get_income_statement("600873.SH", "quarterly", "2027-04-01")

    for kind in (
        "first_quarter",
        "half_year_cumulative",
        "nine_month_cumulative",
        "annual_cumulative",
    ):
        assert f"period_kind={kind}" in income

    balance_body = _ok_payload(item=_fuyao_fixture_items("balance"))
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(balance_body),
         ):
        balance = provider.get_balance_sheet("600873.SH", "quarterly", "2026-09-10")
    assert "period_kind=period_end_stock" in balance


def test_fuyao_q2_derivation_refuses_when_q1_is_missing():
    provider = CnFuyaoProvider()
    q2 = _fuyao_fixture_items("cashflow")[0]
    body = _ok_payload(item=[q2])
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_cashflow("600873.SH", "quarterly", "2026-09-10")

    assert "Q2_single_quarter=N/A" in out
    assert "reason=missing_q1" in out
    assert "禁止把 H1 累计当作 Q2 单季使用" in out


def test_fuyao_future_report_date_is_explicitly_fail_closed():
    provider = CnFuyaoProvider()
    row = dict(_fuyao_fixture_items("cashflow")[0])
    row["report_date_ms"] = CnFuyaoProvider._date_to_ms("2026-09-11")
    body = _ok_payload(item=[row])
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_cashflow("600873.SH", "quarterly", "2026-09-10")

    assert "report_date_status" in out
    assert "future" in out
    assert "2026-09-11" in out
    assert "389140000" not in out  # future financial values are fail-closed
    assert "reason=future_report_date" in out
    assert "derivation_formula=H1-Q1" not in out


def test_fuyao_zero_timestamp_falls_back_to_fiscal_period():
    row = {
        "fiscal_year": 2026,
        "fiscal_period": "Q2",
        "period_end_ms": 0,
        "report_date_ms": 0,
        "currency": "CNY",
        "act_cash_flow_net": 1.0,
    }
    df = CnFuyaoProvider._annotate_financial_rows(
        [row], "cashflow", "2026-09-10"
    )
    assert CnFuyaoProvider._ms_to_date_str(0) is None
    assert df.iloc[0]["period_end"] == "2026-06-30"
    assert df.iloc[0]["report_date"] == "unknown"
    assert df.iloc[0]["report_date_status"] == "missing"


def test_get_income_statement_markdown():
    body = _ok_payload(
        item=[
            {
                "thscode": "600519.SH",
                "fiscal_year": 2024,
                "fiscal_period": "FY",
                "operating_income": 174144000000,
                "net_profit": 93000000000,
                "basic_eps": 68.50,
            }
        ]
    )
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ) as mock_get:
        out = provider.get_income_statement("600519.SH", "annual", "2026-08-05")

    assert "利润表" in out
    assert "同花顺 fuyao" in out
    assert "operating_income" in out or "174144000000" in out
    call_kw = mock_get.call_args[1]
    assert call_kw["params"]["thscode"] == "600519.SH"
    assert call_kw["params"]["period"] == "annual"
    assert call_kw["params"]["start"] < call_kw["params"]["end"]


def test_financial_missing_curr_date_refuses():
    provider = CnFuyaoProvider()
    out = provider.get_income_statement("600519.SH", "annual", None)
    assert "缺少 curr_date" in out


def test_get_fundamentals_indicators():
    body = {
        "code": 0,
        "message": "success",
        "request_id": "r1",
        "data": {
            "thscode": "600519.SH",
            "report": "2026-1",
            "abilities": [
                {
                    "ability": "growth",
                    "indicators": [
                        {"index_id": "total_assets_growth_ratio", "value": "-16.0031"}
                    ],
                },
                {
                    "ability": "profitability",
                    "indicators": [
                        {"index_id": "sale_gross_margin", "value": "89.12"},
                        {"index_id": "earned_interest_multiple", "value": None},
                    ],
                },
            ],
        },
    }
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ) as mock_get:
        out = provider.get_fundamentals("600519.SH", "2026-08-05")

    assert "Fundamentals" in out
    assert "成长能力" in out
    assert "盈利能力" in out
    assert "total_assets_growth_ratio=-16.0031" in out
    assert "earned_interest_multiple=缺失" in out
    assert mock_get.call_args[1]["params"]["report"] == "2026-1"


@pytest.mark.parametrize("missing_date", [None, "", "   ", "\t\n"])
def test_get_fundamentals_missing_curr_date_refuses_without_request(missing_date):
    provider = CnFuyaoProvider()
    with patch.object(
        provider,
        "_request_fuyao",
        side_effect=AssertionError("must not call _request_fuyao without curr_date"),
    ) as mock_request, patch.object(
        provider,
        "_latest_report_period",
        side_effect=AssertionError("must not infer report period without curr_date"),
    ) as mock_infer:
        out = provider.get_fundamentals("600519.SH", curr_date=missing_date)

    mock_request.assert_not_called()
    mock_infer.assert_not_called()
    assert isinstance(out, str)
    assert "【数据获取失败】" in out
    assert "缺少 curr_date" in out
    assert "内部层不得默认今天，本项不可用。" in out


@pytest.mark.parametrize("invalid_date", [None, "", "   ", "\t\n"])
def test_latest_report_period_missing_date_raises(invalid_date):
    with pytest.raises(ValueError, match="缺少 curr_date"):
        CnFuyaoProvider._latest_report_period(invalid_date)


@pytest.mark.parametrize(
    ("curr_date", "expected_report"),
    [
        ("2026-01-01", "2025-4"),
        ("2026-04-29", "2025-4"),
        ("2026-04-30", "2026-1"),
        ("2026-08-05", "2026-1"),
        ("2026-08-30", "2026-1"),
        ("2026-08-31", "2026-2"),
        ("2026-10-30", "2026-2"),
        ("2026-10-31", "2026-3"),
        ("2026-12-31", "2026-3"),
    ],
)
def test_latest_report_period_effective_mapping(curr_date, expected_report):
    assert CnFuyaoProvider._latest_report_period(curr_date) == expected_report


@pytest.mark.parametrize(
    ("curr_date", "expected_report"),
    [
        ("2026-01-15", "2025-4"),
        ("2026-04-30", "2026-1"),
        ("2026-08-31", "2026-2"),
        ("2026-10-31", "2026-3"),
    ],
)
def test_get_fundamentals_valid_curr_date_preserves_request_param(curr_date, expected_report):
    body = {
        "code": 0,
        "message": "success",
        "request_id": "r1",
        "data": {
            "thscode": "600519.SH",
            "report": expected_report,
            "abilities": [
                {
                    "ability": "growth",
                    "indicators": [{"index_id": "total_assets_growth_ratio", "value": "10.5"}],
                }
            ],
        },
    }
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ) as mock_get:
        out = provider.get_fundamentals("600519.SH", curr_date=curr_date)

    assert "Fundamentals" in out
    assert mock_get.call_args[1]["params"]["report"] == expected_report
    assert mock_get.call_args[1]["params"]["thscode"] == "600519.SH"


# ── 财务路径 3001/3002 分治（fuyao 主源 → 弱源降级）──────────────────


def test_get_fundamentals_3001_maps_to_vendor_fail():
    """财务指标路径：3001（标的不存在/未覆盖）→ VendorFail，触发弱源降级。"""
    body = {"code": 3001, "message": "标的不存在", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_fundamentals("999999.SH", "2026-08-05")
    assert isinstance(out, VendorFail)
    assert "3001" in out.error


def test_get_income_statement_3001_maps_to_vendor_fail():
    """三大报表路径：3001 → VendorFail（与 get_fundamentals 一致）。"""
    body = {"code": 3001, "message": "标的不存在", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_income_statement("999999.SH", "annual", "2026-08-05")
    assert isinstance(out, VendorFail)
    assert "3001" in out.error


def test_get_fundamentals_3002_maps_to_vendor_empty():
    """财务指标路径：3002（数据未就绪）保持 VendorEmpty（确认无数据）。"""
    body = {"code": 3002, "message": "数据未就绪", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_fundamentals("600519.SH", "2026-08-05")
    assert isinstance(out, VendorEmpty)
    assert "3002" in out.message


# ── 错误码映射 ────────────────────────────────────────────────────────


def test_1002_param_error_raises_value_error():
    body = {"code": 1002, "message": "参数格式错误", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        with pytest.raises(ValueError, match="参数错误"):
            provider.get_stock_data("600519.SH", "2025-01-01", "2025-01-31")


def test_5001_server_error_maps_to_vendor_fail():
    body = {"code": 5001, "message": "服务内部错误", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_stock_data("600519.SH", "2025-01-01", "2025-01-31")
    assert isinstance(out, VendorFail)
    assert "服务端错误" in out.error


def test_4001_rate_limit_retries_then_vendor_fail():
    rate_limited = _mock_json_response({"code": 4001, "message": "频率超限", "data": None})
    server_error = _mock_json_response({"code": 5003, "message": "上游不可用", "data": None})
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch.object(provider, "_RATE_LIMIT_RETRIES", 1), \
         patch.object(provider, "_RATE_LIMIT_BACKOFF_SECONDS", 0), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             side_effect=[rate_limited, server_error],
         ) as mock_get:
        out = provider.get_stock_data("600519.SH", "2025-01-01", "2025-01-31")

    assert mock_get.call_count == 2
    assert isinstance(out, VendorFail)


# ── 涨跌停池（分页） / 龙虎榜 ─────────────────────────────────────────


def test_get_zt_pool_paginates_and_formats():
    page1 = _ok_payload(
        item=[
            {
                "thscode": "603986.SH",
                "name": "兆易创新",
                "continue_day_text": "2连板",
                "continue_day_cnt": 2,
                "price_change_ratio_pct": 10.0,
                "limit_up_time": "09:34",
                "limit_up_reason": "存储芯片",
            }
        ],
        extra_data={
            "pagination": {"total": 3, "pages": 2, "size": 200, "page": 1},
            "timestamp": 1748102400000,
        },
    )
    page2 = _ok_payload(
        item=[
            {
                "thscode": "000001.SZ",
                "name": "平安银行",
                "continue_day_text": "首板",
                "continue_day_cnt": 1,
                "price_change_ratio_pct": 9.9,
                "limit_up_time": "10:01",
                "limit_up_reason": "低估值",
            }
        ],
        extra_data={
            "pagination": {"total": 3, "pages": 2, "size": 200, "page": 2},
            "timestamp": 1748102400000,
        },
    )
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             side_effect=[_mock_json_response(page1), _mock_json_response(page2)],
         ) as mock_get:
        out = provider.get_zt_pool("2026-08-04")

    assert "共 3 只" in out
    assert "兆易创新" in out
    assert "平安银行" in out
    assert "2连板 1只" in out
    assert "请求日期】2026-08-04" in out
    assert "实际数据日期】2026-08-04" in out
    assert mock_get.call_count == 2
    # 第二页分页参数正确
    page2_params = mock_get.call_args_list[1][1]["params"]
    assert page2_params["page"] == 2
    assert page2_params["date_ms"] == CnFuyaoProvider._date_to_ms("2026-08-04")


def test_get_zt_pool_fallback_preserves_requested_and_actual_dates():
    provider = CnFuyaoProvider()
    with patch.object(provider, "_fetch_zt_pool_for_day", side_effect=[DateDataUnavailable("requested"), "涨停池（2026-08-03，同花顺 fuyao）：共 1 只"]), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.fetch_with_date_fallback") as fallback:
        fallback.return_value = SimpleNamespace(
            ok=True,
            request_date="2026-08-04",
            as_of="2026-08-03",
            attempted=["2026-08-04", "2026-08-03"],
            data="涨停池（2026-08-03，同花顺 fuyao）：共 1 只",
        )
        out = provider.get_zt_pool("2026-08-04")
    assert "请求日期】2026-08-04" in out
    assert "实际数据日期】2026-08-03" in out
    assert "涨停池（2026-08-03" in out


def test_get_zt_pool_missing_date_refuses():
    provider = CnFuyaoProvider()
    out = provider.get_zt_pool(None)
    assert "缺少 date" in out


def test_get_lhb_detail_filters_by_symbol():
    from tradingagents.dataflows import trade_calendar as tc

    tc.clear_cn_trade_date_cache()
    tc._TRADE_DATES_CACHE["dates"] = [pd.Timestamp("2026-07-01").date()]
    tc._TRADE_DATES_CACHE["dates_set"] = {pd.Timestamp("2026-07-01").date()}
    tc._TRADE_DATES_CACHE["loaded_at"] = 1e18

    body = {
        "code": 0,
        "message": "success",
        "request_id": "r1",
        "data": {
            "timestamp": 1782921600000,
            "board_type": "all",
            "trade_date": "2026-07-01",
            "count": 1,
            "stock_count": 1,
            "stock_items": [
                {
                    "thscode": "002407.SZ",
                    "ticker": "002407",
                    "name": "多氟多",
                    "change": 0.09994,
                    "net_value": 1786253128.23,
                    "net_rate": 0.119,
                    "buy_value": 2674755016.05,
                    "sell_value": 888501887.82,
                    "limit_reason": "六氟磷酸锂涨价",
                    "range_days": 3,
                }
            ],
            "hot_money_items": [],
        },
    }
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ) as mock_get:
        out = provider.get_lhb_detail("002407", "2026-07-01")

    assert "龙虎榜明细" in out
    assert "多氟多" in out
    assert mock_get.call_args[1]["params"]["date"] == "2026-07-01"
    assert mock_get.call_args[1]["params"]["board_type"] == "all"
    tc.clear_cn_trade_date_cache()


def test_get_lhb_detail_not_on_board_is_normal_empty():
    from tradingagents.dataflows import trade_calendar as tc

    tc.clear_cn_trade_date_cache()
    tc._TRADE_DATES_CACHE["dates"] = [pd.Timestamp("2026-07-01").date()]
    tc._TRADE_DATES_CACHE["dates_set"] = {pd.Timestamp("2026-07-01").date()}
    tc._TRADE_DATES_CACHE["loaded_at"] = 1e18

    body = {
        "code": 0,
        "message": "success",
        "request_id": "r1",
        "data": {
            "trade_date": "2026-07-01",
            "stock_items": [{"thscode": "002407.SZ", "name": "多氟多"}],
            "hot_money_items": [],
        },
    }
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_lhb_detail("600519", "2026-07-01")
    assert "无龙虎榜数据" in out
    tc.clear_cn_trade_date_cache()


def _seed_fuyao_calendar(days: list[str]) -> None:
    from datetime import date
    from tradingagents.dataflows import trade_calendar as tc

    dates = [date.fromisoformat(d) for d in days]
    tc.clear_cn_trade_date_cache()
    tc._TRADE_DATES_CACHE["dates"] = dates
    tc._TRADE_DATES_CACHE["dates_set"] = set(dates)
    tc._TRADE_DATES_CACHE["loaded_at"] = 1e18


def test_fuyao_api_error_hierarchy_and_scoping():
    from tradingagents.dataflows.providers.cn_fuyao_provider import (
        FuyaoApiError,
        FuyaoRateLimitFatalError,
    )
    from tradingagents.dataflows.trade_calendar import DateFetchFatalError

    # 核心断言：FuyaoApiError 严禁整体继承 DateFetchFatalError
    assert not issubclass(FuyaoApiError, DateFetchFatalError)
    assert issubclass(FuyaoRateLimitFatalError, FuyaoApiError)
    assert issubclass(FuyaoRateLimitFatalError, DateFetchFatalError)

    # 1001/2001/3004/5001 等普通 API 错误不是 fatal
    err_5001 = FuyaoApiError(5001, "服务端错误")
    assert not isinstance(err_5001, DateFetchFatalError)
    err_1001 = FuyaoApiError(1001, "参数错误")
    assert not isinstance(err_1001, DateFetchFatalError)

    # 只有 4001 专用类是 fatal
    err_4001 = FuyaoRateLimitFatalError(4001, "频率超限")
    assert isinstance(err_4001, DateFetchFatalError)
    assert isinstance(err_4001, FuyaoApiError)
    assert err_4001.code == 4001
    assert err_4001.message == "频率超限"


def test_get_lhb_detail_4001_aborts_date_fallback_and_returns_vendor_fail():
    from tradingagents.dataflows import trade_calendar as tc

    _seed_fuyao_calendar(["2026-08-03", "2026-08-04", "2026-08-05"])
    rate_limited_body = {"code": 4001, "message": "频率超限", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch.object(provider, "_RATE_LIMIT_RETRIES", 0), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(rate_limited_body),
         ) as mock_get:
        out = provider.get_lhb_detail("600519", "2026-08-05")

    assert isinstance(out, VendorFail)
    assert "4001" in out.error
    assert "频率超限" in out.error
    # 核心断言：收到 4001 fatal 错误立即中止日期回退，不得触发第二个日期的请求
    assert mock_get.call_count == 1
    assert mock_get.call_args[1]["params"]["date"] == "2026-08-05"
    tc.clear_cn_trade_date_cache()


def test_get_lhb_detail_4001_with_retries_exhausted_never_calls_second_date():
    from tradingagents.dataflows import trade_calendar as tc

    _seed_fuyao_calendar(["2026-08-03", "2026-08-04", "2026-08-05"])
    rate_limited_body = {"code": 4001, "message": "频率超限", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch.object(provider, "_RATE_LIMIT_RETRIES", 2), \
         patch.object(provider, "_RATE_LIMIT_BACKOFF_SECONDS", 0), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(rate_limited_body),
         ) as mock_get:
        out = provider.get_lhb_detail("600519", "2026-08-05")

    assert isinstance(out, VendorFail)
    assert "4001" in out.error
    assert "频率超限" in out.error
    # 内部重试 2 次（共 3 次），全部针对请求日 2026-08-05，不得请求更早交易日 2026-08-04
    assert mock_get.call_count == 3
    for call in mock_get.call_args_list:
        assert call[1]["params"]["date"] == "2026-08-05"
    tc.clear_cn_trade_date_cache()


@pytest.mark.parametrize("code,message", [(3001, "标的不存在"), (3002, "数据未就绪")])
def test_get_lhb_detail_3001_and_3002_allow_fallback(code, message):
    from tradingagents.dataflows import trade_calendar as tc

    _seed_fuyao_calendar(["2026-08-04", "2026-08-05"])
    empty_body = {"code": code, "message": message, "data": None}
    success_body = {
        "code": 0,
        "message": "success",
        "request_id": "r1",
        "data": {
            "trade_date": "2026-08-04",
            "stock_items": [
                {
                    "thscode": "600519.SH",
                    "name": "贵州茅台",
                    "change": 0.05,
                    "net_value": 5000000.0,
                    "net_rate": 0.02,
                    "buy_value": 10000000.0,
                    "sell_value": 5000000.0,
                    "range_days": 1,
                    "limit_reason": "白酒反弹",
                }
            ],
            "hot_money_items": [],
        },
    }
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             side_effect=[_mock_json_response(empty_body), _mock_json_response(success_body)],
         ) as mock_get:
        out = provider.get_lhb_detail("600519", "2026-08-05")

    assert isinstance(out, str)
    assert "龙虎榜明细（2026-08-04，同花顺 fuyao）" in out
    assert "贵州茅台" in out
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0][1]["params"]["date"] == "2026-08-05"
    assert mock_get.call_args_list[1][1]["params"]["date"] == "2026-08-04"
    tc.clear_cn_trade_date_cache()


def test_get_lhb_detail_3001_then_4001_aborts_at_second_date_without_third_date():
    from tradingagents.dataflows import trade_calendar as tc

    _seed_fuyao_calendar(["2026-08-03", "2026-08-04", "2026-08-05"])
    body_3001 = {"code": 3001, "message": "标的不存在", "data": None}
    body_4001 = {"code": 4001, "message": "频率超限", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch.object(provider, "_RATE_LIMIT_RETRIES", 0), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             side_effect=[_mock_json_response(body_3001), _mock_json_response(body_4001)],
         ) as mock_get:
        out = provider.get_lhb_detail("600519", "2026-08-05")

    assert isinstance(out, VendorFail)
    assert "4001" in out.error
    assert "频率超限" in out.error
    # 第一次 2026-08-05（3001 回退），第二次 2026-08-04（4001 限流立即中止），严禁请求第三天 2026-08-03
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0][1]["params"]["date"] == "2026-08-05"
    assert mock_get.call_args_list[1][1]["params"]["date"] == "2026-08-04"
    tc.clear_cn_trade_date_cache()


def test_get_lhb_detail_all_dates_3001_returns_vendor_fail():
    from tradingagents.dataflows import trade_calendar as tc

    _seed_fuyao_calendar(["2026-08-03", "2026-08-04", "2026-08-05"])
    body_3001 = {"code": 3001, "message": "标的不存在", "data": None}
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             side_effect=[
                 _mock_json_response(body_3001),
                 _mock_json_response(body_3001),
                 _mock_json_response(body_3001),
             ],
         ) as mock_get:
        out = provider.get_lhb_detail("600519", "2026-08-05")

    assert isinstance(out, VendorFail)
    assert "龙虎榜数据获取失败（同花顺 fuyao）" in out.error
    assert "已尝试 2026-08-05 至 2026-08-03 共 3 个交易日，均无数据" in out.error
    assert mock_get.call_count == 3
    tc.clear_cn_trade_date_cache()


def test_get_lhb_detail_non_4001_fuyao_error_continues_fallback():
    from tradingagents.dataflows import trade_calendar as tc

    _seed_fuyao_calendar(["2026-08-04", "2026-08-05"])
    body_5001 = {"code": 5001, "message": "服务端错误", "data": None}
    success_body = {
        "code": 0,
        "message": "success",
        "request_id": "r1",
        "data": {
            "trade_date": "2026-08-04",
            "stock_items": [
                {
                    "thscode": "600519.SH",
                    "name": "贵州茅台",
                    "change": 0.03,
                    "net_value": 3000000.0,
                    "net_rate": 0.01,
                    "buy_value": 8000000.0,
                    "sell_value": 5000000.0,
                    "range_days": 1,
                    "limit_reason": "机构买入",
                }
            ],
            "hot_money_items": [],
        },
    }
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             side_effect=[_mock_json_response(body_5001), _mock_json_response(success_body)],
         ) as mock_get:
        out = provider.get_lhb_detail("600519", "2026-08-05")

    assert isinstance(out, str)
    assert "龙虎榜明细（2026-08-04，同花顺 fuyao）" in out
    assert "贵州茅台" in out
    # 5001 非 4001，正常继续回退到 2026-08-04
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0][1]["params"]["date"] == "2026-08-05"
    assert mock_get.call_args_list[1][1]["params"]["date"] == "2026-08-04"
    tc.clear_cn_trade_date_cache()


def test_get_zt_pool_behavior_matches_baseline_parent():
    from tradingagents.dataflows import trade_calendar as tc

    _seed_fuyao_calendar(["2026-08-03", "2026-08-04", "2026-08-05"])
    provider = CnFuyaoProvider()

    # 1. 首日 4001：直接由外层 FuyaoApiError 捕获映射为 VendorFail，不调 fetch_with_date_fallback
    body_4001 = {"code": 4001, "message": "频率超限", "data": None}
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch.object(provider, "_RATE_LIMIT_RETRIES", 0), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body_4001),
         ) as mock_get:
        out = provider.get_zt_pool("2026-08-05")

    assert isinstance(out, VendorFail)
    assert "4001" in out.error
    assert "频率超限" in out.error
    assert mock_get.call_count == 1
    assert mock_get.call_args[1]["params"]["date_ms"] == provider._date_to_ms("2026-08-05")

    # 2. 首日 3001，次日 5001：不作为 fatal 中止，继续回退到第三日（成功）
    body_3001 = {"code": 3001, "message": "标的不存在", "data": None}
    body_5001 = {"code": 5001, "message": "服务端错误", "data": None}
    success_body = {
        "code": 0,
        "message": "success",
        "data": {
            "pagination": {"pages": 1},
            "item": [{"thscode": "000001.SZ", "name": "平安银行", "continue_day_cnt": 1}],
        },
    }
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             side_effect=[
                 _mock_json_response(body_3001),  # direct try 2026-08-05
                 _mock_json_response(body_3001),  # fallback try 2026-08-05
                 _mock_json_response(body_5001),  # fallback try 2026-08-04 (continues!)
                 _mock_json_response(success_body),  # fallback try 2026-08-03
             ],
         ) as mock_get:
        out = provider.get_zt_pool("2026-08-05")

    assert isinstance(out, str)
    assert "【实际数据日期】2026-08-03" in out
    tc.clear_cn_trade_date_cache()


# ── 交易日历 ──────────────────────────────────────────────────────────


def test_get_trading_days_returns_dates():
    body = _ok_payload(
        item=[
            {"date_ms": 1716566400000, "date": "20250525"},
            {"date_ms": 1716652800000, "date": "20250526"},
        ]
    )
    provider = CnFuyaoProvider()
    with patch.object(provider, "_resolve_api_key", return_value="k"), \
         patch(
             "tradingagents.dataflows.providers.cn_fuyao_provider.requests.get",
             return_value=_mock_json_response(body),
         ):
        out = provider.get_trading_days()
    assert "20250525" in out
    assert "20250526" in out
    assert "共 2 个交易日" in out


# ── registry / config ─────────────────────────────────────────────────


def test_build_default_registry_includes_cn_fuyao():
    from tradingagents.dataflows.providers.registry import build_default_registry

    reg = build_default_registry()
    assert "cn_fuyao" in reg.list_names()
    assert reg.get("cn_fuyao") is not None


def test_default_config_routes_fundamentals_to_fuyao_primary():
    from tradingagents.dataflows.interface import get_vendor

    chain = get_vendor("fundamental_data")
    assert chain.split(",")[0] == "cn_fuyao"
    assert "cn_akshare" in chain


def test_default_config_zt_lhb_route_akshare_then_fuyao():
    from tradingagents.dataflows.interface import get_vendor

    assert get_vendor("cn_market_data", "get_zt_pool") == "cn_akshare,cn_fuyao"
    assert get_vendor("cn_market_data", "get_lhb_detail") == "cn_akshare,cn_fuyao"


# ── 不支持方法显式回退 ────────────────────────────────────────────────


def test_unsupported_methods_raise_not_implemented():
    provider = CnFuyaoProvider()
    with pytest.raises(NotImplementedError):
        provider.get_indicators("600519.SH", "rsi", "2026-08-05", 14)
    with pytest.raises(NotImplementedError):
        provider.get_news("600519.SH", "2026-01-01", "2026-01-31")
    with pytest.raises(NotImplementedError):
        provider.get_insider_transactions("600519.SH")


# ── route 接线 ────────────────────────────────────────────────────────


class _FakeProvider:
    def __init__(self, name, func, method):
        self.name = name
        self._func = func
        self._method = method

    def __getattr__(self, attr):
        if attr == self._method:
            return self._func
        raise AttributeError(attr)


class _FakeRegistry:
    def __init__(self, providers):
        self._providers = providers

    def list_names(self):
        return list(self._providers)

    def get(self, name):
        return self._providers.get(name)

    def resource_policy(self, name):
        return FAST_POLICY


def _route(chain: dict[str, object], configured: str, method: str, *args):
    registry = _FakeRegistry(chain)
    with patch.object(iface, "_registry", registry), \
         patch.object(iface, "get_vendor", return_value=configured):
        return iface.route_to_vendor(method, *args)


def test_route_fundamentals_uses_fuyao_primary_before_akshare():
    fuyao = _FakeProvider(
        "cn_fuyao",
        lambda *a, **k: "## 利润表（同花顺 fuyao）",
        method="get_income_statement",
    )
    akshare = _FakeProvider(
        "cn_akshare",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("akshare must not be called")),
        method="get_income_statement",
    )
    out = _route(
        {"cn_fuyao": fuyao, "cn_akshare": akshare},
        "cn_fuyao,cn_akshare",
        "get_income_statement",
        "600519.SH",
        "annual",
        "2026-08-05",
    )
    assert out == "## 利润表（同花顺 fuyao）"


def test_route_fundamentals_fuyao_3001_falls_back_to_weak_source():
    """fuyao 财务路径 3001 → VendorFail：接线应降级到弱源（cn_akshare）。"""
    fuyao = _FakeProvider(
        "cn_fuyao",
        lambda *a, **k: VendorFail("标的不存在（code=3001，财务路径降级到弱源）"),
        method="get_fundamentals",
    )
    akshare = _FakeProvider(
        "cn_akshare",
        lambda *a, **k: "## Fundamentals（cn_akshare 弱源）",
        method="get_fundamentals",
    )
    out = _route(
        {"cn_fuyao": fuyao, "cn_akshare": akshare},
        "cn_fuyao,cn_akshare",
        "get_fundamentals",
        "999999.SH",
        "2026-08-05",
    )
    assert "cn_akshare" in out


def test_route_zt_pool_falls_back_to_fuyao_when_akshare_refuses_historical():
    akshare = _FakeProvider(
        "cn_akshare",
        lambda *a, **k: VendorRefuse(
            "该数据源仅提供当前快照，无法用于历史日期分析，本项不可用",
            allow_peers=("cn_fuyao",),
        ),
        method="get_zt_pool",
    )
    fuyao = _FakeProvider(
        "cn_fuyao",
        lambda *a, **k: "涨停池（2026-08-04，同花顺 fuyao）：共 3 只",
        method="get_zt_pool",
    )
    out = _route(
        {"cn_akshare": akshare, "cn_fuyao": fuyao},
        "cn_akshare,cn_fuyao",
        "get_zt_pool",
        "2026-08-04",
    )
    assert "同花顺 fuyao" in out


def test_route_zt_pool_falls_back_to_fuyao_when_akshare_vendor_fail():
    akshare = _FakeProvider(
        "cn_akshare",
        lambda *a, **k: VendorFail("东财接口失败"),
        method="get_zt_pool",
    )
    fuyao = _FakeProvider(
        "cn_fuyao",
        lambda *a, **k: "涨停池（2026-08-04，同花顺 fuyao）：共 3 只",
        method="get_zt_pool",
    )
    out = _route(
        {"cn_akshare": akshare, "cn_fuyao": fuyao},
        "cn_akshare,cn_fuyao",
        "get_zt_pool",
        "2026-08-04",
    )
    assert "同花顺 fuyao" in out


def test_route_lhb_falls_back_to_fuyao_when_akshare_vendor_fail():
    akshare = _FakeProvider(
        "cn_akshare",
        lambda *a, **k: VendorFail("东财龙虎榜失败"),
        method="get_lhb_detail",
    )
    fuyao = _FakeProvider(
        "cn_fuyao",
        lambda *a, **k: "600519 龙虎榜明细（2026-08-04，同花顺 fuyao）",
        method="get_lhb_detail",
    )
    out = _route(
        {"cn_akshare": akshare, "cn_fuyao": fuyao},
        "cn_akshare,cn_fuyao",
        "get_lhb_detail",
        "600519.SH",
        "2026-08-04",
    )
    assert "同花顺 fuyao" in out


# ── akshare 东财失败 → VendorFail（保证链路可切到 fuyao）────────────


def test_akshare_get_zt_pool_failure_is_vendor_fail(frozen_trade_date):
    from tradingagents.dataflows import trade_calendar as tc
    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

    tc.clear_cn_trade_date_cache()
    today = pd.Timestamp(tc.cn_today_str()).date()
    tc._TRADE_DATES_CACHE["dates"] = [today]
    tc._TRADE_DATES_CACHE["dates_set"] = {today}
    tc._TRADE_DATES_CACHE["loaded_at"] = 1e18

    ak = MagicMock()
    ak.stock_zt_pool_em.side_effect = ConnectionError("RemoteDisconnected")
    p = CnAkshareProvider()
    p._ak = lambda: ak
    out = p.get_zt_pool(tc.cn_today_str())
    assert isinstance(out, VendorFail)
    assert "涨停板情绪池数据获取失败" in out.error
    tc.clear_cn_trade_date_cache()


def test_akshare_get_lhb_detail_double_failure_is_vendor_fail():
    from tradingagents.dataflows import trade_calendar as tc
    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

    tc.clear_cn_trade_date_cache()
    tc._TRADE_DATES_CACHE["dates"] = [pd.Timestamp("2026-08-03").date()]
    tc._TRADE_DATES_CACHE["dates_set"] = {pd.Timestamp("2026-08-03").date()}
    tc._TRADE_DATES_CACHE["loaded_at"] = 1e18

    ak = MagicMock()
    ak.stock_lhb_detail_em.side_effect = ConnectionError("RemoteDisconnected")
    ak.stock_lhb_detail_daily_sina.side_effect = ConnectionError("sina down")
    p = CnAkshareProvider()
    p._ak = lambda: ak
    out = p.get_lhb_detail("600519", "2026-08-03")
    assert isinstance(out, VendorFail)
    assert "龙虎榜数据获取失败" in out.error
    tc.clear_cn_trade_date_cache()
