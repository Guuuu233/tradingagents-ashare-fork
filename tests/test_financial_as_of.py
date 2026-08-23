"""Tests for data-layer as-of extraction and failure triage (Lane A / DAV-337)."""
from __future__ import annotations

import pytest

from tradingagents.dataflows.interface import (
    _extract_as_of,
    _as_of_refusal,
    route_to_vendor,
)
from tradingagents.dataflows.fund_flow_evidence import FundFlowText
from tradingagents.graph.data_collector import (
    _extract_source_as_of,
    _build_source_provenance,
    _classify_failure_value,
    _compact_failure_reason,
)
from tradingagents.dataflows.trade_calendar import (
    SNAPSHOT_ONLY_REFUSAL,
    snapshot_historical_refusal,
)


# ── Sample Payloads for the 8 Financial/Report Interfaces ────────────────────

SAMPLE_BALANCE_SHEET = """## Balance Sheet (600900.SH)

【财务数据截至 2026Q1】（生效公告日 2026-04-30，分析日 2026-08-21）
（该期首次公告日在数据源中已被后续财报刷新覆盖，此处按法定披露截止日估计，若公司当期实际逾期披露，可见时点可能晚于此估计数日至数周）

|      报告日 |     公告日期 |        货币资金 |        应收账款 |          存货 |
|---------:|---------:|------------:|------------:|------------:|
| 20260331 | 20260430 |  12345678.0 |   5678901.0 |   2345678.0 |
"""

SAMPLE_INCOME_STATEMENT = """## Income Statement (600900.SH)

【财务数据截至 2026Q1】（生效公告日 2026-04-30，分析日 2026-08-21）
（该期首次公告日在数据源中已被后续财报刷新覆盖，此处按法定披露截止日估计，若公司当期实际逾期披露，可见时点可能晚于此估计数日至数周）

|      报告日 |     公告日期 |       营业总收入 |        营业收入 |       营业总成本 |
|---------:|---------:|------------:|------------:|------------:|
| 20260331 | 20260430 |  98765432.0 |  98765432.0 |  54321098.0 |
"""

SAMPLE_CASHFLOW = """## Cashflow (600900.SH)

【财务数据截至 2026Q1】（生效公告日 2026-04-30，分析日 2026-08-21）
（该期首次公告日在数据源中已被后续财报刷新覆盖，此处按法定披露截止日估计，若公司当期实际逾期披露，可见时点可能晚于此估计数日至数周）

|      报告日 |     公告日期 |   销售商品、提供劳务收到的现金 |   经营活动现金流入小计 |
|---------:|---------:|-----------------:|-------------:|
| 20260331 | 20260430 |       45678901.0 |   56789012.0 |
"""

SAMPLE_FUNDAMENTALS = """## Fundamentals for 600900.SH

### Company Profile

【数据获取失败】Company Profile（总市值/PE/个股信息）：该数据源仅提供当前快照，无法用于历史日期分析，本项不可用

### Financial Abstract

【财务数据截至 2026Q1】（生效公告日 2026-04-30，分析日 2026-08-21）
（该期首次公告日在数据源中已被后续财报刷新覆盖，此处按法定披露截止日估计，若公司当期实际逾期披露，可见时点可能晚于此估计数日至数周）

| 选项   | 指标          |     20260331 |     20251231 |
|:-----|:------------|-------------:|-------------:|
| 常用   | 每股收益(元)  |         0.35 |         1.20 |
"""

SAMPLE_EARNINGS_FORECAST_EMPTY = """【业绩预告排查】查询报告期 = 20260630（2026H1）。该标的在本报告期暂无业绩预警/预增公告（查询成功，确认无预告）。"""

SAMPLE_EARNINGS_FORECAST_WITH_DATA = """【业绩预告/快报】查询报告期 = 20260630（2026H1）。找到 1 条预告记录：
- 公告日: 2026-07-15 | 类型: 预增 | 变动: 50%
  原因摘要: 主营业务持续增长"""

