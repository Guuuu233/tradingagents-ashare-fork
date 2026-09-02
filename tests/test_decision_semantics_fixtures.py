"""Contract and regression tests for D-009 / decision-semantics-workflow fixtures (R1, R2, R3).

Authoritative rule:
    R1/R2/R3 离线 fixture 齐备并通过前，不得声称历史案例“已修复”。

Samples:
- R1 歌尔股份 002241.SZ / 2026-05-28 (unconfirmed core claims -> WAIT / confirmation state machine & PIT)
- R2 工业富联 601138.SH / 2026-07-30 (news timestamp PIT cutoff & lookahead denial)
- R3 蓝思科技 300433.SZ / 2026-05-06 (7/7 analyst failures -> INVALID_RUN / DATA_ERROR & calibration exclusion)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, ReportDB
from api.services.calibration_service import compute_calibration
from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.trader.trader import create_trader
from tradingagents.agents.utils.decision_status import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_NO_TRADE,
    ACTION_WAIT,
    ANALYSIS_DATA_ERROR,
    ANALYSIS_INVALID_RUN,
    ANALYSIS_VALID,
    CONFIRM_CONFIRMED,
    CONFIRM_PARTIAL,
    CONFIRM_UNRESOLVED,
    DIRECTION_BEAR,
    DIRECTION_BULL,
    DIRECTION_NA,
    evaluate_confirmation_state,
    is_calibration_eligible,
    is_non_executable_status,
    status_from_manager_verdict,
)
from tradingagents.agents.utils.run_integrity import evaluate_run_integrity
from tradingagents.dataflows.news_event_evidence import build_news_event_coverage

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "decision_semantics"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"
R1_FIXTURE_PATH = FIXTURES_DIR / "r1_goertek_fixture.json"
R2_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_events" / "r2_news_fixture.json"
R3_FIXTURE_PATH = FIXTURES_DIR / "r3_lens_fixture.json"


def _mock_llm(content: str = "SHOULD_NOT_BE_CALLED"):
    llm = MagicMock()
    calls = {"n": 0}

    async def _astream(*_a, **_k):
        calls["n"] += 1
        yield MagicMock(content=content)

    llm.astream = _astream
    return llm, calls


# ── 1. Manifest Catalog & Integrity ──────────────────────────────────────────


def test_decision_semantics_manifest_catalog():
    """Verify that manifest.json registers all three regression samples with required metadata."""
    assert MANIFEST_PATH.exists() is True
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["version"] == "1.0.0"
    assert manifest["name"] == "decision_semantics_regression_fixtures"
    assert "R1/R2/R3 离线 fixture 齐备并通过前" in manifest["authority_rule"]

    samples = manifest["samples"]
    assert len(samples) == 3
    assert set(samples.keys()) == {"R1", "R2", "R3"}

    # R1 check
    r1_meta = samples["R1"]
    assert r1_meta["fixture_id"] == "R1"
    assert r1_meta["symbol"] == "002241.SZ"
    assert r1_meta["trade_date"] == "2026-05-28"
    assert r1_meta["expected_analysis_status"] == "VALID"
    assert r1_meta["expected_trade_action"] == "WAIT"
    assert r1_meta["expected_confirmation_state"] == "UNRESOLVED"
    assert r1_meta["calibration_eligible"] is False
    assert (FIXTURES_DIR / r1_meta["fixture_file"]).resolve().exists() is True

    # R2 check
    r2_meta = samples["R2"]
    assert r2_meta["fixture_id"] == "R2"
    assert r2_meta["symbol"] == "601138.SH"
    assert r2_meta["trade_date"] == "2026-07-30"
    assert (FIXTURES_DIR / r2_meta["fixture_file"]).resolve().exists() is True

    # R3 check
    r3_meta = samples["R3"]
    assert r3_meta["fixture_id"] == "R3"
    assert r3_meta["symbol"] == "300433.SZ"
    assert r3_meta["trade_date"] == "2026-05-06"
    assert r3_meta["expected_analysis_status"] == "INVALID_RUN"
    assert r3_meta["expected_failure_class"] == "DATA_ERROR"
    assert r3_meta["expected_trade_action"] == "NO_TRADE"
    assert r3_meta["expected_direction"] == "N/A"
    assert r3_meta["expected_all_required_failed"] is True
    assert r3_meta["expected_failed_required_count"] == 7
    assert r3_meta["calibration_eligible"] is False
    assert (FIXTURES_DIR / r3_meta["fixture_file"]).resolve().exists() is True


# ── 2. R1 Goertek: Unconfirmed Core Claims & WAIT State Machine ──────────────


def test_r1_goertek_fixture_structure_and_pit_boundary():
    """Verify R1 fixture loading, metadata, and Point-in-Time baseline boundary."""
    assert R1_FIXTURE_PATH.exists() is True
    with open(R1_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    manifest = data["manifest"]
    assert manifest["fixture_id"] == "R1"
    assert manifest["symbol"] == "002241.SZ"
    assert manifest["trade_date"] == "2026-05-28"
    assert manifest["baseline_date"] == "2026-05-28"
    assert "Point-in-Time" in manifest["coverage_boundaries"]["coverage_scope"]

    # Top-level fields
    assert data["company_of_interest"] == "002241.SZ"
    assert data["trade_date"] == "2026-05-28"
    assert data["horizon"] == "medium"
    assert data["market_data_context"]["analysis_baseline_date"] == "2026-05-28"
    assert data["market_data_context"]["source_provenance"]["stock_data"]["as_of"] == "2026-05-28"

    # Seven reports present
    seven = data["seven_reports"]
    for key in (
        "macro_report",
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "smart_money_report",
        "volume_price_report",
    ):
        assert key in seven
        assert len(seven[key]) > 20


def test_r1_goertek_confirmation_state_and_action_routing():
    """Test R1 unconfirmed core claims evaluation leads strictly to UNRESOLVED + WAIT."""
    with open(R1_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    inv_state = data["investment_debate_state"]
    focus_ids = inv_state["focus_claim_ids"]
    claims_ver = inv_state["claims_verification"]
    claim_ev_summary = inv_state["claim_evidence_summary"]

    assert focus_ids == ["CLM-1", "CLM-2"]
    assert len(claims_ver) == 2
    assert claims_ver[0]["status"] == "unsupported"
    assert claims_ver[1]["status"] == "unsupported"

    # Evaluate confirmation state
    confirm_state, reason_codes = evaluate_confirmation_state(
        focus_claim_ids=focus_ids,
        unresolved_claim_ids=inv_state["unresolved_claim_ids"],
        claims_verification=claims_ver,
        claim_evidence_summary=claim_ev_summary,
        claims=inv_state["claims"],
    )
    assert confirm_state == CONFIRM_UNRESOLVED
    assert "unverified_core_claims:CLM-1,CLM-2" in reason_codes

    # Status from manager verdict
    mv = data["manager_verdict"]
    status = status_from_manager_verdict(
        mv,
        investment_debate_state=inv_state,
        claims_verification=claims_ver,
        claim_evidence_summary=claim_ev_summary,
        focus_claim_ids=focus_ids,
        unresolved_claim_ids=inv_state["unresolved_claim_ids"],
        claims=inv_state["claims"],
        market_data_context=data["market_data_context"],
    )

    assert status.analysis_status == ANALYSIS_VALID
    assert status.confirmation_state == CONFIRM_UNRESOLVED
    assert status.trade_action == ACTION_WAIT
    assert status.direction == DIRECTION_BULL
    assert status.risk_status == "OK"
    assert status.confidence is None  # WAIT strips confidence

    # Non-executable and calibration eligibility checks
    assert is_non_executable_status(status) is True
    assert is_calibration_eligible(status) is False


def test_r1_goertek_research_manager_and_trader_node_execution():
    """Test that Research Manager node and Trader node handle R1 fixture without buy execution."""
    with open(R1_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Manager execution with mocked LLM
    verdict_text = """
