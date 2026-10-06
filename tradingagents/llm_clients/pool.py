"""Process-wide LLM instance pool with explicit upstream connection lifecycle.

DAV-1571 (follow-up to the DAV-1537 investigation): production logs showed
~8,679 model-client initializations (~69 per analysis) because every graph
construction called ``create_llm_client(...).get_llm()`` fresh, each one
building a new ``ChatOpenAI`` + httpx connection pool that was only reaped
by GC ``__del__`` — leaving upstream connections in CLOSE_WAIT.

This module caches the *constructed LLM instance* keyed by its effective
configuration signature, so:

1. Repeated ``TradingAgentsGraph`` constructions reuse the same client when
   the role/provider/model/base_url/api_key/params are identical — client
   initialization count no longer grows linearly with job count.
2. The underlying httpx pools (``root_client._client`` /
   ``root_async_client._client``) get an explicit close path via
   :func:`close_llm_pool` / :func:`aclose_llm_pool`, wired into the FastAPI
   lifespan shutdown.
3. LLM-call usage attribution is preserved: ``attach_usage_logger`` runs
   once at construction; LangChain merges per-invocation ``callbacks`` from
   RunnableConfig with constructor callbacks, so runtime metadata still
   flows through call-site kwargs.

Key derivation is *conservative*: it only covers kwargs that are lists,
dicts, sets, or scalars — anything whose semantics is well-defined by
equality. Any other object (custom callbacks, injected http_client, ...)
opts the caller out of pooling automatically, so a per-run unique object
can never be served a stale shared instance.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Dict, Hashable, Optional, Tuple

_logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_POOL: Dict[Hashable, Any] = {}

# Observability counters (read by tests / health probes via pool_stats()).
_COUNTS = {
    "build": 0,   # underlying factory invocations (new instances built)
    "hit": 0,     # requests served from the pool
    "miss": 0,    # first requests for a key
    "bypass": 0,  # requests not poolable (unhashable/non-scalar kwargs)
    "closed": 0,  # instances explicitly closed
}


def _hashable_value(value: Any) -> Optional[Hashable]:
    """Return a hashable, equality-stable representation of ``value`` or None.

    None → the value is not poolable and the caller must bypass the pool.
    """
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return value
    if isinstance(value, (list, tuple)):
        items = []
        for v in value:
            hv = _hashable_value(v)
            if hv is None:
                return None
            items.append(hv)
        return tuple(items)
    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            hk = _hashable_value(k)
            hv = _hashable_value(v)
            if hk is None or hv is None:
                return None
            items.append((hk, hv))
        return frozenset(items)
    if isinstance(value, (set, frozenset)):
        items = []
        for v in value:
            hv = _hashable_value(v)
            if hv is None:
                return None
            items.append(hv)
        return frozenset(items)
    return None


def _pool_key(
    factory: Callable[..., Any],
    provider: str,
    model: str,
    base_url: Optional[str],
    kwargs: Dict[str, Any],
) -> Optional[Hashable]:
    parts = []
    for name in sorted(kwargs):
        hv = _hashable_value(kwargs[name])
        if hv is None:
            return None
        parts.append((name, hv))
    # Including the factory identity keeps unit tests isolated: each test
    # patches create_llm_client with a fresh recorder/Mock, which hashes
    # differently from the real factory, so pools never leak mocks across
    # tests. In production the factory is the same module-level function.
    return ("llm", factory, provider, model, base_url, tuple(parts))


def _get_llm_from_client(client: Any) -> Any:
    return client.get_llm() if hasattr(client, "get_llm") else client


def get_or_create_llm(
    factory: Callable[..., Any],
    *,
    provider: str,
    model: str,
    base_url: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Return the pooled LLM for this config signature, building once.

    ``factory`` is called with ``(provider=..., model=..., base_url=..., **kwargs)``
    on a miss only; it must return either a ``BaseLLMClient`` (``get_llm()``)
    or a ready LLM instance. Results are cached indefinitely until the pool
    is closed or invalidated.
    """
    if _pool_disabled():
        _COUNTS["bypass"] += 1
        return _get_llm_from_client(
            factory(provider=provider, model=model, base_url=base_url, **kwargs)
        )

    key = _pool_key(factory, provider, model, base_url, kwargs)
    if key is None:
        _COUNTS["bypass"] += 1
        return _get_llm_from_client(
            factory(provider=provider, model=model, base_url=base_url, **kwargs)
        )

    with _LOCK:
        hit = _POOL.get(key)
        if hit is not None:
            _COUNTS["hit"] += 1
            return hit

    # Build outside the lock so a slow constructor does not serialize misses
    # on unrelated keys; double-checked under the lock below.
    built = _get_llm_from_client(
        factory(provider=provider, model=model, base_url=base_url, **kwargs)
    )

    with _LOCK:
        existing = _POOL.get(key)
        if existing is not None:
            # A racing builder won; drop ours (closing it below) and keep
            # theirs so pool identity stays unique per key.
            _close_llm_instance(built, async_close=False)
            _COUNTS["hit"] += 1
            return existing
        _POOL[key] = built
        _COUNTS["miss"] += 1
        _COUNTS["build"] += 1
        return built


