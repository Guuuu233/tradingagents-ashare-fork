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
import datetime
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

    # 8. DAV-875 Red Team additions: exceed / surpass base forms
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "Company results exceed market expectations", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("超预期" in v or "exceed" in v.lower() for v in viols)

    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "Company profits surpass market estimates", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("超预期" in v or "surpass" in v.lower() for v in viols)

    # 9. DAV-875 Red Team additions: expected comparison expressions
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "Earnings were better than expected", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("超预期" in v or "better than" in v.lower() for v in viols)

    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "Results were worse than expected", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("不及预期" in v or "worse than" in v.lower() for v in viols)

    # 10. DAV-875 Red Team additions: fell short / falls short / fall short
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "Company quarterly performance fell short", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("不及预期" in v or "fell short" in v.lower() for v in viols)

    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "Company quarterly performance falls short", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("不及预期" in v or "falls short" in v.lower() for v in viols)

    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "Operating results fall short of analyst consensus", [fund_er_gap, news_er_unknown]
    )
    assert not is_valid
    assert any("不及预期" in v or "fall short" in v.lower() for v in viols)


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


def _make_dcg_exp_revs():
    """Expectation revisions with double_count_guard active on both legs."""
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
    return {"fundamentals": fund_er, "news": news_er}


def test_double_count_guard_tolerates_non_mapping_excluded_evidence_on_first_pass():
    """DAV-1056: first-pass exclusion must not crash on string/None legacy excluded_evidence entries."""
    exp_revs = _make_dcg_exp_revs()
    claims_same = [
        {"claim_id": "c1", "event_id": "ev_guidance", "claim_text": "公司业绩预告大幅增长", "evidence": ["预告"]},
        {"claim_id": "c2", "event_id": "ev_guidance", "claim_text": "业绩预告大幅增长", "evidence": ["预告"]},
    ]
    initial_verdict = {
        "adopted_claim_ids": ["c1", "c2"],
        "excluded_evidence": ["历史字符串证据", None, {"claim_id": "c9", "reason": "其他原因"}],
    }
    m_out, v_out, _ = apply_manager_double_count_guard(
        claim_cluster_metrics={"independent_cluster_count": 2, "bull_cluster_count": 2, "bear_cluster_count": 0},
        expectation_revisions=exp_revs,
        claims=claims_same,
        manager_verdict=initial_verdict,
    )
    assert v_out["adopted_claim_ids"] == ["c1"]
    excluded = v_out["excluded_evidence"]
    # 字符串与 None 被保留，mapping 按 claim_id 去重后追加 c2
    assert "历史字符串证据" in excluded
    assert None in excluded
    assert any(isinstance(e, dict) and e.get("claim_id") == "c9" for e in excluded)
    assert sum(1 for e in excluded if isinstance(e, dict) and e.get("claim_id") == "c2") == 1


def test_double_count_guard_reentry_tolerates_non_mapping_excluded_evidence():
    """DAV-1056: idempotent reentry must not crash when excluded_evidence contains strings/None/odd mappings."""
    exp_revs = _make_dcg_exp_revs()
    metrics = {
        "independent_cluster_count": 1,
        "bull_cluster_count": 1,
        "double_count_guard_applied": True,
        "double_count_guard_audit": {
            "status": "blocked",
            "excluded_claim_ids": ["c2", "c3"],
        },
    }
    verdict = {
        "adopted_claim_ids": ["c1", "c2", "c3"],
        "excluded_evidence": [
            "历史字符串证据",
            None,
            {"claim_id": "c2", "reason": "double_count_guard: 已排除"},
            {"reason": "无 claim_id 的历史记录"},
        ],
    }
    m_out, v_out, _ = apply_manager_double_count_guard(
        claim_cluster_metrics=metrics,
        expectation_revisions=exp_revs,
        claims=[],
        manager_verdict=verdict,
    )
    # c2/c3 从 adopted 剔除；字符串与 None 保留；c2 不重复追加，c3 新增一条
    assert v_out["adopted_claim_ids"] == ["c1"]
    excluded = v_out["excluded_evidence"]
    assert "历史字符串证据" in excluded
    assert None in excluded
    assert sum(1 for e in excluded if isinstance(e, dict) and e.get("claim_id") == "c2") == 1
    assert sum(1 for e in excluded if isinstance(e, dict) and e.get("claim_id") == "c3") == 1

    # 二次重入结果稳定（幂等）
    m_out2, v_out2, _ = apply_manager_double_count_guard(
        claim_cluster_metrics=m_out,
        expectation_revisions=exp_revs,
        claims=[],
        manager_verdict=v_out,
    )
    assert v_out2["adopted_claim_ids"] == ["c1"]
    assert v_out2["excluded_evidence"] == excluded


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


