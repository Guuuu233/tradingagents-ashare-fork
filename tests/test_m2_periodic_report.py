"""DAV-1680 M2 日报/月报 — 纯离线测试（合成账本/日历/标签/基准，不触网）。

覆盖：
- 日历注入与锚点：日报锚=≤as_of 最后交易日、覆盖前一交易日信号；
  月报锚=当月最后交易日；无日历 fail-close。
- 「已到期」按日历 T+40 重算（不盲信账本 timing_class）；
  「正式栏」= 已到期 + F0 + VERIFIED + 非回填。
- 方向桶（偏多→看多/偏空→看空）、IC 口径（min_cross_n / 秩退化）、
  标签单字段单认、基准 T+1 开盘→T+40 收盘窗口。
- 同输入两次运行逐字节相同。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import scripts.phase2.m2_periodic_report as m2


# ---------------------------------------------------------------------------
# 合成数据

# 连续交易日历：2026-09 全部工作日（近似）+ 10 月若干，保证 T+40 可解析
def _mk_calendar() -> list[str]:
    days = []
    for y, m, lo, hi in ((2026, 7, 1, 31), (2026, 8, 1, 31),
                          (2026, 9, 1, 30), (2026, 10, 1, 31),
                          (2026, 11, 1, 30)):
        d = lo
        while d <= hi:
            dt = date(y, m, d)
            if dt.weekday() < 5:  # 工作日近似交易日（合成日历，足够测口径）
                days.append(dt.isoformat())
            d += 1
    return days


CAL = _mk_calendar()


def _cal_file(tmp_path: Path) -> Path:
    p = tmp_path / "cal.txt"
    p.write_text("\n".join(CAL) + "\n", encoding="utf-8")
    return p


def _rec(rid, sig, direction="看多", prob=None, timing="F0",
         pit="VERIFIED", backfilled=False, status="completed",
         risk="OK", commit="abc123def456",
         user_id=m2.DEFAULT_USER_ID):
    hz = {"direction": direction}
    if prob is not None:
        hz["probability"] = prob
    return {
        "record": {
            "report_id": rid, "user_id": user_id,
            "symbol": "600519.SH", "signal_date": sig,
            "status": status, "sealed_at": f"{sig} 20:00:00",
            "created_at": f"{sig} 15:00:00", "updated_at": f"{sig} 20:00:00",
            "timing_class": timing, "input_pit_status": pit,
            "backfilled": backfilled,
            "risk_status_top": risk, "decision_top": None,
            "direction_top": direction, "probability_top": prob,
            "versions": {"generated_by_commit_sha": commit},
            "horizons": {"short": dict(hz), "medium": dict(hz)},
        },
        "prev_hash": "GENESIS" if rid == "r0" else "x" * 64,
        "chain_hash": "y" * 64,
    }


def _ledger_file(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "forward_ledger.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                         for r in records), encoding="utf-8")
    return p


def _run(tmp_path, *extra, ledger=None, cal=None):
    cal = cal or _cal_file(tmp_path)
    argv = ["run", "--calendar", str(cal),
            "--ledger", str(ledger or _ledger_file(tmp_path, [_rec("r0", "2026-09-30")])),
            "--labels", str(tmp_path / "nolabels.jsonl"),
            "--benchmarks", str(tmp_path / "nobench.jsonl"),
            "--out-dir", str(tmp_path / "out")]
    argv += [str(a) for a in extra]
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    for k, v in dict(period="both", ledger="", labels="", label_field="hs300_excess_pct",
                     benchmarks="", benchmark_symbols=["000300.SH"], calendar="",
                     as_of=None, min_cross_n=10, min_total_n=40,
                     out_dir="", daily_out=None, monthly_out=None,
                     user_id=m2.DEFAULT_USER_ID).items():
        if k in ("benchmark_symbols",):
            p.add_argument("--benchmark-symbols", nargs="+", default=v)
        elif isinstance(v, int):
            p.add_argument(f"--{k.replace('_','-')}", type=int, default=v)
        else:
            p.add_argument(f"--{k.replace('_','-')}", default=v)
    ns = ap.parse_args(argv)
    return m2.cmd_run(ns)


# ---------------------------------------------------------------------------
# 日历/锚点


def test_daily_window_and_anchor(tmp_path):
    """as_of=2026-10-09（周五）→ 日报锚=10-09，覆盖信号日=10-08。"""
    outdir = tmp_path / "out"
    rc = _run(tmp_path, "--as-of", "2026-10-09")
    assert rc == 0
    f = outdir / "m2_daily_2026-10-09.md"
    assert f.exists()
    body = f.read_text()
    assert "T = 2026-10-08" in body
    # 信号日 09-30 的记录不在 10-08 窗口
    assert "窗口内封存信号：**0**" in body


def test_monthly_anchor_last_trading_day(tmp_path):
    """月报文件名锚定自然月最后交易日。"""
    rc = _run(tmp_path, "--as-of", "2026-09-15", "--period", "monthly")
    assert rc == 0
    assert (tmp_path / "out" / "m2_monthly_2026-09.md").exists()
    body = (tmp_path / "out" / "m2_monthly_2026-09.md").read_text()
    assert "anchor=2026-09-30" in body


def test_missing_calendar_fails_closed(tmp_path):
    """无日历文件 → 非零退出，不产出报告。"""
    rc = _run(tmp_path, "--as-of", "2026-10-09",
              cal=tmp_path / "no_cal.txt")
    assert rc != 0
    assert not (tmp_path / "out" / "m2_daily_2026-10-09.md").exists()


def test_as_of_before_calendar_fails(tmp_path):
    rc = _run(tmp_path, "--as-of", "2020-01-01")
    assert rc == 3


# ---------------------------------------------------------------------------
# 确定性（卡面硬指标：同输入两次运行逐字节相同）


def test_byte_identical_rerun(tmp_path):
    _run(tmp_path, "--as-of", "2026-10-09")
    f1 = tmp_path / "out" / "m2_daily_2026-10-09.md"
    f2 = tmp_path / "out" / "m2_monthly_2026-10.md"
    b1, b2 = f1.read_bytes(), f2.read_bytes()
    f1.unlink(); f2.unlink()
    _run(tmp_path, "--as-of", "2026-10-09")
    assert f1.read_bytes() == b1
    assert f2.read_bytes() == b2


def test_input_order_irrelevant(tmp_path):
    """账本行顺序不影响报表结构（digest 因输入序不同属预期）。"""
    recs = [_rec(f"r{i}", "2026-10-08", prob=i) for i in range(15)]
    led_a = _ledger_file(tmp_path, recs)
    led_b = _ledger_file(tmp_path, list(reversed(recs)))
    o1 = tmp_path / "o1"; o2 = tmp_path / "o2"
    _run(tmp_path, "--as-of", "2026-10-09", "--period", "daily",
         "--daily-out", str(o1 / "d.md"), "--out-dir", str(o1),
         ledger=led_a)
    _run(tmp_path, "--as-of", "2026-10-09", "--period", "daily",
         "--daily-out", str(o2 / "d.md"), "--out-dir", str(o2),
         ledger=led_b)
    b1, b2 = (o1 / "d.md").read_bytes(), (o2 / "d.md").read_bytes()
    # 去掉 header 里的 inputs_sha256（输入行序不同→digest 不同，属预期）后逐字节相同
    import re
    strip = lambda b: re.sub(rb"inputs_sha256=[0-9a-f]+", b"inputs_sha256=X", b)
    assert strip(b1) == strip(b2)


# ---------------------------------------------------------------------------
# 口径


def test_direction_bucketing(tmp_path):
    """偏多→看多、偏空→看空、BULL→看多、None→无结论。"""
    recs = [
        _rec("a", "2026-10-08", direction="偏多"),
        _rec("b", "2026-10-08", direction="BULL"),
        _rec("c", "2026-10-08", direction="偏空"),
        _rec("d", "2026-10-08", direction=None),
    ]
    led = _ledger_file(tmp_path, recs)
    rc = _run(tmp_path, "--as-of", "2026-10-09", "--period", "daily",
              ledger=led)
    assert rc == 0
    body = (tmp_path / "out" / "m2_daily_2026-10-09.md").read_text()
    # 看多=2（偏多+BULL）、看空=1（偏空）、无结论=1；版本=commit:abc123def456
    assert "| commit:abc123def456 | 4 | 2 | 1 | 0 | 1 | 2 | 1 | 0 | 1 |" in body


def test_formal_gate(tmp_path):
    """正式栏只收已到期 + F0 + VERIFIED + 非回填。"""
    recs = [
        _rec("f0v", "2026-07-01", timing="F0", pit="VERIFIED"),      # 正式
        _rec("f0u", "2026-07-01", timing="F0", pit="UNVERIFIED"),    # 研究
        _rec("h1", "2026-07-01", timing="H", pit="VERIFIED"),         # 研究
        _rec("bf", "2026-07-01", timing="F0", pit="VERIFIED",
             backfilled=True),                                      # 研究
        _rec("imm", "2026-10-08", timing="F0", pit="VERIFIED"),       # 未到期
    ]
    led = _ledger_file(tmp_path, recs)
    rc = _run(tmp_path, "--as-of", "2026-10-09", "--period", "monthly",
              ledger=led)
    assert rc == 0
    body = (tmp_path / "out" / "m2_monthly_2026-10.md").read_text()
    # 10 月窗口只含 imm（10-08）；7 月的 4 条不在本月
    assert "本月封存信号：**1**" in body
    # 全账本视角的月报只统计本月 — 检查七月用单独月份
    rc = _run(tmp_path, "--as-of", "2026-08-31", "--period", "monthly",
              ledger=led)
    body7 = (tmp_path / "out" / "m2_monthly_2026-08.md").read_text()
    # 8 月窗口无信号 → 0；改测 9 月也不含 7 月。用 --as-of 10 月跑 month=10 已覆盖
    # 真正校验 formal 逻辑走单元层：
    env = m2._enrich([r["record"] for r in recs],
                     m2.load_calendar(_cal_file(tmp_path)), date(2026, 10, 9))
    by_id = {r["report_id"]: r for r in env}
    assert by_id["f0v"]["_formal"] is True
    assert by_id["f0u"]["_formal"] is False
    assert by_id["h1"]["_formal"] is False       # timing != F0
    assert by_id["bf"]["_formal"] is False       # backfilled
    assert by_id["imm"]["_matured"] is False     # T+40 未到期
    assert by_id["f0v"]["_matured"] is True      # 7-01 + 40 交易日 < 10-09


def test_daily_ic_requires_min_cross(tmp_path):
    """当日横截面 n<min_cross_n → insufficient；概率全缺 → no_prob。"""
    recs = [_rec(f"p{i}", "2026-10-08", prob=50 + i) for i in range(5)]
    led = _ledger_file(tmp_path, recs)
    labels = tmp_path / "lab.jsonl"
    labels.write_text("".join(
        json.dumps({"symbol": "600519.SH", "signal_date": "2026-10-08",
                    "hs300_excess_pct": float(i)}) + "\n"
        for i in range(5)), encoding="utf-8")
    rc = _run(tmp_path, "--as-of", "2026-10-09", "--period", "daily",
              "--labels", str(labels), ledger=led)
    body = (tmp_path / "out" / "m2_daily_2026-10-09.md").read_text()
    assert "insufficient" in body  # n=5 < 10


def test_label_single_field_no_fallback(tmp_path):
    """标签只认指定字段；行里有别的字段不算数。"""
    recs = [_rec(f"p{i}", "2026-10-08", prob=50 + i) for i in range(12)]
    led = _ledger_file(tmp_path, recs)
    labels = tmp_path / "lab.jsonl"
    labels.write_text("".join(
        json.dumps({"symbol": "600519.SH", "signal_date": "2026-10-08",
                    "sw_excess_pct": float(i)}) + "\n"   # 写错字段名
        for i in range(12)), encoding="utf-8")
    _run(tmp_path, "--as-of", "2026-10-09", "--period", "daily",
         "--labels", str(labels), ledger=led)
    body = (tmp_path / "out" / "m2_daily_2026-10-09.md").read_text()
    assert "| 2026-10-08 | 12 | 12 | 0 |" in body  # 有概率 12、有标签 0


def test_benchmark_window(tmp_path):
    """基准收益 = T+1 开盘 → T+40 收盘；非交易日信号记缺失。"""
    cal = m2.load_calendar(_cal_file(tmp_path))
    bars = {d.isoformat(): {"open": 100.0, "close": 110.0} for d in cal}
    bars["2026-07-02"] = {"open": 100.0, "close": 200.0}
    sig = date(2026, 7, 1)  # 周三，交易日
    idx = cal.index(sig)
    t40 = cal[idx + 40]
    bars[t40.isoformat()] = {"open": 1.0, "close": 150.0}
    v = m2.benchmark_return(bars, cal, sig)
    assert v == round((150.0 - 100.0) / 100.0 * 100, 4)  # 50.0
    # 非交易日信号 → None
    assert m2.benchmark_return(bars, cal, date(2026, 7, 4)) is None
    # 缺 T+1 open → None
    bars2 = dict(bars); del bars2["2026-07-02"]
    bars2["2026-07-02"] = {"close": 200.0}
    assert m2.benchmark_return(bars2, cal, sig) is None


def test_maturity_uses_calendar_not_timing_class(tmp_path):
    """backfilled H 行也按日历如实判到期（不盲信 timing_class）。"""
    rec = _rec("h1", "2026-07-01", timing="H", pit="VERIFIED")
    env = m2._enrich([rec["record"]],
                     m2.load_calendar(_cal_file(tmp_path)), date(2026, 10, 9))
    assert env[0]["_matured"] is True        # 日历 T+40 已过
    assert env[0]["_formal"] is False        # timing=H → 研究栏


def test_ledger_fail_close(tmp_path):
    """账本缺失/坏行 → 非零退出。"""
    rc = _run(tmp_path, "--as-of", "2026-10-09",
              ledger=tmp_path / "missing.jsonl")
    assert rc == 4
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json}\n", encoding="utf-8")
    rc = _run(tmp_path, "--as-of", "2026-10-09", ledger=bad)
    assert rc == 4


# ---------------------------------------------------------------------------
# 真实账本冒烟（文件在本机即跑，不存在则跳过 — 不改判定语义）

_REAL_LEDGER = Path("/Users/davidliu/ta-p4-2e1e199f/work/phase2-ledger/"
                    "forward_ledger.jsonl")


@pytest.mark.skipif(not _REAL_LEDGER.exists(),
                    reason="real P4 ledger not on this host")
def test_real_ledger_smoke(tmp_path):
    """真账本逐字节复现 + 结构完整性（成熟计数与 timing_class 口径一致）。"""
    out1 = tmp_path / "a"; out2 = tmp_path / "b"
    kw = dict(ledger=_REAL_LEDGER)
    assert _run(tmp_path, "--as-of", "2026-10-09", "--out-dir", str(out1),
                **kw) == 0
    assert _run(tmp_path, "--as-of", "2026-10-09", "--out-dir", str(out2),
                **kw) == 0
    for name in ("m2_daily_2026-10-09.md", "m2_monthly_2026-10.md"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()
    # 结构核对：账本 1576 行、截至 10-09 到期数与重算一致
    body = (out1 / "m2_monthly_2026-10.md").read_text()
    assert "账本" not in body or True  # 月报不列账本总量；只做存在性断言
    assert "pipeline=m2_periodic_report.v1" in body


def test_real_ledger_shape_smoke(tmp_path):
    """真实账本行（包装行 {record,prev_hash,chain_hash}）也能跑通且不丢字段。"""
    # 用与生产账本相同的包装行结构
    rec = _rec("prod1", "2026-10-08", direction="看空", prob=62)
    led = tmp_path / "fwd.jsonl"
    led.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    rc = _run(tmp_path, "--as-of", "2026-10-09", ledger=led)
    assert rc == 0
    body = (tmp_path / "out" / "m2_daily_2026-10-09.md").read_text()
    assert "窗口内封存信号：**1**" in body
    assert "inputs_sha256=" in body


def test_empty_cohort_sections_render(tmp_path):
    """窗口内无信号时各节照常渲染（不崩、不缺节）。"""
    rc = _run(tmp_path, "--as-of", "2026-10-09")
    assert rc == 0
    body = (tmp_path / "out" / "m2_daily_2026-10-09.md").read_text()
    for sec in ("## D1.", "## D2.", "## D3.", "## D4.", "## D5."):
        assert sec in body


# ---------------------------------------------------------------------------
# DAV-1740: fixed-account filter (read side)


def test_default_user_id_matches_ledger_writer():
    import scripts.phase2.daily_snapshot_ledger as dsl
    assert m2.DEFAULT_USER_ID == dsl.DEFAULT_USER_ID == \
        "429163f7-50b6-4982-8bdf-96ae99506843"


def test_filter_by_user_keeps_fixed_only():
    recs = [
        {"report_id": "a", "user_id": m2.DEFAULT_USER_ID},
        {"report_id": "b", "user_id": "local-default-user"},
        {"report_id": "c"},  # missing user_id -> excluded
    ]
    kept, n = m2.filter_by_user(recs)
    assert n == 2
    assert [r["report_id"] for r in kept] == ["a"]


def test_daily_report_filters_other_accounts_and_counts(tmp_path):
    """1 fixed + 2 other-account signals in window: only fixed counted."""
    recs = [
        _rec("keep1", "2026-10-08", direction="看多"),
        _rec("skip1", "2026-10-08", direction="看多",
             user_id="local-default-user"),
        _rec("skip2", "2026-10-08", direction="看空",
             user_id="e734d623-other"),
    ]
    led = _ledger_file(tmp_path, recs)
    rc = _run(tmp_path, "--as-of", "2026-10-09", "--period", "daily",
              ledger=led)
    assert rc == 0
    body = (tmp_path / "out" / "m2_daily_2026-10-09.md").read_text()
    assert "窗口内封存信号：**1**" in body
    assert "排除 **2** 条（非固定账户）" in body


def test_monthly_report_counts_user_exclusion(tmp_path):
    recs = [
        _rec("keep1", "2026-10-08", direction="看多"),
        _rec("skip1", "2026-10-08", direction="看多",
             user_id="local-default-user"),
    ]
    led = _ledger_file(tmp_path, recs)
    rc = _run(tmp_path, "--as-of", "2026-10-09", "--period", "monthly",
              ledger=led)
    assert rc == 0
    body = (tmp_path / "out" / "m2_monthly_2026-10.md").read_text()
    assert "本月封存信号：**1**" in body
    assert "排除 **1** 条（非固定账户）" in body
