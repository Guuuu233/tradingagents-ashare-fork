"""DAV-1308 回归夹具：句内晚于 cutoff 的日期改写 window_end，不再作 as_of。

历史样本中，volume_price_report 把「未来 N 个交易日（至 X 前）观察 Y 元」
一类前瞻观察窗口的截止日 X 误记为 price_ref.as_of，外部评审据此怀疑前视
泄漏。修复后：

- 晚于 run cutoff 的句内日期一律记 ``ref["window_end"]``，并在
  ``ref["flags"]`` 打 ``as_of_after_cutoff`` 标记；
- ``as_of`` 不再出现晚于 cutoff 的值——技术报告路径回落到 cutoff 兜底，
  披露证据路径同样分流晚于 cutoff 的日期；
- 不新增 price_basis_gate 违规类型，不改变 pass/blocked 边界。

夹具的 19 个原句直接取自生产快照里 ``as_of > trade_date`` 的 volume_price_report
条目（符号/trade_date/value/as_of 原样保留，仅抽取单句回归）。
"""

from __future__ import annotations

import pytest

from tradingagents.agents.utils.price_ref_registry import (
    AS_OF_AFTER_CUTOFF_FLAG,
    build_price_ref_registry,
)


# 19 例原句回归夹具：(symbol, trade_date, value, expected_window_end, sentence)
# 全部来自历史样本 volume_price_report 中 as_of > trade_date 的条目。
WINDOW_END_FIXTURE = [
    (
        "600519.SH",
        "2026-09-24",
        1231.0,
        "2026-09-25",
        "- **时间窗口**：未来 1-3 个交易日（2026-09-25 至 2026-09-29）是确认 1231 元下轨得失的关键窗口",
    ),
    (
        "000725.SZ",
        "2026-09-24",
        5.81,
        "2026-09-25",
        "未来 **2 - 4 个交易日（即 2026-09-25 至 2026-09-30）**内必须完成在 5.71-5.81 元的缩量承接确认",
    ),
    (
        "000725.SZ",
        "2026-09-24",
        5.81,
        "2026-09-25",
        "- 重点观察未来 **1-3 个交易日（2026-09-25 至 09-29）** 在 5.71 - 5.81 元区间的量能承接状态",
    ),
    (
        "600519.SH",
        "2026-07-08",
        1206.5,
        "2026-07-13",
        "- **未来 1-3 个交易日**（至 2026-07-13 前），重点观察能否放量突破 1206.50 元中轨天花板",
    ),
    (
        "600519.SH",
        "2026-06-10",
        1260.0,
        "2026-06-15",
        "- 未来 **2-3 个交易日（截至 2026-06-15）** 为多头能否突破 1251-1260 元阻力带的关键窗口",
    ),
    (
        "600276.SH",
        "2026-06-10",
        48.5,
        "2026-06-15",
        "- **时间窗口**：未来 1-3 个交易日（至 2026-06-15 前后）将直接检验 47.60-48.50 元阻力带的承接与抛压",
    ),
    (
        "000333.SZ",
        "2026-06-10",
        79.0,
        "2026-06-11",
        "- 未来 **2-3 个交易日（2026-06-11 至 2026-06-13）** 内需完成对 79.00 元一线的支撑有效性确认",
    ),
    (
        "601899.SH",
        "2026-06-10",
        26.47,
        "2026-06-15",
        "- 未来 1-3 个交易日（截至 2026-06-15 盘中）必须完成对 26.47 元支撑有效性的量能测试",
    ),
    (
        "002594.SZ",
        "2026-06-10",
        91.5,
        "2026-06-11",
        "- **量价企稳验证条件**：在未来 **1-3 个交易日（2026-06-11 至 2026-06-15）** 内，必须出现单日成交量放大至 4000 万股以上（量比 > 1.1）且带有显著下影线的放量止跌阳线（锤头线或吸收长阳），并有效站稳 91.50 元上方，方可确认“卖压衰竭”向“需求进场”转化",
    ),
    (
        "002594.SZ",
        "2026-06-10",
        90.0,
        "2026-06-15",
        "- **明确时间窗口**：未来 **3 个交易日（至 2026-06-15）**为确认 90.00 元整数关口真假破位的关键窗口",
    ),
    (
        "001979.SZ",
        "2026-06-10",
        8.42,
        "2026-06-11",
        "- 核心确认窗口在未来 **2-4 个交易日**（即 2026-06-11 至 2026-06-16 期间）需完成对 8.28-8.42 元阻力区的主动量能发起",
    ),
    (
        "600036.SH",
        "2026-07-22",
        38.4,
        "2026-07-25",
        "- **观察确认窗口为未来 1-3 个交易日（截至 2026-07-25）**，必须在该窗口内完成对 38.40 元地板位的回踩巩固",
    ),
    (
        "600519.SH",
        "2026-07-22",
        1300.0,
        "2026-07-23",
        "- 未来 **2 至 3 个交易日（即 2026-07-23 至 2026-07-25）** 为核心观察窗，需在此窗口内观察 1300 元回踩测试的成交量收敛情况",
    ),
    (
        "300750.SZ",
        "2026-07-22",
        367.0,
        "2026-07-25",
        "- **验证时间窗口**：未来 **1 - 3 个交易日（至 2026-07-25 前）** 必须完成对 367 元一线的量能承接测试",
    ),
    (
        "000063.SZ",
        "2026-07-08",
        36.72,
        "2026-07-09",
        "- **2026-07-09 至 2026-07-13（未来 1-3 个交易日内）**：必须给出针对 36.72 元阻力带的有效放量站稳或缩量回踩确认，不可在高位无量横盘",
    ),
    (
        "600760.SH",
        "2026-07-08",
        38.6,
        "2026-07-09",
        "- **时间窗口**：未来 **1 - 3 个交易日（即 2026-07-09 至 2026-07-13）**将面临前低 38.60 元的测试变盘窗口",
    ),
    (
        "002027.SZ",
        "2026-07-08",
        4.6,
        "2026-07-09",
        "- 未来 2-3 个交易日（2026-07-09 至 2026-07-13）为关键量能确认窗，需观察是否在 4.60 元附近实现地量止跌企稳",
    ),
    (
        "603288.SH",
        "2026-06-10",
        34.62,
        "2026-06-11",
        "- 未来 **1-3 个交易日（2026-06-11 至 2026-06-15）** 是检验 34.62 元阻力与 33.80 元支撑有效性的关键时间窗",
    ),
    (
        "600760.SH",
        "2026-06-10",
        41.5,
        "2026-06-17",
        "- 未来 **3-5 个交易日（至 2026-06-17）** 内，盘面需在 40.00–41.50 元区间完成量能企稳或二次测试确认",
    ),
]


