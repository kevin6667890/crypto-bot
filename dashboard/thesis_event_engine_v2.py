"""Deterministic historical event studies for bounded ThesisExpression V2.

V1 deliberately remains in ``thesis_event_engine.py``.  This module has its
own result/compiler versions so stored V1 definitions and result hashes never
change.  It consumes only validated AST nodes and already time-aligned source
facts; it has no model, trading, strategy, or risk dependency.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
import bisect
import hashlib
import hmac
import math
from threading import RLock
from typing import Any, Iterable, Mapping, Sequence

try:
    from market_context_v2 import TIMEFRAME_SECONDS, confirmed_candles_as_of
    from signal_identity import canonical_json
    from thesis_breakout_features import (
        FAILED_BREAKDOWN_CONFIRMED, FAILED_BREAKOUT_CONFIRMED,
        FEATURE_PARAMETER_SCHEMAS, FEATURE_ROW_KEYS,
        ROLLING_HIGH_BREAKOUT_CONFIRMED, ROLLING_LOW_BREAKDOWN_CONFIRMED,
        ROLLING_STRUCTURE_FEATURE_VERSION, RollingStructureHistoricalBatchAdapterV1,
    )
    from thesis_derivatives import CAUSAL_PERCENTILE_VERSION
    from thesis_event_engine import (
        FEATURE_METADATA, FEATURE_REGISTRY, HistoricalDataSelectionError,
        HistoricalDataSelectionPolicyV1, SelectedHistoricalDatasetV1,
        ThesisValidationError, _compare, _quantile, _sample_quality,
        compile_feature_rows,
    )
    from thesis_expression import (
        AllNode, AnyNode, ConditionNode, ExpressionNode, ExpressionValidationError,
        FeatureContractV2, NotNode, ThesisSpecV2, TruthValue,
        evaluate_expression, feature_contracts_from_capabilities,
        parse_thesis_spec_v2, semantic_presets_projection,
    )
except ImportError:
    from .market_context_v2 import TIMEFRAME_SECONDS, confirmed_candles_as_of
    from .signal_identity import canonical_json
    from .thesis_breakout_features import (
        FAILED_BREAKDOWN_CONFIRMED, FAILED_BREAKOUT_CONFIRMED,
        FEATURE_PARAMETER_SCHEMAS, FEATURE_ROW_KEYS,
        ROLLING_HIGH_BREAKOUT_CONFIRMED, ROLLING_LOW_BREAKDOWN_CONFIRMED,
        ROLLING_STRUCTURE_FEATURE_VERSION, RollingStructureHistoricalBatchAdapterV1,
    )
    from .thesis_derivatives import CAUSAL_PERCENTILE_VERSION
    from .thesis_event_engine import (
        FEATURE_METADATA, FEATURE_REGISTRY, ThesisValidationError, _compare,
        _quantile, _sample_quality, compile_feature_rows,
    )
    from .thesis_historical_data import (
        HistoricalDataSelectionError, HistoricalDataSelectionPolicyV1,
        SelectedHistoricalDatasetV1,
    )
    from .thesis_expression import (
        AllNode, AnyNode, ConditionNode, ExpressionNode, ExpressionValidationError,
        FeatureContractV2, NotNode, ThesisSpecV2, TruthValue,
        evaluate_expression, feature_contracts_from_capabilities,
        parse_thesis_spec_v2, semantic_presets_projection,
    )


ENGINE_VERSION = "thesis-event-engine-v2"
RESULT_VERSION = "thesis-test-result-v2"
COMPILED_DEFINITION_VERSION = "compiled-event-definition-v2"
FEATURE_REGISTRY_VERSION = "thesis-feature-registry-v2"
CAPABILITIES_VERSION = "thesis-capabilities-v2"
COVERAGE_POLICY_VERSION = "thesis-coverage-policy-v2"
INDEPENDENCE_POLICY_VERSION = "event-independence-max-horizon-v1"
OUTCOME_POLICY_VERSION = "post-event-outcome-exact-close-v1"
SUPPORTED_INSTRUMENTS = {"BTC": "BTC-USDT", "ETH": "ETH-USDT", "SOL": "SOL-USDT"}
SUPPORTED_TIMEFRAMES = ("15m", "1H", "4H", "1D")
SUPPORTED_HORIZONS = ("4H", "12H", "24H", "3D", "7D")
HORIZON_SECONDS = {"4H": 14_400, "12H": 43_200, "24H": 86_400,
                   "3D": 259_200, "7D": 604_800}
MAX_SOURCE_ROWS_BY_TIMEFRAME = {"15m": 20_000, "1H": 20_000, "4H": 20_000, "1D": 5_000}
DERIVATIVE_FEATURES = {
    "OI_CHANGE_PCT": ("OI", "oi_change_pct", "percent"),
    "OI_CHANGE_PERCENTILE": ("OI", "oi_change_percentile", "percentile"),
    "FUNDING_RATE": ("FUNDING", "funding_rate", "rate"),
    "FUNDING_RATE_PERCENTILE": ("FUNDING", "funding_rate_percentile", "percentile"),
    "BASIS_PCT": ("BASIS", "basis_pct", "percent"),
    "BASIS_PERCENTILE": ("BASIS", "basis_percentile", "percentile"),
}
STRUCTURE_FEATURES = set(FEATURE_ROW_KEYS)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _availability(status: str | None) -> str:
    return "AVAILABLE" if status == "READY" else "DATASET_UNAVAILABLE"


def _semantic_terms(code: str, label: Mapping[str, str]) -> dict[str, list[str]]:
    """Closed, user-visible vocabulary used to ground parser feature choices."""
    explicit = {
        "RSI": {"en": ["rsi", "relative strength"], "zh": ["RSI", "相对强弱"]},
        "VOLUME_PERCENTILE": {"en": ["volume percentile", "volume surge", "significant volume"],
                              "zh": ["成交量百分位", "明显放量", "成交量大幅增加"]},
        "VOLUME_RATIO": {"en": ["volume ratio", "volume multiple"],
                         "zh": ["成交量比率", "成交量倍数", "量比"]},
        "ATR_PERCENTILE": {"en": ["atr", "volatility"], "zh": ["ATR", "波动率"]},
        "OI_CHANGE_PCT": {"en": ["open interest change", "oi change"],
                          "zh": ["持仓量变化", "未平仓量变化", "OI变化"]},
        "OI_CHANGE_PERCENTILE": {"en": ["open interest percentile", "oi percentile", "oi surge", "open interest surge"],
                                 "zh": ["持仓量变化百分位", "OI变化百分位", "OI大幅增加"]},
        "FUNDING_RATE": {"en": ["funding rate", "settled funding"], "zh": ["资金费率", "结算费率"]},
        "FUNDING_RATE_PERCENTILE": {"en": ["funding rate percentile", "funding percentile"],
                                    "zh": ["资金费率百分位", "费率百分位"]},
        "BASIS_PCT": {"en": ["basis", "basis pct", "perpetual basis"], "zh": ["基差", "永续基差"]},
        "BASIS_PERCENTILE": {"en": ["basis percentile", "perpetual basis percentile"],
                             "zh": ["基差百分位", "永续基差百分位"]},
        ROLLING_HIGH_BREAKOUT_CONFIRMED: {
            "en": ["breakout", "breaks above", "breaks the previous", "breaks previous",
                   "previous high", "rolling high"],
            "zh": ["突破", "前高", "最高点", "滚动高点"],
        },
        ROLLING_LOW_BREAKDOWN_CONFIRMED: {
            "en": ["breakdown", "breaks below", "previous low", "rolling low"],
            "zh": ["跌破", "前低", "最低点", "滚动低点"],
        },
        FAILED_BREAKOUT_CONFIRMED: {
            "en": ["failed breakout", "false breakout", "falls back below"],
            "zh": ["假突破", "失败突破", "跌回突破位", "跌回来", "突破后", "重新跌回突破位"],
        },
        FAILED_BREAKDOWN_CONFIRMED: {
            "en": ["failed breakdown", "false breakdown", "recovers above"],
            "zh": ["假跌破", "失败跌破", "收回跌破位", "涨回来", "跌破后", "重新回到跌破位上方"],
        },
    }
    if code in explicit:
        return explicit[code]
    human = code.replace("_", " ").casefold()
    return {"en": sorted({code.casefold(), human, str(label.get("en", "")).casefold()} - {""}),
            "zh": sorted({code, str(label.get("zh", ""))} - {""})}


def thesis_capabilities_v2(
    derivative_readiness: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project the closed executable registry for both parser and frontend."""
    readiness = derivative_readiness or {}
    features: list[dict[str, Any]] = []
    for code in sorted(FEATURE_REGISTRY):
        if code.startswith("CVD_") or code == "OI_CHANGE":
            continue
        definition, metadata = FEATURE_REGISTRY[code], FEATURE_METADATA[code]
        historical = "AVAILABLE"
        current = "AVAILABLE"
        source = definition.source_group
        if source == "OI":
            historical = _availability(str(readiness.get("OI", {}).get("status")))
            current = _availability(str(readiness.get("OI_CURRENT", readiness.get("OI", {})).get("status")))
        supported = list(readiness.get(source, {}).get("supported_timeframes", SUPPORTED_TIMEFRAMES))
        features.append({
            "code": code, "version": definition.version,
            "label": {"en": metadata["en"], "zh": metadata["zh"]},
            "group": "DERIVATIVES" if source != "OHLCV" else "PRICE_INDICATORS",
            "value_type": definition.value_type, "unit": metadata["unit"],
            "operators": list(definition.allowed_operators),
            "bounds": {"minimum": definition.minimum_value, "maximum": definition.maximum_value},
            "parameters": {}, "source_group": source,
            "historical_availability": historical,
            "current_availability": current,
            "historical_availability_reason": (None if historical == "AVAILABLE"
                                                else f"{source}_HISTORICAL_DATASET_UNAVAILABLE"),
            "current_availability_reason": (None if current == "AVAILABLE"
                                             else str(readiness.get(f"{source}_CURRENT", {}).get("reason")
                                                      or f"{source}_CURRENT_DATASET_UNAVAILABLE")),
            "availability_reason": None if historical == "AVAILABLE" else f"{source}_HISTORICAL_DATASET_UNAVAILABLE",
            "supported_timeframes": supported,
            "semantic_terms": _semantic_terms(code, metadata),
        })
    structure_labels = {
        ROLLING_HIGH_BREAKOUT_CONFIRMED: {"en": "Confirmed rolling-high breakout", "zh": "确认突破滚动前高"},
        ROLLING_LOW_BREAKDOWN_CONFIRMED: {"en": "Confirmed rolling-low breakdown", "zh": "确认跌破滚动前低"},
        FAILED_BREAKOUT_CONFIRMED: {"en": "Confirmed failed breakout", "zh": "确认假突破"},
        FAILED_BREAKDOWN_CONFIRMED: {"en": "Confirmed failed breakdown", "zh": "确认假跌破"},
    }
    for code in sorted(STRUCTURE_FEATURES):
        params = {}
        for name, raw in FEATURE_PARAMETER_SCHEMAS[code].items():
            params[name] = {"value_type": raw["type"], "required": raw.get("required", False),
                            "minimum": raw.get("minimum"), "maximum": raw.get("maximum"),
                            "default": raw.get("default")}
        features.append({
            "code": code, "version": ROLLING_STRUCTURE_FEATURE_VERSION,
            "label": structure_labels[code], "group": "PRICE_STRUCTURE",
            "value_type": "boolean", "unit": "boolean", "operators": ["eq"],
            "bounds": {"minimum": None, "maximum": None}, "parameters": params,
            "source_group": "OHLCV", "historical_availability": "AVAILABLE",
            "current_availability": "AVAILABLE", "availability_reason": None,
            "historical_availability_reason": None, "current_availability_reason": None,
            "supported_timeframes": list(SUPPORTED_TIMEFRAMES),
            "semantic_terms": _semantic_terms(code, structure_labels[code]),
        })
    derivative_labels = {
        "OI_CHANGE_PCT": {"en": "Open interest change", "zh": "持仓量变化"},
        "FUNDING_RATE": {"en": "Funding rate", "zh": "资金费率"},
        "FUNDING_RATE_PERCENTILE": {"en": "Funding rate percentile", "zh": "资金费率百分位"},
        "BASIS_PCT": {"en": "Perpetual basis", "zh": "永续合约基差"},
        "BASIS_PERCENTILE": {"en": "Basis percentile", "zh": "基差百分位"},
    }
    existing = {item["code"] for item in features}
    for code, (source, _key, unit) in sorted(DERIVATIVE_FEATURES.items()):
        if code in existing:
            continue
        historical = _availability(str(readiness.get(source, {}).get("status")))
        current = _availability(str(readiness.get(f"{source}_CURRENT", readiness.get(source, {})).get("status")))
        supported = list(readiness.get(source, {}).get("supported_timeframes", SUPPORTED_TIMEFRAMES))
        labels = derivative_labels.get(code, {"en": code, "zh": code})
        features.append({
            "code": code, "version": f"okx-{source.lower()}-pit-v1", "label": labels,
            "group": "DERIVATIVES", "value_type": "number", "unit": unit,
            "operators": ["gt", "gte", "lt", "lte"],
            "bounds": {"minimum": 0 if code.endswith("PERCENTILE") else None,
                       "maximum": 100 if code.endswith("PERCENTILE") else None},
            "parameters": {}, "source_group": source,
            "historical_availability": historical, "current_availability": current,
            "historical_availability_reason": (None if historical == "AVAILABLE"
                                                else str(readiness.get(source, {}).get("reason")
                                                         or f"{source}_HISTORICAL_DATASET_UNAVAILABLE")),
            "current_availability_reason": (None if current == "AVAILABLE"
                                             else str(readiness.get(f"{source}_CURRENT", {}).get("reason")
                                                      or f"{source}_CURRENT_DATASET_UNAVAILABLE")),
            "availability_reason": None if historical == "AVAILABLE" else f"{source}_HISTORICAL_DATASET_UNAVAILABLE",
            "supported_timeframes": supported,
            "semantic_terms": _semantic_terms(code, labels),
        })
    features.sort(key=lambda item: item["code"])
    # These are executable product examples, not labels assembled by the UI.
    # Their wording is covered by parser-contract tests and uses only public,
    # versioned feature semantics.  The client must never infer examples from
    # a preset label because one label can omit another required parameter.
    example_prompts = [
        {"id": "failed-breakdown-reference-v1", "feature": FAILED_BREAKDOWN_CONFIRMED,
         "text": {"en": "BTC 4H failed breakdown of the previous 20 confirmed candles low. What happened over the next 24H historically?",
                  "zh": "BTC 4H 失败跌破参考过去 20 根已确认 K 线的最低点，之后 24H 历史上怎么样？"}},
        {"id": "failed-breakdown-window-v1", "feature": FAILED_BREAKDOWN_CONFIRMED,
         "text": {"en": "BTC 4H breakdown then closes back above the breakdown level within 3 confirmed candles. What happened over the next 24H historically?",
                  "zh": "BTC 4H 跌破后 3 根已确认 K 线内收盘重新回到跌破位上方，之后 24H 历史上怎么样？"}},
        {"id": "failed-breakout-reference-v1", "feature": FAILED_BREAKOUT_CONFIRMED,
         "text": {"en": "BTC 4H failed breakout of the previous 20 confirmed candles high. What happened over the next 24H historically?",
                  "zh": "BTC 4H 失败突破参考过去 20 根已确认 K 线的最高点，之后 24H 历史上怎么样？"}},
    ]
    return {
        "version": CAPABILITIES_VERSION, "thesis_spec_versions": ["thesis-spec-v1", "thesis-spec-v2"],
        "thesis_spec_version": "thesis-spec-v2", "feature_registry_version": FEATURE_REGISTRY_VERSION,
        "expression": {"node_types": ["CONDITION", "ALL", "ANY", "NOT"],
                       "max_depth": 3, "max_leaf_conditions": 10, "max_group_children": 8},
        "instruments": sorted(SUPPORTED_INSTRUMENTS), "timeframes": list(SUPPORTED_TIMEFRAMES),
        "horizons": list(SUPPORTED_HORIZONS), "features": features,
        "example_prompts": example_prompts,
        "semantic_presets": semantic_presets_projection(),
        "conditional_capabilities": {
            "CVD": {"status": "OPTIONAL_UNAVAILABLE", "reason": "CVD_HISTORICAL_NATIVE_SOURCE_UNAVAILABLE"},
            "CANONICAL_LEVEL_FEATURES": {"status": "DEFERRED", "reason": "CANONICAL_LEVEL_POINT_IN_TIME_HISTORY_UNAVAILABLE"},
        },
        "unsupported_concepts": ["NATIVE_CVD_HISTORY", "CANONICAL_LEVEL_POINT_IN_TIME_REPLAY"],
    }


