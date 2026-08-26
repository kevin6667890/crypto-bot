from __future__ import annotations

from copy import deepcopy

import pytest

from dashboard.thesis_expression import (
    AllNode, AnyNode, ConditionNode, FeatureContractV2, NotNode, ParameterContract,
    ThesisSpecV2, canonicalize_expression,
)
from dashboard.thesis_tracking_v2 import (
    CURRENT_EVALUATION_POLICY_VERSION_V2, CurrentExpressionEvaluatorV2,
    MixedVersionThesisTrackingService, ThesisTrackingServiceV2,
    expression_evaluation_delta, tracked_thesis_v2_artifact,
)
from dashboard.thesis_tracking import ThesisTrackingRepositoryV1, TrackingError
from dashboard.thesis_event_engine_v2 import compile_thesis_v2


WIDTH = 14_400
BASE = 1_700_006_400


def candles(count: int = 220) -> list[dict]:
    output = []
    for index in range(count):
        ts = BASE + index * WIDTH
        close = 100.0
        output.append({
            "ts": ts, "candle_close_ts": ts + WIDTH,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": close,
            "volume": 100.0, "confirmed": True, "source": "fixture",
            "source_version": "fixture-v1", "_source_store": "market_candles",
        })
    return output


class Reader:
    def __init__(self, rows):
        self.rows = rows

    def candles(self, instrument, timeframe, as_of, limit):
        assert (instrument, timeframe) == ("BTC-USDT", "4H")
        return deepcopy(self.rows[-limit:])


class DerivativeReader:
    def __init__(self, observations):
        self.observations = observations

    def latest(self, instrument, group, as_of, *, timeframe):
        assert instrument == "BTC-USDT"
        assert timeframe == "4H"
        value = deepcopy(self.observations.get(group))
        if isinstance(value, dict):
            value.setdefault("current_evidence", True)
        return value


REGISTRY = {
    "RSI": FeatureContractV2("RSI", "market-indicator-registry-v2", "number",
                             ("gt", "gte", "lt", "lte"), "OHLCV"),
    "VOLUME_RATIO": FeatureContractV2("VOLUME_RATIO", "discovery-features-v1", "number",
                                      ("gt", "gte", "lt", "lte"), "OHLCV"),
    "ROLLING_HIGH_BREAKOUT_CONFIRMED": FeatureContractV2(
        "ROLLING_HIGH_BREAKOUT_CONFIRMED", "rolling-structure-features-v1", "boolean",
        ("eq",), "OHLCV", {"lookback_bars": ParameterContract("integer", True, 5, 500)}),
    "FAILED_BREAKOUT_CONFIRMED": FeatureContractV2(
        "FAILED_BREAKOUT_CONFIRMED", "rolling-structure-features-v1", "boolean", ("eq",),
        "OHLCV", {"lookback_bars": ParameterContract("integer", True, 5, 500),
                  "failure_window_bars": ParameterContract("integer", False, 1, 20)}),
    "OI_CHANGE_PERCENTILE": FeatureContractV2(
        "OI_CHANGE_PERCENTILE", "canonical-oi-native-v1", "number",
        ("gt", "gte", "lt", "lte"), "OI"),
}


def track(expression, *, track_id="v2-track"):
    expression = canonicalize_expression(expression)
    spec = ThesisSpecV2("BTC", "4H", expression, ("24H",), 1_750_000_000)
    leaves = []

    def walk(node):
        if isinstance(node, ConditionNode):
            leaves.append(node)
        elif isinstance(node, NotNode):
            walk(node.child)
        else:
            for child in node.children:
                walk(child)
    walk(expression)
    return {
        "schema_version": "tracked-thesis-v2", "track_id": track_id,
        "thesis_spec": spec.to_dict(), "definition_hash": compile_thesis_v2(spec, REGISTRY).definition_hash,
        "feature_versions": {leaf.feature: REGISTRY[leaf.feature].version for leaf in leaves},
        "current_evaluation_policy_version": CURRENT_EVALUATION_POLICY_VERSION_V2,
        "historical_result_hash": "a" * 64,
        "historical_dataset_identity": "immutable-historical-v2",
    }


