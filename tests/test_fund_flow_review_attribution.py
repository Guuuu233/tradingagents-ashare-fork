"""DAV-1434 red-team regressions: all prose and records are synthetic."""
import pytest

from tradingagents.dataflows.fund_flow_evidence import validate_model_summary


def check(text, *, symbol="601398.SH"):
    records = [{
        "source": "eastmoney_direct", "symbol": symbol, "date": "2026-09-24",
        "status": "available", "period_kind": "historical_daily", "time_window": "1d",
        "field": "r0_net", "value": "-1.25", "unit": "亿元",
    }]
    return validate_model_summary(
        records, text, window_days=1, selected_field="r0_net",
        selected_source="eastmoney_direct", requested_as_of="2026-09-24", symbol=symbol,
    )


@pytest.mark.parametrize("text", [
    "昨日成交额123456万元，今日主力净流入3亿元。",
    "昨日成交额1.123456亿元，今日主力净流入3亿元。",
])
def test_r1_six_digit_amount_is_not_a_peer_security_subject(text):
    result = check(text)
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["model_daily"] == {"r0_net": "3"}


def test_r2_later_main_force_label_overrides_non_main_background():
    result = check("非主力资金回落而今日主力净额为3亿元。")
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True


def test_r3_postfixed_sector_scope_still_excludes_value_with_symbol():
    result = check("今日主力净流入3亿元（行业口径）。")
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


def test_r4_short_target_alias_cannot_override_longer_peer_name():
    result = check("长电科技今日主力净流入3亿元。", symbol="600900.SH")
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


def test_r5_table_current_fact_overrides_scenario_cell_label():
    text = "| 分类 | 资金数据 |\n|---|---|\n| 情景 | 当前实测主力净流入3亿元 |"
    result = check(text)
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True


@pytest.mark.parametrize("amount", [
    "123456亿元", "123456元", "123456股", "123456手", "123456.5万元",
    "0.123456", "123456", "（123456万元）", "（123456）万元",
])
def test_financial_number_context_never_overwrites_target_subject(amount):
    result = check(f"昨日成交额{amount}，今日主力净流入3亿元。")
    assert result["status"] == "mismatch"
    assert result["model_daily"] == {"r0_net": "3"}


@pytest.mark.parametrize("code", ["600519", "600519.SH", "SH600519", "sh600519", "600519.sh"])
def test_real_peer_code_still_inherits_across_clauses(code):
    result = check(f"证券{code}昨日上涨，今日主力净流入3亿元。")
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


@pytest.mark.parametrize("prefix", ["非主力资金回落而", "非主力净额波动，但", "此前非主力净额为2亿元，"])
def test_background_non_main_field_does_not_hide_named_actual_conflict(prefix):
    assert check(f"{prefix}今日主力净额为3亿元。")["status"] == "mismatch"


@pytest.mark.parametrize("text", [
    "今日非主力净额为3亿元。",
    "今日主力净额旁证为非主力资金3亿元。",
    "同花顺总净额（非主力净额）为3亿元。",
])
def test_explicit_non_main_owner_remains_excluded(text):
    assert check(text)["hard_guard"]["blocked"] is False


@pytest.mark.parametrize("scope", ["行业口径", "板块资金", "全市场口径", "两市汇总"])
def test_postfixed_sector_scope_belongs_to_current_value(scope):
    result = check(f"今日主力净流入3亿元（{scope}）。")
    assert result["hard_guard"]["blocked"] is False


def test_sector_before_named_target_does_not_discard_target_conflict():
    result = check("行业主力净流入3亿元，601398.SH今日主力净流入4亿元。")
    assert result["status"] == "mismatch"
    assert result["model_daily"]["r0_net"] == "4"


def test_sector_in_next_table_cell_does_not_qualify_previous_actual_value():
    text = "| 个股 | 今日主力净流入3亿元 | 行业观察 |"
    assert check(text)["status"] == "mismatch"


@pytest.mark.parametrize("name", ["长电", "长江电力"])
def test_standalone_target_name_or_alias_still_compares(name):
    result = check(f"{name}今日主力净流入3亿元。", symbol="600900.SH")
    assert result["status"] == "mismatch"


