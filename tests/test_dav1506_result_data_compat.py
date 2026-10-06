"""DAV-1506 (存储 B-1): read-layer compat view for result_data.storage.v1.

Covers the D-072 裁定 contract:

* canonical rows (short_term/medium_term authoritative) get the legacy
  ``horizons.<h>`` map and top-level ``market_data_context`` rebuilt with
  exact old key order / null / absence semantics;
* encode → expand round-trips a legacy row to an identical view;
* common-key conflicts fail closed (never silently reconciled);
* the compat view is never written back (persist boundary strips it).
"""

import copy
import json

import pytest

from tradingagents.storage.result_data_compat import (
    STORAGE_COMPAT_KEY,
    STORAGE_SCHEMA_KEY,
    STORAGE_SCHEMA_VERSION,
    StorageCompatConflict,
    detect_alias_conflicts,
    encode_canonical,
    expand_compat_view,
    is_canonical_storage,
    result_data_compat_view,
    strip_compat_view_for_persist,
)


def _slice(horizon: str) -> dict:
    """Minimal realistic per-horizon payload (order matters for key masks)."""
    return {
        "horizon": horizon,
        "status": "completed",
        "trade_action": "BUY",
        "analysis_status": "VALID",
        "direction": "看多",
        "confidence": 80,
        "probability": 0.7,
        "target_price": 12.5,
        "stop_loss_price": 9.5,
        "manager_verdict": {"winner": "bull", "trade_action": "BUY"},
        "investment_debate_state": {"claims": [{"claim_id": "c1", "claim": "x"}]},
        "market_data_context": {"daily": {"as_of": "2026-10-02"}, "source_provenance": {}},
        "forecast": {"t_plus_5_date": None},
        "data_gaps": [],
        "nullable_field": None,
        # term-only stamps (never in legacy horizons alias):
        "decision_model_version": "decision_model.v1",
        "evidence_contract_version": "evidence_contract.v2",
        "generated_by_commit_sha": "a" * 40,
    }


def _legacy_dual() -> dict:
    short = _slice("short")
    medium = _slice("medium")
    # horizons alias: same content minus term-only stamps, possibly different order
    short_alias = {k: copy.deepcopy(v) for k, v in short.items() if k not in (
        "decision_model_version", "evidence_contract_version", "generated_by_commit_sha")}
    medium_alias = {k: copy.deepcopy(v) for k, v in medium.items() if k not in (
        "decision_model_version", "evidence_contract_version", "generated_by_commit_sha")}
    return {
        "symbol": "600519.SH",
        "trade_date": "2026-10-02",
        "mode": "dual_horizon",
        "market_data_context": {
            "short": copy.deepcopy(short["market_data_context"]),
            "medium": copy.deepcopy(medium["market_data_context"]),
        },
        "short_term": short,
        "medium_term": medium,
        "horizons": {"short": short_alias, "medium": medium_alias},
        "decision": "BUY",
        "final_trade_decision": "buy because reasons",
    }


# ---------------------------------------------------------------------------
# encode


def test_encode_strips_aliases_and_records_masks():
    rd = _legacy_dual()
    canon = encode_canonical(rd)

    assert canon[STORAGE_SCHEMA_KEY] == STORAGE_SCHEMA_VERSION
    assert "horizons" not in canon
    assert "market_data_context" not in canon
    assert canon["short_term"]["market_data_context"] is not None
    assert canon["medium_term"]["market_data_context"] is not None

    compat = canon[STORAGE_COMPAT_KEY]
    assert compat["horizons_order"] == ["short", "medium"]
    assert compat["horizon_key_masks"]["short"] == list(rd["horizons"]["short"].keys())
    assert compat["horizon_key_masks"]["medium"] == list(rd["horizons"]["medium"].keys())
    assert compat["top_market_context"] == {"kind": "per_horizon", "order": ["short", "medium"]}
    assert compat["top_key_order"] == list(rd.keys())


def test_encode_single_slice_top_mdc():
    rd = _legacy_dual()
    # write path B: top mdc is a copy of the primary slice's mdc
    rd["market_data_context"] = copy.deepcopy(rd["short_term"]["market_data_context"])
    canon = encode_canonical(rd)
    assert canon[STORAGE_COMPAT_KEY]["top_market_context"] == {"kind": "slice", "horizon": "short"}


