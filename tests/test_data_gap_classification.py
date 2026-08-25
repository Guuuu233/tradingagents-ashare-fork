"""Tests for P2-10.4: data_gaps structural vs operational classification."""
from __future__ import annotations

from unittest.mock import patch

from tradingagents.dataflows.trade_calendar import SNAPSHOT_ONLY_REFUSAL
from tradingagents.graph.data_collector import (
    _build_data_failure_ledger,
    _build_source_provenance,
    _fetch_all,
)


def test_northbound_institutional_stoppage_classified_as_structural():
    """北向/陆股通个股每日持股制度性停更必须归类为 structural。"""
    results = {
        "northbound_flow": (
            "【数据获取失败】北向资金持股变动 — 原因：沪深港通个股每日持股明细自 2024 年 8 月起停止披露，"
            "本项制度性停更不可用。如需北向数据请使用季度持股口径，注意频率为季度而非每日。 "
            "(来源: akshare.stock_hsgt_individual_em)\n"
            "该项分析不可用，请在报告中标注\"北向资金持股变动未排查/获取失败\"，不要基于记忆推测。\n"
        )
    }
    ledger = _build_data_failure_ledger(results)
    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["source"] == "northbound_flow"
    assert entry["gap_class"] == "structural"
    assert entry["status"] == "unavailable"
    assert "停止披露" in entry["gap"] or "unavailable" in entry["gap"]

    prov = _build_source_provenance(results, "2026-08-15", daily_as_of="2026-08-15")
    assert prov["northbound_flow"]["gap_class"] == "structural"
    assert prov["northbound_flow"]["status"] == "unavailable"
    assert "gap" in prov["northbound_flow"]


def test_historical_snapshot_refusal_classified_as_structural():
    """历史日分析触发 snapshot_historical_refusal 时必须归类为 structural。"""
    results = {
        "share_pledge": f"【数据获取失败】股权质押（全市场快照）：{SNAPSHOT_ONLY_REFUSAL}",
        "fund_flow_board": f"【数据获取失败】板块资金流向（即时）：{SNAPSHOT_ONLY_REFUSAL}",
        "hot_stocks": f"【数据获取失败】雪球热搜：{SNAPSHOT_ONLY_REFUSAL}",
    }
    ledger = _build_data_failure_ledger(results)
    assert len(ledger) == 3
    for entry in ledger:
        assert entry["gap_class"] == "structural"
        assert entry["status"] == "refused"
        assert "refused" in entry["reason"] or "refused" in entry["gap"] or "拒绝" in entry["reason"]

    prov = _build_source_provenance(results, "2025-05-15", daily_as_of="2025-05-15")
    for source in ("share_pledge", "fund_flow_board", "hot_stocks"):
        assert prov[source]["gap_class"] == "structural"
        assert prov[source]["status"] == "refused"
        assert "gap" in prov[source]


def test_operational_failures_classified_as_operational():
    """传输超时、Token 异常、空表、接口异常必须归类为 operational。"""
    results = {
        "news": "news 数据拉取超时（>300s），本次分析跳过该数据源",
        "global_indices": "【数据获取失败】global_indices：Token 认证失败/网络超时 (来源: cn_akshare)",
        "balance_sheet": "【数据获取失败】财务三大表调用异常：empty dataframe",
        "stock_data": "【数据获取失败】600519 在 2026-08-15 无有效完整日线数据（缺列/非法日期），本项不可用。",
    }
    ledger = _build_data_failure_ledger(results)
    assert len(ledger) == 4
    for entry in ledger:
        assert entry["gap_class"] == "operational"
        assert entry["status"] in ("timeout", "failed", "unavailable")

    prov = _build_source_provenance(results, "2026-08-15", daily_as_of=None)
    for source in ("news", "global_indices", "balance_sheet", "stock_data"):
        assert prov[source]["gap_class"] == "operational"
        assert prov[source]["status"] in ("timeout", "failed", "unavailable")


def test_resident_fault_count_only_includes_operational():
    """常驻故障计数只含 operational，structural 不计入常驻故障但保留文案与 ledger。"""
    results = {
        # 3 个 structural 拒绝/停更
        "northbound_flow": "【数据获取失败】沪深港通个股每日持股明细自 2024 年 8 月起停止披露，本项制度性停更不可用。",
        "share_pledge": f"【数据获取失败】股权质押：{SNAPSHOT_ONLY_REFUSAL}",
        "fund_flow_board": f"【数据获取失败】板块资金流向：{SNAPSHOT_ONLY_REFUSAL}",
        # 2 个 operational 故障
        "news": "news 数据拉取超时（>300s）",
        "global_news": "【数据获取失败】全球新闻接口连接超时",
    }
    ledger = _build_data_failure_ledger(results)
    assert len(ledger) == 5

    operational_gaps = [e for e in ledger if e.get("gap_class") == "operational"]
    structural_gaps = [e for e in ledger if e.get("gap_class") == "structural"]

    assert len(operational_gaps) == 2
    assert len(structural_gaps) == 3
    # 目标 <= 5 仅约束 operational
    assert len(operational_gaps) <= 5

    # structural 仍写入 ledger/gap 文案，未被删除也未被伪造成 success
    for entry in structural_gaps:
        assert entry["status"] in ("unavailable", "refused")
        assert entry["gap"].startswith("【数据获取失败】")


