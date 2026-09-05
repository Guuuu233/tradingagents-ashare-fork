"""Unit tests for Tushare Pro forecast collateral reader interface (C-05 Slice 2 / DAV-640).

Covers all contracts:
1. PIT boundary guard: rows with ann_date > as_of are discarded; strictly forbids
   using end_date for truncation.
2. Column access by name (no iloc); missing ann_date in schema -> schema_drift/missing_field.
3. 403 / token_missing -> provider_failure; 0 rows -> collateral_empty (asserts text
   does not contain "确认无公告").
4. Returned records must have canonical_event_id is None (strictly forbids inventing cninfo ID).
5. All tests mock HTTP: success row, date-exceeds-as_of discarded, empty table,
   missing column, token missing, 403 permission denied. Strictly forbids real gateway traffic.
"""

from unittest.mock import patch

import pytest
import requests

from tradingagents.dataflows.providers.cn_akshare_provider import (
    _TUSHARE_FORECAST_API,
    _TUSHARE_FORECAST_REQUIRED_FIELDS,
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


def _make_forecast_payload(
    *,
    code: int = 0,
    msg: str = "",
    fields: list[str] | None = None,
    rows: list[dict] | None = None,
    empty_items: bool = False,
    data: dict | None = None,
):
    if data is not None:
        return {"code": code, "msg": msg, "data": data}

    if fields is None:
        fields = list(_TUSHARE_FORECAST_REQUIRED_FIELDS)

    if empty_items:
        items = []
    elif rows is not None:
        items = [[r.get(f, None) for f in fields] for r in rows]
    else:
        default_row = {
            "ts_code": "600519.SH",
            "ann_date": "20250120",
            "end_date": "20241231",
            "type": "预增",
            "p_change_min": 15.0,
            "p_change_max": 20.0,
            "net_profit_min": 8500000.0,
            "net_profit_max": 8800000.0,
            "last_parent_net": 7473400.0,
            "first_ann_date": "20250120",
            "summary": "预计2024年年度净利润约850亿元至880亿元，同比增长约15%至20%",
            "change_reason": "公司生产经营情况良好，产品销量稳步增长",
        }
        items = [[default_row.get(f, None) for f in fields]]

    return {
        "code": code,
        "msg": msg,
        "data": {
            "fields": fields,
            "items": items,
        },
    }


def test_forecast_success_row(monkeypatch):
    """1. 正常一行：验证返回结构、按列名取数、canonical_event_id 恒为 None、无 token 泄露。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_forecast_token_abc")
    provider = CnAkshareProvider()

    row_data = {
        "ts_code": "600519.SH",
        "ann_date": "20250120",
        "end_date": "20241231",
        "type": "预增",
        "p_change_min": 15.5,
        "p_change_max": 18.2,
        "net_profit_min": 8600000.0,
        "net_profit_max": 8800000.0,
        "last_parent_net": 7473400.0,
        "first_ann_date": "20250120",
        "summary": "预计2024年度净利润同比增长15.5%~18.2%",
        "change_reason": "主营业务持续向好",
    }
    payload = _make_forecast_payload(rows=[row_data])

    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2025-01-25"
        )

    assert error is None
    assert category is None
    assert isinstance(records, list)
    assert len(records) == 1

    rec = records[0]
    # 契约 4：canonical_event_id 必须为 None，严禁伪造 cninfo ID
    assert rec["canonical_event_id"] is None

    # 契约 2：按列名取数
    assert rec["symbol"] == "600519"
    assert rec["ts_code"] == "600519.SH"
    assert rec["ann_date"] == "2025-01-20"
    assert rec["end_date"] == "20241231"
    assert rec["type"] == "预增"
    assert rec["p_change_min"] == 15.5
    assert rec["p_change_max"] == 18.2
    assert rec["net_profit_min"] == 8600000.0
    assert rec["net_profit_max"] == 8800000.0
    assert rec["last_parent_net"] == 7473400.0
    assert rec["first_ann_date"] == "20250120"
    assert "净利润" in rec["summary"]
    assert rec["source_type"] == "tushare_forecast"
    assert rec["collateral_id"] == "tushare:forecast:600519.SH:2025-01-20:20241231"

    # payload 内也同样结构化携带指标
    assert rec["payload"]["type"] == "预增"
    assert rec["payload"]["p_change_min"] == 15.5
    assert rec["payload"]["net_profit_min"] == 8600000.0

    # 验证网关调用参数
    assert mock_post.call_count == 1
    call_json = mock_post.call_args[1]["json"]
    assert call_json["api_name"] == _TUSHARE_FORECAST_API
    assert call_json["params"]["ts_code"] == "600519.SH"
    assert call_json["token"] == "mock_forecast_token_abc"
    for field in _TUSHARE_FORECAST_REQUIRED_FIELDS:
        assert field in call_json["fields"]


def test_forecast_pit_ann_date_exceeds_as_of_dropped(monkeypatch):
    """2. PIT 契约：ann_date > as_of 的行丢弃；不得用 end_date 截断。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_forecast_token_abc")
    provider = CnAkshareProvider()

    row_prior = {
        "ts_code": "600519.SH",
        "ann_date": "20250115",
        "end_date": "20241231",
        "type": "略增",
        "p_change_min": 10.0,
        "p_change_max": 12.0,
        "net_profit_min": 8200000.0,
        "net_profit_max": 8400000.0,
        "last_parent_net": 7473400.0,
        "first_ann_date": "20250115",
        "summary": "首次预告",
        "change_reason": "稳健增长",
    }
    row_future = {
        "ts_code": "600519.SH",
        "ann_date": "20250210",
        "end_date": "20241231",
        "type": "预增",
        "p_change_min": 18.0,
        "p_change_max": 22.0,
        "net_profit_min": 8800000.0,
        "net_profit_max": 9100000.0,
        "last_parent_net": 7473400.0,
        "first_ann_date": "20250115",
        "summary": "业绩预告修正",
        "change_reason": "春节消费旺季超预期",
    }
    payload = _make_forecast_payload(rows=[row_prior, row_future])

    # Case 2.1: as_of = "2025-01-20" 时，row_future (ann_date="20250210") 必须被丢弃，只保留 row_prior
    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2025-01-20"
        )

    assert error is None
    assert category is None
    assert len(records) == 1
    assert records[0]["ann_date"] == "2025-01-15"
    assert records[0]["type"] == "略增"
    assert records[0]["p_change_min"] == 10.0

    # Case 2.2: 铁律检验——严禁用 end_date 截断！
    # 当 as_of = "2024-12-31" 时，两行的 end_date 均为 "20241231"。
    # 若误用 end_date <= as_of 截断，两行都会被错误保留造成未来信息穿越。
    # 严格 PIT 下，两行 ann_date 均晚于 2024-12-31，因此全部被丢弃，返回 collateral_empty。
    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2024-12-31"
        )

    assert records == []
    assert category == "collateral_empty"
    assert "确认无公告" not in str(error)
    assert "确认无公告" not in str(category)