def test_peer_long_name_then_separately_named_target_retains_conflict():
    result = check("长电科技主力净流入3亿元，长电今日主力净流入4亿元。", symbol="600900.SH")
    assert result["status"] == "mismatch"
    assert result["model_daily"]["r0_net"] == "4"


@pytest.mark.parametrize("label", ["情景", "假设", "路径", "预警", "观察项"])
def test_table_label_is_inherited_not_part_of_current_actual_claim(label):
    result = check(f"| 分类 | 资金数据 |\n|---|---|\n| {label} | 当前实测主力净流入3亿元 |")
    assert result["status"] == "mismatch"


@pytest.mark.parametrize("content", [
    "主力净流入3亿元以上", "若今日主力净流入3亿元", "如果当前主力净流入3亿元",
])
def test_table_scenario_or_explicit_condition_does_not_become_actual_fact(content):
    result = check(f"| 分类 | 资金数据 |\n|---|---|\n| 情景 | {content} |")
    assert result["hard_guard"]["blocked"] is False


def test_table_current_matching_fact_is_compared_not_dropped():
    result = check("| 分类 | 资金数据 |\n|---|---|\n| 情景 | 当前实测主力净流出1.25亿元 |")
    assert result["status"] == "matched"
    assert result["model_daily"] == {"r0_net": "-1.25"}


@pytest.mark.parametrize("text", [
    "昨日成交额为123456（万元），今日主力净流入3亿元。",
    "昨日成交额约123456（万元），今日主力净流入3亿元。",
    "昨日成交额为123456，今日主力净流入3亿元。",
])
def test_n1_amount_copula_and_postfixed_unit_are_not_security_subjects(text):
    result = check(text)
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["model_daily"] == {"r0_net": "3"}


@pytest.mark.parametrize("header,point", [("时点", "当前实测"), ("日期", "2026-09-24")])
def test_n2_separate_current_point_column_binds_to_fact(header, point):
    text = f"| 分类 | {header} | 资金数据 |\n|---|---|---|\n| 情景 | {point} | 主力净流入3亿元 |"
    assert check(text)["status"] == "mismatch"


def test_n3_separate_explicit_premise_overrides_current_word_in_fact():
    text = "| 分类 | 前提 | 资金数据 |\n|---|---|---|\n| 情景 | 如果行情转强 | 今日主力净流入3亿元 |"
    assert check(text)["hard_guard"]["blocked"] is False


@pytest.mark.parametrize("copula", ["为", "约", "约为", "大约为", "为约", "是", "达到", "预计为", "合计"])
@pytest.mark.parametrize("unit", ["", "（万元）", "(万元)", "（单位：万元）"])
def test_amount_field_modifiers_do_not_create_security_identity(copula, unit):
    assert check(f"昨日成交额{copula}123456{unit}，今日主力净流入3亿元。")["status"] == "mismatch"


@pytest.mark.parametrize("unit", ["（万元）", "(亿元)", "（单位：元）"])
def test_postfixed_parenthesized_money_unit_alone_excludes_token(unit):
    assert check(f"昨日记录123456{unit}，今日主力净流入3亿元。")["status"] == "mismatch"


@pytest.mark.parametrize("point", ["当前实测", "今日", "当日", "截至分析日", "2026-09-24", "9月24日"])
def test_row_current_point_overrides_inherited_scenario_title(point):
    text = f"### 假设\n| 时点 | 资金数据 |\n|---|---|\n| {point} | 主力净流入3亿元 |"
    assert check(text)["status"] == "mismatch"


def test_timestamp_after_value_still_binds_only_its_row():
    text = "| 分类 | 资金数据 | 日期 |\n|---|---|---|\n| 情景 | 主力净流入3亿元 | 2026-09-24 |"
    assert check(text)["status"] == "mismatch"


