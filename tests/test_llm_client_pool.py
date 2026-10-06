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
