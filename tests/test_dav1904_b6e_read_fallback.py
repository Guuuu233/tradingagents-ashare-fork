"""DAV-1904 B-6e: read-fallback coverage + raw-SQL result_data gate.

Two parts:

1. ORM read fallback — in ``REPORT_STORAGE_MODE=plaintext`` (default),
   a row whose plaintext ``result_data`` was cleared (P5b) still reads
   back through ``db_report.result_data`` via the SQL-level
   plaintext-first ``COALESCE(result_data, result_data_zst)`` bound
   lazily on first connect (DAV-1909 🔴-2 rework: SQL-level, so list
   queries stay at ONE SELECT and DAV-1283 D2 ``load_only`` exclusion
   keeps working). Plaintext rows are untouched, and a missing zst
   column never breaks reads (attribute stays a bare Column there).

2. Static regression gate — every ``.py`` file under ``scripts/``,
   ``tradingagents/`` and ``api/`` is scanned for raw-SQL reads of the
   plaintext ``result_data`` column (``SELECT … result_data`` /
   ``json_extract(result_data`` / ``json_valid(result_data``) that do
   not go through the decode layer
   (``result_data_select_expr`` / ``decode_result_data`` /
   ``decode_frame_bytes``) or an explicit whitelist. ``work/`` scripts
   are exempt only while carrying the B-6d「压缩存储前适用」annotation —
   they are guarded at runtime by ``work/_guard.py``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ── Part 1: ORM read fallback ────────────────────────────────────────────────

_PLAIN_ONLY = pytest.mark.skipif(
    os.getenv("REPORT_STORAGE_MODE", "plaintext") != "plaintext",
    reason="plaintext-mode suite",
)


def _make_db(tmp_path: Path) -> Path:
    """Full-migrated schema via the ORM, then one cleared row + one plain row."""
    db_file = tmp_path / "b6e.db"
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_file}"
    env.pop("REPORT_STORAGE_MODE", None)
    subprocess.run(
        [sys.executable, "-c", _BUILD_DB_SNIPPET],
        env={**env, "PYTHONPATH": str(ROOT)},
        check=True,
        cwd=ROOT,
    )
    return db_file


_BUILD_DB_SNIPPET = r"""
import json, os
from sqlalchemy import text
from api.database import Base, ReportDB, engine
from api.database import SessionLocal
from tradingagents.storage.compressed_json import compress_json_bytes

Base.metadata.create_all(engine)
db = SessionLocal()
db.add(ReportDB(id="plain1", symbol="S", trade_date="2026-01-01",
                status="completed", result_data={"x": 1}))
db.commit()
with engine.connect() as c:
    c.execute(text(
        "INSERT INTO reports (id,symbol,trade_date,status,result_data_zst)"
        " VALUES ('cleared1','S','2026-01-01','completed',:z)"),
        {"z": compress_json_bytes(json.dumps({"x": 42}).encode("utf-8"))})
    c.execute(text(
        "INSERT INTO reports (id,symbol,trade_date,status,result_data)"
        " VALUES ('nulllit','S','2026-01-01','completed','null')"))
    c.commit()
"""


@_PLAIN_ONLY
def test_cleared_row_reads_through_zstd_fallback(tmp_path):
    """P5b-cleared row (plaintext NULL, zst present) decodes via the ORM.

    The lazy COALESCE binding promotes ``result_data`` at first connect,
    so ``cleared1`` resolves through zst inside the SAME SELECT — no
    deferred N+1 — while ``plain1``/``nulllit`` read the plaintext column.
    """
    db_file = _make_db(tmp_path)
    code = r"""
import os, sys
sys.path.insert(0, os.environ["ROOT"])
from api.database import ReportDB, engine
from sqlalchemy.orm import sessionmaker
db = sessionmaker(bind=engine)()
got = {r.id: r.result_data for r in
       db.query(ReportDB).filter(
           ReportDB.id.in_(["plain1", "cleared1", "nulllit"])).all()}
assert got["plain1"] == {"x": 1}, got
assert got["cleared1"] == {"x": 42}, got
assert got["nulllit"] is None, got
# write path still dual-writes plaintext + compressed shadow
r = db.get(ReportDB, "plain1")
r.result_data = {"x": 9}
db.commit()
from sqlalchemy import text
with engine.connect() as c:
    plain = c.execute(text(
        "SELECT result_data FROM reports WHERE id='plain1'")).fetchone()[0]
    zst = c.execute(text(
        "SELECT result_data_zst FROM reports WHERE id='plain1'")).fetchone()[0]