def test_encode_null_top_mdc():
    rd = _legacy_dual()
    rd["market_data_context"] = None
    canon = encode_canonical(rd)
    assert canon[STORAGE_COMPAT_KEY]["top_market_context"] == {"kind": "null"}


def test_encode_absent_top_mdc():
    rd = _legacy_dual()
    del rd["market_data_context"]
    canon = encode_canonical(rd)
    assert canon[STORAGE_COMPAT_KEY]["top_market_context"] == {"kind": "absent"}
    # absent stays absent on expand
    assert "market_data_context" not in expand_compat_view(canon)


def test_encode_leaves_single_horizon_and_non_dict_alone():
    single = {"horizon": "short", "market_data_context": {"daily": {}}}
    assert encode_canonical(single) == single
    assert encode_canonical(None) is None
    assert encode_canonical("x") == "x"


def test_encode_fails_closed_on_common_key_conflict():
    rd = _legacy_dual()
    rd["horizons"]["short"]["direction"] = "看空"
    with pytest.raises(StorageCompatConflict):
        encode_canonical(rd)
    # row stays as-is (caller keeps original)
    assert rd["horizons"]["short"]["direction"] == "看空"


def test_encode_fails_closed_on_horizons_only_key():
    rd = _legacy_dual()
    rd["horizons"]["short"]["legacy_extra"] = {"some": "data"}
    with pytest.raises(StorageCompatConflict):
        encode_canonical(rd)


def test_encode_fails_closed_on_missing_term_twin():
    rd = _legacy_dual()
    rd["horizons"]["weekend"] = {"horizon": "weekend", "x": 1}
    with pytest.raises(StorageCompatConflict):
        encode_canonical(rd)


def test_encode_fails_closed_on_unreconstructible_top_mdc():
    rd = _legacy_dual()
    rd["market_data_context"] = {"totally": "different"}
    with pytest.raises(StorageCompatConflict):
        encode_canonical(rd)


# ---------------------------------------------------------------------------
# decode (expand)


def test_expand_roundtrip_is_view_identical():
    rd = _legacy_dual()
    canon = encode_canonical(rd)
    view = expand_compat_view(canon)

    # horizons slices rebuilt without term-only stamps
    assert view["horizons"]["short"] == rd["horizons"]["short"]
    assert view["horizons"]["medium"] == rd["horizons"]["medium"]
    assert list(view["horizons"].keys()) == list(rd["horizons"].keys())
    assert list(view["horizons"]["short"].keys()) == list(rd["horizons"]["short"].keys())
    # null preserved
    assert view["horizons"]["short"]["nullable_field"] is None
    # term-only stamps absent from the alias view
    assert "decision_model_version" not in view["horizons"]["short"]

    # top-level mdc restored per legacy shape
    assert view["market_data_context"] == rd["market_data_context"]

    # full top-level key order restored (recorded order, then the canonical
    # storage markers appended — physical content is key-for-key identical)
    for k in rd:
        assert k in view
    canonical_only_keys = set(view.keys()) - set(rd.keys())
    assert canonical_only_keys == {STORAGE_COMPAT_KEY, STORAGE_SCHEMA_KEY}


def test_expand_roundtrip_slice_mdc_shape():
    rd = _legacy_dual()
    rd["market_data_context"] = copy.deepcopy(rd["short_term"]["market_data_context"])
    canon = encode_canonical(rd)
    view = expand_compat_view(canon)
    assert view["market_data_context"] == rd["market_data_context"]
    for k in rd:
        assert view[k] == rd[k]


def test_expand_roundtrip_null_mdc_shape():
    rd = _legacy_dual()
    rd["market_data_context"] = None
    canon = encode_canonical(rd)
    view = expand_compat_view(canon)
    assert view["market_data_context"] is None
    assert "market_data_context" in view


def test_expand_does_not_mutate_canonical():
    rd = _legacy_dual()
    canon = encode_canonical(rd)
    canon_before = copy.deepcopy(canon)
    view = expand_compat_view(canon)
    # mutate the view freely; canonical untouched
    view["horizons"]["short"]["direction"] = "mutated"
    view["market_data_context"]["short"]["daily"]["as_of"] = "1999-01-01"
    assert canon == canon_before


def test_expand_legacy_rows_pass_through():
    rd = _legacy_dual()
    assert expand_compat_view(rd) is rd
    assert result_data_compat_view(rd) is rd
    assert expand_compat_view({"horizon": "short"}) == {"horizon": "short"}


