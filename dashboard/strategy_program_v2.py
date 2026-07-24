"""Bounded Phase 6B temporal strategy-program grammar.

This module is deliberately additive.  Phase 6A continues to import
``strategy_program`` and therefore retains its v1 serialization and identities.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from .backtest_engine import SHARED_EXECUTION_ENGINE_VERSION
from .strategy_program import Primitive, _normalize, canonical_hash

STRATEGY_PROGRAM_SCHEMA_VERSION = "strategy-program-schema-v2"
STRATEGY_GRAMMAR_VERSION = "strategy-grammar-v2"
STRATEGY_SEARCH_POLICY_VERSION = "automatic-strategy-search-v2"
STRATEGY_PROGRAM_IDENTITY_VERSION = "strategy-program-identity-v2"
TEMPORAL_SEQUENCE_VERSION = "temporal-sequence-v1"
CHECKPOINT_SCHEMA_VERSION = "discovery-checkpoint-v1"
BENCHMARK_DIAGNOSTIC_VERSION = "benchmark-diagnostics-v1"
STRATEGY_PROGRAM_FEATURE_VERSION = "strategy-program-features-v1"
STRATEGY_PROGRAM_EXECUTION_VERSION = SHARED_EXECUTION_ENGINE_VERSION

SEMANTIC_SEED = 20260724
MAX_SEQUENCE_STAGES = 4
MAX_SEMANTIC_COMPLEXITY = 10
PRIMARY_TIMEFRAMES = ("15m", "1H", "4H")
ALLOWED_CONTEXT = {"15m": ("1H", "4H"), "1H": ("4H", "1D"), "4H": ("1D",)}
TEMPORAL_FAMILIES = (
    "COMPRESSION_EXPANSION_RETEST_CONTINUATION",
    "FAILED_BREAKOUT_REVERSAL",
    "MOMENTUM_IMPULSE_RESET_RECLAIM",
    "VOLATILITY_REGIME_SWITCH",
)
SEARCH_BUDGETS = {
    "raw_structurally_valid": 600,
    "semantic": 400,
    "entry_behavior_families": 200,
    "geometry_per_entry_family": 3,
    "event_study": 120,
    "diagnostic_sparse_btc": 12,
    "btc_backtest": 60,
    "neighborhood_programs": 15,
    "neighborhood_per_program": 5,
    "cross_asset": 10,
}


@dataclass(frozen=True)
class TemporalStage:
    predicate: Primitive
    minimum_bars: int
    maximum_bars: int
    transition: Primitive
    invalidation: Primitive
    expiration_reason: str

    def canonical(self) -> dict[str, Any]:
        return {
            "predicate": self.predicate.canonical(),
            "minimum_bars": self.minimum_bars,
            "maximum_bars": self.maximum_bars,
            "transition": self.transition.canonical(),
            "invalidation": self.invalidation.canonical(),
            "expiration_reason": self.expiration_reason,
        }


@dataclass(frozen=True)
class TemporalSequence:
    family: str
    direction: str
    stages: tuple[TemporalStage, ...]
    version: str = TEMPORAL_SEQUENCE_VERSION

    def canonical(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "family": self.family,
            "direction_specialization": self.direction,
            "ordered_stages": [stage.canonical() for stage in self.stages],
        }

    @property
    def complexity(self) -> int:
        # Family and direction are one semantic choice each; every ordered stage
        # contributes two bounded decisions (predicate + lifecycle conditions).
        return 2 + 2 * len(self.stages)


@dataclass(frozen=True)
class EconomicGeometry:
    stop_type: str
    stop_parameter: float
    exit_type: str
    target_parameter: float | str | None
    time_stop_bars: int | None
    minimum_reward_risk: float
    execution_assumptions: tuple[tuple[str, Any], ...]

    @classmethod
    def make(cls, stop_type: str, stop_parameter: float, exit_type: str,
             target_parameter: float | str | None, time_stop_bars: int | None = None,
             minimum_reward_risk: float = 1.25,
             execution_assumptions: Mapping[str, Any] | None = None) -> "EconomicGeometry":
        assumptions = _normalize(execution_assumptions or {
            "fill": "NEXT_OPEN", "fee": .0005, "slippage": .0003,
            "intrabar_tie": "STOP_FIRST",
        })
        return cls(stop_type, float(stop_parameter), exit_type, target_parameter,
                   time_stop_bars, float(minimum_reward_risk),
                   tuple((key, assumptions[key]) for key in sorted(assumptions)))

    def canonical(self) -> dict[str, Any]:
        return {
            "stop_type": self.stop_type,
            "stop_parameter": self.stop_parameter,
            "exit_type": self.exit_type,
            "target_parameter": self.target_parameter,
            "time_stop_bars": self.time_stop_bars,
            "minimum_reward_risk": self.minimum_reward_risk,
            "execution_assumptions": dict(self.execution_assumptions),
        }

    @property
    def identity(self) -> str:
        return canonical_hash({"geometry_version": "economic-geometry-v1", **self.canonical()})


@dataclass(frozen=True)
class TemporalStrategyProgram:
    timeframe: str
    direction: str
    sequence: TemporalSequence
    geometry: EconomicGeometry
    higher_timeframe_context: Primitive | None = None
    schema_version: str = STRATEGY_PROGRAM_SCHEMA_VERSION
    grammar_version: str = STRATEGY_GRAMMAR_VERSION

    def canonical_entry_ast(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "grammar_version": self.grammar_version,
            "timeframe": self.timeframe,
            "direction_specialization": self.direction,
            "temporal_sequence": self.sequence.canonical(),
            "higher_timeframe_context": (
                self.higher_timeframe_context.canonical()
                if self.higher_timeframe_context else None
            ),
        }

    def canonical_ast(self) -> dict[str, Any]:
        return {**self.canonical_entry_ast(), "economic_geometry": self.geometry.canonical()}

    @property
    def entry_identity(self) -> str:
        return canonical_hash({
            "identity_version": STRATEGY_PROGRAM_IDENTITY_VERSION,
            "canonical_entry_ast": self.canonical_entry_ast(),
            "feature_version": STRATEGY_PROGRAM_FEATURE_VERSION,
        })

    @property
    def semantic_identity(self) -> str:
        return canonical_hash({
            "identity_version": STRATEGY_PROGRAM_IDENTITY_VERSION,
            "canonical_program_ast": self.canonical_ast(),
            "feature_version": STRATEGY_PROGRAM_FEATURE_VERSION,
            "execution_version": STRATEGY_PROGRAM_EXECUTION_VERSION,
        })

    @property
    def complexity(self) -> int:
        return self.sequence.complexity


def build_program_identity(program: TemporalStrategyProgram, *, dataset_fingerprint: str,
                           instrument: str, fold_identity: Mapping[str, Any]) -> str:
    return canonical_hash({
        "identity_version": STRATEGY_PROGRAM_IDENTITY_VERSION,
        "search_policy_version": STRATEGY_SEARCH_POLICY_VERSION,
        "canonical_program_ast": program.canonical_ast(),
        "dataset_fingerprint": dataset_fingerprint,
        "instrument": instrument,
        "timeframe": program.timeframe,
        "fold_identity": _normalize(fold_identity),
    })


def validate_geometry(geometry: EconomicGeometry) -> tuple[str, ...]:
    reasons: list[str] = []
    if geometry.stop_type not in {"ATR", "FAILED_BREAKOUT_EXTREME_ATR", "LEVEL_ATR"}:
        reasons.append("INVALID_STOP_TYPE")
    if not 0 < geometry.stop_parameter <= 3:
        reasons.append("NONFINITE_GEOMETRY")
    if geometry.exit_type not in {
        "FIXED_R", "OPPOSITE_COMPLETED_RANGE_BOUNDARY", "BOLLINGER_MIDLINE", "TIME_STOP"
    }:
        reasons.append("INVALID_EXIT_TYPE")
    if geometry.exit_type == "FIXED_R":
        if not isinstance(geometry.target_parameter, (int, float)):
            reasons.append("NONFINITE_GEOMETRY")
        elif float(geometry.target_parameter) < geometry.minimum_reward_risk:
            reasons.append("REWARD_RISK_BELOW_MINIMUM")
    if geometry.time_stop_bars is not None and geometry.time_stop_bars not in {8, 16, 32}:
        reasons.append("INVALID_TIME_STOP")
    return tuple(dict.fromkeys(reasons))


def validate_program(program: TemporalStrategyProgram) -> tuple[str, ...]:
    reasons = list(validate_geometry(program.geometry))
    if program.timeframe not in PRIMARY_TIMEFRAMES:
        reasons.append("INVALID_TIMEFRAME")
    if program.direction not in {"LONG", "SHORT"}:
        reasons.append("INVALID_DIRECTION")
    if program.sequence.direction != program.direction:
        reasons.append("DIRECTION_MISMATCH")
    if program.sequence.family not in TEMPORAL_FAMILIES:
        reasons.append("INVALID_FAMILY")
    if not 2 <= len(program.sequence.stages) <= MAX_SEQUENCE_STAGES:
        reasons.append("INVALID_STAGE_COUNT")
    if program.complexity > MAX_SEMANTIC_COMPLEXITY:
        reasons.append("EXCESSIVE_COMPLEXITY")
    for stage in program.sequence.stages:
        if not 0 <= stage.minimum_bars <= stage.maximum_bars <= 32:
            reasons.append("INVALID_STAGE_DURATION")
        if not stage.expiration_reason:
            reasons.append("MISSING_EXPIRATION_REASON")
    context = program.higher_timeframe_context
    if context and context.params().get("timeframe") not in ALLOWED_CONTEXT.get(program.timeframe, ()):
        reasons.append("INVALID_HIGHER_TIMEFRAME_ALIGNMENT")
    return tuple(dict.fromkeys(reasons))


def _p(kind: str, name: str, params: Mapping[str, Any] | None, direction: str) -> Primitive:
    return Primitive.make(kind, name, params or {}, direction)


def _stage(direction: str, predicate: str, transition: str, invalidation: str,
           maximum: int, minimum: int = 0, **params: Any) -> TemporalStage:
    return TemporalStage(
        _p("stage_predicate", predicate, params, direction),
        minimum, maximum,
        _p("transition", transition, params, direction),
        _p("invalidation", invalidation, params, direction),
        f"{predicate.upper()}_WINDOW_EXPIRED",
    )


def _sequence(family: str, direction: str, variant: int) -> TemporalSequence:
    if family == TEMPORAL_FAMILIES[0]:
        stages = (
            _stage(direction, "volatility_compression", "expansion_breakout", "compression_lost", 16,
                   maximum_bandwidth=(.035, .045)[variant]),
            _stage(direction, "expansion_breakout", "causal_level_retest", "close_through_level", 8,
                   atr_expansion=(1.1, 1.25)[variant]),
            _stage(direction, "causal_level_retest", "continuation_reclaim", "close_through_level", 6,
                   retest_atr=(.25, .4)[variant]),
            _stage(direction, "continuation_reclaim", "trigger", "close_through_level", 3),
        )
    elif family == TEMPORAL_FAMILIES[1]:
        stages = (
            _stage(direction, "completed_rolling_level", "breakout_attempt", "level_missing", 20,
                   window=(20, 30)[variant]),
            _stage(direction, "breakout_attempt", "close_back_inside", "runaway_breakout", 4,
                   attempt_atr=(.1, .2)[variant]),
            _stage(direction, "close_back_inside", "reversal_confirmation", "reattempt_breakout", 4,
                   rejection_wick=(.25, .4)[variant]),
            _stage(direction, "reversal_confirmation", "trigger", "reattempt_breakout", 2),
        )
    elif family == TEMPORAL_FAMILIES[2]:
        stages = (
            _stage(direction, "momentum_impulse", "bounded_reset", "structure_break", 8,
                   return_atr=(.8, 1.1)[variant]),
            _stage(direction, "bounded_reset", "structure_preserved", "structure_break", 8,
                   rsi_reset=(45, 50)[variant] if direction == "LONG" else (55, 50)[variant]),
            _stage(direction, "structure_preserved", "continuation_reclaim", "structure_break", 4),
            _stage(direction, "continuation_reclaim", "trigger", "structure_break", 2),
        )
    else:
        stages = (
            _stage(direction, "range_or_compression_regime", "confirmed_expansion", "regime_lost", 20,
                   maximum_bandwidth=(.04, .05)[variant]),
            _stage(direction, "confirmed_expansion", "directional_structure", "isolated_noise", 5,
                   confirmation_bars=2),
            _stage(direction, "directional_structure", "continuation_trigger", "structure_break", 8),
            _stage(direction, "continuation_trigger", "trigger", "structure_break", 2),
        )
    return TemporalSequence(family, direction, stages)


def _geometries(family: str) -> tuple[EconomicGeometry, ...]:
    stop_type = "FAILED_BREAKOUT_EXTREME_ATR" if family == TEMPORAL_FAMILIES[1] else "ATR"
    return (
        EconomicGeometry.make(stop_type, 1.0, "FIXED_R", 1.5),
        EconomicGeometry.make(stop_type, 1.0, "FIXED_R", 2.0),
        EconomicGeometry.make("LEVEL_ATR", .5, "OPPOSITE_COMPLETED_RANGE_BOUNDARY",
                              "completed_opposite_level", time_stop_bars=16),
    )


def generate_programs(max_programs: int | None = None) -> dict[str, Any]:
    """Generate exactly the four declared families with explicit directional variants."""
    proposals: list[TemporalStrategyProgram] = []
    for family in TEMPORAL_FAMILIES:
        for timeframe in PRIMARY_TIMEFRAMES:
            for direction in ("LONG", "SHORT"):
                context = _p("context", "aligned_trend_context", {
                    "timeframe": ALLOWED_CONTEXT[timeframe][0],
                    "alignment": "last_fully_closed",
                }, direction)
                for variant in range(2):
                    sequence = _sequence(family, direction, variant)
                    for geometry in _geometries(family):
                        proposals.append(TemporalStrategyProgram(
                            timeframe, direction, sequence, geometry,
                            context if variant else None,
                        ))
    # Deterministic structural failures are reported and never consume capacity.
    invalid = replace(proposals[0], sequence=replace(proposals[0].sequence, stages=()))
    proposals.append(invalid)
    rejected, valid = [], []
    for proposal in proposals:
        reasons = validate_program(proposal)
        if reasons:
            rejected.append({"ast": proposal.canonical_ast(), "rejection_codes": list(reasons)})
        else:
            valid.append(proposal)
    valid.sort(key=lambda item: canonical_hash({"seed": SEMANTIC_SEED, "ast": item.canonical_ast()}))
    valid = valid[:min(max_programs or SEARCH_BUDGETS["raw_structurally_valid"],
                       SEARCH_BUDGETS["raw_structurally_valid"])]
    semantic: dict[str, TemporalStrategyProgram] = {}
    aliases: dict[str, list[str]] = {}
    for item in valid:
        aliases.setdefault(item.semantic_identity, []).append(canonical_hash(item.canonical_ast()))
        semantic.setdefault(item.semantic_identity, item)
    programs = list(semantic.values())[:SEARCH_BUDGETS["semantic"]]
    return {
        "policy": {
            "schema_version": STRATEGY_PROGRAM_SCHEMA_VERSION,
            "grammar_version": STRATEGY_GRAMMAR_VERSION,
            "search_policy_version": STRATEGY_SEARCH_POLICY_VERSION,
            "identity_version": STRATEGY_PROGRAM_IDENTITY_VERSION,
            "temporal_sequence_version": TEMPORAL_SEQUENCE_VERSION,
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "benchmark_diagnostic_version": BENCHMARK_DIAGNOSTIC_VERSION,
            "seed": SEMANTIC_SEED, "budgets": dict(SEARCH_BUDGETS),
            "families": list(TEMPORAL_FAMILIES),
        },
        "proposal_count": len(proposals),
        "raw_program_count": len(valid),
        "structural_rejection_count": len(rejected),
        "structural_rejections": rejected,
        "semantic_duplicate_count": len(valid) - len(semantic),
        "semantic_aliases": aliases,
        "programs": programs,
        "long_only_count": sum(p.direction == "LONG" for p in programs),
        "short_only_count": sum(p.direction == "SHORT" for p in programs),
    }


def neighborhood_variants(program: TemporalStrategyProgram, limit: int = 5
                          ) -> list[TemporalStrategyProgram]:
    """Move one declared numeric stage parameter by one bounded grammar step."""
    output: list[TemporalStrategyProgram] = []
    for index, stage in enumerate(program.sequence.stages):
        params = stage.predicate.params()
        for key, value in sorted(params.items()):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            step = .05 if isinstance(value, float) else 1
            replacement = replace(stage, predicate=Primitive.make(
                stage.predicate.kind, stage.predicate.name,
                {**params, key: value + step}, stage.predicate.direction,
            ))
            stages = list(program.sequence.stages); stages[index] = replacement
            candidate = replace(program, sequence=replace(program.sequence, stages=tuple(stages)))
            if not validate_program(candidate):
                output.append(candidate)
            if len(output) >= min(limit, SEARCH_BUDGETS["neighborhood_per_program"]):
                return output
    return output


def geometry_aware_deduplicate(items: Iterable[dict[str, Any]]
                               ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Group entry vectors, then retain at most three economic geometries."""
    entry_families: dict[tuple[str, str, str, tuple[int, ...]], list[dict[str, Any]]] = {}
    for item in items:
        program = item["program"]
        key = (item["instrument"], program.timeframe, program.direction,
               tuple(item["trigger_vector"]))
        entry_families.setdefault(key, []).append(item)
    selected, evidence = [], {}
    for key, family in sorted(entry_families.items(), key=lambda pair: canonical_hash(pair[0])):
        geometries: dict[str, list[dict[str, Any]]] = {}
        for item in family:
            geometries.setdefault(item["program"].geometry.identity, []).append(item)
        representatives = [
            min(group, key=lambda value: (value["program"].complexity,
                                          value["program"].semantic_identity))
            for _, group in sorted(geometries.items())
        ][:SEARCH_BUDGETS["geometry_per_entry_family"]]
        selected.extend(representatives)
        family_id = canonical_hash(key)
        evidence[family_id] = {
            "semantic_aliases": sorted(item["program"].semantic_identity for item in family),
            "direction_context_evidence": sorted({
                (item["program"].direction,
                 item["program"].higher_timeframe_context.params().get("timeframe")
                 if item["program"].higher_timeframe_context else None)
                for item in family
            }, key=str),
            "geometry_identities": [item["program"].geometry.identity for item in representatives],
        }
    selected.sort(key=lambda item: item["program"].semantic_identity)
    return selected[:SEARCH_BUDGETS["entry_behavior_families"]
                    * SEARCH_BUDGETS["geometry_per_entry_family"]], evidence
