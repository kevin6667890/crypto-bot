from __future__ import annotations

import json

import pytest

from dashboard.thesis_parser_v3 import (
    ThesisParserServiceV3, ThesisParserV3Error, parser_context, validate_provider_output,
)
from tests.test_thesis_expression_v2 import CAPABILITIES, condition


def output(expression, **overrides):
    value = {
        "detected_language": "en", "instrument": "BTC", "timeframe": "4H",
        "forward_horizons": ["24H"], "expression": expression,
        "recognized_clauses": ["RSI between 40 and 60"], "assumptions": [],
        "unsupported_clauses": [], "missing_parameters": [], "warnings": [],
    }
    value.update(overrides)
    return value


def test_context_is_built_from_capabilities_and_exposes_presets():
    context = parser_context(CAPABILITIES)
    assert {item["code"] for item in context["features"]} == {
        "RSI", "VOLUME_PERCENTILE", "ROLLING_HIGH_BREAKOUT_CONFIRMED"}
    assert context["semantic_presets"]["version"] == "semantic-preset-registry-v1"
    assert "OI_CHANGE_PERCENTILE" not in {item["code"] for item in context["features"]}


def test_between_compiles_to_inclusive_all():
    text = "BTC 4H RSI between 40 and 60"
    raw = output({"node_type": "CONDITION", "feature": "RSI",
                  "operator": "between", "value": [40, 60], "parameters": {}})
    result = validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)
    assert result.status == "READY"
    assert result.expression.node_type == "ALL"
    assert {(item.operator, item.value) for item in result.expression.children} == {("gte", 40.0), ("lte", 60.0)}


def test_or_and_negated_comparison_survive_validation():
    text = "BTC 4H RSI above 70 or RSI not above 80"
    raw = output({"node_type": "ANY", "children": [condition("RSI", "gt", 70),
        {"node_type": "NOT", "child": condition("RSI", "gt", 80)}]},
        recognized_clauses=["RSI above 70", "RSI not above 80"])
    result = validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)
    assert result.status == "READY"
    assert result.expression.node_type == "ANY"
    assert {item.operator for item in result.expression.children} == {"gt", "lte"}


def test_visible_preset_yields_ready_with_assumptions():
    text = "BTC 4H breaks the previous high"
    raw = output(condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 20}),
                 recognized_clauses=["breaks the previous high"],
                 assumptions=[{"preset_id": "previous-high-standard", "source_text": "previous high"}])
    result = validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)
    assert result.status == "READY_WITH_ASSUMPTIONS"
    assert result.thesis_spec is not None
    assert result.thesis_spec.assumptions[0].applied["parameters"] == {"lookback_bars": 20}


def test_explicit_number_cannot_be_overridden_by_preset():
    text = "BTC 4H breakout over the previous 50 candles high"
    raw = output(condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 20}),
                 recognized_clauses=["previous 50 candles high"],
                 assumptions=[{"preset_id": "previous-high-standard", "source_text": "previous 50 candles high"}])
    with pytest.raises(ThesisParserV3Error):
        validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)


def test_preset_metadata_must_exactly_match_the_expression():
    text = "BTC 4H breaks the previous high"
    raw = output(condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 21}),
                 recognized_clauses=["breaks the previous high"],
                 assumptions=[{"preset_id": "previous-high-standard", "source_text": "previous high"}])
    with pytest.raises(ThesisParserV3Error, match="does not match"):
        validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)


def test_unsupported_clause_is_never_silently_dropped_or_executable():
    text = "BTC 4H RSI above 70 and whales are accumulating"
    raw = output(condition("RSI", "gt", 70), recognized_clauses=["RSI above 70"],
                 unsupported_clauses=[{"source_text": "whales are accumulating",
                    "reason_code": "WHALE_ACCUMULATION_NOT_OBSERVABLE",
                    "category": "SEMANTIC_UNSUPPORTED", "suggestions": ["VOLUME_PERCENTILE"]}])
    result = validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)
    assert result.status == "PARTIALLY_SUPPORTED"
    assert result.expression is not None
    assert result.thesis_spec is None


