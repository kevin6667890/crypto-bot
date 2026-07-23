"""Typed and deterministic Phase 6A strategy-program grammar.

The grammar composes bounded OHLCV-only primitives.  It is intentionally
separate from the retired template registries: generation produces immutable
program ASTs, not parameter variants of v1/v2/v2.1 templates.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from .backtest_engine import SHARED_EXECUTION_ENGINE_VERSION

STRATEGY_PROGRAM_SCHEMA_VERSION = "strategy-program-schema-v1"
STRATEGY_GRAMMAR_VERSION = "strategy-grammar-v1"
STRATEGY_SEARCH_POLICY_VERSION = "automatic-strategy-search-v1"
STRATEGY_PROGRAM_IDENTITY_VERSION = "strategy-program-identity-v1"
STRATEGY_PROGRAM_FEATURE_VERSION = "strategy-program-features-v1"
STRATEGY_PROGRAM_EXECUTION_VERSION = SHARED_EXECUTION_ENGINE_VERSION
SEMANTIC_SEED = 20260723
PRIMARY_TIMEFRAMES = ("15m", "1H", "4H")
ALLOWED_CONTEXT = {"15m": ("1H", "4H"), "1H": ("4H", "1D"), "4H": ("1D",)}

SEARCH_BUDGETS = {
    "raw_structurally_valid": 600,
    "semantic": 200,
    "event_study": 90,
    "btc_backtest": 45,
    "cross_asset": 12,
    "neighborhood_per_program": 5,
}

PRIMITIVE_REGISTRY: dict[str, tuple[str, ...]] = {
    "source": ("OHLCV", "candle_return", "body_wick_proportions"),
    "trend": ("EMA20", "MA60", "MA200", "MA_slope", "normalized_MA_distance"),
    "volatility": ("ATR", "ATR_percentage", "Bollinger_mid_upper_lower_bandwidth"),
    "oscillator": ("RSI",),
    "participation": ("rolling_volume_ratio",),
    "levels": ("confirmed_rolling_high", "confirmed_rolling_low", "normalized_level_distance"),
}

REJECTION_CODES = (
    "INVALID_TIMEFRAME",
    "INVALID_DIRECTION",
    "INVALID_HIGHER_TIMEFRAME_ALIGNMENT",
    "DIRECTION_MISMATCH",
    "INCOMPATIBLE_REGIME_SETUP",
    "INCOMPATIBLE_SETUP_TRIGGER",
    "BREAKOUT_WITHOUT_COMPLETED_LEVEL",
    "WRONG_SIDE_TARGET",
    "NONFINITE_GEOMETRY",
    "REWARD_RISK_BELOW_MINIMUM",
    "TRIGGER_EQUALS_SETUP",
    "INSUFFICIENT_WARMUP",
    "CONTRADICTORY_PREDICATES",
    "DUPLICATE_PREDICATE",
    "REDUNDANT_TREND_FILTER",
    "REDUNDANT_OSCILLATOR",
    "EXCESSIVE_COMPLEXITY",
    "INVALID_REARM_RULE",
    "INVALID_SIZING",
)


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("numeric parameters must be JSON numbers")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("numeric parameters must be finite")
    return result


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        value = _finite(value)
        return int(value) if value.is_integer() else float(format(value, ".12g"))
    if isinstance(value, Mapping):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    raise ValueError("program identity accepts JSON primitives only")


def canonical_hash(value: Any) -> str:
    payload = json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, order=True)
class Primitive:
    """One typed AST node with canonical, insertion-order-free parameters."""

    kind: str
    name: str
    parameters: tuple[tuple[str, Any], ...] = ()
    direction: str = "BOTH"

    @classmethod
    def make(cls, kind: str, name: str, parameters: Mapping[str, Any] | None = None,
             direction: str = "BOTH") -> "Primitive":
        normalized = _normalize(parameters or {})
        return cls(str(kind), str(name), tuple((key, normalized[key]) for key in sorted(normalized)), direction)

    def params(self) -> dict[str, Any]:
        return dict(self.parameters)

    def canonical(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "parameters": self.params(), "direction": self.direction}


@dataclass(frozen=True)
class StrategyProgram:
    timeframe: str
    direction: str
    regime: Primitive
    setup: tuple[Primitive, ...]
    trigger: Primitive
    invalidation: Primitive
    exit: Primitive
    sizing: Primitive
    rearm_rule: str = "SETUP_AND_TRIGGER_FALSE"
    higher_timeframe_context: Primitive | None = None
    schema_version: str = STRATEGY_PROGRAM_SCHEMA_VERSION
    grammar_version: str = STRATEGY_GRAMMAR_VERSION

    def normalized(self) -> "StrategyProgram":
        # Setup conjunction is commutative.  Exact duplicates are removed here;
        # validation still reports duplicates on unnormalized proposals.
        setup = tuple(sorted(set(self.setup), key=lambda node: canonical_hash(node.canonical())))
        return replace(self, setup=setup)

    def canonical_ast(self) -> dict[str, Any]:
        value = self.normalized()
        return {
            "schema_version": value.schema_version,
            "grammar_version": value.grammar_version,
            "timeframe": value.timeframe,
            "direction": value.direction,
            "layers": {
                "regime": value.regime.canonical(),
                "setup": [node.canonical() for node in value.setup],
                "trigger": value.trigger.canonical(),
                "invalidation": value.invalidation.canonical(),
                "exit": value.exit.canonical(),
                "sizing": value.sizing.canonical(),
            },
            "rearm_rule": value.rearm_rule,
            "higher_timeframe_context": (
                value.higher_timeframe_context.canonical() if value.higher_timeframe_context else None
            ),
        }

    @property
    def complexity(self) -> int:
        # Six required layers plus a second setup predicate and optional context.
        return 6 + max(0, len(self.normalized().setup) - 1) + int(self.higher_timeframe_context is not None)

    @property
    def semantic_identity(self) -> str:
        return canonical_hash({
            "identity_version": STRATEGY_PROGRAM_IDENTITY_VERSION,
            "canonical_program_ast": self.canonical_ast(),
            "feature_version": STRATEGY_PROGRAM_FEATURE_VERSION,
            "execution_version": STRATEGY_PROGRAM_EXECUTION_VERSION,
        })


def build_program_identity(program: StrategyProgram, *, dataset_fingerprint: str, instrument: str,
                           fold_identity: Mapping[str, Any]) -> str:
    """Build an evaluation identity containing every data/context dimension."""
    normalized = program.normalized()
    return canonical_hash({
        "identity_version": STRATEGY_PROGRAM_IDENTITY_VERSION,
        "schema_version": STRATEGY_PROGRAM_SCHEMA_VERSION,
        "grammar_version": STRATEGY_GRAMMAR_VERSION,
        "search_policy_version": STRATEGY_SEARCH_POLICY_VERSION,
        "canonical_program_ast": normalized.canonical_ast(),
        "normalized_parameters": normalized.canonical_ast()["layers"],
        "feature_version": STRATEGY_PROGRAM_FEATURE_VERSION,
        "execution_version": STRATEGY_PROGRAM_EXECUTION_VERSION,
        "dataset_fingerprint": dataset_fingerprint,
        "instrument": instrument,
        "timeframe": normalized.timeframe,
        "fold_identity": _normalize(fold_identity),
        "higher_timeframe_context_configuration": (
            normalized.higher_timeframe_context.canonical()
            if normalized.higher_timeframe_context else None
        ),
    })


def validate_program(program: StrategyProgram) -> tuple[str, ...]:
    """Return stable rejection codes; structural rejection is not execution."""
    reasons: list[str] = []
    if program.timeframe not in PRIMARY_TIMEFRAMES:
        reasons.append("INVALID_TIMEFRAME")
    if program.direction not in ("LONG", "SHORT"):
        reasons.append("INVALID_DIRECTION")
    nodes = (program.regime,) + program.setup + (program.trigger, program.invalidation, program.exit, program.sizing)
    directional = [node.direction for node in nodes if node.direction != "BOTH"]
    if any(direction != program.direction for direction in directional):
        reasons.append("DIRECTION_MISMATCH")
    context = program.higher_timeframe_context
    if context:
        frame = context.params().get("timeframe")
        if frame not in ALLOWED_CONTEXT.get(program.timeframe, ()):
            reasons.append("INVALID_HIGHER_TIMEFRAME_ALIGNMENT")
    setup_names = [node.name for node in program.setup]
    if len(program.setup) > 2:
        reasons.append("EXCESSIVE_COMPLEXITY")
    if len(set(program.setup)) != len(program.setup):
        reasons.append("DUPLICATE_PREDICATE")
    trend_filters = sum(node.name in {"ema_excursion", "ma_distance"} for node in program.setup)
    oscillators = sum(node.name == "rsi_reset" for node in program.setup)
    if trend_filters > 1:
        reasons.append("REDUNDANT_TREND_FILTER")
    if oscillators > 1:
        reasons.append("REDUNDANT_OSCILLATOR")
    regime_mode = program.regime.params().get("mode")
    setup_modes = {node.params().get("mode") for node in program.setup if node.params().get("mode")}
    trigger_mode = program.trigger.params().get("mode")
    if setup_modes and regime_mode and regime_mode not in setup_modes:
        reasons.append("INCOMPATIBLE_REGIME_SETUP")
    if setup_modes and trigger_mode and trigger_mode not in setup_modes:
        reasons.append("INCOMPATIBLE_SETUP_TRIGGER")
    if program.trigger.name in {"completed_level_breakout", "compression_breakout"} and not any(
        node.name in {"consolidation_level", "range_location", "compression"} for node in program.setup
    ):
        reasons.append("BREAKOUT_WITHOUT_COMPLETED_LEVEL")
    if program.trigger.name in setup_names:
        reasons.append("TRIGGER_EQUALS_SETUP")
    if {"bullish_only", "bearish_only"} <= set(setup_names):
        reasons.append("CONTRADICTORY_PREDICATES")
    if program.exit.name == "fixed_r":
        try:
            target_r = _finite(program.exit.params().get("target_r"))
            minimum_r = _finite(program.exit.params().get("minimum_r", 1.25))
            if target_r < minimum_r:
                reasons.append("REWARD_RISK_BELOW_MINIMUM")
        except (TypeError, ValueError):
            reasons.append("NONFINITE_GEOMETRY")
    else:
        reasons.append("WRONG_SIDE_TARGET")
    try:
        if _finite(program.invalidation.params().get("atr_multiplier", 0)) <= 0:
            reasons.append("NONFINITE_GEOMETRY")
    except ValueError:
        reasons.append("NONFINITE_GEOMETRY")
    sizing = program.sizing.params()
    try:
        if not 0 < _finite(sizing.get("risk_per_trade")) <= .1 or not 0 < _finite(
            sizing.get("maximum_notional_fraction")
        ) <= 1:
            reasons.append("INVALID_SIZING")
    except (TypeError, ValueError):
        reasons.append("INVALID_SIZING")
    if program.rearm_rule != "SETUP_AND_TRIGGER_FALSE":
        reasons.append("INVALID_REARM_RULE")
    if program.complexity > 8:
        reasons.append("EXCESSIVE_COMPLEXITY")
    return tuple(dict.fromkeys(reasons))


def _p(kind: str, name: str, params: Mapping[str, Any], direction: str = "BOTH") -> Primitive:
    return Primitive.make(kind, name, params, direction)


def _program(timeframe: str, direction: str, regime: Primitive, setup: Iterable[Primitive],
             trigger: Primitive, invalidation: Primitive, exit_node: Primitive,
             context: Primitive | None = None) -> StrategyProgram:
    sizing = _p("sizing", "paper_accounting_v2", {
        "risk_per_trade": .01, "maximum_notional_fraction": .25,
        "sizing_formula": "requested_risk_divided_by_actual_stop_cost",
    })
    return StrategyProgram(timeframe, direction, regime, tuple(setup), trigger, invalidation,
                           exit_node, sizing, higher_timeframe_context=context)


def _stable_order(program: StrategyProgram) -> tuple[str, str]:
    salted = canonical_hash({"seed": SEMANTIC_SEED, "ast": program.canonical_ast()})
    return salted, program.semantic_identity


def generate_programs() -> dict[str, Any]:
    """Enumerate the bounded grammar and deterministically enforce raw/semantic budgets."""
    proposals: list[StrategyProgram] = []
    for timeframe in PRIMARY_TIMEFRAMES:
        for direction in ("LONG", "SHORT"):
            opposite = "SHORT" if direction == "LONG" else "LONG"
            trend = _p("regime", "aligned_trend", {"mode": "trend"}, direction)
            context_frame = ALLOWED_CONTEXT[timeframe][0]
            context = _p("context", "aligned_trend_context", {
                "mode": "trend", "timeframe": context_frame, "alignment": "last_fully_closed",
            }, direction)
            for excursion in (.25, .5, .75):
                for rsi in ((40, 45) if direction == "LONG" else (55, 60)):
                    for stop in (1.0, 1.5):
                        for target in (1.5, 2.0):
                            setup = (
                                _p("setup", "ema_excursion", {"mode": "trend", "atr_distance": excursion}, direction),
                                _p("setup", "rsi_reset", {"mode": "trend", "threshold": rsi}, direction),
                            )
                            proposals.append(_program(
                                timeframe, direction, trend, setup,
                                _p("trigger", "reclaim_crossing", {"mode": "trend", "level": "EMA20"}, direction),
                                _p("invalidation", "atr_stop", {"atr_multiplier": stop}, direction),
                                _p("exit", "fixed_r", {"target_r": target, "minimum_r": 1.25}, direction),
                                context if excursion == .5 and target == 2.0 else None,
                            ))
            for window in (10, 20):
                for volume in (.8, 1.0):
                    for stop in (1.0, 1.5):
                        for target in (1.5, 2.0):
                            proposals.append(_program(
                                timeframe, direction, trend,
                                (_p("setup", "consolidation_level", {"mode": "trend", "window": window}, direction),
                                 _p("setup", "volume_contraction", {"mode": "trend", "maximum_ratio": volume})),
                                _p("trigger", "completed_level_breakout", {"mode": "trend", "window": window}, direction),
                                _p("invalidation", "level_atr_buffer", {"atr_multiplier": stop}, direction),
                                _p("exit", "fixed_r", {"target_r": target, "minimum_r": 1.25}, direction),
                            ))
            compression_regime = _p(
                "regime", "volatility_compression",
                {"mode": "compression", "maximum_bandwidth": .03},
            )
            for width in (.02, .03):
                for location in (.65, .75):
                    for stop in (1.0, 1.5):
                        for target in (1.5, 2.0):
                            proposals.append(_program(
                                timeframe, direction, compression_regime,
                                (_p("setup", "compression", {"mode": "compression", "maximum_bandwidth": width}),
                                 _p("setup", "range_location", {"mode": "compression", "minimum_location": location}, direction)),
                                _p("trigger", "compression_breakout", {"mode": "compression", "window": 20}, direction),
                                _p("invalidation", "range_extreme_atr_buffer", {"atr_multiplier": stop}, direction),
                                _p("exit", "fixed_r", {"target_r": target, "minimum_r": 1.25}, direction),
                            ))
            range_regime = _p("regime", "low_volatility_range", {"mode": "range", "maximum_bandwidth": .06})
            for depth in (0.0, .1):
                for rsi in ((30, 35) if direction == "LONG" else (65, 70)):
                    for stop in (1.0, 1.5):
                        for target in (1.5, 2.0):
                            proposals.append(_program(
                                timeframe, direction, range_regime,
                                (_p("setup", "bollinger_excursion", {"mode": "range", "atr_depth": depth}, direction),
                                 _p("setup", "rsi_reset", {"mode": "range", "threshold": rsi}, direction)),
                                _p("trigger", "bollinger_reentry", {"mode": "range"}, direction),
                                _p("invalidation", "range_extreme_atr_buffer", {"atr_multiplier": stop}, direction),
                                _p("exit", "fixed_r", {"target_r": target, "minimum_r": 1.25}, direction),
                            ))
            # Deterministic invalid proposals exercise structural rejection without
            # consuming the raw-valid budget.
            proposals.append(_program(
                timeframe, direction, trend,
                (_p("setup", "rsi_reset", {"mode": "range", "threshold": 50}, direction),),
                _p("trigger", "completed_level_breakout", {"mode": "trend", "window": 20}, opposite),
                _p("invalidation", "atr_stop", {"atr_multiplier": 0}, direction),
                _p("exit", "fixed_r", {"target_r": 1.0, "minimum_r": 1.25}, direction),
            ))

    # Equivalent commutative conjunctions are deliberately present in the raw
    # grammar stream so semantic deduplication is exercised, measured, and
    # bounded in every real run.
    proposals.extend(
        replace(program, setup=tuple(reversed(program.setup)))
        for program in tuple(proposals[:24])
        if len(program.setup) == 2
    )

    rejected: list[dict[str, Any]] = []
    structurally_valid: list[StrategyProgram] = []
    for proposal in proposals:
        codes = validate_program(proposal)
        if codes:
            rejected.append({"ast": proposal.canonical_ast(), "rejection_codes": list(codes)})
        else:
            structurally_valid.append(proposal)
    structurally_valid = sorted(structurally_valid, key=_stable_order)[:SEARCH_BUDGETS["raw_structurally_valid"]]

    representatives: dict[str, StrategyProgram] = {}
    aliases: dict[str, list[str]] = {}
    for program in structurally_valid:
        identity = program.semantic_identity
        aliases.setdefault(identity, []).append(canonical_hash(program.canonical_ast()))
        representatives.setdefault(identity, program.normalized())
    semantic_duplicate_count = len(structurally_valid) - len(representatives)
    semantic = sorted(representatives.values(), key=_stable_order)[:SEARCH_BUDGETS["semantic"]]
    return {
        "policy": {
            "schema_version": STRATEGY_PROGRAM_SCHEMA_VERSION,
            "grammar_version": STRATEGY_GRAMMAR_VERSION,
            "search_policy_version": STRATEGY_SEARCH_POLICY_VERSION,
            "identity_version": STRATEGY_PROGRAM_IDENTITY_VERSION,
            "feature_version": STRATEGY_PROGRAM_FEATURE_VERSION,
            "execution_version": STRATEGY_PROGRAM_EXECUTION_VERSION,
            "seed": SEMANTIC_SEED,
            "budgets": dict(SEARCH_BUDGETS),
            "primitive_registry": PRIMITIVE_REGISTRY,
            "primitive_versions": {
                primitive: "strategy-primitive-v1"
                for group in PRIMITIVE_REGISTRY.values() for primitive in group
            },
            "primary_timeframes": list(PRIMARY_TIMEFRAMES),
            "allowed_higher_timeframe_context": {
                timeframe: list(contexts) for timeframe, contexts in ALLOWED_CONTEXT.items()
            },
            "maximum_semantic_complexity": 8,
            "lifecycle_rearm_rule": "SETUP_AND_TRIGGER_FALSE",
            "historical_input_policy": "PRICE_ONLY_OHLCV",
        },
        "proposal_count": len(proposals),
        "raw_program_count": len(structurally_valid),
        "structural_rejection_count": len(rejected),
        "structural_rejections": rejected,
        "semantic_duplicate_count": semantic_duplicate_count,
        "semantic_aliases": aliases,
        "programs": semantic,
        "selected_program_identities": [program.semantic_identity for program in semantic],
    }


def semantic_deduplicate(programs: Iterable[StrategyProgram]) -> tuple[list[StrategyProgram], dict[str, list[str]]]:
    families: dict[str, list[StrategyProgram]] = {}
    for program in programs:
        families.setdefault(program.semantic_identity, []).append(program)
    selected = [min(items, key=lambda item: (item.complexity, _stable_order(item)))
                for items in families.values()]
    selected.sort(key=_stable_order)
    aliases = {identity: [canonical_hash(item.canonical_ast()) for item in items]
               for identity, items in sorted(families.items())}
    return selected, aliases


def neighborhood_variants(program: StrategyProgram, limit: int = 5) -> list[StrategyProgram]:
    """Change exactly one declared numeric parameter by one bounded step."""
    variants: list[StrategyProgram] = []
    layers = ("regime", "setup", "trigger", "invalidation", "exit")
    for layer in layers:
        nodes = list(getattr(program, layer) if layer == "setup" else (getattr(program, layer),))
        for index, node in enumerate(nodes):
            params = node.params()
            for key in sorted(params):
                value = params[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or key == "minimum_r":
                    continue
                step = {
                    "threshold": 5, "window": 5, "atr_multiplier": .25, "target_r": .25,
                    "atr_distance": .25, "maximum_ratio": .1, "maximum_bandwidth": .005,
                    "minimum_location": .05, "atr_depth": .1,
                }.get(key)
                if step is None:
                    continue
                new_params = {**params, key: _normalize(float(value) + step)}
                replacement = Primitive.make(node.kind, node.name, new_params, node.direction)
                if layer == "setup":
                    changed = list(program.setup); changed[index] = replacement
                    variant = replace(program, setup=tuple(changed))
                else:
                    variant = replace(program, **{layer: replacement})
                if not validate_program(variant):
                    variants.append(variant.normalized())
                if len(variants) >= min(limit, SEARCH_BUDGETS["neighborhood_per_program"]):
                    return variants
    return variants