SAMPLE_SHAREHOLDER_COUNT = """【股东户数与筹码集中度】最近 4 期户数变动：
- 截止日: 2025-08-30 | 股东户数: 613514 | 较上期变动: 77.276733212551% | 户均市值: 1120287.7776912 元
- 截止日: 2025-09-30 | 股东户数: 601559 | 较上期变动: -1.948610789648% | 户均市值: 1108384.93441375 元
- 截止日: 2025-12-31 | 股东户数: 543860 | 较上期变动: -9.591577883466% | 户均市值: 1223275.91604097 元
- 截止日: 2026-03-31 | 股东户数: 739609 | 较上期变动: 35.992534843526% | 户均市值: 894554.564696536 元"""

SAMPLE_FUND_FLOW_INDIVIDUAL_TEXT = """【备用数据源：新浪历史/收盘数据】600900.SH 近5日主力资金净流向（截至于 2026-08-21，最新数据日 2026-08-21，单位：亿元）：
        日期 净流入额(亿) 主力净流入(亿)     净占比
2026-08-17    0.50     0.01   2.41%
2026-08-18    0.41     1.85   2.07%
2026-08-19   -0.51    -0.61  -1.46%
2026-08-20    0.14    -0.84   0.60%
2026-08-21   -1.83    -1.58 -10.26%
（新浪历史接口未提供超大单/大单/中单/小单明细）"""

SAMPLE_FUND_FLOW_INDIVIDUAL_OBJ = FundFlowText(
    SAMPLE_FUND_FLOW_INDIVIDUAL_TEXT,
    evidence=[
        {"date": "2026-08-17", "r0_net": "0.01", "source": "sina_legacy", "status": "available", "unit": "亿元"},
        {"date": "2026-08-21", "r0_net": "-1.58", "source": "sina_legacy", "status": "available", "unit": "亿元"},
    ],
    evidence_meta={"as_of": "2026-08-21", "actual_as_of": "2026-08-21", "status": "available", "symbol": "600900.SH"},
)

SAMPLE_RESTRICTED_RELEASE_NONE = """【解禁排查】数据基准日：2026-08-21。距当前分析日期前后60日内无限售股解禁记录，无重大解禁冲击风险。"""

SAMPLE_RESTRICTED_RELEASE_WITH_DATA = """【限售解禁风险预警】（数据基准日：2026-08-21）找到 1 条近期解禁记录：
- 解禁日期: 2026-08-15 | 类型: 首发原股东限售股份 | 占比流通市值: 12.5%"""


# ── Parameterized Tests for the 8 Interfaces ──────────────────────────────────

@pytest.mark.parametrize(
    "interface_name, sample_text, requested_as_of, expected_as_of",
    [
        ("balance_sheet", SAMPLE_BALANCE_SHEET, "2026-08-21", "2026-04-30"),
        ("income_statement", SAMPLE_INCOME_STATEMENT, "2026-08-21", "2026-04-30"),
        ("cashflow", SAMPLE_CASHFLOW, "2026-08-21", "2026-04-30"),
        ("fundamentals", SAMPLE_FUNDAMENTALS, "2026-08-21", "2026-04-30"),
        ("earnings_forecast_empty", SAMPLE_EARNINGS_FORECAST_EMPTY, "2026-08-21", "2026-06-30"),
        ("earnings_forecast_data", SAMPLE_EARNINGS_FORECAST_WITH_DATA, "2026-08-21", "2026-07-15"),
        ("shareholder_count", SAMPLE_SHAREHOLDER_COUNT, "2026-08-21", "2026-03-31"),
        ("fund_flow_individual_text", SAMPLE_FUND_FLOW_INDIVIDUAL_TEXT, "2026-08-21", "2026-08-21"),
        ("fund_flow_individual_obj", SAMPLE_FUND_FLOW_INDIVIDUAL_OBJ, "2026-08-21", "2026-08-21"),
        ("restricted_release_none", SAMPLE_RESTRICTED_RELEASE_NONE, "2026-08-21", "2026-08-21"),
        ("restricted_release_data", SAMPLE_RESTRICTED_RELEASE_WITH_DATA, "2026-08-21", "2026-08-21"),
    ],
)
def test_extract_source_as_of_eight_interfaces(
    interface_name: str,
    sample_text: str | FundFlowText,
    requested_as_of: str,
    expected_as_of: str,
):
    """Assert each of the 8 interfaces extracts actual_as_of <= requested_as_of."""
    extracted = _extract_source_as_of(sample_text, requested_as_of)
    assert extracted is not None, f"Failed to extract as_of for {interface_name}"
    assert extracted <= requested_as_of, f"{extracted} exceeds {requested_as_of} for {interface_name}"
    assert extracted == expected_as_of, f"Expected {expected_as_of}, got {extracted} for {interface_name}"