def test_negative_manager_guard_covers_english_exceed_surpass_expected_and_fall_short():
    """DAV-877 Item 1 & 3: Manager English guard intercepts exceed/surpass base forms, expected comparisons, and fall short phrases."""
    from tradingagents.agents.utils.decision_status import status_from_manager_verdict

    fund_er_gap = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_GAP)
    news_er_unknown = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_GAP)
    verdict = {"direction": "BULLISH", "reason": "保持中性"}

    # 1. Base form: exceed / surpass
    cases_exceed_surpass = [
        ("Company results exceed market expectations", "exceed"),
        ("Quarterly earnings exceed analyst consensus", "exceed"),
        ("Results surpass expectations", "surpass"),
        ("Revenues surpass market forecasts", "surpass"),
    ]
    for text, kw in cases_exceed_surpass:
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, [fund_er_gap, news_er_unknown]
        )
        assert not is_valid, f"Failed to intercept {text!r}"
        assert any("超预期" in v or kw in v.lower() for v in viols)

        # End-to-end: consistency failure enters NO_TRADE path
        v_copy = dict(verdict)
        v_copy["consistency_check_passed"] = False
        v_copy["failed_checks"] = viols
        st = status_from_manager_verdict(v_copy)
        assert st.trade_action == "NO_TRADE"
        assert st.risk_status == "BLOCKED"

    # 2. expected comparison expressions
    cases_expected = [
        ("Earnings were better than expected", "better than"),
        ("Revenues were higher than expected", "higher than"),
        ("Results were worse than expected", "worse than"),
        ("Operating profits were lower than expected", "lower than"),
    ]
    for text, kw in cases_expected:
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, [fund_er_gap, news_er_unknown]
        )
        assert not is_valid, f"Failed to intercept {text!r}"
        assert any("预期" in v or kw in v.lower() for v in viols)

    # 3. fell short / falls short / fall short phrases (standalone and with of)
    cases_fall_short = [
        ("Company quarterly performance fell short", "fell short"),
        ("Company performance falls short", "falls short"),
        ("Results fall short", "fall short"),
        ("Quarterly numbers fell short of market expectations", "fell short"),
        ("Operating performance falls short of analyst consensus", "falls short"),
        ("Revenues fall short of estimates", "fall short"),
    ]
    for text, kw in cases_fall_short:
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, [fund_er_gap, news_er_unknown]
        )
        assert not is_valid, f"Failed to intercept {text!r}"
        assert any("不及预期" in v or kw in v.lower() for v in viols)

    # 4. Positive test: when valid baseline is present, manager can cite beat/miss/exceed
    fund_er_valid = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
    fund_er_valid["baseline"] = {
        "type": "forecast",
        "value": 100.0,
        "unit": "亿元",
        "as_of": "2024-05-01",
        "source": "structured_forecast",
    }
    fund_er_valid["actual"] = {
        "status": STATUS_AVAILABLE,
        "metric": "净利润",
        "value": 120.0,
        "unit": "亿元",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    is_valid_pos, viols_pos = validate_manager_expectation_revision_consumption(
        verdict, "Company results exceed market expectations", [fund_er_valid, news_er_unknown]
    )
    assert is_valid_pos
    assert len(viols_pos) == 0


def test_double_count_guard_cluster_id_deduplication_and_idempotency():
    """DAV-877 Item 2 & 3: Double count guard incorporates bool(cluster_id) in is_event_claim; deduplicates once; idempotent."""
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

    # 1. Claims with cluster_id but NO event_id, neutral event_type, and no financial keywords in claim_text
    # Previously, is_event_claim evaluated to False because cluster_id was omitted from is_event_claim
    claims_same_cluster = [
        {
            "claim_id": "c1",
            "cluster_id": "cluster_industry_expansion_001",
            "claim_text": "行业景气度持续上升拉动产能利用率达到历史高位水平",
            "stance": "bull",
        },
        {
            "claim_id": "c2",
            "cluster_id": "cluster_industry_expansion_001",
            "claim_text": "下游需求旺盛带动产销两旺产线处于饱和运转状态",
            "stance": "bull",
        },
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
        claim_cluster_metrics=dict(initial_metrics),
        expectation_revisions=exp_revs,
        claims=claims_same_cluster,
        manager_verdict=dict(initial_verdict),
    )

    # Identical cluster_id claims ARE deduplicated once!
    assert m_out["double_count_guard_active"] is True
    assert m_out["duplicate_voting_prevented"] is True
    assert m_out["independent_cluster_count"] == 1
    assert m_out["bull_cluster_count"] == 1
    assert v_out["adopted_claim_ids"] == ["c1"]
    assert len(v_out["excluded_evidence"]) == 1
    assert v_out["excluded_evidence"][0]["claim_id"] == "c2"

    # Idempotent re-invocation: calling again does not subtract again
    m_out2, v_out2, _ = apply_manager_double_count_guard(
        claim_cluster_metrics=m_out,
        expectation_revisions=exp_revs,
        claims=claims_same_cluster,
        manager_verdict=v_out,
    )
    assert m_out2["independent_cluster_count"] == 1
    assert m_out2["bull_cluster_count"] == 1
    assert v_out2["adopted_claim_ids"] == ["c1"]
    assert len(v_out2["excluded_evidence"]) == 1

    # 2. Distinct cluster_ids are NOT deduplicated
    claims_diff_cluster = [
        {
            "claim_id": "c1",
            "cluster_id": "cluster_expansion_001",
            "claim_text": "行业景气度持续上升拉动产能利用率达到历史高位水平",
            "stance": "bull",
        },
        {
            "claim_id": "c2",
            "cluster_id": "cluster_patent_002",
            "claim_text": "获得新型固态电池核心材料发明专利授权",
            "stance": "bull",
        },
    ]
    m_diff, v_diff, _ = apply_manager_double_count_guard(
        claim_cluster_metrics=dict(initial_metrics),
        expectation_revisions=exp_revs,
        claims=claims_diff_cluster,
        manager_verdict=dict(initial_verdict),
    )
    assert m_diff["independent_cluster_count"] == 2
    assert m_diff["bull_cluster_count"] == 2
    assert v_diff["adopted_claim_ids"] == ["c1", "c2"]
    assert len(v_diff["excluded_evidence"]) == 0


def test_calendar_date_validation_downgrades_gap_and_forbids_numeric():
    """DAV-877 Item 4: Non-calendar dates (2024-02-31, 0000-00-00) must downgrade to gap and forbid numeric revision."""
    invalid_dates = ["2024-02-31", "0000-00-00", "2024-04-31", "2024-13-01"]

    for inv_date in invalid_dates:
        # A. _extract_structured_actual downgrades to gap and value=None
        outputs = {
            "structured_financials": {
                "metric": "净利润",
                "value": 150.0,
                "unit": "亿元",
                "report_period": "2024Q2",
                "as_of": inv_date,
            }
        }
        act, act_gaps = _extract_structured_actual(outputs, current_date="2024-07-01")
        assert act["status"] == STATUS_GAP
        assert act["value"] is None
        assert "invalid_as_of" in act_gaps

        # B. Baseline with invalid calendar date is demoted to type=none and forbids numeric revision
        pool_inv_baseline = {
            "earnings_forecast": {
                "metric": "净利润",
                "value": 120.0,
                "unit": "亿元",
                "report_period": "2024Q2",
                "as_of": inv_date,
                "source": "structured_forecast",
                "type": "forecast",
            }
        }
        outputs_valid_act = {
            "structured_financials": {
                "metric": "净利润",
                "value": 150.0,
                "unit": "亿元",
                "report_period": "2024Q2",
                "as_of": "2024-06-30",
            }
        }
        act_valid, _ = _extract_structured_actual(outputs_valid_act, current_date="2024-07-01")
        base, rev, base_gaps = _extract_baseline_and_revision(
            actual=act_valid, pool=pool_inv_baseline, current_date="2024-07-01"
        )
        assert base["type"] == BASELINE_NONE
        assert base["value"] is None
        assert "invalid_as_of" in base_gaps
        assert rev["type"] != REVISION_NUMERIC
        assert rev["value"] is None

        # C. Full build_fundamentals_expectation_revision produces gap without numeric revision
        outputs_full_inv = {
            "publish_time": "2024-07-01 10:00:00",
            "source": "上交所",
            "source_hash": "sha256:abcd1234abcd1234",
            "structured_financials": {
                "metric": "净利润",
                "value": 150.0,
                "unit": "亿元",
                "report_period": "2024Q2",
                "as_of": inv_date,
            },
        }
        er = build_fundamentals_expectation_revision(outputs_full_inv, current_date="2024-07-01")
        assert er["status"] == STATUS_GAP
        assert er["actual"]["status"] == STATUS_GAP
        assert er["actual"]["value"] is None
        assert er["revision"]["type"] != REVISION_NUMERIC
        assert er["revision"]["value"] is None
        assert "invalid_as_of" in er["gaps"]

        # D. Contract validator strictly rejects non-calendar dates
        fake_er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)
        fake_er["actual"]["as_of"] = inv_date
        fake_er["actual"]["value"] = 100.0
        fake_er["actual"]["metric"] = "净利润"
        fake_er["actual"]["unit"] = "亿元"
        fake_er["actual"]["report_period"] = "2024Q2"
        is_val, viols = validate_expectation_revision(fake_er)
        assert not is_val
        assert any("invalid calendar date" in v for v in viols)


