"""DAV-2097: tests for the input-PIT three-rule fix (总控 10-15 三裁).

Coverage mirrors the card's required test list:
  1. B-chain (canonical v1, per-horizon `<h>_term.market_data_context`)
     records are judged per horizon;
  2. two horizons with one VERIFIED + one UNVERIFIED merge correctly;
  3. refused/unavailable sources are exempt → gap_sources;
  4. status=available without a readable timestamp stays UNVERIFIED;
  5. pit_reattest.jsonl is append-only + idempotent;
  6. the three eval scripts' value order (ledger VERIFIED/FAILED final,
     UNVERIFIED consults the reattest file);
  7. a mixed ledger (old + new verdicts) still passes `verify`.

Offline only — synthetic result_data + tmp ledger dirs; no DB, no akshare.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
PHASE2 = REPO / "scripts" / "phase2"
sys.path.insert(0, str(PHASE2))

import pit_reattest  # noqa: E402

_dsl_spec = importlib.util.spec_from_file_location(
    "daily_snapshot_ledger", PHASE2 / "daily_snapshot_ledger.py")
dsl = importlib.util.module_from_spec(_dsl_spec)
sys.modules["daily_snapshot_ledger"] = dsl
_dsl_spec.loader.exec_module(dsl)


# ---------------------------------------------------------------------------
# fixtures


def _prov(src_entries: dict) -> dict:
    """A per-horizon mdc slice carrying source_provenance."""
    return {"source_provenance": src_entries,
            "analysis_baseline_date": "2026-10-14"}


def _verified(src: str, req: str = "2026-10-14", act: str = "2026-10-14"):
    return {"requested_as_of": req, "actual_as_of": act, "as_of": act,
            "status": "available", "provenance_status": "verified"}


def _refused(src: str, req: str = "2026-10-14"):
    return {"requested_as_of": req, "actual_as_of": None, "as_of": None,
            "status": "unavailable", "provenance_status": "refused"}


def _avail_no_ts(src: str, req: str = "2026-10-14"):
    return {"requested_as_of": req, "actual_as_of": None, "as_of": None,
            "status": "available", "provenance_status": "unverified"}


def _result_data_canonical(short_sp: dict, medium_sp: dict | None) -> dict:
    """A B-chain canonical row: no top-level mdc; slices carry their own."""
    rd = {
        "storage_schema_version": "result_data.storage.v1",
        "storage_compat": {"version": 1,
                           "top_market_context": {"kind": "absent"},
                           "horizon_key_masks": {}},
        "input_snapshot": {"algo": "sha256", "hash": "x",
                           "keys": sorted(set(short_sp) | set(medium_sp or {}))},
        "short_term": {"horizon": "short",
                       "market_data_context": _prov(short_sp)},
    }
    if medium_sp is not None:
        rd["medium_term"] = {"horizon": "medium",
                             "market_data_context": _prov(medium_sp)}
    return rd


# ---------------------------------------------------------------------------
# ① 读取位置：B 链格式报告按档判定


def test_bchain_per_horizon_read_and_merge():
    rd = _result_data_canonical(
        {"stock_data": _verified("s"), "news": _verified("n")},
        {"stock_data": _verified("s"), "news": _verified("n")})
    status, ev, mdc = dsl._classify_input_pit(rd)
    assert status == "VERIFIED"
    assert ev["mdc_shape"] == "per_horizon"
    assert set(ev["horizons"]) == {"short", "medium"}
    assert ev["horizons"]["short"]["status"] == "VERIFIED"
    assert ev["horizons"]["medium"]["status"] == "VERIFIED"
    # sha pins the exact per-horizon mapping that was read
    expect = hashlib.sha256(dsl._canonical(
        {"medium": rd["medium_term"]["market_data_context"],
         "short": rd["short_term"]["market_data_context"]})).hexdigest()
    assert ev["market_data_context_sha256"] == expect
    assert mdc == {"medium": rd["medium_term"]["market_data_context"],
                   "short": rd["short_term"]["market_data_context"]}


def test_top_level_mdc_still_wins():
    """Pre-B rows with a physical top-level mdc keep the old read path."""
    rd = {
        "input_snapshot": {"keys": ["stock_data"]},
        "market_data_context": {
            "short": {"source_provenance": {"stock_data": _verified("s")}},
        },
        "short_term": {"market_data_context": _prov(
            {"stock_data": _refused("s")})},   # conflicting slice is ignored
    }
    status, ev, _ = dsl._classify_input_pit(rd)
    assert status == "VERIFIED"
    assert ev["mdc_shape"] == "top_level"


def test_flat_top_mdc_shape_reads_provenance():
    """Single-horizon legacy rows store mdc flat with top-level
    source_provenance — the classifier must read it (was previously
    invisible to _iter_source_entries)."""
    rd = {
        "input_snapshot": {"keys": ["stock_data"]},
        "market_data_context": {
            "analysis_baseline_date": "2026-10-09",
            "source_provenance": {"stock_data": _verified("s")},
        },
    }
    status, ev, _ = dsl._classify_input_pit(rd)
    assert status == "VERIFIED"
    assert ev["mdc_shape"] == "top_level"


# ---------------------------------------------------------------------------
# ① 合并：两档一核实一未核实


def test_merge_one_verified_one_unverified():
    rd = _result_data_canonical(
        {"stock_data": _verified("s")},
        {"stock_data": _avail_no_ts("s")})
    status, ev, _ = dsl._classify_input_pit(rd)
    assert status == "UNVERIFIED"
    assert ev["horizons"]["short"]["status"] == "VERIFIED"
    assert ev["horizons"]["medium"]["status"] == "UNVERIFIED"


def test_merge_any_failed_wins():
    rd = _result_data_canonical(
        {"stock_data": _verified("s")},
        {"stock_data": _verified("s", act="2026-10-20")})  # look-ahead
    status, ev, _ = dsl._classify_input_pit(rd)
    assert status == "FAILED"
    assert ev["horizons"]["medium"]["status"] == "FAILED"


def test_merge_single_horizon_present():
    """A failed horizon run (no mdc slice for one horizon) must not drag
    the verdict — the surviving slice's verdict stands."""
    rd = _result_data_canonical({"stock_data": _verified("s")}, None)
    rd["medium_term"] = {"horizon": "medium", "status": "error"}
    status, ev, _ = dsl._classify_input_pit(rd)
    assert status == "VERIFIED"
    assert set(ev["horizons"]) == {"short"}


