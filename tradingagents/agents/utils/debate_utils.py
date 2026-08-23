from __future__ import annotations

import collections
import difflib
import json
import logging
import math
import re
from numbers import Real
from typing import Any, Iterable, Mapping, Sequence


logger = logging.getLogger(__name__)


class DebateProtocolError(RuntimeError):
    """Raised when debate protocol validation fails after retry."""

    def __init__(
        self,
        message: str,
        *,
        message_index: int | None = None,
        speaker: str | None = None,
        details: str | None = None,
        attempts: list[dict[str, Any]] | None = None,
    ):
        super().__init__(message)
        self.message_index = message_index
        self.speaker = speaker
        self.details = details
        self.attempts = attempts or []


_MACHINE_LIST_FIELDS = (
    "responded_claim_ids",
    "new_claims",
    "resolved_claim_ids",
    "unresolved_claim_ids",
    "next_focus_claim_ids",
)
_MACHINE_TEXT_FIELDS = ("round_summary", "round_goal")
_MACHINE_FIELDS = frozenset((*_MACHINE_LIST_FIELDS, *_MACHINE_TEXT_FIELDS))
_MACHINE_CLAIM_FIELDS = frozenset(("claim", "evidence", "confidence", "target_claim_ids"))


def normalize_text(text: str) -> str:
    """Normalize text by stripping whitespace and punctuation and converting to lowercase."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"[^\w一-鿿]+", " ", text.lower()).strip()


def tokenize_text(text: str) -> list[str]:
    """Tokenize text into Chinese characters and alphanumeric words."""
    if not isinstance(text, str):
        return []
    s = text.lower()
    return re.findall(r"[一-鿿]|[a-z0-9_]+", s)


def _token_ngrams(tokens: list[str], n: int = 2) -> list[tuple[str, ...]]:
    if len(tokens) < n:
        return [tuple(tokens)] if tokens else []
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def compute_claim_similarity(t1: str, t2: str) -> float:
    """Compute normalized n-gram and sequence similarity between two texts.

    Returns a float in [0.0, 1.0].
    Threshold >= 0.82 indicates duplicate / high-similarity paraphrase.
    """
    toks1 = tokenize_text(t1)
    toks2 = tokenize_text(t2)
    if not toks1 and not toks2:
        return 1.0
    if not toks1 or not toks2:
        return 0.0
    if toks1 == toks2:
        return 1.0

    s1 = "".join(toks1)
    s2 = "".join(toks2)
    seq_ratio = difflib.SequenceMatcher(None, s1, s2).ratio()

    # 1-gram & 2-gram Dice on tokens
    c1_1, c2_1 = collections.Counter(_token_ngrams(toks1, 1)), collections.Counter(_token_ngrams(toks2, 1))
    total_1 = sum(c1_1.values()) + sum(c2_1.values())
    dice_1 = (2.0 * sum((c1_1 & c2_1).values())) / total_1 if total_1 > 0 else 0.0

    c1_2, c2_2 = collections.Counter(_token_ngrams(toks1, 2)), collections.Counter(_token_ngrams(toks2, 2))
    total_2 = sum(c1_2.values()) + sum(c2_2.values())
    dice_2 = (2.0 * sum((c1_2 & c2_2).values())) / total_2 if total_2 > 0 else 0.0

    token_dice = 0.4 * dice_1 + 0.6 * dice_2
    return round(float(max(seq_ratio, dice_2, token_dice)), 4)


def extract_new_evidence_count(
    new_evidence_list: Iterable[Any] | None,
    prev_evidence_list: Iterable[Any] | None,
    threshold: float = 0.82,
) -> int:
    """Count how many evidence items in new_evidence_list are not duplicates of prev_evidence_list."""
    new_items = [str(item).strip() for item in (new_evidence_list or []) if str(item).strip()]
    prev_items = [str(item).strip() for item in (prev_evidence_list or []) if str(item).strip()]
    if not new_items:
        return 0
    if not prev_items:
        return len(new_items)

    new_count = 0
    for item in new_items:
        max_sim = max([compute_claim_similarity(item, prev) for prev in prev_items], default=0.0)
        if max_sim < threshold:
            new_count += 1
    return new_count


def _evaluate_claim_duplication(
    claim_payload: Mapping[str, Any],
    same_side_claims: Sequence[Mapping[str, Any]],
    prev_ev_list: Sequence[Any],
) -> tuple[bool, float, int, str | None]:
    """Evaluate whether a candidate new claim is a duplicate of same-side historical claims.

    Returns:
        (is_duplicate, max_similarity, new_evidence_count, matched_claim_id)
    """
    claim_text = str(claim_payload.get("claim", "")).strip()
    claim_ev = claim_payload.get("evidence") or []

    max_sim = 0.0
    matched_id = None
    for pc in same_side_claims:
        s = compute_claim_similarity(claim_text, str(pc.get("claim", "")))
        if s > max_sim:
            max_sim = s
            matched_id = str(pc.get("claim_id", "")).strip() or None

    new_ev_count = extract_new_evidence_count(claim_ev, prev_ev_list)

    is_duplicate = False
    if max_sim >= 0.82:
        is_duplicate = True
    elif max_sim >= 0.70 and new_ev_count == 0 and prev_ev_list:
        is_duplicate = True
    elif prev_ev_list and new_ev_count == 0 and all(
        max([compute_claim_similarity(str(e), str(pe)) for pe in prev_ev_list], default=0.0) >= 0.82
        for e in claim_ev
    ):
        is_duplicate = True

    return is_duplicate, max_sim, new_ev_count, matched_id


def _tagged_openings(text: str, tag: str) -> list[re.Match[str]]:
    if not isinstance(text, str):
        return []
    pattern = rf"<!--\s*{re.escape(tag)}\s*:"
    return list(re.finditer(pattern, text, flags=re.DOTALL))


def _tagged_occurrences(text: str, tag: str) -> list[re.Match[str]]:
    """Count same-tag machine block labels before validating their delimiter."""
    if not isinstance(text, str):
        return []
    pattern = rf"<!--\s*{re.escape(tag)}\b"
    return list(re.finditer(pattern, text, flags=re.DOTALL))


def _find_machine_block_close(text: str, start: int) -> int:
    """Return the first ``-->`` that is not inside a JSON string literal."""
    in_string = False
    escaped = False
    index = start
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif text.startswith("-->", index):
                return index
        index += 1
    return -1


def _machine_block_spans(text: str, tag: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    if not isinstance(text, str):
        return spans
    for opening in _tagged_openings(text, tag):
        close = _find_machine_block_close(text, opening.end())
        if close >= 0:
            spans.append((opening.start(), close + 3))
    return spans


def _looks_like_machine_block(text: str) -> bool:
    return bool(
        re.match(
            r"<!--\s*[A-Za-z_][A-Za-z0-9_-]*\s*:",
            text,
            flags=re.DOTALL,
        )
    )


def _get_marker_parse_status(text: str, tag: str, *, warn: bool) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(text, str):
        return None, "missing"
    occurrences = _tagged_occurrences(text, tag)
    openings = _tagged_openings(text, tag)
    if not openings:
        if warn:
            tag_pattern = rf"<!--\s*{re.escape(tag)}\b"
            category = "truncated" if re.search(tag_pattern, text or "", flags=re.DOTALL) else "missing"
            logger.warning("[debate_utils] %s parse warning (%s): machine block not accepted", tag, category)
        tag_pattern = rf"<!--\s*{re.escape(tag)}\b"
        has_marker = bool(re.search(tag_pattern, text or "", flags=re.DOTALL))
        return None, "invalid" if has_marker else "missing"
    if len(occurrences) > 1:
        if warn:
            category = "duplicate_malformed" if len(occurrences) != len(openings) else "duplicate"
            logger.warning(
                "[debate_utils] %s parse warning (%s): %d same-tag machine block labels found; rejecting all",
                tag,
                category,
                len(occurrences),
            )
        return None, "invalid"

    for opening in openings:
        payload_start = opening.end()
        closing_index = _find_machine_block_close(text, payload_start)
        if closing_index < 0:
            continue
        payload_text = text[payload_start:closing_index].strip()
        try:
            payload = json.loads(payload_text)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        remainder = text[closing_index + 3:].lstrip()
        if remainder and not _looks_like_machine_block(remainder):
            continue
        return payload, "valid"

    if warn:
        if not any(_find_machine_block_close(text, opening.end()) >= 0 for opening in openings):
            logger.warning(
                "[debate_utils] %s parse warning (truncated): closing marker is missing",
                tag,
            )
        else:
            logger.warning(
                "[debate_utils] %s parse warning (invalid_or_trailing_prose): machine block not accepted",
                tag,
            )
    return None, "invalid"


def _parse_tagged_json(text: str, tag: str, *, warn: bool) -> dict[str, Any] | None:
    payload, _ = _get_marker_parse_status(text, tag, warn=warn)
    return payload


def extract_tagged_json(text: str, tag: str) -> dict[str, Any]:
    if tag in {"DEBATE_STATE", "RISK_STATE", "RISK_JUDGE"}:
        return _parse_tagged_json(text, tag, warn=True) or {}
    pattern = rf"<!--\s*{re.escape(tag)}:\s*(\{{.*?\}})\s*-->"
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def strip_tagged_json(text: str, tag: str) -> str:
    if not isinstance(text, str):
        return text
    if _parse_tagged_json(text, tag, warn=False) is None:
        return text.strip()
    for start, end in reversed(_machine_block_spans(text, tag)):
        text = text[:start] + text[end:]
    return text.strip()


def _warn_machine_validation(tag: str, category: str, detail: str) -> None:
    logger.warning("[debate_utils] %s validation warning (%s): %s", tag, category, detail)


def _normalize_machine_string_list(value: Any, tag: str, field_name: str, claim_index: int | None = None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        location = f"claim {claim_index} field {field_name}" if claim_index is not None else field_name
        _warn_machine_validation(tag, "invalid_schema", f"{location} must be an array")
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _claim_confidence(value: Any, tag: str, claim_index: int) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        _warn_machine_validation(
            tag,
            "claim_confidence",
            f"claim {claim_index} confidence must be a finite number in [0, 1]",
        )
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        _warn_machine_validation(
            tag,
            "claim_confidence",
            f"claim {claim_index} confidence must be a finite number in [0, 1]",
        )
        return None
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        _warn_machine_validation(
            tag,
            "claim_confidence",
            f"claim {claim_index} confidence must be a finite number in [0, 1]",
        )
        return None
    return confidence


def _sanitize_machine_payload(payload: Mapping[str, Any], tag: str) -> dict[str, Any] | None:
    unknown_fields = sorted(str(key) for key in payload if key not in _MACHINE_FIELDS)
    if unknown_fields:
        _warn_machine_validation(
            tag,
            "unknown_fields",
            f"unknown structured fields ignored: {', '.join(unknown_fields)}",
        )

    normalized: dict[str, Any] = {}
    for field_name in _MACHINE_LIST_FIELDS:
        value = payload.get(field_name)
        if value is None:
            normalized[field_name] = []
        elif not isinstance(value, list):
            _warn_machine_validation(
                tag,
                "invalid_schema",
                f"{field_name} must be an array",
            )
            return None
        else:
            normalized[field_name] = value

    for field_name in _MACHINE_TEXT_FIELDS:
        value = payload.get(field_name)
        if value is None:
            normalized[field_name] = ""
        elif not isinstance(value, str):
            _warn_machine_validation(
                tag,
                "invalid_schema",
                f"{field_name} must be a string",
            )
            return None
        else:
            normalized[field_name] = value

    claims: list[dict[str, Any]] = []
    for claim_index, raw_claim in enumerate(normalized["new_claims"], start=1):
        if not isinstance(raw_claim, Mapping):
            _warn_machine_validation(
                tag,
                "invalid_claim",
                f"claim {claim_index} must be an object and was dropped",
            )
            continue
        unknown_claim_fields = sorted(str(key) for key in raw_claim if key not in _MACHINE_CLAIM_FIELDS)
        if unknown_claim_fields:
            _warn_machine_validation(
                tag,
                "unknown_fields",
                f"claim {claim_index} unknown structured fields ignored: {', '.join(unknown_claim_fields)}",
            )

        claim_text = raw_claim.get("claim")
        if not isinstance(claim_text, str) or not claim_text.strip():
            _warn_machine_validation(
                tag,
                "invalid_claim",
                f"claim {claim_index} needs non-empty claim text and was dropped",
            )
            continue
        confidence = _claim_confidence(raw_claim.get("confidence"), tag, claim_index)
        if confidence is None:
            continue
        claims.append(
            {
                "claim": claim_text.strip(),
                "evidence": _normalize_machine_string_list(
                    raw_claim.get("evidence"), tag, "evidence", claim_index
                ),
                "confidence": confidence,
                "target_claim_ids": _normalize_machine_string_list(
                    raw_claim.get("target_claim_ids"), tag, "target_claim_ids", claim_index
                ),
            }
        )
    normalized["new_claims"] = claims
    return normalized


def safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return default


def extract_risk_judge_result(text: str) -> dict[str, Any]:
    judge_payload = extract_tagged_json(text, "RISK_JUDGE")
    cleaned_response = strip_tagged_json(text, "RISK_JUDGE")
    parse_failed = not bool(judge_payload)

    verdict = str(judge_payload.get("verdict", "")).strip().lower()
    if verdict not in {"pass", "revise", "reject"}:
        parse_failed = True
        verdict = "reject"

    hard_constraints = [str(item).strip() for item in (judge_payload.get("hard_constraints") or []) if str(item).strip()]
    soft_constraints = [str(item).strip() for item in (judge_payload.get("soft_constraints") or []) if str(item).strip()]
    execution_preconditions = [
        str(item).strip() for item in (judge_payload.get("execution_preconditions") or []) if str(item).strip()
    ]
    de_risk_triggers = [str(item).strip() for item in (judge_payload.get("de_risk_triggers") or []) if str(item).strip()]
    revision_reason = str(judge_payload.get("revision_reason", "")).strip()

    if parse_failed:
        revision_reason = revision_reason or "风控裁决机读块解析失败，按拒绝处理"
        if cleaned_response:
            cleaned_response = f"{cleaned_response}\n\n[系统说明] 风控裁决机读块解析失败，已按拒绝处理。"
        else:
            cleaned_response = "风控裁决机读块解析失败，已按拒绝处理。"

    return {
        "judge_payload": judge_payload,
        "cleaned_response": cleaned_response,
        "verdict": verdict,
        "hard_constraints": hard_constraints,
        "soft_constraints": soft_constraints,
        "execution_preconditions": execution_preconditions,
        "de_risk_triggers": de_risk_triggers,
        "revision_reason": revision_reason,
        "parse_failed": parse_failed,
    }


def format_claims_for_prompt(
    claims: Iterable[Mapping[str, Any]] | None,
    focus_claim_ids: Iterable[str] | None = None,
    empty_message: str = "当前没有已登记 claim，本轮请先提出 1 到 2 条最关键 claim。",
) -> str:
    claim_list = list(claims or [])
    if not claim_list:
        return empty_message

    focus_set = {str(item) for item in (focus_claim_ids or []) if str(item).strip()}
    lines: list[str] = []
    for claim in claim_list:
        claim_id = str(claim.get("claim_id", "")).strip()
        status = str(claim.get("status", "open")).strip() or "open"
        speaker = str(claim.get("speaker", "")).strip() or "Unknown"
        summary = str(claim.get("claim", "")).strip() or "未提供 claim 文本"
        evidence = claim.get("evidence") or []
        evidence_text = "；".join(str(item).strip() for item in evidence if str(item).strip()) or "无明确证据"
        prefix = "* " if claim_id in focus_set else "- "
        lines.append(
            f"{prefix}{claim_id} [{status}] {speaker}: {summary} | 证据: {evidence_text}"
        )
    return "\n".join(lines)


def format_claim_subset_for_prompt(
    claims: Iterable[Mapping[str, Any]] | None,
    claim_ids: Iterable[str] | None,
    empty_message: str = "当前没有未解决 claim。",
) -> str:
    claim_id_set = {str(item) for item in (claim_ids or []) if str(item).strip()}
    if not claim_id_set:
        return empty_message
    subset = [claim for claim in (claims or []) if str(claim.get("claim_id", "")) in claim_id_set]
    return format_claims_for_prompt(subset, focus_claim_ids=claim_id_set, empty_message=empty_message)



def summarize_risk_feedback(feedback: Mapping[str, Any] | None) -> str:
    payload = feedback or {}
    verdict = str(payload.get("latest_risk_verdict", "")).strip()
    if not verdict:
        return "当前没有待处理的风控回退要求。"

    hard_constraints = payload.get("hard_constraints") or []
    soft_constraints = payload.get("soft_constraints") or []
    preconditions = payload.get("execution_preconditions") or []
    de_risk_triggers = payload.get("de_risk_triggers") or []

    return "\n".join(
        [
            f"风控裁决: {verdict}",
            f"是否要求重做: {'是' if payload.get('revision_required') else '否'}",
            f"打回原因: {payload.get('revision_reason', '未提供')}",
            f"硬约束: {'; '.join(str(item) for item in hard_constraints) if hard_constraints else '无'}",
            f"软约束: {'; '.join(str(item) for item in soft_constraints) if soft_constraints else '无'}",
            f"执行前提: {'; '.join(str(item) for item in preconditions) if preconditions else '无'}",
            f"降风险触发器: {'; '.join(str(item) for item in de_risk_triggers) if de_risk_triggers else '无'}",
        ]
    )


def default_round_goal(domain: str, next_count: int) -> str:
    goals = {
        "investment": [
            "建立最核心的正反两方 claim，并明确为何是现在。",
            "优先攻击对手最脆弱的假设，不要扩散议题。",
            "围绕时间窗口与触发条件，判断交易时机是否成立。",
            "围绕失败路径与失效条件，判断谁低估了回撤风险。",
            "检查剩余分歧是否仍有信息增量，否则准备收口。",
        ],
        "risk": [
            "建立最关键的执行风险 claim，明确风险预算冲突点。",
            "围绕仓位、止损、流动性约束，攻击对手最薄弱一环。",
            "判断哪些风险是可接受波动，哪些风险是硬性红线。",
            "逼迫双方给出可执行替代方案，而不是抽象立场。",
            "检查是否还存在未解决的高影响执行风险，否则准备收口。",
        ],
    }
    domain_key = domain if domain in goals else "investment"
    goal_list = goals[domain_key]
    index = min(max(next_count - 1, 0), len(goal_list) - 1)
    return goal_list[index]


def validate_debate_preconditions(
    investment_debate_state: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Validate debate preconditions before Research Manager invocation.

    Rules (Contract 5):
    - count == 6 and accepted valid messages == 6.
    - Bull and Bear each have exactly 3 valid messages.
    - Subsequent messages (index 2..6) have non-empty responded_claim_ids targeting opponent claim and target_claim_ids targeting opponent claim.
    - Both Bull and Bear have claims in the claim ledger.

    Returns a list of error strings (empty if valid).
    """
    gate_errors: list[str] = []
    count = safe_int(investment_debate_state.get("count", 0), 0)
    round_messages = investment_debate_state.get("round_messages", []) or []
    accepted_valid_messages = [
        m for m in round_messages
        if m.get("accepted", True) and m.get("parse_status") == "valid"
    ]

    if count != 6 or len(accepted_valid_messages) != 6:
        gate_errors.append(
            f"有效辩论轮次不足6次 (当前count={count}, accepted valid={len(accepted_valid_messages)})"
        )

    bull_msgs = [
        m for m in accepted_valid_messages
        if "Bull" in str(m.get("speaker", "")) or str(m.get("speaker_key", "")) == "Bull"
    ]
    bear_msgs = [
        m for m in accepted_valid_messages
        if "Bear" in str(m.get("speaker", "")) or str(m.get("speaker_key", "")) == "Bear"
    ]
    if len(bull_msgs) != 3 or len(bear_msgs) != 3:
        gate_errors.append(
            f"多空双方有效发言次数不均等 (多头={len(bull_msgs)}, 空头={len(bear_msgs)}, 预期各3次)"
        )

    claim_list = list(claims if claims is not None else investment_debate_state.get("claims", []) or [])
    claim_map = {str(c.get("claim_id", "")): c for c in claim_list if str(c.get("claim_id", ""))}
    for idx, msg in enumerate(accepted_valid_messages):
        m_idx = msg.get("message_index", idx + 1)
        if m_idx >= 2:
            responded = msg.get("responded_claim_ids") or []
            targets = msg.get("target_claim_ids") or []
            is_bull_msg = "Bull" in str(msg.get("speaker", "")) or str(msg.get("speaker_key", "")) == "Bull"
            opponent_key = "Bear" if is_bull_msg else "Bull"
            opponent_stance = "bearish" if is_bull_msg else "bullish"

            has_opp_responded = any(
                cid in claim_map
                and (claim_map[cid].get("speaker_key") == opponent_key or claim_map[cid].get("stance") == opponent_stance)
                for cid in responded
            )
            if not has_opp_responded:
                gate_errors.append(f"第 {m_idx} 次发言未在 responded_claim_ids 中回应对手 claim (responded: {responded})")

            has_opp_target = any(
                cid in claim_map
                and (claim_map[cid].get("speaker_key") == opponent_key or claim_map[cid].get("stance") == opponent_stance)
                for cid in targets
            )
            if not has_opp_target:
                gate_errors.append(f"第 {m_idx} 次发言未在 target_claim_ids 中针对对手 claim (targets: {targets})")

    has_bull_claims = any(c.get("speaker_key") == "Bull" or c.get("stance") == "bullish" for c in claim_list)
    has_bear_claims = any(c.get("speaker_key") == "Bear" or c.get("stance") == "bearish" for c in claim_list)
    if not has_bull_claims or not has_bear_claims:
        gate_errors.append(f"辩论 claim 账本缺失单方或双方论据 (多头claims={has_bull_claims}, 空头claims={has_bear_claims})")

    # Check E: Information gain / anti-repetition hard gate across same-side rounds
    for side_name, side_key, side_stance in (("多头", "Bull", "bullish"), ("空头", "Bear", "bearish")):
        side_claims = [
            c for c in claim_list
            if (c.get("speaker_key") == side_key or c.get("stance") == side_stance)
        ]
        for i in range(len(side_claims)):
            for j in range(i + 1, len(side_claims)):
                c1 = side_claims[i]
                c2 = side_claims[j]
                r1 = c1.get("round_index")
                r2 = c2.get("round_index")
                if r1 and r2 and r1 == r2:
                    continue
                if c1.get("claim_id") == c2.get("claim_id"):
                    continue
                sim = compute_claim_similarity(str(c1.get("claim", "")), str(c2.get("claim", "")))
                if sim >= 0.82:
                    gate_errors.append(
                        f"{side_name}发言存在跨轮重复观点 (最高相似度 {sim:.2f} >= 0.82: '{c1.get('claim', '')}' 与 '{c2.get('claim', '')}')，违反信息增量硬闸"
                    )

    for msg in accepted_valid_messages:
        m_idx = msg.get("message_index", 0)
        gain_score = msg.get("information_gain_score")
        if gain_score is not None and isinstance(gain_score, (int, float)) and gain_score <= 0.0 and m_idx >= 3:
            gate_errors.append(f"第 {m_idx} 次发言信息增量分数为 0，存在重复内容")

    return gate_errors


