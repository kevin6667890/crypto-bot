from __future__ import annotations

import json

from dashboard.thesis_event_engine_v2 import ThesisTestServiceV2, thesis_capabilities_v2
from dashboard.thesis_parser_v3 import ThesisParserServiceV3
from dashboard.thesis_tracking import ThesisTrackingRepositoryV1
from dashboard.thesis_tracking_v2 import CurrentExpressionEvaluatorV2, ThesisTrackingServiceV2


WIDTH = 14_400
BASE = 1_700_006_400


def _candles(count: int, *, current: bool) -> list[dict]:
    rows = []
    for index in range(count):
        close = 100.0 + (index % 25) * 0.1
        if index % 90 == 70:
            close += 8.0
        rows.append({
            "ts": BASE + index * WIDTH, "candle_close_ts": BASE + (index + 1) * WIDTH,
            "open": close - 0.1, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": 1_000.0 if index % 90 == 70 else 100.0,
            "confirmed": 1, "source": "fixture", "source_version": "fixture-v1",
            "_source_store": "market_candles" if current else "historical_candles",
        })
    return rows


class Reader:
    def __init__(self, rows): self.rows = rows
    def candles(self, _instrument, _timeframe, as_of, limit):
        return [dict(row) for row in self.rows if row["candle_close_ts"] <= as_of][-limit:]


class Provider:
    def generate(self, _request):
        return json.dumps({
            "detected_language": "en", "instrument": "BTC", "timeframe": "4H",
            "forward_horizons": ["24H"],
            "expression": {"node_type": "ALL", "children": [
                {"node_type": "CONDITION", "feature": "ROLLING_HIGH_BREAKOUT_CONFIRMED",
                 "operator": "eq", "value": True, "parameters": {"lookback_bars": 20}},
                {"node_type": "CONDITION", "feature": "VOLUME_PERCENTILE",
                 "operator": "gte", "value": 90, "parameters": {}},
            ]},
            "recognized_clauses": ["breaks the previous 20 candle high",
                                   "volume percentile at least 90"],
            "assumptions": [], "unsupported_clauses": [],
            "missing_parameters": [], "warnings": [],
        })


def test_nl_to_ast_history_chart_context_and_track_current_evidence(tmp_path):
    historical, current = _candles(1_200, current=False), _candles(1_200, current=True)
    as_of = historical[-1]["candle_close_ts"]
    capabilities = thesis_capabilities_v2()
    parsed = ThesisParserServiceV3(Provider(), capabilities).parse(
        "BTC 4H breaks the previous 20 candle high and volume percentile at least 90",
        requested_as_of=as_of)
    assert parsed.status == "READY" and parsed.thesis_spec is not None
    assert parsed.expression.node_type == "ALL"

    historical_service = ThesisTestServiceV2(Reader(historical), capabilities=capabilities)
    result = historical_service.test(parsed.thesis_spec.to_dict())
    assert result["status"] == "COMPLETED" and result["independent_event_count"] > 0
    included = next(item for item in result["event_records"]
                    if item["exclusion_status"] == "INCLUDED")
    context = historical_service.event_context({
        "version": "thesis-event-context-request-v2", "result_hash": result["result_hash"],
        "thesis_spec": result["thesis_spec"], "instrument": "BTC", "timeframe": "4H",
        "event_id": included["event_id"], "event_timestamp": included["timestamp"],
    })
    assert context["event"]["structure_context"][0]["reference_level"] > 0
    assert context["candles"]

    repository = ThesisTrackingRepositoryV1(tmp_path / "tracking.sqlite")
    evaluator = CurrentExpressionEvaluatorV2(
        Reader(current), historical_service.registry, clock=lambda: as_of + 1)
    tracking = ThesisTrackingServiceV2(
        repository, evaluator, historical_service,
        trackable_features={item["code"] for item in capabilities["features"]
                            if item["current_availability"] == "AVAILABLE"})
    created = tracking.create({
        "version": "track-thesis-request-v2", "result_hash": result["result_hash"],
        "thesis_spec": result["thesis_spec"], "language": "en",
    })
    assert created["track"]["schema_version"] == "tracked-thesis-v2"
    assert created["latest_evaluation"]["tree_result"]["node_type"] == "ALL"
    assert created["track"]["historical_dataset_identity"] != \
        created["latest_evaluation"]["current_dataset_identity"]["dataset_id"]
