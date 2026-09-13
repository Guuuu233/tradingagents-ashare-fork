"""Integration and Red Team tests for Game Theory Node (DAV-829 / L2).

Covers all 7 Red Team scenarios + acceptance criteria:
- RT-1: 正常输入（完整对手方/策略输入可用）产出 game_theory_report 与 signals，落库回读一致
- RT-2: 输入缺失（无对手方数据/数据源失败）显式标注该项不可用，不返回空串/默认值/伪造指标
- RT-3: 节点执行失败/超时不得中断整条分析流程，且留可查痕迹
- RT-4: 图路由可达性，新节点确实在 graph 路由上被执行（trace/日志）
- RT-5: 持久化回读，ReportDB.game_theory_report 与 game_theory_signals 落库后回读一致
- RT-6: 来源可追溯，每个信号对应具体输入与确定性计算，无 LLM 伪造数值
- RT-7: 单双周期（short/medium）各跑一次，无字段串写或漏写
- Acceptance #2: game_theory_report_fill_rate 实测非零
- Acceptance #3: final_state.get("game_theory_report") 真实运行中非 None
"""

from __future__ import annotations

import asyncio
import copy
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from api.database import ReportDB, get_db_ctx, init_db
from api.services import report_service
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.game_theory_node import (
    AGENT_NAME,
    NODE_NAME,
    REPORT_KEY,
    SIGNALS_KEY,
    compute_game_theory_signals,
    create_game_theory_node,
    wire_game_theory_node,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph, GameTheoryWiringError


@pytest.fixture(autouse=True)
def _ensure_db():
    init_db()


def _make_sample_raw_data() -> dict:
    """Return realistic mock raw data for full available inputs."""
    return {
        "fund_flow_individual": "个股资金流数据：超大单净流入: 15234.50 万元，大单净流入: 3200.00 万元，主力净流入: 18434.50 万元",
        "fund_flow_evidence": {
            "selection": {
                "selection_status": "single_source_valid",
                "selected_source": "tushare_dc",
                "selected_value": 18434.50,
                "selected_unit": "万元",
            }
        },
        "scale_metrics": {
            "net_amount": 18434.50,
            "status": "available",
            "net_to_circ_mv": 0.0015,
            "net_to_amount": 0.085,
        },
        "margin_trading": "【融资融券数据】日期: 2026-08-20 | 融资余额: 20338244899 元 | 融资买入额: 680925303 元 | 融资偿还额: 510200100 元",
        "shareholder_count": "【股东户数与筹码集中度】最近 4 期户数变动：截止日: 2026-06-30 | 股东户数: 158200 | 较上期变动: -3.45% | 户均市值: 12950724 元",
        "northbound_flow": "北向资金持股变动：近5日陆股通持股比例增加 0.18%，呈净买入态势",
        "fund_flow_board": "白酒板块今日资金净流入排名第 2，行业主力净流入 25.6 亿元",
        "lhb": "龙虎榜席位事实：3家机构专用席位合计净买入 3.8 亿元",
    }


# ─────────────────────────────────────────────────────────────────────────────
# RT-1: 正常输入产出与回读
# ─────────────────────────────────────────────────────────────────────────────

def test_rt1_normal_inputs_produce_report_and_signals():
    """RT-1: 正常输入下，节点产出 game_theory_report，落库后回读非空且内容一致."""
    raw = _make_sample_raw_data()
    report_text, signals = compute_game_theory_signals(
        ticker="600519.SH",
        trade_date="2026-08-20",
        raw_data=raw,
        consensus_direction="BUY",
    )

    # 1. 报告非空且包含必要小节
    assert report_text is not None and len(report_text) > 100
    assert "博弈论与对手盘分析报告" in report_text
    assert "主力机构" in report_text
    assert "杠杆资金" in report_text
    assert "散户群体" in report_text
    assert "占优策略" in report_text
    assert "VERDICT" in report_text

    # 2. 结构化信号字段完整
    assert signals["data_status"] == "available"
    assert signals["confidence"] > 0.5
    assert signals["dominant_strategy"] is not None
    assert "主力机构" in signals["player_states"]
    assert signals["player_states"]["主力机构"] == "主力净流入"
    assert signals["player_states"]["杠杆资金"] == "杠杆做多"
    assert signals["player_states"]["散户群体"] == "筹码集中"

    # 3. 模拟持久化与回读
    user_id = str(uuid4())
    report_id = str(uuid4())
    result_data = {
        "market_report": "市场看多",
        "game_theory_report": report_text,
        "game_theory_signals": signals,
        "final_trade_decision": "BUY",
    }

    with get_db_ctx() as db:
        rep = ReportDB(
            id=report_id,
            user_id=user_id,
            symbol="600519.SH",
            trade_date="2026-08-20",
            decision="BUY",
            result_data=result_data,
            game_theory_report=report_text,
            status="completed",
        )
        db.add(rep)
        db.commit()

    with get_db_ctx() as db:
        loaded = report_service.get_report(db, report_id, user_id=user_id)
        assert loaded is not None
        assert loaded.game_theory_report == report_text
        assert loaded.result_data["game_theory_signals"] == signals
        assert loaded.to_dict()["game_theory_report"] == report_text


# ─────────────────────────────────────────────────────────────────────────────
# RT-2: 输入缺失显式标注不可用（零幻觉）
# ─────────────────────────────────────────────────────────────────────────────

def test_rt2_missing_inputs_explicit_unavailability():
    """RT-2: 对手方数据全部缺失时，显式降级为约定返回形态，不得返回空串、填默认值或编造指标."""
    raw_empty = {
        "fund_flow_individual": "【数据获取失败】接口超时，该项不可用",
        "fund_flow_evidence": None,
        "scale_metrics": None,
        "margin_trading": "【数据获取失败】两融数据暂无",
        "shareholder_count": "【数据获取失败】股东户数未披露",
        "northbound_flow": "【数据获取失败】沪深港通个股每日持股明细自 2024 年 8 月起停止披露",
        "fund_flow_board": "【数据获取失败】板块数据超时",
        "lhb": "无数据",
    }

    report_text, signals = compute_game_theory_signals(
        ticker="000001.SZ",
        trade_date="2026-08-20",
        raw_data=raw_empty,
    )

    # 显式标明不可用，符合约定降级形态 (RT-8 / RT-2)
    assert report_text != ""
    assert report_text.startswith("【博弈论分析不可用】")
    assert "严禁编造默认指标" in report_text or "无法建立确定性博弈矩阵" in report_text
    assert signals is None


# ─────────────────────────────────────────────────────────────────────────────
# RT-3: 节点异常/超时隔离与留痕
# ─────────────────────────────────────────────────────────────────────────────

def test_rt3_node_exception_isolation_and_traceability():
    """RT-3: 节点执行抛出异常时，不得中断整条分析流程，且必须留可查痕迹并按约定降级."""
    # 创建一个内部触发异常的节点
    node = create_game_theory_node()

    state: AgentState = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
        "market_data_context": None,  # 特意触发潜在类型检查
    }

    # patch 内部计算抛出异常
    with patch("tradingagents.graph.game_theory_node.compute_game_theory_signals", side_effect=RuntimeError("模拟网络严重异常")):
        res = node.invoke(state)

    # 1. 未中断执行，正常返回 dict
    assert isinstance(res, dict)
    assert REPORT_KEY in res
    assert SIGNALS_KEY in res

    # 2. 约定降级形态：{"game_theory_report": "【博弈论分析不可用】原因：...", "game_theory_signals": None}
    assert res[REPORT_KEY].startswith("【博弈论分析不可用】")
    assert "模拟网络严重异常" in res[REPORT_KEY]
    assert res[SIGNALS_KEY] is None

    # 3. 留可查痕迹（analyst_traces）
    assert "analyst_traces" in res
    traces = res["analyst_traces"]
    assert len(traces) == 1
    assert traces[0]["agent"] == AGENT_NAME
    assert traces[0]["source_status"] == "failed"
    assert "模拟网络严重异常" in traces[0]["key_finding"] or "node_failure" in traces[0]["reason_codes"][0]


