"""Causal lifecycle, alignment, screening, and canonical execution for programs."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
import math
import statistics
from typing import Any, Iterable, Mapping

from .backtest_engine import run_execution_backtest
from .discovery_diagnostics import fixed_path_cost_attribution
from .discovery_features import build_features
from .discovery_identity import canonical_json_hash
from .okx_history import TIMEFRAME_SECONDS
from .strategy_program import (
    STRATEGY_PROGRAM_FEATURE_VERSION, STRATEGY_PROGRAM_SCHEMA_VERSION,
    StrategyProgram, Primitive, build_program_identity,
)
from .strategy_rules import StrategyParameters

DEVELOPMENT_START_TS = 1704067200  # 2024-01-01T00:00:00Z
DEVELOPMENT_END_TS = 1746057600  # 2025-05-01T00:00:00Z, exclusive
EVENT_STUDY_VERSION = "automatic-strategy-event-study-v1"
LIFECYCLE_VERSION = "strategy-program-lifecycle-v1"
LIFECYCLE_STATES = ("IDLE", "ARMED", "TRIGGERED", "REARM_REQUIRED", "INVALIDATED")


def _median(values: Iterable[float | None]) -> float | None:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.median(finite) if finite else None


def build_program_features(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build only the declared PRICE_ONLY/OHLCV feature registry."""
    base = build_features(candles, {
        "ma_periods": [20, 60, 200], "atr_period": 14, "bb_period": 20,
        "rsi_period": 14, "volume_period": 20,
    })
    closes = [float(row["close"]) for row in candles]
    output: list[dict[str, Any]] = []
    for index, (row, feature) in enumerate(zip(candles, base)):
        close = closes[index]
        high, low, open_ = float(row["high"]), float(row["low"]), float(row["open"])
        candle_range = max(high - low, 1e-12)
        recent_high, recent_low = feature["recent_high"], feature["recent_low"]
        range_width = (
            float(recent_high) - float(recent_low)
            if recent_high is not None and recent_low is not None else None
        )
        enriched = {
            **feature,
            "candle_return": close / closes[index - 1] - 1 if index else None,
            "upper_wick_ratio": (high - max(open_, close)) / candle_range,
            "lower_wick_ratio": (min(open_, close) - low) / candle_range,
            "ema_20_slope": (
                float(feature["ema_20"]) - float(base[index - 4]["ema_20"])
                if index >= 4 and feature["ema_20"] is not None and base[index - 4]["ema_20"] is not None
                else None
            ),
            "distance_ema20_atr": (
                (close - float(feature["ema_20"])) / float(feature["atr"])
                if feature["ema_20"] is not None and feature["atr"] else None
            ),
            "distance_ma60_pct": (
                close / float(feature["sma_60"]) - 1 if feature["sma_60"] else None
            ),
            "distance_ma200_pct": (
                close / float(feature["sma_200"]) - 1 if feature["sma_200"] else None
            ),
            "range_location": (
                (close - float(recent_low)) / range_width if range_width and range_width > 0 else None
            ),
            "feature_version": STRATEGY_PROGRAM_FEATURE_VERSION,
        }
        output.append(enriched)
    return output


def align_higher_timeframe(lower_candles: list[dict[str, Any]], lower_timeframe: str,
                           context_candles: list[dict[str, Any]], context_timeframe: str,
                           context_features: list[dict[str, Any]] | None = None) -> list[dict[str, Any] | None]:
    """Use the latest context candle fully closed by each lower decision close."""
    if context_timeframe not in TIMEFRAME_SECONDS and context_timeframe != "1D":
        raise ValueError("unsupported context timeframe")
    lower_seconds = TIMEFRAME_SECONDS[lower_timeframe]
    context_seconds = TIMEFRAME_SECONDS.get(context_timeframe, 86400)
    ordered = sorted(context_candles, key=lambda row: int(row["ts"]))
    features = context_features or build_program_features(ordered)
    close_timestamps = [int(row["ts"]) + context_seconds for row in ordered]
    aligned: list[dict[str, Any] | None] = []
    for lower in lower_candles:
        decision_timestamp = int(lower["ts"]) + lower_seconds
        pointer = bisect_right(close_timestamps, decision_timestamp) - 1
        if pointer < 0:
            aligned.append(None)
            continue
        aligned.append({
            "context_candle_timestamp": int(ordered[pointer]["ts"]),
            "context_candle_close_timestamp": close_timestamps[pointer],
            "lower_decision_timestamp": decision_timestamp,
            "candle": ordered[pointer],
            "feature": features[pointer],
        })
    return aligned


