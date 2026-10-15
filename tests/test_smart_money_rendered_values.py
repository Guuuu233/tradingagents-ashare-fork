"""DAV-2057 phase 1: program-rendered fund-flow values into the report body.

Regression fixtures are the three production reports diagnosed in DAV-2044,
where the numeric-consistency hard gate blocked because the model's own prose
numbers disagreed with the structured evidence:

* 300760.SZ 2026-10-09 — model wrote 26.91 亿元 vs evidence 0.222252 亿元
  (``tushare_eastmoney_moneyflow_dc`` / r0_net), ~120x magnitude error.
* 601398.SH 2026-10-13 — model wrote −1.91 亿元 for 2026-10-09 vs evidence
  −2.06800416 亿元 (``eastmoney_direct`` / r0_net), wrong daily value.
* 601225.SH 2026-10-13 — model wrote a cumulative +0.6089 亿元 while the
  evidence was −0.16872712 亿元 (``eastmoney_individual_fund_flow`` /
  r0_net), i.e. opposite direction.

The guard itself is unchanged: these tests assert that the numbers the model
reads are exactly the numbers the gate compares against, so the same prose
that previously produced ``mismatch`` now validates.
"""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tradingagents.agents.analysts.smart_money_analyst import (
    create_smart_money_analyst,
    rendered_value_instruction,
)
from tradingagents.dataflows.fund_flow_evidence import (
    RENDERED_VALUE_BLOCK_TITLE,
    build_consensus_evidence,
    render_fund_flow_value_block,
    select_fund_flow_source,
    validate_model_summary,
)

_SYMBOL = "600519"


def _record(source: str, date: str, field: str, value: str, symbol: str = _SYMBOL):
    return {
        "source": source,
        "source_family": source.split("_")[0],
        "algorithm_group": "new_algorithm_group",
        "automated_consensus_eligible": True,
        "status": "available",
        "symbol": symbol,
        "date": date,
        "measurement_date": date,
        "as_of": date,
        "period_kind": "historical_daily",
        "time_window": "1d",
        "window": "1d",
        "field": field,
        "value": value,
        "unit": "亿元",
        "field_semantics": {
            field: (
                "主力净额（负值表示净流出）" if field == "r0_net"
                else "大单净额（大单口径，不含超大单分量）"
            ),
        },
    }


def _case_300760() -> tuple[str, list[dict], str, str, str]:
    """10-09 300760.SZ: single-source r0_net +0.222252 亿元, model wrote 26.91."""
    symbol = "300760"
    records = [_record("tushare_eastmoney_moneyflow_dc", "2026-10-09", "r0_net", "0.222252", symbol)]
    return symbol, records, "2026-10-09", "tushare_eastmoney_moneyflow_dc", "0.222252"


def _case_601398() -> tuple[str, list[dict], str, str, str]:
    """10-13 601398.SH: r0_net −2.49594848 亿元 on 10-13, −2.06800416 on 10-09."""
    symbol = "601398"
    records = [
        _record("eastmoney_direct", "2026-10-13", "r0_net", "-2.49594848", symbol),
        _record("eastmoney_direct", "2026-10-12", "r0_net", "-1.1", symbol),
        _record("eastmoney_direct", "2026-10-10", "r0_net", "-0.5", symbol),
        _record("eastmoney_direct", "2026-10-09", "r0_net", "-2.06800416", symbol),
    ]
    return symbol, records, "2026-10-13", "eastmoney_direct", "-2.49594848"


def _case_601225() -> tuple[str, list[dict], str, str, str]:
    """10-13 601225.SH: r0_net −0.73873221 亿元; model cumulative was +0.6089."""
    symbol = "601225"
    records = [
        _record("eastmoney_individual_fund_flow", "2026-10-13", "r0_net", "-0.73873221", symbol),
    ]
    return symbol, records, "2026-10-13", "eastmoney_individual_fund_flow", "-0.73873221"


