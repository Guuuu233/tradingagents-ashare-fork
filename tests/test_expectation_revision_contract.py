"""Tests for E-04 Expectation Revision Contract (Issue 01a099d9, DAV-869).

Covers all 12 Red-Team verification scenarios:
1. Title, URL, or PDF hash only, no body metrics -> cannot generate actual numbers (e.g. gross margin/revenue).
2. Traceable publication with unobtained content -> publication traceable, actual/baseline/revision as gap.
3. Actual financial report period mismatch with forecast period -> cannot substitute.
4. Missing prior baseline -> revision cannot have numeric delta or percentage; must be qualitative/gap.
5. Four distinguishable baseline types (management_guidance, consensus_expectation, prior_self_forecast, implicit_model); missing source cannot masquerade.
6. Incompatible unit, report period, metric, or as-of between actual and baseline -> prohibits numeric revision.
7. Beat/Miss ("超预期/不及预期") valid only when comparable baseline exists; otherwise unknown/gap.
8. "Priced in" must remain unknown without traceable market price-volume evidence.
9. double_count_guard prevents duplicate voting when already accounted for or when status is unknown.
10. Future date, unparseable date, provider failure, or unavailable content -> enter gap, cannot serve as directional evidence.
11. Research manager consumes only structured fields, cannot fabricate from free text; trace and result readback consistent.
12. Semantic integrity and trace preservation: existing analyst, compliance, and research manager contracts preserved.
"""

from __future__ import annotations

import copy
import json
import pytest

from tradingagents.agents.analysts.news_analyst import (
    BASELINE_CONSENSUS_EXPECTATION,
    BASELINE_IMPLICIT_MODEL,
    BASELINE_MANAGEMENT_GUIDANCE,
    BASELINE_NONE,
    BASELINE_PRIOR_SELF_FORECAST,
    CONTENT_HASHED,
    CONTENT_NOT_ATTEMPTED,
    CONTENT_NOT_OBTAINED,
    CONTENT_QUALIFIED,
    CONTENT_UNAVAILABLE,
    DOUBLE_COUNT_ACCOUNTED_FOR,
    DOUBLE_COUNT_NOT_ACCOUNTED_FOR,
    DOUBLE_COUNT_UNKNOWN,
    EVENT_TYPE_EVENT,
    EVENT_TYPE_FUNDAMENTAL,
    EVENT_TYPE_NONE,
    PRICED_IN_NOT_SUPPORTED,
    PRICED_IN_SUPPORTED,
    PRICED_IN_UNKNOWN,
    REVISION_GAP,
    REVISION_NUMERIC,
    REVISION_QUALITATIVE,
    STATUS_AVAILABLE,
    STATUS_GAP,
    STATUS_NOT_APPLICABLE,
    STATUS_PARTIAL,
    build_news_expectation_revision,
    make_default_expectation_revision,
    make_json_safe,
    validate_expectation_revision,
)
from tradingagents.agents.analysts.fundamentals_analyst import (
    build_fundamentals_expectation_revision,
    _extract_structured_actual,
    _extract_baseline_and_revision,
)
from tradingagents.agents.managers.research_manager import (
    _RELATION_PROMPT_REWRITES,
    _RELATION_PROMPT_FORBIDDEN,
    RelationPromptGuardError,
    apply_manager_double_count_guard,
    extract_expectation_revisions_from_traces,
    format_expectation_revisions_for_prompt,
    validate_manager_expectation_revision_consumption,
)
from tradingagents.prompts import get_prompt


# ==============================================================================
# 1. 只有标题、URL 或 PDF 字节 hash，没有正文指标：不得生成毛利率/收入等 actual 数字
# ==============================================================================

def test_case_1_news_only_title_url_or_pdf_hash_no_actual_numbers():
    """News with only headline, URL, or hash cannot fabricate financial actuals."""
    evidence_pool = {
        "news_items": [
            {
                "title": "某公司发布年度报告摘要及毛利率披露",
                "url": "https://example.com/announcement/12345",
                "content_hash": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "content_status": "hashed",
                "publish_time": "2025-05-10",
                "source": "cninfo",
            }
        ]
    }
    er = build_news_expectation_revision(evidence_pool, cutoff_date="2025-05-15")

    # Actual must be a gap and value must be None
    assert er["actual"]["value"] is None
    assert er["actual"]["status"] == STATUS_GAP
    assert er["actual"]["metric"] is None
    assert er["revision"]["type"] in (REVISION_GAP, REVISION_QUALITATIVE)
    assert er["revision"]["value"] is None
    assert er["revision"]["percent"] is None
    assert "content_not_obtained" in er["gaps"]

    # Must pass validation as a clean gap contract
    is_valid, violations = validate_expectation_revision(er, cutoff_date="2025-05-15")
    assert is_valid, f"Validation failed: {violations}"


def test_case_1_fundamentals_incomplete_elements_cannot_generate_actual_value():
    """Fundamentals without all 5 required elements cannot populate actual.value."""
    # Only period and metric, no value, unit, or as_of
    outputs = {
        "fundamentals": "2024Q2 营业收入披露敬请关注",
        "income_statement": "无数据",
    }
    actual, gaps = _extract_structured_actual(outputs, current_date="2025-05-15")
    assert actual["value"] is None
    assert actual["status"] == STATUS_GAP
    assert "actual_incomplete_elements" in gaps


def test_case_1_validator_rejects_actual_numbers_when_text_not_obtained():
    """Validator strictly rejects actual.value != None when content is hashed/unavailable."""
    er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_GAP)
    er["publication"]["content_qualification"] = CONTENT_HASHED
    er["publication"]["source_hash"] = "sha256:abcd1234ef56"
    er["actual"] = {
        "status": STATUS_AVAILABLE,
        "metric": "毛利率",
        "value": 35.5,
        "unit": "%",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
        "source": "fake",
    }
    is_valid, violations = validate_expectation_revision(er)
    assert not is_valid
    assert any("full text not obtained" in v for v in violations)


# ==============================================================================
# 2. 有发布时间和 source hash 但正文未取得：publication 可追溯，actual/baseline/revision 为 gap
# ==============================================================================

