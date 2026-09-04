"""Tests for Tonghuashun large order (buy_lg_amount) r0_net evidence and credibility scoring."""
from decimal import Decimal
import asyncio
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from tradingagents.dataflows.fund_flow_evidence import (
    build_source_evidence,
    select_fund_flow_source,
    score_large_order_reference_credibility,
    consensus_prompt_instruction,
)
from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
from tradingagents.agents.analysts.smart_money_analyst import create_smart_money_analyst


class _TushareResponse:
    def __init__(self, data: dict):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def _tushare_payload(api_name: str, net_amount="12000", buy_lg_amount="300"):
    if api_name == "moneyflow_dc":
        fields = [
            "ts_code", "trade_date", "net_amount",
            "buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount",
        ]
        values = {
            "ts_code": "600519.SH",
            "trade_date": "20260814",
            "net_amount": net_amount,
            "buy_sm_amount": "100",
            "buy_md_amount": "200",
            "buy_lg_amount": buy_lg_amount,
            "buy_elg_amount": "400",
        }
    else:
        fields = [
            "ts_code", "trade_date", "net_amount", "net_d5_amount",
            "buy_sm_amount", "buy_md_amount", "buy_lg_amount",
        ]
        values = {
            "ts_code": "600519.SH",
            "trade_date": "20260814",
            "net_amount": net_amount,
            "net_d5_amount": "56000",
            "buy_sm_amount": "100",
            "buy_md_amount": "200",
            "buy_lg_amount": buy_lg_amount,
        }
    return {
        "code": 0,
        "data": {"fields": fields, "items": [[values[f] for f in fields]]},
    }


def _record(source: str, value: str, field: str = "r0_net", date: str = "2026-08-14", direction: str | None = None):
    val_dec = Decimal(value)
    if direction is None:
        direction = "inflow" if val_dec > 0 else ("outflow" if val_dec < 0 else "neutral")
    return {
        "source": source,
        "source_family": "eastmoney" if "dc" in source or "eastmoney" in source else "ths",
        "algorithm_group": "new_algorithm_group",
        "status": "available",
        "symbol": "600519",
        "date": date,
        "as_of": date,
        "period_kind": "historical_daily",
        "time_window": "1d",
        "field": field,
        field: value,
        "value": value,
        "unit": "亿元",
        "direction": direction,
        "field_semantics": {
            field: "大单净额 / 平台主力口径参考（万元）" if field == "r0_net" and "ths" in source else ("主力净额（负值表示净流出）" if field == "r0_net" else "总净额（负值表示净流出）")
        },
        "upstream_field_semantics": "大单净额 / 平台主力口径参考（万元）" if field == "r0_net" and "ths" in source else ("今日主力净流入额（万元）" if field == "r0_net" else "资金净流入（万元）"),
    }


