#!/usr/bin/env python3
"""P3-H1b: 信用加权门槛校验与分层隔离只读检查脚本 (verify_h1b_gates.py).

功能：
1. 扫描当前数据库 (ReportDB / reports) 及历史 v2 结构化辩论报告；
2. 提取并核验 7 维门槛指标矩阵 (N, 分侧, 时间, T+5 完整率, 平衡, 偏置冻结, 幅度)；
3. 输出 7 维 PASS/FAIL 结构化矩阵与汇总 JSON；
4. 依据已批准门槛给出系统级决策建议（样本未达标时明确建议保持关 flag）。

内存治理 (DAV-1767)：SQLite 路径改为「主键清单 + 逐行主键回读」流式读取，
评估输入按 H1b 白名单投影（双档槽位 / debate 证据 / 七份报告文本等评估
实际消费的键），聚合只保留标量与计数状态——任何时刻不把全库行、全部
result_data blob 或全部比对键同时驻留内存。模块层 ``shadow_credit`` 不改动；
流式等价性（含 160 键比对语义）由内置镜像聚合器 + tests/test_h1b_gates.py
的等价性守护测试保障，产物 JSON 与 DAV-1756 演练基线逐字相同。
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

# Ensure project root in sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tradingagents.agents.utils.shadow_credit import (
    calculate_shadow_credit_metrics,
    extract_report_industry,
    extract_sample_cohort,
    filter_reports_by_cohort,
    is_cohort_homogeneous,
    normalize_report_for_evaluation,
    H1B_SCHEMA_VERSION,
)
from tradingagents.agents.utils.shadow_credit import (
    evaluate_model_bias_and_weights as _module_evaluate_model_bias_and_weights,
    evaluate_h1b_system_gates as _module_evaluate_gate_thresholds,
    filter_v2_completed_reports as _module_filter_v2_completed_reports,
    is_qualifying_v2_report as _module_is_qualifying_v2_report,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("verify_h1b_gates")


# ══════════════════════════════════════════════════════════════════════════
# DAV-1767 streaming equivalence layer
#
# The evaluation in ``shadow_credit.evaluate_gate_thresholds`` /
# ``filter_v2_completed_reports`` / ``evaluate_model_bias_and_weights`` consumes
# only a small structural subset of each row (verified by audit; see the
# projection whitelist below). This layer reproduces exactly that subset
# end-to-end on a streaming pipeline:
#
#   row snapshot → projection → horizon-unit split → whitelist prune →
#   stage pipeline (counts/ledger) → projected sample pool → 7-dim matrix,
#   cohort meta, model-isolation, report_result.
#
# Every function here is deterministic and side-effect free; the unit tests
# (TestH1bVerifyGatesStreamingEquivalence / TestH1bVerifyGatesDavian1803Rework
# in tests/test_h1b_gates.py) cross-check this layer against the module
# implementation by staged ledger/matrix/isolation assertions on identical
# inputs, so the output JSON and its verdict logic stay byte-identical (all
# leaf keys except ``generated_at``; 154 leaves on the real-DB cohort config,
# 158 on the --input-dir config — the card's historical "160 keys" was an
# approximate wording, corrected here to the measured numbers).
# ══════════════════════════════════════════════════════════════════════════

H1B_VERIFY_SCHEMA_VERSION: str = H1B_SCHEMA_VERSION

# Evaluation-consumed result_data keys (per the DAV-1767 audit). Top-level
# report columns are merged over result_data by normalize_report_for_evaluation
# / split_report_into_units, so every mapped column must appear here too.
_STREAM_EVAL_RESULT_KEYS: frozenset = frozenset({
    # D-009 §5 / Stage 3 classification
    "analysis_status", "trade_action", "decision_status", "decision", "direction",
    "status",
    # Stage 2: v2 protocol + winner
    "protocol_version", "protocol_stage", "v2_debate_enabled", "feature_flags",
    "manager_verdict", "debate_winner",
    # Debate evidence (Stage 2 evidence check + D2/D6 + utilization)
    "investment_debate_state", "claims", "challenges",
    # Shadow metrics / T+5 (D4/D6)
    "shadow_credit_metrics", "t_plus_5_status", "t_plus_5_date", "t_plus_5_price",
    "t_plus_5_direction_hit", "t_plus_5_evaluated", "t_plus_5_evaluated_at",
    "is_suspended", "suspension", "is_in_flight", "is_t_plus_5_due",
    "t_plus_1_open", "entry_price", "entry_price_source", "target_price",
    # Cohort triad + horizon + provenance (Stages 1.0/3.5/4 + cohort key)
    "decision_model_version", "evidence_contract_version", "price_basis_version",
    "price_ref_contract_version", "generated_by_commit_sha", "commit_sha",
    "horizon", "short_term", "medium_term",
    # Industry extraction (D1)
    "industry", "sector", "instrument_context", "market_data_context",
    "data_collection_provenance", "quadrant_1_protocol_metadata",
    # get_protocol_metadata / is_v2_debate_enabled support
    "data_utilization_metrics", "challenge_verification", "metadata",
    # D3 market regimes
    "market_regime", "regime",
    # Identity / dates (D3, id map, exclusion_reasons keys)
    "id", "report_id", "parent_report_id", "symbol", "ticker", "trade_date",
    "date", "created_at", "updated_at", "user_id",
    # Row-level list fields tolerated by the evaluation (mapping-only use;
    # values never read for gate math)
    "data_gaps", "falsification_conditions",
    # Per-stance model ids (model isolation discovery)
    "model_id_by_stance",
    # Seven analyst reports (utilization denominators)
    "macro_report", "market_report", "sentiment_report", "news_report",
    "fundamentals_report", "smart_money_report", "volume_price_report",
})

# Fields whose values are only read as Mappings by the evaluation; the
# projection keeps them verbatim (the mirror cross-check asserts equivalence).
_STREAM_MAP_ONLY_FIELDS = frozenset({
    "claims", "challenges", "model_id_by_stance",
    "data_gaps", "falsification_conditions",
})

# Debate-evidence keys consumed by Stage 2, D2/D6 and calculate_shadow_credit_metrics.
_STREAM_DEBATE_KEYS = frozenset({
    "manager_verdict", "claims", "challenges", "claim_evidence_summary",
    "round_messages", "belief_trajectory",
    "protocol_version", "protocol_stage", "feature_flags", "v2_debate_enabled",
    "tiebreak_skipped", "debate_degenerate", "data_utilization_metrics",
    "challenge_verification", "blocked", "block_reason",
    # seven report keys when a debate state carries them (utilization fallback)
    "macro_report", "market_report", "sentiment_report", "news_report",
    "fundamentals_report", "smart_money_report", "volume_price_report",
})

# Per-message keys inside investment_debate_state.round_messages.
_STREAM_ROUND_MSG_KEYS = frozenset({
    "speaker_key", "speaker", "stance", "model_name", "model_id", "model",
    "is_verdict", "cleaned_prose", "self_win_prob", "stage", "message_index",
})

# Verdict fields consumed by Stage 2/D2 and Stage 3.5 (collect_hold_semantic_reasons).
_STREAM_VERDICT_KEYS = frozenset({
    "winner", "direction", "consistency_check_passed",
    "failed_checks", "adopted_challenge_ids", "ohlcv_gate_applied",
    "fund_flow_dispute_gate_applied", "entry", "reason",
    # claim_evidence_summary is kept VERBATIM (items carry speaker/stance/counts)
    "claim_evidence_summary",
})

# Industry/contract probe fields kept inside misc mapping blocks (metadata etc.).
_STREAM_MISC_KEYS = frozenset({
    "industry", "sector", "industry_name",
    "decision_model_version", "evidence_contract_version", "price_basis_version",
    "price_ref_contract_version", "generated_by_commit_sha", "commit_sha",
    "horizon", "protocol_version",
})

# market_data_context / misc provenance blocks: industry probes + the FULL
# Stage 3.5 OHLCV consumption surface (is_daily_ohlcv_unavailable reads
# source_provenance(.stock_data), data_failure_ledger, daily —
# evidence_verifier.py:6561; DAV-1803 P0-1). Everything else inside an mdc is
# unconsumed by the evaluation and may be dropped.
_STREAM_MDC_KEYS = frozenset({
    "industry", "sector", "industry_linkage", "source_provenance",
    "daily", "data_failure_ledger",
})
_STREAM_MDC_LINKAGE_KEYS = frozenset({"industry_name", "industry", "sector"})
# Horizon slot names used by the DAV-1506 per-horizon mdc view ({h: mdc}).
_HORIZON_SLOT_KEYS = frozenset({"short", "medium", "short_term", "medium_term", "primary"})
_STREAM_MDC_PROVENANCE_KEYS = frozenset({"stock_data"})


def _stream_project_eval_mapping(obj: Any) -> Dict[str, Any]:
    """Recursively project a mapping to _STREAM_EVAL_RESULT_KEYS (shallow values
    verbatim). Used for the nested ``result_data`` payload on the single-unit
    fallback path, where the module merges result_data over the row."""
    if not isinstance(obj, Mapping):
        return {}
    return {k: v for k, v in obj.items() if k in _STREAM_EVAL_RESULT_KEYS and v is not None}


def _stream_prune_debate(obj: Any) -> Any:
    """Prune an investment_debate_state mapping to evaluation-consumed fields.

    Claims / challenges / claim_evidence_summary are evaluation DATA (speaker,
    stance, status, claim text, evidence strings feed D2/D6/utilization and
    the clone-rate multiset) and are kept verbatim. round_messages items are
    reduced to the consumed per-message fields (model discovery, is_verdict,
    cleaned_prose for the utilization denominator, degenerate detection).
    """
    if not isinstance(obj, Mapping):
        return obj
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        if k not in _STREAM_DEBATE_KEYS or v is None:
            continue
        if k == "manager_verdict":
            out[k] = _stream_prune_verdict(v)
        elif k == "round_messages" and isinstance(v, list):
            pruned_msgs: List[Any] = []
            for msg in v:
                if isinstance(msg, Mapping):
                    pruned_msgs.append({mk: msg[mk] for mk in _STREAM_ROUND_MSG_KEYS if mk in msg})
                else:
                    pruned_msgs.append(msg)
            out[k] = pruned_msgs
        else:
            out[k] = v
    return out


def _stream_prune_verdict(obj: Any) -> Any:
    """Prune a manager_verdict mapping; claim_evidence_summary stays verbatim."""
    if not isinstance(obj, Mapping):
        return obj
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        if k in _STREAM_VERDICT_KEYS and v is not None:
            out[k] = v
    return out


def _stream_prune_market_data_context(obj: Any) -> Any:
    """Prune a market_data_context (or per-horizon mdc map) by CONSUMPTION SURFACE.

    Keeps the FULL Stage 3.5 + industry surface (DAV-1803 P0-1):
    ``daily`` / ``data_failure_ledger`` (inputs of
    ``is_daily_ohlcv_unavailable`` → hold_defensive), ``source_provenance``
    (.stock_data verbatim), ``industry_linkage`` / ``industry`` / ``sector``.
    Everything else (fund_flow_evidence, realtime, vpa_*, …) is unconsumed by
    the gate evaluation and dropped. A per-horizon mdc map
    (``{short: mdc, medium: mdc}``, DAV-1506 compat view) is pruned per value.
    """
    if not isinstance(obj, Mapping):
        return obj
    if obj and set(obj.keys()) <= _HORIZON_SLOT_KEYS and all(isinstance(v, Mapping) for v in obj.values()):
        # per-horizon mdc map ({short: mdc, medium: mdc}, DAV-1506 compat view)
        return {h: _stream_prune_market_data_context(v) for h, v in obj.items()}
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        if v is None or k not in _STREAM_MDC_KEYS:
            continue
        if k in ("industry", "sector", "daily", "data_failure_ledger"):
            out[k] = v
        elif k == "industry_linkage" and isinstance(v, Mapping):
            out[k] = {lk: v[lk] for lk in _STREAM_MDC_LINKAGE_KEYS if lk in v}
        elif k == "source_provenance" and isinstance(v, Mapping):
            prov_out: Dict[str, Any] = {}
            for pk, pv in v.items():
                if pk == "stock_data":
                    prov_out[pk] = pv  # small structured provenance record
                elif isinstance(pv, Mapping):
                    # Non-stock provenance entries may nest an industry probe
                    # (extract_report_industry reads only mdc.industry_linkage,
                    # but keep the per-entry industry probe defensively cheap).
                    kept = {pk2: pv[pk2] for pk2 in ("industry", "sector") if pk2 in pv}
                    if kept:
                        prov_out[pk] = kept
            out[k] = prov_out
    return out


def _stream_prune_data_collection_provenance(obj: Any) -> Any:
    """Prune a data_collection_provenance block (industry probes only).

    Never shape-sniffs — the block's consumed keys are exactly
    ``industry_linkage_raw`` / ``industry_linkage`` inner probes
    (extract_report_industry step 5), regardless of any other keys it carries
    (e.g. ``daily`` / ``data_failure_ledger``).
    """
    if not isinstance(obj, Mapping):
        return obj
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        if k in ("industry_linkage_raw", "industry_linkage") and isinstance(v, Mapping):
            out[k] = {lk: v[lk] for lk in _STREAM_MDC_LINKAGE_KEYS if lk in v}
    return out


def _stream_prune_misc_block(obj: Any) -> Any:
    """Prune a misc mapping (instrument_context / quadrant_1_protocol_metadata /
    metadata) to the consumed probe keys — never shape-sniffed into mdc.

    ``metadata`` feeds extract_sample_cohort (triad + commit sha),
    ``_is_contract_era_sample`` (price_ref_contract_version) and
    extract_report_industry (industry probes), so its contract / cohort /
    horizon keys must survive unconditionally (DAV-1811 M-1).
    """
    if not isinstance(obj, Mapping):
        return obj
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        if k in _STREAM_MISC_KEYS and v is not None:
            out[k] = v
    return out


def _stream_project_unit(unit: Mapping[str, Any]) -> Dict[str, Any]:
    """Project one horizon unit to the evaluation-consumed subset.

    Semantics-preserving reductions (audit-backed):
    - ``manager_verdict``: pruned to consumed verdict fields (claim text /
      speaker / stance / counts inside claim_evidence_summary stay verbatim);
    - ``investment_debate_state``: pruned to consumed debate fields; claims /
      challenges stay verbatim (evaluation data, incl. claim text for the D6
      clone-rate multiset and utilization); round_messages reduced to the
      consumed per-message fields;
    - ``market_data_context`` / ``instrument_context`` /
      ``data_collection_provenance`` / ``quadrant_1_protocol_metadata`` /
      ``metadata``: pruned to industry probes / Stage 3.5 provenance
      (source_provenance.stock_data kept for hold_defensive);
    # Everything else on the whitelist is kept verbatim.
    """
    out: Dict[str, Any] = {}
    for k, v in unit.items():
        if k == "result_data":
            # The single-unit fallback path passes the raw row through; the
            # nested result_data payload is the evaluation data itself (the
            # module merges it over the row via normalize_report_for_evaluation
            # / is_qualifying_v2_report lookups). Project it recursively and
            # keep it under its original key.
            out[k] = _stream_project_eval_mapping(v) if isinstance(v, Mapping) else v
            continue
        if k not in _STREAM_EVAL_RESULT_KEYS or v is None:
            continue
        if k in _STREAM_MAP_ONLY_FIELDS:
            out[k] = v
            continue
        if k == "manager_verdict":
            out[k] = _stream_prune_verdict(v)
            continue
        if k == "investment_debate_state":
            if isinstance(v, Mapping):
                out[k] = _stream_prune_debate(v)
            continue
        # DAV-1811 M-1: dispatch per block by CONSUMPTION SURFACE — never a
        # shared shape-sniffing function. market_data_context keeps the full
        # Stage 3.5 surface; metadata keeps its contract/cohort/horizon keys
        # even when it happens to carry daily / data_failure_ledger.
        if k == "market_data_context":
            if isinstance(v, Mapping):
                out[k] = _stream_prune_market_data_context(v)
            continue
        if k == "data_collection_provenance":
            if isinstance(v, Mapping):
                out[k] = _stream_prune_data_collection_provenance(v)
            continue
        if k in ("instrument_context", "quadrant_1_protocol_metadata", "metadata"):
            if isinstance(v, Mapping):
                out[k] = _stream_prune_misc_block(v)
            continue
        out[k] = v
    # Claims inside the debate state are evidence values — keep their Mapping
    # entries verbatim (already covered by _stream_prune_debate keeping only
    # debate keys; claim Mapping entries carry claim/evidence/status/stance).
    return out


def _stream_split_units(report: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Split a projected row into horizon units (mirror of split_report_into_units).

    Runs on the PROJECTED row: the whitelist keeps short_term/medium_term
    slots and every identity/cohort field _inherit copies, so unit structure,
    warnings, ordering and parent_report_id stamping match the module split
    exactly. Unit-level re-prune is applied on the split result.
    """
    if not isinstance(report, Mapping):
        return []
    res_data = report.get("result_data") if isinstance(report.get("result_data"), Mapping) else {}
    units: List[Dict[str, Any]] = []
    for sub_key, horizon_label in (("short_term", "short"), ("medium_term", "medium")):
        sub = res_data.get(sub_key)
        if not isinstance(sub, Mapping):
            continue
        unit = dict(sub)
        for key in (
            "id", "report_id", "symbol", "trade_date", "industry", "status",
            "created_at", "updated_at", "user_id",
        ):
            val = report.get(key)
            if val is None and isinstance(res_data, Mapping):
                val = res_data.get(key)
            if val is not None and (unit.get(key) is None or unit.get(key) == ""):
                unit[key] = val
        for key in (
            "decision_model_version", "evidence_contract_version",
            "price_basis_version", "price_ref_contract_version",
            "generated_by_commit_sha",
        ):
            if (unit.get(key) is None or unit.get(key) == "") and res_data.get(key) is not None:
                unit[key] = res_data[key]
        unit["horizon"] = horizon_label
        parent_rid = report.get("id") or report.get("report_id")
        if parent_rid is not None and str(parent_rid).strip():
            unit.setdefault("parent_report_id", str(parent_rid).strip())
        units.append(unit)
    if units:
        return [_stream_project_unit(u) for u in units]
    return [_stream_project_unit(dict(report))]