def evaluate(expression, rows=None, derivatives=None, now=None):
    rows = rows or candles()
    now = now or rows[-1]["candle_close_ts"] + 1
    evaluator = CurrentExpressionEvaluatorV2(
        Reader(rows), REGISTRY,
        derivative_reader=DerivativeReader(derivatives or {}) if derivatives is not None else None,
    )
    return evaluator.evaluate(track(expression), now=now)


def test_nested_three_valued_tree_and_flat_leaf_projection():
    # OI is unavailable, but RSI is true, so ANY is true. NOT(ANY) is false.
    expression = NotNode(AnyNode((ConditionNode("OI_CHANGE_PERCENTILE", "gte", 90.0),
                                  ConditionNode("RSI", "gte", 90.0))))
    result = evaluate(expression)
    assert result["expression_state"] == "FALSE"
    assert result["overall_status"] == "NOT_MATCHING"
    assert result["tree_result"]["node_type"] == "NOT"
    not_group = result["tree_result"]
    assert not_group["children"][0]["node_type"] == "ANY"
    assert not_group["children"][0]["state"] == "TRUE"
    assert len(result["leaf_results"]) == len(result["conditions"]) == 2
    oi = next(item for item in result["leaf_results"] if item["feature"] == "OI_CHANGE_PERCENTILE")
    assert oi["state"] == "UNKNOWN"


def test_latest_confirmed_only_and_rolling_breakout_context():
    rows = candles(30)
    rows[-1].update({"close": 102.0, "high": 103.0})
    forming = {**rows[-1], "ts": rows[-1]["ts"] + WIDTH,
               "candle_close_ts": rows[-1]["candle_close_ts"] + WIDTH,
               "close": 50.0, "low": 49.0, "confirmed": False}
    expression = ConditionNode("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True,
                               {"lookback_bars": 20})
    result = evaluate(expression, [*rows, forming], now=forming["candle_close_ts"] + 1)
    leaf = result["leaf_results"][0]
    assert result["source_candle_timestamp"] == rows[-1]["candle_close_ts"]
    assert leaf["state"] == "TRUE"
    assert leaf["event_context"]["reference_level"] == 101.0
    assert leaf["event_context"]["event_timestamp"] == rows[-1]["candle_close_ts"]


def test_failed_breakout_is_current_only_at_failure_confirmation_timestamp():
    rows = candles(12)
    rows[-2].update({"close": 102.0, "high": 103.0})
    rows[-1].update({"close": 100.0, "high": 101.0})
    expression = ConditionNode("FAILED_BREAKOUT_CONFIRMED", "eq", True,
                               {"lookback_bars": 5, "failure_window_bars": 3})
    result = evaluate(expression, rows)
    context = result["leaf_results"][0]["event_context"]
    assert result["leaf_results"][0]["state"] == "TRUE"
    assert context["original_breakout_timestamp"] == rows[-2]["candle_close_ts"]
    assert context["failure_confirmation_timestamp"] == rows[-1]["candle_close_ts"]
    assert context["event_timestamp"] == rows[-1]["candle_close_ts"]


