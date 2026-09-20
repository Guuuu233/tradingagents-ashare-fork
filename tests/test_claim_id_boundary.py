# -*- coding: utf-8 -*-
"""Unit and regression tests for claim_id token boundary matching (DAV-1106).

Tests cover:
  1. Token boundary helper functions:
     - Exact token match on INV-1 without prefix/suffix leakage.
     - INV-1 must NOT match INV-10, INV-11, INV-12, or INV-100.
     - INV-10 must match INV-10 and not INV-1.
     - Composite FoldedComponent IDs like comp_INV-1_xxx must NOT match INV-1.
  2. 688981.SH (report 3bba59a6) regression fixture:
     - Fake failed_check '裁决正文将未完全核实的 claim INV-1 ... 标注为'证据充分'' must disappear.
     - consistency_check_passed must be True.
  3. Real violations must still be caught:
     - When prose genuinely marks a rejected/partial claim as '证据充分', the gate must still fail.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from tradingagents.agents.utils.evidence_verifier import (
    DECISION_ADOPT,
    DECISION_PARTIAL,
    DECISION_REJECT,
    build_claim_evidence_sufficient_pattern,
    build_claim_id_token_pattern,
    extract_and_validate_manager_verdict,
)


class TestClaimIdTokenPattern:
    """Test claim_id grammar token matching helper and contract boundaries."""

    def test_inv1_matches_inv1_with_various_delimiters(self):
        pat = build_claim_evidence_sufficient_pattern("INV-1")
        positive_cases = [
            "INV-1证据充分，多头全面胜出。",
            "INV-1 证据充分",
            "**INV-1**：覆盖率 100.0%，标注为【证据充分】",
            "[INV-1] 经核验，证据充分",
            "INV-1 / INV-5：证据充分",
            "INV-1、INV-5：证据充分",
            "证据充分：INV-1",
            "证据充分，予以采纳INV-1",
            "经过逐条核验，INV-1证据充分，多头全面胜出。",
        ]
        for text in positive_cases:
            assert pat.search(text) is not None, f"Failed to match valid line: {text}"

    def test_inv1_does_not_match_inv10_11_12(self):
        pat = build_claim_evidence_sufficient_pattern("INV-1")
        negative_cases = [
            "INV-10（多头基本面）：覆盖率 100.0%，标注为【证据充分】",
            "INV-11（空头）：全项真实核验通过，标注为【证据充分】",
            "INV-12：证据充分",
            "INV-100：证据充分",
            "INV-2 / INV-6 / INV-10（多头基本面）：覆盖率 100.0%，标注为【证据充分】，予以采纳。",
        ]
        for text in negative_cases:
            assert pat.search(text) is None, f"Incorrectly matched substring in: {text}"

    def test_inv10_matches_inv10(self):
        pat10 = build_claim_evidence_sufficient_pattern("INV-10")
        line = "INV-2 / INV-6 / INV-10（多头基本面）：覆盖率 100.0%，标注为【证据充分】，予以采纳。"
        assert pat10.search(line) is not None
        # Does not match INV-1
        pat1 = build_claim_evidence_sufficient_pattern("INV-1")
        assert pat1.search(line) is None

    def test_comp_id_does_not_match_base_claim_id(self):
        """FoldedComponent ID comp_INV-1_xxx represents a graph component, NOT claim INV-1."""
        pat = build_claim_evidence_sufficient_pattern("INV-1")
        comp_cases = [
            "证据贡献状态：属于全局唯一折叠组件 comp_INV-1_1b1fb8aa1297，证据充分",
            "折叠组件 comp_INV-1_xxx 证据充分",
            "comp_INV-1_1b1fb8aa1297: 证据充分",
        ]
        for text in comp_cases:
            assert pat.search(text) is None, f"Incorrectly matched comp_ID: {text}"

    def test_empty_or_none_cid_handling(self):
        assert build_claim_id_token_pattern("") == ""
        assert build_claim_id_token_pattern(None) == ""
        pat = build_claim_evidence_sufficient_pattern("")
        assert pat.search("INV-1证据充分") is None


class TestClaimIdBoundaryRegression688981:
    """Regression test for 688981.SH (report 3bba59a6) fixture."""

    @pytest.fixture
    def fixture_688981(self):
        fixture_path = Path(__file__).parent / "fixtures" / "decision_semantics" / "report_3bba59a6_fixture.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_688981_false_failed_check_disappears(self, fixture_688981):
        raw = fixture_688981["raw_response"]
        claims = fixture_688981["claims"]
        cv = fixture_688981["claims_verification"]
        mdc = fixture_688981["market_data_context"]

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw,
            claims=claims,
            claims_verification=cv,
            market_data_context=mdc,
        )

        # False check on INV-1 must be gone
        assert not any("INV-1" in err and "证据充分" in err for err in verdict["failed_checks"])
        assert verdict["consistency_check_passed"] is True
        assert len(verdict["failed_checks"]) == 0
        assert verdict["winner"] == "bear"
        assert verdict["direction"] == "偏空"


class TestClaimIdRealViolationStillCaught:
    """Ensure consistency gate does not relax: real violations must still fail."""

    def test_real_violation_on_rejected_claim_fails(self):
        claims = [
            {"claim_id": "INV-1", "speaker": "Bull", "stance": "bullish", "claim": "短线反弹", "evidence": ["收盘站稳200日线"]},
            {"claim_id": "INV-10", "speaker": "Bull", "stance": "bullish", "claim": "基本面扎实", "evidence": ["现金充沛"]},
        ]
        cv = [
            {"claim_id": "INV-1", "raw": "收盘站稳200日线", "status": "unsupported"},
            {"claim_id": "INV-10", "raw": "现金充沛", "status": "verified"},
        ]
        # Real violation: Manager verdict explicitly writes INV-1 is '证据充分'
        raw_output = """【投研经理裁决报告】
经过逐条核验，INV-1证据充分，予以采纳。
<!-- MANAGER_VERDICT: {"winner": "bull", "direction": "看多", "reason": "多头胜", "position_pct": 50, "stop_loss": "120.0", "adopted_claim_ids": ["INV-10"], "partially_adopted_claims": [], "rejected_claim_ids": ["INV-1"]} -->"""

        verdict = extract_and_validate_manager_verdict(
            raw_response=raw_output,
            claims=claims,
            claims_verification=cv,
        )
        assert verdict["consistency_check_passed"] is False
        assert any("正文将未完全核实的 claim INV-1" in err and "证据充分" in err for err in verdict["failed_checks"])
