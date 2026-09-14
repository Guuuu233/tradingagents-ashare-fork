"""Contract tests for LLM runtime wiring, warmup, probe, and failure classification (DAV-922 / P2-55b).

Covers:
1. PATCH /v1/config save-triggered probe (_probe_runtime_config).
2. POST /v1/config/warmup and _invoke_runtime_warmup.
3. Role-level provider and base_url inheritance in warmup and TradingAgentsGraph.
4. Failure classification and strict credential sanitization (Bearer, sk-, Cookie, Basic Auth, URL query params).
5. Host proxy environment variable contamination and proxy routing guard.
"""

from contextlib import ExitStack
import os
import tempfile
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
import httpx
import pytest

from api.main import _invoke_runtime_warmup, _probe_runtime_config
from api.services import role_routing_service
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients import (
    FailureCategory,
    LLMProxyRoutingError,
    classify_llm_failure,
    resolve_role_base_url,
)
from tradingagents.llm_clients.validators import _sanitize_error_detail


@pytest.fixture(autouse=True)
def _isolate_proxy_env(monkeypatch):
    """Ensure baseline test execution is isolated from host proxy variables."""
    for k in (
        "http_proxy", "https_proxy", "all_proxy",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "no_proxy", "NO_PROXY",
    ):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# 1. Config Save Probe Tests (_probe_runtime_config)
# ---------------------------------------------------------------------------

class TestProbeRuntimeConfig:
    """Tests for _probe_runtime_config."""

    def test_probe_skipped_when_model_or_api_key_empty(self):
        # Missing model
        cfg_no_model = {"llm_provider": "openai", "api_key": "sk-test", "quick_think_llm": ""}
        assert _probe_runtime_config(cfg_no_model) == {"status": "skipped", "reason": "missing_model_or_key"}

        # Missing api_key
        cfg_no_key = {"llm_provider": "openai", "api_key": "", "quick_think_llm": "gpt-4o"}
        assert _probe_runtime_config(cfg_no_key) == {"status": "skipped", "reason": "missing_model_or_key"}

    def test_probe_success_contract(self):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = "OK"
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client):
            cfg = {
                "llm_provider": "openai",
                "api_key": "sk-valid-key",
                "quick_think_llm": "gpt-4o-mini",
            }
            res = _probe_runtime_config(cfg)
            assert res["status"] == "ok"
            assert res["model"] == "gpt-4o-mini"
            assert res["preview"] == "OK"

    def test_probe_auth_failure_401_sanitized(self):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("Error code: 401 - {'error': {'message': 'Invalid API Key sk-secret-12345'}}")
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client):
            cfg = {
                "llm_provider": "openai",
                "api_key": "sk-secret-12345",
                "quick_think_llm": "gpt-4o-mini",
            }
            with pytest.raises(HTTPException) as exc_info:
                _probe_runtime_config(cfg)

            assert exc_info.value.status_code == 400
            assert "模型 Key 验证失败" in exc_info.value.detail
            assert "sk-secret-12345" not in exc_info.value.detail
            assert "401" in exc_info.value.detail

    def test_probe_model_not_found_404_sanitized(self):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception(
            "404 Not Found: model nonexistent does not exist at https://api.openai.com/v1/chat/completions?key=AIzaSecret404"
        )
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client):
            cfg = {
                "llm_provider": "openai",
                "api_key": "sk-test",
                "quick_think_llm": "nonexistent",
            }
            with pytest.raises(HTTPException) as exc_info:
                _probe_runtime_config(cfg)

            assert exc_info.value.status_code == 400
            assert "404" in exc_info.value.detail
            assert "AIzaSecret404" not in exc_info.value.detail
            assert "[REDACTED]" in exc_info.value.detail

    def test_probe_network_timeout_sanitized(self):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = httpx.ConnectTimeout(
            "ConnectTimeout to http://admin:super_secret_pwd@internal-gw:8080 timed out"
        )
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client):
            cfg = {
                "llm_provider": "openai",
                "api_key": "sk-test",
                "quick_think_llm": "gpt-4o-mini",
            }
            with pytest.raises(HTTPException) as exc_info:
                _probe_runtime_config(cfg)

            assert exc_info.value.status_code == 400
            assert "超时或网络不可达" in exc_info.value.detail
            assert "super_secret_pwd" not in exc_info.value.detail
            assert "[REDACTED_USER]:[REDACTED_PASS]@" in exc_info.value.detail

    def test_probe_proxy_routing_sanitized(self):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = LLMProxyRoutingError(
            base_url="http://192.168.1.100:8000/v1?token=tok-secret",
            proxy_url="http://127.0.0.1:7897",
            repair_command="export no_proxy=$no_proxy,192.168.1.100",
        )
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client):
            cfg = {
                "llm_provider": "openai",
                "api_key": "sk-test",
                "quick_think_llm": "gpt-4o-mini",
            }
            with pytest.raises(HTTPException) as exc_info:
                _probe_runtime_config(cfg)

            assert exc_info.value.status_code == 400
            assert "代理可达性自检失败" in exc_info.value.detail
            assert "tok-secret" not in exc_info.value.detail

    def test_probe_comprehensive_credential_redaction_in_detail_and_logs(self, caplog):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        sensitive_raw = (
            "Exception: Authorization: Bearer my-secret-token-xyz "
            "Cookie: session=top_secret_cookie "
            "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw== "
            "sk-proj-9876543210 "
            "?api_key=sk-query-secret&password=hidden_pwd"
        )
        mock_llm.invoke.side_effect = RuntimeError(sensitive_raw)
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client):
            cfg = {
                "llm_provider": "openai",
                "api_key": "sk-test",
                "quick_think_llm": "gpt-4o-mini",
            }
            with pytest.raises(HTTPException) as exc_info:
                _probe_runtime_config(cfg)

            detail = exc_info.value.detail
            assert "my-secret-token-xyz" not in detail
            assert "top_secret_cookie" not in detail
            assert "dXNlcjpwYXNzd29yZDEyMw==" not in detail
            assert "sk-proj-9876543210" not in detail
            assert "sk-query-secret" not in detail
            assert "hidden_pwd" not in detail

            # Check logged warning does not leak secrets either
            log_text = caplog.text
            assert "my-secret-token-xyz" not in log_text
            assert "top_secret_cookie" not in log_text
            assert "dXNlcjpwYXNzd29yZDEyMw==" not in log_text
            assert "sk-proj-9876543210" not in log_text
            assert "sk-query-secret" not in log_text
            assert "hidden_pwd" not in log_text


