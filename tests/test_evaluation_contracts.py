"""Tests for P3-H2.0 Evaluation Metrics and Weekly Dashboard Schema Contracts.

Verifies:
1. Pydantic v2 and TypedDict Schema definitions and JSON Schema export.
2. 4-Quadrant metric matrix correctness (Protocol, Data Gaps, Debate Quality, T+5 Shadow).
3. Structural vs Operational data_gaps classification and resident fault count logic.
4. WeeklyMetricsJSON aggregation, deduplication audit, and H1b 7-dimension gate integration.
5. WeeklySummaryMD markdown rendering and audit conformity.
6. Multi-scenario mock fixtures (Bull Win, Bear Win, Balanced Tie, Gaps, Degenerate, 60-sample dataset).
7. Edge cases, zero denominators, legitimate omissions, and robust error handling.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import pytest

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
)
from tradingagents.agents.utils.evaluation_schemas import (
    EVALUATION_MATRIX_SCHEMA_VERSION,
    WEEKLY_METRICS_SCHEMA_VERSION,
    WEEKLY_SUMMARY_MD_SCHEMA_VERSION,
    EvaluationMetricMatrixModel,
    WeeklyMetricsJSONModel,
    WeeklySummaryMDModel,
    build_evaluation_metric_matrix,
    build_weekly_metrics,
    get_evaluation_metric_matrix_json_schema,
    get_weekly_metrics_json_schema,
    get_weekly_summary_md_json_schema,
    render_weekly_summary_markdown,
    validate_evaluation_matrix,
    validate_weekly_metrics,
    validate_weekly_summary_md,
)
from tradingagents.dataflows.trade_calendar import (
    calculate_t_plus_5_date,
    get_t_plus_n_trading_day,
    trading_days_forward,
)
from tests.mock_evaluations.mock_scenarios import (
    create_mock_balanced_tie_report,
    create_mock_bear_win_report,
    create_mock_bull_win_report,
    create_mock_degenerate_and_gate_report,
    create_mock_operational_gaps_report,
    create_mock_structural_gaps_report,
    generate_60_sample_weekly_dataset,
    save_mock_artifacts,
)


class TestEvaluationSchemasAndJsonSchema:
    """Test Pydantic v2 models, validation, and JSON Schema generation."""

    def test_json_schemas_export_valid_structure(self):
        matrix_schema = get_evaluation_metric_matrix_json_schema()
        assert matrix_schema["type"] == "object"
        assert "properties" in matrix_schema
        assert "quadrant_1_protocol_metadata" in matrix_schema["properties"]
        assert "quadrant_2_data_sources_and_gaps" in matrix_schema["properties"]
        assert "quadrant_3_debate_quality" in matrix_schema["properties"]
        assert "quadrant_4_t_plus_5_and_shadow" in matrix_schema["properties"]

        weekly_schema = get_weekly_metrics_json_schema()
        assert weekly_schema["type"] == "object"
        assert "properties" in weekly_schema
        assert "week_identifier" in weekly_schema["properties"]
        assert "samples" in weekly_schema["properties"]
        assert "weekly_aggregate" in weekly_schema["properties"]

        md_schema = get_weekly_summary_md_json_schema()
        assert md_schema["type"] == "object"
        assert "properties" in md_schema
        assert "markdown_content" in md_schema["properties"]

    def test_pydantic_model_roundtrip_serialization(self):
        raw_bull = create_mock_bull_win_report()
        matrix_dict = build_evaluation_metric_matrix(
            raw_bull,
            report_id="test_001",
            symbol="600519.SH",
            security_name="贵州茅台",
            trade_date="2026-08-20",
            industry="白酒",
            t_plus_5_price=1700.0,
        )

        model = validate_evaluation_matrix(matrix_dict)
        assert isinstance(model, EvaluationMetricMatrixModel)
        assert model.schema_version == EVALUATION_MATRIX_SCHEMA_VERSION
        assert model.quadrant_1_protocol_metadata.symbol == "600519.SH"
        assert model.quadrant_4_t_plus_5_and_shadow.t_plus_5_price == 1700.0

        # Model dump and re-validate
        dumped_dict = model.model_dump()
        dumped_json = model.model_dump_json()
        reloaded_model = EvaluationMetricMatrixModel.model_validate_json(dumped_json)
        assert reloaded_model.matrix_id == model.matrix_id
        assert reloaded_model.quadrant_1_protocol_metadata.report_id == "test_001"


class TestFourQuadrantsEvaluationMatrix:
    """Test detailed metric computation across all four quadrants."""

    def test_quadrant_1_protocol_and_model_metadata(self):
        raw_bull = create_mock_bull_win_report()
        matrix = build_evaluation_metric_matrix(
            raw_bull,
            report_id="rep_q1",
            symbol="600519.SH",
            security_name="贵州茅台",
            trade_date="2026-08-20",
            industry="白酒",
            market_regime="bull_trend",
            latency_ms=1300.0,
            token_usage={"prompt": 5000, "completion": 2000, "total": 7000},
        )
        q1 = matrix["quadrant_1_protocol_metadata"]
        assert q1["report_id"] == "rep_q1"
        assert q1["symbol"] == "600519.SH"
        assert q1["security_name"] == "贵州茅台"
        assert q1["trade_date"] == "2026-08-20"
        assert q1["industry"] == "白酒"
        assert q1["market_regime"] == "bull_trend"
        assert q1["protocol_version"] == PROTOCOL_VERSION_V2_STRUCTURED
        assert q1["protocol_stage"] == "manager"
        assert q1["tiebreak_skipped"] is False
        assert q1["debate_degenerate"] is False
        assert q1["feature_flags"]["credit_weighting_enabled"] is False
        assert q1["model_assignments"]["bull"] == "deepseek-r1"
        assert q1["model_assignments"]["bear"] == "qwen-max"
        assert q1["latency_ms"] == 1300.0
        assert q1["token_usage"]["total"] == 7000

    def test_quadrant_2_structural_vs_operational_gaps(self):
        # Structural gaps
        struct_rep = create_mock_structural_gaps_report()
        m_struct = build_evaluation_metric_matrix(struct_rep)
        q2_s = m_struct["quadrant_2_data_sources_and_gaps"]
        gaps_s = q2_s["data_gaps"]
        assert len(gaps_s) == 2
        assert gaps_s[0]["gap_class"] == "structural"
        assert gaps_s[0]["status"] == "unavailable"
        assert gaps_s[1]["gap_class"] == "structural"
        assert gaps_s[1]["status"] == "refused"
        assert q2_s["gaps_summary"]["structural_count"] == 2
        assert q2_s["gaps_summary"]["operational_count"] == 0
        assert q2_s["gaps_summary"]["resident_fault_count"] == 0  # Structural does not count as resident fault

        # Operational gaps
        oper_rep = create_mock_operational_gaps_report()
        m_oper = build_evaluation_metric_matrix(oper_rep)
        q2_o = m_oper["quadrant_2_data_sources_and_gaps"]
        gaps_o = q2_o["data_gaps"]
        assert len(gaps_o) == 2
        assert gaps_o[0]["gap_class"] == "operational"
        assert gaps_o[0]["status"] == "timeout"
        assert gaps_o[1]["gap_class"] == "operational"
        assert gaps_o[1]["status"] == "failed"
        assert q2_o["gaps_summary"]["structural_count"] == 0
        assert q2_o["gaps_summary"]["operational_count"] == 2
        assert q2_o["gaps_summary"]["resident_fault_count"] == 2

        # Data utilization
        assert q2_o["data_utilization"]["seven_reports_utilization"]["status"] == "valid"
        assert q2_o["data_utilization"]["macro_utilization"]["status"] == "valid"
        assert q2_o["data_utilization"]["fundamentals_utilization"]["status"] == "valid"

    def test_quadrant_3_debate_quality_six_dimensions(self):
        raw_bull = create_mock_bull_win_report()
        matrix = build_evaluation_metric_matrix(raw_bull)
        q3 = matrix["quadrant_3_debate_quality"]

        # 1. Verified Rate
        vr = q3["verified_rates"]
        assert vr["bull_verified_rate"]["rate"] == 1.0
        assert vr["bear_verified_rate"]["rate"] == 0.0
        assert vr["bull_bear_verified_delta"]["rate"] == 1.0

        # 2. Battlefield Coverage
        bc = q3["battlefield_coverage"]
        assert bc["total_claims_count"] == 3
        assert bc["verified_claims_count"] == 2
        assert bc["unsupported_claims_count"] == 1
        assert bc["manager_evidence_coverage"] is not None

        # 3. Evidence Recycling
        er = q3["evidence_recycling"]
        assert er["evidence_recycling_rate"]["status"] == "valid"
        assert er["unique_claim_count"] == 3
        assert er["clone_rate"] == 0.0

        # 4. Challenge Metrics
        cm = q3["challenge_metrics"]
        assert cm["challenge_count"]["numerator"] == 1
        assert cm["challenge_adoption_rate"]["rate"] == 1.0
        assert cm["bull_challenge_adoption_rate"] == 1.0
        assert cm["challenge_evidence_status"]["verified"] == 1

        # 5. Field Completeness
        fc = q3["field_completeness"]
        assert fc["field_completeness_rate"]["status"] == "complete"
        assert fc["field_completeness_rate"]["rate"] == 1.0
        assert "confidence" in fc["present_fields"]
        assert "target_price" in fc["present_fields"]

        # 6. Debate Health
        dh = q3["debate_health"]
        assert dh["consistency_check_passed"] is True
        assert dh["manager_consistency_gate_triggered"] is False
        assert dh["debate_degenerate"] is False

    def test_quadrant_4_t_plus_5_and_shadow_weighting(self):
        # Bull win with T+5 price gain
        raw_bull = create_mock_bull_win_report()
        matrix_bull = build_evaluation_metric_matrix(raw_bull, t_plus_5_price=1708.0)
        q4_b = matrix_bull["quadrant_4_t_plus_5_and_shadow"]
        assert q4_b["decision_direction"] == "BUY"
        assert q4_b["debate_winner"] == "bull"
        assert q4_b["entry_price"] == 1650.0
        assert q4_b["target_price"] == 1820.0
        assert q4_b["stop_loss_price"] == 1580.0
        assert q4_b["t_plus_5_price"] == 1708.0
        assert q4_b["t_plus_5_return_pct"] == pytest.approx(3.52, 0.01)
        assert q4_b["t_plus_5_direction_hit"] is True
        assert q4_b["t_plus_5_status"] == "due_and_evaluated"

        # Shadow metrics isolation
        shadow_m = q4_b["shadow_weighted_metrics"]
        assert shadow_m["credit_weighting_enabled"] is False
        assert shadow_m["credit_weighting_active"] is False
        assert shadow_m["system_gate_status"] == "FAIL"  # No history passed

        # Bear win with T+5 price loss (profitable short call)
        raw_bear = create_mock_bear_win_report()
        matrix_bear = build_evaluation_metric_matrix(raw_bear, t_plus_5_price=8.20)
        q4_bear = matrix_bear["quadrant_4_t_plus_5_and_shadow"]
        assert q4_bear["decision_direction"] == "SELL"
        assert q4_bear["debate_winner"] == "bear"
        assert q4_bear["entry_price"] == 8.50
        assert q4_bear["t_plus_5_price"] == 8.20
        assert q4_bear["t_plus_5_return_pct"] == pytest.approx(-3.53, 0.01)
        assert q4_bear["t_plus_5_direction_hit"] is True  # SELL price drop is a hit


class TestSpecialScenariosAndEdgeCases:
    """Test edge cases such as legitimate omissions, degenerate states, and empty inputs."""

    def test_balanced_hold_legitimate_omissions(self):
        raw_hold = create_mock_balanced_tie_report()
        matrix = build_evaluation_metric_matrix(raw_hold, t_plus_5_price=29.05)
        q3 = matrix["quadrant_3_debate_quality"]
        fc = q3["field_completeness"]

        # HOLD target_price omitted with note is recognized as legitimate omission
        assert "target_price" in fc["legitimate_omissions"]
        assert fc["field_completeness_rate"]["status"] == "complete"
        assert fc["field_completeness_rate"]["rate"] == 1.0

        q4 = matrix["quadrant_4_t_plus_5_and_shadow"]
        assert q4["decision_direction"] == "HOLD"
        assert q4["target_price"] is None
        assert q4["t_plus_5_direction_hit"] is True  # 29.05 vs 29.0 is within 3%

    def test_degenerate_and_consistency_gate_trigger(self):
        raw_degen = create_mock_degenerate_and_gate_report()
        matrix = build_evaluation_metric_matrix(raw_degen)
        q1 = matrix["quadrant_1_protocol_metadata"]
        q3 = matrix["quadrant_3_debate_quality"]

        assert q1["debate_degenerate"] is True
        assert q3["debate_health"]["debate_degenerate"] is True
        assert q3["debate_health"]["consistency_check_passed"] is False
        assert q3["debate_health"]["manager_consistency_gate_triggered"] is True
        assert len(q3["debate_health"]["failed_checks"]) > 0

    def test_empty_or_malformed_input_graceful_handling(self):
        matrix = build_evaluation_metric_matrix({})
        assert matrix["schema_version"] == EVALUATION_MATRIX_SCHEMA_VERSION
        assert matrix["quadrant_1_protocol_metadata"]["protocol_version"] == PROTOCOL_VERSION_V1_LEGACY
        assert matrix["quadrant_2_data_sources_and_gaps"]["gaps_summary"]["total_gaps"] == 0
        assert matrix["quadrant_3_debate_quality"]["debate_health"]["consistency_check_passed"] is True
        assert matrix["quadrant_4_t_plus_5_and_shadow"]["t_plus_5_status"] == "pending_due"

        # Validate with Pydantic
        model = validate_evaluation_matrix(matrix)
        assert model.schema_version == EVALUATION_MATRIX_SCHEMA_VERSION


class TestWeeklyMetricsAggregationAndDashboard:
    """Test WeeklyMetricsJSON generation, deduplication audit, and H1b gate evaluation."""

    def test_60_sample_weekly_dataset_generation_and_validation(self):
        weekly_data = generate_60_sample_weekly_dataset(
            start_date="2026-07-06",
            end_date="2026-08-20",
            week_identifier="week_202634",
        )

        model = validate_weekly_metrics(weekly_data)
        assert isinstance(model, WeeklyMetricsJSONModel)
        assert model.schema_version == WEEKLY_METRICS_SCHEMA_VERSION
        assert model.week_identifier == "week_202634"
        assert model.sample_count == 60
        assert len(model.samples) == 60

        aggs = model.weekly_aggregate
        # 1. Overview
        assert aggs.overview.total_samples == 60
        assert aggs.overview.unique_symbols == 20
        assert aggs.overview.unique_industries >= 5
        assert aggs.overview.max_single_symbol_share <= 0.15
        assert 0.40 <= aggs.overview.bull_decision_ratio <= 0.60

        # 2. Quality
        assert aggs.quality_aggregates.avg_bull_verified_rate is not None
        assert aggs.quality_aggregates.avg_bear_verified_rate is not None
        assert aggs.quality_aggregates.delta_verified_rate <= 0.18
        assert aggs.quality_aggregates.avg_clone_rate <= 0.05
        assert aggs.quality_aggregates.avg_field_completeness_rate == 1.0

        # 3. Data gaps
        assert aggs.data_gaps_aggregates.total_structural_gaps > 0
        assert aggs.data_gaps_aggregates.total_operational_gaps > 0

        # 4. T+5 calibration
        assert aggs.t5_calibration.completeness_rate >= 0.95
        assert aggs.t5_calibration.completed_sample_count == 60
        assert aggs.t5_calibration.direction_accuracy_rate is not None

        # 5. H1b 7-dimension system gates
        assert aggs.h1b_system_gates_evaluation.passed is True
        assert aggs.h1b_system_gates_evaluation.recommendation == "ELIGIBLE_FOR_ACTIVATION"
        iso = aggs.h1b_system_gates_evaluation.model_isolation
        assert iso.credit_weighting_active is True
        assert iso.global_fallback_shadow is False
        assert iso.abnormal_model_ratio == 0.0

        # 6. Deduplication audit
        assert aggs.deduplication_audit.status == "PASSED_NO_DUPLICATES"
        assert aggs.deduplication_audit.duplicate_samples_dropped == 0

    def test_deduplication_audit_drops_duplicate_samples(self):
        rep1 = create_mock_bull_win_report()
        rep1["id"] = "rep_dup_001"
        rep2 = create_mock_bear_win_report()
        rep2["id"] = "rep_dup_002"

        # Duplicate of rep1
        rep1_dup = create_mock_bull_win_report()
        rep1_dup["id"] = "rep_dup_001"

        samples = [rep1, rep2, rep1_dup]
        weekly = build_weekly_metrics(
            samples,
            week_identifier="week_202634",
            start_date="2026-08-15",
            end_date="2026-08-20",
            historical_sample_ids=["rep_hist_already_in_db"],
        )

        assert weekly["sample_count"] == 2
        assert len(weekly["samples"]) == 2
        assert weekly["weekly_aggregate"]["deduplication_audit"]["duplicate_samples_dropped"] == 1
        assert weekly["weekly_aggregate"]["deduplication_audit"]["status"] == "DUPLICATES_DETECTED"

    def test_weekly_summary_markdown_rendering(self):
        weekly_data = generate_60_sample_weekly_dataset(
            start_date="2026-07-06",
            end_date="2026-08-20",
            week_identifier="week_202634",
        )
        md_text = render_weekly_summary_markdown(weekly_data)

        assert "# 周度评测与离线复算看板报告 (week_202634)" in md_text
        assert "## 一、 评测大盘 KPI 核心概览" in md_text
        assert "## 二、 四大象限细分指标透视" in md_text
        assert "## 三、 H1b 信用加权 7 维激活门槛离线复算看板" in md_text
        assert "## 四、 结论与后续行动建议" in md_text
        assert "1. 样本量与多样性 (N)" in md_text
        assert "2. 分侧样本与证据 (Side)" in md_text
        assert "3. 时间跨度 (Time)" in md_text
        assert "4. T+5 完整率 (T+5)" in md_text
        assert "5. 多空平衡性 (Balance)" in md_text
        assert "6. 偏置冻结线 (Bias Freeze)" in md_text
        assert "7. 权重幅度约束 (Magnitude)" in md_text
        assert "ELIGIBLE_FOR_ACTIVATION" in md_text
        assert "### 分层隔离与单模型偏置冻结预警" in md_text

        # Validate with Pydantic model
        md_model = validate_weekly_summary_md(
            {
                "schema_version": WEEKLY_SUMMARY_MD_SCHEMA_VERSION,
                "week_identifier": "week_202634",
                "title": "周度评测与离线复算看板报告 (week_202634)",
                "markdown_content": md_text,
            }
        )
        assert isinstance(md_model, WeeklySummaryMDModel)
        assert md_model.week_identifier == "week_202634"

    def test_weekly_h1b_model_isolation_keeps_false_when_gates_fail(self):
        weekly_data = build_weekly_metrics(
            [create_mock_bull_win_report()],
            week_identifier="week_small",
            start_date="2026-08-15",
            end_date="2026-08-15",
        )
        h1b = weekly_data["weekly_aggregate"]["h1b_system_gates_evaluation"]
        assert h1b["passed"] is False
        assert h1b["recommendation"] == "KEEP_FALSE"
        iso = h1b["model_isolation"]
        assert iso["credit_weighting_active"] is False
        assert iso["global_fallback_shadow"] is True
        assert iso["bias_freeze_reasons"]

        md_text = render_weekly_summary_markdown(weekly_data)
        assert "KEEP_FALSE" in md_text
        assert "7 维门槛动态 Gap 追踪" in md_text
        assert "Shadow-only" in md_text or "global_fallback_shadow=True" in md_text


class TestH2E2EGatesPipelineIntegration:
    """End-to-end: weekly recalc -> H1b gates -> layered isolation audit trail."""

    def test_recalculate_weekly_metrics_cli_includes_isolation_banner(self, tmp_path):
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "recalculate_weekly_metrics.py"
        cmd = [
            sys.executable,
            str(script_path),
            "--week",
            "202634",
            "--use-mock",
            "--dry-run",
            "--format",
            "text",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0
        assert "H1b 信用加权 7 维激活门槛离线复算结论" in proc.stdout
        assert "分层隔离" in proc.stdout


class TestMockArtifactsDiskPersistence:
    """Test disk saving and loading of mock fixtures in tests/ and work/."""

    def test_saved_mock_files_exist_and_are_valid_json(self, tmp_path: Path):
        saved = save_mock_artifacts(tmp_path)
        assert len(saved) == 7

        for fname, fpath in saved.items():
            assert fpath.exists()
            assert fpath.stat().st_size > 0
            with open(fpath, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert isinstance(loaded, dict)
            assert "schema_version" in loaded


class TestTradingCalendarT5CalibrationAndDataGaps:
    """Test explicit Trading Calendar binding, T+5 calibration, in-flight, suspension, and data_gaps."""

    def test_trading_calendar_forward_sequence_and_t_plus_5(self):
        # A-share mock trading calendar crossing a weekend
        calendar = [
            "2026-08-03",  # Monday (T0)
            "2026-08-04",  # Tuesday (T+1)
            "2026-08-05",  # Wednesday (T+2)
            "2026-08-06",  # Thursday (T+3)
            "2026-08-07",  # Friday (T+4)
            "2026-08-10",  # Monday (T+5)
            "2026-08-11",  # Tuesday (T+6)
        ]
        fwd_5 = trading_days_forward("2026-08-03", 5, calendar_dates=calendar)
        assert fwd_5 == ["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10"]

        t5_date = calculate_t_plus_5_date("2026-08-03", calendar_dates=calendar)
        assert t5_date == "2026-08-10"

        # T0 on Friday 2026-08-07 -> T+5 should be Friday 2026-08-14
        calendar_ext = calendar + ["2026-08-12", "2026-08-13", "2026-08-14"]
        t5_from_friday = calculate_t_plus_5_date("2026-08-07", calendar_dates=calendar_ext)
        assert t5_from_friday == "2026-08-14"

    def test_t_plus_5_binding_with_price_series(self):
        calendar = [
            "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10"
        ]
        price_series = {
            "2026-08-03": 1650.0,
            "2026-08-10": 1732.5,  # +5%
        }
        raw_bull = create_mock_bull_win_report()
        matrix = build_evaluation_metric_matrix(
            raw_bull,
            trade_date="2026-08-03",
            trading_calendar=calendar,
            price_series=price_series,
        )
        q4 = matrix["quadrant_4_t_plus_5_and_shadow"]
        assert q4["t_plus_5_date"] == "2026-08-10"
        assert q4["t_plus_5_price"] == 1732.5
        assert q4["t_plus_5_return_pct"] == 5.0
        assert q4["t_plus_5_direction_hit"] is True
        assert q4["t_plus_5_status"] == "due_and_evaluated"

    def test_t_plus_5_in_flight_pending_due_keeps_hit_none(self):
        calendar = ["2026-08-03", "2026-08-04", "2026-08-05"]  # Only 2 forward days available
        raw_bull = create_mock_bull_win_report()
        matrix = build_evaluation_metric_matrix(
            raw_bull,
            trade_date="2026-08-03",
            trading_calendar=calendar,
            as_of_date="2026-08-05",
        )
        q4 = matrix["quadrant_4_t_plus_5_and_shadow"]
        assert q4["t_plus_5_status"] == "pending_due"
        assert q4["t_plus_5_price"] is None
        assert q4["t_plus_5_return_pct"] is None
        assert q4["t_plus_5_direction_hit"] is None  # Strictly None, never False

    def test_t_plus_5_suspension_recorded_and_excluded_from_denominator(self):
        raw_bull = create_mock_bull_win_report()
        raw_bull["data_gaps"] = [
            {
                "source": "trading_calendar",
                "gap_class": "operational",
                "status": "suspended",
                "reason": "标的 600519.SH 停牌",
                "gap": "data_gap: suspension",
            }
        ]
        matrix = build_evaluation_metric_matrix(
            raw_bull,
            trade_date="2026-08-03",
            is_suspended=True,
        )
        q4 = matrix["quadrant_4_t_plus_5_and_shadow"]
        assert q4["t_plus_5_status"] == "suspension"
        assert q4["t_plus_5_direction_hit"] is None
        assert q4["t_plus_5_return_pct"] is None

        q2 = matrix["quadrant_2_data_sources_and_gaps"]
        assert any("suspension" in str(g.get("gap", "")).lower() or g.get("status") == "suspended" for g in q2["data_gaps"])

        # Test weekly aggregation excludes suspension from due denominator
        normal_rep = create_mock_bull_win_report()
        normal_matrix = build_evaluation_metric_matrix(normal_rep, t_plus_5_price=1700.0)

        weekly = build_weekly_metrics(
            [matrix, normal_matrix],
            week_identifier="week_202634",
            start_date="2026-08-03",
            end_date="2026-08-10",
        )
        t5_calib = weekly["weekly_aggregate"]["t5_calibration"]
        assert t5_calib["due_sample_count"] == 1  # 2 samples total, but suspension excluded!
        assert t5_calib["completed_sample_count"] == 1
        assert t5_calib["completeness_rate"] == 1.0

    def test_t_plus_5_data_missing_handling(self):
        raw_bull = create_mock_bull_win_report()
        raw_bull["t_plus_5_evaluated"] = False
        matrix = build_evaluation_metric_matrix(
            raw_bull,
            trade_date="2026-08-03",
            t_plus_5_date="2026-08-10",
            as_of_date="2026-08-15",  # Past T+5, but no price passed
        )
        q4 = matrix["quadrant_4_t_plus_5_and_shadow"]
        assert q4["t_plus_5_status"] in ("data_missing", "pending_due")
        assert q4["t_plus_5_direction_hit"] is None

    def test_protocol_and_model_assignments_metadata_extraction(self):
        raw_bull = create_mock_bull_win_report()
        model_custom = {"bull": "deepseek-r1-0528", "bear": "qwen-max-0801", "manager": "claude-3-7-sonnet"}
        matrix = build_evaluation_metric_matrix(
            raw_bull,
            model_assignments=model_custom,
            latency_ms=1250.5,
            token_usage={"prompt": 4200, "completion": 1800, "total": 6000},
        )
        q1 = matrix["quadrant_1_protocol_metadata"]
        assert q1["protocol_version"] == PROTOCOL_VERSION_V2_STRUCTURED
        assert q1["model_assignments"]["bull"] == "deepseek-r1-0528"
        assert q1["model_assignments"]["bear"] == "qwen-max-0801"
        assert q1["model_assignments"]["manager"] == "claude-3-7-sonnet"
        assert q1["latency_ms"] == 1250.5
        assert q1["token_usage"]["total"] == 6000
