"""DAV-2373: 守卫提取器 6 处缺陷的回归测试（DAV-2372 定责）。

每条用例对应卡面「一」表里的一处缺陷；句式取自 DAV-2372 冻结证据的原始行
（`evidence/originals/`），记录为同结构的 synthetic 逐日 r0_net（亿元），
数值与冻结证据一致，便于与 `DAV-2372-readonly-diagnosis.md` 逐条对照。

判定要求（D-076 条件一）：提取误报必须放行，模型真写错数字必须仍拦截，
取不到数据时仍弃权（不冒充已核对）。
"""

import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    _extract_model_mentions,
    validate_model_summary,
)

SOURCE = "eastmoney_direct"


def _records(symbol, rows):
    return [
        {
            "source": SOURCE,
            "symbol": symbol,
            "date": date,
            "status": "available",
            "period_kind": "historical_daily",
            "time_window": "1d",
            "field": "r0_net",
            "unit": "亿元",
            "r0_net": value,
        }
        for date, value in rows
    ]


def check(text, rows, *, symbol, as_of, window_days=1):
    return validate_model_summary(
        _records(symbol, rows),
        text,
        window_days=window_days,
        selected_field="r0_net",
        selected_source=SOURCE,
        requested_as_of=as_of,
        symbol=symbol,
    )


# ---------------------------------------------------------------------------
# 缺陷 1（:2204 / :2302）板块/行业/并列主体金额被当成个股金额
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    # 191dc4d1 688981.SH 2026-11-03（白酒行业金额）
    "主力资金大举净流入白酒（+31.89亿元）、保险（+23.72亿元）、证券（+17.51亿元），"
    "而科技成长方向全面失血。",
    # 3c2e8605 601919.SH 2026-10-29（银行板块金额）
    "主力资金大幅流入银行（+41.26 亿元）、保险（+15.61 亿元）、白酒（+14.25 亿元）"
    "等大盘价值方向；科技板块遭遇流动性瓦解。",
    # 7c403cdb 600196.SH 2026-11-02（证券行业金额）
    "主力资金大幅流入证券（+55.08 亿）、文化传媒（+46.12 亿）、软件开发（+43.14 亿）、"
    "半导体（+34.86 亿）等高贝塔与科技成长赛道。",
    # 0453ef40 600309.SH 2026-10-28（并列行业金额）
    "半导体、通信设备、汽车零部件主力净流入分别高达 88.56 亿、76.58 亿、42.65 亿元；"
    "而基础化工未进入全市场前列。",
])
def test_de1_named_sector_amounts_are_not_target_values(text):
    result = check(text, [("2026-11-03", "4.5379488"), ("2026-10-29", "0.2369"),
                          ("2026-11-02", "-0.08988759"), ("2026-10-28", "-0.573255")],
                   symbol="688981.SH", as_of="2026-11-03")
    assert result["hard_guard"]["blocked"] is False
    assert result["mismatches"] == []
    assert "r0_net" not in result["model_daily"]


def test_de1_named_sector_amounts_still_surface_a_named_target_claim():
    # 板块金额被排除，但同一段里对目标个股的明示金额仍必须校核。
    result = check(
        "主力资金大幅流入白酒（+31.89亿元）、保险（+23.72亿元）。"
        "今日该股主力净流入4.5379488亿元。",
        [("2026-11-03", "4.5379488")], symbol="688981.SH", as_of="2026-11-03",
    )
    assert result["status"] == "matched"
    assert result["model_daily"] == {"r0_net": "4.5379488"}