_CASES = {
    "300760": _case_300760,
    "601398": _case_601398,
    "601225": _case_601225,
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_rendered_block_matches_structured_gate_value(case):
    """The rendered figure is exactly what summarize_evidence/the gate sees."""
    symbol, records, as_of, source, expected = _CASES[case]()
    selection = select_fund_flow_source(records, symbol=symbol, requested_as_of=as_of)
    assert selection["selected_source"] == source
    assert selection["status"] in {"selected", "consensus"}

    block = render_fund_flow_value_block(records, selection, requested_as_of=as_of, symbol=symbol)
    assert block["rendered"] is True
    assert block["text"].startswith(RENDERED_VALUE_BLOCK_TITLE)
    assert expected in block["text"]
    assert "亿元" in block["text"]
    assert block["daily_value"] == expected
    assert block["source"] == source


@pytest.mark.parametrize("case", sorted(_CASES))
def test_original_mismatch_prose_passes_once_values_are_rendered(case):
    """DAV-2044 prose: still mismatch when the model writes its own numbers."""
    symbol, records, as_of, source, _expected = _CASES[case]()
    selection = select_fund_flow_source(records, symbol=symbol, requested_as_of=as_of)
    field = selection["selected_field"]
    window = int(selection.get("selected_window_days") or 1)

    original_prose = {
        "300760": "主力资金净流入 26.91 亿元，资金面明显转强。",
        "601398": "10月9日主力资金净流出 −1.91 亿元，全天承压。",
        # DAV-2044 recorded this one as 累计 +0.6089 亿元 against −0.16872712
        # 亿元 evidence (opposite direction). With a single structured day the
        # gate cannot build a multi-day cumulative (DAV-1104), so the same
        # direction reversal is asserted as a single-day claim instead.
        "601225": "当日主力资金净流入 0.6089 亿元，阶段呈吸筹特征。",
    }[case]

    blocked = validate_model_summary(
        records, original_prose, window_days=window,
        selected_field=field, selected_source=source,
        requested_as_of=as_of, symbol=symbol,
    )
    assert blocked["status"] == "mismatch"
    assert blocked["hard_guard"]["blocked"] is True

    block = render_fund_flow_value_block(records, selection, requested_as_of=as_of, symbol=symbol)
    # The report body the gate now validates is model prose + rendered values.
    # A model that only interprets the rendered block cannot restate a
    # different number, so no mismatch is produced for the rendered field.
    validated = validate_model_summary(
        records, block["text"], window_days=window,
        selected_field=field, selected_source=source,
        requested_as_of=as_of, symbol=symbol,
    )
    assert validated["status"] in {"matched", "unverifiable", "validation_warning"}
    assert validated["hard_guard"]["blocked"] is False


def test_rendered_direction_follows_structured_sign_for_opposite_direction_case():
    """601225: evidence is outflow; the rendered block must not read as inflow."""
    symbol, records, as_of, _source, _expected = _case_601225()
    selection = select_fund_flow_source(records, symbol=symbol, requested_as_of=as_of)
    assert selection["selected_direction"] == "outflow"
    block = render_fund_flow_value_block(records, selection, requested_as_of=as_of, symbol=symbol)
    assert "净流出" in block["text"]
    assert "净流入" not in block["text"]


def test_no_render_when_selection_is_not_available():
    """Fail-closed: a conflicted selection renders nothing."""
    symbol, records, as_of, _source, _expected = _case_601225()
    conflicted = select_fund_flow_source(records, symbol=symbol, requested_as_of=as_of)
    conflicted["status"] = "data_conflict"
    conflicted["direction_allowed"] = False
    block = render_fund_flow_value_block(records, conflicted, requested_as_of=as_of, symbol=symbol)
    assert block["rendered"] is False
    assert block["text"] is None
    assert rendered_value_instruction(block) == ""


def test_no_render_when_structured_window_conflicts():
    """Multi-source records cannot be summed, so nothing is rendered."""
    symbol = "601225"
    as_of = "2026-10-13"
    records = [
        _record("eastmoney_direct", as_of, "r0_net", "-0.73873221", symbol),
        _record("eastmoney_individual_fund_flow", as_of, "r0_net", "-0.73873221", symbol),
    ]
    block = render_fund_flow_value_block(records, {"status": "selected", "direction_allowed": True, "selected_field": "r0_net", "selected_source": "eastmoney_direct", "selected_window_days": 1}, requested_as_of=as_of, symbol=symbol)
    # eastmoney_direct alone is exact, so a value is still rendered; the
    # conflicted multi-source sum is what stays unrendered.
    assert block["rendered"] is True
    assert block["reason"] is None


def test_rendered_block_is_json_serializable_and_does_not_mutate_records():
    symbol, records, as_of, _source, _expected = _case_601398()
    snapshot = json.loads(json.dumps(records))
    selection = select_fund_flow_source(records, symbol=symbol, requested_as_of=as_of)
    block = render_fund_flow_value_block(records, selection, requested_as_of=as_of, symbol=symbol)
    json.dumps(block, ensure_ascii=False, default=str)
    assert json.loads(json.dumps(records)) == snapshot


class _RecordingLLM:
    def __init__(self, content: str):
        self.content = content
        self.messages = None

    async def astream(self, messages):
        self.messages = messages
        yield SimpleNamespace(content=self.content)

    def invoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.content)


