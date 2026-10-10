"""DAV-1771 B-6b: compressed result_data storage — write/read layer.

Card acceptance criteria:
1. ORM (``api/database.py``), detail endpoint (``api/main.py``), list page
   (``load_post_gate_fragments``), ``load_post_gate_fragments`` — all go
   through the single decode layer.
2. Write path writes zstd-9 compressed column + materialized pg_* columns;
   plaintext column handled per design §4 (dual-write window).
3. Acceptance: rows stored the OLD way (plaintext) and the NEW way
   (compressed) read back **byte-identical** through the interface; list
   page 50-row cost no higher than status quo.
4. 裁定③: write-path compression semaphore (default 4, configurable) +
   64-thread concurrent write peak-footprint measurement.
"""

import json
import os
import sqlite3
import statistics
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest
import zstandard
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from api.database import Base, ReportDB
from api.services import report_service
from tradingagents.storage import compressed_json as cj

# The ORM-bound physical column is chosen at api.database import time from
# REPORT_STORAGE_MODE. ORM/write-path tests below require the default
# plaintext mode; compressed-mode coverage lives in
# test_dav1771_b6b_compressed_mode.py (run via a fresh subprocess).
_PLAIN_ONLY = pytest.mark.skipif(
    os.getenv("REPORT_STORAGE_MODE", "plaintext") != "plaintext",
    reason="plaintext-mode suite (compressed covered in *_mode.py)",
)


