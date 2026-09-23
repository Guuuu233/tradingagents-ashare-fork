"""Unit tests for the Price-Basis Hard Gate (DAV-1199 / DAV-1142-B2).

Covers the card contract:
- b188060f pattern must be blocked: raw 大宗价 78.61 must not stand as a qfq
  executable target; run is fail-closed (non-executable, version downgraded).
- F1/F2/F3-style pollution in new samples → violations (zero tolerance).
- Legal labeled dual display is classified separately, not a violation.
- decision-driving refs with unspecified basis / missing as_of fail closed.
- target/stop/entry without a legal qfq/converted back-reference must not
  stand as executable values.
- compliant samples resolve price_basis_version=price_basis.vendor_qfq and
  record price_ref_contract_version=price_ref.v1.
"""

from tradingagents.agents.utils.price_basis_gate import (
    GATE_BLOCKED_GAP,
    PRICE_BASIS_VERSION_UNSPECIFIED,
    PRICE_BASIS_VERSION_VENDOR_QFQ,
    PRICE_REF_CONTRACT_VERSION,
    enforce_price_basis_gate,
    evaluate_price_basis_gate,
)
import tradingagents.agents.utils.price_basis_gate as gate_mod
import tradingagents.agents.utils.price_ref_registry as registry_mod
from tradingagents.agents.utils.price_ref_registry import audit_price_ref_registry


def _state(**reports):
    state = {"trade_date": "2026-05-22"}
    state.update(reports)
    audit_price_ref_registry(state)
    return state


def _violation_kinds(gate):
    return {v["kind"] for v in gate["violations"]}


# ---------------------------------------------------------------------------
# Anchor: b188060f — raw disclosure price used as qfq target must be blocked
# ---------------------------------------------------------------------------


def test_b188060f_raw_disclosure_as_qfq_target_is_blocked():
    state = _state(
        market_report="现价 81.19 元，均线呈多头排列。",
        news_report=(
            "今日大宗交易成交 78.61 元，较当日收盘 84.03 元折价 6.45%。"
            "该价格对现价 81.19 形成向下锚。"
        ),
        trader_investment_plan="下行风险较大，第一下行目标 78.61 元（大宗折价锚位）。",
        final_trade_decision="建议卖出，止损位 84.50 元。",
        trade_action="SELL",
    )
    gate = enforce_price_basis_gate(state)

    assert gate["status"] == "blocked"
    kinds = _violation_kinds(gate)
    assert "cross_basis_coordinate_mix" in kinds
    # 78.61 is a raw disclosure ref — illegal executable target basis.
    assert "executable_level_wrong_basis" in kinds

    # fail-close: directional action downgraded, version stays unspecified.
    assert state["trade_action"] == "NO_TRADE"
    assert gate["price_basis_version"] == PRICE_BASIS_VERSION_UNSPECIFIED
    assert state["price_basis_version"] == PRICE_BASIS_VERSION_UNSPECIFIED
    assert state["price_ref_contract_version"] == PRICE_REF_CONTRACT_VERSION
    gate_gaps = [g for g in state["price_basis_gaps"] if g["kind"] == "price_basis_gate_violation"]
    assert gate_gaps


def test_f1_resonant_support_mixed_basis_is_blocked():
    """F1 form: raw disclosure price listed as joint support with qfq level."""
    state = _state(
        volume_price_report="现价 81.19 元，布林下轨 77.48 元。",
        news_report="布林下轨 77.48 元与大宗折价成交价 78.61 元共振支撑。",
    )
    gate = enforce_price_basis_gate(state)
    assert gate["status"] == "blocked"
    assert "cross_basis_coordinate_mix" in _violation_kinds(gate)


