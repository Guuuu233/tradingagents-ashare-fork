"""Unit tests for Tushare Pro repurchase collateral reader interface (C-05 Slice 3 / DAV-642).

Covers all contracts:
1. PIT boundary guard: rows with ann_date > as_of are discarded; strictly forbids
   using end_date or exp_date for truncation.
2. Column access by name (no iloc); missing ann_date in schema -> schema_drift/missing_field.
3. 403 / token_missing -> provider_failure; 0 rows -> collateral_empty (asserts text
   does not contain "确认无公告").
4. Returned records must have canonical_event_id is None (strictly forbids inventing cninfo ID).
5. Fields extracted by column name: ts_code, ann_date, end_date, proc, exp_date, vol, amount, high_limit, low_limit.
6. All tests mock HTTP: success row, date-exceeds-as_of discarded, empty table,
   missing column, token missing, 403 permission denied. Strictly forbids real gateway traffic.
7. Must cover: proc='完成' with ann_date > as_of cumulative amount row is discarded
   (prevents completion-state lookahead bias).
"""

from unittest.mock import patch

import pytest
import requests

from tradingagents.dataflows.providers.cn_akshare_provider import (
    _TUSHARE_REPURCHASE_API,
    _TUSHARE_REPURCHASE_REQUIRED_FIELDS,
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


def _make_repurchase_payload(
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
        fields = list(_TUSHARE_REPURCHASE_REQUIRED_FIELDS)

    if empty_items:
        items = []
    elif rows is not None:
        items = [[r.get(f, None) for f in fields] for r in rows]
    else:
        default_row = {
            "ts_code": "600519.SH",
            "ann_date": "20250120",
            "end_date": "20251231",
            "proc": "预案",
            "exp_date": "20251231",
            "vol": 100.0,
            "amount": 50000.0,
            "high_limit": 1800.0,
            "low_limit": 1500.0,
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


def test_repurchase_success_row(monkeypatch):
    """1. 正常一行：验证返回结构、按列名取数、canonical_event_id 恒为 None、无 token 泄露。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    row_data = {
        "ts_code": "600519.SH",
        "ann_date": "20250120",
        "end_date": "20251231",
        "proc": "预案",
        "exp_date": "20251231",
        "vol": 150.0,
        "amount": 60000.0,
        "high_limit": 1850.0,
        "low_limit": 1550.0,
    }
    payload = _make_repurchase_payload(rows=[row_data])

    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-25"
        )

    assert error is None
    assert category is None
    assert isinstance(records, list)
    assert len(records) == 1

    rec = records[0]
    # 契约 4：canonical_event_id 必须为 None，严禁伪造 cninfo ID
    assert rec["canonical_event_id"] is None

    # 契约 2 & 5：按列名取数
    assert rec["symbol"] == "600519"
    assert rec["ts_code"] == "600519.SH"
    assert rec["ann_date"] == "2025-01-20"
    assert rec["end_date"] == "20251231"
    assert rec["proc"] == "预案"
    assert rec["exp_date"] == "20251231"
    assert rec["vol"] == 150.0
    assert rec["amount"] == 60000.0
    assert rec["high_limit"] == 1850.0
    assert rec["low_limit"] == 1550.0
    assert rec["source_type"] == "tushare_repurchase"
    assert rec["collateral_id"].startswith("tushare:repurchase:600519.SH:2025-01-20")

    # payload 内也同样结构化携带指标
    assert rec["payload"]["proc"] == "预案"
    assert rec["payload"]["vol"] == 150.0
    assert rec["payload"]["amount"] == 60000.0
    assert rec["payload"]["high_limit"] == 1850.0
    assert rec["payload"]["low_limit"] == 1550.0

    # 验证网关调用参数
    assert mock_post.call_count == 1
    call_json = mock_post.call_args[1]["json"]
    assert call_json["api_name"] == _TUSHARE_REPURCHASE_API
    assert call_json["params"]["ts_code"] == "600519.SH"
    assert call_json["token"] == "mock_repurchase_token_abc"
    for field in _TUSHARE_REPURCHASE_REQUIRED_FIELDS:
        assert field in call_json["fields"]


def test_repurchase_pit_ann_date_exceeds_as_of_dropped(monkeypatch):
    """2. PIT 契约：ann_date > as_of 的行丢弃；不得用 end_date 或 exp_date 截断。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    row_prior = {
        "ts_code": "600519.SH",
        "ann_date": "20250115",
        "end_date": "20251231",
        "proc": "董事会预案",
        "exp_date": "20251231",
        "vol": 50.0,
        "amount": 20000.0,
        "high_limit": 1700.0,
        "low_limit": 1400.0,
    }
    row_future = {
        "ts_code": "600519.SH",
        "ann_date": "20250210",
        "end_date": "20251231",
        "proc": "股东大会通过",
        "exp_date": "20251231",
        "vol": 100.0,
        "amount": 40000.0,
        "high_limit": 1750.0,
        "low_limit": 1450.0,
    }
    payload = _make_repurchase_payload(rows=[row_prior, row_future])

    # Case 2.1: as_of = "2025-01-20" 时，row_future (ann_date="20250210") 必须被丢弃，只保留 row_prior
    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )

    assert error is None
    assert category is None
    assert len(records) == 1
    assert records[0]["ann_date"] == "2025-01-15"
    assert records[0]["proc"] == "董事会预案"
    assert records[0]["amount"] == 20000.0

    # Case 2.2: 铁律检验——严禁用 end_date 或 exp_date 截断！
    # 当 as_of = "2024-12-31" 时，两行的 end_date/exp_date 均为 "20251231"。
    # 若误用 end_date 或 exp_date 截断，逻辑必然失真。
    # 严格 PIT 下，两行 ann_date 均晚于 2024-12-31，因此全部被丢弃，返回 collateral_empty。
    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2024-12-31"
        )

    assert records == []
    assert category == "collateral_empty"
    assert "确认无公告" not in str(error)
    assert "确认无公告" not in str(category)


def test_repurchase_pit_completion_state_lookahead_dropped(monkeypatch):
    """7. 必须覆盖：proc=完成 且 ann_date > as_of 的累计金额行被丢弃（防完成态穿越）。

    业务场景：
    - 2025-01-10 披露实施中公告：累计回购 5000 万元；
    - 2025-03-20 披露完成公告：回购实施完成，累计金额达 20000 万元（2亿元）；
    - 若在 as_of="2025-01-15" 查询，必须坚决丢弃 2025-03-20 的完成态行，绝不能提前获知最终累计完成金额。
    """
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    row_in_progress = {
        "ts_code": "600519.SH",
        "ann_date": "20250110",
        "end_date": "20251231",
        "proc": "实施中",
        "exp_date": "20251231",
        "vol": 3.0,
        "amount": 5000.0,
        "high_limit": 1800.0,
        "low_limit": 1500.0,
    }
    row_completed_future = {
        "ts_code": "600519.SH",
        "ann_date": "20250320",
        "end_date": "20251231",
        "proc": "完成",
        "exp_date": "20251231",
        "vol": 12.0,
        "amount": 20000.0,
        "high_limit": 1800.0,
        "low_limit": 1500.0,
    }
    payload = _make_repurchase_payload(rows=[row_in_progress, row_completed_future])

    # Case 7.1: 在 as_of="2025-01-15" 时，未发布的完成态行必须被彻底丢弃
    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-15"
        )

    assert error is None
    assert category is None
    assert len(records) == 1
    # 验证只保留了历史实施态，完成态被完全剔除
    assert records[0]["proc"] == "实施中"
    assert records[0]["amount"] == 5000.0
    assert records[0]["ann_date"] == "2025-01-10"

    for r in records:
        assert r["proc"] != "完成"
        assert r["amount"] != 20000.0
        assert r["ann_date"] <= "2025-01-15"

    # Case 7.2: 在 as_of="2025-04-01" 时（完成公告发布后），完成态行合法可见
    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-04-01"
        )

    assert error is None
    assert len(records) == 2
    # 最新公告排在前面
    assert records[0]["proc"] == "完成"
    assert records[0]["amount"] == 20000.0
    assert records[0]["ann_date"] == "2025-03-20"