def _direction_test(direction: str, positive: bool) -> bool:
    return positive if direction == "LONG" else not positive


def _regime(program: StrategyProgram, feature: Mapping[str, Any]) -> bool:
    node = program.regime
    close = feature.get("_close")
    if not feature.get("warm") or close is None:
        return False
    if node.name == "aligned_trend":
        ema, ma60, ma200, slope = (
            feature.get("ema_20"), feature.get("sma_60"),
            feature.get("sma_200"), feature.get("ema_20_slope"),
        )
        if None in (ema, ma60, ma200, slope):
            return False
        # Price may temporarily cross EMA20 during a pullback setup; the regime
        # is the slower aligned structure, not a redundant price-location gate.
        bullish = float(ema) > float(ma60) > float(ma200) and float(slope) > 0
        bearish = float(ema) < float(ma60) < float(ma200) and float(slope) < 0
        return bullish if program.direction == "LONG" else bearish
    if node.name == "volatility_compression":
        width = feature.get("bb_width")
        return width is not None and float(width) <= float(node.params()["maximum_bandwidth"])
    if node.name == "low_volatility_range":
        width, slope = feature.get("bb_width"), feature.get("sma_60_slope")
        return (
            width is not None and slope is not None
            and float(width) <= float(node.params()["maximum_bandwidth"])
            and abs(float(slope) / float(close)) <= .003
        )
    return False


def _context(program: StrategyProgram, aligned: Mapping[str, Any] | None) -> bool:
    if program.higher_timeframe_context is None:
        return True
    if not aligned:
        return False
    feature = {**aligned["feature"], "_close": float(aligned["candle"]["close"])}
    proxy = StrategyProgram(
        program.timeframe, program.direction,
        Primitive.make("regime", "aligned_trend", {"mode": "trend"}, program.direction),
        program.setup, program.trigger, program.invalidation, program.exit, program.sizing,
    )
    return _regime(proxy, feature)


def _setup_node(node: Primitive, program: StrategyProgram, candle: Mapping[str, Any],
                feature: Mapping[str, Any]) -> bool:
    direction = program.direction
    close, high, low = float(candle["close"]), float(candle["high"]), float(candle["low"])
    params = node.params()
    if node.name == "ema_excursion":
        distance = feature.get("distance_ema20_atr")
        if distance is None:
            return False
        threshold = float(params["atr_distance"])
        return float(distance) <= -threshold if direction == "LONG" else float(distance) >= threshold
    if node.name == "rsi_reset":
        rsi = feature.get("rsi")
        if rsi is None:
            return False
        return float(rsi) <= float(params["threshold"]) if direction == "LONG" else float(rsi) >= float(params["threshold"])
    if node.name == "consolidation_level":
        level = feature.get("recent_high" if direction == "LONG" else "recent_low")
        atr = feature.get("atr")
        return level is not None and atr and abs(close - float(level)) <= float(atr)
    if node.name == "volume_contraction":
        ratio = feature.get("volume_ratio")
        return ratio is not None and float(ratio) <= float(params["maximum_ratio"])
    if node.name == "compression":
        width = feature.get("bb_width")
        return width is not None and float(width) <= float(params["maximum_bandwidth"])
    if node.name == "range_location":
        location = feature.get("range_location")
        if location is None:
            return False
        minimum = float(params["minimum_location"])
        return float(location) >= minimum if direction == "LONG" else float(location) <= 1 - minimum
    if node.name == "bollinger_excursion":
        band = feature.get("bb_lower" if direction == "LONG" else "bb_upper")
        atr = feature.get("atr")
        if band is None or not atr:
            return False
        depth = float(params["atr_depth"]) * float(atr)
        return low <= float(band) - depth if direction == "LONG" else high >= float(band) + depth
    return False


