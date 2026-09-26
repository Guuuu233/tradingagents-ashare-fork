import logging
import os
from typing import Any, Optional

from langchain_openai import ChatOpenAI

_logger = logging.getLogger(__name__)

from .base_client import BaseLLMClient
from .concurrency_gate import acquire_llm_slot, acquire_llm_slot_async
from .validators import validate_model


class UnifiedChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass that strips incompatible params for certain models."""

    def __init__(self, **kwargs):
        # 彻底移除重试参数，由构造函数统一控制
        kwargs.pop("response_parse_retries", None)
        kwargs.pop("response_parse_retry_delay", None)

        model = kwargs.get("model") or kwargs.get("model_name", "")
        base_url = kwargs.get("base_url")

        # LOG_LEVEL=DEBUG 时开启 LangChain verbose，打印完整的 LLM 请求和响应
        if os.environ.get("LOG_LEVEL", "").upper() == "DEBUG":
            kwargs["verbose"] = True

        # DAV-1314: 自定义 base_url（如 CPA）时 langchain-openai 默认不开启
        # stream_usage，流式响应不会回用量 → llm_call_logs 全为 NULL。显式开启，
        # 让请求带上 stream_options.include_usage；上游不回时 usage 记 NULL。
        kwargs.setdefault("stream_usage", True)

        # DAV-1314: 统一用量采集 —— 挂上全局回调，覆盖所有角色/返修/意图解析/
        # 结构化提取，不再依赖各角色手写 log_llm_call。
        from .usage_hook import attach_usage_logger

        kwargs = attach_usage_logger(kwargs)

        # 1. Reasoning models (O1 etc) typically don't support temperature
        if self._is_reasoning_model(model):
            kwargs.pop("temperature", None)
            kwargs.pop("top_p", None)

        # 2. Moonshot (Kimi) models often strictly require temperature=1
        if self._is_moonshot_model(model, base_url):
            kwargs["temperature"] = 1

        # 3. devin/* models reject temperature=0 with a 502 invalid_argument
        # (DAV-1325). Near-greedy 0.01 keeps behavior close to deterministic;
        # an explicitly configured non-zero value is passed through unchanged.
        # Reasoning devin models (e.g. devin/gpt-5-5, devin/*-thinking) are
        # excluded: rule 1 already stripped temperature and they must keep
        # the provider default rather than get one re-injected.
        if (
            self._is_devin_model(model)
            and not self._is_reasoning_model(model)
            and not kwargs.get("temperature")
        ):
            kwargs["temperature"] = 0.01

        super().__init__(**kwargs)

    # DAV-1331: concurrency gate on all four call paths. LangChain funnels
    # every entry point into exactly one of _generate/_agenerate/_stream/
    # _astream (non-streaming goes to *_generate; streaming and the
    # _should_stream fallback inside *_generate_with_cache go to *_stream),
    # so gating all four covers sync/async + streaming/non-streaming once.
    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        with acquire_llm_slot(self.model_name):
            return super()._generate(*args, **kwargs)

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        async with acquire_llm_slot_async(self.model_name):
            return await super()._agenerate(*args, **kwargs)

    def _stream(self, *args: Any, **kwargs: Any) -> Any:
        # The slot is held for the whole generator lifecycle; closing the
        # generator early (broken stream, caller abort) still releases it
        # via the context manager's finally.
        with acquire_llm_slot(self.model_name):
            yield from super()._stream(*args, **kwargs)

    async def _astream(self, *args: Any, **kwargs: Any) -> Any:
        async with acquire_llm_slot_async(self.model_name):
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        result = super().invoke(input=input, config=config, **kwargs)
        if _logger.isEnabledFor(logging.DEBUG):
            content = result.content if hasattr(result, "content") else str(result)
            _logger.debug(f"[LLM Response] model={self.model_name} length={len(content)}\n{content}")
        return result

    @staticmethod
    def _is_reasoning_model(model: str) -> bool:
        """Check if model is a reasoning model."""
        model_lower = str(model).lower()
        return (
            model_lower.startswith("o1")
            or model_lower.startswith("o3")
            or "gpt-5" in model_lower
            or "-r1" in model_lower
            or "thinking" in model_lower
            or "reasoning" in model_lower
        )

    @staticmethod
    def _is_moonshot_model(model: str, base_url: Optional[str] = None) -> bool:
        """Check if model or base_url is from Moonshot (Kimi)."""
        m = str(model).lower()
        b = (base_url or "").lower()
        return "moonshot" in m or "kimi" in m or "moonshot" in b or "kimi" in b

    @staticmethod
    def _is_devin_model(model: str) -> bool:
        """Check if model is a devin/* model (devin/swe-2, devin/swe-1-7, ...)."""
        return str(model).lower().startswith("devin/")


class OpenAIClient(BaseLLMClient):
    """Client for OpenAI, Ollama, OpenRouter, xAI, and DeepSeek providers."""

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)
        self.provider = provider.lower()

    def get_llm(self) -> Any:
        """Return configured ChatOpenAI instance with long timeout and transient-error retry."""
        llm_kwargs = {"model": self.model}

        if not UnifiedChatOpenAI._is_reasoning_model(self.model):
            llm_kwargs["temperature"] = self.kwargs.get("temperature", 0)

        # ── 稳定性配置 ──
        # 瞬时错误重试（DAV-91）：交由 OpenAI SDK 内置重试策略实现。SDK 只对
        # "请求根本没有成功响应"的瞬时故障重试——网络层（ConnectionError/EOF/
        # ReadTimeout/APIConnectionError）、上游 5xx（500/502/503）、限流 429、
        # 以及服务器 x-should-retry 标记；401/403/400/404/内容审查等不可重试错误
        # 直接失败，不浪费配额。正常完成的调用零重试，不因重试重复扣费。
        # 次数由 TA_LLM_RETRY_TIMES 环境变量控制（默认 2，0 恢复完全禁用），
        # 也可用构造参数 max_retries 覆盖；SDK 内部为指数退避 + 抖动。
        llm_kwargs["max_retries"] = self._resolve_max_retries()

        # 超长超时：默认 300 秒，给足推理模型思考时间
        llm_kwargs["timeout"] = self.kwargs.get("timeout", 300.0)
        
        target_url = self.base_url or "https://api.openai.com/v1"
        if self.provider == "xai": target_url = "https://api.x.ai/v1"
        elif self.provider == "openrouter": target_url = "https://openrouter.ai/api/v1"
        elif self.provider == "ollama": target_url = "http://localhost:11434/v1"
        elif self.provider == "deepseek": target_url = "https://api.deepseek.com"

        _logger.info(
            "[LLM Client] Init %s (%s) at %s (Retries=%s, Timeout=%ss)",
            self.provider,
            self.model,
            target_url,
            llm_kwargs["max_retries"],
            llm_kwargs["timeout"],
        )

        if self.provider == "xai":
            llm_kwargs["base_url"] = "https://api.x.ai/v1"
            api_key = os.environ.get("XAI_API_KEY")
            if api_key: llm_kwargs["api_key"] = api_key
        elif self.provider == "openrouter":
            llm_kwargs["base_url"] = "https://openrouter.ai/api/v1"
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if api_key: llm_kwargs["api_key"] = api_key
        elif self.provider == "ollama":
            llm_kwargs["base_url"] = "http://localhost:11434/v1"
            llm_kwargs["api_key"] = "ollama"
        elif self.provider == "deepseek":
            llm_kwargs["base_url"] = self.base_url or "https://api.deepseek.com"
            api_key = os.environ.get("DEEPSEEK_API_KEY")
            if api_key: llm_kwargs["api_key"] = api_key
        elif self.base_url:
            llm_kwargs["base_url"] = self.base_url

        # Pass remaining keys. http_client / http_async_client 用于注入自定义
        # httpx 客户端（代理、测试 MockTransport 等），透传给 ChatOpenAI 底层 SDK。
        for key in ("api_key", "callbacks", "reasoning_effort", "http_client", "http_async_client"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        return UnifiedChatOpenAI(**llm_kwargs)

    def _resolve_max_retries(self) -> int:
        """解析瞬时错误重试次数。

        优先级：构造参数 max_retries > 环境变量 TA_LLM_RETRY_TIMES > 默认 2。
        0 保留旧行为（完全禁用重试）。
        """
        if "max_retries" in self.kwargs:
            return max(0, int(self.kwargs["max_retries"]))
        raw = os.environ.get("TA_LLM_RETRY_TIMES", "2")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            _logger.warning("Invalid TA_LLM_RETRY_TIMES=%r, falling back to 2", raw)
            value = 2
        return max(0, value)

    def validate_model(self) -> bool:
        return validate_model(self.provider, self.model, base_url=self.base_url)
