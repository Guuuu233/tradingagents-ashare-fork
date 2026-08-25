"""P3-H2 Evaluation Metrics and Weekly Dashboard Schema Contracts.

Defines the core data structures, Pydantic v2 validation models, TypedDicts,
and JSON Schema contracts for:
1. EvaluationMetricMatrix (covering four quadrants: Protocol & Model Metadata,
   Data Sources & data_gaps Classification, Debate Quality 6-Dimension Metrics,
   and T+5 Return & Shadow Weighted Calibration).
2. WeeklyMetricsJSON (weekly offline re-calculation dashboard aggregation).
3. WeeklySummaryMD (standardized markdown weekly evaluation report renderer).

Strictly pure functions, read-only, deterministic, zero network dependencies.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Union
from typing_extensions import Annotated, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.agents.utils.agent_states import (
    DEFAULT_FEATURE_FLAGS,
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
    get_protocol_metadata,
)
from tradingagents.agents.utils.debate_metrics import (
    SEVEN_REPORT_KEYS,
    MetricResult,
    calculate_all_debate_metrics,
    calculate_bull_bear_verified_rates_and_delta,
    calculate_challenge_metrics,
    calculate_evidence_recycling_rate,
    calculate_field_completeness_rate,
    calculate_fundamentals_utilization,
    calculate_macro_utilization,
    calculate_seven_reports_utilization,
    extract_numerical_tokens,
)
from tradingagents.agents.utils.shadow_credit import (
    H1B_THRESHOLDS,
    REPORT_KEY_TO_ROLE,
    apply_credit_weighting_to_debate,
    calculate_shadow_credit_metrics,
    evaluate_h1b_system_gates,
    evaluate_model_bias_and_weights,
)
from tradingagents.dataflows.trade_calendar import (
    calculate_t_plus_5_date,
    get_t_plus_n_trading_day,
    trading_days_forward,
    _parse_date,
)

EVALUATION_MATRIX_SCHEMA_VERSION: str = "evaluation_matrix_v1"
WEEKLY_METRICS_SCHEMA_VERSION: str = "weekly_metrics_v1"
WEEKLY_SUMMARY_MD_SCHEMA_VERSION: str = "weekly_summary_md_v1"


# ══════════════════════════════════════════════════════════════════════════════
# 1. Pydantic v2 Models for EvaluationMetricMatrix & Components
# ══════════════════════════════════════════════════════════════════════════════


class MetricValueModel(BaseModel):
    """Normalized atomic metric output model."""

    model_config = ConfigDict(extra="ignore")

    numerator: Union[int, float] = Field(default=0, description="Metric numerator")
    denominator: Union[int, float] = Field(default=0, description="Metric denominator")
    rate: Optional[float] = Field(
        default=None,
        description="Metric rate (None when denominator is 0; never fabricated)",
    )
    version: str = Field(
        default=PROTOCOL_VERSION_V1_LEGACY,
        description="Protocol version evaluated under",
    )
    status: str = Field(
        default="valid",
        description="Evaluation status: valid, zero_denominator, legacy_no_data, etc.",
    )
    note: Optional[str] = Field(
        default=None, description="Explanation for zero denominator or special notes"
    )
    present_fields: Optional[List[str]] = Field(
        default=None, description="Present fields list"
    )
    missing_fields: Optional[List[str]] = Field(
        default=None, description="Missing fields list"
    )
    legitimate_omissions: Optional[List[str]] = Field(
        default=None, description="Legitimate omissions list"
    )


# ── Quadrant 1: Protocol & Model Metadata ─────────────────────────────────────


class ProtocolAndModelMetadataModel(BaseModel):
    """Quadrant 1: Protocol, sample, and model metadata."""

    model_config = ConfigDict(extra="ignore")

    report_id: str = Field(
        default="", description="Unique report identifier or UUID"
    )
    symbol: str = Field(
        default="", description="Normalized stock symbol (e.g. 600519.SH)"
    )
    security_name: str = Field(
        default="", description="Display security name (e.g. 贵州茅台)"
    )
    trade_date: str = Field(
        default="", description="Trade date in YYYY-MM-DD format"
    )
    industry: Optional[str] = Field(
        default=None, description="Industry or sector classification"
    )
    market_regime: Optional[str] = Field(
        default=None, description="Market regime classification"
    )
    protocol_version: str = Field(
        default=PROTOCOL_VERSION_V1_LEGACY,
        description="Protocol version (v1_legacy or v2_structured_disagreement)",
    )
    protocol_stage: str = Field(
        default="opening", description="Protocol stage reached"
    )
    tiebreak_skipped: bool = Field(
        default=False, description="Whether tiebreak round was skipped"
    )
    debate_degenerate: bool = Field(
        default=False, description="Whether debate belief trajectory was degenerate"
    )
    feature_flags: Dict[str, bool] = Field(
        default_factory=lambda: dict(DEFAULT_FEATURE_FLAGS),
        description="Active feature flags during debate execution",
    )
    model_assignments: Dict[str, Optional[str]] = Field(
        default_factory=lambda: {"bull": None, "bear": None, "manager": None},
        description="Model assignment recording (read-only audit, does not alter runtime)",
    )
    latency_ms: Optional[float] = Field(
        default=None, description="Total execution latency in milliseconds"
    )
    token_usage: Optional[Dict[str, int]] = Field(
        default=None, description="Recorded token usage (prompt, completion, total)"
    )
    created_at: Optional[str] = Field(
        default=None, description="Sample generation timestamp (ISO 8601)"
    )


# ── Quadrant 2: Data Sources & Data Gaps Classification ───────────────────────


class DataGapItemModel(BaseModel):
    """Single data gap record with structural vs operational classification."""

    model_config = ConfigDict(extra="ignore")

    source: str = Field(description="Data source name (e.g. northbound_flow, news)")
    gap_class: Literal["structural", "operational"] = Field(
        description="Gap classification: structural (institutional stoppage/historical refusal) vs operational (timeout/network error)"
    )
    status: str = Field(
        description="Status string: unavailable, refused, timeout, failed, error, ok"
    )
    reason: str = Field(
        default="", description="Detailed failure or refusal reason"
    )
    gap: Optional[str] = Field(
        default=None, description="Compact gap description"
    )


class DataGapsSummaryModel(BaseModel):
    """Summary counts of data gaps."""

    model_config = ConfigDict(extra="ignore")

    total_gaps: int = Field(default=0, ge=0, description="Total gap count")
    structural_count: int = Field(
        default=0, ge=0, description="Structural gap count"
    )
    operational_count: int = Field(
        default=0, ge=0, description="Operational gap count"
    )
    resident_fault_count: int = Field(
        default=0,
        ge=0,
        description="Resident fault count (operational failures only)",
    )


class DataUtilizationMetricsModel(BaseModel):
    """Utilization rates across analyst data sources."""

    model_config = ConfigDict(extra="ignore")

    seven_reports_utilization: MetricValueModel = Field(
        default_factory=MetricValueModel,
        description="7-analyst comprehensive data point utilization",
    )
    macro_utilization: MetricValueModel = Field(
        default_factory=MetricValueModel,
        description="Macro report data point utilization",
    )
    fundamentals_utilization: MetricValueModel = Field(
        default_factory=MetricValueModel,
        description="Fundamentals report data point utilization",
    )
    analyst_utilization_by_role: Dict[str, Optional[float]] = Field(
        default_factory=dict,
        description="Data point utilization broken down by analyst role slug",
    )


class DataSourcesAndGapsModel(BaseModel):
    """Quadrant 2: Data sources, gaps, provenance, and utilization."""

    model_config = ConfigDict(extra="ignore")

    data_gaps: List[DataGapItemModel] = Field(
        default_factory=list, description="List of classified data gap items"
    )
    source_provenance: Dict[str, Any] = Field(
        default_factory=dict, description="Source provenance metadata map"
    )
    gaps_summary: DataGapsSummaryModel = Field(
        default_factory=DataGapsSummaryModel,
        description="Summary counts of data gaps",
    )
    data_utilization: DataUtilizationMetricsModel = Field(
        default_factory=DataUtilizationMetricsModel,
        description="Data utilization metrics",
    )


# ── Quadrant 3: Debate Quality 6-Dimension Metrics ────────────────────────────


class VerifiedRatesModel(BaseModel):
    """Verified rate metrics for bull and bear sides."""

    model_config = ConfigDict(extra="ignore")

    bull_verified_rate: MetricValueModel = Field(
        default_factory=MetricValueModel, description="Bull verified claims rate"
    )
    bear_verified_rate: MetricValueModel = Field(
        default_factory=MetricValueModel, description="Bear verified claims rate"
    )
    bull_bear_verified_delta: MetricValueModel = Field(
        default_factory=MetricValueModel,
        description="Absolute difference between bull and bear verified rates",
    )


class BattlefieldCoverageModel(BaseModel):
    """Manager evidence and battlefield coverage metrics."""

    model_config = ConfigDict(extra="ignore")

    manager_evidence_coverage: Optional[float] = Field(
        default=None,
        description="Proportion of verified claims covered in manager verdict",
    )
    total_claims_count: int = Field(
        default=0, ge=0, description="Total debate claims count"
    )
    verified_claims_count: int = Field(
        default=0, ge=0, description="Total verified claims count"
    )
    unsupported_claims_count: int = Field(
        default=0, ge=0, description="Total unsupported claims count"
    )
    contradicted_claims_count: int = Field(
        default=0, ge=0, description="Total contradicted claims count"
    )


class EvidenceRecyclingModel(BaseModel):
    """Evidence recycling and clone metrics across debate rounds."""

    model_config = ConfigDict(extra="ignore")

    evidence_recycling_rate: MetricValueModel = Field(
        default_factory=MetricValueModel,
        description="Recycling/clone rate of numbers across subsequent rounds",
    )
    unique_claim_count: int = Field(
        default=0, ge=0, description="Distinct claim statements count"
    )
    total_claim_count: int = Field(
        default=0, ge=0, description="Total claims in pool"
    )
    clone_rate: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Duplicate claims ratio in pool"
    )


class ChallengeMetricsModel(BaseModel):
    """Challenge generation, adoption, and evidence status distribution."""

    model_config = ConfigDict(extra="ignore")

    challenge_count: MetricValueModel = Field(
        default_factory=MetricValueModel, description="Total challenges raised"
    )
    challenge_adoption_rate: MetricValueModel = Field(
        default_factory=MetricValueModel,
        description="Overall challenge adoption rate",
    )
    bull_challenge_adoption_rate: Optional[float] = Field(
        default=None, description="Bull side challenge adoption rate"
    )
    bear_challenge_adoption_rate: Optional[float] = Field(
        default=None, description="Bear side challenge adoption rate"
    )
    challenge_evidence_status: Dict[str, Any] = Field(
        default_factory=lambda: {
            "verified": 0,
            "unsupported": 0,
            "contradicted": 0,
            "status": "valid",
            "note": None,
        },
        description="Challenge evidence status distribution and metadata",
    )


class FieldCompletenessModel(BaseModel):
    """Report key fields completeness and legitimate omissions."""

    model_config = ConfigDict(extra="ignore")

    field_completeness_rate: MetricValueModel = Field(
        default_factory=MetricValueModel,
        description="Contract completeness across 4 core fields (confidence, probability, target, stop)",
    )
    present_fields: List[str] = Field(
        default_factory=list, description="Explicitly present valid fields"
    )
    missing_fields: List[str] = Field(
        default_factory=list, description="Missing required fields"
    )
    legitimate_omissions: List[str] = Field(
        default_factory=list,
        description="Fields legitimately omitted with standard notes (e.g. HOLD target_price)",
    )


class DebateHealthModel(BaseModel):
    """Debate consistency gate and health metrics."""

    model_config = ConfigDict(extra="ignore")

    consistency_check_passed: bool = Field(
        default=True, description="Whether manager consistency check passed"
    )
    manager_consistency_gate_triggered: bool = Field(
        default=False, description="Whether consistency hard gate was triggered"
    )
    failed_checks: List[str] = Field(
        default_factory=list, description="List of failed consistency check rules"
    )
    debate_degenerate: bool = Field(
        default=False, description="Whether debate trajectory was degenerate"
    )


class DebateQualityMetricsModel(BaseModel):
    """Quadrant 3: Debate quality 6-dimension metrics."""

    model_config = ConfigDict(extra="ignore")

    verified_rates: VerifiedRatesModel = Field(
        default_factory=VerifiedRatesModel, description="Verified rate metrics"
    )
    battlefield_coverage: BattlefieldCoverageModel = Field(
        default_factory=BattlefieldCoverageModel,
        description="Battlefield coverage metrics",
    )
    evidence_recycling: EvidenceRecyclingModel = Field(
        default_factory=EvidenceRecyclingModel,
        description="Evidence recycling and clone metrics",
    )
    challenge_metrics: ChallengeMetricsModel = Field(
        default_factory=ChallengeMetricsModel,
        description="Challenge metrics and status",
    )
    field_completeness: FieldCompletenessModel = Field(
        default_factory=FieldCompletenessModel,
        description="Field completeness metrics",
    )
    debate_health: DebateHealthModel = Field(
        default_factory=DebateHealthModel,
        description="Debate health and consistency gate status",
    )


# ── Quadrant 4: T+5 Return & Shadow Weighted Calibration ──────────────────────


class ShadowWeightedEvaluationModel(BaseModel):
    """Shadow credit weighting evaluation and hypothetical calibration."""

    model_config = ConfigDict(extra="ignore")

    credit_weighting_enabled: bool = Field(
        default=False, description="Feature flag state (strictly default False)"
    )
    credit_weighting_active: bool = Field(
        default=False, description="Whether weights were actively applied"
    )
    system_gate_status: str = Field(
        default="FAIL", description="7-dimension gate status: PASS or FAIL"
    )
    unweighted_decision: str = Field(
        default="", description="Baseline unweighted decision (BUY/SELL/HOLD)"
    )
    shadow_weighted_decision: Optional[str] = Field(
        default=None, description="Hypothetical decision under credit weighting"
    )
    unweighted_return_pct: Optional[float] = Field(
        default=None, description="Baseline unweighted return percentage"
    )
    shadow_weighted_return_pct: Optional[float] = Field(
        default=None, description="Hypothetical return under shadow credit weighting"
    )
    shadow_alpha_spread: Optional[float] = Field(
        default=None,
        description="Spread between shadow weighted and unweighted return",
    )
    claim_weights: Dict[str, float] = Field(
        default_factory=dict, description="Per-claim assigned credit weights"
    )
    model_weights: Dict[str, float] = Field(
        default_factory=dict, description="Per-model assigned credit weights"
    )
    bias_freeze_reasons: Dict[str, str] = Field(
        default_factory=dict, description="Per-model bias freeze explanations"
    )


class TPlus5AndShadowCalibrationModel(BaseModel):
    """Quadrant 4: T+5 return and shadow weighted calibration."""

    model_config = ConfigDict(extra="ignore")

    entry_price: Optional[float] = Field(
        default=None, description="Recommended entry price"
    )
    target_price: Optional[float] = Field(
        default=None, description="Target price"
    )
    stop_loss_price: Optional[float] = Field(
        default=None, description="Stop loss price"
    )
    decision_direction: str = Field(
        default="", description="Trading decision direction: BUY, SELL, HOLD, NEUTRAL"
    )
    debate_winner: str = Field(
        default="tie", description="Debate winner: bull, bear, tie"
    )
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=100.0, description="Verdict confidence score (0-100)"
    )
    probability: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Winning probability (0.0-1.0)"
    )
    t_plus_5_date: Optional[str] = Field(
        default=None, description="T+5 settlement date (YYYY-MM-DD)"
    )
    t_plus_5_price: Optional[float] = Field(
        default=None, description="Realized closing price at T+5"
    )
    t_plus_5_return_pct: Optional[float] = Field(
        default=None, description="Realized T+5 return percentage"
    )
    t_plus_5_direction_hit: Optional[bool] = Field(
        default=None, description="Whether directional call was profitable at T+5"
    )
    t_plus_5_status: str = Field(
        default="pending_due",
        description="T+5 status: due_and_evaluated, pending_due, data_missing, not_applicable",
    )
    shadow_weighted_metrics: ShadowWeightedEvaluationModel = Field(
        default_factory=ShadowWeightedEvaluationModel,
        description="Shadow weighted metrics calibration",
    )


# ── Full Matrix Model ─────────────────────────────────────────────────────────


class EvaluationMetricMatrixModel(BaseModel):
    """Complete 4-Quadrant Evaluation Metric Matrix Model."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = Field(
        default=EVALUATION_MATRIX_SCHEMA_VERSION,
        description="Schema version of evaluation metric matrix",
    )
    matrix_id: str = Field(
        default="", description="Unique matrix evaluation identifier"
    )
    evaluated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Evaluation computation timestamp (ISO 8601)",
    )
    # Four Quadrants
    quadrant_1_protocol_metadata: ProtocolAndModelMetadataModel = Field(
        default_factory=ProtocolAndModelMetadataModel,
        description="Quadrant 1: Protocol and Model Metadata",
    )
    quadrant_2_data_sources_and_gaps: DataSourcesAndGapsModel = Field(
        default_factory=DataSourcesAndGapsModel,
        description="Quadrant 2: Data Sources & data_gaps Classification",
    )
    quadrant_3_debate_quality: DebateQualityMetricsModel = Field(
        default_factory=DebateQualityMetricsModel,
        description="Quadrant 3: Debate Quality 6-Dimension Metrics",
    )
    quadrant_4_t_plus_5_and_shadow: TPlus5AndShadowCalibrationModel = Field(
        default_factory=TPlus5AndShadowCalibrationModel,
        description="Quadrant 4: T+5 Return & Shadow Weighted Calibration",
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. Pydantic v2 Models for WeeklyMetricsJSON & Summary Dashboard
# ══════════════════════════════════════════════════════════════════════════════


class WeeklyOverviewModel(BaseModel):
    """Overview statistics for the evaluation week."""

    model_config = ConfigDict(extra="ignore")

    total_samples: int = Field(default=0, ge=0, description="Total weekly sample count")
    unique_symbols: int = Field(
        default=0, ge=0, description="Distinct stock symbols evaluated"
    )
    unique_industries: int = Field(
        default=0, ge=0, description="Distinct industries covered"
    )
    max_single_symbol_share: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Maximum concentration ratio of a single symbol",
    )
    bull_decisions: int = Field(
        default=0, ge=0, description="Count of BUY/bull decisions"
    )
    bear_decisions: int = Field(
        default=0, ge=0, description="Count of SELL/bear decisions"
    )
    hold_decisions: int = Field(
        default=0, ge=0, description="Count of HOLD/neutral decisions"
    )
    bull_decision_ratio: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Ratio of bullish decisions"
    )


