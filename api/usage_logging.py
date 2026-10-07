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


def _install_streaming_usage_last_wins() -> None:
    """DAV-1426: merge stream-chunk usage as last-wins, not per-chunk sum.

    langchain-core's ``add_ai_message_chunks`` merges per-chunk
    ``usage_metadata`` with ``add_usage`` (recursive add). That is correct
    for providers that emit usage once at the end of the stream, but some
    OpenAI-compatible providers (observed: deepseek-v4.1-flash,
    space-bunny-free) attach the *cumulative* usage to every chunk — summing
    then inflates tokens quadratically (real case: 2.11e8 prompt tokens on
    a ~25K-token prompt, llm_call_logs row 5fab9ea5…).

    The ledger only ever wants the provider's final accounting, so this
    wrapper replaces the usage merge inside ``add_ai_message_chunks`` with
    per-field last-wins for the scalar counters and per-field max for
    ``input_token_details`` / ``output_token_details``. Chunk order is
    stream order ⇒ the last observed value is the most complete for
    cumulative senders and equal-or-superset for delta/final senders.
    ``add_usage`` itself is left untouched (langchain_core.messages.ai
    re-exports it and other merge paths may rely on add semantics).

    Idempotent and defensive: any failure leaves the stock merger.
    """
    try:
        from langchain_core.messages import ai as _lc_ai

        original = _lc_ai.add_ai_message_chunks
        if getattr(original, "_usage_last_wins_installed", False):
            return

        def _merge_usage_last_wins(left: Any, right: Any) -> Any:
            """Last-wins merge of two UsageMetadata dicts.

            input/output/total_tokens: right wins when present (later chunk
            = newer cumulative reading). Detail dicts: per-key max, since
            cumulative counters never decrease within a stream and a key
            reported by only some chunks must not be lost or inflated.
            """
            if left is None:
                return right
            if right is None:
                return left
            merged = dict(left)
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                rv = right.get(key)
                if rv is not None:
                    merged[key] = rv
            for details_key in ("input_token_details", "output_token_details"):
                ld = left.get(details_key) or {}
                rd = right.get(details_key) or {}
                if not isinstance(ld, dict):
                    ld = {}
                if not isinstance(rd, dict):
                    rd = {}
                details = dict(ld)
                for k, rv in rd.items():
                    if rv is None:
                        continue
                    lv = details.get(k)
                    try:
                        details[k] = rv if lv is None else max(lv, rv)
                    except TypeError:
                        details[k] = rv
                if details:
                    merged[details_key] = details
            return merged

        def _add_ai_message_chunks_last_wins(left, *others):
            if getattr(left, "usage_metadata", None) or any(
                getattr(o, "usage_metadata", None) is not None for o in others
            ):
                try:
                    merged = getattr(left, "usage_metadata", None)
                    rest = []
                    for other in others:
                        um = getattr(other, "usage_metadata", None)
                        merged = _merge_usage_last_wins(merged, um)
                        # copy(): chunks yielded to callers must not be
                        # mutated under them; the merge result carries the
                        # final usage, per-chunk objects keep their own.
                        rest.append(
                            other.model_copy(update={"usage_metadata": None})
                            if um is not None
                            else other
                        )
                    left = left.model_copy(update={"usage_metadata": merged})
                    others = tuple(rest)
                except Exception:
                    # Fall through un-patched rather than break streaming.
                    return original(left, *others)
            return original(left, *others)

        _add_ai_message_chunks_last_wins._usage_last_wins_installed = True
        _lc_ai.add_ai_message_chunks = _add_ai_message_chunks_last_wins
    except Exception:  # pragma: no cover - defensive
        logger.debug("streaming usage last-wins patch unavailable", exc_info=True)


_install_streaming_usage_last_wins()


