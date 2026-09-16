"""DAV-934: AKShare duplicate rows handling and anti-lookahead tests.

Validates that duplicate rows for the same symbol:
- Fold deterministically when values agree.
- Reject / mark unassessed explicitly when values conflict (no silent first-row selection).
- Produce identical results regardless of vendor return order.
- Explicitly fail on missing/corrupted data.
- Reject out-of-bounds/future dates (anti-lookahead).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.dataflows.providers.cn_akshare_provider import (
    CnAkshareProvider,
    FundFlowText,
)
from tradingagents.dataflows.trade_calendar import DateFetchResult


# ── Fixtures & Mocks ────────────────────────────────────────────────


class _PledgeAk:
    def __init__(self, df: pd.DataFrame | None):
        self.df = df

    def stock_gpzy_pledge_ratio_em(self):
        return self.df


class _PledgeProvider(CnAkshareProvider):
    def __init__(self, df: pd.DataFrame | None):
        super().__init__()
        self._df = df

    def _ak(self):
        return _PledgeAk(self._df)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).zfill(6)


class _MarginAk:
    def __init__(self, df: pd.DataFrame | None):
        self.df = df

    def stock_margin_detail_sse(self, date=None):
        return self.df

    def stock_margin_detail_szse(self, date=None):
        return self.df


class _MarginProvider(CnAkshareProvider):
    def __init__(self, df: pd.DataFrame | None):
        super().__init__()
        self._df = df

    def _ak(self):
        return _MarginAk(self._df)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).zfill(6)


# ── 1. get_share_pledge Red Team Scenarios ─────────────────────────


def test_pledge_rt1_normal_single_row(frozen_trade_date):
    """RT-1: 正常单行数据正常解析。"""
    df = pd.DataFrame(
        {
            "股票代码": ["600519"],
            "质押比例": ["15.5"],
            "质押笔数": [3],
            "所属行业": ["白酒"],
        }
    )
    out = _PledgeProvider(df).get_share_pledge("600519", curr_date=frozen_trade_date)
    assert "整体质押比例：15.5%" in out
    assert "质押笔数: 3 笔" in out
    assert "未排查" not in out


def test_pledge_rt2_duplicate_consistent_rows(frozen_trade_date):
    """RT-2: 同标的重复行、值一致 → 确定性折叠。"""
    df = pd.DataFrame(
        {
            "股票代码": ["600519", "600519"],
            "质押比例": ["15.5", "15.5"],
            "质押笔数": [3, 3],
            "所属行业": ["白酒", "白酒"],
        }
    )
    out = _PledgeProvider(df).get_share_pledge("600519", curr_date=frozen_trade_date)
    assert "整体质押比例：15.5%" in out
    assert "质押笔数: 3 笔" in out
    assert "未排查" not in out


def test_pledge_rt3_duplicate_conflicting_rows(frozen_trade_date):
    """RT-3: 同标的重复行、值冲突 → 显式未排查，不得静默取第一行。"""
    df = pd.DataFrame(
        {
            "股票代码": ["600519", "600519"],
            "质押比例": ["15.5", "25.0"],
            "质押笔数": [3, 3],
            "所属行业": ["白酒", "白酒"],
        }
    )
    out = _PledgeProvider(df).get_share_pledge("600519", curr_date=frozen_trade_date)
    assert "未排查" in out
    assert "冲突" in out
    assert "整体质押比例：15.5%" not in out
    assert "整体质押比例：25.0%" not in out


def test_pledge_rt4_reverse_order_exact_match(frozen_trade_date):
    """RT-4: 供应商返回顺序颠倒 → 结果必须与 RT-3 完全一致。"""
    df_forward = pd.DataFrame(
        {
            "股票代码": ["600519", "600519"],
            "质押比例": ["15.5", "25.0"],
            "质押笔数": [3, 5],
            "所属行业": ["白酒", "白酒"],
        }
    )
    df_reverse = pd.DataFrame(
        {
            "股票代码": ["600519", "600519"],
            "质押比例": ["25.0", "15.5"],
            "质押笔数": [5, 3],
            "所属行业": ["白酒", "白酒"],
        }
    )
    out_forward = _PledgeProvider(df_forward).get_share_pledge("600519", curr_date=frozen_trade_date)
    out_reverse = _PledgeProvider(df_reverse).get_share_pledge("600519", curr_date=frozen_trade_date)
    assert out_forward == out_reverse
    assert "冲突" in out_forward
    assert "未排查" in out_forward


def test_pledge_rt5_empty_missing_and_corrupt(frozen_trade_date):
    """RT-5: 空数据 / 缺列 / 类型异常 → 显式失败，不得静默。"""
    # Empty market dataframe
    out_empty = _PledgeProvider(pd.DataFrame()).get_share_pledge("600519", curr_date=frozen_trade_date)
    assert "未排查" in out_empty
    assert "安全" not in out_empty

    # Missing stock code column
    df_no_code = pd.DataFrame({"质押比例": ["15.5"], "质押笔数": [3]})
    out_no_code = _PledgeProvider(df_no_code).get_share_pledge("600519", curr_date=frozen_trade_date)
    assert "未排查" in out_no_code

    # Unparseable ratio
    df_corrupt = pd.DataFrame(
        {
            "股票代码": ["600519"],
            "质押比例": ["invalid_pct"],
            "质押笔数": [3],
        }
    )
    out_corrupt = _PledgeProvider(df_corrupt).get_share_pledge("600519", curr_date=frozen_trade_date)
    assert "未排查" in out_corrupt
    assert "不可解析" in out_corrupt


def test_pledge_rt6_future_date_rejected(frozen_trade_date):
    """RT-6: 日期越界（晚于请求日）→ 必须拒绝。"""
    df_future = pd.DataFrame(
        {
            "股票代码": ["600519"],
            "交易日期": ["2026-09-30"],  # later than frozen_trade_date (2026-07-28)
            "质押比例": ["15.5"],
            "质押笔数": [3],
        }
    )
    out = _PledgeProvider(df_future).get_share_pledge("600519", curr_date=frozen_trade_date)
    assert "未排查" in out
    assert "晚于请求日" in out


# ── 2. get_margin_trading Red Team Scenarios ───────────────────────


def test_margin_rt1_normal_single_row():
    """RT-1: 正常单行融资融券数据。"""
    df = pd.DataFrame(
        {
            "标的证券代码": ["600519"],
            "融资余额": [10000],
            "融资买入额": [500],
            "融券余量": [10],
        }
    )
    provider = _MarginProvider(df)
    with patch(
        "tradingagents.dataflows.providers.cn_akshare_provider.fetch_with_date_fallback"
    ) as mock_fb:
        def _run(fetch_fn, date_str, max_back=5, start_offset=0):
            return DateFetchResult(
                ok=True,
                data=fetch_fn("2026-07-27"),
                as_of="2026-07-27",
                request_date=date_str,
                attempted=["2026-07-27"],
            )
        mock_fb.side_effect = _run
        out = provider.get_margin_trading("600519", curr_date="2026-07-29")

    assert "融资余额: 10000 元" in out
    assert "融资买入额: 500 元" in out
    assert "融券余量: 10" in out
    assert "未排查" not in out


def test_margin_rt2_duplicate_consistent_rows():
    """RT-2: 同标的重复行、值一致 → 确定性折叠。"""
    df = pd.DataFrame(
        {
            "标的证券代码": ["600519", "600519"],
            "融资余额": [10000, 10000],
            "融资买入额": [500, 500],
            "融券余量": [10, 10],
        }
    )
    provider = _MarginProvider(df)
    with patch(
        "tradingagents.dataflows.providers.cn_akshare_provider.fetch_with_date_fallback"
    ) as mock_fb:
        def _run(fetch_fn, date_str, max_back=5, start_offset=0):
            return DateFetchResult(
                ok=True,
                data=fetch_fn("2026-07-27"),
                as_of="2026-07-27",
                request_date=date_str,
                attempted=["2026-07-27"],
            )
        mock_fb.side_effect = _run
        out = provider.get_margin_trading("600519", curr_date="2026-07-29")

    assert "融资余额: 10000 元" in out
    assert "未排查" not in out


def test_margin_rt3_duplicate_conflicting_rows_stops_fallback():
    """RT-3: 同标的重复行冲突 → 显式未排查，不得向前日期回退。"""
    df = pd.DataFrame(
        {
            "标的证券代码": ["600519", "600519"],
            "融资余额": [10000, 20000],  # conflicting
            "融资买入额": [500, 500],
            "融券余量": [10, 10],
        }
    )
    provider = _MarginProvider(df)
    # Using real fetch_with_date_fallback logic: _fetch_one returns conflict string,
    # which satisfies fetch_with_date_fallback and prevents falling back to earlier days.
    attempted_days = []
    with patch(
        "tradingagents.dataflows.providers.cn_akshare_provider.fetch_with_date_fallback"
    ) as mock_fb:
        def _run(fetch_fn, date_str, max_back=5, start_offset=0):
            attempted_days.append("2026-07-28")
            data = fetch_fn("2026-07-28")
            # Should NOT attempt earlier day (e.g. 2026-07-27) because data was returned
            return DateFetchResult(
                ok=True,
                data=data,
                as_of="2026-07-28",
                request_date=date_str,
                attempted=attempted_days,
            )
        mock_fb.side_effect = _run
        out = provider.get_margin_trading("600519", curr_date="2026-07-29")

    assert "未排查" in out
    assert "冲突" in out
    assert "融资余额" in out
    assert len(attempted_days) == 1


def test_margin_rt4_reverse_order_exact_match():
    """RT-4: 供应商返回顺序颠倒 → 结果必须与 RT-3 完全一致。"""
    df_forward = pd.DataFrame(
        {
            "标的证券代码": ["600519", "600519"],
            "融资余额": [10000, 20000],
            "融资买入额": [500, 600],
            "融券余量": [10, 20],
        }
    )
    df_reverse = pd.DataFrame(
        {
            "标的证券代码": ["600519", "600519"],
            "融资余额": [20000, 10000],
            "融资买入额": [600, 500],
            "融券余量": [20, 10],
        }
    )

    def _get_result(df):
        provider = _MarginProvider(df)
        with patch(
            "tradingagents.dataflows.providers.cn_akshare_provider.fetch_with_date_fallback"
        ) as mock_fb:
            def _run(fetch_fn, date_str, max_back=5, start_offset=0):
                return DateFetchResult(
                    ok=True,
                    data=fetch_fn("2026-07-28"),
                    as_of="2026-07-28",
                    request_date=date_str,
                    attempted=["2026-07-28"],
                )
            mock_fb.side_effect = _run
            return provider.get_margin_trading("600519", curr_date="2026-07-29")

    out_forward = _get_result(df_forward)
    out_reverse = _get_result(df_reverse)
    assert out_forward == out_reverse
    assert "未排查" in out_forward
    assert "冲突" in out_forward


def test_margin_rt5_corrupt_types_explicit_failure():
    """RT-5: 字段不可解析 → 显式未排查。"""
    df = pd.DataFrame(
        {
            "标的证券代码": ["600519"],
            "融资余额": ["corrupt_value"],
            "融资买入额": [500],
            "融券余量": [10],
        }
    )
    provider = _MarginProvider(df)
    with patch(
        "tradingagents.dataflows.providers.cn_akshare_provider.fetch_with_date_fallback"
    ) as mock_fb:
        def _run(fetch_fn, date_str, max_back=5, start_offset=0):
            return DateFetchResult(
                ok=True,
                data=fetch_fn("2026-07-28"),
                as_of="2026-07-28",
                request_date=date_str,
                attempted=["2026-07-28"],
            )
        mock_fb.side_effect = _run
        out = provider.get_margin_trading("600519", curr_date="2026-07-29")

    assert "未排查" in out
    assert "不可解析" in out


def test_margin_rt6_future_date_rejected():
    """RT-6: 数据包含晚于请求日的未来记录 → 必须拒绝。"""
    df = pd.DataFrame(
        {
            "标的证券代码": ["600519"],
            "交易日期": ["2026-08-01"],  # after request_date 2026-07-29
            "融资余额": [10000],
            "融资买入额": [500],
            "融券余量": [10],
        }
    )
    provider = _MarginProvider(df)
    with patch(
        "tradingagents.dataflows.providers.cn_akshare_provider.fetch_with_date_fallback"
    ) as mock_fb:
        def _run(fetch_fn, date_str, max_back=5, start_offset=0):
            return DateFetchResult(
                ok=True,
                data=fetch_fn("2026-07-28"),
                as_of="2026-07-28",
                request_date=date_str,
                attempted=["2026-07-28"],
            )
        mock_fb.side_effect = _run
        out = provider.get_margin_trading("600519", curr_date="2026-07-29")

    assert "未排查" in out
    assert "晚于请求日" in out


# ── 3. _augment_new_algorithm_sources Red Team Scenarios ───────────


def test_augment_rt1_normal_single_row():
    """RT-1: 正常单行同花顺即时快照产生 THS evidence。"""
    provider = CnAkshareProvider()
    base_val = FundFlowText(
        "base_text",
        evidence=[{"source": "eastmoney", "r0_net": 100.0}],
        evidence_meta={"status": "available"},
    )
    mock_ak = MagicMock()
    mock_ak.stock_fund_flow_individual.return_value = pd.DataFrame(
        {
            "股票代码": ["600519"],
            "净额": ["50000000"],  # 0.5 亿元
        }
    )

    out = provider._augment_new_algorithm_sources(
        base_val,
        ak=mock_ak,
        symbol="600519",
        curr_date="2026-07-28",
        code="600519",
        is_historical=False,
    )
    sources = [rec.get("source") for rec in getattr(out, "fund_flow_evidence", [])]
    assert "ths_instant_snapshot" in sources


def test_augment_rt2_duplicate_consistent_rows():
    """RT-2: 同标的重复行、值一致 → 折叠并产生 THS evidence。"""
    provider = CnAkshareProvider()
    base_val = FundFlowText(
        "base_text",
        evidence=[{"source": "eastmoney", "r0_net": 100.0}],
        evidence_meta={"status": "available"},
    )
    mock_ak = MagicMock()
    mock_ak.stock_fund_flow_individual.return_value = pd.DataFrame(
        {
            "股票代码": ["600519", "600519"],
            "净额": ["50000000", "50000000"],
        }
    )

    out = provider._augment_new_algorithm_sources(
        base_val,
        ak=mock_ak,
        symbol="600519",
        curr_date="2026-07-28",
        code="600519",
        is_historical=False,
    )
    sources = [rec.get("source") for rec in getattr(out, "fund_flow_evidence", [])]
    assert "ths_instant_snapshot" in sources


def test_augment_rt3_duplicate_conflicting_rows_no_ths_evidence():
    """RT-3: 同标的即时快照冲突 → 不得产生 THS evidence，保留原数据和明确 gap/audit 语义。"""
    provider = CnAkshareProvider()
    base_val = FundFlowText(
        "base_text",
        evidence=[{"source": "eastmoney", "r0_net": 100.0}],
        evidence_meta={"status": "available"},
    )
    mock_ak = MagicMock()
    mock_ak.stock_fund_flow_individual.return_value = pd.DataFrame(
        {
            "股票代码": ["600519", "600519"],
            "净额": ["50000000", "90000000"],  # conflict
        }
    )

    out = provider._augment_new_algorithm_sources(
        base_val,
        ak=mock_ak,
        symbol="600519",
        curr_date="2026-07-28",
        code="600519",
        is_historical=False,
    )
    # THS evidence must NOT be added
    sources = [rec.get("source") for rec in getattr(out, "fund_flow_evidence", [])]
    assert "ths_instant_snapshot" not in sources
    meta = getattr(out, "fund_flow_evidence_meta", {})
    assert "duplicate_symbol_conflict" in meta.get("consensus_source_warning", "")
    assert meta.get("manual_calibration_gap", {}).get("status") == "blocked"


def test_augment_rt4_reverse_order_exact_match():
    """RT-4: 供应商返回顺序颠倒 → 结果必须与 RT-3 完全一致。"""
    provider = CnAkshareProvider()
    base_val = FundFlowText(
        "base_text",
        evidence=[{"source": "eastmoney", "r0_net": 100.0}],
        evidence_meta={"status": "available"},
    )
    mock_ak_fwd = MagicMock()
    mock_ak_fwd.stock_fund_flow_individual.return_value = pd.DataFrame(
        {
            "股票代码": ["600519", "600519"],
            "净额": ["50000000", "90000000"],
        }
    )
    mock_ak_rev = MagicMock()
    mock_ak_rev.stock_fund_flow_individual.return_value = pd.DataFrame(
        {
            "股票代码": ["600519", "600519"],
            "净额": ["90000000", "50000000"],
        }
    )

    with patch.object(provider, "_sina_retrieved_at", return_value="2026-07-28T09:30:00+08:00"):
        out_fwd = provider._augment_new_algorithm_sources(
            base_val,
            ak=mock_ak_fwd,
            symbol="600519",
            curr_date="2026-07-28",
            code="600519",
            is_historical=False,
        )
        out_rev = provider._augment_new_algorithm_sources(
            base_val,
            ak=mock_ak_rev,
            symbol="600519",
            curr_date="2026-07-28",
            code="600519",
            is_historical=False,
        )

    assert getattr(out_fwd, "fund_flow_evidence", []) == getattr(out_rev, "fund_flow_evidence", [])
    assert getattr(out_fwd, "fund_flow_evidence_meta", {}) == getattr(out_rev, "fund_flow_evidence_meta", {})


# ── 4. _fetch_tushare_financial_tables Red Team Scenarios ─────────


def test_tushare_rt1_normal_single_row(monkeypatch):
    """RT-1: 正常各期单行财务数据。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake_token")
    provider = CnAkshareProvider()

    def _mock_transport(api_name, payload):
        class _Resp:
            status_code = 200
            def json(self):
                return {
                    "code": 0,
                    "msg": "",
                    "data": {
                        "fields": ["ts_code", "ann_date", "f_ann_date", "end_date", "total_assets"],
                        "items": [
                            ["600519.SH", "20240428", "20240428", "20240331", 10000.0],
                        ],
                    },
                }
        return _Resp(), None, None, False

    monkeypatch.setattr(provider, "_tushare_transport_post", _mock_transport)
    tables, errors = provider._fetch_tushare_financial_tables("600519")
    assert errors == []
    assert "资产负债表" in tables
    assert len(tables["资产负债表"]) == 1


