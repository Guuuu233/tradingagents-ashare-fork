"""Contract tests for LLM model validation policy and offline constructor behaviors (P2-55).

Guarantees:
1. 100% offline execution — zero real provider calls, zero database writes.
2. Advisory validation policy:
   - Known models on standard endpoints match the static catalog.
   - Custom OpenAI-compatible endpoints (e.g. DashScope qwen-plus) permit uncataloged models.
   - Permissive providers (Ollama, OpenRouter) permit dynamic model names.
   - Uncataloged models on standard endpoints produce non-blocking advisory warnings.
3. Discovery integration: dynamic model lists confirm models and bypass catalog constraints.
4. Role-level configuration: independent per-role validation without cross-role leakage.
5. Constructor & fake transport: clients construct correctly without network; get_llm()
   does NOT block uncataloged models at startup (no premature hard gate).
6. Failure classification: typed categorisation of 401, 404, 429, proxy routing, and timeout.
7. Backward compatibility: existing validate_model() functions continue to work as expected.
"""

from unittest.mock import MagicMock
import httpx
import pytest

from tradingagents.llm_clients import (
    FailureCategory,
    LLMProxyRoutingError,
    ModelValidationResult,
    ModelValidationStatus,
    classify_llm_failure,
    create_llm_client,
    evaluate_model_policy,
    evaluate_role_configurations,
    is_custom_base_url,
    validate_model,
)
from tradingagents.llm_clients.anthropic_client import AnthropicClient
from tradingagents.llm_clients.google_client import GoogleClient
from tradingagents.llm_clients.openai_client import OpenAIClient, UnifiedChatOpenAI


@pytest.fixture(autouse=True)
def _isolate_proxy_env(monkeypatch):
    """Isolate tests from host proxy environment variables (especially SOCKS proxies)."""
    for k in (
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ):
        monkeypatch.delenv(k, raising=False)


class TestAdvisoryCatalog:
    """Tests for static advisory catalog matching and unknown model advisory semantics."""

    @pytest.mark.parametrize(
        "provider,model",
        [
            ("openai", "gpt-4o"),
            ("openai", "gpt-4o-mini"),
            ("openai", "gpt-5"),
            ("openai", "gpt-5-mini"),
            ("openai", "o1"),
            ("openai", "o3-mini"),
            ("anthropic", "claude-opus-4-5"),
            ("anthropic", "claude-sonnet-4-5"),
            ("anthropic", "claude-3-7-sonnet-20250219"),
            ("google", "gemini-2.5-pro"),
            ("google", "gemini-2.5-flash"),
            ("google", "gemini-3-pro-preview"),
            ("xai", "grok-4"),
            ("deepseek", "deepseek-chat"),
            ("deepseek", "deepseek-reasoner"),
        ],
    )
    def test_known_catalog_models_matched(self, provider: str, model: str):
        result = evaluate_model_policy(provider, model)
        assert result.status == ModelValidationStatus.CATALOG_MATCHED
        assert result.is_supported is True
        assert result.is_in_catalog is True
        assert result.is_advisory is False
        assert result.provider == provider
        assert result.model == model

    def test_empty_model_name_rejected(self):
        result = evaluate_model_policy("openai", "")
        assert result.status == ModelValidationStatus.EMPTY_MODEL
        assert result.is_supported is False
        assert result.is_in_catalog is False
        assert "cannot be empty" in result.message

        result_whitespace = evaluate_model_policy("openai", "   ")
        assert result_whitespace.status == ModelValidationStatus.EMPTY_MODEL
        assert result_whitespace.is_supported is False

    def test_uncataloged_model_on_standard_endpoint_is_advisory_not_hard_block(self):
        """Unrecognized model on standard endpoint produces advisory warning, but remains supported by default."""
        result = evaluate_model_policy("openai", "gpt-future-nonexistent-model-2027")
        assert result.status == ModelValidationStatus.ADVISORY_UNRECOGNIZED
        # Advisory: does NOT hard-block runtime execution unless strict mode requested
        assert result.is_supported is True
        assert result.is_in_catalog is False
        assert result.is_advisory is True
        assert "not in static advisory catalog" in result.message

    def test_uncataloged_model_in_strict_mode_reports_unsupported(self):
        """When strict validation is explicitly requested (e.g. strict pre-check), unsupported is returned."""
        result = evaluate_model_policy("openai", "unknown-model", strict=True)
        assert result.status == ModelValidationStatus.ADVISORY_UNRECOGNIZED
        assert result.is_supported is False
        assert result.is_advisory is True

    def test_unknown_provider_strict_and_non_strict_semantics(self):
        """Unknown provider under strict=True must return ADVISORY_UNRECOGNIZED (not ALLOWED) and is_supported=False."""
        strict_res = evaluate_model_policy("unknown_provider_xyz", "some-model", strict=True)
        assert strict_res.status == ModelValidationStatus.ADVISORY_UNRECOGNIZED
        assert "ALLOWED" not in strict_res.status.name
        assert strict_res.is_supported is False
        assert strict_res.is_advisory is True

        permissive_res = evaluate_model_policy("unknown_provider_xyz", "some-model", strict=False)
        assert permissive_res.status == ModelValidationStatus.CUSTOM_ENDPOINT_ALLOWED
        assert permissive_res.is_supported is True