def test_case_2_traceable_publication_with_unobtained_content_leaves_actual_baseline_revision_as_gap():
    """Publication is traceable via source and hash, while financial columns remain gaps."""
    evidence_pool = {
        "news_items": [
            {
                "title": "重大合同中标提示性公告",
                "publish_time": "2025-05-10 18:30:00",
                "source": "巨潮资讯网",
                "source_hash": "sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069",
                "content_status": "hashed",
            }
        ]
    }
    er = build_news_expectation_revision(evidence_pool, cutoff_date="2025-05-15")

    # Publication is properly tracked and traceable
    assert er["publication"]["publish_time"] == "2025-05-10 18:30:00"
    assert er["publication"]["source"] == "巨潮资讯网"
    assert er["publication"]["source_hash"] == "sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069"
    assert er["publication"]["content_qualification"] in (CONTENT_HASHED, CONTENT_NOT_OBTAINED)

    # Actual, baseline, revision MUST remain gaps
    assert er["actual"]["status"] == STATUS_GAP
    assert er["actual"]["value"] is None
    assert er["baseline"]["type"] == BASELINE_NONE
    assert er["baseline"]["value"] is None
    assert er["revision"]["type"] == REVISION_GAP
    assert er["revision"]["value"] is None
    assert er["revision"]["percent"] is None
    assert er["status"] == STATUS_GAP

    is_valid, violations = validate_expectation_revision(er, cutoff_date="2025-05-15")
    assert is_valid, f"Validation failed: {violations}"


# ==============================================================================
# 3. 实际财报期间与业绩预测期间不同：不得互相替代
# ==============================================================================

def test_case_3_financial_actual_period_mismatch_with_forecast_cannot_substitute():
    """Actual report period (e.g. 2024H1) cannot substitute for forecast period (e.g. 2024FY)."""
    actual = {
        "status": STATUS_AVAILABLE,
        "metric": "净利润",
        "value": 5000.0,
        "unit": "万元",
        "report_period": "2024H1",
        "as_of": "2024-06-30",
        "source": "fundamentals",
    }
    pool = {
        "earnings_forecast": {
            "source": "业绩预告",
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "metric": "净利润",
            "value": 12000.0,
            "unit": "万元",
            "period": "2024FY",  # Different from 2024H1!
            "as_of": "2024-12-31",
        }
    }
    baseline, revision, gaps = _extract_baseline_and_revision(actual, pool, current_date="2025-05-15")

    assert "period_mismatch" in gaps
    assert revision["type"] == REVISION_GAP
    assert revision["value"] is None
    assert revision["percent"] is None
    assert "不得互相替代" in revision["detail"]


def test_case_3_validator_rejects_numeric_revision_with_period_mismatch():
    """Validator strictly rejects numeric revision when actual period != baseline period."""
    er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
    er["actual"] = {
        "status": STATUS_AVAILABLE,
        "metric": "营业收入",
        "value": 100.0,
        "unit": "亿元",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    er["baseline"] = {
        "type": BASELINE_CONSENSUS_EXPECTATION,
        "source": "wind_consensus",
        "metric": "营业收入",
        "value": 90.0,
        "unit": "亿元",
        "report_period": "2024Q3",  # Mismatch!
        "as_of": "2024-09-30",
    }
    er["revision"] = {
        "type": REVISION_NUMERIC,
        "value": 10.0,
        "percent": 11.11,
        "unit": "亿元",
        "direction": "positive",
        "detail": "超预期",
    }
    is_valid, violations = validate_expectation_revision(er)
    assert not is_valid
    assert any("report period mismatch" in v for v in violations)


# ==============================================================================
# 4. 没有旧基线：revision 不能出现百分比或数值幅度，只能 qualitative/gap
# ==============================================================================

def test_case_4_missing_baseline_revision_cannot_have_numeric_delta_or_percent():
    """When baseline is missing, revision must be qualitative or gap, never numeric delta."""
    actual = {
        "status": STATUS_AVAILABLE,
        "metric": "营业收入",
        "value": 120.0,
        "unit": "亿元",
        "report_period": "2024H1",
        "as_of": "2024-06-30",
    }
    # No baseline in pool
    baseline, revision, gaps = _extract_baseline_and_revision(actual, pool={}, current_date="2025-05-15")

    assert baseline["type"] == BASELINE_NONE
    assert baseline["value"] is None
    assert revision["type"] == REVISION_QUALITATIVE
    assert revision["value"] is None
    assert revision["percent"] is None
    assert "无旧基线" in revision["detail"]


def test_case_4_validator_rejects_numeric_delta_or_percent_without_baseline():
    """Validator strictly rejects numeric values or percentages when baseline type is 'none'."""
    er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_PARTIAL)
    er["actual"] = {
        "status": STATUS_AVAILABLE,
        "metric": "营业收入",
        "value": 120.0,
        "unit": "亿元",
        "report_period": "2024H1",
        "as_of": "2024-06-30",
    }
    er["baseline"] = {
        "type": BASELINE_NONE,
        "source": None,
        "value": None,
    }
    # Violating attempt: providing numeric delta or percent without baseline
    er["revision"] = {
        "type": REVISION_QUALITATIVE,
        "value": 15.0,  # Illegal!
        "percent": 14.2,  # Illegal!
        "unit": "亿元",
        "direction": "positive",
        "detail": "非法附带数值幅度",
    }
    is_valid, violations = validate_expectation_revision(er)
    assert not is_valid
    assert any("revision.value must be None" in v for v in violations)
    assert any("revision.percent must be None" in v for v in violations)


# ==============================================================================
# 5. 管理层指引、一致预期、自身旧预测、隐含模型四类 baseline 必须可区分，来源缺失不能降级冒充另一类
# ==============================================================================

def test_case_5_distinguishable_baseline_types_and_source_requirement():
    """The four baseline types are strictly distinguished; missing source cannot masquerade."""
    baseline_types = [
        BASELINE_MANAGEMENT_GUIDANCE,
        BASELINE_CONSENSUS_EXPECTATION,
        BASELINE_PRIOR_SELF_FORECAST,
        BASELINE_IMPLICIT_MODEL,
    ]
    # Ensure all 4 are distinct
    assert len(set(baseline_types)) == 4

    for b_type in baseline_types:
        er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
        er["actual"] = {
            "status": STATUS_AVAILABLE,
            "metric": "营业收入",
            "value": 100.0,
            "unit": "亿元",
            "report_period": "2024Q2",
            "as_of": "2024-06-30",
        }
        # Baseline with missing source
        er["baseline"] = {
            "type": b_type,
            "source": "",  # Empty source!
            "metric": "营业收入",
            "value": 90.0,
            "unit": "亿元",
            "period": "2024Q2",
            "report_period": "2024Q2",
            "as_of": "2024-06-30",
        }
        er["revision"] = {
            "type": REVISION_GAP,
            "value": None,
            "percent": None,
            "unit": None,
            "direction": "gap",
            "detail": "source missing",
        }
        is_valid, violations = validate_expectation_revision(er)
        assert not is_valid
        assert any("source is missing or empty" in v for v in violations)

        # Baseline with valid source passes
        er["baseline"]["source"] = f"verified_source_for_{b_type}"
        er["publication"]["content_qualification"] = CONTENT_QUALIFIED
        is_valid_with_source, violations_with_source = validate_expectation_revision(er)
        assert is_valid_with_source, f"Failed for {b_type}: {violations_with_source}"


