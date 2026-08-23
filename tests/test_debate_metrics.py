"""Tests for pure function debate metrics calculator (P1-M2).

Uses small handcrafted fixtures where every numerator and denominator can be
re-calculated manually. Does not use golden audit files for tuning.
"""

import pytest
from tradingagents.agents.utils.debate_metrics import (
    calculate_evidence_recycling_rate,
    calculate_seven_reports_utilization,
    calculate_macro_utilization,
    calculate_fundamentals_utilization,
    calculate_bull_bear_verified_rates_and_delta,
    calculate_challenge_metrics,
    calculate_field_completeness_rate,
    calculate_all_debate_metrics,
)


def test_evidence_recycling_rate_with_recycled_numbers():
    """Handcrafted fixture: Round 1 introduces 2 numbers [100, 20]. Round 2 reuses [100] and introduces [50].

    Subsequent round numbers: [100, 50] (denominator = 2).
    Recycled numbers: [100] (numerator = 1).
    Rate = 1/2 = 0.5.
    """
    debate_state = {
        "round_messages": [
            {
                "round_index": 1,
                "message_index": 1,
                "speaker_key": "Bull",
                "claims": [{"claim": "营收增长 100 亿，ROE 达到 20%"}],
            },
            {
                "round_index": 2,
                "message_index": 3,
                "speaker_key": "Bull",
                "claims": [{"claim": "重申营收 100 亿，且净利润 50 亿"}],
            },
        ],
        "claims": [
            {"claim_id": "INV-1", "round_index": 1, "evidence": ["营收 100 亿", "ROE 20%"]},
            {"claim_id": "INV-2", "round_index": 2, "evidence": ["营收 100 亿", "净利 50 亿"]},
        ],
    }
    res = calculate_evidence_recycling_rate(debate_state, version="v1")
    assert res["denominator"] == 2
    assert res["numerator"] == 1
    assert res["rate"] == 0.5
    assert res["status"] == "valid"
    assert res["note"] is None


def test_evidence_recycling_rate_zero_denominator_no_subsequent_rounds():
    """Single round debate: denominator is 0. Rate must be None, typed note emitted, NOT fake 0%."""
    debate_state = {
        "round_messages": [
            {
                "round_index": 1,
                "message_index": 1,
                "speaker_key": "Bull",
                "claims": [{"claim": "营收 100 亿"}],
            }
        ],
        "claims": [
            {"claim_id": "INV-1", "round_index": 1, "evidence": ["营收 100 亿"]},
        ],
    }
    res = calculate_evidence_recycling_rate(debate_state, version="v1")
    assert res["denominator"] == 0
    assert res["numerator"] == 0
    assert res["rate"] is None
    assert res["status"] == "zero_denominator"
    assert "分母为0" in res["note"]


def test_seven_reports_data_utilization():
    """Handcrafted fixture:

    7 reports have 4 distinct numbers:
      - macro: [3.5%]
      - fundamentals: [150亿, 25%]
      - market: [50.0元]
    Debate cites: [150亿, 3.5%] (2 numbers cited).
    Denominator = 4, Numerator = 2, Rate = 0.5.
    """
    seven_reports = {
        "macro_report": "GDP 预期 3.5%",
        "fundamentals_report": "营业收入 150 亿，毛利率 25%",
        "market_report": "支撑位 50.0 元",
        "sentiment_report": "情绪一般",
        "news_report": "无重大新闻",
        "smart_money_report": "资金平稳",
        "volume_price_report": "量价正常",
    }
    claims = [
        {"claim_id": "INV-1", "evidence": ["营收达到 150 亿，宏观增长 3.5%"]},
    ]
    res = calculate_seven_reports_utilization(seven_reports, claims, version="v1")
    assert res["denominator"] == 4
    assert res["numerator"] == 2
    assert res["rate"] == 0.5
    assert res["status"] == "valid"


def test_seven_reports_data_utilization_zero_denominator():
    """When seven reports contain no numbers, denominator=0, rate=None, typed note."""
    seven_reports = {
        "macro_report": "宏观无数据",
        "fundamentals_report": "基本面无数据",
        "market_report": "",
        "sentiment_report": "",
        "news_report": "",
        "smart_money_report": "",
        "volume_price_report": "",
    }
    claims = []
    res = calculate_seven_reports_utilization(seven_reports, claims, version="v1")
    assert res["denominator"] == 0
    assert res["numerator"] == 0
    assert res["rate"] is None
    assert res["status"] == "zero_denominator"
    assert "分母为0" in res["note"]


