"""Unit tests for Tushare Pro disclosure_date collateral reader interface (C-05 Slice 4 / DAV-644).

Covers all contracts:
1. PIT boundary guard: rows with ann_date > as_of are discarded; strictly forbids
   using end_date, pre_date, actual_date, or modify_date for truncation.
2. actual_date is preserved as a collateral field as-is (if present); strictly forbidden
   from deciding historical visibility or PIT.
3. Column access by name (no iloc); missing ann_date in schema -> schema_drift/missing_field.
   Schema drift check must occur before checking items emptiness (empty table cannot mask missing columns).
4. 403 / token_missing -> provider_failure; 0 rows -> collateral_empty (asserts text
   does not contain "确认无公告").
5. Returned records must have canonical_event_id is None (strictly forbids inventing cninfo ID).
6. Fields extracted by column name: ts_code, ann_date, end_date, pre_date, actual_date, modify_date
   (missing columns typed as None / typed missing, strictly forbids inventing values).
7. All tests mock HTTP: success row, date-exceeds-as_of discarded, empty table,
   missing column, token missing, 403 permission denied. Strictly forbids real gateway traffic.
   Must specifically test: actual_date in future but ann_date <= as_of is preserved
   (prevents treating actual disclosure date as PIT).
"""

from unittest.mock import patch

import pytest
import requests

