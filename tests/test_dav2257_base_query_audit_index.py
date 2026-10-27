"""DAV-2257: audit counts keep their values without query-by-label scans."""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "phase2"))
import p_rel_t10_baseline_eval as P  # noqa: E402


def _panel(rows):
    return pd.DataFrame(
        [("2025-01-01", mat, l1, 1.0) for mat, l1 in rows],
        columns=["signal_date", "mat", "l1", "y"],
    )


def _scalar_counts(panel, days):
    """Independent row-wise oracle for the pre-optimisation maturity rule."""
    out = {}
    for day in days:
        counts = Counter()
        market = 0
        for mat, l1 in zip(panel["mat"].astype(str), panel["l1"].astype(str)):
            token = mat.strip().replace("-", "")
            if re.fullmatch(r"\d{8}", token) and int(token) <= P.day_key(day):
                market += 1
                if l1 != "":
                    counts[l1] += 1
        out[day] = {"by_l1": dict(counts), "market": market}
    return out


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("dashed", [False, True])
def test_all_maturity_fields_equal_scalar_oracle(seed, dashed):
    rng = np.random.default_rng(seed)
    mats = ["2025-01-02", "20250103", " 2025-01-06 ", "2025-02-01",
            "2026-01-01", "", None, np.nan, "bad", "2025/01/03"]
    industries = ["A", "B", "C", "", None, np.nan, 7]
    panel = _panel([(mats[int(rng.integers(len(mats)))],
                     industries[int(rng.integers(len(industries)))])
                    for _ in range(300)])
    if not dashed:
        panel["signal_date"] = panel["signal_date"].str.replace("-", "")
    # Duplicate row indexes must not drop or multiply labels during aggregation.
    panel.index = np.arange(len(panel)) // 2
    raw_days = ["20250101", "2025-01-02", "20250103", "2025-01-05",
                "2025-01-06", "2025-01-31", "2026-01-01", "20250103"]
    days = P.normalise_days(raw_days, str(panel["signal_date"].iloc[0]))
    expected = _scalar_counts(panel, days)
    out = P.base_query_audit(panel, raw_days, qmap={})
    last = expected[days[-1]]
    same = all(row == last for row in expected.values())
    assert out["formal_signal_days"] == len(days)
    assert out["range"] == [days[0], days[-1]]
    assert out["matured_as_of"] == days[-1]
    assert out["matured_market"] == last["market"]
    assert out["matured_by_l1"] == last["by_l1"]
    assert out["matured_by_l1_same_all_days"] is same
    assert out["matured_by_l1_per_day"] == (
        None if same else {day: row["by_l1"] for day, row in expected.items()})


def test_only_unassigned_labels_change_the_market_and_day_equality():
    panel = _panel([("2025-01-02", "A"), ("2025-01-03", ""),
                    ("2025-01-04", "")])
    days = ["2025-01-02", "2025-01-03", "2025-01-04"]
    out = P.base_query_audit(panel, days, qmap={})
    assert out["matured_market"] == 3
    assert out["matured_by_l1"] == {"A": 1}
    assert out["matured_by_l1_same_all_days"] is False
    assert out["matured_by_l1_per_day"] == {day: {"A": 1} for day in days}


@pytest.mark.parametrize("rows", [
    [("", "A"), (None, "B"), ("not a day", "")],
    [("2026-01-01", "A"), ("2026-01-02", "")],
])
def test_no_valid_matured_labels_is_an_empty_count_not_a_missing_query(rows):
    panel = _panel(rows)
    days = ["2025-01-01", "2025-01-02"]
    out = P.base_query_audit(panel, days, qmap={})
    assert out["formal_signal_days"] == 2
    assert out["matured_market"] == 0
    assert out["matured_by_l1"] == {}
    assert out["matured_by_l1_same_all_days"] is True
    assert out["matured_by_l1_per_day"] is None


def test_counts_and_missing_cells_remain_independent_of_m_and_window():
    panel = _panel([("2024-01-01", "A"), ("2025-01-02", "A"),
                    ("2025-01-02", "B"), ("2025-01-03", "")])
    days = ["2025-01-02", "2025-01-03"]
    qmap = {(days[0], "A"): 0.5, (days[0], "B"): float("nan"),
            (days[1], "A"): float("inf"), (days[1], "B"): 0.0,
            ("2024-01-01", "A"): 0.8}
    out = P.base_query_audit(panel, days, m=50, W=250, qmap=qmap)
    assert out["cells"] == 4
    assert out["keys"] == 2
    assert out["missing_cells"] == 2
    assert out["keys_by_day"] == {days[0]: 1, days[1]: 1}
    assert out["matured_market"] == 4
    assert out["matured_by_l1"] == {"A": 2, "B": 1}
    assert out["matured_by_l1_per_day"] == {
        day: {"A": 2, "B": 1} for day in days}


@pytest.mark.parametrize("empty_panel,days", [(True, []), (True, ["20250102"]),
                                               (False, [])])
def test_empty_inputs_keep_the_existing_output_contract(empty_panel, days):
    panel = _panel([] if empty_panel else [("2025-01-02", "A")])
    out = P.base_query_audit(panel, days, qmap={})
    assert out == {
        "formal_signal_days": len(days),
        "cells": 0, "keys": 0, "missing_cells": 0,
        "keys_by_day": {}, "matured_by_l1": {}, "matured_market": 0,
        "matured_by_l1_same_all_days": True,
        "matured_by_l1_per_day": None,
        "reason": ("no --base-chunks: 基率来源缺失（D-075 增量不可判定）"
                   if empty_panel else "无正式信号日"),
    }


@pytest.mark.parametrize("rows_per_day", [10, 1000])
def test_maturity_comparisons_do_not_grow_with_labels_times_queries(
        monkeypatch, rows_per_day):
    """Count actual date comparisons instead of asserting wall-clock timing."""
    comparisons = []
    original_day_key = P.day_key

    class CountedDay:
        def __init__(self, value):
            self.value = value

        def __lt__(self, other):
            comparisons.append(1)
            return self.value < other.value

        def __le__(self, other):
            comparisons.append(1)
            return self.value <= other.value

    monkeypatch.setattr(P, "day_key", lambda day: CountedDay(original_day_key(day)))
    mats = ["2025-01-02", "2025-01-15", "2025-03-01"]
    panel = _panel([(mat, "A") for mat in mats for _ in range(rows_per_day)])
    days = pd.bdate_range("2025-01-01", periods=60).strftime("%Y-%m-%d").tolist()
    out = P.base_query_audit(panel, days, qmap={})
    assert out["matured_market"] == len(panel)
    assert out["matured_by_l1"] == {"A": len(panel)}
    # Sorting the query grid and searching each DISTINCT maturity day is
    # bounded independently of how many labels repeat those maturity days.
    bound = (len(days) + len(mats)) * (math.ceil(math.log2(len(days))) + 1)
    assert len(comparisons) <= bound
