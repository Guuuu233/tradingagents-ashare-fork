"""A4: conflicting fund-flow dispute rows must default to tie; high tie size blocks Trader."""

from __future__ import annotations

from tradingagents.agents.utils.evidence_verifier import (
    extract_and_validate_manager_verdict,
    is_conflicting_fund_flow_dispute,
)


def _conflict_manager_block(*, position_pct: int) -> str:
    return (
        "裁决。\n"
        '<!-- MANAGER_VERDICT: {'
        f'"direction": "偏多", "winner": "bull", "reason": "资金流对打仍判多", '
        f'"position_pct": {position_pct}, "entry": "100", "target": "110", "stop_loss": "95", '
        '"adopted_claim_ids": [], "partially_adopted_claims": [], '
        '"rejected_claim_ids": [], "excluded_evidence": [], '
        '"dispute_map": [{'
        '"data_point": "超大单净流入1.2亿同时大单净流出0.9亿", '
        '"bull_interpretation": "机构吸筹", '
        '"bear_interpretation": "主力派发", '
        '"evidence_decision": "流出等于吸筹，多方占优", '
        '"winner": "bull"'
        "}]} -->"
    )


def test_is_conflicting_fund_flow_dispute_detects_in_out_and_absorption():
    assert is_conflicting_fund_flow_dispute(
        data_point="超大单净流入1.2亿 / 大单净流出0.8亿",
        bull_interpretation="机构吸筹",
        bear_interpretation="主力出货",
        evidence_decision="流出等于吸筹，多方占优",
    )
    assert is_conflicting_fund_flow_dispute(
        data_point="主力净流出2亿",
        bull_interpretation="边打边吸筹",
        bear_interpretation="派发",
        evidence_decision="流出=吸筹",
    )
    assert not is_conflicting_fund_flow_dispute(
        data_point="主力净流入3亿且各分单同向",
        bull_interpretation="同向流入",
        bear_interpretation="力度不够",
        evidence_decision="净流入明确",
    )


def test_conflicting_fund_flow_dispute_forced_to_tie():
    verdict = extract_and_validate_manager_verdict(_conflict_manager_block(position_pct=10))
    assert len(verdict["dispute_map"]) == 1
    row = verdict["dispute_map"][0]
    assert row["winner"] == "tie"
    assert "不得" in row["evidence_decision"]
    assert "吸筹" not in row["evidence_decision"] or "禁止" in row["evidence_decision"]
    assert verdict.get("fund_flow_dispute_gate_applied") is True
    assert verdict["winner"] == "tie"
    assert verdict["direction"] == "中性"


def test_conflicting_fund_flow_low_position_tie_passes_consistency():
    """Low-size tie after fund-flow conflict may pass consistency (Trader not blocked by size)."""
    verdict = extract_and_validate_manager_verdict(_conflict_manager_block(position_pct=10))
    assert verdict["winner"] == "tie"
    assert verdict["consistency_check_passed"] is True
    assert verdict["failed_checks"] == []


def test_conflicting_fund_flow_high_position_tie_blocks_trader():
    """40% size on a forced tie must fail consistency and block Trader."""
    verdict = extract_and_validate_manager_verdict(_conflict_manager_block(position_pct=40))
    assert verdict["winner"] == "tie"
    assert verdict["consistency_check_passed"] is False
    assert any("不得高于30%" in msg or "仓位" in msg for msg in verdict["failed_checks"])
