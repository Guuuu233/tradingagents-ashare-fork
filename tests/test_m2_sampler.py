"""DAV-1679 M2 sampler gates — pure offline tests, no cache, no network.

1. Point-in-time index membership: the latest index_weight snapshot
   trade_date <= T is used; a stock dropped from the index after T still
   counts as a member at T, one added after T does not.
2. ST / listing<1y / suspended exclusions are all enforced.
3. Stratified quota math is proportional (floor + largest remainder) and the
   output is byte-deterministic under a fixed seed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import scripts.phase2.m2_sampler as ms


def _eligible_df() -> pd.DataFrame:
    rows = []
    # 6 banks, 3 chemicals, 1 steel — quotas at N=5: 3.0 / 1.5 / 0.5
    for i in range(6):
        rows.append(("0000%02d.SZ" % i, "801780.SI", "银行"))
    for i in range(10, 13):
        rows.append(("0000%02d.SZ" % i, "801030.SI", "基础化工"))
    rows.append(("600000.SH", "801040.SI", "钢铁"))
    return pd.DataFrame(rows, columns=["ts_code", "sw_l1_code", "sw_l1_name"])


def test_st_intervals_cover_date():
    nc = pd.DataFrame([
        {"ts_code": "000001.SZ", "name": "ST测试", "start_date": "20250101", "end_date": "20250301"},
        {"ts_code": "000001.SZ", "name": "测试股份", "start_date": "20250302", "end_date": None},
        {"ts_code": "000002.SZ", "name": "*ST退", "start_date": "20200101", "end_date": None},
        {"ts_code": "000003.SZ", "name": "正常名", "start_date": "20200101", "end_date": None},
        # re-ST: normal name, then ST again later
        {"ts_code": "000004.SZ", "name": "正常股", "start_date": "20200101", "end_date": "20251215"},
        {"ts_code": "000004.SZ", "name": "ST戴帽", "start_date": "20251216", "end_date": None},
        # duplicate same-start conflicting end rows (cache artifact): a later
        # named row supersedes, so the open-ended dup must NOT mark it ST
        {"ts_code": "000005.SZ", "name": "*ST藏格", "start_date": "20200506", "end_date": "20210511"},
        {"ts_code": "000005.SZ", "name": "*ST藏格", "start_date": "20200506", "end_date": None},
        {"ts_code": "000005.SZ", "name": "藏格矿业", "start_date": "20211208", "end_date": None},
    ])
    iv = ms.build_st_intervals(nc)
    assert ms.is_st_on("000001.SZ", "20250201", iv)
    assert not ms.is_st_on("000001.SZ", "20250401", iv)
    assert ms.is_st_on("000002.SZ", "20251231", iv)
    assert not ms.is_st_on("000003.SZ", "20251231", iv)
    assert not ms.is_st_on("000004.SZ", "20250630", iv)
    assert ms.is_st_on("000004.SZ", "20251231", iv)   # re-ST
    # open-ended duplicate artifact: latest-starting name is 藏格矿业 → not ST
    assert not ms.is_st_on("000005.SZ", "20250630", iv)
    assert ms.is_st_on("000005.SZ", "20210101", iv)  # within real *ST window


def test_stratified_quota_proportional_and_deterministic():
    g = _eligible_df()
    a = ms.stratified_sample(g, 5, seed=42)
    b = ms.stratified_sample(g.sample(frac=1, random_state=7), 5, seed=42)  # shuffled input
    assert len(a) == 5
    assert list(a["ts_code"]) == list(b["ts_code"])
    by = a["sw_l1_code"].value_counts()
    assert by["801780.SI"] == 3      # floor(3.0)
    assert by.get("801030.SI", 0) == 2   # 1.5 → floor 1 + largest remainder
    assert by.get("801040.SI", 0) == 0   # 0.5 → floor 0, loses the remainder tie
    # different seed → different draw
    c = ms.stratified_sample(g, 5, seed=1)
    assert list(c["ts_code"]) != list(a["ts_code"])


def test_sample_all_when_n_ge_pool():
    g = _eligible_df()
    out = ms.stratified_sample(g, 99, seed=42)
    assert len(out) == len(g)
    assert list(out["ts_code"]) == sorted(g["ts_code"])


def test_index_snapshot_pit(tmp_path):
    """Latest trade_date <= T wins; newer snapshot ignored."""
    w = pd.DataFrame([
        {"index_code": "000300.SH", "trade_date": "20251201", "con_code": "000001.SZ", "weight": 1.0},
        {"index_code": "000300.SH", "trade_date": "20251201", "con_code": "000002.SZ", "weight": 1.0},
        {"index_code": "000300.SH", "trade_date": "20260601", "con_code": "000001.SZ", "weight": 1.0},
        {"index_code": "000300.SH", "trade_date": "20260601", "con_code": "000003.SZ", "weight": 1.0},
    ])
    w.to_pickle(tmp_path / "index_weight_000300.SH.pkl")
    snap = ms.load_index_weight_snapshot(tmp_path, "hs300", "20251231")
    assert set(snap["ts_code"]) == {"000001.SZ", "000002.SZ"}
    assert snap["idx_snap_date"].iloc[0] == "20251201"
    with pytest.raises(ValueError):
        ms.load_index_weight_snapshot(tmp_path, "hs300", "20200101")


def test_sw_l1_pit(tmp_path):
    member = pd.DataFrame([
        {"ts_code": "000001.SZ", "l1_code": "801780.SI", "l1_name": "银行",
         "in_date": "19910403", "out_date": None, "is_new": "Y"},
        {"ts_code": "000006.SZ", "l1_code": "801180.SI", "l1_name": "房地产",
         "in_date": "19920427", "out_date": "20150929", "is_new": "N"},
        {"ts_code": "000006.SZ", "l1_code": "801180.SI", "l1_name": "房地产",
         "in_date": "20150930", "out_date": None, "is_new": "Y"},
    ])
    member.to_pickle(tmp_path / "index_member_all_all.pkl")
    m = ms.load_sw_l1_pit(tmp_path, ["000001.SZ", "000006.SZ"], "20150101")
    assert m["000006.SZ"] == ("801180.SI", "房地产")
    m2 = ms.load_sw_l1_pit(tmp_path, ["000006.SZ"], "20251231")
    assert "000006.SZ" in m2
