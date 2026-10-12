"""DAV-1463: /healthz must stay responsive while an analysis is running.

Regression guard for the event-loop stalls recorded on 2026-10-04: while the
backfill batch ran, ``/healthz`` did not answer within ``curl --max-time 8``
even though the process stayed alive and answered in ~1ms when idle. The
blocking work was running on the event loop thread instead of a worker thread,
so every other request queued behind it.

Each test stands in a long *synchronous* call of the kind that used to run
inline on the loop:

  1. the SQLite writes on the job failure / hard-timeout / manual-trigger paths,
  2. the model call inside signal processing, and
  3. the job-store count inside ``/healthz`` itself.

The probe is the real thing: an ASGI request to ``/healthz`` issued over the
same event loop while the blocking work is in flight, asserted to answer in
under 1 second against a 3-second blocking call.

Note on (2): ``_extract_decision_keyword`` returns a truthy value (``"UNKNOWN"``
at worst) for every input, so the LLM fallback in ``process_signal`` is
unreachable in practice; that path is covered here only as hardening against a
future change to the extractor.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

BLOCK_S = 3.0
RESPONSE_BUDGET_S = 1.0


async def _healthz_latency_during(app, blocking_call, tracker) -> tuple[float, bool]:
    """Serve /healthz while ``blocking_call()`` is running.

    Returns ``(latency_seconds, in_flight)``. ``in_flight`` is True only when
    the blocking call was still running (not yet released) at the moment
    /healthz answered — that is what makes the measurement meaningful.

    The job is scheduled first and /healthz is requested straight after, with
    no intervening await: if the code under test blocks the loop, the request
    cannot be served until the block ends, and the measured latency carries
    the whole blocking duration.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://probe") as client:
        job = asyncio.ensure_future(blocking_call())
        # Let the code under test reach its blocking call. This spin is safe in
        # both worlds: with the fix the loop is free and this exits immediately;
        # with the bug the loop is stuck inside the blocking call, so the spin
        # resumes only once that call returns, at which point ``in_flight`` is
        # already False and the assertion below rejects the measurement.
        deadline = time.perf_counter() + BLOCK_S * 3
        while not tracker.started.is_set() and time.perf_counter() < deadline:
            await asyncio.sleep(0.01)
        assert tracker.started.is_set(), "the blocking call never started"

        t0 = time.perf_counter()
        resp = await client.get("/healthz")
        elapsed = time.perf_counter() - t0
        in_flight = tracker.in_flight
        tracker.release.set()
        try:
            await job
        except (asyncio.CancelledError, Exception):
            pass
    assert resp.status_code in (200, 503), resp.text
    return elapsed, in_flight


class _SlowCall:
    """A long synchronous callable plus the events that describe its state."""

    def __init__(self, fn) -> None:
        self._fn = fn
        self.started = threading.Event()
        self.finished = threading.Event()
        self.release = threading.Event()
        self.thread_names: list[str] = []

    @property
    def in_flight(self) -> bool:
        return self.started.is_set() and not self.finished.is_set()

    def __call__(self):
        self.thread_names.append(threading.current_thread().name)
        self.started.set()
        # Auto-release bounds the test even when the code under test blocks
        # the loop (in that case the release below can never arrive in time).
        self.release.wait(timeout=BLOCK_S)
        self.finished.set()

    def slow_db(self, *_a, **_k):
        self()


def _assert_reported(elapsed: float, in_flight: bool, label: str) -> None:
    print(
        f"\n[DAV-1463/{label}] /healthz answered in {elapsed*1000:.1f}ms while a "
        f"{BLOCK_S}s synchronous call was in flight "
        f"(budget {RESPONSE_BUDGET_S*1000:.0f}ms)"
    )
    assert in_flight, (
        "the blocking call was not running while /healthz was served — the "
        "measurement proves nothing"
    )
    assert elapsed < RESPONSE_BUDGET_S, (
        f"/healthz took {elapsed:.3f}s while a {BLOCK_S}s synchronous call was "
        f"in flight — the blocking work is on the event loop"
    )


