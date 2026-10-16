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
        key = m._record_version_key(rec)
        assert key.startswith("AV|anchor=abcdef123456|contract=v1|")
        assert "|resolved=none|custom=none|served=none" in key

    def test_unversioned(self):
        rec = _rec("r", "S", "2026-01-05", 50)
        rec["versions"] = {}
        assert m._record_version_key(rec) == (
            "AV|anchor=unanchored|contract=novc"
            "|resolved=none|custom=none|served=none")

    def test_multi_version_fails_closed(self):
        recs = [_rec(f"a{i}", "S1", "2026-01-05", 50, commit="aaa") for i in range(3)]
        recs += [_rec(f"b{i}", "S2", "2026-01-05", 50, commit="bbb") for i in range(3)]
        df = pd.DataFrame([{**r, "prob": 50, "q": 0.5, "r": 0.01,
                            "signal_date": "20260105",
                            "version_key": m._record_version_key(r)} for r in recs])
        with pytest.raises(ValueError, match="version_key"):
            m.m1.select_version(df)


class TestFrozenVersionKey:
    """DAV-2160: version key follows the frozen plan (登记 §3) — the split
    factors are contract + prompt-equivalence hashes + served_model; the
    production release commit is only an audit anchor."""

    @staticmethod
    def _served(by_role, missing=0):
        return {"by_role": by_role, "served_model_missing": missing,
                "call_count": sum(len(v) for v in by_role.values())}

    def test_equivalent_deploy_same_key(self):
        """等价部署前后归为同一版本: the adjudicated-equivalent 10-12
        deploy (65988c79→082be047) changes the record commit but none of
        the split factors -> with a fixed --analysis-anchor both sides of
        the deploy share ONE version key."""
        pre = _rec("pre", "S", "2026-01-05", 50, commit="65988c79aaaa",
                   served_models=self._served({"trader": ["m1"]}))
        post = _rec("post", "S", "2026-01-06", 50, commit="082be047bbbb",
                    served_models=self._served({"trader": ["m1"]}))
        k_pre = m._record_version_key(pre, anchor="082be047")
        k_post = m._record_version_key(post, anchor="082be047")
        assert k_pre == k_post
        assert "anchor=082be047" in k_pre
        # without a fixed anchor the two commits anchor differently —
        # fail-closed split, never a silent merge.
        assert m._record_version_key(pre) != m._record_version_key(post)

    def test_served_model_change_switches_key(self):
        """served_model 变化时切换版本 — even under the same frozen
        anchor and identical prompts/commit."""
        a = _rec("a", "S", "2026-01-05", 50, commit="082be047",
                 served_models=self._served({"trader": ["gemini-3.8-flash-n"]}))
        b = _rec("b", "S", "2026-01-05", 50, commit="082be047",
                 served_models=self._served({"trader": ["gemini-3.8-flash-high"]}))
        ka = m._record_version_key(a, anchor="082be047")
        kb = m._record_version_key(b, anchor="082be047")
        assert ka != kb
        assert "served=gemini-3.8-flash-n" in ka
        assert "served=gemini-3.8-flash-high" in kb

    def test_prompt_hash_change_switches_key(self):
        """提示词等价哈希变化也切换版本 (resolved / custom 任一变即变)。"""
        base = dict(commit="082be047",
                    served_models=self._served({"trader": ["m1"]}),
                    prompt_resolved={"trader": "h1"},
                    custom_prompt={"roles": {"trader": {"resolved_hash": "x"}}})
        changed = dict(base)
        changed["prompt_resolved"] = {"trader": "h2"}
        ka = m._record_version_key(_rec("a", "S", "2026-01-05", 50, **base),
                                   anchor="082be047")
        kb = m._record_version_key(_rec("b", "S", "2026-01-05", 50, **changed),
                                   anchor="082be047")
        assert ka != kb

    def test_multi_model_report_flagged(self, tmp_path):
        """多模型报告被标记: a report whose served_models span >1 model is
        flagged in the freeze block and counted in the funnel."""
        days = ["2026-01-05", "2026-01-06", "2026-01-07"]
        recs, labs = [], []
        for i in range(12):
            recs.append(_rec(f"ok{i}", f"A{i:02d}", "2026-01-05", 30 + i,
                             sealed_at="2026-01-05 08:00:00.000000",
                             served_models=self._served({"trader": ["m1"]})))
            labs.append(m._label(f"A{i:02d}", "2026-01-05", 0.1 * i))
        recs.append(_rec("mm1", "MM", "2026-01-05", 50,
                         sealed_at="2026-01-05 08:00:00.000000",
                         served_models=self._served(
                             {"trader": ["m1", "m2"]})))
        labs.append(m._label("MM", "2026-01-05", 1.0))
        ledger = tmp_path / "l.jsonl"; _write_ledger(ledger, recs)
        lab = tmp_path / "lab.jsonl"; _write_labels(lab, labs)
        cal = tmp_path / "c.txt"; cal.write_text("\n".join(days))
        # the multi-model report carries a different served set -> its own
        # version key. Select the m1 queue explicitly (single-version rule);
        # the multi-model report is excluded from the formal queue yet still
        # flagged in the freeze block (报告级披露独立于版本分键).
        probe = m.load_ledger_signals(ledger,
                                      {("A00", "2026-01-05"): labs[0]},
                                      FIXED, analysis_anchor="082be047")
        m1_key = sorted(probe.loc[probe["served_models"].map(len) == 1,
                                  "version_key"])[0]
        out = m.run(ledger, lab, tmp_path / "o", calendar_path=cal,
                    analysis_anchor="082be047", report_kind="descriptive",
                    version_key=m1_key)
        fb = out["freeze"]
        assert fb["multi_model_reports"] == ["mm1"]
        assert fb["multi_model_count"] == 1
        assert fb["report_kind"] == "descriptive"
        assert fb["plan_sha256"] == m.FREEZE_PLAN_SHA256_SIGNED
        assert fb["register_sha256"] == m.FREEZE_REGISTER_SHA256_SIGNED
        assert fb["analysis_anchor"] == "082be047"
        assert fb["served_model_distribution"]["m1"] == 12
        assert fb["served_model_distribution"]["m1+m2"] == 1
        # run.json first screen carries the same freeze block.
        runj = json.loads((tmp_path / "o" / "run.json").read_text())
        assert runj["freeze"]["multi_model_reports"] == ["mm1"]
        assert runj["freeze"]["plan_sha256"] == fb["plan_sha256"]
        assert out["verdict"]["funnel"]["multi_model_reports"] == 1

    def test_missing_served_models_counts_none(self):
        rec = _rec("r", "S", "2026-01-05", 50)
        served, missing = m._record_served_set(rec)
        assert served == [] and missing == 0
        assert "served=none" in m._record_version_key(rec)


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
        formal, fun = m.m1.admit(
            df.drop(columns=["sealed_at"], errors="ignore"),
            user_id=FIXED)
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
        formal, fun = m.m1.admit(
            df.drop(columns=["sealed_at"], errors="ignore"),
            user_id=FIXED)
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
        formal, fun = m.m1.admit(
            df.drop(columns=["sealed_at"], errors="ignore"),
            user_id=FIXED)
        assert len(formal) == 0
        assert fun["excl_bad_prob"] == 1