def test_build_fundamentals_expectation_revision_fails_closed_on_fabricated_fin_hash():
    """DAV-877 Item 5: Builder must fail closed on fin_<period>_<date> fabricated fingerprints without qualified status."""
    fabricated_hashes = [
        "fin_2024Q2_2024-06-30",
        "fin_NA_NA",
        "fin_2024Q1_2024-03-31",
    ]
    for fake_hash in fabricated_hashes:
        outputs = {
            "publish_time": "2024-07-01 10:00:00",
            "source": "巨潮资讯网",
            "source_hash": fake_hash,
            "structured_financials": {
                "metric": "净利润",
                "value": 100.0,
                "unit": "亿元",
                "report_period": "2024Q2",
                "as_of": "2024-06-30",
                "source_hash": fake_hash,
            },
        }
        pool = {
            "baseline": {
                "type": "forecast",
                "source": "broker_consensus",
                "metric": "净利润",
                "value": 80.0,
                "unit": "亿元",
                "period": "2024Q2",
                "as_of": "2024-05-01",
            }
        }
        er = build_fundamentals_expectation_revision(outputs, pool=pool, current_date="2024-07-01")

        # Builder must fail-closed!
        assert er["status"] == STATUS_GAP
        assert er["publication"]["content_qualification"] != CONTENT_QUALIFIED
        assert er["publication"]["content_qualification"] in (CONTENT_UNAVAILABLE, CONTENT_NOT_ATTEMPTED)
        assert er["publication"]["source_hash"] is None
        assert "source_hash_missing" in er["gaps"]
        assert "fabricated_source_hash" in er["gaps"]
        assert er["priced_in"]["status"] == PRICED_IN_UNKNOWN
        assert er["double_count_guard"]["status"] == DOUBLE_COUNT_UNKNOWN


# ==============================================================================
# 13. DAV-883: 公告 publication 时间的严格公历校验
# ==============================================================================