def _close_transport(llm: Any, *, async_close: bool) -> int:
    """Close the httpx transports reachable from a LangChain LLM instance.

    Handles langchain-openai's ``root_client``/``root_async_client`` (each
    an openai SDK client whose ``._client`` is the httpx transport) and
    explicitly injected ``http_client``/``http_async_client``. Best-effort:
    never raises. Returns the number of SDK client / httpx closes issued.
    """
    closed = 0
    seen: set = set()

    def _mark(obj: Any) -> bool:
        if obj is None or id(obj) in seen:
            return False
        seen.add(id(obj))
        return True

    def _close_sdk_client(sdk_client: Any) -> None:
        """openai.OpenAI-style client: .close() closes its httpx transport."""
        nonlocal closed
        if not _mark(sdk_client):
            return
        try:
            is_closed = getattr(sdk_client, "is_closed", None)
            if callable(is_closed) and is_closed():
                return
            close = getattr(sdk_client, "close", None)
            if callable(close):
                close()
                closed += 1
        except Exception as exc:
            _logger.debug("[LLMPool] sync client close failed: %r", exc)

    def _close_httpx_sync(httpx_client: Any) -> None:
        """Raw httpx.Client (e.g. injected http_client or sdk._client)."""
        nonlocal closed
        if not _mark(httpx_client):
            return
        try:
            if getattr(httpx_client, "is_closed", False):
                return
            close = getattr(httpx_client, "close", None)
            if callable(close):
                close()
                closed += 1
        except Exception as exc:
            _logger.debug("[LLMPool] httpx client close failed: %r", exc)

    def _close_httpx_async(httpx_client: Any) -> None:
        nonlocal closed
        if not _mark(httpx_client):
            return
        try:
            if getattr(httpx_client, "is_closed", False):
                return
            import asyncio

            try:
                asyncio.get_running_loop().create_task(httpx_client.aclose())
            except RuntimeError:
                if async_close:
                    asyncio.run(httpx_client.aclose())
                else:
                    # No running loop in a sync teardown path; the openai
                    # SDK's AsyncHttpxClientWrapper.__del__ schedules aclose
                    # on GC as before — nothing worse than the status quo.
                    return
            closed += 1
        except Exception as exc:
            _logger.debug("[LLMPool] httpx async client close failed: %r", exc)

    async def _aclose_sdk_client(sdk_client: Any) -> None:
        try:
            is_closed = getattr(sdk_client, "is_closed", None)
            if callable(is_closed) and is_closed():
                return
            close = getattr(sdk_client, "close", None)
            if close is not None:
                res = close()
                import inspect

                if inspect.isawaitable(res):
                    await res
        except Exception as exc:
            _logger.debug("[LLMPool] async sdk client close failed: %r", exc)

    # langchain-openai: root_client (openai.OpenAI) / root_async_client
    # (openai.AsyncOpenAI). Closing the SDK client closes its httpx
    # transport (SyncAPIClient.close → self._client.close()).
    _close_sdk_client(getattr(llm, "root_client", None))

    root_async = getattr(llm, "root_async_client", None)
    if root_async is not None and _mark(root_async):
        try:
            is_closed = getattr(root_async, "is_closed", None)
            already = callable(is_closed) and is_closed()
        except Exception:
            already = False
        if not already:
            import asyncio
            import inspect

            try:
                res = root_async.close()
                if inspect.isawaitable(res):
                    try:
                        asyncio.get_running_loop().create_task(_await_and_forget(res))
                    except RuntimeError:
                        if async_close:
                            asyncio.run(res)
                        else:
                            # Unawaited coroutine would warn; drop it.
                            try:
                                res.close()
                            except Exception:
                                pass
                            res = None
                    if res is not None:
                        closed += 1
            except Exception as exc:
                _logger.debug("[LLMPool] root_async_client close failed: %r", exc)

    # Explicitly injected transports (custom httpx clients).
    _close_httpx_sync(getattr(llm, "http_client", None))
    _close_httpx_async(getattr(llm, "http_async_client", None))

    return closed


async def _await_and_forget(coro):
    try:
        await coro
    except Exception as exc:
        _logger.debug("[LLMPool] awaited close failed: %r", exc)


def _close_llm_instance(llm: Any, *, async_close: bool) -> int:
    return _close_transport(llm, async_close=async_close)


def _pool_disabled() -> bool:
    """Opt-out switch (tests / emergency rollback): TA_LLM_POOL=0|off|disable."""
    raw = (os.environ.get("TA_LLM_POOL") or "").strip().lower()
    return raw in ("0", "off", "false", "disable", "disabled")


def invalidate_llm_pool() -> Tuple[int, int]:
    """Close every pooled instance and clear the registry.

    Returns ``(instances, transports)``. Safe to call at shutdown or when
    rotating credentials/roles for a *new* process segment; in-flight LLM
    calls keep working on their already-held references.
    """
    with _LOCK:
        items = list(_POOL.items())
        _POOL.clear()
    transports = 0
    for key, llm in items:
        try:
            transports += _close_llm_instance(llm, async_close=False)
            _COUNTS["closed"] += 1
        except Exception as exc:
            _logger.warning("[LLMPool] failed closing pooled llm %r: %r", key, exc)
    return len(items), transports


async def aclose_llm_pool() -> Tuple[int, int]:
    """Async variant of :func:`invalidate_llm_pool` for lifespan shutdown."""
    with _LOCK:
        items = list(_POOL.items())
        _POOL.clear()
    transports = 0
    for key, llm in items:
        try:
            transports += _close_llm_instance(llm, async_close=True)
            _COUNTS["closed"] += 1
        except Exception as exc:
            _logger.warning("[LLMPool] failed closing pooled llm %r: %r", key, exc)
    return len(items), transports


# Back-compat alias: "close" is the explicit upstream-connection management
# verb requested by DAV-1571.
close_llm_pool = invalidate_llm_pool


def pool_stats() -> Dict[str, int]:
    """Snapshot of pool counters (for tests, health probes, evidence)."""
    with _LOCK:
        return {**_COUNTS, "size": len(_POOL)}


def reset_pool_stats() -> None:
    with _LOCK:
        for k in _COUNTS:
            _COUNTS[k] = 0
