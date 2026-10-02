"""DAV-1428: synthetic prose only; no production-report fixtures."""
from decimal import Decimal

import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    _extract_model_mentions,
    extract_model_daily_values,
    extract_model_totals,
    validate_model_summary,
)

DATE = "2026-09-24"
SYMBOL = "601398.SH"
SOURCE = "eastmoney_direct"


def record(value="-1.25", date=DATE, field="r0_net"):
    return {
        "source": SOURCE, "symbol": SYMBOL, "date": date,
        "status": "available", "period_kind": "historical_daily",
        "time_window": "1d", "field": field, "value": value, "unit": "亿元",
    }


def validate(text, value="-1.25", *, records=None, **kwargs):
    return validate_model_summary(
        [record(value)] if records is None else records, text,
        window_days=1, selected_field="r0_net", selected_source=SOURCE,
        requested_as_of=DATE, **kwargs,
    )


@pytest.mark.parametrize("text", [
    "若主力净流入维持在3亿元以上，则进入观察。",
    "如果单日主力净流出超过3亿元，应复核。",
    "单日主力净流入需达到3亿元。",
    "预期主力净流入3亿元。",
    "目标主力净流入3亿元。",
    "关注主力净额能否收敛至-0.3亿元以内。",
    "主力净流入 >+3亿元/日时确认。",
    "### 触发条件\n- 单日主力净流入3亿元以上。",
    "### 假设\n- 主力净流入3亿元。",
    "### 路径A\n- 主力净流入3亿元。",
    "1. **修复触发条件**：\n   - 单日主力净流入3亿元以上。",
    "1. **主导情景**：\n   - 资金特征：主力净流出持续在0.3亿元以上。",
    "| 路径 | 触发条件 |\n|---|---|\n| 转弱 | 主力净流出3亿元以上 |",
    "若连续2日主力净流入3亿元，则确认。",
])
def test_conditions_are_not_actual_claims(text):
    result = validate(text)
    assert result["hard_guard"]["blocked"] is False
    assert result["mismatches"] == []


@pytest.mark.parametrize("text", [
    "今日主力净流出1.25亿元。",
    "当日主力净额为-1.25亿元。",
    "2026-09-24主力净流出1.25亿元。",
    "### 假设\n当前实测主力净流出1.25亿元。",
    "### 推演路径\n2026-09-24主力净流出1.25亿元。",
    "- **情景标签**：当前实测主力净流出1.25亿元。",
    "- **预警标签**：当日主力净流出1.25亿元。",
])
def test_current_actual_claims_survive_heading_context(text):
    result = validate(text)
    assert result["status"] == "matched"
    assert result["model_daily"] == {"r0_net": "-1.25"}


@pytest.mark.parametrize("text", [
    "今日主力净流入3亿元。",  # Directional contradiction.
    "今日主力净流出4亿元。",  # Magnitude contradiction.
    "今日主力净流出突破3亿元。",  # False factual lower bound.
    "今日主力净额−4亿元。",  # Unicode negative, genuinely wrong.
    "### 假设\n当前实测主力净流入3亿元。",
    "### 预警\n截至分析日主力净流出4亿元。",
    "- **情景标签**：当前实测主力净流出4亿元。",
    "- **预警标签**：当日主力净流入3亿元。",
])
def test_real_contradictions_remain_hard_blocked(text):
    result = validate(text)
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"]


@pytest.mark.parametrize("range_text", [
    "1~3亿", "1～3亿", "1至3亿", "1到3亿", "1-3亿", "1–3亿",
    "1亿至3亿", "1亿-3亿", "-1至-3亿", "1 至 3 亿元",
])
def test_amount_range_endpoints_never_become_daily_claims(range_text):
    result = validate(f"今日主力净流出{range_text}。")
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


def test_range_exclusion_does_not_hide_later_true_contradiction():
    result = validate("今日主力净流出1至3亿。今日主力净流入4亿。")
    assert result["status"] == "mismatch"
    assert result["model_daily"]["r0_net"] == "4"


@pytest.mark.parametrize("minus", ["−", "－", "-"])
def test_negative_sign_normalization_preserves_signed_amount(minus):
    result = validate(f"今日主力净额{minus}1.25亿元。")
    assert result["status"] == "matched"
    assert result["model_daily"]["r0_net"] == "-1.25"


