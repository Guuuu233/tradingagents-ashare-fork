#!/usr/bin/env python3
"""DAV-1614 (存储 B-4): 存量瘦身 — legacy result_data rows -> canonical v1.

Rewrites reports.result_data rows that still carry the physical legacy
aliases (``horizons.<h>`` and/or a duplicated top-level
``market_data_context``) into the single authoritative layout
(``result_data.storage.v1``): each horizon payload lives only in
``<h>_term`` and its in-slice ``market_data_context``.

Hard contract (卡面): after conversion, reading the row through the B-1
compat layer (``expand_compat_view`` + ``json.dumps``) must produce bytes
identical to the pre-conversion stored text. This is verified per row
inside the same transaction that writes the new bytes; a mismatch rolls
that row back and counts it as a verification failure — never a partial
silently-different write.

Preservation model: ``*_term`` slices are the authoritative content —
including the term-only stamps the legacy ``horizons.<h>`` alias never
carried — so they are kept physically as-is. ``horizons`` becomes a pure
read-side view rebuilt under the recorded masks (semantically identical
to the legacy alias on every common key — proven by
``detect_alias_conflicts`` — and differing only by the term-only keys
that are the point of the dedup). The top-level ``market_data_context``
is removed and, when a ``*_term`` slice lacks its own mdc, the
slice-appropriate mdc value is transplanted from it verbatim. Only the
``horizons`` member, the top-level mdc member and the two appended
``storage_compat`` / ``storage_schema_version`` markers are touched; all
other bytes stay exactly, so ``_witness_equal(stored_new, stored_old)``
holds for every converted row.

Safety (DAV-1544 mechanism, reused):
* Independent zstd pre-image export of every row about to be written,
  outside the database directory, sha256 self-verified. Export failure
  means zero writes of that batch.
* In-transaction drift check: a row whose current sha256 no longer
  matches its exported pre-image is skipped and counted, never forced.
* ``restore`` subcommand writes the exported bytes back row-by-row with
  the same before/after verification.
* Batch commits (--batch-size, default 200) + journal.jsonl: every
  committed report_id is journaled, so an interrupted run resumes by
  skipping already-committed ids.
* Runtime guard identical to the T+5 backfill: healthz must be ok and
  the runtime must report zero active analyses; a busy/unknown state
  skips the run. A production-path guard additionally refuses writes
  before the signed post-10-09 handoff.
* Rows the compat layer cannot canonicalize (StorageCompatConflict —
  alias disagreements, unreconstructible top-level mdc) are fail-close:
  left untouched and recorded in the on-disk exceptions report.

Final step (卡面): ``--vacuum-into`` produces the slimmed database file;
``verify`` (integrity_check + row-count + per-row compat-view compare)
must pass before the release card's switch steps apply. See
work/dav1614-b4-release-card.md for the switch draft.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import zstandard

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Independent pre-image exports live outside the data directory (库外固定目录),
# same convention as DAV-1544's work/tplus5-pre-export.
PRE_EXPORT_DIR = ROOT / "work/dav1614-pre-export"
PRE_EXPORT_VERSION = 1

# Journals live outside the data directory as well; each line is a
# self-contained JSON event so an interrupted run can resume and audit.
JOURNAL_DIR = ROOT / "work/dav1614-journal"

from tradingagents.dataflows.trade_calendar import now_cn
from tradingagents.storage.result_data_compat import (
    STORAGE_COMPAT_KEY,
    STORAGE_SCHEMA_KEY,
    STORAGE_SCHEMA_VERSION,
    StorageCompatConflict,
    encode_canonical,
    expand_compat_view,
    is_canonical_storage,
    strip_compat_view_for_persist,
)

HORIZON_NAMES = ("short", "medium")
MARKER_KEYS = (STORAGE_COMPAT_KEY, STORAGE_SCHEMA_KEY)


# ---------------------------------------------------------------------------
# write-side canonicalization
#
# B-4 delegates to B-1's ``encode_canonical`` (imported). The migration's
# scope is exactly the rows that physically carry the legacy ``horizons``
# map — see canonicalize_for_slim's docstring for why ``*_term``-only rows
# are deliberately out of scope.


def canonicalize_for_slim(result_data):
    """B-4 offline canonicalization — delegates to B-1's
    ``encode_canonical`` for the rows this migration owns.

    Scope decision (see card / 卡面): B-4 removes the *physical* legacy
    aliases — ``horizons.<h>`` and the duplicated top-level
    ``market_data_context`` — i.e. exactly the domain
    ``encode_canonical`` covers. ``*_term``-only rows (no physical
    ``horizons``) are deliberately out of scope: stamping them v1 would
    *add* a ``horizons`` key to their compat-layer read (the view
    rebuilds it from the term slices), which is a read change the 逐字
    contract forbids — and whether those rows should be re-stamped is a
    read-layer/B-2 concern, not this migration's. They are reported as
    ``no_alias_content`` and left byte-exact.
    """
    if not isinstance(result_data, dict):
        return result_data
    if is_canonical_storage(result_data):
        return strip_compat_view_for_persist(result_data)
    return encode_canonical(result_data)


# ---------------------------------------------------------------------------
# raw-JSON helpers (token-level, whitespace- and spelling-preserving)


def _json_node(text, start=0):
    """Parse one JSON value; object nodes keep member token spans.

    Same approach as backfill_tplus5_shadow._json_node: members map key ->
    (key_start, child_node), so byte slices of member values stay verbatim.
    """
    while text[start].isspace():
        start += 1
    if text[start] != "{":
        value, end = json.JSONDecoder().raw_decode(text, start)
        return {"start": start, "end": end, "value": value}
    pos = start + 1
    members = {}
    while True:
        while text[pos].isspace():
            pos += 1
        if text[pos] == "}":
            break
        key_start = pos
        key, pos = json.JSONDecoder().raw_decode(text, pos)
        if key in members:
            raise ValueError("duplicate JSON key; unsafe to patch")
        while text[pos].isspace():
            pos += 1
        if text[pos] != ":":
            raise ValueError("invalid JSON object")
        node = _json_node(text, pos + 1)
        members[key] = (key_start, node)
        pos = node["end"]
        while text[pos].isspace():
            pos += 1
        if text[pos] == "}":
            break
        if text[pos] != ",":
            raise ValueError("invalid JSON separator")
        pos += 1
    return {"start": start, "end": pos + 1, "close": pos, "members": members,
            "value": {k: v[1]["value"] for k, v in members.items()}}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _remove_member(text, node, key):
    """Remove member `key` (plus a neighbouring comma) from the object
    `node` describes in `text`. Returns new text."""
    key_start, child = node["members"][key]
    start, end = key_start, child["end"]
    # Prefer deleting the preceding comma.
    pos = key_start - 1
    while pos > node["start"] and text[pos].isspace():
        pos -= 1
    if pos > node["start"] and text[pos] == ",":
        start = pos
    else:
        pos = end
        while pos < node["close"] and text[pos].isspace():
            pos += 1
        if pos < node["close"] and text[pos] == ",":
            end = pos + 1
    return text[:start] + text[end:]


def _append_members(text, node, fragments):
    """Insert '"key":value' fragments before the object's closing brace."""
    addition = ("," if node["members"] else "") + ",".join(fragments)
    return text[:node["close"]] + addition + text[node["close"]:]


