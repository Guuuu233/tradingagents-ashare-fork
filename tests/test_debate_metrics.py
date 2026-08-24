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


def test_evidence_recycling_rate_real_shape_debate_round():
    """S1: Real shape round_messages containing debate_round, message_index, cleaned_prose, claims (no round_index)."""
    debate_state = {
        "round_messages": [
            {
                "debate_round": 1,
                "message_index": 1,
                "speaker_key": "Bull",
                "cleaned_prose": "营收 100 亿，ROE 20%",
            },
            {
                "debate_round": 2,
                "message_index": 3,
                "speaker_key": "Bull",
                "cleaned_prose": "重申营收 100 亿，且净利润 50 亿",
            },
        ]
    }
    res = calculate_evidence_recycling_rate(debate_state, version="v1")
    assert res["denominator"] == 2
    assert res["numerator"] == 1
    assert res["rate"] == 0.5
    assert res["status"] == "valid"


def test_evidence_recycling_rate_derived_from_message_index():
    """S1: When debate_round and round_index are absent, derive round from message_index ((message_index - 1) // 2 + 1)."""
    debate_state = {
        "round_messages": [
            {
                "message_index": 1,
                "speaker_key": "Bull",
                "cleaned_prose": "营收 100 亿",
            },
            {
                "message_index": 3,
                "speaker_key": "Bull",
                "cleaned_prose": "营收 100 亿，毛利 30 亿",
            },
        ]
    }
    res = calculate_evidence_recycling_rate(debate_state, version="v1")
    assert res["denominator"] == 2
    assert res["numerator"] == 1
    assert res["rate"] == 0.5
    assert res["status"] == "valid"


def test_evidence_recycling_rate_invalid_or_missing_round_typed_no_data():
    """S1: Invalid or missing round fields -> typed zero_denominator, no arbitrary guessing."""
    debate_state = {
        "round_messages": [
            {
                "debate_round": None,
                "round_index": None,
                "message_index": None,
                "cleaned_prose": "营收 100 亿",
            }
        ]
    }
    res = calculate_evidence_recycling_rate(debate_state, version="v1")
    assert res["denominator"] == 0
    assert res["numerator"] == 0
    assert res["rate"] is None
    assert res["status"] == "zero_denominator"


