"""DAV-1875 — expiry return-label pipeline tests (P4 ledger -> T+10/T+40 labels).

Fully offline: every market input is a synthetic file fixture (D-040 — no
vendor data rows are ever committed). Coverage required by the card:
* P2 口径: T+1 open entry, calendar-indexed T+N target (holiday gap proof),
  行业腿同窗 (SW window == stock window), direction hit semantics;
* 停牌顺延: target-day suspension rolls within max_roll; exhausted ->
  suspension, no fabricated return;
* 窗口内除权: vendor_qfq basis ignores the ex-date price halving, the raw
  basis shows it — the bases never mix silently;
* 未到期不出标签: no row before maturity (incl. as-of intraday open);
* 日历取不到报错退出: missing / empty calendar file and a dead live route
  all fail-close with exit 2 and no label rows;
* append-only + idempotent: re-runs append nothing, the ledger is never
  written back, per-row input_sha256 is recomputable and stable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

import scripts.phase2.expiry_label_pipeline as elp
from tradingagents.dataflows.return_labels import (
    HORIZON_MAX_ROLL_DAYS,
    OutcomeStatus,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "phase2" / "expiry_label_pipeline.py"
FIXED_USER = elp.DEFAULT_USER_ID


# ---------------------------------------------------------------------------
# fixtures


def _weekday_calendar(start: str, n: int, gaps: tuple[int, ...] = ()) -> list[str]:
    """n consecutive Mon–Fri trading dates from ``start``; ``gaps`` inserts
    extra full-week holiday holes after the given indices (calendar-indexing
    proof: weekday counting would drift)."""
    d = date.fromisoformat(start)
    days: list[str] = []
    gap_set = set(gaps)
    i = 0
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
            if len(days) - 1 in gap_set:
                d += timedelta(days=7)  # one-week holiday hole
        d += timedelta(days=1)
    return days


def _bar(symbol: str, d: str, close: float, *, volume: float = 1.0e6,
         open_: float | None = None, is_suspended: bool = False) -> dict:
    o = close if open_ is None else open_
    return {
        "symbol": symbol, "date": d,
        "open": o, "high": max(o, close), "low": min(o, close),
        "close": close, "volume": (0.0 if is_suspended else volume),
        "is_suspended": is_suspended,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _stock_series(symbol: str, cal: list[str], *, base: float = 10.0,
                  step: float = 0.1,
                  suspended: dict[str, bool] | None = None,
                  opens: dict[str, float] | None = None,
                  closes: dict[str, float] | None = None) -> list[dict]:
    rows = []
    for i, d in enumerate(cal):
        c = (closes or {}).get(d, base + step * i)
        o = (opens or {}).get(d, c)
        rows.append(_bar(symbol, d, c, open_=o,
                         is_suspended=bool((suspended or {}).get(d))))
    return rows


def _record(report_id: str, symbol: str, signal_date: str, *,
            direction: str = "看多", user_id: str = FIXED_USER,
            timing_class: str = "H", signal_date_field: str = "signal_date",
            horizons: dict | None = None) -> dict:
    rec = {
        "ledger_version": 1,
        "report_id": report_id,
        "user_id": user_id,
        "symbol": symbol,
        "industry": "银行",
        signal_date_field: signal_date,
        "status": "completed",
        "timing_class": timing_class,
        "input_pit_status": "VERIFIED",
        "backfilled": False,
        "horizons": horizons if horizons is not None else {
            "short": {"direction": direction},
            "medium": {"direction": direction},
        },
        "direction_top": direction,
    }
    rec["record_sha256"] = hashlib.sha256(
        json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return rec


def _write_ledger(path: Path, records: list[dict], *, wrap: bool = False) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            if wrap:
                fh.write(json.dumps({"record": rec, "prev_hash": "x",
                                     "chain_hash": "y"},
                                    ensure_ascii=False) + "\n")
            else:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _offline_inputs(tmp_path: Path, cal: list[str], *,
                    stock_rows: list[dict] | None = None,
                    hs300_rows: list[dict] | None = None,
                    sw_rows: list[dict] | None = None,
                    industry_map: dict | None = None) -> dict:
    bars = tmp_path / "bars.jsonl"
    hs = tmp_path / "hs300.jsonl"
    sw = tmp_path / "sw_daily.jsonl"
    imap = tmp_path / "industry_map.json"
    _write_jsonl(bars, stock_rows if stock_rows is not None else
                 _stock_series("000001.SZ", cal))
    _write_jsonl(hs, hs300_rows if hs300_rows is not None else
                 [_bar("000300.SH", d, 4000.0 + 0.5 * i)
                  for i, d in enumerate(cal)])
    _write_jsonl(sw, sw_rows if sw_rows is not None else
                 [{"index_code": "801010.SI", "date": d, "open": 1000.0,
                   "high": 1000.0, "low": 1000.0, "close": 1000.0,
                   "volume": 1.0e6} for d in cal])
    imap.write_text(json.dumps(industry_map if industry_map is not None
                               else {"000001.SZ": {"code": "801010.SI",
                                                   "name": "银行"}},
                               ensure_ascii=False), "utf-8")
    return {"bars": bars, "hs300": hs, "sw": sw, "imap": imap}


def _run(tmp_path: Path, cal: list[str], records: list[dict], *,
         inputs: dict, as_of: str, out_name: str = "out",
         extra: list[str] | None = None, wrap: bool = False) -> tuple[int, dict, Path]:
    ledger = tmp_path / "forward_ledger.jsonl"
    cal_file = tmp_path / "calendar.txt"
    out_dir = tmp_path / out_name
    _write_ledger(ledger, records, wrap=wrap)
    cal_file.write_text("\n".join(cal) + "\n", "utf-8")
    argv = [
        "run", "--ledger", str(ledger), "--out-dir", str(out_dir),
        "--calendar-file", str(cal_file),
        "--bars-file", str(inputs["bars"]), "--hs300-file", str(inputs["hs300"]),
        "--sw-daily-file", str(inputs["sw"]),
        "--sw-industry-map", str(inputs["imap"]),
        "--as-of", as_of,
    ]
    if extra:
        argv += extra
    rc = elp.main(argv)
    state = {}
    state_path = out_dir / elp.STATE_FILE_NAME
    if state_path.exists():
        state = json.loads(state_path.read_text("utf-8"))
    return rc, state, out_dir


def _read_labels(out_dir: Path, horizon: str) -> list[dict]:
    path = out_dir / elp.LABEL_FILE_NAMES[horizon]
    if not path.exists():
        return []
    return [json.loads(l) for l in
            path.read_text("utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# P2 口径: entry / target / excess / direction


def test_p2_caliber_labels_both_horizons(tmp_path):
    # Calendar with a one-week holiday hole after index 11: weekday counting
    # would drift, calendar indexing must not.
    cal = _weekday_calendar("2026-03-02", 60, gaps=(11,))
    signal = cal[3]
    rec = _record("r1", "000001.SZ", signal)
    inputs = _offline_inputs(tmp_path, cal)
    rc, state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                          as_of=cal[-1])

    assert rc == 0
    assert state["counts"]["labeled"] == 2  # short + medium
    short = _read_labels(out, "short")[0]
    medium = _read_labels(out, "medium")[0]

    for row, offset in ((short, 10), (medium, 40)):
        assert row["entry_date"] == cal[4]              # T+1 open entry
        assert row["target_date"] == cal[3 + offset]    # calendar-indexed T+N
        assert row["actual_exit_date"] == cal[3 + offset]
        assert row["roll_days_used"] == 0
        assert row["outcome_status"] == OutcomeStatus.EVALUATED_OK.value
        assert row["price_basis"] == "vendor_qfq"
        assert row["return_type"] == "price_return"
        # return = (target close - entry open) / entry open
        exp_entry = 10.0 + 0.1 * 4
        exp_exit = 10.0 + 0.1 * (3 + offset)
        assert row["return_pct"] == pytest.approx(
            (exp_exit - exp_entry) / exp_entry * 100.0, abs=5e-4)
        # CSI 300 excess over the SAME execution window
        bmk_entry = 4000.0 + 0.5 * 4
        bmk_exit = 4000.0 + 0.5 * (3 + offset)
        assert row["hs300"]["window_start"] == row["entry_date"]
        assert row["hs300"]["window_end"] == row["actual_exit_date"]
        assert row["hs300_excess_pct"] == pytest.approx(
            row["return_pct"] - (bmk_exit - bmk_entry) / bmk_entry * 100.0,
            abs=1e-3)
        # SW industry leg: 同窗 + same-window excess
        assert row["sw"]["index_code"] == "801010.SI"
        assert row["sw"]["prediction"]["window_start"] == row["entry_date"]
        assert row["sw"]["prediction"]["window_end"] == row["target_date"]
        assert row["sw"]["execution"]["window_start"] == row["entry_date"]
        assert row["sw"]["execution"]["window_end"] == row["actual_exit_date"]
        assert row["sw"]["execution"]["return_pct"] == pytest.approx(0.0)
        assert row["sw_excess_pct"] == pytest.approx(row["return_pct"], abs=1e-3)
        assert row["y_rel_prediction"] is True
        # direction hit: 看多 with a positive return
        assert row["direction_bucket"] == "看多"
        assert row["direction_hit"] is True
        assert row["direction_signed_return_pct"] == row["return_pct"]


def test_short_and_medium_offsets_are_calendar_indexed(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    signal = cal[2]
    rec = _record("r1", "000001.SZ", signal)
    inputs = _offline_inputs(tmp_path, cal)
    rc, _state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[-1])
    assert rc == 0
    short = _read_labels(out, "short")[0]
    medium = _read_labels(out, "medium")[0]
    assert short["eval_offset"] == 10 and medium["eval_offset"] == 40
    assert short["target_date"] == cal[12]
    assert medium["target_date"] == cal[42]
    assert short["roll_days_used"] == 0


def test_direction_hit_variants(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    signal = cal[2]
    records = [
        _record("bull", "000001.SZ", signal, direction="看多"),
        _record("bear", "000002.SZ", signal, direction="看空"),
        _record("neutral", "000003.SZ", signal, direction="中性"),
    ]
    rows = _stock_series("000001.SZ", cal) + \
        _stock_series("000002.SZ", cal, base=20.0) + \
        _stock_series("000003.SZ", cal, base=30.0)
    inputs = _offline_inputs(
        tmp_path, cal, stock_rows=rows,
        industry_map={"000001.SZ": {"code": "801010.SI", "name": "银行"},
                      "000002.SZ": {"code": "801080.SI", "name": "电子"},
                      "000003.SZ": {"code": "801780.SI", "name": "银行"}})
    rc, _state, out = _run(tmp_path, cal, records, inputs=inputs,
                           as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    by_id = {r["report_id"]: r for r in _read_labels(out, "short")}
    assert by_id["bull"]["direction_hit"] is True
    assert by_id["bull"]["direction_signed_return_pct"] == by_id["bull"]["return_pct"]
    assert by_id["bear"]["direction_hit"] is False        # positive return, bear call
    assert by_id["bear"]["direction_signed_return_pct"] == \
        -by_id["bear"]["return_pct"]
    assert by_id["neutral"]["direction_hit"] is None
    assert by_id["neutral"]["direction_signed_return_pct"] is None


def test_bearish_hit_when_return_negative(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    signal = cal[2]
    closes = {d: 10.0 - 0.1 * i for i, d in enumerate(cal)}  # falling market
    rows = _stock_series("000001.SZ", cal, closes=closes)
    inputs = _offline_inputs(tmp_path, cal, stock_rows=rows)
    rec = _record("bear", "000001.SZ", signal, direction="看空")
    rc, _state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    row = _read_labels(out, "short")[0]
    assert row["return_pct"] < 0
    assert row["direction_hit"] is True
    assert row["direction_signed_return_pct"] > 0


# ---------------------------------------------------------------------------
# 停牌顺延 / 窗口内除权


def test_target_suspension_rolls_within_max_roll(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    signal = cal[2]
    target = cal[2 + 10]           # short horizon target
    suspended = {target: True}     # target day suspended -> roll 1 day
    rows = _stock_series("000001.SZ", cal, suspended=suspended)
    inputs = _offline_inputs(tmp_path, cal, stock_rows=rows)
    rec = _record("r1", "000001.SZ", signal)
    rc, _state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    short = _read_labels(out, "short")[0]
    assert short["actual_exit_date"] == cal[2 + 10 + 1]   # rolled exit
    assert short["roll_days_used"] == 1
    assert short["outcome_status"] == OutcomeStatus.EVALUATED_OK.value
    exp_entry = 10.0 + 0.1 * 3
    exp_exit = 10.0 + 0.1 * (2 + 10 + 1)
    assert short["return_pct"] == pytest.approx(
        (exp_exit - exp_entry) / exp_entry * 100.0, abs=5e-4)
    # prediction basis does NOT roll: target suspended -> typed status
    assert short["prediction"]["status"] == "target_suspended"
    # SW execution leg follows the rolled window
    assert short["sw"]["execution"]["window_end"] == short["actual_exit_date"]


def test_roll_exhausted_is_suspension_no_return(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    signal = cal[2]
    # target + both short-horizon roll candidates suspended
    suspended = {cal[2 + 10 + k]: True for k in range(
        1 + HORIZON_MAX_ROLL_DAYS["short"])}
    rows = _stock_series("000001.SZ", cal, suspended=suspended)
    inputs = _offline_inputs(tmp_path, cal, stock_rows=rows)
    rec = _record("r1", "000001.SZ", signal)
    rc, state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                          as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    row = _read_labels(out, "short")[0]
    assert row["outcome_status"] == OutcomeStatus.SUSPENSION.value
    assert row["return_pct"] is None
    assert row["entry_price"] is not None     # entry happened; exit did not
    assert row["exit_price"] is None
    assert row["direction_hit"] is None
    assert row["hs300_excess_pct"] is None
    assert state["counts"]["typed_status"] == 1


def test_unexecutable_entry_no_return(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    signal = cal[2]
    rows = _stock_series("000001.SZ", cal,
                         suspended={cal[3]: True})  # T+1 suspended
    inputs = _offline_inputs(tmp_path, cal, stock_rows=rows)
    rec = _record("r1", "000001.SZ", signal)
    rc, _state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    row = _read_labels(out, "short")[0]
    assert row["outcome_status"] == OutcomeStatus.UNEXECUTABLE_ENTRY.value
    assert row["return_pct"] is None
    assert row["prediction"]["status"] == "unexecutable_entry"


def test_ex_rights_in_window_qfq_vs_raw(tmp_path):
    """10-for-10 split mid-window: qfq series stays continuous (no fake
    crash); raw series shows the halving. The bases never mix silently."""
    cal = _weekday_calendar("2026-03-02", 60)
    signal = cal[2]
    ex_idx = 8                      # ex-date INSIDE the short window
    ex_date = cal[ex_idx]           # (entry cal[3] .. target cal[12])
    raw_closes = {d: (10.0 if i < ex_idx else 5.0)
                  for i, d in enumerate(cal)}
    raw_rows = _stock_series("000001.SZ", cal, closes=raw_closes)
    # qfq: pre-ex prices back-adjusted by the 0.5 factor -> continuous 5.0
    qfq_closes = {d: 5.0 for d in cal}
    qfq_rows = _stock_series("000001.SZ", cal, closes=qfq_closes)

    rec = _record("r1", "000001.SZ", signal)

    # qfq run (production basis)
    (tmp_path / "qfq").mkdir()
    inputs_qfq = _offline_inputs(tmp_path / "qfq", cal, stock_rows=qfq_rows)
    rc, _state, out_qfq = _run(tmp_path / "qfq", cal, [rec],
                               inputs=inputs_qfq, as_of=cal[-1],
                               out_name="out_qfq",
                               extra=["--horizons", "short"])
    assert rc == 0
    row_qfq = _read_labels(out_qfq, "short")[0]
    assert row_qfq["price_basis"] == "vendor_qfq"
    # entry open (pre-ex, qfq) = 5.0; exit close (post-ex) = 5.0 -> 0%
    assert row_qfq["return_pct"] == pytest.approx(0.0, abs=5e-4)
    assert row_qfq["entry_date"] < ex_date < row_qfq["target_date"]

    # raw run: same window on unadjusted prices shows the split crash
    (tmp_path / "raw").mkdir()
    inputs_raw = _offline_inputs(tmp_path / "raw", cal, stock_rows=raw_rows)
    rc, _state, out_raw = _run(tmp_path / "raw", cal, [rec],
                               inputs=inputs_raw, as_of=cal[-1],
                               out_name="out_raw",
                               extra=["--horizons", "short",
                                      "--price-basis", "raw"])
    assert rc == 0
    row_raw = _read_labels(out_raw, "short")[0]
    assert row_raw["price_basis"] == "raw"
    assert row_raw["return_pct"] == pytest.approx(-50.0, abs=5e-4)
    # the two bases differ exactly at the ex-event — never mixed
    assert row_qfq["return_pct"] != row_raw["return_pct"]


# ---------------------------------------------------------------------------
# 未到期不出标签


def test_not_matured_no_label(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    signal = cal[2]
    rec = _record("r1", "000001.SZ", signal)
    inputs = _offline_inputs(tmp_path, cal)

    # target T+10 = cal[12] > as_of=cal[10] -> not matured (both horizons)
    rc, state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                          as_of=cal[10])
    assert rc == 0
    assert state["counts"]["not_matured"] == 2
    assert state["counts"]["labeled"] == 0
    assert not (out / elp.LABEL_FILE_NAMES["short"]).exists()
    assert not (out / elp.LABEL_FILE_NAMES["medium"]).exists()

    # target == as_of with market still open -> not matured
    rc, state, out2 = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[12], out_name="out_open",
                           extra=["--as-of-market-open"])
    assert rc == 0
    assert state["counts"]["not_matured"] == 2
    assert not (out2 / elp.LABEL_FILE_NAMES["short"]).exists()

    # target == as_of, market closed (default) -> short matured;
    # medium T+40 is still far out -> not matured
    rc, state, out3 = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[12], out_name="out_closed")
    assert rc == 0
    assert state["counts"]["labeled"] == 1
    assert state["counts"]["not_matured"] == 1
    assert len(_read_labels(out3, "short")) == 1
    assert _read_labels(out3, "medium") == []


def test_medium_not_matured_but_short_is(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    signal = cal[2]
    rec = _record("r1", "000001.SZ", signal)
    inputs = _offline_inputs(tmp_path, cal)
    rc, state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                          as_of=cal[20])
    assert rc == 0
    assert len(_read_labels(out, "short")) == 1      # T+10 matured
    assert _read_labels(out, "medium") == []          # T+40 not matured
    assert state["counts"]["not_matured"] == 1
    assert state["counts"]["labeled"] == 1


# ---------------------------------------------------------------------------
# 日历取不到报错退出


def test_calendar_file_missing_fail_close(tmp_path, capsys):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    ledger = tmp_path / "forward_ledger.jsonl"
    out_dir = tmp_path / "out"
    _write_ledger(ledger, [rec])
    rc = elp.main([
        "run", "--ledger", str(ledger), "--out-dir", str(out_dir),
        "--calendar-file", str(tmp_path / "missing.txt"),
        "--bars-file", str(inputs["bars"]), "--hs300-file", str(inputs["hs300"]),
        "--sw-daily-file", str(inputs["sw"]),
        "--sw-industry-map", str(inputs["imap"]),
        "--as-of", cal[-1],
    ])
    assert rc == elp.EXIT_FAIL_CLOSED
    err = capsys.readouterr().err
    assert "FAIL-CLOSE" in err and "交易日历取不到" in err
    assert not (out_dir / elp.LABEL_FILE_NAMES["short"]).exists()


def test_calendar_file_empty_fail_close(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    ledger = tmp_path / "forward_ledger.jsonl"
    empty = tmp_path / "empty_calendar.txt"
    empty.write_text("\n\n", "utf-8")
    out_dir = tmp_path / "out"
    _write_ledger(ledger, [rec])
    rc = elp.main([
        "run", "--ledger", str(ledger), "--out-dir", str(out_dir),
        "--calendar-file", str(empty),
        "--bars-file", str(inputs["bars"]), "--hs300-file", str(inputs["hs300"]),
        "--sw-daily-file", str(inputs["sw"]),
        "--sw-industry-map", str(inputs["imap"]),
        "--as-of", cal[-1],
    ])
    assert rc == elp.EXIT_FAIL_CLOSED
    assert not (out_dir / elp.LABEL_FILE_NAMES["short"]).exists()


def test_live_calendar_route_failure_fail_close(tmp_path, monkeypatch):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    ledger = tmp_path / "forward_ledger.jsonl"
    out_dir = tmp_path / "out"
    _write_ledger(ledger, [rec])

    import tradingagents.dataflows.trade_calendar as tc

    def _boom():
        raise RuntimeError("akshare down")

    monkeypatch.setattr(tc, "require_cn_trade_dates", _boom)
    rc = elp.main([
        "run", "--ledger", str(ledger), "--out-dir", str(out_dir),
        "--bars-file", str(inputs["bars"]), "--hs300-file", str(inputs["hs300"]),
        "--sw-daily-file", str(inputs["sw"]),
        "--sw-industry-map", str(inputs["imap"]),
        "--as-of", cal[-1],
    ])
    assert rc == elp.EXIT_FAIL_CLOSED
    assert not (out_dir / elp.LABEL_FILE_NAMES["short"]).exists()
    # no label rows: nothing was appended before the calendar check
    assert not (out_dir / elp.LABEL_FILE_NAMES["short"]).exists()


# ---------------------------------------------------------------------------
# append-only / idempotent / no write-back


def test_rerun_appends_nothing_and_file_is_stable(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    ledger = tmp_path / "forward_ledger.jsonl"
    _write_ledger(ledger, [rec])
    cal_file = tmp_path / "calendar.txt"
    cal_file.write_text("\n".join(cal) + "\n", "utf-8")
    out_dir = tmp_path / "out"

    argv = [
        "run", "--ledger", str(ledger), "--out-dir", str(out_dir),
        "--calendar-file", str(cal_file),
        "--bars-file", str(inputs["bars"]), "--hs300-file", str(inputs["hs300"]),
        "--sw-daily-file", str(inputs["sw"]),
        "--sw-industry-map", str(inputs["imap"]),
        "--as-of", cal[-1],
    ]
    assert elp.main(argv) == 0
    files = {h: (out_dir / elp.LABEL_FILE_NAMES[h]).read_bytes()
             for h in elp.HORIZONS}
    ledger_sha_before = hashlib.sha256(ledger.read_bytes()).hexdigest()

    # second run: nothing appended, byte-identical files
    assert elp.main(argv) == 0
    for h in elp.HORIZONS:
        assert (out_dir / elp.LABEL_FILE_NAMES[h]).read_bytes() == files[h]
    state = json.loads((out_dir / elp.STATE_FILE_NAME).read_text("utf-8"))
    assert state["counts"]["skipped_existing"] == 2
    assert state["counts"]["labeled"] == 0
    assert state["counts"]["not_matured"] == 0
    # the ledger itself is never written back
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == ledger_sha_before


def test_append_only_new_record_only(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    r1 = _record("r1", "000001.SZ", cal[2])
    r2 = _record("r2", "000002.SZ", cal[3])
    inputs = _offline_inputs(
        tmp_path, cal,
        stock_rows=_stock_series("000001.SZ", cal)
        + _stock_series("000002.SZ", cal, base=20.0),
        industry_map={"000001.SZ": {"code": "801010.SI", "name": "银行"},
                      "000002.SZ": {"code": "801080.SI", "name": "电子"}})
    ledger = tmp_path / "forward_ledger.jsonl"
    _write_ledger(ledger, [r1])
    cal_file = tmp_path / "calendar.txt"
    cal_file.write_text("\n".join(cal) + "\n", "utf-8")
    out_dir = tmp_path / "out"
    argv = [
        "run", "--ledger", str(ledger), "--out-dir", str(out_dir),
        "--calendar-file", str(cal_file),
        "--bars-file", str(inputs["bars"]), "--hs300-file", str(inputs["hs300"]),
        "--sw-daily-file", str(inputs["sw"]),
        "--sw-industry-map", str(inputs["imap"]),
        "--as-of", cal[-1], "--horizons", "short",
    ]
    assert elp.main(argv) == 0
    first_bytes = (out_dir / elp.LABEL_FILE_NAMES["short"]).read_bytes()
    assert first_bytes.count(b"\n") == 1

    # ledger grows (append-only upstream); pipeline appends only the new key
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(r2, ensure_ascii=False) + "\n")
    assert elp.main(argv) == 0
    after = (out_dir / elp.LABEL_FILE_NAMES["short"]).read_bytes()
    assert after.startswith(first_bytes)          # old rows untouched
    rows = [json.loads(l) for l in after.splitlines() if l.strip()]
    assert [r["report_id"] for r in rows] == ["r1", "r2"]


def test_ledger_and_reports_never_written_back(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    ledger_dir = tmp_path / "ledger_dir"
    ledger_dir.mkdir()
    ledger = ledger_dir / "forward_ledger.jsonl"
    fake_db = ledger_dir / "tradingagents.db"
    _write_ledger(ledger, [rec])
    fake_db.write_bytes(b"SQLITE-FAKE")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(ledger_dir.iterdir())}
    rc, _state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[-1])
    assert rc == 0
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(ledger_dir.iterdir())}
    assert before == after                        # no new files, no changes
    assert set(before) == {"forward_ledger.jsonl", "tradingagents.db"}
    assert out != ledger_dir


def test_wrapped_ledger_lines_accepted(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    rc, _state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[-1], wrap=True,
                           extra=["--horizons", "short"])
    assert rc == 0
    assert len(_read_labels(out, "short")) == 1


# ---------------------------------------------------------------------------
# input hash


def _recompute_input_hash(out_dir: Path, horizon: str, cal: list[str],
                          inputs: dict, record: dict) -> str:
    """Recompute the row's input_sha256 from the recorded inputs."""
    row = _read_labels(out_dir, horizon)[0]
    from tradingagents.dataflows.return_labels import (
        HORIZON_MAX_ROLL_DAYS as MAX_ROLL,
    )
    idx = cal.index(row["signal_date"])
    end_idx = min(idx + row["eval_offset"] + MAX_ROLL[row["horizon"]] + 1,
                  len(cal) - 1)
    stock_bars = {}
    for line in inputs["bars"].read_text("utf-8").splitlines():
        obj = json.loads(line)
        if obj["symbol"] == row["symbol"] and \
                row["signal_date"] <= obj["date"] <= cal[end_idx]:
            stock_bars[obj["date"]] = {
                "date": obj["date"], "open": obj["open"],
                "high": obj["high"], "low": obj["low"],
                "close": obj["close"], "volume": obj["volume"],
                "is_suspended": obj.get("is_suspended", False)}
    bmk_bars = {}
    for line in inputs["hs300"].read_text("utf-8").splitlines():
        obj = json.loads(line)
        if row["entry_date"] <= obj["date"] <= row["actual_exit_date"]:
            bmk_bars[obj["date"]] = {
                "date": obj["date"], "open": obj["open"],
                "high": obj["high"], "low": obj["low"],
                "close": obj["close"], "volume": obj["volume"],
                "is_suspended": obj.get("is_suspended", False)}
    sw_bars = {}
    for line in inputs["sw"].read_text("utf-8").splitlines():
        obj = json.loads(line)
        if obj["index_code"] == row["sw"]["index_code"] and \
                row["entry_date"] <= obj["date"] <= row["actual_exit_date"]:
            sw_bars.setdefault(obj["index_code"], {})[obj["date"]] = {
                "open": obj["open"], "high": obj["high"],
                "low": obj["low"], "close": obj["close"]}
    return elp._compute_input_hash(
        report_id=row["report_id"], symbol=row["symbol"],
        signal_date=row["signal_date"], horizon=row["horizon"],
        eval_offset=row["eval_offset"],
        ledger_record_sha256=row["ledger_record_sha256"],
        price_basis=row["price_basis"], return_type=row["return_type"],
        direction_raw=row["direction_raw"],
        stock_bars=stock_bars, benchmark_bars=bmk_bars,
        sw_bars=sw_bars, sw_index_code=row["sw"]["index_code"])


