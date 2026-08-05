from __future__ import annotations

from copy import deepcopy
import inspect
import json
from pathlib import Path

import pytest

from dashboard.market_state_v2 import MarketStateEngineV2
from dashboard.strategy_phase4a import TimeSegmentV2
from dashboard.strategy_phase4a_router_repair import (
    DEVELOPMENT_END, DEVELOPMENT_START, HistoricalMarketContextV2Provider,
    stable_hash, trials_from_original_manifest,
)
from dashboard.strategy_phase4a_state_transition_repair import (
    BACKTEST_ENGINE_VERSION, REPLAY_CONTRACT_VERSION, REPLAY_ENGINE_VERSION,
    StrategyEventReplayEngineV2_2, comparison_lineage,
)
from dashboard.strategy_router_reachability_audit import _context
from scripts.run_strategy_phase4a_state_transition_repair import (
    DATASET, EXPECTED_DATASET, ORIGINAL_MANIFEST, full_chain_witnesses,
)


def _segment(end=DEVELOPMENT_START + 3 * 900):
    return TimeSegmentV2("DEVELOPMENT", DEVELOPMENT_START, end, "fixture")


def test_versions_and_compare_signature_are_frozen():
    assert REPLAY_ENGINE_VERSION == "strategy-event-replay-engine-v2.2"
    assert BACKTEST_ENGINE_VERSION == "strategy-backtest-engine-v2.2"
    assert REPLAY_CONTRACT_VERSION == "market-state-replay-contract-v1"
    assert list(inspect.signature(MarketStateEngineV2.compare).parameters) == [
        "self", "previous_context", "current_context"]


def test_first_context_evaluates_and_second_context_compares():
    class CountingState(MarketStateEngineV2):
        def __init__(self):
            self.direct_evaluations = 0; self.comparisons = 0; self.inside_compare = False
        def evaluate(self, context, *, previous_snapshot=None):
            if not self.inside_compare:
                self.direct_evaluations += 1
            return super().evaluate(context, previous_snapshot=previous_snapshot)
        def compare(self, previous_context, current_context):
            assert previous_context["as_of"] < current_context["as_of"]
            assert previous_context.get("context_identity")
            self.comparisons += 1; self.inside_compare = True
            try:
                return super().compare(previous_context, current_context)
            finally:
                self.inside_compare = False
    state = CountingState()
    result = StrategyEventReplayEngineV2_2(
        HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET),
        state_engine=state).replay(
            instrument="BTC-USDT-SWAP",
            confirmed_close_timestamps=[DEVELOPMENT_START, DEVELOPMENT_START + 900],
            trials=trials_from_original_manifest(ORIGINAL_MANIFEST)[:1], segment=_segment())
    assert state.direct_evaluations == result["evaluate_calls"] == 1
    assert state.comparisons == result["compare_calls"] == 1
    assert result["state_ledger"][0]["mode"] == "SEGMENT_INITIAL_EVALUATE"
    assert result["state_ledger"][1]["mode"] == "COMPARE"
    assert result["checkpoint"]["previous_context"]["context_identity"]
    assert result["checkpoint"]["previous_state"]["state_snapshot_identity"]


def test_gap_skips_compare_without_fabricating_transition():
    result = StrategyEventReplayEngineV2_2(
        HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET)).replay(
            instrument="BTC-USDT-SWAP",
            confirmed_close_timestamps=[DEVELOPMENT_START, DEVELOPMENT_START + 1800],
            trials=trials_from_original_manifest(ORIGINAL_MANIFEST)[:1],
            segment=_segment(end=DEVELOPMENT_START + 2700))
    assert result["evaluate_calls"] == 2
    assert result["compare_calls"] == 0
    assert result["compare_skipped_gap_calls"] == 1
    assert result["transition_ledger"] == []
    assert result["fallback_calls"] == 0


def test_compare_exception_is_a_hard_failure():
    class BrokenState(MarketStateEngineV2):
        def compare(self, previous_context, current_context):
            raise ValueError("broken")
    with pytest.raises(RuntimeError, match="INVALID_ENGINE_OR_DATA"):
        StrategyEventReplayEngineV2_2(
            HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET),
            state_engine=BrokenState()).replay(
                instrument="BTC-USDT-SWAP",
                confirmed_close_timestamps=[DEVELOPMENT_START, DEVELOPMENT_START + 900],
                trials=trials_from_original_manifest(ORIGINAL_MANIFEST)[:1], segment=_segment())


@pytest.mark.parametrize("family,direction", [
    ("TREND_PULLBACK", "LONG"), ("TREND_PULLBACK", "SHORT"),
    ("MA200_MEAN_REVERSION", "LONG"), ("MA200_MEAN_REVERSION", "SHORT")])
def test_full_chain_witness_uses_compare_and_reaches_trigger(family, direction):
    row = next(item for item in full_chain_witnesses(
        trials_from_original_manifest(ORIGINAL_MANIFEST))
        if (item["family"], item["direction"]) == (family, direction))
    assert row["result"] == "PASS" and row["compare_calls"] == 2
    assert [item["stage"] for item in row["trace"]] == ["WATCH", "ARMED", "TRIGGER_READY"]
    assert any("REJECTED" in item["interaction_types"] for item in row["trace"])
    assert all(item["geometry_valid"] for item in row["trace"])


def test_compare_lineage_uses_formal_state_and_context_identities():
    previous = _context(1_700_100_000, "LONG", "TREND_PULLBACK", "ARMED")
    current = _context(1_700_100_900, "LONG", "TREND_PULLBACK", "TRIGGER_READY")
    previous["price"]["value"] = 100.05; current["price"]["value"] = 101.0
    for context in (previous, current):
        context["context_identity"] = stable_hash(context)
    engine = MarketStateEngineV2(); previous_state = engine.evaluate(previous)
    comparison = engine.compare(previous, current)
    row = comparison_lineage(previous, current, previous_state, comparison)
    assert row["previous_context_identity"] == previous["context_identity"]
    assert row["current_context_identity"] == current["context_identity"]
    assert row["current_state_identity"] == comparison["current"]["state_snapshot_identity"]
    assert row["transition_identity"]
    assert any(item["current_interaction_type"] == "REJECTED"
               for item in row["facts"]["level_transition_facts"])


def test_phase4a3_invalidation_is_non_destructive_and_complete():
    value = json.loads(Path("research/phase4a3_router_native_replay_invalidation_v1.json").read_text())
    assert value["status"] == "INVALIDATED_ENGINE_BUG" and value["research_effect"] == "NONE"
    assert value["artifact_preserved"] and value["trial_ledger_preserved"]
    assert {"STATE_COMPARE_PATH_BYPASSED", "PREVIOUS_CONTEXT_NOT_PROVIDED",
            "CONFIRMATION_STATE_PRODUCER_MISSING"}.issubset(value["reasons"])


@pytest.mark.parametrize("as_of", [DEVELOPMENT_END, DEVELOPMENT_END + 900, 1_753_452_900])
def test_validation_and_oot_are_refused(as_of):
    with pytest.raises(PermissionError):
        HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET).provide(
            "BTC-USDT-SWAP", as_of, segment_identity="forbidden")


@pytest.mark.parametrize("token", ["create_order", "requests.get", "httpx", "openai", "frontend/", "collector", "deploy"])
def test_repair_path_has_no_network_llm_order_frontend_or_deploy(token):
    source = Path("dashboard/strategy_phase4a_state_transition_repair.py").read_text()
    source += Path("scripts/run_strategy_phase4a_state_transition_repair.py").read_text()
    assert token not in source