@pytest.mark.parametrize("premise", ["若行情转强", "如果行情转强"])
@pytest.mark.parametrize("point", ["当前实测", "2026-09-24"])
def test_bound_explicit_premise_wins_over_bound_current_timestamp(premise, point):
    text = f"| 分类 | 前提 | 时点 | 资金数据 |\n|---|---|---|---|\n| 情景 | {premise} | {point} | 今日主力净流入3亿元 |"
    assert check(text)["hard_guard"]["blocked"] is False


def test_postfixed_premise_is_still_explicit_condition():
    text = "| 分类 | 资金数据 | 前提 |\n|---|---|---|\n| 情景 | 今日主力净流入3亿元 | 如果行情转强 |"
    assert check(text)["hard_guard"]["blocked"] is False


def test_prior_row_current_marker_does_not_release_next_scenario():
    text = "| 分类 | 时点 | 资金数据 |\n|---|---|---|\n| 观察 | 当前实测 | 不可用 |\n| 情景 | 未确认 | 主力净流入3亿元 |"
    result = check(text)
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


def test_prior_row_premise_does_not_hide_next_current_fact():
    text = "| 分类 | 前提 | 时点 | 资金数据 |\n|---|---|---|---|\n| 情景 | 如果行情转强 | 当前实测 | 不可用 |\n| 情景 | 无 | 当前实测 | 主力净流入3亿元 |"
    assert check(text)["status"] == "mismatch"


def test_other_value_column_current_timestamp_does_not_release_first_scenario():
    text = "| 分类 | 时点A | 资金数据A | 时点B | 资金数据B |\n|---|---|---|---|---|\n| 情景 | 未确认 | 主力净流入3亿元 | 当前实测 | 总资金净流入1亿元 |"
    result = check(text)
    assert result["hard_guard"]["blocked"] is False
    assert "r0_net" not in result["model_daily"]


def test_current_second_value_not_shadowed_by_unconfirmed_first_value():
    text = "| 分类 | 时点A | 资金数据A | 时点B | 资金数据B |\n|---|---|---|---|---|\n| 情景 | 未确认 | 主力净流入3亿元 | 当前实测 | 主力净流入4亿元 |"
    result = check(text)
    assert result["status"] == "mismatch"
    assert result["model_daily"] == {"r0_net": "4"}


def test_second_value_timestamp_does_not_rebind_first_value_date():
    records = [
        {"source": "eastmoney_direct", "symbol": "601398.SH", "date": date,
         "status": "available", "period_kind": "historical_daily", "time_window": "1d",
         "field": "r0_net", "value": value, "unit": "亿元"}
        for date, value in [("2026-09-23", "3"), ("2026-09-24", "-1.25")]
    ]
    text = "| 分类 | 资金数据A | 日期A | 资金数据B | 日期B |\n|---|---|---|---|---|\n| 实际值 | 主力净流入3亿元 | 2026-09-23 | 主力净流出1.25亿元 | 2026-09-24 |"
    result = validate_model_summary(records, text, window_days=1, selected_field="r0_net", selected_source="eastmoney_direct", requested_as_of="2026-09-24", symbol="601398.SH")
    assert result["status"] == "matched"
    assert result["model_daily"] == {"r0_net": "-1.25"}
    assert result["model_dated"] == {"r0_net": "3"}


def test_other_value_premise_does_not_hide_current_second_value():
    text = "| 分类 | 前提A | 资金数据A | 时点B | 资金数据B |\n|---|---|---|---|---|\n| 情景 | 如果行情转强 | 今日主力净流入3亿元 | 当前实测 | 主力净流入4亿元 |"
    result = check(text)
    assert result["status"] == "mismatch"
    assert result["model_daily"] == {"r0_net": "4"}


def test_unrelated_note_is_not_a_bound_current_point():
    text = "| 分类 | 备注 | 资金数据 |\n|---|---|---|\n| 情景 | 当前实测2026-09-24 | 主力净流入3亿元 |"
    assert check(text)["hard_guard"]["blocked"] is False


def test_if_in_note_is_not_a_bound_explicit_premise():
    text = "| 分类 | 备注 | 资金数据 |\n|---|---|---|\n| 情景 | 引用如果行情转强的说法 | 当前实测主力净流入3亿元 |"
    assert check(text)["status"] == "mismatch"


