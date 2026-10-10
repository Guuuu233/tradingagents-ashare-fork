"""DAV-1770 B-6b: zstd-9 compressed storage for ``reports.result_data``.

Physical layout (design ``docs/dav1770-b6-compression-design.md`` §2/§3):

* ``result_data_zst``     (BLOB) — zstd-9 compressed UTF-8 JSON bytes, no
  dict, one-shot ``compress()`` so every frame carries a content-size
  header (FCS).
* ``result_data_zst_len`` (INT)  — uncompressed byte length (audit only;
  decode reads the frame's own FCS).
* ``result_data``         (JSON) — plaintext column, still written on every
  ORM write during the P1–P4 dual-write window (fallback insurance). In
  ``compressed`` mode it is mapped as the *deferred* ORM attribute
  ``result_data_plaintext`` — never selected by any read path unless
  explicitly undefer()ed (design §3.1 fallback-mapping clause).
* 31 ``pg_*`` columns            — materialized list-page scalars populated
  on every result_data write (§2.2).

``REPORT_STORAGE_MODE`` (read selector, §4/§6 — restart-time switch):

* ``plaintext``  (default): ``ReportDB.result_data`` maps the plaintext
  JSON column — byte-identical pre-compression behaviour (P0/P1).
* ``compressed`` (P3): ``ReportDB.result_data`` maps the BLOB column via
  :class:`ZstdJSON` (``Column('result_data_zst', ZstdJSON,
  key='result_data')``). ``dual`` aliases to ``compressed``.
* Instant rollback: set back to ``plaintext`` — the shadow write has kept
  the plaintext column byte-identical to the decoded bytes on every write.

Write path (all modes): a ``before_flush`` hook on the ``result_data``
attribute history (:func:`sync_report_storage_row`) keeps the non-read-side
physical column (``result_data_zst`` + ``result_data_zst_len`` in plaintext
mode; the deferred ``result_data_plaintext`` shadow + ``result_data_zst_len``
in compressed mode) and the 31 ``pg_*`` columns in sync on every INSERT /
UPDATE where ``result_data`` changed. Compressed bytes come from
:func:`encode_result_data`, which serializes via ``json.dumps`` with default
parameters — identical to what SQLAlchemy ``Column(JSON)`` stores, so
``decompress(zst) == plaintext_bytes`` holds and the sealed
``result_data_sha256_at_seal`` ledger chain stays verifiable (§3.3/§8.4).
Compression is concurrency-capped — 总控裁定③ (10-10):
``REPORT_ZSTD_WRITE_CONCURRENCY`` default 4.

Thread safety (DAV-1776 🔴-1): ``ZstdCompressor``/``ZstdDecompressor``
instances are NOT thread safe — per-thread contexts via
``threading.local()``; never share one across threads.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Optional

import zstandard
from sqlalchemy.types import LargeBinary, TypeDecorator

logger = logging.getLogger(__name__)

# ── Env / tunables ───────────────────────────────────────────────────────────

_STORAGE_MODE_ENV = "REPORT_STORAGE_MODE"
_WRITE_CONCURRENCY_ENV = "REPORT_ZSTD_WRITE_CONCURRENCY"
_VALID_MODES = {"plaintext", "dual", "compressed"}

DEFAULT_WRITE_CONCURRENCY = 4
# Output cap used ONLY for frames lacking a content-size header (streamed
# frames — one-shot compress() frames always carry FCS). 64 MiB ≈ 4×
# headroom over the observed p100 row (13 MiB). Not a memory bound: on
# FCS frames decompress allocates the declared size (§3.1 note).
MAX_RESULT_DATA_BYTES = 64 * 1024 * 1024

COMPRESSED_COL = "result_data_zst"
PLAINTEXT_COL = "result_data"
ZST_LEN_COL = "result_data_zst_len"

ZSTD_LEVEL = 9


def report_storage_mode() -> str:
    """Read mode from ``REPORT_STORAGE_MODE`` (``dual`` → ``compressed``).

    Evaluated once per process (env is a restart-time switch); the result
    decides which physical column the ``result_data`` ORM attribute binds
    at class-definition time.
    """
    mode = os.getenv(_STORAGE_MODE_ENV, "plaintext").strip().lower()
    if mode not in _VALID_MODES:
        logger.warning(
            "[compressed_json] unknown %s=%r; treating as 'plaintext'",
            _STORAGE_MODE_ENV, mode,
        )
        return "plaintext"
    return "compressed" if mode == "dual" else mode


def _env_concurrency() -> int:
    raw = os.getenv(_WRITE_CONCURRENCY_ENV, "").strip()
    if not raw:
        return DEFAULT_WRITE_CONCURRENCY
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(
            "[compressed_json] bad %s=%r; using %d",
            _WRITE_CONCURRENCY_ENV, raw, DEFAULT_WRITE_CONCURRENCY,
        )
        return DEFAULT_WRITE_CONCURRENCY


_write_semaphore = threading.BoundedSemaphore(_env_concurrency())


def write_concurrency_limit() -> int:
    """Configured compression concurrency cap (env)."""
    return _env_concurrency()


def set_write_concurrency_limit(n: int) -> int:
    """@internal/test-only — rebuild the write semaphore at capacity ``n``.

    Production code must use the ``REPORT_ZSTD_WRITE_CONCURRENCY`` env at
    process start; this hook exists for tests asserting the cap. Not safe
    to call while a compression is in-flight.
    """
    global _write_semaphore
    n = max(1, int(n))
    _write_semaphore = threading.BoundedSemaphore(n)
    return n


# ── Per-thread zstd contexts (DAV-1776: instances are NOT thread safe) ───────

_tls = threading.local()


def _cctx() -> zstandard.ZstdCompressor:
    ctx = getattr(_tls, "cctx", None)
    if ctx is None:
        ctx = zstandard.ZstdCompressor(level=ZSTD_LEVEL)
        _tls.cctx = ctx
    return ctx


def _dctx() -> zstandard.ZstdDecompressor:
    ctx = getattr(_tls, "dctx", None)
    if ctx is None:
        ctx = zstandard.ZstdDecompressor()
        _tls.dctx = ctx
    return ctx


# ── Raw encode/decode helpers ────────────────────────────────────────────────


def compress_json_bytes(raw: bytes) -> bytes:
    """Compress already-serialized JSON bytes under the write semaphore.

    Same operation B-6c performs for the 原始字节 migration (总控裁定②):
    ``zst = compress(stored_bytes)`` with ``decompress(zst) == stored``.
    """
    with _write_semaphore:
        return _cctx().compress(raw)


def encode_result_data(value: Any) -> Optional[bytes]:
    """dict → zstd-9 bytes; ``None`` → ``None``.

    ``json.dumps`` default parameters — byte-identical to what SQLAlchemy
    ``Column(JSON)`` writes to the plaintext column, so
    ``decompress(encode(v)) == stored_plaintext_bytes`` (§3.3/§8.4).
    """
    if value is None:
        return None
    return compress_json_bytes(json.dumps(value).encode("utf-8"))


def result_data_plaintext_bytes(value: Any) -> Optional[bytes]:
    """dict → the exact UTF-8 bytes the plaintext column stores (None→None)."""
    if value is None:
        return None
    return json.dumps(value).encode("utf-8")


def decode_frame_bytes(zst: bytes) -> bytes:
    """Compressed frame → raw serialized JSON bytes (FCS-sized)."""
    data = bytes(zst)
    n = zstandard.frame_content_size(data)
    if n < 0:
        n = MAX_RESULT_DATA_BYTES
    return _dctx().decompress(data, max_output_size=n)


def decode_result_data(raw: Any) -> Optional[dict]:
    """bytes / str / None → dict (or ``None``).

    * compressed BLOB   → decompress + ``json.loads``
    * plaintext JSON    → ``json.loads`` (legacy rows / raw-SQL callers)
    * ``'null'``/``None``/empty → ``None`` — matches
      ``json.loads('null') is None`` for the 318 non-migrated rows (§2.1)
    * already-parsed    → returned unchanged (defensive)
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        data = bytes(raw)
        if not data:
            return None
        return json.loads(decode_frame_bytes(data))
    if isinstance(raw, str):
        if not raw:
            return None
        return json.loads(raw)
    return raw


