"""Tests for the ``active_analysis_count`` field exposed by /healthz (DAV-1466).

Covers:
  - JobStore.active_job_count() on InMemoryJobStore and RedisJobStore
    (pending / queued / running count; completed / failed do not).
  - /healthz and /api/health include the field on both the 200 and the
    thread-pool-starved 503 branches.
  - A counting failure degrades to ``active_analysis_count: null`` plus
    ``active_analysis_count_error`` instead of breaking the probe.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import uuid
from unittest.mock import patch

import pytest
import redis
from fastapi.testclient import TestClient

from api.job_store import InMemoryJobStore

# ---------------------------------------------------------------------------
# Redis backend selection (same pattern as tests/test_job_store_redis.py)
# ---------------------------------------------------------------------------

_REDIS_TEST_URL = os.environ.get("REDIS_TEST_URL", "redis://localhost:6379/15")


def _real_redis_available() -> bool:
    try:
        r = redis.Redis.from_url(_REDIS_TEST_URL, decode_responses=True)
        r.ping()
        r.close()
        return True
    except Exception:
        return False


def _fakeredis_available() -> bool:
    try:
        importlib.import_module("fakeredis")
        return True
    except ImportError:
        return False


REAL_REDIS_AVAILABLE = _real_redis_available()
FAKEREDIS_AVAILABLE = _fakeredis_available()

_needs_redis_backend = pytest.mark.skipif(
    not (REAL_REDIS_AVAILABLE or FAKEREDIS_AVAILABLE),
    reason="Neither real Redis at " + _REDIS_TEST_URL + " nor fakeredis is available",
)


def _seed(store, statuses, tag=""):
    for i, status in enumerate(statuses):
        store.set_job(f"{tag}j{i}", status=status)


# ---------------------------------------------------------------------------
# InMemoryJobStore.active_job_count
# ---------------------------------------------------------------------------

class TestInMemoryActiveJobCount:
    def test_empty_store_returns_zero(self):
        assert InMemoryJobStore().active_job_count() == 0

    def test_non_terminal_statuses_counted(self):
        store = InMemoryJobStore()
        _seed(store, ["pending", "queued", "running"])
        assert store.active_job_count() == 3

    def test_terminal_statuses_excluded(self):
        store = InMemoryJobStore()
        _seed(store, ["pending", "running", "completed", "failed"])
        assert store.active_job_count() == 2

    def test_job_without_status_counts_as_active(self):
        # A job that was created (e.g. initial metadata write) but has not yet
        # been marked running is still in-flight work.
        store = InMemoryJobStore()
        store.set_job("j0", symbol="AAPL")
        assert store.active_job_count() == 1

    def test_transition_to_terminal_drops_count(self):
        store = InMemoryJobStore()
        store.set_job("j1", status="running")
        assert store.active_job_count() == 1
        store.set_job("j1", status="completed")
        assert store.active_job_count() == 0

    def test_delete_job_drops_count(self):
        store = InMemoryJobStore()
        store.set_job("j1", status="running")
        store.delete_job("j1")
        assert store.active_job_count() == 0


# ---------------------------------------------------------------------------
# RedisJobStore.active_job_count
# ---------------------------------------------------------------------------

@_needs_redis_backend
class TestRedisActiveJobCount:
    @pytest.fixture()
    def store(self, monkeypatch):
        from api.job_store_redis import RedisJobStore

        prefix = f"ta_test:{uuid.uuid4().hex[:8]}:"
        if not REAL_REDIS_AVAILABLE:
            import fakeredis

            monkeypatch.setattr(redis, "Redis", fakeredis.FakeRedis)
        s = RedisJobStore(_REDIS_TEST_URL, prefix=prefix)
        yield s
        s.clear()
        s._r.close()

    def test_empty_store_returns_zero(self, store):
        assert store.active_job_count() == 0

    def test_non_terminal_statuses_counted(self, store):
        _seed(store, ["pending", "queued", "running"])
        assert store.active_job_count() == 3

    def test_terminal_statuses_excluded(self, store):
        _seed(store, ["pending", "running", "completed", "failed"])
        assert store.active_job_count() == 2

    def test_job_without_status_counts_as_active(self, store):
        store.set_job("j0", symbol="AAPL")
        assert store.active_job_count() == 1

    def test_transition_to_terminal_drops_count(self, store):
        store.set_job("j1", status="running")
        assert store.active_job_count() == 1
        store.set_job("j1", status="completed")
        assert store.active_job_count() == 0


# ---------------------------------------------------------------------------
# /healthz field exposure
# ---------------------------------------------------------------------------

def _get_client() -> TestClient:
    from api.main import app

    return TestClient(app, raise_server_exceptions=False)


class TestHealthzActiveAnalysisCount:
    @pytest.fixture(autouse=True)
    def _isolate_store(self):
        """Swap the module-level job store for a fresh InMemoryJobStore."""
        from api import main as main_mod

        original = main_mod._job_store_instance
        store = InMemoryJobStore()
        main_mod._job_store_instance = store
        try:
            yield store
        finally:
            main_mod._job_store_instance = original

    def test_healthz_zero_when_no_jobs(self):
        client = _get_client()
        try:
            r = client.get("/healthz")
        finally:
            client.close()
        assert r.status_code == 200
        assert r.json()["active_analysis_count"] == 0

    def test_healthz_counts_non_terminal_jobs(self):
        from api.main import get_job_store

        _seed(get_job_store(), ["pending", "queued", "running"], tag="a_")
        _seed(get_job_store(), ["completed", "failed"], tag="t_")
        client = _get_client()
        try:
            r = client.get("/api/health")
        finally:
            client.close()
        assert r.status_code == 200
        assert r.json()["active_analysis_count"] == 3

    def test_healthz_503_branch_still_carries_field(self):
        from api.main import get_job_store

        _seed(get_job_store(), ["running"])

        async def _starve(coro, timeout):
            raise asyncio.TimeoutError()

        with patch("api.main.asyncio.wait_for", side_effect=_starve):
            client = _get_client()
            try:
                r = client.get("/healthz")
            finally:
                client.close()
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "thread_pool_starved"
        assert body["active_analysis_count"] == 1

    def test_healthz_count_error_yields_null_and_error_field(self):
        from api import main as main_mod

        broken = InMemoryJobStore()

        def _boom():
            raise RuntimeError("store exploded")

        broken.active_job_count = _boom
        main_mod._job_store_instance = broken
        client = _get_client()
        try:
            r = client.get("/healthz")
        finally:
            client.close()
        assert r.status_code == 200
        body = r.json()
        assert body["active_analysis_count"] is None
        assert "store exploded" in body["active_analysis_count_error"]