# ---------------------------------------------------------------------------
# ② refused / unavailable 豁免 + gap_sources


def test_refused_exempt_to_gap_sources():
    rd = _result_data_canonical(
        {"stock_data": _verified("s"), "hot_stocks": _refused("h")},
        {"stock_data": _verified("s"), "hot_stocks": _refused("h")})
    status, ev, _ = dsl._classify_input_pit(rd)
    assert status == "VERIFIED"
    gaps = ev["gap_sources"]
    assert any(g["source"] == "hot_stocks" for g in gaps)
    assert not any(u["source"] == "hot_stocks"
                   for u in ev["unverified_sources"])


def test_unavailable_status_also_exempt():
    e = dict(_verified("s"))
    e["provenance_status"] = None
    e["status"] = "unavailable"
    rd = _result_data_canonical({"stock_data": e}, None)
    status, ev, _ = dsl._classify_input_pit(rd)
    assert status == "VERIFIED"
    assert any(g["source"] == "stock_data" for g in ev["gap_sources"])


def test_refused_with_future_asof_still_fails():
    """A look-ahead timestamp beats the fetch-gap exemption."""
    e = _refused("h")
    e["actual_as_of"] = "2026-10-20"
    rd = _result_data_canonical({"stock_data": _verified("s"), "hot": e},
                                None)
    status, ev, _ = dsl._classify_input_pit(rd)
    assert status == "FAILED"
    assert any(f["source"] == "hot" for f in ev["failed_sources"])


