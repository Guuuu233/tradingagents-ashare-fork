"""DAV-2469: 资金流守卫抽取/归属缺陷修复回归。

来源：总控 2026-11-19 23:45 令。固定快照中的 19 个 b1 单元经 DAV-2467/2468
逐字原文复核均为 `GUARD_FP`（v2 真值表 6 份/12 单元 + 11-09..11-11 的 7 个
历史单元），本文件锁定这些单元不再被拦截。

同时守住：
* P1 板块/行业/并列主体金额不得当作个股净额；
* P2 触发条件/阈值金额不得当作实际读数；
* P3b 金额后的「合计」不得把单日值改判为累计；
* P3c 逐日序列按项序对齐交易日，首项不是区间合计；
* P4a 同句多窗口按紧邻金额的显式窗口绑定；
* P4b 「从 A 递减至 B，再到 C」与前文日期序列共用日期；
* P5 主体/窗口不唯一时 `unverifiable`，不触发下游阻断；
* 四类人造真错与 DAV-2452 的 3 份历史真错必须继续被拦截。
"""

import json
from pathlib import Path

import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    _extract_model_mentions,
    validate_model_summary,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dav2469_guard_fp_reports.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
BY_ID = {case["report_id"]: case for case in CASES}

DAV2388_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "dav2388_frozen_reports.json"
)
DAV2388 = {
    case["report_id"]: case
    for case in json.loads(DAV2388_FIXTURE.read_text(encoding="utf-8"))["cases"]
}

HORIZONS = ("short_term", "medium_term")


def replay(case, *, text=None):
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


def values(case):
    return {mention["value_text"] for mention in extract(case)}


def full_id(prefix):
    matches = [report_id for report_id in BY_ID if report_id.startswith(prefix)]
    assert len(matches) == 1, (prefix, matches)
    return matches[0]


def find(case, value_text):
    found = [
        mention for mention in extract(case)
        if mention["value_text"] == value_text
    ]
    assert found, (value_text, [m["value_text"] for m in extract(case)])
    return found


def _records(values_by_date, *, source="eastmoney_direct", symbol="600519.SH"):
    records = []
    for date, value in values_by_date:
        records.append({
            "source": source,
            "source_family": "eastmoney",
            "algorithm_group": "new_algorithm_group",
            "field": "r0_net",
            "value": value,
            "unit": "亿元",
            "date": date,
            "as_of": date,
            "requested_as_of": date,
            "symbol": symbol,
            "status": "available",
            "period_kind": "historical_daily",
            "time_window": "1d",
        })
    return records


def _validate(text, records, *, as_of, symbol="600519.SH"):
    return validate_model_summary(
        records,
        text,
        window_days=1,
        selected_field="r0_net",
        selected_source="eastmoney_direct",
        requested_as_of=as_of,
        symbol=symbol,
    )


# ---------------------------------------------------------------------------
# 19 个 GUARD_FP 单元：不得再被拦截
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", sorted(BY_ID))
def test_guard_fp_report_is_not_blocked(prefix):
    result = replay(BY_ID[prefix])
    assert result["hard_guard"]["blocked"] is False, (prefix, result["mismatches"])
    assert result["status"] == "matched", (prefix, result["status"],
                                           result["mismatches"],
                                           result["unverifiable_fields"])


def test_guard_fp_rollup_is_zero_blocked():
    blocked = {prefix: replay(BY_ID[prefix])["hard_guard"]["blocked"]
               for prefix in BY_ID}
    assert not any(blocked.values()), blocked
    statuses = {prefix: replay(BY_ID[prefix])["status"] for prefix in BY_ID}
    assert set(statuses.values()) == {"matched"}, statuses


# ---------------------------------------------------------------------------
# P1：板块/行业/并列主体金额不参与个股校验
# ---------------------------------------------------------------------------

def test_p1_sector_amounts_are_not_extracted_as_stock_values():
    assert "46.78" not in values(BY_ID[full_id("ccd1b9bb")])
    assert "44.26" not in values(BY_ID[full_id("251e36a7")])
    assert "6.49" not in values(BY_ID[full_id("9c6c0e6d")])
    assert "5.97" not in values(BY_ID[full_id("9c6c0e6d")])
    assert "5.48" not in values(BY_ID[full_id("9c6c0e6d")])


def test_p1_named_owner_list_keeps_stock_value_when_target_is_named():
    """「工行 -1.91 亿元、招行 -4.55 亿元」里的工行是本标的，不能被板块规则吞掉。"""
    records = _records([
        ("2026-10-08", "-0.4501"),
        ("2026-10-09", "-2.068"),
    ], symbol="601398.SH")
    text = "10月9日银行板块普遍录得主力流出（工行 -1.91 亿元、招行 -4.55 亿元）。"
    result = _validate(text, records, as_of="2026-10-09", symbol="601398.SH")
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["model"] == "-1.91"
    assert result["mismatches"][0]["structured"] == "-2.068"