def test_input_hash_recomputable_and_stable(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    rc, _state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    row = _read_labels(out, "short")[0]
    assert row["input_sha256"]
    assert row["ledger_record_sha256"] == rec["record_sha256"]
    # recomputed from the recorded inputs -> identical
    assert _recompute_input_hash(out, "short", cal, inputs, rec) == \
        row["input_sha256"]

    # same inputs, fresh out dir -> identical hash (determinism)
    rc, _state, out2 = _run(tmp_path, cal, [rec], inputs=inputs,
                            as_of=cal[-1], out_name="out2",
                            extra=["--horizons", "short"])
    assert rc == 0
    assert _read_labels(out2, "short")[0]["input_sha256"] == row["input_sha256"]

    # a changed market input (one bar's close) -> different hash, and the
    # append-only file keeps the old row (never silently overwritten)
    moved = json.loads(inputs["bars"].read_text("utf-8").splitlines()[5])
    moved["close"] = moved["close"] + 1.0
    lines = inputs["bars"].read_text("utf-8").splitlines()
    lines[5] = json.dumps(moved, ensure_ascii=False)
    inputs["bars"].write_text("\n".join(lines) + "\n", "utf-8")
    rc, state, out3 = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[-1], out_name="out3",
                           extra=["--horizons", "short"])
    assert rc == 0
    row3 = _read_labels(out3, "short")[0]
    assert row3["input_sha256"] != row["input_sha256"]