def _leaves(node: ExpressionNode) -> tuple[ConditionNode, ...]:
    if isinstance(node, ConditionNode):
        return (node,)
    if isinstance(node, NotNode):
        return _leaves(node.child)
    output: list[ConditionNode] = []
    for child in node.children:
        output.extend(_leaves(child))
    return tuple(output)


def _condition_key(condition: ConditionNode) -> str:
    return canonical_json(condition.to_dict())


def _causal_observation_series(rows: Sequence[Mapping[str, Any]], *, value_key: str,
                               source_timestamp_key: str, min_history: int,
                               change_pct: bool = False) -> tuple[list[float | None], list[float | None]]:
    """Project one derivative publication onto candles without re-ranking repeats."""
    history: list[float] = []
    values: list[float | None] = []
    percentiles: list[float | None] = []
    last_source_ts: int | None = None
    previous_source_value: float | None = None
    current_value: float | None = None
    current_percentile: float | None = None
    for row in rows:
        raw, raw_ts = row.get(value_key), row.get(source_timestamp_key)
        valid = (isinstance(raw, (int, float)) and not isinstance(raw, bool)
                 and math.isfinite(float(raw)) and isinstance(raw_ts, int)
                 and not isinstance(raw_ts, bool))
        if not valid:
            values.append(None); percentiles.append(None); continue
        source_ts, source_value = int(raw_ts), float(raw)
        if source_ts != last_source_ts:
            current_value = ((source_value / previous_source_value - 1.0) * 100.0
                             if change_pct and previous_source_value not in (None, 0)
                             else None if change_pct else source_value)
            if current_value is not None and len(history) >= min_history:
                less = bisect.bisect_left(history, current_value)
                equal = bisect.bisect_right(history, current_value) - less
                current_percentile = 100.0 * (less + 0.5 * equal) / len(history)
            else:
                current_percentile = None
            if current_value is not None:
                bisect.insort(history, current_value)
            previous_source_value = source_value
            last_source_ts = source_ts
        values.append(current_value)
        percentiles.append(current_percentile)
    return values, percentiles