class _FakeLLM:
    """Stands in for quick_thinking_llm: .invoke blocks like a real round-trip."""

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self._started = started
        self._release = release
        self.invoked_from: list[str] = []

    def invoke(self, messages):
        self.invoked_from.append(threading.current_thread().name)
        self._started.set()
        self._release.wait(timeout=BLOCK_S + 10)
        return SimpleNamespace(content="HOLD")


# ---------------------------------------------------------------------------
# 1. Long synchronous SQLite writes on the job failure paths
# ---------------------------------------------------------------------------

def test_run_job_failure_sqlite_write_keeps_healthz_responsive(monkeypatch):
    """The DB write on _run_job's outer error guard must go through to_thread."""
    import api.main as m

    slow = _SlowCall(None)

    monkeypatch.setattr(m.report_service, "mark_report_failed", slow.slow_db)
    monkeypatch.setattr(m.report_service, "update_report_partial", slow.slow_db)

    async def _boom(*_a, **_k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(m, "_run_job_inner", _boom)

    request = SimpleNamespace(
        symbol="600000.SH", trade_date="2026-01-05", horizons=["short"],
    )

    async def run() -> None:
        await m._run_job("job-err", request, False, False, "u1", "test")

    elapsed, in_flight = asyncio.run(_healthz_latency_during(m.app, run, slow))
    _assert_reported(elapsed, in_flight, "run_job_failure_db")
    assert threading.current_thread().name not in slow.thread_names, (
        f"the SQLite write ran on the event loop thread ({slow.thread_names})"
    )


def test_run_job_hard_timeout_sqlite_write_keeps_healthz_responsive(monkeypatch):
    """The hard-timeout branch writes SQLite too; same requirement."""
    import api.main as m

    slow = _SlowCall(None)

    monkeypatch.setattr(m.report_service, "mark_report_failed", slow.slow_db)
    monkeypatch.setattr(m.report_service, "update_report_partial", slow.slow_db)

    async def _slow_inner(*_a, **_k):
        await asyncio.sleep(BLOCK_S + 10)

    monkeypatch.setattr(m, "_run_job_inner", _slow_inner)
    monkeypatch.setattr(m, "_JOB_TIMEOUT", 0)
    monkeypatch.setattr(m, "_JOB_HARD_TIMEOUT", 1)

    request = SimpleNamespace(
        symbol="600000.SH", trade_date="2026-01-05", horizons=["short"],
    )

    async def run() -> None:
        await m._run_job("job-hard", request, False, False, "u1", "test")

    elapsed, in_flight = asyncio.run(_healthz_latency_during(m.app, run, slow))
    _assert_reported(elapsed, in_flight, "run_job_hard_timeout_db")
    assert threading.current_thread().name not in slow.thread_names, (
        f"the SQLite write ran on the event loop thread ({slow.thread_names})"
    )


def test_manual_trigger_sqlite_write_keeps_healthz_responsive(monkeypatch):
    """_run_manual_trigger reads and writes SQLite; both go off-loop now."""
    import api.main as m

    slow = _SlowCall(None)

    def _slow_read(*_a, **_k):
        slow()
        return {}

    monkeypatch.setattr(m, "_build_imported_user_context", _slow_read)
    monkeypatch.setattr(
        m.scheduled_service, "record_manual_test_result", lambda *_a, **_k: None
    )

    task = {
        "id": "t1", "user_id": "u1", "symbol": "600000.SH",
        "horizon": "short", "manual_user_context": None,
    }

    async def run() -> None:
        await m._run_manual_trigger(task, "2026-01-05", "job-manual")

    elapsed, in_flight = asyncio.run(_healthz_latency_during(m.app, run, slow))
    _assert_reported(elapsed, in_flight, "manual_trigger_db")
    assert threading.current_thread().name not in slow.thread_names, (
        f"the SQLite read ran on the event loop thread ({slow.thread_names})"
    )


# ---------------------------------------------------------------------------
# 2. Model call inside signal processing (hardening; unreachable today)
# ---------------------------------------------------------------------------

def test_signal_extraction_llm_call_is_offloaded(monkeypatch):
    """aprocess_signal must never run llm.invoke on the loop thread.

    ``_extract_decision_keyword`` currently never returns ``None`` (it answers
    ``"UNKNOWN"`` as a last resort), so the LLM branch is unreachable today.
    Stub it to ``None`` to exercise the path that a future extractor change
    could activate.
    """
    from tradingagents.graph import signal_processing as sp

    monkeypatch.setattr(sp, "_extract_decision_keyword", lambda _t: None)
    started, release = threading.Event(), threading.Event()
    fake = _FakeLLM(started, release)

    async def main() -> None:
        proc = sp.SignalProcessor(fake)
        call = asyncio.create_task(
            proc.aprocess_signal("综合研判：当前风险收益比不明朗。")
        )
        assert await call == "HOLD"
        release.set()

    asyncio.run(main())
    assert fake.invoked_from, "the fake LLM was never reached"
    assert threading.current_thread().name not in fake.invoked_from, (
        f"llm.invoke ran on the calling thread ({fake.invoked_from}) instead "
        f"of a worker thread"
    )


def test_process_signal_llm_fallback_is_unreachable_today():
    """
    ``classify()`` has no fall-through, so the extractor always returns a
    truthy value and ``process_signal`` never spends an LLM call. Recorded so
    a future change to the extractor surfaces here instead of silently adding a
    blocking model round-trip to the event loop.
    """
    from tradingagents.graph.signal_processing import _extract_decision_keyword

    samples = [
        "",
        "abc",
        "中文无关键词内容示例",
        "没有任何可识别结论的长文本",
        "最终建议：买入",
    ]
    for text in samples:
        assert _extract_decision_keyword(text), (
            f"extractor returned a falsy value for {text!r}; the LLM fallback "
            f"in process_signal has become reachable"
        )


def test_resolve_graph_decision_falls_back_for_test_doubles():
    """Graphs that only expose the sync method (test doubles) still work.

    ``_resolve_graph_decision`` must not require the async twin: plenty of
    tests define a fake graph with ``process_signal`` alone, and the job path
    has to keep accepting it — while still running the call off the loop.
    """
    import api.main as m

    class _SyncOnlyGraph:
        def __init__(self) -> None:
            self.thread_names: list[str] = []

        def process_signal(self, text):
            import threading

            self.thread_names.append(threading.current_thread().name)
            return "BUY"

    async def main() -> None:
        graph = _SyncOnlyGraph()
        result = await m._resolve_graph_decision(graph, "最终建议：买入")
        assert result == "BUY"
        return graph

    graph = asyncio.run(main())
    import threading

    assert threading.current_thread().name not in graph.thread_names, (
        f"process_signal ran on the event loop thread ({graph.thread_names})"
    )


def test_resolve_graph_decision_prefers_async_twin():
    """When the async twin exists it is used directly."""
    import api.main as m

    calls: list[str] = []

    class _BothGraph:
        async def aprocess_signal(self, text):
            calls.append("async")
            return "SELL"

        def process_signal(self, text):  # pragma: no cover - must not be used
            calls.append("sync")
            return "BUY"

    result = asyncio.run(m._resolve_graph_decision(_BothGraph(), "x"))
    assert result == "SELL"
    assert calls == ["async"]


def test_aprocess_signal_matches_process_signal_results(monkeypatch):
    """Behaviour equivalence: the async twin must not change any decision."""
    from tradingagents.graph import signal_processing as sp

    started, release = threading.Event(), threading.Event()
    release.set()  # no blocking; this test only compares outcomes

    samples = [
        "最终建议：买入",
        "最终建议：卖出",
        "最终建议：观望为主",
        "方向：看多，谨慎看多为主，但需回避短期回调风险",
        "核心定性：风险偏高，回避追高，暂不建仓",
        "<!-- VERDICT: {\"direction\": \"N/A\"} -->",
        "",
        "没有任何可识别结论的长文本",
    ]
    for text in samples:
        sync_proc = sp.SignalProcessor(_FakeLLM(started, release))
        async_proc = sp.SignalProcessor(_FakeLLM(started, release))
        sync_result = sync_proc.process_signal(text)
        async_result = asyncio.run(async_proc.aprocess_signal(text))
        assert sync_result == async_result, (
            f"aprocess_signal diverged from process_signal for {text!r}: "
            f"{sync_result!r} vs {async_result!r}"
        )


# ---------------------------------------------------------------------------
# DAV-1957 🟡3: chat_completions failure branch writes SQLite off-loop
# ---------------------------------------------------------------------------

def test_chat_completions_failure_write_is_off_loop(monkeypatch):
    """The chat failure branch must not write SQLite on the event loop thread.

    Drives the real ``chat_completions`` endpoint (SSE mode) with a failing
    analysis entry point and asserts the resulting ``mark_report_failed`` write
    lands on a worker thread.
    """
    import api.main as m

    slow = _SlowCall(None)
    monkeypatch.setattr(m.report_service, "mark_report_failed", slow.slow_db)

    async def _boom(*_a, **_k):
        raise RuntimeError("synthetic chat failure")

    monkeypatch.setattr(m, "_run_job", _boom)
    monkeypatch.setattr(m, "_emit_job_event", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_set_job", lambda *_a, **_k: None)

    async def _stream(_job_id):
        if False:  # pragma: no cover - generator stand-in
            yield ""

    monkeypatch.setattr(m, "_stream_job_events", _stream)

    captured: dict = {}

    class _StreamingResponse:
        def __init__(self, content=None, **kwargs) -> None:
            captured["content"] = content

    monkeypatch.setattr(m, "StreamingResponse", _StreamingResponse)

    async def main() -> None:
        # Enter the endpoint and let its tracked task run to the failure branch.
        await m.chat_completions(_chat_request(), _fake_user())
        for _ in range(300):
            if slow.started.is_set():
                break
            await asyncio.sleep(0.01)
        assert slow.started.is_set(), "mark_report_failed was never called"
        slow.release.set()
        for _ in range(300):
            if not [t for t in list(m._background_tasks) if not t.done()]:
                break
            await asyncio.sleep(0.01)
        for t in list(m._background_tasks):
            t.cancel()
        m._background_tasks.clear()

    asyncio.run(main())
    assert threading.current_thread().name not in slow.thread_names, (
        f"the chat failure write ran on the event loop thread "
        f"({slow.thread_names})"
    )


def _chat_request():
    """A real ``ChatCompletionRequest`` so model_fields_set behaves normally."""
    import api.main as m

    return m.ChatCompletionRequest(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "分析一下 600000.SH"}],
    )