def test_provider_cannot_silently_drop_an_unknown_clause():
    text = "BTC 4H RSI above 70 and whales are accumulating"
    raw = output(condition("RSI", "gt", 70), recognized_clauses=["RSI above 70"])
    with pytest.raises(ThesisParserV3Error, match="unaccounted clause"):
        validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)


def test_empty_accounting_cannot_substitute_unknown_semantics():
    text = "鲸鱼正在吸筹"
    raw = output(condition("RSI", "gte", 70), recognized_clauses=[])
    with pytest.raises(ThesisParserV3Error, match="unaccounted clause"):
        validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)


def test_explicit_or_cannot_drift_to_all():
    text = "BTC 4H RSI above 70 or volume percentile above 90"
    raw = output({"node_type": "ALL", "children": [
        condition("RSI", "gt", 70), condition("VOLUME_PERCENTILE", "gte", 90),
    ]}, recognized_clauses=["RSI above 70", "volume percentile above 90"])
    with pytest.raises(ThesisParserV3Error, match="explicit OR"):
        validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)


def test_threshold_must_be_explicit_or_exactly_preset_backed():
    text = "BTC 4H 明显放量"
    invented = output(condition("VOLUME_PERCENTILE", "gte", 91),
                      recognized_clauses=["明显放量"])
    with pytest.raises(ThesisParserV3Error, match="ungrounded|not grounded"):
        validate_provider_output(text, invented, CAPABILITIES, requested_as_of=1_700_000_000)
    explicit = output(condition("VOLUME_PERCENTILE", "gte", 95),
                      recognized_clauses=["成交量百分位至少95"])
    result = validate_provider_output("BTC 4H 成交量百分位至少95", explicit, CAPABILITIES,
                                      requested_as_of=1_700_000_000)
    assert result.status == "READY"


def test_explicit_operator_cannot_be_weakened_or_strengthened():
    text = "BTC 4H RSI at least 95"
    raw = output(condition("RSI", "gt", 95),
                 recognized_clauses=["RSI at least 95"])
    with pytest.raises(ThesisParserV3Error, match="operator for RSI"):
        validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)


def test_historical_data_availability_is_part_of_parse_status():
    unavailable = {**CAPABILITIES, "features": [
        {**item, "historical_availability": "DATASET_UNAVAILABLE"}
        if item["code"] == "VOLUME_PERCENTILE" else item
        for item in CAPABILITIES["features"]
    ]}
    text = "BTC 4H volume percentile at least 90"
    raw = output(condition("VOLUME_PERCENTILE", "gte", 90),
                 recognized_clauses=["volume percentile at least 90"])
    result = validate_provider_output(text, raw, unavailable, requested_as_of=1_700_000_000)
    assert result.status == "UNSUPPORTED"
    assert result.thesis_spec is None
    assert result.unsupported_clauses[0].category == "DATASET_UNAVAILABLE"


def test_unknown_feature_and_parameter_fail_closed():
    text = "BTC 4H CVD confirms"
    with pytest.raises(Exception, match="unsupported feature"):
        validate_provider_output(text, output(condition("CVD", "eq", True),
            recognized_clauses=["CVD confirms"]), CAPABILITIES, requested_as_of=1_700_000_000)
    text = "BTC 4H RSI above 70"
    with pytest.raises(Exception, match="unsupported parameter"):
        validate_provider_output(text, output(condition("RSI", "gt", 70, {"period": 14}),
            recognized_clauses=["RSI above 70"]), CAPABILITIES, requested_as_of=1_700_000_000)


class Provider:
    def __init__(self, response):
        self.response = response
        self.request = None

    def generate(self, request):
        self.request = request
        return json.dumps(self.response)