def test_source_provenance_and_ledger_status_alignment():
    """五战场数据清单与 provenance 的 source 名对齐：同一 source 不得一边叫可用一边进 gap。"""
    results = {
        "news": "## 新闻（最新发布时间：2026-08-10 15:00:00）\n有效新闻正文",
        "global_news": "## 全球新闻（无日期信息）",
        "northbound_flow": "【数据获取失败】停止披露",
    }
    prov = _build_source_provenance(results, "2026-08-11", daily_as_of="2026-08-11")

    # news 有效且有合法日期 -> status 为 available，无 gap，无 gap_class
    assert prov["news"]["status"] == "available"
    assert "gap" not in prov["news"]
    assert prov["news"].get("gap_class") is None

    # global_news 缺少可验证日期 -> 必须进入 gap，status 绝不能是 available
    assert prov["global_news"]["status"] == "unavailable"
    assert "gap" in prov["global_news"]
    assert prov["global_news"]["gap_class"] == "operational"

    # northbound_flow 结构性停更 -> status 为 unavailable，有 gap，gap_class 为 structural
    assert prov["northbound_flow"]["status"] == "unavailable"
    assert "gap" in prov["northbound_flow"]
    assert prov["northbound_flow"]["gap_class"] == "structural"

    # 验证没有任一条目同时满足 status == "available" 且含有 gap
    for src, p in prov.items():
        if p.get("status") == "available":
            assert "gap" not in p, f"{src} 状态为 available 但包含了 gap"
            assert p.get("gap_class") is None, f"{src} 状态为 available 但包含了 gap_class"
        else:
            assert "gap" in p, f"{src} 状态为非 available ({p.get('status')}) 但缺少 gap"
            assert p.get("gap_class") in ("structural", "operational"), f"{src} 缺少有效 gap_class"


def test_date_parsing_failure_never_defaults_to_today():
    """解析失败不得填今天。"""
    prov = _build_source_provenance(
        {"global_news": "## 全球新闻（无可解析日期文本）"},
        "2026-08-11",
        daily_as_of="2026-08-11",
    )
    assert prov["global_news"]["as_of"] is None
    assert prov["global_news"]["actual_as_of"] is None
    assert prov["global_news"]["status"] == "unavailable"
    assert prov["global_news"]["gap_class"] == "operational"


def test_fetch_all_historical_date_ledger_and_provenance_integration():
    """在历史日期全量抓取下，验证 structural vs operational 分类与常驻故障计数。"""
    from tradingagents.graph import data_collector

    historical_refusal_share_pledge = f"【数据获取失败】股权质押（全市场快照）：{SNAPSHOT_ONLY_REFUSAL}"
    historical_refusal_fund_flow_board = f"【数据获取失败】板块资金流向（即时）：{SNAPSHOT_ONLY_REFUSAL}"
    northbound_stoppage = (
        "【数据获取失败】北向资金持股变动 — 原因：沪深港通个股每日持股明细自 2024 年 8 月起停止披露，"
        "本项制度性停更不可用。 (来源: akshare.stock_hsgt_individual_em)"
    )
    valid_news = "## 600519 新闻（最新发布时间：2025-05-14 10:00:00）：\n新闻内容"
    valid_daily = (
        "# requested-as-of: 2025-05-15\n"
        "# as-of: 2025-05-15\n"
        "Date,Open,High,Low,Close,Volume\n"
        "2025-05-14,100,105,99,102,1000\n"
        "2025-05-15,102,103,101,102,1000\n"
    )

    def mock_safe(tool, payload):
        tool_name = getattr(tool, "name", str(tool))
        if "get_northbound_flow" in tool_name or tool == data_collector.get_northbound_flow:
            return northbound_stoppage
        if "get_share_pledge" in tool_name or tool == data_collector.get_share_pledge:
            return historical_refusal_share_pledge
        if "get_board_fund_flow" in tool_name or tool == data_collector.get_board_fund_flow:
            return historical_refusal_fund_flow_board
        if "get_stock_data" in tool_name or tool == data_collector.get_stock_data:
            return valid_daily
        if "get_news" in tool_name or tool == data_collector.get_news:
            return valid_news
        if "get_global_indices" in tool_name or tool == data_collector.get_global_indices:
            return "【数据获取失败】global_indices：Token 认证超时"
        return "【数据获取失败】该项测试未提供"

    with patch.object(data_collector, "_safe", side_effect=mock_safe), \
         patch.object(data_collector, "FETCH_ALL_TIMEOUT", 2):
        pool = data_collector._fetch_all("600519", "2025-05-15")

    market_context = pool["market_data_context"]
    ledger = market_context["data_failure_ledger"]
    provenance = market_context["source_provenance"]

    # 验证每一个 ledger 条目都有合法 gap_class
    for entry in ledger:
        assert entry.get("gap_class") in ("structural", "operational"), f"Entry missing gap_class: {entry}"

    # 验证 northbound_flow, share_pledge, fund_flow_board 属于 structural
    structural_sources = {e["source"] for e in ledger if e.get("gap_class") == "structural"}
    assert "northbound_flow" in structural_sources
    assert "share_pledge" in structural_sources
    assert "fund_flow_board" in structural_sources

    # 验证 global_indices 属于 operational
    operational_sources = {e["source"] for e in ledger if e.get("gap_class") == "operational"}
    assert "global_indices" in operational_sources

    # 验证 provenance 中有对应 gap_class 且状态对齐
    assert provenance["northbound_flow"]["gap_class"] == "structural"
    assert provenance["share_pledge"]["gap_class"] == "structural"
    assert provenance["fund_flow_board"]["gap_class"] == "structural"
    assert provenance["global_indices"]["gap_class"] == "operational"

    # stock_data 有效日线 -> status available，无 gap，无 gap_class
    assert provenance["stock_data"]["status"] == "available"
    assert "gap" not in provenance["stock_data"]
    assert provenance["stock_data"].get("gap_class") is None

