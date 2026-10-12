"""DAV-1314: unified LLM usage ledger tests.

Covers: stream_usage on UnifiedChatOpenAI, streaming & non-streaming usage
capture via LLMUsageLogger, NULL columns when the upstream returns no usage,
cache/reasoning detail extraction, retry flag, report_id/horizon/role metadata,
and the per-role usage_summary written at report finalize.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid

import httpx
from api.database import LLMCallLogDB, get_db_ctx, log_llm_call
from api.usage_logging import (
    LLMUsageLogger,
    _extract_llm_result,
    build_llm_usage_summary,
)
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult


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
    # DAV-1362 ③: async dispatch offloads the ledger commit to a writer
    # thread, so drain the queue before reading rows back.
    from api.usage_logging import flush_usage_writes

    flush_usage_writes()
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
        from api.database import _ensure_llm_call_log_schema, engine
        from sqlalchemy import text as sa_text

        with engine.begin() as conn:
            cols = {row[1] for row in conn.execute(
                sa_text("PRAGMA table_info(llm_call_logs)"))}
        for c in ("horizon", "cached_prompt_tokens", "reasoning_tokens",
                  "retried", "queue_seconds"):
            assert c in cols
        # idempotent
        _ensure_llm_call_log_schema()


class TestQueueSeconds:
    """DAV-1334: elapsed_seconds must exclude concurrency-gate queue wait;
    the wait is recorded in the separate queue_seconds column."""

    def _run_call(self, report_id, queue_wait=None):
        from tradingagents.llm_clients.concurrency_gate import (
            _record_queue_wait,
        )

        handler = LLMUsageLogger()
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            {},
            [[{"role": "user", "content": "hi"}]],
            run_id=run_id,
            metadata={"report_id": report_id, "langgraph_node": "N"},
        )
        if queue_wait is not None:
            # Simulates the gate having just acquired after queue_wait
            # seconds (it writes the per-run record at acquire time).
            _record_queue_wait(queue_wait)
        handler.on_llm_end(_llm_result(usage=_usage_metadata()), run_id=run_id)
        return run_id

    def test_queue_wait_split_out_of_elapsed(self):
        report_id = uuid.uuid4().hex
        self._run_call(report_id, queue_wait=50.0)
        rows = _rows_for(report_id)
        assert len(rows) == 1
        r = rows[0]
        assert r.queue_seconds == 50.0
        # The 50s gate wait must not leak into elapsed_seconds.
        assert r.elapsed_seconds is not None
        assert r.elapsed_seconds < 5

    def test_no_gate_wait_records_null_queue(self):
        report_id = uuid.uuid4().hex
        self._run_call(report_id, queue_wait=0.0)
        rows = _rows_for(report_id)
        assert len(rows) == 1
        assert rows[0].queue_seconds is None
        assert rows[0].elapsed_seconds is not None

    def test_consumed_wait_not_attributed_to_next_call(self):
        """After a gated call is logged, the per-run record is popped so a
        later call in the same context doesn't inherit the stale wait."""
        import tradingagents.llm_clients.concurrency_gate as cg

        report_id = uuid.uuid4().hex
        run_id = self._run_call(report_id, queue_wait=30.0)
        # This run's record was consumed at on_llm_end.
        assert str(run_id) not in cg._pop_queue_waits_for_tests()
        self._run_call(report_id)  # no new wait recorded
        rows = sorted(_rows_for(report_id), key=lambda r: r.created_at)
        assert len(rows) == 2
        assert rows[0].queue_seconds == 30.0
        assert rows[1].queue_seconds is None


# ── DAV-1426: streaming cumulative-usage inflation fix ──────────────────


