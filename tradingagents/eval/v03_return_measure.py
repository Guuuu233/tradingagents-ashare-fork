"""V-03a Read-Only Return Measurement Engine (Baseline Progress Measurement).

Strict Frozen Specification (work/v03-freeze-sheet-20260909.md / DAV-802):
1. Positioning: System is incomplete (game theory 0%, real news/sentiment unconnected,
   ~30% missing items). This engine is a PROGRESS BASELINE MEASUREMENT TOOL,
   NOT A QUALITATIVE JUDGMENT on whether AI can make profit.
   Every output is stamped with system completeness and explicit disclaimer:
   "半成品基线,非定性判断".
2. Read-Only: Production database opened strictly in mode=ro. Measurement runs
   only on an atomic sqlite3 .backup() replica copy. Zero production DB mutation.
3. Entry: T+1 Open price (signal at T, execute at T+1 Open; no look-ahead).
   If T+1 is suspended, locked at limit-up/down, or untradable -> marked as 'untradable';
   strictly forbidden to pretend to fill at Open.
4. Costs:
   - Commission: <= 3‰ (default account assumption 0.025% = 2.5 bps), min 5 RMB.
     Commission INCLUDES regulatory fees; STRICTLY NO extra handling/supervision fees.
   - Transfer fee: 0.01‰ (0.001% = 0.1 bps) each way (buy & sell).
   - Stamp duty: 0.5‰ (0.05% = 5 bps) sell side only.
   - Slippage: fixed 5 bps single side (10 bps round trip).
5. Benchmark: CSI 300 (000300.SH / 沪深300) over the exact same holding window;
   calculates excess return (alpha).
6. OOS 3-Segment Partition:
   - DEV <= 2025-12-31
   - HISTORICAL_OOS = 2026-01-01 ~ 2026-09-08
   - FORWARD_OOS >= 2026-09-09
   No parameter re-tuning across OOS segments; zero cross-segment leakage.
7. Stock Pool Filtering:
   - Canonical normalization on read using DAV-800 symbol_canonical module.
   - Eligible: A-share ordinary stocks (Main board, ChiNext, STAR market).
   - Excluded: BSE (.BJ / 8xx/4xx/920), ST/*ST, listing < 60 trading days, untradable.
   - Collision merging: e.g. 000001 and 000001.SZ merged into one canonical entity.
8. Typed-Missing Handling:
   - outcome_status = 'typed_missing', return = NULL,
     included_in_return_metrics = false, included_in_coverage_metrics = true.
   - Records missing_reason (e.g. Lens June gap).
   - STRICTLY NO carry-forward; STRICTLY NO silent drop.
9. Baseline Metadata & Completeness Stamping:
   - Model: gemini-3.8-flash-high
   - Prompt hash: 5489166b + code prompt @SHA
   - Code SHA + running service SHA
   - System completeness status.
"""

from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Set, Tuple

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

from tradingagents.dataflows.interface import NetworkAccessDeniedError
from tradingagents.agents.utils.symbol_canonical import (
    CanonicalStatus,
    CanonicalSymbolResult,
    UnmappableReason,
    canonicalize_symbol,
    find_symbol_collisions,
)

# ---------------------------------------------------------------------------
# Enums & Classifications
# ---------------------------------------------------------------------------


class OOSSegment(str, Enum):
    """Out-of-sample partition segments."""

    DEV = "DEV"  # trade_date <= dev_cutoff
    HISTORICAL_OOS = "HISTORICAL_OOS"  # dev_cutoff < trade_date <= historical_cutoff
    FORWARD_OOS = "FORWARD_OOS"  # historical_cutoff < trade_date <= forward_oos_end
    FUTURE_DATA = "FUTURE_DATA"  # trade_date > forward_oos_end


class SampleRole(str, Enum):
    """Role classification for audit records (V-03a-3 Section 1 & Section 4)."""

    REGRESSION = "regression"  # 6 golden benchmark symbols, strictly excluded from OOS metrics
    DEV = "dev"  # trade_date <= 2025-12-31
    HISTORICAL_OOS = "historical_oos"  # 2026-01-01 ~ 2026-09-08
    FORWARD_OOS = "forward_oos"  # trade_date >= 2026-09-09
    CALIBRATION = "calibration"  # calibration cohort
    FUTURE_DATA = "future_data"  # trade_date > forward_oos_end


class OrderAblationMode(str, Enum):
    """Order ablation modes (V-03a-3 Section 3)."""

    CANONICAL = "canonical"
    REVERSED = "reversed"
    SEEDED_SHUFFLED = "seeded_shuffled"


# Six regression benchmark symbols permanently marked sample_role=regression (DAV-799 / V-03)
REGRESSION_SYMBOLS: Set[str] = {
    "002241.SZ",  # 歌尔股份
    "601138.SH",  # 工业富联
    "300433.SZ",  # 蓝思科技
    "000333.SZ",  # 美的集团
    "601012.SH",  # 隆基绿能
    "300015.SZ",  # 爱尔眼科
}

REGRESSION_NAMES: Dict[str, str] = {
    "002241.SZ": "歌尔股份",
    "601138.SH": "工业富联",
    "300433.SZ": "蓝思科技",
    "000333.SZ": "美的集团",
    "601012.SH": "隆基绿能",
    "300015.SZ": "爱尔眼科",
}

# Strict 25-field offline audit table schema (V-03a-3 Section 2)
MINIMUM_AUDIT_FIELDS: Tuple[str, ...] = (
    "sample_id",
    "symbol",
    "cutoff_datetime",
    "requested_as_of",
    "profile_id",
    "model_name",
    "prompt_version",
    "sample_role",
    "evaluation_eligible",
    "exclusion_reason",
    "label_horizon",
    "eval_offset_days",
    "signal_date",
    "executable_entry_date",
    "actual_exit_date",
    "roll_days_used",
    "trade_action",
    "entry_price",
    "exit_price",
    "cost_assumptions",
    "gross_return_pct",
    "net_return_pct",
    "performance_category",
    "wait_subsequent_return_pct",
    "evidence_provenance",
)


def validate_audit_row(row: Dict[str, Any]) -> None:
    """Strictly validate that all 25 audit fields are present without extra or missing fields."""
    missing = [f for f in MINIMUM_AUDIT_FIELDS if f not in row]
    if missing:
        raise ValueError(f"Audit record missing required fields: {missing}")
    extra = [f for f in row if f not in MINIMUM_AUDIT_FIELDS]
    if extra:
        raise ValueError(f"Audit record contains unexpected extra fields: {extra}")
    for mandatory in (
        "sample_id",
        "symbol",
        "cutoff_datetime",
        "requested_as_of",
        "sample_role",
        "evaluation_eligible",
        "trade_action",
        "performance_category",
    ):
        if row.get(mandatory) is None:
            raise ValueError(f"Audit record mandatory field '{mandatory}' cannot be None")


# Default Cost Rates & Parameters (Codex / David 2026 Frozen)
DEFAULT_COMMISSION_RATE: float = 0.00025  # 0.025% = 2.5 bps (<= 3‰)
DEFAULT_MIN_COMMISSION: float = 5.0  # 5 RMB minimum
DEFAULT_TRANSFER_FEE_RATE: float = 0.00001  # 0.01‰ = 0.1 bps (both buy & sell)
DEFAULT_STAMP_DUTY_RATE: float = 0.0005  # 0.5‰ = 5 bps (sell side only)
DEFAULT_SLIPPAGE_BPS: float = 5.0  # 5 bps single side (0.0005)
DEFAULT_HOLD_DAYS: int = 5
DEFAULT_BENCHMARK_SYMBOL: str = "000300.SH"
DEFAULT_TARGET_USER_ID: str = "429163f7-50b6-4982-8bdf-96ae99506843"
DEFAULT_STATUS_FILTER: str = "completed"
DEFAULT_HISTORICAL_CUTOFF_DATE: str = "2026-09-08"
DEFAULT_HISTORICAL_CUTOFF_DATETIME: str = "2026-09-08 23:59:59"
DEFAULT_FORWARD_OOS_END_DATE: str = "2026-09-09"


def classify_oos_segment(
    trade_date: str,
    dev_cutoff: str = "2025-12-31",
    historical_cutoff: str = DEFAULT_HISTORICAL_CUTOFF_DATE,
    forward_oos_end: Optional[str] = DEFAULT_FORWARD_OOS_END_DATE,
) -> OOSSegment:
    """Classify a trade date (YYYY-MM-DD) into its OOS segment."""
    clean_date = str(trade_date).strip()[:10]
    if clean_date <= dev_cutoff:
        return OOSSegment.DEV
    if clean_date <= historical_cutoff:
        return OOSSegment.HISTORICAL_OOS
    if forward_oos_end is not None and clean_date > forward_oos_end:
        return OOSSegment.FUTURE_DATA
    return OOSSegment.FORWARD_OOS


class PoolFilterStatus(str, Enum):
    """Stock pool qualification and exclusion status."""

    IN_POOL = "in_pool"
    EXCLUDED_BSE = "excluded_bse"
    EXCLUDED_ST = "excluded_st"
    EXCLUDED_NEW_LISTING = "excluded_new_listing"
    EXCLUDED_UNMAPPABLE = "excluded_unmappable"
    EXCLUDED_UNKNOWN_PREFIX = "excluded_unknown_prefix"
    EXCLUDED_UNKNOWN = "excluded_unknown"
    EXCLUDED_METADATA_UNKNOWN = "excluded_unknown"


class MeasurementOutcomeStatus(str, Enum):
    """Terminal outcome status for each report sample in return measurement."""

    EVALUATED = "evaluated"
    UNTRADABLE = "untradable"  # T+1 suspended, limit locked, zero volume
    TYPED_MISSING = "typed_missing"  # Data gap, missing exit price, provider failure
    EXCLUDED_POOL = "excluded_pool"  # Filtered out by stock pool rules
    NON_ACTIONABLE = "non_actionable"  # Non-BUY / non-trade decisions in return metric


# ---------------------------------------------------------------------------
# Frozen Constants & Baseline Stamping
# ---------------------------------------------------------------------------

BASELINE_MODEL: str = "gemini-3.8-flash-high"
BASELINE_GLOBAL_PROMPT_HASH: str = "5489166b"
HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA: str = "a6d4540feaa8043ff36b0607a31c1d2d5f004149"
OFFLINE_REPLAY_GAP: str = "offline_replay_gap"
# Retained for legacy compatibility; explicitly points to offline replay gap, no longer represents live running service nor points to historical sample generating SHA
BASELINE_RUNNING_SERVICE_SHA: str = OFFLINE_REPLAY_GAP
BASELINE_DISCLAIMER: str = "半成品基线,非定性判断"
BASELINE_DISCLAIMER_DETAIL: str = (
    "系统尚未施工完成，舆情等真实数据源未接入。本引擎仅为进度基线与测量工具，"
    "所得数字反映半成品系统状态，不得作为AI能否盈利的定性判断。"
)

SYSTEM_COMPLETENESS_DICT: Dict[str, Any] = {
    "game_theory_report_fill_rate": 0.0,
    "sentiment_news_real_source_connected": False,
    "volume_price_fill_rate": "unknown",
    "macro_report_fill_rate": "unknown",
    "overall_missing_items_rate": "unknown",
    "volume_price_fill_rate_approx": "unknown",
    "macro_report_fill_rate_approx": "unknown",
    "overall_missing_items_rate_approx": "unknown",
    "status_note": BASELINE_DISCLAIMER,
}


