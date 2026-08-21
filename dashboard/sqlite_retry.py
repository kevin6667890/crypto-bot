"""Bounded retry for transient SQLite writer contention."""
from __future__ import annotations

import sqlite3
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def is_transient_lock(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower() for marker in ("database is locked", "database is busy")
    )


def retry_locked(operation: Callable[[], T], *, attempts: int = 8,
                 initial_delay_seconds: float = 0.05,
                 sleep: Callable[[float], None] = time.sleep) -> T:
    """Retry a short, idempotent SQLite transaction with bounded backoff."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            if not is_transient_lock(error) or attempt + 1 >= attempts:
                raise
            sleep(min(1.0, initial_delay_seconds * (2 ** attempt)))
    raise AssertionError("unreachable")