def test_build_source_provenance_eight_interfaces_no_unverified_gap():
    """Verify source provenance builds clean records without '未返回可验证数据日期'."""
    results = {
        "balance_sheet": SAMPLE_BALANCE_SHEET,
        "income_statement": SAMPLE_INCOME_STATEMENT,
        "cashflow": SAMPLE_CASHFLOW,
        "fundamentals": SAMPLE_FUNDAMENTALS,
        "earnings_forecast": SAMPLE_EARNINGS_FORECAST_EMPTY,
        "shareholder_count": SAMPLE_SHAREHOLDER_COUNT,
        "fund_flow_individual": SAMPLE_FUND_FLOW_INDIVIDUAL_OBJ,
        "restricted_release": SAMPLE_RESTRICTED_RELEASE_NONE,
    }
    requested_as_of = "2026-08-21"
    provenance = _build_source_provenance(results, requested_as_of, daily_as_of="2026-08-21")

    for key, expected_as_of in [
        ("balance_sheet", "2026-04-30"),
        ("income_statement", "2026-04-30"),
        ("cashflow", "2026-04-30"),
        ("fundamentals", "2026-04-30"),
        ("earnings_forecast", "2026-06-30"),
        ("shareholder_count", "2026-03-31"),
        ("fund_flow_individual", "2026-08-21"),
        ("restricted_release", "2026-08-21"),
    ]:
        entry = provenance.get(key)
        assert entry is not None, f"Missing provenance for {key}"
        assert entry["actual_as_of"] == expected_as_of, f"Mismatch actual_as_of for {key}"
        assert entry["actual_as_of"] <= requested_as_of
        assert "gap" not in entry, f"Unexpected gap for {key}: {entry.get('gap')}"


# ── Tests for interface.py _extract_as_of ────────────────────────────────────

def test_extract_as_of_financial_statements():
    """Assert _extract_as_of parses kwargs and positional args correctly."""
    # Kwargs
    assert _extract_as_of("get_balance_sheet", (), {"ticker": "600900.SH", "curr_date": "2026-08-21"}) == "2026-08-21"
    assert _extract_as_of("get_income_statement", (), {"ticker": "600900.SH", "curr_date": "2026-08-21"}) == "2026-08-21"
    assert _extract_as_of("get_cashflow", (), {"ticker": "600900.SH", "curr_date": "2026-08-21"}) == "2026-08-21"
    assert _extract_as_of("get_fundamentals", (), {"ticker": "600900.SH", "curr_date": "2026-08-21"}) == "2026-08-21"
    assert _extract_as_of("get_earnings_forecast", (), {"symbol": "600900.SH", "curr_date": "2026-08-21"}) == "2026-08-21"
    assert _extract_as_of("get_shareholder_count", (), {"symbol": "600900.SH", "curr_date": "2026-08-21"}) == "2026-08-21"
    assert _extract_as_of("get_individual_fund_flow", (), {"symbol": "600900.SH", "curr_date": "2026-08-21"}) == "2026-08-21"
    assert _extract_as_of("get_restricted_release", (), {"symbol": "600900.SH", "curr_date": "2026-08-21"}) == "2026-08-21"

    # Positional 3 args: (ticker, freq, curr_date)
    assert _extract_as_of("get_balance_sheet", ("600900.SH", "quarterly", "2026-08-21"), {}) == "2026-08-21"
    # Positional 2 args: (ticker, curr_date)
    assert _extract_as_of("get_balance_sheet", ("600900.SH", "2026-08-21"), {}) == "2026-08-21"


