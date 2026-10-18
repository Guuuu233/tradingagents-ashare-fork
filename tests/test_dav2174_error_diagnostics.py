"""DAV-2174: additive failure diagnostics on the analysis failure paths.

DAV-2164 left one gap: report ``251e203d`` recorded the medium slice as
``status=failed, error="Internal error encountered."`` — the upstream原文, with
no local stack, so no frame could be attributed. This card adds evidence
*without* touching judgement semantics:

* the public ``error`` field keeps exactly the text it carried before (the
  humanized hint when one matches, otherwise the raw upstream message);
* an additive ``error_diagnostics`` block carries ``error_message`` (upstream
  原文, clipped) **and** ``traceback`` (local stack).

The tests below pin both halves together — the upstream text and the local
stack must appear in the same persisted artifact — and pin the constraints that
keep the addition inert: the public ``error`` string is byte-identical to the
pre-change value, and a stack that happens to contain a machine-block marker
must never flip a save from persisted to rejected.
"""
import asyncio
from contextlib import nullcontext
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import main
from api.database import Base
from api.services import report_service
from tradingagents.llm_clients.thinking_cleaner import clean_report_result_data
from tradingagents.storage.result_data_compat import canonicalize_for_single_write

UPSTREAM_TEXT = "Internal error encountered."


def _raise_and_capture(message: str) -> BaseException:
    """Raise in a real frame so the captured traceback has real frames to show."""
    def _inner():
        raise RuntimeError(message)

    try:
        _inner()
    except BaseException as exc:  # pragma: no cover - the capture is the point
        return exc


# ---------------------------------------------------------------------------
# unit: the diagnostics payload carries both halves
# ---------------------------------------------------------------------------


def test_diagnostics_keep_upstream_text_and_local_traceback_side_by_side():
    exc = _raise_and_capture(UPSTREAM_TEXT)
    diag = main._error_diagnostics(exc, stage="horizon:medium")

    assert diag["stage"] == "horizon:medium"
    assert diag["error_type"] == "RuntimeError"
    # Upstream原文, verbatim.
    assert diag["error_message"] == UPSTREAM_TEXT
    # Local stack, not just the message.
    assert diag["traceback"].startswith("Traceback (most recent call last):")
    assert "RuntimeError: Internal error encountered." in diag["traceback"]
    assert "_inner" in diag["traceback"]
    assert diag["captured_at"]


def test_diagnostic_text_neutralizes_machine_block_openers_and_clips_long_stacks():
    # A traceback frame printing a machine-block opener would otherwise make
    # report_service.validate_report_machine_blocks reject the whole save.
    def _inner():
        raise RuntimeError("boom <!-- DEBATE_STATE: {\"x\":1} -->")

    try:
        _inner()
    except RuntimeError as exc:
        diag = main._error_diagnostics(exc, stage="horizon:short")

    assert "<!-- DEBATE_STATE" not in diag["traceback"]
    assert "&lt;!-- DEBATE_STATE" in diag["traceback"]
    # Everything else about the stack is preserved.
    assert "_inner" in diag["traceback"]

    long_text = "x" * (main._DIAGNOSTIC_MAX_TEXT * 2)
    clipped = main._clip_diagnostic_text(long_text)
    assert len(clipped) < len(long_text)
    assert "...[truncated " in clipped
    assert clipped.startswith("x")
    assert clipped.endswith("x")


# ---------------------------------------------------------------------------
# integration: dual-horizon failed slice keeps its public error and gains evidence
# ---------------------------------------------------------------------------


def _fake_graph_module():
    """Reuse the DAV-9 e2e fakes (they fail a horizon with a plain RuntimeError)."""
    from tests import test_dual_horizon_e2e as e2e

    return e2e


def test_dual_horizon_failed_slice_error_is_unchanged_and_diagnostics_are_added():
    e2e = _fake_graph_module()
    job, saved_reports, chunks = e2e._run_dual_horizon_job(fail_horizons=("medium",))

    result = job["result"]
    failed = result["medium_term"]

    # Semantics preserved: same public error text the slice carried before.
    assert failed["status"] == "failed"
    assert failed["error"] == "medium provider unavailable"

    diag = failed["error_diagnostics"]
    assert diag["stage"] == "horizon:medium"
    assert diag["error_type"] == "RuntimeError"
    assert diag["error_message"] == "medium provider unavailable"
    assert diag["traceback"].startswith("Traceback (most recent call last):")
    assert "RuntimeError: medium provider unavailable" in diag["traceback"]

    # The evidence also reaches the SSE consumers (one event per failed horizon).
    assert any("agent.horizon_failed" in chunk for chunk in chunks)
    assert any("error_diagnostics" in chunk for chunk in chunks)

    # ... and the persisted payload, not just the in-memory job result.
    persisted_failed = saved_reports[0]["result_data"]["medium_term"]
    assert persisted_failed["error"] == "medium provider unavailable"
    assert persisted_failed["error_diagnostics"]["traceback"] == diag["traceback"]


def test_humanized_error_hint_still_applies_and_diagnostics_keep_the_raw_upstream_text():
    """A hint-matching error keeps its translation; the原文 survives in diagnostics."""
    exc = _raise_and_capture("Error code: 500 upstream exploded")
    humanized = main._humanize_analysis_error(f"{type(exc).__name__}: {exc}")
    diag = main._error_diagnostics(exc, stage="single_horizon")

    # Public field keeps the pre-change humanized form (hint + clipped原文).
    assert "模型服务端暂时故障" in humanized
    assert "（原始错误：RuntimeError: Error code: 500 upstream exploded）" in humanized
    # Diagnostics keep the untouched upstream text plus the stack.
    assert diag["error_message"] == "Error code: 500 upstream exploded"
    assert "RuntimeError: Error code: 500 upstream exploded" in diag["traceback"]