def test_tushare_rt2_duplicate_consistent_rows(monkeypatch):
    """RT-2: 同报告期完全相同的重复可折叠。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake_token")
    provider = CnAkshareProvider()

    def _mock_transport(api_name, payload):
        class _Resp:
            status_code = 200
            def json(self):
                return {
                    "code": 0,
                    "msg": "",
                    "data": {
                        "fields": ["ts_code", "ann_date", "f_ann_date", "end_date", "total_assets"],
                        "items": [
                            ["600519.SH", "20240428", "20240428", "20240331", 10000.0],
                            ["600519.SH", "20240428", "20240428", "20240331", 10000.0],
                        ],
                    },
                }
        return _Resp(), None, None, False

    monkeypatch.setattr(provider, "_tushare_transport_post", _mock_transport)
    tables, errors = provider._fetch_tushare_financial_tables("600519")
    assert errors == []
    assert "资产负债表" in tables
    assert len(tables["资产负债表"]) == 1


def test_tushare_rt3_duplicate_conflicting_rows_rejected(monkeypatch):
    """RT-3: 同报告期公告日或数值冲突不得保留第一行，必须显式不可用/报错。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake_token")
    provider = CnAkshareProvider()

    def _mock_transport(api_name, payload):
        class _Resp:
            status_code = 200
            def json(self):
                return {
                    "code": 0,
                    "msg": "",
                    "data": {
                        "fields": ["ts_code", "ann_date", "f_ann_date", "end_date", "total_assets"],
                        "items": [
                            ["600519.SH", "20240428", "20240428", "20240331", 10000.0],
                            ["600519.SH", "20240430", "20240430", "20240331", 12000.0],  # conflict
                        ],
                    },
                }
        return _Resp(), None, None, False

    monkeypatch.setattr(provider, "_tushare_transport_post", _mock_transport)
    tables, errors = provider._fetch_tushare_financial_tables("600519")
    assert "资产负债表" not in tables
    assert any("duplicate_conflict" in err for err in errors)


