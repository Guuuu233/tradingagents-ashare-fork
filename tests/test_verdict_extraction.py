"""Tests for verdict extraction fallback chain and key_metrics structured fields (DAV-339 Lane B, DAV-356)."""

import json
import logging
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import report_service
from api.services.report_service import (
    KeyMetricSchema,
    RiskItemSchema,
    StructuredReport,
    _canonicalize_structured_items,
    resolve_report_fields,
)

GOLDEN_DIR = Path(__file__).parent / "golden" / "audit_20260823"


def _make_sqlite_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


# ── B1. Fallback chain tests ───────────────────────────────────────────────────


def test_verdict_json_with_confidence_directly_extracted():
    """1. VERDICT JSON contains confidence -> directly extracted."""
    result_data = {
        "final_trade_decision": (
            "分析总结...\n"
            '<!-- VERDICT: {"direction": "看多", "reason": "突破均线", "confidence": 80} -->'
        ),
        "trader_investment_plan": "**置信度：65**",
    }
    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] == 80
    assert resolved["direction"] == "看多"
    assert resolved["extraction_warning"] is None


def test_verdict_lacks_confidence_fallback_to_trader_plan_regex():
    """2. VERDICT JSON lacks confidence -> fallback to trader_investment_plan regex."""
    result_data = {
        "final_trade_decision": (
            "分析总结...\n"
            '<!-- VERDICT: {"direction": "偏多", "reason": "地量筹码沉淀"} -->'
        ),
        "trader_investment_plan": "机会成立。\n**置信度：65**（数据充分，但存在口径差异）",
    }
    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] == 65
    assert resolved["direction"] == "偏多"


def test_verdict_fallback_000333_golden_fixture():
    """2b. Real 000333 golden fixture fallback: trader_investment_plan regex extracts 65."""
    fixture_file = GOLDEN_DIR / "3c09051e7e364d859dfbe5f1af7cc2c9_result_data.json"
    assert fixture_file.exists(), f"Missing golden fixture: {fixture_file}"
    with open(fixture_file, "r", encoding="utf-8") as fp:
        raw = json.load(fp)
    result_data = raw.get("result_data") or raw
    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] == 65
    assert resolved["direction"] == "偏多"
    assert resolved["target_price"] == 86.2
    assert resolved["stop_loss_price"] == 82.8


def test_trader_plan_lacks_confidence_fallback_to_judge_decision_probability():
    """3. trader_plan also lacks confidence -> fallback to debate_state.judge_decision probability.

    Semantic boundary: judge decision '上涨概率 0.65' -> probability field, MUST NOT be mixed into confidence.
    """
    result_data = {
        "final_trade_decision": (
            "分析总结...\n"
            '<!-- VERDICT: {"direction": "看多", "reason": "多头占优"} -->'
        ),
        "trader_investment_plan": "交易计划中未给出置信度数值。",
        "investment_debate_state": {
            "judge_decision": "风险收益比达 2.10:1，短线上涨概率预估为 **0.65**。"
        },
    }
    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] is None
    assert resolved["probability"] == 0.65
    assert resolved["extraction_warning"] is None


def test_all_missing_sets_extraction_warning():
    """4. All missing -> report flagged with extraction_warning rather than silent null."""
    result_data = {
        "final_trade_decision": (
            "分析总结...\n"
            '<!-- VERDICT: {"direction": "中性", "reason": "无方向"} -->'
        ),
        "trader_investment_plan": "交易计划无置信度说明。",
        "investment_debate_state": {
            "judge_decision": "双方势均力敌，未提供概率估计。"
        },
    }
    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] is None
    assert resolved["probability"] is None
    assert resolved["extraction_warning"] is not None
    assert "缺失" in resolved["extraction_warning"]


def test_hold_decision_sets_extraction_note_for_null_target_price():
    """HOLD decision with null target_price writes extraction_note containing '观望不设目标价'."""
    result_data = {
        "decision": "HOLD",
        "final_trade_decision": (
            "分析总结...\n"
            '<!-- VERDICT: {"direction": "中性", "reason": "维持观望"} -->'
        ),
        "trader_investment_plan": "**当前置信度**：70 / 100",
    }
    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] == 70
    assert resolved["target_price"] is None
    assert resolved["extraction_note"] is not None
    assert "观望不设目标价" in resolved["extraction_note"]


