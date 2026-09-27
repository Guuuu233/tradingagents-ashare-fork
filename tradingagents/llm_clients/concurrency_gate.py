"""Process-level concurrency gate for LLM calls, keyed by model name prefix.

DAV-1331: devin/* providers enforce a concurrency ceiling (~5 in-flight
requests); a single two-tier analysis already bursts ~14 simultaneous
requests. This module caps in-flight calls per model prefix with a
threading.BoundedSemaphore so synchronous nodes (running on the LangChain
thread pool) and async nodes (running on the event loop) share the same
counter. Async callers acquire the semaphore via the default executor so
waiting never blocks the event loop.

DAV-1334: re-entrant acquires no longer take a second slot. The gate's "no
nesting" property relied on langchain internals (_generate not calling
_stream); if a library upgrade introduces a nested acquire, the caller's
context already holds a slot for that prefix, so the inner acquire passes
through instead of deadlocking against the cap.

The gate also publishes each acquire's queue-wait duration through
``llm_queue_wait_seconds`` so the usage ledger can separate queue time from
model-call time (elapsed_seconds used to start at on_chat_model_start,
before the gate, and silently included the wait).

Configured via the MODEL_CONCURRENCY_LIMITS constant below; no new
environment variables. Models not matching any prefix are unaffected.
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import threading
import time
from contextvars import ContextVar
from typing import AsyncIterator, Dict, FrozenSet, Iterator, Optional

_logger = logging.getLogger(__name__)

# Model-name prefix -> max in-flight requests. devin/* is capped at 4 to
# keep one slot of headroom under the provider's 5-request ceiling.
MODEL_CONCURRENCY_LIMITS: Dict[str, int] = {"devin/": 4}

# Queue-wait durations above this threshold are logged (the wait itself is
# never counted into model-call latency; logging is the queue-time record).
_QUEUE_LOG_THRESHOLD_S = 0.05

# Prefixes for which THIS context already holds a slot. A re-entrant acquire
# for a held prefix passes through without consuming a second slot, so a
# nested call (e.g. _generate internally invoking _stream) cannot deadlock
# against the cap. ContextVar: threads spawned via run_in_executor and
# asyncio tasks inherit the caller's context, so nesting is detected only
# where it can actually occur.
_held_prefixes: ContextVar[FrozenSet[str]] = ContextVar(
    "llm_gate_held_prefixes", default=frozenset()
)

# Queue-wait seconds of the most recent slot acquire in this context. The
# usage ledger reads (and clears) this at call end to record queue_seconds
# and strip the wait out of elapsed_seconds. The acquire path sets it
# without resetting so the value survives gate exit for the ledger to read;
# pass-through paths shadow it to 0.0 (restored on exit) so a nested call
# never inherits an outer call's wait.
llm_queue_wait_seconds: ContextVar[float] = ContextVar(
    "llm_queue_wait_seconds", default=0.0
)

_semaphore_lock = threading.Lock()
_semaphores: Dict[str, threading.BoundedSemaphore] = {}


def _prefix_for_model(model: object) -> Optional[str]:
    """Return the concurrency-capped prefix for a model name, or None."""
    name = str(model or "").lower()
    for prefix in MODEL_CONCURRENCY_LIMITS:
        if name.startswith(prefix):
            return prefix
    return None


def _semaphore_for_prefix(prefix: str) -> threading.BoundedSemaphore:
    """Return the shared semaphore for a capped prefix, creating it lazily."""
    with _semaphore_lock:
        sem = _semaphores.get(prefix)
        if sem is None:
            sem = threading.BoundedSemaphore(MODEL_CONCURRENCY_LIMITS[prefix])
            _semaphores[prefix] = sem
    return sem


def _log_wait(model: object, waited_s: float) -> None:
    if waited_s >= _QUEUE_LOG_THRESHOLD_S:
        _logger.info(
            "[LLM Gate] model=%s queued %.2fs for a concurrency slot",
            model,
            waited_s,
        )


@contextlib.contextmanager
def acquire_llm_slot(model: object) -> Iterator[None]:
    """Sync gate: hold one in-flight slot for the model's prefix.

    The semaphore is always released on exit (exception, early return,
    generator close), so a failed or interrupted call never leaks a slot.
    A re-entrant acquire for a prefix this context already holds passes
    through without taking another slot.
    """
    prefix = _prefix_for_model(model)
    if prefix is None or prefix in _held_prefixes.get():
        # Uncapped model, or a nested acquire while this context already
        # holds the prefix's slot — pass through. Shadow the queue marker
        # so an inner ledger record doesn't inherit an outer wait.
        token = llm_queue_wait_seconds.set(0.0)
        try:
            yield
        finally:
            llm_queue_wait_seconds.reset(token)
        return
    sem = _semaphore_for_prefix(prefix)
    start = time.monotonic()
    sem.acquire()
    waited = time.monotonic() - start
    _log_wait(model, waited)
    # No reset: the ledger reads this after the gate exits.
    llm_queue_wait_seconds.set(waited)
    held_token = _held_prefixes.set(_held_prefixes.get() | {prefix})
    try:
        yield
    finally:
        _held_prefixes.reset(held_token)
        sem.release()


@contextlib.asynccontextmanager
async def acquire_llm_slot_async(model: object) -> AsyncIterator[None]:
    """Async gate: same counter as the sync path, without blocking the loop.

    The blocking acquire runs on a dedicated daemon thread. If the awaiting
    coroutine is cancelled while queued, the waiter is retracted and a
    cleanup thread releases any slot that landed in the race window, so a
    cancellation can never leak a slot.
    """
    prefix = _prefix_for_model(model)
    if prefix is None or prefix in _held_prefixes.get():
        token = llm_queue_wait_seconds.set(0.0)
        try:
            yield
        finally:
            llm_queue_wait_seconds.reset(token)
        return
    sem = _semaphore_for_prefix(prefix)
    start = time.monotonic()
    # Poll-based acquire on a dedicated daemon thread so a cancellation can
    # retract the queued waiter instead of leaving a thread blocked on
    # sem.acquire() forever (which would also hang interpreter shutdown).
    cancelled = threading.Event()

    def _acquire() -> bool:
        while not cancelled.is_set():
            if sem.acquire(timeout=0.1):
                if cancelled.is_set():
                    # Slot landed after the waiter was cancelled: the
                    # caller is gone, so hand it back immediately.
                    sem.release()
                    return False
                return True
        return False

    acquire_result: concurrent.futures.Future = concurrent.futures.Future()

    def _run_acquire() -> None:
        try:
            acquired = _acquire()
        except BaseException as exc:  # pragma: no cover - defensive
            try:
                acquire_result.set_exception(exc)
            except BaseException:
                pass
            return
        try:
            acquire_result.set_result(acquired)
        except concurrent.futures.InvalidStateError:
            # The awaiting coroutine's cancellation already marked the
            # future cancelled; if the acquire landed a slot anyway, give
            # it back here.
            if acquired:
                sem.release()

    threading.Thread(target=_run_acquire, daemon=True).start()
    try:
        # wrap_future: cancelling this coroutine leaves the underlying
        # concurrent future (and the acquire thread) running; the cleanup
        # thread below then retracts it or releases a late-landed slot.
        acquired = await asyncio.wrap_future(acquire_result)
    except asyncio.CancelledError:
        cancelled.set()

        def _cleanup() -> None:
            try:
                if acquire_result.result(timeout=10):
                    sem.release()
            # BaseException: concurrent.futures raises CancelledError
            # (a BaseException since 3.8) when the future was cancelled.
            except BaseException:
                pass

        threading.Thread(target=_cleanup, daemon=True).start()
        raise
    if not acquired:
        # The waiter was retracted by a cancellation between poll rounds.
        raise asyncio.CancelledError()
    waited = time.monotonic() - start
    _log_wait(model, waited)
    llm_queue_wait_seconds.set(waited)
    held_token = _held_prefixes.set(_held_prefixes.get() | {prefix})
    try:
        yield
    finally:
        _held_prefixes.reset(held_token)
        sem.release()


def _reset_gates_for_tests() -> None:
    """Drop all gate semaphores. Test-only helper; not used in production."""
    with _semaphore_lock:
        _semaphores.clear()
