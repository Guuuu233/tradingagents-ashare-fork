"""B1 v1 synthetic output-contract fixtures; never use real report prose."""
from copy import deepcopy
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from api import main
from api.services import report_service
from tradingagents.agents.managers import research_manager as manager


BENCHMARK = {"src": "SW2021", "level": "L1", "code": "801999.SI", "name": "合成行业"}
DATE = "2026-09-30"
SOURCE = {"stock_data": "# price_basis: vendor_qfq\nvolume,close,date,low,high,open\n100,100,2026-09-30,98,103,99\n"}


def feature(module, name):
    fn = getattr(module, name, None)
    assert callable(fn), f"B1 contract implementation missing: {name}"
    return fn


def forecast_input(value=60):
    return {"node_ran": True, "p_rel_t10": value, "benchmark": deepcopy(BENCHMARK), "data_gap": None}


def slice_(action="BUY", target=110, stop=90, value=60):
    return {
        "symbol": "000001.SZ", "horizon": "short", "trade_date": DATE,
        "analysis_baseline_date": DATE, "market_data_context": {"daily": {"as_of": DATE}},
        "analysis_status": "VALID", "trade_action": action, "decision": action,
        "decision_status": {"analysis_status": "VALID", "trade_action": action,
                            "direction": "BEARISH" if action == "SELL" else "BULLISH",
                            "risk_status": "PASS", "confirmation_state": "CONFIRMED", "reason_codes": ["existing"]},
        "target_price": target, "stop_loss_price": stop, "confidence": 70,
        "probability": 0.62, "manager_verdict": {"forecast_input": forecast_input(value)},
        "data_gaps": [], "price_basis_gate": {"status": "pass"},
    }


@pytest.mark.parametrize("value,status,want", [
    (0, "malformed", None), (1, "valid", 1), (99, "valid", 99), (100, "malformed", None),
    ("60%", "malformed", None), ("60", "malformed", None), (60.0, "valid", 60),
    (60.5, "malformed", None), (None, "missing", None), (True, "malformed", None),
    (False, "malformed", None), (float("nan"), "malformed", None),
    (float("inf"), "malformed", None), (10**400, "malformed", None),
])
def test_forecast_numeric_contract(value, status, want):
    result = feature(report_service, "parse_horizon_forecast")(slice_(value=value))
    assert result == {"contract": "b1.v1", "p_rel_t10": want, "status": status,
                      "benchmark": BENCHMARK, "node": "research_manager"}
    if want is not None:
        assert type(result["p_rel_t10"]) is int


@pytest.mark.parametrize("text", [
    '正文 p_rel_t10=60；上涨概率 60%',
    '<!-- VERDICT: {"p_rel_t10": 60, "direction": "BULLISH"} -->',
    '<!-- MANAGER_VERDICT: {"p_rel_t10": 60,} -->',
    '<!-- MANAGER_VERDICT: {"winner": "tie"} -->',
    '<!-- MANAGER_VERDICT: {"p_rel_t10": null} -->',
])
def test_forecast_never_fills_from_prose_fallback_or_invalid_block(text):
    captured = feature(manager, "capture_manager_forecast_input")(text, BENCHMARK)
    result = feature(report_service, "parse_horizon_forecast")({"manager_verdict": {"forecast_input": captured}})
    assert result["status"] == "missing" and result["p_rel_t10"] is None


@pytest.mark.parametrize("text,want", [
    ('<!-- MANAGER_VERDICT: {"p_rel_t10":60} -->\n正文尾部', 60),
    ('<!-- MANAGER_VERDICT: {"p_rel_t10":20} -->\n<!-- MANAGER_VERDICT: {"p_rel_t10":80} -->', 20),
])
def test_forecast_matches_existing_first_adopted_block_parser(text, want):
    captured = feature(manager, "capture_manager_forecast_input")(text, BENCHMARK)
    assert captured["p_rel_t10"] == want


def test_forecast_reads_canonical_block_with_escaped_comment_and_verdict_tail():
    text = ('说明：p_rel_t10=7\n<!-- MANAGER_VERDICT: '
            '{"reason":"字符串含 --> 不结束块", "p_rel_t10":99} -->\n'
            '<!-- VERDICT: {"direction":"NEUTRAL"} -->')
    captured = feature(manager, "capture_manager_forecast_input")(text, BENCHMARK)
    assert feature(report_service, "parse_horizon_forecast")({"manager_verdict": {"forecast_input": captured}})["p_rel_t10"] == 99


