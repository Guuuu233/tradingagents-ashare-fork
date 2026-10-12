"""DAV-1883 — p_rel_t10 probability-ranking baseline tests.

Offline only: synthetic ledger + label fixtures exercise the read node,
the M1 funnel, single-version fail-close, the D-075 paired increment, and
the calendar fail-close. No DB, no network, no vendor data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "phase2"))
import expiry_label_pipeline as elp
import p_rel_t10_baseline_eval as m

FIXED = m.DEFAULT_USER_ID


def _rec(rid, sym, day, p, **kw):
    # sealed_at defaults to the signal day's own 08:00 UTC -> always before
    # the next-trading-day 09:30 Asia/Shanghai seal gate for these fixtures.
    kw.setdefault("sealed_at", f"{day} 08:00:00.000000")
    return m._ledger_rec(rid, sym, day, p, **kw)


def _write_ledger(path, recs):
    with open(path, "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in recs)


def _write_labels(path, labs):
    with open(path, "w") as fh:
        fh.writelines(json.dumps(l) + "\n" for l in labs)


class TestReadNode:
    """M1 签收 item 2 — p_rel_t10 read node & contract."""

    def test_valid_int(self):
        rec = _rec("r1", "000001.SZ", "2026-01-05", 63)
        assert m._p_rel_t10(rec) == (63, "valid")

    @pytest.mark.parametrize("p", [0, 100, "60", 60.5, None, True])
    def test_invalid(self, p):
        rec = _rec("r1", "000001.SZ", "2026-01-05", 60)
        rec["horizons"]["short"]["forecast"] = {"status": "malformed",
                                                "p_rel_t10": p}
        assert m._p_rel_t10(rec)[1] == "malformed"

    @pytest.mark.parametrize("p", [0, 100, -5, 60.5, "60", True])
    def test_valid_status_still_enforces_contract(self, p):
        # DAV-1890 🔴-1: a "valid" status with an out-of-contract value must
        # be re-typed malformed, never coerced into the formal queue.
        rec = _rec("r1", "000001.SZ", "2026-01-05", 60)
        rec["horizons"]["short"]["forecast"] = {"status": "valid",
                                                "p_rel_t10": p}
        assert m._p_rel_t10(rec)[1] == "malformed"
        assert m._p_rel_t10(rec)[0] is None

    def test_node_failed(self):
        rec = _rec("r1", "000001.SZ", "2026-01-05", None)
        rec["horizons"]["short"] = {
            "manager_verdict": {"forecast_input": {"node_ran": False}}}
        assert m._p_rel_t10(rec)[1] == "node_failed"

    def test_forecast_dict_wins_over_fallback(self):
        rec = _rec("r1", "000001.SZ", "2026-01-05", 40)
        rec["horizons"]["short"]["forecast"] = {"status": "valid", "p_rel_t10": 71}
        rec["horizons"]["short"]["manager_verdict"] = {
            "forecast_input": {"node_ran": True, "p_rel_t10": 40}}
        assert m._p_rel_t10(rec) == (71, "valid")


class TestVersionKey:
    def test_commit_contract(self):
        rec = _rec("r", "S", "2026-01-05", 50, commit="abcdef123456")
        assert m._record_version_key(rec) == "commit:abcdef123456:v1"

    def test_unversioned(self):
        rec = _rec("r", "S", "2026-01-05", 50)
        rec["versions"] = {}
        assert m._record_version_key(rec) == "unversioned:novc"

    def test_multi_version_fails_closed(self):
        recs = [_rec(f"a{i}", "S1", "2026-01-05", 50, commit="aaa") for i in range(3)]
        recs += [_rec(f"b{i}", "S2", "2026-01-05", 50, commit="bbb") for i in range(3)]
        df = pd.DataFrame([{**r, "prob": 50, "q": 0.5, "r": 0.01,
                            "signal_date": "20260105",
                            "version_key": m._record_version_key(r)} for r in recs])
        with pytest.raises(ValueError, match="version_key"):
            m.m1.select_version(df)


class TestFunnel:
    """M1 admission funnel on ledger-derived rows."""

    def test_funnel_counts(self, tmp_path):
        recs = [
            _rec("ok", "S1", "2026-01-05", 60),
            _rec("other", "S2", "2026-01-05", 60, user="x"),
            _rec("pf", "S3", "2026-01-05", 60, pit="FAILED"),
            _rec("f1", "S4", "2026-01-05", 60, tc="F1"),
            _rec("uv", "S5", "2026-01-05", 60, pit="UNVERIFIED"),
            _rec("np", "S6", "2026-01-05", None),   # missing prob
            _rec("nom", "S7", "2026-01-05", 60),    # no matured label
        ]
        labels = {("S1", "2026-01-05"): m._label("S1", "2026-01-05", 5.0),
                  ("S7", "2026-01-05"): m._label("S7", "2026-01-05", 5.0)}
        ledger = tmp_path / "l.jsonl"
        _write_ledger(ledger, recs)
        df = m.load_ledger_signals(ledger, labels, FIXED)
        formal, fun = m.m1.admit(df, user_id=FIXED)
        # S1 (valid p, VERIFIED F0, matured label) and S7 (valid p, VERIFIED
        # F0, matured label) both survive; everything else funnel-excluded.
        assert len(formal) == 2
        assert set(formal["symbol"]) == {"S1", "S7"}
        assert fun["excl_wrong_user"] == 1
        assert fun["excl_pit_failed"] == 1
        assert fun["excl_non_f0"] == 1
        assert fun["excl_unverified"] == 1
        assert fun["excl_bad_prob"] == 1

    def test_dedup_earliest_wins(self, tmp_path):
        recs = [
            _rec("late", "S1", "2026-01-05", 90, created="2026-01-05T10:00"),
            _rec("early", "S1", "2026-01-05", 10, created="2026-01-05T09:00"),
        ]
        labels = {("S1", "2026-01-05"): m._label("S1", "2026-01-05", 5.0)}
        ledger = tmp_path / "l.jsonl"
        _write_ledger(ledger, recs)
        df = m.load_ledger_signals(ledger, labels, FIXED)
        formal, fun = m.m1.admit(df, user_id=FIXED)
        assert len(formal) == 1
        assert formal.iloc[0]["run_id"] == "early"
        assert fun["excl_dup_run"] == 1

    @pytest.mark.parametrize("bad_p", [100, 60.5])
    def test_valid_status_out_of_contract_funnels_bad_prob(self, tmp_path, bad_p):
        # DAV-1890 必补两用例: forecast.status=="valid" but p out of contract
        # -> excl_bad_prob +1, never enters the formal queue.
        rec = _rec("bad", "S1", "2026-01-05", 60)
        rec["horizons"]["short"]["forecast"] = {"status": "valid",
                                                "p_rel_t10": bad_p}
        labels = {("S1", "2026-01-05"): m._label("S1", "2026-01-05", 5.0)}
        ledger = tmp_path / "l.jsonl"
        _write_ledger(ledger, [rec])
        df = m.load_ledger_signals(ledger, labels, FIXED)
        formal, fun = m.m1.admit(df, user_id=FIXED)
        assert len(formal) == 0
        assert fun["excl_bad_prob"] == 1


class TestSealGate:
    """总控追加封存时点门: formal 只收 sealed_at < T+1 09:30 (Asia/Shanghai)."""

    CAL = ["2026-01-05", "2026-01-06", "2026-01-07"]

    def _frame(self, recs):
        labels = {}
        ledger_rows = recs
        df = pd.DataFrame([
            {"signal_date": pd.to_datetime(r["signal_date"]).strftime("%Y%m%d"),
             "symbol": r["symbol"], "run_id": r["report_id"],
             "sealed_at": r.get("sealed_at"), "timing_class": r["timing_class"],
             "input_pit_status": r["input_pit_status"], "completed": True,
             "user_id": r["user_id"], "version_key": "v",
             "p_rel_t10": r["horizons"]["short"]["forecast"]["p_rel_t10"],
             "prob": r["horizons"]["short"]["forecast"]["p_rel_t10"],
             "r": 0.01}
            for r in ledger_rows])
        return df

    def test_sealed_before_entry_open_admitted(self):
        # sealed 2026-01-05 16:00 CN = before T+1(2026-01-06) 09:30 CN
        rec = _rec("a", "S1", "2026-01-05", 60, sealed_at="2026-01-05 08:00:00.000000")
        df, gate = m.apply_seal_gate(self._frame([rec]), self.CAL)
        assert gate["excl_seal_late"] == 0 and len(df) == 1

    def test_sealed_after_entry_open_diverted(self):
        # sealed 2026-01-06 10:00 CN (> T+1 09:30 CN) -> diagnostics only
        rec = _rec("b", "S1", "2026-01-05", 60, sealed_at="2026-01-06 02:00:00.000000")
        df, gate = m.apply_seal_gate(self._frame([rec]), self.CAL)
        assert gate["excl_seal_late"] == 1 and len(df) == 0
        assert gate["seal_late_ids"] == ["b"]

    def test_missing_sealed_at_diverted(self):
        # sealed_at 缺失 → 门口径不可判定 → 单列 excl_seal_unresolvable
        # (L-5 分流, 不混进"晚封"标签), 不进 formal。
        rec = _rec("c", "S1", "2026-01-05", 60, sealed_at=None)
        df, gate = m.apply_seal_gate(self._frame([rec]), self.CAL)
        assert len(df) == 0
        assert gate["excl_seal_late"] == 0
        assert gate["excl_seal_unresolvable"] == 1
        assert gate["seal_unresolvable_ids"] == ["c"]

    def test_utc_semantics_mandated_cases(self):
        # 总控口径: sealed_at 是无时区标记 UTC → 转 Asia/Shanghai → 比 T+1 09:30。
        cal = ["2026-10-08", "2026-10-09", "2026-10-10"]
        # case 1: signal 10-08, sealed 10-09 02:09 UTC (北京 10-09 10:09)
        #         -> after T+1(10-09) 09:30 CN -> 晚封存/诊断 only
        late = _rec("late", "S1", "2026-10-08", 60,
                    sealed_at="2026-10-09 02:09:00")
        df, gate = m.apply_seal_gate(self._frame([late]), cal)
        assert len(df) == 0 and gate["excl_seal_late"] == 1
        # case 2: sealed 10-08 11:45 UTC (北京 10-08 19:45)
        #         -> before T+1(10-09) 09:30 CN -> admitted
        ontime = _rec("ontime", "S1", "2026-10-08", 60,
                      sealed_at="2026-10-08 11:45:00")
        df, gate = m.apply_seal_gate(self._frame([ontime]), cal)
        assert len(df) == 1 and gate["excl_seal_late"] == 0

    def test_boundary_equal_excluded(self):
        # L-1 固化: sealed_at 恰为 T+1 09:30:00.000000 Asia/Shanghai 应排除
        # (口径为严格小于 sa < entry_open)。T+1(01-06) 09:30 CN = 01-06
        # 01:30:00.000000 UTC —— 恰在阈值的记录不进 formal。
        rec = _rec("edge", "S1", "2026-01-05", 60,
                   sealed_at="2026-01-06 01:30:00.000000")
        df, gate = m.apply_seal_gate(self._frame([rec]), self.CAL)
        assert len(df) == 0
        assert gate["excl_seal_late"] == 1
        assert gate["seal_late_ids"] == ["edge"]
        # 亚毫秒前一条仍进 formal。
        rec2 = _rec("edgeok", "S1", "2026-01-05", 60,
                    sealed_at="2026-01-06 01:29:59.999999")
        df2, gate2 = m.apply_seal_gate(self._frame([rec2]), self.CAL)
        assert len(df2) == 1 and gate2["excl_seal_late"] == 0

    def test_signal_not_trading_day_unresolvable(self):
        # L-2 固化: signal_date 非交易日 → 门口径不可解析 →
        # excl_seal_unresolvable, 不进 formal (此前只靠 selftest 隐含)。
        rec = _rec("we", "S1", "2026-01-04", 60,      # Sunday
                   sealed_at="2026-01-04 08:00:00.000000")
        df, gate = m.apply_seal_gate(self._frame([rec]), self.CAL)
        assert len(df) == 0
        assert gate["excl_seal_late"] == 0
        assert gate["excl_seal_unresolvable"] == 1
        assert gate["seal_unresolvable_ids"] == ["we"]

    def test_t1_unresolvable_at_calendar_end(self):
        # L-2 固化: signal 为日历末日 → T+1 不可解析 → unresolvable。
        rec = _rec("last", "S1", "2026-01-07", 60,
                   sealed_at="2026-01-07 08:00:00.000000")
        df, gate = m.apply_seal_gate(self._frame([rec]), self.CAL)
        assert len(df) == 0
        assert gate["excl_seal_late"] == 0
        assert gate["excl_seal_unresolvable"] == 1

    def test_gate_artifact_fields(self):
        # M-1: 门口径 + 门前/门后行数可自证 — gate dict 必须携带落盘字段。
        rec = _rec("g", "S1", "2026-01-05", 60)
        df, gate = m.apply_seal_gate(self._frame([rec]), self.CAL)
        assert gate["field"] == "sealed_at"
        assert "严格小于" in gate["threshold"]
        assert gate["input_rows"] == 1
        assert gate["seal_late_ids"] == [] and gate["seal_unresolvable_ids"] == []

    def test_utc_not_local(self):
        # naive local interpretation would misjudge: 02:09 treated as local
        # CN time would be *after* 09:30 only if same-day — but the correct
        # UTC read makes it clearly late (10:09 CN on T+1).
        cal = ["2026-10-08", "2026-10-09"]
        dt = m._sealed_at_utc("2026-10-09 02:09:00")
        assert dt is not None and dt.tzinfo is not None
        assert dt.hour == 2          # held as UTC, not 10:09 local
        assert dt.astimezone(m._CN_TZ).hour == 10

    def test_seal_gate_counts_in_run_funnel(self, tmp_path):
        days = ["2026-01-05", "2026-01-06", "2026-01-07"]
        recs = []
        labs = []
        # 12 on-time seals on 01-05, 12 late seals on 01-05
        for i in range(12):
            recs.append(_rec(f"on{i}", f"A{i:02d}", "2026-01-05", 30 + i,
                             sealed_at="2026-01-05 08:00:00.000000"))
            labs.append(m._label(f"A{i:02d}", "2026-01-05", 0.1 * i))
            recs.append(_rec(f"late{i}", f"B{i:02d}", "2026-01-05", 30 + i,
                             sealed_at="2026-01-06 03:00:00.000000"))
            labs.append(m._label(f"B{i:02d}", "2026-01-05", 0.1 * i))
        ledger = tmp_path / "l.jsonl"; _write_ledger(ledger, recs)
        lab = tmp_path / "lab.jsonl"; _write_labels(lab, labs)
        cal = tmp_path / "c.txt"; cal.write_text("\n".join(days))
        prows = [m._p1_row("M", "20260105", "801010.SI", y, exit_date="20260105")
                 for y in [1.0]*20 + [0.0]*20]
        chunks = tmp_path / "chunks"; m._write_p1_chunks(chunks, prows)
        out = m.run(ledger, lab, tmp_path / "o", calendar_path=cal,
                    base_chunks=chunks)
        assert out["verdict"]["funnel"]["excl_seal_late"] == 12
        assert out["verdict"]["funnel"]["formal_rows"] == 12

        # M-1: 封存门口径与被分流 run_id 落盘 — verdict.model 与 run.json
        # 顶层各一份同值 seal_gate 块, funnel 守恒在产物内闭合。
        sg = out["verdict"]["seal_gate"]
        assert sg["field"] == "sealed_at"
        assert sg["excl_seal_late"] == 12
        assert len(sg["seal_late_ids"]) == 12
        assert set(sg["seal_late_ids"]) == {f"late{i}" for i in range(12)}
        assert sg["excl_seal_unresolvable"] == 0
        assert sg["input_rows"] == 24 and sg["post_gate_rows"] == 12
        runj = json.loads((tmp_path / "o" / "run.json").read_text())
        assert runj["seal_gate"] == sg
        assert runj["ledger_rows"] == 24          # 门前全量
        assert runj["seal_gate"]["input_rows"] == 24
        vj = json.loads((tmp_path / "o" / "verdict.json").read_text())
        assert vj["model"]["seal_gate"] == sg    # 透传进 verdict.json


class TestIncrement:
    def test_flat_base_is_degenerate(self, tmp_path):
        # a (day,l1) cell with a 50/50 win mix -> p_m200 ~= 0.5 constant
        # across the day -> rank-degenerate base -> increment A.
        rows = [m._p1_row(f"M{k}", "20260105", "801010.SI", y,
                          exit_date="20260105")
                for k, y in enumerate([1.0] * 20 + [0.0] * 20)]
        chunks = tmp_path / "chunks"
        m._write_p1_chunks(chunks, rows)
        qmap = m.base_q_map(m.build_base_panel(chunks))
        assert qmap == {("2026-01-05", "801010.SI"): pytest.approx(0.5, abs=0.01)}


class TestAllMarketBase:
    """DAV-1883 rework (总控 10-12): base rate must come from the P1
    all-market partitions, NOT the P4 ledger's own label sample."""

    def _panel(self, tmp_path, n_per_cell=40):
        days = pd.bdate_range("2026-01-05", periods=5).strftime("%Y-%m-%d")
        rng = np.random.default_rng(3)
        rows = []
        for di, d in enumerate(days):
            for li, l1 in enumerate(("801010.SI", "801020.SI", "801030.SI")):
                wr = 0.30 + 0.10 * li + 0.02 * di
                for k in range(n_per_cell):
                    rows.append(m._p1_row(
                        f"M{li}_{k}", d.replace("-", ""), l1,
                        1.0 if rng.random() < wr else 0.0,
                        exit_date=d.replace("-", "")))
        chunks = tmp_path / "chunks"
        m._write_p1_chunks(chunks, rows)
        return chunks

    def test_base_uses_all_market_not_ledger(self, tmp_path):
        chunks = self._panel(tmp_path)
        panel = m.build_base_panel(chunks)
        assert not panel.empty
        # ~120 matured rows/cell from the all-market panel; a P4-ledger-only
        # build would see ~24/day total across 31 industries.
        grp = panel.groupby(["signal_date", "l1"]).size()
        assert grp.min() >= 30
        qmap = m.base_q_map(panel)
        assert qmap and all(0 < v < 1 for v in qmap.values())
        # mutating the ledger labels must NOT change the base rate — the
        # panel is sourced from the partitions only.
        crazy = {("x", "2026-01-05"): {"prediction": {"status": "evaluated"},
                                       "y_rel_prediction": True,
                                       "sw": {"index_code": "801010.SI"},
                                       "target_date": "2026-01-05"}}
        qmap2 = m.base_q_map(panel)   # ledger labels never enter here
        assert qmap == qmap2

    def test_no_base_chunks_marks_indeterminate(self, tmp_path):
        days = pd.bdate_range("2026-01-05", periods=3).strftime("%Y-%m-%d")
        recs, labs = [], []
        for di, d in enumerate(days):
            for i in range(12):
                recs.append(_rec(f"{di}_{i}", f"S{i:02d}", d, 30 + i,
                                 created=f"{d}T09:00"))
                labs.append(m._label(f"S{i:02d}", d, 0.2 * i))
        ledger = tmp_path / "l.jsonl"
        _write_ledger(ledger, recs)
        lab = tmp_path / "lab.jsonl"
        _write_labels(lab, labs)
        cal = tmp_path / "c.txt"
        cal.write_text("\n".join(days) + "\n")
        out = m.run(ledger, lab, tmp_path / "o", calendar_path=cal)
        assert out["base_source"]["kind"] == "none"
        assert out["increment_m200_W0"]["paired_days"] == 0


