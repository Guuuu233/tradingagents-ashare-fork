"""DAV-1314: unified LLM usage logging.

A single LangChain callback handler attached to every ``UnifiedChatOpenAI``
instance. On each completed model call it writes one row to ``llm_call_logs``
via ``api.database.log_llm_call`` — covering all graph roles, repair passes,
intent parsing, and structured extraction without per-role instrumentation.

Run context (report_id / horizon / role) comes from LangChain run metadata,
which flows down from the graph invocation config (``metadata`` merges into
child runs; LangGraph also injects ``langgraph_node`` with the node name), or
from contextvars as fallback: ``api.database.current_report_id`` for
report_id, ``current_llm_role`` for the role, ``current_llm_horizon`` for
horizon. The contextvar channel exists because child-run metadata does not
reach LLM calls made inside graph nodes (nodes do not forward RunnableConfig
and sync nodes run in a thread pool).

Usage is read from ``usage_metadata`` (input_tokens / output_tokens /
total_tokens + input_token_details.cache_read + output_token_details.reasoning)
which works for both streaming (requires ``stream_usage=True`` on the client)
and non-streaming calls; ``llm_output["token_usage"]`` is the fallback for
non-streaming responses. Missing usage → NULL columns, never an error.
"""

from __future__ import annotations

import logging
import threading
import time
from contextvars import ContextVar
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# Role label for LLM calls that run OUTSIDE the LangGraph graph (intent
# parsing, structured extraction, config probes, warmup). Graph nodes are
# named automatically via langgraph_node metadata; direct calls set this
# contextvar so no call-site signature has to change (mock-friendly).
# metadata["llm_role"] is a reserved hook with the same purpose — it always
# wins over this contextvar, letting future callers tag a role through
# RunnableConfig without touching the context.
current_llm_role: ContextVar[Optional[str]] = ContextVar(
    "current_llm_role", default=None
)

# Horizon label for the same reason: graph-level RunnableConfig metadata does
# not reach LLM calls inside nodes. Each horizon pipeline (``_process_horizon``
# in api.main, ``TradingAgentsGraph.propagate``) sets this before running the
# graph; dual-horizon runs execute in separate asyncio tasks so the two
# context copies never bleed into each other. metadata["horizon"] still wins
# when present; missing everywhere → NULL horizon column.
current_llm_horizon: ContextVar[Optional[str]] = ContextVar(
    "current_llm_horizon", default=None
)


