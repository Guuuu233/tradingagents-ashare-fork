"""DAV-2412：「连续 N 个交易日」被误当求和窗口的回归修复。

背景：`DAV-2373` 守卫热修（`9ced305a`）覆盖了「连续第 N 个交易日」（带「第」），
未覆盖「连续 N 个交易日」（不带「第」）变体，导致 `aba4b9cf`（600809.SH 山西
汾酒 2026-11-10）short/medium 两档被误拦：

    主力净额（`r0_net`）：+1557.17 万元（+0.16 亿元），终结了此前连续4个
    交易日的净流出状态

`+0.16 亿` 是单日值（records 双源一致 0.15571695），新代码把「连续4个交易日」
读成 window_days=4，拿单日值与四日累计 -2.06008596（11-05..11-10）比对 → 判
mismatch → 拦。`082be047` 旧代码 matched / 不拦 → 回归确认。

修复口径（卡面第三节，精确）：
1. 「连续 N 个交易日」「连续 N 日」+（流入/流出/状态/净流出/净流入）这类描述
   连续天数的说法与金额隔着从句边界时（prefix/suffix 扫描），一律不得当作求和
   窗口（window_days 不得由此类措辞推导）；金额与该措辞同处一个从句时
   （金额本身是连续段谓语的一部分，如 411c1ab1「连续 4 日…净流出超 1.43 亿元」）
   保持原口径（tests/test_fund_flow_guard_extraction_dav2373.py::test_de3_*）。
2. 只有当某个数字在同一个从句里被「合计 / 累计 / 共 / 区间」显式绑定到这段
   时间时，该数字才算窗口值。
3. 不得放宽弃权方向：无法核实的数字仍判 unverifiable 或继续拦截。

冻结原文与结构化记录取自生产库 result_data（tests/fixtures/
dav2412_frozen_reports.json）：aba4b9cf 两档的 records / selection /
`smart_money_report_blocked_original.original_text` 逐字节一致，存一份。
"""

import json
from pathlib import Path

import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    _extract_model_mentions,
    validate_model_summary,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dav2412_frozen_reports.json"
FROZEN = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

SOURCE = "eastmoney_direct"


def replay(report_id_prefix, text=None):
    case = next(c for c in FROZEN if c["report_id"].startswith(report_id_prefix))
    selection = case["selection"]
    return validate_model_summary(
        case["records"],
        case["smart_money_text"] if text is None else text,
        window_days=int(selection.get("selected_window_days") or 1),
        selected_field=selection.get("selected_field"),
        selected_source=selection.get("selected_source"),
        requested_as_of=case["trade_date"],
        symbol=case["symbol"],
    )


def extract(text, *, symbol, as_of):
    return _extract_model_mentions(text, requested_as_of=as_of, symbol=symbol)


def find(mentions, value_text):
    found = [m for m in mentions if m["value_text"] == value_text]
    assert found, (value_text, [m["value_text"] for m in mentions])
    return found


