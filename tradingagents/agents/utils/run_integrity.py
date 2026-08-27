"""Run-level analyst integrity checks (D-009 P0-1).

Detects failed / empty / degraded analyst reports so the graph and API can emit
INVALID_RUN / DATA_ERROR instead of Neutral/HOLD.

Failure detection prefers whole-report failure wrappers and short stubs.
Substring matches such as ``【数据获取失败】`` inside long disclosure text must
NOT mark an otherwise valid analyst report as failed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, MutableMapping, Optional, Sequence

from tradingagents.agents.utils.decision_status import (
    ACTION_NO_TRADE,
    ANALYSIS_DATA_ERROR,
    ANALYSIS_INVALID_RUN,
    ANALYSIS_PARTIAL,
    DIRECTION_NA,
    DecisionStatus,
    abstain_status,
    apply_decision_status_to_result,
    invalid_run_status,
    partial_status,
)

# Canonical roster (API / TradingAgentsGraph default).
DEFAULT_REQUIRED_ANALYSTS: tuple[str, ...] = (
    "market",
    "social",
    "news",
    "fundamentals",
    "macro",
    "smart_money",
    "volume_price",
)

# Analyst key → state / result_data report field
ANALYST_REPORT_FIELDS: dict[str, str] = {
    "market": "market_report",
    "social": "sentiment_report",
    "sentiment": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "macro": "macro_report",
    "smart_money": "smart_money_report",
    "volume_price": "volume_price_report",
}

# Trace agent name → analyst key
_TRACE_AGENT_TO_KEY: dict[str, str] = {
    "market_analyst": "market",
    "market": "market",
    "social_analyst": "social",
    "social": "social",
    "sentiment_analyst": "social",
    "news_analyst": "news",
    "news": "news",
    "fundamentals_analyst": "fundamentals",
    "fundamentals": "fundamentals",
    "macro_analyst": "macro",
    "macro": "macro",
    "smart_money_analyst": "smart_money",
    "smart_money": "smart_money",
    "volume_price_analyst": "volume_price",
    "volume_price": "volume_price",
}

# Whole-report failure wrappers produced by analyst nodes on exception.
_WHOLE_REPORT_FAILURE_PREFIXES: tuple[str, ...] = (
    "分析报告生成失败",
)

# Short degraded stubs (entire body is the stub, not a long report with a gap note).
_SHORT_STUB_MAX_CHARS = 220
_SHORT_STUB_MARKERS: tuple[str, ...] = (
    "生成异常（输出退化）",
    "本项不可用",
    "调用失败：",
    "调用失败:",
)

_TRACE_FAILURE_MARKERS: tuple[str, ...] = (
    "分析报告生成失败",
    "生成异常（输出退化）",
    "本项不可用",
)


@dataclass
class AnalystReportAssessment:
    analyst_key: str
    report_field: str
    failed: bool
    reason: Optional[str] = None
    length: int = 0
    source: str = "report"  # report | trace | empty


@dataclass
class RunIntegrity:
    required_analysts: list[str]
    assessments: list[AnalystReportAssessment]
    failed_required: list[str]
    available_required: list[str]
    failed_required_count: int
    required_count: int
    all_required_failed: bool
    analysis_status: Optional[str]
    failure_class: Optional[str]
    reason_codes: list[str] = field(default_factory=list)
    decision_status: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["assessments"] = [asdict(a) for a in self.assessments]
        return payload


def is_failed_analyst_report(text: Any) -> tuple[bool, Optional[str]]:
    """Return (failed, reason) for a single analyst report body.

    Strict rules:
    - empty → failed
    - body starts with whole-report failure prefix → failed
    - short stub containing degraded/unavailable markers → failed
    - long reports that merely disclose a local ``【数据获取失败】`` gap → NOT failed
    """
    if text is None:
        return True, "empty_report"
    raw = str(text).strip()
    if not raw:
        return True, "empty_report"
    for prefix in _WHOLE_REPORT_FAILURE_PREFIXES:
        if raw.startswith(prefix):
            return True, f"prefix:{prefix}"
    if len(raw) <= _SHORT_STUB_MAX_CHARS:
        for marker in _SHORT_STUB_MARKERS:
            if marker in raw:
                return True, f"stub:{marker}"
        # Extremely short unavailable stubs without the longer markers
        if len(raw) < 40 and ("不可用" in raw or "失败" in raw):
            return True, "short_unavailable_stub"
    return False, None


def is_failed_analyst_trace(trace: Mapping[str, Any] | None) -> tuple[bool, Optional[str]]:
    """Structured trace hint: only when finding/verdict themselves are failure stubs."""
    if not isinstance(trace, Mapping):
        return False, None
    blob = " ".join(
        str(trace.get(key) or "")
        for key in ("key_finding", "verdict", "error", "status")
    ).strip()
    if not blob:
        return False, None
    if len(blob) > _SHORT_STUB_MAX_CHARS:
        # Long findings that mention a gap are not whole-analyst failures.
        for prefix in _WHOLE_REPORT_FAILURE_PREFIXES:
            if blob.startswith(prefix):
                return True, f"trace_prefix:{prefix}"
        return False, None
    for marker in _TRACE_FAILURE_MARKERS:
        if marker in blob:
            return True, f"trace:{marker}"
    return False, None


def normalize_required_analysts(
    selected: Optional[Sequence[str]] = None,
) -> list[str]:
    if not selected:
        return list(DEFAULT_REQUIRED_ANALYSTS)
    out: list[str] = []
    seen: set[str] = set()
    for item in selected:
        key = str(item or "").strip().lower()
        if key == "sentiment":
            key = "social"
        if not key or key in seen:
            continue
        if key not in ANALYST_REPORT_FIELDS:
            continue
        seen.add(key)
        out.append(key)
    return out or list(DEFAULT_REQUIRED_ANALYSTS)


def _traces_by_analyst(traces: Sequence[Any] | None) -> dict[str, Mapping[str, Any]]:
    by_key: dict[str, Mapping[str, Any]] = {}
    if not traces:
        return by_key
    for item in traces:
        if not isinstance(item, Mapping):
            continue
        agent = str(item.get("agent") or "").strip().lower()
        key = _TRACE_AGENT_TO_KEY.get(agent)
        if key and key not in by_key:
            by_key[key] = item
    return by_key


def assess_reports(
    reports: Mapping[str, Any],
    *,
    required_analysts: Optional[Sequence[str]] = None,
    analyst_traces: Optional[Sequence[Any]] = None,
) -> list[AnalystReportAssessment]:
    required = normalize_required_analysts(required_analysts)
    traces = _traces_by_analyst(analyst_traces)
    assessments: list[AnalystReportAssessment] = []
    for key in required:
        field_name = ANALYST_REPORT_FIELDS[key]
        content = reports.get(field_name, "")
        failed, reason = is_failed_analyst_report(content)
        source = "report"
        if not failed:
            # Structured trace as secondary signal only when report body is empty/short.
            # Prefer report body; never let a long valid report fail via mid-text gaps.
            trace_failed, trace_reason = is_failed_analyst_trace(traces.get(key))
            raw_len = len(str(content or "").strip())
            if trace_failed and raw_len <= _SHORT_STUB_MAX_CHARS:
                failed, reason, source = True, trace_reason, "trace"
        assessments.append(
            AnalystReportAssessment(
                analyst_key=key,
                report_field=field_name,
                failed=failed,
                reason=reason,
                length=len(str(content or "")),
                source=source if failed else "report",
            )
        )
    return assessments


def evaluate_run_integrity(
    reports: Mapping[str, Any],
    *,
    required_analysts: Optional[Sequence[str]] = None,
    analyst_traces: Optional[Sequence[Any]] = None,
) -> RunIntegrity:
    required = normalize_required_analysts(required_analysts)
    assessments = assess_reports(
        reports,
        required_analysts=required,
        analyst_traces=analyst_traces,
    )
    failed = [a.analyst_key for a in assessments if a.failed]
    available = [a.analyst_key for a in assessments if not a.failed]
    all_failed = bool(required) and len(failed) == len(required)
    partial = bool(failed) and not all_failed
    reason_codes: list[str] = []
    analysis_status: Optional[str] = None
    failure_class: Optional[str] = None
    decision: Optional[DecisionStatus] = None

    if all_failed:
        reason_codes.append(f"analyst_upstream_{len(failed)}_of_{len(required)}_failed")
        for a in assessments:
            if a.reason:
                reason_codes.append(f"{a.analyst_key}:{a.reason}")
        failure_class = ANALYSIS_DATA_ERROR
        analysis_status = ANALYSIS_INVALID_RUN
        decision = invalid_run_status(
            failure_class=ANALYSIS_DATA_ERROR,
            reason_codes=reason_codes,
        )
    elif partial:
        reason_codes.append(
            f"analyst_upstream_{len(failed)}_of_{len(required)}_partial"
        )
        for a in assessments:
            if a.failed and a.reason:
                reason_codes.append(f"{a.analyst_key}:{a.reason}")
        analysis_status = ANALYSIS_PARTIAL
        decision = partial_status(
            reason_codes=reason_codes,
            failed_analysts=failed,
        )

    return RunIntegrity(
        required_analysts=required,
        assessments=assessments,
        failed_required=failed,
        available_required=available,
        failed_required_count=len(failed),
        required_count=len(required),
        all_required_failed=all_failed,
        analysis_status=analysis_status,
        failure_class=failure_class,
        reason_codes=reason_codes,
        decision_status=decision.to_dict() if decision else None,
    )


def reports_from_state(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "market_report": state.get("market_report", ""),
        "sentiment_report": state.get("sentiment_report", ""),
        "news_report": state.get("news_report", ""),
        "fundamentals_report": state.get("fundamentals_report", ""),
        "macro_report": state.get("macro_report", ""),
        "smart_money_report": state.get("smart_money_report", ""),
        "volume_price_report": state.get("volume_price_report", ""),
    }


def required_analysts_from_state(state: Mapping[str, Any]) -> list[str]:
    selected = state.get("selected_analysts")
    if not selected:
        wf = state.get("workflow_context")
        if isinstance(wf, Mapping):
            selected = wf.get("selected_analysts")
    if isinstance(selected, (list, tuple)):
        return normalize_required_analysts(selected)
    return list(DEFAULT_REQUIRED_ANALYSTS)


def evaluate_state_integrity(state: Mapping[str, Any]) -> RunIntegrity:
    traces = state.get("analyst_traces")
    if not isinstance(traces, list):
        traces = None
    return evaluate_run_integrity(
        reports_from_state(state),
        required_analysts=required_analysts_from_state(state),
        analyst_traces=traces,
    )


def fund_flow_guard_abstain_status(guard: Mapping[str, Any] | None) -> DecisionStatus:
    """Map fund-flow direction block to ABSTAIN/NO_TRADE (not Neutral/HOLD)."""
    status = str((guard or {}).get("status") or "blocked")
    return abstain_status(
        reason_codes=[f"fund_flow_guard:{status}", "direction_evidence_blocked"],
        trade_action="NO_TRADE",
        risk_status="BLOCKED",
    )


def resolve_decision_status_for_result(
    result: Mapping[str, Any],
) -> Optional[DecisionStatus]:
    """Integrity INVALID wins; else explicit decision_status from state/result."""
    from tradingagents.agents.utils.decision_status import (
        decision_status_from_mapping,
        decision_status_from_state,
    )

    selected = None
    wf = result.get("workflow_context")
    if isinstance(wf, Mapping):
        selected = wf.get("selected_analysts")
    traces = result.get("analyst_traces")
    if not isinstance(traces, list):
        traces = None
    integrity = evaluate_run_integrity(
        result, required_analysts=selected, analyst_traces=traces
    )
    if integrity.all_required_failed and integrity.decision_status:
        return decision_status_from_mapping(integrity.decision_status)

    parsed = decision_status_from_state(result)
    if parsed is not None:
        return parsed

    # Persist PARTIAL from integrity when no richer status exists yet.
    if integrity.decision_status:
        return decision_status_from_mapping(integrity.decision_status)
    return None


def build_invalid_run_terminal_payload(
    state: Mapping[str, Any],
    integrity: RunIntegrity,
) -> dict[str, Any]:
    """Populate graph state so INVALID_RUN can END without debate/trader/risk LLMs."""
    decision = integrity.decision_status or invalid_run_status(
        failure_class=ANALYSIS_DATA_ERROR,
        reason_codes=list(integrity.reason_codes),
    )
    if isinstance(decision, dict):
        decision_dict = decision
    else:
        decision_dict = decision.to_dict()
    blocked_plan = (
        "运行完整性判定：必需分析师报告全部失败（INVALID_RUN/DATA_ERROR）。"
        "已绕过辩论/交易员/风控；不得输出方向、概率、百分比区间或 Neutral/HOLD；"
        f"执行动作 NO_TRADE。失败角色={','.join(integrity.failed_required)}"
    )
    manager_verdict = {
        "direction": DIRECTION_NA,
        "winner": "tie",
        "reason": "必需分析师上游全部失败",
        "position_pct": 0,
        "entry": None,
        "target": None,
        "stop_loss": None,
        "upside": None,
        "downside": None,
        "odds": None,
        "adopted_claim_ids": [],
        "partially_adopted_claims": [],
        "rejected_claim_ids": [],
        "excluded_evidence": [],
        "consistency_check_passed": False,
        "failed_checks": list(integrity.reason_codes),
        "decision_status": decision_dict,
        "analysis_status": decision_dict.get("analysis_status"),
        "trade_action": decision_dict.get("trade_action", ACTION_NO_TRADE),
        "risk_status": decision_dict.get("risk_status"),
    }
    inv = dict(state.get("investment_debate_state") or {})
    inv.update(
        {
            "judge_decision": blocked_plan,
            "current_response": blocked_plan,
            "manager_verdict": manager_verdict,
            "blocked": True,
            "block_reason": "run_integrity_invalid",
            "parse_status": "invalid_run",
        }
    )
    return {
        "run_integrity": integrity.to_dict(),
        "decision_status": decision_dict,
        "analysis_status": decision_dict.get("analysis_status"),
        "trade_action": decision_dict.get("trade_action", ACTION_NO_TRADE),
        "risk_status": decision_dict.get("risk_status"),
        "investment_plan": blocked_plan,
        "trader_investment_plan": blocked_plan,
        "final_trade_decision": blocked_plan,
        "manager_verdict": manager_verdict,
        "investment_debate_state": inv,
        "integrity_route": "END",
    }


def create_run_integrity_gate():
    """Graph node: after analyst barrier, before Bull/Bear debate."""

    def run_integrity_gate_node(state: Mapping[str, Any]) -> dict[str, Any]:
        integrity = evaluate_state_integrity(state)
        if integrity.all_required_failed:
            return build_invalid_run_terminal_payload(state, integrity)

        payload: dict[str, Any] = {
            "run_integrity": integrity.to_dict(),
            "integrity_route": "Bull Researcher",
        }
        if integrity.failed_required_count > 0 and integrity.decision_status:
            # PARTIAL: continue debate but stamp canonical status early.
            ds = integrity.decision_status
            payload["decision_status"] = ds
            payload["analysis_status"] = ds.get("analysis_status", ANALYSIS_PARTIAL)
            payload["trade_action"] = ds.get("trade_action")
            payload["risk_status"] = ds.get("risk_status")
        return payload

    return run_integrity_gate_node


def apply_integrity_to_mutable_result(
    result: MutableMapping[str, Any],
    integrity: RunIntegrity,
) -> MutableMapping[str, Any]:
    if integrity.decision_status:
        apply_decision_status_to_result(result, integrity.decision_status)
    result["run_integrity"] = integrity.to_dict()
    return result
