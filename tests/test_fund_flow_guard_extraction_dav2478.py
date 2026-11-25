"""DAV-2478：资金流窗口绑定三类误报修复回归。

固定快照 2026-11-23 的 4 份报告 / 8 个单元被 `fund_flow_consensus_guard`
误拦，三类共同点都是「模型写的是另一个时间窗口的合计，数值正确，守卫绑错
窗口」：

1. 显式日期区间（「11-20 至 11-23 合计」「11-17至11-20累计」）；
2. 「此前连续 N 个交易日」窗口截止 as-of 前一交易日；
3. 「N 日累计从 X 回升/降至 Y」的 X = 同一累计窗口去掉 as-of 当日。

同时锁定：无法确定/缺记录的显式区间维持拦截；DAV-2388 的 5 份已知真错
（含 79275169 的「（09-30至10-09）…（合计 +0.6089）」跨括号误绑）继续被拦。
"""

import json
from pathlib import Path

import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    _extract_model_mentions,
    validate_model_summary,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dav2478_guard_fp_reports.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
BY_SYMBOL = {case["symbol"]: case for case in CASES}
HORIZONS = ("short_term", "medium_term")

SIX_DIGIT = "601899.SH"


def replay(case, text=None):
    selection = case["selection"]
    return validate_model_summary(
        selection["records"],
        case["smart_money_text"] if text is None else text,
        window_days=int(selection.get("selected_window_days") or 1),
        selected_field=selection.get("selected_field"),
        selected_source=selection.get("selected_source"),
        requested_as_of=case["trade_date"],
        symbol=case["symbol"],
    )


def extract(case):
    return _extract_model_mentions(
        case["smart_money_text"],
        requested_as_of=case["trade_date"],
        symbol=case["symbol"],
    )


def find(case, value_text):
    found = [m for m in extract(case) if m["value_text"] == value_text]
    assert found, (value_text, [m["value_text"] for m in extract(case)])
    return found


def _records(symbol, rows, source="eastmoney_direct"):
    return [
        {
            "source": source,
            "symbol": symbol,
            "date": date,
            "status": "available",
            "period_kind": "historical_daily",
            "time_window": "1d",
            "field": "r0_net",
            "value": value,
            "unit": "亿元",
        }
        for date, value in rows
    ]


def _validate(text, rows, *, as_of="2026-11-23", symbol=SIX_DIGIT):
    return validate_model_summary(
        _records(symbol, rows),
        text,
        window_days=1,
        selected_field="r0_net",
        selected_source="eastmoney_direct",
        requested_as_of=as_of,
        symbol=symbol,
    )


ROWS = [
    ("2026-11-17", "-1.38"),
    ("2026-11-18", "-9.42"),
    ("2026-11-19", "-0.47"),
    ("2026-11-20", "8.20"),
    ("2026-11-23", "3.18"),
]
ROWS_RUNNING = [
    ("2026-11-17", "1.3186"),
    ("2026-11-18", "-0.6049"),
    ("2026-11-19", "-0.0495"),
    ("2026-11-20", "-1.6754"),
    ("2026-11-23", "0.9969"),
]


# ---------------------------------------------------------------------------
# 8 个 GUARD_FP 单元：2026-11-23 四份报告（short + medium）全部放行
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("symbol", sorted(BY_SYMBOL))
@pytest.mark.parametrize("horizon", HORIZONS)
def test_guard_fp_window_binding_units_are_released(symbol, horizon):
    result = replay(BY_SYMBOL[symbol])
    assert result["status"] == "matched", (symbol, horizon, result["mismatches"])
    assert result["hard_guard"]["blocked"] is False


def test_explicit_two_day_range_binds_the_end_date():
    mention = find(BY_SYMBOL["601899.SH"], "11.38")[0]
    assert mention["kind"] == "interval"
    assert mention["window_days"] == 2
    assert mention["window_start"] == "2026-11-20"
    assert mention["date"] == "2026-11-23"
    assert mention["explicit_range"] is True


def test_explicit_four_day_range_binds_the_end_date():
    mention = find(BY_SYMBOL["600030.SH"], "-3.8153")[0]
    assert mention["kind"] == "interval"
    assert mention["window_days"] == 4
    assert mention["window_start"] == "2026-11-17"
    assert mention["date"] == "2026-11-20"
    assert mention["explicit_range"] is True


def test_prior_streak_ends_before_as_of():
    mention = find(BY_SYMBOL["600887.SH"], "-7.34")[0]
    assert mention["kind"] == "interval"
    assert mention["window_days"] == 4
    assert mention["window_end_exclusive"] is True
    assert replay(BY_SYMBOL["600887.SH"])["status"] == "matched"