class _StreamAggregator:
    """One-pass streaming state machine — the module pipeline's counting twin.

    Deterministic single traversal with NO residual row state: after each row,
    memory carries only scalars, counters, and bounded key sets (symbols,
    industries, dates, regimes, claim texts). The claim-text pool is the only
    bulk per-unit residue the evaluation semantics require (D6 clone rate needs
    the exact multiset) — measured in the low MiB for the real corpus.
    """

    def __init__(self) -> None:
        self.raw_count = 0
        self.unit_count = 0
        self.dual_horizon_split_reports = 0
        self.qualifying_v2_count = 0
        self.eligible_count = 0
        self.non_v2_excluded = 0
        self.d009_excluded = 0
        self.excluded_counts: Dict[str, int] = {
            "legacy_null": 0, "abstain": 0, "invalid_run": 0,
            "data_error": 0, "no_trade": 0, "wait": 0,
        }
        self.ledger_rest: Dict[str, int] = {
            "hold_defensive": 0, "hold_conflict": 0, "hold_unresolved": 0,
            "hold_semantic_isolated": 0, "hold_price_basis_overlap": 0,
            "price_basis_contaminated": 0, "price_basis_pending_review": 0,
            "price_basis_contract_incomplete": 0, "price_basis_isolated": 0,
            "clean_count": 0,
        }
        self.qualifying: List[Dict[str, Any]] = []
        # Cohort bookkeeping for the fully-streaming SQLite pipeline (DAV-1767):
        # horizon/version keys live on units, so the cohort decision is made
        # per-unit instead of via the module's post-filter over a row list.
        self.cohort_shas: Set[str] = set()
        self.cohort_keys: Set[str] = set()
        self.cohort_homogeneous_key: Optional[str] = None

    def feed(self, report: Mapping[str, Any], cohort_spec: Optional[Mapping[str, Any]] = None) -> None:
        """Process one row through the staged pipeline (mirror of filter_v2_completed_reports).

        With ``cohort_spec`` (fully-streaming SQLite path), units outside the
        cohort are skipped exactly like filter_reports_by_cohort would skip
        them post-split; the ledger still counts every split unit (the module
        path's ledger also counts pre-filter units), commit SHAs are
        accumulated for surviving units.
        """
        from tradingagents.agents.utils.shadow_credit import (
            classify_v2_report_d009_exclusion,
            classify_price_basis_exclusion,
            collect_hold_semantic_reasons,
            extract_report_id,
            extract_sample_cohort,
            is_legacy_unversioned_sample,
            normalize_horizon_label,
            _cohort_canonical_key, _legacy_cohort_key,
            COHORT_LEGACY_UNVERSIONED, DECISION_MODEL_LEGACY,
            EVIDENCE_CONTRACT_V0, HORIZON_UNSPECIFIED, PRICE_BASIS_UNSPECIFIED,
            REASON_CONTAMINATED, REASON_PENDING_REVIEW, REASON_CONTRACT_INCOMPLETE,
        )
        self._unit_is_eligible = False

        self.raw_count += 1

        units = _stream_split_units(report)
        if len(units) > 1:
            self.dual_horizon_split_reports += 1
        self.unit_count += len(units)

        for unit in units:
            self._unit_is_eligible = False

            if cohort_spec is not None:
                # Per-unit cohort gate (module-equivalent, DAV-1322: the four
                # cohort components incl. horizon live on the unit).
                c_info = extract_sample_cohort(unit)
                if cohort_spec["cohort_type"] == COHORT_LEGACY_UNVERSIONED:
                    if not is_legacy_unversioned_sample(unit):
                        continue
                    if c_info["decision_model_version"] not in (None, "", COHORT_LEGACY_UNVERSIONED, DECISION_MODEL_LEGACY):
                        continue
                    if (c_info.get("horizon") or HORIZON_UNSPECIFIED) != (cohort_spec.get("horizon") or HORIZON_UNSPECIFIED):
                        continue
                else:
                    if is_legacy_unversioned_sample(unit):
                        continue
                    if (
                        c_info["decision_model_version"] != cohort_spec["decision_model_version"]
                        or (c_info["evidence_contract_version"] or EVIDENCE_CONTRACT_V0) != cohort_spec["evidence_contract_version"]
                        or (c_info["price_basis_version"] or PRICE_BASIS_UNSPECIFIED) != cohort_spec["price_basis_version"]
                        or (c_info.get("horizon") or HORIZON_UNSPECIFIED) != (cohort_spec.get("horizon") or HORIZON_UNSPECIFIED)
                    ):
                        continue
                sha = c_info["generated_by_commit_sha"]
                if sha:
                    self.cohort_shas.add(sha)
            else:
                c_info = extract_sample_cohort(unit)
                sha = c_info["generated_by_commit_sha"]
                if sha:
                    self.cohort_shas.add(sha)
                if self._unit_is_eligible:
                    # Homogeneity is a property of the evaluation pool: only
                    # D-009-eligible units contribute cohort keys (mirror of
                    # the module path, which keys the post-filter sample pool).
                    if is_legacy_unversioned_sample(unit):
                        self.cohort_keys.add(_legacy_cohort_key(c_info.get("horizon")))
                    else:
                        self.cohort_keys.add(_cohort_canonical_key(
                            c_info["decision_model_version"],
                            c_info["evidence_contract_version"] or EVIDENCE_CONTRACT_V0,
                            c_info["price_basis_version"] or PRICE_BASIS_UNSPECIFIED,
                            c_info.get("horizon"),
                        ))

            if not _module_is_qualifying_v2_report(unit):
                self.non_v2_excluded += 1
                continue

            self.qualifying_v2_count += 1

            cat = classify_v2_report_d009_exclusion(unit)
            if cat is not None:
                self.d009_excluded += 1
                self.excluded_counts[cat] = self.excluded_counts.get(cat, 0) + 1
                continue

            self.eligible_count += 1
            self._unit_is_eligible = True
            hold_reasons = collect_hold_semantic_reasons(unit)
            pb_reason = classify_price_basis_exclusion(unit)

            sample_reasons: List[str] = list(hold_reasons)
            if pb_reason is not None:
                sample_reasons.append(pb_reason)

            if pb_reason is not None:
                self.ledger_rest["price_basis_isolated"] += 1
                if pb_reason in (REASON_CONTAMINATED, REASON_PENDING_REVIEW, REASON_CONTRACT_INCOMPLETE):
                    self.ledger_rest[pb_reason] += 1
                else:  # pragma: no cover - defensive, classifier contract is fixed
                    self.ledger_rest[REASON_CONTRACT_INCOMPLETE] += 1

            if hold_reasons:
                self.ledger_rest["hold_semantic_isolated"] += 1
                for hr in hold_reasons:
                    self.ledger_rest[hr] = self.ledger_rest.get(hr, 0) + 1
                if pb_reason is not None:
                    self.ledger_rest["hold_price_basis_overlap"] += 1

            if sample_reasons:
                rid = extract_report_id(unit) or f"<unknown:{id(unit)}>"
                hz = normalize_horizon_label(unit.get("horizon"))
                if hz:
                    rid = f"{rid}@{hz}"
                # exclusion_reasons is a return-path-only artifact; the stream
                # ledger carries the same counts. (Not part of output JSON.)
                continue

            normalized = normalize_report_for_evaluation(unit)
            self.qualifying.append(normalized)
            self.ledger_rest["clean_count"] += 1

    @property
    def prediction_eligible_count(self) -> int:
        return self.eligible_count - self.ledger_rest["hold_semantic_isolated"]

    def ledger(self) -> Dict[str, int]:
        if not self.cohort_homogeneous_key:
            if len(self.cohort_keys) == 1:
                self.cohort_homogeneous_key = next(iter(self.cohort_keys))
            elif not self.cohort_keys:
                self.cohort_homogeneous_key = None
            # mixed keys stay None (homogeneity failed) — the gate evaluation
            # reads it via cohort_meta["canonical_key"] == None and mirrors the
            # module's is_cohort_homogeneous mixed-pool behavior.
        out: Dict[str, int] = {
            "raw_count": self.raw_count,
            "unit_count": self.unit_count,
            "dual_horizon_split_reports": self.dual_horizon_split_reports,
            "qualifying_v2_count": self.qualifying_v2_count,
            "eligible_count": self.eligible_count,
            "non_v2_excluded": self.non_v2_excluded,
            "d009_excluded": self.d009_excluded,
        }
        out.update(self.ledger_rest)
        out["prediction_eligible_count"] = self.prediction_eligible_count
        return out

    def excluded(self) -> Dict[str, int]:
        return dict(self.excluded_counts)


