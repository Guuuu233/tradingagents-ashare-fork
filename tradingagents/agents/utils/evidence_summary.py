"""Bounded, fact-dense evidence summaries of analyst reports.

Adjudicators receive compact first-hand excerpts of analyst reports rather than
the full reports, so the final verdict can be anchored to evidence strength
(KNOWN_ISSUES #2 / DAV-68 M2) without blowing up context. ``build_evidence_summary``
is the single owner of that extraction.

The extractor is deterministic — it never invokes an LLM — so a given report
always maps to the same summary and the behaviour is unit-testable. It follows
the KNOWN_ISSUES #2 suggested fix: evidence summaries keep verifiable facts
(numbers, dates, named events) and drop argumentation. The analyst's own
VERDICT direction is preserved as a *labeled* fact, because adjudicators are
asked to tally analyst verdicts but do not receive the full reports.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from tradingagents.agents.utils.debate_utils import extract_tagged_json, strip_tagged_json

DEFAULT_MAX_CHARS = 300
DEFAULT_DENSE_INPUT_MAX_CHARS = 2000
_ELLIPSIS = "…"

# Machine-readable blocks that are output protocol, not evidence.
_MACHINE_TAGS = ("VERDICT", "DEBATE_STATE", "RISK_STATE", "RISK_JUDGE", "MANAGER_VERDICT")

# A line counts as evidence-bearing when it carries a number, a percentage, a
# currency/quantifier unit, or a date-like token — the raw material an
# adjudicator needs to cross-check a claim against the underlying data.
_EVIDENCE_RE = re.compile(
    r"\d|%|％|亿|万|同比|环比|净流入|净流出|上涨|下跌|增速|毛利率|营收|利润|"
    r"元|港元|美元|日期|风险等级|结论倾向"
)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


def extract_verdict_direction(report: str) -> str:
    """Return the analyst's own direction from the VERDICT machine block, if any."""
    if not report:
        return ""
    payload = extract_tagged_json(report, "VERDICT")
    if not payload:
        payload = extract_tagged_json(report, "MANAGER_VERDICT")
    return str(payload.get("direction", "")).strip()


def strip_machine_blocks(report: str) -> str:
    """Remove every machine-readable block (VERDICT / DEBATE_STATE / RISK_* / MANAGER_VERDICT)."""
    text = report or ""
    for tag in _MACHINE_TAGS:
        text = strip_tagged_json(text, tag)
    return text


def _normalize_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    if not line:
        return ""
    # Flatten markdown table cells: "| a | b |" -> "a | b"
    if line.startswith("|") and line.endswith("|"):
        line = line.strip("|").strip()
    return line


def _is_evidence_line(line: str) -> bool:
    if len(line) < 4:
        return False
    return bool(_EVIDENCE_RE.search(line))


_PLACEHOLDER_MARKERS = (
    "【数据缺失】",
    "【数据获取失败】",
    "【暂无数据】",
    "分析报告生成失败",
    "生成异常（输出退化）",
    "生成异常",
    "本项不可用",
    "调用失败：",
    "调用失败:",
    "已确认无数据",
    "确认没有数据",
)


def _is_placeholder_line(line: str) -> bool:
    stripped = line.strip()
    return any(marker in stripped for marker in _PLACEHOLDER_MARKERS)


def build_dense_report_input(
    report: str,
    max_chars: int = DEFAULT_DENSE_INPUT_MAX_CHARS,
    role_name: str = "",
) -> tuple[str, str, int]:
    """Return structured high-density summary and key evidence excerpts for a report.

    Args:
        report: Full report text.
        max_chars: Maximum character limit for the extracted input.
        role_name: Name of the analyst role for labeling context.

    Returns:
        tuple of (input_text, mode, char_count)
        where mode is "full", "structured_dense_summary_and_excerpts", or "empty".
    """
    if not report or not str(report).strip():
        return "", "empty", 0

    raw_text = str(report).strip()
    raw_len = len(raw_text)
    if raw_len <= max_chars:
        return raw_text, "full", raw_len

    direction = extract_verdict_direction(raw_text)
    body = strip_machine_blocks(raw_text)

    lines: list[str] = []
    for raw in body.splitlines():
        line = _normalize_line(raw)
        if not line or _TABLE_SEPARATOR_RE.match(line):
            continue
        lines.append(line)

    evidence_lines = [ln for ln in lines if _is_evidence_line(ln)]
    chosen = evidence_lines if evidence_lines else lines

    seen: set[str] = set()
    selected_parts: list[str] = []
    current_length = 0

    prefix = f"[分析师结论：{direction}] " if direction else ""
    current_length += len(prefix)

    for line in chosen:
        if line in seen:
            continue
        seen.add(line)
        if current_length + len(line) + 2 > max_chars:
            remaining_budget = max_chars - current_length - len(_ELLIPSIS) - 2
            if remaining_budget > 20:
                selected_parts.append(line[:remaining_budget] + _ELLIPSIS)
            break
        selected_parts.append(line)
        current_length += len(line) + 1

    extracted_body = "\n".join(selected_parts) if selected_parts else body[:max_chars]
    final_text = f"{prefix}{extracted_body}".strip()
    return final_text, "structured_dense_summary_and_excerpts", len(final_text)


