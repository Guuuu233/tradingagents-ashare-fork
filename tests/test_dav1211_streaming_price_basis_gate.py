"""Tests for DAV-1211: streaming /v1/analyze must run price_ref audit + hard gate.

Contract Requirements (all exercised through ``api.main._run_job_inner`` with
``stream_events=True`` — the production ``POST /v1/analyze`` shape):

1. Ordinary streaming run: audit/gate are invoked on the accumulated
   final_state, and the persisted result carries
   ``price_ref_contract_version == "price_ref.v1"`` plus the five
   price_ref side-channel keys (non-null; empty list/dict is legal, null is not).
2. Clean same-basis run: gate passes, real ``price_basis.vendor_qfq`` version
   is persisted.
3. raw→qfq executable target shape (b188060f pattern): gate blocks, the final
   persisted decision/trade_action stay NO_TRADE — structured extraction /
   post-processing must not resurrect BUY/SELL, and raw-priced levels must not
   remain executable.
4. Audit error: audit_error → gate blocked → NO_TRADE; no crash, no fail-open.
5. Streaming and non-streaming payloads expose the same price-ref key set.
6. Production smoke regression: an ``evidence_contract.v2`` run that used to
   persist null markers now persists the full contract marker set.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from api.job_store import InMemoryJobStore
from api import main
from api.services import report_service
from tradingagents.agents.utils.price_basis_gate import (
    PRICE_BASIS_VERSION_UNSPECIFIED,
    PRICE_BASIS_VERSION_VENDOR_QFQ,
    PRICE_REF_CONTRACT_VERSION,
)
from tradingagents.agents.utils.price_basis_gate import finalize_price_ref_state
from tradingagents.graph.trading_graph import TradingAgentsGraph

PRICE_REF_KEYS = (
    "price_refs",
    "price_basis_gaps",
    "price_basis_validation",
    "price_basis_gate",
    "price_ref_contract_version",
    "price_basis_version",
)


class _FakePropagator:
    def create_initial_state(self, *args, **kwargs):
        return {
            "company_of_interest": "600519.SH",
            "trade_date": "2026-08-20",
            "horizon": "short",
        }

    def get_graph_args(self):
        return {}


class _FakeGraphStream:
    """Yields chunks that accumulate into a configurable final_state."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, init_state, **_kwargs):
        for chunk in self._chunks:
            yield chunk


def _valid_decision_status(trade_action: str = "BUY") -> dict:
    return {
        "analysis_status": "VALID",
        "direction": "UP",
        "trade_action": trade_action,
        "risk_status": "OK",
        "confirmation_state": "CONFIRMED",
        "reason_codes": [],
        "failed_checks": [],
    }


def _clean_qfq_chunks() -> list[dict]:
    """Clean same-basis run: technical qfq prices, executable target backed by
    a vendor_qfq back-reference."""
    return [
        {
            "market_report": "现价 1700.00 元，支撑位 1650.00 元。",
            "final_trade_decision": (
                "买入。目标价 1700.00，止损 1650.00。"
                "<!-- VERDICT: {\"decision\": \"BUY\"} -->"
            ),
            "decision_status": _valid_decision_status("BUY"),
            "analysis_status": "VALID",
            "trade_action": "BUY",
        },
    ]


def _raw_target_chunks() -> list[dict]:
    """b188060f shape: raw block-trade price reused as executable qfq target."""
    return [
        {
            "news_report": "大宗交易成交价 1268.73 元。",
            "final_trade_decision": (
                "卖出。目标价 1268.73，止损 1238.00。"
                "<!-- VERDICT: {\"decision\": \"SELL\"} -->"
            ),
            "decision_status": _valid_decision_status("SELL"),
            "analysis_status": "VALID",
            "trade_action": "SELL",
        },
    ]


def _fake_graph(chunks) -> SimpleNamespace:
    graph = SimpleNamespace()
    graph.data_collector = MagicMock()
    graph.data_collector.collect.return_value = {}
    graph.propagator = _FakePropagator()
    graph.graph = _FakeGraphStream(chunks)
    graph.role_resolved_configs = {}
    graph.config = {}
    graph.process_signal = lambda decision: (
        "SELL" if "SELL" in str(decision).upper()
        else "BUY" if "BUY" in str(decision).upper() or "买入" in str(decision)
        else "HOLD"
    )
    return graph


def _request() -> main.AnalyzeRequest:
    return main.AnalyzeRequest(
        symbol="600519.SH",
        trade_date="2026-08-20",
        horizons=["short"],
        selected_analysts=["market"],
    )


def _run_streaming_job(chunks, extra_patches=()):
    """Drive _run_job_inner(stream_events=True) with fakes; return (store,
    job_id, saved_report_kwargs, audit_calls, gate_calls)."""
    store = InMemoryJobStore()
    events: list[tuple[str, dict]] = []
    saved: list[dict] = []
    job_id = f"job-{uuid4().hex}"
    db = MagicMock()

    def capture_event(j_id: str, event: str, data: dict) -> None:
        events.append((event, data))
        store.emit_event(j_id, event, data)

    finalize_calls: list[dict] = []

    def spy_finalize(state, market_source=None):
        finalize_calls.append(state)
        return finalize_price_ref_state(state, market_source)

    patches = [
        patch.object(main, "_job_store_instance", store),
        patch.object(main, "TradingAgentsGraph", return_value=_fake_graph(chunks)),
        patch.object(main, "_resolve_and_freeze_custom_prompts", return_value=({}, False)),
        patch.object(main, "get_db_ctx", return_value=nullcontext(db)),
        patch.object(report_service, "init_report"),
        patch.object(report_service, "update_report_partial"),
        patch.object(report_service, "extract_structured_data", return_value=None),
        patch.object(report_service, "create_report", side_effect=lambda **kw: saved.append(kw)),
        patch.object(report_service, "mark_report_failed"),
        patch.object(main, "_emit_job_event", side_effect=capture_event),
        patch.object(main, "finalize_price_ref_state", side_effect=spy_finalize),
        *extra_patches,
    ]

    async def scenario():
        with patches[0]:
            for p in patches[1:]:
                p.start()
            try:
                await main._run_job_inner(
                    job_id, _request(), stream_events=True, save_report=True
                )
            finally:
                for p in reversed(patches[1:]):
                    p.stop()

    asyncio.run(scenario())
    return store, job_id, saved, events, finalize_calls