def frame_content_len(zst: bytes) -> int:
    """Uncompressed length from the frame header (FCS); -1 when absent."""
    return int(zstandard.frame_content_size(bytes(zst)))


def reports_has_compressed_columns(con) -> bool:
    """True when the ``reports`` table physically carries the B-6b columns.

    ``con`` may be a raw ``sqlite3.Connection`` or a SQLAlchemy
    ``Connection``. Used by raw-SQL callers to decide between
    ``COALESCE(result_data_zst, result_data)`` and plain ``result_data`` on
    pre-migration schemas (P0 test fixtures).
    """
    try:
        if hasattr(con, "execute"):
            rows = con.execute("PRAGMA table_info(reports)")
            names = {r[1] for r in rows}
        else:
            return False
    except Exception:
        return False
    return COMPRESSED_COL in names


def result_data_select_expr(con) -> str:
    """SQL fragment yielding the JSON payload (compressed preferred).

    Emits ``COALESCE(result_data_zst, result_data)`` when the zst column
    physically exists, else bare ``result_data`` — identical output on both
    schemas for raw-SQL callers.
    """
    if reports_has_compressed_columns(con):
        return f"COALESCE({COMPRESSED_COL}, {PLAINTEXT_COL})"
    return PLAINTEXT_COL


# ── SQLAlchemy type ──────────────────────────────────────────────────────────