def test_dav883_publication_valid_calendar_dates_and_timestamps_pass():
    """DAV-883 Positive test: Valid YYYY-MM-DD and valid timestamps pass validation and builder."""
    valid_times = [
        "2024-05-15",
        "2024-02-29",  # Leap year
        "2020-02-29",  # Leap year
        "2024-05-15 10:30:00",
        "2024-05-15 10:30",
        "2024-05-15T10:30:00",
        "2024-05-15T10:30:00Z",
        "2024-05-15T10:30:00+08:00",
        "2024-05-15 10:30:00.123456",
        "2024/05/15 10:30:00",
        "2024年05月15日 10:30:00",
        "2024年5月15日",
        1715767200,
        "1715767200",
        datetime.datetime(2024, 5, 15, 10, 30, 0),
        datetime.date(2024, 5, 15),
    ]

    for val_time in valid_times:
        evidence_pool = {
            "news_items": [
                {
                    "title": "合法时间新闻",
                    "publish_time": val_time,
                    "source": "官方公告",
                    "source_hash": "sha256:1122334455667788",
                    "content_status": "qualified",
                    "body": "合法日期新闻正文",
                }
            ]
        }
        er = build_news_expectation_revision(evidence_pool, cutoff_date="2025-01-01")
        assert "invalid_publish_time" not in er["gaps"]
        assert "future_date" not in er["gaps"]
        assert er["status"] in (STATUS_PARTIAL, STATUS_AVAILABLE)
        assert er["revision"]["type"] == REVISION_QUALITATIVE
        if isinstance(val_time, (datetime.datetime, datetime.date)):
            assert er["publication"]["publish_time"] == str(val_time)
            assert er["publication"]["published_at"] == str(val_time)
        else:
            assert er["publication"]["publish_time"] == val_time
            assert er["publication"]["published_at"] == val_time
        assert er["publication"]["source"] == "官方公告"

        # Validator cleanly validates positive contracts
        is_val, viols = validate_expectation_revision(er, cutoff_date="2025-01-01")
        assert is_val, f"Validation failed for {val_time!r}: {viols}"


def test_dav883_publication_illegal_calendar_dates_blocked_and_enter_gap():
    """DAV-883 Negative test: Impossible calendar dates (2024-02-31, 0000-00-00, etc.) enter typed gap and block qualification/numeric revision."""
    invalid_calendar_dates = [
        "2024-02-31",
        "0000-00-00",
        "2024-04-31",
        "2023-02-29",  # Non-leap year
        "1900-02-29",  # 1900 is not a leap year in Gregorian calendar
        "2024-13-01",
        "2024-00-10",
        "2024-02-31 10:00:00",
        "2024-04-31T12:00:00Z",
        "0000-00-00 00:00:00",
    ]

    for inv_date in invalid_calendar_dates:
        # 1. Builder demotes to gap, records invalid_publish_time, denies qualified content, blocks numeric revision
        evidence_pool = {
            "news_items": [
                {
                    "title": "非法公历日期新闻",
                    "publish_time": inv_date,
                    "source": "非法源",
                    "source_hash": "sha256:aabbccddeeff0011",
                    "content_status": "qualified",
                    "body": "公告正文",
                }
            ]
        }
        er = build_news_expectation_revision(evidence_pool, cutoff_date="2025-01-01")
        assert "invalid_publish_time" in er["gaps"], f"Failed to record invalid_publish_time for {inv_date}"
        assert er["status"] == STATUS_GAP
        assert er["actual"]["value"] is None
        assert er["revision"]["type"] != REVISION_NUMERIC
        assert er["revision"]["type"] == REVISION_GAP
        assert er["revision"]["value"] is None
        # Qualification is downgraded - cannot masquerade as qualified
        assert er["publication"]["content_qualification"] != CONTENT_QUALIFIED
        # Publication readback semantics preserved
        assert er["publication"]["publish_time"] == inv_date
        assert er["publication"]["published_at"] == inv_date

        # 2. Contract validator strictly rejects non-calendar dates when attempting qualification or numeric revision
        fake_er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_AVAILABLE)
        fake_er["publication"]["publish_time"] = inv_date
        fake_er["publication"]["content_qualification"] = CONTENT_QUALIFIED
        fake_er["actual"]["value"] = 100.0
        fake_er["revision"]["type"] = REVISION_NUMERIC
        fake_er["revision"]["value"] = 10.0
        is_val, viols = validate_expectation_revision(fake_er)
        assert not is_val, f"Validator unexpectedly accepted {inv_date!r}"
        assert any("invalid calendar date" in v for v in viols)
        assert any("content_qualification cannot be 'qualified'" in v for v in viols)
        assert any("actual.value must be None" in v for v in viols)
        assert any("revision cannot be 'numeric'" in v for v in viols)


def test_dav883_publication_format_errors_blocked_and_enter_gap():
    """DAV-883 Negative test: Malformed format strings enter typed gap and are rejected by validator."""
    format_errors = [
        "invalid_date_format",
        "not_a_date",
        "2024-99-99",
        "abc-def-ghi",
        "2024-02-29 25:00:00",
        "2024-02-29 10:60:00",
    ]

    for fmt_err in format_errors:
        evidence_pool = {
            "news_items": [
                {
                    "title": "格式错误新闻",
                    "publish_time": fmt_err,
                    "source": "测试源",
                    "source_hash": "sha256:1234567890abcdef",
                    "content_status": "qualified",
                }
            ]
        }
        er = build_news_expectation_revision(evidence_pool, cutoff_date="2025-01-01")
        assert "invalid_publish_time" in er["gaps"]
        assert er["status"] == STATUS_GAP
        assert er["revision"]["type"] == REVISION_GAP
        assert er["publication"]["content_qualification"] != CONTENT_QUALIFIED
        assert er["publication"]["publish_time"] == fmt_err

        # Validator rejects
        fake_er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_AVAILABLE)
        fake_er["publication"]["publish_time"] = fmt_err
        is_val, viols = validate_expectation_revision(fake_er)
        assert not is_val
        assert any("invalid calendar date" in v for v in viols)


