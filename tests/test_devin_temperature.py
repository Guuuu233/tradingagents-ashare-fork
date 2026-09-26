"""Tests for the devin/* temperature=0 workaround (DAV-1325).

devin/* models reject temperature=0 with ``502 invalid_argument`` upstream.
``UnifiedChatOpenAI`` must rewrite an unset/zero temperature to 0.01 for
``devin/``-prefixed models while leaving every other model untouched.
"""

from tradingagents.llm_clients.openai_client import OpenAIClient, UnifiedChatOpenAI


def _make_llm(model: str, **kwargs) -> UnifiedChatOpenAI:
    client = OpenAIClient(
        model=model,
        base_url="https://example.invalid/v1",
        api_key="sk-fake-key",
        **kwargs,
    )
    return client.get_llm()


class TestDevinTemperatureRewrite:
    def test_devin_default_temperature_rewritten_to_0_01(self):
        """No explicit temperature → get_llm defaults to 0 → must ship 0.01."""
        llm = _make_llm("devin/swe-2")
        assert isinstance(llm, UnifiedChatOpenAI)
        assert llm.temperature == 0.01

    def test_devin_explicit_zero_rewritten_to_0_01(self):
        llm = _make_llm("devin/swe-2", temperature=0)
        assert llm.temperature == 0.01

    def test_devin_other_model_also_rewritten(self):
        llm = _make_llm("devin/swe-1-7")
        assert llm.temperature == 0.01

    def test_devin_unset_temperature_on_unified_client(self):
        """temperature key absent entirely → still rewritten to 0.01."""
        llm = UnifiedChatOpenAI(
            model="devin/swe-2",
            base_url="https://example.invalid/v1",
            api_key="sk-fake-key",
        )
        assert llm.temperature == 0.01

    def test_devin_explicit_value_passes_through(self):
        llm = _make_llm("devin/swe-2", temperature=0.7)
        assert llm.temperature == 0.7

    def test_devin_explicit_small_value_passes_through(self):
        llm = _make_llm("devin/swe-2", temperature=0.2)
        assert llm.temperature == 0.2

    def test_devin_reasoning_model_keeps_no_temperature(self):
        """devin/gpt-5-5 is a reasoning model: rule 1 strips temperature and
        rule 3 must NOT re-inject 0.01 (DAV-1325 rework)."""
        llm = _make_llm("devin/gpt-5-5")
        assert getattr(llm, "temperature", None) is None

    def test_devin_thinking_model_keeps_no_temperature(self):
        """devin/claude-opus-4-6-thinking hits the 'thinking' reasoning
        keyword; temperature must stay absent, not rewritten to 0.01."""
        llm = _make_llm("devin/claude-opus-4-6-thinking")
        assert getattr(llm, "temperature", None) is None

    def test_devin_reasoning_explicit_temperature_stripped(self):
        """Explicit temperature on a reasoning devin model is dropped by
        rule 1; rule 3 must not resurrect it."""
        llm = _make_llm("devin/gpt-5-5", temperature=0.5)
        assert getattr(llm, "temperature", None) is None


class TestOtherModelsUnaffected:
    def test_non_devin_default_remains_zero(self):
        llm = _make_llm("gpt-4o")
        assert llm.temperature == 0

    def test_devin_without_slash_not_rewritten(self):
        """Only the ``devin/`` prefix is covered, not 'devin' alone."""
        llm = _make_llm("devin-swe-2")
        assert llm.temperature == 0

    def test_non_devin_explicit_zero_stays_zero(self):
        llm = _make_llm("qwen-max", temperature=0)
        assert llm.temperature == 0