def _sse_body_deepseek(chunks_usage):
    """SSE stream where EVERY content chunk carries cumulative usage —
    the deepseek-v4.1-flash pattern that triggered DAV-1426."""
    chunks = [
        {"id": "c1", "object": "chat.completion.chunk", "created": 1,
         "model": "deepseek-v4.1-flash",
         "choices": [{"index": 0, "delta": {"role": "assistant", "content": "你"},
                      "finish_reason": None}]},
        {"id": "c1", "object": "chat.completion.chunk", "created": 1,
         "model": "deepseek-v4.1-flash",
         "choices": [{"index": 0, "delta": {"content": "好"},
                      "finish_reason": "stop"}]},
    ]
    # Attach cumulative usage to every chunk, incl. a trailing usage-only one.
    for i, u in enumerate(chunks_usage):
        chunks[min(i, len(chunks) - 1)].setdefault("usage", u)
    lines = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return lines + "data: [DONE]\n\n"


_CUMULATIVE_USAGE = [
    {"prompt_tokens": 25700, "completion_tokens": 1, "total_tokens": 25701},
    {"prompt_tokens": 25700, "completion_tokens": 2, "total_tokens": 25702},
]


def _make_deepseek_llm(requests_log):
    from tradingagents.llm_clients.openai_client import UnifiedChatOpenAI

    def _handle(request: httpx.Request) -> httpx.Response:
        requests_log.append(json.loads(request.content.decode()))
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                text=_sse_body_deepseek(_CUMULATIVE_USAGE),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404, text="nope")

    transport = httpx.MockTransport(_handle)
    return UnifiedChatOpenAI(
        model="deepseek-v4.1-flash",
        base_url="http://test.invalid/v1",
        api_key="sk-test",
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


class TestCumulativeStreamUsage:
    """DAV-1426: providers that repeat cumulative usage on every chunk must
    produce last-chunk (not summed) token counts."""

    def test_chunk_merge_is_last_wins_not_sum(self):
        """Unit-level: merged usage_metadata equals the last chunk's values."""
        from langchain_core.messages import AIMessageChunk
        from langchain_core.messages.ai import add_ai_message_chunks

        c1 = AIMessageChunk(content="你", usage_metadata={
            "input_tokens": 25700, "output_tokens": 1, "total_tokens": 25701,
        })
        c2 = AIMessageChunk(content="好", usage_metadata={
            "input_tokens": 25700, "output_tokens": 2, "total_tokens": 25702,
        })
        merged = add_ai_message_chunks(c1, c2)
        assert merged.usage_metadata["input_tokens"] == 25700
        assert merged.usage_metadata["output_tokens"] == 2
        assert merged.usage_metadata["total_tokens"] == 25702
        # chunks handed to the merger are not mutated under the caller
        assert c2.usage_metadata["output_tokens"] == 2

    def test_details_merge_takes_max(self):
        from langchain_core.messages import AIMessageChunk
        from langchain_core.messages.ai import add_ai_message_chunks

        c1 = AIMessageChunk(content="a", usage_metadata={
            "input_tokens": 10, "output_tokens": 1, "total_tokens": 11,
            "input_token_details": {"cache_read": 5},
            "output_token_details": {"reasoning": 3},
        })
        c2 = AIMessageChunk(content="b", usage_metadata={
            "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
            "input_token_details": {"cache_read": 5},
            "output_token_details": {"reasoning": 7},
        })
        merged = add_ai_message_chunks(c1, c2)
        assert merged.usage_metadata["input_token_details"]["cache_read"] == 5
        assert merged.usage_metadata["output_token_details"]["reasoning"] == 7

    def test_extract_prefers_provider_body_over_inflated_merge(self):
        """When the merged usage is inflated but llm_output carries the
        provider's final body, extraction prefers the smaller real value."""
        um = {
            "input_tokens": 51400, "output_tokens": 3,
            "total_tokens": 51403,  # 2× the real value
        }
        result = _llm_result(usage=um)
        result.llm_output["token_usage"] = {
            "prompt_tokens": 25700, "completion_tokens": 2,
            "total_tokens": 25702,
        }
        out = _extract_llm_result(result)
        assert out["input_tokens"] == 25700
        assert out["output_tokens"] == 2
        assert out["total_tokens"] == 25702
        assert out["usage_unreliable"] is False

    def test_prompt_over_2m_flagged_unreliable(self):
        out = _extract_llm_result(_llm_result(usage={
            "input_tokens": 211_262_470, "output_tokens": 33_729_692,
            "total_tokens": 244_992_162,
        }))
        assert out["usage_unreliable"] is True
        # raw evidence preserved, not nulled
        assert out["input_tokens"] == 211_262_470

    def test_astream_deepseek_records_last_chunk_usage(self):
        """End-to-end: cumulative per-chunk usage ⇒ stored row equals the
        last chunk's values, not the sum."""
        reqs = []
        llm = _make_deepseek_llm(reqs)
        report_id = uuid.uuid4().hex

        async def _run():
            async for _ in llm.astream(
                "hi", config={"metadata": {"report_id": report_id}}
            ):
                pass

        asyncio.run(_run())
        r = _rows_for(report_id)[0]
        assert r.prompt_tokens == 25700
        assert r.completion_tokens == 2
        assert r.total_tokens == 25702
        assert r.usage_unreliable is False

    def test_usage_summary_excludes_unreliable_rows(self):
        report_id = uuid.uuid4().hex
        log_llm_call(
            agent_name="Bear", prompt_tokens=100, completion_tokens=10,
            total_tokens=110, report_id=report_id,
        )
        log_llm_call(
            agent_name="Bear", prompt_tokens=211_262_470,
            completion_tokens=33_729_692, total_tokens=244_992_162,
            report_id=report_id, usage_unreliable=True,
        )
        s = build_llm_usage_summary(report_id)
        assert s["call_count"] == 2
        assert s["usage_unreliable_count"] == 1
        assert s["prompt_tokens"] == 100
        assert s["completion_tokens"] == 10
        assert s["total_tokens"] == 110
        assert s["by_role"][0]["calls"] == 1


class TestDav1426StatsScript:
    """The stats script drops flagged/>2M rows and labels the deepseek
    10-01~10-02 window as unreliable."""

    def test_excludes_extreme_and_annotates_window(self, tmp_path):
        import sqlite3
        db = tmp_path / "t.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE llm_call_logs ("
            "id TEXT, model_name TEXT, agent_name TEXT, created_at TEXT,"
            " prompt_tokens INTEGER, completion_tokens INTEGER,"
            " total_tokens INTEGER, cached_prompt_tokens INTEGER,"
            " reasoning_tokens INTEGER, usage_unreliable BOOLEAN DEFAULT 0)"
        )
        conn.executemany(
            "INSERT INTO llm_call_logs VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("ok1", "gpt-6", "a", "2026-10-03 01:00:00", 100, 10, 110,
                 None, None, 0),
                ("bad1", "deepseek-v4.1-flash", "b", "2026-10-01 22:00:00",
                 211_262_470, 33_729_692, 244_992_162, None, None, 0),
                ("win1", "deepseek-v4.1-flash", "b", "2026-10-02 01:00:00",
                 60000, 500, 60500, None, None, 0),
                ("ok2", "deepseek-v4.1-flash", "b", "2026-10-03 01:00:00",
                 200, 20, 220, None, None, 0),
            ],
        )
        conn.commit()
        conn.close()

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dav1426_usage_stats",
            os.path.join(
                os.path.dirname(__file__), "..", "scripts",
                "dav1426_usage_stats.py",
            ),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        conn = mod._connect(str(db))
        report = mod.collect_stats(conn)
        conn.close()

        assert report["total_rows"] == 4
        # bad1 flagged >2M, win1 in the unreliable window → 2 excluded
        assert report["excluded_count"] == 2
        reasons = {e["id"]: e["reason"] for e in report["excluded_rows"]}
        assert reasons["bad1"] == "prompt_tokens>2M"
        assert reasons["win1"].startswith("deepseek_cumulative_usage_window")
        w = report["unreliable_window"]
        assert w["rows_in_window"] == 2  # bad1 + win1
        assert "unreliable" in w["label"]
        # deepseek totals only count the post-window row
        ds = report["by_model"]["deepseek-v4.1-flash"]
        assert ds["calls"] == 3
        assert ds["excluded_calls"] == 2
        assert ds["prompt_tokens"] == 200