def test_expand_conflict_fails_closed():
    rd = _legacy_dual()
    canon = encode_canonical(rd)
    # corrupt the mask so a recorded key no longer exists in term
    canon[STORAGE_COMPAT_KEY]["horizon_key_masks"]["short"].append("ghost_key")
    with pytest.raises(StorageCompatConflict):
        expand_compat_view(canon)
    # safe wrapper returns stored form
    assert result_data_compat_view(canon) is canon


def test_expand_fallback_masks_without_storage_compat():
    rd = _legacy_dual()
    canon = encode_canonical(rd)
    del canon[STORAGE_COMPAT_KEY]
    view = expand_compat_view(canon)
    # fallback mask drops the three always-term-only stamps; instrument_context
    # isn't in the fixture's term slice so equality holds.
    assert view["horizons"]["short"] == rd["horizons"]["short"]


def test_is_canonical_storage():
    assert not is_canonical_storage(_legacy_dual())
    assert is_canonical_storage(encode_canonical(_legacy_dual()))
    assert not is_canonical_storage({"storage_schema_version": "other"})
    assert not is_canonical_storage("x")


# ---------------------------------------------------------------------------
# persist guard


def test_strip_compat_view_removes_virtual_keys_on_v1():
    rd = _legacy_dual()
    canon = encode_canonical(rd)
    view = expand_compat_view(canon)
    stripped = strip_compat_view_for_persist(view)
    assert "horizons" not in stripped
    assert "market_data_context" not in stripped
    assert stripped[STORAGE_SCHEMA_KEY] == STORAGE_SCHEMA_VERSION
    assert stripped["short_term"] == canon["short_term"]


def test_strip_reports_foreign_alias_conflict_then_drops(caplog):
    """Foreign alias content on a v1 row is logged then dropped (DAV-1514)."""
    rd = _legacy_dual()
    canon = encode_canonical(rd)
    canon["horizons"] = {"short": {"foreign": True}}
    with caplog.at_level("WARNING", logger="tradingagents.storage.result_data_compat"):
        stripped = strip_compat_view_for_persist(canon)
    assert "horizons" not in stripped
    assert any("alias conflict" in rec.getMessage() or "conflict" in rec.getMessage().lower() for rec in caplog.records)


def test_strip_noop_on_legacy():
    rd = _legacy_dual()
    assert strip_compat_view_for_persist(rd) is rd


# ---------------------------------------------------------------------------
# conflict detector


def test_detect_alias_conflicts_flags_non_dict_horizon_slot():
    """DAV-1514 🟢-2: ``horizons.<h> = None`` must be reported, not skipped."""
    rd = _legacy_dual()
    rd["horizons"]["short"] = None
    conflicts = detect_alias_conflicts(rd)
    assert any("horizons.short" in c and "not an object" in c for c in conflicts)
    # and encode refuses (fail-close), never silently dropping the slot
    with pytest.raises(StorageCompatConflict):
        encode_canonical(rd)


def test_detect_alias_conflicts_reports_each_kind():
    rd = _legacy_dual()
    assert detect_alias_conflicts(rd) == []

    rd["horizons"]["short"]["direction"] = "看空"          # common-key conflict
    rd["horizons"]["medium"]["extra"] = 1                 # horizons-only key
    rd["horizons"]["weekend"] = {"x": 1}                  # missing term twin
    rd["market_data_context"] = {"alien": True}           # unreconstructible mdc
    conflicts = detect_alias_conflicts(rd)
    assert any("direction" in c for c in conflicts)
    assert any("extra" in c for c in conflicts)
    assert any("weekend" in c for c in conflicts)
    assert any("market_data_context" in c for c in conflicts)


def test_strip_drops_virtual_keys_unconditionally_on_v1():
    """DAV-1514 🔴: read-time backfills must not defeat the persist guard.

    After ``ensure_horizon_run_metadata_on_read`` stamps hrm into the served
    view's ``horizons.<h>`` slices (a key absent from the recorded mask), the
    strip must still drop ``horizons`` / ``market_data_context`` wholesale.
    """
    rd = _legacy_dual()
    canon = encode_canonical(rd)
    view = expand_compat_view(canon)
    # simulate the get_report read-side backfill into the view slices
    view["horizons"]["short"]["horizon_run_metadata"] = {"resolved": ["short"]}
    stripped = strip_compat_view_for_persist(view)
    assert "horizons" not in stripped
    assert "market_data_context" not in stripped
    # authoritative slots survive untouched
    assert stripped["short_term"] == canon["short_term"]


