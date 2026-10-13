"""Tests for scheduled-task persistence and graceful scheduler shutdown.

Covers the "service restart must not lose in-progress tasks" acceptance
criterion for task 3:

- ``_claim_pending_tasks`` persists the running job's id/timestamps so a crash
  leaves a recoverable trace.
- ``_recover_stale_tasks`` re-queues tasks that were genuinely in-flight when
  the previous process died, instead of silently dropping them.
- ``_drain_inflight_jobs`` waits for in-flight jobs on SIGTERM and only
  cancels after a bounded timeout (the cancelled job is re-queued on restart).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, ReportDB, ScheduledAnalysisDB
from scheduler import main as scheduler_main


# ── Heartbeat worker-thread join (DAV-1934) ─────────────────────────────────
#
# ``_run_job_heartbeat`` writes from ``asyncio.to_thread`` worker threads.
# Cancelling the heartbeat task makes ``await hb`` return as soon as the task
# observes the cancellation, while the worker thread is still inside its DB
# write; ``asyncio.run`` only joins the default executor *after* the coroutine
# (and therefore the DB session) is gone.  The heartbeat tests therefore join
# the in-flight heartbeat writes explicitly, so the shutdown order is
# cancel -> join -> close the session.  Without the join, the worker's COMMIT
# and the session close's ROLLBACK interleaved on one shared DBAPI connection
# (``sqlite3.OperationalError: cannot rollback - no transaction is active``).

_heartbeat_writes_lock = threading.Lock()
_heartbeat_writes_inflight = 0
_heartbeat_writes_idle = threading.Event()
_heartbeat_writes_idle.set()


def _heartbeat_write_started() -> None:
    """Record a heartbeat DB write entering the isolated DB from a worker thread."""
    global _heartbeat_writes_inflight
    with _heartbeat_writes_lock:
        _heartbeat_writes_inflight += 1
        _heartbeat_writes_idle.clear()


def _heartbeat_write_finished() -> None:
    global _heartbeat_writes_inflight
    with _heartbeat_writes_lock:
        _heartbeat_writes_inflight = max(_heartbeat_writes_inflight - 1, 0)
        if _heartbeat_writes_inflight == 0:
            _heartbeat_writes_idle.set()


def _reset_heartbeat_write_tracking() -> None:
    """Start every isolated DB from a clean idle state."""
    global _heartbeat_writes_inflight
    with _heartbeat_writes_lock:
        _heartbeat_writes_inflight = 0
        _heartbeat_writes_idle.set()


def join_heartbeat_writes(timeout: float = 10.0) -> None:
    """Block until no heartbeat DB write is in flight (DAV-1934).

    Called right after the heartbeat task is cancelled so the test session is
    closed only once the worker thread's write has finished.  The worker's
    last DB action is the session close at the end of ``_touch``, so waiting
    for the write to drain is the join point that removes the interleave.
    """
    if not _heartbeat_writes_idle.wait(timeout=timeout):
        with _heartbeat_writes_lock:
            inflight = _heartbeat_writes_inflight
        raise AssertionError(
            f"heartbeat DB writes still in flight after {timeout}s (inflight={inflight})"
        )


@contextmanager
def _isolated_db():
    """Yield a session on an isolated per-test SQLite DB file, with
    ``scheduler.main.get_db_ctx`` patched to open sessions on the same engine.

    Keeps scheduler persistence tests from seeing rows other tests created in
    the shared conftest database.

    The DB is a file behind the default connection pool — one DBAPI connection
    per checkout — instead of an in-memory DB behind ``StaticPool``, which
    handed the *same* connection to every session.  The heartbeat under test
    writes from ``asyncio.to_thread`` worker threads, and that single shared
    connection let the worker's COMMIT interleave with the test session's
    ROLLBACK (``sqlite3.OperationalError: cannot rollback - no transaction is
    active``, DAV-1934).  ``check_same_thread`` stays off, as in
    ``api/database.py``, so a pooled connection may be reused by a later
    worker thread.
    """
    _reset_heartbeat_write_tracking()
    db_dir = tempfile.mkdtemp(prefix="ta-sched-test-")
    db_path = os.path.join(db_dir, "scheduler.db")
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Session = sessionmaker(bind=engine)

    class _Ctx:
        def __enter__(self):
            self._session = Session()
            if self is not outer:
                _heartbeat_write_started()
            return self._session

        def __exit__(self, exc_type, exc_val, exc_tb):
            if self is not outer:
                _heartbeat_write_finished()
            if self is outer:
                # Join any in-flight heartbeat write before closing the session.
                join_heartbeat_writes()
            if exc_type is not None:
                self._session.rollback()
            self._session.close()

    outer = _Ctx()
    try:
        Base.metadata.create_all(engine)
        with patch("scheduler.main.get_db_ctx", _Ctx):
            with outer as session:
                yield session
    finally:
        engine.dispose()
        shutil.rmtree(db_dir, ignore_errors=True)


def _add_scheduled(
    db,
    *,
    last_run_status: str | None = None,
    last_run_date: str | None = None,
    last_job_id: str | None = None,
    last_job_heartbeat_at: datetime | None = None,
    last_report_id: str | None = None,
    is_active: bool = True,
) -> str:
    task_id = uuid4().hex
    db.add(
        ScheduledAnalysisDB(
            id=task_id,
            user_id=uuid4().hex,
            symbol="600519.SH",
            horizon="short",
            trigger_time="20:00",
            is_active=is_active,
            last_run_date=last_run_date,
            last_run_status=last_run_status,
            last_job_id=last_job_id,
            last_job_heartbeat_at=last_job_heartbeat_at,
            last_report_id=last_report_id,
        )
    )
    db.commit()
    return task_id


def _add_completed_report(db, report_id: str, run_date: str) -> None:
    db.add(
        ReportDB(
            id=report_id,
            user_id=uuid4().hex,
            symbol="600519.SH",
            trade_date=run_date,
            status="completed",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


# ── Claiming persists the running job's identity ────────────────────────────

def test_claim_pending_task_persists_job_id():
    with _isolated_db() as db:
        task_id = _add_scheduled(db)
        snapshots = scheduler_main._claim_pending_tasks("2026-08-04", "21:00")
        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap["id"] == task_id
        assert snap["job_id"]

        row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
        assert row is not None
        assert row.last_run_status == "running"
        assert row.last_run_date == "2026-08-04"
        assert row.last_job_id == snap["job_id"]
        assert row.last_job_started_at is not None
        assert row.last_job_heartbeat_at is not None


def test_claim_pending_task_returns_empty_when_none():
    with _isolated_db() as db:
        # A task that already ran today must not be claimed twice.
        _add_scheduled(db, last_run_status="success", last_run_date="2026-08-04")
        assert scheduler_main._claim_pending_tasks("2026-08-04", "21:00") == []


# ── Startup recovery re-queues in-flight tasks ──────────────────────────────

def test_recover_requeues_interrupted_running_task():
    """A task that was mid-analysis when the process died is re-queued, not
    dropped, so today's scheduled run is not lost."""
    with _isolated_db() as db:
        task_id = _add_scheduled(
            db,
            last_run_status="running",
            last_run_date="2026-08-04",
            last_job_id="deadbeef",
        )
        scheduler_main._recover_stale_tasks()

        row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
        assert row is not None
        assert row.last_run_status is None  # back to pending
        assert row.last_run_date is None  # eligible again today
        assert row.last_job_id is None  # persisted trace cleared
        assert row.last_job_started_at is None