class TestQueueWaitRunAttribution:
    """DAV-1362: queue_seconds must come from THIS call's gate wait only.

    Regression for the production bug where a repair call ("<role>/返修")
    recorded either the previous call's stale wait or its own wait folded
    into elapsed_seconds, because the ledger read a contextvar that the
    callback's copied/shielded dispatch context could not see correctly.
    """

    def _handler(self):
        return LLMUsageLogger()

    def _drive(self, handler, report_id, role, queued_s):
        """Simulate one full call: start callback → gate acquire → end.

        ``queued_s`` > 0 occupies all 4 devin/ slots first so the async
        gate really queues. Returns the run_id used.
        """
        import tradingagents.llm_clients.concurrency_gate as cg

        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            {}, [], run_id=run_id,
            metadata={"report_id": report_id, "llm_role": role},
        )
        if queued_s and queued_s > 0:
            # Simulate the acquire's write directly (deterministic: the
            # gate records the wait into the run bound by
            # begin_queue_wait inside on_chat_model_start).
            cg._record_queue_wait(queued_s)
        handler.on_llm_end(_llm_result(usage=_usage_metadata()),
                           run_id=run_id)
        return run_id

    def test_queued_then_repair_call_same_context(self):
        """Two sequential calls in the same node context: first queues,
        repair call queues again — each row must carry its own wait."""
        handler = self._handler()
        report_id = uuid.uuid4().hex
        self._drive(handler, report_id, "Smart Money Analyst", 0.40)
        self._drive(handler, report_id, "Smart Money Analyst/返修", 0.35)
        rows = sorted(_rows_for(report_id), key=lambda r: r.created_at)
        assert len(rows) == 2
        assert rows[0].agent_name == "Smart Money Analyst"
        assert rows[0].queue_seconds == 0.40
        assert rows[1].agent_name == "Smart Money Analyst/返修"
        assert rows[1].queue_seconds == 0.35

    def test_repair_call_without_queue_records_null_not_stale(self):
        """The production failure: a queued original call followed by an
        UNQUEUED repair call must not inherit the stale wait."""
        handler = self._handler()
        report_id = uuid.uuid4().hex
        self._drive(handler, report_id, "Smart Money Analyst", 0.40)
        self._drive(handler, report_id, "Smart Money Analyst/返修", 0.0)
        rows = sorted(_rows_for(report_id), key=lambda r: r.created_at)
        assert len(rows) == 2
        assert rows[0].queue_seconds == 0.40
        # Repair call had no wait — must be NULL, never the stale 0.4.
        assert rows[1].queue_seconds is None

    def test_wait_popped_at_end_leaves_no_residue(self):
        """The run record is removed at on_llm_end — nothing leaks into
        the shared map for a hypothetical later run_id collision."""
        import tradingagents.llm_clients.concurrency_gate as cg
        handler = self._handler()
        report_id = uuid.uuid4().hex
        run_id = self._drive(handler, report_id, "N", 0.5)
        assert str(run_id) not in cg._pop_queue_waits_for_tests()

    def test_error_path_drops_run_record(self):
        """on_llm_error pops the record too — failed calls cannot leak a
        wait into a later unrelated run."""
        import tradingagents.llm_clients.concurrency_gate as cg
        handler = self._handler()
        report_id = uuid.uuid4().hex
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            {}, [], run_id=run_id,
            metadata={"report_id": report_id},
        )
        cg._record_queue_wait(0.9)
        handler.on_llm_error(RuntimeError("boom"), run_id=run_id)
        assert str(run_id) not in cg._pop_queue_waits_for_tests()

    def test_run_inline_enabled(self):
        """run_inline=True is what makes on_chat_model_start run in the
        caller's context on the async path so begin_queue_wait's
        contextvar binding reaches the acquire. Guard it."""
        assert LLMUsageLogger().run_inline is True


