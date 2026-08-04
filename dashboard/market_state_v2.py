"""Deterministic market-state interpretation of MarketAnalysisContextV2.

This module is deliberately a pure consumer.  It performs no indicator
calculation, persistence, raw-data query, strategy decision, or LLM call.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Iterable

try:
    from market_context_v2 import CONTEXT_VERSION
except ImportError:
    from .market_context_v2 import CONTEXT_VERSION


STATE_ENGINE_VERSION = "market-state-engine-v2"
STATE_DEFINITION_VERSION = "market-state-definitions-v2.1"
TIMEFRAMES = ("1W", "1D", "4H", "1H", "15m")
TIMEFRAME_ROLES = {
    "1W": "LONG_TERM_STRUCTURE",
    "1D": "MEDIUM_TERM_DIRECTION",
    "4H": "PRIMARY_ENVIRONMENT",
    "1H": "SETUP_CONTEXT",
    "15m": "TRIGGER_CONTEXT",
}
PRIMARY_STATES = (
    "TREND_UP", "TREND_DOWN", "RANGE_LOW_VOLATILITY",
    "RANGE_HIGH_VOLATILITY", "TRANSITION_UP", "TRANSITION_DOWN",
    "TRANSITION_MIXED", "UNKNOWN",
)
COMPOSITE_STATES = (
    "HTF_UPTREND_CONTINUATION", "HTF_DOWNTREND_CONTINUATION",
    "HTF_UPTREND_PULLBACK", "HTF_DOWNTREND_BOUNCE",
    "MAJOR_SUPPORT_TEST", "MAJOR_RESISTANCE_TEST", "RANGE_ROTATION",
    "BREAKOUT_DEVELOPING", "BREAKDOWN_DEVELOPING",
    "FAILED_BREAKOUT_DEVELOPING", "VOLATILITY_TRANSITION",
    "NO_CLEAR_STATE", "INSUFFICIENT_DATA",
)

# All thresholds are fixed by the definition version.  Percent values use
# percentage points (0.50 means 0.50%), never fractions.
THRESHOLDS = {
    "trend_score": 55.0,
    "trend_margin": 24.0,
    "transition_margin": 12.0,
    "compression_percentile": 70.0,
    "expansion_percentile": 70.0,
    "high_atr_pct": 2.0,
    "low_atr_pct": 0.8,
    "volume_expansion_ratio": 1.20,
    "level_min_pct": 0.10,
    "level_atr_fraction": 0.25,
    "confirmation_min_pct": 0.15,
    "confirmation_atr_fraction": 0.35,
    "momentum_overbought": 80.0,
    "momentum_oversold": 20.0,
}


@dataclass(frozen=True)
class StateEvidenceV2:
    code: str
    timeframe: str
    value: Any
    weight: float
    source_timestamp: int | None
    quality: str
    classification: str


@dataclass(frozen=True)
class TimeframeStateV2:
    timeframe: str
    role: str
    primary_state: str
    primary_state_code: str
    evidence_strength: float
    quality: dict[str, Any]
    momentum_state: str
    overlays: tuple[str, ...]
    source_timestamps: tuple[int, ...]
    supporting_evidence: tuple[str, ...]
    conflicting_evidence: tuple[str, ...]
    unavailable_evidence: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class LevelInteractionV2:
    level_type: str
    timeframe: str
    zone_low: float
    zone_high: float
    boundary: float
    distance_pct: float
    approach_direction: str
    interaction_type: str
    touch_count: int
    rejection_strength: float | None
    reclaim_status: str
    source_timestamps: tuple[int, ...]
    quality: str
    breakout_timestamp: int | None = None
    confirmation_timestamp: int | None = None
    reclaim_timestamp: int | None = None
    volume_ratio: float | None = None
    cvd_oi_quality: str = "UNAVAILABLE"
    current_stage: str = "OBSERVING"
    invalidation_reason: str | None = None


@dataclass(frozen=True)
class CrossTimeframeAlignmentV2:
    state: str
    supporting_timeframes: tuple[str, ...]
    conflicting_timeframes: tuple[str, ...]
    missing_timeframes: tuple[str, ...]
    normal_pullback: bool
    countertrend_lower_timeframe_move: bool
    structure_state: str
    environment_state: str
    setup_state: str
    trigger_state: str


@dataclass(frozen=True)
class StateTransitionV2:
    from_state: str
    to_state: str
    transition_timestamp: int
    trigger_evidence: tuple[str, ...]
    source_candle_timestamps: tuple[int, ...]
    confirmation_status: str
    invalidation_reason: str | None


@dataclass(frozen=True)
class MarketStateSnapshotV2:
    version: str
    definition_version: str
    instrument: str
    as_of: int
    execution_timeframe: str
    primary_state: str
    primary_state_code: str
    evidence_strength: float
    quality: dict[str, Any]
    timeframes: dict[str, TimeframeStateV2]
    cross_timeframe: CrossTimeframeAlignmentV2
    level_interactions: tuple[LevelInteractionV2, ...]
    overlays: tuple[str, ...]
    transitions: tuple[StateTransitionV2, ...]
    evidence: tuple[StateEvidenceV2, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _indicator(frame: dict[str, Any], group: str, name: str) -> dict[str, Any]:
    item = frame.get(group, {}).get(name)
    return item if isinstance(item, dict) else {
        "value": None, "available": False, "source_timestamp": None,
        "stale": False, "partial": False, "warmup_complete": False,
    }


def _usable(item: dict[str, Any]) -> bool:
    return bool(item.get("available")) and not bool(item.get("stale")) and item.get("value") is not None


def _quality_of(item: dict[str, Any]) -> str:
    if not item.get("available") or item.get("value") is None:
        return "UNAVAILABLE"
    if item.get("stale"):
        return "STALE"
    if item.get("partial"):
        return "PARTIAL"
    if not item.get("warmup_complete", True):
        return "WARMUP"
    return "AVAILABLE"


def _number(item: dict[str, Any]) -> float | None:
    if not _usable(item) or not isinstance(item.get("value"), (int, float)):
        return None
    value = float(item["value"])
    return value if math.isfinite(value) else None


def _timestamp(item: dict[str, Any]) -> int | None:
    value = item.get("source_timestamp")
    return int(value) if value is not None else None


def _future_timestamps(value: Any, as_of: int, path: str = "context") -> list[str]:
    violations: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in {"source_timestamp", "candle_close_ts", "start_timestamp", "end_timestamp"} and item is not None:
                try:
                    if int(item) > as_of:
                        violations.append(f"{child}={item}")
                except (TypeError, ValueError):
                    violations.append(f"{child}=invalid")
            else:
                violations.extend(_future_timestamps(item, as_of, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            violations.extend(_future_timestamps(item, as_of, f"{path}[{index}]"))
    return violations


def _strength(evidence: Iterable[StateEvidenceV2]) -> float:
    values = list(evidence)
    available = [item for item in values if item.quality in {"AVAILABLE", "PARTIAL"}]
    if not values or not available:
        return 0.0
    positive = sum(max(0.0, item.weight) for item in available)
    negative = sum(max(0.0, -item.weight) for item in available)
    directional = positive + negative
    agreement = max(positive, negative) / directional if directional else 0.5
    completeness = len(available) / len(values)
    return round(min(100.0, (0.55 * agreement + 0.45 * completeness) * 100), 2)


class MarketStateEngineV2:
    """Pure, deterministic state engine over one V2 fact snapshot."""

    version = STATE_ENGINE_VERSION
    definition_version = STATE_DEFINITION_VERSION

    def evaluate(self, context: dict[str, Any], *,
                 previous_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        if context.get("version") != CONTEXT_VERSION:
            raise ValueError(f"context version must be {CONTEXT_VERSION}")
        as_of = int(context.get("as_of", 0))
        violations = _future_timestamps(context, as_of)
        if violations:
            raise ValueError("source timestamp later than as_of: " + ", ".join(sorted(violations)[:8]))
        frames = context.get("timeframes")
        if not isinstance(frames, dict):
            raise ValueError("context timeframes must be an object")

        evidence: list[StateEvidenceV2] = []
        timeframe_states: dict[str, TimeframeStateV2] = {}
        for timeframe in TIMEFRAMES:
            state, frame_evidence = self._timeframe_state(timeframe, frames.get(timeframe) or {})
            timeframe_states[timeframe] = state
            evidence.extend(frame_evidence)

        level_interactions, level_overlays = self._level_interactions(context, timeframe_states)
        volatility_overlays = self._volatility_overlays(context, timeframe_states)
        flow_overlays, flow_evidence = self._flow_overlays(context)
        evidence.extend(flow_evidence)
        alignment = self._alignment(timeframe_states)
        code = self._compose(context, timeframe_states, alignment,
                             level_interactions, volatility_overlays)
        limitations = self._limitations(context, timeframe_states)
        if limitations and code != "INSUFFICIENT_DATA":
            # Limitations degrade completeness, not an otherwise valid price structure.
            for limitation in limitations:
                evidence.append(StateEvidenceV2("LIMITATION", "GLOBAL", limitation, 0, None,
                                                "UNAVAILABLE", "unavailable"))
        overlays = tuple(sorted(set(
            item for state in timeframe_states.values() for item in state.overlays
        ) | set(level_overlays) | set(volatility_overlays) | set(flow_overlays)))
        transitions = self._transitions(previous_snapshot, code, context, evidence)
        snapshot = MarketStateSnapshotV2(
            STATE_ENGINE_VERSION, STATE_DEFINITION_VERSION,
            str(context.get("instrument", "")), as_of,
            str(context.get("execution_timeframe", "15m")), code, code,
            _strength(evidence), dict(context.get("quality") or {}),
            timeframe_states, alignment, tuple(level_interactions), overlays,
            tuple(transitions), tuple(evidence), tuple(sorted(set(limitations))),
        )
        return snapshot.to_dict()

    def compare(self, previous_context: dict[str, Any], current_context: dict[str, Any]) -> dict[str, Any]:
        if str(previous_context.get("instrument")) != str(current_context.get("instrument")):
            raise ValueError("compare contexts must use the same instrument")
        if int(previous_context.get("as_of", 0)) >= int(current_context.get("as_of", 0)):
            raise ValueError("compare requires previous_as_of < current_as_of")
        previous = self.evaluate(previous_context)
        current = self.evaluate(current_context, previous_snapshot=previous)
        timeframe_transitions: list[dict[str, Any]] = []
        for timeframe in TIMEFRAMES:
            prior_state = previous["timeframes"][timeframe]["primary_state"]
            current_state = current["timeframes"][timeframe]["primary_state"]
            if (prior_state, current_state) in {
                ("TREND_UP", "TRANSITION_DOWN"),
                ("TREND_DOWN", "TRANSITION_UP"),
            }:
                timeframe_transitions.append(asdict(StateTransitionV2(
                    prior_state, current_state, int(current_context["as_of"]),
                    (f"{timeframe}:PRIMARY_STATE_CHANGED",),
                    tuple(current["timeframes"][timeframe]["source_timestamps"]),
                    "CONFIRMED", None,
                )))
            if (f"{timeframe}:VOLATILITY_COMPRESSION" in previous.get("overlays", []) and
                    f"{timeframe}:VOLATILITY_EXPANSION" in current.get("overlays", [])):
                timeframe_transitions.append(asdict(StateTransitionV2(
                    "VOLATILITY_COMPRESSION", "VOLATILITY_EXPANSION",
                    int(current_context["as_of"]),
                    (f"{timeframe}:EXPANSION_PERCENTILE_CROSSED",),
                    tuple(current["timeframes"][timeframe]["source_timestamps"]),
                    "CONFIRMED", None,
                )))
        prior_levels = {
            (item["level_type"], item["timeframe"], item["boundary"]): item
            for item in previous.get("level_interactions", [])
        }
        sequence_transition: dict[str, Any] | None = None
        for item in current.get("level_interactions", []):
            prior = prior_levels.get((item["level_type"], item["timeframe"], item["boundary"]))
            if not prior:
                continue
            prior_stage = prior.get("current_stage")
            distance = float(item.get("distance_pct", 0))
            if prior_stage == "BREAKOUT_CANDIDATE" and distance > 0 and item.get("interaction_type") == "BROKEN":
                item["current_stage"] = "BREAKOUT_CONFIRMED"
                item["confirmation_timestamp"] = int(current_context["as_of"])
                current["overlays"] = sorted(set([*current["overlays"], "BREAKOUT_CONFIRMED"]))
                if current["cross_timeframe"]["state"] == "ALIGNED_UP":
                    current["primary_state"] = current["primary_state_code"] = "HTF_UPTREND_CONTINUATION"
                    sequence_transition = self._comparison_transition(previous, current, "CONFIRMED")
            elif prior_stage == "BREAKDOWN_CANDIDATE" and distance < 0 and item.get("interaction_type") == "BROKEN":
                item["current_stage"] = "BREAKDOWN_CONFIRMED"
                item["confirmation_timestamp"] = int(current_context["as_of"])
                current["overlays"] = sorted(set([*current["overlays"], "BREAKDOWN_CONFIRMED"]))
                if current["cross_timeframe"]["state"] == "ALIGNED_DOWN":
                    current["primary_state"] = current["primary_state_code"] = "HTF_DOWNTREND_CONTINUATION"
                    sequence_transition = self._comparison_transition(previous, current, "CONFIRMED")
            elif prior_stage == "BREAKOUT_CANDIDATE" and distance > 0 and item.get("interaction_type") in {"TOUCHING", "INSIDE_ZONE"}:
                item["interaction_type"] = "RETESTING"
                item["current_stage"] = "BREAKOUT_RETESTING"
            elif prior_stage == "BREAKDOWN_CANDIDATE" and distance < 0 and item.get("interaction_type") in {"TOUCHING", "INSIDE_ZONE"}:
                item["interaction_type"] = "RETESTING"
                item["current_stage"] = "BREAKDOWN_RETESTING"
            elif prior_stage == "BREAKOUT_CANDIDATE" and distance <= 0:
                item["interaction_type"] = "RECLAIMED"
                item["reclaim_status"] = "RECLAIMED_INTO_PRIOR_RANGE"
                item["reclaim_timestamp"] = int(current_context["as_of"])
                item["current_stage"] = "FAILED_BREAKOUT_CANDIDATE"
                current["primary_state"] = current["primary_state_code"] = "FAILED_BREAKOUT_DEVELOPING"
                current["overlays"] = sorted(set([*current["overlays"], "FAILED_BREAKOUT_CANDIDATE"]))
                sequence_transition = self._comparison_transition(previous, current, "CONFIRMED")
            elif prior_stage == "BREAKDOWN_CANDIDATE" and distance >= 0:
                item["interaction_type"] = "RECLAIMED"
                item["reclaim_status"] = "RECLAIMED_ABOVE_PRIOR_SUPPORT"
                item["reclaim_timestamp"] = int(current_context["as_of"])
                item["current_stage"] = "FAILED_BREAKDOWN_CANDIDATE"
                current["overlays"] = sorted(set([*current["overlays"], "FAILED_BREAKDOWN_CANDIDATE"]))
                sequence_transition = self._comparison_transition(previous, current, "CONFIRMED")
            elif (prior.get("interaction_type") in {"TOUCHING", "INSIDE_ZONE"} and
                  item["level_type"] == "MA200" and
                  prior.get("approach_direction") == "FROM_ABOVE" and distance < 0):
                item["interaction_type"] = "BROKEN"
                item["current_stage"] = "MA200_BREAKDOWN_CONFIRMED"
                item["confirmation_timestamp"] = int(current_context["as_of"])
                current["overlays"] = sorted(set([*current["overlays"], "BREAKDOWN_CONFIRMED"]))
            elif (prior.get("interaction_type") in {"TOUCHING", "INSIDE_ZONE"} and
                  item["level_type"] == "MA200" and
                  prior.get("approach_direction") == "FROM_BELOW" and distance > 0):
                item["interaction_type"] = "RECLAIMED"
                item["current_stage"] = "MA200_RECLAIM_CONFIRMED"
                item["reclaim_status"] = "RECLAIMED_ABOVE_MA200"
                item["reclaim_timestamp"] = int(current_context["as_of"])
            elif (prior.get("interaction_type") in {"TOUCHING", "INSIDE_ZONE"} and
                  item.get("interaction_type") == "UNKNOWN" and
                  distance * float(prior.get("distance_pct", 0) or (1 if prior.get("approach_direction") == "FROM_ABOVE" else -1)) > 0):
                item["interaction_type"] = "REJECTED"
                item["rejection_strength"] = round(abs(distance), 6)
                item["current_stage"] = "BOUNDARY_REJECTION_CANDIDATE"
        if sequence_transition is not None:
            current["transitions"] = [sequence_transition]
        current["transitions"] = [*current.get("transitions", []), *timeframe_transitions]
        return {"version": STATE_ENGINE_VERSION, "previous": previous, "current": current,
                "transitions": current["transitions"]}

    @staticmethod
    def _comparison_transition(previous: dict[str, Any], current: dict[str, Any],
                               confirmation: str) -> dict[str, Any]:
        timestamps = sorted({timestamp for state in current["timeframes"].values()
                             for timestamp in state.get("source_timestamps", [])})
        return asdict(StateTransitionV2(
            str(previous["primary_state_code"]), str(current["primary_state_code"]),
            int(current["as_of"]), tuple(current.get("overlays", [])), tuple(timestamps),
            confirmation, None,
        ))

    def _timeframe_state(self, timeframe: str, frame: dict[str, Any]) -> tuple[TimeframeStateV2, list[StateEvidenceV2]]:
        quality = dict(frame.get("quality") or {"status": "MISSING", "missing": True})
        limitations: list[str] = []
        if not frame or quality.get("missing") or not frame.get("confirmed"):
            limitation = f"{timeframe} has no confirmed market context"
            return TimeframeStateV2(timeframe, TIMEFRAME_ROLES[timeframe], "UNKNOWN", "UNKNOWN", 0.0,
                                    quality, "UNAVAILABLE", (), (), (), (), (), (limitation,)), []

        evidence: list[StateEvidenceV2] = []
        up = down = 0.0

        def directional(group: str, name: str, weight: float, positive_code: str,
                        negative_code: str, *, positive: callable = lambda value: value > 0) -> None:
            nonlocal up, down
            item = _indicator(frame, group, name)
            value = item.get("value")
            item_quality = _quality_of(item)
            timestamp = _timestamp(item)
            if item_quality in {"AVAILABLE", "PARTIAL"} and isinstance(value, (int, float)):
                if abs(float(value)) <= 1e-12:
                    evidence.append(StateEvidenceV2(f"{name.upper()}_NEUTRAL", timeframe, value, 0,
                                                    timestamp, item_quality, "conflicting"))
                    return
                is_positive = bool(positive(float(value)))
                code = positive_code if is_positive else negative_code
                signed = weight if is_positive else -weight
                up += weight if is_positive else 0
                down += weight if not is_positive else 0
                evidence.append(StateEvidenceV2(code, timeframe, value, signed, timestamp,
                                                item_quality, "supporting" if is_positive else "conflicting"))
            else:
                evidence.append(StateEvidenceV2(f"{name.upper()}_UNAVAILABLE", timeframe, None, 0,
                                                timestamp, item_quality, "unavailable"))

        directional("trend", "close_distance_to_ema20", 8, "PRICE_ABOVE_EMA20", "PRICE_BELOW_EMA20")
        directional("trend", "close_distance_to_ma60", 12, "PRICE_ABOVE_MA60", "PRICE_BELOW_MA60")
        directional("trend", "close_distance_to_ma200", 15, "PRICE_ABOVE_MA200", "PRICE_BELOW_MA200")
        directional("trend", "ema20_slope", 7, "EMA20_RISING", "EMA20_FALLING")
        directional("trend", "ma60_slope", 9, "MA60_RISING", "MA60_FALLING")
        directional("trend", "ma200_slope", 11, "MA200_RISING", "MA200_FALLING")
        directional("momentum", "price_momentum", 8, "PRICE_MOMENTUM_POSITIVE", "PRICE_MOMENTUM_NEGATIVE")
        directional("momentum", "momentum_persistence", 10, "MOMENTUM_PERSISTENCE_POSITIVE", "MOMENTUM_PERSISTENCE_NEGATIVE")

        arrangement = _indicator(frame, "trend", "ma_arrangement")
        arrangement_value = arrangement.get("value") if _usable(arrangement) else None
        if arrangement_value == "EMA20_GT_MA60_GT_MA200":
            up += 20
            evidence.append(StateEvidenceV2("BULLISH_MA_ARRANGEMENT", timeframe, arrangement_value, 20,
                                            _timestamp(arrangement), _quality_of(arrangement), "supporting"))
        elif arrangement_value == "EMA20_LT_MA60_LT_MA200":
            down += 20
            evidence.append(StateEvidenceV2("BEARISH_MA_ARRANGEMENT", timeframe, arrangement_value, -20,
                                            _timestamp(arrangement), _quality_of(arrangement), "conflicting"))
        elif arrangement_value == "MIXED":
            evidence.append(StateEvidenceV2("MIXED_MA_ARRANGEMENT", timeframe, arrangement_value, 0,
                                            _timestamp(arrangement), _quality_of(arrangement), "conflicting"))
        else:
            evidence.append(StateEvidenceV2("MA_ARRANGEMENT_UNAVAILABLE", timeframe, None, 0,
                                            _timestamp(arrangement), _quality_of(arrangement), "unavailable"))

        atr = _number(_indicator(frame, "volatility", "atr_percentage"))
        realized = _number(_indicator(frame, "volatility", "realized_volatility"))
        compression = _number(_indicator(frame, "volatility", "compression_percentile"))
        expansion = _number(_indicator(frame, "volatility", "expansion_percentile"))
        for code, value, item in (
            ("ATR_PERCENTAGE_CONTEXT", atr, _indicator(frame, "volatility", "atr_percentage")),
            ("REALIZED_VOLATILITY_CONTEXT", realized, _indicator(frame, "volatility", "realized_volatility")),
            ("COMPRESSION_PERCENTILE_CONTEXT", compression, _indicator(frame, "volatility", "compression_percentile")),
            ("EXPANSION_PERCENTILE_CONTEXT", expansion, _indicator(frame, "volatility", "expansion_percentile")),
        ):
            evidence.append(StateEvidenceV2(code if value is not None else code + "_UNAVAILABLE",
                                            timeframe, value, 0, _timestamp(item), _quality_of(item),
                                            "supporting" if value is not None else "unavailable"))
        rolling_high = _number(_indicator(frame, "structure", "rolling_high_distance"))
        rolling_low = _number(_indicator(frame, "structure", "rolling_low_distance"))
        if rolling_high is not None and rolling_high > THRESHOLDS["confirmation_min_pct"]:
            up += 12
            evidence.append(StateEvidenceV2("CLOSE_ABOVE_PRIOR_ROLLING_HIGH", timeframe, rolling_high, 12,
                                            frame.get("candle_close_ts"), "AVAILABLE", "supporting"))
        if rolling_low is not None and rolling_low < -THRESHOLDS["confirmation_min_pct"]:
            down += 12
            evidence.append(StateEvidenceV2("CLOSE_BELOW_PRIOR_ROLLING_LOW", timeframe, rolling_low, -12,
                                            frame.get("candle_close_ts"), "AVAILABLE", "conflicting"))

        available_directional = sum(item.quality in {"AVAILABLE", "PARTIAL"} for item in evidence)
        margin = up - down
        if available_directional < 4:
            primary = "UNKNOWN"
            limitations.append(f"{timeframe} has insufficient trend evidence")
        elif up >= THRESHOLDS["trend_score"] and margin >= THRESHOLDS["trend_margin"]:
            primary = "TREND_UP"
        elif down >= THRESHOLDS["trend_score"] and margin <= -THRESHOLDS["trend_margin"]:
            primary = "TREND_DOWN"
        elif abs(margin) < THRESHOLDS["transition_margin"] and (
                (expansion is not None and expansion >= THRESHOLDS["expansion_percentile"]) or
                (atr is not None and atr >= THRESHOLDS["high_atr_pct"])):
            primary = "RANGE_HIGH_VOLATILITY"
        elif abs(margin) < THRESHOLDS["transition_margin"] and (
                (compression is not None and compression >= THRESHOLDS["compression_percentile"]) or
                (atr is not None and atr <= THRESHOLDS["low_atr_pct"])):
            primary = "RANGE_LOW_VOLATILITY"
        elif margin >= THRESHOLDS["transition_margin"]:
            primary = "TRANSITION_UP"
        elif margin <= -THRESHOLDS["transition_margin"]:
            primary = "TRANSITION_DOWN"
        else:
            primary = "TRANSITION_MIXED"

        overlays, momentum_state = self._frame_overlays(timeframe, frame, primary)
        conclusion_sign = 1 if primary in {"TREND_UP", "TRANSITION_UP"} else -1 if primary in {"TREND_DOWN", "TRANSITION_DOWN"} else 0
        if conclusion_sign:
            evidence = [StateEvidenceV2(
                item.code, item.timeframe, item.value, item.weight, item.source_timestamp,
                item.quality,
                ("supporting" if item.weight * conclusion_sign > 0 else "conflicting")
                if item.weight else item.classification,
            ) for item in evidence]
        supporting = tuple(item.code for item in evidence if item.classification == "supporting")
        conflicting = tuple(item.code for item in evidence if item.classification == "conflicting")
        unavailable = tuple(item.code for item in evidence if item.classification == "unavailable")
        timestamps = tuple(sorted({item.source_timestamp for item in evidence if item.source_timestamp is not None}))
        return TimeframeStateV2(timeframe, TIMEFRAME_ROLES[timeframe], primary, primary,
                                _strength(evidence), quality, momentum_state, tuple(sorted(set(overlays))),
                                timestamps, supporting, conflicting, unavailable,
                                tuple(limitations)), evidence

    def _frame_overlays(self, timeframe: str, frame: dict[str, Any], primary: str) -> tuple[list[str], str]:
        overlays: list[str] = []
        atr = _number(_indicator(frame, "volatility", "atr_percentage")) or 0.0
        proximity = max(THRESHOLDS["level_min_pct"], atr * THRESHOLDS["level_atr_fraction"])
        for name, code in (("close_distance_to_ema20", "EMA20"),
                           ("close_distance_to_ma60", "MA60"),
                           ("close_distance_to_ma200", "MA200")):
            distance = _number(_indicator(frame, "trend", name))
            if distance is None:
                continue
            if abs(distance) <= proximity:
                overlays.append(f"TESTING_{code}")
                if code in {"EMA20", "MA60"} and primary == "TREND_UP" and distance >= 0:
                    overlays.append(f"PULLBACK_TO_{code}")
                if code == "MA200":
                    overlays.append("TESTING_MA200_FROM_ABOVE" if distance >= 0 else "TESTING_MA200_FROM_BELOW")
                    overlays.append("MA200_RECLAIM_CANDIDATE" if distance >= 0 else "MA200_BREAKDOWN_CANDIDATE")
                    upper_wick = _number(_indicator(frame, "volume", "upper_wick_percentage"))
                    body = _number(_indicator(frame, "volume", "candle_body_percentage"))
                    if distance < 0 and upper_wick is not None and body is not None and upper_wick > body:
                        overlays.append("MA200_REJECTION_CANDIDATE")
            elif 0 < distance <= proximity * 2:
                overlays.append(f"RECLAIMING_{code}")

        persistence = _number(_indicator(frame, "momentum", "momentum_persistence"))
        if persistence is not None and abs(persistence) >= 0.65:
            overlays.append("TREND_ACCELERATION")
        elif persistence is not None and abs(persistence) <= 0.15 and primary.startswith("TREND"):
            overlays.append("TREND_DECELERATION")

        rsi = _number(_indicator(frame, "momentum", "rsi14"))
        stoch = _number(_indicator(frame, "momentum", "stoch_rsi"))
        k = _number(_indicator(frame, "momentum", "stoch_rsi_k"))
        d = _number(_indicator(frame, "momentum", "stoch_rsi_d"))
        if rsi is None and stoch is None:
            momentum = "UNAVAILABLE"
        elif (stoch is not None and stoch <= THRESHOLDS["momentum_oversold"]) or (rsi is not None and rsi <= 30):
            momentum = "OVERSOLD"; overlays.append("MOMENTUM_OVERSOLD")
        elif (stoch is not None and stoch >= THRESHOLDS["momentum_overbought"]) or (rsi is not None and rsi >= 70):
            momentum = "OVERBOUGHT"; overlays.append("MOMENTUM_OVERBOUGHT")
        elif k is not None and d is not None and k > d and k < 50:
            momentum = "RECOVERING_FROM_OVERSOLD"; overlays.append("MOMENTUM_RECOVERING")
        elif k is not None and d is not None and k < d and k > 50:
            momentum = "ROLLING_OVER_FROM_OVERBOUGHT"; overlays.append("MOMENTUM_ROLLING_OVER")
        else:
            momentum = "NEUTRAL"
        price_momentum = _number(_indicator(frame, "momentum", "price_momentum"))
        persistence = _number(_indicator(frame, "momentum", "momentum_persistence"))
        if price_momentum is not None and persistence is not None and price_momentum * persistence < 0:
            overlays.append("MOMENTUM_DIVERGENCE_CANDIDATE")
        if primary.startswith("RANGE") and momentum == "NEUTRAL":
            overlays.append("MID_RANGE_NOISE")
        return overlays, momentum

    def _level_interactions(self, context: dict[str, Any], states: dict[str, TimeframeStateV2]) -> tuple[list[LevelInteractionV2], list[str]]:
        price_item = context.get("price") or {}
        price = _number(price_item)
        if price is None:
            return [], []
        output: list[LevelInteractionV2] = []
        overlays: list[str] = []
        frames = context.get("timeframes") or {}
        for level in context.get("levels") or []:
            timeframe = str(level.get("timeframe", "GLOBAL"))
            atr_frame = timeframe if timeframe in frames else context.get("execution_timeframe", "15m")
            atr = _number(_indicator(frames.get(atr_frame, {}), "volatility", "atr_percentage")) or 0.0
            touch_pct = max(THRESHOLDS["level_min_pct"], atr * THRESHOLDS["level_atr_fraction"])
            confirm_pct = max(THRESHOLDS["confirmation_min_pct"], atr * THRESHOLDS["confirmation_atr_fraction"])
            boundary = float(level["value"])
            distance = (price / boundary - 1) * 100 if boundary else 0.0
            zone_low, zone_high = boundary * (1-touch_pct/100), boundary * (1+touch_pct/100)
            approach = "FROM_ABOVE" if price >= boundary else "FROM_BELOW"
            level_type = str(level.get("type", "UNKNOWN"))
            sources = " ".join(str(item) for item in level.get("confluence_sources", []))
            semantic_type = f"{level_type} {sources}"
            support = any(token in semantic_type for token in ("LOW", "VAL"))
            resistance = any(token in semantic_type for token in ("HIGH", "VAH"))
            confirmed = bool(level.get("confirmed")) and bool((frames.get(atr_frame) or {}).get("confirmed"))
            interaction = "APPROACHING" if abs(distance) <= touch_pct * 2 else "UNKNOWN"
            stage = "OBSERVING"
            confirmation_ts = None
            invalidation = None
            if zone_low <= price <= zone_high:
                interaction = "TOUCHING" if abs(distance) <= touch_pct / 2 else "INSIDE_ZONE"
                stage = "BOUNDARY_TEST"
                overlays.append("RANGE_BOUNDARY_TEST")
            frame = frames.get(atr_frame) or {}
            volume_ratio = _number(_indicator(frame, "volume", "volume_ratio"))
            expansion = _number(_indicator(frame, "volatility", "expansion_percentile"))
            expanded = ((volume_ratio is not None and volume_ratio >= THRESHOLDS["volume_expansion_ratio"]) or
                        (expansion is not None and expansion >= THRESHOLDS["expansion_percentile"]))
            approached = int(level.get("touches", 0)) > 0
            if confirmed and support and distance < -confirm_pct:
                interaction = "BROKEN"; stage = "BREAKDOWN_CANDIDATE"
                if expanded and approached:
                    overlays.append("BREAKDOWN_CANDIDATE")
                else:
                    stage = "BOUNDARY_BREACH_OBSERVED"
                invalidation = "close back above support confirmation buffer"
            elif confirmed and resistance and distance > confirm_pct:
                interaction = "BROKEN"; stage = "BREAKOUT_CANDIDATE"
                if expanded and approached:
                    overlays.append("BREAKOUT_CANDIDATE")
                else:
                    stage = "BOUNDARY_BREACH_OBSERVED"
                invalidation = "close back below resistance confirmation buffer"
            if "MA200" in semantic_type and interaction in {"TOUCHING", "INSIDE_ZONE", "APPROACHING"}:
                overlays.append("TESTING_MA200")
            flow_quality = self._combined_flow_quality(context)
            output.append(LevelInteractionV2(
                level_type, timeframe, round(zone_low, 8), round(zone_high, 8), boundary,
                round(distance, 6), approach, interaction, int(level.get("touches", 0)),
                None, "NOT_RECLAIMED", (int(level.get("source_timestamp", 0)),),
                "AVAILABLE" if confirmed else "PARTIAL", None, confirmation_ts, None,
                volume_ratio, flow_quality, stage, invalidation,
            ))
        return output, overlays

    def _volatility_overlays(self, context: dict[str, Any], states: dict[str, TimeframeStateV2]) -> list[str]:
        overlays: list[str] = []
        for timeframe in TIMEFRAMES:
            frame = (context.get("timeframes") or {}).get(timeframe) or {}
            compression = _number(_indicator(frame, "volatility", "compression_percentile"))
            expansion = _number(_indicator(frame, "volatility", "expansion_percentile"))
            volume = _number(_indicator(frame, "volume", "volume_ratio"))
            persistence = _number(_indicator(frame, "momentum", "momentum_persistence"))
            if compression is not None and compression >= THRESHOLDS["compression_percentile"]:
                overlays.append(f"{timeframe}:VOLATILITY_COMPRESSION")
            elif expansion is not None and expansion >= THRESHOLDS["expansion_percentile"]:
                overlays.append(f"{timeframe}:VOLATILITY_EXPANSION")
                if volume is not None and volume >= THRESHOLDS["volume_expansion_ratio"]:
                    overlays.append(f"{timeframe}:COMPRESSION_RELEASE_CANDIDATE")
                if persistence is not None and abs(persistence) < 0.15:
                    overlays.append(f"{timeframe}:EXPANSION_EXHAUSTION_CANDIDATE")
                    overlays.append(f"{timeframe}:EXHAUSTION_CANDIDATE")
            else:
                overlays.append(f"{timeframe}:VOLATILITY_NORMAL")
        return overlays

    def _combined_flow_quality(self, context: dict[str, Any]) -> str:
        flow = context.get("flow") or {}
        qualities = [str((flow.get(name + "_combination") or {}).get("data_quality", "MISSING"))
                     for name in ("price_oi", "price_cvd")]
        if "STALE" in qualities:
            return "STALE"
        if all(value == "AVAILABLE" for value in qualities):
            return "AVAILABLE"
        if any(value == "PARTIAL" for value in qualities):
            return "PARTIAL"
        return "UNAVAILABLE"

    def _flow_overlays(self, context: dict[str, Any]) -> tuple[list[str], list[StateEvidenceV2]]:
        flow = context.get("flow") or {}
        quality = self._combined_flow_quality(context)
        overlays: list[str] = []
        evidence: list[StateEvidenceV2] = []
        oi = flow.get("price_oi_combination") or {}
        cvd = flow.get("price_cvd_combination") or {}
        if quality == "STALE":
            return ["FLOW_STALE"], [StateEvidenceV2("FLOW_STALE", "FLOW", None, 0, None, "STALE", "unavailable")]
        if quality == "UNAVAILABLE":
            return ["FLOW_UNAVAILABLE"], [StateEvidenceV2("FLOW_UNAVAILABLE", "FLOW", None, 0, None, "UNAVAILABLE", "unavailable")]
        if quality == "PARTIAL":
            overlays.append("FLOW_PARTIAL")
        oi_state = str(oi.get("state", ""))
        if oi_state in {"PRICE_UP_OI_UP", "PRICE_UP_OI_DOWN", "PRICE_DOWN_OI_UP", "PRICE_DOWN_OI_DOWN"}:
            overlays.append(oi_state)
        cvd_state = str(cvd.get("state", ""))
        if cvd_state:
            confirming = cvd_state in {"PRICE_UP_CVD_UP", "PRICE_DOWN_CVD_DOWN"}
            overlays.append("CVD_CONFIRMING_PRICE" if confirming else "CVD_DIVERGING_PRICE")
            evidence.append(StateEvidenceV2(overlays[-1], "FLOW", cvd_state,
                                            5 if confirming and quality == "AVAILABLE" else -3 if quality == "AVAILABLE" else 0,
                                            cvd.get("end_timestamp"), quality,
                                            "supporting" if confirming else "conflicting"))
        return overlays, evidence

    def _alignment(self, states: dict[str, TimeframeStateV2]) -> CrossTimeframeAlignmentV2:
        missing = tuple(frame for frame in TIMEFRAMES if states[frame].primary_state == "UNKNOWN")
        direction = {frame: ("UP" if states[frame].primary_state in {"TREND_UP", "TRANSITION_UP"}
                             else "DOWN" if states[frame].primary_state in {"TREND_DOWN", "TRANSITION_DOWN"}
                             else "MIXED") for frame in TIMEFRAMES}
        higher = [direction[frame] for frame in ("1W", "1D") if frame not in missing]
        lower = [direction[frame] for frame in ("1H", "15m") if frame not in missing]
        environment = direction["4H"]
        if not higher and "4H" in missing:
            alignment = "INSUFFICIENT_DATA"
        elif higher and all(value == "UP" for value in higher) and environment == "UP":
            alignment = "HIGHER_UP_LOWER_PULLBACK" if "DOWN" in lower else "ALIGNED_UP"
        elif higher and all(value == "DOWN" for value in higher) and environment == "DOWN":
            alignment = "HIGHER_DOWN_LOWER_BOUNCE" if "UP" in lower else "ALIGNED_DOWN"
        elif higher and all(value == "MIXED" for value in higher):
            alignment = "HIGHER_MIXED_LOWER_UP" if lower and all(value == "UP" for value in lower) else "HIGHER_MIXED_LOWER_DOWN" if lower and all(value == "DOWN" for value in lower) else "CONFLICTED"
        else:
            alignment = "CONFLICTED"
        dominant = "UP" if alignment in {"ALIGNED_UP", "HIGHER_UP_LOWER_PULLBACK"} else "DOWN" if alignment in {"ALIGNED_DOWN", "HIGHER_DOWN_LOWER_BOUNCE"} else None
        supporting = tuple(frame for frame in TIMEFRAMES if frame not in missing and direction[frame] == dominant)
        conflicting = tuple(frame for frame in TIMEFRAMES if frame not in missing and dominant and direction[frame] not in {dominant, "MIXED"})
        normal_pullback = alignment in {"HIGHER_UP_LOWER_PULLBACK", "HIGHER_DOWN_LOWER_BOUNCE"}
        return CrossTimeframeAlignmentV2(alignment, supporting, conflicting, missing,
                                         normal_pullback, normal_pullback,
                                         "/".join(direction[frame] for frame in ("1W", "1D")),
                                         states["4H"].primary_state, states["1H"].primary_state,
                                         states["15m"].primary_state)

    def _compose(self, context: dict[str, Any], states: dict[str, TimeframeStateV2],
                 alignment: CrossTimeframeAlignmentV2,
                 levels: list[LevelInteractionV2], volatility: list[str]) -> str:
        execution = str(context.get("execution_timeframe", "15m"))
        if execution not in states or states[execution].primary_state == "UNKNOWN":
            return "INSUFFICIENT_DATA"
        if states["1D"].primary_state == "UNKNOWN" and states["4H"].primary_state == "UNKNOWN":
            return "NO_CLEAR_STATE"
        major = [item for item in levels if item.timeframe in {"1D", "4H", "MULTI"} and item.interaction_type in {"APPROACHING", "TOUCHING", "INSIDE_ZONE"}]
        if any(any(token in item.level_type for token in ("LOW", "VAL")) or
               ("MA200" in item.level_type and item.approach_direction == "FROM_ABOVE")
               for item in major):
            return "MAJOR_SUPPORT_TEST"
        if any(any(token in item.level_type for token in ("HIGH", "VAH")) or
               ("MA200" in item.level_type and item.approach_direction == "FROM_BELOW")
               for item in major):
            return "MAJOR_RESISTANCE_TEST"
        if any(item.current_stage == "BREAKOUT_CANDIDATE" for item in levels):
            return "BREAKOUT_DEVELOPING"
        if any(item.current_stage == "BREAKDOWN_CANDIDATE" for item in levels):
            return "BREAKDOWN_DEVELOPING"
        if alignment.state == "ALIGNED_UP":
            return "HTF_UPTREND_CONTINUATION"
        if alignment.state == "ALIGNED_DOWN":
            return "HTF_DOWNTREND_CONTINUATION"
        if alignment.state == "HIGHER_UP_LOWER_PULLBACK":
            return "HTF_UPTREND_PULLBACK"
        if alignment.state == "HIGHER_DOWN_LOWER_BOUNCE":
            return "HTF_DOWNTREND_BOUNCE"
        if states["4H"].primary_state.startswith("RANGE"):
            return "RANGE_ROTATION"
        if any("VOLATILITY_COMPRESSION" in item or "COMPRESSION_RELEASE" in item for item in volatility):
            return "VOLATILITY_TRANSITION"
        return "NO_CLEAR_STATE"

    def _limitations(self, context: dict[str, Any], states: dict[str, TimeframeStateV2]) -> list[str]:
        quality = context.get("quality") or {}
        limitations = [f"missing source: {item}" for item in quality.get("missing_sources", [])]
        limitations += [f"stale source excluded: {item}" for item in quality.get("stale_sources", [])]
        limitations += [f"partial source cannot provide strong confirmation: {item}" for item in quality.get("partial_sources", [])]
        limitations += [item for state in states.values() for item in state.limitations]
        if states["1W"].primary_state == "UNKNOWN":
            limitations.append("weekly context unavailable; lower timeframes remain usable")
        if states["1D"].primary_state == "UNKNOWN" and states["4H"].primary_state == "UNKNOWN":
            limitations.append("1D and 4H unavailable; no higher-timeframe trend emitted")
        return limitations

    def _transitions(self, previous: dict[str, Any] | None, current_code: str,
                     context: dict[str, Any], evidence: list[StateEvidenceV2]) -> list[StateTransitionV2]:
        if not previous:
            return []
        prior = str(previous.get("primary_state_code", "NO_CLEAR_STATE"))
        if prior == current_code:
            return []
        mapping = {
            ("RANGE_ROTATION", "BREAKOUT_DEVELOPING"),
            ("RANGE_ROTATION", "BREAKDOWN_DEVELOPING"),
            ("BREAKOUT_DEVELOPING", "HTF_UPTREND_CONTINUATION"),
            ("BREAKOUT_DEVELOPING", "FAILED_BREAKOUT_DEVELOPING"),
            ("MAJOR_SUPPORT_TEST", "BREAKDOWN_DEVELOPING"),
            ("MAJOR_RESISTANCE_TEST", "BREAKOUT_DEVELOPING"),
            ("VOLATILITY_TRANSITION", "BREAKOUT_DEVELOPING"),
            ("VOLATILITY_TRANSITION", "BREAKDOWN_DEVELOPING"),
            ("HTF_UPTREND_PULLBACK", "HTF_UPTREND_CONTINUATION"),
            ("HTF_DOWNTREND_BOUNCE", "HTF_DOWNTREND_CONTINUATION"),
        }
        confirmation = "CONFIRMED" if (prior, current_code) in mapping else "OBSERVED"
        triggers = tuple(item.code for item in evidence if item.classification != "unavailable")[:12]
        timestamps = tuple(sorted({int(item.source_timestamp) for item in evidence if item.source_timestamp is not None}))
        return [StateTransitionV2(prior, current_code, int(context["as_of"]), triggers,
                                  timestamps, confirmation,
                                  None if confirmation == "CONFIRMED" else "transition sequence not in confirmed mapping")]