class TestFailClose:
    def test_missing_labels_file(self, tmp_path):
        with pytest.raises(elp.PipelineError):
            m.load_labels(tmp_path / "nope.jsonl")

    def test_missing_calendar_fails(self, tmp_path):
        ledger = tmp_path / "l.jsonl"
        _write_ledger(ledger, [_rec("a", "S1", "2026-01-05", 60)])
        labs = tmp_path / "lab.jsonl"
        _write_labels(labs, [m._label("S1", "2026-01-05", 1.0)])
        rc = m.main(["run", "--ledger", str(ledger), "--labels", str(labs),
                     "--calendar-file", str(tmp_path / "nocal.txt"),
                     "--out-dir", str(tmp_path / "o")])
        assert rc == 2


class TestEndToEnd:
    def test_run_smoke(self, tmp_path):
        rng = np.random.default_rng(1)
        days = pd.bdate_range("2026-02-02", periods=25).strftime("%Y-%m-%d")
        recs, labs = [], []
        l1s = ["801010.SI", "801020.SI", "801030.SI", "801040.SI"]
        for di, d in enumerate(days):
            drift = 0.03 * np.sin(di / 6.0)     # industry win-rate drift
            for i in range(15):
                l1 = l1s[i % 4]
                q = 10 + i * 5
                r = 0.4 * i - 3 + drift * 10 + float(rng.normal(0, 0.5))
                recs.append(_rec(f"{di}_{i}", f"S{i:02d}", d, q,
                                 created=f"{d}T09:00"))
                labs.append(m._label(f"S{i:02d}", d, r, l1=l1))
        ledger = tmp_path / "l.jsonl"
        _write_ledger(ledger, recs)
        lab = tmp_path / "lab.jsonl"
        _write_labels(lab, labs)
        # all-market T+10 panel as partitions (base must come from here)
        prows = []
        for di, d in enumerate(days):
            for li, l1 in enumerate(l1s):
                for k in range(30):
                    prows.append(m._p1_row(
                        f"M{li}{k}", d.replace("-", ""), l1,
                        1.0 if rng.random() < 0.4 + 0.1 * li else 0.0,
                        exit_date=d.replace("-", "")))
        chunks = tmp_path / "chunks"
        m._write_p1_chunks(chunks, prows)
        cal = tmp_path / "c.txt"
        # calendar extends past the last signal so its T+1 entry day exists
        cal_days = pd.bdate_range("2026-02-02", periods=40).strftime("%Y-%m-%d")
        cal.write_text("\n".join(cal_days) + "\n")
        out = m.run(ledger, lab, tmp_path / "o", calendar_path=cal,
                    base_chunks=chunks)
        assert out["verdict"]["valid_days"] == 25
        assert out["verdict"]["grade"] == "B"   # <60 days -> never C
        assert out["increment_m200_W0"]["paired_days"] == 25
        assert out["base_source"]["kind"] == "p1_t10_partitions"
        assert (tmp_path / "o" / "day_series.parquet").exists()
        assert (tmp_path / "o" / "verdict.json").exists()
        assert (tmp_path / "o" / "run.json").exists()
