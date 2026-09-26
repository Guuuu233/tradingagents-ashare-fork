"""DAV-1301: dual-horizon reports expose ``horizon_decisions`` on the list and
detail endpoints — per-horizon post-gate action/direction, the research
manager's original verdict, and the per-slice run status — without ever
bulk-loading ``result_data`` on the list path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import main
from api.database import Base, ReportDB
from api.services import report_service


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _dual_result():
    """Mirrors a747371c: short=SELL/看空, medium=BUY→NO_TRADE (gate-blocked)/看多."""
    return {
        "mode": "dual_horizon",
        "status": "completed",
        "decision": "SELL",
        "trade_action": "SELL",
        "direction": "看空",
        "short_term": {
            "horizon": "short",
            "status": "completed",
            "trade_action": "SELL",
            "analysis_status": "VALID",
            "direction": "BEAR",
            "confidence": 70,
            "target_price": 55.0,
            "stop_loss_price": 53.3,
            "decision_status": {
                "analysis_status": "VALID",
                "direction": "BEAR",
                "trade_action": "SELL",
                "reason_codes": ["manager_terminal"],
                "failed_checks": [],
            },
            "price_basis_gate": {"status": "pass"},
            "manager_verdict": {"trade_action": "SELL", "direction": "偏空", "winner": "bear"},
        },
        "medium_term": {
            "horizon": "medium",
            "status": "completed",
            "trade_action": "NO_TRADE",
            "analysis_status": "VALID",
            "direction": "BULL",
            "decision_status": {
                "analysis_status": "VALID",
                "direction": "BULL",
                "trade_action": "NO_TRADE",
                "reason_codes": ["price_basis_gate_blocked"],
                "failed_checks": ["price_basis_gate_blocked"],
            },
            "price_basis_gate": {"status": "blocked"},
            "investment_debate_state": {
                "manager_verdict": {"trade_action": "BUY", "direction": "偏多", "winner": "bull"},
            },
        },
    }


def test_resolve_horizon_decisions_dual():
    hds = report_service.resolve_horizon_decisions(_dual_result())
    assert hds is not None and len(hds) == 2
    short, medium = hds
    assert short["horizon"] == "short"
    assert short["trade_action"] == "SELL"
    assert short["direction"] == "看空"
    assert short["analysis_status"] == "VALID"
    assert short["manager_action"] == "SELL"
    assert short["status"] == "completed"
    assert short["confidence"] == 70
    assert short["target_price"] == 55.0
    assert medium["horizon"] == "medium"
    assert medium["trade_action"] == "NO_TRADE"
    assert medium["direction"] == "看多"
    # 研究经理原结论 BUY，被价格门降级
    assert medium["manager_action"] == "BUY"
    assert medium["gate_blocked"] is True
    assert medium["non_executable"] is True
    # 非可执行档不得带出数值
    assert medium["confidence"] is None
    assert medium["target_price"] is None


def test_resolve_horizon_decisions_single_is_none():
    rd = _dual_result()
    del rd["medium_term"]
    assert report_service.resolve_horizon_decisions(rd) is None
    assert report_service.resolve_horizon_decisions(None) is None
    assert report_service.resolve_horizon_decisions({}) is None


def test_resolve_horizon_decisions_failed_horizon():
    rd = _dual_result()
    rd["medium_term"] = {"horizon": "medium", "status": "failed", "error": "boom"}
    hds = report_service.resolve_horizon_decisions(rd)
    assert hds is not None and len(hds) == 2
    assert hds[1]["status"] == "failed"
    assert hds[1]["trade_action"] is None


# ── 返修：研究经理原结论只取记录值，禁止按方向推算 ──────────────────────────

def test_manager_action_prefers_recorded_trade_action_over_direction():
    """manager_verdict.trade_action=WAIT + direction=偏多：不得推算成 BUY。"""
    rd = _dual_result()
    rd["medium_term"]["investment_debate_state"]["manager_verdict"] = {
        "trade_action": "WAIT",
        "direction": "偏多",
        "winner": "bull",
    }
    hds = report_service.resolve_horizon_decisions(rd)
    assert hds[1]["manager_action"] == "WAIT"


def test_manager_action_no_inference_when_unrecorded():
    """verdict 无 trade_action 且无 pre_gate_trade_action → None，不显示原结论。"""
    rd = _dual_result()
    rd["medium_term"]["investment_debate_state"]["manager_verdict"] = {
        "direction": "偏多",
        "winner": "bull",
    }
    hds = report_service.resolve_horizon_decisions(rd)
    assert hds[1]["manager_action"] is None


def test_manager_action_falls_back_to_pre_gate_trade_action():
    rd = _dual_result()
    rd["medium_term"].pop("investment_debate_state")
    rd["medium_term"]["pre_gate_trade_action"] = "HOLD"
    hds = report_service.resolve_horizon_decisions(rd)
    assert hds[1]["manager_action"] == "HOLD"


def _insert_dual_report(user_id: str) -> str:
    from api.database import get_db_ctx

    report_id = uuid4().hex
    now = datetime.now(timezone.utc)
    with get_db_ctx() as db:
        db.add(
            ReportDB(
                id=report_id,
                user_id=user_id,
                symbol="600276.SH",
                trade_date="2026-07-08",
                status="completed",
                # Columns persisted per DAV-1283 (primary horizon) — the list
                # row must still surface both horizons.
                decision="SELL",
                direction="看空",
                confidence=70,
                analysis_status="VALID",
                trade_action="SELL",
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
    email = auth_service.normalize_email(f"dav1301-{uuid4().hex[:8]}@test.com")
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


def test_list_and_detail_expose_horizon_decisions():
    client, user_id, headers = _auth_client()
    try:
        report_id = _insert_dual_report(user_id)

        list_resp = client.get("/v1/reports", headers=headers)
        assert list_resp.status_code == 200
        rows = [r for r in list_resp.json()["reports"] if r["id"] == report_id]
        assert rows, "inserted report missing from list"
        hds = rows[0].get("horizon_decisions")
        assert hds is not None and len(hds) == 2
        by_h = {h["horizon"]: h for h in hds}
        assert by_h["short"]["trade_action"] == "SELL"
        assert by_h["short"]["direction"] == "看空"
        assert by_h["medium"]["trade_action"] == "NO_TRADE"
        assert by_h["medium"]["direction"] == "看多"
        assert by_h["medium"]["manager_action"] == "BUY"
        assert by_h["medium"]["gate_blocked"] is True

        detail_resp = client.get(f"/v1/reports/{report_id}", headers=headers)
        assert detail_resp.status_code == 200
        detail_hds = detail_resp.json().get("horizon_decisions")
        assert detail_hds is not None and len(detail_hds) == 2
        assert detail_hds[1]["manager_action"] == "BUY"
    finally:
        client.close()


def test_list_horizon_decisions_uses_json_extract_only():
    """The list path must never bulk-load result_data (DAV-1283 D2 rule)."""
    import re

    from sqlalchemy import event

    from api.database import engine

    client, user_id, headers = _auth_client()
    try:
        _insert_dual_report(user_id)
        statements: list[str] = []

        def _capture(_conn, _cursor, statement, _params, _ctx, _many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            resp = client.get("/v1/reports", headers=headers)
            assert resp.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        def _has_bare_result_data(stmt: str) -> bool:
            if "result_data" not in stmt:
                return False
            stripped = re.sub(r"json_extract\(\s*reports\.result_data\s*,", "", stmt)
            return "result_data" in stripped

        offenders = [s for s in statements if _has_bare_result_data(s)]
        assert not offenders, f"list endpoint loaded full result_data: {offenders[:1]}"
        assert any("json_extract" in s for s in statements)
    finally:
        client.close()


def test_single_horizon_report_has_no_horizon_decisions():
    client, user_id, headers = _auth_client()
    try:
        from api.database import get_db_ctx

        report_id = uuid4().hex
        now = datetime.now(timezone.utc)
        rd = _dual_result()
        del rd["medium_term"]
        rd["mode"] = "single_horizon"
        with get_db_ctx() as db:
            db.add(
                ReportDB(
                    id=report_id,
                    user_id=user_id,
                    symbol="000657.SZ",
                    trade_date="2026-09-26",
                    status="completed",
                    decision="SELL",
                    direction="看空",
                    analysis_status="VALID",
                    trade_action="SELL",
                    result_data=rd,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.commit()

        detail = client.get(f"/v1/reports/{report_id}", headers=headers).json()
        assert detail.get("horizon_decisions") in (None, [])
        rows = [r for r in client.get("/v1/reports", headers=headers).json()["reports"] if r["id"] == report_id]
        assert rows and rows[0].get("horizon_decisions") in (None, [])
    finally:
        client.close()