def _quarantine_rejected_machine_blocks(
    text: str,
    tags: str | Iterable[str] = ("DEBATE_STATE", "RISK_STATE"),
) -> str:
    """Isolate rejected machine blocks from transcript history while keeping prose."""
    if not isinstance(text, str):
        return text

    tag_list = (tags,) if isinstance(tags, str) else tuple(tags)

    spans: list[tuple[int, int]] = []
    for tag in tag_list:
        for match in _tagged_occurrences(text, tag):
            start = match.start()
            close_idx = _find_machine_block_close(text, match.end())
            if close_idx >= 0:
                end = close_idx + 3
            else:
                raw_close = text.find("-->", match.end())
                if raw_close >= 0:
                    end = raw_close + 3
                else:
                    end = len(text)
            spans.append((start, end))

    if not spans:
        return text.strip()

    spans.sort(key=lambda item: item[0])
    merged_spans: list[list[int]] = []
    for s, e in spans:
        if not merged_spans or s > merged_spans[-1][1]:
            merged_spans.append([s, e])
        else:
            merged_spans[-1][1] = max(merged_spans[-1][1], e)

    cleaned = text
    for s, e in reversed(merged_spans):
        cleaned = cleaned[:s] + cleaned[e:]

    return cleaned.strip()


def sanitize_debate_response(
    text: str,
    tags: str | Iterable[str] = ("DEBATE_STATE", "RISK_STATE"),
) -> str:
    """Isolate rejected or malformed machine blocks from debate responses while preserving prose."""
    return _quarantine_rejected_machine_blocks(text, tags)