def test_dav883_publication_future_date_regression_and_cutoff_preservation():
    """DAV-883 Future date test: Future dates beyond cutoff enter future_date gap, not invalid_publish_time, preserving readback."""
    evidence_pool = {
        "news_items": [
            {
                "title": "未来新闻",
                "publish_time": "2025-06-01 10:00:00",
                "source": "未来公告",
                "source_hash": "sha256:abcdef1234567890",
                "content_status": "qualified",
                "body": "未来真实正文",
            }
        ]
    }
    er = build_news_expectation_revision(evidence_pool, cutoff_date="2025-05-15")
    assert "future_date" in er["gaps"]
    assert "invalid_publish_time" not in er["gaps"]
    assert er["status"] == STATUS_GAP
    assert er["actual"]["value"] is None
    assert er["revision"]["type"] == REVISION_GAP
    assert er["publication"]["publish_time"] == "2025-06-01 10:00:00"
    assert er["publication"]["published_at"] == "2025-06-01 10:00:00"
    assert er["publication"]["content_qualification"] != CONTENT_QUALIFIED

    # Validator checks future date against cutoff
    fake_er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_AVAILABLE)
    fake_er["publication"]["publish_time"] = "2025-06-01 10:00:00"
    fake_er["publication"]["content_qualification"] = CONTENT_QUALIFIED
    is_val, viols = validate_expectation_revision(fake_er, cutoff_date="2025-05-15")
    assert not is_val
    assert any("later than cutoff" in v for v in viols)
    assert any("content_qualification cannot be 'qualified'" in v for v in viols)

# ==============================================================================
# DAV-1068 缺陷B：E-04 priced-in 守卫不得把明确否定/不确定句误报为肯定断言
# ==============================================================================

def _dav1068_gap_revs():
    fund_er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_GAP)
    news_er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_GAP)
    return [fund_er, news_er]


def test_dav1068_priced_in_negation_zh_en_not_flagged():
    """B1: 中英文明确否定/不确定句不得被判为已定价断言。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    for text in (
        "利好尚未充分定价。",
        "目前无法确认利好已定价。",
        "利好并未完全定价，仍待验证。",
        "不能确定该利好已被市场消化。",
        "The benefit is not fully priced in.",
        "The positive is not yet priced in.",
        "We cannot confirm the news is already priced in.",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1068_gap_revs()
        )
        assert not any("已定价" in v for v in viols), f"误报: {text!r} -> {viols}"


def test_dav1068_priced_in_affirmation_still_flagged():
    """B2: 肯定断言、双重否定、同段并列肯定仍拒绝。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    for text in (
        "利好已充分定价。",
        "当前市场已充分定价该项合同利好。",
        "利好已定价，不宜追高。",
        "并非未充分定价，反而已完全定价。",
        "利好尚未充分定价，但估值已完全定价。",
        "The market has already priced in the recent contract developments",
        "The positive news is fully discounted in the current stock price",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1068_gap_revs()
        )
        assert not is_valid, f"应拦截: {text!r}"
        assert any("已定价" in v for v in viols), f"应报已定价违规: {text!r} -> {viols}"


def test_dav1068_priced_in_rejected_quote_not_flagged():
    """B3: 引用对方观点并明确驳回/不成立，不算自己断言；其他守卫不退化。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "多方称利好已充分定价，我方予以驳回，该说法不成立。",
        _dav1068_gap_revs(),
    )
    assert not any("已定价" in v for v in viols), f"误报: {viols}"

# ==============================================================================
# DAV-1071：E-04 引用型误报修复 + beat/miss per-occurrence + 条件句判别 + 披露依据降权豁免
# ==============================================================================

def _dav1071_gap_revs():
    return _dav1068_gap_revs()


_DAV1071_CLAIM_PI_UNKNOWN = (
    "中报披露已超10个交易日，700亿现金流属于全市场皆知的历史财务陈迹（已定价），不构成新增催化"
)


def test_dav1071_priced_in_claim_quotation_not_flagged():
    """A/C/E: claim 标 priced_in=unknown 时，manager 引用该 claim 文本不算自行断言；自行新增仍拦。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    claims = [{"claim_id": "CLM-PI", "claim": _DAV1071_CLAIM_PI_UNKNOWN, "evidence": []}]

    # A: 原样引用 claim 文本 -> 放行
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "采纳多方观点：" + _DAV1071_CLAIM_PI_UNKNOWN, _dav1071_gap_revs(), claims=claims
    )
    assert not any("已定价" in v for v in viols), f"引用被误报: {viols}"

    # B: 同段引用之外另有自行断言 -> 拦
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        _DAV1071_CLAIM_PI_UNKNOWN + "。此外我认为估值提升空间已充分定价，可积极做多。",
        _dav1071_gap_revs(), claims=claims,
    )
    assert any("已定价" in v for v in viols), f"自行断言漏拦: {viols}"

    # D: 无任何 claim 支撑，manager 自己说已定价 -> 拦
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, "该利好已被市场充分消化，且现金流改善已定价。", _dav1071_gap_revs(), claims=[]
    )
    assert any("已定价" in v for v in viols), f"无支撑断言漏拦: {viols}"


