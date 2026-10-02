"""DAV-1430 (D-066 B3): run traceability tests.

Covers the three new llm_call_logs columns (requested_model / served_model /
system_fingerprint — served_model must NEVER fall back to the nominal model),
the input_snapshot canonical-hash field, run_identity (no credentials), and
the per-report served_models rollup.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy

import httpx
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, LLMResult

from api.database import LLMCallLogDB, Base, engine, get_db_ctx, log_llm_call
from api.usage_logging import (
    LLMUsageLogger,
    _extract_llm_result,
    build_served_models_summary,
)


def _ensure_tables():
    Base.metadata.create_all(bind=engine)


def _rows_for(report_id):
    with get_db_ctx() as db:
        return (
            db.query(LLMCallLogDB)
            .filter(LLMCallLogDB.report_id == report_id)
            .all()
        )


def _llm_result(*, content="你好", finish="stop", model=None, llm_output=None):
    gen_info = {"finish_reason": finish}
    if model is not None:
        gen_info["model_name"] = model
    gen = ChatGeneration(message=AIMessage(content=content), generation_info=gen_info)
    return LLMResult(generations=[[gen]], llm_output=llm_output or {})


# ── HTTP fixtures (same pattern as test_llm_usage_logging) ───────────────────


def _sse_body(*, model: str | None, fingerprint: str | None, usage=None):
    def _chunk(delta, finish):
        c = {
            "id": "c1", "object": "chat.completion.chunk", "created": 1,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if model is not None:
            c["model"] = model
        if fingerprint is not None:
            c["system_fingerprint"] = fingerprint
        return c

    chunks = [
        _chunk({"role": "assistant", "content": "你"}, None),
        _chunk({"content": "好"}, "stop"),
    ]
    if usage is not None:
        chunks.append({**_chunk({}, None), "choices": [], "usage": usage})
    lines = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return lines + "data: [DONE]\n\n"


def _make_llm(requests_log, *, resp_model="served-9x", resp_fp="fp_abc",
              stream_chunks_model="served-9x", stream_fp="fp_stream"):
    """UnifiedChatOpenAI backed by a mock transport.

    Non-streaming responses include/exclude `model` per ``resp_model``
    (None → provider did not report a model). Streaming chunks carry
    ``stream_chunks_model`` / ``stream_fp`` on every chunk.
    """
    from tradingagents.llm_clients.openai_client import UnifiedChatOpenAI

    def _handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        requests_log.append(body)
        if body.get("stream"):
            return httpx.Response(
                200,
                text=_sse_body(
                    model=stream_chunks_model,
                    fingerprint=stream_fp,
                    usage={
                        "prompt_tokens": 8, "completion_tokens": 2,
                        "total_tokens": 10,
                    },
                ),
                headers={"content-type": "text/event-stream"},
            )
        resp = {
            "id": "r1", "object": "chat.completion", "created": 1,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "你好"}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2,
                      "total_tokens": 10},
        }
        if resp_model is not None:
            resp["model"] = resp_model
        if resp_fp is not None:
            resp["system_fingerprint"] = resp_fp
        return httpx.Response(200, json=resp)

    transport = httpx.MockTransport(_handle)
    return UnifiedChatOpenAI(
        model="nominal-x",
        base_url="http://test.invalid/v1",
        api_key="sk-test",
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


class TestCreateChatResultPatch:
    """The non-streaming served-model capture, proven directly on
    ChatOpenAI._create_chat_result (no network)."""

    _RESP = {
        "id": "r1", "object": "chat.completion", "created": 1,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                  "total_tokens": 2},
    }

    def _llm(self):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model="nominal-x", api_key="sk-test")

    def test_model_present_is_captured(self):
        resp = {**self._RESP, "model": "served-9x",
                "system_fingerprint": "fp_1"}
        res = self._llm()._create_chat_result(resp)
        assert res.llm_output["served_model"] == "served-9x"
        # merged legacy key unchanged
        assert res.llm_output["model_name"] == "served-9x"

    def test_model_absent_leaves_no_served_key(self):
        res = self._llm()._create_chat_result(dict(self._RESP))
        assert "served_model" not in res.llm_output
        # merged legacy key fell back to nominal — proof the fallback exists
        assert res.llm_output["model_name"] == "nominal-x"

    def test_patch_is_idempotent(self):
        from langchain_openai.chat_models.base import ChatOpenAI
        from api.usage_logging import _install_openai_served_model_capture

        _install_openai_served_model_capture()
        _install_openai_served_model_capture()
        assert getattr(ChatOpenAI._create_chat_result,
                       "_served_model_capture_installed", False)


class TestServedModelNonStreaming:
    def test_response_with_model_records_served_and_fingerprint(self):
        _ensure_tables()
        llm = _make_llm([], resp_model="served-9x", resp_fp="fp_abc")
        report_id = uuid.uuid4().hex
        llm.invoke("hi", config={"metadata": {"report_id": report_id,
                                              "llm_role": "结构化提取"}})
        r = _rows_for(report_id)[0]
        # response body carried model=served-9x (differs from nominal)
        assert r.served_model == "served-9x"
        assert r.requested_model == "nominal-x"
        assert r.system_fingerprint == "fp_abc"
        # legacy column keeps its old semantics
        assert r.model_name == "served-9x"

    def test_response_without_model_leaves_served_null(self):
        _ensure_tables()
        llm = _make_llm([], resp_model=None, resp_fp=None)
        report_id = uuid.uuid4().hex
        llm.invoke("hi", config={"metadata": {"report_id": report_id}})
        r = _rows_for(report_id)[0]
        # no `model` in the response body → must NOT fall back to nominal
        assert r.served_model is None
        assert r.system_fingerprint is None
        assert r.requested_model == "nominal-x"
        # legacy column keeps its pre-change semantics (langchain merges
        # nominal when nothing was reported); with this mock it stays NULL —
        # we only assert it is not the fabricated served value.
        assert r.model_name in (None, "nominal-x")

    def test_response_echoing_nominal_still_counts_as_served(self):
        _ensure_tables()
        llm = _make_llm([], resp_model="nominal-x")
        report_id = uuid.uuid4().hex
        llm.invoke("hi", config={"metadata": {"report_id": report_id}})
        r = _rows_for(report_id)[0]
        # the body did carry `model` (equal to nominal) — legitimately served
        assert r.served_model == "nominal-x"
        # and requested/served can now be told apart in the ledger
        assert r.requested_model == "nominal-x"


class TestServedModelStreaming:
    """Proves the streaming path CAN read the response-body `model`."""

    def test_stream_chunks_with_model_record_served(self):
        _ensure_tables()
        llm = _make_llm([], stream_chunks_model="served-9x",
                        stream_fp="fp_stream")
        report_id = uuid.uuid4().hex

        async def _run():
            async for _ in llm.astream(
                "hi", config={"metadata": {"report_id": report_id}}
            ):
                pass

        asyncio.run(_run())
        r = _rows_for(report_id)[0]
        assert r.served_model == "served-9x"
        assert r.system_fingerprint == "fp_stream"
        assert r.requested_model == "nominal-x"

    def test_stream_chunks_without_model_leave_null(self):
        _ensure_tables()
        llm = _make_llm([], stream_chunks_model=None, stream_fp=None)
        report_id = uuid.uuid4().hex

        async def _run():
            async for _ in llm.astream(
                "hi", config={"metadata": {"report_id": report_id}}
            ):
                pass

        asyncio.run(_run())
        r = _rows_for(report_id)[0]
        assert r.served_model is None
        assert r.system_fingerprint is None
        assert r.requested_model == "nominal-x"


class TestExtractServedModel:
    """Unit-level extraction rules: only provably-raw sources count."""

    def test_llm_output_served_model_key(self):
        out = _extract_llm_result(
            _llm_result(llm_output={"served_model": "served-9x",
                                    "model_name": "nominal-x"})
        )
        assert out["served_model"] == "served-9x"

    def test_llm_output_model_name_alone_is_not_served(self):
        # non-streaming llm_output["model_name"] is the fallback-merged value
        # — it must never be mistaken for the served model.
        out = _extract_llm_result(
            _llm_result(llm_output={"model_name": "nominal-x"})
        )
        assert out["served_model"] is None

    def test_anthropic_style_raw_model_key(self):
        out = _extract_llm_result(
            _llm_result(llm_output={"model": "claude-served"})
        )
        assert out["served_model"] == "claude-served"

    def test_streaming_generation_info_model_name(self):
        msg = AIMessageChunk(content="hi")
        gen = ChatGenerationChunk(
            message=msg,
            generation_info={"finish_reason": "stop",
                             "model_name": "served-stream",
                             "system_fingerprint": "fp_s"},
        )
        out = _extract_llm_result(LLMResult(generations=[[gen]], llm_output={}))
        assert out["served_model"] == "served-stream"
        assert out["system_fingerprint"] == "fp_s"

    def test_system_fingerprint_from_llm_output(self):
        out = _extract_llm_result(
            _llm_result(llm_output={"system_fingerprint": "fp_x"})
        )
        assert out["system_fingerprint"] == "fp_x"

    def test_empty_fingerprint_is_null(self):
        out = _extract_llm_result(
            _llm_result(llm_output={"system_fingerprint": ""})
        )
        assert out["system_fingerprint"] is None


class TestRequestedModelAndLogCall:
    def test_requested_model_from_invocation_params(self):
        _ensure_tables()
        handler = LLMUsageLogger()
        report_id = uuid.uuid4().hex
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            {}, [], run_id=run_id,
            metadata={"report_id": report_id},
            invocation_params={"model": "nominal-x", "temperature": 0.1},
        )
        handler.on_llm_end(_llm_result(), run_id=run_id)
        r = _rows_for(report_id)[0]
        assert r.requested_model == "nominal-x"

    def test_requested_model_absent_without_invocation_params(self):
        _ensure_tables()
        handler = LLMUsageLogger()
        report_id = uuid.uuid4().hex
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            {}, [], run_id=run_id, metadata={"report_id": report_id}
        )
        handler.on_llm_end(_llm_result(), run_id=run_id)
        assert _rows_for(report_id)[0].requested_model is None

    def test_log_llm_call_writes_new_columns(self):
        _ensure_tables()
        report_id = uuid.uuid4().hex
        log_llm_call(
            agent_name="x", model_name="m", report_id=report_id,
            requested_model="nominal-x", served_model="served-9x",
            system_fingerprint="fp_1",
        )
        r = _rows_for(report_id)[0]
        assert (r.requested_model, r.served_model, r.system_fingerprint) == (
            "nominal-x", "served-9x", "fp_1",
        )


class TestServedModelsSummary:
    def test_dedupe_per_role_and_missing_count(self):
        _ensure_tables()
        report_id = uuid.uuid4().hex
        log_llm_call(agent_name="Trader", report_id=report_id,
                     served_model="served-9x")
        log_llm_call(agent_name="Trader", report_id=report_id,
                     served_model="served-9x")
        log_llm_call(agent_name="Trader", report_id=report_id,
                     served_model="served-8x")
        log_llm_call(agent_name="Bear Researcher", report_id=report_id,
                     served_model="claude-served")
        log_llm_call(agent_name="意图解析", report_id=report_id,
                     served_model=None)
        out = build_served_models_summary(report_id)
        assert out["by_role"] == {
            "Bear Researcher": ["claude-served"],
            "Trader": ["served-8x", "served-9x"],
        }
        assert out["served_model_missing"] == 1
        assert out["call_count"] == 5

    def test_none_when_no_rows(self):
        assert build_served_models_summary(uuid.uuid4().hex) is None
        assert build_served_models_summary(None) is None


class TestInputSnapshot:
    """Canonical-hash determinism for the collected input pool."""

    def _fn(self):
        from api.main import _compute_input_snapshot

        return _compute_input_snapshot

    _POOL = {
        "market_data_context": {"daily": {"as_of": "2026-08-17"},
                                "基金流": {"净流入": 1.5}},
        "stock_data": "date,close\n2026-08-17,10.5",
        "social_data_context": {"posts": ["a", "b"]},
        "trade_date": "2026-08-17",
        "symbol": "600519",
    }

    def test_deterministic_same_input_same_hash(self):
        fn = self._fn()
        a = fn(deepcopy(self._POOL))
        b = fn(deepcopy(self._POOL))
        assert a == b
        assert a["algo"] == "sha256"
        assert len(a["hash"]) == 64
        assert a["keys"] == sorted(self._POOL.keys())
        assert a["bytes"] > 0

    def test_one_value_change_changes_hash(self):
        fn = self._fn()
        a = fn(deepcopy(self._POOL))
        modified = deepcopy(self._POOL)
        modified["market_data_context"]["基金流"]["净流入"] = 1.6
        b = fn(modified)
        assert a["hash"] != b["hash"]

    def test_key_order_does_not_change_hash(self):
        fn = self._fn()
        a = fn(deepcopy(self._POOL))
        reordered = {k: self._POOL[k] for k in reversed(list(self._POOL))}
        assert fn(reordered)["hash"] == a["hash"]

    def test_dual_horizon_shares_one_pool_one_hash(self):
        # 双档共用同一 collected_pool：两档写出的快照必须相同。
        fn = self._fn()
        shared = deepcopy(self._POOL)
        short_slice = {"horizon": "short", "input_snapshot": fn(shared)}
        medium_slice = {"horizon": "medium", "input_snapshot": fn(shared)}
        assert short_slice["input_snapshot"]["hash"] \
            == medium_slice["input_snapshot"]["hash"]
        assert short_slice["input_snapshot"] == medium_slice["input_snapshot"]

    def test_non_serializable_uses_default_str_and_lists_key(self):
        fn = self._fn()
        pool = deepcopy(self._POOL)

        class _Odd:
            def __str__(self):
                return "odd-value"

        pool["opaque"] = _Odd()
        snap = fn(pool)
        assert snap is not None
        assert "opaque" in snap["keys"]
        assert snap["default_str_keys"] == ["opaque"]
        # deterministic across calls
        assert fn(pool)["hash"] == snap["hash"]

    def test_no_pool_returns_none(self):
        assert self._fn()(None) is None


class TestRunIdentity:
    def _fn(self):
        from api.main import _build_run_identity

        return _build_run_identity

    def test_contains_sha_hashes_and_role_models(self):
        class _G:
            role_resolved_configs = {
                "trader": {
                    "model_name": "nominal-x",
                    "temperature": 0.1,
                    "max_tokens": 512,
                    "provider_type": "openai",
                    "base_url": "http://secret.internal/v1",
                    "api_key": "sk-should-not-leak",
                }
            }

        snapshot = {
            "enabled": True,
            "roles": {"trader": {"resolved_hash": "abc123",
                                 "resolved_text": "text"}},
        }
        identity = self._fn()(_G(), snapshot)
        assert isinstance(identity["commit_sha"], str)
        assert identity["prompt_resolved_hashes"] == {"trader": "abc123"}
        assert identity["role_models"] == {
            "trader": {"model_name": "nominal-x", "temperature": 0.1,
                       "max_tokens": 512}
        }

    def test_no_credentials_or_endpoints_leak(self):
        class _G:
            role_resolved_configs = {
                "bear_researcher": {
                    "model_name": "m",
                    "temperature": None,
                    "max_tokens": None,
                    "base_url": "http://secret.internal/v1",
                    "api_key": "sk-secret",
                    "provider_id": "p1",
                    "profile_id": "pf1",
                }
            }

        identity = self._fn()(_G(), {"roles": {}})
        blob = json.dumps(identity, ensure_ascii=False)
        assert "sk-secret" not in blob
        assert "secret.internal" not in blob
        assert "api_key" not in blob
        assert "base_url" not in blob
        assert "provider_id" not in blob
        assert "profile_id" not in blob


class TestAttachTraceabilityFields:
    def test_attach_writes_all_three_fields(self):
        _ensure_tables()
        from api.main import _attach_traceability_fields

        class _G:
            role_resolved_configs = {}

        report_id = uuid.uuid4().hex
        log_llm_call(agent_name="Trader", report_id=report_id,
                     served_model="served-9x")
        result = {}
        _attach_traceability_fields(
            result,
            pool={"a": 1}, graph=_G(),
            prompt_snapshot={"roles": {}}, report_id=report_id,
        )
        assert result["input_snapshot"]["algo"] == "sha256"
        assert "run_identity" in result
        assert result["served_models"]["by_role"] == {"Trader": ["served-9x"]}
        assert result["served_models"]["served_model_missing"] == 0
