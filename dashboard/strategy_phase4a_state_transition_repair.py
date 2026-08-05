"""Phase 4A5 Context-to-State transition replay contract.

This module changes orchestration only.  It deliberately delegates all market
state facts to :class:`MarketStateEngineV2`: the first context in a segment is
evaluated and every subsequent contiguous context is compared with its actual
predecessor.  No confirmation fact is manufactured here.
"""
from __future__ import annotations

from collections import OrderedDict
import time
from typing import Any, Callable, Mapping, Sequence

from .market_state_v2 import STATE_DEFINITION_VERSION, MarketStateEngineV2
from .strategy_phase4a import EntryIntentV2, ReplayEventV2, TimeSegmentV2
from .strategy_phase4a_router_repair import (
    DevelopmentAccessGuard, GEOMETRY_VERSION, HistoricalMarketContextV2Provider,
    LIFECYCLE_VERSION, RouterNativeTrialV2, StrategyEventReplayEngineV2_1,
    StrategyBacktestEngineV2_1,
    stable_hash,
)
from .strategy_router_v2 import StrategyRouterV2


REPLAY_ENGINE_VERSION = "strategy-event-replay-engine-v2.2"
BACKTEST_ENGINE_VERSION = "strategy-backtest-engine-v2.2"
REPLAY_CONTRACT_VERSION = "market-state-replay-contract-v1"
REPORT_VERSION = "strategy-phase4a-state-transition-repair-report-v1"
REPAIR_MANIFEST_VERSION = "phase4a-state-transition-repair-manifest-v1"


class StrategyBacktestEngineV2_2(StrategyBacktestEngineV2_1):
    """Version envelope; execution semantics remain frozen and unchanged."""

    version = BACKTEST_ENGINE_VERSION


def _future_source_timestamps(value: Any, as_of: int) -> list[int]:
    found: list[int] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"source_timestamp", "candle_close_ts", "start_timestamp", "end_timestamp"} and item is not None:
                try:
                    timestamp = int(item)
                except (TypeError, ValueError):
                    found.append(as_of + 1)
                else:
                    if timestamp > as_of:
                        found.append(timestamp)
            else:
                found.extend(_future_source_timestamps(item, as_of))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_future_source_timestamps(item, as_of))
    return found


def _level_key(item: Mapping[str, Any]) -> tuple[str, str, float]:
    return str(item.get("level_type")), str(item.get("timeframe")), float(item.get("boundary", 0))


def comparison_lineage(previous_context: Mapping[str, Any], current_context: Mapping[str, Any],
                       previous_state: Mapping[str, Any], comparison: Mapping[str, Any],
                       *, gap_status: str = "CONTIGUOUS") -> dict[str, Any]:
    """Persist identities and facts already emitted by the formal compare call."""
    current = comparison["current"]
    prior_levels = {_level_key(item): item for item in comparison["previous"].get("level_interactions", [])}
    level_changes: list[dict[str, Any]] = []
    for item in current.get("level_interactions", []):
        prior = prior_levels.get(_level_key(item))
        if not prior:
            continue
        fields = ("interaction_type", "reclaim_status", "current_stage", "confirmation_timestamp",
                  "reclaim_timestamp", "rejection_strength")
        if any(prior.get(field) != item.get(field) for field in fields):
            level_changes.append({
                "source_level_identity": stable_hash({
                    "type": item.get("level_type"), "timeframe": item.get("timeframe"),
                    "boundary": item.get("boundary"), "sources": item.get("source_timestamps"),
                }),
                "level_type": item.get("level_type"), "timeframe": item.get("timeframe"),
                "boundary": item.get("boundary"),
                "previous_interaction_type": prior.get("interaction_type"),
                "current_interaction_type": item.get("interaction_type"),
                "reclaim_status": item.get("reclaim_status"),
                "rejection_status": "REJECTED" if item.get("interaction_type") == "REJECTED" else "NOT_REJECTED",
                "current_stage": item.get("current_stage"),
                "confirmation_timestamp": item.get("confirmation_timestamp") or item.get("reclaim_timestamp"),
                "source_candle_timestamps": list(item.get("source_timestamps", [])),
                "data_quality": item.get("quality"),
            })
    facts = {
        "state_transitions": list(comparison.get("transitions", [])),
        "level_transition_facts": level_changes,
    }
    transition_identity = stable_hash({
        "contract": REPLAY_CONTRACT_VERSION,
        "previous_context_identity": previous_context["context_identity"],
        "current_context_identity": current_context["context_identity"],
        "previous_state_identity": previous_state["state_snapshot_identity"],
        "current_state_identity": current["state_snapshot_identity"],
        "compare_engine_version": comparison["version"],
        "definition_version": STATE_DEFINITION_VERSION,
        "facts": facts,
    })
    types = sorted({str(item.get("current_interaction_type")) for item in level_changes} |
                   {f"{item.get('from_state')}->{item.get('to_state')}" for item in comparison.get("transitions", [])})
    confirmations = [item["confirmation_timestamp"] for item in level_changes
                     if item.get("confirmation_timestamp") is not None]
    confirmations.extend(int(item["transition_timestamp"]) for item in comparison.get("transitions", [])
                         if item.get("transition_timestamp") is not None)
    return {
        "version": REPLAY_CONTRACT_VERSION,
        "instrument": current_context["instrument"],
        "previous_context_identity": previous_context["context_identity"],
        "current_context_identity": current_context["context_identity"],
        "previous_context_as_of": int(previous_context["as_of"]),
        "current_context_as_of": int(current_context["as_of"]),
        "previous_state_identity": previous_state["state_snapshot_identity"],
        "current_state_identity": current["state_snapshot_identity"],
        "compare_engine_version": comparison["version"],
        "transition_identity": transition_identity,
        "transition_type": types,
        "transition_confirmation_timestamp": max(confirmations) if confirmations else None,
        "facts": facts,
        "data_quality": current.get("quality", {}),
        "gap_status": gap_status,
    }


