"""Tests for DAV-1200 (1142-B3) legacy price-basis isolation manifest.

Contract Requirements:
1. Manifest double layer: 86 history confirmed + 51 formal pool (15/18/18).
2. Anchor b188060fa75045bd9e52d1eacd265372 must be confirmed_contaminated_v1.
3. filter_v2_completed_reports applies price-basis isolation as an INDEPENDENT
   stage: reasons price_basis_contaminated / price_basis_pending_review /
   price_basis_contract_incomplete live in the ledger only, never in D-009
   excluded_counts; ledger quantity conservation holds.
4. New-contract samples fail close: clean eligibility requires BOTH
   price_basis_version=price_basis.vendor_qfq AND
   price_ref_contract_version=price_ref.v1.
5. All tests use in-memory fixtures; no network, no production DB.
"""

from typing import Any, Mapping, Optional

import pytest

from tradingagents.agents.utils.price_basis_isolation import (
    MANIFEST_VERSION,
    REASON_CONTRACT_INCOMPLETE,
    REASON_CONTAMINATED,
    REASON_PENDING_REVIEW,
    classify_price_basis_exclusion,
    load_manifest,
    manifest_entry,
)
from tradingagents.agents.utils.shadow_credit import (
    PROTOCOL_VERSION_V2_STRUCTURED,
    filter_v2_completed_reports,
)

ANCHOR_ID = "b188060fa75045bd9e52d1eacd265372"
# Real ids from the DAV-1197 crosswalk (checked into the manifest)
PENDING_ID = "07ee2029810b405fb067989dea75b937"  # pending_review_v1, H1b, qfq_cooc_same_ref=True
PENDING_ID_LOW = "8b8b3ca3d8df47b88be23e9b560d98d5"  # pending_review_v1, B2, qfq_cooc_same_ref=False
CLEAN_ID = "173bbf00a2cb49cd9a7ab592f945a7d5"  # clean_not_in_scope_v1, H1b
HISTORY_ONLY_ID = "da11041fd5394d758b1a60bfb5078ff5"  # in 86 history layer, outside formal pool