# ---------------------------------------------------------------------------
# 2. Runtime Warmup Tests (_invoke_runtime_warmup)
# ---------------------------------------------------------------------------

class TestInvokeRuntimeWarmup:
    """Tests for _invoke_runtime_warmup."""

    def test_warmup_raises_when_no_models_configured(self):
        with patch("api.services.role_routing_service.resolve_all_roles", return_value={}):
            with pytest.raises(HTTPException) as exc_info:
                _invoke_runtime_warmup({}, "hi", "user-1")
            assert exc_info.value.status_code == 400
            assert "请先配置至少一个可用模型" in exc_info.value.detail

    def test_warmup_success_single_model(self):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = "Warmup reply OK"
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client), \
             patch("api.database.get_db_ctx") as mock_db_ctx:
            mock_db_ctx.return_value.__enter__.return_value = MagicMock()
            cfg = {
                "llm_provider": "openai",
                "quick_think_llm": "gpt-4o-mini",
                "api_key": "sk-test",
            }
            results = _invoke_runtime_warmup(cfg, "ping", "user-1")
            assert len(results) == 1
            assert results[0]["model"] == "gpt-4o-mini"
            assert results[0]["content"] == "Warmup reply OK"
            assert results[0]["error"] is None

    def test_warmup_all_failed_raises_400_with_sanitized_errors(self, caplog):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception(
            "401 Unauthorized with Bearer secret-bearer-abc and Cookie: uid=secret-cookie"
        )
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client), \
             patch("api.database.get_db_ctx") as mock_db_ctx:
            mock_db_ctx.return_value.__enter__.return_value = MagicMock()
            cfg = {
                "llm_provider": "openai",
                "quick_think_llm": "gpt-4o-mini",
                "api_key": "sk-test",
            }
            with pytest.raises(HTTPException) as exc_info:
                _invoke_runtime_warmup(cfg, "ping", "user-1")

            assert exc_info.value.status_code == 400
            assert "模型 warmup 失败" in exc_info.value.detail
            assert "secret-bearer-abc" not in exc_info.value.detail
            assert "secret-cookie" not in exc_info.value.detail
            assert "secret-bearer-abc" not in caplog.text

    def test_warmup_partial_success_records_sanitized_error(self):
        """When one model succeeds and another fails, results include both without raising 400."""
        call_count = 0

        def _fake_create_llm_client(provider, model, **kwargs):
            nonlocal call_count
            client = MagicMock()
            llm = MagicMock()
            if model == "gpt-4o-mini":
                llm.invoke.return_value = "quick ok"
            else:
                llm.invoke.side_effect = Exception(
                    "404 Not Found for deep model at https://upstream/v1?token=tok-secret-404"
                )
            client.get_llm.return_value = llm
            return client

        with patch("tradingagents.llm_clients.create_llm_client", side_effect=_fake_create_llm_client), \
             patch("api.database.get_db_ctx") as mock_db_ctx:
            mock_db_ctx.return_value.__enter__.return_value = MagicMock()
            cfg = {
                "llm_provider": "openai",
                "quick_think_llm": "gpt-4o-mini",
                "deep_think_llm": "gpt-4o",
                "api_key": "sk-test",
            }
            results = _invoke_runtime_warmup(cfg, "ping", "user-1")
            assert len(results) == 2

            success_item = next(r for r in results if r["model"] == "gpt-4o-mini")
            assert success_item["content"] == "quick ok"
            assert success_item["error"] is None

            fail_item = next(r for r in results if r["model"] == "gpt-4o-warmup" or r["model"] == "gpt-4o")
            assert fail_item["content"] is None
            assert fail_item["error"] is not None
            assert "404" in fail_item["error"]
            assert "tok-secret-404" not in fail_item["error"]