def test_derivative_future_join_and_staleness_are_unknown_never_false():
    rows = candles()
    close = rows[-1]["candle_close_ts"]
    expression = ConditionNode("OI_CHANGE_PERCENTILE", "gte", 90.0)
    future = evaluate(expression, rows, {"OI": {
        "timestamp": close + 300, "values": {"OI_CHANGE_PERCENTILE": 99.0},
        "source": "official", "source_version": "v1",
    }})
    assert future["expression_state"] == "UNKNOWN"
    assert future["leaf_results"][0]["limitation"] == "CURRENT_DERIVATIVE_FUTURE_TIMESTAMP_REJECTED"

    stale = evaluate(expression, rows, {"OI": {
        "timestamp": close - 4 * 60 * 60 - 1,
        "values": {"OI_CHANGE_PERCENTILE": 99.0},
        "source": "official", "source_version": "v1",
    }})
    assert stale["expression_state"] == "UNKNOWN"
    assert stale["leaf_results"][0]["quality"] == "STALE"

    published_late = evaluate(expression, rows, {"OI": {
        "timestamp": close, "available_at": close + 1,
        "values": {"OI_CHANGE_PERCENTILE": 99.0},
        "source": "official", "source_version": "v1",
    }})
    assert published_late["expression_state"] == "UNKNOWN"
    assert published_late["leaf_results"][0]["limitation"] == \
        "CURRENT_DERIVATIVE_FUTURE_TIMESTAMP_REJECTED"


def test_derivative_current_value_and_component_identity_are_separate():
    rows = candles()
    close = rows[-1]["candle_close_ts"]
    expression = ConditionNode("OI_CHANGE_PERCENTILE", "gte", 90.0)
    result = evaluate(expression, rows, {"OI": {
        "timestamp": close, "values": {"OI_CHANGE_PERCENTILE": 95.0},
        "source": "okx-official", "source_version": "api-v5", "dataset_id": "live-oi",
    }})
    assert result["overall_status"] == "MATCHING"
    components = result["current_dataset_identity"]["components"]
    assert [item["component"] for item in components] == ["OHLCV", "OI"]
    assert result["current_dataset_identity"]["dataset_id"] != "immutable-historical-v2"

    history_only = evaluate(expression, rows, {"OI": {
        "timestamp": close, "values": {"OI_CHANGE_PERCENTILE": 95.0},
        "current_evidence": False, "source": "immutable-history-only",
        "source_version": "v1", "dataset_id": "history-oi",
    }})
    assert history_only["expression_state"] == "UNKNOWN"
    assert history_only["leaf_results"][0]["limitation"] == \
        "CURRENT_DERIVATIVE_SOURCE_UNAVAILABLE"


def test_expression_delta_reports_leaf_group_and_overall_changes():
    expression = AnyNode((ConditionNode("RSI", "gt", 101.0),
                          ConditionNode("VOLUME_RATIO", "gte", 1.2)))
    low = candles()
    high = candles()
    high[-1]["volume"] = 200.0
    previous = evaluate(expression, low)
    current = evaluate(expression, high)
    delta = expression_evaluation_delta(previous, current)
    assert delta["overall_change"] == {"from": "NOT_MATCHING", "to": "MATCHING"}
    assert [(item["feature"], item["from"], item["to"]) for item in delta["leaf_changes"]] == [
        ("VOLUME_RATIO", "FALSE", "TRUE")]
    assert delta["group_changes"] == [{
        "node_id": "root", "node_type": "ANY", "feature": None,
        "from": "FALSE", "to": "TRUE",
    }]
    assert delta["material_change"] is True


def test_v2_artifact_keeps_historical_identity_and_hash_verbatim():
    expression = ConditionNode("RSI", "lte", 80.0)
    spec = ThesisSpecV2("BTC", "4H", expression, ("24H",), 1_750_000_000)
    result = {
        "status": "COMPLETED", "result_hash": "f" * 64,
        "definition_hash": spec.definition_hash, "thesis_spec": spec.to_dict(),
        "feature_versions": {"RSI": REGISTRY["RSI"].version},
        "historical_data": {"dataset_id": "composite-history-v2", "components": [
            {"component": "OHLCV", "dataset_id": "ohlcv-v1"}]},
        "engine_version": "thesis-event-engine-v2", "tested_range": {"start": 1, "end": 2},
    }
    artifact = tracked_thesis_v2_artifact(result, track_id="fixed")
    assert artifact["schema_version"] == "tracked-thesis-v2"
    assert artifact["definition_hash"] == spec.definition_hash
    assert artifact["historical_result_hash"] == "f" * 64
    assert artifact["historical_dataset_identity"] == "composite-history-v2"
    assert artifact["historical_baseline"]["historical_data"]["components"][0]["dataset_id"] == "ohlcv-v1"


