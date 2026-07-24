"""Regression tests for the corrected Phase 6D coverage pipeline."""

from __future__ import annotations

import json

import pytest

from dashboard.microstructure import (
    MICROSTRUCTURE_SOURCE_VERSION,
    MicrostructureStore,
    normalize_epoch_ms,
)
from dashboard.microstructure_backfill import OfficialBackfill
from dashboard.microstructure_research import SourceSpecificEventStudy


DAY = 86_400_000
NOW = 1_780_000_000_000


@pytest.fixture
def store(tmp_path) -> MicrostructureStore:
    result = MicrostructureStore(tmp_path / "microstructure_6d.db")
    result.initialize()
    return result


def insert_oi(store: MicrostructureStore, instrument: str, timestamp: int) -> None:
    store.insert_oi(
        instrument, timestamp, oi_contracts=1.0, oi_currency=None, oi_usd=100.0,
        source="test OI", source_identity=f"{instrument}:{timestamp}")


def insert_funding(store: MicrostructureStore, instrument: str, timestamp: int) -> None:
    store.insert_funding(
        instrument, {"fundingTime": timestamp, "fundingRate": "0.0001"},
        settled=True)


def insert_basis(store: MicrostructureStore, instrument: str, timestamp: int) -> None:
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO basis_aggregates VALUES(
               ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (instrument, "1H", timestamp, 1.0, 1.0, 1.0, 1.0,
             0.001, 0.001, 0.0, 1, timestamp, timestamp, 0,
             MICROSTRUCTURE_SOURCE_VERSION))


def insert_mark(store: MicrostructureStore, instrument: str, timestamp: int,
                close: float = 100.0) -> None:
    store.insert_price(
        "mark", instrument, timestamp, close,
        source_identity=f"{instrument}:{timestamp}")


def test_phase6c_historical_oi_is_not_zero_coverage(store: MicrostructureStore) -> None:
    insert_oi(store, "BTC-USDT", NOW - 7 * DAY)
    insert_oi(store, "BTC-USDT-SWAP", NOW)
    item = store.per_feature_eligibility()["feature_groups"]["oi"]["instruments"][
        "BTC-USDT-SWAP"]
    assert item["source_observation_count"] == 2
    assert item["source_usable_days"] == 7.0


def test_settled_funding_is_independent_of_collector_restart(
        store: MicrostructureStore) -> None:
    insert_funding(store, "BTC-USDT-SWAP", NOW - 60 * DAY)
    insert_funding(store, "BTC-USDT-SWAP", NOW)
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO collection_gaps VALUES(
               'funding_settled','BTC-USDT-SWAP',?,?,?, ?,NULL)""",
            (NOW - 30 * DAY, NOW - 29 * DAY, "collector restart", NOW))
    item = store.per_feature_eligibility()["feature_groups"]["settled_funding"][
        "instruments"]["BTC-USDT-SWAP"]
    assert item["source_usable_days"] == 60.0
    assert item["source_data_status"] == "FORMAL_RESEARCH_READY"


def test_historical_basis_not_reduced_to_recent_live_segment(
        store: MicrostructureStore) -> None:
    insert_basis(store, "BTC-USDT-SWAP", NOW - 60 * DAY)
    insert_basis(store, "BTC-USDT-SWAP", NOW)
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO collection_gaps VALUES(
               'mark','BTC-USDT-SWAP',?,?,?, ?,NULL)""",
            (NOW - DAY, NOW - DAY + 300_000, "collector restart", NOW))
    item = store.per_feature_eligibility()["feature_groups"]["basis"]["instruments"][
        "BTC-USDT-SWAP"]
    assert item["source_usable_days"] == 60.0


def test_seconds_and_milliseconds_are_normalized_at_ingestion(
        store: MicrostructureStore) -> None:
    seconds = NOW // 1000
    insert_oi(store, "BTC-USDT", seconds)
    insert_oi(store, "BTC-USDT", NOW + 1_000)
    with store.connect(readonly=True) as connection:
        timestamps = [row[0] for row in connection.execute(
            "SELECT source_ts_ms FROM oi_observations ORDER BY source_ts_ms")]
    assert timestamps == [NOW, NOW + 1_000]
    assert normalize_epoch_ms(seconds) == NOW
    with pytest.raises(ValueError):
        normalize_epoch_ms(12345)


def test_instrument_normalization_for_swap_and_index_symbols(
        store: MicrostructureStore) -> None:
    insert_oi(store, "BTC-USDT", NOW)
    store.insert_price(
        "index", "BTC-USDT-SWAP", NOW, 100.0,
        source_identity="btc-index")
    with store.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT instrument FROM oi_observations").fetchone()[0] == "BTC-USDT-SWAP"
        assert connection.execute(
            "SELECT instrument FROM index_price_observations").fetchone()[0] == "BTC-USDT"