def test_macro_and_fundamentals_utilization():
    """Macro report has 2 numbers: [5%, 10年]. Debate cites [5%]. Rate = 1/2 = 0.5.

    Fundamentals report has 3 numbers: [100亿, 20%, 30倍]. Debate cites [100亿, 20%]. Rate = 2/3 = 0.6667.
    """
    macro_text = "央行降息 5%，美债 10年 期收益率稳定。"
    fundamentals_text = "营收 100 亿，净利润率 20%，市盈率 30倍。"
    claims = [
        {"claim_id": "INV-1", "evidence": ["降息 5%", "营收 100 亿，净利率 20%"]}
    ]

    macro_res = calculate_macro_utilization(macro_text, claims, version="v1")
    assert macro_res["denominator"] == 2
    assert macro_res["numerator"] == 1
    assert macro_res["rate"] == 0.5
    assert macro_res["status"] == "valid"

    fund_res = calculate_fundamentals_utilization(fundamentals_text, claims, version="v1")
    assert fund_res["denominator"] == 3
    assert fund_res["numerator"] == 2
    assert fund_res["rate"] == pytest.approx(0.6667, abs=1e-3)
    assert fund_res["status"] == "valid"


def test_bull_bear_verified_rates_and_delta():
    """Bull: 4 total, 3 verified -> 75% (0.75).

    Bear: 5 total, 3 verified -> 60% (0.60).
    Delta = |0.75 - 0.60| = 0.15 (15.0 pt).
    """
    claims_summary = {
        "INV-1": {"speaker_key": "Bull", "counts": {"total": 4, "verified": 3}},
        "INV-2": {"speaker_key": "Bear", "counts": {"total": 5, "verified": 3}},
    }
    res = calculate_bull_bear_verified_rates_and_delta(claims_summary, version="v1")
    assert res["bull_verified_rate"]["numerator"] == 3
    assert res["bull_verified_rate"]["denominator"] == 4
    assert res["bull_verified_rate"]["rate"] == 0.75

    assert res["bear_verified_rate"]["numerator"] == 3
    assert res["bear_verified_rate"]["denominator"] == 5
    assert res["bear_verified_rate"]["rate"] == 0.60

    assert res["bull_bear_verified_delta"]["rate"] == 0.15
    assert res["bull_bear_verified_delta"]["status"] == "valid"


def test_bull_bear_verified_rates_zero_denominator():
    """When Bull has 0 evidence items, bull_rate is None with typed note, delta is None."""
    claims_summary = {
        "INV-1": {"speaker_key": "Bull", "counts": {"total": 0, "verified": 0}},
        "INV-2": {"speaker_key": "Bear", "counts": {"total": 2, "verified": 2}},
    }
    res = calculate_bull_bear_verified_rates_and_delta(claims_summary, version="v1")
    assert res["bull_verified_rate"]["denominator"] == 0
    assert res["bull_verified_rate"]["rate"] is None
    assert res["bull_verified_rate"]["status"] == "zero_denominator"

    assert res["bear_verified_rate"]["rate"] == 1.0
    assert res["bull_bear_verified_delta"]["rate"] is None
    assert res["bull_bear_verified_delta"]["status"] == "insufficient_data"


def test_challenge_metrics_legacy():
    """In v1_legacy, challenge metrics must return legacy_no_data status without fabricating 0%."""
    debate_state = {"protocol_version": "v1_legacy"}
    res = calculate_challenge_metrics(debate_state, version="v1_legacy")
    assert res["challenge_count"]["rate"] is None
    assert res["challenge_count"]["status"] == "legacy_no_data"
    assert "v1_legacy" in res["challenge_count"]["note"]

    assert res["challenge_adoption_rate"]["rate"] is None
    assert res["challenge_adoption_rate"]["status"] == "legacy_no_data"

    assert res["challenge_evidence_status"]["status"] == "legacy_no_data"


