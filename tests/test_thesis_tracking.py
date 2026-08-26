from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from dashboard.thesis_event_engine import ThesisSpecV1, compile_thesis
from dashboard.thesis_tracking import (
    CURRENT_EVALUATION_POLICY_VERSION,
    CurrentFeatureEvaluatorV1,
    ThesisTrackingRepositoryV1, ThesisTrackingSchedulerV1, ThesisTrackingServiceV1,
    TrackingError,
    evaluation_delta,
)


WIDTH = 14_400
BASE = 1_700_006_400  # UTC-aligned 4H open.


def candles(count: int = 220, *, latest_volume: float = 200.0,
            gap_before_last: int | None = None) -> list[dict]:
    rows = []
    offset = 0
    for index in range(count):
        if gap_before_last is not None and index == count - gap_before_last:
            offset += 1
        ts = BASE + (index + offset) * WIDTH
        rows.append({
            "ts": ts, "candle_close_ts": ts + WIDTH,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "volume": latest_volume if index == count - 1 else 100.0,
            "confirmed": True, "source": "live-fixture-v1",
            "source_version": "fixture-source-v1", "_source_store": "market_candles",
        })
    return rows


def spec(*, feature: str = "VOLUME_RATIO", operator: str = "gte",
         value: float | bool = 1.2) -> dict:
    return {
        "version": "thesis-spec-v1", "instrument": "BTC", "timeframe": "4H",
        "required_conditions": [{"feature": feature, "operator": operator, "value": value}],
        "optional_conditions": [], "forward_horizons": ["4H"],
        "requested_as_of": 1_750_000_000,
    }


def track(*, track_id: str = "track-1", spec_value: dict | None = None) -> dict:
    raw_spec = spec_value or spec()
    thesis_spec = ThesisSpecV1.from_dict(raw_spec)
    definition = compile_thesis(thesis_spec)
    return {
        "schema_version": "tracked-thesis-v1", "track_id": track_id,
        "thesis_spec": deepcopy(raw_spec),
        "compiled_definition": definition.to_dict(),
        "definition_hash": definition.definition_hash,
        "historical_result_hash": "a" * 64,
        "historical_dataset_identity": "historical-dataset-immutable",
        "historical_baseline": {
            "result_hash": "a" * 64,
            "historical_dataset_identity": "historical-dataset-immutable",
            "historical_summary": {"independent_event_count": 346},
        },
        "current_evaluation_policy_version": CURRENT_EVALUATION_POLICY_VERSION,
        "is_active": True, "status": "WATCHING",
    }


class Reader:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def candles(self, instrument: str, timeframe: str, as_of: int, limit: int) -> list[dict]:
        assert instrument == "BTC-USDT"
        assert timeframe == "4H"
        return deepcopy(self.rows[-limit:])


def evaluate(rows: list[dict], value: dict | None = None, *, now: int | None = None) -> dict:
    current_now = now if now is not None else rows[-1]["candle_close_ts"] + 1
    return CurrentFeatureEvaluatorV1(Reader(rows)).evaluate(track(spec_value=value), now=current_now)


def test_current_three_valued_logic_matching_and_not_matching() -> None:
    matched = evaluate(candles(latest_volume=200.0))
    assert matched["overall_status"] == "MATCHING"
    assert matched["conditions"][0]["state"] == "TRUE"
    assert matched["current_dataset_identity"]["version"] == "current-canonical-dataset-v1"
    assert matched["current_dataset_identity"]["dataset_id"] != track()["historical_dataset_identity"]

    not_matched = evaluate(candles(latest_volume=50.0))
    assert not_matched["overall_status"] == "NOT_MATCHING"
    assert not_matched["conditions"][0]["state"] == "FALSE"


def test_recent_gap_and_warmup_are_unknown_partial_but_old_gap_is_ignored() -> None:
    recent = evaluate(candles(gap_before_last=10))
    assert recent["overall_status"] == "PARTIAL"
    assert recent["conditions"][0]["state"] == "UNKNOWN"
    assert recent["conditions"][0]["quality"] == "PARTIAL"

    old = evaluate(candles(gap_before_last=100))
    assert old["overall_status"] == "MATCHING"
    assert old["conditions"][0]["state"] == "TRUE"


