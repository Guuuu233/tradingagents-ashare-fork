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
import shutil
import sqlite3
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.ta_snapshot import _find_zstd, refresh_current_db

# zstd binary is a hard dependency of refresh_current_db; environments
# without it should SKIP, not ERROR (DAV-2032 🟢-6; cf. test_dav1772).
pytestmark = pytest.mark.skipif(
    shutil.which("zstd") is None
    and not os.path.exists("/opt/homebrew/bin/zstd"),
    reason="zstd binary not found on PATH or /opt/homebrew/bin",
)


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


def _header_write_version(path: str) -> int:
    """SQLite header byte 18 (file-format write version): 1=legacy/DELETE, 2=WAL."""
    with open(path, "rb") as f:
        return f.read(20)[18]


def _make_wal_db(db_path: str, rows: list[str]) -> None:
    """Create a real WAL-mode db: live -wal file with committed frames."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
        conn.commit()
        wal_bytes = open(db_path + "-wal", "rb").read()  # snapshot hot WAL
    finally:
        conn.close()          # close checkpoints and deletes the live -wal
    with open(db_path + "-wal", "wb") as f:   # …so plant it back as "stale"
        f.write(wal_bytes)
    with open(db_path + "-shm", "wb") as f:
        f.write(b"\x00" * 128)
    assert len(wal_bytes) > 512  # real WAL frames, not just a header


def _snapshot_refresh(out: str, src: str, day: str = "2026-10-13") -> dict:
    """zstd ``src`` as tradingagents-<day>.db.zst inside ``out`` and refresh."""
    _zstd_compress(src, os.path.join(out, f"tradingagents-{day}.db.zst"))
    return refresh_current_db(out)


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

    # 5. Journal mode normalized: header write_ver == 1 (DELETE), not WAL's 2.
    #    A 0444 WAL-mode current.db would make plain mode=ro readers recreate
    #    -wal/-shm sidecars — the very hazard this card removes.
    assert _header_write_version(final) == 1


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
    assert _header_write_version(final) == 1


def test_sidecar_removal_is_idempotent_and_fail_closed(tmp_path, monkeypatch):
    """🟡-1: FileNotFoundError on -shm (concurrent reader deleted it first)
    must NOT abort the refresh; a real OSError must fail-closed."""
    out = str(tmp_path)
    old_db = os.path.join(out, "current.db")
    _make_db(old_db, ["old"])
    for side in (old_db + "-wal", old_db + "-shm"):
        with open(side, "wb") as f:
            f.write(b"x" * 16)

    src = os.path.join(out, "src.db")
    _make_db(src, ["new"])
    _zstd_compress(src, os.path.join(out, "tradingagents-2026-10-13.db.zst"))

    real_remove = os.remove
    first_call = {"done": False}

    def flaky(path, *a, **kw):
        # First -shm removal fails as if a concurrent reader deleted it first;
        # subsequent calls behave normally.
        if str(path).endswith("-shm") and not first_call["done"]:
            first_call["done"] = True
            raise FileNotFoundError(path)
        return real_remove(path, *a, **kw)

    monkeypatch.setattr(os, "remove", flaky)
    refresh_current_db(out)                 # must succeed, not raise
    monkeypatch.undo()
    final = os.path.join(out, "current.db")
    assert not os.path.exists(final + "-wal")
    # The planted -shm may still be there: our fake FileNotFoundError told the
    # code it was already gone, so refresh rightly skipped removing it — the
    # point under test is that the refresh SUCCEEDED rather than dying on the
    # TOCTOU. Clean it up ourselves (in production the racing reader owns it).
    if os.path.exists(final + "-shm"):
        real_remove(final + "-shm")
    assert _read_all(final) == ["new"]

    # Real removal failure -> RuntimeError, stale files untouched, no swap.
    denied_first = {"done": False}

    def denied(path, *a, **kw):
        if str(path).endswith("-shm") and not denied_first["done"]:
            denied_first["done"] = True
            raise PermissionError(13, "denied", str(path))
        return real_remove(path, *a, **kw)

    with open(final + "-shm", "wb") as f:
        f.write(b"x" * 16)
    monkeypatch.setattr(os, "remove", denied)
    with pytest.raises(RuntimeError, match="cannot remove stale sidecar"):
        refresh_current_db(out)
    monkeypatch.undo()
    assert os.path.exists(final + "-shm")    # evidence left for diagnosis
    real_remove(final + "-shm")              # cleanup: not refresh's job
    assert _read_all(final) == ["new"]       # old current.db NOT overwritten


def test_real_hot_wal_carried_over_would_serve_stale_data(tmp_path):
    """🟢-2: a REAL non-empty WAL left next to an old current.db must be
    removed by the swap — under the old code path a plain mode=ro open would
    silently replay it and serve the OLD rows."""
    out = str(tmp_path)
    old_db = os.path.join(out, "current.db")
    _make_wal_db(old_db, ["old-live-row"])

    src = os.path.join(out, "src.db")
    new_rows = ["new-a", "new-b"]
    _make_db(src, new_rows)
    _zstd_compress(src, os.path.join(out, "tradingagents-2026-10-13.db.zst"))

    refresh_current_db(out)
    final = os.path.join(out, "current.db")
    assert not os.path.exists(final + "-wal")
    assert not os.path.exists(final + "-shm")

    # Plain mode=ro (no immutable=1): must see NEW rows and leave no sidecars.
    conn = sqlite3.connect(f"file:{final}?mode=ro", uri=True)
    try:
        rows = [r[0] for r in conn.execute("SELECT v FROM t ORDER BY v")]
    finally:
        conn.close()
    assert rows == sorted(new_rows)
    assert not os.path.exists(final + "-wal")
    assert not os.path.exists(final + "-shm")


def test_second_day_refresh_over_readonly_current(tmp_path):
    """🟢-3: day-2 refresh replaces the existing 0444 current.db fine, and
    the refresh itself leaves no sidecars on the new file (🟢-4)."""
    out = str(tmp_path)

    src1 = os.path.join(out, "src1.db")
    _make_db(src1, ["day1"])
    _snapshot_refresh(out, src1, day="2026-10-12")
    final = os.path.join(out, "current.db")
    assert (os.stat(final).st_mode & 0o777) == 0o444

    src2 = os.path.join(out, "src2.db")
    _make_db(src2, ["day2-a", "day2-b"])
    _snapshot_refresh(out, src2, day="2026-10-13")   # newest zst wins

    assert _read_all(final) == ["day2-a", "day2-b"]
    assert (os.stat(final).st_mode & 0o777) == 0o444
    assert _header_write_version(final) == 1
    assert not os.path.exists(final + "-wal")
    assert not os.path.exists(final + "-shm")