def test_forecast_empty_table_collateral_empty(monkeypatch):
    """3. 空表契约：0 行 -> collateral_empty（测试断言文本不含「确认无公告」）。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_forecast_token_abc")
    provider = CnAkshareProvider()

    # Case 3.1: items 为空列表 []
    empty_payload = _make_forecast_payload(empty_items=True)
    with patch("requests.post", return_value=_MockResponse(empty_payload)):
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "collateral_empty"
    assert error == "tushare.forecast:collateral_empty"
    # 契约 3 显式断言：文本绝对不包含「确认无公告」
    assert "确认无公告" not in str(error)
    assert "确认无公告" not in str(category)

    # Case 3.2: data 为 None
    none_data_payload = {"code": 0, "msg": "", "data": None}
    with patch("requests.post", return_value=_MockResponse(none_data_payload)):
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "collateral_empty"
    assert "确认无公告" not in str(error)
    assert "确认无公告" not in str(category)


def test_forecast_missing_ann_date_schema_drift(monkeypatch):
    """4. 缺列契约：缺 ann_date -> schema_drift/missing_field。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_forecast_token_abc")
    provider = CnAkshareProvider()

    # 构造缺失 ann_date 的 fields
    fields_without_ann_date = [
        f for f in _TUSHARE_FORECAST_REQUIRED_FIELDS if f != "ann_date"
    ]
    payload = _make_forecast_payload(fields=fields_without_ann_date)

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category in ("schema_drift", "missing_field")
    assert "schema_drift" in str(error) or "missing_field" in str(error)
    assert "ann_date" in str(error)


def test_forecast_token_missing_provider_failure(monkeypatch):
    """5. Token 缺失契约：token_missing -> provider_failure，禁止发起网络请求。"""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert error == "tushare.forecast:provider_failure(token_missing)"
    # 严格禁止发起网络调用
    assert mock_post.call_count == 0