def test_stale_is_unknown_and_never_false() -> None:
    rows = candles()
    result = evaluate(rows, now=rows[-1]["candle_close_ts"] + WIDTH * 2 + 1)
    assert result["overall_status"] == "STALE"
    assert result["conditions"][0]["state"] == "UNKNOWN"
    assert result["conditions"][0]["quality"] == "STALE"


def test_unconfirmed_latest_candle_is_never_consumed() -> None:
    rows = candles()
    forming = {**rows[-1], "ts": rows[-1]["ts"] + WIDTH,
               "candle_close_ts": rows[-1]["candle_close_ts"] + WIDTH,
               "volume": 10_000.0, "confirmed": False}
    result = evaluate([*rows, forming], now=forming["candle_close_ts"] + 1)
    assert result["source_candle_timestamp"] == rows[-1]["candle_close_ts"]
    assert result["current_dataset_identity"]["latest_confirmed_candle"] == rows[-1]["candle_close_ts"]


def test_frozen_or_historical_only_latest_row_cannot_pose_as_current() -> None:
    rows = candles()
    for row in rows:
        row["_source_store"] = "historical_candles"
    result = evaluate(rows)
    assert result["overall_status"] == "BLOCKED"
    assert result["current_dataset_identity"] is None
    assert result["limitations"] == ["LATEST_CANDLE_IS_NOT_FROM_CURRENT_LIVE_CANONICAL_STORE"]


def test_feature_version_mismatch_blocks_without_silent_upgrade() -> None:
    value = track()
    value["compiled_definition"] = deepcopy(value["compiled_definition"])
    value["compiled_definition"]["feature_versions"]["VOLUME_RATIO"] = "future-version"
    rows = candles()
    result = CurrentFeatureEvaluatorV1(Reader(rows)).evaluate(
        value, now=rows[-1]["candle_close_ts"] + 1)
    assert result["overall_status"] == "BLOCKED_VERSION_MISMATCH"
    assert result["conditions"] == []


@pytest.mark.parametrize("field,value", [
    ("current_evaluation_policy_version", "future-policy-v99"),
    ("schema_version", "tracked-thesis-v99"),
])
def test_track_or_evaluation_policy_version_mismatch_blocks(
        field: str, value: str) -> None:
    tracked = track()
    tracked[field] = value
    rows = candles()
    result = CurrentFeatureEvaluatorV1(Reader(rows)).evaluate(
        tracked, now=rows[-1]["candle_close_ts"] + 1)
    assert result["overall_status"] == "BLOCKED_VERSION_MISMATCH"
    assert result["conditions"] == []
    assert result["limitations"] == ["TRACK_OR_EVALUATION_POLICY_VERSION_MISMATCH"]


def test_repository_create_is_idempotent_and_historical_identity_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "tracking.sqlite3"
    repository = ThesisTrackingRepositoryV1(path)
    first, created = repository.create(track(track_id="first"))
    duplicate = track(track_id="duplicate")
    duplicate["historical_baseline"] = {"forged": True}
    second, duplicate_created = repository.create(duplicate)
    assert created is True and duplicate_created is False
    assert second == first

    # Reopening the process-level repository preserves the exact baseline.
    reopened = ThesisTrackingRepositoryV1(path)
    persisted = reopened.get("first")
    assert persisted is not None
    assert persisted["historical_dataset_identity"] == "historical-dataset-immutable"
    assert persisted["historical_baseline"] == first["historical_baseline"]


