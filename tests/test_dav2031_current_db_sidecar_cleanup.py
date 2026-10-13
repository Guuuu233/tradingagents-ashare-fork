"""DAV-2031: refresh_current_db deletes stale current.db-wal/-shm sidecars.

总控 10-13 令: a reader that opens current.db without immutable=1 leaves
current.db-wal/-current.db-shm next to it; when the snapshot job swaps in a
new current.db those sidecars must be removed first, otherwise the new file
appears to have a WAL and ro readers may misread it.

These tests build a real (small) SQLite db, zstd-compress it into a fake
``tradingagents-<date>.db.zst`` snapshot, put a *non-empty* ``current.db-wal``
next to an old ``current.db``, and call ``refresh_current_db`` directly:

- the stale sidecars must be gone after the swap;
- the new ``current.db`` must pass ``PRAGMA quick_check``;
- its contents must equal the new snapshot's contents.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.ta_snapshot import _find_zstd, refresh_current_db


def _make_db(path: str, rows: list[str]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
        conn.commit()
    finally:
        conn.close()


def _read_all(path: str) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT v FROM t ORDER BY v")]
    finally:
        conn.close()


def _zstd_compress(src: str, dst_zst: str) -> None:
    subprocess.run(
        [_find_zstd(), "-19", "-f", src, "-o", dst_zst],
        check=True, capture_output=True, text=True,
    )


def test_stale_wal_shm_removed_and_new_db_ok(tmp_path):
    out = str(tmp_path)

    # Old current.db + NON-EMPTY stale sidecars (as if a reader opened the old
    # file without immutable=1 and left a hot WAL behind).
    old_db = os.path.join(out, "current.db")
    _make_db(old_db, ["old-row-1", "old-row-2"])
    with open(old_db + "-wal", "wb") as f:
        f.write(b"\xde\xad\xbe\xef" * 64)          # non-empty stale WAL
    with open(old_db + "-shm", "wb") as f:
        f.write(b"\x00" * 128)                     # stale shm

    # New snapshot: different content, compressed as tradingagents-<d>.db.zst.
    new_src = os.path.join(out, "new-src.db")
    new_rows = ["new-row-a", "new-row-b", "new-row-c"]
    _make_db(new_src, new_rows)
    _zstd_compress(new_src, os.path.join(out, "tradingagents-2026-10-13.db.zst"))

    result = refresh_current_db(out)

    final = os.path.join(out, "current.db")
    assert result["current_db"] == final

    # 1. Stale sidecars must be gone (not carried across the atomic swap).
    assert not os.path.exists(final + "-wal")
    assert not os.path.exists(final + "-shm")

    # 2. New current.db passes quick_check.
    conn = sqlite3.connect(f"file:{final}?mode=ro&immutable=1", uri=True)
    try:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()

    # 3. Content equals the new snapshot (not the old db).
    assert _read_all(final) == sorted(new_rows)

    # 4. Mode is the read-only 0444 the script sets.
    assert (os.stat(final).st_mode & 0o777) == 0o444


def test_refresh_without_sidecars_still_works(tmp_path):
    """No stale sidecars at all -> swap still succeeds (no crash on absent files)."""
    out = str(tmp_path)
    src = os.path.join(out, "src.db")
    _make_db(src, ["only-row"])
    _zstd_compress(src, os.path.join(out, "tradingagents-2026-10-13.db.zst"))

    refresh_current_db(out)
    final = os.path.join(out, "current.db")
    assert os.path.exists(final)
    assert _read_all(final) == ["only-row"]
    assert not os.path.exists(final + "-wal")
    assert not os.path.exists(final + "-shm")