def test_evidence_recycling_rate_golden_000333_denominator_positive():
    """S1: 000333 Golden fixture subsequent round denominator must be > 0 and not zero_denominator."""
    import json
    from pathlib import Path
    golden_file = Path(__file__).resolve().parent / "golden" / "audit_20260823" / "3c09051e7e364d859dfbe5f1af7cc2c9_result_data.json"
    with open(golden_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    rd = data.get("result_data", data)
    ids = rd["investment_debate_state"]
    res = calculate_evidence_recycling_rate(ids, version="v1_legacy")
    assert res["denominator"] > 0
    assert res["status"] == "valid"
    assert res["rate"] is not None


def test_numerical_tokens_de_pollution_stock_codes():
    """S2: Stock codes like 600900.SH, 000333.SZ, 000333 must not produce 600900 or 333."""
    from tradingagents.agents.utils.debate_metrics import extract_numerical_tokens
    text = "关注 600900.SH 和 000333.SZ 以及 600276 标的。"
    tokens = extract_numerical_tokens(text)
    assert "600900" not in tokens
    assert "333" not in tokens
    assert "000333" not in tokens
    assert "600276" not in tokens
    assert tokens == []


def test_numerical_tokens_de_pollution_dates_and_years():
    """S2: Dates and years like 2026-08-23, 2026年8月21日, 2025年 must not produce 2026, 8, 23, 21, 2025."""
    from tradingagents.agents.utils.debate_metrics import extract_numerical_tokens
    text = "在 2026-08-23 与 2026年8月21日 发布的 2025年年报 中提到 2026Q2 表现。"
    tokens = extract_numerical_tokens(text)
    assert "2026" not in tokens
    assert "8" not in tokens
    assert "23" not in tokens
    assert "21" not in tokens
    assert "2025" not in tokens
    assert tokens == []


def test_numerical_tokens_de_pollution_claim_ids():
    """S2: Claim IDs like INV-1, CH-2, CHAL-3 must not produce 1, 2, 3."""
    from tradingagents.agents.utils.debate_metrics import extract_numerical_tokens
    text = "根据 INV-1 与 CH-2 的论点，参考 CHAL-3 进行核验。"
    tokens = extract_numerical_tokens(text)
    assert "1" not in tokens
    assert "2" not in tokens
    assert "3" not in tokens
    assert tokens == []


def test_numerical_tokens_price_interval_single_or_excluded():
    """S2: 45.0-50.0元 must be captured as a single interval fact or excluded, not split into 45 and 50元."""
    from tradingagents.agents.utils.debate_metrics import extract_numerical_tokens
    text = "目标价区间 45.0-50.0元，或 45.0~50.0元。"
    tokens = extract_numerical_tokens(text)
    assert "45" not in tokens
    assert "50元" not in tokens
    for t in tokens:
        assert "-" in t or "~" in t


def test_numerical_tokens_valid_financial_facts_preserved():
    """S2: Valid financial numbers like 15.5倍, 100亿元, +30.5% are preserved."""
    from tradingagents.agents.utils.debate_metrics import extract_numerical_tokens
    text = "市盈率 15.5倍，营收 100 亿元，同比增长 +30.5%，毛利率 25%，目标价 50.0元。"
    tokens = extract_numerical_tokens(text)
    assert "15.5倍" in tokens
    assert "100亿" in tokens
    assert "+30.5%" in tokens or "30.5%" in tokens
    assert "25%" in tokens
    assert "50元" in tokens


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
    assert res["legitimate_omissions"] == []
    assert res["missing_fields"] == []


def test_field_completeness_rate_hold_decision_target_omitted():
    """For HOLD/观望 decision with note 观望不设目标价, target_price is legitimately empty and counted in numerator."""
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
    assert res["numerator"] == 4
    assert res["denominator"] == 4
    assert res["rate"] == 1.0
    assert res["status"] == "complete"
    assert "target_price" in res["legitimate_omissions"]
    assert "target_price" not in res["missing_fields"]
    assert "观望不设目标价" in res["note"]


def test_field_completeness_probability_legitimate_empty():
    """S3: probability=None with extraction_note containing '概率未提供/未提取' is counted in numerator."""
    result_data = {
        "confidence": 80,
        "probability": None,
        "target_price": 100.0,
        "stop_loss_price": 85.0,
        "decision": "BUY",
        "extraction_note": "概率未提供/未提取",
    }
    res = calculate_field_completeness_rate(result_data)
    assert res["numerator"] == 4
    assert res["denominator"] == 4
    assert res["rate"] == 1.0
    assert res["status"] == "complete"
    assert "probability" in res["legitimate_omissions"]
    assert "probability" not in res["missing_fields"]


def test_field_completeness_hold_and_prob_both_legitimate_empty():
    """S3: Both probability and target_price have legitimate empty notes (e.g. golden 600276 case)."""
    result_data = {
        "confidence": 75,
        "probability": None,
        "target_price": None,
        "stop_loss_price": 40.0,
        "decision": "HOLD",
        "direction": "观望",
        "extraction_note": "观望不设目标价；概率未提供/未提取",
    }
    res = calculate_field_completeness_rate(result_data)
    assert res["numerator"] == 4
    assert res["denominator"] == 4
    assert res["rate"] == 1.0
    assert res["status"] == "complete"
    assert "probability" in res["legitimate_omissions"]
    assert "target_price" in res["legitimate_omissions"]
    assert res["missing_fields"] == []


def test_field_completeness_missing_without_note_still_missing():
    """S3: None values without whitelisted note remain missing and reduce numerator."""
    result_data = {
        "confidence": 70,
        "probability": None,
        "target_price": None,
        "stop_loss_price": 85.0,
        "decision": "BUY",
        "extraction_note": None,
    }
    res = calculate_field_completeness_rate(result_data)
    assert res["numerator"] == 2
    assert res["denominator"] == 4
    assert res["rate"] == 0.5
    assert res["status"] == "partial"
    assert "probability" in res["missing_fields"]
    assert "target_price" in res["missing_fields"]
    assert res["legitimate_omissions"] == []


def test_field_completeness_non_whitelisted_note_rejected():
    """S3: Non-whitelisted random note cannot whitelist a missing field."""
    result_data = {
        "confidence": 70,
        "probability": None,
        "target_price": 100.0,
        "stop_loss_price": 85.0,
        "decision": "BUY",
        "extraction_note": "任意非白名单说明文本",
    }
    res = calculate_field_completeness_rate(result_data)
    assert res["numerator"] == 3
    assert res["denominator"] == 4
    assert res["rate"] == 0.75
    assert res["status"] == "partial"
    assert "probability" in res["missing_fields"]
    assert res["legitimate_omissions"] == []


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
