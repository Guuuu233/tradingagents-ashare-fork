"""DAV-1545 (存储 B-2): all save entries persist the authoritative slots only.

Write-side contract:

* every funnel through ``canonicalize_report_result_data`` ends canonical
  (``storage_schema_version == result_data.storage.v1``) with no physical
  ``horizons.<h>`` map and no aliased top-level ``market_data_context``;
* the job-level result builders stop emitting the legacy physical aliases;
* the B-1 compat view still reconstructs ``horizons.<h>`` and the top-level
  ``market_data_context`` read-side, so serialized responses keep the exact
  legacy shape;
* conflict payloads fail closed (``StorageCompatConflict``) instead of
  silently reconciling;
* the read-path zero-write guarantees from B-1 (detached shadow, no view
  write-back) do not regress.
"""

import copy
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, ReportDB
from api.services import report_service
from tradingagents.storage.result_data_compat import (
    STORAGE_SCHEMA_KEY,
    STORAGE_SCHEMA_VERSION,
    StorageCompatConflict,
    canonicalize_for_single_write,
    detect_alias_conflicts,
    expand_compat_view,
    is_canonical_storage,
    result_data_compat_view,
)


def _slice(horizon: str) -> dict:
    """Per-horizon payload as the current writer produces it (order matters)."""
    return {
        "horizon": horizon,
        "status": "completed",
        "trade_action": "BUY",
        "analysis_status": "VALID",
        "direction": "看多",
        "confidence": 80,
        "probability": 0.7,
        "manager_verdict": {"winner": "bull", "trade_action": "BUY"},
        "investment_debate_state": {"claims": [{"claim_id": "c1", "claim": "x"}]},
        "market_data_context": {"daily": {"as_of": "2026-10-02"}, "source_provenance": {}},
        "forecast": {"t_plus_5_date": None},
        "data_gaps": [],
        "decision_model_version": "decision_model.v1",
        "evidence_contract_version": "evidence_contract.v2",
        "generated_by_commit_sha": "a" * 40,
    }


def _dual_writer_payload() -> dict:
    """The payload the job writes after the B-2 construction change: only the
    authoritative ``*_term`` slices plus the (still-emitted) top-level
    per-horizon mdc map."""
    short, medium = _slice("short"), _slice("medium")
    return {
        "symbol": "600519.SH",
        "trade_date": "2026-10-02",
        "mode": "dual_horizon",
        "status": "completed",
        "requested_horizons": ["short", "medium"],
        "horizon_status": {"short": "completed", "medium": "completed"},
        "market_data_context": {
            "short": copy.deepcopy(short["market_data_context"]),
            "medium": copy.deepcopy(medium["market_data_context"]),
        },
        "social_data_context": {"short": {}, "medium": {}},
        "short_term": short,
        "medium_term": medium,
        "data_gaps": [],
    }


# ---------------------------------------------------------------------------
# write-side canonicalization


def test_dual_write_persists_authoritative_only():
    canon = canonicalize_for_single_write(_dual_writer_payload())
    assert canon[STORAGE_SCHEMA_KEY] == STORAGE_SCHEMA_VERSION
    assert "horizons" not in canon
    assert "market_data_context" not in canon
    assert canon["short_term"]["market_data_context"]
    assert canon["medium_term"]["market_data_context"]


def test_dual_write_view_roundtrip():
    rd = _dual_writer_payload()
    canon = canonicalize_for_single_write(rd)
    view = expand_compat_view(canon)
    # legacy aliases rebuilt read-side, term-only stamps excluded
    assert view["horizons"]["short"]["status"] == "completed"
    assert "decision_model_version" not in view["horizons"]["short"]
    assert view["market_data_context"] == rd["market_data_context"]


def test_slice_shaped_top_mdc_deduplicated():
    rd = _dual_writer_payload()
    rd["market_data_context"] = copy.deepcopy(rd["short_term"]["market_data_context"])
    canon = canonicalize_for_single_write(rd)
    assert "market_data_context" not in canon
    view = expand_compat_view(canon)
    assert view["market_data_context"] == rd["market_data_context"]


