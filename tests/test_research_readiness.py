from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dashboard import research_readiness as rr
from scripts import check_microstructure_research_readiness as cli


DAY = rr.DAY_MS


def _schema(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE cvd_aggregates(
                instrument TEXT, resolution TEXT, bucket_ms INTEGER,
                delta REAL, gap_flag INTEGER DEFAULT 0);
            CREATE TABLE oi_aggregates(
                instrument TEXT, resolution TEXT, bucket_ms INTEGER,
                last_value REAL, gap_flag INTEGER DEFAULT 0);
            CREATE TABLE funding_settled(
                instrument TEXT, funding_time_ms INTEGER, funding_rate REAL,
                state TEXT DEFAULT 'confirmed');
            CREATE TABLE basis_aggregates(
                instrument TEXT, resolution TEXT, bucket_ms INTEGER,
                last_basis_pct REAL, gap_flag INTEGER DEFAULT 0);
            CREATE TABLE collection_gaps(
                lane TEXT, instrument TEXT, start_ms INTEGER, end_ms INTEGER,
                reason TEXT, classification TEXT, severity TEXT,
                resolved_at_ms INTEGER);
            """
        )


@pytest.fixture
def compact_policy(monkeypatch):
    sources = rr.READINESS_CONFIG["sources"]
    for source in sources.values():
        monkeypatch.setitem(source, "frequency_ms", DAY)
        monkeypatch.setitem(source, "freshness_ms", 2 * DAY)
    monkeypatch.setitem(
        rr.READINESS_CONFIG["native_independent_events_min"], "CVD", 30)
    monkeypatch.setitem(
        rr.READINESS_CONFIG["native_independent_events_min"], "OI", 30)
    monkeypatch.setitem(rr.READINESS_CONFIG["label"], "horizon_ms", DAY)
    monkeypatch.setitem(rr.READINESS_CONFIG["label"], "non_overlapping_min", 29)
    monkeypatch.setitem(rr.READINESS_CONFIG["label"], "overlap_min", 0.95)


def _insert_days(
    path: Path, table: str, count: int, *, start: int = 0,
    step: int = DAY, value=None, gap_days: set[int] | None = None,
) -> None:
    gap_days = gap_days or set()
    with sqlite3.connect(path) as connection:
        for index in range(count):
            if index in gap_days:
                continue
            timestamp = start + index * step
            if table == "cvd_aggregates":
                connection.execute(
                    "INSERT INTO cvd_aggregates VALUES(?,?,?,?,0)",
                    ("BTC-USDT-SWAP", "1m", timestamp,
                     float(index if value is None else value)))
            elif table == "oi_aggregates":
                item_value = float(index if value is None else value)
                connection.execute(
                    "INSERT INTO oi_aggregates VALUES(?,?,?,?,0)",
                    ("BTC-USDT-SWAP", "5m", timestamp, item_value))


def _evaluate(path: Path, group: str, as_of: int) -> dict:
    return rr.evaluate_readiness(
        path, instruments=["BTC"], feature_groups=[group], as_of_ms=as_of
    )["results"][0]


def test_natural_span_over_30_days_but_usable_days_is_not_ready(
    tmp_path, compact_policy
):
    database = tmp_path / "sparse.db"
    _schema(database)
    _insert_days(database, "cvd_aggregates", 20, step=2 * DAY)
    row = _evaluate(database, "CVD", 38 * DAY)
    assert row["natural_coverage_days"] > 30
    assert row["gap_adjusted_usable_days"] < 30
    assert row["status"] != rr.READY_PENDING


def test_continuous_interval_below_30_days_is_not_ready(
    tmp_path, compact_policy
):
    database = tmp_path / "split.db"
    _schema(database)
    _insert_days(database, "cvd_aggregates", 40, gap_days={20})
    row = _evaluate(database, "CVD", 40 * DAY)
    assert row["gap_adjusted_usable_days"] >= 30
    assert row["max_continuous_usable_days"] < 30
    assert row["status"] != rr.READY_PENDING


def test_recent_coverage_below_threshold_is_not_ready(tmp_path, compact_policy):
    database = tmp_path / "old.db"
    _schema(database)
    _insert_days(database, "cvd_aggregates", 35)
    row = _evaluate(database, "CVD", 40 * DAY)
    assert row["recent_30d_coverage"] < 0.95
    assert "RECENT_30D_COVERAGE_BELOW_THRESHOLD" in row["blocking_reasons"]


def test_critical_gap_blocks(tmp_path, compact_policy):
    database = tmp_path / "gap.db"
    _schema(database)
    _insert_days(database, "cvd_aggregates", 32)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO collection_gaps VALUES(?,?,?,?,?,?,?,NULL)",
            ("trades", "BTC-USDT-SWAP", 10 * DAY, 11 * DAY,
             "live watchdog", "CRITICAL_LIVE_GAP", "critical"))
    row = _evaluate(database, "CVD", 31 * DAY)
    assert row["status"] == "BLOCKED_CRITICAL_GAP"
    assert row["unresolved_critical_gap_count"] == 1


def test_insufficient_independent_events_blocks(tmp_path, compact_policy):
    database = tmp_path / "events.db"
    _schema(database)
    _insert_days(database, "oi_aggregates", 32, value=100)
    row = _evaluate(database, "OI", 31 * DAY)
    assert row["native_independent_event_count"] == 1
    assert "INDEPENDENT_EVENTS_BELOW_THRESHOLD" in row["blocking_reasons"]


def test_stale_source_blocks(tmp_path, compact_policy):
    database = tmp_path / "stale.db"
    _schema(database)
    _insert_days(database, "cvd_aggregates", 32)
    row = _evaluate(database, "CVD", 40 * DAY)
    assert row["status"] == "STALE_SOURCE"


def test_all_thresholds_only_wait_for_human_approval(tmp_path, compact_policy):
    database = tmp_path / "ready.db"
    _schema(database)
    _insert_days(database, "cvd_aggregates", 32)
    row = _evaluate(database, "CVD", 31 * DAY)
    assert row["status"] == rr.READY_PENDING
    assert row["automatic_actions"] == []


def test_evaluator_does_not_create_research_tasks(tmp_path, compact_policy):
    database = tmp_path / "read_only.db"
    _schema(database)
    _insert_days(database, "cvd_aggregates", 32)
    before = database.read_bytes()
    payload = rr.evaluate_readiness(
        database, instruments=["BTC"], feature_groups=["CVD"], as_of_ms=31 * DAY)
    assert database.read_bytes() == before
    assert payload["side_effects"]["research_jobs_created"] == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='research_jobs'"
        ).fetchone() is None


def test_dataset_identity_changes_when_native_values_change(
    tmp_path, compact_policy
):
    database = tmp_path / "identity.db"
    _schema(database)
    _insert_days(database, "cvd_aggregates", 32)
    first = _evaluate(database, "CVD", 31 * DAY)["dataset_identity"]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE cvd_aggregates SET delta=delta+1 WHERE bucket_ms=?",
            (10 * DAY,))
    second = _evaluate(database, "CVD", 31 * DAY)["dataset_identity"]
    assert second != first


def test_cli_repeated_run_is_idempotent(tmp_path, monkeypatch):
    database = tmp_path / "empty.db"
    output = tmp_path / "readiness.json"
    previous = tmp_path / "previous.json"
    _schema(database)
    fixed = {
        "schema_version": rr.RESEARCH_READINESS_VERSION,
        "evaluated_at": "fixed",
        "results": [{
            "feature_group": "CVD", "instrument": "BTC",
            "status": "COLLECTING", "blocking_reasons": ["missing"],
        }],
    }
    previous.write_text(json.dumps(fixed), encoding="utf-8")
    monkeypatch.setattr(cli, "evaluate_readiness", lambda *a, **k: fixed)
    assert cli.main([
        "--database", str(database), "--output-json", str(output),
        "--previous-result", str(previous)]) == cli.EXIT_COLLECTING
    first = output.read_bytes()
    assert cli.main([
        "--database", str(database), "--output-json", str(output),
        "--previous-result", str(previous)]) == cli.EXIT_COLLECTING
    assert output.read_bytes() == first
    assert not output.with_name("readiness.notification.json").exists()


def test_notification_only_contains_real_status_changes():
    previous = {"results": [
        {"feature_group": "CVD", "instrument": "BTC", "status": "COLLECTING"}]}
    unchanged = {"schema_version": rr.RESEARCH_READINESS_VERSION, "results": [
        {"feature_group": "CVD", "instrument": "BTC", "status": "COLLECTING"}]}
    changed = {"schema_version": rr.RESEARCH_READINESS_VERSION, "results": [
        {"feature_group": "CVD", "instrument": "BTC",
         "status": "APPROACHING_READINESS"}]}
    assert cli._notification(previous, unchanged) is None
    notification = cli._notification(previous, changed)
    assert notification is not None
    assert notification["changes"] == [{
        "key": "CVD|BTC", "previous_status": "COLLECTING",
        "current_status": "APPROACHING_READINESS"}]
    assert notification["sent"] is False


def test_forward_filled_oi_does_not_add_events_and_missing_stays_missing(
    tmp_path, compact_policy
):
    database = tmp_path / "forward_fill.db"
    _schema(database)
    _insert_days(database, "oi_aggregates", 10, value=100)
    _insert_days(database, "oi_aggregates", 1, start=10 * DAY, value=101)
    row = _evaluate(database, "OI", 10 * DAY)
    assert row["raw_observation_count"] == 11
    assert row["native_independent_event_count"] == 2
    assert row["gap_adjusted_usable_days"] == 11
    # There are no invented rows between native buckets.
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM oi_aggregates").fetchone()[0] == 11


def test_module_has_no_strategy_or_order_api_dependency():
    source = Path(rr.__file__).read_text(encoding="utf-8").lower()
    assert "strategy_api" not in source
    assert "order_api" not in source
    assert "factor_autoresearch" not in source
