#!/usr/bin/env python3
"""DAV-1772 (存储 B-6c): ``reports.result_data`` 明文 -> zstd-9 压缩列迁移脚本.

Scope (卡面): 只做脚本, 不依赖 B-6b 接口细节. 本脚本只建/写
``result_data_zst`` / ``result_data_zst_len`` 两列; 31 个 ``pg_*`` 物化列的
回填是 B-6b 的职责 (B-6b 落地后补跑), 本脚本不建那些列, 以免与 B-6b 的
schema 猜测分叉.

Hard contract (总控 10-10 裁定①②):
* 按**原始字节**压缩: ``zst = compress(stored_bytes)``, 不做 ``json.loads``
  再 ``json.dumps``. 核对条件为 ``decompress(zst) == stored_bytes`` —
  逐行、事务内、写入后即验, 失配则回滚该行并记台账, 永不落半截写.
* 演练报告须报「原始字节与重新序列化结果不一致的行数」
  (``reserialize_mismatch_rows``, 纯诊断口径, 不阻塞).
* P5b (``clear-plaintext``) 纳入本脚本范围, 执行前提:
  ①逐行核对解压结果与原始字节相同 (每行先验后清);
  ②执行前做 ``.backup()`` + zstd 整库备份 (保留 30 天, 同目录超期自动裁剪);
  ③提供 ``restore-plaintext`` 反向子命令 (从压缩列写回明文) 并在副本上演练.
  生产执行由总控在发布卡上另签 (见 ``_refuse_production_unless_signed``).

Safety (复用 DAV-1544 / B-4 的成熟机制):
* Independent zstd pre-image export of every row about to be written,
  outside the database directory, sha256 self-verified. Export failure
  means zero writes of that batch.
* In-transaction drift check: a row whose current sha256 no longer
  matches its exported pre-image is skipped and counted, never forced.
* ``rollback`` subcommand restores the exact pre-convert state from an
  export file (clears ``result_data_zst``; restores明文 from the export
  when P5b already cleared it; skips rows someone else touched).
* Batch commits (--batch-size, default 50) + journal.jsonl: every
  committed report_id is journaled, so an interrupted run resumes by
  skipping already-committed ids.
* Runtime guard identical to the T+5 backfill: healthz must be ok and
  the runtime must report zero active analyses; a busy/unknown state
  skips the run. A production-path guard additionally refuses writes
  before the signed post-B-4 handoff (卡面: B-4 10-14 稳定两个交易日之后,
  走发布卡、总控签版本号).

Subcommands: ``export-preimage`` / ``convert`` (default) / ``verify``
/ ``rollback`` / ``restore-plaintext`` / ``clear-plaintext`` (P5b).
``convert`` also accepts ``--vacuum-into`` for an optional slimmed copy +
pair verification (same semantics as B-4; P5b 瘦身的主路径仍是
``clear-plaintext --vacuum-into``).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import zstandard

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Independent pre-image exports / journals / backups live outside the data
# directory (库外固定目录), same convention as DAV-1544 / B-4.
PRE_EXPORT_DIR = ROOT / "work/dav1772-pre-export"
PRE_EXPORT_VERSION = 1
JOURNAL_DIR = ROOT / "work/dav1772-journal"
BACKUP_DIR = ROOT / "work/dav1772-backups"
BACKUP_PREFIX = "tradingagents-b6c-p5b-"
BACKUP_RETENTION_DAYS = 30

from tradingagents.dataflows.trade_calendar import now_cn  # noqa: E402

COMPRESSION_LEVEL = 9
# Fallback cap for stream frames without content size (设计 §3.1: > p100
# 13 MiB 留 4x 余量). One-shot compress() frames always carry FCS.
MAX_RESULT_DATA_BYTES = 64 * 1024 * 1024

# 迁移/校验三处口径统一为可执行判据 (设计 §2.1/§4/§5):
# result_data='null' (4B) 不迁移 —— 压缩反而变大, 解码层按 None 处理.
CANDIDATE_WHERE = (
    "result_data_zst IS NULL "
    "AND result_data IS NOT NULL "
    "AND result_data <> 'null'"
)
RESIDUAL_WHERE = CANDIDATE_WHERE  # P4/P5b 准入: 该计数降到 0 (严禁 IS NULL = 0 写法)

_PRODUCTION_EARLIEST = date(2026, 10, 16)  # B-4 10-14 + 两个交易日, 见卡面


# ---------------------------------------------------------------------------
# raw-bytes codec (single-threaded offline script; no shared-service ctx)


def _cctx() -> zstandard.ZstdCompressor:
    global _CCTX  # noqa: PLW0603
    try:
        return _CCTX
    except NameError:
        _CCTX = zstandard.ZstdCompressor(level=COMPRESSION_LEVEL)
        return _CCTX


def _dctx() -> zstandard.ZstdDecompressor:
    global _DCTX  # noqa: PLW0603
    try:
        return _DCTX
    except NameError:
        _DCTX = zstandard.ZstdDecompressor()
        return _DCTX


def compress_raw(data: bytes) -> bytes:
    """裁定②: compress the stored bytes verbatim, no json round-trip."""
    return _cctx().compress(data)


def decompress_raw(blob: bytes) -> bytes:
    """Decompress one one-shot frame; stream frames fall back to 64 MiB."""
    if not blob:
        raise ValueError("empty zstd frame")
    size = zstandard.frame_content_size(blob)
    if size is None or size < 0 or size >= zstandard.CONTENTSIZE_ERROR:
        size = MAX_RESULT_DATA_BYTES
    if size == 0:
        return b""
    return _dctx().decompress(blob, max_output_size=size)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# db helpers (same pattern as B-4 / T+5 backfill)


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


def ensure_columns(db_path):
    """Idempotently add result_data_zst / result_data_zst_len (B-6c owns
    only these two; pg_*物化列归 B-6b, 本脚本不碰)."""
    _validate_db(db_path)
    with closing(_open_db(db_path, writable=True)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reports)")}
        if "result_data_zst" not in columns:
            conn.execute("ALTER TABLE reports ADD COLUMN result_data_zst BLOB")
        if "result_data_zst_len" not in columns:
            conn.execute("ALTER TABLE reports ADD COLUMN result_data_zst_len INTEGER")
        conn.commit()
        after = {row[1] for row in conn.execute("PRAGMA table_info(reports)")}
    if not {"result_data_zst", "result_data_zst_len"} <= after:
        raise RuntimeError("compression columns missing after ensure")
    return sorted(after)


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


def _refuse_production_unless_signed(db_path, production_authorized):
    """卡面: 上线执行排在 B-4 (10-14) 稳定两个交易日之后, 走发布卡、总控签版本号."""
    if _is_production(db_path) and (not production_authorized
                                    or now_cn().date() < _PRODUCTION_EARLIEST):
        raise RuntimeError(
            "production B-6c write forbidden: requires 发布卡 + 总控签字 "
            f"(卡面: B-4 10-14 稳定两个交易日之后; earliest {_PRODUCTION_EARLIEST})")


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
    """Ids already committed in a previous (interrupted) run — resume skip.
    Accepts both journal spellings (B-4 DAV-1640: {"phase":"committed"} from
    _write_row and {"event": outcome} from the flush loop)."""
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
            "tool": "b6c_compress_migration",
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
                if raw_text is None:
                    raise RuntimeError(
                        f"planned row {report_id} has NULL result_data at export")
                sha = _sha256_bytes(raw_text.encode("utf-8"))
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
    actual = _sha256_bytes(Path(path).read_bytes())
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
    sha = _sha256_bytes(blob)
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
# write path


def _write_row(conn, report_id, audit, exported_sha=None):
    """Compress one row's stored bytes inside its own transaction.

    exported_sha (sha256 of the pre-exported result_data) is the drift
    witness: a row whose current bytes no longer match its export is
    skipped as "pre_export_mismatch". The compressed bytes are computed
    from the *current* stored text inside the transaction, and the commit
    only lands after ``decompress(stored_zst) == stored明文`` verifies —
    a mismatch raises, rolls back, and counts as a verification failure.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT result_data FROM reports WHERE id=?", (report_id,)).fetchone()
        if row is None or row[0] is None:
            conn.rollback()
            return "missing"
        original = row[0]
        if exported_sha is not None:
            current_sha = _sha256_bytes(original.encode("utf-8"))
            if current_sha != exported_sha:
                conn.rollback()
                return "pre_export_mismatch"
        raw_bytes = original.encode("utf-8")
        zst = compress_raw(raw_bytes)
        before_sha = _sha256_bytes(raw_bytes)
        after_sha = _sha256_bytes(zst)
        _journal_emit(audit, {"phase": "prepared", "report_id": report_id,
                              "before_sha256": before_sha, "after_sha256": after_sha,
                              "plain_bytes": len(raw_bytes),
                              "zst_bytes": len(zst)})
        conn.execute("UPDATE reports SET result_data_zst=?, result_data_zst_len=? WHERE id=?",
                     (sqlite3.Binary(zst), len(raw_bytes), report_id))
        stored = conn.execute(
            "SELECT result_data, result_data_zst, result_data_zst_len FROM reports WHERE id=?",
            (report_id,)).fetchone()
        if stored[0] != original:
            raise ValueError("stored明文 changed by our own write")
        if stored[2] != len(raw_bytes):
            raise ValueError("stored zst_len mismatch")
        if bytes(stored[1]) != zst:
            raise ValueError("stored bytes differ from compressed bytes")
        # 卡面硬验收 (裁定②): decompress(zst) == stored_bytes.
        if decompress_raw(bytes(stored[1])) != raw_bytes:
            raise ValueError("post-write decompress mismatch")
        conn.commit()
        _journal_emit(audit, {"phase": "committed", "report_id": report_id,
                              "after_sha256": after_sha,
                              "plain_bytes": len(raw_bytes),
                              "zst_bytes": len(zst)})
        return "converted"
    except Exception:
        conn.rollback()
        raise