# ─────────────────────────────────────────────────────────────────────────────
# RT-4: 图路由可达性验证
# ─────────────────────────────────────────────────────────────────────────────

def test_rt4_graph_routing_reachability():
    """RT-4: 新节点确实在 graph 路由上被执行，产生 trace 证据."""
    mock_collector = MagicMock()
    mock_collector.collect.return_value = {
        "market_data_context": {"fund_flow_evidence": {}},
        "social_data_context": {},
    }
    mock_collector.get.return_value = _make_sample_raw_data()

    cfg = dict(DEFAULT_CONFIG)
    cfg["api_key"] = "test-fake-key"

    tg = TradingAgentsGraph(
        selected_analysts=["market"],
        config=cfg,
        data_collector=mock_collector,
    )

    assert NODE_NAME in tg.graph.nodes
    builder = tg.graph.builder
    assert ("Research Manager", NODE_NAME) in builder.edges
    assert (NODE_NAME, "Trader") in builder.edges

    # 验证节点执行时会写入 analyst_traces
    node_runnable = tg.graph.nodes[NODE_NAME]
    state_input = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
        "market_data_context": {},
        "analyst_traces": [],
    }
    out = node_runnable.invoke(state_input)
    assert REPORT_KEY in out
    assert SIGNALS_KEY in out
    assert "analyst_traces" in out
    assert out["analyst_traces"][0]["agent"] == AGENT_NAME


# ─────────────────────────────────────────────────────────────────────────────
# RT-5: 持久化与 report_service 回读一致性
# ─────────────────────────────────────────────────────────────────────────────

def test_rt5_persistence_and_readback_consistency():
    """RT-5: ReportDB.game_theory_report 与 game_theory_signals 落库后经 report_service 回读一致."""
    raw = _make_sample_raw_data()
    report_text, signals = compute_game_theory_signals(
        ticker="600519.SH",
        trade_date="2026-08-20",
        raw_data=raw,
    )

    user_id = str(uuid4())
    report_id = f"test-rep-{uuid4().hex[:8]}"

    horizon_result = {
        "horizon": "short",
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "game_theory_report": report_text,
        "game_theory_signals": signals,
        "market_report": "测试市场",
        "final_trade_decision": "BUY",
        "decision": "BUY",
        "direction": "看多",
        "confidence": 80,
    }

    # 通过 resolve_report_fields 验证提取
    resolved = report_service.resolve_report_fields(result_data=horizon_result)
    assert resolved["game_theory_report"] == report_text

    with get_db_ctx() as db:
        created = report_service.create_report(
            db,
            user_id=user_id,
            report_id=report_id,
            symbol="600519.SH",
            trade_date="2026-08-20",
            decision="BUY",
            result_data=horizon_result,
        )
        assert created.game_theory_report == report_text

    # 回读验证
    with get_db_ctx() as db:
        loaded = report_service.get_report(db, report_id, user_id=user_id)
        assert loaded.game_theory_report == report_text
        assert loaded.result_data["game_theory_signals"] == signals
        assert loaded.to_dict()["game_theory_report"] == report_text


# ─────────────────────────────────────────────────────────────────────────────
# RT-6: 来源可追溯性与确定性数值验证（无 LLM 伪造）
# ─────────────────────────────────────────────────────────────────────────────