def _replace_value(text, node, key, new_text):
    """Replace the raw value token of member `key` with `new_text`."""
    _key_start, child = node["members"][key]
    return text[:child["start"]] + new_text + text[child["end"]:]


def _drop_keys(value, keys):
    """Deep-copy `value` minus `keys` at every object level (recursive),
    preserving all other content and order."""
    if isinstance(value, dict):
        return {k: _drop_keys(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [_drop_keys(v, keys) for v in value]
    return value


def _slice_witness(value):
    """Witness for one ``*_term`` slice: the slice minus its
    ``market_data_context`` member. A slice's mdc may legitimately differ
    between legacy and converted rows — it can be transplanted from the
    removed top-level mdc map (verified separately by the view contract),
    so it is excluded from the non-alias witness.
    """
    if not isinstance(value, dict):
        return value
    return {k: v for k, v in value.items() if k != "market_data_context"}


def _legacy_witness(text):
    """Semantic witness of a *legacy* row: the parsed document minus the
    aliases being removed (``horizons``, top-level ``market_data_context``);
    each ``*_term`` slice is reduced to its non-mdc witness.
    """
    node = _json_node(text)
    if "members" not in node:
        return node["value"]
    out = {}
    for k, v in node["value"].items():
        if k in ("horizons", "market_data_context"):
            continue
        if k.endswith("_term"):
            v = _slice_witness(v)
        out[k] = v
    return out


def _converted_witness(text):
    """Semantic witness of a *converted* row: parsed document minus the v1
    marker keys; each ``*_term`` slice reduced to its non-mdc witness."""
    node = _json_node(text)
    if "members" not in node:
        return node["value"]
    out = {}
    for k, v in node["value"].items():
        if k in MARKER_KEYS:
            continue
        if k.endswith("_term"):
            v = _slice_witness(v)
        out[k] = v
    return out


def _witness_equal(converted_text, legacy_text):
    """True when the converted row and the legacy row agree on every member
    except: the removed aliases, the appended markers, and ``*_term`` slices'
    ``market_data_context`` (which may be transplanted from the removed
    top-level mdc — its correctness is proven separately by the
    compat-view 逐字 check).

    Comparison is semantic (parsed values), not raw bytes. What this
    witness locks: no foreign member is added/removed and no non-alias
    value changed.
    """
    return _converted_witness(converted_text) == _legacy_witness(legacy_text)


def _view_bytes(text):
    """json.dumps of the compat-layer read of `text`, minus the v1 marker
    keys (``storage_compat`` / ``storage_schema_version``).

    ``expand_compat_view`` surfaces the stored marker keys in the served
    view; they are metadata, not report content. The 逐字 contract
    compares the *report content* a reader sees: legacy rows have no
    markers, so we drop them on both sides.
    """
    view = expand_compat_view(json.loads(text))
    if isinstance(view, dict):
        for key in MARKER_KEYS:
            view.pop(key, None)
    return _dump(view).encode("utf-8")


# ---------------------------------------------------------------------------
# conversion


def convert_legacy_text(raw):
    """Convert a legacy-format result_data JSON *string* to canonical text.

    Returns the new JSON text. Raises StorageCompatConflict (fail-close)
    or ValueError when the row cannot be converted without changing the
    compat-layer read output.
    """
    node = _json_node(raw)
    if "members" not in node:
        raise StorageCompatConflict(["result_data is not a JSON object"])
    value = node["value"]

    canonical = canonicalize_for_slim(value)
    if not is_canonical_storage(canonical):
        raise StorageCompatConflict(
            ["canonicalize_for_slim did not produce a v1 row"])
    compat = canonical[STORAGE_COMPAT_KEY]

    # Contract precheck on parsed values before touching bytes.
    if _view_bytes(_dump(canonical)) != _view_bytes(raw):
        raise ValueError("canonical round-trip differs from legacy view")

    text = raw
    kept_mdc = "market_data_context" in canonical  # kept-mdc stock form

    # Preservation model (revised): the ``*_term`` slices are already the
    # authoritative content readers consume directly — including the
    # term-only stamps (decision_model_version / evidence_contract_version
    # / generated_by_commit_sha / instrument_context) that the legacy
    # ``horizons.<h>`` alias deliberately does NOT carry. Transplanting
    # horizons.<h> verbatim into <h>_term would silently delete those
    # stamps from the direct-read path. So we keep ``*_term`` physically
    # as-is and rebuild ``horizons`` purely as the read-side view under
    # the recorded masks — the alias content the view serves is the mask's
    # subset of each term slice, which is semantically identical to the
    # legacy physical alias on every common key (detect_alias_conflicts
    # already proved that) and differs only by the term-only keys that are
    # the point of the dedup. The only byte-level splices are therefore:
    # remove ``horizons``, remove/dedupe the top-level mdc, append markers.

    # 1. Re-parse (node already describes `text`), handle the top-level mdc.
    if "market_data_context" in node["members"] and not kept_mdc:
        mdc_node = node["members"]["market_data_context"][1]
        mdc_raw = text[mdc_node["start"]:mdc_node["end"]]
        shape = compat.get("top_market_context") or {}
        kind = shape.get("kind")
        if kind == "per_horizon":
            # Transplant the mdc map's per-horizon values verbatim into
            # slices that lack their own market_data_context. Collect
            # insert positions per horizon, then apply them right-to-left
            # so each insert can't shift the next one's target close.
            inserts = {}
            for h in shape.get("order") or []:
                term = value.get(f"{h}_term")
                if not isinstance(term, dict) or "market_data_context" in term:
                    continue
                if "members" not in mdc_node or h not in mdc_node["members"]:
                    raise ValueError(f"per_horizon mdc missing member {h}")
                sub = mdc_node["members"][h][1]
                sub_raw = mdc_raw[sub["start"] - mdc_node["start"]:
                                  sub["end"] - mdc_node["start"]]
                term_node = node["members"][f"{h}_term"][1]
                inserts[h] = (term_node["close"],
                              ("," if term_node["members"] else "")
                              + '"market_data_context":' + sub_raw)
            for h, (pos, addition) in sorted(
                    inserts.items(), key=lambda kv: kv[1][0], reverse=True):
                text = text[:pos] + addition + text[pos:]
            if inserts:
                node = _json_node(text)
        elif kind == "slice":
            h = shape.get("horizon")
            term_key = f"{h}_term" if isinstance(h, str) else None
            term = value.get(term_key) if term_key else None
            if term_key and term_key in node["members"] and \
                    isinstance(term, dict) and "market_data_context" not in term:
                text = _append_members(
                    text, node["members"][term_key][1],
                    ['"market_data_context":' + mdc_raw])
                node = _json_node(text)
        # kind null / absent: nothing to transplant.
        text = _remove_member(text, node, "market_data_context")
        node = _json_node(text)

    # 3. Remove the physical horizons member.
    if "horizons" in node["members"]:
        text = _remove_member(text, node, "horizons")
        node = _json_node(text)

    # 4. Append the canonical markers.
    text = _append_members(text, node, [
        _dump(STORAGE_COMPAT_KEY) + ":" + _dump(compat),
        _dump(STORAGE_SCHEMA_KEY) + ":" + _dump(STORAGE_SCHEMA_VERSION),
    ])

    # 5. Post-conditions (defense in depth; _write_row re-verifies).
    if not _witness_equal(text, raw):
        raise ValueError("non-alias bytes changed during conversion")
    if _view_bytes(text) != _view_bytes(raw):
        raise ValueError("compat view differs after conversion")
    return text


# ---------------------------------------------------------------------------
# db helpers (same pattern as the T+5 backfill)


def _open_db(path, writable=False):
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(
        f"file:{quote(str(p))}?mode={'rw' if writable else 'ro'}", uri=True, timeout=0)
    conn.row_factory = sqlite3.Row
    if not writable:
        # 重型只读纪律: never spill temp b-trees onto the live disk.
        conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _validate_db(path):
    try:
        with closing(_open_db(path)) as conn:
            count = conn.execute(
                "SELECT count(*) FROM reports WHERE status='completed'").fetchone()[0]
            columns = {row[1] for row in conn.execute("PRAGMA table_info(reports)")}
            if not {"id", "symbol", "trade_date", "status", "result_data"} <= columns:
                raise RuntimeError("reports columns missing; migration forbidden")
            if not count:
                raise RuntimeError("explicit database has no completed reports")
    except sqlite3.Error as exc:
        raise RuntimeError("cannot read explicit reports database") from exc


def _iter_db_raw(path, after_id=""):
    """Keyset-paged raw row iterator (id, result_data text) — resumable."""
    _validate_db(path)
    last_id = after_id
    while True:
        with closing(_open_db(path)) as conn:
            rows = conn.execute(
                "SELECT rowid AS __rowid__, id, result_data FROM reports "
                "WHERE status='completed' AND id>? ORDER BY id LIMIT 500",
                (last_id,)).fetchall()
        if not rows:
            return
        for row in rows:
            last_id = row["id"]
            yield {"id": row["id"], "rowid": row["__rowid__"],
                   "raw": row["result_data"]}


def check_runtime_guard(db_path, health_url):
    """Fail closed on unknown runtime state. Never restart or wait for idle.
    Identical contract to backfill_tplus5_shadow.check_runtime_guard."""
    import requests
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(health_url.rstrip("/") + "/healthz", timeout=(2, 7))
        payload = response.json()
    except requests.Timeout:
        return {"allowed": False, "reason": "health_busy_timeout"}
    except (requests.RequestException, ValueError):
        return {"allowed": False, "reason": "health_unavailable"}
    finally:
        session.close()
    if response.status_code != 200 or payload.get("status") != "ok":
        return {"allowed": False, "reason": "health_not_ok"}
    count = payload.get("active_analysis_count")
    if type(count) is not int or count < 0:
        return {"allowed": False, "reason": "active_analysis_count_unknown"}
    if count:
        return {"allowed": False, "reason": "busy", "running_count": count}
    try:
        with closing(_open_db(db_path)) as conn:
            n = conn.execute(
                "SELECT count(*) FROM reports WHERE status IN ('running','pending','queued')"
            ).fetchone()[0]
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "scheduled_analyses" in tables:
                n += conn.execute(
                    "SELECT count(*) FROM scheduled_analyses "
                    "WHERE last_run_status IN ('running','pending','queued')").fetchone()[0]
    except sqlite3.Error:
        return {"allowed": False, "reason": "analysis_count_unavailable"}
    return {"allowed": n == 0, "reason": "idle" if n == 0 else "busy", "running_count": n}


def _is_production(path):
    production = Path("/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db")
    return Path(path).resolve() == production.resolve() or \
        (production.exists() and os.path.samefile(path, production))


# ---------------------------------------------------------------------------
# journal (resumable) + exceptions report


def _open_journal(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8")


def _journal_emit(handle, obj):
    handle.write(json.dumps(obj, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def load_committed_ids(journal_path):
    """Ids already committed in a previous (interrupted) run — resume skip."""
    done = set()
    p = Path(journal_path)
    if not p.is_file():
        return done
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        # A row counts as committed when the journal records its
        # successful write. Two real emit sites mark it: _write_row
        # emits {"phase":"committed"} inside the transaction, and the
        # flush loop emits {"event":<outcome>} where outcome is
        # "converted"/"unchanged" once the transaction closed. Accept
        # both spellings (DAV-1640 🟡-1 — previously only the
        # never-emitted {"event":"committed"} matched, so resume skip
        # silently never fired).
        if event.get("report_id") and (
                event.get("phase") == "committed"
                or event.get("event") in ("committed", "converted", "unchanged")):
            done.add(event["report_id"])
    return done


# ---------------------------------------------------------------------------
# independent pre-image export (DAV-1544 mechanism, reused)


def _pre_export_stream(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    raw = os.fdopen(fd, "wb")

    class _HashingWriter:
        def __init__(self, sink):
            self._sink = sink
            self.sha = hashlib.sha256()

        def write(self, data):
            self.sha.update(data)
            return self._sink.write(data)

        def flush(self):
            self._sink.flush()

    hashing = _HashingWriter(raw)
    stream = zstandard.ZstdCompressor(level=3).stream_writer(hashing, closefd=False)
    return stream, raw, hashing


def export_pre_images(db_path, planned, *, export_dir=None):
    """Export the exact stored bytes of the rows in `planned`.

    `planned` entries are {"id", "sha256"} where sha256 was computed on the
    pass-1 read. The export re-reads each row's current bytes and refuses
    the batch when a row drifted between plan and export (DAV-1544 drift
    rule). Returns {path, sha256, records}; a raised error means the caller
    must not open any write transaction for this batch.
    """
    export_dir = Path(export_dir or PRE_EXPORT_DIR)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S-%f")
    path = export_dir / (stamp + ".jsonl.zst")
    stream = raw = None
    records = {}
    try:
        stream, raw, hashing = _pre_export_stream(path)

        def emit(obj):
            stream.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

        emit({
            "version": PRE_EXPORT_VERSION, "kind": "header",
            "tool": "slim_result_data_storage",
            "db_path": str(db_path), "created_at": datetime.now().isoformat(),
        })
        with closing(_open_db(db_path)) as conn:
            for item in planned:
                report_id = item["id"]
                row = conn.execute(
                    "SELECT rowid AS __rowid__, result_data FROM reports WHERE id=?",
                    (report_id,)).fetchone()
                if row is None:
                    raise RuntimeError(
                        f"planned row {report_id} vanished before pre-export")
                raw_text = row["result_data"]
                sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                if sha != item["sha256"]:
                    raise RuntimeError(
                        f"pre-export drift on {report_id}: row changed between "
                        "plan pass and export; refusing to write")
                records[report_id] = sha
                emit({
                    "kind": "row", "report_id": report_id,
                    "rowid": row["__rowid__"],
                    "result_data_sha256": sha,
                    "result_data": raw_text,
                })
        emit({"kind": "trailer", "row_count": len(records)})
        stream.close(); stream = None
        raw.flush(); os.fsync(raw.fileno()); raw.close(); raw = None
        file_sha = hashing.sha.hexdigest()
    except Exception:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if raw is not None:
            raw.close()
        try:
            path.unlink()
        except OSError:
            pass
        raise
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual != file_sha:
        try:
            path.unlink()
        except OSError:
            pass
        raise RuntimeError("pre-export sha256 mismatch; refusing to write")
    return {"path": str(path), "sha256": file_sha, "records": records}


def _load_pre_export(path, expected_sha256=None):
    """Decompress a pre-export file, verifying sha256 and the row trailer."""
    blob = Path(path).read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    if expected_sha256 is not None and sha != expected_sha256:
        raise RuntimeError("pre-export sha256 mismatch; refusing to restore")
    records = []
    text = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(blob)).read().decode("utf-8")
    row_count = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("kind") == "row":
            records.append(obj)
        elif obj.get("kind") == "trailer":
            row_count = obj.get("row_count")
    if row_count is None or row_count != len(records):
        raise RuntimeError("pre-export trailer missing or row_count mismatch")
    return {"sha256": sha, "records": records}


# ---------------------------------------------------------------------------
# write + restore


def _write_row(conn, report_id, audit, exported_sha=None):
    """Convert one row inside its own transaction.

    exported_sha (sha256 of the pre-exported result_data) is the drift
    witness: a row whose current bytes no longer match its export is
    skipped as "pre_export_mismatch" — someone else touched it between
    export and write. The new bytes are computed from the *current* stored
    text inside the transaction, so the write can never silently depend on
    stale pass-1 state.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT result_data FROM reports WHERE id=? AND status='completed'",
            (report_id,)).fetchone()
        if row is None:
            conn.rollback()
            return "missing"
        original = row[0]
        if exported_sha is not None:
            current_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
            if current_sha != exported_sha:
                conn.rollback()
                return "pre_export_mismatch"
        new_text = convert_legacy_text(original)
        if original == new_text:
            conn.rollback()
            return "unchanged"
        before_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
        after_sha = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
        _journal_emit(audit, {"phase": "prepared", "report_id": report_id,
                              "before_sha256": before_sha, "after_sha256": after_sha})
        conn.execute("UPDATE reports SET result_data=? WHERE id=?",
                     (new_text, report_id))
        stored = conn.execute(
            "SELECT result_data FROM reports WHERE id=?", (report_id,)).fetchone()[0]
        if stored != new_text:
            raise ValueError("stored bytes differ from converted bytes")
        # 卡面硬验收: compat-layer read must be verbatim identical.
        if _view_bytes(stored) != _view_bytes(original):
            raise ValueError("post-write compat view mismatch")
        if not _witness_equal(stored, original):
            raise ValueError("post-write non-alias byte mismatch")
        conn.commit()
        _journal_emit(audit, {"phase": "committed", "report_id": report_id,
                              "after_sha256": after_sha})
        return "converted"
    except Exception:
        conn.rollback()
        raise


def restore_from_export(db_path, export_file, *, sha256=None,
                        health_url="http://127.0.0.1:8000",
                        production_authorized=False):
    """Rollback subcommand: write exported pre-images back, row-by-row verified."""
    _validate_db(db_path)
    if _is_production(db_path) and (not production_authorized
                                    or now_cn().date() <= date(2026, 10, 9)):
        raise RuntimeError("production restore forbidden before signed post-10-09 handoff")
    export = _load_pre_export(export_file, expected_sha256=sha256)
    guard = check_runtime_guard(db_path, health_url)
    if not guard["allowed"]:
        result = {"skipped": True, "guard": guard, "restored_rows": 0}
        print(json.dumps(result, ensure_ascii=False))
        return result
    result = {"restored_rows": 0, "already_current": 0, "missing": 0,
              "mismatched": 0, "conflicts": 0, "guard": guard}
    conn = _open_db(db_path, writable=True)
    try:
        for record in export["records"]:
            report_id = record["report_id"]
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT result_data FROM reports WHERE id=? AND status='completed'",
                    (report_id,)).fetchone()
                if row is None:
                    conn.rollback()
                    result["missing"] += 1
                    continue
                current = row[0]
                if current == record["result_data"]:
                    conn.rollback()
                    result["already_current"] += 1
                    continue
                conn.execute("UPDATE reports SET result_data=? WHERE id=?",
                             (record["result_data"], report_id))
                stored = conn.execute(
                    "SELECT result_data FROM reports WHERE id=?", (report_id,)).fetchone()[0]
                if stored != record["result_data"]:
                    raise ValueError("post-restore verification failed")
                conn.commit()
                result["restored_rows"] += 1
            except sqlite3.OperationalError:
                conn.rollback()
                result["conflicts"] += 1
            except Exception:
                conn.rollback()
                result["mismatched"] += 1
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ---------------------------------------------------------------------------
# VACUUM INTO + verification


def vacuum_into(db_path, out_path):
    """Create a slimmed copy of `db_path` at `out_path` via VACUUM INTO.

    Memory discipline (总控 ruling, footprint <= 4 GiB): VACUUM INTO on a
    multi-GB DB can hold temp pages in memory and balloon footprint, so
    temp storage is forced to FILE, mmap disabled, and the page cache
    capped at 256 MB before the vacuum. temp files land where SQLite's
    own resolution puts them (TMPDIR / sqlite temp dir) — caller sets
    TMPDIR to a volume with headroom.
    """
    p = Path(out_path).resolve()
    if p.exists():
        raise RuntimeError(f"vacuum target already exists: {p}")
    escaped = str(p).replace("'", "''")
    with closing(_open_db(db_path, writable=True)) as conn:
        conn.execute("PRAGMA temp_store=FILE")
        conn.execute("PRAGMA mmap_size=0")
        conn.execute("PRAGMA cache_size=-262144")  # 256 MB page cache cap
        conn.execute(f"VACUUM INTO '{escaped}'")
    return str(p)


def verify_db_pair(old_path, new_path):
    """Post-VACUUM verification: integrity + row counts + per-row
    compat-view equality between source and slimmed databases.

    Streams the source row-by-row and does a keyed read on the slimmed
    side — neither table is materialized in memory (the prior version
    loaded both `result_data` columns fully, ~8 GB, blowing the footprint
    red line). A missing id is re-confirmed by a direct keyed read.
    """
    result = {"old_rows": 0, "new_rows": 0, "view_mismatches": 0,
              "missing_in_new": 0, "integrity": None}
    with closing(_open_db(new_path)) as conn:
        result["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        result["new_rows"] = conn.execute(
            "SELECT count(*) FROM reports").fetchone()[0]

    missing_ids = []
    with closing(_open_db(old_path)) as oconn, \
            closing(_open_db(new_path)) as nconn:
        for row in oconn.execute("SELECT id, result_data FROM reports"):
            result["old_rows"] += 1
            rid, old_text = row["id"], row["result_data"]
            nrow = nconn.execute(
                "SELECT result_data FROM reports WHERE id=?",
                (rid,)).fetchone()
            if nrow is None:
                missing_ids.append(rid)
                continue
            new_text = nrow["result_data"]
            try:
                old_view = None if old_text is None else _view_bytes(old_text)
                new_view = None if new_text is None else _view_bytes(new_text)
                if old_view != new_view:
                    result["view_mismatches"] += 1
            except Exception:
                result["view_mismatches"] += 1
    # Direct keyed re-check for ids absent from the new table: rules out
    # snapshot artifacts before flagging a genuine missing row.
    if missing_ids:
        still_missing = []
        with closing(_open_db(new_path)) as conn:
            for rid in missing_ids:
                n = conn.execute(
                    "SELECT count(*) FROM reports WHERE id=?", (rid,)).fetchone()[0]
                if n == 0:
                    still_missing.append(rid)
        missing_ids = still_missing
    result["missing_in_new"] = len(missing_ids)
    return result


# ---------------------------------------------------------------------------
# main conversion run


def run_slim(*, db_path, batch_size=200, batch_byte_limit=512 * 1024 * 1024,
             journal_path=None, exceptions_path=None,
             health_url="http://127.0.0.1:8000", production_authorized=False,
             copy_rehearsal=False, pre_export_dir=None, dry_run=False,
             scan_only=False):
    """Convert legacy rows in `db_path` to canonical v1.

    Batched: every batch exports pre-images first, then writes inside
    per-row transactions, then journals each committed id. Interrupting at
    any point leaves a consistent DB; re-running skips committed ids.
    """
    _validate_db(db_path)
    production = _is_production(db_path)
    if production and (not production_authorized
                       or now_cn().date() <= date(2026, 10, 9)):
        raise RuntimeError("production write forbidden before signed post-10-09 handoff")
    if not dry_run and not scan_only and (production or not copy_rehearsal):
        # Same runtime guard as the backfill: never write while the service
        # may be touching the database.
        guard = check_runtime_guard(db_path, health_url)
        if not guard["allowed"]:
            result = {"skipped": True, "guard": guard, "converted_rows": 0}
            print(json.dumps(result, ensure_ascii=False))
            return result

    journal_path = Path(journal_path or JOURNAL_DIR /
                        (datetime.now().strftime("%Y%m%dT%H%M%S") + ".jsonl"))
    exceptions_path = Path(exceptions_path or
                           journal_path.with_name(journal_path.stem + "-exceptions.json"))
    committed_ids = load_committed_ids(journal_path)

    result = {"dry_run": dry_run, "scan_only": scan_only,
              "scanned": 0, "already_canonical": 0, "resumed_skipped": 0,
              "non_dict": 0, "no_alias_content": 0, "candidates": 0,
              "top_mdc_sole_copy": 0, "top_mdc_rebuildable": 0,
              "converted_rows": 0, "unchanged": 0, "pre_export_mismatch": 0,
              "conflicts": 0, "verify_failures": 0, "missing": 0,
              "write_conflicts": 0, "batches": 0, "pre_export_files": []}
    exceptions = []

    # Pass 1 runs in a *subprocess*: parsing ~5 GB of JSON peaks the
    # process heap (CPython retains freed arenas), and that peak would
    # otherwise persist into the write phase — the very red line this
    # migration must stay under. A subprocess (not fork) starts with a
    # fresh heap, so the parent's peak stays at the write phase only. The
    # child writes its small result (planned ids + counters + exceptions)
    # to a temp JSON file. See _scan_pass for the classification rules.
    # Temp file lands beside the journal/exceptions outputs: that
    # directory is created here (DAV-1636 🔴-1 — JOURNAL_DIR is untracked
    # and absent in clean checkouts, so mkstemp into it crashed every
    # run_slim call with FileNotFoundError).
    import subprocess
    import tempfile
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    fd, scan_path = tempfile.mkstemp(
        prefix="dav1614-scan-", suffix=".json", dir=str(journal_path.parent))
    os.close(fd)
    child_code = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "from scripts import slim_result_data_storage as slim\n"
        "result = {\"scanned\":0,\"already_canonical\":0,\"resumed_skipped\":0,"
        "\"non_dict\":0,\"no_alias_content\":0,\"candidates\":0,\"conflicts\":0,"
        "\"top_mdc_sole_copy\":0,\"top_mdc_rebuildable\":0}\n"
        "exc = []\n"
        "planned = slim._scan_pass(%r, set(%r), result, exc)\n"
        "json.dump({\"result\":result,\"exceptions\":exc,\"planned\":planned},"
        " open(%r,'w'))\n"
    ) % (str(ROOT), str(db_path), sorted(committed_ids), scan_path)
    proc = subprocess.run(
        [sys.executable, "-c", child_code],
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        capture_output=True, text=True)
    if proc.returncode != 0 or not Path(scan_path).exists() \
            or Path(scan_path).stat().st_size == 0:
        raise RuntimeError(
            "scan-pass subprocess failed: " + (proc.stderr or "<no stderr>")[-500:])
    scan_out = json.loads(Path(scan_path).read_text(encoding="utf-8"))
    result.update(scan_out["result"])
    exceptions = scan_out["exceptions"]
    planned = scan_out["planned"]
    try:
        Path(scan_path).unlink()
    except OSError:
        pass

    # An explicit --exceptions-path may point at a directory that differs
    # from the journal's and does not exist yet.
    exceptions_path.parent.mkdir(parents=True, exist_ok=True)
    exceptions_path.write_text(
        json.dumps({"db_path": str(db_path), "generated_at": datetime.now().isoformat(),
                    "exceptions": exceptions}, ensure_ascii=False, indent=2))
    result["exceptions_file"] = str(exceptions_path)
    result["exceptions"] = len(exceptions)

    if scan_only or dry_run or not planned:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


    # Pass 2: batch -> independent pre-export -> per-row transactions ->
    # journal each committed id (resume skips them next run). Batches are
    # split by BOTH row count and cumulative raw bytes: the export stream
    # buffers each row's raw text into the compressed file, so a count-only
    # cap can hold ~1-3 GB resident when rows are large — a real path over
    # the 4 GB peak-memory red line (DAV-1631 🟡-1). `batch_byte_limit`
    # bounds each export batch by bytes.
    audit = _open_journal(journal_path)
    try:
        batch = []
        batch_bytes = 0

        def flush():
            nonlocal batch, batch_bytes
            if not batch:
                return
            pre_export = export_pre_images(db_path, batch, export_dir=pre_export_dir)
            if set(pre_export["records"]) != {item["id"] for item in batch}:
                raise RuntimeError("pre-export report set mismatch; refusing to write")
            result["pre_export_files"].append(
                {"path": pre_export["path"], "sha256": pre_export["sha256"],
                 "rows": len(pre_export["records"])})
            conn = _open_db(db_path, writable=True)
            try:
                for item in batch:
                    try:
                        outcome = _write_row(
                            conn, item["id"], audit,
                            exported_sha=pre_export["records"].get(item["id"]))
                    except (StorageCompatConflict, ValueError) as exc:
                        result["verify_failures"] += 1
                        exceptions.append(
                            {"report_id": item["id"], "phase": "write",
                             "conflicts": getattr(exc, "conflicts", [str(exc)])})
                        _journal_emit(audit, {"event": "verify_failure",
                                              "report_id": item["id"],
                                              "error": str(exc)[:500]})
                        continue
                    except sqlite3.OperationalError:
                        result["write_conflicts"] += 1
                        continue
                    key = {"converted": "converted_rows",
                           "unchanged": "unchanged",
                           "pre_export_mismatch": "pre_export_mismatch",
                           "missing": "missing"}[outcome]
                    result[key] += 1
                    _journal_emit(audit, {"event": outcome, "report_id": item["id"]})
            finally:
                conn.close()
            result["batches"] += 1
            batch = []
            batch_bytes = 0

        for item in planned:
            # Split a batch when it reaches the row cap OR would exceed the
            # cumulative raw-byte cap (item["bytes"] recorded in pass 1).
            if batch and (len(batch) >= batch_size
                          or batch_bytes + item["bytes"] > batch_byte_limit):
                flush()
            batch.append(item)
            batch_bytes += item["bytes"]
        flush()
    finally:
        audit.close()

    # Rewrite the exceptions file including write-phase failures.
    exceptions_path.write_text(
        json.dumps({"db_path": str(db_path), "generated_at": datetime.now().isoformat(),
                    "exceptions": exceptions}, ensure_ascii=False, indent=2))
    result["exceptions"] = len(exceptions)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _scan_pass(db_path, committed_ids, result, exceptions):
    """Pass 1 (read-only): classify every completed row, return the planned
    write list. Runs convert_legacy_text per row as a convertibility probe;
    bytes are re-derived inside each write transaction (never from pass-1
    state).

    NOTE (memory): pass 1 parses ~5 GB of JSON; CPython's allocator keeps
    freed arenas, so the parsing peak is process-wide and does not drop
    after the rows are released. To keep the write phase under the 4 GB
    peak-memory red line, run_slim executes this pass in a forked child
    that only returns the small planned-id list — the child's peak dies
    with it. See run_slim."""
    planned = []
    for row in _iter_db_raw(db_path):
        result["scanned"] += 1
        report_id, raw = row["id"], row["raw"]
        if report_id in committed_ids:
            result["resumed_skipped"] += 1
            continue
        if raw is None:
            result["non_dict"] += 1
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            result["non_dict"] += 1
            exceptions.append({"report_id": report_id,
                               "conflicts": ["result_data is not valid JSON"]})
            continue
        if not isinstance(value, dict):
            result["non_dict"] += 1
            continue
        if is_canonical_storage(value):
            result["already_canonical"] += 1
            continue
        if not isinstance(value.get("horizons"), dict):
            # Only rows that physically carry the legacy ``horizons`` map
            # are conversion candidates. ``*_term``-only rows are already
            # single-write; stamping them v1 would change their compat read
            # (a ``horizons`` key would be added) — out of B-4 scope.
            result["no_alias_content"] += 1
            # 总控 ruling: report the residual top-level mdc rows by the
            # two classes — rows whose top-mdc could be rebuilt from a
            # ``*_term`` slice's own mdc (needs the alias mask keys, a
            # compat-layer decision deferred to DAV-1615) vs rows where
            # the top-level mdc is the sole authoritative copy (deleting
            # it would lose data).
            if isinstance(value.get("market_data_context"), dict):
                if any(isinstance(value.get(k), dict)
                       and isinstance(value[k].get("market_data_context"), dict)
                       for k in value if k.endswith("_term")):
                    result["top_mdc_rebuildable"] += 1
                else:
                    result["top_mdc_sole_copy"] += 1
            continue
        try:
            convert_legacy_text(raw)
        except (StorageCompatConflict, ValueError) as exc:
            result["conflicts"] += 1
            exceptions.append({"report_id": report_id,
                               "conflicts": getattr(exc, "conflicts", [str(exc)])})
            continue
        planned.append({"id": report_id,
                        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                        "bytes": len(raw.encode("utf-8"))})
        result["candidates"] += 1
    return planned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--batch-byte-limit", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--journal-path")
    parser.add_argument("--exceptions-path")
    parser.add_argument("--pre-export-dir")
    parser.add_argument("--health-url", default="http://127.0.0.1:8000")
    parser.add_argument("--vacuum-into")
    for name in ("dry-run", "scan-only", "production-authorized",
                 "copy-rehearsal"):
        parser.add_argument("--" + name, action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=False)
    restore_parser = sub.add_parser(
        "restore", help="restore rows from an independent pre-export file")
    restore_parser.add_argument("--db-path", required=True)
    restore_parser.add_argument("--export-file", required=True)
    restore_parser.add_argument("--export-sha256")
    restore_parser.add_argument("--health-url", default="http://127.0.0.1:8000")
    restore_parser.add_argument("--production-authorized", action="store_true")
    verify_parser = sub.add_parser(
        "verify", help="verify a VACUUMed db against its source")
    verify_parser.add_argument("--old-path", required=True)
    verify_parser.add_argument("--new-path", required=True)
    args = vars(parser.parse_args())
    command = args.pop("command")
    args.pop("verbose")
    try:
        if command == "restore":
            result = restore_from_export(
                args["db_path"], args["export_file"],
                sha256=args.get("export_sha256"), health_url=args["health_url"],
                production_authorized=args.get("production_authorized", False))
            if result.get("mismatched") or result.get("conflicts") or result.get("missing"):
                return 1
            return 0
        if command == "verify":
            result = verify_db_pair(args["old_path"], args["new_path"])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result["integrity"] != "ok" or result["old_rows"] != result["new_rows"] \
                    or result["view_mismatches"] or result["missing_in_new"]:
                return 1
            return 0
        vacuum_target = args.pop("vacuum_into")
        result = run_slim(**args)
        if vacuum_target:
            new_db = vacuum_into(args["db_path"], vacuum_target)
            check = verify_db_pair(args["db_path"], new_db)
            print(json.dumps({"vacuum_into": new_db, "verify": check},
                             ensure_ascii=False, indent=2))
            if check["integrity"] != "ok" or check["view_mismatches"] \
                    or check["missing_in_new"] or check["old_rows"] != check["new_rows"]:
                return 1
        if result.get("verify_failures") or result.get("write_conflicts"):
            return 1
    except Exception as exc:
        print("Slim refused: " + type(exc).__name__ + " " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