class TestCustomEndpointAndCompatibility:
    """Tests for custom OpenAI-compatible endpoints, proxies, and non-default base URLs."""

    @pytest.mark.parametrize(
        "custom_url",
        [
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://api.moonshot.cn/v1",
            "https://api.baichuan-ai.com/v1",
            "http://100.65.130.33:8317/v1",
            "http://localhost:8000/v1",
        ],
    )
    def test_custom_endpoint_allows_uncataloged_models(self, custom_url: str):
        """OpenAI-compatible models like qwen-plus or moonshot-v1 are permitted on custom base_urls."""
        result = evaluate_model_policy("openai", "qwen-plus", base_url=custom_url)
        assert result.status == ModelValidationStatus.CUSTOM_ENDPOINT_ALLOWED
        assert result.is_supported is True
        assert result.is_in_catalog is False
        assert result.is_advisory is True
        assert custom_url in result.message

    def test_custom_endpoint_with_catalog_model_reports_matched(self):
        """If a known model is used against a proxy or mirror, catalog match is acknowledged."""
        result = evaluate_model_policy(
            "openai", "gpt-4o", base_url="http://internal-ai-gateway.local/v1"
        )
        assert result.status == ModelValidationStatus.CATALOG_MATCHED
        assert result.is_supported is True
        assert result.is_in_catalog is True
        assert result.is_advisory is False

    def test_is_custom_base_url_detection(self):
        assert is_custom_base_url("openai", None) is False
        assert is_custom_base_url("openai", "") is False
        assert is_custom_base_url("openai", "https://api.openai.com/v1") is False
        assert is_custom_base_url("openai", "https://api.openai.com/v1/") is False
        assert is_custom_base_url("openai", "https://api.openai.com") is False
        assert is_custom_base_url("anthropic", "https://api.anthropic.com") is False
        assert is_custom_base_url("anthropic", "https://api.anthropic.com/v1") is False
        assert is_custom_base_url("deepseek", "https://api.deepseek.com") is False
        # Google official standard endpoints
        assert is_custom_base_url("google", "https://generativelanguage.googleapis.com") is False
        assert is_custom_base_url("google", "https://generativelanguage.googleapis.com/v1beta") is False
        assert is_custom_base_url("google", "https://generativelanguage.googleapis.com/v1beta/") is False
        assert is_custom_base_url("google", "https://custom-proxy.local/v1") is True
        # Custom URLs
        assert is_custom_base_url("openai", "https://dashscope.aliyuncs.com/compatible-mode/v1") is True
        assert is_custom_base_url("openai", "http://127.0.0.1:8080/v1") is True


