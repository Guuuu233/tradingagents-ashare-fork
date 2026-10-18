"""DAV-2255 — 敏感性网格 W>0 接入查询日期集合 + 统一 ISO 日期口径。

卡面（总控 10-18 令）三条：

  1. **W>0 各档在面板之后的日期能得到基率**，且等于手算的**同一 W 滚窗**
     值（同一冻结面板、按截至查询日已成熟标签、无前视）；
  2. **无前视**（退出日晚于查询日不计入）；
  3. **面板内日期结果与改动前逐值相同**；日期表现形（`YYYYMMDD` /
     `YYYY-MM-DD`）不得影响任何取值 —— W>0 滚窗曾用「YYYYMMDD 截点 vs
     ISO 成熟日」的原样字符串比较，`'2023-09-27' < '20230927'`（`'-' <
     `'0'`）导致 ISO 面板下 W=250/500 的成熟标签被全量逐出。

另有接线用例：主口径（`increment_m200_W0`）与 8 个敏感性格共用**同一
查询日期集合**（DAV-2240 的 `formal_days`）。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "phase2"))
import expiry_label_pipeline as elp  # noqa: E402
import m1_base_rate as m1  # noqa: E402
import p_rel_t10_baseline_eval as P  # noqa: E402

# 正式 cohort 起点 2026-10-08（冻结登记 §2）——正式窗 3 个信号日。
D0, D1, D2 = "2026-10-08", "2026-10-09", "2026-10-12"
L1A, L1B, L1C = "801010.SI", "801020.SI", "801030.SI"
SENS_KEYS = {"m50_W0", "m500_W0", "m50_W250", "m200_W250", "m500_W250",
             "m50_W500", "m200_W500", "m500_W500"}


def _panel(rows) -> pd.DataFrame:
    """rows: (signal_date, mat, l1, y) -> m1_base_rate panel schema."""
    return pd.DataFrame(
        [{"signal_date": s, "mat": mt, "l1": l1, "y": float(y)}
         for s, mt, l1, y in rows])


def _cell(df: pd.DataFrame, T: str, l1: str):
    sub = df[(df["signal_date"] == T) & (df["l1"] == l1)]
    assert len(sub) == 1, (T, l1, len(sub))
    return sub.iloc[0]


def _rising_panel(n: int = 400, start: str = "2024-01-02"):
    """One matured label per calendar day, signal day == maturity day."""
    d0 = pd.Timestamp(start)
    rows = []
    for k in range(n):
        d = (d0 + pd.Timedelta(days=k)).strftime("%Y-%m-%d")
        rows.append((d, d, L1A, float(k % 2)))
    return rows


# ---------------------------------------------------------------------------
# 1) ISO 日期口径统一：日期表现形不得改变任何取值
# ---------------------------------------------------------------------------

class TestDayShapeInvariance:
    def test_day_key_and_shift_day_are_shape_invariant(self):
        assert m1.day_key("2024-06-03") == m1.day_key("20240603") == 20240603
        # 同一个日期的两种写法必须是同一个日键，而原样字符串比较说它们不同
        assert m1.day_key("2023-09-27") == m1.day_key("20230927")
        assert "2023-09-27" < "20230927"
        assert m1.day_key("20230927") < m1.day_key("2024-06-03")
        assert m1.shift_day("2024-06-03", -250) == "2023-09-27"
        assert m1.shift_day("20240603", -250) == "20230927"
        with pytest.raises(ValueError):
            m1.day_key("2024/06/03")

    def test_rolling_window_counts_inside_window_in_both_shapes(self):
        """W=250 在 T=2025-02-04 的窗口 = [2024-05-30, 2025-02-04] -> 251 天。

        改动前（ISO 面板）：字符串截点比较把窗口内样本逐出，n=35；
        改动后必须与 YYYYMMDD 面板逐值相同。
        """
        rows = _rising_panel()
        iso = _panel(rows)
        ymd = _panel([(s.replace("-", ""), mt.replace("-", ""), l1, y)
                      for s, mt, l1, y in rows])
        for W, want_last_n in ((0, 400), (250, 251), (500, 400)):
            fi, _ = m1.build_pit_base_rates(iso, shrink_ms=(200,),
                                            window_days=(W,), min_cell_n=1)
            fy, _ = m1.build_pit_base_rates(ymd, shrink_ms=(200,),
                                            window_days=(W,), min_cell_n=1)
            assert int(_cell(fi, "2025-02-04", L1A)["n"]) == want_last_n
            a = fi[["signal_date", "l1", "n", "p_mkt", "p_m200"]].sort_values(
                ["signal_date", "l1"]).reset_index(drop=True)
            b = fy[["signal_date", "l1", "n", "p_mkt", "p_m200"]].sort_values(
                ["signal_date", "l1"]).reset_index(drop=True)
            assert a["n"].tolist() == b["n"].tolist()
            assert a["p_m200"].fillna(-1).tolist() == \
                b["p_m200"].fillna(-1).tolist()

    def test_mixed_shape_panel_is_ordered_by_date_not_by_string(self):
        """同一面板内 ISO 成熟日与 YYYYMMDD 信号日混排：日期序由日键决定。"""
        iso_rows = _rising_panel(n=120, start="2025-09-01")
        mixed = _panel([(s.replace("-", ""), mt, l1, y)
                        for s, mt, l1, y in iso_rows])
        base, _ = m1.build_pit_base_rates(mixed, shrink_ms=(200,),
                                          window_days=(250,), min_cell_n=1)
        # 窗口 = [2025-04-23, 2025-12-29]（含），120 条全部在内
        assert int(_cell(base, "20251229", L1A)["n"]) == 120
        # 反证：旧的字符串比较把每条 ISO 成熟日都判成早于 YYYYMMDD 截点
        cutoff = m1.shift_day("20251229", -250)
        assert (mixed["mat"].astype(str) < cutoff).all()

    def test_matured_audit_counts_are_shape_invariant(self):
        rows = _rising_panel(n=120, start="2025-09-01")
        iso = _panel(rows)
        ymd = _panel([(s.replace("-", ""), mt.replace("-", ""), l1, y)
                      for s, mt, l1, y in rows])
        for panel, day in ((iso, "2025-12-29"), (ymd, "20251229")):
            aud = P.base_query_audit(panel, [day])
            assert aud["matured_by_l1"] == {L1A: 120}
            assert aud["matured_market"] == 120
            assert aud["keys"] == 1 and aud["missing_cells"] == 0


# ---------------------------------------------------------------------------
# 2) W>0 各档在面板之后的日期能取到基率 == 手算滚窗值；无前视
# ---------------------------------------------------------------------------

class TestWGridsOnQueryDaysAfterPanel:
    """面板成熟日 2024-01-01 / 2025-08-01 / 2026-09-01，查询日 T=2026-10-08
    （ISO），另有一条退出日 2026-12-01 晚于 T 的标签。

    手算（窗口 = [T-W, T]，含端点）：
      W=0   -> L1A 2 条(1 胜) / L1B 1 条(1 胜) / 市场 3 条 2 胜 -> p_mkt=2/3
      W=250 -> 截点 2026-01-31 -> L1A 1 条(0 胜) / L1B 0 / 市场 1 条 0 胜 -> 0
      W=500 -> 截点 2025-05-26 -> L1A 2 条(1 胜) / L1B 0 / 市场 2 条 1 胜 -> 0.5
    """

    PANEL_ROWS = [
        ("2024-01-01", "2024-01-01", L1B, 1),   # 早于任何 W>0 窗口
        ("2025-08-01", "2025-08-01", L1A, 1),   # 只在 W=0/500 内
        ("2026-09-01", "2026-09-01", L1A, 0),   # 各 W 窗口内
        ("2026-09-30", "2026-12-01", L1C, 1),   # 退出日晚于 T：任何 W 不可见
    ]
    T = "2026-10-08"
    S_VAL = {(0, L1A): 1.0, (500, L1A): 1.0, (0, L1B): 1.0}

    def _base(self, q_days=None):
        base, _ = m1.build_pit_base_rates(
            _panel(self.PANEL_ROWS), shrink_ms=(50, 200, 500),
            window_days=(0, 250, 500), min_cell_n=1,
            query_days=q_days or [self.T])
        assert self.T in set(base["signal_date"])
        return base

    def test_w0_w250_w500_values_equal_hand_computed_window(self):
        base = self._base()
        want = {
            0: {"n": {L1A: 2, L1B: 1}, "mkt": (3, 2)},
            250: {"n": {L1A: 1, L1B: 0}, "mkt": (1, 0)},
            500: {"n": {L1A: 2, L1B: 0}, "mkt": (2, 1)},
        }
        for W, exp in want.items():
            row = base[base["W"] == W]
            n_mkt, s_mkt = exp["mkt"]
            p_mkt = s_mkt / n_mkt
            for l1 in (L1A, L1B):
                c = _cell(row, self.T, l1)
                assert int(c["n"]) == exp["n"][l1], (W, l1)
                assert c["p_mkt"] == pytest.approx(p_mkt)
                if exp["n"][l1] == 0:
                    assert math.isnan(c["p_raw"]) and math.isnan(c["p_m200"])
                else:
                    s = self.S_VAL.get((W, l1), 0.0)
                    assert c["p_m200"] == pytest.approx(
                        (s + 200 * p_mkt) / (exp["n"][l1] + 200))
            # 缺历史行业不填市场常数凑配对
            assert math.isnan(_cell(row, self.T, L1C)["p_m200"])

    def test_no_look_ahead_label_maturing_after_query_day(self):
        base = self._base()
        for W in (0, 250, 500):
            assert int(_cell(base[base["W"] == W], self.T, L1C)["n"]) == 0
        # 非空反证：该标签确实在面板里，换到它成熟之后的查询日就可见
        base2, _ = m1.build_pit_base_rates(
            _panel(self.PANEL_ROWS), shrink_ms=(200,),
            window_days=(0,), min_cell_n=1, query_days=["2026-12-02"])
        assert int(_cell(base2, "2026-12-02", L1C)["n"]) == 1

    def test_panel_days_unchanged_with_and_without_query_days(self):
        cols = ["W", "signal_date", "l1", "n", "p_mkt", "p_raw",
                "p_m50", "p_m200", "p_m500"]
        f0, _ = m1.build_pit_base_rates(
            _panel(self.PANEL_ROWS), shrink_ms=(50, 200, 500),
            window_days=(0, 250, 500), min_cell_n=1)
        f1 = self._base()
        panel_days = set(f0["signal_date"])
        key = ["W", "signal_date", "l1"]
        a = f0[cols].sort_values(key).reset_index(drop=True)
        b = (f1[f1["signal_date"].isin(panel_days)][cols]
             .sort_values(key).reset_index(drop=True))
        pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------------------
# 3) run() 级接线：8 个敏感性格与主口径共用正式查询日集合
# ---------------------------------------------------------------------------

def _rec(rid, sym, day, p, **kw):
    kw.setdefault("sealed_at", f"{day} 08:00:00.000000")
    return P._ledger_rec(rid, sym, day, p, **kw)


def _lab(rec, r_pct, l1):
    return P._label(rec["symbol"], rec["signal_date"], r_pct, l1=l1,
                    report_id=rec["report_id"],
                    record_sha256=elp._record_identity(rec))


def _run_fixture(tmp_path, panel_days):
    """正式窗 3 天 × 12 行（标签全部已成熟）+ 指定面板日的冻结面板。"""
    recs, labs = [], []
    for di, d in enumerate((D0, D1, D2)):
        for i in range(12):
            l1 = (L1A, L1B, L1C)[i % 3]
            rec = _rec(f"{di}_{i}", f"S{i:02d}", d, 20 + i * 5,
                       created=f"{d}T09:00")
            recs.append(rec)
            labs.append(_lab(rec, 0.5 * i - 2.0, l1))
    ledger = tmp_path / "l.jsonl"
    ledger.write_text("".join(f"{json.dumps(r)}\n" for r in recs),
                      encoding="utf-8")
    labels = tmp_path / "lab.jsonl"
    labels.write_text("".join(f"{json.dumps(x)}\n" for x in labs),
                      encoding="utf-8")

    prows = []
    for d in panel_days:
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
    return ledger, labels, chunks, cal


def _run(tmp_path, panel_days):
    ledger, lab, chunks, cal = _run_fixture(tmp_path, panel_days)
    out = P.run(ledger, lab, tmp_path / "o", calendar_path=cal,
                base_chunks=chunks)
    return out, chunks


def test_sensitivity_grid_uses_the_same_formal_query_days(tmp_path):
    """面板成熟日都落在 W=250 窗口内：8 个敏感性格都必须与主口径一样在
    3 个正式信号日取到基率（改动前 sensitivity 不传 query_days，W=0 的
    两格与 W>0 的六格全是 0 配对 / 无单元）。"""
    out, chunks = _run(tmp_path, ["2026-09-28", "2026-09-29", "2026-09-30"])
    assert out["increment_m200_W0"]["paired_days"] == 3
    sens = out["sensitivity"]
    assert set(sens) == SENS_KEYS
    for key in SENS_KEYS:
        assert sens[key]["query_days"] == 3, key
        assert sens[key]["paired_days"] == 3, key
        assert sens[key]["base_cells"] == 3 * 3, key   # 3 日 × 3 行业

    # 反证（接线自证）：同一面板、不传查询日时，正式窗日期上一个单元都没有
    base, _ = m1.build_pit_base_rates(
        P.build_base_panel(chunks), shrink_ms=P.SENSITIVITY_MS,
        window_days=P.SENSITIVITY_WS, min_cell_n=1)
    for W in P.SENSITIVITY_WS:
        days = set(base.loc[base["W"] == W, "signal_date"])
        assert not (days & {D0, D1, D2})


def test_w250_has_no_evidence_when_window_precedes_the_panel(tmp_path):
    """面板成熟日 2025-12-29..31 距正式信号日约 283 天：W=250 窗口
    （2026-01-31 起）内没有任何已成熟标签 -> 该格诚实记 0 配对（缺证据），
    W=500（2025-05-26 起）覆盖面板末日 -> 有证据。这是滚窗（自然日）定义
    的结果，不是日期形状缺陷。"""
    out, _ = _run(tmp_path, ["2025-12-29", "2025-12-30", "2025-12-31"])
    sens = out["sensitivity"]
    assert out["increment_m200_W0"]["paired_days"] == 3
    assert sens["m200_W250"]["base_cells"] == 0
    assert sens["m200_W250"]["paired_days"] == 0
    for key in ("m50_W500", "m200_W500", "m500_W500", "m50_W0", "m500_W0"):
        assert sens[key]["base_cells"] == 9
        assert sens[key]["paired_days"] == 3, key