# ── Contract 1+6: ordinary streaming run persists the full price-ref contract ─


def test_streaming_analyze_invokes_audit_and_gate_and_persists_markers():
    """Production smoke 95e42f65 regression: the streaming path must run the
    same audit+gate as propagate(), and every price-ref side-channel field
    must be persisted non-null."""
    store, job_id, saved, _events, finalize_calls = _run_streaming_job(
        _clean_qfq_chunks()
    )

    assert store.get_job(job_id)["status"] == "completed"
    assert len(finalize_calls) == 1, (
        "shared price_ref finalization not invoked on streaming path"
    )
    # The single finalize call ran both audit and gate on the final_state.
    state = finalize_calls[0]
    assert "price_refs" in state and "price_basis_gate" in state

    assert len(saved) == 1
    result_data = saved[0]["result_data"]
    assert result_data["price_ref_contract_version"] == PRICE_REF_CONTRACT_VERSION
    for key in PRICE_REF_KEYS:
        assert key in result_data, f"missing persisted key: {key}"
        assert result_data[key] is not None, f"persisted {key} is null"


# ── Contract 2: clean same-basis streaming run passes the gate ────────────────


def test_streaming_clean_same_basis_passes_gate_and_persists_vendor_qfq():
    _store, job_id, saved, _e, _f = _run_streaming_job(_clean_qfq_chunks())
    assert len(saved) == 1
    result_data = saved[0]["result_data"]
    assert result_data["price_basis_gate"]["status"] == "pass"
    assert result_data["price_basis_version"] == PRICE_BASIS_VERSION_VENDOR_QFQ
    assert result_data["trade_action"] == "BUY"


# ── Contract 3: raw→qfq executable target fails closed end-to-end ─────────────


def test_streaming_raw_basis_executable_target_blocked_no_trade():
    _store, _job_id, saved, _e, _f = _run_streaming_job(_raw_target_chunks())
    assert len(saved) == 1
    kw = saved[0]
    result_data = kw["result_data"]

    gate = result_data["price_basis_gate"]
    assert gate["status"] == "blocked"
    assert result_data["price_basis_version"] == PRICE_BASIS_VERSION_UNSPECIFIED

    # End-to-end fail-close: neither the persisted row decision nor the
    # result_data action may stay directional.
    assert kw["decision"] == "NO_TRADE"
    assert result_data["decision"] == "NO_TRADE"
    assert result_data["trade_action"] == "NO_TRADE"
    ds = result_data["decision_status"]
    assert ds["trade_action"] == "NO_TRADE"

    # An unbacked/wrong-basis level must not persist as an executable price.
    assert result_data["target_price"] is None
    assert result_data["stop_loss_price"] is None


# ── Contract 4: audit error fails closed, never crashes the job ───────────────


def test_streaming_audit_error_blocks_gate_no_fail_open():
    with patch(
        "tradingagents.agents.utils.price_ref_registry.build_price_ref_registry",
        side_effect=RuntimeError("registry boom"),
    ):
        store, job_id, saved, _e, _f = _run_streaming_job(_clean_qfq_chunks())

    # No crash: job still completes, but the run is fail-closed.
    assert store.get_job(job_id)["status"] == "completed"
    assert len(saved) == 1
    result_data = saved[0]["result_data"]
    assert result_data["price_basis_validation"]["status"] == "audit_error"
    assert result_data["price_basis_gate"]["status"] == "blocked"
    assert result_data["trade_action"] == "NO_TRADE"
    assert saved[0]["decision"] == "NO_TRADE"


# ── Contract 5: streaming payload price-ref key set matches dual-horizon ──────


def test_streaming_price_ref_key_set_matches_horizon_result():
    _store, _job_id, saved, _e, _f = _run_streaming_job(_clean_qfq_chunks())
    stream_result = saved[0]["result_data"]

    # Re-run the same post-gate state through the dual-horizon horizon-result
    # builder and compare the price-ref key sets.
    state = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
        "market_report": "现价 1700.00 元。",
        "final_trade_decision": "买入。目标价 1700.00。",
    }
    finalize_price_ref_state(state)

    stub_self = SimpleNamespace(game_theory_wired=False, game_theory_unavailable=None)
    horizon_result = TradingAgentsGraph._build_horizon_result(
        stub_self, "short", state
    )
    ordinary_payload = main._build_result_payload(state)

    for key in PRICE_REF_KEYS:
        assert key in stream_result
        assert key in horizon_result
        assert key in ordinary_payload
    assert (
        stream_result["price_ref_contract_version"]
        == horizon_result["price_ref_contract_version"]
        == ordinary_payload["price_ref_contract_version"]
        == PRICE_REF_CONTRACT_VERSION
    )
