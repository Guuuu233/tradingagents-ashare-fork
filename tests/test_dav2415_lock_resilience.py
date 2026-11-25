from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import sqlite3
import sys
import threading
from types import SimpleNamespace
from urllib.parse import urlparse
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError
from sqlalchemy.orm import sessionmaker

from api import database, main
from api.db_retry import is_transient_sqlite_lock, retry_sqlite_lock


def test_sqlite_busy_timeout_is_applied_to_new_connections():
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


def test_transient_sqlite_lock_classification_unwraps_sqlalchemy_orig():
    original = sqlite3.OperationalError("database is locked")
    wrapped = SQLAlchemyOperationalError("statement", {}, original)
    assert is_transient_sqlite_lock(wrapped)

    readonly = sqlite3.OperationalError("attempt to write a readonly database")
    wrapped_readonly = SQLAlchemyOperationalError("statement", {}, readonly)
    assert not is_transient_sqlite_lock(wrapped_readonly)


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


def test_retry_sqlite_lock_recovers_real_sqlalchemy_session_lock(tmp_path, monkeypatch):
    db_path = tmp_path / "sqlalchemy-lock.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 0},
    )
    session_factory = sessionmaker(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("create table test_lock(id integer primary key)")

    attempts = []

    def release_lock_during_retry(_delay):
        attempts_before_wait = len(attempts)
        threading.Event().wait(timeout=0.2)
        if len(attempts) == attempts_before_wait:
            lock_connection.rollback()
            lock_connection.close()
            return True
        return False

    monkeypatch.setattr(
        "api.db_retry.time.sleep",
        release_lock_during_retry,
    )

    lock_connection = sqlite3.connect(
        db_path, timeout=0, check_same_thread=False
    )
    lock_connection.execute("BEGIN EXCLUSIVE")

    def operation():
        attempts.append(1)
        with session_factory() as session:
            session.execute(text("select count(*) from test_lock"))
            session.commit()
        return "saved"

    result = retry_sqlite_lock(
        operation,
        delays_seconds=(5,),
        operation_name="real SQLAlchemy lock test",
    )

    engine.dispose()
    assert len(attempts) == 2
    assert result == "saved"


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


def test_save_report_or_raise_retries_transient_sqlalchemy_lock(monkeypatch):
    attempts = []

    def save_callable():
        attempts.append(1)
        if len(attempts) < 2:
            raise SQLAlchemyOperationalError(
                "statement", {}, sqlite3.OperationalError("database is locked")
            )
        return "saved"

    monkeypatch.setattr(main, "_DB_FAILURE_RETRY_DELAYS_SECONDS", (5,))
    monkeypatch.setattr("api.db_retry.time.sleep", lambda _delay: None)

    async def scenario():
        return await main._save_report_or_raise(
            "job-save-retry", save_callable, stage="save"
        )

    assert asyncio.run(scenario()) is None
    assert len(attempts) == 2


def test_run_job_inner_failure_write_retries_transient_sqlalchemy_lock(monkeypatch):
    store = main.get_job_store()
    store.clear()
    attempts = []
    update_results = []

    def raise_inner_error(*_args, **_kwargs):
        raise RuntimeError("forced inner failure")

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    def mark_failed(_db, report_id, error):
        attempts.append((report_id, error))
        if len(attempts) < 2:
            raise SQLAlchemyOperationalError(
                "statement", {}, sqlite3.OperationalError("database is locked")
            )

    def update_failed(_db, report_id, **fields):
        update_results.append((report_id, fields["result_data"]))

    monkeypatch.setattr(
        main,
        "_resolve_and_freeze_custom_prompts",
        lambda _db, _user_id: ({}, False),
    )
    monkeypatch.setattr(main, "_build_runtime_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main, "_normalize_symbol", lambda symbol: symbol)
    monkeypatch.setattr(main, "_extract_request_user_context", lambda _request: {})
    monkeypatch.setattr(
        main,
        "AgentProgressTracker",
        lambda *_args, **_kwargs: SimpleNamespace(snapshot=lambda: {}),
    )
    monkeypatch.setattr(
        main,
        "resolve_analysis_horizons",
        lambda *_args, **_kwargs: SimpleNamespace(
            resolved=["short", "medium"],
            resolution_source="explicit",
            notice=None,
        ),
    )
    monkeypatch.setattr(main, "TradingAgentsGraph", raise_inner_error)
    monkeypatch.setattr(main, "get_db_ctx", _SessionContext)
    monkeypatch.setattr(main.report_service, "mark_report_failed", mark_failed)
    monkeypatch.setattr(main.report_service, "update_report_partial", update_failed)
    monkeypatch.setattr(main, "_DB_FAILURE_RETRY_DELAYS_SECONDS", (5,))
    monkeypatch.setattr("api.db_retry.time.sleep", lambda _delay: None)
    monkeypatch.setattr(main._shared_data_collector, "evict", lambda *_args, **_kwargs: None)

    request = SimpleNamespace(
        symbol="600519.SH",
        trade_date="2026-08-20",
        trade_date_explicit=True,
        horizons=["short", "medium"],
        selected_analysts=[],
        config_overrides={},
    )
    asyncio.run(
        main._run_job_inner(
            "job-inner-lock", request, stream_events=False, save_report=False
        )
    )

    assert len(attempts) == 2
    assert update_results[0][0] == "job-inner-lock"
    assert update_results[0][1]["status"] == "failed"


def test_chat_stream_failure_write_retries_and_logs(monkeypatch):
    store = main.get_job_store()
    store.clear()
    attempts = []
    logged = []

    async def extract_failure(*_args, **_kwargs):
        raise RuntimeError("intent extraction failed")

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    def mark_failed(_db, report_id, error):
        attempts.append((report_id, error))
        if len(attempts) < 2:
            raise SQLAlchemyOperationalError(
                "statement", {}, sqlite3.OperationalError("database is locked")
            )

    monkeypatch.setattr(
        main,
        "_build_runtime_config",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        main,
        "resolve_analysis_horizons",
        lambda *_args, **_kwargs: SimpleNamespace(
            resolved=["short"], resolution_source="explicit"
        ),
    )
    monkeypatch.setattr(
        main, "_ai_extract_symbol_and_date_streaming", extract_failure
    )
    monkeypatch.setattr(main, "get_db_ctx", _SessionContext)
    monkeypatch.setattr(main.report_service, "mark_report_failed", mark_failed)
    monkeypatch.setattr(main, "_DB_FAILURE_RETRY_DELAYS_SECONDS", (5,))
    monkeypatch.setattr("api.db_retry.time.sleep", lambda _delay: None)
    monkeypatch.setattr(main, "_log", logged.append)

    tasks = []

    def create_tracked_task(coro, **_kwargs):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(main, "_create_tracked_task", create_tracked_task)
    request = main.ChatCompletionRequest(
        messages=[main.ChatMessage(role="user", content="600519.SH")],
        stream=True,
    )
    current_user = SimpleNamespace(id="user-chat-lock")

    async def scenario():
        await main.chat_completions(request, current_user)
        await tasks[0]

    asyncio.run(scenario())

    assert len(attempts) == 2
    assert any("Failed to mark chat job" in line for line in logged) is False
    assert store.get_job(attempts[0][0])["status"] == "failed"


def test_settings_update_locks_return_503(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    db_path = tmp_path / "settings-lock.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 0},
    )
    database.Base.metadata.create_all(bind=engine)
    user = SimpleNamespace(id="user-settings-lock")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            insert into users(id, email, is_active)
            values ('user-settings-lock', 'settings-lock@example.test', 1)
            """
        )

    session_factory = sessionmaker(bind=engine)
    lock_connection = sqlite3.connect(db_path, timeout=0)
    lock_connection.execute("BEGIN EXCLUSIVE")

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[main.get_db] = override_get_db
    main.app.dependency_overrides[main._require_web_user] = lambda: user
    role_response = client.patch(
        "/v1/role-bindings", json={"bindings": []}
    )
    prompt_response = client.patch(
        "/v1/custom-prompts",
        json={
            "prompts": [
                {
                    "target_type": "global",
                    "target_key": "",
                    "prompt_text": "保持中文输出。",
                    "enabled": True,
                }
            ]
        },
    )
    lock_connection.rollback()
    lock_connection.close()
    main.app.dependency_overrides.clear()
    engine.dispose()

    assert role_response.status_code == 503
    assert prompt_response.status_code == 503
    assert role_response.json()["detail"] == "数据库暂时被占用，请稍后重试角色绑定保存"
    assert prompt_response.json()["detail"] == "数据库暂时被占用，请稍后重试自定义提示词保存"


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


class _DailyRunnerStubHandler(http.server.BaseHTTPRequestHandler):
    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._reply({"status": "ok", "commit_sha": "stub"})
        else:
            self._reply({"detail": "not found"}, status=404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        path = urlparse(self.path).path
        if path == "/v1/auth/request-code":
            self._reply({"dev_code": "stub-code"})
        elif path == "/v1/auth/verify-code":
            self._reply({
                "user": {"id": "429163f7-50b6-4982-8bdf-96ae99506843"},
                "access_token": "stub-token",
            })
        elif path == "/v1/analyze":
            self.server.analyze_attempts += 1
            if self.server.analyze_attempts < 3:
                self._reply(
                    {"detail": "locked"},
                    status=503 if self.server.analyze_attempts == 2 else 500,
                )
            else:
                self._reply({"job_id": "job-e2e"})
        else:
            self._reply({"detail": "not found"}, status=404)

    def log_message(self, *args):
        pass


class _DailyRunnerStubServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def test_daily_runner_main_retries_5xx_and_progress_lock(monkeypatch, tmp_path):
    import importlib.util

    root = tmp_path / "root"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "tradingagents.db"
    real_connect = sqlite3.connect
    with real_connect(db_path) as connection:
        connection.execute(
            "create table reports(id text primary key, status text)"
        )
        connection.execute(
            "insert into reports values ('job-e2e', 'completed')"
        )
        connection.commit()

    runner_path = Path(__file__).parents[1] / "work" / "daily-batch" / "run_daily.py"
    spec = importlib.util.spec_from_file_location("dav2415_runner_e2e", runner_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(json.dumps(["20261015", "20261016"]))
    runner_path_dir = tmp_path / "runner"
    runner_path_dir.mkdir()

    with _DailyRunnerStubServer(("127.0.0.1", 0), _DailyRunnerStubHandler) as server:
        server.analyze_attempts = 0
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        progress_reads = []

        def connect(*args, **kwargs):
            if server.analyze_attempts >= 3 and len(progress_reads) < 2:
                progress_reads.append(1)
                raise sqlite3.OperationalError("database is locked")
            if server.analyze_attempts >= 3:
                progress_reads.append(1)
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(runner, "CAL", str(calendar_path))
        monkeypatch.setattr(runner, "HERE", str(runner_path_dir))
        monkeypatch.setattr(runner, "GROUP_A", ["600519.SH"])
        monkeypatch.setattr(runner, "MAXC", 1)
        monkeypatch.setattr(runner, "STAGGER", 0)
        monkeypatch.setattr(runner, "ANALYZE_RETRY_DELAY_SECONDS", 0)
        monkeypatch.setattr(runner, "PROGRESS_RETRY_DELAYS_SECONDS", (0, 0, 0))
        monkeypatch.setattr(runner.sqlite3, "connect", connect)
        monkeypatch.setattr(runner.time, "sleep", lambda _delay: None)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_daily.py",
                "--date",
                "2026-10-15",
                "--base",
                base_url,
                "--root",
                str(root),
                "--account",
                "batch@example.test",
            ],
        )

        assert runner.main() == runner.EXIT_OK

        server.shutdown()
        thread.join(timeout=5)

    manifest_path = runner_path_dir / "2026-10-15" / "manifest.log"
    manifest = manifest_path.read_text(encoding="utf-8")
    assert "analyze retry=1 status=500" in manifest
    assert "analyze retry=2 status=503" in manifest
    assert "progress retry=1" in manifest
    assert "progress retry=2" in manifest
    assert "finished 600519.SH job-e2e completed" in manifest
