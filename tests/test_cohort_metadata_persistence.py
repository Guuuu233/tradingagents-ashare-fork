"""Tests for DAV-604: H1b cohort metadata persistence on analysis completion.

Validates:
1. Newly completed reports via create_report persist the cohort triad + commit SHA into result_data root dictionary.
2. Completed reports via update_report_partial persist the cohort triad + commit SHA.
3. Priority for generated_by_commit_sha: GIT_COMMIT_SHA > git rev-parse HEAD > None (explicit missing marker).
   Never fabricates today's date or random UUIDs.
4. Legacy samples (unversioned or explicitly marked legacy) are NOT backfilled to v1.
5. Non-completed reports (failed/pending) do not receive cohort metadata.
6. Persisted reports integrate seamlessly with shadow_credit.extract_sample_cohort and filter_reports_by_cohort.
"""

from __future__ import annotations

import os
import re
import subprocess
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, ReportDB
from api.services.report_service import (
    create_report,
    init_report,
    update_report_partial,
)
from tradingagents.agents.utils.agent_states import PROTOCOL_VERSION_V2_STRUCTURED
from tradingagents.agents.utils.shadow_credit import (
    COHORT_LEGACY_UNVERSIONED,
    DECISION_MODEL_LEGACY,
    DECISION_MODEL_V1,
    PRICE_BASIS_UNSPECIFIED,
    extract_sample_cohort,
    filter_reports_by_cohort,
)


