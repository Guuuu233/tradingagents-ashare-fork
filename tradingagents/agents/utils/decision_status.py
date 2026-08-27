"""Deterministic decision / run status vocabulary (D-009 P0-1).

Lifecycle ``reports.status`` (pending/running/completed/failed) remains the job
lifecycle. These fields describe *analysis validity* and *trade action* and must
not be collapsed into Neutral/HOLD.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, MutableMapping, Optional

# analysis_status
ANALYSIS_VALID = "VALID"
ANALYSIS_PARTIAL = "PARTIAL"
ANALYSIS_ABSTAIN = "ABSTAIN"
ANALYSIS_INVALID_RUN = "INVALID_RUN"
ANALYSIS_DATA_ERROR = "DATA_ERROR"

ANALYSIS_STATUSES = frozenset(
    {
        ANALYSIS_VALID,
        ANALYSIS_PARTIAL,
        ANALYSIS_ABSTAIN,
        ANALYSIS_INVALID_RUN,
        ANALYSIS_DATA_ERROR,
    }
)

# direction
DIRECTION_BULL = "BULL"
DIRECTION_BEAR = "BEAR"
DIRECTION_NEUTRAL = "NEUTRAL"
DIRECTION_NA = "N/A"

DIRECTIONS = frozenset(
    {DIRECTION_BULL, DIRECTION_BEAR, DIRECTION_NEUTRAL, DIRECTION_NA}
)

# trade_action
ACTION_BUY = "BUY"
ACTION_SELL = "SELL"
ACTION_HOLD = "HOLD"
ACTION_WAIT = "WAIT"
ACTION_NO_TRADE = "NO_TRADE"

TRADE_ACTIONS = frozenset(
    {ACTION_BUY, ACTION_SELL, ACTION_HOLD, ACTION_WAIT, ACTION_NO_TRADE}
)

# risk_status
RISK_OK = "OK"
RISK_ELEVATED = "ELEVATED"
RISK_BLOCKED = "BLOCKED"
RISK_UNKNOWN = "UNKNOWN"

RISK_STATUSES = frozenset({RISK_OK, RISK_ELEVATED, RISK_BLOCKED, RISK_UNKNOWN})

# confirmation_state
CONFIRM_CONFIRMED = "CONFIRMED"
CONFIRM_PARTIAL = "PARTIAL"
CONFIRM_UNRESOLVED = "UNRESOLVED"

CONFIRMATION_STATES = frozenset(
    {CONFIRM_CONFIRMED, CONFIRM_PARTIAL, CONFIRM_UNRESOLVED}
)

# analysis_status values that must never enter calibration / directional backtest
NON_ELIGIBLE_ANALYSIS_STATUSES = frozenset(
    {
        ANALYSIS_INVALID_RUN,
        ANALYSIS_DATA_ERROR,
        ANALYSIS_ABSTAIN,
        ANALYSIS_PARTIAL,
    }
)

# trade actions that are not directional market views
NON_DIRECTIONAL_TRADE_ACTIONS = frozenset({ACTION_WAIT, ACTION_NO_TRADE})


@dataclass
class DecisionStatus:
    analysis_status: str
    direction: str
    trade_action: str
    risk_status: str
    confirmation_state: str = CONFIRM_UNRESOLVED
    failure_class: Optional[str] = None
    reason_codes: list[str] = field(default_factory=list)
    confidence: Optional[int] = None
    probability: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def invalid_run_status(
    *,
    failure_class: str = ANALYSIS_DATA_ERROR,
    reason_codes: Optional[list[str]] = None,
    risk_status: str = RISK_UNKNOWN,
) -> DecisionStatus:
    """Build the canonical INVALID_RUN → NO_TRADE payload.

    ``analysis_status`` is always INVALID_RUN; ``failure_class`` carries the
    diagnostic label (typically DATA_ERROR) without collapsing the run into a
    Neutral/HOLD market view.
    """
    fc = failure_class or ANALYSIS_DATA_ERROR
    return DecisionStatus(
        analysis_status=ANALYSIS_INVALID_RUN,
        direction=DIRECTION_NA,
        trade_action=ACTION_NO_TRADE,
        risk_status=risk_status,
        confirmation_state=CONFIRM_UNRESOLVED,
        failure_class=fc,
        reason_codes=list(reason_codes or []),
        confidence=None,
        probability=None,
    )


def abstain_status(
    *,
    reason_codes: Optional[list[str]] = None,
    trade_action: str = ACTION_NO_TRADE,
    risk_status: str = RISK_BLOCKED,
) -> DecisionStatus:
    """Data partially usable but direction must not be asserted as Neutral."""
    action = trade_action if trade_action in TRADE_ACTIONS else ACTION_NO_TRADE
    if action not in NON_DIRECTIONAL_TRADE_ACTIONS:
        action = ACTION_NO_TRADE
    return DecisionStatus(
        analysis_status=ANALYSIS_ABSTAIN,
        direction=DIRECTION_NA,
        trade_action=action,
        risk_status=risk_status,
        confirmation_state=CONFIRM_UNRESOLVED,
        failure_class=None,
        reason_codes=list(reason_codes or ["direction_not_decidable"]),
        confidence=None,
        probability=None,
    )


def valid_status(
    *,
    direction: str,
    trade_action: str,
    risk_status: str = RISK_OK,
    confirmation_state: str = CONFIRM_CONFIRMED,
    confidence: Optional[int] = None,
    probability: Optional[float] = None,
    reason_codes: Optional[list[str]] = None,
) -> DecisionStatus:
    dir_norm = direction if direction in DIRECTIONS else DIRECTION_NEUTRAL
    act_norm = trade_action if trade_action in TRADE_ACTIONS else ACTION_HOLD
    return DecisionStatus(
        analysis_status=ANALYSIS_VALID,
        direction=dir_norm,
        trade_action=act_norm,
        risk_status=risk_status if risk_status in RISK_STATUSES else RISK_OK,
        confirmation_state=confirmation_state
        if confirmation_state in CONFIRMATION_STATES
        else CONFIRM_CONFIRMED,
        failure_class=None,
        reason_codes=list(reason_codes or []),
        confidence=confidence,
        probability=probability,
    )


def partial_status(
    *,
    reason_codes: Optional[list[str]] = None,
    failed_analysts: Optional[list[str]] = None,
    trade_action: str = ACTION_NO_TRADE,
    risk_status: str = RISK_ELEVATED,
    direction: str = DIRECTION_NA,
) -> DecisionStatus:
    """Some required analysts failed; direction must not be treated as eligible Neutral."""
    codes = list(reason_codes or [])
    if failed_analysts:
        codes.append(f"failed_analysts:{','.join(failed_analysts)}")
    action = trade_action if trade_action in TRADE_ACTIONS else ACTION_NO_TRADE
    # PARTIAL runs are not calibration-eligible; prefer non-directional actions.
    if action not in NON_DIRECTIONAL_TRADE_ACTIONS and action != ACTION_HOLD:
        action = ACTION_NO_TRADE
    return DecisionStatus(
        analysis_status=ANALYSIS_PARTIAL,
        direction=direction if direction in DIRECTIONS else DIRECTION_NA,
        trade_action=action,
        risk_status=risk_status if risk_status in RISK_STATUSES else RISK_ELEVATED,
        confirmation_state=CONFIRM_PARTIAL,
        failure_class=None,
        reason_codes=codes,
        confidence=None,
        probability=None,
    )


_DIRECTION_FROM_VERDICT: dict[str, str] = {
    "看多": DIRECTION_BULL,
    "偏多": DIRECTION_BULL,
    "BULL": DIRECTION_BULL,
    "BULLISH": DIRECTION_BULL,
    "BUY": DIRECTION_BULL,
    "看空": DIRECTION_BEAR,
    "偏空": DIRECTION_BEAR,
    "BEAR": DIRECTION_BEAR,
    "BEARISH": DIRECTION_BEAR,
    "SELL": DIRECTION_BEAR,
    "中性": DIRECTION_NEUTRAL,
    "NEUTRAL": DIRECTION_NEUTRAL,
    "HOLD": DIRECTION_NEUTRAL,
    "观望": DIRECTION_NA,
    "N/A": DIRECTION_NA,
    "NA": DIRECTION_NA,
}


def map_verdict_direction(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return DIRECTION_NA
    # Prefer exact Chinese keys via original casing first
    orig = str(raw or "").strip()
    if orig in _DIRECTION_FROM_VERDICT:
        return _DIRECTION_FROM_VERDICT[orig]
    if text in _DIRECTION_FROM_VERDICT:
        return _DIRECTION_FROM_VERDICT[text]
    return DIRECTION_NA


def map_verdict_trade_action(
    *,
    direction: str,
    winner: Any = None,
    position_pct: Any = None,
) -> str:
    """Map a successful manager verdict into BUY/SELL/HOLD (not WAIT/NO_TRADE)."""
    dir_norm = direction if direction in DIRECTIONS else map_verdict_direction(direction)
    if dir_norm == DIRECTION_BULL:
        return ACTION_BUY
    if dir_norm == DIRECTION_BEAR:
        return ACTION_SELL
    if dir_norm == DIRECTION_NEUTRAL:
        return ACTION_HOLD
    return ACTION_NO_TRADE


def status_from_manager_verdict(
    manager_verdict: Mapping[str, Any] | None,
    *,
    prior_analysis_status: Optional[str] = None,
) -> DecisionStatus:
    """Build canonical status for a completed Research Manager path."""
    mv = manager_verdict if isinstance(manager_verdict, Mapping) else {}
    nested = mv.get("decision_status")
    if isinstance(nested, Mapping):
        from_nested = decision_status_from_mapping(nested)
        if from_nested is not None:
            # Nested ABSTAIN/INVALID from earlier gates wins.
            if from_nested.analysis_status in {
                ANALYSIS_INVALID_RUN,
                ANALYSIS_DATA_ERROR,
                ANALYSIS_ABSTAIN,
            }:
                return from_nested

    # Consistency hard gate: never emit VALID/BUY when the plan is blocked.
    if mv.get("consistency_check_passed") is False:
        failed = [str(x) for x in (mv.get("failed_checks") or []) if x]
        return abstain_status(
            reason_codes=["manager_consistency_hard_gate", *failed],
            trade_action=ACTION_NO_TRADE,
            risk_status=RISK_BLOCKED,
        )

    direction = map_verdict_direction(mv.get("direction"))
    trade_action = map_verdict_trade_action(
        direction=direction,
        winner=mv.get("winner"),
        position_pct=mv.get("position_pct"),
    )
    if prior_analysis_status == ANALYSIS_PARTIAL:
        return partial_status(
            reason_codes=["prior_partial_analyst_failures"],
            trade_action=ACTION_NO_TRADE
            if trade_action not in NON_DIRECTIONAL_TRADE_ACTIONS
            else trade_action,
            direction=DIRECTION_NA,
        )
    if direction == DIRECTION_NA and trade_action in NON_DIRECTIONAL_TRADE_ACTIONS:
        return abstain_status(reason_codes=["manager_direction_na"])
    return valid_status(
        direction=direction if direction != DIRECTION_NA else DIRECTION_NEUTRAL,
        trade_action=trade_action if trade_action != ACTION_NO_TRADE else ACTION_HOLD,
        risk_status=RISK_OK,
        confirmation_state=CONFIRM_CONFIRMED,
        reason_codes=["manager_terminal"],
    )


def aggregate_horizon_decision_statuses(
    horizon_payloads: Mapping[str, Mapping[str, Any]] | None,
    *,
    requested_horizons: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Aggregate short/medium statuses into top-level canonical fields.

    Semantics:
    - all_invalid: every requested completed horizon is INVALID/DATA_ERROR/ABSTAIN
      → top INVALID_RUN + NO_TRADE
    - mixed: some eligible VALID and some non-VALID → PARTIAL + NO_TRADE
    - all_valid: every completed horizon VALID → promote primary VALID status
    """
    from tradingagents.agents.utils.decision_status import decision_status_from_state

    requested = list(requested_horizons or [])
    if not requested and isinstance(horizon_payloads, Mapping):
        requested = [k for k in ("short", "medium") if k in horizon_payloads]

    statuses: list[DecisionStatus] = []
    by_horizon: dict[str, dict[str, Any]] = {}
    for horizon in requested:
        payload = (horizon_payloads or {}).get(horizon) or {}
        if not isinstance(payload, Mapping):
            continue
        if payload.get("status") in {"failed", "not_requested"}:
            # Treat failed horizon as INVALID for aggregation.
            st = invalid_run_status(
                reason_codes=[f"horizon_{horizon}_failed"],
            )
        else:
            st = decision_status_from_state(payload) or resolve_soft(
                payload
            )
        statuses.append(st)
        by_horizon[horizon] = st.to_dict()

    if not statuses:
        top = invalid_run_status(reason_codes=["dual_horizon_no_status"])
        return {
            "aggregation": "all_invalid",
            "decision_status": top.to_dict(),
            "analysis_status": top.analysis_status,
            "trade_action": top.trade_action,
            "direction": top.direction,
            "risk_status": top.risk_status,
            "by_horizon": by_horizon,
        }

    non_exec = {
        ANALYSIS_INVALID_RUN,
        ANALYSIS_DATA_ERROR,
        ANALYSIS_ABSTAIN,
    }
    all_invalid = all(s.analysis_status in non_exec for s in statuses)
    all_valid = all(s.analysis_status == ANALYSIS_VALID for s in statuses)

    if all_invalid:
        top = invalid_run_status(reason_codes=["dual_horizon_all_invalid"])
        aggregation = "all_invalid"
    elif all_valid:
        top = statuses[0]
        aggregation = "all_valid"
    else:
        top = partial_status(
            reason_codes=["dual_horizon_mixed"],
            trade_action=ACTION_NO_TRADE,
        )
        aggregation = "mixed"

    return {
        "aggregation": aggregation,
        "decision_status": top.to_dict(),
        "analysis_status": top.analysis_status,
        "trade_action": top.trade_action,
        "direction": top.direction,
        "risk_status": top.risk_status,
        "by_horizon": by_horizon,
    }


