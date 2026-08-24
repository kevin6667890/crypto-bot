from __future__ import annotations

from copy import deepcopy
import math

import pytest

from dashboard.thesis_event_engine import (
    FEATURE_REGISTRY, RESULT_VERSION, THESIS_SPEC_VERSION, ThesisEventEngineV1,
    ThesisSpecV1, ThesisTestServiceV1, ThesisValidationError, compile_feature_rows,
    compile_thesis,
)


WIDTH = 14_400
BASE = 1_700_006_400


def candles(count: int = 90, *, volumes: dict[int, float] | None = None,
            closes: dict[int, float] | None = None) -> list[dict]:
    volumes, closes = volumes or {}, closes or {}
    rows = []
    for index in range(count):
        close = closes.get(index, 100 + index * .02)
        rows.append({"ts": BASE + index * WIDTH, "candle_close_ts": BASE + (index + 1) * WIDTH,
                     "open": close - .1, "high": close + 1, "low": close - 1,
                     "close": close, "volume": volumes.get(index, 100.0),
                     "confirmed": True, "source": "fixture-v1"})
    return rows


def payload(feature: str = "VOLUME_RATIO", operator: str = "gte", value=1.5,
            rows: list[dict] | None = None, **overrides) -> dict:
    data = {"version": THESIS_SPEC_VERSION, "instrument": "BTC", "timeframe": "4H",
            "required_conditions": [{"feature": feature, "operator": operator, "value": value}],
            "optional_conditions": [], "forward_horizons": ["4H", "12H", "24H"],
            "requested_as_of": (rows or candles())[-1]["candle_close_ts"]}
    data.update(overrides)
    return data


def run(rows: list[dict], **overrides) -> dict:
    return ThesisEventEngineV1().run(ThesisSpecV1.from_dict(payload(rows=rows, **overrides)), rows)


@pytest.mark.parametrize("change,match", [
    ({"instrument": "DOGE"}, "unsupported instrument"),
    ({"timeframe": "15m"}, "unsupported timeframe"),
    ({"required_conditions": []}, "must not be empty"),
    ({"required_conditions": [{"feature": "PYTHON", "operator": "eq", "value": True}]}, "unsupported feature"),
    ({"required_conditions": [{"feature": "VOLUME_RATIO", "operator": "eq", "value": 1.0}]}, "invalid operator"),
    ({"required_conditions": [{"feature": "PRICE_ABOVE_EMA20", "operator": "eq", "value": 1}]}, "requires a boolean"),
])
def test_contract_and_compiler_reject_closed_vocabulary_violations(change, match):
    rows = candles()
    body = payload(rows=rows)
    body.update(change)
    with pytest.raises(ThesisValidationError, match=match):
        compile_thesis(ThesisSpecV1.from_dict(body))


def test_nonfinite_threshold_and_future_as_of_are_rejected():
    body = payload(rows=candles())
    body["required_conditions"][0]["value"] = math.inf
    with pytest.raises(ThesisValidationError, match="finite"):
        ThesisSpecV1.from_dict(body)
    body = payload(rows=candles(), requested_as_of=4_000_000_000)
    with pytest.raises(ThesisValidationError, match="future"):
        ThesisSpecV1.from_dict(body)


@pytest.mark.parametrize("feature,value", [
    ("RSI", 101), ("VOLUME_PERCENTILE", -1), ("ATR_PCT", -0.1),
    ("MOMENTUM_PERSISTENCE", 1.01),
])
def test_feature_specific_invalid_thresholds_are_rejected(feature, value):
    body = payload(feature=feature, value=value, rows=candles())
    with pytest.raises(ThesisValidationError, match="threshold"):
        compile_thesis(ThesisSpecV1.from_dict(body))


