"""Causal temporal lifecycle and execution adapter for Phase 6B."""
from __future__ import annotations

from collections import Counter
import math
from typing import Any, Mapping

from .backtest_engine import run_execution_backtest
from .okx_history import TIMEFRAME_SECONDS
from .strategy_program import canonical_hash
from .strategy_program_runtime import (
    DEVELOPMENT_END_TS, align_higher_timeframe, build_program_features,
)
from .strategy_program_v2 import (
    STRATEGY_PROGRAM_SCHEMA_VERSION, TemporalStrategyProgram, build_program_identity,
)
from .strategy_rules import StrategyParameters

LIFECYCLE_STATES = (
    "IDLE", "STAGE_1_ARMED", "STAGE_2_ARMED", "STAGE_3_ARMED",
    "STAGE_4_ARMED", "TRIGGERED", "REARM_REQUIRED", "INVALIDATED",
)


def _direction(program: TemporalStrategyProgram, bullish: bool) -> bool:
    return bullish if program.direction == "LONG" else not bullish


def _context_ok(program: TemporalStrategyProgram, aligned: Mapping[str, Any] | None) -> bool:
    if program.higher_timeframe_context is None:
        return True
    if not aligned:
        return False
    candle, feature = aligned["candle"], aligned["feature"]
    ema, ma60, ma200 = feature.get("ema_20"), feature.get("sma_60"), feature.get("sma_200")
    if None in (ema, ma60, ma200):
        return False
    bullish = float(candle["close"]) > float(ema) > float(ma60) > float(ma200)
    return _direction(program, bullish)