def _scan_pass(db_path, committed_ids, result, exceptions):
    """Pass 1 (read-only): classify every row, return the planned write list.

    Candidates are exactly ``CANDIDATE_WHERE`` rows. Bytes are re-derived
    inside each write transaction (never from pass-1 state). The
    reserialize diagnostic (裁定②演练口径) is computed here: rows whose
    stored bytes differ from ``json.dumps(json.loads(stored))`` — i.e. rows
    where a re-serializing writer would NOT reproduce the stored bytes.
    NOTE (memory): pass 1 touches every明文 byte; run_convert executes this
    pass in a child process with a fresh heap so the parent's peak stays at
    the write phase only (same discipline as B-4).
    """
    planned = []
    mismatch_samples = []
    with closing(_open_db(db_path)) as conn:
        for row in conn.execute(
                "SELECT id, result_data, result_data_zst FROM reports ORDER BY id"):
            result["scanned"] += 1
            report_id, raw, have_zst = row["id"], row["result_data"], row["result_data_zst"]
            if report_id in committed_ids:
                result["resumed_skipped"] += 1
                continue
            if raw is None:
                result["null_rows"] += 1
                continue
            if raw == "null":
                result["null_literal_rows"] += 1
                continue
            if have_zst is not None:
                result["already_compressed"] += 1
                continue
            try:
                value = json.loads(raw)
                if json.dumps(value).encode("utf-8") != raw.encode("utf-8"):
                    result["reserialize_mismatch_rows"] += 1
                    if len(mismatch_samples) < 50:
                        mismatch_samples.append(report_id)
            except ValueError:
                result["unparsable_rows"] += 1
                exceptions.append({"report_id": report_id,
                                   "conflicts": ["result_data is not valid JSON "
                                                 "(migrated as raw bytes)"]})
            planned.append({"id": report_id,
                            "sha256": _sha256_bytes(raw.encode("utf-8")),
                            "bytes": len(raw.encode("utf-8"))})
            result["candidates"] += 1
    result["reserialize_mismatch_samples"] = mismatch_samples
    return planned