def test_definition_hash_is_order_stable_and_threshold_sensitive():
    rows = candles()
    first = payload(rows=rows, required_conditions=[
        {"feature": "VOLUME_RATIO", "operator": "gte", "value": 1.2},
        {"feature": "RSI", "operator": "gte", "value": 50},
    ])
    second = dict(reversed(list(first.items())))
    second["required_conditions"] = list(reversed(first["required_conditions"]))
    assert compile_thesis(ThesisSpecV1.from_dict(first)).definition_hash == compile_thesis(ThesisSpecV1.from_dict(second)).definition_hash
    second["required_conditions"][0] = {"feature": "RSI", "operator": "gte", "value": 51}
    assert compile_thesis(ThesisSpecV1.from_dict(first)).definition_hash != compile_thesis(ThesisSpecV1.from_dict(second)).definition_hash


def test_point_in_time_percentile_is_unchanged_by_future_extreme_and_ties_are_defined():
    prefix = candles(45)
    before = compile_feature_rows(prefix)
    after = compile_feature_rows(prefix + candles(1)[0:0])
    assert [row["volume_percentile"] for row in before] == [row["volume_percentile"] for row in after]
    mutated = deepcopy(prefix) + candles(1)
    mutated[-1]["ts"] = prefix[-1]["ts"] + WIDTH
    mutated[-1]["candle_close_ts"] = prefix[-1]["candle_close_ts"] + WIDTH
    mutated[-1]["volume"] = 1e12
    compiled = compile_feature_rows(mutated)
    assert [row["volume_percentile"] for row in compiled[:45]] == [row["volume_percentile"] for row in before]
    assert before[18]["volume_percentile"] is None
    assert before[19]["volume_percentile"] == 100.0


def test_transition_persistence_second_episode_and_overlap_counts():
    rows = candles(90, volumes={25: 250, 26: 250, 30: 250, 40: 250})
    result = run(rows)
    # index 25/26 is one true episode; 30 overlaps index 25's 24H window; 40 is independent.
    assert result["raw_candidate_count"] == 3
    assert result["independent_event_count"] == 2
    assert result["excluded_overlap_count"] == 1
    assert [event["exclusion_reason"] for event in result["event_records"]].count("OVERLAPPING_MAX_FORWARD_WINDOW") == 1


def test_first_true_and_unknown_to_true_are_not_transitions():
    rows = candles(70)
    # PRICE_ABOVE_MA60 first becomes evaluable as true; no prior explicit false exists.
    result = run(rows, feature="PRICE_ABOVE_MA60", operator="eq", value=True)
    assert result["raw_candidate_count"] == 0


def test_exact_outcomes_exclude_event_bar_and_censor_terminal_history():
    rows = candles(60, volumes={30: 250})
    rows[30].update(close=100, high=999, low=1)
    rows[31].update(close=102, high=105, low=98)
    rows[32].update(close=101, high=103, low=95)
    rows[33].update(close=104, high=104, low=99)
    rows[34].update(high=106, low=97)
    rows[35].update(high=107, low=96, close=103)
    rows[36].update(high=104, low=99, close=103)
    result = run(rows)
    event = next(item for item in result["event_records"] if item["exclusion_status"] == "INCLUDED")
    assert event["outcomes"]["4H"] == {"available": True, "censor_reason": None,
        "forward_return_fraction": pytest.approx(.02), "mfe_fraction": pytest.approx(.05),
        "mae_fraction": pytest.approx(-.02)}
    assert event["outcomes"]["12H"]["forward_return_fraction"] == pytest.approx(.04)
    assert event["outcomes"]["24H"]["forward_return_fraction"] == pytest.approx(.03)
    assert event["outcomes"]["24H"]["mfe_fraction"] == pytest.approx(.07)
    assert event["outcomes"]["24H"]["mae_fraction"] == pytest.approx(-.05)

    near_end = candles(60, volumes={57: 250})
    censored = run(near_end)
    terminal = censored["event_records"][0]
    assert terminal["outcomes"]["4H"]["available"] is True
    assert terminal["outcomes"]["12H"]["censor_reason"] == "TERMINAL_HISTORY"
    assert terminal["outcomes"]["24H"]["censor_reason"] == "TERMINAL_HISTORY"
    assert censored["aggregates"]["24H"]["censored_n"] == 1


