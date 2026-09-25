"""DAV-1271 G1: read-only fallback for game_theory_report.

Dual-horizon runs store the section only under
``result_data.short_term.game_theory_report`` /
``result_data.medium_term.game_theory_report`` while the top-level key and the
DB column stay NULL. The resolver must fall back top → short_term →
medium_term; the report detail API must expose the fallback for historical
rows without ever backfilling the database.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.database import ReportDB, get_db_ctx, init_db
from api.services import report_service


@pytest.fixture(autouse=True)
def _ensure_db():
    init_db()


GT_TEXT = "博弈论报告：对手盘策略分析正文。"
DEGRADED = "【博弈论分析不可用】原因：上游数据缺失，该项不可用。"


# ─── resolve_game_theory_report / resolve_report_fields ──────────────────────


def test_resolve_top_level_wins():
    rd = {
        "game_theory_report": "TOP",
        "short_term": {"game_theory_report": "SHORT"},
        "medium_term": {"game_theory_report": "MEDIUM"},
    }
    assert report_service.resolve_game_theory_report(rd) == "TOP"
    assert report_service.resolve_report_fields(result_data=rd)["game_theory_report"] == "TOP"


def test_resolve_falls_back_to_short_term():
    rd = {"short_term": {"game_theory_report": GT_TEXT}}
    assert report_service.resolve_game_theory_report(rd) == GT_TEXT
    assert report_service.resolve_report_fields(result_data=rd)["game_theory_report"] == GT_TEXT


def test_resolve_falls_back_to_medium_term_when_short_missing():
    rd = {"medium_term": {"game_theory_report": "MEDIUM-GT"}}
    assert report_service.resolve_game_theory_report(rd) == "MEDIUM-GT"


def test_resolve_short_term_preferred_over_medium():
    rd = {
        "short_term": {"game_theory_report": "S"},
        "medium_term": {"game_theory_report": "M"},
    }
    assert report_service.resolve_game_theory_report(rd) == "S"


def test_resolve_skips_empty_top_level():
    rd = {"game_theory_report": "  ", "short_term": {"game_theory_report": GT_TEXT}}
    assert report_service.resolve_game_theory_report(rd) == GT_TEXT


def test_resolve_returns_none_when_absent():
    assert report_service.resolve_game_theory_report(None) is None
    assert report_service.resolve_game_theory_report({}) is None
    assert report_service.resolve_game_theory_report({"short_term": {}}) is None
    assert report_service.resolve_report_fields(result_data={})["game_theory_report"] is None


def test_resolve_degraded_text_returned_as_is():
    rd = {"short_term": {"game_theory_report": DEGRADED}}
    assert report_service.resolve_game_theory_report(rd) == DEGRADED


def test_create_report_writes_fallback_value_to_db_column():
    """New reports persist the resolved fallback into the DB column."""
    rd = {"short_term": {"game_theory_report": GT_TEXT}, "decision": "WAIT"}
    with get_db_ctx() as db:
        created = report_service.create_report(
            db,
            user_id=str(uuid4()),
            report_id=f"rep-{uuid4().hex[:8]}",
            symbol="000725.SZ",
            trade_date="2026-09-24",
            decision="WAIT",
            result_data=rd,
        )
        assert created.game_theory_report == GT_TEXT


# ─── read path: GET /v1/reports/{id} exposes fallback without backfill ───────


def _auth(client: TestClient) -> str:
    email = f"gt-fallback-{uuid4().hex[:8]}@test.com"
    r = client.post("/v1/auth/request-code", json={"email": email})
    code = r.json()["dev_code"]
    r2 = client.post("/v1/auth/verify-code", json={"email": email, "code": code})
    return r2.json()["access_token"]


def _user_id(client: TestClient, token: str) -> str:
    r = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return r.json()["id"]


def _seed_legacy_report(column_value=None, user_id=None) -> str:
    """Insert a report row mimicking a pre-fix historical record: DB column NULL."""
    report_id = f"legacy-{uuid4().hex[:8]}"
    rd = {
        "short_term": {"game_theory_report": GT_TEXT},
        "medium_term": {},
        "decision": "WAIT",
    }
    with get_db_ctx() as db:
        row = ReportDB(
            id=report_id,
            user_id=user_id,
            symbol="000725.SZ",
            trade_date="2026-09-24",
            status="completed",
            decision="WAIT",
            result_data=rd,
            game_theory_report=column_value,
        )
        db.add(row)
        db.commit()
    return report_id


def test_report_detail_api_returns_fallback_without_backfill():
    from api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    token = _auth(client)
    report_id = _seed_legacy_report(column_value=None, user_id=_user_id(client, token))

    r = client.get(f"/v1/reports/{report_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["game_theory_report"] == GT_TEXT

    # Read path must NOT backfill the DB column.
    with get_db_ctx() as db:
        row = db.get(ReportDB, report_id)
        assert row.game_theory_report is None


def test_report_detail_api_prefers_db_column():
    from api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    token = _auth(client)
    report_id = _seed_legacy_report(column_value="COLUMN-GT", user_id=_user_id(client, token))

    r = client.get(f"/v1/reports/{report_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["game_theory_report"] == "COLUMN-GT"


def test_report_detail_api_returns_null_when_truly_absent():
    from api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    token = _auth(client)
    report_id = f"legacy-{uuid4().hex[:8]}"
    with get_db_ctx() as db:
        db.add(ReportDB(
            id=report_id,
            user_id=_user_id(client, token),
            symbol="000725.SZ",
            trade_date="2026-09-24",
            status="completed",
            result_data={"short_term": {}},
        ))
        db.commit()

    r = client.get(f"/v1/reports/{report_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["game_theory_report"] is None