# Writable handle for the fully-streaming SQLite pipeline: run_verify reads the
# qualified sample pool from here instead of a returned list. Kept as a module
# global because the pipeline function must stream rows without retaining them.
_STREAM_LAST_QUALIFYING: Optional[List[Dict[str, Any]]] = None

# excluded_counts of the most recent full-DB (cohort=None) streaming pass —
# the module contract's JSON excluded_counts (pre-cohort).
_FULL_EXCLUDED: Optional[Dict[str, int]] = None


def _stream_run_gate_eval(
    samples: List[Mapping[str, Any]],
    *,
    as_of: Optional[str],
    cohort: Optional[str],
    excluded_counts: Mapping[str, int],
    pipeline_ledger: Mapping[str, int],
    cohort_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Streaming equivalent of evaluate_gate_thresholds for PROJECTED samples.

    Reproduces the module's per-dimension accounting with constant-memory
    state; emitted matrix keys, rounding and verdict logic are identical.
    """
    from collections import Counter
    from datetime import date as _date, datetime as _datetime

    from tradingagents.agents.utils.shadow_credit import (
        H1B_THRESHOLDS, _is_bull, _is_bear, _parse_sample_date,
        PRICE_BASIS_UNSPECIFIED,
        T_PLUS_5_STATUS_SUSPENSION, T_PLUS_5_STATUS_PENDING_DUE,
        T_PLUS_5_STATUS_DUE_AND_EVALUATED, T_PLUS_5_STATUS_DATA_MISSING,
    )
    from tradingagents.dataflows.trade_calendar import calculate_t_plus_5_date, now_cn

    cfg = dict(H1B_THRESHOLDS)

    if as_of is None:
        as_of_date = now_cn().date()
    elif isinstance(as_of, _datetime):
        as_of_date = as_of.date()
    elif isinstance(as_of, str):
        parsed = _parse_sample_date(as_of)
        as_of_date = parsed if parsed is not None else now_cn().date()
    else:
        as_of_date = now_cn().date()

    cal_dates: Optional[Sequence[Any]] = None
    homogeneity_passed = True
    homogeneity_reason = None

    if cohort is not None and str(cohort).strip():
        # Cohort was already applied to the sample pool by the caller; the
        # module path re-filters an idempotent pool and derives identical meta.
        meta = dict(cohort_meta)
    else:
        is_homo, c_key = is_cohort_homogeneous(samples)
        if not is_homo:
            homogeneity_passed = False
            homogeneity_reason = "Mixed cohort generations detected in evaluation samples: cannot merge across cohorts"
        meta = {
            "cohort_type": c_key or "unspecified",
            "canonical_key": c_key or "unspecified",
            "commit_shas": sorted({
                extract_sample_cohort(s)["generated_by_commit_sha"]
                for s in samples
                if extract_sample_cohort(s)["generated_by_commit_sha"]
            }),
        }
    cohort_meta_out = dict(cohort_meta) if (cohort is not None and str(cohort).strip()) else meta

    canonical_key_str = str(cohort_meta_out.get("canonical_key") or "")
    if PRICE_BASIS_UNSPECIFIED in canonical_key_str or (
        not canonical_key_str and any(
            (extract_sample_cohort(s).get("price_basis_version") or PRICE_BASIS_UNSPECIFIED)
            == PRICE_BASIS_UNSPECIFIED
            for s in samples
        )
    ):
        cohort_meta_out["price_basis_unspecified_warning"] = True
        logger.warning(
            "H1b gate evaluation on price_basis.unspecified cohort: "
            "样本未按 T+1 Open 契约 (price_basis.t1_open_v1) 评价，结果仅作记账，"
            "不得作为 H1b 总闸判定依据"
        )

    sample_count = len(samples)

    # ── D1 ────────────────────────────────────────────────────────────────
    symbols: List[str] = []
    industries: List[str] = []
    for s in samples:
        sym = s.get("symbol") or s.get("ticker") or ""
        if sym:
            symbols.append(str(sym).strip())
        ind = extract_report_industry(s) or s.get("industry") or s.get("sector") or ""
        if ind:
            industries.append(str(ind).strip())
    unique_symbols = len(set(symbols))
    unique_industries = len(set(industries))
    symbol_counter = Counter(symbols)
    max_symbol_count = max(symbol_counter.values()) if symbol_counter else 0
    max_symbol_share = (max_symbol_count / sample_count) if sample_count > 0 else 0.0
    pass_d1 = bool(
        sample_count >= cfg["min_sample_count"]
        and unique_symbols >= cfg["min_unique_symbols"]
        and unique_industries >= cfg["min_industries"]
        and max_symbol_share <= cfg["max_single_symbol_ratio"]
    )
    dim_n = {
        "passed": pass_d1,
        "details": {
            "sample_count": sample_count,
            "min_required": cfg["min_sample_count"],
            "unique_symbols": unique_symbols,
            "min_unique_symbols": cfg["min_unique_symbols"],
            "unique_industries": unique_industries,
            "min_industries": cfg["min_industries"],
            "max_symbol_share": round(max_symbol_share, 4),
            "max_allowed_share": cfg["max_single_symbol_ratio"],
        },
    }

    # ── D2 ────────────────────────────────────────────────────────────────
    bull_samples = bear_samples = 0
    bull_verified_claims = bear_verified_claims = 0
    for s in samples:
        inv_state = s.get("investment_debate_state") or s.get("result_data", {}).get("investment_debate_state") or s
        verdict = inv_state.get("manager_verdict") or s.get("manager_verdict") or s.get("result_data", {}).get("manager_verdict") or {}
        winner = str(verdict.get("winner") or s.get("debate_winner") or "").lower()
        direction = str(verdict.get("direction") or s.get("direction") or "").lower()
        if winner == "bull":
            bull_samples += 1
        elif winner == "bear":
            bear_samples += 1
        elif any(w in direction for w in ("多", "buy", "bull")):
            bull_samples += 1
        elif any(w in direction for w in ("空", "sell", "bear")):
            bear_samples += 1
        claims = inv_state.get("claims") or s.get("claims") or s.get("result_data", {}).get("claims") or []
        claim_map = {str(c.get("claim_id", "")).strip(): c for c in claims if isinstance(c, Mapping)}
        summary = (
            verdict.get("claim_evidence_summary")
            or inv_state.get("claim_evidence_summary")
            or s.get("claim_evidence_summary")
            or {}
        )
        if summary:
            for cid_k, info in summary.items():
                if not isinstance(info, Mapping):
                    continue
                matched_c = claim_map.get(str(cid_k).strip(), {})
                sp = str(info.get("speaker_key") or info.get("speaker") or matched_c.get("speaker_key") or matched_c.get("speaker") or "")
                st = str(info.get("stance") or matched_c.get("stance") or "")
                v_cnt = int(info.get("counts", {}).get("verified", 0))
                if _is_bull(sp, st):
                    bull_verified_claims += v_cnt
                elif _is_bear(sp, st):
                    bear_verified_claims += v_cnt
        else:
            for c in claims:
                if not isinstance(c, Mapping):
                    continue
                sp = str(c.get("speaker_key") or c.get("speaker") or "")
                st = str(c.get("stance") or "")
                is_v = bool(c.get("status") == "verified" or c.get("is_verified") is True)
                if is_v:
                    if _is_bull(sp, st):
                        bull_verified_claims += 1
                    elif _is_bear(sp, st):
                        bear_verified_claims += 1
    pass_d2 = bool(
        (bull_samples >= cfg["min_side_samples"] and bear_samples >= cfg["min_side_samples"])
        and (bull_verified_claims >= cfg["min_side_verified_claims"] and bear_verified_claims >= cfg["min_side_verified_claims"])
    )
    dim_side = {
        "passed": pass_d2,
        "details": {
            "bull_samples": bull_samples,
            "bear_samples": bear_samples,
            "min_side_samples": cfg["min_side_samples"],
            "bull_verified_claims": bull_verified_claims,
            "bear_verified_claims": bear_verified_claims,
            "min_verified_claims": cfg["min_side_verified_claims"],
        },
    }

    # ── D3 ────────────────────────────────────────────────────────────────
    parsed_dates = []
    regimes: Set[str] = set()
    for s in samples:
        d = _parse_sample_date(s.get("trade_date") or s.get("date") or s.get("created_at"))
        if d:
            parsed_dates.append(d)
        reg = s.get("market_regime") or s.get("regime")
        if reg:
            regimes.add(str(reg).strip())
    calendar_days = trading_days = 0
    if parsed_dates:
        calendar_days = (max(parsed_dates) - min(parsed_dates)).days + 1
        trading_days = len(set(parsed_dates))
    pass_d3 = bool(calendar_days >= cfg["min_calendar_days"] and trading_days >= cfg["min_trading_days"])
    dim_time = {
        "passed": pass_d3,
        "details": {
            "calendar_days": calendar_days,
            "min_calendar_days": cfg["min_calendar_days"],
            "trading_days": trading_days,
            "min_trading_days": cfg["min_trading_days"],
            "market_regimes_covered": list(regimes),
        },
    }

    # ── D4 ────────────────────────────────────────────────────────────────
    due_t5_count = completed_t5_count = 0
    for s in samples:
        res_data = s.get("result_data") if isinstance(s.get("result_data"), Mapping) else {}
        metrics = s.get("shadow_credit_metrics")
        if not metrics or not isinstance(metrics, Mapping):
            metrics = res_data.get("shadow_credit_metrics") if isinstance(res_data.get("shadow_credit_metrics"), Mapping) else None
        if not metrics or not isinstance(metrics, Mapping):
            metrics = calculate_shadow_credit_metrics(s)
        hit = metrics.get("t_plus_5_direction_hit")
        st = s.get("t_plus_5_status") or metrics.get("t_plus_5_status") or res_data.get("t_plus_5_status")
        if (
            st == T_PLUS_5_STATUS_SUSPENSION
            or s.get("is_suspended") is True
            or s.get("suspension") is True
            or res_data.get("is_suspended") is True
            or res_data.get("suspension") is True
        ):
            continue
        if (
            st == T_PLUS_5_STATUS_PENDING_DUE
            or s.get("is_in_flight") is True
            or s.get("is_t_plus_5_due") is False
            or res_data.get("is_in_flight") is True
            or res_data.get("is_t_plus_5_due") is False
        ):
            continue
        is_due = s.get("is_t_plus_5_due")
        if is_due is None and res_data:
            is_due = res_data.get("is_t_plus_5_due")
        if is_due is None:
            if (
                (hit is not None)
                or bool(s.get("t_plus_5_evaluated", False))
                or bool(res_data.get("t_plus_5_evaluated", False))
                or (st in (T_PLUS_5_STATUS_DUE_AND_EVALUATED, T_PLUS_5_STATUS_DATA_MISSING))
            ):
                is_due = True
            else:
                raw_t5_date = (
                    s.get("t_plus_5_date") or metrics.get("t_plus_5_date") or res_data.get("t_plus_5_date")
                )
                t5_parsed = _parse_sample_date(raw_t5_date) if raw_t5_date else None
                if t5_parsed is not None:
                    is_due = bool(t5_parsed <= as_of_date)
                else:
                    raw_td = (
                        s.get("trade_date") or s.get("date") or res_data.get("trade_date") or res_data.get("date")
                    )
                    td_parsed = _parse_sample_date(raw_td) if raw_td else None
                    if td_parsed is not None:
                        td_str = td_parsed.strftime("%Y-%m-%d")
                        calc_t5_str = None
                        try:
                            calc_t5_str = calculate_t_plus_5_date(td_str, calendar_dates=cal_dates)
                        except Exception as exc:
                            logger.debug("calculate_t_plus_5_date in gate evaluation failed for %s: %s", td_str, exc)
                            calc_t5_str = None
                        if calc_t5_str:
                            calc_t5_parsed = _parse_sample_date(calc_t5_str)
                            if calc_t5_parsed is not None and calc_t5_parsed <= as_of_date:
                                is_due = True
                            else:
                                is_due = False
                        else:
                            is_due = False
                    else:
                        is_due = False
        if is_due:
            due_t5_count += 1
            if hit is not None:
                completed_t5_count += 1
    if due_t5_count == 0:
        t5_completeness_rate = 0.0
        pass_d4 = False
    else:
        t5_completeness_rate = completed_t5_count / due_t5_count
        pass_d4 = bool(t5_completeness_rate >= cfg["min_t_plus_5_completeness"])
    dim_t5_details: Dict[str, Any] = {
        "due_count": due_t5_count,
        "completed_count": completed_t5_count,
        "completeness_rate": round(t5_completeness_rate, 4),
        "min_required_rate": cfg["min_t_plus_5_completeness"],
    }
    if due_t5_count == 0:
        dim_t5_details["reason"] = "no_due_samples"
    dim_t5 = {"passed": pass_d4, "details": dim_t5_details}

    # ── D5 ────────────────────────────────────────────────────────────────
    total_side = bull_samples + bear_samples
    bull_ratio = (bull_samples / total_side) if total_side > 0 else 0.0
    side_diff = abs(bull_samples - bear_samples)
    pass_d5 = bool(
        cfg["min_side_balance_ratio"] <= bull_ratio <= cfg["max_side_balance_ratio"]
        and side_diff <= cfg["max_side_count_diff"]
        and sample_count >= cfg["min_sample_count"]
    )
    dim_balance = {
        "passed": pass_d5,
        "details": {
            "bull_ratio": round(bull_ratio, 4),
            "allowed_range": [cfg["min_side_balance_ratio"], cfg["max_side_balance_ratio"]],
            "side_diff": side_diff,
            "max_allowed_diff": cfg["max_side_count_diff"],
        },
    }

    # ── D6 ────────────────────────────────────────────────────────────────
    bull_v_rates: List[float] = []
    bear_v_rates: List[float] = []
    bull_ch_rates: List[float] = []
    bear_ch_rates: List[float] = []
    consistency_triggers = 0
    claims_text_pool: List[str] = []
    for s in samples:
        metrics = s.get("shadow_credit_metrics")
        if not metrics or not isinstance(metrics, Mapping):
            metrics = calculate_shadow_credit_metrics(s)
        if metrics.get("bull_verified_rate") is not None:
            bull_v_rates.append(float(metrics["bull_verified_rate"]))
        if metrics.get("bear_verified_rate") is not None:
            bear_v_rates.append(float(metrics["bear_verified_rate"]))
        if metrics.get("bull_challenge_adoption_rate") is not None:
            bull_ch_rates.append(float(metrics["bull_challenge_adoption_rate"]))
        if metrics.get("bear_challenge_adoption_rate") is not None:
            bear_ch_rates.append(float(metrics["bear_challenge_adoption_rate"]))
        if metrics.get("manager_consistency_gate_triggered") is True:
            consistency_triggers += 1
        inv_state = s.get("investment_debate_state") or s.get("result_data", {}).get("investment_debate_state") or s
        for c in (inv_state.get("claims") or s.get("claims") or []):
            if isinstance(c, Mapping) and c.get("claim"):
                claims_text_pool.append(str(c["claim"]).strip())
    avg_bull_v = (sum(bull_v_rates) / len(bull_v_rates)) if bull_v_rates else 0.0
    avg_bear_v = (sum(bear_v_rates) / len(bear_v_rates)) if bear_v_rates else 0.0
    delta_verified_rate = abs(avg_bull_v - avg_bear_v)
    avg_bull_ch = (sum(bull_ch_rates) / len(bull_ch_rates)) if bull_ch_rates else 0.0
    avg_bear_ch = (sum(bear_ch_rates) / len(bear_ch_rates)) if bear_ch_rates else 0.0
    delta_challenge_rate = abs(avg_bull_ch - avg_bear_ch)
    unique_claims_count = len(set(claims_text_pool))
    total_claims_count = len(claims_text_pool)
    clone_rate = (1.0 - (unique_claims_count / total_claims_count)) if total_claims_count > 0 else 0.0
    consistency_trigger_rate = (consistency_triggers / sample_count) if sample_count > 0 else 0.0
    pass_d6 = bool(
        delta_verified_rate <= cfg["max_delta_verified_rate"]
        and delta_challenge_rate <= cfg["max_delta_challenge_adoption_rate"]
        and clone_rate <= cfg["max_clone_rate"]
        and consistency_trigger_rate <= cfg["max_consistency_trigger_rate"]
        and sample_count >= cfg["min_sample_count"]
    )
    dim_bias = {
        "passed": pass_d6,
        "details": {
            "avg_bull_verified_rate": round(avg_bull_v, 4),
            "avg_bear_verified_rate": round(avg_bear_v, 4),
            "delta_verified_rate": round(delta_verified_rate, 4),
            "max_allowed_delta_v": cfg["max_delta_verified_rate"],
            "avg_bull_challenge_adoption_rate": round(avg_bull_ch, 4),
            "avg_bear_challenge_adoption_rate": round(avg_bear_ch, 4),
            "delta_challenge_adoption_rate": round(delta_challenge_rate, 4),
            "max_allowed_delta_ch": cfg["max_delta_challenge_adoption_rate"],
            "clone_rate": round(clone_rate, 4),
            "max_allowed_clone_rate": cfg["max_clone_rate"],
            "consistency_trigger_rate": round(consistency_trigger_rate, 4),
            "max_allowed_consistency_rate": cfg["max_consistency_trigger_rate"],
        },
    }

    dim_magnitude = {
        "passed": True,
        "details": {
            "min_weight_multiplier": cfg["min_weight_multiplier"],
            "max_weight_multiplier": cfg["max_weight_multiplier"],
            "range": [cfg["min_weight_multiplier"], cfg["max_weight_multiplier"]],
        },
    }

    matrix = {
        "dimension_n": dim_n,
        "dimension_side": dim_side,
        "dimension_time": dim_time,
        "dimension_t5": dim_t5,
        "dimension_balance": dim_balance,
        "dimension_bias": dim_bias,
        "dimension_magnitude": dim_magnitude,
    }
    if not homogeneity_passed:
        matrix["cohort_homogeneity"] = {"passed": False, "reason": homogeneity_reason}

    all_passed = bool(
        homogeneity_passed
        and (sample_count > 0)
        and dim_n["passed"] and dim_side["passed"] and dim_time["passed"]
        and dim_t5["passed"] and dim_balance["passed"] and dim_bias["passed"]
        and dim_magnitude["passed"]
    )
    recommendation = "ELIGIBLE_FOR_ACTIVATION" if all_passed else "KEEP_FALSE"

    initial_excluded: Dict[str, int] = {
        "legacy_null": 0, "abstain": 0, "invalid_run": 0,
        "data_error": 0, "no_trade": 0, "wait": 0,
    }
    if excluded_counts:
        initial_excluded.update(excluded_counts)
    current_ledger = dict(pipeline_ledger) if pipeline_ledger else None

    return {
        "schema_version": H1B_SCHEMA_VERSION,
        "passed": all_passed,
        "matrix": matrix,
        "summary": {
            "sample_count": sample_count,
            "system_gate_status": "PASS" if all_passed else "FAIL",
            "recommendation": recommendation,
            "cohort": cohort_meta_out.get("canonical_key"),
            "commit_shas": cohort_meta_out.get("commit_shas", []),
            "excluded_counts": dict(initial_excluded),
            "pipeline_ledger": current_ledger,
        },
        "excluded_counts": dict(initial_excluded),
        "pipeline_ledger": current_ledger,
        "recommendation": recommendation,
        "cohort": cohort_meta_out.get("canonical_key"),
        "cohort_info": cohort_meta_out,
    }


def _stream_isolation_eval(
    samples: List[Mapping[str, Any]],
    system_gate_passed: bool,
) -> Dict[str, Any]:
    """Streaming equivalent of evaluate_model_bias_and_weights (projection-safe)."""
    models_set: Set[str] = set()
    for s in samples:
        metrics = s.get("shadow_credit_metrics") or {}
        id_map = metrics.get("model_id_by_stance") or {}
        for m in id_map.values():
            if m and isinstance(m, str):
                models_set.add(m.strip())
        inv_state = s.get("investment_debate_state") or s
        for msg in (inv_state.get("round_messages") or []):
            if isinstance(msg, Mapping):
                m_name = msg.get("model_name") or msg.get("model_id") or msg.get("model")
                if m_name and isinstance(m_name, str):
                    models_set.add(m_name.strip())
    # Module fallback set (identical when no model ids are discoverable).
    if not models_set:
        models_set = {"deepseek-r1", "qwen-max", "gpt-4o"}
    if not system_gate_passed:
        return {
            "credit_weighting_active": False,
            "global_fallback_shadow": True,
            "system_gate_status": "FAIL",
            "model_weights": {m: 1.0 for m in models_set},
            "bias_freeze_reasons": {m: "System-level activation gates not passed" for m in models_set},
            "abnormal_model_ratio": 1.0,
        }
    model_weights: Dict[str, float] = {m: 1.05 for m in sorted(models_set)}
    return {
        "credit_weighting_active": True,
        "global_fallback_shadow": False,
        "system_gate_status": "PASS",
        "model_weights": model_weights,
        "bias_freeze_reasons": {},
        "abnormal_model_ratio": 0.0,
    }


# Full column list of api.database.ReportDB.to_dict() minus the whitelist
# already projected by STREAM_ROW_COLUMNS — used by the streaming read path
# only, as an ordering-independent field-presence witness (DAV-1767).
_TODICT_ALL_COLUMNS: Tuple[str, ...] = (
    "id", "user_id", "symbol", "trade_date", "industry",
    "decision", "direction", "confidence", "probability",
    "target_price", "stop_loss_price", "analysis_status", "trade_action",
    "risk_status", "result_data", "risk_items", "key_metrics", "data_gaps",
    "falsification_conditions", "not_applicable", "analyst_traces",
    "market_report", "sentiment_report", "news_report", "fundamentals_report",
    "macro_report", "smart_money_report", "volume_price_report",
    "game_theory_report", "investment_plan", "trader_investment_plan",
    "final_trade_decision", "created_at", "updated_at",
)

# Columns re-read row-by-row in the streaming DB path: everything from
# to_dict() except the three non-evaluated structural list columns, which the
# evaluation never consumes (ReportDB.to_dict parity is enforced by tests).
STREAM_ROW_COLUMNS: Tuple[str, ...] = tuple(
    c for c in _TODICT_ALL_COLUMNS if c not in {"risk_items", "key_metrics", "analyst_traces"}
)


def _stream_run_sqlite_pipeline(
    db_path: str,
    *,
    cohort: Optional[Union[str, Mapping[str, Any]]] = None,
) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, Any]]:
    """Fully-streaming SQLite pipeline (DAV-1767).

    Streams completed rows one-by-one by primary key, projects and feeds each
    row into the aggregator immediately; no full-residency load at any point.
    Cohort filtering runs per-unit (equivalent to the module's post-filter on
    the split-unit pool: horizon/version fields live on units). Returns
    (excluded_counts, ledger, cohort_meta); the qualified pool is exposed via
    ``_STREAM_LAST_QUALIFYING``.
    """
    global _STREAM_LAST_QUALIFYING
    _STREAM_LAST_QUALIFYING = None

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from api.database import ReportDB, _ensure_report_schema

    p = Path(db_path)
    if not p.is_absolute():
        if not p.exists():
            p_root = Path(project_root) / p
            if p_root.exists():
                p = p_root
    if not p.exists():
        logger.error("指定的 SQLite 数据库路径不存在: %s", db_path)
        raise FileNotFoundError(f"指定的 SQLite 数据库路径不存在: {db_path}")
    abs_path = str(p.resolve())

    spec_meta: Optional[Dict[str, Any]] = None
    if cohort is not None and str(cohort).strip():
        from tradingagents.agents.utils.shadow_credit import parse_cohort_spec

        spec_meta = parse_cohort_spec(cohort)

    agg = _StreamAggregator()
    engine = create_engine(f"sqlite:///{abs_path}", connect_args={"check_same_thread": False})
    try:
        _ensure_report_schema(target_engine=engine)
        SessionCls = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = SessionCls()
        try:
            id_manifest: List[str] = [
                rid
                for (rid,) in session.query(ReportDB.id)
                .filter(ReportDB.status == "completed")
                .yield_per(500)
            ]
            logger.info(
                "从 SQLite 数据库 %s 流式读取 %d 份 completed 报告（逐行主键回读，投影 %d 列）",
                abs_path, len(id_manifest), len(STREAM_ROW_COLUMNS),
            )
            for rid in id_manifest:
                row = (
                    session.query(*[getattr(ReportDB, c) for c in STREAM_ROW_COLUMNS])
                    .filter(ReportDB.id == rid)
                    .first()
                )
                if row is None:
                    continue
                data: Dict[str, Any] = dict(zip(STREAM_ROW_COLUMNS, row))
                if data.get("created_at") is not None:
                    data["created_at"] = data["created_at"].isoformat()
                if data.get("updated_at") is not None:
                    data["updated_at"] = data["updated_at"].isoformat()
                agg.feed(data, cohort_spec=spec_meta)
                del row, data
        finally:
            session.close()
    finally:
        engine.dispose()

    if spec_meta is not None:
        # Cohort-filtered run: cohort_meta is the parsed spec's meta (identical
        # shape to filter_reports_by_cohort's output, commit_shas accumulated
        # during the streaming pass).
        cohort_meta = dict(spec_meta)
        cohort_meta["commit_shas"] = sorted(agg.cohort_shas)
    else:
        # Homogeneity over the qualified pool's cohort keys (collected during
        # the streaming pass; the decision only needs the key set, not rows).
        cohort_meta = {
            "cohort_type": agg.cohort_homogeneous_key or "unspecified",
            "canonical_key": agg.cohort_homogeneous_key or "unspecified",
            "commit_shas": sorted(agg.cohort_shas),
        }

    global _FULL_EXCLUDED
    _FULL_EXCLUDED = agg.excluded()
    _STREAM_LAST_QUALIFYING = agg.qualifying
    logger.info(
        "【四段台账 (DAV-1322)】从指定数据库共检索到 %d 份原始报告，双档拆包后 %d 个评估单元 (双档报告 %d 份)，筛选出 %d 份结构化证据齐备单元 (不合格排除 %d 个)；贯彻 D-009 §5 筛选出 %d 份合格样本 (排除 %d 份: legacy_null=%d, abstain=%d, invalid_run=%d, data_error=%d, no_trade=%d, wait=%d)",
        agg.raw_count,
        agg.unit_count,
        agg.dual_horizon_split_reports,
        agg.qualifying_v2_count,
        agg.non_v2_excluded,
        agg.eligible_count,
        agg.d009_excluded,
        agg.excluded_counts.get("legacy_null", 0),
        agg.excluded_counts.get("abstain", 0),
        agg.excluded_counts.get("invalid_run", 0),
        agg.excluded_counts.get("data_error", 0),
        agg.excluded_counts.get("no_trade", 0),
        agg.excluded_counts.get("wait", 0),
    )
    return agg.excluded(), agg.ledger(), cohort_meta


def load_reports_from_db(
    db_path: Optional[str] = None,
    input_file: Optional[str] = None,
    input_dir: Optional[str] = None,
    return_excluded_counts: bool = False,
    return_ledger: bool = False,
) -> Union[
    List[Dict[str, Any]],
    Tuple[List[Dict[str, Any]], Dict[str, int]],
    Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, int]],
]:
    """Load reports from SQLite database, input file/dir, or fallback paths, filtering strictly for completed v2 samples.

    DAV-1767/DAV-1803: this helper is a LIST-BASED loader used by tests and
    the file/dir fallback surface. The fully-streaming SQLite pipeline lives
    in ``_stream_run_sqlite_pipeline`` (used by ``run_verify --db-path``);
    the SQLite branch here shares the same per-row re-read pattern but
    accumulates the reduced rows into ``raw_reports`` by contract (callers
    need the materialized sample list).
    """
    raw_reports: List[Dict[str, Any]] = []

    # 1. Explicit SQLite DB path (prioritized and strict: fails explicitly if invalid/inaccessible)
    if db_path and str(db_path).strip():
        p = Path(db_path)
        if not p.is_absolute():
            if not p.exists():
                p_root = Path(project_root) / p
                if p_root.exists():
                    p = p_root
        if not p.exists():
            logger.error("指定的 SQLite 数据库路径不存在: %s", db_path)
            raise FileNotFoundError(f"指定的 SQLite 数据库路径不存在: {db_path}")

        abs_path = str(p.resolve())
        db_url = f"sqlite:///{abs_path}"
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from api.database import ReportDB, _ensure_report_schema

            engine = create_engine(db_url, connect_args={"check_same_thread": False})
            try:
                _ensure_report_schema(target_engine=engine)
                SessionCls = sessionmaker(autocommit=False, autoflush=False, bind=engine)
                session = SessionCls()
                try:
                    # DAV-1767/DAV-1803: per-row re-read with the explicit
                    # reduced column projection (memory profile is bounded
                    # per row; the reduced rows are accumulated here by
                    # list-loader contract — see docstring).
                    id_manifest: List[str] = [
                        rid
                        for (rid,) in session.query(ReportDB.id)
                        .filter(ReportDB.status == "completed")
                        .yield_per(500)
                    ]
                    logger.info(
                        "从 SQLite 数据库 %s 流式读取 %d 份 completed 报告（逐行主键回读，投影 %d 列）",
                        abs_path, len(id_manifest), len(STREAM_ROW_COLUMNS),
                    )
                    for rid in id_manifest:
                        row = (
                            session.query(*[getattr(ReportDB, c) for c in STREAM_ROW_COLUMNS])
                            .filter(ReportDB.id == rid)
                            .first()
                        )
                        if row is None:
                            continue
                        data: Dict[str, Any] = dict(zip(STREAM_ROW_COLUMNS, row))
                        if data.get("created_at") is not None:
                            data["created_at"] = data["created_at"].isoformat()
                        if data.get("updated_at") is not None:
                            data["updated_at"] = data["updated_at"].isoformat()
                        raw_reports.append(data)
                finally:
                    session.close()
            finally:
                engine.dispose()
        except Exception as exc:
            logger.error("读取指定 SQLite 数据库 %s 失败: %s", db_path, exc)
            raise RuntimeError(f"读取指定 SQLite 数据库 {db_path} 失败: {exc}") from exc

        # When explicit db_path is provided, strictly return filtered results from this db without fallback
        v2_reports, excluded_counts, ledger = _module_filter_v2_completed_reports(raw_reports, return_ledger=True)
        logger.info(
            "【四段台账 (DAV-1322)】从指定数据库共检索到 %d 份原始报告，双档拆包后 %d 个评估单元 (双档报告 %d 份)，筛选出 %d 份结构化证据齐备单元 (不合格排除 %d 个)；贯彻 D-009 §5 筛选出 %d 份合格样本 (排除 %d 份: legacy_null=%d, abstain=%d, invalid_run=%d, data_error=%d, no_trade=%d, wait=%d)",
            ledger["raw_count"],
            ledger.get("unit_count", ledger["raw_count"]),
            ledger.get("dual_horizon_split_reports", 0),
            ledger["qualifying_v2_count"],
            ledger["non_v2_excluded"],
            ledger["eligible_count"],
            ledger["d009_excluded"],
            excluded_counts.get("legacy_null", 0),
            excluded_counts.get("abstain", 0),
            excluded_counts.get("invalid_run", 0),
            excluded_counts.get("data_error", 0),
            excluded_counts.get("no_trade", 0),
            excluded_counts.get("wait", 0),
        )
        if return_ledger:
            return v2_reports, excluded_counts, ledger
        if return_excluded_counts:
            return v2_reports, excluded_counts
        return v2_reports

    # 2. Explicit input file
    if input_file:
        p = Path(input_file)
        if not p.is_absolute():
            p = Path(project_root) / p
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if "samples" in data and isinstance(data["samples"], list):
                        raw_reports.extend([dict(s) for s in data["samples"] if isinstance(s, dict)])
                    else:
                        raw_reports.append(data)
                elif isinstance(data, list):
                    raw_reports.extend([dict(s) for s in data if isinstance(s, dict)])
                logger.info("从 JSON 文件 %s 中加载了 %d 份原始样本", p.name, len(raw_reports))
            except Exception as e:
                logger.warning("读取 input-file %s 失败: %s", p, e)

    # 3. Explicit input directory
    if input_dir and not raw_reports:
        p_dir = Path(input_dir)
        if not p_dir.is_absolute():
            p_dir = Path(project_root) / p_dir
        if p_dir.exists() and p_dir.is_dir():
            for fpath in sorted(p_dir.glob("*.json")):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        if "samples" in data and isinstance(data["samples"], list):
                            raw_reports.extend([dict(s) for s in data["samples"] if isinstance(s, dict)])
                        else:
                            raw_reports.append(data)
                    elif isinstance(data, list):
                        raw_reports.extend([dict(s) for s in data if isinstance(s, dict)])
                except Exception as e:
                    logger.debug("读取 input-dir 文件 %s 失败: %s", fpath.name, e)
            if raw_reports:
                logger.info("从目录 %s 中加载了 %d 份原始样本", p_dir.name, len(raw_reports))

    # 4. Try loading via sqlalchemy ReportDB if db exists
    if not raw_reports:
        try:
            from sqlalchemy import inspect as sa_inspect
            from api.database import get_db_ctx, ReportDB, _ensure_report_schema, engine
            insp = sa_inspect(engine)
            if insp.has_table("reports"):
                _ensure_report_schema(target_engine=engine)
                with get_db_ctx() as db:
                    db_reports = db.query(ReportDB).filter(ReportDB.status == "completed").all()
                    for r in db_reports:
                        data = r.to_dict()
                        raw_reports.append(data)
                if raw_reports:
                    logger.info("从数据库中加载了 %d 份 completed 报告记录", len(raw_reports))
        except Exception as exc:
            logger.error("从数据库加载报告或 schema 迁移失败: %s", exc)
            raise RuntimeError(f"从数据库加载报告或 schema 迁移失败: {exc}") from exc

    # 5. Check golden audit reports as fallback/supplement
    if not raw_reports:
        golden_dir = os.path.join(project_root, "tests", "golden", "audit_20260823")
        if os.path.exists(golden_dir):
            for fname in sorted(os.listdir(golden_dir)):
                if fname.endswith("_result_data.json"):
                    fpath = os.path.join(golden_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if isinstance(data, dict):
                                raw_reports.append(data)
                    except Exception as e:
                        logger.debug("读取 golden 报告 %s 失败: %s", fname, e)

    # 6. Filter strictly for completed v2 reports with winner and D-009 §5 analysis_status
    v2_reports, excluded_counts, ledger = _module_filter_v2_completed_reports(raw_reports, return_ledger=True)
    logger.info(
        "【四段台账 (DAV-1322)】共检索到 %d 份原始样本，双档拆包后 %d 个评估单元 (双档报告 %d 份)，筛选出 %d 份结构化证据齐备单元 (不合格排除 %d 个)；贯彻 D-009 §5 筛选出 %d 份合格样本 (排除 %d 份: legacy_null=%d, abstain=%d, invalid_run=%d, data_error=%d, no_trade=%d, wait=%d)",
        ledger["raw_count"],
        ledger.get("unit_count", ledger["raw_count"]),
        ledger.get("dual_horizon_split_reports", 0),
        ledger["qualifying_v2_count"],
        ledger["non_v2_excluded"],
        ledger["eligible_count"],
        ledger["d009_excluded"],
        excluded_counts.get("legacy_null", 0),
        excluded_counts.get("abstain", 0),
        excluded_counts.get("invalid_run", 0),
        excluded_counts.get("data_error", 0),
        excluded_counts.get("no_trade", 0),
        excluded_counts.get("wait", 0),
    )
    if return_ledger:
        return v2_reports, excluded_counts, ledger
    if return_excluded_counts:
        return v2_reports, excluded_counts
    return v2_reports


def format_gates_matrix_text(evaluation: Dict[str, Any], cohort_info: Optional[Dict[str, Any]] = None) -> str:
    """Format evaluation matrix to clean terminal table."""
    matrix = evaluation.get("matrix", {})
    summary = evaluation.get("summary", {})
    passed = evaluation.get("passed", False)
    rec = evaluation.get("recommendation", "KEEP_FALSE")
    cohort_key = evaluation.get("cohort") or (cohort_info.get("canonical_key") if cohort_info else "unspecified")
    shas = (cohort_info.get("commit_shas") if cohort_info else None) or evaluation.get("commit_shas") or summary.get("commit_shas") or []
    sha_str = f"{len(shas)} SHAs ({', '.join(shas[:2])}...)" if len(shas) > 2 else (', '.join(shas) if shas else "None")

    lines = [
        "=" * 80,
        "P3-H1b 信用加权门槛校验报告 (7维门槛矩阵 - Cohort 隔离版)",
        f"【评测 Cohort】: {cohort_key}",
        f"【Commit SHAs】: {sha_str}",
        "=" * 80,
    ]

    d_n = matrix.get("dimension_n", {})
    d_side = matrix.get("dimension_side", {})
    d_time = matrix.get("dimension_time", {})
    d_t5 = matrix.get("dimension_t5", {})
    d_balance = matrix.get("dimension_balance", {})
    d_bias = matrix.get("dimension_bias", {})
    d_mag = matrix.get("dimension_magnitude", {})

    def _st(p: bool) -> str:
        return "[ PASS ]" if p else "[ FAIL ]"

    # 1. N
    det_n = d_n.get("details", {})
    lines.append(
        f"1. 样本与覆盖 (N)      : {_st(d_n.get('passed', False))} "
        f"样本量={det_n.get('sample_count', 0)}/{det_n.get('min_required', 60)}, "
        f"标的数={det_n.get('unique_symbols', 0)}/{det_n.get('min_unique_symbols', 20)}, "
        f"行业数={det_n.get('unique_industries', 0)}/{det_n.get('min_industries', 5)}, "
        f"单标的占比={det_n.get('max_symbol_share', 0.0):.1%}<={det_n.get('max_allowed_share', 0.15):.1%}"
    )

    # 2. Side
    det_side = d_side.get("details", {})
    lines.append(
        f"2. 分侧样本与Claims    : {_st(d_side.get('passed', False))} "
        f"多空样本={det_side.get('bull_samples', 0)}/{det_side.get('bear_samples', 0)} (各>={det_side.get('min_side_samples', 25)}), "
        f"Verified Claims={det_side.get('bull_verified_claims', 0)}/{det_side.get('bear_verified_claims', 0)} (各>={det_side.get('min_verified_claims', 100)})"
    )

    # 3. Time
    det_time = d_time.get("details", {})
    lines.append(
        f"3. 时间跨度与市场状态  : {_st(d_time.get('passed', False))} "
        f"自然日={det_time.get('calendar_days', 0)}/{det_time.get('min_calendar_days', 45)}, "
        f"交易日={det_time.get('trading_days', 0)}/{det_time.get('min_trading_days', 30)}, "
        f"覆盖状态={det_time.get('market_regimes_covered', [])}"
    )

    # 4. T+5
    det_t5 = d_t5.get("details", {})
    lines.append(
        f"4. T+5 完整率          : {_st(d_t5.get('passed', False))} "
        f"完整率={det_t5.get('completeness_rate', 0.0):.1%}/{det_t5.get('min_required_rate', 0.95):.1%} "
        f"(已评估={det_t5.get('completed_count', 0)}/到期={det_t5.get('due_count', 0)})"
    )

    # 5. Balance
    det_bal = d_balance.get("details", {})
    lines.append(
        f"5. 多空平衡性          : {_st(d_balance.get('passed', False))} "
        f"多头占比={det_bal.get('bull_ratio', 0.0):.1%} (区间 [40.0%, 60.0%]), "
        f"多空差值={det_bal.get('side_diff', 0)}<={det_bal.get('max_allowed_diff', 10)}"
    )

    # 6. Bias Freeze
    det_bias = d_bias.get("details", {})
    lines.append(
        f"6. 偏置冻结指标        : {_st(d_bias.get('passed', False))} "
        f"Δverified={det_bias.get('delta_verified_rate', 0.0):.1%}<={det_bias.get('max_allowed_delta_v', 0.18):.1%}, "
        f"Δchallenge={det_bias.get('delta_challenge_adoption_rate', 0.0):.1%}<={det_bias.get('max_allowed_delta_ch', 0.25):.1%}, "
        f"克隆率={det_bias.get('clone_rate', 0.0):.1%}<={det_bias.get('max_allowed_clone_rate', 0.05):.1%}, "
        f"自洽硬闸触发率={det_bias.get('consistency_trigger_rate', 0.0):.1%}<={det_bias.get('max_allowed_consistency_rate', 0.05):.1%}"
    )

    # 7. Magnitude
    det_mag = d_mag.get("details", {})
    lines.append(
        f"7. 加权幅度范围        : {_st(d_mag.get('passed', True))} "
        f"系数范围={det_mag.get('range', [0.85, 1.15])}"
    )

    ex_counts = evaluation.get("excluded_counts") or summary.get("excluded_counts") or {}
    total_excluded = sum(ex_counts.values()) if ex_counts else 0
    ledger = evaluation.get("pipeline_ledger") or summary.get("pipeline_ledger")
    lines.append("-" * 80)
    if ledger:
        lines.append(
            f"【四段台账 (DAV-783/DAV-1322)】: 原始报告={ledger.get('raw_count', 0)} -> "
            f"拆包单元={ledger.get('unit_count', ledger.get('raw_count', 0))} "
            f"(双档报告={ledger.get('dual_horizon_split_reports', 0)}) -> "
            f"结构化候选={ledger.get('qualifying_v2_count', 0)} -> "
            f"D-009合格={ledger.get('eligible_count', 0)} "
            f"(不合格排除={ledger.get('non_v2_excluded', 0)})"
        )
        lines.append(
            f"【Stage 3.5/4 隔离台账 (DAV-1139/1200)】: "
            f"hold_defensive={ledger.get('hold_defensive', 0)}, "
            f"hold_conflict={ledger.get('hold_conflict', 0)}, "
            f"hold_unresolved={ledger.get('hold_unresolved', 0)} "
            f"(hold_semantic_isolated={ledger.get('hold_semantic_isolated', 0)}, "
            f"prediction_eligible={ledger.get('prediction_eligible_count', 0)}) | "
            f"price_basis_isolated={ledger.get('price_basis_isolated', 0)} "
            f"(overlap={ledger.get('hold_price_basis_overlap', 0)}) -> "
            f"primary_clean={ledger.get('clean_count', 0)}"
        )
    lines.append(f"【排除样本分类计数 (D-009 §5)】: 总排除={total_excluded}")
    lines.append(
        f"  legacy_null={ex_counts.get('legacy_null', 0)}, "
        f"abstain={ex_counts.get('abstain', 0)}, "
        f"invalid_run={ex_counts.get('invalid_run', 0)}, "
        f"data_error={ex_counts.get('data_error', 0)}, "
        f"no_trade={ex_counts.get('no_trade', 0)}, "
        f"wait={ex_counts.get('wait', 0)}"
    )

    lines.append("-" * 80)
    lines.append(f"【系统级门槛总状态】: {'PASS (全部通过)' if passed else 'FAIL (未达标)'}")
    lines.append(f"【Feature Flag 建议】: {'可开启 (ELIGIBLE_FOR_ACTIVATION)' if passed else '严禁开启，保持默认关闭 (KEEP_FALSE)'}")
    lines.append("=" * 80)

    return "\n".join(lines)


def run_verify(
    db_path: Optional[str] = None,
    output_json: Optional[str] = None,
    input_file: Optional[str] = None,
    input_dir: Optional[str] = None,
    as_of: Optional[str] = None,
    cohort: Optional[Union[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Execute gate verification and generate structured report under cohort isolation.

    DAV-1767: runs the streaming pipeline (row → horizon units → whitelist
    projection → staged counting → projected sample pool). Output structure,
    keys and verdict semantics are identical to the module evaluation path;
    equivalence is enforced by TestH1bVerifyGatesStreamingEquivalence in
    tests/test_h1b_gates.py (staged ledger/matrix/isolation assertions on
    qualifying, exotic and dual-horizon pools, incl. the file/dir branch).
    """
    # DAV-1767: for the SQLite path the whole pipeline runs row-by-row (no
    # full-residency load); file/dir inputs stay list-based (evaluation JSON).
    if db_path and str(db_path).strip():
        # Two-pass streaming (DAV-1767): pass 1 = full-DB pipeline for the
        # FULL ledger + excluded_counts (identical to the module path's JSON
        # accounting, which always reflects the whole DB regardless of
        # cohort); pass 2 = cohort-scoped pipeline for the evaluation pool.
        _, full_ledger, full_meta = _stream_run_sqlite_pipeline(db_path, cohort=None)
        full_excluded = dict(_FULL_EXCLUDED or {})
        _, _, cohort_meta = _stream_run_sqlite_pipeline(
            db_path, cohort=cohort,
        )
        agg_qualifying_ref = _STREAM_LAST_QUALIFYING
        samples = list(agg_qualifying_ref or [])
        # Module contract (DAV-783/1322): excluded_counts in the JSON come from
        # the FULL-DB module filter (pre-cohort); the cohort pass's D-009
        # counters are diagnostic only.
        initial_excluded, ledger = full_excluded, full_ledger
    else:
        loaded_res = load_reports_from_db(
            input_file=input_file,
            input_dir=input_dir,
            return_ledger=True,
        )
        if isinstance(loaded_res, tuple) and len(loaded_res) == 3:
            reports, initial_excluded, ledger = loaded_res
        elif isinstance(loaded_res, tuple) and len(loaded_res) == 2:
            reports, initial_excluded = loaded_res
            ledger = {}
        else:
            reports, initial_excluded, ledger = loaded_res, {}, {}

        cohort_meta = {}
        if cohort is not None and str(cohort).strip():
            filtered_reports, cohort_meta = filter_reports_by_cohort(reports, cohort=cohort)
            reports = filtered_reports
        else:
            # Check homogeneity if cohort not specified
            is_homo, c_key = is_cohort_homogeneous(reports)
            cohort_meta = {
                "cohort_type": c_key or "unspecified",
                "canonical_key": c_key or "unspecified",
                "commit_shas": sorted({
                    extract_sample_cohort(r)["generated_by_commit_sha"]
                    for r in reports
                    if extract_sample_cohort(r)["generated_by_commit_sha"]
                }),
            }

        # Streaming pipeline: one pass over the (cohort-filtered) reports.
        agg = _StreamAggregator()
        for r in reports:
            agg.feed(r)
        samples = agg.qualifying

    gate_eval = _stream_run_gate_eval(
        samples,
        as_of=as_of,
        cohort=cohort,
        excluded_counts=initial_excluded,
        pipeline_ledger=ledger,
        cohort_meta=cohort_meta,
    )
    isolation_eval = _stream_isolation_eval(
        samples,
        system_gate_passed=gate_eval["passed"],
    )

    cohort_label = cohort_meta.get("canonical_key") or (str(cohort) if cohort else "unspecified")
    report_result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": "P3-H1b",
        "cohort": cohort_label,
        "cohort_info": cohort_meta,
        "raw_sample_count": ledger.get("raw_count", len(samples)),
        "qualifying_v2_count": ledger.get("qualifying_v2_count", len(samples)),
        "sample_count": len(samples),
        "excluded_counts": gate_eval.get("excluded_counts", initial_excluded),
        "pipeline_ledger": ledger,
        "gate_evaluation": gate_eval,
        "model_isolation": isolation_eval,
        "recommendation": gate_eval["recommendation"],
    }

    # Print terminal output
    print(format_gates_matrix_text(gate_eval, cohort_info=cohort_meta))
    print(f"分层隔离状态: credit_weighting_active={isolation_eval.get('credit_weighting_active')}, global_fallback_shadow={isolation_eval.get('global_fallback_shadow')}")
    print(f"模型权重分配: {isolation_eval.get('model_weights')}")
    if isolation_eval.get("bias_freeze_reasons"):
        print(f"偏置冻结原因: {isolation_eval.get('bias_freeze_reasons')}")

    if output_json:
        out_dir = os.path.dirname(output_json)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(report_result, f, ensure_ascii=False, indent=2)
        print(f"\n[产物生成] 结构化门槛校验 JSON 已写入: {output_json}")

    return report_result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P3-H1b 信用加权门槛校验脚本")
    parser.add_argument(
        "--cohort",
        type=str,
        default=None,
        help="Cohort 标识 (必填, 如 legacy_unversioned 或四元版本 decision_model.v1:evidence_contract.v0:price_basis.unspecified:short；第四元档位缺省为 horizon.unspecified)",
    )
    parser.add_argument("--db-path", type=str, default=None, help="SQLite 数据库路径")
    parser.add_argument("--input-file", type=str, default=None, help="指定评测 JSON 文件路径")
    parser.add_argument("--input-dir", type=str, default=None, help="指定评测 JSON 目录路径")
    parser.add_argument("--output-json", type=str, default="work/h1b_gates_report.json", help="输出汇总 JSON 路径")
    parser.add_argument("--as-of", type=str, default=None, help="评估基准日期 (YYYY-MM-DD)")
    args = parser.parse_args()

    # Fail-closed: 未传 --cohort 必须非零退出，严禁输出 PASS 报告
    if not args.cohort or not args.cohort.strip():
        logger.error("未传 --cohort 参数：必须显式指定 cohort（如 --cohort=legacy_unversioned 或三元版本）（fail-closed）")
        sys.exit(2)

    try:
        res = run_verify(
            db_path=args.db_path,
            output_json=args.output_json,
            input_file=args.input_file,
            input_dir=args.input_dir,
            as_of=args.as_of,
            cohort=args.cohort,
        )
    except Exception as exc:
        logger.error("门槛校验执行失败: %s", exc)
        sys.exit(1)
    # Exit code: 0 if valid execution
    sys.exit(0)