def resolve_soft(payload: Mapping[str, Any]) -> DecisionStatus:
    """Fallback when a horizon payload lacks decision_status."""
    from tradingagents.agents.utils.run_integrity import evaluate_run_integrity
    from tradingagents.agents.utils.decision_status import (
        abstain_status,
        decision_status_from_mapping,
    )

    integrity = evaluate_run_integrity(payload)
    if integrity.decision_status:
        parsed = decision_status_from_mapping(integrity.decision_status)
        if parsed is not None:
            return parsed
    return abstain_status(
        reason_codes=["horizon_status_missing_abstain"],
        trade_action=ACTION_NO_TRADE,
        risk_status=RISK_UNKNOWN,
    )


def db_direction_from_canonical(
    canonical: Mapping[str, Any] | None,
    *,
    fallback: Optional[str] = None,
) -> Optional[str]:
    """Persist direction consistent with analysis_status / trade_action.

    D-009: BULL + Risk BLOCKED -> keep BULL direction, trade_action NO_TRADE.
    Only INVALID_RUN / DATA_ERROR / ABSTAIN collapse direction to N/A.
    """
    if not isinstance(canonical, Mapping):
        return fallback
    analysis_status = str(canonical.get("analysis_status") or "").upper()
    direction = str(canonical.get("direction") or "").upper()
    if analysis_status in {
        ANALYSIS_INVALID_RUN,
        ANALYSIS_DATA_ERROR,
        ANALYSIS_ABSTAIN,
    } or direction in {DIRECTION_NA, "N/A", "NA", ""}:
        return DIRECTION_NA
    display = {
        DIRECTION_BULL: "看多",
        DIRECTION_BEAR: "看空",
        DIRECTION_NEUTRAL: "中性",
        DIRECTION_NA: DIRECTION_NA,
    }
    if direction in display:
        return display[direction]
    if direction in {"看多", "偏多", "看空", "偏空", "中性", "N/A"}:
        return direction
    return fallback


