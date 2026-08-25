"""Read-only model tier warning and multi-model stance parity check module (P2-G1).

Specification (10.2):
- Strictly read-only: does NOT modify role_bindings, model selections, or user configurations.
- Checks bull/bear models for tier alignment during investment debate.
- Behavior:
  1. bull/bear 同一模型 -> warning: "同模自我辩论"
  2. 不同模型但无法证明同档 -> warning: "无法证明同档"
  3. 明显跨档 -> warning: "明显跨档"
  4. 同档异模 -> 校验通过，无 warning
- Output metadata (model_id × stance, provider, warnings) attached into result_data.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"

# Known model tier classifications
_DEEP_TIER_PATTERNS = [
    r"^o[1-9](-|$)",  # o1, o1-preview, o1-mini, o3, o3-mini, o4-mini
    r"deepseek-r1",
    r"deepseek-reasoner",
    r"qwq",
    r"claude-.*sonnet",
    r"claude-.*opus",
    r"gpt-4o($|-[0-9]|-(realtime|audio))",
    r"gpt-4\.[0-9]+($|-[0-9])",
    r"gpt-5(\.[0-9]+)?($|-[0-9])",
    r"gpt-4-turbo",
    r"^gpt-4($|-[0-9])",
    r"gemini-.*pro",
    r"qwen-max",
    r"qwen-2\.5-72b",
    r"deepseek-v3",
]

_QUICK_TIER_PATTERNS = [
    r"(^|[-_.])mini($|[-_.])",
    r"(^|[-_.])nano($|[-_.])",
    r"(^|[-_.])haiku($|[-_.])",
    r"(^|[-_.])flash($|[-_.])",
    r"(^|[-_.])lite($|[-_.])",
    r"(^|[-_.])turbo($|[-_.])",
    r"gpt-3\.5",
    r"deepseek-(chat|lite|coder)",
    r"qwen-(turbo|plus)",
    r"qwen-2\.5-(7b|14b|32b)",
    r"llama-?3\.[0-9]+-?[0-9]+b",
    r"mistral-?7b",
]


def infer_model_tier(
    model_name: Optional[str],
    explicit_tier: Optional[str] = None,
) -> Optional[str]:
    """Infer model tier ('quick' | 'deep') from explicit tier or model name.

    Returns:
        'quick' | 'deep' | None (None means unknown / unproven tier).
    """
    if explicit_tier and isinstance(explicit_tier, str):
        cleaned_tier = explicit_tier.strip().lower()
        if cleaned_tier in ("quick", "deep"):
            return cleaned_tier

    if not model_name or not isinstance(model_name, str):
        return None

    cleaned_name = model_name.strip().lower()
    if not cleaned_name or cleaned_name in ("unknown", "none", "null"):
        return None

    # Check deep/reasoning tier patterns first
    for pattern in _DEEP_TIER_PATTERNS:
        if re.search(pattern, cleaned_name):
            return "deep"

    # Check quick tier patterns
    for pattern in _QUICK_TIER_PATTERNS:
        if re.search(pattern, cleaned_name):
            return "quick"

    return None


def _is_bull_msg(speaker: str, stance: str) -> bool:
    s_clean = str(speaker or "").lower()
    st_clean = str(stance or "").lower()
    return "bull" in s_clean or "多" in s_clean or "bull" in st_clean or "多" in st_clean


def _is_bear_msg(speaker: str, stance: str) -> bool:
    s_clean = str(speaker or "").lower()
    st_clean = str(stance or "").lower()
    return "bear" in s_clean or "空" in s_clean or "bear" in st_clean or "空" in st_clean


def extract_stance_models_and_providers(
    source: Optional[Mapping[str, Any]] = None,
    role_resolved_configs: Optional[Mapping[str, Any]] = None,
) -> tuple[dict[str, Optional[str]], dict[str, Optional[str]], dict[str, Optional[str]]]:
    """Extract model_id, provider, and explicit tier by stance from source objects."""
    models: dict[str, Optional[str]] = {"bull": None, "bear": None, "manager": None}
    providers: dict[str, Optional[str]] = {"bull": None, "bear": None, "manager": None}
    tiers: dict[str, Optional[str]] = {"bull": None, "bear": None, "manager": None}

    # 1. From role_resolved_configs if present
    if isinstance(role_resolved_configs, Mapping):
        bull_cfg = role_resolved_configs.get("bull_researcher") or {}
        if isinstance(bull_cfg, Mapping):
            models["bull"] = bull_cfg.get("model_name")
            providers["bull"] = bull_cfg.get("provider_type") or bull_cfg.get("provider")
            tiers["bull"] = bull_cfg.get("tier")

        bear_cfg = role_resolved_configs.get("bear_researcher") or {}
        if isinstance(bear_cfg, Mapping):
            models["bear"] = bear_cfg.get("model_name")
            providers["bear"] = bear_cfg.get("provider_type") or bear_cfg.get("provider")
            tiers["bear"] = bear_cfg.get("tier")

        mgr_cfg = role_resolved_configs.get("research_manager") or {}
        if isinstance(mgr_cfg, Mapping):
            models["manager"] = mgr_cfg.get("model_name")
            providers["manager"] = mgr_cfg.get("provider_type") or mgr_cfg.get("provider")
            tiers["manager"] = mgr_cfg.get("tier")

    if not isinstance(source, Mapping):
        return models, providers, tiers

    # 2. From investment_debate_state.round_messages if available
    inv_state = source.get("investment_debate_state")
    if not isinstance(inv_state, Mapping):
        inv_state = source

    round_messages = inv_state.get("round_messages") or source.get("round_messages") or []
    if isinstance(round_messages, list):
        for msg in round_messages:
            if not isinstance(msg, Mapping):
                continue
            m_name = msg.get("model_name") or msg.get("model_id") or msg.get("model")
            p_name = msg.get("provider") or msg.get("provider_type")
            t_name = msg.get("tier")
            if m_name and isinstance(m_name, str) and m_name.strip():
                m_clean = m_name.strip()
                sp_key = str(msg.get("speaker_key") or msg.get("speaker") or "")
                st_val = str(msg.get("stance") or "")
                is_v = bool(msg.get("is_verdict") or "manager" in sp_key.lower() or "总监" in sp_key)
                if is_v:
                    if not models["manager"]:
                        models["manager"] = m_clean
                    if not providers["manager"] and p_name:
                        providers["manager"] = str(p_name).strip()
                    if not tiers["manager"] and t_name:
                        tiers["manager"] = str(t_name).strip()
                elif _is_bull_msg(sp_key, st_val):
                    if not models["bull"]:
                        models["bull"] = m_clean
                    if not providers["bull"] and p_name:
                        providers["bull"] = str(p_name).strip()
                    if not tiers["bull"] and t_name:
                        tiers["bull"] = str(t_name).strip()
                elif _is_bear_msg(sp_key, st_val):
                    if not models["bear"]:
                        models["bear"] = m_clean
                    if not providers["bear"] and p_name:
                        providers["bear"] = str(p_name).strip()
                    if not tiers["bear"] and t_name:
                        tiers["bear"] = str(t_name).strip()

    # 3. Direct mappings in source
    direct_models = (
        source.get("model_id_by_stance")
        or inv_state.get("model_id_by_stance")
        or source.get("role_models")
        or {}
    )
    if isinstance(direct_models, Mapping):
        if not models["bull"] and direct_models.get("bull"):
            models["bull"] = str(direct_models["bull"]).strip()
        if not models["bear"] and direct_models.get("bear"):
            models["bear"] = str(direct_models["bear"]).strip()
        if not models["manager"] and direct_models.get("manager"):
            models["manager"] = str(direct_models["manager"]).strip()

    direct_providers = (
        source.get("provider_by_stance")
        or inv_state.get("provider_by_stance")
        or source.get("role_providers")
        or {}
    )
    if isinstance(direct_providers, Mapping):
        if not providers["bull"] and direct_providers.get("bull"):
            providers["bull"] = str(direct_providers["bull"]).strip()
        if not providers["bear"] and direct_providers.get("bear"):
            providers["bear"] = str(direct_providers["bear"]).strip()
        if not providers["manager"] and direct_providers.get("manager"):
            providers["manager"] = str(direct_providers["manager"]).strip()

    # 4. Fallback to runtime_config defaults if still None
    if not models["bull"] and source.get("quick_think_llm"):
        models["bull"] = str(source["quick_think_llm"]).strip()
    if not models["bear"] and source.get("quick_think_llm"):
        models["bear"] = str(source["quick_think_llm"]).strip()
    if not models["manager"] and source.get("deep_think_llm"):
        models["manager"] = str(source["deep_think_llm"]).strip()
    if not providers["bull"] and source.get("llm_provider"):
        providers["bull"] = str(source["llm_provider"]).strip()
    if not providers["bear"] and source.get("llm_provider"):
        providers["bear"] = str(source["llm_provider"]).strip()
    if not providers["manager"] and source.get("llm_provider"):
        providers["manager"] = str(source["llm_provider"]).strip()

    return models, providers, tiers


def check_model_tier_warnings(
    result_data_or_config: Optional[Mapping[str, Any]] = None,
    *,
    bull_model: Optional[str] = None,
    bear_model: Optional[str] = None,
    manager_model: Optional[str] = None,
    bull_provider: Optional[str] = None,
    bear_provider: Optional[str] = None,
    manager_provider: Optional[str] = None,
    bull_tier: Optional[str] = None,
    bear_tier: Optional[str] = None,
    manager_tier: Optional[str] = None,
    role_resolved_configs: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Inspect bull/bear debate model configuration and return read-only tier warning metadata.

    Strictly read-only: does not alter user configuration or role bindings.
    """
    ext_models, ext_providers, ext_tiers = extract_stance_models_and_providers(
        result_data_or_config,
        role_resolved_configs=role_resolved_configs,
    )

    b_model = bull_model or ext_models.get("bull")
    s_model = bear_model or ext_models.get("bear")
    m_model = manager_model or ext_models.get("manager")

    b_provider = bull_provider or ext_providers.get("bull")
    s_provider = bear_provider or ext_providers.get("bear")
    m_provider = manager_provider or ext_providers.get("manager")

    b_tier_exp = bull_tier or ext_tiers.get("bull")
    s_tier_exp = bear_tier or ext_tiers.get("bear")
    m_tier_exp = manager_tier or ext_tiers.get("manager")

    # Infer tiers
    b_tier = infer_model_tier(b_model, explicit_tier=b_tier_exp)
    s_tier = infer_model_tier(s_model, explicit_tier=s_tier_exp)
    m_tier = infer_model_tier(m_model, explicit_tier=m_tier_exp)

    warnings: List[str] = []
    is_same_model = False
    is_same_tier = False
    is_cross_tier = False

    # Check presence
    if not b_model and not s_model:
        warnings.append("未能检测到多空辩论模型配置，无法完成同档校验。")
    elif not b_model or not s_model:
        warnings.append(f"多空模型配置不完整 (多头: {b_model or '未配置'}, 空头: {s_model or '未配置'})。")
    else:
        # Check same model
        if b_model.strip().lower() == s_model.strip().lower():
            is_same_model = True
            is_same_tier = True if (b_tier and b_tier == s_tier) else False
            warnings.append(
                f"同模自我辩论：多空双方均使用 '{b_model}' 模型，可能导致辩论观点同质化与缺乏真实博弈对抗。"
            )
        else:
            # Different models
            if b_tier is not None and s_tier is not None:
                if b_tier == s_tier:
                    is_same_tier = True
                    is_cross_tier = False
                    # Same tier verified, no warning
                else:
                    is_same_tier = False
                    is_cross_tier = True
                    warnings.append(
                        f"明显跨档：多头模型 '{b_model}' (档位: {b_tier}) 与空头模型 '{s_model}' (档位: {s_tier}) 跨档位运行，可能导致辩论能力不对等。"
                    )
            else:
                # One or both tiers cannot be proven
                is_same_tier = False
                is_cross_tier = False
                unproven_parts = []
                if b_tier is None:
                    unproven_parts.append(f"多头模型 '{b_model}'")
                if s_tier is None:
                    unproven_parts.append(f"空头模型 '{s_model}'")
                warnings.append(
                    f"无法证明同档：无法确定{'及'.join(unproven_parts)}的能力档位，未能证明多空双方处于相同能力档位。"
                )

    result_metadata = {
        "schema_version": SCHEMA_VERSION,
        "model_id_by_stance": {
            "bull": b_model,
            "bear": s_model,
            "manager": m_model,
        },
        "provider_by_stance": {
            "bull": b_provider,
            "bear": s_provider,
            "manager": m_provider,
        },
        "tier_by_stance": {
            "bull": b_tier,
            "bear": s_tier,
            "manager": m_tier,
        },
        "is_same_model": is_same_model,
        "is_same_tier": is_same_tier,
        "is_cross_tier": is_cross_tier,
        "warnings": warnings,
        "has_warnings": bool(len(warnings) > 0),
    }

    return result_metadata


def attach_model_tier_warnings(
    result_data: Mapping[str, Any],
    role_resolved_configs: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Attach model tier warning metadata to a copy of result_data without mutating input."""
    if not isinstance(result_data, Mapping):
        return {}

    updated = dict(result_data)
    tier_meta = check_model_tier_warnings(
        updated,
        role_resolved_configs=role_resolved_configs,
    )

    updated["model_tier_warning"] = tier_meta
    updated["model_tier_warnings"] = tier_meta["warnings"]
    updated["model_tier_check"] = tier_meta

    inv_state = updated.get("investment_debate_state")
    if isinstance(inv_state, Mapping):
        inv_copy = dict(inv_state)
        inv_copy["model_tier_warning"] = tier_meta
        inv_copy["model_tier_warnings"] = tier_meta["warnings"]
        inv_copy["model_tier_check"] = tier_meta
        updated["investment_debate_state"] = inv_copy

    return updated
