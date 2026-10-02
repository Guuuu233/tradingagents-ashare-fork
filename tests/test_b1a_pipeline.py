"""Synthetic B1 pipeline tests: adopted node output, persistence and every entry."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import main
from api.database import Base, ReportDB
from api.job_store import InMemoryJobStore
from api.services import report_service
from tradingagents.agents.managers import research_manager as manager
from tradingagents.graph import data_collector
from tradingagents.knowledge import historical_cases
from tests.test_b1a_output_contract import BENCHMARK, DATE, SOURCE, feature, forecast_input, slice_, sw_module
from tests.test_research_manager_seven_reports_and_verdict_gate import _make_seven_reports_state


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("horizon", ["short", "medium"])
def test_manager_injects_industry_name_only_and_captures_final_revised_probability(monkeypatch, language, horizon):
    prompts = []
    initial = '<!-- MANAGER_VERDICT: {"winner":"tie", "direction":"NEUTRAL", "p_rel_t10":60} -->'
    final = '<!-- MANAGER_VERDICT: {"winner":"tie", "direction":"NEUTRAL", "p_rel_t10":71} -->'
    async def stream(prompt):
        prompts.append(prompt)
        yield SimpleNamespace(content=initial)
    async def revision(state, **kwargs):
        assert kwargs["text"] == initial
        return final, {"accepted": True}
    monkeypatch.setattr(manager, "get_config", lambda: {"prompt_language": language})
    monkeypatch.setattr(manager, "resolve_sw_l1_benchmark", lambda *args: (deepcopy(BENCHMARK), None))
    monkeypatch.setattr(manager, "maybe_revise_role_report", revision)
    state = _make_seven_reports_state({"horizon": horizon, "trade_date": DATE, "company_of_interest": "000001.SZ"})
    payload = asyncio.run(manager.create_research_manager(SimpleNamespace(astream=stream), SimpleNamespace(get_memories=lambda *a, **k: []))(state))
    forecast = report_service.parse_horizon_forecast(payload)
    assert forecast["p_rel_t10"] == 71 and forecast["benchmark"] == BENCHMARK
    assert payload["manager_verdict"]["p_rel_t10"] == 71
    assert len(prompts) == 1
    line = "行业基准：申万一级 合成行业（801999.SI）" if language == "zh" else "Industry benchmark: Shenwan L1 合成行业 (801999.SI)"
    assert line in prompts[0]
    # New forecast is horizon-independent T+10; only the primary role differs.
    assert "p_rel_t10" in prompts[0] and "T+10" in prompts[0]


@pytest.mark.parametrize("value,status", [("60%", "malformed"), (None, "missing"), (60, "valid")])
def test_industry_failure_records_gap_but_never_suppresses_forecast_or_retries_format(monkeypatch, value, status):
    calls = []
    text = '<!-- MANAGER_VERDICT: ' + json.dumps({"winner": "tie", "direction": "NEUTRAL", "p_rel_t10": value}) + ' -->'
    async def stream(prompt):
        calls.append(prompt)
        yield SimpleNamespace(content=text)
    async def revision(state, **kwargs):
        return kwargs["text"], None
    gap = "【数据获取失败】申万一级行业基准：index_classify_unavailable"
    monkeypatch.setattr(manager, "resolve_sw_l1_benchmark", lambda *a: (None, gap))
    monkeypatch.setattr(manager, "maybe_revise_role_report", revision)
    monkeypatch.setattr(manager, "get_config", lambda: {"prompt_language": "zh"})
    state = _make_seven_reports_state({"trade_date": DATE, "company_of_interest": "000001.SZ"})
    result = asyncio.run(manager.create_research_manager(SimpleNamespace(astream=stream), SimpleNamespace(get_memories=lambda *a, **k: []))(state))
    main._apply_b1_output_contract(result, SOURCE)
    assert len(calls) == 1 and "行业基准：获取失败" in calls[0]
    assert result["forecast"]["status"] == status and result["forecast"]["benchmark"] is None
    assert gap in result["data_gaps"]


def test_sw_unexpected_transport_exception_is_still_a_gap(monkeypatch):
    mod = sw_module()
    def failure(*args, **kwargs):
        raise RuntimeError("private provider diagnostic must not be echoed")
    monkeypatch.setattr(mod, "_query_tushare_api", failure)
    benchmark, gap = mod.resolve_sw_l1_benchmark("000001.SZ", DATE)
    assert benchmark is None and gap.startswith("【数据获取失败】")
    assert "private provider" not in gap


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(data_collector, "_map_stock_to_industry", lambda *a: "合成行业")
    monkeypatch.setattr(historical_cases, "record_historical_case", lambda **k: None)
    monkeypatch.setattr(historical_cases, "backfill_pending_cases", lambda **k: None)
    @contextmanager
    def context():
        with Session() as db:
            yield db
    monkeypatch.setattr(main, "get_db_ctx", context)
    yield Session
    engine.dispose()


@pytest.mark.parametrize("action,target,stop,text", [
    ("SELL", 90, 95, "目标价：90；止损价：95；置信度：70；上涨概率：62%"),
    ("SELL", 90, 110, "失效价：110；下行参考：—"),
])
def test_saved_b1_numerics_cannot_be_resurrected_by_legacy_extraction(database, action, target, stop, text):
    result = slice_(action, target, stop)
    result["final_trade_decision"] = text
    result["trader_investment_plan"] = "目标价：90；止损价：110；置信度：70"
    main._apply_b1_output_contract(result, SOURCE)
    with database() as db:
        row = report_service.create_report(db, "000001.SZ", DATE, result_data=result)
        main._persist_b1_price_fields(row)
        db.commit()
        db.expire_all()
        row = db.query(ReportDB).filter(ReportDB.id == row.id).one()
        assert row.result_data["forecast"]["p_rel_t10"] == 60
        if stop < 100:
            assert row.result_data["trade_action"] == "NO_TRADE"
            for key in ("confidence", "probability", "target_price", "stop_loss_price"):
                assert row.result_data[key] is None and getattr(row, key) is None
        else:
            assert row.result_data["target_price"] is None and row.target_price is None
            assert row.result_data["stop_loss_price"] == row.stop_loss_price == 110


class PipelineGraph:
    fail_horizons = set()
    fail_all = False
    def __init__(self, selected_analysts=None, data_collector=None, **kwargs):
        self.data_collector = data_collector
        self.role_resolved_configs = {}
        self.quick_thinking_llm = object()
        self.propagator = SimpleNamespace(get_graph_args=lambda: {}, create_initial_state=self.initial)
        self.graph = SimpleNamespace(astream=self.stream, invoke=self.invoke)

    @staticmethod
    def initial(symbol, date, **kwargs):
        return {"company_of_interest": symbol, "trade_date": date, "horizon": kwargs.get("horizon", "short")}

    @classmethod
    def state(cls, init):
        horizon = init["horizon"]
        if cls.fail_all or horizon in cls.fail_horizons:
            raise RuntimeError("synthetic node failure")
        result = slice_("SELL", 90, 110, 45) if horizon == "medium" else slice_()
        result.update(init)
        for field in ("market_report", "sentiment_report", "news_report", "fundamentals_report",
                      "macro_report", "smart_money_report", "volume_price_report"):
            result[field] = "合成分析师报告：数据已提供，研究窗口完整，指标与来源可核对。"
        result["horizon"] = horizon
        result["price_ref_source"] = deepcopy(SOURCE)
        result["final_trade_decision"] = (
            "SELL；失效价：110元（前复权）；下行参考：90元（前复权）" if horizon == "medium" else
            "BUY；目标价：110元（前复权）；止损价：90元（前复权）"
        )
        result["investment_debate_state"] = {"manager_verdict": result["manager_verdict"]}
        return result

    @classmethod
    async def stream(cls, init, **kwargs):
        yield cls.state(init)

    @classmethod
    def invoke(cls, init, **kwargs):
        return cls.state(init)

    def propagate(self, symbol, date, **kwargs):
        state = self.state(self.initial(symbol, date, horizon="short"))
        return state, "BUY"

    def _build_horizon_result(self, horizon, state, market_source=None):
        return deepcopy(state)

    def process_signal(self, text):
        return "SELL" if "SELL" in text else "BUY"


@pytest.mark.parametrize("horizons,query,streaming", [
    (["short", "medium"], None, False), (["short"], "synthetic query", False),
    (["short"], None, False), (["short"], None, True), (["medium"], None, False),
])
def test_every_generation_path_writes_forecast_and_named_close_check(database, monkeypatch, horizons, query, streaming):
    store = InMemoryJobStore()
    collector = MagicMock()
    collector.collect.return_value = {**deepcopy(SOURCE), "market_data_context": {"daily": {"as_of": DATE}}}
    collector.get.return_value = collector.collect.return_value
    monkeypatch.setattr(main, "_job_store_instance", store)
    monkeypatch.setattr(main, "_shared_data_collector", collector)
    monkeypatch.setattr(main, "TradingAgentsGraph", PipelineGraph)
    monkeypatch.setattr(PipelineGraph, "fail_all", False)
    monkeypatch.setattr(PipelineGraph, "fail_horizons", set())
    monkeypatch.setattr(main, "_build_runtime_config", lambda *a, **k: {})
    monkeypatch.setattr(main, "_resolve_and_freeze_custom_prompts", lambda *a: ({}, False))
    def structured(**kwargs):
        sell = "SELL" in kwargs["final_trade_decision"]
        return report_service.StructuredReport(decision="SELL" if sell else "BUY", probability=0.62,
                                              confidence=70, target_price=90 if sell else 110,
                                              stop_loss_price=110 if sell else 90)
    monkeypatch.setattr(report_service, "extract_structured_data", structured)
    req = main.AnalyzeRequest(symbol="000001.SZ", trade_date=DATE, horizons=horizons, query=query,
                              user_intent={"ticker": "000001.SZ", "horizons": horizons}, selected_analysts=[])
    job_id = uuid4().hex
    asyncio.run(main._run_job_inner(job_id, req, streaming, True, "synthetic-user"))
    job = store.get_job(job_id)
    assert job["status"] == "completed"
    result = job["result"]
    units = [result["short_term"], result["medium_term"]] if len(horizons) > 1 else [result]
    for unit in units:
        assert unit["forecast"]["status"] == "valid"
        assert unit["forecast"]["p_rel_t10"] == (45 if unit.get("horizon") == "medium" else 60)
        assert unit["price_plan_check"]["status"] == "ok"
        assert unit["price_plan_check"]["ref_field"] == "stock_data.2026-09-30.close"
        assert unit["probability"] == 0.62
    if len(horizons) > 1:
        assert result["forecast"]["p_rel_t10"] == 60
    with database() as db:
        saved = db.query(ReportDB).filter(ReportDB.id == job_id).one().result_data
        assert saved["forecast"] == result["forecast"]
        assert "input_snapshot" in saved and "run_identity" in saved


def test_failed_job_persists_node_failed_for_each_requested_horizon(database, monkeypatch):
    store = InMemoryJobStore()
    collector = MagicMock()
    collector.collect.return_value = deepcopy(SOURCE)
    monkeypatch.setattr(main, "_job_store_instance", store)
    monkeypatch.setattr(main, "_shared_data_collector", collector)
    monkeypatch.setattr(main, "TradingAgentsGraph", PipelineGraph)
    monkeypatch.setattr(PipelineGraph, "fail_all", True)
    monkeypatch.setattr(main, "_build_runtime_config", lambda *a, **k: {})
    monkeypatch.setattr(main, "_resolve_and_freeze_custom_prompts", lambda *a: ({}, False))
    req = main.AnalyzeRequest(symbol="000001.SZ", trade_date=DATE, horizons=["short", "medium"], selected_analysts=[])
    job_id = uuid4().hex
    asyncio.run(main._run_job_inner(job_id, req, False, True, "synthetic-user"))
    assert store.get_job(job_id)["status"] == "failed"
    with database() as db:
        row = db.query(ReportDB).filter(ReportDB.id == job_id).one()
        assert row.status == "failed"
        assert isinstance(row.result_data, dict), "failed horizons must retain node_failed forecast records"
        for key in ("short_term", "medium_term"):
            assert row.result_data[key]["forecast"]["status"] == "node_failed"
            assert row.result_data[key]["price_plan_check"]["status"] == "not_applicable"
