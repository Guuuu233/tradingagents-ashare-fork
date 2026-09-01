"""Unit tests for Track A5: v2 winner -> T+5 shadow credit metrics offline backfill.

Test suite covers:
1. Due hit determination from manager_verdict.winner (bull, bear, tie with +-3% band).
2. Strict T+5 trading calendar window (hold window strictly 5 trading days, not arrived -> pending_due, hit=None).
3. Suspension handling (t_plus_5_status='suspension', hit=None, is_suspended=True, excluded from due denominator).
4. Market data missing handling (t_plus_5_status='data_missing', hit=None, included in due denominator).
5. Non-qualifying v2 report skipping (status != 'completed', legacy v1, missing v2 winner).
6. Result_data preservation and backfill idempotency.
7. Batch backfill pipeline and --dry-run CLI safety.
8. System gate Dimension 4 integration (due_count > 0, genuine completeness calculation).
9. Feature flag immutability (credit_weighting_enabled remains False).
"""

import copy
import json
import os
import pytest
from pathlib import Path

from tradingagents.agents.utils.agent_states import (
    DEFAULT_FEATURE_FLAGS,
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
)
from tradingagents.agents.utils.shadow_credit import (
    backfill_tplus5_shadow_for_report,
    backfill_tplus5_shadow_for_reports,
    calculate_shadow_credit_metrics,
    evaluate_h1b_system_gates,
    filter_v2_completed_reports,
    is_qualifying_v2_report,
)
from scripts.backfill_tplus5_shadow import run_backfill


def _build_v2_report_fixture(
    *,
    symbol: str = "600519.SH",
    trade_date: str = "2026-08-03",
    winner: str = "bull",
    entry_price: float = 1600.0,
    status: str = "completed",
    is_suspended: bool = False,
    existing_t5_price: float | None = None,
) -> dict:
    """Helper to build a qualifying completed v2 report fixture."""
    return {
        "id": f"rep-{symbol}-{trade_date}",
        "symbol": symbol,
        "trade_date": trade_date,
        "status": status,
        "industry": "白酒",
        "result_data": {
            "symbol": symbol,
            "trade_date": trade_date,
            "industry": "白酒",
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "market_report": "市场分析正文内容",
            "sentiment_report": "情绪分析正文内容",
            "news_report": "新闻分析正文内容",
            "fundamentals_report": "基本面分析正文内容",
            "smart_money_report": "主力资金分析正文内容",
            "volume_price_report": "量价分析正文内容",
            "macro_report": "宏观分析正文内容",
            "analyst_traces": [{"agent": "bull", "verdict": "看多"}],
            "data_gaps": [],
            "investment_debate_state": {
                "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
                "claims": [
                    {
                        "claim_id": "INV-1",
                        "speaker_key": "Bull",
                        "stance": "bullish",
                        "claim": "主力资金持续净流入",
                        "status": "verified",
                        "is_verified": True,
                    },
                    {
                        "claim_id": "INV-2",
                        "speaker_key": "Bear",
                        "stance": "bearish",
                        "claim": "估值处于历史高位",
                        "status": "verified",
                        "is_verified": True,
                    },
                ],
                "claim_evidence_summary": {
                    "INV-1": {"speaker_key": "Bull", "counts": {"verified": 1, "total": 1}, "decision": "adopt"},
                    "INV-2": {"speaker_key": "Bear", "counts": {"verified": 1, "total": 1}, "decision": "adopt"},
                },
                "manager_verdict": {
                    "winner": winner,
                    "direction": "看多" if winner == "bull" else ("看空" if winner == "bear" else "中性"),
                    "entry": f"{entry_price:.2f}元",
                    "consistency_check_passed": True,
                    "failed_checks": [],
                },
                "round_messages": [
                    {"speaker_key": "Bull", "stance": "bullish", "model_name": "deepseek-r1"},
                    {"speaker_key": "Bear", "stance": "bearish", "model_name": "qwen-max"},
                    {"speaker_key": "Research Manager", "is_verdict": True, "model_name": "gpt-4o"},
                ],
                "feature_flags": {
                    "v2_debate_enabled": True,
                    "shadow_credit_enabled": True,
                    "credit_weighting_enabled": False,
                },
            },
            "manager_verdict": {
                "winner": winner,
                "direction": "看多" if winner == "bull" else ("看空" if winner == "bear" else "中性"),
                "entry": f"{entry_price:.2f}元",
                "consistency_check_passed": True,
                "failed_checks": [],
            },
            "is_suspended": is_suspended,
            "t_plus_5_price": existing_t5_price,
        },
    }


