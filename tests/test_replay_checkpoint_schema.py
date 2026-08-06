from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from dashboard.strategy_phase4a import TimeSegmentV2
from dashboard.strategy_phase4a_router_repair import (
    CHECKPOINT_SCHEMA_VERSION, DEVELOPMENT_START,
    HistoricalMarketContextV2Provider, StrategyEventReplayEngineV2_1,
    trials_from_original_manifest,
)


DATASET = Path(r"C:\Users\ASUS\crypto-bot-research\data\canonical_ohlcv_2023_2025.db")
DATASET_ID = "e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062"
MANIFEST = Path("research/phase4a_research_manifest_v1.json")
INSTRUMENT = "BTC-USDT-SWAP"


@pytest.fixture(scope="module")
def replay_case():
    trial = trials_from_original_manifest(MANIFEST)[:1]
    timestamps = [DEVELOPMENT_START, DEVELOPMENT_START + 900]
    segment = TimeSegmentV2("DEVELOPMENT", DEVELOPMENT_START, DEVELOPMENT_START + 1800, "schema-fixture")
    engine = StrategyEventReplayEngineV2_1(
        HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID))
    result = engine.replay(instrument=INSTRUMENT, confirmed_close_timestamps=timestamps,
                           trials=trial, segment=segment)
    return engine, trial, timestamps, segment, result["checkpoint"]


def _resume(case, checkpoint, **overrides):
    engine, trials, timestamps, segment, _ = case
    return engine.replay(instrument=overrides.get("instrument", INSTRUMENT),
                         confirmed_close_timestamps=timestamps, trials=overrides.get("trials", trials),
                         segment=overrides.get("segment", segment), checkpoint=checkpoint)


def test_current_schema_resumes_and_preserves_anchor_without_duplicate_trigger(replay_case):
    checkpoint = replay_case[-1]
    resumed = _resume(replay_case, checkpoint)
    assert checkpoint["schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert resumed["event_count"] == 0 and resumed["intents"] == []
    assert resumed["checkpoint"]["lifecycle"] == checkpoint["lifecycle"]


@pytest.mark.parametrize(("mutation", "code"), [
    (lambda cp: cp.pop("schema_version"), "CHECKPOINT_SCHEMA_MISSING"),
    (lambda cp: cp.__setitem__("schema_version", "strategy-replay-checkpoint-v2"), "CHECKPOINT_SCHEMA_MISMATCH"),
    (lambda cp: cp.__setitem__("schema_version", "strategy-replay-checkpoint-v999"), "CHECKPOINT_SCHEMA_MISMATCH"),
    (lambda cp: cp.__setitem__("lifecycle_identity_contract_version", "lifecycle-identity-contract-v1"), "CHECKPOINT_IDENTITY_CONTRACT_MISMATCH"),
    (lambda cp: cp.__setitem__("dataset_identity", "other"), "CHECKPOINT_DATASET_MISMATCH"),
    (lambda cp: cp.__setitem__("segment_identity", "other"), "CHECKPOINT_SEGMENT_MISMATCH"),
    (lambda cp: cp.__setitem__("instrument", "ETH-USDT-SWAP"), "CHECKPOINT_INSTRUMENT_MISMATCH"),
])
def test_checkpoint_identity_and_schema_mismatches_are_rejected(replay_case, mutation, code):
    checkpoint = deepcopy(replay_case[-1]); mutation(checkpoint)
    with pytest.raises(ValueError, match=code):
        _resume(replay_case, checkpoint)


def test_raw_lifecycle_key_checkpoint_is_rejected(replay_case):
    checkpoint = deepcopy(replay_case[-1]); key = next(iter(checkpoint["lifecycle"]))
    checkpoint["lifecycle"][key]["lifecycle_setup_key"] = "raw-key"
    checkpoint["routes"][key]["candidates"][0]["identity"]["lifecycle_setup_key"] = "raw-key"
    with pytest.raises(ValueError, match="CHECKPOINT_RAW_LIFECYCLE_KEY"):
        _resume(replay_case, checkpoint)


@pytest.mark.parametrize("field", ["parameter_set_id", "family", "direction"])
def test_checkpoint_trial_identity_mismatch_is_rejected(replay_case, field):
    checkpoint = deepcopy(replay_case[-1]); key = next(iter(checkpoint["lifecycle"]))
    checkpoint["lifecycle"][key][field] = "different"
    with pytest.raises(ValueError, match="CHECKPOINT_SCHEMA_MISMATCH"):
        _resume(replay_case, checkpoint)


def test_old_artifact_is_not_rewritten_on_rejection(replay_case):
    checkpoint = deepcopy(replay_case[-1]); checkpoint.pop("schema_version")
    before = deepcopy(checkpoint)
    with pytest.raises(ValueError, match="CHECKPOINT_SCHEMA_MISSING"):
        _resume(replay_case, checkpoint)
    assert checkpoint == before


def test_checkpoint_contains_all_required_version_and_lifecycle_fields(replay_case):
    checkpoint = replay_case[-1]
    assert {"schema_version", "lifecycle_identity_contract_version", "replay_engine_version",
            "market_context_version", "market_state_version", "router_version",
            "definitions_version", "dataset_identity", "segment_identity", "instrument",
            "created_at", "last_evaluated_ts"}.issubset(checkpoint)
    required = {"stage", "strategy_setup_anchor_id", "level_continuity_id", "lifecycle_setup_key",
                "setup_started_at", "trigger_timestamp", "expiry", "cooldown",
                "parameter_set_id", "family", "direction"}
    assert all(required.issubset(record) for record in checkpoint["lifecycle"].values())