def _refs_for_value(result, value):
    return [r for r in result["price_refs"] if r["value"] == value]


@pytest.mark.parametrize(
    "symbol,trade_date,value,expected_window_end,sentence",
    WINDOW_END_FIXTURE,
    ids=[f"{sym}-{td}-{v}" for sym, td, v, _w, _s in WINDOW_END_FIXTURE],
)
def test_window_sentence_date_moves_to_window_end_not_as_of(
    symbol, trade_date, value, expected_window_end, sentence
):
    """19 例原句回归：句内晚于 cutoff 的日期 → window_end + as_of_after_cutoff
    标记；as_of 不再晚于 trade_date（技术报告兜底为 cutoff）。"""
    result = build_price_ref_registry(
        {"volume_price_report": sentence}, cutoff=trade_date
    )
    refs = _refs_for_value(result, value)
    assert refs, f"fixture {symbol} {trade_date} value={value} must be registered"

    # 对该值的所有 ref：as_of 不得晚于 cutoff；window_end 落原前瞻日期；
    # flags 带 as_of_after_cutoff 标记。
    for ref in refs:
        as_of = ref.get("as_of")
        if as_of is not None:
            assert as_of <= trade_date, (
                f"{symbol} {trade_date} value={value}: as_of {as_of} "
                f"still later than cutoff"
            )
    # 至少一条 ref 记上了预期的 window_end + 异常标记。
    assert any(
        r.get("window_end") == expected_window_end
        and AS_OF_AFTER_CUTOFF_FLAG in (r.get("flags") or [])
        for r in refs
    ), (
        f"{symbol} {trade_date} value={value}: expected a ref with "
        f"window_end={expected_window_end} + {AS_OF_AFTER_CUTOFF_FLAG}; "
        f"got {refs}"
    )


