"""Model name validators and validation policies for each provider.

Advisory validation policy:
- VALID_MODELS serves as a static advisory catalog, NOT a live capability proof.
- Custom OpenAI-compatible endpoints (e.g. DashScope, Moonshot, Baichuan, vLLM, local proxies)
  and permissive providers (Ollama, OpenRouter) are non-exhaustive and allowed by policy.
- Runtime get_llm() does not hard-block on uncataloged models; blocking validation occurs only
  upon explicit warmup failure or concrete upstream error (fail-closed on concrete error).
"""

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

VALID_MODELS = {
    "openai": [
        # GPT-5 series (2025)
        "gpt-5.2",
        "gpt-5.1",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        # GPT-4.1 series (2025)
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        # o-series reasoning models
        "o4-mini",
        "o3",
        "o3-mini",
        "o1",
        "o1-preview",
        # GPT-4o series (legacy but still supported)
        "gpt-4o",
        "gpt-4o-mini",
    ],
    "anthropic": [
        # Claude 4.5 series (2025)
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
        # Claude 4.x series
        "claude-opus-4-1-20250805",
        "claude-sonnet-4-20250514",
        # Claude 3.7 series
        "claude-3-7-sonnet-20250219",
        # Claude 3.5 series (legacy)
        "claude-3-5-haiku-20241022",
        "claude-3-5-sonnet-20241022",
    ],
    "google": [
        # Gemini 3 series (preview)
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        # Gemini 2.5 series
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        # Gemini 2.0 series
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ],
    "xai": [
        # Grok 4.1 series
        "grok-4-1-fast",
        "grok-4-1-fast-reasoning",
        "grok-4-1-fast-non-reasoning",
        # Grok 4 series
        "grok-4",
        "grok-4-0709",
        "grok-4-fast-reasoning",
        "grok-4-fast-non-reasoning",
    ],
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
    ],
}

STANDARD_BASE_URLS: Dict[str, Tuple[str, ...]] = {
    "openai": ("https://api.openai.com", "https://api.openai.com/v1"),
    "anthropic": ("https://api.anthropic.com", "https://api.anthropic.com/v1"),
    "xai": ("https://api.x.ai", "https://api.x.ai/v1"),
    "deepseek": ("https://api.deepseek.com", "https://api.deepseek.com/v1"),
    "openrouter": ("https://openrouter.ai", "https://openrouter.ai/api/v1"),
    "ollama": ("http://localhost:11434", "http://localhost:11434/v1"),
}


class ModelValidationStatus(str, Enum):
    """Status classification for model validation policy."""

    CATALOG_MATCHED = "catalog_matched"
    CUSTOM_ENDPOINT_ALLOWED = "custom_endpoint_allowed"
    PERMISSIVE_PROVIDER = "permissive_provider"
    DISCOVERED_MATCH = "discovered_match"
    ADVISORY_UNRECOGNIZED = "advisory_unrecognized"
    EMPTY_MODEL = "empty_model"


@dataclass(frozen=True)
class ModelValidationResult:
    """Result of model policy evaluation.

    Attributes:
        status: Detailed validation status category.
        is_supported: Whether model can proceed with execution (non-blocking).
        is_in_catalog: Whether model is recognized in the static catalog.
        is_advisory: Whether this result is an advisory notice (non-fatal).
        provider: Evaluated provider name.
        model: Evaluated model name.
        base_url: Base URL if specified.
        message: Human-readable explanation.
    """

    status: ModelValidationStatus
    is_supported: bool
    is_in_catalog: bool
    is_advisory: bool
    provider: str
    model: str
    base_url: Optional[str] = None
    message: str = ""


class FailureCategory(str, Enum):
    """Categorized failure types for LLM warmup and invocation."""

    AUTH_FAILURE = "auth_failure"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMIT = "rate_limit"
    PROXY_ROUTING = "proxy_routing"
    NETWORK_TIMEOUT = "network_timeout"
    UNKNOWN_ERROR = "unknown_error"