assert plain == '{"x": 9}', plain
import zstandard
assert zstandard.ZstdDecompressor().decompress(zst) == b'{"x": 9}'
print("OK")
"""
    env = dict(os.environ)
    env.pop("REPORT_STORAGE_MODE", None)
    env["DATABASE_URL"] = f"sqlite:///{db_file}"
    env["ROOT"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr + out.stdout
    assert "OK" in out.stdout


# ── Part 2: raw-SQL result_data regression gate ─────────────────────────────

# Raw reads of the plaintext column that are legitimate:
#  - decode-layer internals (compressed_json itself, result_data_compat)
#  - write paths (UPDATE … SET result_data=…)
#  - migration/guard tooling that deliberately inspects physical columns
#  - work/ scripts carrying the B-6d annotation (runtime-guarded)
_SCAN_DIRS = ("scripts", "tradingagents", "api")

_WHITELIST = {
    # decode layer itself
    "tradingagents/storage/compressed_json.py",
    "tradingagents/storage/result_data_compat.py",
    # B-6b write/read path (dual-write hook, migration DDL, ORM coalesce)
    "api/database.py",
    # B-6c migration script — operates on physical columns by design
    "scripts/b6c_compress_migration.py",
    # B-4 slim tool — operates on plaintext column by design (guarded)
    "scripts/slim_result_data_storage.py",
    # ledger/verification harnesses that hash/compare raw bytes on purpose
    "scripts/phase2/daily_snapshot_ledger.py",
    "work/b6b_acceptance.py",
    # B-6e acceptance DB builder — reads plaintext to compress, by design
    "work/dav1904_b6e_acceptance/build_dbs.py",
}

# Files that already route reads through the decode layer get a pass if the
# helper is imported anywhere in the file.
_DECODE_MARKERS = (
    "result_data_select_expr",
    "decode_result_data",
    "decode_frame_bytes",
)

# A line counts as a raw read when it embeds ``result_data`` in a SQL SELECT
# / json_extract / json_valid without going through the select-expression.
_RAW_READ_RE = re.compile(
    r"(?:json_extract\s*\(\s*result_data\b"
    r"|json_valid\s*\(\s*result_data\b"
    r"|SELECT[^\"'\n]*\bresult_data\b)",
    re.IGNORECASE,
)

_B6D_ANNOTATION = "压缩存储前适用"


def _iter_py_files():
    for d in _SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            yield p


def test_raw_sql_result_data_reads_go_through_decode_layer():
    offenders = []
    for path in _iter_py_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in _WHITELIST:
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # DAV-1909 🟡-2: line-level decode-marker exemption — a marker
        # anywhere in the file no longer waives unrelated raw-read lines;
        # a line may instead opt out with an inline ``B6E-GATE-OK`` note.
        for lineno, line in _gate_offenders(src):
            offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    assert not offenders, (
        "raw-SQL reads of plaintext result_data bypassing the decode "
        "layer (use result_data_select_expr*/decode_result_data on the "
        "same line, or whitelist the file):\n"
        + "\n".join(offenders)
    )


_B6E_GATE_OK = "B6E-GATE-OK"  # inline opt-out for prose/comments


def _gate_offenders(src: str):
    """Lines the gate flags: raw result_data reads w/o a decode marker or
    an explicit ``B6E-GATE-OK`` annotation on the same line."""
    out = []
    for lineno, line in enumerate(src.splitlines(), 1):
        if not _RAW_READ_RE.search(line):
            continue
        if any(m in line for m in _DECODE_MARKERS):
            continue
        if _B6E_GATE_OK in line:
            continue
        out.append((lineno, line))
    return out


def test_raw_sql_gate_catches_undecoded_read():
    """Negative self-test (DAV-1909 🟡-2): a fixture file containing a
    bare ``SELECT … result_data`` line MUST be flagged by the same
    predicate the gate uses — guards against the regex/exemption being
    weakened until the gate silently passes everything."""
    fixture = (
        'cur.execute("SELECT id, result_data FROM reports '
        'WHERE user_id=?", (uid,))\n'
        'cur.execute("SELECT json_extract(result_data, \'$.a\') FROM reports")\n'
        'cur.execute(f"SELECT {result_data_select_expr_plaintext(con)} FROM reports")\n'
        '# prose: plain ``result_data`` column mention is NOT a read\n'
        'cur.execute("SELECT result_data FROM reports")  # B6E-GATE-OK: test fixture\n'
    )
    hits = _gate_offenders(fixture)
    assert len(hits) == 2, hits  # lines 1-2 flagged; decode/prose/opt-out exempt


def test_work_guarded_scripts_still_annotated_or_guarded():
    """work/ scripts that read result_data raw must either carry the B-6d
    annotation (runtime-guarded) or import the decode layer."""
    work_dir = ROOT / "work"
    if not work_dir.is_dir():
        return
    bad = []
    for p in sorted(work_dir.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel in _WHITELIST:
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(m in src for m in _DECODE_MARKERS):
            continue
        if _B6D_ANNOTATION in src:
            continue  # guarded at runtime by work/_guard.py wiring
        if "work._guard" in src or "_b6e_guard" in src:
            continue
        for lineno, line in enumerate(src.splitlines(), 1):
            if _RAW_READ_RE.search(line):
                bad.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    assert not bad, (
        "work/ scripts reading result_data raw without B-6d annotation, "
        "guard, or decode layer:\n" + "\n".join(bad)
    )


# ── Part 3: work/ guard injection must never break syntax (DAV-1909 🔴-1) ────


def test_work_scripts_remain_parseable():
    """Every ``work/**/*.py`` must ``ast.parse`` cleanly — the B-6e guard
    block must land AFTER ``from __future__ import annotations`` (its only
    legal position is before any other statement), never before it. The
    previous injection put imports ahead of ``__future__`` and turned 18
    scripts into SyntaxErrors that broke pytest collection."""
    import ast

    work_dir = ROOT / "work"
    if not work_dir.is_dir():
        return
    bad = []
    for p in sorted(work_dir.rglob("*.py")):
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError as e:
            bad.append(f"{p.relative_to(ROOT)}: {e}")
    assert not bad, "work/ scripts with SyntaxError:\n" + "\n".join(bad)
