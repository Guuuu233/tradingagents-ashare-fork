"""DAV-2097 ③: append-only re-attestation file + effective-PIT lookup order.

总控 10-15 三裁③ (carried on DAV-2097 / DAV-2098): the sealed forward
ledger is never rewritten. Re-classifications under the fixed rules
(DAV-2097 ① read-position + ② refused/unavailable exemption) live in a
sibling append-only file ``pit_reattest.jsonl`` next to
``forward_ledger.jsonl`` — one JSONL line per ``report_id`` carrying:

* ``report_id``        — the sealed report this line re-adjudicates;
* ``input_pit_status`` — verdict under the FIXED rule set
                         (VERIFIED / UNVERIFIED / FAILED);
* ``rule_version``     — :data:`PIT_RULE_VERSION` of the classifier run;
* ``mdc_sha256``       — sha256 of the canonical JSON of the exact
                         ``market_data_context`` mapping(s) the verdict
                         was computed from (``None`` for ``no_evidence``);
* ``adjudicated_at``   — UTC timestamp of the adjudication;
* ``evidence``         — the per-horizon evidence dict
                         (:func:`daily_snapshot_ledger._classify_input_pit`
                         output, incl. ``gap_sources`` / ``horizons``);
* ``status``           — ``"adjudicated"`` or ``"no_evidence"``:
    ``no_evidence`` rows are DIAGNOSTIC ONLY — the report carried no
    readable provenance (result_data null, or no market_data_context /
    source_provenance anywhere the fixed read positions look), so no
    verdict can be re-derived. They never enter the effective-status
    lookup (:func:`resolve_effective_pit` ignores them — the caller keeps
    the ledger's own verdict, which stays UNVERIFIED there).

Evaluation value order (总控原文: 账本 VERIFIED/FAILED 以账本为准,
UNVERIFIED 查补判文件):

* ledger VERIFIED or FAILED → ledger verdict (reattest lines, if any,
  are ignored — the file records them for audit only);
* ledger UNVERIFIED + an ``adjudicated`` reattest line → the reattest
  verdict;
* ledger UNVERIFIED + no adjudicated line (absent or ``no_evidence``) →
  UNVERIFIED stands.

Both writers and readers fail closed: an unreadable line aborts (a
hash-chained-ledger sidecar must never be partially trusted — same
contract as expiry_label_pipeline._iter_ledger_records).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional

#: Name of the append-only re-attestation file living next to the ledger.
REATTEST_FILE = "pit_reattest.jsonl"

#: Rules version stamped on every adjudicated line. Bump when the
#: classification semantics change so audits can tell which rule produced
#: a verdict. v2 = DAV-2097 (per-horizon read + refused/unavailable
#: exemption + per-horizon merge); v1 was the pre-2097 flat classifier.
PIT_RULE_VERSION = "pit_rules.v2"

#: reattest ``status`` value for rows with no readable evidence — recorded
#: for diagnosis, never consulted by the effective-status lookup.
STATUS_NO_EVIDENCE = "no_evidence"
STATUS_ADJUDICATED = "adjudicated"

#: Ledger verdicts that are authoritative and never overridden.
_LEDGER_FINAL = {"VERIFIED", "FAILED"}


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def mdc_sha256(mdc: Any) -> Optional[str]:
    """sha256 over the canonical JSON of the mdc evidence read.

    ``None`` when no mdc was readable (the caller then records a
    ``no_evidence`` line with ``mdc_sha256: null``).
    """
    if mdc is None:
        return None
    return hashlib.sha256(_canonical(mdc)).hexdigest()


# ---------------------------------------------------------------------------
# reading (fail-close)


def iter_reattest_lines(path: Path) -> Iterator[tuple[int, dict]]:
    """Yield (lineno, obj) per non-blank line; raise ValueError on junk."""
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"reattest file {path} line {lineno} unreadable: {exc}"
                ) from exc
            if not isinstance(obj, dict) or not obj.get("report_id"):
                raise ValueError(
                    f"reattest file {path} line {lineno}: "
                    "not a re-attestation object (report_id missing)")
            yield lineno, obj


def load_reattest_index(path: Path) -> dict[str, dict]:
    """report_id -> latest ADJUDICATED reattest line.

    ``no_evidence`` lines are diagnostics — they never enter the index, so
    the lookup order treats them like 'no reattest row' (UNVERIFIED stands).
    A later adjudicated line for the same report_id wins over an earlier
    one (append-only file; the newest line is the current verdict).
    """
    index: dict[str, dict] = {}
    for _lineno, obj in iter_reattest_lines(path):
        if obj.get("status") != STATUS_ADJUDICATED:
            continue
        index[str(obj["report_id"])] = obj
    return index


# ---------------------------------------------------------------------------
# effective-status lookup (评估脚本取值顺序)


def resolve_effective_pit(ledger_status: Any,
                          report_id: Any,
                          reattest_index: Optional[Mapping[str, dict]],
                          ) -> str:
    """总控取值顺序: VERIFIED/FAILED 以账本为准; UNVERIFIED 查补判文件.

    * ledger VERIFIED / FAILED → returned as-is (账本为准, reattest
      ignored even if present);
    * ledger UNVERIFIED (or missing/other — 非终态一律按 UNVERIFIED
      口径查补判) → the reattest file's adjudicated verdict when one
      exists for ``report_id``; otherwise the ledger status
      (UNVERIFIED) stands.
    """
    ls = str(ledger_status or "").upper()
    if ls in _LEDGER_FINAL:
        return ls
    if reattest_index and report_id is not None:
        obj = reattest_index.get(str(report_id))
        if obj is not None:
            v = str(obj.get("input_pit_status") or "").upper()
            if v:
                return v
    return ls or "UNVERIFIED"


# ---------------------------------------------------------------------------
# writing (append-only; idempotent per (report_id, status, mdc_sha256))


def append_reattest(ledger_dir: Path,
                    entries: Iterable[Mapping[str, Any]],
                    *,
                    adjudicated_at: Optional[str] = None) -> dict:
    """Append re-attestation lines to ``<ledger_dir>/pit_reattest.jsonl``.

    Idempotent: an entry whose (report_id, input_pit_status, mdc_sha256,
    status) already appears in the file is skipped, so re-running the
    adjudication never duplicates lines. Returns a small report dict.

    ``entries`` items carry the fields documented in the module docstring;
    missing ``adjudicated_at`` defaults to one shared timestamp for the
    whole batch (single判定时间 for a run).
    """
    path = Path(ledger_dir) / REATTEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = adjudicated_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds")

    existing: set[tuple] = set()
    n_existing = 0
    for _lineno, obj in iter_reattest_lines(path):
        existing.add((
            str(obj.get("report_id")),
            "" if obj.get("input_pit_status") is None
            else str(obj["input_pit_status"]),
            "" if obj.get("mdc_sha256") is None else str(obj["mdc_sha256"]),
            str(obj.get("status") or ""),
        ))
        n_existing += 1

    appended = 0
    skipped = 0
    with open(path, "a", encoding="utf-8") as out:
        for e in entries:
            rec = {
                "report_id": e.get("report_id"),
                "input_pit_status": e.get("input_pit_status"),
                "rule_version": e.get("rule_version") or PIT_RULE_VERSION,
                "mdc_sha256": e.get("mdc_sha256"),
                "adjudicated_at": e.get("adjudicated_at") or stamp,
                "status": e.get("status") or STATUS_ADJUDICATED,
            }
            if e.get("evidence") is not None:
                rec["evidence"] = e["evidence"]
            key = (str(rec["report_id"]),
                   "" if rec["input_pit_status"] is None
                   else str(rec["input_pit_status"]),
                   "" if rec["mdc_sha256"] is None else str(rec["mdc_sha256"]),
                   str(rec["status"]))
            if key in existing:
                skipped += 1
                continue
            out.write(json.dumps(rec, ensure_ascii=False,
                                 separators=(",", ":")) + "\n")
            existing.add(key)
            appended += 1
    return {"file": str(path), "existing_lines": n_existing,
            "appended": appended, "skipped_duplicates": skipped}
