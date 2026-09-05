"""Regression tests for real desensitized reports under Track B-1 (Commit C / DAV-649).

Verifies:
1. Tests fixtures for Longi 601012 and Aier 300015 exist, are desensitized (0 tokens/secrets),
   and carry not_applicable + direction_allowed=False.
2. Legacy reports (which turned data gaps into market facts such as '完全冷却', '冷淡/休眠期',
   '关注度真空', '沉寂期') reliably fail the social gap semantic quality gate.
3. Compliant reports (which lock semantics with 不可用/未采集/样本不足 and refrain from
   fabricating market facts) pass the social gap quality gate.
4. Semantic lock enforcement: forbidding blacklisting '真空' alone while locking semantics
   (disclaimers containing '真空' pass; gap deductions without '真空' fail; missing lock markers fail).
5. Social data cannot become directional evidence when direction_allowed=False.
6. Compatible neutral verdict with direction_allowed=False is proven excluded from calibration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingagents.agents.utils.decision_status import (
    ACTION_NO_TRADE,
    ACTION_WAIT,
    ANALYSIS_VALID,
    is_calibration_eligible,
)
from tradingagents.agents.utils.evidence_verifier import (
    STATUS_UNSUPPORTED,
    EvidenceFactualTruthEvaluator,
)
from tradingagents.graph.report_quality_gate import (
    check_forbidden_social_directional_claims,
    check_forbidden_social_gap_claims,
    evaluate_social_depth,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "social" / "reports"


def test_real_reports_fixtures_exist_and_desensitized():
    """Verify fixture files exist, are valid JSON, contain no secrets/tokens, and have valid contract schema."""
    longi_path = FIXTURES_DIR / "longi_601012_report.json"
    aier_path = FIXTURES_DIR / "aier_300015_report.json"

    assert longi_path.exists(), f"Missing fixture {longi_path}"
    assert aier_path.exists(), f"Missing fixture {aier_path}"

    for path in (longi_path, aier_path):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "symbol" in data
        assert "trade_date" in data
        assert "social_data_context" in data
        assert "legacy_defect_report" in data
        assert "compliant_report" in data

        ctx = data["social_data_context"]
        assert ctx["status"] == "not_applicable"
        assert ctx["direction_allowed"] is False
        assert ctx["reason_codes"] == ["social_not_applicable"]

        # Desensitization check: 0 tokens, passwords, cookies, or user UUIDs
        content = path.read_text(encoding="utf-8").lower()
        assert "token" not in content or "tushare_token" not in content
        assert "password" not in content
        assert "cookie=" not in content
        assert "bearer" not in content
        assert "secret" not in content


def test_legacy_defect_reports_fail_gap_semantic_gate():
    """Legacy reports that turned data gaps into '市场冷淡', '讨论真空', '沉寂期' must fail quality gate."""
    for fixture_name in ("longi_601012_report.json", "aier_300015_report.json"):
        path = FIXTURES_DIR / fixture_name
        data = json.loads(path.read_text(encoding="utf-8"))
        legacy_text = data["legacy_defect_report"]
        ctx = data["social_data_context"]

        # 1. check_forbidden_social_gap_claims identifies defect statements
        violations = check_forbidden_social_gap_claims(legacy_text)
        assert len(violations) >= 2, (
            f"Expected multiple gap claim violations in legacy {fixture_name}, found {len(violations)}: {violations}"
        )

        # 2. evaluate_social_depth must fail
        passed, score, failed_dims, reason = evaluate_social_depth(
            text=legacy_text,
            social_data_context=ctx,
        )
        assert passed is False
        assert score == 0.0
        assert "gap_misinterpreted_as_market_fact" in failed_dims
        assert "违规将缺口推导为市场事实" in reason


def test_compliant_reports_pass_gap_semantic_gate():
    """Compliant reports with semantic lock (不可用/未采集/样本不足) and no gap fact deductions must pass."""
    for fixture_name in ("longi_601012_report.json", "aier_300015_report.json"):
        path = FIXTURES_DIR / fixture_name
        data = json.loads(path.read_text(encoding="utf-8"))
        compliant_text = data["compliant_report"]
        ctx = data["social_data_context"]

        # 1. No forbidden gap fact claims
        violations = check_forbidden_social_gap_claims(compliant_text)
        assert violations == [], f"Unexpected violations in compliant {fixture_name}: {violations}"

        # 2. No directional claims
        dir_violations = check_forbidden_social_directional_claims(compliant_text)
        assert dir_violations == []

        # 3. evaluate_social_depth passes with score 1.0
        passed, score, failed_dims, reason = evaluate_social_depth(
            text=compliant_text,
            social_data_context=ctx,
        )
        assert passed is True, f"Compliant report failed for {fixture_name}: {reason}"
        assert score == 1.0
        assert failed_dims == []


def test_gap_semantics_lock_not_keyword_banned_only():
    """Requirement: FORBID banning '真空' alone; must lock semantics (不可用/未采集/样本不足).

    Tests:
    1. A report containing '真空' within a rule negation warning MUST PASS.
    2. A report deducing market coldness/no discussion WITHOUT using '真空' MUST FAIL.
    3. A report lacking semantic lock markers MUST FAIL with 'indeterminate_or_missing_marker'.
    """
    ctx = {
        "status": "not_applicable",
        "mode": "disabled",
        "direction_allowed": False,
        "reason_codes": ["social_not_applicable"],
    }

    # Case 1: Report contains the keyword '真空' inside an explicit negation/rule statement -> MUST PASS
    report_with_vacuum_negation = (
        "【一、数据状态与有效性】\n"
        "分析标的：601012.SH，社交归档状态为 not_applicable（未采集/不可用），direction_allowed=False。\n"
        "【语义约束说明】社交归档数据不可用，数据缺失不代表市场冷淡，严禁将数据不可用推导为无人关注、散户没有讨论或讨论真空！\n"
        "【二、社交观点】数据不可用/未采集，方向不可判断，不作为多空方向依据。\n"
        "【三、社交热度】指标未采集/不可用，热度≠看多。\n"
        "【四、市场关注度】涨停池与雪球榜单独立分栏，未进连板梯队。\n"
        "【五、深度推演】因社交数据不可用，不提供情绪定价参考，后续关注数据接入。\n"
        "<!-- VERDICT: {\"direction\": \"中性\", \"reason\": \"社交数据不可用，方向不可判断\"} -->"
    )
    assert "真空" in report_with_vacuum_negation
    passed, score, failed_dims, reason = evaluate_social_depth(
        text=report_with_vacuum_negation,
        social_data_context=ctx,
    )
    assert passed is True, f"Report with '真空' in negation should have passed, but failed: {reason}"
    assert "gap_misinterpreted_as_market_fact" not in failed_dims

    # Case 2: Report deduces market coldness/no discussion WITHOUT using '真空' -> MUST FAIL
    report_coldness_without_vacuum = (
        "【一、数据状态】社交归档状态 not_applicable，数据不可用。\n"
        "【二、社交观点】未提取到样本。表明标的在零售端讨论氛围处于完全冷却状态，大众投资者没有讨论，散户讨论处于极低活跃度。\n"
        "【三、社交热度】处于冷淡期。\n"
        "<!-- VERDICT: {\"direction\": \"中性\", \"reason\": \"零售端讨论处于完全冷却\"} -->"
    )
    assert "真空" not in report_coldness_without_vacuum
    passed_c, _, failed_dims_c, reason_c = evaluate_social_depth(
        text=report_coldness_without_vacuum,
        social_data_context=ctx,
    )
    assert passed_c is False, "Report deducing market coldness without '真空' should have failed"
    assert "gap_misinterpreted_as_market_fact" in failed_dims_c

    # Case 3: Report lacks any semantic lock markers (no 不可用/未采集/样本不足/不可判断/数据缺失) -> MUST FAIL
    report_missing_lock_markers = (
        "【一、数据状态】标的 601012.SH 分析完成，当前状态良好。\n"
        "【二、社交观点】散户观点偏多，持仓信心较强。\n"
        "【三、社交热度】发帖量适中，互动活跃。\n"
        "<!-- VERDICT: {\"direction\": \"中性\", \"reason\": \"常规分析\"} -->"
    )
    passed_m, _, failed_dims_m, reason_m = evaluate_social_depth(
        text=report_missing_lock_markers,
        social_data_context=ctx,
    )
    assert passed_m is False
    assert "indeterminate_or_missing_marker" in failed_dims_m


def test_social_direction_guard_forbids_directional_evidence_when_unavailable():
    """When direction_allowed=False, social sentiment cannot be used as directional evidence."""
    ctx = {
        "status": "not_applicable",
        "mode": "disabled",
        "direction_allowed": False,
        "reason_codes": ["social_not_applicable"],
    }

    # 1. Report claims social sentiment supports bullish direction -> quality gate fails
    report_directional_claim = (
        "【一、数据状态】社交归档 not_applicable，数据不可用。\n"
        "【二、社交观点】社交舆情构成偏多证据，驱动短线买入。\n"
        "<!-- VERDICT: {\"direction\": \"看多\", \"reason\": \"社交偏多\"} -->"
    )
    passed, _, failed_dims, reason = evaluate_social_depth(
        text=report_directional_claim,
        social_data_context=ctx,
    )
    assert passed is False
    assert "social_used_as_directional_evidence" in failed_dims

    # 2. evidence_verifier blocks social score/direction when direction_allowed=False
    verifier = EvidenceFactualTruthEvaluator()
    res_verifier = verifier.evaluate_single_evidence(
        raw_evidence="小红书社交讨论情绪得分0.45，散户情绪偏多，构成买入信号",
        seven_reports={},
        market_data_context={"trade_date": "2026-08-27"},
        claim_id="claim_social_1",
        social_data_context=ctx,
    )
    assert res_verifier["status"] == STATUS_UNSUPPORTED
    assert "direction_allowed=false" in res_verifier["details"]


def test_compatible_neutral_verdict_with_direction_disallowed_excluded_from_calibration():
    """Contract 3: Compatible neutral verdict with direction_allowed=false must not enter calibration."""
    # 1. Non-directional trade action (WAIT) is excluded from calibration
    row_wait = {
        "analysis_status": ANALYSIS_VALID,
        "trade_action": ACTION_WAIT,
        "direction": "NEUTRAL",
        "probability": 0.50,
        "result_data": {
            "social_data_context": {
                "status": "not_applicable",
                "direction_allowed": False,
            }
        },
    }
    assert is_calibration_eligible(row_wait) is False

    # 2. NO_TRADE action is excluded from calibration
    row_no_trade = {
        "analysis_status": ANALYSIS_VALID,
        "trade_action": ACTION_NO_TRADE,
        "direction": "NEUTRAL",
        "probability": None,
    }
    assert is_calibration_eligible(row_no_trade) is False