class TestTPlus5ShadowHitDetermination:
    """Test suite for T+5 direction hit determination based on manager_verdict.winner."""

    def test_bull_winner_hit_when_price_increases(self):
        """Bull winner + price up -> hit=True."""
        report = _build_v2_report_fixture(winner="bull", entry_price=10.0, trade_date="2026-08-03")
        # T+5 date for 2026-08-03 is 2026-08-10
        prices = {"2026-08-03": 10.0, "2026-08-10": 11.5}
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15", price_series=prices)

        assert res["_backfill_status"] == "hit"
        assert res["t_plus_5_status"] == "due_and_evaluated"
        assert res["t_plus_5_direction_hit"] is True
        assert res["t_plus_5_date"] == "2026-08-10"
        assert res["t_plus_5_price"] == 11.5
        assert res["result_data"]["shadow_credit_metrics"]["t_plus_5_direction_hit"] is True

    def test_bull_winner_miss_when_price_decreases(self):
        """Bull winner + price down -> hit=False."""
        report = _build_v2_report_fixture(winner="bull", entry_price=10.0, trade_date="2026-08-03")
        prices = {"2026-08-03": 10.0, "2026-08-10": 9.2}
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15", price_series=prices)

        assert res["_backfill_status"] == "miss"
        assert res["t_plus_5_status"] == "due_and_evaluated"
        assert res["t_plus_5_direction_hit"] is False
        assert res["t_plus_5_price"] == 9.2
        assert res["result_data"]["shadow_credit_metrics"]["t_plus_5_direction_hit"] is False

    def test_bear_winner_hit_when_price_decreases(self):
        """Bear winner + price down -> hit=True."""
        report = _build_v2_report_fixture(winner="bear", entry_price=20.0, trade_date="2026-08-03")
        prices = {"2026-08-03": 20.0, "2026-08-10": 18.0}
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15", price_series=prices)

        assert res["_backfill_status"] == "hit"
        assert res["t_plus_5_status"] == "due_and_evaluated"
        assert res["t_plus_5_direction_hit"] is True
        assert res["t_plus_5_price"] == 18.0

    def test_bear_winner_miss_when_price_increases(self):
        """Bear winner + price up -> hit=False."""
        report = _build_v2_report_fixture(winner="bear", entry_price=20.0, trade_date="2026-08-03")
        prices = {"2026-08-03": 20.0, "2026-08-10": 22.0}
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15", price_series=prices)

        assert res["_backfill_status"] == "miss"
        assert res["t_plus_5_status"] == "due_and_evaluated"
        assert res["t_plus_5_direction_hit"] is False
        assert res["t_plus_5_price"] == 22.0

    def test_tie_winner_hit_within_three_percent_band(self):
        """Tie winner + price change <= 3% -> hit=True."""
        report = _build_v2_report_fixture(winner="tie", entry_price=100.0, trade_date="2026-08-03")
        prices = {"2026-08-03": 100.0, "2026-08-10": 102.5}  # +2.5%
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15", price_series=prices)

        assert res["_backfill_status"] == "hit"
        assert res["t_plus_5_direction_hit"] is True
        assert res["t_plus_5_price"] == 102.5

    def test_tie_winner_miss_outside_three_percent_band(self):
        """Tie winner + price change > 3% -> hit=False."""
        report = _build_v2_report_fixture(winner="tie", entry_price=100.0, trade_date="2026-08-03")
        prices = {"2026-08-03": 100.0, "2026-08-10": 105.0}  # +5.0%
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15", price_series=prices)

        assert res["_backfill_status"] == "miss"
        assert res["t_plus_5_direction_hit"] is False
        assert res["t_plus_5_price"] == 105.0


class TestTPlus5TradingCalendarAndWindow:
    """Test suite for strict trading calendar forward and pending-due status."""

    def test_future_unreached_t_plus_5_is_pending_due(self):
        """T+5 date > as_of -> pending_due, hit=None, is_due=False."""
        # 2026-08-20 -> T+5 trading date is 2026-08-27
        report = _build_v2_report_fixture(winner="bull", trade_date="2026-08-20")
        prices = {"2026-08-20": 10.0, "2026-08-27": 12.0}
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-22", price_series=prices)

        assert res["_backfill_status"] == "pending_due"
        assert res["t_plus_5_status"] == "pending_due"
        assert res["is_t_plus_5_due"] is False
        assert res["t_plus_5_evaluated"] is False
        assert res["t_plus_5_direction_hit"] is None
        assert res["t_plus_5_price"] is None
        assert res["t_plus_5_date"] == "2026-08-27"
        assert res["result_data"]["shadow_credit_metrics"]["t_plus_5_direction_hit"] is None

    def test_strict_five_trading_days_no_shortening(self):
        """Hold window must be strictly 5 trading days, not T+1 or T+2."""
        report = _build_v2_report_fixture(winner="bull", trade_date="2026-08-03")
        # Monday 08-03 -> Tue 08-04(1), Wed 08-05(2), Thu 08-06(3), Fri 08-07(4), Mon 08-10(5)
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-07", price_series={})
        # As of Friday 08-07, T+5 (08-10) is NOT reached
        assert res["t_plus_5_status"] == "pending_due"
        assert res["t_plus_5_date"] == "2026-08-10"
        assert res["is_t_plus_5_due"] is False


