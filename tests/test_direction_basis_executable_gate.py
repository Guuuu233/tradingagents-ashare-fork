"""DAV-1353（D-051 补“同向”）：可执行动作必须有同向依据。

规则：status_from_manager_verdict 终态 trade_action ∈ {BUY,SELL,HOLD} 且
manager_verdict.direction_basis.status != "ledgered" 时，动作降为 WAIT，
原因码 ``direction_basis_not_ledgered:<status>``。

边界：只影响 trade_action；不改 analysis_status / direction_basis 计算；
中性方向 not_applicable 的 HOLD 不豁免（待总控裁定）。
"""
from __future__ import annotations

from tradingagents.agents.utils.decision_status import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_NO_TRADE,
    ACTION_SELL,
    ACTION_WAIT,
    ANALYSIS_VALID,
    CONFIRM_CONFIRMED,
    status_from_manager_verdict,
)


def _mv(**overrides) -> dict:
    mv = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 50,
        "consistency_check_passed": True,
        "adopted_claim_ids": ["BULL-1"],
        "partially_adopted_claims": [],
        "rejected_claim_ids": [],
    }
    mv.update(overrides)
    return mv


def _summary_adopt(cid: str) -> dict:
    return {
        cid: {
            "counts": {"total": 1, "verified": 1, "unsupported": 0,
                       "contradicted": 0, "source_unavailable": 0},
            "coverage": 1.0,
            "decision": "adopt",
        }
    }


def _db(status: str) -> dict:
    return {"status": status, "same_direction_claims": [],
            "undetermined_claim_ids": []}


def test_ledgered_buy_unchanged():
    st = status_from_manager_verdict(
        _mv(direction_basis=_db("ledgered")),
        focus_claim_ids=["BULL-1"],
        claim_evidence_summary=_summary_adopt("BULL-1"),
    )
    assert st.analysis_status == ANALYSIS_VALID
    assert st.confirmation_state == CONFIRM_CONFIRMED
    assert st.trade_action == ACTION_BUY
    assert not any(c.startswith("direction_basis_not_ledgered") for c in st.reason_codes)


def test_unledgered_buy_downgraded_wait():
    """c590d925:short_term 等价构造：winner=bull 但采纳账本只有空头论点。"""
    st = status_from_manager_verdict(
        _mv(direction_basis=_db("unledgered")),
        focus_claim_ids=["BULL-1"],
        claim_evidence_summary=_summary_adopt("BULL-1"),
    )
    # DAV-1440：空同向账本升级为 ABSTAIN，而非只保留方向降 WAIT。
    assert st.analysis_status == "ABSTAIN"
    assert st.direction == "N/A"
    assert st.trade_action == ACTION_NO_TRADE
    assert "direction_without_adjudicated_support" in st.reason_codes


def test_partial_only_sell_downgraded_wait():
    st = status_from_manager_verdict(
        _mv(direction="看空", winner="bear",
            adopted_claim_ids=["BEAR-1"], direction_basis=_db("partial_only")),
        focus_claim_ids=["BEAR-1"],
        claim_evidence_summary=_summary_adopt("BEAR-1"),
    )
    assert st.analysis_status == ANALYSIS_VALID
    assert st.trade_action == ACTION_WAIT
    assert "direction_basis_not_ledgered:partial_only" in st.reason_codes


def test_unknown_downgraded_wait():
    st = status_from_manager_verdict(
        _mv(direction_basis=_db("unknown")),
        focus_claim_ids=["BULL-1"],
        claim_evidence_summary=_summary_adopt("BULL-1"),
    )
    assert st.trade_action == ACTION_WAIT
    assert "direction_basis_not_ledgered:unknown" in st.reason_codes


def test_missing_direction_basis_downgraded_wait():
    st = status_from_manager_verdict(
        _mv(),
        focus_claim_ids=["BULL-1"],
        claim_evidence_summary=_summary_adopt("BULL-1"),
    )
    assert st.trade_action == ACTION_WAIT
    assert "direction_basis_not_ledgered:missing" in st.reason_codes


def test_neutral_hold_not_applicable_downgraded_wait():
    """中性方向 HOLD + direction_basis=not_applicable 不豁免 → WAIT（供总控裁定）。"""
    st = status_from_manager_verdict(
        _mv(direction="中性", winner="tie",
            adopted_claim_ids=["N-1"], direction_basis=_db("not_applicable")),
        focus_claim_ids=["N-1"],
        claim_evidence_summary=_summary_adopt("N-1"),
    )
    assert st.analysis_status == ANALYSIS_VALID
    assert st.trade_action == ACTION_WAIT
    assert "direction_basis_not_ledgered:not_applicable" in st.reason_codes


def test_ledgered_hold_unchanged():
    """ledgered 的 HOLD 不受影响。"""
    st = status_from_manager_verdict(
        _mv(direction="中性", winner="bull", position_pct=0,
            adopted_claim_ids=["N-1"], direction_basis=_db("ledgered")),
        focus_claim_ids=["N-1"],
        claim_evidence_summary=_summary_adopt("N-1"),
    )
    assert st.trade_action == ACTION_HOLD
    assert not any(c.startswith("direction_basis_not_ledgered") for c in st.reason_codes)


def test_already_wait_unresolved_no_db_code():
    """UNRESOLVED → WAIT 的路径不追加 direction_basis 码（动作本已非可执行）。"""
    summary = {
        "BULL-1": {
            "counts": {"total": 1, "verified": 0, "unsupported": 0,
                       "contradicted": 1, "source_unavailable": 0},
            "coverage": 0.0,
            "decision": "reject",
        }
    }
    st = status_from_manager_verdict(
        _mv(direction_basis=_db("unledgered")),
        focus_claim_ids=["BULL-1"],
        claim_evidence_summary=summary,
    )
    assert st.analysis_status == "ABSTAIN"
    assert st.trade_action == ACTION_NO_TRADE
    assert "direction_without_adjudicated_support" in st.reason_codes
    assert not any(c.startswith("direction_basis_not_ledgered") for c in st.reason_codes)


def test_direction_basis_from_nested_debate_state_verdict():
    """direction_basis 也可从 investment_debate_state.manager_verdict 读取。"""
    st = status_from_manager_verdict(
        _mv(),
        investment_debate_state={
            "manager_verdict": {"direction_basis": _db("unledgered")},
        },
        focus_claim_ids=["BULL-1"],
        claim_evidence_summary=_summary_adopt("BULL-1"),
    )
    assert st.analysis_status == "ABSTAIN"
    assert st.direction == "N/A"
    assert st.trade_action == ACTION_NO_TRADE
    assert "direction_without_adjudicated_support" in st.reason_codes