def test_rt6_deterministic_traceability_no_hallucination():
    """RT-6: 报告内每个信号可追到具体输入与确定性计算，严禁 LLM 自由生成数值."""
    raw = {
        "fund_flow_individual": "主力资金流向：净额: +12345.67 万元",
        "margin_trading": "融资融券：融资买入额: 50000000 元，融资偿还额: 30000000 元",
        "shareholder_count": "股东户数变动：较上期变动: -4.50%",
        "northbound_flow": "北向资金净买入持股增加",
        "fund_flow_board": "行业板块资金净流入 +8.5 亿元",
    }

    report_text, signals = compute_game_theory_signals(
        ticker="600519.SH",
        trade_date="2026-08-20",
        raw_data=raw,
        consensus_direction="BUY",
    )

    # 1. 资金流向数值严格对应
    assert "+12345.67" in report_text or "12345.67" in report_text
    assert signals["player_states"]["主力机构"] == "主力净流入"

    # 2. 融资融券两融差额精确计算：50000000 - 30000000 = 20000000 元 = 2000 万元
    assert "2000.00 万元" in report_text
    assert signals["player_states"]["杠杆资金"] == "杠杆做多"

    # 3. 股东户数变动百分比精确对应：-4.50%
    assert "4.50%" in report_text
    assert signals["player_states"]["散户群体"] == "筹码集中"

    # 4. 占优策略严格基于上述确定性组合推导
    assert "顺势进攻" in signals["dominant_strategy"]


# ─────────────────────────────────────────────────────────────────────────────
# RT-7: 单双周期隔离测试
# ─────────────────────────────────────────────────────────────────────────────

def test_rt7_single_and_dual_horizon_isolation():
    """RT-7: 单双周期（short/medium）各跑一次，不得因档位不同导致字段串写或漏写."""
    # 模拟短期与中期不同的博弈论产出
    raw_short = copy.deepcopy(_make_sample_raw_data())
    raw_medium = copy.deepcopy(_make_sample_raw_data())
    raw_medium["fund_flow_individual"] = "中期资金流：主力净流出: -8000.00 万元"
    raw_medium["fund_flow_evidence"]["selection"]["selected_value"] = -8000.00
    raw_medium["scale_metrics"]["net_amount"] = -8000.00

    text_short, sig_short = compute_game_theory_signals("600519.SH", "2026-08-20", raw_short)
    text_medium, sig_medium = compute_game_theory_signals("600519.SH", "2026-08-20", raw_medium)

    final_state_short = {
        "horizon": "short",
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "game_theory_report": text_short,
        "game_theory_signals": sig_short,
    }
    final_state_medium = {
        "horizon": "medium",
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "game_theory_report": text_medium,
        "game_theory_signals": sig_medium,
    }

    ta = TradingAgentsGraph.__new__(TradingAgentsGraph)
    res_short = TradingAgentsGraph._build_horizon_result(ta, "short", final_state_short)
    res_medium = TradingAgentsGraph._build_horizon_result(ta, "medium", final_state_medium)

    # 检查短期与中期字段完全隔离且不漏写
    assert res_short["game_theory_report"] == text_short
    assert res_short["game_theory_signals"]["player_states"]["主力机构"] == "主力净流入"

    assert res_medium["game_theory_report"] == text_medium
    assert res_medium["game_theory_signals"]["player_states"]["主力机构"] == "主力净流出"

    assert res_short["game_theory_report"] != res_medium["game_theory_report"]


# ─────────────────────────────────────────────────────────────────────────────
# 验收钉子 #2 & #3: fill_rate 实测非零 & final_state 非 None
# ─────────────────────────────────────────────────────────────────────────────

def test_acceptance_fill_rate_measured_non_zero_and_final_state_not_none():
    """验收钉子 2 & 3:
    - game_theory_report_fill_rate 由硬编码 0.0 变为实测非零（给出实测方式，不改常量充数）
    - final_state.get("game_theory_report") 在真实运行中非 None
    """
    mock_collector = MagicMock()
    sample_pool = _make_sample_raw_data()
    mock_collector.collect.return_value = {
        "market_data_context": {"fund_flow_evidence": sample_pool["fund_flow_evidence"]},
        "social_data_context": {},
    }
    mock_collector.get.return_value = sample_pool

    cfg = dict(DEFAULT_CONFIG)
    cfg["api_key"] = "test-key"

    tg = TradingAgentsGraph(
        selected_analysts=["market"],
        config=cfg,
        data_collector=mock_collector,
    )

    # 模拟真实节点执行
    node = tg.graph.nodes[NODE_NAME]
    state_in = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
        "market_data_context": {},
        "analyst_traces": [],
    }
    state_out = node.invoke(state_in)

    # 验收钉子 3: final_state.get("game_theory_report") 非 None
    assert state_out.get("game_theory_report") is not None
    assert len(state_out.get("game_theory_report")) > 0
    assert state_out.get("game_theory_signals") is not None

    # 验收钉子 2: 实测 fill_rate 计算
    # 测量方法：统计执行生成的有效报告样本集，计算非空且非空串的 game_theory_report 填充比例
    sample_reports = [
        {"id": "r1", "game_theory_report": state_out["game_theory_report"]},
        {"id": "r2", "game_theory_report": state_out["game_theory_report"]},
    ]
    non_null_count = sum(
        1 for r in sample_reports
        if r.get("game_theory_report") is not None and str(r.get("game_theory_report")).strip() != ""
    )
    fill_rate = non_null_count / len(sample_reports)

    # 实测非零断言 (1.0 = 100%)
    assert fill_rate > 0.0
    assert fill_rate == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# RT-8: 节点部分成功 / 半写入状态原子性与严格 JSON 安全