def run_convert(*, db_path, batch_size=50, batch_byte_limit=512 * 1024 * 1024,
                journal_path=None, exceptions_path=None,
                health_url="http://127.0.0.1:8000", production_authorized=False,
                copy_rehearsal=False, pre_export_dir=None, dry_run=False,
                scan_only=False):
    """Compress candidate rows' stored bytes into result_data_zst, batched.

    Batched: every batch exports pre-images first, then writes inside
    per-row transactions, then journals each committed id. Interrupting at
    any point leaves a consistent DB; re-running skips committed ids.
    明文列 is never touched by convert (P5b clears it only via
    clear-plaintext with its own backup + per-row gates).
    """
    ensure_columns(db_path)
    _refuse_production_unless_signed(db_path, production_authorized)
    production = _is_production(db_path)
    if not dry_run and not scan_only and (production or not copy_rehearsal):
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
              "scanned": 0, "already_compressed": 0, "resumed_skipped": 0,
              "null_rows": 0, "null_literal_rows": 0, "candidates": 0,
              "unparsable_rows": 0, "reserialize_mismatch_rows": 0,
              "reserialize_mismatch_samples": [],
              "converted_rows": 0, "pre_export_mismatch": 0,
              "verify_failures": 0, "missing": 0,
              "write_conflicts": 0, "batches": 0, "pre_export_files": [],
              "plain_bytes": 0, "zst_bytes": 0}
    exceptions = []

    # Pass 1 runs in a *subprocess*: touching ~5 GB of明文 peaks the process
    # heap (CPython retains freed arenas), and that peak would otherwise
    # persist into the write phase — the very red line this migration must
    # stay under. The child returns only the small planned-id list.
    import subprocess
    import tempfile
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    fd, scan_path = tempfile.mkstemp(
        prefix="dav1772-scan-", suffix=".json", dir=str(journal_path.parent))
    os.close(fd)
    child_code = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "from scripts import b6c_compress_migration as mig\n"
        "result = {\"scanned\":0,\"already_compressed\":0,\"resumed_skipped\":0,"
        "\"null_rows\":0,\"null_literal_rows\":0,\"candidates\":0,"
        "\"unparsable_rows\":0,\"reserialize_mismatch_rows\":0,"
        "\"reserialize_mismatch_samples\":[]}\n"
        "exc = []\n"
        "planned = mig._scan_pass(%r, set(%r), result, exc)\n"
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

    exceptions_path.parent.mkdir(parents=True, exist_ok=True)
    exceptions_path.write_text(
        json.dumps({"db_path": str(db_path), "generated_at": datetime.now().isoformat(),
                    "exceptions": exceptions}, ensure_ascii=False, indent=2))
    result["exceptions_file"] = str(exceptions_path)
    result["exceptions"] = len(exceptions)

    def _journal_totals():
        # Byte totals are re-derived from the journal so resumed runs report
        # full-run totals, not just this invocation's delta.
        try:
            plain_total = zst_total = 0
            for line in Path(journal_path).read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("phase") == "committed":
                    plain_total += int(event.get("plain_bytes") or 0)
                    zst_total += int(event.get("zst_bytes") or 0)
            result["plain_bytes"] = plain_total
            result["zst_bytes"] = zst_total
        except OSError:
            pass

    if scan_only or dry_run or not planned:
        _journal_totals()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    # Pass 2: batch -> independent pre-export -> per-row transactions ->
    # journal each committed id (resume skips them next run). Batches are
    # split by BOTH row count and cumulative raw bytes (B-4 DAV-1631: a
    # count-only cap can hold GBs resident when rows are large).
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
                    except ValueError as exc:
                        result["verify_failures"] += 1
                        exceptions.append(
                            {"report_id": item["id"], "phase": "write",
                             "conflicts": [str(exc)]})
                        _journal_emit(audit, {"event": "verify_failure",
                                              "report_id": item["id"],
                                              "error": str(exc)[:500]})
                        continue
                    except sqlite3.OperationalError:
                        result["write_conflicts"] += 1
                        continue
                    key = {"converted": "converted_rows",
                           "pre_export_mismatch": "pre_export_mismatch",
                           "missing": "missing"}[outcome]
                    result[key] += 1
                    if outcome == "converted":
                        result["plain_bytes"] += item["bytes"]
                        _journal_emit(audit, {"event": outcome, "report_id": item["id"]})
                    else:
                        _journal_emit(audit, {"event": outcome, "report_id": item["id"]})
            finally:
                conn.close()
            result["batches"] += 1
            batch = []
            batch_bytes = 0

        for item in planned:
            if batch and (len(batch) >= batch_size
                          or batch_bytes + item["bytes"] > batch_byte_limit):
                flush()
            batch.append(item)
            batch_bytes += item["bytes"]
        flush()
    finally:
        audit.close()

    _journal_totals()

    exceptions_path.write_text(
        json.dumps({"db_path": str(db_path), "generated_at": datetime.now().isoformat(),
                    "exceptions": exceptions}, ensure_ascii=False, indent=2))
    result["exceptions"] = len(exceptions)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ---------------------------------------------------------------------------