@dataclass(frozen=True)
class CompiledThesisV2:
    version: str
    instrument: str
    canonical_instrument: str
    timeframe: str
    expression: Mapping[str, Any]
    forward_horizons: tuple[str, ...]
    feature_versions: Mapping[str, str]
    source_requirements: tuple[str, ...]
    independence_policy: Mapping[str, Any]
    event_transition_semantics: str
    definition_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compile_thesis_v2(spec: ThesisSpecV2, registry: Mapping[str, FeatureContractV2]) -> CompiledThesisV2:
    if spec.timeframe == "1D" and any(HORIZON_SECONDS[item] < 86_400 for item in spec.forward_horizons):
        raise ThesisValidationError("1D thesis horizons must be at least 24H")
    leaves = _leaves(spec.expression)
    unavailable = [item.feature for item in leaves
                   if registry[item.feature].historical_availability != "AVAILABLE"]
    if unavailable:
        raise ThesisValidationError(f"required historical feature is unavailable: {sorted(set(unavailable))[0]}")
    versions = {item.feature: registry[item.feature].version for item in leaves}
    sources = tuple(sorted({registry[item.feature].source_group for item in leaves}))
    identity = {
        "version": COMPILED_DEFINITION_VERSION, "instrument": spec.instrument,
        "canonical_instrument": SUPPORTED_INSTRUMENTS[spec.instrument], "timeframe": spec.timeframe,
        "expression": spec.expression.to_dict(), "forward_horizons": list(spec.forward_horizons),
        "feature_versions": dict(sorted(versions.items())), "source_requirements": list(sources),
        "assumptions": [item.to_dict() for item in spec.assumptions],
        "event_transition_semantics": "QUALIFIED_EXPRESSION_NON_TRUE_TO_TRUE_CONFIRMED_CLOSE_V2",
        "independence_policy": {"version": INDEPENDENCE_POLICY_VERSION,
                                "exclude_overlapping_forward_windows": True},
    }
    return CompiledThesisV2(
        COMPILED_DEFINITION_VERSION, spec.instrument, SUPPORTED_INSTRUMENTS[spec.instrument],
        spec.timeframe, spec.expression.to_dict(), spec.forward_horizons,
        dict(sorted(versions.items())), sources, identity["independence_policy"],
        identity["event_transition_semantics"], _hash(identity),
    )