def test_unreconstructible_top_mdc_with_no_slice_mdc_is_kept():
    """Kept only when no slice carries an mdc to contradict it — the
    only-record stock form (B-1 census `kept_mdc` shape)."""
    rd = _dual_writer_payload()
    for h in ("short", "medium"):
        del rd[f"{h}_term"]["market_data_context"]
    rd["market_data_context"] = {"legacy_only": {"sources": ["akshare"]}}
    canon = canonicalize_for_single_write(rd)
    assert is_canonical_storage(canon)
    assert canon["market_data_context"] == {"legacy_only": {"sources": ["akshare"]}}
    view = expand_compat_view(canon)
    assert view["market_data_context"] == {"legacy_only": {"sources": ["akshare"]}}
    # and the persist strip cannot drop it either
    from tradingagents.storage.result_data_compat import strip_compat_view_for_persist
    assert strip_compat_view_for_persist(canon)["market_data_context"] == {
        "legacy_only": {"sources": ["akshare"]}
    }


def test_contradictory_top_mdc_fails_closed_like_encode_path():
    """DAV-1551 🟡-1: a top-level mdc that cannot be reconstructed *and*
    contradicts a slice mdc must raise — the same verdict
    ``encode_canonical`` returns when a ``horizons`` key is present.
    Admissibility must not depend on the physical key set."""
    rd = _dual_writer_payload()
    # contradicts the short slice's own mdc but has horizon-shaped keys
    rd["market_data_context"] = {"short": {"daily": {"as_of": "1999-01-01"}}}
    with pytest.raises(StorageCompatConflict):
        canonicalize_for_single_write(rd)
    # same payload through the full-alias path: identical verdict
    rd_alias = copy.deepcopy(rd)
    rd_alias["horizons"] = {
        h: {k: v for k, v in copy.deepcopy(rd[f"{h}_term"]).items()
            if k not in ("decision_model_version", "evidence_contract_version",
                         "generated_by_commit_sha", "instrument_context")}
        for h in ("short", "medium")
    }
    with pytest.raises(StorageCompatConflict):
        canonicalize_for_single_write(rd_alias)


def test_non_dict_horizons_value_is_dropped_before_v1_stamp():
    """DAV-1551 🟡-2: a non-dict ``horizons`` (str/None/scalar) is
    unreconstructible junk, not alias content — it must not survive into
    the stamped v1 row nor leak through the read-side view."""
    rd = _dual_writer_payload()
    rd["horizons"] = "junk"
    canon = canonicalize_for_single_write(rd)
    assert is_canonical_storage(canon)
    assert canon.get("horizons") != "junk"
    view = expand_compat_view(canon)
    assert isinstance(view.get("horizons"), dict)  # rebuilt, not "junk"
    assert view["horizons"]["short"]["status"] == "completed"


def test_kept_mdc_row_reentering_funnel_is_idempotent():
    """A kept-mdc canonical row passing the funnel again must come out
    unchanged (strip keeps the physical key, no conflict is raised)."""
    rd = _dual_writer_payload()
    for h in ("short", "medium"):
        del rd[f"{h}_term"]["market_data_context"]
    rd["market_data_context"] = {"legacy_only": 1}
    once = canonicalize_for_single_write(rd)
    twice = canonicalize_for_single_write(copy.deepcopy(once))
    assert twice["market_data_context"] == {"legacy_only": 1}
    assert is_canonical_storage(twice)
    assert detect_alias_conflicts(twice) == []


def test_single_horizon_flat_payload_passes_through():
    flat = {"symbol": "X", "horizon": "short", "market_data_context": {"daily": {}}}
    assert canonicalize_for_single_write(flat) == flat
    assert canonicalize_for_single_write(None) is None
    assert canonicalize_for_single_write("x") == "x"


