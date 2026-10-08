"""DAV-1677 — M1 base-rate module PIT / shrinkage unit tests (synthetic panel).

Covers the four ruled cases:
  1. a label maturing on T+1 does NOT move the T-day base rate;
  2. a label maturing exactly on T DOES count in the T-day base rate;
  3. with W>0, a label matured beyond the trailing window is evicted;
  4. m=0 yields the industry's raw matured win-rate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "phase2"))
import m1_base_rate as m1  # noqa: E402


def _panel(rows):
    """rows: list of (signal_date, mat, l1, y) -> module panel schema."""
    return pd.DataFrame(
        [{"signal_date": s, "mat": mt, "l1": l, "y": float(y)}
         for s, mt, l, y in rows])


def _cell(df, T, l1):
    sub = df[(df["signal_date"] == T) & (df["l1"] == l1)]
    assert len(sub) == 1, (T, l1, len(sub))
    return sub.iloc[0]


# ---------------------------------------------------------------------------
# 1) label maturing at T+1 must NOT affect the T-day base rate
# ---------------------------------------------------------------------------
def test_label_maturing_next_day_does_not_leak_into_t():
    rows = [
        # two A labels matured strictly before/at T0 (win, loss) -> p_raw = 0.5
        ("20240101", "20231220", "A", 1),
        ("20240101", "20231225", "A", 0),
        # this label matures on the NEXT signal day (T+1), not T0
        ("20240101", "20240102", "A", 1),
        # a T+1 signal row so that signal day exists in the grid
        ("20240102", "20231228", "B", 1),
    ]
    panel = _panel(rows)
    df, _ = m1.build_pit_base_rates(panel, shrink_ms=(0,), window_days=(0,))
    cell = _cell(df, "20240101", "A")
    # only the two already-matured labels count on T0
    assert cell["n"] == 2
    assert cell["p_m0"] == pytest.approx(0.5)
    # on the next signal day the third label becomes visible
    nxt = _cell(df, "20240102", "A")
    assert nxt["n"] == 3
    assert nxt["p_m0"] == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# 2) label maturing exactly on T counts in the T-day base rate
# ---------------------------------------------------------------------------
def test_label_maturing_on_t_counts():
    rows = [
        ("20240101", "20240101", "A", 1),   # matures ON T0
        ("20240101", "20231230", "A", 0),   # matured earlier
    ]
    panel = _panel(rows)
    df, _ = m1.build_pit_base_rates(panel, shrink_ms=(0,), window_days=(0,))
    cell = _cell(df, "20240101", "A")
    assert cell["n"] == 2                     # both counted
    assert cell["p_m0"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 3) with W>0 a label matured outside the trailing window is evicted
# ---------------------------------------------------------------------------
def test_rolling_window_evicts_old_labels():
    # T0 label matured 2020-01-01; by T=2024-06-01 a 365-day window must drop it
    rows = [
        ("20200102", "20200101", "A", 1),   # matured 2020-01-01
        ("20240601", "20240520", "A", 0),   # matured inside window of T=2024-06-01
    ]
    panel = _panel(rows)
    df, _ = m1.build_pit_base_rates(panel, shrink_ms=(0,), window_days=(365,))
    # on 2020-01-02 the first label is inside its own window
    c0 = _cell(df, "20200102", "A")
    assert c0["n"] == 1 and c0["p_m0"] == pytest.approx(1.0)
    # on 2024-06-01 the 2020 label is evicted; only the 2024-05-20 label counts
    c1 = _cell(df, "20240601", "A")
    assert c1["n"] == 1 and c1["p_m0"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 4) m=0 returns the industry's raw matured win-rate (no shrinkage)
# ---------------------------------------------------------------------------
def test_m_zero_equals_raw_win_rate():
    rows = []
    # industry A: 3 wins / 4 matured -> raw 0.75 ; market differs via B
    for mt, y in [("20231210", 1), ("20231211", 1), ("20231212", 1),
                  ("20231213", 0)]:
        rows.append(("20240101", mt, "A", y))
    for mt, y in [("20231210", 0), ("20231211", 0)]:
        rows.append(("20240101", mt, "B", y))
    panel = _panel(rows)
    df, _ = m1.build_pit_base_rates(panel, shrink_ms=(0, 200), window_days=(0,))
    a = _cell(df, "20240101", "A")
    assert a["p_m0"] == pytest.approx(0.75)
    # market prior = (3+0)/(4+2) = 0.5 ; m=200 pulls 0.75 toward 0.5
    expected_shrunk = (3 + 200 * 0.5) / (4 + 200)
    assert a["p_m200"] == pytest.approx(expected_shrunk)
    assert a["p_m200"] < a["p_m0"]            # pulled down toward market