class ZstdJSON(TypeDecorator):
    """BLOB type transparently exposing a decoded dict.

    Bound to physical ``result_data_zst`` under the public attribute name
    via ``Column('result_data_zst', ZstdJSON, key='result_data')``. Bind
    compresses under the write semaphore (裁定③); result decodes by FCS.
    """

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encode_result_data(value)

    def process_result_value(self, value, dialect):
        return decode_result_data(value)


# ── Post-gate materialized columns (§2.2) ────────────────────────────────────

# Fragment slots fed by exactly one physical column each. The two
# manager-verdict json paths merge into one column per side at write time
# (the fragment reader ORs them — merged single column is lossless, §2.2).
PG_FRAGMENT_COLUMN_MAP: dict = {
    "st_trade_action": "pg_st_trade_action",
    "st_analysis_status": "pg_st_analysis_status",
    "st_risk_status": "pg_st_risk_status",
    "st_direction": "pg_st_direction",
    "st_decision_status": "pg_st_decision_status",
    "st_reason_codes": "pg_st_reason_codes",
    "st_gate_status": "pg_st_gate_status",
    "st_status": "pg_st_status",
    "st_confidence": "pg_st_confidence",
    "st_target_price": "pg_st_target_price",
    "st_stop_loss_price": "pg_st_stop_loss_price",
    "st_pre_gate_action": "pg_st_pre_gate_action",
    "st_manager_action": "pg_st_manager_action",
    "st_ids_manager_action": "pg_st_manager_action",
    "mt_trade_action": "pg_mt_trade_action",
    "mt_analysis_status": "pg_mt_analysis_status",
    "mt_risk_status": "pg_mt_risk_status",
    "mt_direction": "pg_mt_direction",
    "mt_decision_status": "pg_mt_decision_status",
    "mt_reason_codes": "pg_mt_reason_codes",
    "mt_gate_status": "pg_mt_gate_status",
    "mt_status": "pg_mt_status",
    "mt_confidence": "pg_mt_confidence",
    "mt_target_price": "pg_mt_target_price",
    "mt_stop_loss_price": "pg_mt_stop_loss_price",
    "mt_pre_gate_action": "pg_mt_pre_gate_action",
    "mt_manager_action": "pg_mt_manager_action",
    "mt_ids_manager_action": "pg_mt_manager_action",
    "top_ds_reason_codes": "pg_top_reason_codes",
    "top_reason_codes": "pg_top_reason_codes",
    "confidence": "pg_confidence",
    "probability": "pg_probability",
    "target_price": "pg_target_price",
    "stop_loss_price": "pg_stop_loss_price",
}

# Ordered list of the 31 distinct physical pg_* columns.
PG_PHYSICAL_COLUMNS: tuple = tuple(dict.fromkeys(PG_FRAGMENT_COLUMN_MAP.values()))

