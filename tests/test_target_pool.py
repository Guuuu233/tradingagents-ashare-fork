"""Unit and integration tests for P3-H2.1 Target Pool Selection, Deduplication & Dynamic Re-balancing.

Verifies:
1. Shenwan Level 1 primary industry rotation (covering >= 5 core sectors);
2. Sample pool size 8~10 and single symbol concentration <= 15%;
3. Deterministic SHA256(symbol + trade_date + protocol_version) deduplication fingerprints;
4. Strict historical benchmark blacklist filtering (000333.SZ, 600900.SH, 600276.SH, 000725.SZ);
5. P0 Dynamic Pool Re-balancing: deviation detection and multi-empty stabilization in [40%, 60%];
6. Liquidity (ADV >= 100M) and Market Cap (>= 10B) admission rules;
7. Pydantic schema validation and CLI command execution.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
)
from tradingagents.agents.utils.target_pool import (
    DEFAULT_MAX_SINGLE_SYMBOL_SHARE,
    DEFAULT_MIN_ADV_MIL,
    DEFAULT_MIN_MARKET_CAP_BIL,
    DEFAULT_MIN_UNIQUE_INDUSTRIES,
    HISTORICAL_BENCHMARK_BLACKLIST,
    REBALANCE_MAX_RATIO,
    REBALANCE_MIN_RATIO,
    REBALANCE_PARITY_TARGET,
    REBALANCE_TOLERANCE_BAND,
    SHENWAN_PRIMARY_INDUSTRIES,
    TargetPoolResultModel,
    calculate_rebalance_state,
    generate_sample_fingerprint,
    generate_weekly_target_pool,
    normalize_symbol_code,
    normalize_trade_date_str,
    verify_sample_fingerprint,
)


class TestTargetPoolIndustryRotationAndDiversification:
    """Test Shenwan Level 1 industry coverage, rotation, and concentration constraints."""

    def test_shenwan_primary_industries_defined_comprehensively(self):
        """Ensure core Shenwan Level 1 industries are defined."""
        assert len(SHENWAN_PRIMARY_INDUSTRIES) >= 28
        for core in ["电子", "医药生物", "食品饮料", "电力设备", "机械设备", "有色金属", "石油石化", "银行", "公用事业", "通信"]:
            assert core in SHENWAN_PRIMARY_INDUSTRIES

    @pytest.mark.parametrize("pool_count", [8, 9, 10])
    def test_target_pool_generates_required_count_and_diversity(self, pool_count: int):
        """Generate weekly target pool and verify >= 5 industries and <= 15% max share."""
        result = generate_weekly_target_pool(
            trade_date="2026-08-26",
            count=pool_count,
            protocol_version=PROTOCOL_VERSION_V2_STRUCTURED,
        )

        assert isinstance(result, TargetPoolResultModel)
        assert result.count == pool_count
        assert len(result.items) == pool_count

        # Verify industry diversification
        dist = result.industry_distribution
        assert dist.total_samples == pool_count
        assert dist.unique_industries_count >= min(DEFAULT_MIN_UNIQUE_INDUSTRIES, pool_count)
        assert dist.unique_industries_count >= 5
        assert dist.diversification_passed is True

        # Verify max single symbol share <= 15%
        assert dist.max_single_symbol_share <= DEFAULT_MAX_SINGLE_SYMBOL_SHARE
        # For a batch of 8~10 with unique symbols, max share is 1/N <= 12.5% <= 15%
        symbols = [item.symbol for item in result.items]
        assert len(symbols) == len(set(symbols)), "Duplicate symbols found in target pool"

    def test_target_pool_admission_filters(self):
        """Verify all selected candidates satisfy market cap >= 10B and ADV >= 100M."""
        result = generate_weekly_target_pool(
            trade_date="2026-08-26",
            count=10,
        )
        for item in result.items:
            assert item.market_cap_bil >= DEFAULT_MIN_MARKET_CAP_BIL
            assert item.adv_mil >= DEFAULT_MIN_ADV_MIL
            assert item.symbol
            assert item.name
            assert item.industry in SHENWAN_PRIMARY_INDUSTRIES
            assert item.fingerprint
            assert len(item.fingerprint) == 64


class TestDeterministicDeduplicationAndBlacklist:
    """Test SHA256 deterministic fingerprints and historical blacklist filtering."""

    def test_sha256_fingerprint_determinism_and_correctness(self):
        """Verify fingerprint generation produces exact SHA256 output."""
        symbol = "600519.SH"
        trade_date = "2026-08-26"
        prot = PROTOCOL_VERSION_V2_STRUCTURED

        raw_str = f"600519.SH:2026-08-26:{PROTOCOL_VERSION_V2_STRUCTURED}"
        expected_hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

        fp = generate_sample_fingerprint(symbol, trade_date, prot)
        assert fp == expected_hash
        assert len(fp) == 64
        assert verify_sample_fingerprint(fp, symbol, trade_date, prot) is True
        assert verify_sample_fingerprint("wrong_hash", symbol, trade_date, prot) is False

    def test_date_and_symbol_normalization_in_fingerprints(self):
        """Verify various date and symbol formats normalize to identical fingerprint."""
        fp1 = generate_sample_fingerprint("600519.SH", "20260826", PROTOCOL_VERSION_V2_STRUCTURED)
        fp2 = generate_sample_fingerprint("600519", "2026-08-26", PROTOCOL_VERSION_V2_STRUCTURED)
        fp3 = generate_sample_fingerprint("  600519.sh  ", "2026/08/26", PROTOCOL_VERSION_V2_STRUCTURED)
        assert fp1 == fp2 == fp3

    def test_historical_benchmark_blacklist_strictly_excluded(self):
        """Verify historical baseline fixtures (000333, 600900, 600276, 000725) never leak into pool."""
        result = generate_weekly_target_pool(
            trade_date="2026-08-26",
            count=10,
        )

        selected_symbols = {it.symbol for it in result.items}
        for blacklisted in HISTORICAL_BENCHMARK_BLACKLIST:
            assert blacklisted not in selected_symbols, f"Blacklisted symbol {blacklisted} leaked into pool"
            assert blacklisted.split(".")[0] not in selected_symbols

    def test_custom_blacklist_and_historical_fingerprints_filtered(self):
        """Verify custom blacklists and duplicate fingerprints are excluded."""
        # Pre-calculate fingerprint for 600519.SH
        fp_maotai = generate_sample_fingerprint("600519.SH", "2026-08-26", PROTOCOL_VERSION_V2_STRUCTURED)

        result = generate_weekly_target_pool(
            trade_date="2026-08-26",
            count=10,
            blacklist_symbols={"300750.SZ"},  # Exclude CATL
            historical_fingerprints={fp_maotai},  # Exclude Maotai via duplicate fingerprint
        )

        selected_symbols = {it.symbol for it in result.items}
        assert "300750.SZ" not in selected_symbols
        assert "600519.SH" not in selected_symbols
        assert result.blacklist_filtered_count >= 1
        assert result.duplicate_fingerprints_dropped >= 1


class TestDynamicPoolRebalancing:
    """Test P0 Dynamic Pool Re-balancing logic and multi-empty stabilization."""

    def test_rebalance_parity_baseline_no_imbalance(self):
        """When history is 50-50 or empty, no imbalance is triggered."""
        state_empty = calculate_rebalance_state(historical_bull_samples=0, historical_bear_samples=0)
        assert state_empty.imbalance_detected is False
        assert state_empty.rebalance_direction == "balanced"
        assert state_empty.historical_bull_ratio == 0.50

        state_balanced = calculate_rebalance_state(historical_bull_samples=25, historical_bear_samples=25)
        assert state_balanced.imbalance_detected is False
        assert state_balanced.rebalance_direction == "balanced"
        assert state_balanced.historical_bull_ratio == 0.50
        assert state_balanced.deviation_from_parity == 0.0

    def test_rebalance_within_tolerance_band_no_imbalance(self):
        """When history is within 50% +- 5% (e.g. 52% or 48%), no imbalance is triggered."""
        # 52% bull ratio
        state_52 = calculate_rebalance_state(historical_bull_samples=52, historical_bear_samples=48)
        assert state_52.imbalance_detected is False
        assert state_52.rebalance_direction == "balanced"

        # 48% bull ratio
        state_48 = calculate_rebalance_state(historical_bull_samples=48, historical_bear_samples=52)
        assert state_48.imbalance_detected is False
        assert state_48.rebalance_direction == "balanced"

    def test_rebalance_bull_imbalance_triggers_bear_compensate(self):
        """When historical bull ratio > 55% (e.g. 70%), triggers bear_compensate."""
        state = calculate_rebalance_state(historical_bull_samples=35, historical_bear_samples=15)
        assert state.historical_bull_ratio == 0.70
        assert state.deviation_from_parity == 0.20
        assert state.imbalance_detected is True
        assert state.rebalance_direction == "bear_compensate"

        # Defensive and cyclical clusters must be upweighted, growth clusters downweighted
        adj = state.cluster_weight_adjustments
        assert adj["DEFENSIVE_UTILITIES"] > 1.0
        assert adj["CYCLICAL_COMMODITIES"] > 1.0
        assert adj["TMT_GROWTH"] < 1.0

        # Generate target pool with bull imbalance and verify defensive tilt
        result = generate_weekly_target_pool(
            trade_date="2026-08-26",
            count=10,
            historical_bull_samples=35,
            historical_bear_samples=15,
        )
        assert result.rebalance_audit.imbalance_detected is True
        assert result.rebalance_audit.rebalance_direction == "bear_compensate"
        # Check presence of defensive / bear-tilt / divergence sectors
        industries = {it.industry for it in result.items}
        defensive_sectors = {"公用事业", "交通运输", "煤炭", "石油石化", "钢铁", "银行", "非银金融", "房地产", "基础化工"}
        assert len(industries.intersection(defensive_sectors)) >= 4

    def test_rebalance_bear_imbalance_triggers_bull_compensate(self):
        """When historical bull ratio < 45% (e.g. 30%), triggers bull_compensate."""
        state = calculate_rebalance_state(historical_bull_samples=15, historical_bear_samples=35)
        assert state.historical_bull_ratio == 0.30
        assert state.deviation_from_parity == -0.20
        assert state.imbalance_detected is True
        assert state.rebalance_direction == "bull_compensate"

        # TMT & New energy clusters must be upweighted, defensive clusters downweighted
        adj = state.cluster_weight_adjustments
        assert adj["TMT_GROWTH"] > 1.0
        assert adj["NEW_ENERGY_AUTO"] > 1.0
        assert adj["DEFENSIVE_UTILITIES"] < 1.0

        # Generate target pool with bear imbalance and verify growth tilt
        result = generate_weekly_target_pool(
            trade_date="2026-08-26",
            count=10,
            historical_bull_samples=15,
            historical_bear_samples=35,
        )
        assert result.rebalance_audit.imbalance_detected is True
        assert result.rebalance_audit.rebalance_direction == "bull_compensate"
        industries = {it.industry for it in result.items}
        growth_sectors = {"电子", "计算机", "通信", "电力设备", "汽车", "机械设备", "家用电器", "食品饮料"}
        assert len(industries.intersection(growth_sectors)) >= 5


class TestTargetPoolPydanticModelAndJsonExport:
    """Test Pydantic model validation and JSON serialization roundtrip."""

    def test_target_pool_pydantic_model_roundtrip(self):
        result = generate_weekly_target_pool(
            trade_date="2026-08-26",
            count=10,
        )

        dumped_json = result.model_dump_json()
        assert isinstance(dumped_json, str)

        reloaded = TargetPoolResultModel.model_validate_json(dumped_json)
        assert reloaded.trade_date == result.trade_date
        assert reloaded.count == result.count
        assert len(reloaded.items) == 10
        assert reloaded.industry_distribution.unique_industries_count == result.industry_distribution.unique_industries_count


class TestTargetPoolCLIIntegration:
    """Test CLI command execution via subprocess."""

    def test_cli_execution_table_output(self):
        cmd = [
            sys.executable,
            "scripts/generate_weekly_target_pool.py",
            "--trade-date",
            "20260826",
            "--count",
            "10",
            "--dry-run",
            "--output",
            "table",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0
        assert "每周自动化标的池筛选与防污染排重报告" in res.stdout
        assert "申万一级行业" in res.stdout
        assert "排重指纹" in res.stdout

    def test_cli_execution_json_output(self):
        cmd = [
            sys.executable,
            "scripts/generate_weekly_target_pool.py",
            "--trade-date",
            "20260826",
            "--count",
            "8",
            "--output",
            "json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["schema_version"] == "target_pool_v1"
        assert data["count"] == 8
        assert len(data["items"]) == 8

    def test_cli_execution_with_historical_metrics_file(self):
        metrics_file = Path("work/evaluations/week_202634_metrics.json")
        if metrics_file.exists():
            cmd = [
                sys.executable,
                "scripts/generate_weekly_target_pool.py",
                "--trade-date",
                "20260826",
                "--count",
                "10",
                "--historical-metrics",
                str(metrics_file),
                "--output",
                "json",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            assert res.returncode == 0
            data = json.loads(res.stdout)
            assert data["rebalance_audit"]["historical_bull_samples"] == 26
            assert data["rebalance_audit"]["historical_bear_samples"] == 26
