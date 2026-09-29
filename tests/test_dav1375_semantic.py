"""DAV-1375: 判定词、并列名词片段和技术指标周期不是事实证据。"""

import pytest

from tradingagents.agents.utils.evidence_verifier import (
    _sem_extract_nums,
    audit_claim_semantic_coverage,
    decompose_claim_propositions,
)


@pytest.mark.parametrize("claim", [
    "5.1亿注资难抵百亿失血",
    "30元破位下探28.6元",
])
def test_judgment_cannot_be_supported_by_matching_numbers(claim):
    result = audit_claim_semantic_coverage(claim, verified_evidence=[claim], report_fields={})
    assert result["semantic_decision"] == "non_factual_only"
    assert result["semantic_counts"]["supported"] == 0
    assert len(result["proposition_audit"]) == 1


def test_historical_observation_of_downward_touch_remains_factual():
    assert decompose_claim_propositions("盘中下探至28.6元")[0]["type"] == "factual_numeric"


def test_coordinated_noun_fragment_is_not_independent_fact():
    claim = "50SMA与MACD将引致破位踩踏"
    props = decompose_claim_propositions(claim)
    assert len(props) == 1
    assert props[0]["text"] == claim
    result = audit_claim_semantic_coverage(claim, verified_evidence=["50SMA"], report_fields={})
    assert result["semantic_decision"] == "non_factual_only"
    assert result["semantic_counts"]["supported"] == 0


@pytest.mark.parametrize("claim", [
    "MACD与50SMA将引致破位踩踏",
    "200日线与RSI(14)将破位",
])
def test_multiple_noun_fragments_merge_into_neighboring_predicate(claim):
    props = decompose_claim_propositions(claim)
    assert len(props) == 1
    assert props[0]["text"] == claim
    assert props[0]["type"] == "scenario_hypothesis"


def test_real_numeric_fact_not_merged_into_predicted_clause():
    props = decompose_claim_propositions("18.5倍PE且面临二次杀跌")
    assert [p["type"] for p in props] == ["factual_numeric", "scenario_hypothesis"]


@pytest.mark.parametrize("claim", [
    "50SMA均线带与MACD将引致破位踩踏",
    "布林下轨与200日线将引致破位踩踏",
])
def test_modified_indicator_noun_phrases_merge_before_classification(claim):
    props = decompose_claim_propositions(claim)
    assert len(props) == 1
    assert props[0]["text"] == claim
    assert props[0]["type"] == "scenario_hypothesis"


@pytest.mark.parametrize("claim, expected", [
    ("50SMA", set()),
    ("200日线", set()),
    ("RSI(14)", set()),
    ("MACD(12,26,9)", set()),
    ("KDJ（9，3，3）", set()),
    ("50SMA(89.28)与RSI(14)录得57.99", {"89.28", "57.99"}),
    ("200日均线为28.6元", {"28.6"}),
    ("近5日跌2.53%", {"5", "2.53%"}),
])
def test_indicator_period_is_not_verified_numeric_atom(claim, expected):
    assert _sem_extract_nums(claim) == expected


@pytest.mark.parametrize("claim", ["50SMA", "200日线", "RSI(14)", "MACD(12,26,9)", "KDJ(9,3,3)"])
def test_indicator_name_alone_cannot_gain_numeric_support(claim):
    result = audit_claim_semantic_coverage(claim, verified_evidence=[claim], report_fields={})
    assert result["semantic_counts"]["supported"] == 0
    assert result["semantic_decision"] == "non_factual_only"


@pytest.mark.parametrize("word", [
    "难抵", "难挡", "难改", "将破", "或破", "有望", "料将", "上探",
])
def test_ruled_prediction_words_never_gain_support(word):
    claim = f"30元{word}28.6元"
    result = audit_claim_semantic_coverage(claim, verified_evidence=[claim])
    assert result["semantic_decision"] == "non_factual_only"
    assert result["semantic_counts"]["supported"] == 0


@pytest.mark.parametrize("text", ["破位下探", "下探28.6元", "将下探", "或下探", "下探至28.6元"])
def test_downward_probe_without_completed_time_is_prediction(text):
    assert decompose_claim_propositions(text)[0]["type"] == "scenario_hypothesis"


@pytest.mark.parametrize("text", [
    "盘中下探至28.6元", "一度下探至1221", "最低下探到28.6元",
    "曾下探至28.6元", "已下探到28.6元", "盘中下探至28.6元仍维持低位",
])
def test_completed_downward_probe_with_price_is_factual(text):
    assert decompose_claim_propositions(text)[0]["type"] not in (
        "scenario_hypothesis", "interpretation_causal"
    )


def test_completed_probe_without_price_is_not_released():
    assert decompose_claim_propositions("盘中下探后企稳")[0]["type"] == "scenario_hypothesis"


@pytest.mark.parametrize("text", [
    "盘中或下探至28.6元", "最低会下探到28元",
    "盘中或一度下探至28.6元", "已可能下探至28.6元",
])
def test_past_probe_exemption_must_not_swallow_future_markers(text):
    assert decompose_claim_propositions(text)[0]["type"] == "scenario_hypothesis"


@pytest.mark.parametrize("text", [
    "无背离属顺势破位下探", "无背离属顺势破位", "价格属于超跌",
    "地量系买盘枯竭", "缩量实为买盘断崖", "负营运资本乃供应链统治力",
])
def test_causal_attribution_predicates_are_interpretation(text):
    result = audit_claim_semantic_coverage(text, verified_evidence=[text])
    assert [p["kind"] for p in result["proposition_audit"]] == ["interpretation_causal"]
    assert result["semantic_decision"] == "non_factual_only"


@pytest.mark.parametrize("noun", [
    "属性", "属地", "金属", "下属", "亲属", "系统", "关系", "体系", "系列",
    "联系", "派系", "直系", "菌系", "属实", "家属", "军属", "眷属",
    "维系", "干系", "根系", "山系", "世系", "语系", "星系", "嫡系",
])
def test_noun_compounds_do_not_trigger_attribution(noun):
    assert decompose_claim_propositions(f"{noun}增长30元")[0]["type"] == "factual_numeric"


def test_relational_noun_phrase_does_not_trigger_attribution():
    assert decompose_claim_propositions("维系客户关系")[0]["type"] != "interpretation_causal"


@pytest.mark.parametrize("text", ["隶属洗盘", "从属假突破"])
def test_true_attribution_remains_interpretation(text):
    assert decompose_claim_propositions(text)[0]["type"] == "interpretation_causal"