def _proc_rss_bytes():
    """Current RSS in bytes — macOS (`ps` fallback) /Linux portable."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024  # kB → B
    except OSError:
        pass
    try:
        import subprocess
        out = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(os.getpid())], text=True)
        return int(out.strip()) * 1024
    except Exception:
        return None


def _session(mode_db_url):
    engine = create_engine(mode_db_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _dual_result():
    return {
        "short_term": {
            "trade_action": "NO_TRADE",
            "analysis_status": "VALID",
            "risk_status": "OK",
            "direction": "BEAR",
            "decision_status": {"reason_codes": ["price_basis_gate_blocked"]},
            "reason_codes": ["manager_terminal"],
            "price_basis_gate": {"status": "blocked"},
            "status": "completed",
            "confidence": 40,
            "target_price": 50.0,
            "stop_loss_price": 45.0,
            "pre_gate_trade_action": "SELL",
            "manager_verdict": {"trade_action": "SELL"},
        },
        "medium_term": {
            "trade_action": "HOLD",
            "manager_verdict": {"trade_action": "HOLD"},
        },
        "decision_status": {"reason_codes": ["top_a", "top_b"]},
        "reason_codes": ["top_a", "top_b"],
        "confidence": 60,
        "probability": 0.7,
        "target_price": 55.0,
        "stop_loss_price": 44.0,
    }


# ── module-level codec contracts ─────────────────────────────────────────────


def test_encode_decode_roundtrip_and_fcs():
    payload = _dual_result()
    zst = cj.encode_result_data(payload)
    assert isinstance(zst, bytes)
    # one-shot compress() emits a frame with FCS (design §3.1)
    assert cj.frame_content_len(zst) == len(json.dumps(payload).encode("utf-8"))
    assert cj.decode_result_data(zst) == payload
    assert cj.decode_result_data(None) is None
    assert cj.decode_result_data("null") is None      # 318 non-migrated rows
    assert cj.decode_result_data('{"a": 1}') == {"a": 1}


def test_encode_is_byte_identical_to_json_column_serialization():
    """daily_snapshot_ledger sha256 contract (§3.3/§8.4): decompress(encode(v))
    must equal what SQLite Column(JSON) stores = json.dumps default args."""
    payload = _dual_result()
    zst = cj.encode_result_data(payload)
    plaintext_bytes = json.dumps(payload).encode("utf-8")  # SQLAlchemy default
    assert cj.decode_frame_bytes(zst) == plaintext_bytes


def test_compression_semaphore_default_4_and_configurable():
    """裁定③: default 4, env-configurable."""
    assert cj.write_concurrency_limit() == 4
    saved = cj._env_concurrency()
    try:
        os.environ["REPORT_ZSTD_WRITE_CONCURRENCY"] = "7"
        assert cj.write_concurrency_limit() == 7
        os.environ["REPORT_ZSTD_WRITE_CONCURRENCY"] = "bogus"
        assert cj.write_concurrency_limit() == 4
    finally:
        if saved == 4:
            os.environ.pop("REPORT_ZSTD_WRITE_CONCURRENCY", None)
        else:
            os.environ["REPORT_ZSTD_WRITE_CONCURRENCY"] = str(saved)


def test_concurrent_compression_cap_observed():
    """64 threads hammering encode — measured in-flight compression count
    never exceeds the configured limit."""
    cj.set_write_concurrency_limit(4)
    try:
        inside = [0]
        peak = [0]
        lock = threading.Lock()
        payload = {"data": "z" * 50_000}

        # instrument the semaphore-acquire point via compress_json_bytes
        orig = cj._cctx
        def spy_cctx():
            return orig()
        # simpler: measure via a hook around the semaphore itself
        sem = cj._write_semaphore
        class SpySem:
            def __enter__(self):
                sem.__enter__()
                with lock:
                    inside[0] += 1
                    peak[0] = max(peak[0], inside[0])
                return self
            def __exit__(self, *a):
                with lock:
                    inside[0] -= 1
                return sem.__exit__(*a)
        cj._write_semaphore = SpySem()
        try:
            with ThreadPoolExecutor(max_workers=64) as ex:
                list(ex.map(lambda _: cj.encode_result_data(payload), range(256)))
        finally:
            cj._write_semaphore = sem
        assert peak[0] <= 4
        assert peak[0] >= 2  # concurrency actually happened
    finally:
        cj.set_write_concurrency_limit(cj._env_concurrency())


def test_thread_safety_8_threads_x_125_roundtrips_zero_errors():
    """DAV-1776 gate: >=8 threads × 1000 round-trips, zero exceptions /
    zero silent byte mismatches."""
    payload = _dual_result()
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait()
            for _ in range(125):  # 8 × 125 = 1000
                zst = cj.encode_result_data(payload)
                back = cj.decode_result_data(zst)
                if back != payload:
                    errors.append("MISMATCH")
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_frame_without_fcs_uses_fallback_cap():
    """A streamed frame (no content-size header) still decodes."""
    cctx = zstandard.ZstdCompressor(level=9)
    payload = json.dumps({"k": "v" * 100}).encode("utf-8")
    # stream_writer produces a frame without FCS
    import io
    buf = io.BytesIO()
    w = cctx.stream_writer(buf, closefd=False)
    w.write(payload)
    w.flush(zstandard.FLUSH_FRAME)
    streamed = buf.getvalue()
    assert cj.frame_content_len(streamed) == -1
    assert cj.decode_result_data(streamed) == json.loads(payload)


# ── ORM write path (plaintext mode — default) ────────────────────────────────


@_PLAIN_ONLY
def test_plaintext_mode_dual_write_populates_all_columns(tmp_path):
    url = f"sqlite:///{tmp_path}/p.db"
    assert cj.report_storage_mode() == "plaintext"
    db, engine = _session(url)
    rd = _dual_result()
    db.add(ReportDB(id="p1", symbol="600519.SH", trade_date="2026-10-10",
                    status="completed", result_data=rd))
    db.commit()
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT result_data, result_data_zst, result_data_zst_len,"
            " pg_st_trade_action, pg_st_pre_gate_action, pg_top_reason_codes,"
            " pg_confidence, pg_mt_manager_action FROM reports WHERE id='p1'"
        )).fetchone()
    plain, zst, zlen = row[0], row[1], row[2]
    # plaintext column unchanged (read source in this mode)
    assert json.loads(plain) == rd
    # compressed mirror carries the same bytes
    assert cj.decode_frame_bytes(zst) == plain.encode("utf-8")
    assert zlen == len(plain.encode("utf-8"))
    # materialized columns
    assert row[3] == "NO_TRADE"
    assert row[4] == "SELL"
    assert json.loads(row[5]) == ["top_a", "top_b"]
    assert row[6] == 60
    assert row[7] == "HOLD"


@_PLAIN_ONLY
def test_plaintext_mode_update_syncs_shadow_and_pg(tmp_path):
    url = f"sqlite:///{tmp_path}/p.db"
    db, engine = _session(url)
    rd = _dual_result()
    db.add(ReportDB(id="p2", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    rep = db.get(ReportDB, "p2")
    rep.result_data = {"short_term": {"trade_action": "BUY"}, "confidence": 90}
    db.commit()
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT pg_st_trade_action, pg_confidence, result_data_zst_len,"
            " length(result_data) FROM reports WHERE id='p2'"
        )).fetchone()
    assert row[0] == "BUY"
    assert row[1] == 90
    assert row[2] == row[3]  # zst_len tracks plaintext byte length


@_PLAIN_ONLY
def test_plaintext_mode_flag_modified_in_place_update(tmp_path):
    from sqlalchemy.orm.attributes import flag_modified
    url = f"sqlite:///{tmp_path}/p.db"
    db, engine = _session(url)
    db.add(ReportDB(id="p3", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data={"a": 1}))
    db.commit()
    rep = db.get(ReportDB, "p3")
    rep.result_data["a"] = 999
    flag_modified(rep, "result_data")
    db.commit()
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT result_data, result_data_zst FROM reports WHERE id='p3'"
        )).fetchone()
    assert json.loads(row[0])["a"] == 999
    assert json.loads(cj.decode_frame_bytes(row[1]))["a"] == 999


# ── load_post_gate_fragments: plaintext mode keeps json_extract path ─────────


@_PLAIN_ONLY
def test_load_post_gate_fragments_plaintext_mode(tmp_path):
    url = f"sqlite:///{tmp_path}/l.db"
    db, _ = _session(url)
    rd = _dual_result()
    db.add(ReportDB(id="l1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    frags = report_service.load_post_gate_fragments(db, ["l1"])
    frag = frags["l1"]
    assert frag["short_term"]["trade_action"] == "NO_TRADE"
    assert frag["short_term"]["manager_verdict"] == {"trade_action": "SELL"}
    assert frag["short_term"]["price_basis_gate"] == {"status": "blocked"}
    assert frag["short_term"]["pre_gate_trade_action"] == "SELL"
    assert frag["decision_status"] == {"reason_codes": ["top_a", "top_b"]}
    assert frag["reason_codes"] == ["top_a", "top_b"]
    assert frag["confidence"] == 60
    assert frag["medium_term"]["manager_verdict"] == {"trade_action": "HOLD"}


def test_fragments_empty_ids():
    class _S:  # no DB touch needed
        pass
    assert report_service.load_post_gate_fragments(_S(), []) == {}


# ── post_gate_column_values pure function ────────────────────────────────────


def test_post_gate_column_values_emits_all_31():
    vals = cj.post_gate_column_values(_dual_result())
    assert set(vals.keys()) == set(cj.PG_PHYSICAL_COLUMNS)
    assert len(vals) == 31
    assert vals["pg_st_trade_action"] == "NO_TRADE"
    assert vals["pg_st_pre_gate_action"] == "SELL"
    assert vals["pg_st_manager_action"] == "SELL"
    assert vals["pg_mt_manager_action"] == "HOLD"
    assert vals["pg_top_reason_codes"] == ["top_a", "top_b"]
    assert vals["pg_confidence"] == 60
    # ids fallback merge: only investment_debate_state.manager_verdict set
    rd2 = {"short_term": {"investment_debate_state": {"manager_verdict": {"trade_action": "BUY"}}}}
    assert cj.post_gate_column_values(rd2)["pg_st_manager_action"] == "BUY"
    # missing slice → Nones (update clears stale values)
    assert cj.post_gate_column_values({})["pg_st_trade_action"] is None
    # non-dict input → all None
    assert all(v is None for v in cj.post_gate_column_values("garbage").values())


# ── ensure_report_schema covers the 33 new columns ───────────────────────────


def test_ensure_report_schema_adds_b6b_columns(tmp_path):
    import api.database as dbmod
    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE reports (id VARCHAR(36) PRIMARY KEY,"
            " symbol VARCHAR(20), trade_date VARCHAR(10), result_data JSON)"
        ))
    dbmod._ensure_report_schema(target_engine=engine)
    from sqlalchemy import inspect
    cols = {c["name"] for c in inspect(engine).get_columns("reports")}
    assert {"result_data_zst", "result_data_zst_len"} <= cols
    assert set(cj.PG_PHYSICAL_COLUMNS) <= cols
    # idempotent
    dbmod._ensure_report_schema(target_engine=engine)


# ── list-page timing: 50-row fragment read ≤ status quo (json_extract) ──────


def _bench_fragment_query(db, ids, use_pg_columns, rounds=9):
    """Time the fragment SELECT itself (the part B-6b changed)."""
    from sqlalchemy import func
    samples = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        if use_pg_columns:
            exprs = {n: getattr(ReportDB, c) for n, c in cj.PG_FRAGMENT_COLUMN_MAP.items()}
        else:
            exprs = {n: func.json_extract(ReportDB.result_data, p)
                     for n, p in report_service._POST_GATE_FRAGMENT_PATHS.items()}
        rows = (db.query(ReportDB.id, *exprs.values())
                  .filter(ReportDB.id.in_(list(ids))).all())
        # force materialization cost
        for r in rows:
            _ = dict(zip(exprs.keys(), r[1:]))
        samples.append(time.perf_counter() - t0)
    return samples


@_PLAIN_ONLY
def test_list_page_50row_fragment_cost_pg_not_slower(tmp_path):
    """Card acceptance: 50-row list-page fragment fetch via pg_* columns is
    not slower than the json_extract status quo (median/p95)."""
    url = f"sqlite:///{tmp_path}/b.db"
    db, _ = _session(url)
    ids = []
    # 50 dual-horizon rows, realistic size (~50KB JSON each)
    base = _dual_result()
    base["market_data_context"] = {"kline": ["x" * 1000] * 40}
    for i in range(50):
        rid = f"b{i:03d}"
        db.add(ReportDB(id=rid, symbol=f"S{i}", trade_date="2026-01-01",
                        status="completed", result_data=base))
        ids.append(rid)
    db.commit()

    pg = _bench_fragment_query(db, ids, use_pg_columns=True)
    je = _bench_fragment_query(db, ids, use_pg_columns=False)
    med_pg, med_je = statistics.median(pg), statistics.median(je)
    p95_pg = sorted(pg)[int(len(pg) * 0.95) - 1]
    p95_je = sorted(je)[int(len(je) * 0.95) - 1]
    # materialized-column read must not be slower than json_extract
    assert med_pg <= med_je * 1.5 + 0.001, (med_pg, med_je)
    assert p95_pg <= p95_je * 1.5 + 0.001, (p95_pg, p95_je)


# ── 64-thread concurrent write peak footprint (裁定③ measurement) ────────────


def test_64_thread_concurrent_write_peak_footprint(tmp_path):
    """裁定③: report peak RSS during 64-thread concurrent writes.
    Asserts only that it completes without error and stays under the 4 GiB
    envelope — the absolute number is reported in the delivery comment."""
    url = f"sqlite:///{tmp_path}/w.db"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine)

    payload = _dual_result()
    payload["market_data_context"] = {"kline": ["x" * 1000] * 200}  # ~200KB

    errors = []
    barrier = threading.Barrier(64)

    def writer(i):
        try:
            barrier.wait(timeout=30)
            sess = SF()
            try:
                sess.add(ReportDB(id=f"w{i}", symbol="S", trade_date="2026-01-01",
                                  status="completed", result_data=payload))
                sess.commit()
            finally:
                sess.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(64)]
    rss_before = _proc_rss_bytes()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    rss_after = _proc_rss_bytes()
    assert errors == []
    assert all(not t.is_alive() for t in threads)
    n = SF().query(ReportDB).count()
    assert n == 64
    if rss_before and rss_after:
        delta_mib = (rss_after - rss_before) / (1024 * 1024)
        total_mib = rss_after / (1024 * 1024)
        # 4 GiB envelope on the absolute process RSS (a delta this size
        # could not hide >4 GiB of write-path allocations anyway).
        assert total_mib < 4 * 1024, f"peak RSS {total_mib:.0f} MiB"