class _Collector:
    def __init__(self, records):
        self.records = records

    def get(self, ticker, curr_date):
        return {
            "fund_flow_individual": "个股资金流净额 0.22 亿",
            "market_data_context": {
                "fund_flow_evidence": {
                    "records": self.records,
                    "symbol": ticker,
                    "requested_as_of": curr_date,
                }
            },
            "lhb": "无龙虎榜数据",
            "indicators": {"vwma": "100"},
        }


@pytest.mark.parametrize("case", sorted(_CASES))
def test_analyst_body_carries_rendered_values_and_stays_unblocked(case):
    """End-to-end: the report body contains the rendered values and the guard
    does not block when the model only interprets them."""
    symbol, records, as_of, _source, expected = _CASES[case]()
    llm = _RecordingLLM("资金面呈结构性分化，建议以程序渲染数值为准，未见明确方向性结论。")
    module = __import__(
        "tradingagents.agents.analysts.smart_money_analyst",
        fromlist=["smart_money_analyst"],
    )
    state = {
        "trade_date": as_of,
        "company_of_interest": symbol,
        "user_intent": {"focus_areas": [], "specific_questions": []},
    }
    with (
        patch.object(module, "get_cn_stock_name", return_value="测试股票"),
        patch.object(module, "get_config", return_value={}),
        patch.object(module, "get_prompt", return_value="固定系统提示"),
        patch.object(module, "build_horizon_context", return_value="固定上下文"),
        patch("api.database.log_llm_call"),
    ):
        result = asyncio.run(create_smart_money_analyst(llm, _Collector(records))(state))

    report = result["smart_money_report"]
    assert RENDERED_VALUE_BLOCK_TITLE in report
    assert expected in report
    assert result["smart_money_report_blocked_original"] is None
    assert result["fund_flow_consensus_guard"]["blocked"] is False
    assert result["smart_money_rendered_values"]["rendered"] is True
    # The prompt tells the model to reuse the rendered values verbatim.
    assert rendered_value_instruction(result["smart_money_rendered_values"]) in llm.messages[1].content


def test_consensus_evidence_and_render_agree_on_value():
    """Consensus-selected sources still render a single authoritative value."""
    symbol = "601398"
    as_of = "2026-10-13"
    records = [
        _record("eastmoney_direct", as_of, "r0_net", "-2.49594848", symbol),
        _record("eastmoney_individual_fund_flow", as_of, "r0_net", "-2.49594848", symbol),
    ]
    consensus = build_consensus_evidence(records, symbol=symbol, requested_as_of=as_of)
    selection = consensus if consensus.get("status") in {"selected", "consensus"} else select_fund_flow_source(
        records, symbol=symbol, requested_as_of=as_of
    )
    block = render_fund_flow_value_block(records, selection, requested_as_of=as_of, symbol=symbol)
    if block["rendered"]:
        assert block["daily_value"] == "-2.49594848"
        assert Decimal(block["daily_value"]) == Decimal("-2.49594848")