def test_source_and_event_study_status_are_separate(
        store: MicrostructureStore) -> None:
    insert_funding(store, "BTC-USDT-SWAP", NOW - 60 * DAY)
    insert_funding(store, "BTC-USDT-SWAP", NOW)
    insert_mark(store, "BTC-USDT-SWAP", NOW - DAY)
    insert_mark(store, "BTC-USDT-SWAP", NOW)
    item = store.per_feature_eligibility()["feature_groups"]["settled_funding"][
        "instruments"]["BTC-USDT-SWAP"]
    assert item["source_data_status"] == "FORMAL_RESEARCH_READY"
    assert item["event_study_status"] == "EXPLORATORY_ONLY"


def test_collector_health_can_skip_expensive_research_eligibility(
        store: MicrostructureStore) -> None:
    health = store.health(include_eligibility=False)
    assert "per_feature_eligibility" not in health


def test_event_study_uses_every_genuine_overlapping_mark(
        store: MicrostructureStore) -> None:
    study = SourceSpecificEventStudy(store)
    marks: dict[int, float] = {}
    observations = []
    for index in range(12):
        timestamp = NOW + index * 2 * 3_600_000
        observations.append((timestamp, float(index)))
        marks[timestamp] = 100.0 + index
        marks[timestamp + 900_000] = 100.5 + index
    result = study._study_features(
        "overlap_test", observations, dict(sorted(marks.items())),
        "BTC-USDT-SWAP")
    assert result["15m"]["event_count"] == 12


def test_missing_labels_are_not_interpolated(store: MicrostructureStore) -> None:
    study = SourceSpecificEventStudy(store)
    marks = {
        NOW: 100.0,
        NOW + 900_000 + 120_000: 101.0,
    }
    assert study._forward_return(marks, NOW, 900_000) is None


class FakePublicClient:
    def __init__(self) -> None:
        self.retries = 0
        self.failed_requests = 0
        self.trade_params: list[dict[str, object]] = []

    def get_public(self, path: str, params: dict[str, object]):
        if path.endswith("/instruments"):
            return [{"ctType": "linear", "ctVal": "0.01"}]
        self.trade_params.append(dict(params))
        if params.get("after") == "resume-cursor":
            return [{
                "instId": "BTC-USDT-SWAP", "tradeId": "older-cursor",
                "px": "100", "sz": "1", "side": "buy",
                "ts": str(NOW - 1_000),
            }]
        return []


def prepared_backfill(store: MicrostructureStore) -> tuple[OfficialBackfill, FakePublicClient]:
    store.checkpoint(
        "trades", "BTC-USDT-SWAP", cursor="resume-cursor",
        last_source_ts_ms=NOW, status="BATCH_LIMIT_REACHED")
    client = FakePublicClient()
    return OfficialBackfill(store, client), client


def test_cvd_backfill_cursor_moves_monotonically_backward(
        store: MicrostructureStore) -> None:
    backfill, _ = prepared_backfill(store)
    result = backfill.backfill_trades("BTC-USDT-SWAP", max_pages=1)
    assert result["cursor_before"] == "resume-cursor"
    assert result["cursor"] == "older-cursor"
    assert result["earliest_ms"] < NOW


def test_cvd_resume_does_not_restart_from_newest_trades(
        store: MicrostructureStore) -> None:
    backfill, client = prepared_backfill(store)
    backfill.backfill_trades("BTC-USDT-SWAP", max_pages=1)
    assert client.trade_params[0]["after"] == "resume-cursor"


def test_duplicate_backfill_page_is_idempotent(store: MicrostructureStore) -> None:
    backfill, _ = prepared_backfill(store)
    first = backfill.backfill_trades("BTC-USDT-SWAP", max_pages=1)
    store.checkpoint(
        "trades", "BTC-USDT-SWAP", cursor="resume-cursor",
        last_source_ts_ms=NOW, status="BATCH_LIMIT_REACHED")
    second = backfill.backfill_trades("BTC-USDT-SWAP", max_pages=1)
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["duplicate_rows_ignored"] == 1
    with store.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM trade_flow_observations").fetchone()[0] == 1


def test_event_study_results_persistence(store: MicrostructureStore) -> None:
    study = SourceSpecificEventStudy(store)
    study._save_result("test_feature", "1H", {"test": "payload"}, 42)
    with store.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT * FROM event_study_results WHERE report_id=?",
            (study.report_id,)).fetchone()
    assert row["event_count"] == 42
    assert json.loads(row["payload_json"]) == {"test": "payload"}
