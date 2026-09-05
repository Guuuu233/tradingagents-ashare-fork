"""Unit tests for Tushare Pro daily_basic reader interface (C-09-2 / DAV-638).

Covers:
- Normal single-row fetching and column access by name (no iloc).
- Token missing handling without network traffic.
- Empty table / no rows handling (suspension, non-trading days).
- Missing required fields reporting (circ_mv, free_share, amount, etc.).
- PIT date boundary guard (trade_date > as_of rejected before network call).
- Typed error classifications: permission_denied, rate_limited, json_shape, transport.
- URL routing via TUSHARE_API_URL / TUSHARE_BASE_URL with official default fallback.
"""

from unittest.mock import patch

import pytest
import requests

from tradingagents.dataflows.providers.cn_akshare_provider import (
    _TUSHARE_DAILY_BASIC_API,
    _TUSHARE_DAILY_BASIC_REQUIRED_FIELDS,
    CnAkshareProvider,
)


class _MockResponse:
    def __init__(self, data: dict, status_code: int = 200, text: str = ""):
        self._data = data
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def _make_daily_basic_payload(
    *,
    code: int = 0,
    msg: str = "",
    ts_code: str = "600519.SH",
    trade_date: str = "20260814",
    close: float = 1800.0,
    turnover_rate: float = 0.52,
    turnover_rate_f: float = 0.85,
    volume_ratio: float = 1.12,
    free_share: float = 125619.78,
    circ_mv: float = 2261156.04,
    total_mv: float = 2261156.04,
    amount: float = 521400.12,
    fields: list[str] | None = None,
    empty_items: bool = False,
    data: dict | None = None,
):
    if data is not None:
        return {"code": code, "msg": msg, "data": data}

    if fields is None:
        fields = list(_TUSHARE_DAILY_BASIC_REQUIRED_FIELDS)

    field_val_map = {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "close": close,
        "turnover_rate": turnover_rate,
        "turnover_rate_f": turnover_rate_f,
        "volume_ratio": volume_ratio,
        "free_share": free_share,
        "circ_mv": circ_mv,
        "total_mv": total_mv,
        "amount": amount,
    }

    if empty_items:
        items = []
    else:
        items = [[field_val_map.get(f, 0.0) for f in fields]]

    return {
        "code": code,
        "msg": msg,
        "data": {
            "fields": fields,
            "items": items,
        },
    }


def test_daily_basic_success_row(monkeypatch):
    """1. 正常一行：验证返回字典按列名取数，包含所有必需列，无 iloc，无 token 泄露。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_token_12345")
    provider = CnAkshareProvider()

    payload = _make_daily_basic_payload(
        ts_code="600519.SH",
        trade_date="20260814",
        close=1850.5,
        turnover_rate=0.65,
        turnover_rate_f=0.92,
        volume_ratio=1.15,
        free_share=120000.0,
        circ_mv=2220600.0,
        total_mv=2220600.0,
        amount=550000.0,
    )

    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14", as_of="2026-08-14"
        )

    assert error is None
    assert category is None
    assert row is not None
    assert isinstance(row, dict)

    # 验证按名字取数
    assert row["ts_code"] == "600519.SH"
    assert row["trade_date"] == "20260814"
    assert row["close"] == 1850.5
    assert row["turnover_rate"] == 0.65
    assert row["turnover_rate_f"] == 0.92
    assert row["volume_ratio"] == 1.15
    assert row["free_share"] == 120000.0
    assert row["circ_mv"] == 2220600.0
    assert row["total_mv"] == 2220600.0
    assert row["amount"] == 550000.0

    # 验证向网关传递的参数规范
    assert mock_post.call_count == 1
    call_json = mock_post.call_args[1]["json"]
    assert call_json["api_name"] == _TUSHARE_DAILY_BASIC_API
    assert call_json["params"] == {"ts_code": "600519.SH", "trade_date": "20260814"}
    assert call_json["token"] == "mock_token_12345"
    for col in _TUSHARE_DAILY_BASIC_REQUIRED_FIELDS:
        assert col in call_json["fields"]


def test_daily_basic_token_missing(monkeypatch):
    """2. Token 缺失：未配置 TUSHARE_TOKEN 时直接返回 token_missing，禁止外发网络请求。"""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )

    assert row is None
    assert category == "token_missing"
    assert error == "tushare.daily_basic:token_missing"
    assert mock_post.call_count == 0


def test_daily_basic_empty_table_no_rows(monkeypatch):
    """3. 空表：网关返回 items=[] 或无匹配交易日，返回类型化 no_rows 错误，严禁回填。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_token_12345")
    provider = CnAkshareProvider()

    # Case 3.1: 接口返回成功但 items 为空（停牌或非交易日）
    empty_payload = _make_daily_basic_payload(empty_items=True)
    with patch("requests.post", return_value=_MockResponse(empty_payload)):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )

    assert row is None
    assert category == "no_rows"
    assert error == "tushare.daily_basic:no_rows"

    # Case 3.2: data 为 None
    none_data_payload = {"code": 0, "msg": "", "data": None}
    with patch("requests.post", return_value=_MockResponse(none_data_payload)):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )

    assert row is None
    assert category == "no_rows"
    assert error == "tushare.daily_basic:no_rows"

    # Case 3.3: items 中日期不匹配请求日期
    mismatch_payload = _make_daily_basic_payload(trade_date="20260813")
    with patch("requests.post", return_value=_MockResponse(mismatch_payload)):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )

    assert row is None
    assert category == "no_rows"
    assert error == "tushare.daily_basic:no_rows"


