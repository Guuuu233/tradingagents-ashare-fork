"""DAV-2242 — 评估入口 peak footprint：基率面板日期归一化的**数值零漂移**验收。

本卡只改内存布局，不改口径：冻结来源（`p1-base_t10-frozen/`）、m200/W0
主基准、敏感性网格三样都不动。因此验收必须是「改前 == 改后」逐值相同，
而不是「看起来差不多」。

**实测高水位的真实来源**（本卡定位结论，非猜测）：评估入口的 peak
footprint 由 `build_base_panel` 独占 —— 9,244,318 行面板上，日期归一化原
实现按**行**做四段字符串拼接：

    sd.str[:4] + "-" + sd.str[4:6] + "-" + sd.str[6:8]

每一步都新建一列对象数组（9.24M 个 Python `str`），链式求值时中间列全部
同时存活，单列瞬时 3.55 GiB、峰值 4.80 GiB，而面板本身驻留只有 ~1.9 GiB。
日期只有 2,481 个**不同值**，改为「先 factorize、只归一化不同值、再按
int 位置回填」后同一列只保留 2,481 个对象、峰值 2.00 GiB。

四个层次：

1. **函数级**：逐行表达式 vs 去重值实现，同一输入逐值相同（含空值分支，
   历史表示为 `"nan--"` / `"None--"`，绝不能因为「空值不重要」而改掉）；
2. **面板级**：真实 `build_base_panel` 的输出与改前逐行表达式逐值相同；
3. **引擎级**：两条帧喂同一个 `build_pit_base_rates`，主基准 m200/W0 与
   敏感性网格的每个格子逐值（含 NaN 位置）相同 —— 日期键、扩窗口径、
   链式位移都没有被内存改动碰到；
4. **资源级**：同一列新实现持有的对象数必须等于**不同值个数**，而不是
   行数（这条防的是「零漂移」被用来夹带无效改动：若新实现仍每行一个
   对象，本卡的实测收益就不成立，测试必须失败）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "phase2"))
import m1_base_rate as m1
import p_rel_t10_baseline_eval as P

EVAL_OK = "evaluated_ok"


def _legacy_iso(series: pd.Series) -> np.ndarray:
    """DAV-2242 改前的逐行归一化表达式，逐字保留作为零漂移比对基准。

    故意保持与改前代码一字不差（含四段 `+` 的链式求值），否则「改前 ==
    改后」就成了拿新实现跟自己比。
    """
    sd = series.astype(str).str.replace("-", "", regex=False)
    return (sd.str[:4] + "-" + sd.str[4:6] + "-" + sd.str[6:8]
            ).to_numpy(dtype=object)


def _assert_same_values(old: np.ndarray, new: np.ndarray) -> None:
    assert len(old) == len(new)
    assert np.array_equal(old, new)


# --------------------------------------------------------------------------
# 1. 函数级
# --------------------------------------------------------------------------
class TestIsoDaysByUniqueValue:
    @pytest.mark.parametrize("values", [
        ["20260105", "2026-01-05", "20260106", "", "20260105"],
        ["20251231", "2025-12-31", "20251231"],
        ["20151008", "20251231"],                  # 冻结面板的首末信号日
        [],                                        # 空帧分支
        [" 20260105 "],                            # 绝不"顺手 trim"
    ])
    def test_matches_the_row_wise_expression(self, values):
        series = pd.Series(values, dtype=object)
        _assert_same_values(_legacy_iso(series),
                            P._iso_days_by_unique_value(series))

    def test_missing_values_keep_their_historical_rendering(self):
        """`NaN` 与 `None` 在旧表达式下渲染不同（`nan--` / `None--`），
        新实现必须逐行保留这一差异，不能折叠成一个共享值。"""
        series = pd.Series([np.nan, None, "20260105", "2026-01-05"],
                           dtype=object)
        got = P._iso_days_by_unique_value(series)
        _assert_same_values(_legacy_iso(series), got)
        assert list(got[:2]) == ["nan--", "None--"]

    def test_all_missing_column(self):
        series = pd.Series([None, None, None], dtype=object)
        _assert_same_values(_legacy_iso(series),
                            P._iso_days_by_unique_value(series))


# --------------------------------------------------------------------------
# 2. 面板级
# --------------------------------------------------------------------------
def _prow(ts_code, signal_date, l1, y, exit_date,
          status=EVAL_OK, universe=True):
    return {"ts_code": ts_code, "signal_date": signal_date,
            "sw_l1_code": l1, "actual_exit_date": exit_date,
            "outcome_status": status, "universe_ok": universe, "y_rel": y}


def _write(chunks: Path, rows) -> None:
    df = pd.DataFrame(rows)
    df["year"] = df["signal_date"].astype(str).str[:4]
    for year, g in df.groupby("year"):
        d = chunks / f"year={year}"
        d.mkdir(parents=True, exist_ok=True)
        g.drop(columns="year").to_parquet(d / "data.parquet", index=False)


# Every branch the loader has: unsorted years, dashed + digit-only day shapes,
# an unassigned industry, an out-of-universe row, a non-evaluated status, a
# null y_rel and a malformed exit day.
PARTITIONS = {
    2023: [
        _prow("A", "20230105", "801020.SI", 1.0, "20230119"),
        _prow("B", "20230105", "", 0.0, "20230119"),
        _prow("C", "20230105", "801010.SI", 1.0, "20230119"),
        _prow("D", "20230106", "801010.SI", 0.0, "20230120"),
        _prow("E", "20230106", "801010.SI", 1.0, "20230120",
              status="pending_due"),
        _prow("F", "20230106", "801010.SI", 1.0, "20230120", universe=False),
        _prow("G", "20230106", "801010.SI", None, "20230120"),
        _prow("H", "20230106", "801010.SI", 1.0, None),
        _prow("I", "20230106", "801010.SI", 1.0, "2023-01-20"),
    ],
    2024: [
        _prow("J", "2024-03-01", "801030.SI", 1.0, "2024-03-15"),
        _prow("K", "2024-03-01", "801030.SI", 0.0, "2024-03-15"),
        _prow("L", "2024-03-04", "801010.SI", 1.0, "2024-03-18"),
    ],
}


class TestBuildBasePanelZeroDrift:
    def _chunks(self, tmp_path):
        chunks = tmp_path / "chunks"
        _write(chunks, [r for rows in PARTITIONS.values() for r in rows])
        return chunks

    def test_dates_match_the_old_normalisation_value_for_value(self, tmp_path):
        raw = m1.load_panel(self._chunks(tmp_path))
        panel = P.build_base_panel(self._chunks(tmp_path))

        assert len(panel) == 4            # 4 of the 13 fixture rows are realised
        # the dashed-date partition rows (2024-03-*) are dropped by the
        # pre-existing 8-char `actual_exit_date` matured filter — unchanged
        # behaviour, asserted here so the fixture cannot silently drift
        assert not any(str(d).startswith("2024") for d in panel["signal_date"])
        for col in ("signal_date", "mat"):
            _assert_same_values(_legacy_iso(raw[col]), panel[col].to_numpy(
                dtype=object))
        # untouched columns keep the loader's own values
        assert list(panel["l1"]) == list(raw["l1"])
        assert list(panel["y"]) == list(raw["y"])
        # an unassigned industry stays the empty string (feeds market prior)
        assert (panel["l1"] == "").sum() == 1
        # the malformed dashed exit day is excluded by the pre-existing
        # 8-char matured filter, never normalised into a panel row
        assert len(panel) == 4 and "2023-01-20" in set(panel["mat"])

    def test_empty_panel_is_returned_unharmed(self, tmp_path):
        chunks = tmp_path / "chunks" / "year=2026"
        chunks.mkdir(parents=True)
        pd.DataFrame([], columns=["signal_date", "sw_l1_code",
                                  "actual_exit_date", "outcome_status",
                                  "universe_ok", "y_rel"]).to_parquet(
            chunks / "data.parquet", index=False)
        panel = P.build_base_panel(tmp_path / "chunks")
        assert panel.empty
        assert list(panel.columns) == ["signal_date", "mat", "l1", "y"]


# --------------------------------------------------------------------------
# 3. 引擎级
# --------------------------------------------------------------------------
class TestEngineZeroDrift:
    def _panels(self, tmp_path):
        chunks = tmp_path / "chunks"
        rows = []
        rng = np.random.default_rng(2242)
        for day in pd.bdate_range("2026-01-05", periods=6).strftime("%Y%m%d"):
            for l1 in ("801010.SI", "801020.SI", "801030.SI", ""):
                for k in range(12):
                    rows.append(_prow(f"S{k}", day, l1,
                                      1.0 if rng.random() < 0.4 else 0.0,
                                      exit_date=day))
        _write(chunks, rows)
        raw = m1.load_panel(chunks)               # loader output, dates as-is
        legacy = raw.copy()
        for col in ("signal_date", "mat"):
            legacy[col] = _legacy_iso(legacy[col])
        return P.build_base_panel(chunks), legacy

    def test_primary_and_sensitivity_cells_are_identical(self, tmp_path):
        new, legacy = self._panels(tmp_path)
        for col in new.columns:
            _assert_same_values(legacy[col].to_numpy(dtype=object),
                                new[col].to_numpy(dtype=object))

        kw = {"shrink_ms": m1.DEFAULT_SHRINK_M,
              "window_days": m1.DEFAULT_WINDOW_DAYS, "min_cell_n": 1}
        a, _ = P.build_pit_base_rates(new, **kw)
        b, _ = P.build_pit_base_rates(legacy, **kw)
        assert len(a) == len(b) > 0
        for col in a.columns:
            np.testing.assert_array_equal(a[col].to_numpy(dtype=object),
                                          b[col].to_numpy(dtype=object))

        # the fixed primary benchmark (m200/W0) is what the report reads,
        # including for a query day after the panel's last signal day
        for query in (None, ["2026-02-02"]):
            qa = P.base_q_map(new, m=200, W=0, query_days=query)
            qb = P.base_q_map(legacy, m=200, W=0, query_days=query)
            assert set(qa) == set(qb)
            for key in qa:
                x, y = qa[key], qb[key]
                assert (x == y) or (np.isnan(x) and np.isnan(y)), key


# --------------------------------------------------------------------------
# 4. 资源级
# --------------------------------------------------------------------------
class TestResourceRegression:
    def test_distinct_objects_per_column_equals_distinct_days(self):
        """The measured reason for the change: objects per DISTINCT value.

        Before: the chained concat left one fresh Python `str` per row
        (9.24M objects on the frozen panel). After: one per distinct day
        (2,481). If this ever regresses to per-row objects the card's
        measured win is gone, so the assertion is on object identity, not on
        `memory_usage(deep=True)` — the latter counts every row's string and
        reports nearly the same size for a frame whose cells share objects.
        """
        days = pd.bdate_range("2026-01-05", periods=40).strftime("%Y%m%d")
        series = pd.Series(np.repeat(days.to_numpy(), 500), dtype=object)
        assert len(series) == 20000

        old = _legacy_iso(series)
        new = P._iso_days_by_unique_value(series)

        old_objects = len({id(v) for v in old})
        new_objects = len({id(v) for v in new})
        _assert_same_values(old, new)          # zero drift comes first
        assert old_objects == len(series)      # 20,000 live strings
        assert new_objects == len(days)        # 40 live strings
        assert new_objects * 100 < old_objects