# ---------------------------------------------------------------------------
# 3. Role-level Provider and Base URL Inheritance Tests
# ---------------------------------------------------------------------------

class TestRoleProviderAndBaseUrlInheritance:
    """Tests for heterogeneous vs homogeneous role base_url inheritance."""

    def test_warmup_role_inheritance_contract(self):
        """In _invoke_runtime_warmup:
        - Homogeneous role inherits global custom base_url
        - Heterogeneous role does NOT inherit global custom base_url
        - Role with explicit base_url keeps its explicit base_url
        """
        captured_client_calls = []

        def _recording_create_client(provider, model, base_url=None, **kwargs):
            captured_client_calls.append({
                "provider": provider,
                "model": model,
                "base_url": base_url,
            })
            client = MagicMock()
            client.get_llm.return_value.invoke.return_value = "mock OK"
            return client

        mock_resolved_roles = {
            "openai_role": {
                "provider_type": "openai",
                "model_name": "qwen-plus",
                "base_url": None,
            },
            "anthropic_role": {
                "provider_type": "anthropic",
                "model_name": "claude-3-5-sonnet-20241022",
                "base_url": None,
            },
            "custom_anthropic_role": {
                "provider_type": "anthropic",
                "model_name": "claude-3-5-sonnet-20241022",
                "base_url": "https://my-anthropic-proxy.internal/v1",
            },
        }

        with patch("tradingagents.llm_clients.create_llm_client", side_effect=_recording_create_client), \
             patch("api.database.get_db_ctx") as mock_db_ctx, \
             patch("api.services.role_routing_service.resolve_all_roles", return_value=mock_resolved_roles):
            mock_db_ctx.return_value.__enter__.return_value = MagicMock()

            cfg = {
                "llm_provider": "openai",
                "backend_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "quick_think_llm": "gpt-4o-mini",
                "api_key": "sk-test",
            }
            results = _invoke_runtime_warmup(cfg, "ping", "user-1")
            assert len(results) >= 3

            calls_by_model = {c["model"]: c for c in captured_client_calls}

            # 1. Homogeneous role (openai_role: qwen-plus) inherited global custom base_url
            assert calls_by_model["qwen-plus"]["provider"] == "openai"
            assert calls_by_model["qwen-plus"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

            # 2. Heterogeneous role (anthropic_role) did NOT inherit OpenAI custom base_url
            # Note: custom_anthropic_role has explicit base_url, so anthropic_role without base_url gets None
            anthropic_calls = [c for c in captured_client_calls if c["provider"] == "anthropic"]
            none_url_call = next(c for c in anthropic_calls if c["base_url"] is None)
            assert none_url_call["base_url"] is None

            # 3. Explicit role keeps its explicit base_url
            explicit_call = next(c for c in anthropic_calls if c["base_url"] == "https://my-anthropic-proxy.internal/v1")
            assert explicit_call["base_url"] == "https://my-anthropic-proxy.internal/v1"

    def test_trading_graph_role_client_construction_inheritance(self):
        """In TradingAgentsGraph.__init__:
        - Homogeneous role inherits self.config['backend_url']
        - Heterogeneous role does NOT inherit self.config['backend_url']
        - Role with explicit base_url keeps it
        """
        captured_calls = []

        class _Recorder:
            def __call__(self, provider, model, base_url=None, **kwargs):
                captured_calls.append({"provider": provider, "model": model, "base_url": base_url})
                client = MagicMock()
                client.get_llm.return_value = MagicMock()
                return client

        recorder = _Recorder()

        # Build resolved_roles covering ALL_ROLES
        resolved_roles = {}
        for role in role_routing_service.ALL_ROLES:
            if role == "market":
                # Heterogeneous provider without base_url
                resolved_roles[role] = {
                    "role_key": role,
                    "provider_type": "anthropic",
                    "model_name": "claude-3-5-haiku-20241022",
                    "base_url": None,
                }
            elif role == "social":
                # Heterogeneous provider with explicit base_url
                resolved_roles[role] = {
                    "role_key": role,
                    "provider_type": "anthropic",
                    "model_name": "claude-3-5-haiku-20241022",
                    "base_url": "https://custom-anthropic-gw.internal/v1",
                }
            else:
                # Homogeneous provider without base_url
                resolved_roles[role] = {
                    "role_key": role,
                    "provider_type": "openai",
                    "model_name": "gpt-4o-mini",
                    "base_url": None,
                }

        config = {
            "project_dir": tempfile.mkdtemp(prefix="ta-graph-wiring-"),
            "llm_provider": "openai",
            "backend_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "quick_think_llm": "gpt-4o-mini",
            "deep_think_llm": "gpt-4o",
            "api_key": "sk-test",
            "user_id": "user-test",
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
            "max_recur_limit": 5,
        }

        patches = (
            patch("tradingagents.graph.trading_graph.create_llm_client", recorder),
            patch("tradingagents.graph.trading_graph.FinancialSituationMemory"),
            patch("tradingagents.graph.trading_graph.GraphSetup"),
            patch("tradingagents.graph.trading_graph.ConditionalLogic"),
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.ToolNode"),
            patch("api.services.role_routing_service.resolve_all_roles", return_value=resolved_roles),
            patch("api.database.get_db_ctx"),
        )

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            graph = TradingAgentsGraph(config=config, data_collector=MagicMock())

        # Verify calls recorded
        assert len(captured_calls) >= len(role_routing_service.ALL_ROLES)

        # 1. Heterogeneous role 'market' (anthropic with no base_url) must NOT inherit OpenAI custom base_url
        market_calls = [c for c in captured_calls if c["provider"] == "anthropic" and c["model"] == "claude-3-5-haiku-20241022"]
        market_isolated = next(c for c in market_calls if c["base_url"] is None)
        assert market_isolated["base_url"] is None

        # 2. Heterogeneous role 'social' with explicit base_url keeps its explicit base_url
        social_call = next(c for c in market_calls if c["base_url"] == "https://custom-anthropic-gw.internal/v1")
        assert social_call["base_url"] == "https://custom-anthropic-gw.internal/v1"

        # 3. Homogeneous roles (e.g. news) inherit global custom base_url
        openai_role_calls = [c for c in captured_calls if c["provider"] == "openai"]
        assert any(c["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1" for c in openai_role_calls)


# ---------------------------------------------------------------------------
# 4. Host Proxy Environment Contamination Tests
# ---------------------------------------------------------------------------

class TestHostProxyEnvironmentContamination:
    """Tests guaranteeing that host proxy environment variables do not bypass fail-closed guards."""

    def test_proxy_guard_triggers_on_literal_ip_and_probe_surfaces_error(self, monkeypatch):
        # Contaminate host environment with forward proxy
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:7897")
        monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")
        monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1080")
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)

        # Literal IP target that would be routed through proxy
        cfg = {
            "llm_provider": "openai",
            "backend_url": "http://100.65.130.33:8317/v1",
            "quick_think_llm": "gpt-4o-mini",
            "api_key": "sk-test",
        }

        # Probe should fail-closed with 400 and proxy routing failure message
        with pytest.raises(HTTPException) as exc_info:
            _probe_runtime_config(cfg)

        assert exc_info.value.status_code == 400
        assert "代理可达性自检失败" in exc_info.value.detail or "proxy" in exc_info.value.detail.lower()

    def test_proxy_guard_passes_when_literal_ip_is_in_no_proxy(self, monkeypatch):
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:7897")
        monkeypatch.setenv("no_proxy", "100.65.130.33,localhost,127.0.0.1")

        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = "OK"
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client):
            cfg = {
                "llm_provider": "openai",
                "backend_url": "http://100.65.130.33:8317/v1",
                "quick_think_llm": "gpt-4o-mini",
                "api_key": "sk-test",
            }
            res = _probe_runtime_config(cfg)
            assert res["status"] == "ok"
            assert res["preview"] == "OK"