class TestPermissiveProviders:
    """Tests for Ollama and OpenRouter permissive handling."""

    @pytest.mark.parametrize(
        "model_name",
        [
            "llama3:latest",
            "llama3.1:70b-instruct-q4_K_M",
            "qwen2.5:32b",
            "deepseek-r1:14b",
            "custom-finetune:v2",
        ],
    )
    def test_ollama_accepts_any_model(self, model_name: str):
        result = evaluate_model_policy("ollama", model_name)
        assert result.status == ModelValidationStatus.PERMISSIVE_PROVIDER
        assert result.is_supported is True
        assert result.is_advisory is False

    @pytest.mark.parametrize(
        "model_name",
        [
            "meta-llama/llama-3.1-8b-instruct",
            "anthropic/claude-3.5-sonnet",
            "google/gemini-2.0-flash-001",
            "deepseek/deepseek-r1",
        ],
    )
    def test_openrouter_accepts_any_slug(self, model_name: str):
        result = evaluate_model_policy("openrouter", model_name)
        assert result.status == ModelValidationStatus.PERMISSIVE_PROVIDER
        assert result.is_supported is True
        assert result.is_advisory is False


class TestProviderDiscoveryIntegration:
    """Tests for dynamic model discovery (matching against /v1/models/fetch outputs)."""

    def test_discovered_models_confirm_uncataloged_model(self):
        """When dynamic discovery returns models, an uncataloged model is marked DISCOVERED_MATCH."""
        discovered = ["qwen-max", "qwen-plus", "qwen-turbo"]
        result = evaluate_model_policy(
            "openai",
            "qwen-plus",
            discovered_models=discovered,
        )
        assert result.status == ModelValidationStatus.DISCOVERED_MATCH
        assert result.is_supported is True
        assert result.is_advisory is False
        assert "confirmed via provider discovery" in result.message

    def test_discovered_models_mismatch_falls_back_to_advisory(self):
        """When dynamic discovery is present but model is absent, standard policy applies."""
        discovered = ["gpt-4o", "gpt-4o-mini"]
        result = evaluate_model_policy(
            "openai",
            "nonexistent-model",
            discovered_models=discovered,
        )
        assert result.status == ModelValidationStatus.ADVISORY_UNRECOGNIZED
        assert result.is_supported is True
        assert result.is_advisory is True


