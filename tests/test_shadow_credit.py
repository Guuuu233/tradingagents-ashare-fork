"""Unit tests for H1a shadow credit metrics collection and zero-weighting contract (P1-S)."""

import json
from unittest.mock import MagicMock
import pytest

from tradingagents.agents.utils.agent_states import (
    DEFAULT_FEATURE_FLAGS,
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
    get_protocol_metadata,
)
from tradingagents.agents.utils.shadow_credit import (
    SCHEMA_VERSION,
    calculate_shadow_credit_metrics,
    extract_sample_cohort,
    is_cohort_homogeneous,
    assert_cohort_homogeneity,
    filter_reports_by_cohort,
)
from api.services.report_service import (
    canonicalize_report_result_data,
    create_report,
)


def _build_sample_v2_result_data() -> dict:
    """Build a comprehensive v2 debate result fixture."""
    bull_claims = [
        {
            "claim_id": "INV-1",
            "speaker_key": "Bull",
            "speaker": "Bull Analyst",
            "stance": "bullish",
            "claim": "主力资金持续净流入",
            "evidence": ["主力资金净流入1.2亿"],
            "status": "verified",
            "is_verified": True,
        },
        {
            "claim_id": "INV-2",
            "speaker_key": "Bull",
            "speaker": "Bull Analyst",
            "stance": "bullish",
            "claim": "行业景气度上行",
            "evidence": ["三季报净利润同比大增50%"],
            "status": "verified",
            "is_verified": True,
        },
        {
            "claim_id": "INV-3",
            "speaker_key": "Bull",
            "speaker": "Bull Analyst",
            "stance": "bullish",
            "claim": "突破均线多头排列",
            "evidence": ["放量突破60日均线"],
            "status": "unsupported",
            "is_verified": False,
        },
    ]
    bear_claims = [
        {
            "claim_id": "INV-4",
            "speaker_key": "Bear",
            "speaker": "Bear Analyst",
            "stance": "bearish",
            "claim": "应收账款恶化现金流承压",
            "evidence": ["应收账款周转天数升至120天"],
            "status": "verified",
            "is_verified": True,
        },
        {
            "claim_id": "INV-5",
            "speaker_key": "Bear",
            "speaker": "Bear Analyst",
            "stance": "bearish",
            "claim": "外需降温出口承压",
            "evidence": ["出口交货值下滑15%"],
            "status": "unsupported",
            "is_verified": False,
        },
    ]
    all_claims = bull_claims + bear_claims

    claim_evidence_summary = {
        "INV-1": {"speaker_key": "Bull", "counts": {"verified": 1, "total": 1}, "coverage": 1.0, "decision": "adopt"},
        "INV-2": {"speaker_key": "Bull", "counts": {"verified": 1, "total": 1}, "coverage": 1.0, "decision": "adopt"},
        "INV-3": {"speaker_key": "Bull", "counts": {"verified": 0, "total": 1}, "coverage": 0.0, "decision": "reject"},
        "INV-4": {"speaker_key": "Bear", "counts": {"verified": 1, "total": 1}, "coverage": 1.0, "decision": "adopt"},
        "INV-5": {"speaker_key": "Bear", "counts": {"verified": 0, "total": 1}, "coverage": 0.0, "decision": "reject"},
    }

    challenges = [
        {
            "challenge_id": "CH-1",
            "speaker_key": "Bull",
            "speaker": "Bull Analyst",
            "stance": "bullish",
            "target_claim_id": "INV-4",
            "weakest_point": "忽略了合同负债增长",
            "adopted": True,
        },
        {
            "challenge_id": "CH-2",
            "speaker_key": "Bull",
            "speaker": "Bull Analyst",
            "stance": "bullish",
            "target_claim_id": "INV-5",
            "weakest_point": "出口占比较低仅5%",
            "adopted": False,
        },
        {
            "challenge_id": "CH-3",
            "speaker_key": "Bear",
            "speaker": "Bear Analyst",
            "stance": "bearish",
            "target_claim_id": "INV-1",
            "weakest_point": "资金流入为尾盘脉冲不可持续",
            "adopted": True,
        },
    ]

    manager_verdict = {
        "direction": "偏多",
        "winner": "bull",
        "reason": "多头逻辑主线清晰，基本面增速强劲",
        "position_pct": 45,
        "entry": "25.50",
        "target": "30.00",
        "stop_loss": "23.80",
        "adopted_claim_ids": ["INV-1", "INV-2"],
        "adopted_challenge_ids": ["CH-1", "CH-3"],
        "rejected_claim_ids": ["INV-3", "INV-5"],
        "claim_evidence_summary": claim_evidence_summary,
        "consistency_check_passed": True,
        "failed_checks": [],
    }

    return {
        "final_trade_decision": "【交易决策】建议逢低布局，目标价30.00元，止损位23.80元，建议仓位45%。",
        "macro_report": "宏观经济稳健增长，GDP增速5.2%，货币政策维持宽松。",
        "fundamentals_report": "三季报营收同比增长25%，净利润同比增长50%，毛利率升至35%。",
        "sentiment_report": "市场情绪中性偏多，多空情绪指数65分。",
        "news_report": "行业利好政策出台，支持高端制造产业发展。",
        "market_report": "大盘指数放量突破3000点，成交额突破1万亿元。",
        "smart_money_report": "北向资金净买入45亿元，主力资金净流入1.2亿元。",
        "volume_price_report": "股价突破60日均线，换手率达到8.5%。",
        "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
        "protocol_stage": "manager",
        "investment_debate_state": {
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "manager",
            "claims": all_claims,
            "challenges": challenges,
            "claim_evidence_summary": claim_evidence_summary,
            "manager_verdict": manager_verdict,
            "round_messages": [
                {
                    "message_index": 1,
                    "speaker_key": "Bull",
                    "speaker": "Bull Analyst",
                    "stance": "bullish",
                    "model_name": "deepseek-r1",
                },
                {
                    "message_index": 2,
                    "speaker_key": "Bear",
                    "speaker": "Bear Analyst",
                    "stance": "bearish",
                    "model_name": "qwen-max",
                },
                {
                    "message_index": 3,
                    "speaker_key": "Research Manager",
                    "speaker": "Research Manager",
                    "is_verdict": True,
                    "model_name": "gpt-4o",
                },
            ],
            "feature_flags": {
                "v2_debate_enabled": True,
                "shadow_credit_enabled": True,
                "credit_weighting_enabled": False,
            },
        },
        "feature_flags": {
            "v2_debate_enabled": True,
            "shadow_credit_enabled": True,
            "credit_weighting_enabled": False,
        },
    }


