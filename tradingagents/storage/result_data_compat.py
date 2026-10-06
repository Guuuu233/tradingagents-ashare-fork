"""Read-layer compatibility view for ``result_data.storage.v1``.

DAV-1506 (存储治理 B-1, 依据 D-072 附注唯一存储结构裁定与 DAV-1475 盘点):

* Canonical rows persist each horizon's full payload only once, under
  ``result_data.short_term`` / ``result_data.medium_term``; each slice keeps
  its own ``market_data_context``. The legacy ``horizons.<h>`` map and the
  duplicated top-level ``market_data_context`` are not written anymore.
* This module rebuilds the legacy *view* on demand so every read path that
  still expects ``horizons.<h>`` or a top-level ``market_data_context`` keeps
  working unchanged. The view is a **copy**: it is never written back to the
  ORM row, and persistence helpers strip its virtual keys before save.
* Fidelity contract (裁定 §1): the rebuilt view preserves the old key order,
  ``null`` values and key absence exactly, via ``storage_compat`` masks
  recorded at encode time:

  .. code-block:: json

      {
        "storage_schema_version": "result_data.storage.v1",
        "storage_compat": {
          "version": 1,
          "horizons_order": ["short", "medium"],
          "horizon_key_masks": {
            "short":  ["<old horizons.short key order>"],
            "medium": ["<old horizons.medium key order>"]
          },
          "top_market_context": {"kind": "per_horizon", "order": ["short", "medium"]},
          "top_key_order": ["<old top-level key order>"]
        }
      }

* ``top_market_context.kind`` values:
    ``per_horizon`` — legacy write path A: ``{h: slice.market_data_context}``;
    ``slice``       — legacy write path B: a single slice's mdc
                      (``{"kind": "slice", "horizon": "short"}``);
    ``null``        — the key existed with a ``None`` value;
    ``absent``      — the key did not exist.
* Conflict handling (裁定 §3): when both aliases are present, every common
  key must be equal; a ``horizons``-only key or a top-level mdc that matches
  no slice payload is a conflict. Conflicts fail closed — the report keeps
  its stored form untouched and the conflict is reported, never silently
  reconciled.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)

STORAGE_SCHEMA_VERSION = "result_data.storage.v1"
STORAGE_SCHEMA_KEY = "storage_schema_version"
STORAGE_COMPAT_KEY = "storage_compat"

HORIZON_NAMES: tuple[str, ...] = ("short", "medium")

#: Map a ``horizons.<h>`` slot to its authoritative ``<h>_term`` slot.
HORIZON_TERM_KEY: Dict[str, str] = {h: f"{h}_term" for h in HORIZON_NAMES}

#: Keys that ``*_term`` slices persist but legacy ``horizons.<h>`` never
#: carried (DAV-1475 ``alias_equality.json``: 40/40 slices term-only for the
#: three version/SHA keys, 32/40 additionally for ``instrument_context``).
#: Used only as the *fallback* mask when a canonical row lacks recorded
#: ``storage_compat`` (defensive path — the encoder always records masks).
TERM_ONLY_SLICE_KEYS = frozenset(
    {
        "decision_model_version",
        "evidence_contract_version",
        "generated_by_commit_sha",
        "instrument_context",
    }
)

_VIRTUAL_TOP_KEYS = ("horizons", "market_data_context")


class StorageCompatConflict(ValueError):
    """Raised when alias families disagree on shared content (fail-close).

    ``conflicts`` carries human-readable, machine-greppable descriptions of
    every detected disagreement so operators can route the report to the
    exception list without re-deriving the evidence.
    """

    def __init__(self, conflicts: Iterable[str]):
        self.conflicts: List[str] = [str(c) for c in conflicts]
        super().__init__("; ".join(self.conflicts))


# ---------------------------------------------------------------------------
# detection helpers


def _term_key_for(horizon: str) -> str:
    """Authoritative slice key for a ``horizons`` slot name."""
    return HORIZON_TERM_KEY.get(horizon, f"{horizon}_term")


def is_canonical_storage(result_data: Any) -> bool:
    """True when the row declares the canonical storage layout (v1)."""
    return (
        isinstance(result_data, dict)
        and result_data.get(STORAGE_SCHEMA_KEY) == STORAGE_SCHEMA_VERSION
    )


def _values_equal(a: Any, b: Any) -> bool:
    """Semantic equality for JSON values (dict order is not significant)."""
    if type(a) is not type(b):
        # bool/int edge: Python ``True == 1`` but the stored JSON types differ;
        # treat them as conflicts so no report is silently reconciled.
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_values_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_values_equal(x, y) for x, y in zip(a, b))
    return bool(a == b)


def _top_mdc_kept(result_data: Mapping[str, Any]) -> bool:
    """True when a physically present top-level ``market_data_context`` was
    deliberately *kept* rather than deduplicated (DAV-1545 B-2).

    ``canonicalize_for_single_write`` preserves an unreconstructible
    top-level mdc **only when no slice carries one to compare against**
    (DAV-1551 🟡-1: exemption applies to "no reference", never to
    "contradicts a slice"), and records ``top_market_context = {"kind":
    "absent"}``; the physical key then co-exists with the canonical
    markers. The conflict detector must not treat this sanctioned keep as
    an alias conflict, and the expander must not try to rebuild it.
    """
    compat = result_data.get(STORAGE_COMPAT_KEY)
    if not isinstance(compat, dict):
        return False
    shape = compat.get("top_market_context")
    return (
        isinstance(shape, dict)
        and shape.get("kind") == "absent"
        and "market_data_context" in result_data
    )


def detect_alias_conflicts(result_data: Any) -> List[str]:
    """List every disagreement between co-present alias families.

    Checks, for each ``horizons.<h>`` / ``<h>_term`` pair that physically
    co-exists:

    * common keys whose values differ (裁定 §3 — fail-close);
    * keys present only in ``horizons.<h>`` (would be lost by
      canonicalization — fail-close);
    * ``horizons.<h>`` slots with no ``<h>_term`` twin at all.

    Also flags a physically present top-level ``market_data_context`` that
    matches neither the ``{h: mdc}`` per-horizon map nor any single slice's
    mdc — such a value cannot be reconstructed on read.
    """
    conflicts: List[str] = []
    if not isinstance(result_data, dict):
        return conflicts

    horizons = result_data.get("horizons")
    if isinstance(horizons, dict):
        for horizon, slice_payload in horizons.items():
            if not isinstance(slice_payload, dict):
                # A non-dict slot (None, scalar, list) cannot be round-tripped
                # through a *_term slice — encode would drop it silently and
                # expand could never rebuild it. Report it (DAV-1514 🟢-2).
                conflicts.append(
                    f"horizons.{horizon} is not an object "
                    f"({type(slice_payload).__name__}); cannot canonicalize"
                )
                continue
            term_key = _term_key_for(horizon)
            term = result_data.get(term_key)
            if not isinstance(term, dict):
                conflicts.append(
                    f"horizons.{horizon} has no authoritative {term_key} twin"
                )
                continue
            horizon_keys = set(slice_payload.keys())
            term_keys = set(term.keys())
            for key in sorted(horizon_keys & term_keys):
                if not _values_equal(slice_payload[key], term[key]):
                    conflicts.append(
                        f"horizons.{horizon}.{key} != {term_key}.{key}"
                    )
            for key in sorted(horizon_keys - term_keys):
                conflicts.append(
                    f"horizons.{horizon}.{key} exists only in horizons alias"
                )

    if "market_data_context" in result_data:
        mdc = result_data.get("market_data_context")
        if mdc is not None:
            if _top_mdc_kept(result_data):
                # DAV-1551: the kept exemption covers only "no slice to
                # compare". A kept-mdc row that *does* carry slice mdcs can
                # only come from hand-editing or an out-of-band writer —
                # compare it anyway: a ``{h: mdc}`` map matching the slice,
                # or a verbatim copy of one slice's mdc, is consistent;
                # anything else disagrees with the authoritative record.
                for h in HORIZON_NAMES:
                    term = result_data.get(_term_key_for(h))
                    if not (isinstance(term, dict) and "market_data_context" in term):
                        continue
                    term_mdc = term["market_data_context"]
                    consistent = (
                        _values_equal(mdc, term_mdc)
                        or (
                            isinstance(mdc, dict)
                            and h in mdc
                            and _values_equal(mdc.get(h), term_mdc)
                        )
                    )
                    if not consistent:
                        conflicts.append(
                            f"kept top-level market_data_context disagrees "
                            f"with {_term_key_for(h)}.market_data_context"
                        )
            elif not _top_mdc_shape(result_data, mdc):
                conflicts.append(
                    "top-level market_data_context matches no slice "
                    "market_data_context (unreconstructible)"
                )
    return conflicts


def _top_mdc_shape(
    result_data: Mapping[str, Any], mdc: Any
) -> Optional[Dict[str, Any]]:
    """Classify a physically stored top-level ``market_data_context``.

    Returns the recorded shape dict, or ``None`` when the value cannot be
    derived from any slice payload (conflict).
    """
    term_mdcs: Dict[str, Any] = {}
    for horizon in HORIZON_NAMES:
        term = result_data.get(_term_key_for(horizon))
        if isinstance(term, dict) and "market_data_context" in term:
            term_mdcs[horizon] = term.get("market_data_context")

    if isinstance(mdc, dict):
        # Legacy write path A: ``{short: mdc, medium: mdc}`` — possibly a
        # subset when one horizon failed. Non-horizon keys disqualify.
        if mdc and all(isinstance(k, str) and k in term_mdcs for k in mdc):
            order = [k for k in mdc.keys()]
            if all(_values_equal(mdc[k], term_mdcs[k]) for k in order):
                return {"kind": "per_horizon", "order": order}
        # Single-slice copy (legacy write path B: primary horizon's mdc).
        for horizon in HORIZON_NAMES:
            if horizon in term_mdcs and _values_equal(mdc, term_mdcs[horizon]):
                return {"kind": "slice", "horizon": horizon}
        # Empty dict: equal to an empty per-horizon map only when no slice
        # mdc exists; otherwise ambiguous-but-harmless (rebuild as-is).
        if not mdc:
            if not term_mdcs:
                return {"kind": "per_horizon", "order": []}
            return None
        return None
    # Non-dict mdc (list/scalar): cannot be a slice copy we know of.
    return None


# ---------------------------------------------------------------------------
# encode: legacy -> canonical (offline transform; used by B-3 migration and by
# tests to build canonical fixtures)


def encode_canonical(result_data: Any) -> Dict[str, Any]:
    """Return the canonical (single-write) form of a legacy ``result_data``.

    * Removes the physical ``horizons`` map and the top-level
      ``market_data_context`` when — and only when — they are exact aliases of
      authoritative slice content, recording the reconstruction masks under
      ``storage_compat`` (old key order, null/absence, mdc shape).
    * Rows without ``horizons`` / dual slices pass through unchanged (no
      canonicalization is claimed for single-horizon rows).
    * Any alias conflict raises :class:`StorageCompatConflict` — the report
      must keep its stored form and go to the exception list (裁定 §3).
    """
    if not isinstance(result_data, dict):
        return result_data

    horizons = result_data.get("horizons")
    if not isinstance(horizons, dict) or not horizons:
        # Nothing to deduplicate. A bare top-level mdc is *the* record for
        # single-horizon rows — leave it in place.
        return result_data

    conflicts = detect_alias_conflicts(result_data)
    if conflicts:
        raise StorageCompatConflict(conflicts)

    canonical = dict(result_data)
    compat: Dict[str, Any] = {
        "version": 1,
        "horizons_order": [h for h in horizons.keys() if isinstance(h, str)],
        "horizon_key_masks": {
            h: list(slice_payload.keys())
            for h, slice_payload in horizons.items()
            if isinstance(h, str) and isinstance(slice_payload, dict)
        },
        "top_key_order": list(result_data.keys()),
    }

    if "market_data_context" in result_data:
        mdc = result_data.get("market_data_context")
        if mdc is None:
            compat["top_market_context"] = {"kind": "null"}
        else:
            shape = _top_mdc_shape(result_data, mdc)
            if shape is None:
                # detect_alias_conflicts already catches this; defensive.
                raise StorageCompatConflict(
                    ["top-level market_data_context unreconstructible"]
                )
            compat["top_market_context"] = shape
        canonical.pop("market_data_context", None)
    else:
        compat["top_market_context"] = {"kind": "absent"}

    canonical.pop("horizons", None)
    canonical[STORAGE_COMPAT_KEY] = compat
    canonical[STORAGE_SCHEMA_KEY] = STORAGE_SCHEMA_VERSION
    return canonical


# ---------------------------------------------------------------------------
# write-side canonicalization (DAV-1545 存储 B-2)


def _alias_keys_equal(a: Any, b: Any) -> bool:
    """Alias equality used by write-time canonicalization.

    Same semantic-equality rule as :func:`_values_equal` (dict order
    insignificant); a thin alias so call sites read as an explicit audit
    step rather than a shared implementation detail.
    """
    return _values_equal(a, b)


def _record_masks_for_persisted_aliases(canonical: Dict[str, Any]) -> Dict[str, Any]:
    """Record reconstruction masks for a payload the write path already
    holds as physical aliases.

    The write-time result keeps ``short_term`` / ``medium_term`` (each with
    its own ``market_data_context``) as the authoritative slots; every key
    the legacy ``horizons.<h>`` alias carried is derived from the matching
    ``<h>_term`` slice, so the recorded mask is the term slice's key order
    minus the known term-only stamps — identical to
    :func:`_fallback_horizon_masks`. The top-level key order is captured
    *before* the schema markers are stamped.
    """
    masks = _fallback_horizon_masks(canonical)
    return {
        "version": 1,
        "horizons_order": [h for h in HORIZON_NAMES if h in masks],
        "horizon_key_masks": masks,
        "top_key_order": list(canonical.keys()),
    }


def canonicalize_for_single_write(result_data: Any) -> Any:
    """Normalize a persistence-bound ``result_data`` to the canonical
    single-write layout (DAV-1545 / 存储 B-2).

    Every save funnel calls this at the write boundary so the row stored in
    ``reports.result_data`` only ever carries the authoritative slots:
    ``short_term`` / ``medium_term`` and their in-slice
    ``market_data_context`` — never a physical ``horizons.<h>`` map or a
    duplicated top-level ``market_data_context``.

    Rules:

    * Already canonical (``storage_schema_version == v1``): drop any
      physical alias keys (a compat view that slipped through
      :func:`strip_compat_view_for_persist`, or one smuggled in by an
      out-of-band writer). Foreign content disagreeing with the
      authoritative slices is reported via :func:`detect_alias_conflicts`
      — fail-close semantics are unchanged from B-1.
    * Legacy rows carrying the ``horizons`` alias: run
      :func:`encode_canonical`. Alias conflicts raise
      :class:`StorageCompatConflict` (裁定 §3 fail-close) — the caller
      rejects the payload instead of persisting a silently reconciled row.
    * Legacy rows without ``horizons`` but with ``<h>_term`` slices
      (dual-horizon payload built by the current writer): the top-level
      ``market_data_context`` is deduplicated only when it is a *proven*
      alias of slice content (same reconstruction shapes as
      :func:`encode_canonical`: per-horizon map / single-slice copy /
      ``null`` / absent). An unreconstructible top-level mdc
      **fails closed** (:class:`StorageCompatConflict`) whenever a slice
      mdc exists to contradict it — identical to the ``encode_canonical``
      verdict on a full-alias row (DAV-1551 🟡-1: admissibility must not
      depend on the ``horizons`` key being present); it is kept only when
      no slice mdc exists to compare against (the only-record stock
      form). A non-dict ``horizons`` value is dropped before stamping v1
      (DAV-1551 🟡-2).
    * Anything else (non-dict, single-horizon rows whose top-level mdc is
      the record, rows with no ``*_term`` slices): passed through
      unchanged — 卡面边界: ``no_horizons`` / ``non_dict`` 存量行为不变.
    """
    if not isinstance(result_data, dict):
        return result_data

    if is_canonical_storage(result_data):
        return strip_compat_view_for_persist(result_data)

    if isinstance(result_data.get("horizons"), dict):
        # Full legacy alias form — the B-1 encoder decides canonicalization
        # and fails closed on any unreconstructible alias content.
        return encode_canonical(result_data)

    has_term_slice = any(
        isinstance(result_data.get(f"{h}_term"), dict) for h in HORIZON_NAMES
    )
    if not has_term_slice:
        # Single-horizon / legacy flat payload: the top-level mdc is the
        # record, not an alias — nothing to deduplicate.
        return result_data

    canonical = dict(result_data)
    compat = _record_masks_for_persisted_aliases(canonical)

    # DAV-1551 🟡-2: a non-dict ``horizons`` value (str/None/scalar) must
    # never survive into a v1 row — the invariant is that ``horizons`` on a
    # canonical row only exists as a read-side view and is always a dict.
    # A dict value never reaches here (handled by encode_canonical above);
    # any other shape is unreconstructible junk, not alias content, so
    # dropping it loses nothing restorable.
    canonical.pop("horizons", None)

    if "market_data_context" in canonical:
        mdc = canonical.get("market_data_context")
        if mdc is None:
            compat["top_market_context"] = {"kind": "null"}
            canonical.pop("market_data_context", None)
        else:
            shape = _top_mdc_shape(canonical, mdc)
            if shape is None:
                # DAV-1551 🟡-1: an unreconstructible top-level mdc is only
                # kept when it *cannot be compared* with any slice mdc
                # (no term slice carries one — the same ``term_mdcs`` empty
                # condition under which ``_top_mdc_shape`` accepts an empty
                # dict). When slice mdcs exist, a non-matching top-level
                # value contradicts the authoritative record: fail closed
                # with the same ``StorageCompatConflict`` the
                # ``encode_canonical`` path would raise on a full-alias
                # row, instead of letting admissibility depend on whether
                # the caller happened to carry a ``horizons`` key.
                term_mdcs = {
                    h: canonical[_term_key_for(h)]["market_data_context"]
                    for h in HORIZON_NAMES
                    if isinstance(canonical.get(_term_key_for(h)), dict)
                    and "market_data_context" in canonical[_term_key_for(h)]
                }
                if term_mdcs:
                    raise StorageCompatConflict(
                        [
                            "top-level market_data_context matches no slice "
                            "market_data_context (unreconstructible); "
                            "refusing to persist contradictory alias content"
                        ]
                    )
                # No slice mdc exists to contradict — the value is the only
                # record of its shape (B-1 confirmed stock form). Keep it;
                # ``_top_mdc_kept`` exempts it from alias-conflict
                # detection precisely because there is nothing to compare.
                compat["top_market_context"] = {"kind": "absent"}
            else:
                compat["top_market_context"] = shape
                canonical.pop("market_data_context", None)
    else:
        compat["top_market_context"] = {"kind": "absent"}

    canonical[STORAGE_COMPAT_KEY] = compat
    canonical[STORAGE_SCHEMA_KEY] = STORAGE_SCHEMA_VERSION
    return canonical


# ---------------------------------------------------------------------------
# decode: canonical -> legacy view


def _fallback_horizon_masks(result_data: Mapping[str, Any]) -> Dict[str, List[str]]:
    """Masks for canonical rows that lack recorded ``storage_compat``.

    The encoder always records masks; this path only protects hand-built or
    externally produced canonical payloads. The mask is the term slice's key
    order minus the known term-only keys (DAV-1475 alias audit).
    """
    masks: Dict[str, List[str]] = {}
    for horizon in HORIZON_NAMES:
        term = result_data.get(_term_key_for(horizon))
        if isinstance(term, dict):
            masks[horizon] = [
                k for k in term.keys() if k not in TERM_ONLY_SLICE_KEYS
            ]
    return masks


def _build_horizon_slice(
    term: Mapping[str, Any], mask: List[str], *, label: str
) -> Dict[str, Any]:
    """Rebuild one legacy ``horizons.<h>`` slice from its authoritative term.

    Every masked key must exist in the term payload — a missing key means the
    recorded mask and the authoritative slice disagree (fail-close). Keys the
    term carries but the mask omits (e.g. term-only version stamps) are not
    copied, matching the legacy alias shape exactly.
    """
    missing = [k for k in mask if k not in term]
    if missing:
        raise StorageCompatConflict(
            [f"{label}: mask keys absent from term slice: {missing}"]
        )
    return {k: copy.deepcopy(term[k]) for k in mask}


def expand_compat_view(result_data: Any) -> Dict[str, Any]:
    """Return a *copy* of ``result_data`` with the legacy view reconstructed.

    * Non-canonical rows (legacy physical shape, single-horizon rows,
      non-dicts) are returned as-is — a shallow copy contract applies to the
      top level only for canonical rows; legacy callers keep the same object.
    * Canonical rows (``storage_schema_version == result_data.storage.v1``)
      get ``horizons.<h>`` rebuilt from the authoritative ``<h>_term`` slices
      under the recorded masks, and the top-level ``market_data_context``
      rebuilt per its recorded shape. Top-level key order is restored via
      ``storage_compat.top_key_order``.
    * Reconstruction never mutates the input; every expanded value is a
      deepcopy so consumers may mutate the view freely.
    * Conflicts raise :class:`StorageCompatConflict` (fail-close); use
      :func:`result_data_compat_view` for the log-and-keep-original wrapper.
    """
    if not is_canonical_storage(result_data):
        return result_data

    # 裁定 §3 fail-close: a v1 row that still physically carries the legacy
    # aliases must agree with the authoritative slices on every common key;
    # disagreement leaves the stored form untouched.
    conflicts = detect_alias_conflicts(result_data)
    if conflicts:
        raise StorageCompatConflict(conflicts)

    compat = result_data.get(STORAGE_COMPAT_KEY)
    compat = compat if isinstance(compat, dict) else {}

    masks = compat.get("horizon_key_masks")
    if not isinstance(masks, dict) or not masks:
        masks = _fallback_horizon_masks(result_data)
    horizons_order = [
        h
        for h in (compat.get("horizons_order") or list(masks.keys()))
        if isinstance(h, str) and h in masks
    ]
    # Fallback order for mask slots the recorded ``horizons_order`` does not
    # name (e.g. hand-edited compat blocks): they are appended at the end, in
    # mask declaration order (DAV-1514 🟢-1 — documented for B-3 checks).
    for h in masks:
        if h not in horizons_order:
            horizons_order.append(h)

    view: Dict[str, Any] = dict(result_data)

    if "horizons" not in view:
        rebuilt: Dict[str, Any] = {}
        for horizon in horizons_order:
            mask = masks.get(horizon)
            term = result_data.get(_term_key_for(horizon))
            if not isinstance(mask, list) or not isinstance(term, dict):
                continue
            rebuilt[horizon] = _build_horizon_slice(
                term, mask, label=f"horizons.{horizon}"
            )
        if rebuilt:
            view["horizons"] = rebuilt

    if "market_data_context" not in view:
        shape = compat.get("top_market_context")
        if not isinstance(shape, dict):
            # Defensive default for canonical rows without recorded shape:
            # a {h: mdc} map over the slices that carry one.
            order = [
                h
                for h in horizons_order
                if isinstance(result_data.get(_term_key_for(h)), dict)
                and "market_data_context" in result_data[_term_key_for(h)]
            ]
            shape = {"kind": "per_horizon", "order": order}
        kind = shape.get("kind")
        if kind == "per_horizon":
            mdc_map: Dict[str, Any] = {}
            for horizon in shape.get("order") or []:
                term = result_data.get(_term_key_for(horizon))
                if isinstance(term, dict) and "market_data_context" in term:
                    mdc_map[horizon] = copy.deepcopy(term["market_data_context"])
            view["market_data_context"] = mdc_map
        elif kind == "slice":
            horizon = shape.get("horizon")
            term = result_data.get(_term_key_for(horizon)) if isinstance(horizon, str) else None
            if isinstance(term, dict) and "market_data_context" in term:
                view["market_data_context"] = copy.deepcopy(
                    term["market_data_context"]
                )
            else:
                view["market_data_context"] = None
        elif kind == "null":
            view["market_data_context"] = None
        # kind == "absent": leave the key out.

    elif _top_mdc_kept(result_data):
        # DAV-1545: an unreconstructible top-level mdc kept by the writer —
        # serve it verbatim (the physical key is the record), never
        # overwrite it with a slice-derived view.
        view["market_data_context"] = copy.deepcopy(view["market_data_context"])

    # Restore the legacy top-level key order when recorded. Keys unknown to
    # the recorded order (e.g. post-encode stamps) keep their current order,
    # appended after the recorded ones.
    top_order = compat.get("top_key_order")
    if isinstance(top_order, list) and top_order:
        ordered: Dict[str, Any] = {}
        for key in top_order:
            if isinstance(key, str) and key in view:
                ordered[key] = view[key]
        for key, value in view.items():
            if key not in ordered:
                ordered[key] = value
        view = ordered
    return view


def result_data_compat_view(result_data: Any) -> Dict[str, Any]:
    """Fail-close wrapper: expanded view, or the stored form on conflict.

    Conflicts are logged with enough detail for the exception list; the
    report's stored form is returned untouched so downstream readers keep a
    consistent (if unexpanded) object rather than a partially reconciled one.
    """
    try:
        return expand_compat_view(result_data)
    except StorageCompatConflict as exc:
        logger.warning(
            "[result_data_compat] alias conflict — returning stored form: %s",
            "; ".join(exc.conflicts),
        )
        return result_data


def strip_compat_view_for_persist(result_data: Any) -> Any:
    """Remove reconstructed view keys before a canonical row is persisted.

    裁定 §4: the compat view must never be written back. Write boundaries call
    this so a dict that passed through :func:`expand_compat_view` cannot
    re-persist ``horizons`` / top-level ``market_data_context`` into a v1 row.

    * No-op for non-canonical rows — legacy rows keep their physical aliases
      until B-3 migration encodes them.
    * On v1 rows the virtual top keys are dropped **unconditionally**
      (DAV-1514 🔴 fix): any content that does not match the reconstructed
      view — e.g. read-time backfills like ``horizon_run_metadata`` stamped
      into the served view — is *not* treated as authoritative and must not
      be persisted. Foreign content is surfaced through
      :func:`detect_alias_conflicts` (logged once per call), never silently
      re-attached to the row.
    """
    if not is_canonical_storage(result_data):
        return result_data

    # Report (do not resurrect) physical alias content that disagrees with the
    # authoritative slices — a caller persisting such a row needs the signal.
    conflicts = detect_alias_conflicts(result_data)
    if conflicts:
        logger.warning(
            "[result_data_compat] strip on v1 row with alias conflicts; "
            "virtual keys dropped anyway (fail-close): %s",
            "; ".join(conflicts),
        )

    stripped = dict(result_data)
    for key in _VIRTUAL_TOP_KEYS:
        # DAV-1545: a top-level ``market_data_context`` the writer
        # deliberately kept (``top_market_context.kind == "absent"`` while
        # the physical key is present) is authoritative content, not a
        # rebuilt view key — never strip it.
        if key == "market_data_context" and _top_mdc_kept(result_data):
            continue
        stripped.pop(key, None)
    return stripped


__all__ = [
    "HORIZON_NAMES",
    "HORIZON_TERM_KEY",
    "STORAGE_COMPAT_KEY",
    "STORAGE_SCHEMA_KEY",
    "STORAGE_SCHEMA_VERSION",
    "StorageCompatConflict",
    "TERM_ONLY_SLICE_KEYS",
    "canonicalize_for_single_write",
    "detect_alias_conflicts",
    "encode_canonical",
    "expand_compat_view",
    "is_canonical_storage",
    "result_data_compat_view",
    "strip_compat_view_for_persist",
]