# DDL type per column for _ensure_report_schema (§2.2 table).
PG_COLUMN_DDL: dict = {
    "pg_st_trade_action": "VARCHAR(32)",
    "pg_st_analysis_status": "VARCHAR(32)",
    "pg_st_risk_status": "VARCHAR(32)",
    "pg_st_direction": "VARCHAR(16)",
    "pg_st_decision_status": "JSON",
    "pg_st_reason_codes": "JSON",
    "pg_st_gate_status": "VARCHAR(32)",
    "pg_st_status": "VARCHAR(20)",
    "pg_st_confidence": "INTEGER",
    "pg_st_target_price": "FLOAT",
    "pg_st_stop_loss_price": "FLOAT",
    "pg_st_pre_gate_action": "VARCHAR(32)",
    "pg_st_manager_action": "VARCHAR(32)",
    "pg_mt_trade_action": "VARCHAR(32)",
    "pg_mt_analysis_status": "VARCHAR(32)",
    "pg_mt_risk_status": "VARCHAR(32)",
    "pg_mt_direction": "VARCHAR(16)",
    "pg_mt_decision_status": "JSON",
    "pg_mt_reason_codes": "JSON",
    "pg_mt_gate_status": "VARCHAR(32)",
    "pg_mt_status": "VARCHAR(20)",
    "pg_mt_confidence": "INTEGER",
    "pg_mt_target_price": "FLOAT",
    "pg_mt_stop_loss_price": "FLOAT",
    "pg_mt_pre_gate_action": "VARCHAR(32)",
    "pg_mt_manager_action": "VARCHAR(32)",
    "pg_top_reason_codes": "JSON",
    "pg_confidence": "INTEGER",
    "pg_probability": "FLOAT",
    "pg_target_price": "FLOAT",
    "pg_stop_loss_price": "FLOAT",
}

_PG_SLICE_FIELDS = (
    "trade_action", "analysis_status", "risk_status", "direction",
    "decision_status", "reason_codes", "status", "confidence",
    "target_price", "stop_loss_price",
)


def _get_path(d: Any, *keys: str) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _first(*vals: Any) -> Any:
    for v in vals:
        if v is not None:
            return v
    return None


def post_gate_column_values(result_data: Any) -> dict:
    """``result_data`` dict → ``{physical_col: value}`` for the 31 pg_* cols.

    Pure function — reused verbatim by the B-6c backfill (§5). Absent paths
    produce ``None`` so updates also *clear* stale materialized values.
    """
    rd = result_data if isinstance(result_data, dict) else {}
    out: dict = {}

    for prefix, slice_key in (("st", "short_term"), ("mt", "medium_term")):
        sl = rd.get(slice_key)
        sl = sl if isinstance(sl, dict) else {}
        for field in _PG_SLICE_FIELDS:
            out[f"pg_{prefix}_{field}"] = sl.get(field)
        # pre_gate_trade_action lives under the ``pre_gate_trade_action``
        # json path (``$.<h>_term.pre_gate_trade_action``) — same name, but
        # must be emitted as its own key because the loop above only maps
        # fields whose column name is ``pg_<h>_<field>``.
        out[f"pg_{prefix}_pre_gate_action"] = sl.get("pre_gate_trade_action")
        gate = sl.get("price_basis_gate")
        out[f"pg_{prefix}_gate_status"] = (
            gate.get("status") if isinstance(gate, dict) else None
        )
        out[f"pg_{prefix}_manager_action"] = _first(
            _get_path(sl, "manager_verdict", "trade_action"),
            _get_path(sl, "investment_debate_state", "manager_verdict", "trade_action"),
        )

    # top-level reason_codes: two source json paths share one column.
    # Merge order (DAV-1792 🟢-1): ``decision_status.reason_codes`` wins
    # when both paths are non-empty — it is the post-gate adjudicated set;
    # bare ``reason_codes`` is the fallback/legacy slot.
    out["pg_top_reason_codes"] = _first(
        _get_path(rd, "decision_status", "reason_codes"),
        rd.get("reason_codes"),
    )
    out["pg_confidence"] = rd.get("confidence")
    out["pg_probability"] = rd.get("probability")
    out["pg_target_price"] = rd.get("target_price")
    out["pg_stop_loss_price"] = rd.get("stop_loss_price")
    return out


