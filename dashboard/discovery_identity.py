"""Canonical, public identity and parameter semantics for Discovery."""
from __future__ import annotations
import hashlib, json
import math
from typing import Any
from .backtest_engine import SHARED_EXECUTION_ENGINE_VERSION
from .discovery_features import FEATURE_VERSION

DISCOVERY_PARAMETER_IDENTITY_VERSION = "discovery-parameter-identity-v1"
DISCOVERY_CANDIDATE_IDENTITY_VERSION = "discovery-candidate-identity-v1"
DISCOVERY_EVALUATION_IDENTITY_VERSION = "discovery-evaluation-identity-v1"
TEMPLATE_VERSION = {"TREND_PULLBACK":"trend-pullback-v1", "VOLATILITY_BREAKOUT":"volatility-breakout-v1", "MEAN_REVERSION":"mean-reversion-v1", "TREND_BREAKOUT":"trend-breakout-v1"}
TEMPLATES = tuple(TEMPLATE_VERSION)
V2_PARAMETER_IDENTITY_VERSION = "discovery-v2-parameter-identity-v1"
V2_CANDIDATE_IDENTITY_VERSION = "discovery-v2-candidate-identity-v1"
V2_EVALUATION_IDENTITY_VERSION = "discovery-v2-evaluation-identity-v1"
V21_PARAMETER_IDENTITY_VERSION = "discovery-v2.1-parameter-identity-v1"
V21_CANDIDATE_IDENTITY_VERSION = "discovery-v2.1-candidate-identity-v1"
V21_EVALUATION_IDENTITY_VERSION = "discovery-v2.1-evaluation-identity-v1"