def _records(rows):
    return [
        {
            "source": SOURCE,
            "symbol": "600809.SH",
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


def check(text, rows, *, as_of, window_days=1):
    return validate_model_summary(
        _records(rows),
        text,
        window_days=window_days,
        selected_field="r0_net",
        selected_source=SOURCE,
        requested_as_of=as_of,
        symbol="600809.SH",
    )


ABA4B9CF_ROWS = [
    ("2026-11-05", "-0.90051621"), ("2026-11-06", "-0.86974874"),
    ("2026-11-09", "-0.44553796"), ("2026-11-10", "0.15571695"),
]
# 四日累计（11-05..11-10）= -2.06008596，与生产误拦记录里的 structured 一致。


# ---------------------------------------------------------------------------
# 正例：aba4b9cf 冻结原文必须放行（short、medium 两档均不拦）
# ---------------------------------------------------------------------------

def test_aba4b9cf_frozen_report_is_not_blocked():
    """冻结原文重放：两档共享同一份冻结数据，blocked 必须为 False。"""
    result = replay("aba4b9cf")
    assert result["hard_guard"]["blocked"] is False, result["mismatches"]
    assert result["status"] == "matched", (result["status"], result["mismatches"],
                                           result["unverifiable_fields"])


def test_aba4b9cf_streak_clause_is_not_an_aggregation_window():
    """「终结了此前连续4个交易日的净流出状态」不是 +0.16 亿的 4 日窗口。

    金额与连续段措辞隔着从句边界（「，终结了…」），单日读数必须按 daily
    与当日结构化值（0.15571695）比对，而不是与四日累计比对。
    """
    mentions = extract(
        "- 主力净额（`r0_net`）：**+1557.17 万元**（+0.16 亿元），"
        "终结了此前连续4个交易日的净流出状态，实现单日由负转正。",
        symbol="600809.SH", as_of="2026-11-10",
    )
    mention = find(mentions, "0.16")[0]
    assert mention["kind"] == "daily"
    assert mention["window_days"] is None


def test_aba4b9cf_single_day_value_matches_structured_record():
    """误拦原句作为最小输入：单日 +0.16 亿元与 0.15571695 一致 → 不拦。"""
    result = check(
        "- 主力净额（`r0_net`）：**+1557.17 万元**（+0.16 亿元），"
        "终结了此前连续4个交易日的净流出状态，实现单日由负转正。",
        ABA4B9CF_ROWS,
        as_of="2026-11-10",
    )
    assert result["hard_guard"]["blocked"] is False, result["mismatches"]
    assert result["mismatches"] == [], result["mismatches"]


# ---------------------------------------------------------------------------
# 反例：显式「累计」绑定的连续段数字仍必须校验数值（同从句 → 窗口值）
# ---------------------------------------------------------------------------

ABA4B9CF_CORRUPT_STREAK = "终结了此前连续4个交易日的净流出状态，实现单日由负转正"


def test_streak_with_explicit_cumulative_binding_is_still_a_window():
    """「连续4日主力资金累计净流出9.99亿元」：显式「累计」绑定 → 仍为 4 日窗口值。"""
    mentions = extract(
        "连续4日主力资金累计净流出9.99亿元，今日转为净流入。",
        symbol="600809.SH", as_of="2026-11-10",
    )
    mention = find(mentions, "-9.99")[0]
    assert mention["kind"] == "interval"
    assert mention["window_days"] == 4


def test_streak_with_explicit_cumulative_binding_wrong_value_blocks():
    """反例（卡面第四节 2）：同从句「连续4日累计流出 X 亿」且 X 写错 → 拦。"""
    result = check(
        "连续4日主力资金累计净流出9.99亿元，11月10日转为净流入。",
        ABA4B9CF_ROWS,
        as_of="2026-11-10",
    )
    assert result["status"] == "mismatch", (result["status"], result["mismatches"])
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"], "窗口值写错必须产生 mismatch"
    assert result["mismatches"][0]["structured"] == "-2.06008596", result["mismatches"]


def test_aba4b9cf_in_place_streak_corruption_is_still_blocked():
    """红队：在 aba4b9cf 冻结原文里原地写入「连续4日累计净流出9.99亿元」。

    单日 +0.16 亿仍放行，但显式「累计」绑定的连续段数字写错必须拦截，
    且按真实四日窗口（11-05..11-10 累计 -2.06008596）比对。
    """
    case = FROZEN[0]
    assert ABA4B9CF_CORRUPT_STREAK in case["smart_money_text"]
    clean = replay("aba4b9cf")
    assert clean["hard_guard"]["blocked"] is False, clean["mismatches"]

    corrupted_text = case["smart_money_text"].replace(
        ABA4B9CF_CORRUPT_STREAK,
        "连续4日主力资金累计净流出9.99亿元后转为净流入，实现单日由负转正",
    )
    corrupted = replay("aba4b9cf", corrupted_text)
    assert corrupted["status"] == "mismatch", (corrupted["status"],
                                               corrupted["mismatches"])
    assert corrupted["hard_guard"]["blocked"] is True
    assert corrupted["mismatches"], "连续段累计值写错必须产生 mismatch"
    mismatch = corrupted["mismatches"][0]
    assert mismatch["structured"] == "-2.06008596", mismatch
    assert mismatch["kind"] == "interval" and mismatch["window_days"] == 4, mismatch


# ---------------------------------------------------------------------------
# 弃权方向：无法核实的数字仍 unverifiable / 拦截，不得因本修复改判 matched
# ---------------------------------------------------------------------------

def test_streak_window_without_structured_coverage_stays_unverifiable():
    """「连续6个交易日累计净流出9.99亿元」但结构化只有 4 日 → unverifiable。

    修复只取消跨从句的连续段窗口绑定，不把取不到数据的窗口值放行为 matched。
    """
    result = check(
        "连续6个交易日主力资金累计净流出9.99亿元。",
        ABA4B9CF_ROWS,
        as_of="2026-11-10",
    )
    assert result["hard_guard"]["blocked"] is False
    assert result["status"] == "unverifiable", (result["status"],
                                                result["mismatches"])
    assert "r0_net" in result["unverifiable_fields"], result["unverifiable_fields"]


def test_streak_ordinal_variant_stays_daily():
    """「连续第 N 个交易日」（带「第」，DAV-2388 #1 口径）在跨从句位置仍不建窗。"""
    mentions = extract(
        "- 主力净额（`r0_net`）：-8541.50 万元（-0.85415 亿元），"
        "终结了此前连续第 4 个交易日的净流出态势。",
        symbol="600028.SH", as_of="2026-11-04",
    )
    mention = find(mentions, "-0.85415")[0]
    assert mention["kind"] == "daily"
    assert mention["window_days"] is None


# ---------------------------------------------------------------------------
# 同从句连续段（de3/eebddfdc/3eca48c6 口径）不受本修复影响
# ---------------------------------------------------------------------------

def test_same_clause_streak_predication_keeps_its_window():
    """金额是连续段谓语的一部分（同从句）→ 窗口口径不变（DAV-2373 #3）。"""
    mentions = extract(
        "连续 4 日主力资金分组大幅净流出超 1.43 亿元后，"
        "11 月 2 日单日主力资金转为净流入 1398.07 万元。",
        symbol="601628.SH", as_of="2026-11-02",
    )
    mention = find(mentions, "-1.43")[0]
    assert mention["kind"] == "interval"
    assert mention["window_days"] == 4
    assert mention["window_end_exclusive"] is True


def test_cumulative_bound_paren_after_streak_keeps_its_window():
    """「此前连续 4 日主力大额流出（累计流出逾 10.4 亿元）之后」保持 4 日窗口。

    eebddfdc（600276.SH）口径：括号里的「累计」把金额显式绑定到连续段，
    与金额同从句，窗口与 window_end_exclusive 都不得丢。
    """
    mentions = extract(
        "在此前连续 4 日主力大额流出（累计流出逾 10.4 亿元）之后，"
        "11 月 5 日主力资金指标转为净流入 2.12 亿元。",
        symbol="600276.SH", as_of="2026-11-05",
    )
    mention = find(mentions, "-10.4")[0]
    assert mention["kind"] == "interval"
    assert mention["window_days"] == 4
    assert mention["window_end_exclusive"] is True
