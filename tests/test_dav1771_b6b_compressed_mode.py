"""DAV-1771 B-6b: compressed-mode (REPORT_STORAGE_MODE=compressed) acceptance.

Must run in a fresh process — the mode binds the ORM column at
``api.database`` import time. The suite skips automatically when the env is
not set, and ``run_compressed_acceptance.sh`` (work/) invokes it with
``REPORT_STORAGE_MODE=compressed DATABASE_URL=<isolated tmpdb>``.

Card acceptance: rows stored the OLD way (plaintext) and the NEW way
(compressed) read back **byte-identical** through the interface; list-page
50-row fragment fetch no slower than json_extract.
"""

import json
import os
import statistics
import time

import pytest
import zstandard
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.skipif(
    os.getenv("REPORT_STORAGE_MODE", "plaintext") != "compressed",
    reason="requires REPORT_STORAGE_MODE=compressed subprocess",
)

from api.database import Base, ReportDB  # noqa: E402  (import after env)
from api.services import report_service  # noqa: E402
from tradingagents.storage import compressed_json as cj  # noqa: E402


def _session(url):
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _dual_result():
    return {
        "short_term": {
            "trade_action": "NO_TRADE", "analysis_status": "VALID",
            "risk_status": "OK", "direction": "BEAR",
            "decision_status": {"reason_codes": ["price_basis_gate_blocked"]},
            "reason_codes": ["manager_terminal"],
            "price_basis_gate": {"status": "blocked"},
            "status": "completed", "confidence": 40,
            "target_price": 50.0, "stop_loss_price": 45.0,
            "pre_gate_trade_action": "SELL",
            "manager_verdict": {"trade_action": "SELL"},
        },
        "medium_term": {"trade_action": "HOLD",
                        "manager_verdict": {"trade_action": "HOLD"}},
        "decision_status": {"reason_codes": ["top_a", "top_b"]},
        "reason_codes": ["top_a", "top_b"],
        "confidence": 60, "probability": 0.7,
        "target_price": 55.0, "stop_loss_price": 44.0,
    }


def test_compressed_mode_orm_read_returns_dict(tmp_path):
    db, engine = _session(f"sqlite:///{tmp_path}/c.db")
    rd = _dual_result()
    db.add(ReportDB(id="c1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    # raw: both columns populated, decode(zst) == plaintext bytes
    with engine.connect() as c:
        zst, plain = c.execute(text(
            "SELECT result_data_zst, result_data FROM reports WHERE id='c1'")).fetchone()
    assert isinstance(zst, (bytes, memoryview))
    assert cj.decode_frame_bytes(bytes(zst)) == plain.encode("utf-8")
    # ORM read → dict
    rep = db.get(ReportDB, "c1")
    assert rep.result_data == rd


def test_new_and_legacy_rows_read_byte_identical_through_interface(tmp_path):
    """A row written by OLD code (plaintext-only columns filled) and a row
    written by NEW code (compressed + shadow plaintext) return the same
    result_data dict via the same ORM read."""
    db, engine = _session(f"sqlite:///{tmp_path}/m.db")
    rd = _dual_result()
    # OLD row: only the plaintext column populated — what pre-B-6b code
    # produced (zst NULL, pg cols NULL).
    with engine.connect() as c:
        c.execute(text(
            "INSERT INTO reports (id, symbol, trade_date, status, result_data)"
            " VALUES ('old1', 'S', '2026-01-01', 'completed', :j)"),
            {"j": json.dumps(rd)})
        c.commit()
    # NEW row via ORM
    db.add(ReportDB(id="new1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    old = db.get(ReportDB, "old1")
    new = db.get(ReportDB, "new1")
    # byte-identical at the dict level (interface contract)
    assert old.result_data == new.result_data == rd


def test_compressed_mode_fragments_from_materialized_columns(tmp_path):
    db, _ = _session(f"sqlite:///{tmp_path}/f.db")
    rd = _dual_result()
    db.add(ReportDB(id="f1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    frag = report_service.load_post_gate_fragments(db, ["f1"])["f1"]
    assert frag["short_term"]["trade_action"] == "NO_TRADE"
    assert frag["short_term"]["manager_verdict"] == {"trade_action": "SELL"}
    assert frag["short_term"]["pre_gate_trade_action"] == "SELL"
    assert frag["decision_status"] == {"reason_codes": ["top_a", "top_b"]}
    assert frag["reason_codes"] == ["top_a", "top_b"]
    assert frag["confidence"] == 60


def test_compressed_mode_update_flag_modified(tmp_path):
    from sqlalchemy.orm.attributes import flag_modified
    db, engine = _session(f"sqlite:///{tmp_path}/u.db")
    db.add(ReportDB(id="u1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data={"a": 1}))
    db.commit()
    rep = db.get(ReportDB, "u1")
    rep.result_data["a"] = 999
    flag_modified(rep, "result_data")
    db.commit()
    with engine.connect() as c:
        zst, plain = c.execute(text(
            "SELECT result_data_zst, result_data FROM reports WHERE id='u1'")).fetchone()
    assert json.loads(cj.decode_frame_bytes(bytes(zst)))["a"] == 999
    assert json.loads(plain)["a"] == 999  # shadow kept byte-identical


def test_plaintext_fallback_edge_row(tmp_path):
    """Edge row (zst NULL, plaintext present) decodes via the deferred
    plaintext column — migration-window safety net."""
    db, engine = _session(f"sqlite:///{tmp_path}/e.db")
    rd = {"edge": True, "confidence": 5}
    with engine.connect() as c:
        c.execute(text(
            "INSERT INTO reports (id, symbol, trade_date, status, result_data)"
            " VALUES ('edge1', 'S', '2026-01-01', 'completed', :j)"),
            {"j": json.dumps(rd)})
        c.commit()
    rep = db.get(ReportDB, "edge1")
    assert rep.result_data == rd


def test_null_literal_row_decodes_to_none(tmp_path):
    """The 318 'null'-text rows (never migrated) read as None — same as
    json.loads('null')."""
    db, engine = _session(f"sqlite:///{tmp_path}/n.db")
    with engine.connect() as c:
        c.execute(text(
            "INSERT INTO reports (id, symbol, trade_date, status, result_data)"
            " VALUES ('n1', 'S', '2026-01-01', 'completed', 'null')"))
        c.commit()
    rep = db.get(ReportDB, "n1")
    assert rep.result_data is None


def test_get_report_detached_shadow_still_works(tmp_path):
    """get_report's detached-shadow + compat-view path is unchanged."""
    db, _ = _session(f"sqlite:///{tmp_path}/g.db")
    rd = _dual_result()
    db.add(ReportDB(id="g1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    rep = report_service.get_report(db, "g1")
    assert rep is not None
    assert rep.result_data.get("short_term", {}).get("trade_action") == "NO_TRADE"
    # compat view adds horizons alias on canonical rows — non-destructive
    assert isinstance(rep.result_data, dict)