def test_service_uses_capability_context_and_separates_untrusted_text():
    text = "BTC 4H RSI between 40 and 60"
    provider = Provider(output({"node_type": "CONDITION", "feature": "RSI",
                                "operator": "between", "value": [40, 60], "parameters": {}}))
    result = ThesisParserServiceV3(provider, CAPABILITIES).parse(text, requested_as_of=1_700_000_000)
    assert result.status == "READY"
    assert json.loads(provider.request["messages"][1]["content"]) == {"untrusted_text": text}
    system_message = provider.request["messages"][0]["content"]
    assert system_message.startswith("Return only one JSON object. ")
    system = json.loads(system_message.removeprefix("Return only one JSON object. "))
    assert all(item["code"] != "CVD" for item in system["features"])


@pytest.mark.parametrize("text", [
    "BTC 4H RSI超过70后怎么样", "BTC 4H RSI不高于80", "RSI在40到60之间",
    "BTC 4H突破过去20根K线高点", "BTC 4H突破前高", "BTC 4H OI大幅增加",
    "BTC 4H RSI超过70或者成交量大幅增加", "BTC 4H RSI above 70",
    "BTC 4H RSI not above 80", "BTC 4H RSI between 40 and 60",
    "BTC 4H breakout above the previous 20 candle high", "BTC 4H previous high breakout",
    "BTC 4H volume percentile at least 95", "BTC 4H significant volume",
    "BTC 4H open interest surge", "BTC 4H OI percentile above 90",
    "ETH 1H RSI below 30", "SOL 4H RSI above 70", "BTC 4H RSI at least 50",
    "BTC 4H RSI at most 80", "BTC 4H RSI above 40 and RSI below 60",
    "BTC 4H RSI above 70 or volume percentile above 90", "BTC 4H not RSI above 80",
    "BTC 4H either RSI above 70 or volume percentile above 90", "BTC 4H 前高突破并明显放量",
    "BTC 4H 突破过去50根K线最高点", "BTC 4H 成交量百分位至少95",
    "BTC 4H OI变化百分位至少90", "BTC 4H RSI超买", "BTC 4H RSI超卖",
    "BTC 4H 假突破", "BTC 4H 突破前高后三根K线内跌回来",
    "BTC 4H failed breakout", "BTC 4H false breakout within 3 candles",
    "BTC 4H 突破前高同时OI大幅增加或明显放量但RSI不高于80",
    "BTC 4H breakout and either OI surge or volume surge while RSI is not above 80",
    "BTC看起来很强", "鲸鱼正在吸筹", "做市商操纵", "链上聪明钱进场",
    "market makers are manipulating BTC", "smart money is entering on-chain",
    "whales are accumulating BTC", "BTC sentiment is euphoric", "BTC liquidation cascade",
    "BTC CVD confirms price", "BTC 4H funding is bullish", "BTC 4H support looks strong",
    "BTC 4H resistance may break", "BTC 4H volume is kind of large",
])
def test_bilingual_real_prompt_corpus_is_safe_to_embed_as_untrusted_data(text):
    # The corpus guards the model boundary: no prompt can alter the closed
    # capability context, and user text is always isolated as JSON data.
    from dashboard.thesis_parser_v3 import provider_request
    request = provider_request(text, CAPABILITIES)
    assert json.loads(request["messages"][1]["content"])["untrusted_text"] == text
    assert "CVD" not in {item["code"] for item in
                         json.loads(request["messages"][0]["content"].removeprefix(
                             "Return only one JSON object. "))["features"]}


def test_provider_cannot_substitute_whale_claim_with_breakout_preset():
    text = "whales are accumulating BTC"
    raw = output(
        condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 20}),
        recognized_clauses=["whales are accumulating"],
        assumptions=[{"preset_id": "previous-high-standard",
                      "source_text": "whales are accumulating"}],
    )
    with pytest.raises(ThesisParserV3Error, match="registered preset phrase"):
        validate_provider_output(text, raw, CAPABILITIES, requested_as_of=1_700_000_000)

    mixed = "whales are accumulating at previous high"
    mixed_raw = output(
        condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 20}),
        recognized_clauses=[mixed],
        assumptions=[{"preset_id": "previous-high-standard", "source_text": "previous high"}],
    )
    with pytest.raises(ThesisParserV3Error, match="ungrounded semantic text"):
        validate_provider_output(mixed, mixed_raw, CAPABILITIES,
                                 requested_as_of=1_700_000_000)


