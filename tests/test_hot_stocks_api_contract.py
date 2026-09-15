import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import HTTPException

from api import main as main_mod


INVALID_QUOTES = [None, "   ", float("nan"), "not-a-number"]


def _fake_akshare(source: str, rows: list[dict]) -> MagicMock:
    ak = MagicMock()
    providers = {
        "em": "stock_hot_rank_em",
        "xq": "stock_hot_follow_xq",
        "ths": "stock_rank_lxsz_ths",
    }
    for provider in providers.values():
        getattr(ak, provider).side_effect = RuntimeError("unexpected fallback")
    getattr(ak, providers[source]).side_effect = None
    getattr(ak, providers[source]).return_value = pd.DataFrame(rows)
    return ak


@pytest.mark.parametrize(
    ("source", "rows", "nullable_fields"),
    [
        (
            "em",
            [
                {
                    "代码": f"SH60000{index}",
                    "股票名称": f"EM {index}",
                    **({} if value is None else {
                        "最新价": value,
                        "涨跌额": value,
                        "涨跌幅": value,
                    }),
                }
                for index, value in enumerate([*INVALID_QUOTES, 0])
            ],
            ("price", "change", "change_pct"),
        ),
        (
            "xq",
            [
                {
                    "股票代码": f"SZ00000{index}",
                    "股票简称": f"XQ {index}",
                    "关注": 100 + index,
                    **({} if value is None else {"最新价": value}),
                }
                for index, value in enumerate([*INVALID_QUOTES, 0])
            ],
            ("price", "change", "change_pct"),
        ),
        (
            "ths",
            [
                {
                    "股票代码": f"00000{index}",
                    "股票简称": f"THS {index}",
                    "连涨天数": 3,
                    **({} if value is None else {
                        "收盘价": value,
                        "连续涨跌幅": value,
                    }),
                }
                for index, value in enumerate([*INVALID_QUOTES, 0])
            ],
            ("price", "change", "change_pct"),
        ),
    ],
)
def test_missing_quotes_stay_null_and_real_zero_is_preserved(
    source: str,
    rows: list[dict],
    nullable_fields: tuple[str, ...],
):
    ak = _fake_akshare(source, rows)

    with patch.dict(sys.modules, {"akshare": ak}):
        result = main_mod.get_hot_stocks(source=source, limit=len(rows))

    assert result["source"] == source
    assert result["requested_source"] == source
    assert result["fallback"] is False
    assert [stock["rank"] for stock in result["stocks"]] == [1, 2, 3, 4, 5]
    for field in nullable_fields:
        assert [stock[field] for stock in result["stocks"][:4]] == [None] * 4
        source_does_not_provide = (
            (source == "xq" and field in {"change", "change_pct"})
            or (source == "ths" and field == "change")
        )
        if not source_does_not_provide:
            assert result["stocks"][4][field] == 0.0
    if source == "xq":
        assert [stock["change"] for stock in result["stocks"]] == [None] * 5
        assert [stock["change_pct"] for stock in result["stocks"]] == [None] * 5
    if source == "ths":
        assert [stock["change"] for stock in result["stocks"]] == [None] * 5


def test_fallback_reports_actual_and_requested_sources():
    ak = _fake_akshare(
        "xq",
        [{"股票代码": "SH600519", "股票简称": "贵州茅台", "最新价": 0, "关注": 1}],
    )
    ak.stock_hot_rank_em.side_effect = RuntimeError("primary unavailable")

    with patch.dict(sys.modules, {"akshare": ak}):
        result = main_mod.get_hot_stocks(source="em", limit=1)

    assert result["source"] == "xq"
    assert result["requested_source"] == "em"
    assert result["fallback"] is True
    assert result["stocks"][0]["price"] == 0.0
    assert result["stocks"][0]["change"] is None
    assert result["stocks"][0]["change_pct"] is None


def test_negative_limit_is_rejected_before_calling_a_provider():
    ak = _fake_akshare("em", [{"代码": "SH600519"}])

    with patch.dict(sys.modules, {"akshare": ak}), pytest.raises(HTTPException) as exc_info:
        main_mod.get_hot_stocks(source="em", limit=-1)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "limit must be non-negative"
    ak.stock_hot_rank_em.assert_not_called()