@pytest.fixture
def sqlite_db_session():
    """In-memory SQLite session for report_service tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


class TestCohortMetadataPersistence:
    """Test suite for DAV-604 cohort metadata persistence."""

    def test_create_report_persists_cohort_metadata_in_result_data(self, sqlite_db_session):
        """1. Newly completed report must persist 4 cohort fields into result_data root."""
        report_id = f"test-rep-{uuid4().hex[:8]}"
        res_data = {
            "symbol": "688981.SH",
            "market_report": "半导体龙头分析",
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "manager_verdict": {"winner": "bull", "direction": "看多"},
        }

        rep = create_report(
            db=sqlite_db_session,
            symbol="688981.SH",
            trade_date="2026-09-04",
            decision="BUY",
            result_data=res_data,
            report_id=report_id,
            status="completed",
        )

        assert rep.id == report_id
        assert isinstance(rep.result_data, dict)

        # 4 required fields in root dictionary of ReportDB.result_data
        assert "decision_model_version" in rep.result_data
        assert "evidence_contract_version" in rep.result_data
        assert "price_basis_version" in rep.result_data
        assert "generated_by_commit_sha" in rep.result_data

        assert rep.result_data["decision_model_version"] == "decision_model.v1"
        assert rep.result_data["evidence_contract_version"] == "evidence_contract.v1"
        assert rep.result_data["price_basis_version"] == "price_basis.unspecified"

        sha = rep.result_data["generated_by_commit_sha"]
        assert sha is not None
        assert len(sha) == 40
        assert bool(re.match(r"^[0-9a-f]{40}$", sha))

        # Direct DB query verification
        from_db = sqlite_db_session.query(ReportDB).filter(ReportDB.id == report_id).first()
        assert from_db is not None
        assert from_db.result_data["decision_model_version"] == "decision_model.v1"
        assert from_db.result_data["evidence_contract_version"] == "evidence_contract.v1"
        assert from_db.result_data["price_basis_version"] == "price_basis.unspecified"
        assert from_db.result_data["generated_by_commit_sha"] == sha

    def test_update_report_partial_completed_persists_cohort_metadata(self, sqlite_db_session):
        """2. Completed reports via update_report_partial persist cohort triad + sha."""
        report_id = f"test-init-{uuid4().hex[:8]}"
        init_report(
            db=sqlite_db_session,
            report_id=report_id,
            symbol="600519.SH",
            trade_date="2026-09-04",
        )

        res_data = {
            "symbol": "600519.SH",
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "manager_verdict": {"winner": "bull", "direction": "看多"},
        }
        updated = update_report_partial(
            db=sqlite_db_session,
            report_id=report_id,
            status="completed",
            result_data=res_data,
        )

        assert updated.status == "completed"
        assert isinstance(updated.result_data, dict)
        assert updated.result_data["decision_model_version"] == "decision_model.v1"
        assert updated.result_data["evidence_contract_version"] == "evidence_contract.v1"
        assert updated.result_data["price_basis_version"] == "price_basis.unspecified"
        assert updated.result_data["generated_by_commit_sha"] is not None
        assert len(updated.result_data["generated_by_commit_sha"]) == 40

    def test_commit_sha_prefers_git_commit_sha_env(self, sqlite_db_session):
        """3a. Priority: GIT_COMMIT_SHA env var takes precedence if 40 hex chars."""
        test_env_sha = "1234567890abcdef1234567890abcdef12345678"
        with patch.dict(os.environ, {"GIT_COMMIT_SHA": test_env_sha}):
            report_id = f"test-env-{uuid4().hex[:8]}"
            rep = create_report(
                db=sqlite_db_session,
                symbol="000858.SZ",
                trade_date="2026-09-04",
                decision="BUY",
                result_data={"symbol": "000858.SZ"},
                report_id=report_id,
                status="completed",
            )
            assert rep.result_data["generated_by_commit_sha"] == test_env_sha

    def test_commit_sha_falls_back_to_git_rev_parse(self, sqlite_db_session):
        """3b. Priority: If GIT_COMMIT_SHA is unset/invalid, falls back to git rev-parse HEAD."""
        with patch.dict(os.environ, {"GIT_COMMIT_SHA": ""}):
            report_id = f"test-git-{uuid4().hex[:8]}"
            rep = create_report(
                db=sqlite_db_session,
                symbol="000858.SZ",
                trade_date="2026-09-04",
                decision="BUY",
                result_data={"symbol": "000858.SZ"},
                report_id=report_id,
                status="completed",
            )
            sha = rep.result_data["generated_by_commit_sha"]
            assert sha is not None
            assert len(sha) == 40
            assert bool(re.match(r"^[0-9a-f]{40}$", sha))

    def test_commit_sha_explicit_missing_when_resolution_fails(self, sqlite_db_session):
        """3c. If both GIT_COMMIT_SHA and git rev-parse fail: explicitly None, no random/fake value."""
        with patch.dict(os.environ, {"GIT_COMMIT_SHA": ""}):
            with patch("subprocess.run", side_effect=RuntimeError("git not found")):
                report_id = f"test-fail-{uuid4().hex[:8]}"
                rep = create_report(
                    db=sqlite_db_session,
                    symbol="000858.SZ",
                    trade_date="2026-09-04",
                    decision="BUY",
                    result_data={"symbol": "000858.SZ"},
                    report_id=report_id,
                    status="completed",
                )
                assert "generated_by_commit_sha" in rep.result_data
                # Must be explicitly None (never today's date, timestamp, or random uuid)
                assert rep.result_data["generated_by_commit_sha"] is None
                assert rep.result_data["decision_model_version"] == "decision_model.v1"
                assert rep.result_data["evidence_contract_version"] == "evidence_contract.v1"
                assert rep.result_data["price_basis_version"] == "price_basis.unspecified"

    def test_legacy_samples_not_backfilled_to_v1(self, sqlite_db_session):
        """4. Old 121 / legacy samples MUST NOT be backfilled or overwritten with v1."""
        report_id = f"test-legacy-{uuid4().hex[:8]}"
        res_data = {
            "symbol": "600519.SH",
            "decision_model_version": DECISION_MODEL_LEGACY,
        }
        rep = create_report(
            db=sqlite_db_session,
            symbol="600519.SH",
            trade_date="2026-08-01",
            decision="BUY",
            result_data=res_data,
            report_id=report_id,
            status="completed",
        )
        assert rep.result_data["decision_model_version"] == DECISION_MODEL_LEGACY
        assert rep.result_data["decision_model_version"] != "decision_model.v1"

    def test_existing_completed_historical_report_not_backfilled_on_partial_update(self, sqlite_db_session):
        """4a. RED test: An existing completed historical report lacking cohort keys must NOT be backfilled to v1
        when update_report_partial(status='completed') is called on it.
        """
        report_id = f"test-hist-{uuid4().hex[:8]}"
        # Simulate an old 121 sample already in DB: status=completed, result_data has NO cohort keys
        historical_result_data = {
            "symbol": "600519.SH",
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "manager_verdict": {"winner": "bull", "direction": "看多"},
        }
        hist_report = ReportDB(
            id=report_id,
            symbol="600519.SH",
            trade_date="2026-08-01",
            status="completed",
            result_data=historical_result_data,
        )
        sqlite_db_session.add(hist_report)
        sqlite_db_session.commit()

        # Now call update_report_partial on this existing completed report without new result_data
        updated = update_report_partial(
            db=sqlite_db_session,
            report_id=report_id,
            status="completed",
        )

        assert updated.status == "completed"
        # Assert none of the 4 cohort keys were backfilled!
        assert "decision_model_version" not in updated.result_data
        assert "evidence_contract_version" not in updated.result_data
        assert "price_basis_version" not in updated.result_data
        assert "generated_by_commit_sha" not in updated.result_data

        # Also verify from direct database query
        from_db = sqlite_db_session.query(ReportDB).filter(ReportDB.id == report_id).first()
        assert "decision_model_version" not in from_db.result_data

    def test_existing_completed_historical_report_not_backfilled_on_create_report(self, sqlite_db_session):
        """4b. An existing completed historical report lacking cohort keys must NOT be backfilled to v1
        when create_report is called to update fields on it.
        """
        report_id = f"test-hist-cr-{uuid4().hex[:8]}"
        historical_result_data = {
            "symbol": "600519.SH",
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "manager_verdict": {"winner": "bull", "direction": "看多"},
        }
        hist_report = ReportDB(
            id=report_id,
            symbol="600519.SH",
            trade_date="2026-08-01",
            status="completed",
            result_data=historical_result_data,
        )
        sqlite_db_session.add(hist_report)
        sqlite_db_session.commit()

        # Update the existing completed report via create_report
        updated = create_report(
            db=sqlite_db_session,
            report_id=report_id,
            symbol="600519.SH",
            trade_date="2026-08-01",
            decision="BUY",
            result_data=dict(historical_result_data),
            status="completed",
        )

        assert updated.status == "completed"
        assert "decision_model_version" not in updated.result_data
        assert "evidence_contract_version" not in updated.result_data
        assert "price_basis_version" not in updated.result_data
        assert "generated_by_commit_sha" not in updated.result_data

        from_db = sqlite_db_session.query(ReportDB).filter(ReportDB.id == report_id).first()
        assert "decision_model_version" not in from_db.result_data

    def test_explicit_none_dmv_not_overwritten(self, sqlite_db_session):
        """4b. Explicit None decision_model_version (unlabeled legacy sample) is preserved."""
        report_id = f"test-none-{uuid4().hex[:8]}"
        res_data = {
            "symbol": "600519.SH",
            "decision_model_version": None,
        }
        rep = create_report(
            db=sqlite_db_session,
            symbol="600519.SH",
            trade_date="2026-08-01",
            decision="BUY",
            result_data=res_data,
            report_id=report_id,
            status="completed",
        )
        assert rep.result_data["decision_model_version"] is None
        assert rep.result_data["decision_model_version"] != "decision_model.v1"

    def test_non_completed_reports_do_not_persist_cohort_metadata(self, sqlite_db_session):
        """5. Failed or pending reports do not get stamped with cohort metadata."""
        report_id = f"test-failed-{uuid4().hex[:8]}"
        res_data = {
            "symbol": "688981.SH",
            "error": "Timeout",
            "status": "failed",
        }
        rep = create_report(
            db=sqlite_db_session,
            symbol="688981.SH",
            trade_date="2026-09-04",
            decision="HOLD",
            result_data=res_data,
            report_id=report_id,
            status="failed",
        )
        assert rep.status == "failed"
        assert "decision_model_version" not in rep.result_data
        assert "evidence_contract_version" not in rep.result_data
        assert "price_basis_version" not in rep.result_data
        assert "generated_by_commit_sha" not in rep.result_data

    def test_dual_horizon_nested_cohort_persistence(self, sqlite_db_session):
        """6. Dual horizon reports persist cohort metadata at root and nested horizons."""
        report_id = f"test-dual-{uuid4().hex[:8]}"
        res_data = {
            "symbol": "600519.SH",
            "mode": "dual_horizon",
            "short_term": {
                "decision": "BUY",
                "status": "completed",
            },
            "medium_term": {
                "decision": "HOLD",
                "status": "completed",
            },
        }
        rep = create_report(
            db=sqlite_db_session,
            symbol="600519.SH",
            trade_date="2026-09-04",
            decision="BUY",
            result_data=res_data,
            report_id=report_id,
            status="completed",
        )
        # Root dictionary
        assert rep.result_data["decision_model_version"] == "decision_model.v1"
        assert rep.result_data["evidence_contract_version"] == "evidence_contract.v1"
        assert rep.result_data["price_basis_version"] == "price_basis.unspecified"
        assert len(rep.result_data["generated_by_commit_sha"]) == 40

        # Nested horizons
        assert rep.result_data["short_term"]["decision_model_version"] == "decision_model.v1"
        assert rep.result_data["medium_term"]["decision_model_version"] == "decision_model.v1"

    def test_persisted_report_compatible_with_h1b_cohort_filtering(self, sqlite_db_session):
        """7. Persisted reports integrate seamlessly with shadow_credit cohort filtering."""
        report_id = f"test-filter-{uuid4().hex[:8]}"
        rep = create_report(
            db=sqlite_db_session,
            symbol="688981.SH",
            trade_date="2026-09-04",
            decision="BUY",
            result_data={
                "symbol": "688981.SH",
                "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
                "manager_verdict": {"winner": "bull", "direction": "看多"},
            },
            report_id=report_id,
            status="completed",
        )

        extracted = extract_sample_cohort(rep.to_dict())
        assert extracted["decision_model_version"] == "decision_model.v1"
        assert extracted["evidence_contract_version"] == "evidence_contract.v1"
        assert extracted["price_basis_version"] == "price_basis.unspecified"
        assert len(extracted["generated_by_commit_sha"]) == 40

        target_cohort = "decision_model.v1:evidence_contract.v1:price_basis.unspecified"
        filtered, meta = filter_reports_by_cohort([rep.to_dict()], cohort=target_cohort)
        assert len(filtered) == 1
        assert meta["canonical_key"] == target_cohort
        assert meta["commit_shas"] == [extracted["generated_by_commit_sha"]]