### 辩论裁决与总结方案
【多空辩论五步深度裁决】
多头关于新产线良率的主张具有进攻性，空头关于砍单的质疑缺乏一手数据。
综合评定：多头胜。
建议仓位：50%，止损位：28.50元。
<!-- VERDICT: {"direction": "看多", "winner": "bull", "reason": "看好新产线交付", "position_pct": 50, "entry": "30.00", "target": "35.00", "stop_loss": "28.50", "confidence": 75, "probability": 0.70} -->
"""
    manager_llm, manager_calls = _mock_llm(verdict_text)
    memory = MagicMock()
    memory.get_memories.return_value = []
    manager_node = create_research_manager(manager_llm, memory)

    manager_res = asyncio.run(manager_node(data))
    assert manager_calls["n"] == 1
    assert manager_res["analysis_status"] == ANALYSIS_VALID
    assert manager_res["confirmation_state"] == CONFIRM_UNRESOLVED
    assert manager_res["trade_action"] == ACTION_WAIT
    assert manager_res["decision_status"]["trade_action"] == ACTION_WAIT
    assert manager_res["decision_status"]["confirmation_state"] == CONFIRM_UNRESOLVED

    # Trader execution: must short-circuit on WAIT without LLM call
    trader_state = {
        "company_of_interest": "002241.SZ",
        "investment_plan": manager_res["investment_plan"],
        "trader_investment_plan": "",
        "market_report": data["market_report"],
        "sentiment_report": data["sentiment_report"],
        "news_report": data["news_report"],
        "fundamentals_report": data["fundamentals_report"],
        "risk_feedback_state": {},
        "fund_flow_consensus_guard": data["fund_flow_consensus_guard"],
        "decision_status": manager_res["decision_status"],
        "analysis_status": manager_res["analysis_status"],
        "trade_action": manager_res["trade_action"],
        "confirmation_state": manager_res["confirmation_state"],
        "instrument_context": {},
        "market_context": {},
        "user_context": {},
    }
    trader_llm, trader_calls = _mock_llm("次日开仓买入 50% 仓位")
    trader_node = create_trader(trader_llm, memory)
    trader_res = asyncio.run(trader_node(trader_state))

    assert trader_calls["n"] == 0
    assert (
        "观望" in trader_res["trader_investment_plan"]
        or "WAIT" in trader_res["trader_investment_plan"]
        or "NO_TRADE" in trader_res["trader_investment_plan"]
    )
    assert "买入 50%" not in trader_res["trader_investment_plan"]
    assert "次日开仓" not in trader_res["trader_investment_plan"]


def test_r1_goertek_evidence_verification_transition():
    """Test state transition: when R1 core claims have verified evidence, status transitions to CONFIRMED + BUY."""
    with open(R1_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    inv_state = data["investment_debate_state"]
    focus_ids = inv_state["focus_claim_ids"]

    # Simulate verified evidence for both core claims
    verified_claims_ver = [
        {"claim_id": "CLM-1", "status": "verified", "raw": "良率数据经第三方审计报告证实"},
        {"claim_id": "CLM-2", "status": "verified", "raw": "砍单传闻已被上市公司辟谣公告澄清"},
    ]
    verified_evidence_summary = {
        "CLM-1": {
            "counts": {"total": 2, "verified": 2, "unsupported": 0, "contradicted": 0, "source_unavailable": 0},
            "coverage": 1.0,
            "decision": "adopt",
        },
        "CLM-2": {
            "counts": {"total": 2, "verified": 2, "unsupported": 0, "contradicted": 0, "source_unavailable": 0},
            "coverage": 1.0,
            "decision": "adopt",
        },
    }

    confirm_state, reason_codes = evaluate_confirmation_state(
        focus_claim_ids=focus_ids,
        unresolved_claim_ids=inv_state["unresolved_claim_ids"],
        claims_verification=verified_claims_ver,
        claim_evidence_summary=verified_evidence_summary,
        claims=inv_state["claims"],
    )
    assert confirm_state == CONFIRM_CONFIRMED
    assert "all_core_claims_verified:CLM-1,CLM-2" in reason_codes

    status = status_from_manager_verdict(
        data["manager_verdict"],
        investment_debate_state=inv_state,
        claims_verification=verified_claims_ver,
        claim_evidence_summary=verified_evidence_summary,
        focus_claim_ids=focus_ids,
        unresolved_claim_ids=inv_state["unresolved_claim_ids"],
        claims=inv_state["claims"],
        market_data_context=data["market_data_context"],
    )
    assert status.analysis_status == ANALYSIS_VALID
    assert status.confirmation_state == CONFIRM_CONFIRMED
    assert status.trade_action == ACTION_BUY
    assert status.direction == DIRECTION_BULL
    assert is_non_executable_status(status) is False


# ── 3. R3 Lens: 7/7 Analyst Failures & Calibration Isolation ─────────────────


def test_r3_lens_fixture_structure_and_seven_failures():
    """Verify R3 fixture loading, metadata, and 7/7 failure stubs structure."""
    assert R3_FIXTURE_PATH.exists() is True
    with open(R3_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    manifest = data["manifest"]
    assert manifest["fixture_id"] == "R3"
    assert manifest["symbol"] == "300433.SZ"
    assert manifest["trade_date"] == "2026-05-06"
    assert manifest["expected_contract"]["analysis_status"] == "INVALID_RUN"
    assert manifest["expected_contract"]["failure_class"] == "DATA_ERROR"
    assert manifest["expected_contract"]["trade_action"] == "NO_TRADE"
    assert manifest["expected_contract"]["failed_required_count"] == 7

    # Top-level fields
    assert data["symbol"] == "300433.SZ"
    assert data["trade_date"] == "2026-05-06"
    assert data["analysis_status"] == "INVALID_RUN"
    assert data["trade_action"] == "NO_TRADE"
    assert data["direction"] == "N/A"
    assert data["decision"] == "NO_TRADE"

    # Check that all 7 reports contain the standard failure prefix
    seven = data["seven_reports"]
    required_keys = (
        "macro_report",
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "smart_money_report",
        "volume_price_report",
    )
    for key in required_keys:
        assert key in seven
        assert seven[key].startswith("分析报告生成失败")
        assert "502" in seven[key]


def test_r3_lens_run_integrity_evaluation():
    """Test that evaluate_run_integrity on R3 seven reports resolves to INVALID_RUN / DATA_ERROR."""
    with open(R3_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    integrity = evaluate_run_integrity(
        data["seven_reports"],
        analyst_traces=data.get("analyst_traces"),
    )

    assert integrity.all_required_failed is True
    assert integrity.failed_required_count == 7
    assert integrity.required_count == 7
    assert len(integrity.available_required) == 0
    assert integrity.analysis_status == ANALYSIS_INVALID_RUN
    assert integrity.failure_class == ANALYSIS_DATA_ERROR
    assert integrity.decision_status["analysis_status"] == ANALYSIS_INVALID_RUN
    assert integrity.decision_status["trade_action"] == ACTION_NO_TRADE
    assert integrity.decision_status["direction"] == DIRECTION_NA
    assert integrity.decision_status["confirmation_state"] == CONFIRM_UNRESOLVED
    assert integrity.decision_status["failure_class"] == ANALYSIS_DATA_ERROR
    assert "analyst_upstream_7_of_7_failed" in integrity.reason_codes


def test_r3_lens_research_manager_and_trader_pre_llm_short_circuit():
    """Test that Research Manager and Trader nodes short-circuit with 0 LLM calls on R3."""
    with open(R3_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    manager_llm, manager_calls = _mock_llm("SHOULD_NOT_BE_CALLED")
    memory = MagicMock()
    memory.get_memories.return_value = []
    manager_node = create_research_manager(manager_llm, memory)

    manager_res = asyncio.run(manager_node(data))
    assert manager_calls["n"] == 0
    assert manager_res["analysis_status"] == ANALYSIS_INVALID_RUN
    assert manager_res["trade_action"] == ACTION_NO_TRADE
    assert manager_res["decision_status"]["direction"] == DIRECTION_NA
    assert manager_res["manager_verdict"]["direction"] == DIRECTION_NA
    assert manager_res["manager_verdict"]["winner"] == "tie"
    assert manager_res["manager_verdict"]["consistency_check_passed"] is False
    assert "INVALID_RUN" in manager_res["investment_plan"] or "NO_TRADE" in manager_res["investment_plan"]

    # Pass to Trader
    trader_state = {
        "company_of_interest": "300433.SZ",
        "investment_plan": manager_res["investment_plan"],
        "trader_investment_plan": "",
        "market_report": data["market_report"],
        "sentiment_report": data["sentiment_report"],
        "news_report": data["news_report"],
        "fundamentals_report": data["fundamentals_report"],
        "risk_feedback_state": {},
        "fund_flow_consensus_guard": data["fund_flow_consensus_guard"],
        "decision_status": manager_res["decision_status"],
        "analysis_status": manager_res["analysis_status"],
        "trade_action": manager_res["trade_action"],
        "instrument_context": {},
        "market_context": {},
        "user_context": {},
    }
    trader_llm, trader_calls = _mock_llm("SHOULD_NOT_BE_CALLED")
    trader_node = create_trader(trader_llm, memory)
    trader_res = asyncio.run(trader_node(trader_state))

    assert trader_calls["n"] == 0
    assert trader_res["analysis_status"] == ANALYSIS_INVALID_RUN
    assert trader_res["trade_action"] == ACTION_NO_TRADE
    assert "NO_TRADE" in trader_res["trader_investment_plan"]


def test_r3_lens_calibration_isolation_and_db_exclusion():
    """Test that R3 fixture is strictly excluded from calibration evaluation and DB reports."""
    with open(R3_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. Direct function checks
    assert is_calibration_eligible(data) is False
    assert is_calibration_eligible(data, allow_winner_only=True) is False

    # 2. Database-backed calibration evaluation
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()

    try:
        user_id = "test-r3-user"
        report_row = ReportDB(
            id="r3-lens-report-001",
            user_id=user_id,
            symbol="300433.SZ",
            trade_date="2026-05-06",
            status="completed",
            analysis_status="INVALID_RUN",
            trade_action="NO_TRADE",
            direction="N/A",
            decision="NO_TRADE",
            probability=None,
            confidence=None,
            result_data=data,
        )
        db.add(report_row)
        db.commit()

        calib_res = compute_calibration(
            db,
            user_id=user_id,
            symbol="300433.SZ",
            start_date="2026-05-01",
            end_date="2026-05-10",
            outcome_resolver=lambda r: True,
        )

        assert calib_res["sample_size"] == 0
        assert calib_res["winner_only_admitted"] == 0
        assert calib_res["excluded_invalid"] == 1
        assert calib_res["excluded_total"] >= 1
        assert calib_res["brier_score"] is None
        assert calib_res["winner_only_hit_rate"] is None
    finally:
        db.close()


# ── 4. Unified R1, R2, R3 Regression Suite Verification ──────────────────────


def test_all_three_regression_samples_offline_acceptance():
    """Unified regression test asserting R1, R2, and R3 offline fixtures all pass strictly."""
    # R1: Goertek unconfirmed focus claims -> WAIT
    assert R1_FIXTURE_PATH.exists() is True
    with open(R1_FIXTURE_PATH, "r", encoding="utf-8") as f:
        r1_data = json.load(f)
    assert r1_data["company_of_interest"] == "002241.SZ"
    assert r1_data["trade_date"] == "2026-05-28"
    assert r1_data["decision_status"]["trade_action"] == "WAIT"
    assert r1_data["decision_status"]["confirmation_state"] == "UNRESOLVED"
    assert is_calibration_eligible(r1_data["decision_status"]) is False

    # R2: Foxconn news timestamp PIT cutoff & event coverage
    assert R2_FIXTURE_PATH.exists() is True
    with open(R2_FIXTURE_PATH, "r", encoding="utf-8") as f:
        r2_data = json.load(f)
    r2_coverage = build_news_event_coverage(
        r2_data["items"],
        cutoff=r2_data["cutoff"],
        window=r2_data["window"],
        requested_themes=r2_data["requested_themes"],
        default_entity=r2_data.get("entity", ""),
    )
    assert r2_coverage["cutoff"] == "2026-07-30"
    assert r2_coverage["hit_count"] == 1
    assert r2_coverage["unverifiable_count"] == 2
    assert r2_coverage["future_rejected_count"] == 1
    assert r2_coverage["valid_evidence_count"] == 2
    gap_themes = [g["theme"] for g in r2_coverage["suspected_gaps"]]
    assert "财报" in gap_themes
    assert "行业政策" in gap_themes

    # R3: Lens 7/7 failures -> INVALID_RUN / DATA_ERROR / NO_TRADE
    assert R3_FIXTURE_PATH.exists() is True
    with open(R3_FIXTURE_PATH, "r", encoding="utf-8") as f:
        r3_data = json.load(f)
    assert r3_data["symbol"] == "300433.SZ"
    assert r3_data["trade_date"] == "2026-05-06"
    r3_integrity = evaluate_run_integrity(r3_data["seven_reports"])
    assert r3_integrity.all_required_failed is True
    assert r3_integrity.analysis_status == "INVALID_RUN"
    assert r3_integrity.failure_class == "DATA_ERROR"
    assert r3_integrity.decision_status["trade_action"] == "NO_TRADE"
    assert is_calibration_eligible(r3_data) is False