def test_case_5_builder_drops_baseline_without_source_to_none_with_gap():
    """Builder sets baseline to none and records gap if explicit baseline lacks source."""
    actual = {
        "status": STATUS_AVAILABLE,
        "metric": "营业收入",
        "value": 100.0,
        "unit": "亿元",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    pool = {
        "baseline": {
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "source": None,  # Missing source
            "metric": "营业收入",
            "value": 90.0,
            "unit": "亿元",
            "period": "2024Q2",
        }
    }
    baseline, revision, gaps = _extract_baseline_and_revision(actual, pool)
    assert baseline["type"] == BASELINE_NONE
    assert "baseline_source_missing" in gaps


# ==============================================================================
# 6. actual 与 baseline 的单位、报告期、as-of 任一不一致：不得 numeric
# ==============================================================================

@pytest.mark.parametrize(
    "mismatch_field, actual_mod, baseline_mod, expected_gap",
    [
        ("unit", {"unit": "亿元"}, {"unit": "USD"}, "unit_mismatch"),
        ("unit_unconverted", {"unit": "万元"}, {"unit": "亿元"}, "unit_mismatch"),
        ("metric", {"metric": "营业收入"}, {"metric": "净利润"}, "metric_mismatch"),
        ("report_period", {"report_period": "2024Q2"}, {"period": "2024Q1"}, "period_mismatch"),
    ],
)
def test_case_6_actual_and_baseline_incompatible_fields_prohibit_numeric_revision(
    mismatch_field, actual_mod, baseline_mod, expected_gap
):
    """Incompatibility in unit, metric, or report_period prohibits numeric revision."""
    actual = {
        "status": STATUS_AVAILABLE,
        "metric": "营业收入",
        "value": 100.0,
        "unit": "亿元",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    actual.update(actual_mod)

    baseline_data = {
        "type": BASELINE_CONSENSUS_EXPECTATION,
        "source": "wind",
        "metric": "营业收入",
        "value": 90.0,
        "unit": "亿元",
        "period": "2024Q2",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    baseline_data.update(baseline_mod)
    pool = {"baseline": baseline_data}

    baseline, revision, gaps = _extract_baseline_and_revision(actual, pool)
    assert expected_gap in gaps
    assert revision["type"] == REVISION_GAP
    assert revision["value"] is None
    assert revision["percent"] is None


def test_case_6_validator_rejects_numeric_revision_on_unit_or_metric_mismatch():
    """Validator strictly rejects numeric revision if unit or metric mismatch."""
    er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
    er["actual"] = {
        "status": STATUS_AVAILABLE,
        "metric": "净利润",
        "value": 50.0,
        "unit": "万元",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    er["baseline"] = {
        "type": BASELINE_CONSENSUS_EXPECTATION,
        "source": "analyst_consensus",
        "metric": "净利润",
        "value": 50.0,
        "unit": "亿元",  # Unit mismatch!
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    er["revision"] = {
        "type": REVISION_NUMERIC,
        "value": 0.0,
        "percent": 0.0,
        "unit": "万元",
        "direction": "neutral",
        "detail": "数值持平",
    }
    is_valid, violations = validate_expectation_revision(er)
    assert not is_valid
    assert any("unit mismatch" in v for v in violations)


# ==============================================================================
# 7. “超预期/不及预期”只有在可比基线真实存在时才能成立，否则 unknown/gap
# ==============================================================================

def test_case_7_beat_or_miss_only_valid_when_comparable_baseline_exists():
    """Beat or miss assertions require a valid comparable baseline."""
    er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_GAP)
    er["actual"] = {
        "status": STATUS_GAP,
        "metric": None,
        "value": None,
        "unit": None,
        "report_period": None,
        "as_of": None,
    }
    er["baseline"] = {
        "type": BASELINE_NONE,
        "source": None,
        "value": None,
    }
    # Illegal claim: claiming '超预期' when baseline is none
    er["revision"] = {
        "type": REVISION_QUALITATIVE,
        "value": None,
        "percent": None,
        "unit": None,
        "direction": "beat",
        "detail": "公司业绩超预期增长",
    }
    is_valid, violations = validate_expectation_revision(er)
    assert not is_valid
    assert any("cannot assert beat/miss" in v for v in violations)


def test_case_7_manager_consumption_catches_unsubstantiated_beat_miss():
    """Manager verdict validation detects beat/miss claims unsupported by analyst traces."""
    expectation_revisions = [
        make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_GAP)
    ]
    # Manager asserts beat in reason while traces have baseline=none
    manager_verdict = {
        "direction": "BULLISH",
        "reason": "二季度净利润大幅超预期，业绩超预期兑现",
    }
    is_valid, violations = validate_manager_expectation_revision_consumption(
        manager_verdict, "full text", expectation_revisions
    )
    assert not is_valid
    assert any("超预期" in v for v in violations)


# ==============================================================================
# 8. “已定价”没有可回溯证据时必须 unknown，不得由标题或模型措辞推断为事实
# ==============================================================================

def test_case_8_priced_in_must_be_unknown_without_traceable_evidence():
    """Priced-in must remain unknown; setting supported without evidence is rejected."""
    er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_AVAILABLE)
    er["actual"] = {
        "status": STATUS_AVAILABLE,
        "metric": "中标金额",
        "value": 5.0,
        "unit": "亿元",
        "report_period": "2025Q2",
        "as_of": "2025-05-10",
    }
    er["baseline"] = {
        "type": BASELINE_NONE,
        "source": None,
        "value": None,
    }
    er["revision"] = {
        "type": REVISION_QUALITATIVE,
        "value": None,
        "percent": None,
        "unit": None,
        "direction": "neutral",
        "detail": "中标定性利好",
    }

    # Attempt to set priced_in to supported without evidence
    er["priced_in"] = {
        "status": PRICED_IN_SUPPORTED,
        "evidence": None,  # No traceable evidence!
    }
    is_valid, violations = validate_expectation_revision(er)
    assert not is_valid
    assert any("evidence is missing; must be 'unknown'" in v for v in violations)

    # With traceable market reaction evidence, it passes
    er["priced_in"]["evidence"] = "2025-05-11 09:35 股价高开3%后成交量急剧萎缩回落，已充分反映利好"
    er["publication"]["content_qualification"] = CONTENT_QUALIFIED
    is_valid_with_ev, violations_with_ev = validate_expectation_revision(er)
    assert is_valid_with_ev, f"Validation failed: {violations_with_ev}"


# ==============================================================================
# 9. 同一事件已经进入 forecast/事件栏位时，double_count_guard 阻止再次加票；无法判断不加票
# ==============================================================================

def test_case_9_double_count_guard_prevents_duplicate_voting():
    """double_count_guard prevents double voting when accounted_for or unknown."""
    # When accounted_for: prevent_double_voting MUST be True
    er_accounted = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_AVAILABLE)
    er_accounted["double_count_guard"] = {
        "status": DOUBLE_COUNT_ACCOUNTED_FOR,
        "prevent_double_voting": False,  # Illegal violation!
    }
    is_valid, violations = validate_expectation_revision(er_accounted)
    assert not is_valid
    assert any("prevent_double_voting must be True" in v for v in violations)

    # When unknown: prevent_double_voting MUST also be True (cannot determine -> do not add vote!)
    er_unknown = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_AVAILABLE)
    er_unknown["double_count_guard"] = {
        "status": DOUBLE_COUNT_UNKNOWN,
        "prevent_double_voting": False,  # Illegal violation!
    }
    is_valid, violations = validate_expectation_revision(er_unknown)
    assert not is_valid
    assert any("prevent_double_voting must be True" in v for v in violations)

    # Only when not_accounted_for can prevent_double_voting be False
    er_not_accounted = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_AVAILABLE)
    er_not_accounted["double_count_guard"] = {
        "status": DOUBLE_COUNT_NOT_ACCOUNTED_FOR,
        "prevent_double_voting": False,
    }
    is_valid, violations = validate_expectation_revision(er_not_accounted)
    assert is_valid, f"Validation failed: {violations}"