def test_challenge_metrics_v2():
    """In v2, 3 challenges submitted, 2 adopted. Rate = 2/3 = 0.6667."""
    debate_state = {
        "protocol_version": "v2_structured_disagreement",
        "challenges": [
            {"challenge_id": "CH-1", "target_claim_id": "INV-1", "severity": "fatal", "status": "verified"},
            {"challenge_id": "CH-2", "target_claim_id": "INV-2", "severity": "major", "status": "verified"},
            {"challenge_id": "CH-3", "target_claim_id": "INV-3", "severity": "minor", "status": "unsupported"},
        ],
    }
    manager_verdict = {
        "adopted_challenge_ids": ["CH-1", "CH-2"],
    }
    res = calculate_challenge_metrics(debate_state, manager_verdict=manager_verdict, version="v2_structured_disagreement")
    assert res["challenge_count"]["numerator"] == 3
    assert res["challenge_count"]["denominator"] == 3
    assert res["challenge_count"]["rate"] == 3.0

    assert res["challenge_adoption_rate"]["numerator"] == 2
    assert res["challenge_adoption_rate"]["denominator"] == 3
    assert res["challenge_adoption_rate"]["rate"] == pytest.approx(0.6667, abs=1e-3)
    assert res["challenge_adoption_rate"]["status"] == "valid"

    assert res["challenge_evidence_status"]["verified"] == 2
    assert res["challenge_evidence_status"]["unsupported"] == 1
    assert res["challenge_evidence_status"]["contradicted"] == 0


def test_field_completeness_rate_all_present():
    """confidence=80, probability=0.65, target=100.0, stop=85.0 -> 4/4 = 1.0 (100%)."""
    result_data = {
        "confidence": 80,
        "probability": 0.65,
        "target_price": 100.0,
        "stop_loss_price": 85.0,
        "decision": "BUY",
    }
    res = calculate_field_completeness_rate(result_data)
    assert res["numerator"] == 4
    assert res["denominator"] == 4
    assert res["rate"] == 1.0
    assert res["status"] == "complete"


def test_field_completeness_rate_hold_decision_target_omitted():
    """For HOLD/观望 decision, target_price is legitimately omitted and identified with typed note."""
    result_data = {
        "confidence": 70,
        "probability": 0.50,
        "target_price": None,
        "stop_loss_price": 85.0,
        "decision": "HOLD",
        "direction": "观望",
        "extraction_note": "观望不设目标价",
    }
    res = calculate_field_completeness_rate(result_data)
    # 3 present + 1 legitimate omission identified in note
    assert res["numerator"] == 3
    assert res["denominator"] == 4
    assert res["rate"] == 0.75
    assert "观望不设目标价" in res["note"]


def test_calculate_all_debate_metrics_composite():
    """calculate_all_debate_metrics combines all metrics into a structured dict."""
    result_data = {
        "protocol_version": "v1_legacy",
        "confidence": 65,
        "probability": 0.60,
        "target_price": 50.0,
        "stop_loss_price": 40.0,
        "decision": "BUY",
        "macro_report": "宏观利率 3.0%",
        "fundamentals_report": "营收 100 亿",
        "market_report": "支撑 45 元",
        "sentiment_report": "",
        "news_report": "",
        "smart_money_report": "",
        "volume_price_report": "",
        "investment_debate_state": {
            "protocol_version": "v1_legacy",
            "round_messages": [],
            "claims": [
                {"claim_id": "INV-1", "speaker_key": "Bull", "evidence": ["营收 100 亿"]},
            ],
        },
        "manager_verdict": {
            "claim_evidence_summary": {
                "INV-1": {"speaker_key": "Bull", "counts": {"total": 1, "verified": 1}},
            },
        },
    }
    all_metrics = calculate_all_debate_metrics(result_data)
    assert all_metrics["protocol_version"] == "v1_legacy"
    assert "evidence_recycling" in all_metrics
    assert "seven_reports_utilization" in all_metrics
    assert "macro_utilization" in all_metrics
    assert "fundamentals_utilization" in all_metrics
    assert "bull_bear_verified" in all_metrics
    assert "challenge_metrics" in all_metrics
    assert "field_completeness" in all_metrics