def status_from_risk_verdict(
    *,
    upstream: Optional[DecisionStatus],
    risk_verdict: str,
    reason_codes: Optional[list[str]] = None,
    retry_count: int = 0,
    max_retries: int = 1,
    retry_exhausted: Optional[bool] = None,
) -> DecisionStatus:
    """Rewrite canonical status at Risk Judge terminal — never inherit stale BUY blindly.

    - upstream non-executable → keep non-executable, force risk BLOCKED
    - revise (retry not exhausted) → keep upstream analysis_status (VALID) & direction & trade_action, risk_status ELEVATED
    - revise (retry exhausted) → keep direction, trade_action NO_TRADE, risk_status BLOCKED
    - reject/blocked → keep upstream analysis_status & direction, trade_action NO_TRADE, risk_status BLOCKED
    - pass/approve → keep upstream direction/action if VALID; else ABSTAIN
    """
    verdict = str(risk_verdict or "").strip().lower()
    codes = list(reason_codes or [])
    codes.append(f"risk_verdict:{verdict or 'unknown'}")

    if upstream is not None and is_non_executable_status(upstream):
        return DecisionStatus(
            analysis_status=upstream.analysis_status,
            direction=upstream.direction if upstream.analysis_status == ANALYSIS_VALID else DIRECTION_NA,
            trade_action=ACTION_NO_TRADE
            if upstream.trade_action not in NON_DIRECTIONAL_TRADE_ACTIONS
            else upstream.trade_action,
            risk_status=RISK_BLOCKED,
            confirmation_state=CONFIRM_UNRESOLVED,
            failure_class=upstream.failure_class,
            reason_codes=list(upstream.reason_codes) + codes,
            confidence=None,
            probability=None,
        )

    is_exhausted = (
        retry_exhausted
        if retry_exhausted is not None
        else (retry_count > max_retries if retry_count > 0 else False)
    )

    if verdict in {"reject", "blocked", "fail", "failed"}:
        if upstream is not None and upstream.analysis_status == ANALYSIS_VALID:
            return DecisionStatus(
                analysis_status=ANALYSIS_VALID,
                direction=upstream.direction,
                trade_action=ACTION_NO_TRADE,
                risk_status=RISK_BLOCKED,
                confirmation_state=upstream.confirmation_state,
                failure_class=None,
                reason_codes=list(upstream.reason_codes) + codes,
                confidence=None,
                probability=None,
            )
        return abstain_status(
            reason_codes=codes,
            trade_action=ACTION_NO_TRADE,
            risk_status=RISK_BLOCKED,
        )

    if verdict == "revise":
        if is_exhausted:
            dir_val = upstream.direction if upstream is not None and upstream.analysis_status == ANALYSIS_VALID else DIRECTION_NA
            analysis_val = upstream.analysis_status if upstream is not None else ANALYSIS_ABSTAIN
            return DecisionStatus(
                analysis_status=analysis_val,
                direction=dir_val,
                trade_action=ACTION_NO_TRADE,
                risk_status=RISK_BLOCKED,
                confirmation_state=CONFIRM_UNRESOLVED,
                failure_class=None,
                reason_codes=list(upstream.reason_codes if upstream else []) + codes + ["risk_retries_exhausted"],
                confidence=None,
                probability=None,
            )
        # revise with retries remaining: keep upstream VALID status, direction, and trade_action proposal
        if upstream is not None and upstream.analysis_status == ANALYSIS_VALID:
            return DecisionStatus(
                analysis_status=ANALYSIS_VALID,
                direction=upstream.direction,
                trade_action=upstream.trade_action,
                risk_status=RISK_ELEVATED,
                confirmation_state=CONFIRM_PARTIAL,
                failure_class=None,
                reason_codes=list(upstream.reason_codes) + codes,
                confidence=upstream.confidence,
                probability=upstream.probability,
            )
        return DecisionStatus(
            analysis_status=ANALYSIS_ABSTAIN,
            direction=DIRECTION_NA,
            trade_action=ACTION_WAIT,
            risk_status=RISK_ELEVATED,
            confirmation_state=CONFIRM_PARTIAL,
            failure_class=None,
            reason_codes=codes,
            confidence=None,
            probability=None,
        )

    # pass / approve / empty → promote upstream VALID if present
    if upstream is not None and upstream.analysis_status == ANALYSIS_VALID:
        return DecisionStatus(
            analysis_status=ANALYSIS_VALID,
            direction=upstream.direction,
            trade_action=upstream.trade_action,
            risk_status=RISK_OK if verdict in {"pass", "approve", "approved", ""} else RISK_ELEVATED,
            confirmation_state=CONFIRM_CONFIRMED,
            failure_class=None,
            reason_codes=list(upstream.reason_codes) + codes,
            confidence=upstream.confidence,
            probability=upstream.probability,
        )

    return abstain_status(
        reason_codes=codes + ["risk_missing_valid_upstream"],
        trade_action=ACTION_NO_TRADE,
        risk_status=RISK_UNKNOWN,
    )


