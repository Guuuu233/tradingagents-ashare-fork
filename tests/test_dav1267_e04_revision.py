"""DAV-1267 — 研究经理 E-04 违规定向返修单元测试（零 LLM）。

覆盖总控门禁五条：
1. 触发条件：只在失败项全为 E-04 时触发（e04_only_failures）；
2. 价格问题与 E-04 违规合并为一次调用；
3. 同义词断言检查（near_hit / novel_term / 正常改写不误伤）；
4. 各项保护（签名、长度、确定性检查、同义词逃逸 → 丢稿）；
5. 开关关闭时零调用。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tradingagents.agents.utils.e04_revision import (
    E04_REVISION_ENV,
    E04_REVISION_VERSION,
    E04_SYNONYM_TERMS,
    E04_VIOLATION_PREFIX,
    build_e04_revision_message,
    e04_only_failures,
    e04_revision_enabled,
    find_synonym_escapes,
    locate_hit_spans,
)
from tradingagents.agents.utils.price_ref_revision import (
    PRICE_REF_REVISION_ENV,
    maybe_revise_role_report,
)


@pytest.fixture(autouse=True)
def _enable_revision(monkeypatch):
    monkeypatch.setenv(PRICE_REF_REVISION_ENV, "on")
    monkeypatch.setenv(E04_REVISION_ENV, "on")


class FakeLLM:
    def __init__(self, replies=()):
        self.calls = 0
        self.replies = list(replies)
        self.messages_seen = []

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages_seen.append(list(messages))
        text = self.replies.pop(0) if self.replies else ""
        return SimpleNamespace(content=text)


def _state():
    return {"company_of_interest": "002475.SZ", "trade_date": "2026-08-14"}


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


def _pool_state():
    """带冻结 pool 的 state：自拟 45.00 元会被判 unspecified_basis 问题。"""
    st = _state()
    st["price_ref_source"] = {
        "stock_data": STOCK_DATA,
        "indicators": {"close_10_ema": 56.73},
        "price_basis": "vendor_qfq",
        "symbol": "002475.SZ",
        "trade_date": "2026-08-14",
    }
    return st


def _e04_req(original: str, sentences, verified=("c1", "c2")):
    hits = [
        {"violation": f"{E04_VIOLATION_PREFIX}：测试违规{i}",
         "sentence": s}
        for i, s in enumerate(sentences, 1)
    ]
    return {
        "triggered": True,
        "hits": hits,
        "hit_spans": locate_hit_spans(original, hits),
        "verified_claim_ids": list(verified),
    }


# ── 1. 触发条件 ────────────────────────────────────────────────────


class TestE04OnlyFailures:
    def test_all_e04(self):
        fc = [f"{E04_VIOLATION_PREFIX}：a", f"{E04_VIOLATION_PREFIX}：b"]
        assert e04_only_failures(fc) is True

    def test_empty(self):
        assert e04_only_failures([]) is False
        assert e04_only_failures(None) is False

    def test_mixed(self):
        fc = [f"{E04_VIOLATION_PREFIX}：a", "证据覆盖率不足：x"]
        assert e04_only_failures(fc) is False

    def test_non_e04_only(self):
        assert e04_only_failures(["胜方与正文矛盾"]) is False

    def test_non_str_items(self):
        assert e04_only_failures([f"{E04_VIOLATION_PREFIX}：a", 123]) is False


# ── 2. 命中句定位 ──────────────────────────────────────────────────


def test_locate_hit_spans():
    text = "第一段。市场已完全反映利好，继续看涨。第三段。"
    sent = "市场已完全反映利好，继续看涨"
    hits = [{"sentence": sent}]
    spans = locate_hit_spans(text, hits)
    idx = text.find(sent)
    assert spans == [(idx, idx + len(sent))]
    assert text[idx:idx + len(sent)] == sent


def test_locate_hit_spans_unresolved():
    spans = locate_hit_spans("abc", [{"sentence": "不存在"}])
    assert spans == [None]


# ── 3. 同义词检查 ──────────────────────────────────────────────────


class TestSynonymEscapes:
    ORIG = "分析：市场已完全反映该利好。结论维持 BUY。"

    def _hits(self):
        return [{"sentence": "市场已完全反映该利好", "violation": "v"}]

    def test_delete_passes(self):
        revised = "分析：。结论维持 BUY。"
        spans = locate_hit_spans(self.ORIG, self._hits())
        assert find_synonym_escapes(self.ORIG, revised, spans) == []

    def test_uncertain_rewrite_passes(self):
        revised = "分析：已定价状态为 unknown。结论维持 BUY。"
        spans = locate_hit_spans(self.ORIG, self._hits())
        assert find_synonym_escapes(self.ORIG, revised, spans) == []

    def test_near_hit_escape(self):
        revised = "分析：该利好已被消化。结论维持 BUY。"
        spans = locate_hit_spans(self.ORIG, self._hits())
        esc = find_synonym_escapes(self.ORIG, revised, spans)
        assert esc and esc[0]["term"] == "已被消化"
        assert esc[0]["rule"] == "near_hit"

    @pytest.mark.parametrize("term", E04_SYNONYM_TERMS)
    def test_each_term_detected_near_hit(self, term):
        revised = f"分析：该利好{term}。结论维持 BUY。"
        spans = locate_hit_spans(self.ORIG, self._hits())
        esc = find_synonym_escapes(self.ORIG, revised, spans)
        assert any(e["term"] == term for e in esc)

    def test_novel_term_elsewhere_escape(self):
        """词表项被搬到远离命中句的位置：原稿无该词 → novel_term 逃逸。"""
        pad = "中期看，基本面稳健，" * 30  # 把命中句推远
        orig = pad + "市场已完全反映该利好。结论维持 BUY。"
        revised = pad + "该利好程度尚不确定，已被消化。结论维持 BUY。"
        spans = locate_hit_spans(orig, self._hits())
        esc = find_synonym_escapes(orig, revised, spans)
        assert any(e["term"] == "已被消化" for e in esc)

    def test_unresolved_hit_conservative(self):
        """命中句无法定位时，变更区出现词表项一律逃逸。"""
        revised = "分析：该利好已被消化。结论维持 BUY。"
        esc = find_synonym_escapes(self.ORIG, revised, [None])
        assert esc and esc[0]["rule"] == "unresolved_hit"

    def test_preexisting_term_not_escape(self):
        """原稿本来就有的词表项且不在命中窗口内的正常编辑不判逃逸。"""
        orig = ("前文：该风险市场已知。"
                + "无关段落，" * 60
                + "市场已完全反映该利好。结论 BUY。")
        # 改写命中句为不确定表述；远处已存在的「市场已知」未动
        revised = orig.replace("市场已完全反映该利好", "已定价状态为 unknown")
        hits = [{"sentence": "市场已完全反映该利好", "violation": "v"}]
        spans = locate_hit_spans(orig, hits)
        assert find_synonym_escapes(orig, revised, spans) == []

    def test_identical_text(self):
        assert find_synonym_escapes("abc", "abc", [(0, 3)]) == []


# ── 4. 返修消息 ────────────────────────────────────────────────────


def test_build_e04_message_lists_hits_and_options():
    hits = [{"violation": f"{E04_VIOLATION_PREFIX}：x",
             "sentence": "业绩超预期"}]
    msg = build_e04_revision_message(hits, ["c1", "c2"])
    assert "业绩超预期" in msg
    assert "c1" in msg and "c2" in msg
    assert "删除该句" in msg and "unknown" in msg
    assert "已被消化" in msg  # 禁止同义词条款


def test_build_e04_message_merges_price_section():
    msg = build_e04_revision_message(
        [{"violation": "v", "sentence": "已定价"}],
        ["c1"], price_section="价格 45.00 无法归因")
    assert "已定价" in msg and "45.00" in msg
    assert msg.index("已定价") < msg.index("45.00")


# ── 5. 合并为一次调用 + 保护 ───────────────────────────────────────


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _async(coro):
    return asyncio.run(coro)


ORIG_TEXT = "市场已完全反映该利好。" + "填充段落。" * 40 + "结论 BUY。"
REVISED_OK = "已定价状态为 unknown。" + "填充段落。" * 40 + "结论 BUY。"


class TestMaybeReviseE04:
    def test_single_call_merges_e04_and_price(self):
        """价格问题与 E-04 违规共存 → 仍只调用一次模型。"""
        # 45.00 为池外自拟价格 → unspecified_basis；需 pool 才有价格问题，
        # 这里 state 无 pool → problems 为空，E-04 单独触发也是一次调用。
        llm = FakeLLM([REVISED_OK])
        text, rec = _async(maybe_revise_role_report(
            _state(), role_key="research_manager",
            report_field="investment_plan", text=ORIG_TEXT, llm=llm,
            e04=_e04_req(ORIG_TEXT, ["市场已完全反映该利好"]),
        ))
        assert llm.calls == 1
        msg = llm.messages_seen[0][-1].content
        assert "市场已完全反映该利好" in msg
        assert text == REVISED_OK
        e04r = rec["e04_revision"]
        assert e04r["version"] == E04_REVISION_VERSION
        assert e04r["triggered"] is True
        assert e04r["adopted"] == "revised"
        assert rec["adopted"] == "revised"

    def test_synonym_escape_discards(self):
        revised = "该利好已被消化。" + "填充段落。" * 40 + "结论 BUY。"
        llm = FakeLLM([revised])
        text, rec = _async(maybe_revise_role_report(
            _state(), role_key="research_manager",
            report_field="investment_plan", text=ORIG_TEXT, llm=llm,
            e04=_e04_req(ORIG_TEXT, ["市场已完全反映该利好"]),
        ))
        assert text == ORIG_TEXT
        assert rec["adopted"] == "original"
        assert rec["discard_reason"] == "synonym_escape"
        assert rec["e04_revision"]["adopted"] == "original"
        assert rec["e04_revision"]["escapes"]

    def test_signature_change_discards(self):
        revised = "已定价状态为 unknown。" + "填充段落。" * 40 + "结论 SELL。"
        llm = FakeLLM([revised])
        text, rec = _async(maybe_revise_role_report(
            _state(), role_key="research_manager",
            report_field="investment_plan", text=ORIG_TEXT, llm=llm,
            e04=_e04_req(ORIG_TEXT, ["市场已完全反映该利好"]),
        ))
        assert text == ORIG_TEXT
        assert rec["discard_reason"] == "conclusion_changed"

    def test_truncation_discards(self):
        llm = FakeLLM(["结论 BUY。"])
        text, rec = _async(maybe_revise_role_report(
            _state(), role_key="research_manager",
            report_field="investment_plan", text=ORIG_TEXT, llm=llm,
            e04=_e04_req(ORIG_TEXT, ["市场已完全反映该利好"]),
        ))
        assert text == ORIG_TEXT
        assert rec["discard_reason"] == "excessive_truncation"

    def test_deterministic_check_failure_discards(self):
        llm = FakeLLM([REVISED_OK])
        text, rec = _async(maybe_revise_role_report(
            _state(), role_key="research_manager",
            report_field="investment_plan", text=ORIG_TEXT, llm=llm,
            deterministic_check=lambda t: False,
            e04=_e04_req(ORIG_TEXT, ["市场已完全反映该利好"]),
        ))
        assert text == ORIG_TEXT
        assert rec["discard_reason"] == "revised_check_failed"

    def test_not_triggered_no_hit_no_call(self):
        """E-04 未触发且无价格问题 → 零调用，仅记录。"""
        llm = FakeLLM([REVISED_OK])
        text, rec = _async(maybe_revise_role_report(
            _state(), role_key="research_manager",
            report_field="investment_plan", text=ORIG_TEXT, llm=llm,
            e04={"triggered": False, "hits": [], "hit_spans": []},
        ))
        assert llm.calls == 0
        assert text == ORIG_TEXT
        assert rec["e04_revision"]["triggered"] is False
        assert rec["e04_revision"]["revision_attempted"] is False


# ── 5.5 🟡-1：同义词检查只在 E-04 段参与返修时执行 ────────────────


class TestSynonymCheckScope:
    PRICE_TEXT = "建议关注 45.00 元支撑位。" + "填充段落。" * 40

    def test_price_only_synonym_term_not_escape(self):
        """只有价格问题（e04 未触发）时，返修稿出现词表项不得按
        synonym_escape 丢弃——走原 DAV-1249 v2 路径。"""
        revised = "该利好已被消化。" + "填充段落。" * 40
        llm = FakeLLM([revised])
        text, rec = _async(maybe_revise_role_report(
            _pool_state(), role_key="research_manager",
            report_field="investment_plan", text=self.PRICE_TEXT, llm=llm,
            e04={"triggered": False, "hits": [], "hit_spans": []},
        ))
        assert llm.calls == 1
        assert rec["adopted"] == "revised"
        assert text == revised
        e04r = rec["e04_revision"]
        assert e04r["triggered"] is False
        assert e04r["revision_attempted"] is False
        assert e04r["discard_reason"] is None

    def test_price_plus_e04_synonym_check_still_runs(self):
        """价格问题与 E-04 同时存在时，同义词检查照常执行。"""
        orig = "市场已完全反映该利好。建议关注 45.00 元支撑位。" + "填充段落。" * 40
        revised = "该利好已被消化。建议关注 56.58 元支撑位。" + "填充段落。" * 40
        llm = FakeLLM([revised])
        text, rec = _async(maybe_revise_role_report(
            _pool_state(), role_key="research_manager",
            report_field="investment_plan", text=orig, llm=llm,
            e04=_e04_req(orig, ["市场已完全反映该利好"]),
        ))
        assert llm.calls == 1
        assert rec["adopted"] == "original"
        assert rec["discard_reason"] == "synonym_escape"
        assert text == orig
        e04r = rec["e04_revision"]
        assert e04r["revision_attempted"] is True
        assert e04r["adopted"] == "original"
        assert e04r["discard_reason"] == "synonym_escape"


# ── 6. 开关关闭时零调用 ────────────────────────────────────────────


class TestSwitchOff:
    def test_e04_env_off(self, monkeypatch):
        monkeypatch.delenv(E04_REVISION_ENV, raising=False)
        assert e04_revision_enabled() is False

    def test_price_env_off_zero_call(self, monkeypatch):
        """总开关未置位：即使给了 e04 请求也不调用模型。"""
        monkeypatch.delenv(PRICE_REF_REVISION_ENV, raising=False)
        llm = FakeLLM([REVISED_OK])
        text, rec = _async(maybe_revise_role_report(
            _state(), role_key="research_manager",
            report_field="investment_plan", text=ORIG_TEXT, llm=llm,
            e04=_e04_req(ORIG_TEXT, ["市场已完全反映该利好"]),
        ))
        assert llm.calls == 0
        assert text == ORIG_TEXT
        assert rec == {}