def test_recover_marks_success_when_report_completed():
    """If the analysis actually finished (report persisted) but the process died
    before writing the success marker, recovery must not re-queue it."""
    with _isolated_db() as db:
        report_id = uuid4().hex
        task_id = _add_scheduled(
            db,
            last_run_status="running",
            last_run_date="2026-08-04",
            last_job_id="deadbeef",
            last_report_id=report_id,
        )
        _add_completed_report(db, report_id, "2026-08-04")

        scheduler_main._recover_stale_tasks()

        row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
        assert row.last_run_status == "success"


def test_recover_marks_legacy_running_without_job_id_stale():
    """Pre-persistence rows (no last_job_id) keep the legacy stale reset."""
    with _isolated_db() as db:
        task_id = _add_scheduled(
            db,
            last_run_status="running",
            last_run_date="2026-08-04",
            last_job_id=None,
        )
        scheduler_main._recover_stale_tasks()

        row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
        assert row.last_run_status == "stale"
        assert row.last_run_date is None


def test_recover_requeues_fresh_heartbeat_running_task():
    """A running task with a recent heartbeat was genuinely in-flight when the
    process died — re-queue it so the scheduled run is not lost."""
    with _isolated_db() as db:
        task_id = _add_scheduled(
            db,
            last_run_status="running",
            last_run_date="2026-08-04",
            last_job_id="deadbeef",
            last_job_heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        scheduler_main._recover_stale_tasks()

        row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
        assert row is not None
        assert row.last_run_status is None  # back to pending
        assert row.last_run_date is None  # eligible again today
        assert row.last_job_id is None  # persisted trace cleared
        assert row.last_job_started_at is None
        assert row.last_job_heartbeat_at is None


def test_recover_marks_stale_when_heartbeat_abandoned():
    """A running task whose heartbeat is older than the stale threshold was
    abandoned, not crashed mid-analysis — mark it stale instead of re-queueing."""
    with _isolated_db() as db:
        task_id = _add_scheduled(
            db,
            last_run_status="running",
            last_run_date="2026-08-04",
            last_job_id="deadbeef",
            last_job_heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=6),
        )
        scheduler_main._recover_stale_tasks()

        row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
        assert row is not None
        assert row.last_run_status == "stale"
        assert row.last_run_date is None
        assert row.last_job_id is None


