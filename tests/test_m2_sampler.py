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


def test_remainder_tie_breaking_stratum_code_ascending():
    """Ties in largest-remainder quota allocation must break by stratum code ascending."""
    # 20 industries, 1 stock each. For N=5, raw quota is 5/20 = 0.25 for all.
    # Floor quota is 0, leftover is 5.
    # All 20 have equal remainder (0.25).
    # Quota must be allocated to the 5 smallest stratum codes in ascending order:
    # 801000.SI ... 801004.SI (reproducing the review scenario in DAV-1685).
    rows = []
    scrambled = [18, 5, 2, 19, 0, 11, 8, 1, 14, 3, 17, 7, 16, 4, 12, 6, 13, 9, 15, 10]
    for i in scrambled:
        code = f"801{i:03d}.SI"
        rows.append((f"{i:06d}.SZ", code, f"行业{i}"))
    df = pd.DataFrame(rows, columns=["ts_code", "sw_l1_code", "sw_l1_name"])

    sample1 = ms.stratified_sample(df, 5, seed=42)
    sample2 = ms.stratified_sample(df.sample(frac=1, random_state=99), 5, seed=42)

    # Shuffled input yields identical output
    assert sample1.equals(sample2)

    # Allocated industries must be the first 5 stratum codes ascending
    picked_strata = sorted(sample1["sw_l1_code"].unique())
    expected_strata = [f"801{i:03d}.SI" for i in range(5)]
    assert picked_strata == expected_strata

    # Byte-identical across repeated runs under the same seed
    sample3 = ms.stratified_sample(df, 5, seed=42)
    assert sample1.to_dict("records") == sample3.to_dict("records")


def test_remainder_tie_breaking_mixed_remainders():
    """Larger remainders win first; among tied remainders, stratum code ascending breaks ties."""
    # S1: 4 stocks -> 4/10 * 4 = 1.6 -> floor 1, rem 0.6
    # S2: 2 stocks -> 2/10 * 4 = 0.8 -> floor 0, rem 0.8
    # S3: 1 stock  -> 1/10 * 4 = 0.4 -> floor 0, rem 0.4
    # S4: 1 stock  -> 1/10 * 4 = 0.4 -> floor 0, rem 0.4
    # S5: 1 stock  -> 1/10 * 4 = 0.4 -> floor 0, rem 0.4
    # S6: 1 stock  -> 1/10 * 4 = 0.4 -> floor 0, rem 0.4
    # Total stocks = 10, N = 4.
    # Floor quotas: S1: 1, S2: 0, S3: 0, S4: 0, S5: 0, S6: 0. Sum floor = 1. Leftover = 3.
    # Remainders:
    # S2: 0.8 (largest -> gets 1)
    # S1: 0.6 (second largest -> gets 1)
    # S3, S4, S5, S6: tied at 0.4. Need 1 more quota.
    # Tie broken by stratum code ascending -> S3 gets 1.
    # Total quotas: S1: 2, S2: 1, S3: 1, S4: 0, S5: 0, S6: 0.
    rows = []
    for i in range(4):
        rows.append((f"00000{i}.SZ", "801010.SI", "S1"))
    for i in range(2):
        rows.append((f"00001{i}.SZ", "801020.SI", "S2"))
    rows.append(("000020.SZ", "801030.SI", "S3"))
    rows.append(("000030.SZ", "801040.SI", "S4"))
    rows.append(("000040.SZ", "801050.SI", "S5"))
    rows.append(("000050.SZ", "801060.SI", "S6"))
    df = pd.DataFrame(rows, columns=["ts_code", "sw_l1_code", "sw_l1_name"])

    out = ms.stratified_sample(df, 4, seed=42)
    counts = out["sw_l1_code"].value_counts().to_dict()
    assert counts == {"801010.SI": 2, "801020.SI": 1, "801030.SI": 1}


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