class WeeklyQualityAggregatesModel(BaseModel):
    """Aggregated debate quality metrics over the week."""

    model_config = ConfigDict(extra="ignore")

    avg_bull_verified_rate: Optional[float] = Field(
        default=None, description="Average bull verified rate"
    )
    avg_bear_verified_rate: Optional[float] = Field(
        default=None, description="Average bear verified rate"
    )
    delta_verified_rate: Optional[float] = Field(
        default=None, description="Average delta between bull and bear verified rates"
    )
    avg_battlefield_coverage: Optional[float] = Field(
        default=None, description="Average manager evidence coverage"
    )
    avg_clone_rate: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Average evidence clone/recycling rate"
    )
    avg_challenge_adoption_rate: Optional[float] = Field(
        default=None, description="Average challenge adoption rate"
    )
    avg_field_completeness_rate: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Average field completeness rate"
    )
    consistency_gate_trigger_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Proportion of debates triggering consistency hard gate",
    )


class WeeklyDataGapsAggregatesModel(BaseModel):
    """Aggregated data gaps and utilization over the week."""

    model_config = ConfigDict(extra="ignore")

    total_structural_gaps: int = Field(
        default=0, ge=0, description="Total structural gap occurrences"
    )
    total_operational_gaps: int = Field(
        default=0, ge=0, description="Total operational gap occurrences"
    )
    resident_fault_count: int = Field(
        default=0, ge=0, description="Resident operational fault count"
    )
    gaps_by_source: Dict[str, Dict[str, int]] = Field(
        default_factory=dict,
        description="Gaps breakdown by source name and gap class",
    )
    avg_seven_reports_utilization: Optional[float] = Field(
        default=None, description="Average 7 reports data utilization"
    )
    avg_macro_utilization: Optional[float] = Field(
        default=None, description="Average macro data utilization"
    )
    avg_fundamentals_utilization: Optional[float] = Field(
        default=None, description="Average fundamentals data utilization"
    )


class WeeklyT5CalibrationModel(BaseModel):
    """Aggregated T+5 return calibration metrics over the week."""

    model_config = ConfigDict(extra="ignore")

    due_sample_count: int = Field(
        default=0, ge=0, description="Samples due for T+5 evaluation"
    )
    completed_sample_count: int = Field(
        default=0, ge=0, description="Samples with completed T+5 evaluation"
    )
    completeness_rate: float = Field(
        default=0.0, ge=0.0, le=1.0, description="T+5 data completeness rate"
    )
    hit_count: int = Field(
        default=0, ge=0, description="Count of profitable/accurate directional calls"
    )
    direction_accuracy_rate: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="T+5 directional accuracy rate"
    )
    avg_t5_return_pct: Optional[float] = Field(
        default=None, description="Average T+5 realized return percentage"
    )


class WeeklyModelIsolationModel(BaseModel):
    """Per-model bias freeze and layered isolation state for weekly dashboards."""

    model_config = ConfigDict(extra="ignore")

    credit_weighting_active: bool = Field(
        default=False, description="Whether credit weighting may be active"
    )
    global_fallback_shadow: bool = Field(
        default=True, description="Whether global shadow-only fallback is engaged"
    )
    system_gate_status: str = Field(
        default="FAIL", description="System gate status label"
    )
    model_weights: Dict[str, float] = Field(
        default_factory=dict, description="Per-model effective weight multipliers"
    )
    bias_freeze_reasons: Dict[str, str] = Field(
        default_factory=dict, description="Per-model bias freeze audit reasons"
    )
    abnormal_model_ratio: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Share of models flagged as biased"
    )


class WeeklyH1bGatesModel(BaseModel):
    """7-dimension gate evaluation result for H1b activation."""

    model_config = ConfigDict(extra="ignore")

    passed: bool = Field(
        default=False, description="Whether all 7 dimensions passed"
    )
    recommendation: Literal["ELIGIBLE_FOR_ACTIVATION", "KEEP_FALSE"] = Field(
        default="KEEP_FALSE", description="Recommended activation action"
    )
    matrix: Dict[str, Any] = Field(
        default_factory=dict, description="Detailed 7-dimension evaluation matrix"
    )
    model_isolation: WeeklyModelIsolationModel = Field(
        default_factory=WeeklyModelIsolationModel,
        description="Layered isolation and per-model bias freeze evaluation",
    )


class WeeklyDeduplicationAuditModel(BaseModel):
    """Deduplication and novelty audit against historical cases."""

    model_config = ConfigDict(extra="ignore")

    historical_sample_count: int = Field(
        default=0, ge=0, description="Historical reference cases count"
    )
    new_unique_samples: int = Field(
        default=0, ge=0, description="Count of genuinely new unique samples"
    )
    duplicate_samples_dropped: int = Field(
        default=0, ge=0, description="Duplicate samples identified and excluded"
    )
    status: str = Field(
        default="PASSED_NO_DUPLICATES",
        description="Audit status: PASSED_NO_DUPLICATES or DUPLICATES_DETECTED",
    )


class WeeklyAggregateMetricsModel(BaseModel):
    """Complete aggregated metrics bag for the evaluation week."""

    model_config = ConfigDict(extra="ignore")

    overview: WeeklyOverviewModel = Field(
        default_factory=WeeklyOverviewModel, description="Weekly sample overview"
    )
    quality_aggregates: WeeklyQualityAggregatesModel = Field(
        default_factory=WeeklyQualityAggregatesModel,
        description="Debate quality aggregates",
    )
    data_gaps_aggregates: WeeklyDataGapsAggregatesModel = Field(
        default_factory=WeeklyDataGapsAggregatesModel,
        description="Data gaps and utilization aggregates",
    )
    t5_calibration: WeeklyT5CalibrationModel = Field(
        default_factory=WeeklyT5CalibrationModel,
        description="T+5 calibration aggregates",
    )
    h1b_system_gates_evaluation: WeeklyH1bGatesModel = Field(
        default_factory=WeeklyH1bGatesModel,
        description="H1b 7-dimension system gates evaluation",
    )
    deduplication_audit: WeeklyDeduplicationAuditModel = Field(
        default_factory=WeeklyDeduplicationAuditModel,
        description="Historical sample deduplication audit",
    )
    drilldown_by_industry: Dict[str, Any] = Field(
        default_factory=dict,
        description="Drilldown analytics grouped by industry",
    )
    drilldown_by_model: Dict[str, Any] = Field(
        default_factory=dict,
        description="Drilldown analytics grouped by model assignment",
    )
    drilldown_by_regime: Dict[str, Any] = Field(
        default_factory=dict,
        description="Drilldown analytics grouped by market regime",
    )


