"""Deterministic, research-only strategy routing over V2 context and state.

This module is intentionally pure: it has no database, network, order, LLM,
paper scheduler, or legacy decision-engine dependency.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any, Iterable, Mapping


ROUTER_VERSION = "strategy-router-v2"
DEFINITIONS_VERSION = "strategy-family-definitions-v2.1"
PARAMETER_SET_VERSION = "strategy-router-parameters-v2.1"
FAMILY_VERSIONS = {
    "TREND_PULLBACK": "trend-pullback-v2",
    "MA200_MEAN_REVERSION": "ma200-mean-reversion-v2",
    "BREAKOUT_CONTINUATION": "breakout-continuation-v2",
    "FAILED_BREAKOUT_REVERSAL": "failed-breakout-reversal-v2",
    "NO_TRADE": "no-trade-policy-v2",
}
FAMILIES = tuple(FAMILY_VERSIONS)
TRADE_FAMILIES = FAMILIES[:-1]
STAGES = (
    "INELIGIBLE", "WATCH", "ARMED", "TRIGGER_READY",
    "TRIGGERED_RESEARCH_ONLY", "INVALIDATED", "EXPIRED",
    "COOLDOWN_RESEARCH_ONLY",
)
NO_TRADE_CODES = (
    "INSUFFICIENT_DATA", "STALE_EXECUTION_DATA", "HTF_CONFLICT",
    "MID_RANGE_NOISE", "NO_STRUCTURAL_LEVEL", "NO_CONFIRMATION",
    "TOO_CLOSE_TO_OPPOSING_LEVEL", "VOLATILITY_TOO_LOW",
    "VOLATILITY_TOO_HIGH", "EXTENDED_FROM_STRUCTURE",
    "BREAKOUT_NOT_CONFIRMED", "MA200_TOUCH_WITHOUT_RECLAIM",
    "FLOW_CONFLICT", "SETUP_EXPIRED", "DUPLICATE_SETUP",
    "INVALID_GEOMETRY", "NO_STRATEGY_MATCH",
)
TIMEFRAME_SECONDS = {"15m": 900, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
TIMEFRAME_ROLES = {
    "execution": "15m", "setup": "1H", "environment": "4H",
    "higher_context": ("1D", "1W"),
}
PARAMETERS: dict[str, Any] = {
    "trigger_ready_score": 72.0,
    "trigger_dimension_minimum": 15.0,
    "minimum_structural_reward_risk": 1.25,
    "opposing_level_buffer_r": 1.25,
    "maximum_wait_bars": {
        "TREND_PULLBACK": 12, "MA200_MEAN_REVERSION": 12,
        "BREAKOUT_CONTINUATION": 16, "FAILED_BREAKOUT_REVERSAL": 12,
    },
    "maximum_holding_bars": {
        "TREND_PULLBACK": 64, "MA200_MEAN_REVERSION": 48,
        "BREAKOUT_CONTINUATION": 80, "FAILED_BREAKOUT_REVERSAL": 48,
    },
    "cooldown_bars": 4,
}


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple, set)):
        values = [_canonical(item) for item in value]
        return sorted(values, key=lambda item: json.dumps(item, sort_keys=True)) if isinstance(value, set) else values
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("identity accepts finite values only")
        return float(format(value, ".12g"))
    return value


def stable_hash(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StrategyIdentityV2:
    strategy_family_id: str
    strategy_setup_id: str
    strategy_evaluation_id: str
    configuration_hash: str
    family: str
    direction: str
    strategy_version: str
    definitions_version: str
    parameter_set_version: str
    instrument: str
    execution_timeframe: str
    setup_timeframe: str
    environment_timeframe: str
    context_timeframes: tuple[str, ...]
    source_candle_timestamps: tuple[int, ...]
    level_identity: str
    setup_started_at: int | None
    trigger_timestamp: int | None


@dataclass(frozen=True)
class StrategyEvidenceV2:
    code: str
    dimension: str
    timeframe: str
    classification: str
    strength: str
    source_timestamp: int | None
    detail: str


@dataclass(frozen=True)
class StrategyBlockerV2:
    code: str
    timeframe: str
    evidence: tuple[str, ...]
    source_timestamp: int | None
    blocking: bool
    release_condition: str


@dataclass(frozen=True)
class StrategyGeometryV2:
    valid: bool
    setup_zone: dict[str, Any]
    trigger_boundary: dict[str, Any]
    confirmation_rule: tuple[str, ...]
    invalidation_reference: dict[str, Any]
    stop_reference_type: str
    target_reference_types: tuple[str, ...]
    maximum_wait_bars: int
    maximum_holding_bars: int
    minimum_structural_reward_risk: float
    structural_reward_risk: float | None
    entry_timing: str
    intrabar_policy_placeholder: str
    gap_policy_placeholder: str
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class StrategyStageV2:
    state: str
    setup_started_at: int | None
    trigger_timestamp: int | None
    expires_at: int | None
    rearm_after: int | None


@dataclass(frozen=True)
class StrategyTransitionV2:
    strategy_setup_id: str
    from_state: str
    to_state: str
    transition_timestamp: int
    reason: str
    idempotency_key: str


@dataclass(frozen=True)
class NoTradeReasonV2:
    code: str
    timeframe: str
    evidence: tuple[str, ...]
    source_timestamp: int | None
    temporary: bool
    release_condition: str


@dataclass(frozen=True)
class StrategyCandidateV2:
    family: str
    direction: str
    strategy_version: str
    parameter_set_version: str
    state: str
    stage: StrategyStageV2
    score: float
    score_breakdown: dict[str, float]
    evidence_strength: float
    supporting_evidence: tuple[StrategyEvidenceV2, ...]
    conflicting_evidence: tuple[StrategyEvidenceV2, ...]
    blockers: tuple[StrategyBlockerV2, ...]
    next_confirmation: tuple[str, ...]
    geometry: StrategyGeometryV2
    source_timestamps: tuple[int, ...]
    data_quality: dict[str, Any]
    identity: StrategyIdentityV2
    identity_hash: str
    limitations: tuple[str, ...]
    selection_status: str = "NOT_SELECTED"
    selection_reason: str = ""
    parameter_progress: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyRouteSnapshotV2:
    version: str
    definitions_version: str
    instrument: str
    as_of: int
    market_context_version: str
    market_state_version: str
    execution_timeframe: str
    timeframe_roles: dict[str, Any]
    primary_route: dict[str, Any] | None
    alternatives: tuple[dict[str, Any], ...]
    candidates: tuple[dict[str, Any], ...]
    no_trade: dict[str, Any]
    quality: dict[str, Any]
    transitions: tuple[dict[str, Any], ...]
    disclaimer: str
    route_snapshot_identity: str | None = None
    parameter_set_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyBacktestSpecificationV2:
    version: str
    family: str
    direction: str
    required_timeframes: tuple[str, ...]
    feature_versions: tuple[str, ...]
    market_state_versions: tuple[str, ...]
    parameter_ranges: dict[str, tuple[Any, ...]]
    parameter_combination_count: int
    entry_timing: str
    stop_rules: tuple[str, ...]
    exit_rules: tuple[str, ...]
    maximum_wait: int
    maximum_hold: int
    rearm: str
    cost_assumptions_placeholder: dict[str, Any]
    intrabar_ordering_policy: str
    benchmark_policy_placeholder: str
    allowed_assets: tuple[str, ...]
    allowed_timeframes: tuple[str, ...]
    required_data_quality: str
    setup_identity_rules: tuple[str, ...]


def _fact(context: Mapping[str, Any], timeframe: str, group: str, name: str) -> Mapping[str, Any]:
    return context.get("timeframes", {}).get(timeframe, {}).get(group, {}).get(name, {}) or {}


def _value(context: Mapping[str, Any], timeframe: str, group: str, name: str) -> float | str | None:
    item = _fact(context, timeframe, group, name)
    if not item.get("available") or item.get("stale") or item.get("value") is None:
        return None
    return item["value"]


def _source_ts(context: Mapping[str, Any], timeframe: str) -> int | None:
    frame = context.get("timeframes", {}).get(timeframe, {})
    value = frame.get("candle_close_ts")
    return int(value) if value is not None else None


def _all_source_timestamps(context: Mapping[str, Any], state: Mapping[str, Any]) -> tuple[int, ...]:
    timestamps: set[int] = set()
    for frame in context.get("timeframes", {}).values():
        if frame.get("confirmed") and frame.get("candle_close_ts") is not None:
            timestamps.add(int(frame["candle_close_ts"]))
    price_ts = context.get("price", {}).get("source_timestamp")
    if price_ts is not None:
        timestamps.add(int(price_ts))
    for level in state.get("level_interactions", []):
        timestamps.update(int(item) for item in level.get("source_timestamps", []) if item is not None)
        timestamps.update(int(level[key]) for key in ("breakout_timestamp", "confirmation_timestamp", "reclaim_timestamp") if level.get(key) is not None)
    return tuple(sorted(timestamps))


def _future_timestamps(value: Any, as_of: int, path: str = "input") -> list[str]:
    output: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if (key.endswith("timestamp") or key.endswith("_ts") or key in {"as_of", "source_timestamps", "source_candle_timestamps"}) and item is not None:
                values = item if isinstance(item, (list, tuple)) else (item,)
                for timestamp in values:
                    try:
                        if int(timestamp) > as_of:
                            output.append(f"{child}={timestamp}")
                    except (TypeError, ValueError):
                        output.append(f"{child}=invalid")
            else:
                output.extend(_future_timestamps(item, as_of, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            output.extend(_future_timestamps(item, as_of, f"{path}[{index}]"))
    return output


def _state(state: Mapping[str, Any], timeframe: str) -> str:
    return str(state.get("timeframes", {}).get(timeframe, {}).get("primary_state", "UNKNOWN"))


def _momentum(state: Mapping[str, Any], timeframe: str) -> str:
    return str(state.get("timeframes", {}).get(timeframe, {}).get("momentum_state", "UNAVAILABLE"))


def _overlays(state: Mapping[str, Any]) -> set[str]:
    result = set(state.get("overlays", []))
    for frame in state.get("timeframes", {}).values():
        result.update(frame.get("overlays", []))
    return result


def _quality(context: Mapping[str, Any]) -> tuple[dict[str, Any], float, list[StrategyBlockerV2], list[str]]:
    quality = dict(context.get("quality") or {})
    status = str(quality.get("overall_status", "MISSING"))
    execution = context.get("timeframes", {}).get(str(context.get("execution_timeframe", "15m")), {})
    blockers: list[StrategyBlockerV2] = []
    limitations: list[str] = []
    if not execution or not execution.get("confirmed") or execution.get("quality", {}).get("missing"):
        blockers.append(StrategyBlockerV2("INSUFFICIENT_DATA", "15m", ("confirmed execution candle unavailable",), _source_ts(context, "15m"), True, "wait for a confirmed 15m candle"))
    if execution.get("quality", {}).get("stale"):
        blockers.append(StrategyBlockerV2("STALE_EXECUTION_DATA", "15m", ("execution data is stale",), _source_ts(context, "15m"), True, "wait for fresh confirmed execution data"))
    score = {"AVAILABLE": 10.0, "PARTIAL": 7.0, "STALE": 3.0, "MISSING": 0.0}.get(status, 0.0)
    missing = set(quality.get("missing_sources", []))
    if missing & {"cvd", "oi", "funding", "basis"}:
        limitations.append("flow missing; price structure remains evaluable at reduced completeness")
    if quality.get("partial_sources"):
        limitations.append("partial sources are weak evidence only")
    if quality.get("stale_sources"):
        limitations.append("stale sources are excluded from scoring")
    return quality, score, blockers, limitations


def _evidence(code: str, dimension: str, timeframe: str, ok: bool, ts: int | None,
              detail: str, strength: str = "STRONG") -> StrategyEvidenceV2:
    return StrategyEvidenceV2(code, dimension, timeframe, "supporting" if ok else "conflicting", strength, ts, detail)


def _candidate_level(state: Mapping[str, Any], types: Iterable[str], timeframes: Iterable[str] = ("1H", "4H"),
                     approaches: Iterable[str] = ("FROM_ABOVE", "FROM_BELOW"),
                     context: Mapping[str, Any] | None = None) -> Mapping[str, Any] | None:
    allowed_types, allowed_frames, allowed_approaches = set(types), set(timeframes), set(approaches)
    def semantic(item: Mapping[str, Any]) -> tuple[set[str], set[str]]:
        item_types, item_frames = {str(item.get("level_type"))}, {str(item.get("timeframe"))}
        if item.get("level_type") == "CONFLUENCE_ZONE" and context:
            for source_level in context.get("levels", []):
                if abs(float(source_level.get("value", math.inf)) - float(item.get("boundary", 0))) <= max(abs(float(item.get("boundary", 0))) * 1e-8, 1e-8):
                    for source in source_level.get("confluence_sources", []):
                        parts = str(source).split(":", 1)
                        if len(parts) == 2:
                            item_frames.add(parts[0]); item_types.add(parts[1])
        return item_types, item_frames
    items = []
    for item in state.get("level_interactions", []):
        item_types, item_frames = semantic(item)
        if (item_types & allowed_types and item_frames & allowed_frames
                and item.get("approach_direction") in allowed_approaches
                and item.get("quality") not in {"UNAVAILABLE", "STALE"}):
            items.append(item)
    return min(items, key=lambda item: abs(float(item.get("distance_pct", 999)))) if items else None


def _geometry(family: str, direction: str, context: Mapping[str, Any],
              state: Mapping[str, Any], level: Mapping[str, Any] | None, *,
              trigger_ready: bool = False,
              parameters: Mapping[str, Any] = PARAMETERS) -> StrategyGeometryV2:
    wait, hold = PARAMETERS["maximum_wait_bars"][family], PARAMETERS["maximum_holding_bars"][family]
    if not level:
        return StrategyGeometryV2(False, {}, {}, (), {}, "confirmed swing", (), wait, hold,
                                  PARAMETERS["minimum_structural_reward_risk"], None,
                                  "NEXT_CONFIRMED_15M_OPEN_AFTER_TRIGGER", "DEFINE_IN_PHASE_4",
                                  "DEFINE_IN_PHASE_4", ("NO_STRUCTURAL_LEVEL",))
    boundary = float(level["boundary"])
    zone_low, zone_high = float(level["zone_low"]), float(level["zone_high"])
    atr = _value(context, "15m", "volatility", "atr14")
    configured_buffer = parameters.get("zone_buffer_atr")
    if atr is not None and configured_buffer is not None:
        distance = float(atr) * float(configured_buffer)
        zone_low, zone_high = boundary - distance, boundary + distance
    invalidation = zone_low if direction == "LONG" else zone_high
    stop_type = {
        "TREND_PULLBACK": "confirmed swing", "MA200_MEAN_REVERSION": "MA zone opposite boundary",
        "BREAKOUT_CONTINUATION": "breakout/retest boundary", "FAILED_BREAKOUT_REVERSAL": "failed breakout extreme",
    }[family]
    targets = ("next confirmed resistance/support", "prior swing")
    opposing: list[tuple[float, float, str, str]] = []
    for other in state.get("level_interactions", []):
        other_boundary = float(other.get("boundary", boundary))
        if direction == "LONG" and other_boundary > boundary or direction == "SHORT" and other_boundary < boundary:
            opposing.append((abs(other_boundary - boundary), other_boundary,
                              str(other.get("level_type")), str(other.get("timeframe"))))
    risk = abs(boundary - invalidation)
    nearest = min(opposing, default=None)
    reward = nearest[0] if nearest else None
    structural_r = reward / risk if reward is not None and risk > 0 else None
    limitations: list[str] = []
    valid = risk > 0
    minimum_r = float(parameters.get("minimum_r", PARAMETERS["minimum_structural_reward_risk"]))
    if structural_r is not None and structural_r < minimum_r:
        valid = False; limitations.append("TOO_CLOSE_TO_OPPOSING_LEVEL")
    if trigger_ready and structural_r is None:
        valid = False; limitations.append("target-side structural level unavailable")
    confirmation = {
        "TREND_PULLBACK": ("confirmed 15m reclaim of EMA20 or local structure", "momentum recovery"),
        "MA200_MEAN_REVERSION": ("confirmed close reclaims MA200 zone or local boundary", "rejection structure plus momentum recovery"),
        "BREAKOUT_CONTINUATION": ("confirmed breakout", "confirmed retest holds", "15m continuation"),
        "FAILED_BREAKOUT_REVERSAL": ("confirmed re-entry into range", "confirmed reverse structure break"),
    }[family]
    target_boundary = nearest[1] if nearest else None
    target_type = nearest[2] if nearest else None
    target_timeframe = nearest[3] if nearest else None
    return StrategyGeometryV2(valid, {"reference": level.get("level_type"), "timeframe": level.get("timeframe"), "zone_low": zone_low, "zone_high": zone_high},
                              {"reference": level.get("level_type"), "timeframe": "15m", "boundary": boundary,
                               "target_boundary": target_boundary, "target_reference": target_type,
                               "target_timeframe": target_timeframe}, confirmation,
                              {"reference": stop_type, "boundary": invalidation}, stop_type, targets, wait, hold,
                              minimum_r, round(structural_r, 4) if structural_r is not None else None,
                              "NEXT_CONFIRMED_15M_OPEN_AFTER_TRIGGER", "DEFINE_IN_PHASE_4", "DEFINE_IN_PHASE_4", tuple(limitations))


def _flow(context: Mapping[str, Any], direction: str) -> tuple[list[StrategyEvidenceV2], list[StrategyEvidenceV2], float, list[str]]:
    supporting: list[StrategyEvidenceV2] = []
    conflicting: list[StrategyEvidenceV2] = []
    limitations: list[str] = []
    used = 0
    for key, prefix in (("price_oi_combination", "OI"), ("price_cvd_combination", "CVD")):
        item = context.get("flow", {}).get(key, {}) or {}
        quality, observed = str(item.get("data_quality", "MISSING")), str(item.get("state", "INSUFFICIENT_DATA"))
        if quality == "STALE":
            limitations.append(f"{prefix} stale and excluded")
            continue
        if quality == "MISSING" or observed == "INSUFFICIENT_DATA":
            limitations.append(f"{prefix} unavailable")
            continue
        expected_up = direction == "LONG"
        confirming = (expected_up and observed in {"PRICE_UP_OI_UP", "PRICE_UP_OI_DOWN", "PRICE_UP_CVD_UP"}) or (not expected_up and observed in {"PRICE_DOWN_OI_UP", "PRICE_DOWN_OI_DOWN", "PRICE_DOWN_CVD_DOWN"})
        strength = "WEAK" if quality == "PARTIAL" else "CONTEXT"
        ev = _evidence(f"{prefix}_{'CONFIRMING_PRICE' if confirming else 'DIVERGING_PRICE'}", "Data quality", "15m", confirming, item.get("end_timestamp"), observed, strength)
        (supporting if confirming else conflicting).append(ev)
        used += 1 if quality == "AVAILABLE" else .5
    return supporting, conflicting, min(2.0, used), limitations


class StrategyLifecycleV2:
    """Pure lifecycle reducer; persisted candidates can be supplied after restart."""

    @staticmethod
    def advance(candidate: StrategyCandidateV2, previous: Mapping[str, Any] | None, now: int) -> tuple[StrategyStageV2, StrategyTransitionV2 | None]:
        desired = candidate.state
        prior = str((previous or {}).get("state", "INELIGIBLE"))
        prior_stage = (previous or {}).get("stage", {}) or {}
        started = prior_stage.get("setup_started_at") or candidate.stage.setup_started_at
        trigger_ts = prior_stage.get("trigger_timestamp") or candidate.stage.trigger_timestamp
        expires_at = (int(started) + candidate.geometry.maximum_wait_bars * TIMEFRAME_SECONDS["15m"]) if started else None
        rearm_after = prior_stage.get("rearm_after")
        reason = "deterministic rule evaluation"
        if desired in {"WATCH", "ARMED"} and expires_at is not None and now > expires_at:
            desired, reason = "EXPIRED", "maximum wait bars elapsed"
        elif prior == "TRIGGER_READY" and desired == "TRIGGER_READY":
            desired, trigger_ts, reason = "TRIGGERED_RESEARCH_ONLY", trigger_ts or now, "research trigger recorded after confirmed candle"
        elif prior == "TRIGGERED_RESEARCH_ONLY":
            desired, rearm_after, reason = "COOLDOWN_RESEARCH_ONLY", now + PARAMETERS["cooldown_bars"] * TIMEFRAME_SECONDS["15m"], "same setup cooldown"
        elif prior == "COOLDOWN_RESEARCH_ONLY" and rearm_after and now < int(rearm_after):
            desired, reason = "COOLDOWN_RESEARCH_ONLY", "cooldown not elapsed"
        elif prior == "COOLDOWN_RESEARCH_ONLY" and desired not in {"INVALIDATED", "INELIGIBLE"}:
            desired, reason = "WATCH", "cooldown elapsed; setup must re-form"
        stage = StrategyStageV2(desired, int(started) if started else None, int(trigger_ts) if trigger_ts else None,
                                expires_at, int(rearm_after) if rearm_after else None)
        transition = None
        if prior != desired:
            key = stable_hash({"setup": candidate.identity.strategy_setup_id, "from": prior, "to": desired, "at": now})
            transition = StrategyTransitionV2(candidate.identity.strategy_setup_id, prior, desired, now, reason, key)
        return stage, transition


class StrategyRouterV2:
    version = ROUTER_VERSION
    definitions_version = DEFINITIONS_VERSION

    def route(self, context: Mapping[str, Any], state: Mapping[str, Any], *,
              previous_route: Mapping[str, Any] | None = None,
              family: str | None = None, direction: str | None = None,
              parameter_set_id: str | None = None,
              parameter_set: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if context.get("version") != "market-analysis-context-v2":
            raise ValueError("context version must be market-analysis-context-v2")
        if state.get("version") != "market-state-engine-v2":
            raise ValueError("state version must be market-state-engine-v2")
        if context.get("instrument") != state.get("instrument") or int(context.get("as_of", 0)) != int(state.get("as_of", -1)):
            raise ValueError("context and state instrument/as_of must match")
        as_of = int(context["as_of"])
        violations = _future_timestamps({"context": context, "state": state}, as_of)
        if violations:
            raise ValueError("source timestamp later than as_of: " + ", ".join(sorted(violations)[:8]))
        execution = str(context.get("execution_timeframe", "15m"))
        if execution != "15m":
            raise ValueError("strategy-router-v2 currently supports execution_timeframe=15m")
        if (family is None) != (direction is None) or (parameter_set is None) != (parameter_set_id is None):
            raise ValueError("family/direction and parameter_set_id/parameter_set must be supplied together")
        if family is not None:
            if family not in TRADE_FAMILIES or direction not in {"LONG", "SHORT"}:
                raise ValueError("unsupported family/direction")
            if parameter_set is None or parameter_set_id is None:
                raise ValueError("explicit frozen parameter set is required for bounded routing")
            allowed = dict(backtest_parameter_ranges_v2()[family])
            if set(parameter_set) != set(allowed) or any(parameter_set[name] not in allowed[name] for name in allowed):
                raise ValueError("parameter set is outside the frozen Router V2 specification")
        previous_candidates = {(item["family"], item["direction"]): item for item in (previous_route or {}).get("candidates", [])}
        candidates: list[StrategyCandidateV2] = []
        transitions: list[StrategyTransitionV2] = []
        requested = ((family, direction),) if family is not None else tuple(
            (item_family, item_direction) for item_family in TRADE_FAMILIES for item_direction in ("LONG", "SHORT"))
        for item_family, item_direction in requested:
            prior = previous_candidates.get((item_family, item_direction))
            candidate = self._evaluate(item_family, item_direction, context, state,
                                       parameter_set_id=parameter_set_id,
                                       parameter_set=parameter_set,
                                       previous_candidate=prior)
            if prior and (
                prior.get("identity", {}).get("level_identity") != candidate.identity.level_identity
                or prior.get("identity", {}).get("configuration_hash") != candidate.identity.configuration_hash
                or prior.get("identity", {}).get("instrument") != candidate.identity.instrument
            ):
                prior = None
            if prior:
                    prior_identity = prior["identity"]
                    started = prior.get("stage", {}).get("setup_started_at")
                    evaluation_id = stable_hash({
                        "setup_id": prior_identity["strategy_setup_id"], "as_of": as_of,
                        "source_timestamps": candidate.source_timestamps,
                        "trigger_timestamp": candidate.stage.trigger_timestamp,
                    })
                    identity = StrategyIdentityV2(**{
                        **candidate.identity.__dict__,
                        "strategy_setup_id": prior_identity["strategy_setup_id"],
                        "strategy_evaluation_id": evaluation_id,
                        "setup_started_at": started,
                    })
                    candidate = StrategyCandidateV2(**{
                        **candidate.__dict__, "identity": identity,
                        "identity_hash": stable_hash(asdict(identity)),
                        "stage": StrategyStageV2(candidate.state, started,
                            candidate.stage.trigger_timestamp,
                            int(started) + candidate.geometry.maximum_wait_bars * 900 if started else None,
                            candidate.stage.rearm_after),
                    })
            stage, transition = StrategyLifecycleV2.advance(candidate, prior, as_of)
            candidate = StrategyCandidateV2(**{**candidate.__dict__, "state": stage.state, "stage": stage})
            candidates.append(candidate)
            if transition:
                transitions.append(transition)
        eligible = [item for item in candidates if item.state != "INELIGIBLE"]
        routable = [item for item in eligible if item.state in {
            "WATCH", "ARMED", "TRIGGER_READY", "TRIGGERED_RESEARCH_ONLY"}]
        ranked = sorted(routable, key=self._priority, reverse=True)
        conflict = len({item.direction for item in routable if item.state == "TRIGGER_READY"}) > 1
        primary = ranked[0] if ranked and not conflict else None
        selected_candidates: list[StrategyCandidateV2] = []
        for item in candidates:
            selected = primary is not None and item.identity_hash == primary.identity_hash
            reason = "highest event-specific completion with valid quality and geometry" if selected else (
                "opposing TRIGGER_READY routes conflict" if conflict else "lower lifecycle/event priority or completion")
            selected_candidates.append(StrategyCandidateV2(**{**item.__dict__, "selection_status": "PRIMARY" if selected else "ALTERNATIVE", "selection_reason": reason}))
        primary = next((item for item in selected_candidates if item.selection_status == "PRIMARY"), None)
        reasons = self._no_trade_reasons(context, state, selected_candidates, primary, conflict)
        active_no_trade = primary is None or primary.state not in {"TRIGGER_READY", "TRIGGERED_RESEARCH_ONLY"}
        quality = dict(context.get("quality") or {})
        quality["degraded"] = quality.get("overall_status") != "AVAILABLE"
        quality["routing_compute_only"] = True
        snapshot = StrategyRouteSnapshotV2(
            ROUTER_VERSION, DEFINITIONS_VERSION, str(context["instrument"]), as_of,
            str(context["version"]), str(state["version"]), execution, dict(TIMEFRAME_ROLES),
            asdict(primary) if primary else None,
            tuple(asdict(item) for item in selected_candidates if item.selection_status != "PRIMARY" and item.state != "INELIGIBLE"),
            tuple(asdict(item) for item in selected_candidates),
            {"active": active_no_trade, "strategy_version": FAMILY_VERSIONS["NO_TRADE"], "reasons": [asdict(item) for item in reasons]},
            quality, tuple(asdict(item) for item in transitions),
            "研究策略路由，不是实时交易建议，当前未连接Paper或实盘执行。",
        ).to_dict()
        snapshot["parameter_set_id"] = parameter_set_id
        snapshot["route_snapshot_identity"] = stable_hash(
            {key: value for key, value in snapshot.items() if key != "route_snapshot_identity"})
        return snapshot

    def _evaluate(self, family: str, direction: str, context: Mapping[str, Any], state: Mapping[str, Any], *,
                  parameter_set_id: str | None = None,
                  parameter_set: Mapping[str, Any] | None = None,
                  previous_candidate: Mapping[str, Any] | None = None) -> StrategyCandidateV2:
        effective_parameters = dict(PARAMETERS)
        if parameter_set:
            effective_parameters.update(parameter_set)
        quality, quality_points, blockers, limitations = _quality(context)
        supporting: list[StrategyEvidenceV2] = []
        conflicting: list[StrategyEvidenceV2] = []
        next_confirmation: list[str] = []
        env = structure = setup = trigger = 0.0
        overlays = _overlays(state)
        htf = {_state(state, frame) for frame in ("1D", "4H")}
        side_trend, opposite_trend = ("TREND_UP", "TREND_DOWN") if direction == "LONG" else ("TREND_DOWN", "TREND_UP")
        approach = "FROM_ABOVE" if direction == "LONG" else "FROM_BELOW"
        level: Mapping[str, Any] | None = None
        desired = "INELIGIBLE"
        invalidated = False
        parameter_progress: dict[str, Any] = {}

        if family == "TREND_PULLBACK":
            level_types = {"EMA20", "MA60", "BREAKOUT_RETEST", "SWING_LOW" if direction == "LONG" else "SWING_HIGH"}
            level = _candidate_level(state, level_types, approaches=(approach,), context=context)
            environment_ok = side_trend in htf and _state(state, "4H") != opposite_trend
            slope = _value(context, "4H", "trend", "ma200_slope")
            slope_ok = slope is not None and (float(slope) >= 0 if direction == "LONG" else float(slope) <= 0)
            setup_state = _state(state, "1H")
            pullback_ok = setup_state in ({"TREND_DOWN", "TRANSITION_DOWN", "TRANSITION_MIXED"} if direction == "LONG" else {"TREND_UP", "TRANSITION_UP", "TRANSITION_MIXED"}) or state.get("cross_timeframe", {}).get("normal_pullback")
            pressure = state.get("primary_state_code") == ("MAJOR_RESISTANCE_TEST" if direction == "LONG" else "MAJOR_SUPPORT_TEST")
            invalidated = opposite_trend == _state(state, "4H") or ("BREAKDOWN_CONFIRMED" if direction == "LONG" else "BREAKOUT_CONFIRMED") in overlays
            env = 25 if environment_ok and slope_ok and not pressure else 15 if environment_ok else 0
            structure = 25 if level and not pressure else 12 if environment_ok else 0
            touched = bool(level and level.get("interaction_type") in {"TOUCHING", "INSIDE_ZONE", "RETESTING", "REJECTED", "RECLAIMED"})
            cooled = _momentum(state, "1H") in ({"OVERSOLD", "NEUTRAL"} if direction == "LONG" else {"OVERBOUGHT", "NEUTRAL"})
            setup = 20 if touched and pullback_ok and cooled else 10 if pullback_ok or level else 0
            recovered = (_momentum(state, "15m") in
                         ({"RECOVERING_FROM_OVERSOLD", "BULLISH"} if direction == "LONG"
                          else {"ROLLING_OVER_FROM_OVERBOUGHT", "BEARISH"}))
            reclaimed = bool(level and level.get("interaction_type") in {"RECLAIMED", "REJECTED"})
            trigger = 20 if reclaimed and recovered and not pressure else 8 if touched else 0
            desired = "TRIGGER_READY" if trigger == 20 else "ARMED" if touched and environment_ok else "WATCH" if environment_ok or level else "INELIGIBLE"
            next_confirmation.append("15m confirmed reclaim and momentum recovery" if direction == "LONG" else "15m confirmed rejection and momentum rollover")
            if pressure:
                blockers.append(StrategyBlockerV2("TOO_CLOSE_TO_OPPOSING_LEVEL", "4H", (str(state.get("primary_state_code")),), _source_ts(context, "4H"), True, "wait for structural space away from the opposing level"))

        elif family == "MA200_MEAN_REVERSION":
            level = _candidate_level(state, {"MA200"}, approaches=(approach,), context=context)
            slope = _value(context, str(level.get("timeframe", "4H")) if level else "4H", "trend", "ma200_slope")
            slope_ok = slope is not None and (float(slope) >= 0 if direction == "LONG" else float(slope) <= 0)
            strong_opposite = opposite_trend == _state(state, "4H") and state.get("primary_state_code") in {"HTF_DOWNTREND_CONTINUATION", "HTF_UPTREND_CONTINUATION"}
            confluence = bool(level and (int(level.get("touch_count", 0)) >= 2 or level.get("level_type") == "CONFLUENCE_ZONE"))
            if level:
                boundary = float(level["boundary"])
                confluence = confluence or any(item.get("type") in ({"SWING_LOW", "ROLLING_LOW", "VPVR_VAL", "VPVR_POC", "PREVIOUS_BREAKOUT"} if direction == "LONG" else {"SWING_HIGH", "ROLLING_HIGH", "VPVR_VAH", "PREVIOUS_BREAKDOWN"}) and abs(float(item.get("value", 0))-boundary) <= max(abs(boundary)*.003, 1e-9) for item in context.get("levels", []))
            touching = bool(level and level.get("interaction_type") in {"TOUCHING", "INSIDE_ZONE", "RECLAIMED", "REJECTED"})
            oversold = _momentum(state, "15m") == ("OVERSOLD" if direction == "LONG" else "OVERBOUGHT") or _momentum(state, "1H") == ("OVERSOLD" if direction == "LONG" else "OVERBOUGHT")
            recovered = _momentum(state, "15m") in ({"RECOVERING_FROM_OVERSOLD", "BULLISH"} if direction == "LONG" else {"ROLLING_OVER_FROM_OVERBOUGHT", "BEARISH"})
            raw_reclaimed = bool(level and (level.get("reclaim_status") not in {None, "", "NOT_RECLAIMED"} or level.get("interaction_type") == "REJECTED"))
            current_level_identity = stable_hash({"type": level.get("level_type"), "timeframe": level.get("timeframe"), "boundary": level.get("boundary"), "sources": level.get("source_timestamps")}) if level else stable_hash({"level": "NONE"})
            same_level = bool(previous_candidate and previous_candidate.get("identity", {}).get("level_identity") == current_level_identity)
            prior_reclaims = int((previous_candidate or {}).get("parameter_progress", {}).get("reclaim_confirmations", 0)) if same_level else 0
            reclaim_confirmations = prior_reclaims + 1 if raw_reclaimed else 0
            required_reclaims = int(effective_parameters.get("reclaim_bars", 1))
            reclaimed = raw_reclaimed and reclaim_confirmations >= required_reclaims
            parameter_progress = {"reclaim_confirmations": reclaim_confirmations,
                                  "required_reclaim_bars": required_reclaims}
            wick = _value(context, "15m", "volume", "lower_wick_percentage" if direction == "LONG" else "upper_wick_percentage")
            rejection = wick is not None and float(wick) >= 30
            invalidated = bool(level and level.get("current_stage") in ({"MA200_BREAKDOWN_CONFIRMED"} if direction == "LONG" else {"MA200_RECLAIM_CONFIRMED"})) or strong_opposite
            env = 25 if level and slope_ok and not strong_opposite else 12 if level else 0
            structure = 25 if level and confluence else 12 if level else 0
            setup = 20 if touching and oversold else 10 if level else 0
            trigger = 20 if reclaimed and rejection and recovered else 8 if touching else 0
            desired = "TRIGGER_READY" if trigger == 20 and confluence else "ARMED" if touching and oversold else "WATCH" if level else "INELIGIBLE"
            next_confirmation.append("confirmed MA200-zone reclaim plus rejection structure; touch or wick alone is insufficient")
            if level and not confluence:
                blockers.append(StrategyBlockerV2("NO_STRUCTURAL_LEVEL", str(level.get("timeframe")), ("MA200 has no independent confluence",), _source_ts(context, str(level.get("timeframe"))), True, "wait for confirmed swing, VPVR, rolling level, or prior break confluence"))
            if touching and not reclaimed:
                blockers.append(StrategyBlockerV2("MA200_TOUCH_WITHOUT_RECLAIM", str(level.get("timeframe")), ("touch is not confirmation",), _source_ts(context, str(level.get("timeframe"))), True, "wait for confirmed reclaim/rejection structure"))

        elif family == "BREAKOUT_CONTINUATION":
            level = _candidate_level(state, {"SWING_HIGH", "ROLLING_HIGH", "RANGE_HIGH"} if direction == "LONG" else {"SWING_LOW", "ROLLING_LOW", "RANGE_LOW"}, context=context)
            stages = {str(item.get("current_stage")) for item in state.get("level_interactions", [])}
            candidate = ("BREAKOUT_CANDIDATE" if direction == "LONG" else "BREAKDOWN_CANDIDATE") in stages
            confirmed = ("BREAKOUT_CONFIRMED" if direction == "LONG" else "BREAKDOWN_CONFIRMED") in stages or ("BREAKOUT_CONFIRMED" if direction == "LONG" else "BREAKDOWN_CONFIRMED") in overlays
            retest = ("BREAKOUT_RETESTING" if direction == "LONG" else "BREAKDOWN_RETESTING") in stages or bool(level and level.get("interaction_type") == "RETESTING")
            first_breach = bool(level and level.get("current_stage") in {"BREAKOUT_CANDIDATE", "BREAKDOWN_CANDIDATE"}) and not confirmed
            continuation = retest and (_state(state, "15m") == side_trend or _momentum(state, "15m") in ({"RECOVERING_FROM_OVERSOLD", "BULLISH"} if direction == "LONG" else {"ROLLING_OVER_FROM_OVERBOUGHT", "BEARISH"}))
            environment_ok = opposite_trend != _state(state, "4H") and (any("COMPRESSION" in item for item in overlays) or _state(state, "1H").startswith("RANGE") or _state(state, "1H") in {"TRANSITION_UP", "TRANSITION_DOWN", "TRANSITION_MIXED"})
            failed = ("FAILED_BREAKOUT_CANDIDATE" if direction == "LONG" else "FAILED_BREAKDOWN_CANDIDATE") in stages
            invalidated = failed or bool(level and level.get("reclaim_status") in {"RECLAIMED_INTO_PRIOR_RANGE", "RECLAIMED_ABOVE_PRIOR_SUPPORT"})
            env = 25 if environment_ok else 10 if level else 0
            structure = 25 if level and int(level.get("touch_count", 0)) > 0 else 12 if level else 0
            setup = 20 if confirmed else 12 if candidate else 8 if environment_ok else 0
            trigger = 20 if confirmed and continuation else 0 if first_breach else 8 if confirmed else 0
            desired = "TRIGGER_READY" if trigger == 20 else "ARMED" if confirmed else "WATCH" if environment_ok or candidate else "INELIGIBLE"
            next_confirmation.append("second confirmed close, boundary retest, then 15m continuation; first breach never triggers")
            if first_breach:
                blockers.append(StrategyBlockerV2("BREAKOUT_NOT_CONFIRMED", "1H", ("first boundary breach only",), _source_ts(context, "1H"), True, "wait for boundary hold and confirmed retest"))

        else:  # FAILED_BREAKOUT_REVERSAL
            level = _candidate_level(state, {"SWING_HIGH", "ROLLING_HIGH", "RANGE_HIGH"} if direction == "SHORT" else {"SWING_LOW", "ROLLING_LOW", "RANGE_LOW"}, context=context)
            stages = {str(item.get("current_stage")) for item in state.get("level_interactions", [])}
            prerequisite = ("FAILED_BREAKOUT_CANDIDATE" if direction == "SHORT" else "FAILED_BREAKDOWN_CANDIDATE") in stages
            reentered = bool(level and level.get("reclaim_status") in ({"RECLAIMED_INTO_PRIOR_RANGE"} if direction == "SHORT" else {"RECLAIMED_ABOVE_PRIOR_SUPPORT"}))
            reverse_momentum = (_state(state, "15m") == ("TREND_DOWN" if direction == "SHORT" else "TREND_UP") or
                                _momentum(state, "15m") in ({"ROLLING_OVER_FROM_OVERBOUGHT", "BEARISH"} if direction == "SHORT" else {"RECOVERING_FROM_OVERSOLD", "BULLISH"}))
            invalidated = bool(level and level.get("interaction_type") == "BROKEN" and not reentered)
            env = 25 if prerequisite and reentered else 8 if level else 0
            structure = 25 if prerequisite and level else 10 if level else 0
            setup = 20 if prerequisite and reentered else 0
            trigger = 20 if prerequisite and reentered and reverse_momentum else 8 if prerequisite else 0
            desired = "TRIGGER_READY" if trigger == 20 else "ARMED" if prerequisite and reentered else "WATCH" if prerequisite else "INELIGIBLE"
            next_confirmation.append("confirmed reverse local-structure break after confirmed range re-entry")

        if invalidated:
            desired = "INVALIDATED"
            blockers.append(StrategyBlockerV2("INVALIDATED_STRUCTURE", "4H", ("versioned invalidation rule met",), _source_ts(context, "4H"), True, "a new setup identity must form"))
        flow_support, flow_conflict, flow_points, flow_limits = _flow(context, direction)
        supporting.extend(flow_support); conflicting.extend(flow_conflict); limitations.extend(flow_limits)
        for name, points, maximum, timeframe in (("ENVIRONMENT", env, 25, "4H"), ("STRUCTURE", structure, 25, "1H"), ("SETUP", setup, 20, "1H"), ("TRIGGER", trigger, 20, "15m")):
            item = _evidence(f"{family}_{direction}_{name}", name.title(), timeframe, points == maximum, _source_ts(context, timeframe), f"{points}/{maximum}")
            (supporting if points == maximum else conflicting).append(item)
        score_breakdown = {"environment": env, "structure": structure, "setup": setup, "trigger": trigger, "data_quality": quality_points}
        score = round(sum(score_breakdown.values()), 2)
        geometry = _geometry(family, direction, context, state, level,
                             trigger_ready=desired == "TRIGGER_READY",
                             parameters=effective_parameters)
        if not geometry.valid and desired not in {"INELIGIBLE", "INVALIDATED"}:
            blockers.append(StrategyBlockerV2(
                "INVALID_GEOMETRY", "1H", geometry.limitations or ("structural geometry unavailable",),
                _source_ts(context, "1H"), True, "wait for valid trigger, invalidation, and opposing structural references"))
        strong_flow_conflict = any(item.strength != "WEAK" for item in flow_conflict)
        if desired == "TRIGGER_READY" and strong_flow_conflict:
            blockers.append(StrategyBlockerV2(
                "FLOW_CONFLICT", "15m", tuple(item.code for item in flow_conflict),
                _source_ts(context, "15m"), True, "wait for price/flow conflict to clear or flow to become unavailable"))
        trigger_threshold = float(effective_parameters.get("trigger_score", PARAMETERS["trigger_ready_score"]))
        if desired == "TRIGGER_READY" and (score < trigger_threshold or trigger < PARAMETERS["trigger_dimension_minimum"] or any(item.blocking for item in blockers) or not geometry.valid):
            desired = "ARMED" if setup >= 20 else "WATCH"
        if not geometry.valid and desired == "TRIGGER_READY":
            desired = "WATCH"
        sources = _all_source_timestamps(context, state)
        level_identity = stable_hash({"type": level.get("level_type"), "timeframe": level.get("timeframe"), "boundary": level.get("boundary"), "sources": level.get("source_timestamps")}) if level else stable_hash({"level": "NONE"})
        event_timestamps = [int((level or {})[key]) for key in
                            ("breakout_timestamp", "confirmation_timestamp", "reclaim_timestamp")
                            if (level or {}).get(key) is not None]
        setup_started = (max(event_timestamps) if event_timestamps else int(context["as_of"])) if desired not in {"INELIGIBLE", "INVALIDATED"} else None
        trigger_ts = int(context["as_of"]) if desired == "TRIGGER_READY" else None
        configuration_hash = stable_hash({"definitions": DEFINITIONS_VERSION, "parameters": effective_parameters,
                                          "parameter_set_id": parameter_set_id, "family": family,
                                          "direction": direction})
        family_id = stable_hash({"family": family, "direction": direction, "strategy_version": FAMILY_VERSIONS[family], "definitions": DEFINITIONS_VERSION, "parameters": PARAMETER_SET_VERSION})
        setup_id = stable_hash({"family_id": family_id, "instrument": context["instrument"], "timeframes": TIMEFRAME_ROLES, "level_identity": level_identity, "setup_started_at": setup_started, "configuration_hash": configuration_hash,
                                "parameter_set_id": parameter_set_id})
        evaluation_id = stable_hash({"setup_id": setup_id, "as_of": context["as_of"], "source_timestamps": sources, "trigger_timestamp": trigger_ts})
        identity = StrategyIdentityV2(family_id, setup_id, evaluation_id, configuration_hash, family, direction,
                                      FAMILY_VERSIONS[family], DEFINITIONS_VERSION, PARAMETER_SET_VERSION,
                                      str(context["instrument"]), "15m", "1H", "4H", ("1D", "1W"), sources,
                                      level_identity, setup_started, trigger_ts)
        available_evidence = supporting + conflicting
        agreement = len(supporting) / len(available_evidence) if available_evidence else 0
        completeness = score / 100
        evidence_strength = round((agreement * .6 + completeness * .4) * 100, 2)
        stage = StrategyStageV2(desired, setup_started, trigger_ts,
                                setup_started + PARAMETERS["maximum_wait_bars"][family] * 900 if setup_started else None, None)
        return StrategyCandidateV2(family, direction, FAMILY_VERSIONS[family], PARAMETER_SET_VERSION,
                                   desired, stage, score, score_breakdown, evidence_strength,
                                   tuple(supporting), tuple(conflicting), tuple(blockers), tuple(next_confirmation),
                                   geometry, sources, quality, identity, stable_hash(asdict(identity)), tuple(sorted(set(limitations))),
                                   parameter_progress=parameter_progress)

    @staticmethod
    def _priority(candidate: StrategyCandidateV2) -> tuple[Any, ...]:
        event = {"FAILED_BREAKOUT_REVERSAL": 4, "BREAKOUT_CONTINUATION": 3,
                 "MA200_MEAN_REVERSION": 2, "TREND_PULLBACK": 1}[candidate.family]
        stage = {"TRIGGERED_RESEARCH_ONLY": 7, "TRIGGER_READY": 6, "ARMED": 5, "WATCH": 4,
                 "COOLDOWN_RESEARCH_ONLY": 3, "EXPIRED": 2, "INVALIDATED": 1, "INELIGIBLE": 0}[candidate.state]
        geometry = candidate.geometry.structural_reward_risk or 0
        freshness = max(candidate.source_timestamps, default=0)
        return stage, event, candidate.score, candidate.evidence_strength, geometry, freshness, candidate.identity_hash

    @staticmethod
    def _no_trade_reasons(context: Mapping[str, Any], state: Mapping[str, Any], candidates: list[StrategyCandidateV2],
                          primary: StrategyCandidateV2 | None, conflict: bool) -> list[NoTradeReasonV2]:
        now = int(context["as_of"]); reasons: list[NoTradeReasonV2] = []
        def add(code: str, timeframe: str, evidence: Iterable[str], release: str, temporary: bool = True) -> None:
            if code not in {item.code for item in reasons}:
                reasons.append(NoTradeReasonV2(code, timeframe, tuple(evidence), _source_ts(context, timeframe) or now, temporary, release))
        quality = context.get("quality", {})
        if quality.get("overall_status") == "MISSING" or _state(state, "15m") == "UNKNOWN": add("INSUFFICIENT_DATA", "15m", ("execution facts unavailable",), "confirmed execution data becomes available")
        if context.get("timeframes", {}).get("15m", {}).get("quality", {}).get("stale"): add("STALE_EXECUTION_DATA", "15m", ("stale execution candle",), "fresh confirmed 15m candle")
        if state.get("cross_timeframe", {}).get("state") == "CONFLICTED" or conflict: add("HTF_CONFLICT", "4H", (str(state.get("cross_timeframe", {}).get("state")),), "higher timeframes align or trigger conflict clears")
        if state.get("primary_state_code") in {"RANGE_ROTATION", "NO_CLEAR_STATE"}: add("MID_RANGE_NOISE", "4H", (str(state.get("primary_state_code")),), "price reaches a confirmed structural boundary")
        overlays = _overlays(state)
        if "MID_RANGE_NOISE" in overlays: add("MID_RANGE_NOISE", "1H", ("MID_RANGE_NOISE",), "price reaches a confirmed structural boundary")
        if any("VOLATILITY_COMPRESSION" in item for item in overlays) and not any(item.family == "BREAKOUT_CONTINUATION" and item.state in {"ARMED", "TRIGGER_READY"} for item in candidates):
            add("VOLATILITY_TOO_LOW", "4H", ("volatility compression has not released",), "confirmed expansion and boundary event")
        if any("VOLATILITY_EXPANSION" in item for item in overlays) and state.get("primary_state_code") == "VOLATILITY_TRANSITION":
            add("VOLATILITY_TOO_HIGH", "4H", ("uncontrolled volatility expansion",), "volatility normalizes around confirmed structure")
        if any("EXTENDED" in item for item in overlays): add("EXTENDED_FROM_STRUCTURE", "1H", tuple(item for item in overlays if "EXTENDED" in item), "price returns to a defined setup zone")
        if not state.get("level_interactions"): add("NO_STRUCTURAL_LEVEL", "1H", ("no confirmed level interaction",), "confirmed structural level forms")
        for candidate in candidates:
            for blocker in candidate.blockers:
                if blocker.code in NO_TRADE_CODES: add(blocker.code, blocker.timeframe, blocker.evidence, blocker.release_condition)
            if candidate.state == "EXPIRED": add("SETUP_EXPIRED", "15m", (candidate.identity.strategy_setup_id,), "new setup identity forms")
            if candidate.state in {"TRIGGERED_RESEARCH_ONLY", "COOLDOWN_RESEARCH_ONLY"}: add("DUPLICATE_SETUP", "15m", (candidate.identity.strategy_setup_id,), "cooldown elapses and a new setup forms")
            if "TOO_CLOSE_TO_OPPOSING_LEVEL" in candidate.geometry.limitations: add("TOO_CLOSE_TO_OPPOSING_LEVEL", "1H", candidate.geometry.limitations, "structural reward space expands")
        if primary is None or primary.state not in {"TRIGGER_READY", "TRIGGERED_RESEARCH_ONLY"}:
            add("NO_CONFIRMATION" if any(item.state in {"WATCH", "ARMED"} for item in candidates) else "NO_STRATEGY_MATCH", "15m", ("no candidate passed every trigger gate",), "next listed confirmation completes")
        return reasons


def backtest_parameter_ranges_v2() -> dict[str, dict[str, tuple[Any, ...]]]:
    return {
        "TREND_PULLBACK": {"zone_buffer_atr": (.2, .35), "trigger_score": (72, 78), "minimum_r": (1.25, 1.5)},
        "MA200_MEAN_REVERSION": {"zone_buffer_atr": (.25, .4), "reclaim_bars": (1, 2), "minimum_r": (1.25, 1.5)},
        "BREAKOUT_CONTINUATION": {"boundary_buffer_atr": (.15, .3), "retest_wait_bars": (2, 4), "minimum_r": (1.25, 1.5)},
        "FAILED_BREAKOUT_REVERSAL": {"reentry_buffer_atr": (.15, .3), "reverse_confirm_bars": (1, 2), "minimum_r": (1.25, 1.5)},
    }


def backtest_specifications_v2() -> list[dict[str, Any]]:
    """Bounded Phase-4 input contract. It does not run a backtest."""
    ranges = backtest_parameter_ranges_v2()
    output: list[dict[str, Any]] = []
    for family in TRADE_FAMILIES:
        combination_count = math.prod(len(values) for values in ranges[family].values())
        for direction in ("LONG", "SHORT"):
            output.append(asdict(StrategyBacktestSpecificationV2(
                "strategy-backtest-specification-v2", family, direction, ("15m", "1H", "4H", "1D", "1W"),
                ("market-analysis-context-v2", PARAMETER_SET_VERSION), ("market-state-engine-v2", "market-state-definitions-v2.1"),
                ranges[family], combination_count, "next confirmed 15m open after TRIGGER_READY",
                ("use candidate invalidation reference", "never invent fixed ATR when geometry is invalid"),
                ("structural target", "maximum holding bars", "fixed R only as benchmark"),
                PARAMETERS["maximum_wait_bars"][family], PARAMETERS["maximum_holding_bars"][family],
                "new setup identity after invalidation/expiry/cooldown", {"fees": None, "slippage": None},
                "EXPLICIT_PHASE_4_POLICY_REQUIRED", "DEFINE_WITHOUT_HOLDOUT_RANKING",
                ("BTC-USDT-SWAP", "ETH-USDT-SWAP"), ("15m",), "confirmed price required; flow optional and quality-aware",
                ("family/direction/version/config/instrument/timeframes", "level identity and setup start", "source timestamps and trigger timestamp"),
            )))
    return output