def test_f2_raw_value_reused_in_technical_context_is_blocked():
    """F2 form: raw value reused as a pure technical level."""
    state = _state(
        market_report="现价 81.19 元。",
        news_report="今日大宗交易成交 78.61 元。",
        investment_plan="支撑位看 78.61 元，跌破止损。",
    )
    gate = enforce_price_basis_gate(state)
    assert gate["status"] == "blocked"
    kinds = _violation_kinds(gate)
    # raw value reused in a coordinate context and/or wrong-basis level
    assert kinds & {"cross_basis_coordinate_mix", "executable_level_wrong_basis",
                    "decision_driving_unspecified_basis"}


# ---------------------------------------------------------------------------
# Legal labeled dual display — not pollution
# ---------------------------------------------------------------------------


def test_labeled_dual_display_is_allowed_not_violation():
    # DAV-1224 C2：「定增」是确定披露词（pit_raw）；「大宗交易」句级不再
    # 自动贴 raw。用定增构造 raw/pit_raw 双列展示。
    state = _state(
        market_report="现价 81.19 元。",
        news_report=(
            "披露口径：定增 78.61 元（pit_raw）与前复权现价 81.19 元"
            "双列展示，两者不可直接比较。"
        ),
        investment_plan="前复权目标价 90.00 元，前复权止损位 78.00 元。",
    )
    gate = enforce_price_basis_gate(state)
    assert gate["status"] == "pass", gate["violations"]
    assert gate["allowed_dual_display"]
    assert gate["price_basis_version"] == PRICE_BASIS_VERSION_VENDOR_QFQ


# ---------------------------------------------------------------------------
# Fail-close on unspecified / unbacked executable levels
# ---------------------------------------------------------------------------


def test_unspecified_decision_driving_price_fails_closed():
    state = _state(
        market_report="现价 81.19 元。",
        final_trade_decision="某渠道消息称市价 70.00 元，建议观望。",
    )
    gate = enforce_price_basis_gate(state)
    assert gate["status"] == "blocked"
    assert "decision_driving_unspecified_basis" in _violation_kinds(gate)


def test_unbacked_executable_level_blocked():
    """A target price with no registry ref at all must not stand."""
    state = _state(
        market_report="现价 81.19 元。",
        trader_investment_plan="目标价 999.99 元。",
    )
    gate = enforce_price_basis_gate(state)
    assert gate["status"] == "blocked"
    # 999.99 gets extracted by the registry as unspecified (model new price)
    assert _violation_kinds(gate)


def test_invalid_conversion_on_decision_ref_blocked():
    state = _state(
        market_report="现价 81.19 元。",
        investment_plan="大宗交易 78.61 元，换算为前复权约 76.25 元作为支撑位。",
    )
    gate = enforce_price_basis_gate(state)
    assert gate["status"] == "blocked"
    assert "invalid_conversion" in _violation_kinds(gate)


def test_valid_pit_safe_conversion_allows_converted_level():
    state = _state(
        market_report="现价 81.19 元。",
        investment_plan=(
            "大宗交易 78.61 元，按复权因子 0.97（2026-05-20）换算为前复权约 "
            "76.25 元，前复权支撑位 76.25 元。"
        ),
    )
    gate = enforce_price_basis_gate(state)
    assert gate["status"] == "pass", gate["violations"]


# ---------------------------------------------------------------------------
# Compliant sample → vendor_qfq version + contract version recorded
# ---------------------------------------------------------------------------


def test_compliant_sample_passes_with_vendor_qfq_version():
    state = _state(
        market_report="现价 81.19 元，均线呈多头排列。",
        news_report="今日大宗交易成交 78.61 元。",
        investment_plan="前复权目标价 90.00 元，前复权止损位 78.00 元。",
        trade_action="BUY",
        decision_status={"trade_action": "BUY", "reason_codes": []},
    )
    gate = enforce_price_basis_gate(state)
    assert gate["status"] == "pass", gate["violations"]
    assert gate["price_basis_version"] == PRICE_BASIS_VERSION_VENDOR_QFQ
    assert state["price_basis_version"] == PRICE_BASIS_VERSION_VENDOR_QFQ
    assert state["price_ref_contract_version"] == PRICE_REF_CONTRACT_VERSION
    # compliant run keeps its directional action
    assert state["trade_action"] == "BUY"