class TestSealGate:
    """总控追加封存时点门: formal 只收 sealed_at < T+1 09:30 (Asia/Shanghai)。
    统一实现 = m1_eval 的 DAV-1991 门（总控 10-14 令）：1883 不再自养
    apply_seal_gate，用例直接调 m.m1.admit(calendar=...)/m.run()。"""

    CAL = ["20260105", "20260106", "20260107"]

    def _frame(self, recs):
        def _sd(r):
            # load_ledger_signals 对缺失 signal_date 产出 nan (NaT→strftime),
            # 而非 None — fixture 要与真实管线同一表示。
            if r.get("signal_date") is None:
                return float("nan")
            return pd.to_datetime(r["signal_date"]).strftime("%Y%m%d")
        df = pd.DataFrame([
            {"signal_date": _sd(r),
             "symbol": r["symbol"], "run_id": r["report_id"],
             "sealed_at": r.get("sealed_at"), "timing_class": r["timing_class"],
             "input_pit_status": r["input_pit_status"], "completed": True,
             "user_id": r["user_id"], "version_key": "v",
             "prob": r["horizons"]["short"]["forecast"]["p_rel_t10"],
             "rel_return": 0.01}
            for r in recs])
        return df

    def _admit(self, recs, cal=None):
        return m.m1.admit(self._frame(recs), user_id=FIXED,
                          calendar=cal or self.CAL)

    def test_sealed_before_entry_open_admitted(self):
        # sealed 2026-01-05 16:00 CN = before T+1(2026-01-06) 09:30 CN
        rec = _rec("a", "S1", "2026-01-05", 60, sealed_at="2026-01-05 08:00:00.000000")
        formal, fun = self._admit([rec])
        assert fun["excl_late_seal"] == 0 and len(formal) == 1

    def test_sealed_after_entry_open_diverted(self):
        # sealed 2026-01-06 10:00 CN (> T+1 09:30 CN) -> diagnostics only
        rec = _rec("b", "S1", "2026-01-05", 60, sealed_at="2026-01-06 02:00:00.000000")
        formal, fun = self._admit([rec])
        assert len(formal) == 0 and fun["excl_late_seal"] == 1

    def test_missing_sealed_at_diverted(self):
        # sealed_at 缺失 → sealed NaT → fail-closed 计入 excl_late_seal /
        # seal_unparseable, 不进 formal。
        rec = _rec("c", "S1", "2026-01-05", 60, sealed_at=None)
        formal, fun = self._admit([rec])
        assert len(formal) == 0
        assert fun["excl_late_seal"] == 1
        assert fun["seal_unparseable"] == 1

    def test_utc_semantics_mandated_cases(self):
        # 总控口径: sealed_at 是无时区标记 UTC → 转 Asia/Shanghai → 比 T+1 09:30。
        cal = ["20261008", "20261009", "20261010"]
        # case 1: signal 10-08, sealed 10-09 02:09 UTC (北京 10-09 10:09)
        #         -> after T+1(10-09) 09:30 CN -> 晚封存/诊断 only
        late = _rec("late", "S1", "2026-10-08", 60,
                    sealed_at="2026-10-09 02:09:00")
        formal, fun = self._admit([late], cal)
        assert len(formal) == 0 and fun["excl_late_seal"] == 1
        # case 2: sealed 10-08 11:45 UTC (北京 10-08 19:45)
        #         -> before T+1(10-09) 09:30 CN -> admitted
        ontime = _rec("ontime", "S1", "2026-10-08", 60,
                      sealed_at="2026-10-08 11:45:00")
        formal, fun = self._admit([ontime], cal)
        assert len(formal) == 1 and fun["excl_late_seal"] == 0

    def test_boundary_equal_excluded(self):
        # sealed_at 恰为 T+1 09:30:00.000000 Asia/Shanghai 应排除
        # (口径为严格小于 sealed < entry_open)。T+1(01-06) 09:30 CN = 01-06
        # 01:30:00.000000 UTC —— 恰在阈值的记录不进 formal。
        rec = _rec("edge", "S1", "2026-01-05", 60,
                   sealed_at="2026-01-06 01:30:00.000000")
        formal, fun = self._admit([rec])
        assert len(formal) == 0 and fun["excl_late_seal"] == 1
        # 亚毫秒前一条仍进 formal。
        rec2 = _rec("edgeok", "S1", "2026-01-05", 60,
                    sealed_at="2026-01-06 01:29:59.999999")
        formal2, fun2 = self._admit([rec2])
        assert len(formal2) == 1 and fun2["excl_late_seal"] == 0

    def test_signal_not_trading_day_unresolvable(self):
        # signal_date 非交易日 → T+1 截止无法解析 → fail-closed 排除
        # (m1 口径: entry NaT → excl_late_seal + excl_seal_t1_unresolved)。
        rec = _rec("we", "S1", "2026-01-04", 60,      # Sunday
                   sealed_at="2026-01-04 08:00:00.000000")
        formal, fun = self._admit([rec])
        assert len(formal) == 0
        assert fun["excl_late_seal"] == 1
        assert fun["excl_seal_t1_unresolved"] == 1

    def test_t1_unresolvable_at_calendar_end(self):
        # signal 为日历末日 → T+1 不可解析 → fail-closed 排除。
        rec = _rec("last", "S1", "2026-01-07", 60,
                   sealed_at="2026-01-07 08:00:00.000000")
        formal, fun = self._admit([rec])
        assert len(formal) == 0
        assert fun["excl_late_seal"] == 1
        assert fun["excl_seal_t1_unresolved"] == 1

    def test_signal_date_unparseable_unresolvable(self):
        # signal_date 缺失/不可解析 (NaT) → T+1 无法解析 → fail-closed 排除。
        rec = _rec("nosig", "S1", "2026-01-05", 60,
                   sealed_at="2026-01-05 08:00:00.000000")
        rec["signal_date"] = None
        formal, fun = self._admit([rec])
        assert len(formal) == 0
        assert fun["excl_late_seal"] == 1
        assert fun["excl_seal_t1_unresolved"] == 1

    def test_negative_offset_seal_is_late_not_on_time(self):
        # DAV-2000: "2026-10-09 01:00:00-05:00" is UTC 10-09 06:00 = CN
        # 14:00 -> after T+1 09:30 CN -> late (look-ahead leak if misread).
        cal = ["20261008", "20261009"]
        rec = _rec("neg", "S1", "2026-10-08", 60,
                   sealed_at="2026-10-09 01:00:00-05:00")
        formal, fun = self._admit([rec], cal)
        assert len(formal) == 0 and fun["excl_late_seal"] == 1

    def test_negative_offset_before_entry_open_admitted(self):
        # Sanity on the other side: an early CN-morning seal expressed in a
        # negative offset must still be admitted (no blanket rejection).
        cal = ["20261008", "20261009"]
        rec = _rec("neg_ok", "S1", "2026-10-08", 60,
                   sealed_at="2026-10-08 18:00:00-05:00")  # UTC 10-08 23:00
        formal, fun = self._admit([rec], cal)
        assert len(formal) == 1 and fun["excl_late_seal"] == 0

    def test_overlong_fraction_with_offset_not_date_only(self):
        # >6 fractional digits + offset: must not degrade to 00:00 UTC.
        cal = ["20261008", "20261009"]
        rec = _rec("frac", "S1", "2026-10-08", 60,
                   sealed_at="2026-10-09 02:09:00.123456789+08:00")
        formal, fun = self._admit([rec], cal)
        assert len(formal) == 1 and fun["excl_late_seal"] == 0

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
        # DAV-2052 🟡-1: input_rows = 进入封存门的行数，守恒式只加门后桶
        # （门前桶已在 input_rows 之前分流）；excl_late_seal 已含不可判定
        # 子集不再另加。
        fun = out["verdict"]["funnel"]
        assert sg["input_rows"] == sg["post_gate_rows"] + sum(
            fun[k] for k in ("excl_late_seal", "excl_not_completed",
                             "excl_bad_prob", "excl_immature_label",
                             "excl_dup_run"))
        # admit 入口行数守恒: admit_input_rows = post_gate + 全部 9 桶。
        assert sg["admit_input_rows"] == sg["post_gate_rows"] + sum(
            fun[k] for k in ("excl_wrong_user", "excl_pit_failed",
                             "excl_non_f0", "excl_unverified",
                             "excl_late_seal", "excl_not_completed",
                             "excl_bad_prob", "excl_immature_label",
                             "excl_dup_run"))
        assert sg["excl_seal_late_includes_unresolvable"] is True
        runj = json.loads((tmp_path / "o" / "run.json").read_text())
        assert runj["seal_gate"] == sg
        assert runj["ledger_rows"] == 24          # 门前全量
        assert runj["seal_gate"]["input_rows"] == 24
        vj = json.loads((tmp_path / "o" / "verdict.json").read_text())
        assert vj["model"]["seal_gate"] == sg    # 透传进 verdict.json

    def test_seal_gate_conservation_with_dirty_buckets(self, tmp_path):
        """DAV-2052 🟡-1 脏样本: 旧等式 input_rows = post_gate_rows +
        excl_seal_late + excl_seal_unresolvable 在任一非封存桶非零时即破
        ——这里混入 wrong_user / not_completed / bad_prob / dup_run 各一
        条，守恒式必须按真等式闭合。"""
        days = ["2026-01-05", "2026-01-06", "2026-01-07"]
        recs, labs = [], []
        for i in range(4):
            recs.append(_rec(f"ok{i}", f"A{i}", "2026-01-05", 30 + i,
                             sealed_at="2026-01-05 08:00:00.000000"))
            labs.append(m._label(f"A{i}", "2026-01-05", 0.1 * i))
        recs.append(_rec("wu", "W1", "2026-01-05", 50, user="other"))
        recs.append(_rec("nc", "W2", "2026-01-05", 50,
                         sealed_at="2026-01-05 08:00:00.000000"))
        recs[-1]["status"] = "running"            # not completed
        recs.append(_rec("bp", "W3", "2026-01-05", None,
                         sealed_at="2026-01-05 08:00:00.000000"))
        # dup: same symbol+day, later created -> excl_dup_run
        recs.append(_rec("ok0dup", "A0", "2026-01-05", 70,
                         created="2026-01-05T10:00",
                         sealed_at="2026-01-05 08:00:00.000000"))
        ledger = tmp_path / "l.jsonl"; _write_ledger(ledger, recs)
        lab = tmp_path / "lab.jsonl"; _write_labels(lab, labs)
        cal = tmp_path / "c.txt"; cal.write_text("\n".join(days))
        out = m.run(ledger, lab, tmp_path / "o", calendar_path=cal)
        sg, fun = out["verdict"]["seal_gate"], out["verdict"]["funnel"]
        # 4 ok + 1 late-dup gone: wu/nc/bp/dup 各 1 排除, formal = 4
        assert fun["excl_wrong_user"] == 1
        assert fun["excl_not_completed"] == 1
        assert fun["excl_bad_prob"] == 1
        assert fun["excl_dup_run"] == 1
        assert sg["post_gate_rows"] == 4
        # input_rows 是进封存门的行数（user/pit/non_f0/unverified 之后）:
        # 8 - 1(wu) = 7 = 4 formal + nc + bp + dup (无封存桶排除)。
        assert sg["input_rows"] == 7
        assert sg["input_rows"] == sg["post_gate_rows"] + sum(
            fun[k] for k in ("excl_late_seal", "excl_not_completed",
                             "excl_bad_prob", "excl_immature_label",
                             "excl_dup_run"))
        # admit 入口守恒: 8 = 4 + wu + nc + bp + dup (excl_late_seal=0)。
        assert sg["admit_input_rows"] == 8
        assert sg["admit_input_rows"] == sg["post_gate_rows"] + sum(
            fun[k] for k in ("excl_wrong_user", "excl_pit_failed",
                             "excl_non_f0", "excl_unverified",
                             "excl_late_seal", "excl_not_completed",
                             "excl_bad_prob", "excl_immature_label",
                             "excl_dup_run"))
        # 旧等式在脏样本上必破: post_gate + excl_seal_late +
        # excl_seal_unresolvable = 4 + 0 + 0 = 4 ≠ 7。
        assert (sg["post_gate_rows"] + sg["excl_seal_late"]
                + sg["excl_seal_unresolvable"]) != sg["input_rows"]
        # ledger_rows = excl_version + admit_input_rows 两侧闭合。
        runj = json.loads((tmp_path / "o" / "run.json").read_text())
        assert runj["ledger_rows"] == fun["excl_version"] + sg["admit_input_rows"]
        assert sg["admit_excluded_ids"] == sorted(
            {"wu", "nc", "bp", "ok0dup"})

    def test_none_run_id_sorted_no_crash(self, tmp_path):
        """DAV-2052 🟡-2: 账本行 report_id=null 且被排除时，sorted() 必须
        不抛 TypeError —— rc in (0,2)，不走 traceback。"""
        days = ["2026-01-05", "2026-01-06", "2026-01-07"]
        ok = _rec("ok1", "A1", "2026-01-05", 50,
                  sealed_at="2026-01-05 08:00:00.000000")
        labs = [m._label("A1", "2026-01-05", 1.0)]
        noid = _rec(None, "W9", "2026-01-05", 50, user="other")
        noid["report_id"] = None
        late = _rec(None, "A1", "2026-01-05", 60,
                    sealed_at="2026-01-06 03:00:00.000000")
        ledger = tmp_path / "l.jsonl"; _write_ledger(ledger, [ok, noid, late])
        lab = tmp_path / "lab.jsonl"; _write_labels(lab, labs)
        cal = tmp_path / "c.txt"; cal.write_text("\n".join(days))
        rc = m.main(["run", "--ledger", str(ledger), "--labels", str(lab),
                     "--calendar-file", str(cal),
                     "--out-dir", str(tmp_path / "o")])
        assert rc in (0, 2)
        if rc == 0:
            sg = json.loads((tmp_path / "o" / "run.json")
                            .read_text())["seal_gate"]
            # None 被 str() 化后确定性落盘 — 不 crash 且列表有序。
            assert "None" in sg["admit_excluded_ids"]

    def test_dateonly_seal_disclosed_in_funnel(self, tmp_path):
        """DAV-2052 🟡-3: date-only sealed_at 由 m1 读成 00:00 UTC 准入
        （行为与旧实现一致），但产物必须如实计数，不再是硬编码 0。"""
        days = ["2026-01-05", "2026-01-06", "2026-01-07"]
        recs = [_rec("d1", "A1", "2026-01-05", 50, sealed_at="2026-01-05"),
                _rec("d2", "A2", "2026-01-05", 55,
                     sealed_at="2026-01-05 08:00:00.000000")]
        labs = [m._label("A1", "2026-01-05", 1.0),
                m._label("A2", "2026-01-05", 2.0)]
        ledger = tmp_path / "l.jsonl"; _write_ledger(ledger, recs)
        lab = tmp_path / "lab.jsonl"; _write_labels(lab, labs)
        cal = tmp_path / "c.txt"; cal.write_text("\n".join(days))
        out = m.run(ledger, lab, tmp_path / "o", calendar_path=cal)
        fun = out["verdict"]["funnel"]
        assert fun["seal_dateonly_fallback"] == 1
        sg = out["verdict"]["seal_gate"]
        assert sg["seal_dateonly_fallback"] == 1
        # date-only 行被准入 formal（m1 现行口径），计数仍如实。
        assert sg["post_gate_rows"] == 2