class StrategyEventReplayEngineV2_2(StrategyEventReplayEngineV2_1):
    """Router-native replay with distinct previous Context and State slots."""

    version = REPLAY_ENGINE_VERSION
    MAX_COMPARE_CACHE = 64

    def __init__(self, provider: HistoricalMarketContextV2Provider,
                 state_engine: MarketStateEngineV2 | None = None,
                 router: StrategyRouterV2 | None = None) -> None:
        super().__init__(provider, state_engine=state_engine, router=router)
        self.evaluate_calls = 0
        self.compare_calls = 0
        self.compare_cache_hits = 0
        self.compare_skipped_gap_calls = 0
        self.fallback_calls = 0
        self._compare_cache: OrderedDict[tuple[str, str, str, str, str, str], dict[str, Any]] = OrderedDict()

    @staticmethod
    def _is_gap(previous_context: Mapping[str, Any], current_context: Mapping[str, Any]) -> bool:
        # MarketStateEngineV2.compare has no explicit gap policy.  The frozen
        # execution cadence is 15m, so only adjacent confirmed closes compare.
        if int(current_context["as_of"]) - int(previous_context["as_of"]) != 900:
            return True
        frame = current_context.get("timeframes", {}).get("15m", {})
        quality = frame.get("quality", {})
        return (not frame.get("confirmed") or bool(quality.get("missing")) or
                bool(quality.get("stale")) or bool(quality.get("gaps")))

    def _compare(self, previous_context: Mapping[str, Any], current_context: Mapping[str, Any],
                 segment_identity: str) -> dict[str, Any]:
        key = (str(previous_context["context_identity"]), str(current_context["context_identity"]),
               self.state_engine.version, self.state_engine.definition_version,
               str(current_context["instrument"]), segment_identity)
        cached = self._compare_cache.get(key)
        if cached is not None:
            self.compare_cache_hits += 1
            self._compare_cache.move_to_end(key)
            return cached
        result = self.state_engine.compare(dict(previous_context), dict(current_context))
        if not isinstance(result, dict) or not isinstance(result.get("current"), dict):
            raise RuntimeError("INVALID_ENGINE_OR_DATA: compare did not return a current State snapshot")
        self.compare_calls += 1
        self._compare_cache[key] = result
        while len(self._compare_cache) > self.MAX_COMPARE_CACHE:
            self._compare_cache.popitem(last=False)
        return result

    def replay(self, *, instrument: str, confirmed_close_timestamps: Sequence[int],
               trials: Sequence[RouterNativeTrialV2], segment: TimeSegmentV2,
               checkpoint: Mapping[str, Any] | None = None,
               sinks: Mapping[str, Callable[[Mapping[str, Any]], None]] | None = None,
               retain_lineage: bool = True) -> dict[str, Any]:
        DevelopmentAccessGuard.require_segment(segment)
        if checkpoint and checkpoint.get("segment_identity") != segment.identity:
            raise ValueError("checkpoint segment identity mismatch")
        if checkpoint and checkpoint.get("dataset_identity") != self.provider.dataset_identity:
            raise ValueError("checkpoint dataset identity mismatch")
        routes = dict((checkpoint or {}).get("routes", {}))
        last_by_key = {k: int(v) for k, v in (checkpoint or {}).get("last_by_key", {}).items()}
        previous_context = dict(checkpoint["previous_context"]) if checkpoint and checkpoint.get("previous_context") else None
        previous_state = dict(checkpoint["previous_state"]) if checkpoint and checkpoint.get("previous_state") else None
        events: list[dict[str, Any]] = []
        intents: list[EntryIntentV2] = []
        event_count = 0
        ledgers: dict[str, list[dict[str, Any]]] = {name: [] for name in
            ("context", "state", "transition", "route", "lifecycle", "geometry")}
        started = time.perf_counter()
        replay_keys = [self._checkpoint_key(instrument, trial) for trial in trials]
        for as_of in sorted(set(int(value) for value in confirmed_close_timestamps)):
            if not segment.contains_close(as_of):
                continue
            if replay_keys and all(as_of <= last_by_key.get(key, -1) for key in replay_keys):
                continue
            try:
                current_context = self.provider.provide(instrument, as_of, segment_identity=segment.identity)
                if int(current_context["as_of"]) != as_of or _future_source_timestamps(current_context, as_of):
                    raise ValueError("context as_of/source timestamp causality violation")
                transition_row = None
                if previous_context is None:
                    current_state = self.state_engine.evaluate(current_context)
                    self.evaluate_calls += 1
                    state_mode = "SEGMENT_INITIAL_EVALUATE"
                elif self._is_gap(previous_context, current_context):
                    current_state = self.state_engine.evaluate(current_context)
                    self.evaluate_calls += 1
                    self.compare_skipped_gap_calls += 1
                    state_mode = "COMPARE_SKIPPED_DATA_GAP"
                else:
                    comparison = self._compare(previous_context, current_context, segment.identity)
                    current_state = comparison["current"]
                    transition_row = comparison_lineage(previous_context, current_context,
                                                        previous_state, comparison)
                    state_mode = "COMPARE"
            except Exception as exc:
                raise RuntimeError("INVALID_ENGINE_OR_DATA: Context/State compare V2 failed") from exc
            self.state_calculations += 1
            context_row = {"instrument": instrument, "as_of": as_of,
                           "context_identity": current_context["context_identity"],
                           "version": current_context["version"], "segment_identity": segment.identity}
            state_row = {"instrument": instrument, "as_of": as_of,
                         "state_snapshot_identity": current_state["state_snapshot_identity"],
                         "version": current_state["version"], "definition_version": current_state["definition_version"],
                         "mode": state_mode,
                         "previous_context_identity": previous_context.get("context_identity") if previous_context else None,
                         "previous_state_identity": previous_state.get("state_snapshot_identity") if previous_state else None,
                         "transition_identity": transition_row.get("transition_identity") if transition_row else None,
                         "interaction_types": [x.get("interaction_type") for x in current_state.get("level_interactions", [])],
                         "reclaim_statuses": [x.get("reclaim_status") for x in current_state.get("level_interactions", [])],
                         "stages": [x.get("current_stage") for x in current_state.get("level_interactions", [])]}
            for name, row in (("context", context_row), ("state", state_row), ("transition", transition_row)):
                if row is None:
                    continue
                if sinks and sinks.get(name):
                    sinks[name](row)
                if retain_lineage:
                    ledgers[name].append(row)
            for trial in trials:
                key = self._checkpoint_key(instrument, trial)
                if as_of <= last_by_key.get(key, -1):
                    continue
                previous_route = routes.get(key)
                try:
                    route = self.router.route(current_context, current_state, previous_route=previous_route,
                        family=trial.family, direction=trial.direction,
                        parameter_set_id=trial.parameter_set_id, parameter_set=trial.parameters)
                except Exception as exc:
                    raise RuntimeError(f"INVALID_ENGINE_OR_DATA: Router V2 failed for {key}") from exc
                self.router_evaluations += 1
                candidate = route["candidates"][0]
                identity = candidate["identity"]
                lifecycle_row = {
                    "instrument": instrument, "as_of": as_of, "trial_id": trial.trial_id,
                    "family": trial.family, "direction": trial.direction,
                    "parameter_set_id": trial.parameter_set_id, "stage": candidate["state"],
                    "previous_stage": (previous_route or {}).get("candidates", [{}])[0].get("state", "INELIGIBLE"),
                    "strategy_setup_id": identity["strategy_setup_id"],
                    "strategy_evaluation_id": identity["strategy_evaluation_id"],
                    "level_identity": identity["level_identity"],
                    "state_snapshot_identity": current_state["state_snapshot_identity"],
                    "transition_identity": transition_row.get("transition_identity") if transition_row else None,
                    "source_candle_timestamps": identity["source_candle_timestamps"],
                    "blockers": candidate["blockers"], "geometry": candidate["geometry"],
                }
                if sinks and sinks.get("lifecycle"):
                    sinks["lifecycle"]({
                        **{key: value for key, value in lifecycle_row.items()
                           if key not in {"blockers", "geometry"}},
                        "blockers": [item.get("code") for item in candidate["blockers"]],
                        "geometry_valid": bool(candidate["geometry"].get("valid")),
                    })
                if retain_lineage:
                    ledgers["lifecycle"].append(lifecycle_row)
                if route.get("transitions"):
                    event_count += 1
                    prior_stage = lifecycle_row["previous_stage"]
                    event = self._event(instrument, trial, current_context, current_state,
                                        route, candidate, prior_stage)
                    event["transition_identity"] = lifecycle_row["transition_identity"]
                    event["confirmation_evidence_code"] = [x.get("interaction_type") for x in current_state.get("level_interactions", [])
                                                             if x.get("interaction_type") in {"RECLAIMED", "REJECTED"}]
                    route_row = {**lifecycle_row,
                                 "route_snapshot_identity": route["route_snapshot_identity"],
                                 "context_identity": current_context["context_identity"],
                                 "router_version": route["version"]}
                    geometry_row = {"event_identity": event["event_identity"],
                                    "router_geometry": candidate["geometry"],
                                    "execution_frozen_geometry": candidate["geometry"],
                                    "geometry_version": GEOMETRY_VERSION,
                                    "source_level_identity": identity["level_identity"]}
                    for name, row in (("route", route_row), ("geometry", geometry_row)):
                        if sinks and sinks.get(name):
                            sinks[name](row)
                        if retain_lineage:
                            ledgers[name].append(row)
                    if sinks and sinks.get("event"):
                        sinks["event"](event)
                    if retain_lineage:
                        events.append(event)
                    if candidate["state"] == "TRIGGER_READY":
                        if not transition_row:
                            raise RuntimeError("INVALID_ENGINE_OR_DATA: TRIGGER_READY lacks compare lineage")
                        geometry = candidate["geometry"]
                        stop = geometry.get("invalidation_reference", {}).get("boundary")
                        target = geometry.get("trigger_boundary", {}).get("target_boundary")
                        if not geometry.get("valid") or stop is None or target is None:
                            raise RuntimeError("INVALID_ENGINE_OR_DATA: Router TRIGGER_READY geometry is incomplete")
                        replay_event = ReplayEventV2(
                            event["event_identity"], instrument, trial.family, trial.direction,
                            trial.parameter_set_id, event["lifecycle_from"], event["lifecycle_to"],
                            as_of, as_of, identity.get("setup_started_at"), identity.get("trigger_timestamp"),
                            tuple(identity["source_candle_timestamps"]), identity["level_identity"],
                            identity["strategy_setup_id"], identity["strategy_evaluation_id"],
                            route["route_snapshot_identity"], str(current_context["quality"]["overall_status"]),
                            tuple(item["code"] for item in candidate["blockers"]), geometry, self.version)
                        intents.append(EntryIntentV2(replay_event, trial.direction, float(stop), float(target),
                                                    int(geometry["maximum_holding_bars"]),
                                                    float(geometry["minimum_structural_reward_risk"])))
                routes[key] = route
                last_by_key[key] = as_of
            previous_context = current_context
            previous_state = current_state
        wall = time.perf_counter() - started
        checkpoint_out = {
            "version": "phase4a-state-transition-repair-checkpoint-v1",
            "dataset_identity": self.provider.dataset_identity, "segment_identity": segment.identity,
            "instrument": instrument, "previous_context": previous_context,
            "previous_state": previous_state, "routes": routes, "last_by_key": last_by_key,
        }
        return {
            "events": events, "intents": intents,
            **{f"{name}_ledger": rows for name, rows in ledgers.items()},
            "checkpoint": checkpoint_out, "wall_seconds": wall,
            "router_evaluations": self.router_evaluations,
            "event_count": event_count,
            "evaluations_per_second": self.router_evaluations / wall if wall else None,
            "context_evaluations": self.provider.calculations,
            "state_evaluations": self.state_calculations,
            "evaluate_calls": self.evaluate_calls, "compare_calls": self.compare_calls,
            "compare_cache_hits": self.compare_cache_hits,
            "compare_skipped_gap_calls": self.compare_skipped_gap_calls,
            "legacy_evaluator_calls": self.legacy_evaluator_calls,
            "fallback_calls": self.fallback_calls,
        }