def test_decision_status_dict_updated_on_block():
    state = _state(
        news_report="大宗交易 78.61 元。",
        final_trade_decision="止损位 78.61 元。",
        trade_action="SELL",
        decision_status={"trade_action": "SELL", "reason_codes": [], "failed_checks": []},
    )
    gate = enforce_price_basis_gate(state)
    assert gate["status"] == "blocked"
    assert state["trade_action"] == "NO_TRADE"
    assert state["decision_status"]["trade_action"] == "NO_TRADE"
    assert GATE_BLOCKED_GAP in state["decision_status"]["reason_codes"]
    assert GATE_BLOCKED_GAP in state["decision_status"]["failed_checks"]


def test_gate_idempotent_and_malformed_safe():
    state = _state(market_report="现价 81.19 元。")
    g1 = enforce_price_basis_gate(state)
    g2 = enforce_price_basis_gate(state)
    assert g1["status"] == g2["status"]

    out = enforce_price_basis_gate(None)
    assert out["status"] == "pass"
    out = evaluate_price_basis_gate({})
    assert out["status"] == "pass"


# ---------------------------------------------------------------------------
# 🟡-1 (mandatory) RED: internal exceptions must convert to fail-close
# semantics — never propagate as runtime errors, never silently pass.
# ---------------------------------------------------------------------------


def test_audit_internal_exception_fails_closed(monkeypatch):
    """An audit crash must surface as audit_error + gap, not a raised error."""
    def _boom(reports, *, cutoff=None):
        raise RuntimeError("synthetic audit crash")

    monkeypatch.setattr(registry_mod, "build_price_ref_registry", _boom)

    state = {"trade_date": "2026-05-22", "news_report": "大宗交易 78.61 元。"}
    result = audit_price_ref_registry(state)  # must not raise

    assert result["validation"]["status"] == "audit_error"
    assert result["validation"]["audit_error"] is True
    assert state["price_basis_validation"]["audit_error"] is True
    assert any(g["kind"] == "audit_error" for g in state["price_basis_gaps"])


def test_gate_blocks_when_audit_unavailable(monkeypatch):
    """Un-audited prices must never reach decision math: audit failure →
    gate blocked → directional action downgraded → version unspecified."""
    def _boom(reports, *, cutoff=None):
        raise RuntimeError("synthetic audit crash")

    monkeypatch.setattr(registry_mod, "build_price_ref_registry", _boom)

    state = {
        "trade_date": "2026-05-22",
        "news_report": "大宗交易 78.61 元。",
        "final_trade_decision": "前复权目标价 90.00 元。",
        "trade_action": "BUY",
        "decision_status": {"trade_action": "BUY", "reason_codes": []},
    }
    gate = enforce_price_basis_gate(state)

    assert gate["status"] == "blocked"
    assert "audit_unavailable" in _violation_kinds(gate)
    assert gate["price_basis_version"] == PRICE_BASIS_VERSION_UNSPECIFIED
    assert state["trade_action"] == "NO_TRADE"
    assert state["decision_status"]["trade_action"] == "NO_TRADE"


def test_gate_internal_exception_fails_closed(monkeypatch):
    """A crash inside gate evaluation itself must also fail closed."""
    def _boom(state):
        raise RuntimeError("synthetic gate crash")

    monkeypatch.setattr(gate_mod, "evaluate_price_basis_gate", _boom)

    state = _state(
        market_report="现价 81.19 元。",
        final_trade_decision="前复权目标价 90.00 元。",
        trade_action="BUY",
    )
    gate = enforce_price_basis_gate(state)  # must not raise

    assert gate["status"] == "blocked"
    assert gate["violations"][0]["kind"] == "gate_internal_error"
    assert state["price_basis_version"] == PRICE_BASIS_VERSION_UNSPECIFIED
    assert state["trade_action"] == "NO_TRADE"
    assert any(
        g["kind"] == "price_basis_gate_violation" for g in state["price_basis_gaps"]
    )
