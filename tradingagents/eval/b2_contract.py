"""DAV-1953 — M1 中线概率契约 b2.v1 (`p_rel_t40`) 核心解析与派生字段.

Implements the controller-approved output contract
(``docs/plans/phase2-midterm/m1-output-contract-spec.md``, DAV-1949 审定版
+ 四项裁定) for the medium-horizon manager probability:

* strict ``p_rel_t40`` integer validation (JSON integer 1–99 inclusive;
  bool / float / str / percent / null / 0 / 100 / out-of-range → reject);
* mutually-exclusive failure classification — ``valid`` / ``missing`` /
  ``malformed`` / ``node_failed`` / ``not_run`` — fail-close, never
  substituted with a default, a direction map, or another horizon's value;
* code-derived fields (contract version, error codes, raw-output audit
  hash, base-rate payload + meta) for the ``result_data.medium_term``
  canonical path and the P4 ledger;
* ledger ``ledger_version`` 1 ↔ 2 read-compatibility helpers shared by
  every ledger reader (m1_eval, run_shadow_trial, m2_periodic_report,
  weekly_report, daily_snapshot_ledger verify).

Purity contract: this module performs **no** I/O of its own — no DB, no
network, no model calls, no parquet reads. The base-rate lookup is an
injectable callable so callers decide where the PIT history comes from.

Ruling details honoured here:

* the canonical block is the FIRST successfully parsed MANAGER_VERDICT
  block under the same loose seam ``capture_manager_forecast_input`` /
  ``extract_tagged_json`` adopts (DAV-1960 🟡-3, option a); a second
  MANAGER_VERDICT block or duplicate JSON keys inside it stay strict
  rejections (``duplicate_block`` / ``duplicate_key``) — never a value
  that hides ambiguity;
* ``not_run`` only when the medium node was planned but never entered;
  ``node_failed`` when the node was entered but produced no successful
  final output; ``missing``/``malformed`` are only classified after the
  node completed;
* explicit ``null`` is a type error (``invalid_type`` → ``malformed``),
  not ``missing``;
* duplicate block / duplicate key → ``malformed`` — never take a value
  that hides ambiguity;
* as_of 基率时点裁定: mature condition is ``actual_exit_date <= T`` when
  the analysis cutoff (Asia/Shanghai) is ≥ T 15:00, else ``<= T-1``;
  missing as_of → ``pit_unverified``;
* base rate is a percentage-point number 0–100 (×100 of the 0–1 source
  value, NOT rounded); model probability stays the strict 1–99 integer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

# --- contract constants (frozen by the spec — never drift) ---------------
PROB_CONTRACT_VERSION = "b2.v1"
P_REL_T40_FIELD = "p_rel_t40"
BASE_FIELD = "p_base_rel_t40"
BASE_STATUS_FIELD = "p_base_rel_t40_status"
BASE_META_FIELD = "p_base_rel_t40_meta"
MANAGER_BLOCK_TAG = "MANAGER_VERDICT"

#: mutually-exclusive model-probability statuses (spec §3)
P40_VALID = "valid"
P40_MISSING = "missing"
P40_MALFORMED = "malformed"
P40_NODE_FAILED = "node_failed"
P40_NOT_RUN = "not_run"
P40_STATUSES = (P40_VALID, P40_MISSING, P40_MALFORMED,
                P40_NODE_FAILED, P40_NOT_RUN)
#: statuses counted as 解析无效 (missing + malformed per the signed gate)
P40_PARSE_INVALID = (P40_MISSING, P40_MALFORMED)

#: base-rate statuses (spec §2.2)
BASE_VALID = "valid"
BASE_MISSING_INDUSTRY = "missing_industry"
BASE_INSUFFICIENT_HISTORY = "insufficient_history"
BASE_SOURCE_UNAVAILABLE = "source_unavailable"
BASE_PIT_UNVERIFIED = "pit_unverified"
BASE_STATUSES = (BASE_VALID, BASE_MISSING_INDUSTRY,
                 BASE_INSUFFICIENT_HISTORY, BASE_SOURCE_UNAVAILABLE,
                 BASE_PIT_UNVERIFIED)

#: primary base-rate config (D-075 / 签收): m=200 shrinkage, W=0 expanding
BASE_PRIMARY_M = 200
BASE_PRIMARY_W = 0
BASE_CALCULATION_VERSION = "m1_base_rate.m200_W0.v1"

#: new keys under P4 ``record.horizons.medium`` (ledger_version=2)
LEDGER_MEDIUM_CONTRACT_KEYS = (
    "p_rel_t40", "p_rel_t40_status", "p_rel_t40_error_codes",
    "probability_contract_version", "raw_output_sha256",
    "p_base_rel_t40", "p_base_rel_t40_status", "p_base_rel_t40_meta",
)
LEDGER_VERSION = 2

_MARKET_CLOSE_HHMM = "15:00"
_CN_TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# strict p_rel_t40 validation
# ---------------------------------------------------------------------------

def _is_json_int(value: Any) -> bool:
    """JSON-integer check: ``type(value) is int`` — bool excluded by type."""
    return type(value) is int


def classify_p_rel_t40(value: Any) -> tuple[str, list[str]]:
    """Classify one extracted field value.

    Returns ``(status, error_codes)`` where status ∈ {valid, missing,
    malformed}. ``missing`` means "key absent"; every other bad shape is
    ``malformed`` with a specific error code. Explicit ``null`` is an
    ``invalid_type`` malformed, never a missing.
    """
    if value is _ABSENT:
        return P40_MISSING, ["missing_field"]
    if value is None:
        return P40_MALFORMED, ["invalid_type"]
    if _is_json_int(value):
        if 1 <= value <= 99:
            return P40_VALID, []
        return P40_MALFORMED, ["out_of_range"]
    if isinstance(value, bool):  # defensive; type-is-int already covers
        return P40_MALFORMED, ["invalid_type"]
    if isinstance(value, float):
        return P40_MALFORMED, ["invalid_type"]
    if isinstance(value, str):
        return P40_MALFORMED, ["invalid_type"]
    return P40_MALFORMED, ["invalid_type"]


class _Absent:
    def __repr__(self) -> str:  # pragma: no cover
        return "<ABSENT>"


_ABSENT = _Absent()


# ---------------------------------------------------------------------------
# MANAGER_VERDICT block inspection (read-side classification only)
# ---------------------------------------------------------------------------

def _manager_verdict_block_status(text: Any) -> tuple[Optional[dict], str, list[str]]:
    """Locate the canonical adopted MANAGER_VERDICT block.

    Reads the SAME loose seam the production adoption path uses
    (``extract_tagged_json`` — first ``<!-- MANAGER_VERDICT: {...} -->``
    non-greedy match), then applies the contract's strict rejections on
    top (DAV-1960 🟡-3, 裁定口径 a：与采纳接缝一致 + 显式去重):

    * trailing prose after the block is NOT a rejection — it is exactly
      what ``capture_manager_forecast_input`` adopted;
    * a second MANAGER_VERDICT block anywhere in the output → invalid
      (``duplicate_block``), never silently picking one;
    * duplicate JSON keys inside the accepted block → invalid
      (``duplicate_key``), handled by ``_block_has_duplicate_key``.

    Returns ``(payload_or_None, shape, error_codes)``:

    * ``shape == "ok"``      → payload is the parsed dict;
    * ``"absent"``           → no MANAGER_VERDICT marker at all;
    * ``"invalid"``          → marker present but no acceptable block
                               (truncated / unparseable / non-dict /
                               duplicate block).
    """
    if not isinstance(text, str):
        return None, "absent", ["missing_block"]
    import re as _re
    from tradingagents.agents.utils.debate_utils import (
        _tagged_occurrences,
    )

    has_marker = bool(
        _re.search(r"<!--\s*" + MANAGER_BLOCK_TAG + r"\b", text,
                   flags=_re.DOTALL))
    if not has_marker:
        return None, "absent", ["missing_block"]
    if len(_tagged_occurrences(text, MANAGER_BLOCK_TAG)) > 1:
        # Same strict rejection as before: two blocks = ambiguity a
        # non-greedy first-match would silently hide.
        return None, "invalid", ["duplicate_block"]
    # The exact adoption-seam pattern used by extract_tagged_json: first
    # non-greedy ``{...}`` after ``<!-- MANAGER_VERDICT:``. An empty dict
    # is a legal parse (→ missing_field downstream), never malformed.
    m = _re.search(
        r"<!--\s*" + MANAGER_BLOCK_TAG + r":\s*(\{.*?\})\s*-->",
        text, flags=_re.DOTALL)
    if m is not None:
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return payload, "ok", []
        return None, "invalid", ["invalid_json"]
    # Marker present but the loose seam cannot parse it (truncated or
    # non-dict payload): distinguish truncated (no closing -->) from
    # malformed JSON for honest error codes.
    if text.find("-->") < 0:
        return None, "invalid", ["truncated_block"]
    return None, "invalid", ["invalid_json"]


def evaluate_p_rel_t40(
    text: Any,
    *,
    node_ran: bool = True,
    node_succeeded: bool = True,
    raw_output: Optional[str] = None,
) -> dict[str, Any]:
    """Evaluate the medium-horizon ``p_rel_t40`` contract on a manager output.

    Parameters
    ----------
    text:
        The FINAL full text the manager node adopted (post-revision seam).
    node_ran:
        False only when the medium node was planned but never entered
        (``not_run``). Callers derive this from run_integrity / horizon
        status — the parser itself never guesses.
    node_succeeded:
        False when the node was entered but threw / timed out / produced
        no successful final output (``node_failed``).
    raw_output:
        The text hashed into ``raw_output_sha256``; defaults to ``text``.

    Returns the b2.v1 evaluation dict — the same shape persisted under
    ``forecast_input`` and re-used by the ledger::

        {node_ran, p_rel_t40, p_rel_t40_status, p_rel_t40_error_codes,
         probability_contract_version, raw_output_sha256}
    """
    if not node_ran:
        return {
            "node_ran": False,
            "p_rel_t40": None,
            "p_rel_t40_status": P40_NOT_RUN,
            "p_rel_t40_error_codes": ["node_not_run"],
            "probability_contract_version": PROB_CONTRACT_VERSION,
            "raw_output_sha256": None,
        }

    audit_src = raw_output if isinstance(raw_output, str) else (
        text if isinstance(text, str) else None)
    raw_sha = (hashlib.sha256(audit_src.encode("utf-8")).hexdigest()
               if audit_src else None)

    if not node_succeeded:
        return {
            "node_ran": True,
            "p_rel_t40": None,
            "p_rel_t40_status": P40_NODE_FAILED,
            "p_rel_t40_error_codes": ["node_exception"],
            "probability_contract_version": PROB_CONTRACT_VERSION,
            "raw_output_sha256": raw_sha,
        }

    payload, shape, codes = _manager_verdict_block_status(text)
    if shape == "absent":
        status, error_codes = P40_MISSING, ["missing_block"]
        value = _ABSENT
    elif shape == "invalid":
        # Block exists but is not uniquely/validly parseable → malformed.
        status, error_codes = P40_MALFORMED, codes or ["invalid_json"]
        value = _ABSENT
    else:
        # Duplicate JSON keys are a malformed source — json.loads keeps the
        # last duplicate and would hide the ambiguity (spec §2.1).
        if _block_has_duplicate_key(text):
            status, error_codes = P40_MALFORMED, ["duplicate_key"]
            value = _ABSENT
        else:
            value = payload.get(P_REL_T40_FIELD, _ABSENT)
            if value is _ABSENT:
                status, error_codes = P40_MISSING, ["missing_field"]
            else:
                status, error_codes = classify_p_rel_t40(value)

    parsed = int(value) if status == P40_VALID else None
    return {
        "node_ran": True,
        "p_rel_t40": parsed,
        "p_rel_t40_status": status,
        "p_rel_t40_error_codes": list(error_codes),
        "probability_contract_version": PROB_CONTRACT_VERSION,
        "raw_output_sha256": raw_sha,
    }


def _block_has_duplicate_key(text: Any) -> bool:
    """True when the accepted MANAGER_VERDICT payload text repeats a key.

    Extracts the raw ``{...}`` literal of the FIRST accepted block opening
    (the same span ``_get_marker_parse_status`` validates) and re-parses
    with ``object_pairs_hook`` — ``json.loads`` alone silently keeps the
    last duplicate.
    """
    if not isinstance(text, str):
        return False
    from tradingagents.agents.utils.debate_utils import (
        _find_machine_block_close,
        _tagged_openings,
    )

    for opening in _tagged_openings(text, MANAGER_BLOCK_TAG):
        close = _find_machine_block_close(text, opening.end())
        if close < 0:
            continue
        payload_text = text[opening.end():close].strip()
        try:
            pairs = json.loads(payload_text, object_pairs_hook=list)
        except Exception:
            continue
        if _pairs_have_dup(pairs):
            return True
        return False
    return False


def _pairs_have_dup(node: Any) -> bool:
    """Recursive duplicate-key check over an ``object_pairs_hook`` tree.

    With ``object_pairs_hook=list`` every OBJECT becomes a list of
    ``(key, value)`` tuples, while JSON arrays stay plain lists of values —
    distinguish them by element shape, not by list-ness alone.
    """
    if not isinstance(node, list):
        return False
    if not all(isinstance(item, tuple) and len(item) == 2 for item in node):
        # JSON array — recurse into elements that may be objects.
        return any(_pairs_have_dup(item) for item in node)
    keys = [k for k, _v in node if isinstance(k, str)]
    if len(keys) != len(set(keys)):
        return True
    return any(_pairs_have_dup(v) for _k, v in node)


# ---------------------------------------------------------------------------
# base-rate as_of 时点判定 (controller ruling #3)
# ---------------------------------------------------------------------------

def parse_analysis_cutoff(value: Any) -> Optional[datetime]:
    """Parse the analysis cutoff to a tz-aware Asia/Shanghai instant.

    Accepts ISO-8601 datetimes (with/without offset), ``YYYY-MM-DD HH:MM:SS``
    and bare ``YYYY-MM-DD`` (interpreted as 00:00 Asia/Shanghai). A bare
    date is therefore treated as BEFORE the 15:00 close — the conservative
    reading, matching "labels maturing on T count only once the signal is
    generated after that day's close".
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_CN_TZ)
        return dt.astimezone(_CN_TZ)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day,
                        tzinfo=_CN_TZ)
    s = str(value).strip()
    if not s:
        return None
    try:
        if "T" in s or "+" in s[10:] or s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_CN_TZ)
            return dt.astimezone(_CN_TZ)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if fmt != "%Y-%m-%d" else s[:10],
                                     fmt).replace(tzinfo=_CN_TZ)
        except ValueError:
            continue
    return None