# DAV-1426: prompt_tokens above this bound cannot be a real reading — the
# streaming double-count bug showed per-call usage orders of magnitude past
# any legitimate context (~130K tokens max prompt). Rows crossing it get
# usage_unreliable=1; their token fields stay stored as captured so
# investigators keep the evidence, and aggregations must exclude flagged rows.
USAGE_UNRELIABLE_THRESHOLD = 2_000_000


def _install_openai_served_model_capture() -> None:
    """DAV-1430 (D-066 B3): preserve the raw response-body ``model`` field.

    langchain_openai merges ``response_dict.get("model", self.model_name)``
    into ``llm_output["model_name"]``, so downstream code cannot tell whether
    the provider actually reported a served model or the value is the nominal
    fallback. This wrapper re-reads the raw response inside
    ``_create_chat_result`` and, only when the response body truly carried a
    ``model`` key, stashes it under ``llm_output["served_model"]`` — a
    distinct key that can never be confused with the fallback merge. Streaming
    needs no patch: ``_convert_chunk_to_generation_chunk`` already copies the
    raw chunk ``model`` into ``generation_info["model_name"]``.

    Idempotent and defensive: any failure leaves the class untouched.
    """
    try:
        from langchain_openai.chat_models.base import ChatOpenAI

        original = ChatOpenAI._create_chat_result
        if getattr(original, "_served_model_capture_installed", False):
            return

        def _create_chat_result_with_served_model(self, response, generation_info=None):
            result = original(self, response, generation_info)
            try:
                raw = (
                    response
                    if isinstance(response, dict)
                    else response.model_dump()
                )
                served = raw.get("model") if isinstance(raw, dict) else None
                if served and isinstance(result.llm_output, dict):
                    result.llm_output["served_model"] = served
            except Exception:
                pass
            return result

        _create_chat_result_with_served_model._served_model_capture_installed = True
        ChatOpenAI._create_chat_result = _create_chat_result_with_served_model
    except Exception:  # pragma: no cover - defensive
        logger.debug("served_model capture patch unavailable", exc_info=True)