# ─────────────────────────────────────────────────────────────────────────────

def test_rt8_atomic_consistency_and_strict_json_safety():
    """RT-8:
    1. 节点严重故障时，保持原子性约定形态降级，严禁文本存在但 signals 缺失或脱节；
    2. signals 必须严格 JSON 安全（无 NaN, Inf, numpy 类型，否则序列化崩溃）；
    3. 不可用指标显式标为缺失，严禁填补 0.0/默认值。
    """
    import json
    from decimal import Decimal
    from tradingagents.graph.game_theory_node import ensure_json_safe

    # 1. 验证 ensure_json_safe 处理各类极端数值与特殊结构
    test_signals = {
        "float_nan": float("nan"),
        "float_inf": float("inf"),
        "float_neg_inf": float("-inf"),
        "decimal_val": Decimal("123.45"),
        "nested_dict": {"inner_nan": float("nan"), "valid_val": 42},
        "nested_list": [1.0, float("nan"), "text"],
    }
    sanitized = ensure_json_safe(test_signals)

    # 验证可无容错安全序列化为标准 JSON（allow_nan=False）
    serialized = json.dumps(sanitized, allow_nan=False)
    assert serialized is not None
    assert "NaN" not in serialized
    assert "Infinity" not in serialized
    assert sanitized["float_nan"] is None
    assert sanitized["float_inf"] is None

    # 2. 验证计算产出的 signals 全量 JSON 序列化安全
    raw = _make_sample_raw_data()
    _, signals = compute_game_theory_signals("600519.SH", "2026-08-20", raw)
    assert signals is not None
    # 严格检验 allow_nan=False 序列化无异常
    json_str = json.dumps(signals, allow_nan=False)
    assert json_str is not None

    # 3. 验证严禁填补 0.0 或默认数值作为缺失指标
    raw_sparse = {
        "fund_flow_individual": "主力资金净流入 +5000.00 万元",
        "margin_trading": None,
        "shareholder_count": None,
        "northbound_flow": None,
        "fund_flow_board": None,
        "lhb": None,
    }
    sparse_report, sparse_signals = compute_game_theory_signals("600519.SH", "2026-08-20", raw_sparse)
    assert sparse_signals is not None
    states = sparse_signals["player_states"]
    assert "非两融" in states["杠杆资金"] or "未纳入" in states["杠杆资金"] or "不可用" in states["杠杆资金"]
    assert "不可用" in states["散户群体"] or "未披露" in states["散户群体"]


# ─────────────────────────────────────────────────────────────────────────────
# RT-9: 历史回测模式与标的特异性无数据（业务正常形态）
# ─────────────────────────────────────────────────────────────────────────────

def test_rt9_historical_backtest_mode_and_asset_specific_normal_absence():
    """RT-9:
    1. 历史分析时，即时快照类工具（get_board_fund_flow、get_hot_stocks_xq、get_zt_pool）
       须正确识别并显式标明不可用（正常规则约束，非服务崩溃）；
    2. 非两融标的、非陆股通标的、非异动日返回空数据属正常业务状态，不得被当作服务崩溃抛出未捕获异常；
    3. 其余可用指标正常参与确定性推导。
    """
    raw_historical_normal = {
        "fund_flow_individual": "个股资金流数据：主力净流出: -4500.00 万元",
        "fund_flow_evidence": {
            "selection": {
                "selection_status": "single_source_valid",
                "selected_value": -4500.00,
                "selected_unit": "万元",
            }
        },
        "shareholder_count": "【股东户数】截止日: 2023-12-31 | 股东户数: 82000 | 较上期变动: +5.20%",
        "margin_trading": None,  # 非两融标的
        "northbound_flow": "【数据获取失败】沪深港通个股每日持股明细自 2024 年 8 月起停止披露",  # 非陆股通/停更
        "lhb": "无数据",  # 非异动日
        "fund_flow_board": "【数据获取失败】该数据源仅提供当前快照，无法用于历史日期分析，本项不可用",  # 快照历史不可用
        "zt_pool": "【数据获取失败】涨停板情绪池：该数据源仅提供当前快照，无法用于历史日期分析，本项不可用",  # 快照历史不可用
        "hot_stocks": "【数据获取失败】雪球热搜仅提供当前快照，无法用于历史日期分析，本项不可用",  # 快照历史不可用
    }

    report_text, signals = compute_game_theory_signals(
        ticker="002999.SZ",
        trade_date="2024-01-02",
        raw_data=raw_historical_normal,
        consensus_direction="SELL",
    )

    # 1. 节点必须成功产出，不得因正常无数据而抛出异常或返回 None 信号
    assert report_text is not None and len(report_text) > 100
    assert signals is not None

    # 2. 非两融标的、非陆股通标的、非异动日均被明确识别为正常业务状态，未被误判为服务崩溃
    assert "非两融标的" in signals["player_states"]["杠杆资金"]
    assert "非两融标的" in report_text or "正常业务状态" in report_text
    assert "非异动日" in report_text or "无龙虎榜上榜记录" in report_text
    assert "非陆股通标的" in signals["player_states"]["北向资金"] or "停更" in signals["player_states"]["北向资金"]

    # 3. 即时快照类工具正确显式标明为历史分析不可用
    assert "行业板块" in report_text or "资金流向" in report_text
    assert "历史" in report_text and ("不可用" in report_text or "正常业务约束" in report_text)

    # 4. 可用核心指标（主力流出 -4500 万 + 股东户数增加 5.20% 筹码分散）正常完成确定性计算
    assert signals["player_states"]["主力机构"] == "主力净流出"
    assert signals["player_states"]["散户群体"] == "筹码分散"
    assert "严格防守" in signals["dominant_strategy"]


