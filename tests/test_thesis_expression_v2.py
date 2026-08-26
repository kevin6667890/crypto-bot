from __future__ import annotations

import copy

import pytest

from dashboard.thesis_event_engine import ConditionV1, ThesisSpecV1
from dashboard.thesis_expression import (
    AllNode, AnyNode, ConditionNode, ExpressionValidationError, NotNode, TruthValue,
    adapt_v1_expression, evaluate_expression, expression_hash,
    feature_contracts_from_capabilities, parse_expression, parse_thesis_spec_v2,
    semantic_presets_projection,
)


CAPABILITIES = {
    "feature_registry_version": "test-registry-v2",
    "instruments": ["BTC", "ETH", "SOL"],
    "timeframes": ["1H", "4H", "1D"],
    "horizons": ["4H", "12H", "24H", "3D"],
    "semantic_presets": semantic_presets_projection(),
    "features": [
        {"code": "RSI", "value_type": "number", "operators": ["gt", "gte", "lt", "lte"],
         "bounds": {"minimum": 0, "maximum": 100},
         "semantic_terms": {"en": ["rsi"]},
         "source_group": "OHLCV", "supported_timeframes": ["1H", "4H", "1D"]},
        {"code": "VOLUME_PERCENTILE", "value_type": "number", "operators": ["gte", "lte"],
         "semantic_terms": {"en": ["volume percentile", "volume surge"],
                            "zh": ["成交量百分位", "明显放量", "放量", "百分位"]},
         "source_group": "OHLCV", "supported_timeframes": ["1H", "4H"]},
        {"code": "ROLLING_HIGH_BREAKOUT_CONFIRMED", "value_type": "boolean", "operators": ["eq"],
         "semantic_terms": {"en": ["breakout", "breaks above", "breaks the previous",
                                    "breaks previous", "previous high"]},
         "source_group": "OHLCV", "supported_timeframes": ["1H", "4H", "1D"],
         "parameters": {"lookback_bars": {"value_type": "integer", "required": True,
                                                "minimum": 5, "maximum": 500}}},
    ],
}


@pytest.fixture
def registry():
    return feature_contracts_from_capabilities(CAPABILITIES)


def condition(feature="RSI", operator="gte", value=70, parameters=None):
    return {"node_type": "CONDITION", "feature": feature, "operator": operator,
            "value": value, "parameters": parameters or {}}


@pytest.mark.parametrize(
    ("node", "values", "expected"),
    [
        (AllNode((ConditionNode("A", "eq", True), ConditionNode("B", "eq", True))),
         {"A": TruthValue.TRUE, "B": TruthValue.TRUE}, TruthValue.TRUE),
        (AllNode((ConditionNode("A", "eq", True), ConditionNode("B", "eq", True))),
         {"A": TruthValue.UNKNOWN, "B": TruthValue.TRUE}, TruthValue.UNKNOWN),
        (AllNode((ConditionNode("A", "eq", True), ConditionNode("B", "eq", True))),
         {"A": TruthValue.UNKNOWN, "B": TruthValue.FALSE}, TruthValue.FALSE),
        (AnyNode((ConditionNode("A", "eq", True), ConditionNode("B", "eq", True))),
         {"A": TruthValue.FALSE, "B": TruthValue.FALSE}, TruthValue.FALSE),
        (AnyNode((ConditionNode("A", "eq", True), ConditionNode("B", "eq", True))),
         {"A": TruthValue.UNKNOWN, "B": TruthValue.FALSE}, TruthValue.UNKNOWN),
        (AnyNode((ConditionNode("A", "eq", True), ConditionNode("B", "eq", True))),
         {"A": TruthValue.UNKNOWN, "B": TruthValue.TRUE}, TruthValue.TRUE),
        (NotNode(ConditionNode("A", "eq", True)), {"A": TruthValue.UNKNOWN}, TruthValue.UNKNOWN),
        (NotNode(ConditionNode("A", "eq", True)), {"A": TruthValue.TRUE}, TruthValue.FALSE),
    ],
)
def test_three_valued_logic(node, values, expected):
    assert evaluate_expression(node, lambda leaf: values[leaf.feature]) is expected


def test_parse_nested_expression_and_canonical_hash_is_order_independent(registry):
    left = {"node_type": "ALL", "children": [
        condition(),
        {"node_type": "ANY", "children": [
            condition("VOLUME_PERCENTILE", "gte", 90),
            condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 20}),
        ]},
    ]}
    right = copy.deepcopy(left)
    right["children"].reverse()
    right["children"][0]["children"].reverse()
    assert expression_hash(parse_expression(left, registry)) == expression_hash(parse_expression(right, registry))