# verify (逐行解码回读对照)


def verify_compressed(db_path, *, require_complete=False):
    """Per-row decode readback: decompress(result_data_zst) == result_data.

    Reports residual unmigrated rows, mismatches, P5b-cleared rows, and the
    裁定② reserialize diagnostic. Pure read-only (temp_store=MEMORY).
    """
    ensure_columns(db_path)
    result = {"scanned": 0, "verified_ok": 0, "mismatches": [],
              "unmigrated": 0, "cleared_ok": 0, "cleared_bad": [],
              "null_rows": 0, "null_literal_rows": 0,
              "reserialize_mismatch_rows": 0, "unparsable_rows": 0}
    with closing(_open_db(db_path)) as conn:
        for row in conn.execute(
                "SELECT id, result_data, result_data_zst, result_data_zst_len "
                "FROM reports ORDER BY id"):
            result["scanned"] += 1
            report_id, raw, zst, zlen = row["id"], row["result_data"], \
                row["result_data_zst"], row["result_data_zst_len"]
            if raw is None and zst is None:
                result["null_rows"] += 1
                continue
            if raw == "null":
                if zst is not None:
                    result["mismatches"].append(
                        {"report_id": report_id, "error": "'null' row must not carry zst"})
                else:
                    result["null_literal_rows"] += 1
                continue
            if zst is None:
                result["unmigrated"] += 1
                continue
            try:
                plain = decompress_raw(bytes(zst))
            except Exception as exc:
                bucket = "cleared_bad" if raw is None else "mismatches"
                result[bucket].append(
                    {"report_id": report_id, "error": f"decompress failed: {exc!r}"[:300]})
                continue
            if raw is None:
                # P5b-cleared row: zst must at least decode to *some* bytes.
                try:
                    json.loads(plain.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    result["cleared_bad"].append(
                        {"report_id": report_id,
                         "error": "cleared row zst does not decode to JSON"})
                    continue
                result["cleared_ok"] += 1
                continue
            raw_bytes = raw.encode("utf-8")
            if plain != raw_bytes:
                result["mismatches"].append(
                    {"report_id": report_id,
                     "error": f"decompress != stored bytes "
                              f"(plain={len(raw_bytes)}, out={len(plain)})"})
                continue
            if zlen is not None and zlen != len(raw_bytes):
                result["mismatches"].append(
                    {"report_id": report_id, "error": "zst_len drift"})
                continue
            result["verified_ok"] += 1
            try:
                if json.dumps(json.loads(raw)).encode("utf-8") != raw_bytes:
                    result["reserialize_mismatch_rows"] += 1
            except ValueError:
                result["unparsable_rows"] += 1
    result["mismatch_count"] = len(result["mismatches"])
    result["cleared_bad_count"] = len(result["cleared_bad"])
    result["ok"] = (not result["mismatches"] and not result["cleared_bad"]
                    and (not require_complete or result["unmigrated"] == 0))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ---------------------------------------------------------------------------
# rollback (回退子命令): restore exact pre-convert state from a pre-export


def rollback_from_export(db_path, export_file, *, sha256=None,
                         health_url="http://127.0.0.1:8000",
                         production_authorized=False, copy_rehearsal=False):
    """Rollback: for each exported row, restore the pre-convert state.

    * current明文 == exported bytes  -> clear zst columns (rolled_back);
    * current明文 IS NULL (P5b cleared) -> restore明文 from export +
      clear zst (restored_plaintext);
    * anything else (someone rewrote明文 after convert) -> conflict skip.
    Every mutation is re-read-verified inside its own transaction.
    """
    ensure_columns(db_path)
    _refuse_production_unless_signed(db_path, production_authorized)
    _validate_db(db_path)
    export = _load_pre_export(export_file, expected_sha256=sha256)
    production = _is_production(db_path)
    if production or not copy_rehearsal:
        guard = check_runtime_guard(db_path, health_url)
        if not guard["allowed"]:
            result = {"skipped": True, "guard": guard}
            print(json.dumps(result, ensure_ascii=False))
            return result
    else:
        guard = {"allowed": True, "reason": "copy_rehearsal"}
    result = {"rolled_back": 0, "restored_plaintext": 0, "already_clean": 0,
              "missing": 0, "conflicts": 0, "mismatched": 0, "guard": guard}
    conn = _open_db(db_path, writable=True)
    try:
        for record in export["records"]:
            report_id = record["report_id"]
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT result_data, result_data_zst FROM reports WHERE id=?",
                    (report_id,)).fetchone()
                if row is None:
                    conn.rollback()
                    result["missing"] += 1
                    continue
                current, zst = row[0], row[1]
                if zst is None and current == record["result_data"]:
                    conn.rollback()
                    result["already_clean"] += 1
                    continue
                if current is None:
                    # P5b cleared the明文: bring it back from the export.
                    conn.execute("UPDATE reports SET result_data=?, "
                                 "result_data_zst=NULL, result_data_zst_len=NULL WHERE id=?",
                                 (record["result_data"], report_id))
                    stored = conn.execute(
                        "SELECT result_data, result_data_zst FROM reports WHERE id=?",
                        (report_id,)).fetchone()
                    if stored[0] != record["result_data"] or stored[1] is not None:
                        raise ValueError("post-rollback verification failed")
                    conn.commit()
                    result["restored_plaintext"] += 1
                    continue
                if current != record["result_data"]:
                    conn.rollback()
                    result["conflicts"] += 1
                    continue
                conn.execute("UPDATE reports SET result_data_zst=NULL, "
                             "result_data_zst_len=NULL WHERE id=?", (report_id,))
                stored = conn.execute(
                    "SELECT result_data, result_data_zst FROM reports WHERE id=?",
                    (report_id,)).fetchone()
                if stored[0] != record["result_data"] or stored[1] is not None:
                    raise ValueError("post-rollback verification failed")
                conn.commit()
                result["rolled_back"] += 1
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
# restore-plaintext (裁定① 反向子命令): zst ->明文, for P5b-cleared rows