# ---------------------------------------------------------------------------
# P2：触发条件/阈值金额不是实际读数
# ---------------------------------------------------------------------------

def test_p2_trigger_threshold_is_not_compared_as_actual_value():
    assert "-3.5" not in values(BY_ID[full_id("01867c2a")])


# ---------------------------------------------------------------------------
# P3b：金额后的「合计」不得改判单日值
# ---------------------------------------------------------------------------

def test_p3b_trailing_he_total_does_not_reclassify_daily_value():
    mention = find(BY_ID[full_id("251e36a7")], "-1.12795")[0]
    assert mention["kind"] == "daily", mention
    assert mention["window_days"] is None


# ---------------------------------------------------------------------------
# P3c：逐日序列按项序对齐交易日
# ---------------------------------------------------------------------------

def test_p3c_first_series_item_is_not_the_interval_total():
    case = BY_ID[full_id("c4062870")]
    first = find(case, "-4.0168")[0]
    total = find(case, "-14.5768")[0]
    assert first["kind"] == "series" and first.get("series_index") == 0, first
    assert total["kind"] == "interval" and total["window_days"] == 5, total
    assert replay(case)["status"] == "matched"


def test_p3c_parenthetical_series_binds_each_item_to_its_date():
    case = BY_ID[full_id("251329d6")]
    first = find(case, "3.41")[0]
    second = find(case, "2.76")[0]
    assert (first["kind"], first.get("series_index")) == ("series", 0), first
    assert (second["kind"], second.get("series_index")) == ("series", 1), second
    assert replay(case)["status"] == "matched"


# ---------------------------------------------------------------------------
# P4a / P4b：窗口与日期序列归属
# ---------------------------------------------------------------------------

def test_p4a_nearest_explicit_window_wins():
    mention = find(BY_ID[full_id("0f422b0f")], "1.97")[0]
    assert mention["kind"] == "interval"
    assert mention["window_days"] == 2
    assert mention["window_start"] == "2026-11-09"
    assert mention["date"] == "2026-11-10"


def test_p4b_transition_chain_shares_the_dated_series():
    case = BY_ID[full_id("7b7b3905")]
    opening = find(case, "3.33")[0]
    middle = find(case, "0.99")[0]
    closing = [
        mention for mention in find(case, "0.59")
        if mention["date"] == "2026-11-19"
    ][0]
    assert (opening["kind"], opening["date"]) == ("dated", "2026-11-17"), opening
    assert middle["date"] == "2026-11-18", middle
    assert closing["date"] == "2026-11-19", closing
    assert replay(case)["status"] == "matched"


# ---------------------------------------------------------------------------
# P5：主体/窗口不唯一 → unverifiable，不阻断
# ---------------------------------------------------------------------------

def test_p5_ambiguous_series_is_unverifiable_not_blocked():
    records = _records([
        ("2026-11-13", "-1.0"),
        ("2026-11-16", "+2.0"),
        ("2026-11-17", "+3.0"),
    ])
    text = (
        "近 3 个交易日（2026-11-13 至 2026-11-17）主力净额序列分别为："
        "-1.0 亿元、+2.0 亿元。"
    )
    result = _validate(text, records, as_of="2026-11-17")
    assert result["status"] == "unverifiable", result
    assert result["hard_guard"]["blocked"] is False
    assert result["mismatches"] == []


def test_p5_unverifiable_never_enters_the_downstream_blocking_set():
    root = Path(__file__).resolve().parents[1]
    analyst = (root / "tradingagents/agents/analysts/smart_money_analyst.py").read_text(
        encoding="utf-8"
    )
    conditional = (root / "tradingagents/graph/conditional_logic.py").read_text(
        encoding="utf-8"
    )
    trader = (root / "tradingagents/agents/trader/trader.py").read_text(encoding="utf-8")
    risk = (root / "tradingagents/agents/managers/risk_manager.py").read_text(
        encoding="utf-8"
    )
    assert 'validation.get("status") in {"blocked", "mismatch"}' in analyst
    for source in (conditional, trader, risk):
        assert 'fund_flow_guard.get("blocked") or not fund_flow_guard.get("direction_allowed")' in source


# ---------------------------------------------------------------------------
# 元数据：selected_window_days 与 comparison_window_days 分开记录
# ---------------------------------------------------------------------------