def test_not_comparison_is_deterministically_normalized(registry):
    parsed = parse_expression({"node_type": "NOT", "child": condition("RSI", "gt", 80)}, registry)
    assert parsed == ConditionNode("RSI", "lte", 80.0, {})


@pytest.mark.parametrize("lookback", [4, 501, 20.5, True])
def test_parameter_contract_rejects_out_of_bounds_or_non_integer(registry, lookback):
    with pytest.raises(ExpressionValidationError):
        parse_expression(condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True,
                                   {"lookback_bars": lookback}), registry)


def test_parameter_contract_rejects_missing_and_arbitrary_keys(registry):
    with pytest.raises(ExpressionValidationError, match="missing parameter"):
        parse_expression(condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True), registry)
    with pytest.raises(ExpressionValidationError, match="unsupported parameter"):
        parse_expression(condition("RSI", "gte", 70, {"period": 14}), registry)


def test_feature_value_bounds_are_enforced(registry):
    with pytest.raises(ExpressionValidationError, match="must be <= 100"):
        parse_expression(condition("RSI", "gte", 101), registry)


def test_depth_leaf_and_group_limits_are_enforced(registry):
    too_deep = {"node_type": "NOT", "child": {"node_type": "NOT", "child": {
        "node_type": "NOT", "child": condition()}}}
    with pytest.raises(ExpressionValidationError, match="depth"):
        parse_expression(too_deep, registry)
    with pytest.raises(ExpressionValidationError, match="children"):
        parse_expression({"node_type": "ALL", "children": [condition()]}, registry)
    eleven = {"node_type": "ALL", "children": [
        {"node_type": "ANY", "children": [condition("RSI", "gte", 10 + i),
                                             condition("RSI", "lte", 90 - i)]}
        for i in range(6)
    ]}
    with pytest.raises(ExpressionValidationError, match="leaf count"):
        parse_expression(eleven, registry)


def test_spec_definition_hash_includes_visible_preset_but_not_run_timestamp(registry):
    expression = condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 20})
    from dashboard.thesis_expression import SEMANTIC_PRESETS
    assumption = SEMANTIC_PRESETS["previous-high-standard"].assumption("前高").to_dict()
    payload = {"version": "thesis-spec-v2", "instrument": "BTC", "timeframe": "4H",
               "expression": expression, "forward_horizons": ["24H"], "requested_as_of": 100,
               "assumptions": [assumption], "metadata": {"parser": "test"}}
    first = parse_thesis_spec_v2(payload, registry, supported_instruments=CAPABILITIES["instruments"],
                                 supported_timeframes=CAPABILITIES["timeframes"],
                                 supported_horizons=CAPABILITIES["horizons"])
    payload["requested_as_of"] = 200
    second = parse_thesis_spec_v2(payload, registry, supported_instruments=CAPABILITIES["instruments"],
                                  supported_timeframes=CAPABILITIES["timeframes"],
                                  supported_horizons=CAPABILITIES["horizons"])
    assert first.definition_hash == second.definition_hash
    changed = copy.deepcopy(payload)
    changed["assumptions"][0]["applied"]["parameters"]["lookback_bars"] = 21
    with pytest.raises(ExpressionValidationError, match="does not match"):
        parse_thesis_spec_v2(changed, registry, supported_instruments=CAPABILITIES["instruments"],
                             supported_timeframes=CAPABILITIES["timeframes"],
                             supported_horizons=CAPABILITIES["horizons"])


def test_explicit_v1_adapter_preserves_v1_and_marks_compatibility():
    original = ThesisSpecV1("thesis-spec-v1", "BTC", "4H",
                            (ConditionV1("RSI", "gte", 70.0), ConditionV1("VOLUME_RATIO", "gte", 1.2)),
                            requested_as_of=1_700_000_000)
    before = original.to_dict()
    adapted = adapt_v1_expression(original)
    assert original.to_dict() == before
    assert adapted.version == "thesis-spec-v2"
    assert adapted.metadata["adapter_version"] == "legacy-v1-expression-adapter-v1"
    assert isinstance(adapted.expression, AllNode)


def test_lowercase_minute_timeframe_keeps_canonical_capability_spelling():
    capabilities = copy.deepcopy(CAPABILITIES)
    capabilities["features"][0]["supported_timeframes"].append("15m")
    registry = feature_contracts_from_capabilities(capabilities)
    payload = {"version": "thesis-spec-v2", "instrument": "BTC", "timeframe": "15m",
               "expression": condition("RSI", "gte", 70), "forward_horizons": ["4H"],
               "requested_as_of": 100, "assumptions": [], "metadata": {}}
    parsed = parse_thesis_spec_v2(payload, registry, supported_instruments=("BTC",),
                                  supported_timeframes=("15m", "1H", "4H", "1D"),
                                  supported_horizons=("4H",))
    assert parsed.timeframe == "15m"
