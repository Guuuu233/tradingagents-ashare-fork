"""Tests for the devin/* concurrency gate (DAV-1331).

UnifiedChatOpenAI gates all four call paths (_generate/_agenerate/_stream/
_astream) on a process-wide semaphore keyed by model prefix. devin/* is
capped at 4 in-flight requests; other models are uncapped. Slots must be
released on exceptions, cancellation, and early stream close.
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tradingagents.llm_clients import concurrency_gate
from tradingagents.llm_clients.openai_client import UnifiedChatOpenAI


def _make_llm(model: str) -> UnifiedChatOpenAI:
    return UnifiedChatOpenAI(
        model=model,
        base_url="https://example.invalid/v1",
        api_key="sk-fake-key",
    )


@pytest.fixture(autouse=True)
def _reset_gates():
    concurrency_gate._reset_gates_for_tests()
    yield
    concurrency_gate._reset_gates_for_tests()


class _InFlightTracker:
    """Counts currently executing fake LLM calls and records the peak."""

    def __init__(self):
        self.current = 0
        self.peak = 0
        self._lock = threading.Lock()

    def enter(self):
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def exit(self):
        with self._lock:
            self.current -= 1


def _patch_sync_generate(monkeypatch, tracker, delay=0.02, raises=None):
    from langchain_openai.chat_models.base import BaseChatOpenAI

    def fake(self, *args, **kwargs):
        tracker.enter()
        try:
            time.sleep(delay)
            if raises:
                raise raises
            return "ok"
        finally:
            tracker.exit()

    monkeypatch.setattr(BaseChatOpenAI, "_generate", fake)


def _patch_async_generate(monkeypatch, tracker, delay=0.02, raises=None):
    from langchain_openai.chat_models.base import BaseChatOpenAI

    async def fake(self, *args, **kwargs):
        tracker.enter()
        try:
            await asyncio.sleep(delay)
            if raises:
                raise raises
            return "ok"
        finally:
            tracker.exit()

    monkeypatch.setattr(BaseChatOpenAI, "_agenerate", fake)


def _gate_sem() -> threading.BoundedSemaphore:
    return concurrency_gate._semaphores["devin/"]


class TestDevinConcurrencyCap:
    def test_sync_generate_capped_at_4(self, monkeypatch):
        tracker = _InFlightTracker()
        _patch_sync_generate(monkeypatch, tracker)
        llm = _make_llm("devin/swe-2")

        with ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(lambda _: llm._generate([], stop=None), range(20)))

        assert tracker.peak == 4
        assert tracker.current == 0

    def test_async_generate_capped_at_4(self, monkeypatch):
        tracker = _InFlightTracker()
        _patch_async_generate(monkeypatch, tracker)
        llm = _make_llm("devin/swe-2")

        async def run():
            await asyncio.gather(
                *(llm._agenerate([], stop=None) for _ in range(20))
            )

        asyncio.run(run())
        assert tracker.peak == 4
        assert tracker.current == 0

    def test_sync_stream_capped_at_4(self, monkeypatch):
        tracker = _InFlightTracker()

        def fake_stream(self, *args, **kwargs):
            tracker.enter()
            try:
                time.sleep(0.02)
                yield "chunk"
            finally:
                tracker.exit()

        monkeypatch.setattr(
            UnifiedChatOpenAI.__mro__[1], "_stream", fake_stream
        )
        llm = _make_llm("devin/swe-2")

        with ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(lambda _: list(llm._stream([], stop=None)), range(20)))

        assert tracker.peak == 4
        assert tracker.current == 0

    def test_async_stream_capped_at_4(self, monkeypatch):
        tracker = _InFlightTracker()

        async def fake_astream(self, *args, **kwargs):
            tracker.enter()
            try:
                await asyncio.sleep(0.02)
                yield "chunk"
            finally:
                tracker.exit()

        monkeypatch.setattr(
            UnifiedChatOpenAI.__mro__[1], "_astream", fake_astream
        )
        llm = _make_llm("devin/swe-2")

        async def consume():
            async for _ in llm._astream([], stop=None):
                pass

        async def run():
            await asyncio.gather(*(consume() for _ in range(20)))

        asyncio.run(run())
        assert tracker.peak == 4
        assert tracker.current == 0

    def test_sync_and_async_share_one_counter(self, monkeypatch):
        """Sync (thread pool) and async (event loop) calls share the cap."""
        tracker = _InFlightTracker()
        _patch_sync_generate(monkeypatch, tracker, delay=0.05)
        _patch_async_generate(monkeypatch, tracker, delay=0.05)
        llm = _make_llm("devin/swe-2")

        async def run():
            loop = asyncio.get_running_loop()
            sync_calls = [
                loop.run_in_executor(None, llm._generate, [], None)
                for _ in range(10)
            ]
            async_calls = [llm._agenerate([], stop=None) for _ in range(10)]
            await asyncio.gather(*(sync_calls + async_calls))

        asyncio.run(run())
        assert tracker.peak == 4
        assert tracker.current == 0

    def test_non_devin_model_uncapped(self, monkeypatch):
        tracker = _InFlightTracker()
        _patch_async_generate(monkeypatch, tracker)
        llm = _make_llm("gpt-4o")

        async def run():
            await asyncio.gather(
                *(llm._agenerate([], stop=None) for _ in range(10))
            )

        asyncio.run(run())
        assert tracker.peak == 10

    def test_exception_releases_slot(self, monkeypatch):
        tracker = _InFlightTracker()
        _patch_async_generate(monkeypatch, tracker, raises=RuntimeError("boom"))
        llm = _make_llm("devin/swe-2")

        async def run():
            results = await asyncio.gather(
                *(llm._agenerate([], stop=None) for _ in range(10)),
                return_exceptions=True,
            )
            assert all(isinstance(r, RuntimeError) for r in results)

        asyncio.run(run())
        # All slots released — the gate is not wedged after failures.
        assert _gate_sem()._value == 4

    def test_early_stream_close_releases_slot(self, monkeypatch):
        def fake_stream(self, *args, **kwargs):
            for i in range(100):
                yield f"chunk-{i}"
                time.sleep(0.001)

        monkeypatch.setattr(
            UnifiedChatOpenAI.__mro__[1], "_stream", fake_stream
        )
        llm = _make_llm("devin/swe-2")

        gen = llm._stream([], stop=None)
        next(gen)
        gen.close()  # simulates a broken/interrupted stream

        for _ in range(100):
            if _gate_sem()._value == 4:
                break
            time.sleep(0.01)
        assert _gate_sem()._value == 4

    def test_cancelled_waiter_does_not_leak_slot(self, monkeypatch):
        """A task cancelled while queued must not consume a slot later."""
        entered = threading.Event()
        release = threading.Event()

        async def fake(self, *args, **kwargs):
            entered.set()
            await asyncio.to_thread(release.wait)
            return "ok"

        from langchain_openai.chat_models.base import BaseChatOpenAI

        monkeypatch.setattr(BaseChatOpenAI, "_agenerate", fake)
        llm = _make_llm("devin/swe-2")

        async def run():
            holders = [asyncio.create_task(llm._agenerate([], stop=None)) for _ in range(4)]
            waiter = asyncio.create_task(llm._agenerate([], stop=None))
            await asyncio.sleep(0.1)  # waiter is now queued on the gate
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            release.set()
            await asyncio.gather(*holders)

        asyncio.run(run())
        # The queued acquire may land after cancellation; the compensating
        # release must return the gate to full capacity.
        for _ in range(200):
            if _gate_sem()._value == 4:
                break
            time.sleep(0.01)
        assert _gate_sem()._value == 4
