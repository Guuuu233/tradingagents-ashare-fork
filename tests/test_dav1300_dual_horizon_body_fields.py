"""DAV-1300: explicit dual-horizon reports persist the primary horizon slice's
report body fields (hoisted to top level before ``create_report``), and
``GET /v1/reports/{id}`` falls back read-only to the primary slice for
historical rows whose body columns were left NULL.

Primary-slice rule (shared ``_primary_horizon_slice``): the short slice when
it carries a recorded status (trade_action / analysis_status /
decision_status), otherwise the medium slice.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import main as main_mod
from api.database import Base, ReportDB
from api.services import report_service
from tests.test_dual_horizon_e2e import _run_dual_horizon_job

BODY_FIELDS = report_service.PRIMARY_HORIZON_REPORT_FIELDS


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ── Write path: hoisting + persistence ───────────────────────────────────────

def test_dual_horizon_hoists_primary_slice_body_fields_to_result_and_save():
    """Both horizons complete -> top level equals the whole short slice."""
    job, saved_reports, _ = _run_dual_horizon_job()

    assert job["status"] == "completed"
    result = job["result"]
    primary = result["short_term"]
    assert primary["status"] == "completed"
    for field in BODY_FIELDS:
        assert primary.get(field), f"fixture missing {field} on short slice"
        assert result.get(field) == primary.get(field)
    # risk_items / key_metrics come from the primary slice's own
    # extract_structured_data output (already run per horizon).
    assert result["risk_items"] == primary["risk_items"]
    assert result["key_metrics"] == primary["key_metrics"]

    assert len(saved_reports) == 1
    kwargs = saved_reports[0]
    assert kwargs["result_data"] is result
    assert kwargs["risk_items"] == primary["risk_items"]
    assert kwargs["key_metrics"] == primary["key_metrics"]


def test_dual_horizon_short_failed_hoists_medium_slice():
    """Short slice failed -> the medium slice becomes the primary source."""
    job, saved_reports, _ = _run_dual_horizon_job(fail_horizons=("short",))

    assert job["status"] == "completed"
    result = job["result"]
    assert result["short_term"]["status"] == "failed"
    primary = result["medium_term"]
    assert primary["status"] == "completed"
    for field in BODY_FIELDS:
        assert primary.get(field), f"fixture missing {field} on medium slice"
        assert result.get(field) == primary.get(field)
    assert result["risk_items"] == primary["risk_items"]
    assert result["key_metrics"] == primary["key_metrics"]

    assert len(saved_reports) == 1
    kwargs = saved_reports[0]
    assert kwargs["risk_items"] == primary["risk_items"]
    assert kwargs["key_metrics"] == primary["key_metrics"]


def test_dual_horizon_structured_extraction_failure_still_saves_report():
    """extract_structured_data failing is non-fatal: the report still saves."""
    job, saved_reports, _ = _run_dual_horizon_job(structured_fails=True)

    assert job["status"] == "completed"
    result = job["result"]
    primary = result["short_term"]
    for field in BODY_FIELDS:
        assert result.get(field) == primary.get(field)
    # Slice-level risk_items/key_metrics are [] on extraction failure.
    assert result["risk_items"] == []
    assert result["key_metrics"] == []
    assert len(saved_reports) == 1
    # ``or None`` normalises [] to NULL semantics, same as the intent path.
    assert saved_reports[0]["risk_items"] is None
    assert saved_reports[0]["key_metrics"] is None


def test_dual_horizon_report_columns_match_primary_slice():
    """create_report persists all 10 body columns + risk_items/key_metrics."""
    job, saved_reports, _ = _run_dual_horizon_job()
    assert job["status"] == "completed"
    kwargs = saved_reports[0]
    result = kwargs["result_data"]
    primary = result["short_term"]

    db = _session()
    try:
        report = report_service.create_report(
            db=db,
            symbol=kwargs["symbol"],
            trade_date=kwargs["trade_date"],
            decision=None,
            result_data=result,
            risk_items=kwargs["risk_items"],
            key_metrics=kwargs["key_metrics"],
            data_gaps=kwargs["data_gaps"],
            falsification_conditions=kwargs["falsification_conditions"],
            not_applicable=kwargs["not_applicable"],
        )
        for field in BODY_FIELDS:
            assert getattr(report, field) == primary.get(field)
            assert getattr(report, field)
        assert report.risk_items
        assert report.risk_items[0]["name"] == "short波动风险"
        assert report.key_metrics
        assert report.key_metrics[0]["value"] == "short-28.5x"
    finally:
        db.close()


# ── Read path: read-only fallback for historical rows ────────────────────────

def _legacy_result(*, short_failed: bool = False) -> dict:
    medium = {
        "horizon": "medium",
        "status": "completed",
        "trade_action": "HOLD",
        "market_report": "medium 市场分析正文",
        "sentiment_report": "medium 情绪分析正文",
        "news_report": "medium 新闻正文",
        "fundamentals_report": "medium 基本面正文",
        "macro_report": "medium 宏观正文",
        "smart_money_report": "medium 主力资金正文",
        "volume_price_report": "medium 量价正文",
        "investment_plan": "medium 投资计划正文",
        "trader_investment_plan": "medium 交易员计划正文",
        "final_trade_decision": "medium 最终交易决策正文",
    }
    if short_failed:
        short = {
            "horizon": "short",
            "status": "failed",
            "error": "short provider unavailable",
        }
    else:
        short = {**medium, "horizon": "short"}
        short = {
            k: (v.replace("medium", "short") if isinstance(v, str) else v)
            for k, v in short.items()
        }
    return {
        "mode": "dual_horizon",
        "status": "partial" if short_failed else "completed",
        "requested_horizons": ["short", "medium"],
        "short_term": short,
        "medium_term": medium,
    }


def _auth_client():
    from api.database import UserDB, get_db_ctx
    from api.services import auth_service

    client = TestClient(main_mod.app, raise_server_exceptions=False)
    email = auth_service.normalize_email(f"dav1300-{uuid4().hex[:8]}@test.com")
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


def _insert_legacy_report(user_id: str, result_data: dict) -> str:
    from api.database import get_db_ctx

    report_id = uuid4().hex
    now = datetime.now(timezone.utc)
    with get_db_ctx() as db:
        db.add(
            ReportDB(
                id=report_id,
                user_id=user_id,
                symbol="600036.SH",
                trade_date="2026-07-08",
                status="completed",
                result_data=result_data,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    return report_id


def _assert_columns_still_null(report_id: str):
    from api.database import get_db_ctx

    with get_db_ctx() as db:
        row = db.query(ReportDB).filter(ReportDB.id == report_id).first()
        assert row is not None
        for field in BODY_FIELDS:
            assert getattr(row, field) is None, f"{field} was backfilled"


def test_report_detail_falls_back_to_primary_short_slice_read_only():
    client, user_id, headers = _auth_client()
    try:
        report_id = _insert_legacy_report(user_id, _legacy_result())

        resp = client.get(f"/v1/reports/{report_id}", headers=headers)
        assert resp.status_code == 200
        detail = resp.json()
        for field in BODY_FIELDS:
            assert detail[field] == _legacy_result()["short_term"][field]
        _assert_columns_still_null(report_id)
    finally:
        client.close()


def test_report_detail_falls_back_to_medium_when_short_has_no_status():
    client, user_id, headers = _auth_client()
    try:
        report_id = _insert_legacy_report(user_id, _legacy_result(short_failed=True))

        resp = client.get(f"/v1/reports/{report_id}", headers=headers)
        assert resp.status_code == 200
        detail = resp.json()
        for field in BODY_FIELDS:
            assert detail[field] == _legacy_result()["medium_term"][field]
        _assert_columns_still_null(report_id)
    finally:
        client.close()
