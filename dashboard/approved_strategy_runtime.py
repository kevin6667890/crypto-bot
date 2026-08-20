"""Frozen evaluator shared by registry adapters, router adapters and paper."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from .discovery_features import build_features
from .factor_registry import value
from .factor_strategy_program import Condition, FactorStrategyProgram, validate

def deserialize_program(ast: Mapping[str, Any]) -> FactorStrategyProgram:
    condition = lambda x: Condition(str(x["factor"]), str(x["operator"]), x["threshold"], int(x.get("bars", 1)))
    program = FactorStrategyProgram(str(ast["direction"]), tuple(condition(x) for x in ast["environment"]), tuple(condition(x) for x in ast["setup"]), tuple(condition(x) for x in ast["trigger"]), str(ast.get("timeframe", "15m")), str(ast.get("schema_version")), str(ast.get("grammar_version")))
    if validate(program): raise ValueError("frozen program is not runtime executable")
    return program

class FrozenProgramEvaluator:
    def __init__(self, program: FactorStrategyProgram, *, registry_id: int | None = None, configuration_hash: str | None = None):
        if validate(program): raise ValueError("program is not runtime executable")
        self.program, self.registry_id = program, registry_id
        self.configuration_hash = configuration_hash or program.identity
        self._features: list[dict[str, Any]] = []
    def _condition(self, c: Condition, candles: list[Mapping[str, Any]], index: int) -> bool:
        if index < c.bars: return False
        def v(i: int, factor: str): return value(factor, candles[i], self._features[i])
        now, prior = v(index, c.factor), v(index - 1, c.factor)
        rhs = v(index, str(c.threshold)) if isinstance(c.threshold, str) else float(c.threshold)
        previous_rhs = v(index - 1, str(c.threshold)) if isinstance(c.threshold, str) else rhs
        if now is None or rhs is None: return False
        if c.operator == ">": base = now > rhs
        elif c.operator == "<": base = now < rhs
        elif c.operator == "cross_above": base = prior is not None and previous_rhs is not None and prior <= previous_rhs and now > rhs
        elif c.operator == "cross_below": base = prior is not None and previous_rhs is not None and prior >= previous_rhs and now < rhs
        elif c.operator == "rising": base = prior is not None and now > prior
        else: base = prior is not None and now < prior
        return base and all(self._condition(Condition(c.factor, c.operator, c.threshold), candles, j) for j in range(index - c.bars + 1, index))
    def evaluate(self, candles: list[Mapping[str, Any]], index: int) -> dict[str, Any]:
        if not self._features: self._features = build_features([dict(x) for x in candles])
        stages = (self.program.environment, self.program.setup, self.program.trigger)
        triggered = all(all(self._condition(c, candles, index) for c in stage) for stage in stages)
        feature = self._features[index]; atr = feature.get("atr")
        action = self.program.direction if triggered and atr else "WAIT"
        ts = int(candles[index]["ts"])
        return {"action": action, "atr": atr, "stop_distance": float(atr) if atr else None, "target_r": 1.5,
                "warmed": bool(feature.get("warm")), "score": max(0.0, 100 - self.program.complexity * 5), "signal_ts": ts,
                "signal_id": f"program:{self.program.identity[:16]}:{ts}", "strategy_version": self.program.schema_version,
                "config_hash": self.configuration_hash, "registry_id": self.registry_id,
                "candidate_identity": self.program.identity, "program_version": self.program.grammar_version}
