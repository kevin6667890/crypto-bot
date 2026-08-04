from __future__ import annotations

from dataclasses import asdict
import copy
import json
from pathlib import Path

import pytest

from dashboard.market_context_v2 import CONTEXT_VERSION, MarketAnalysisContextV2
from dashboard.market_state_v2 import STATE_ENGINE_VERSION
from dashboard.strategy_phase4a import TimeSegmentV2
from dashboard.strategy_phase4a_router_repair import (
    BACKTEST_ENGINE_VERSION, DEVELOPMENT_END, DEVELOPMENT_START,
    DevelopmentAccessGuard, HistoricalMarketContextV2Provider,
    REPLAY_ENGINE_VERSION, StrategyEventReplayEngineV2_1,
    stable_hash, trials_from_original_manifest,
)


DATASET = Path(r"C:\Users\ASUS\crypto-bot-research\data\canonical_ohlcv_2023_2025.db")
MANIFEST = Path("research/phase4a_research_manifest_v1.json")
DATASET_ID = "e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062"


def segment(start=DEVELOPMENT_START, end=DEVELOPMENT_START + 4 * 900, identity="fixture"):
    return TimeSegmentV2("DEVELOPMENT", start, end, identity)


def test_versions_are_explicitly_bumped():
    assert REPLAY_ENGINE_VERSION == "strategy-event-replay-engine-v2.1"
    assert BACKTEST_ENGINE_VERSION == "strategy-backtest-engine-v2.1"


def test_original_trial_space_is_exact_and_isolated():
    trials = trials_from_original_manifest(MANIFEST)
    assert len(trials) == len({item.parameter_set_id for item in trials}) == 32
    for family in ("TREND_PULLBACK", "MA200_MEAN_REVERSION"):
        for direction in ("LONG", "SHORT"):
            assert sum((item.family, item.direction) == (family, direction) for item in trials) == 8


def test_historical_provider_returns_formal_context_model_and_missing_flow():
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID)
    model = provider.provide_model("BTC-USDT-SWAP", DEVELOPMENT_START, segment_identity="x")
    payload = provider.provide("BTC-USDT-SWAP", DEVELOPMENT_START, segment_identity="x")
    assert isinstance(model, MarketAnalysisContextV2)
    assert model.version == payload["version"] == CONTEXT_VERSION
    assert payload["flow"]["price_cvd_combination"]["data_quality"] == "MISSING"
    assert payload["flow"]["price_oi_combination"]["data_quality"] == "MISSING"
    assert provider.cache_size <= provider.MAX_CONTEXT_CACHE == 64


def test_confirmed_and_higher_timeframe_causality():
    payload = HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID).provide(
        "BTC-USDT-SWAP", DEVELOPMENT_START + 450, segment_identity="x")
    assert all(frame.get("candle_close_ts") is None or frame["candle_close_ts"] <= payload["as_of"]
               for frame in payload["timeframes"].values())
    assert payload["timeframes"]["15m"]["confirmed"] is True


def test_historical_context_cache_is_strictly_bounded():
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID)
    template = provider.provide("BTC-USDT-SWAP", DEVELOPMENT_START, segment_identity="seed")
    def synthetic(_instrument, *, as_of, execution_timeframe):
        payload = copy.deepcopy(template); payload["as_of"] = as_of
        payload["context_identity"] = stable_hash({key: value for key, value in payload.items()
                                                    if key != "context_identity"})
        return payload
    provider.service.context = synthetic
    for offset in range(70):
        provider.provide("BTC-USDT-SWAP", DEVELOPMENT_START + offset, segment_identity="bounded")
    assert provider.cache_size == provider.MAX_CONTEXT_CACHE == 64


@pytest.mark.parametrize("as_of", [DEVELOPMENT_END, DEVELOPMENT_END + 900, 1_753_452_900])
def test_validation_and_oot_reads_are_refused(as_of):
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID)
    with pytest.raises(PermissionError):
        provider.provide("BTC-USDT-SWAP", as_of, segment_identity="x")


@pytest.mark.parametrize("start,end", [(DEVELOPMENT_START - 900, DEVELOPMENT_START + 900),
                                        (DEVELOPMENT_START, DEVELOPMENT_END + 900)])
def test_segment_boundary_is_hard(start, end):
    with pytest.raises(PermissionError):
        DevelopmentAccessGuard.require_segment(TimeSegmentV2("bad", start, end, "bad"))


def test_formal_chain_persists_router_state_context_and_geometry_identity():
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID)
    trial = trials_from_original_manifest(MANIFEST)[:1]
    result = StrategyEventReplayEngineV2_1(provider).replay(
        instrument="BTC-USDT-SWAP",
        confirmed_close_timestamps=range(DEVELOPMENT_START, DEVELOPMENT_START + 4 * 900, 900),
        trials=trial, segment=segment())
    assert result["state_ledger"][0]["version"] == STATE_ENGINE_VERSION
    assert all(row["strategy_setup_id"] and row["strategy_evaluation_id"] and row["level_identity"]
               for row in result["route_ledger"])
    assert all(row["router_geometry"] == row["execution_frozen_geometry"]
               for row in result["geometry_ledger"])
    assert all(max(row["source_candle_timestamps"]) <= row["context_timestamp"]
               for row in result["events"])