def build_evidence_summary(report: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return a bounded, fact-dense evidence summary of one analyst report.

    The summary is composed of the report's evidence-bearing lines (those
    containing numbers / percentages / quantitative keywords), preserving
    document order, and is prefixed with the analyst's own direction when one
    is present so adjudicators can tally verdicts without the full report.

    Args:
        report: Full analyst report text (may be empty).
        max_chars: Hard cap on the returned summary length (exclusive of the
            direction prefix).

    Returns:
        A compact, single-paragraph evidence summary, or an empty string when
        the report is empty (callers may then omit the summary line entirely
        rather than inject a misleading placeholder).
    """
    if not report or not report.strip():
        return ""

    direction = extract_verdict_direction(report)
    body = strip_machine_blocks(report)

    lines: list[str] = []
    for raw in body.splitlines():
        line = _normalize_line(raw)
        if not line:
            continue
        if _TABLE_SEPARATOR_RE.match(line):
            continue
        lines.append(line)

    evidence_lines = [ln for ln in lines if _is_evidence_line(ln) and not _is_placeholder_line(ln)]
    clean_lines = [ln for ln in lines if not _is_placeholder_line(ln)]
    # If the report carries almost no numbers, fall back to a small leading
    # excerpt so the adjudicator still sees the analyst's core content.
    chosen = evidence_lines if evidence_lines else clean_lines[:6]

    seen: set[str] = set()
    parts: list[str] = []
    for line in chosen:
        if line in seen:
            continue
        seen.add(line)
        parts.append(line)

    summary = "；".join(parts)
    if len(summary) > max_chars:
        summary = summary[: max_chars - len(_ELLIPSIS)].rstrip("；，、 ") + _ELLIPSIS

    if direction and summary:
        return f"[分析师结论：{direction}] {summary}"
    if direction:
        return f"[分析师结论：{direction}]"
    return summary


# Fixed individual character caps (inclusive of source label and direction prefix)
SEVEN_SOURCE_CAPS: dict[str, int] = {
    "market_report": 300,
    "news_report": 240,
    "fundamentals_report": 300,
    "macro_report": 240,
    "sentiment_report": 180,
    "smart_money_report": 260,
    "volume_price_report": 260,
}

# Fixed order of seven sources and their fixed labels
SEVEN_SOURCE_SPECS: tuple[tuple[str, str, int], ...] = (
    ("market_report", "市场技术证据摘要：", 300),
    ("news_report", "新闻证据摘要：", 240),
    ("fundamentals_report", "基本面证据摘要：", 300),
    ("macro_report", "宏观/板块证据摘要：", 240),
    ("sentiment_report", "情绪证据摘要：", 180),
    ("smart_money_report", "主力资金证据摘要：", 260),
    ("volume_price_report", "量价证据摘要：", 260),
)

MAX_SEVEN_SOURCE_TOTAL_CHARS = 2400

_SOURCE_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "market_report": ("market_research_report", "market"),
    "news_report": ("news",),
    "fundamentals_report": ("fundamentals",),
    "macro_report": ("macro",),
    "sentiment_report": ("social_report", "social", "sentiment"),
    "smart_money_report": ("smart_money",),
    "volume_price_report": ("volume_price",),
}

_UNAVAILABLE_STATUS_KEYWORDS = frozenset(
    {
        "failed",
        "unavailable",
        "empty",
        "error",
        "missing",
        "partial_failure",
        "not_found",
        "rejected",
        "available_unverified_as_of",
        "unverified",
        "refused",
        "future",
    }
)


def _check_source_status(raw: Any) -> tuple[bool, bool, str]:
    """Check if a source report is missing or failed.

    Returns:
        tuple of (is_missing, is_failed, clean_text)
    """
    if raw is None:
        return True, False, ""

    if isinstance(raw, dict):
        status = str(raw.get("status", "")).strip().lower()
        if status in _UNAVAILABLE_STATUS_KEYWORDS or raw.get("failed"):
            return False, True, ""
        raw = raw.get("report") or raw.get("content") or ""

    text = str(raw).strip()
    if not text:
        return True, False, ""

    lower = text.lower()
    if lower in _UNAVAILABLE_STATUS_KEYWORDS:
        return False, True, ""
    if text.startswith("分析报告生成失败"):
        return False, True, ""
    if len(text) <= 220 and any(m in text for m in ("生成异常（输出退化）", "本项不可用", "调用失败：", "调用失败:", "【数据缺失】", "【数据获取失败】", "【暂无数据】")):
        return False, True, ""
    if len(text) < 40 and any(w in lower or w in text for w in ("不可用", "失败", "refused", "rejected", "future")):
        return False, True, ""
    if text in ("已确认无数据", "确认没有数据", "无数据", "【数据缺失】", "【数据获取失败】", "confirmed empty"):
        return False, True, ""

    return False, False, text


@dataclass(frozen=True)
class SevenSourceEvidenceBundle:
    """Bounded, fact-dense evidence summaries across seven analyst sources.

    Adjudicators receive compact first-hand excerpts of analyst reports
    rather than full reports.
    """

    text: str
    missing_sources: tuple[str, ...] = ()
    failed_sources: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.text

    def __len__(self) -> int:
        return len(self.text)

    def __bool__(self) -> bool:
        return bool(self.text)


def build_seven_source_evidence_bundle(
    reports: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> SevenSourceEvidenceBundle:
    """Combine seven analyst reports into bounded evidence summaries.

    Fixed order:
        1. market_report (≤ 300 chars)
        2. news_report (≤ 240 chars)
        3. fundamentals_report (≤ 300 chars)
        4. macro_report (≤ 240 chars)
        5. sentiment_report (≤ 180 chars)
        6. smart_money_report (≤ 260 chars)
        7. volume_price_report (≤ 260 chars)

    Total combined text is strictly capped at len(text) <= 2400.
    Deterministic, zero LLM calls.
    """
    merged: dict[str, Any] = {}
    if reports is not None:
        if isinstance(reports, Mapping):
            merged.update(reports)
        else:
            raise TypeError(f"reports must be a Mapping or None, got {type(reports).__name__}")
    merged.update(kwargs)

    missing_sources: list[str] = []
    failed_sources: list[str] = []
    lines: list[str] = []

    for source_key, label, cap in SEVEN_SOURCE_SPECS:
        raw = merged.get(source_key)
        if raw is None:
            for alt in _SOURCE_KEY_ALIASES.get(source_key, ()):
                if alt in merged and merged[alt] is not None:
                    raw = merged[alt]
                    break

        is_missing, is_failed, text = _check_source_status(raw)
        if is_missing:
            missing_sources.append(source_key)
            continue
        if is_failed:
            failed_sources.append(source_key)
            continue

        direction = extract_verdict_direction(text)
        prefix_len = len(f"[分析师结论：{direction}] ") if direction else 0
        budget = max(20, cap - len(label) - prefix_len)
        summary = build_evidence_summary(text, max_chars=budget)
        if not summary.strip():
            continue

        line = f"{label}{summary}"
        if len(line) > cap:
            line = line[: cap - len(_ELLIPSIS)].rstrip("；，、 ") + _ELLIPSIS
        lines.append(line)

    combined_text = "\n".join(lines)
    if len(combined_text) > MAX_SEVEN_SOURCE_TOTAL_CHARS:
        combined_text = combined_text[: MAX_SEVEN_SOURCE_TOTAL_CHARS - len(_ELLIPSIS)].rstrip() + _ELLIPSIS

    # Code-enforced hard ceiling
    if len(combined_text) > MAX_SEVEN_SOURCE_TOTAL_CHARS:
        combined_text = combined_text[:MAX_SEVEN_SOURCE_TOTAL_CHARS]

    return SevenSourceEvidenceBundle(
        text=combined_text,
        missing_sources=tuple(missing_sources),
        failed_sources=tuple(failed_sources),
    )


def build_seven_source_evidence_summary(
    reports: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> str:
    """Return bounded 7-source first-hand evidence summary string.

    Fixed order: market, news, fundamentals, macro, sentiment, smart_money, volume_price.
    Hard cap: len(text) <= 2400.
    """
    bundle = build_seven_source_evidence_bundle(reports, **kwargs)
    return bundle.text