def test_related_volume_features_cannot_substitute_for_each_other():
    expanded = {**CAPABILITIES, "features": [*CAPABILITIES["features"], {
        "code": "VOLUME_RATIO", "value_type": "number", "operators": ["gte", "lte"],
        "source_group": "OHLCV", "supported_timeframes": ["4H"],
        "semantic_terms": {"en": ["volume ratio", "volume multiple"]},
    }]}
    wrong_percentile = output(condition("VOLUME_PERCENTILE", "gte", 1.2),
                              recognized_clauses=["volume ratio at least 1.2"])
    with pytest.raises(ThesisParserV3Error, match="not grounded"):
        validate_provider_output("volume ratio at least 1.2", wrong_percentile, expanded,
                                 requested_as_of=1_700_000_000)
    wrong_ratio = output(condition("VOLUME_RATIO", "gte", 90),
                         recognized_clauses=["volume percentile at least 90"])
    with pytest.raises(ThesisParserV3Error, match="not grounded"):
        validate_provider_output("volume percentile at least 90", wrong_ratio, expanded,
                                 requested_as_of=1_700_000_000)


def test_boolean_price_above_ma_remains_v1_compatible_in_parser_v3():
    expanded = {**CAPABILITIES, "features": [*CAPABILITIES["features"], {
        "code": "PRICE_ABOVE_MA200", "value_type": "boolean", "operators": ["eq"],
        "source_group": "OHLCV", "supported_timeframes": ["4H"],
        "semantic_terms": {"en": ["price above ma200"]},
    }]}
    positive = output(condition("PRICE_ABOVE_MA200", "eq", True),
                      recognized_clauses=["price above ma200"])
    assert validate_provider_output("BTC 4H price above ma200", positive, expanded,
                                    requested_as_of=1_700_000_000).status == "READY"
    negative = output(condition("PRICE_ABOVE_MA200", "eq", False),
                      recognized_clauses=["not price above ma200"])
    assert validate_provider_output("BTC 4H not price above ma200", negative, expanded,
                                    requested_as_of=1_700_000_000).status == "READY"
    wrong_polarity = output(condition("PRICE_ABOVE_MA200", "eq", True),
                            recognized_clauses=["not price above ma200"])
    with pytest.raises(ThesisParserV3Error, match="boolean grouping"):
        validate_provider_output("BTC 4H not price above ma200", wrong_polarity, expanded,
                                 requested_as_of=1_700_000_000)


def test_general_leaf_and_group_not_are_grounded_and_executable():
    leaf_text = "BTC 4H not RSI at least 70"
    leaf_raw = output({"node_type": "NOT", "child": condition("RSI", "gte", 70)},
                      recognized_clauses=["not RSI at least 70"])
    leaf_result = validate_provider_output(leaf_text, leaf_raw, CAPABILITIES,
                                           requested_as_of=1_700_000_000)
    assert leaf_result.status == "READY"
    assert leaf_result.expression.operator == "lt"

    group_text = "BTC 4H not (RSI above 70 or volume percentile at least 90)"
    group_raw = output({"node_type": "NOT", "child": {"node_type": "ANY", "children": [
        condition("RSI", "gt", 70), condition("VOLUME_PERCENTILE", "gte", 90),
    ]}}, recognized_clauses=["RSI above 70", "volume percentile at least 90"])
    group_result = validate_provider_output(group_text, group_raw, CAPABILITIES,
                                            requested_as_of=1_700_000_000)
    assert group_result.status == "READY"
    assert group_result.expression.node_type == "NOT"

    wrong_group = output({"node_type": "ANY", "children": [
        condition("RSI", "gt", 70), condition("VOLUME_PERCENTILE", "gte", 90),
    ]}, recognized_clauses=["RSI above 70", "volume percentile at least 90"])
    with pytest.raises(ThesisParserV3Error, match="boolean grouping"):
        validate_provider_output(group_text, wrong_group, CAPABILITIES,
                                 requested_as_of=1_700_000_000)