def _trigger(program: StrategyProgram, candle: Mapping[str, Any], feature: Mapping[str, Any],
             previous_candle: Mapping[str, Any] | None, previous_feature: Mapping[str, Any] | None) -> bool:
    if previous_candle is None or previous_feature is None:
        return False
    direction = program.direction
    close, previous_close = float(candle["close"]), float(previous_candle["close"])
    node = program.trigger
    if node.name == "reclaim_crossing":
        current, previous = feature.get("ema_20"), previous_feature.get("ema_20")
        if current is None or previous is None:
            return False
        return (
            previous_close <= float(previous) and close > float(current)
            if direction == "LONG"
            else previous_close >= float(previous) and close < float(current)
        )
    if node.name in {"completed_level_breakout", "compression_breakout"}:
        level = feature.get("recent_high" if direction == "LONG" else "recent_low")
        previous_level = previous_feature.get("recent_high" if direction == "LONG" else "recent_low")
        if level is None or previous_level is None:
            return False
        return (
            previous_close <= float(previous_level) and close > float(level)
            if direction == "LONG"
            else previous_close >= float(previous_level) and close < float(level)
        )
    if node.name == "bollinger_reentry":
        band = feature.get("bb_lower" if direction == "LONG" else "bb_upper")
        previous_band = previous_feature.get("bb_lower" if direction == "LONG" else "bb_upper")
        if band is None or previous_band is None:
            return False
        return (
            previous_close <= float(previous_band) and close > float(band)
            if direction == "LONG"
            else previous_close >= float(previous_band) and close < float(band)
        )
    return False


class StrategyProgramEvaluator:
    """One isolated causal state machine for one candidate/asset/timeframe/fold."""

    def __init__(self, program: StrategyProgram, candles: list[dict[str, Any]],
                 features: list[dict[str, Any]], *, instrument: str,
                 dataset_fingerprint: str, fold_identity: Mapping[str, Any],
                 aligned_context: list[dict[str, Any] | None] | None = None):
        self.program = program.normalized()
        self.candles = candles
        self.features = features
        self.instrument = instrument
        self.fold_identity = dict(fold_identity)
        self.identity = build_program_identity(
            self.program, dataset_fingerprint=dataset_fingerprint,
            instrument=instrument, fold_identity=fold_identity,
        )
        self.aligned_context = aligned_context or [None] * len(candles)
        self.state = "IDLE"
        self.setup_sequence = 0
        self.active_setup_id: str | None = None
        self.transitions: list[dict[str, Any]] = []
        self.trigger_evidence: dict[int, dict[str, Any]] = {}

    def evaluate(self, index: int) -> dict[str, Any]:
        candle = self.candles[index]
        feature = {**self.features[index], "_close": float(candle["close"])}
        previous_candle = self.candles[index - 1] if index else None
        previous_feature = self.features[index - 1] if index else None
        warmed = bool(feature.get("warm"))
        regime = _regime(self.program, feature)
        context_ok = _context(self.program, self.aligned_context[index])
        setup = warmed and regime and context_ok and all(
            _setup_node(node, self.program, candle, feature) for node in self.program.setup
        )
        trigger = warmed and _trigger(
            self.program, candle, feature, previous_candle, previous_feature
        )
        prior = self.state
        action = "WAIT"
        if self.state == "IDLE" and setup:
            self.setup_sequence += 1
            self.active_setup_id = canonical_json_hash({
                "program": self.identity, "fold": self.fold_identity,
                "sequence": self.setup_sequence, "activation_ts": int(candle["ts"]),
            })
            self.state = "ARMED"
        elif self.state == "ARMED":
            if not regime or not context_ok:
                self.state = "INVALIDATED"
            elif trigger:
                self.state = "TRIGGERED"
                action = self.program.direction
        elif self.state == "TRIGGERED":
            self.state = "REARM_REQUIRED"
        elif self.state in {"REARM_REQUIRED", "INVALIDATED"} and not setup and not trigger:
            self.state = "IDLE"
            self.active_setup_id = None
        if self.state != prior:
            transition = {
                "timestamp": int(candle["ts"]), "prior_state": prior,
                "resulting_state": self.state, "setup_id": self.active_setup_id,
                "setup_true": setup, "trigger_true": trigger,
            }
            self.transitions.append(transition)
        atr = feature.get("atr")
        stop_multiplier = float(self.program.invalidation.params()["atr_multiplier"])
        stop_distance = float(atr) * stop_multiplier if atr else None
        target_r = float(self.program.exit.params()["target_r"])
        context_evidence = self.aligned_context[index]
        evidence = {
            "source_candle_timestamp": int(candle["ts"]),
            "program_identity": self.identity,
            "fold_identity": self.fold_identity,
            "instrument": self.instrument,
            "timeframe": self.program.timeframe,
            "prior_state": prior,
            "resulting_state": self.state,
            "setup_id": self.active_setup_id,
            "setup_true": setup,
            "trigger_true": trigger,
            "trigger_timestamp": int(candle["ts"]) if action != "WAIT" else None,
            "context_candle_timestamp": (
                context_evidence["context_candle_timestamp"] if context_evidence else None
            ),
            "context_candle_close_timestamp": (
                context_evidence["context_candle_close_timestamp"] if context_evidence else None
            ),
            "lower_decision_timestamp": (
                context_evidence["lower_decision_timestamp"] if context_evidence else
                int(candle["ts"]) + TIMEFRAME_SECONDS[self.program.timeframe]
            ),
            "stop_distance": stop_distance,
            "target_r": target_r,
            "geometry_valid": bool(stop_distance and stop_distance > 0 and target_r >= 1.25),
        }
        if action != "WAIT":
            self.trigger_evidence[int(candle["ts"])] = evidence
        return {
            "action": action,
            "atr": atr,
            "stop_distance": stop_distance,
            "target_r": target_r,
            "score": 0.0,
            "signal_ts": int(candle["ts"]),
            "signal_id": f"program:{self.identity[:16]}:{int(candle['ts'])}",
            "strategy_version": STRATEGY_PROGRAM_SCHEMA_VERSION,
            "config_hash": self.identity,
            "warmed": warmed,
            "evidence": evidence,
        }


