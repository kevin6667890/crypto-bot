"""Shared deterministic runtime for an approved frozen Discovery candidate."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .discovery_features import build_features
from .strategy_v2_1 import StrategyV21Evaluator


RUNTIME_VERSION = "approved-strategy-runtime-v2.1-v1"
FEATURE_CONFIG = {
    "ma_periods": [20, 60, 200],
    "atr_period": 14,
    "bb_period": 20,
    "rsi_period": 14,
    "volume_period": 20,
}


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def canonical_instrument(instrument: str) -> str:
    return instrument[:-5] if instrument.endswith("-SWAP") else instrument


def evaluate_frozen_candidate(
    registry: Mapping[str, Any], instrument: str, candles: list[dict[str, Any]], *, as_of: int | None = None,
) -> dict[str, Any]:
    """Evaluate exactly the serialized candidate used by research and Paper.

    The function is state-free between calls: state is deterministically replayed
    over confirmed candles, so Router and Paper cannot drift through adapters.
    """
    definition = dict(registry["serialized_definition"])
<<<<<<< HEAD
    requested = canonical_instrument(instrument)
    # Program ASTs use this same public canonical entrypoint.  Router and Paper
    # therefore cannot select a different evaluator for an ACTIVE program.
    if definition.get("program_ast"):
        program = deserialize_program(definition["program_ast"])
        evaluator = FrozenProgramEvaluator(program, registry_id=registry.get("registry_id"), configuration_hash=registry.get("configuration_hash"))
        visible = [row for row in candles if as_of is None or int(row.get("ts", row.get("candle_close_ts", 0))) <= int(as_of)]
        if not visible: raise ValueError("no confirmed candles for frozen program")
        result = evaluator.evaluate(visible, len(visible) - 1)
        return {"runtime_version": RUNTIME_VERSION, "action": result["action"], "warmed": result["warmed"], "state": "TRIGGERED" if result["action"] != "WAIT" else "WATCH", "evidence": {"program_ast": definition["program_ast"], "factor_versions": definition.get("factor_versions", {}), "program_version": definition.get("program_version")}, "stop_price": None, "target_price": None, "candle_close_ts": int(visible[-1].get("candle_close_ts", visible[-1]["ts"])), "last_close": float(visible[-1]["close"]), "strategy_registry_id": registry["registry_id"], "candidate_identity": registry["candidate_identity"], "strategy_version": registry["strategy_version"], "configuration_hash": registry["configuration_hash"], "parameters": definition["parameters"], "instrument": requested, "timeframe": definition.get("timeframe", "15m")}
=======
>>>>>>> feat/auto-research-closed-loop
    parameters = dict(registry["parameters"])
    if definition.get("parameters") != parameters:
        raise ValueError("approved strategy parameter snapshots disagree")
    if canonical_hash(definition) != registry.get("configuration_hash"):
        raise ValueError("approved strategy configuration hash mismatch")
<<<<<<< HEAD
=======
    requested = canonical_instrument(instrument)
>>>>>>> feat/auto-research-closed-loop
    scope = {canonical_instrument(str(value)) for value in registry.get("instrument_scope", [])}
    definition_scope = {
        canonical_instrument(str(value))
        for value in (definition.get("activation_scope") or {}).get("instruments", [])
    }
    if (definition.get("activation_scope") or {}).get("mode") != "GLOBAL_CROSS_ASSET" or definition_scope != scope:
        raise ValueError("approved strategy activation scope snapshots disagree")
    if requested not in scope:
        raise ValueError("instrument is outside the ACTIVE global strategy scope")
    timeframe = str(registry.get("timeframe") or "15m")
    if timeframe != "15m":
        raise ValueError("approved strategy runtime currently requires 15m")
    visible = [
        dict(row) for row in candles
        if bool(row.get("confirmed", True))
        and (as_of is None or int(row.get("candle_close_ts", int(row["ts"]) + 900)) <= int(as_of))
    ]
    visible.sort(key=lambda row: int(row["ts"]))
    if not visible:
        raise ValueError("confirmed candles unavailable for approved strategy")
    for row in visible:
        row.setdefault("candle_close_ts", int(row["ts"]) + 900)
    features = build_features(visible, FEATURE_CONFIG)
    evaluator = StrategyV21Evaluator(
        str(definition["template"]), parameters, requested, timeframe,
        registry.get("source_dataset_fingerprint"),
        {"runtime": RUNTIME_VERSION, "configuration_hash": registry["configuration_hash"]},
    )
    result: dict[str, Any] = {"action": "WAIT", "warmed": False, "evidence": {}}
    for index in range(len(visible)):
        result = evaluator.evaluate(visible, features, index)
    action = str(result.get("action") or "WAIT")
    evidence = dict(result.get("evidence") or {})
    return {
        "runtime_version": RUNTIME_VERSION,
        "action": action if action in {"LONG", "SHORT"} else "WAIT",
        "warmed": bool(result.get("warmed")),
        "state": str(evidence.get("resulting_state") or "WATCH"),
        "evidence": evidence,
        "stop_price": evidence.get("stop_price"),
        "target_price": evidence.get("target_price"),
        "candle_close_ts": int(visible[-1]["candle_close_ts"]),
        "last_close": float(visible[-1]["close"]),
        "strategy_registry_id": registry["registry_id"],
        "candidate_identity": registry["candidate_identity"],
        "strategy_version": registry["strategy_version"],
        "configuration_hash": registry["configuration_hash"],
        "parameters": parameters,
        "instrument": requested,
        "timeframe": timeframe,
    }
<<<<<<< HEAD


# Factor-program adapter.  It is intentionally side-by-side with the existing
# V2.1 adapter so approved legacy candidates retain byte-for-byte behaviour.
def deserialize_program(ast: Mapping[str, Any]):
    from .factor_strategy_program import Condition, FactorStrategyProgram, validate
    make = lambda x: Condition(str(x["factor"]), str(x["operator"]), x["threshold"], int(x.get("bars", 1)))
    program = FactorStrategyProgram(str(ast["direction"]), tuple(make(x) for x in ast["environment"]), tuple(make(x) for x in ast["setup"]), tuple(make(x) for x in ast["trigger"]), str(ast.get("timeframe", "15m")), str(ast.get("schema_version")), str(ast.get("grammar_version")))
    if validate(program): raise ValueError("frozen program is not runtime executable")
    return program

class FrozenProgramEvaluator:
    def __init__(self, program, *, registry_id=None, configuration_hash=None):
        from .factor_strategy_program import validate
        if validate(program): raise ValueError("program is not runtime executable")
        self.program,self.registry_id,self.configuration_hash=program,registry_id,configuration_hash or program.identity; self.features=[]
    def evaluate(self,candles,index):
        from .discovery_features import build_features
        from .factor_registry import value
        if not self.features:self.features=build_features([dict(x) for x in candles])
        def check(c,i):
            now=value(c.factor,candles[i],self.features[i]); prior=value(c.factor,candles[i-1],self.features[i]) if i else None; rhs=value(str(c.threshold),candles[i],self.features[i]) if isinstance(c.threshold,str) else float(c.threshold); old=value(str(c.threshold),candles[i-1],self.features[i-1]) if i and isinstance(c.threshold,str) else rhs
            if now is None or rhs is None:return False
            basic={">":now>rhs,"<":now<rhs,"cross_above":prior is not None and old is not None and prior<=old and now>rhs,"cross_below":prior is not None and old is not None and prior>=old and now<rhs,"rising":prior is not None and now>prior,"falling":prior is not None and now<prior}.get(c.operator,False)
            return basic and all(check(type(c)(c.factor,c.operator,c.threshold),j) for j in range(max(1,i-c.bars+1),i))
        feature=self.features[index]; triggered=bool(feature.get("warm")) and all(check(c,index) for stage in (self.program.environment,self.program.setup,self.program.trigger) for c in stage); atr=feature.get("atr"); ts=int(candles[index]["ts"]); action=self.program.direction if triggered and atr else "WAIT"
        return {"action":action,"atr":atr,"stop_distance":float(atr) if atr else None,"target_r":1.5,"warmed":bool(feature.get("warm")),"score":100-self.program.complexity*5,"signal_ts":ts,"signal_id":f"program:{self.program.identity[:16]}:{ts}","strategy_version":self.program.schema_version,"config_hash":self.configuration_hash,"registry_id":self.registry_id,"candidate_identity":self.program.identity,"program_version":self.program.grammar_version}
=======
>>>>>>> feat/auto-research-closed-loop