def _fake_user():
    class _U:
        id = "u-test"
        is_active = True
        email = "test@example.com"

    return _U()


def test_chat_completions_source_has_no_inline_db_write():
    """Static guard: no DB session may be opened *directly* on the loop.

    A ``with get_db_ctx()`` nested inside a plain ``def`` helper is fine as
    long as that helper is invoked through ``asyncio.to_thread`` (the codebase
    already does this for the two ``_load_user_context*`` helpers). What must
    never happen is opening the session directly in the ``async def`` body.
    """
    import ast
    import inspect

    import api.main as m

    src = inspect.getsource(m)
    tree = ast.parse(src)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name != "chat_completions":
            continue

        # A DB session opened inside a plain sync helper is fine when that
        # helper is dispatched with ``asyncio.to_thread`` (the codebase already
        # does this for the two ``_load_user_context*`` helpers). A nested
        # ``async def`` is different: its body also runs on the event loop, so
        # it must be walked rather than skipped wholesale (DAV-1976).
        def walk_stmt(stmts):
            for st in stmts:
                if isinstance(st, ast.FunctionDef) or isinstance(st, ast.Lambda):
                    continue
                if isinstance(st, ast.AsyncFunctionDef):
                    walk_stmt(st.body)
                    continue
                if isinstance(st, ast.With):
                    for item in st.items:
                        call = item.context_expr
                        if isinstance(call, ast.Call):
                            fn = call.func
                            name = (
                                fn.attr if isinstance(fn, ast.Attribute)
                                else getattr(fn, "id", "")
                            )
                            if name == "get_db_ctx":
                                offenders.append(call.lineno)
                    for body in st.body:
                        walk_stmt([body])
                    continue
                if isinstance(st, (ast.For, ast.While, ast.If, ast.Try,
                                   ast.With, ast.AsyncFor)):
                    walk_stmt(st.body)
                    walk_stmt(getattr(st, "orelse", []) or [])
                    walk_stmt(getattr(st, "finalbody", []) or [])
                    for h in getattr(st, "handlers", []) or []:
                        walk_stmt(h.body)
                    continue
                for child in ast.iter_child_nodes(st):
                    if isinstance(child, ast.stmt):
                        walk_stmt([child])

        walk_stmt(node.body)

    assert not offenders, (
        f"chat_completions opens a synchronous DB session inline on the event "
        f"loop at line(s) {offenders}"
    )


