"""DAV-1373: manager ledger eligibility, prompt contract, downgrade-only fallback."""
import json

import pytest

from tradingagents.agents.utils.evidence_verifier import (
    extract_and_validate_manager_verdict,
    format_claims_with_verification_for_prompt,
    ledger_eligibility,
)
from tradingagents.prompts.zh import PROMPTS


def _summary(**overrides):
    result = {
        "claim_id": "INV-1", "claim": "营收增长30%", "counts": {"total": 1, "verified": 1},
        "coverage": 1.0, "decision": "adopt", "semantic_decision": "adopt",
        "verified_evidence": ["营收增长30%"], "unsupported_evidence": [],
        "proposition_audit": [],
    }
    result.update(overrides)
    return result


def _verdict(summary, bucket, *, reason="观望", excluded=None):
    payload = {
        "winner": "tie", "direction": "中性", "reason": reason,
        "position_pct": 0, "adopted_claim_ids": ["INV-1"] if bucket == "adopted" else [],
        "partially_adopted_claims": ["INV-1"] if bucket == "partial" else [],
        "rejected_claim_ids": [], "excluded_evidence": excluded or [],
        "claim_evidence_summary": {"INV-1": summary},
    }
    return extract_and_validate_manager_verdict(
        "裁决：观望。\n<!-- MANAGER_VERDICT: " + json.dumps(payload, ensure_ascii=False) + " -->"
    )


@pytest.mark.parametrize("fields,expected", [
    ({}, "adopted"),
    ({"semantic_decision": "non_factual_only"}, "rejected_only"),
    ({"semantic_decision": "reject"}, "rejected_only"),
    ({"semantic_decision": "partial_threshold"}, "partial"),
    ({"semantic_hard_guards": ["E-04"]}, "partial"),
    ({"counts": {"total": 3, "verified": 2}, "coverage": 2 / 3, "decision": "partial"}, "partial"),
    ({"pit_failed": True}, "rejected_only"),
    ({"counts": {"total": 1, "verified": 0}}, "rejected_only"),
    ({"is_observation_or_hypothesis": True}, "partial"),
])
def test_eligibility_matches_hard_gate(fields, expected):
    eligibility = ledger_eligibility(_summary(**fields))
    assert eligibility["max_bucket"] == expected
    assert eligibility["reason"]


@pytest.mark.parametrize("fields,bucket,expected_error", [
    ({"pit_failed": True}, "adopted", "存在事实冲突/前视偏差"),
    ({"counts": {"total": 1, "verified": 0}}, "adopted", "全部证据未获验证"),
    ({"counts": {"total": 3, "verified": 1}, "coverage": 1 / 3}, "partial", "证据覆盖率不足"),
    ({"counts": {"total": 1, "verified": 1, "source_unavailable": 1}}, "partial", "不可用数据源"),
    ({"semantic_decision": "reject"}, "adopted", "semantic_decision=reject"),
])
def test_check_7_error_precedence_unchanged(fields, bucket, expected_error):
    result = _verdict(_summary(**fields), bucket)
    assert result["consistency_check_passed"] is False
    assert any(expected_error in e for e in result["failed_checks"])
    assert result["ledger_normalizations"] == []


def test_non_factual_adopted_and_partial_downgrade_without_evidence_anchor():
    s = _summary(semantic_decision="non_factual_only", proposition_audit=[
        {"text": "会触发价格反弹", "kind": "scenario_hypothesis", "support_status": "unsupported"}
    ])
    for bucket in ("adopted", "partial"):
        result = _verdict(s, bucket, reason="会触发价格反弹")
        assert result["adopted_claim_ids"] == []
        assert result["partially_adopted_claims"] == []
        assert result["rejected_claim_ids"] == ["INV-1"]
        assert result["consistency_check_passed"] is True, result["failed_checks"]
        assert result["ledger_normalizations"] == [f"ledger_normalized:{bucket}_to_rejected:INV-1"]
        assert result["direction_basis"]["status"] != "ledgered"


def test_missing_claim_text_never_normalizes_fatal_verification():
    raw = '<!-- MANAGER_VERDICT: {"winner":"tie","direction":"中性","reason":"观望","position_pct":0,"adopted_claim_ids":["INV-1"]} -->'
    result = extract_and_validate_manager_verdict(
        raw, claims_verification=[{"claim_id": "INV-1", "status": "source_unavailable", "is_fatal": True}]
    )
    assert result["adopted_claim_ids"] == ["INV-1"]
    assert result["consistency_check_passed"] is False
    assert any("严重幻觉" in e for e in result["failed_checks"])


def test_semantic_partial_adopted_downgrades_without_unsupported_atoms():
    s = _summary(semantic_decision="partial_threshold", semantic_coverage=0.5)
    result = _verdict(s, "adopted")
    assert result["adopted_claim_ids"] == []
    assert result["partially_adopted_claims"] == ["INV-1"]
    assert result["ledger_normalizations"] == ["ledger_normalized:adopt_to_partial:INV-1"]
    assert result["consistency_check_passed"] is True, result["failed_checks"]


def test_semantic_reject_not_downgraded_from_adopted():
    result = _verdict(_summary(semantic_decision="reject"), "adopted")
    assert result["adopted_claim_ids"] == ["INV-1"]
    assert result["consistency_check_passed"] is False


def test_prompt_shows_only_eligibility_and_reference_evidence():
    claim = {"claim_id": "INV-1", "speaker": "Bull", "claim": "营收增长30%"}
    for sem, expected in (("adopt", "全额"), ("partial_threshold", "部分"),
                          ("non_factual_only", "仅可驳回")):
        text = format_claims_with_verification_for_prompt(
            [claim], claim_evidence_summary={"INV-1": _summary(semantic_decision=sem)}
        )
        assert text.count("账本资格上限：") == 1
        assert f"账本资格上限：{expected}" in text
        assert "覆盖率=100.0%" in text
        assert "[VERIFIED / 真实核验] 营收增长30%" in text
        assert "证据充分 / 全Verified" not in text
        assert "规则判定:" not in text
        assert "语义命题判定:" not in text


def test_manager_prompt_has_single_eligibility_rule():
    template = PROMPTS["research_manager_prompt"]
    assert "每条论点只能放在其资格上限或更低的位置，不得升级" in template
    assert "non_factual_only 与语义 reject 的论点只能放进 rejected_claim_ids" in template
    assert "语义命题门禁（semantic_decision" not in template