# ---------------------------------------------------------------------------
# ② available 但无可读时间戳 → 仍 UNVERIFIED


def test_available_without_timestamp_unverified():
    rd = _result_data_canonical({"realtime": _avail_no_ts("r")}, None)
    status, ev, _ = dsl._classify_input_pit(rd)
    assert status == "UNVERIFIED"
    assert any(u["source"] == "realtime" for u in ev["unverified_sources"])
    assert not ev["gap_sources"]


# ---------------------------------------------------------------------------
# ③ 补判文件：只追加 + 幂等 + 取值顺序


def test_reattest_append_only_and_idempotent(tmp_path):
    entries = [
        {"report_id": "r1", "input_pit_status": "VERIFIED",
         "rule_version": "pit_rules.v2", "mdc_sha256": "a" * 64,
         "status": "adjudicated"},
        {"report_id": "r2", "input_pit_status": None,
         "rule_version": "pit_rules.v2", "mdc_sha256": None,
         "status": "no_evidence"},
    ]
    res1 = pit_reattest.append_reattest(tmp_path, entries,
                                        adjudicated_at="2026-10-15T00:00:00+00:00")
    assert res1["appended"] == 2
    path = tmp_path / pit_reattest.REATTEST_FILE
    lines1 = path.read_text("utf-8").splitlines()
    assert len(lines1) == 2
    # second identical run is a no-op (idempotent)
    res2 = pit_reattest.append_reattest(tmp_path, entries,
                                        adjudicated_at="2026-10-15T01:00:00+00:00")
    assert res2["appended"] == 0 and res2["skipped_duplicates"] == 2
    assert path.read_text("utf-8").splitlines() == lines1  # file untouched
    # a NEW verdict for r1 (different mdc) appends; file only grows
    res3 = pit_reattest.append_reattest(
        tmp_path, [{"report_id": "r1", "input_pit_status": "UNVERIFIED",
                    "mdc_sha256": "b" * 64, "status": "adjudicated"}],
        adjudicated_at="2026-10-15T02:00:00+00:00")
    assert res3["appended"] == 1
    assert len(path.read_text("utf-8").splitlines()) == 3


def test_load_index_skips_no_evidence(tmp_path):
    pit_reattest.append_reattest(tmp_path, [
        {"report_id": "r1", "input_pit_status": "VERIFIED",
         "mdc_sha256": "a" * 64, "status": "adjudicated"},
        {"report_id": "r2", "input_pit_status": "UNVERIFIED",
         "mdc_sha256": None, "status": "no_evidence"},
    ], adjudicated_at="t")
    idx = pit_reattest.load_reattest_index(tmp_path / pit_reattest.REATTEST_FILE)
    assert "r1" in idx and "r2" not in idx


def test_effective_pit_value_order():
    idx = {"u1": {"input_pit_status": "VERIFIED"},
           "u2": {"input_pit_status": "FAILED"}}
    # 账本 VERIFIED / FAILED 以账本为准（补判文件忽略）
    assert pit_reattest.resolve_effective_pit("VERIFIED", "u1", idx) == "VERIFIED"
    assert pit_reattest.resolve_effective_pit("FAILED", "u2", idx) == "FAILED"
    assert pit_reattest.resolve_effective_pit("VERIFIED", "u2", idx) == "VERIFIED"
    # 账本 UNVERIFIED → 查补判
    assert pit_reattest.resolve_effective_pit("UNVERIFIED", "u1", idx) == "VERIFIED"
    assert pit_reattest.resolve_effective_pit("UNVERIFIED", "u2", idx) == "FAILED"
    # 补判无记录 → UNVERIFIED 保持
    assert pit_reattest.resolve_effective_pit("UNVERIFIED", "uX", idx) == "UNVERIFIED"
    assert pit_reattest.resolve_effective_pit("UNVERIFIED", "u1", None) == "UNVERIFIED"