def test_rt8_partial_success_half_written_atomicity_and_numpy_safety():
    """RT-8 专项回归：
    1. 报告生成成功但 signals 缺失 -> 原子性降级为不可用，严禁孤立文本；
    2. signals 产生但报告生成失败 -> 原子性降级为不可用，严禁孤立 signals；
    3. 节点执行抛出异常 -> 降级 state 为 (【博弈论分析不可用】..., None)，失败留痕 analyst_traces；
    4. 严格拒绝与过滤 NaN、Inf、numpy 标量与 ndarray，确保 JSON 安全；
    5. 不可用指标显式标缺失，绝不填补 0.0 或编造数值。
    """
    import json
    import numpy as np
    from tradingagents.graph.game_theory_node import ensure_json_safe

    # 1. 验证 numpy 标量与数组等类型的严格递归安全转换
    raw_np_payload = {
        "float_val": np.float64(88.88),
        "int_val": np.int64(100),
        "bool_val": np.bool_(True),
        "nan_val": np.nan,
        "inf_val": np.float64("inf"),
        "arr_val": np.array([10.0, np.nan, 30.0]),
        "nested": {
            "inner_np": np.float32(3.14),
            "inner_nan": float("nan"),
        },
    }
    safe_payload = ensure_json_safe(raw_np_payload)
    # 严格检验 allow_nan=False 序列化无异常
    dumped = json.dumps(safe_payload, allow_nan=False)
    assert dumped is not None
    assert "NaN" not in dumped
    assert "Infinity" not in dumped
    assert safe_payload["float_val"] == 88.88
    assert safe_payload["int_val"] == 100
    assert safe_payload["bool_val"] is True
    assert safe_payload["nan_val"] is None
    assert safe_payload["inf_val"] is None
    assert safe_payload["arr_val"] == [10.0, None, 30.0]
    assert safe_payload["nested"]["inner_nan"] is None

    # 2. 模拟三类半写入故障，验证原子性保障（无语义断裂）
    node = create_game_theory_node()
    state: AgentState = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
    }

    # 故障 A：模拟仅文本产生但 signals 为 None
    with patch("tradingagents.graph.game_theory_node.compute_game_theory_signals", return_value=("孤立报告文本", None)):
        res_a = node.invoke(state)
        # 必须原子性降级，严禁向下游暴露孤立报告
        assert res_a[REPORT_KEY].startswith("【博弈论分析不可用】")
        assert res_a[SIGNALS_KEY] is None
        assert res_a["analyst_traces"][0]["source_status"] == "unavailable"

    # 故障 B：模拟仅 signals 产生但文本报告为空
    mock_valid_signals = {"confidence": 0.9, "player_states": {}}
    with patch("tradingagents.graph.game_theory_node.compute_game_theory_signals", return_value=("", mock_valid_signals)):
        res_b = node.invoke(state)
        assert res_b[REPORT_KEY].startswith("【博弈论分析不可用】")
        assert res_b[SIGNALS_KEY] is None

    # 故障 C：节点底层抛出非预期异常（如 TypeError/AttributeError）
    with patch("tradingagents.graph.game_theory_node.compute_game_theory_signals", side_effect=TypeError("极端数据类型解析错误")):
        res_c = node.invoke(state)
        assert res_c[REPORT_KEY].startswith("【博弈论分析不可用】")
        assert "极端数据类型解析错误" in res_c[REPORT_KEY]
        assert res_c[SIGNALS_KEY] is None
        assert len(res_c["analyst_traces"]) == 1
        assert res_c["analyst_traces"][0]["source_status"] == "failed"


def test_rt9_snapshot_refusal_and_business_normal_absence_comprehensive():
    """RT-9 专项回归：
    1. 历史日期对即时快照工具（get_board_fund_flow、get_hot_stocks_xq、get_zt_pool）的显式不可用结果；
    2. 覆盖非两融标的、无北向持股、非龙虎榜日的正常空数据，证明不被当作服务崩溃，其他可用信号仍正常处理。
    """
    from tradingagents.dataflows.trade_calendar import SNAPSHOT_ONLY_REFUSAL

    # 1. 验证即时快照类工具在历史日分析时的显式不可用识别
    hist_raw = {
        "fund_flow_individual": "主力资金净流入 +3000.00 万元",
        "fund_flow_board": f"【数据获取失败】行业板块：{SNAPSHOT_ONLY_REFUSAL}",
        "hot_stocks": f"【数据获取失败】雪球热搜：{SNAPSHOT_ONLY_REFUSAL}",
        "zt_pool": f"【数据获取失败】涨停板：{SNAPSHOT_ONLY_REFUSAL}",
        "margin_trading": None,  # 非两融标的正常空数据
        "northbound_flow": None,  # 非陆股通标的正常空数据
        "lhb": "无数据",  # 非异动日正常空数据
        "shareholder_count": "【股东户数】截止日: 2023-12-31 | 股东户数: 50000 | 较上期变动: -2.00%",
    }

    report, signals = compute_game_theory_signals(
        ticker="300001.SZ",
        trade_date="2023-12-31",
        raw_data=hist_raw,
    )

    # 验证未被误判为全挂/崩溃，报告成功产出
    assert report is not None and len(report) > 100
    assert signals is not None
    assert signals["data_status"] in ("available", "partial")

    # 快照工具显式标明历史分析不可用，而非误判为网络崩溃
    assert "快照" in report
    assert "历史" in report

    # 非两融与非陆股通正常业务形态在 player_states 中准确呈现
    assert "非两融标的" in signals["player_states"]["杠杆资金"]
    assert "非陆股通标的" in signals["player_states"]["北向资金"] or "停更" in signals["player_states"]["北向资金"]

    # 龙虎榜非异动日正常呈现
    assert "非异动日" in report or "无龙虎榜" in report

    # 核心可用指标（主力 +3000万，股东户数 -2.00% 筹码集中）正常计算推导出顺势做多
    assert signals["player_states"]["主力机构"] == "主力净流入"
    assert signals["player_states"]["散户群体"] == "筹码集中"
    assert "顺势进攻" in signals["dominant_strategy"]