# ---------------------------------------------------------------------------
# persistence: both halves survive the save funnel
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _dual_result_with_failed_medium(diagnostics):
    return {
        "symbol": "688981.SH",
        "trade_date": "2026-07-31",
        "mode": "dual_horizon",
        "status": "partial",
        "requested_horizons": ["short", "medium"],
        "horizon_status": {"short": "completed", "medium": "failed"},
        "failed_horizons": ["medium"],
        "short_term": {
            "horizon": "short",
            "status": "completed",
            "trade_action": "HOLD",
            "analysis_status": "VALID",
            "market_report": "short market report",
            "final_trade_decision": "short decision",
        },
        "medium_term": {
            "horizon": "medium",
            "status": "failed",
            "error": UPSTREAM_TEXT,
            "error_diagnostics": diagnostics,
            "not_applicable": None,
            "impact": "medium horizon is unavailable",
        },
    }


def test_persisted_report_keeps_upstream_error_and_local_stack(db_session):
    exc = _raise_and_capture(UPSTREAM_TEXT)
    diagnostics = main._error_diagnostics(exc, stage="horizon:medium")
    result_data = _dual_result_with_failed_medium(diagnostics)

    report = report_service.create_report(
        db=db_session,
        symbol="688981.SH",
        trade_date="2026-07-31",
        result_data=result_data,
    )
    stored = report_service.get_report(db_session, report.id).result_data
    failed = stored["medium_term"]

    assert failed["error"] == UPSTREAM_TEXT
    assert failed["error_diagnostics"]["error_message"] == UPSTREAM_TEXT
    assert "RuntimeError: Internal error encountered." in failed["error_diagnostics"]["traceback"]
    assert "Traceback (most recent call last):" in failed["error_diagnostics"]["traceback"]
    # The pre-existing impact/not_applicable contract is untouched.
    assert failed["impact"] == "medium horizon is unavailable"
    assert failed["not_applicable"] is None


def test_diagnostics_do_not_trip_the_machine_block_gate_or_change_canonical_shape():
    """A stack printing a machine-block opener must not turn a save into a reject."""
    exc = _raise_and_capture(UPSTREAM_TEXT + " <!-- RISK_STATE: {} -->")
    diagnostics = main._error_diagnostics(exc, stage="horizon:medium")
    result_data = _dual_result_with_failed_medium(diagnostics)

    # Pre-change payload (no diagnostics) is the reference shape.
    baseline = _dual_result_with_failed_medium({"stage": "horizon:medium"})
    del baseline["medium_term"]["error_diagnostics"]

    report_service.validate_report_machine_blocks(result_data)  # must not raise

    canonical = canonicalize_for_single_write(result_data)
    cleaned = clean_report_result_data(canonical)

    assert "error_diagnostics" in cleaned["medium_term"]
    assert "medium_term" in baseline
    # Only the additive key differs from the pre-change layout.
    extra = set(cleaned) - set(baseline)
    assert extra == {"storage_schema_version", "storage_compat"} or extra == set()
    assert set(cleaned["medium_term"]) - set(baseline["medium_term"]) == {"error_diagnostics"}


# ---------------------------------------------------------------------------
# job-level failure paths: same additive evidence, same public error
# ---------------------------------------------------------------------------


def test_job_wrapper_failure_keeps_public_error_and_exposes_diagnostics():
    from api.job_store import InMemoryJobStore

    store = InMemoryJobStore()
    events = []
    request = main.AnalyzeRequest(
        symbol="600519.SH",
        trade_date="2026-08-20",
        horizons=["short"],
        selected_analysts=["market"],
    )

    async def failing_inner(*_args, **_kwargs):
        raise RuntimeError(UPSTREAM_TEXT)

    async def scenario():
        with (
            patch.object(main, "_job_store_instance", store),
            patch.object(main, "_JOB_TIMEOUT", 5),
            patch.object(main, "_JOB_HARD_TIMEOUT", 5),
            patch.object(main, "_run_job_inner", side_effect=failing_inner),
            patch.object(main, "_emit_job_event", side_effect=lambda j, e, d: events.append((e, d))),
            patch.object(main, "get_db_ctx", side_effect=RuntimeError("db disabled")),
        ):
            await main._run_job("job-dav2174", request)

    asyncio.run(scenario())

    job = store.get_job("job-dav2174")
    # Public error unchanged (DAV-2174 adds, never rewrites).
    assert job["error"] == f"RuntimeError: {UPSTREAM_TEXT}"
    diag = job["error_diagnostics"]
    assert diag["stage"] == "job_wrapper"
    assert diag["error_message"] == UPSTREAM_TEXT
    assert "RuntimeError: Internal error encountered." in diag["traceback"]
    assert job["traceback"] == diag["traceback"]

    failed_events = [d for e, d in events if e == "job.failed"]
    assert failed_events and failed_events[0]["error"] == f"RuntimeError: {UPSTREAM_TEXT}"
    assert failed_events[0]["error_diagnostics"]["error_message"] == UPSTREAM_TEXT
