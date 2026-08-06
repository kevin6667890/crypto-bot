"""Router-native, Development-only Phase 4A replay.

This module is deliberately orchestration-only.  Market facts, state, route,
lifecycle and geometry are produced by the public V2 components.  There is no
private evaluator and no fallback path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import bisect
import sqlite3
import time
from typing import Any, Callable, Mapping, Sequence

from .market_context_v2 import (
    BoundedMarketDataReaderV2, CONTEXT_VERSION, MarketAnalysisContextV2,
    MarketContextServiceV2,
)
from .market_state_v2 import MarketStateEngineV2, STATE_DEFINITION_VERSION, STATE_ENGINE_VERSION
from .strategy_router_v2 import (
    DEFINITIONS_VERSION, LIFECYCLE_IDENTITY_CONTRACT_VERSION, ROUTER_VERSION,
    StrategyLifecycleV2, StrategyRouterV2, validate_lifecycle_identity,
)
from .strategy_phase4a import (
    AccountPolicyV2, CostPolicyV2, EntryIntentV2, ReplayEventV2,
    StrategyBacktestEngineV2 as AuditedStrategyBacktestEngineV2, TimeSegmentV2,
)


REPLAY_ENGINE_VERSION = "strategy-event-replay-engine-v2.1"
BACKTEST_ENGINE_VERSION = "strategy-backtest-engine-v2.1"
LIFECYCLE_VERSION = "strategy-lifecycle-v2"
GEOMETRY_VERSION = "strategy-geometry-v2"
LEDGER_VERSION = "strategy-phase4a-router-native-ledger-v1"
REPORT_VERSION = "strategy-phase4a-router-repair-report-v1"
REPAIR_MANIFEST_VERSION = "phase4a-router-repair-manifest-v1"
CHECKPOINT_SCHEMA_VERSION = "strategy-replay-checkpoint-v3"
DEVELOPMENT_START = 1_698_365_700
DEVELOPMENT_END = 1_739_681_100
OOT_START = 1_753_452_900
ALLOWED_FAMILIES = frozenset({"TREND_PULLBACK", "MA200_MEAN_REVERSION"})


def _checkpoint_error(code: str) -> None:
    raise ValueError(code)


def checkpoint_lifecycle_records(routes: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for key, route in routes.items():
        candidates = route.get("candidates", [])
        if not candidates:
            continue
        candidate = candidates[0]
        identity = candidate.get("identity", {})
        stage = candidate.get("stage", {})
        records[str(key)] = {
            "stage": candidate.get("state"),
            "strategy_setup_anchor_id": identity.get("strategy_setup_anchor_id"),
            "level_continuity_id": identity.get("level_continuity_id"),
            "lifecycle_setup_key": identity.get("lifecycle_setup_key"),
            "setup_started_at": stage.get("setup_started_at"),
            "trigger_timestamp": stage.get("trigger_timestamp"),
            "expiry": stage.get("expires_at"),
            "cooldown": stage.get("rearm_after"),
            "parameter_set_id": identity.get("parameter_set_id"),
            "family": candidate.get("family"),
            "direction": candidate.get("direction"),
        }
    return records


def validate_replay_checkpoint(checkpoint: Mapping[str, Any], *, dataset_identity: str,
                               segment_identity: str, instrument: str,
                               replay_engine_version: str,
                               trials: Sequence[RouterNativeTrialV2]) -> None:
    """Validate current checkpoints without migration, defaults, or fallback."""
    if "schema_version" not in checkpoint:
        _checkpoint_error("CHECKPOINT_SCHEMA_MISSING")
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        _checkpoint_error("CHECKPOINT_SCHEMA_MISMATCH")
    if checkpoint.get("lifecycle_identity_contract_version") != LIFECYCLE_IDENTITY_CONTRACT_VERSION:
        _checkpoint_error("CHECKPOINT_IDENTITY_CONTRACT_MISMATCH")
    if "created_at" not in checkpoint or "last_evaluated_ts" not in checkpoint:
        _checkpoint_error("CHECKPOINT_SCHEMA_MISMATCH")
    if checkpoint.get("dataset_identity") != dataset_identity:
        _checkpoint_error("CHECKPOINT_DATASET_MISMATCH")
    if checkpoint.get("segment_identity") != segment_identity:
        _checkpoint_error("CHECKPOINT_SEGMENT_MISMATCH")
    if checkpoint.get("instrument") != instrument:
        _checkpoint_error("CHECKPOINT_INSTRUMENT_MISMATCH")
    required_versions = {
        "replay_engine_version": replay_engine_version,
        "market_context_version": CONTEXT_VERSION,
        "market_state_version": STATE_ENGINE_VERSION,
        "router_version": ROUTER_VERSION,
        "definitions_version": DEFINITIONS_VERSION,
    }
    if any(checkpoint.get(name) != expected for name, expected in required_versions.items()):
        _checkpoint_error("CHECKPOINT_SCHEMA_MISMATCH")
    lifecycle = checkpoint.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        _checkpoint_error("CHECKPOINT_SCHEMA_MISMATCH")
    expected = {StrategyEventReplayEngineV2_1._checkpoint_key(instrument, trial): trial
                for trial in trials}
    if set(lifecycle) != set(expected):
        _checkpoint_error("CHECKPOINT_SCHEMA_MISMATCH")
    routes = checkpoint.get("routes")
    if not isinstance(routes, Mapping) or set(routes) != set(expected):
        _checkpoint_error("CHECKPOINT_SCHEMA_MISMATCH")
    for key, trial in expected.items():
        record = lifecycle[key]
        required = {"stage", "strategy_setup_anchor_id", "level_continuity_id",
                    "lifecycle_setup_key", "setup_started_at", "trigger_timestamp",
                    "expiry", "cooldown", "parameter_set_id", "family", "direction"}
        if not isinstance(record, Mapping) or not required.issubset(record):
            _checkpoint_error("CHECKPOINT_SCHEMA_MISMATCH")
        if (record.get("parameter_set_id"), record.get("family"), record.get("direction")) != (
                trial.parameter_set_id, trial.family, trial.direction):
            _checkpoint_error("CHECKPOINT_SCHEMA_MISMATCH")
        candidate = routes[key].get("candidates", [{}])[0]
        identity = candidate.get("identity", {})
        try:
            validate_lifecycle_identity(identity, error_code="CHECKPOINT_RAW_LIFECYCLE_KEY")
        except ValueError:
            _checkpoint_error("CHECKPOINT_RAW_LIFECYCLE_KEY")
        if (record.get("strategy_setup_anchor_id") != identity.get("strategy_setup_anchor_id") or
                record.get("lifecycle_setup_key") != identity.get("lifecycle_setup_key")):
            _checkpoint_error("CHECKPOINT_SCHEMA_MISMATCH")


def replay_checkpoint_payload(*, dataset_identity: str, segment_identity: str,
                              instrument: str, replay_engine_version: str,
                              routes: Mapping[str, Mapping[str, Any]],
                              last_by_key: Mapping[str, int], **state: Any) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "lifecycle_identity_contract_version": LIFECYCLE_IDENTITY_CONTRACT_VERSION,
        "replay_engine_version": replay_engine_version,
        "market_context_version": CONTEXT_VERSION,
        "market_state_version": STATE_ENGINE_VERSION,
        "router_version": ROUTER_VERSION,
        "definitions_version": DEFINITIONS_VERSION,
        "dataset_identity": dataset_identity,
        "segment_identity": segment_identity,
        "instrument": instrument,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_evaluated_ts": max(last_by_key.values(), default=None),
        "lifecycle": checkpoint_lifecycle_records(routes),
        "routes": dict(routes), "last_by_key": dict(last_by_key),
        **state,
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RouterNativeTrialV2:
    trial_id: str
    family: str
    direction: str
    parameter_set_id: str
    parameters: dict[str, Any]
    config_hash: str

    @classmethod
    def from_manifest(cls, item: Mapping[str, Any]) -> "RouterNativeTrialV2":
        if item["family"] not in ALLOWED_FAMILIES:
            raise PermissionError("breakout and failed-breakout families are forbidden in Phase 4A3")
        return cls(str(item["trial_id"]), str(item["family"]), str(item["direction"]),
                   str(item["parameter_set_id"]), dict(item["parameters"]), str(item["config_hash"]))


class DevelopmentAccessGuard:
    """Exclusive Development upper boundary; warm-up is read-only and cannot emit state."""

    @staticmethod
    def require_as_of(as_of: int) -> None:
        if int(as_of) >= DEVELOPMENT_END:
            raise PermissionError("Validation/OOT read refused")

    @staticmethod
    def require_segment(segment: TimeSegmentV2) -> None:
        if segment.start_ts < DEVELOPMENT_START or segment.end_ts > DEVELOPMENT_END:
            raise PermissionError("only the frozen Development segment may be replayed")


class HistoricalMarketContextV2Provider:
    """Offline adapter over immutable canonical OHLCV and the production V2 algorithm."""

    version = "historical-market-context-v2-provider-v1"
    MAX_CONTEXT_CACHE = 64

    def __init__(self, database: Path | str, *, dataset_identity: str) -> None:
        self.database = Path(database).resolve()
        if not self.database.is_file():
            raise FileNotFoundError(self.database)
        self.dataset_identity = str(dataset_identity)
        # No microstructure repository is injected: flow therefore propagates
        # as MISSING.  The reader is SQLite read-only and never calls an API.
        self.reader = HistoricalOHLCVReaderV2(self.database)
        self.service = MarketContextServiceV2(self.reader)
        self.calculations = 0
        self.cache_hits = 0
        self._cache: OrderedDict[tuple[str, int, str, str, str], dict[str, Any]] = OrderedDict()

    def provide(self, instrument: str, as_of: int, *, segment_identity: str) -> dict[str, Any]:
        DevelopmentAccessGuard.require_as_of(as_of)
        key = (self.dataset_identity, instrument, int(as_of), CONTEXT_VERSION, segment_identity)
        cached = self._cache.get(key)
        if cached is not None:
            if cached["context_identity"] != stable_hash({k: v for k, v in cached.items() if k != "context_identity"}):
                raise RuntimeError("cached context identity mismatch")
            self.cache_hits += 1
            self._cache.move_to_end(key)
            return cached
        context = self.service.context(instrument, as_of=int(as_of), execution_timeframe="15m")
        if context.get("version") != CONTEXT_VERSION:
            raise RuntimeError("formal MarketAnalysisContextV2 schema/version mismatch")
        if any(int(frame["candle_close_ts"]) > int(as_of) for frame in context["timeframes"].values()
               if frame.get("candle_close_ts") is not None):
            raise RuntimeError("unconfirmed/future higher-timeframe candle became visible")
        if context.get("flow", {}).get("price_cvd_combination", {}).get("data_quality") != "MISSING":
            raise RuntimeError("historical CVD must remain MISSING without an injected source")
        self._cache[key] = context
        while len(self._cache) > self.MAX_CONTEXT_CACHE:
            self._cache.popitem(last=False)
        self.calculations += 1
        return context

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def provide_model(self, instrument: str, as_of: int, *, segment_identity: str) -> MarketAnalysisContextV2:
        payload = self.provide(instrument, as_of, segment_identity=segment_identity)
        return MarketAnalysisContextV2(**{key: payload[key] for key in MarketAnalysisContextV2.__dataclass_fields__})


class HistoricalOHLCVReaderV2(BoundedMarketDataReaderV2):
    """Bounded immutable candle cache; it never materializes future state or indicators."""

    def __init__(self, database: Path | str) -> None:
        super().__init__(database, microstructure_db=None)
        self._partitions: dict[tuple[str, str], tuple[list[int], list[dict[str, Any]]]] = {}

    def candles(self, instrument: str, timeframe: str, as_of: int, limit: int) -> list[dict[str, Any]]:
        DevelopmentAccessGuard.require_as_of(as_of)
        if timeframe == "1W":
            return []
        canonical = instrument.removesuffix("-SWAP")
        key = (canonical, timeframe)
        if key not in self._partitions:
            widths = {"15m": 900, "1H": 3600, "4H": 14_400, "1D": 86_400}
            connection = sqlite3.connect(f"file:{self.paper_db.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            selected = connection.execute(
                """SELECT ts,open,high,low,close,volume,confirmed,source
                   FROM historical_candles WHERE instrument=? AND timeframe=?
                     AND ts<? AND confirmed=1 ORDER BY ts""",
                (canonical, timeframe, DEVELOPMENT_END)).fetchall()
            connection.close()
            rows = [{**dict(row), "candle_close_ts": int(row["ts"]) + widths[timeframe]}
                    for row in selected]
            self._partitions[key] = ([int(row["candle_close_ts"]) for row in rows], rows)
        closes, rows = self._partitions[key]
        end = bisect.bisect_right(closes, int(as_of))
        start = max(0, end - int(limit))
        return rows[start:end]


class StrategyBacktestEngineV2_1(AuditedStrategyBacktestEngineV2):
    """Version envelope around the execution implementation audited in Phase 4A2."""

    version = BACKTEST_ENGINE_VERSION


class StrategyEventReplayEngineV2_1:
    """Causal V2 orchestration.  Router errors hard-fail the owning trial."""

    version = REPLAY_ENGINE_VERSION

    def __init__(self, provider: HistoricalMarketContextV2Provider,
                 state_engine: MarketStateEngineV2 | None = None,
                 router: StrategyRouterV2 | None = None) -> None:
        self.provider = provider
        self.state_engine = state_engine or MarketStateEngineV2()
        self.router = router or StrategyRouterV2()
        self.state_calculations = 0
        self.router_evaluations = 0
        self.legacy_evaluator_calls = 0

    @staticmethod
    def _checkpoint_key(instrument: str, trial: RouterNativeTrialV2) -> str:
        return ":".join((instrument, trial.family, trial.direction, trial.parameter_set_id))

    def replay(self, *, instrument: str, confirmed_close_timestamps: Sequence[int],
               trials: Sequence[RouterNativeTrialV2], segment: TimeSegmentV2,
               checkpoint: Mapping[str, Any] | None = None,
               sinks: Mapping[str, Callable[[Mapping[str, Any]], None]] | None = None,
               retain_lineage: bool = True) -> dict[str, Any]:
        DevelopmentAccessGuard.require_segment(segment)
        if checkpoint is not None:
            validate_replay_checkpoint(
                checkpoint, dataset_identity=self.provider.dataset_identity,
                segment_identity=segment.identity, instrument=instrument,
                replay_engine_version=self.version, trials=trials,
            )
        routes: dict[str, dict[str, Any]] = dict((checkpoint or {}).get("routes", {}))
        last_by_key: dict[str, int] = {k: int(v) for k, v in (checkpoint or {}).get("last_by_key", {}).items()}
        prior_state: dict[str, Any] | None = dict(checkpoint["previous_state"]) if checkpoint and checkpoint.get("previous_state") else None
        events: list[dict[str, Any]] = []
        intents: list[EntryIntentV2] = []
        context_ledgers: list[dict[str, Any]] = []
        state_ledgers: list[dict[str, Any]] = []
        route_ledgers: list[dict[str, Any]] = []
        geometry_ledgers: list[dict[str, Any]] = []
        event_count = 0
        started = time.perf_counter()
        replay_keys = [self._checkpoint_key(instrument, trial) for trial in trials]
        for as_of in sorted(set(int(value) for value in confirmed_close_timestamps)):
            if as_of < segment.start_ts or as_of >= segment.end_ts:
                continue
            if replay_keys and all(as_of <= last_by_key.get(key, -1) for key in replay_keys):
                continue
            try:
                context = self.provider.provide(instrument, as_of, segment_identity=segment.identity)
                state = self.state_engine.evaluate(context, previous_snapshot=prior_state)
            except Exception as exc:
                raise RuntimeError("INVALID_ENGINE_OR_DATA: Context/State V2 failed") from exc
            self.state_calculations += 1
            prior_state = state
            context_row = {"instrument": instrument, "as_of": as_of,
                           "context_identity": context["context_identity"],
                           "version": context["version"]}
            state_row = {"instrument": instrument, "as_of": as_of,
                         "state_snapshot_identity": state["state_snapshot_identity"],
                         "version": state["version"]}
            if sinks and sinks.get("context"): sinks["context"](context_row)
            if sinks and sinks.get("state"): sinks["state"](state_row)
            if retain_lineage:
                context_ledgers.append(context_row); state_ledgers.append(state_row)
            for trial in trials:
                key = self._checkpoint_key(instrument, trial)
                if as_of <= last_by_key.get(key, -1):
                    continue
                previous_route = routes.get(key)
                try:
                    route = self.router.route(
                        context, state, previous_route=previous_route,
                        family=trial.family, direction=trial.direction,
                        parameter_set_id=trial.parameter_set_id,
                        parameter_set=trial.parameters,
                        segment_identity=segment.identity,
                    )
                except Exception as exc:
                    raise RuntimeError(f"INVALID_ENGINE_OR_DATA: Router V2 failed for {key}") from exc
                self.router_evaluations += 1
                candidate = route["candidates"][0]
                identity = candidate["identity"]
                transition = route.get("transitions", [])
                if transition:
                    event_count += 1
                    route_row = {
                        "instrument": instrument, "as_of": as_of, "trial_id": trial.trial_id,
                        "parameter_set_id": trial.parameter_set_id,
                        "route_snapshot_identity": route["route_snapshot_identity"],
                        "strategy_family_id": identity["strategy_family_id"],
                        "strategy_setup_id": identity["strategy_setup_id"],
                        "strategy_setup_anchor_id": identity.get("strategy_setup_anchor_id"),
                        "strategy_evaluation_id": identity["strategy_evaluation_id"],
                        "level_identity": identity["level_identity"],
                        "level_continuity_id": identity.get("level_continuity_id"),
                        "lifecycle_setup_key": identity.get("lifecycle_setup_key"),
                        "context_identity": context["context_identity"],
                        "state_snapshot_identity": state["state_snapshot_identity"],
                        "config_hash": identity["configuration_hash"],
                        "router_version": route["version"],
                    }
                    prior_candidate = (previous_route or {}).get("candidates", [{}])[0]
                    prior_stage = prior_candidate.get("state", "INELIGIBLE")
                    event = self._event(instrument, trial, context, state, route, candidate, prior_stage)
                    geometry_row = {
                        "event_identity": event["event_identity"],
                        "router_geometry": candidate["geometry"],
                        "execution_frozen_geometry": candidate["geometry"],
                        "geometry_version": GEOMETRY_VERSION,
                        "source_level_identity": identity["level_identity"],
                    }
                    if sinks and sinks.get("route"): sinks["route"](route_row)
                    if sinks and sinks.get("event"): sinks["event"](event)
                    if sinks and sinks.get("geometry"): sinks["geometry"](geometry_row)
                    if retain_lineage:
                        route_ledgers.append(route_row); events.append(event); geometry_ledgers.append(geometry_row)
                    if candidate["state"] == "TRIGGER_READY":
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
                            route["route_snapshot_identity"], str(context["quality"]["overall_status"]),
                            tuple(item["code"] for item in candidate["blockers"]), geometry, self.version)
                        intents.append(EntryIntentV2(
                            replay_event, trial.direction, float(stop), float(target),
                            int(geometry["maximum_holding_bars"]),
                            float(geometry["minimum_structural_reward_risk"])))
                routes[key] = route
                last_by_key[key] = as_of
        wall = time.perf_counter() - started
        checkpoint_out = replay_checkpoint_payload(
            dataset_identity=self.provider.dataset_identity,
            segment_identity=segment.identity, instrument=instrument,
            replay_engine_version=self.version, routes=routes,
            last_by_key=last_by_key,
            previous_context_timestamp=prior_state.get("as_of") if prior_state else None,
            previous_state_identity=prior_state.get("state_snapshot_identity") if prior_state else None,
            previous_state=prior_state,
        )
        return {
            "events": events, "intents": intents, "context_ledger": context_ledgers,
            "state_ledger": state_ledgers, "route_ledger": route_ledgers,
            "geometry_ledger": geometry_ledgers, "checkpoint": checkpoint_out,
            "wall_seconds": wall, "router_evaluations": self.router_evaluations,
            "event_count": event_count,
            "evaluations_per_second": self.router_evaluations / wall if wall else None,
            "legacy_evaluator_calls": self.legacy_evaluator_calls,
        }

    @staticmethod
    def _event(instrument: str, trial: RouterNativeTrialV2, context: Mapping[str, Any],
               state: Mapping[str, Any], route: Mapping[str, Any], candidate: Mapping[str, Any],
               prior_stage: str) -> dict[str, Any]:
        identity = candidate["identity"]
        transition = route["transitions"][0]
        return {
            "ledger_version": LEDGER_VERSION,
            "event_identity": transition["idempotency_key"],
            "instrument": instrument, "family": trial.family, "direction": trial.direction,
            "parameter_set_id": trial.parameter_set_id, "config_hash": identity["configuration_hash"],
            "lifecycle_from": transition.get("from_state", prior_stage),
            "lifecycle_to": transition["to_state"], "context_timestamp": int(context["as_of"]),
            "strategy_family_id": identity["strategy_family_id"],
            "strategy_setup_id": identity["strategy_setup_id"],
            "strategy_setup_anchor_id": identity.get("strategy_setup_anchor_id"),
            "strategy_evaluation_id": identity["strategy_evaluation_id"],
            "route_snapshot_identity": route["route_snapshot_identity"],
            "state_snapshot_identity": state["state_snapshot_identity"],
            "context_identity": context["context_identity"],
            "level_identity": identity["level_identity"],
            "level_continuity_id": identity.get("level_continuity_id"),
            "lifecycle_setup_key": identity.get("lifecycle_setup_key"),
            "market_context_version": context["version"], "market_state_version": state["version"],
            "router_version": route["version"], "definitions_version": route["definitions_version"],
            "lifecycle_version": LIFECYCLE_VERSION, "geometry_version": GEOMETRY_VERSION,
            "source_candle_timestamps": identity["source_candle_timestamps"],
            "evidence_codes": [item["code"] for item in candidate["supporting_evidence"]],
            "blockers": [item["code"] for item in candidate["blockers"]],
            "limitations": candidate["limitations"], "quality_status": context["quality"]["overall_status"],
            "geometry": candidate["geometry"],
        }


def trials_from_original_manifest(path: Path | str) -> tuple[RouterNativeTrialV2, ...]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    trials = tuple(RouterNativeTrialV2.from_manifest(item) for item in manifest["trials"])
    if len(trials) != 32 or len({item.parameter_set_id for item in trials}) != 32:
        raise ValueError("frozen trial space must contain exactly 32 unique parameter sets")
    return trials


def direct_router_chain(provider: HistoricalMarketContextV2Provider, *, instrument: str,
                        timestamps: Sequence[int], trials: Sequence[RouterNativeTrialV2],
                        segment: TimeSegmentV2) -> list[dict[str, Any]]:
    """Independent timestamp loop used by the exhaustive canary comparator."""
    state_engine = MarketStateEngineV2()
    router = StrategyRouterV2()
    previous_state = None
    previous_routes: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    for as_of in sorted(set(int(item) for item in timestamps)):
        if not segment.contains_close(as_of):
            continue
        context = provider.provide(instrument, as_of, segment_identity=segment.identity)
        state = state_engine.evaluate(context, previous_snapshot=previous_state)
        previous_state = state
        for trial in trials:
            key = StrategyEventReplayEngineV2_1._checkpoint_key(instrument, trial)
            route = router.route(context, state, previous_route=previous_routes.get(key),
                                 family=trial.family, direction=trial.direction,
                                 parameter_set_id=trial.parameter_set_id,
                                 parameter_set=trial.parameters)
            if route["transitions"]:
                candidate = route["candidates"][0]
                prior = previous_routes.get(key, {}).get("candidates", [{}])[0].get("state", "INELIGIBLE")
                events.append(StrategyEventReplayEngineV2_1._event(
                    instrument, trial, context, state, route, candidate, prior))
            previous_routes[key] = route
    return events


def compare_canary(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = {
        "stage": ("lifecycle_to",),
        "setup_identity": ("strategy_setup_id",),
        "evaluation_identity": ("strategy_evaluation_id",),
        "level_identity": ("level_identity",),
        "geometry": ("geometry",),
        "blockers": ("blockers",),
        "source_timestamps": ("source_candle_timestamps",),
    }
    ordered_left = sorted(left, key=lambda item: item["event_identity"])
    ordered_right = sorted(right, key=lambda item: item["event_identity"])
    output: dict[str, Any] = {"left_events": len(left), "right_events": len(right), "differences": []}
    for label, (field,) in fields.items():
        matches = sum(a.get(field) == b.get(field) for a, b in zip(ordered_left, ordered_right))
        denominator = max(len(ordered_left), len(ordered_right))
        output[f"{label}_match_rate"] = 1.0 if denominator == 0 else matches / denominator
    if len(ordered_left) != len(ordered_right) or any(output[f"{name}_match_rate"] != 1.0 for name in fields):
        output["differences"] = [{"left": a, "right": b} for a, b in zip(ordered_left, ordered_right) if a != b]
        raise AssertionError("canary mismatch")
    return output
