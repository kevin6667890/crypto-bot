from __future__ import annotations

import sqlite3
from pathlib import Path

from dashboard.flow_history import CanonicalFlowHistoryStore
from dashboard.microstructure import (
    MICROSTRUCTURE_SOURCE_VERSION,
    MicrostructureStore,
    identity,
)
from dashboard.microstructure_gap_repair import AggregateGapRepair


MIDNIGHT = 1_800_057_600_000
START = MIDNIGHT - 86_400_000
END = MIDNIGHT + 900_000
INSTRUMENT = "ETH-USDT-SWAP"


def seeded_store(path: Path) -> MicrostructureStore:
    store = MicrostructureStore(path)
    store.initialize()
    trades = []
    oi = []
    ingested = START
    for index, timestamp in enumerate(range(START, END, 60_000)):
        side = "buy" if index % 2 else "sell"
        source_id = f"trade-{index}"
        base = (
            "test official trades",
            MICROSTRUCTURE_SOURCE_VERSION,
            INSTRUMENT,
            timestamp,
            ingested,
            "trade",
            "confirmed",
            source_id,
            identity("trade", source_id),
        )
        trades.append((*base, source_id, side, 100.0, 1.0, 1.0, 100.0, None))
        oi_id = f"oi-{index}"
        oi_base = (
            "test official OI",
            MICROSTRUCTURE_SOURCE_VERSION,
            INSTRUMENT,
            timestamp,
            ingested,
            "snapshot",
            "confirmed",
            oi_id,
            identity("oi", oi_id),
        )
        oi.append((*oi_base, 1_000 + index, None, 10_000 + index, None))
    with store.connect() as connection:
        connection.executemany(
            "INSERT INTO trade_flow_observations VALUES("
            + ",".join("?" for _ in range(16)) + ")",
            trades,
        )
        connection.executemany(
            "INSERT INTO oi_observations VALUES("
            + ",".join("?" for _ in range(13)) + ")",
            oi,
        )
    store.aggregate_all()
    return store


