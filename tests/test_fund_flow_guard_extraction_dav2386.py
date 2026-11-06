"""DAV-2386：个股自身数字的数值核验 + N 日累计过渡句终点绑定。

来源：DAV-2381 复审对候选 `28a77573` 的 3 项 🔴 打回，以及总控 11-07 02:36
四条书面裁定（见本卡卡面）。冻结原文与结构化记录逐字取自 DAV-2372 冻结证据
（`tests/fixtures/dav2386_frozen_reports.json`，原文件 sha256 见
`dav2372/SHA256SUMS.txt`），因此这里的「原地篡改」改的是报告里真实存在的
数字，不是另造一句注入断言。

统一目标（裁定 2 收紧口径）：
10 份误报 = 10 matched / 0 not_checked / 0 unverifiable / 0 仍弃权；
海康威视 `5336af4a` = mismatch 且 blocked=True。
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    _extract_model_mentions,
    _model_bound,
    validate_model_summary,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dav2386_frozen_reports.json"
FROZEN = {
    case["report_id"]: case
    for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
}

SOURCE = "eastmoney_direct"

# 卡面「复验口径 4」的 11 份逐份口径表（report_id → 放行路径）。
EXPECTED_PATHS = {
    "0453ef40bf294cbe8eca285c2bd3556b": "matched",   # 600309.SH 万华化学
    "07441c59a9fe40ab8b119587377d172e": "matched",   # 600887.SH 伊利股份
    "191dc4d1f5144dc083efc087a10db924": "matched",   # 688981.SH 中芯国际
    "3c2e86052c0140698b5c16ed04fec250": "matched",   # 601919.SH 中远海控
    "3d3d63b1d11f4994982827553ef2538a": "matched",   # 600036.SH 招商银行
    "411c1ab1ee1748ec8da36c018657efcc": "matched",   # 601628.SH 中国人寿
    "4cddedfceef04d9a98c05882409fc78e": "matched",   # 300750.SZ 宁德时代
    "5336af4a26254e248e8d2dce08aab5d8": "mismatch",  # 002415.SZ 海康威视（真错）
    "7c403cdbf42d482985860cdebaf23888": "matched",   # 600196.SH 复星医药
    "f12a116713854f67ace10559aa8c1527": "matched",   # 000651.SZ 格力电器
    "f5f9e4e019784d648c4a1eb40129122d": "matched",   # 601888.SH 中国中免
}


def replay(report_id, text=None):
    case = FROZEN[report_id]
    selection = case["selection"]
    return validate_model_summary(
        case["records"],
        case["smart_money_text"] if text is None else text,
        window_days=selection["selected_window_days"],
        selected_field=selection["selected_field"],
        selected_source=selection["selected_source"],
        requested_as_of=case["trade_date"],
        symbol=case["symbol"],
    )


def _records(symbol, rows, source=SOURCE):
    return [
        {
            "source": source,
            "symbol": symbol,
            "date": date,
            "status": "available",
            "period_kind": "historical_daily",
            "time_window": "1d",
            "field": "r0_net",
            "unit": "亿元",
            "r0_net": value,
        }
        for date, value in rows
    ]


def check(text, rows, *, symbol="601888.SH", as_of="2026-11-03", window_days=1,
          source=SOURCE, records=None):
    return validate_model_summary(
        records if records is not None else _records(symbol, rows, source),
        text,
        window_days=window_days,
        selected_field="r0_net",
        selected_source=source,
        requested_as_of=as_of,
        symbol=symbol,
    )


# ---------------------------------------------------------------------------
# 裁定 1/2：11 份冻结报告的放行路径（X/Y/Z/W 表中的 X 与 W）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("report_id, expected", sorted(EXPECTED_PATHS.items()))
def test_frozen_report_reaches_required_path(report_id, expected):
    result = replay(report_id)
    assert result["status"] == expected, (report_id, result["mismatches"],
                                          result["unverifiable_fields"])
    assert result["hard_guard"]["blocked"] is (expected == "mismatch")


def test_rollup_is_ten_matched_one_true_error():
    results = {report_id: replay(report_id) for report_id in EXPECTED_PATHS}
    statuses = {report_id: result["status"] for report_id, result in results.items()}
    # X = 数值核验通过，Y = not_checked，Z = unverifiable，W = 仍弃权
    x = [rid for rid, status in statuses.items() if status == "matched"]
    yzw = [
        rid for rid, status in statuses.items()
        if status in {"not_checked", "unverifiable", "blocked"}
    ]
    assert len(x) == 10
    assert yzw == []
    assert statuses["5336af4a26254e248e8d2dce08aab5d8"] == "mismatch"
    assert [m["structured"] for m in results["5336af4a26254e248e8d2dce08aab5d8"]["mismatches"]] == [
        "-0.01458144"
    ]


def test_every_frozen_report_with_a_matched_path_verifies_its_own_r0_net():
    # 「not_checked」/「unverifiable」只在没有可比数据时才允许出现；这 10 份
    # 原文都写了个股自身的 r0_net，冻结证据也都有对应结构化值。
    for report_id in EXPECTED_PATHS:
        if EXPECTED_PATHS[report_id] != "matched":
            continue
        result = replay(report_id)
        assert result["status"] == "matched", report_id
        assert "r0_net" not in result["unverifiable_fields"], report_id


# ---------------------------------------------------------------------------
# 裁定 1/2 红队：原地篡改冻结原文里的个股自身数字 → 必须拦
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("report_id, own_text, corrupt_text, structured", [
    # 3c2e8605 中远海控：小标题/汇总表行 6 处 +0.2369 亿元
    ("3c2e86052c0140698b5c16ed04fec250", "+0.2369 亿元", "+9.99 亿元", "0.2369"),
    # 0453ef40 万华化学：小标题 `主力净流入额（r0_net）：$-0.5733$ 亿元`
    ("0453ef40bf294cbe8eca285c2bd3556b", "$-0.5733$ 亿元", "$-9.99$ 亿元", "-0.573255"),
    # 191dc4d1 中芯国际：`r0_net = +4.54 亿元` / `2026-11-03：+4.54 亿元`
    ("191dc4d1f5144dc083efc087a10db924", "+4.54 亿元", "+9.99 亿元", "4.5379488"),
    # 7c403cdb 复星医药：`单日主力净额（r0_net）：-898.88 万元（-0.0899 亿元）`
    ("7c403cdbf42d482985860cdebaf23888", "-0.0899 亿元", "-9.99 亿元", "-0.08988759"),
])
def test_in_place_corruption_of_own_stock_number_is_blocked(
    report_id, own_text, corrupt_text, structured,
):
    case = FROZEN[report_id]
    assert own_text in case["smart_money_text"], (report_id, own_text)
    clean = replay(report_id)
    assert clean["status"] == "matched", (report_id, clean["mismatches"])
    assert clean["mismatches"] == []

    corrupted = replay(report_id, case["smart_money_text"].replace(own_text, corrupt_text))
    assert corrupted["status"] == "mismatch", (report_id, corrupted["unverifiable_fields"])
    assert corrupted["hard_guard"]["blocked"] is True
    assert corrupted["mismatches"], report_id
    assert corrupted["mismatches"][0]["structured"] == structured


def test_prior_date_series_rows_of_own_stock_are_verified_too():
    # 0453ef40 的 5 日序列行（10-22…10-27）由第二个来源承接；「只要有结构化
    # 数据可比仍必须核验」，所以把其中一天改错也必须拦。
    case = FROZEN["0453ef40bf294cbe8eca285c2bd3556b"]
    assert "10-23：$-0.5488$ 亿元" in case["smart_money_text"]
    corrupted = replay(
        "0453ef40bf294cbe8eca285c2bd3556b",
        case["smart_money_text"].replace("10-23：$-0.5488$ 亿元", "10-23：$-9.99$ 亿元"),
    )
    assert corrupted["status"] == "mismatch"
    assert corrupted["hard_guard"]["blocked"] is True
    assert any(
        m["kind"] == "dated" and m["date"] == "2026-10-23" and m["structured"] == "-0.5488302"
        for m in corrupted["mismatches"]
    ), corrupted["mismatches"]


# ---------------------------------------------------------------------------
# 裁定 2 抽取面：汇总表行 / 小标题 `r0_net` 标签行 / 带日期序列行
# ---------------------------------------------------------------------------

def test_summary_row_and_heading_bind_own_net_as_daily():
    text = "\n".join([
        # 3c2e8605 冻结原文第 11 行（小标题）
        "- **主力净额合计（超大单+大单代理口径）**：+0.2369 亿元（+2,369.00 万元）。",
        # 3c2e8605 冻结原文第 115-117 行（汇总表）
        "| 资金与微观维度 | 2026-10-29 数据表现 | 可观察信号定性 (candidate) | 置信度 | 交易启示 |",
        "| :--- | :--- | :--- | :--- | :--- |",
        "| **主力/大单净额** | 主力净流入 +0.2369 亿元 (超大单+0.2108亿, 大单+0.0261亿) "
        "| 潜在逆势大单弱承接候选 (event_candidate) | 中等偏低 (单源口径无交叉印证) "
        "| 资金呈现微量托底，未见激进抢筹，维持谨慎做多 |",
        # 审核员最小复现：单行 `r0_net` 标签行
        "| **当日主力大单** | `r0_net` = +0.2369 亿元 | 观察项 |",
    ])
    mentions = _extract_model_mentions(
        text, requested_as_of="2026-10-29", symbol="601919.SH",
    )
    own = [m for m in mentions if m["field"] == "r0_net"]
    assert own, mentions
    assert {m["kind"] for m in own} == {"daily"}
    assert {m["value_text"] for m in own} == {"0.2369"}


def test_component_and_breakdown_amounts_are_not_own_net():
    text = "\n".join([
        # 3c2e8605 冻结原文第 117 行（汇总表）：括号里的分单分量不是主力净额
        "| **主力/大单净额** | 主力净流入 +0.2369 亿元 (超大单+0.2108亿, 大单+0.0261亿) "
        "| 潜在逆势大单弱承接候选 (event_candidate) | 中等偏低 (单源口径无交叉印证) "
        "| 资金呈现微量托底，未见激进抢筹，维持谨慎做多 |",
        # 合计值的逐日拆分：括号里的 1/2 亿元不是另一条当期读数
        "两日主力净流入3亿元（9月23日1亿元、9月24日2亿元）。",
        # 板块/行业金额：仍必须排除
        "主力资金大举净流入白酒（+31.89亿元）、保险（+23.72亿元）。",
        "半导体、通信设备、汽车零部件主力净流入分别高达 88.56 亿、76.58 亿、42.65 亿元。",
    ])
    mentions = _extract_model_mentions(
        text, requested_as_of="2026-09-24", symbol="000651.SZ",
    )
    assert [m["value_text"] for m in mentions if m["field"] == "r0_net"] == ["0.2369", "3"], mentions
    # 分单分量/拆分项/板块金额从未作为本标的读数出现
    values = {m["value_text"] for m in mentions}
    assert values.isdisjoint({"0.2108", "0.0261", "1", "2", "31.89", "23.72", "88.56", "76.58"})


def test_own_net_amount_is_not_duplicated_within_one_sentence():
    # 同一条「由 A 缩减至 B」只能产出 previous + transition 两条，B 不能被
    # 再当成一条普通日度读数重复入队。
    mentions = _extract_model_mentions(
        "11 月 3 日主力净流入规模由 2.26 亿元缩减至 0.59 亿元，买盘意愿降温。",
        requested_as_of="2026-11-03", symbol="601888.SH",
    )
    assert [(m["value_text"], m["kind"]) for m in mentions] == [
        ("2.26", "previous"), ("0.59", "transition"),
    ]


# ---------------------------------------------------------------------------
# 裁定 3：N 日累计过渡句的终点 B 必须绑定窗口比对
# ---------------------------------------------------------------------------

ROWS_5D = [
    ("2026-10-28", "-0.50"), ("2026-10-29", "-0.30"), ("2026-10-30", "-0.40"),
    ("2026-11-02", "2.00"), ("2026-11-03", "1.20"),
]
WINDOW_5D = Decimal("2.00")  # -0.50 -0.30 -0.40 + 2.00 + 1.20


def test_n_day_cumulative_transition_binds_endpoint_to_window():
    text = "5 日累计主力净额由 2.00 亿元缩减至 2.00 亿元。"
    result = check(text, ROWS_5D)
    assert result["status"] == "matched"
    assert result["mismatches"] == []


def test_wrong_n_day_cumulative_endpoint_is_blocked_not_dropped():
    text = "5 日累计主力净额由 2.00 亿元缩减至 9.99 亿元。"
    result = check(text, ROWS_5D)
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    mismatch = result["mismatches"][0]
    assert mismatch["kind"] == "interval"
    assert mismatch["window_days"] == 5
    assert Decimal(mismatch["structured"]) == WINDOW_5D


def test_daily_streak_transition_compares_endpoint_at_daily_granularity():
    # 「连续 N 个交易日…由 A 缩减至 B」里的 N 描述连续走势，B 是当日读数。
    ok = check("连续 5 个交易日主力净流入由 2.00 亿元缩减至 1.20 亿元。", ROWS_5D)
    assert ok["status"] == "matched", ok["mismatches"]
    wrong = check("连续 5 个交易日主力净流入由 2.00 亿元缩减至 9.99 亿元。", ROWS_5D)
    assert wrong["status"] == "mismatch"
    assert wrong["hard_guard"]["blocked"] is True
    assert wrong["mismatches"][0]["structured"] == "1.2"


def test_bare_cumulative_transition_endpoint_is_still_compared():
    # 无 N 的「累计…由 A 至 B」：B 按调用方累计窗口比对（既有规则），不丢。
    result = check(
        "累计主力净额由 -0.50 亿元缩减至 9.99 亿元。",
        [("2026-11-02", "-0.50"), ("2026-11-03", "-2.50")],
        as_of="2026-11-03", window_days=2,
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True


def test_uncomputable_window_transition_endpoint_stays_unverifiable():
    # 窗口在结构化序列里算不出来 → 按既有规则记 unverifiable，既不放行也不拦。
    result = check(
        "5 日累计主力净额由 2.00 亿元缩减至 9.99 亿元。",
        [("2026-11-03", "1.20")],
    )
    assert result["status"] == "unverifiable"
    assert result["hard_guard"]["blocked"] is False
    assert "r0_net" in result["unverifiable_fields"]


# ---------------------------------------------------------------------------
# 裁定 2 的核对面：选定源缺覆盖时回退到同一份 evidence 的其他来源
# ---------------------------------------------------------------------------

OWN_SERIES_TEXT = "\n".join([
    "   - **多日主力净额趋势（东财口径）**：",
    "     - 10-22：$-0.8049$ 亿元；10-23：$-0.5488$ 亿元；10-26：$+0.1491$ 亿元；"
    "10-27：$+1.8271$ 亿元；10-28：$-0.5733$ 亿元。近 5 日累计主力净额为 $+0.0493$ 亿元。",
])


def test_other_source_covers_prior_dates_and_window():
    # 选定源只有 as-of 一天；同一份 evidence 的另一来源完整覆盖 5 日序列。
    records = _records("600309.SH", [("2026-10-28", "-0.573255")]) + _records(
        "600309.SH",
        [("2026-10-22", "-0.80486329"), ("2026-10-23", "-0.5488302"),
         ("2026-10-26", "0.14913763"), ("2026-10-27", "1.8271048"),
         ("2026-10-28", "-0.5732549")],
        source="eastmoney_individual_fund_flow",
    )
    result = check(
        OWN_SERIES_TEXT, [], symbol="600309.SH", as_of="2026-10-28",
        records=records,
    )
    assert result["status"] == "matched", (result["mismatches"],
                                           result["unverifiable_fields"])
    assert "r0_net" not in result["unverifiable_fields"]

    corrupted = OWN_SERIES_TEXT.replace("10-27：$+1.8271$ 亿元", "10-27：$+9.99$ 亿元")
    wrong = check(corrupted, [], symbol="600309.SH", as_of="2026-10-28", records=records)
    assert wrong["status"] == "mismatch"
    assert wrong["hard_guard"]["blocked"] is True


def test_disagreeing_sources_stay_unverifiable():
    # 两个非选定来源对同一窗口给出不同值 → 仍弃权，不得挑一个「凑匹配」。
    records = _records("600309.SH", [("2026-10-28", "-0.573255")]) + _records(
        "600309.SH",
        [("2026-10-22", "-0.80"), ("2026-10-23", "-0.55"), ("2026-10-26", "0.15"),
         ("2026-10-27", "1.83"), ("2026-10-28", "-0.57")],
        source="eastmoney_individual_fund_flow",
    ) + _records(
        "600309.SH",
        [("2026-10-22", "-0.90"), ("2026-10-23", "-0.55"), ("2026-10-26", "0.15"),
         ("2026-10-27", "1.83"), ("2026-10-28", "-0.57")],
        source="tushare_eastmoney_moneyflow_dc",
    )
    result = check(
        OWN_SERIES_TEXT, [], symbol="600309.SH", as_of="2026-10-28", records=records,
    )
    assert result["status"] == "unverifiable"
    assert result["hard_guard"]["blocked"] is False


# ---------------------------------------------------------------------------
# 裁定 4：`_MODEL_UPPER_BOUND_NEGATED`（范围外改动）显式回归
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wording", ["不超过", "不高于", "不超"])
def test_upper_bound_negated_wording_is_upper_bound(wording):
    assert _model_bound(f"主力净流入{wording}", " 亿元，") == "upper"


@pytest.mark.parametrize("wording", ["不超过", "不高于", "不超"])
def test_upper_bound_negated_wording_accepts_a_smaller_net_outflow(wording):
    # 上界措辞本就允许「实际净流出小于上界」；若被误判成下界（修复前的行为）
    # 这个断言会变成 mismatch/blocked。
    result = check(
        f"今日主力净流出{wording}1.2亿元。",
        [("2026-11-03", "-0.18")],
        as_of="2026-11-03",
    )
    assert result["status"] == "matched", result["mismatches"]
    assert result["hard_guard"]["blocked"] is False
