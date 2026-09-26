"""DAV-1314: unified LLM usage ledger tests.

Covers: stream_usage on UnifiedChatOpenAI, streaming & non-streaming usage
capture via LLMUsageLogger, NULL columns when the upstream returns no usage,
cache/reasoning detail extraction, retry flag, report_id/horizon/role metadata,
and the per-role usage_summary written at report finalize.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from api.database import LLMCallLogDB, get_db_ctx, log_llm_call
from api.usage_logging import (
    LLMUsageLogger,
    _extract_llm_result,
    build_llm_usage_summary,
)


def _usage_metadata(**over):
    um = {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "input_token_details": {"cache_read": 40},
        "output_token_details": {"reasoning": 12},
    }
    um.update(over)
    return um


def _llm_result(*, usage=None, content="你好", finish="stop", model="gpt-6-luna"):
    msg = AIMessage(content=content, usage_metadata=usage)
    gen = ChatGeneration(
        message=msg,
        generation_info={"finish_reason": finish, "model_name": model},
    )
    return LLMResult(generations=[[gen]], llm_output={})


def _rows_for(report_id):
    with get_db_ctx() as db:
        return (
            db.query(LLMCallLogDB)
            .filter(LLMCallLogDB.report_id == report_id)
            .all()
        )


class TestExtractLlmResult:
    def test_usage_metadata_fields(self):
        out = _extract_llm_result(_llm_result(usage=_usage_metadata()))
        assert out["input_tokens"] == 120
        assert out["output_tokens"] == 30
        assert out["total_tokens"] == 150
        assert out["cached_prompt_tokens"] == 40
        assert out["reasoning_tokens"] == 12
        assert out["finish_reason"] == "stop"
        assert out["model_name"] == "gpt-6-luna"
        assert out["response_chars"] == len("你好")

    def test_missing_usage_is_none_not_error(self):
        out = _extract_llm_result(_llm_result(usage=None))
        assert out["input_tokens"] is None
        assert out["total_tokens"] is None
        assert out["cached_prompt_tokens"] is None
        assert out["reasoning_tokens"] is None

    def test_llm_output_token_usage_fallback(self):
        gen = ChatGeneration(message=AIMessage(content="ok"), generation_info={})
        result = LLMResult(
            generations=[[gen]],
            llm_output={
                "token_usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
                "model_name": "m1",
            },
        )
        out = _extract_llm_result(result)
        assert (out["input_tokens"], out["output_tokens"], out["total_tokens"]) == (5, 2, 7)
        assert out["model_name"] == "m1"


class TestCallbackLogging:
    def test_end_to_end_row(self):
        handler = LLMUsageLogger()
        report_id = uuid.uuid4().hex
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            {},
            [[{"role": "user", "content": "hi"}]],
            run_id=run_id,
            metadata={
                "report_id": report_id,
                "horizon": "short",
                "langgraph_node": "Market Analyst",
            },
        )
        handler.on_llm_end(_llm_result(usage=_usage_metadata()), run_id=run_id)

        rows = _rows_for(report_id)
        assert len(rows) == 1
        r = rows[0]
        assert r.agent_name == "Market Analyst"
        assert r.horizon == "short"
        assert r.prompt_tokens == 120
        assert r.cached_prompt_tokens == 40
        assert r.completion_tokens == 30
        assert r.reasoning_tokens == 12
        assert r.total_tokens == 150
        assert r.elapsed_seconds is not None
        assert r.retried is False

    def test_llm_role_metadata_wins_over_node_name(self):
        handler = LLMUsageLogger()
        report_id = uuid.uuid4().hex
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            {},
            [],
            run_id=run_id,
            metadata={"llm_role": "意图解析", "langgraph_node": "Some Node",
                      "report_id": report_id},
        )
        handler.on_llm_end(_llm_result(usage=_usage_metadata()), run_id=run_id)
        rows = _rows_for(report_id)
        assert rows[0].agent_name == "意图解析"

    def test_missing_usage_writes_nulls(self):
        handler = LLMUsageLogger()
        report_id = uuid.uuid4().hex
        run_id = uuid.uuid4()
        handler.on_llm_start({}, ["hi"], run_id=run_id,
                           metadata={"report_id": report_id, "llm_role": "x"})
        handler.on_llm_end(_llm_result(usage=None), run_id=run_id)
        r = _rows_for(report_id)[0]
        assert r.prompt_tokens is None
        assert r.completion_tokens is None
        assert r.total_tokens is None
        assert r.cached_prompt_tokens is None
        assert r.reasoning_tokens is None

    def test_retry_flag(self):
        handler = LLMUsageLogger()
        report_id = uuid.uuid4().hex
        run_id = uuid.uuid4()
        handler.on_chat_model_start({}, [], run_id=run_id,
                                    metadata={"report_id": report_id})
        handler.on_retry(object(), run_id=run_id)
        handler.on_llm_end(_llm_result(usage=_usage_metadata()), run_id=run_id)
        assert _rows_for(report_id)[0].retried is True

    def test_error_drops_run_and_no_row(self):
        handler = LLMUsageLogger()
        run_id = uuid.uuid4()
        handler.on_chat_model_start({}, [], run_id=run_id, metadata={})
        handler.on_llm_error(RuntimeError("boom"), run_id=run_id)
        assert str(run_id) not in handler._runs

    def test_report_id_falls_back_to_contextvar(self):
        from api.database import current_report_id

        handler = LLMUsageLogger()
        report_id = uuid.uuid4().hex
        token = current_report_id.set(report_id)
        try:
            run_id = uuid.uuid4()
            handler.on_chat_model_start({}, [], run_id=run_id, metadata={})
            handler.on_llm_end(_llm_result(usage=_usage_metadata()), run_id=run_id)
        finally:
            current_report_id.reset(token)
        assert _rows_for(report_id)[0].report_id == report_id


def _sse_body(*, usage):
    chunks = [
        {"id": "c1", "object": "chat.completion.chunk", "created": 1,
         "model": "gpt-6-luna",
         "choices": [{"index": 0, "delta": {"role": "assistant", "content": "你"},
                      "finish_reason": None}]},
        {"id": "c1", "object": "chat.completion.chunk", "created": 1,
         "model": "gpt-6-luna",
         "choices": [{"index": 0, "delta": {"content": "好"}, "finish_reason": "stop"}]},
    ]
    if usage is not None:
        chunks.append({
            "id": "c1", "object": "chat.completion.chunk", "created": 1,
            "model": "gpt-6-luna", "choices": [], "usage": usage,
        })
    lines = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return lines + "data: [DONE]\n\n"


_USAGE_PAYLOAD = {
    "prompt_tokens": 88,
    "completion_tokens": 2,
    "total_tokens": 90,
    "prompt_tokens_details": {"cached_tokens": 16},
    "completion_tokens_details": {"reasoning_tokens": 0},
}


def _make_llm(requests_log, stream_usage_payload=_USAGE_PAYLOAD):
    from tradingagents.llm_clients.openai_client import UnifiedChatOpenAI

    def _handle(request: httpx.Request) -> httpx.Response:
        requests_log.append(json.loads(request.content.decode()))
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content.decode())
            if body.get("stream"):
                return httpx.Response(
                    200,
                    text=_sse_body(usage=stream_usage_payload),
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(200, json={
                "id": "r1", "object": "chat.completion", "created": 1,
                "model": "gpt-6-luna",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "你好"}}],
                "usage": _USAGE_PAYLOAD,
            })
        return httpx.Response(404, text="nope")

    transport = httpx.MockTransport(_handle)
    return UnifiedChatOpenAI(
        model="gpt-6-luna",
        base_url="http://test.invalid/v1",
        api_key="sk-test",
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


class TestUnifiedChatOpenAIIntegration:
    def test_constructor_enables_stream_usage_and_attaches_logger(self):
        from tradingagents.llm_clients.openai_client import UnifiedChatOpenAI

        llm = UnifiedChatOpenAI(
            model="gpt-6-luna", base_url="http://test.invalid/v1",
            api_key="sk-test",
        )
        assert llm.stream_usage is True
        assert any(isinstance(c, LLMUsageLogger) for c in (llm.callbacks or []))
        # no double-attach when caller supplies the same logger
        from api.usage_logging import LLM_USAGE_LOGGER

        llm2 = UnifiedChatOpenAI(
            model="gpt-6-luna", base_url="http://test.invalid/v1",
            api_key="sk-test", callbacks=[LLM_USAGE_LOGGER],
        )
        assert sum(isinstance(c, LLMUsageLogger) for c in llm2.callbacks) == 1

    def test_invoke_records_usage(self):
        reqs = []
        llm = _make_llm(reqs)
        report_id = uuid.uuid4().hex
        llm.invoke("hi", config={"metadata": {"report_id": report_id,
                                              "llm_role": "结构化提取"}})
        r = _rows_for(report_id)[0]
        assert r.agent_name == "结构化提取"
        assert r.prompt_tokens == 88
        assert r.total_tokens == 90

    def test_astream_records_usage_and_sends_stream_options(self):
        reqs = []
        llm = _make_llm(reqs)
        report_id = uuid.uuid4().hex

        async def _run():
            text = ""
            async for chunk in llm.astream(
                "hi", config={"metadata": {"report_id": report_id,
                                           "langgraph_node": "Trader",
                                           "horizon": "medium"}}
            ):
                text += chunk.content if isinstance(chunk.content, str) else ""
            return text

        text = asyncio.run(_run())
        assert text == "你好"
        stream_reqs = [b for b in reqs if b.get("stream")]
        assert stream_reqs, "no streaming request captured"
        assert stream_reqs[0].get("stream_options", {}).get("include_usage") is True
        r = _rows_for(report_id)[0]
        assert r.agent_name == "Trader"
        assert r.horizon == "medium"
        assert r.prompt_tokens == 88
        assert r.completion_tokens == 2
        assert r.total_tokens == 90
        assert r.cached_prompt_tokens == 16
        assert r.reasoning_tokens == 0

    def test_astream_without_usage_chunk_records_null(self):
        reqs = []
        llm = _make_llm(reqs, stream_usage_payload=None)
        report_id = uuid.uuid4().hex

        async def _run():
            async for _ in llm.astream(
                "hi", config={"metadata": {"report_id": report_id}}
            ):
                pass

        asyncio.run(_run())
        r = _rows_for(report_id)[0]
        assert r.prompt_tokens is None
        assert r.total_tokens is None


class TestNonOpenAIProviderCoverage:
    """DAV-1314 返修项：Anthropic/Google 客户端同样必须挂用量回调。"""

    def _logger_on(self, llm):
        loggers = [c for c in (llm.callbacks or []) if isinstance(c, LLMUsageLogger)]
        assert len(loggers) == 1
        return loggers[0]

    def test_anthropic_client_mounts_logger(self):
        from tradingagents.llm_clients.anthropic_client import AnthropicClient

        llm = AnthropicClient(model="claude-test", api_key="sk-test").get_llm()
        self._logger_on(llm)

    def test_google_client_mounts_logger(self):
        from tradingagents.llm_clients.google_client import GoogleClient

        llm = GoogleClient(model="gemini-test", api_key="sk-test").get_llm()
        self._logger_on(llm)

    def test_caller_supplied_logger_not_duplicated(self):
        from api.usage_logging import LLM_USAGE_LOGGER
        from tradingagents.llm_clients.anthropic_client import AnthropicClient
        from tradingagents.llm_clients.google_client import GoogleClient

        for client in (
            AnthropicClient(model="claude-test", api_key="sk-test",
                            callbacks=[LLM_USAGE_LOGGER]),
            GoogleClient(model="gemini-test", api_key="sk-test",
                         callbacks=[LLM_USAGE_LOGGER]),
        ):
            llm = client.get_llm()
            self._logger_on(llm)

    def _drive_handler(self, llm, report_id, *, usage):
        handler = self._logger_on(llm)
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            {}, [], run_id=run_id,
            metadata={"report_id": report_id, "langgraph_node": "Bull Researcher"},
        )
        handler.on_llm_end(_llm_result(usage=usage), run_id=run_id)

    def test_anthropic_handler_writes_row(self):
        from tradingagents.llm_clients.anthropic_client import AnthropicClient

        llm = AnthropicClient(model="claude-test", api_key="sk-test").get_llm()
        report_id = uuid.uuid4().hex
        self._drive_handler(llm, report_id, usage=_usage_metadata())
        r = _rows_for(report_id)[0]
        assert r.agent_name == "Bull Researcher"
        assert r.prompt_tokens == 120
        assert r.cached_prompt_tokens == 40
        assert r.reasoning_tokens == 12

    def test_google_handler_writes_row_and_null_usage(self):
        from tradingagents.llm_clients.google_client import GoogleClient

        llm = GoogleClient(model="gemini-test", api_key="sk-test").get_llm()
        report_id = uuid.uuid4().hex
        self._drive_handler(llm, report_id, usage=None)
        r = _rows_for(report_id)[0]
        assert r.agent_name == "Bull Researcher"
        assert r.prompt_tokens is None
        assert r.total_tokens is None


class TestUsageSummary:
    def test_summary_aggregates_by_role(self):
        report_id = uuid.uuid4().hex
        log_llm_call(agent_name="Market Analyst", report_id=report_id,
                     model_name="m", prompt_tokens=10, completion_tokens=5,
                     total_tokens=15, cached_prompt_tokens=4,
                     reasoning_tokens=0, elapsed_seconds=1.0, horizon="short")
        log_llm_call(agent_name="Market Analyst", report_id=report_id,
                     model_name="m", prompt_tokens=20, completion_tokens=5,
                     total_tokens=25, retried=True, horizon="medium")
        log_llm_call(agent_name="Bull Researcher", report_id=report_id,
                     model_name="m", prompt_tokens=7, completion_tokens=3,
                     total_tokens=10, horizon="short")
        other = uuid.uuid4().hex
        log_llm_call(agent_name="Noise", report_id=other,
                     prompt_tokens=999, total_tokens=999)

        s = build_llm_usage_summary(report_id)
        assert s["report_id"] == report_id
        assert s["call_count"] == 3
        assert s["prompt_tokens"] == 37
        assert s["cached_prompt_tokens"] == 4
        assert s["total_tokens"] == 50
        assert s["retried_calls"] == 1
        roles = {r["agent_name"]: r for r in s["by_role"]}
        assert set(roles) == {"Market Analyst", "Bull Researcher"}
        assert roles["Market Analyst"]["calls"] == 2
        assert roles["Market Analyst"]["prompt_tokens"] == 30
        assert roles["Market Analyst"]["horizons"] == ["medium", "short"]
        assert roles["Bull Researcher"]["total_tokens"] == 10

    def test_summary_none_when_empty_or_no_report(self):
        assert build_llm_usage_summary(None) is None
        assert build_llm_usage_summary(uuid.uuid4().hex) is None


class TestSchemaEnsure:
    def test_new_columns_added_to_legacy_table(self):
        from sqlalchemy import text as sa_text

        from api.database import _ensure_llm_call_log_schema, engine

        with engine.begin() as conn:
            cols = {row[1] for row in conn.execute(
                sa_text("PRAGMA table_info(llm_call_logs)"))}
        for c in ("horizon", "cached_prompt_tokens", "reasoning_tokens", "retried"):
            assert c in cols
        # idempotent
        _ensure_llm_call_log_schema()