def test_same_candle_is_noop_but_fresh_to_stale_is_one_material_transition(tmp_path: Path) -> None:
    repository = ThesisTrackingRepositoryV1(tmp_path / "tracking.sqlite3")
    stored_track, _ = repository.create(track())
    rows = candles()
    evaluator = CurrentFeatureEvaluatorV1(Reader(rows))
    close = rows[-1]["candle_close_ts"]

    fresh = evaluator.evaluate(stored_track, now=close + 1)
    first, first_created = repository.record_evaluation(fresh)
    repeated, repeated_created = repository.record_evaluation(
        evaluator.evaluate(stored_track, now=close + 2))
    assert first_created is True and repeated_created is False
    assert repeated["evaluation_id"] == first["evaluation_id"]

    stale = evaluator.evaluate(stored_track, now=close + WIDTH * 2 + 1)
    stale_stored, stale_created = repository.record_evaluation(stale)
    assert stale_created is True
    assert stale_stored["delta"]["status_changed"] is True
    assert stale_stored["delta"]["quality_changes"][0] == {
        "feature": "VOLUME_RATIO", "from": "AVAILABLE", "to": "STALE"}
    assert repository.get("track-1")["status"] == "STALE"


def test_existing_history_restores_snapshot_after_other_evaluator_replaced_it(tmp_path: Path) -> None:
    import json
    import sqlite3

    path = tmp_path / "tracking.sqlite3"
    repository = ThesisTrackingRepositoryV1(path)
    stored_track, _ = repository.create(track())
    rows = candles()
    evaluator = CurrentFeatureEvaluatorV1(Reader(rows))
    close = rows[-1]["candle_close_ts"]
    first = evaluator.evaluate(stored_track, now=close + 1)
    repository.record_evaluation(first)
    replacement = {**first, "evaluation_version": "temporary-other-evaluator",
                   "overall_status": "BLOCKED_VERSION_MISMATCH"}
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE thesis_current_snapshots SET idempotency_key=?,evaluation_json=?,delta_json=? WHERE track_id=?",
            ("temporary-snapshot-key", json.dumps(replacement), "{}", "track-1"),
        )
    restored, created = repository.record_evaluation(
        evaluator.evaluate(stored_track, now=close + 2))
    assert created is False
    assert restored["overall_status"] == first["overall_status"]
    detail = repository.detail("track-1")
    assert detail["latest_evaluation"]["overall_status"] == first["overall_status"]
    assert len(detail["evaluation_history"]) == 1


def test_new_candle_without_material_change_updates_snapshot_not_history(tmp_path: Path) -> None:
    repository = ThesisTrackingRepositoryV1(tmp_path / "tracking.sqlite3")
    stored_track, _ = repository.create(track())
    rows = candles(latest_volume=200.0)
    evaluator = CurrentFeatureEvaluatorV1(Reader(rows))
    first = evaluator.evaluate(stored_track, now=rows[-1]["candle_close_ts"] + 1)
    assert repository.record_evaluation(first)[1] is True

    prior = rows[-1]
    next_open = prior["ts"] + WIDTH
    rows.append({**prior, "ts": next_open, "candle_close_ts": next_open + WIDTH})
    second = CurrentFeatureEvaluatorV1(Reader(rows)).evaluate(
        stored_track, now=next_open + WIDTH + 1)
    latest, history_created = repository.record_evaluation(second)
    assert history_created is False
    assert latest["source_candle_timestamp"] == next_open + WIDTH
    detail = repository.detail("track-1")
    assert detail["latest_evaluation"]["source_candle_timestamp"] == next_open + WIDTH
    assert len(detail["evaluation_history"]) == 1