def test_dav1071_beat_miss_conditional_and_listing_not_flagged():
    """beat/miss 条件/假设句、否定句、名词性列举（生产 8 条真实命中形态）全部放行。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    for text in (
        "若节日消费实际承接超预期，可能迅速阻断空头破位进程",
        "若白酒双节动销大幅不及预期，叠加批价走弱将演化为加速破位",
        "但若半年报业绩大幅不及预期且放量破位，短线估值溢价将被挤压",
        "若日收盘跌破88.80元或08-29中报单季扣非净利同比大幅不及预期，无条件止损离场",
        "若中报超预期，上方SMA200（94.62元）将转为支撑",
        "正文无未计提超预期信息",
        "盲点：中报历史静态超预期幅度、渠道库存未披露",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs()
        )
        assert not any("预期" in v for v in viols), f"误报: {text!r} -> {viols}"


def test_dav1071_beat_miss_own_assertion_still_flagged():
    """beat/miss 自行断言（非条件、非引用）仍拦，守卫不退化。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    for text in (
        "公司二季度业绩大幅超预期，建议适度建仓",
        "本次中报实际披露净利不及预期，需下修判断",
        "The results beat expectations significantly",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs()
        )
        assert not is_valid, f"应拦截: {text!r}"
        assert any("预期" in v for v in viols), f"应报业绩预期违规: {text!r} -> {viols}"


def test_dav1071_beat_miss_claim_quotation_not_flagged():
    """manager 引用分析师 claim 中的超预期表述不算自行断言。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    claim = "多方认为二季度动销超预期，但证据仅为渠道调研口径"
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "记录多方观点：" + claim + "，不予采纳。",
        _dav1071_gap_revs(),
        claims=[{"claim_id": "CLM-BM", "claim": claim}],
    )
    assert not any("预期" in v for v in viols), f"引用被误报: {viols}"


def test_dav1071_priced_in_traceable_basis_downweight_not_flagged():
    """附带可回溯披露日期/公开时长依据且用于降权的 priced-in 引用放行（生产真实句）。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    for text in (
        "该信息8月29日披露，超过14个交易日的信息属于已定价，短线择时解释力偏弱",
        "该财务事实已于8月31日披露超10个交易日，属于已定价信息，对短线交易解释力弱",
        "依据信息定价纪律，中报已披露超10个交易日，属于已定价基线，短线解释力降权",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs()
        )
        assert not any("已定价" in v for v in viols), f"误报: {text!r} -> {viols}"


def test_dav1071_priced_in_basis_without_downweight_or_reverse_still_flagged():
    """有日期依据但用于支撑方向、或有降权词但无日期依据，仍拦。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    for text in (
        "该利好已于8月29日披露，市场已充分定价，建议追高买入",
        "该利好已定价，短线解释力偏弱，不宜追高",
        "信息已充分定价，该判断不作为独立择时依据",  # 无日期/时长依据
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs()
        )
        assert any("已定价" in v for v in viols), f"漏拦: {text!r} -> {viols}"

# ==============================================================================
# DAV-1073 复审返修：条件豁免限同子句 + 生产「claim ID+改写」引用覆盖
# ==============================================================================

def test_dav1073_conditional_scope_limited_to_same_clause():
    """🔴-1：条件词只豁免同子句命中；「若A则B，C已定价」中 C 是主句断言，必须拦。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "若消息属实则观望，该利好已充分定价，短线无上行空间",
        _dav1071_gap_revs(),
    )
    assert not is_valid
    assert any("已定价" in v for v in viols), f"句级豁免漏拦: {viols}"
    # 同子句条件仍放行（含小数点价格不应断句）
    for text in (
        "若节日消费实际承接超预期，可能迅速阻断空头破位进程",
        "若日收盘跌破88.80元或08-29中报单季扣非净利同比大幅不及预期，无条件止损离场",
        "若该信息已被市场消化则利好出尽",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs()
        )
        assert not any("已定价" in v or "预期" in v for v in viols), f"误报: {text!r} -> {viols}"


def test_dav1073_claim_paraphrase_and_id_quotation_not_flagged():
    """🟡-1：生产形态「claim ID + 改写」引用放行——LCS≥10 或句内 claim_id + claim 含同类关键词。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    claim_txt = (
        "中报披露已超10个交易日，700亿现金流属于全市场皆知的历史财务陈迹（已定价），不构成新增催化"
    )
    claims = [{"claim_id": "CLM-PI", "claim": claim_txt, "evidence": []}]
    for text in (
        "多方指出：中报披露已超10个交易日，现金流属历史陈迹，市场层面已定价",
        "采纳CLM-PI的判断：该信息已定价，解释力降权",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs(), claims=claims
        )
        assert not any("已定价" in v for v in viols), f"生产引用形态误报: {text!r} -> {viols}"


def test_dav1073_quotation_with_directional_support_still_flagged():
    """引用形态叠加非否定方向词（可积极做多）属反向支撑自断言，仍拦。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    claims = [{
        "claim_id": "CLM-PI",
        "claim": "中报披露已超10个交易日，700亿现金流属于全市场皆知的历史财务陈迹（已定价），不构成新增催化",
    }]
    for text in (
        "采纳CLM-PI的判断：该信息已定价，可积极做多",
        "中报披露已超10个交易日，我认为利好已被市场消化，可积极做多",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs(), claims=claims
        )
        assert any("已定价" in v for v in viols), f"反向支撑漏拦: {text!r} -> {viols}"