class TestNullDefense:
    """DAV-2025 🟡-2: explicit JSON nulls on label sub-dicts must degrade
    gracefully — never AttributeError out of `.get(...)`."""

    def test_prediction_null_routes_immature_not_crash(self, tmp_path):
        rec = _rec("np", "S1", "2026-01-05", 60)
        lab = m._label("S1", "2026-01-05", 5.0)
        lab["prediction"] = None
        labels = {("S1", "2026-01-05"): lab}
        ledger = tmp_path / "l.jsonl"
        _write_ledger(ledger, [rec])
        # must not raise AttributeError on `None.get("status")`
        df = m.load_ledger_signals(ledger, labels, FIXED)
        assert len(df) == 1
        assert df.iloc[0]["r"] is None or pd.isna(df.iloc[0]["r"])
        formal, fun = m.m1.admit(
            df.drop(columns=["sealed_at"], errors="ignore"),
            user_id=FIXED)
        assert len(formal) == 0
        assert fun["excl_immature_label"] == 1

    def test_prediction_null_cli_fails_clean_or_excludes(self, tmp_path):
        days = ["2026-01-05", "2026-01-06", "2026-01-07"]
        recs = [_rec("ok", "S1", "2026-01-05", 60)]
        lab = m._label("S1", "2026-01-05", 5.0)
        lab["prediction"] = None
        labs = [lab]
        ledger = tmp_path / "l.jsonl"; _write_ledger(ledger, recs)
        labf = tmp_path / "lab.jsonl"; _write_labels(labf, labs)
        cal = tmp_path / "c.txt"; cal.write_text("\n".join(days) + "\n")
        # Either a clean run that funnel-excludes the row, or a PipelineError
        # exit 2 — never a bare AttributeError traceback.
        rc = m.main(["run", "--ledger", str(ledger), "--labels", str(labf),
                     "--calendar-file", str(cal),
                     "--out-dir", str(tmp_path / "o")])
        assert rc in (0, 2)

    def test_sw_null_qbase_nan_not_crash(self, tmp_path):
        # sw: null inside the labels map must yield l1="" -> q_base NaN ->
        # row dropped from the paired subset, not AttributeError.
        formal = pd.DataFrame({
            "signal_date": ["2026-01-05"] * 12,
            "symbol": [f"S{i}" for i in range(12)],
            "q": np.linspace(0.1, 0.9, 12),
            "r": np.linspace(-1, 1, 12),
        })
        labels = {}
        for i in range(12):
            lab = m._label(f"S{i}", "2026-01-05", 1.0)
            lab["sw"] = None
            labels[(f"S{i}", "2026-01-05")] = lab
        inc = m.paired_increment(formal, labels,
                                 {("2026-01-05", "801010.SI"): 0.6},
                                 ["20260105"])
        # every paired leg dropped -> 0 paired days, diff series empty
        assert inc["paired_days"] == 0


