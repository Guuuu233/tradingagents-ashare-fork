"""Tests for DAV-1207: ordinary /v1/analyze price_ref.v1 persistence + contract-era Stage 4.

Contract Requirements:
1. api.main._build_result_payload must persist price_refs / price_basis_gaps /
   price_basis_validation / price_basis_gate / price_ref_contract_version, plus
   the gate-written real price_basis_version — report_service's
   ``price_basis.unspecified`` backfill stays only as a last-resort fallback
   when the gate never stamped the field.
2. classify_price_basis_exclusion is contract-era aware: a persisted
   ``evidence_contract_version == evidence_contract.v2`` sample missing
   ``price_ref_contract_version`` is ``price_basis_contract_incomplete``, NOT
   legacy — v2 and price_ref.v1 ship in the same deployed contract stack, so
   "v2 present + marker absent" is itself evidence of a broken write path.
   True legacy samples (neither marker) stay manifest-only.
3. Ordinary and dual-horizon payloads expose the same price-ref key set with
   identical value semantics.
4. No historical report fields are rewritten; all fixtures are in-memory.
"""

from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from api.main import _build_result_payload
from api.services import report_service
from tradingagents.agents.utils.price_basis_gate import (
    PRICE_BASIS_VERSION_UNSPECIFIED,
    PRICE_BASIS_VERSION_VENDOR_QFQ,
    PRICE_REF_CONTRACT_VERSION,
)
from tradingagents.agents.utils.price_basis_isolation import (
    REASON_CONTRACT_INCOMPLETE,
    classify_price_basis_exclusion,
)

PRICE_REF_KEYS = (
    "price_refs",
    "price_basis_gaps",
    "price_basis_validation",
    "price_basis_gate",
    "price_ref_contract_version",
    "price_basis_version",
)


def _gate_payload(status: str = "pass") -> dict:
    return {
        "contract_version": PRICE_REF_CONTRACT_VERSION,
        "status": status,
        "violations": [],
        "allowed_dual_display": [],
        "decision_driving_ref_count": 1,
        "price_basis_version": (
            PRICE_BASIS_VERSION_VENDOR_QFQ if status == "pass" else PRICE_BASIS_VERSION_UNSPECIFIED
        ),
    }


def _ordinary_final_state(**overrides) -> dict:
    """Minimal post-gate final_state as propagate() hands it to the API layer."""
    state = {
        "company_of_interest": "600519.SH",
        "horizon": "short",
        "trade_date": "2026-09-22",
        "final_trade_decision": "观望",
        "price_refs": [
            {"ref_id": "r1", "source": "close", "value": 1700.0, "basis": "vendor_qfq"}
        ],
        "price_basis_gaps": [
            {"kind": "price_basis_gate_violation", "ref_id": None, "source": None, "detail": "x"}
        ],
        "price_basis_validation": {"status": "checked", "ref_count": 1},
        "price_basis_gate": _gate_payload("blocked"),
        "price_ref_contract_version": PRICE_REF_CONTRACT_VERSION,
        "price_basis_version": PRICE_BASIS_VERSION_UNSPECIFIED,
    }
    state.update(overrides)
    return state


# ── Contract 1: ordinary-path field pass-through ──────────────────────────────


class TestOrdinaryPayloadPriceRefPersistence:
    def test_payload_persists_all_price_ref_fields(self):
        state = _ordinary_final_state()
        payload = _build_result_payload(state)
        for key in PRICE_REF_KEYS:
            assert key in payload, f"missing persisted key: {key}"
            assert payload[key] == state[key], f"value mismatch for {key}"

    def test_payload_preserves_gate_price_basis_version_not_masked(self):
        """Gate-passed vendor_qfq must survive ensure_report_cohort_persisted —
        the unspecified backfill must not overwrite real gate output."""
        state = _ordinary_final_state(
            price_basis_gate=_gate_payload("pass"),
            price_basis_version=PRICE_BASIS_VERSION_VENDOR_QFQ,
        )
        payload = _build_result_payload(state)
        assert payload["price_basis_version"] == PRICE_BASIS_VERSION_VENDOR_QFQ
        persisted = report_service.ensure_report_cohort_persisted(payload)
        assert persisted["price_basis_version"] == PRICE_BASIS_VERSION_VENDOR_QFQ

    def test_missing_pbv_leaves_key_absent_for_last_resort_backfill(self):
        """When the gate never stamped a pbv, the key stays absent so
        report_service's `unspecified` fallback still applies."""
        state = _ordinary_final_state()
        del state["price_basis_version"]
        payload = _build_result_payload(state)
        assert "price_basis_version" not in payload
        persisted = report_service.ensure_report_cohort_persisted(payload)
        assert persisted["price_basis_version"] == PRICE_BASIS_VERSION_UNSPECIFIED

    def test_production_regression_shape(self):
        """Reproduce report d2959cbacb5b4887a725e0590a0b75f3: gate ran (fields in
        final_state) but the old payload dropped them. After the fix the full
        contract set is persisted."""
        payload = _build_result_payload(_ordinary_final_state())
        persisted = report_service.ensure_report_cohort_persisted(payload)
        assert persisted["price_ref_contract_version"] == PRICE_REF_CONTRACT_VERSION
        assert persisted["price_refs"] is not None
        assert persisted["price_basis_gaps"] is not None
        assert persisted["price_basis_validation"] is not None
        assert persisted["price_basis_gate"] is not None
        assert persisted["price_basis_version"] == PRICE_BASIS_VERSION_UNSPECIFIED


