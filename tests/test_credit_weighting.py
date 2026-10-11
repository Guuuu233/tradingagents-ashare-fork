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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def test_verify_h1b_gates_script_cli_runs_and_reports_fail(tmp_path, repo_script_env):
    """scripts/verify_h1b_gates.py runs end to end and fails closed on an empty cohort.

    Previously the output path was the repo-relative ``work/h1b_gates_report.json``
    and the assertion was ``exists(...) or returncode in (0, 1)`` — which passes
    unconditionally and let the test write into the developer's checkout. It now
    writes into ``tmp_path`` against an explicit empty DB and pins both the exit
    code and the payload: exit 0 (the CLI itself succeeded) with
    ``passed is False`` / ``KEEP_FALSE`` (the gate failed closed, never a PASS).
    """
    from sqlalchemy import create_engine
    from api.database import Base

    script_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        "verify_h1b_gates.py",
    )
    db_path = str(tmp_path / "empty_reports.db")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()

    out_json = tmp_path / "h1b_gates_report.json"
    proc = subprocess.run(
        [sys.executable, script_path,
         "--cohort", "legacy_unversioned",
         "--db-path", db_path,
         "--output-json", str(out_json)],
        capture_output=True,
        text=True,
        env=repo_script_env,
        cwd=REPO_ROOT,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert out_json.exists()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["sample_count"] == 0
    assert payload["recommendation"] == "KEEP_FALSE"
    assert payload["gate_evaluation"]["passed"] is False
    assert len(payload["gate_evaluation"]["matrix"]) == 7
    assert payload["model_isolation"]["credit_weighting_active"] is False
    assert payload["model_isolation"]["system_gate_status"] == "FAIL"
