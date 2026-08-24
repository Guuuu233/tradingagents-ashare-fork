"""DAV-397: request-level v2_debate_enabled must survive HTTP config_overrides.

Internal Propagator tests can pass a runtime_config dict directly. Real
POST /v1/analyze goes through _build_runtime_config, which previously
dropped any key not in _CONFIG_OVERRIDES_ALLOWLIST.
"""
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from api.database import UserDB, UserLLMConfigDB, get_db_ctx, init_db
from api.main import _CONFIG_OVERRIDES_ALLOWLIST, _build_runtime_config, app
from tradingagents.agents.utils.agent_states import is_v2_debate_enabled


def _client() -> TestClient:
    init_db()
    return TestClient(app, raise_server_exceptions=False)


def _auth(client: TestClient) -> tuple[str, str]:
    email = f"v2-override-{uuid4().hex[:8]}@test.com"
    r = client.post("/v1/auth/request-code", json={"email": email})
    code = r.json()["dev_code"]
    r2 = client.post("/v1/auth/verify-code", json={"email": email, "code": code})
    token = r2.json()["access_token"]
    with get_db_ctx() as db:
        user = db.query(UserDB).filter(UserDB.email == email).one()
        user_id = user.id
    return token, user_id


def test_default_runtime_config_does_not_enable_v2():
    config = _build_runtime_config({})
    assert config.get("v2_debate_enabled") in (None, False)
    assert is_v2_debate_enabled(config) is False


def test_request_override_enables_v2_without_persisting_sensitive_keys():
    config = _build_runtime_config(
        {
            "v2_debate_enabled": True,
            "api_key": "sk-request-secret",
            "backend_url": "https://evil.example/v1",
            "llm_provider": "not-a-persisted-provider-check",
        }
    )
    assert config.get("v2_debate_enabled") is True
    assert is_v2_debate_enabled(config) is True
    assert config.get("api_key") != "sk-request-secret"
    assert config.get("backend_url") != "https://evil.example/v1"
    assert "api_key" not in _CONFIG_OVERRIDES_ALLOWLIST
    assert "backend_url" not in _CONFIG_OVERRIDES_ALLOWLIST


def test_http_analyze_dry_run_passes_v2_override_and_keeps_3_1():
    """Real FastAPI request path, not a direct Propagator runtime_config."""
    client = _client()
    token, user_id = _auth(client)
    headers = {"Authorization": f"Bearer {token}"}

    with get_db_ctx() as db:
        db.merge(
            UserLLMConfigDB(
                user_id=user_id,
                max_debate_rounds=3,
                max_risk_discuss_rounds=1,
            )
        )
        db.commit()
        before = (
            db.query(UserLLMConfigDB)
            .filter(UserLLMConfigDB.user_id == user_id)
            .one()
        )
        before_updated = before.updated_at
        before_rounds = (before.max_debate_rounds, before.max_risk_discuss_rounds)

    captured: dict = {}
    original = _build_runtime_config

    def _wrapped(overrides, user_id=None, db=None):
        captured["request_keys"] = sorted((overrides or {}).keys())
        cfg = original(overrides, user_id=user_id, db=db)
        captured["cfg"] = cfg
        return cfg

    with patch("api.main._build_runtime_config", side_effect=_wrapped):
        r = client.post(
            "/v1/analyze",
            headers=headers,
            json={
                "symbol": "600000.SH",
                "trade_date": "2026-08-21",
                "dry_run": True,
                "config_overrides": {
                    "v2_debate_enabled": True,
                    "api_key": "sk-request-secret",
                    "backend_url": "https://evil.example/v1",
                },
            },
        )
    assert r.status_code == 200, r.text
    assert "v2_debate_enabled" in captured["request_keys"]
    cfg = captured["cfg"]
    assert cfg.get("v2_debate_enabled") is True
    assert is_v2_debate_enabled(cfg) is True
    assert cfg.get("api_key") != "sk-request-secret"
    assert cfg.get("backend_url") != "https://evil.example/v1"

    with get_db_ctx() as db:
        after = (
            db.query(UserLLMConfigDB)
            .filter(UserLLMConfigDB.user_id == user_id)
            .one()
        )
        assert (after.max_debate_rounds, after.max_risk_discuss_rounds) == (3, 1)
        assert (after.max_debate_rounds, after.max_risk_discuss_rounds) == before_rounds
        assert after.updated_at == before_updated
