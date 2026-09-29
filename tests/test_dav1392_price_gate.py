"""DAV-1392: frozen d17b0fe5 seven-horizon, 28-violation zero-model evidence."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingagents.agents.utils.price_ref_registry import (
    build_price_ref_registry, extract_executable_levels,
)
from tradingagents.agents.utils.price_basis_gate import evaluate_price_basis_gate
from tradingagents.agents.utils.price_ref_revision import (
    PRICE_REF_REVISION_ENV, maybe_revise_role_report,
)

CASES = json.loads((Path(__file__).parent / "fixtures/dav1392_price_gate.json").read_text())["cases"]


def _case(symbol, horizon):
    return next(c for c in CASES if c["symbol"].startswith(symbol) and c["horizon"] == horizon)


def _gate(reports, cutoff="2026-09-29"):
    registry = build_price_ref_registry(reports, cutoff=cutoff)
    state = {**reports, "trade_date": cutoff, "price_refs": registry["price_refs"],
             "price_basis_validation": registry["validation"]}
    return registry, evaluate_price_basis_gate(state)


def test_frozen_violations_have_explicit_individual_verdict_and_evidence():
    assert len(CASES) == 7
    violations = [v for c in CASES for v in c["original_violations"]]
    assert len(violations) == 28
    assert {v["verdict"] for v in violations} == {"true_positive", "false_positive"}
    assert all(v["evidence"] and all(r["sentence"] for r in v["evidence"]) for v in violations)
    assert sum(v["verdict"] == "true_positive" for v in violations) == 3


@pytest.mark.parametrize("symbol,horizon,value,source", [
    ("002594", "medium", 1.3, "investment_plan"),
    ("601318", "medium", 56.775127, "fundamentals_report"),
    ("688981", "short", 0.618, "investment_plan"),
])
def test_frozen_non_prices_never_become_refs(symbol, horizon, value, source):
    c = _case(symbol, horizon)
    text = c["reports"][source]
    refs = build_price_ref_registry({source: text}, cutoff=c["trade_date"])["price_refs"]
    assert not any(r["value"] == value for r in refs)
    if value == 0.618:
        assert value not in [v for v, _, _ in extract_executable_levels(text)]


def test_frozen_pb_support_is_display_only_but_not_executable():
    c = _case("600036", "short")
    sentence = c["original_violations"][0]["evidence"][0]["sentence"]
    reg, gate = _gate({"fundamentals_report": sentence}, c["trade_date"])
    assert any(r["basis"] == "derived_estimate" and r["value"] == 36.3 for r in reg["price_refs"])
    assert not gate["violations"]
    _, gate = _gate({"investment_plan": "按 PB 估值测算入场价 36.30 元，止损位 37.20 元。"}, c["trade_date"])
    assert gate["status"] == "blocked"
    assert any(v["kind"] == "executable_level_wrong_basis" for v in gate["violations"])


def test_frozen_600276_proposed_buyback_range_is_not_disclosure_or_cross():
    c = _case("600276", "short")
    raw_sentence = c["original_violations"][0]["evidence"][1]["sentence"]
    quote_sentence = c["original_violations"][0]["evidence"][0]["sentence"]
    reg, gate = _gate({"market_report": quote_sentence, "smart_money_report": raw_sentence}, c["trade_date"])
    assert not any(r["basis"] == "pit_raw" for r in reg["price_refs"])
    assert not any(v["kind"] == "cross_basis_coordinate_mix" for v in gate["violations"])


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["symbol"] + "-" + c["horizon"])
def test_full_frozen_horizon_replay(case):
    _, gate = _gate(case["reports"], case["trade_date"])
    assert gate["status"] == ("blocked" if case["symbol"].startswith("000858") else "pass"), gate["violations"]
    if case["symbol"].startswith("000858"):
        assert {v["kind"] for v in gate["violations"]} == {"cross_basis_coordinate_mix"}


def test_frozen_000858_direct_raw_qfq_comparison_still_blocked():
    c = _case("000858", "short")
    sentence = c["original_violations"][0]["evidence"][0]["sentence"]
    reg, gate = _gate({"market_report": "现价 69.67 元。",
                       "news_report": c["reports"]["news_report"]}, c["trade_date"])
    assert any(r["basis"] == "pit_raw" for r in reg["price_refs"])
    assert any(v["kind"] == "cross_basis_coordinate_mix" for v in gate["violations"])


def test_dated_disclosure_explicit_reference_chain_blocks_once_not_fanout():
    reg, gate = _gate({"market_report": "现价 69.67 元。",
                       "news_report": "2026-09-20 已成交回购均价 71.01 元。现价 69.67 元已跌破回购底价。"})
    cross = [v for v in gate["violations"] if v["kind"] == "cross_basis_coordinate_mix"]
    assert len(cross) == 1, cross
    reg, gate = _gate({"market_report": "现价 69.67 元。",
                       "news_report": "2026-09-20 已成交回购均价 71.01 元。VWMA 69.67 元保持平稳。"})
    assert not any(v["kind"] == "cross_basis_coordinate_mix" for v in gate["violations"])


def test_json_fields_do_not_cross_feed_executable_anchor():
    text = ('MANAGER_VERDICT: {"stop": "盘中止损需观察", '
            '"bull_interpretation": "波段0.618强力支撑风险微弱", '
            '"target": "前复权目标价 69.67 元"}')
    assert [v for v, _, _ in extract_executable_levels(text)] == [69.67]


def test_dated_disclosure_comparison_without_price_on_same_line_still_blocks():
    reports = {"market_report": "现价 69.67 元。",
               "news_report": "2026-09-20 公司公告回购成交均价 71.01 元。现价 69.67 元低于回购价。"}
    _, gate = _gate(reports)
    assert any(v["kind"] == "cross_basis_coordinate_mix" for v in gate["violations"])


def test_same_sentence_disclosure_coordinate_and_fact_only_controls():
    disclosed = "2026-09-21 公司公告已成交回购价格区间下沿 296.26 元。"
    _, gate = _gate({"market_report": "现价 293.50 元。",
                     "news_report": disclosed + "现价 293.50 元一举击穿回购价格区间下沿 296.26 元。"})
    assert "cross_basis_coordinate_mix" in {v["kind"] for v in gate["violations"]}
    _, gate = _gate({"market_report": "7月8日收盘 1199.30 元。",
                     "news_report": "2026-07-08 大宗交易成交均价1192.14元，溢价0.28%接盘，并在7月8日收涨于1199.30元。"})
    assert "cross_basis_coordinate_mix" not in {v["kind"] for v in gate["violations"]}
    _, gate = _gate({"market_report": "现价1199.30元。",
                     "news_report": "2026-07-08 大宗交易成交均价1192.14元与现价1199.30元靠近形成支撑共振。"})
    assert "cross_basis_coordinate_mix" in {v["kind"] for v in gate["violations"]}


def test_disclosure_name_reference_does_not_require_comparison_verb():
    reports = {"market_report": "现价 35.80 元。",
               "news_report": "2026-05-21 公司公告已成交回购底价 36.02 元。"
                              "在现价 35.80 元区间依托回购底价分批低吸。"}
    _, gate = _gate(reports)
    assert "cross_basis_coordinate_mix" in {v["kind"] for v in gate["violations"]}
    reports["news_report"] = "2026-05-21 公司公告已成交回购底价 36.02 元。" \
                             "现价 35.80 元仅作技术观察，资金回购承接情绪有所改善。"
    _, gate = _gate(reports)
    assert "cross_basis_coordinate_mix" not in {v["kind"] for v in gate["violations"]}


def test_undated_disclosure_name_used_as_coordinate_is_not_typed_or_executable():
    reports = {"market_report": "收盘 20.50 元。",
               "news_report": "大宗交易成交位（19.50-20.20元）与布林下轨20.50元共振。"}
    reg, gate = _gate(reports)
    assert not any(r["basis"] == "raw" for r in reg["price_refs"] if r["source"] == "news_report")
    assert "cross_basis_coordinate_mix" in {v["kind"] for v in gate["violations"]}
    _, gate = _gate({"macro_report": "299元附近回购密集承接区构成强支撑，短线需谨慎。"})
    assert "cross_basis_coordinate_mix" in {v["kind"] for v in gate["violations"]}


def test_four_redteam_report_coordinates_when_readonly_corpus_available():
    import sqlite3
    from tradingagents.agents.utils.price_ref_registry import REPORT_FIELDS, _split_sentences

    db = Path("/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db")
    if not db.exists():
        pytest.skip("production corpus unavailable; synthetic tests remain mandatory")
    coordinates = json.loads((Path(__file__).parent / "fixtures/dav1392_r2_redteam.json").read_text())
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for c in coordinates:
            row = conn.execute("select result_data from reports where id=?", (c["report_id"],)).fetchone()
            assert row, c
            horizon = json.loads(row[0])["horizons"][c["horizon"]]
            sentences = _split_sentences(horizon[c["field"]])
            assert all(i < len(sentences) for i in c["sentence_indices"]), c
            reports = {f: horizon[f] for f in REPORT_FIELDS if isinstance(horizon.get(f), str)}
            _, gate = _gate(reports, horizon["trade_date"])
            assert gate["status"] == "blocked" and any(
                v["kind"] == "cross_basis_coordinate_mix" and v["source"] == c["field"]
                for v in gate["violations"]), c
    finally:
        conn.close()


def test_cross_triggers_single_revision_and_records_complete_post_gate(monkeypatch):
    monkeypatch.setenv(PRICE_REF_REVISION_ENV, "on")
    text = "2026-09-20 回购成交均价 71.01 元，现价 69.67 元低于回购价。"
    revised = "2026-09-20 回购成交均价 71.01 元，与前复权现价 69.67 元双列展示，不可直接比较。"
    class LLM:
        calls = 0
        async def ainvoke(self, messages):
            self.calls += 1
            return SimpleNamespace(content=revised)
    llm = LLM()
    out, record = asyncio.run(maybe_revise_role_report(
        {"trade_date": "2026-09-29", "market_report": "现价 69.67 元。"},
        role_key="news", report_field="news_report", text=text, llm=llm))
    assert llm.calls == 1
    assert record["triggered"] is True
    assert any(p["kind"] == "cross_basis_coordinate_mix" for p in record["problems"])
    assert record["post_gate"]["status"] == "pass"
    assert out == revised