def test_daily_basic_missing_columns(monkeypatch):
    """4. 缺列：当 fields 缺少任一必需字段时上报 missing_field 类型化错误。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_token_12345")
    provider = CnAkshareProvider()

    # 缺少 circ_mv
    fields_without_circ_mv = [
        f for f in _TUSHARE_DAILY_BASIC_REQUIRED_FIELDS if f != "circ_mv"
    ]
    payload1 = _make_daily_basic_payload(fields=fields_without_circ_mv)
    with patch("requests.post", return_value=_MockResponse(payload1)):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )

    assert row is None
    assert category == "missing_field"
    assert error == "tushare.daily_basic:missing_field(circ_mv)"

    # 缺少 free_share
    fields_without_free_share = [
        f for f in _TUSHARE_DAILY_BASIC_REQUIRED_FIELDS if f != "free_share"
    ]
    payload2 = _make_daily_basic_payload(fields=fields_without_free_share)
    with patch("requests.post", return_value=_MockResponse(payload2)):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )

    assert row is None
    assert category == "missing_field"
    assert error == "tushare.daily_basic:missing_field(free_share)"

    # 缺少 amount
    fields_without_amount = [
        f for f in _TUSHARE_DAILY_BASIC_REQUIRED_FIELDS if f != "amount"
    ]
    payload3 = _make_daily_basic_payload(fields=fields_without_amount)
    with patch("requests.post", return_value=_MockResponse(payload3)):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )

    assert row is None
    assert category == "missing_field"
    assert error == "tushare.daily_basic:missing_field(amount)"


def test_daily_basic_pit_date_exceeds_as_of(monkeypatch):
    """5. 日期越界：trade_date > as_of 必须严格拒绝，不得发出网关请求。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_token_12345")
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        # trade_date (2026-08-15) > as_of (2026-08-14)
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-15", as_of="2026-08-14"
        )

    assert row is None
    assert category == "date_exceeds_as_of"
    assert "date_exceeds_as_of" in str(error)
    assert "2026-08-15>2026-08-14" in str(error)
    assert mock_post.call_count == 0

    # 跨格式日期校验 (YYYYMMDD 与 YYYY-MM-DD 混合)
    with patch("requests.post") as mock_post2:
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "20260815", as_of="2026-08-14"
        )

    assert row is None
    assert category == "date_exceeds_as_of"
    assert mock_post2.call_count == 0


