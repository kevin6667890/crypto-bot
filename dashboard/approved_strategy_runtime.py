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
    parameters = dict(registry["parameters"])
    if definition.get("parameters") != parameters:
        raise ValueError("approved strategy parameter snapshots disagree")
    if canonical_hash(definition) != registry.get("configuration_hash"):
        raise ValueError("approved strategy configuration hash mismatch")
    requested = canonical_instrument(instrument)
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