_install_openai_served_model_capture()

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

    # DAV-1430 (D-066 B3): served_model / system_fingerprint.
    # served_model may ONLY come from sources provably carrying the raw
    # response-body `model` field — never from the fallback-merged
    # llm_output["model_name"] (non-streaming) or self.model_name:
    #   1. llm_output["served_model"]    — set by the _create_chat_result
    #      wrapper above only when the response body had `model` (non-streaming
    #      OpenAI-compatible path).
    #   2. generation_info["model_name"] — langchain_openai streaming copies the
    #      raw chunk `model` here (only on chunks that carried it).
    #   3. llm_output["model"]           — langchain_anthropic keeps the raw
    #      response field under this key.
    served_model: Optional[str] = None
    if isinstance(llm_output.get("served_model"), str) and llm_output["served_model"]:
        served_model = llm_output["served_model"]
    elif isinstance(llm_output.get("model"), str) and llm_output["model"]:
        served_model = llm_output["model"]
    if not served_model:
        for gens in (response.generations or []):
            for gen in gens:
                gen_info = getattr(gen, "generation_info", None) or {}
                if gen_info.get("model_name"):
                    served_model = gen_info["model_name"]
                    break
            if served_model:
                break

    system_fingerprint: Optional[str] = None
    fp = llm_output.get("system_fingerprint")
    if isinstance(fp, str) and fp:
        system_fingerprint = fp
    if not system_fingerprint:
        for gens in (response.generations or []):
            for gen in gens:
                gen_info = getattr(gen, "generation_info", None) or {}
                if gen_info.get("system_fingerprint"):
                    system_fingerprint = gen_info["system_fingerprint"]
                    break
            if system_fingerprint:
                break

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

    # llm_output["token_usage"] carries the provider's single authoritative
    # usage body (non-streaming _create_chat_result; some proxies also inject
    # it on the aggregate). When it disagrees with the chunk-merged value
    # upward, trust it — the merged value can only *under*state (missing
    # chunks) or *over*state (a residual per-chunk-sum path), and the
    # provider's final body is the better reading in both directions only
    # when it is not itself a sum. Practically: take it when it differs,
    # since for cumulative senders it equals the last chunk's value and for
    # final-only senders it equals the only value.
    tu = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if isinstance(tu, dict) and tu:
        ti = tu.get("prompt_tokens") or tu.get("input_tokens")
        to = tu.get("completion_tokens") or tu.get("output_tokens")
        tt = tu.get("total_tokens")
        in_details = tu.get("prompt_tokens_details") or {}
        out_details = tu.get("completion_tokens_details") or {}
        t_cached = (
            in_details.get("cached_tokens") if isinstance(in_details, dict) else None
        )
        t_reason = (
            out_details.get("reasoning_tokens")
            if isinstance(out_details, dict)
            else None
        )
        if usage is None:
            input_tokens, output_tokens, total_tokens = ti, to, tt
            cached_prompt_tokens = cached_prompt_tokens or t_cached
            reasoning_tokens = reasoning_tokens or t_reason
        else:
            # DAV-1426 backstop: when both sources exist but differ, prefer
            # the one with the smaller input_tokens — an inflated merged
            # value is the bug signature; a smaller llm_output reading that
            # is still nonzero is the provider's own accounting. Equal or
            # unavailable → keep the merged value.
            try:
                if ti is not None and input_tokens is not None and int(ti) < int(input_tokens):
                    input_tokens = ti
            except (TypeError, ValueError):
                pass
            try:
                if to is not None and output_tokens is not None and int(to) < int(output_tokens):
                    output_tokens = to
            except (TypeError, ValueError):
                pass
            try:
                if tt is not None and total_tokens is not None and int(tt) < int(total_tokens):
                    total_tokens = tt
            except (TypeError, ValueError):
                pass

    # DAV-1426: prompt_tokens past USAGE_UNRELIABLE_THRESHOLD cannot be a
    # real single-call reading (the streaming double-count bug produced
    # values orders of magnitude beyond any legitimate prompt). Flag the
    # row — the captured numbers stay in the token columns as evidence,
    # but build_llm_usage_summary and ad-hoc statistics must exclude
    # flagged rows from aggregates.
    usage_unreliable = False
    try:
        if input_tokens is not None and int(input_tokens) > USAGE_UNRELIABLE_THRESHOLD:
            usage_unreliable = True
    except (TypeError, ValueError):
        pass

    return {
        "input_tokens": _usage_to_int(input_tokens),
        "cached_prompt_tokens": _usage_to_int(cached_prompt_tokens),
        "output_tokens": _usage_to_int(output_tokens),
        "reasoning_tokens": _usage_to_int(reasoning_tokens),
        "total_tokens": _usage_to_int(total_tokens),
        "usage_unreliable": usage_unreliable,
        "finish_reason": finish_reason,
        "model_name": model_name,
        "served_model": served_model,
        "system_fingerprint": system_fingerprint,
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
        self,
        run_id: Optional[UUID],
        metadata: Optional[Dict[str, Any]],
        invocation_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        if run_id is None:
            return
        # DAV-1430: the nominal model actually sent in the request payload.
        # LangChain passes _get_invocation_params() through kwargs; its "model"
        # key is self.model_name for OpenAI/Anthropic/Google chat clients.
        params = invocation_params or {}
        requested_model = params.get("model") or params.get("model_name")
        with self._lock:
            self._runs[str(run_id)] = {
                "t0": time.monotonic(),
                "metadata": metadata or {},
                "retried": False,
                "requested_model": (
                    str(requested_model) if requested_model else None
                ),
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
        self._register_run(
            run_id, metadata, invocation_params=kwargs.get("invocation_params")
        )

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
        self._register_run(
            run_id, metadata, invocation_params=kwargs.get("invocation_params")
        )

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
            # DAV-1334: the gate publishes its queue wait via contextvar;
            # elapsed_seconds must exclude it so the ledger reflects model
            # call time, not gate wait. Read-then-clear: the var persists
            # after the gate exits, and clearing prevents a leftover wait
            # from being attributed to a later ungated call in this context.
            queue_seconds = None
            queue_wait = 0.0
            try:
                from tradingagents.llm_clients.concurrency_gate import (
                    llm_queue_wait_seconds,
                )

                queue_wait = llm_queue_wait_seconds.get(0.0) or 0.0
                llm_queue_wait_seconds.set(0.0)
            except Exception:  # pragma: no cover - gate module always present
                queue_wait = 0.0
            if queue_wait > 0:
                queue_seconds = round(queue_wait, 2)
            elapsed = None
            if run.get("t0") is not None:
                elapsed = round(
                    max(0.0, time.monotonic() - run["t0"] - queue_wait), 2
                )

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
                queue_seconds=queue_seconds,
                requested_model=run.get("requested_model"),
                served_model=extracted["served_model"],
                system_fingerprint=extracted["system_fingerprint"],
                usage_unreliable=extracted["usage_unreliable"],
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

        # DAV-1426: rows flagged usage_unreliable carry token counts that
        # cannot be real (streaming cumulative-usage inflation). Keep them
        # out of every aggregate and report the exclusion count.
        reliable_rows = [r for r in rows if not getattr(r, "usage_unreliable", False)]
        unreliable_count = len(rows) - len(reliable_rows)

        def _sum(field: str) -> Optional[int]:
            vals = [getattr(r, field) for r in reliable_rows]
            vals = [v for v in vals if v is not None]
            return sum(vals) if vals else None

        by_role: Dict[str, Dict[str, Any]] = {}
        for r in reliable_rows:
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
                    "queue_seconds": 0.0,
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
            if getattr(r, "queue_seconds", None) is not None:
                entry["queue_seconds"] += r.queue_seconds
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
                    "queue_seconds": round(e["queue_seconds"], 2),
                    "retried_calls": e["retried_calls"],
                }
            )

        return {
            "report_id": report_id,
            "call_count": len(rows),
            "usage_unreliable_count": unreliable_count,
            "prompt_tokens": _sum("prompt_tokens"),
            "cached_prompt_tokens": _sum("cached_prompt_tokens"),
            "completion_tokens": _sum("completion_tokens"),
            "reasoning_tokens": _sum("reasoning_tokens"),
            "total_tokens": _sum("total_tokens"),
            "elapsed_seconds": _sum("elapsed_seconds"),
            "queue_seconds": _sum("queue_seconds"),
            "retried_calls": sum(1 for r in reliable_rows if r.retried),
            "by_role": roles,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("build_llm_usage_summary failed (non-fatal): %s", exc)
        return None


def build_served_models_summary(report_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """DAV-1430 (D-066 B3): per-role actual served models for one report.

    Returns ``{"by_role": {agent_name: [deduped served models]},
    "served_model_missing": <count>, "call_count": <count>}`` — the missing
    counter covers calls whose response body carried no ``model`` field (or
    errored before one could be read). Returns None when nothing was logged;
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
        by_role: Dict[str, set] = {}
        missing = 0
        for r in rows:
            role = r.agent_name or "unknown"
            if r.served_model:
                by_role.setdefault(role, set()).add(r.served_model)
            else:
                missing += 1
        return {
            "report_id": report_id,
            "call_count": len(rows),
            "by_role": {
                role: sorted(models)
                for role, models in sorted(by_role.items())
            },
            "served_model_missing": missing,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("build_served_models_summary failed (non-fatal): %s", exc)
        return None


# Shared instance: handlers are stateless apart from the per-run bookkeeping
# dict (keyed by run_id), so one object is safe to attach to every client.
LLM_USAGE_LOGGER = LLMUsageLogger()