def test_running_cumulative_previous_value_is_window_minus_as_of():
    mention = find(BY_SYMBOL["603259.SH"], "-1.01")[0]
    assert mention["kind"] == "previous"
    assert mention["previous_window_days"] == 5
    result = replay(BY_SYMBOL["603259.SH"])
    assert result["status"] == "matched"


# ---------------------------------------------------------------------------
# 规则 1：显式日期区间 —— 每类至少 2 个人造真错（区间写对、数字写错）
# ---------------------------------------------------------------------------

def test_rule1_wrong_two_day_range_total_is_blocked():
    result = _validate("11-20 至 11-23 合计主力净流入 9.99 亿元。", ROWS)
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    mismatch = result["mismatches"][0]
    assert mismatch["kind"] == "interval"
    assert mismatch["window_start"] == "2026-11-20"
    assert mismatch["window_end"] == "2026-11-23"


def test_rule1_wrong_four_day_range_total_is_blocked():
    result = _validate(
        "主力资金净流出（11-17至11-20累计流出 9.99 亿元）。", ROWS,
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    mismatch = result["mismatches"][0]
    assert mismatch["kind"] == "interval"
    assert mismatch["window_start"] == "2026-11-17"
    assert mismatch["window_end"] == "2026-11-20"


def test_rule1_missing_trading_day_in_range_is_blocked_not_released():
    rows = [
        ("2026-11-17", "-1.38"),
        ("2026-11-18", "-9.42"),
        ("2026-11-20", "8.20"),
        ("2026-11-23", "3.18"),
    ]
    result = _validate(
        "连续 4 个交易日主力资金净流出（11-17至11-20累计流出 3.07 亿元）。",
        rows,
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["window_start"] == "2026-11-17"
    assert result["mismatches"][0]["window_end"] == "2026-11-20"


# ---------------------------------------------------------------------------
# 规则 2：「此前连续 N 个交易日」窗口止于 as-of 前一交易日
# ---------------------------------------------------------------------------

def test_rule2_wrong_prior_streak_total_is_blocked():
    result = _validate(
        "终结了此前连续 4 个交易日主力净流出（累计流出 9.99 亿元）的态势。",
        ROWS,
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["kind"] == "interval"


def test_rule2_wrong_three_day_prior_streak_total_is_blocked():
    result = _validate(
        "此前连续 3 个交易日主力净流入（合计 9.99 亿元）后转弱。", ROWS,
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["kind"] == "interval"


def test_rule2_correct_prior_streak_total_is_released():
    result = _validate(
        "终结了此前连续 4 个交易日主力净流出（累计流出 3.07 亿元）的态势。",
        ROWS,
    )
    assert result["status"] == "matched"
    assert result["hard_guard"]["blocked"] is False


# ---------------------------------------------------------------------------
# 规则 3：「N 日累计从 X 回升/降至 Y」的 X 是窗口去掉 as-of 当日
# ---------------------------------------------------------------------------

def test_rule3_wrong_running_cumulative_origin_is_blocked():
    result = _validate(
        "近 5 日累计主力净额从 -9.99 亿元回升至 -0.0144 亿元。",
        ROWS_RUNNING, symbol="603259.SH",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    mismatch = result["mismatches"][0]
    assert mismatch["kind"] == "previous"
    assert mismatch["window_days"] == 4
    assert mismatch["window_start"] == "2026-11-17"
    assert mismatch["date"] == "2026-11-20"


def test_rule3_wrong_running_cumulative_endpoint_is_blocked():
    result = _validate(
        "近 5 日累计主力净额从 -1.01 亿元降至 -9.99 亿元。",
        ROWS_RUNNING, symbol="603259.SH",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["model"] == "-9.99"


def test_rule3_correct_running_cumulative_is_released():
    result = _validate(
        "近 5 日累计主力净额从 -1.01 亿元回升至 -0.0144 亿元。",
        ROWS_RUNNING, symbol="603259.SH",
    )
    assert result["status"] == "matched"
    assert result["hard_guard"]["blocked"] is False


# ---------------------------------------------------------------------------
# 已闭合括号里的区间不得跨括号绑定后面的金额（DAV-2388 真错守护）
# ---------------------------------------------------------------------------

def test_closed_parenthetical_range_does_not_scope_a_later_amount():
    rows = [
        ("2026-09-30", "0.1421"),
        ("2026-10-08", "0.1655"),
        ("2026-10-09", "0.3013"),
        ("2026-10-12", "-0.0389"),
        ("2026-10-13", "-0.7387"),
    ]
    result = _validate(
        "在连续三个交易日（09-30至10-09）累计呈现主力净流入"
        "（合计约+0.6089亿元）并推升股价后转弱。",
        rows, as_of="2026-10-13", symbol="601225.SH",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["kind"] == "cumulative"