def test_repurchase_empty_table_collateral_empty(monkeypatch):
    """3. 空表契约：0 行 -> collateral_empty（测试断言文本不含「确认无公告」）。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    # Case 3.1: items 为空列表 []
    empty_payload = _make_repurchase_payload(empty_items=True)
    with patch("requests.post", return_value=_MockResponse(empty_payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "collateral_empty"
    assert error == "tushare.repurchase:collateral_empty"
    # 契约 3 显式断言：文本绝对不包含「确认无公告」
    assert "确认无公告" not in str(error)
    assert "确认无公告" not in str(category)

    # Case 3.2: data 为 None
    none_data_payload = {"code": 0, "msg": "", "data": None}
    with patch("requests.post", return_value=_MockResponse(none_data_payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "collateral_empty"
    assert "确认无公告" not in str(error)
    assert "确认无公告" not in str(category)


def test_repurchase_missing_ann_date_schema_drift(monkeypatch):
    """4. 缺列契约：缺 ann_date -> schema_drift/missing_field。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    # 构造缺失 ann_date 的 fields
    fields_without_ann_date = [
        f for f in _TUSHARE_REPURCHASE_REQUIRED_FIELDS if f != "ann_date"
    ]
    payload = _make_repurchase_payload(fields=fields_without_ann_date)

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category in ("schema_drift", "missing_field")
    assert "schema_drift" in str(error) or "missing_field" in str(error)
    assert "ann_date" in str(error)


