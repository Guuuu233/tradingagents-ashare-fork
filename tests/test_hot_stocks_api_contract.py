import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import HTTPException

from api import main as main_mod


_PROVIDER_NAMES = {
    "em": "stock_hot_rank_em",
    "xq": "stock_hot_follow_xq",
    "ths": "stock_rank_lxsz_ths",
}
_MISSING = object()


def _fake_akshare(responses: dict[str, object]) -> MagicMock:
    ak = MagicMock()
    for source, provider_name in _PROVIDER_NAMES.items():
        provider = getattr(ak, provider_name)
        response = responses.get(source, _MISSING)
        if response is _MISSING:
            provider.side_effect = RuntimeError("unexpected provider call")
        elif isinstance(response, BaseException):
            provider.side_effect = response
        else:
            provider.return_value = response
    return ak


def _patch_akshare(ak: MagicMock):
    return patch.dict(sys.modules, {"akshare": ak})


_INVALID_QUOTES = [None, "", "   ", float("nan"), "NaN", "not-a-number", pd.NA]


@pytest.mark.parametrize(
    ("source", "rows"),
    [
        (
            "em",
            [
                {
                    "代码": f"SH60000{index}",
                    "股票名称": f"EM {index}",
                    **(
                        {}
                        if value is None
                        else {
                            "最新价": value,
                            "涨跌额": value,
                            "涨跌幅": value,
                        }
                    ),
                }
                for index, value in enumerate([*_INVALID_QUOTES, 0])
            ],
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
                for index, value in enumerate([*_INVALID_QUOTES, 0])
            ],
        ),
        (
            "ths",
            [
                {
                    "股票代码": f"00000{index}",
                    "股票简称": f"THS {index}",
                    "连涨天数": 3,
                    **(
                        {}
                        if value is None
                        else {
                            "收盘价": value,
                            "连续涨跌幅": value,
                        }
                    ),
                }
                for index, value in enumerate([*_INVALID_QUOTES, 0])
            ],
        ),
    ],
)
def test_missing_quotes_stay_null_and_real_zero_is_preserved(source: str, rows: list[dict]):
    ak = _fake_akshare({source: pd.DataFrame(rows)})

    with _patch_akshare(ak):
        result = main_mod.get_hot_stocks(source=source, limit=len(rows))

    assert result["source"] == source
    assert result["requested_source"] == source
    assert result["fallback"] is False
    assert [stock["rank"] for stock in result["stocks"]] == list(range(1, len(rows) + 1))

    if source == "em":
        for field in ("price", "change", "change_pct"):
            assert [stock[field] for stock in result["stocks"][:-1]] == [None] * len(_INVALID_QUOTES)
            assert result["stocks"][-1][field] == 0.0
    elif source == "xq":
        assert [stock["price"] for stock in result["stocks"][:-1]] == [None] * len(_INVALID_QUOTES)
        assert result["stocks"][-1]["price"] == 0.0
        assert [stock["change"] for stock in result["stocks"]] == [None] * len(rows)
        assert [stock["change_pct"] for stock in result["stocks"]] == [None] * len(rows)
    else:
        for field in ("price", "change_pct"):
            assert [stock[field] for stock in result["stocks"][:-1]] == [None] * len(_INVALID_QUOTES)
            assert result["stocks"][-1][field] == 0.0
        assert [stock["change"] for stock in result["stocks"]] == [None] * len(rows)


def test_fallback_reports_actual_and_requested_sources():
    ak = _fake_akshare(
        {
            "em": RuntimeError("primary unavailable"),
            "xq": pd.DataFrame(
                [{"股票代码": "SH600519", "股票简称": "贵州茅台", "最新价": 0, "关注": 1}]
            ),
        }
    )

    with _patch_akshare(ak):
        result = main_mod.get_hot_stocks(source="em", limit=1)

    assert result["source"] == "xq"
    assert result["requested_source"] == "em"
    assert result["fallback"] is True
    assert result["stocks"][0]["price"] == 0.0
    assert result["stocks"][0]["change"] is None
    assert result["stocks"][0]["change_pct"] is None


def test_negative_limit_is_rejected_before_calling_a_provider():
    ak = _fake_akshare({"em": pd.DataFrame([{"代码": "SH600519"}])})

    with _patch_akshare(ak), pytest.raises(HTTPException) as exc_info:
        main_mod.get_hot_stocks(source="em", limit=-1)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "limit must be non-negative"
    ak.stock_hot_rank_em.assert_not_called()


def test_empty_primary_frame_falls_back_for_positive_limit():
    ak = _fake_akshare(
        {
            "em": pd.DataFrame(),
            "xq": pd.DataFrame(
                [{"股票代码": "SH600519", "股票简称": "贵州茅台", "最新价": 12.5, "关注": 3}]
            ),
        }
    )

    with _patch_akshare(ak):
        result = main_mod.get_hot_stocks(source="em", limit=1)

    assert result["source"] == "xq"
    assert result["requested_source"] == "em"
    assert result["fallback"] is True
    assert result["total"] == 1
    assert result["stocks"][0]["price"] == 12.5


def test_none_primary_frame_falls_back_for_positive_limit():
    ak = _fake_akshare(
        {
            "em": None,
            "xq": pd.DataFrame(
                [{"股票代码": "SH600519", "股票简称": "贵州茅台", "最新价": 12.5, "关注": 3}]
            ),
        }
    )

    with _patch_akshare(ak):
        result = main_mod.get_hot_stocks(source="em", limit=1)

    assert result["source"] == "xq"
    assert result["requested_source"] == "em"
    assert result["fallback"] is True
    assert result["total"] == 1


def test_all_sources_empty_raise_503_for_positive_limit():
    empty = pd.DataFrame()
    ak = _fake_akshare({source: empty for source in _PROVIDER_NAMES})

    with _patch_akshare(ak), pytest.raises(HTTPException) as exc_info:
        main_mod.get_hot_stocks(source="em", limit=1)

    assert exc_info.value.status_code == 503
    assert "All data sources failed" in exc_info.value.detail
    for provider_name in _PROVIDER_NAMES.values():
        getattr(ak, provider_name).assert_called_once()


def test_zero_limit_keeps_empty_success_without_fallback():
    ak = _fake_akshare({"em": pd.DataFrame()})

    with _patch_akshare(ak):
        result = main_mod.get_hot_stocks(source="em", limit=0)

    assert result == {
        "stocks": [],
        "total": 0,
        "source": "em",
        "requested_source": "em",
        "fallback": False,
    }
    ak.stock_hot_rank_em.assert_called_once_with()
    ak.stock_hot_follow_xq.assert_not_called()
    ak.stock_rank_lxsz_ths.assert_not_called()