def is_calibration_eligible(
    result_or_row: Mapping[str, Any] | Any,
) -> bool:
    """True only for explicit VALID runs with a directional trade action and probability.

    Legacy rows with ``analysis_status=NULL`` are excluded so pre-P0 Neutral/HOLD
    pollution cannot keep entering calibration.
    """
    analysis_status, trade_action, probability = _extract_status_fields(result_or_row)
    if analysis_status is None:
        return False
    if analysis_status != ANALYSIS_VALID:
        return False
    if trade_action in NON_DIRECTIONAL_TRADE_ACTIONS:
        return False
    return probability is not None


def _extract_status_fields(
    result_or_row: Mapping[str, Any] | Any,
) -> tuple[Optional[str], Optional[str], Optional[float]]:
    if isinstance(result_or_row, Mapping):
        analysis_status = result_or_row.get("analysis_status")
        trade_action = result_or_row.get("trade_action")
        probability = result_or_row.get("probability")
        nested = result_or_row.get("decision_status")
        if isinstance(nested, Mapping):
            analysis_status = analysis_status or nested.get("analysis_status")
            trade_action = trade_action or nested.get("trade_action")
            if probability is None:
                probability = nested.get("probability")
        # Also peek result_data if this looks like a row wrapper
        rd = result_or_row.get("result_data")
        if isinstance(rd, Mapping):
            analysis_status = analysis_status or rd.get("analysis_status")
            trade_action = trade_action or rd.get("trade_action")
            if probability is None:
                probability = rd.get("probability")
            nested2 = rd.get("decision_status")
            if isinstance(nested2, Mapping):
                analysis_status = analysis_status or nested2.get("analysis_status")
                trade_action = trade_action or nested2.get("trade_action")
        return (
            str(analysis_status).strip().upper() if analysis_status else None,
            str(trade_action).strip().upper() if trade_action else None,
            float(probability) if isinstance(probability, (int, float)) else None,
        )

    analysis_status = getattr(result_or_row, "analysis_status", None)
    trade_action = getattr(result_or_row, "trade_action", None)
    probability = getattr(result_or_row, "probability", None)
    rd = getattr(result_or_row, "result_data", None)
    if isinstance(rd, Mapping):
        analysis_status = analysis_status or rd.get("analysis_status")
        trade_action = trade_action or rd.get("trade_action")
        if probability is None:
            probability = rd.get("probability")
        nested = rd.get("decision_status")
        if isinstance(nested, Mapping):
            analysis_status = analysis_status or nested.get("analysis_status")
            trade_action = trade_action or nested.get("trade_action")
    return (
        str(analysis_status).strip().upper() if analysis_status else None,
        str(trade_action).strip().upper() if trade_action else None,
        float(probability) if isinstance(probability, (int, float)) else None,
    )