class TemporalSequenceEvaluator:
    """One state instance, owned by one program/geometry/asset/timeframe/fold."""

    def __init__(self, program: TemporalStrategyProgram, candles: list[dict[str, Any]],
                 features: list[dict[str, Any]], *, instrument: str,
                 dataset_fingerprint: str, fold_identity: Mapping[str, Any],
                 aligned_context: list[dict[str, Any] | None] | None = None):
        self.program = program
        self.candles = candles
        self.features = features
        self.instrument = instrument
        self.fold_identity = dict(fold_identity)
        self.identity = build_program_identity(
            program, dataset_fingerprint=dataset_fingerprint,
            instrument=instrument, fold_identity=fold_identity,
        )
        self.aligned_context = aligned_context or [None] * len(candles)
        self.state = "IDLE"
        self.stage_index: int | None = None
        self.activation_index: int | None = None
        self.stage_activation_timestamp: int | None = None
        self.sequence_number = 0
        self.sequence_id: str | None = None
        self.anchor: dict[str, Any] = {}
        self.transitions: list[dict[str, Any]] = []
        self.trigger_evidence: dict[int, dict[str, Any]] = {}

    def _capture_anchor(self, index: int) -> None:
        candle, feature = self.candles[index], self.features[index]
        direction = self.program.direction
        failed_reversal = self.program.sequence.family == "FAILED_BREAKOUT_REVERSAL"
        level_key = (
            "recent_low" if direction == "LONG" else "recent_high"
        ) if failed_reversal else (
            "recent_high" if direction == "LONG" else "recent_low"
        )
        level = feature.get(level_key)
        self.anchor = {
            "level": float(level) if level is not None else None,
            "opposite_level": feature.get("recent_low" if direction == "LONG" else "recent_high"),
            "structure_level": feature.get("sma_60"),
            "extreme": float(
                candle["low" if direction == "LONG" else "high"]
                if failed_reversal else candle["high" if direction == "LONG" else "low"]
            ),
            "feature_timestamp": int(candle["ts"]),
        }

    def _predicate(self, name: str, index: int, params: Mapping[str, Any]) -> bool:
        candle, feature = self.candles[index], self.features[index]
        close, high, low = map(float, (candle["close"], candle["high"], candle["low"]))
        atr, width, rsi = feature.get("atr"), feature.get("bb_width"), feature.get("rsi")
        level = self.anchor.get("level")
        previous = self.candles[index - 1] if index else None
        sign = 1 if self.program.direction == "LONG" else -1
        directional = lambda value: sign * float(value) > 0
        if name in {"volatility_compression", "range_or_compression_regime"}:
            return bool(feature.get("warm") and width is not None
                        and float(width) <= float(params["maximum_bandwidth"]))
        if name == "completed_rolling_level":
            candidate = feature.get("recent_high" if self.program.direction == "LONG" else "recent_low")
            return bool(feature.get("warm") and candidate is not None)
        if name == "momentum_impulse":
            ret = feature.get("candle_return")
            ema, ma60 = feature.get("ema_20"), feature.get("sma_60")
            return bool(feature.get("warm") and ret is not None and atr
                        and directional(ret) and abs(float(ret)) * close / float(atr)
                        >= float(params["return_atr"])
                        and None not in (ema, ma60) and _direction(
                            self.program, float(ema) > float(ma60)))
        if name in {"expansion_breakout", "breakout_attempt"}:
            if level is None or not atr:
                return False
            if name == "breakout_attempt":
                beyond = level - low if self.program.direction == "LONG" else high - level
            else:
                beyond = high - level if self.program.direction == "LONG" else level - low
            threshold = float(params.get("attempt_atr", 0)) * float(atr)
            if name == "expansion_breakout":
                beyond = close - level if self.program.direction == "LONG" else level - close
            if beyond > threshold:
                extreme = (
                    low if self.program.direction == "LONG" else high
                ) if name == "breakout_attempt" else (
                    high if self.program.direction == "LONG" else low
                )
                old = self.anchor.get("extreme")
                self.anchor["extreme"] = (
                    min(float(old), extreme)
                    if name == "breakout_attempt" and self.program.direction == "LONG" and old is not None
                    else max(float(old), extreme)
                    if name == "breakout_attempt" and old is not None
                    else max(float(old), extreme)
                    if self.program.direction == "LONG" and old is not None
                    else min(float(old), extreme) if old is not None else extreme
                )
                return True
            return False
        if name == "causal_level_retest":
            if level is None or not atr:
                return False
            touched = low <= level + float(params.get("retest_atr", .4)) * float(atr) if sign > 0 \
                else high >= level - float(params.get("retest_atr", .4)) * float(atr)
            preserved = close >= level if sign > 0 else close <= level
            return touched and preserved
        if name == "close_back_inside":
            return level is not None and (close > level if sign > 0 else close < level)
        if name == "bounded_reset":
            if rsi is None:
                return False
            threshold = float(params.get(
                "rsi_reset", 45 if self.program.direction == "LONG" else 55))
            return float(rsi) <= threshold if sign > 0 else float(rsi) >= threshold
        if name == "structure_preserved":
            structure = self.anchor.get("structure_level")
            return structure is not None and (close > float(structure) if sign > 0 else close < float(structure))
        if name in {"confirmed_expansion", "directional_structure"}:
            if name == "confirmed_expansion":
                if not atr or not previous:
                    return False
                current_range = high - low
                previous_range = float(previous["high"]) - float(previous["low"])
                return current_range >= float(atr) and previous_range >= .75 * float(atr)
            ema, ma60, slope = feature.get("ema_20"), feature.get("sma_60"), feature.get("ema_20_slope")
            return None not in (ema, ma60, slope) and _direction(
                self.program, float(ema) > float(ma60) and float(slope) > 0)
        if name in {"continuation_reclaim", "reversal_confirmation", "continuation_trigger"}:
            if not previous:
                return False
            previous_close = float(previous["close"])
            ema = feature.get("ema_20")
            if name == "reversal_confirmation":
                return close > float(previous["high"]) if sign > 0 else close < float(previous["low"])
            if name == "continuation_trigger":
                return close > float(previous["high"]) if sign > 0 else close < float(previous["low"])
            return ema is not None and (
                previous_close <= float(ema) < close if sign > 0
                else previous_close >= float(ema) > close
            )
        return name == "trigger"

    def _invalid(self, name: str, index: int) -> bool:
        candle, feature = self.candles[index], self.features[index]
        close = float(candle["close"]); level = self.anchor.get("level")
        sign = 1 if self.program.direction == "LONG" else -1
        if name in {"close_through_level", "reattempt_breakout"} and level is not None:
            return close < level if sign > 0 else close > level
        if name == "runaway_breakout" and level is not None and feature.get("atr"):
            return close > level + 2 * float(feature["atr"]) if sign > 0 else close < level - 2 * float(feature["atr"])
        if name == "structure_break" and self.anchor.get("structure_level") is not None:
            structure = float(self.anchor["structure_level"])
            return close < structure if sign > 0 else close > structure
        if name == "isolated_noise":
            return False
        return False

    def _transition(self, prior: str, resulting: str, index: int, reason: str,
                    context: Mapping[str, Any] | None) -> None:
        timestamp = int(self.candles[index]["ts"])
        self.transitions.append({
            "timestamp": timestamp, "prior_state": prior, "resulting_state": resulting,
            "reason": reason, "sequence_id": self.sequence_id,
            "stage_activation_timestamp": self.stage_activation_timestamp,
            "transition_timestamp": timestamp,
            "causal_feature_timestamps": {
                "primary": timestamp,
                "anchor": self.anchor.get("feature_timestamp"),
                "context": context.get("context_candle_timestamp") if context else None,
            },
        })

    def evaluate(self, index: int) -> dict[str, Any]:
        candle, feature = self.candles[index], self.features[index]
        context = self.aligned_context[index]
        prior = self.state
        action, reason = "WAIT", None
        stages = self.program.sequence.stages
        if self.state == "IDLE":
            first = stages[0]
            if _context_ok(self.program, context) and self._predicate(
                    first.predicate.name, index, first.predicate.params()):
                self.sequence_number += 1
                self.sequence_id = canonical_hash({
                    "program": self.identity, "sequence": self.sequence_number,
                    "activation": int(candle["ts"]),
                })
                self.stage_index = 0; self.activation_index = index
                self.stage_activation_timestamp = int(candle["ts"])
                self._capture_anchor(index)
                self.state = "STAGE_1_ARMED"; reason = "STAGE_PREDICATE_TRUE"
        elif self.state.startswith("STAGE_"):
            assert self.stage_index is not None and self.activation_index is not None
            stage = stages[self.stage_index]
            elapsed = index - self.activation_index
            if not _context_ok(self.program, context) or self._invalid(stage.invalidation.name, index):
                self.state = "INVALIDATED"; reason = stage.invalidation.name
            elif elapsed > stage.maximum_bars:
                self.state = "INVALIDATED"; reason = stage.expiration_reason
            elif elapsed >= stage.minimum_bars and self._predicate(
                    stage.transition.name, index, stage.transition.params()):
                if self.stage_index == len(stages) - 1:
                    self.state = "TRIGGERED"; action = self.program.direction; reason = "SEQUENCE_COMPLETE"
                else:
                    self.stage_index += 1; self.activation_index = index
                    self.stage_activation_timestamp = int(candle["ts"])
                    self.state = f"STAGE_{self.stage_index + 1}_ARMED"
                    reason = stage.transition.name
        elif self.state == "TRIGGERED":
            self.state = "REARM_REQUIRED"; reason = "ONE_SHOT_TRIGGER_CONSUMED"
        elif self.state in {"REARM_REQUIRED", "INVALIDATED"}:
            first = stages[0]
            if not self._predicate(first.predicate.name, index, first.predicate.params()):
                self.state = "IDLE"; reason = "REARM_CONDITION_MET"
                self.stage_index = self.activation_index = None
                self.stage_activation_timestamp = None; self.sequence_id = None; self.anchor = {}
        if self.state != prior:
            self._transition(prior, self.state, index, str(reason), context)

        atr = feature.get("atr")
        stop_distance, target_r = self._geometry(index)
        geometry_valid = bool(
            stop_distance and math.isfinite(stop_distance) and stop_distance > 0
            and target_r and math.isfinite(target_r)
            and target_r >= self.program.geometry.minimum_reward_risk
        )
        if action != "WAIT" and not geometry_valid:
            action = "WAIT"
        evidence = {
            "source_candle_timestamp": int(candle["ts"]),
            "program_identity": self.identity, "geometry_identity": self.program.geometry.identity,
            "instrument": self.instrument, "timeframe": self.program.timeframe,
            "fold_identity": self.fold_identity, "prior_state": prior,
            "resulting_state": self.state, "sequence_id": self.sequence_id,
            "stage_activation_timestamp": self.stage_activation_timestamp,
            "transition_timestamp": int(candle["ts"]) if self.state != prior else None,
            "context_candle_timestamp": context.get("context_candle_timestamp") if context else None,
            "context_candle_close_timestamp": context.get("context_candle_close_timestamp") if context else None,
            "lower_decision_timestamp": context.get("lower_decision_timestamp") if context else int(candle["ts"]) + TIMEFRAME_SECONDS[self.program.timeframe],
            "stop_distance": stop_distance, "target_r": target_r,
            "geometry_valid": geometry_valid,
        }
        if action != "WAIT":
            self.trigger_evidence[int(candle["ts"])] = evidence
        return {
            "action": action, "atr": atr, "stop_distance": stop_distance,
            "target_r": target_r, "time_stop_bars": self.program.geometry.time_stop_bars,
            "score": 0.0, "signal_ts": int(candle["ts"]),
            "signal_id": f"temporal:{self.identity[:16]}:{int(candle['ts'])}",
            "strategy_version": STRATEGY_PROGRAM_SCHEMA_VERSION,
            "config_hash": self.identity, "warmed": bool(feature.get("warm")),
            "evidence": evidence,
        }

    def _geometry(self, index: int) -> tuple[float | None, float | None]:
        candle, feature = self.candles[index], self.features[index]
        atr = feature.get("atr")
        if not atr:
            return None, None
        geometry = self.program.geometry
        close = float(candle["close"])
        if geometry.stop_type == "FAILED_BREAKOUT_EXTREME_ATR" and self.anchor.get("extreme") is not None:
            extreme = float(self.anchor["extreme"])
            stop = extreme + float(atr) * geometry.stop_parameter * (-1 if self.program.direction == "LONG" else 1)
            distance = abs(close - stop)
        elif geometry.stop_type == "LEVEL_ATR" and self.anchor.get("level") is not None:
            level = float(self.anchor["level"])
            stop = level - float(atr) * geometry.stop_parameter if self.program.direction == "LONG" else level + float(atr) * geometry.stop_parameter
            distance = abs(close - stop)
        else:
            distance = float(atr) * geometry.stop_parameter
        if geometry.exit_type == "FIXED_R":
            return distance, float(geometry.target_parameter)
        target = (
            feature.get("bb_mid")
            if geometry.exit_type == "BOLLINGER_MIDLINE"
            else self.anchor.get("opposite_level")
        )
        if target is None or distance <= 0:
            return distance, None
        reward = float(target) - close if self.program.direction == "LONG" else close - float(target)
        return distance, reward / distance