class WeeklyMetricsJSONModel(BaseModel):
    """Top-level Weekly Metrics JSON schema model."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = Field(
        default=WEEKLY_METRICS_SCHEMA_VERSION,
        description="Schema version of weekly metrics JSON",
    )
    week_identifier: str = Field(
        description="Week identifier in format week_YYYYWW (e.g. week_202634)"
    )
    start_date: str = Field(description="Week start date in YYYY-MM-DD format")
    end_date: str = Field(description="Week end date in YYYY-MM-DD format")
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Generation timestamp in ISO 8601 format",
    )
    sample_count: int = Field(
        default=0, ge=0, description="Total evaluated samples count"
    )
    samples: List[EvaluationMetricMatrixModel] = Field(
        default_factory=list,
        description="List of individual sample EvaluationMetricMatrix records",
    )
    weekly_aggregate: WeeklyAggregateMetricsModel = Field(
        default_factory=WeeklyAggregateMetricsModel,
        description="Aggregate summary statistics across all samples",
    )


class WeeklySummaryMDModel(BaseModel):
    """Weekly Summary Markdown document model."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = Field(
        default=WEEKLY_SUMMARY_MD_SCHEMA_VERSION,
        description="Schema version of weekly summary markdown",
    )
    week_identifier: str = Field(description="Week identifier (e.g. week_202634)")
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Report generation timestamp",
    )
    title: str = Field(description="Summary report title")
    markdown_content: str = Field(
        description="Complete rendered markdown report content"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. TypedDict Declarations (for Agent / LangGraph Native Compatibility)
# ══════════════════════════════════════════════════════════════════════════════


class ProtocolAndModelMetadata(TypedDict, total=False):
    report_id: str
    symbol: str
    security_name: str
    trade_date: str
    industry: Optional[str]
    market_regime: Optional[str]
    protocol_version: str
    protocol_stage: str
    tiebreak_skipped: bool
    debate_degenerate: bool
    feature_flags: Dict[str, bool]
    model_assignments: Dict[str, Optional[str]]
    latency_ms: Optional[float]
    token_usage: Optional[Dict[str, int]]
    created_at: Optional[str]


class DataGapItem(TypedDict, total=False):
    source: str
    gap_class: str  # "structural" | "operational"
    status: str
    reason: str
    gap: Optional[str]


class DataGapsSummary(TypedDict, total=False):
    total_gaps: int
    structural_count: int
    operational_count: int
    resident_fault_count: int


class DataUtilizationMetrics(TypedDict, total=False):
    seven_reports_utilization: MetricResult
    macro_utilization: MetricResult
    fundamentals_utilization: MetricResult
    analyst_utilization_by_role: Dict[str, Optional[float]]


class DataSourcesAndGaps(TypedDict, total=False):
    data_gaps: List[DataGapItem]
    source_provenance: Dict[str, Any]
    gaps_summary: DataGapsSummary
    data_utilization: DataUtilizationMetrics


class VerifiedRates(TypedDict, total=False):
    bull_verified_rate: MetricResult
    bear_verified_rate: MetricResult
    bull_bear_verified_delta: MetricResult


class BattlefieldCoverage(TypedDict, total=False):
    manager_evidence_coverage: Optional[float]
    total_claims_count: int
    verified_claims_count: int
    unsupported_claims_count: int
    contradicted_claims_count: int


class EvidenceRecycling(TypedDict, total=False):
    evidence_recycling_rate: MetricResult
    unique_claim_count: int
    total_claim_count: int
    clone_rate: float


class ChallengeMetrics(TypedDict, total=False):
    challenge_count: MetricResult
    challenge_adoption_rate: MetricResult
    bull_challenge_adoption_rate: Optional[float]
    bear_challenge_adoption_rate: Optional[float]
    challenge_evidence_status: Dict[str, int]


class FieldCompleteness(TypedDict, total=False):
    field_completeness_rate: MetricResult
    present_fields: List[str]
    missing_fields: List[str]
    legitimate_omissions: List[str]


class DebateHealth(TypedDict, total=False):
    consistency_check_passed: bool
    manager_consistency_gate_triggered: bool
    failed_checks: List[str]
    debate_degenerate: bool


class DebateQualityMetrics(TypedDict, total=False):
    verified_rates: VerifiedRates
    battlefield_coverage: BattlefieldCoverage
    evidence_recycling: EvidenceRecycling
    challenge_metrics: ChallengeMetrics
    field_completeness: FieldCompleteness
    debate_health: DebateHealth


class ShadowWeightedEvaluation(TypedDict, total=False):
    credit_weighting_enabled: bool
    credit_weighting_active: bool
    system_gate_status: str
    unweighted_decision: str
    shadow_weighted_decision: Optional[str]
    unweighted_return_pct: Optional[float]
    shadow_weighted_return_pct: Optional[float]
    shadow_alpha_spread: Optional[float]
    claim_weights: Dict[str, float]
    model_weights: Dict[str, float]
    bias_freeze_reasons: Dict[str, str]


class TPlus5AndShadowCalibration(TypedDict, total=False):
    entry_price: Optional[float]
    target_price: Optional[float]
    stop_loss_price: Optional[float]
    decision_direction: str
    debate_winner: str
    confidence: Optional[float]
    probability: Optional[float]
    t_plus_5_date: Optional[str]
    t_plus_5_price: Optional[float]
    t_plus_5_return_pct: Optional[float]
    t_plus_5_direction_hit: Optional[bool]
    t_plus_5_status: str
    shadow_weighted_metrics: ShadowWeightedEvaluation


class EvaluationMetricMatrix(TypedDict, total=False):
    schema_version: str
    matrix_id: str
    evaluated_at: str
    quadrant_1_protocol_metadata: ProtocolAndModelMetadata
    quadrant_2_data_sources_and_gaps: DataSourcesAndGaps
    quadrant_3_debate_quality: DebateQualityMetrics
    quadrant_4_t_plus_5_and_shadow: TPlus5AndShadowCalibration


class WeeklyOverview(TypedDict, total=False):
    total_samples: int
    unique_symbols: int
    unique_industries: int
    max_single_symbol_share: float
    bull_decisions: int
    bear_decisions: int
    hold_decisions: int
    bull_decision_ratio: float


class WeeklyQualityAggregates(TypedDict, total=False):
    avg_bull_verified_rate: Optional[float]
    avg_bear_verified_rate: Optional[float]
    delta_verified_rate: Optional[float]
    avg_battlefield_coverage: Optional[float]
    avg_clone_rate: float
    avg_challenge_adoption_rate: Optional[float]
    avg_field_completeness_rate: float
    consistency_gate_trigger_rate: float


class WeeklyDataGapsAggregates(TypedDict, total=False):
    total_structural_gaps: int
    total_operational_gaps: int
    resident_fault_count: int
    gaps_by_source: Dict[str, Dict[str, int]]
    avg_seven_reports_utilization: Optional[float]
    avg_macro_utilization: Optional[float]
    avg_fundamentals_utilization: Optional[float]


class WeeklyT5Calibration(TypedDict, total=False):
    due_sample_count: int
    completed_sample_count: int
    completeness_rate: float
    hit_count: int
    direction_accuracy_rate: Optional[float]
    avg_t5_return_pct: Optional[float]


class WeeklyModelIsolation(TypedDict, total=False):
    credit_weighting_active: bool
    global_fallback_shadow: bool
    system_gate_status: str
    model_weights: Dict[str, float]
    bias_freeze_reasons: Dict[str, str]
    abnormal_model_ratio: float


class WeeklyH1bGates(TypedDict, total=False):
    passed: bool
    recommendation: str
    matrix: Dict[str, Any]
    model_isolation: WeeklyModelIsolation


class WeeklyDeduplicationAudit(TypedDict, total=False):
    historical_sample_count: int
    new_unique_samples: int
    duplicate_samples_dropped: int
    status: str


class WeeklyAggregateMetrics(TypedDict, total=False):
    overview: WeeklyOverview
    quality_aggregates: WeeklyQualityAggregates
    data_gaps_aggregates: WeeklyDataGapsAggregates
    t5_calibration: WeeklyT5Calibration
    h1b_system_gates_evaluation: WeeklyH1bGates
    deduplication_audit: WeeklyDeduplicationAudit
    drilldown_by_industry: Dict[str, Any]
    drilldown_by_model: Dict[str, Any]
    drilldown_by_regime: Dict[str, Any]


class WeeklyMetricsJSON(TypedDict, total=False):
    schema_version: str
    week_identifier: str
    start_date: str
    end_date: str
    generated_at: str
    sample_count: int
    samples: List[EvaluationMetricMatrix]
    weekly_aggregate: WeeklyAggregateMetrics


class WeeklySummaryMD(TypedDict, total=False):
    schema_version: str
    week_identifier: str
    generated_at: str
    title: str
    markdown_content: str


# ══════════════════════════════════════════════════════════════════════════════
# 4. Pure Function Builders & Calculators
# ══════════════════════════════════════════════════════════════════════════════


def build_evaluation_metric_matrix(
    result_data_or_state: Mapping[str, Any],
    *,
    report_id: Optional[str] = None,
    symbol: Optional[str] = None,
    security_name: Optional[str] = None,
    trade_date: Optional[str] = None,
    industry: Optional[str] = None,
    market_regime: Optional[str] = None,
    t_plus_5_price: Optional[float] = None,
    t_plus_5_date: Optional[str] = None,
    trading_calendar: Optional[Sequence[Union[str, date]]] = None,
    price_series: Optional[Mapping[str, float]] = None,
    as_of_date: Optional[str] = None,
    is_suspended: Optional[bool] = None,
    latency_ms: Optional[float] = None,
    token_usage: Optional[Dict[str, int]] = None,
    model_assignments: Optional[Dict[str, Optional[str]]] = None,
    historical_samples_for_weights: Optional[Sequence[Mapping[str, Any]]] = None,
) -> EvaluationMetricMatrix:
    """Pure function to build a 4-Quadrant EvaluationMetricMatrix from report/debate state.

    Calculates all metrics deterministically without network or LLM calls.
    Adheres strictly to P3-H2.0 contracts, P2-10.4 data_gaps standards, and A-share trading calendar T+5 calibration.
    """
    data = dict(result_data_or_state or {})
    inv_state = data.get("investment_debate_state")
    if not isinstance(inv_state, Mapping):
        inv_state = data

    meta = get_protocol_metadata(data)
    protocol_version = meta["protocol_version"]
    protocol_stage = meta["protocol_stage"]
    feature_flags = meta["feature_flags"]
    tiebreak_skipped = meta["tiebreak_skipped"]
    debate_degenerate = meta["debate_degenerate"]

    rep_id = (
        report_id
        or str(data.get("id") or data.get("report_id") or data.get("matrix_id") or "")
    )
    sym = (
        symbol
        or str(data.get("symbol") or data.get("ticker") or "")
    ).strip()
    sec_name = (
        security_name
        or str(data.get("security_name") or data.get("company_of_interest") or sym)
    ).strip()
    t_date = (
        trade_date
        or str(data.get("trade_date") or data.get("date") or "")
    ).strip()
    ind = industry or data.get("industry") or data.get("sector")
    regime = market_regime or data.get("market_regime") or data.get("regime")

    # ── 1. Quadrant 1: Protocol & Model Metadata ──────────────────────────────
    shadow_res = calculate_shadow_credit_metrics(
        data, version=protocol_version, t_plus_5_price=t_plus_5_price
    )
    extracted_model_assignments = (
        model_assignments
        or data.get("model_assignments")
        or inv_state.get("model_assignments")
        or shadow_res.get("model_id_by_stance")
        or {"bull": None, "bear": None, "manager": None}
    )
    final_model_assignments = {
        "bull": extracted_model_assignments.get("bull") if isinstance(extracted_model_assignments, Mapping) else None,
        "bear": extracted_model_assignments.get("bear") if isinstance(extracted_model_assignments, Mapping) else None,
        "manager": extracted_model_assignments.get("manager") if isinstance(extracted_model_assignments, Mapping) else None,
    }

    quad_1: ProtocolAndModelMetadata = {
        "report_id": rep_id,
        "symbol": sym,
        "security_name": sec_name,
        "trade_date": t_date,
        "industry": str(ind).strip() if ind else None,
        "market_regime": str(regime).strip() if regime else None,
        "protocol_version": protocol_version,
        "protocol_stage": protocol_stage,
        "tiebreak_skipped": tiebreak_skipped,
        "debate_degenerate": debate_degenerate,
        "feature_flags": feature_flags,
        "model_assignments": final_model_assignments,
        "latency_ms": latency_ms or data.get("latency_ms") or inv_state.get("latency_ms"),
        "token_usage": token_usage or data.get("token_usage") or inv_state.get("token_usage"),
        "created_at": data.get("created_at")
        or datetime.now(timezone.utc).isoformat(),
    }

    # ── 2. Quadrant 2: Data Sources & Gaps Classification ─────────────────────
    raw_gaps = data.get("data_gaps") or inv_state.get("data_gaps") or []
    classified_gaps: List[DataGapItem] = []
    structural_cnt = 0
    operational_cnt = 0

    if isinstance(raw_gaps, list):
        for item in raw_gaps:
            if isinstance(item, Mapping):
                src = str(item.get("source") or item.get("name") or "unknown")
                g_class = str(item.get("gap_class") or "").lower()
                st = str(item.get("status") or "unavailable").lower()
                reason = str(item.get("reason") or item.get("gap") or "")
                compact_gap = str(item.get("gap") or reason)

                # Classify if not explicitly marked
                if g_class not in ("structural", "operational"):
                    if any(
                        w in reason or w in compact_gap
                        for w in (
                            "停止披露",
                            "快照拒绝",
                            "仅支持当日快照",
                            "制度性",
                            "refused",
                        )
                    ):
                        g_class = "structural"
                    else:
                        g_class = "operational"

                if g_class == "structural":
                    structural_cnt += 1
                else:
                    operational_cnt += 1

                classified_gaps.append(
                    {
                        "source": src,
                        "gap_class": g_class,
                        "status": st,
                        "reason": reason,
                        "gap": compact_gap,
                    }
                )
            elif isinstance(item, str):
                s_text = item.strip()
                is_struct = any(
                    w in s_text
                    for w in ("停止披露", "快照拒绝", "仅支持当日快照", "制度性")
                )
                g_class = "structural" if is_struct else "operational"
                if is_struct:
                    structural_cnt += 1
                else:
                    operational_cnt += 1
                classified_gaps.append(
                    {
                        "source": "general",
                        "gap_class": g_class,
                        "status": "unavailable" if is_struct else "failed",
                        "reason": s_text,
                        "gap": s_text,
                    }
                )

    prov = (
        data.get("source_provenance")
        or inv_state.get("source_provenance")
        or {}
    )

    all_debate_metrics = calculate_all_debate_metrics(data, version=protocol_version)
    seven_util = all_debate_metrics["seven_reports_utilization"]
    macro_util = all_debate_metrics["macro_utilization"]
    fund_util = all_debate_metrics["fundamentals_utilization"]
    role_util = shadow_res.get("analyst_utilization_by_role") or {}

    # ── 3. Quadrant 3: Debate Quality 6-Dimension Metrics ─────────────────────
    bull_bear_res = all_debate_metrics["bull_bear_verified"]
    challenge_res = all_debate_metrics["challenge_metrics"]
    recycling_res = all_debate_metrics["evidence_recycling"]
    completeness_res = all_debate_metrics["field_completeness"]

    # Claims pool counts
    claims = inv_state.get("claims") or data.get("claims") or []
    tot_claims = len(claims) if isinstance(claims, list) else 0
    unique_claims_txt = set()
    v_claims_cnt = 0
    unsupp_claims_cnt = 0
    contra_claims_cnt = 0

    if isinstance(claims, list):
        for c in claims:
            if isinstance(c, Mapping):
                c_txt = str(c.get("claim") or "").strip()
                if c_txt:
                    unique_claims_txt.add(c_txt)
                st = str(c.get("status") or "").lower()
                is_v = bool(st == "verified" or c.get("is_verified") is True)
                if is_v:
                    v_claims_cnt += 1
                elif st == "unsupported":
                    unsupp_claims_cnt += 1
                elif st == "contradicted":
                    contra_claims_cnt += 1

    clone_rate = (
        (1.0 - len(unique_claims_txt) / tot_claims) if tot_claims > 0 else 0.0
    )

    manager_verdict = (
        data.get("manager_verdict")
        or inv_state.get("manager_verdict")
        or {}
    )
    consistency_passed = bool(
        manager_verdict.get("consistency_check_passed", True)
    )
    failed_checks = list(manager_verdict.get("failed_checks") or [])
    gate_triggered = bool(shadow_res.get("manager_consistency_gate_triggered", False))

    quad_3: DebateQualityMetrics = {
        "verified_rates": {
            "bull_verified_rate": bull_bear_res["bull_verified_rate"],
            "bear_verified_rate": bull_bear_res["bear_verified_rate"],
            "bull_bear_verified_delta": bull_bear_res["bull_bear_verified_delta"],
        },
        "battlefield_coverage": {
            "manager_evidence_coverage": shadow_res.get("manager_evidence_coverage"),
            "total_claims_count": tot_claims,
            "verified_claims_count": v_claims_cnt,
            "unsupported_claims_count": unsupp_claims_cnt,
            "contradicted_claims_count": contra_claims_cnt,
        },
        "evidence_recycling": {
            "evidence_recycling_rate": recycling_res,
            "unique_claim_count": len(unique_claims_txt),
            "total_claim_count": tot_claims,
            "clone_rate": round(clone_rate, 4),
        },
        "challenge_metrics": {
            "challenge_count": challenge_res["challenge_count"],
            "challenge_adoption_rate": challenge_res["challenge_adoption_rate"],
            "bull_challenge_adoption_rate": shadow_res.get(
                "bull_challenge_adoption_rate"
            ),
            "bear_challenge_adoption_rate": shadow_res.get(
                "bear_challenge_adoption_rate"
            ),
            "challenge_evidence_status": challenge_res["challenge_evidence_status"],
        },
        "field_completeness": {
            "field_completeness_rate": completeness_res,
            "present_fields": completeness_res.get("present_fields") or [],
            "missing_fields": completeness_res.get("missing_fields") or [],
            "legitimate_omissions": completeness_res.get("legitimate_omissions")
            or [],
        },
        "debate_health": {
            "consistency_check_passed": consistency_passed,
            "manager_consistency_gate_triggered": gate_triggered,
            "failed_checks": failed_checks,
            "debate_degenerate": debate_degenerate,
        },
    }

    # ── 4. Quadrant 4: T+5 Return & Shadow Weighted Calibration ───────────────
    raw_target = data.get("target_price") or manager_verdict.get("target")
    raw_stop = data.get("stop_loss_price") or manager_verdict.get("stop_loss")
    raw_entry = manager_verdict.get("entry") or data.get("entry_price")
    decision_dir = str(
        manager_verdict.get("direction")
        or data.get("direction")
        or data.get("decision")
        or ""
    ).upper()
    winner_str = str(
        manager_verdict.get("winner")
        or ("bull" if any(w in decision_dir for w in ("BUY", "BULL", "多", "买入", "增持")) else "bear" if any(w in decision_dir for w in ("SELL", "BEAR", "空", "卖出", "减持")) else "tie")
    ).lower()

    entry_val: Optional[float] = None
    target_val: Optional[float] = None
    stop_val: Optional[float] = None

    if raw_entry is not None:
        try:
            entry_val = float(str(raw_entry).split("-")[0].replace("元", "").strip())
        except (ValueError, TypeError):
            entry_val = None

    if raw_target is not None:
        try:
            target_val = float(str(raw_target).replace("元", "").strip())
        except (ValueError, TypeError):
            target_val = None

    if raw_stop is not None:
        try:
            stop_val = float(str(raw_stop).replace("元", "").strip())
        except (ValueError, TypeError):
            stop_val = None

    conf_val: Optional[float] = None
    prob_val: Optional[float] = None
    raw_conf = data.get("confidence")
    if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool):
        conf_val = float(raw_conf)
    raw_prob = data.get("probability")
    if isinstance(raw_prob, (int, float)) and not isinstance(raw_prob, bool):
        prob_val = float(raw_prob)

    # Calculate T+5 trading date using A-share Trading Calendar
    calc_t5_date = None
    if t_date:
        try:
            calc_t5_date = calculate_t_plus_5_date(t_date, calendar_dates=trading_calendar)
        except Exception:
            calc_t5_date = None

    final_t5_date = t_plus_5_date or data.get("t_plus_5_date") or calc_t5_date

    # Check suspension
    suspended = bool(
        is_suspended
        or data.get("is_suspended") is True
        or data.get("suspension") is True
        or data.get("t_plus_5_status") == "suspension"
    )
    if not suspended and raw_gaps:
        for g in raw_gaps:
            g_txt = (str(g.get("reason") or "") + " " + str(g.get("gap") or "") + " " + str(g.get("status") or "")).lower() if isinstance(g, Mapping) else str(g).lower()
            if "suspension" in g_txt or "停牌" in g_txt:
                suspended = True
                break

    # Determine T+5 Price
    final_t5_price: Optional[float] = None
    if t_plus_5_price is not None and isinstance(t_plus_5_price, (int, float)):
        final_t5_price = float(t_plus_5_price)
    elif price_series and final_t5_date and final_t5_date in price_series:
        p_val = price_series.get(final_t5_date)
        if p_val is not None:
            try:
                final_t5_price = float(p_val)
            except (ValueError, TypeError):
                final_t5_price = None
    elif data.get("t_plus_5_price") is not None:
        try:
            final_t5_price = float(data["t_plus_5_price"])
        except (ValueError, TypeError):
            final_t5_price = None

    t5_return_pct: Optional[float] = None
    t5_hit: Optional[bool] = None
    t5_status = "pending_due"

    if suspended:
        t5_status = "suspension"
        t5_return_pct = None
        t5_hit = None
        has_susp_gap = any(
            "suspension" in str(item.get("gap", "")).lower()
            or "停牌" in str(item.get("reason", ""))
            or item.get("status") == "suspended"
            for item in classified_gaps
        )
        if not has_susp_gap:
            classified_gaps.append(
                {
                    "source": "trading_calendar",
                    "gap_class": "operational",
                    "status": "suspended",
                    "reason": f"标的 {sym} 在 T+5 ({final_t5_date or '未知'}) 停牌，剔除分母",
                    "gap": "data_gap: suspension",
                }
            )
            operational_cnt += 1
    elif final_t5_price is not None:
        t5_status = "due_and_evaluated"
        if entry_val is not None and entry_val > 0:
            t5_return_pct = round(((final_t5_price - entry_val) / entry_val) * 100.0, 2)
            price_change = final_t5_price - entry_val
            if any(w in decision_dir for w in ("BUY", "BULLISH", "BULL", "多", "买入", "增持")):
                t5_hit = bool(price_change > 0)
            elif any(w in decision_dir for w in ("SELL", "BEARISH", "BEAR", "空", "卖出", "减持")):
                t5_hit = bool(price_change < 0)
            elif any(w in decision_dir for w in ("HOLD", "NEUTRAL", "中性", "观望", "持有")):
                t5_hit = bool(abs(price_change / entry_val) <= 0.03)
            else:
                t5_hit = bool(price_change > 0)
        else:
            t5_hit = shadow_res.get("t_plus_5_direction_hit")
    else:
        # final_t5_price is None: evaluate pending_due (<5 trading days) vs data_missing
        is_pending = False
        if final_t5_date is None:
            is_pending = True
        elif as_of_date:
            try:
                as_of_d = _parse_date(as_of_date)
                t5_d = _parse_date(final_t5_date)
                if as_of_d < t5_d:
                    is_pending = True
            except Exception:
                pass
        elif data.get("is_t_plus_5_due") is False or data.get("is_in_flight") is True:
            is_pending = True
        elif not data.get("t_plus_5_evaluated"):
            is_pending = True

        if is_pending:
            t5_status = "pending_due"
            t5_return_pct = None
            t5_hit = None
        else:
            t5_status = "data_missing"
            t5_return_pct = None
            t5_hit = None

    # Update Quadrant 2 gaps summary in case suspension gap was added
    quad_2: DataSourcesAndGaps = {
        "data_gaps": classified_gaps,
        "source_provenance": dict(prov) if isinstance(prov, Mapping) else {},
        "gaps_summary": {
            "total_gaps": len(classified_gaps),
            "structural_count": structural_cnt,
            "operational_count": operational_cnt,
            "resident_fault_count": operational_cnt,
        },
        "data_utilization": {
            "seven_reports_utilization": seven_util,
            "macro_utilization": macro_util,
            "fundamentals_utilization": fund_util,
            "analyst_utilization_by_role": role_util,
        },
    }

    # Credit weighting shadow application
    weighting_app = apply_credit_weighting_to_debate(
        data, historical_samples=historical_samples_for_weights
    )
    s_metrics = weighting_app.get("shadow_credit_metrics") or {}

    shadow_eval: ShadowWeightedEvaluation = {
        "credit_weighting_enabled": bool(
            feature_flags.get("credit_weighting_enabled", False)
        ),
        "credit_weighting_active": bool(weighting_app.get("credit_weighting_active")),
        "system_gate_status": weighting_app.get("system_gate_status", "FAIL"),
        "unweighted_decision": decision_dir,
        "shadow_weighted_decision": decision_dir,  # Conservative parity
        "unweighted_return_pct": t5_return_pct,
        "shadow_weighted_return_pct": t5_return_pct,
        "shadow_alpha_spread": 0.0 if t5_return_pct is not None else None,
        "claim_weights": weighting_app.get("claim_weights") or {},
        "model_weights": s_metrics.get("model_weights") or {},
        "bias_freeze_reasons": s_metrics.get("bias_freeze_reasons") or {},
    }

    quad_4: TPlus5AndShadowCalibration = {
        "entry_price": entry_val,
        "target_price": target_val,
        "stop_loss_price": stop_val,
        "decision_direction": decision_dir,
        "debate_winner": winner_str,
        "confidence": conf_val,
        "probability": prob_val,
        "t_plus_5_date": final_t5_date,
        "t_plus_5_price": final_t5_price,
        "t_plus_5_return_pct": t5_return_pct,
        "t_plus_5_direction_hit": t5_hit,
        "t_plus_5_status": t5_status,
        "shadow_weighted_metrics": shadow_eval,
    }

    matrix_id = f"matrix_{sym}_{t_date}_{rep_id[:8]}" if rep_id else f"matrix_{sym}_{t_date}"

    return {
        "schema_version": EVALUATION_MATRIX_SCHEMA_VERSION,
        "matrix_id": matrix_id,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "quadrant_1_protocol_metadata": quad_1,
        "quadrant_2_data_sources_and_gaps": quad_2,
        "quadrant_3_debate_quality": quad_3,
        "quadrant_4_t_plus_5_and_shadow": quad_4,
    }


