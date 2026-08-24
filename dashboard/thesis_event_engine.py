"""Deterministic, read-only historical conditional evidence engine.

This is an event-study domain core.  It does not import an API framework,
trading execution, strategy code, persistence writers, or an LLM.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    from discovery_features import FEATURE_VERSION as DISCOVERY_FEATURE_VERSION, build_features
    from market_context_v2 import (INDICATOR_REGISTRY_VERSION, TIMEFRAME_SECONDS,
                                   _percentile_rank, confirmed_candles_as_of)
    from signal_identity import canonical_json
except ImportError:
    from .discovery_features import FEATURE_VERSION as DISCOVERY_FEATURE_VERSION, build_features
    from .market_context_v2 import (INDICATOR_REGISTRY_VERSION, TIMEFRAME_SECONDS,
                                    _percentile_rank, confirmed_candles_as_of)
    from .signal_identity import canonical_json


THESIS_SPEC_VERSION = "thesis-spec-v1"
COMPILED_DEFINITION_VERSION = "compiled-event-definition-v1"
FEATURE_REGISTRY_VERSION = "thesis-feature-registry-v1"
COVERAGE_POLICY_VERSION = "thesis-coverage-policy-v1"
INDEPENDENCE_POLICY_VERSION = "event-independence-max-horizon-v1"
OUTCOME_POLICY_VERSION = "post-event-outcome-exact-close-v1"
SAMPLE_QUALITY_POLICY_VERSION = "sample-quality-v1"
ENGINE_VERSION = "thesis-event-engine-v1"
RESULT_VERSION = "thesis-test-result-v1"
CAPABILITIES_VERSION = "thesis-capabilities-v1"

SUPPORTED_INSTRUMENTS = {"BTC": "BTC-USDT", "ETH": "ETH-USDT", "SOL": "SOL-USDT"}
SUPPORTED_TIMEFRAMES = ("1H", "4H")
SUPPORTED_HORIZONS = ("4H", "12H", "24H")
HORIZON_SECONDS = {"4H": 14_400, "12H": 43_200, "24H": 86_400}
MAX_SOURCE_ROWS = 20_000


class ThesisValidationError(ValueError):
    """A stable, user-safe validation error."""


class CoverageQualification(str, Enum):
    SUPPORTED = "SUPPORTED"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    STALE_CURRENT_DATA = "STALE_CURRENT_DATA"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ConditionV1:
    feature: str
    operator: str
    value: bool | float


@dataclass(frozen=True)
class EventIndependencePolicyV1:
    version: str = INDEPENDENCE_POLICY_VERSION
    exclude_overlapping_forward_windows: bool = True


@dataclass(frozen=True)
class ThesisSpecV1:
    version: str
    instrument: str
    timeframe: str
    required_conditions: tuple[ConditionV1, ...]
    optional_conditions: tuple[ConditionV1, ...] = ()
    forward_horizons: tuple[str, ...] = SUPPORTED_HORIZONS
    event_independence: EventIndependencePolicyV1 = field(default_factory=EventIndependencePolicyV1)
    requested_as_of: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ThesisSpecV1":
        if not isinstance(payload, Mapping):
            raise ThesisValidationError("request body must be an object")
        allowed = {"version", "instrument", "timeframe", "required_conditions",
                   "optional_conditions", "forward_horizons", "event_independence",
                   "requested_as_of", "metadata"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ThesisValidationError(f"unsupported fields: {', '.join(unknown)}")
        version = payload.get("version")
        if version != THESIS_SPEC_VERSION:
            raise ThesisValidationError(f"version must be {THESIS_SPEC_VERSION}")
        required = _parse_conditions(payload.get("required_conditions"), "required_conditions")
        if not required:
            raise ThesisValidationError("required_conditions must not be empty")
        optional = _parse_conditions(payload.get("optional_conditions", []), "optional_conditions")
        instrument = str(payload.get("instrument", "")).upper()
        if instrument not in SUPPORTED_INSTRUMENTS:
            raise ThesisValidationError(f"unsupported instrument: {instrument or '<empty>'}")
        timeframe = str(payload.get("timeframe", ""))
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ThesisValidationError(f"unsupported timeframe: {timeframe or '<empty>'}")
        horizons_raw = payload.get("forward_horizons", list(SUPPORTED_HORIZONS))
        if not isinstance(horizons_raw, list) or not horizons_raw:
            raise ThesisValidationError("forward_horizons must be a non-empty array")
        horizons = tuple(sorted(set(map(str, horizons_raw)), key=lambda item: HORIZON_SECONDS.get(item, 10**12)))
        unsupported_horizons = [item for item in horizons if item not in SUPPORTED_HORIZONS]
        if unsupported_horizons:
            raise ThesisValidationError(f"unsupported forward horizon: {unsupported_horizons[0]}")
        independence_raw = payload.get("event_independence", {})
        if not isinstance(independence_raw, Mapping):
            raise ThesisValidationError("event_independence must be an object")
        independence_allowed = {"version", "exclude_overlapping_forward_windows"}
        if set(independence_raw) - independence_allowed:
            raise ThesisValidationError("unsupported event_independence field")
        overlap_value = independence_raw.get("exclude_overlapping_forward_windows", True)
        if not isinstance(overlap_value, bool):
            raise ThesisValidationError("exclude_overlapping_forward_windows must be boolean")
        independence = EventIndependencePolicyV1(
            version=str(independence_raw.get("version", INDEPENDENCE_POLICY_VERSION)),
            exclude_overlapping_forward_windows=overlap_value,
        )
        if independence.version != INDEPENDENCE_POLICY_VERSION:
            raise ThesisValidationError(f"unsupported independence policy: {independence.version}")
        requested_as_of = payload.get("requested_as_of")
        if isinstance(requested_as_of, bool) or not isinstance(requested_as_of, int) or requested_as_of <= 0:
            raise ThesisValidationError("requested_as_of is required and must be a positive Unix timestamp in seconds")
        if requested_as_of > int(time.time()) + 5:
            raise ThesisValidationError("requested_as_of must not be in the future")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ThesisValidationError("metadata must be an object")
        # Metadata is provenance only and may not smuggle executable values.
        try:
            canonical_json(metadata)
        except (TypeError, ValueError) as error:
            raise ThesisValidationError("metadata must be finite JSON data") from error
        return cls(str(version), instrument, timeframe, required, optional, horizons,
                   independence, requested_as_of, dict(metadata))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_conditions(raw: Any, field_name: str) -> tuple[ConditionV1, ...]:
    if not isinstance(raw, list):
        raise ThesisValidationError(f"{field_name} must be an array")
    output: list[ConditionV1] = []
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"feature", "operator", "value"}:
            raise ThesisValidationError(f"each {field_name} item must contain only feature, operator, value")
        value = item["value"]
        if not isinstance(value, (bool, int, float)) or isinstance(value, complex):
            raise ThesisValidationError("condition value must be boolean or numeric")
        if isinstance(value, float) and not math.isfinite(value):
            raise ThesisValidationError("condition value must be finite")
        normalized = value if isinstance(value, bool) else float(value)
        normalized = 0.0 if not isinstance(normalized, bool) and normalized == 0 else normalized
        output.append(ConditionV1(str(item["feature"]), str(item["operator"]), normalized))
    return tuple(output)


Evaluator = Callable[[Mapping[str, Any]], bool | float | None]


@dataclass(frozen=True)
class FeatureDefinition:
    code: str
    version: str
    source_group: str
    allowed_operators: tuple[str, ...]
    value_type: str
    evaluator: Evaluator
    minimum_history: int
    missing_data_behavior: str = "UNKNOWN_NEVER_MATCHES"
    state_transition_based: bool = False
    requires_confirmed_candle: bool = True
    supported_timeframes: tuple[str, ...] = SUPPORTED_TIMEFRAMES
    minimum_value: float | None = None
    maximum_value: float | None = None


def _get(name: str) -> Evaluator:
    return lambda row: row.get(name)


FEATURE_REGISTRY: Mapping[str, FeatureDefinition] = {
    "PRICE_ABOVE_EMA20": FeatureDefinition("PRICE_ABOVE_EMA20", INDICATOR_REGISTRY_VERSION, "OHLCV", ("eq",), "boolean", _get("price_above_ema20"), 20),
    "PRICE_BELOW_EMA20": FeatureDefinition("PRICE_BELOW_EMA20", INDICATOR_REGISTRY_VERSION, "OHLCV", ("eq",), "boolean", _get("price_below_ema20"), 20),
    "PRICE_ABOVE_MA60": FeatureDefinition("PRICE_ABOVE_MA60", INDICATOR_REGISTRY_VERSION, "OHLCV", ("eq",), "boolean", _get("price_above_ma60"), 60),
    "PRICE_BELOW_MA60": FeatureDefinition("PRICE_BELOW_MA60", INDICATOR_REGISTRY_VERSION, "OHLCV", ("eq",), "boolean", _get("price_below_ma60"), 60),
    "PRICE_ABOVE_MA200": FeatureDefinition("PRICE_ABOVE_MA200", INDICATOR_REGISTRY_VERSION, "OHLCV", ("eq",), "boolean", _get("price_above_ma200"), 200),
    "PRICE_BELOW_MA200": FeatureDefinition("PRICE_BELOW_MA200", INDICATOR_REGISTRY_VERSION, "OHLCV", ("eq",), "boolean", _get("price_below_ma200"), 200),
    "DISTANCE_TO_MA200_PCT": FeatureDefinition("DISTANCE_TO_MA200_PCT", INDICATOR_REGISTRY_VERSION, "OHLCV", ("gt", "gte", "lt", "lte"), "number", _get("distance_to_ma200_pct"), 200),
    "VOLUME_RATIO": FeatureDefinition("VOLUME_RATIO", DISCOVERY_FEATURE_VERSION, "OHLCV", ("gt", "gte", "lt", "lte"), "number", _get("volume_ratio"), 21, minimum_value=0),
    "VOLUME_PERCENTILE": FeatureDefinition("VOLUME_PERCENTILE", FEATURE_REGISTRY_VERSION, "OHLCV", ("gt", "gte", "lt", "lte"), "number", _get("volume_percentile"), 20, minimum_value=0, maximum_value=100),
    "ATR_PCT": FeatureDefinition("ATR_PCT", INDICATOR_REGISTRY_VERSION, "OHLCV", ("gt", "gte", "lt", "lte"), "number", _get("atr_pct"), 15, minimum_value=0),
    "VOLATILITY_COMPRESSION_PERCENTILE": FeatureDefinition("VOLATILITY_COMPRESSION_PERCENTILE", INDICATOR_REGISTRY_VERSION, "OHLCV", ("gt", "gte", "lt", "lte"), "number", _get("compression_percentile"), 39, minimum_value=0, maximum_value=100),
    "VOLATILITY_EXPANSION_PERCENTILE": FeatureDefinition("VOLATILITY_EXPANSION_PERCENTILE", INDICATOR_REGISTRY_VERSION, "OHLCV", ("gt", "gte", "lt", "lte"), "number", _get("expansion_percentile"), 39, minimum_value=0, maximum_value=100),
    "RSI": FeatureDefinition("RSI", INDICATOR_REGISTRY_VERSION, "OHLCV", ("gt", "gte", "lt", "lte"), "number", _get("rsi"), 15, minimum_value=0, maximum_value=100),
    "PRICE_MOMENTUM": FeatureDefinition("PRICE_MOMENTUM", INDICATOR_REGISTRY_VERSION, "OHLCV", ("gt", "gte", "lt", "lte"), "number", _get("price_momentum"), 15),
    "MOMENTUM_PERSISTENCE": FeatureDefinition("MOMENTUM_PERSISTENCE", INDICATOR_REGISTRY_VERSION, "OHLCV", ("gt", "gte", "lt", "lte"), "number", _get("momentum_persistence"), 15, minimum_value=-1, maximum_value=1),
    # Closed registry entries make flow requests explicit.  V1 refuses to
    # fabricate or substitute these until a bounded aligned historical reader
    # supplies native observations.
    "OI_CHANGE": FeatureDefinition("OI_CHANGE", "canonical-oi-native-v1", "OI", ("gt", "gte", "lt", "lte"), "number", _get("oi_change"), 30),
    "OI_CHANGE_PERCENTILE": FeatureDefinition("OI_CHANGE_PERCENTILE", "canonical-oi-native-v1", "OI", ("gt", "gte", "lt", "lte"), "number", _get("oi_change_percentile"), 30, minimum_value=0, maximum_value=100),
    "CVD_CONFIRMING_PRICE": FeatureDefinition("CVD_CONFIRMING_PRICE", "canonical-cvd-native-v1", "CVD", ("eq",), "boolean", _get("cvd_confirming_price"), 30),
    "CVD_DIVERGING_PRICE": FeatureDefinition("CVD_DIVERGING_PRICE", "canonical-cvd-native-v1", "CVD", ("eq",), "boolean", _get("cvd_diverging_price"), 30),
}

# Product-facing metadata lives beside the executable registry.  Prompts and
# clients consume the capabilities projection below; they never maintain a
# second feature vocabulary.
FEATURE_METADATA: Mapping[str, Mapping[str, Any]] = {
    "PRICE_ABOVE_EMA20": {"en": "Price above EMA20", "zh": "价格高于 EMA20", "unit": "boolean", "availability": "AVAILABLE"},
    "PRICE_BELOW_EMA20": {"en": "Price below EMA20", "zh": "价格低于 EMA20", "unit": "boolean", "availability": "AVAILABLE"},
    "PRICE_ABOVE_MA60": {"en": "Price above MA60", "zh": "价格高于 MA60", "unit": "boolean", "availability": "AVAILABLE"},
    "PRICE_BELOW_MA60": {"en": "Price below MA60", "zh": "价格低于 MA60", "unit": "boolean", "availability": "AVAILABLE"},
    "PRICE_ABOVE_MA200": {"en": "Price above MA200", "zh": "价格高于 MA200", "unit": "boolean", "availability": "AVAILABLE"},
    "PRICE_BELOW_MA200": {"en": "Price below MA200", "zh": "价格低于 MA200", "unit": "boolean", "availability": "AVAILABLE"},
    "DISTANCE_TO_MA200_PCT": {"en": "Distance to MA200", "zh": "距 MA200 百分比", "unit": "percent", "availability": "AVAILABLE"},
    "VOLUME_RATIO": {"en": "Volume ratio", "zh": "成交量比率", "unit": "ratio", "availability": "AVAILABLE"},
    "VOLUME_PERCENTILE": {"en": "Volume percentile", "zh": "成交量百分位", "unit": "percentile", "availability": "AVAILABLE"},
    "ATR_PCT": {"en": "ATR percentage", "zh": "ATR 百分比", "unit": "percent", "availability": "AVAILABLE"},
    "VOLATILITY_COMPRESSION_PERCENTILE": {"en": "Volatility compression percentile", "zh": "波动率压缩百分位", "unit": "percentile", "availability": "AVAILABLE"},
    "VOLATILITY_EXPANSION_PERCENTILE": {"en": "Volatility expansion percentile", "zh": "波动率扩张百分位", "unit": "percentile", "availability": "AVAILABLE"},
    "RSI": {"en": "RSI", "zh": "RSI", "unit": "index", "availability": "AVAILABLE"},
    "PRICE_MOMENTUM": {"en": "Price momentum", "zh": "价格动量", "unit": "percent", "availability": "AVAILABLE"},
    "MOMENTUM_PERSISTENCE": {"en": "Momentum persistence", "zh": "动量持续性", "unit": "score", "availability": "AVAILABLE"},
    "OI_CHANGE": {"en": "Open interest change", "zh": "持仓量变化", "unit": "percent", "availability": "NOT_CURRENTLY_TESTABLE"},
    "OI_CHANGE_PERCENTILE": {"en": "Open interest change percentile", "zh": "持仓量变化百分位", "unit": "percentile", "availability": "NOT_CURRENTLY_TESTABLE"},
    "CVD_CONFIRMING_PRICE": {"en": "CVD confirming price", "zh": "CVD 确认价格", "unit": "boolean", "availability": "NOT_CURRENTLY_TESTABLE"},
    "CVD_DIVERGING_PRICE": {"en": "CVD diverging from price", "zh": "CVD 与价格背离", "unit": "boolean", "availability": "NOT_CURRENTLY_TESTABLE"},
}


def thesis_capabilities() -> dict[str, Any]:
    """Return a stable public projection of the closed executable registry."""
    if set(FEATURE_METADATA) != set(FEATURE_REGISTRY):
        raise RuntimeError("thesis feature metadata is not aligned with the registry")
    features = []
    for code in sorted(FEATURE_REGISTRY):
        definition, metadata = FEATURE_REGISTRY[code], FEATURE_METADATA[code]
        features.append({
            "code": code,
            "label": {"en": metadata["en"], "zh": metadata["zh"]},
            "value_type": definition.value_type,
            "unit": metadata["unit"],
            "operators": list(definition.allowed_operators),
            "bounds": {"minimum": definition.minimum_value, "maximum": definition.maximum_value},
            "requires_threshold": definition.value_type == "number",
            "fixed_value": True if definition.value_type == "boolean" else None,
            "input_scale": "percentage_points" if metadata["unit"] == "percent" else "identity",
            "source_group": definition.source_group,
            "availability": metadata["availability"],
            "supported_timeframes": list(definition.supported_timeframes),
        })
    return {
        "version": CAPABILITIES_VERSION,
        "thesis_spec_version": THESIS_SPEC_VERSION,
        "feature_registry_version": FEATURE_REGISTRY_VERSION,
        "instruments": sorted(SUPPORTED_INSTRUMENTS),
        "timeframes": list(SUPPORTED_TIMEFRAMES),
        "horizons": list(SUPPORTED_HORIZONS),
        "features": features,
        "unsupported_concepts": ["CONFIRMED_STRUCTURE_BREAKOUT", "FAILED_BREAKOUT"],
    }


@dataclass(frozen=True)
class CompiledEventDefinition:
    version: str
    instrument: str
    canonical_instrument: str
    timeframe: str
    required_conditions: tuple[ConditionV1, ...]
    optional_conditions: tuple[ConditionV1, ...]
    forward_horizons: tuple[str, ...]
    feature_versions: Mapping[str, str]
    source_requirements: tuple[str, ...]
    event_transition_semantics: str
    independence_policy: EventIndependencePolicyV1
    definition_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compile_thesis(spec: ThesisSpecV1) -> CompiledEventDefinition:
    conditions = [*spec.required_conditions, *spec.optional_conditions]
    for condition in conditions:
        definition = FEATURE_REGISTRY.get(condition.feature)
        if definition is None:
            raise ThesisValidationError(f"unsupported feature: {condition.feature}")
        if spec.timeframe not in definition.supported_timeframes:
            raise ThesisValidationError(f"feature {condition.feature} does not support {spec.timeframe}")
        if condition.operator not in definition.allowed_operators:
            raise ThesisValidationError(f"invalid operator {condition.operator} for {condition.feature}")
        if definition.value_type == "boolean" and not isinstance(condition.value, bool):
            raise ThesisValidationError(f"{condition.feature} requires a boolean value")
        if definition.value_type == "number" and (isinstance(condition.value, bool) or not isinstance(condition.value, (int, float))):
            raise ThesisValidationError(f"{condition.feature} requires a numeric threshold")
        if (definition.value_type == "number" and definition.minimum_value is not None and
                float(condition.value) < definition.minimum_value):
            raise ThesisValidationError(f"threshold for {condition.feature} must be >= {definition.minimum_value}")
        if (definition.value_type == "number" and definition.maximum_value is not None and
                float(condition.value) > definition.maximum_value):
            raise ThesisValidationError(f"threshold for {condition.feature} must be <= {definition.maximum_value}")
    condition_key = lambda item: (item.feature, item.operator, canonical_json(item.value))
    required = tuple(sorted(spec.required_conditions, key=condition_key))
    optional = tuple(sorted(spec.optional_conditions, key=condition_key))
    if len(set(condition_key(item) for item in required)) != len(required):
        raise ThesisValidationError("required_conditions contains an exact duplicate")
    if len(set(condition_key(item) for item in optional)) != len(optional):
        raise ThesisValidationError("optional_conditions contains an exact duplicate")
    feature_versions = {code: FEATURE_REGISTRY[code].version for code in sorted({item.feature for item in conditions})}
    sources = tuple(sorted({FEATURE_REGISTRY[item.feature].source_group for item in spec.required_conditions}))
    identity = {
        "version": COMPILED_DEFINITION_VERSION, "instrument": spec.instrument,
        "canonical_instrument": SUPPORTED_INSTRUMENTS[spec.instrument], "timeframe": spec.timeframe,
        "required_conditions": [asdict(item) for item in required],
        "optional_conditions": [asdict(item) for item in optional],
        "forward_horizons": list(spec.forward_horizons), "feature_versions": feature_versions,
        "source_requirements": list(sources),
        "event_transition_semantics": "COMPOSITE_FALSE_TO_TRUE_CONFIRMED_CLOSE_V1",
        "independence_policy": asdict(spec.event_independence),
    }
    return CompiledEventDefinition(
        version=COMPILED_DEFINITION_VERSION, instrument=spec.instrument,
        canonical_instrument=SUPPORTED_INSTRUMENTS[spec.instrument], timeframe=spec.timeframe,
        required_conditions=required, optional_conditions=optional,
        forward_horizons=spec.forward_horizons, feature_versions=feature_versions,
        source_requirements=sources,
        event_transition_semantics="COMPOSITE_FALSE_TO_TRUE_CONFIRMED_CLOSE_V1",
        independence_policy=spec.event_independence, definition_hash=_hash(identity))


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def compile_feature_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compile all price features in one causal pass using canonical primitives."""
    candles = [dict(item) for item in rows]
    base = build_features(candles, {"ma_periods": [20, 60, 200], "atr_period": 14,
                                    "bb_period": 20, "rsi_period": 14,
                                    "volume_period": 20})
    closes = [float(item["close"]) for item in candles]
    volumes = [float(item["volume"]) for item in candles]
    output: list[dict[str, Any]] = []
    for index, (candle, item) in enumerate(zip(candles, base)):
        close = closes[index]
        ma60, ma200, ema20 = item.get("sma_60"), item.get("sma_200"), item.get("ema_20")
        widths = [float(prior["bb_width"]) for prior in base[max(0, index - 99):index + 1]
                  if prior.get("bb_width") is not None]
        width = item.get("bb_width")
        width_rank = _percentile_rank(widths, float(width)) if width is not None else None
        volume_window = volumes[max(0, index - 99):index + 1]
        volume_rank = _percentile_rank(volume_window, volumes[index])
        changes = [closes[pos] / closes[pos - 1] - 1
                   for pos in range(max(1, index - 13), index + 1) if closes[pos - 1]]
        persistence = ((sum(change > 0 for change in changes) - sum(change < 0 for change in changes)) /
                       len(changes)) if len(changes) == 14 else None
        compiled = {
            "ts": int(candle["ts"]), "candle_close_ts": int(candle["candle_close_ts"]),
            "close": close, "high": float(candle["high"]), "low": float(candle["low"]),
            "price_above_ema20": close > float(ema20) if ema20 is not None and index >= 19 else None,
            "price_below_ema20": close < float(ema20) if ema20 is not None and index >= 19 else None,
            "price_above_ma60": close > float(ma60) if ma60 is not None else None,
            "price_below_ma60": close < float(ma60) if ma60 is not None else None,
            "price_above_ma200": close > float(ma200) if ma200 is not None else None,
            "price_below_ma200": close < float(ma200) if ma200 is not None else None,
            "distance_to_ma200_pct": (close / float(ma200) - 1) * 100 if ma200 else None,
            "volume_ratio": item.get("volume_ratio"), "volume_percentile": volume_rank,
            "atr_pct": float(item["atr"]) / close * 100 if item.get("atr") and close else None,
            "compression_percentile": 100 - width_rank if width_rank is not None else None,
            "expansion_percentile": width_rank, "rsi": item.get("rsi"),
            "price_momentum": (close / closes[index - 14] - 1) * 100 if index >= 14 and closes[index - 14] else None,
            "momentum_persistence": persistence,
        }
        # Native flow values are accepted only from an explicitly aligned
        # source adapter.  They are copied, never inferred from candle data.
        for key in ("oi_change", "oi_change_percentile", "cvd_confirming_price",
                    "cvd_diverging_price"):
            compiled[key] = candle.get(key)
        output.append(compiled)
    return output