def test_dav1073_priced_in_unknown_annotation_not_flagged():
    """「已定价状态为 unknown」显式标注引用放行。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "分析师已定价状态为unknown，经理按未知处理，不作定价事实依据",
        _dav1071_gap_revs(),
    )
    assert not any("已定价" in v for v in viols), f"标注引用误报: {viols}"

# ==============================================================================
# DAV-1074 复审返修：句级豁免锚定到命中/子句粒度
# ==============================================================================

def test_dav1074_annotation_does_not_exempt_same_sentence_own_assertion():
    """🔴-1：同句标注引用不连带豁免独立断言。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "已定价状态为unknown，但我方独立判断该利好已定价",
        _dav1071_gap_revs(),
    )
    assert not is_valid
    assert any("已定价" in v for v in viols), f"同句独立断言漏拦: {viols}"
    # 纯标注引用仍放行
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "分析师已定价状态为unknown，经理按未知处理，不作定价事实依据",
        _dav1071_gap_revs(),
    )
    assert not any("已定价" in v for v in viols), f"标注引用误报: {viols}"


def test_dav1074_claim_id_does_not_exempt_own_assertion_after_pivot():
    """🔴-2：claim_id 后接自主判断标记时，命中属独立断言仍拦。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    claims = [{"claim_id": "INV-3", "claim": "该利好或已定价，需核实", "evidence": []}]
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "提及INV-3后，我方独立判断利好已定价",
        _dav1071_gap_revs(),
        claims=claims,
    )
    assert not is_valid
    assert any("已定价" in v for v in viols), f"ID 引用语域漏拦: {viols}"
    # 同子句/引用语域内的 ID 引用仍放行
    claim2 = "中报披露已超10个交易日，700亿现金流属于全市场皆知的历史财务陈迹（已定价），不构成新增催化"
    for text in (
        "采纳CLM-PI的判断：该信息已定价，解释力降权",
        "按CLM-PI标注该信息已定价处理",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs(),
            claims=[{"claim_id": "CLM-PI", "claim": claim2}],
        )
        assert not any("已定价" in v for v in viols), f"ID 引用误报: {text!r} -> {viols}"


def test_dav1074_traceable_basis_requires_disclosure_word():
    """🟡-1：仅日期无披露类词不构成可回溯依据；弱降权词收窄。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "9月18日美联储降息落地，利好已定价，市场需谨慎",
        _dav1071_gap_revs(),
    )
    assert not is_valid
    assert any("已定价" in v for v in viols), f"弱依据漏拦: {viols}"

# ==============================================================================
# DAV-1076 复审返修：无标点转折绕过家族 + LCS 语域检查
# ==============================================================================

def test_dav1076_annotation_nopunct_pivot_own_assertion_flagged():
    """🔴-3：标注 match 不再按「同子句」豁免；无标点转折（但/即/而）后的独立断言仍拦。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    for text in (
        "已定价状态为unknown但市场实际已定价",
        "已定价状态为unknown即判断利好已定价",
        "已定价状态为unknown而我方认为利好已定价",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs()
        )
        assert not is_valid, f"无标点转折漏拦: {text!r}"
        assert any("已定价" in v for v in viols), f"应报已定价违规: {text!r} -> {viols}"


def test_dav1076_lcs_paraphrase_with_own_voice_flagged():
    """🔴-4：LCS 改写路径施加同一语域检查；含自主判断/转折标记的句子不再豁免。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    claims = [{
        "claim_id": "CLM-PI",
        "claim": "中报披露已超10个交易日，700亿现金流属于全市场皆知的历史财务陈迹（已定价），不构成新增催化",
    }]
    for text in (
        "多方称该利好已被市场消化，但我方独立判断利好已定价",
        "多方称中报披露已超10个交易日，但该利好实际已定价",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs(), claims=claims
        )
        assert not is_valid, f"LCS+转折漏拦: {text!r}"
        assert any("已定价" in v for v in viols), f"应报已定价违规: {text!r} -> {viols}"
    # 干净 paraphrase 仍放行
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "多方指出：中报披露已超10个交易日，现金流属历史陈迹，市场层面已定价",
        _dav1071_gap_revs(), claims=claims,
    )
    assert not any("已定价" in v for v in viols), f"paraphrase 误报: {viols}"

