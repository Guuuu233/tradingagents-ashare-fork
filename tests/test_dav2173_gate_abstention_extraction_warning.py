"""DAV-2173: ``extraction_warning`` must separate a business gate / rule driven
abstention (ABSTAIN / NO_TRADE) from a real extraction failure.

Reproduction: report ``83eb5372c4874f659811198f95f48b5f`` (000858.SZ, 2026-10-16)
— both horizons were stopped by the fund-flow guard
(``fund_flow_guard:selected`` → ABSTAIN/NO_TRADE) and correctly abstained, yet
``extraction_warning`` = "置信度及概率数据全部缺失" was stamped on the top level and
on both horizon slices, mislabelling a normal abstention as an extraction bug.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services.report_service import (
    create_report,
    is_rule_driven_abstention,
    resolve_report_fields,
)

EXTRACTION_WARNING = "置信度及概率数据全部缺失"

BLOCKED_PLAN = (
    "资金流门控拦截（fund_flow_guard:selected）。"
    "状态=ABSTAIN，动作=NO_TRADE；已阻断进入 Trader 执行阶段。"
)


def _make_db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return session_factory()


def _gate_decision_status():
    """Post-gate status recorded for the fund-flow guard short-circuit (DAV-2164)."""
    return {
        "analysis_status": "ABSTAIN",
        "direction": "N/A",
        "trade_action": "NO_TRADE",
        "risk_status": "BLOCKED",
        "confirmation_state": "UNRESOLVED",
        "failure_class": None,
        "reason_codes": [
            "fund_flow_guard:selected",
            "direction_evidence_blocked",
            "fund_flow_consensus_guard",
            "risk_verdict:blocked",
        ],
        "confidence": None,
        "probability": None,
        "failed_checks": [],
        "human_reasons": [],
    }


def _gated_horizon(horizon):
    return {
        "horizon": horizon,
        "status": "completed",
        "analysis_status": "ABSTAIN",
        "trade_action": "NO_TRADE",
        "risk_status": "BLOCKED",
        "decision_status": _gate_decision_status(),
        "final_trade_decision": BLOCKED_PLAN,
        "trader_investment_plan": BLOCKED_PLAN,
    }


def _dual_horizon_gated_result():
    """The persisted shape of the reproduction report (both horizons abstained)."""
    return {
        "symbol": "000858.SZ",
        "trade_date": "2026-10-16",
        "mode": "dual_horizon",
        "requested_horizons": ["short", "medium"],
        "horizon_status": {"short": "completed", "medium": "completed"},
        "short_term": _gated_horizon("short"),
        "medium_term": _gated_horizon("medium"),
        "analysis_status": "INVALID_RUN",
        "trade_action": "NO_TRADE",
        "decision_status": {
            "analysis_status": "INVALID_RUN",
            "direction": "N/A",
            "trade_action": "NO_TRADE",
            "risk_status": "UNKNOWN",
            "confirmation_state": "UNRESOLVED",
            "failure_class": "DATA_ERROR",
            "reason_codes": ["dual_horizon_all_invalid"],
            "confidence": None,
            "probability": None,
            "failed_checks": [],
            "human_reasons": [],
        },
        "final_trade_decision": BLOCKED_PLAN,
        "trader_investment_plan": BLOCKED_PLAN,
    }


def test_single_horizon_gate_abstention_has_no_extraction_warning():
    """Gate-driven ABSTAIN: no confidence/probability by construction → no warning."""
    result_data = {
        "symbol": "000858.SZ",
        "trade_date": "2026-10-16",
        "analysis_status": "ABSTAIN",
        "trade_action": "NO_TRADE",
        "risk_status": "BLOCKED",
        "decision_status": _gate_decision_status(),
        "final_trade_decision": BLOCKED_PLAN,
        "trader_investment_plan": BLOCKED_PLAN,
    }

    assert is_rule_driven_abstention(result_data) is True
    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] is None
    assert resolved["probability"] is None
    assert resolved["extraction_warning"] is None
    # No fallback extraction note either: the empty fields are expected here.
    assert resolved["extraction_note"] is None


def test_dual_horizon_all_abstain_has_no_extraction_warning():
    """DAV-2164 reproduction: both slices ABSTAIN → top level and slices stay clean."""
    result_data = _dual_horizon_gated_result()

    assert is_rule_driven_abstention(result_data) is True
    top_resolved = resolve_report_fields(result_data)
    assert top_resolved["confidence"] is None
    assert top_resolved["probability"] is None
    assert top_resolved["extraction_warning"] is None

    # Per-horizon resolve calls (main.py resolves each slice on its own).
    for horizon in ("short", "medium"):
        slice_resolved = resolve_report_fields(result_data[f"{horizon}_term"])
        assert slice_resolved["extraction_warning"] is None
        assert slice_resolved["extraction_note"] is None


def test_gate_abstention_warning_not_persisted_by_create_report():
    """The persisted row must not carry the false extraction_warning either."""
    db = _make_db_session()
    try:
        report = create_report(
            db=db,
            symbol="000858.SZ",
            trade_date="2026-10-16",
            result_data=_dual_horizon_gated_result(),
        )
        assert report.analysis_status == "ABSTAIN"
        assert report.confidence is None
        assert report.probability is None
        assert report.result_data.get("extraction_warning") is None
        assert report.result_data["short_term"]["analysis_status"] == "ABSTAIN"
    finally:
        db.close()


def test_executable_valid_run_without_confidence_still_warns():
    """Real extraction failure on an executable run keeps the warning."""
    result_data = {
        "symbol": "600406.SH",
        "analysis_status": "VALID",
        "trade_action": "BUY",
        "direction": "看多",
        "decision_status": {
            "analysis_status": "VALID",
            "direction": "BULL",
            "trade_action": "BUY",
            "risk_status": "OK",
            "confirmation_state": "CONFIRMED",
            "reason_codes": [],
            "confidence": None,
            "probability": None,
            "failed_checks": [],
            "human_reasons": [],
        },
        "final_trade_decision": "综合研判，建议买入，但正文未给出置信度与概率。",
        "trader_investment_plan": "分批建仓，未给出置信度与概率。",
    }

    assert is_rule_driven_abstention(result_data) is False
    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] is None
    assert resolved["probability"] is None
    assert resolved["extraction_warning"] == EXTRACTION_WARNING
    # Pre-existing behaviour for a real failure: the warning replaces the note.
    assert resolved["extraction_note"] is None


def test_run_without_recorded_status_still_warns():
    """No recorded analysis status at all → untouched legacy behaviour."""
    result_data = {
        "symbol": "600406.SH",
        "final_trade_decision": "综合各方研判，当前时点多空博弈激烈，建议观望等待明确信号。",
        "trader_investment_plan": "交易计划未给出置信度说明。",
    }

    assert is_rule_driven_abstention(result_data) is False
    resolved = resolve_report_fields(result_data)
    assert resolved["extraction_warning"] == EXTRACTION_WARNING


def test_invalid_run_without_abstain_horizon_keeps_warning():
    """INVALID_RUN from a real failure (no ABSTAIN anywhere) keeps its alert."""
    result_data = {
        "symbol": "600000.SH",
        "analysis_status": "INVALID_RUN",
        "trade_action": "NO_TRADE",
        "decision_status": {
            "analysis_status": "INVALID_RUN",
            "direction": "N/A",
            "trade_action": "NO_TRADE",
            "risk_status": "UNKNOWN",
            "failure_class": "DATA_ERROR",
            "confirmation_state": "UNRESOLVED",
            "reason_codes": ["all_required_analysts_failed"],
            "confidence": None,
            "probability": None,
            "failed_checks": [],
            "human_reasons": [],
        },
        "final_trade_decision": "",
    }

    assert is_rule_driven_abstention(result_data) is False
    assert resolve_report_fields(result_data)["extraction_warning"] == EXTRACTION_WARNING


def test_legacy_dual_row_with_stub_medium_slice_has_no_warning():
    """Legacy rows (no requested_horizons) whose medium slice never ran still abstain."""
    result_data = {
        "symbol": "600519.SH",
        "mode": "dual_horizon",
        "short_term": _gated_horizon("short"),
        # Stub slice of a horizon that never ran: no status recorded.
        "medium_term": {"horizon": "medium", "final_trade_decision": ""},
        "analysis_status": "ABSTAIN",
        "trade_action": "NO_TRADE",
        "decision_status": _gate_decision_status(),
        "final_trade_decision": BLOCKED_PLAN,
    }

    assert is_rule_driven_abstention(result_data) is True
    assert resolve_report_fields(result_data)["extraction_warning"] is None


def test_mixed_horizons_keep_extraction_warning():
    """A gated short horizon plus an executable medium horizon is not an abstention."""
    result_data = {
        "symbol": "000858.SZ",
        "requested_horizons": ["short", "medium"],
        "short_term": _gated_horizon("short"),
        "medium_term": {
            "horizon": "medium",
            "status": "completed",
            "analysis_status": "VALID",
            "trade_action": "BUY",
            "direction": "BULL",
            "final_trade_decision": "建议买入，正文未给出置信度与概率。",
        },
        "analysis_status": "PARTIAL",
        "trade_action": "NO_TRADE",
        "decision_status": {
            "analysis_status": "PARTIAL",
            "direction": "N/A",
            "trade_action": "NO_TRADE",
            "risk_status": "UNKNOWN",
            "confirmation_state": "UNRESOLVED",
            "reason_codes": ["dual_horizon_mixed"],
            "confidence": None,
            "probability": None,
            "failed_checks": [],
            "human_reasons": [],
        },
        "final_trade_decision": "建议买入，正文未给出置信度与概率。",
    }

    assert is_rule_driven_abstention(result_data) is False
    assert resolve_report_fields(result_data)["extraction_warning"] == EXTRACTION_WARNING


def test_requested_horizon_without_recorded_slice_keeps_warning():
    """A requested horizon with no slice is a silent anomaly, not an abstention."""
    result_data = {
        "symbol": "000858.SZ",
        "requested_horizons": ["short", "medium"],
        "short_term": _gated_horizon("short"),
        "analysis_status": "ABSTAIN",
        "trade_action": "NO_TRADE",
        "final_trade_decision": BLOCKED_PLAN,
    }

    assert is_rule_driven_abstention(result_data) is False
    assert resolve_report_fields(result_data)["extraction_warning"] == EXTRACTION_WARNING