class TestQueueWaitAcrossCallPaths:
    """DAV-1362: the per-run queue attribution must hold on all four real
    call paths — sync/async × streaming/non-streaming — through the real
    LangChain dispatch (MockTransport exercises the actual callback
    manager, gate, and ledger together).
    """

    def _devin_llm(self, requests_log):
        import json

        import httpx
        from api.usage_logging import LLM_USAGE_LOGGER
        from tradingagents.llm_clients import concurrency_gate as cg
        from tradingagents.llm_clients.openai_client import (
            UnifiedChatOpenAI,
        )

        def _handle(request):
            requests_log.append(json.loads(request.content.decode()))
            if request.url.path.endswith("/chat/completions"):
                body = json.loads(request.content.decode())
                if body.get("stream"):
                    return httpx.Response(
                        200, text=_sse_body(usage=_USAGE_PAYLOAD),
                        headers={"content-type": "text/event-stream"})
                return httpx.Response(200, json={
                    "id": "r1", "object": "chat.completion",
                    "created": 1, "model": "devin/swe-2",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant",
                                             "content": "ok"}}],
                    "usage": _USAGE_PAYLOAD,
                })
            return httpx.Response(404, text="nope")

        cg._reset_gates_for_tests()
        return UnifiedChatOpenAI(
            model="devin/swe-2", base_url="http://test.invalid/v1",
            api_key="sk-test",
            http_client=httpx.Client(transport=httpx.MockTransport(_handle)),
            http_async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(_handle)),
            callbacks=[LLM_USAGE_LOGGER],
        )

    def _latest_row(self, report_id):
        rows = _rows_for(report_id)
        assert len(rows) == 1
        return rows[0]

    def test_sync_invoke_path(self):
        report_id = uuid.uuid4().hex
        llm = self._devin_llm([])
        llm.invoke("hi", config={"metadata": {"report_id": report_id}})
        r = self._latest_row(report_id)
        # No contention → acquire is instant → no queue wait recorded.
        assert r.queue_seconds is None
        assert r.elapsed_seconds is not None
        assert r.prompt_tokens == 88

    def test_sync_stream_path(self):
        report_id = uuid.uuid4().hex
        llm = self._devin_llm([])
        text = "".join(
            c.content if isinstance(c.content, str) else ""
            for c in llm.stream(
                "hi", config={"metadata": {"report_id": report_id}})
        )
        assert text == "你好"
        r = self._latest_row(report_id)
        assert r.queue_seconds is None
        assert r.elapsed_seconds is not None

    def test_async_ainvoke_path(self):
        report_id = uuid.uuid4().hex
        llm = self._devin_llm([])

        async def _run():
            return await llm.ainvoke(
                "hi", config={"metadata": {"report_id": report_id}})

        asyncio.run(_run())
        r = self._latest_row(report_id)
        assert r.queue_seconds is None
        assert r.elapsed_seconds is not None
        assert r.prompt_tokens == 88

    def test_async_astream_path(self):
        report_id = uuid.uuid4().hex
        llm = self._devin_llm([])

        async def _run():
            text = ""
            async for c in llm.astream(
                "hi", config={"metadata": {"report_id": report_id}}):
                if isinstance(c.content, str):
                    text += c.content
            return text

        assert asyncio.run(_run()) == "你好"
        r = self._latest_row(report_id)
        assert r.queue_seconds is None
        assert r.elapsed_seconds is not None
        assert r.prompt_tokens == 88

    def test_queued_async_call_records_own_wait(self):
        """Occupy all devin/ slots during an ainvoke so the acquire
        genuinely queues; the row must carry the real wait."""
        import tradingagents.llm_clients.concurrency_gate as cg

        report_id = uuid.uuid4().hex
        llm = self._devin_llm([])

        async def _run():
            sem = cg._semaphore_for_prefix("devin/")
            for _ in range(4):
                sem.acquire()
            async def _release():
                await asyncio.sleep(0.3)
                for _ in range(4):
                    sem.release()
            rel = asyncio.create_task(_release())
            try:
                await llm.ainvoke(
                    "hi",
                    config={"metadata": {"report_id": report_id}})
            finally:
                await rel

        asyncio.run(_run())
        cg._reset_gates_for_tests()
        r = self._latest_row(report_id)
        # The call really queued ~0.3s — the wait belongs to THIS row,
        # and elapsed excludes it.
        assert r.queue_seconds is not None
        assert abs(r.queue_seconds - 0.3) < 0.15, r.queue_seconds
        assert r.elapsed_seconds is not None
        assert r.elapsed_seconds < 1.5