# ── B1b. Probability null explicit note / warning tests (DAV-356) ─────────────


def test_probability_null_when_absent_sets_extraction_note_and_persists_in_result_data():
    """DAV-356 RED: Real production repro (601899.SH): BUY, confidence=68, target=36.8, stop=33.0.

    When all formal texts lack probability/胜率:
    - probability must remain None (no 0, no confidence mapping)
    - extraction_warning must be None (confidence was extracted, no false alarm)
    - extraction_note must explicitly state '概率未提供/未提取'
    - create_report must persist extraction_note into result_data
    """
    result_data = {
        "decision": "BUY",
        "final_trade_decision": (
            "分析总结：技术面突破年线，筹码结构良好。\n"
            '<!-- VERDICT: {"direction": "看多", "reason": "突破均线", "confidence": 68} -->\n'
            "核心目标价：36.8元，初始止损价：33.0元。"
        ),
        "trader_investment_plan": "执行买入方案。目标价格：36.8元，止损价格：33.0元。",
        "investment_debate_state": {
            "judge_decision": "双方辩论充分，多头逻辑占优，建议采纳买入策略。"
        },
    }

    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] == 68
    assert resolved["direction"] == "看多"
    assert resolved["target_price"] == 36.8
    assert resolved["stop_loss_price"] == 33.0
    assert resolved["probability"] is None
    assert resolved["extraction_warning"] is None
    assert resolved["extraction_note"] is not None
    assert "概率未提供/未提取" in resolved["extraction_note"]

    # Verify DB creation and result_data persistence
    db = _make_sqlite_session()
    try:
        report = report_service.create_report(
            db=db,
            symbol="601899.SH",
            trade_date="2026-08-21",
            decision="BUY",
            result_data=result_data,
        )
        assert report.confidence == 68
        assert report.probability is None
        assert report.target_price == 36.8
        assert report.stop_loss_price == 33.0
        assert report.result_data.get("extraction_note") is not None
        assert "概率未提供/未提取" in report.result_data.get("extraction_note")
        assert report.result_data.get("extraction_warning") is None
    finally:
        db.close()


def test_probability_null_with_structured_field_persists_in_structured_object():
    """DAV-356 RED: Structured dictionary inside result_data must also receive extraction_note."""
    result_data = {
        "decision": "BUY",
        "final_trade_decision": (
            "分析总结...\n"
            '<!-- VERDICT: {"direction": "看多", "reason": "突破", "confidence": 68} -->'
        ),
        "trader_investment_plan": "目标价：36.8元，止损价：33.0元。",
        "structured": {
            "decision": "BUY",
            "confidence": 68,
            "probability": None,
            "target_price": 36.8,
            "stop_loss_price": 33.0,
        },
    }

    db = _make_sqlite_session()
    try:
        report = report_service.create_report(
            db=db,
            symbol="601899.SH",
            trade_date="2026-08-21",
            decision="BUY",
            result_data=result_data,
        )
        assert report.result_data["structured"]["probability"] is None
        assert report.result_data["structured"].get("extraction_note") is not None
        assert "概率未提供/未提取" in report.result_data["structured"]["extraction_note"]
        assert report.result_data["structured"].get("extraction_warning") is None
    finally:
        db.close()


def test_hold_decision_with_null_target_and_null_probability_combines_notes():
    """DAV-356: When both HOLD without target price and null probability occur, notes are combined."""
    result_data = {
        "decision": "HOLD",
        "final_trade_decision": (
            "分析总结...\n"
            '<!-- VERDICT: {"direction": "中性", "reason": "维持观望"} -->'
        ),
        "trader_investment_plan": "**当前置信度**：70 / 100",
    }
    resolved = resolve_report_fields(result_data)
    assert resolved["confidence"] == 70
    assert resolved["target_price"] is None
    assert resolved["probability"] is None
    assert resolved["extraction_warning"] is None
    assert resolved["extraction_note"] is not None
    assert "观望不设目标价" in resolved["extraction_note"]
    assert "概率未提供/未提取" in resolved["extraction_note"]


# ── B2. key_metrics and risk_items structured field tests ─────────────────────


