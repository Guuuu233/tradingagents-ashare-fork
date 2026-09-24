"""DAV-1243: startup (lifespan) must not send any request to app.510168.xyz.

The upstream ``_report_version_stats()`` hook POSTed anonymous version stats
to ``https://app.510168.xyz/api/version-stats`` on every boot. That outbound
call was removed; this test guards against its return by patching
``requests.post``/``requests.get`` at the ``requests`` module level, running a
full lifespan cycle, and asserting no recorded call targets that host.
"""
import asyncio
import time
from unittest.mock import patch

import pytest


UPSTREAM_HOST = "app.510168.xyz"


@pytest.fixture
def _lifespan_patches(monkeypatch):
    """Stub every heavyweight startup step (same shape as test_lifespan_state_restore)."""
    from api import main as main_mod

    monkeypatch.setattr(main_mod, "_RUNTIME_IDENTITY_CACHE", None)
    monkeypatch.setenv("TA_SOCKET_DEFAULT_TIMEOUT", "60")
    patches = [
        patch("api.main.auth_service.ensure_secure_secret_configured"),
        patch("api.main.auth_service.is_custom_secret_configured", return_value=True),
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


def _called_urls(mock):
    urls = []
    for call in mock.call_args_list:
        args, kwargs = call
        if args:
            urls.append(str(args[0]))
        if "url" in kwargs:
            urls.append(str(kwargs["url"]))
        if "json" in kwargs:
            urls.append(repr(kwargs["json"]))
    return urls


class TestNoUpstreamVersionStatsReport:
    def test_lifespan_sends_no_request_to_upstream_stats_host(self, _lifespan_patches):
        main_mod = _lifespan_patches

        async def _run():
            async with main_mod.lifespan(main_mod.app):
                pass

        with patch("requests.post") as mock_post, \
             patch("requests.get") as mock_get:
            asyncio.run(_run())
            # The removed reporter fired from a daemon thread; give any
            # straggler thread a moment so a regression can't race past us.
            time.sleep(0.5)
            for t in list(__import__("threading").enumerate()):
                if t.daemon and t.name.startswith("Thread"):
                    t.join(timeout=1.0)

        for mock in (mock_post, mock_get):
            for url in _called_urls(mock):
                assert UPSTREAM_HOST not in url, (
                    f"lifespan issued outbound request to upstream stats host: {url}"
                )