def apply_decision_status_to_result(
    result: MutableMapping[str, Any],
    status: DecisionStatus | Mapping[str, Any],
) -> MutableMapping[str, Any]:
    """Write decision_status into result_data and null unsafe numeric fields."""
    payload = status.to_dict() if isinstance(status, DecisionStatus) else dict(status)
    analysis_status = str(payload.get("analysis_status") or "").upper()
    trade_action = str(payload.get("trade_action") or "").upper()
    direction = str(payload.get("direction") or DIRECTION_NA)

    result["decision_status"] = payload
    result["analysis_status"] = analysis_status
    result["trade_action"] = trade_action
    result["risk_status"] = str(payload.get("risk_status") or RISK_UNKNOWN).upper()
    result["confirmation_state"] = str(
        payload.get("confirmation_state") or CONFIRM_UNRESOLVED
    ).upper()
    if payload.get("failure_class"):
        result["failure_class"] = payload.get("failure_class")
    if payload.get("reason_codes") is not None:
        result["reason_codes"] = list(payload.get("reason_codes") or [])

    # Compat: lifecycle decision stores trade_action (includes NO_TRADE/WAIT).
    result["decision"] = trade_action
    result["direction"] = direction

    if analysis_status in {
        ANALYSIS_INVALID_RUN,
        ANALYSIS_DATA_ERROR,
        ANALYSIS_ABSTAIN,
        ANALYSIS_PARTIAL,
    } or trade_action in NON_DIRECTIONAL_TRADE_ACTIONS:
        result["confidence"] = None
        result["probability"] = None
        result["target_price"] = None
        result["stop_loss_price"] = None
        # Strip fabricated range keys if present
        for key in ("upside", "downside", "numeric_ranges", "odds"):
            if key in result:
                result[key] = None if key != "numeric_ranges" else []

    return result