def test_key_metric_evaluation_field_preserved_and_no_warning(caplog):
    """B2: key_metrics structured parser preserves 'evaluation' field without unknown field warning."""
    raw_key_metrics = [
        {
            "name": "PE",
            "value": "28.5x",
            "status": "neutral",
            "evaluation": "估值处于历史中枢偏高位置",
        }
    ]
    with caplog.at_level(logging.WARNING):
        canonical = _canonicalize_structured_items(raw_key_metrics, KeyMetricSchema, "key_metrics")

    assert canonical is not None
    assert len(canonical) == 1
    assert canonical[0]["name"] == "PE"
    assert canonical[0]["value"] == "28.5x"
    assert canonical[0]["status"] == "neutral"
    assert canonical[0]["evaluation"] == "估值处于历史中枢偏高位置"

    # Must NOT log unknown fields warning for evaluation
    assert not any("unknown structured fields ignored for key metric: evaluation" in r.message for r in caplog.records)


def test_risk_item_statement_field_preserved_and_no_warning(caplog):
    """B2: risk_items structured parser preserves 'statement' field without unknown field warning."""
    raw_risk_items = [
        {
            "name": "流动性风险",
            "level": "high",
            "description": "成交量低迷",
            "statement": "极端缩量环境下市价止损滑点冲击超预期",
        }
    ]
    with caplog.at_level(logging.WARNING):
        canonical = _canonicalize_structured_items(raw_risk_items, RiskItemSchema, "risk_items")

    assert canonical is not None
    assert len(canonical) == 1
    assert canonical[0]["name"] == "流动性风险"
    assert canonical[0]["level"] == "high"
    assert canonical[0]["statement"] == "极端缩量环境下市价止损滑点冲击超预期"

    # Must NOT log unknown fields warning for statement
    assert not any("unknown structured fields ignored for risk item: statement" in r.message for r in caplog.records)


# ── Golden fixtures replay tests ──────────────────────────────────────────────


def test_golden_replay_all_three_symbols():
    """Acceptance criteria 1: Offline replay of three golden fixtures.

    - 600900 -> confidence 60, target 29.2, stop_loss 27.5
    - 000333 -> confidence 65 (trader_plan fallback), target 86.2, stop_loss 82.8
    - 600276 -> confidence 70, target None, extraction_note="观望不设目标价"
    """
    cases = [
        {
            "file": "597f6cf371114a3b9844112238a0f1a9_result_data.json",
            "expected_confidence": 60,
            "expected_target": 29.2,
            "expected_stop_loss": 27.5,
            "expected_note": "概率未提供/未提取",
        },
        {
            "file": "3c09051e7e364d859dfbe5f1af7cc2c9_result_data.json",
            "expected_confidence": 65,
            "expected_target": 86.2,
            "expected_stop_loss": 82.8,
            "expected_note": None,
        },
        {
            "file": "ba255b88dfa446279c2d6e9529be6f5e_result_data.json",
            "expected_confidence": 70,
            "expected_target": None,
            "expected_stop_loss": None,
            "expected_note": "观望不设目标价",
        },
    ]

    for c in cases:
        path = GOLDEN_DIR / c["file"]
        assert path.exists(), f"Fixture file not found: {path}"
        with open(path, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
        data = raw.get("result_data") or raw
        resolved = resolve_report_fields(data)

        assert resolved["confidence"] == c["expected_confidence"], (
            f"{c['file']}: confidence expected {c['expected_confidence']}, got {resolved['confidence']}"
        )
        assert resolved["target_price"] == c["expected_target"], (
            f"{c['file']}: target_price expected {c['expected_target']}, got {resolved['target_price']}"
        )
        assert resolved["stop_loss_price"] == c["expected_stop_loss"], (
            f"{c['file']}: stop_loss_price expected {c['expected_stop_loss']}, got {resolved['stop_loss_price']}"
        )
        if c["expected_note"]:
            assert resolved["extraction_note"] is not None, (
                f"{c['file']}: extraction_note expected to contain {c['expected_note']!r}, got None"
            )
            assert c["expected_note"] in resolved["extraction_note"], (
                f"{c['file']}: extraction_note expected to contain {c['expected_note']!r}, got {resolved['extraction_note']!r}"
            )
        else:
            assert resolved["extraction_note"] is None, (
                f"{c['file']}: extraction_note expected None, got {resolved['extraction_note']!r}"
            )