class TestRoleLevelConfigurations:
    """Tests for multi-role configuration resolution and independent validation."""

    def test_role_level_independent_evaluation(self):
        global_config = {
            "llm_provider": "openai",
            "backend_url": "https://api.openai.com/v1",
            "quick_think_llm": "gpt-4o-mini",
            "deep_think_llm": "gpt-4o",
        }
        resolved_roles = {
            "research_manager": {
                "model_name": "gpt-5",
                "provider_type": "openai",
            },
            "bull_researcher": {
                "model_name": "deepseek-r1",
                "provider_type": "deepseek",
            },
            "bear_researcher": {
                "model_name": "qwen-plus",
                "provider_type": "openai",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
            "risk_manager": {
                # Omits model_name, should fallback to global deep_think_llm
            },
        }

        results = evaluate_role_configurations(global_config, resolved_roles)

        assert results["research_manager"].status == ModelValidationStatus.CATALOG_MATCHED
        assert results["research_manager"].model == "gpt-5"

        assert results["bull_researcher"].status == ModelValidationStatus.ADVISORY_UNRECOGNIZED  # deepseek-r1 on standard deepseek URL
        assert results["bull_researcher"].is_supported is True  # Advisory: non-blocking

        assert results["bear_researcher"].status == ModelValidationStatus.CUSTOM_ENDPOINT_ALLOWED
        assert results["bear_researcher"].model == "qwen-plus"
        assert results["bear_researcher"].is_supported is True

        assert results["risk_manager"].status == ModelValidationStatus.CATALOG_MATCHED
        assert results["risk_manager"].model == "gpt-4o"

    def test_heterogeneous_roles_do_not_inherit_global_custom_base_url(self):
        """Heterogeneous providers (Anthropic, DeepSeek) must NOT inherit OpenAI custom base_url."""
        global_config = {
            "llm_provider": "openai",
            "backend_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "quick_think_llm": "gpt-4o-mini",
            "deep_think_llm": "gpt-4o",
        }
        resolved_roles = {
            "openai_agent": {
                "provider_type": "openai",
                "model_name": "qwen-plus",
                # No base_url specified -> should inherit global_base_url (same provider)
            },
            "anthropic_agent": {
                "provider_type": "anthropic",
                "model_name": "claude-3-5-sonnet-20241022",
                # No base_url specified -> must NOT inherit OpenAI custom base_url
            },
            "deepseek_agent": {
                "provider_type": "deepseek",
                "model_name": "deepseek-chat",
                # No base_url specified -> must NOT inherit OpenAI custom base_url
            },
            "custom_anthropic_agent": {
                "provider_type": "anthropic",
                "model_name": "claude-3-5-sonnet-20241022",
                "base_url": "https://my-anthropic-proxy.internal/v1",
                # Explicit base_url -> should keep its own base_url
            },
        }

        results = evaluate_role_configurations(global_config, resolved_roles)

        # 1. OpenAI agent inherits global_base_url (同厂商继承)
        assert results["openai_agent"].base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert results["openai_agent"].status == ModelValidationStatus.CUSTOM_ENDPOINT_ALLOWED

        # 2. Anthropic agent does NOT inherit OpenAI base_url (异构隔离)
        assert results["anthropic_agent"].base_url is None
        assert results["anthropic_agent"].status == ModelValidationStatus.CATALOG_MATCHED

        # 3. DeepSeek agent does NOT inherit OpenAI base_url (异构隔离)
        assert results["deepseek_agent"].base_url is None
        assert results["deepseek_agent"].status == ModelValidationStatus.CATALOG_MATCHED

        # 4. Custom Anthropic agent keeps its own base_url
        assert results["custom_anthropic_agent"].base_url == "https://my-anthropic-proxy.internal/v1"


class TestClientMethodsAndContract:
    """Tests for client validate_model() and evaluate_model() methods."""

    def test_openai_client_validate_model_custom_url(self):
        client = OpenAIClient(
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        assert client.validate_model() is True

        eval_res = client.evaluate_model()
        assert eval_res.status == ModelValidationStatus.CUSTOM_ENDPOINT_ALLOWED
        assert eval_res.is_supported is True

    def test_openai_client_validate_model_standard_url(self):
        client_valid = OpenAIClient(model="gpt-4o-mini")
        assert client_valid.validate_model() is True

        client_invalid = OpenAIClient(model="nonexistent-custom-123")
        assert client_invalid.validate_model() is False

    def test_anthropic_client_evaluate_model(self):
        client = AnthropicClient(model="claude-sonnet-4-5")
        assert client.validate_model() is True
        res = client.evaluate_model()
        assert res.status == ModelValidationStatus.CATALOG_MATCHED

    def test_google_client_evaluate_model(self):
        client = GoogleClient(model="gemini-2.5-pro")
        assert client.validate_model() is True
        res = client.evaluate_model()
        assert res.status == ModelValidationStatus.CATALOG_MATCHED

    def test_factory_creates_client_with_proper_validation(self):
        client = create_llm_client(
            provider="openai",
            model="qwen-max",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        assert client.validate_model() is True

        ollama_client = create_llm_client(
            provider="ollama",
            model="arbitrary-local-model",
        )
        assert ollama_client.validate_model() is True


class TestOfflineConstructorAndFakeTransport:
    """Tests for client construction and MockTransport invocation (strictly offline)."""

    def test_get_llm_does_not_block_uncataloged_models(self):
        """Proves that get_llm() does not enforce a hard blocking validation at initialization time."""
        client = OpenAIClient(
            model="uncataloged-experimental-model",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-fake-key",
        )
        # Must return UnifiedChatOpenAI instance without raising ValueError or HardGateError
        llm = client.get_llm()
        assert isinstance(llm, UnifiedChatOpenAI)
        assert llm.model_name == "uncataloged-experimental-model"

    def test_fake_transport_mock_invocation(self):
        """Constructs an OpenAIClient with httpx.MockTransport and executes mock invocation offline."""
        mock_response_json = {
            "id": "chatcmpl-test-fake",
            "object": "chat.completion",
            "created": 1726300000,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "offline mock answer"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }

        def fake_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=mock_response_json)

        fake_transport = httpx.MockTransport(fake_handler)
        fake_http_client = httpx.Client(transport=fake_transport)
        fake_async_client = httpx.AsyncClient(transport=fake_transport)

        client = OpenAIClient(
            model="gpt-4o-mini",
            api_key="sk-test-key",
            http_client=fake_http_client,
            http_async_client=fake_async_client,
        )
        llm = client.get_llm()
        res = llm.invoke("ping")
        assert res.content == "offline mock answer"


class TestFailureClassification:
    """Tests for classifying exceptions into typed failure categories."""

    def test_classify_auth_error_401(self):
        exc = Exception("Error code: 401 - {'error': {'message': 'Incorrect API key provided'}}")
        category, msg = classify_llm_failure(exc)
        assert category == FailureCategory.AUTH_FAILURE
        assert "401" in msg
        assert "API Key" in msg

    def test_classify_model_not_found_404(self):
        exc = Exception("Error code: 404 - {'error': {'message': 'The model `foo` does not exist'}}")
        category, msg = classify_llm_failure(exc)
        assert category == FailureCategory.MODEL_NOT_FOUND
        assert "404" in msg

    def test_classify_rate_limit_429(self):
        exc = Exception("Error code: 429 - {'error': {'message': 'Rate limit reached'}}")
        category, msg = classify_llm_failure(exc)
        assert category == FailureCategory.RATE_LIMIT
        assert "429" in msg

    def test_classify_proxy_routing_error(self):
        exc = LLMProxyRoutingError(
            base_url="http://100.65.130.33:8317/v1",
            proxy_url="http://127.0.0.1:7897",
            repair_command='export no_proxy="${no_proxy},100.65.130.33"',
        )
        category, msg = classify_llm_failure(exc)
        assert category == FailureCategory.PROXY_ROUTING
        assert "100.65.130.33" in msg

    def test_classify_network_timeout(self):
        exc = httpx.ConnectTimeout("ConnectTimeout to https://api.openai.com timed out")
        category, msg = classify_llm_failure(exc)
        assert category == FailureCategory.NETWORK_TIMEOUT
        assert "超时或网络不可达" in msg

    @pytest.mark.parametrize(
        "failure_maker,expected_category",
        [
            (
                lambda raw: RuntimeError(f"Unexpected invocation error: {raw}"),
                FailureCategory.UNKNOWN_ERROR,
            ),
            (
                lambda raw: Exception(f"404 Not Found: model nonexistent does not exist at {raw}"),
                FailureCategory.MODEL_NOT_FOUND,
            ),
            (
                lambda raw: httpx.ConnectTimeout(f"ConnectTimeout while requesting {raw}"),
                FailureCategory.NETWORK_TIMEOUT,
            ),
            (
                lambda raw: LLMProxyRoutingError(
                    base_url=f"http://100.65.130.33:8317/v1?info={raw}",
                    proxy_url="http://127.0.0.1:7897",
                    repair_command='export no_proxy="${no_proxy},100.65.130.33"',
                ),
                FailureCategory.PROXY_ROUTING,
            ),
        ],
    )
    def test_classify_llm_failure_sanitization_across_all_four_paths(
        self, failure_maker, expected_category
    ):
        """Strict contract: sk-, Bearer, Cookie, query parameters, and Basic Auth are redacted in all 4 paths."""
        raw_sensitive = (
            "https://testuser:supersecretpass@api.openai.com/v1/chat/completions"
            "?key=AIzaSyGoogleKey123&api_key=sk-paramkey456&token=tokAbc123&password=passWord456"
            " Headers: Authorization: Bearer my-secret-bearer-token-abcdef"
            " and Cookie: session_id=topsecretcookie123; tracking=xyz"
            " and direct key sk-test-secret-key-12345"
        )
        exc = failure_maker(raw_sensitive)
        category, msg = classify_llm_failure(exc)

        assert category == expected_category

        # 1. sk- API key assertions
        assert "sk-test-secret-key-12345" not in msg
        assert "sk-paramkey456" not in msg
        assert "sk-" not in msg

        # 2. Bearer token assertion
        assert "my-secret-bearer-token-abcdef" not in msg

        # 3. Cookie assertion
        assert "topsecretcookie123" not in msg

        # 4. URL query params assertions (key, api_key, token, password)
        assert "AIzaSyGoogleKey123" not in msg
        assert "tokAbc123" not in msg
        assert "passWord456" not in msg

        # 5. Basic Auth assertion
        assert "testuser:supersecretpass@" not in msg
        assert "supersecretpass" not in msg

        # Redaction placeholders should be present
        assert "[REDACTED" in msg

    def test_classify_llm_failure_sk_api_key_redaction(self):
        exc = Exception("Failed calling OpenAI with key sk-proj-1234567890abcdefghijklmn")
        _, msg = classify_llm_failure(exc)
        assert "sk-proj-1234567890abcdefghijklmn" not in msg
        assert "sk-" not in msg
        assert "[REDACTED_API_KEY]" in msg

    def test_classify_llm_failure_bearer_token_redaction(self):
        exc = Exception("Request Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 rejected")
        _, msg = classify_llm_failure(exc)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in msg
        assert "[REDACTED]" in msg

    def test_classify_llm_failure_cookie_redaction(self):
        exc = Exception("Error processing request with Cookie: session_token=secret987654; path=/")
        _, msg = classify_llm_failure(exc)
        assert "secret987654" not in msg
        assert "[REDACTED_COOKIE]" in msg

        exc_json = Exception("Headers: {'cookie': 'auth_session=top_secret_cookie_token'}")
        _, msg_json = classify_llm_failure(exc_json)
        assert "top_secret_cookie_token" not in msg_json
        assert "[REDACTED_COOKIE]" in msg_json

    def test_classify_llm_failure_url_sensitive_params_redaction(self):
        exc = Exception(
            "Failed GET https://api.service.com/v1/query?key=AIzaSecret1&api_key=sk-key2&token=token3&password=pass4"
        )
        _, msg = classify_llm_failure(exc)
        assert "AIzaSecret1" not in msg
        assert "sk-key2" not in msg
        assert "token3" not in msg
        assert "pass4" not in msg
        assert "[REDACTED]" in msg

    def test_classify_llm_failure_basic_auth_redaction(self):
        exc = Exception("Upstream connection to http://admin:very_secret_pwd@internal-gw:8080 failed")
        _, msg = classify_llm_failure(exc)
        assert "admin:very_secret_pwd@" not in msg
        assert "very_secret_pwd" not in msg
        assert "[REDACTED_USER]:[REDACTED_PASS]@" in msg


class TestBackwardCompatibility:
    """Tests guaranteeing that legacy validate_model function behaviors are fully preserved."""

    def test_legacy_validate_model_behavior(self):
        assert validate_model("openai", "gpt-4o-mini") is True
        assert validate_model("openai", "gpt-5") is True
        assert validate_model("openai", "nonexistent-model") is False
        assert validate_model("ollama", "anything") is True
        assert validate_model("openrouter", "anything") is True
        assert validate_model("unknown_provider_xyz", "anything") is True
        # Legacy behavior: uncataloged provider returns True even when model is empty
        assert validate_model("unknown_provider_xyz", "") is True
        # Custom base_url enables permissive behavior for compatible models
        assert validate_model(
            "openai", "qwen-plus", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        ) is True

    def test_legacy_validate_model_unknown_provider_empty_model_regression(self):
        """Regression test for DAV-918: legacy validate_model accepts empty model on uncataloged providers."""
        assert validate_model("unknown_provider_xyz", "") is True
        assert validate_model("unknown_provider_xyz", "   ") is True
        assert validate_model("ollama", "") is True
        assert validate_model("openrouter", "") is True
        # Known providers must still reject empty models
        assert validate_model("openai", "") is False
        assert validate_model("anthropic", "") is False