# ---------------------------------------------------------------------------
# DAV-1957 🟡1: twin detection must require a real coroutine function
# ---------------------------------------------------------------------------

def test_yellow1_bare_magicmock_falls_back_to_sync_stub():
    """A bare MagicMock auto-creates any attribute; it must not be awaited.

    With a ``callable()`` check the branch took the "twin" path and raised
    ``TypeError: object MagicMock can't be used in 'await' expression``,
    turning a duck-typed graph into a failed analysis.
    """
    import api.main as m
    from unittest.mock import MagicMock

    graph = MagicMock()
    graph.process_signal.return_value = "BUY"

    result = asyncio.run(m._resolve_graph_decision(graph, "最终建议：买入"))

    assert result == "BUY", f"expected the sync stub's value, got {result!r}"
    assert graph.process_signal.called, "the sync stub was bypassed"


def test_yellow1_specced_magicmock_uses_async_twin_deliberately():
    """``MagicMock(spec=TradingAgentsGraph)`` builds a genuine ``AsyncMock`` twin.

    ``asyncio.iscoroutinefunction`` therefore returns True and the async path
    is taken on purpose: the spec says this graph has an async twin, so
    honouring it is correct. What the spec'd mock must never do is return a
    *bare* Mock in place of a decision string, so assert the value the twin
    was told to return.
    """
    import api.main as m
    from unittest.mock import MagicMock
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = MagicMock(spec=TradingAgentsGraph)
    graph.aprocess_signal.return_value = "SELL"
    graph.process_signal.return_value = "BUY"

    result = asyncio.run(m._resolve_graph_decision(graph, "最终建议：卖出"))

    assert result == "SELL", f"expected the twin's value, got {result!r}"
    assert not graph.process_signal.called, (
        "the sync stub should not run when a real async twin is present"
    )


