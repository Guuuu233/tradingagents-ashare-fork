"""Pure function debate metrics calculator for TradingAgents-AShare (P1-M2).

Every metric outputs:
- numerator: int | float
- denominator: int | float
- rate: float | None (None when denominator is 0; never fabricated as 0.0%)
- version: str (e.g. "v1_legacy" or "v2_structured_disagreement")
- status: str (e.g. "valid", "zero_denominator", "legacy_no_data", "insufficient_data")
- note: str | None (typed explanation when denominator is 0 or special conditions)
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence, TypedDict, Union, Optional

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
    get_protocol_metadata,
)

# Standard seven report keys
SEVEN_REPORT_KEYS = (
    "macro_report",
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "smart_money_report",
    "volume_price_report",
)

# Robust numerical pattern capturing numbers with financial units
_NUM_UNIT_RE = re.compile(
    r"(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*(万股|亿股|股|亿元|万元|万|亿|%|％|pct|bp|点|元|港元|美元|倍|次|手|年|月|日|天)?(?!\w)",
    re.IGNORECASE,
)


class MetricResult(TypedDict):
    numerator: Union[int, float]
    denominator: Union[int, float]
    rate: Optional[float]
    version: str
    status: str
    note: Optional[str]


def _normalize_num_token(num_str: str, unit_str: Optional[str]) -> str:
    """Normalize a numerical token with unit for deterministic comparison."""
    try:
        val = float(num_str)
        # format float nicely
        formatted_num = f"{val:.4f}".rstrip("0").rstrip(".") if "." in f"{val:.4f}" else str(int(val))
    except (ValueError, TypeError):
        formatted_num = num_str.strip()
    unit = (unit_str or "").strip()
    if unit in ("％", "pct"):
        unit = "%"
    elif unit in ("亿元",):
        unit = "亿"
    elif unit in ("万元",):
        unit = "万"
    return f"{formatted_num}{unit}"


def extract_numerical_tokens(text: str) -> list[str]:
    """Extract distinct numerical tokens with units from text."""
    if not text or not isinstance(text, str):
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for m in _NUM_UNIT_RE.finditer(text):
        num_str, unit_str = m.group(1), m.group(2)
        norm = _normalize_num_token(num_str, unit_str)
        if norm and norm not in seen:
            seen.add(norm)
            tokens.append(norm)
    return tokens


def calculate_evidence_recycling_rate(
    debate_state: Mapping[str, Any],
    version: str = PROTOCOL_VERSION_V1_LEGACY,
) -> MetricResult:
    """Calculate numerical evidence recycling/clone rate across debate rounds.

    Denominator: count of numerical tokens introduced in subsequent messages/rounds (round > 1).
    Numerator: count of subsequent numerical tokens that match previously seen numbers.
    When denominator == 0: rate is None, status='zero_denominator'.
    """
    if not isinstance(debate_state, dict):
        return {
            "numerator": 0,
            "denominator": 0,
            "rate": None,
            "version": version,
            "status": "zero_denominator",
            "note": "分母为0：辩论状态为空或非字典",
        }

    round_messages = debate_state.get("round_messages") or []
    claims = debate_state.get("claims") or []

    # Organize numbers by round or message order
    seen_numbers_early: set[str] = set()
    subsequent_numbers: list[str] = []

    if round_messages:
        for msg in round_messages:
            r_idx = msg.get("round_index", 1)
            msg_text = msg.get("cleaned_prose", "") or ""
            # also extract from message claims if any
            for c in msg.get("claims") or []:
                if isinstance(c, dict):
                    msg_text += " " + str(c.get("claim", ""))
                    for ev in c.get("evidence") or []:
                        msg_text += " " + str(ev)
            nums = extract_numerical_tokens(msg_text)
            if r_idx <= 1:
                seen_numbers_early.update(nums)
            else:
                subsequent_numbers.extend(nums)
    else:
        # Fallback to claims round_index
        for c in claims:
            if not isinstance(c, dict):
                continue
            r_idx = c.get("round_index", 1)
            claim_text = str(c.get("claim", ""))
            for ev in c.get("evidence") or []:
                claim_text += " " + str(ev)
            nums = extract_numerical_tokens(claim_text)
            if r_idx <= 1:
                seen_numbers_early.update(nums)
            else:
                subsequent_numbers.extend(nums)

    denominator = len(subsequent_numbers)
    if denominator == 0:
        return {
            "numerator": 0,
            "denominator": 0,
            "rate": None,
            "version": version,
            "status": "zero_denominator",
            "note": "分母为0：无多轮后续证据或数字可计算回收率",
        }

    numerator = sum(1 for n in subsequent_numbers if n in seen_numbers_early)
    rate = round(numerator / denominator, 4)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "version": version,
        "status": "valid",
        "note": None,
    }


def _extract_cited_debate_numbers(debate_state_or_claims: Any) -> set[str]:
    """Extract all distinct numerical tokens cited across claims/evidence/messages."""
    cited: set[str] = set()
    if isinstance(debate_state_or_claims, list):
        for c in debate_state_or_claims:
            if isinstance(c, dict):
                claim_text = str(c.get("claim", ""))
                for ev in c.get("evidence") or []:
                    claim_text += " " + str(ev)
                cited.update(extract_numerical_tokens(claim_text))
            elif isinstance(c, str):
                cited.update(extract_numerical_tokens(c))
    elif isinstance(debate_state_or_claims, dict):
        claims = debate_state_or_claims.get("claims") or []
        for c in claims:
            if isinstance(c, dict):
                claim_text = str(c.get("claim", ""))
                for ev in c.get("evidence") or []:
                    claim_text += " " + str(ev)
                cited.update(extract_numerical_tokens(claim_text))
        for msg in debate_state_or_claims.get("round_messages") or []:
            if isinstance(msg, dict):
                cited.update(extract_numerical_tokens(msg.get("cleaned_prose", "")))
        mv = debate_state_or_claims.get("manager_verdict")
        if isinstance(mv, dict):
            cited.update(extract_numerical_tokens(str(mv.get("reason", ""))))
    return cited


def calculate_seven_reports_utilization(
    seven_reports: Mapping[str, str],
    debate_state_or_claims: Any,
    version: str = PROTOCOL_VERSION_V1_LEGACY,
) -> MetricResult:
    """Calculate utilization rate of data points across seven analyst reports.

    Denominator: total unique numerical tokens present across all 7 reports.
    Numerator: count of report tokens cited in debate claims/evidence.
    """
    if not isinstance(seven_reports, dict):
        return {
            "numerator": 0,
            "denominator": 0,
            "rate": None,
            "version": version,
            "status": "zero_denominator",
            "note": "分母为0：七报告参数非字典",
        }

    all_report_tokens: list[str] = []
    seen: set[str] = set()
    for key in SEVEN_REPORT_KEYS:
        content = seven_reports.get(key, "") or ""
        tokens = extract_numerical_tokens(str(content))
        for t in tokens:
            if t not in seen:
                seen.add(t)
                all_report_tokens.append(t)

    denominator = len(all_report_tokens)
    if denominator == 0:
        return {
            "numerator": 0,
            "denominator": 0,
            "rate": None,
            "version": version,
            "status": "zero_denominator",
            "note": "分母为0：七报告无有效数字数据点",
        }

    cited_tokens = _extract_cited_debate_numbers(debate_state_or_claims)
    numerator = sum(1 for t in all_report_tokens if t in cited_tokens)
    rate = round(numerator / denominator, 4)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "version": version,
        "status": "valid",
        "note": None,
    }


def calculate_macro_utilization(
    macro_report: str,
    debate_state_or_claims: Any,
    version: str = PROTOCOL_VERSION_V1_LEGACY,
) -> MetricResult:
    """Calculate data utilization rate for the macro/sector analyst report."""
    tokens = extract_numerical_tokens(macro_report or "")
    denominator = len(tokens)
    if denominator == 0:
        return {
            "numerator": 0,
            "denominator": 0,
            "rate": None,
            "version": version,
            "status": "zero_denominator",
            "note": "分母为0：宏观报告无有效数据点",
        }

    cited_tokens = _extract_cited_debate_numbers(debate_state_or_claims)
    numerator = sum(1 for t in tokens if t in cited_tokens)
    rate = round(numerator / denominator, 4)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "version": version,
        "status": "valid",
        "note": None,
    }


def calculate_fundamentals_utilization(
    fundamentals_report: str,
    debate_state_or_claims: Any,
    version: str = PROTOCOL_VERSION_V1_LEGACY,
) -> MetricResult:
    """Calculate data utilization rate for the fundamentals analyst report."""
    tokens = extract_numerical_tokens(fundamentals_report or "")
    denominator = len(tokens)
    if denominator == 0:
        return {
            "numerator": 0,
            "denominator": 0,
            "rate": None,
            "version": version,
            "status": "zero_denominator",
            "note": "分母为0：基本面报告无有效数据点",
        }

    cited_tokens = _extract_cited_debate_numbers(debate_state_or_claims)
    numerator = sum(1 for t in tokens if t in cited_tokens)
    rate = round(numerator / denominator, 4)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "version": version,
        "status": "valid",
        "note": None,
    }


def calculate_bull_bear_verified_rates_and_delta(
    claims_or_summary: Any,
    version: str = PROTOCOL_VERSION_V1_LEGACY,
) -> dict[str, MetricResult]:
    """Calculate bull/bear verified rates and their rate delta.

    Input can be claim_evidence_summary mapping or list of claim dicts.
    """
    bull_v, bull_t = 0, 0
    bear_v, bear_t = 0, 0

    if isinstance(claims_or_summary, dict):
        for cid, info in claims_or_summary.items():
            if not isinstance(info, dict):
                continue
            speaker = str(info.get("speaker_key") or "")
            counts = info.get("counts") or {}
            v_cnt = counts.get("verified", 0)
            t_cnt = counts.get("total", 0)
            if speaker.lower() in ("bull", "看多", "多方"):
                bull_v += v_cnt
                bull_t += t_cnt
            elif speaker.lower() in ("bear", "看空", "空方"):
                bear_v += v_cnt
                bear_t += t_cnt
    elif isinstance(claims_or_summary, list):
        for c in claims_or_summary:
            if not isinstance(c, dict):
                continue
            speaker = str(c.get("speaker_key") or "")
            is_verified = c.get("status") == "verified" or c.get("is_verified") is True
            if speaker.lower() in ("bull", "看多", "多方"):
                bull_t += 1
                if is_verified:
                    bull_v += 1
            elif speaker.lower() in ("bear", "看空", "空方"):
                bear_t += 1
                if is_verified:
                    bear_v += 1

    bull_rate = round(bull_v / bull_t, 4) if bull_t > 0 else None
    bear_rate = round(bear_v / bear_t, 4) if bear_t > 0 else None

    bull_res: MetricResult = {
        "numerator": bull_v,
        "denominator": bull_t,
        "rate": bull_rate,
        "version": version,
        "status": "valid" if bull_t > 0 else "zero_denominator",
        "note": None if bull_t > 0 else "分母为0：多头无待核验证据",
    }

    bear_res: MetricResult = {
        "numerator": bear_v,
        "denominator": bear_t,
        "rate": bear_rate,
        "version": version,
        "status": "valid" if bear_t > 0 else "zero_denominator",
        "note": None if bear_t > 0 else "分母为0：空头无待核验证据",
    }

    if bull_rate is not None and bear_rate is not None:
        delta_val = round(abs(bull_rate - bear_rate), 4)
        delta_res: MetricResult = {
            "numerator": abs(bull_v * bear_t - bear_v * bull_t),
            "denominator": bull_t * bear_t,
            "rate": delta_val,
            "version": version,
            "status": "valid",
            "note": None,
        }
    else:
        delta_res = {
            "numerator": 0,
            "denominator": 0,
            "rate": None,
            "version": version,
            "status": "insufficient_data",
            "note": "单方或双方无核验数据，无法计算差值",
        }

    return {
        "bull_verified_rate": bull_res,
        "bear_verified_rate": bear_res,
        "bull_bear_verified_delta": delta_res,
    }


def calculate_challenge_metrics(
    debate_state: Mapping[str, Any],
    manager_verdict: Optional[Mapping[str, Any]] = None,
    version: str = PROTOCOL_VERSION_V1_LEGACY,
) -> dict[str, Any]:
    """Calculate challenge count, adoption rate, and evidence status distribution.

    In v1_legacy, returns legacy_no_data status.
    In v2, calculates structured challenge metrics.
    """
    is_legacy = version == PROTOCOL_VERSION_V1_LEGACY or (
        isinstance(debate_state, dict)
        and debate_state.get("protocol_version") == PROTOCOL_VERSION_V1_LEGACY
        and not debate_state.get("challenges")
    )

    if is_legacy:
        return {
            "challenge_count": {
                "numerator": 0,
                "denominator": 0,
                "rate": None,
                "version": version,
                "status": "legacy_no_data",
                "note": "v1_legacy协议无challenge机制",
            },
            "challenge_adoption_rate": {
                "numerator": 0,
                "denominator": 0,
                "rate": None,
                "version": version,
                "status": "legacy_no_data",
                "note": "v1_legacy协议无challenge采纳数据",
            },
            "challenge_evidence_status": {
                "verified": 0,
                "unsupported": 0,
                "contradicted": 0,
                "status": "legacy_no_data",
                "note": "v1_legacy协议无challenge证据",
            },
        }

    # v2 structured disagreement protocol
    challenges = debate_state.get("challenges") or []
    if not isinstance(challenges, list):
        challenges = []

    total_challenges = len(challenges)
    if total_challenges == 0:
        return {
            "challenge_count": {
                "numerator": 0,
                "denominator": 0,
                "rate": None,
                "version": version,
                "status": "zero_denominator",
                "note": "分母为0：未提出challenge",
            },
            "challenge_adoption_rate": {
                "numerator": 0,
                "denominator": 0,
                "rate": None,
                "version": version,
                "status": "zero_denominator",
                "note": "分母为0：未提出challenge",
            },
            "challenge_evidence_status": {
                "verified": 0,
                "unsupported": 0,
                "contradicted": 0,
                "status": "zero_denominator",
                "note": "无challenge证据",
            },
        }

    # Count adopted challenges from manager_verdict or challenge objects
    adopted_count = 0
    adopted_ids = set()
    if isinstance(manager_verdict, dict):
        raw_adopted = manager_verdict.get("adopted_challenge_ids") or []
        if isinstance(raw_adopted, list):
            adopted_ids = set(raw_adopted)

    verified_count = 0
    unsupported_count = 0
    contradicted_count = 0

    for ch in challenges:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("challenge_id")
        if (cid and cid in adopted_ids) or ch.get("adopted") is True:
            adopted_count += 1
        st = str(ch.get("status") or "").lower()
        if st == "verified":
            verified_count += 1
        elif st == "contradicted":
            contradicted_count += 1
        elif st == "unsupported":
            unsupported_count += 1

    return {
        "challenge_count": {
            "numerator": total_challenges,
            "denominator": total_challenges,
            "rate": float(total_challenges),
            "version": version,
            "status": "valid",
            "note": None,
        },
        "challenge_adoption_rate": {
            "numerator": adopted_count,
            "denominator": total_challenges,
            "rate": round(adopted_count / total_challenges, 4),
            "version": version,
            "status": "valid",
            "note": None,
        },
        "challenge_evidence_status": {
            "verified": verified_count,
            "unsupported": unsupported_count,
            "contradicted": contradicted_count,
            "status": "valid",
            "note": None,
        },
    }


def calculate_field_completeness_rate(
    result_data: Mapping[str, Any],
    version: str = PROTOCOL_VERSION_V1_LEGACY,
) -> MetricResult:
    """Calculate field completeness rate across confidence, probability, target, stop.

    Denominator: 4 core fields.
    For HOLD / 观望 decisions, legitimate target_price omission is recognized with typed note.
    """
    if not isinstance(result_data, dict):
        return {
            "numerator": 0,
            "denominator": 4,
            "rate": 0.0,
            "version": version,
            "status": "invalid_input",
            "note": "输入非字典",
        }

    conf = result_data.get("confidence")
    prob = result_data.get("probability")
    target = result_data.get("target_price")
    stop = result_data.get("stop_loss_price")
    direction = str(result_data.get("direction") or result_data.get("decision") or "")
    extraction_note = str(result_data.get("extraction_note") or "")

    is_hold = direction.upper() in ("HOLD", "中性", "观望", "持有", "NEUTRAL") or "观望不设目标价" in extraction_note

    present_fields = []
    missing_fields = []
    notes = []

    # 1. confidence (0-100 int)
    if isinstance(conf, (int, float)) and 0 <= conf <= 100 and not isinstance(conf, bool):
        present_fields.append("confidence")
    else:
        missing_fields.append("confidence")

    # 2. probability (0.0 - 1.0 float)
    if isinstance(prob, (int, float)) and 0.0 <= prob <= 1.0 and not isinstance(prob, bool):
        present_fields.append("probability")
    else:
        missing_fields.append("probability")

    # 3. target_price (positive float or legitimate omission for HOLD)
    if isinstance(target, (int, float)) and target > 0 and not isinstance(target, bool):
        present_fields.append("target_price")
    else:
        missing_fields.append("target_price")
        if is_hold:
            notes.append("观望不设目标价")

    # 4. stop_loss_price (positive float)
    if isinstance(stop, (int, float)) and stop > 0 and not isinstance(stop, bool):
        present_fields.append("stop_loss_price")
    else:
        missing_fields.append("stop_loss_price")

    denominator = 4
    numerator = len(present_fields)
    rate = round(numerator / denominator, 4)

    status = "complete" if numerator == 4 else "partial" if numerator > 0 else "incomplete"
    note_str = "；".join(notes) if notes else (f"缺失字段: {', '.join(missing_fields)}" if missing_fields else None)

    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "version": version,
        "status": status,
        "note": note_str,
    }


def calculate_all_debate_metrics(
    result_data: Mapping[str, Any],
    version: Optional[str] = None,
) -> dict[str, Any]:
    """Calculate all P1-M metrics for a given report result_data."""
    meta = get_protocol_metadata(result_data)
    proto_version = version or meta["protocol_version"]

    inv_state = result_data.get("investment_debate_state")
    if not isinstance(inv_state, dict):
        inv_state = {}

    manager_verdict = result_data.get("manager_verdict") or inv_state.get("manager_verdict") or {}
    claim_evidence_summary = manager_verdict.get("claim_evidence_summary") or inv_state.get("claim_evidence_summary") or {}

    seven_reports = {k: str(result_data.get(k, "") or "") for k in SEVEN_REPORT_KEYS}

    evidence_recycling = calculate_evidence_recycling_rate(inv_state, version=proto_version)
    seven_reports_util = calculate_seven_reports_utilization(seven_reports, inv_state, version=proto_version)
    macro_util = calculate_macro_utilization(seven_reports.get("macro_report", ""), inv_state, version=proto_version)
    fund_util = calculate_fundamentals_utilization(seven_reports.get("fundamentals_report", ""), inv_state, version=proto_version)
    bull_bear_verif = calculate_bull_bear_verified_rates_and_delta(claim_evidence_summary or inv_state.get("claims", []), version=proto_version)
    challenges = calculate_challenge_metrics(inv_state, manager_verdict=manager_verdict, version=proto_version)
    completeness = calculate_field_completeness_rate(result_data, version=proto_version)

    return {
        "protocol_version": proto_version,
        "protocol_stage": meta["protocol_stage"],
        "feature_flags": meta["feature_flags"],
        "evidence_recycling": evidence_recycling,
        "seven_reports_utilization": seven_reports_util,
        "macro_utilization": macro_util,
        "fundamentals_utilization": fund_util,
        "bull_bear_verified": bull_bear_verif,
        "challenge_metrics": challenges,
        "field_completeness": completeness,
    }