from tradingagents.dataflows.providers.cn_akshare_provider import (
    _TUSHARE_DISCLOSURE_DATE_API,
    _TUSHARE_DISCLOSURE_DATE_REQUIRED_FIELDS,
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


def _make_disclosure_date_payload(
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
        fields = list(_TUSHARE_DISCLOSURE_DATE_REQUIRED_FIELDS)

    if empty_items:
        items = []
    elif rows is not None:
        items = [[r.get(f, None) for f in fields] for r in rows]
    else:
        default_row = {
            "ts_code": "600519.SH",
            "ann_date": "20250120",
            "end_date": "20241231",
            "pre_date": "20250420",
            "actual_date": "20250425",
            "modify_date": "20250410",
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


def test_disclosure_date_success_row(monkeypatch):
    """1. 正常一行：验证返回结构、按列名取数、canonical_event_id 恒为 None、无 token 泄露。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    row_data = {
        "ts_code": "600519.SH",
        "ann_date": "20250120",
        "end_date": "20241231",
        "pre_date": "20250420",
        "actual_date": "20250425",
        "modify_date": "20250410",
    }
    payload = _make_disclosure_date_payload(rows=[row_data])

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-25"
        )

    assert error is None
    assert category is None
    assert len(records) == 1

    rec = records[0]
    # Contract 5: canonical_event_id 必须是 None，严禁伪造 cninfo ID
    assert rec["canonical_event_id"] is None
    assert rec["collateral_id"] == "tushare:disclosure_date:600519.SH:2025-01-20:20241231"
    assert rec["source_type"] == "tushare_disclosure_date"
    assert rec["symbol"] == "600519"
    assert rec["ts_code"] == "600519.SH"
    assert rec["ann_date"] == "2025-01-20"
    assert rec["end_date"] == "20241231"
    assert rec["pre_date"] == "20250420"
    assert rec["actual_date"] == "20250425"
    assert rec["modify_date"] == "20250410"

    # Contract 6: payload 结构化旁证字段
    assert rec["payload"] == {
        "end_date": "20241231",
        "pre_date": "20250420",
        "actual_date": "20250425",
        "modify_date": "20250410",
    }

    # 严禁在返回结构中泄露任何 Token
    assert "mock_disclosure_token_abc" not in str(rec)


def test_disclosure_date_pit_ann_date_exceeds_as_of_dropped(monkeypatch):
    """2. PIT 契约：ann_date > as_of 的行丢弃；不得用 end_date、pre_date、actual_date、modify_date 截断。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    row_prior = {
        "ts_code": "600519.SH",
        "ann_date": "20250115",
        "end_date": "20241231",
        "pre_date": "20250420",
        "actual_date": "20250425",
        "modify_date": None,
    }
    row_future = {
        "ts_code": "600519.SH",
        "ann_date": "20250210",
        "end_date": "20241231",
        "pre_date": "20250428",
        "actual_date": "20250429",
        "modify_date": "20250210",
    }
    payload = _make_disclosure_date_payload(rows=[row_prior, row_future])

    # Case 2.1: as_of = "2025-01-20" 时，row_future (ann_date="20250210") 必须被丢弃，只保留 row_prior
    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert error is None
    assert category is None
    assert len(records) == 1
    assert records[0]["ann_date"] == "2025-01-15"
    assert records[0]["pre_date"] == "20250420"
    assert records[0]["actual_date"] == "20250425"

    # Case 2.2: 铁律检验——严禁用 end_date 截断！
    # 当 as_of = "2024-12-31" 时，两行的 end_date 均为 "20241231"。
    # 若误用 end_date 截断（如 end_date <= as_of），就会错误保留两行。
    # 严格 PIT 下，两行 ann_date 均晚于 2024-12-31，因此必须全部被丢弃，返回 collateral_empty。
    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2024-12-31"
        )

    assert records == []
    assert category == "collateral_empty"
    assert "确认无公告" not in str(error)
    assert "确认无公告" not in str(category)


def test_disclosure_date_pit_strictly_forbids_pre_date_and_modify_date_truncation(monkeypatch):
    """验证不得用 pre_date 或 modify_date 作为 PIT 截断标准。

    场景说明：
    如果一条记录 ann_date="20250210" 处于未来，即便其 pre_date 或 modify_date 早于 as_of，
    也不能由于误用 pre_date/modify_date 导致该行在历史提前可见。必须严格按 ann_date <= as_of 截断。
    """
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    row_data = {
        "ts_code": "600519.SH",
        "ann_date": "20250210",
        "end_date": "20241231",
        "pre_date": "20250105",
        "actual_date": "20250425",
        "modify_date": "20250110",
    }
    payload = _make_disclosure_date_payload(rows=[row_data])

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    # ann_date="20250210" > as_of="2025-01-20"，因此必须被丢弃
    assert records == []
    assert category == "collateral_empty"


def test_disclosure_date_actual_date_in_future_preserved_pit(monkeypatch):
    """7. 核心契约：另测 actual_date 在未来但 ann_date <= as_of 的行仍保留（防把实际披露日当 PIT）。

    业务场景：
    - 上市公司在 2025-01-15 披露财报披露预约计划 (ann_date="20250115")；
    - 预约披露日为 2025-04-20 (pre_date="20250420")；
    - 财报最终在 2025-04-25 实际发布 (actual_date="20250425")；
    - 站在历史时点 as_of="2025-01-20"：
      ann_date="2025-01-15" <= as_of="2025-01-20"，属于历史已知事实；
      若误用 actual_date 进行 PIT 过滤，就会由于 actual_date > as_of 误杀该行。
    - 契约规定：此时该行必须合法保留，且 actual_date 字段作为旁证原样保留，严禁用 actual_date 决定历史可见性。
    """
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    row_data = {
        "ts_code": "600519.SH",
        "ann_date": "20250115",
        "end_date": "20241231",
        "pre_date": "20250420",
        "actual_date": "20250425",
        "modify_date": None,
    }
    payload = _make_disclosure_date_payload(rows=[row_data])

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert error is None
    assert category is None
    assert len(records) == 1

    rec = records[0]
    assert rec["ann_date"] == "2025-01-15"
    # actual_date 原样保留
    assert rec["actual_date"] == "20250425"
    assert rec["pre_date"] == "20250420"
    assert rec["end_date"] == "20241231"
    assert rec["modify_date"] is None
    assert rec["canonical_event_id"] is None


def test_disclosure_date_empty_table_collateral_empty(monkeypatch):
    """4. 空表契约：0 行 -> collateral_empty（测试断言文本不含「确认无公告」）。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    # Case 4.1: items 为空列表 []
    empty_payload = _make_disclosure_date_payload(empty_items=True)
    with patch("requests.post", return_value=_MockResponse(empty_payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "collateral_empty"
    assert error == "tushare.disclosure_date:collateral_empty"
    # 契约 4 显式断言：文本绝对不包含「确认无公告」
    assert "确认无公告" not in str(error)
    assert "确认无公告" not in str(category)

    # Case 4.2: data 为 None
    none_data_payload = {"code": 0, "msg": "", "data": None}
    with patch("requests.post", return_value=_MockResponse(none_data_payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "collateral_empty"
    assert "确认无公告" not in str(error)
    assert "确认无公告" not in str(category)


def test_disclosure_date_missing_ann_date_schema_drift(monkeypatch):
    """3. 缺列契约：缺 ann_date -> schema_drift/missing_field。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    # 构造缺失 ann_date 的 fields
    fields_without_ann_date = [
        f for f in _TUSHARE_DISCLOSURE_DATE_REQUIRED_FIELDS if f != "ann_date"
    ]
    payload = _make_disclosure_date_payload(fields=fields_without_ann_date)

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category in ("schema_drift", "missing_field")
    assert "schema_drift" in str(error) or "missing_field" in str(error)
    assert "ann_date" in str(error)


def test_disclosure_date_empty_items_missing_ann_date_schema_drift(monkeypatch):
    """3. 契约深化：空表判定不得盖住缺列（items=[] 但 fields 缺少 ann_date 时必须判定为 schema_drift）。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    fields_without_ann_date = [
        f for f in _TUSHARE_DISCLOSURE_DATE_REQUIRED_FIELDS if f != "ann_date"
    ]
    payload = _make_disclosure_date_payload(
        fields=fields_without_ann_date, empty_items=True
    )

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "schema_drift"
    assert "schema_drift" in str(error)
    assert "ann_date" in str(error)


def test_disclosure_date_token_missing_provider_failure(monkeypatch):
    """4. Token 缺失契约：token_missing -> provider_failure，禁止发起网络请求。"""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert error == "tushare.disclosure_date:provider_failure(token_missing)"
    # 严格禁止发起真实或 mock 网络调用
    assert mock_post.call_count == 0


def test_disclosure_date_403_forbidden_provider_failure(monkeypatch):
    """4. 403 契约：403 / permission_denied -> provider_failure，严禁泄露 Token。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "secret_disclosure_token_never_leak_777")
    provider = CnAkshareProvider()

    # Case 4.1: HTTP 403
    with patch(
        "requests.post",
        return_value=_MockResponse({"code": 0, "data": None}, status_code=403),
    ):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert "provider_failure" in str(error)
    assert "secret_disclosure_token_never_leak_777" not in str(error)

    # Case 4.2: 业务返回码 2002 权限不足
    perm_payload = {
        "code": 2002,
        "msg": "抱歉，您没有权限访问 disclosure_date 接口",
        "data": None,
    }
    with patch(
        "requests.post",
        return_value=_MockResponse(perm_payload, status_code=200),
    ):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert "provider_failure" in str(error)
    assert "secret_disclosure_token_never_leak_777" not in str(error)


def test_disclosure_date_no_iloc_column_reordering(monkeypatch):
    """6. 验证按列名取数：返回字段次序颠倒打乱时，依然能够按列名准确取数，不依赖 iloc 索引。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    scrambled_fields = [
        "modify_date",
        "pre_date",
        "ts_code",
        "actual_date",
        "ann_date",
        "end_date",
    ]
    row_data = {
        "ts_code": "600519.SH",
        "ann_date": "20250120",
        "end_date": "20241231",
        "pre_date": "20250420",
        "actual_date": "20250425",
        "modify_date": "20250410",
    }
    payload = _make_disclosure_date_payload(fields=scrambled_fields, rows=[row_data])

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-25"
        )

    assert error is None
    assert category is None
    assert len(records) == 1
    rec = records[0]
    assert rec["ts_code"] == "600519.SH"
    assert rec["ann_date"] == "2025-01-20"
    assert rec["end_date"] == "20241231"
    assert rec["pre_date"] == "20250420"
    assert rec["actual_date"] == "20250425"
    assert rec["modify_date"] == "20250410"
    assert rec["canonical_event_id"] is None


def test_disclosure_date_input_validation(monkeypatch):
    """验证非法输入参数校验（symbol / as_of），验证不发出任何网关请求。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    with patch("requests.post") as mock_post:
        # 非法 symbol
        records, error, category = provider._fetch_tushare_disclosure_date(
            "invalid_symbol_999", as_of="2025-01-20"
        )
        assert records == []
        assert category == "validation"
        assert error == "tushare.disclosure_date:validation(symbol)"

        # 非法 as_of
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="invalid_as_of_date"
        )
        assert records == []
        assert category == "validation"
        assert error == "tushare.disclosure_date:validation(as_of)"

    assert mock_post.call_count == 0