def test_forecast_403_forbidden_provider_failure(monkeypatch):
    """6. 403 契约：403 / permission_denied -> provider_failure，严禁泄露 Token。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "secret_token_never_leak_999")
    provider = CnAkshareProvider()

    # Case 6.1: HTTP 403
    with patch(
        "requests.post",
        return_value=_MockResponse({"code": 0, "data": None}, status_code=403),
    ):
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert "provider_failure" in str(error)
    assert "secret_token_never_leak_999" not in str(error)

    # Case 6.2: 业务返回码 2002 权限不足
    perm_payload = {"code": 2002, "msg": "抱歉，您没有权限访问 forecast 接口", "data": None}
    with patch(
        "requests.post",
        return_value=_MockResponse(perm_payload, status_code=200),
    ):
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert "provider_failure" in str(error)
    assert "secret_token_never_leak_999" not in str(error)


def test_forecast_no_iloc_column_reordering(monkeypatch):
    """验证按列名取数：返回字段次序颠倒打乱时，依然能够按列名准确取数，不依赖 iloc 索引。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_forecast_token_abc")
    provider = CnAkshareProvider()

    scrambled_fields = [
        "summary",
        "net_profit_max",
        "type",
        "ts_code",
        "ann_date",
        "change_reason",
        "end_date",
        "p_change_min",
        "last_parent_net",
        "p_change_max",
        "net_profit_min",
        "first_ann_date",
    ]
    row_data = {
        "ts_code": "600519.SH",
        "ann_date": "20250120",
        "end_date": "20241231",
        "type": "预增",
        "p_change_min": 16.0,
        "p_change_max": 19.0,
        "net_profit_min": 8700000.0,
        "net_profit_max": 8900000.0,
        "last_parent_net": 7473400.0,
        "first_ann_date": "20250120",
        "summary": "打乱顺序按列名取数验证",
        "change_reason": "顺序鲁棒性良好",
    }
    payload = _make_forecast_payload(fields=scrambled_fields, rows=[row_data])

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="2025-01-25"
        )

    assert error is None
    assert category is None
    assert len(records) == 1
    rec = records[0]
    assert rec["type"] == "预增"
    assert rec["p_change_min"] == 16.0
    assert rec["net_profit_max"] == 8900000.0
    assert rec["ann_date"] == "2025-01-20"
    assert rec["summary"] == "打乱顺序按列名取数验证"
    assert rec["canonical_event_id"] is None


def test_forecast_input_validation(monkeypatch):
    """验证非法输入参数校验（symbol / as_of），验证不发出任何网关请求。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_forecast_token_abc")
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        # 非法 symbol
        records, error, category = provider._fetch_tushare_forecast(
            "invalid_symbol_999", as_of="2025-01-20"
        )
        assert records == []
        assert category == "validation"
        assert error == "tushare.forecast:validation(symbol)"

        # 非法 as_of
        records, error, category = provider._fetch_tushare_forecast(
            "600519", as_of="invalid_as_of_date"
        )
        assert records == []
        assert category == "validation"
        assert error == "tushare.forecast:validation(as_of)"

    assert mock_post.call_count == 0


def test_forecast_url_environment_cascade(monkeypatch):
    """验证网关环境变量路由：TUSHARE_API_URL > TUSHARE_BASE_URL > 官方兜底。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_forecast_token_abc")
    provider = CnAkshareProvider()
    payload = _make_forecast_payload()

    # 1. TUSHARE_API_URL 优先
    monkeypatch.setenv("TUSHARE_API_URL", "http://gateway.collateral:8080/v1")
    monkeypatch.setenv("TUSHARE_BASE_URL", "http://backup.collateral:8080/v1")
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_forecast("600519", as_of="2025-01-25")
    assert mock_post.call_args[0][0] == "http://gateway.collateral:8080/v1"

    # 2. TUSHARE_BASE_URL 备选
    monkeypatch.delenv("TUSHARE_API_URL", raising=False)
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_forecast("600519", as_of="2025-01-25")
    assert mock_post.call_args[0][0] == "http://backup.collateral:8080/v1"

    # 3. 环境变量均未配置时，默认回落至官方端点 https://api.tushare.pro
    monkeypatch.delenv("TUSHARE_BASE_URL", raising=False)
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_forecast("600519", as_of="2025-01-25")
    assert mock_post.call_args[0][0] == "https://api.tushare.pro"