def _validate_source_rows(rows: Sequence[Mapping[str, Any]], timeframe: str, as_of: int) -> list[dict[str, Any]]:
    width = TIMEFRAME_SECONDS[timeframe]
    required = {"ts", "candle_close_ts", "open", "high", "low", "close", "volume", "confirmed"}
    unique: dict[int, dict[str, Any]] = {}
    for source in rows:
        if not required <= set(source) or source.get("confirmed") not in (True, 1):
            raise ThesisValidationError("V2 source requires explicit confirmed OHLCV fields")
        row = dict(source)
        ts, close_ts = row.get("ts"), row.get("candle_close_ts")
        if isinstance(ts, bool) or not isinstance(ts, int) or isinstance(close_ts, bool) or not isinstance(close_ts, int):
            raise ThesisValidationError("source timestamps must be integer Unix seconds")
        if ts % width or close_ts != ts + width or close_ts > as_of:
            raise ThesisValidationError("source timestamp is misaligned or future")
        try:
            values = [float(row[key]) for key in ("open", "high", "low", "close", "volume")]
        except (TypeError, ValueError, OverflowError) as error:
            raise ThesisValidationError("source OHLCV values must be finite") from error
        if not all(math.isfinite(item) for item in values):
            raise ThesisValidationError("source OHLCV values must be finite")
        if ts in unique and canonical_json(unique[ts]) != canonical_json(row):
            raise ThesisValidationError("source contains conflicting duplicate candles")
        unique[ts] = row
    ordered = [unique[key] for key in sorted(unique)]
    if any(int(right["ts"]) - int(left["ts"]) != width for left, right in zip(ordered, ordered[1:])):
        raise ThesisValidationError("source contains a candle gap")
    return confirmed_candles_as_of(ordered, timeframe, as_of)


