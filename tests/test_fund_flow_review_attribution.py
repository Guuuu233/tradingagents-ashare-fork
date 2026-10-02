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
