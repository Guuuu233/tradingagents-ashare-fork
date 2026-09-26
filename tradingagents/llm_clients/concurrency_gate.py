"""Process-level concurrency gate for LLM calls, keyed by model name prefix.

DAV-1331: devin/* providers enforce a concurrency ceiling (~5 in-flight
requests); a single two-tier analysis already bursts ~14 simultaneous
requests. This module caps in-flight calls per model prefix with a
threading.BoundedSemaphore so synchronous nodes (running on the LangChain
thread pool) and async nodes (running on the event loop) share the same
counter. Async callers acquire the semaphore via the default executor so
waiting never blocks the event loop.

Configured via the MODEL_CONCURRENCY_LIMITS constant below; no new
environment variables. Models not matching any prefix are unaffected.
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import threading
import time
from typing import AsyncIterator, Dict, Iterator, Optional

_logger = logging.getLogger(__name__)

# Model-name prefix -> max in-flight requests. devin/* is capped at 4 to
# keep one slot of headroom under the provider's 5-request ceiling.
MODEL_CONCURRENCY_LIMITS: Dict[str, int] = {"devin/": 4}

# Queue-wait durations above this threshold are logged (the wait itself is
# never counted into model-call latency; logging is the queue-time record).
_QUEUE_LOG_THRESHOLD_S = 0.05

_semaphore_lock = threading.Lock()
_semaphores: Dict[str, threading.BoundedSemaphore] = {}


def _semaphore_for_model(model: object) -> Optional[threading.BoundedSemaphore]:
    """Return the shared semaphore for a model's prefix, or None if uncapped."""
    name = str(model or "").lower()
    for prefix, limit in MODEL_CONCURRENCY_LIMITS.items():
        if name.startswith(prefix):
            with _semaphore_lock:
                sem = _semaphores.get(prefix)
                if sem is None:
                    sem = threading.BoundedSemaphore(limit)
                    _semaphores[prefix] = sem
            return sem
    return None


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
    """
    sem = _semaphore_for_model(model)
    if sem is None:
        yield
        return
    start = time.monotonic()
    sem.acquire()
    _log_wait(model, time.monotonic() - start)
    try:
        yield
    finally:
        sem.release()


@contextlib.asynccontextmanager
async def acquire_llm_slot_async(model: object) -> AsyncIterator[None]:
    """Async gate: same counter as the sync path, without blocking the loop.

    The blocking acquire runs on a dedicated daemon thread. If the awaiting
    coroutine is cancelled while queued, the waiter is retracted and a
    cleanup thread releases any slot that landed in the race window, so a
    cancellation can never leak a slot.
    """
    sem = _semaphore_for_model(model)
    if sem is None:
        yield
        return
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
        except Exception as exc:  # pragma: no cover - defensive
            try:
                acquire_result.set_exception(exc)
            except Exception:
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
            except Exception:
                pass

        threading.Thread(target=_cleanup, daemon=True).start()
        raise
    if not acquired:
        # The waiter was retracted by a cancellation between poll rounds.
        raise asyncio.CancelledError()
    _log_wait(model, time.monotonic() - start)
    try:
        yield
    finally:
        sem.release()


def _reset_gates_for_tests() -> None:
    """Drop all gate semaphores. Test-only helper; not used in production."""
    with _semaphore_lock:
        _semaphores.clear()