def test_no_ref_has_as_of_later_than_cutoff():
    """整批 19 例聚合断言：任何 ref.as_of 都不得晚于各自 cutoff。"""
    for symbol, trade_date, _v, _w, sentence in WINDOW_END_FIXTURE:
        result = build_price_ref_registry(
            {"volume_price_report": sentence}, cutoff=trade_date
        )
        for ref in result["price_refs"]:
            as_of = ref.get("as_of")
            if as_of is None:
                continue
            assert as_of <= trade_date, (
                f"{symbol} {trade_date}: ref {ref['ref_id']} as_of {as_of} "
                f"> cutoff"
            )


def test_window_end_flag_name_is_canonical():
    """异常标记名为 ``as_of_after_cutoff``，可被下游审计/前视检查消费。"""
    result = build_price_ref_registry(
        {
            "volume_price_report": (
                "未来 1-3 个交易日（至 2026-07-13 前），"
                "重点观察能否放量突破 1206.50 元中轨天花板。"
            )
        },
        cutoff="2026-07-08",
    )
    flagged = [
        r
        for r in result["price_refs"]
        if AS_OF_AFTER_CUTOFF_FLAG in (r.get("flags") or [])
    ]
    assert flagged, "expected at least one ref flagged as_of_after_cutoff"
    for r in flagged:
        assert r.get("window_end") == "2026-07-13"
        # 技术报告路径下 as_of 回落为 cutoff。
        assert r["as_of"] == "2026-07-08"


def test_past_date_still_lands_in_as_of_not_window_end():
    """句内日期 ≤ cutoff 仍写 as_of，不进入 window_end 分流。"""
    result = build_price_ref_registry(
        {
            "news_report": (
                "2026-05-21 今日大宗交易成交 78.61 元，较当日收盘 84.03 元折价。"
            )
        },
        cutoff="2026-05-22",
    )
    for ref in result["price_refs"]:
        assert "window_end" not in ref
        assert AS_OF_AFTER_CUTOFF_FLAG not in (ref.get("flags") or [])


def test_construction_future_price_reference_still_detectable():
    """真前瞻引用（未来某日的目标价/触发价）仍能被标记——句内日期晚于
    cutoff 时统一改写 window_end + as_of_after_cutoff；gate 的
    pass/blocked 边界不变（missing_as_of 之类既有规则照常适用）。"""
    result = build_price_ref_registry(
        {
            "trader_investment_plan": (
                "2026-08-27 及之后：价格在 17.00 - 17.30 元企稳且不破 16.85 元，"
                "补齐剩余 10% 仓位至 20% 上限"
            )
        },
        cutoff="2026-08-21",
    )
    refs = result["price_refs"]
    assert refs, "trader_investment_plan must register refs"
    # 句内日期 2026-08-27 > cutoff 2026-08-21 → 所有该句 ref 的 as_of 不落
    # 该未来日期；至少一条记 window_end + 异常标记。
    assert all(
        (r.get("as_of") is None or r["as_of"] <= "2026-08-21") for r in refs
    ), "no ref may carry as_of > cutoff"
    assert any(
        r.get("window_end") == "2026-08-27"
        and AS_OF_AFTER_CUTOFF_FLAG in (r.get("flags") or [])
        for r in refs
    )