def test_heartbeat_abandoned_helper():
    """_heartbeat_is_abandoned treats missing heartbeats as not abandoned and
    compares naive/aware timestamps against the stale threshold in UTC."""
    now = datetime.now(timezone.utc)
    assert scheduler_main._heartbeat_is_abandoned(None) is False
    assert scheduler_main._heartbeat_is_abandoned(now - timedelta(seconds=30)) is False
    assert (
        scheduler_main._heartbeat_is_abandoned(now - timedelta(hours=2)) is True
    )
    # SQLite returns naive datetimes; they represent UTC in this codebase.
    assert (
        scheduler_main._heartbeat_is_abandoned(
            (now - timedelta(hours=2)).replace(tzinfo=None)
        )
        is True
    )


def test_recover_uses_shanghai_date_boundary_for_report_match():
    """created_at is UTC; a report created in the first UTC hours of a Shanghai
    day must still match the Shanghai run date (AGENTS.md 3.5)."""
    with _isolated_db() as db:
        report_id = uuid4().hex
        task_id = _add_scheduled(
            db,
            last_run_status="running",
            last_run_date="2026-08-04",
            last_job_id="deadbeef",
            last_report_id=report_id,
        )
        # 2026-08-04 01:30 Shanghai == 2026-08-03 17:30 UTC (previous UTC day).
        db.add(
            ReportDB(
                id=report_id,
                user_id=uuid4().hex,
                symbol="600519.SH",
                trade_date="2026-08-04",
                status="completed",
                created_at=datetime(2026, 8, 3, 17, 30, 0),
            )
        )
        db.commit()

        scheduler_main._recover_stale_tasks()

        row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
        assert row.last_run_status == "success"


# ── Graceful shutdown drains in-flight jobs ─────────────────────────────────

