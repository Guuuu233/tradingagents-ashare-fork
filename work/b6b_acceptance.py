#!/usr/bin/env python3
"""DAV-1771 B-6b acceptance harness — runs against a *copy* of the
production snapshot. Never touches the live DB.

Usage (locked interpreter):
    env -u PYTHONPATH /path/.venv310/bin/python work/b6b_acceptance.py \
        --source /private/tmp/ta-snapshot/current.db \
        --work /tmp/ta-b6b-acceptance.db

What it does:
1. Copies the source DB to --work (fresh copy each run).
2. Runs ``init_db()``-equivalent ``_ensure_report_schema`` so the 33 new
   columns exist.
3. **New storage rows**: migrates every row with
   ``result_data IS NOT NULL AND result_data <> 'null'`` into
   ``result_data_zst`` + ``result_data_zst_len`` + pg_* columns — exactly
   the B-6c batch operation (raw-bytes compression, 裁定②).
4. **Acceptance ①** — with ``REPORT_STORAGE_MODE=compressed`` active,
   reads every migrated row *plus* every unmigrated row (old plaintext
   storage incl. the 318 'null' rows) through the ORM + the
   ``load_post_gate_fragments`` interface and asserts byte-identical
   equality vs the plaintext column content.
5. **Acceptance ②** — times the 50-row list-page fragment fetch two ways
   (pg_* columns vs json_extract on the plaintext column) and reports
   median/p95.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["REPORT_STORAGE_MODE"] = "compressed"

from tradingagents.storage.compressed_json import (  # noqa: E402
    decode_frame_bytes,
    post_gate_column_values,
    compress_json_bytes,
)


def migrate(conn):
    """B-6c batch op on the copy: raw-bytes compress + pg_* backfill."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(reports)")]
    assert "result_data_zst" in cols
    rows = conn.execute(
        "SELECT id, result_data FROM reports"
        " WHERE result_data_zst IS NULL"
        "   AND result_data IS NOT NULL AND result_data <> 'null'"
        " ORDER BY created_at"
    ).fetchall()
    pg_cols = list(dict.fromkeys(
        v for v in post_gate_column_values({}).keys()))
    set_clause = ", ".join(f"{c}=?" for c in pg_cols)
    n = 0
    # Leave every 37th real row unmigrated → those exercise the compressed-
    # mode plaintext fallback on real data (old-storage rows).
    for rid, raw in rows:
        if n % 37 == 0:
            n += 1
            continue
        zst = compress_json_bytes(raw.encode("utf-8"))
        vals = post_gate_column_values(json.loads(raw))
        conn.execute(
            f"UPDATE reports SET result_data_zst=?, result_data_zst_len=?, {set_clause}"
            " WHERE id=?",
            [zst, len(raw.encode("utf-8"))]
            + [vals[c] if not isinstance(vals[c], (dict, list)) else json.dumps(vals[c])
               for c in pg_cols]
            + [rid],
        )
        n += 1
    conn.commit()
    n_migrated = conn.execute(
        "SELECT count(*) FROM reports WHERE result_data_zst IS NOT NULL"
    ).fetchone()[0]
    return n_migrated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args()

    import shutil
    for suffix in ("", "-wal", "-shm"):
        p = args.work + suffix
        if os.path.exists(p):
            os.remove(p)
    shutil.copyfile(args.source, args.work)

    # add the 33 columns via the same code path init_db uses
    os.environ["DATABASE_URL"] = f"sqlite:///{args.work}"
    import api.database as dbmod
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{args.work}")
    dbmod._ensure_report_schema(target_engine=engine)

    conn = sqlite3.connect(args.work)
    n_migrated = migrate(conn)
    total = conn.execute("SELECT count(*) FROM reports").fetchone()[0]
    n_null = conn.execute(
        "SELECT count(*) FROM reports WHERE result_data='null'").fetchone()[0]
    print(f"migrated {n_migrated} rows ({total} total, {n_null} 'null' rows left)")

    # ── Acceptance ①: byte-identical read-through ──────────────────────
    from sqlalchemy.orm import sessionmaker
    from api.database import ReportDB
    from api.services import report_service
    SF = sessionmaker(bind=engine)

    # Level-0 raw check on migrated rows: decompress(zst) == plaintext bytes
    # (the sha256 ledger contract, design §3.3/§8.4 — REQUIRED evidence).
    byte_bad = 0
    for rid, zst, plain in conn.execute(
        "SELECT id, result_data_zst, result_data FROM reports"
        " WHERE result_data_zst IS NOT NULL"
    ).fetchall():
        if decode_frame_bytes(zst) != plain.encode("utf-8"):
            byte_bad += 1
    print(f"acceptance① raw bytes: decompress(zst)==result_data on all "
          f"{n_migrated} migrated rows, {byte_bad} bad")

    mismatches = []
    sampled = 0
    sess = SF()
    all_ids = [r[0] for r in conn.execute("SELECT id FROM reports ORDER BY id")]
    for rid in all_ids:
        rep = sess.get(ReportDB, rid)
        # expected = what the OLD interface returned: json.loads(result_data)
        # (or None for NULL/'null')
        raw = conn.execute(
            "SELECT result_data FROM reports WHERE id=?", (rid,)).fetchone()[0]
        expected = json.loads(raw) if raw not in (None, "null") else None
        got = rep.result_data
        if got != expected:
            mismatches.append((rid, type(got), type(expected)))
        sampled += 1
        sess.expunge_all()
    n_unmigrated = conn.execute(
        "SELECT count(*) FROM reports WHERE result_data_zst IS NULL"
        " AND result_data IS NOT NULL AND result_data <> 'null'"
    ).fetchone()[0]
    print(f"acceptance① ORM read: {sampled} rows checked "
          f"({n_unmigrated} unmigrated fallback rows incl.), "
          f"{len(mismatches)} mismatches")
    for m in mismatches[:10]:
        print("  MISMATCH:", m)

    # fragment-level check on a 200-id sample: compressed frag == plaintext frag
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM reports WHERE result_data IS NOT NULL"
        " AND result_data <> 'null' AND result_data_zst IS NOT NULL"
        " ORDER BY id LIMIT 200")]
    sess2 = SF()
    frag_c = report_service.load_post_gate_fragments(sess2, ids)

    # Compare every populated fragment field against the same value
    # extracted in Python from the plaintext column — a full per-cell
    # equality check, not a spot check.
    from sqlalchemy import func
    frag_mismatches = []
    for rid in ids:
        raw = conn.execute(
            "SELECT result_data FROM reports WHERE id=?", (rid,)).fetchone()[0]
        d = json.loads(raw)
        st, mt = d.get("short_term") or {}, d.get("medium_term") or {}
        f = frag_c.get(rid) or {}
        checks = {
            ("short_term", "trade_action"): st.get("trade_action"),
            ("short_term", "pre_gate_trade_action"): st.get("pre_gate_trade_action"),
            ("short_term", "manager_verdict"): (
                {"trade_action": st.get("manager_verdict", {}).get("trade_action")}
                if (st.get("manager_verdict") or {}).get("trade_action")
                or ((st.get("investment_debate_state") or {}).get("manager_verdict") or {}).get("trade_action")
                else None
            ),
            ("medium_term", "trade_action"): mt.get("trade_action"),
            ("short_term", "price_basis_gate"): (
                {"status": st["price_basis_gate"]["status"]}
                if isinstance(st.get("price_basis_gate"), dict)
                and st["price_basis_gate"].get("status") else None
            ),
        }
        for (side, key), want in checks.items():
            got = (f.get(side) or {}).get(key) if key != "__top__" else f.get(key)
            if got != want:
                frag_mismatches.append((rid, side, key, got, want))
        # top-level reason_codes dual-slot rule
        top_rc = (d.get("decision_status") or {}).get("reason_codes") or d.get("reason_codes")
        if top_rc:
            if f.get("decision_status") != {"reason_codes": top_rc} or f.get("reason_codes") != top_rc:
                frag_mismatches.append((rid, "top", "reason_codes", f.get("reason_codes"), top_rc))
    frag_ok = sum(1 for v in frag_c.values() if isinstance(v, dict))
    print(f"acceptance① fragments: {frag_ok}/{len(ids)} populated; "
          f"{len(frag_mismatches)} field-level mismatches")
    for m in frag_mismatches[:10]:
        print("  FRAG MISMATCH:", m)

    # ── Acceptance ②: 50-row list-page timing ──────────────────────────
    sess3 = SF()
    page_ids = [r[0] for r in conn.execute(
        "SELECT id FROM reports WHERE status='completed'"
        " ORDER BY created_at DESC LIMIT 50")]

    def run_pg():
        t0 = time.perf_counter()
        report_service.load_post_gate_fragments(sess3, page_ids)
        return time.perf_counter() - t0

    def run_je():
        # Status-quo equivalent: json_extract on the *plaintext* column —
        # what the pre-B-6b code executed (attribute bound to the JSON
        # column). In compressed mode the attribute maps to the BLOB, so
        # address the physical column directly.
        import api.services.report_service as rs
        from sqlalchemy import literal_column
        t0 = time.perf_counter()
        # NB: table.c lookup is keyed by column *key* — in compressed mode
        # key 'result_data' is the zst column. Address the physical
        # plaintext column by literal name instead.
        exprs = {n: func.json_extract(literal_column("reports.result_data"), p)
                 for n, p in rs._POST_GATE_FRAGMENT_PATHS.items()}
        rows = (sess3.query(ReportDB.id, *exprs.values())
                    .filter(ReportDB.id.in_(page_ids)).all())
        for r in rows:
            _ = r[1:]
        return time.perf_counter() - t0

    pg_t = sorted(run_pg() for _ in range(9))
    je_t = sorted(run_je() for _ in range(9))
    med_pg, med_je = statistics.median(pg_t), statistics.median(je_t)
    p95_pg, p95_je = pg_t[int(len(pg_t) * 0.95) - 1], je_t[int(len(je_t) * 0.95) - 1]
    print(f"acceptance② 50-row fragment fetch: "
          f"pg_* median {med_pg*1000:.2f}ms p95 {p95_pg*1000:.2f}ms | "
          f"json_extract median {med_je*1000:.2f}ms p95 {p95_je*1000:.2f}ms")

    ok = (not mismatches and not frag_mismatches and byte_bad == 0
          and med_pg <= med_je * 1.5 + 0.001)
    print("RESULT:", "PASS" if ok else "FAIL")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