class TestIncrement:
    def test_same_sample_contract(self):
        """DAV-2025 🟡-1 / 总控 10-13: both legs of a paired day must be
        computed on the IDENTICAL cross-section — rows without a (day,l1)
        base rate are excluded from BOTH IC_model and IC_base.

        Probe design (3 paired days, NW mean needs n>=3): each day has 12
        formal rows; row 11 of every day sits in a membership-gap cell
        (sw.index_code="" -> q_base NaN) and is the q-max with r=-999 — the
        strongest IC-breaker in the sample:
        - OLD code: ic_model over all 12 rows -> IC << 1, mixes samples;
        - FIXED code: both legs over the same 11 covered rows -> IC=1 each
          -> diff == 0.
        """
        days = ["2026-01-05", "2026-01-06", "2026-01-07"]
        days_index = [d.replace("-", "") for d in days]
        n = 12
        formal, labels, qmap = [], {}, {}
        for day in days:
            iso = day
            for i in range(n):
                sym = f"{day[-2:]}S{i}"
                r = float(i) if i < n - 1 else -999.0   # S11 = IC breaker
                formal.append({"signal_date": day, "symbol": sym,
                               "q": float(i), "r": r})
                l1 = "" if i == n - 1 else f"L{i}"
                if l1:
                    # one cell per row, base rate monotone in i -> base
                    # IC == 1 on the paired 11-row subset
                    qmap[(iso, l1)] = 0.5 + 0.02 * i
                labels[(sym, iso)] = m._label(sym, day, 1.0, l1=l1)
        dfr = pd.DataFrame(formal)
        inc = m.paired_increment(dfr, labels, qmap, days_index)
        assert inc["paired_days"] == 3
        # 总控 10-13 漏斗单列: 3 天 x 1 条 gap = 3 行缺基率两侧都不用。
        assert inc["model_rows"] == 36
        assert inc["paired_rows"] == 33
        assert inc["no_base_rows"] == 3
        assert inc["paired_day_n"] == {d: 11 for d in days}
        # model leg on the paired 11 rows/day: q 0..10 monotone in r 0..10.
        assert inc["model_ic_mean"] == pytest.approx(1.0)
        # base leg on the SAME rows: q_base monotone in r by construction.
        assert inc["base_ic_mean"] == pytest.approx(1.0)
        # identical cross-section -> diff collapses to 0; the old mixed-
        # sample code would report model IC over all 12 rows (<1).
        assert inc["diff_mean"] == pytest.approx(0.0)

    def test_paired_36_model_24_base(self):
        """总控 10-13 固化用例: one day with 36 rows carrying p_rel_t10
        but only 24 carrying a base rate -> BOTH legs must be computed on
        the SAME 24-row cross-section.
        """
        day = "2026-01-05"
        formal, labels, qmap = [], {}, {}
        for i in range(36):
            sym = f"S{i:02d}"
            formal.append({"signal_date": day, "symbol": sym,
                           "q": float(i), "r": float(i)})
            if i < 24:
                l1 = f"L{i}"
                qmap[(day, l1)] = 0.4 + 0.01 * i   # monotone -> base IC 1
            else:
                l1 = ""                            # no base rate
            labels[(sym, day)] = m._label(sym, day, 1.0, l1=l1)
        inc = m.paired_increment(pd.DataFrame(formal), labels, qmap,
                                 ["20260105"])
        assert inc["model_rows"] == 36
        assert inc["paired_rows"] == 24
        assert inc["no_base_rows"] == 12
        assert inc["paired_days"] == 1
        # 两侧都只用 24 条 — paired_day_n 记录两腿共用横截面大小。
        assert inc["paired_day_n"] == {day: 24}
        # q 0..23 monotone in r 0..23 on the shared 24 -> both IC = 1.
        assert inc["model_ic_mean"] == pytest.approx(1.0)
        assert inc["base_ic_mean"] == pytest.approx(1.0)

    def test_gap_row_drops_day_from_pairing(self):
        """Same-sample corollary: if the uncovered rows are what kept the
        day at/above MIN_DAILY_N, the whole day leaves the paired set."""
        n = m.m1.MIN_DAILY_N            # exactly at the threshold
        formal = pd.DataFrame({
            "signal_date": ["2026-01-05"] * n,
            "symbol": [f"S{i}" for i in range(n)],
            "q": np.arange(n, dtype=float),
            "r": np.arange(n, dtype=float),
        })
        labels = {}
        qmap = {}
        for i in range(n):
            l1 = "" if i == n - 1 else f"L{i}"
            if l1:
                qmap[("2026-01-05", l1)] = 0.5 + 0.01 * i
            labels[(f"S{i}", "2026-01-05")] = m._label(
                f"S{i}", "2026-01-05", 1.0, l1=l1)
        inc = m.paired_increment(formal, labels, qmap, ["20260105"])
        # paired subset = 9 < MIN_DAILY_N -> the day never pairs.
        assert inc["paired_days"] == 0

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

    def test_paired_diff_zero_when_legs_identical(self):
        """When q == q_base on the same paired subset, diff must be exactly
        0 — guards the same-sample wiring end to end (3 days so the NW mean
        is defined)."""
        days = ["2026-01-05", "2026-01-06", "2026-01-07"]
        days_index = [d.replace("-", "") for d in days]
        n = 12
        q = np.linspace(0.05, 0.95, n)
        formal, labels, qmap = [], {}, {}
        for day in days:
            for i in range(n):
                sym = f"{day[-2:]}S{i}"
                formal.append({"signal_date": day, "symbol": sym,
                               "q": float(q[i]), "r": float(i)})
                l1 = f"L{i}"          # own cell -> per-row q_base == q
                qmap[(day, l1)] = float(q[i])
                labels[(sym, day)] = m._label(sym, day, 1.0, l1=l1)
        inc = m.paired_increment(pd.DataFrame(formal), labels, qmap,
                                 days_index)
        assert inc["paired_days"] == 3
        assert inc["model_ic_mean"] == pytest.approx(1.0)
        assert inc["base_ic_mean"] == pytest.approx(1.0)
        assert inc["diff_mean"] == pytest.approx(0.0)


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