# ── Contract 2: contract-era aware classification ─────────────────────────────


def _report(**fields) -> dict:
    rep = {"id": "rep-dav1207", "status": "completed"}
    rep.update(fields)
    return rep


class TestContractEraClassification:
    def test_v2_era_missing_price_ref_marker_fails_closed(self):
        """The production bug shape: ecv=v2 persisted, price-ref fields dropped
        by the ordinary path → must be contract_incomplete, not legacy."""
        rep = _report(
            evidence_contract_version="evidence_contract.v2",
            price_basis_version=PRICE_BASIS_VERSION_UNSPECIFIED,
            generated_by_commit_sha="96f14eb073003988513a92e02282b5457c591385",
        )
        assert classify_price_basis_exclusion(rep) == REASON_CONTRACT_INCOMPLETE

    def test_v2_era_marker_nested_in_result_data_fails_closed(self):
        rep = _report(
            result_data={
                "evidence_contract_version": "evidence_contract.v2",
                "price_basis_version": PRICE_BASIS_VERSION_UNSPECIFIED,
            }
        )
        assert classify_price_basis_exclusion(rep) == REASON_CONTRACT_INCOMPLETE

    def test_v2_era_full_contract_eligible(self):
        rep = _report(
            evidence_contract_version="evidence_contract.v2",
            price_ref_contract_version=PRICE_REF_CONTRACT_VERSION,
            price_basis_version=PRICE_BASIS_VERSION_VENDOR_QFQ,
        )
        assert classify_price_basis_exclusion(rep) is None

    def test_v2_era_marker_present_but_wrong_pbv_fails_closed(self):
        rep = _report(
            evidence_contract_version="evidence_contract.v2",
            price_ref_contract_version=PRICE_REF_CONTRACT_VERSION,
            price_basis_version=PRICE_BASIS_VERSION_UNSPECIFIED,
        )
        assert classify_price_basis_exclusion(rep) == REASON_CONTRACT_INCOMPLETE

    def test_true_legacy_without_v2_stays_manifest_only(self):
        rep = _report(generated_by_commit_sha="96f14eb073003988513a92e02282b5457c591385")
        assert classify_price_basis_exclusion(rep) is None

    def test_pre_v2_evidence_contract_stays_manifest_only(self):
        rep = _report(evidence_contract_version="evidence_contract.v1")
        assert classify_price_basis_exclusion(rep) is None


# ── Contract 3: ordinary / dual-horizon key-set isomorphism ───────────────────


def _make_mock_graph():
    with patch("tradingagents.graph.trading_graph.create_llm_client"), \
         patch("tradingagents.graph.trading_graph.FinancialSituationMemory"), \
         patch("tradingagents.graph.trading_graph.GraphSetup"), \
         patch("tradingagents.graph.trading_graph.ConditionalLogic"), \
         patch("tradingagents.graph.trading_graph.Propagator"), \
         patch("tradingagents.graph.trading_graph.Reflector"), \
         patch("tradingagents.graph.trading_graph.SignalProcessor"), \
         patch("tradingagents.graph.trading_graph.set_config"):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.graph.propagation import Propagator
        from tradingagents.graph.data_collector import DataCollector
        ta = TradingAgentsGraph.__new__(TradingAgentsGraph)
        ta.debug = False
        ta.config = {}
        ta.callbacks = []
        ta.ticker = None
        ta.log_states_dict = {}
        ta.quick_thinking_llm = MagicMock()
        ta.data_collector = DataCollector()
        ta.propagator = Propagator()
        ta.graph = MagicMock()
        ta.signal_processor = MagicMock()
        return ta


class TestOrdinaryDualHorizonIsomorphism:
    def test_same_price_ref_key_set_and_semantics(self):
        state = _ordinary_final_state(
            price_basis_gate=_gate_payload("pass"),
            price_basis_version=PRICE_BASIS_VERSION_VENDOR_QFQ,
        )
        horizon_result = _make_mock_graph()._build_horizon_result("short", deepcopy(state))
        ordinary_payload = _build_result_payload(deepcopy(state))
        # Same price-ref key set on both paths.
        for key in PRICE_REF_KEYS:
            assert key in horizon_result, f"dual-horizon missing {key}"
            assert key in ordinary_payload, f"ordinary missing {key}"
        # Contract markers carry identical semantics on both paths.  (The
        # horizon builder re-runs audit+gate internally, so collection fields
        # are re-derived there; identity of those values is asserted in
        # TestOrdinaryPayloadPriceRefPersistence against the gate's own
        # output.)
        assert (
            ordinary_payload["price_ref_contract_version"]
            == horizon_result["price_ref_contract_version"]
            == PRICE_REF_CONTRACT_VERSION
        )
        assert ordinary_payload["price_basis_version"] == horizon_result["price_basis_version"] == PRICE_BASIS_VERSION_VENDOR_QFQ
        assert isinstance(horizon_result["price_basis_gate"], dict)
        assert isinstance(ordinary_payload["price_basis_gate"], dict)
