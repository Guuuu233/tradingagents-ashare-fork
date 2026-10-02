"""DAV-1323: reports, not role/stage/horizon/custom text, own the cache prefix."""
import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from tradingagents.agents.researchers import bear_researcher, bull_researcher
from tradingagents.prompts.en import PROMPTS as EN_PROMPTS
from tradingagents.prompts.zh import PROMPTS as ZH_PROMPTS


class ModelBoundary(Exception):
    """Stop at the actual astream boundary without invoking any provider."""


class CaptureModel:
    def __init__(self, retry=False):
        self.prompts = []
        self.retry = retry

    async def astream(self, prompt):
        self.prompts.append(prompt)
        if self.retry and len(self.prompts) == 1:
            yield SimpleNamespace(content="invalid machine block")
        else:
            raise ModelBoundary


class EmptyMemory:
    def get_memories(self, situation, n_matches):
        return []


REPORTS = {
    "macro_report": "宏观{保留花括号}\n第二行",
    "market_report": "市场",
    "sentiment_report": "情绪",
    "news_report": "新闻",
    "fundamentals_report": "基本面",
    "smart_money_report": "资金",
    "volume_price_report": "量价",
}
EXPECTED_PREFIX = {
    "zh": "可用材料：\n宏观报告：宏观{保留花括号}\n第二行\n市场报告：市场\n情绪报告：情绪\n新闻报告：新闻\n基本面报告：基本面\n主力资金报告：资金\n量价报告：量价\n",
    "en": "Use these inputs:\nMacro report: 宏观{保留花括号}\n第二行\nMarket report: 市场\nSentiment report: 情绪\nNews report: 新闻\nFundamentals report: 基本面\nSmart money report: 资金\nVolume-Price report: 量价\n",
}


def capture(monkeypatch, role, language, horizon, stage, placement, retry=False, reports=None):
    module = bull_researcher if role == "bull" else bear_researcher
    from tradingagents.graph import intent_parser
    config = lambda: {"prompt_language": language}
    monkeypatch.setattr(module, "get_config", config)
    monkeypatch.setattr(intent_parser, "get_config", config)
    count = {"opening": 0, "challenge": 2, "rebuttal": 4, "legacy": 4}[stage]
    state = {
        **(REPORTS if reports is None else reports),
        "horizon": horizon,
        "user_intent": {"focus_areas": ["资金"], "specific_questions": ["保留{问题}？"]},
        "investment_debate_state": {
            "count": count,
            "protocol_stage": stage,
            "feature_flags": {"v2_debate_enabled": stage != "legacy"},
            "history": "VARIABLE_HISTORY",
            "current_response": "VARIABLE_RESPONSE",
            "claims": [],
            "round_summary": "VARIABLE_SUMMARY",
            "round_goal": "VARIABLE_GOAL",
        },
    }
    llm = CaptureModel(retry)
    factory = getattr(module, f"create_{role}_researcher")
    node = factory(llm, EmptyMemory(), custom_prompt="ROLE_CUSTOM" if placement else "", placement=placement or "after_data")
    with pytest.raises(ModelBoundary):
        asyncio.run(node(state))
    return llm.prompts


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("horizon", ["short", "medium"])
@pytest.mark.parametrize("stage", ["opening", "challenge", "rebuttal", "legacy"])
@pytest.mark.parametrize("placement", ["", "before_data", "after_data"])
def test_complete_calls_share_reports_before_role_stage_horizon_and_round_data(monkeypatch, language, horizon, stage, placement):
    """A role, stage, horizon or injection prepended to reports breaks this test."""
    for role in ("bull", "bear"):
        prompt = capture(monkeypatch, role, language, horizon, stage, placement)[0]
        assert prompt.encode().startswith(EXPECTED_PREFIX[language].encode())
        role_text = ("你是多头研究员" if role == "bull" else "你是空头研究员") if language == "zh" else f"You are a {role.title()} Analyst"
        horizon_text = "【分析视角】" if language == "zh" else "[Analysis Perspective]"
        history_text = "辩论历史：" if language == "zh" else "Debate history:"
        assert len(EXPECTED_PREFIX[language]) <= prompt.index(role_text) < prompt.index(horizon_text) < prompt.index(history_text)
        if stage == "opening":
            assert "VARIABLE_HISTORY" not in prompt
            assert "VARIABLE_RESPONSE" not in prompt
        else:
            assert prompt.index("VARIABLE_HISTORY") > prompt.index(horizon_text)
        if placement:
            assert prompt.count("ROLE_CUSTOM") == 1
            assert prompt.index("ROLE_CUSTOM") > prompt.index(horizon_text)
        assert prompt.count("宏观{保留花括号}") == 1
        assert "保留{问题}？" in prompt


@pytest.mark.parametrize("role", ["bull", "bear"])
@pytest.mark.parametrize("language", ["zh", "en"])
def test_protocol_retry_keeps_the_entire_original_prefix(monkeypatch, role, language):
    """Moving the retry warning to the front would lose the shared cache prefix."""
    first, retry = capture(monkeypatch, role, language, "medium", "opening", "after_data", retry=True)
    assert first.startswith(EXPECTED_PREFIX[language])
    assert retry.startswith(first)
    assert "协议重试警告" in retry[len(first):]


@pytest.mark.parametrize("role", ["bull", "bear"])
@pytest.mark.parametrize("language", ["zh", "en"])
def test_missing_reports_keep_fixed_labels_and_no_role_dependent_fallback(monkeypatch, role, language):
    prompt = capture(monkeypatch, role, language, "short", "opening", "", reports={})[0]
    expected = "可用材料：\n宏观报告：\n市场报告：\n情绪报告：\n新闻报告：\n基本面报告：\n主力资金报告：\n量价报告：\n" if language == "zh" else "Use these inputs:\nMacro report: \nMarket report: \nSentiment report: \nNews report: \nFundamentals report: \nSmart money report: \nVolume-Price report: \n"
    assert prompt.startswith(expected)


@pytest.mark.parametrize("language,key,digest", [
    ("zh", "bull_prompt", "ea0c536439993b54f514c8401c5b44ab0e4493be7e978e0bcbc43c935572fd3c"),
    ("zh", "bear_prompt", "309aea456052ebdb26814c25c6c99ae57555701f58d717ed140597158e57ad4f"),
    ("en", "bull_prompt", "715a98392a0c2b70c2682ac2ec309863b1ad0a4d52723120d17c7b6e87a4e664"),
    ("en", "bear_prompt", "66f506980234ac52c63da6790fe3b505b4b59884c9ceba58a2f67e4ed8cdb19b"),
])
def test_reorder_preserves_every_template_line(language, key, digest):
    """Contract is order-only: catch any wording/whitespace/placeholder change.

    Frozen line-multiset hashes are from parent 8817c660fb2e, not the builder
    under test. Strip the inline before_data slot (whose occurrence is checked
    separately) before sorting to permit relocation but not wording changes.
    """
    prompts = ZH_PROMPTS if language == "zh" else EN_PROMPTS
    assert prompts[key].count("{custom_prompt_before_data}") == 1
    text = prompts[key].replace("{custom_prompt_before_data}", "")
    assert hashlib.sha256("\n".join(sorted(text.splitlines())).encode()).hexdigest() == digest