def decision_status_from_mapping(
    raw: Mapping[str, Any] | None,
) -> Optional[DecisionStatus]:
    if not isinstance(raw, Mapping):
        return None
    analysis_status = str(raw.get("analysis_status") or "").upper()
    if analysis_status not in ANALYSIS_STATUSES:
        return None
    return DecisionStatus(
        analysis_status=analysis_status,
        direction=str(raw.get("direction") or DIRECTION_NA).upper()
        if str(raw.get("direction") or "").upper() in DIRECTIONS
        else DIRECTION_NA,
        trade_action=str(raw.get("trade_action") or ACTION_NO_TRADE).upper()
        if str(raw.get("trade_action") or "").upper() in TRADE_ACTIONS
        else ACTION_NO_TRADE,
        risk_status=str(raw.get("risk_status") or RISK_UNKNOWN).upper()
        if str(raw.get("risk_status") or "").upper() in RISK_STATUSES
        else RISK_UNKNOWN,
        confirmation_state=str(raw.get("confirmation_state") or CONFIRM_UNRESOLVED).upper()
        if str(raw.get("confirmation_state") or "").upper() in CONFIRMATION_STATES
        else CONFIRM_UNRESOLVED,
        failure_class=raw.get("failure_class"),
        reason_codes=list(raw.get("reason_codes") or []),
        confidence=raw.get("confidence"),
        probability=raw.get("probability"),
    )