def test_current_marker_in_owned_field_label_is_not_just_a_scenario_tag():
    text = "### 假设\n| 资金维度 | 资金数据 |\n|---|---|\n| 今日主力净额 | -1.25亿元 |"
    assert check(text)["status"] == "matched"


def test_empty_current_point_column_preserves_header_alignment():
    text = "| 分类 | 时点 | 资金数据 |\n|---|---|---|\n| 情景 | | 主力净流入3亿元 |"
    assert check(text)["hard_guard"]["blocked"] is False


def test_empty_premise_cell_does_not_shift_current_point_binding():
    text = "| 分类 | 前提 | 时点 | 资金数据 |\n|---|---|---|---|\n| 情景 | | 当前实测 | 主力净流入3亿元 |"
    assert check(text)["status"] == "mismatch"


def test_current_point_for_empty_other_value_does_not_spill_to_existing_value():
    text = "| 分类 | 时点A | 资金数据A | 时点B | 资金数据B |\n|---|---|---|---|---|\n| 情景 | 当前实测 | | | 主力净流入3亿元 |"
    result = check(text)
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


def test_explicit_premise_for_empty_other_value_does_not_hide_existing_fact():
    text = "| 分类 | 前提A | 资金数据A | 时点B | 资金数据B |\n|---|---|---|---|---|\n| 情景 | 如果行情转强 | | 当前实测 | 主力净流入3亿元 |"
    assert check(text)["status"] == "mismatch"


def test_target_name_in_other_value_group_does_not_own_peer_value():
    text = "| 证券A | 资金数据A | 证券B | 资金数据B |\n|---|---|---|---|\n| 601398.SH | 不可用 | 600519.SH | 今日主力净流入3亿元 |"
    result = check(text)
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


def test_peer_value_before_target_fact_does_not_shadow_target_conflict():
    text = "| 证券A | 资金数据A | 证券B | 资金数据B |\n|---|---|---|---|\n| 600519.SH | 主力净流入3亿元 | 601398.SH | 今日主力净流入4亿元 |"
    result = check(text)
    assert result["status"] == "mismatch"
    assert result["model_daily"] == {"r0_net": "4"}


@pytest.mark.parametrize("headers", [
    "| 前提 | 资金数据 | 前提 | 资金数据 |",
    "| 前提 | 资金数据 | 分类 | 资金数据 |",
])
def test_n4_unlabeled_empty_data_group_does_not_lend_premise(headers):
    text = f"{headers}\n|---|---|---|---|\n| 如果行情转强 |  | 无 | 今日主力净流入3亿元 |"
    result = check(text)
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["model_daily"] == {"r0_net": "3"}


@pytest.mark.parametrize("headers", [
    "| 分类 | 时点 | 资金数据 | 时点 | 资金数据 |",
    "| 分类 | 时点 | 资金数据 | 前提 | 资金数据 |",
])
def test_n5_unlabeled_empty_data_group_does_not_lend_timestamp(headers):
    text = f"{headers}\n|---|---|---|---|---|\n| 情景 | 当前实测 |  | 未确认 | 主力净流入3亿元 |"
    assert check(text)["hard_guard"]["blocked"] is False


def test_empty_group_premise_does_not_leak_even_with_labeled_second_group():
    text = "| 前提A | 资金数据A | 前提B | 资金数据B |\n|---|---|---|---|\n| 如果行情转强 |  | 无 | 今日主力净流入3亿元 |"
    assert check(text)["status"] == "mismatch"


def test_empty_group_time_does_not_leak_even_with_labeled_second_group():
    text = "| 分类 | 时点A | 资金数据A | 时点B | 资金数据B |\n|---|---|---|---|---|\n| 情景 | 当前实测 |  | 未确认 | 主力净流入3亿元 |"
    assert check(text)["hard_guard"]["blocked"] is False


def test_unlabeled_time_first_value_keeps_its_own_bound_time():
    text = "| 分类 | 时点 | 资金数据 | 时点 | 资金数据 |\n|---|---|---|---|---|\n| 情景 | 当前实测 | 主力净流出1.25亿元 | 未确认 | 主力净流入3亿元 |"
    result = check(text)
    assert result["status"] == "matched"
    assert result["model_daily"] == {"r0_net": "-1.25"}


