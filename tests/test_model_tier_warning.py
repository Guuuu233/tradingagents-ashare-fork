"""Tests for model tier warning and multi-model stance parity checks (P2-G1).

Covers:
1. 同模 (Same model for bull & bear) -> generates '同模自我辩论' warning.
2. 同档异模 (Different models proven in same tier) -> clean, no warnings.
3. 跨档 (Different models across different tiers) -> generates '明显跨档' warning.
4. 无法证明同档 (Unknown/custom model tier) -> generates '无法证明同档' warning.
5. Extraction from various sources (round_messages, role_resolved_configs, result_data).
6. Read-only guarantee: strictly inspects and warns without modifying bindings/configs.
"""

from copy import deepcopy
from typing import Any, Dict
import pytest

from tradingagents.agents.utils.model_tier_warning import (
    check_model_tier_warnings,
    infer_model_tier,
    attach_model_tier_warnings,
)


class TestModelTierInference:
    """Unit tests for model tier inference."""

    @pytest.mark.parametrize(
        "model_name,expected_tier",
        [
            ("gpt-4o-mini", "quick"),
            ("gpt-4.1-mini", "quick"),
            ("gpt-4.1-nano", "quick"),
            ("gpt-5-mini", "quick"),
            ("gpt-5-nano", "quick"),
            ("gpt-3.5-turbo", "quick"),
            ("claude-3-5-haiku-20241022", "quick"),
            ("claude-haiku-4-5", "quick"),
            ("gemini-2.5-flash", "quick"),
            ("gemini-2.5-flash-lite", "quick"),
            ("gemini-2.0-flash", "quick"),
            ("qwen-turbo", "quick"),
            ("qwen-plus", "quick"),
            ("qwen-2.5-7b-instruct", "quick"),
            ("deepseek-chat", "quick"),
            # Deep / Reasoning models
            ("o1", "deep"),
            ("o1-preview", "deep"),
            ("o3", "deep"),
            ("o3-mini", "deep"),
            ("o4-mini", "deep"),
            ("deepseek-r1", "deep"),
            ("deepseek-reasoner", "deep"),
            ("qwq-32b", "deep"),
            ("claude-3-7-sonnet-20250219", "deep"),
            ("claude-3-5-sonnet-20241022", "deep"),
            ("claude-opus-4-5", "deep"),
            ("gpt-4o", "deep"),
            ("gpt-4.1", "deep"),
            ("gpt-5", "deep"),
            ("gpt-5.1", "deep"),
            ("gpt-5.2", "deep"),
            ("gemini-3-pro-preview", "deep"),
            ("gemini-2.5-pro", "deep"),
            ("qwen-max", "deep"),
        ],
    )
    def test_infer_known_models(self, model_name: str, expected_tier: str):
        assert infer_model_tier(model_name) == expected_tier

    def test_explicit_tier_override(self):
        assert infer_model_tier("custom-model-x", explicit_tier="quick") == "quick"
        assert infer_model_tier("custom-model-y", explicit_tier="deep") == "deep"

    def test_unknown_model_returns_none(self):
        assert infer_model_tier("completely-unrecognized-model-12345") is None


class TestCoreThreeCases:
    """TDD tests for the 3 core cases required by P2-G1."""

    def test_case_1_same_model_triggers_self_debate_warning(self):
        """Case 1: bull and bear use identical model -> '同模自我辩论' warning."""
        result = check_model_tier_warnings(
            bull_model="gpt-4o-mini",
            bear_model="gpt-4o-mini",
            bull_provider="openai",
            bear_provider="openai",
        )

        assert result["is_same_model"] is True
        assert result["model_id_by_stance"]["bull"] == "gpt-4o-mini"
        assert result["model_id_by_stance"]["bear"] == "gpt-4o-mini"
        assert len(result["warnings"]) >= 1
        assert any("同模自我辩论" in w for w in result["warnings"])
        assert result["has_warnings"] is True

    def test_case_2_same_tier_different_models_passes_without_warning(self):
        """Case 2: bull and bear use different models proven to be in the same tier -> No warnings."""
        # Subcase A: Both in quick tier
        result_quick = check_model_tier_warnings(
            bull_model="gpt-4o-mini",
            bear_model="claude-3-5-haiku-20241022",
            bull_provider="openai",
            bear_provider="anthropic",
        )
        assert result_quick["is_same_model"] is False
        assert result_quick["is_same_tier"] is True
        assert result_quick["is_cross_tier"] is False
        assert result_quick["warnings"] == []
        assert result_quick["has_warnings"] is False

        # Subcase B: Both in deep tier
        result_deep = check_model_tier_warnings(
            bull_model="deepseek-r1",
            bear_model="claude-3-7-sonnet-20250219",
            bull_provider="deepseek",
            bear_provider="anthropic",
        )
        assert result_deep["is_same_model"] is False
        assert result_deep["is_same_tier"] is True
        assert result_deep["is_cross_tier"] is False
        assert result_deep["warnings"] == []
        assert result_deep["has_warnings"] is False

    def test_case_3_cross_tier_triggers_warning(self):
        """Case 3: bull and bear use models from clearly different tiers -> '明显跨档' warning."""
        result = check_model_tier_warnings(
            bull_model="deepseek-r1",
            bear_model="gpt-4o-mini",
            bull_provider="deepseek",
            bear_provider="openai",
        )

        assert result["is_same_model"] is False
        assert result["is_same_tier"] is False
        assert result["is_cross_tier"] is True
        assert result["tier_by_stance"]["bull"] == "deep"
        assert result["tier_by_stance"]["bear"] == "quick"
        assert len(result["warnings"]) >= 1
        assert any("跨档" in w for w in result["warnings"])
        assert result["has_warnings"] is True


