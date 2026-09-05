"""Unit and contract tests for horizon run metadata in AgentState and Propagator (H-02a).

Covers:
- Propagator.create_initial_state populates horizon_run_metadata
- Default fallback: default + short + T+10
- Explicit medium: explicit + medium + T+40
- Explicit dual horizons: resolved preserved in state while slice horizon remains short/medium
- horizon="medium" slice without resolution remains default/short metadata
- Separation of investment_horizon (user intent) and resolved analysis horizons
- Cutoff alignment with workflow_context.data_as_of (never auto-fills today)
- No evaluation_eligible: True in metadata or state
- Social data context preservation
- H-01 to_dict() mapping compatibility
- Order preservation for resolved horizons and primary_eval_offsets
- JSON serializability of horizon_run_metadata
- AgentState TypedDict annotations for horizon_run_metadata
"""

import json
from unittest.mock import patch

import pytest

from tradingagents.agents.utils.agent_states import AgentState, HorizonRunMetadata
from tradingagents.dataflows.social.contracts import SocialStatus
from tradingagents.graph.horizon_profile import (
    HORIZON_PROFILE_V1,
    HorizonResolution,
    resolve_analysis_horizons,
)
from tradingagents.graph.propagation import Propagator


class TestHorizonRunMetadataContract:
    """Test suite for H-02a horizon run metadata contracts."""

    def test_default_horizon_run_metadata(self):
        """Default path (unprovided resolution): default + short + T+10."""
        p = Propagator()
        state = p.create_initial_state("600519", "2024-01-15")

        assert "horizon_run_metadata" in state
        meta = state["horizon_run_metadata"]
        assert isinstance(meta, dict)

        assert meta["requested"] is None
        assert meta["resolved"] == ["short"]
        assert meta["resolution_source"] == "default"
        assert meta["profile_id"] == "horizon_profile_v1"
        assert meta["primary_eval_offsets"] == {"short": 10}
        assert meta["cutoff"] == state["workflow_context"]["data_as_of"]
        assert meta["investment_horizon"] is None
        assert meta.get("evaluation_eligible") is not True

        assert state["horizon"] == "short"

        # Must be JSON serializable
        serialized = json.dumps(meta)
        deserialized = json.loads(serialized)
        assert deserialized["resolved"] == ["short"]
        assert deserialized["requested"] is None
        assert deserialized["primary_eval_offsets"] == {"short": 10}

    def test_explicit_medium_metadata(self):
        """Explicit medium resolution populates medium + T+40."""
        res = resolve_analysis_horizons(["medium"])
        p = Propagator()
        state = p.create_initial_state(
            "600519",
            "2024-01-15",
            horizon="medium",
            horizon_resolution=res,
        )

        assert "horizon_run_metadata" in state
        meta = state["horizon_run_metadata"]

        assert meta["requested"] == ["medium"]
        assert meta["resolved"] == ["medium"]
        assert meta["resolution_source"] == "explicit"
        assert meta["profile_id"] == "horizon_profile_v1"
        assert meta["primary_eval_offsets"] == {"medium": 40}
        assert meta.get("evaluation_eligible") is not True
        assert state["horizon"] == "medium"

    def test_explicit_dual_horizon_with_short_slice(self):
        """Explicit dual horizon in metadata while current graph slice is short."""
        res = resolve_analysis_horizons(["short", "medium"])
        p = Propagator()
        state = p.create_initial_state(
            "600519",
            "2024-01-15",
            horizon="short",
            horizon_resolution=res,
        )

        assert state["horizon"] == "short"
        meta = state["horizon_run_metadata"]
        assert meta["requested"] == ["short", "medium"]
        assert meta["resolved"] == ["short", "medium"]
        assert meta["resolution_source"] == "explicit"
        assert meta["profile_id"] == "horizon_profile_v1"
        assert meta["primary_eval_offsets"] == {"short": 10, "medium": 40}
        assert meta.get("evaluation_eligible") is not True

    def test_explicit_dual_horizon_with_medium_slice(self):
        """Explicit dual horizon in metadata while current graph slice is medium."""
        res = resolve_analysis_horizons(["short", "medium"])
        p = Propagator()
        state = p.create_initial_state(
            "600519",
            "2024-01-15",
            horizon="medium",
            horizon_resolution=res,
        )

        assert state["horizon"] == "medium"
        meta = state["horizon_run_metadata"]
        assert meta["resolved"] == ["short", "medium"]
        assert meta["resolution_source"] == "explicit"
        assert meta["primary_eval_offsets"] == {"short": 10, "medium": 40}

    def test_slice_medium_without_resolution_remains_default_metadata(self):
        """horizon='medium' slice without resolution passed must remain default short metadata."""
        p = Propagator()
        state = p.create_initial_state("600519", "2024-01-15", horizon="medium")

        # Slice is medium
        assert state["horizon"] == "medium"

        # But run metadata MUST NOT infer explicit medium from slice parameter
        meta = state["horizon_run_metadata"]
        assert meta["requested"] is None
        assert meta["resolved"] == ["short"]
        assert meta["resolution_source"] == "default"
        assert meta["primary_eval_offsets"] == {"short": 10}

    def test_investment_horizon_separated_from_resolved(self):
        """user_context.investment_horizon is copied but never overwrites resolved horizons."""
        res = resolve_analysis_horizons(["short"])
        p = Propagator()
        state = p.create_initial_state(
            "600519",
            "2024-01-15",
            user_context={"investment_horizon": "中长线持有三年"},
            horizon_resolution=res,
        )

        meta = state["horizon_run_metadata"]
        assert meta["investment_horizon"] == "中长线持有三年"
        assert meta["resolved"] == ["short"]
        assert meta["resolution_source"] == "explicit"
        assert state["user_context"]["investment_horizon"] == "中长线持有三年"

    def test_investment_horizon_medium_with_default_short_run(self):
        """User holding intent is medium, but run analysis horizon remains default short."""
        p = Propagator()
        state = p.create_initial_state(
            "600519",
            "2024-01-15",
            user_context={"investment_horizon": "medium"},
        )

        meta = state["horizon_run_metadata"]
        assert meta["investment_horizon"] == "medium"
        assert meta["resolved"] == ["short"]
        assert meta["resolution_source"] == "default"
        assert meta["requested"] is None

    def test_cutoff_matches_workflow_context_and_never_fills_today(self):
        """cutoff references workflow_context.data_as_of exactly; missing is null, not today."""
        p = Propagator()
        state = p.create_initial_state("600519", "2024-01-15")

        meta = state["horizon_run_metadata"]
        assert meta["cutoff"] is not None
        assert meta["cutoff"] == state["workflow_context"]["data_as_of"]

        # If market_context has None data_as_of, cutoff must be None
        with patch("tradingagents.graph.propagation.build_market_context") as mock_bmc:
            mock_bmc.return_value = {
                "trade_date": "2024-01-15",
                "analysis_baseline_date": "2024-01-15",
                "timezone": "Asia/Shanghai",
                "market_session": "closed",
                "market_is_open": False,
                "analysis_mode": "historical",
                "data_as_of": None,
                "session_note": "test",
            }
            state_null_cutoff = p.create_initial_state("600519", "2024-01-15")
            assert state_null_cutoff["workflow_context"]["data_as_of"] is None
            assert state_null_cutoff["horizon_run_metadata"]["cutoff"] is None

    def test_no_evaluation_eligible_true(self):
        """Must never have evaluation_eligible=True in metadata or state."""
        p = Propagator()
        state = p.create_initial_state("600519", "2024-01-15")
        meta = state["horizon_run_metadata"]

        assert meta.get("evaluation_eligible") is not True
        assert state.get("evaluation_eligible") is not True

        # Also with explicit resolution
        res = resolve_analysis_horizons(["short", "medium"])
        state_dual = p.create_initial_state("600519", "2024-01-15", horizon_resolution=res)
        assert state_dual["horizon_run_metadata"].get("evaluation_eligible") is not True

    def test_social_default_context_preserved(self):
        """Social data context is preserved in initial state alongside horizon_run_metadata."""
        p = Propagator()
        state = p.create_initial_state("600519", "2024-01-15")

        assert "social_data_context" in state
        social_ctx = state["social_data_context"]
        assert isinstance(social_ctx, dict)
        assert social_ctx["status"] == SocialStatus.NOT_APPLICABLE.value
        assert social_ctx["mode"] == "disabled"
        assert social_ctx["direction_allowed"] is False

    def test_mapping_compatibility_with_h01_to_dict(self):
        """Accepts mapping from H-01 to_dict(), preserving resolution_source without flipping."""
        # 1. Default resolution to_dict()
        default_dict = resolve_analysis_horizons().to_dict()
        p = Propagator()
        state_def = p.create_initial_state("600519", "2024-01-15", horizon_resolution=default_dict)
        meta_def = state_def["horizon_run_metadata"]
        assert meta_def["resolution_source"] == "default"
        assert meta_def["resolved"] == ["short"]
        assert meta_def["requested"] is None

        # 2. Explicit resolution to_dict()
        explicit_dict = resolve_analysis_horizons(["medium"]).to_dict()
        state_exp = p.create_initial_state("600519", "2024-01-15", horizon_resolution=explicit_dict)
        meta_exp = state_exp["horizon_run_metadata"]
        assert meta_exp["resolution_source"] == "explicit"
        assert meta_exp["resolved"] == ["medium"]
        assert meta_exp["requested"] == ["medium"]

        # 3. Mapping with explicit requested preserved
        custom_mapping = {
            "requested": ["short", "medium"],
            "resolved": ["short", "medium"],
            "resolution_source": "explicit",
        }
        state_custom = p.create_initial_state("600519", "2024-01-15", horizon_resolution=custom_mapping)
        meta_custom = state_custom["horizon_run_metadata"]
        assert meta_custom["requested"] == ["short", "medium"]
        assert meta_custom["resolved"] == ["short", "medium"]
        assert meta_custom["resolution_source"] == "explicit"

        # 4. Legacy resolution source support
        legacy_mapping = {
            "resolved": ["short"],
            "resolution_source": "legacy",
        }
        state_legacy = p.create_initial_state("600519", "2024-01-15", horizon_resolution=legacy_mapping)
        meta_legacy = state_legacy["horizon_run_metadata"]
        assert meta_legacy["resolution_source"] == "legacy"
        assert meta_legacy["requested"] is None

    def test_order_preservation_and_reversed_dual(self):
        """Preserves resolved order (medium, short) and matches offsets."""
        res = resolve_analysis_horizons(["medium", "short"])
        p = Propagator()
        state = p.create_initial_state("600519", "2024-01-15", horizon_resolution=res)

        meta = state["horizon_run_metadata"]
        assert meta["resolved"] == ["medium", "short"]
        assert list(meta["primary_eval_offsets"].keys()) == ["medium", "short"]
        assert meta["primary_eval_offsets"] == {"medium": 40, "short": 10}

    def test_agent_state_annotations(self):
        """AgentState typed annotations include horizon_run_metadata and HorizonRunMetadata exists."""
        annotations = AgentState.__annotations__
        assert "horizon_run_metadata" in annotations

        # HorizonRunMetadata TypedDict check
        run_meta_annotations = HorizonRunMetadata.__annotations__
        assert "requested" in run_meta_annotations
        assert "resolved" in run_meta_annotations
        assert "resolution_source" in run_meta_annotations
        assert "profile_id" in run_meta_annotations
        assert "primary_eval_offsets" in run_meta_annotations
        assert "cutoff" in run_meta_annotations
        assert "investment_horizon" in run_meta_annotations