class TestH1aShadowCreditHardRules:
    """Hard rule verification suite for H1a shadow credit metrics (P1-S)."""

    def test_rule1_credit_weighting_enabled_is_strictly_false(self):
        """Hard Rule 1: feature_flags.credit_weighting_enabled and shadow metrics must remain False."""
        fixture = _build_sample_v2_result_data()
        metrics = calculate_shadow_credit_metrics(fixture)

        # In shadow credit metrics
        assert metrics["credit_weighting_enabled"] is False

        # In feature flags
        meta = get_protocol_metadata(fixture)
        assert meta["feature_flags"]["credit_weighting_enabled"] is False

        # Default feature flags must never be flipped
        assert DEFAULT_FEATURE_FLAGS["credit_weighting_enabled"] is False

    def test_rule2_manager_prompt_does_not_leak_credit_scores(self):
        """Hard Rule 2: Manager prompt / verdict inputs must not contain credit score numbers."""
        from tradingagents.agents.managers.research_manager import format_claims_with_verification_for_prompt
        fixture = _build_sample_v2_result_data()
        claims = fixture["investment_debate_state"]["claims"]
        claim_evidence_summary = fixture["investment_debate_state"]["claim_evidence_summary"]

        formatted_text = format_claims_with_verification_for_prompt(
            claims=claims,
            claim_evidence_summary=claim_evidence_summary,
        )

        assert "shadow_credit" not in formatted_text
        assert "credit_score" not in formatted_text
        assert "信用分" not in formatted_text
        assert "加权得分" not in formatted_text

    def test_rule3_t_plus_5_unreached_is_strictly_none(self):
        """Hard Rule 3: T+5 window unreached must be None, never False or 0."""
        fixture = _build_sample_v2_result_data()
        metrics = calculate_shadow_credit_metrics(fixture)

        assert metrics["t_plus_5_direction_hit"] is None
        assert metrics["t_plus_5_direction_hit"] is not False
        assert metrics["t_plus_5_direction_hit"] != 0

    def test_rule4_missing_market_data_is_typed_gap_not_zero(self):
        """Hard Rule 4: Missing market data / zero-denominator rates are typed None, not 0."""
        empty_data = {
            "protocol_version": PROTOCOL_VERSION_V1_LEGACY,
            "investment_debate_state": {
                "claims": [],
                "challenges": [],
            },
        }
        metrics = calculate_shadow_credit_metrics(empty_data)

        assert metrics["bull_verified_rate"] is None
        assert metrics["bear_verified_rate"] is None
        assert metrics["bull_challenge_adoption_rate"] is None
        assert metrics["bear_challenge_adoption_rate"] is None
        assert metrics["manager_evidence_coverage"] is None
        assert metrics["t_plus_5_direction_hit"] is None

    def test_rule5_collection_on_vs_off_decision_fields_identical(self):
        """Hard Rule 5: Closing vs opening shadow metrics collection produces 100% identical decision fields."""
        fixture = _build_sample_v2_result_data()

        # Enabled
        metrics_on = calculate_shadow_credit_metrics(fixture)
        res_on = canonicalize_report_result_data(fixture)

        # Disabled
        fixture_off = _build_sample_v2_result_data()
        fixture_off["feature_flags"]["shadow_credit_enabled"] = False
        fixture_off["investment_debate_state"]["feature_flags"]["shadow_credit_enabled"] = False
        res_off = canonicalize_report_result_data(fixture_off)

        # Assert key trading decision fields are strictly identical
        assert res_on["final_trade_decision"] == res_off["final_trade_decision"]
        assert res_on["investment_debate_state"]["manager_verdict"]["direction"] == res_off["investment_debate_state"]["manager_verdict"]["direction"]
        assert res_on["investment_debate_state"]["manager_verdict"]["position_pct"] == res_off["investment_debate_state"]["manager_verdict"]["position_pct"]
        assert res_on["investment_debate_state"]["manager_verdict"]["entry"] == res_off["investment_debate_state"]["manager_verdict"]["entry"]
        assert res_on["investment_debate_state"]["manager_verdict"]["target"] == res_off["investment_debate_state"]["manager_verdict"]["target"]
        assert res_on["investment_debate_state"]["manager_verdict"]["stop_loss"] == res_off["investment_debate_state"]["manager_verdict"]["stop_loss"]

    def test_rule6_legacy_report_missing_fields_canonicalize_does_not_crash(self):
        """Hard Rule 6: Old reports missing fields canonicalize cleanly without crash, metrics can be {}."""
        old_report = {
            "market_report": "旧版本市场分析报告正文",
            "fundamentals_report": "旧版本基本面报告正文",
        }
        canonical = canonicalize_report_result_data(old_report)
        assert canonical is not None
        assert canonical["market_report"] == "旧版本市场分析报告正文"

        # Explicit empty dict in old report is preserved without crash
        old_report_with_empty = {
            "market_report": "旧版本",
            "shadow_credit_metrics": {},
        }
        canonical_empty = canonicalize_report_result_data(old_report_with_empty)
        assert canonical_empty is not None
        assert canonical_empty["shadow_credit_metrics"] == {}


