"""DAV-2240 — 基率引擎接收查询日期集合（冻结 P1 面板不变；无前视）。

口径（冻结计划 §2.4，D-075 主基准 m200/W0）：正式评价的每个信号日 T 用
**同一冻结 P1 全市场 T+10 面板**、按 `actual_exit_date <= T` 已成熟标签的
扩窗（W0）计算 `q_base`；面板不替换、不扩充、不重算，不引入未登记数据。
四条验收用例：

  1. 面板之后的查询日能得到基率，且 == 截至该日成熟标签手算的扩窗值；
  2. 退出日晚于查询日的标签不计入（无前视）；
  3. 面板内日期的结果与改动前逐值相同；
  4. 缺行业 / 缺历史仍按原有判缺失口径（NaN + `no_base_rows`，
     不填市场常数凑配对）。

另有 run() 级用例：正式 cohort 的信号日确实被传给引擎（改动 3 的接线），
且冻结面板的信号日网格没有被扩充。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "phase2"))
import expiry_label_pipeline as elp  # noqa: E402
import m1_base_rate as m1  # noqa: E402
import p_rel_t10_baseline_eval as P  # noqa: E402

FIXED = P.DEFAULT_USER_ID

# 正式 cohort 起点 2026-10-08（冻结登记 §2）；冻结 P1 面板止于 2025-12-31。
D0, D1, D2 = "2026-10-08", "2026-10-09", "2026-10-12"
L1A, L1B, L1C = "801010.SI", "801020.SI", "801030.SI"


def _panel(rows) -> pd.DataFrame:
    """rows: (signal_date, mat, l1, y) -> m1_base_rate panel schema."""
    return pd.DataFrame(
        [{"signal_date": s, "mat": mt, "l1": l1, "y": float(y)}
         for s, mt, l1, y in rows])


def _cell(df: pd.DataFrame, T: str, l1: str):
    sub = df[(df["signal_date"] == T) & (df["l1"] == l1)]
    assert len(sub) == 1, (T, l1, len(sub))
    return sub.iloc[0]


def _maps_num_equal(x: dict, y: dict) -> bool:
    """Dict equality treating NaN == NaN (a missing base rate is a value)."""
    if set(x) != set(y):
        return False
    for k in x:
        a, b = x[k], y[k]
        if isinstance(a, float) and isinstance(b, float) \
                and math.isnan(a) and math.isnan(b):
            continue
        if a != b:
            return False
    return True


# 冻结面板（面板日 2025-12-29..31，全部标签已成熟）——
# A: 3 条 (2 胜 1 负)；B: 2 条 (0 胜)；市场 5 条 / 2 胜 -> p_mkt = 0.4
FROZEN_PANEL_ROWS = [
    ("2025-12-29", "2025-12-29", L1A, 1),
    ("2025-12-30", "2025-12-30", L1A, 1),
    ("2025-12-31", "2025-12-31", L1A, 0),
    ("2025-12-29", "2025-12-29", L1B, 0),
    ("2025-12-30", "2025-12-30", L1B, 0),
]

PANEL_DAYS = ["2025-12-29", "2025-12-30", "2025-12-31"]


class TestQueryDayAfterPanel:
    """验收 1：面板之后的查询日能得到基率（改动前该键不存在）。"""

    def test_query_day_after_panel_gets_expanding_base_rate(self):
        panel = _panel(FROZEN_PANEL_ROWS)
        # 改动前：引擎只走面板自身的信号日 -> 正式信号日没有任何键。
        before, _ = m1.build_pit_base_rates(
            panel, shrink_ms=(200,), window_days=(0,))
        assert set(before["signal_date"]) == set(PANEL_DAYS)
        assert not any(k[0] == D0 for k in P.base_q_map(panel))

        base, summary = m1.build_pit_base_rates(
            panel, shrink_ms=(200,), window_days=(0,), query_days=[D0])
        assert D0 in set(base["signal_date"])
        assert summary["panel_signal_days"] == 3      # 面板没被扩充
        assert summary["query_days"] == 1
        assert summary["query_days_not_in_panel"] == 1
        assert summary["query_days_after_panel_max"] == 1

        # 手算：截至 D0 已成熟 = 面板全部 5 条（2 胜），同 m200/W0 扩窗值
        p_mkt = 2 / 5
        a = _cell(base, D0, L1A)
        b = _cell(base, D0, L1B)
        assert int(a["n"]) == 3 and a["p_mkt"] == pytest.approx(p_mkt)
        assert int(b["n"]) == 2
        assert a["p_m200"] == pytest.approx((2 + 200 * p_mkt) / (3 + 200))
        assert b["p_m200"] == pytest.approx((0 + 200 * p_mkt) / (2 + 200))

        # 包装层同值；正式 cohort 的 signal_date 是 YYYYMMDD，两种写法同键
        qmap = P.base_q_map(panel, query_days=[D0])
        assert qmap[(D0, L1A)] == pytest.approx((2 + 200 * p_mkt) / (3 + 200))
        assert P.base_q_map(panel, query_days=[D0.replace("-", "")]) == qmap


class TestNoLookAhead:
    """验收 2：退出日晚于查询日的标签不计入。"""

    def test_label_maturing_after_query_day_is_not_counted(self):
        panel = _panel([
            # 查询日 2026-01-10 之前（含当日）已成熟：A 1 胜、B 1 负
            ("2026-01-05", "2026-01-05", L1A, 1),
            ("2026-01-05", "2026-01-05", L1B, 0),
            # 查询日之后才成熟（退出日 2026-01-20）—— 任何早期查询日都不可见
            ("2026-01-05", "2026-01-20", L1A, 1),
            ("2026-01-05", "2026-01-20", L1A, 1),
        ])
        base, _ = m1.build_pit_base_rates(
            panel, shrink_ms=(200,), window_days=(0,),
            query_days=["2026-01-10", "2026-01-25"])

        early = _cell(base, "2026-01-10", L1A)
        assert int(early["n"]) == 1                  # 只有 2026-01-05 那条
        assert early["p_mkt"] == pytest.approx(1 / 2)
        assert early["p_m200"] == pytest.approx((1 + 200 * 0.5) / (1 + 200))
        # 反证（非空断言）：若把后成熟的两条也算进来，值会不同
        assert early["p_m200"] != pytest.approx((3 + 200 * 0.75) / (3 + 200))

        # 到 2026-01-25 两条才进入 W0 扩窗
        late = _cell(base, "2026-01-25", L1A)
        assert int(late["n"]) == 3
        assert late["p_mkt"] == pytest.approx(3 / 4)
        assert late["p_m200"] == pytest.approx((3 + 200 * 0.75) / (3 + 200))


class TestPanelDaysUnchanged:
    """验收 3：面板内日期的结果与改动前逐值相同。"""

    def test_panel_day_rows_identical_with_and_without_query_days(self):
        panel = _panel(FROZEN_PANEL_ROWS + [
            ("2025-12-29", "2026-01-15", L1C, 1),   # 面板内日期之后才成熟
        ])
        f0, s0 = m1.build_pit_base_rates(
            panel, shrink_ms=(50, 200, 500), window_days=(0,))
        f1, s1 = m1.build_pit_base_rates(
            panel, shrink_ms=(50, 200, 500), window_days=(0,),
            query_days=[D0, D1])

        cols = ["signal_date", "l1", "n", "p_mkt", "p_raw",
                "p_m50", "p_m200", "p_m500"]
        a = (f0[cols].sort_values(["signal_date", "l1"])
             .reset_index(drop=True))
        b = (f1[f1["signal_date"].isin(set(f0["signal_date"]))][cols]
             .sort_values(["signal_date", "l1"]).reset_index(drop=True))
        pd.testing.assert_frame_equal(a, b)          # 逐值（含 NaN 位置）相同
        assert s1["signal_days"] == s0["signal_days"] + len({D0, D1})
        assert s1["panel_signal_days"] == s0["panel_signal_days"]

        q0 = P.base_q_map(panel)
        q1 = P.base_q_map(panel, query_days=[D0, D1])
        panel_keys = {k for k in q1 if k[0] in set(f0["signal_date"])}
        assert _maps_num_equal({k: q1[k] for k in panel_keys}, q0)


class TestMissingStaysMissing:
    """验收 4：缺历史 / 缺行业仍判缺失，不填市场常数。"""

    def test_no_matured_history_yields_nan_cell_not_market_constant(self):
        panel = _panel([
            ("2025-12-29", "2025-12-29", L1A, 1),
            ("2025-12-29", "2025-12-29", L1A, 0),
            # B 的首条标签在查询日之后才成熟
            ("2025-12-29", "2026-01-20", L1B, 1),
        ])
        base, _ = m1.build_pit_base_rates(
            panel, shrink_ms=(200,), window_days=(0,),
            query_days=["2026-01-10"])
        b = _cell(base, "2026-01-10", L1B)
        assert int(b["n"]) == 0
        assert math.isnan(b["p_raw"]) and math.isnan(b["p_m200"])
        a = _cell(base, "2026-01-10", L1A)
        assert int(a["n"]) == 2
        assert a["p_m200"] == pytest.approx((1 + 200 * 0.5) / (2 + 200))
        assert not math.isnan(a["p_m200"])

    def test_missing_industry_row_is_dropped_and_counted_not_filled(self):
        """sw.index_code 空的记录没有行业线 -> 不入配对（no_base_rows），
        绝不填市场常数或别的行业线。"""
        day = D0
        qmap = {(day, f"L{i}"): 0.5 + 0.01 * i for i in range(12)}
        rows = [{"signal_date": day, "symbol": f"S{i:02d}", "q": 60.0 + i,
                 "r": float(i), "label_l1": f"L{i}"} for i in range(12)]
        rows += [{"signal_date": day, "symbol": f"X{i}", "q": 60.0 + i,
                  "r": float(i), "label_l1": ""} for i in range(2)]
        inc = P.paired_increment(pd.DataFrame(rows), qmap,
                                 [day.replace("-", "")])
        assert inc["model_rows"] == 14
        assert inc["paired_rows"] == 12              # 只有有基率的记录入配对
        assert inc["no_base_rows"] == 2
        assert inc["paired_days"] == 1
        assert inc["paired_day_n"] == {day: 12}
        # 缺行业的 2 条从未被填上基率（两腿都用同一 12 条配对子集）
        assert inc["base_ic_mean"] == pytest.approx(inc["model_ic_mean"])


# ---------------------------------------------------------------------------
# run() 级：正式信号日确实被传给基率引擎（改动 3 的接线）
# ---------------------------------------------------------------------------

def _rec(rid, sym, day, p, **kw):
    kw.setdefault("sealed_at", f"{day} 08:00:00.000000")
    return P._ledger_rec(rid, sym, day, p, **kw)


def _lab(rec, r_pct, l1):
    return P._label(rec["symbol"], rec["signal_date"], r_pct, l1=l1,
                    report_id=rec["report_id"],
                    record_sha256=elp._record_identity(rec))


def _write_run_fixture(tmp_path, label_days):
    """正式窗 3 天 × 12 行的账本 + 标签 + 早于 cohort 的冻结面板 + 日历。

    ``label_days`` 指定哪些信号日给出已成熟标签；其余日子的标签留空 =
    待评（r 为空）。返回 (ledger, labels, chunks, calendar)。
    """
    days = [D0, D1, D2]
    recs, labs = [], []
    for di, d in enumerate(days):
        for i in range(12):
            l1 = (L1A, L1B, L1C)[i % 3]
            rec = _rec(f"{di}_{i}", f"S{i:02d}", d, 20 + i * 5,
                       created=f"{d}T09:00")
            recs.append(rec)
            if d in label_days:
                labs.append(_lab(rec, 0.5 * i - 2.0, l1))
    ledger = tmp_path / "l.jsonl"
    ledger.write_text("".join(f"{json.dumps(r)}\n" for r in recs),
                      encoding="utf-8")
    lab = tmp_path / "lab.jsonl"
    lab.write_text("".join(f"{json.dumps(x)}\n" for x in labs),
                   encoding="utf-8")

    prows = []
    for d in PANEL_DAYS:
        for li, l1 in enumerate((L1A, L1B, L1C)):
            for k in range(10):
                prows.append(P._p1_row(f"M{li}{k}", d.replace("-", ""), l1,
                                       1.0 if k < 4 + 2 * li else 0.0,
                                       exit_date=d.replace("-", "")))
    chunks = tmp_path / "chunks"
    P._write_p1_chunks(chunks, prows)
    cal = tmp_path / "c.txt"
    cal.write_text("\n".join(
        pd.bdate_range(D0, periods=40).strftime("%Y-%m-%d")) + "\n",
        encoding="utf-8")
    return ledger, lab, chunks, cal


def test_run_passes_formal_signal_days_to_base_engine(tmp_path):
    """面板（2025-12-29..31）整体早于正式 cohort：只有把正式信号日作为
    查询日传给引擎，D-075 配对才有基率；面板信号日网格不得被扩充。"""
    days = [D0, D1, D2]
    ledger, lab, chunks, cal = _write_run_fixture(tmp_path, set(days))
    out = P.run(ledger, lab, tmp_path / "o", calendar_path=cal,
                base_chunks=chunks)

    inc = out["increment_m200_W0"]
    assert inc["model_rows"] == 36 and inc["no_base_rows"] == 0
    assert inc["paired_days"] == 3                   # 面板后三天都有基率
    # 冻结面板的信号日网格没有被查询日扩充
    assert out["base_source"]["panel"]["signal_days"] == len(PANEL_DAYS)
    aud = out["base_source"]["query_days"]
    assert aud["formal_signal_days"] == 3
    assert aud["keys"] == 3 * 3                      # 3 个正式信号日 × 3 行业
    assert aud["missing_cells"] == 0
    assert aud["keys_by_day"] == {d: 3 for d in days}
    # 按行业成熟数 = 面板逐行业已成熟标签（10 条/行业，3 个面板日）
    assert aud["matured_by_l1"] == {L1A: 30, L1B: 30, L1C: 30}
    assert aud["matured_market"] == 90
    assert aud["matured_by_l1_same_all_days"] is True
    assert aud["matured_by_l1_per_day"] is None
    assert aud["scope"] == {
        "rule": "query days = 正式 cohort 信号日 ∪ admit 输入行中标签未到期"
                "（r 为空）的待评信号日（DAV-2240）",
        "formal_cohort_days": 3, "pending_days": 0, "query_days": 3}


def test_run_base_exists_for_pending_formal_days(tmp_path):
    """标签尚未到期的正式窗日子（待评）也必须有基率 —— 否则首个 T+10
    到期日才发现面板之后无基率，D-075 配对直接断掉。"""
    days = [D0, D1, D2]
    ledger, lab, chunks, cal = _write_run_fixture(tmp_path, {D0})
    out = P.run(ledger, lab, tmp_path / "o", calendar_path=cal,
                base_chunks=chunks)

    # 只有 D0 的标签已成熟 -> 只有 D0 参与配对
    inc = out["increment_m200_W0"]
    assert inc["model_rows"] == 12 and inc["paired_days"] == 1
    aud = out["base_source"]["query_days"]
    assert aud["scope"]["formal_cohort_days"] == 1
    assert aud["scope"]["pending_days"] == 2
    assert aud["formal_signal_days"] == 3
    assert aud["keys"] == 3 * 3
    assert aud["keys_by_day"] == {d: 3 for d in days}