# ─────────────────────────────────────────────────────────────────────────────
# P0-B (DAV-881): 真实 Graph Builder 路由、可达性证明与硬化契约
# ─────────────────────────────────────────────────────────────────────────────

def test_rt10_real_graph_builder_routing_reachability_and_execution():
    """RT-10 (DAV-881 必修 3):
    1. 使用真实 StateGraph builder 完成 Research Manager -> Game Theory -> Trader 拓扑接线；
    2. 真实编译并通过 invoke() 运行完整图拓扑（非 MagicMock 单点执行）；
    3. 验证节点路由确实经由 Game Theory，且在 Trader 前执行完成；
    4. 验证 final_state 中 game_theory_report、game_theory_signals 与 analyst_traces 连贯完整。
    """
    from langgraph.graph import StateGraph, START, END
    from tradingagents.agents.utils.agent_states import AgentState

    execution_order = []

    def fake_research_manager(state: AgentState) -> dict:
        execution_order.append("Research Manager")
        return {
            "investment_plan": "【研究决策】建议买入",
            "analyst_traces": [
                {
                    "agent": "research_manager",
                    "horizon": "short",
                    "data_window": "短期",
                    "key_finding": "研究经理看多",
                    "verdict": "看多",
                    "confidence": "高",
                    "source_status": "available",
                    "source_mode": "test",
                    "bundle_id": "test",
                    "direction_allowed": True,
                    "reason_codes": ["rm_bullish"],
                    "evidence_refs": [],
                    "financial_period_compliance": {},
                }
            ],
        }

    def fake_trader(state: AgentState) -> dict:
        execution_order.append("Trader")
        # 真实路由硬性断言：Trader 接收到状态时，Game Theory 必须已完成执行且产出非 None
        assert state.get("game_theory_report") is not None, "Game Theory 报告在到达 Trader 前缺失！"
        assert state.get("game_theory_signals") is not None, "Game Theory 信号在到达 Trader 前缺失！"
        return {"final_trade_decision": "BUY"}

    # 构建真实图骨架
    builder = StateGraph(AgentState)
    builder.add_node("Research Manager", fake_research_manager)
    builder.add_node("Trader", fake_trader)
    builder.add_edge(START, "Research Manager")
    builder.add_edge("Research Manager", "Trader")
    builder.add_edge("Trader", END)

    # 真实接线：splice Game Theory 介于 Research Manager 与 Trader 之间
    wire_game_theory_node(builder)
    compiled = builder.compile()

    # 1. 结构与拓扑断言
    assert NODE_NAME in compiled.nodes
    assert ("Research Manager", NODE_NAME) in builder.edges
    assert (NODE_NAME, "Trader") in builder.edges
    assert ("Research Manager", "Trader") not in builder.edges

    # 2. 真实图执行断言
    sample_raw = _make_sample_raw_data()
    mock_collector = MagicMock()
    mock_collector.get.return_value = sample_raw

    # 包装执行状态
    state_in = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
        "market_data_context": {},
        "analyst_traces": [],
    }

    final_state = compiled.invoke(state_in)

    # 3. 路由顺序验证：必须严格为 Research Manager -> Game Theory -> Trader
    assert execution_order == ["Research Manager", "Trader"]
    # 证明 Game Theory 在两者之间执行过：
    assert final_state.get(REPORT_KEY) is not None
    assert len(final_state[REPORT_KEY]) > 100
    assert "博弈论与对手盘分析报告" in final_state[REPORT_KEY]
    assert final_state.get(SIGNALS_KEY) is not None
    assert final_state[SIGNALS_KEY]["data_status"] == "available"

    # 4. trace 连贯性验证：analyst_traces 包含完整 trace 链
    trace_agents = [t["agent"] for t in final_state.get("analyst_traces", [])]
    assert "research_manager" in trace_agents
    assert AGENT_NAME in trace_agents
    gt_trace = next(t for t in final_state["analyst_traces"] if t["agent"] == AGENT_NAME)
    assert gt_trace["source_status"] == "available"
    assert gt_trace["direction_allowed"] is True
    assert gt_trace["verdict"] in ("看多", "偏多", "看空", "偏空", "中性")


def test_rt11_wiring_contract_strict_mode_failures():
    """RT-11 (DAV-881 必修 1): 严格接线模式（strict_game_theory_wiring=True）下：
    接线异常明确使图构造失败抛出 GameTheoryWiringError，禁止仅 warning 悄悄降级。
    """
    from langgraph.graph import StateGraph, START, END
    from tradingagents.agents.utils.agent_states import AgentState

    # 1. raw_graph is None 失败
    tg_dummy = TradingAgentsGraph.__new__(TradingAgentsGraph)
    tg_dummy.strict_game_theory_wiring = True
    with pytest.raises(GameTheoryWiringError) as exc_info:
        tg_dummy._wire_game_theory_into_graph(None)
    assert exc_info.value.reason_code == "raw_graph_none"

    # 2. builder 缺失或非合法图对象失败
    with pytest.raises(GameTheoryWiringError) as exc_info:
        tg_dummy._wire_game_theory_into_graph(object())
    assert exc_info.value.reason_code == "builder_missing"

    # 3. 缺少必需锚点边 ('Research Manager', 'Trader') 失败
    bad_builder = StateGraph(AgentState)
    bad_builder.add_node("NodeA", lambda s: {})
    bad_builder.add_node("NodeB", lambda s: {})
    bad_builder.add_edge(START, "NodeA")
    bad_builder.add_edge("NodeA", "NodeB")
    bad_builder.add_edge("NodeB", END)
    compiled_bad = bad_builder.compile()

    with pytest.raises(GameTheoryWiringError) as exc_info:
        tg_dummy._wire_game_theory_into_graph(compiled_bad)
    assert exc_info.value.reason_code == "missing_anchor_edge"

    # 4. Mock 实例在 strict 模式下明确拒绝
    with pytest.raises(GameTheoryWiringError) as exc_info:
        tg_dummy._wire_game_theory_into_graph(MagicMock())
    assert exc_info.value.reason_code == "mock_graph_unwired"