class TestUnprovenTierWarning:
    """Tests for unproven tier scenarios."""

    def test_unproven_tier_triggers_warning(self):
        """When one or both models cannot be classified, warning is emitted."""
        result = check_model_tier_warnings(
            bull_model="gpt-4o-mini",
            bear_model="custom-fin-model-proprietary-v1",
            bull_provider="openai",
            bear_provider="custom",
        )

        assert result["is_same_model"] is False
        assert result["is_same_tier"] is False
        assert result["is_cross_tier"] is False
        assert len(result["warnings"]) >= 1
        assert any("无法证明同档" in w for w in result["warnings"])


class TestExtractionAndMetadataAttachment:
    """Tests metadata extraction and result_data attachment."""

    def test_extract_from_resolved_roles(self):
        resolved_roles = {
            "bull_researcher": {
                "model_name": "deepseek-r1",
                "provider_type": "deepseek",
                "tier": "deep",
            },
            "bear_researcher": {
                "model_name": "deepseek-r1",
                "provider_type": "deepseek",
                "tier": "deep",
            },
            "research_manager": {
                "model_name": "gpt-4o",
                "provider_type": "openai",
                "tier": "deep",
            },
        }

        result = check_model_tier_warnings(role_resolved_configs=resolved_roles)
        assert result["model_id_by_stance"]["bull"] == "deepseek-r1"
        assert result["model_id_by_stance"]["bear"] == "deepseek-r1"
        assert result["model_id_by_stance"]["manager"] == "gpt-4o"
        assert result["provider_by_stance"]["bull"] == "deepseek"
        assert result["provider_by_stance"]["bear"] == "deepseek"
        assert result["is_same_model"] is True
        assert any("同模自我辩论" in w for w in result["warnings"])

    def test_extract_from_round_messages(self):
        result_data = {
            "investment_debate_state": {
                "round_messages": [
                    {
                        "speaker_key": "Bull",
                        "model_name": "deepseek-r1",
                        "provider": "deepseek",
                    },
                    {
                        "speaker_key": "Bear",
                        "model_name": "gpt-4o-mini",
                        "provider": "openai",
                    },
                ]
            }
        }
        result = check_model_tier_warnings(result_data)
        assert result["model_id_by_stance"]["bull"] == "deepseek-r1"
        assert result["model_id_by_stance"]["bear"] == "gpt-4o-mini"
        assert result["is_cross_tier"] is True

    def test_attach_model_tier_warnings_preserves_input_immutability(self):
        original_data: Dict[str, Any] = {
            "symbol": "600519.SH",
            "investment_debate_state": {
                "bull_history": "看多",
                "bear_history": "看空",
            },
            "role_models": {
                "bull": "gpt-4o-mini",
                "bear": "gpt-4o-mini",
            },
        }
        snapshot = deepcopy(original_data)

        attached = attach_model_tier_warnings(original_data)

        # Input original_data dict keys must not have role_models changed
        assert original_data["role_models"] == snapshot["role_models"]
        # Attached dict contains warning metadata
        assert "model_tier_warning" in attached
        assert "model_tier_warnings" in attached
        assert "model_tier_check" in attached
        assert attached["model_tier_check"]["is_same_model"] is True
        assert attached["investment_debate_state"]["model_tier_check"]["is_same_model"] is True
