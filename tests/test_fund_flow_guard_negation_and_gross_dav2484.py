"""DAV-2484：资金流守卫两类误拦的确定性修复。

2026-11-25 生产有两份报告 / 4 个单元被 `fund_flow_consensus_guard` 误拦
（真值文件 `zk-20261125/truth-labels-zk-20261125.csv`，全部 GUARD_FP）：

1. ``74c02d78`` 601899.SH：``selected_field=netamount`` 时对正文做禁词子串
   匹配，命中「……属于待验证假设，严禁定义为主力建仓或洗盘已成事实」中的
   「主力建仓」——否定句被当成断言。
2. ``17055315`` 603259.SH：``validate_model_summary`` 把「总流入资金 17.55
   亿元」当成当日 netamount，与结构化 -0.03519 亿元比较；原文同时写明
   「总流出资金 17.58 亿元，总资金净额为 -0.03519 亿元」。

规则写死、不做语义猜测：

* 否定词表：严禁、禁止、不得、不应、不能、不构成、并非、不宜、切勿；必须出现
  在禁词之前且在同一分句（按 ``，。；！？\\n`` 切分）；禁词之后的否定词不算，
  单字「非」不算否定。
* netamount 只接受明确的「净额/净流入/净流出」标签；总流入/总流出/流入资金/
  流出资金是毛流，不得当净额抽取。

同时锁定：两类规则各自的人造真错仍须被拦。
"""

import json
from pathlib import Path

import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    _extract_model_mentions,
    extract_model_daily_values,
    find_non_main_force_violations,
    validate_model_summary,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dav2484_guard_fp_reports.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
UNITS = [
    (case, unit) for case in CASES for unit in case["units"]
]

NEGATION_TOKENS = ("严禁", "禁止", "不得", "不应", "不能", "不构成", "并非", "不宜", "切勿")

NET_SENTENCE = (
    "当日总流入资金 17.55 亿元，总流出资金 17.58 亿元，"
    "总资金净额为 **-351.90 万元（-0.03519 亿元）**，呈现微幅净流出状态。"
)


def _records(symbol, field, value, *, date="2026-11-25", source="ths_instant_snapshot"):
    return [
        {
            "source": source,
            "status": "available",
            "symbol": symbol,
            "date": date,
            "period_kind": "realtime_single_day",
            "time_window": "1d",
            "field": field,
            "value": value,
            "unit": "亿元",
            "field_semantics": {
                "netamount": "总净额（负值表示净流出）",
                "r0_net": "主力净额（负值表示净流出）",
            },
        }
    ]


def _validate(text, records, *, selected_field, symbol="603259.SH"):
    return validate_model_summary(
        records,
        text,
        window_days=1,
        selected_field=selected_field,
        selected_source=records[0]["source"],
        requested_as_of="2026-11-25",
        symbol=symbol,
    )


# ---------------------------------------------------------------------------
# 规则 1：否定语境下的禁词
# ---------------------------------------------------------------------------

def test_negated_forbidden_term_is_released():
    text = (
        "当前资金表现符合低位流入候选特征，属于待验证假设，"
        "严禁定义为主力建仓或洗盘已成事实。"
    )
    assert find_non_main_force_violations(text) == []


@pytest.mark.parametrize("token", NEGATION_TOKENS)
@pytest.mark.parametrize("term", ["主力建仓", "主力吸筹", "主力增持", "主力减持", "主力派发"])
def test_every_negation_token_waives_only_when_it_precedes_the_term(token, term):
    assert find_non_main_force_violations(f"{token}把盘面定性为{term}") == []
    assert find_non_main_force_violations(f"盘面已经{term}，{token}轻信") == [term]


def test_plain_assertion_is_blocked():
    assert find_non_main_force_violations("主力建仓已成事实。") == ["主力建仓"]


def test_single_character_fei_is_not_negation():
    # 「非常」里的单字「非」不算否定词。
    assert find_non_main_force_violations("主力建仓非常明显。") == ["主力建仓"]


def test_negation_after_the_term_does_not_waive():
    assert find_non_main_force_violations("主力建仓，并非洗盘。") == ["主力建仓"]
    assert find_non_main_force_violations("主力建仓不构成事实。") == ["主力建仓"]


def test_negation_in_another_clause_does_not_waive():
    text = "严禁据此追涨，主力建仓已经完成。"
    assert find_non_main_force_violations(text) == ["主力建仓"]


def test_negation_inside_a_quoted_disclaimer_is_scoped_to_its_clause():
    text = (
        "本项不可定性，严禁把统计口径写成主力建仓或主力吸筹；"
        "但主力增持已确认。"
    )
    assert find_non_main_force_violations(text) == ["主力增持"]