def is_non_executable_status(status: DecisionStatus | Mapping[str, Any] | None) -> bool:
    """True when downstream Trader/Risk must not invent a directional plan."""
    if status is None:
        return False
    if isinstance(status, DecisionStatus):
        analysis_status = status.analysis_status
        trade_action = status.trade_action
    elif isinstance(status, Mapping):
        analysis_status = str(status.get("analysis_status") or "").upper()
        trade_action = str(status.get("trade_action") or "").upper()
    else:
        return False
    if analysis_status in {
        ANALYSIS_INVALID_RUN,
        ANALYSIS_DATA_ERROR,
        ANALYSIS_ABSTAIN,
    }:
        return True
    return trade_action in NON_DIRECTIONAL_TRADE_ACTIONS


def decision_status_from_state(
    state: Mapping[str, Any] | None,
) -> Optional[DecisionStatus]:
    """Read decision_status from graph state (top-level or manager_verdict)."""
    if not isinstance(state, Mapping):
        return None
    parsed = decision_status_from_mapping(
        state.get("decision_status") if isinstance(state.get("decision_status"), Mapping) else None
    )
    if parsed is not None:
        return parsed

    analysis_status = state.get("analysis_status")
    trade_action = state.get("trade_action")
    if analysis_status or trade_action:
        parsed = decision_status_from_mapping(
            {
                "analysis_status": analysis_status or ANALYSIS_ABSTAIN,
                "direction": state.get("direction") or DIRECTION_NA,
                "trade_action": trade_action or ACTION_NO_TRADE,
                "risk_status": state.get("risk_status") or RISK_UNKNOWN,
                "failure_class": state.get("failure_class"),
                "reason_codes": state.get("reason_codes") or [],
            }
        )
        if parsed is not None:
            return parsed

    for container_key in ("manager_verdict", "investment_debate_state"):
        container = state.get(container_key)
        if not isinstance(container, Mapping):
            continue
        nested = container.get("decision_status")
        if isinstance(nested, Mapping):
            parsed = decision_status_from_mapping(nested)
            if parsed is not None:
                return parsed
        if container_key == "investment_debate_state":
            mv = container.get("manager_verdict")
            if isinstance(mv, Mapping) and isinstance(mv.get("decision_status"), Mapping):
                parsed = decision_status_from_mapping(mv.get("decision_status"))
                if parsed is not None:
                    return parsed
    return None