def test_ths_buy_lg_amount_produces_r0_net_evidence_alongside_dc(monkeypatch):
    """1. THS buy_lg -> r0_net 证据入库，与 DC r0_net 平级出现在 evidence。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()
    with patch(
        "requests.post",
        side_effect=[
            _TushareResponse(_tushare_payload("moneyflow_dc", net_amount="12000")),
            _TushareResponse(_tushare_payload("moneyflow_ths", net_amount="12000", buy_lg_amount="300")),
        ],
    ):
        out, errors, meta = provider._fetch_tushare_fund_flow("600519", "2026-08-14")

    assert errors == []
    assert out is not None
    # 应有 3 条证据：DC r0_net, THS netamount, THS r0_net (来自 buy_lg_amount)
    assert len(out.fund_flow_evidence) == 3
    dc_r0 = next(r for r in out.fund_flow_evidence if r.get("source") == "tushare_eastmoney_moneyflow_dc" and "r0_net" in r)
    ths_net = next(r for r in out.fund_flow_evidence if r.get("source") == "tushare_ths_moneyflow_ths" and "netamount" in r)
    ths_r0 = next(r for r in out.fund_flow_evidence if r.get("source") == "tushare_ths_moneyflow_ths" and "r0_net" in r)

    assert dc_r0["r0_net"] == "1.2"
    assert ths_net["netamount"] == "1.2"
    assert ths_r0["r0_net"] == "0.03"
    assert ths_r0["upstream_field"] == "buy_lg_amount"
    assert "大单净额" in ths_r0["upstream_field_semantics"]
    assert "参考" in ths_r0["upstream_field_semantics"]


def test_dc_r0_net_and_ths_netamount_coexist_allows_direction_no_incomparable_block():
    """2. DC r0_net + THS netamount 并存 -> 不再 incomparable 整页阻断；方向可来自 r0_net。"""
    records = [
        _record("tushare_eastmoney_moneyflow_dc", "2.5", field="r0_net"),
        _record("tushare_ths_moneyflow_ths", "3.0", field="netamount"),
    ]
    result = select_fund_flow_source(records, symbol="600519", requested_as_of="2026-08-14")

    assert result["direction_allowed"] is True
    assert result["hard_guard"]["blocked"] is False
    assert result["selected_field"] == "r0_net"
    assert result["selected_source"] == "tushare_eastmoney_moneyflow_dc"
    assert result["selected_direction"] == "inflow"
    assert result["reason_code"] != "incomparable_field_semantics"
    assert result["reference_only"] is True
    assert result["single_source"] is True
    assert result["credibility"] in {"medium_low", "low", "偏低", "中等偏低"}


def test_large_order_credibility_concordant_higher_than_single_source():
    """3a. DC r0_net 与 THS buy_lg 同向 -> 可信度高于单源。"""
    single_record = [_record("tushare_eastmoney_moneyflow_dc", "2.0", field="r0_net")]
    single_score = score_large_order_reference_credibility(single_record)
    assert single_score["single_source"] is True
    assert single_score["divergence"] is False

    dual_concordant = [
        _record("tushare_eastmoney_moneyflow_dc", "2.0", field="r0_net"),
        _record("tushare_ths_moneyflow_ths", "0.5", field="r0_net"),
    ]
    concordant_score = score_large_order_reference_credibility(dual_concordant)
    assert concordant_score["single_source"] is False
    assert concordant_score["divergence"] is False
    assert concordant_score["credibility_score"] > single_score["credibility_score"]
    assert concordant_score["credibility"] in {"high", "偏高"}


def test_large_order_credibility_divergent_lower_with_divergence_details():
    """3b. DC r0_net 与 THS buy_lg 反向 -> 可信度降低且报告保留分源说明。"""
    single_record = [_record("tushare_eastmoney_moneyflow_dc", "2.0", field="r0_net")]
    single_score = score_large_order_reference_credibility(single_record)

    dual_divergent = [
        _record("tushare_eastmoney_moneyflow_dc", "2.0", field="r0_net"),
        _record("tushare_ths_moneyflow_ths", "-0.5", field="r0_net"),
    ]
    divergent_score = score_large_order_reference_credibility(dual_divergent)
    assert divergent_score["single_source"] is False
    assert divergent_score["divergence"] is True
    assert divergent_score["credibility_score"] < single_score["credibility_score"]
    assert divergent_score["credibility"] in {"low", "偏低"}
    assert "分歧" in divergent_score["credibility_reason"]


class _YieldingLLM:
    def __init__(self, content: str):
        self.content = content

    async def astream(self, messages):
        yield SimpleNamespace(content=self.content)


class _DualSourceCollector:
    def __init__(self, ths_lg_val="0.5"):
        self.ths_lg_val = ths_lg_val

    def get(self, ticker, curr_date):
        records = [
            _record("tushare_eastmoney_moneyflow_dc", "2.0", field="r0_net", date=curr_date),
            _record("tushare_ths_moneyflow_ths", "1.5", field="netamount", date=curr_date),
            _record("tushare_ths_moneyflow_ths", self.ths_lg_val, field="r0_net", date=curr_date),
        ]
        return {
            "fund_flow_individual": "东方财富与同花顺资金流数据",
            "market_data_context": {
                "fund_flow_evidence": {
                    "records": records,
                    "symbol": ticker,
                    "requested_as_of": curr_date,
                }
            },
            "lhb": "无龙虎榜数据",
            "indicators": {"vwma": "100"},
        }


def test_smart_money_dual_source_lg_reference_report_retained_not_empty_shell():
    """4a. smart_money: 双源大单参考时报告不是冲突空壳，保留正文并带文首参考说明与可信度。"""
    original_text = "综合数据显示大单资金偏向流入，短期筹码结构良好。"
    llm = _YieldingLLM(original_text)
    state = {
        "trade_date": "2026-08-14",
        "company_of_interest": "600519",
        "user_intent": {"focus_areas": [], "specific_questions": []},
    }
    with (
        patch("tradingagents.agents.analysts.smart_money_analyst.get_cn_stock_name", return_value="贵州茅台"),
        patch("tradingagents.agents.analysts.smart_money_analyst.get_config", return_value={}),
        patch("tradingagents.agents.analysts.smart_money_analyst.get_prompt", return_value="提示词"),
        patch("tradingagents.agents.analysts.smart_money_analyst.build_horizon_context", return_value="上下文"),
        patch("tradingagents.agents.analysts.smart_money_analyst.log_llm_call"),
    ):
        result = asyncio.run(
            create_smart_money_analyst(llm, _DualSourceCollector(ths_lg_val="0.5"))(state)
        )

    report = result["smart_money_report"]
    # 绝不能是 68 字冲突空壳
    assert "资金流来源选择不可用或结构化累计存在冲突；已阻断增持、减持、吸筹方向摘要" not in report
    assert original_text in report
    # 文首带参考说明和可信度
    assert "参考" in report
    assert "可信度" in report

    guard = result["fund_flow_consensus_guard"]
    assert guard["blocked"] is False
    assert guard["direction_allowed"] is True
    assert "credibility" in guard
    assert guard["reference_only"] is True


def test_smart_money_netamount_only_still_blocks_main_force_accumulation_claims():
    """4b. smart_money: 仅 netamount 写「主力吸筹」仍阻断。"""
    violating_text = "资金面显示主力资金积极吸筹建仓，主力大幅增持。"
    llm = _YieldingLLM(violating_text)
    state = {
        "trade_date": "2026-08-14",
        "company_of_interest": "600519",
        "user_intent": {"focus_areas": [], "specific_questions": []},
    }
    netamount_collector = SimpleNamespace(
        get=lambda ticker, curr_date: {
            "fund_flow_individual": "同花顺即时资金流净额 5.60 亿",
            "market_data_context": {
                "fund_flow_evidence": {
                    "records": [_record("ths_instant_snapshot", "5.60", field="netamount", date=curr_date)],
                    "symbol": ticker,
                    "requested_as_of": curr_date,
                }
            },
            "lhb": "无数据",
            "indicators": {"vwma": "100"},
        }
    )
    with (
        patch("tradingagents.agents.analysts.smart_money_analyst.get_cn_stock_name", return_value="贵州茅台"),
        patch("tradingagents.agents.analysts.smart_money_analyst.get_config", return_value={}),
        patch("tradingagents.agents.analysts.smart_money_analyst.get_prompt", return_value="提示词"),
        patch("tradingagents.agents.analysts.smart_money_analyst.build_horizon_context", return_value="上下文"),
        patch("tradingagents.agents.analysts.smart_money_analyst.log_llm_call"),
    ):
        result = asyncio.run(
            create_smart_money_analyst(llm, netamount_collector)(state)
        )

    guard = result["fund_flow_consensus_guard"]
    assert guard["blocked"] is True
    assert guard["direction_allowed"] is False
    assert "已阻断增持、减持、吸筹方向摘要" in result["smart_money_report"]