def test_canonical_cvd_gap_is_whitespace_then_partial_until_utc_reset(tmp_path):
    path = tmp_path / "micro.db"
    seeded_store(path)
    missing = MIDNIGHT - 120_000
    with sqlite3.connect(path) as connection:
        connection.execute(
            """DELETE FROM cvd_aggregates
               WHERE instrument=? AND resolution='1m' AND bucket_ms=?""",
            (INSTRUMENT, missing),
        )
    result = CanonicalFlowHistoryStore(path).query(
        "ETH-USDT", "cvd",
        start=(MIDNIGHT - 180_000) // 1000,
        end=(MIDNIGHT + 120_000) // 1000,
        max_points=100,
        now=(MIDNIGHT + 120_000) // 1000,
    )
    by_time = {point["time"]: point for point in result["points"]}
    assert by_time[missing // 1000]["status"] == "WHITESPACE"
    assert by_time[missing // 1000]["gap_reason"] == "AGGREGATION_GAP"
    assert by_time[(MIDNIGHT - 60_000) // 1000]["status"] == "PARTIAL_AFTER_GAP"
    assert by_time[MIDNIGHT // 1000]["status"] == "VALID"
    assert by_time[MIDNIGHT // 1000]["value"] == by_time[MIDNIGHT // 1000]["delta"]


def test_canonical_oi_recovers_at_first_confirmed_point(tmp_path):
    path = tmp_path / "micro.db"
    seeded_store(path)
    missing = MIDNIGHT - 60_000
    with sqlite3.connect(path) as connection:
        connection.execute(
            """DELETE FROM oi_aggregates
               WHERE instrument=? AND resolution='1m' AND bucket_ms=?""",
            (INSTRUMENT, missing),
        )
    result = CanonicalFlowHistoryStore(path).query(
        "ETH-USDT", "oi",
        start=(MIDNIGHT - 120_000) // 1000,
        end=(MIDNIGHT + 60_000) // 1000,
        max_points=100,
        now=(MIDNIGHT + 60_000) // 1000,
    )
    by_time = {point["time"]: point for point in result["points"]}
    assert by_time[missing // 1000]["status"] == "WHITESPACE"
    assert by_time[MIDNIGHT // 1000]["status"] == "VALID"
    assert by_time[MIDNIGHT // 1000]["partial_after_gap"] is False


def test_higher_resolution_requires_every_constituent_minute(tmp_path):
    path = tmp_path / "micro.db"
    seeded_store(path)
    missing = MIDNIGHT - 120_000
    with sqlite3.connect(path) as connection:
        connection.execute(
            """DELETE FROM cvd_aggregates
               WHERE instrument=? AND resolution='1m' AND bucket_ms=?""",
            (INSTRUMENT, missing),
        )
    result = CanonicalFlowHistoryStore(path).query(
        "ETH-USDT-SWAP", "cvd",
        start=(MIDNIGHT - 900_000) // 1000,
        end=(MIDNIGHT + 900_000) // 1000,
        max_points=3,
        now=(MIDNIGHT + 900_000) // 1000,
    )
    assert result["resolution"] == "15m"
    by_time = {point["time"]: point for point in result["points"]}
    assert by_time[(MIDNIGHT - 900_000) // 1000]["status"] == "WHITESPACE"
    assert by_time[MIDNIGHT // 1000]["status"] == "VALID"
    assert result["has_gaps"] is True


def test_canonical_partial_range_matches_full_range_values(tmp_path):
    path = tmp_path / "micro.db"
    seeded_store(path)
    history = CanonicalFlowHistoryStore(path)
    full = history.query(
        "ETH-USDT", "cvd",
        start=MIDNIGHT // 1000,
        end=(MIDNIGHT + 120_000) // 1000,
        max_points=100,
        now=(MIDNIGHT + 120_000) // 1000,
    )
    partial = history.query(
        "ETH-USDT", "cvd",
        start=(MIDNIGHT + 60_000) // 1000,
        end=(MIDNIGHT + 120_000) // 1000,
        max_points=100,
        now=(MIDNIGHT + 120_000) // 1000,
    )
    full_by_time = {point["time"]: point for point in full["points"]}
    assert partial["canonical_instrument"] == INSTRUMENT
    for point in partial["points"]:
        assert point == full_by_time[point["time"]]


def test_repair_is_bounded_deterministic_and_idempotent(tmp_path):
    path = tmp_path / "micro.db"
    seeded_store(path)
    missing = MIDNIGHT - 120_000
    with sqlite3.connect(path) as connection:
        connection.execute(
            """DELETE FROM cvd_aggregates
               WHERE instrument=? AND resolution='1m' AND bucket_ms=?""",
            (INSTRUMENT, missing),
        )
    repair = AggregateGapRepair(path)
    dry = repair.diagnose(
        INSTRUMENT, "cvd", MIDNIGHT - 180_000, MIDNIGHT + 120_000
    )
    assert dry["recoverable_bucket_count"] == 1
    first = repair.rebuild(
        INSTRUMENT, "cvd", MIDNIGHT - 180_000, MIDNIGHT + 120_000,
        max_rows=100,
    )
    second = repair.rebuild(
        INSTRUMENT, "cvd", MIDNIGHT - 180_000, MIDNIGHT + 120_000,
        max_rows=100,
    )
    assert first["inserted"] >= 1
    assert first["verified_recoverable_bucket_count"] == 0
    assert second["inserted"] == 0
    assert second["updated"] == 0


def test_raw_gap_remains_explicit_and_is_never_synthesized(tmp_path):
    path = tmp_path / "micro.db"
    seeded_store(path)
    missing = MIDNIGHT - 120_000
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM trade_flow_observations WHERE instrument=? "
            "AND source_ts_ms>=? AND source_ts_ms<?",
            (INSTRUMENT, missing, missing + 60_000),
        )
        connection.execute(
            "DELETE FROM cvd_aggregates WHERE instrument=? "
            "AND resolution='1m' AND bucket_ms=?",
            (INSTRUMENT, missing),
        )
    repair = AggregateGapRepair(path)
    report = repair.rebuild(
        INSTRUMENT, "cvd", MIDNIGHT - 180_000, MIDNIGHT + 120_000,
        max_rows=100,
    )
    assert report["unrecoverable_bucket_count"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cvd_aggregates WHERE instrument=? "
            "AND resolution='1m' AND bucket_ms=?",
            (INSTRUMENT, missing),
        ).fetchone()[0] == 0
        reason = connection.execute(
            "SELECT reason FROM collection_gaps WHERE instrument=? "
            "AND start_ms=?",
            (INSTRUMENT, missing),
        ).fetchone()[0]
    assert reason == "UNRECOVERABLE_RAW_GAP"


def test_tool_source_has_no_vacuum_or_strategy_execution():
    source = Path("scripts/diagnose_and_repair_microstructure_gap.py").read_text()
    lowered = source.lower()
    assert "vacuum" not in lowered
    assert "backtest" not in lowered
    assert "strategy" not in lowered
    assert "synthetic" not in lowered