@pytest.mark.parametrize("text", [
    "| 资金数据 |\n|---|\n| 9月23日主力净流入3亿元、9月24日主力净流出1.25亿元 |",
    "| 资金数据 |\n|---|\n| 9月23日主力净流入3亿元，9月24日主力净流出1.25亿元 |",
    "| 资金数据 |\n|---|\n| 2026-09-23主力净流入3亿元；2026-09-24主力净流出1.25亿元 |",
])
def test_n6_same_cell_multiple_dated_facts_bind_each_to_own_date(text):
    records = [
        {"source": "eastmoney_direct", "symbol": "601398.SH", "date": date,
         "status": "available", "period_kind": "historical_daily", "time_window": "1d",
         "field": "r0_net", "value": value, "unit": "亿元"}
        for date, value in [("2026-09-23", "3"), ("2026-09-24", "-1.25")]
    ]
    result = validate_model_summary(records, text, window_days=1, selected_field="r0_net", selected_source="eastmoney_direct", requested_as_of="2026-09-24", symbol="601398.SH")
    assert result["status"] == "matched"
    assert result["model_daily"] == {"r0_net": "-1.25"}
    assert result["model_dated"] == {"r0_net": "3"}


def test_later_same_cell_value_does_not_shadow_earlier_current_conflict():
    text = "| 资金数据 |\n|---|\n| 9月23日主力净流入3亿元、9月24日主力净流出1.25亿元 |"
    records = [
        {"source": "eastmoney_direct", "symbol": "601398.SH", "date": date,
         "status": "available", "period_kind": "historical_daily", "time_window": "1d",
         "field": "r0_net", "value": value, "unit": "亿元"}
        for date, value in [("2026-09-23", "2"), ("2026-09-24", "-1.25")]
    ]
    assert validate_model_summary(records, text, window_days=1, selected_field="r0_net", selected_source="eastmoney_direct", requested_as_of="2026-09-24", symbol="601398.SH")["status"] == "mismatch"


@pytest.mark.parametrize("header", ["分类", "情景", "路径", "条件标签", "观察项"])
def test_explicit_if_in_category_cell_remains_conditional(header):
    # Review N3 sibling: explicit 若/如果 in the row's category/context cell
    # conditions the row's fact, just like a dedicated premise column.
    text = f"| {header} | 资金数据 |\n|---|---|\n| 若行情转强 | 当前实测主力净流入3亿元 |"
    result = check(text)
    assert result["hard_guard"]["blocked"] is False


def test_explicit_if_after_named_target_still_conditions_only_that_value():
    text = "| 分类 | 资金数据 |\n|---|---|\n| 601398.SH | 若行情转强，当前实测主力净流入3亿元 |"
    assert check(text)["hard_guard"]["blocked"] is False


@pytest.mark.parametrize("empty", ["", "不可用", "—"])
@pytest.mark.parametrize("context, expected", [("如果行情转强", True), ("当前实测", False)])
def test_unavailable_unlabeled_group_retains_its_own_context(empty, context, expected):
    role = "前提" if expected else "时点"
    value = "今日主力净流入3亿元" if expected else "主力净流入3亿元"
    text = f"| 分类 | {role} | 资金数据 | {role} | 资金数据 |\n|---|---|---|---|---|\n| 情景 | {context} | {empty} | 未确认 | {value} |"
    assert check(text)["hard_guard"]["blocked"] is expected


@pytest.mark.parametrize("text", [
    "| 分类 | 时点 | 前提 | 资金数据 |\n|---|---|---|---|\n| 情景 | 当前实测 | | 主力净流入3亿元 |",
    "| 分类 | 前提 | 时点 | 资金数据 |\n|---|---|---|---|\n| 情景 | 如果行情转强 | | 今日主力净流入3亿元 |",
])
def test_empty_context_column_is_not_an_empty_data_group(text):
    assert check(text)["hard_guard"]["blocked"] is ("如果" not in text)


