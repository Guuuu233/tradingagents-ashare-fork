"""Unit and integration tests for Fuyao limit-up ladder capability (P1-F / DAV-905).

Red team acceptance criteria:
1. Legal full 30-day fixture with all 6 board keys and source metadata; seal_nextday=null preserved.
2. Legal full 30-day fixture where all 6 boards are empty lists ([]); retains available status.
3. Envelope missing, window/date_list type error, illegal dates, length contradiction, missing board keys -> explicit typed failure (VendorFail).
4. HTTP 4xx/5xx, Fuyao 4001, invalid key, no data error semantics; assert NO switch to AkShare, no synthesis.
5. Historical analysis date refused before network call; assert request count is 0.
6. Return window containing date later than requested as-of refused with VendorFail; no cropping, no iloc.
7. seal_nextday=null preserved as None, not inferred.
8. Existing get_zt_pool parent behavior and fields unchanged.
9. market_attention.limit_up_ladder and zt_pool separate columns; prompt displays source/as_of/status/disclaimer; no direction signals.
10. Trace/return structure includes requested as-of, upstream window, source; failure paths traceable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from tradingagents.agents.utils.game_theory_tools import (
    fetch_limit_up_ladder,
    get_limit_up_ladder,
)
from tradingagents.dataflows import interface as iface
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.providers.cn_fuyao_provider import (
    _LADDER_BOARD_KEYS,
    CnFuyaoProvider,
    FuyaoApiError,
    LimitUpLadderText,
)
from tradingagents.dataflows.social.prompt_formatter import format_social_sections
from tradingagents.dataflows.trade_calendar import (
    SNAPSHOT_ONLY_REFUSAL,
    cn_today_str,
    now_cn,
)
from tradingagents.dataflows.vendor_result import (
    VendorEmpty,
    VendorFail,
    VendorOk,
    VendorRefuse,
    result_to_prompt,
)
from tradingagents.graph.data_collector import (
    _DATA_FAILURE_SOURCE_ORDER,
    _build_market_attention,
)


# ── Fixtures & Helpers ──────────────────────────────────────────────────


def _make_date_list(base_date: str, count: int = 30) -> list[str]:
    """Generate YYYYMMDD string dates descending from base_date."""
    dt = datetime.strptime(base_date, "%Y-%m-%d")
    dates = []
    curr = dt
    while len(dates) < count:
        dates.append(curr.strftime("%Y%m%d"))
        curr -= timedelta(days=1)
    return dates


def _make_valid_ladder_payload(
    base_date: str = "2026-09-14",
    *,
    count: int = 30,
    timestamp: int = 1748102400000,
    with_stocks: bool = True,
    future_date: str | None = None,
) -> dict[str, Any]:
    date_list = _make_date_list(base_date, count)
    if future_date:
        date_list[0] = future_date.replace("-", "")

    items = []
    for idx, d_str in enumerate(date_list):
        boards: dict[str, list[dict[str, Any]]] = {k: [] for k in _LADDER_BOARD_KEYS}
        if with_stocks and idx == 0:
            boards["two_board"] = [
                {
                    "thscode": "603986.SH",
                    "ticker": "603986",
                    "name": "兆易创新",
                    "board_num": 2,
                    "seal_nextday": None,
                    "sign_level": 1,
                },
                {
                    "thscode": "000001.SZ",
                    "ticker": "000001",
                    "name": "平安银行",
                    "board_num": 2,
                    "seal_nextday": 1,
                    "sign_level": 2,
                },
            ]
            boards["three_board"] = [
                {
                    "thscode": "600519.SH",
                    "ticker": "600519",
                    "name": "贵州茅台",
                    "board_num": 3,
                    "seal_nextday": None,
                    "sign_level": 1,
                }
            ]
        items.append({"date": d_str, "boards": boards})

    return {
        "code": 0,
        "message": "success",
        "data": {
            "timestamp": timestamp,
            "window": {
                "length": count,
                "date_list": date_list,
                "board_caps": {k: 4 for k in _LADDER_BOARD_KEYS},
            },
            "item": items,
        },
    }


def _mock_json_response(body: dict[str, Any], status_code: int = 200) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = (
        __import__("json").dumps(body, ensure_ascii=False).encode("utf-8")
    )
    return resp


# ── 1. 合法完整 30 日 Fixture ───────────────────────────────────────────


def test_legal_full_30_day_fixture():
    provider = CnFuyaoProvider()
    today_str = cn_today_str()
    payload = _make_valid_ladder_payload(base_date=today_str, count=30, with_stocks=True)

    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", return_value=_mock_json_response(payload)):
        res = provider.get_limit_up_ladder(curr_date=today_str)

    assert isinstance(res, str)
    assert isinstance(res, LimitUpLadderText)
    assert res.timestamp == 1748102400000
    assert res.as_of == today_str
    assert res.curr_date == today_str
    assert res.source == "cn_fuyao"
    assert res.length == 30
    assert len(res.date_list) == 30
    assert len(res.item) == 30

    # Structured dict access
    assert res["timestamp"] == 1748102400000
    assert res["source"] == "cn_fuyao"
    assert len(res["item"]) == 30

    # seal_nextday=null preserved
    first_two_board = res.item[0]["boards"]["two_board"]
    assert first_two_board[0]["seal_nextday"] is None
    assert first_two_board[0]["thscode"] == "603986.SH"
    assert first_two_board[0]["board_num"] == 2
    assert first_two_board[0]["sign_level"] == 1
    assert first_two_board[1]["seal_nextday"] == 1

    # Text rendering checks
    assert "【数据日期】" in res
    assert "【请求日期】" in res
    assert "【数据来源】cn_fuyao" in res
    assert "兆易创新" in res
    assert "603986.SH" in res
    assert "次日封板:null" in res
    assert "市场关注度背景，非方向证据、非交易信号" in res


# ── 2. 六板块为空的正常 Fixture ──────────────────────────────────────────


def test_legal_fixture_all_boards_empty_is_available():
    provider = CnFuyaoProvider()
    today_str = cn_today_str()
    payload = _make_valid_ladder_payload(base_date=today_str, count=30, with_stocks=False)

    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", return_value=_mock_json_response(payload)):
        res = provider.get_limit_up_ladder(curr_date=today_str)

    assert isinstance(res, str)
    assert not isinstance(res, (VendorFail, VendorEmpty, VendorRefuse))
    assert res.length == 30
    assert "各梯队无连板标的" in res or "均无连板标的" in res
    assert "市场关注度背景，非方向证据、非交易信号" in res


# ── 3. 信封 / Window / Item 校验失败返回 VendorFail ──────────────────────


def test_envelope_validation_failures():
    provider = CnFuyaoProvider()
    today_str = cn_today_str()

    cases = [
        # Missing data dict
        {"code": 0, "message": "success", "data": None},
        # Missing timestamp
        {
            "code": 0,
            "message": "success",
            "data": {"window": {"length": 30, "date_list": []}, "item": []},
        },
        # Missing window
        {"code": 0, "message": "success", "data": {"timestamp": 123, "item": []}},
        # Window length not int
        {
            "code": 0,
            "message": "success",
            "data": {
                "timestamp": 123,
                "window": {"length": "30", "date_list": []},
                "item": [],
            },
        },
        # Window date_list not list
        {
            "code": 0,
            "message": "success",
            "data": {
                "timestamp": 123,
                "window": {"length": 30, "date_list": "invalid"},
                "item": [],
            },
        },
        # Length contradiction: length=30, date_list=29
        {
            "code": 0,
            "message": "success",
            "data": {
                "timestamp": 123,
                "window": {
                    "length": 30,
                    "date_list": _make_date_list(today_str, 29),
                },
                "item": [],
            },
        },
        # Illegal date in date_list
        {
            "code": 0,
            "message": "success",
            "data": {
                "timestamp": 123,
                "window": {
                    "length": 1,
                    "date_list": ["99999999"],
                },
                "item": [{"date": "99999999", "boards": {k: [] for k in _LADDER_BOARD_KEYS}}],
            },
        },
        # Item length contradiction
        {
            "code": 0,
            "message": "success",
            "data": {
                "timestamp": 123,
                "window": {
                    "length": 2,
                    "date_list": _make_date_list(today_str, 2),
                },
                "item": [{"date": today_str.replace("-", ""), "boards": {k: [] for k in _LADDER_BOARD_KEYS}}],
            },
        },
        # Boards missing key
        {
            "code": 0,
            "message": "success",
            "data": {
                "timestamp": 123,
                "window": {
                    "length": 1,
                    "date_list": [today_str.replace("-", "")],
                },
                "item": [
                    {
                        "date": today_str.replace("-", ""),
                        "boards": {"two_board": []},  # Missing other 5 board keys
                    }
                ],
            },
        },
        # Board key not a list
        {
            "code": 0,
            "message": "success",
            "data": {
                "timestamp": 123,
                "window": {
                    "length": 1,
                    "date_list": [today_str.replace("-", "")],
                },
                "item": [
                    {
                        "date": today_str.replace("-", ""),
                        "boards": {k: None for k in _LADDER_BOARD_KEYS},
                    }
                ],
            },
        },
    ]

    for bad_payload in cases:
        with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
             patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", return_value=_mock_json_response(bad_payload)):
            res = provider.get_limit_up_ladder(curr_date=today_str)
            assert isinstance(res, VendorFail), f"Expected VendorFail for {bad_payload}, got {type(res)}: {res}"
            assert res.error.startswith("[cn_fuyao]")


# ── 4. 错误语义（HTTP 4xx/5xx、4001、无效Key、无数据）与不切换 AkShare ────


def test_http_error_returns_vendor_fail():
    provider = CnFuyaoProvider()
    today_str = cn_today_str()
    mock_resp = requests.Response()
    mock_resp.status_code = 500
    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", side_effect=requests.HTTPError("500 Server Error", response=mock_resp)):
        res = provider.get_limit_up_ladder(curr_date=today_str)
    assert isinstance(res, VendorFail)
    assert "HTTP 请求失败" in res.error


def test_rate_limit_4001_returns_vendor_fail():
    provider = CnFuyaoProvider()
    today_str = cn_today_str()
    payload = {"code": 4001, "message": "rate limit exceeded", "data": None}
    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch.object(provider, "_RATE_LIMIT_BACKOFF_SECONDS", 0.001), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", return_value=_mock_json_response(payload)):
        res = provider.get_limit_up_ladder(curr_date=today_str)
    assert isinstance(res, VendorFail)
    assert "频率超限" in res.error


def test_invalid_key_raises_not_implemented_error():
    provider = CnFuyaoProvider()
    today_str = cn_today_str()
    payload = {"code": 2001, "message": "invalid api key", "data": None}
    with patch.object(provider, "_resolve_api_key", return_value="bad_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", return_value=_mock_json_response(payload)):
        with pytest.raises(NotImplementedError, match="API Key"):
            provider.get_limit_up_ladder(curr_date=today_str)


def test_no_data_3001_returns_vendor_empty():
    provider = CnFuyaoProvider()
    today_str = cn_today_str()
    payload = {"code": 3001, "message": "no data", "data": None}
    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", return_value=_mock_json_response(payload)):
        res = provider.get_limit_up_ladder(curr_date=today_str)
    assert isinstance(res, VendorEmpty)
    assert "code=3001" in res.message


def test_route_never_switches_to_akshare():
    """Assert router vendor chain for get_limit_up_ladder contains only cn_fuyao and never calls AkShare."""
    chain = iface._resolve_vendor_chain("get_limit_up_ladder", "cn_akshare,cn_fuyao")
    assert chain == ["cn_fuyao"]

    akshare_mock = MagicMock()
    akshare_mock.name = "cn_akshare"
    akshare_mock.is_placeholder = False
    akshare_mock.get_limit_up_ladder = MagicMock(side_effect=AssertionError("AkShare must never be called"))

    fuyao_mock = MagicMock()
    fuyao_mock.name = "cn_fuyao"
    fuyao_mock.is_placeholder = False
    fuyao_mock.get_limit_up_ladder = MagicMock(return_value=VendorFail("Fuyao failed"))

    class _CustomRegistry:
        def list_names(self):
            return ["cn_akshare", "cn_fuyao"]

        def get(self, name):
            if name == "cn_akshare":
                return akshare_mock
            if name == "cn_fuyao":
                return fuyao_mock
            return None

        def resource_policy(self, name):
            return iface.DEFAULT_PROVIDER_RESOURCE_POLICY

    with patch.object(iface, "_registry", _CustomRegistry()), \
         patch.object(iface, "get_vendor", return_value="cn_fuyao"):
        with pytest.raises(RuntimeError, match="No available vendor"):
            iface.route_to_vendor("get_limit_up_ladder", curr_date=cn_today_str())

    assert akshare_mock.get_limit_up_ladder.call_count == 0


# ── 5. 历史分析日期在网络调用前拒绝（断言请求次数为 0）────────────────────


def test_historical_date_refused_before_network_call():
    provider = CnFuyaoProvider()
    past_date = (now_cn().date() - timedelta(days=90)).strftime("%Y-%m-%d")

    mock_req = MagicMock(side_effect=AssertionError("Network must not be called for historical date"))
    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", mock_req):
        res = provider.get_limit_up_ladder(curr_date=past_date)

    assert mock_req.call_count == 0
    assert isinstance(res, VendorRefuse)
    assert SNAPSHOT_ONLY_REFUSAL in res.reason
    assert res.reason.startswith("【数据获取失败】")


def test_missing_or_invalid_curr_date_refused_before_network_call():
    provider = CnFuyaoProvider()
    mock_req = MagicMock(side_effect=AssertionError("Network must not be called"))

    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", mock_req):
        res_none = provider.get_limit_up_ladder(curr_date=None)
        res_empty = provider.get_limit_up_ladder(curr_date="")
        res_invalid = provider.get_limit_up_ladder(curr_date="not-a-date")

    assert mock_req.call_count == 0
    assert "【数据获取失败】" in str(res_none)
    assert "【数据获取失败】" in str(res_empty)
    assert "【数据获取失败】" in str(res_invalid)


# ── 6. 返回窗口含未来日期时拒绝（不裁剪、不回退）─────────────────────────


def test_future_date_in_window_refused_without_cropping():
    provider = CnFuyaoProvider()
    today_str = cn_today_str()
    tomorrow_str = (now_cn().date() + timedelta(days=1)).strftime("%Y-%m-%d")
    payload = _make_valid_ladder_payload(base_date=today_str, count=30, future_date=tomorrow_str)

    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", return_value=_mock_json_response(payload)):
        res = provider.get_limit_up_ladder(curr_date=today_str)

    assert isinstance(res, VendorFail)
    assert "晚于请求基准日期" in res.error
    assert "拒绝未来数据" in res.error


# ── 7. seal_nextday=null 保真 ──────────────────────────────────────────


def test_seal_nextday_null_fidelity():
    provider = CnFuyaoProvider()
    today_str = cn_today_str()
    payload = _make_valid_ladder_payload(base_date=today_str, count=30, with_stocks=True)

    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", return_value=_mock_json_response(payload)):
        res = provider.get_limit_up_ladder(curr_date=today_str)

    stocks = res.item[0]["boards"]["two_board"]
    assert stocks[0]["seal_nextday"] is None
    assert stocks[1]["seal_nextday"] == 1
    # Plain text representation shows null
    assert "次日封板:null" in res


# ── 8. 既有 get_zt_pool 回归保持通过 ───────────────────────────────────


def test_get_zt_pool_regression_preserved():
    provider = CnFuyaoProvider()
    # Missing date refuses
    out = provider.get_zt_pool(None)
    assert "【数据获取失败】" in out
    assert "缺少 date/curr_date" in out

    # Tool vendor configuration for get_zt_pool contains AkShare and Fuyao
    assert DEFAULT_CONFIG["tool_vendors"]["get_zt_pool"] == "cn_akshare,cn_fuyao"
    assert DEFAULT_CONFIG["tool_vendors"]["get_limit_up_ladder"] == "cn_fuyao"


# ── 9. DataCollector / PromptFormatter / 无方向信号 ─────────────────────


def test_market_attention_contains_limit_up_ladder():
    results = {
        "zt_pool": "涨停池（2026-09-14，同花顺）：共 10 只",
        "limit_up_ladder": LimitUpLadderText(
            "【数据日期】2026-09-14\n【数据来源】cn_fuyao\n连板天梯",
            as_of="2026-09-14",
            curr_date="2026-09-14",
            length=30,
        ),
        "hot_stocks": "雪球热搜",
    }
    source_provenance = {
        "zt_pool": {"status": "available", "as_of": "2026-09-14"},
        "limit_up_ladder": {"status": "available", "as_of": "2026-09-14"},
        "hot_stocks": {"status": "available", "as_of": "2026-09-14"},
    }

    attention = _build_market_attention(results, source_provenance, "2026-09-14")
    assert "zt_pool" in attention
    assert "limit_up_ladder" in attention
    assert "hot_stocks" in attention

    ladder = attention["limit_up_ladder"]
    assert ladder["status"] == "available"
    assert ladder["as_of"] == "2026-09-14"
    assert "连板天梯" in str(ladder["raw"])


def test_prompt_formatter_renders_independent_ladder_column():
    market_attention = {
        "zt_pool": {"status": "available", "as_of": "2026-09-14", "raw": "涨停池文本"},
        "limit_up_ladder": {
            "status": "available",
            "as_of": "2026-09-14",
            "raw": "连板天梯矩阵内容",
        },
        "hot_stocks": {"status": "available", "as_of": "2026-09-14", "raw": "热门股票"},
    }

    prompt = format_social_sections(
        bundle={"status": "not_applicable", "direction_allowed": False},
        market_attention=market_attention,
        ticker_display="603986 (兆易创新)",
        current_date="2026-09-14",
        direction_allowed=False,
    )

    # Both zt_pool and limit_up_ladder columns exist independently
    assert "【涨停池数据】" in prompt
    assert "【连板天梯数据】" in prompt
    assert "来源: cn_fuyao" in prompt
    assert "时效: 2026-09-14" in prompt
    assert "连板天梯矩阵内容" in prompt
    assert "市场关注度背景，非方向证据、非交易信号" in prompt

    # No trade signals generated
    assert "BUY" not in prompt
    assert "SELL" not in prompt


def test_prompt_formatter_unavailable_ladder_shows_status_and_reason():
    market_attention = {
        "zt_pool": {"status": "available", "as_of": "2026-09-14", "raw": "涨停池文本"},
        "limit_up_ladder": {
            "status": "refused",
            "gap": "【数据获取失败】连板天梯：快照拒绝",
        },
        "hot_stocks": {"status": "available", "as_of": "2026-09-14", "raw": "热门股票"},
    }

    prompt = format_social_sections(
        bundle={"status": "not_applicable", "direction_allowed": False},
        market_attention=market_attention,
        ticker_display="603986 (兆易创新)",
        current_date="2026-09-14",
        direction_allowed=False,
    )

    assert "【连板天梯数据】(状态: refused | 来源: cn_fuyao)" in prompt
    assert "【不可用原因】" in prompt
    assert "市场关注度背景，非方向证据、非交易信号" in prompt


# ── 10. Tools 包装与调用接口 ──────────────────────────────────────────


def test_tool_invocations():
    today_str = cn_today_str()
    payload = _make_valid_ladder_payload(base_date=today_str, count=30, with_stocks=True)
    provider = CnFuyaoProvider()

    with patch.object(provider, "_resolve_api_key", return_value="test_key"), \
         patch("tradingagents.dataflows.providers.cn_fuyao_provider.requests.get", return_value=_mock_json_response(payload)):
        out = provider.get_limit_up_ladder(today_str)
        assert isinstance(out, LimitUpLadderText)
        assert out.as_of == today_str
        assert out.timestamp == 1748102400000

    # Ensure get_limit_up_ladder tool has the correct docstring and name
    assert get_limit_up_ladder.name == "get_limit_up_ladder"
    assert "连板天梯" in get_limit_up_ladder.description
    assert "非方向证据、非交易信号" in get_limit_up_ladder.description

    # Ensure source order contains limit_up_ladder
    assert "limit_up_ladder" in _DATA_FAILURE_SOURCE_ORDER