class TestTPlus5SuspensionAndMissingData:
    """Test suite for suspension and missing price handling."""

    def test_suspended_stock_marked_suspension_and_excluded_from_due(self):
        """Suspended stock on T+5 -> suspension, hit=None, is_suspended=True."""
        report = _build_v2_report_fixture(winner="bull", trade_date="2026-08-03", is_suspended=True)
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15")

        assert res["_backfill_status"] == "suspension"
        assert res["t_plus_5_status"] == "suspension"
        assert res["is_suspended"] is True
        assert res["is_t_plus_5_due"] is False
        assert res["t_plus_5_direction_hit"] is None

    def test_missing_price_marked_data_missing_and_included_in_due(self):
        """Due report with missing price -> data_missing, hit=None, is_due=True."""
        report = _build_v2_report_fixture(winner="bull", trade_date="2026-08-03")
        # Empty price series -> price missing
        res = backfill_tplus5_shadow_for_report(
            report,
            as_of="2026-08-15",
            price_series={},
            get_price_fn=lambda s, t0, t5: None,
        )

        assert res["_backfill_status"] == "data_missing"
        assert res["t_plus_5_status"] == "data_missing"
        assert res["is_t_plus_5_due"] is True
        assert res["t_plus_5_evaluated"] is True
        assert res["t_plus_5_direction_hit"] is None
        assert res["t_plus_5_price"] is None


class TestQualifyingV2FilterAndFieldPreservation:
    """Test suite for report qualification, idempotency, and field preservation."""

    def test_non_qualifying_v1_report_skipped(self):
        """Legacy v1 report without v2 structured disagreement is skipped."""
        v1_rep = {
            "id": "rep-v1-old",
            "symbol": "600000.SH",
            "trade_date": "2026-08-03",
            "status": "completed",
            "protocol_version": PROTOCOL_VERSION_V1_LEGACY,
            "decision": "BUY",
        }
        res = backfill_tplus5_shadow_for_report(v1_rep, as_of="2026-08-15")
        assert res.get("_backfill_status") == "skipped_non_qualifying"

    def test_failed_status_report_skipped(self):
        """Failed status report is not completed -> skipped."""
        failed_rep = _build_v2_report_fixture(status="failed")
        res = backfill_tplus5_shadow_for_report(failed_rep, as_of="2026-08-15")
        assert res.get("_backfill_status") == "skipped_non_qualifying"

    def test_result_data_fields_fully_preserved(self):
        """Backfilling preserves all existing fields in result_data without data loss."""
        report = _build_v2_report_fixture(winner="bull", trade_date="2026-08-03")
        report["result_data"]["custom_key_1"] = "important_data"
        report["result_data"]["analyst_traces"] = [{"trace": 123}]

        prices = {"2026-08-03": 1600.0, "2026-08-10": 1700.0}
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15", price_series=prices)

        assert res["result_data"]["custom_key_1"] == "important_data"
        assert res["result_data"]["analyst_traces"] == [{"trace": 123}]
        assert res["result_data"]["market_report"] == "市场分析正文内容"

    def test_idempotent_backfill(self):
        """Backfilling twice on the same report produces identical results."""
        report = _build_v2_report_fixture(winner="bull", trade_date="2026-08-03")
        prices = {"2026-08-03": 1600.0, "2026-08-10": 1700.0}

        res1 = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15", price_series=prices)
        res2 = backfill_tplus5_shadow_for_report(res1, as_of="2026-08-15", price_series=prices)

        assert res1["t_plus_5_direction_hit"] == res2["t_plus_5_direction_hit"]
        assert res1["t_plus_5_price"] == res2["t_plus_5_price"]
        assert res1["t_plus_5_status"] == res2["t_plus_5_status"]