def test_metadata_records_selected_and_comparison_windows():
    records = _records([
        ("2026-11-03", "1.0"),
        ("2026-11-04", "-2.0"),
        ("2026-11-05", "0.5"),
        ("2026-11-06", "1.5"),
        ("2026-11-09", "-0.5"),
    ])
    text = "累计主力净额：-9.99 亿元。"
    result = _validate(text, records, as_of="2026-11-09")
    assert result["selected_window_days"] == 1
    assert result["comparison_window_days"] == 5
    assert result["mismatches"], result
    mismatch = result["mismatches"][0]
    assert mismatch["kind"] == "cumulative"
    assert mismatch["window_days"] == 5
    assert mismatch["selected_window_days"] == 1
    assert mismatch["comparison_window_days"] == 5


# ---------------------------------------------------------------------------
# 四类人造真错必须继续被拦截
# ---------------------------------------------------------------------------

ROWS = [
    ("2026-11-03", "1.00"),
    ("2026-11-04", "-2.00"),
    ("2026-11-05", "0.50"),
    ("2026-11-06", "1.50"),
    ("2026-11-09", "-1.00"),
]


def test_artificial_stock_number_error_is_blocked():
    result = _validate(
        "- **单日主力净额（`r0_net`）**：**-1.23 亿元**（净流出）。",
        _records(ROWS),
        as_of="2026-11-09",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["model"] == "-1.23"


def test_artificial_sign_flip_is_blocked():
    result = _validate(
        "- **单日主力净额（`r0_net`）**：**+1.00 亿元**（净流入）。",
        _records(ROWS),
        as_of="2026-11-09",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True


def test_artificial_interval_sum_error_is_blocked():
    result = _validate(
        "- **5日累计主力净额**：**-9.99 亿元**。",
        _records(ROWS),
        as_of="2026-11-09",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["kind"] == "interval"
    assert result["mismatches"][0]["window_days"] == 5


def test_artificial_date_mismatch_is_blocked():
    result = _validate(
        "2026-11-06 单日主力净额（`r0_net`）为 **-2.00 亿元**。",
        _records(ROWS),
        as_of="2026-11-09",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["date"] == "2026-11-06"


# ---------------------------------------------------------------------------
# 返修 🟡-1：单主体括号内的个股真错不得被板块主体规则吞掉
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "modifier",
    ["连续三日", "日内", "全天", "盘中", "实际", "收盘", "同期", "当前"],
)
def test_parenthetical_stock_true_error_is_blocked(modifier):
    result = _validate(
        f"- **主力净额（`r0_net`）**：（{modifier}主力净流出 -9.99 亿元）。",
        _records(ROWS),
        as_of="2026-11-09",
    )
    assert result["status"] == "mismatch", (modifier, result["status"])
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["model"] == "-9.99"


# ---------------------------------------------------------------------------
# 返修 🟡-2：多段逐日序列的 dedupe 不能因 series_index 重计而碰撞
# ---------------------------------------------------------------------------

def test_multi_stage_series_does_not_dedupe_away_the_second_stage():
    records = _records([
        ("2026-11-03", "1.0"),
        ("2026-11-04", "1.0"),
        ("2026-11-05", "-1.0"),
        ("2026-11-06", "1.0"),
    ])
    text = (
        "阶段一（11-03 至 11-04）主力净流出（+1.0 亿元、+1.0 亿元）；"
        "阶段二（11-05 至 11-06）主力净流出（-9.99 亿元、+1.0 亿元）。"
    )
    result = _validate(text, records, as_of="2026-11-06")
    assert result["status"] == "mismatch", result
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["model"] == "-9.99"
    assert result["mismatches"][0]["date"] == "2026-11-05"


# ---------------------------------------------------------------------------
# 返修 🟡-3：章节标题含「预警条件」不得把整节实际读数变成条件值
# ---------------------------------------------------------------------------

def test_heading_with_warning_word_does_not_condition_the_section():
    text = (
        "### 1. 主力资金实况与预警条件\n"
        "- 今日单日主力净额（`r0_net`）：-9.99 亿元。"
    )
    result = _validate(text, _records(ROWS), as_of="2026-11-09")
    assert result["status"] == "mismatch", result
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["model"] == "-9.99"


# ---------------------------------------------------------------------------
# DAV-2452 的 3 份历史真错必须继续被拦截
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["5336af4a", "075b4c14", "74465dad"])
def test_dav2452_true_errors_are_still_blocked(prefix):
    matches = [rid for rid in DAV2388 if rid.startswith(prefix)]
    assert matches, prefix
    case = DAV2388[matches[0]]
    selection = case["selection"]
    result = validate_model_summary(
        case["records"],
        case["smart_money_text"],
        window_days=int(selection.get("selected_window_days") or 1),
        selected_field=selection.get("selected_field"),
        selected_source=selection.get("selected_source"),
        requested_as_of=case["trade_date"],
        symbol=case["symbol"],
    )
    assert result["status"] == "mismatch", (prefix, result["status"])
    assert result["hard_guard"]["blocked"] is True