class TestReview1925Cleanup:
    """DAV-1362 review DAV-1925 post-verdict cleanup (3 🟡 items).

    ① dead code / ghost-symbol comments removed from concurrency_gate,
    ② `_run_queue_waits` leak path bounded,
    ③ the run_inline sync SQLite write no longer executes on the loop.
    """

    # ── ① dead code + ghost symbol comments ───────────────────────────

    def test_abandoned_scaffolding_removed(self):
        """The two-design leftover (run-stamp contextvar + stamp/snapshot
        helpers) must be gone — it had no callers and its docstring
        described wiring openai_client never had."""
        from tradingagents.llm_clients import concurrency_gate as cg

        for gone in ("_run_stamp_last", "stamp_queue_wait_run",
                     "queue_wait_snapshot"):
            assert not hasattr(cg, gone), f"{gone} still present"
        src = inspect.getsource(cg)
        assert "_queue_wait_by_run" not in src, "ghost symbol in comment"

    def test_live_api_intact(self):
        """The three functions the fix actually uses must remain."""
        from tradingagents.llm_clients import concurrency_gate as cg

        for live in ("begin_queue_wait", "_record_queue_wait",
                     "pop_queue_wait"):
            assert callable(getattr(cg, live)), f"{live} missing"

    # ── ② leak path bounded ───────────────────────────────────────────

    def test_run_records_are_capped(self):
        """Runs that never fire on_llm_end/on_llm_error must not grow the
        module dict without bound: past the cap the oldest is evicted."""
        import tradingagents.llm_clients.concurrency_gate as cg

        ids = [uuid.uuid4() for _ in range(cg._MAX_RUN_QUEUE_WAIT_ENTRIES + 5)]
        for rid in ids:
            cg.begin_queue_wait(rid)
        leftovers = cg._pop_queue_waits_for_tests()
        assert len(leftovers) == cg._MAX_RUN_QUEUE_WAIT_ENTRIES
        # Oldest evicted, newest retained.
        assert str(ids[0]) not in leftovers
        assert str(ids[-1]) in leftovers
        assert cg._evicted_run_queue_waits >= 5

    def test_stale_binding_creates_no_orphan(self):
        """A binding left over from an already-closed run must not
        resurrect that run's record (the orphan-leak path)."""
        import tradingagents.llm_clients.concurrency_gate as cg

        closed = uuid.uuid4()
        cg.begin_queue_wait(closed)
        assert cg.pop_queue_wait(closed) == 0.0  # run closed normally
        # Simulate a later acquire in a context still bound to `closed`.
        cg._current_queue_run.set(str(closed))
        cg._record_queue_wait(1.0)
        assert str(closed) not in cg._pop_queue_waits_for_tests()

    def test_open_run_still_records_its_wait(self):
        """The cap/orphan guard must not break the normal path."""
        import tradingagents.llm_clients.concurrency_gate as cg

        rid = uuid.uuid4()
        cg.begin_queue_wait(rid)
        cg._record_queue_wait(1.0)
        assert cg.pop_queue_wait(rid) == 1.0
        assert cg._pop_queue_waits_for_tests() == {}

    # ── ③ sync SQLite write must not run on the event loop ────────────

    def test_async_dispatch_writes_off_the_loop(self):
        """run_inline=True puts the sync hooks on the caller's context,
        which for async dispatch is the loop thread — so the ledger
        commit must be handed to the writer thread instead of blocking
        the loop. Asserts the commit thread ≠ loop thread AND the row
        still lands."""
        import api.database as db
        import threading

        import tradingagents.llm_clients.concurrency_gate as cg

        loop_idents = []
        write_idents = []
        real_log = db.log_llm_call

        def _spy(**kwargs):
            write_idents.append(threading.get_ident())

        db.log_llm_call = _spy
        try:
            llm = self._devin_llm([])
            report_id = uuid.uuid4().hex

            async def _run():
                loop_idents.append(threading.get_ident())
                await llm.ainvoke(
                    "hi", config={"metadata": {"report_id": report_id}})

            asyncio.run(_run())
            cg._reset_gates_for_tests()
            # Drain while the spy is still installed: the writer thread
            # imports log_llm_call when it runs, so restoring first would
            # let the real writer through and race the assertion.
            from api.usage_logging import flush_usage_writes

            flush_usage_writes()
        finally:
            db.log_llm_call = real_log

        assert len(write_idents) == 1, "ledger row was not submitted"
        assert write_idents[0] != loop_idents[0], (
            "ledger commit ran on the event-loop thread"
        )

    def test_sync_dispatch_writes_inline(self):
        """With no loop running the row is written inline, so a sync
        caller can read its own row back without flushing."""
        from api.database import LLMCallLogDB

        report_id = uuid.uuid4().hex
        handler = LLMUsageLogger()
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            {}, [], run_id=run_id, metadata={"report_id": report_id})
        handler.on_llm_end(_llm_result(usage=_usage_metadata()),
                           run_id=run_id)
        with get_db_ctx() as db:  # no flush: must already be there
            assert db.query(LLMCallLogDB).filter(
                LLMCallLogDB.report_id == report_id).count() == 1

    def test_summary_flushes_pending_rows(self):
        """build_llm_usage_summary must never miss a report's last call
        whose row is still queued on the writer thread."""
        report_id = uuid.uuid4().hex
        handler = LLMUsageLogger()
        run_id = uuid.uuid4()

        async def _call():
            # Inside a running loop ⇒ the row is queued on the writer
            # thread, not written yet.
            handler.on_chat_model_start(
                {}, [], run_id=run_id,
                metadata={"report_id": report_id, "llm_role": "Trader"})
            handler.on_llm_end(_llm_result(usage=_usage_metadata()),
                               run_id=run_id)

        asyncio.run(_call())
        # No explicit flush here: the summary drains the queue itself.
        summary = build_llm_usage_summary(report_id)
        assert summary is not None
        assert summary["call_count"] == 1

    def test_flush_usage_writes_is_public_and_idempotent(self):
        from api.usage_logging import flush_usage_writes

        flush_usage_writes()
        flush_usage_writes()  # empty queue must be a no-op, not an error

    def _devin_llm(self, requests_log):
        from tradingagents.llm_clients import concurrency_gate as cg
        from tradingagents.llm_clients.openai_client import (
            UnifiedChatOpenAI,
        )

        def _handle(request):
            requests_log.append(json.loads(request.content.decode()))
            if request.url.path.endswith("/chat/completions"):
                body = json.loads(request.content.decode())
                if body.get("stream"):
                    return httpx.Response(
                        200, text=_sse_body(usage=_USAGE_PAYLOAD),
                        headers={"content-type": "text/event-stream"})
                return httpx.Response(200, json={
                    "id": "r1", "object": "chat.completion",
                    "created": 1, "model": "devin/swe-2",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant",
                                             "content": "ok"}}],
                    "usage": _USAGE_PAYLOAD,
                })
            return httpx.Response(404, text="nope")

        cg._reset_gates_for_tests()
        return UnifiedChatOpenAI(
            model="devin/swe-2", base_url="http://test.invalid/v1",
            api_key="sk-test",
            http_client=httpx.Client(transport=httpx.MockTransport(_handle)),
            http_async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(_handle)),
        )