def test_de1_enumerated_amounts_with_named_target_stay_fail_closed():
    # 目标个股本身出现在「分别」主语列表里时不得整体放行。
    result = check(
        "半导体、通信设备、万华化学主力净流入分别高达 88.56 亿、76.58 亿、42.65 亿元。",
        [("2026-10-28", "-0.573255")], symbol="600309.SH", as_of="2026-10-28",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True


# ---------------------------------------------------------------------------
# 缺陷 2（:2644）绝对值上界被当成点估计并要求正方向
# ---------------------------------------------------------------------------

def test_de2_absolute_magnitude_upper_bound_accepts_negative_net():
    # f12a1167 000651.SZ 2026-10-29：|−0.180515| <= 0.25，原句约束的是绝对净额。
    result = check(
        "两者共同特征在于：绝对金额极小（主力/大单绝对净额均在 0.25 亿元以内），"
        "换手与成交量处于阶段性萎缩状态。",
        [("2026-10-29", "-0.180515")], symbol="000651.SZ", as_of="2026-10-29",
    )
    assert result["hard_guard"]["blocked"] is False
    assert result["mismatches"] == []


@pytest.mark.parametrize("text", [
    "今日主力净流入超过1.2亿元。",
    "今日主力净流出低于1亿元。",
])
def test_de2_directional_bounds_keep_requiring_same_sign(text):
    # 去掉「绝对/幅度」语义后，带方向的上界仍必须同向，不得借修复放宽。
    result = check(text, [("2026-10-29", "-1.25")], symbol="000651.SZ", as_of="2026-10-29")
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True


# ---------------------------------------------------------------------------
# 缺陷 3（:2721 + :3264）多日累计窗口末日错绑到「后」子句的日期
# ---------------------------------------------------------------------------

def test_de3_historical_window_ends_before_transition_clause():
    # 411c1ab1 601628.SH 2026-11-02：正确窗口 10-27~10-30 合计 -1.43836251。
    result = check(
        "连续 4 日主力资金分组大幅净流出超 1.43 亿元后，"
        "11 月 2 日单日主力资金转为净流入 1398.07 万元。",
        [("2026-10-27", "-0.20405163"), ("2026-10-28", "-0.27088763"),
         ("2026-10-29", "-0.70717661"), ("2026-10-30", "-0.25624664"),
         ("2026-11-02", "0.13980662")],
        symbol="601628.SH", as_of="2026-11-02", window_days=4,
    )
    assert result["hard_guard"]["blocked"] is False
    assert result["mismatches"] == []
    assert result["model_interval"] == {"r0_net": "-1.43"}


def test_de3_wrong_historical_window_still_blocks():
    result = check(
        "连续 4 日主力资金分组大幅净流出超 3.00 亿元后，11 月 2 日单日主力资金转为净流入 1398.07 万元。",
        [("2026-10-27", "-0.20405163"), ("2026-10-28", "-0.27088763"),
         ("2026-10-29", "-0.70717661"), ("2026-10-30", "-0.25624664"),
         ("2026-11-02", "0.13980662")],
        symbol="601628.SH", as_of="2026-11-02", window_days=4,
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True


# ---------------------------------------------------------------------------
# 缺陷 4（:2775）「由A缩减至B」把上期值当成当期值
# ---------------------------------------------------------------------------

def test_de4_transition_opening_value_binds_to_prior_trading_day():
    # f5f9e4e0 601888.SH 2026-11-03：A=11-02 的 2.26591028，B=11-03 的 0.59565659。
    text = (
        "经过连续两日放量冲高，11 月 3 日主力净流入规模由 2.26 亿元缩减至 0.59 亿元，"
        "表明买方主动推高意愿有所降温。"
    )
    mentions = _extract_model_mentions(
        text, requested_as_of="2026-11-03", symbol="601888.SH",
    )
    opening = [m for m in mentions if m["value_text"] == "2.26"]
    assert len(opening) == 1
    assert opening[0]["kind"] == "previous"
    assert opening[0]["date"] == "2026-11-03"
    result = check(
        text,
        [("2026-11-02", "2.26591028"), ("2026-11-03", "0.59565659")],
        symbol="601888.SH", as_of="2026-11-03",
    )
    assert result["hard_guard"]["blocked"] is False
    assert result["mismatches"] == []
    # 过渡句的终点值由同一报告的日度行承担，不当作期初值重比。
    assert "r0_net" not in result["model_daily"]


def test_de4_wrong_opening_value_is_not_silently_accepted():
    result = check(
        "11 月 3 日主力净流入规模由 5.00 亿元缩减至 0.59 亿元，买盘意愿降温。",
        [("2026-11-02", "2.26591028"), ("2026-11-03", "0.59565659")],
        symbol="601888.SH", as_of="2026-11-03",
    )
    assert result["status"] == "mismatch"
    assert result["mismatches"][0]["kind"] == "previous"
    assert result["mismatches"][0]["structured"] == "2.26591028"


# ---------------------------------------------------------------------------
# 缺陷 5（:2838）表格局部作用域失效，邻列趋势天数侵入本格金额
# ---------------------------------------------------------------------------

def test_de5_table_trend_column_does_not_redefine_cell_window():
    # 3d3d63b1 600036.SH 2026-11-03：单日 -2.62 == 当日 -2.62072112。
    text = (
        "| 分类 | 资金数据 | 趋势 | 可信度 |\n"
        "|---|---|---|---|\n"
        "| 单日大单流向 | 主力净额 -2.62 亿元 | 连续 2 日主动卖单占优候选 | 偏高 |\n"
    )
    result = check(
        text, [("2026-11-02", "-3.16000000"), ("2026-11-03", "-2.62072112")],
        symbol="600036.SH", as_of="2026-11-03", window_days=2,
    )
    assert result["hard_guard"]["blocked"] is False
    assert result["mismatches"] == []
    assert result["model_daily"] == {"r0_net": "-2.62"}


def test_de5_table_cell_daily_marker_is_kept_for_real_conflict():
    text = (
        "| 分类 | 资金数据 | 趋势 |\n"
        "|---|---|---|\n"
        "| 单日大单流向 | 主力净额 -9.99 亿元 | 连续 2 日主动卖单占优候选 |\n"
    )
    result = check(
        text, [("2026-11-02", "-3.16000000"), ("2026-11-03", "-2.62072112")],
        symbol="600036.SH", as_of="2026-11-03", window_days=2,
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True


# ---------------------------------------------------------------------------
# 缺陷 6（:3231-3239）窗口/口径绑定未校验：句内趋势覆盖「N日累计」标签
# ---------------------------------------------------------------------------

def test_de6_explicit_cumulative_label_beats_inner_trend():
    # 07441c59 600887.SH 2026-11-03：5 行合计 -1.96528143，对 -1.97。
    result = check(
        "- **5日累计主力净额**：**-1.97 亿元**（近两个交易日主力分单流出加速，回吐前期流入量）。",
        [("2026-10-28", "-1.06916559"), ("2026-10-29", "1.74689472"),
         ("2026-10-30", "0.34614976"), ("2026-11-02", "-0.91047904"),
         ("2026-11-03", "-2.07868128")],
        symbol="600887.SH", as_of="2026-11-03",
    )
    assert result["hard_guard"]["blocked"] is False
    assert result["mismatches"] == []
    assert result["model_interval"] == {"r0_net": "-1.97"}


def test_de6_cumulative_label_window_still_compares_wrong_total():
    result = check(
        "- **5日累计主力净额**：**-4.00 亿元**（近两个交易日主力分单流出加速）。",
        [("2026-10-28", "-1.06916559"), ("2026-10-29", "1.74689472"),
         ("2026-10-30", "0.34614976"), ("2026-11-02", "-0.91047904"),
         ("2026-11-03", "-2.07868128")],
        symbol="600887.SH", as_of="2026-11-03",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["window_days"] == 5


# ---------------------------------------------------------------------------
# 反例：模型真写错数字必须仍被拦截（D-076 条件一）
# ---------------------------------------------------------------------------

def test_dav2373_true_model_decimal_error_is_still_blocked():
    # 5336af4a 002415.SZ 2026-11-03：模型把 -0.01458144 亿写成 -0.0015 亿。
    result = check(
        "2026-10-29：主力净额 -0.0015 亿元（微幅流出）",
        [("2026-10-29", "-0.01458144")], symbol="002415.SZ", as_of="2026-11-03",
    )
    assert result["status"] == "mismatch"
    assert result["hard_guard"]["blocked"] is True
    assert result["mismatches"][0]["kind"] == "dated"
    assert result["mismatches"][0]["structured"] == "-0.01458144"


def test_dav2373_missing_record_is_unverifiable_not_verified():
    # 取不到同日数据时仍弃权，不得把无法核对写成已核对。
    result = check(
        "2026-10-29：主力净流出0.50亿元。",
        [("2026-10-28", "-1.06916559")], symbol="600887.SH", as_of="2026-11-03",
    )
    assert result["hard_guard"]["blocked"] is False
    assert "r0_net" in result["unverifiable_fields"]
    assert result["status"] != "matched"
    assert result["mismatches"] == []