def _compile_rows(rows: Sequence[Mapping[str, Any]], leaves: Sequence[ConditionNode]) -> list[dict[str, Any]]:
    output = compile_feature_rows(rows)
    structure_cache: dict[str, list[dict[str, Any]]] = {}
    for leaf in leaves:
        key = _condition_key(leaf)
        if leaf.feature in STRUCTURE_FEATURES and key not in structure_cache:
            structure_cache[key] = RollingStructureHistoricalBatchAdapterV1().compile(rows, leaf.parameters)
    oi_change, oi_percentile = _causal_observation_series(
        rows, value_key="open_interest_usd",
        source_timestamp_key="_open_interest_usd_source_ts_ms", min_history=30,
        change_pct=True)
    funding, funding_percentile = _causal_observation_series(
        rows, value_key="funding_rate", source_timestamp_key="_funding_rate_source_ts_ms",
        min_history=30)
    basis, basis_percentile = _causal_observation_series(
        rows, value_key="basis_pct", source_timestamp_key="_basis_pct_source_ts_ms",
        min_history=30)
    for index, row in enumerate(output):
        values: dict[str, Any] = {
            "OI_CHANGE_PCT": oi_change[index], "OI_CHANGE_PERCENTILE": oi_percentile[index],
            "FUNDING_RATE": funding[index], "FUNDING_RATE_PERCENTILE": funding_percentile[index],
            "BASIS_PCT": basis[index], "BASIS_PERCENTILE": basis_percentile[index],
        }
        contexts: dict[str, Any] = {}
        for leaf in leaves:
            key = _condition_key(leaf)
            if leaf.feature in STRUCTURE_FEATURES:
                compiled = structure_cache[key][index]
                values[key] = compiled.get(FEATURE_ROW_KEYS[leaf.feature])
                context = (compiled.get("rolling_structure_event_contexts") or {}).get(leaf.feature)
                if context:
                    contexts[key] = context
            elif leaf.feature in DERIVATIVE_FEATURES:
                values[key] = values.get(leaf.feature)
            elif leaf.feature in FEATURE_REGISTRY:
                values[key] = FEATURE_REGISTRY[leaf.feature].evaluator(row)
            else:
                values[key] = values.get(leaf.feature)
        row["_v2_values"] = values
        row["_v2_contexts"] = contexts
    return output


def _leaf_value(row: Mapping[str, Any], condition: ConditionNode) -> Any:
    return (row.get("_v2_values") or {}).get(_condition_key(condition))