@pytest.mark.parametrize("result", [{}, {"manager_verdict": {"p_rel_t10": 50}}, {"status": "failed", "manager_verdict": {"forecast_input": forecast_input()}}])
def test_forecast_node_failed_never_infers_a_run_from_synthetic_verdict(result):
    forecast = feature(report_service, "parse_horizon_forecast")(result)
    assert forecast["status"] == "node_failed" and forecast["p_rel_t10"] is None


def test_forecast_missing_key_in_ran_node_is_missing():
    result = {"manager_verdict": {"forecast_input": {"node_ran": True, "benchmark": BENCHMARK}}}
    assert feature(report_service, "parse_horizon_forecast")(result)["status"] == "missing"


@pytest.mark.parametrize("action,target,stop,status", [
    ("BUY", 110, 90, "ok"), ("BUY", 100, 90, "violation"), ("BUY", 110, 100, "violation"),
    ("BUY", 90, 110, "violation"), ("BUY", None, 90, "missing_required"),
    ("BUY", 110, None, "missing_required"), ("SELL", 90, 110, "ok"),
    ("SELL", None, 110, "ok"), ("SELL", 100, 110, "violation"),
    ("SELL", 90, 100, "violation"), ("SELL", 110, 90, "violation"),
    ("SELL", 90, 90, "violation"), ("SELL", None, None, "missing_required"),
    ("SELL", None, 95, "violation"), ("SELL", None, 0, "missing_required"),
])
def test_buy_sell_price_geometry_is_deterministic(action, target, stop, status):
    data = slice_(action, target, stop)
    saved = deepcopy(data)
    check = feature(report_service, "check_price_plan")(data, SOURCE)
    assert data == saved
    assert check["status"] == status and check["ref_close"] == 100
    assert check["ref_field"] == "stock_data.2026-09-30.close"
    assert check["original_executable"] is True and check["original_action"] == action
    assert check["raw"] == {"target_price": target, "stop_loss_price": stop}


@pytest.mark.parametrize("source", [
    {}, {"stock_data": "date,close\n2026-09-30,100\n"},
    {"stock_data": "date,open,high,low,close\n2026-09-29,99,103,98,100\n"},
    {"stock_data": SOURCE["stock_data"].replace("vendor_qfq", "raw")},
    {"stock_data": SOURCE["stock_data"].replace("100,100,", "100,nan,")},
])
def test_missing_or_non_qfq_analysis_day_close_is_unverifiable(source):
    check = feature(report_service, "check_price_plan")(slice_(), source)
    assert check["status"] == "unverifiable" and check["ref_close"] is None


def test_price_check_uses_actual_daily_date_not_request_or_latest_alias():
    result = slice_()
    result["analysis_baseline_date"] = "2026-10-01"
    source = {"stock_data": SOURCE["stock_data"] + "100,200,2026-10-01,198,203,199\n"}
    assert feature(report_service, "check_price_plan")(result, source)["ref_close"] == 100


@pytest.mark.parametrize("action", ["HOLD", "WAIT", "NO_TRADE", "ABSTAIN"])
def test_non_directional_actions_do_not_require_a_price_reference(action):
    check = feature(report_service, "check_price_plan")(slice_(action, None, None), {})
    assert check["status"] == "not_applicable" and check["original_executable"] is False


@pytest.mark.parametrize("text", [
    "最终交易建议：卖出。跌破95确认离场；失效价：95；下行参考：90。",
    "SELL: exit on a break below 95. Invalidation price: 95; Downside reference: 90.",
])
def test_breakdown_confirmation_mislabeled_invalidation_fails_closed(text):
    result = slice_("SELL", None, None)
    result["final_trade_decision"] = text
    feature(main, "_apply_b1_output_contract")(result, SOURCE)
    assert result["price_plan_check"]["status"] == "violation"
    assert result["price_plan_check"]["raw"] == {"target_price": 90.0, "stop_loss_price": 95.0}
    assert result["trade_action"] == result["decision"] == "NO_TRADE"
    assert "price_plan_invalid" in result["reason_codes"]
    assert "existing" in result["reason_codes"]
    assert result["confidence"] is result["target_price"] is result["stop_loss_price"] is None
    assert result["forecast"]["p_rel_t10"] == 60
    assert result["price_basis_gate"] == {"status": "pass"}


@pytest.mark.parametrize("action", ["WAIT", "NO_TRADE", "HOLD", "ABSTAIN"])
def test_non_executable_forecast_survives_existing_numeric_null_rules(action):
    result = slice_(action)
    feature(main, "_apply_b1_output_contract")(result, {})
    assert result["forecast"]["status"] == "valid" and result["forecast"]["p_rel_t10"] == 60
    assert result["price_plan_check"]["status"] == "not_applicable"


