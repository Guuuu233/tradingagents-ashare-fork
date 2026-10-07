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


def _write_bench(path: Path, bars: dict[str, dict],
                 symbol: str = "000300.SH") -> Path:
    """bars: date -> {"open": x, "close": y} (either key may be absent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for d, bar in sorted(bars.items()):
            row = {"symbol": symbol, "date": d}
            row.update(bar)
            fh.write(json.dumps(row) + "\n")
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
         db_rows=None, as_of="2026-12-01", caliber="hs300",
         label_fields=None, no_bench_file=False):
    """Invoke cmd_run with synthetic inputs; returns (exit_code, out_text)."""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    ledger = _write_ledger(tmp_path / "forward_ledger.jsonl", records)
    lab = _write_labels(tmp_path / "labels.jsonl", labels or [])
    if no_bench_file:
        bench_f = tmp_path / "bench_absent.jsonl"  # never written
    else:
        bench_f = _write_bench(tmp_path / "bench.jsonl", bench or {})
    db = _mk_db(tmp_path / "t.db", db_rows or [])
    out = tmp_path / "report.md"

    monkeypatch.setattr(mod.dsl, "_load_trade_dates", lambda: CAL)

    args = type("A", (), {
        "out_dir": str(tmp_path / "out"), "as_of": as_of,
        "ledger": str(ledger), "db": str(db),
        "labels": str(lab),
        "relative_caliber": caliber,
        "label_fields": label_fields,
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
                     "hs300_excess_pct": float(i)})
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
                     "hs300_excess_pct": float(i)})
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
             "hs300_excess_pct": float(i * 1.5)} for i in range(8)]
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
        "labels": str(tmp_path / "x"),
        "relative_caliber": "hs300", "label_fields": None,
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
    """D-072 window: T+1 OPEN -> T+40 trading-day CLOSE (not close-close)."""
    bars = {}
    cal = _cal("2026-07-01", 300)
    for i, d in enumerate(cal):
        bars[d.isoformat()] = {"open": 3900 + i, "close": 4000 + i}
    sig = date.fromisoformat("2026-07-28")
    v = mod._benchmark_return(bars, cal, sig, 40)
    idx = cal.index(sig)
    expect = round((bars[cal[idx + 40].isoformat()]["close"]
                    - bars[cal[idx + 1].isoformat()]["open"])
                   / bars[cal[idx + 1].isoformat()]["open"] * 100, 4)
    assert v == pytest.approx(expect)


def test_benchmark_return_missing_open_is_missing_not_close():
    """入场日开盘价取不到记缺失，不得用收盘价替代。"""
    bars = {}
    cal = _cal("2026-07-01", 300)
    for i, d in enumerate(cal):
        bars[d.isoformat()] = {"open": 3900 + i, "close": 4000 + i}
    sig = date.fromisoformat("2026-07-28")
    idx = cal.index(sig)
    bars[cal[idx + 1].isoformat()] = {"close": 4001}  # open stripped
    assert mod._benchmark_return(bars, cal, sig, 40) is None


def test_benchmark_return_missing_exit_close_is_missing():
    bars = {}
    cal = _cal("2026-07-01", 300)
    for i, d in enumerate(cal):
        bars[d.isoformat()] = {"open": 3900 + i, "close": 4000 + i}
    sig = date.fromisoformat("2026-07-28")
    idx = cal.index(sig)
    del bars[cal[idx + 40].isoformat()]["close"]
    assert mod._benchmark_return(bars, cal, sig, 40) is None


# ---------------------------------------------------------------------------
# DAV-1500 rework: §3 labels-only, single-field labels, caliber param


def test_label_return_reads_exactly_one_field():
    lab = {"hs300_excess_pct": 1.23, "return_pct": 9.99}
    assert mod._label_return(lab, "hs300_excess_pct") == 1.23
    # the other field must never be consulted
    assert mod._label_return(lab, "sw_excess_pct") is None
    assert mod._label_return({}, "hs300_excess_pct") is None
    assert mod._label_return({"hs300_excess_pct": True},
                             "hs300_excess_pct") is None  # bool rejected
    assert mod._label_return({"hs300_excess_pct": "1.2"},
                             "hs300_excess_pct") is None  # str not numeric


def test_section3_excludes_unlabeled_and_reports_count(
        tmp_path, monkeypatch):
    """缺标签记录不进 §3 横截面，且缺标签计数在报告中列出。"""
    recs = []
    labs = []
    for i in range(4):
        recs.append(_rec(f"r{i}", f"60000{i}.SH", "2026-07-28",
                         prob=50 + i))
        labs.append({"symbol": f"60000{i}.SH",
                     "signal_date": "2026-07-28",
                     "hs300_excess_pct": float(i)})
    recs.append(_rec("r9", "600099.SH", "2026-07-28", prob=99))
    # bench bars exist — §3 must NOT fall back to them
    cal = _cal("2026-07-01", 300)
    bench = {d.isoformat(): {"open": 3900 + i, "close": 4000 + i}
             for i, d in enumerate(cal)}
    code, text = _run(monkeypatch, tmp_path, recs, labs, bench=bench)
    assert code == 0
    assert "缺标签/缺字段样本 **1** 条" in text
    # the unlabeled record must not be in the daily cross-section n
    assert "| 2026-07-28 | 4 | ok" in text


def test_section3_wrong_field_does_not_fall_back(tmp_path, monkeypatch):
    """标签行只有其他字段（return_pct）时记缺失，不回落。"""
    recs = [_rec(f"r{i}", f"60000{i}.SH", "2026-07-28", prob=50 + i)
            for i in range(4)]
    labs = [{"symbol": f"60000{i}.SH", "signal_date": "2026-07-28",
             "return_pct": float(i)} for i in range(4)]  # wrong field name
    code, text = _run(monkeypatch, tmp_path, recs, labs)
    assert code == 0
    assert "缺标签/缺字段样本 **4** 条" in text
    assert "无可用日序列" in text


def test_caliber_param_printed_in_header(tmp_path, monkeypatch):
    recs = [_rec("r1", "600519.SH", "2026-07-28")]
    labs = [{"symbol": "600519.SH", "signal_date": "2026-07-28",
             "sw_excess_pct": 2.5}]
    code, text = _run(monkeypatch, tmp_path, recs, labs, caliber="sw")
    assert code == 0
    assert "relative_caliber=sw" in text
    assert "label_field=sw_excess_pct" in text
    assert "--relative-caliber sw" in text
    assert "字段 sw_excess_pct" in text
    assert "| 成熟样本 | 1 | 1 | 2.5000" in text


def test_caliber_default_hs300(tmp_path, monkeypatch):
    recs = [_rec("r1", "600519.SH", "2026-07-28")]
    labs = [{"symbol": "600519.SH", "signal_date": "2026-07-28",
             "hs300_excess_pct": -1.0}]
    code, text = _run(monkeypatch, tmp_path, recs, labs)
    assert code == 0
    assert "relative_caliber=hs300" in text
    assert "label_field=hs300_excess_pct" in text
    assert "| 成熟样本 | 1 | 1 | -1.0000" in text


def test_label_fields_override(tmp_path, monkeypatch):
    recs = [_rec("r1", "600519.SH", "2026-07-28")]
    labs = [{"symbol": "600519.SH", "signal_date": "2026-07-28",
             "custom_rel": 7.5}]
    code, text = _run(monkeypatch, tmp_path, recs, labs,
                      label_fields=["hs300=custom_rel"])
    assert code == 0
    assert "label_field=custom_rel" in text
    assert "| 成熟样本 | 1 | 1 | 7.5000" in text


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


# ---------------------------------------------------------------------------
# DAV-1651: compat view (canonical result_data.storage.v1 records) +
# non-trading-day benchmark leg counted missing


def _canonical_rec(rid: str, symbol: str, signal: str,
                   direction: str = "看多", prob: float = 60.0,
                   timing: str = "F0", pit: str = "VERIFIED",
                   backfilled: bool = False, commit: str = "abc123def456"):
    """Ledger record whose horizons payload is stored in the *canonical*
    layout (result_data.storage.v1): ``horizons`` is a read-layer virtual
    key absent from the stored row; the medium judgement lives under
    ``medium_term``."""
    rec = _rec(rid, symbol, signal, direction, prob, timing, pit,
               backfilled, commit)
    medium_slice = rec["horizons"].pop("medium")
    short_slice = rec["horizons"].pop("short")
    rec.pop("horizons")
    rec["medium_term"] = dict(medium_slice)
    rec["short_term"] = dict(short_slice)
    rec["storage_schema_version"] = "result_data.storage.v1"
    rec["storage_compat"] = {
        "version": 1,
        "horizons_order": ["short", "medium"],
        "horizon_key_masks": {
            "short": list(short_slice.keys()),
            "medium": list(medium_slice.keys()),
        },
        "top_market_context": {"kind": "absent"},
        "top_key_order": [k for k in rec.keys()
                          if k not in ("storage_schema_version",
                                       "storage_compat")],
    }
    return rec


def test_canonical_record_resolves_medium_horizon(tmp_path, monkeypatch):
    """新格式行（canonical storage.v1）：record 无物理 horizons 键，medium
    判定由 medium_term 经兼容层重建——方向分布与到期样本不得丢。"""
    recs = [_canonical_rec("r1", "600519.SH", "2026-07-28",
                           direction="看多", prob=71.0)]
    labs = [{"symbol": "600519.SH", "signal_date": "2026-07-28",
             "hs300_excess_pct": 1.5}]
    code, text = _run(monkeypatch, tmp_path, recs, labs)
    assert code == 0
    # record is counted in the medium pool and direction bucketed 看多
    assert "中线账本记录总数：**1**" in text
    assert "已到期（T+40 结果可知日 < as_of）样本：**1**" in text
    assert "| 成熟样本 | 1 | 1 | 1.5000" in text
    # §1 direction table: 看多=1, not dropped into 无结论
    assert "| commit:abc123def45" in text
    for line in text.splitlines():
        if line.startswith("| commit:abc123def45"):
            cells = [c.strip() for c in line.split("|")]
            # (queue, total, 看多, 看空, 中性, 无结论)
            assert cells[3] == "1" and cells[6] == "0", line


def test_canonical_mixed_with_legacy_records(tmp_path, monkeypatch):
    """canonical 与 legacy 记录混排：两种格式都进同一个版本队列。"""
    recs = [
        _rec("r1", "600519.SH", "2026-07-28", direction="看空"),
        _canonical_rec("r2", "600520.SH", "2026-07-28", direction="看多"),
    ]
    code, text = _run(monkeypatch, tmp_path, recs)
    assert code == 0
    assert "中线账本记录总数：**2**" in text
    for line in text.splitlines():
        if line.startswith("| commit:abc123def45"):
            cells = [c.strip() for c in line.split("|")]
            assert cells[2] == "2"          # total
            assert cells[3] == "1"          # 看多 (canonical row)
            assert cells[4] == "1"          # 看空 (legacy row)
            assert cells[6] == "0"          # 无结论


def test_canonical_alias_conflict_fails_closed(tmp_path, monkeypatch):
    """canonical 行若仍带物理 horizons 且与权威档冲突：fail-close 保留存储
    形式（不重建视图）——该记录读到的仍是物理存储值，不得静默调和。"""
    rec = _canonical_rec("r1", "600519.SH", "2026-07-28", direction="看多")
    # Re-attach a conflicting physical alias: same key set, different value.
    rec["horizons"] = {"short": dict(rec["short_term"]),
                       "medium": dict(rec["medium_term"],
                                      direction="看空")}  # conflict
    code, text = _run(monkeypatch, tmp_path, [rec])
    assert code == 0
    assert "中线账本记录总数：**1**" in text
    # fail-close: the stored (physical) horizons.medium direction is kept —
    # 看空=1, 看多=0; nothing was reconciled to medium_term's 看多.
    for line in text.splitlines():
        if line.startswith("| commit:abc123def45"):
            cells = [c.strip() for c in line.split("|")]
            assert cells[3] == "0" and cells[4] == "1", line


def test_benchmark_return_non_trading_day_signal_is_missing():
    """DAV-1498/DAV-1651：signal_date 落在非交易日（周末/假日）时不得
    向下吸附到前一交易日——记缺失。"""
    cal = _cal("2026-07-01", 300)
    bars = {d.isoformat(): {"open": 3900 + i, "close": 4000 + i}
            for i, d in enumerate(cal)}
    # 2026-08-01 is a Saturday — not a trading day in CAL
    sig = date(2026, 8, 1)
    assert sig not in cal
    assert mod._benchmark_return(bars, cal, sig, 40) is None
    # sanity: a real trading day still resolves
    sig_td = date.fromisoformat("2026-07-28")
    assert sig_td in cal
    assert mod._benchmark_return(bars, cal, sig_td, 40) is not None


def test_benchmark_return_signal_before_calendar_is_missing():
    cal = _cal("2026-07-01", 300)
    bars = {d.isoformat(): {"open": 3900 + i, "close": 4000 + i}
            for i, d in enumerate(cal)}
    assert mod._benchmark_return(bars, cal, date(2020, 1, 1), 40) is None


# ---------------------------------------------------------------------------
# DAV-1504 non-blocking cleanups: --fetch-benchmarks help wording +
# 基准缺失 n 标注全量缺失情形


def test_fetch_benchmarks_help_mentions_ohlc_not_closes():
    """DAV-1504 项 1：help 文案必须写 OHLC/日线口径并注明行格式，
    不得再写 'fetch index closes'。"""
    import argparse
    src = SCRIPT.read_text("utf-8")
    # raw wording check: stale phrase removed
    assert "fetch index closes via akshare" not in src
    # argparse help surfaces the OHLC wording at runtime
    ap = argparse.ArgumentParser()
    # reconstructing the parser via main() is heavier; assert on the help
    # string content compiled into the parser instead
    assert "fetch index daily OHLC via akshare" in src
    assert "{symbol,date,open,close} JSONL rows" in src


def test_bench_missing_no_file_notes_total(tmp_path, monkeypatch):
    """DAV-1504 项 2a：基准文件完全缺失时，基准缺失列注明 n = 全部成熟
    样本数，读者不会误以为是部分样本窗口缺 open/close。"""
    recs = [_rec("r1", "600519.SH", "2026-07-28"),
            _rec("r2", "600520.SH", "2026-07-28")]
    code, text = _run(monkeypatch, tmp_path, recs, no_bench_file=True)
    assert code == 0
    assert "基准缺失 n（基准文件缺失/无可用行情：n = 全部成熟样本数）" \
        in text
    # the column still reports the cohort-wide count (fail-close, no
    # fallback price)
    assert "| 成熟样本 | 2 | 0 |" in text


def test_bench_missing_partial_windows_no_note(tmp_path, monkeypatch):
    """DAV-1504 项 2b：基准文件存在但部分样本窗口缺 open/close 时，列头
    不得带「全部成熟样本数」注记——区分部分缺失与全量缺失。"""
    cal = _cal("2026-07-01", 300)
    bars = {d.isoformat(): {"open": 3900 + i, "close": 4000 + i}
            for i, d in enumerate(cal)}
    # strip the entry-day open for the 2026-07-28 signal window only
    idx = cal.index(date.fromisoformat("2026-07-28"))
    del bars[cal[idx + 1].isoformat()]["open"]
    recs = [_rec("r1", "600519.SH", "2026-07-28")]
    code, text = _run(monkeypatch, tmp_path, recs, bench=bars)
    assert code == 0
    assert "基准缺失 n（" not in text
    # partial missing still counted in the column: bench leg shows
    # n=0 returns / missing=1
    assert "| 成熟样本 | 1 | 0 | — | 1 | 0 | — | 1 |" in text
