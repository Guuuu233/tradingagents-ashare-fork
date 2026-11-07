"""DAV-2388：清零 6 份假拒 + 修 3 份前序窗口误报 + 守住 5 份真错。

来源：总控 11-07 06:15 对候选 `a19ee27a` 的打回（未跑全库 532 份重放，掩盖了
6 份 12 档 100% 假拒），以及 11-05 新触发的 3 份「前一日 / 前序窗口」误报。

冻结原文与结构化记录是 11-06 静态快照的比对相关字段等价投影
（`tests/fixtures/dav2388_frozen_reports.json`）：symbol / trade_date /
selection / `fund_flow_evidence.records` + `smart_money_report`
（弃权报告取 `smart_money_report_blocked_original.original_text`）。因此这里的
「原地篡改」改的是报告里真实存在的数字，不是另造一句注入断言。

本轮口径（总控已裁定）：
6 份假拒全部 blocked=False（有当日可比数据的走 matched）；
11-05 的 3 份前日/前序窗口误报 = matched；
5 份模型真写错的报告继续 mismatch / blocked=True。
"""

import json
from pathlib import Path

import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    _extract_model_mentions,
    validate_model_summary,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dav2388_frozen_reports.json"
FROZEN = {
    case["report_id"]: case
    for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
}

# 必改 1：6 份 100% 假拒（12 档），报告原文数字全部正确。
FALSE_REJECTS = {
    "e2e8c10c": ("601012.SH", "2026-11-03", "matched"),
    "8a4bf213": ("600028.SH", "2026-11-04", "matched"),
    "e5103b31": ("002371.SZ", "2026-10-23", "matched"),
    "b027075f": ("601127.SH", "2026-10-23", "matched"),
    "02c3cb16": ("002371.SZ", "2026-11-06", "matched"),
    # 「分别为」逐日枚举项没有归属日，不冒充当日值也不硬绑区间右端点；
    # 该份其余读数可核，故沿用主干 f0dc92a0 的 unverifiable（非拦截）。
    "daf62d29": ("600276.SH", "2026-10-28", "unverifiable"),
}

# 必改 2：11-05 三份前序窗口 / 前日读数误报。
PRIOR_WINDOW = {
    "5617bad7": ("600030.SH", "较前一日的 -2.84 亿元 → 上一交易日读数"),
    "eebddfdc": ("600276.SH", "此前连续 4 日（累计流出逾 10.4 亿元）之后 → T-4..T-1"),
    "3eca48c6": ("000725.SZ", "连续两日净流入（合计 +5.61 亿元）后 → T-2..T-1"),
}

# 必改 2：5 份模型真写错，必须继续拦截（不得被本轮放松放行）。
MODEL_ERRORS = {
    "5336af4a": ("002415.SZ", "-0.0015", "-0.01458144"),
    "075b4c14": ("600196.SH", "0.2508", "0.02508432"),
    "74465dad": (None, "-1.91", "-2.06800416"),
    "79275169": (None, "0.6089", "-0.16872712"),
    "fcb0d179": (None, "-0.1896", "0.112759"),
}


def replay(report_id, text=None):
    case = FROZEN[report_id]
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


def full_id(prefix):
    matches = [rid for rid in FROZEN if rid.startswith(prefix)]
    assert len(matches) == 1, (prefix, matches)
    return matches[0]


def extract(text, *, symbol, as_of):
    return _extract_model_mentions(text, requested_as_of=as_of, symbol=symbol)


def find(mentions, value_text):
    found = [m for m in mentions if m["value_text"] == value_text]
    assert found, (value_text, [m["value_text"] for m in mentions])
    return found


# ---------------------------------------------------------------------------
# 必改 1：6 份假拒全部不再拦截
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prefix, expected", sorted(FALSE_REJECTS.items()))
def test_false_reject_report_is_not_blocked(prefix, expected):
    symbol, trade_date, status = expected
    case = FROZEN[full_id(prefix)]
    assert case["symbol"] == symbol and case["trade_date"] == trade_date
    result = replay(full_id(prefix))
    assert result["hard_guard"]["blocked"] is False, (prefix, result["mismatches"])
    assert result["status"] == status, (prefix, result["status"], result["mismatches"])


def test_false_reject_rollup_is_zero_blocked():
    blocked = {
        prefix: replay(full_id(prefix))["hard_guard"]["blocked"]
        for prefix in FALSE_REJECTS
    }
    assert not any(blocked.values()), blocked
    statuses = {prefix: replay(full_id(prefix))["status"] for prefix in FALSE_REJECTS}
    assert sum(1 for status in statuses.values() if status == "matched") == 5
    assert statuses["daf62d29"] == "unverifiable"