def restore_plaintext(db_path, *, health_url="http://127.0.0.1:8000",
                      production_authorized=False, copy_rehearsal=False):
    """Write decompressed bytes back to result_data for rows where明文 is
    NULL but zst exists. Per-row verified: stored明文 == decompress(zst).
    Rerun-safe (already-restored rows no longer match the WHERE)."""
    ensure_columns(db_path)
    _refuse_production_unless_signed(db_path, production_authorized)
    _validate_db(db_path)
    production = _is_production(db_path)
    if production or not copy_rehearsal:
        guard = check_runtime_guard(db_path, health_url)
        if not guard["allowed"]:
            result = {"skipped": True, "guard": guard, "restored_rows": 0}
            print(json.dumps(result, ensure_ascii=False))
            return result
    else:
        guard = {"allowed": True, "reason": "copy_rehearsal"}
    result = {"restored_rows": 0, "failed": 0, "guard": guard}
    conn = _open_db(db_path, writable=True)
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM reports WHERE result_data IS NULL "
            "AND result_data_zst IS NOT NULL ORDER BY id").fetchall()]
        for report_id in ids:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT result_data_zst FROM reports WHERE id=?",
                    (report_id,)).fetchone()
                if row is None or row[0] is None:
                    conn.rollback()
                    continue
                plain = decompress_raw(bytes(row[0])).decode("utf-8")
                conn.execute("UPDATE reports SET result_data=? WHERE id=?",
                             (plain, report_id))
                stored = conn.execute(
                    "SELECT result_data FROM reports WHERE id=?",
                    (report_id,)).fetchone()[0]
                if stored != plain:
                    raise ValueError("post-restore verification failed")
                conn.commit()
                result["restored_rows"] += 1
            except sqlite3.OperationalError:
                conn.rollback()
                result["failed"] += 1
            except Exception:
                conn.rollback()
                result["failed"] += 1
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ---------------------------------------------------------------------------
# clear-plaintext (P5b 瘦身档): backup -> per-row verify -> clear明文