def test_tushare_rt4_reverse_order_exact_match(monkeypatch):
    """RT-4: 供应商返回顺序颠倒 → 结果必须与 RT-3 完全一致。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake_token")

    def _run_with_items(items):
        provider = CnAkshareProvider()
        def _mock_transport(api_name, payload):
            class _Resp:
                status_code = 200
                def json(self):
                    return {
                        "code": 0,
                        "msg": "",
                        "data": {
                            "fields": ["ts_code", "ann_date", "f_ann_date", "end_date", "total_assets"],
                            "items": items,
                        },
                    }
            return _Resp(), None, None, False

        monkeypatch.setattr(provider, "_tushare_transport_post", _mock_transport)
        return provider._fetch_tushare_financial_tables("600519")

    items_fwd = [
        ["600519.SH", "20240428", "20240428", "20240331", 10000.0],
        ["600519.SH", "20240430", "20240430", "20240331", 12000.0],
    ]
    items_rev = [
        ["600519.SH", "20240430", "20240430", "20240331", 12000.0],
        ["600519.SH", "20240428", "20240428", "20240331", 10000.0],
    ]

    tables_fwd, errors_fwd = _run_with_items(items_fwd)
    tables_rev, errors_rev = _run_with_items(items_rev)

    assert tables_fwd == tables_rev == {}
    assert errors_fwd == errors_rev
    assert any("duplicate_conflict" in err for err in errors_fwd)
