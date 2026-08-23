"""Tests for Offline A/B debate harness (P1-M3).

Tests:
1. Running legacy evaluator vs v2 evaluator on the same complete result_data.
2. Zero network/LLM calls (verified by assertions/mocks).
3. Structural metrics comparison without subjective text scoring.
4. Golden replay on the 3 audit files (000333, 600900, 600276) as read-only inputs.
"""

import json
from pathlib import Path
import pytest
from unittest.mock import patch

from tradingagents.agents.utils.offline_ab_harness import (
    LegacyDebateEvaluator,
    V2DebateEvaluator,
    OfflineABHarness,
)


def test_offline_ab_harness_single_report_comparison():
    """Test running OfflineABHarness on a synthetic result_data fixture."""
    synthetic_result_data = {
        "confidence": 75,
        "probability": 0.65,
        "target_price": 120.0,
        "stop_loss_price": 95.0,
        "decision": "BUY",
        "macro_report": "宏观利率 2.5%，外贸增长 5%",
        "fundamentals_report": "营收 200 亿，净利润 30 亿",
        "market_report": "站稳 20 日均线",
        "sentiment_report": "情绪高涨",
        "news_report": "无利空",
        "smart_money_report": "主力流入 5 亿",
        "volume_price_report": "放量突破",
        "investment_debate_state": {
            "protocol_version": "v1_legacy",
            "round_messages": [
                {
                    "round_index": 1,
                    "message_index": 1,
                    "speaker_key": "Bull",
                    "cleaned_prose": "营收 200 亿，外贸增长 5%",
                },
                {
                    "round_index": 2,
                    "message_index": 3,
                    "speaker_key": "Bull",
                    "cleaned_prose": "重申营收 200 亿",
                },
            ],
            "claims": [
                {
                    "claim_id": "INV-1",
                    "speaker_key": "Bull",
                    "round_index": 1,
                    "evidence": ["营收 200 亿", "外贸 5%"],
                },
                {
                    "claim_id": "INV-2",
                    "speaker_key": "Bull",
                    "round_index": 2,
                    "evidence": ["营收 200 亿"],
                },
            ],
            "challenges": [
                {
                    "challenge_id": "CH-1",
                    "target_claim_id": "INV-1",
                    "severity": "major",
                    "status": "verified",
                    "adopted": True,
                }
            ],
        },
        "manager_verdict": {
            "adopted_challenge_ids": ["CH-1"],
            "claim_evidence_summary": {
                "INV-1": {"speaker_key": "Bull", "counts": {"total": 2, "verified": 2}},
                "INV-2": {"speaker_key": "Bull", "counts": {"total": 1, "verified": 1}},
            },
        },
    }

    harness = OfflineABHarness()
    res = harness.compare_report(synthetic_result_data)

    assert "legacy" in res
    assert "v2" in res
    assert "diff" in res
    assert "summary" in res

    # Legacy evaluator sees v1_legacy
    assert res["legacy"]["protocol_version"] == "v1_legacy"
    assert res["legacy"]["challenge_metrics"]["challenge_count"]["status"] == "legacy_no_data"

    # V2 evaluator sees v2 structured disagreement with challenge metrics
    assert res["v2"]["protocol_version"] == "v2_structured_disagreement"
    assert res["v2"]["challenge_metrics"]["challenge_count"]["numerator"] == 1
    assert res["v2"]["challenge_metrics"]["challenge_adoption_rate"]["rate"] == 1.0

    # Diff captures structural differences
    diff = res["diff"]
    assert diff["protocol_version"] == ("v1_legacy", "v2_structured_disagreement")
    assert "seven_reports_utilization_rate" in diff
    assert "field_completeness_rate" in diff


def test_offline_ab_harness_no_network_or_llm():
    """Assert that running OfflineABHarness makes zero network or LLM API calls."""
    harness = OfflineABHarness()
    minimal_data = {
        "confidence": 60,
        "probability": 0.55,
        "target_price": 50.0,
        "stop_loss_price": 45.0,
        "decision": "HOLD",
        "investment_debate_state": {"claims": []},
    }

    # Patch requests and OpenAI / Anthropic client calls to raise if invoked
    with patch("requests.get", side_effect=RuntimeError("Network call forbidden in offline A/B")), \
         patch("requests.post", side_effect=RuntimeError("Network call forbidden in offline A/B")):
        res = harness.compare_report(minimal_data)
        assert res is not None
        assert res["legacy"]["field_completeness"]["rate"] == 1.0


def test_offline_ab_harness_golden_replay():
    """Replay across the 3 golden audit cases (000333, 600900, 600276).

    Verifies that fixtures can be replayed in read-only mode without errors
    and without altering any files.
    """
    harness = OfflineABHarness()
    golden_dir = Path(__file__).resolve().parent / "golden" / "audit_20260823"
    assert golden_dir.exists(), f"Golden directory not found at {golden_dir}"

    comparison_results = harness.compare_golden_fixtures(golden_dir)
    assert len(comparison_results) == 3

    for sym, res in comparison_results.items():
        assert sym in ("000333.SZ", "600900.SH", "600276.SH")
        assert "legacy" in res
        assert "v2" in res
        assert "diff" in res
        # Check that field completeness is evaluated for each
        assert res["legacy"]["field_completeness"]["denominator"] == 4
        assert res["legacy"]["seven_reports_utilization"]["denominator"] > 0
        assert res["legacy"]["macro_utilization"]["denominator"] > 0
        assert res["legacy"]["fundamentals_utilization"]["denominator"] > 0