def make_report(
    *,
    report_id: str = "rep-test",
    symbol: str = "600519.SH",
    status: str = "completed",
    analysis_status: Optional[str] = "VALID",
    trade_action: Optional[str] = "BUY",
    winner: str = "bull",
    protocol_version: str = PROTOCOL_VERSION_V2_STRUCTURED,
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    rep: dict[str, Any] = {
        "id": report_id,
        "symbol": symbol,
        "trade_date": "2026-08-01",
        "status": status,
        "protocol_version": protocol_version,
        "manager_verdict": {"winner": winner, "direction": "看多"},
        "claims": [
            {
                "claim_id": "c1",
                "speaker": "Bull Researcher",
                "stance": "bullish",
                "status": "verified",
                "claim": "Bull argument",
            }
        ],
    }
    if analysis_status is not None:
        rep["analysis_status"] = analysis_status
    if trade_action is not None:
        rep["trade_action"] = trade_action
    if extra_fields:
        rep.update(extra_fields)
    return rep


# ── Manifest structure ────────────────────────────────────────────────────────


class TestManifestStructure:
    def test_manifest_loads_and_versioned(self):
        m = load_manifest()
        assert m["manifest_version"] == MANIFEST_VERSION

    def test_history_layer_keeps_full_86(self):
        m = load_manifest()
        assert len(m["history_confirmed_v1"]) == 86

    def test_formal_pool_reproduces_15_18_18(self):
        pool = load_manifest()["formal_pool_v1"]
        assert len(pool["confirmed_contaminated_v1"]) == 15
        assert len(pool["pending_review_v1"]) == 18
        assert len(pool["clean_not_in_scope_v1"]) == 18

    def test_anchor_b188060f_confirmed(self):
        pool = load_manifest()["formal_pool_v1"]
        conf_ids = {e["report_id"] for e in pool["confirmed_contaminated_v1"]}
        assert ANCHOR_ID in conf_ids
        entry = manifest_entry(ANCHOR_ID)
        assert entry is not None
        assert entry["adjudication_status"] == "confirmed"
        assert entry["reason_code"] == "CONFIRMED_RAW_IN_QFQ"
        assert entry["manifest_version"] == MANIFEST_VERSION
        assert entry["evidence_ref"]

    def test_confirmed_pool_is_subset_of_history(self):
        m = load_manifest()
        hist_ids = {e["report_id"] for e in m["history_confirmed_v1"]}
        conf_ids = {e["report_id"] for e in m["formal_pool_v1"]["confirmed_contaminated_v1"]}
        assert conf_ids <= hist_ids

    def test_pool_layers_disjoint_and_conserve_51(self):
        pool = load_manifest()["formal_pool_v1"]
        conf = {e["report_id"] for e in pool["confirmed_contaminated_v1"]}
        pend = {e["report_id"] for e in pool["pending_review_v1"]}
        clean = {e["report_id"] for e in pool["clean_not_in_scope_v1"]}
        assert not (conf & pend) and not (conf & clean) and not (pend & clean)
        assert len(conf | pend | clean) == 51

    def test_pending_entries_carry_qfq_cooc_flag(self):
        pool = load_manifest()["formal_pool_v1"]
        entries = {e["report_id"]: e for e in pool["pending_review_v1"]}
        assert all("qfq_cooc_same_ref" in e for e in entries.values())
        assert entries[PENDING_ID]["qfq_cooc_same_ref"] is True
        assert entries[PENDING_ID_LOW]["qfq_cooc_same_ref"] is False
        assert all(e["adjudication_status"] == "pending_review" for e in entries.values())

    def test_limitation_metadata_present(self):
        meta = load_manifest()["metadata"]
        text = " ".join(meta["limitations"])
        assert "259" in text and "208" in text  # honest semantics recorded
        assert meta["counts"]["history_confirmed_v1"] == 86
        assert meta["counts"]["formal_pool_total"] == 51

    def test_b2_b3_freeze_batch_identities_traceable(self):
        pool = load_manifest()["formal_pool_v1"]

        def batch(layer):
            b2 = [e for e in pool[layer] if e["pool"] and "B2" in e["pool"]]
            b3 = [e for e in pool[layer] if e["pool"] and "B3" in e["pool"]]
            return b2, b3

        b2c, b3c = batch("confirmed_contaminated_v1")
        b2p, b3p = batch("pending_review_v1")
        b2cl, b3cl = batch("clean_not_in_scope_v1")
        assert (len(b2c), len(b2p), len(b2cl)) == (2, 6, 4)  # B2 frozen batch
        assert (len(b3c), len(b3p), len(b3cl)) == (5, 2, 5)  # B3 frozen batch

    def test_h1b_only_counts_traceable(self):
        pool = load_manifest()["formal_pool_v1"]

        def h1b_only(layer):
            return [e for e in pool[layer] if e["pool"] == "H1b"]

        assert len(h1b_only("confirmed_contaminated_v1")) == 8
        assert len(h1b_only("pending_review_v1")) == 10
        assert len(h1b_only("clean_not_in_scope_v1")) == 9


# ── Classifier ────────────────────────────────────────────────────────────────


class TestClassifyPriceBasisExclusion:
    def test_confirmed_report_excluded(self):
        assert classify_price_basis_exclusion(make_report(report_id=ANCHOR_ID)) == REASON_CONTAMINATED

    def test_history_only_confirmed_excluded(self):
        # 86-layer confirmed outside the formal pool is still a hard exclusion
        assert classify_price_basis_exclusion(make_report(report_id=HISTORY_ONLY_ID)) == REASON_CONTAMINATED

    def test_pending_report_excluded_not_confirmed(self):
        reason = classify_price_basis_exclusion(make_report(report_id=PENDING_ID))
        assert reason == REASON_PENDING_REVIEW

    def test_clean_pool_report_eligible(self):
        assert classify_price_basis_exclusion(make_report(report_id=CLEAN_ID)) is None

    def test_unadjudicated_legacy_report_eligible(self):
        assert classify_price_basis_exclusion(make_report(report_id="legacy-unknown")) is None

    def test_new_contract_complete_eligible(self):
        rep = make_report(
            report_id="new-contract-ok",
            extra_fields={
                "price_ref_contract_version": "price_ref.v1",
                "price_basis_version": "price_basis.vendor_qfq",
            },
        )
        assert classify_price_basis_exclusion(rep) is None

    def test_new_contract_missing_pbv_fails_closed(self):
        rep = make_report(
            report_id="new-contract-bad-pbv",
            extra_fields={
                "price_ref_contract_version": "price_ref.v1",
                "price_basis_version": "price_basis.unspecified",
            },
        )
        assert classify_price_basis_exclusion(rep) == REASON_CONTRACT_INCOMPLETE

    def test_new_contract_wrong_contract_version_fails_closed(self):
        rep = make_report(
            report_id="new-contract-bad-prcv",
            extra_fields={
                "price_ref_contract_version": "price_ref.v0",
                "price_basis_version": "price_basis.vendor_qfq",
            },
        )
        assert classify_price_basis_exclusion(rep) == REASON_CONTRACT_INCOMPLETE

    def test_manifest_classification_precedes_contract_check(self):
        rep = make_report(
            report_id=ANCHOR_ID,
            extra_fields={
                "price_ref_contract_version": "price_ref.v1",
                "price_basis_version": "price_basis.vendor_qfq",
            },
        )
        assert classify_price_basis_exclusion(rep) == REASON_CONTAMINATED


# ── Pipeline stage wiring ─────────────────────────────────────────────────────


class TestFilterPipelineIsolation:
    def test_confirmed_and_pending_excluded_from_clean(self):
        reports = [
            make_report(report_id=ANCHOR_ID),
            make_report(report_id=PENDING_ID),
            make_report(report_id=CLEAN_ID),
            make_report(report_id="unlisted-1"),
        ]
        qualifying, excluded_counts, ledger = filter_v2_completed_reports(reports, return_ledger=True)
        qids = {q["id"] for q in qualifying}
        assert qids == {CLEAN_ID, "unlisted-1"}
        assert ledger["price_basis_contaminated"] == 1
        assert ledger["price_basis_pending_review"] == 1
        assert ledger["price_basis_isolated"] == 2
        assert ledger["clean_count"] == 2

    def test_isolation_reasons_not_in_d009_excluded_counts(self):
        reports = [
            make_report(report_id=ANCHOR_ID),
            make_report(report_id=PENDING_ID),
            make_report(report_id="clean-x", analysis_status="ABSTAIN", trade_action="NO_TRADE"),
            make_report(report_id="ok-1"),
        ]
        qualifying, excluded_counts, ledger = filter_v2_completed_reports(reports, return_ledger=True)
        # D-009 excluded_counts stays pure: only 'abstain'
        assert excluded_counts == {
            "legacy_null": 0,
            "abstain": 1,
            "invalid_run": 0,
            "data_error": 0,
            "no_trade": 0,
            "wait": 0,
        }
        for key in ("price_basis_contaminated", "price_basis_pending_review", "price_basis_contract_incomplete"):
            assert key not in excluded_counts

    def test_ledger_quantity_conservation(self):
        reports = [
            {"id": "non-v2", "status": "completed", "protocol_version": "v1_legacy"},
            make_report(report_id=ANCHOR_ID),
            make_report(report_id=PENDING_ID),
            make_report(report_id="d009-wait", analysis_status="VALID", trade_action="WAIT"),
            make_report(report_id=CLEAN_ID),
            make_report(report_id="unlisted-2"),
            make_report(
                report_id="new-bad",
                extra_fields={"price_ref_contract_version": "price_ref.v1", "price_basis_version": "price_basis.unspecified"},
            ),
        ]
        qualifying, excluded_counts, ledger = filter_v2_completed_reports(reports, return_ledger=True)
        assert ledger["raw_count"] == ledger["non_v2_excluded"] + ledger["qualifying_v2_count"]
        assert ledger["qualifying_v2_count"] == ledger["eligible_count"] + ledger["d009_excluded"]
        assert ledger["eligible_count"] == ledger["clean_count"] + ledger["price_basis_isolated"]
        assert ledger["price_basis_isolated"] == (
            ledger["price_basis_contaminated"]
            + ledger["price_basis_pending_review"]
            + ledger["price_basis_contract_incomplete"]
        )
        assert ledger["clean_count"] == len(qualifying)
        assert ledger["price_basis_contract_incomplete"] == 1

    def test_anchor_never_enters_clean_denominator(self):
        reports = [make_report(report_id=ANCHOR_ID)] + [make_report(report_id=f"ok-{i}") for i in range(3)]
        qualifying = filter_v2_completed_reports(reports)
        assert ANCHOR_ID not in {q["id"] for q in qualifying}
        assert len(qualifying) == 3
