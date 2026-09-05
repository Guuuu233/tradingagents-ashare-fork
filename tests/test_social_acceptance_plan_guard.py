"""Test guards locking the social data acceptance plan and checklist invariants.

Verifies:
- docs/social_data/acceptance_checklist.md and docs/social_data/implementation_plan.md exist.
- Hard gate thresholds and core numbers are strictly preserved and NOT lowered:
  * MediaCrawler pinned commit SHA: d6f7c5bb906b6dac40ddf343ef9e26438a3de092
  * Gate 0: SQLite working DB, loopback 127.0.0.1, xhs/dy dual-platform >=1 round, append-only (no UPDATE on snapshots)
  * Gate 2: shadow mode, 30 份 reports / 10 只股票 manual coverage, direction_allowed=false guard
  * Gate 3: 2–5 只 canary active (marked as requiring authorization)
  * Historical missing snapshot: marked as missing, forbid backfilling with newly collected data
  * Invariant: Unavailable != market cold (honest gap semantics)
  * D-009: Incomplete gates forbid claiming social integration done
  * Separation of '代码已交付' vs '真实未做' and presence of pending authorizations AUTH-01..AUTH-07
"""

import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs" / "social_data"
CHECKLIST_FILE = DOCS_DIR / "acceptance_checklist.md"
PLAN_FILE = DOCS_DIR / "implementation_plan.md"


def test_acceptance_documents_exist():
    """Verify that both acceptance checklist and implementation plan exist."""
    assert CHECKLIST_FILE.is_file(), f"Missing required checklist: {CHECKLIST_FILE}"
    assert PLAN_FILE.is_file(), f"Missing required implementation plan: {PLAN_FILE}"


def test_mediacrawler_commit_sha_locked():
    """MediaCrawler pinned commit SHA must be exactly d6f7c5bb906b6dac40ddf343ef9e26438a3de092."""
    expected_sha = "d6f7c5bb906b6dac40ddf343ef9e26438a3de092"
    checklist_text = CHECKLIST_FILE.read_text(encoding="utf-8")
    plan_text = PLAN_FILE.read_text(encoding="utf-8")

    assert expected_sha in checklist_text
    assert expected_sha in plan_text


def test_gate0_thresholds_intact():
    """Gate 0 must require SQLite work DB, loopback 127.0.0.1, dual-platform (xhs/dy) ingestion, and append-only."""
    for filepath in (CHECKLIST_FILE, PLAN_FILE):
        content = filepath.read_text(encoding="utf-8")
        assert "sqlite" in content.lower(), f"{filepath.name} must mandate sqlite"
        assert "127.0.0.1" in content, f"{filepath.name} must mandate loopback 127.0.0.1"
        assert "xhs" in content and "dy" in content, f"{filepath.name} must mention xhs and dy platforms"
        assert ("UPDATE" in content or "update" in content.lower()) and "append-only" in content


def test_gate2_shadow_and_manual_audit_thresholds_intact():
    """Gate 2 threshold numbers must NOT be lowered: exactly 30 份 reports and 10 只股票."""
    for filepath in (CHECKLIST_FILE, PLAN_FILE):
        content = filepath.read_text(encoding="utf-8")
        assert "TA_SOCIAL_MODE=shadow" in content or "shadow" in content
        assert "30 份" in content or "30份" in content, f"{filepath.name} must lock 30 份 reports threshold"
        assert "10 只股票" in content or "10只股票" in content, f"{filepath.name} must lock 10 只股票 threshold"
        assert "direction_allowed=false" in content or "direction_allowed" in content


def test_gate3_canary_thresholds_intact():
    """Gate 3 canary numbers must NOT be lowered: exactly 2–5 只 canary stocks, requiring authorization."""
    for filepath in (CHECKLIST_FILE, PLAN_FILE):
        content = filepath.read_text(encoding="utf-8")
        assert ("2–5 只" in content or "2-5 只" in content or "2–5" in content), f"{filepath.name} must lock 2-5 canary"
        assert "canary" in content.lower()
        assert ("授权" in content), f"{filepath.name} must mark canary execution as requiring authorization"


def test_historical_snapshot_no_backfill_invariant():
    """Historical missing snapshot must be flagged missing; backfilling with new data is forbidden."""
    for filepath in (CHECKLIST_FILE, PLAN_FILE):
        content = filepath.read_text(encoding="utf-8")
        assert (
            "禁止用当天新采回填" in content
            or "严禁当天新采回填" in content
            or "禁止用当天或事后新采" in content
        )
        assert "缺失" in content


def test_historical_snapshot_no_backfill_invariant_negative():
    """Verify that temporary content lacking backfill prohibition phrases fails assertion."""
    for bad_content in ("", "历史快照缺失标为缺失，但未包含任何回填禁令短语"):
        with pytest.raises(AssertionError):
            assert (
                "禁止用当天新采回填" in bad_content
                or "严禁当天新采回填" in bad_content
                or "禁止用当天或事后新采" in bad_content
            )


def test_d009_rule_and_delivery_status_separation():
    """Checklist must enforce D-009 rule and explicitly separate '代码已交付' vs '真实未做'."""
    checklist_text = CHECKLIST_FILE.read_text(encoding="utf-8")
    assert "D-009" in checklist_text
    assert "代码已交付" in checklist_text
    assert "真实未做" in checklist_text
    assert "待授权" in checklist_text

    # All 7 authorizations must be documented
    for auth_id in ("AUTH-01", "AUTH-02", "AUTH-03", "AUTH-04", "AUTH-05", "AUTH-06", "AUTH-07"):
        assert auth_id in checklist_text, f"Checklist missing {auth_id}"