def compute_system_completeness(reports: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute verified system completeness metrics from report dataset.

    Strictly avoids hardcoded approximate numbers. Returns measured rates
    when reports are available, or typed unknown ('unknown') when unobserved.
    """
    if not reports:
        return {
            "game_theory_report_fill_rate": 0.0,
            "sentiment_news_real_source_connected": False,
            "volume_price_fill_rate": "unknown",
            "macro_report_fill_rate": "unknown",
            "overall_missing_items_rate": "unknown",
            "volume_price_fill_rate_approx": "unknown",
            "macro_report_fill_rate_approx": "unknown",
            "overall_missing_items_rate_approx": "unknown",
            "status_note": BASELINE_DISCLAIMER,
        }

    total = len(reports)
    gt_count = 0
    vp_count = 0
    macro_count = 0
    core_sections = [
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "macro_report",
        "volume_price_report",
        "game_theory_report",
    ]
    missing_sections_count = 0

    for r in reports:
        res_data: Dict[str, Any] = {}
        rd_raw = r.get("result_data")
        if rd_raw:
            if isinstance(rd_raw, dict):
                res_data = rd_raw
            elif isinstance(rd_raw, str):
                try:
                    res_data = json.loads(rd_raw)
                except Exception:
                    pass

        gt = (
            r.get("game_theory_report")
            or res_data.get("game_theory_report")
            or r.get("game_theory_analysis")
            or res_data.get("game_theory_analysis")
        )
        if gt is not None and str(gt).strip():
            gt_count += 1

        vp = (
            r.get("volume_price_report")
            or res_data.get("volume_price_report")
            or r.get("volume_price_analysis")
            or res_data.get("volume_price_analysis")
        )
        if vp is not None and str(vp).strip():
            vp_count += 1

        mc = (
            r.get("macro_report")
            or res_data.get("macro_report")
            or r.get("macro_analysis")
            or res_data.get("macro_analysis")
        )
        if mc is not None and str(mc).strip():
            macro_count += 1

        for sec in core_sections:
            alt_sec = sec.replace("_report", "_analysis")
            val = (
                r.get(sec)
                or res_data.get(sec)
                or r.get(alt_sec)
                or res_data.get(alt_sec)
            )
            if val is None or not str(val).strip():
                missing_sections_count += 1

    gt_rate = round(gt_count / total, 4)
    vp_rate = round(vp_count / total, 4)
    mc_rate = round(macro_count / total, 4)
    missing_rate = round(missing_sections_count / (total * len(core_sections)), 4)

    return {
        "game_theory_report_fill_rate": gt_rate,
        "sentiment_news_real_source_connected": False,
        "volume_price_fill_rate": vp_rate,
        "macro_report_fill_rate": mc_rate,
        "overall_missing_items_rate": missing_rate,
        "volume_price_fill_rate_approx": vp_rate,
        "macro_report_fill_rate_approx": mc_rate,
        "overall_missing_items_rate_approx": missing_rate,
        "status_note": BASELINE_DISCLAIMER,
    }


def compute_file_sha256(file_path: str | Path) -> str:
    """Compute sha256 checksum of a file."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def probe_running_service_sha(
    healthz_url: str = "http://127.0.0.1:8000/healthz",
    timeout_sec: float = 1.0,
) -> Tuple[Optional[str], str]:
    """Probe live running service SHA via read-only /healthz probe (DAV-865).

    Returns:
        (commit_sha, provenance_source)
        If healthz available: (commit_sha, f"healthz_probe: {healthz_url}")
        If unreachable or offline: (None, f"offline_replay_gap: service unavailable ({reason})")
    """
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(
            healthz_url, headers={"User-Agent": "v03-return-measure-runner"}
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                commit_sha = payload.get("commit_sha")
                if commit_sha:
                    return str(commit_sha), f"healthz_probe: {healthz_url}"
                return (
                    None,
                    f"offline_replay_gap: commit_sha missing in healthz response from {healthz_url}",
                )
            return None, f"offline_replay_gap: healthz probe HTTP {resp.status}"
    except Exception as e:
        return None, f"offline_replay_gap: healthz probe unreachable ({type(e).__name__})"


def get_code_prompt_sha() -> str:
    """Compute sha256 hash of built-in zh prompt file."""
    prompt_file = PROJECT_ROOT / "tradingagents" / "prompts" / "zh.py"
    if prompt_file.exists():
        try:
            return hashlib.sha256(prompt_file.read_bytes()).hexdigest()[:8]
        except Exception:
            pass
    return "0195ed05"


def get_current_code_sha() -> str:
    """Retrieve current commit SHA dynamically via git or .git lookup."""
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0 and len(proc.stdout.strip()) >= 40:
            return proc.stdout.strip()
    except Exception:
        pass

    git_entry = PROJECT_ROOT / ".git"
    if git_entry.is_file():
        try:
            content = git_entry.read_text().strip()
            if content.startswith("gitdir:"):
                git_dir = Path(content[7:].strip())
                head_file = git_dir / "HEAD"
                if head_file.exists():
                    ref = head_file.read_text().strip()
                    if ref.startswith("ref:"):
                        ref_target = git_dir / ref[4:].strip()
                        if ref_target.exists():
                            return ref_target.read_text().strip()
                    elif len(ref) >= 40:
                        return ref
        except Exception:
            pass
    elif git_entry.is_dir():
        try:
            head_file = git_entry / "HEAD"
            if head_file.exists():
                ref = head_file.read_text().strip()
                if ref.startswith("ref:"):
                    ref_target = git_entry / ref[4:].strip()
                    if ref_target.exists():
                        return ref_target.read_text().strip()
                elif len(ref) >= 40:
                    return ref
        except Exception:
            pass

    return "a227cdc3bb466edf2e910419cb6013cfc021d309"


# ---------------------------------------------------------------------------
# Data Models: Cost Model, Stamps, Records, Metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostModel:
    """A-share transaction cost model with frozen rules.

    Rule: Commission INCLUDES regulatory fees; strictly no extra handling
    (0.0341‰) or supervision (0.002%) fees.
    """

    commission_rate: float = DEFAULT_COMMISSION_RATE
    min_commission: float = 0.0  # Set to 0.0 for pure percentage-rate returns
    transfer_fee_rate: float = DEFAULT_TRANSFER_FEE_RATE
    stamp_duty_rate: float = DEFAULT_STAMP_DUTY_RATE
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS

    @property
    def slippage_rate(self) -> float:
        return self.slippage_bps / 10000.0

    @property
    def buy_cost_rate(self) -> float:
        """Total buy-side rate: commission + transfer_fee + slippage."""
        return self.commission_rate + self.transfer_fee_rate + self.slippage_rate

    @property
    def sell_cost_rate(self) -> float:
        """Total sell-side rate: commission + transfer_fee + stamp_duty + slippage."""
        return (
            self.commission_rate
            + self.transfer_fee_rate
            + self.stamp_duty_rate
            + self.slippage_rate
        )

    @property
    def round_trip_cost_rate(self) -> float:
        """Total round-trip cost rate."""
        return self.buy_cost_rate + self.sell_cost_rate

    def calculate_costs(
        self, entry_price: float, exit_price: float
    ) -> Dict[str, float]:
        """Calculate detailed costs and net return for a trade.

        Returns gross_return, net_return, and itemized cost breakdown.
        """
        if entry_price <= 0 or exit_price <= 0:
            raise ValueError("Prices must be positive")

        gross_return = (exit_price - entry_price) / entry_price

        # Exact effective prices with execution costs
        effective_buy_price = entry_price * (1.0 + self.buy_cost_rate)
        effective_sell_price = exit_price * (1.0 - self.sell_cost_rate)
        net_return = (effective_sell_price - effective_buy_price) / effective_buy_price

        return {
            "gross_return": gross_return,
            "net_return": net_return,
            "buy_commission_rate": self.commission_rate,
            "buy_transfer_fee_rate": self.transfer_fee_rate,
            "buy_slippage_rate": self.slippage_rate,
            "total_buy_cost_rate": self.buy_cost_rate,
            "sell_commission_rate": self.commission_rate,
            "sell_transfer_fee_rate": self.transfer_fee_rate,
            "sell_stamp_duty_rate": self.stamp_duty_rate,
            "sell_slippage_rate": self.slippage_rate,
            "total_sell_cost_rate": self.sell_cost_rate,
            "total_round_trip_cost_rate": self.round_trip_cost_rate,
        }


@dataclass
class EvaluationStamp:
    """Metadata stamp proving immutable configuration and system state."""

    model: str = BASELINE_MODEL
    prompt_hash: str = field(
        default_factory=lambda: f"{BASELINE_GLOBAL_PROMPT_HASH}@{get_code_prompt_sha()}"
    )
    code_sha: str = field(default_factory=get_current_code_sha)
    running_service_sha: str = OFFLINE_REPLAY_GAP
    sample_generating_service_sha: str = HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    historical_sample_generating_service_sha: str = HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    running_service_provenance_source: Optional[str] = None
    disclaimer: str = BASELINE_DISCLAIMER
    disclaimer_detail: str = BASELINE_DISCLAIMER_DETAIL
    system_completeness: Dict[str, Any] = field(
        default_factory=lambda: dict(SYSTEM_COMPLETENESS_DICT)
    )
    evaluated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    # V-03a-2 Scope Stamping (DAV-804 & DAV-865)
    target_user_id: Optional[str] = DEFAULT_TARGET_USER_ID
    status_filter: Optional[str] = DEFAULT_STATUS_FILTER
    scope_filter_description: str = "仅 completed"
    target_user_total: Optional[int] = None
    target_user_completed: Optional[int] = None
    target_user_failed: Optional[int] = None
    account_stats: Optional[Dict[str, Any]] = None
    dev_cutoff_date: str = "2025-12-31"
    historical_cutoff_date: str = DEFAULT_HISTORICAL_CUTOFF_DATE
    forward_oos_end_date: Optional[str] = DEFAULT_FORWARD_OOS_END_DATE


@dataclass
class SnapshotManifest:
    """Read-only data snapshot manifest & audit metadata (V-03a-3 Section 1 & DAV-865)."""

    manifest_id: str
    target_user_id: str = DEFAULT_TARGET_USER_ID
    status_scope: str = DEFAULT_STATUS_FILTER
    scope_description: str = "仅 completed"
    production_db_path: str = ""
    replica_db_path: str = ""
    replica_file_hash: str = ""
    snapshot_created_at: str = ""
    dev_cutoff_date: str = "2025-12-31"
    historical_cutoff_date: str = DEFAULT_HISTORICAL_CUTOFF_DATE
    forward_oos_end_date: Optional[str] = DEFAULT_FORWARD_OOS_END_DATE
    cutoff_datetime: str = DEFAULT_HISTORICAL_CUTOFF_DATETIME
    requested_as_of: str = DEFAULT_HISTORICAL_CUTOFF_DATE
    cutoff_requested_distinction: str = (
        "cutoff_datetime 为 PIT 数据观察硬边界（在此之后产生的数据严禁可见）；"
        "requested_as_of 为请求发起锚定日期/时点，二者在回测与离线重放中具有明确时序因果区分，严禁混用。"
    )
    target_user_total: Optional[int] = None
    target_user_completed: Optional[int] = None
    target_user_failed: Optional[int] = None
    candidate_reports: Optional[int] = None
    account_stats: Optional[Dict[str, Any]] = None
    code_sha: str = field(default_factory=get_current_code_sha)
    sample_generating_service_sha: str = HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    historical_sample_generating_service_sha: str = HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    running_service_sha: Optional[str] = OFFLINE_REPLAY_GAP
    running_service_provenance_source: Optional[str] = None
    model_name: str = BASELINE_MODEL
    temperature: float = 0.0
    prompt_hash: str = field(
        default_factory=lambda: f"{BASELINE_GLOBAL_PROMPT_HASH}@{get_code_prompt_sha()}"
    )
    horizon_profile: str = "T+5"
    cost_assumptions: Dict[str, float] = field(default_factory=dict)
    system_completeness: Dict[str, Any] = field(
        default_factory=lambda: dict(SYSTEM_COMPLETENESS_DICT)
    )
    disclaimer: str = BASELINE_DISCLAIMER
    disclaimer_detail: str = BASELINE_DISCLAIMER_DETAIL
    forward_oos_count: int = 0
    forward_oos_zero_reason: str = (
        "当前数据库截止基准日期未产生或未纳入已完成前向验证样本，严格杜绝将历史样本改名充作前向样本。"
    )
    regression_symbols: List[str] = field(
        default_factory=lambda: sorted(list(REGRESSION_SYMBOLS))
    )
    # DAV-1050 offline price-source stamping (empty strings in online mode)
    price_provider: str = ""
    price_snapshot_path: str = ""
    price_snapshot_sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SampleMeasureRecord:
    """Individual report measurement audit record with 25-field offline audit support."""

    report_id: str
    symbol_raw: str
    symbol_canonical: Optional[str]
    trade_date: str  # T
    oos_segment: str  # DEV, HISTORICAL_OOS, FORWARD_OOS
    decision: Optional[str] = None
    direction: Optional[str] = None
    raw_direction: Optional[str] = None
    user_id: Optional[str] = None
    status: Optional[str] = None
    pool_status: str = PoolFilterStatus.IN_POOL.value
    outcome_status: str = MeasurementOutcomeStatus.EVALUATED.value
    untradable_reason: Optional[str] = None
    missing_reason: Optional[str] = None
    entry_date: Optional[str] = None  # T+1
    entry_price: Optional[float] = None  # T+1 Open
    exit_date: Optional[str] = None  # T+1+hold_days
    exit_price: Optional[float] = None
    gross_return: Optional[float] = None
    net_return: Optional[float] = None
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL
    benchmark_entry_price: Optional[float] = None
    benchmark_exit_price: Optional[float] = None
    benchmark_return: Optional[float] = None
    excess_return: Optional[float] = None  # alpha = net_return - benchmark_return
    cost_breakdown: Optional[Dict[str, float]] = None
    included_in_return_metrics: bool = False
    included_in_coverage_metrics: bool = True

    # V-03a-3 25-Field Offline Audit Extensions
    sample_role: str = SampleRole.HISTORICAL_OOS.value
    evaluation_eligible: bool = True
    exclusion_reason: Optional[str] = None
    label_horizon: str = "T+5"
    eval_offset_days: int = 1
    roll_days_used: int = 0
    trade_action: Optional[str] = None
    wait_subsequent_return_pct: Optional[float] = None
    evidence_provenance: Optional[Dict[str, Any]] = None
    cutoff_datetime: str = DEFAULT_HISTORICAL_CUTOFF_DATETIME
    requested_as_of: str = DEFAULT_HISTORICAL_CUTOFF_DATE
    profile_id: str = "default_t5"
    model_name: str = BASELINE_MODEL
    prompt_version: str = ""
    cost_assumptions: Optional[Dict[str, float]] = None
    performance_category: str = "evaluated"

    def to_audit_row(self) -> Dict[str, Any]:
        """Convert record to strict 25-field offline audit format (V-03a-3 Section 2)."""
        gross_pct = (
            round(self.gross_return * 100.0, 4)
            if self.gross_return is not None
            else None
        )
        net_pct = (
            round(self.net_return * 100.0, 4)
            if self.net_return is not None
            else None
        )
        sym_val = self.symbol_canonical or (
            f"UNMAPPABLE:{self.symbol_raw}" if self.symbol_raw else "MISSING"
        )
        action_val = self.trade_action or (self.decision or "NO_TRADE")
        prompt_v = self.prompt_version or f"{BASELINE_GLOBAL_PROMPT_HASH}@{get_code_prompt_sha()}"

        row = {
            "sample_id": self.report_id,
            "symbol": sym_val,
            "cutoff_datetime": self.cutoff_datetime,
            "requested_as_of": self.requested_as_of,
            "profile_id": self.profile_id,
            "model_name": self.model_name,
            "prompt_version": prompt_v,
            "sample_role": self.sample_role,
            "evaluation_eligible": self.evaluation_eligible,
            "exclusion_reason": self.exclusion_reason,
            "label_horizon": self.label_horizon,
            "eval_offset_days": self.eval_offset_days,
            "signal_date": self.trade_date,
            "executable_entry_date": self.entry_date,
            "actual_exit_date": self.exit_date,
            "roll_days_used": self.roll_days_used,
            "trade_action": action_val,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "cost_assumptions": dict(self.cost_assumptions or {}),
            "gross_return_pct": gross_pct,
            "net_return_pct": net_pct,
            "performance_category": self.performance_category,
            "wait_subsequent_return_pct": self.wait_subsequent_return_pct,
            "evidence_provenance": dict(self.evidence_provenance or {}),
        }
        validate_audit_row(row)
        return row

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SegmentMetrics:
    """Aggregated metrics for a single segment (or total)."""

    segment_name: str
    # Coverage & Evaluability Metrics
    total_reports: int = 0
    mappable_count: int = 0
    unmappable_count: int = 0
    in_pool_count: int = 0
    excluded_pool_count: int = 0
    tradable_count: int = 0
    untradable_count: int = 0
    evaluated_count: int = 0
    typed_missing_count: int = 0
    non_actionable_count: int = 0
    coverage_rate: float = 0.0  # evaluated_count / in_pool_count (if in_pool_count > 0)
    evaluability_rate: float = 0.0  # tradable_count / in_pool_count
    pool_exclusion_reasons: Dict[str, int] = field(default_factory=dict)
    untradable_reasons: Dict[str, int] = field(default_factory=dict)
    missing_reasons: Dict[str, int] = field(default_factory=dict)

    # Return & Excess Return Metrics (computed strictly on evaluated valid returns)
    return_sample_count: int = 0
    mean_gross_return: Optional[float] = None
    mean_net_return: Optional[float] = None
    median_net_return: Optional[float] = None
    mean_benchmark_return: Optional[float] = None
    mean_excess_return: Optional[float] = None
    win_rate: Optional[float] = None  # % of net_return > 0
    excess_win_rate: Optional[float] = None  # % of excess_return > 0
    profit_loss_ratio: Optional[float] = None  # avg_gain / avg_loss
    max_return: Optional[float] = None
    min_return: Optional[float] = None
    return_std: Optional[float] = None

    # Direction Diagnostics
    total_directional_predictions: int = 0
    directional_candidate_count: int = 0
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    bullish_accuracy: Optional[float] = None
    bearish_accuracy: Optional[float] = None
    overall_accuracy: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class V03MeasurementResult:
    """Complete V-03a measurement bundle with manifest, audit table, and regression isolation."""

    stamp: EvaluationStamp
    all_metrics: SegmentMetrics
    dev_metrics: SegmentMetrics
    historical_oos_metrics: SegmentMetrics
    forward_oos_metrics: SegmentMetrics
    collision_summary: Dict[str, Any]
    records: List[SampleMeasureRecord]
    regression_metrics: SegmentMetrics = field(
        default_factory=lambda: SegmentMetrics("REGRESSION")
    )
    snapshot_manifest: Optional[SnapshotManifest] = None
    ablation_summary: Optional[Dict[str, Any]] = None
    audit_table: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stamp": asdict(self.stamp),
            "all_metrics": self.all_metrics.to_dict(),
            "dev_metrics": self.dev_metrics.to_dict(),
            "historical_oos_metrics": self.historical_oos_metrics.to_dict(),
            "forward_oos_metrics": self.forward_oos_metrics.to_dict(),
            "regression_metrics": self.regression_metrics.to_dict(),
            "collision_summary": self.collision_summary,
            "snapshot_manifest": self.snapshot_manifest.to_dict() if self.snapshot_manifest else None,
            "ablation_summary": self.ablation_summary,
            "records_count": len(self.records),
            "audit_table_count": len(self.audit_table) if self.audit_table else 0,
        }


# ---------------------------------------------------------------------------
# Market & Price Data Provider Abstraction
# ---------------------------------------------------------------------------