def test_consecutive_not_uses_deterministic_even_odd_parity():
    expanded = {**CAPABILITIES, "features": [*CAPABILITIES["features"], {
        "code": "PRICE_ABOVE_MA200", "value_type": "boolean", "operators": ["eq"],
        "source_group": "OHLCV", "supported_timeframes": ["4H"],
        "semantic_terms": {"en": ["price above ma200"]},
    }]}
    double_text = "BTC 4H not not price above ma200"
    correct = output(condition("PRICE_ABOVE_MA200", "eq", True),
                     recognized_clauses=["not not price above ma200"])
    assert validate_provider_output(double_text, correct, expanded,
                                    requested_as_of=1_700_000_000).status == "READY"
    wrong = output(condition("PRICE_ABOVE_MA200", "eq", False),
                   recognized_clauses=["not not price above ma200"])
    with pytest.raises(ThesisParserV3Error, match="boolean grouping"):
        validate_provider_output(double_text, wrong, expanded,
                                 requested_as_of=1_700_000_000)

    group_text = "not not (RSI above 70 or volume percentile at least 90)"
    group = {"node_type": "ANY", "children": [
        condition("RSI", "gt", 70), condition("VOLUME_PERCENTILE", "gte", 90)]}
    assert validate_provider_output(group_text, output(group, recognized_clauses=[
        "RSI above 70", "volume percentile at least 90"]), CAPABILITIES,
        requested_as_of=1_700_000_000).status == "READY"


def test_derivative_value_and_percentile_semantics_are_distinct():
    expanded = {**CAPABILITIES, "features": [*CAPABILITIES["features"],
        {"code": "FUNDING_RATE", "value_type": "number", "operators": ["gte"],
         "source_group": "FUNDING", "supported_timeframes": ["4H"],
         "semantic_terms": {"en": ["funding rate"]}},
        {"code": "FUNDING_RATE_PERCENTILE", "value_type": "number", "operators": ["gte"],
         "source_group": "FUNDING", "supported_timeframes": ["4H"],
         "semantic_terms": {"en": ["funding rate percentile", "funding percentile"]}},
        {"code": "BASIS_PCT", "value_type": "number", "operators": ["gte"],
         "source_group": "BASIS", "supported_timeframes": ["4H"],
         "semantic_terms": {"en": ["basis"]}},
        {"code": "BASIS_PERCENTILE", "value_type": "number", "operators": ["gte"],
         "source_group": "BASIS", "supported_timeframes": ["4H"],
         "semantic_terms": {"en": ["basis percentile"]}},
    ]}
    cases = [
        ("funding rate at least 0.01", "FUNDING_RATE_PERCENTILE", 0.01),
        ("basis at least 0.02", "BASIS_PERCENTILE", 0.02),
    ]
    for text, feature, value in cases:
        raw = output(condition(feature, "gte", value), recognized_clauses=[text])
        with pytest.raises(ThesisParserV3Error, match="not grounded"):
            validate_provider_output(text, raw, expanded, requested_as_of=1_700_000_000)


def test_mixed_logic_grouping_must_match_source_precedence():
    text = "RSI above 70 or volume percentile at least 90 and breaks the previous 20 candles high"
    wrong = output({"node_type": "ANY", "children": [
        {"node_type": "ALL", "children": [condition("RSI", "gt", 70),
                                              condition("VOLUME_PERCENTILE", "gte", 90)]},
        condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 20}),
    ]}, recognized_clauses=["RSI above 70", "volume percentile at least 90",
                           "breaks the previous 20 candles high"])
    with pytest.raises(ThesisParserV3Error, match="boolean grouping"):
        validate_provider_output(text, wrong, CAPABILITIES, requested_as_of=1_700_000_000)