def test_yellow1_sync_method_only_object_uses_sync_stub():
    """A graph exposing only the sync method must use it, off-loop."""
    import api.main as m

    class _SyncOnly:
        def __init__(self) -> None:
            import threading

            self.threads: list[str] = []

        def process_signal(self, text):
            import threading

            self.threads.append(threading.current_thread().name)
            return "HOLD"

    graph = _SyncOnly()
    result = asyncio.run(m._resolve_graph_decision(graph, "x"))
    import threading

    assert result == "HOLD"
    assert threading.current_thread().name not in graph.threads, (
        f"process_signal ran on the event loop thread ({graph.threads})"
    )


def test_yellow1_real_graph_uses_async_twin():
    """The production graph genuinely has an async twin; it must be used."""
    import api.main as m

    calls: list[str] = []

    class _RealishGraph:
        async def aprocess_signal(self, text):
            calls.append("async")
            return "HOLD"

        def process_signal(self, text):  # pragma: no cover - must not be used
            calls.append("sync")
            return "BUY"

    result = asyncio.run(m._resolve_graph_decision(_RealishGraph(), "x"))
    assert result == "HOLD"
    assert calls == ["async"]


# ---------------------------------------------------------------------------
# DAV-1957 🟡2: the count must time out so the saturation probe stays reachable
# ---------------------------------------------------------------------------