def _primitive(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)): return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")): raise ValueError("Identity values must be finite.")
        return value
    if isinstance(value, dict): return {str(k): _primitive(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [_primitive(v) for v in value]
    raise ValueError("Identity values must be JSON primitives.")

def canonical_json_hash(value: Any) -> str:
    raw=json.dumps(_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def normalize_template_parameters(template: str, parameters: dict[str, Any]) -> dict[str, Any]:
    if template not in TEMPLATES: raise ValueError("Unknown discovery template.")
    if not isinstance(parameters, dict): raise ValueError("Discovery template parameters must be an object.")
    common={"fast_period","slow_period","fast_ma_type","atr_period","volume_enabled","minimum_volume_ratio"}
    special={"TREND_PULLBACK":{"maximum_distance"},"MEAN_REVERSION":{"rsi_lower","rsi_upper"},"VOLATILITY_BREAKOUT":set(),"TREND_BREAKOUT":set()}[template]
    unknown=set(parameters)-common-special
    if unknown: raise ValueError("Unknown or inactive Discovery template parameters: " + ", ".join(sorted(unknown)))
    # Public requests accept JSON numbers, never numeric strings or booleans as
    # numbers.  This keeps persisted identities independent of coercion quirks.
    def integer(name: str, default: int) -> int:
        value=parameters.get(name,default)
        if isinstance(value,bool) or not isinstance(value,int):
            raise ValueError(f"{name} must be an integer JSON value.")
        return value
    def finite_number(name: str, default: float) -> float:
        value=parameters.get(name,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite numeric JSON value.")
        return float(value)
    volume=parameters.get("volume_enabled",False)
    if not isinstance(volume,bool): raise ValueError("volume_enabled must be a boolean JSON value.")
    if not volume and "minimum_volume_ratio" in parameters:
        raise ValueError("Unknown or inactive Discovery template parameters: minimum_volume_ratio")
    ma_type=parameters.get("fast_ma_type","EMA")
    if not isinstance(ma_type,str): raise ValueError("fast_ma_type must be a string.")
    p={"fast_period":integer("fast_period",20),"slow_period":integer("slow_period",200),"fast_ma_type":ma_type,"atr_period":integer("atr_period",14),"volume_enabled":volume}
    if p["fast_period"] not in (6,10,20,30,60) or p["slow_period"] not in (60,100,150,200) or p["fast_period"] >= p["slow_period"]: raise ValueError("fast_period must be supported and smaller than slow_period.")
    if p["fast_ma_type"] not in ("SMA","EMA") or p["atr_period"] not in (7,10,14,20,28): raise ValueError("Unsupported Discovery template parameter.")
    if p["volume_enabled"]:
        ratio=round(finite_number("minimum_volume_ratio",1.0),2)
        if not .70 <= ratio <= 2.00: raise ValueError("minimum_volume_ratio must be 0.70..2.00.")
        p["minimum_volume_ratio"]=ratio
    if template == "TREND_PULLBACK":
        distance=round(finite_number("maximum_distance",.004),4)
        if distance not in (.002,.003,.004,.005,.006,.008): raise ValueError("maximum_distance must use the supported grid.")
        p["maximum_distance"]=distance
    if template == "MEAN_REVERSION":
        low=integer("rsi_lower",35); high=integer("rsi_upper",65)
        if not 20 <= low <= 49 or not 51 <= high <= 80 or low >= high: raise ValueError("RSI thresholds must be supported and ordered.")
        p.update(rsi_lower=low,rsi_upper=high)
    return p

def build_parameter_identity(template: str, parameters: dict[str, Any]) -> str:
    if template.endswith("_V2_1"):
        from .strategy_v2_1 import normalize_parameters, TEMPLATE_VERSION as versions, DISCOVERY_STRATEGY_VERSION
        normalized=normalize_parameters(template,parameters)
        return canonical_json_hash({"parameter_identity_version":V21_PARAMETER_IDENTITY_VERSION,
          "strategy_version":DISCOVERY_STRATEGY_VERSION,"template":template,"template_version":versions[template],
          "feature_version":FEATURE_VERSION,"parameters":normalized})
    if template.endswith("_V2"):
        from .strategy_v2 import normalize_parameters, TEMPLATE_VERSION as v2_versions, DISCOVERY_STRATEGY_VERSION, V2_FEATURE_VERSION
        normalized=normalize_parameters(template,parameters)
        return canonical_json_hash({"parameter_identity_version":V2_PARAMETER_IDENTITY_VERSION,"strategy_version":DISCOVERY_STRATEGY_VERSION,"template":template,"template_version":v2_versions[template],"feature_version":V2_FEATURE_VERSION,"parameters":normalized})
    normalized=normalize_template_parameters(template,parameters)
    return canonical_json_hash({"parameter_identity_version":DISCOVERY_PARAMETER_IDENTITY_VERSION,"template":template,"template_version":TEMPLATE_VERSION[template],"feature_version":FEATURE_VERSION,"parameters":normalized})

def build_candidate_identity(template: str, parameters: dict[str, Any], execution_hash: str) -> str:
    if template.endswith("_V2_1"):
        from .strategy_v2_1 import TEMPLATE_VERSION as versions, DISCOVERY_STRATEGY_VERSION
        return canonical_json_hash({"candidate_identity_version":V21_CANDIDATE_IDENTITY_VERSION,
          "strategy_version":DISCOVERY_STRATEGY_VERSION,"parameter_hash":build_parameter_identity(template,parameters),
          "execution_hash":execution_hash,"template_version":versions[template],"feature_version":FEATURE_VERSION,
          "execution_engine_version":SHARED_EXECUTION_ENGINE_VERSION})
    if template.endswith("_V2"):
        from .strategy_v2 import TEMPLATE_VERSION as v2_versions, DISCOVERY_STRATEGY_VERSION, V2_FEATURE_VERSION
        return canonical_json_hash({"candidate_identity_version":V2_CANDIDATE_IDENTITY_VERSION,"strategy_version":DISCOVERY_STRATEGY_VERSION,"parameter_hash":build_parameter_identity(template,parameters),"execution_hash":execution_hash,"template_version":v2_versions[template],"feature_version":V2_FEATURE_VERSION,"execution_engine_version":SHARED_EXECUTION_ENGINE_VERSION})
    return canonical_json_hash({"candidate_identity_version":DISCOVERY_CANDIDATE_IDENTITY_VERSION,"parameter_hash":build_parameter_identity(template,parameters),"execution_hash":execution_hash,"template_version":TEMPLATE_VERSION[template],"feature_version":FEATURE_VERSION,"execution_engine_version":SHARED_EXECUTION_ENGINE_VERSION})

def build_evaluation_identity(candidate_config_hash: str, instrument: str, timeframe: str, start_ts: int, end_ts: int, dataset_fingerprint: str | None) -> str:
    # Version derives from the candidate identity, avoiding a v1/v2 collision while
    # retaining byte-for-byte v1 evaluation identities.
    version=V2_EVALUATION_IDENTITY_VERSION if candidate_config_hash.startswith("v2:") else DISCOVERY_EVALUATION_IDENTITY_VERSION
    # Candidate hashes are opaque SHA-256 values; callers needing v2 must pass the
    # explicit marker through the dedicated helper below.
    return canonical_json_hash({"evaluation_identity_version":version,"candidate_config_hash":candidate_config_hash,"instrument":instrument,"timeframe":timeframe,"start_ts":int(start_ts),"end_ts":int(end_ts),"dataset_fingerprint":dataset_fingerprint})

def build_v2_evaluation_identity(candidate_config_hash: str, instrument: str, timeframe: str, start_ts: int, end_ts: int, dataset_fingerprint: str | None) -> str:
    return canonical_json_hash({"evaluation_identity_version":V2_EVALUATION_IDENTITY_VERSION,"candidate_config_hash":candidate_config_hash,"instrument":instrument,"timeframe":timeframe,"start_ts":int(start_ts),"end_ts":int(end_ts),"dataset_fingerprint":dataset_fingerprint})

def build_v21_evaluation_identity(candidate_config_hash: str, instrument: str, timeframe: str, start_ts: int, end_ts: int, dataset_fingerprint: str | None) -> str:
    return canonical_json_hash({"evaluation_identity_version":V21_EVALUATION_IDENTITY_VERSION,
      "candidate_config_hash":candidate_config_hash,"instrument":instrument,"timeframe":timeframe,
      "start_ts":int(start_ts),"end_ts":int(end_ts),"dataset_fingerprint":dataset_fingerprint})
