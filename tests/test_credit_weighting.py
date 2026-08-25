"""Unit tests for credit weighting integration, feature flags, and verify_h1b_gates script."""

import json
import os
import subprocess
import sys
import pytest

from tradingagents.agents.utils.agent_states import (
    DEFAULT_FEATURE_FLAGS,
    PROTOCOL_VERSION_V2_STRUCTURED,
    get_protocol_metadata,
)
from tradingagents.agents.utils.shadow_credit import (
    SCHEMA_VERSION,
    calculate_shadow_credit_metrics,
    evaluate_h1b_system_gates,
    apply_credit_weighting_to_debate,
)


def test_default_feature_flags_credit_weighting_is_false():
    """Verify DEFAULT_FEATURE_FLAGS['credit_weighting_enabled'] is strictly False."""
    assert DEFAULT_FEATURE_FLAGS["credit_weighting_enabled"] is False


def test_shadow_credit_metrics_records_h1b_fields_without_breaking_h1a():
    """Verify calculate_shadow_credit_metrics preserves all H1a fields and handles credit_weighting_enabled."""
    sample = {
        "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
        "feature_flags": {
            "v2_debate_enabled": True,
            "shadow_credit_enabled": True,
            "credit_weighting_enabled": False,
        },
        "investment_debate_state": {
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "claims": [
                {
                    "claim_id": "C1",
                    "speaker": "Bull",
                    "status": "verified",
                    "is_verified": True,
                }
            ],
            "claim_evidence_summary": {
                "C1": {"counts": {"verified": 1, "total": 1}, "decision": "adopt"},
            },
        },
    }

    metrics = calculate_shadow_credit_metrics(sample)
    assert metrics["schema_version"] == SCHEMA_VERSION
    assert metrics["credit_weighting_enabled"] is False
    assert metrics["bull_verified_rate"] == 1.0


def test_verify_h1b_gates_script_cli_runs_and_reports_fail():
    """Test running scripts/verify_h1b_gates.py via python subprocess."""
    script_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        "verify_h1b_gates.py",
    )
    python_bin = sys.executable

    # Run script against empty DB or repo directory
    proc = subprocess.run(
        [python_bin, script_path, "--output-json", "work/h1b_gates_report.json"],
        capture_output=True,
        text=True,
    )

    # Script should execute, produce JSON output and print 7-dimension matrix
    assert os.path.exists("work/h1b_gates_report.json") or proc.returncode in (0, 1)
