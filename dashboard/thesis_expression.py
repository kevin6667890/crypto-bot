"""Closed, deterministic ThesisSpec V2 expression domain.

This module deliberately contains no statistics, persistence, network, or model
code.  Untrusted parser output must pass through :func:`parse_expression` and
the supplied closed feature registry before it can become executable domain
data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import math
from typing import Any, Callable, Mapping, Sequence, TypeAlias

try:
    from signal_identity import canonical_json
except ImportError:
    from .signal_identity import canonical_json


THESIS_SPEC_V2_VERSION = "thesis-spec-v2"
EXPRESSION_VERSION = "thesis-expression-v2"
EXPRESSION_LIMITS_VERSION = "thesis-expression-limits-v1"
FEATURE_CONTRACTS_VERSION = "thesis-feature-contracts-v2"
LEGACY_V1_ADAPTER_VERSION = "legacy-v1-expression-adapter-v1"
SEMANTIC_PRESET_REGISTRY_VERSION = "semantic-preset-registry-v1"
MAX_EXPRESSION_DEPTH = 3
MAX_LEAF_CONDITIONS = 10
MAX_GROUP_CHILDREN = 8


class ExpressionValidationError(ValueError):
    """Safe validation error for an untrusted expression."""


class TruthValue(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ParameterContract:
    value_type: str
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    allowed_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeatureContractV2:
    code: str
    version: str
    value_type: str
    operators: tuple[str, ...]
    source_group: str
    parameters: Mapping[str, ParameterContract] = field(default_factory=dict)
    supported_timeframes: tuple[str, ...] = ("1H", "4H")
    historical_availability: str = "AVAILABLE"
    current_availability: str = "AVAILABLE"
    minimum_value: float | None = None
    maximum_value: float | None = None


@dataclass(frozen=True)
class ConditionNode:
    feature: str
    operator: str
    value: bool | float
    parameters: Mapping[str, Any] = field(default_factory=dict)
    node_type: str = field(default="CONDITION", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"node_type": self.node_type, "feature": self.feature,
                "operator": self.operator, "value": self.value,
                "parameters": dict(self.parameters)}


@dataclass(frozen=True)
class AllNode:
    children: tuple["ExpressionNode", ...]
    node_type: str = field(default="ALL", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"node_type": self.node_type, "children": [item.to_dict() for item in self.children]}


@dataclass(frozen=True)
class AnyNode:
    children: tuple["ExpressionNode", ...]
    node_type: str = field(default="ANY", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"node_type": self.node_type, "children": [item.to_dict() for item in self.children]}


@dataclass(frozen=True)
class NotNode:
    child: "ExpressionNode"
    node_type: str = field(default="NOT", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"node_type": self.node_type, "child": self.child.to_dict()}


ExpressionNode: TypeAlias = ConditionNode | AllNode | AnyNode | NotNode


@dataclass(frozen=True)
class PresetAssumptionV1:
    preset_id: str
    preset_version: str
    source_text: str
    feature: str
    applied: Mapping[str, Any]
    label: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticPresetV1:
    preset_id: str
    feature: str
    operator: str
    value: bool | float
    parameters: Mapping[str, Any]
    phrases: Mapping[str, tuple[str, ...]]
    label: Mapping[str, str]
    version: str = SEMANTIC_PRESET_REGISTRY_VERSION

    def assumption(self, source_text: str) -> PresetAssumptionV1:
        return PresetAssumptionV1(
            self.preset_id, self.version, source_text, self.feature,
            {"operator": self.operator, "value": self.value,
             "parameters": dict(self.parameters)}, self.label)


# Values are product semantics, not model defaults.  They are versioned,
# returned by capabilities, recorded in a spec, and therefore hash-visible.
SEMANTIC_PRESETS: Mapping[str, SemanticPresetV1] = {
    "previous-high-standard": SemanticPresetV1(
        "previous-high-standard", "ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True,
        {"lookback_bars": 20},
        {"en": ("previous high",), "zh": ("前高",)},
        {"en": "Previous 20 confirmed candles high",
         "zh": "过去 20 根已确认 K 线的最高点"}),
    "previous-low-standard": SemanticPresetV1(
        "previous-low-standard", "ROLLING_LOW_BREAKDOWN_CONFIRMED", "eq", True,
        {"lookback_bars": 20},
        {"en": ("previous low",), "zh": ("前低",)},
        {"en": "Previous 20 confirmed candles low",
         "zh": "过去 20 根已确认 K 线的最低点"}),
    "volume-surge-percentile": SemanticPresetV1(
        "volume-surge-percentile", "VOLUME_PERCENTILE", "gte", 90.0, {},
        {"en": ("volume surge", "significant volume"), "zh": ("明显放量", "成交量大幅增加")},
        {"en": "Volume historical percentile at least 90",
         "zh": "成交量历史百分位至少为 90"}),
    "oi-surge-percentile": SemanticPresetV1(
        "oi-surge-percentile", "OI_CHANGE_PERCENTILE", "gte", 90.0, {},
        {"en": ("OI surge", "open interest surge"), "zh": ("OI大幅增加", "持仓量大幅增加")},
        {"en": "OI change historical percentile at least 90",
         "zh": "OI 变化历史百分位至少为 90"}),
    "rsi-overbought": SemanticPresetV1(
        "rsi-overbought", "RSI", "gte", 70.0, {},
        {"en": ("RSI overbought",), "zh": ("RSI超买",)},
        {"en": "RSI at least 70", "zh": "RSI 至少为 70"}),
    "rsi-oversold": SemanticPresetV1(
        "rsi-oversold", "RSI", "lte", 30.0, {},
        {"en": ("RSI oversold",), "zh": ("RSI超卖",)},
        {"en": "RSI at most 30", "zh": "RSI 不高于 30"}),
    "failed-breakout-standard": SemanticPresetV1(
        "failed-breakout-standard", "FAILED_BREAKOUT_CONFIRMED", "eq", True,
        {"lookback_bars": 20, "failure_window_bars": 3},
        {"en": ("failed breakout", "false breakout"), "zh": ("假突破", "失败突破")},
        {"en": "Close returns below the breakout level within 3 confirmed candles",
         "zh": "突破后 3 根已确认 K 线内收盘重新跌回突破位"}),
    "failed-breakout-lookback-standard": SemanticPresetV1(
        "failed-breakout-lookback-standard", "FAILED_BREAKOUT_CONFIRMED", "eq", True,
        {"lookback_bars": 20},
        {"en": ("previous high",), "zh": ("前高",)},
        {"en": "Failure reference uses the previous 20 confirmed candles high",
         "zh": "失败突破参考过去 20 根已确认 K 线的最高点"}),
    "failed-breakdown-standard": SemanticPresetV1(
        "failed-breakdown-standard", "FAILED_BREAKDOWN_CONFIRMED", "eq", True,
        {"lookback_bars": 20, "failure_window_bars": 3},
        {"en": ("failed breakdown", "false breakdown"), "zh": ("假跌破", "失败跌破")},
        {"en": "Close returns above the breakdown level within 3 confirmed candles",
         "zh": "跌破后 3 根已确认 K 线内收盘重新回到跌破位上方"}),
    "failed-breakdown-lookback-standard": SemanticPresetV1(
        "failed-breakdown-lookback-standard", "FAILED_BREAKDOWN_CONFIRMED", "eq", True,
        {"lookback_bars": 20},
        {"en": ("previous low",), "zh": ("前低",)},
        {"en": "Failure reference uses the previous 20 confirmed candles low",
         "zh": "失败跌破参考过去 20 根已确认 K 线的最低点"}),
}


def semantic_presets_projection() -> dict[str, Any]:
    return {"version": SEMANTIC_PRESET_REGISTRY_VERSION,
            "presets": [asdict(SEMANTIC_PRESETS[key]) for key in sorted(SEMANTIC_PRESETS)]}


def feature_contracts_from_capabilities(capabilities: Mapping[str, Any]) -> dict[str, FeatureContractV2]:
    """Build the only parser/expression vocabulary from backend capabilities."""
    features = capabilities.get("features")
    if not isinstance(features, Sequence) or isinstance(features, (str, bytes)):
        raise ExpressionValidationError("capabilities.features must be an array")
    output: dict[str, FeatureContractV2] = {}
    for raw in features:
        if not isinstance(raw, Mapping):
            raise ExpressionValidationError("capability feature must be an object")
        code = str(raw.get("code", ""))
        if not code or code in output:
            raise ExpressionValidationError("capability feature code must be unique and non-empty")
        parameters: dict[str, ParameterContract] = {}
        raw_params = raw.get("parameters", {})
        if not isinstance(raw_params, Mapping):
            raise ExpressionValidationError(f"parameters for {code} must be an object")
        for name, parameter in raw_params.items():
            if not isinstance(parameter, Mapping):
                raise ExpressionValidationError(f"parameter {name} for {code} must be an object")
            parameters[str(name)] = ParameterContract(
                value_type=str(parameter.get("value_type", "number")),
                required=bool(parameter.get("required", False)),
                minimum=parameter.get("minimum"), maximum=parameter.get("maximum"),
                allowed_values=tuple(map(str, parameter.get("allowed_values", ()))),
            )
        output[code] = FeatureContractV2(
            code=code, version=str(raw.get("version", capabilities.get("feature_registry_version", "unknown"))),
            value_type=str(raw.get("value_type", "number")),
            operators=tuple(map(str, raw.get("operators", ()))),
            source_group=str(raw.get("source_group", "UNKNOWN")), parameters=parameters,
            supported_timeframes=tuple(map(str, raw.get("supported_timeframes", ("1H", "4H")))),
            historical_availability=str(raw.get("historical_availability", raw.get("availability", "AVAILABLE"))),
            current_availability=str(raw.get("current_availability", raw.get("availability", "AVAILABLE"))),
            minimum_value=(raw.get("bounds") or {}).get("minimum"),
            maximum_value=(raw.get("bounds") or {}).get("maximum"),
        )
    return output


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ExpressionValidationError(f"{name} must be a finite number")
    normalized = float(value)
    return 0.0 if normalized == 0 else normalized


def _validate_parameter(name: str, value: Any, contract: ParameterContract) -> Any:
    if contract.value_type == "integer":
        number = _finite_number(value, name)
        if not number.is_integer():
            raise ExpressionValidationError(f"parameter {name} must be an integer")
        result: Any = int(number)
    elif contract.value_type == "number":
        result = _finite_number(value, name)
    elif contract.value_type == "boolean":
        if not isinstance(value, bool):
            raise ExpressionValidationError(f"parameter {name} must be boolean")
        result = value
    elif contract.value_type == "string":
        if not isinstance(value, str):
            raise ExpressionValidationError(f"parameter {name} must be a string")
        result = value
    else:
        raise ExpressionValidationError(f"parameter {name} has unsupported type")
    if isinstance(result, (int, float)) and not isinstance(result, bool):
        if contract.minimum is not None and result < contract.minimum:
            raise ExpressionValidationError(f"parameter {name} must be >= {contract.minimum}")
        if contract.maximum is not None and result > contract.maximum:
            raise ExpressionValidationError(f"parameter {name} must be <= {contract.maximum}")
    if contract.allowed_values and str(result) not in contract.allowed_values:
        raise ExpressionValidationError(f"parameter {name} is not allowed")
    return result


def _parse_condition(raw: Mapping[str, Any], registry: Mapping[str, FeatureContractV2]) -> ConditionNode:
    if set(raw) - {"node_type", "feature", "operator", "value", "parameters"}:
        raise ExpressionValidationError("CONDITION contains unsupported fields")
    feature, operator = str(raw.get("feature", "")), str(raw.get("operator", ""))
    contract = registry.get(feature)
    if contract is None:
        raise ExpressionValidationError(f"unsupported feature: {feature or '<empty>'}")
    if operator not in contract.operators:
        raise ExpressionValidationError(f"invalid operator {operator} for {feature}")
    value = raw.get("value")
    if contract.value_type == "boolean":
        if not isinstance(value, bool):
            raise ExpressionValidationError(f"{feature} requires a boolean value")
        normalized_value: bool | float = value
    elif contract.value_type == "number":
        normalized_value = _finite_number(value, f"value for {feature}")
        if contract.minimum_value is not None and normalized_value < contract.minimum_value:
            raise ExpressionValidationError(f"value for {feature} must be >= {contract.minimum_value}")
        if contract.maximum_value is not None and normalized_value > contract.maximum_value:
            raise ExpressionValidationError(f"value for {feature} must be <= {contract.maximum_value}")
    else:
        raise ExpressionValidationError(f"unsupported value type for {feature}")
    raw_parameters = raw.get("parameters", {})
    if not isinstance(raw_parameters, Mapping):
        raise ExpressionValidationError("condition parameters must be an object")
    unknown = set(raw_parameters) - set(contract.parameters)
    if unknown:
        raise ExpressionValidationError(f"unsupported parameter for {feature}: {sorted(unknown)[0]}")
    missing = [name for name, definition in contract.parameters.items()
               if definition.required and name not in raw_parameters]
    if missing:
        raise ExpressionValidationError(f"missing parameter for {feature}: {missing[0]}")
    parameters = {name: _validate_parameter(name, raw_parameters[name], contract.parameters[name])
                  for name in sorted(raw_parameters)}
    return ConditionNode(feature, operator, normalized_value, parameters)


def parse_expression(raw: Mapping[str, Any], registry: Mapping[str, FeatureContractV2],
                     *, depth: int = 1) -> ExpressionNode:
    if not isinstance(raw, Mapping):
        raise ExpressionValidationError("expression node must be an object")
    if depth > MAX_EXPRESSION_DEPTH:
        raise ExpressionValidationError(f"expression depth exceeds {MAX_EXPRESSION_DEPTH}")
    node_type = raw.get("node_type")
    if node_type == "CONDITION":
        node = _parse_condition(raw, registry)
    elif node_type in {"ALL", "ANY"}:
        if set(raw) != {"node_type", "children"}:
            raise ExpressionValidationError(f"{node_type} must contain only node_type and children")
        children = raw.get("children")
        if not isinstance(children, list) or not 2 <= len(children) <= MAX_GROUP_CHILDREN:
            raise ExpressionValidationError(f"{node_type} children must contain 2 to {MAX_GROUP_CHILDREN} nodes")
        parsed = tuple(parse_expression(item, registry, depth=depth + 1) for item in children)
        node = AllNode(parsed) if node_type == "ALL" else AnyNode(parsed)
    elif node_type == "NOT":
        if set(raw) != {"node_type", "child"}:
            raise ExpressionValidationError("NOT must contain only node_type and child")
        node = NotNode(parse_expression(raw.get("child"), registry, depth=depth + 1))
    else:
        raise ExpressionValidationError(f"unsupported expression node: {node_type}")
    if expression_leaf_count(node) > MAX_LEAF_CONDITIONS:
        raise ExpressionValidationError(f"expression leaf count exceeds {MAX_LEAF_CONDITIONS}")
    return canonicalize_expression(node)


def expression_leaf_count(node: ExpressionNode) -> int:
    if isinstance(node, ConditionNode):
        return 1
    if isinstance(node, NotNode):
        return expression_leaf_count(node.child)
    return sum(expression_leaf_count(item) for item in node.children)


def canonicalize_expression(node: ExpressionNode) -> ExpressionNode:
    """Normalize safely equivalent forms and make commutative hashes stable."""
    if isinstance(node, ConditionNode):
        return ConditionNode(node.feature, node.operator, node.value,
                             {key: node.parameters[key] for key in sorted(node.parameters)})
    if isinstance(node, NotNode):
        child = canonicalize_expression(node.child)
        if isinstance(child, NotNode):
            return canonicalize_expression(child.child)
        if isinstance(child, ConditionNode):
            inverse = {"gt": "lte", "gte": "lt", "lt": "gte", "lte": "gt"}.get(child.operator)
            if inverse:
                return ConditionNode(child.feature, inverse, child.value, child.parameters)
            if child.operator == "eq" and isinstance(child.value, bool):
                return ConditionNode(child.feature, "eq", not child.value, child.parameters)
        return NotNode(child)
    children = tuple(canonicalize_expression(item) for item in node.children)
    children = tuple(sorted(children, key=lambda item: canonical_json(item.to_dict())))
    return AllNode(children) if isinstance(node, AllNode) else AnyNode(children)


def expression_hash(node: ExpressionNode) -> str:
    payload = {"version": EXPRESSION_VERSION, "expression": canonicalize_expression(node).to_dict()}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def evaluate_expression(node: ExpressionNode,
                        evaluate_leaf: Callable[[ConditionNode], TruthValue]) -> TruthValue:
    if isinstance(node, ConditionNode):
        result = evaluate_leaf(node)
        if not isinstance(result, TruthValue):
            raise TypeError("leaf evaluator must return TruthValue")
        return result
    if isinstance(node, NotNode):
        return {TruthValue.TRUE: TruthValue.FALSE, TruthValue.FALSE: TruthValue.TRUE,
                TruthValue.UNKNOWN: TruthValue.UNKNOWN}[evaluate_expression(node.child, evaluate_leaf)]
    results = tuple(evaluate_expression(item, evaluate_leaf) for item in node.children)
    if isinstance(node, AllNode):
        if TruthValue.FALSE in results:
            return TruthValue.FALSE
        return TruthValue.TRUE if all(item is TruthValue.TRUE for item in results) else TruthValue.UNKNOWN
    if TruthValue.TRUE in results:
        return TruthValue.TRUE
    return TruthValue.FALSE if all(item is TruthValue.FALSE for item in results) else TruthValue.UNKNOWN


@dataclass(frozen=True)
class ThesisSpecV2:
    instrument: str
    timeframe: str
    expression: ExpressionNode
    forward_horizons: tuple[str, ...]
    requested_as_of: int
    assumptions: tuple[PresetAssumptionV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    version: str = THESIS_SPEC_V2_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "instrument": self.instrument,
                "timeframe": self.timeframe, "expression": self.expression.to_dict(),
                "forward_horizons": list(self.forward_horizons),
                "requested_as_of": self.requested_as_of,
                "assumptions": [item.to_dict() for item in self.assumptions],
                "metadata": dict(self.metadata)}

    @property
    def definition_hash(self) -> str:
        identity = {"version": self.version, "instrument": self.instrument,
                    "timeframe": self.timeframe, "expression": self.expression.to_dict(),
                    "forward_horizons": list(self.forward_horizons),
                    "assumptions": [item.to_dict() for item in self.assumptions]}
        return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def parse_thesis_spec_v2(payload: Mapping[str, Any], registry: Mapping[str, FeatureContractV2],
                         *, supported_instruments: Sequence[str],
                         supported_timeframes: Sequence[str],
                         supported_horizons: Sequence[str]) -> ThesisSpecV2:
    if not isinstance(payload, Mapping):
        raise ExpressionValidationError("thesis spec must be an object")
    allowed = {"version", "instrument", "timeframe", "expression", "forward_horizons",
               "requested_as_of", "assumptions", "metadata"}
    if set(payload) - allowed:
        raise ExpressionValidationError("thesis spec contains unsupported fields")
    if payload.get("version") != THESIS_SPEC_V2_VERSION:
        raise ExpressionValidationError(f"version must be {THESIS_SPEC_V2_VERSION}")
    instrument = str(payload.get("instrument", "")).upper()
    raw_timeframe = str(payload.get("timeframe", ""))
    timeframe = {str(item).upper(): str(item) for item in supported_timeframes}.get(
        raw_timeframe.upper(), raw_timeframe)
    if instrument not in supported_instruments:
        raise ExpressionValidationError(f"unsupported instrument: {instrument or '<empty>'}")
    if timeframe not in supported_timeframes:
        raise ExpressionValidationError(f"unsupported timeframe: {timeframe or '<empty>'}")
    expression = parse_expression(payload.get("expression"), registry)
    # Every leaf must support the selected timeframe.
    def check_timeframe(node: ExpressionNode) -> None:
        if isinstance(node, ConditionNode):
            if timeframe not in registry[node.feature].supported_timeframes:
                raise ExpressionValidationError(f"feature {node.feature} does not support {timeframe}")
        elif isinstance(node, NotNode):
            check_timeframe(node.child)
        else:
            for child in node.children:
                check_timeframe(child)
    check_timeframe(expression)
    raw_horizons = payload.get("forward_horizons")
    if not isinstance(raw_horizons, list) or not raw_horizons:
        raise ExpressionValidationError("forward_horizons must be a non-empty array")
    if any(str(item) not in supported_horizons for item in raw_horizons):
        raise ExpressionValidationError("forward_horizons contains unsupported value")
    horizons = tuple(dict.fromkeys(map(str, raw_horizons)))
    requested_as_of = payload.get("requested_as_of")
    if isinstance(requested_as_of, bool) or not isinstance(requested_as_of, int) or requested_as_of <= 0:
        raise ExpressionValidationError("requested_as_of must be a positive Unix timestamp")
    raw_assumptions = payload.get("assumptions", [])
    if not isinstance(raw_assumptions, list):
        raise ExpressionValidationError("assumptions must be an array")
    assumptions: list[PresetAssumptionV1] = []
    for raw in raw_assumptions:
        if not isinstance(raw, Mapping):
            raise ExpressionValidationError("assumption must be an object")
        preset = SEMANTIC_PRESETS.get(str(raw.get("preset_id", "")))
        if preset is None or raw.get("preset_version") != preset.version:
            raise ExpressionValidationError("assumption references an unknown preset version")
        expected = preset.assumption(str(raw.get("source_text", "")))
        if canonical_json(raw) != canonical_json(expected.to_dict()):
            raise ExpressionValidationError("assumption does not match the preset registry")
        assumptions.append(expected)
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ExpressionValidationError("metadata must be an object")
    try:
        canonical_json(metadata)
    except (TypeError, ValueError) as error:
        raise ExpressionValidationError("metadata must be finite JSON data") from error
    return ThesisSpecV2(instrument, timeframe, expression, horizons, requested_as_of,
                        tuple(assumptions), dict(metadata))


def adapt_v1_expression(spec: Any) -> ThesisSpecV2:
    """Explicit evaluation adapter; never mutates or re-hashes stored V1 data."""
    conditions = tuple(ConditionNode(item.feature, item.operator, item.value, {})
                       for item in spec.required_conditions)
    expression: ExpressionNode = conditions[0] if len(conditions) == 1 else AllNode(conditions)
    metadata = {"adapter_version": LEGACY_V1_ADAPTER_VERSION,
                "legacy_spec_version": spec.version,
                "compatibility_only": True}
    return ThesisSpecV2(spec.instrument, spec.timeframe, canonicalize_expression(expression),
                        tuple(spec.forward_horizons), int(spec.requested_as_of), (), metadata)