def test_disclosure_date_optional_dates_validation(monkeypatch):
    """验证可选日期参数合法解析及非法输入校验（end_date, pre_date, actual_date, ann_date）。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()
    payload = _make_disclosure_date_payload()

    # 合法参数透传验证
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519",
            as_of="2025-01-25",
            end_date="2024-12-31",
            pre_date="2025-04-20",
            actual_date="2025-04-25",
            ann_date="2025-01-20",
        )
        assert error is None
        assert category is None
        sent_params = mock_post.call_args[1]["json"]["params"]
        assert sent_params["end_date"] == "20241231"
        assert sent_params["pre_date"] == "20250420"
        assert sent_params["actual_date"] == "20250425"
        assert sent_params["ann_date"] == "20250120"

    # 非法日期校验
    with patch("requests.post") as mock_post:
        # 非法 end_date
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20", end_date="bad_end_date"
        )
        assert category == "validation"
        assert error == "tushare.disclosure_date:validation(end_date)"

        # 非法 pre_date
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20", pre_date="bad_pre_date"
        )
        assert category == "validation"
        assert error == "tushare.disclosure_date:validation(pre_date)"

        # 非法 actual_date
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20", actual_date="bad_actual_date"
        )
        assert category == "validation"
        assert error == "tushare.disclosure_date:validation(actual_date)"

        # 非法 ann_date
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20", ann_date="bad_ann_date"
        )
        assert category == "validation"
        assert error == "tushare.disclosure_date:validation(ann_date)"

    assert mock_post.call_count == 0


def test_disclosure_date_malformed_row_schema_drift(monkeypatch):
    """验证单行数据畸形或行内 ann_date 无法解析时，归入 schema_drift。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    # Case 1: 行长度少于 fields 数量
    bad_length_payload = {
        "code": 0,
        "msg": "",
        "data": {
            "fields": list(_TUSHARE_DISCLOSURE_DATE_REQUIRED_FIELDS),
            "items": [["600519.SH"]],  # 只有1列，少于6列
        },
    }
    with patch("requests.post", return_value=_MockResponse(bad_length_payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )
    assert category == "schema_drift"
    assert "malformed_row" in str(error)

    # Case 2: 行内缺少 ann_date 值
    row_missing_ann_val = {
        "ts_code": "600519.SH",
        "ann_date": None,
        "end_date": "20241231",
        "pre_date": "20250420",
        "actual_date": None,
        "modify_date": None,
    }
    payload2 = _make_disclosure_date_payload(rows=[row_missing_ann_val])
    with patch("requests.post", return_value=_MockResponse(payload2)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )
    assert category == "schema_drift"
    assert "missing_field:ann_date" in str(error)


def test_disclosure_date_cross_security_row_ignored(monkeypatch):
    """验证跨标的异常数据行（非查询标的 ts_code）被剔除。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    row_correct = {
        "ts_code": "600519.SH",
        "ann_date": "20250120",
        "end_date": "20241231",
        "pre_date": "20250420",
        "actual_date": "20250425",
        "modify_date": None,
    }
    row_other = {
        "ts_code": "000001.SZ",  # 平安银行，非贵州茅台
        "ann_date": "20250120",
        "end_date": "20241231",
        "pre_date": "20250415",
        "actual_date": "20250418",
        "modify_date": None,
    }
    payload = _make_disclosure_date_payload(rows=[row_correct, row_other])

    with patch("requests.post", return_value=_MockResponse(payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-25"
        )

    assert error is None
    assert category is None
    assert len(records) == 1
    assert records[0]["ts_code"] == "600519.SH"


def test_disclosure_date_token_error_in_msg_provider_failure(monkeypatch):
    """验证未知 code 只要 msg 中包含 token / permission / 403 标识，一律归入 provider_failure。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()

    token_err_payload = {
        "code": -1,
        "msg": "Token is required or invalid parameter",
        "data": None,
    }
    with patch("requests.post", return_value=_MockResponse(token_err_payload)):
        records, error, category = provider._fetch_tushare_disclosure_date(
            "600519", as_of="2025-01-20"
        )

    assert records == []
    assert category == "provider_failure"
    assert "provider_failure" in str(error)


def test_disclosure_date_url_environment_cascade(monkeypatch):
    """验证网关环境变量路由：TUSHARE_API_URL > TUSHARE_BASE_URL > 官方兜底。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock_disclosure_token_abc")
    provider = CnAkshareProvider()
    payload = _make_disclosure_date_payload()

    # 1. TUSHARE_API_URL 优先
    monkeypatch.setenv("TUSHARE_API_URL", "http://gateway.disclosure:8080/v1")
    monkeypatch.setenv("TUSHARE_BASE_URL", "http://backup.disclosure:8080/v1")
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_disclosure_date("600519", as_of="2025-01-25")
    assert mock_post.call_args[0][0] == "http://gateway.disclosure:8080/v1"

    # 2. TUSHARE_BASE_URL 备选
    monkeypatch.delenv("TUSHARE_API_URL", raising=False)
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_disclosure_date("600519", as_of="2025-01-25")
    assert mock_post.call_args[0][0] == "http://backup.disclosure:8080/v1"

    # 3. 环境变量均未配置时，默认回落至官方端点 https://api.tushare.pro
    monkeypatch.delenv("TUSHARE_BASE_URL", raising=False)
    with patch("requests.post", return_value=_MockResponse(payload)) as mock_post:
        provider._fetch_tushare_disclosure_date("600519", as_of="2025-01-25")
    assert mock_post.call_args[0][0] == "https://api.tushare.pro"
