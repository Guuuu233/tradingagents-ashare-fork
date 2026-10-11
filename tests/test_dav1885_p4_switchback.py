"""DAV-1885 P4 rollback: switching REPORT_STORAGE_MODE back to ``compressed``
restores plaintext dual-write (card acceptance #2 / 回退验证).

Drives a fresh subprocess per phase because the mode binds the ORM column at
``api.database`` import time and cannot change in-process. Phase A seeds a
DB under compressed_only (plaintext NULL); Phase B reopens it under
``compressed`` and asserts (a) the old row still reads correctly via
COALESCE, (b) a new write dual-writes the plaintext shadow again.
"""

import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.skipif(
    os.getenv("REPORT_STORAGE_MODE", "plaintext") != "compressed",
    reason="switchback test runs the driver under REPORT_STORAGE_MODE=compressed",
)


def _phase(mode, db_url, phase):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["REPORT_STORAGE_MODE"] = mode
    env["DATABASE_URL"] = db_url
    code = _PHASE_SNIPPET[phase]
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env, capture_output=True, text=True, timeout=120,
    )


_PHASE_SNIPPET = {
    "seed": """
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from api.database import Base, ReportDB
import os
eng = create_engine(os.environ['DATABASE_URL'])
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()
db.add(ReportDB(id='sw1', symbol='S', trade_date='2026-01-01',
                status='completed', result_data={'x': 1, 'confidence': 7}))
db.commit()
""",
    "write_new": """
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from api.database import Base, ReportDB
import os
eng = create_engine(os.environ['DATABASE_URL'])
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()
db.add(ReportDB(id='sw2', symbol='S', trade_date='2026-01-01',
                status='completed', result_data={'y': 2}))
db.commit()
""",
}


def test_switch_back_to_compressed_restores_dual_write(tmp_path):
    db_url = f"sqlite:///{tmp_path}/sw.db"
    from api.database import Base, ReportDB  # noqa: E402
    from tradingagents.storage import compressed_json as cj

    # Phase A: write under compressed_only → plaintext NULL
    r = _phase("compressed_only", db_url, "seed")
    assert r.returncode == 0, r.stderr
    engine = create_engine(db_url)
    with engine.connect() as c:
        plain, zst = c.execute(text(
            "SELECT result_data, result_data_zst FROM reports WHERE id='sw1'")
        ).fetchone()
    assert plain is None
    assert zst is not None

    # Phase B: switch to compressed, add a new row → dual-write restored
    r = _phase("compressed", db_url, "write_new")
    assert r.returncode == 0, r.stderr
    with engine.connect() as c:
        plain, zst = c.execute(text(
            "SELECT result_data, result_data_zst FROM reports WHERE id='sw2'")
        ).fetchone()
    # plaintext shadow re-populated (dual write restored)
    assert json.loads(plain) == {"y": 2}
    assert cj.decode_frame_bytes(bytes(zst)) == plain.encode("utf-8")

    # old compressed_only row still reads correctly under compressed mode
    session = sessionmaker(bind=engine)()
    rep = session.get(ReportDB, "sw1")
    assert rep.result_data == {"x": 1, "confidence": 7}