# ==============================================================================
# 10. 未来日期、不可解析日期、provider failure 或正文 unavailable 必须进入 gap，不能作为方向证据
# ==============================================================================

def test_case_10_future_date_enters_gap():
    """Future publication date beyond cutoff date is blocked and recorded in gaps."""
    evidence_pool = {
        "news_items": [
            {
                "title": "未来发生的新闻",
                "publish_time": "2025-06-01 10:00:00",  # Future!
                "source": "future_source",
                "content_status": "qualified",
                "body": "未来财报实际营业收入100亿元",
            }
        ]
    }
    er = build_news_expectation_revision(evidence_pool, cutoff_date="2025-05-15")
    assert "future_date" in er["gaps"]
    assert er["actual"]["value"] is None
    assert er["status"] == STATUS_GAP


def test_case_10_unparseable_date_enters_gap():
    """Unparseable publication date is recorded in gaps."""
    evidence_pool = {
        "news_items": [
            {
                "title": "无效日期新闻",
                "publish_time": "invalid_date_format",
                "source": "test",
            }
        ]
    }
    er = build_news_expectation_revision(evidence_pool, cutoff_date="2025-05-15")
    assert "invalid_publish_time" in er["gaps"]


def test_case_10_provider_failure_enters_gap():
    """Provider failure in fundamentals or news enters gap."""
    # Fundamentals provider failure
    outputs = {
        "fundamentals": "获取失败: 网络超时 HTTP 504",
        "income_statement": "接口异常",
    }
    actual, gaps = _extract_structured_actual(outputs, current_date="2025-05-15")
    assert actual["status"] == STATUS_GAP
    assert actual["value"] is None
    assert any("provider_failure" in g for g in gaps)

    # News provider failure
    er = build_news_expectation_revision(
        {"event_coverage": {"failure_reason": "provider_error"}},
        cutoff_date="2025-05-15",
    )
    assert "provider_failure" in er["gaps"]
    assert er["status"] == STATUS_GAP


def test_case_10_content_unavailable_enters_gap():
    """Content marked unavailable is recorded in gaps and yields no actual value."""
    evidence_pool = {
        "news_items": [
            {
                "title": "公告正文丢失",
                "publish_time": "2025-05-10",
                "source": "sse",
                "content_status": "unavailable",
            }
        ]
    }
    er = build_news_expectation_revision(evidence_pool, cutoff_date="2025-05-15")
    assert "content_not_obtained" in er["gaps"]
    assert er["actual"]["value"] is None
    assert er["actual"]["status"] == STATUS_GAP


# ==============================================================================
# 11. research manager 只能消费结构化栏位，不能从自由文本补造数值；trace 与最终结果回读一致
# ==============================================================================

def test_case_11_research_manager_structured_consumption_and_readback_consistency():
    """Research manager strictly consumes expectation_revision and preserves trace readback."""
    news_er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_GAP)
    fund_er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
    fund_er["actual"] = {
        "status": STATUS_AVAILABLE,
        "metric": "归母净利润",
        "value": 15.2,
        "unit": "亿元",
        "report_period": "2024FY",
        "as_of": "2024-12-31",
    }
    fund_er["baseline"] = {
        "type": BASELINE_MANAGEMENT_GUIDANCE,
        "source": "业绩预告",
        "metric": "归母净利润",
        "value": 14.0,
        "unit": "亿元",
        "report_period": "2024FY",
        "as_of": "2024-12-31",
    }
    fund_er["revision"] = {
        "type": REVISION_NUMERIC,
        "value": 1.2,
        "percent": 8.57,
        "unit": "亿元",
        "direction": "positive",
        "detail": "实际值较业绩预告高出1.2亿元",
    }

    traces = [
        {"analyst_name": "news_analyst", "agent": "news_analyst", "expectation_revision": news_er},
        {"analyst_name": "fundamentals_analyst", "agent": "fundamentals_analyst", "expectation_revision": fund_er},
    ]

    # Extraction extracts structured dicts
    extracted = extract_expectation_revisions_from_traces(traces)
    assert len(extracted) == 2
    assert "news" in extracted
    assert "fundamentals" in extracted
    assert extracted["fundamentals"]["revision"]["type"] == REVISION_NUMERIC

    # Formatting produces auditable context
    prompt_context = format_expectation_revisions_for_prompt(extracted, language="zh")
    assert "归母净利润" in prompt_context
    assert "15.2" in prompt_context
    assert "8.57%" in prompt_context

    # Validation catches manager hallucinating numbers when actual is a gap
    bad_manager_verdict = {
        "direction": "BULLISH",
        "reason": "新闻显示营业收入达到9999亿元",
    }
    # For news, actual is gap:
    is_valid, violations = validate_manager_expectation_revision_consumption(
        bad_manager_verdict, "raw", [news_er]
    )
    assert not is_valid
    assert any("营业收入" in v for v in violations)


