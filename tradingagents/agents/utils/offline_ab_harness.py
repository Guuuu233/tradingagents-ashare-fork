"""Offline A/B debate harness for TradingAgents-AShare (P1-M3).

Enables structural comparison between legacy (v1_legacy) and structured (v2_structured_disagreement)
evaluators on identical result_data inputs without network or LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
)
from tradingagents.agents.utils.debate_metrics import calculate_all_debate_metrics

GOLDEN_FIXTURE_FILES = [
    ("000333.SZ", "3c09051e7e364d859dfbe5f1af7cc2c9"),
    ("600900.SH", "597f6cf371114a3b9844112238a0f1a9"),
    ("600276.SH", "ba255b88dfa446279c2d6e9529be6f5e"),
]


class LegacyDebateEvaluator:
    """Evaluates debate result_data under v1_legacy protocol assumptions."""

    def evaluate(self, result_data: Mapping[str, Any]) -> dict[str, Any]:
        """Compute all debate metrics under v1_legacy version."""
        return calculate_all_debate_metrics(result_data, version=PROTOCOL_VERSION_V1_LEGACY)


class V2DebateEvaluator:
    """Evaluates debate result_data under v2_structured_disagreement protocol assumptions."""

    def evaluate(self, result_data: Mapping[str, Any]) -> dict[str, Any]:
        """Compute all debate metrics under v2_structured_disagreement version."""
        return calculate_all_debate_metrics(result_data, version=PROTOCOL_VERSION_V2_STRUCTURED)


class OfflineABHarness:
    """Offline A/B comparison harness between legacy and v2 evaluators."""

    def __init__(self) -> None:
        self.legacy_evaluator = LegacyDebateEvaluator()
        self.v2_evaluator = V2DebateEvaluator()

    def compare_report(self, result_data: Mapping[str, Any]) -> dict[str, Any]:
        """Run both evaluators on identical result_data and produce structural diff."""
        if not isinstance(result_data, dict):
            raise ValueError("result_data must be a dict")

        legacy_res = self.legacy_evaluator.evaluate(result_data)
        v2_res = self.v2_evaluator.evaluate(result_data)

        # Build metric diffs
        diff: dict[str, Any] = {
            "protocol_version": (legacy_res.get("protocol_version"), v2_res.get("protocol_version")),
            "evidence_recycling_rate": (
                legacy_res.get("evidence_recycling", {}).get("rate"),
                v2_res.get("evidence_recycling", {}).get("rate"),
            ),
            "seven_reports_utilization_rate": (
                legacy_res.get("seven_reports_utilization", {}).get("rate"),
                v2_res.get("seven_reports_utilization", {}).get("rate"),
            ),
            "macro_utilization_rate": (
                legacy_res.get("macro_utilization", {}).get("rate"),
                v2_res.get("macro_utilization", {}).get("rate"),
            ),
            "fundamentals_utilization_rate": (
                legacy_res.get("fundamentals_utilization", {}).get("rate"),
                v2_res.get("fundamentals_utilization", {}).get("rate"),
            ),
            "bull_verified_rate": (
                legacy_res.get("bull_bear_verified", {}).get("bull_verified_rate", {}).get("rate"),
                v2_res.get("bull_bear_verified", {}).get("bull_verified_rate", {}).get("rate"),
            ),
            "bear_verified_rate": (
                legacy_res.get("bull_bear_verified", {}).get("bear_verified_rate", {}).get("rate"),
                v2_res.get("bull_bear_verified", {}).get("bear_verified_rate", {}).get("rate"),
            ),
            "bull_bear_verified_delta": (
                legacy_res.get("bull_bear_verified", {}).get("bull_bear_verified_delta", {}).get("rate"),
                v2_res.get("bull_bear_verified", {}).get("bull_bear_verified_delta", {}).get("rate"),
            ),
            "challenge_count": (
                legacy_res.get("challenge_metrics", {}).get("challenge_count", {}).get("rate"),
                v2_res.get("challenge_metrics", {}).get("challenge_count", {}).get("rate"),
            ),
            "challenge_adoption_rate": (
                legacy_res.get("challenge_metrics", {}).get("challenge_adoption_rate", {}).get("rate"),
                v2_res.get("challenge_metrics", {}).get("challenge_adoption_rate", {}).get("rate"),
            ),
            "field_completeness_rate": (
                legacy_res.get("field_completeness", {}).get("rate"),
                v2_res.get("field_completeness", {}).get("rate"),
            ),
        }

        summary = {
            "comparison_mode": "structural_compatibility_baseline",
            "protocol_change": f"{legacy_res.get('protocol_version')} -> {v2_res.get('protocol_version')}",
            "seven_reports_utilization": v2_res.get("seven_reports_utilization", {}).get("rate"),
            "field_completeness": v2_res.get("field_completeness", {}).get("rate"),
            "challenge_count": v2_res.get("challenge_metrics", {}).get("challenge_count", {}).get("rate"),
            "note": "结构兼容性基线对比：同一 legacy result_data 重放时 delta=0 仅验证协议结构解析兼容性，不代表真实 v2 质量等同。",
        }

        return {
            "comparison_mode": "structural_compatibility_baseline",
            "note": "结构兼容性基线对比：同一 legacy result_data 重放时 delta=0 仅验证协议结构解析兼容性，不代表真实 v2 质量等同。",
            "legacy": legacy_res,
            "v2": v2_res,
            "diff": diff,
            "summary": summary,
        }

    def compare_golden_fixtures(
        self,
        golden_dir: Optional[Union[str, Path]] = None,
    ) -> dict[str, Any]:
        """Replay comparison across the 3 golden audit fixtures (000333, 600900, 600276)."""
        if golden_dir is None:
            base_dir = Path(__file__).resolve().parents[3]
            golden_dir = base_dir / "tests" / "golden" / "audit_20260823"
        else:
            golden_dir = Path(golden_dir)

        results: dict[str, Any] = {}
        for sym, fid in GOLDEN_FIXTURE_FILES:
            file_path = golden_dir / f"{fid}_result_data.json"
            if not file_path.exists():
                continue
            with open(file_path, "r", encoding="utf-8") as f:
                content = json.load(f)
            rd = content.get("result_data", content)
            comparison = self.compare_report(rd)
            results[sym] = comparison

        return results