def test_legacy_full_alias_payload_still_encodes():
    rd = _dual_writer_payload()
    rd["horizons"] = {
        h: {k: v for k, v in copy.deepcopy(rd[f"{h}_term"]).items()
            if k not in ("decision_model_version", "evidence_contract_version",
                         "generated_by_commit_sha", "instrument_context")}
        for h in ("short", "medium")
    }
    canon = canonicalize_for_single_write(rd)
    assert is_canonical_storage(canon)
    assert "horizons" not in canon
    assert expand_compat_view(canon)["horizons"]["short"] == rd["horizons"]["short"]


def test_alias_conflict_fails_closed_on_write():
    rd = _dual_writer_payload()
    rd["horizons"] = {
        h: {k: v for k, v in copy.deepcopy(rd[f"{h}_term"]).items()
            if k not in ("decision_model_version", "evidence_contract_version",
                         "generated_by_commit_sha", "instrument_context")}
        for h in ("short", "medium")
    }
    rd["horizons"]["short"]["trade_action"] = "SELL"
    with pytest.raises(StorageCompatConflict):
        canonicalize_for_single_write(rd)


def test_canonical_reentry_strips_stray_view_keys(caplog):
    canon = canonicalize_for_single_write(_dual_writer_payload())
    polluted = dict(canon)
    polluted["horizons"] = {"short": {"foreign": True}}
    with caplog.at_level("WARNING", logger="tradingagents.storage.result_data_compat"):
        out = canonicalize_for_single_write(polluted)
    assert "horizons" not in out
    assert any("conflict" in r.getMessage().lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# persistence funnels (create_report / update_report_partial / create endpoint)


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    sf = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine, sf()


def test_create_report_stores_canonical_only():
    engine, db = _db()
    try:
        rep = report_service.create_report(
            db=db, symbol="600519.SH", trade_date="2026-10-02",
            decision="BUY", result_data=_dual_writer_payload(), user_id="u1",
        )
        stored = db.query(ReportDB).filter(ReportDB.id == rep.id).one().result_data
        assert stored[STORAGE_SCHEMA_KEY] == STORAGE_SCHEMA_VERSION
        assert "horizons" not in stored
        assert "market_data_context" not in stored
        assert stored["short_term"]["market_data_context"]
    finally:
        db.close(); engine.dispose()


def test_update_report_partial_result_data_canonicalizes():
    engine, db = _db()
    try:
        rep = report_service.create_report(
            db=db, symbol="600519.SH", trade_date="2026-10-02",
            decision="BUY", result_data={"symbol": "600519.SH", "status": "running"},
            user_id="u1", status="running",
        )
        report_service.update_report_partial(
            db, rep.id, result_data=_dual_writer_payload(),
        )
        stored = db.query(ReportDB).filter(ReportDB.id == rep.id).one().result_data
        assert stored[STORAGE_SCHEMA_KEY] == STORAGE_SCHEMA_VERSION
        assert "horizons" not in stored
        assert "market_data_context" not in stored
    finally:
        db.close(); engine.dispose()


def test_get_report_served_view_has_legacy_shape_and_zero_writes():
    """A canonical row must serve the same horizons/top-mdc view a legacy row
    did — and the serve must leave the session clean (B-1 零写库不回退)."""
    engine, db = _db()
    try:
        rep = report_service.create_report(
            db=db, symbol="600519.SH", trade_date="2026-10-02",
            decision="BUY", result_data=_dual_writer_payload(), user_id="u1",
        )
        served = report_service.get_report(db, rep.id)
        rd = served.result_data
        assert rd["horizons"]["short"]["status"] == "completed"
        assert rd["horizons"]["medium"]["trade_action"] == "BUY"
        assert rd["market_data_context"]["short"]["daily"]["as_of"] == "2026-10-02"
        # detached shadow: nothing dirty; commit writes nothing back
        assert served not in db.dirty or not db.is_modified(served)
        db.commit()
        stored = db.query(ReportDB).filter(ReportDB.id == rep.id).one().result_data
        assert "horizons" not in stored
        assert "market_data_context" not in stored
    finally:
        db.close(); engine.dispose()


def test_finalize_orphan_does_not_write_view_back():
    """DAV-1514 regression, B-2 wording: finalize commits only
    {status, error, updated_at}; the stored row keeps no virtual keys."""
    engine, db = _db()
    try:
        canon = canonicalize_for_single_write(_dual_writer_payload())
        row = ReportDB(id="r-b2-1", symbol="600519.SH", trade_date="2026-10-02",
                       status="running", result_data=canon)
        db.add(row); db.commit()
        fetched = report_service.get_report(db, "r-b2-1")
        assert isinstance(fetched.result_data.get("horizons"), dict)
        report_service.finalize_orphan_report(db, fetched)
        stored = db.query(ReportDB).filter(ReportDB.id == "r-b2-1").one()
        assert stored.status == "failed"
        assert "horizons" not in (stored.result_data or {})
        assert "market_data_context" not in (stored.result_data or {})
    finally:
        db.close(); engine.dispose()


# ---------------------------------------------------------------------------
# writer-side construction (api/main result builders)


def test_build_b1_unrun_result_dual_emits_no_horizons_alias():
    """The failed/partial dual writer must not persist a physical
    ``horizons`` map — ``*_term`` slots are the only record."""
    import api.main as main

    request = type("R", (), {
        "symbol": "600519.SH", "trade_date": "2026-10-02",
        "horizons": ["short", "medium"],
    })()
    result = main._build_b1_unrun_result(request, status="failed")
    assert result["mode"] == "dual_horizon"
    assert "horizons" not in result
    assert result["short_term"]["status"] == "failed"
    assert result["medium_term"]["status"] == "failed"
    # and it round-trips through the persist funnel
    canon = canonicalize_for_single_write(result)
    assert is_canonical_storage(canon)
    assert "horizons" not in canon


def test_detect_alias_conflicts_on_handmade_contradictory_kept_mdc_row():
    """DAV-1551 follow-up: the kept-mdc exemption only exempts "no slice
    to compare" — a hand-built kept row that *does* contradict a slice mdc
    is reported again, never silently excused."""
    rd = _dual_writer_payload()
    for h in ("short", "medium"):
        del rd[f"{h}_term"]["market_data_context"]
    rd["market_data_context"] = {"legacy_only": 1}
    canon = canonicalize_for_single_write(rd)
    # hand-pollute: give a slice an mdc that disagrees with the kept value
    canon["short_term"]["market_data_context"] = {"daily": {"as_of": "1999-01-01"}}
    conflicts = detect_alias_conflicts(canon)
    assert any("market_data_context" in c for c in conflicts)
    # a consistent slice mdc reports nothing
    canon["short_term"]["market_data_context"] = {"legacy_only": 1}
    assert detect_alias_conflicts(canon) == []


def test_quarantine_operates_on_authoritative_slice_only():
    """Machine-block quarantine degrades the owning ``*_term`` slice; no
    physical ``horizons`` twin is consulted or re-created."""
    rd = _dual_writer_payload()
    rd["short_term"]["investment_debate_state"] = {
        "count": 1,
        "claims": [{"claim": "c", "confidence": 0.8, "target_claim_ids": [],
                    "evidence": []}],
        "attempts": [{
            "attempt_index": 1, "message_index": 1, "debate_round": 1,
            "speaker": "Bull Analyst", "speaker_key": "Bull",
            "parse_status": "valid", "error_detail": "",
            "raw_response": '正文\n<!-- DEBATE_STATE: {"new_claims": [bad json}} -->',
        }],
    }
    out = report_service.quarantine_invalid_report_machine_blocks(rd)
    assert out["short_term"]["trade_action"] == "NO_TRADE"
    assert out["short_term"]["analysis_status"] == "ABSTAIN"
    assert out["medium_term"]["trade_action"] == "BUY"
    assert "horizons" not in out
    assert "MACHINE_BLOCK_INVALID" in \
        out["short_term"]["investment_debate_state"]["attempts"][0]["raw_response"]