# ==============================================================================
# 12. 现有新闻、基本面、财务期间合规、研究经理和 E-02/E-03 相关测试保持原语义
# ==============================================================================

def test_case_12_json_safety_and_langgraph_trace_preservation():
    """LangGraph state serialization safety: trace items with expectation_revision serialize cleanly."""
    er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
    er["actual"] = {
        "status": STATUS_AVAILABLE,
        "metric": "营收",
        "value": 100.0,
        "unit": "亿元",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    trace_item = {
        "analyst_name": "fundamentals_analyst",
        "verdict": {"direction": "BULLISH", "reason": "稳健"},
        "expectation_revision": er,
    }
    safe_trace = make_json_safe(trace_item)
    # Must be JSON serializable without error
    dumped = json.dumps(safe_trace, ensure_ascii=False)
    loaded = json.loads(dumped)
    assert loaded["expectation_revision"]["actual"]["value"] == 100.0
    assert loaded["expectation_revision"]["status"] == STATUS_AVAILABLE


def test_case_12_prompt_guard_invariants_preserved_zh_and_en():
    """Prompt guard rewrites in research manager run cleanly without raising RelationPromptGuardError."""
    from tradingagents.prompts.zh import PROMPTS as ZH_PROMPTS
    from tradingagents.prompts.en import PROMPTS as EN_PROMPTS

    # Test Chinese prompt rewrite
    zh_prompt = ZH_PROMPTS["research_manager_prompt"]
    rewritten_zh = zh_prompt
    for old, new, expected_count in _RELATION_PROMPT_REWRITES["zh"]:
        actual_count = rewritten_zh.count(old)
        assert actual_count == expected_count, f"ZH count mismatch for {old[:20]}: {actual_count} != {expected_count}"
        rewritten_zh = rewritten_zh.replace(old, new)
    # Ensure no forbidden terms in ZH
    assert not _RELATION_PROMPT_FORBIDDEN["zh"].search(rewritten_zh), "Forbidden token survived in ZH prompt"

    # Test English prompt rewrite
    en_prompt = EN_PROMPTS["research_manager_prompt"]
    rewritten_en = en_prompt
    for old, new, expected_count in _RELATION_PROMPT_REWRITES["en"]:
        actual_count = rewritten_en.count(old)
        assert actual_count == expected_count, f"EN count mismatch for {old[:20]}: {actual_count} != {expected_count}"
        rewritten_en = rewritten_en.replace(old, new)
    # Ensure no forbidden terms in EN
    assert not _RELATION_PROMPT_FORBIDDEN["en"].search(rewritten_en), "Forbidden token survived in EN prompt"


# ==============================================================================
# 必须新增的负向测试 (DAV-871 / DAV-870 返修契约穿透验证)
# ==============================================================================

def test_negative_announcement_title_url_pdf_filename_cannot_generate_actual():
    """Negative test: headline, URL, PDF filename, or announcement text with numbers cannot generate actual."""
    # Scenario: free text mixing headline and URL with profit amount
    outputs = {
        "fundamentals": "公告标题: 关于2024Q2净利润5000万元业绩预告的澄清说明 http://cninfo.com.cn/2024Q2净利润5000万元.pdf",
        "income_statement": "无数据",
    }
    actual, gaps = _extract_structured_actual(outputs, current_date="2024-05-15")
    assert actual["value"] is None
    assert actual["status"] == STATUS_GAP
    assert actual["metric"] is None
    assert "actual_incomplete_elements" in gaps

    # Full build_fundamentals_expectation_revision must also yield gap and prohibited numeric revision
    er = build_fundamentals_expectation_revision(outputs, current_date="2024-05-15")
    assert er["actual"]["value"] is None
    assert er["actual"]["status"] == STATUS_GAP
    assert er["revision"]["type"] in (REVISION_GAP, REVISION_QUALITATIVE)
    assert er["revision"]["value"] is None
    assert er["revision"]["percent"] is None
    assert er["status"] == STATUS_GAP

    is_valid, violations = validate_expectation_revision(er, cutoff_date="2024-05-15")
    assert is_valid, f"Contract validation failed: {violations}"


def test_negative_missing_as_of_or_future_as_of_cannot_generate_actual_or_numeric():
    """Negative test: missing legal as_of date or future as_of date cannot generate actual or numeric revision."""
    # A. Future as_of date (e.g. 2024-09-30 > current_date 2024-05-15)
    outputs_future = {
        "structured_financials": {
            "source": "structured_financials",
            "metric": "营业收入",
            "value": 150.0,
            "unit": "亿元",
            "report_period": "2024Q3",
            "as_of": "2024-09-30",
        }
    }
    act_future, gaps_future = _extract_structured_actual(outputs_future, current_date="2024-05-15")
    assert act_future["value"] is None
    assert act_future["status"] == STATUS_GAP
    assert act_future["as_of"] == "2024-09-30"  # Preserved as future audit trail, NOT rewritten to current_date
    assert "future_date" in gaps_future

    # Even with baseline forecast, numeric revision is strictly blocked
    pool_with_forecast = {
        "baseline": {
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "source": "业绩指引",
            "metric": "营业收入",
            "value": 140.0,
            "unit": "亿元",
            "report_period": "2024Q3",
            "as_of": "2024-09-30",
        }
    }
    er_future = build_fundamentals_expectation_revision(outputs_future, pool=pool_with_forecast, current_date="2024-05-15")
    assert er_future["actual"]["value"] is None
    assert er_future["actual"]["status"] == STATUS_GAP
    assert er_future["revision"]["type"] == REVISION_GAP
    assert er_future["revision"]["value"] is None
    assert er_future["revision"]["percent"] is None
    assert "future_date" in er_future["gaps"]

    # B. Missing as_of date (cannot derive standard YYYY-MM-DD)
    outputs_no_asof = {
        "structured_financials": {
            "metric": "营业收入",
            "value": 100.0,
            "unit": "亿元",
            "report_period": "2024",
            "as_of": "invalid-date",
        }
    }
    act_no_asof, gaps_no_asof = _extract_structured_actual(outputs_no_asof, current_date="2024-05-15")
    assert act_no_asof["value"] is None
    assert act_no_asof["status"] == STATUS_GAP
    assert "invalid_as_of" in gaps_no_asof


def test_negative_earnings_forecast_without_source_or_type_cannot_masquerade_as_baseline():
    """Negative test: earnings forecast lacking real source or type must be degraded to none and block numeric revision."""
    actual = {
        "status": STATUS_AVAILABLE,
        "metric": "净利润",
        "value": 100.0,
        "unit": "亿元",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    # Forecast missing both source and type
    pool_unattributed = {
        "earnings_forecast": {
            "metric": "净利润",
            "value": 80.0,
            "unit": "亿元",
            "report_period": "2024Q2",
        }
    }
    baseline, revision, gaps = _extract_baseline_and_revision(actual, pool_unattributed, current_date="2024-07-01")
    assert baseline["type"] == BASELINE_NONE
    assert baseline["value"] is None
    assert "baseline_source_missing" in gaps
    assert revision["type"] == REVISION_QUALITATIVE
    assert revision["value"] is None
    assert revision["percent"] is None

    # Explicit baseline missing source
    pool_explicit_no_source = {
        "baseline": {
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "source": "",
            "metric": "净利润",
            "value": 80.0,
            "unit": "亿元",
            "report_period": "2024Q2",
            "as_of": "2024-06-30",
        }
    }
    base2, rev2, gaps2 = _extract_baseline_and_revision(actual, pool_explicit_no_source, current_date="2024-07-01")
    assert base2["type"] == BASELINE_NONE
    assert base2["value"] is None
    assert "baseline_source_missing" in gaps2
    assert rev2["value"] is None


def test_negative_compliance_cumulative_labeled_as_single_quarter_blocks_numeric_revision():
    """Negative test: compliance reporting cumulative labeled as single quarter strictly blocks numeric revision."""
    outputs = {
        "structured_financials": {
            "metric": "净利润",
            "value": 100.0,
            "unit": "亿元",
            "report_period": "2024Q2",
            "as_of": "2024-06-30",
        }
    }
    pool = {
        "baseline": {
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "source": "业绩预告",
            "metric": "净利润",
            "value": 50.0,
            "unit": "亿元",
            "report_period": "2024Q2",
            "as_of": "2024-06-30",
        }
    }
    # Compliance detects that reported Q2 value was actually H1 cumulative mislabeled as Q2
    compliance = {
        "status": "violations_found",
        "violations": [
            {
                "kind": "cumulative_labeled_as_single_quarter",
                "quoted_text": "2024Q2净利润100亿元",
                "statement": "income_statement",
                "field": "net_profit",
                "reported_label": "Q2",
                "input_period_kind": "half_year_cumulative",
                "input_value": 100.0,
            }
        ],
    }
    er = build_fundamentals_expectation_revision(outputs, pool=pool, current_date="2024-07-01", compliance=compliance)
    # Numeric revision MUST be blocked into gap
    assert er["revision"]["type"] == REVISION_GAP
    assert er["revision"]["value"] is None
    assert er["revision"]["percent"] is None
    assert "compliance_violation:cumulative_labeled_as_single_quarter" in er["gaps"]
    assert "period_mismatch" in er["gaps"]
    assert er["status"] == STATUS_GAP


def test_negative_manager_raw_response_hallucinating_numbers_beat_miss_or_priced_in_is_blocked():
    """Negative test: manager raw_response hallucinating numbers, beat/miss, or priced-in is blocked."""
    fund_er_gap = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_GAP)
    news_er_unknown = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_GAP)

    # 1. raw_response contains "超预期" when baseline is none (even if reason is neutral)
    manager_verdict = {"direction": "BULLISH", "reason": "维持中性研判"}
    raw_response_beat = "基于综合推演，公司二季度业绩大幅超预期，建议适度建仓"
    is_valid, violations = validate_manager_expectation_revision_consumption(
        manager_verdict, raw_response_beat, [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("超预期" in v for v in violations)

    # 2. raw_response contains "已定价" when priced_in is unknown
    raw_response_pi = "【投资建议】当前市场已充分定价该项合同利好，不建议跟风追高"
    is_valid_pi, violations_pi = validate_manager_expectation_revision_consumption(
        manager_verdict, raw_response_pi, [fund_er_gap, news_er_unknown]
    )
    assert not is_valid_pi
    assert any("已定价" in v for v in violations_pi)

    # 3. raw_response hallucinates financial numbers when actual is a gap
    raw_response_num = "【财务拆解】本期公司实现营业收入达到250.5亿元，表现亮眼"
    is_valid_num, violations_num = validate_manager_expectation_revision_consumption(
        manager_verdict, raw_response_num, [fund_er_gap, news_er_unknown]
    )
    assert not is_valid_num
    assert any("营业收入" in v or "财务指标数值" in v for v in violations_num)

    # 4. End-to-end: consistency failure enters NO_TRADE path
    from tradingagents.agents.utils.decision_status import status_from_manager_verdict
    manager_verdict["consistency_check_passed"] = False
    manager_verdict["failed_checks"] = violations_num
    status = status_from_manager_verdict(manager_verdict)
    assert status.trade_action == "NO_TRADE"
    assert status.risk_status == "BLOCKED"


def test_negative_double_count_guard_blocks_duplicate_support_in_manager_consumption():
    """Negative test: double_count_guard in manager consumption gate actively blocks duplicate support and voting."""
    # Setup: both fundamentals and news cover the same event, double_count_guard active
    fund_er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
    fund_er["double_count_guard"] = {
        "status": DOUBLE_COUNT_ACCOUNTED_FOR,
        "prevent_double_voting": True,
        "description": "已在预测中计入",
    }
    news_er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_PARTIAL)
    news_er["double_count_guard"] = {
        "status": DOUBLE_COUNT_UNKNOWN,
        "prevent_double_voting": True,
        "description": "无法确证是否重复，阻止加票",
    }
    expectation_revisions = {"fundamentals": fund_er, "news": news_er}

    claims = [
        {"claim_id": "c1", "event_type": "fundamental", "claim_text": "公司业绩预告披露大幅增长"},
        {"claim_id": "c2", "event_type": "event", "claim_text": "新闻报道公司业绩预告大幅增长"},
    ]
    claim_cluster_metrics = {
        "independent_cluster_count": 2,
        "bull_cluster_count": 2,
        "bear_cluster_count": 0,
    }
    manager_verdict = {
        "direction": "BULLISH",
        "reason": "看多",
        "adopted_claim_ids": ["c1", "c2"],
        "excluded_evidence": [],
    }

    # Apply consumption gate
    metrics_out, verdict_out, _ = apply_manager_double_count_guard(
        claim_cluster_metrics=claim_cluster_metrics,
        expectation_revisions=expectation_revisions,
        claims=claims,
        manager_verdict=manager_verdict,
    )

    # 1. Duplicate vote is blocked from cluster metrics
    assert metrics_out["double_count_guard_active"] is True
    assert metrics_out["duplicate_voting_prevented"] is True
    assert metrics_out["independent_cluster_count"] == 1
    assert metrics_out["bull_cluster_count"] == 1

    # 2. Duplicate claim is stripped from manager verdict adopted claims into excluded_evidence
    assert verdict_out["adopted_claim_ids"] == ["c1"]
    assert any(e["claim_id"] == "c2" for e in verdict_out["excluded_evidence"])
    assert any("double_count_guard" in e["reason"] for e in verdict_out["excluded_evidence"])

    # 3. Manager raw_response claiming double voting is intercepted
    raw_response_double_voting = "基本面与新闻双重支持，提供额外支持加票"
    is_valid, violations = validate_manager_expectation_revision_consumption(
        manager_verdict, raw_response_double_voting, expectation_revisions
    )
    assert not is_valid
    assert any("double_count_guard" in v for v in violations)


def test_negative_missing_source_hash_retains_gap_without_fake_fingerprint_and_multi_news_retained():
    """Negative test: missing source_hash stays gap without fake fingerprint, and multiple news evidences are not lost."""
    # A. Fundamentals without real source_hash stays None and enters gap
    outputs_no_hash = {
        "structured_financials": {
            "metric": "净利润",
            "value": 100.0,
            "unit": "亿元",
            "report_period": "2024Q2",
            "as_of": "2024-06-30",
        }
    }
    er_fund = build_fundamentals_expectation_revision(outputs_no_hash, current_date="2024-07-01")
    assert er_fund["publication"]["source_hash"] is None
    assert "source_hash_missing" in er_fund["gaps"]

    # Faked "fin_" prefix is strictly rejected by contract validator
    fake_er = copy.deepcopy(er_fund)
    fake_er["publication"]["source_hash"] = "fin_2024Q2_2024-06-30"
    is_valid, violations = validate_expectation_revision(fake_er)
    assert not is_valid
    assert any("fin_" in v for v in violations)

    # B. Multiple news items retained without silent truncation
    evidence_pool = {
        "news_items": [
            {
                "title": "新闻一: 公司中标重大项目",
                "publish_time": "2025-05-10 10:00:00",
                "source": "证券时报",
                "source_hash": "sha256:1111111111111111",
                "content_status": "qualified",
            },
            {
                "title": "新闻二: 行业协会发布利好政策",
                "publish_time": "2025-05-11 11:00:00",
                "source": "上海证券报",
                "source_hash": "sha256:2222222222222222",
                "content_status": "qualified",
            },
            {
                "title": "新闻三: 核心股东增持计划完成",
                "publish_time": "2025-05-12 12:00:00",
                "source": "巨潮资讯网",
                "source_hash": "sha256:3333333333333333",
                "content_status": "qualified",
            },
        ]
    }
    er_news = build_news_expectation_revision(evidence_pool, cutoff_date="2025-05-15")
    assert er_news["publication"]["evidence_count"] == 3
    assert len(er_news["publication"]["evidences"]) == 3
    # Check all 3 sources and hashes are preserved
    sources = [e["source"] for e in er_news["publication"]["evidences"]]
    hashes = [e["source_hash"] for e in er_news["publication"]["evidences"]]
    assert sources == ["证券时报", "上海证券报", "巨潮资讯网"]
    assert hashes == ["sha256:1111111111111111", "sha256:2222222222222222", "sha256:3333333333333333"]


# ==============================================================================
# DAV-874 第二轮返修新增验收测试
# ==============================================================================

def test_negative_plain_free_text_amount_cannot_become_actual():
    """DAV-874 Item 1: Plain free text amount without structured records must stay gap."""
    # Plain text mentioning profit amount
    outputs = {
        "fundamentals": "2024Q2 净利润 5000万元，公司经营稳健",
        "income_statement": "无数据",
    }
    actual, gaps = _extract_structured_actual(outputs, current_date="2024-05-15")
    assert actual["value"] is None
    assert actual["status"] == STATUS_GAP
    assert actual["metric"] is None
    assert "actual_incomplete_elements" in gaps

    er = build_fundamentals_expectation_revision(outputs, current_date="2024-05-15")
    assert er["actual"]["value"] is None
    assert er["actual"]["status"] == STATUS_GAP
    assert er["revision"]["type"] in (REVISION_GAP, REVISION_QUALITATIVE)
    assert er["revision"]["value"] is None
    assert er["revision"]["percent"] is None
    assert er["status"] == STATUS_GAP

    is_valid, violations = validate_expectation_revision(er, cutoff_date="2024-05-15")
    assert is_valid, f"Validation failed: {violations}"


def test_negative_forecast_missing_or_illegal_or_future_as_of_blocks_numeric():
    """DAV-874 Item 2: Forecast lacking as_of, future as_of, or illegal as_of blocks numeric revision."""
    actual = {
        "status": STATUS_AVAILABLE,
        "metric": "净利润",
        "value": 100.0,
        "unit": "亿元",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    # 1. Missing as_of in forecast cannot backfill current_date
    pool_no_asof = {
        "earnings_forecast": {
            "source": "业绩预告",
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "metric": "净利润",
            "value": 80.0,
            "unit": "亿元",
            "report_period": "2024Q2",
        }
    }
    base1, rev1, gaps1 = _extract_baseline_and_revision(actual, pool_no_asof, current_date="2024-07-01")
    assert base1["type"] == BASELINE_NONE
    assert base1["value"] is None
    assert base1["as_of"] is None
    assert "baseline_as_of_missing" in gaps1 or "missing_as_of" in gaps1
    assert rev1["type"] != REVISION_NUMERIC
    assert rev1["value"] is None

    # 2. Future as_of in forecast
    pool_future_asof = {
        "earnings_forecast": {
            "source": "业绩预告",
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "metric": "净利润",
            "value": 80.0,
            "unit": "亿元",
            "report_period": "2024Q2",
            "as_of": "2024-09-30",  # Future relative to current_date 2024-07-01
        }
    }
    base2, rev2, gaps2 = _extract_baseline_and_revision(actual, pool_future_asof, current_date="2024-07-01")
    assert base2["type"] == BASELINE_NONE
    assert base2["value"] is None
    assert "future_date" in gaps2
    assert rev2["type"] != REVISION_NUMERIC

    # 3. Illegal format as_of in forecast
    pool_illegal_asof = {
        "earnings_forecast": {
            "source": "业绩预告",
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "metric": "净利润",
            "value": 80.0,
            "unit": "亿元",
            "report_period": "2024Q2",
            "as_of": "2024/06/30_invalid",
        }
    }
    base3, rev3, gaps3 = _extract_baseline_and_revision(actual, pool_illegal_asof, current_date="2024-07-01")
    assert base3["type"] == BASELINE_NONE
    assert base3["value"] is None
    assert "invalid_as_of" in gaps3
    assert rev3["type"] != REVISION_NUMERIC


def test_negative_manager_guard_covers_chinese_english_and_synonyms():
    """DAV-874 Item 3: Manager guard intercepts Chinese, English, and synonyms for beat/miss, priced-in, and numbers."""
    fund_er_gap = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_GAP)
    news_er_unknown = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_GAP)
    verdict = {"direction": "BULLISH", "reason": "保持跟踪"}

    # 1. English beat
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "Company beat market expectations on quarterly earnings", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("超预期" in v or "beat" in v.lower() for v in viols)

    # 2. English miss
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "Operating revenues missed consensus estimates significantly", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("不及预期" in v or "miss" in v.lower() for v in viols)

    # 3. Chinese synonyms: 超出预期 / 弱于预期
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "本次定期报告业绩大幅超出预期，表现亮眼", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("超出预期" in v for v in viols)

    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "公司单季盈利明显弱于预期，需防范风险", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("弱于预期" in v for v in viols)

    # 4. English priced-in
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "The market has already priced in the recent contract developments", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("priced in" in v.lower() or "已定价" in v for v in viols)

    # 5. English fully discounted
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "The positive news is fully discounted in the current stock price", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("priced in" in v.lower() or "已定价" in v for v in viols)

    # 6. Chinese priced-in synonym: 利好已被充分消化
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "该项利好已被充分消化，不宜追涨", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("已定价" in v for v in viols)

    # 7. English hallucinated financial numbers without evidence
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "The firm delivered net profit of $120 million in the recent quarter", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("财务指标数值" in v or "net profit" in v.lower() for v in viols)


