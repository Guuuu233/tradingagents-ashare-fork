"""DAV-1322: H1b 资格解析层测试。

Coverage:
1. Dual-horizon reports split into per-horizon units (short_term / medium_term);
   the packaged top level is NEVER counted as a unit.
2. Qualification is based on structured evidence (winner + claims /
   claim_evidence_summary / challenges), not protocol_version — v1_legacy
   reports with complete structured evidence qualify; reports missing all
   structured artifacts are rejected even with protocol_version=v2.
3. Cohort key appends the horizon component: short and medium never mix in
   the same cohort, and one report contributes at most one unit per cohort.
"""

import pytest

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
)
from tradingagents.agents.utils.shadow_credit import (
    HORIZON_UNSPECIFIED,
    extract_sample_cohort,
    filter_reports_by_cohort,
    filter_v2_completed_reports,
    is_cohort_homogeneous,
    is_qualifying_v2_report,
    parse_cohort_spec,
    split_report_into_units,
)


def _horizon_subreport(
    *,
    horizon: str,
    winner: str,
    trade_action: str,
    analysis_status: str = "VALID",
) -> dict:
    """Minimal per-horizon sub-report as nested under result_data.short_term / medium_term."""
    return {
        "horizon": horizon,
        "trade_date": "2026-09-20",
        "protocol_version": PROTOCOL_VERSION_V1_LEGACY,
        "decision_model_version": "decision_model.v1",
        "evidence_contract_version": "evidence_contract.v2",
        "price_basis_version": "price_basis.vendor_qfq",
        "price_ref_contract_version": "price_ref.v1",
        "analysis_status": analysis_status,
        "trade_action": trade_action,
        "investment_debate_state": {
            "protocol_version": PROTOCOL_VERSION_V1_LEGACY,
            "claims": [
                {"claim_id": "c1", "speaker_key": "Bull", "stance": "bullish"},
                {"claim_id": "c2", "speaker_key": "Bear", "stance": "bearish"},
            ],
            "challenges": [
                {"challenge_id": "ch1", "speaker_key": "Bull"},
            ],
            "manager_verdict": {
                "winner": winner,
                "direction": "看多" if winner == "bull" else "看空",
                "claim_evidence_summary": {
                    "c1": {"speaker_key": "Bull", "counts": {"verified": 1, "total": 1}},
                    "c2": {"speaker_key": "Bear", "counts": {"verified": 1, "total": 1}},
                },
            },
        },
        "manager_verdict": {
            "winner": winner,
            "claim_evidence_summary": {
                "c1": {"speaker_key": "Bull", "counts": {"verified": 1, "total": 1}},
                "c2": {"speaker_key": "Bear", "counts": {"verified": 1, "total": 1}},
            },
        },
    }


def _dual_report() -> dict:
    """Packaged dual-horizon report: aggregate top level must never count."""
    return {
        "id": "dual-rep-1",
        "symbol": "600036.SH",
        "trade_date": "2026-09-20",
        "status": "completed",
        # Aggregate top level mixes both horizons (PARTIAL/NO_TRADE) — poison if counted.
        "analysis_status": "PARTIAL",
        "trade_action": "NO_TRADE",
        "result_data": {
            "symbol": "600036.SH",
            "mode": "dual_horizon",
            "decision_model_version": "decision_model.v1",
            "generated_by_commit_sha": "a" * 40,
            "short_term": _horizon_subreport(horizon="short", winner="bull", trade_action="BUY"),
            "medium_term": _horizon_subreport(horizon="medium", winner="bear", trade_action="SELL"),
        },
    }


class TestDualHorizonSplitting:
    def test_dual_report_splits_into_two_units_top_not_counted(self):
        units = split_report_into_units(_dual_report())
        assert len(units) == 2
        horizons = sorted(u["horizon"] for u in units)
        assert horizons == ["medium", "short"]
        # No unit is the packaged top-level aggregate
        for u in units:
            assert u.get("mode") is None or u.get("mode") != "dual_horizon"
            assert u["analysis_status"] == "VALID"
            assert u["trade_action"] in ("BUY", "SELL")
            assert u["id"] == "dual-rep-1"
            assert u["symbol"] == "600036.SH"
            assert u["parent_report_id"] == "dual-rep-1"

    def test_filter_counts_units_not_top_level(self):
        """1 dual report -> 2 clean units; top-level PARTIAL/NO_TRADE never counted."""
        filtered, excluded, ledger = filter_v2_completed_reports(
            [_dual_report()], return_ledger=True
        )
        assert ledger["raw_count"] == 1
        assert ledger["unit_count"] == 2
        assert ledger["dual_horizon_split_reports"] == 1
        assert ledger["qualifying_v2_count"] == 2
        assert ledger["eligible_count"] == 2
        assert ledger["clean_count"] == 2
        assert len(filtered) == 2

    def test_single_horizon_report_remains_one_unit(self):
        single = {
            "id": "single-1",
            "symbol": "600519.SH",
            "status": "completed",
            "horizon": "short",
            "analysis_status": "VALID",
            "trade_action": "BUY",
            "claims": [{"claim_id": "c1", "speaker_key": "Bull"}],
            "manager_verdict": {"winner": "bull"},
        }
        units = split_report_into_units(single)
        assert len(units) == 1
        assert units[0]["id"] == "single-1"

    def test_dual_report_missing_medium_yields_one_unit(self):
        rep = _dual_report()
        del rep["result_data"]["medium_term"]
        units = split_report_into_units(rep)
        assert len(units) == 1
        assert units[0]["horizon"] == "short"