def maturity_cutoff(signal_date: date, as_of: Optional[datetime]) -> Optional[date]:
    """Return the ``actual_exit_date`` maturity bound for the base rate.

    Controller ruling #3: as_of ≥ T 15:00 (Asia/Shanghai) → bound = T
    (labels maturing ON the signal day count); as_of < T 15:00 → bound =
    T−1. ``as_of`` missing → None (caller maps to ``pit_unverified``).
    """
    if as_of is None:
        return None
    close = datetime(signal_date.year, signal_date.month, signal_date.day,
                     15, 0, tzinfo=_CN_TZ)
    return signal_date if as_of >= close else signal_date - timedelta(days=1)


def build_base_rate_payload(
    *,
    lookup: Optional[Callable[[date, str], Mapping[str, Any]]],
    signal_date: Optional[date],
    industry_code: Optional[str],
    as_of: Optional[datetime],
    history_sha256: Optional[str] = None,
    calculation_version: str = BASE_CALCULATION_VERSION,
    m: int = BASE_PRIMARY_M,
    W: int = BASE_PRIMARY_W,
) -> dict[str, Any]:
    """Build the ``p_base_rel_t40`` triplet (value / status / meta).

    ``lookup(cutoff_date, industry_code)`` → mapping with ``p_m{m}`` (0–1
    shrunken win-rate), ``n`` (industry matured count) and ``n_market``
    (market matured count); ``None`` return means "no history for this
    cell". A lookup raising → ``source_unavailable``. ``lookup=None`` →
    ``source_unavailable`` (no provider configured).

    The emitted value is **percentage points** (0–100, decimals allowed,
    never rounded) — one ×100 conversion of the 0–1 source figure,
    applied exactly once here.
    """
    meta: dict[str, Any] = {
        "m": int(m),
        "W": int(W),
        "industry_code": industry_code if isinstance(industry_code, str)
                         and industry_code.strip() else None,
        "as_of": as_of.isoformat() if isinstance(as_of, datetime) else None,
        "history_sha256": history_sha256,
        "calculation_version": calculation_version,
        "n_industry": None,
        "n_market": None,
        "error_codes": [],
    }

    def _result(value, status, codes):
        meta["error_codes"] = list(codes)
        return {"p_base_rel_t40": value,
                "p_base_rel_t40_status": status,
                "p_base_rel_t40_meta": dict(meta)}

    if as_of is None:
        return _result(None, BASE_PIT_UNVERIFIED, ["pit_unverified"])
    if signal_date is None:
        return _result(None, BASE_PIT_UNVERIFIED, ["pit_unverified"])
    if not meta["industry_code"]:
        return _result(None, BASE_MISSING_INDUSTRY, ["missing_industry"])
    cutoff = maturity_cutoff(signal_date, as_of)
    if cutoff is None:
        return _result(None, BASE_PIT_UNVERIFIED, ["pit_unverified"])
    meta["maturity_cutoff_date"] = cutoff.isoformat()

    if lookup is None:
        return _result(None, BASE_SOURCE_UNAVAILABLE, ["source_unavailable"])
    try:
        cell = lookup(cutoff, meta["industry_code"])
    except Exception as exc:  # provider failure → explicit status, never 0
        logger.warning("[b2_contract] base-rate lookup failed: %s", exc)
        return _result(None, BASE_SOURCE_UNAVAILABLE, ["source_unavailable"])
    if not isinstance(cell, Mapping):
        return _result(None, BASE_INSUFFICIENT_HISTORY,
                       ["insufficient_history"])
    p_01 = cell.get(f"p_m{m}", cell.get("p"))
    n_ind = cell.get("n_industry", cell.get("n"))
    n_mkt = cell.get("n_market")
    meta["n_industry"] = int(n_ind) if isinstance(n_ind, (int, float)) \
        and not isinstance(n_ind, bool) else None
    meta["n_market"] = int(n_mkt) if isinstance(n_mkt, (int, float)) \
        and not isinstance(n_mkt, bool) else None
    if not isinstance(p_01, (int, float)) or isinstance(p_01, bool) \
            or not (0.0 <= float(p_01) <= 1.0):
        return _result(None, BASE_INSUFFICIENT_HISTORY,
                       ["insufficient_history"])
    # single 0–1 → percentage-point conversion; never rounded
    return _result(float(p_01) * 100.0, BASE_VALID, [])