def _p5b_backup(db_path, backup_dir=None):
    """裁定①前提②: sqlite .backup() + zstd 整库备份, 保留 30 天.

    Returns {"backup_db", "backup_zst", "pruned"}. Both artifacts are
    written before any明文 is touched; files older than 30 days with our
    prefix are pruned from the same directory.
    """
    backup_dir = Path(backup_dir or BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    db_target = backup_dir / f"{BACKUP_PREFIX}{stamp}.db"
    zst_target = Path(str(db_target) + ".zst")
    if db_target.exists() or zst_target.exists():
        raise RuntimeError("backup target already exists; refusing to overwrite")
    with closing(_open_db(db_path)) as src, \
            closing(sqlite3.connect(str(db_target))) as dst:
        src.backup(dst)
    # 流式压缩整库: 一次性读入 6GB 会冲破 4GiB 红线 (实测 peak 6.6GB),
    # 必须分块 stream (chunk 64MiB, 常驻 ~百 MB).
    cctx = zstandard.ZstdCompressor(level=3)
    with open(db_target, "rb") as fin, open(zst_target, "wb") as fout:
        with cctx.stream_writer(fout, closefd=False) as streamer:
            while True:
                chunk = fin.read(64 * 1024 * 1024)
                if not chunk:
                    break
                streamer.write(chunk)
    pruned = []
    cutoff = time.time() - BACKUP_RETENTION_DAYS * 86400
    for candidate in backup_dir.glob(BACKUP_PREFIX + "*"):
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
                pruned.append(candidate.name)
        except OSError:
            pass
    return {"backup_db": str(db_target), "backup_zst": str(zst_target),
            "pruned": pruned}


def clear_plaintext(db_path, *, backup_dir=None, vacuum_into_path=None,
                    health_url="http://127.0.0.1:8000",
                    production_authorized=False, copy_rehearsal=False,
                    allow_clear=False):
    """P5b: clear明文 for verified rows, then optionally VACUUM INTO.

    Gates (all must hold): --allow-clear flag; backup succeeds first;
    every cleared row re-verifies decompress(zst) ==明文 inside its own
    transaction (failures keep their明文 and abort the vacuum).明文 is
    cleared ONLY for rows whose zst verifies — the failure path is never
    a data-loss path.
    """
    if not allow_clear:
        raise RuntimeError("clear-plaintext requires --allow-clear (P5b, 总控签字)")
    ensure_columns(db_path)
    _refuse_production_unless_signed(db_path, production_authorized)
    _validate_db(db_path)
    production = _is_production(db_path)
    if production or not copy_rehearsal:
        guard = check_runtime_guard(db_path, health_url)
        if not guard["allowed"]:
            result = {"skipped": True, "guard": guard, "cleared_rows": 0}
            print(json.dumps(result, ensure_ascii=False))
            return result
    else:
        guard = {"allowed": True, "reason": "copy_rehearsal"}
    backup = _p5b_backup(db_path, backup_dir=backup_dir)
    result = {"cleared_rows": 0, "failed": 0, "failures": [],
              "backup": backup, "guard": guard}
    conn = _open_db(db_path, writable=True)
    try:
        # 已校验行 = zst 存在且明文仍在 (先验后清, 见循环内逐行核对).
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM reports WHERE result_data_zst IS NOT NULL "
            "AND result_data IS NOT NULL AND result_data <> 'null' "
            "ORDER BY id").fetchall()]
        for report_id in ids:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT result_data, result_data_zst FROM reports WHERE id=?",
                    (report_id,)).fetchone()
                if row is None or row[0] is None or row[1] is None:
                    conn.rollback()
                    continue
                raw_bytes = row[0].encode("utf-8")
                # 裁定①前提①: 逐行核对解压结果与原始字节相同 — 先验后清.
                if decompress_raw(bytes(row[1])) != raw_bytes:
                    raise ValueError("decompress != stored bytes;明文 kept")
                conn.execute("UPDATE reports SET result_data=NULL WHERE id=?",
                             (report_id,))
                stored = conn.execute(
                    "SELECT result_data, result_data_zst FROM reports WHERE id=?",
                    (report_id,)).fetchone()
                if stored[0] is not None or stored[1] is None \
                        or decompress_raw(bytes(stored[1])) != raw_bytes:
                    raise ValueError("post-clear verification failed")
                conn.commit()
                result["cleared_rows"] += 1
            except sqlite3.OperationalError:
                conn.rollback()
                result["failed"] += 1
                result["failures"].append({"report_id": report_id, "error": "locked"})
            except Exception as exc:
                conn.rollback()
                result["failed"] += 1
                if len(result["failures"]) < 50:
                    result["failures"].append(
                        {"report_id": report_id, "error": str(exc)[:300]})
    finally:
        conn.close()
    if vacuum_into_path:
        if result["failed"]:
            result["vacuum"] = "skipped: clear failures present"
        else:
            new_db = vacuum_into(db_path, vacuum_into_path)
            check = verify_db_pair(db_path, new_db)
            result["vacuum_into"] = new_db
            result["vacuum_verify"] = check
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ---------------------------------------------------------------------------
# VACUUM INTO + pair verification (same semantics as B-4)


