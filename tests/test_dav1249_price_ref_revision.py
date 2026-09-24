"""DAV-1249 — price_ref 逐角色校验 + 定向返修单元测试（零 LLM）。

覆盖总控门禁四条：
1. 检查函数与 finalize（registry + gate Rule 1）口径一致；
2. 返修每角色最多触发一次；
3. 两条保护措施生效（改变结论丢弃 / 过度删减丢弃）；
4. 没有问题价格时不调用模型。
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.agents.utils.price_ref_registry import (
    PRICE_REF_SOURCE_KEY,
    build_price_ref_registry,
)
from tradingagents.agents.utils.price_basis_gate import evaluate_price_basis_gate
from tradingagents.agents.utils.price_ref_revision import (
    PRICE_REF_REVISION_DUMP_ENV,
    PRICE_REF_REVISION_ENV,
    PRICE_REF_REVISION_STATE_KEY,
    build_price_ref_table,
    build_revision_message,
    check_role_price_refs,
    conclusion_signature,
    maybe_revise_role_report,
)


@pytest.fixture(autouse=True)
def _enable_revision(monkeypatch):
    """v2 起返修由环境开关控制；单测一律置开。"""
    monkeypatch.setenv(PRICE_REF_REVISION_ENV, "on")

STOCK_DATA = (
    "# Stock data for 002475.SZ\n"
    "# price_basis: vendor_qfq\n"
    "date,low,close,volume,open,high\n"
    "2026-08-10,55.00,57.50,1000.0,55.30,57.86\n"
    "2026-08-11,54.52,55.90,1100.0,57.60,56.23\n"
    "2026-08-12,55.30,56.80,1200.0,55.90,57.58\n"
    "2026-08-13,56.05,57.90,1300.0,56.70,58.75\n"
    "2026-08-14,55.80,56.58,1400.0,56.60,56.88\n"
)

INDICATORS = {"close_10_ema": 56.73, "close_50_sma": 55.10}


def make_state(**over):
    state = {
        "company_of_interest": "002475.SZ",
        "trade_date": "2026-08-14",
        PRICE_REF_SOURCE_KEY: {
            "stock_data": STOCK_DATA,
            "indicators": dict(INDICATORS),
            "price_basis": "vendor_qfq",
            "symbol": "002475.SZ",
            "trade_date": "2026-08-14",
        },
    }
    state.update(over)
    return state


class FakeLLM:
    """记录调用次数、消息序列并按队列返回内容的假 LLM。"""

    def __init__(self, replies=()):
        self.calls = 0
        self.replies = list(replies)
        self.messages_seen = []

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages_seen.append(list(messages))
        text = self.replies.pop(0) if self.replies else ""
        return SimpleNamespace(content=text)


ORIG_MESSAGES = [
    SystemMessage(content="原始系统提示：含用户自定义注入，不得删减"),
    HumanMessage(content="原始输入上下文"),
]


# ── 1. 检查函数与 finalize 口径一致 ─────────────────────────────────


def test_check_flags_unspecified_decision_driving_price():
    """自拟支撑位（池外值）+ 坐标词 → unspecified_basis 问题。"""
    text = "建议关注 45.00 元支撑位，跌破则止损。"
    problems = check_role_price_refs(make_state(), "news_report", text)
    assert len(problems) == 1
    assert problems[0]["value"] == 45.0
    assert problems[0]["kind"] == "unspecified_basis"
    assert problems[0]["sentence"]


def test_check_consistent_with_gate_rule1():
    """check 标记的每个问题都必须能在 gate 违规中找到对应（同值同 kind）。"""
    text = (
        "目标价 62.30 元；止损设在 45.00 元。"
        "参考 8月14日 收盘 56.58 元（合规引用）。"
    )
    state = make_state()
    problems = check_role_price_refs(state, "investment_plan", text)
    assert {p["value"] for p in problems} == {62.30, 45.00}

    # finalize 口径：registry + gate Rule 1
    reports = {"investment_plan": text}
    from tradingagents.agents.utils.price_ref_registry import _pool_from_state
    reg = build_price_ref_registry(
        reports, cutoff="2026-08-14", pool=_pool_from_state(state)
    )
    gate_state = {
        "trade_date": "2026-08-14",
        "investment_plan": text,
        "price_refs": reg["price_refs"],
        "price_basis_validation": reg["validation"],
    }
    gate = evaluate_price_basis_gate(gate_state)
    rule1_violations = [
        v for v in gate["violations"]
        if v["kind"] in ("decision_driving_unspecified_basis",
                         "decision_driving_missing_as_of")
    ]
    gate_ref_ids = {rid for v in rule1_violations for rid in v["ref_ids"]}
    problem_ref_ids = {p["ref_id"] for p in problems}
    # check 每 ref 一条记录；gate 可能对同一 ref 同时报 basis/as_of 两条。
    assert problem_ref_ids == gate_ref_ids


def test_check_passes_table_and_derived_prices():
    """表内价格（C5 桥接）与规范 derived_estimate 不触发返修。"""
    text = (
        "现价参考 8月14日 收盘 56.58 元，止损参考 8月14日 最低 55.80 元。"
        "按 25 倍 PE 对应股价约 62.30 元。"
    )
    problems = check_role_price_refs(make_state(), "news_report", text)
    assert problems == []


def test_back_reference_from_prior_report():
    """back-reference：本报告引用此前已产出报告中的 vendor_qfq 值 → 不算问题。"""
    prior = "8月14日 收盘价 56.58 元。"
    state = make_state(market_report=prior)
    text = "延续市场报告，56.58 元一带形成支撑。"
    problems = check_role_price_refs(state, "news_report", text)
    assert problems == []


# ── 2. 返修只触发一次 ────────────────────────────────────────────


def test_revision_only_once():
    text = "建议关注 45.00 元支撑位。"
    rec = {"revision_attempted": True, "adopted": "revised"}
    state = make_state(**{PRICE_REF_REVISION_STATE_KEY: {"news": rec}})
    llm = FakeLLM(["改写后文本"])

    async def run():
        return await maybe_revise_role_report(
            state, role_key="news", report_field="news_report",
            text=text, llm=llm,
        )

    final_text, new_rec = asyncio.run(run())
    assert final_text == text
    assert new_rec == {}          # 调用方不得覆盖既有记录
    assert llm.calls == 0


# ── 3. 保护措施 ──────────────────────────────────────────────────


PROBLEM_TEXT = (
    "公司公告与资金面显示分歧，短线波动加大。"
    "建议关注 45.00 元支撑位，跌破需控制风险。"
    "情绪面偏谨慎但趋势未破坏，仓位建议维持中性偏积极配置。\n\n"
    "<!-- VERDICT: {\"direction\": \"看多\", \"confidence\": 0.7} -->"
)


def _run_revision(state, llm, text=PROBLEM_TEXT, **kw):
    kw.setdefault("orig_messages", ORIG_MESSAGES)

    async def run():
        return await maybe_revise_role_report(
            state, role_key="news", report_field="news_report",
            text=text, llm=llm, **kw,
        )
    return asyncio.run(run())


def test_revision_adopted_when_conclusion_unchanged():
    revised = PROBLEM_TEXT.replace("45.00 元支撑", "8月14日 收盘 56.58 元支撑")
    llm = FakeLLM([revised])
    final_text, rec = _run_revision(make_state(), llm)
    assert llm.calls == 1
    assert rec["triggered"] is True
    assert rec["adopted"] == "revised"
    assert final_text == revised
    assert rec["post_revision_problem_count"] == 0


def test_revision_discarded_when_conclusion_changed():
    revised = (
        PROBLEM_TEXT.replace("45.00 元支撑", "8月14日 收盘 56.58 元支撑")
        .replace("看多", "看空")
    )
    llm = FakeLLM([revised])
    final_text, rec = _run_revision(make_state(), llm)
    assert rec["adopted"] == "original"
    assert rec["discard_reason"] == "conclusion_changed"
    assert final_text == PROBLEM_TEXT


def test_revision_discarded_when_truncated():
    llm = FakeLLM(["<!-- VERDICT: {\"direction\": \"看多\", \"confidence\": 0.7} -->"])
    final_text, rec = _run_revision(make_state(), llm)
    assert rec["adopted"] == "original"
    assert rec["discard_reason"] == "excessive_truncation"
    assert final_text == PROBLEM_TEXT


def test_revision_call_failure_keeps_original():
    class BoomLLM(FakeLLM):
        async def ainvoke(self, messages):
            self.calls += 1
            raise RuntimeError("network down")

    llm = BoomLLM()
    final_text, rec = _run_revision(make_state(), llm)
    assert rec["adopted"] == "original"
    assert rec["discard_reason"] == "revision_call_failed"
    assert final_text == PROBLEM_TEXT


# ── 4. 无问题价格不调模型 ─────────────────────────────────────────


def test_no_problem_no_llm_call():
    text = "现价参考 8月14日 收盘 56.58 元。"
    llm = FakeLLM(["不应被调用"])
    final_text, rec = _run_revision(make_state(), llm, text=text)
    assert llm.calls == 0
    assert rec["triggered"] is False
    assert rec["revision_attempted"] is False
    assert final_text == text


# ── 辅助函数 ──────────────────────────────────────────────────────


def test_build_price_ref_table_rows_bridge_verified():
    source = {"stock_data": STOCK_DATA, "indicators": dict(INDICATORS)}
    rows = build_price_ref_table(source, "002475.SZ", "2026-08-14")
    assert rows
    assert all(r["context"] for r in rows)
    assert any(r["label"].endswith("收盘") for r in rows)


def test_build_revision_message_lists_problems_and_table():
    problems = [{"value": 45.0, "kind": "unspecified_basis", "sentence": "支撑 45.00 元"}]
    msg = build_revision_message(problems, "- 8月14日 收盘 56.58 元")
    assert "45.0" in msg and "支撑 45.00 元" in msg
    assert "8月14日 收盘 56.58 元" in msg
    assert "结论、方向、交易动作、概率均不得改变" in msg


def test_conclusion_signature_detects_direction_change():
    a = "文本 <!-- VERDICT: {\"direction\": \"看多\"} -->"
    b = "文本 <!-- VERDICT: {\"direction\": \"看空\"} -->"
    assert conclusion_signature(a) != conclusion_signature(b)
    assert conclusion_signature(a) == conclusion_signature(a + " 补充")


# ── V1：签名只比结论字段 ──────────────────────────────────────────


def test_v1_signature_ignores_reason_and_price_fields():
    """机读块内的价格与理由文本变化不视为改变结论。"""
    a = ('<!-- MANAGER_VERDICT: {"winner": "bear", "direction": "偏空", '
         '"reason": "放量下跌", "target": "37.36", "stop_loss": "34.50"} -->')
    b = ('<!-- MANAGER_VERDICT: {"winner": "bear", "direction": "偏空", '
         '"reason": "完全不同的理由文本", "target": "8月14日收盘 56.58", '
         '"stop_loss": "34.50"} -->')
    assert conclusion_signature(a) == conclusion_signature(b)
    # 结论字段变化仍被捕获
    c = a.replace('"偏空"', '"偏多"')
    assert conclusion_signature(a) != conclusion_signature(c)


def test_v1_revision_may_edit_prices_inside_machine_block():
    """V1 直通：返修稿只改 MANAGER_VERDICT 内价格/理由 → 采用。"""
    revised = PROBLEM_TEXT.replace(
        '"confidence": 0.7',
        '"confidence": 0.7, "target": "8月14日收盘56.58元"')
    revised = revised.replace("45.00 元支撑", "8月14日 收盘 56.58 元支撑")
    llm = FakeLLM([revised])
    final_text, rec = _run_revision(make_state(), llm)
    assert rec["adopted"] == "revised"
    assert final_text == revised


# ── V2：返修调用带完整原始上下文 ──────────────────────────────────


def test_v2_revision_call_includes_original_system_prompt():
    llm = FakeLLM(["改写后文本无价格问题"])
    _run_revision(make_state(), llm)
    assert llm.calls == 1
    msgs = llm.messages_seen[0]
    # 原始消息序列在前：系统提示原样保留（含注入内容）
    assert isinstance(msgs[0], SystemMessage)
    assert "用户自定义注入" in msgs[0].content
    assert msgs[1].content == "原始输入上下文"
    # 末尾追加：原稿 + 返修要求
    assert msgs[-2].content == PROBLEM_TEXT
    assert "可引用价位表" in msgs[-1].content


def test_v2_revision_call_accepts_bare_string_prompt():
    """裸 prompt 调用形态（research_manager/risk_manager）也带上文。"""
    llm = FakeLLM(["改写后文本"])
    _run_revision(make_state(), llm, orig_messages="系统提示字符串")
    msgs = llm.messages_seen[0]
    assert msgs[0].content == "系统提示字符串"
    assert msgs[-2].content == PROBLEM_TEXT


# ── V3：确定性检查回归 → 丢弃 ─────────────────────────────────────


def test_v3_consistency_regression_discards_revision():
    """原稿过检查、返修稿不过 → 丢弃并记 consistency_regression。"""
    def fake_consistency(t):
        return "硬伤标记" not in t

    revised = PROBLEM_TEXT.replace("45.00 元支撑", "8月14日 收盘 56.58 元支撑")
    revised += " 硬伤标记"
    llm = FakeLLM([revised])
    final_text, rec = _run_revision(make_state(), llm,
                                    deterministic_check=fake_consistency)
    assert rec["adopted"] == "original"
    assert rec["discard_reason"] == "consistency_regression"
    assert rec["orig_check_passed"] is True
    assert rec["revised_check_passed"] is False
    assert final_text == PROBLEM_TEXT


def test_v3_both_fail_marks_revised_check_failed():
    """原稿也不过检查时丢弃原因不为 regression（不产生 rescue 假象）。"""
    def fake_consistency(t):
        return False

    revised = PROBLEM_TEXT.replace("45.00 元支撑", "8月14日 收盘 56.58 元支撑")
    llm = FakeLLM([revised])
    final_text, rec = _run_revision(make_state(), llm,
                                    deterministic_check=fake_consistency)
    assert rec["adopted"] == "original"
    assert rec["discard_reason"] == "revised_check_failed"
    assert rec["orig_check_passed"] is False


def test_v3_revised_passing_check_is_adopted():
    revised = PROBLEM_TEXT.replace("45.00 元支撑", "8月14日 收盘 56.58 元支撑")
    llm = FakeLLM([revised])
    final_text, rec = _run_revision(make_state(), llm,
                                    deterministic_check=lambda t: True)
    assert rec["adopted"] == "revised"
    assert rec["revised_check_passed"] is True
    assert final_text == revised


# ── V4：记录字段与全文落盘 ────────────────────────────────────────


def test_v4_record_carries_signatures_and_sha(monkeypatch, tmp_path):
    monkeypatch.setenv(PRICE_REF_REVISION_DUMP_ENV, str(tmp_path))
    revised = PROBLEM_TEXT.replace("45.00 元支撑", "8月14日 收盘 56.58 元支撑")
    llm = FakeLLM([revised])
    _final, rec = _run_revision(make_state(), llm)
    assert rec["adopted"] == "revised"
    assert rec["orig_signature"] == conclusion_signature(PROBLEM_TEXT)
    assert rec["revised_signature"] == conclusion_signature(revised)
    sha = hashlib.sha256(revised.encode("utf-8")).hexdigest()
    assert rec["revised_sha256"] == sha
    # 全文落盘到隔离目录，记录本身不含全文
    dumped = list(tmp_path.glob("news_*.txt"))
    assert len(dumped) == 1
    assert dumped[0].read_text(encoding="utf-8") == revised
    assert "revised_text" not in rec


def test_v4_discarded_revision_also_recorded(monkeypatch, tmp_path):
    monkeypatch.setenv(PRICE_REF_REVISION_DUMP_ENV, str(tmp_path))
    revised = PROBLEM_TEXT.replace("看多", "看空")  # 结论变 → 丢弃
    llm = FakeLLM([revised])
    _final, rec = _run_revision(make_state(), llm)
    assert rec["adopted"] == "original"
    assert rec["revised_sha256"] == hashlib.sha256(
        revised.encode("utf-8")).hexdigest()
    assert rec["revised_signature"] != rec["orig_signature"]
    assert list(tmp_path.glob("news_*.txt"))  # 丢弃稿同样留档可复核


# ── 开关：未置位时完全关闭（对照组/生产默认行为） ──────────────────


def test_switch_off_returns_text_and_no_record(monkeypatch):
    monkeypatch.delenv(PRICE_REF_REVISION_ENV, raising=False)
    llm = FakeLLM(["不应被调用"])
    final_text, rec = _run_revision(make_state(), llm)
    assert final_text == PROBLEM_TEXT
    assert rec == {}
    assert llm.calls == 0