class TestStructuredEvidenceQualification:
    def test_v1_legacy_with_structured_evidence_qualifies(self):
        rep = {
            "status": "completed",
            "protocol_version": PROTOCOL_VERSION_V1_LEGACY,
            "analysis_status": "VALID",
            "trade_action": "BUY",
            "investment_debate_state": {
                "claims": [{"claim_id": "c1", "speaker_key": "Bull"}],
                "manager_verdict": {"winner": "bull"},
            },
        }
        assert is_qualifying_v2_report(rep) is True

    def test_v2_without_structured_evidence_rejected(self):
        """protocol_version=v2 alone no longer qualifies without evidence artifacts."""
        rep = {
            "status": "completed",
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "analysis_status": "VALID",
            "trade_action": "BUY",
            "manager_verdict": {"winner": "bull"},
        }
        assert is_qualifying_v2_report(rep) is False

    def test_winner_plus_challenges_only_qualifies(self):
        rep = {
            "status": "completed",
            "protocol_version": PROTOCOL_VERSION_V1_LEGACY,
            "investment_debate_state": {
                "challenges": [{"challenge_id": "ch1", "speaker_key": "Bear"}],
                "manager_verdict": {"winner": "bear"},
            },
        }
        assert is_qualifying_v2_report(rep) is True

    def test_winner_missing_rejected_despite_evidence(self):
        rep = {
            "status": "completed",
            "protocol_version": PROTOCOL_VERSION_V1_LEGACY,
            "claims": [{"claim_id": "c1", "speaker_key": "Bull"}],
            "manager_verdict": {},
        }
        assert is_qualifying_v2_report(rep) is False


class TestHorizonCohortSeparation:
    def test_cohort_key_appends_horizon(self):
        spec = parse_cohort_spec(
            "decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq:short"
        )
        assert spec["horizon"] == "short"
        assert spec["canonical_key"].endswith(":short")

        spec_default = parse_cohort_spec(
            "decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq"
        )
        assert spec_default["horizon"] == HORIZON_UNSPECIFIED
        assert spec_default["canonical_key"].endswith(f":{HORIZON_UNSPECIFIED}")

    def test_extract_sample_cohort_reads_horizon(self):
        c = extract_sample_cohort({"horizon": "short"})
        assert c["horizon"] == "short"
        c_none = extract_sample_cohort({})
        assert c_none["horizon"] is None

    def test_short_medium_never_same_cohort(self):
        rep = _dual_report()
        units = filter_v2_completed_reports([rep])
        assert len(units) == 2
        is_homo, _ = is_cohort_homogeneous(units)
        assert is_homo is False

        # Each horizon cohort contains exactly one unit from the same report
        short_filtered, meta_s = filter_reports_by_cohort(
            units, "decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq:short"
        )
        medium_filtered, meta_m = filter_reports_by_cohort(
            units, "decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq:medium"
        )
        assert len(short_filtered) == 1
        assert len(medium_filtered) == 1
        assert short_filtered[0]["horizon"] == "short"
        assert medium_filtered[0]["horizon"] == "medium"
        # A legacy triad spec (no horizon) matches neither horizon unit
        triad_filtered, _ = filter_reports_by_cohort(
            units, "decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq"
        )
        assert len(triad_filtered) == 0

    def test_legacy_unversioned_dual_report_horizon_isolation(self):
        """红队回归：无版本字段的双档报告拆包后，short/medium 单元不得落入同一
        legacy_unversioned cohort（同 cohort 至多 1 单元）。"""
        rep = _dual_report()
        # Strip every version field -> legacy_unversioned units
        rep["result_data"].pop("decision_model_version", None)
        for sub in (rep["result_data"]["short_term"], rep["result_data"]["medium_term"]):
            for k in (
                "decision_model_version",
                "evidence_contract_version",
                "price_basis_version",
                "price_ref_contract_version",
            ):
                sub.pop(k, None)
        units = filter_v2_completed_reports([rep])
        assert len(units) == 2

        # Units are NOT homogeneous: they belong to distinct legacy cohorts
        is_homo, _ = is_cohort_homogeneous(units)
        assert is_homo is False

        cohorts = {extract_sample_cohort(u)["horizon"] for u in units}
        assert cohorts == {"short", "medium"}

        short_f, meta_s = filter_reports_by_cohort(units, "legacy_unversioned:short")
        med_f, meta_m = filter_reports_by_cohort(units, "legacy_unversioned:medium")
        assert [u["horizon"] for u in short_f] == ["short"]
        assert [u["horizon"] for u in med_f] == ["medium"]
        assert meta_s["canonical_key"] == "legacy_unversioned:short"
        assert meta_m["canonical_key"] == "legacy_unversioned:medium"

        # Bare legacy_unversioned spec only matches horizon-unspecified legacy
        bare, meta_bare = filter_reports_by_cohort(units, "legacy_unversioned")
        assert len(bare) == 0
        assert meta_bare["canonical_key"] == "legacy_unversioned"

    def test_legacy_unversioned_without_horizon_still_matches_bare_spec(self):
        """Backward-compat: horizon-less legacy samples stay in bare legacy cohort."""
        legacy = [
            {"symbol": "600519.SH"},
            {"symbol": "000858.SZ", "decision_model_version": "decision_model.legacy_unversioned"},
        ]
        filtered, meta = filter_reports_by_cohort(legacy, cohort="legacy_unversioned")
        assert len(filtered) == 2
        assert meta["cohort_type"] == "legacy_unversioned"
        is_homo, key = is_cohort_homogeneous(legacy)
        assert is_homo is True
        assert key == "legacy_unversioned"