def test_dav1077_lcs_path_same_sentence_own_assertion_flagged():
    """DAV-1077：LCS 改写路径句级豁免收口复核——审查实测句固化。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    claims = [{
        "claim_id": "CLM-PI",
        "claim": "中报披露已超10个交易日，700亿现金流属于全市场皆知的历史财务陈迹（已定价），不构成新增催化",
    }]
    for text in (
        "多方称该利好已被市场消化反映需核实，我方独立判断其已定价",
        "多方称该利好已被市场消化反映需核实。我方独立判断其已定价",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs(), claims=claims
        )
        assert not is_valid, f"LCS 句级豁免漏拦: {text!r}"
        assert any("已定价" in v for v in viols), f"应报已定价违规: {text!r} -> {viols}"
    # beat/miss 对称路径
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "多方称中报披露已超10个交易日现金流属陈迹，我方独立判断其已超预期",
        _dav1071_gap_revs(), claims=claims,
    )
    assert not is_valid
    assert any("预期" in v for v in viols), f"beat/miss 对称漏拦: {viols}"

def test_dav1080_claim_id_same_clause_own_voice_flagged():
    """DAV-1080 🔴：claim_id 同子句分支补齐语域检查——无标点转折连接的独立断言仍拦。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    claims = [{
        "claim_id": "CLM-PI",
        "claim": "中报披露已超10个交易日，700亿现金流属于全市场皆知的历史财务陈迹（已定价），不构成新增催化",
    }]
    for text in (
        "采纳CLM-PI但我方独立判断该利好已定价",
        "按CLM-PI然而市场实际已定价",
        "采纳CLM-PI，但我方独立判断该利好已定价",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs(), claims=claims
        )
        assert not is_valid, f"同子句 ID+转折漏拦: {text!r}"
        assert any("已定价" in v for v in viols), f"应报已定价违规: {text!r} -> {viols}"
    # 干净 ID 引用仍放行（豁免不依赖标点有无的反向面）
    for text in (
        "采纳CLM-PI的判断：该信息已定价",
        "按CLM-PI标注该信息已定价处理",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, _dav1071_gap_revs(), claims=claims
        )
        assert not any("已定价" in v for v in viols), f"ID 引用误报: {text!r} -> {viols}"


# ==============================================================================
# DAV-1110：E-04 条件作用域修复（并列合取从句「若A，且B…」豁免与后果从句拦截）
# ==============================================================================

def test_dav1110_conjunction_conditional_scope_exemption():
    """DAV-1110 regression fixture：以 a2a7f1e0 真实文本验证并列合取条件从句豁免。"""
    verdict = {"direction": "偏空", "winner": "bear", "reason": "大单持续净流出且均线承压反转未确认"}
    gap_exp = _dav1071_gap_revs()

    # 1. 真实 fixture：a2a7f1e0 极端情景测试段
    real_text = (
        "若美债利率持续突破 4.8%，且 8 月 27 日中报披露折旧超预期侵蚀扣非净利，"
        "杠杆盘（当前仍有114.2亿两融）与短线散户可能引发多杀多。"
    )
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, real_text, gap_exp
    )
    assert is_valid, f"a2a7f1e0 真实条件从句被误拦: {viols}"
    assert not any("预期" in v for v in viols)

    # 2. 中文多重并列合取：若A，且B，并且C超预期
    multi_conj_text = "如果外围市场大跌，且大宗商品反弹，并且三季报业绩超预期，则维持仓位观察"
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, multi_conj_text, gap_exp
    )
    assert is_valid, f"多重合取条件句误拦: {viols}"

    # 3. 英文条件并列合取：If A, and B beats expectations...
    en_conj_text = (
        "If 10Y treasury yields break 4.8%, and the interim report shows depreciation "
        "beats expectations significantly, margin liquidation may occur."
    )
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict, en_conj_text, gap_exp
    )
    assert is_valid, f"英文并列合取条件句误拦: {viols}"

    # 4. 高频词边界：合取从句中「就业/将/便」复合词不得截断条件域（DAV-1110 返修 🟡-1）
    for text in (
        "若美联储降息，且就业数据超预期走强，则风险资产或阶段性受益",
        "若利率上行，且中报业绩将超预期，杠杆资金或回流",
        "若美债利率突破4.8%，且就业市场数据不及预期，衰退交易或升温",
        "若海外流动性收紧，且就诊人数数据超预期，医药板块或承压",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, gap_exp
        )
        assert is_valid, f"高频复合词截断条件域误拦: {text!r} -> {viols}"


def test_dav1110_consequence_and_unconditional_assertions_still_flagged():
    """DAV-1110 边界：后果域及无条件直接断言必须继续拦截，红线绝不退化。"""
    verdict = {"direction": "NEUTRAL", "reason": "保持跟踪"}
    gap_exp = _dav1071_gap_revs()

    # 1. DAV-1073 红线：「若A则B，C已定价」中 C 属主句断言，必须拦
    is_valid, viols = validate_manager_expectation_revision_consumption(
        verdict,
        "若消息属实则观望，该利好已充分定价，短线无上行空间",
        gap_exp,
    )
    assert not is_valid
    assert any("已定价" in v for v in viols), f"主句已定价断言漏拦: {viols}"

    # 2. 「若A则B超预期」：后果断言必须拦
    for text in (
        "若利率上行则中报业绩超预期，建议加仓",
        "如果大盘企稳那么中报业绩超预期将催化反弹",
        "If bond yields rise then interim earnings beat expectations, go long.",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, gap_exp
        )
        assert not is_valid, f"后果句超预期漏拦: {text!r}"
        assert any("预期" in v or "beat" in v for v in viols), f"未报超预期违规: {text!r} -> {viols}"

    # 3. 无条件词的直接断言：必须拦
    for text in (
        "8 月 27 日中报披露折旧超预期侵蚀扣非净利",
        "公司二季度业绩大幅超预期，建议建仓",
        "Interim earnings beat expectations, risk premium drops.",
    ):
        is_valid, viols = validate_manager_expectation_revision_consumption(
            verdict, text, gap_exp
        )
        assert not is_valid, f"直接断言漏拦: {text!r}"
        assert any("预期" in v or "beat" in v for v in viols), f"未报违规: {text!r} -> {viols}"