def evaluate_trigger_vector(program: TemporalStrategyProgram, candles: list[dict[str, Any]],
                            features: list[dict[str, Any]], start_ts: int, end_ts: int, *,
                            instrument: str, dataset_fingerprint: str,
                            fold_identity: Mapping[str, Any],
                            aligned_context: list[dict[str, Any] | None] | None = None
                            ) -> tuple[tuple[int, ...], TemporalSequenceEvaluator]:
    evaluator = TemporalSequenceEvaluator(
        program, candles, features, instrument=instrument,
        dataset_fingerprint=dataset_fingerprint, fold_identity=fold_identity,
        aligned_context=aligned_context,
    )
    for index, candle in enumerate(candles):
        if start_ts <= int(candle["ts"]) < end_ts:
            evaluator.evaluate(index)
    return tuple(sorted(evaluator.trigger_evidence)), evaluator


def run_program_backtest(program: TemporalStrategyProgram, candles: list[dict[str, Any]],
                         features: list[dict[str, Any]], start_ts: int, end_ts_exclusive: int,
                         *, instrument: str, dataset_fingerprint: str,
                         fold_identity: Mapping[str, Any],
                         aligned_context: list[dict[str, Any] | None] | None = None,
                         trading_fee: float = .0005, slippage: float = .0003) -> dict[str, Any]:
    if end_ts_exclusive > DEVELOPMENT_END_TS:
        raise ValueError("Phase 6B cannot access Primary holdout or Final OOT")
    evaluator = TemporalSequenceEvaluator(
        program, candles, features, instrument=instrument,
        dataset_fingerprint=dataset_fingerprint, fold_identity=fold_identity,
        aligned_context=aligned_context,
    )
    parameters = StrategyParameters(
        initial_capital=10_000, risk_per_trade=.01, max_notional_fraction=.25,
        trading_fee=trading_fee, slippage=slippage, cooldown_bars=0,
        enable_long=program.direction == "LONG", enable_short=program.direction == "SHORT",
    )
    result = run_execution_backtest(
        candles, instrument, program.timeframe, parameters, start_ts,
        end_ts_exclusive - TIMEFRAME_SECONDS[program.timeframe],
        signal_provider=lambda candle, index: evaluator.evaluate(index),
    )
    result["program_evaluations"] = evaluator.trigger_evidence
    result["lifecycle_evidence"] = {
        "version": "temporal-lifecycle-v1",
        "state_isolation": {
            "program": program.semantic_identity, "geometry": program.geometry.identity,
            "instrument": instrument, "timeframe": program.timeframe,
            "fold": dict(fold_identity),
        },
        "transitions": evaluator.transitions,
        "trigger_count": len(evaluator.trigger_evidence),
        "maximum_triggers_per_sequence": max(Counter(
            evidence["sequence_id"] for evidence in evaluator.trigger_evidence.values()
        ).values(), default=0),
    }
    result["program_evidence"] = {
        "program_identity": evaluator.identity, "canonical_ast": program.canonical_ast(),
        "historical_input_policy": "PRICE_ONLY_OHLCV", "flow_history_requested": False,
        "maximum_candle_timestamp_loaded": max(int(row["ts"]) for row in candles),
        "development_end_exclusive": DEVELOPMENT_END_TS,
    }
    return result