def test_path_gap_fails_coverage_before_event_scan():
    rows = candles(70, volumes={30: 250})
    del rows[33]
    result = run(rows)
    assert result["status"] == "THESIS_NOT_TESTABLE_AS_REQUESTED"
    assert result["event_records"] == []
    assert result["coverage"]["features"][0]["qualification"] == "INSUFFICIENT_COVERAGE"


def test_missing_flow_fails_coverage_without_zero_or_condition_drop():
    rows = candles()
    result = run(rows, feature="OI_CHANGE", value=0.0)
    assert result["status"] == "THESIS_NOT_TESTABLE_AS_REQUESTED"
    assert result["coverage"]["features"][0]["qualification"] == "UNAVAILABLE"
    assert result["raw_candidate_count"] == 0
    assert "OI" in result["coverage"]["features"][0]["reason"]


@pytest.mark.parametrize("quality,expected", [
    ("PARTIAL", "PARTIAL"), ("STALE", "STALE_CURRENT_DATA"),
    ("GAP", "INSUFFICIENT_COVERAGE"), ("UNAVAILABLE", "UNAVAILABLE"),
])
def test_flow_quality_states_fail_closed_before_scan(quality, expected):
    rows = candles()
    spec = ThesisSpecV1.from_dict(payload(feature="OI_CHANGE", value=0.0, rows=rows))
    result = ThesisEventEngineV1().run(spec, rows, source_quality={"OI": quality})
    assert result["status"] == "THESIS_NOT_TESTABLE_AS_REQUESTED"
    assert result["coverage"]["features"][0]["qualification"] == expected
    assert result["event_records"] == []


def test_gap_stale_and_insufficient_history_fail_before_scan():
    short = candles(25)
    assert run(short)["status"] == "THESIS_NOT_TESTABLE_AS_REQUESTED"
    gapped = candles(70)
    del gapped[45]
    assert run(gapped)["coverage"]["features"][0]["qualification"] == "INSUFFICIENT_COVERAGE"
    stale = candles(70)
    result = run(stale, requested_as_of=stale[-1]["candle_close_ts"] + WIDTH * 3)
    assert result["coverage"]["features"][0]["qualification"] == "STALE_CURRENT_DATA"


def test_future_unconfirmed_and_conflicting_duplicate_sources_fail_closed():
    rows = candles()
    future = deepcopy(rows)
    future[-1]["candle_close_ts"] += WIDTH
    with pytest.raises(ThesisValidationError, match="future timestamp"):
        run(future, requested_as_of=rows[-1]["candle_close_ts"])
    unconfirmed = deepcopy(rows)
    unconfirmed[-1]["confirmed"] = False
    with pytest.raises(ThesisValidationError, match="unconfirmed"):
        run(unconfirmed)
    duplicate = deepcopy(rows) + [dict(rows[-1], close=rows[-1]["close"] + .1)]
    with pytest.raises(ThesisValidationError, match="conflicting duplicate"):
        run(duplicate)
    provenance_conflict = deepcopy(rows) + [dict(rows[-1], source="other-source")]
    with pytest.raises(ThesisValidationError, match="conflicting duplicate"):
        run(provenance_conflict)


@pytest.mark.parametrize("mutation,match", [
    ({"high": 50, "low": 150}, "geometry"),
    ({"volume": -1}, "non-negative"),
    ({"close": float("nan")}, "finite"),
    ({"ts": BASE + 1}, "aligned"),
])
def test_corrupt_ohlcv_fails_before_features_or_outcomes(mutation, match):
    rows = candles()
    rows[40].update(mutation)
    if "ts" in mutation:
        rows[40]["candle_close_ts"] = rows[40]["ts"] + WIDTH
    with pytest.raises(ThesisValidationError, match=match):
        run(rows)