def test_strip_keeps_legacy_rows_untouched():
    rd = _legacy_dual()
    assert strip_compat_view_for_persist(rd) is rd


def test_get_report_then_finalize_does_not_write_view_back():
    """DAV-1514 回归: v1 行缺 hrm → get_report → finalize_orphan_report 后
    ORM 对象 ``result_data`` 不含 ``horizons`` / ``market_data_context``。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from api.database import Base, ReportDB
    from api.services import report_service

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = session_factory()
    try:
        rd = _legacy_dual()
        canon = encode_canonical(rd)
        row = ReportDB(
            id="r-v1-1", symbol="600519.SH", trade_date="2026-10-02",
            status="running", result_data=canon,
        )
        db.add(row)
        db.commit()

        fetched = report_service.get_report(db, "r-v1-1")
        # served view has the reconstructed aliases; hrm is backfilled at the
        # top level and into the authoritative *_term slices only (DAV-1545:
        # read-time backfill must not stamp the virtual ``horizons.<h>``
        # slices — the persist strip drops them unconditionally anyway).
        assert isinstance(fetched.result_data.get("horizons"), dict)
        assert "horizon_run_metadata" in fetched.result_data
        assert "horizon_run_metadata" in fetched.result_data["short_term"]

        report_service.finalize_orphan_report(db, fetched)

        persisted = db.query(ReportDB).filter(ReportDB.id == "r-v1-1").one()
        assert persisted.status == "failed"
        assert "horizons" not in (persisted.result_data or {})
        assert "market_data_context" not in (persisted.result_data or {})
        # authoritative slices carry the backfilled hrm (term slots), but no
        # virtual alias keys were written back.
        assert persisted.result_data["short_term"].get("horizon_run_metadata") or \
            persisted.result_data.get("horizon_run_metadata")
    finally:
        db.close()
        engine.dispose()


def test_get_report_does_not_mutate_stored_nested_dicts():
    """总控验收: get_report 的读时回填不得污染 ORM 行持有的 stored dict。

    深拷贝前的旧实现里，``view['short_term']`` 与 stored ``short_term`` 是同
    一对象，``ensure_horizon_run_metadata_on_read`` 就地写入会把
    ``horizon_run_metadata`` 渗进 stored dict —— 下一次 commit 即写回。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from api.database import Base, ReportDB
    from api.services import report_service

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = session_factory()
    try:
        canon = encode_canonical(_legacy_dual())
        # strip hrm everywhere so read-side backfill has something to add
        canon.pop("horizon_run_metadata", None)
        for t in ("short_term", "medium_term"):
            canon.get(t, {}).pop("horizon_run_metadata", None)
        db.add(ReportDB(id="r-v1-2", symbol="X", trade_date="2026-10-02",
                        status="completed", result_data=copy.deepcopy(canon)))
        db.commit()

        rep = report_service.get_report(db, "r-v1-2")
        # served view carries the read-time backfill
        assert rep.result_data.get("horizon_run_metadata")
        # but the ORM session must hold nothing dirty — a later flush writes nothing
        assert rep not in db.dirty or not db.is_modified(rep)
        # stored dict untouched: no hrm bled into the stored *_term slices
        stored = db.query(ReportDB).filter(ReportDB.id == "r-v1-2").one().result_data
        assert "horizon_run_metadata" not in stored.get("short_term", {})
        assert "horizons" not in stored
    finally:
        db.close()
        engine.dispose()


def test_json_serialized_view_equals_legacy_serialization_subset():
    """The expanded view preserves legacy key order at every level we control."""
    rd = _legacy_dual()
    canon = encode_canonical(rd)
    view = expand_compat_view(canon)
    # serialized view == serialized original for the shared physical content:
    # horizons slices and top mdc serialize identically.
    assert json.dumps(view["horizons"], ensure_ascii=False) == json.dumps(
        rd["horizons"], ensure_ascii=False
    )
    assert json.dumps(view["market_data_context"], ensure_ascii=False) == json.dumps(
        rd["market_data_context"], ensure_ascii=False
    )
