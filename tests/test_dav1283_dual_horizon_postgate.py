"""DAV-1283: dual-horizon (chat-entry) reports persist and read back the
primary horizon's POST-GATE decision fields, and the API exposes the
price-basis-gate downgrade signal for display.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import main
from api.database import Base, ReportDB
from api.job_store import InMemoryJobStore
from api.services import report_service


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _dual_result(
    *,
    short_action: str = "NO_TRADE",
    short_direction: str = "BEAR",
    gate_blocked: bool = True,
    top_action: str = "SELL",
):
    reason_codes = ["manager_terminal", "risk_verdict:pass"]
    failed_checks = []
    if gate_blocked:
        reason_codes = reason_codes + ["price_basis_gate_blocked"]
        failed_checks = ["price_basis_gate_blocked"]
    decision_status = {
        "analysis_status": "VALID",
        "direction": short_direction,
        "trade_action": short_action,
        "risk_status": "OK",
        "confirmation_state": "CONFIRMED",
        "reason_codes": reason_codes,
        "failed_checks": failed_checks,
    }
    gate = {
        "contract_version": "price_ref.v1",
        "status": "blocked" if gate_blocked else "pass",
        "violations": (
            [
                {
                    "kind": "decision_driving_unspecified_basis",
                    "ref_ids": ["pr-131"],
                    "source": "investment_plan",
                    "detail": "决策驱动价格 50.0(pr-131) basis 无法归因，禁止消费",
                }
            ]
            if gate_blocked
            else []
        ),
    }
    return {
        "mode": "dual_horizon",
        "status": "completed",
        # Top level still carries the pre-gate signal for historical rows.
        "decision": top_action,
        "trade_action": top_action,
        "direction": "BEAR",
        "confidence": 70,
        "probability": 0.7,
        "target_price": 52.0,
        "stop_loss_price": 55.5,
        "analysis_status": "VALID",
        "risk_status": "OK",
        "final_trade_decision": "最终交易建议：卖出\n...",
        "short_term": {
            "horizon": "short",
            "status": "completed",
            "trade_action": short_action,
            "analysis_status": "VALID",
            "risk_status": "OK",
            "decision_status": decision_status,
            "price_basis_gate": gate,
        },
        "medium_term": {"horizon": "medium", "status": "completed"},
    }


# ── D1: persistence writes post-gate values ──────────────────────────────────

def test_dual_horizon_persists_post_gate_no_trade_and_nulls_prices():
    db = _session()
    try:
        report = report_service.create_report(
            db=db,
            symbol="000657.SZ",
            trade_date="2026-09-24",
            decision="SELL",
            result_data=_dual_result(),
            confidence_override=70,
            target_price_override=52.0,
            stop_loss_override=55.5,
        )
        assert report.trade_action == "NO_TRADE"
        assert report.decision == "NO_TRADE"
        assert report.analysis_status == "VALID"
        assert report.direction == "看空"
        # Gate downgrade must not keep the extracted target/stop.
        assert report.confidence is None
        assert report.probability is None
        assert report.target_price is None
        assert report.stop_loss_price is None
    finally:
        db.close()


def test_dual_horizon_persists_post_gate_executable_values():
    db = _session()
    try:
        report = report_service.create_report(
            db=db,
            symbol="000657.SZ",
            trade_date="2026-09-24",
            decision="BUY",
            result_data=_dual_result(
                short_action="BUY", short_direction="BULL", gate_blocked=False, top_action="BUY"
            ),
            confidence_override=70,
            target_price_override=52.0,
            stop_loss_override=48.0,
        )
        assert report.trade_action == "BUY"
        assert report.decision == "BUY"
        assert report.analysis_status == "VALID"
        assert report.direction == "看多"
        assert report.confidence == 70
        assert report.target_price == 52.0
        assert report.stop_loss_price == 48.0
    finally:
        db.close()


def test_dual_horizon_without_post_gate_status_stays_unrecorded():
    db = _session()
    try:
        result_data = _dual_result()
        result_data["short_term"] = {"horizon": "short", "status": "completed"}
        result_data["medium_term"] = {"horizon": "medium", "status": "completed"}
        report = report_service.create_report(
            db=db,
            symbol="000657.SZ",
            trade_date="2026-09-24",
            decision="SELL",
            result_data=result_data,
        )
        # No post-gate record → 未记录, never the pre-gate caller decision.
        assert report.decision is None
        assert report.trade_action is None
        assert report.direction is None
        assert report.confidence is None
        assert report.target_price is None
        assert report.stop_loss_price is None
    finally:
        db.close()


def test_dual_horizon_update_existing_row_persists_post_gate_fields():
    """The update branch (report_id points at an init_report row) must apply
    the same post-gate persistence."""
    db = _session()
    try:
        report_id = uuid4().hex
        db.add(
            ReportDB(
                id=report_id,
                symbol="000657.SZ",
                trade_date="2026-09-24",
                status="pending",
            )
        )
        db.commit()
        report = report_service.create_report(
            db=db,
            symbol="000657.SZ",
            trade_date="2026-09-24",
            decision="SELL",
            result_data=_dual_result(),
            report_id=report_id,
        )
        assert report.id == report_id
        assert report.trade_action == "NO_TRADE"
        assert report.decision == "NO_TRADE"
        assert report.direction == "看空"
        assert report.target_price is None
        assert report.stop_loss_price is None
    finally:
        db.close()


# ── D2: read fallback exposes post-gate fields when DB columns are empty ─────

def _insert_legacy_dual_report(user_id: str) -> str:
    """Insert a report row the way historical chat-entry reports look:
    decision columns NULL, post-gate values only inside result_data."""
    from api.database import get_db_ctx

    report_id = uuid4().hex
    now = datetime.now(timezone.utc)
    with get_db_ctx() as db:
        db.add(
            ReportDB(
                id=report_id,
                user_id=user_id,
                symbol="000657.SZ",
                trade_date="2026-09-24",
                status="completed",
                decision=None,
                direction=None,
                confidence=None,
                probability=None,
                target_price=None,
                stop_loss_price=None,
                analysis_status="VALID",
                trade_action=None,
                result_data=_dual_result(),
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    return report_id


def _auth_client():
    from api import main as main_mod
    from api.database import UserDB, get_db_ctx
    from api.services import auth_service

    client = TestClient(main_mod.app, raise_server_exceptions=False)
    email = auth_service.normalize_email(f"dav1283-{uuid4().hex[:8]}@test.com")
    now = datetime.now(timezone.utc)
    with get_db_ctx() as db:
        user = UserDB(
            id=str(uuid4()),
            email=email,
            is_active=True,
            created_at=now,
            updated_at=now,
            last_login_at=now,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
    token = auth_service.create_access_token(user)
    return client, user_id, {"Authorization": f"Bearer {token}"}


def test_reports_list_and_detail_fall_back_to_post_gate_fields():
    client, user_id, headers = _auth_client()
    try:
        report_id = _insert_legacy_dual_report(user_id)

        list_resp = client.get("/v1/reports", headers=headers)
        assert list_resp.status_code == 200
        rows = [r for r in list_resp.json()["reports"] if r["id"] == report_id]
        assert rows, "inserted report missing from list"
        row = rows[0]
        assert row["trade_action"] == "NO_TRADE"
        assert row["decision"] == "NO_TRADE"
        assert row["direction"] == "看空"
        assert row["analysis_status"] == "VALID"
        assert row["target_price"] is None
        assert row["stop_loss_price"] is None
        assert "price_basis_gate_blocked" in (row.get("reason_codes") or [])

        detail_resp = client.get(f"/v1/reports/{report_id}", headers=headers)
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["trade_action"] == "NO_TRADE"
        assert detail["decision"] == "NO_TRADE"
        assert "price_basis_gate_blocked" in (detail.get("reason_codes") or [])
    finally:
        client.close()


# ── D5: top-level result carries the post-gate action ────────────────────────

class _FakePropagator:
    def get_graph_args(self):
        return {}

    def create_initial_state(self, *_args, **kwargs):
        return {"horizon": kwargs.get("horizon", "short")}


class _GateBlockedFakeGraph:
    """Chat-entry (path-2) fake: the graph run's final state already carries
    the post-gate NO_TRADE status while the decision text still says SELL."""

    POST_GATE_DS = {
        "analysis_status": "VALID",
        "direction": "BEAR",
        "trade_action": "NO_TRADE",
        "risk_status": "OK",
        "confirmation_state": "CONFIRMED",
        "reason_codes": ["manager_terminal", "price_basis_gate_blocked"],
        "failed_checks": ["price_basis_gate_blocked"],
    }
    GATE = {
        "status": "blocked",
        "violations": [{"kind": "k", "source": "investment_plan", "detail": "决策驱动价格 50.0 无法归因"}],
    }

    def __init__(self, selected_analysts, data_collector, **_kwargs):
        self.data_collector = data_collector
        self.propagator = _FakePropagator()
        self.role_resolved_configs = {}
        self.quick_thinking_llm = object()
        self.graph = self

    def process_signal(self, _decision):
        return "SELL"

    async def astream(self, _init_state, **_kwargs):
        yield {
            "horizon": "short",
            "company_of_interest": "000657.SZ",
            "trade_date": "2026-09-24",
            "final_trade_decision": "最终交易建议：卖出",
            "market_report": "text",
            "decision_status": dict(self.POST_GATE_DS),
            "analysis_status": "VALID",
            "trade_action": "NO_TRADE",
            "risk_status": "OK",
            "price_basis_gate": dict(self.GATE),
            "analyst_traces": [],
            "data_gaps": [],
        }

    def _build_horizon_result(self, _horizon, state, _market_source=None):
        return dict(state)


def test_chat_path_hoists_post_gate_status_and_keeps_pre_gate_field():
    job_id = f"dav1283-{uuid4().hex}"
    store = InMemoryJobStore()
    collector = MagicMock()
    collector.collect.return_value = {"market_data_context": {"source": "fixture"}}
    saved_reports = []
    db = MagicMock()
    request = main.AnalyzeRequest(
        symbol="000657.SZ",
        trade_date="2026-09-24",
        horizons=["short"],
        selected_analysts=[],
        query="分析 000657.SZ 短线机会",
    )

    async def run_job():
        stream = main._stream_job_events(job_id)
        assert (await stream.__anext__()).startswith("event: job.ready")
        task = asyncio.create_task(
            main._run_job_inner(job_id, request, stream_events=False, save_report=True)
        )
        async for chunk in stream:
            if "event: done" in chunk:
                break
        await task
        await stream.aclose()

    with (
        patch.object(main, "_job_store_instance", store),
        patch.object(main, "_shared_data_collector", collector),
        patch.object(main, "TradingAgentsGraph", _GateBlockedFakeGraph),
        patch.object(main, "_build_runtime_config", return_value={}),
        patch.object(main, "_resolve_and_freeze_custom_prompts", return_value=({}, False)),
        patch.object(main, "_parse_intent", return_value={"ticker": "000657.SZ"}),
        patch.object(main, "get_db_ctx", return_value=nullcontext(db)),
        patch.object(report_service, "init_report"),
        patch.object(report_service, "update_report_partial"),
        patch.object(report_service, "extract_structured_data", return_value=None),
        patch.object(
            report_service,
            "create_report",
            side_effect=lambda **kwargs: saved_reports.append(kwargs),
        ),
    ):
        asyncio.run(run_job())

    job = store.get_job(job_id)
    assert job["status"] == "completed"
    result = job["result"]
    # Top level now mirrors the primary horizon's post-gate status…
    assert result["trade_action"] == "NO_TRADE"
    assert result["decision"] == "NO_TRADE"
    assert result["analysis_status"] == "VALID"
    # …while the pre-gate signal is preserved separately.
    assert result["pre_gate_trade_action"] == "SELL"
    assert result["price_basis_gate"]["status"] == "blocked"
    assert result["short_term"]["trade_action"] == "NO_TRADE"
    # The DB write received the post-gate decision, not the pre-gate SELL.
    assert saved_reports
    assert saved_reports[0]["decision"] == "NO_TRADE"


def test_reports_list_never_bulk_loads_result_data():
    """DAV-1283 D2 rework: the list query stays on summary columns; the
    post-gate fallback uses json_extract only for rows that need it."""
    from sqlalchemy import event, inspect

    from api.database import engine

    client, user_id, headers = _auth_client()
    try:
        _insert_legacy_dual_report(user_id)

        # 1) The summary query itself must leave result_data unloaded.
        from api.database import get_db_ctx

        with get_db_ctx() as db:
            row = report_service.get_reports_by_user(db=db, user_id=user_id, limit=1)[0]
            assert "result_data" in inspect(row).unloaded

        # 2) Capture all SQL during GET /v1/reports: no statement may select
        #    the result_data column outside a json_extract(...) call.
        statements: list[str] = []

        def _capture(_conn, _cursor, statement, _params, _ctx, _many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            resp = client.get("/v1/reports", headers=headers)
            assert resp.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        import re

        def _has_bare_result_data(stmt: str) -> bool:
            if "result_data" not in stmt:
                return False
            stripped = re.sub(r"json_extract\(\s*reports\.result_data\s*,", "", stmt)
            return "result_data" in stripped

        offenders = [s for s in statements if _has_bare_result_data(s)]
        assert not offenders, f"list endpoint loaded full result_data: {offenders[:1]}"
        # Sanity: the narrow json_extract read did run for the legacy row.
        assert any("json_extract" in s for s in statements)

        rows = [r for r in resp.json()["reports"] if r["symbol"] == "000657.SZ"]
        assert rows and rows[0]["trade_action"] == "NO_TRADE"
    finally:
        client.close()


def test_apply_post_gate_read_fallback_is_read_only():
    payload = {
        "decision": None,
        "direction": None,
        "trade_action": None,
        "analysis_status": "VALID",
        "risk_status": None,
        "confidence": None,
        "probability": None,
        "target_price": None,
        "stop_loss_price": None,
    }
    rd = _dual_result()
    out = report_service.apply_post_gate_read_fallback(dict(payload), rd)
    assert out["trade_action"] == "NO_TRADE"
    assert out["decision"] == "NO_TRADE"
    assert out["direction"] == "看空"
    assert "price_basis_gate_blocked" in out["reason_codes"]
    # Existing populated columns are never overwritten by the fallback.
    kept = dict(payload, trade_action="HOLD", decision="HOLD", direction="看多")
    out2 = report_service.apply_post_gate_read_fallback(kept, rd)
    assert out2["trade_action"] == "HOLD"
    assert out2["decision"] == "HOLD"
    # ...but a post-gate non-executable result still suppresses stale numerics.
    kept2 = dict(payload, target_price=52.0)
    out3 = report_service.apply_post_gate_read_fallback(kept2, rd)
    assert out3["target_price"] is None