def is_custom_base_url(provider: str, base_url: Optional[str]) -> bool:
    """Check if base_url is a custom / proxy / compatible endpoint.

    Returns False if base_url is None, empty, or matches official standard endpoints.
    Returns True for custom endpoints (e.g. DashScope, Azure, Moonshot, internal proxy).
    """
    if not base_url:
        return False
    cleaned = base_url.strip().rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned_no_v1 = cleaned[:-3]
    else:
        cleaned_no_v1 = cleaned

    p = (provider or "").strip().lower()
    standards = STANDARD_BASE_URLS.get(p, ())
    for std in standards:
        std_clean = std.rstrip("/")
        if std_clean.endswith("/v1"):
            std_no_v1 = std_clean[:-3]
        else:
            std_no_v1 = std_clean
        if cleaned.lower() == std_clean.lower() or cleaned_no_v1.lower() == std_no_v1.lower():
            return False
    return True


def evaluate_model_policy(
    provider: str,
    model: str,
    base_url: Optional[str] = None,
    *,
    discovered_models: Optional[List[str]] = None,
    strict: bool = False,
) -> ModelValidationResult:
    """Evaluate model name against provider catalog, custom endpoints, and discovery.

    Advisory philosophy:
    - Never hard-blocks uncataloged models in default mode (strict=False),
      preventing breakage when new models launch or custom models are used.
    - Custom OpenAI-compatible endpoints (e.g. DashScope, vLLM) are automatically
      allowed with an advisory status.
    - Permissive providers (Ollama, OpenRouter) are automatically allowed.
    - If discovered_models is supplied (from /v1/models/fetch), matching models are confirmed.
    """
    prov = (provider or "").strip().lower()
    m = (model or "").strip()

    if not m:
        return ModelValidationResult(
            status=ModelValidationStatus.EMPTY_MODEL,
            is_supported=False,
            is_in_catalog=False,
            is_advisory=False,
            provider=prov,
            model=model,
            base_url=base_url,
            message="Model name cannot be empty.",
        )

    # 1. Permissive providers
    if prov in ("ollama", "openrouter"):
        return ModelValidationResult(
            status=ModelValidationStatus.PERMISSIVE_PROVIDER,
            is_supported=True,
            is_in_catalog=False,
            is_advisory=False,
            provider=prov,
            model=m,
            base_url=base_url,
            message=f"Provider '{prov}' accepts dynamic model names.",
        )

    # 2. Dynamic discovery match
    if discovered_models and m in discovered_models:
        in_cat = m in VALID_MODELS.get(prov, [])
        return ModelValidationResult(
            status=ModelValidationStatus.DISCOVERED_MATCH,
            is_supported=True,
            is_in_catalog=in_cat,
            is_advisory=False,
            provider=prov,
            model=m,
            base_url=base_url,
            message=f"Model '{m}' confirmed via provider discovery.",
        )

    # 3. Custom endpoint check
    if is_custom_base_url(prov, base_url):
        in_cat = m in VALID_MODELS.get(prov, [])
        if in_cat:
            return ModelValidationResult(
                status=ModelValidationStatus.CATALOG_MATCHED,
                is_supported=True,
                is_in_catalog=True,
                is_advisory=False,
                provider=prov,
                model=m,
                base_url=base_url,
                message=f"Model '{m}' matches static catalog on custom endpoint.",
            )
        return ModelValidationResult(
            status=ModelValidationStatus.CUSTOM_ENDPOINT_ALLOWED,
            is_supported=True,
            is_in_catalog=False,
            is_advisory=True,
            provider=prov,
            model=m,
            base_url=base_url,
            message=f"Model '{m}' is allowed on custom compatible endpoint '{base_url}'.",
        )

    # 4. Static catalog check
    known = VALID_MODELS.get(prov)
    if known is not None:
        if m in known:
            return ModelValidationResult(
                status=ModelValidationStatus.CATALOG_MATCHED,
                is_supported=True,
                is_in_catalog=True,
                is_advisory=False,
                provider=prov,
                model=m,
                base_url=base_url,
                message=f"Model '{m}' matches static advisory catalog for '{prov}'.",
            )
        # Unrecognized on standard endpoint
        is_sup = False if strict else True
        return ModelValidationResult(
            status=ModelValidationStatus.ADVISORY_UNRECOGNIZED,
            is_supported=is_sup,
            is_in_catalog=False,
            is_advisory=True,
            provider=prov,
            model=m,
            base_url=base_url,
            message=f"Model '{m}' is not in static advisory catalog for provider '{prov}'.",
        )

    # 5. Unknown provider
    is_sup = False if strict else True
    return ModelValidationResult(
        status=ModelValidationStatus.CUSTOM_ENDPOINT_ALLOWED,
        is_supported=is_sup,
        is_in_catalog=False,
        is_advisory=True,
        provider=prov,
        model=m,
        base_url=base_url,
        message=f"Provider '{prov}' has no static catalog; permissive policy applied.",
    )