def vacuum_into(db_path, out_path):
    """Create a slimmed copy of `db_path` at `out_path` via VACUUM INTO.

    Memory discipline: temp storage forced to FILE, mmap disabled, page
    cache capped at 256 MB. Caller sets TMPDIR to a volume with headroom
    (≈2x the library size).
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


def _new_view_bytes(conn, report_id):
    """Read-side view of one row in a (possibly migrated) db: the明文 when
    present, else decompress(zst). Returns None only when both are NULL."""
    row = conn.execute(
        "SELECT result_data, result_data_zst FROM reports WHERE id=?",
        (report_id,)).fetchone()
    if row is None:
        return None, False
    raw, zst = row[0], row[1]
    if raw is not None:
        return raw.encode("utf-8"), True
    if zst is not None:
        try:
            return decompress_raw(bytes(zst)), True
        except Exception:
            return None, True
    return None, True


def verify_db_pair(old_path, new_path):
    """Post-VACUUM verification: integrity + row counts + per-row byte
    equality between source and slimmed databases (new side read through
    the zst view when明文 was cleared). Streams row-by-row; neither table
    is materialized in memory."""
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
            new_bytes, found = _new_view_bytes(nconn, rid)
            if not found:
                missing_ids.append(rid)
                continue
            old_bytes = None if old_text is None else old_text.encode("utf-8")
            # Old side may itself be migrated (verify after P5b): compare
            # through the same view so the check is symmetric.
            if old_text is None:
                old_bytes, _ = _new_view_bytes(oconn, rid)
            try:
                if old_bytes != new_bytes:
                    result["view_mismatches"] += 1
            except Exception:
                result["view_mismatches"] += 1
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
# export-preimage standalone (audit / manual rollback cover)


def run_export_preimage(db_path, *, export_dir=None):
    """Scan candidates and export their pre-images without writing."""
    ensure_columns(db_path)
    _validate_db(db_path)
    committed: set = set()
    stats = {"scanned": 0, "already_compressed": 0, "resumed_skipped": 0,
             "null_rows": 0, "null_literal_rows": 0, "candidates": 0,
             "unparsable_rows": 0, "reserialize_mismatch_rows": 0,
             "reserialize_mismatch_samples": []}
    planned = _scan_pass(db_path, committed, stats, [])
    if not planned:
        result = {"rows": 0, "stats": stats}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    pre_export = export_pre_images(db_path, planned, export_dir=export_dir)
    result = {"rows": len(planned), "path": pre_export["path"],
              "sha256": pre_export["sha256"], "stats": stats}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ---------------------------------------------------------------------------
# CLI


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path")
    parser.add_argument("--batch-size", type=int, default=50)
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

    exp_parser = sub.add_parser("export-preimage",
                                help="export pre-images of candidate rows (no writes)")
    exp_parser.add_argument("--db-path", required=True)
    exp_parser.add_argument("--pre-export-dir")

    verify_parser = sub.add_parser("verify",
                                   help="per-row decode readback对照 (read-only)")
    verify_parser.add_argument("--db-path", required=True)
    verify_parser.add_argument("--require-complete", action="store_true",
                               help="also fail when unmigrated rows remain")

    rb_parser = sub.add_parser("rollback",
                               help="restore pre-convert state from a pre-export file")
    rb_parser.add_argument("--db-path", required=True)
    rb_parser.add_argument("--export-file", required=True)
    rb_parser.add_argument("--export-sha256")
    rb_parser.add_argument("--health-url", default="http://127.0.0.1:8000")
    rb_parser.add_argument("--production-authorized", action="store_true")
    rb_parser.add_argument("--copy-rehearsal", action="store_true")

    rp_parser = sub.add_parser("restore-plaintext",
                               help="write decompressed bytes back to明文 (P5b reverse)")
    rp_parser.add_argument("--db-path", required=True)
    rp_parser.add_argument("--health-url", default="http://127.0.0.1:8000")
    rp_parser.add_argument("--production-authorized", action="store_true")
    rp_parser.add_argument("--copy-rehearsal", action="store_true")

    clr_parser = sub.add_parser("clear-plaintext",
                                help="P5b: backup, per-row verify, clear明文")
    clr_parser.add_argument("--db-path", required=True)
    clr_parser.add_argument("--backup-dir")
    clr_parser.add_argument("--vacuum-into")
    clr_parser.add_argument("--health-url", default="http://127.0.0.1:8000")
    clr_parser.add_argument("--production-authorized", action="store_true")
    clr_parser.add_argument("--copy-rehearsal", action="store_true")
    clr_parser.add_argument("--allow-clear", action="store_true",
                            help="explicit P5b acknowledgment (requires 总控签字)")

    args = vars(parser.parse_args())
    command = args.pop("command")
    args.pop("verbose")
    try:
        if command == "export-preimage":
            run_export_preimage(args["db_path"],
                                export_dir=args.get("pre_export_dir"))
            return 0
        if command == "verify":
            result = verify_compressed(args["db_path"],
                                       require_complete=args.get("require_complete", False))
            return 0 if result["ok"] else 1
        if command == "rollback":
            result = rollback_from_export(
                args["db_path"], args["export_file"],
                sha256=args.get("export_sha256"), health_url=args["health_url"],
                production_authorized=args.get("production_authorized", False),
                copy_rehearsal=args.get("copy_rehearsal", False))
            if result.get("skipped"):
                return 0
            if result.get("mismatched") or result.get("conflicts") or result.get("missing"):
                return 1
            return 0
        if command == "restore-plaintext":
            result = restore_plaintext(
                args["db_path"], health_url=args["health_url"],
                production_authorized=args.get("production_authorized", False),
                copy_rehearsal=args.get("copy_rehearsal", False))
            if result.get("skipped"):
                return 0
            return 0 if not result.get("failed") else 1
        if command == "clear-plaintext":
            result = clear_plaintext(
                args["db_path"], backup_dir=args.get("backup_dir"),
                vacuum_into_path=args.get("vacuum_into"),
                health_url=args["health_url"],
                production_authorized=args.get("production_authorized", False),
                copy_rehearsal=args.get("copy_rehearsal", False),
                allow_clear=args.get("allow_clear", False))
            if result.get("skipped"):
                return 0
            if result.get("failed"):
                return 1
            vac = result.get("vacuum_verify")
            if isinstance(vac, dict) and (
                    vac.get("integrity") != "ok" or vac.get("view_mismatches")
                    or vac.get("missing_in_new")
                    or vac.get("old_rows") != vac.get("new_rows")):
                return 1
            return 0
        vacuum_target = args.pop("vacuum_into")
        result = run_convert(**args)
        if result.get("skipped"):
            return 0
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
        print("B-6c refused: " + type(exc).__name__ + " " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