def populate_post_gate_columns(report: Any, result_data: Any) -> None:
    """Assign the 31 pg_* columns on a ``ReportDB`` row from ``result_data``.

    Called from the flush hook so it always sees the final persisted value
    (§8.1 — must run after all in-place ensure_* mutators).
    """
    for col_name, value in post_gate_column_values(result_data).items():
        setattr(report, col_name, value)


def _compressed_columns_present(report: Any) -> bool:
    """True when the ORM can see the new physical columns.

    Guards the shadow sync so an un-migrated schema (P0) still writes — the
    ``pg_*``/``result_data_zst*`` attributes simply do not exist on the
    mapping there.
    """
    return hasattr(type(report), ZST_LEN_COL)


def sync_report_storage_row(report: Any, *, is_pending: bool = False) -> None:
    """Dual-write hook: populate both storage columns + pg_* columns.

    Called from ``before_insert``/``before_update`` mapper events. On
    UPDATE it only acts when the ``result_data`` attribute history shows
    changes — **the 31 ``pg_*`` materialized columns and ``zst_len`` are
    refreshed exclusively on ``result_data`` writes**, never on unrelated
    field updates (DAV-1792 🟢-3). On INSERT it always runs.
    """
    if not _compressed_columns_present(report):
        return
    if not is_pending:
        from sqlalchemy import inspect as sa_inspect
        try:
            hist = sa_inspect(report).attrs["result_data"].history
        except Exception:
            return
        if not hist.has_changes():
            return

    value = getattr(report, "result_data", None)
    raw_bytes = result_data_plaintext_bytes(value)
    mode = report_storage_mode()

    # Both physical write columns are always populated on every
    # result_data change — the read side never depends on which one it
    # reads (coalesce / plaintext).
    if hasattr(type(report), "result_data_plaintext"):
        # Deferred JSON attr — accepts dict or text; writes identical
        # json.dumps bytes → decompress(zst) == stored plaintext.
        report.result_data_plaintext = value
    if hasattr(type(report), "result_data_zst"):
        # Deferred raw BLOB attr — stores compressed bytes directly.
        report.result_data_zst = (
            compress_json_bytes(raw_bytes) if raw_bytes is not None else None
        )
    report.result_data_zst_len = len(raw_bytes) if raw_bytes is not None else None
    populate_post_gate_columns(report, value)


# ── DDL description for _ensure_report_schema (§2.4) ─────────────────────────

# physical column → DDL fragment (dialect-agnostic: BLOB maps to BLOB on
# SQLite and must be translated by the caller for PG/MySQL).
B6B_COLUMN_DDL: dict = {
    COMPRESSED_COL: "BLOB",
    ZST_LEN_COL: "INTEGER",
    **PG_COLUMN_DDL,
}

# Ordered column list appended to _ensure_report_schema's ordered_columns.
B6B_ORDERED_COLUMNS: tuple = (COMPRESSED_COL, ZST_LEN_COL) + PG_PHYSICAL_COLUMNS


__all__ = [
    "COMPRESSED_COL",
    "MAX_RESULT_DATA_BYTES",
    "PG_COLUMN_DDL",
    "PG_FRAGMENT_COLUMN_MAP",
    "PG_PHYSICAL_COLUMNS",
    "PLAINTEXT_COL",
    "ZSTD_LEVEL",
    "ZST_LEN_COL",
    "ZstdJSON",
    "compress_json_bytes",
    "decode_frame_bytes",
    "decode_result_data",
    "encode_result_data",
    "frame_content_len",
    "populate_post_gate_columns",
    "post_gate_column_values",
    "report_storage_mode",
    "reports_has_compressed_columns",
    "result_data_plaintext_bytes",
    "result_data_select_expr",
    "set_write_concurrency_limit",
    "sync_report_storage_row",
    "write_concurrency_limit",
    "B6B_COLUMN_DDL",
    "B6B_ORDERED_COLUMNS",
]
