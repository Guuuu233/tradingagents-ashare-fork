"""PriceRef registry and per-reference basis/provenance audit layer (DAV-1142 / DAV-1198).

Pure bypass audit — ZERO effect on decision, target/stop, H1b eligibility, or any
production behavior. It extracts price references from report texts into a
per-run registry, assigns deterministic canonical basis labels, records
provenance, and emits side-channel fields on the final state:

- ``state["price_refs"]``           — registry entries (value/basis/source/as_of/lineage)
- ``state["price_basis_gaps"]``     — missing basis / missing as_of / basis mismatch gaps
- ``state["price_basis_validation"]``— preview-only validation verdict (never fail-closed)

Contract rules (DAV-1142):

- basis reuses the existing canonical short labels (``vendor_qfq`` / ``raw`` /
  ``pit_raw`` / ``unspecified``) — no new ``disclosure_raw``/``derived`` basis.
- known typed disclosures are deterministically labelled: 大宗交易/龙虎榜 → ``raw``;
  增持/减持/回购/定增·增发·发行 → ``pit_raw``. Untyped news prices NEVER default to raw.
- technical market reports inherit ``vendor_qfq`` (their data channel is known).
- models cannot self-certify basis: a model-emitted price that cannot be traced
  back to a registry entry stays ``unspecified``; one matching a registered
  vendor_qfq value inherits it via registry back-reference.
- derived/converted prices keep ``derived_from`` / ``conversion`` lineage;
  "derived" is provenance, never a basis. A raw→qfq conversion missing factor
  provenance, or with ``factor_as_of`` later than the run cutoff, previews invalid.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Tuple

from tradingagents.dataflows.providers.cn_akshare_provider import (
    PRICE_BASIS_PIT_RAW,
    PRICE_BASIS_RAW,
    PRICE_BASIS_UNSPECIFIED,
    PRICE_BASIS_VENDOR_QFQ,
)

# ---------------------------------------------------------------------------
# Canonical basis vocabulary (re-exported for consumers/tests)
# ---------------------------------------------------------------------------

CANONICAL_PRICE_BASES = frozenset(
    (
        PRICE_BASIS_VENDOR_QFQ,
        PRICE_BASIS_RAW,
        PRICE_BASIS_PIT_RAW,
        PRICE_BASIS_UNSPECIFIED,
    )
)

# Reports scanned by the audit. market/volume_price are built on the vendor
# qfq data channel, so their unmarked prices inherit vendor_qfq. All other
# reports are model-authored: their prices must trace back to the registry
# (back-reference) or stay unspecified.
TECHNICAL_REPORT_FIELDS = ("market_report", "volume_price_report")
MODEL_REPORT_FIELDS = (
    "news_report",
    "fundamentals_report",
    "sentiment_report",
    "macro_report",
    "smart_money_report",
    "game_theory_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
)
REPORT_FIELDS = TECHNICAL_REPORT_FIELDS + MODEL_REPORT_FIELDS

# ---------------------------------------------------------------------------
# Typed disclosure vocabulary → deterministic basis
# ---------------------------------------------------------------------------

# keyword -> disclosure_type. Order matters only for longest-match preference.
TYPED_DISCLOSURE_KEYWORDS: Mapping[str, str] = {
    "大宗交易": "block_trade",
    "大宗": "block_trade",
    "龙虎榜": "dragon_tiger_list",
    "增持": "shareholder_increase",
    "减持": "shareholder_decrease",
    "回购": "repurchase",
    "定增": "private_placement",
    "增发": "private_placement",
    "发行": "issuance",
}

# Deterministic disclosure_type -> canonical basis.
# Contemporaneous exchange-published quotes (block trades, dragon-tiger) are raw;
# point-in-time holder-transaction / issuance disclosures are pit_raw.
DISCLOSURE_TYPE_BASIS: Mapping[str, str] = {
    "block_trade": PRICE_BASIS_RAW,
    "dragon_tiger_list": PRICE_BASIS_RAW,
    "shareholder_increase": PRICE_BASIS_PIT_RAW,
    "shareholder_decrease": PRICE_BASIS_PIT_RAW,
    "repurchase": PRICE_BASIS_PIT_RAW,
    "private_placement": PRICE_BASIS_PIT_RAW,
    "issuance": PRICE_BASIS_PIT_RAW,
}

# Words anchoring a price to the technical (qfq) coordinate system. A raw/pit_raw
# price referenced next to these inside the same report is a candidate mismatch.
COORDINATE_KEYWORDS = (
    "现价",
    "最新价",
    "当前价",
    "支撑",
    "压力",
    "阻力",
    "锚",
    "均线",
    "技术位",
    "止损",
    "止盈",
    "目标价",
)

# Markers that a price was derived / converted from another price.
DERIVED_KEYWORDS = ("换算", "折算", "折合", "复权因子", "前复权", "除权")

_PRICE_KEYWORD_PATTERN = re.compile(
    r"(?:现价|最新价|当前价|收盘价?|收于|开盘价?|最高|最低|均价|成本价?|"
    r"目标价|止损|止盈|支撑|压力位?|阻力位?|成交价|作价|单价|定增价|"
    r"发行价|回购价|增持价|减持价|投标价|锚定?|报价|每股)"
    r"[^0-9%]{0,8}?(\d+(?:\.\d+)?)(?!\s*[%％倍分角]|亿|万|股|手|户|家|次|日|天|年|月)"
)

_BARE_YUAN_PATTERN = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*元(?!\s*[/%％]|/股|吨|克|人|次)"
)

_PER_SHARE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*元/股|每股\s*(\d+(?:\.\d+)?)\s*元"
)

_DATE_PATTERN = re.compile(
    r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?"
)

_FACTOR_PATTERN = re.compile(r"(?:复权)?因子\s*[:：为是]?\s*(\d+(?:\.\d+)?)")

_SENTENCE_SPLIT_PATTERN = re.compile(r"[。；;！!？?\n\r]+")

_VALUE_MATCH_TOLERANCE = 5e-3


def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_PATTERN.split(text) if s and s.strip()]


def _extract_dates(text: str) -> List[str]:
    dates: List[str] = []
    for m in _DATE_PATTERN.finditer(text):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            dates.append(f"{y}-{mo:02d}-{d:02d}")
    return dates


def _extract_price_values(sentence: str) -> List[Tuple[float, int]]:
    """Extract (value, position) price mentions from a sentence, deduplicated."""
    found: List[Tuple[float, int]] = []
    seen_spans: List[Tuple[int, int]] = []

    def _add(value_str: str, start: int, end: int) -> None:
        for s0, e0 in seen_spans:
            if start < e0 and s0 < end:
                return
        try:
            value = float(value_str)
        except (TypeError, ValueError):
            return
        if value <= 0:
            return
        seen_spans.append((start, end))
        found.append((value, start))

    for m in _PER_SHARE_PATTERN.finditer(sentence):
        num = m.group(1) or m.group(2)
        _add(num, m.start(), m.end())
    for m in _BARE_YUAN_PATTERN.finditer(sentence):
        _add(m.group(1), m.start(), m.end())
    for m in _PRICE_KEYWORD_PATTERN.finditer(sentence):
        _add(m.group(1), m.start(1), m.end(1))

    found.sort(key=lambda item: item[1])
    return found


def _detect_disclosure_type(sentence: str) -> Optional[str]:
    """Return the disclosure type for the longest matching keyword, else None."""
    best: Optional[Tuple[int, str]] = None  # (keyword length, type)
    for kw, dtype in TYPED_DISCLOSURE_KEYWORDS.items():
        if kw in sentence:
            if best is None or len(kw) > best[0]:
                best = (len(kw), dtype)
    return best[1] if best else None


def _has_coordinate_keyword(sentence: str) -> bool:
    return any(kw in sentence for kw in COORDINATE_KEYWORDS)


def _has_derived_keyword(sentence: str) -> bool:
    return any(kw in sentence for kw in DERIVED_KEYWORDS)


def _conversion_target_basis(sentence: str) -> Optional[str]:
    """Target basis declared by a conversion sentence (前复权→qfq; 不复权→raw)."""
    return _declared_basis(sentence)


def _declared_basis(window: str) -> Optional[str]:
    """Basis explicitly declared by sentence keywords (deterministic text marker,
    not model self-certification — the label comes from the declared word).
    """
    if "不复权" in window or "未复权" in window:
        return PRICE_BASIS_RAW
    if "前复权" in window or "qfq" in window.lower():
        return PRICE_BASIS_VENDOR_QFQ
    return None


def _norm_date(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    m = _DATE_PATTERN.search(value)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    stripped = value.strip()
    return stripped or None


def build_price_ref_registry(
    reports: Mapping[str, Any],
    *,
    cutoff: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the price-ref registry, gap ledger, and validation preview.

    Args:
        reports: mapping of report field name -> report text (non-string values
            are ignored).
        cutoff: run cutoff date (YYYY-MM-DD). Technical-report prices inherit it
            as as_of; conversion factor_as_of later than cutoff previews invalid.

    Returns:
        {"price_refs": [...], "price_basis_gaps": [...],
         "validation": {"status": ..., "findings": [...]}}
    """
    cutoff_norm = _norm_date(cutoff)
    refs: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []

    def _next_id() -> str:
        return f"pr-{len(refs) + 1:03d}"

    def _add_gap(kind: str, ref_id: str, report: str, detail: str) -> None:
        entry = {"kind": kind, "ref_id": ref_id, "source": report, "detail": detail}
        if entry not in gaps:
            gaps.append(entry)

    # Pass 1 — extract refs per report/sentence with deterministic basis.
    for report_name in REPORT_FIELDS:
        text = reports.get(report_name)
        if not isinstance(text, str) or not text.strip():
            continue
        is_technical = report_name in TECHNICAL_REPORT_FIELDS
        for sentence in _split_sentences(text):
            mentions = _extract_price_values(sentence)
            if not mentions:
                continue
            disclosure_type = _detect_disclosure_type(sentence)
            derived = _has_derived_keyword(sentence)
            sentence_dates = _extract_dates(sentence)
            sentence_as_of = sentence_dates[0] if sentence_dates else None

            sentence_ref_ids: List[Tuple[str, float, str]] = []
            for value, pos in mentions:
                mention_window = sentence[max(0, pos - 15):pos]
                declared = _declared_basis(mention_window)
                ref: Dict[str, Any] = {
                    "ref_id": _next_id(),
                    "value": value,
                    "basis": PRICE_BASIS_UNSPECIFIED,
                    "source": report_name,
                    "provenance": "model_text",
                    "as_of": sentence_as_of,
                    "context": sentence[:120],
                }
                if disclosure_type is not None:
                    ref["basis"] = DISCLOSURE_TYPE_BASIS[disclosure_type]
                    ref["provenance"] = f"typed_disclosure:{disclosure_type}"
                    ref["disclosure_type"] = disclosure_type
                elif declared is not None:
                    ref["basis"] = declared
                    ref["provenance"] = f"declared_basis:{declared}"
                elif is_technical:
                    ref["basis"] = PRICE_BASIS_VENDOR_QFQ
                    ref["provenance"] = "technical_report:vendor_qfq"
                    if ref["as_of"] is None:
                        ref["as_of"] = cutoff_norm

                if derived:
                    ref["provenance"] = "derived:" + ref["provenance"]
                    factor_match = _FACTOR_PATTERN.search(sentence)
                    ref["conversion"] = {
                        "factor": float(factor_match.group(1)) if factor_match else None,
                        "factor_as_of": sentence_as_of,
                    }
                    target = _conversion_target_basis(sentence)
                    if target is not None and disclosure_type is None:
                        ref["basis"] = target

                refs.append(ref)
                sentence_ref_ids.append((ref["ref_id"], value, ref["basis"]))

            # derived_from lineage: other price mentions in the same sentence.
            if derived and len(sentence_ref_ids) > 1:
                ids = [rid for rid, _v, _b in sentence_ref_ids]
                for rid in ids:
                    ref = next(r for r in refs if r["ref_id"] == rid)
                    ref["derived_from"] = [other for other in ids if other != rid]

    # Pass 2 — registry back-reference inheritance for unspecified model prices.
    qfq_values = [
        r["value"]
        for r in refs
        if r["basis"] == PRICE_BASIS_VENDOR_QFQ and not r["provenance"].startswith("derived")
    ]
    for ref in refs:
        if ref["basis"] != PRICE_BASIS_UNSPECIFIED:
            continue
        for qv in qfq_values:
            if abs(ref["value"] - qv) <= _VALUE_MATCH_TOLERANCE:
                ref["basis"] = PRICE_BASIS_VENDOR_QFQ
                ref["provenance"] = "registry_backref:vendor_qfq"
                if ref["as_of"] is None:
                    ref["as_of"] = cutoff_norm
                break

    # Pass 3 — gaps: missing basis / missing as_of.
    for ref in refs:
        if ref["basis"] == PRICE_BASIS_UNSPECIFIED:
            _add_gap(
                "missing_basis",
                ref["ref_id"],
                ref["source"],
                f"价格 {ref['value']} 无法归因 basis（模型新价不可自证）",
            )
        if ref["as_of"] is None:
            _add_gap(
                "missing_as_of",
                ref["ref_id"],
                ref["source"],
                f"价格 {ref['value']} 缺少 as_of",
            )

    # Pass 4 — basis mismatch detection.
    concrete_bases = {PRICE_BASIS_VENDOR_QFQ, PRICE_BASIS_RAW, PRICE_BASIS_PIT_RAW}
    by_report: Dict[str, List[Dict[str, Any]]] = {}
    for ref in refs:
        by_report.setdefault(ref["source"], []).append(ref)

    for report_name, report_refs in by_report.items():
        # R1: same-sentence refs with different concrete bases.
        by_sentence: Dict[str, List[Dict[str, Any]]] = {}
        for ref in report_refs:
            by_sentence.setdefault(ref["context"], []).append(ref)
        for _ctx, s_refs in by_sentence.items():
            bases = {r["basis"] for r in s_refs} & concrete_bases
            if len(bases) > 1:
                ids = [r["ref_id"] for r in s_refs]
                finding = {
                    "kind": "basis_mismatch",
                    "rule": "same_sentence_mixed_basis",
                    "source": report_name,
                    "ref_ids": ids,
                    "detail": f"同句混用 basis {sorted(bases)}: {ids}",
                }
                findings.append(finding)
                _add_gap(
                    "basis_mismatch",
                    ids[0],
                    report_name,
                    finding["detail"],
                )

        # R2: a qfq ref anchored to technical coordinates while the same report
        # also carries raw/pit_raw refs (the b188060f pattern: raw 大宗价 vs
        # qfq 现价/锚/支撑混用).
        non_qfq = [r for r in report_refs if r["basis"] in (PRICE_BASIS_RAW, PRICE_BASIS_PIT_RAW)]
        if not non_qfq:
            continue
        for ref in report_refs:
            if ref["basis"] != PRICE_BASIS_VENDOR_QFQ:
                continue
            if not _has_coordinate_keyword(ref["context"]):
                continue
            other_ids = [r["ref_id"] for r in non_qfq]
            detail = (
                f"qfq 价格 {ref['value']}({ref['ref_id']}) 与 raw/pit_raw 价格 "
                f"{other_ids} 处于同一坐标语境（现价/支撑/锚等）"
            )
            findings.append(
                {
                    "kind": "basis_mismatch",
                    "rule": "cross_basis_coordinate_reference",
                    "source": report_name,
                    "ref_ids": [ref["ref_id"], *other_ids],
                    "detail": detail,
                }
            )
            _add_gap("basis_mismatch", ref["ref_id"], report_name, detail)

    # Pass 5 — conversion validity preview.
    invalid = False
    for ref in refs:
        conv = ref.get("conversion")
        if not isinstance(conv, dict):
            continue
        factor = conv.get("factor")
        factor_as_of = _norm_date(conv.get("factor_as_of"))
        reason = None
        if factor is None:
            reason = "raw→qfq 换算缺少 factor provenance"
        elif cutoff_norm and factor_as_of and factor_as_of > cutoff_norm:
            reason = f"factor_as_of {factor_as_of} 晚于 cutoff {cutoff_norm}"
        elif cutoff_norm and factor_as_of is None:
            reason = "换算缺少 factor_as_of"
        if reason is not None:
            invalid = True
            findings.append(
                {
                    "kind": "invalid_conversion",
                    "source": ref["source"],
                    "ref_ids": [ref["ref_id"]],
                    "detail": reason,
                }
            )

    status = "invalid" if invalid else ("gaps_present" if (gaps or findings) else "ok")
    validation = {
        "status": status,
        "preview_only": True,
        "cutoff": cutoff_norm,
        "findings": findings,
    }
    return {
        "price_refs": refs,
        "price_basis_gaps": gaps,
        "validation": validation,
    }


