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
from uuid import UUID

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

# Queue-wait seconds of the most recent slot acquire in this context.
#
# The contextvar is the producer-side fast path only: it is the correct
# value *while a single acquire context is live*, but it is NOT a safe
# ledger record on its own — the wait persists after the gate exits and a
# callback that reads it after the fact cannot tell whether the value
# belongs to the run it is closing or was left over by an earlier call in
# this context (DAV-1362). The authoritative hand-off to the usage ledger
# is the per-run stamp below (``_queue_wait_by_run``), captured at the
# innermost site where gate and run_id share one context.
llm_queue_wait_seconds: ContextVar[float] = ContextVar(
    "llm_queue_wait_seconds", default=0.0
)

# run_id (str) -> monotonic timestamp of the last run-stamp emitted in this
# context. Written by ``_stamp_queue_wait`` at run start (pre-gate) and
# again at run end so a retry inside one run_id also rotates the stamp.
_run_stamp_last: ContextVar[Optional[float]] = ContextVar(
    "llm_queue_run_stamp", default=None
)


@contextlib.contextmanager
def stamp_queue_wait_run(run_id: object) -> Iterator[None]:
    """Rotate the run stamp so the wait recorded inside belongs to this run.

    Must wrap the whole call (start -> gate -> end) in the context where
    the gate acquires: ``UnifiedChatOpenAI._generate/_agenerate/_stream/
    _astream`` do this at their outer boundary, before
    ``acquire_llm_slot[_async]`` sets ``llm_queue_wait_seconds``.

    Nested stamps are supported (each gets its own token); a non-None
    run_id is required to stamp — callers with no run_id skip stamping,
    which leaves the ledger's contextvar fallback in place.
    """
    if run_id is None:
        yield
        return
    token = _run_stamp_last.set(time.monotonic())
    try:
        yield
    finally:
        _run_stamp_last.reset(token)


def queue_wait_snapshot() -> Optional[float]:
    """Return the queue wait attributed to the CURRENT run stamp.

    ``None`` means "no wait was recorded under the active stamp" (ungated
    call, or the stamp rotated after the gate wrote). The usage ledger
    prefers this over the raw contextvar because it cannot inherit another
    call's wait: the stamp is set at the same context boundary where the
    gate writes, so the values can never come from different runs.
    """
    stamp = _run_stamp_last.get()
    if stamp is None:
        return None
    # The gate sets llm_queue_wait_seconds only on acquire; a nonzero
    # value under this stamp is this run's own wait.
    return llm_queue_wait_seconds.get()


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
    # No reset on the contextvar: the ledger reads this after the gate
    # exits. Also records the wait under this context's bound run_id
    # (set by on_*_start) so the ledger sees THIS call's own wait.
    _record_queue_wait(waited)
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
    _record_queue_wait(waited)
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


# ── per-run queue-wait snapshot (DAV-1362) ────────────────────────────
#
# The ledger's read-then-clear of ``llm_queue_wait_seconds`` used to be
# contextvar-based, which broke for any call whose LangChain callback ran
# in a *different* context than the gate acquire: langchain_core 1.3.3
# shields ``on_llm_end`` (``asyncio.create_task(coro,
# context=copy_context())``) and runs non-inline sync handlers via
# ``run_in_executor(copy_context().run, ...)``, while repair calls travel
# through ``asyncio.to_thread`` (also a context copy). In every one of
# those cases the handler saw either a stale leftover or an empty
# context, never this call's own wait.
#
# The authoritative record is now a per-run dict written by the gate
# itself at acquire time: ``begin_queue_wait(run_id)`` (called from
# ``on_*_start``, before the acquire) binds this context — and every
# context copied from it — to ``run_id`` via ``_current_queue_run``. The
# acquire path then writes ``_run_queue_waits[run_id] = waited``
# directly, so the value is correct no matter which context or call
# path (sync / async / streaming / repair) carried the acquire.
# ``pop_queue_wait`` removes the record at on_llm_end/on_llm_error.

_current_queue_run: ContextVar[Optional[str]] = ContextVar(
    "llm_queue_current_run", default=None
)

_run_queue_waits: Dict[str, float] = {}
_run_queue_waits_lock = threading.Lock()


def begin_queue_wait(run_id: Optional[UUID]) -> None:
    """Bind this context to ``run_id`` and open its wait record at 0.

    Called from ``on_llm_start``/``on_chat_model_start`` — always before
    the gated acquire — so any nonzero ``llm_queue_wait_seconds`` still
    in this context is a leftover from an earlier call; it is cleared
    here so neither this run nor a later one inherits it.
    """
    if run_id is None:
        return
    llm_queue_wait_seconds.set(0.0)
    _current_queue_run.set(str(run_id))
    with _run_queue_waits_lock:
        _run_queue_waits[str(run_id)] = 0.0


def _record_queue_wait(waited: float) -> None:
    """Write this acquire's wait to the run bound to this context.

    Invoked by both acquire paths immediately after the slot is granted.
    Waits below ``_QUEUE_LOG_THRESHOLD_S`` are not recorded — a sub-50ms
    acquire is "didn't queue" for ledger purposes (keeps queue_seconds
    NULL for uncontended calls, matching the pre-DAV-1362 convention and
    keeping elapsed_seconds honest). The raw contextvar is updated for
    any code still reading it.
    """
    llm_queue_wait_seconds.set(waited)
    if waited < _QUEUE_LOG_THRESHOLD_S:
        return
    run_key = _current_queue_run.get()
    if run_key is not None:
        with _run_queue_waits_lock:
            _run_queue_waits[run_key] = waited


def pop_queue_wait(run_id: Optional[UUID]) -> float:
    """Return and remove the queue wait recorded for ``run_id`` (0 if none).

    Only touches the module-level record — safe from a copied context
    (the contextvar clear lives in ``begin_queue_wait`` where it can
    actually reach the caller's context).
    """
    if run_id is None:
        return 0.0
    with _run_queue_waits_lock:
        return _run_queue_waits.pop(str(run_id), 0.0)


def _pop_queue_waits_for_tests() -> Dict[str, float]:
    """Return leftover snapshots (leak check). Test-only."""
    with _run_queue_waits_lock:
        out = dict(_run_queue_waits)
        _run_queue_waits.clear()
    return out
