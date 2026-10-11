"""DAV-1904 B-6e acceptance DB builder.

Constructs two SQLite DBs with identical logical content:

* ``plain.db``   — full reports schema (via ``Base.metadata.create_all``)
  holding (a) v1 reject-stats corpus rows (dav1505) and (b) H1b v2
  qualifying sample rows (verify_h1b_gates), all with plaintext
  ``result_data``.
* ``cleared.db`` — byte-copy then every row's ``result_data`` compressed
  into ``result_data_zst`` (+ ``result_data_zst_len``) and the plaintext
  column set to NULL — the P5b end-state.

Both scripts under acceptance must produce **byte-identical** output on
the two DBs when run without REPORT_STORAGE_MODE.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from api.database import Base, ReportDB, engine, SessionLocal  # noqa: E402
from tradingagents.storage.compressed_json import compress_json_bytes  # noqa: E402
from scripts import dav1505_six_stage_reject_stats as dav1505  # noqa: E402
from tests.test_h1b_gates import _build_qualifying_sample_pool  # noqa: E402

V1 = dav1505.DECISION_MODEL_V1
ACCOUNT = dav1505.FIXED_USER_ID
HORIZONS = ("short_term", "medium_term")


def _claim(cid, side):
    return {"claim_id": cid, "speaker_key": side,
            "speaker": side.title() + " Analyst",
            "stance": "bullish" if side == "bull" else "bearish",
            "stage": "opening", "battlefield": "capital_flow",
            "claim": f"{side} {cid}"}


def _slot(prefix, *, direction, adopt_bull, adopt_bear):
    bull = [_claim(f"{prefix}B{i}", "bull") for i in range(2)]
    bear = [_claim(f"{prefix}S{i}", "bear") for i in range(2)]
    claims = bull + bear
    ces = {c["claim_id"]: {
        "decision": "adopt", "semantic_decision": "adopt",
        "counts": {"verified": 2, "total": 2}, "coverage": 1.0,
        "is_observation_or_hypothesis": False, "reason": "核验通过",
        "claim": c["claim"], "speaker_key": c["speaker_key"],
        "stance": c["stance"], "speaker": c["speaker"],
    } for c in claims}
    adopted = ([c["claim_id"] for c in bull] if adopt_bull else []) + \
              ([c["claim_id"] for c in bear] if adopt_bear else [])
    return {
        "investment_debate_state": {
            "claims": claims,
            "claim_cluster_metrics": {"double_count_guard_audit": {"excluded_claim_ids": []}},
        },
        "manager_verdict": {
            "direction": direction, "consistency_check_passed": True,
            "adopted_claim_ids": adopted, "claim_evidence_summary": ces,
        },
        "decision_status": {"reason_codes": [], "failed_checks": []},
        "price_basis_gate": {"status": "pass", "violations": []},
    }


_CATS = {"bull_only": (True, False), "bear_only": (False, True), "both": (True, True)}


def _v1_rd(rid, baseline, cat, direction):
    ab, ac = _CATS[cat]
    rd = {"decision_model_version": V1, "analysis_baseline_date": baseline}
    for hz in HORIZONS:
        rd[hz] = _slot(f"{hz[:2].upper()}{rid}", direction=direction,
                       adopt_bull=ab, adopt_bear=ac)
    return rd


def build_plain(path: Path) -> None:
    if path.exists():
        path.unlink()
    eng = __import__("sqlalchemy").create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng)
    db = __import__("sqlalchemy").orm.sessionmaker(bind=eng)()

    # v1 reject-stats corpus (dav1505) — mixed bull/bear/both + no-slot + legacy
    cats = ("bull_only", "bear_only", "both")
    dirs = ("看空", "看多", "看空")
    n = 0
    for i in range(12):
        n += 1
        bd = f"2026-05-{4 + i:02d}"
        db.add(ReportDB(
            id=f"v1r{n:04d}", symbol="000001", trade_date=bd,
            created_at=datetime.fromisoformat("2026-09-05T10:00:00"),
            user_id=ACCOUNT,
            status="completed",
            result_data=_v1_rd(f"v1r{n:04d}", bd, cats[i % 3], dirs[i % 3])))
    # NULL-version bucket rows (no decision_model_version)
    for i in range(3):
        rd = {"short_term": _slot(f"LEG{i}", direction="看空",
                                  adopt_bull=False, adopt_bear=True),
              "analysis_baseline_date": f"2026-03-0{i+1}"}
        db.add(ReportDB(
            id=f"leg{i}", symbol="LEGACY", trade_date=f"2026-03-0{i+1}",
            created_at=datetime.fromisoformat(f"2026-03-0{i+1}T09:00:00"),
            user_id=ACCOUNT,
            status="completed", result_data=rd))
    # 'null'-literal edge row
    db.add(ReportDB(id="nulllit", symbol="X", trade_date="2026-01-01",
                    created_at=datetime.fromisoformat("2026-01-01T00:00:00"),
                    user_id=ACCOUNT,
                    status="completed", result_data=None))
    db.commit()

    # H1b v2 qualifying pool (verify_h1b_gates, cohort legacy_unversioned)
    for i, rd in enumerate(_build_qualifying_sample_pool(40)):
        db.add(ReportDB(
            id=f"h1b{i:03d}", symbol=rd["symbol"], trade_date=rd["trade_date"],
            created_at=datetime.fromisoformat(rd["trade_date"] + "T15:00:00"),
            user_id=ACCOUNT,
            status="completed", analysis_status="VALID",
            trade_action=rd["trade_action"], result_data=rd))
    db.commit()
    eng.dispose()


def make_cleared(src: Path, dst: Path) -> None:
    if dst.exists():
        dst.unlink()
    shutil.copyfile(src, dst)
    con = sqlite3.connect(dst)
    rows = con.execute(
        "SELECT id, result_data FROM reports WHERE result_data IS NOT NULL"
    ).fetchall()
    for rid, txt in rows:
        raw = txt.encode("utf-8")
        con.execute(
            "UPDATE reports SET result_data=NULL, result_data_zst=?, "
            "result_data_zst_len=? WHERE id=?",
            (compress_json_bytes(raw), len(raw), rid))
    con.commit()
    con.close()


if __name__ == "__main__":
    out_dir = Path(__file__).resolve().parent
    plain = out_dir / "plain.db"
    cleared = out_dir / "cleared.db"
    build_plain(plain)
    make_cleared(plain, cleared)
    print("built:", plain, cleared)