def test_lookahead_mutation_does_not_change_past_event_membership_or_percentile():
    rows = candles(90, volumes={25: 250, 50: 250})
    baseline = run(rows)
    changed = deepcopy(rows)
    for row in changed[60:]:
        row["close"] *= 5
        row["high"] *= 7
        row["low"] *= .2
        row["volume"] *= 100
    alternate = run(changed)
    cutoff = rows[59]["candle_close_ts"]
    membership = lambda result: [(item["event_id"], item["timestamp"]) for item in result["event_records"] if item["timestamp"] <= cutoff]
    assert membership(baseline) == membership(alternate)
    assert [row["volume_percentile"] for row in compile_feature_rows(rows)[:60]] == [row["volume_percentile"] for row in compile_feature_rows(changed)[:60]]


def test_no_events_and_small_sample_statistics_are_honest_and_reproducible():
    rows = candles(90, volumes={25: 250, 40: 250, 55: 250})
    first, second = run(rows), run(deepcopy(rows))
    assert first["result_version"] == RESULT_VERSION
    assert first["result_hash"] == second["result_hash"]
    assert first["aggregates"]["24H"]["sample_quality"] == "INSUFFICIENT"
    assert first["aggregates"]["24H"]["eligible_n"] == 3
    empty = run(candles(90))
    assert empty["raw_candidate_count"] == 0
    assert empty["aggregates"]["24H"]["historical_positive_rate"] is None
    assert empty["aggregates"]["24H"]["mean_return_fraction"] is None
    assert "not causal proof" in empty["limitations"][0]


def test_provenance_metadata_does_not_change_canonical_result_identity():
    rows = candles(90, volumes={30: 250})
    first = run(rows, metadata={"request_uuid": "one", "runtime_ms": 1})
    second = run(rows, metadata={"request_uuid": "two", "runtime_ms": 999})
    assert first["result_hash"] == second["result_hash"]


def test_optional_unavailable_flow_is_visible_but_does_not_change_membership():
    rows = candles(90, volumes={30: 250})
    body = payload(rows=rows, optional_conditions=[
        {"feature": "OI_CHANGE", "operator": "gte", "value": 0}])
    spec = ThesisSpecV1.from_dict(body)
    result = ThesisEventEngineV1().run(spec, rows, source_quality={"OI": "PARTIAL"})
    assert result["status"] == "COMPLETED"
    assert result["raw_candidate_count"] == 1
    assert result["optional_coverage"] == [{"feature": "OI_CHANGE", "source_group": "OI",
        "source_quality": "PARTIAL", "usable_observations": 0, "status": "UNAVAILABLE"}]
    assert result["warnings"] == ["OPTIONAL_FEATURE_UNAVAILABLE:OI_CHANGE:PARTIAL"]
    assert result["event_records"][0]["optional_observations"]["OI_CHANGE"] is None


def test_unused_source_quality_does_not_change_result_identity():
    rows = candles(90, volumes={30: 250})
    spec = ThesisSpecV1.from_dict(payload(rows=rows))
    first = ThesisEventEngineV1().run(spec, rows)
    second = ThesisEventEngineV1().run(spec, rows, source_quality={"CVD": "PARTIAL"})
    assert first["result_hash"] == second["result_hash"]


class Reader:
    def __init__(self, rows): self.rows = rows; self.calls = []
    def candles(self, instrument, timeframe, as_of, limit):
        self.calls.append((instrument, timeframe, as_of, limit)); return deepcopy(self.rows)


def test_complete_service_boundary_is_bounded_and_read_only():
    rows = candles(90, volumes={30: 250})
    reader = Reader(rows)
    result = ThesisTestServiceV1(reader).test(payload(rows=rows))
    assert result["status"] == "COMPLETED"
    assert reader.calls == [("BTC-USDT", "4H", rows[-1]["candle_close_ts"], 20_000)]
    assert result["data_identity"]["row_count"] == 90
    assert len(result["data_identity"]["content_sha256"]) == 64
    assert set(FEATURE_REGISTRY) >= {"VOLUME_RATIO", "RSI", "OI_CHANGE", "CVD_DIVERGING_PRICE"}