def test_repurchase_token_missing_provider_failure(monkeypatch):
    """5. Token 缺失契约：token_missing -> provider_failure，禁止发起网络请求。"""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert error == "tushare.repurchase:provider_failure(token_missing)"
    # 严格禁止发起网络调用
    assert mock_post.call_count == 0


def test_repurchase_403_forbidden_provider_failure(monkeypatch):
    """6. 403 契约：403 / permission_denied -> provider_failure，严禁泄露 Token。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "secret_repurchase_token_never_leak_888")
    provider = CnAkshareProvider()

    # Case 6.1: HTTP 403
    with patch(
        "requests.post",
        return_value=_MockResponse({"code": 0, "data": None}, status_code=403),
    ):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert "provider_failure" in str(error)
    assert "secret_repurchase_token_never_leak_888" not in str(error)

    # Case 6.2: 业务返回码 2002 权限不足
    perm_payload = {"code": 2002, "msg": "抱歉，您没有权限访问 repurchase 接口", "data": None}
    with patch(
        "requests.post",
        return_value=_MockResponse(perm_payload, status_code=200),
    ):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert "provider_failure" in str(error)
    assert "secret_repurchase_token_never_leak_888" not in str(error)


def test_repurchase_no_iloc_column_reordering(monkeypatch):
    """验证按列名取数：返回字段次序颠倒打乱时，依然能够按列名准确取数，不依赖 iloc 索引。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    scrambled_fields = [
        "proc",
        "low_limit",
        "ts_code",
        "amount",
        "ann_date",
        "exp_date",
        "high_limit",
        "end_date",
        "vol",
    ]
    row_data = {
        "ts_code": "600519.SH",
        "ann_date": "20250120",
        "end_date": "20251231",
        "proc": "股东大会通过",
        "exp_date": "20251231",
        "vol": 80.0,
        "amount": 35000.0,
        "high_limit": 1820.0,
        "low_limit": 1520.0,
    }
    payload = _make_repurchase_payload(fields=scrambled_fields, rows=[row_data])

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-25"
        )

    assert error is None
    assert category is None
    assert len(records) == 1
    rec = records[0]
    assert rec["proc"] == "股东大会通过"
    assert rec["amount"] == 35000.0
    assert rec["vol"] == 80.0
    assert rec["high_limit"] == 1820.0
    assert rec["low_limit"] == 1520.0
    assert rec["ann_date"] == "2025-01-20"
    assert rec["ts_code"] == "600519.SH"
    assert rec["canonical_event_id"] is None


def test_repurchase_input_validation(monkeypatch):
    """验证非法输入参数校验（symbol / as_of），验证不发出任何网关请求。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        # 非法 symbol
        records, error, category = provider._fetch_tushare_repurchase(
            "invalid_symbol_999", as_of="2025-01-20"
        )
        assert records == []
        assert category == "validation"
        assert error == "tushare.repurchase:validation(symbol)"

        # 非法 as_of
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="invalid_as_of_date"
        )
        assert records == []
        assert category == "validation"
        assert error == "tushare.repurchase:validation(as_of)"

    assert mock_post.call_count == 0


def test_repurchase_url_environment_cascade(monkeypatch):
    """验证网关环境变量路由：TUSHARE_API_URL > TUSHARE_BASE_URL > 官方兜底。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()
    payload = _make_repurchase_payload()

    # 1. TUSHARE_API_URL 优先
    monkeypatch.setenv("TUSHARE_API_URL", "http://gateway.collateral:8080/v1")
    monkeypatch.setenv("TUSHARE_BASE_URL", "http://backup.collateral:8080/v1")
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_repurchase("600519", as_of="2025-01-25")
    assert mock_post.call_args[0][0] == "http://gateway.collateral:8080/v1"

    # 2. TUSHARE_BASE_URL 备选
    monkeypatch.delenv("TUSHARE_API_URL", raising=False)
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_repurchase("600519", as_of="2025-01-25")
    assert mock_post.call_args[0][0] == "http://backup.collateral:8080/v1"

    # 3. 环境变量均未配置时，默认回落至官方端点 https://api.tushare.pro
    monkeypatch.delenv("TUSHARE_BASE_URL", raising=False)
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_repurchase("600519", as_of="2025-01-25")
    assert mock_post.call_args[0][0] == "https://api.tushare.pro"


