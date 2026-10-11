"""DAV-1885 P4: REPORT_STORAGE_MODE=compressed_only acceptance.

Must run in a fresh process — the mode binds the ORM column at
``api.database`` import time. Skip automatically unless the env is set;
invoke e.g.
``REPORT_STORAGE_MODE=compressed_only DATABASE_URL=sqlite:///<tmp>.db
  python -m pytest tests/test_dav1885_p4_compressed_only.py``.

Card acceptance (issue body):
1. ``compressed_only``: new/updated rows have plaintext column NULL and the
   compressed column authoritative; ORM read returns the same dict.
2. Switch back to ``compressed`` → dual-write restored (plaintext shadow
   re-populated byte-identical).
"""

import json
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.skipif(
    os.getenv("REPORT_STORAGE_MODE", "plaintext") != "compressed_only",
    reason="requires REPORT_STORAGE_MODE=compressed_only subprocess",
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


def test_mode_is_compressed_only():
    assert cj.report_storage_mode() == "compressed_only"
    assert cj.writes_plaintext() is False
    assert cj.writes_plaintext("compressed") is True
    assert cj.writes_plaintext("dual") is True
    assert cj.writes_plaintext("plaintext") is True


def test_new_row_plaintext_null_compressed_authoritative(tmp_path):
    """New write in compressed_only: result_data NULL, zst holds bytes."""
    db, engine = _session(f"sqlite:///{tmp_path}/n.db")
    rd = _dual_result()
    db.add(ReportDB(id="n1", symbol="600519.SH", trade_date="2026-10-11",
                    status="completed", result_data=rd))
    db.commit()
    with engine.connect() as c:
        plain, zst, zlen = c.execute(text(
            "SELECT result_data, result_data_zst, result_data_zst_len"
            " FROM reports WHERE id='n1'")).fetchone()
    # plaintext column is NULL (P4: 新写或更新行的明文列置 NULL)
    assert plain is None
    # compressed column carries the full JSON bytes
    assert isinstance(zst, (bytes, memoryview))
    assert json.loads(cj.decode_frame_bytes(bytes(zst))) == rd
    assert zlen == len(json.dumps(rd).encode("utf-8"))
    # ORM read returns the same dict
    rep = db.get(ReportDB, "n1")
    assert rep.result_data == rd


def test_update_row_plaintext_null_readback_unchanged(tmp_path):
    """Updating result_data also NULLs plaintext; readback is unchanged."""
    from sqlalchemy.orm.attributes import flag_modified
    db, engine = _session(f"sqlite:///{tmp_path}/u.db")
    rd = _dual_result()
    db.add(ReportDB(id="u1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    rep = db.get(ReportDB, "u1")
    rep.result_data["confidence"] = 99
    flag_modified(rep, "result_data")
    db.commit()
    with engine.connect() as c:
        plain, zst = c.execute(text(
            "SELECT result_data, result_data_zst FROM reports WHERE id='u1'")
        ).fetchone()
    assert plain is None
    assert json.loads(cj.decode_frame_bytes(bytes(zst)))["confidence"] == 99
    rep2 = db.get(ReportDB, "u1")
    assert rep2.result_data["confidence"] == 99


def test_pg_materialized_columns_still_written(tmp_path):
    """31 pg_* columns are still populated (they are the list-page read)."""
    db, engine = _session(f"sqlite:///{tmp_path}/p.db")
    rd = _dual_result()
    db.add(ReportDB(id="p1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT pg_st_trade_action, pg_mt_trade_action,"
            " pg_top_reason_codes, pg_confidence FROM reports WHERE id='p1'")
        ).fetchone()
    assert row[0] == "NO_TRADE"
    assert row[1] == "HOLD"
    assert json.loads(row[2]) == ["top_a", "top_b"]
    assert row[3] == 60


def test_post_gate_fragments_from_materialized_columns(tmp_path):
    """load_post_gate_fragments reads pg_* in compressed_only."""
    db, engine = _session(f"sqlite:///{tmp_path}/f.db")
    rd = _dual_result()
    db.add(ReportDB(id="f1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    frags = report_service.load_post_gate_fragments(db, ["f1"])
    assert frags["f1"]["short_term"]["trade_action"] == "NO_TRADE"
    assert frags["f1"]["medium_term"]["trade_action"] == "HOLD"


def test_plaintext_cleared_row_read_via_compressed(tmp_path):
    """A row whose plaintext was cleared (P5b-style) still reads correctly."""
    db, engine = _session(f"sqlite:///{tmp_path}/c.db")
    rd = _dual_result()
    db.add(ReportDB(id="c1", symbol="S", trade_date="2026-01-01",
                    status="completed", result_data=rd))
    db.commit()
    # simulate P5b: plaintext column cleared on this row
    with engine.connect() as c:
        c.execute(text("UPDATE reports SET result_data=NULL WHERE id='c1'"))
        c.commit()
    rep = db.get(ReportDB, "c1")
    assert rep.result_data == rd