def audit_price_ref_registry(state: MutableMapping[str, Any]) -> Dict[str, Any]:
    """Run the bypass audit over a final graph state and attach side-channel fields.

    Writes ``price_refs`` / ``price_basis_gaps`` / ``price_basis_validation`` into
    ``state`` and returns the audit payload. Never raises on malformed input and
    never mutates decision/target/stop fields.
    """
    if not isinstance(state, MutableMapping):
        return {"price_refs": [], "price_basis_gaps": [], "validation": {"status": "ok", "preview_only": True, "findings": []}}

    try:
        cutoff = state.get("trade_date")
        reports = {name: state.get(name) for name in REPORT_FIELDS}
        # Also scan nested horizon results when present (short_term/medium_term/result_data).
        for sub_key in ("short_term", "medium_term", "result_data"):
            sub = state.get(sub_key)
            if isinstance(sub, Mapping):
                for name in REPORT_FIELDS:
                    if name not in reports or reports[name] in (None, ""):
                        if isinstance(sub.get(name), str):
                            reports[name] = sub[name]

        result = build_price_ref_registry(reports, cutoff=cutoff)
    except Exception:
        # DAV-1199 🟡-1 (upgraded to mandatory): the audit is bypass-only — a
        # malformed state must never crash the pipeline. Fail-closed: the error
        # is recorded as a price_basis_gap and flagged on the validation payload
        # so downstream (price_basis_gate) treats every price as un-audited and
        # non-consumable, rather than silently letting them through.
        result = {
            "price_refs": [],
            "price_basis_gaps": [
                {
                    "kind": "audit_error",
                    "ref_id": None,
                    "source": None,
                    "detail": "price_ref 审计内部异常，全部价格视为未审计、不可消费",
                }
            ],
            "validation": {
                "status": "audit_error",
                "preview_only": True,
                "audit_error": True,
                "findings": [],
            },
        }
    state["price_refs"] = result["price_refs"]
    state["price_basis_gaps"] = result["price_basis_gaps"]
    state["price_basis_validation"] = result["validation"]
    return result