@pytest.mark.parametrize(
    "text, expected",
    [
        # 每类 3 个人造真错（DAV-2484 §三.5 否定类）
        ("主力建仓已成事实", ["主力建仓"]),
        ("主力建仓非常明显", ["主力建仓"]),
        ("主力建仓，并非洗盘", ["主力建仓"]),
    ],
)
def test_rule1_artificial_true_errors_are_blocked(text, expected):
    assert find_non_main_force_violations(text) == expected


# ---------------------------------------------------------------------------
# 规则 2：总流入/总流出不得当作 netamount
# ---------------------------------------------------------------------------

def test_gross_inflow_and_outflow_are_not_netamount_mentions():
    mentions = _extract_model_mentions(
        NET_SENTENCE, requested_as_of="2026-11-25", symbol="603259.SH"
    )
    netamount = [m for m in mentions if m["field"] == "netamount"]
    assert [m["value_text"] for m in netamount] == ["-0.03519"]


def test_net_amount_sentence_matches_structured_value_without_blocking():
    records = _records("603259.SH", "netamount", "-0.03519")
    result = _validate(NET_SENTENCE, records, selected_field="netamount")
    assert result["mismatches"] == []
    assert result["hard_guard"]["blocked"] is False
    assert result["status"] == "matched"


def test_gross_inflow_wrong_value_does_not_block():
    records = _records("603259.SH", "netamount", "-0.03519")
    text = "总流入资金 99.99 亿元，总资金净额为 -0.03519 亿元。"
    result = _validate(text, records, selected_field="netamount")
    assert result["hard_guard"]["blocked"] is False


def test_total_net_label_is_still_netamount():
    assert extract_model_daily_values("总资金净额 17.55 亿") == {"netamount": "17.55"}
    assert extract_model_daily_values("净流入资金 17.55 亿") == {"netamount": "17.55"}
    assert extract_model_daily_values("总流入资金 17.55 亿") == {}
    assert extract_model_daily_values("总流出 17.55 亿") == {}


def test_gross_component_inside_parenthesis_does_not_borrow_net_label():
    text = "| **总资金流向** | 净额 -351.90 万元（流入 17.55 亿 / 流出 17.58 亿） |"
    assert extract_model_daily_values(text) == {}


@pytest.mark.parametrize(
    "text",
    [
        # 每类 3 个人造真错（DAV-2484 §三.5 总流入类）
        "净额 17.55 亿",
        "总资金净额 17.55 亿",
    ],
)
def test_rule2_wrong_net_total_is_blocked(text):
    records = _records("603259.SH", "netamount", "-0.03519")
    result = _validate(text, records, selected_field="netamount")
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["model"] == "17.55"


def test_rule2_wrong_main_force_net_inflow_is_blocked():
    records = _records("603259.SH", "r0_net", "0.85")
    result = _validate("主力净流入 17.55 亿", records, selected_field="r0_net")
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["field"] == "r0_net"
    assert result["mismatches"][0]["model"] == "17.55"


# ---------------------------------------------------------------------------
# 2026-11-25 的 4 个 GUARD_FP 单元全部放行（真实原文 + 结构化记录）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case, unit", UNITS, ids=[
    f"{case['report_id'][:8]}-{unit['horizon']}" for case, unit in UNITS
])
def test_dav2484_guard_fp_units_are_released(case, unit):
    selection = unit["selection"]
    result = validate_model_summary(
        unit["records"],
        unit["smart_money_text"],
        window_days=int(selection.get("selected_window_days") or 1),
        selected_field=selection.get("selected_field"),
        selected_source=selection.get("selected_source"),
        requested_as_of=selection.get("requested_as_of") or case["trade_date"],
        symbol=case["symbol"],
    )
    assert result["hard_guard"]["blocked"] is False
    assert result["status"] not in {"blocked", "mismatch"}
    assert find_non_main_force_violations(unit["smart_money_text"]) == []


def test_601899_forbidden_term_hit_is_the_negated_disclaimer_only():
    case = next(c for c in CASES if c["symbol"] == "601899.SH")
    text = case["units"][0]["smart_money_text"]
    clauses = [clause for clause in text.replace("\n", "。").split("，") if "主力建仓" in clause]
    assert clauses
    for clause in clauses:
        assert "严禁" in clause[: clause.index("主力建仓")]
    assert find_non_main_force_violations(text) == []


def test_fixture_matches_recorded_baseline_blocks():
    assert {case["symbol"] for case in CASES} == {"601899.SH", "603259.SH"}
    assert all(
        unit["baseline_guard"]["blocked"] is True
        for case in CASES for unit in case["units"]
    )
