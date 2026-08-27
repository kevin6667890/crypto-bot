from dashboard.thesis_event_engine_v2 import ThesisEventEngineV2, feature_contracts_from_capabilities, thesis_capabilities_v2
from dashboard.thesis_expression import ConditionNode, SequenceNodeV1, ThesisSpecV2
from dashboard.thesis_parser_v3 import ThesisParserServiceV3


class _NoProvider:
    def generate(self, _request):
        raise AssertionError("V3.1 smoke grammar must not call the provider")


def test_sequence_smoke_is_ready_and_partial_language_needs_input():
    service = ThesisParserServiceV3(_NoProvider(), thesis_capabilities_v2())
    sequence = service.parse("BTC 4H 假突破以后重新站回 MA200，24H 后一般怎样？", requested_as_of=1_700_000_000)
    assert sequence.status == "READY_WITH_ASSUMPTIONS"
    assert sequence.expression.to_dict()["node_type"] == "SEQUENCE"
    assert sequence.expression.to_dict()["max_gap_bars"] == 10
    partial = service.parse("ETH 1H RSI 超卖或者价格远离 MA200，同时 OI 没有明显下降。", requested_as_of=1_700_000_000)
    assert partial.status == "NEEDS_INPUT"
    assert partial.expression is None  # an incomplete boolean expression is never runnable as RSI-only
    assert {item.parameter for item in partial.missing_parameters} == {"distance_threshold_pct", "maximum_oi_decline_pct", "forward_horizons"}


def test_breakout_followed_by_24h_is_a_single_event_not_a_sequence():
    result = ThesisParserServiceV3(_NoProvider(), thesis_capabilities_v2()).parse(
        "BTC 4H \u7a81\u7834\u524d\u9ad8\u4e4b\u540e24H\u901a\u5e38\u600e\u6837\uff1f", requested_as_of=1_700_000_000)
    assert result.status == "READY_WITH_ASSUMPTIONS"
    assert result.expression.to_dict()["node_type"] == "CONDITION"
    assert result.expression.feature == "ROLLING_HIGH_BREAKOUT_CONFIRMED"
    assert result.thesis_spec.forward_horizons == ("24H",)


def test_sequence_event_is_final_step_only_and_window_is_causal():
    registry = feature_contracts_from_capabilities(thesis_capabilities_v2())
    start, width = 1_700_006_400, 14_400
    rows = []
    for index in range(240):
        close = 90.0 if index in (200, 201) else 110.0 if index >= 202 else 100.0
        rows.append({"ts": start + index * width - width, "candle_close_ts": start + index * width,
                     "open": close, "high": close + 1, "low": close - 1, "close": close,
                     "volume": 100, "confirmed": True})
    expression = SequenceNodeV1((ConditionNode("PRICE_BELOW_MA200", "eq", True),
                                 ConditionNode("PRICE_ABOVE_MA200", "eq", True)), 3)
    result = ThesisEventEngineV2(registry).run(ThesisSpecV2("BTC", "4H", expression, ("24H",), rows[-1]["candle_close_ts"]), rows)
    assert result["raw_candidate_count"] == 1
    event = result["event_records"][0]
    assert event["timestamp"] == start + 202 * width
    assert event["source_timestamps"] == [start + 200 * width, start + 202 * width]