def _usage_to_int(value: Any) -> Optional[int]:
    """Best-effort int coercion; None stays None (missing usage → NULL)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_llm_result(response: LLMResult) -> Dict[str, Any]:
    """Pull usage / finish_reason / model / response chars out of an LLMResult.

    Returns keys: input_tokens, cached_prompt_tokens, output_tokens,
    reasoning_tokens, total_tokens, finish_reason, model_name, response_chars.
    All values may be None when the provider returns no usage.
    """
    usage: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None
    model_name: Optional[str] = None
    response_chars = 0
    saw_text = False

    for gens in (response.generations or []):
        for gen in gens:
            msg = getattr(gen, "message", None)
            um = getattr(msg, "usage_metadata", None)
            if um:
                usage = um
            gen_info = getattr(gen, "generation_info", None) or {}
            if not finish_reason:
                finish_reason = gen_info.get("finish_reason")
            if not model_name:
                model_name = gen_info.get("model_name")
            # response_chars only counts plain-str content; list-form content
            # (tool-call / thinking blocks on Anthropic/Gemini) is skipped and
            # the column stays NULL — the column means "final text length".
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content:
                saw_text = True
                response_chars += len(content)

    llm_output = getattr(response, "llm_output", None) or {}
    if not model_name:
        model_name = llm_output.get("model_name")

    input_tokens = output_tokens = total_tokens = None
    cached_prompt_tokens = reasoning_tokens = None
    if usage:
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        in_details = usage.get("input_token_details") or {}
        out_details = usage.get("output_token_details") or {}
        if isinstance(in_details, dict):
            cached_prompt_tokens = in_details.get("cache_read")
        if isinstance(out_details, dict):
            reasoning_tokens = out_details.get("reasoning")
    else:
        # Non-streaming fallback: some providers only populate llm_output.
        tu = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if isinstance(tu, dict):
            input_tokens = tu.get("prompt_tokens") or tu.get("input_tokens")
            output_tokens = tu.get("completion_tokens") or tu.get("output_tokens")
            total_tokens = tu.get("total_tokens")
            in_details = tu.get("prompt_tokens_details") or {}
            out_details = tu.get("completion_tokens_details") or {}
            if isinstance(in_details, dict):
                cached_prompt_tokens = in_details.get("cached_tokens")
            if isinstance(out_details, dict):
                reasoning_tokens = out_details.get("reasoning_tokens")

    return {
        "input_tokens": _usage_to_int(input_tokens),
        "cached_prompt_tokens": _usage_to_int(cached_prompt_tokens),
        "output_tokens": _usage_to_int(output_tokens),
        "reasoning_tokens": _usage_to_int(reasoning_tokens),
        "total_tokens": _usage_to_int(total_tokens),
        "finish_reason": finish_reason,
        "model_name": model_name,
        "response_chars": response_chars if saw_text else None,
    }


class LLMUsageLogger(BaseCallbackHandler):
    """Log every completed LLM call to ``llm_call_logs``.

    Attached once per ``UnifiedChatOpenAI`` instance (constructor-level
    callback ⇒ inherited by all invoke/stream/astream runs on that model).
    Fire-and-forget: ``log_llm_call`` swallows its own errors, and this
    handler never raises.
    """

    # Never let bookkeeping break an analysis run.
    raise_error = False

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        # run_id (str) -> {"t0": monotonic, "metadata": dict, "retried": bool}
        self._runs: Dict[str, Dict[str, Any]] = {}

    # ── lifecycle bookkeeping ──────────────────────────────────────────

    def _register_run(
        self, run_id: Optional[UUID], metadata: Optional[Dict[str, Any]]
    ) -> None:
        if run_id is None:
            return
        with self._lock:
            self._runs[str(run_id)] = {
                "t0": time.monotonic(),
                "metadata": metadata or {},
                "retried": False,
            }

    def _mark_retried(self, run_id: Optional[UUID]) -> None:
        if run_id is None:
            return
        with self._lock:
            run = self._runs.get(str(run_id))
            if run is not None:
                run["retried"] = True

    def _pop_run(self, run_id: Optional[UUID]) -> Optional[Dict[str, Any]]:
        if run_id is None:
            return None
        with self._lock:
            return self._runs.pop(str(run_id), None)

    # ── sync hooks ─────────────────────────────────────────────────────

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self._register_run(run_id, metadata)

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[Any],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self._register_run(run_id, metadata)

    def on_retry(
        self,
        retry_state: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        self._mark_retried(run_id)

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        self._log_end(response, run_id)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        # A failed attempt produced no usage; drop the bookkeeping entry so
        # the dict cannot grow unboundedly. Any subsequent retry registers
        # a fresh run_id and on_retry marks the parent lineage.
        self._pop_run(run_id)

    # ── async mirrors (same logic; async managers call these) ──────────

    async def aon_llm_start(self, *args: Any, **kwargs: Any) -> None:
        self.on_llm_start(*args, **kwargs)

    async def aon_chat_model_start(self, *args: Any, **kwargs: Any) -> None:
        self.on_chat_model_start(*args, **kwargs)

    async def aon_retry(self, *args: Any, **kwargs: Any) -> None:
        self.on_retry(*args, **kwargs)

    async def aon_llm_end(self, *args: Any, **kwargs: Any) -> None:
        self.on_llm_end(*args, **kwargs)

    async def aon_llm_error(self, *args: Any, **kwargs: Any) -> None:
        self.on_llm_error(*args, **kwargs)

    # ── record writer ──────────────────────────────────────────────────

    def _log_end(self, response: LLMResult, run_id: Optional[UUID]) -> None:
        try:
            run = self._pop_run(run_id) or {}
            metadata = run.get("metadata") or {}
            agent_name = (
                metadata.get("llm_role")
                or current_llm_role.get()
                or metadata.get("langgraph_node")
                or "unknown"
            )
            report_id = metadata.get("report_id")
            if report_id is None:
                from api.database import current_report_id

                report_id = current_report_id.get()
            extracted = _extract_llm_result(response)
            elapsed = None
            if run.get("t0") is not None:
                elapsed = round(time.monotonic() - run["t0"], 2)

            from api.database import log_llm_call

            log_llm_call(
                agent_name=agent_name,
                model_name=extracted["model_name"],
                finish_reason=extracted["finish_reason"],
                prompt_tokens=extracted["input_tokens"],
                completion_tokens=extracted["output_tokens"],
                total_tokens=extracted["total_tokens"],
                cached_prompt_tokens=extracted["cached_prompt_tokens"],
                reasoning_tokens=extracted["reasoning_tokens"],
                elapsed_seconds=elapsed,
                response_chars=extracted["response_chars"],
                # degraded 判定依赖调用方对流式全文的业务检查（本项不可用），
                # 回调层拿不到该语义，统一记 False；列保留兼容旧数据。
                degraded=False,
                report_id=report_id,
                horizon=metadata.get("horizon") or current_llm_horizon.get(),
                retried=bool(run.get("retried")),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("LLMUsageLogger.on_llm_end failed (non-fatal): %s", exc)


def build_llm_usage_summary(report_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Aggregate llm_call_logs for a report into a per-role usage summary.

    Written into result_data["usage_summary"] at report finalization so each
    report carries its own cost vector. Returns None when nothing was logged;
    swallows errors (summary must never break report persistence).
    """
    if not report_id:
        return None
    try:
        from api.database import LLMCallLogDB, get_db_ctx

        with get_db_ctx() as db:
            rows = (
                db.query(LLMCallLogDB)
                .filter(LLMCallLogDB.report_id == report_id)
                .all()
            )
        if not rows:
            return None

        def _sum(field: str) -> Optional[int]:
            vals = [getattr(r, field) for r in rows]
            vals = [v for v in vals if v is not None]
            return sum(vals) if vals else None

        by_role: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            entry = by_role.setdefault(
                r.agent_name,
                {
                    "calls": 0,
                    "horizons": set(),
                    "prompt_tokens": 0,
                    "cached_prompt_tokens": 0,
                    "completion_tokens": 0,
                    "reasoning_tokens": 0,
                    "total_tokens": 0,
                    "elapsed_seconds": 0.0,
                    "retried_calls": 0,
                },
            )
            entry["calls"] += 1
            if r.horizon:
                entry["horizons"].add(r.horizon)
            for src, dst in (
                ("prompt_tokens", "prompt_tokens"),
                ("cached_prompt_tokens", "cached_prompt_tokens"),
                ("completion_tokens", "completion_tokens"),
                ("reasoning_tokens", "reasoning_tokens"),
                ("total_tokens", "total_tokens"),
            ):
                v = getattr(r, src)
                if v is not None:
                    entry[dst] += v
            if r.elapsed_seconds is not None:
                entry["elapsed_seconds"] += r.elapsed_seconds
            if r.retried:
                entry["retried_calls"] += 1

        roles = []
        for name in sorted(by_role):
            e = by_role[name]
            roles.append(
                {
                    "agent_name": name,
                    "calls": e["calls"],
                    "horizons": sorted(e["horizons"]),
                    "prompt_tokens": e["prompt_tokens"],
                    "cached_prompt_tokens": e["cached_prompt_tokens"],
                    "completion_tokens": e["completion_tokens"],
                    "reasoning_tokens": e["reasoning_tokens"],
                    "total_tokens": e["total_tokens"],
                    "elapsed_seconds": round(e["elapsed_seconds"], 2),
                    "retried_calls": e["retried_calls"],
                }
            )

        return {
            "report_id": report_id,
            "call_count": len(rows),
            "prompt_tokens": _sum("prompt_tokens"),
            "cached_prompt_tokens": _sum("cached_prompt_tokens"),
            "completion_tokens": _sum("completion_tokens"),
            "reasoning_tokens": _sum("reasoning_tokens"),
            "total_tokens": _sum("total_tokens"),
            "elapsed_seconds": _sum("elapsed_seconds"),
            "retried_calls": sum(1 for r in rows if r.retried),
            "by_role": roles,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("build_llm_usage_summary failed (non-fatal): %s", exc)
        return None


# Shared instance: handlers are stateless apart from the per-run bookkeeping
# dict (keyed by run_id), so one object is safe to attach to every client.
LLM_USAGE_LOGGER = LLMUsageLogger()