def evaluate_trigger_vector(program: StrategyProgram, candles: list[dict[str, Any]],
                            features: list[dict[str, Any]], start_ts: int, end_ts: int,
                            *, instrument: str, dataset_fingerprint: str,
                            fold_identity: Mapping[str, Any],
                            aligned_context: list[dict[str, Any] | None] | None = None
                            ) -> tuple[tuple[int, ...], StrategyProgramEvaluator]:
    evaluator = StrategyProgramEvaluator(
        program, candles, features, instrument=instrument,
        dataset_fingerprint=dataset_fingerprint, fold_identity=fold_identity,
        aligned_context=aligned_context,
    )
    for index, candle in enumerate(candles):
        timestamp = int(candle["ts"])
        if start_ts <= timestamp < end_ts:
            evaluator.evaluate(index)
    return tuple(sorted(evaluator.trigger_evidence)), evaluator


def run_program_backtest(program: StrategyProgram, candles: list[dict[str, Any]],
                         features: list[dict[str, Any]], start_ts: int, end_ts_exclusive: int,
                         *, instrument: str, dataset_fingerprint: str,
                         fold_identity: Mapping[str, Any],
                         aligned_context: list[dict[str, Any] | None] | None = None,
                         trading_fee: float = .0005, slippage: float = .0003) -> dict[str, Any]:
    if end_ts_exclusive > DEVELOPMENT_END_TS:
        raise ValueError("Phase 6A cannot access Primary holdout or Final OOT")
    evaluator = StrategyProgramEvaluator(
        program, candles, features, instrument=instrument,
        dataset_fingerprint=dataset_fingerprint, fold_identity=fold_identity,
        aligned_context=aligned_context,
    )

    def provider(candle: dict[str, Any], index: int) -> dict[str, Any]:
        return evaluator.evaluate(index)

    sizing = program.sizing.params()
    parameters = StrategyParameters(
        initial_capital=10_000.0,
        risk_per_trade=float(sizing["risk_per_trade"]),
        max_notional_fraction=float(sizing["maximum_notional_fraction"]),
        trading_fee=trading_fee,
        slippage=slippage,
        cooldown_bars=0,
        enable_long=program.direction == "LONG",
        enable_short=program.direction == "SHORT",
    )
    effective_end = end_ts_exclusive - TIMEFRAME_SECONDS[program.timeframe]
    result = run_execution_backtest(
        candles, instrument, program.timeframe, parameters, start_ts, effective_end,
        signal_provider=provider,
    )
    result["program_evaluations"] = evaluator.trigger_evidence
    result["lifecycle_evidence"] = {
        "version": LIFECYCLE_VERSION,
        "state_isolation": {
            "candidate": program.semantic_identity, "instrument": instrument,
            "timeframe": program.timeframe, "fold": dict(fold_identity),
        },
        "transitions": evaluator.transitions,
        "trigger_count": len(evaluator.trigger_evidence),
        "maximum_triggers_per_setup": max(
            Counter(
                evidence["setup_id"] for evidence in evaluator.trigger_evidence.values()
                if evidence["setup_id"]
            ).values(),
            default=0,
        ),
    }
    result["program_evidence"] = {
        "program_identity": evaluator.identity,
        "semantic_identity": program.semantic_identity,
        "canonical_ast": program.canonical_ast(),
        "feature_version": STRATEGY_PROGRAM_FEATURE_VERSION,
        "historical_input_policy": "PRICE_ONLY_OHLCV",
        "flow_history_requested": False,
        "maximum_candle_timestamp_loaded": max(int(row["ts"]) for row in candles),
        "development_end_exclusive": DEVELOPMENT_END_TS,
    }
    return result


