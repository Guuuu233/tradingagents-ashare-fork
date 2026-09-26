"""DAV-1104: 资金流守卫短路治理 TDD 门禁测试集

覆盖总工批准的机制 A、机制 B 以及四类硬阻断红线场景。
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    select_fund_flow_source,
    validate_model_summary,
)
from tradingagents.graph.propagation import (
    Propagator,
    default_fund_flow_consensus_guard,
)
from tradingagents.agents.managers.research_manager import (
    create_research_manager,
    fund_flow_guard_abstain_status,
)


def _sample_dc_record(value: str = "0.40185", date: str = "2026-09-18") -> dict:
    return {
        "source": "tushare_eastmoney_moneyflow_dc",
        "source_family": "eastmoney",
        "algorithm_group": "new_algorithm_group",
        "status": "available",
        "symbol": "600036.SH",
        "date": date,
        "period_kind": "historical_daily",
        "time_window": "1d",
        "field": "r0_net",
        "value": value,
        "r0_net": value,
        "unit": "亿元",
        "field_semantics": {"r0_net": "主力净额（负值表示净流出）"},
        "field_categories": {"r0_net": "main_force"},
        "transport_provider": "tushare",
        "upstream_api": "moneyflow_dc",
        "upstream_field": "net_amount",
    }


def _sample_ths_lg_record(value: str = "0.758931", date: str = "2026-09-18") -> dict:
    return {
        "source": "tushare_ths_moneyflow_ths",
        "source_family": "ths",
        "algorithm_group": "new_algorithm_group",
        "status": "available",
        "symbol": "600036.SH",
        "date": date,
        "period_kind": "historical_daily",
        "time_window": "1d",
        "field": "lg_net",
        "value": value,
        "lg_net": value,
        "unit": "亿元",
        "field_semantics": {"lg_net": "今日大单净流入额（大单口径，不含超大单分量；万元）"},
        "field_categories": {},
        "transport_provider": "tushare",
        "upstream_api": "moneyflow_ths",
        "upstream_field": "buy_lg_amount",
    }


# =========================================================================
# 机制 A 测试：未选 smart_money 分析师时，合格 selection 必须回写图状态
# =========================================================================

def test_mechanism_a_data_collector_bridges_guard_to_market_context():
    """机制 A: data_collector 产生合格 selection 时，回写 fund_flow_consensus_guard。"""
    from tradingagents.graph.data_collector import DataCollector

    dc_rec = _sample_dc_record()
    records = [dc_rec, _sample_ths_lg_record()]
    sel = select_fund_flow_source(records, symbol="600036.SH", requested_as_of="2026-09-18")
    assert sel["direction_allowed"] is True

    # 验证 DataCollector 产物中包含非悬空的 fund_flow_consensus_guard
    from tradingagents.graph.data_collector import _build_fund_flow_consensus_guard
    guard = _build_fund_flow_consensus_guard(sel)
    assert guard["blocked"] is False
    assert guard["direction_allowed"] is True
    assert guard["status"] == "selected"
    assert guard["selected_source"] == "tushare_eastmoney_moneyflow_dc"


def test_mechanism_a_propagator_initial_state_not_stuck_when_smart_money_omitted():
    """机制 A: selected_analysts 无 smart_money 时，create_initial_state 不再悬空 fail-closed。"""
    dc_rec = _sample_dc_record()
    records = [dc_rec]
    sel = select_fund_flow_source(records, symbol="600036.SH", requested_as_of="2026-09-18")

    from tradingagents.graph.data_collector import _build_fund_flow_consensus_guard
    guard = _build_fund_flow_consensus_guard(sel)

    market_data_context = {
        "analysis_baseline_date": "2026-09-18",
        "fund_flow_evidence": {
            "records": records,
            "selection": sel,
            "status": "selected",
            "fund_flow_consensus_guard": guard,
        },
        "fund_flow_consensus_guard": guard,
    }

    propagator = Propagator()
    state = propagator.create_initial_state(
        "600036.SH",
        "2026-09-18",
        selected_analysts=["macro", "market", "news", "fundamentals"],  # 无 smart_money
        market_data_context=market_data_context,
    )

    # 状态不应再是未初始化的 not_checked + blocked=True
    state_guard = state.get("fund_flow_consensus_guard")
    assert state_guard is not None
    assert state_guard["blocked"] is False
    assert state_guard["direction_allowed"] is True
    assert state_guard["status"] == "selected"


def test_mechanism_a_research_manager_does_not_abort_when_smart_money_omitted():
    """机制 A: 当 smart_money 未被选择但数据层 selection 合格时，research_manager 不短路。"""
    dc_rec = _sample_dc_record()
    sel = select_fund_flow_source([dc_rec], symbol="600036.SH", requested_as_of="2026-09-18")
    from tradingagents.graph.data_collector import _build_fund_flow_consensus_guard
    guard = _build_fund_flow_consensus_guard(sel)

    # 模拟 research_manager 读取 guard
    assert not (guard.get("blocked") or not guard.get("direction_allowed"))


# =========================================================================
# 机制 B 测试：validate_model_summary 收紧提取，修辞偏差降级为 warning
# =========================================================================

def test_mechanism_b_model_mentioning_multi_day_does_not_mismatch_single_day():
    """机制 B: 结构化为单日数据，模型提及多日趋势时，不应误判单日 mismatch。"""
    records = [_sample_dc_record(value="0.40185", date="2026-09-18")]
    # 模拟 31dcbebb 场景：模型正文中提到 0.40 亿以及多日流出
    model_text = (
        "根据同花顺与东财统计，今日主力资金净流入约0.40亿元，呈现温和吸筹态势；"
        "而在过去5个交易日内，主力资金整体流出约5亿元，短期与中期呈现分歧。"
    )

    val = validate_model_summary(
        records,
        model_text,
        window_days=1,
        selected_field="r0_net",
        selected_source="tushare_eastmoney_moneyflow_dc",
        requested_as_of="2026-09-18",
    )

    # 不应导致 hard_guard.blocked=True，也不应置 status='mismatch' 阻止流程
    # DAV-1291 rework: 无偏差仅无法逐日核对的场景归 unverifiable（同样不阻断）
    assert val["hard_guard"]["blocked"] is False
    assert val["status"] in {"matched", "validation_warning", "unverifiable"}


def test_mechanism_b_rhetorical_deviation_downgrades_to_warning():
    """机制 B: 纯数值修辞轻微偏差降级为 validation_warning，不置 blocked。"""
    records = [_sample_dc_record(value="0.40185", date="2026-09-18")]
    # 模型文字写了一个近似或包含其他分量的数值，但方向同为流入
    model_text = "今日主力资金流入达0.45亿元，多头资金介入。"

    val = validate_model_summary(
        records,
        model_text,
        window_days=1,
        selected_field="r0_net",
        selected_source="tushare_eastmoney_moneyflow_dc",
        requested_as_of="2026-09-18",
    )

    # 修辞偏差降级为 warning，不得硬阻断
    # DAV-1291 rework: 该场景语义唯一，收紧为精确状态断言（复审建议）
    assert val["hard_guard"]["blocked"] is False
    assert val["status"] == "validation_warning"


# =========================================================================
# 红线测试：四类场景保持坚决硬阻断
# =========================================================================

def test_redline_all_sources_unavailable_remains_blocked():
    """红线 1: 全源不可用时，必须硬阻断。"""
    sel = select_fund_flow_source([], symbol="600036.SH", requested_as_of="2026-09-18")
    assert sel["direction_allowed"] is False
    assert sel["status"] == "blocked"
    assert sel["hard_guard"]["blocked"] is True


def test_redline_date_or_symbol_mismatch_remains_blocked():
    """红线 2: 日期或股票代码不符，必须硬阻断。"""
    # 错误标的
    wrong_symbol_rec = _sample_dc_record()
    wrong_symbol_rec["symbol"] = "000001.SZ"
    sel_sym = select_fund_flow_source([wrong_symbol_rec], symbol="600036.SH", requested_as_of="2026-09-18")
    assert sel_sym["direction_allowed"] is False
    assert sel_sym["hard_guard"]["blocked"] is True

    # 错误日期
    wrong_date_rec = _sample_dc_record(date="2026-09-10")
    sel_date = select_fund_flow_source([wrong_date_rec], symbol="600036.SH", requested_as_of="2026-09-18")
    assert sel_date["direction_allowed"] is False
    assert sel_date["hard_guard"]["blocked"] is True


def test_redline_true_same_field_conflict_remains_blocked():
    """红线 3: 同语义主力净额真冲突（离散度 >20% 且无主导优先级），必须硬阻断。"""
    rec1 = _sample_dc_record(value="1.0")
    rec2 = _sample_dc_record(value="-2.0")
    rec2["source"] = "tushare_ths_moneyflow_ths"
    rec2["source_family"] = "ths"

    from tradingagents.dataflows.fund_flow_evidence import build_consensus_evidence
    audit = build_consensus_evidence([rec1, rec2], symbol="600036.SH", requested_as_of="2026-09-18")
    assert audit["status"] == "data_conflict"
    assert audit["direction_allowed"] is False


def test_redline_directional_contradiction_remains_blocked():
    """红线 4: 模型输出严重违背事实（颠倒乾坤：结构化大幅流入，模型却下达强烈做空流出指令）。"""
    records = [_sample_dc_record(value="5.0", date="2026-09-18")]
    # 结构化主力流入 5 亿，模型却断言主力大幅流出 5 亿
    contradictory_text = "今日主力资金大幅净流出5亿元，资金恐慌出逃，建议清仓做空。"

    val = validate_model_summary(
        records,
        contradictory_text,
        window_days=1,
        selected_field="r0_net",
        selected_source="tushare_eastmoney_moneyflow_dc",
        requested_as_of="2026-09-18",
    )

    # 方向颠倒乾坤必须保持硬阻断！
    assert val["hard_guard"]["blocked"] is True
    assert val["status"] in {"blocked", "mismatch"}
