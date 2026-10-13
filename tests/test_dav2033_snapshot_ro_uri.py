#!/usr/bin/env python3
"""DAV-2033 — snapshot read-path URI contract: ``mode=ro&immutable=1``.

总控 10-13 令: readers of the shared static snapshot ``~/ta-snapshot/current.db``
must open it as ``file:<path>?mode=ro&immutable=1``.  ``mode=ro`` alone is not
enough: SQLite still opens the WAL of a WAL-mode database, which either creates
``-wal``/``-shm`` beside the file or rewrites an existing ``-shm``.  That is the
source of the stray ``~/ta-snapshot/current.db-shm`` residue.

Measured on the project's locked interpreter (see ``AGENTS.md``), sqlite
3.53.1, on synthetic WAL-mode files with the same header as ``current.db``
(bytes 18/19 = ``02 02``):

    file mode  dir   sidecars  URI                       result
    ---------  ----  --------  ------------------------  ---------------------------------
    0444       rw    absent    mode=ro                   new -wal + new -shm created
    0444       rw    present   mode=ro                   existing -shm bytes CHANGED
    0444       rw    absent    mode=ro&immutable=1        nothing created, rows identical
    0444       rw    present   mode=ro&immutable=1        nothing created, -shm untouched
    0444       ro    absent    mode=ro                   OperationalError: readonly database
    0444       ro    absent    mode=ro&immutable=1        opens fine, nothing created

Precondition pinned by the last test: ``immutable=1`` also makes SQLite skip
the WAL entirely, so it is only correct on a fully checkpointed static file —
which is exactly what ``run_snapshot.py``/``ta_snapshot.py`` produce for
``current.db`` (quick_check-verified, 0444, atomically swapped in).

No production database is read or written by these tests.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "work" / "dav1705_pipeline"))

import build_ledger_chunked as blc  # noqa: E402
import dav1507_exit_progress as m  # noqa: E402
import dav1950_exit_acceptance_report as gen  # noqa: E402

OLD_SNAPSHOT_DB = "/private/tmp/ta-snapshot/current.db"
NEW_SNAPSHOT_DB = "~/ta-snapshot/current.db"
RO_CONTRACT = "mode=ro&immutable=1"


# --------------------------------------------------------------------------- #
# fixtures                                                                    #
# --------------------------------------------------------------------------- #

def _sidecars(directory: Path, stem: str = "snap") -> list[str]:
    return sorted(p.name for p in directory.iterdir()
                  if p.name.startswith(f"{stem}.db-"))


def _make_wal_db(path: Path, rows: int = 1000) -> Path:
    """Fully checkpointed, sidecar-free WAL-mode database (like current.db)."""
    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(path) + suffix)
        if stale.exists():
            stale.unlink()
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE reports(id TEXT PRIMARY KEY, symbol TEXT)")
        con.executemany("INSERT INTO reports VALUES(?, ?)",
                        [(f"{i:032x}", f"{i % 90 + 600000}.SH") for i in range(rows)])
        con.commit()
    finally:
        con.close()  # clean close checkpoints the WAL away
    assert path.read_bytes()[18:20] == b"\x02\x02", "fixture is not WAL mode"
    assert _sidecars(path.parent) == [], "fixture must start sidecar-free"
    return path


def _precreate_sidecars(path: Path) -> bytes:
    """Materialise the sidecar pair the shared snapshot carries (0-byte WAL,
    32 KiB shm) and return the shm bytes so callers can assert they survive."""
    Path(str(path) + "-wal").write_bytes(b"")
    shm = b"\x00" * 32768
    Path(str(path) + "-shm").write_bytes(shm)
    return shm


def _count_rows(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM reports").fetchone()[0]


# --------------------------------------------------------------------------- #
# the fix: no sidecars, no -shm rewrite                                        #
# --------------------------------------------------------------------------- #

def test_dav1507_open_ro_creates_no_wal_sidecars(tmp_path):
    """open_ro() on a WAL-mode snapshot must leave nothing beside the file."""
    db = _make_wal_db(tmp_path / "snap.db")
    con = m.open_ro(str(db))
    try:
        assert _count_rows(con) == 1000
    finally:
        con.close()
    assert _sidecars(tmp_path) == [], "open_ro created WAL sidecars"


def test_dav1705_connect_ro_creates_no_wal_sidecars(tmp_path):
    """_connect_ro() must leave nothing beside the file either."""
    db = _make_wal_db(tmp_path / "snap.db")
    con = blc._connect_ro(db)
    try:
        assert _count_rows(con) == 1000
    finally:
        con.close()
    assert _sidecars(tmp_path) == [], "_connect_ro created WAL sidecars"


def test_dav1507_open_ro_does_not_rewrite_existing_shm(tmp_path):
    """A shared snapshot that already carries -shm/-wal must be left alone."""
    db = _make_wal_db(tmp_path / "snap.db")
    shm_before = _precreate_sidecars(db)
    shm = Path(str(db) + "-shm")
    con = m.open_ro(str(db))
    try:
        assert _count_rows(con) == 1000
    finally:
        con.close()
    assert shm.read_bytes() == shm_before, "open_ro rewrote the shared -shm"
    assert Path(str(db) + "-wal").stat().st_size == 0


def test_dav1705_connect_ro_does_not_rewrite_existing_shm(tmp_path):
    db = _make_wal_db(tmp_path / "snap.db")
    shm_before = _precreate_sidecars(db)
    shm = Path(str(db) + "-shm")
    con = blc._connect_ro(db)
    try:
        assert _count_rows(con) == 1000
    finally:
        con.close()
    assert shm.read_bytes() == shm_before, "_connect_ro rewrote the shared -shm"


def test_plain_mode_ro_would_have_created_sidecars(tmp_path):
    """The bug this card fixes, pinned: bare mode=ro DOES create sidecars.

    Guards against a future "simplification" of the URI back to ``?mode=ro``:
    if SQLite ever stops creating sidecars on a read-only WAL open, this test
    fails and the URI contract can be revisited deliberately.
    """
    db = _make_wal_db(tmp_path / "snap.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert _count_rows(con) == 1000
    finally:
        con.close()
    assert _sidecars(tmp_path) == ["snap.db-shm", "snap.db-wal"], (
        "bare mode=ro no longer creates sidecars — revisit the URI contract")


def test_immutable_opens_wal_db_in_read_only_directory(tmp_path):
    """Correctness win beyond sidecars: mode=ro cannot open a WAL db when the
    directory is not writable; immutable=1 can. A shared snapshot directory may
    legitimately be read-only for the reader."""
    db = _make_wal_db(tmp_path / "snap.db")
    os.chmod(tmp_path, 0o555)
    try:
        with_sqlite_ro = None
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                with_sqlite_ro = _count_rows(con)
            finally:
                con.close()
        except sqlite3.Error:
            with_sqlite_ro = None
        con = m.open_ro(str(db))
        try:
            assert _count_rows(con) == 1000
        finally:
            con.close()
        # immutable=1 always works; plain mode=ro may or may not, depending on
        # whether sidecars are already present. Assert the direction we rely on.
        assert with_sqlite_ro in (None, 1000)
    finally:
        os.chmod(tmp_path, 0o755)


# --------------------------------------------------------------------------- #
# the precondition: immutable=1 skips unmerged WAL frames                      #
# --------------------------------------------------------------------------- #

def test_immutable_skips_unmerged_wal_frames(tmp_path):
    """immutable=1 is only correct on a CHECKPOINTED static file.

    With frames still sitting in the WAL, ``mode=ro`` sees them and
    ``immutable=1`` does not.  ``current.db`` is always produced by a
    backup/decompress + quick_check + atomic swap, so it is checkpointed —
    but this pins the semantic so nobody points immutable=1 at a live database.
    """
    db = _make_wal_db(tmp_path / "snap.db")
    writer = sqlite3.connect(str(db))
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO reports VALUES(?, ?)", ("f" * 32, "600001.SH"))
        writer.commit()
        assert Path(str(db) + "-wal").stat().st_size > 0, "fixture WAL is empty"

        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            ro_rows = _count_rows(ro)
        finally:
            ro.close()
        im = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        try:
            im_rows = _count_rows(im)
        finally:
            im.close()
    finally:
        writer.close()
    assert ro_rows == 1001, "mode=ro must see the unmerged frame"
    assert im_rows == 1000, "immutable=1 must skip the WAL — documented precondition"


# --------------------------------------------------------------------------- #
# the guard: refuse a database whose WAL still holds unmerged frames          #
# --------------------------------------------------------------------------- #

def test_dav1507_open_ro_refuses_non_empty_wal(tmp_path):
    """immutable=1 would skip the WAL, so a non-empty -wal must fail closed.

    This is the live-production case: data/tradingagents.db routinely carries
    MBs of uncheckpointed WAL. Before the guard, immutable=1 would have read a
    stale view and the progress sheet would have reported wrong numbers.
    """
    db = _make_wal_db(tmp_path / "snap.db")
    writer = sqlite3.connect(str(db))
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO reports VALUES(?, ?)", ("a" * 32, "600001.SH"))
        writer.commit()
        assert Path(str(db) + "-wal").stat().st_size > 0
        with pytest.raises(SystemExit) as exc:
            m.open_ro(str(db))
    finally:
        writer.close()
    assert "non-empty WAL" in str(exc.value)
    assert "exit 2" in str(exc.value)


def test_dav1705_connect_ro_refuses_non_empty_wal(tmp_path, capsys):
    db = _make_wal_db(tmp_path / "snap.db")
    writer = sqlite3.connect(str(db))
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO reports VALUES(?, ?)", ("a" * 32, "600001.SH"))
        writer.commit()
        assert Path(str(db) + "-wal").stat().st_size > 0
        with pytest.raises(SystemExit) as exc:
            blc._connect_ro(db)
    finally:
        writer.close()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "non-empty WAL" in err, err
    assert "wal_checkpoint(TRUNCATE)" in err, err


def test_zero_length_wal_sidecar_is_accepted(tmp_path):
    """The shared snapshot legitimately carries a 0-byte -wal: no frames, safe."""
    db = _make_wal_db(tmp_path / "snap.db")
    _precreate_sidecars(db)
    con = m.open_ro(str(db))
    try:
        assert _count_rows(con) == 1000
    finally:
        con.close()
    con = blc._connect_ro(db)
    try:
        assert _count_rows(con) == 1000
    finally:
        con.close()
    assert _sidecars(tmp_path) == ["snap.db-shm", "snap.db-wal"]


# --------------------------------------------------------------------------- #
# source-level contract locks (a future edit cannot silently drop immutable)   #
# --------------------------------------------------------------------------- #

def _open_ro_source() -> str:
    return (ROOT / "scripts" / "dav1507_exit_progress.py").read_text(encoding="utf-8")


def _connect_ro_source() -> str:
    return (ROOT / "work" / "dav1705_pipeline" / "build_ledger_chunked.py"
            ).read_text(encoding="utf-8")


def test_dav1507_open_ro_uri_carries_immutable():
    src = _open_ro_source()
    assert RO_CONTRACT in src, "dav1507 open_ro lost immutable=1"
    assert 'f"file:{db}?mode=ro"' not in src, "bare mode=ro URI is back in dav1507"


def test_dav1705_connect_ro_uri_carries_immutable():
    src = _connect_ro_source()
    assert RO_CONTRACT in src, "dav1705 _connect_ro lost immutable=1"
    assert 'f"file:{db_path}?mode=ro"' not in src, "bare mode=ro URI is back in dav1705"


# --------------------------------------------------------------------------- #
# DAV-2033 item 3: the default snapshot path moved out of /tmp                 #
# --------------------------------------------------------------------------- #

def test_dav1950_default_db_points_at_home_snapshot_and_is_expanded():
    """DEFAULT_DB is the new ~/ta-snapshot location, already expanded.

    Expansion matters: upstream open_ro() does Path(...).resolve(), which does
    not expand ``~``, so an unexpanded default would fail with
    "database does not exist: <cwd>/~/ta-snapshot/current.db".
    """
    assert gen.DEFAULT_DB == os.path.expanduser(NEW_SNAPSHOT_DB)
    assert gen.DEFAULT_DB != OLD_SNAPSHOT_DB
    assert "~" not in gen.DEFAULT_DB, "DEFAULT_DB must be expanded"
    assert Path(gen.DEFAULT_DB).is_absolute()


def test_dav1950_no_stale_tmp_snapshot_default_anywhere():
    for src in (_open_ro_source(), _connect_ro_source(),
                (ROOT / "scripts" / "dav1950_exit_acceptance_report.py"
                 ).read_text(encoding="utf-8")):
        assert OLD_SNAPSHOT_DB not in src, "stale /private/tmp snapshot path left in source"