def test_repurchase_empty_items_missing_ann_date_schema_drift(monkeypatch):
    """验证即便 items 为空列表，只要 fields 缺少 ann_date，必须判定为 schema_drift，绝不误判为 collateral_empty。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    fields_without_ann_date = [
        f for f in _TUSHARE_REPURCHASE_REQUIRED_FIELDS if f != "ann_date"
    ]
    payload = _make_repurchase_payload(
        fields=fields_without_ann_date, empty_items=True
    )

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "schema_drift"
    assert "schema_drift" in str(error)
    assert "ann_date" in str(error)


def test_repurchase_token_error_in_msg_provider_failure(monkeypatch):
    """验证未知 code 只要 msg 中包含 token / permission / 403 标识，一律归入 provider_failure。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    token_err_payload = {
        "code": -1,
        "msg": "Token is required or invalid parameter",
        "data": None,
    }
    with patch("requests.post", return_value=_MockResponse(token_err_payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert "provider_failure" in str(error)


def test_repurchase_optional_dates_validation(monkeypatch):
    """验证可选日期参数非法输入校验（ann_date, start_date, end_date）。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        # 非法 ann_date
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20", ann_date="bad_ann_date"
        )
        assert category == "validation"
        assert error == "tushare.repurchase:validation(ann_date)"

        # 非法 start_date
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20", start_date="bad_start_date"
        )
        assert category == "validation"
        assert error == "tushare.repurchase:validation(start_date)"

        # 非法 end_date
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20", end_date="bad_end_date"
        )
        assert category == "validation"
        assert error == "tushare.repurchase:validation(end_date)"

    assert mock_post.call_count == 0


def test_repurchase_malformed_row_schema_drift(monkeypatch):
    """验证单行数据畸形或行内 ann_date 无法解析时，严肃归入 schema_drift。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    # Case 1: 行长度少于 fields 数量
    bad_length_payload = {
        "code": 0,
        "msg": "",
        "data": {
            "fields": list(_TUSHARE_REPURCHASE_REQUIRED_FIELDS),
            "items": [["600519.SH"]],  # 只有1列，远少于9列
        },
    }
    with patch("requests.post", return_value=_MockResponse(bad_length_payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )
    assert category == "schema_drift"
    assert "malformed_row" in str(error)

    # Case 2: 行内缺少 ann_date 值
    row_missing_ann_val = {
        "ts_code": "600519.SH",
        "ann_date": None,
        "end_date": "20251231",
        "proc": "预案",
        "exp_date": "20251231",
        "vol": 100.0,
        "amount": 50000.0,
        "high_limit": 1800.0,
        "low_limit": 1500.0,
    }
    payload2 = _make_repurchase_payload(rows=[row_missing_ann_val])
    with patch("requests.post", return_value=_MockResponse(payload2)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-20"
        )
    assert category == "schema_drift"
    assert "missing_field:ann_date" in str(error)


def test_repurchase_cross_security_row_ignored(monkeypatch):
    """验证跨标的异常数据行（非查询标的 ts_code）被剔除。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_repurchase_token_abc")
    provider = CnAkshareProvider()

    row_correct = {
        "ts_code": "600519.SH",
        "ann_date": "20250120",
        "end_date": "20251231",
        "proc": "预案",
        "exp_date": "20251231",
        "vol": 100.0,
        "amount": 50000.0,
        "high_limit": 1800.0,
        "low_limit": 1500.0,
    }
    row_other = {
        "ts_code": "000001.SZ",  # 平安银行，非贵州茅台
        "ann_date": "20250120",
        "end_date": "20251231",
        "proc": "预案",
        "exp_date": "20251231",
        "vol": 500.0,
        "amount": 100000.0,
        "high_limit": 15.0,
        "low_limit": 10.0,
    }
    payload = _make_repurchase_payload(rows=[row_correct, row_other])

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_repurchase(
            "600519", as_of="2025-01-25"
        )

    assert error is None
    assert category is None
    assert len(records) == 1
    assert records[0]["ts_code"] == "600519.SH"
    assert records[0]["symbol"] == "600519"