def _tree_result(node: ExpressionNode, row: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(node, ConditionNode):
        actual = _leaf_value(row, node)
        compared = _compare(actual, node.operator, node.value)
        state = TruthValue.UNKNOWN if compared is None else TruthValue.TRUE if compared else TruthValue.FALSE
        return {"node_type": "CONDITION", "state": state.value, "feature": node.feature,
                "operator": node.operator, "value": node.value, "parameters": dict(node.parameters),
                "observed_value": actual}
    if isinstance(node, NotNode):
        child = _tree_result(node.child, row)
        state = {"TRUE": "FALSE", "FALSE": "TRUE", "UNKNOWN": "UNKNOWN"}[child["state"]]
        return {"node_type": "NOT", "state": state, "child": child}
    children = [_tree_result(child, row) for child in node.children]
    states = [TruthValue(child["state"]) for child in children]
    state = evaluate_expression(node, lambda leaf: TruthValue(_tree_result(leaf, row)["state"]))
    return {"node_type": node.node_type, "state": state.value, "children": children,
            "child_states": [item.value for item in states]}


def _all_leaves_known(tree: Mapping[str, Any]) -> bool:
    if tree.get("node_type") == "CONDITION":
        return tree.get("state") != "UNKNOWN"
    if tree.get("node_type") == "NOT":
        return _all_leaves_known(tree["child"])
    return all(_all_leaves_known(item) for item in tree.get("children", []))


class ThesisEventEngineV2:
    def __init__(self, registry: Mapping[str, FeatureContractV2]) -> None:
        self.registry = registry

    def run(self, spec: ThesisSpecV2, source_rows: Iterable[Mapping[str, Any]], *,
            effective_as_of: int | None = None,
            historical_data_selection: Mapping[str, Any] | None = None,
            composite_dataset_identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
        definition = compile_thesis_v2(spec, self.registry)
        as_of = int(effective_as_of if effective_as_of is not None else spec.requested_as_of)
        if as_of > spec.requested_as_of:
            raise ThesisValidationError("effective_as_of must not exceed requested_as_of")
        rows = _validate_source_rows(list(source_rows), spec.timeframe, as_of)
        leaves = _leaves(spec.expression)
        feature_rows = _compile_rows(rows, leaves)
        width = TIMEFRAME_SECONDS[spec.timeframe]
        minimum_history = max(
            [30]
            + [FEATURE_REGISTRY[item.feature].minimum_history
               for item in leaves if item.feature in FEATURE_REGISTRY]
            + [int(item.parameters.get("lookback_bars", 1)) +
               int(item.parameters.get("failure_window_bars", 0))
               for item in leaves if item.feature in STRUCTURE_FEATURES]
        )
        if any(item.feature == "OI_CHANGE_PERCENTILE" for item in leaves):
            minimum_history = max(minimum_history, 31)
        if any(item.feature == "OI_CHANGE_PCT" for item in leaves):
            minimum_history = max(minimum_history, 2)
        start_index = min(len(feature_rows), minimum_history)
        scoped = feature_rows[start_index:]
        trees = [_tree_result(spec.expression, row) for row in scoped]
        qualified = [_all_leaves_known(tree) for tree in trees]
        available_indexes = [index for index, ok in enumerate(qualified) if ok]
        common_start = int(scoped[available_indexes[0]]["candle_close_ts"]) if available_indexes else None
        common_end = int(scoped[available_indexes[-1]]["candle_close_ts"]) if available_indexes else None
        complete_coverage = bool(scoped) and all(qualified)
        feature_coverage = []
        for leaf in sorted({_condition_key(item): item for item in leaves}.values(),
                           key=_condition_key):
            usable = sum(_leaf_value(row, leaf) is not None for row in scoped)
            ratio = usable / len(scoped) if scoped else 0.0
            feature_coverage.append({
                "feature": leaf.feature,
                "qualification": "SUPPORTED" if usable == len(scoped) and scoped else "INSUFFICIENT_COVERAGE",
                "usable_observations": usable, "coverage_ratio": round(ratio, 8),
                "reason": ("qualified point-in-time coverage" if usable == len(scoped) and scoped
                           else "feature contains unavailable point-in-time observations"),
                "stale": False, "partial": usable != len(scoped),
            })
        coverage = {
            "version": COVERAGE_POLICY_VERSION,
            "qualification": "SUPPORTED" if complete_coverage else "THESIS_NOT_TESTABLE_AS_REQUESTED",
            "testable": complete_coverage, "common_start": common_start, "common_end": common_end,
            "reason": None if complete_coverage else "every leaf requires qualified point-in-time coverage",
            "required_source_groups": list(definition.source_requirements),
            "usable_observations": len(available_indexes), "total_observations": len(scoped),
            "features": feature_coverage,
            "testable_subset": [item["feature"] for item in feature_coverage
                                if item["qualification"] == "SUPPORTED"],
        }
        selection = dict(historical_data_selection or {})
        selected_id = selection.get("dataset_id")
        raw_identity = {
            "version": "bounded-thesis-v2-dataset-identity-v1", "instrument": definition.canonical_instrument,
            "timeframe": spec.timeframe, "start": int(rows[0]["candle_close_ts"]) if rows else None,
            "end": int(rows[-1]["candle_close_ts"]) if rows else None, "row_count": len(rows),
            "content_sha256": _hash([{key: row.get(key) for key in
                                      ("ts", "candle_close_ts", "open", "high", "low", "close", "volume",
                                       "open_interest_usd", "funding_rate", "basis_pct")} for row in rows]),
            "selected_dataset_id": selected_id,
        }
        data_identity = dict(composite_dataset_identity or raw_identity)
        base = {
            "result_version": RESULT_VERSION, "status": coverage["qualification"],
            "thesis_spec": spec.to_dict(), "compiled_definition": definition.to_dict(),
            "definition_hash": definition.definition_hash, "engine_version": ENGINE_VERSION,
            "feature_versions": dict(definition.feature_versions), "instrument": spec.instrument,
            "canonical_instrument": definition.canonical_instrument, "timeframe": spec.timeframe,
            "tested_range": {"start": common_start, "end": common_end}, "coverage": coverage,
            "data_identity": data_identity,
            "historical_data": {
                "dataset_id": selected_id or data_identity.get("dataset_id") or data_identity.get("content_sha256"),
                "source_label": selection.get("source_label", "Qualified historical evidence"),
                "source_type": selection.get("source_type", "CANONICAL_STORE"),
                "source_version": selection.get("source_version"),
                "selection_policy_version": selection.get("policy_version"),
                "partition_content_sha256": selection.get("content_sha256", raw_identity["content_sha256"]),
                "immutable_store_sha256": selection.get("immutable_store_sha256"),
                "immutable_store_verification": selection.get("immutable_store_verification"),
                "declared_dataset_id": selection.get("declared_dataset_id"),
                "raw_range": {"start": int(rows[0]["candle_close_ts"]) if rows else None,
                              "end": int(rows[-1]["candle_close_ts"]) if rows else None},
                "evaluable_range": {"start": common_start, "end": common_end},
                "warmup_candles": minimum_history,
                "reduction_reasons": [f"FEATURE_WARMUP:{minimum_history}_CANDLES"],
                "continuity": selection.get("continuity", "VALIDATED_BY_V2_COVERAGE_GATE"),
                "gap_count": selection.get("gap_count", 0),
                "raw_span_days": selection.get("span_days"),
                "span_days": ((common_end - common_start) // 86_400
                              if common_start is not None and common_end is not None else 0),
                "breadth_qualification": selection.get("breadth_qualification", "UNKNOWN"),
                "minimum_research_span_days": int(selection.get("minimum_research_span_seconds", 0)) // 86_400,
                "minimum_research_span_policy_version": selection.get("minimum_research_span_policy_version"),
                "component_identities": data_identity.get("components", []),
            },
            "limitations": ["Historical conditional evidence; not causal proof or a trading signal."],
            "warnings": [],
        }
        if not complete_coverage:
            result = {**base, "raw_candidate_count": 0, "independent_event_count": 0,
                      "excluded_overlap_count": 0, "excluded_events_summary": {},
                      "event_records": [], "aggregates": {}}
            result["result_hash"] = self._result_hash(result)
            return result
        candidates: list[dict[str, Any]] = []
        previous_state: str | None = None
        previous_qualified = False
        for row, tree, row_qualified in zip(scoped, trees, qualified):
            current_state = tree["state"]
            # UNKNOWN caused by missing coverage must never manufacture an event.
            if row_qualified and previous_qualified and current_state == "TRUE" and previous_state != "TRUE":
                event_ts = int(row["candle_close_ts"])
                contexts = list((row.get("_v2_contexts") or {}).values())
                candidates.append({
                    "event_id": _hash({"definition_hash": definition.definition_hash,
                                       "event_timestamp": event_ts}),
                    "timestamp": event_ts, "reference_close": float(row["close"]),
                    "expression_result": tree, "event_context": contexts,
                    "source_timestamps": [event_ts], "exclusion_status": "INCLUDED",
                    "exclusion_reason": None, "outcomes": {},
                })
            previous_state, previous_qualified = current_state, row_qualified
        max_horizon = max(HORIZON_SECONDS[item] for item in spec.forward_horizons)
        independent: list[dict[str, Any]] = []
        last_event: int | None = None
        for event in candidates:
            timestamp = int(event["timestamp"])
            if last_event is not None and timestamp < last_event + max_horizon:
                event["exclusion_status"] = "EXCLUDED"
                event["exclusion_reason"] = "OVERLAPPING_MAX_FORWARD_WINDOW"
                continue
            independent.append(event)
            last_event = timestamp
        by_close = {int(row["candle_close_ts"]): row for row in feature_rows}
        for event in independent:
            reference, event_ts = float(event["reference_close"]), int(event["timestamp"])
            for horizon in spec.forward_horizons:
                end = event_ts + HORIZON_SECONDS[horizon]
                path_times = list(range(event_ts + width, end + 1, width))
                if end % width != event_ts % width or any(timestamp not in by_close for timestamp in path_times):
                    event["outcomes"][horizon] = {"available": False, "censor_reason": "PATH_GAP_OR_TERMINAL_HISTORY",
                                                   "forward_return_fraction": None, "mfe_fraction": None, "mae_fraction": None}
                    continue
                path = [by_close[timestamp] for timestamp in path_times]
                event["outcomes"][horizon] = {
                    "available": True, "censor_reason": None,
                    "forward_return_fraction": float(path[-1]["close"]) / reference - 1,
                    "mfe_fraction": max(float(item["high"]) for item in path) / reference - 1,
                    "mae_fraction": min(float(item["low"]) for item in path) / reference - 1,
                }
        aggregates: dict[str, Any] = {}
        for horizon in spec.forward_horizons:
            available = [item["outcomes"][horizon] for item in independent if item["outcomes"][horizon]["available"]]
            returns = [float(item["forward_return_fraction"]) for item in available]
            mfes = [float(item["mfe_fraction"]) for item in available]
            maes = [float(item["mae_fraction"]) for item in available]
            positive, zero = sum(item > 0 for item in returns), sum(item == 0 for item in returns)
            aggregates[horizon] = {
                "eligible_n": len(returns), "censored_n": len(independent) - len(returns),
                "positive_n": positive, "zero_n": zero,
                "negative_n": len(returns) - positive - zero,
                "historical_positive_rate": positive / len(returns) if returns else None,
                "mean_return_fraction": sum(returns) / len(returns) if returns else None,
                "median_return_fraction": _quantile(returns, .5), "p25_return_fraction": _quantile(returns, .25),
                "p75_return_fraction": _quantile(returns, .75), "min_return_fraction": min(returns) if returns else None,
                "max_return_fraction": max(returns) if returns else None,
                "median_mfe_fraction": _quantile(mfes, .5), "median_mae_fraction": _quantile(maes, .5),
                "sample_quality": _sample_quality(len(returns)), "sample_quality_policy_version": "sample-quality-v1",
            }
        excluded = len(candidates) - len(independent)
        result = {**base, "status": "COMPLETED", "raw_candidate_count": len(candidates),
                  "independent_event_count": len(independent), "excluded_overlap_count": excluded,
                  "excluded_events_summary": {"OVERLAPPING_MAX_FORWARD_WINDOW": excluded} if excluded else {},
                  "event_records": candidates, "aggregates": aggregates}
        result["result_hash"] = self._result_hash(result)
        return result

    @staticmethod
    def _result_hash(result: Mapping[str, Any]) -> str:
        keys = ("result_version", "status", "compiled_definition", "definition_hash", "engine_version",
                "feature_versions", "instrument", "canonical_instrument", "timeframe", "tested_range",
                "coverage", "data_identity", "historical_data", "raw_candidate_count",
                "independent_event_count", "excluded_overlap_count", "excluded_events_summary",
                "event_records", "aggregates")
        return _hash({**{key: result[key] for key in keys}, "outcome_policy_version": OUTCOME_POLICY_VERSION})


class ThesisTestServiceV2:
    """Read-only V2 service with version-separated artifact verification."""

    def __init__(self, reader: Any, *, selection_policy: HistoricalDataSelectionPolicyV1 | None = None,
                 derivative_reader: Any | None = None,
                 capabilities: Mapping[str, Any] | None = None,
                 artifact_cache_size: int = 32) -> None:
        self.reader, self.selection_policy, self.derivative_reader = reader, selection_policy, derivative_reader
        self.capabilities = dict(capabilities or thesis_capabilities_v2())
        self.registry = feature_contracts_from_capabilities(self.capabilities)
        self.engine = ThesisEventEngineV2(self.registry)
        self.artifact_cache_size = max(1, int(artifact_cache_size))
        self._artifacts: OrderedDict[str, tuple[dict[str, Any], tuple[Mapping[str, Any], ...]]] = OrderedDict()
        self._lock = RLock()

    def _spec(self, payload: Mapping[str, Any]) -> ThesisSpecV2:
        try:
            return parse_thesis_spec_v2(payload, self.registry,
                                        supported_instruments=tuple(SUPPORTED_INSTRUMENTS),
                                        supported_timeframes=SUPPORTED_TIMEFRAMES,
                                        supported_horizons=SUPPORTED_HORIZONS)
        except ExpressionValidationError as error:
            raise ThesisValidationError(str(error)) from error

    def test(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        spec = self._spec(payload)
        definition = compile_thesis_v2(spec, self.registry)
        selection: SelectedHistoricalDatasetV1 | None = None
        if self.selection_policy is not None:
            try:
                selection = self.selection_policy.select(definition.canonical_instrument, spec.timeframe,
                                                         spec.requested_as_of, ("OHLCV",))
            except HistoricalDataSelectionError as error:
                raise ThesisValidationError(str(error)) from error
            rows = [dict(item) for item in selection.rows]
            effective_as_of = selection.selection.effective_as_of
            selection_public = selection.selection.public_dict()
        else:
            rows = [dict(item) for item in self.reader.candles(
                definition.canonical_instrument, spec.timeframe, spec.requested_as_of,
                MAX_SOURCE_ROWS_BY_TIMEFRAME[spec.timeframe])]
            effective_as_of, selection_public = spec.requested_as_of, None
        composite = None
        derivative_groups = set(definition.source_requirements) - {"OHLCV"}
        if derivative_groups:
            if self.derivative_reader is None:
                raise ThesisValidationError("required derivative historical dataset is unavailable")
            aligned = self.derivative_reader.align(
                rows, canonical_instrument=definition.canonical_instrument,
                timeframe=spec.timeframe, required_groups=tuple(sorted(derivative_groups)),
                as_of=effective_as_of,
                ohlcv_component=({
                    "kind": "OHLCV", "dataset_id": selection_public.get("dataset_id"),
                    "sha256": selection_public.get("content_sha256"),
                    "raw_start_ms": int(selection_public.get("raw_start", 0)) * 1000,
                    "raw_end_ms": int(selection_public.get("raw_end", 0)) * 1000,
                    "source": selection_public.get("source_name"),
                    "source_version": selection_public.get("source_version"),
                } if selection_public else None),
            )
            rows = list(aligned["rows"])
            composite = aligned.get("composite_dataset_identity")
        result = self.engine.run(spec, rows, effective_as_of=effective_as_of,
                                 historical_data_selection=selection_public,
                                 composite_dataset_identity=composite)
        with self._lock:
            self._artifacts[result["result_hash"]] = (result, tuple(rows))
            self._artifacts.move_to_end(result["result_hash"])
            while len(self._artifacts) > self.artifact_cache_size:
                self._artifacts.popitem(last=False)
        return result

    def verified_result(self, payload: Mapping[str, Any], result_hash: str) -> tuple[dict[str, Any], tuple[Mapping[str, Any], ...]]:
        if not isinstance(result_hash, str) or len(result_hash) != 64:
            raise ThesisValidationError("result_hash must be a SHA-256 identity")
        spec = self._spec(payload)
        expected = compile_thesis_v2(spec, self.registry).definition_hash
        with self._lock:
            cached = self._artifacts.get(result_hash)
            if cached is not None:
                if cached[0]["definition_hash"] != expected:
                    raise ThesisValidationError("result identity does not match the thesis definition")
                return cached
        regenerated = self.test(payload)
        if not hmac.compare_digest(regenerated["result_hash"], result_hash):
            raise ThesisValidationError("result identity no longer matches the selected historical dataset")
        return self._artifacts[result_hash]

    def event_context(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"version", "result_hash", "thesis_spec", "instrument", "timeframe",
                   "event_id", "event_timestamp"}
        if not isinstance(payload, Mapping) or set(payload) != allowed:
            raise ThesisValidationError("V2 event-context request contains unsupported or missing fields")
        spec = payload.get("thesis_spec")
        if not isinstance(spec, Mapping):
            raise ThesisValidationError("thesis_spec must be an object")
        result, rows = self.verified_result(spec, str(payload.get("result_hash", "")))
        if payload.get("instrument") != result["instrument"] or payload.get("timeframe") != result["timeframe"]:
            raise ThesisValidationError("event instrument or timeframe does not match result")
        event_id, timestamp = payload.get("event_id"), payload.get("event_timestamp")
        if not isinstance(event_id, str) or isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ThesisValidationError("event identity is invalid")
        event = next((item for item in result["event_records"]
                      if item["event_id"] == event_id and item["timestamp"] == timestamp
                      and item.get("exclusion_status") == "INCLUDED"), None)
        if event is None:
            raise ThesisValidationError("event is not an included member of this result")
        close_times = [int(row["candle_close_ts"]) for row in rows]
        try:
            event_index = close_times.index(timestamp)
        except ValueError as error:
            raise ThesisValidationError("event candle is absent from the result dataset") from error
        width = TIMEFRAME_SECONDS[result["timeframe"]]
        max_bars = max(HORIZON_SECONDS[item] for item in spec["forward_horizons"]) // width
        start, stop = max(0, event_index - 40), min(len(rows), event_index + max_bars + 5)
        if stop - start > 96:
            start = max(0, stop - 96)
        context_rows = rows[start:stop]
        by_close = {int(row["candle_close_ts"]): index for index, row in enumerate(context_rows)}
        horizons = []
        for horizon in spec["forward_horizons"]:
            target = timestamp + HORIZON_SECONDS[horizon]
            outcome = event["outcomes"].get(horizon) or {}
            absolute = close_times.index(target) if target in close_times else None
            horizons.append({"horizon": horizon, "target_timestamp": target,
                             "candle_index": by_close.get(target),
                             "outcome_close": (float(rows[absolute]["close"])
                                               if outcome.get("available") and absolute is not None else None),
                             **outcome})
        return {
            "version": "thesis-event-context-v2", "context_policy_version": "thesis-event-context-window-v2",
            "result_hash": result["result_hash"], "definition_hash": result["definition_hash"],
            "engine_version": result["engine_version"], "dataset_identity": result["data_identity"],
            "instrument": result["instrument"], "canonical_instrument": result["canonical_instrument"],
            "timeframe": result["timeframe"],
            "event": {"event_id": event_id, "timestamp": timestamp,
                      "candle_index": event_index - start, "reference_close": event["reference_close"],
                      "expression_result": event["expression_result"],
                      "structure_context": event.get("event_context", [])},
            "candles": [{"open_timestamp": int(row["ts"]),
                         "close_timestamp": int(row["candle_close_ts"]),
                         **{key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}}
                        for row in context_rows],
            "horizons": horizons, "row_limit": 96,
        }
