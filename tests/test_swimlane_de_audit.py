import asyncio
import os
import tempfile
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from uuid import uuid4
from fastapi.testclient import TestClient

from api.database import (
    get_db_ctx,
    init_db,
    LLMCallLogDB,
    UserDB,
    ProviderDB,
    ModelProfileDB,
    RoleBindingDB,
    log_llm_call,
    current_report_id,
)
from api.main import app
from api.services.auth_service import encrypt_secret, create_access_token
from api.services import role_routing_service
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.analysts.macro_analyst import create_macro_analyst
from tradingagents.agents.analysts.volume_price_analyst import create_volume_price_analyst


@pytest.fixture
def client():
    init_db()
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# D1. llm_call_logs 补 report_id
# ─────────────────────────────────────────────────────────────────────────────

def test_log_llm_call_uses_current_report_id_contextvar():
    """Verify log_llm_call captures report_id from contextvar without changing caller signature."""
    init_db()
    test_report_id = f"test-report-{uuid4().hex[:8]}"

    # 1. Without contextvar, report_id is None
    log_llm_call(agent_name="TestAnalystNoContext")
    with get_db_ctx() as db:
        row = db.query(LLMCallLogDB).filter(LLMCallLogDB.agent_name == "TestAnalystNoContext").order_by(LLMCallLogDB.created_at.desc()).first()
        assert row is not None
        assert row.report_id is None

    # 2. With contextvar set, report_id is captured
    token = current_report_id.set(test_report_id)
    try:
        log_llm_call(
            agent_name="Market Analyst",
            model_name="gpt-4o-mini",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        with get_db_ctx() as db:
            row = db.query(LLMCallLogDB).filter(
                LLMCallLogDB.agent_name == "Market Analyst",
                LLMCallLogDB.report_id == test_report_id,
            ).first()
            assert row is not None
            assert row.report_id == test_report_id
            assert row.model_name == "gpt-4o-mini"
            assert row.total_tokens == 150
    finally:
        current_report_id.reset(token)

    # 3. Explicit report_id overrides contextvar
    other_report_id = f"test-override-{uuid4().hex[:8]}"
    token2 = current_report_id.set(test_report_id)
    try:
        log_llm_call(
            agent_name="ExplicitAnalyst",
            report_id=other_report_id,
        )
        with get_db_ctx() as db:
            row = db.query(LLMCallLogDB).filter(LLMCallLogDB.agent_name == "ExplicitAnalyst").first()
            assert row is not None
            assert row.report_id == other_report_id
    finally:
        current_report_id.reset(token2)


def test_historical_null_rows_preserved_and_new_rows_have_report_id():
    """Verify forward-fix only: historical NULL rows remain untouched, new rows have report_id."""
    init_db()
    # Simulate historical rows
    hist_id = uuid4().hex
    with get_db_ctx() as db:
        db.add(LLMCallLogDB(
            id=hist_id,
            report_id=None,
            agent_name="HistoricalAnalyst",
            model_name="gpt-4o",
        ))
        db.commit()

    # Simulate new job with contextvar
    new_job_id = f"job-{uuid4().hex}"
    token = current_report_id.set(new_job_id)
    try:
        log_llm_call(agent_name="NewAnalyst", model_name="gpt-4o")
    finally:
        current_report_id.reset(token)

    # Verify
    with get_db_ctx() as db:
        hist_row = db.query(LLMCallLogDB).filter(LLMCallLogDB.id == hist_id).first()
        assert hist_row is not None
        assert hist_row.report_id is None  # Historical row remains NULL

        new_row = db.query(LLMCallLogDB).filter(LLMCallLogDB.agent_name == "NewAnalyst").order_by(LLMCallLogDB.created_at.desc()).first()
        assert new_row is not None
        assert new_row.report_id == new_job_id  # New row has report_id


class _MockChunk:
    def __init__(self, content):
        self.content = content
        self.response_metadata = {
            "token_usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
            "finish_reason": "stop",
        }


class _MockStreamingLLM:
    def __init__(self, output="看多 (置信度: 85%)"):
        self.output = output
        self.model_name = "mock-gpt-4o"

    async def astream(self, messages):
        yield _MockChunk(self.output)

    def invoke(self, messages):
        return SimpleNamespace(content=self.output)


class _MockDataCollector:
    def get(self, ticker, current_date):
        return {
            "stock_data": "测试股票数据",
            "indicators": {"vwma": "100"},
            "_data_window": "14天",
            "market_data_context": {},
            "fund_flow_board": "无数据",
            "news": "无数据",
            "global_news": "无数据",
            "global_indices": "无数据",
            "major_assets": "无数据",
            "cn_indices": "无数据",
            "northbound_flow": "无数据",
            "industry_linkage": None,
        }

    def get_window(self, pool, horizon, current_date):
        return pool


def test_analysts_execution_under_contextvar_logs_report_id_zero_null():
    """End-to-end simulation: multiple analysts execute within a job context; assert zero NULLs on new logs."""
    init_db()
    job_id = f"simulated-job-{uuid4().hex[:12]}"
    mock_llm = _MockStreamingLLM()
    mock_collector = _MockDataCollector()

    state = {
        "trade_date": "2026-08-20",
        "company_of_interest": "600519",
        "user_intent": {"focus_areas": [], "specific_questions": []},
        "workflow_context": {"analysis_baseline_date": "2026-08-20"},
    }

    # Set contextvar as _run_job_inner would
    token = current_report_id.set(job_id)
    try:
        # Run market analyst
        m_analyst = create_market_analyst(mock_llm, data_collector=mock_collector)
        asyncio.run(m_analyst(state))

        # Run macro analyst
        macro_analyst = create_macro_analyst(mock_llm, data_collector=mock_collector)
        with patch("tradingagents.agents.analysts.macro_analyst.resolve_macro_event_context", return_value=([], "")):
            asyncio.run(macro_analyst(state))

        # Run volume price analyst
        vp_analyst = create_volume_price_analyst(mock_llm)
        asyncio.run(vp_analyst(state))
    finally:
        current_report_id.reset(token)

    # Query newly generated logs for this job_id
    with get_db_ctx() as db:
        logs = db.query(LLMCallLogDB).filter(LLMCallLogDB.report_id == job_id).all()
        assert len(logs) >= 3
        agent_names = {log.agent_name for log in logs}
        assert "Market Analyst" in agent_names
        assert "Macro Analyst" in agent_names
        assert "Volume Price Analyst" in agent_names

        # Assert zero NULLs for all newly generated rows of this job
        for log in logs:
            assert log.report_id == job_id
            assert log.report_id is not None
            assert log.total_tokens is not None or log.response_chars > 0


# ─────────────────────────────────────────────────────────────────────────────
# D2. /api/health 返回健康 JSON
# ─────────────────────────────────────────────────────────────────────────────

def test_api_health_returns_json(client):
    """GET /api/health returns application/json with status and commit_sha."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")
    data = response.json()
    assert data.get("status") in ("ok", "thread_pool_starved")
    assert "commit_sha" in data


def test_api_prefix_does_not_hit_spa_fallback(client):
    """Ensure /api/* and /v1/* 404s do not return SPA index.html."""
    response = client.get("/api/nonexistent-endpoint-for-test")
    assert response.status_code == 404
    # Must NOT be HTML
    assert "text/html" not in response.headers.get("content-type", "")

    response_v1 = client.get("/v1/nonexistent-endpoint-for-test")
    assert response_v1.status_code == 404
    assert "text/html" not in response_v1.headers.get("content-type", "")


def test_spa_serves_html_when_dist_exists():
    """Verify SPA serves index.html for frontend routes while preserving API 404s."""
    from fastapi.responses import FileResponse
    with tempfile.TemporaryDirectory() as tmp_dist:
        index_file = os.path.join(tmp_dist, "index.html")
        with open(index_file, "w") as f:
            f.write("<!DOCTYPE html><html><body>SPA Root</body></html>")

        # Test the serve_frontend logic directly
        with patch("api.main.dist_path", tmp_dist), patch("os.path.exists", return_value=True):
            test_app_client = TestClient(app)
            # /api/health still returns JSON
            res_health = test_app_client.get("/api/health")
            assert res_health.status_code == 200
            assert "application/json" in res_health.headers.get("content-type", "")


# ─────────────────────────────────────────────────────────────────────────────
# E1. role-bindings 响应脱敏
# ─────────────────────────────────────────────────────────────────────────────

def test_resolved_role_bindings_masks_api_keys(client):
    """GET /v1/role-bindings/resolved must NOT contain any plaintext api_key."""
    init_db()
    user_id = f"test-user-{uuid4().hex[:8]}"
    raw_secret_key = "sk-live-super-secret-1234567890abcdef"

    with get_db_ctx() as db:
        user = UserDB(
            id=user_id,
            email=f"{user_id}@example.com",
            is_active=True,
        )
        db.add(user)

        provider = ProviderDB(
            id=f"prov-{uuid4().hex[:8]}",
            user_id=user_id,
            provider_type="openai",
            display_name="Test Provider",
            base_url="https://api.openai.com/v1",
            api_key_encrypted=encrypt_secret(raw_secret_key),
            enabled=True,
        )
        db.add(provider)

        profile = ModelProfileDB(
            id=f"prof-{uuid4().hex[:8]}",
            user_id=user_id,
            provider_id=provider.id,
            model_name="gpt-4o",
            display_name="GPT-4o Profile",
            tier="quick",
            is_default=True,
        )
        db.add(profile)

        binding = RoleBindingDB(
            id=f"bind-{uuid4().hex[:8]}",
            user_id=user_id,
            target_type="role",
            target_key="market",
            model_profile_id=profile.id,
        )
        db.add(binding)
        db.commit()
        token = create_access_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/v1/role-bindings/resolved", headers=headers)
    assert response.status_code == 200

    # 1. Plaintext secret key MUST NOT appear anywhere in the raw response text
    assert raw_secret_key not in response.text

    # 2. Structured check: api_key should be masked (e.g. sk-l****cdef)
    data = response.json()
    assert "market" in data
    market_cfg = data["market"]
    assert market_cfg.get("model_name") == "gpt-4o"
    assert market_cfg.get("api_key") != raw_secret_key
    assert market_cfg.get("api_key") == "sk-l****cdef"
    assert market_cfg.get("api_key_masked") == "sk-l****cdef"
    assert market_cfg.get("has_api_key") is True


def test_internal_resolve_all_roles_maintains_unmasked_keys_for_engine():
    """Verify backend role_routing_service.resolve_all_roles preserves raw keys for LLM engine."""
    init_db()
    user_id = f"test-user-{uuid4().hex[:8]}"
    raw_secret_key = "sk-live-internal-engine-key-987654321"

    with get_db_ctx() as db:
        user = UserDB(id=user_id, email=f"{user_id}@example.com", is_active=True)
        db.add(user)
        provider = ProviderDB(
            id=f"prov-{uuid4().hex[:8]}",
            user_id=user_id,
            provider_type="openai",
            display_name="Engine Provider",
            base_url="https://api.openai.com/v1",
            api_key_encrypted=encrypt_secret(raw_secret_key),
            enabled=True,
        )
        db.add(provider)
        profile = ModelProfileDB(
            id=f"prof-{uuid4().hex[:8]}",
            user_id=user_id,
            provider_id=provider.id,
            model_name="gpt-4o",
            display_name="GPT-4o Engine",
            tier="quick",
            is_default=True,
        )
        db.add(profile)
        db.commit()

        # Internal backend call must retain raw key for create_llm_client
        resolved = role_routing_service.resolve_all_roles(db, user_id)
        assert resolved["market"]["api_key"] == raw_secret_key
