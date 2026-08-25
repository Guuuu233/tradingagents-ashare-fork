"""Unit tests and CLI integration tests for P3-H2.3 recalculate_weekly_metrics.py.

Verifies:
1. Date parsing and week identifier normalization (YYYYWW, week_YYYYWW, ISO calendar).
2. Report and Mock dataset loading from files, directories, and synthetic fixtures.
3. Multi-dimensional drill-down calculations (by industry, by model, by market regime).
4. Full week recalculation against Pydantic schema contracts.
5. Idempotent artifact persistence and atomic symlink maintenance (latest_metrics.json).
6. CLI dry-run and formatting options (text, json, markdown).
7. End-to-end subprocess execution with 0 external network / API dependencies.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

project_root = Path(__file__).resolve().parent.parent

from tradingagents.agents.utils.evaluation_schemas import (
    EVALUATION_MATRIX_SCHEMA_VERSION,
    WEEKLY_METRICS_SCHEMA_VERSION,
    WEEKLY_SUMMARY_MD_SCHEMA_VERSION,
    WeeklyMetricsJSONModel,
    WeeklySummaryMDModel,
    calculate_drilldown_by_industry,
    calculate_drilldown_by_model,
    calculate_drilldown_by_regime,
    validate_weekly_metrics,
    validate_weekly_summary_md,
)
from tests.mock_evaluations.mock_scenarios import (
    create_mock_balanced_tie_report,
    create_mock_bear_win_report,
    create_mock_bull_win_report,
    generate_60_sample_weekly_dataset,
)
from scripts.recalculate_weekly_metrics import (
    compute_week_date_range,
    extract_sample_trade_date,
    extract_week_from_date,
    group_samples_by_week,
    load_reports,
    normalize_week_identifier,
    recalculate_week,
    run_recalculate,
    save_weekly_artifacts,
)


class TestWeekIdentifierAndDateParsing:
    """Test week identifier parsing, normalization, and trade date extraction."""

    def test_normalize_week_identifier(self):
        assert normalize_week_identifier("202634") == "week_202634"
        assert normalize_week_identifier("week_202634") == "week_202634"
        assert normalize_week_identifier("2026-W34") == "week_202634"
        assert normalize_week_identifier("2026_34") == "week_202634"
        assert normalize_week_identifier("2026W34") == "week_202634"
        assert normalize_week_identifier(None) is None
        assert normalize_week_identifier("") is None

    def test_extract_week_from_date(self):
        assert extract_week_from_date("2026-08-20") == "week_202634"
        assert extract_week_from_date("2026-07-06") == "week_202628"
        assert extract_week_from_date("2026-08-20T18:30:00Z") == "week_202634"

    def test_extract_sample_trade_date(self):
        sample_q1 = {
            "quadrant_1_protocol_metadata": {
                "trade_date": "2026-08-18",
                "symbol": "600519.SH",
            }
        }
        assert extract_sample_trade_date(sample_q1) == "2026-08-18"

        sample_flat = {"trade_date": "2026-08-19", "symbol": "000002.SZ"}
        assert extract_sample_trade_date(sample_flat) == "2026-08-19"


class TestReportLoadingAndGrouping:
    """Test loading reports from JSON files, directories, and grouping logic."""

    def test_load_reports_from_mock_weekly_dataset_file(self, tmp_path: Path):
        mock_data = generate_60_sample_weekly_dataset(
            start_date="2026-07-06",
            end_date="2026-08-20",
            week_identifier="week_202634",
        )
        json_file = tmp_path / "mock_weekly_test.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(mock_data, f, ensure_ascii=False)

        loaded = load_reports(input_file=str(json_file))
        assert len(loaded) == 60
        assert loaded[0]["_source_week_identifier"] == "week_202634"

    def test_load_reports_from_directory(self, tmp_path: Path):
        rep1 = create_mock_bull_win_report()
        rep2 = create_mock_bear_win_report()
        with open(tmp_path / "rep1.json", "w", encoding="utf-8") as f:
            json.dump(rep1, f)
        with open(tmp_path / "rep2.json", "w", encoding="utf-8") as f:
            json.dump(rep2, f)

        loaded = load_reports(input_dir=str(tmp_path))
        assert len(loaded) == 2

    def test_group_samples_by_week(self):
        s1 = {"_source_week_identifier": "week_202634", "symbol": "600519.SH", "trade_date": "2026-08-20"}
        s2 = {"_source_week_identifier": "week_202634", "symbol": "000002.SZ", "trade_date": "2026-08-20"}
        s3 = {"symbol": "600900.SH", "trade_date": "2026-07-06"}  # ISO week 28

        groups = group_samples_by_week([s1, s2, s3])
        assert "week_202634" in groups
        assert len(groups["week_202634"]) == 2
        assert "week_202628" in groups
        assert len(groups["week_202628"]) == 1


class TestDrilldownCalculations:
    """Test multi-dimensional drilldown analytics by industry, model, and market regime."""

    def test_drilldown_by_industry(self):
        dataset = generate_60_sample_weekly_dataset()
        samples = dataset["samples"]
        drill_ind = calculate_drilldown_by_industry(samples)

        assert isinstance(drill_ind, dict)
        assert len(drill_ind) >= 5
        assert "白酒" in drill_ind
        bj = drill_ind["白酒"]
        assert bj["sample_count"] > 0
        assert "bull_count" in bj
        assert "bear_count" in bj
        assert "hold_count" in bj
        assert "delta_verified_rate" in bj
        assert "avg_clone_rate" in bj
        assert "t5_accuracy_rate" in bj

    def test_drilldown_by_model(self):
        dataset = generate_60_sample_weekly_dataset()
        samples = dataset["samples"]
        drill_model = calculate_drilldown_by_model(samples)

        assert isinstance(drill_model, dict)
        assert "deepseek-r1" in drill_model
        assert "qwen-max" in drill_model

        ds = drill_model["deepseek-r1"]
        assert ds["total_debates"] == 60
        assert ds["bull_wins"] == 26
        assert ds["win_rate"] == pytest.approx(0.4333, 0.01)
        assert ds["avg_verified_rate"] is not None
        assert ds["avg_challenge_adoption_rate"] is not None

    def test_drilldown_by_regime(self):
        dataset = generate_60_sample_weekly_dataset()
        samples = dataset["samples"]
        drill_regime = calculate_drilldown_by_regime(samples)

        assert isinstance(drill_regime, dict)
        assert "bull_trend" in drill_regime
        assert "bear_trend" in drill_regime
        assert "consolidation" in drill_regime
        assert drill_regime["bull_trend"]["sample_count"] == 26
        assert drill_regime["bear_trend"]["sample_count"] == 26
        assert drill_regime["consolidation"]["sample_count"] == 8


class TestWeeklyRecalculationAndArtifactPersistence:
    """Test full recalculation, Pydantic validation, and atomic idempotent disk persistence."""

    def test_recalculate_week_generates_valid_schema(self):
        dataset = generate_60_sample_weekly_dataset()
        weekly_json, summary_md = recalculate_week(
            dataset["samples"],
            "week_202634",
        )

        model = validate_weekly_metrics(weekly_json)
        assert isinstance(model, WeeklyMetricsJSONModel)
        assert model.schema_version == WEEKLY_METRICS_SCHEMA_VERSION
        assert model.sample_count == 60
        assert model.weekly_aggregate.overview.total_samples == 60
        assert model.weekly_aggregate.h1b_system_gates_evaluation.passed is True

        md_model = validate_weekly_summary_md(
            {
                "schema_version": WEEKLY_SUMMARY_MD_SCHEMA_VERSION,
                "week_identifier": "week_202634",
                "title": "看板报告",
                "markdown_content": summary_md,
            }
        )
        assert isinstance(md_model, WeeklySummaryMDModel)
        assert "## 一、 评测大盘 KPI 核心概览" in summary_md
        assert "## 二、 四大象限细分指标透视" in summary_md
        assert "## 三、 H1b 信用加权 7 维激活门槛离线复算看板" in summary_md
        assert "### 5. 多维度下钻分析 (按行业 / 模型 / 市场状态)" in summary_md

    def test_save_weekly_artifacts_and_symlink_idempotency(self, tmp_path: Path):
        dataset = generate_60_sample_weekly_dataset()
        weekly_json, summary_md = recalculate_week(dataset["samples"], "week_202634")

        # 1. First save
        artifacts = save_weekly_artifacts(
            weekly_json, summary_md, tmp_path, is_latest=True
        )

        json_path = Path(artifacts["metrics_json"])
        md_path = Path(artifacts["summary_md"])
        symlink_json = Path(artifacts["latest_metrics_json"])
        symlink_md = Path(artifacts["latest_summary_md"])

        assert json_path.exists()
        assert md_path.exists()
        assert symlink_json.exists()
        assert symlink_md.exists()

        # Check symlink destination
        if symlink_json.is_symlink():
            assert symlink_json.readlink().name == "week_202634_metrics.json"

        # 2. Re-save (idempotency check)
        artifacts2 = save_weekly_artifacts(
            weekly_json, summary_md, tmp_path, is_latest=True
        )
        assert Path(artifacts2["metrics_json"]).exists()
        assert Path(artifacts2["summary_md"]).exists()

        # Validate saved JSON from disk
        with open(json_path, "r", encoding="utf-8") as f:
            disk_loaded = json.load(f)
        validated_disk_model = validate_weekly_metrics(disk_loaded)
        assert validated_disk_model.sample_count == 60


class TestCliIntegrationAndSubprocess:
    """Test CLI commands, dry-run, output directory, format switches, and subprocess execution."""

    def test_cli_dry_run_does_not_write_files(self, tmp_path: Path):
        res = run_recalculate(
            week="202634",
            use_mock=True,
            dry_run=True,
            output_dir=str(tmp_path),
            output_format="text",
        )
        assert res["success"] is True
        assert res["dry_run"] is True
        assert len(res["weeks_processed"]) == 1
        # tmp_path should be empty
        assert len(list(tmp_path.glob("*.json"))) == 0
        assert len(list(tmp_path.glob("*.md"))) == 0

    def test_cli_write_to_custom_output_dir(self, tmp_path: Path):
        res = run_recalculate(
            week="202634",
            use_mock=True,
            dry_run=False,
            output_dir=str(tmp_path),
            output_format="text",
        )
        assert res["success"] is True
        assert (tmp_path / "week_202634_metrics.json").exists()
        assert (tmp_path / "week_202634_summary.md").exists()
        assert (tmp_path / "latest_metrics.json").exists()

    def test_cli_subprocess_run(self, tmp_path: Path):
        script_path = Path(project_root) / "scripts" / "recalculate_weekly_metrics.py"
        cmd = [
            sys.executable,
            str(script_path),
            "--week",
            "202634",
            "--use-mock",
            "--output-dir",
            str(tmp_path),
            "--format",
            "text",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0
        assert "周度离线复算看板与指标聚合报告" in proc.stdout
        assert (tmp_path / "week_202634_metrics.json").exists()
        assert (tmp_path / "week_202634_summary.md").exists()
        assert (tmp_path / "latest_metrics.json").exists()

    def test_cli_subprocess_format_json(self, tmp_path: Path):
        script_path = Path(project_root) / "scripts" / "recalculate_weekly_metrics.py"
        cmd = [
            sys.executable,
            str(script_path),
            "--week",
            "202634",
            "--use-mock",
            "--dry-run",
            "--format",
            "json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0
        # Parse JSON output from stdout (filter out any log lines)
        stdout_lines = proc.stdout.strip().split("\n")
        json_lines = [l for l in stdout_lines if not l.startswith("2026-")]
        json_text = "\n".join(json_lines)
        parsed = json.loads(json_text)
        assert parsed["schema_version"] == WEEKLY_METRICS_SCHEMA_VERSION
        assert parsed["week_identifier"] == "week_202634"
        assert parsed["sample_count"] == 60