def test_valid_price_plan_preserves_absolute_probability_and_is_idempotent():
    result = slice_()
    feature(main, "_apply_b1_output_contract")(result, SOURCE)
    assert result["trade_action"] == "BUY" and result["probability"] == 0.62
    assert report_service.resolve_report_fields(result)["probability"] == 0.62
    first = deepcopy(result)
    feature(main, "_apply_b1_output_contract")(result, SOURCE)
    assert result == first


def test_explicit_sell_dash_is_not_filled_from_old_target_or_trader():
    result = slice_("SELL", 90, 110)
    result["final_trade_decision"] = "失效价：110；下行参考：—"
    result["trader_investment_plan"] = "目标价：95；止损价：105"
    feature(main, "_apply_b1_output_contract")(result, SOURCE)
    assert result["price_plan_check"]["status"] == "ok"
    assert result["target_price"] is None and result["stop_loss_price"] == 110


def sw_module():
    spec = importlib.util.find_spec("tradingagents.dataflows.sw_industry")
    assert spec is not None, "B1 SW2021 point-in-time adapter missing"
    return importlib.import_module("tradingagents.dataflows.sw_industry")


def sw_frames(in_date="20260930", out_date=None):
    classify = pd.DataFrame([{"index_code": "801999.SI", "industry_name": "合成行业", "level": "L1"}])
    members = pd.DataFrame([{"ts_code": "000001.SZ", "l1_code": "801999.SI", "l1_name": "合成行业", "in_date": in_date, "out_date": out_date}])
    return classify, members


@pytest.mark.parametrize("in_date,out_date,want", [
    ("20260930", None, True), ("20261001", None, False),
    ("20260101", "20260930", False), ("20260101", "20261001", True),
    ("2026-09-30", "", True), ("invalid", None, False),
])
def test_sw_membership_inclusive_in_exclusive_out(in_date, out_date, want, monkeypatch):
    mod = sw_module()
    classify, members = sw_frames(in_date, out_date)
    def query(api_name, **kwargs):
        return (classify if api_name == "index_classify" else members), None, None
    monkeypatch.setattr(mod, "_query_tushare_api", query)
    benchmark, gap = mod.resolve_sw_l1_benchmark("000001.SZ", DATE)
    assert (benchmark == BENCHMARK) is want
    assert (gap is None) is want


def test_sw_adapter_requests_historical_and_current_members_and_no_prices(monkeypatch):
    mod = sw_module()
    classify, historic = sw_frames("20240101", "20261001")
    current = historic.assign(in_date="20261001", out_date=None)
    calls = []
    def query(api_name, **kwargs):
        calls.append((api_name, kwargs))
        if api_name == "index_classify":
            assert kwargs["params"] == {"level": "L1", "src": "SW2021"}
            return classify, None, None
        assert api_name == "index_member_all" and kwargs["ts_code"] == "000001.SZ"
        return (current if kwargs["params"]["is_new"] == "Y" else historic), None, None
    monkeypatch.setattr(mod, "_query_tushare_api", query)
    benchmark, gap = mod.resolve_sw_l1_benchmark("000001.SZ", DATE)
    assert benchmark == BENCHMARK and gap is None
    assert {c[1]["params"]["is_new"] for c in calls if c[0] == "index_member_all"} == {"Y", "N"}


def test_sw_transport_failure_is_explicit_not_a_default_industry(monkeypatch):
    mod = sw_module()
    monkeypatch.setattr(mod, "_query_tushare_api", lambda *a, **k: (None, "timeout", "do not echo provider details"))
    benchmark, gap = mod.resolve_sw_l1_benchmark("000001.SZ", DATE)
    assert benchmark is None and gap.startswith("【数据获取失败】申万一级行业基准")
    assert "provider details" not in gap


def test_sw_multiple_active_industries_fail_closed(monkeypatch):
    mod = sw_module()
    classify, members = sw_frames()
    classify = pd.concat([classify, pd.DataFrame([{"index_code": "801998.SI", "industry_name": "另一个合成行业", "level": "L1"}])])
    members = pd.concat([members, members.assign(l1_code="801998.SI")])
    monkeypatch.setattr(mod, "_query_tushare_api", lambda api_name, **k: ((classify if api_name == "index_classify" else members), None, None))
    benchmark, gap = mod.resolve_sw_l1_benchmark("000001.SZ", DATE)
    assert benchmark is None and gap is not None
