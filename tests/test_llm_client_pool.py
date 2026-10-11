"""DAV-1571 regression: model client reuse & upstream connection lifecycle.

Contracts:
1. ``get_or_create_llm`` pools LLM instances by effective config signature —
   calling it N times with identical (provider, model, base_url, params)
   builds the underlying client once, not N times (init count must NOT grow
   linearly with the number of consecutive tasks).
2. Different configs produce different instances.
3. Non-scalar kwargs (per-run callbacks, injected http_client) bypass the
   pool automatically — a per-run unique object is never served a stale
   shared instance.
4. ``aclose_llm_pool`` explicitly closes the underlying httpx transports
   (root_client._client / root_async_client._client / injected http clients)
   — the upstream-connection lifecycle is managed, not left to GC __del__.
5. TA_LLM_POOL=0 disables pooling (emergency rollback / tests).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from tradingagents.llm_clients.pool import (
    aclose_llm_pool,
    get_or_create_llm,
    invalidate_llm_pool,
    pool_stats,
    reset_pool_stats,
)


class _FakeLLM:
    """Stand-in for UnifiedChatOpenAI carrying fake sync+async SDK clients."""

    def __init__(self):
        self.root_client = MagicMock()
        self.root_client.is_closed.return_value = False
        self.root_async_client = MagicMock()
        self.root_async_client.is_closed.return_value = False
        self.http_client = None
        self.http_async_client = None


class _FakeClient:
    """Mimics BaseLLMClient — get_llm() returns the 'constructed' LLM."""

    def __init__(self, llm):
        self._llm = llm

    def get_llm(self):
        return self._llm


@pytest.fixture(autouse=True)
def clean_pool():
    invalidate_llm_pool()
    reset_pool_stats()
    yield
    invalidate_llm_pool()
    reset_pool_stats()


def _recording_factory(built: list):
    def factory(provider, model, base_url=None, **kwargs):
        llm = _FakeLLM()
        built.append({"provider": provider, "model": model, "llm": llm, "kwargs": kwargs})
        return _FakeClient(llm)

    return factory


def test_identical_config_reuses_client_over_n_tasks():
    """Contract 1: N consecutive 'tasks' with the same config → 1 build."""
    built = []
    factory = _recording_factory(built)
    N = 5
    llms = [
        get_or_create_llm(
            factory, provider="openai", model="gpt-4o-mini",
            base_url="https://x/v1", api_key="sk-1", temperature=0,
        )
        for _ in range(N)
    ]
    assert len(built) == 1
    assert all(l is llms[0] for l in llms)
    stats = pool_stats()
    assert stats["build"] == 1 and stats["miss"] == 1 and stats["hit"] == N - 1


def test_different_config_produces_different_client():
    """Contract 2: distinct (provider/model/key/params) → distinct instance."""
    built = []
    factory = _recording_factory(built)
    a = get_or_create_llm(factory, provider="openai", model="m1", api_key="k1")
    b = get_or_create_llm(factory, provider="openai", model="m2", api_key="k1")
    c = get_or_create_llm(factory, provider="openai", model="m1", api_key="k2")
    assert len(built) == 3
    assert a is not b and b is not c and a is not c


def test_nonscalar_kwargs_bypass_pool():
    """Contract 3: an unhashable callback object forces a fresh build each call."""
    built = []
    factory = _recording_factory(built)

    class _Cb:  # unhashable custom object → not poolable
        pass

    first = get_or_create_llm(
        factory, provider="openai", model="m", api_key="k", callbacks=[_Cb()]
    )
    second = get_or_create_llm(
        factory, provider="openai", model="m", api_key="k", callbacks=[_Cb()]
    )
    assert len(built) == 2
    assert first is not second
    assert pool_stats()["bypass"] == 2


def test_invalidate_closes_upstream_transports():
    """Contract 4: close_llm_pool closes sync + async httpx transports."""
    built = []
    factory = _recording_factory(built)
    llm = get_or_create_llm(factory, provider="openai", model="m", api_key="k")
    instances, _ = invalidate_llm_pool()
    assert instances == 1
    llm.root_client.close.assert_called_once()
    llm.root_async_client.close.assert_called_once()
    assert pool_stats()["size"] == 0


def test_pool_disabled_env_bypasses(monkeypatch):
    """Contract 5: TA_LLM_POOL=0 restores per-call construction."""
    monkeypatch.setenv("TA_LLM_POOL", "0")
    built = []
    factory = _recording_factory(built)
    a = get_or_create_llm(factory, provider="openai", model="m", api_key="k")
    b = get_or_create_llm(factory, provider="openai", model="m", api_key="k")
    assert len(built) == 2
    assert a is not b


def test_graph_construction_init_count_does_not_grow_with_n(caplog):
    """End-to-end: N TradingAgentsGraph() constructions → *real* OpenAIClient
    init count bounded by distinct configs, not by N (DAV-1537 regression).

    Counts the real ``[LLM Client] Init`` log emitted inside
    ``OpenAIClient.get_llm`` — this is the initialization DAV-1537 measured
    (~69 per analysis in production).
    """
    import logging
    from contextlib import ExitStack
    from unittest.mock import MagicMock, patch

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = {
        "project_dir": "/tmp/ta-dav1571",
        "llm_provider": "openai",
        "quick_think_llm": "gpt-4o-mini",
        "deep_think_llm": "gpt-4o",
        "api_key": "sk-x",
    }
    # Patch everything except create_llm_client/get_or_create_llm so the real
    # OpenAIClient + UnifiedChatOpenAI construction path runs. UnifiedChatOpenAI
    # builds an openai.OpenAI without network at __init__ (httpx client is lazy).
    patches = (
        patch("tradingagents.graph.trading_graph.FinancialSituationMemory"),
        patch("tradingagents.graph.trading_graph.GraphSetup"),
        patch("tradingagents.graph.trading_graph.ConditionalLogic"),
        patch("tradingagents.graph.trading_graph.Propagator"),
        patch("tradingagents.graph.trading_graph.Reflector"),
        patch("tradingagents.graph.trading_graph.SignalProcessor"),
        patch("tradingagents.graph.trading_graph.set_config"),
        patch("tradingagents.graph.trading_graph.ToolNode"),
        patch(
            "api.services.role_routing_service.resolve_all_roles",
            return_value={},
        ),
    )

    N = 4
    with caplog.at_level(logging.INFO, logger="tradingagents.llm_clients.openai_client"):
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            for _ in range(N):
                TradingAgentsGraph(config=dict(config), data_collector=MagicMock())

    inits = [r for r in caplog.records if "[LLM Client] Init" in r.getMessage()]
    # 2 distinct configs (deep + quick) — not 2*N. Linear growth would be 8.
    assert len(inits) == 2, (
        f"expected 2 real client inits for {N} graph constructions, "
        f"got {len(inits)}: {[r.getMessage() for r in inits]}"
    )
    assert pool_stats()["build"] == 2


# ---------------------------------------------------------------------------
# DAV-1842 MED-1: LRU cap + explicit close of evicted connections
# ---------------------------------------------------------------------------


def test_pool_is_lru_capped_and_evicted_instance_stays_open():
    """Over the cap the LRU entry is dropped from the registry but NOT closed.

    DAV-1842 MED-1 rework: a graph holds its LLM instance for the whole
    analysis and never touches the cache again, so closing an evicted entry
    would kill an in-flight analysis.
    """
    pool_max_size = pool_stats()["max_size"]
    factory = MagicMock(side_effect=lambda **kw: _FakeClient(_FakeLLM()))

    llm0 = get_or_create_llm(factory, provider="openai", model="m0", base_url=None)
    # Touch llm0 so it is no longer the LRU entry.
    assert get_or_create_llm(factory, provider="openai", model="m0", base_url=None) is llm0

    keys = []
    for i in range(1, pool_max_size + 1):
        keys.append(get_or_create_llm(factory, provider="openai", model=f"m{i}", base_url=None))

    stats = pool_stats()
    assert stats["size"] == pool_max_size, stats
    assert stats["evicted"] == 1, stats
    # Eviction must NOT close anything (closes belong to pool shutdown only).
    assert stats["closed"] == 0, stats
    assert llm0.root_client.close.call_count == 0
    assert llm0.root_async_client.close.call_count == 0
    assert keys[-1].root_client.close.call_count == 0
    # A key built after eviction rebuilds from the factory.
    factory.side_effect = None
    factory.return_value = _FakeClient(_FakeLLM())
    assert get_or_create_llm(factory, provider="openai", model="m0", base_url=None) is not llm0


def test_evicted_instance_can_still_serve_calls():
    """Card item 4: fill the pool until A is evicted; A still works."""
    class _StubTransport:
        """Mimics an openai SDK client with a real open/closed state."""

        def __init__(self):
            self.is_closed = False

        def close(self):
            if self.is_closed:
                raise RuntimeError("transport already closed")
            self.is_closed = True

        async def aclose(self):
            self.is_closed = True

        def invoke(self):
            if self.is_closed:
                raise RuntimeError("Cannot send a request, as the client is closed.")
            return "ok"

    class _LLM(_FakeLLM):
        def __init__(self):
            super().__init__()
            self.root_client = _StubTransport()
            self.root_async_client = _StubTransport()

    max_size = pool_stats()["max_size"]
    factory = MagicMock(side_effect=lambda **kw: _FakeClient(_LLM()))
    instance_a = get_or_create_llm(factory, provider="openai", model="A", base_url=None)
    assert instance_a.root_client.invoke() == "ok"

    for i in range(max_size):
        get_or_create_llm(factory, provider="openai", model=f"filler{i}", base_url=None)

    assert pool_stats()["evicted"] >= 1
    # A was the LRU entry and is now out of the registry, but a live analysis
    # still holding it must be able to call it without a "client closed" error.
    assert instance_a.root_client.invoke() == "ok"
    assert instance_a.root_client.is_closed is False


def test_eviction_logs_provider_model_without_api_key(caplog):
    caplog.set_level(logging.WARNING, logger="tradingagents.llm_clients.pool")
    factory = MagicMock(side_effect=lambda **kw: _FakeClient(_FakeLLM()))
    max_size = pool_stats()["max_size"]
    get_or_create_llm(
        factory, provider="openai", model="secret-model", base_url="https://gw/v1", api_key="sk-super-secret",
    )
    for i in range(max_size):
        get_or_create_llm(factory, provider="openai", model=f"f{i}", base_url=None)

    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("evicted LRU entry" in m for m in warns), warns
    msg = next(m for m in warns if "evicted LRU entry" in m)
    assert "provider=openai" in msg and "model=secret-model" in msg
    assert "sk-super-secret" not in msg
    assert "sk-super-secret" not in caplog.text


def test_lru_recency_prefers_reused_key_over_older_inserts():
    pool_max_size = pool_stats()["max_size"]
    factory = MagicMock(side_effect=lambda **kw: _FakeClient(_FakeLLM()))
    held = []
    for i in range(pool_max_size):
        held.append(get_or_create_llm(factory, provider="openai", model=f"m{i}", base_url=None))
    # Re-hit the oldest key twice -> it becomes most-recently-used.
    get_or_create_llm(factory, provider="openai", model="m0", base_url=None)
    get_or_create_llm(factory, provider="openai", model="m0", base_url=None)
    get_or_create_llm(factory, provider="openai", model="extra", base_url=None)

    assert pool_stats()["evicted"] == 1
    # Which entry got evicted is observable via the warning log, not via close().
    # m0 (re-hit) survives; the oldest *inserted* (m1) is the LRU victim.
    assert pool_stats()["size"] == pool_max_size


def test_pool_max_size_env_override(monkeypatch):
    monkeypatch.setenv("TA_LLM_POOL_MAX_SIZE", "3")
    assert pool_stats()["max_size"] == 3
    factory = MagicMock(side_effect=lambda **kw: _FakeClient(_FakeLLM()))
    for i in range(6):
        get_or_create_llm(factory, provider="openai", model=f"m{i}", base_url=None)
    assert pool_stats()["size"] == 3
    # Invalid values fall back to the documented default.
    monkeypatch.setenv("TA_LLM_POOL_MAX_SIZE", "0")
    assert pool_stats()["max_size"] == 32
    monkeypatch.setenv("TA_LLM_POOL_MAX_SIZE", "nonsense")
    assert pool_stats()["max_size"] == 32


# ---------------------------------------------------------------------------
# DAV-1842 MED-2: async teardown awaits completion
# ---------------------------------------------------------------------------


def test_aclose_llm_pool_awaits_async_transport_close():
    """No fire-and-forget: aclose_llm_pool returns only after aclose ran."""
    closed_flags = []

    class _SlowAsyncClient:
        def __init__(self):
            self.is_closed = False

        async def close(self):
            await asyncio.sleep(0.05)  # real await point
            self.is_closed = True

    class _SlowLLM(_FakeLLM):
        def __init__(self):
            super().__init__()
            self._async_sdk = _SlowAsyncClient()
            self.root_async_client = self._async_sdk

    factory = MagicMock(side_effect=lambda **kw: _FakeClient(_SlowLLM()))
    llm = get_or_create_llm(factory, provider="openai", model="slow", base_url=None)
    sdk = llm.root_async_client

    async def _run():
        return await aclose_llm_pool()

    instances, transports = asyncio.run(_run())
    assert (instances, transports) == (1, 2), (instances, transports)
    # The key MED-2 assertion: without an awaited close this is still False
    # right after aclose_llm_pool() returns.
    assert sdk.is_closed is True, "async transport close did not complete before return"


def test_aclose_transport_awaits_injected_httpx_async_client():
    class _HttpxAsync:
        def __init__(self):
            self.is_closed = False

        async def aclose(self):
            await asyncio.sleep(0.02)
            self.is_closed = True

    class _LLM(_FakeLLM):
        def __init__(self):
            super().__init__()
            self.http_async_client = _HttpxAsync()

    factory = MagicMock(side_effect=lambda **kw: _FakeClient(_LLM()))
    llm = get_or_create_llm(factory, provider="openai", model="inj", base_url=None)
    httpx_client = llm.http_async_client

    asyncio.run(aclose_llm_pool())
    assert httpx_client.is_closed is True