def validate_model(provider: str, model: str, base_url: Optional[str] = None) -> bool:
    """Check if model name is valid for the given provider.

    Backward-compatible function returning bool:
    - If base_url is a custom endpoint, permits uncataloged models (True).
    - For ollama, openrouter, or uncataloged providers, returns True.
    - For standard endpoints of known providers, checks static catalog.
    """
    res = evaluate_model_policy(provider, model, base_url, strict=True)
    if res.status == ModelValidationStatus.ADVISORY_UNRECOGNIZED:
        return False
    if res.status == ModelValidationStatus.EMPTY_MODEL:
        return False
    return True


def evaluate_role_configurations(
    global_config: Dict[str, Any],
    resolved_roles: Dict[str, Dict[str, Any]],
    *,
    strict: bool = False,
) -> Dict[str, ModelValidationResult]:
    """Evaluate model validation for all configured roles independently.

    Ensures that role-level custom overrides (model_name, provider_type, base_url)
    are validated against their specific provider and endpoint without cross-role leakage,
    falling back to global config (deep_think_llm / quick_think_llm) when unspecified.
    """
    global_provider = str(global_config.get("llm_provider") or "openai")
    global_base_url = global_config.get("backend_url")
    global_quick = str(global_config.get("quick_think_llm") or "gpt-4o-mini")
    global_deep = str(global_config.get("deep_think_llm") or "gpt-4o")

    results: Dict[str, ModelValidationResult] = {}
    for role_key, role_cfg in resolved_roles.items():
        prov = str(role_cfg.get("provider_type") or global_provider)
        b_url = role_cfg.get("base_url")
        if not b_url:
            # Only inherit global_base_url if role uses the same provider as global,
            # or if global_base_url is a genuine custom proxy/endpoint (not a standard endpoint of global_provider)
            if prov == global_provider:
                b_url = global_base_url
            elif is_custom_base_url(global_provider, global_base_url):
                b_url = global_base_url
            else:
                b_url = None
        m_name = role_cfg.get("model_name")
        if not m_name:
            # Default tiers: research_manager and risk_manager default to deep tier
            is_deep = role_key in ("research_manager", "risk_manager")
            m_name = global_deep if is_deep else global_quick

        results[role_key] = evaluate_model_policy(
            provider=prov,
            model=str(m_name),
            base_url=b_url,
            strict=strict,
        )
    return results



def classify_llm_failure(exc: Exception) -> Tuple[FailureCategory, str]:
    """Classify an LLM invocation / warmup exception into typed category and actionable message.

    Supports OpenAI, Anthropic, Google, and Proxy routing errors without network calls.
    """
    detail = str(exc).strip()
    lowered = detail.lower()

    if "llmproxyroutingerror" in exc.__class__.__name__.lower() or (
        "no_proxy" in lowered and "proxy" in lowered
    ):
        return FailureCategory.PROXY_ROUTING, detail

    if (
        "401" in lowered
        or "authentication" in lowered
        or "authenticationerror" in lowered
        or "invalid api key" in lowered
        or "unauthorized" in lowered
    ):
        return (
            FailureCategory.AUTH_FAILURE,
            "模型 Key 验证失败：上游返回 401 Authentication Error，请检查 API Key 是否正确。",
        )

    if (
        "404" in lowered
        or "not_found" in lowered
        or "model_not_found" in lowered
        or "does not exist" in lowered
        or "no such model" in lowered
    ):
        return (
            FailureCategory.MODEL_NOT_FOUND,
            f"模型不存在或无权限访问（404）：{detail[:160]}",
        )

    if "429" in lowered or "rate_limit" in lowered or "quota" in lowered or "resource_exhausted" in lowered:
        return (
            FailureCategory.RATE_LIMIT,
            "上游服务限流或配额耗尽（429/ResourceExhausted），请稍后重试或检查账号额度。",
        )

    if (
        "timeout" in lowered
        or "connecterror" in lowered
        or "connection error" in lowered
        or "econnrefused" in lowered
        or "connection refused" in lowered
    ):
        return (
            FailureCategory.NETWORK_TIMEOUT,
            f"上游模型服务连接超时或网络不可达：{detail[:160]}",
        )

    return FailureCategory.UNKNOWN_ERROR, detail[:200] or "unknown error"