@pytest.mark.parametrize("text", [
    "中单与小单在主力口径下分别流出0.3亿元与0.2亿元。",
    "同花顺总净额（非主力净额）为-3亿元。",
    "主力净额因计入超大单（-3亿元）而呈净流出（-1.25亿元）。",
    "主力净流入由大单贡献（+3亿元），当前主力净流出1.25亿元。",
    "主力净额与总净额背离：同花顺总净额净流入3亿元。",
    "主力统计口径与同花顺大单（+3亿）不同，当前主力净流出1.25亿元。",
])
def test_explicit_component_or_other_field_not_compared_as_main_force(text):
    result = validate(text)
    assert result["hard_guard"]["blocked"] is False
    assert all(m["field"] != "r0_net" for m in result["mismatches"])


@pytest.mark.parametrize("text", [
    "今日主力/大单净额为3亿元。",
    "今日主力大单净流入3亿元。",
    "今日主力净额（超大单加大单）为3亿元。",
])
def test_ambiguous_main_force_component_attribution_fails_closed(text):
    assert validate(text)["status"] == "mismatch"


@pytest.mark.parametrize("bound", ["突破", "超过", "超", "逾", "不低于"])
@pytest.mark.parametrize("direction,value", [("流出", "-1.25"), ("流入", "1.25")])
def test_factual_lower_bound_uses_magnitude_and_direction(bound, direction, value):
    result = validate(f"今日主力净{direction}{bound}1.2亿元。", value)
    assert result["status"] == "matched"
    assert result["mismatches"] == []


@pytest.mark.parametrize("bound", ["不足", "低于", "不到"])
def test_factual_upper_bound_is_not_equality(bound):
    assert validate(f"今日主力净流出{bound}1.5亿元。")["status"] == "matched"


@pytest.mark.parametrize("text", [
    "今日主力净流出1.2亿元以上。",
    "今日主力净流出1.5亿元以内。",
])
def test_postfixed_factual_bounds(text):
    assert validate(text)["status"] == "matched"


@pytest.mark.parametrize("text", [
    "今日主力净流入超过1.2亿元。",  # Even a satisfied magnitude bound needs same sign.
    "今日主力净流出低于1亿元。",
    "今日主力净流出突破1.3亿元。",
    "今日主力净流出约3亿元。",
    "今日主力净流出近3亿元。",
])
def test_wrong_factual_bounds_and_approximate_equalities_block(text):
    assert validate(text)["status"] == "mismatch"


def test_bound_tolerance_remains_one_hundredth():
    assert validate("今日主力净流出超过1.26亿元。")["status"] == "matched"
    assert validate("今日主力净流出超过1.261亿元。")["status"] == "mismatch"
    assert validate("今日主力净流出不足1.24亿元。")["status"] == "matched"
    assert validate("今日主力净流出不足1.239亿元。")["status"] == "mismatch"


@pytest.mark.parametrize("text", [
    "9月23日至24日两日累计主力净流入3亿元。",
    "两日主力净流入3亿元（9月23日1亿元、9月24日2亿元）。",
])
def test_cumulative_window_precedes_dates_and_compares_same_window(text):
    records = [record("1", "2026-09-23"), record("2")]
    result = validate(text, records=records)
    assert result["status"] == "matched"
    assert result["model_daily"] == {}
    assert result["model_interval"] == {"r0_net": "3"}


def test_date_range_without_explicit_day_count_is_not_single_day():
    result = validate("9月23日至24日主力累计净流入3亿元。")
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


def test_historical_two_day_window_does_not_compare_to_latest_two_days():
    records = [record("1", "2026-09-21"), record("2", "2026-09-22"), record("8", "2026-09-23"), record("9")]
    result = validate("9月21日至22日两日累计主力净流入3亿元。", records=records)
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


def test_cumulative_wrong_value_still_blocked():
    records = [record("1", "2026-09-23"), record("2")]
    assert validate("两日主力累计净流入8亿元。", records=records)["status"] == "mismatch"


def test_cumulative_without_matching_window_is_unverifiable():
    result = validate("两日主力累计净流入8亿元。")
    assert result["hard_guard"]["blocked"] is False
    assert "r0_net" in result["unverifiable_fields"]