def test_heartbeat_writes_last_job_heartbeat_at():
    """While a job runs, the heartbeat persists liveness so startup recovery
    can tell an in-flight task from an abandoned one."""
    async def scenario():
        with _isolated_db() as db:
            task_id = _add_scheduled(db)
            job_id = "job-heartbeat"
            row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
            row.last_job_id = job_id
            db.commit()

            saved_interval = scheduler_main.SCHEDULER_HEARTBEAT_SECONDS
            scheduler_main.SCHEDULER_HEARTBEAT_SECONDS = 0.01
            try:
                hb = asyncio.create_task(scheduler_main._run_job_heartbeat(task_id, job_id))
                await asyncio.sleep(0.06)
                hb.cancel()
                try:
                    await hb
                except asyncio.CancelledError:
                    pass
                # DAV-1934: ``await hb`` returns while the worker thread may
                # still be inside the heartbeat write; join it before the
                # session below is closed.
                join_heartbeat_writes()
            finally:
                scheduler_main.SCHEDULER_HEARTBEAT_SECONDS = saved_interval

            row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
            assert row.last_job_heartbeat_at is not None

    asyncio.run(scenario())


def test_heartbeat_skips_when_job_no_longer_matches():
    """A stale heartbeat must not write once the row is claimed by a new job."""
    async def scenario():
        with _isolated_db() as db:
            task_id = _add_scheduled(db)
            row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
            row.last_job_id = "new-job-id"
            db.commit()

            saved_interval = scheduler_main.SCHEDULER_HEARTBEAT_SECONDS
            scheduler_main.SCHEDULER_HEARTBEAT_SECONDS = 0.01
            try:
                hb = asyncio.create_task(scheduler_main._run_job_heartbeat(task_id, "stale-job-id"))
                await asyncio.sleep(0.06)
                hb.cancel()
                try:
                    await hb
                except asyncio.CancelledError:
                    pass
                # DAV-1934: join the in-flight heartbeat write before closing
                # the session below.
                join_heartbeat_writes()
            finally:
                scheduler_main.SCHEDULER_HEARTBEAT_SECONDS = saved_interval

            row = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == task_id).first()
            assert row.last_job_heartbeat_at is None

    asyncio.run(scenario())


def test_drain_waits_for_inflight_jobs_to_complete():
    async def scenario():
        scheduler_main._background_tasks.clear()
        completed: list[str] = []
        release = asyncio.Event()

        async def fake_job():
            await release.wait()
            completed.append("done")

        task = asyncio.create_task(fake_job())
        scheduler_main._background_tasks.add(task)
        try:
            drain = asyncio.create_task(scheduler_main._drain_inflight_jobs(timeout_seconds=5))
            await asyncio.sleep(0.05)
            # Drain is still waiting; the in-flight job has not been cancelled.
            assert not drain.done()
            assert task.cancelled() is False

            release.set()
            await drain

            assert completed == ["done"]
            assert task.done() and not task.cancelled()
        finally:
            scheduler_main._background_tasks.discard(task)
            if not task.done():
                task.cancel()

    asyncio.run(scenario())


def test_drain_cancels_remaining_jobs_on_timeout():
    async def scenario():
        scheduler_main._background_tasks.clear()

        async def forever():
            await asyncio.sleep(100)

        task = asyncio.create_task(forever())
        scheduler_main._background_tasks.add(task)
        try:
            await scheduler_main._drain_inflight_jobs(timeout_seconds=0.05)
            assert task.cancelled()
        finally:
            scheduler_main._background_tasks.discard(task)

    asyncio.run(scenario())


def test_scheduler_loop_exits_when_stop_event_set():
    async def scenario():
        stop = asyncio.Event()
        stop.set()
        # A stopped scheduler must return promptly — bound the wait so a future
        # regression that ignores the stop event fails instead of hanging.
        await asyncio.wait_for(scheduler_main._scheduler_loop(stop), timeout=2.0)
        assert stop.is_set()

    asyncio.run(scenario())
