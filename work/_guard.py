"""DAV-1904 B-6e: shared plaintext-completeness guard for work/ scripts.

The 29 one-off scripts annotated by B-6d (「压缩存储前适用」) read
``reports.result_data`` directly and were written against plaintext-only
DBs. After P5b clears the plaintext column they would silently read
NULLs and produce wrong output. ``require_plaintext_complete(conn)``
fails the process fast when any row carries only the compressed copy —
the script must then be run against a plaintext-complete DB or ported to
``tradingagents.storage.compressed_json`` decode helpers.
"""

from __future__ import annotations

import sys


def _reports_columns(conn) -> set:
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(reports)")}
    except Exception:
        return set()


def require_plaintext_complete(conn) -> None:
    """Abort if the DB has rows whose plaintext ``result_data`` was cleared.

    Checks for ``result_data IS NULL AND result_data_zst IS NOT NULL`` —
    the exact P5b-cleared signature. Exits with status 2 and a pointed
    message; a DB without the zst column is trivially plaintext-complete.
    """
    cols = _reports_columns(conn)
    if not {"result_data", "result_data_zst"} <= cols:
        return  # legacy schema: whatever plaintext exists is authoritative
    n = conn.execute(
        "SELECT count(*) FROM reports "
        "WHERE result_data IS NULL AND result_data_zst IS NOT NULL"
    ).fetchone()[0]
    if n:
        sys.stderr.write(
            f"[B-6e] {n} reports rows carry ONLY compressed result_data "
            "(result_data IS NULL AND result_data_zst IS NOT NULL). This "
            "script reads the plaintext column directly and cannot run on "
            "a P5b-cleared DB — use a plaintext-complete copy or port to "
            "tradingagents.storage.compressed_json.decode_result_data.\n"
        )
        sys.exit(2)