# ---------------------------------------------------------------------------
# ledger v1/v2 read compatibility (controller ruling #1 supplement)
# ---------------------------------------------------------------------------

def normalize_ledger_record(record: Any) -> dict[str, Any]:
    """Return ``record`` unchanged if it is a dict; non-dict → {}.

    v1 and v2 records share the same envelope (``record``, ``prev_hash``,
    ``chain_hash``); v2 only *adds* keys under ``record.horizons.medium``
    and ``record.versions`` plus ``record.ledger_version == 2``. Reading a
    mixed ledger therefore never needs to branch on version for the v1
    fields — this helper centralises that contract so every reader
    handles both uniformly and tolerates unexpected shapes without
    crashing.
    """
    return dict(record) if isinstance(record, Mapping) else {}


def ledger_record_version(record: Any) -> int:
    """Numeric ledger_version of a record; absent/invalid → 1 (legacy)."""
    rec = normalize_ledger_record(record)
    v = rec.get("ledger_version")
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else 1


def medium_contract_fields(record: Any) -> dict[str, Any]:
    """Extract the b2.v1 medium contract keys from a v1 OR v2 record.

    Always returns every contract key (absent → None / empty list /
    status ``not_sealed``) so readers never KeyError on v1 rows — v1 rows
    predate the contract and are reported as ``"not_sealed"`` rather than
    silently fabricating a model ``missing``.
    """
    rec = normalize_ledger_record(record)
    hz = rec.get("horizons")
    medium = hz.get("medium") if isinstance(hz, Mapping) else None
    medium = medium if isinstance(medium, Mapping) else {}
    sealed = ((ledger_record_version(rec) >= 2 and P_REL_T40_FIELD in medium)
              or "p_rel_t40_status" in medium)
    out = {k: medium.get(k) for k in LEDGER_MEDIUM_CONTRACT_KEYS}
    if not sealed:
        out["p_rel_t40_status"] = "not_sealed"
        out["p_rel_t40_error_codes"] = []
    return out