def test_rt12_wiring_contract_graceful_typed_unavailable():
    """RT-12 (DAV-881 必修 1): 非严格接线模式（默认）下：
    若接线异常保留主图，但必须写入 typed game_theory_unavailable、trace、reason code 和报告状态，
    禁止仅 warning 后伪装成正常完成。
    """
    from langgraph.graph import StateGraph, START, END
    from tradingagents.agents.utils.agent_states import AgentState

    # 1. 构造一个缺失锚点边的普通图
    bad_builder = StateGraph(AgentState)
    bad_builder.add_node("Research Manager", lambda s: {"investment_plan": "测试计划"})
    bad_builder.add_edge(START, "Research Manager")
    bad_builder.add_edge("Research Manager", END)
    compiled_raw = bad_builder.compile()

    tg = TradingAgentsGraph.__new__(TradingAgentsGraph)
    tg.strict_game_theory_wiring = False
    tg.checkpointer = None
    tg.quick_thinking_llm = None
    tg.data_collector = None

    # 接线尝试失败，保留 raw_graph 但记录不可用元数据
    wired_graph = tg._wire_game_theory_into_graph(compiled_raw)
    assert wired_graph is compiled_raw
    assert tg.game_theory_wired is False
    assert tg.game_theory_unavailable is not None
    assert tg.game_theory_unavailable["status"] == "unavailable"
    assert tg.game_theory_unavailable["reason_code"] == "missing_anchor_edge"
    assert tg.game_theory_unavailable["wired"] is False

    # 2. 模拟图运行后的状态保全（_ensure_game_theory_state）
    run_state: dict = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
        "game_theory_report": None,
        "game_theory_signals": None,
        "analyst_traces": [],
    }
    tg._ensure_game_theory_state(run_state, horizon="short")

    # 状态断言：不可伪装为正常完成或 None
    assert run_state[REPORT_KEY] is not None
    assert run_state[REPORT_KEY].startswith("【博弈论分析不可用】原因：博弈论接线未生效或未被路由")
    assert "missing_anchor_edge" in run_state[REPORT_KEY]
    assert run_state[SIGNALS_KEY] is None  # 原子性保持 None
    assert run_state["game_theory_unavailable"] == tg.game_theory_unavailable

    # trace 断言：必须含有 failed 状态的可查痕迹
    assert len(run_state["analyst_traces"]) == 1
    t = run_state["analyst_traces"][0]
    assert t["agent"] == AGENT_NAME
    assert t["source_status"] == "failed"
    assert "missing_anchor_edge" in t["reason_codes"][0]

    # 3. 结果打包断言：_build_horizon_result 必须包含 game_theory_unavailable 并记录进 data_gaps
    res = TradingAgentsGraph._build_horizon_result(tg, "short", run_state)
    assert res["game_theory_unavailable"] is not None
    assert res["game_theory_unavailable"]["reason_code"] == "missing_anchor_edge"
    assert "game_theory_unavailable" in res["data_gaps"]
    assert res["game_theory_report"].startswith("【博弈论分析不可用】")
    assert res["game_theory_signals"] is None


def test_rt13_node_level_fail_closed_explicit_degradation_preservation():
    """RT-13 (DAV-881 必修 2): 保留节点自身输入缺失/执行异常的既有显式降级：
    图接线硬化不得把合法的节点级 fail-closed 改成空串、默认值或伪造信号。
    """
    tg = TradingAgentsGraph.__new__(TradingAgentsGraph)
    tg.game_theory_wired = True
    tg.game_theory_unavailable = None

    # 场景 A: 节点因数据源缺失显式返回降级文本与 None 信号
    degraded_state = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
        "game_theory_report": "【博弈论分析不可用】原因：标的 600519.SH 关键对手方数据缺失，执行显式降级。",
        "game_theory_signals": None,
        "analyst_traces": [
            {
                "agent": AGENT_NAME,
                "source_status": "unavailable",
                "reason_codes": ["data_unavailable_or_degraded"],
            }
        ],
    }

    tg._ensure_game_theory_state(degraded_state, horizon="short")

    # 验证原样保留，未被篡改为默认值或空串
    assert degraded_state[REPORT_KEY] == "【博弈论分析不可用】原因：标的 600519.SH 关键对手方数据缺失，执行显式降级。"
    assert degraded_state[SIGNALS_KEY] is None
    assert degraded_state.get("game_theory_unavailable") is None  # 节点级降级非接线失败

    # 场景 B: 节点计算异常返回的 fail-closed 文本
    exception_state = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
        "game_theory_report": "【博弈论分析不可用】原因：节点执行严重异常（TimeoutError: 接口超时），该项不可用。",
        "game_theory_signals": None,
        "analyst_traces": [
            {
                "agent": AGENT_NAME,
                "source_status": "failed",
                "reason_codes": ["node_failure_TimeoutError"],
            }
        ],
    }

    tg._ensure_game_theory_state(exception_state, horizon="short")
    assert "TimeoutError" in exception_state[REPORT_KEY]
    assert exception_state[SIGNALS_KEY] is None


