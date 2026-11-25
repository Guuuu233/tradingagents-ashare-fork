from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from api import database, main
from api.db_retry import is_transient_sqlite_lock, retry_sqlite_lock


def test_sqlite_busy_timeout_is_applied_to_new_connections(tmp_path):
    engine = database.engine
    with engine.connect() as connection:
        timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert timeout == database.SQLITE_BUSY_TIMEOUT_SECONDS * 1000


def test_transient_sqlite_lock_classification_is_narrow():
    assert is_transient_sqlite_lock(sqlite3.OperationalError("database is locked"))
    assert is_transient_sqlite_lock(sqlite3.OperationalError("database table is busy"))
    assert not is_transient_sqlite_lock(
        sqlite3.OperationalError("attempt to write a readonly database")
    )
    assert not is_transient_sqlite_lock(sqlite3.OperationalError("no such table: reports"))
    assert not is_transient_sqlite_lock(RuntimeError("database is locked"))


def test_retry_sqlite_lock_recovers_after_transient_errors(monkeypatch):
    attempts = []
    delays = []
    monkeypatch.setattr("api.db_retry.time.sleep", delays.append)

    def operation():
        attempts.append(1)
        if len(attempts) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "saved"

    assert retry_sqlite_lock(
        operation,
        delays_seconds=(5, 15, 30),
        operation_name="test operation",
    ) == "saved"
    assert delays == [5, 15]


def test_retry_sqlite_lock_does_not_retry_non_lock_errors(monkeypatch):
    attempts = []
    monkeypatch.setattr(
        "api.db_retry.time.sleep",
        lambda _delay: pytest.fail("must not sleep"),
    )

    def operation():
        attempts.append(1)
        raise sqlite3.OperationalError("attempt to write a readonly database")

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        retry_sqlite_lock(operation, delays_seconds=(5,), operation_name="test operation")
    assert len(attempts) == 1


def test_hard_timeout_failure_write_retries_transient_lock(monkeypatch):
    store = main.get_job_store()
    store.clear()
    requests = []
    update_results = []

    async def blocked_inner(*_args, **_kwargs):
        await asyncio.sleep(10)

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    def mark_failed(_db, report_id, error):
        requests.append((report_id, error))
        if len(requests) < 3:
            raise sqlite3.OperationalError("database is locked")

    def update_failed(_db, report_id, **fields):
        update_results.append((report_id, fields["result_data"]))

    monkeypatch.setattr(main, "_JOB_TIMEOUT", 0)
    monkeypatch.setattr(main, "_JOB_HARD_TIMEOUT", 0.01)
    monkeypatch.setattr(main, "_run_job_inner", blocked_inner)
    monkeypatch.setattr(main, "get_db_ctx", _SessionContext)
    monkeypatch.setattr(main.report_service, "mark_report_failed", mark_failed)
    monkeypatch.setattr(main.report_service, "update_report_partial", update_failed)
    monkeypatch.setattr(main, "_DB_FAILURE_RETRY_DELAYS_SECONDS", (5, 15, 30))
    monkeypatch.setattr("api.db_retry.time.sleep", lambda _delay: None)

    request = SimpleNamespace(
        symbol="600519.SH", trade_date="2026-08-20", horizons=["short", "medium"]
    )
    asyncio.run(main._run_job("job-dav2415", request, False, False, "user", "test"))

    assert len(requests) == 3
    assert len(update_results) == 1
    report_id, result = update_results[0]
    assert report_id == "job-dav2415"
    assert result["symbol"] == "600519.SH"
    assert result["trade_date"] == "2026-08-20"
    assert result["status"] == "failed"
    assert result["mode"] == "dual_horizon"
    assert result["horizon_status"] == {"short": "failed", "medium": "failed"}


def test_hard_timeout_failure_write_logs_non_lock_db_error(monkeypatch):
    store = main.get_job_store()
    store.clear()
    logged = []

    async def blocked_inner(*_args, **_kwargs):
        await asyncio.sleep(10)

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    def mark_failed(*_args, **_kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(main, "_JOB_TIMEOUT", 0)
    monkeypatch.setattr(main, "_JOB_HARD_TIMEOUT", 0.01)
    monkeypatch.setattr(main, "_run_job_inner", blocked_inner)
    monkeypatch.setattr(main, "get_db_ctx", _SessionContext)
    monkeypatch.setattr(main.report_service, "mark_report_failed", mark_failed)
    monkeypatch.setattr(main.report_service, "update_report_partial", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_DB_FAILURE_RETRY_DELAYS_SECONDS", (5,))
    monkeypatch.setattr(main, "_log", logged.append)

    request = SimpleNamespace(
        symbol="600519.SH", trade_date="2026-08-20", horizons=["short"]
    )
    asyncio.run(main._run_job("job-dav2415-readonly", request, False, False, "user", "test"))

    assert store.get_job("job-dav2415-readonly")["status"] == "failed"
    assert any("attempt to write a readonly database" in line for line in logged)


def test_daily_runner_retries_analyze_5xx_and_progress_locks(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path

    runner_path = Path(__file__).parents[1] / "work" / "daily-batch" / "run_daily.py"
    spec = importlib.util.spec_from_file_location("dav2415_run_daily", runner_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    post_results = [
        SimpleNamespace(status_code=500),
        SimpleNamespace(status_code=503),
        SimpleNamespace(status_code=200, json=lambda: {"job_id": "job-retry"}),
    ]
    progress_calls = []
    sleep_delays = []
    real_connect = sqlite3.connect

    monkeypatch.setattr(runner, "ANALYZE_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(runner, "PROGRESS_RETRY_DELAYS_SECONDS", (0, 0, 0))
    monkeypatch.setattr(
        runner.requests,
        "post",
        lambda *_args, **_kwargs: post_results.pop(0),
    )

    def connect(*_args, **_kwargs):
        if len(progress_calls) < 3:
            progress_calls.append(1)
            raise sqlite3.OperationalError("database is locked")
        progress_calls.append(1)
        connection = real_connect(":memory:")
        connection.execute("create table reports(id text primary key, status text)")
        connection.execute("insert into reports values ('job-retry', 'completed')")
        connection.commit()
        return connection

    monkeypatch.setattr(runner.sqlite3, "connect", connect)
    monkeypatch.setattr(runner.time, "sleep", sleep_delays.append)
    monkeypatch.setattr(runner, "DB", str(tmp_path / "unused.db"))

    log_lines = []

    def log(message):
        log_lines.append(message)

    response = runner.post_analyze_with_retries(
        "600519.SH", "2026-11-25", {}, log
    )
    assert response.json()["job_id"] == "job-retry"
    assert runner.read_report_status_with_retries(
        "600519.SH", "job-retry", log
    ) == "completed"

    assert sleep_delays == [0, 0, 0, 0, 0]
    assert any("analyze retry=1 status=500" in line for line in log_lines)
    assert any("analyze retry=2 status=503" in line for line in log_lines)
    assert any("progress retry=1" in line for line in log_lines)
    assert any("progress retry=2" in line for line in log_lines)