def test_unknown_symbol_context_keeps_legacy_code_attribution():
    # Omitted optional symbol must not invent a target security.
    assert validate("600519.SH今日主力净流入3亿元。")["status"] == "mismatch"


@pytest.mark.parametrize("text", [
    "600519.SH今日主力净流入3亿元。",
    "贵州茅台今日主力净流入3亿元。",
    "可比公司主力净流入3亿元。",
    "同行主力净流入3亿元。",
    "龙头主力净流入3亿元。",
    "银行板块今日上涨，主力净流入3亿元。",
    "青蒿素概念上涨，主力净流入3亿元。",
])
def test_other_security_or_sector_cannot_supply_target_value(text):
    result = validate(text, symbol=SYMBOL)
    assert result["hard_guard"]["blocked"] is False
    assert result["model_daily"] == {}


@pytest.mark.parametrize("text", [
    "600519.SH主力净流入3亿元，601398.SH今日主力净流入4亿元。",
    "贵州茅台主力净流入3亿元，工商银行今日主力净流入4亿元。",
    "600519.SH上涨，主力净流入3亿元，601398.SH今日主力净流入4亿元。",
    "银行板块主力净流入3亿元，601398.SH今日主力净流入4亿元。",
])
def test_named_target_contradiction_survives_other_stock_same_sentence(text):
    result = validate(text, symbol=SYMBOL)
    assert result["status"] == "mismatch"
    assert result["model_daily"]["r0_net"] == "4"


def test_other_stock_all_occurrences_removed_before_first_match_wins():
    text = "600519.SH主力净流入3亿元。贵州茅台主力净流入5亿元。工商银行今日主力净流出1.25亿元。"
    result = validate(text, symbol=SYMBOL)
    assert result["status"] == "matched"
    assert result["model_daily"] == {"r0_net": "-1.25"}


def test_main_force_inflow_amount_label_is_not_total_field():
    result = validate("今日主力净流入额：-1.25亿元。")
    assert result["status"] == "matched"
    assert result["model_daily"] == {"r0_net": "-1.25"}


def test_composition_by_component_has_explicit_owner():
    result = validate("主力净额由超大单（-3亿元）与大单合成，今日主力净额为-1.25亿元。")
    assert result["status"] == "matched"
    assert result["model_daily"] == {"r0_net": "-1.25"}


def test_closest_preceding_date_not_later_sector_date_owns_value():
    text = "9月21日主力净流入1亿元、9月23日银行板块净流入5亿元。"
    records = [record("1", "2026-09-21"), record("8", "2026-09-23"), record("-1.25")]
    result = validate(text, records=records, symbol=SYMBOL)
    assert result["status"] == "matched"
    assert result["model_dated"] == {"r0_net": "1"}


def test_approximate_interval_uses_its_own_mention_context():
    records = [record("1.004", "2026-09-23"), record("2.008")]
    text = "前两日数据合计3.012亿元。两日主力累计净流入约3亿元。"
    result = validate(text, records=records)
    assert result["status"] == "validation_warning"
    assert result["hard_guard"]["blocked"] is False


def test_cumulative_extractors_share_sign_and_condition_attribution():
    assert extract_model_totals("两日主力累计净额−3亿元。") == {"r0_net": "-3"}
    assert extract_model_totals("若两日主力累计净流入3亿元，则确认。") == {}


def test_explicit_window_with_missing_start_record_is_unverifiable():
    records = [record("1", "2026-09-20"), record("2")]
    result = validate("9月23日至24日两日主力累计净流入8亿元。", records=records)
    assert result["hard_guard"]["blocked"] is False
    assert "r0_net" in result["unverifiable_fields"]


def test_named_unknown_security_can_use_prose_code_pair_without_lookup():
    text = "合成甲公司（601398.SH）主力净流出1.25亿元。合成乙公司（600001.SH）主力净流入3亿元。"
    assert validate(text, symbol=SYMBOL)["status"] == "matched"


def test_daily_extractor_retains_unscoped_actual_values():
    assert extract_model_daily_values("今日主力净流出1.25亿元。") == {"r0_net": "-1.25"}
    assert _extract_model_mentions(None) == []