# ── Tests for Failure Triage & Known-Gaps ────────────────────────────────────

def test_classify_and_compact_reason_triage():
    """Verify failure triage correctly distinguishes refused snapshots vs stopped disclosures vs errors."""
    # Northbound flow disclosure stopped
    nb_refusal = "【数据获取失败】北向资金持股变动 — 原因：沪深港通个股每日持股明细自 2024 年 8 月起停止披露，本项不可用。"
    assert _classify_failure_value(nb_refusal) == "unavailable"
    assert _compact_failure_reason("unavailable") == "data source unavailable"

    # Snapshot historical refusal
    snap_refusal = "【数据获取失败】板块资金流向（即时）：" + SNAPSHOT_ONLY_REFUSAL
    assert _classify_failure_value(snap_refusal) == "refused"
    assert _compact_failure_reason("refused") == "data source refused"


# ── Live 3-Ticker Smoke Test for 8 Interfaces ────────────────────────────────

@pytest.mark.parametrize("ticker", ["600900.SH", "000333.SZ", "600276.SH"])
def test_smoke_three_tickers_eight_interfaces_as_of(ticker: str):
    """Smoke test: 600900 / 000333 / 600276 x 8 interfaces -> actual_as_of <= curr_date."""
    curr_date = "2026-08-21"
    methods = [
        ("balance_sheet", "get_balance_sheet", {"ticker": ticker, "freq": "quarterly", "curr_date": curr_date}),
        ("income_statement", "get_income_statement", {"ticker": ticker, "freq": "quarterly", "curr_date": curr_date}),
        ("cashflow", "get_cashflow", {"ticker": ticker, "freq": "quarterly", "curr_date": curr_date}),
        ("fundamentals", "get_fundamentals", {"ticker": ticker, "curr_date": curr_date}),
        ("earnings_forecast", "get_earnings_forecast", {"symbol": ticker, "curr_date": curr_date}),
        ("shareholder_count", "get_shareholder_count", {"symbol": ticker, "curr_date": curr_date}),
        ("fund_flow_individual", "get_individual_fund_flow", {"symbol": ticker, "curr_date": curr_date}),
        ("restricted_release", "get_restricted_release", {"symbol": ticker, "curr_date": curr_date}),
    ]

    results = {}
    for name, method, kwargs in methods:
        res = route_to_vendor(method, **kwargs)
        results[name] = res
        as_of = _extract_source_as_of(res, curr_date)
        assert as_of is not None, f"{ticker} {name} returned None as_of"
        assert as_of <= curr_date, f"{ticker} {name} as_of {as_of} exceeds {curr_date}"

    prov = _build_source_provenance(results, curr_date, daily_as_of=curr_date)
    for name, _m, _kw in methods:
        entry = prov.get(name)
        assert entry is not None, f"{ticker} missing provenance for {name}"
        assert entry.get("actual_as_of") is not None, f"{ticker} null actual_as_of for {name}"
        assert entry.get("actual_as_of") <= curr_date, f"{ticker} actual_as_of > curr_date for {name}"
        gap = entry.get("gap")
        assert not (gap and "未返回可验证数据日期" in gap), f"{ticker} unverified gap for {name}: {gap}"