class TestShadowCreditMetricsComputation:
    """Detailed calculation and schema compliance tests."""

    def test_metrics_schema_and_required_fields(self):
        """Verify all required fields exist and have correct types."""
        fixture = _build_sample_v2_result_data()
        metrics = calculate_shadow_credit_metrics(fixture)

        assert metrics["schema_version"] == SCHEMA_VERSION
        assert metrics["schema_version"] == "h1a_json_v1"
        assert metrics["credit_weighting_enabled"] is False
        assert isinstance(metrics["sample_count"], int)
        assert metrics["sample_count"] == 1
        assert metrics["protocol_version"] == PROTOCOL_VERSION_V2_STRUCTURED

        # Rates
        assert metrics["bull_verified_rate"] == 0.6667 or metrics["bull_verified_rate"] == round(2 / 3, 4)
        assert metrics["bear_verified_rate"] == 0.5000 or metrics["bear_verified_rate"] == 0.5
        assert metrics["bull_challenge_adoption_rate"] == 0.5000 or metrics["bull_challenge_adoption_rate"] == 0.5
        assert metrics["bear_challenge_adoption_rate"] == 1.0

        # Analyst utilization by role
        assert isinstance(metrics["analyst_utilization_by_role"], dict)
        assert "macro" in metrics["analyst_utilization_by_role"]
        assert "fundamentals" in metrics["analyst_utilization_by_role"]

        # Manager evidence coverage & consistency gate
        assert isinstance(metrics["manager_evidence_coverage"], (float, int))
        assert metrics["manager_consistency_gate_triggered"] is False

        # Models by stance
        assert isinstance(metrics["model_id_by_stance"], dict)
        assert metrics["model_id_by_stance"]["bull"] == "deepseek-r1"
        assert metrics["model_id_by_stance"]["bear"] == "qwen-max"
        assert metrics["model_id_by_stance"]["manager"] == "gpt-4o"

    def test_missing_model_typed_none(self):
        """Missing model IDs must be typed None, never fabricated."""
        fixture = _build_sample_v2_result_data()
        # Remove model_name from messages
        for msg in fixture["investment_debate_state"]["round_messages"]:
            msg.pop("model_name", None)

        metrics = calculate_shadow_credit_metrics(fixture)
        assert metrics["model_id_by_stance"]["bull"] is None
        assert metrics["model_id_by_stance"]["bear"] is None
        assert metrics["model_id_by_stance"]["manager"] is None

    def test_manager_consistency_gate_triggered_when_failed(self):
        """Manager consistency gate triggered is True when consistency checks fail."""
        fixture = _build_sample_v2_result_data()
        fixture["investment_debate_state"]["manager_verdict"]["consistency_check_passed"] = False
        fixture["investment_debate_state"]["manager_verdict"]["failed_checks"] = ["多头胜裁决下方向不得为看空"]

        metrics = calculate_shadow_credit_metrics(fixture)
        assert metrics["manager_consistency_gate_triggered"] is True

    def test_replay_determinism(self):
        """Replaying calculation on the same result_data produces identical JSON."""
        fixture = _build_sample_v2_result_data()
        metrics1 = calculate_shadow_credit_metrics(fixture)
        metrics2 = calculate_shadow_credit_metrics(fixture)

        json1 = json.dumps(metrics1, sort_keys=True, ensure_ascii=False)
        json2 = json.dumps(metrics2, sort_keys=True, ensure_ascii=False)
        assert json1 == json2

    def test_input_immutability(self):
        """calculate_shadow_credit_metrics must not mutate input result_data."""
        fixture = _build_sample_v2_result_data()
        raw_copy = json.dumps(fixture, sort_keys=True, ensure_ascii=False)

        calculate_shadow_credit_metrics(fixture)

        after_copy = json.dumps(fixture, sort_keys=True, ensure_ascii=False)
        assert raw_copy == after_copy


