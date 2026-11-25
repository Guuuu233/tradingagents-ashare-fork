"""Transient SQLite lock retry helpers."""

import logging
import sqlite3
import time
from typing import Callable, Iterable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
LOCK_MESSAGES = ("database is locked", "database table is busy")


def is_transient_sqlite_lock(exc: BaseException) -> bool:
    """Classify only genuine SQLite lock contention as retryable."""
    return isinstance(exc, sqlite3.OperationalError) and any(
        message in str(exc).lower() for message in LOCK_MESSAGES
    )


def retry_sqlite_lock(
    operation: Callable[[], T],
    *,
    delays_seconds: Iterable[float],
    operation_name: str,
) -> T:
    """Retry an operation only while SQLite reports transient lock contention."""
    for attempt, delay_seconds in enumerate(delays_seconds, start=1):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not is_transient_sqlite_lock(exc):
                raise
            logger.warning(
                "%s hit transient SQLite lock on attempt %d; retrying in %.1fs",
                operation_name,
                attempt,
                float(delay_seconds),
            )
            time.sleep(float(delay_seconds))
    return operation()
