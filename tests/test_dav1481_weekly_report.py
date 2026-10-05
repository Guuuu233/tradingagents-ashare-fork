"""DAV-1481: unit + CLI tests for scripts/phase2/weekly_report.py.

Offline only — synthetic ledger JSONL, label files, benchmark closes, and a
fixture sqlite DB. No akshare, no production DB, no network.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "phase2" / "weekly_report.py"

spec = importlib.util.spec_from_file_location("weekly_report", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["weekly_report"] = mod
spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# helpers


def _cal(start="2026-07-01", days=500) -> list[date]:
    out = []
    d = date.fromisoformat(start)
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return out


CAL = _cal()


def _rec(rid: str, symbol: str, signal: str, direction: str = "看多",
         prob: float = 60.0, timing: str = "F0", pit: str = "VERIFIED",
         backfilled: bool = False, commit: str = "abc123def456"):
    return {
        "report_id": rid, "symbol": symbol, "signal_date": signal,
        "timing_class": timing, "input_pit_status": pit,
        "backfilled": backfilled,
        "horizons": {"medium": {"direction": direction,
                                "probability": prob},
                     "short": {"direction": "中性"}},
        "versions": {"generated_by_commit_sha": commit},
        "risk_status_top": "APPROVED",
    }


def _write_ledger(path: Path, records: list[dict]) -> Path:
    prev = "GENESIS"
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            rec_hash = mod._sha256(mod._canon(rec))
            chain = mod._sha256((prev + rec_hash).encode())
            fh.write(json.dumps({"record": rec, "prev_hash": prev,
                                 "chain_hash": chain},
                                ensure_ascii=False) + "\n")
            prev = chain
    return path


def _write_labels(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _write_bench(path: Path, closes: dict[str, float],
                 symbol: str = "000300.SH") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for d, c in sorted(closes.items()):
            fh.write(json.dumps({"symbol": symbol, "date": d, "close": c})
                     + "\n")
    return path


def _mk_db(path: Path, rows: list[tuple[str, str]]) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, "
                "risk_status TEXT, secret_text TEXT)")
    con.executemany("INSERT INTO reports VALUES (?,?,?)",
                    [(rid, rs, "BODY-TEXT-LEAK-CHK") for rid, rs in rows])
    con.commit()
    con.close()
    return path


def _run(monkeypatch, tmp_path, records, labels=None, bench=None,
         db_rows=None, as_of="2026-12-01"):
    """Invoke cmd_run with synthetic inputs; returns (exit_code, out_text)."""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    ledger = _write_ledger(tmp_path / "forward_ledger.jsonl", records)
    lab = _write_labels(tmp_path / "labels.jsonl", labels or [])
    sw = _write_labels(tmp_path / "sw.jsonl", [])
    bench_f = _write_bench(tmp_path / "bench.jsonl", bench or {})
    db = _mk_db(tmp_path / "t.db", db_rows or [])
    out = tmp_path / "report.md"

    monkeypatch.setattr(mod.dsl, "_load_trade_dates", lambda: CAL)

    args = type("A", (), {
        "out_dir": str(tmp_path / "out"), "as_of": as_of,
        "ledger": str(ledger), "db": str(db),
        "labels": str(lab), "sw_labels": str(sw),
        "benchmarks": str(bench_f), "benchmarks_dir": str(tmp_path),
        "benchmark_symbols": ["000300.SH"], "fetch_benchmarks": False,
        "min_cross_n": 3, "min_total_n": 40, "out": str(out),
    })()
    code = mod.cmd_run(args)
    return code, (out.read_text("utf-8") if out.exists() else "")


# ---------------------------------------------------------------------------
# spearman


def test_spearman_perfect_positive():
    assert mod._spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0


def test_spearman_perfect_negative():
    assert mod._spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0


def test_spearman_ties_get_average_ranks():
    # all probs tied -> variance 0 -> None (undefined), never a crash
    assert mod._spearman([5, 5, 5], [1, 2, 3]) is None


def test_spearman_too_small():
    assert mod._spearman([1], [2]) is None


# ---------------------------------------------------------------------------
# maturity + report


def test_maturity_uses_calendar_not_timing_class(tmp_path, monkeypatch):
    """F1 record whose T+40 has passed counts as matured even though its
    ledger timing_class is F1 — maturity is re-derived from the calendar."""
    recs = [_rec("r1", "600519.SH", "2026-07-28",
                 timing="F1", pit="UNVERIFIED")]
    code, text = _run(monkeypatch, tmp_path, recs, as_of="2026-12-01")
    assert code == 0
    assert "已到期（T+40 结果可知日 < as_of）样本：**1**" in text


def test_formal_requires_f0_verified_not_backfilled(tmp_path, monkeypatch):
    recs = [
        _rec("r1", "600519.SH", "2026-07-28", timing="F0", pit="VERIFIED"),
        _rec("r2", "600519.SH", "2026-07-28", timing="F0", pit="UNVERIFIED"),
        _rec("r3", "600519.SH", "2026-07-28", timing="H", pit="VERIFIED",
             backfilled=True),
        _rec("r4", "600519.SH", "2026-07-28", timing="F0", pit="VERIFIED",
             backfilled=True),
    ]
    code, text = _run(monkeypatch, tmp_path, recs, as_of="2026-12-01")
    assert code == 0
    assert "正式栏样本（到期 + 封存时 F0 + VERIFIED + 非回填）：**1**" in text


def test_banner_insufficient_by_default(tmp_path, monkeypatch):
    recs = [_rec(f"r{i}", "600519.SH", "2026-07-28") for i in range(5)]
    code, text = _run(monkeypatch, tmp_path, recs, as_of="2026-12-01")
    assert code == 0
    assert "样本不足，不下结论" in text


def test_daily_cross_section_ic_and_spread(tmp_path, monkeypatch):
    recs, labs = [], []
    for i in range(6):
        recs.append(_rec(f"r{i}", f"60000{i}.SH", "2026-07-28",
                         prob=50 + i))
        labs.append({"symbol": f"60000{i}.SH", "signal_date": "2026-07-28",
                     "return_pct": float(i)})
    code, text = _run(monkeypatch, tmp_path, recs, labels=labs,
                      as_of="2026-12-01")
    assert code == 0
    assert "1.0000" in text          # perfect monotone IC
    assert "2026-07-28" in text


def test_insufficient_day_marked(tmp_path, monkeypatch):
    recs, labs = [], []
    for i in range(2):  # below min_cross_n=3
        recs.append(_rec(f"r{i}", f"60000{i}.SH", "2026-07-28",
                         prob=50 + i))
        labs.append({"symbol": f"60000{i}.SH", "signal_date": "2026-07-28",
                     "return_pct": float(i)})
    code, text = _run(monkeypatch, tmp_path, recs, labels=labs,
                      as_of="2026-12-01")
    assert code == 0
    assert "insufficient" in text


def test_risk_gate_split_from_db(tmp_path, monkeypatch):
    recs = [_rec("r1", "600519.SH", "2026-07-28"),
            _rec("r2", "600520.SH", "2026-07-28")]
    db_rows = [("r1", "BLOCKED"), ("r2", "APPROVED")]
    code, text = _run(monkeypatch, tmp_path, recs, db_rows=db_rows)
    assert code == 0
    assert "BLOCKED" in text and "PASSED_OR_OTHER" in text
    assert "BODY-TEXT-LEAK-CHK" not in text


def test_determinism_byte_identical(tmp_path, monkeypatch):
    recs = [_rec(f"r{i}", f"60000{i}.SH", "2026-07-28", prob=40 + i)
            for i in range(8)]
    labs = [{"symbol": f"60000{i}.SH", "signal_date": "2026-07-28",
             "return_pct": float(i * 1.5)} for i in range(8)]
    code1, t1 = _run(monkeypatch, tmp_path / "a", recs, labs)
    code2, t2 = _run(monkeypatch, tmp_path / "b", recs, labs)
    assert code1 == code2 == 0
    assert t1 == t2
    assert "inputs_sha256=" in t1


def test_fail_close_missing_ledger(tmp_path, monkeypatch):
    args = type("A", (), {
        "out_dir": str(tmp_path), "as_of": "2026-12-01",
        "ledger": str(tmp_path / "nope.jsonl"),
        "db": str(tmp_path / "nope.db"),
        "labels": str(tmp_path / "x"), "sw_labels": str(tmp_path / "y"),
        "benchmarks": str(tmp_path / "z"), "benchmarks_dir": str(tmp_path),
        "benchmark_symbols": ["000300.SH"], "fetch_benchmarks": False,
        "min_cross_n": 3, "min_total_n": 40, "out": str(tmp_path / "o.md"),
    })()
    monkeypatch.setattr(mod.dsl, "_load_trade_dates", lambda: CAL)
    assert mod.cmd_run(args) == 4


def test_fail_close_corrupt_ledger_line(tmp_path):
    p = tmp_path / "forward_ledger.jsonl"
    p.write_text('{"record": {"a":1}}\n{"corrupt\n', "utf-8")
    with pytest.raises(ValueError):
        mod._load_ledger(p)


def test_week_anchor_uses_last_trading_day():
    # 2026-10-05 is a Monday; its week ends Friday 10-09 (trading day)
    assert mod._iso_week_anchor(date(2026, 10, 5), CAL) == "2026-10-09"
    # Sunday of the same week anchors identically
    assert mod._iso_week_anchor(date(2026, 10, 11), CAL) == "2026-10-09"


def test_benchmark_return_window():
    closes = {}
    cal = _cal("2026-07-01", 300)
    for i, d in enumerate(cal):
        closes[d.isoformat()] = 4000 + i
    sig = date.fromisoformat("2026-07-28")
    v = mod._benchmark_return(closes, cal, sig, 40)
    idx = cal.index(sig)
    expect = round((closes[cal[idx + 40].isoformat()]
                    - closes[cal[idx + 1].isoformat()])
                   / closes[cal[idx + 1].isoformat()] * 100, 4)
    assert v == pytest.approx(expect)


def test_version_queue_split(tmp_path, monkeypatch):
    recs = [
        _rec("r1", "600519.SH", "2026-07-28", commit="aaaa11111111"),
        _rec("r2", "600520.SH", "2026-07-28", commit="bbbb22222222"),
        _rec("r3", "600521.SH", "2026-07-28", commit=None),
    ]
    recs[2]["versions"] = {}
    code, text = _run(monkeypatch, tmp_path, recs)
    assert code == 0
    assert "commit:aaaa11111111" in text
    assert "commit:bbbb22222222" in text
    assert "unversioned" in text
