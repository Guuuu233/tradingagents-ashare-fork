"""Price-Basis Hard Gate (DAV-1142 / DAV-1199, 1142-B2).

Turns the DAV-1198 bypass audit (``price_ref_registry``) into a deterministic
fail-close gate. Unlike the B1 audit this layer DOES change behaviour: a
violation marks the run non-executable and downgrades ``price_basis_version``.

Contract semantics (mirrored verbatim in the manager/trader/risk prompts — the
validator, not model self-discipline, is the enforcement):

1. Inside the qfq execution coordinate (现价 / 支撑 / 压力 / 均线 / entry /
   target / stop) a ``raw`` or ``pit_raw`` price must never directly feed
   levels, discount/premium, odds or distance math. Cross-basis comparison is
   legal only via raw↔raw re-pricing or a full PIT-safe conversion (factor +
   factor_as_of ≤ cutoff) into one coordinate.
2. A decision-driving price_ref with ``basis`` or ``as_of`` unspecified fails
   closed: it is recorded into ``price_basis_gaps`` and must not be consumed.
3. raw+qfq dual display is legal only when the sentence is explicitly labeled
   non-comparable (e.g. 「不可直接比较」); 「共振支撑」-style cross-coordinate
   reasoning is always a violation.
4. An executable level (entry/target/stop) whose registry ref is not legal
   ``vendor_qfq`` (or a validly converted price) must not stand as an
   executable value — the run is marked non-executable
   (``trade_action`` → ``NO_TRADE``, directional actions only).
5. ``price_basis_version`` resolves to ``price_basis.vendor_qfq`` only when
   the gate passes; any unresolved decision-driving basis gap keeps
   ``price_basis.unspecified``. ``result_data`` records
   ``price_ref_contract_version="price_ref.v1"`` (no DB schema change).

Gate output lives on ``state["price_basis_gate"]`` and is propagated into the
horizon result by ``trading_graph``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Set

from tradingagents.dataflows.providers.cn_akshare_provider import (
    PRICE_BASIS_PIT_RAW,
    PRICE_BASIS_RAW,
    PRICE_BASIS_UNSPECIFIED,
    PRICE_BASIS_VENDOR_QFQ,
)
from tradingagents.agents.utils.decision_status import (
    ACTION_BUY,
    ACTION_NO_TRADE,
    ACTION_SELL,
)
from tradingagents.agents.utils.price_ref_registry import (
    COORDINATE_KEYWORDS,
    audit_price_ref_registry,
)

PRICE_REF_CONTRACT_VERSION = "price_ref.v1"
PRICE_BASIS_VERSION_VENDOR_QFQ = "price_basis.vendor_qfq"
PRICE_BASIS_VERSION_UNSPECIFIED = "price_basis.unspecified"

GATE_GAP_KIND = "price_basis_gate_violation"
GATE_BLOCKED_GAP = "price_basis_gate_blocked"

# Reports whose prices directly drive the decision/execution plan.
DECISION_REPORT_FIELDS = (
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
)

# Labels that make a raw+qfq same-sentence display legal (dual display, §3).
NON_COMPARABLE_MARKERS = (
    "不可直接比较",
    "不可直接对比",
    "不可比",
    "不同坐标",
    "口径不同",
    "双列",
)

# Executable-level keywords in decision texts. A value anchored to one of these
# is an executable number and must resolve to a legal qfq/converted ref.
_LEVEL_PATTERN = re.compile(
    r"(?:目标价|目标位|第一目标|第二目标|下行目标|上行目标|止盈位?|止损位?|"
    r"入场价?|进场价?|买入价|卖出价|建仓价|开仓价|出场价|加仓价|减仓价)"
    r"[^0-9]{0,12}?(\d+(?:\.\d+)?)"
)

_DIRECTIONAL_ACTIONS = frozenset({ACTION_BUY, ACTION_SELL})

_VALUE_MATCH_TOLERANCE = 5e-3


def _is_decision_driving(ref: Mapping[str, Any]) -> bool:
    """A ref is decision-driving when it lives in a decision report or sits in
    a coordinate context (support/resistance/anchor/target/stop language).
    Refs inside an explicitly non-comparable labeled sentence are display-only
    dual display, not decision-driving."""
    context = ref.get("context") or ""
    if _has_non_comparable_label(context):
        return False
    if ref.get("source") in DECISION_REPORT_FIELDS:
        return True
    return any(kw in context for kw in COORDINATE_KEYWORDS)


_CONVERSION_VERBS = ("换算", "折算", "折合", "复权因子")


def _is_real_conversion(ref: Mapping[str, Any]) -> bool:
    """Distinguish an actual cross-basis conversion from a bare basis
    declaration. The registry marks any sentence containing 「前复权」 as
    derived; a plain 「前复权目标价 X」 declaration (no conversion verb, no
    derived_from lineage) is a declaration, not a conversion."""
    if not isinstance(ref.get("conversion"), dict):
        return False
    context = ref.get("context") or ""
    return any(kw in context for kw in _CONVERSION_VERBS)


def _has_non_comparable_label(context: str) -> bool:
    return any(marker in context for marker in NON_COMPARABLE_MARKERS)


def _legal_execution_ref(ref: Mapping[str, Any], invalid_ref_ids: Set[str]) -> bool:
    """A ref may back an executable level only when it is vendor_qfq and, if a
    conversion is attached, that conversion was not previewed invalid."""
    if ref.get("basis") != PRICE_BASIS_VENDOR_QFQ:
        return False
    if ref.get("ref_id") in invalid_ref_ids:
        return False
    return True


def _extract_executable_levels(text: str) -> List[float]:
    values: List[float] = []
    if not isinstance(text, str):
        return values
    for m in _LEVEL_PATTERN.finditer(text):
        try:
            values.append(float(m.group(1)))
        except (TypeError, ValueError):
            continue
    return values


def evaluate_price_basis_gate(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Pure evaluation — no mutation. Returns the gate payload.

    Reads ``price_refs`` / ``price_basis_validation`` produced by
    ``audit_price_ref_registry`` (the caller must run the audit first).
    """
    refs = list(state.get("price_refs") or [])
    validation = state.get("price_basis_validation") or {}
    findings = list(validation.get("findings") or []) if isinstance(validation, Mapping) else []

    ref_by_id = {r.get("ref_id"): r for r in refs if isinstance(r, Mapping)}
    invalid_ref_ids: Set[str] = set()
    for f in findings:
        if f.get("kind") == "invalid_conversion":
            invalid_ref_ids.update(f.get("ref_ids") or [])
    # Bare 「前复权…」 declarations are not real conversions — drop them from
    # the invalid set (their basis is already declared vendor_qfq).
    invalid_ref_ids = {
        rid
        for rid in invalid_ref_ids
        if rid in ref_by_id and _is_real_conversion(ref_by_id[rid])
    }

    violations: List[Dict[str, Any]] = []
    allowed_dual_display: List[Dict[str, Any]] = []
    seen_violation_keys: Set[tuple] = set()

    def _violate(kind: str, detail: str, ref_ids: Optional[List[str]] = None,
                 source: Optional[str] = None) -> None:
        key = (kind, tuple(ref_ids or []), detail)
        if key in seen_violation_keys:
            return
        seen_violation_keys.add(key)
        violations.append(
            {
                "kind": kind,
                "ref_ids": ref_ids or [],
                "source": source,
                "detail": detail,
            }
        )

    # Rule 0 — an audit that could not run leaves no basis evidence: fail closed.
    if isinstance(validation, Mapping) and validation.get("audit_error"):
        _violate(
            "audit_unavailable",
            "price_ref 审计未能完成，无 basis 证据，fail-close",
            [],
            None,
        )

    # Rule 1 — decision-driving refs must carry a usable basis and as_of.
    # vendor_qfq refs inherit the run cutoff as as_of (same convention the
    # registry applies to technical-report prices); raw/pit_raw/unspecified
    # refs must carry their own as_of.
    cutoff = state.get("trade_date") if isinstance(state.get("trade_date"), str) else None
    decision_driving = [r for r in refs if isinstance(r, Mapping) and _is_decision_driving(r)]
    for ref in decision_driving:
        if ref.get("basis") == PRICE_BASIS_UNSPECIFIED:
            _violate(
                "decision_driving_unspecified_basis",
                f"决策驱动价格 {ref.get('value')}({ref.get('ref_id')}) basis 无法归因，禁止消费",
                [ref.get("ref_id")],
                ref.get("source"),
            )
        inherits_cutoff = ref.get("basis") == PRICE_BASIS_VENDOR_QFQ and cutoff
        if ref.get("as_of") is None and not inherits_cutoff:
            _violate(
                "decision_driving_missing_as_of",
                f"决策驱动价格 {ref.get('value')}({ref.get('ref_id')}) 缺少 as_of",
                [ref.get("ref_id")],
                ref.get("source"),
            )

    # Rule 2 — audit findings escalate. Cross-basis mixing is a violation
    # unless the sentence is explicitly labeled non-comparable (legal dual
    # display). Invalid conversions on decision-driving refs fail closed.
    for f in findings:
        kind = f.get("kind")
        ref_ids = list(f.get("ref_ids") or [])
        if kind == "invalid_conversion":
            involved = [
                ref_by_id[rid]
                for rid in ref_ids
                if rid in ref_by_id and _is_real_conversion(ref_by_id[rid])
            ]
            if any(_is_decision_driving(r) for r in involved):
                _violate(
                    "invalid_conversion",
                    f.get("detail") or "换算缺少 PIT-safe provenance",
                    ref_ids,
                    f.get("source"),
                )
        elif kind == "basis_mismatch":
            contexts = [
                str(ref_by_id[rid].get("context") or "")
                for rid in ref_ids
                if rid in ref_by_id
            ]
            if contexts and all(_has_non_comparable_label(c) for c in contexts):
                allowed_dual_display.append(
                    {
                        "kind": "labeled_dual_display",
                        "ref_ids": ref_ids,
                        "source": f.get("source"),
                        "detail": f.get("detail"),
                    }
                )
            else:
                _violate(
                    "cross_basis_coordinate_mix",
                    f.get("detail") or "跨 basis 价格混入同一坐标语境",
                    ref_ids,
                    f.get("source"),
                )

    # Rule 2b — direct check: a raw/pit_raw ref sitting in a coordinate context
    # (support/resistance/anchor/level language) is cross-coordinate pollution
    # even when the audit's report-level mismatch rules did not fire. Exempted:
    # explicitly non-comparable labeled dual display, and refs that carry a
    # real, PIT-safe conversion into the qfq coordinate.
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        if ref.get("basis") not in (PRICE_BASIS_RAW, PRICE_BASIS_PIT_RAW):
            continue
        context = ref.get("context") or ""
        if not any(kw in context for kw in COORDINATE_KEYWORDS):
            continue
        if _has_non_comparable_label(context):
            allowed_dual_display.append(
                {
                    "kind": "labeled_dual_display",
                    "ref_ids": [ref.get("ref_id")],
                    "source": ref.get("source"),
                    "detail": f"{ref.get('basis')} 价格 {ref.get('value')} 双列展示（已标不可直接比较）",
                }
            )
            continue
        if _is_real_conversion(ref) and ref.get("ref_id") not in invalid_ref_ids:
            continue
        _violate(
            "cross_basis_coordinate_mix",
            f"{ref.get('basis')} 价格 {ref.get('value')}({ref.get('ref_id')}) 直接进入坐标语境，禁止跨坐标混用",
            [ref.get("ref_id")],
            ref.get("source"),
        )

    # Rule 3 — executable levels in decision texts must resolve to a legal
    # vendor_qfq (or validly converted) registry ref. A level whose own ref is
    # raw/pit_raw/unspecified (or unregistered) must not stand as executable.
    for field in DECISION_REPORT_FIELDS:
        text = state.get(field)
        if not isinstance(text, str):
            continue
        for value in _extract_executable_levels(text):
            candidates = [
                r
                for r in refs
                if isinstance(r, Mapping)
                and r.get("source") == field
                and isinstance(r.get("value"), (int, float))
                and abs(r["value"] - value) <= _VALUE_MATCH_TOLERANCE
            ]
            if not candidates:
                _violate(
                    "unbacked_executable_level",
                    f"可执行价位 {value}（{field}）未登记任何 price_ref，不得生成可执行数值",
                    [],
                    field,
                )
                continue
            if not any(_legal_execution_ref(r, invalid_ref_ids) for r in candidates):
                ids = [r.get("ref_id") for r in candidates]
                bases = sorted({r.get("basis") for r in candidates})
                _violate(
                    "executable_level_wrong_basis",
                    f"可执行价位 {value}（{field}）basis={bases}，非合法 qfq/converted 坐标",
                    ids,
                    field,
                )

    status = "blocked" if violations else "pass"
    price_basis_version = (
        PRICE_BASIS_VERSION_VENDOR_QFQ if status == "pass" else PRICE_BASIS_VERSION_UNSPECIFIED
    )
    return {
        "contract_version": PRICE_REF_CONTRACT_VERSION,
        "status": status,
        "violations": violations,
        "allowed_dual_display": allowed_dual_display,
        "decision_driving_ref_count": len(decision_driving),
        "price_basis_version": price_basis_version,
    }


