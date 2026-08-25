"""Shared deterministic runtime for an approved frozen Discovery candidate."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .discovery_features import build_features
from .discovery_execution import DiscoveryExecutionConfig
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


def _decision_timestamp(value: str | None, fallback_as_of: int) -> str:
    if value is None:
        return datetime.fromtimestamp(int(fallback_as_of), timezone.utc).isoformat()
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("approved strategy decision timestamp must be timezone-aware")
    return parsed.isoformat()


def _validate_frozen_registry(registry: Mapping[str, Any], requested: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    definition = dict(registry.get("serialized_definition") or {})
    parameters = dict(registry.get("parameters") or {})
    if definition.get("parameters") != parameters:
        raise ValueError("approved strategy parameter snapshots disagree")
    if canonical_hash(definition) != registry.get("configuration_hash"):
        raise ValueError("approved strategy configuration hash mismatch")
    if definition.get("runtime_adapter_version") != RUNTIME_VERSION:
        raise ValueError("approved strategy runtime adapter version mismatch")
    validation = definition.get("validation_status") or {}
    if any(validation.get(key) != "PASS" for key in (
        "development", "walk_forward", "holdout", "oot", "cross_asset", "robustness"
    )):
        raise ValueError("approved strategy frozen validation contract is incomplete")
    scope = {canonical_instrument(str(value)) for value in registry.get("instrument_scope", [])}
    activation = definition.get("activation_scope") or {}
    definition_scope = {canonical_instrument(str(value)) for value in activation.get("instruments", [])}
    if activation.get("mode") != "GLOBAL_CROSS_ASSET" or definition_scope != scope:
        raise ValueError("approved strategy activation scope snapshots disagree")
    if requested not in scope:
        raise ValueError("instrument is outside the ACTIVE global strategy scope")
    timeframe = str(registry.get("timeframe") or "")
    if timeframe != "15m" or str(definition.get("timeframe") or "") != timeframe:
        raise ValueError("approved strategy timeframe snapshots disagree or are unsupported")
    assumptions = definition.get("execution_assumptions")
    if not isinstance(assumptions, Mapping):
        raise ValueError("approved strategy execution assumptions are missing")
    try:
        DiscoveryExecutionConfig(**dict(assumptions)).validate()
    except (TypeError, ValueError) as error:
        raise ValueError("approved strategy execution assumptions are invalid") from error
    return definition, parameters, timeframe


def _visible_candles(candles: list[dict[str, Any]], timeframe: str, as_of: int | None) -> list[dict[str, Any]]:
    seconds = 900 if timeframe == "15m" else 0
    visible = []
    for source in candles:
        row = dict(source)
        close_ts = int(row.get("candle_close_ts", int(row["ts"]) + seconds))
        if bool(row.get("confirmed", True)) and (as_of is None or close_ts <= int(as_of)):
            row["candle_close_ts"] = close_ts
            visible.append(row)
    visible.sort(key=lambda row: int(row["ts"]))
    return visible


def _execution_envelope(
    registry: Mapping[str, Any], *, requested: str, timeframe: str,
    visible: list[dict[str, Any]], features: Any, decision_timestamp: str,
    snapshot_identity: str,
) -> dict[str, Any]:
    registry_identity = registry.get("registry_snapshot_identity")
    if not isinstance(registry_identity, str) or not registry_identity:
        registry_identity = canonical_hash({
            key: registry.get(key) for key in (
                "registry_id", "candidate_identity", "configuration_hash", "strategy_version",
                "engine_version", "policy_version", "source_dataset_fingerprint", "timeframe",
                "instrument_scope", "serialized_definition", "parameters", "active_at",
            )
        })
    candle_payload = [
        {key: row.get(key) for key in ("ts", "candle_close_ts", "open", "high", "low", "close", "volume")}
        for row in visible
    ]
    return {
        "registry_id": registry["registry_id"],
        "candidate_identity": registry["candidate_identity"],
        "configuration_hash": registry["configuration_hash"],
        "runtime_version": RUNTIME_VERSION,
        "registry_snapshot_identity": registry_identity,
        "snapshot_identity": snapshot_identity,
        "decision_timestamp": decision_timestamp,
        "input_fingerprint": canonical_hash({
            "instrument": requested, "timeframe": timeframe, "candles": candle_payload,
        }),
        "factor_fingerprint": canonical_hash({
            "feature_config": FEATURE_CONFIG,
            "factor_versions": (registry.get("serialized_definition") or {}).get("factor_versions", {}),
            "features": features,
        }),
        "instrument": requested,
        "timeframe": timeframe,
        "candle_close_ts": int(visible[-1]["candle_close_ts"]),
    }


def evaluate_frozen_candidate(
    registry: Mapping[str, Any], instrument: str, candles: list[dict[str, Any]], *,
    as_of: int | None = None, decision_timestamp: str | None = None,
    snapshot_identity: str | None = None,
) -> dict[str, Any]:
    """Evaluate exactly the serialized candidate used by research and Paper.

    The function is state-free between calls: state is deterministically replayed
    over confirmed candles, so Router and Paper cannot drift through adapters.
    """
    requested = canonical_instrument(instrument)
    definition, parameters, timeframe = _validate_frozen_registry(registry, requested)
    visible = _visible_candles(candles, timeframe, as_of)
    if not visible:
        raise ValueError("confirmed candles unavailable for approved strategy")
    stamp = _decision_timestamp(
        decision_timestamp,
        int(as_of) if as_of is not None else int(visible[-1]["candle_close_ts"]),
    )
    if not isinstance(snapshot_identity, str) or not snapshot_identity.strip():
        raise ValueError("canonical market snapshot identity is required")
    # Program ASTs use this same public canonical entrypoint.  Router and Paper
    # therefore cannot select a different evaluator for an ACTIVE program.
    if definition.get("program_ast"):
        program = deserialize_program(definition["program_ast"])
        if program.timeframe != timeframe:
            raise ValueError("frozen program timeframe does not match the registry")
        if definition.get("program_version") != program.grammar_version:
            raise ValueError("frozen program version does not match its AST")
        if definition.get("factor_versions") != program.factor_versions:
            raise ValueError("frozen program factor versions do not match its AST")
        evaluator = FrozenProgramEvaluator(program, registry_id=registry.get("registry_id"), configuration_hash=registry.get("configuration_hash"))
        result = evaluator.evaluate(visible, len(visible) - 1)
        features = evaluator.features
        envelope = _execution_envelope(
            registry, requested=requested, timeframe=timeframe, visible=visible,
            features=features, decision_timestamp=stamp, snapshot_identity=snapshot_identity,
        )
        return {"runtime_version": RUNTIME_VERSION, "action": result["action"] if result["action"] in {"LONG", "SHORT"} else "WAIT", "warmed": result["warmed"], "state": "TRIGGERED" if result["action"] != "WAIT" else "WATCH", "evidence": {"program_ast": definition["program_ast"], "factor_versions": definition.get("factor_versions", {}), "program_version": definition.get("program_version")}, "stop_price": None, "target_price": None, "candle_close_ts": int(visible[-1]["candle_close_ts"]), "last_close": float(visible[-1]["close"]), "strategy_registry_id": registry["registry_id"], "candidate_identity": registry["candidate_identity"], "strategy_version": registry["strategy_version"], "configuration_hash": registry["configuration_hash"], "parameters": parameters, "instrument": requested, "timeframe": timeframe, "execution_envelope": envelope, **{key: envelope[key] for key in ("registry_snapshot_identity", "snapshot_identity", "decision_timestamp", "input_fingerprint", "factor_fingerprint")}}
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
    envelope = _execution_envelope(
            registry, requested=requested, timeframe=timeframe, visible=visible,
        features=features, decision_timestamp=stamp, snapshot_identity=snapshot_identity,
    )
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
        "execution_envelope": envelope,
        **{key: envelope[key] for key in (
            "registry_snapshot_identity", "snapshot_identity", "decision_timestamp", "input_fingerprint", "factor_fingerprint"
        )},
    }


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