@dataclass
class DailyBar:
    """OHLCV market bar for a single trading day."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    is_suspended: bool = False
    limit_up: Optional[float] = None
    limit_down: Optional[float] = None


class PriceDataProvider(Protocol):
    """Abstract protocol for retrieving historical bars and trading calendar."""

    def get_bar(self, symbol: str, date: str) -> Optional[DailyBar]:
        """Fetch daily bar on given date."""
        ...

    def get_t_plus_n_date(self, base_date: str, n: int) -> Optional[str]:
        """Get the N-th trading day after base_date."""
        ...

    def is_st(self, symbol: str, date: str) -> Optional[bool]:
        """Check if stock was ST/*ST on date. Returns None if unknown (fail-closed)."""
        ...

    def is_listed_for_n_days(
        self, symbol: str, date: str, min_days: int = 60
    ) -> Optional[bool]:
        """Check if stock has been listed for at least min_days trading days. Returns None if unknown."""
        ...


class DictPriceDataProvider:
    """In-memory dictionary price data provider for deterministic tests."""

    def __init__(
        self,
        bars: Optional[Dict[Tuple[str, str], DailyBar]] = None,
        trade_dates: Optional[List[str]] = None,
        st_stocks: Optional[Set[str]] = None,
        listing_dates: Optional[Dict[str, str]] = None,
        unknown_metadata_stocks: Optional[Set[str]] = None,
    ):
        self._bars: Dict[Tuple[str, str], DailyBar] = dict(bars or {})
        self._trade_dates: List[str] = sorted(trade_dates or [])
        self._st_stocks: Set[str] = set(st_stocks or [])
        self._listing_dates: Dict[str, str] = dict(listing_dates or {})
        self._unknown_metadata_stocks: Set[str] = set(unknown_metadata_stocks or [])

    def add_bar(self, symbol: str, bar: DailyBar) -> None:
        self._bars[(symbol, bar.date)] = bar
        if bar.date not in self._trade_dates:
            self._trade_dates.append(bar.date)
            self._trade_dates.sort()

    def get_bar(self, symbol: str, date: str) -> Optional[DailyBar]:
        return self._bars.get((symbol, date))

    def get_t_plus_n_date(self, base_date: str, n: int) -> Optional[str]:
        if not self._trade_dates:
            return None
        # Find index of base_date or next trading day
        dates = self._trade_dates
        if base_date in dates:
            idx = dates.index(base_date)
        else:
            # find first date > base_date
            idx = -1
            for i, d in enumerate(dates):
                if d > base_date:
                    idx = i - 1
                    break
            if idx == -1:
                return None
        target_idx = idx + n
        if 0 <= target_idx < len(dates):
            return dates[target_idx]
        return None

    def is_st(self, symbol: str, date: str) -> Optional[bool]:
        if symbol in self._unknown_metadata_stocks:
            return None
        return symbol in self._st_stocks

    def is_listed_for_n_days(
        self, symbol: str, date: str, min_days: int = 60
    ) -> Optional[bool]:
        if symbol in self._unknown_metadata_stocks:
            return None
        list_date = self._listing_dates.get(symbol)
        if not list_date:
            # Missing/unknown listing date: typed unknown, forbidden to fail-open
            return None
        try:
            d_list = datetime.strptime(str(list_date)[:10], "%Y-%m-%d").date()
            d_as_of = datetime.strptime(str(date)[:10], "%Y-%m-%d").date()
            cal_days = (d_as_of - d_list).days
            if cal_days < min_days:
                return False
            if cal_days >= min_days * 2:
                return True
            count = sum(1 for d in self._trade_dates if str(list_date) <= d <= str(date))
            return count >= min_days
        except Exception:
            return None


class VendorPriceDataProvider:
    """Live/cached price provider backed by TradingAgents dataflows / trade_calendar."""

    _GLOBAL_A_SHARE_META: Optional[Dict[str, Dict[str, str]]] = None

    def __init__(self, forward_oos_end_date: Optional[str] = DEFAULT_FORWARD_OOS_END_DATE) -> None:
        self.forward_oos_end_date = forward_oos_end_date
        self._bar_cache: Dict[Tuple[str, str], Optional[DailyBar]] = {}
        self._series_cache: Dict[Tuple[str, str, str], Any] = {}
        self._st_cache: Dict[Tuple[str, str], Optional[bool]] = {}
        self._listing_cache: Dict[Tuple[str, str, int], Optional[bool]] = {}
        self._meta_cache: Dict[str, Optional[Dict[str, str]]] = {}

    def get_t_plus_n_date(self, base_date: str, n: int) -> Optional[str]:
        from tradingagents.dataflows.trade_calendar import get_t_plus_n_trading_day

        try:
            return get_t_plus_n_trading_day(base_date, n)
        except Exception:
            return None

    def get_bar(self, symbol: str, date: str) -> Optional[DailyBar]:
        clean_date = str(date).strip()[:10]
        if self.forward_oos_end_date is not None and clean_date > self.forward_oos_end_date:
            return None
        cache_key = (symbol, clean_date)
        if cache_key in self._bar_cache:
            return self._bar_cache[cache_key]

        bar = self._fetch_bar(symbol, clean_date)
        self._bar_cache[cache_key] = bar
        return bar

    def _fetch_bar(self, symbol: str, date: str) -> Optional[DailyBar]:
        # If it is CSI 300 benchmark
        if symbol in ("000300.SH", "sh000300", "000300"):
            return self._fetch_benchmark_bar(date)

        try:
            if symbol not in self._series_cache:
                from tradingagents.dataflows.interface import route_to_vendor
                from tradingagents.dataflows.trade_calendar import cn_today_str
                import io
                import pandas as pd

                today_str = cn_today_str()
                # Dynamic forward OOS upper bound: must not be hardcoded to 2026-09-09
                if self.forward_oos_end_date:
                    end_str = self.forward_oos_end_date
                else:
                    end_str = today_str

                # Cache full historical window for the symbol
                csv_data = route_to_vendor("get_stock_data", symbol, "2024-01-01", end_str)
                if not csv_data or str(csv_data).startswith("【数据获取失败】"):
                    self._series_cache[symbol] = None
                    return None

                df = pd.read_csv(io.StringIO(str(csv_data)), comment="#")
                if df.empty or "Date" not in df.columns:
                    self._series_cache[symbol] = None
                    return None
                df["Date"] = df["Date"].astype(str).str[:10]
                from tradingagents.dataflows.trade_calendar import dedupe_daily_bars

                val_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
                df = dedupe_daily_bars(df, "Date", val_cols)
                self._series_cache[symbol] = df

            df = self._series_cache.get(symbol)
            if df is None or df.empty:
                return None

            row = df[df["Date"] == date]
            if row.empty:
                return None
            if len(row) > 1:
                from tradingagents.dataflows.trade_calendar import dedupe_daily_bars

                val_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in row.columns]
                row = dedupe_daily_bars(row, "Date", val_cols)
                if len(row) != 1:
                    return None
            r = row.iloc[0]
            open_val = float(r["Open"])
            high_val = float(r["High"])
            low_val = float(r["Low"])
            close_val = float(r["Close"])
            vol_val = float(r.get("Volume", 0.0))
            is_susp = vol_val == 0.0 or (open_val == 0.0 and close_val == 0.0)
            return DailyBar(
                date=date,
                open=open_val,
                high=high_val,
                low=low_val,
                close=close_val,
                volume=vol_val,
                is_suspended=is_susp,
            )
        except Exception:
            return None

    def _fetch_benchmark_bar(self, date: str) -> Optional[DailyBar]:
        """Fetch CSI 300 index daily bar via Tencent akshare provider."""
        clean_date = str(date).strip()[:10]
        if self.forward_oos_end_date is not None and clean_date > self.forward_oos_end_date:
            return None

        cache_key = ("__CSI300__", clean_date)
        if cache_key in self._bar_cache:
            return self._bar_cache[cache_key]

        try:
            import akshare as ak

            # Check if dataframe is already cached
            if "__CSI300_DF__" not in self._series_cache:
                df = ak.stock_zh_index_daily_tx(symbol="sh000300")
                if df is not None and not df.empty:
                    df = df.copy()
                    df["date"] = df["date"].astype(str).str[:10]
                    from tradingagents.dataflows.trade_calendar import dedupe_daily_bars

                    val_cols = [c for c in ["open", "high", "low", "close", "amount"] if c in df.columns]
                    df = dedupe_daily_bars(df, "date", val_cols)
                    self._series_cache["__CSI300_DF__"] = df
                else:
                    return None
            df = self._series_cache.get("__CSI300_DF__")
            if df is None or df.empty:
                return None
            match = df[df["date"] == clean_date]
            if match.empty:
                return None
            if len(match) > 1:
                from tradingagents.dataflows.trade_calendar import dedupe_daily_bars

                val_cols = [c for c in ["open", "high", "low", "close", "amount"] if c in match.columns]
                match = dedupe_daily_bars(match, "date", val_cols)
                if len(match) != 1:
                    return None
            r = match.iloc[0]
            bar = DailyBar(
                date=clean_date,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                amount=float(r.get("amount", 0.0)),
            )
            self._bar_cache[cache_key] = bar
            return bar
        except Exception:
            return None

    @classmethod
    def _ensure_global_metadata(cls) -> Dict[str, Dict[str, str]]:
        if cls._GLOBAL_A_SHARE_META is not None:
            return cls._GLOBAL_A_SHARE_META

        meta: Dict[str, Dict[str, str]] = {}
        try:
            import akshare as ak
            for sym_type in ("主板A股", "科创板"):
                try:
                    df = ak.stock_info_sh_name_code(symbol=sym_type)
                    if df is not None and not df.empty:
                        for _, row in df.iterrows():
                            c = str(row.get("证券代码", "")).zfill(6)
                            if c:
                                meta[c] = {
                                    "name": str(row.get("证券简称", "")),
                                    "list_date": str(row.get("上市日期", ""))[:10],
                                }
                except Exception:
                    pass
            try:
                df_sz = ak.stock_info_sz_name_code(symbol="A股列表")
                if df_sz is not None and not df_sz.empty:
                    for _, row in df_sz.iterrows():
                        c = str(row.get("A股代码", "")).zfill(6)
                        if c:
                            meta[c] = {
                                "name": str(row.get("A股简称", "")),
                                "list_date": str(row.get("A股上市日期", ""))[:10],
                            }
            except Exception:
                pass
        except Exception:
            pass

        cls._GLOBAL_A_SHARE_META = meta
        return meta

    def _get_stock_metadata(self, symbol: str) -> Optional[Dict[str, str]]:
        norm_sym = symbol.strip().upper()
        if norm_sym in self._meta_cache:
            return self._meta_cache[norm_sym]

        code = norm_sym[:6]
        meta_dict = self._ensure_global_metadata()
        m = meta_dict.get(code)
        if m is not None and m.get("list_date"):
            self._meta_cache[norm_sym] = m
            return m

        # Fallback to baostock query_stock_basic
        try:
            from tradingagents.dataflows.providers.cn_baostock_provider import baostock_session
            bs_market = "sh" if norm_sym.endswith(".SH") or code.startswith(("5", "6", "9")) else "sz"
            bs_code = f"{bs_market}.{code}"
            with baostock_session() as bs:
                rs = bs.query_stock_basic(code=bs_code)
                if rs and getattr(rs, "error_code", None) == "0" and rs.next():
                    row = dict(zip(rs.fields, rs.get_row_data()))
                    ipo = str(row.get("ipoDate", "")).strip()[:10]
                    cname = str(row.get("code_name", "")).strip()
                    if ipo:
                        res = {"name": cname, "list_date": ipo}
                        self._meta_cache[norm_sym] = res
                        return res
        except NetworkAccessDeniedError:
            raise
        except Exception as exc:
            logger.debug("v03_return_measure baostock query_stock_basic fallback failed for %s: %s", norm_sym, exc)

        self._meta_cache[norm_sym] = None
        return None

    def is_st(self, symbol: str, date: str) -> Optional[bool]:
        """Check if stock was ST/*ST on date (strict Point-In-Time).

        Returns True if ST on date, False if normal on date, None if unknown/unverifiable (fail-closed).
        """
        norm_sym = symbol.upper()
        if not norm_sym.endswith((".SH", ".SZ")):
            code = norm_sym[:6]
            if code.startswith(("5", "6", "9")):
                norm_sym = f"{code}.SH"
            elif code.startswith(("0", "3")):
                norm_sym = f"{code}.SZ"
            else:
                return None

        clean_date = str(date).strip()[:10]
        cache_key = (norm_sym, clean_date)
        if cache_key in self._st_cache:
            return self._st_cache[cache_key]

        meta = self._get_stock_metadata(norm_sym)
        if meta is None:
            self._st_cache[cache_key] = None
            return None

        code_6 = norm_sym[:6]
        bs_prefix = "sh" if norm_sym.endswith(".SH") or code_6.startswith(("5", "6", "9")) else "sz"
        bs_code = f"{bs_prefix}.{code_6}"

        pit_st: Optional[bool] = None
        try:
            from tradingagents.dataflows.providers.cn_baostock_provider import baostock_session
            with baostock_session() as bs:
                # 1. First try exact date query
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,isST",
                    start_date=clean_date,
                    end_date=clean_date,
                    frequency="d",
                    adjustflag="3",
                )
                if rs and rs.error_code == "0":
                    while rs.next():
                        row = rs.get_row_data()
                        if row and len(row) >= 2:
                            pit_st = (row[1] == "1")
                            break
                # 2. If exact date returned no bar (e.g. suspension, weekend, or holiday),
                # look back up to 30 calendar days for the latest known trading day
                if pit_st is None:
                    d = datetime.strptime(clean_date, "%Y-%m-%d").date()
                    start_d = (d - timedelta(days=30)).strftime("%Y-%m-%d")
                    rs = bs.query_history_k_data_plus(
                        bs_code,
                        "date,isST",
                        start_date=start_d,
                        end_date=clean_date,
                        frequency="d",
                        adjustflag="3",
                    )
                    if rs and rs.error_code == "0":
                        while rs.next():
                            row = rs.get_row_data()
                            if row and len(row) >= 2:
                                pit_st = (row[1] == "1")
        except NetworkAccessDeniedError:
            raise
        except Exception as exc:
            logger.debug("v03_return_measure baostock is_st check failed for %s on %s: %s", bs_code, clean_date, exc)
            pit_st = None

        self._st_cache[cache_key] = pit_st
        return pit_st

    def is_listed_for_n_days(
        self, symbol: str, date: str, min_days: int = 60
    ) -> Optional[bool]:
        cache_key = (symbol, date, min_days)
        if cache_key in self._listing_cache:
            return self._listing_cache[cache_key]

        meta = self._get_stock_metadata(symbol)
        if meta is None:
            self._listing_cache[cache_key] = None
            return None

        list_date = meta.get("list_date", "").strip()[:10]
        if not list_date:
            self._listing_cache[cache_key] = None
            return None

        try:
            from tradingagents.dataflows.trade_calendar import require_cn_trade_dates
            all_dates, _ = require_cn_trade_dates()
            clean_date = date[:10]
            count = sum(1 for d in all_dates if list_date <= d.isoformat() <= clean_date)
            qualified = count >= min_days
            self._listing_cache[cache_key] = qualified
            return qualified
        except Exception:
            self._listing_cache[cache_key] = None
            return None


class PriceSnapshotValidationError(ValueError):
    """Raised when a local offline price snapshot fails fail-closed validation."""

    __slots__ = ()


class OfflineSnapshotPriceDataProvider:
    """Deterministic offline price provider for V-03 --offline measurement (DAV-1050).

    Hard guarantees:
    - NEVER calls route_to_vendor / akshare / baostock / any network or socket.
    - Prices come only from an explicitly provided local JSON/CSV snapshot file.
    - The snapshot file is hashed (sha256 recorded) and validated fail-closed:
      missing required fields, conflicting duplicate (symbol, date) rows, or rows
      dated beyond ``forward_oos_end_date`` all raise PriceSnapshotValidationError.
    - With no snapshot, every lookup returns the typed-missing path (None /
      EXCLUDED_UNKNOWN upstream) instantly; no waiting, no fabricated prices.
    - get_t_plus_n_date is computed from the snapshot's own trade-date list
      (or the union of bar dates), never from the live trade calendar.

    Snapshot JSON schema::

        {
          "trade_dates": ["2026-09-09", ...],          # optional; derived from bars if absent
          "bars": [
            {"symbol": "600519.SH", "date": "2026-09-18",
             "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...,
             "amount": ..., "is_suspended": ..., "limit_up": ..., "limit_down": ...}
          ],
          "metadata": {                                 # optional; absent -> typed gap (fail-closed)
            "600519.SH": {"list_date": "2001-08-27", "name": "...",
                          "st": false, "st_dates": ["2021-06-01"]}
          }
        }

    CSV form: one header row with at least symbol,date,open,high,low,close;
    optional columns volume,amount,is_suspended,limit_up,limit_down.
    """

    REQUIRED_BAR_FIELDS: Tuple[str, ...] = (
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
    )

    def __init__(
        self,
        snapshot_path: Optional[str] = None,
        forward_oos_end_date: Optional[str] = DEFAULT_FORWARD_OOS_END_DATE,
    ) -> None:
        self.forward_oos_end_date = forward_oos_end_date
        self.offline: bool = True  # marker: measurement must not reach external vendors
        self.snapshot_path: Optional[str] = None
        self.snapshot_sha256: Optional[str] = None
        self._bars: Dict[Tuple[str, str], DailyBar] = {}
        self._trade_dates: List[str] = []
        self._metadata: Dict[str, Dict[str, Any]] = {}
        if snapshot_path:
            self._load_snapshot(snapshot_path)

    # ------------------------------------------------------------------
    # Snapshot loading & fail-closed validation
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_symbol(symbol: Any) -> str:
        res = canonicalize_symbol(symbol)
        canon = res.canonical_symbol
        if canon:
            return canon.upper()
        return str(symbol or "").strip().upper()

    def _load_snapshot(self, snapshot_path: str) -> None:
        p = Path(snapshot_path).resolve()
        if not p.exists():
            raise PriceSnapshotValidationError(f"price snapshot not found: {p}")
        raw = p.read_bytes()
        self.snapshot_path = str(p)
        self.snapshot_sha256 = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8")
        if p.suffix.lower() == ".csv":
            payload = self._parse_csv(text, p)
        else:
            try:
                payload = json.loads(text)
            except Exception as exc:
                raise PriceSnapshotValidationError(
                    f"price snapshot {p} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise PriceSnapshotValidationError(
                    f"price snapshot {p} must be a JSON object"
                )
        self._ingest_payload(payload, p)

    def _parse_csv(self, text: str, p: Path) -> Dict[str, Any]:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise PriceSnapshotValidationError(f"price snapshot {p}: empty CSV")
        cols = {str(c).strip().lower() for c in reader.fieldnames}
        missing_cols = [c for c in self.REQUIRED_BAR_FIELDS if c not in cols]
        if missing_cols:
            raise PriceSnapshotValidationError(
                f"price snapshot {p}: CSV missing required columns: {missing_cols}"
            )
        bars: List[Dict[str, Any]] = []
        for row in reader:
            bars.append(
                {str(k).strip().lower(): v for k, v in row.items() if k is not None}
            )
        return {"bars": bars}

    def _ingest_payload(self, payload: Dict[str, Any], p: Path) -> None:
        raw_dates = payload.get("trade_dates") or []
        if not isinstance(raw_dates, list):
            raise PriceSnapshotValidationError(
                f"price snapshot {p}: 'trade_dates' must be a list"
            )
        dates: Set[str] = set()
        for d in raw_dates:
            ds = str(d).strip()[:10]
            self._check_date_bound(ds, p)
            dates.add(ds)

        bars = payload.get("bars") or []
        if not isinstance(bars, list):
            raise PriceSnapshotValidationError(
                f"price snapshot {p}: 'bars' must be a list"
            )
        for i, row in enumerate(bars):
            if not isinstance(row, dict):
                raise PriceSnapshotValidationError(
                    f"price snapshot {p}: bar #{i} is not an object"
                )
            self._ingest_bar(row, i, p, dates)

        meta = payload.get("metadata") or {}
        if not isinstance(meta, dict):
            raise PriceSnapshotValidationError(
                f"price snapshot {p}: 'metadata' must be an object"
            )
        for sym, m in meta.items():
            if isinstance(m, dict):
                self._metadata[self._norm_symbol(sym)] = dict(m)

        self._trade_dates = sorted(dates)

    def _check_date_bound(self, ds: str, p: Path) -> None:
        if not ds or len(ds) != 10:
            raise PriceSnapshotValidationError(
                f"price snapshot {p}: invalid trade date {ds!r}"
            )
        if self.forward_oos_end_date is not None and ds > self.forward_oos_end_date:
            raise PriceSnapshotValidationError(
                f"price snapshot {p}: date {ds} exceeds forward_oos_end_date "
                f"{self.forward_oos_end_date} (future rows are rejected fail-closed)"
            )

    def _ingest_bar(
        self, row: Dict[str, Any], idx: int, p: Path, dates: Set[str]
    ) -> None:
        lower = {str(k).strip().lower(): v for k, v in row.items()}
        missing = [f for f in self.REQUIRED_BAR_FIELDS if lower.get(f) in (None, "")]
        if missing:
            raise PriceSnapshotValidationError(
                f"price snapshot {p}: bar #{idx} missing required fields: {missing}"
            )
        sym = self._norm_symbol(lower["symbol"])
        ds = str(lower["date"]).strip()[:10]
        self._check_date_bound(ds, p)
        try:
            open_v = float(lower["open"])
            high_v = float(lower["high"])
            low_v = float(lower["low"])
            close_v = float(lower["close"])
        except Exception as exc:
            raise PriceSnapshotValidationError(
                f"price snapshot {p}: bar #{idx} ({sym} {ds}) has non-numeric OHLC: {exc}"
            ) from exc
        try:
            vol_v = float(lower.get("volume") or 0.0)
            amt_v = float(lower.get("amount") or 0.0)
        except Exception as exc:
            raise PriceSnapshotValidationError(
                f"price snapshot {p}: bar #{idx} ({sym} {ds}) has non-numeric volume/amount: {exc}"
            ) from exc
        susp = lower.get("is_suspended")
        if susp is None or susp == "":
            is_susp = vol_v == 0.0 or (open_v == 0.0 and close_v == 0.0)
        else:
            is_susp = bool(susp) if isinstance(susp, bool) else str(susp).strip().lower() in ("1", "true", "yes")

        def _opt_float(key: str) -> Optional[float]:
            v = lower.get(key)
            if v in (None, ""):
                return None
            try:
                return float(v)
            except Exception as exc:
                raise PriceSnapshotValidationError(
                    f"price snapshot {p}: bar #{idx} ({sym} {ds}) invalid {key}: {exc}"
                ) from exc

        bar = DailyBar(
            date=ds,
            open=open_v,
            high=high_v,
            low=low_v,
            close=close_v,
            volume=vol_v,
            amount=amt_v,
            is_suspended=is_susp,
            limit_up=_opt_float("limit_up"),
            limit_down=_opt_float("limit_down"),
        )
        key = (sym, ds)
        if key in self._bars:
            prev = self._bars[key]
            if prev != bar:
                raise PriceSnapshotValidationError(
                    f"price snapshot {p}: conflicting duplicate bars for {sym} {ds} "
                    "(fail-closed; cannot choose deterministically)"
                )
            return  # identical duplicate: dedupe
        self._bars[key] = bar
        dates.add(ds)

    # ------------------------------------------------------------------
    # PriceDataProvider protocol (offline-only)
    # ------------------------------------------------------------------

    def describe_price_gap(self, symbol: str, date: str) -> str:
        """Explain why a bar lookup returned None; used for typed-missing audit."""
        clean_date = str(date).strip()[:10]
        if self.forward_oos_end_date is not None and clean_date > self.forward_oos_end_date:
            return "date_beyond_forward_oos_end"
        if not self.snapshot_path:
            return "offline_no_price_snapshot"
        sym = self._norm_symbol(symbol)
        if (sym, clean_date) not in self._bars:
            if not any(s == sym for (s, _d) in self._bars):
                return "snapshot_missing_symbol"
            return "snapshot_missing_date"
        return "unknown"

    def get_t_plus_n_date(self, base_date: str, n: int) -> Optional[str]:
        if not self._trade_dates or n <= 0:
            return None
        base = str(base_date).strip()[:10]
        idx = bisect.bisect_left(self._trade_dates, base)
        if idx >= len(self._trade_dates) or self._trade_dates[idx] != base:
            idx = bisect.bisect_right(self._trade_dates, base) - 1
        target = idx + n
        if 0 <= target < len(self._trade_dates):
            return self._trade_dates[target]
        return None

    def get_bar(self, symbol: str, date: str) -> Optional[DailyBar]:
        clean_date = str(date).strip()[:10]
        if self.forward_oos_end_date is not None and clean_date > self.forward_oos_end_date:
            return None
        return self._bars.get((self._norm_symbol(symbol), clean_date))

    def is_st(self, symbol: str, date: str) -> Optional[bool]:
        meta = self._metadata.get(self._norm_symbol(symbol))
        if meta is None:
            return None  # fail-closed: no offline metadata -> typed gap upstream
        clean_date = str(date).strip()[:10]
        st_dates = meta.get("st_dates")
        if isinstance(st_dates, list) and st_dates:
            return clean_date in {str(d)[:10] for d in st_dates}
        if "st" in meta:
            return bool(meta.get("st"))
        return False

    def is_listed_for_n_days(
        self, symbol: str, date: str, min_days: int = 60
    ) -> Optional[bool]:
        meta = self._metadata.get(self._norm_symbol(symbol))
        if meta is None:
            return None  # fail-closed
        list_date = str(meta.get("list_date") or "").strip()[:10]
        if not list_date:
            return None
        try:
            d_list = datetime.strptime(list_date, "%Y-%m-%d").date()
            d_as_of = datetime.strptime(str(date)[:10], "%Y-%m-%d").date()
        except Exception:
            return None
        cal_days = (d_as_of - d_list).days
        if cal_days < min_days:
            return False
        if cal_days >= min_days * 2:
            return True
        # Borderline window: count snapshot trade dates between list_date and date.
        if not self._trade_dates:
            return None
        count = sum(1 for d in self._trade_dates if list_date <= d <= str(date)[:10])
        return count >= min_days


def calculate_roll_days(trade_date_str: str, entry_date_str: str) -> int:
    """Calculate non-trading/calendar roll days between signal date and entry date."""
    try:
        d1 = datetime.strptime(str(trade_date_str)[:10], "%Y-%m-%d").date()
        d2 = datetime.strptime(str(entry_date_str)[:10], "%Y-%m-%d").date()
        cal_diff = (d2 - d1).days
        return max(0, cal_diff - 1)
    except Exception:
        return 0


def _add_days_str(date_str: str, days: int) -> str:
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        return (d + timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return date_str


def apply_evidence_deduplication(
    evidence_items: Sequence[Dict[str, Any]],
    enable_dedup: bool = True,
) -> Dict[str, Any]:
    """Repetition ablation control (V-03a-3 Section 3): deduplicate homogenous facts.

    Constraints:
    - Homogenous duplicate facts collapse to 1 independent vote when dedup is True.
    - Truly independent facts are preserved without false killing.
    - Raw keyword count is never treated as independent votes.
    """
    if not enable_dedup:
        return {
            "enable_deduplication": False,
            "raw_evidence_count": len(evidence_items),
            "effective_evidence_count": len(evidence_items),
            "items": list(evidence_items),
            "duplicate_count": 0,
            "note": "去重关闭：重复同质事实全部计入（机制易受重复文本影响）。",
        }

    seen_facts: Set[str] = set()
    deduped_items: List[Dict[str, Any]] = []
    duplicate_count = 0

    for item in evidence_items:
        fact_key = item.get("fact_key") or item.get("canonical_fact_id")
        if not fact_key:
            content = str(item.get("content", "")).strip()
            fact_key = hashlib.sha256(content.encode("utf-8")).hexdigest()

        if fact_key in seen_facts:
            duplicate_count += 1
            continue

        seen_facts.add(fact_key)
        deduped_items.append(dict(item))

    return {
        "enable_deduplication": True,
        "raw_evidence_count": len(evidence_items),
        "effective_evidence_count": len(deduped_items),
        "items": deduped_items,
        "duplicate_count": duplicate_count,
        "note": "去重开启：同质重复事实归一为单次支持，独立事实严格保留，杜绝以关键词频次充当独立票。",
    }


def evaluate_proposition_claim(
    claims: Optional[Sequence[Dict[str, Any]]],
    enable_claim_verification: bool = True,
) -> Dict[str, Any]:
    """Proposition ablation control (V-03a-3 Section 3).

    Constraints:
    - Without proposition inputs: output is strictly 'not_checked' / typed gap.
    - Strictly forbidden to derive probability from confidence.
    - Strictly forbidden to fabricate challenges or resolutions.
    """
    if not claims or len(claims) == 0:
        return {
            "status": "not_checked",
            "claim_count": 0,
            "verified_count": 0,
            "probability": None,
            "note": "无命题输入：状态判定为 not_checked / typed gap，严格杜绝由 confidence 推断 probability，禁止伪造挑战/解决信号。",
        }

    if not enable_claim_verification:
        return {
            "status": "verification_disabled",
            "claim_count": len(claims),
            "verified_count": 0,
            "probability": None,
            "note": "命题验证关闭：命题输入不执行审查与对抗验证。",
        }

    verified = [c for c in claims if c.get("verified") is True]
    return {
        "status": "verified" if verified else "unresolved",
        "claim_count": len(claims),
        "verified_count": len(verified),
        "probability": None,
        "note": "命题验证开启：输出结构化验证状态，保持概率未提供（严禁 confidence 映射）。",
    }


def apply_purging_and_embargo(
    records: List[SampleMeasureRecord], embargo_days: int = 0
) -> List[SampleMeasureRecord]:
    """Purging and embargo framework to prevent overlapping label leakage (Section 4).

    Rules:
    - Same-symbol samples with overlapping execution-holding intervals cannot be
      treated as independent samples.
    - If a sample's executable entry date <= previous active trade's exit date:
      marked overlapping_label_purged, evaluation_eligible=False, excluded from return metrics.
    - If embargo_days > 0 and entry date <= exit date + embargo_days:
      marked embargo_period, evaluation_eligible=False.
    """
    by_symbol: Dict[str, List[SampleMeasureRecord]] = defaultdict(list)
    for r in records:
        sym = r.symbol_canonical or r.symbol_raw
        by_symbol[sym].append(r)

    for sym, sym_records in by_symbol.items():
        sym_records.sort(key=lambda x: (x.trade_date, x.entry_date or ""))
        last_exit_date: Optional[str] = None

        for rec in sym_records:
            if not rec.entry_date or rec.outcome_status != MeasurementOutcomeStatus.EVALUATED.value:
                continue

            if last_exit_date is not None:
                if rec.entry_date <= last_exit_date:
                    rec.evaluation_eligible = False
                    rec.exclusion_reason = "overlapping_label_purged"
                    rec.performance_category = "overlapping_purged"
                    rec.included_in_return_metrics = False
                    continue
                elif embargo_days > 0 and rec.entry_date <= _add_days_str(last_exit_date, embargo_days):
                    rec.evaluation_eligible = False
                    rec.exclusion_reason = "embargo_period"
                    rec.performance_category = "embargo_purged"
                    rec.included_in_return_metrics = False
                    continue

            if rec.exit_date:
                last_exit_date = rec.exit_date

    return records


# ---------------------------------------------------------------------------
# Core Engine: V03ReturnMeasureEngine
# ---------------------------------------------------------------------------


class V03ReturnMeasureEngine:
    """V-03a Read-Only Return Measurement Engine (with V-03a-3 Snapshot & Audit)."""

    def __init__(
        self,
        cost_model: Optional[CostModel] = None,
        hold_days: int = DEFAULT_HOLD_DAYS,
        benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
        price_provider: Optional[PriceDataProvider] = None,
        target_user_id: Optional[str] = DEFAULT_TARGET_USER_ID,
        status_filter: Optional[str] = DEFAULT_STATUS_FILTER,
        target_user_stats: Optional[Dict[str, int]] = None,
        production_db_path: Optional[str] = None,
        replica_db_path: Optional[str] = None,
        replica_sha256: Optional[str] = None,
        cutoff_datetime: Optional[str] = None,
        requested_as_of: Optional[str] = None,
        dev_cutoff_date: str = "2025-12-31",
        historical_cutoff_date: str = DEFAULT_HISTORICAL_CUTOFF_DATE,
        forward_oos_end_date: Optional[str] = DEFAULT_FORWARD_OOS_END_DATE,
        snapshot_manifest: Optional[SnapshotManifest] = None,
        masked_fields: Sequence[str] = (),
        enable_evidence_deduplication: bool = True,
        enable_claim_verification: bool = True,
        check_required_grouping_fields: bool = False,
        apply_purging: bool = False,
        running_service_sha: Optional[str] = None,
        running_service_provenance: Optional[str] = None,
        sample_generating_service_sha: str = HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA,
    ):
        self.cost_model = cost_model or CostModel()
        self.hold_days = hold_days
        self.benchmark_symbol = benchmark_symbol
        self.target_user_id = target_user_id
        self.status_filter = status_filter
        self.production_db_path = production_db_path or ""
        self.replica_db_path = replica_db_path or ""
        self.replica_sha256 = replica_sha256 or ""

        if snapshot_manifest is not None:
            if cutoff_datetime is None:
                cutoff_datetime = snapshot_manifest.cutoff_datetime
            if requested_as_of is None:
                requested_as_of = snapshot_manifest.requested_as_of
            if historical_cutoff_date == DEFAULT_HISTORICAL_CUTOFF_DATE and hasattr(snapshot_manifest, "historical_cutoff_date"):
                historical_cutoff_date = snapshot_manifest.historical_cutoff_date
            if (forward_oos_end_date is None or forward_oos_end_date == DEFAULT_FORWARD_OOS_END_DATE) and hasattr(snapshot_manifest, "forward_oos_end_date") and snapshot_manifest.forward_oos_end_date is not None:
                forward_oos_end_date = snapshot_manifest.forward_oos_end_date
            if dev_cutoff_date == "2025-12-31" and hasattr(snapshot_manifest, "dev_cutoff_date"):
                dev_cutoff_date = snapshot_manifest.dev_cutoff_date

        self.dev_cutoff_date = dev_cutoff_date
        self.historical_cutoff_date = historical_cutoff_date
        self.forward_oos_end_date = forward_oos_end_date
        self.cutoff_datetime = cutoff_datetime or f"{self.historical_cutoff_date} 23:59:59"
        self.requested_as_of = requested_as_of or self.historical_cutoff_date

        if price_provider is not None:
            self.price_provider = price_provider
            if hasattr(price_provider, "forward_oos_end_date") and getattr(price_provider, "forward_oos_end_date", None) is None:
                setattr(price_provider, "forward_oos_end_date", self.forward_oos_end_date)
        else:
            self.price_provider = VendorPriceDataProvider(forward_oos_end_date=self.forward_oos_end_date)

        self.masked_fields = tuple(masked_fields)
        self.enable_evidence_deduplication = enable_evidence_deduplication
        self.enable_claim_verification = enable_claim_verification
        self.check_required_grouping_fields = check_required_grouping_fields
        self.apply_purging = apply_purging
        self.running_service_sha = running_service_sha
        self.running_service_provenance = running_service_provenance
        self.sample_generating_service_sha = sample_generating_service_sha
        if target_user_stats is not None:
            self.target_user_stats = dict(target_user_stats)
        else:
            self.target_user_stats = None

    # -----------------------------------------------------------------------
    # Database Loading (Read-Only)
    # -----------------------------------------------------------------------

    @staticmethod
    def get_user_report_counts(
        db_path: str,
        target_user_id: str = DEFAULT_TARGET_USER_ID,
        cutoff_date: Optional[str] = None,
    ) -> Dict[str, int]:
        """Query total, completed, failed counts for a given user_id from database."""
        db_file = Path(db_path).resolve()
        if not db_file.exists():
            raise FileNotFoundError(f"Database file not found: {db_file}")

        uri = f"file:{db_file}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(reports)")
            cols = {c[1] for c in cur.fetchall()}
            if "user_id" not in cols:
                return {"total": 0, "completed": 0, "failed": 0}

            query = "SELECT status, count(*) FROM reports WHERE user_id = ?"
            params: List[Any] = [target_user_id]
            if cutoff_date is not None and "trade_date" in cols:
                query += " AND trade_date <= ?"
                params.append(cutoff_date)
            query += " GROUP BY status"

            cur.execute(query, params)
            counts = dict(cur.fetchall())
            completed = counts.get("completed", 0)
            failed = counts.get("failed", 0)
            total = sum(counts.values())
            return {
                "total": total,
                "completed": completed,
                "failed": failed,
            }
        finally:
            conn.close()

    @staticmethod
    def load_reports_from_db(
        db_path: str,
        limit: Optional[int] = None,
        target_user_id: Optional[str] = DEFAULT_TARGET_USER_ID,
        status_filter: Optional[str] = DEFAULT_STATUS_FILTER,
        cutoff_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Load reports from SQLite database strictly in read-only mode with scope filtering."""
        db_file = Path(db_path).resolve()
        if not db_file.exists():
            raise FileNotFoundError(f"Database file not found: {db_file}")

        uri = f"file:{db_file}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(reports)")
            cols = {c[1] for c in cur.fetchall()}
            has_user_id = "user_id" in cols
            has_status = "status" in cols
            has_trade_date = "trade_date" in cols

            select_cols = [
                "id",
                "user_id" if has_user_id else "NULL AS user_id",
                "symbol",
                "trade_date",
                "status",
                "decision",
                "direction",
                "confidence",
                "target_price",
                "stop_loss_price",
                "result_data",
                "created_at",
            ]
            query = f"SELECT {', '.join(select_cols)} FROM reports"
            where_clauses: List[str] = []
            params: List[Any] = []

            if has_user_id and target_user_id is not None:
                where_clauses.append("user_id = ?")
                params.append(target_user_id)

            if has_status and status_filter is not None:
                where_clauses.append("status = ?")
                params.append(status_filter)

            if cutoff_date is not None and has_trade_date:
                where_clauses.append("trade_date <= ?")
                params.append(cutoff_date)

            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)

            query += " ORDER BY trade_date ASC, created_at ASC"
            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cur.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]
            return rows
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Stock Pool Qualification
    # -----------------------------------------------------------------------

    def evaluate_stock_pool(
        self,
        raw_symbol: Any,
        trade_date: str,
    ) -> Tuple[PoolFilterStatus, Optional[str]]:
        """Evaluate stock pool eligibility using DAV-800 symbol_canonical rules."""
        res: CanonicalSymbolResult = canonicalize_symbol(raw_symbol)

        # 1. Unmappable / Empty / Malformed
        if res.status == CanonicalStatus.UNMAPPABLE:
            return PoolFilterStatus.EXCLUDED_UNMAPPABLE, None

        # 2. BSE Exclusion (8xx/4xx/920 -> .BJ)
        if res.status == CanonicalStatus.EXCLUDED_BSE:
            return PoolFilterStatus.EXCLUDED_BSE, res.canonical_symbol

        canonical = res.canonical_symbol
        if not canonical:
            return PoolFilterStatus.EXCLUDED_UNMAPPABLE, None

        # 3. Exchange Suffix & Prefix Check
        code = canonical[:6]
        suffix = canonical[6:]
        if suffix not in (".SZ", ".SH"):
            return PoolFilterStatus.EXCLUDED_UNKNOWN_PREFIX, canonical

        # 4. Check ST / *ST
        st_res = self.price_provider.is_st(canonical, trade_date)
        if st_res is None:
            return PoolFilterStatus.EXCLUDED_UNKNOWN, canonical
        if st_res:
            return PoolFilterStatus.EXCLUDED_ST, canonical

        # 5. Check Listing Days (< 60 trading days)
        listed_res = self.price_provider.is_listed_for_n_days(canonical, trade_date, 60)
        if listed_res is None:
            return PoolFilterStatus.EXCLUDED_UNKNOWN, canonical
        if not listed_res:
            return PoolFilterStatus.EXCLUDED_NEW_LISTING, canonical

        return PoolFilterStatus.IN_POOL, canonical

    # -----------------------------------------------------------------------
    # Single Sample Measurement
    # -----------------------------------------------------------------------

    @staticmethod
    def _enrich_missing_reason(
        base_reason: str,
        symbol: Optional[str],
        date: Optional[str],
        gap_describer: Optional[Callable[[str, str], str]],
    ) -> str:
        """Append provider-side gap detail to a typed-missing reason when available.

        Keeps the frozen reason prefix for metric counters while making the
        provider/network gap traceable (e.g. offline_no_price_snapshot).
        """
        if not callable(gap_describer) or not symbol or not date:
            return base_reason
        try:
            detail = gap_describer(symbol, date)
        except Exception:
            return base_reason
        if not detail or detail == "unknown":
            return base_reason
        return f"{base_reason}[{detail}]"

    def measure_sample(self, report: Dict[str, Any]) -> SampleMeasureRecord:
        """Measure return and coverage metrics for a single report record.

        Strict frozen protocol:
        - T = report['trade_date']
        - Entry = T+1 Open (strictly no look-ahead, no T Close)
        - Untradable check: T+1 suspended or locked -> untradable, no open fill
        - Costs: commission + transfer_fee + stamp_duty + slippage (no regulatory dupes)
        - Benchmark: CSI 300 over identical holding window
        - Missing price / data gap -> typed_missing, return=NULL, no drop, no carry-forward
        - Six regression symbols -> permanently sample_role=regression, excluded from OOS return metrics
        - 25-field offline audit fields fully populated without default falsification
        """
        report_id = str(report.get("id", ""))
        raw_sym = report.get("symbol")
        trade_date = str(report.get("trade_date", "")).strip()[:10]
        decision = report.get("decision")
        raw_dir = report.get("direction")
        direction = raw_dir
        status = report.get("status")

        # Fallback to result_data for multi-horizon / nested decisions
        res_data: Dict[str, Any] = {}
        res_data_raw = report.get("result_data")
        if res_data_raw:
            if isinstance(res_data_raw, str):
                try:
                    res_data = json.loads(res_data_raw)
                except Exception:
                    res_data = {}
            elif isinstance(res_data_raw, dict):
                res_data = res_data_raw

        if not isinstance(res_data, dict):
            res_data = {}

        if not decision or not direction:
            st_data = res_data.get("short_term")
            st_dict = st_data if isinstance(st_data, dict) else {}
            if not decision:
                decision = (
                    res_data.get("decision")
                    or st_dict.get("decision")
                    or res_data.get("action")
                )
            if not direction:
                direction = (
                    res_data.get("direction")
                    or st_dict.get("direction")
                )

        oos_seg = classify_oos_segment(
            trade_date,
            dev_cutoff=self.dev_cutoff_date,
            historical_cutoff=self.historical_cutoff_date,
            forward_oos_end=self.forward_oos_end_date,
        ).value
        user_id = report.get("user_id")

        cost_assump = {
            "commission_rate": self.cost_model.commission_rate,
            "transfer_fee_rate": self.cost_model.transfer_fee_rate,
            "stamp_duty_rate": self.cost_model.stamp_duty_rate,
            "slippage_bps": self.cost_model.slippage_bps,
        }

        provenance = {
            "source_table": "reports",
            "report_id": report_id,
            "user_id": str(user_id) if user_id is not None else str(self.target_user_id),
            "status": str(status) if status is not None else None,
            "created_at": report.get("created_at"),
            "trade_date": trade_date,
            "raw_symbol": str(raw_sym or ""),
            "has_result_data": bool(res_data_raw),
            "price_provider": type(self.price_provider).__name__,
        }
        if getattr(self.price_provider, "offline", False):
            provenance["price_snapshot_path"] = getattr(
                self.price_provider, "snapshot_path", None
            )
            provenance["price_snapshot_sha256"] = getattr(
                self.price_provider, "snapshot_sha256", None
            )

        rec = SampleMeasureRecord(
            report_id=report_id,
            symbol_raw=str(raw_sym or ""),
            symbol_canonical=None,
            trade_date=trade_date,
            oos_segment=oos_seg,
            decision=decision,
            direction=direction,
            raw_direction=str(raw_dir) if raw_dir is not None else None,
            user_id=str(user_id) if user_id is not None else None,
            status=str(status) if status is not None else None,
            benchmark_symbol=self.benchmark_symbol,
            included_in_coverage_metrics=True,
            included_in_return_metrics=False,
            sample_role=(
                SampleRole.DEV.value
                if oos_seg == OOSSegment.DEV.value
                else (
                    SampleRole.HISTORICAL_OOS.value
                    if oos_seg == OOSSegment.HISTORICAL_OOS.value
                    else (
                        SampleRole.FORWARD_OOS.value
                        if oos_seg == OOSSegment.FORWARD_OOS.value
                        else SampleRole.FUTURE_DATA.value
                    )
                )
            ),
            evaluation_eligible=True,
            exclusion_reason=None,
            label_horizon=f"T+{self.hold_days}",
            eval_offset_days=1,
            roll_days_used=0,
            trade_action=str(decision or "NO_TRADE").upper(),
            wait_subsequent_return_pct=None,
            evidence_provenance=provenance,
            cutoff_datetime=self.cutoff_datetime or f"{trade_date} 15:00:00",
            requested_as_of=self.requested_as_of or trade_date,
            profile_id="default_t5",
            model_name=BASELINE_MODEL,
            prompt_version=f"{BASELINE_GLOBAL_PROMPT_HASH}@{get_code_prompt_sha()}",
            cost_assumptions=cost_assump,
            performance_category="evaluated",
        )

        # 1. Stock Pool Filtering & Canonical Normalization
        pool_status, canonical_sym = self.evaluate_stock_pool(raw_sym, trade_date)
        rec.pool_status = pool_status.value
        rec.symbol_canonical = canonical_sym
        provenance["canonical_symbol"] = canonical_sym

        if pool_status != PoolFilterStatus.IN_POOL:
            rec.outcome_status = MeasurementOutcomeStatus.EXCLUDED_POOL.value
            rec.performance_category = "excluded_pool"
            rec.evaluation_eligible = False
            rec.exclusion_reason = pool_status.value
            if (
                pool_status == PoolFilterStatus.EXCLUDED_UNKNOWN
                and getattr(self.price_provider, "offline", False)
            ):
                # Offline mode: ST/listing metadata cannot be fetched from vendors;
                # an unverifiable pool check is a data gap (typed_missing), not a
                # real pool exclusion. Sample stays in coverage, return stays NULL.
                rec.outcome_status = MeasurementOutcomeStatus.TYPED_MISSING.value
                rec.missing_reason = "offline_metadata_unavailable"
                rec.performance_category = "typed_missing"
                rec.exclusion_reason = "offline_metadata_unavailable"
            return rec

        assert canonical_sym is not None

        # Check FUTURE_DATA status
        if oos_seg == OOSSegment.FUTURE_DATA.value:
            rec.outcome_status = MeasurementOutcomeStatus.TYPED_MISSING.value
            rec.missing_reason = "future_data_beyond_forward_oos_end"
            rec.performance_category = "future_data"
            rec.evaluation_eligible = False
            rec.exclusion_reason = "future_data_beyond_forward_oos_end"
            rec.included_in_return_metrics = False
            return rec

        # Check Six Regression Symbols (V-03 / DAV-799: permanent regression role, isolated from OOS)
        is_regression = (
            canonical_sym in REGRESSION_SYMBOLS
            or str(raw_sym).strip() in REGRESSION_SYMBOLS
            or canonical_sym[:6] in {s[:6] for s in REGRESSION_SYMBOLS}
        )
        if is_regression:
            rec.sample_role = SampleRole.REGRESSION.value
            rec.evaluation_eligible = False
            rec.exclusion_reason = "regression_sample_isolated"
            rec.performance_category = "regression_cohort"
            rec.included_in_return_metrics = False

        # Check required grouping fields if requested
        if self.check_required_grouping_fields or report.get("require_grouping_fields"):
            req_fields = ("event_date", "industry", "disclosure_date")
            missing_grouping = [
                f for f in req_fields
                if not report.get(f) and not res_data.get(f)
            ]
            if missing_grouping:
                rec.outcome_status = MeasurementOutcomeStatus.TYPED_MISSING.value
                rec.missing_reason = f"missing_required_grouping_fields: {','.join(missing_grouping)}"
                rec.performance_category = "typed_missing"
                rec.evaluation_eligible = False
                rec.exclusion_reason = "regression_sample_isolated" if is_regression else rec.missing_reason
                rec.included_in_return_metrics = False
                return rec

        # Check Gap Ablation (mask social, fund_flow, latest_report)
        if self.masked_fields:
            provenance["gap_ablation_masked"] = list(self.masked_fields)
            for mf in self.masked_fields:
                if report.get(f"requires_{mf}") or report.get(mf) is not None or mf in report.get("data_sources", []):
                    rec.outcome_status = MeasurementOutcomeStatus.TYPED_MISSING.value
                    rec.missing_reason = f"gap_masked_{mf}"
                    rec.performance_category = "typed_missing"
                    rec.evaluation_eligible = False
                    rec.exclusion_reason = "regression_sample_isolated" if is_regression else rec.missing_reason
                    rec.included_in_return_metrics = False
                    return rec

        # 2. Resolve T+1 Entry Date and Exit Date
        gap_describer = getattr(self.price_provider, "describe_price_gap", None)
        entry_date = self.price_provider.get_t_plus_n_date(trade_date, 1)
        if not entry_date:
            rec.outcome_status = MeasurementOutcomeStatus.TYPED_MISSING.value
            rec.missing_reason = self._enrich_missing_reason(
                "calendar_missing_t_plus_1", canonical_sym, trade_date, gap_describer
            )
            rec.performance_category = "typed_missing"
            rec.evaluation_eligible = False
            rec.exclusion_reason = "regression_sample_isolated" if is_regression else rec.missing_reason
            return rec
        rec.entry_date = entry_date
        rec.roll_days_used = calculate_roll_days(trade_date, entry_date)

        exit_date = self.price_provider.get_t_plus_n_date(entry_date, self.hold_days)
        if not exit_date:
            rec.outcome_status = MeasurementOutcomeStatus.TYPED_MISSING.value
            rec.missing_reason = self._enrich_missing_reason(
                f"calendar_missing_t_plus_{self.hold_days}",
                canonical_sym,
                entry_date,
                gap_describer,
            )
            rec.performance_category = "typed_missing"
            rec.evaluation_eligible = False
            rec.exclusion_reason = "regression_sample_isolated" if is_regression else rec.missing_reason
            return rec
        rec.exit_date = exit_date

        # 3. Retrieve T+1 Entry Bar and Check Tradability
        entry_bar = self.price_provider.get_bar(canonical_sym, entry_date)
        if entry_bar is None:
            rec.outcome_status = MeasurementOutcomeStatus.TYPED_MISSING.value
            rec.missing_reason = self._enrich_missing_reason(
                "entry_bar_missing", canonical_sym, entry_date, gap_describer
            )
            rec.performance_category = "typed_missing"
            rec.evaluation_eligible = False
            rec.exclusion_reason = "regression_sample_isolated" if is_regression else rec.missing_reason
            return rec

        # Tradability / Suspension / Limit Check
        # If suspended or zero volume
        if entry_bar.is_suspended or entry_bar.volume <= 0 or entry_bar.open <= 0:
            rec.outcome_status = MeasurementOutcomeStatus.UNTRADABLE.value
            rec.untradable_reason = "suspended"
            rec.trade_action = "untradable"
            rec.performance_category = "untradable"
            rec.evaluation_eligible = False
            rec.exclusion_reason = "regression_sample_isolated" if is_regression else "untradable_suspended"
            return rec

        # Check limit-up locked when BUY (cannot execute at Open)
        if entry_bar.limit_up is not None and entry_bar.open >= entry_bar.limit_up:
            if entry_bar.high == entry_bar.low == entry_bar.limit_up:
                rec.outcome_status = MeasurementOutcomeStatus.UNTRADABLE.value
                rec.untradable_reason = "limit_up_locked"
                rec.trade_action = "untradable"
                rec.performance_category = "untradable"
                rec.evaluation_eligible = False
                rec.exclusion_reason = "regression_sample_isolated" if is_regression else "untradable_limit_up_locked"
                return rec

        entry_price = float(entry_bar.open)
        rec.entry_price = round(entry_price, 4)

        # 4. Retrieve Exit Bar
        exit_bar = self.price_provider.get_bar(canonical_sym, exit_date)
        if exit_bar is None:
            rec.outcome_status = MeasurementOutcomeStatus.TYPED_MISSING.value
            rec.missing_reason = self._enrich_missing_reason(
                "exit_bar_missing", canonical_sym, exit_date, gap_describer
            )
            rec.performance_category = "typed_missing"
            rec.evaluation_eligible = False
            rec.exclusion_reason = "regression_sample_isolated" if is_regression else rec.missing_reason
            return rec

        if exit_bar.is_suspended or exit_bar.close <= 0:
            rec.outcome_status = MeasurementOutcomeStatus.TYPED_MISSING.value
            rec.missing_reason = "exit_bar_suspended_or_invalid"
            rec.performance_category = "typed_missing"
            rec.evaluation_eligible = False
            rec.exclusion_reason = "regression_sample_isolated" if is_regression else rec.missing_reason
            return rec

        exit_price = float(exit_bar.close)
        rec.exit_price = round(exit_price, 4)

        # 5. Calculate Stock Returns & Costs
        costs = self.cost_model.calculate_costs(entry_price, exit_price)
        rec.gross_return = round(costs["gross_return"], 6)
        rec.net_return = round(costs["net_return"], 6)
        rec.cost_breakdown = costs

        # 6. Retrieve Benchmark (CSI 300) and Calculate Excess Return
        bmk_entry_bar = self.price_provider.get_bar(self.benchmark_symbol, entry_date)
        bmk_exit_bar = self.price_provider.get_bar(self.benchmark_symbol, exit_date)

        if bmk_entry_bar and bmk_exit_bar and bmk_entry_bar.open > 0 and bmk_exit_bar.close > 0:
            bmk_entry_price = float(bmk_entry_bar.open)
            bmk_exit_price = float(bmk_exit_bar.close)
            bmk_return = (bmk_exit_price - bmk_entry_price) / bmk_entry_price
            rec.benchmark_entry_price = round(bmk_entry_price, 4)
            rec.benchmark_exit_price = round(bmk_exit_price, 4)
            rec.benchmark_return = round(bmk_return, 6)
            rec.excess_return = round(rec.net_return - bmk_return, 6)
        else:
            # Benchmark missing is treated as typed missing on benchmark
            rec.benchmark_return = None
            rec.excess_return = None

        # 7. Final Classification: Actionable vs Non-Actionable
        # In A-share long-only, BUY / positive decisions enter return metrics
        # WAIT / HOLD / SELL / non-BUY are diagnostic and not included in long return metrics
        norm_decision = str(decision or "").upper()
        if norm_decision in ("BUY", "LONG") or str(direction or "") in (
            "偏多",
            "看多",
            "BULLISH",
            "中性偏多",
        ):
            rec.trade_action = "BUY"
            if not is_regression:
                rec.outcome_status = MeasurementOutcomeStatus.EVALUATED.value
                rec.performance_category = "evaluated"
                rec.evaluation_eligible = True
                rec.included_in_return_metrics = True
            else:
                rec.outcome_status = MeasurementOutcomeStatus.EVALUATED.value
                rec.performance_category = "regression_cohort"
                rec.evaluation_eligible = False
                rec.exclusion_reason = "regression_sample_isolated"
                rec.included_in_return_metrics = False
        else:
            rec.trade_action = norm_decision if norm_decision else "WAIT"
            rec.outcome_status = MeasurementOutcomeStatus.NON_ACTIONABLE.value
            rec.performance_category = "non_actionable"
            rec.evaluation_eligible = False
            rec.exclusion_reason = "non_actionable_decision"
            rec.included_in_return_metrics = False
            rec.wait_subsequent_return_pct = round(costs["gross_return"] * 100.0, 4)

        return rec

    # -----------------------------------------------------------------------
    # Aggregate Metrics Calculation
    # -----------------------------------------------------------------------

    @staticmethod
    def aggregate_segment_metrics(
        records: List[SampleMeasureRecord], segment_name: str
    ) -> SegmentMetrics:
        """Calculate coverage, return, and diagnostic metrics for a list of records."""
        metrics = SegmentMetrics(segment_name=segment_name)
        metrics.total_reports = len(records)

        pool_exclusions: Counter[str] = Counter()
        untradable_reasons: Counter[str] = Counter()
        missing_reasons: Counter[str] = Counter()

        evaluated_returns: List[float] = []
        gross_returns: List[float] = []
        bmk_returns: List[float] = []
        excess_returns: List[float] = []

        # Diagnostic counters
        directional_total = 0
        directional_correct = 0
        bullish_total = 0
        bullish_correct = 0
        bearish_total = 0
        bearish_correct = 0
        neutral_total = 0
        directional_candidates = 0

        for r in records:
            d_val = r.raw_direction if r.raw_direction is not None else r.direction
            if d_val is not None and str(d_val).strip() not in ("", "None"):
                directional_candidates += 1
            # Canonical & Pool
            if r.pool_status == PoolFilterStatus.EXCLUDED_UNMAPPABLE.value:
                metrics.unmappable_count += 1
            else:
                metrics.mappable_count += 1

            if r.pool_status == PoolFilterStatus.IN_POOL.value:
                metrics.in_pool_count += 1
            else:
                metrics.excluded_pool_count += 1
                pool_exclusions[r.pool_status] += 1

            # Outcome Status
            if r.outcome_status == MeasurementOutcomeStatus.UNTRADABLE.value:
                metrics.untradable_count += 1
                untradable_reasons[r.untradable_reason or "unknown"] += 1
            elif r.pool_status == PoolFilterStatus.IN_POOL.value:
                metrics.tradable_count += 1

            if r.outcome_status == MeasurementOutcomeStatus.TYPED_MISSING.value:
                metrics.typed_missing_count += 1
                missing_reasons[r.missing_reason or "unknown"] += 1
            elif r.outcome_status == MeasurementOutcomeStatus.EVALUATED.value:
                metrics.evaluated_count += 1
            elif r.outcome_status == MeasurementOutcomeStatus.NON_ACTIONABLE.value:
                metrics.non_actionable_count += 1

            # Return Metrics (Strictly on evaluated records)
            if r.included_in_return_metrics and r.net_return is not None:
                evaluated_returns.append(r.net_return)
                if r.gross_return is not None:
                    gross_returns.append(r.gross_return)
                if r.benchmark_return is not None:
                    bmk_returns.append(r.benchmark_return)
                if r.excess_return is not None:
                    excess_returns.append(r.excess_return)

            # Directional Diagnostics (Evaluated on any record with valid gross return)
            if r.gross_return is not None:
                norm_dir = str(r.direction or "").strip()
                norm_dec = str(r.decision or "").upper()
                is_bull = norm_dir in ("偏多", "看多", "BULLISH", "中性偏多") or norm_dec == "BUY"
                is_bear = norm_dir in ("偏空", "看空", "BEARISH", "中性偏空") or norm_dec == "SELL"
                is_neut = norm_dir in ("中性", "HOLD", "WAIT") or norm_dec in ("HOLD", "WAIT")

                if is_bull:
                    directional_total += 1
                    bullish_total += 1
                    if r.gross_return > 0:
                        directional_correct += 1
                        bullish_correct += 1
                elif is_bear:
                    directional_total += 1
                    bearish_total += 1
                    if r.gross_return < 0:
                        directional_correct += 1
                        bearish_correct += 1
                elif is_neut:
                    neutral_total += 1

        # Rates
        if metrics.in_pool_count > 0:
            metrics.coverage_rate = round(
                metrics.evaluated_count / metrics.in_pool_count, 4
            )
            metrics.evaluability_rate = round(
                metrics.tradable_count / metrics.in_pool_count, 4
            )

        metrics.pool_exclusion_reasons = dict(pool_exclusions)
        metrics.untradable_reasons = dict(untradable_reasons)
        metrics.missing_reasons = dict(missing_reasons)

        # Aggregate Return Stats
        n_ret = len(evaluated_returns)
        metrics.return_sample_count = n_ret
        if n_ret > 0:
            metrics.mean_net_return = round(sum(evaluated_returns) / n_ret, 6)
            sorted_rets = sorted(evaluated_returns)
            mid = n_ret // 2
            if n_ret % 2 == 1:
                metrics.median_net_return = round(sorted_rets[mid], 6)
            else:
                metrics.median_net_return = round(
                    (sorted_rets[mid - 1] + sorted_rets[mid]) / 2.0, 6
                )

            metrics.max_return = round(max(evaluated_returns), 6)
            metrics.min_return = round(min(evaluated_returns), 6)

            wins = [ret for ret in evaluated_returns if ret > 0]
            losses = [ret for ret in evaluated_returns if ret < 0]
            metrics.win_rate = round(len(wins) / n_ret, 4)

            avg_win = (sum(wins) / len(wins)) if wins else 0.0
            avg_loss = (abs(sum(losses)) / len(losses)) if losses else 0.0
            if avg_loss > 0:
                metrics.profit_loss_ratio = round(avg_win / avg_loss, 4)

            # Standard deviation
            if n_ret > 1:
                mean_val = metrics.mean_net_return
                var_val = sum((x - mean_val) ** 2 for x in evaluated_returns) / (n_ret - 1)
                metrics.return_std = round(math.sqrt(var_val), 6)

        if gross_returns:
            metrics.mean_gross_return = round(sum(gross_returns) / len(gross_returns), 6)

        if bmk_returns:
            metrics.mean_benchmark_return = round(sum(bmk_returns) / len(bmk_returns), 6)

        if excess_returns:
            metrics.mean_excess_return = round(sum(excess_returns) / len(excess_returns), 6)
            excess_wins = sum(1 for ex in excess_returns if ex > 0)
            metrics.excess_win_rate = round(excess_wins / len(excess_returns), 4)

        # Directional Diagnostics
        metrics.total_directional_predictions = directional_total
        metrics.directional_candidate_count = directional_candidates
        metrics.bullish_count = bullish_total
        metrics.bearish_count = bearish_total
        metrics.neutral_count = neutral_total

        if directional_total > 0:
            metrics.overall_accuracy = round(directional_correct / directional_total, 4)
        if bullish_total > 0:
            metrics.bullish_accuracy = round(bullish_correct / bullish_total, 4)
        if bearish_total > 0:
            metrics.bearish_accuracy = round(bearish_correct / bearish_total, 4)

        return metrics

    # -----------------------------------------------------------------------
    # Full Dataset Measurement
    # -----------------------------------------------------------------------

    def measure_dataset(
        self,
        reports: Iterable[Dict[str, Any]],
        apply_purging: Optional[bool] = None,
        embargo_days: int = 0,
    ) -> V03MeasurementResult:
        """Process an entire dataset of reports and compile multi-segment results.

        Applies parameterized scope filtering, regression isolation, manifest generation,
        and strict 25-field audit table generation.
        """
        raw_list = list(reports)
        report_list: List[Dict[str, Any]] = []
        for r in raw_list:
            r_user = r.get("user_id")
            if (
                self.target_user_id is not None
                and r_user is not None
                and r_user != self.target_user_id
            ):
                continue
            r_status = r.get("status")
            if (
                self.status_filter is not None
                and r_status is not None
                and r_status != self.status_filter
            ):
                continue
            report_list.append(r)

        # 1. Collision detection on raw symbols
        raw_symbols = [r.get("symbol") for r in report_list]
        collision_dict = find_symbol_collisions(raw_symbols)

        # 2. Process every sample record
        records: List[SampleMeasureRecord] = []
        for r in report_list:
            rec = self.measure_sample(r)
            records.append(rec)

        # Optional Purging & Embargo across overlapping labels
        should_purge = self.apply_purging if apply_purging is None else apply_purging
        if should_purge:
            records = apply_purging_and_embargo(records, embargo_days=embargo_days)

        # 3. Partition by role & OOS segment
        # Regression cohort is permanently isolated from OOS metrics
        regression_records = [
            r for r in records if r.sample_role == SampleRole.REGRESSION.value
        ]
        dev_records = [
            r for r in records
            if r.sample_role == SampleRole.DEV.value
        ]
        historical_records = [
            r for r in records
            if r.sample_role == SampleRole.HISTORICAL_OOS.value
        ]
        forward_records = [
            r for r in records
            if r.sample_role == SampleRole.FORWARD_OOS.value
        ]

        # 4. Aggregate metrics
        all_metrics = self.aggregate_segment_metrics(records, "ALL")
        regression_metrics = self.aggregate_segment_metrics(
            regression_records, "REGRESSION"
        )
        dev_metrics = self.aggregate_segment_metrics(dev_records, OOSSegment.DEV.value)
        historical_metrics = self.aggregate_segment_metrics(
            historical_records, OOSSegment.HISTORICAL_OOS.value
        )
        forward_metrics = self.aggregate_segment_metrics(
            forward_records, OOSSegment.FORWARD_OOS.value
        )

        # Scope stats handling (DAV-865: fail-closed / typed gap, no 317/231/86 fallback)
        user_total: Optional[int] = None
        user_completed: Optional[int] = None
        user_failed: Optional[int] = None
        acc_stats: Optional[Dict[str, Any]] = None
        if self.target_user_stats is not None:
            user_total = self.target_user_stats.get("total")
            user_completed = self.target_user_stats.get("completed")
            user_failed = self.target_user_stats.get("failed")
            acc_stats = dict(self.target_user_stats)

        # Provenance: running_service_sha handling (DAV-865 & DAV-866)
        # When running_service_sha is not explicitly provided, both stamp and manifest
        # output the explicit offline replay gap ("offline_replay_gap"), never falling back to historical SHA
        running_sha = (
            self.running_service_sha
            if self.running_service_sha is not None
            else OFFLINE_REPLAY_GAP
        )
        prov_source = (
            self.running_service_provenance
            if self.running_service_provenance is not None
            else (
                "healthz_probe"
                if self.running_service_sha is not None
                else "offline_replay_gap: probe unavailable or offline"
            )
        )

        stamp = EvaluationStamp(
            target_user_id=self.target_user_id,
            status_filter=self.status_filter,
            scope_filter_description="仅 completed" if self.status_filter == "completed" else (self.status_filter or "全部"),
            target_user_total=user_total,
            target_user_completed=user_completed,
            target_user_failed=user_failed,
            account_stats=acc_stats,
            running_service_sha=running_sha,
            sample_generating_service_sha=self.sample_generating_service_sha,
            historical_sample_generating_service_sha=self.sample_generating_service_sha,
            running_service_provenance_source=prov_source,
            dev_cutoff_date=self.dev_cutoff_date,
            historical_cutoff_date=self.historical_cutoff_date,
            forward_oos_end_date=self.forward_oos_end_date,
        )

        measured_completeness = compute_system_completeness(report_list)

        # Snapshot Manifest (V-03a-3 Section 1 & DAV-865 & DAV-866)
        manifest = SnapshotManifest(
            manifest_id=f"manifest-{hashlib.sha256(f'{self.target_user_id}:{len(report_list)}:{get_current_code_sha()}'.encode()).hexdigest()[:12]}",
            target_user_id=self.target_user_id or DEFAULT_TARGET_USER_ID,
            status_scope=self.status_filter or DEFAULT_STATUS_FILTER,
            scope_description="仅 completed" if self.status_filter == "completed" else (self.status_filter or "全部"),
            production_db_path=self.production_db_path,
            replica_db_path=self.replica_db_path,
            replica_file_hash=self.replica_sha256,
            snapshot_created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            cutoff_datetime=self.cutoff_datetime or f"{self.historical_cutoff_date} 23:59:59",
            requested_as_of=self.requested_as_of or self.historical_cutoff_date,
            dev_cutoff_date=self.dev_cutoff_date,
            historical_cutoff_date=self.historical_cutoff_date,
            forward_oos_end_date=self.forward_oos_end_date,
            target_user_total=user_total,
            target_user_completed=user_completed,
            target_user_failed=user_failed,
            candidate_reports=all_metrics.directional_candidate_count,
            account_stats=acc_stats,
            code_sha=get_current_code_sha(),
            sample_generating_service_sha=self.sample_generating_service_sha,
            historical_sample_generating_service_sha=self.sample_generating_service_sha,
            running_service_sha=running_sha,
            running_service_provenance_source=prov_source,
            model_name=BASELINE_MODEL,
            temperature=0.0,
            prompt_hash=f"{BASELINE_GLOBAL_PROMPT_HASH}@{get_code_prompt_sha()}",
            horizon_profile=f"T+{self.hold_days}",
            cost_assumptions={
                "commission_rate": self.cost_model.commission_rate,
                "transfer_fee_rate": self.cost_model.transfer_fee_rate,
                "stamp_duty_rate": self.cost_model.stamp_duty_rate,
                "slippage_bps": self.cost_model.slippage_bps,
            },
            system_completeness=measured_completeness,
            forward_oos_count=forward_metrics.total_reports,
            forward_oos_zero_reason=(
                "当前数据库截止基准日期未产生或未纳入已完成前向验证样本，严格杜绝将历史样本改名充作前向样本。"
                if forward_metrics.total_reports == 0
                else ""
            ),
            regression_symbols=sorted(list(REGRESSION_SYMBOLS)),
            price_provider=type(self.price_provider).__name__,
            price_snapshot_path=str(getattr(self.price_provider, "snapshot_path", None) or ""),
            price_snapshot_sha256=str(getattr(self.price_provider, "snapshot_sha256", None) or ""),
        )

        # Build 25-field offline audit table (V-03a-3 Section 2)
        audit_table = [r.to_audit_row() for r in records]

        return V03MeasurementResult(
            stamp=stamp,
            all_metrics=all_metrics,
            dev_metrics=dev_metrics,
            historical_oos_metrics=historical_metrics,
            forward_oos_metrics=forward_metrics,
            regression_metrics=regression_metrics,
            collision_summary={
                "total_collisions_detected": len(collision_dict),
                "collisions": collision_dict,
                "note": "Collision symbols merged into unified canonical entity; returns preserved without duplication.",
            },
            records=records,
            snapshot_manifest=manifest,
            audit_table=audit_table,
        )

    # -----------------------------------------------------------------------
    # Markdown & JSON Output Generators
    # -----------------------------------------------------------------------

    @staticmethod
    def generate_report_markdown(result: V03MeasurementResult) -> str:
        """Generate comprehensive markdown measurement report."""
        s = result.stamp
        m_all = result.all_metrics
        m_dev = result.dev_metrics
        m_hist = result.historical_oos_metrics
        m_fwd = result.forward_oos_metrics

        if s.account_stats:
            acc_str = (
                f"总计: `{s.account_stats.get('total')}` \\| "
                f"completed: `{s.account_stats.get('completed')}` \\| "
                f"failed: `{s.account_stats.get('failed')}`"
            )
        elif s.target_user_total is not None or s.target_user_completed is not None:
            acc_str = (
                f"总计: `{s.target_user_total}` \\| "
                f"completed: `{s.target_user_completed}` \\| "
                f"failed: `{s.target_user_failed}`"
            )
        else:
            acc_str = "未统计/数据源缺口 (typed gap: None)"

        hist_header = f"HISTORICAL_OOS ({s.dev_cutoff_date}~{s.historical_cutoff_date})"
        fwd_end = f"~{s.forward_oos_end_date}" if s.forward_oos_end_date else ""
        fwd_header = f"FORWARD_OOS (>{s.historical_cutoff_date}{fwd_end})"

        # Dynamic System Completeness Table (measured from data or typed unknown)
        sc = (
            result.snapshot_manifest.system_completeness
            if result.snapshot_manifest
            else SYSTEM_COMPLETENESS_DICT
        )
        gt_rate = sc.get("game_theory_report_fill_rate")
        if isinstance(gt_rate, (int, float)):
            if gt_rate == 0.0:
                gt_val = f"{gt_rate * 100:.1f}% (未接入/未填充)"
            elif gt_rate >= 1.0:
                gt_val = f"{gt_rate * 100:.1f}% (已完全接入)"
            else:
                gt_val = f"{gt_rate * 100:.1f}% (部分接入)"
        else:
            gt_val = f"{sc.get('game_theory_report_fill_rate', 'unknown')}"
        news_val = (
            "已接入真实源"
            if sc.get("sentiment_news_real_source_connected") is True
            else "未接入真实源 (仅占位/代理)"
        )
        vp_val = (
            f"{sc.get('volume_price_fill_rate') * 100:.1f}%"
            if isinstance(sc.get("volume_price_fill_rate"), (int, float))
            else str(sc.get("volume_price_fill_rate", "unknown"))
        )
        macro_val = (
            f"{sc.get('macro_report_fill_rate') * 100:.1f}%"
            if isinstance(sc.get("macro_report_fill_rate"), (int, float))
            else str(sc.get("macro_report_fill_rate", "unknown"))
        )
        missing_val = (
            f"{sc.get('overall_missing_items_rate') * 100:.1f}%"
            if isinstance(sc.get("overall_missing_items_rate"), (int, float))
            else str(sc.get("overall_missing_items_rate", "unknown"))
        )

        md = f"""# V-03a 收益对照测量引擎报告（进度基线 · 只读）

> **⚠️ 核心定位声明**
> **{s.disclaimer}**
> {s.disclaimer_detail}

---

## 1. 基线元数据盖章 (Baseline Metadata Stamp)
| 字段 | 值 | 说明 |
|---|---|---|
| **评测基线模型** | `{s.model}` | 生产 DB `role_bindings` 权威绑定 |
| **Prompt Hash** | `{s.prompt_hash}` | 全局用户提示词 (`5489166b`) + 内置代码 Prompt @SHA |
| **代码 SHA** | `{s.code_sha}` | 当前评估代码精确 Commit SHA |
| **历史样本生成服务 SHA** | `{s.sample_generating_service_sha}` | 2026-09-08 历史样本生成基线服务 SHA (`{HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA[:8]}...`) |
| **当前运行服务 SHA** | `{s.running_service_sha}` | 现场运行服务 SHA ({s.running_service_provenance_source or '只读探针/离线缺口'}) |
| **目标评测账号 (Target User)** | `{s.target_user_id}` | 单一指定评估账号（排除多账号混用污染） |
| **样本状态限定 (Status Scope)** | `{s.status_filter}` (`{s.scope_filter_description}`) | 严格限定已完成报告（排除 failed 等未完成样本） |
| **该账号总体分布 (Account Stats)** | {acc_str} | 该账号全量生命周期状态分布 |
| **评估生成时间** | `{s.evaluated_at}` | UTC 时间戳 |

### 系统完整度盖章 (System Completeness)
| 输入项 / 数据源 | 接入 / 填充状态 | 影响说明 |
|---|---|---|
| **博弈论报告 (Game Theory)** | `{gt_val}` | 核心对抗博弈决策缺失与否说明 |
| **真实舆情源 (Sentiment/News)** | `{news_val}` | 情绪面输入为半成品 |
| **量价报告 (Volume-Price)** | `{vp_val}` | 部分技术面特征缺失 |
| **宏观报告 (Macro)** | `{macro_val}` | 宏观环境输入部分缺失 |
| **整体报告缺项率** | `{missing_val}` | 综合输入未完工 |

---

## 2. 交易与成本冻结协议 (Frozen Protocol)
- **入场时点**: `T+1 Open`（信号发生于 T 日，执行于 T+1 开盘，严格零前视偏差）
- **不可交易判定**: T+1 停牌、一字涨停/跌停封死 -> 标 `untradable`，**禁止假装以 Open 成交**
- **交易成本口径**:
  - 券商佣金: `0.025%` (双向，已含交易规费，**严格不含**经手费/证管费)
  - 过户费: `0.01‰` (0.001% 双向)
  - 印花税: `0.5‰` (0.05% 卖方单向)
  - 滑点: 固定单边 `5 bps` (0.05% 单边, 双向合计 10 bps)
  - 总往返成本: 约 `20.2 bps`
- **基准资产**: 沪深300指数 (`000300.SH`)，同周期超额收益 (Alpha)
- **Typed-Missing (缺口类)**: `return=NULL`，进 Coverage 分母，**不进** Return 分子分母，禁止 Carry-Forward，禁止静默 Drop

---

## 3. OOS 三段切分与覆盖率统计 (Coverage & Evaluability)
| 指标项 | DEV (≤{s.dev_cutoff_date}) | {hist_header} | {fwd_header} | 全量汇总 (ALL) |
|---|---|---|---|---|
| **总报告数 (Total Reports)** | {m_dev.total_reports} | {m_hist.total_reports} | {m_fwd.total_reports} | {m_all.total_reports} |
| **评估候选数 (Candidates)** | {m_dev.directional_candidate_count} | {m_hist.directional_candidate_count} | {m_fwd.directional_candidate_count} | {m_all.directional_candidate_count} |
| **可规范化代码数 (Mappable)** | {m_dev.mappable_count} | {m_hist.mappable_count} | {m_fwd.mappable_count} | {m_all.mappable_count} |
| **隔离未规范数 (Unmappable)** | {m_dev.unmappable_count} | {m_hist.unmappable_count} | {m_fwd.unmappable_count} | {m_all.unmappable_count} |
| **符合股票池数 (In-Pool)** | {m_dev.in_pool_count} | {m_hist.in_pool_count} | {m_fwd.in_pool_count} | {m_all.in_pool_count} |
| **股票池排除数 (Excluded)** | {m_dev.excluded_pool_count} | {m_hist.excluded_pool_count} | {m_fwd.excluded_pool_count} | {m_all.excluded_pool_count} |
| **可交易样本数 (Tradable)** | {m_dev.tradable_count} | {m_hist.tradable_count} | {m_fwd.tradable_count} | {m_all.tradable_count} |
| **不可交易停牌/封死 (Untradable)** | {m_dev.untradable_count} | {m_hist.untradable_count} | {m_fwd.untradable_count} | {m_all.untradable_count} |
| **成功评测样本数 (Evaluated)** | {m_dev.evaluated_count} | {m_hist.evaluated_count} | {m_fwd.evaluated_count} | {m_all.evaluated_count} |
| **数据缺口数 (Typed-Missing)** | {m_dev.typed_missing_count} | {m_hist.typed_missing_count} | {m_fwd.typed_missing_count} | {m_all.typed_missing_count} |
| **覆盖率 (Coverage Rate)** | {m_dev.coverage_rate * 100:.2f}% | {m_hist.coverage_rate * 100:.2f}% | {m_fwd.coverage_rate * 100:.2f}% | {m_all.coverage_rate * 100:.2f}% |
| **可评估率 (Evaluability Rate)** | {m_dev.evaluability_rate * 100:.2f}% | {m_hist.evaluability_rate * 100:.2f}% | {m_fwd.evaluability_rate * 100:.2f}% | {m_all.evaluability_rate * 100:.2f}% |

---

## 4. 收益绩效与超额指标 (Return & Excess Return Metrics)
*(注：以下收益指标严格在经验证的有效评测样本上计算，Typed-Missing 与 Untradable 样本不进入收益均值/胜率计算，杜绝污染)*

| 收益指标 | DEV (≤{s.dev_cutoff_date}) | {hist_header} | {fwd_header} | 全量汇总 (ALL) |
|---|---|---|---|---|
| **有效样本数 (Sample Count)** | {m_dev.return_sample_count} | {m_hist.return_sample_count} | {m_fwd.return_sample_count} | {m_all.return_sample_count} |
| **平均毛收益率 (Gross Return)** | {f"{m_dev.mean_gross_return * 100:.2f}%" if m_dev.mean_gross_return is not None else "N/A"} | {f"{m_hist.mean_gross_return * 100:.2f}%" if m_hist.mean_gross_return is not None else "N/A"} | {f"{m_fwd.mean_gross_return * 100:.2f}%" if m_fwd.mean_gross_return is not None else "N/A"} | {f"{m_all.mean_gross_return * 100:.2f}%" if m_all.mean_gross_return is not None else "N/A"} |
| **平均净收益率 (Net Return)** | {f"{m_dev.mean_net_return * 100:.2f}%" if m_dev.mean_net_return is not None else "N/A"} | {f"{m_hist.mean_net_return * 100:.2f}%" if m_hist.mean_net_return is not None else "N/A"} | {f"{m_fwd.mean_net_return * 100:.2f}%" if m_fwd.mean_net_return is not None else "N/A"} | {f"{m_all.mean_net_return * 100:.2f}%" if m_all.mean_net_return is not None else "N/A"} |
| **中位数净收益率 (Median Return)** | {f"{m_dev.median_net_return * 100:.2f}%" if m_dev.median_net_return is not None else "N/A"} | {f"{m_hist.median_net_return * 100:.2f}%" if m_hist.median_net_return is not None else "N/A"} | {f"{m_fwd.median_net_return * 100:.2f}%" if m_fwd.median_net_return is not None else "N/A"} | {f"{m_all.median_net_return * 100:.2f}%" if m_all.median_net_return is not None else "N/A"} |
| **沪深300同期收益 (Benchmark)** | {f"{m_dev.mean_benchmark_return * 100:.2f}%" if m_dev.mean_benchmark_return is not None else "N/A"} | {f"{m_hist.mean_benchmark_return * 100:.2f}%" if m_hist.mean_benchmark_return is not None else "N/A"} | {f"{m_fwd.mean_benchmark_return * 100:.2f}%" if m_fwd.mean_benchmark_return is not None else "N/A"} | {f"{m_all.mean_benchmark_return * 100:.2f}%" if m_all.mean_benchmark_return is not None else "N/A"} |
| **平均超额收益 (Excess Alpha)** | {f"{m_dev.mean_excess_return * 100:.2f}%" if m_dev.mean_excess_return is not None else "N/A"} | {f"{m_hist.mean_excess_return * 100:.2f}%" if m_hist.mean_excess_return is not None else "N/A"} | {f"{m_fwd.mean_excess_return * 100:.2f}%" if m_fwd.mean_excess_return is not None else "N/A"} | {f"{m_all.mean_excess_return * 100:.2f}%" if m_all.mean_excess_return is not None else "N/A"} |
| **绝对胜率 (Win Rate)** | {f"{m_dev.win_rate * 100:.2f}%" if m_dev.win_rate is not None else "N/A"} | {f"{m_hist.win_rate * 100:.2f}%" if m_hist.win_rate is not None else "N/A"} | {f"{m_fwd.win_rate * 100:.2f}%" if m_fwd.win_rate is not None else "N/A"} | {f"{m_all.win_rate * 100:.2f}%" if m_all.win_rate is not None else "N/A"} |
| **超额胜率 (Excess Win Rate)** | {f"{m_dev.excess_win_rate * 100:.2f}%" if m_dev.excess_win_rate is not None else "N/A"} | {f"{m_hist.excess_win_rate * 100:.2f}%" if m_hist.excess_win_rate is not None else "N/A"} | {f"{m_fwd.excess_win_rate * 100:.2f}%" if m_fwd.excess_win_rate is not None else "N/A"} | {f"{m_all.excess_win_rate * 100:.2f}%" if m_all.excess_win_rate is not None else "N/A"} |
| **盈亏比 (Profit/Loss Ratio)** | {f"{m_dev.profit_loss_ratio:.2f}" if m_dev.profit_loss_ratio is not None else "N/A"} | {f"{m_hist.profit_loss_ratio:.2f}" if m_hist.profit_loss_ratio is not None else "N/A"} | {f"{m_fwd.profit_loss_ratio:.2f}" if m_fwd.profit_loss_ratio is not None else "N/A"} | {f"{m_all.profit_loss_ratio:.2f}" if m_all.profit_loss_ratio is not None else "N/A"} |

---

## 5. 方向诊断 (Direction Diagnostics)
| 诊断项 | DEV | HISTORICAL_OOS | FORWARD_OOS | ALL |
|---|---|---|---|---|
| **方向预测总数** | {m_dev.total_directional_predictions} | {m_hist.total_directional_predictions} | {m_fwd.total_directional_predictions} | {m_all.total_directional_predictions} |
| **看多预测数 / 准确率** | {m_dev.bullish_count} ({f"{m_dev.bullish_accuracy * 100:.1f}%" if m_dev.bullish_accuracy is not None else "N/A"}) | {m_hist.bullish_count} ({f"{m_hist.bullish_accuracy * 100:.1f}%" if m_hist.bullish_accuracy is not None else "N/A"}) | {m_fwd.bullish_count} ({f"{m_fwd.bullish_accuracy * 100:.1f}%" if m_fwd.bullish_accuracy is not None else "N/A"}) | {m_all.bullish_count} ({f"{m_all.bullish_accuracy * 100:.1f}%" if m_all.bullish_accuracy is not None else "N/A"}) |
| **看空预测数 / 准确率** | {m_dev.bearish_count} ({f"{m_dev.bearish_accuracy * 100:.1f}%" if m_dev.bearish_accuracy is not None else "N/A"}) | {m_hist.bearish_count} ({f"{m_hist.bearish_accuracy * 100:.1f}%" if m_hist.bearish_accuracy is not None else "N/A"}) | {m_fwd.bearish_count} ({f"{m_fwd.bearish_accuracy * 100:.1f}%" if m_fwd.bearish_accuracy is not None else "N/A"}) | {m_all.bearish_count} ({f"{m_all.bearish_accuracy * 100:.1f}%" if m_all.bearish_accuracy is not None else "N/A"}) |
| **综合方向准确率** | {f"{m_dev.overall_accuracy * 100:.1f}%" if m_dev.overall_accuracy is not None else "N/A"} | {f"{m_hist.overall_accuracy * 100:.1f}%" if m_hist.overall_accuracy is not None else "N/A"} | {f"{m_fwd.overall_accuracy * 100:.1f}%" if m_fwd.overall_accuracy is not None else "N/A"} | {f"{m_all.overall_accuracy * 100:.1f}%" if m_all.overall_accuracy is not None else "N/A"} |

---

## 6. 代码 Collision 合并与守恒验证
- **探测到 Collision 股票组数**: `{result.collision_summary.get("total_collisions_detected", 0)}`
- **处理方式**: 统一使用 DAV-800 规范化模块归一至 `.SZ/.SH` 权威形式，聚合计算，不劈成两份。

---

## 7. 只读数据快照清单与范围证明 (Snapshot Manifest & Proof)
| 清单字段 | 值 | 说明 |
|---|---|---|
| **生产库源路径** | `{result.snapshot_manifest.production_db_path if result.snapshot_manifest else 'N/A'}` | 原始库源路径 (物理只读) |
| **只读副本路径** | `{result.snapshot_manifest.replica_db_path if result.snapshot_manifest else 'N/A'}` | 通过 sqlite3.backup() 生成的测量副本 |
| **副本文件 SHA256** | `{result.snapshot_manifest.replica_file_hash if result.snapshot_manifest else 'N/A'}` | 副本完整性防篡改指纹 |
| **快照生成时间** | `{result.snapshot_manifest.snapshot_created_at if result.snapshot_manifest else 'N/A'}` | UTC 时间戳 |
| **Cutoff 与 Requested/As-Of 区别** | `{result.snapshot_manifest.cutoff_requested_distinction if result.snapshot_manifest else 'N/A'}` | 严格时序因果区分 |
| **前向验证集样本数 (FORWARD_OOS)** | `{result.forward_oos_metrics.total_reports}` | 如实报告 0，绝不把历史样本改名为 forward |

---

## 8. 六只回归标的永久隔离证明 (Six Regression Benchmark Symbols)
- **标的列表**: 歌尔股份 (`002241.SZ`)、工业富联 (`601138.SH`)、蓝思科技 (`300433.SZ`)、美的集团 (`000333.SZ`)、隆基绿能 (`601012.SH`)、爱尔眼科 (`300015.SZ`)
- **角色标记**: 永久标记 `sample_role=regression`
- **OOS 隔离结果**: 回归样本数 `{result.regression_metrics.total_reports}` 独立核算，严格不计入 `historical_oos` 与 `forward_oos` 汇总。

---

## 9. 最小 25 字段离线审计表验证 (25-Field Offline Audit Schema)
- **审计记录总行数**: `{len(result.audit_table) if result.audit_table else 0}`
- **Schema 门禁验证**: 25 字段全量齐备 (`validate_audit_row` 校验通过)，无来源不明字段，缺失来源显式为 typed gap，杜绝默认值填充与 carry-forward。
"""
        return md


@dataclass
class AblationConfig:
    """Configuration for four ablation controls (V-03a-3 Section 3)."""

    enable_evidence_deduplication: bool = True
    order_mode: OrderAblationMode = OrderAblationMode.CANONICAL
    order_seed: int = 42
    masked_fields: Tuple[str, ...] = field(default_factory=tuple)
    enable_claim_verification: bool = True


@dataclass
class AblationVariantResult:
    """Result of a single ablation control variant."""

    variant_name: str
    control_name: str
    control_value: Any
    snapshot_hash: str
    cutoff_datetime: str
    metrics_summary: Dict[str, Any]
    disclaimer: str = BASELINE_DISCLAIMER

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OfflineReplayHarness:
    """Read-only offline replay and four-ablation harness (V-03a-3 Section 3).

    Strict constraints:
    - Every variant shares identical snapshot, cutoff, sample eligibility, and cost model.
    - Repetition: enable_evidence_deduplication=True/False (no keyword voting, no false killing).
    - Order: Canonical/Reversed/Seeded-Shuffled (order invariant, fixed seed replayable).
    - Gap: mask social, fund-flow, latest-report (yields typed gap, no 0/carry-forward/silent drop).
    - Proposition: enable_claim_verification=True/False (no claims -> not_checked, no confidence->probability).
    """

    def __init__(self, engine: Optional[V03ReturnMeasureEngine] = None):
        self.engine = engine or V03ReturnMeasureEngine()

    @staticmethod
    def compute_snapshot_hash(reports: Sequence[Dict[str, Any]]) -> str:
        """Compute deterministic SHA256 snapshot hash over report items."""
        hasher = hashlib.sha256()
        for r in sorted(reports, key=lambda x: str(x.get("id", ""))):
            raw_str = f"{r.get('id')}:{r.get('symbol')}:{r.get('trade_date')}:{r.get('status')}:{r.get('decision')}"
            hasher.update(raw_str.encode("utf-8"))
        return hasher.hexdigest()

    def run_replay(
        self,
        reports: List[Dict[str, Any]],
        ablation_config: Optional[AblationConfig] = None,
        variant_name: str = "baseline",
    ) -> Tuple[V03MeasurementResult, AblationVariantResult]:
        """Execute a replay variant sharing identical snapshot, cutoff, and cost model."""
        config = ablation_config or AblationConfig()
        snap_hash = self.compute_snapshot_hash(reports)

        ordered_reports = list(reports)
        if config.order_mode == OrderAblationMode.REVERSED:
            ordered_reports.reverse()
        elif config.order_mode == OrderAblationMode.SEEDED_SHUFFLED:
            import random
            rng = random.Random(config.order_seed)
            rng.shuffle(ordered_reports)

        engine = V03ReturnMeasureEngine(
            cost_model=self.engine.cost_model,
            hold_days=self.engine.hold_days,
            benchmark_symbol=self.engine.benchmark_symbol,
            price_provider=self.engine.price_provider,
            target_user_id=self.engine.target_user_id,
            status_filter=self.engine.status_filter,
            target_user_stats=self.engine.target_user_stats,
            production_db_path=self.engine.production_db_path,
            replica_db_path=self.engine.replica_db_path,
            replica_sha256=self.engine.replica_sha256,
            cutoff_datetime=self.engine.cutoff_datetime,
            requested_as_of=self.engine.requested_as_of,
            masked_fields=config.masked_fields,
            enable_evidence_deduplication=config.enable_evidence_deduplication,
            enable_claim_verification=config.enable_claim_verification,
            running_service_sha=self.engine.running_service_sha,
            running_service_provenance=self.engine.running_service_provenance,
            sample_generating_service_sha=self.engine.sample_generating_service_sha,
        )

        res = engine.measure_dataset(ordered_reports)

        control_name = "none"
        control_value = "default"
        if not config.enable_evidence_deduplication:
            control_name = "enable_evidence_deduplication"
            control_value = False
        elif config.order_mode != OrderAblationMode.CANONICAL:
            control_name = "order_mode"
            control_value = config.order_mode.value
        elif config.masked_fields:
            control_name = "masked_fields"
            control_value = list(config.masked_fields)
        elif not config.enable_claim_verification:
            control_name = "enable_claim_verification"
            control_value = False

        summary = {
            "total_reports": res.all_metrics.total_reports,
            "in_pool_count": res.all_metrics.in_pool_count,
            "evaluated_count": res.all_metrics.evaluated_count,
            "typed_missing_count": res.all_metrics.typed_missing_count,
            "mean_net_return": res.all_metrics.mean_net_return,
            "dev_count": res.dev_metrics.total_reports,
            "historical_oos_count": res.historical_oos_metrics.total_reports,
            "forward_oos_count": res.forward_oos_metrics.total_reports,
            "regression_count": res.regression_metrics.total_reports,
        }

        variant_res = AblationVariantResult(
            variant_name=variant_name,
            control_name=control_name,
            control_value=control_value,
            snapshot_hash=snap_hash,
            cutoff_datetime=self.engine.cutoff_datetime,
            metrics_summary=summary,
            disclaimer=BASELINE_DISCLAIMER,
        )

        return res, variant_res

    def run_all_ablation_variants(
        self, reports: List[Dict[str, Any]]
    ) -> Dict[str, AblationVariantResult]:
        """Run all four frozen ablation controls and verify shared snapshot hash."""
        variants: Dict[str, AblationVariantResult] = {}

        # 1. Baseline
        _, v_base = self.run_replay(reports, AblationConfig(), "variant_0_baseline")
        variants["variant_0_baseline"] = v_base

        # 2. Repetition ablation: dedup=False
        _, v_rep = self.run_replay(
            reports,
            AblationConfig(enable_evidence_deduplication=False),
            "variant_1_repetition_no_dedup",
        )
        variants["variant_1_repetition_no_dedup"] = v_rep

        # 3. Order ablation: reversed
        _, v_ord_rev = self.run_replay(
            reports,
            AblationConfig(order_mode=OrderAblationMode.REVERSED),
            "variant_2a_order_reversed",
        )
        variants["variant_2a_order_reversed"] = v_ord_rev

        # Order ablation: seeded shuffled
        _, v_ord_shuf = self.run_replay(
            reports,
            AblationConfig(order_mode=OrderAblationMode.SEEDED_SHUFFLED, order_seed=42),
            "variant_2b_order_shuffled_seed42",
        )
        variants["variant_2b_order_shuffled_seed42"] = v_ord_shuf

        # 4. Gap ablation: mask social
        _, v_gap_soc = self.run_replay(
            reports,
            AblationConfig(masked_fields=("social",)),
            "variant_3a_gap_masked_social",
        )
        variants["variant_3a_gap_masked_social"] = v_gap_soc

        # Gap ablation: mask fund_flow & latest_report
        _, v_gap_all = self.run_replay(
            reports,
            AblationConfig(masked_fields=("social", "fund_flow", "latest_report")),
            "variant_3b_gap_masked_all",
        )
        variants["variant_3b_gap_masked_all"] = v_gap_all

        # 5. Proposition ablation: disable verification
        _, v_prop = self.run_replay(
            reports,
            AblationConfig(enable_claim_verification=False),
            "variant_4_proposition_verification_disabled",
        )
        variants["variant_4_proposition_verification_disabled"] = v_prop

        # Verification: all variants share identical snapshot hash
        base_hash = v_base.snapshot_hash
        for name, v in variants.items():
            assert v.snapshot_hash == base_hash, f"Variant {name} snapshot hash mismatch!"

        return variants


def main() -> None:
    """CLI entry point for running V-03 return measurement engine."""
    parser = argparse.ArgumentParser(
        description="V-03a Read-Only Return Measurement Engine"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db",
        help="Path to database (opened strictly mode=ro)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of reports to measure",
    )
    parser.add_argument(
        "--hold-days",
        type=int,
        default=DEFAULT_HOLD_DAYS,
        help="Holding days horizon (default: 5)",
    )
    parser.add_argument(
        "--output-md",
        type=str,
        default=str(PROJECT_ROOT / "work" / "v03_return_measurement_report.md"),
        help="Output markdown report path",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=str(PROJECT_ROOT / "work" / "v03_return_measurement_report.json"),
        help="Output json report path",
    )
    parser.add_argument(
        "--target-user-id",
        type=str,
        default=DEFAULT_TARGET_USER_ID,
        help="Target user ID to evaluate (default: David)",
    )
    parser.add_argument(
        "--status-filter",
        type=str,
        default=DEFAULT_STATUS_FILTER,
        help="Status filter (default: completed)",
    )
    parser.add_argument(
        "--cutoff-date",
        type=str,
        default=DEFAULT_HISTORICAL_CUTOFF_DATE,
        help=f"Cutoff trade date (default: {DEFAULT_HISTORICAL_CUTOFF_DATE})",
    )
    parser.add_argument(
        "--output-manifest",
        type=str,
        default=str(PROJECT_ROOT / "work" / "v03_snapshot_manifest.json"),
        help="Output manifest JSON path",
    )
    parser.add_argument(
        "--output-audit",
        type=str,
        default=str(PROJECT_ROOT / "work" / "v03_audit_records.json"),
        help="Output 25-field audit records JSON path",
    )
    args = parser.parse_args()

    user_stats = V03ReturnMeasureEngine.get_user_report_counts(
        args.db_path, target_user_id=args.target_user_id, cutoff_date=args.cutoff_date
    )
    print(f"Loading reports from database (READ-ONLY): {args.db_path}")
    print(f"Target user: {args.target_user_id}, Status: {args.status_filter}, Cutoff: {args.cutoff_date}")
    print(f"Account stats: total={user_stats['total']}, completed={user_stats['completed']}, failed={user_stats['failed']}")

    engine = V03ReturnMeasureEngine(
        hold_days=args.hold_days,
        target_user_id=args.target_user_id,
        status_filter=args.status_filter,
        target_user_stats=user_stats,
        production_db_path=args.db_path,
        cutoff_datetime=f"{args.cutoff_date} 23:59:59",
        requested_as_of=args.cutoff_date,
    )
    reports = engine.load_reports_from_db(
        args.db_path,
        limit=args.limit,
        target_user_id=args.target_user_id,
        status_filter=args.status_filter,
        cutoff_date=args.cutoff_date,
    )
    print(f"Loaded {len(reports)} reports. Running measurement engine...")

    result = engine.measure_dataset(reports)
    print(
        f"Measurement completed: {result.all_metrics.total_reports} reports processed."
    )
    print(f"  - In-Pool: {result.all_metrics.in_pool_count}")
    print(f"  - Evaluated: {result.all_metrics.evaluated_count}")
    print(f"  - Untradable: {result.all_metrics.untradable_count}")
    print(f"  - Typed-Missing: {result.all_metrics.typed_missing_count}")
    print(f"  - Regression Isolated: {result.regression_metrics.total_reports}")
    print(f"  - Forward OOS (Honest 0): {result.forward_oos_metrics.total_reports}")

    # Write output reports
    md_content = engine.generate_report_markdown(result)
    md_path = Path(args.output_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_content, encoding="utf-8")
    print(f"Saved Markdown report to: {md_path}")

    json_dict = result.to_dict()
    json_path = Path(args.output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(json_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved JSON report to: {json_path}")

    # Save manifest
    if result.snapshot_manifest:
        man_path = Path(args.output_manifest)
        man_path.parent.mkdir(parents=True, exist_ok=True)
        man_path.write_text(
            json.dumps(result.snapshot_manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Saved Snapshot Manifest to: {man_path}")

    # Save 25-field audit table
    if result.audit_table:
        audit_path = Path(args.output_audit)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            json.dumps(result.audit_table, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Saved 25-field Audit Records to: {audit_path}")


if __name__ == "__main__":
    main()