# ---------------------------------------------------------------------------
# 5. FastAPI End-to-End HTTP Contract Tests
# ---------------------------------------------------------------------------

class TestHttpRuntimeWiringIntegration:
    """HTTP endpoint integration tests ensuring responses are sanitized."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from api.main import app
        from fastapi.testclient import TestClient
        from tests.test_api_smoke import _auth_unique
        self.client = TestClient(app, raise_server_exceptions=False)
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_patch_config_probe_auth_failure_sanitized_response(self):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception(
            "401 Unauthorized: Bearer super-secret-jwt-token-123456"
        )
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client), \
             patch("api.main._run_config_warmup"):
            r = self.client.patch("/v1/config", headers=self.headers, json={
                "llm_provider": "openai",
                "quick_think_llm": "gpt-4o-mini",
                "api_key": "sk-secret-probe-key",
            })
            assert r.status_code == 400
            detail = r.json().get("detail", "")
            assert "模型 Key 验证失败" in detail
            assert "super-secret-jwt-token-123456" not in detail
            assert "sk-secret-probe-key" not in detail

    def test_patch_config_probe_404_failure_sanitized_response(self):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception(
            "404 Not Found at https://api.moonshot.cn/v1/chat/completions?key=AIzaSecretMoonshot: model moonshot-v99 does not exist"
        )
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client), \
             patch("api.main._run_config_warmup"):
            r = self.client.patch("/v1/config", headers=self.headers, json={
                "llm_provider": "openai",
                "backend_url": "https://api.moonshot.cn/v1",
                "quick_think_llm": "moonshot-v99",
                "api_key": "sk-secret-probe-key",
            })
            assert r.status_code == 400
            detail = r.json().get("detail", "")
            assert "404" in detail
            assert "AIzaSecretMoonshot" not in detail
            assert "sk-secret-probe-key" not in detail

    def test_post_config_warmup_failure_sanitized_response(self):
        mock_client = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception(
            "Connection failed with Cookie: auth_token=secret_cookie_token and Basic dXNlcjpzZWNyZXQ="
        )
        mock_client.get_llm.return_value = mock_llm

        with patch("tradingagents.llm_clients.create_llm_client", return_value=mock_client), \
             patch("api.database.get_db_ctx") as mock_db:
            mock_db.return_value.__enter__.return_value = MagicMock()
            r = self.client.post("/v1/config/warmup", headers=self.headers, json={
                "quick_think_llm": "gpt-4o-mini",
                "prompt": "ping",
            })
            assert r.status_code == 400
            detail = r.json().get("detail", "")
            assert "模型 warmup 失败" in detail
            assert "secret_cookie_token" not in detail
            assert "dXNlcjpzZWNyZXQ=" not in detail