# ---------------------------------------------------------------------------
# 必改 2：前日读数 / 前序窗口按正确粒度比对
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prefix, expected", sorted(PRIOR_WINDOW.items()))
def test_prior_period_and_window_report_reaches_matched(prefix, expected):
    symbol, reason = expected
    case = FROZEN[full_id(prefix)]
    assert case["symbol"] == symbol
    result = replay(full_id(prefix))
    assert result["status"] == "matched", (prefix, reason, result["mismatches"],
                                           result["unverifiable_fields"])
    assert result["hard_guard"]["blocked"] is False


@pytest.mark.parametrize("prefix, model", [
    ("5617bad7", "-2.84"),
    ("eebddfdc", "-10.4"),
    ("3eca48c6", "5.61"),
])
def test_prior_window_values_are_compared_against_the_prior_window(prefix, model):
    """这些金额必须真的参与比对，而不是靠「不提取」蒙过门禁。

    比对本轮口径的证明在下面的红队用例：把该金额原地改错，同一前序窗口的
    结构化值会以 mismatch 显形。
    """
    case = FROZEN[full_id(prefix)]
    mentions = extract(case["smart_money_text"], symbol=case["symbol"],
                       as_of=case["trade_date"])
    mention = find(mentions, model)[0]
    if prefix == "5617bad7":
        assert mention["kind"] == "previous"
    else:
        assert mention["kind"] == "interval"
        assert mention["window_end_exclusive"] is True
    result = replay(full_id(prefix))
    assert result["status"] == "matched"
    assert result["hard_guard"]["blocked"] is False


# ---------------------------------------------------------------------------
# 必改 2：5 份模型真写错继续拦截
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prefix, expected", sorted(MODEL_ERRORS.items()))
def test_model_error_is_still_blocked(prefix, expected):
    _symbol, model, structured = expected
    result = replay(full_id(prefix))
    assert result["status"] == "mismatch", (prefix, result["status"], result["mismatches"])
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"], prefix
    assert result["mismatches"][0]["model"].lstrip("+") == model.lstrip("+"), result["mismatches"]
    assert result["mismatches"][0]["structured"] == structured, result["mismatches"]


# ---------------------------------------------------------------------------
# 红队：原地篡改冻结原文里的资金数字 → 必须拦
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prefix, own_text, corrupt_text, structured", [
    # e2e8c10c 隆基绿能：单日主力净额 +0.1976 亿元（本轮新核验的个股自身读数）
    ("e2e8c10c", "+0.1976 亿元", "+9.99 亿元", "0.19764992"),
    # 8a4bf213 中国石化：5 日主力累计净额 +1.2000 亿元
    ("8a4bf213", "+1.2000 亿元", "+9.99 亿元", "1.2000146"),
    # e5103b31 北方华创：口径小标题的 -2.93 亿元（同为个股自身读数）
    ("e5103b31", "-2.93 亿元", "-9.99 亿元", "-2.927957"),
    # b027075f 赛力斯：5 日累计主力净额 -5.746173 亿元
    ("b027075f", "-5.746173 亿元", "-9.99 亿元", "-5.74617292"),
    # 02c3cb16 北方华创：单日主力净额 +6.1048 亿元
    ("02c3cb16", "+6.1048 亿元", "+9.99 亿元", "6.1048432"),
    # daf62d29 恒瑞医药：5 日累计主力净额 -1.97 亿元
    ("daf62d29", "-1.97 亿元", "-9.99 亿元", "-1.96571778"),
    # 5617bad7 中信证券：较前一日的 -2.84 亿元（previous 粒度）
    ("5617bad7", "-2.84 亿元", "-9.99 亿元", "-2.83793248"),
    # eebddfdc 恒瑞医药：此前连续 4 日累计流出逾 10.4 亿元（T-4..T-1 粒度）
    ("eebddfdc", "10.4 亿元", "19.99 亿元", "-10.4360456"),
    # 3eca48c6 京东方Ａ：连续两日净流入合计 +5.61 亿元（T-2..T-1 粒度）
    ("3eca48c6", "+5.61 亿元", "+9.99 亿元", "5.60580272"),
])
def test_in_place_corruption_of_fund_flow_number_is_blocked(
    prefix, own_text, corrupt_text, structured,
):
    case = FROZEN[full_id(prefix)]
    assert own_text in case["smart_money_text"], (prefix, own_text)
    clean = replay(full_id(prefix))
    assert clean["hard_guard"]["blocked"] is False, (prefix, clean["mismatches"])

    corrupted = replay(full_id(prefix),
                       case["smart_money_text"].replace(own_text, corrupt_text))
    assert corrupted["status"] == "mismatch", (prefix, corrupted["unverifiable_fields"])
    assert corrupted["hard_guard"]["blocked"] is True
    assert corrupted["mismatches"], prefix
    assert corrupted["mismatches"][0]["structured"] == structured, corrupted["mismatches"]