class TestCohortMetadataAndHomogeneity:
    """Test suite for DAV-601: Cohort extraction, filtering, and homogeneity checks."""

    def test_extract_sample_cohort_from_different_locations(self):
        """extract_sample_cohort retrieves triad and commit sha from top-level or nested structures."""
        # 1. Top-level
        sample_top = {
            "decision_model_version": "decision_model.v1",
            "evidence_contract_version": "evidence_contract.v0",
            "price_basis_version": "price_basis.unspecified",
            "generated_by_commit_sha": "e10b106df9d3173258b0a3fefc90ba7f3559f109",
        }
        res = extract_sample_cohort(sample_top)
        assert res["decision_model_version"] == "decision_model.v1"
        assert res["evidence_contract_version"] == "evidence_contract.v0"
        assert res["price_basis_version"] == "price_basis.unspecified"
        assert res["generated_by_commit_sha"] == "e10b106df9d3173258b0a3fefc90ba7f3559f109"

        # 2. Nested under result_data
        sample_nested = {
            "result_data": {
                "decision_model_version": "decision_model.v1",
                "evidence_contract_version": "evidence_contract.v0",
                "price_basis_version": "price_basis.unspecified",
                "generated_by_commit_sha": "e10b106df9d3173258b0a3fefc90ba7f3559f109",
            }
        }
        res_nested = extract_sample_cohort(sample_nested)
        assert res_nested["decision_model_version"] == "decision_model.v1"
        assert res_nested["evidence_contract_version"] == "evidence_contract.v0"
        assert res_nested["price_basis_version"] == "price_basis.unspecified"
        assert res_nested["generated_by_commit_sha"] == "e10b106df9d3173258b0a3fefc90ba7f3559f109"

        # 3. Unlabeled / legacy sample -> all None
        sample_legacy = {"symbol": "600519.SH"}
        res_legacy = extract_sample_cohort(sample_legacy)
        assert res_legacy["decision_model_version"] is None
        assert res_legacy["evidence_contract_version"] is None
        assert res_legacy["price_basis_version"] is None
        assert res_legacy["generated_by_commit_sha"] is None

    def test_cohort_homogeneity_pure_vs_mixed(self):
        """Homogeneity checker returns True for single cohort pool, False for mixed generations."""
        pure_legacy = [
            {"symbol": "600519.SH"},
            {"symbol": "000858.SZ", "decision_model_version": "decision_model.legacy_unversioned"},
        ]
        is_homo, cohort_key = is_cohort_homogeneous(pure_legacy)
        assert is_homo is True
        assert cohort_key == "legacy_unversioned"
        assert_cohort_homogeneity(pure_legacy)

        pure_v1 = [
            {
                "symbol": "600519.SH",
                "decision_model_version": "decision_model.v1",
                "evidence_contract_version": "evidence_contract.v0",
                "price_basis_version": "price_basis.unspecified",
                "generated_by_commit_sha": "sha_111",
            },
            {
                "symbol": "000858.SZ",
                "decision_model_version": "decision_model.v1",
                "evidence_contract_version": "evidence_contract.v0",
                "price_basis_version": "price_basis.unspecified",
                "generated_by_commit_sha": "sha_222",
            },
        ]
        is_homo_v1, cohort_key_v1 = is_cohort_homogeneous(pure_v1)
        assert is_homo_v1 is True
        assert cohort_key_v1 == "decision_model.v1:evidence_contract.v0:price_basis.unspecified"
        assert_cohort_homogeneity(pure_v1)

        # Mixed generations: legacy + v1
        mixed = pure_legacy + pure_v1
        is_homo_m, _ = is_cohort_homogeneous(mixed)
        assert is_homo_m is False
        with pytest.raises(ValueError, match="Mixed cohort generations detected"):
            assert_cohort_homogeneity(mixed)

    def test_filter_reports_by_cohort_fail_closed_on_empty_spec(self):
        """filter_reports_by_cohort raises ValueError if cohort spec is missing or empty."""
        samples = [{"symbol": "600519.SH"}]
        with pytest.raises(ValueError, match="Cohort specification is required"):
            filter_reports_by_cohort(samples, cohort=None)
        with pytest.raises(ValueError, match="Cohort specification is required"):
            filter_reports_by_cohort(samples, cohort="")