def test_v2_artifact_round_trips_through_unchanged_v1_repository_schema(tmp_path):
    expression = ConditionNode("RSI", "lte", 80.0)
    spec = ThesisSpecV2("BTC", "4H", expression, ("24H",), 1_750_000_000)
    result = {
        "status": "COMPLETED", "result_hash": "e" * 64,
        "definition_hash": spec.definition_hash, "thesis_spec": spec.to_dict(),
        "feature_versions": {"RSI": REGISTRY["RSI"].version},
        "historical_data": {"dataset_id": "v2-history", "components": []},
    }
    repository = ThesisTrackingRepositoryV1(tmp_path / "tracking.sqlite3")
    artifact = tracked_thesis_v2_artifact(result, track_id="v2-stored")
    stored, created = repository.create(artifact)
    assert created is True
    assert repository.readiness()["status"] == "READY"
    assert repository.get("v2-stored")["schema_version"] == "tracked-thesis-v2"
    assert stored["historical_baseline"] == repository.get("v2-stored")["historical_baseline"]


def test_track_creation_fails_closed_for_historical_only_feature(tmp_path):
    expression = ConditionNode("OI_CHANGE_PERCENTILE", "gte", 90.0)
    spec = ThesisSpecV2("BTC", "4H", expression, ("24H",), 1_750_000_000)
    compiled = compile_thesis_v2(spec, REGISTRY)
    result = {
        "status": "COMPLETED", "result_hash": "d" * 64,
        "definition_hash": compiled.definition_hash, "thesis_spec": spec.to_dict(),
        "feature_versions": {"OI_CHANGE_PERCENTILE": REGISTRY["OI_CHANGE_PERCENTILE"].version},
        "historical_data": {"dataset_id": "oi-history", "components": []},
    }
    thesis_service = type("Historical", (), {
        "verified_result": lambda _self, _spec, _hash: (result, []),
    })()
    repository = ThesisTrackingRepositoryV1(tmp_path / "tracking.sqlite3")
    evaluator = CurrentExpressionEvaluatorV2(Reader(candles()), REGISTRY)
    service = ThesisTrackingServiceV2(
        repository, evaluator, thesis_service, trackable_features={"RSI", "VOLUME_RATIO"})
    with pytest.raises(TrackingError, match="HISTORICAL_ONLY:OI_CHANGE_PERCENTILE"):
        service.create({
            "version": "track-thesis-request-v2", "result_hash": "d" * 64,
            "thesis_spec": spec.to_dict(), "language": "en",
        })
    assert repository.list() == []


def test_mixed_scheduler_dispatches_v1_and_v2_without_version_mismatch():
    tracks = {
        "legacy": {"track_id": "legacy", "schema_version": "tracked-thesis-v1"},
        "modern": {"track_id": "modern", "schema_version": "tracked-thesis-v2"},
    }

    class Repository:
        def get(self, track_id):
            return tracks.get(track_id)

        def list(self, limit=100):
            return [{"track": value} for value in tracks.values()][:limit]

    calls = []

    class Service:
        def __init__(self, version):
            self.version = version

        def evaluate(self, track_id, now=None):
            calls.append((self.version, track_id, now))
            return {"evaluation_created": True}

    mixed = MixedVersionThesisTrackingService(
        Repository(), Service("v1"), Service("v2"))
    assert mixed.evaluate_active(now=1_700_000_000) == {
        "evaluated": 2, "no_change": 0, "failed": 0}
    assert calls == [("v1", "legacy", 1_700_000_000),
                     ("v2", "modern", 1_700_000_000)]
