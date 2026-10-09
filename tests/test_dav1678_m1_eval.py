"""DAV-1678: unit tests for scripts/phase2/m1_eval.py.

Synthetic constructed data only — no DB, no akshare, no real model. Mirrors
the DAV-1573 signed-off criteria: average-rank ties, equal-weight day IC,
insufficient/rank-degenerate day labels, §3.2 quintile boundary-tie weights,
fixed 10 reliability bins, primary-run dedup by earliest created_at, NW60/120
inference and the A–D grade gate.
"""
from __future__ import annotations

import importlib.util
import sys
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