def event_study(program: StrategyProgram, trigger_timestamps: Iterable[int],
                candles: list[dict[str, Any]], features: list[dict[str, Any]],
                fold_start: int, fold_end: int, horizons: tuple[int, ...] = (1, 2, 4, 8, 16)
                ) -> dict[str, Any]:
    """Attach diagnostic labels after signals, strictly within one fold."""
    visible = [(index, row) for index, row in enumerate(candles)
               if fold_start <= int(row["ts"]) < fold_end]
    positions = {int(row["ts"]): position for position, (_, row) in enumerate(visible)}
    events: list[dict[str, Any]] = []
    stop_multiplier = float(program.invalidation.params()["atr_multiplier"])
    target_r = float(program.exit.params()["target_r"])
    sign = 1.0 if program.direction == "LONG" else -1.0
    for timestamp in sorted(trigger_timestamps):
        if timestamp not in positions:
            continue
        position = positions[timestamp]
        source_index, origin = visible[position]
        close = float(origin["close"])
        atr = features[source_index].get("atr")
        if not atr or not math.isfinite(float(atr)):
            continue
        stop_distance = float(atr) * stop_multiplier
        stop = close - stop_distance if program.direction == "LONG" else close + stop_distance
        target = close + stop_distance * target_r if program.direction == "LONG" else close - stop_distance * target_r
        labels: dict[str, Any] = {}
        for horizon in horizons:
            end = position + horizon
            if end >= len(visible):
                continue
            path = [row for _, row in visible[position + 1:end + 1]]
            outcome = "NEITHER"
            for row in path:
                stop_hit = (
                    float(row["low"]) <= stop if program.direction == "LONG"
                    else float(row["high"]) >= stop
                )
                target_hit = (
                    float(row["high"]) >= target if program.direction == "LONG"
                    else float(row["low"]) <= target
                )
                if stop_hit or target_hit:
                    outcome = "STOP_FIRST" if stop_hit else "TARGET_FIRST"
                    break
            final_close = float(path[-1]["close"])
            favorable = max(
                float(row["high"]) - close if program.direction == "LONG" else close - float(row["low"])
                for row in path
            )
            adverse = max(
                close - float(row["low"]) if program.direction == "LONG" else float(row["high"]) - close
                for row in path
            )
            labels[str(horizon)] = {
                "outcome_timestamp": int(path[-1]["ts"]),
                "direction_adjusted_forward_return": sign * (final_close / close - 1) * 100,
                "mfe": favorable / close * 100,
                "mae": adverse / close * 100,
                "stop_first": outcome == "STOP_FIRST",
                "target_first": outcome == "TARGET_FIRST",
            }
        events.append({"trigger_timestamp": timestamp, "fold_start": fold_start, "fold_end": fold_end,
                       "labels": labels})
    aggregate: dict[str, Any] = {}
    for horizon in horizons:
        labels = [event["labels"][str(horizon)] for event in events if str(horizon) in event["labels"]]
        returns = [label["direction_adjusted_forward_return"] for label in labels]
        aggregate[str(horizon)] = {
            "event_count": len(labels),
            "median_forward_return": _median(returns),
            "profitable_event_ratio": sum(value > 0 for value in returns) / len(returns) if returns else None,
            "median_mfe": _median(label["mfe"] for label in labels),
            "median_mae": _median(label["mae"] for label in labels),
            "stop_first_ratio": sum(label["stop_first"] for label in labels) / len(labels) if labels else None,
            "target_first_ratio": sum(label["target_first"] for label in labels) / len(labels) if labels else None,
        }
    return {
        "version": EVENT_STUDY_VERSION,
        "diagnostic_labels_only": True,
        "signals_hash": canonical_json_hash(list(sorted(trigger_timestamps))),
        "fold_start": fold_start,
        "fold_end_exclusive": fold_end,
        "events": events,
        "aggregate": aggregate,
    }