def test_negative_double_count_guard_deduplicates_by_real_identity_and_is_idempotent():
    """DAV-874 Item 4: Distinct events not deduplicated; identical events deduplicated once; guard is idempotent."""
    fund_er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
    fund_er["double_count_guard"] = {
        "status": DOUBLE_COUNT_ACCOUNTED_FOR,
        "prevent_double_voting": True,
        "description": "已在基线中计入",
    }
    news_er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_PARTIAL)
    news_er["double_count_guard"] = {
        "status": DOUBLE_COUNT_UNKNOWN,
        "prevent_double_voting": True,
        "description": "阻止再次加票",
    }
    exp_revs = {"fundamentals": fund_er, "news": news_er}

    # 1. Two genuinely independent events with distinct identities and evidence
    claims_indep = [
        {"claim_id": "c1", "event_id": "ev_contract", "claim_text": "公司中标10亿元重大项目", "evidence": ["中标公示10亿元"]},
        {"claim_id": "c2", "event_id": "ev_patent", "claim_text": "公司获得核心技术发明专利授权", "evidence": ["发明专利证书ZL9988"]},
    ]
    initial_metrics = {
        "independent_cluster_count": 2,
        "bull_cluster_count": 2,
        "bear_cluster_count": 0,
    }
    initial_verdict = {
        "adopted_claim_ids": ["c1", "c2"],
        "excluded_evidence": [],
    }

    m_out, v_out, _ = apply_manager_double_count_guard(
        claim_cluster_metrics=initial_metrics,
        expectation_revisions=exp_revs,
        claims=claims_indep,
        manager_verdict=initial_verdict,
    )
    # Distinct events are NOT collapsed!
    assert m_out["independent_cluster_count"] == 2
    assert m_out["bull_cluster_count"] == 2
    assert v_out["adopted_claim_ids"] == ["c1", "c2"]
    assert len(v_out["excluded_evidence"]) == 0

    # 2. Idempotency test: repeating invocation does NOT subtract again
    m_out2, v_out2, _ = apply_manager_double_count_guard(
        claim_cluster_metrics=m_out,
        expectation_revisions=exp_revs,
        claims=claims_indep,
        manager_verdict=v_out,
    )
    assert m_out2["independent_cluster_count"] == 2
    assert m_out2["bull_cluster_count"] == 2
    assert v_out2["adopted_claim_ids"] == ["c1", "c2"]

    # 3. Two claims for the SAME event are deduplicated
    claims_same = [
        {"claim_id": "c1", "event_id": "ev_guidance", "claim_text": "公司业绩预告大幅增长", "evidence": ["预告披露增长50%"]},
        {"claim_id": "c2", "event_id": "ev_guidance", "claim_text": "新闻报道业绩预告大幅增长", "evidence": ["预告披露增长50%"]},
    ]
    m_dup, v_dup, _ = apply_manager_double_count_guard(
        claim_cluster_metrics=dict(initial_metrics),
        expectation_revisions=exp_revs,
        claims=claims_same,
        manager_verdict={"adopted_claim_ids": ["c1", "c2"], "excluded_evidence": []},
    )
    assert m_dup["independent_cluster_count"] == 1
    assert m_dup["bull_cluster_count"] == 1
    assert v_dup["adopted_claim_ids"] == ["c1"]
    assert len(v_dup["excluded_evidence"]) == 1
    assert v_dup["excluded_evidence"][0]["claim_id"] == "c2"

    # Idempotent re-invocation on deduplicated state does not double subtract
    m_dup2, v_dup2, _ = apply_manager_double_count_guard(
        claim_cluster_metrics=m_dup,
        expectation_revisions=exp_revs,
        claims=claims_same,
        manager_verdict=v_dup,
    )
    assert m_dup2["independent_cluster_count"] == 1
    assert len(v_dup2["excluded_evidence"]) == 1


def test_negative_publication_missing_provenance_stays_gap_without_faking_publish_time():
    """DAV-874 Item 5: Publication lacking provenance stays gap; cannot use actual as_of as publish_time."""
    outputs = {
        "structured_financials": {
            "source": "structured_financials",
            "metric": "净利润",
            "value": 100.0,
            "unit": "亿元",
            "report_period": "2024Q2",
            "as_of": "2024-06-30",
        }
    }
    # outputs has no publish_time, no source_hash
    er = build_fundamentals_expectation_revision(outputs, current_date="2024-07-01")
    assert er["publication"]["publish_time"] is None
    assert er["publication"]["published_at"] is None
    assert er["publication"]["source_hash"] is None
    assert "missing_publish_time" in er["gaps"]
    assert "source_hash_missing" in er["gaps"]
    assert er["publication"]["content_qualification"] in (CONTENT_UNAVAILABLE, CONTENT_NOT_ATTEMPTED)
    assert er["status"] == STATUS_GAP