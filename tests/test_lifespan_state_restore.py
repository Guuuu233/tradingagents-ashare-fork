"""Regression tests for DAV-1021: lifespan must restore mutable global state.

The lifespan startup mutates three process/loop-level globals:

- ``socket.setdefaulttimeout(...)``          (process-wide default timeout)
- ``loop.set_default_executor(...)``         (event-loop default executor)
- ``api.main._default_executor``             (module-global reference)

Before the fix, an exception raised between those mutations and ``yield``
skipped the cleanup section entirely, leaking all three. These tests drive the
lifespan coroutine directly on a real event loop and assert that both the
failure path and the normal-exit path restore the exact pre-entry state.
"""
import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest
from unittest.mock import patch


@pytest.fixture
def _lifespan_patches(monkeypatch):
    """Stub every heavyweight startup step; init_db is left real by default."""
    from api import main as main_mod

    monkeypatch.setattr(main_mod, "_RUNTIME_IDENTITY_CACHE", None)
    monkeypatch.setenv("TA_SOCKET_DEFAULT_TIMEOUT", "60")
    patches = [
        patch("api.main.auth_service.ensure_secure_secret_configured"),
        patch("api.main.auth_service.is_custom_secret_configured", return_value=True),
        patch("api.main._report_version_stats"),
        patch("api.main._load_cn_stock_map", return_value={}),
        patch("tradingagents.dataflows.trade_calendar._load_cn_trade_dates"),
        patch(
            "api.services.report_service.recover_stale_active_reports",
            return_value={"failed": 0},
        ),
        patch("tradingagents.knowledge.historical_cases.backfill_pending_cases",
              return_value={}),
        patch.object(main_mod, "_resolve_runtime_commit_sha", return_value="e" * 40),
        patch.object(main_mod, "init_db"),
    ]
    for p in patches:
        p.start()
    yield main_mod
    for p in reversed(patches):
        p.stop()


def _snapshot(loop):
    return (
        socket.getdefaulttimeout(),
        getattr(loop, "_default_executor", None),
    )


class TestLifespanStateRestore:
    def test_startup_failure_restores_socket_timeout(self, _lifespan_patches):
        main_mod = _lifespan_patches

        socket.setdefaulttimeout(23.0)

        async def _run():
            loop = asyncio.get_running_loop()
            prev_timeout, prev_executor = _snapshot(loop)
            prev_module_executor = main_mod._default_executor
            try:
                with patch.object(
                    main_mod, "init_db", side_effect=RuntimeError("boom")
                ):
                    with pytest.raises(RuntimeError, match="boom"):
                        async with main_mod.lifespan(main_mod.app):
                            pass
            finally:
                restored = _snapshot(loop)
            return prev_timeout, prev_executor, prev_module_executor, restored

        try:
            prev_timeout, prev_executor, prev_module_executor, restored = (
                asyncio.run(_run())
            )
        finally:
            socket.setdefaulttimeout(None)
        assert restored[0] == prev_timeout
        assert restored[1] is prev_executor
        assert main_mod._default_executor is prev_module_executor

    def test_startup_failure_shuts_down_new_executor(self, _lifespan_patches):
        """The ta-asyncio executor built during startup must not leak threads."""
        main_mod = _lifespan_patches
        created = []
        real_tpe = ThreadPoolExecutor

        class _SpyTPE(real_tpe):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                if kw.get("thread_name_prefix") == "ta-asyncio":
                    created.append(self)

        async def _run():
            with patch.object(main_mod, "init_db", side_effect=RuntimeError("x")):
                with patch.object(main_mod, "ThreadPoolExecutor", _SpyTPE):
                    with pytest.raises(RuntimeError):
                        async with main_mod.lifespan(main_mod.app):
                            pass
            # Check before returning: asyncio.run() shuts down the loop default
            # executor during teardown, which would mask an un-fixed leak.
            return [ex for ex in created if not ex._shutdown]

        live = asyncio.run(_run())
        assert created, "lifespan never built the ta-asyncio executor"
        assert not live, f"{len(live)} ta-asyncio executor(s) left unshutdown"

    def test_normal_exit_restores_socket_timeout(self, _lifespan_patches):
        main_mod = _lifespan_patches
        # Use a sentinel distinct from the lifespan-set value (60) so the check
        # cannot pass vacuously due to a leak from an earlier test.
        socket.setdefaulttimeout(17.0)

        async def _run():
            async with main_mod.lifespan(main_mod.app):
                assert socket.getdefaulttimeout() == 60.0

        try:
            asyncio.run(_run())
            assert socket.getdefaulttimeout() == 17.0
        finally:
            socket.setdefaulttimeout(None)

    def test_failed_then_normal_lifespan_on_same_loop(self, _lifespan_patches):
        """A failed lifespan must not poison a later one on the same loop."""
        main_mod = _lifespan_patches

        async def _run():
            loop = asyncio.get_running_loop()
            prev_timeout, prev_executor = _snapshot(loop)
            prev_module_executor = main_mod._default_executor

            with patch.object(
                main_mod, "init_db", side_effect=RuntimeError("boom")
            ):
                with pytest.raises(RuntimeError):
                    async with main_mod.lifespan(main_mod.app):
                        pass

            # State fully restored after the failure...
            assert _snapshot(loop) == (prev_timeout, prev_executor)
            assert main_mod._default_executor is prev_module_executor

            # ...and a subsequent normal lifespan still works (asyncio.to_thread
            # relies on the loop default executor restored above).
            async with main_mod.lifespan(main_mod.app):
                assert await asyncio.to_thread(lambda: 42) == 42

            assert _snapshot(loop) == (prev_timeout, prev_executor)
            assert main_mod._default_executor is prev_module_executor

        asyncio.run(_run())