# ---------------------------------------------------------------------------
# 抽取面：作用域三条边界（本轮修复的最小可复现片段）
# ---------------------------------------------------------------------------

def test_following_parenthetical_window_is_not_bound_to_the_daily_value():
    """「-2.927957 亿元（5日累计净流出达 -13.3565 亿元）」：括号是另一笔读数。"""
    mentions = extract(
        "- **主力净额（r0_net）**：-2.927957 亿元"
        "（5日累计净流出达 -13.3565 亿元）。",
        symbol="002371.SZ", as_of="2026-10-23",
    )
    daily = find(mentions, "-2.927957")[0]
    assert daily["kind"] == "daily"
    assert daily["window_days"] is None
    # 括号里那笔 5 日累计读数不得反过来把当日值变成 5 日窗口。
    assert all(
        mention["window_days"] != 5 for mention in mentions
        if mention["value_text"] == "-2.927957"
    )


def test_streak_ordinal_is_not_an_aggregation_window():
    """「，呈现连续第 3 个交易日净流出态势」不是 -0.85415 亿的 3 日累计。"""
    mentions = extract(
        "- **主力净额（r0_net）**：-8541.50 万元（-0.85415 亿元），"
        "呈现连续第 3 个交易日净流出态势。",
        symbol="600028.SH", as_of="2026-11-04",
    )
    mention = find(mentions, "-0.85415")[0]
    assert mention["kind"] == "daily"
    assert mention["window_days"] is None


def test_dated_enumeration_items_bind_their_own_date_only():
    """「10月19日（-1.44亿元）、10月20日（-1.29亿元）」：各项只认自己的日期。"""
    mentions = extract(
        "- **近5日累计主力净额（东财序列）**：10月19日（-1.44亿元）、"
        "10月20日（-1.29亿元）、10月21日（-1.80亿元），"
        "5日累计主力净额为 -5.746173 亿元。",
        symbol="601127.SH", as_of="2026-10-23",
    )
    assert find(mentions, "-1.44")[0]["date"] == "2026-10-19"
    assert find(mentions, "-1.29")[0]["date"] == "2026-10-20"
    assert find(mentions, "-1.8")[0]["date"] == "2026-10-21"
    for value in ("-1.44", "-1.29", "-1.8"):
        mention = find(mentions, value)[0]
        assert mention["kind"] == "dated"
        assert mention["window_days"] is None
    total = find(mentions, "-5.746173")[0]
    assert total["kind"] == "interval" and total["window_days"] == 5


def test_plural_enumeration_items_do_not_inherit_the_running_total_label():
    """「（11-02 至 11-06）分别录得 …、…，5日累计净流入达 +23.80 亿元」。"""
    mentions = extract(
        "- **近5日累计主力净额**：近5个交易日（11-02 至 11-06）分别录得 "
        "+8.03 亿元、+0.43 亿元、+8.12 亿元、+1.11 亿元及 +6.10 亿元，"
        "5日累计净流入达 +23.80 亿元。",
        symbol="002371.SZ", as_of="2026-11-06",
    )
    values = {m["value_text"] for m in mentions}
    # 逐日枚举项没有归属日：既不冒充当日值（会被误判 mismatch），也不借用
    # 区间右端点日期，因此不作为独立读数提取。
    assert not values & {"8.03", "0.43", "8.12", "1.11"}, values
    total = find(mentions, "23.8")[0]
    assert total["kind"] == "interval" and total["window_days"] == 5


def test_prior_period_wording_binds_the_previous_trading_day():
    """「较前一日的 -2.84 亿元」是前一期读数，不是当日值。"""
    mentions = extract(
        "但 11 月 5 日单日主力净流出显著收窄至 -1658 万元"
        "（较前一日的 -2.84 亿元大幅缩减 94%）。",
        symbol="600030.SH", as_of="2026-11-05",
    )
    mention = find(mentions, "-2.84")[0]
    assert mention["kind"] == "previous"
    assert mention["window_days"] is None