def test_determinism_from_scratch_byte_identical(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    records = [_record("r1", "000001.SZ", cal[2]),
               _record("r2", "000002.SZ", cal[3], direction="看空")]
    inputs = _offline_inputs(
        tmp_path, cal,
        stock_rows=_stock_series("000001.SZ", cal)
        + _stock_series("000002.SZ", cal, base=20.0),
        industry_map={"000001.SZ": {"code": "801010.SI", "name": "银行"},
                      "000002.SZ": {"code": "801080.SI", "name": "电子"}})
    rc, _s, out_a = _run(tmp_path, cal, records, inputs=inputs,
                         as_of=cal[-1], out_name="a")
    rc2, _s2, out_b = _run(tmp_path, cal, records, inputs=inputs,
                           as_of=cal[-1], out_name="b")
    assert rc == 0 and rc2 == 0
    for h in elp.HORIZONS:
        assert (out_a / elp.LABEL_FILE_NAMES[h]).read_bytes() == \
            (out_b / elp.LABEL_FILE_NAMES[h]).read_bytes()


# ---------------------------------------------------------------------------
# typed statuses / exclusions


def test_typed_status_symbol_excluded_bse(tmp_path):
    """DAV-1880 M-1: 北交所 .BJ 标的必须走 typed 分支，绝不产出 evaluated_ok。

    股票池规则（V-03/DAV-800 口径）排除北交所；canonicalize_symbol 对
    8xx/4xx/920 返回 EXCLUDED_BSE（带 canonical_symbol）——若只挡
    unmappable，BJ 标的会正常取行情并污染 T+10/T+40 统计口径。
    """
    cal = _weekday_calendar("2026-03-02", 60)
    bj_suffixed = _record("bj1", "830001.BJ", cal[2])
    bj_bare = _record("bj2", "830001", cal[2])
    sh_normal = _record("sh1", "600519.SH", cal[2])
    stock_rows = (
        _stock_series("830001.BJ", cal)
        + _stock_series("600519.SH", cal, base=50.0))
    inputs = _offline_inputs(
        tmp_path, cal, stock_rows=stock_rows,
        industry_map={"830001.BJ": {"code": "801010.SI", "name": "银行"},
                      "600519.SH": {"code": "801120.SI", "name": "食品饮料"}})
    rc, state, out = _run(
        tmp_path, cal, [bj_suffixed, bj_bare, sh_normal], inputs=inputs,
        as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    rows = {r["report_id"]: r for r in _read_labels(out, "short")}
    # 北交所（带后缀与裸代码两种写法）-> typed status，无收益
    for rid in ("bj1", "bj2"):
        row = rows[rid]
        assert row["outcome_status"] == elp.STATUS_SYMBOL_EXCLUDED_BSE
        assert row["symbol"] is None
        assert row["symbol_raw"] in ("830001.BJ", "830001")
        assert row["return_pct"] is None
        assert row["entry_price"] is None
        assert row["input_sha256"] is None
    # 同批沪深标的照常评估：排除是定向的，不是一刀切回归
    assert rows["sh1"]["outcome_status"] == OutcomeStatus.EVALUATED_OK.value
    assert rows["sh1"]["return_pct"] is not None
    assert state["counts"]["typed_status"] == 2
    assert state["counts"]["labeled"] == 1


def test_typed_status_symbol_unmappable(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "AGENT", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    rc, state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                          as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    row = _read_labels(out, "short")[0]
    assert row["outcome_status"] == elp.STATUS_SYMBOL_UNMAPPABLE
    assert row["symbol"] is None
    assert row["symbol_raw"] == "AGENT"
    assert state["counts"]["typed_status"] == 1


def test_typed_status_signal_date_not_trading_day(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", "2026-03-01")   # Sunday
    inputs = _offline_inputs(tmp_path, cal)
    rc, state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                          as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    row = _read_labels(out, "short")[0]
    assert row["outcome_status"] == elp.STATUS_SIGNAL_DATE_NOT_IN_CALENDAR
    assert state["counts"]["typed_status"] == 1


def test_typed_status_calendar_insufficient_coverage(tmp_path):
    # matured medium sample, but the calendar ends inside the roll band
    cal = _weekday_calendar("2026-03-02", 45)
    signal = cal[2]
    rec = _record("r1", "000001.SZ", signal)
    inputs = _offline_inputs(tmp_path, cal)
    rc, state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                          as_of="2026-12-31", extra=["--horizons", "medium"])
    assert rc == 0
    row = _read_labels(out, "medium")[0]
    assert row["outcome_status"] == elp.STATUS_CALENDAR_INSUFFICIENT
    assert state["counts"]["typed_status"] == 1


def test_typed_status_stock_bars_unavailable(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000009.SZ", cal[2])   # no bars in the file
    inputs = _offline_inputs(tmp_path, cal)
    rc, state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                          as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    row = _read_labels(out, "short")[0]
    assert row["outcome_status"] == elp.STATUS_STOCK_BARS_UNAVAILABLE
    assert state["counts"]["typed_status"] == 1


def test_fixed_account_and_invalid_signal_date_exclusions(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    other = _record("other", "000001.SZ", cal[2],
                    user_id="11111111-1111-1111-1111-111111111111")
    bad_date = _record("bad", "000001.SZ", "not-a-date")
    good = _record("good", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    rc, state, out = _run(tmp_path, cal, [other, bad_date, good],
                          inputs=inputs, as_of=cal[-1],
                          extra=["--horizons", "short"])
    assert rc == 0
    assert state["counts"]["excluded_other_user"] == 1
    assert state["counts"]["excluded_invalid_signal_date"] == 1
    assert state["counts"]["labeled"] == 1
    assert len(_read_labels(out, "short")) == 1


def test_unparseable_ledger_line_fail_close(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    ledger = tmp_path / "forward_ledger.jsonl"
    cal_file = tmp_path / "calendar.txt"
    cal_file.write_text("\n".join(cal) + "\n", "utf-8")
    _write_ledger(ledger, [rec])
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write("{broken json\n")
    out_dir = tmp_path / "out"
    rc = elp.main([
        "run", "--ledger", str(ledger), "--out-dir", str(out_dir),
        "--calendar-file", str(cal_file),
        "--bars-file", str(inputs["bars"]), "--hs300-file", str(inputs["hs300"]),
        "--sw-daily-file", str(inputs["sw"]),
        "--sw-industry-map", str(inputs["imap"]),
        "--as-of", cal[-1],
    ])
    assert rc == elp.EXIT_FAIL_CLOSED


# ---------------------------------------------------------------------------
# verify / status


def test_verify_ok_and_failures(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    rc, _state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                           as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    assert elp.main(["verify", "--out-dir", str(out)]) == elp.EXIT_OK

    # torn / unreadable line -> verify fails
    path = out / elp.LABEL_FILE_NAMES["short"]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"pipeline": "torn\n')
    assert elp.main(["verify", "--out-dir", str(out)]) == elp.EXIT_VERIFY_FAIL

    # duplicate key -> verify fails
    rows = [l for l in path.read_text("utf-8").splitlines()
            if l.strip() and "torn" not in l]
    rows.append(rows[0])
    path.write_text("\n".join(rows) + "\n", "utf-8")
    assert elp.main(["verify", "--out-dir", str(out)]) == elp.EXIT_VERIFY_FAIL

    assert elp.main(["status", "--out-dir", str(out)]) == elp.EXIT_OK


def test_cli_subprocess_exit_codes(tmp_path):
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    ledger = tmp_path / "forward_ledger.jsonl"
    cal_file = tmp_path / "calendar.txt"
    cal_file.write_text("\n".join(cal) + "\n", "utf-8")
    _write_ledger(ledger, [rec])
    out_dir = tmp_path / "out"
    base = [
        sys.executable, str(SCRIPT), "run",
        "--ledger", str(ledger), "--out-dir", str(out_dir),
        "--calendar-file", str(cal_file),
        "--bars-file", str(inputs["bars"]), "--hs300-file", str(inputs["hs300"]),
        "--sw-daily-file", str(inputs["sw"]),
        "--sw-industry-map", str(inputs["imap"]),
        "--as-of", cal[-1], "--horizons", "short",
    ]
    proc = subprocess.run(base, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (out_dir / elp.LABEL_FILE_NAMES["short"]).exists()

    # missing calendar -> exit 2 via the real CLI
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "run",
         "--ledger", str(ledger), "--out-dir", str(tmp_path / "out2"),
         "--calendar-file", str(tmp_path / "nope.txt"),
         "--bars-file", str(inputs["bars"]), "--hs300-file", str(inputs["hs300"]),
         "--sw-daily-file", str(inputs["sw"]),
         "--sw-industry-map", str(inputs["imap"]),
         "--as-of", cal[-1]],
        capture_output=True, text=True)
    assert proc.returncode == 2
    assert "FAIL-CLOSE" in proc.stderr


# ---------------------------------------------------------------------------
# DAV-2041 — SW-leg degradation observability, hardened fail-close (DAV-2034
# 总控 10-14 令): an ONLINE run without TUSHARE_TOKEN exits non-zero with
# zero rows appended — the append-only key set can never be re-labelled, so
# an unauthenticated run must fail before the first row is written.


def _run_online_sw_stub(tmp_path: Path, cal: list[str], records: list[dict],
                        monkeypatch, *, sw_member: dict | None,
                        as_of: str, out_name: str = "out") -> tuple[int, Path]:
    """Drive `run` through the ONLINE code path (no --*-file args) with all
    market access stubbed: vendor bars synthetic, SW membership/sw_daily
    stubbed to `sw_member` (None => the tushare-rejected gap path)."""
    ledger = tmp_path / "forward_ledger.jsonl"
    cal_file = tmp_path / "calendar.txt"
    out_dir = tmp_path / out_name
    if records:
        _write_ledger(ledger, records)
    cal_file.write_text("\n".join(cal) + "\n", "utf-8")

    stock = {r["date"]: r for r in _stock_series("000001.SZ", cal)}
    hs300 = {r["date"]: r for r in _stock_series("000300.SH", cal, base=4000.0)}

    class _Bar:
        def __init__(self, d):
            self.open = d["open"]; self.high = d["high"]
            self.low = d["low"]; self.close = d["close"]
            self.volume = d["volume"]; self.is_suspended = d["is_suspended"]

    class _StubProvider:
        def get_bar(self, symbol, d):
            src = hs300 if symbol == "000300.SH" else stock
            row = src.get(d)
            return _Bar(row) if row else None

    monkeypatch.setattr(
        "tradingagents.eval.v03_return_measure.VendorPriceDataProvider",
        lambda *a, **k: _StubProvider())
    monkeypatch.setattr(
        "tradingagents.dataflows.sw_benchmark.default_sw_daily_fetcher",
        lambda code, s, e: {d: {"open": 1.0, "high": 1.0, "low": 1.0,
                                "close": 1.0} for d in cal if s <= d <= e})
    monkeypatch.setattr(
        "tradingagents.dataflows.sw_industry.resolve_sw_l1_benchmark",
        lambda symbol, analysis_date: (
            (sw_member, None) if sw_member is not None
            else (None, "【数据获取失败】申万一级行业基准：index_classify_unavailable")))

    rc = elp.main([
        "run", "--ledger", str(ledger), "--out-dir", str(out_dir),
        "--calendar-file", str(cal_file), "--as-of", as_of,
        "--horizons", "short",
    ])
    return rc, out_dir


def test_online_run_without_token_fail_close_zero_rows(tmp_path, monkeypatch):
    """DAV-2034 10-14 令: online mode (no --*-file inputs) with empty
    TUSHARE_TOKEN -> non-zero exit AND zero label rows appended — not a
    WARN, because append-only gap rows are irreversible."""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    cal = _weekday_calendar("2026-03-02", 60)
    recs = [_record(f"r{i}", "000001.SZ", cal[2 + i]) for i in range(3)]
    rc, out = _run_online_sw_stub(tmp_path, cal, recs, monkeypatch,
                                  sw_member=None, as_of=cal[20])
    assert rc == elp.EXIT_FAIL_CLOSED
    # zero rows written: no label file may exist at all
    assert not (out / elp.LABEL_FILE_NAMES["short"]).exists()
    assert not (out / elp.LABEL_FILE_NAMES["medium"]).exists()
    # state file records the aborted run is not written either — the run
    # never reached the state-write stage
    assert not (out / elp.STATE_FILE_NAME).exists()


def test_offline_run_without_token_runs_normally(tmp_path, monkeypatch):
    """Offline mode needs no TUSHARE_TOKEN at all — file inputs fully
    drive the SW leg, so a missing token must NOT block replay/test runs."""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    inputs = _offline_inputs(tmp_path, cal)
    rc, state, out = _run(tmp_path, cal, [rec], inputs=inputs,
                          as_of=cal[-1], extra=["--horizons", "short"])
    assert rc == 0
    assert state["counts"]["labeled"] == 1
    assert state["sw_leg_missing"] is False


def test_sw_gap_fail_loud_when_tushare_degrades(tmp_path, monkeypatch, capsys):
    """DAV-2041 🟡-1 (post-10-14 semantics): token present but the Tushare
    membership route rejects/degrades every row -> run still exits 0
    (typed-status semantics, degradation discovered per-row) but `verify`
    MUST fail loud instead of letting the timer report all-green."""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-present")
    cal = _weekday_calendar("2026-03-02", 60)
    recs = [_record(f"r{i}", "000001.SZ", cal[2 + i]) for i in range(3)]
    rc, out = _run_online_sw_stub(tmp_path, cal, recs, monkeypatch,
                                  sw_member=None, as_of=cal[20])
    assert rc == 0
    rows = _read_labels(out, "short")
    assert len(rows) == 3
    assert all(r["sw"]["membership_status"] == "gap" for r in rows)

    capsys.readouterr()  # drain run output
    assert elp.main(["verify", "--out-dir", str(out)]) == elp.EXIT_VERIFY_FAIL
    err = capsys.readouterr().err
    assert "membership_status=gap" in err


def test_sw_gap_warn_only_when_minority(tmp_path, monkeypatch, capsys):
    """Mixed file (some resolved, some gap) stays OK on verify — the
    fail-loud gate is all-gap (or >=90%), not any-gap."""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-present")
    cal = _weekday_calendar("2026-03-02", 60)
    member = {"src": "SW2021", "level": "L1", "code": "801010.SI", "name": "银行"}
    # 1 resolved row first...
    rc, out = _run_online_sw_stub(tmp_path, cal, [_record("r0", "000001.SZ", cal[2])],
                                  monkeypatch, sw_member=member, as_of=cal[20])
    assert rc == 0
    # ...then run again against the SAME out dir with the ledger now
    # carrying r1 — this time the membership resolver degrades to gap
    rec2 = _record("r1", "000001.SZ", cal[3])
    ledger = tmp_path / "forward_ledger.jsonl"
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec2, ensure_ascii=False) + "\n")
    rc, out = _run_online_sw_stub(tmp_path, cal, [], monkeypatch,
                                  sw_member=None, as_of=cal[20], out_name="out")
    assert rc == 0
    rows = _read_labels(out, "short")
    assert len(rows) == 2
    assert [r["sw"]["membership_status"] for r in rows].count("gap") == 1

    capsys.readouterr()
    assert elp.main(["verify", "--out-dir", str(out)]) == elp.EXIT_OK
    assert "WARN" in capsys.readouterr().out


def test_sw_gap_denominator_excludes_pre_sw_typed_rows(tmp_path, monkeypatch, capsys):
    """DAV-2043 🟡: typed rows that never reach SW resolution (BSE-excluded
    symbols, unmappable, non-trading signal date, missing stock bars,
    resolution failure) are structurally membership=gap — they must NOT
    inflate the verify gap-ratio denominator into a false-positive
    SW-degradation alarm."""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-present")
    cal = _weekday_calendar("2026-03-02", 60)
    member = {"src": "SW2021", "level": "L1", "code": "801010.SI", "name": "银行"}
    # 9 BSE-excluded rows (typed pre-SW) + 1 properly evaluated row.
    recs = [_record(f"b{i}", "830799.BJ", cal[2]) for i in range(9)]
    recs.append(_record("ok1", "000001.SZ", cal[2]))
    rc, out = _run_online_sw_stub(tmp_path, cal, recs, monkeypatch,
                                  sw_member=member, as_of=cal[20])
    assert rc == 0
    rows = _read_labels(out, "short")
    assert len(rows) == 10
    assert sum(1 for r in rows
               if r["sw"]["membership_status"] == "gap") == 9

    capsys.readouterr()
    # 90% raw gap ratio, but only 0/1 of the sw-eligible denominator -> OK.
    assert elp.main(["verify", "--out-dir", str(out)]) == elp.EXIT_OK


def test_sw_leg_missing_flag_in_run_state(tmp_path, monkeypatch, capsys):
    """run state still carries sw_leg_missing for observability; with a
    token present the flag is False even when the route degrades."""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-present")
    cal = _weekday_calendar("2026-03-02", 60)
    rec = _record("r1", "000001.SZ", cal[2])
    rc, out = _run_online_sw_stub(tmp_path, cal, [rec], monkeypatch,
                                  sw_member=None, as_of=cal[20])
    assert rc == 0
    state = json.loads((out / elp.STATE_FILE_NAME).read_text("utf-8"))
    assert state["sw_leg_missing"] is False
    assert state["files"][elp.LABEL_FILE_NAMES["short"]]["sw_gap_appended"] == 1
    assert "all appended rows have sw membership gap" in \
        (out / "label_pipeline.log").read_text("utf-8")


# ---------------------------------------------------------------------------
# DAV-2058 打回复修 — wrapper shell gates (run_expiry_label_daily.sh)
#
# These tests exercise the REAL wrapper script as a subprocess (it is the
# code that launchd runs; the .env gate only exists in shell). The wrapper's
# fixed paths are shadowed by PATH stubs so no real credential file, ledger
# or python interpreter is touched.


WRAPPER = REPO / "scripts" / "phase2" / "run_expiry_label_daily.sh"


def _wrapper_harness(tmp_path: Path, env_lines: list[str]):
    """Run the REAL wrapper script under a shadowed runtime and return
    (proc, sandbox_dir, alert_text). Only the path constants in the copied
    script are rewritten; a stub interpreter makes the trading-day check
    answer "non-trading day" so the wrapper exits 0 immediately AFTER the
    .env gate — meaning: rc==16 => the gate rejected the file (the real
    assertion of every evil-line test), and rc==0 => the gate accepted it
    (non-trading-day skip path). A rejected .env is never sourced, so an
    evil line's payload can be checked for non-execution via an untouched
    sentinel file."""
    import re as _re
    import shutil
    import stat

    tmp_path.mkdir(parents=True, exist_ok=True)
    sandbox = tmp_path / "wrapper_sandbox"
    bin_dir = sandbox / "bin"
    work_root = sandbox / "work"
    code_dir = sandbox / "code"
    for d in (bin_dir, work_root / "phase2-labels",
              work_root / "phase2-ledger",
              code_dir / "scripts" / "phase2"):
        d.mkdir(parents=True, exist_ok=True)
    (work_root / "phase2-ledger" / "forward_ledger.jsonl").write_text(
        "", "utf-8")

    env_file = sandbox / "test.env"
    env_file.write_text("\n".join(env_lines) + "\n", "utf-8")

    # stub for the fixed PYTHON path: the trading-day check is the only
    # invocation we let through; it answers NON-trading day so the wrapper
    # takes the early `exit 0` skip and never reaches run/gap/verify.
    stub = """#!/usr/bin/env bash
input="$(cat 2>/dev/null)"
case "$input" in
    *is_cn_trading_day*) echo "2026-10-14 False"; exit 0 ;;
esac
exit 0
"""
    stub_path = bin_dir / "python_stub"
    stub_path.write_text(stub, "utf-8")
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR
                    | stat.S_IXGRP | stat.S_IXOTH)

    src = WRAPPER.read_text("utf-8")
    src = _re.sub(r'^PYTHON=.*$', f'PYTHON="{stub_path}"', src, flags=_re.M)
    src = _re.sub(r'^ENV_FILE=.*$', f'ENV_FILE="{env_file}"', src,
                  flags=_re.M)
    src = _re.sub(r'^CODE_DIR=.*$', f'CODE_DIR="{code_dir}"', src,
                  flags=_re.M)
    src = _re.sub(r'^WORK_ROOT=.*$', f'WORK_ROOT="{work_root}"', src,
                  flags=_re.M)
    shutil.copy(REPO / "scripts" / "phase2" / "expiry_label_pipeline.py",
                code_dir / "scripts" / "phase2" / "expiry_label_pipeline.py")
    wrapper_copy = sandbox / "wrapper.sh"
    wrapper_copy.write_text(src, "utf-8")

    proc = subprocess.run(
        ["/bin/bash", str(wrapper_copy)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    alert = work_root / "phase2-labels" / "ALERT.launchd"
    return (proc, sandbox,
            alert.read_text("utf-8") if alert.exists() else "")


def test_env_gate_rejects_assignment_prefixed_command(tmp_path):
    """DAV-2058 🟡-1: `KEY=value touch <file>` passed the OLD head-anchored
    regex and `source` then executed `touch`. The tightened value-whitelist
    + line-end anchor must reject it at exit 16 — and the sentinel file
    must NOT exist afterwards (proves the line was never sourced)."""
    pwned = tmp_path / "PWNED_env_cmd"
    proc, _, alert = _wrapper_harness(
        tmp_path,
        [f'EVIL=harmless touch {pwned}',
         'TUSHARE_TOKEN=fake'])
    assert proc.returncode == 16, (proc.returncode, proc.stderr, alert)
    assert not pwned.exists()
    assert "non KEY=VALUE lines" in alert


def test_env_gate_rejects_metachar_injection_variants(tmp_path):
    """Same threat class, different spellings — every line carries a valid
    `KEY=value` prefix followed by a command or redirection; all must
    exit 16 and none may have been sourced."""
    shapes = (';touch {p}', ' touch {p}', '|tee {p}', '`touch {p}`',
              '$(touch {p})', '>{p}', '>>{p}', '{{1,2}}id {p}')
    for i, frag in enumerate(shapes):
        sub = tmp_path / f"h{i}"
        pwned = sub / "PWNED_meta"
        proc, _, _ = _wrapper_harness(sub, ['K=v' + frag.format(p=pwned)])
        assert proc.returncode == 16, (frag, proc.returncode)
        assert not pwned.exists(), frag


def test_env_gate_rejects_dquoted_substitution_payloads(tmp_path):
    """DAV-2072 🟡-1: the previous double-quoted alternative excluded only
    `"` and `\\`, so `K="$(touch ...)"` and K="`touch ...`" passed the gate
    and `source` then EXECUTED the substitution (sandbox-verified: the
    sentinel file was created). With $/`/\\ excluded a double-quoted value
    is literal text only — each payload must exit 16 and leave no file."""
    frags = ('"$(touch {p})"', '"`touch {p}`"',
             '"${{BASH_ENV:-$(touch {p})}}"', '"x\\;touch {p}"')
    for i, frag in enumerate(frags):
        sub = tmp_path / f"dq{i}"
        pwned = sub / "PWNED_dq"
        proc, _, alert = _wrapper_harness(
            sub, ['K=' + frag.format(p=pwned), 'TUSHARE_TOKEN=fake'])
        assert proc.returncode == 16, (frag, proc.returncode, alert)
        assert not pwned.exists(), frag


def test_env_gate_rejects_dquoted_variable_expansion(tmp_path):
    """DAV-2072 🟡-1 companion: `K="${HOME}/x"` needs no command
    substitution to be dangerous — a sourced variable expansion can
    retarget paths (e.g. shadow HOME) and re-opens the dollar door. Any
    `$` inside double quotes is rejected, even without a payload."""
    for i, line in enumerate(('K="${HOME}/x"', 'K="$HOME/x"',
                              'K="a${B}b"')):
        proc, _, _ = _wrapper_harness(tmp_path / f"dv{i}", [line])
        assert proc.returncode == 16, (line, proc.returncode)


def test_env_gate_accepts_production_shaped_env(tmp_path):
    """The production .env (28 lines: comments, blanks, plain KEY=value,
    no spaces around =, bare alnum/punct values incl. . : / -) must still pass — the
    tighter pattern must not kill the legitimate credential file. Also
    covers benign quoted values (literal double-quoted token without
    $/`/\\, and a space-carrying single-quoted value). Gate pass =>
    wrapper proceeds to the trading-day check => our stub says
    non-trading => exit 0."""
    proc, _, _ = _wrapper_harness(
        tmp_path,
        ['# comment line', '',
         'TUSHARE_TOKEN=abc123',
         'FUYAO_API_KEY=xyz-987',
         'INVESTODAY_API_KEY=q1w2e3',
         'MODEL_URL=https://api.example.com/v1',
         'LIST=a,b,c:d/e.f-g',
         'AKSHARE_TIMEOUT=30',
         'QUOTED_DQ="abc-123_XYZ"',
         "QUOTED_SQ='abc 123'",
         'export EXTRA_FLAG=1'])
    assert proc.returncode == 0, (proc.returncode, proc.stderr)


def test_env_gate_rejects_space_around_equals(tmp_path):
    """`FOO =bar` / `FOO= bar` are not assignments to bash (FOO becomes a
    command); the gate must keep rejecting them."""
    for i, line in enumerate(('FOO =bar', 'FOO= bar', 'FOO = bar')):
        proc, _, _ = _wrapper_harness(tmp_path / f"s{i}", [line])
        assert proc.returncode == 16, (line, proc.returncode)


def _wrapper_gap_guard_python() -> str:
    """Extract the embedded Python the wrapper pipes to $PYTHON for its
    post-run gap guard (the `<<'PY' ... PY` heredoc body) so the tests run
    the SAME code the timer runs — not a paraphrased copy."""
    script = WRAPPER.read_text("utf-8")
    # second <<'PY' heredoc — the first is the trading-day check
    marker = "<<'PY' 2>&1"
    body = script.split(marker, 2)[2]
    return body.split("\nPY\n", 1)[0]


def test_wrapper_gap_guard_uses_sw_eligible_denominator(tmp_path):
    """DAV-2058 🟡-2: an all-pre-SW-typed night (every row a typed
    pre-resolution status like BSE-excluded) is structurally gap but NOT a
    Tushare degradation — the guard must not exit 17. verify's
    sw_eligible denominator is the contract; the wrapper now mirrors it."""
    rows = [{"report_id": f"r{i}", "horizon": "t10",
             "outcome_status": "symbol_excluded_bse",
             "sw": {"membership_status": "gap"}}
            for i in range(9)]
    # stub reports worst=0 would be wrong — we want the REAL guard python
    # to run on these rows. Re-run the embedded snippet directly instead:
    # extract + exec via the real interpreter? Simpler: drive the real
    # guard logic through the stubbed wrapper is impossible (stub prints a
    # canned verdict). So this test executes the SAME python the wrapper
    # embeds, extracted from the script, against a real out dir.
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with open(out_dir / "labels_t10.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    proc = subprocess.run(
        [sys.executable, "-", str(out_dir)],
        input=_wrapper_gap_guard_python(), capture_output=True, text=True)
    assert proc.returncode == 0
    worst = int(proc.stdout.split()[0])
    assert worst == 0, proc.stdout  # eligible=0 -> no all-gap alarm


def test_wrapper_gap_guard_still_catches_real_all_gap(tmp_path):
    """Same guard, opposite case: all rows are sw-ELIGIBLE (evaluated) and
    every one is gap -> worst=2 (real degradation, exit 17)."""
    rows = [{"report_id": f"r{i}", "horizon": "t10",
             "outcome_status": "evaluated_ok",
             "sw": {"membership_status": "gap"}}
            for i in range(9)]
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with open(out_dir / "labels_t10.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    proc = subprocess.run(
        [sys.executable, "-", str(out_dir)],
        input=_wrapper_gap_guard_python(), capture_output=True, text=True)
    assert proc.returncode == 0
    assert int(proc.stdout.split()[0]) == 2
    assert "sw-eligible" in proc.stdout