# ---------------------------------------------------------------------------
# 三处读数核对 (spec §4): node state ↔ report persistence ↔ sealed ledger
# ---------------------------------------------------------------------------

_CONTRACT_VALUE_KEYS = (
    "p_rel_t40", "p_rel_t40_status", "p_rel_t40_error_codes",
    "probability_contract_version", "raw_output_sha256",
    "p_base_rel_t40", "p_base_rel_t40_status",
)


def verify_three_readings(
    *,
    node_forecast_input: Any,
    report_medium_slice: Any,
    ledger_record: Any,
) -> dict[str, Any]:
    """Compare the three canonical p_rel_t40 readings (spec §4 三处读数).

    * node   — ``investment_debate_state.manager_verdict.forecast_input``
               (or a medium slice's ``manager_verdict.forecast_input``);
    * report — the persisted medium-term SLICE (``result_data.medium_term``
               or ``horizons.medium``); its ``manager_verdict`` mirror keys
               are read first, ``manager_verdict.forecast_input`` as
               fallback (passing the bare ``manager_verdict`` sub-dict also
               works — its ``forecast_input`` is picked up the same way);
    * ledger — a v1/v2 sealed record (``record.horizons.medium``).

    Returns ``{"consistent": bool, "mismatches": [...], "readings": {...}}``.
    Every value key in ``_CONTRACT_VALUE_KEYS`` must agree on all three
    surfaces that actually carry it — a missing surface is reported, not
    silently equal.
    """

    def _from_node(fi: Any) -> dict:
        fi = fi if isinstance(fi, Mapping) else {}
        return {k: fi.get(k) for k in _CONTRACT_VALUE_KEYS}

    def _surface_present(reading: Mapping[str, Any]) -> bool:
        """A surface "carries" the contract when ANY contract key is
        non-None — an absent/None surface is skipped in comparison (it is
        reported under ``readings`` as absent, never silently equal to a
        real reading). The medium-term slice carries NO top-level keys of
        its own, so presence is judged purely by the values read."""
        return any(v is not None for v in reading.values())

    def _from_report(slice_: Any) -> dict:
        mv = (slice_ or {}).get("manager_verdict") \
            if isinstance(slice_, Mapping) else None
        if not isinstance(mv, Mapping) and isinstance(slice_, Mapping) \
                and ("forecast_input" in slice_ or P_REL_T40_FIELD in slice_):
            # caller passed the bare manager_verdict sub-dict
            mv = slice_
        mv = mv if isinstance(mv, Mapping) else {}
        fi = mv.get("forecast_input") if isinstance(
            mv.get("forecast_input"), Mapping) else {}
        # mirror keys live on manager_verdict top level AND forecast_input
        return {k: (mv[k] if k in mv else fi.get(k))
                for k in _CONTRACT_VALUE_KEYS}

    readings = {
        "node": _from_node(node_forecast_input),
        "report": _from_report(report_medium_slice),
        "ledger": medium_contract_fields(ledger_record),
    }
    present = {src: _surface_present(r) for src, r in readings.items()}
    mismatches: list[str] = []
    for k in _CONTRACT_VALUE_KEYS:
        vals = {src: readings[src].get(k)
                for src in readings if present[src]}
        if len({json.dumps(v, sort_keys=True, default=str) for v in
                vals.values()}) > 1:
            mismatches.append(
                f"{k}: " + " ".join(
                    f"{src}={vals[src]!r}" for src in vals))
    return {"consistent": not mismatches,
            "mismatches": mismatches,
            "surfaces_present": present,
            "readings": readings}