class TestBatchBackfillAndSystemGatesIntegration:
    """Test suite for batch backfill pipeline and H1b gate evaluation integration."""

    def test_batch_backfill_summary_statistics(self):
        """Batch backfill returns accurate summary stats across mixed sample states."""
        reports = [
            # Due hit
            _build_v2_report_fixture(symbol="600001.SH", trade_date="2026-08-03", winner="bull"),
            # Due miss
            _build_v2_report_fixture(symbol="600002.SH", trade_date="2026-08-03", winner="bear"),
            # Suspended
            _build_v2_report_fixture(symbol="600003.SH", trade_date="2026-08-03", is_suspended=True),
            # Pending due
            _build_v2_report_fixture(symbol="600004.SH", trade_date="2026-08-20", winner="bull"),
            # Non-qualifying
            {"status": "failed", "symbol": "600005.SH"},
        ]

        price_map = {
            "600001.SH": {"2026-08-03": 10.0, "2026-08-10": 12.0},  # bull hit
            "600002.SH": {"2026-08-03": 10.0, "2026-08-10": 12.0},  # bear miss
        }

        updated, stats = backfill_tplus5_shadow_for_reports(
            reports,
            as_of="2026-08-15",
            price_series_map=price_map,
        )

        assert stats["total_scanned"] == 5
        assert stats["qualifying_v2_count"] == 4
        assert stats["skipped_non_qualifying"] == 1
        assert stats["due_count"] == 2
        assert stats["evaluated_count"] == 2
        assert stats["hit_count"] == 1
        assert stats["miss_count"] == 1
        assert stats["suspension_count"] == 1
        assert stats["pending_due_count"] == 1
        assert stats["completeness_rate"] == 1.0
        assert stats["hit_rate"] == 0.5

    def test_h1b_gate_dimension_4_with_backfilled_samples(self):
        """Dimension 4 evaluates due_count > 0 and genuine completeness rate."""
        samples = []
        for i in range(60):
            sym = f"6000{i:02d}.SH"
            winner = "bull" if i % 2 == 0 else "bear"
            rep = _build_v2_report_fixture(symbol=sym, trade_date="2026-08-03", winner=winner)
            samples.append(rep)

        # 58 samples have prices, 2 missing
        price_map = {}
        for i in range(58):
            sym = f"6000{i:02d}.SH"
            price_map[sym] = {"2026-08-03": 10.0, "2026-08-10": 11.0 if (i % 2 == 0) else 9.0}

        backfilled, stats = backfill_tplus5_shadow_for_reports(
            samples,
            as_of="2026-08-15",
            get_price_fn=lambda sym, t0, t5: price_map.get(sym, {}).get(t5),
        )

        gate_res = evaluate_h1b_system_gates(backfilled)
        dim_t5 = gate_res["matrix"]["dimension_t5"]

        assert dim_t5["details"]["due_count"] == 60
        assert dim_t5["details"]["completed_count"] == 58
        assert dim_t5["details"]["completeness_rate"] == round(58 / 60, 4)
        assert dim_t5["passed"] is True  # 58/60 = 96.67% >= 95%

    def test_credit_weighting_flag_remains_strictly_false(self):
        """Feature flag credit_weighting_enabled must remain False throughout backfill."""
        report = _build_v2_report_fixture(winner="bull", trade_date="2026-08-03")
        prices = {"2026-08-03": 10.0, "2026-08-10": 11.0}
        res = backfill_tplus5_shadow_for_report(report, as_of="2026-08-15", price_series=prices)

        assert res["result_data"]["shadow_credit_metrics"]["credit_weighting_enabled"] is False
        assert DEFAULT_FEATURE_FLAGS["credit_weighting_enabled"] is False


class TestBackfillCLIAndPersistence:
    """Test suite for backfill CLI execution and disk / dry-run safety."""

    def test_run_backfill_dry_run_does_not_mutate_file(self, tmp_path):
        """--dry-run mode leaves input file unchanged and does not write output."""
        sample_file = tmp_path / "samples.json"
        rep = _build_v2_report_fixture(winner="bull", trade_date="2026-08-03")
        with open(sample_file, "w", encoding="utf-8") as f:
            json.dump([rep], f)

        out_file = tmp_path / "out.json"

        result = run_backfill(
            input_file=str(sample_file),
            output_file=str(out_file),
            as_of="2026-08-15",
            dry_run=True,
            verify_gates=True,
        )

        assert result["dry_run"] is True
        assert result["qualifying_count"] == 1
        assert not out_file.exists()

    def test_run_backfill_active_writes_output_file(self, tmp_path):
        """Active backfill writes output JSON with updated shadow metrics and fields."""
        sample_file = tmp_path / "samples.json"
        rep = _build_v2_report_fixture(winner="bull", trade_date="2026-08-03", existing_t5_price=1700.0)
        with open(sample_file, "w", encoding="utf-8") as f:
            json.dump({"samples": [rep]}, f)

        out_file = tmp_path / "out.json"

        result = run_backfill(
            input_file=str(sample_file),
            output_file=str(out_file),
            as_of="2026-08-15",
            dry_run=False,
            verify_gates=False,
        )

        assert result["dry_run"] is False
        assert out_file.exists()

        with open(out_file, "r", encoding="utf-8") as f:
            saved = json.load(f)

        assert "samples" in saved
        assert len(saved["samples"]) == 1
        assert saved["samples"][0]["t_plus_5_direction_hit"] is True
        assert saved["samples"][0]["t_plus_5_status"] == "due_and_evaluated"