def behavior_deduplicate(items: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Deduplicate trigger vectors within the same instrument/timeframe family."""
    families: dict[tuple[str, str, tuple[int, ...]], list[dict[str, Any]]] = {}
    for item in items:
        key = (item["instrument"], item["program"].timeframe, tuple(item["trigger_vector"]))
        families.setdefault(key, []).append(item)
    selected: list[dict[str, Any]] = []
    aliases: dict[str, list[str]] = {}
    for key, family in sorted(families.items(), key=lambda pair: canonical_json_hash(pair[0])):
        representative = min(
            family,
            key=lambda item: (item["program"].complexity, item["program"].semantic_identity),
        )
        selected.append(representative)
        aliases[representative["program"].semantic_identity] = sorted(
            item["program"].semantic_identity for item in family
        )
    selected.sort(key=lambda item: item["program"].semantic_identity)
    return selected, aliases


def summarize_backtests(fold_results: list[dict[str, Any]], benchmark_results: list[dict[str, Any]],
                        timeframe: str) -> dict[str, Any]:
    costs = [fixed_path_cost_attribution(result, 10_000.0) for result in fold_results]
    gross = [item["gross_return_before_costs"] for item in costs]
    net = [float(result["metrics"]["total_return"]) for result in fold_results]
    raw_benchmark = [
        (float(item["raw_exit_price"]) / float(item["raw_entry_price"]) - 1) * 100
        for item in benchmark_results
    ]
    net_benchmark = [float(item["total_return"]) for item in benchmark_results]
    gross_excess = [value - benchmark for value, benchmark in zip(gross, raw_benchmark)]
    net_excess = [value - benchmark for value, benchmark in zip(net, net_benchmark)]
    trades = [trade for result in fold_results for trade in result["trades"]]
    positive = [max(0.0, value) for value in gross]
    exits = Counter(trade["exit_reason"] for trade in trades)
    return {
        "fold_gross_returns": gross,
        "fold_net_returns": net,
        "fold_gross_excess_returns": gross_excess,
        "fold_net_excess_returns": net_excess,
        "median_gross_return": _median(gross),
        "median_net_return": _median(net),
        "median_gross_excess_return": _median(gross_excess),
        "median_net_excess_return": _median(net_excess),
        "gross_excess_positive_fold_ratio": sum(value > 0 for value in gross_excess) / len(gross_excess),
        "profitable_fold_ratio": sum(value > 0 for value in net) / len(net),
        "benchmark_beating_fold_ratio": sum(value > benchmark for value, benchmark in zip(net, net_benchmark)) / len(net),
        "worst_fold_return": min(net),
        "maximum_drawdown": max(float(result["metrics"]["maximum_drawdown"]) for result in fold_results),
        "trade_count": len(trades),
        "return_concentration": max(positive) / sum(positive) if sum(positive) else None,
        "average_holding_time_seconds": statistics.mean(
            [float(trade["holding_seconds"]) for trade in trades]
        ) if trades else None,
        "stop_exit_ratio": exits["STOP_LOSS"] / len(trades) if trades else None,
        "target_exit_ratio": exits["TAKE_PROFIT"] / len(trades) if trades else None,
        "time_exit_ratio": (
            (exits["END_OF_DATA"] + exits["TIME_STOP"]) / len(trades) if trades else None
        ),
        "fee_drag": sum(item["fee_drag_return"] for item in costs),
        "slippage_drag": sum(item["slippage_drag_return"] for item in costs),
        "net_return": sum(net),
        "gross_return": sum(gross),
        "cost_attribution": costs,
        "fold_count": len(fold_results),
        "timeframe": timeframe,
    }