def enforce_price_basis_gate(state: MutableMapping[str, Any]) -> Dict[str, Any]:
    """Run the gate over a final graph state and fail-close on violation.

    Side effects (all deterministic, no model involvement):
    - ``state["price_basis_gate"]`` = gate payload (idempotent re-run safe).
    - every violation is appended to ``state["price_basis_gaps"]``
      (kind ``price_basis_gate_violation``).
    - on ``blocked``: directional ``trade_action`` is downgraded to
      ``NO_TRADE`` and ``decision_status`` (when dict-shaped) is updated with
      the same fail-close — an unbacked level must not remain executable.
    - ``state["price_basis_version"]`` resolves to ``price_basis.vendor_qfq``
      only on pass, else ``price_basis.unspecified``.

    Never raises on malformed input.
    """
    if not isinstance(state, MutableMapping):
        return {
            "contract_version": PRICE_REF_CONTRACT_VERSION,
            "status": "pass",
            "violations": [],
            "allowed_dual_display": [],
            "decision_driving_ref_count": 0,
            "price_basis_version": PRICE_BASIS_VERSION_VENDOR_QFQ,
        }

    try:
        if "price_refs" not in state or "price_basis_validation" not in state:
            audit_price_ref_registry(state)

        gate = evaluate_price_basis_gate(state)
        state["price_basis_gate"] = gate
        state["price_ref_contract_version"] = PRICE_REF_CONTRACT_VERSION
        state["price_basis_version"] = gate["price_basis_version"]

        if gate["status"] == "blocked":
            gaps = state.setdefault("price_basis_gaps", [])
            if isinstance(gaps, list):
                for v in gate["violations"]:
                    entry = {
                        "kind": GATE_GAP_KIND,
                        "ref_id": (v.get("ref_ids") or [None])[0],
                        "source": v.get("source"),
                        "detail": v.get("detail"),
                    }
                    if entry not in gaps:
                        gaps.append(entry)

            # Fail-close: a polluted decision must not stay executable.
            if state.get("trade_action") in _DIRECTIONAL_ACTIONS:
                state["trade_action"] = ACTION_NO_TRADE
            ds = state.get("decision_status")
            if isinstance(ds, dict):
                if ds.get("trade_action") in _DIRECTIONAL_ACTIONS:
                    ds["trade_action"] = ACTION_NO_TRADE
                for key in ("reason_codes", "failed_checks"):
                    bucket = ds.setdefault(key, [])
                    if isinstance(bucket, list) and GATE_BLOCKED_GAP not in bucket:
                        bucket.append(GATE_BLOCKED_GAP)
        return gate
    except Exception:  # defensive: gate failure must never crash the pipeline
        fallback = {
            "contract_version": PRICE_REF_CONTRACT_VERSION,
            "status": "blocked",
            "violations": [
                {
                    "kind": "gate_internal_error",
                    "ref_ids": [],
                    "source": None,
                    "detail": "price_basis_gate 内部异常，fail-close 处理",
                }
            ],
            "allowed_dual_display": [],
            "decision_driving_ref_count": 0,
            "price_basis_version": PRICE_BASIS_VERSION_UNSPECIFIED,
        }
        try:
            state["price_basis_gate"] = fallback
            state["price_basis_version"] = PRICE_BASIS_VERSION_UNSPECIFIED
            state["price_ref_contract_version"] = PRICE_REF_CONTRACT_VERSION
            gaps = state.setdefault("price_basis_gaps", [])
            if isinstance(gaps, list):
                entry = {
                    "kind": GATE_GAP_KIND,
                    "ref_id": None,
                    "source": None,
                    "detail": "price_basis_gate 内部异常，fail-close",
                }
                if entry not in gaps:
                    gaps.append(entry)
            # fail-close: never leave a possibly-unaudited directional action
            if state.get("trade_action") in _DIRECTIONAL_ACTIONS:
                state["trade_action"] = ACTION_NO_TRADE
            ds = state.get("decision_status")
            if isinstance(ds, dict):
                if ds.get("trade_action") in _DIRECTIONAL_ACTIONS:
                    ds["trade_action"] = ACTION_NO_TRADE
                for key in ("reason_codes", "failed_checks"):
                    bucket = ds.setdefault(key, [])
                    if isinstance(bucket, list) and GATE_BLOCKED_GAP not in bucket:
                        bucket.append(GATE_BLOCKED_GAP)
        except Exception:
            pass
        return fallback


def finalize_price_ref_state(state: MutableMapping[str, Any]) -> Dict[str, Any]:
    """Single post-graph price_ref finalization for every entry point
    (propagate / dual-horizon horizon result / streaming astream / raw invoke).

    Runs the DAV-1198 bypass audit then the DAV-1199 hard gate and returns the
    gate payload. Fail-close mutation of ``trade_action``/``decision_status``
    lives inside ``enforce_price_basis_gate``; callers that derive a signal
    from the raw decision text must still apply the blocked->NO_TRADE
    downgrade to that signal (as ``propagate()`` does).
    """
    audit_price_ref_registry(state)
    return enforce_price_basis_gate(state)