# R5: unrelated historical windows/trend duration must not override a local
# single-day claim. Values/dates below are synthetic, not production prose.
R5_DATE = "2026-09-22"
R5_RECORDS = [
    {"source": "eastmoney_direct", "symbol": "601398.SH", "date": date,
     "status": "available", "period_kind": "historical_daily", "time_window": "1d",
     "field": "r0_net", "value": value, "unit": "亿元"}
    for date, value in [
        ("2026-09-19", "-3"), ("2026-09-20", "-2"),
        ("2026-09-21", "0.42"), (R5_DATE, "2.6042"),
    ]
]


@pytest.mark.parametrize("text", [
    "9月19日至20日连续两日走弱后，9月22日主力净额回升至+2.60亿元。",
    "9月19日至20日持续走弱后，9月22日主力净额回升至+2.60亿元。",
    "此前连续两日调整，在2026-09-22主力净流入2.60亿元。",
    "经历9月19日至20日的连续两日回落后在9月22日主力净额达到2.60亿元。",
    "此前9月19日至20日连续两日回落；9月22日主力净额2.60亿元。",
    "今日主力净额2.60亿元（连续两日改善）。",
    "单日主力净流入2.60亿元（连续2个交易日回升）。",
    "| 日期 | 资金数据 |\n|---|---|\n| 2026-09-22 | 主力净流入2.60亿元（连续两日改善） |",
    "| 资金维度 | 资金数据 |\n|---|---|\n| 单日主力净额 | +2.60亿元（连续两日改善） |",
])
def test_r5_single_day_binding_wins_over_unrelated_history_or_trend(text):
    result = validate_model_summary(
        R5_RECORDS, text, window_days=1, selected_field="r0_net",
        selected_source="eastmoney_direct", requested_as_of=R5_DATE, symbol="601398.SH",
    )
    assert result["status"] == "matched"
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {"r0_net": "2.6"}
    assert result["model_interval"] == {}


@pytest.mark.parametrize("text", [
    "9月22日两日累计主力净流入3.02亿元。",
    "两日累计：9月22日主力净流入3.02亿元。",
    "两日主力净流入3.02亿元（9月21日0.42亿元、9月22日2.6042亿元）。",
    "9月21日至22日两日累计主力净流入3.02亿元。",
    "今日两日累计主力净流入3.02亿元。",
    "今日主力净流入3.02亿元（两日累计）。",
    "| 日期 | 资金数据 |\n|---|---|\n| 2026-09-22 | 两日累计主力净流入3.02亿元 |",
])
@pytest.mark.parametrize("wrong", [False, True])
def test_r5_direct_cumulative_modifier_still_compares_window(text, wrong):
    if wrong:
        text = text.replace("3.02", "8.02")
    result = validate_model_summary(
        R5_RECORDS, text, window_days=1, selected_field="r0_net",
        selected_source="eastmoney_direct", requested_as_of=R5_DATE, symbol="601398.SH",
    )
    assert result["status"] == ("mismatch" if wrong else "matched")
    assert result["hard_guard"]["blocked"] is wrong
    assert result["model_daily"] == {}
    assert result["model_interval"] == {"r0_net": "8.02" if wrong else "3.02"}


@pytest.mark.parametrize("text", [
    "此前连续两日调整，在9月22日主力净流入7亿元。",
    "今日主力净流入7亿元（连续两日改善）。",
])
def test_r5_true_single_day_contradictions_are_not_relaxed(text):
    result = validate_model_summary(
        R5_RECORDS, text, window_days=1, selected_field="r0_net",
        selected_source="eastmoney_direct", requested_as_of=R5_DATE, symbol="601398.SH",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["model_daily"] == {"r0_net": "7"}
    assert result["model_interval"] == {}


def test_unknown_extended_name_does_not_confirm_embedded_target_alias():
    # No claim about the unknown company's ticker. It must not overwrite the
    # explicit preceding peer solely by matching the embedded short alias.
    text = "601398.SH昨日上涨，长电股份今日主力净流入3亿元。"
    assert check(text, symbol="600900.SH")["hard_guard"]["blocked"] is False
