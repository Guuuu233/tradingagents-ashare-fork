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
# 端到端红队穿透复核专项回归测试 (DAV-870 复核闭环)
# ==============================================================================

def test_end_to_end_announcement_title_or_url_cannot_become_actual():
    """Announcement titles or PDF URLs containing amounts must not penetrate into actual numbers."""
    outputs = {
        "fundamentals": "公告标题: 关于2024Q2净利润5000万元业绩预告的澄清说明 http://cninfo.com.cn/2024Q2净利润5000万元.pdf",
        "income_statement": "公告正文链接: http://example.com/2024Q2营业收入100亿元.pdf",
    }
    actual, gaps = _extract_structured_actual(outputs, current_date="2024-06-30")
    assert actual["status"] == STATUS_GAP
    assert actual["value"] is None
    assert "actual_incomplete_elements" in gaps

    er = build_fundamentals_expectation_revision(outputs, pool={}, current_date="2024-06-30")
    assert er["actual"]["value"] is None
    assert er["actual"]["status"] == STATUS_GAP
    assert er["revision"]["type"] != REVISION_NUMERIC


def test_end_to_end_future_as_of_date_blocked_cannot_become_numeric_revision():
    """Future statement date beyond cutoff cannot be silently rewritten to current_date or generate numeric revision."""
    outputs = {
        "fundamentals": "2024Q3 净利润 150.0 亿元",
    }
    # Current date is 2024-05-15, but 2024Q3 as_of is 2024-09-30
    actual, gaps = _extract_structured_actual(outputs, current_date="2024-05-15")
    assert actual["status"] == STATUS_GAP
    assert actual["value"] is None
    assert "future_date" in gaps

    pool = {
        "baseline": {
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "source": "业绩指引",
            "metric": "净利润",
            "value": 140.0,
            "unit": "亿元",
            "period": "2024Q3",
            "as_of": "2024-09-30",
        }
    }
    er = build_fundamentals_expectation_revision(outputs, pool=pool, current_date="2024-05-15")
    assert er["actual"]["value"] is None
    assert er["actual"]["status"] == STATUS_GAP
    assert er["revision"]["type"] != REVISION_NUMERIC
    assert "future_date" in er["gaps"]
    assert er["status"] == STATUS_GAP


def test_end_to_end_earnings_forecast_without_source_cannot_fabricate_baseline():
    """earnings_forecast lacking explicit source and type cannot masquerade as management_guidance."""
    actual = {
        "status": STATUS_AVAILABLE,
        "metric": "净利润",
        "value": 100.0,
        "unit": "亿元",
        "report_period": "2024Q2",
        "as_of": "2024-06-30",
    }
    # Forecast without source and without type
    pool = {
        "earnings_forecast": {
            "metric": "净利润",
            "value": 80.0,
            "unit": "亿元",
            "period": "2024Q2",
        }
    }
    baseline, revision, gaps = _extract_baseline_and_revision(actual, pool, current_date="2024-06-30")
    assert baseline["type"] == BASELINE_NONE
    assert "baseline_source_missing" in gaps
    assert revision["type"] != REVISION_NUMERIC
    assert revision["value"] is None
    assert revision["percent"] is None


def test_end_to_end_compliance_violation_blocks_numeric_revision():
    """Compliance period violations (cumulative labeled as single quarter) force revision to gap."""
    outputs = {
        "fundamentals": "2024Q2 营业收入 50.0 亿元",
    }
    pool = {
        "baseline": {
            "type": BASELINE_MANAGEMENT_GUIDANCE,
            "source": "管理层指引",
            "metric": "营业收入",
            "value": 25.0,
            "unit": "亿元",
            "period": "2024Q2",
            "as_of": "2024-06-30",
        }
    }
    compliance = {
        "compliance_status": "violations_found",
        "violations": [
            {
                "kind": "cumulative_labeled_as_single_quarter",
                "message": "H1 累计值被误标为 Q2 单季值",
            }
        ],
    }
    er = build_fundamentals_expectation_revision(
        outputs=outputs,
        pool=pool,
        current_date="2024-06-30",
        compliance=compliance,
    )
    assert "period_mismatch" in er["gaps"]
    assert any("cumulative_labeled_as_single_quarter" in g for g in er["gaps"])
    assert er["actual"]["status"] == STATUS_GAP
    assert er["actual"]["value"] is None
    assert er["revision"]["type"] == REVISION_GAP
    assert er["revision"]["value"] is None


def test_end_to_end_manager_raw_response_hallucination_blocked_even_with_clean_verdict_reason():
    """validate_manager_expectation_revision_consumption inspects raw_response to catch body hallucinations."""
    news_er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_GAP)
    fund_er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_GAP)

    # 15-word reason is completely clean and innocent
    manager_verdict = {
        "winner": "tie",
        "direction": "NEUTRAL",
        "reason": "多空证据分歧，保持中性观望等待确定性",
    }
    # But body text hallucinates "超预期" without baseline
    body_with_beat = """【投资裁决报告】
经深入分析，公司二季度业绩大幅超预期，核心驱动力强劲。
<!-- MANAGER_VERDICT: ... -->"""
    is_valid, violations = validate_manager_expectation_revision_consumption(
        manager_verdict=manager_verdict,
        raw_response=body_with_beat,
        expectation_revisions={"fundamentals": fund_er, "news": news_er},
    )
    assert not is_valid
    assert any("超预期" in v for v in violations)

    # Body text hallucinates financial numbers without actual
    body_with_fake_num = """【投资裁决报告】
根据非公开渠道，本季度营业收入达到9999亿元，大幅看好。
<!-- MANAGER_VERDICT: ... -->"""
    is_valid_num, violations_num = validate_manager_expectation_revision_consumption(
        manager_verdict=manager_verdict,
        raw_response=body_with_fake_num,
        expectation_revisions={"fundamentals": fund_er, "news": news_er},
    )
    assert not is_valid_num
    assert any("9999亿元" in v for v in violations_num)

    # Body text claims priced-in without evidence
    body_with_priced_in = """【投资裁决报告】
市场已充分定价该利好消息，后续无预期差空间。
<!-- MANAGER_VERDICT: ... -->"""
    is_valid_pi, violations_pi = validate_manager_expectation_revision_consumption(
        manager_verdict=manager_verdict,
        raw_response=body_with_priced_in,
        expectation_revisions={"fundamentals": fund_er, "news": news_er},
    )
    assert not is_valid_pi
    assert any("已定价" in v for v in violations_pi)


def test_end_to_end_double_count_guard_audit_in_verdict():
    """double_count_guard blocks duplicate voting and records double_count_guard_audit."""
    news_er = make_default_expectation_revision(event_type=EVENT_TYPE_EVENT, status=STATUS_AVAILABLE)
    news_er["double_count_guard"] = {
        "status": DOUBLE_COUNT_ACCOUNTED_FOR,
        "prevent_double_voting": True,
        "description": "已计入预测",
    }
    fund_er = make_default_expectation_revision(event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE)

    manager_verdict = {
        "direction": "BULLISH",
        "reason": "多头胜出",
    }
    # Attempting duplicate voting
    body_with_double_vote = "该事件已计入盈利预测，但多头逻辑继续重复计入该利好加一票。"
    is_valid, violations = validate_manager_expectation_revision_consumption(
        manager_verdict=manager_verdict,
        raw_response=body_with_double_vote,
        expectation_revisions={"news": news_er, "fundamentals": fund_er},
    )
    assert not is_valid
    assert any("double_count_guard" in v for v in violations)
