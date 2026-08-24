from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from dashboard.canonical_market_snapshot import (
    CANONICAL_MARKET_SNAPSHOT_VERSION,
    FactProvenanceClass,
    build_canonical_market_snapshot,
)
from dashboard.market_context_v2 import MarketContextServiceV2
from tests.test_market_context_v2 import FakeReader, candles


def context_payload() -> dict:
    rows = candles(240)
    return MarketContextServiceV2(FakeReader({"15m": rows})).context(
        "ETH-USDT-SWAP", as_of=rows[-1]["candle_close_ts"])


def test_snapshot_is_versioned_immutable_and_hash_is_deterministic() -> None:
    payload = context_payload()
    first = build_canonical_market_snapshot(payload)
    second = build_canonical_market_snapshot(deepcopy(payload))
    assert first.version == CANONICAL_MARKET_SNAPSHOT_VERSION
    assert first.snapshot_identity == second.snapshot_identity
    assert first.to_dict() == second.to_dict()
    with pytest.raises(FrozenInstanceError):
        first.instrument = "BTC-USDT-SWAP"  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.facts_by_path["/price"] = first.facts[0]  # type: ignore[index]


def test_context_service_exposes_canonical_snapshot_integration_hook() -> None:
    rows = candles(240)
    service = MarketContextServiceV2(FakeReader({"15m": rows}))
    snapshot = service.canonical_snapshot(
        "ETH-USDT-SWAP", as_of=rows[-1]["candle_close_ts"])
    assert snapshot.snapshot_identity == build_canonical_market_snapshot(
        service.context("ETH-USDT-SWAP", as_of=rows[-1]["candle_close_ts"])
    ).snapshot_identity


def test_snapshot_enforces_inclusive_causal_cutoff() -> None:
    payload = context_payload()
    payload["price"]["source_timestamp"] = payload["as_of"] + 1
    with pytest.raises(ValueError, match="later than causal cutoff"):
        build_canonical_market_snapshot(payload)


def test_missing_observations_are_explicit_null_not_neutral() -> None:
    snapshot = build_canonical_market_snapshot(context_payload())
    cvd = snapshot.fact("/flow/cvd/current")
    assert cvd is not None
    assert cvd.value is None
    assert cvd.quality == "MISSING"
    assert cvd.provenance_class == FactProvenanceClass.CANONICAL_OBSERVATION.value
    spot_cvd = next(item for item in snapshot.microstructure
                    if item.series == "cvd" and item.identity.product_type == "SPOT")
    swap_cvd = next(item for item in snapshot.microstructure
                    if item.series == "cvd" and item.identity.product_type == "SWAP")
    assert spot_cvd.value is None
    assert spot_cvd.missing_reason == "SOURCE_PRODUCT_NOT_COLLECTED"
    assert swap_cvd.identity.instrument == "ETH-USDT-SWAP"
    assert swap_cvd.value is None and swap_cvd.missing_reason == "NO_CONFIRMED_OBSERVATION"


def test_structured_consumer_contract_exposes_price_and_each_timeframe_ohlcv() -> None:
    snapshot = build_canonical_market_snapshot(context_payload())
    assert snapshot.decision_time == snapshot.as_of == snapshot.causal_cutoff
    assert snapshot.price.identity.instrument == "ETH-USDT-SWAP"
    assert snapshot.price.identity.product_type == "SWAP"
    assert tuple(item.timeframe for item in snapshot.timeframes) == ("15m", "1H", "4H", "1D", "1W")
    execution = snapshot.timeframes[0]
    assert execution.confirmed is True
    assert execution.last_close == snapshot.price.value
    assert execution.source_timestamp <= snapshot.causal_cutoff
    assert execution.gap_state == "CONTIGUOUS"
    weekly = snapshot.timeframes[-1]
    assert weekly.last_close is None
    assert weekly.missing_reason == "NO_CONFIRMED_CANDLES"


def test_context_boundary_labels_each_semantic_layer() -> None:
    payload = context_payload()
    assert payload["provenance"]["layer_contract"] == {
        "source_input": "RAW",
        "accepted_observation": "CANONICAL_OBSERVATION",
        "calculated_value": "DERIVED_FACT",
        "semantic_lens": "DETERMINISTIC_INTERPRETATION",
    }
    assert payload["price"]["provenance_class"] == "CANONICAL_OBSERVATION"
    assert payload["timeframes"]["15m"]["trend"]["ema20"]["provenance_class"] == "DERIVED_FACT"
    assert payload["timeframes"]["15m"]["observation"]["input_provenance_class"] == "RAW"
    assert payload["flow"]["price_cvd_combination"]["provenance_class"] == "DETERMINISTIC_INTERPRETATION"


def test_microstructure_evidence_adapter_payload_maps_without_product_substitution() -> None:
    payload = context_payload()
    cutoff_ms = payload["as_of"] * 1000
    payload["microstructure_evidence"] = [{
        "contract_version": "microstructure-evidence-v1", "series": "cvd",
        "identity": {"venue": "OKX", "product_type": "SWAP",
                     "instrument": "ETH-USDT-SWAP"},
        "window": {"start_ms": cutoff_ms - 60_000, "end_ms": cutoff_ms,
                   "resolution": "1m"},
        "value": 12.5, "source_end_ms": cutoff_ms, "freshness_ms": 0,
        "coverage": {"expected_buckets": 2, "observed_buckets": 2,
                     "ratio": 1.0, "has_gaps": False},
        "quality": "VALID", "missing_reason": None,
        "provenance": {"source": "canonical_history"},
    }]
    snapshot = build_canonical_market_snapshot(payload)
    swap = next(item for item in snapshot.microstructure
                if item.series == "cvd" and item.identity.product_type == "SWAP")
    spot = next(item for item in snapshot.microstructure
                if item.series == "cvd" and item.identity.product_type == "SPOT")
    assert swap.value == 12.5 and swap.coverage_ratio == 1.0
    assert swap.source == "canonical_history"
    assert spot.value is None and spot.missing_reason == "EVIDENCE_SERIES_NOT_SUPPLIED"