def test_reattest_corrupt_line_fails_closed(tmp_path):
    p = tmp_path / pit_reattest.REATTEST_FILE
    p.write_text('{"report_id": "r1", "status": "adjudicated"}\n{junk\n')
    with pytest.raises(ValueError, match="unreadable"):
        pit_reattest.load_reattest_index(p)


# ---------------------------------------------------------------------------
# ⑥ 三个评估脚本的取值顺序


def _m1_frame():
    """3 ledger-UNVERIFIED rows + 1 VERIFIED + 1 FAILED; run_id keyed."""
    rows = []
    for rid, pit in (("u1", "UNVERIFIED"), ("u2", "UNVERIFIED"),
                     ("u3", "UNVERIFIED"), ("v1", "VERIFIED"),
                     ("f1", "FAILED")):
        rows.append({"signal_date": "2026-01-05", "symbol": "S1",
                     "run_id": rid, "prob": 60, "rel_return": 0.01,
                     "timing_class": "F0", "input_pit_status": pit,
                     "created_at": "2026-01-05T08:00"})
    # give the admitted rows distinct days so dedup doesn't collapse them
    days = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    for r, d in zip(rows, days):
        r["signal_date"] = d
        r["symbol"] = "S" + r["run_id"]
    return pd.DataFrame(rows)


def test_m1_admit_reattest_order():
    m1_spec = importlib.util.spec_from_file_location(
        "m1_eval", PHASE2 / "m1_eval.py")
    m1 = importlib.util.module_from_spec(m1_spec)
    sys.modules["m1_eval"] = m1
    m1_spec.loader.exec_module(m1)

    idx = {"u1": {"input_pit_status": "VERIFIED"},
           "u2": {"input_pit_status": "FAILED"}}
    df = _m1_frame()
    formal, funnel = m1.admit(df, user_id=None, reattest_index=idx)
    admitted = set(formal["run_id"])
    # u1 upgraded → admitted; u2 upgraded to FAILED → excluded by pit_failed;
    # u3 still UNVERIFIED → excluded; v1 ledger VERIFIED → admitted;
    # f1 ledger FAILED → excluded.
    assert admitted == {"u1", "v1"}
    assert funnel["pit_reattest_applied"] == 2
    assert funnel["excl_pit_failed"] == 2   # f1 + reattested u2
    assert funnel["excl_unverified"] == 1   # u3


def test_m2_enrich_reattest_order():
    m2_spec = importlib.util.spec_from_file_location(
        "m2_periodic_report", PHASE2 / "m2_periodic_report.py")
    m2 = importlib.util.module_from_spec(m2_spec)
    sys.modules["m2_periodic_report"] = m2
    m2_spec.loader.exec_module(m2)

    recs = [
        {"report_id": "u1", "input_pit_status": "UNVERIFIED",
         "signal_date": "2026-01-05", "timing_class": "F0",
         "backfilled": False, "horizons": {}},
        {"report_id": "v1", "input_pit_status": "VERIFIED",
         "signal_date": "2026-01-05", "timing_class": "F0",
         "backfilled": False, "horizons": {}},
    ]
    idx = {"u1": {"input_pit_status": "VERIFIED"}}
    # trade_dates covering T+40 after 2026-01-05 so both records mature
    # before as_of=2026-03-01.
    import datetime as _dt
    tds = []
    d = _dt.date(2026, 1, 5)
    while len(tds) < 41:
        if d.weekday() < 5:
            tds.append(d)
        d += _dt.timedelta(days=1)
    env = m2._enrich(recs, tds, _dt.date(2026, 3, 10),
                     reattest_index=idx)
    assert env[0]["input_pit_status"] == "VERIFIED"
    assert env[0]["_ledger_input_pit_status"] == "UNVERIFIED"
    assert env[1]["input_pit_status"] == "VERIFIED"
    assert "_ledger_input_pit_status" not in env[1]
    # DAV-2102 regression: _formal must use the overlayed status — u1 is
    # ledger UNVERIFIED but reattested VERIFIED, matured F0 → formal.
    assert env[0]["_matured"] and env[0]["_formal"] is True
    assert env[1]["_formal"] is True