def test_healthz_job_count_timeout_keeps_probe_reachable(monkeypatch):
    """A hung active_job_count must degrade, not hang /healthz.

    Without the timeout the await parks forever on a saturated executor and the
    DAV-1885 ``thread_pool_starved`` probe below it can never run — the probe
    this change was meant to keep diagnostic.
    """
    import api.main as m
    from api.job_store import InMemoryJobStore

    store = InMemoryJobStore()
    release = threading.Event()

    def _hung_count():
        release.wait(timeout=30)
        return 1

    monkeypatch.setattr(store, "active_job_count", _hung_count)
    monkeypatch.setattr(m, "get_job_store", lambda: store)
    monkeypatch.setattr(m, "_HEALTHZ_JOB_COUNT_TIMEOUT_S", 0.5)

    async def main() -> None:
        transport = httpx.ASGITransport(app=m.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://probe") as client:
            t0 = time.perf_counter()
            resp = await client.get("/healthz")
            elapsed = time.perf_counter() - t0
        release.set()
        return resp, elapsed

    resp, elapsed = asyncio.run(main())

    print(
        f"\n[DAV-1957/🟡2] /healthz answered in {elapsed*1000:.1f}ms with a hung "
        f"active_job_count (timeout 500ms)"
    )
    assert elapsed < 2.0, (
        f"/healthz took {elapsed:.3f}s with a hung job-store count — the "
        f"timeout is not applied"
    )
    body = resp.json()
    assert body["active_analysis_count"] is None
    assert "active_analysis_count_error" in body, (
        "a timed-out count must surface as an error field, not vanish"
    )
    # The probe must still have run and answered normally.
    assert resp.status_code in (200, 503)


def test_healthz_job_count_timeout_is_below_saturation_probe(monkeypatch):
    """The count timeout must leave room for the 5s saturation probe."""
    import api.main as m

    assert 0 < m._HEALTHZ_JOB_COUNT_TIMEOUT_S < 5, (
        f"count timeout {m._HEALTHZ_JOB_COUNT_TIMEOUT_S}s must be below the 5s "
        f"thread-pool saturation probe"
    )


# ---------------------------------------------------------------------------
# 3. /healthz's own job-store count must not run on the loop
# ---------------------------------------------------------------------------

def test_healthz_job_store_count_is_offloaded(monkeypatch):
    """DAV-1463: active_job_count() blocks in Redis mode; off the loop now.

    The response still waits for the count (that is the endpoint's contract),
    so this asserts the *thread*: the call must not occupy the loop thread.
    """
    import api.main as m
    from api.job_store import InMemoryJobStore

    store = InMemoryJobStore()
    invoked_from: list[str] = []

    def _counting():
        invoked_from.append(threading.current_thread().name)
        return 3

    monkeypatch.setattr(store, "active_job_count", _counting)
    monkeypatch.setattr(m, "get_job_store", lambda: store)

    async def main() -> None:
        transport = httpx.ASGITransport(app=m.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://probe") as client:
            resp = await client.get("/healthz")
        assert resp.status_code in (200, 503), resp.text

    asyncio.run(main())
    assert invoked_from, "active_job_count was never called"
    assert threading.current_thread().name not in invoked_from, (
        f"active_job_count ran on the event loop thread ({invoked_from})"
    )