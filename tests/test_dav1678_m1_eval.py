"""DAV-1678: unit tests for scripts/phase2/m1_eval.py.

Synthetic constructed data only — no DB, no akshare, no real model. Mirrors
the DAV-1573 signed-off criteria: average-rank ties, equal-weight day IC,
insufficient/rank-degenerate day labels, §3.2 quintile boundary-tie weights,
fixed 10 reliability bins, primary-run dedup by earliest created_at, NW60/120
inference and the A–D grade gate.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "phase2" / "m1_eval.py"

spec = importlib.util.spec_from_file_location("m1_eval", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["m1_eval"] = mod
spec.loader.exec_module(mod)


def _day(day, qs, rs, vkey="v1", start=0):
    return [{"signal_date": day, "version_key": vkey,
             "symbol": f"S{start + i:05d}", "q": q, "r": r,
             "timing_class": "F0", "input_pit_status": "VERIFIED"}
            for i, (q, r) in enumerate(zip(qs, rs))]


def _sealed(day, qs, rs, sealed_at, **kw):
    """_day() rows stamped with one seal timestamp (UTC-naive ledger form)."""
    rows = _day(day, qs, rs, **kw)
    for r in rows:
        r["sealed_at"] = sealed_at
    return rows


class TestDailyIC:
    def test_monotone_ic_one(self):
        d, _ = mod.admit(pd.DataFrame(
            _day("20260105", range(1, 21), np.linspace(-0.05, 0.08, 20))))
        days = mod.eval_days(d)
        assert days[0].status == "ok"
        assert days[0].ic == pytest.approx(1.0)

    def test_prob_scale_invariant(self):
        r = np.linspace(-0.05, 0.08, 20)
        a = mod.eval_days(mod.admit(pd.DataFrame(
            _day("20260105", range(1, 21), r)))[0])[0]
        b = mod.eval_days(mod.admit(pd.DataFrame(
            _day("20260105", np.arange(1, 21) / 100, r)))[0])[0]
        assert a.ic == pytest.approx(b.ic)

    def test_anticorrelation(self):
        d, _ = mod.admit(pd.DataFrame(
            _day("20260105", range(1, 21), np.linspace(0.08, -0.05, 20))))
        assert mod.eval_days(d)[0].ic == pytest.approx(-1.0)

    def test_ties_average_rank(self):
        # all q tied → rank-degenerate, NOT 0, NOT insufficient
        d, _ = mod.admit(pd.DataFrame(_day("20260105", [50] * 12, range(12))))
        assert mod.eval_days(d)[0].status == "rank_degenerate"
        # all r tied → same
        d, _ = mod.admit(pd.DataFrame(_day("20260105", range(12), [0.01] * 12)))
        assert mod.eval_days(d)[0].status == "rank_degenerate"


class TestDayStatus:
    def test_insufficient_under_10(self):
        d, _ = mod.admit(pd.DataFrame(_day("20260105", range(1, 8), range(7))))
        days = mod.eval_days(d)
        assert days[0].status == "insufficient"
        assert np.isnan(days[0].ic)

    def test_exactly_10_ok(self):
        d, _ = mod.admit(pd.DataFrame(
            _day("20260105", range(1, 11), range(10))))
        assert mod.eval_days(d)[0].status == "ok"

    def test_real_date_gaps_kept(self):
        # days are the trading-day index; absent dates are simply missing
        rows = _day("20260105", range(1, 21), np.linspace(0, 0.1, 20))
        rows += _day("20260120", range(1, 21), np.linspace(0, 0.1, 20), start=20)
        d, _ = mod.admit(pd.DataFrame(rows))
        days = mod.eval_days(d)
        assert [x.date for x in days] == ["20260105", "20260120"]

    def test_calendar_slots_no_data(self):
        # calendar aligned: a zero-signal trading day stays a no_data slot
        rows = _day("20260105", range(1, 21), np.linspace(0, 0.1, 20))
        rows += _day("20260109", range(1, 21), np.linspace(0, 0.1, 20), start=30)
        d, _ = mod.admit(pd.DataFrame(rows))
        cal = ["20260105", "20260106", "20260107", "20260108", "20260109"]
        days = mod.eval_days(d, calendar=cal)
        assert [x.status for x in days] == ["ok", "no_data", "no_data",
                                            "no_data", "ok"]


class TestQuintile:
    def test_weights_sum_to_h(self):
        q = np.array([1, 2, 3, 4, 5, 5, 6, 7, 8, 9, 10, 11], float)
        wl, wh, h = mod.quintile_weights(q)
        assert h == pytest.approx(12 / 5)
        assert wl.sum() == pytest.approx(h)
        assert wh.sum() == pytest.approx(h)

    def test_boundary_tie_fractional(self):
        # n=12 → h=2.4; prob 3 has L=2 < 2.4 → partial weight 0.4/1
        q = np.array([1, 2, 3, 4, 5, 5, 6, 7, 8, 9, 10, 11], float)
        wl, wh, h = mod.quintile_weights(q)
        assert wl[2] == pytest.approx(0.4)   # the '3' entry shares boundary
        assert wl[4] == 0.0                  # '5' fully above low tail

    def test_diff_sign(self):
        r_hi = np.linspace(-0.05, 0.10, 20)
        d, _ = mod.admit(pd.DataFrame(_day("20260105", range(1, 21), r_hi)))
        assert mod.eval_days(d)[0].quintile_diff > 0


class TestAdmission:
    def test_funnel_counts(self):
        rows = _day("20260108", range(1, 15), np.linspace(-1, 1, 14))
        rows[0]["input_pit_status"] = "FAILED"
        rows[1]["timing_class"] = "F1"
        rows[2]["input_pit_status"] = "UNVERIFIED"
        rows[3]["q"] = 0
        rows[4]["r"] = np.nan
        d, fun = mod.admit(pd.DataFrame(rows))
        assert len(d) == 9
        assert fun["excl_pit_failed"] == 1
        assert fun["excl_non_f0"] == 1
        assert fun["excl_unverified"] == 1
        assert fun["excl_bad_prob"] == 1
        assert fun["excl_immature_label"] == 1

    def test_dedup_earliest_created_wins(self):
        rows = []
        for rid, cat, q in [(2, "2026-01-05T09:30", 10),
                            (1, "2026-01-05T09:00", 90),
                            (3, "2026-01-05T10:00", 50)]:
            rows.append({"signal_date": "20260105", "version_key": "v1",
                         "symbol": "S001", "run_id": rid, "created_at": cat,
                         "q": q, "r": 0.01, "timing_class": "F0",
                         "input_pit_status": "VERIFIED", "completed": True})
        d, fun = mod.admit(pd.DataFrame(rows))
        assert len(d) == 1
        assert d.iloc[0]["run_id"] == 1
        assert fun["excl_dup_run"] == 2

    def test_not_completed_excluded(self):
        # 🟡-2: a stray incomplete row must never reach the formal queue
        rows = _day("20260111", range(1, 21), np.linspace(0, 0.1, 20))
        rows.append({"signal_date": "20260111", "version_key": "v1",
                     "symbol": "INC", "q": 0.5, "r": 0.01,
                     "timing_class": "F0", "input_pit_status": "VERIFIED",
                     "completed": False})
        d, fun = mod.admit(pd.DataFrame(rows))
        assert fun["excl_not_completed"] == 1
        assert len(d) == 20

    def test_bad_prob_funnel_count(self):
        # 🟢-3: unparseable/mixed-scale prob surfaced in funnel
        rows = _day("20260112", list(range(1, 11)) + ["abc"],
                    np.linspace(0, 0.1, 11))
        d, fun = mod.admit(pd.DataFrame(rows))
        assert fun["excl_bad_prob"] == 1


class TestInferenceAndGrade:
    def _strong_days(self, n):
        rng = np.random.default_rng(7)
        rows = []
        for i in range(n):
            day = f"2026{(i // 22) + 1:02d}{(i % 22) + 1:02d}"
            rs = np.linspace(-0.03, 0.09, 20) + rng.normal(0, 0.005, 20)
            rows += _day(day, range(1, 21), rs, start=i * 100)
        d, _ = mod.admit(pd.DataFrame(rows))
        return mod.eval_days(d)

    def test_grade_c_at_60_days(self):
        v = mod.grade(self._strong_days(60), {})
        assert v["grade"] == "C"
        assert v["c_gate"]["days_ok"] and v["c_gate"]["nw60_lo_pos"]

    def test_grade_b_at_59_days(self):
        v = mod.grade(self._strong_days(59), {})
        assert v["grade"] == "B"

    def test_grade_a_no_valid_days(self):
        d, _ = mod.admit(pd.DataFrame(_day("20260105", range(1, 8), range(7))))
        v = mod.grade(mod.eval_days(d), {})
        assert v["grade"] == "A"

    def test_multiversion_failclose(self):
        # 🔴 blocking: two version keys in one frame must NOT merge into a
        # single day series (which would double valid_days and pass C gate)
        rows = _day("20260110", range(1, 21), np.linspace(0, 0.1, 20))
        rows += _day("20260110", range(1, 21), np.linspace(0, 0.1, 20),
                     vkey="v2", start=50)
        mv = pd.DataFrame(rows)
        with pytest.raises(ValueError):
            mod.select_version(mv)

    def test_multiversion_select_single(self):
        # same frame, but caller picks one queue → one DayRow, valid_days=1
        rows = _day("20260110", range(1, 21), np.linspace(0, 0.1, 20))
        rows += _day("20260110", range(1, 21), np.linspace(0, 0.1, 20),
                     vkey="v2", start=50)
        one = mod.select_version(pd.DataFrame(rows), "v2")
        days = mod.eval_days(one)
        assert len(days) == 1 and days[0].n_t == 20
        v = mod.grade(days, {})
        assert v["valid_days"] == 1

    def test_multiversion_no_double_valid_days(self):
        # 35 real days × 2 versions must yield 35 valid days, not 70
        rows = []
        for i in range(35):
            day = f"2026{(i // 22) + 1:02d}{(i % 22) + 1:02d}"
            rs = np.linspace(0, 0.1, 20)
            rows += _day(day, range(1, 21), rs)
            rows += _day(day, range(1, 21), rs, vkey="v2", start=500)
        one = mod.select_version(pd.DataFrame(rows), "v1")
        v = mod.grade(mod.eval_days(one), {})
        assert v["valid_days"] == 35

    def test_nan_version_failclose(self):
        # DAV-1701: NaN version_key must count as a key of its own.
        # NaN×15 + 'v2'×15 previously escaped fail-close (dropna hid the
        # NaN group) and inflated n_t 15→30.
        rows = _day("20260110", range(1, 16), np.linspace(0, 0.1, 15),
                    vkey="v2")
        for i in range(15):
            rows.append({"signal_date": "20260110", "version_key": np.nan,
                         "symbol": f"N{i:05d}", "q": 0.5, "r": 0.01,
                         "timing_class": "F0", "input_pit_status": "VERIFIED"})
        mv = pd.DataFrame(rows)
        with pytest.raises(ValueError):
            mod.select_version(mv)
        # selecting v2 keeps exactly the 15 v2 rows (NaN rows stay out)
        one = mod.select_version(mv, "v2")
        days = mod.eval_days(one)
        assert len(days) == 1 and days[0].n_t == 15

    def test_single_version_typo_version_key_failclose(self):
        # DAV-1716: single-version frame + typo'd --version-key used to slip
        # the len(keys) <= 1 early return (all rows kept) and then be
        # written into run.json as the provenance label. Must fail closed.
        one = pd.DataFrame(_day("20260110", range(1, 21),
                                np.linspace(0, 0.1, 20), vkey="v1"))
        with pytest.raises(ValueError, match="not in input"):
            mod.select_version(one, "TYPO_KEY")

    def test_single_version_correct_version_key_ok(self):
        # DAV-1716: the matching key still selects (and passes through
        # unchanged) on a single-version frame.
        one = pd.DataFrame(_day("20260110", range(1, 21),
                                np.linspace(0, 0.1, 20), vkey="v1"))
        out = mod.select_version(one, "v1")
        assert len(out) == 20
        days = mod.eval_days(out)
        assert len(days) == 1 and days[0].n_t == 20

    def test_single_version_nan_key_typo_failclose(self):
        # DAV-1716: single all-NaN-version frame — "<NaN>" is selectable,
        # anything else is a typo and must fail closed.
        rows = _day("20260110", range(1, 16), np.linspace(0, 0.1, 15))
        for r in rows:
            r["version_key"] = np.nan
        frame = pd.DataFrame(rows)
        assert len(mod.select_version(frame, "<NaN>")) == 15
        with pytest.raises(ValueError, match="not in input"):
            mod.select_version(frame, "v1")

    def test_single_version_typo_provenance_not_written(self):
        # DAV-1716 end-to-end: a typo must abort run() before run.json can
        # record the bogus version_key label.
        rows = _day("20260105", range(1, 15), np.linspace(0, 0.1, 14),
                    vkey="v1")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ip = td / "in.csv"
            pd.DataFrame(rows).to_csv(ip, index=False)
            out = td / "out"
            with pytest.raises(ValueError, match="not in input"):
                mod.run(ip, out, version_key="TYPO_KEY")
            assert not (out / "run.json").exists()

    def test_nan_version_selectable(self):
        # the NaN queue itself can be selected by its "<NaN>" display name
        rows = _day("20260110", range(1, 16), np.linspace(0, 0.1, 15),
                    vkey="v2")
        for i in range(15):
            rows.append({"signal_date": "20260110", "version_key": np.nan,
                         "symbol": f"N{i:05d}", "q": 0.5, "r": 0.01,
                         "timing_class": "F0", "input_pit_status": "VERIFIED"})
        one = mod.select_version(pd.DataFrame(rows), "<NaN>")
        days = mod.eval_days(one)
        assert len(days) == 1 and days[0].n_t == 15

    def test_all_excluded_empty_calendar_no_crash(self):
        # DAV-1701: every row funnel-excluded + calendar provided → empty
        # day list (grade-A empty report), not ValueError from min().
        rows = _day("20260105", range(1, 15), np.linspace(0, 0.1, 14))
        for r in rows:
            r["input_pit_status"] = "FAILED"
        d, fun = mod.admit(pd.DataFrame(rows))
        assert len(d) == 0
        cal = ["20260105", "20260106"]
        days = mod.eval_days(d, calendar=cal)
        assert days == []
        v = mod.grade(days, fun)
        assert v["grade"] == "A" and v["valid_days"] == 0

    def test_dedup_earliest_created_wins_mixed_spelling(self):
        # DAV-1701 🟡-1/🟡-3: contract is earliest-created wins, whatever
        # the surviving 'completed' spelling. Mixed spellings (bool True
        # vs str "yes") cross created_at here: the old 'completed'-desc
        # key let spelling order override created_at and elected the
        # LATER-created run (run 2) — this test FAILS on the parent code
        # and PASSES on the fixed code (run 1).
        rows = [
            {"signal_date": "20260105", "version_key": "v1",
             "symbol": "S001", "run_id": 1,
             "created_at": "2026-01-05T09:00",
             "q": 0.9, "r": 0.01, "timing_class": "F0",
             "input_pit_status": "VERIFIED", "completed": True},
            {"signal_date": "20260105", "version_key": "v1",
             "symbol": "S001", "run_id": 2,
             "created_at": "2026-01-05T09:30",
             "q": 0.1, "r": 0.01, "timing_class": "F0",
             "input_pit_status": "VERIFIED", "completed": "yes"},
        ]
        d, fun = mod.admit(pd.DataFrame(rows))
        assert len(d) == 1 and d.iloc[0]["run_id"] == 1
        assert fun["excl_dup_run"] == 1

    def test_version_key_type_collision_failclose(self):
        # DAV-1701 🟡-2: int 2 vs str "2" must fail closed —
        # stringification must not silently merge them into one queue.
        rows = _day("20260110", range(1, 16), np.linspace(0, 0.1, 15),
                    vkey=2)
        rows += _day("20260110", range(1, 16), np.linspace(0, 0.1, 15),
                     vkey="2", start=50)
        with pytest.raises(ValueError, match="collide"):
            mod.select_version(pd.DataFrame(rows))

    def test_empty_days_parquet_keeps_schema(self):
        # DAV-1701 🟡-4: an empty day series must still carry the README
        # columns when written via the run() path (no (0,0) schemaless
        # frame). End-to-end through run() with an all-excluded input.
        rows = _day("20260105", range(1, 15), np.linspace(0, 0.1, 14))
        for r in rows:
            r["input_pit_status"] = "FAILED"
        inp = pd.DataFrame(rows)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ip = td / "in.csv"
            inp.to_csv(ip, index=False)
            cp = td / "cal.csv"
            cp.write_text("date\n20260105\n20260106\n")
            out = td / "out"
            res = mod.run(ip, out, calendar_path=cp)
            assert res["days"] == []
            assert res["verdict"]["grade"] == "A"
            back = pd.read_parquet(out / "m1_day_series.parquet")
            assert list(back.columns) == ["date", "n_t", "status", "ic",
                                           "quintile_diff", "quintile_tied_n",
                                           "brier"]
            assert len(back) == 0

    def test_nw_gap_not_compressed(self):
        # two strongly-autocorrelated blocks separated by a wide NaN gap:
        # compressing the gap would let lag-1 pairs bridge block A→B and
        # inflate the NW variance. With real spacing kept, lag-1 pairs stay
        # inside each block → smaller variance.
        a = np.concatenate([np.linspace(0.5, 0.5, 10),
                            np.full(40, np.nan),
                            np.linspace(-0.5, -0.5, 10)])
        # same data with the gap removed (compressed)
        b = np.concatenate([np.linspace(0.5, 0.5, 10),
                            np.linspace(-0.5, -0.5, 10)])
        _, lo_a, _, _ = mod.newey_west_ci(a, band=10)
        _, lo_b, _, _ = mod.newey_west_ci(b, band=10)
        # compressed series couples +0.5 with −0.5 at lag 1 → huge negative
        # autocovariance → tighter (here smaller-lo) interval; gap version is
        # the honest, wider one. Assert they differ (not silently identical).
        assert not np.isclose(lo_a, lo_b)


class TestReliability:
    def test_fixed_10_bins(self):
        d, _ = mod.admit(pd.DataFrame(_day("20260109", range(1, 21), range(20))))
        rel = mod.reliability_table(d)
        assert [r["bin"] for r in rel] == [
            "1-10", "11-20", "21-30", "31-40", "41-50",
            "51-60", "61-70", "71-80", "81-90", "91-99"]

    def test_bin_membership(self):
        d, _ = mod.admit(pd.DataFrame(_day("20260109", range(1, 21), range(20))))
        rel = mod.reliability_table(d)
        assert rel[0]["n"] == 10 and rel[1]["n"] == 10
        assert sum(r["n"] for r in rel) == 20


class TestRepeatMeasures:
    def test_identical_pairs(self):
        rng = np.random.default_rng(1)
        q1 = np.linspace(0.1, 0.9, 40)
        C, A, m = mod.repeat_measures(q1, q1 + rng.normal(0, 0.01, 40))
        assert m == 40 and C > 0 and A > 0

    def test_shuffled_pairs_low_C(self):
        rng = np.random.default_rng(2)
        q1 = np.linspace(0.1, 0.9, 40)
        C, _, _ = mod.repeat_measures(q1, rng.permutation(q1))
        Csame, _, _ = mod.repeat_measures(q1, q1)
        assert C < Csame


class TestUserFilter:
    """DAV-1740: fixed-account filter counts into excl_wrong_user."""

    def test_default_user_id_is_fixed_account(self):
        assert mod.DEFAULT_USER_ID == "429163f7-50b6-4982-8bdf-96ae99506843"

    def test_no_user_column_means_no_exclusion(self):
        rows = _day("20260105", range(1, 21), np.linspace(0, 0.1, 20))
        _, fun = mod.admit(pd.DataFrame(rows))
        assert fun["excl_wrong_user"] == 0

    def test_wrong_user_excluded_and_counted(self):
        fixed = "429163f7-50b6-4982-8bdf-96ae99506843"
        rows = _day("20260105", range(1, 21), np.linspace(0, 0.1, 20))
        for r in rows:
            r["user_id"] = fixed
        for i in range(5):
            rows.append({"signal_date": "20260105", "version_key": "v1",
                         "symbol": f"X{i:05d}", "q": 0.5, "r": 0.01,
                         "timing_class": "F0", "input_pit_status": "VERIFIED",
                         "user_id": "local-default-user"})
        d, fun = mod.admit(pd.DataFrame(rows))
        assert fun["excl_wrong_user"] == 5
        assert len(d) == 20
        assert set(d["user_id"].unique().tolist()) == {fixed}

    def test_custom_user_id_param_selects_other_account(self):
        rows = _day("20260105", range(1, 21), np.linspace(0, 0.1, 20))
        for r in rows:
            r["user_id"] = "local-default-user"
        d, fun = mod.admit(pd.DataFrame(rows), user_id="local-default-user")
        assert fun["excl_wrong_user"] == 0
        assert len(d) == 20
        d2, fun2 = mod.admit(pd.DataFrame(rows))
        assert fun2["excl_wrong_user"] == 20
        assert len(d2) == 0


class TestSealTimeGate:
    """DAV-1991 封存时点门 (总控 10-13 令).

    The formal queue only takes records sealed STRICTLY before the signal
    day's next trading day 09:30 (Asia/Shanghai). The rule is re-derived
    from `sealed_at` rather than trusted from the `timing_class` label, so a
    mislabeled F0 row (or a frame with no label at all — the default is F0)
    is still caught.

    总控时区口径: sealed_at / seal_basis_at / created_at are timezone-NAIVE
    UTC (the ledger tail line "2026-10-12 11:45:05" is Beijing 19:45). They
    are parsed as UTC, converted to Asia/Shanghai, and compared with the T+1
    trading-day 09:30 in that zone — never read as local time. A seal stamp
    of "2026-10-09 02:09:00" is Beijing 10:09, i.e. AFTER the open.
    """

    CAL = ["20260105", "20260106", "20260107", "20260108", "20260109",
           "20260112", "20260113"]
    # 总控点名的 10 月用例日历：10-08 → T+1 = 10-09；10-09（周五）→ T+1 = 10-12
    OCT_CAL = ["20261008", "20261009", "20261012", "20261013"]

    def test_controller_tz_case_late_seal_excluded(self):
        # 总控时区口径用例①: 信号日 2026-10-08, T+1 = 2026-10-09。
        # sealed_at "2026-10-09 02:09:00" 是无时区标记的 UTC（=北京 10-09
        # 10:09），晚于 T+1 开盘 09:30 → 只进诊断。若把该串误当北京时间
        # （02:09 < 09:30）就会错放进正式队列——本用例专门钉死这个误读。
        rows = _sealed("20261008", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-10-09 02:09:00.000000")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.OCT_CAL)
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_controller_tz_case_forward_seal_admitted(self):
        # 总控时区口径用例②: sealed_at "2026-10-08 11:45:00"（UTC = 北京
        # 10-08 19:45）严格早于 T+1 开盘 → 进正式队列。
        rows = _sealed("20261008", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-10-08 11:45:00.000000")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.OCT_CAL)
        assert fun["excl_late_seal"] == 0 and len(d) == 20

    def test_ledger_tail_style_seal_excluded(self):
        # 账本末行式: 信号日 2026-10-09（周五）→ T+1 = 2026-10-12（周一）。
        # 次日快照运行在 2026-10-12 11:45:05 UTC（=北京 19:45）才封存，晚于
        # 当日 09:30 开盘 → 排除（对应总控所述"晚于信号日封存"的那批记录）。
        rows = _sealed("20261009", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-10-12 11:45:05.000000")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.OCT_CAL)
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_utc_stamp_between_open_and_local_open_is_late(self):
        # 判别带: T+1 当日 UTC 01:30–09:30 之间的封存串——按 UTC 读是盘后
        # （晚），按本地读是盘前（前瞻）。口径要求按 UTC → 必须判晚。
        rows = _sealed("20261008", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-10-09 09:00:00.000000")   # = 北京 17:00
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.OCT_CAL)
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_forward_sealed_row_admitted(self):
        # T+1 = 20260106; deadline 01:30 UTC; sealed 01:20 UTC → forward
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:20:00.000000")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["seal_gate_applied"] is True
        assert fun["excl_late_seal"] == 0
        assert len(d) == 20

    def test_sealed_one_microsecond_before_open_admitted(self):
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:29:59.999999")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["excl_late_seal"] == 0 and len(d) == 20

    def test_sealed_exactly_at_open_excluded(self):
        # strict <: sealed exactly at the T+1 open is NOT forward-sealed
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:30:00.000000")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["excl_late_seal"] == 20 and len(d) == 0
        assert mod.eval_days(d) == []

    def test_late_seal_excluded_and_counted(self):
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-08 10:00:00.000000")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_backfilled_record_excluded(self):
        # created on the signal day, sealed two days later (a backfill)
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-07 10:00:00.000000")
        for r in rows:
            r["created_at"] = "2026-01-05 08:00:00.000000"
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_tz_aware_iso_sealed_at(self):
        # explicit +08:00 offsets: 09:20 CST is before the open, 09:40 after
        early = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                        "2026-01-06T09:20:00+08:00")
        d, fun = mod.admit(pd.DataFrame(early), calendar=self.CAL)
        assert fun["excl_late_seal"] == 0 and len(d) == 20
        late = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06T09:40:00+08:00")
        d2, fun2 = mod.admit(pd.DataFrame(late), calendar=self.CAL)
        assert fun2["excl_late_seal"] == 20 and len(d2) == 0

    def test_mixed_batch_counts_late_only(self):
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:00:00.000000")
        for i in range(5):
            rows.append({"signal_date": "20260105", "version_key": "v1",
                         "symbol": f"L{i:05d}", "q": 0.5, "r": 0.01,
                         "timing_class": "F0", "input_pit_status": "VERIFIED",
                         "sealed_at": "2026-01-07 03:00:00.000000"})
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["excl_late_seal"] == 5 and len(d) == 20

    def test_unparseable_sealed_at_fails_closed(self):
        # a seal time that cannot be parsed cannot prove forward sealing
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "not-a-timestamp")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["seal_unparseable"] == 20
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_missing_sealed_at_value_excluded(self):
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:00:00.000000")
        rows[0]["sealed_at"] = None
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["seal_unparseable"] == 1 and fun["excl_late_seal"] == 1
        assert len(d) == 19

    def test_t1_unresolved_excluded_and_counted(self):
        # 总控 10-13 令 rework (DAV-2007 review M-2): a calendar that ends
        # before the signal's T+1 leaves the deadline unresolvable, and the
        # row is EXCLUDED — _classify_timing's F0 is the ledger's
        # classification口径, not the formal score's admission rule.
        cal = ["20260104", "20260105"]
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-09 23:00:00.000000")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=cal)
        assert fun["excl_seal_t1_unresolved"] == 20
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_controller_case_late_july_backfill_without_july_calendar(self):
        # 总控点名的泄漏场景①: a late-July signal sealed in the 10-04
        # backfill, with a calendar that starts 20260901 — the signal day
        # precedes the calendar's first day, so no T+1 entry open can be
        # resolved and the record cannot be proven forward-sealed. It must
        # be excluded (it used to be admitted: entry NaT → late False).
        cal = ["20260901", "20260902", "20260903", "20260904"]
        rows = _sealed("20260728", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-10-04 03:00:00.000000")   # 10-04 补录封存
        d, fun = mod.admit(pd.DataFrame(rows), calendar=cal)
        assert fun["excl_seal_t1_unresolved"] == 20
        assert fun["excl_late_seal"] == 20 and len(d) == 0
        assert mod.eval_days(d) == []

    def test_calendar_ends_before_signal_day_excluded(self):
        # 总控点名的泄漏场景②: the calendar stops before the signal day, so
        # T+1 is unresolvable → excluded, never admitted.
        cal = ["20260105", "20260106"]
        rows = _sealed("20260112", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-13 10:00:00.000000")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=cal)
        assert fun["excl_seal_t1_unresolved"] == 20
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_unparseable_signal_date_excluded_via_t1_unresolved(self):
        # a garbage signal_date cannot be resolved to a T+1 either → the
        # same exclusion path, counted with the gate's T+1 failures.
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:00:00.000000")
        for r in rows:
            r["signal_date"] = "not-a-date"
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["excl_seal_t1_unresolved"] == 20
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_weekend_signal_snaps_to_monday(self):
        # signal on Saturday 20260110 (not a calendar day) → T+1 = Monday
        # 20260112; sealed before Monday 01:30 UTC still counts as forward
        cal = ["20260109", "20260112"]
        rows = _sealed("20260110", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-12 01:00:00.000000")
        d, fun = mod.admit(pd.DataFrame(rows), calendar=cal)
        assert fun["excl_late_seal"] == 0 and len(d) == 20

    def test_signal_before_first_calendar_day_is_t1_unresolved(self):
        # DAV-2004 / DAV-1999 review 🟡 M-1: a signal day earlier than the
        # calendar's FIRST day is the same unresolvable-T+1 case as a calendar
        # that ends first — daily_snapshot_ledger._t_plus_n's ``idx < 0``
        # makes it None. Returning calendar[0] here would hand the gate a
        # deadline derived from the wrong trading day; with the DAV-2007 M-2
        # rework the rows are excluded instead of admitted.
        cal = ["20260105", "20260106", "20260107", "20260108"]
        rows = _sealed("20260102", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 10:00:00.000000")
        # the failure mode pinned directly: with calendar[0] returned as
        # "T+1" (deadline 20260105 09:30 CST) this row would be judged late
        assert mod._next_trading_day(cal, "20260102") is None
        d, fun = mod.admit(pd.DataFrame(rows), calendar=cal)
        assert fun["excl_seal_t1_unresolved"] == 20
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_non_ledger_shaped_sealed_at_fails_closed(self):
        # DAV-2004 / DAV-1999 review 🟢 S-3: format="mixed" would guess
        # "20260106" / "01/06/2026" / "2026-1-6" into a UTC-midnight instant
        # and admit a row whose seal time was never established. The ledger's
        # _parse_dt only accepts a leading YYYY-MM-DD, so these must land in
        # seal_unparseable and be excluded.
        for bad in ("20260106", "2026/01/06", "01/06/2026", "2026-1-6"):
            rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                           bad)
            d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
            assert fun["seal_unparseable"] == 20, bad
            assert fun["excl_late_seal"] == 20 and len(d) == 0, bad

    def test_datetime_typed_signal_date_resolves_same_t1_day(self):
        # DAV-2004 / DAV-1999 review 🟢 S-4: admit() is public and tests hand
        # it raw frames, so signal_date need not be the canonical YYYYMMDD
        # string load_frame produces. A datetime column astype(str)s to
        # "2026-01-05 00:00:00", which sorts before every calendar day: before
        # M-1 the whole batch silently snapped onto the calendar's first day,
        # after M-1 every row would become t1-unresolved. Re-normalising keeps
        # the answer identical to the string form (T+1 = 20260106).
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:00:00.000000")
        as_str, fun_str = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun_str["excl_late_seal"] == 0 and len(as_str) == 20
        for r in rows:
            r["signal_date"] = datetime(2026, 1, 5)
        as_dt, fun_dt = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun_dt["excl_seal_t1_unresolved"] == 0
        assert fun_dt["excl_late_seal"] == 0 and len(as_dt) == 20
        assert sorted(as_dt["symbol"]) == sorted(as_str["symbol"])

    def test_frame_without_sealed_at_predates_gate(self):
        # no sealed_at column → gate off, no calendar needed, no exclusion
        rows = _day("20260105", range(1, 21), np.linspace(0, 0.1, 20))
        d, fun = mod.admit(pd.DataFrame(rows))
        assert fun["seal_gate_applied"] is False
        assert fun["excl_late_seal"] == 0
        assert fun["seal_unparseable"] == 0
        assert fun["excl_seal_t1_unresolved"] == 0
        assert len(d) == 20

    def test_sealed_at_without_calendar_fails_closed(self):
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:00:00.000000")
        with pytest.raises(ValueError, match="sealed_at"):
            mod.admit(pd.DataFrame(rows))

    def test_mislabeled_f0_late_seal_excluded(self):
        # label says F0 but the record was sealed after the open: the gate
        # re-derives the rule from sealed_at and drops it
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 02:00:00.000000")
        assert all(r["timing_class"] == "F0" for r in rows)
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_missing_timing_label_late_seal_excluded(self):
        # no timing_class column at all → default F0; the gate is then the
        # only timing protection left in the formal queue
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 02:00:00.000000")
        for r in rows:
            del r["timing_class"]
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        assert fun["excl_late_seal"] == 20 and len(d) == 0

    def test_late_duplicate_never_wins_dedup(self):
        rows = []
        for rid, sealed in [(1, "2026-01-06 01:00:00.000000"),   # forward
                            (2, "2026-01-08 10:00:00.000000")]:  # late
            rows.append({"signal_date": "20260105", "version_key": "v1",
                         "symbol": "S001", "run_id": rid,
                         "created_at": "2026-01-05 09:00:00.000000",
                         "sealed_at": sealed, "q": 0.5, "r": 0.01,
                         "timing_class": "F0", "input_pit_status": "VERIFIED",
                         "completed": True})
        d, fun = mod.admit(pd.DataFrame(rows), calendar=self.CAL)
        # the late run is gone before dedup, so it cannot be elected there
        assert fun["excl_late_seal"] == 1
        assert fun["excl_dup_run"] == 0
        assert len(d) == 1 and d.iloc[0]["run_id"] == 1

    def test_load_frame_keeps_sealed_at_only_column(self):
        # a frame carrying ONLY sealed_at must not lose it to the created_at
        # alias rename; dedup still works and the gate applies
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ip = td / "in.csv"
            pd.DataFrame([
                {"signal_date": "20260105", "symbol": "S1", "prob": 50,
                 "rel_return": 0.01,
                 "sealed_at": "2026-01-06 01:00:00.000000"},
                {"signal_date": "20260105", "symbol": "S2", "prob": 60,
                 "rel_return": 0.02,
                 "sealed_at": "2026-01-07 10:00:00.000000"},
            ]).to_csv(ip, index=False)
            df = mod.load_frame(ip)
            assert "sealed_at" in df.columns
            assert str(df.iloc[0]["created_at"]) == \
                "2026-01-06 01:00:00.000000"
            d, fun = mod.admit(df, calendar=self.CAL)
            assert fun["excl_late_seal"] == 1 and len(d) == 1

    def test_run_reports_gate_in_funnel_and_run_json(self):
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:00:00.000000")
        rows += _sealed("20260106", range(1, 21), np.linspace(0, 0.1, 20),
                        "2026-01-08 10:00:00.000000", start=30)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ip = td / "in.csv"
            pd.DataFrame(rows).to_csv(ip, index=False)
            cp = td / "cal.csv"
            cp.write_text("date\n" + "\n".join(self.CAL) + "\n")
            out = td / "out"
            res = mod.run(ip, out, calendar_path=cp)
            fun = res["verdict"]["funnel"]
            assert fun["seal_gate_applied"] is True
            assert fun["excl_late_seal"] == 20
            assert fun["formal_rows"] == 20
            runj = json.loads((out / "run.json").read_text())
            assert runj["seal_gate_applied"] is True
            assert runj["excl_late_seal"] == 20
            # DAV-2004 / DAV-1999 review 🟢 S-1: run.json is the artefact ops
            # reads first, so the gate's "cannot prove forward sealing"
            # counters travel with it (they used to live only in
            # verdict.funnel, so a run whose T+1 resolution failed wholesale
            # looked clean here).
            assert runj["seal_unparseable"] == fun["seal_unparseable"] == 0
            assert runj["excl_seal_t1_unresolved"] == \
                fun["excl_seal_t1_unresolved"] == 0
            days = pd.read_parquet(out / "m1_day_series.parquet")
            assert list(days["date"]) == ["20260105"]

    def test_run_without_calendar_on_sealed_frame_aborts_before_writing(self):
        rows = _sealed("20260105", range(1, 21), np.linspace(0, 0.1, 20),
                       "2026-01-06 01:00:00.000000")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ip = td / "in.csv"
            pd.DataFrame(rows).to_csv(ip, index=False)
            out = td / "out"
            with pytest.raises(ValueError, match="sealed_at"):
                mod.run(ip, out)
            assert not (out / "run.json").exists()


class TestCalendarFileLoad:
    """DAV-2011 (DAV-2007 review 🟡 M-2): the --calendar file is documented
    as one YYYYMMDD per line (m1_eval --help / scripts/phase2/README.md), so
    a headerless file must load EVERY day. The pre-fix read
    (``pd.read_csv(path, dtype=str).iloc[:, 0]`` with default header=0)
    consumed the first line as a column name and silently dropped the
    calendar's first day — which changes the T+1 resolution and the formal
    queue for boundary signal days. Repo tests all wrote a ``date`` header,
    so the bug was invisible on the test path."""

    CAL = ["20260105", "20260106", "20260107", "20260108"]

    def _cal(self, td: Path, text: str) -> Path:
        cp = td / "cal.txt"
        cp.write_text(text)
        return cp

    def test_no_calendar_returns_none(self):
        assert mod._load_calendar(None) is None

    def test_headerless_calendar_loads_every_day(self):
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td), "\n".join(self.CAL) + "\n")
            assert mod._load_calendar(cp) == self.CAL

    def test_headerless_calendar_without_trailing_newline(self):
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td), "\n".join(self.CAL))
            assert mod._load_calendar(cp) == self.CAL

    def test_headerless_single_day_calendar(self):
        # the pre-fix read turned a one-line calendar into an empty list
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td), "20260105\n")
            assert mod._load_calendar(cp) == ["20260105"]

    def test_date_header_calendar_loads_every_day(self):
        # the repo test convention ("date\n…"): explicit single-header
        # support, not silent header guessing
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td), "date\n" + "\n".join(self.CAL) + "\n")
            assert mod._load_calendar(cp) == self.CAL

    def test_blank_lines_are_cosmetic(self):
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td), "\n20260105\n\n20260106\n\n")
            assert mod._load_calendar(cp) == ["20260105", "20260106"]

    def test_unsorted_calendar_comes_back_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td),
                           "20260108\n20260105\n20260107\n20260106\n")
            assert mod._load_calendar(cp) == self.CAL

    def test_empty_calendar_file_raises_clean_value_error(self):
        # pre-fix: uncaught pandas.errors.EmptyDataError
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td), "")
            with pytest.raises(ValueError, match="no YYYYMMDD"):
                mod._load_calendar(cp)

    def test_header_only_calendar_raises_clean_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td), "date\n")
            with pytest.raises(ValueError, match="no YYYYMMDD"):
                mod._load_calendar(cp)

    def test_non_date_line_fails_closed(self):
        # a calendar day is never silently dropped: fail closed instead
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td), "date\n20260105\nnot-a-date\n")
            with pytest.raises(ValueError, match="not YYYYMMDD"):
                mod._load_calendar(cp)

    def test_eight_digit_non_date_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            cp = self._cal(Path(td), "20260230\n")
            with pytest.raises(ValueError):
                mod._load_calendar(cp)

    def test_run_headerless_calendar_resolves_first_day_t1(self):
        # End-to-end M-2 impact: with a headerless calendar the FIRST day is
        # the boundary the DAV-1991 seal gate resolves T+1 against. Pre-fix
        # the day was eaten, _next_trading_day hit bisect_right == 0 (signal
        # day precedes the calendar) and every row landed in
        # excl_seal_t1_unresolved and was excluded instead of being judged
        # against the 20260106 09:30 Asia/Shanghai deadline — a different
        # formal queue.
        cal = ["20260105", "20260106"]
        rows = [
            # sealed 2026-01-06 01:00 UTC = 09:00 CST → before the open
            {"signal_date": "20260105", "symbol": "S1", "prob": 50,
             "rel_return": 0.01,
             "sealed_at": "2026-01-06 01:00:00.000000"},
            # sealed 2026-01-06 02:00 UTC = 10:00 CST → after the open
            {"signal_date": "20260105", "symbol": "S2", "prob": 60,
             "rel_return": 0.02,
             "sealed_at": "2026-01-06 02:00:00.000000"},
        ]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ip = td / "in.csv"
            pd.DataFrame(rows).to_csv(ip, index=False)
            cp = td / "cal.txt"
            cp.write_text("\n".join(cal) + "\n")
            out = td / "out"
            res = mod.run(ip, out, calendar_path=cp)
            fun = res["verdict"]["funnel"]
            assert fun["seal_gate_applied"] is True
            assert fun["excl_seal_t1_unresolved"] == 0
            assert fun["excl_late_seal"] == 1
            assert fun["formal_rows"] == 1