@dataclass(frozen=True)
class CoverageResult:
    feature: str
    requested_start: int | None
    requested_end: int
    available_start: int | None
    available_end: int | None
    total_expected_observations: int
    usable_observations: int
    coverage_ratio: float
    gaps: tuple[Mapping[str, int], ...]
    stale: bool
    partial: bool
    qualification: str
    reason: str


@dataclass(frozen=True)
class CoverageGateResult:
    version: str
    qualification: str
    testable: bool
    common_start: int | None
    common_end: int | None
    features: tuple[CoverageResult, ...]
    reason: str | None = None
    testable_subset: tuple[str, ...] = ()


def coverage_gate(definition: CompiledEventDefinition, feature_rows: Sequence[Mapping[str, Any]],
                  as_of: int, source_quality: Mapping[str, str] | None = None) -> CoverageGateResult:
    width = TIMEFRAME_SECONDS[definition.timeframe]
    required_codes = tuple(sorted({item.feature for item in definition.required_conditions}))
    results: list[CoverageResult] = []
    starts: list[int] = []
    ends: list[int] = []
    for code in required_codes:
        feature = FEATURE_REGISTRY[code]
        quality = (source_quality or {}).get(feature.source_group, "VALID" if feature.source_group == "OHLCV" else "UNAVAILABLE")
        usable = [row for row in feature_rows if feature.evaluator(row) is not None]
        available_start = int(usable[0]["candle_close_ts"]) if usable else None
        available_end = int(usable[-1]["candle_close_ts"]) if usable else None
        requested_start = int(feature_rows[0]["candle_close_ts"]) if feature_rows else None
        expected = ((available_end - available_start) // width + 1
                    if available_start is not None and available_end is not None else 0)
        # A gap in the source prefix can corrupt a later rolling value even if
        # the gap predates the first evaluable row, so inspect the full prefix.
        timestamps = [int(row["candle_close_ts"]) for row in feature_rows]
        gaps = tuple({"start": left, "end": right, "missing_observations": (right - left) // width - 1}
                     for left, right in zip(timestamps, timestamps[1:]) if right - left > width)
        stale = available_end is not None and as_of - available_end > width * 2
        ratio = len(usable) / expected if expected else 0.0
        partial = bool(gaps) or (0 < ratio < 0.95)
        if quality in {"UNAVAILABLE", "MISSING"}:
            qualification, reason = CoverageQualification.UNAVAILABLE, f"native aligned {feature.source_group} history is unavailable"
        elif quality == "PARTIAL":
            qualification, reason = CoverageQualification.PARTIAL, f"native aligned {feature.source_group} history is partial"
        elif quality == "STALE":
            qualification, reason = CoverageQualification.STALE_CURRENT_DATA, f"native aligned {feature.source_group} history is stale"
        elif quality == "GAP":
            qualification, reason = CoverageQualification.INSUFFICIENT_COVERAGE, f"native aligned {feature.source_group} history contains a gap"
        elif not usable:
            qualification, reason = CoverageQualification.UNAVAILABLE, "no usable observations"
        elif len(feature_rows) < feature.minimum_history or len(usable) < max(30, max(HORIZON_SECONDS[item] for item in definition.forward_horizons) // width + 1):
            qualification, reason = CoverageQualification.INSUFFICIENT_HISTORY, "common evaluable history requires at least 30 observations and one maximum-horizon path"
        elif stale:
            qualification, reason = CoverageQualification.STALE_CURRENT_DATA, "latest usable observation exceeds two timeframe intervals"
        elif partial:
            qualification, reason = CoverageQualification.INSUFFICIENT_COVERAGE, "usable coverage is below 95% or contains gaps"
        else:
            qualification, reason = CoverageQualification.SUPPORTED, "qualified by thesis-coverage-policy-v1"
            starts.append(available_start)  # type: ignore[arg-type]
            ends.append(available_end)  # type: ignore[arg-type]
        results.append(CoverageResult(code, requested_start, as_of, available_start, available_end,
                                      expected, len(usable), round(ratio, 8), gaps, stale, partial,
                                      qualification.value, reason))
    common_start = max(starts) if len(starts) == len(results) and starts else None
    common_end = min(ends) if len(ends) == len(results) and ends else None
    testable = bool(results) and all(item.qualification == CoverageQualification.SUPPORTED.value for item in results)
    if testable and (common_start is None or common_end is None or common_start > common_end):
        testable = False
    overall = CoverageQualification.SUPPORTED.value if testable else "THESIS_NOT_TESTABLE_AS_REQUESTED"
    subset = tuple(item.feature for item in results if item.qualification == CoverageQualification.SUPPORTED.value)
    return CoverageGateResult(COVERAGE_POLICY_VERSION, overall, testable, common_start, common_end,
                              tuple(results), None if testable else "required feature coverage did not qualify", subset)


def _compare(actual: bool | float | None, operator: str, expected: bool | float) -> bool | None:
    if actual is None:
        return None
    operations = {"eq": lambda: actual == expected, "gt": lambda: actual > expected,
                  "gte": lambda: actual >= expected, "lt": lambda: actual < expected,
                  "lte": lambda: actual <= expected}
    return bool(operations[operator]())


def _sample_quality(n: int) -> str:
    if n < 10:
        return "INSUFFICIENT"
    if n < 30:
        return "LOW"
    if n < 100:
        return "MODERATE"
    return "ADEQUATE"


def _quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _dataset_identity(rows: Sequence[Mapping[str, Any]], instrument: str, timeframe: str,
                      source_quality: Mapping[str, str]) -> dict[str, Any]:
    stable_rows = [{**{key: row[key] for key in ("ts", "candle_close_ts", "open", "high", "low", "close", "volume", "confirmed")},
                    "source": row.get("source"), "source_version": row.get("source_version"),
                    "source_store": row.get("_source_store"),
                    "oi_change": row.get("oi_change"),
                    "oi_change_percentile": row.get("oi_change_percentile"),
                    "cvd_confirming_price": row.get("cvd_confirming_price"),
                    "cvd_diverging_price": row.get("cvd_diverging_price")} for row in rows]
    sources = sorted({canonical_json({"source": row.get("source") or "unknown",
                                      "source_version": row.get("source_version"),
                                      "source_store": row.get("_source_store")}) for row in rows})
    source_identity = [json.loads(item) for item in sources]
    return {"version": "bounded-ohlcv-dataset-identity-v1", "instrument": instrument,
            "timeframe": timeframe, "start": stable_rows[0]["candle_close_ts"] if stable_rows else None,
            "end": stable_rows[-1]["candle_close_ts"] if stable_rows else None,
            "row_count": len(stable_rows), "content_sha256": _hash({"rows": stable_rows, "source_quality": dict(sorted(source_quality.items()))}),
            "sources": source_identity, "source_quality": dict(sorted(source_quality.items()))}


class ThesisEventEngineV1:
    def run(self, spec: ThesisSpecV1, source_rows: Iterable[Mapping[str, Any]],
            *, source_quality: Mapping[str, str] | None = None) -> dict[str, Any]:
        definition = compile_thesis(spec)
        if spec.requested_as_of is None:  # Defensive for direct dataclass construction.
            raise ThesisValidationError("requested_as_of is required")
        as_of = spec.requested_as_of
        raw_rows = [dict(row) for row in source_rows]
        resolved_quality = {"OHLCV": "VALID", **dict(source_quality or {})}
        allowed_quality = {"VALID", "MISSING", "PARTIAL", "STALE", "GAP", "UNAVAILABLE"}
        if any(not isinstance(group, str) or not isinstance(status, str) or
               group not in {"OHLCV", "OI", "CVD"} or status not in allowed_quality
               for group, status in resolved_quality.items()):
            raise ThesisValidationError("source_quality contains an unsupported group or status")
        required_fields = {"ts", "candle_close_ts", "open", "high", "low", "close", "volume", "confirmed"}
        if any(not required_fields.issubset(row) for row in raw_rows):
            raise ThesisValidationError("source candle is missing an explicit confirmed OHLCV contract field")
        if any(row["confirmed"] is not True and row["confirmed"] != 1 for row in raw_rows):
            raise ThesisValidationError("source contains an unconfirmed candle")
        width = TIMEFRAME_SECONDS[definition.timeframe]
        for row in raw_rows:
            if (isinstance(row["ts"], bool) or not isinstance(row["ts"], int) or
                    isinstance(row["candle_close_ts"], bool) or not isinstance(row["candle_close_ts"], int)):
                raise ThesisValidationError("source candle timestamps must be integer Unix seconds")
            if row["ts"] > as_of or row["candle_close_ts"] > as_of:
                raise ThesisValidationError("source contains a future timestamp beyond requested_as_of")
            if any(isinstance(row[key], bool) for key in ("open", "high", "low", "close", "volume")):
                raise ThesisValidationError("source OHLCV values must be finite numbers")
            try:
                timestamp, close_timestamp = int(row["ts"]), int(row["candle_close_ts"])
                open_value, high, low, close, volume = (float(row[key]) for key in ("open", "high", "low", "close", "volume"))
            except (TypeError, ValueError, OverflowError) as error:
                raise ThesisValidationError("source OHLCV values must be finite numbers") from error
            if not all(math.isfinite(value) for value in (open_value, high, low, close, volume)):
                raise ThesisValidationError("source OHLCV values must be finite numbers")
            if timestamp <= 0 or timestamp % width or close_timestamp != timestamp + width:
                raise ThesisValidationError("source candle timestamps are not aligned to the thesis timeframe")
            if min(open_value, high, low, close) <= 0 or volume < 0:
                raise ThesisValidationError("source prices must be positive and volume must be non-negative")
            if low > min(open_value, close) or high < max(open_value, close) or high < low:
                raise ThesisValidationError("source candle OHLC geometry is invalid")
            for key in ("oi_change", "oi_change_percentile"):
                value = row.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                    raise ThesisValidationError(f"source {key} must be a finite number or null")
            for key in ("cvd_confirming_price", "cvd_diverging_price"):
                if row.get(key) is not None and not isinstance(row[key], bool):
                    raise ThesisValidationError(f"source {key} must be boolean or null")
        unique: dict[int, dict[str, Any]] = {}
        semantic_fields = ("candle_close_ts", "open", "high", "low", "close", "volume", "confirmed",
                           "source", "source_version", "_source_store", "oi_change",
                           "oi_change_percentile", "cvd_confirming_price", "cvd_diverging_price")
        for row in raw_rows:
            timestamp = int(row["ts"])
            if timestamp in unique and any(row.get(key) != unique[timestamp].get(key) for key in semantic_fields):
                raise ThesisValidationError("source contains conflicting duplicate candles")
            unique[timestamp] = row
        raw_rows = [unique[key] for key in sorted(unique)]
        rows = confirmed_candles_as_of(raw_rows, definition.timeframe, as_of)
        feature_rows = compile_feature_rows(rows)
        coverage = coverage_gate(definition, feature_rows, as_of, resolved_quality)
        used_groups = {FEATURE_REGISTRY[item.feature].source_group
                       for item in (*definition.required_conditions, *definition.optional_conditions)}
        identity_quality = {group: status for group, status in resolved_quality.items() if group in used_groups}
        optional_coverage = []
        for condition in definition.optional_conditions:
            feature = FEATURE_REGISTRY[condition.feature]
            quality = resolved_quality.get(feature.source_group, "UNAVAILABLE")
            usable = sum(feature.evaluator(row) is not None for row in feature_rows) if quality == "VALID" else 0
            optional_coverage.append({"feature": condition.feature, "source_group": feature.source_group,
                                      "source_quality": quality, "usable_observations": usable,
                                      "status": "AVAILABLE" if usable else "UNAVAILABLE"})
        warnings = [f"OPTIONAL_FEATURE_UNAVAILABLE:{item['feature']}:{item['source_quality']}"
                    for item in optional_coverage if item["status"] == "UNAVAILABLE"]
        data_identity = _dataset_identity(rows, definition.canonical_instrument, definition.timeframe, identity_quality)
        base = {"result_version": RESULT_VERSION, "status": coverage.qualification,
                "thesis_spec": spec.to_dict(), "compiled_definition": definition.to_dict(),
                "definition_hash": definition.definition_hash, "engine_version": ENGINE_VERSION,
                "feature_versions": dict(definition.feature_versions), "instrument": spec.instrument,
                "canonical_instrument": definition.canonical_instrument, "timeframe": spec.timeframe,
                "tested_range": {"start": coverage.common_start, "end": coverage.common_end},
                "requested_as_of": spec.requested_as_of, "effective_as_of": as_of,
                "coverage": asdict(coverage), "optional_coverage": optional_coverage,
                "data_identity": data_identity,
                "source_identity": data_identity["sources"],
                "limitations": ["Historical conditional evidence; not causal proof, a trading signal, or a forward probability guarantee."],
                "warnings": warnings}
        if not coverage.testable:
            result = {**base, "raw_candidate_count": 0, "independent_event_count": 0,
                      "excluded_overlap_count": 0, "excluded_events_summary": {},
                      "event_records": [], "aggregates": {}}
            result["result_hash"] = self._result_hash(result)
            return result
        common_start, common_end = int(coverage.common_start), int(coverage.common_end)
        scoped = [row for row in feature_rows if common_start <= int(row["candle_close_ts"]) <= common_end]
        candidates: list[dict[str, Any]] = []
        previous_state: bool | None = None
        for row in scoped:
            matched: dict[str, Any] = {}
            states: list[bool | None] = []
            for condition in definition.required_conditions:
                actual = FEATURE_REGISTRY[condition.feature].evaluator(row)
                states.append(_compare(actual, condition.operator, condition.value))
                matched[condition.feature] = actual
            current_state = None if any(state is None for state in states) else all(states)
            if previous_state is False and current_state is True:
                optional = {
                    condition.feature: (FEATURE_REGISTRY[condition.feature].evaluator(row)
                                        if resolved_quality.get(FEATURE_REGISTRY[condition.feature].source_group,
                                                                "UNAVAILABLE") == "VALID" else None)
                    for condition in definition.optional_conditions}
                event_ts = int(row["candle_close_ts"])
                candidates.append({"event_id": _hash({"definition_hash": definition.definition_hash,
                                                       "event_timestamp": event_ts}),
                                   "timestamp": event_ts, "reference_close": float(row["close"]),
                                   "matched_conditions": matched, "optional_observations": optional,
                                   "source_timestamps": [event_ts], "exclusion_status": "INCLUDED",
                                   "exclusion_reason": None, "outcomes": {}})
            previous_state = current_state
        max_horizon = max(HORIZON_SECONDS[item] for item in definition.forward_horizons)
        last_independent: int | None = None
        independent: list[dict[str, Any]] = []
        for event in candidates:
            timestamp = int(event["timestamp"])
            if (definition.independence_policy.exclude_overlapping_forward_windows and
                    last_independent is not None and timestamp < last_independent + max_horizon):
                event["exclusion_status"] = "EXCLUDED"
                event["exclusion_reason"] = "OVERLAPPING_MAX_FORWARD_WINDOW"
                continue
            independent.append(event)
            last_independent = timestamp
        by_close = {int(row["candle_close_ts"]): row for row in feature_rows}
        for event in independent:
            event_ts, reference = int(event["timestamp"]), float(event["reference_close"])
            for horizon in definition.forward_horizons:
                horizon_end = event_ts + HORIZON_SECONDS[horizon]
                path_times = list(range(event_ts + TIMEFRAME_SECONDS[definition.timeframe],
                                        horizon_end + 1, TIMEFRAME_SECONDS[definition.timeframe]))
                missing = [timestamp for timestamp in path_times if timestamp not in by_close]
                if missing:
                    event["outcomes"][horizon] = {"available": False,
                        "censor_reason": "TERMINAL_HISTORY" if missing[-1] > (feature_rows[-1]["candle_close_ts"] if feature_rows else 0) else "PATH_GAP",
                        "forward_return_fraction": None, "mfe_fraction": None, "mae_fraction": None}
                    continue
                path = [by_close[timestamp] for timestamp in path_times]
                terminal = path[-1]
                event["outcomes"][horizon] = {"available": True, "censor_reason": None,
                    "forward_return_fraction": float(terminal["close"]) / reference - 1,
                    "mfe_fraction": max(float(item["high"]) for item in path) / reference - 1,
                    "mae_fraction": min(float(item["low"]) for item in path) / reference - 1}
        aggregates: dict[str, Any] = {}
        for horizon in definition.forward_horizons:
            available = [event["outcomes"][horizon] for event in independent if event["outcomes"][horizon]["available"]]
            returns = [float(item["forward_return_fraction"]) for item in available]
            mfes = [float(item["mfe_fraction"]) for item in available]
            maes = [float(item["mae_fraction"]) for item in available]
            positive, zero = sum(value > 0 for value in returns), sum(value == 0 for value in returns)
            aggregates[horizon] = {"eligible_n": len(returns), "censored_n": len(independent) - len(returns),
                "positive_n": positive, "zero_n": zero, "negative_n": len(returns) - positive - zero,
                "historical_positive_rate": positive / len(returns) if returns else None,
                "mean_return_fraction": sum(returns) / len(returns) if returns else None,
                "median_return_fraction": _quantile(returns, .5), "p25_return_fraction": _quantile(returns, .25),
                "p75_return_fraction": _quantile(returns, .75), "min_return_fraction": min(returns) if returns else None,
                "max_return_fraction": max(returns) if returns else None, "median_mfe_fraction": _quantile(mfes, .5),
                "median_mae_fraction": _quantile(maes, .5), "sample_quality": _sample_quality(len(returns)),
                "sample_quality_policy_version": SAMPLE_QUALITY_POLICY_VERSION}
        excluded = len(candidates) - len(independent)
        result = {**base, "status": "COMPLETED", "raw_candidate_count": len(candidates),
                  "independent_event_count": len(independent), "excluded_overlap_count": excluded,
                  "excluded_events_summary": {"OVERLAPPING_MAX_FORWARD_WINDOW": excluded} if excluded else {},
                  "event_records": candidates, "aggregates": aggregates}
        result["result_hash"] = self._result_hash(result)
        return result

    @staticmethod
    def _result_hash(result: Mapping[str, Any]) -> str:
        deterministic = {key: result[key] for key in (
            "result_version", "status", "compiled_definition", "definition_hash",
            "engine_version", "feature_versions", "instrument", "canonical_instrument",
            "timeframe", "tested_range", "effective_as_of", "coverage", "data_identity",
            "source_identity", "raw_candidate_count", "independent_event_count",
            "excluded_overlap_count", "excluded_events_summary", "event_records", "aggregates")}
        deterministic["outcome_policy_version"] = OUTCOME_POLICY_VERSION
        return _hash(deterministic)


class ThesisTestServiceV1:
    """Thin service boundary around a bounded, read-only candle reader."""

    def __init__(self, reader: Any, engine: ThesisEventEngineV1 | None = None) -> None:
        self.reader = reader
        self.engine = engine or ThesisEventEngineV1()

    def test(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        spec = ThesisSpecV1.from_dict(payload)
        as_of = spec.requested_as_of
        if as_of is None:
            raise ThesisValidationError("requested_as_of is required")
        rows = self.reader.candles(SUPPORTED_INSTRUMENTS[spec.instrument], spec.timeframe,
                                   as_of, MAX_SOURCE_ROWS)
        return self.engine.run(spec, rows)


def utc_iso(timestamp: int | None) -> str | None:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None