def build_debate_report_manifest(
    reports_or_state: Mapping[str, Any],
    pass_info: Mapping[str, tuple[str, int]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build report input manifest recording length, mode, passed chars, and passed=True for 7 input reports."""
    report_keys = (
        ("macro_report", "macro_report"),
        ("market_report", "market_report"),
        ("sentiment_report", "sentiment_report"),
        ("news_report", "news_report"),
        ("fundamentals_report", "fundamentals_report"),
        ("smart_money_report", "smart_money_report"),
        ("volume_price_report", "volume_price_report"),
    )
    manifest: dict[str, dict[str, Any]] = {}
    pass_info = pass_info or {}
    for manifest_key, state_key in report_keys:
        content = reports_or_state.get(state_key, "")
        text = str(content or "")
        raw_length = len(text)
        info = pass_info.get(manifest_key)
        if info:
            mode, passed_chars = info
        else:
            mode = "full" if raw_length > 0 else "empty"
            passed_chars = raw_length
        manifest[manifest_key] = {
            "length": raw_length,
            "raw_length": raw_length,
            "passed": True,
            "mode": mode,
            "pass_mode": mode,
            "passed_chars": passed_chars,
            "char_count": passed_chars,
        }
    return manifest


def validate_debate_response(
    *,
    state: Mapping[str, Any],
    raw_response: str,
    speaker_key: str,
    stance: str,
    marker: str = "DEBATE_STATE",
    domain: str = "investment",
) -> tuple[bool, str, str, dict[str, Any] | None]:
    """Validate debate machine block format and protocol rules.

    Returns:
        (is_valid, parse_status, error_detail, sanitized_payload)
    """
    parsed_payload, raw_parse_status = _get_marker_parse_status(raw_response, marker, warn=True)
    if parsed_payload is None:
        if raw_parse_status == "missing":
            detail = f"未找到 <!-- {marker}: ... --> 机器块"
        elif raw_parse_status == "invalid":
            detail = f"{marker} 机器块 JSON 解析失败或格式不合法"
        else:
            detail = f"{marker} 机器块状态为 {raw_parse_status}"
        return False, raw_parse_status, detail, None

    payload = _sanitize_machine_payload(parsed_payload, marker)
    if payload is None:
        return False, "invalid", f"{marker} 机器块字段类型不符合规范", None

    current_count = safe_int(state.get("count", 0), 0)
    message_index = current_count + 1

    claims = [dict(item) for item in (state.get("claims", []) or []) if isinstance(item, Mapping)]
    claim_map = {
        str(item.get("claim_id", "")).strip(): item
        for item in claims
        if str(item.get("claim_id", "")).strip()
    }

    if domain == "investment":
        # Check A: Camp permission for resolved_claim_ids (cannot resolve opponent's claims)
        raw_resolved = _string_list(payload.get("resolved_claim_ids"))
        unauthorized = []
        for cid in raw_resolved:
            if cid in claim_map:
                c = claim_map[cid]
                is_opponent = (c.get("speaker_key") and c.get("speaker_key") != speaker_key) or (
                    c.get("stance") and c.get("stance") != stance
                )
                if is_opponent:
                    unauthorized.append(cid)
        if unauthorized:
            detail = f"发言人 {speaker_key} 试图单方面将对手 claim {unauthorized} 标记为 resolved，违反阵营权限契约"
            logger.warning("[debate_utils] protocol violation: %s", detail)
            return False, "invalid_protocol", detail, payload

        # Check B: message_index >= 2 must respond to at least one un-resolved opponent claim
        if message_index >= 2:
            raw_responded = _string_list(payload.get("responded_claim_ids"))
            valid_opponent_responded = [
                cid
                for cid in raw_responded
                if cid in claim_map
                and (
                    (claim_map[cid].get("speaker_key") and claim_map[cid].get("speaker_key") != speaker_key)
                    or (claim_map[cid].get("stance") and claim_map[cid].get("stance") != stance)
                )
                and claim_map[cid].get("status") != "resolved"
            ]
            if not valid_opponent_responded:
                opponent_open_ids = [
                    c["claim_id"]
                    for c in claims
                    if (
                        (c.get("speaker_key") and c.get("speaker_key") != speaker_key)
                        or (c.get("stance") and c.get("stance") != stance)
                    )
                    and c.get("status") != "resolved"
                ]
                detail = (
                    f"第 {message_index} 次发言 ({speaker_key}) 必须在 responded_claim_ids 中回应至少一条未解决的对手 claim ID "
                    f"(当前 responded: {raw_responded}, 合法对手未解决 claim: {opponent_open_ids})"
                )
                logger.warning("[debate_utils] protocol violation: %s", detail)
                return False, "invalid_protocol", detail, payload

        # Check C: message_index >= 2 must have at least one new claim targeting an opponent claim
        if message_index >= 2:
            new_claims_list = payload.get("new_claims") or []
            has_opponent_target = False
            for nc in new_claims_list:
                t_ids = _string_list(nc.get("target_claim_ids"))
                for tid in t_ids:
                    if tid in claim_map:
                        c = claim_map[tid]
                        is_opponent = (
                            c.get("speaker_key") and c.get("speaker_key") != speaker_key
                        ) or (c.get("stance") and c.get("stance") != stance)
                        if is_opponent:
                            has_opponent_target = True
                            break
                if has_opponent_target:
                    break
            if not has_opponent_target:
                opponent_all_ids = [
                    c["claim_id"]
                    for c in claims
                    if (c.get("speaker_key") and c.get("speaker_key") != speaker_key)
                    or (c.get("stance") and c.get("stance") != stance)
                ]
                detail = (
                    f"第 {message_index} 次发言 ({speaker_key}) 必须在 new_claims[].target_claim_ids 中指定至少一条对手 claim ID 作为反驳目标 "
                    f"(合法对手 claim: {opponent_all_ids})"
                )
                logger.warning("[debate_utils] protocol violation: %s", detail)
                return False, "invalid_protocol", detail, payload

        # Check D: message_index >= 3 (or same-side claims exist) must provide genuine information gain (anti-repetition hard gate)
        same_side_claims = [
            c
            for c in claims
            if (c.get("speaker_key") and c.get("speaker_key") == speaker_key)
            or (c.get("stance") and c.get("stance") == stance)
        ]
        if same_side_claims:
            new_claims_list = payload.get("new_claims") or []
            if not new_claims_list:
                detail = (
                    f"第 {message_index} 次发言 ({speaker_key}) 必须在 new_claims 中提供至少一条具有信息增量的新观点"
                )
                logger.warning("[debate_utils] protocol violation: %s", detail)
                return False, "invalid_protocol", detail, payload

            prev_ev_list = [
                ev
                for pc in same_side_claims
                for ev in (pc.get("evidence") or [])
                if str(ev).strip()
            ]

            duplicate_claims = []
            valid_new_claims = []
            max_sims = []

            for nc in new_claims_list:
                is_duplicate, sim, new_ev_count, _ = _evaluate_claim_duplication(
                    nc, same_side_claims, prev_ev_list
                )
                max_sims.append(sim)

                if is_duplicate:
                    duplicate_claims.append(str(nc.get("claim", "")).strip())
                else:
                    valid_new_claims.append(nc)

            if not valid_new_claims:
                max_sim = max(max_sims) if max_sims else 0.0
                detail = (
                    f"第 {message_index} 次发言 ({speaker_key}) 未提供有效信息增量：所有 new_claims 均与同侧历史观点重复或复用相同证据 "
                    f"(最高相似度 {max_sim:.2f} >= 0.82 或缺乏新证据，重复观点: {duplicate_claims})"
                )
                logger.warning("[debate_utils] protocol violation: %s", detail)
                return False, "invalid_protocol", detail, payload

    return True, "valid", "", payload


def _record_unstructured_response(
    *,
    state: Mapping[str, Any],
    raw_response: str,
    speaker_label: str,
    speaker_key: str,
    history_key: str,
    speaker_field: str,
    store_current_response: bool,
    current_response_key: str | None = None,
    parse_status: str = "missing",
    error_detail: str = "",
    model_name: str | None = None,
    domain: str = "investment",
) -> dict[str, Any]:
    """Record an unaccepted attempt trace without advancing valid count or mutating valid transcript history."""
    cleaned_response = _quarantine_rejected_machine_blocks(raw_response)
    new_state = dict(state)
    current_count = safe_int(state.get("count", 0), 0)
    message_index = current_count + 1
    debate_round = (message_index - 1) // 2 + 1 if domain == "investment" else (message_index - 1) // 3 + 1

    if domain == "investment":
        attempt_record: dict[str, Any] = {
            "message_index": message_index,
            "debate_round": debate_round,
            "speaker": speaker_label,
            "speaker_key": speaker_key,
            "cleaned_prose": cleaned_response,
            "parse_status": parse_status,
            "accepted": False,
            "error_detail": error_detail,
            "responded_claim_ids": [],
            "new_claim_ids": [],
            "target_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "resolved": [],
            "unresolved": [],
            "round_summary": _fallback_summary(cleaned_response),
            "round_goal": state.get("round_goal") or default_round_goal(domain, message_index),
            "information_gain_score": 0.0,
            "duplicate_claim_ids": [],
            "duplicate_claims": [],
            "new_evidence_count": 0,
            "max_similarity": 1.0 if ("重复" in error_detail or "信息增量" in error_detail) else 0.0,
        }
        if model_name is not None:
            attempt_record["model_name"] = model_name

        round_messages = [dict(m) for m in (state.get("round_messages", []) or [])]
        round_messages.append(attempt_record)

        attempts = [dict(a) for a in (state.get("attempts", []) or [])]
        attempts.append(attempt_record)

        updates = {
            "current_speaker": speaker_key,
            speaker_field: speaker_key,
            "count": current_count,
            "round_messages": round_messages,
            "attempts": attempts,
            "blocked": True,
            "parse_status": parse_status,
            "block_reason": error_detail,
        }
        new_state.update(updates)
        return new_state
    else:
        argument = f"{speaker_label}: {cleaned_response}"
        round_messages = [dict(m) for m in (state.get("round_messages", []) or [])]
        round_msg: dict[str, Any] = {
            "message_index": message_index,
            "debate_round": debate_round,
            "speaker": speaker_label,
            "cleaned_prose": cleaned_response,
            "parse_status": parse_status,
            "accepted": True,
            "responded_claim_ids": [],
            "new_claim_ids": [],
            "target_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "resolved": [],
            "unresolved": [],
            "round_summary": _fallback_summary(cleaned_response),
            "round_goal": state.get("round_goal") or default_round_goal(domain, message_index),
        }
        if model_name is not None:
            round_msg["model_name"] = model_name
        round_messages.append(round_msg)

        updates = {
            "history": _append_history(state.get("history", ""), argument),
            history_key: _append_history(state.get(history_key, ""), argument),
            "current_speaker": speaker_key,
            speaker_field: speaker_key,
            "count": message_index,
            "round_messages": round_messages,
        }
        if parse_status != "valid":
            updates["parse_status"] = parse_status
            updates["blocked"] = True

        if current_response_key:
            updates[current_response_key] = argument
        if store_current_response and current_response_key != "current_response":
            updates["current_response"] = argument
        new_state.update(updates)
        return new_state


def update_debate_state_with_payload(
    *,
    state: Mapping[str, Any],
    raw_response: str,
    speaker_label: str,
    speaker_key: str,
    stance: str,
    history_key: str,
    marker: str,
    claim_prefix: str,
    domain: str,
    speaker_field: str,
    store_current_response: bool = True,
    current_response_key: str | None = None,
    model_name: str | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    is_valid, parse_status, error_detail, payload = validate_debate_response(
        state=state,
        raw_response=raw_response,
        speaker_key=speaker_key,
        stance=stance,
        marker=marker,
        domain=domain,
    )
    if not is_valid or payload is None:
        return _record_unstructured_response(
            state=state,
            raw_response=raw_response,
            speaker_label=speaker_label,
            speaker_key=speaker_key,
            history_key=history_key,
            speaker_field=speaker_field,
            store_current_response=store_current_response,
            current_response_key=current_response_key,
            parse_status=parse_status,
            error_detail=error_detail,
            model_name=model_name,
            domain=domain,
        )

    current_count = safe_int(state.get("count", 0), 0)
    message_index = current_count + 1
    debate_round = (message_index - 1) // 2 + 1 if domain == "investment" else (message_index - 1) // 3 + 1

    claims = [dict(item) for item in (state.get("claims", []) or []) if isinstance(item, Mapping)]
    claim_map = {
        str(item.get("claim_id", "")).strip(): item
        for item in claims
        if str(item.get("claim_id", "")).strip()
    }

    cleaned_response = strip_tagged_json(raw_response, marker)
    claim_counter = safe_int(state.get("claim_counter", 0), 0)
    responded_claim_ids = _filter_known_claim_ids(payload.get("responded_claim_ids"), claim_map)
    resolved_claim_ids = _filter_known_claim_ids(payload.get("resolved_claim_ids"), claim_map)
    unresolved_claim_ids = _filter_known_claim_ids(payload.get("unresolved_claim_ids"), claim_map)

    open_claim_ids = set(_string_list(state.get("open_claim_ids")))
    resolved_set = set(_string_list(state.get("resolved_claim_ids")))
    unresolved_set = set(_string_list(state.get("unresolved_claim_ids")))

    for claim_id in responded_claim_ids:
        if claim_id in claim_map and claim_map[claim_id].get("status") == "open":
            claim_map[claim_id]["status"] = "addressed"

    for claim_id in resolved_claim_ids:
        if claim_id in claim_map:
            claim_map[claim_id]["status"] = "resolved"
        open_claim_ids.discard(claim_id)
        unresolved_set.discard(claim_id)
        resolved_set.add(claim_id)

    for claim_id in unresolved_claim_ids:
        if claim_id in claim_map:
            claim_map[claim_id]["status"] = "unresolved"
        open_claim_ids.add(claim_id)
        unresolved_set.add(claim_id)
        resolved_set.discard(claim_id)

    # Process new claims with per-claim duplicate rejection
    same_side_claims = [
        c
        for c in claims
        if (c.get("speaker_key") and c.get("speaker_key") == speaker_key)
        or (c.get("stance") and c.get("stance") == stance)
    ]
    prev_ev_list = [
        ev
        for pc in same_side_claims
        for ev in (pc.get("evidence") or [])
        if str(ev).strip()
    ]

    new_claim_ids = []
    all_target_claim_ids = []
    duplicate_claim_ids = []
    duplicate_claims = []
    valid_max_sims = []
    all_max_sims = []
    total_new_ev = 0

    for claim_payload in payload.get("new_claims", []) or []:
        claim_text = str(claim_payload.get("claim", "")).strip()
        if not claim_text:
            continue
        claim_counter += 1
        claim_id = f"{claim_prefix}-{claim_counter}"

        is_dup, sim, ev_count, matched_id = _evaluate_claim_duplication(
            claim_payload, same_side_claims, prev_ev_list
        )
        all_max_sims.append(sim)

        if is_dup:
            duplicate_claim_ids.append(claim_id)
            duplicate_claims.append(claim_text)
        else:
            evidence = [
                str(item).strip()
                for item in (claim_payload.get("evidence") or [])[:3]
                if str(item).strip()
            ]
            target_claim_ids = _filter_known_claim_ids(claim_payload.get("target_claim_ids"), claim_map)
            claim_entry = {
                "claim_id": claim_id,
                "speaker": speaker_label,
                "speaker_key": speaker_key,
                "stance": stance,
                "claim": claim_text,
                "evidence": evidence,
                "confidence": claim_payload["confidence"],
                "status": "open",
                "target_claim_ids": target_claim_ids,
                "round_index": message_index,
            }
            claims.append(claim_entry)
            claim_map[claim_id] = claim_entry
            open_claim_ids.add(claim_id)
            new_claim_ids.append(claim_id)
            all_target_claim_ids.extend(target_claim_ids)
            valid_max_sims.append(sim)
            total_new_ev += ev_count

    all_target_claim_ids = list(dict.fromkeys(all_target_claim_ids))

    next_focus_claim_ids = _filter_known_claim_ids(payload.get("next_focus_claim_ids"), claim_map)
    if not next_focus_claim_ids:
        preferred_ids = list(unresolved_set) + [cid for cid in open_claim_ids if cid not in unresolved_set]
        next_focus_claim_ids = preferred_ids[:2]

    summary = str(payload.get("round_summary", "")).strip() or _fallback_summary(cleaned_response)
    round_goal = str(payload.get("round_goal", "")).strip() or default_round_goal(
        domain, message_index
    )

    # Compute information gain metrics for round_messages
    if not same_side_claims:
        information_gain_score = 1.0
        max_similarity = 0.0
        new_evidence_count = sum(len(nc.get("evidence") or []) for nc in payload.get("new_claims", []) or [])
    else:
        max_similarity = max(valid_max_sims) if valid_max_sims else (max(all_max_sims) if all_max_sims else 0.0)
        information_gain_score = max(0.0, min(1.0, round(1.0 - max_similarity, 4)))
        new_evidence_count = total_new_ev

    round_messages = [dict(m) for m in (state.get("round_messages", []) or [])]
    round_msg = {
        "message_index": message_index,
        "debate_round": debate_round,
        "speaker": speaker_label,
        "speaker_key": speaker_key,
        "cleaned_prose": cleaned_response,
        "parse_status": "valid",
        "accepted": True,
        "responded_claim_ids": responded_claim_ids,
        "new_claim_ids": new_claim_ids,
        "target_claim_ids": all_target_claim_ids,
        "resolved_claim_ids": resolved_claim_ids,
        "unresolved_claim_ids": unresolved_claim_ids,
        "resolved": resolved_claim_ids,
        "unresolved": unresolved_claim_ids,
        "round_summary": summary,
        "round_goal": round_goal,
        "information_gain_score": information_gain_score,
        "duplicate_claim_ids": duplicate_claim_ids,
        "duplicate_claims": duplicate_claims,
        "new_evidence_count": new_evidence_count,
        "max_similarity": max_similarity,
    }
    if model_name is not None:
        round_msg["model_name"] = model_name
    if attempts:
        round_msg["attempts"] = [dict(a) for a in attempts]
    round_messages.append(round_msg)

    state_attempts = [dict(a) for a in (state.get("attempts", []) or [])]
    if attempts:
        for a in attempts:
            if not any(
                sa.get("attempt_index") == a.get("attempt_index")
                and sa.get("message_index") == a.get("message_index")
                for sa in state_attempts
            ):
                state_attempts.append(dict(a))
    else:
        state_attempts.append({
            "attempt_index": 1,
            "message_index": message_index,
            "debate_round": debate_round,
            "speaker": speaker_label,
            "parse_status": "valid",
            "accepted": True,
            "error_detail": "",
            "raw_response": raw_response,
        })

    argument = f"{speaker_label}: {cleaned_response}"
    new_state = dict(state)
    updates = {
        "history": _append_history(state.get("history", ""), argument),
        history_key: _append_history(state.get(history_key, ""), argument),
        "current_speaker": speaker_key,
        speaker_field: speaker_key,
        "count": message_index,
        "claims": claims,
        "claim_counter": claim_counter,
        "open_claim_ids": sorted(open_claim_ids),
        "resolved_claim_ids": sorted(resolved_set),
        "unresolved_claim_ids": sorted(unresolved_set),
        "focus_claim_ids": next_focus_claim_ids,
        "round_summary": summary,
        "round_goal": round_goal,
        "round_messages": round_messages,
        "attempts": state_attempts,
    }
    if "blocked" in new_state:
        del new_state["blocked"]
    if "parse_status" in new_state:
        del new_state["parse_status"]
    if "block_reason" in new_state:
        del new_state["block_reason"]
    if current_response_key:
        updates[current_response_key] = argument
    if store_current_response and current_response_key != "current_response":
        updates["current_response"] = argument
    new_state.update(updates)
    return new_state


def _append_history(history: Any, argument: str) -> str:
    existing = str(history or "").strip()
    if not existing:
        return argument
    return f"{existing}\n{argument}"


def _filter_known_claim_ids(values: Any, claim_map: Mapping[str, Any]) -> list[str]:
    result = []
    for item in _string_list(values):
        if item in claim_map:
            result.append(item)
    return result


def _string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _fallback_summary(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return "本轮未提取到有效摘要。"
    return compact[:120]


def build_empty_risk_debate_state() -> dict[str, Any]:
    return {
        "history": "",
        "aggressive_history": "",
        "conservative_history": "",
        "neutral_history": "",
        "latest_speaker": "",
        "current_aggressive_response": "",
        "current_conservative_response": "",
        "current_neutral_response": "",
        "judge_decision": "",
        "count": 0,
        "claims": [],
        "focus_claim_ids": [],
        "open_claim_ids": [],
        "resolved_claim_ids": [],
        "unresolved_claim_ids": [],
        "round_summary": "",
        "round_goal": default_round_goal("risk", 1),
        "claim_counter": 0,
    }