def test_future_tracking_database_schema_is_blocked(tmp_path: Path) -> None:
    path = tmp_path / "tracking.sqlite3"
    import sqlite3
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE thesis_tracking_schema(singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO thesis_tracking_schema VALUES(1,99)")
    reopened = ThesisTrackingRepositoryV1(path)
    assert reopened.readiness() == {
        "status": "BLOCKED", "schema_version": 99,
        "reason": "TRACKING_SCHEMA_VERSION_UNSUPPORTED",
    }
    with pytest.raises(TrackingError, match="unsupported thesis tracking database schema"):
        reopened.list()


def test_version_one_tracking_database_migrates_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "tracking.sqlite3"
    import sqlite3
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE thesis_tracking_schema(singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO thesis_tracking_schema VALUES(1,1)")
    migrated = ThesisTrackingRepositoryV1(path)
    assert migrated.readiness()["status"] == "READY"
    assert migrated.readiness()["schema_version"] == 2
    with migrated._connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='thesis_current_snapshots'"
        ).fetchone()


def test_manual_scheduler_race_writes_one_logical_evaluation(tmp_path: Path) -> None:
    repository = ThesisTrackingRepositoryV1(tmp_path / "tracking.sqlite3")
    stored_track, _ = repository.create(track())
    rows = candles()
    evaluation = CurrentFeatureEvaluatorV1(Reader(rows)).evaluate(
        stored_track, now=rows[-1]["candle_close_ts"] + 1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: repository.record_evaluation(evaluation), range(16)))
    assert sum(created for _result, created in outcomes) == 1
    assert len(repository.detail("track-1")["evaluation_history"]) == 1


def test_scheduler_source_a_repeat_noop_then_source_b_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "tracking.sqlite3"
    repository = ThesisTrackingRepositoryV1(path)
    repository.create(track())
    reader = Reader(candles())
    now = [reader.rows[-1]["candle_close_ts"] + 1]
    evaluator = CurrentFeatureEvaluatorV1(reader, clock=lambda: now[0])
    service = ThesisTrackingServiceV1(repository, None, evaluator)
    scheduler = ThesisTrackingSchedulerV1(service, clock=lambda: now[0])
    assert scheduler.tick() == {"evaluated": 1, "no_change": 0, "failed": 0}
    assert scheduler.tick() == {"evaluated": 0, "no_change": 1, "failed": 0}

    prior = reader.rows[-1]
    next_open = prior["ts"] + WIDTH
    reader.rows.append({**prior, "ts": next_open, "candle_close_ts": next_open + WIDTH,
                        "volume": 50.0})
    now[0] = next_open + WIDTH + 1
    assert scheduler.tick() == {"evaluated": 1, "no_change": 0, "failed": 0}
    assert len(repository.detail("track-1")["evaluation_history"]) == 2

    restarted = ThesisTrackingServiceV1(
        ThesisTrackingRepositoryV1(path), None,
        CurrentFeatureEvaluatorV1(reader, clock=lambda: now[0]))
    assert ThesisTrackingSchedulerV1(restarted, clock=lambda: now[0]).tick() == {
        "evaluated": 0, "no_change": 1, "failed": 0}


def test_same_candle_identity_mutation_fails_closed(tmp_path: Path) -> None:
    repository = ThesisTrackingRepositoryV1(tmp_path / "tracking.sqlite3")
    stored_track, _ = repository.create(track())
    rows = candles()
    first = CurrentFeatureEvaluatorV1(Reader(rows)).evaluate(
        stored_track, now=rows[-1]["candle_close_ts"] + 1)
    repository.record_evaluation(first)
    changed = deepcopy(first)
    changed["current_dataset_identity"]["dataset_id"] = "changed"
    with pytest.raises(TrackingError, match="dataset identity changed"):
        repository.record_evaluation(changed)


def test_delta_tracks_boolean_quality_and_source_changes_deterministically() -> None:
    rows = candles(latest_volume=50.0)
    previous = evaluate(rows)
    current = evaluate(candles(latest_volume=200.0))
    delta = evaluation_delta(previous, current)
    assert delta["condition_changes"][0]["from"] == "FALSE"
    assert delta["condition_changes"][0]["to"] == "TRUE"
    assert delta["status_changed"] is True
    # Candle contents/identity update every confirmed candle and are retained
    # for audit, but are not by themselves a noisy product-feed transition.
    assert delta["source_changes"] == []
    assert delta["material_change"] is True

    changed_source = deepcopy(current)
    changed_source["current_source_version"] = ["fixture-source-v2"]
    changed_source["current_dataset_identity"]["sources"] = [
        {"source": "replacement", "source_store": "market_candles",
         "source_version": "fixture-source-v2"}]
    assert evaluation_delta(current, changed_source)["source_changes"]