def test_numbers_cannot_move_between_feature_clauses():
    text = "breaks the previous 20 candles high and volume percentile at least 90"
    swapped = output({"node_type": "ALL", "children": [
        condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 90}),
        condition("VOLUME_PERCENTILE", "gte", 20),
    ]}, recognized_clauses=["breaks the previous 20 candles high",
                           "volume percentile at least 90"])
    with pytest.raises(ThesisParserV3Error, match="source clause"):
        validate_provider_output(text, swapped, CAPABILITIES, requested_as_of=1_700_000_000)


def test_compound_breakout_or_not_grouping_is_accepted_when_exactly_grounded():
    capabilities = {**CAPABILITIES, "features": [*CAPABILITIES["features"], {
        "code": "OI_CHANGE_PERCENTILE", "value_type": "number",
        "operators": ["gte", "lte"], "source_group": "OI",
        "supported_timeframes": ["4H"],
        "semantic_terms": {"en": ["oi surge", "open interest surge"]},
    }]}
    text = ("breaks previous high, OI surge or volume surge, "
            "but RSI not above 80")
    expression = {"node_type": "ALL", "children": [
        condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True,
                  {"lookback_bars": 20}),
        {"node_type": "ANY", "children": [
            condition("OI_CHANGE_PERCENTILE", "gte", 90),
            condition("VOLUME_PERCENTILE", "gte", 90),
        ]},
        condition("RSI", "lte", 80),
    ]}
    raw = output(expression, recognized_clauses=[
        "breaks previous high", "OI surge", "volume surge", "RSI not above 80"],
        assumptions=[
            {"preset_id": "previous-high-standard", "source_text": "previous high"},
            {"preset_id": "oi-surge-percentile", "source_text": "OI surge"},
            {"preset_id": "volume-surge-percentile", "source_text": "volume surge"},
        ])
    result = validate_provider_output(text, raw, capabilities,
                                      requested_as_of=1_700_000_000)
    assert result.status == "READY_WITH_ASSUMPTIONS"
    assert result.expression.node_type == "ALL"


def test_parenthesized_group_and_between_or_cannot_drift():
    text = "RSI above 70 and (volume percentile at least 90 or RSI below 30)"
    correct = output({"node_type": "ALL", "children": [condition("RSI", "gt", 70),
        {"node_type": "ANY", "children": [condition("VOLUME_PERCENTILE", "gte", 90),
                                             condition("RSI", "lt", 30)]}]},
        recognized_clauses=["RSI above 70", "volume percentile at least 90", "RSI below 30"])
    assert validate_provider_output(text, correct, CAPABILITIES,
                                    requested_as_of=1_700_000_000).status == "READY"
    wrong = output({"node_type": "ANY", "children": [
        {"node_type": "ALL", "children": [condition("RSI", "gt", 70),
                                              condition("VOLUME_PERCENTILE", "gte", 90)]},
        condition("RSI", "lt", 30)]},
        recognized_clauses=["RSI above 70", "volume percentile at least 90", "RSI below 30"])
    with pytest.raises(ThesisParserV3Error, match="boolean grouping"):
        validate_provider_output(text, wrong, CAPABILITIES, requested_as_of=1_700_000_000)

    fullwidth = text.replace("(", "（").replace(")", "）")
    assert validate_provider_output(fullwidth, correct, CAPABILITIES,
                                    requested_as_of=1_700_000_000).status == "READY"
    with pytest.raises(ThesisParserV3Error, match="boolean grouping"):
        validate_provider_output(fullwidth, wrong, CAPABILITIES,
                                 requested_as_of=1_700_000_000)

    between_text = "RSI between 40 and 60 or volume percentile at least 90"
    between_wrong = output({"node_type": "ALL", "children": [
        {"node_type": "ANY", "children": [condition("RSI", "gte", 40),
                                             condition("VOLUME_PERCENTILE", "gte", 90)]},
        condition("RSI", "lte", 60)]},
        recognized_clauses=["RSI between 40 and 60", "volume percentile at least 90"])
    with pytest.raises(ThesisParserV3Error, match="boolean grouping"):
        validate_provider_output(between_text, between_wrong, CAPABILITIES,
                                 requested_as_of=1_700_000_000)
