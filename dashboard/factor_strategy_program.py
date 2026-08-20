"""Small deterministic Environment -> Setup -> Trigger program grammar."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib, json, math, random
from typing import Any, Iterable, Mapping

from .factor_registry import FACTOR_REGISTRY_VERSION, FACTORS, metadata

PROGRAM_SCHEMA_VERSION = "factor-strategy-program-v1"
PROGRAM_GRAMMAR_VERSION = "factor-strategy-grammar-v1"
MAX_AST_DEPTH, MAX_CONDITIONS, MAX_LOOKBACK, MAX_TEMPORAL_STAGES = 4, 6, 200, 3

def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

@dataclass(frozen=True)
class Condition:
    factor: str
    operator: str
    threshold: float | str
    bars: int = 1
    def canonical(self) -> dict[str, Any]:
        return {"factor": self.factor, "operator": self.operator, "threshold": self.threshold, "bars": self.bars}

@dataclass(frozen=True)
class FactorStrategyProgram:
    direction: str
    environment: tuple[Condition, ...]
    setup: tuple[Condition, ...]
    trigger: tuple[Condition, ...]
    timeframe: str = "15m"
    schema_version: str = PROGRAM_SCHEMA_VERSION
    grammar_version: str = PROGRAM_GRAMMAR_VERSION
    def canonical_ast(self) -> dict[str, Any]:
        layer = lambda values: [v.canonical() for v in sorted(values, key=lambda x: canonical_hash(x.canonical()))]
        return {"schema_version": self.schema_version, "grammar_version": self.grammar_version,
                "timeframe": self.timeframe, "direction": self.direction,
                "environment": layer(self.environment), "setup": layer(self.setup), "trigger": layer(self.trigger)}
    @property
    def identity(self) -> str: return canonical_hash({"program": self.canonical_ast(), "factor_registry": FACTOR_REGISTRY_VERSION})
    @property
    def complexity(self) -> int:
        return len(self.environment) + len(self.setup) + len(self.trigger) + sum(max(0, c.bars - 1) for c in self.environment + self.setup + self.trigger)
    @property
    def factor_versions(self) -> dict[str, str]:
        return {c.factor: FACTORS[c.factor].version for c in self.environment + self.setup + self.trigger}
    def description(self) -> str:
        render = lambda c: f"{c.factor} {c.operator} {c.threshold}" + (f" for {c.bars} bars" if c.bars > 1 else "")
        return " AND ".join(("environment: " + ", ".join(map(render, self.environment)), "setup: " + ", ".join(map(render, self.setup)), "trigger: " + ", ".join(map(render, self.trigger))))

def validate(program: FactorStrategyProgram) -> tuple[str, ...]:
    errors = []
    conditions = program.environment + program.setup + program.trigger
    if program.direction not in {"LONG", "SHORT"}: errors.append("INVALID_DIRECTION")
    if program.timeframe not in {"15m", "1H", "4H"}: errors.append("INVALID_TIMEFRAME")
    if len(conditions) > MAX_CONDITIONS or program.complexity > MAX_CONDITIONS + MAX_TEMPORAL_STAGES: errors.append("EXCESSIVE_COMPLEXITY")
    if not program.environment or not program.setup or not program.trigger: errors.append("MISSING_TEMPORAL_STAGE")
    seen = set()
    for item in conditions:
        if item.factor not in FACTORS or FACTORS[item.factor].availability != "AVAILABLE": errors.append("UNAVAILABLE_FACTOR")
        if item.operator not in {">", "<", "cross_above", "cross_below", "rising", "falling"}: errors.append("INVALID_OPERATOR")
        if item.bars < 1 or item.bars > MAX_LOOKBACK: errors.append("INVALID_LOOKBACK")
        key = canonical_hash(item.canonical())
        if key in seen: errors.append("DUPLICATE_CONDITION")
        seen.add(key)
    return tuple(dict.fromkeys(errors))

def generate(seed: int = 20260820, budget: int = 200) -> list[FactorStrategyProgram]:
    """Bounded seeded combinations, rather than template parameter sampling."""
    if not 1 <= budget <= 200: raise ValueError("program search budget must be 1..200")
    # Cartesian composition is intentionally over-complete; a seed only chooses
    # a stable bounded ordering.  No named strategy family or Python template is
    # present in the search space.
    rng = random.Random(seed)
    environments = (("ma_slope", 0.0), ("ma_slope", .01), ("atr_pct", .02), ("atr_pct", .03), ("volume_ratio", .8), ("volume_ratio", 1.0))
    setups = (("rsi_14", 45.0, 55.0), ("ma_distance", -.25, .25), ("body_range", .15, .15))
    triggers = (("close", "ema_20"), ("close", "sma_20"), ("close", "rolling_high"), ("close", "rolling_low"))
    proposals = []
    for direction in ("LONG", "SHORT"):
        for env_factor, env_threshold in environments:
            for setup_factor, long_threshold, short_threshold in setups:
                for trigger_factor, reference in triggers:
                    env_op = ">" if direction == "LONG" else "<"
                    # ATR is a regime constraint rather than a directional bias.
                    if env_factor == "atr_pct": env_op = "<"
                    setup_op = "<" if direction == "LONG" else ">"
                    trigger_op = "cross_above" if direction == "LONG" else "cross_below"
                    threshold = long_threshold if direction == "LONG" else short_threshold
                    for bars in (1, 2, 3):
                        proposals.append(FactorStrategyProgram(direction,
                            (Condition(env_factor, env_op, env_threshold),),
                            (Condition(setup_factor, setup_op, threshold, bars),),
                            (Condition(trigger_factor, trigger_op, reference),)))
    rng.shuffle(proposals)
    output: dict[str, FactorStrategyProgram] = {}
    for program in proposals:
        if not validate(program): output[program.identity] = program
    return list(output.values())[:budget]

def registry_snapshot() -> dict[str, Any]: return metadata()
