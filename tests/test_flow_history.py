from __future__ import annotations

import sqlite3
from pathlib import Path

from dashboard.flow_history import FlowHistoryStore
from dashboard.paper_api import FLOW_RETENTION_SECONDS, PaperService


def seed_database(path: Path, *, now: int = 1_800_000_000) -> FlowHistoryStore:
    connection = sqlite3.connect(path)
    connection.executescript(
        """CREATE TABLE flow_trade_buckets (
            instrument TEXT NOT NULL,ts INTEGER NOT NULL,buy_notional REAL NOT NULL,
            sell_notional REAL NOT NULL,trade_count INTEGER NOT NULL,
            PRIMARY KEY(instrument,ts));
        CREATE TABLE oi_snapshots (
            instrument TEXT NOT NULL,ts INTEGER NOT NULL,oi REAL NOT NULL,source TEXT NOT NULL,
            PRIMARY KEY(instrument,ts));
        CREATE TABLE flow_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,oi REAL,cvd REAL,
            instrument TEXT,trade_count INTEGER,window_seconds INTEGER,last_trade_ts INTEGER);"""
    )
    for instrument, offset in (("BTC-USDT", 0), ("ETH-USDT", 1000)):
        for age in range(10 * 3600, -1, -60):
            timestamp = now - age
            connection.execute(
                "INSERT INTO flow_trade_buckets VALUES(?,?,?,?,?)",
                (instrument, timestamp, 20 + offset, 5, 3),
            )
        for age in range(10 * 3600, -1, -15):
            timestamp = now - age
            connection.execute(
                "INSERT INTO oi_snapshots VALUES(?,?,?,?)",
                (instrument, timestamp, 1_000_000 + offset + timestamp % 1000, "test"),
            )
    connection.commit()
    connection.close()
    store = FlowHistoryStore(path)
    store.initialize()
    store.backfill()
    return store


def aggregate_count(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM flow_history_aggregates"
        ).fetchone()[0]


def test_existing_sqlite_rows_survive_restart_and_old_range_is_returned(tmp_path):
    path = tmp_path / "flow.db"
    store = seed_database(path)
    first = store.query(
        "BTC-USDT",
        "cvd",
        start=1_800_000_000 - 9 * 3600,
        end=1_800_000_000 - 7 * 3600,
        now=1_800_000_000,
    )
    restarted = FlowHistoryStore(path)
    restarted.initialize()
    restarted.backfill()
    second = restarted.query(
        "BTC-USDT",
        "cvd",
        start=1_800_000_000 - 9 * 3600,
        end=1_800_000_000 - 7 * 3600,
        now=1_800_000_000,
    )
    assert first["points"] == second["points"]
    assert first["points"] and first["points"][0]["time"] < 1_800_000_000 - 6 * 3600


def test_current_empty_window_falls_back_without_hiding_older_history(tmp_path):
    store = seed_database(tmp_path / "flow.db")
    response = store.query(
        "BTC-USDT",
        "cvd",
        start=1_800_000_000 + 3600,
        end=1_800_000_000 + 7200,
        now=1_800_000_000 + 7200,
    )
    assert response["fallback"] is True
    assert response["has_history"] is True
    assert response["available_start"] < response["requested_start"]
    assert response["points"]


def test_pagination_is_deterministic_and_nonoverlapping(tmp_path):
    store = seed_database(tmp_path / "flow.db")
    recent = store.query(
        "BTC-USDT",
        "oi",
        start=1_800_000_000 - 2 * 3600,
        end=1_800_000_000,
        max_points=100,
        now=1_800_000_000,
    )
    older = store.query(
        "BTC-USDT",
        "oi",
        start=1_800_000_000 - 10 * 3600,
        end=1_800_000_000,
        max_points=100,
        cursor=recent["next_before_cursor"],
        now=1_800_000_000,
    )
    repeated = store.query(
        "BTC-USDT",
        "oi",
        start=1_800_000_000 - 10 * 3600,
        end=1_800_000_000,
        max_points=100,
        cursor=recent["next_before_cursor"],
        now=1_800_000_000,
    )
    assert older == repeated
    assert older["points"][-1]["time"] < recent["points"][0]["time"]


def test_downsampling_is_deterministic(tmp_path):
    store = seed_database(tmp_path / "flow.db")
    arguments = dict(
        instrument="BTC-USDT",
        series="oi",
        start=1_800_000_000 - 10 * 3600,
        end=1_800_000_000,
        max_points=12,
        now=1_800_000_000,
    )
    first = store.query(**arguments)
    second = store.query(**arguments)
    assert first["points"] == second["points"]
    assert first["returned_point_count"] <= 12
    assert first["resolution_seconds"] >= 3600


def test_cvd_aggregation_preserves_delta_and_global_cumulative_semantics(tmp_path):
    path = tmp_path / "flow.db"
    store = seed_database(path)
    response = store.query(
        "BTC-USDT",
        "cvd",
        start=1_800_000_000 - 600,
        end=1_800_000_000,
        max_points=100,
        now=1_800_000_000,
    )
    assert all(point["delta"] == 15 for point in response["points"])
    assert response["points"][-1]["value"] > response["points"][0]["value"]
    with sqlite3.connect(path) as connection:
        expected = connection.execute(
            """SELECT SUM(buy_notional-sell_notional)
               FROM flow_trade_buckets WHERE instrument='BTC-USDT'"""
        ).fetchone()[0]
    assert response["points"][-1]["value"] == expected