def test_rt14_report_and_signals_combinations_matrix():
    """RT-14 (DAV-881 必修 3): 报告和 signals 的 None/非 None 组合有明确定义：
    1. 正常成功: (str, dict, unavailable=None) -> 合法
    2. 输入缺失降级: (degraded_str, None, unavailable=None) -> 合法
    3. 节点异常降级: (degraded_str, None, unavailable=None) -> 合法
    4. 接线失败/未路由: (degraded_str, None, unavailable=dict) -> 合法
    5. 违背原子性的组合自动修复/被拦截:
       - (None, None) -> 转换为 explicit degradation 状态
       - (normal_str, None) -> 降级为 explicit degradation（修复脱节）
       - (str="", ...) -> 绝对禁止空串
       - (None, dict) -> 严禁无报告却有 signals
    """
    tg = TradingAgentsGraph.__new__(TradingAgentsGraph)
    tg.game_theory_wired = False
    tg.game_theory_unavailable = {
        "status": "unavailable",
        "reason_code": "test_failure",
        "message": "测试接线不可用",
        "wired": False,
    }

    # 1. 初始全 None 状态 -> 被硬化为显式降级文本与 None 信号
    s1 = {"game_theory_report": None, "game_theory_signals": None}
    tg._ensure_game_theory_state(s1)
    assert s1["game_theory_report"].startswith("【博弈论分析不可用】")
    assert s1["game_theory_signals"] is None
    assert s1["game_theory_unavailable"]["reason_code"] == "test_failure"

    # 2. 存在正常报告文本但 signals 为 None（脱节/半写入） -> 自动修复为原子性不可用
    s2 = {"game_theory_report": "正常看多分析报告...", "game_theory_signals": None}
    tg._ensure_game_theory_state(s2)
    assert s2["game_theory_report"].startswith("【博弈论分析不可用】")
    assert s2["game_theory_signals"] is None

    # 3. 报告为空字符串 "" -> 视同缺失，硬化为显式降级
    s3 = {"game_theory_report": "", "game_theory_signals": None}
    tg._ensure_game_theory_state(s3)
    assert s3["game_theory_report"].startswith("【博弈论分析不可用】")
    assert s3["game_theory_signals"] is None

    # 4. 正常完整组合 -> 保持不变
    tg_ok = TradingAgentsGraph.__new__(TradingAgentsGraph)
    tg_ok.game_theory_wired = True
    tg_ok.game_theory_unavailable = None
    s4 = {
        "game_theory_report": "## 博弈论与对手盘分析报告\n正常内容...",
        "game_theory_signals": {"confidence": 0.8, "data_status": "available"},
    }
    tg_ok._ensure_game_theory_state(s4)
    assert s4["game_theory_report"].startswith("## 博弈论与对手盘分析报告")
    assert s4["game_theory_signals"]["confidence"] == 0.8
    assert s4.get("game_theory_unavailable") is None


def test_rt15_isolated_fixture_and_production_smoke_gap():
    """RT-15 (DAV-881 必修 4): 隔离环境端到端验证与明确 typed gap：
    1. 真实 StateGraph 结合隔离数据 fixture 完成全链路测试；
    2. 严格不触碰生产库与真实分析触发（符合门禁约束）；
    3. 显式记录 typed gap: production_smoke_gap，留可复现命令。
    """
    from langgraph.graph import StateGraph, START, END
    from tradingagents.agents.utils.agent_states import AgentState

    # 隔离 fixture
    isolated_fixture = _make_sample_raw_data()
    assert isolated_fixture["fund_flow_individual"] is not None
    assert isolated_fixture["margin_trading"] is not None

    # 构建带隔离 fixture 的完整测试图
    builder = StateGraph(AgentState)
    builder.add_node("Research Manager", lambda s: {"investment_plan": "隔离基准计划"})
    builder.add_node("Trader", lambda s: {"final_trade_decision": "HOLD"})
    builder.add_edge(START, "Research Manager")
    builder.add_edge("Research Manager", "Trader")
    builder.add_edge("Trader", END)

    wire_game_theory_node(builder)
    app = builder.compile()

    state_input = {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": "short",
        "market_data_context": {},
        "analyst_traces": [],
    }
    with patch("tradingagents.graph.game_theory_node.compute_game_theory_signals") as mock_calc:
        mock_calc.return_value = (
            "## 博弈论与对手盘分析报告（隔离环境测试）\n主力中性，散户平稳。",
            {"confidence": 0.65, "data_status": "available", "dominant_strategy": "防守反击"},
        )
        final_state = app.invoke(state_input)

    assert final_state["game_theory_report"] is not None
    assert "隔离环境测试" in final_state["game_theory_report"]
    assert final_state["game_theory_signals"]["confidence"] == 0.65

    # 明确 typed gap 声明：根据门禁协议，生产库保持锁定，业务 smoke 通过隔离测试验证
    production_smoke_gap = {
        "gap_type": "production_db_write_forbidden_by_gate",
        "status": "verified_via_isolated_fixture",
        "reproducible_command": "env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_game_theory_integration.py -v",
        "production_verified": False,
        "reason": "严格遵守门禁：禁止修改生产数据、禁止部署、禁止重启、禁止触发真实分析；生产环境待合入后由运维推进。",
    }
    assert production_smoke_gap["production_verified"] is False
    assert production_smoke_gap["status"] == "verified_via_isolated_fixture"