def test_prel_load_reattest_order(tmp_path):
    p_spec = importlib.util.spec_from_file_location(
        "p_rel_t10_baseline_eval", PHASE2 / "p_rel_t10_baseline_eval.py")
    pm = importlib.util.module_from_spec(p_spec)
    sys.modules["p_rel_t10_baseline_eval"] = pm
    p_spec.loader.exec_module(pm)

    recs = [pm._ledger_rec("u1", "S1", "2026-01-05", 60, pit="UNVERIFIED"),
            pm._ledger_rec("v1", "S2", "2026-01-05", 60, pit="VERIFIED")]
    ledger = tmp_path / "forward_ledger.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in recs))
    idx = {"u1": {"input_pit_status": "VERIFIED"}}
    df = pm.load_ledger_signals(ledger, {}, pm.DEFAULT_USER_ID,
                                reattest_index=idx)
    got = dict(zip(df["run_id"], df["input_pit_status"]))
    assert got == {"u1": "VERIFIED", "v1": "VERIFIED"}
    assert df.loc[df["run_id"] == "u1", "_ledger_input_pit_status"].iloc[0] \
        == "UNVERIFIED"


# ---------------------------------------------------------------------------
# ⑦ 混合账本 verify 哈希链通过


def _sealed_rec(rid: str, rd: dict) -> dict:
    row = dict(
        id=rid, user_id=dsl.DEFAULT_USER_ID, symbol="600519.SH",
        industry="白酒", trade_date="2026-10-14", status="completed",
        analysis_status="VALID", decision="BUY", direction="看多",
        probability=60, trade_action="BUY", risk_status="APPROVED",
        final_trade_decision="ok",
        created_at="2026-10-14 15:43:57.730577",
        updated_at="2026-10-14 15:43:57.730577")
    sealed = __import__("datetime").datetime(
        2026, 10, 14, 12, 0, tzinfo=__import__("datetime").timezone.utc)
    cal = []
    d = __import__("datetime").date(2026, 9, 1)
    while len(cal) < 120:
        if d.weekday() < 5:
            cal.append(d)
        d = __import__("datetime").date.fromordinal(d.toordinal() + 1)
    return dsl.build_record(row, json.dumps(rd, ensure_ascii=False),
                            sealed, "2026-10-14", cal)


def test_mixed_ledger_verifies(tmp_path):
    # old-shape record (top-level mdc, judged top_level) + B-chain record
    old_rd = {
        "input_snapshot": {"keys": ["stock_data"]},
        "market_data_context": {"short": {"source_provenance":
                                        {"stock_data": _verified("s")}}},
        "horizons": {"short": {"direction": "BULL"}},
    }
    new_rd = _result_data_canonical({"stock_data": _verified("s")}, None)
    recs = [_sealed_rec("old1", old_rd), _sealed_rec("new1", new_rd)]
    assert recs[0]["input_pit_status"] == "VERIFIED"
    assert recs[1]["input_pit_status"] == "VERIFIED"
    assert recs[0]["input_pit_evidence"]["mdc_shape"] == "top_level"
    assert recs[1]["input_pit_evidence"]["mdc_shape"] == "per_horizon"

    ledger = tmp_path / "forward_ledger.jsonl"
    prev = "GENESIS"
    with open(ledger, "w", encoding="utf-8") as fh:
        for rec in recs:
            chain = hashlib.sha256(
                (prev + rec["record_sha256"]).encode()).hexdigest()
            fh.write(json.dumps({"record": rec, "prev_hash": prev,
                                 "chain_hash": chain},
                                ensure_ascii=False) + "\n")
            prev = chain
    ns = type("NS", (), {"ledger_dir": str(tmp_path)})
    assert dsl.cmd_verify(ns) == 0