def test_parameter_direction_instrument_and_family_identity_isolation():
    trials = trials_from_original_manifest(MANIFEST)
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID)
    chosen = (trials[0], trials[1], trials[8], trials[16])
    result = StrategyEventReplayEngineV2_1(provider).replay(
        instrument="BTC-USDT-SWAP", confirmed_close_timestamps=[DEVELOPMENT_START],
        trials=chosen, segment=segment(end=DEVELOPMENT_START + 900))
    rows = result["route_ledger"]
    assert len({row["strategy_family_id"] for row in rows}) == 3
    assert len({row["strategy_setup_id"] for row in rows}) == len(rows)
    other = StrategyEventReplayEngineV2_1(
        HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID)).replay(
            instrument="ETH-USDT-SWAP", confirmed_close_timestamps=[DEVELOPMENT_START],
            trials=chosen[:1], segment=segment(end=DEVELOPMENT_START + 900))
    assert rows[0]["strategy_setup_id"] != other["route_ledger"][0]["strategy_setup_id"]


def test_checkpoint_resume_is_idempotent_and_segment_bound():
    trial = trials_from_original_manifest(MANIFEST)[:1]
    timestamps = list(range(DEVELOPMENT_START, DEVELOPMENT_START + 4 * 900, 900))
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID)
    engine = StrategyEventReplayEngineV2_1(provider)
    first = engine.replay(instrument="BTC-USDT-SWAP", confirmed_close_timestamps=timestamps,
                          trials=trial, segment=segment())
    resumed = engine.replay(instrument="BTC-USDT-SWAP", confirmed_close_timestamps=timestamps,
                            trials=trial, segment=segment(), checkpoint=first["checkpoint"])
    assert resumed["event_count"] == 0 and resumed["intents"] == []
    with pytest.raises(ValueError, match="segment identity"):
        engine.replay(instrument="BTC-USDT-SWAP", confirmed_close_timestamps=timestamps,
                      trials=trial, segment=segment(identity="other"), checkpoint=first["checkpoint"])


@pytest.mark.parametrize("token", ["._frame(", "._evaluate(", "def fallback_evaluator", "HistoricalContextLite",
                                    "SimplifiedContext", "BacktestOnlyFrame", "requests.get", "httpx",
                                    "openai", "create_order", "paper_api", "decision_engine"])
def test_formal_path_has_no_private_fallback_network_llm_or_paper(token):
    source = Path("dashboard/strategy_phase4a_router_repair.py").read_text(encoding="utf-8")
    source += Path("scripts/run_strategy_phase4a_router_repair.py").read_text(encoding="utf-8")
    assert token not in source


def test_router_exception_hard_fails_without_fallback():
    class BrokenRouter:
        def route(self, *_args, **_kwargs):
            raise ValueError("broken")
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=DATASET_ID)
    engine = StrategyEventReplayEngineV2_1(provider, router=BrokenRouter())
    with pytest.raises(RuntimeError, match="INVALID_ENGINE_OR_DATA"):
        engine.replay(instrument="BTC-USDT-SWAP", confirmed_close_timestamps=[DEVELOPMENT_START],
                      trials=trials_from_original_manifest(MANIFEST)[:1],
                      segment=segment(end=DEVELOPMENT_START + 900))


def test_old_formal_run_is_explicitly_invalidated_and_preserved():
    pointer = json.loads(Path("research/phase4a_latest_run.json").read_text(encoding="utf-8"))
    invalidation = json.loads(Path("research/phase4a_router_repair_invalidation_v1.json").read_text(encoding="utf-8"))
    assert pointer["status"] == invalidation["status"] == "INVALIDATED_ENGINE_BUG"
    assert invalidation["research_effect"] == "NONE"
    assert invalidation["artifact_preserved"] and invalidation["trial_ledger_preserved"]


@pytest.mark.parametrize("name", ["phase4a_engine_bug_001.json", "phase4a_engine_bug_002.json",
                                   "phase4a_engine_bug_003.json", "phase4a_engine_bug_004.json"])
def test_four_older_runs_remain_invalidated(name):
    assert json.loads((Path("research") / name).read_text(encoding="utf-8"))["affected_run_status"] == "INVALIDATED_ENGINE_BUG"


def test_no_frontend_or_production_scope_in_diff_contract():
    source = Path("scripts/run_strategy_phase4a_router_repair.py").read_text(encoding="utf-8")
    assert "frontend/" not in source and "collector" not in source and "deploy" not in source