def test_daily_basic_no_iloc_column_reordering(monkeypatch):
    """验证按名字取数：返回字段次序颠倒打乱时，依然能够按列名准确取数，不依赖 iloc 索引。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_token_12345")
    provider = CnAkshareProvider()

    # 打乱列顺序
    scrambled_fields = [
        "amount",
        "close",
        "trade_date",
        "ts_code",
        "circ_mv",
        "total_mv",
        "volume_ratio",
        "free_share",
        "turnover_rate_f",
        "turnover_rate",
    ]
    payload = _make_daily_basic_payload(
        fields=scrambled_fields,
        ts_code="600519.SH",
        trade_date="20260814",
        close=1900.0,
        amount=888888.8,
        circ_mv=2300000.0,
    )

    with patch("requests.post", return_value=_MockResponse(payload)):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )

    assert error is None
    assert category is None
    assert row is not None
    # 验证各字段无论顺序如何，均由列名正确取回
    assert row["close"] == 1900.0
    assert row["amount"] == 888888.8
    assert row["circ_mv"] == 2300000.0
    assert row["ts_code"] == "600519.SH"
    assert row["trade_date"] == "20260814"


@pytest.mark.parametrize(
    "status_code, resp_payload, expected_cat",
    [
        (200, {"code": 2002, "msg": "权限不足", "data": None}, "permission_denied"),
        (200, {"code": 40101, "msg": "未授权凭证", "data": None}, "permission_denied"),
        (403, {"code": 0, "data": None}, "permission_denied"),
        (200, {"code": 40203, "msg": "访问频次超限", "data": None}, "rate_limited"),
        (429, {"code": 0, "data": None}, "rate_limited"),
        (200, {"code": 99999, "msg": "其它服务端未知错误", "data": None}, "api_code"),
        (200, {"code": "invalid"}, "api_code_invalid"),
        (200, {"code": 0}, "json_shape"),
        (200, {"code": 0, "data": "not-a-dict"}, "json_shape"),
        (200, {"code": 0, "data": {"fields": "not-a-list", "items": []}}, "json_shape"),
    ],
)
def test_daily_basic_error_classifications(
    monkeypatch, status_code, resp_payload, expected_cat
):
    """验证错误码精细化分类：permission_denied / rate_limited / json_shape 等。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_token_12345")
    provider = CnAkshareProvider()

    with patch("requests.post", return_value=_MockResponse(resp_payload, status_code=status_code)):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )

    assert row is None
    assert category == expected_cat
    assert f"tushare.daily_basic:{expected_cat}" in error


def test_daily_basic_transport_timeout_and_error(monkeypatch):
    """验证传输超时与网络错误处理。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_token_12345")
    provider = CnAkshareProvider()

    with patch("requests.post", side_effect=requests.Timeout("gateway timeout")):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )
    assert row is None
    assert category == "transport_timeout"
    assert error == "tushare.daily_basic:transport_timeout"

    with patch("requests.post", side_effect=requests.ConnectionError("connection failed")):
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14"
        )
    assert row is None
    assert category == "transport_error"
    assert error == "tushare.daily_basic:transport_error"


def test_daily_basic_url_environment_cascade(monkeypatch):
    """验证网关环境变量路由：TUSHARE_API_URL > TUSHARE_BASE_URL > 官方兜底。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_token_12345")
    provider = CnAkshareProvider()
    payload = _make_daily_basic_payload()

    # 1. TUSHARE_API_URL 优先
    monkeypatch.setenv("TUSHARE_API_URL", "http://gateway.internal:9000/v1")
    monkeypatch.setenv("TUSHARE_BASE_URL", "http://backup.internal:9000/v1")
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_daily_basic("600519", "2026-08-14")
    assert mock_post.call_args[0][0] == "http://gateway.internal:9000/v1"

    # 2. TUSHARE_BASE_URL 备选
    monkeypatch.delenv("TUSHARE_API_URL", raising=False)
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_daily_basic("600519", "2026-08-14")
    assert mock_post.call_args[0][0] == "http://backup.internal:9000/v1"

    # 3. 环境变量均未配置时，默认回落至官方端点 https://api.tushare.pro
    monkeypatch.delenv("TUSHARE_BASE_URL", raising=False)
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_daily_basic("600519", "2026-08-14")
    assert mock_post.call_args[0][0] == "https://api.tushare.pro"


def test_daily_basic_input_validation(monkeypatch):
    """验证非法股票代码和非法日期输入。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_token_12345")
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        # 非法 symbol
        row, error, category = provider._fetch_tushare_daily_basic(
            "bad_symbol", "2026-08-14"
        )
        assert row is None
        assert category == "validation"
        assert error == "tushare.daily_basic:validation(symbol)"

        # 非法 trade_date
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "not-a-date"
        )
        assert row is None
        assert category == "validation"
        assert error == "tushare.daily_basic:validation(trade_date)"

        # 非法 as_of
        row, error, category = provider._fetch_tushare_daily_basic(
            "600519", "2026-08-14", as_of="invalid_as_of"
        )
        assert row is None
        assert category == "validation"
        assert error == "tushare.daily_basic:validation(as_of)"

    assert mock_post.call_count == 0