def test_oi_uses_last_confirmed_value_and_retains_min_max(tmp_path):
    path = tmp_path / "flow.db"
    store = seed_database(path)
    response = store.query(
        "BTC-USDT",
        "oi",
        start=1_800_000_000 - 600,
        end=1_800_000_000,
        max_points=2,
        now=1_800_000_000,
    )
    last = response["points"][-1]
    with sqlite3.connect(path) as connection:
        expected = connection.execute(
            """SELECT oi FROM oi_snapshots WHERE instrument='BTC-USDT'
               AND ts>=? AND ts<? ORDER BY ts DESC LIMIT 1""",
            (last["time"], last["time"] + response["resolution_seconds"]),
        ).fetchone()[0]
    assert last["value"] == expected
    assert last["min"] <= last["value"] <= last["max"]


def test_missing_periods_are_gaps_not_zeroes(tmp_path):
    path = tmp_path / "flow.db"
    store = seed_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """DELETE FROM flow_trade_buckets
               WHERE instrument='BTC-USDT' AND ts>=? AND ts<?""",
            (1_800_000_000 - 3600, 1_800_000_000 - 3000),
        )
        connection.execute(
            """DELETE FROM flow_history_migrations WHERE series='cvd'
               AND instrument='BTC-USDT'"""
        )
        connection.commit()
    store.backfill(force=True)
    response = store.query(
        "BTC-USDT",
        "cvd",
        start=1_800_000_000 - 2 * 3600,
        end=1_800_000_000,
        max_points=200,
        now=1_800_000_000,
    )
    assert response["has_gaps"] is True
    assert not any(point["value"] == 0 for point in response["points"])


def test_aggregate_backfill_is_idempotent(tmp_path):
    store = seed_database(tmp_path / "flow.db")
    before = aggregate_count(store.db_path)
    store.backfill(force=True)
    once = aggregate_count(store.db_path)
    store.backfill(force=True)
    twice = aggregate_count(store.db_path)
    assert before == once == twice


def test_raw_pruning_does_not_delete_durable_aggregates(tmp_path, monkeypatch):
    path = tmp_path / "paper.db"
    service = PaperService(path)
    old = 1_800_000_000 - FLOW_RETENTION_SECONDS - 60
    with service._connect() as connection:
        connection.execute(
            "INSERT INTO flow_trade_buckets VALUES(?,?,?,?,?)",
            ("BTC-USDT", old, 10, 2, 1),
        )
    service.flow_history.backfill(force=True)
    before = aggregate_count(path)
    monkeypatch.setattr("dashboard.paper_api.time.time", lambda: 1_800_000_000)
    service._prune_flow_retention()
    with sqlite3.connect(path) as connection:
        raw = connection.execute(
            "SELECT COUNT(*) FROM flow_trade_buckets WHERE ts=?", (old,)
        ).fetchone()[0]
    assert raw == 0
    assert aggregate_count(path) == before


def test_temporary_collector_failure_does_not_erase_history(tmp_path):
    store = seed_database(tmp_path / "flow.db")
    before = store.query(
        "BTC-USDT",
        "oi",
        start=1_800_000_000 - 3600,
        end=1_800_000_000,
        now=1_800_000_000,
    )
    after = store.query(
        "BTC-USDT",
        "oi",
        start=1_800_000_000 - 3600,
        end=1_800_000_000 + 600,
        now=1_800_000_000 + 600,
    )
    assert after["has_history"] is True
    assert after["stale"] is True
    assert after["points"] == before["points"]


def test_instruments_are_isolated(tmp_path):
    store = seed_database(tmp_path / "flow.db")
    btc = store.query(
        "BTC-USDT",
        "oi",
        start=1_800_000_000 - 600,
        end=1_800_000_000,
        now=1_800_000_000,
    )
    eth = store.query(
        "ETH-USDT",
        "oi",
        start=1_800_000_000 - 600,
        end=1_800_000_000,
        now=1_800_000_000,
    )
    assert btc["instrument"] == "BTC-USDT"
    assert eth["instrument"] == "ETH-USDT"
    assert btc["points"] != eth["points"]


def test_versioned_contract_includes_complete_coverage_metadata(tmp_path):
    store = seed_database(tmp_path / "flow.db")
    response = store.query(
        "BTC-USDT",
        "cvd",
        start=1_800_000_000 - 3600,
        end=1_800_000_000,
        now=1_800_000_000,
    )
    assert response["api_version"] == "flow-history-v1"
    assert {
        "instrument",
        "series",
        "requested_start",
        "requested_end",
        "available_start",
        "available_end",
        "latest_timestamp",
        "raw_row_count",
        "returned_point_count",
        "resolution",
        "stale",
        "has_history",
        "has_more_before",
        "has_more_after",
        "next_before_cursor",
        "source",
        "retention_policy_version",
    } <= response.keys()