# ---------------------------------------------------------------------------
# run-list parse counters (spec §3 计数口径 — 20×2 / 滚动批次)
# ---------------------------------------------------------------------------


def parse_counters(evaluations: Sequence[Mapping[str, Any]],
                   *,
                   planned: Optional[int] = None,
                   three_way_consistent: int = 0) -> dict[str, int]:
    """Spec §3 run-list counter: mutually exclusive per-attempt tallies.

    Every element of ``evaluations`` is one ``evaluate_p_rel_t40`` output
    for ONE planned medium analysis unit — never double-counted, never
    reconstructed by counting completed reports backwards (units whose
    report never landed stay in the denominator via the caller's run
    list). ``planned`` defaults to len(evaluations); pass the scheduled
    unit count when the caller tracks it separately.
    """
    total = planned if planned is not None else len(evaluations)
    c = {P40_VALID: 0, P40_MISSING: 0, P40_MALFORMED: 0,
         P40_NODE_FAILED: 0, P40_NOT_RUN: 0}
    attempted = completed = 0
    for ev in evaluations:
        st = (ev or {}).get("p_rel_t40_status")
        if st in c:
            c[st] += 1
        if st != P40_NOT_RUN:
            attempted += 1
        if st in (P40_VALID, P40_MISSING, P40_MALFORMED):
            completed += 1
    parse_invalid = c[P40_MISSING] + c[P40_MALFORMED]
    return {
        "planned_units": total,
        "attempted": attempted,
        "completed_units": completed,
        "node_failed": c[P40_NODE_FAILED],
        "not_run": c[P40_NOT_RUN],
        "missing": c[P40_MISSING],
        "malformed": c[P40_MALFORMED],
        "valid": c[P40_VALID],
        "parse_invalid": parse_invalid,
        "three_way_consistent": three_way_consistent,
    }