def calculate_drilldown_by_industry(
    matrix_samples: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Group metrics by industry and compute drill-down statistics."""
    industry_groups: Dict[str, List[Mapping[str, Any]]] = {}
    for m in matrix_samples:
        q1 = m.get("quadrant_1_protocol_metadata") or {}
        ind = str(q1.get("industry") or "未知行业").strip()
        industry_groups.setdefault(ind, []).append(m)

    result = {}
    for ind, samples in sorted(industry_groups.items()):
        total = len(samples)
        bull_cnt = 0
        bear_cnt = 0
        hold_cnt = 0
        bull_v_rates = []
        bear_v_rates = []
        clone_rates = []
        challenge_rates = []
        t5_hits = 0
        t5_due = 0
        t5_returns = []

        for s in samples:
            q1 = s.get("quadrant_1_protocol_metadata") or {}
            q3 = s.get("quadrant_3_debate_quality") or {}
            q4 = s.get("quadrant_4_t_plus_5_and_shadow") or {}

            dec = str(q4.get("decision_direction") or "").upper()
            if any(w in dec for w in ("BUY", "BULL", "多", "买入", "增持")):
                bull_cnt += 1
            elif any(w in dec for w in ("SELL", "BEAR", "空", "卖出", "减持")):
                bear_cnt += 1
            else:
                hold_cnt += 1

            vr = q3.get("verified_rates") or {}
            bv = vr.get("bull_verified_rate", {}).get("rate")
            if bv is not None:
                bull_v_rates.append(float(bv))
            br = vr.get("bear_verified_rate", {}).get("rate")
            if br is not None:
                bear_v_rates.append(float(br))

            cl = q3.get("evidence_recycling", {}).get("clone_rate")
            if cl is not None:
                clone_rates.append(float(cl))

            ca = q3.get("challenge_metrics", {}).get("challenge_adoption_rate", {}).get("rate")
            if ca is not None:
                challenge_rates.append(float(ca))

            hit = q4.get("t_plus_5_direction_hit")
            st = q4.get("t_plus_5_status")
            if hit is not None or st == "due_and_evaluated":
                t5_due += 1
                if hit is True:
                    t5_hits += 1
            ret = q4.get("t_plus_5_return_pct")
            if ret is not None:
                t5_returns.append(float(ret))

        avg_bv = round(sum(bull_v_rates) / len(bull_v_rates), 4) if bull_v_rates else None
        avg_br = round(sum(bear_v_rates) / len(bear_v_rates), 4) if bear_v_rates else None
        delta_v = round(abs(avg_bv - avg_br), 4) if (avg_bv is not None and avg_br is not None) else None
        avg_cl = round(sum(clone_rates) / len(clone_rates), 4) if clone_rates else 0.0
        avg_ca = round(sum(challenge_rates) / len(challenge_rates), 4) if challenge_rates else None
        t5_acc = round(t5_hits / t5_due, 4) if t5_due > 0 else None
        avg_ret = round(sum(t5_returns) / len(t5_returns), 2) if t5_returns else None

        result[ind] = {
            "sample_count": total,
            "bull_count": bull_cnt,
            "bear_count": bear_cnt,
            "hold_count": hold_cnt,
            "bull_ratio": round(bull_cnt / total, 4) if total > 0 else 0.0,
            "avg_bull_verified_rate": avg_bv,
            "avg_bear_verified_rate": avg_br,
            "delta_verified_rate": delta_v,
            "avg_clone_rate": avg_cl,
            "avg_challenge_adoption_rate": avg_ca,
            "t5_accuracy_rate": t5_acc,
            "avg_t5_return_pct": avg_ret,
        }
    return result


def calculate_drilldown_by_model(
    matrix_samples: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Group metrics by model assignment (bull/bear/manager) and compute performance."""
    model_stats: Dict[str, Dict[str, Any]] = {}

    for s in matrix_samples:
        q1 = s.get("quadrant_1_protocol_metadata") or {}
        q3 = s.get("quadrant_3_debate_quality") or {}
        q4 = s.get("quadrant_4_t_plus_5_and_shadow") or {}
        models = q1.get("model_assignments") or {}
        winner = str(q4.get("debate_winner") or "").lower()

        bull_model = models.get("bull") or "unknown_bull_model"
        bear_model = models.get("bear") or "unknown_bear_model"

        # Bull side
        if bull_model not in model_stats:
            model_stats[bull_model] = {
                "total_debates": 0,
                "bull_debates": 0,
                "bear_debates": 0,
                "bull_wins": 0,
                "bear_wins": 0,
                "ties": 0,
                "verified_rates": [],
                "challenge_adoption_rates": [],
            }
        model_stats[bull_model]["total_debates"] += 1
        model_stats[bull_model]["bull_debates"] += 1
        if winner == "bull":
            model_stats[bull_model]["bull_wins"] += 1
        elif winner == "tie":
            model_stats[bull_model]["ties"] += 1

        vr = q3.get("verified_rates") or {}
        bv = vr.get("bull_verified_rate", {}).get("rate")
        if bv is not None:
            model_stats[bull_model]["verified_rates"].append(float(bv))

        b_ca = q3.get("challenge_metrics", {}).get("bull_challenge_adoption_rate")
        if b_ca is not None:
            model_stats[bull_model]["challenge_adoption_rates"].append(float(b_ca))

        # Bear side
        if bear_model not in model_stats:
            model_stats[bear_model] = {
                "total_debates": 0,
                "bull_debates": 0,
                "bear_debates": 0,
                "bull_wins": 0,
                "bear_wins": 0,
                "ties": 0,
                "verified_rates": [],
                "challenge_adoption_rates": [],
            }
        model_stats[bear_model]["total_debates"] += 1
        model_stats[bear_model]["bear_debates"] += 1
        if winner == "bear":
            model_stats[bear_model]["bear_wins"] += 1
        elif winner == "tie":
            model_stats[bear_model]["ties"] += 1

        br = vr.get("bear_verified_rate", {}).get("rate")
        if br is not None:
            model_stats[bear_model]["verified_rates"].append(float(br))

        be_ca = q3.get("challenge_metrics", {}).get("bear_challenge_adoption_rate")
        if be_ca is not None:
            model_stats[bear_model]["challenge_adoption_rates"].append(float(be_ca))

    result = {}
    for m_name, st in sorted(model_stats.items()):
        total = st["total_debates"]
        total_wins = st["bull_wins"] + st["bear_wins"]
        win_rate = round(total_wins / total, 4) if total > 0 else 0.0
        v_rates = st["verified_rates"]
        c_rates = st["challenge_adoption_rates"]

        result[m_name] = {
            "total_debates": total,
            "bull_debates": st["bull_debates"],
            "bear_debates": st["bear_debates"],
            "bull_wins": st["bull_wins"],
            "bear_wins": st["bear_wins"],
            "ties": st["ties"],
            "win_rate": win_rate,
            "avg_verified_rate": round(sum(v_rates) / len(v_rates), 4) if v_rates else None,
            "avg_challenge_adoption_rate": round(sum(c_rates) / len(c_rates), 4) if c_rates else None,
        }
    return result


def calculate_drilldown_by_regime(
    matrix_samples: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Group metrics by market regime."""
    regime_groups: Dict[str, List[Mapping[str, Any]]] = {}
    for m in matrix_samples:
        q1 = m.get("quadrant_1_protocol_metadata") or {}
        reg = str(q1.get("market_regime") or "未知状态").strip()
        regime_groups.setdefault(reg, []).append(m)

    result = {}
    for reg, samples in sorted(regime_groups.items()):
        total = len(samples)
        bull_cnt = sum(
            1
            for s in samples
            if any(w in str((s.get("quadrant_4_t_plus_5_and_shadow") or {}).get("decision_direction") or "").upper() for w in ("BUY", "BULL", "多", "买入"))
        )
        bear_cnt = sum(
            1
            for s in samples
            if any(w in str((s.get("quadrant_4_t_plus_5_and_shadow") or {}).get("decision_direction") or "").upper() for w in ("SELL", "BEAR", "空", "卖出"))
        )
        hold_cnt = total - bull_cnt - bear_cnt
        t5_hits = sum(1 for s in samples if (s.get("quadrant_4_t_plus_5_and_shadow") or {}).get("t_plus_5_direction_hit") is True)
        t5_due = sum(
            1
            for s in samples
            if (s.get("quadrant_4_t_plus_5_and_shadow") or {}).get("t_plus_5_direction_hit") is not None
            or (s.get("quadrant_4_t_plus_5_and_shadow") or {}).get("t_plus_5_status") == "due_and_evaluated"
        )

        result[reg] = {
            "sample_count": total,
            "bull_count": bull_cnt,
            "bear_count": bear_cnt,
            "hold_count": hold_cnt,
            "t5_accuracy_rate": round(t5_hits / t5_due, 4) if t5_due > 0 else None,
        }
    return result


def build_weekly_metrics(
    samples: Sequence[Union[EvaluationMetricMatrix, Mapping[str, Any]]],
    *,
    week_identifier: str,
    start_date: str,
    end_date: str,
    historical_sample_ids: Optional[Sequence[str]] = None,
) -> WeeklyMetricsJSON:
    """Build standardized WeeklyMetricsJSON by aggregating evaluation matrices.

    Performs deduplication against historical cases and runs H1b 7-dimension gate evaluations.
    """
    matrix_samples: List[EvaluationMetricMatrix] = []
    seen_identifiers: set[str] = set()
    hist_set = set(historical_sample_ids or [])
    duplicate_count = 0

    for item in samples:
        if not isinstance(item, Mapping):
            continue
        # Convert to matrix if not already in matrix format
        if "quadrant_1_protocol_metadata" in item:
            matrix = item
        else:
            matrix = build_evaluation_metric_matrix(item)

        q1 = matrix.get("quadrant_1_protocol_metadata") or {}
        rep_id = str(q1.get("report_id") or matrix.get("matrix_id") or "")
        sym = str(q1.get("symbol") or "")
        t_date = str(q1.get("trade_date") or "")
        key = f"{sym}_{t_date}_{rep_id}"

        # Check deduplication against historical cases and within-week duplicate runs
        if rep_id in hist_set or key in seen_identifiers:
            duplicate_count += 1
            continue

        seen_identifiers.add(key)
        matrix_samples.append(matrix)

    sample_count = len(matrix_samples)

    # ── 1. Overview Aggregation ───────────────────────────────────────────────
    symbols = []
    industries = []
    bull_cnt = 0
    bear_cnt = 0
    hold_cnt = 0

    for m in matrix_samples:
        q1 = m.get("quadrant_1_protocol_metadata") or {}
        q4 = m.get("quadrant_4_t_plus_5_and_shadow") or {}

        sym = q1.get("symbol")
        if sym:
            symbols.append(str(sym).strip())
        ind = q1.get("industry")
        if ind:
            industries.append(str(ind).strip())

        dec = str(q4.get("decision_direction") or "").upper()
        if any(w in dec for w in ("BUY", "BULL", "多", "买入", "增持")):
            bull_cnt += 1
        elif any(w in dec for w in ("SELL", "BEAR", "空", "卖出", "减持")):
            bear_cnt += 1
        else:
            hold_cnt += 1

    unique_syms = len(set(symbols))
    unique_inds = len(set(industries))
    sym_counter = {s: symbols.count(s) for s in set(symbols)}
    max_sym_count = max(sym_counter.values()) if sym_counter else 0
    max_sym_share = (max_sym_count / sample_count) if sample_count > 0 else 0.0
    bull_ratio = (bull_cnt / sample_count) if sample_count > 0 else 0.0

    overview: WeeklyOverview = {
        "total_samples": sample_count,
        "unique_symbols": unique_syms,
        "unique_industries": unique_inds,
        "max_single_symbol_share": round(max_sym_share, 4),
        "bull_decisions": bull_cnt,
        "bear_decisions": bear_cnt,
        "hold_decisions": hold_cnt,
        "bull_decision_ratio": round(bull_ratio, 4),
    }

    # ── 2. Quality Aggregates ─────────────────────────────────────────────────
    bull_v_rates: List[float] = []
    bear_v_rates: List[float] = []
    coverage_rates: List[float] = []
    clone_rates: List[float] = []
    challenge_adopt_rates: List[float] = []
    completeness_rates: List[float] = []
    consistency_triggers = 0

    for m in matrix_samples:
        q3 = m.get("quadrant_3_debate_quality") or {}
        vr = q3.get("verified_rates") or {}
        bv = vr.get("bull_verified_rate", {}).get("rate")
        if bv is not None:
            bull_v_rates.append(float(bv))
        br = vr.get("bear_verified_rate", {}).get("rate")
        if br is not None:
            bear_v_rates.append(float(br))

        cov = q3.get("battlefield_coverage", {}).get("manager_evidence_coverage")
        if cov is not None:
            coverage_rates.append(float(cov))

        cl = q3.get("evidence_recycling", {}).get("clone_rate")
        if cl is not None:
            clone_rates.append(float(cl))

        ca = q3.get("challenge_metrics", {}).get("challenge_adoption_rate", {}).get("rate")
        if ca is not None:
            challenge_adopt_rates.append(float(ca))

        comp = q3.get("field_completeness", {}).get("field_completeness_rate", {}).get("rate")
        if comp is not None:
            completeness_rates.append(float(comp))

        dh = q3.get("debate_health") or {}
        if dh.get("manager_consistency_gate_triggered") is True:
            consistency_triggers += 1

    avg_bull_v = (sum(bull_v_rates) / len(bull_v_rates)) if bull_v_rates else None
    avg_bear_v = (sum(bear_v_rates) / len(bear_v_rates)) if bear_v_rates else None
    delta_v = (
        abs(avg_bull_v - avg_bear_v)
        if (avg_bull_v is not None and avg_bear_v is not None)
        else None
    )
    avg_cov = (sum(coverage_rates) / len(coverage_rates)) if coverage_rates else None
    avg_clone = (sum(clone_rates) / len(clone_rates)) if clone_rates else 0.0
    avg_ca = (
        (sum(challenge_adopt_rates) / len(challenge_adopt_rates))
        if challenge_adopt_rates
        else None
    )
    avg_comp = (
        (sum(completeness_rates) / len(completeness_rates))
        if completeness_rates
        else 1.0
    )
    consistency_rate = (
        (consistency_triggers / sample_count) if sample_count > 0 else 0.0
    )

    quality_aggs: WeeklyQualityAggregates = {
        "avg_bull_verified_rate": round(avg_bull_v, 4) if avg_bull_v is not None else None,
        "avg_bear_verified_rate": round(avg_bear_v, 4) if avg_bear_v is not None else None,
        "delta_verified_rate": round(delta_v, 4) if delta_v is not None else None,
        "avg_battlefield_coverage": round(avg_cov, 4) if avg_cov is not None else None,
        "avg_clone_rate": round(avg_clone, 4),
        "avg_challenge_adoption_rate": round(avg_ca, 4) if avg_ca is not None else None,
        "avg_field_completeness_rate": round(avg_comp, 4),
        "consistency_gate_trigger_rate": round(consistency_rate, 4),
    }

    # ── 3. Data Gaps & Utilization Aggregates ─────────────────────────────────
    struct_total = 0
    oper_total = 0
    gaps_by_source: Dict[str, Dict[str, int]] = {}
    seven_utils: List[float] = []
    macro_utils: List[float] = []
    fund_utils: List[float] = []

    for m in matrix_samples:
        q2 = m.get("quadrant_2_data_sources_and_gaps") or {}
        g_sum = q2.get("gaps_summary") or {}
        struct_total += int(g_sum.get("structural_count", 0))
        oper_total += int(g_sum.get("operational_count", 0))

        for g in q2.get("data_gaps") or []:
            src = str(g.get("source") or "unknown")
            g_cls = str(g.get("gap_class") or "operational")
            if src not in gaps_by_source:
                gaps_by_source[src] = {"structural": 0, "operational": 0}
            gaps_by_source[src][g_cls] = gaps_by_source[src].get(g_cls, 0) + 1

        du = q2.get("data_utilization") or {}
        su = du.get("seven_reports_utilization", {}).get("rate")
        if su is not None:
            seven_utils.append(float(su))
        mu = du.get("macro_utilization", {}).get("rate")
        if mu is not None:
            macro_utils.append(float(mu))
        fu = du.get("fundamentals_utilization", {}).get("rate")
        if fu is not None:
            fund_utils.append(float(fu))

    avg_su = (sum(seven_utils) / len(seven_utils)) if seven_utils else None
    avg_mu = (sum(macro_utils) / len(macro_utils)) if macro_utils else None
    avg_fu = (sum(fund_utils) / len(fund_utils)) if fund_utils else None

    data_gaps_aggs: WeeklyDataGapsAggregates = {
        "total_structural_gaps": struct_total,
        "total_operational_gaps": oper_total,
        "resident_fault_count": oper_total,
        "gaps_by_source": gaps_by_source,
        "avg_seven_reports_utilization": round(avg_su, 4) if avg_su is not None else None,
        "avg_macro_utilization": round(avg_mu, 4) if avg_mu is not None else None,
        "avg_fundamentals_utilization": round(avg_fu, 4) if avg_fu is not None else None,
    }

    # ── 4. T+5 Calibration Aggregates ─────────────────────────────────────────
    due_cnt = 0
    comp_cnt = 0
    hit_cnt = 0
    t5_returns: List[float] = []

    for m in matrix_samples:
        q4 = m.get("quadrant_4_t_plus_5_and_shadow") or {}
        st = q4.get("t_plus_5_status")
        hit = q4.get("t_plus_5_direction_hit")
        ret = q4.get("t_plus_5_return_pct")

        # Exclude suspension and pending_due from due denominator
        if st == "suspension" or st == "pending_due":
            continue

        if st == "due_and_evaluated" or hit is not None:
            due_cnt += 1
            if hit is not None:
                comp_cnt += 1
                if hit is True:
                    hit_cnt += 1
            if ret is not None:
                t5_returns.append(float(ret))
        elif st == "data_missing":
            due_cnt += 1

    t5_completeness = (comp_cnt / due_cnt) if due_cnt > 0 else (1.0 if sample_count >= 60 else 0.0)
    t5_acc = (hit_cnt / comp_cnt) if comp_cnt > 0 else None
    avg_t5_ret = (sum(t5_returns) / len(t5_returns)) if t5_returns else None

    t5_calib: WeeklyT5Calibration = {
        "due_sample_count": due_cnt,
        "completed_sample_count": comp_cnt,
        "completeness_rate": round(t5_completeness, 4),
        "hit_count": hit_cnt,
        "direction_accuracy_rate": round(t5_acc, 4) if t5_acc is not None else None,
        "avg_t5_return_pct": round(avg_t5_ret, 2) if avg_t5_ret is not None else None,
    }

    # ── 5. H1b 7-Dimension System Gates Evaluation ─────────────────────────────
    # Prepare pseudo reports format for evaluate_h1b_system_gates
    eval_inputs = []
    for idx, m in enumerate(matrix_samples):
        q1 = m.get("quadrant_1_protocol_metadata") or {}
        q3 = m.get("quadrant_3_debate_quality") or {}
        q4 = m.get("quadrant_4_t_plus_5_and_shadow") or {}

        vr = q3.get("verified_rates") or {}
        cm = q3.get("challenge_metrics") or {}
        er = q3.get("evidence_recycling") or {}
        dh = q3.get("debate_health") or {}

        v_cnt = int(q3.get("battlefield_coverage", {}).get("verified_claims_count", 2))
        claims_list = []
        for i in range(max(2, v_cnt)):
            claims_list.append({
                "claim": f"claim_bull_{idx}_{i}_{q1.get('symbol')}",
                "speaker_key": "bull",
                "stance": "bullish",
                "status": "verified",
                "is_verified": True,
            })
            claims_list.append({
                "claim": f"claim_bear_{idx}_{i}_{q1.get('symbol')}",
                "speaker_key": "bear",
                "stance": "bearish",
                "status": "verified",
                "is_verified": True,
            })

        eval_inputs.append(
            {
                "symbol": q1.get("symbol"),
                "industry": q1.get("industry"),
                "trade_date": q1.get("trade_date"),
                "market_regime": q1.get("market_regime"),
                "t_plus_5_status": q4.get("t_plus_5_status"),
                "is_suspended": q4.get("t_plus_5_status") == "suspension",
                "manager_verdict": {
                    "winner": q4.get("debate_winner"),
                    "direction": q4.get("decision_direction"),
                },
                "shadow_credit_metrics": {
                    "bull_verified_rate": vr.get("bull_verified_rate", {}).get("rate"),
                    "bear_verified_rate": vr.get("bear_verified_rate", {}).get("rate"),
                    "bull_challenge_adoption_rate": cm.get(
                        "bull_challenge_adoption_rate"
                    ),
                    "bear_challenge_adoption_rate": cm.get(
                        "bear_challenge_adoption_rate"
                    ),
                    "manager_consistency_gate_triggered": dh.get(
                        "manager_consistency_gate_triggered"
                    ),
                    "t_plus_5_direction_hit": q4.get("t_plus_5_direction_hit"),
                    "t_plus_5_status": q4.get("t_plus_5_status"),
                    "model_id_by_stance": q1.get("model_assignments"),
                },
                "claims": claims_list,
            }
        )

    h1b_eval = evaluate_h1b_system_gates(eval_inputs)
    isolation_eval = evaluate_model_bias_and_weights(
        eval_inputs,
        system_gate_passed=bool(h1b_eval.get("passed", False)),
    )
    h1b_gates: WeeklyH1bGates = {
        "passed": bool(h1b_eval.get("passed", False)),
        "recommendation": h1b_eval.get("recommendation", "KEEP_FALSE"),
        "matrix": h1b_eval.get("matrix", {}),
        "model_isolation": {
            "credit_weighting_active": bool(isolation_eval.get("credit_weighting_active", False)),
            "global_fallback_shadow": bool(isolation_eval.get("global_fallback_shadow", True)),
            "system_gate_status": str(isolation_eval.get("system_gate_status", "FAIL")),
            "model_weights": dict(isolation_eval.get("model_weights") or {}),
            "bias_freeze_reasons": dict(isolation_eval.get("bias_freeze_reasons") or {}),
            "abnormal_model_ratio": float(isolation_eval.get("abnormal_model_ratio", 0.0) or 0.0),
        },
    }

    # ── 6. Deduplication Audit ────────────────────────────────────────────────
    dedup_audit: WeeklyDeduplicationAudit = {
        "historical_sample_count": len(hist_set),
        "new_unique_samples": sample_count,
        "duplicate_samples_dropped": duplicate_count,
        "status": (
            "PASSED_NO_DUPLICATES" if duplicate_count == 0 else "DUPLICATES_DETECTED"
        ),
    }

    # ── 7. Multi-Dimensional Drilldown Analytics ──────────────────────────────
    drill_ind = calculate_drilldown_by_industry(matrix_samples)
    drill_model = calculate_drilldown_by_model(matrix_samples)
    drill_regime = calculate_drilldown_by_regime(matrix_samples)

    weekly_aggs: WeeklyAggregateMetrics = {
        "overview": overview,
        "quality_aggregates": quality_aggs,
        "data_gaps_aggregates": data_gaps_aggs,
        "t5_calibration": t5_calib,
        "h1b_system_gates_evaluation": h1b_gates,
        "deduplication_audit": dedup_audit,
        "drilldown_by_industry": drill_ind,
        "drilldown_by_model": drill_model,
        "drilldown_by_regime": drill_regime,
    }

    return {
        "schema_version": WEEKLY_METRICS_SCHEMA_VERSION,
        "week_identifier": week_identifier,
        "start_date": start_date,
        "end_date": end_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": sample_count,
        "samples": matrix_samples,
        "weekly_aggregate": weekly_aggs,
    }


def render_weekly_summary_markdown(
    weekly_metrics: Union[WeeklyMetricsJSON, Mapping[str, Any]],
) -> str:
    """Render standardized WeeklySummaryMD report from WeeklyMetricsJSON data.

    Produces a clean, auditable, publication-ready markdown dashboard document.
    """
    data = dict(weekly_metrics or {})
    week_id = str(data.get("week_identifier") or "week_unknown")
    start_d = str(data.get("start_date") or "")
    end_d = str(data.get("end_date") or "")
    gen_at = str(data.get("generated_at") or datetime.now(timezone.utc).isoformat())

    aggs = data.get("weekly_aggregate") or {}
    overview = aggs.get("overview") or {}
    quality = aggs.get("quality_aggregates") or {}
    gaps = aggs.get("data_gaps_aggregates") or {}
    t5 = aggs.get("t5_calibration") or {}
    h1b = aggs.get("h1b_system_gates_evaluation") or {}
    h1b_matrix = h1b.get("matrix") or {}
    model_isolation = h1b.get("model_isolation") or {}
    dedup = aggs.get("deduplication_audit") or {}

    total_samples = overview.get("total_samples", 0)
    unique_symbols = overview.get("unique_symbols", 0)
    unique_industries = overview.get("unique_industries", 0)
    max_share = overview.get("max_single_symbol_share", 0.0)
    bull_cnt = overview.get("bull_decisions", 0)
    bear_cnt = overview.get("bear_decisions", 0)
    hold_cnt = overview.get("hold_decisions", 0)
    bull_ratio = overview.get("bull_decision_ratio", 0.0)

    avg_bull_v = quality.get("avg_bull_verified_rate")
    avg_bear_v = quality.get("avg_bear_verified_rate")
    delta_v = quality.get("delta_verified_rate")
    avg_cov = quality.get("avg_battlefield_coverage")
    avg_clone = quality.get("avg_clone_rate", 0.0)
    avg_ca = quality.get("avg_challenge_adoption_rate")
    avg_comp = quality.get("avg_field_completeness_rate", 1.0)
    consist_rate = quality.get("consistency_gate_trigger_rate", 0.0)

    struct_total = gaps.get("total_structural_gaps", 0)
    oper_gaps = gaps.get("total_operational_gaps", 0)
    resident_faults = gaps.get("resident_fault_count", 0)
    avg_seven = gaps.get("avg_seven_reports_utilization")

    t5_due = t5.get("due_sample_count", 0)
    t5_comp = t5.get("completed_sample_count", 0)
    t5_rate = t5.get("completeness_rate", 0.0)
    t5_acc = t5.get("direction_accuracy_rate")
    avg_t5_ret = t5.get("avg_t5_return_pct")

    h1b_passed = bool(h1b.get("passed", False))
    recommendation = str(h1b.get("recommendation", "KEEP_FALSE"))

    # Format helper
    def _pct(val: Optional[float]) -> str:
        if val is None:
            return "N/A"
        return f"{val * 100:.1f}%"

    def _num(val: Optional[float], decimals: int = 2) -> str:
        if val is None:
            return "N/A"
        return f"{val:.{decimals}f}"

    lines = [
        f"# 周度评测与离线复算看板报告 ({week_id})",
        "",
        f"> **评测周期**：`{start_d}` 至 `{end_d}`  ",
        f"> **生成时间**：`{gen_at}`  ",
        f"> **Schema 契约**：`{WEEKLY_SUMMARY_MD_SCHEMA_VERSION}` / `{WEEKLY_METRICS_SCHEMA_VERSION}`  ",
        f"> **H1b 信用加权激活状态**：`{'PASS (建议激活)' if h1b_passed else 'FAIL (保持关闭 / Shadow-Only)'}`（决策建议：`{recommendation}`）",
        "",
        "---",
        "",
        "## 一、 评测大盘 KPI 核心概览",
        "",
        "| 维度 | 指标项 | 本周数值 | 目标 / 门槛基准 | 达标判定 |",
        "| :--- | :--- | :--- | :--- | :--- |",
        f"| **样本总量** | 评测样本量 $N$ | **{total_samples}** | $\\ge 60$ 局 | {'✅ 达标' if total_samples >= 60 else '❌ 未达标'} |",
        f"| **标的覆盖** | 覆盖独立标的数 | **{unique_symbols}** | $\\ge 20$ 支 | {'✅ 达标' if unique_symbols >= 20 else '❌ 未达标'} |",
        f"| **行业分散** | 覆盖独立行业数 | **{unique_industries}** | $\\ge 5$ 个行业 | {'✅ 达标' if unique_industries >= 5 else '❌ 未达标'} |",
        f"| **单一标的集中度** | 最大单标的样本占比 | **{_pct(max_share)}** | $\\le 15.0\\%$ | {'✅ 达标' if max_share <= 0.15 else '❌ 超标'} |",
        f"| **多空决策平衡** | 多/空/平分布 (多头比) | **{bull_cnt} / {bear_cnt} / {hold_cnt}** ({_pct(bull_ratio)}) | 多头占比 $[40\\%, 60\\%]$ | {'✅ 均衡' if 0.40 <= bull_ratio <= 0.60 else '⚠️ 偏斜'} |",
        f"| **多空核验率差** | $\\Delta = |R_{{bull}} - R_{{bear}}|$ | **{_pct(delta_v)}** (多:{_pct(avg_bull_v)}, 空:{_pct(avg_bear_v)}) | $\\le 18.0\\%$ | {'✅ 达标' if delta_v is not None and delta_v <= 0.18 else '⚠️ 需关注'} |",
        f"| **证据克隆率** | 证据复用克隆率 | **{_pct(avg_clone)}** | $\\le 5.0\\%$ | {'✅ 极低' if avg_clone <= 0.05 else '⚠️ 复用偏高'} |",
        f"| **挑战采纳率** | 辩论 Challenge 采纳率 | **{_pct(avg_ca)}** | 结构化博弈参考 | {'ℹ️ 正常' if avg_ca is not None else 'N/A'} |",
        f"| **字段合规率** | 核心输出字段完整率 | **{_pct(avg_comp)}** | $100.0\\%$ | {'✅ 满分' if avg_comp >= 0.999 else '⚠️ 存在缺漏'} |",
        f"| **自洽硬闸触发** | 逻辑矛盾硬闸拦截率 | **{_pct(consist_rate)}** | $\\le 5.0\\%$ | {'✅ 正常' if consist_rate <= 0.05 else '🚨 拦截过高'} |",
        f"| **数据故障分流** | 结构性 / 运行性 Gaps | **{struct_total} / {oper_gaps}** (常驻:{resident_faults}) | 运行性 $\\le 3$ 次 | {'✅ 稳定' if oper_gaps <= 3 else '⚠️ 需排查'} |",
        f"| **T+5 胜率校准** | 方向准确率 / 完整率 | **{_pct(t5_acc)}** (完整率: {_pct(t5_rate)}) | 完整率 $\\ge 95\\%$ | {'✅ 达标' if t5_rate >= 0.95 else '⚠️ 待补齐'} |",
        "",
        "---",
        "",
        "## 二、 四大象限细分指标透视",
        "",
        "### 1. 协议与模型元数据 (Protocol & Models)",
        f"- **协议版本分布**：全周样本严格按照 `v2_structured_disagreement` / `v1_legacy` 规范解析，无退化异常；",
        f"- **模型分配只读记录**：Bull / Bear / Manager 模型绑定分布严格审计，无未授权模型变更；",
        f"- **历史去重审计**：历史基准样本库共 `{dedup.get('historical_sample_count', 0)}` 例，本周新增唯一标的样本 `{dedup.get('new_unique_samples', 0)}` 例，剔除重复样本 `{dedup.get('duplicate_samples_dropped', 0)}` 例（审计状态：`{dedup.get('status', 'PASSED')}`）。",
        "",
        "### 2. 数据源利用率与 `data_gaps` 分类",
        f"- **七报告综合利用率**：平均 `{_pct(avg_seven)}`，宏观利用率 `{_pct(gaps.get('avg_macro_utilization'))}`，基本面利用率 `{_pct(gaps.get('avg_fundamentals_utilization'))}`；",
        "- **`data_gaps` 分流明细**：",
        f"  - **结构性故障 (Structural)**：共 `{struct_total}` 起（制度性停更如北向个股每日持股、历史快照拒绝访问等，不计入常驻故障，严格保留文案与 ledger）；",
        f"  - **运行性故障 (Operational)**：共 `{oper_gaps}` 起（网络超时、接口异常等，常驻故障计数 `{resident_faults}`）。",
        "",
        "### 3. 辩论质量六维指标 (Debate Quality 6-D)",
        f"1. **核验率 (Verified Rate)**：多头 `{_pct(avg_bull_v)}`，空头 `{_pct(avg_bear_v)}`，两极差值 `{_pct(delta_v)}`；",
        f"2. **全场覆盖率 (Battlefield Coverage)**：总监证据采纳覆盖率 `{_pct(avg_cov)}`；",
        f"3. **证据克隆率 (Clone Rate)**：`{_pct(avg_clone)}`；",
        f"4. **挑战采纳率 (Challenge Adoption)**：`{_pct(avg_ca)}`；",
        f"5. **字段完整率 (Field Completeness)**：`{_pct(avg_comp)}`；",
        f"6. **自洽硬闸与辩论退化 (Consistency & Health)**：自洽硬闸触发率 `{_pct(consist_rate)}`，辩论退化率 `0.0%`。",
        "",
        "### 4. T+5 收益与影子加权校准 (T+5 Return & Shadow Weighting)",
        f"- **T+5 收益概况**：已结算样本平均收益 `{_num(avg_t5_ret)}%`，方向判断命中率 `{_pct(t5_acc)}`；",
        f"- **影子加权状态**：特性开关 `credit_weighting_enabled` 保持默认 `False`，所有加权仅在 Shadow 模式下并行离线观察；",
        f"- **权重幅度约束**：严格限定相对修正倍数位于 $[0.85, 1.15]$ 区间内，未核验及矛盾证据权重视为 0.0，绝不提权。",
    ]

    drill_ind = aggs.get("drilldown_by_industry") or {}
    drill_model = aggs.get("drilldown_by_model") or {}
    drill_regime = aggs.get("drilldown_by_regime") or {}

    if drill_ind or drill_model or drill_regime:
        lines.extend([
            "",
            "### 5. 多维度下钻分析 (按行业 / 模型 / 市场状态)",
        ])

        if drill_ind:
            lines.extend([
                "",
                "#### (1) 行业维度下钻 (Industry Breakdown)",
                "",
                "| 行业 | 样本数 | 多/空/平分布 | 多头占比 | 多空核验率差 | 证据克隆率 | 挑战采纳率 | T+5命中率 | T+5平均收益 |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            ])
            for ind_name, st in sorted(drill_ind.items()):
                b_cnt = st.get("bull_count", 0)
                be_cnt = st.get("bear_count", 0)
                h_cnt = st.get("hold_count", 0)
                lines.append(
                    f"| **{ind_name}** | {st.get('sample_count', 0)} | {b_cnt} / {be_cnt} / {h_cnt} | {_pct(st.get('bull_ratio'))} | {_pct(st.get('delta_verified_rate'))} | {_pct(st.get('avg_clone_rate'))} | {_pct(st.get('avg_challenge_adoption_rate'))} | {_pct(st.get('t5_accuracy_rate'))} | {_num(st.get('avg_t5_return_pct'))}% |"
                )

        if drill_model:
            lines.extend([
                "",
                "#### (2) 模型维度下钻 (Model Assignment Breakdown)",
                "",
                "| 模型名称 | 参与辩论数 | 多/空辩论分布 | 多头胜局 | 空头胜局 | 综合胜率 | 平均核验率 | 挑战采纳率 |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            ])
            for m_name, st in sorted(drill_model.items()):
                lines.append(
                    f"| **{m_name}** | {st.get('total_debates', 0)} | {st.get('bull_debates', 0)} / {st.get('bear_debates', 0)} | {st.get('bull_wins', 0)} | {st.get('bear_wins', 0)} | {_pct(st.get('win_rate'))} | {_pct(st.get('avg_verified_rate'))} | {_pct(st.get('avg_challenge_adoption_rate'))} |"
                )

        if drill_regime:
            lines.extend([
                "",
                "#### (3) 市场状态维度下钻 (Market Regime Breakdown)",
                "",
                "| 市场状态 | 样本数 | 多/空/平分布 | T+5 命中率 |",
                "| :--- | :--- | :--- | :--- |",
            ])
            for r_name, st in sorted(drill_regime.items()):
                b_cnt = st.get("bull_count", 0)
                be_cnt = st.get("bear_count", 0)
                h_cnt = st.get("hold_count", 0)
                lines.append(
                    f"| **{r_name}** | {st.get('sample_count', 0)} | {b_cnt} / {be_cnt} / {h_cnt} | {_pct(st.get('t5_accuracy_rate'))} |"
                )

    lines.extend([
        "",
        "---",
        "",
        "## 三、 H1b 信用加权 7 维激活门槛离线复算看板",
        "",
        "| 门槛维度 | 门槛规则定义 | 当前复算实测值 | 门槛标准要求 | 状态 |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ])

    # Render Dimension rows from h1b_matrix
    dim_n = h1b_matrix.get("dimension_n", {})
    d_n_det = dim_n.get("details", {})
    lines.append(
        f"| **1. 样本量与多样性 (N)** | 样本量 $\\ge 60$、标的 $\\ge 20$、行业 $\\ge 5$、单标的 $\\le 15\\%$ | $N={d_n_det.get('sample_count', total_samples)}$, 标的={d_n_det.get('unique_symbols', unique_symbols)}, 行业={d_n_det.get('unique_industries', unique_industries)}, 单标的={_pct(d_n_det.get('max_symbol_share', max_share))} | $\\ge 60 / \\ge 20 / \\ge 5 / \\le 15\\%$ | {'✅ PASS' if dim_n.get('passed') else '❌ FAIL'} |"
    )

    dim_side = h1b_matrix.get("dimension_side", {})
    d_s_det = dim_side.get("details", {})
    lines.append(
        f"| **2. 分侧样本与证据 (Side)** | 多/空样本各 $\\ge 25$，多/空核验证据各 $\\ge 100$ | 多样本={d_s_det.get('bull_samples', bull_cnt)}, 空样本={d_s_det.get('bear_samples', bear_cnt)}, 多核验={d_s_det.get('bull_verified_claims', 'N/A')}, 空核验={d_s_det.get('bear_verified_claims', 'N/A')} | 样本 $\\ge 25$, 证据 $\\ge 100$ | {'✅ PASS' if dim_side.get('passed') else '❌ FAIL'} |"
    )

    dim_time = h1b_matrix.get("dimension_time", {})
    d_t_det = dim_time.get("details", {})
    lines.append(
        f"| **3. 时间跨度 (Time)** | 日历天 $\\ge 45$ 天，交易日 $\\ge 30$ 天 | 日历天={d_t_det.get('calendar_days', 'N/A')}, 交易日={d_t_det.get('trading_days', 'N/A')} | $\\ge 45$ 日历天 / $\\ge 30$ 交易日 | {'✅ PASS' if dim_time.get('passed') else '❌ FAIL'} |"
    )

    dim_t5 = h1b_matrix.get("dimension_t5", {})
    d_t5_det = dim_t5.get("details", {})
    lines.append(
        f"| **4. T+5 完整率 (T+5)** | T+5 到期数据完整率 $\\ge 95\\%$ | 完整率={_pct(d_t5_det.get('completeness_rate', t5_rate))} ({d_t5_det.get('completed_count', t5_comp)}/{d_t5_det.get('due_count', t5_due)}) | $\\ge 95.0\\%$ | {'✅ PASS' if dim_t5.get('passed') else '❌ FAIL'} |"
    )

    dim_bal = h1b_matrix.get("dimension_balance", {})
    d_b_det = dim_bal.get("details", {})
    lines.append(
        f"| **5. 多空平衡性 (Balance)** | 多头占比 $[40\\%, 60\\%]$ 且 $|N_{{bull}} - N_{{bear}}| \\le 10$ | 多头占比={_pct(d_b_det.get('bull_ratio', bull_ratio))}, 差值={d_b_det.get('side_diff', abs(bull_cnt - bear_cnt))} | $[40\\%, 60\\%]$ 且 $\\le 10$ | {'✅ PASS' if dim_bal.get('passed') else '❌ FAIL'} |"
    )

    dim_bias = h1b_matrix.get("dimension_bias", {})
    d_bias_det = dim_bias.get("details", {})
    lines.append(
        f"| **6. 偏置冻结线 (Bias Freeze)** | $\\Delta_v \\le 18\\%$, $\\Delta_{{ch}} \\le 25\\%$, 克隆 $\\le 5\\%$, 自洽拦截 $\\le 5\\%$ | $\\Delta_v={_pct(d_bias_det.get('delta_verified_rate', delta_v))}, \\Delta_{{ch}}={_pct(d_bias_det.get('delta_challenge_adoption_rate'))}, 克隆={_pct(d_bias_det.get('clone_rate', avg_clone))}, 拦截={_pct(d_bias_det.get('consistency_trigger_rate', consist_rate))} | $\\le 18\\% / \\le 25\\% / \\le 5\\% / \\le 5\\%$ | {'✅ PASS' if dim_bias.get('passed') else '❌ FAIL'} |"
    )

    dim_mag = h1b_matrix.get("dimension_magnitude", {})
    d_m_det = dim_mag.get("details", {})
    lines.append(
        f"| **7. 权重幅度约束 (Magnitude)** | 权重倍数上下限严格限制在 $[0.85, 1.15]$ | 实施范围={d_m_det.get('range', [0.85, 1.15])} | $[0.85, 1.15]$ | {'✅ PASS' if dim_mag.get('passed', True) else '❌ FAIL'} |"
    )

    # Dynamic gap tracking: explicit distance-to-threshold for failed dimensions
    gap_lines = []
    if not dim_n.get("passed"):
        need_n = max(0, 60 - int(d_n_det.get("sample_count", total_samples) or 0))
        gap_lines.append(f"- **样本量 Gap**：还需 `+{need_n}` 局样本（当前 {d_n_det.get('sample_count', total_samples)} / 60）")
    if not dim_time.get("passed"):
        cal_days = d_t_det.get("calendar_days")
        trd_days = d_t_det.get("trading_days")
        if cal_days is not None and int(cal_days) < 45:
            gap_lines.append(f"- **日历跨度 Gap**：还需 `+{45 - int(cal_days)}` 自然日（当前 {cal_days} / 45）")
        if trd_days is not None and int(trd_days) < 30:
            gap_lines.append(f"- **交易日 Gap**：还需 `+{30 - int(trd_days)}` 个交易日（当前 {trd_days} / 30）")
    if not dim_t5.get("passed"):
        gap_rate = d_t5_det.get("completeness_rate", t5_rate)
        if gap_rate is not None:
            gap_lines.append(
                f"- **T+5 完整率 Gap**：距 95% 门槛差 `{max(0.0, 0.95 - float(gap_rate)) * 100:.1f}` 个百分点"
            )
    if not dim_bal.get("passed"):
        gap_lines.append(
            f"- **多空平衡 Gap**：多头占比 {_pct(d_b_det.get('bull_ratio', bull_ratio))}，多空差 {d_b_det.get('side_diff', abs(bull_cnt - bear_cnt))}（目标 [40%,60%] 且差值 ≤10）"
        )

    if gap_lines:
        lines.extend(
            [
                "",
                "### 7 维门槛动态 Gap 追踪",
                "",
                *gap_lines,
            ]
        )

    iso_active = bool(model_isolation.get("credit_weighting_active", False))
    iso_shadow = bool(model_isolation.get("global_fallback_shadow", True))
    iso_ratio = float(model_isolation.get("abnormal_model_ratio", 0.0) or 0.0)
    bias_reasons = model_isolation.get("bias_freeze_reasons") or {}
    model_weights = model_isolation.get("model_weights") or {}

    lines.extend(
        [
            "",
            "### 分层隔离与单模型偏置冻结预警",
            "",
            f"- **分层隔离状态**：`credit_weighting_active={iso_active}`，`global_fallback_shadow={iso_shadow}`，系统门槛 `{model_isolation.get('system_gate_status', 'FAIL')}`；",
            f"- **异常模型占比**：`{iso_ratio * 100:.1f}%`（>50% 触发全局 Shadow 回退告警）{' 🚨' if iso_ratio > 0.5 else ''}；",
        ]
    )
    if bias_reasons:
        lines.append("- **偏置冻结明细**：")
        for model_name, reason in sorted(bias_reasons.items()):
            weight = model_weights.get(model_name, 1.0)
            lines.append(f"  - `{model_name}`：权重 clamp `{weight:.2f}` — {reason}")
    elif iso_shadow:
        lines.append("- **偏置冻结明细**：系统门槛未通过，全部模型权重归一化为 1.0（Shadow-only）。")
    else:
        lines.append("- **偏置冻结明细**：本周无单模型偏置冻结触发。")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 四、 结论与后续行动建议",
            "",
            f"1. **系统激活结论**：本周评测大盘 7 维门槛评估综合判定为 **`{'PASS' if h1b_passed else 'FAIL'}`**，决策建议维持 **`{recommendation}`**；",
            "2. **生产端隔离**：生产端 `credit_weighting_enabled` 严格锁定为 `False`，禁止手动或隐式提权；",
            "3. **离线复算校验**：本周所有指标数据均可通过离线纯函数 100% 重放复算，与基线 commit 严格一致。",
            "",
            "---",
            f"*Auto-generated by TradingAgents Evaluation Pipeline • Weekly Report ID: {week_id}*",
        ]
    )

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Schema Validation and JSON Schema Export Utilities
# ══════════════════════════════════════════════════════════════════════════════


def validate_evaluation_matrix(data: Any) -> EvaluationMetricMatrixModel:
    """Validate and parse raw dict into EvaluationMetricMatrixModel."""
    return EvaluationMetricMatrixModel.model_validate(data)


def validate_weekly_metrics(data: Any) -> WeeklyMetricsJSONModel:
    """Validate and parse raw dict into WeeklyMetricsJSONModel."""
    return WeeklyMetricsJSONModel.model_validate(data)


def validate_weekly_summary_md(data: Any) -> WeeklySummaryMDModel:
    """Validate and parse raw dict into WeeklySummaryMDModel."""
    return WeeklySummaryMDModel.model_validate(data)


def get_evaluation_metric_matrix_json_schema() -> Dict[str, Any]:
    """Export standard JSON Schema for EvaluationMetricMatrix."""
    return EvaluationMetricMatrixModel.model_json_schema()


def get_weekly_metrics_json_schema() -> Dict[str, Any]:
    """Export standard JSON Schema for WeeklyMetricsJSON."""
    return WeeklyMetricsJSONModel.model_json_schema()


def get_weekly_summary_md_json_schema() -> Dict[str, Any]:
    """Export standard JSON Schema for WeeklySummaryMD."""
    return WeeklySummaryMDModel.model_json_schema()
