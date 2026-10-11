"""DAV-1904 B-6e: shared plaintext-completeness guard for work/ scripts.

The 29 one-off scripts annotated by B-6d (「压缩存储前适用」) read
``reports.result_data`` directly and were written against plaintext-only
DBs. After P5b clears the plaintext column they would silently read
NULLs and produce wrong output. ``require_plaintext_complete(conn)``
fails the process fast when any row carries only the compressed copy —
the script must then be run against a plaintext-complete DB or ported to
``tradingagents.storage.compressed_json`` decode helpers.

Scope note (DAV-1909 🟡-3): the guard checks ONLY the P5b-cleared
signature (``result_data IS NULL AND result_data_zst IS NOT NULL``).
It does NOT detect rows where the plaintext copy exists but is stale /
diverged from the compressed shadow — such rows pass the guard and are
read verbatim. Scripts whose statistics are sensitive to that split-brain
state must compare ``result_data_zst_len`` against
``length(result_data)`` themselves or migrate to the decode layer.
"""

from __future__ import annotations

import sys
from pathlib import Path


def bootstrap() -> Path:
    """Insert the repo root into ``sys.path`` so ``import work._guard`` works.

    Self-locating: walks up from this file until a directory that carries
    ``work/_guard.py`` (the repo root) is found, then prepends it to
    ``sys.path``. Lets ``work/`` scripts at any nesting depth bootstrap
    with just ``import work._guard as _g; _g.bootstrap()`` — no
    hard-coded ``parents[N]`` — which keeps the import block legal
    *after* ``from __future__ import annotations`` (DAV-1909 🔴-1).
    """
    d = Path(__file__).resolve().parent
    for cand in (d, *d.parents):
        if (cand / "work" / "_guard.py").is_file():
            s = str(cand)
            if s not in sys.path:
                sys.path.insert(0, s)
            return cand
    raise RuntimeError(
        "work/_guard.py bootstrap: repo root (dir containing work/_guard.py) "
        f"not found above {__file__}"
    )


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
