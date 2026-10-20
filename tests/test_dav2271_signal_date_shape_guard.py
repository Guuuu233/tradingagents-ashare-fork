r"""DAV-2271 — 复核发现（低）：`signal_date` 日身份边界。

两条发现（均在 `scripts/phase2/m1_base_rate.py`）：

  ① 混合形状 signal_date 面板：同一日历日的两种写法各走一次游走，输出两条
     `(signal_date, l1)` 行（取值相同、字符串不同）；下游按精确字符串取键
     （`base_q_map` → `paired_increment` 的 ISO 键）会静默命中其中之一。
  ② 非标准 signal_date（如 `'2026-9-1'`）：旧实现抛成 9M 行管线深处的裸
     `ValueError`，没有分区文件名与样本值 —— 方向（硬失败）可取，缺可定位
     信息。

本卡处置（复核建议 1 的备选 + 建议 2 + 建议 3）：

  * **引擎入口拒绝混合网格**，而不是按 `day_key` 去重：面板日字符串对外原样
    发出（不重写），消费侧的键是精确字符串，去重只会把歧义下移一层 ——
    留下的写法不一定是下游查的写法，静默空配。同一天双写法的面板在入口
    即报错，并点名是哪些日历日。
  * `load_panel` 在**分区边界**做形状校验，报错带分区文件路径与样本值；
    只有进入面板的行被校验（已按成熟度/标签口径剔除的行与面板无关）。
  * 单形状面板（`YYYYMMDD` / `YYYY-MM-DD` 各自）取值不变；`mat` 的既有
    容忍度不变（不可读成熟日只是离开成熟集，不是日网格）。

DAV-2280 复审后的加固（本文件追加的用例段）：

  * 🟢-1 形状正则必须是 **ASCII** 字符类：Python 的 `\d` 匹配 Unicode 十进制
    数字，全角日期 `'２０２４０１０２'` 能同时穿过两道守卫**并**被 `day_key`
    接受（`int()` 收它），等于凭空造出一个没人写过的日键。
  * 🟢-2 形状校验只扫**去重后的日值**（面板只有 ~2.5k 个不同信号日，却
    重复在 920 万行上）；违规**行数**仍在报错里给出，但只在出错路径上算。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "phase2"))
import m1_base_rate as m1  # noqa: E402
import p_rel_t10_baseline_eval as P  # noqa: E402

L1A, L1B = "801010.SI", "801020.SI"

# 只读冻结 P1 T+10 面板（不在本机的机器上跳过）。
FROZEN_T10_CHUNKS = Path(
    "/Users/davidliu/Documents/TradingAgents-AShare-cache/phase2/"
    "p1-base_t10-frozen/chunks")


def _panel(rows) -> pd.DataFrame:
    """rows: (signal_date, mat, l1, y) -> m1_base_rate panel schema."""
    return pd.DataFrame(
        [{"signal_date": s, "mat": mt, "l1": l1, "y": float(y)}
         for s, mt, l1, y in rows])


def _cell(df: pd.DataFrame, T: str, l1: str):
    sub = df[(df["signal_date"] == T) & (df["l1"] == l1)]
    assert len(sub) == 1, (T, l1, len(sub))
    return sub.iloc[0]


# 复核实测面板：3 行 / 3 个日-行业键，但 `20240102` 有两种写法
MIXED_ROWS = [
    ("20240102", "20240102", L1A, 1),
    ("2024-01-02", "20240102", L1A, 1),
    ("20240103", "20240103", L1A, 0),
]


# ---------------------------------------------------------------------------
# ① 混合形状 signal_date：入口拒绝，不再产出同日重复行
# ---------------------------------------------------------------------------

class TestMixedShapeSignalDateRejected:
    def test_same_day_in_both_shapes_is_rejected_not_doubled(self):
        """旧行为：6 行 / 3 个日-行业键（`20240102` 与 `2024-01-02` 各一份）。"""
        with pytest.raises(ValueError) as exc:
            m1.build_pit_base_rates(_panel(MIXED_ROWS), shrink_ms=(0,),
                                    window_days=(0,))
        msg = str(exc.value)
        assert "mix" in msg and "ONE shape" in msg
        assert "20240102" in msg                  # 点名被双写的日历日
        # 反证：同一批行的同形状面板是干净的 2 行 / 2 个日-行业键
        # （说明拒绝的只有“同日双写法”，不是这批数据本身）
        good, _ = m1.build_pit_base_rates(
            _panel([r for r in MIXED_ROWS if "-" not in r[0]]),
            shrink_ms=(0,), window_days=(0,))
        assert len(good) == 2
        assert len({(m1.day_key(d), l1) for d, l1 in
                    zip(good["signal_date"], good["l1"])}) == 2
        # 消费侧（base_q_map → paired_increment 的精确字符串键）同样在入口
        # 拒绝，不会静默命中其中一种写法
        with pytest.raises(ValueError):
            P.base_q_map(_panel(MIXED_ROWS))

    def test_mixed_grid_without_a_duplicated_day_is_also_rejected(self):
        """不同日各用一种写法、没有任何一日双写 —— 仍是混合网格，同样拒绝。"""
        rows = [("20240102", "20240102", L1A, 1),
                ("2024-01-03", "20240103", L1A, 0)]
        with pytest.raises(ValueError) as exc:
            m1.build_pit_base_rates(_panel(rows), shrink_ms=(0,),
                                    window_days=(0,))
        msg = str(exc.value)
        assert "no calendar day is spelled twice" in msg
        # 查询日集合（DAV-2240）也按同一形状契约把门：第三种写法在门外报错，
        # 不再从游走中的 `day_key` 抛裸错，且报的是调用方原样传入的值
        # （不会被 normalise_days 的渲染改写成 '202691'）
        with pytest.raises(ValueError) as exc2:
            m1.build_pit_base_rates(
                _panel([("20240102", "20240102", L1A, 1)]),
                shrink_ms=(0,), window_days=(0,), query_days=["2026-9-1"])
        assert "query_days" in str(exc2.value)
        assert "'2026-9-1'" in str(exc2.value)

    def test_uniform_panels_in_either_shape_keep_identical_values(self):
        """不误伤：两种写法各自单形状时，日键与取值逐值一致。"""
        rows = [("2024-01-02", "2024-01-02", L1A, 1),
                ("2024-01-03", "2024-01-03", L1A, 0),
                ("2024-01-03", "2024-01-03", L1B, 1)]
        iso = _panel(rows)
        ymd = _panel([(s.replace("-", ""), mt.replace("-", ""), l1, y)
                      for s, mt, l1, y in rows])
        for W in (0, 250):
            a, _ = m1.build_pit_base_rates(iso, shrink_ms=(200,),
                                           window_days=(W,))
            b, _ = m1.build_pit_base_rates(ymd, shrink_ms=(200,),
                                           window_days=(W,))
            assert len(a) == len(b) == 4      # 2 日 × 2 行业
            assert [(m1.day_key(d), l1) for d, l1 in
                    zip(a["signal_date"], a["l1"])] == \
                [(m1.day_key(d), l1) for d, l1 in
                 zip(b["signal_date"], b["l1"])]
            assert a["n"].tolist() == b["n"].tolist()
            assert a["p_m200"].fillna(-1).tolist() == \
                b["p_m200"].fillna(-1).tolist()


# ---------------------------------------------------------------------------
# ② 非标准 signal_date：在分区边界报错，带文件与样本值
# ---------------------------------------------------------------------------

def _row(signal_date, mat="20240131", l1=L1A, y=1.0,
         status=m1.EVAL_OK, universe=True) -> dict:
    """One P1 chunk row (the six columns `load_panel` reads)."""
    return {"signal_date": signal_date, "sw_l1_code": l1,
            "actual_exit_date": mat, "outcome_status": status,
            "universe_ok": universe, "y_rel": y}


def _write_chunks(root: Path, year: int, rows) -> Path:
    d = root / f"year={year}"
    d.mkdir(parents=True, exist_ok=True)
    pq = d / "data.parquet"
    pd.DataFrame(rows).to_parquet(pq, index=False)
    return pq


class TestLoadPanelDayGridGuard:
    def test_non_standard_signal_date_names_the_partition(self, tmp_path):
        pq = _write_chunks(tmp_path / "chunks", 2026,
                           [_row("2026-9-1"), _row("20260102")])
        with pytest.raises(ValueError) as exc:
            m1.load_panel(tmp_path / "chunks")
        msg = str(exc.value)
        assert str(pq) in msg            # 分区文件路径（旧报错完全没有）
        assert "'2026-9-1'" in msg       # 样本值
        assert "YYYYMMDD" in msg and "YYYY-MM-DD" in msg

    def test_rows_already_dropped_are_not_shape_checked(self, tmp_path):
        """只有进入面板的行被校验：未成熟 / 缺标签 / 缺成熟日的行与面板无关。"""
        _write_chunks(tmp_path / "chunks", 2026, [
            _row("20260102"),
            _row("2026-9-1", status="pending_due"),   # 未成熟 -> 剔除
            _row("2026-9-2", y=None),                 # 标签缺失 -> 剔除
            _row("2026-9-3", mat=None),               # 成熟日缺失 -> 剔除
        ])
        panel = m1.load_panel(tmp_path / "chunks")
        assert panel["signal_date"].tolist() == ["20260102"]

    def test_standard_shapes_load_verbatim_mixed_grid_stops_at_the_engine(
            self, tmp_path):
        """load_panel 不改写日字符串；跨分区混合形状由引擎入口拒绝。

        （`build_base_panel` 走 `load_panel` 后统一渲染 ISO，是本口径下的
        正规化调用方。）
        """
        _write_chunks(tmp_path / "chunks", 2025,
                      [_row("20251231", mat="20251231")])
        _write_chunks(tmp_path / "chunks", 2026,
                      [_row("2026-01-05", mat="20260105")])
        panel = m1.load_panel(tmp_path / "chunks")
        assert set(panel["signal_date"]) == {"20251231", "2026-01-05"}
        with pytest.raises(ValueError, match="ONE shape"):
            m1.build_pit_base_rates(panel, shrink_ms=(0,), window_days=(0,))

    def test_unreadable_mat_keeps_its_documented_tolerance(self):
        """`mat` 不对称是刻意的：不可读成熟日只是离开成熟集，不报错。"""
        panel = _panel([("20240102", "20240102", L1A, 1),
                        ("20240102", "not-a-day", L1A, 0)])
        df, _ = m1.build_pit_base_rates(panel, shrink_ms=(0,),
                                        window_days=(0,))
        assert int(_cell(df, "20240102", L1A)["n"]) == 1


# ---------------------------------------------------------------------------
# DAV-2280 🟢-1：形状字符类必须是 ASCII（`\d` 会匹配 Unicode 十进制数字）
# ---------------------------------------------------------------------------

# 全角 / 阿拉伯-Indic 数字：Python `re` 的 `\d` 匹配它们，`int()` 也收
FULLWIDTH = "２０２４０１０２"
ARABIC_INDIC = "٢٠٢٤٠١٠٢"


class TestAsciiOnlyDayShape:
    def test_unicode_digits_are_rejected_by_the_value_guard(self):
        for bad in (FULLWIDTH, ARABIC_INDIC):
            with pytest.raises(ValueError) as exc:
                m1._check_day_values(pd.Series([bad]), "probe")
            assert repr(bad) in str(exc.value)
            # 单形状守卫只管“两种形状混排”，不重复拦非标准值；
            # 引擎入口两道守卫都跑，非标准值必被拦下（见下一条）
            m1._check_single_day_shape(pd.Series([bad]), "probe")

    def test_unicode_digit_panel_is_rejected_at_the_engine_entry(self):
        """反证：改前（Unicode 数字类）这道门是空的 —— 全角日期能穿过去。"""
        # `day_key` 本身也收它（`int()` 接受 Unicode 数字），所以只有守卫
        # 能挡住；这里断言守卫确实挡住了。
        assert m1.day_key(FULLWIDTH) == 20240102      # 旧行为：被接受
        panel = _panel([(FULLWIDTH, "20240102", L1A, 1)])
        with pytest.raises(ValueError) as exc:
            m1.build_pit_base_rates(panel, shrink_ms=(0,), window_days=(0,))
        assert "YYYYMMDD" in str(exc.value)

    def test_unicode_digit_mat_leaves_the_matured_set(self):
        """`mat` 的成熟集过滤同样只认 ASCII 八位数字。"""
        panel = _panel([("20240102", "20240102", L1A, 1),
                        ("20240102", FULLWIDTH, L1A, 0)])
        df, _ = m1.build_pit_base_rates(panel, shrink_ms=(0,),
                                        window_days=(0,))
        assert int(_cell(df, "20240102", L1A)["n"]) == 1


# ---------------------------------------------------------------------------
# DAV-2280 🟢-2：形状校验只扫去重后的日值，但报错仍给违规行数
# ---------------------------------------------------------------------------

class TestDistinctValueScan:
    def test_bad_value_reports_row_count_and_distinct_count(self):
        days = pd.Series(["20240102"] * 5 + ["2026-9-1"] * 3)
        with pytest.raises(ValueError) as exc:
            m1._check_day_values(days, "probe")
        msg = str(exc.value)
        assert "3 row(s)" in msg
        assert "1 distinct day" in msg
        assert "'2026-9-1'" in msg

    def test_repeated_good_days_are_accepted(self):
        """同一形状的重复日值不报错（去重扫描不改变判定）。"""
        m1._check_day_values(pd.Series(["20240102"] * 1000), "probe")
        m1._check_single_day_shape(pd.Series(["20240102"] * 1000), "probe")

    def test_empty_day_grid_is_accepted(self):
        m1._check_day_values(pd.Series([], dtype="object"), "probe")
        m1._check_single_day_shape(pd.Series([], dtype="object"), "probe")


# ---------------------------------------------------------------------------
# 冻结面板自证：新校验在真实数据上不可能触发（只读，缺件跳过）
# ---------------------------------------------------------------------------

class TestFrozenPanelIsWithinTheDayContract:
    @pytest.mark.skipif(not FROZEN_T10_CHUNKS.is_dir(),
                        reason="frozen P1 T+10 chunks not on this workstation")
    def test_frozen_signal_days_are_one_standard_shape(self):
        shapes: set[str] = set()
        days: set[str] = set()
        for pq in sorted(FROZEN_T10_CHUNKS.glob("year=*/data.parquet")):
            s = pd.read_parquet(pq, columns=["signal_date"])["signal_date"]
            m1._check_day_values(s, str(pq))      # 全部标准形状
            m1._check_single_day_shape(s, str(pq))
            sv = s.astype(str)
            shapes |= {"dashed" if "-" in v else "digits" for v in sv}
            days |= set(sv)
        assert shapes == {"digits"}               # 分区原始写法 = YYYYMMDD
        assert len({m1.day_key(d) for d in days}) == len(days)
