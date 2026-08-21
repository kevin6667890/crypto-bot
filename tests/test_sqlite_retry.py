from __future__ import annotations

import sqlite3

from dashboard.sqlite_retry import retry_locked


def test_transient_sqlite_lock_is_retried_with_bounded_backoff():
    calls: list[int] = []
    sleeps: list[float] = []

    def write() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "persisted"

    assert retry_locked(write, attempts=4, initial_delay_seconds=.01, sleep=sleeps.append) == "persisted"
    assert len(calls) == 3 and sleeps == [.01, .02]


def test_non_lock_sqlite_error_is_not_retried():
    calls: list[int] = []
    try:
        retry_locked(lambda: (calls.append(1), (_ for _ in ()).throw(sqlite3.OperationalError("no such table")))[1])
    except sqlite3.OperationalError as error:
        assert "no such table" in str(error)
    else:
        assert False
    assert len(calls) == 1
