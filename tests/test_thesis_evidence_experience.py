from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

import dashboard.thesis_event_engine as thesis_event_engine
import dashboard.thesis_historical_data as thesis_historical_data
from dashboard.market_context_v2 import BoundedMarketDataReaderV2
from dashboard.thesis_evidence_explanation import (
    EVIDENCE_PLAN_VERSION, EvidenceExplanationError,
    ThesisEvidenceExplanationServiceV1, build_evidence_fact_set,
)
from dashboard.thesis_event_engine import ThesisTestServiceV1, ThesisValidationError
from dashboard.thesis_historical_data import (
    HistoricalDataSelectionError, HistoricalDataSelectionPolicyV1, HistoricalStoreV1,
)


WIDTH = 14_400
BASE = 1_700_006_400


def make_db(path, count: int, *, source="OKX historical", version="v1", gap_at=None, market=False):
    with sqlite3.connect(path) as connection:
        if market:
            connection.execute("CREATE TABLE market_candles(instrument TEXT,bar TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL)")
            table = "market_candles"
        else:
            connection.execute("""CREATE TABLE historical_candles(
                instrument TEXT,timeframe TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,
                volume REAL,confirmed INTEGER,source TEXT,source_version TEXT)""")
            table = "historical_candles"
        for index in range(count):
            ts = BASE + index * WIDTH + (WIDTH if gap_at is not None and index >= gap_at else 0)
            close = 100 + index * .03
            volume = 300 if index >= 205 and index % 10 == 5 else 100
            values = ("BTC-USDT", "4H", ts, close - .1, close + 1, close - 1, close, volume)
            if table == "market_candles":
                connection.execute("INSERT INTO market_candles VALUES(?,?,?,?,?,?,?,?)", values)
            else:
                connection.execute("INSERT INTO historical_candles VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (*values, 1, source, version))


def body(count=280):
    return {"version": "thesis-spec-v1", "instrument": "BTC", "timeframe": "4H",
            "required_conditions": [
                {"feature": "VOLUME_RATIO", "operator": "gte", "value": 1.2},
                {"feature": "PRICE_ABOVE_MA200", "operator": "eq", "value": True},
            ], "optional_conditions": [], "forward_horizons": ["4H", "12H", "24H"],
            "requested_as_of": BASE + count * WIDTH}


def service(tmp_path, count=280):
    frozen, recent = tmp_path / "frozen.db", tmp_path / "recent.db"
    make_db(frozen, count)
    make_db(recent, 80, market=True)
    policy = HistoricalDataSelectionPolicyV1([
        HistoricalStoreV1(recent, "current_canonical", 100),
        HistoricalStoreV1(frozen, "frozen_research", 0),
    ])
    return ThesisTestServiceV1(BoundedMarketDataReaderV2(recent), selection_policy=policy), frozen, recent


def test_long_history_wins_and_recent_store_cannot_override_it(tmp_path):
    svc, _frozen, _recent = service(tmp_path)
    result = svc.test(body())
    assert result["historical_data"]["source_type"] == "FROZEN_CANONICAL"
    assert result["historical_data"]["raw_range"]["start"] == BASE + WIDTH
    assert result["historical_data"]["raw_range"]["end"] == BASE + 280 * WIDTH
    assert result["historical_data"]["evaluable_range"]["start"] == BASE + 200 * WIDTH
    assert result["historical_data"]["reduction_reasons"] == ["FEATURE_WARMUP:PRICE_ABOVE_MA200:200_CANDLES"]
    assert result["status"] == "COMPLETED"


def test_policy_never_stitches_sources_and_rejects_gapped_partition(tmp_path):
    mixed = tmp_path / "mixed.db"
    make_db(mixed, 100, source="A", version="one")
    with sqlite3.connect(mixed) as connection:
        for index in range(100, 180):
            ts, close = BASE + index * WIDTH, 100 + index * .03
            connection.execute("INSERT INTO historical_candles VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               ("BTC-USDT", "4H", ts, close-.1, close+1, close-1, close, 100, 1, "B", "two"))
    selected = HistoricalDataSelectionPolicyV1([HistoricalStoreV1(mixed, "frozen_research", 0)]).select(
        "BTC-USDT", "4H", BASE + 180 * WIDTH, ["OHLCV"])
    assert selected.selection.source_name == "A"
    assert selected.selection.row_count == 100
    assert {row["source"] for row in selected.rows} == {"A"}
    gap = tmp_path / "gap.db"
    make_db(gap, 100, gap_at=50)
    with pytest.raises(HistoricalDataSelectionError, match="gap"):
        HistoricalDataSelectionPolicyV1([HistoricalStoreV1(gap, "frozen_research", 0)]).select(
            "BTC-USDT", "4H", BASE + 101 * WIDTH, ["OHLCV"])


def test_selection_is_deterministic_and_policy_version_changes_identity(tmp_path):
    frozen = tmp_path / "frozen.db"; make_db(frozen, 80)
    first_policy = HistoricalDataSelectionPolicyV1([HistoricalStoreV1(frozen, "frozen_research", 0)])
    first = first_policy.select("BTC-USDT", "4H", BASE + 80 * WIDTH, ["OHLCV"])
    assert first.selection.dataset_id == first_policy.select("BTC-USDT", "4H", BASE + 80 * WIDTH, ["OHLCV"]).selection.dataset_id
    class NextPolicy(HistoricalDataSelectionPolicyV1):
        version = "historical-data-selection-policy-v2-test"
    second = NextPolicy([HistoricalStoreV1(frozen, "frozen_research", 0)]).select(
        "BTC-USDT", "4H", BASE + 80 * WIDTH, ["OHLCV"])
    assert second.selection.dataset_id != first.selection.dataset_id
    with pytest.raises(HistoricalDataSelectionError):
        HistoricalDataSelectionPolicyV1([
            HistoricalStoreV1(frozen, "frozen_research", 0, "0" * 64)
        ]).select("BTC-USDT", "4H", BASE + 80 * WIDTH, ["OHLCV"])


def test_sha_verified_frozen_store_uses_sqlite_immutable_mode(tmp_path, monkeypatch):
    frozen = tmp_path / "frozen.db"
    make_db(frozen, 80)
    digest = hashlib.sha256(frozen.read_bytes()).hexdigest()
    real_connect = sqlite3.connect
    opened = []

    def capture(database_uri, *args, **kwargs):
        opened.append(str(database_uri))
        return real_connect(database_uri, *args, **kwargs)

    monkeypatch.setattr(thesis_historical_data.sqlite3, "connect", capture)
    selected = HistoricalDataSelectionPolicyV1([
        HistoricalStoreV1(frozen, "frozen_research", 0, digest)
    ]).select("BTC-USDT", "4H", BASE + 80 * WIDTH, ["OHLCV"])
    assert selected.selection.row_count == 80
    assert opened and all("mode=ro&immutable=1" in uri for uri in opened)


def test_short_history_is_valid_but_explicitly_warned(tmp_path):
    frozen = tmp_path / "short.db"; make_db(frozen, 240)
    svc = ThesisTestServiceV1(BoundedMarketDataReaderV2(frozen), selection_policy=
        HistoricalDataSelectionPolicyV1([HistoricalStoreV1(frozen, "frozen_research", 0)]))
    result = svc.test(body(240))
    assert result["historical_data"]["breadth_qualification"] == "LIMITED_HISTORICAL_SPAN"
    assert "LIMITED_HISTORICAL_SPAN" in result["warnings"]


def test_breadth_uses_warmup_adjusted_evaluable_span(tmp_path):
    frozen = tmp_path / "raw-over-threshold.db"; make_db(frozen, 1100)
    svc = ThesisTestServiceV1(BoundedMarketDataReaderV2(frozen), selection_policy=
        HistoricalDataSelectionPolicyV1([HistoricalStoreV1(frozen, "frozen_research", 0)]))
    result = svc.test(body(1100))
    assert result["historical_data"]["raw_span_days"] >= 180
    assert result["historical_data"]["span_days"] < 180
    assert result["historical_data"]["breadth_qualification"] == "LIMITED_HISTORICAL_SPAN"
    assert "LIMITED_HISTORICAL_SPAN" in result["warnings"]


def event_fixture(tmp_path):
    svc, _frozen, _recent = service(tmp_path)
    spec = body(); result = svc.test(spec)
    event = next(item for item in result["event_records"] if item["exclusion_status"] == "INCLUDED")
    request = {"version": "thesis-event-context-request-v1", "result_hash": result["result_hash"],
               "thesis_spec": spec, "instrument": "BTC", "timeframe": "4H",
               "event_id": event["event_id"], "event_timestamp": event["timestamp"]}
    return svc, result, event, request


def test_event_context_is_bounded_exact_and_same_dataset(tmp_path):
    svc, result, event, request = event_fixture(tmp_path)
    context = svc.event_context(request)
    assert len(context["candles"]) <= context["row_limit"] == 96
    assert context["candles"][context["event"]["candle_index"]]["close_timestamp"] == event["timestamp"]
    assert context["dataset_identity"]["selected_dataset_id"] == result["historical_data"]["dataset_id"]
    for horizon in context["horizons"]:
        assert context["candles"][horizon["candle_index"]]["close_timestamp"] == horizon["target_timestamp"]
        assert horizon["outcome_close"] is not None


def test_event_context_rejects_correct_id_with_wrong_timestamp(tmp_path):
    svc, _result, _event, request = event_fixture(tmp_path)
    request["event_timestamp"] += WIDTH
    with pytest.raises(ThesisValidationError, match="included member"):
        svc.event_context(request)


def test_event_context_future_window_stops_at_backend_policy_bound(tmp_path):
    svc, _result, event, request = event_fixture(tmp_path)
    context = svc.event_context(request)
    expected_last_close = event["timestamp"] + 24 * 60 * 60 + 4 * WIDTH
    assert context["candles"][-1]["close_timestamp"] == expected_last_close
    assert all(candle["close_timestamp"] <= expected_last_close for candle in context["candles"])


def test_event_context_hard_limit_preserves_event_and_outcomes(tmp_path, monkeypatch):
    svc, _result, event, request = event_fixture(tmp_path)
    monkeypatch.setattr(thesis_event_engine, "EVENT_CONTEXT_BEFORE_ROWS", 100)
    context = svc.event_context(request)
    assert len(context["candles"]) == context["row_limit"] == 96
    event_index = context["event"]["candle_index"]
    assert 0 <= event_index < len(context["candles"])
    assert context["candles"][event_index]["close_timestamp"] == event["timestamp"]
    assert all(item["candle_index"] is not None for item in context["horizons"])


@pytest.mark.parametrize("change,match", [
    ({"event_id": "0" * 64}, "included member"),
    ({"instrument": "ETH"}, "instrument"),
    ({"timeframe": "1H"}, "timeframe"),
    ({"result_hash": "0" * 64}, "identity"),
])
def test_event_context_rejects_wrong_membership_and_identity(tmp_path, change, match):
    svc, _result, _event, request = event_fixture(tmp_path)
    request.update(change)
    with pytest.raises(ThesisValidationError, match=match):
        svc.event_context(request)


class Provider:
    def __init__(self, raw): self.raw, self.calls = raw, 0
    def generate(self, request):
        self.calls += 1
        assert not any(char.isdigit() for message in request["messages"] for char in message["content"])
        return SimpleNamespace(raw_text=self.raw, model="fake", latency_ms=1)


@pytest.mark.parametrize("attack", ["62%", "BTC is likely to rise", "buy BTC", "RSI 70"])
def test_provider_cannot_inject_numbers_predictions_or_trades(tmp_path, attack):
    svc, result, _event, _request = event_fixture(tmp_path)
    provider = Provider(json.dumps({"primary": "LONGEST_OUTCOME",
                                    "secondary": "DOWNSIDE_EXCURSION", "ordering": "OUTCOME_THEN_RISK",
                                    "comment": attack}))
    explanation = ThesisEvidenceExplanationServiceV1(svc, provider).explain({
        "version": "thesis-evidence-explain-request-v1", "thesis_spec": body(),
        "result_hash": result["result_hash"], "language": "en"})
    assert explanation["status"] == "FALLBACK"
    assert attack not in " ".join(block["text"] for block in explanation["blocks"])
    assert "not a forecast or trading recommendation" in explanation["blocks"][-1]["text"]


def test_valid_numeric_free_provider_plan_is_used_and_cached(tmp_path):
    svc, result, _event, _request = event_fixture(tmp_path)
    provider = Provider(json.dumps({"primary": "HIGHEST_MEDIAN_RETURN",
                                    "secondary": "RETURN_RANGE", "ordering": "RISK_THEN_OUTCOME"}))
    explanation_service = ThesisEvidenceExplanationServiceV1(svc, provider)
    request = {"version": "thesis-evidence-explain-request-v1", "thesis_spec": body(),
               "result_hash": result["result_hash"], "language": "en"}
    first = explanation_service.explain(request)
    second = explanation_service.explain({**request, "language": "zh"})
    assert first["status"] == second["status"] == "GENERATED"
    assert first["cache_status"] == "MISS" and second["cache_status"] == "HIT"
    assert provider.calls == 1


def test_same_evidence_hash_accepts_equivalent_nonexecuting_request_metadata(tmp_path):
    svc, _frozen, _recent = service(tmp_path)
    first_spec = body(); first_spec["metadata"] = {"request": "first"}
    second_spec = body(); second_spec["metadata"] = {"request": "second"}
    first = svc.test(first_spec); second = svc.test(second_spec)
    assert first["result_hash"] == second["result_hash"]
    verified, _rows = svc.verified_result(first_spec, first["result_hash"])
    assert verified["result_hash"] == first["result_hash"]


def test_low_n_limitation_and_ai_timeout_fallback_are_mandatory(tmp_path):
    svc, result, _event, _request = event_fixture(tmp_path)
    class TimeoutProvider:
        def generate(self, _request): raise TimeoutError
    explanation = ThesisEvidenceExplanationServiceV1(svc, TimeoutProvider()).explain({
        "version": "thesis-evidence-explain-request-v1", "thesis_spec": body(),
        "result_hash": result["result_hash"], "language": "en"})
    assert explanation["status"] == "FALLBACK"
    assert "Sample quality is" in explanation["blocks"][-1]["text"]
    assert build_evidence_fact_set(result)["facts_hash"] == explanation["facts_hash"]


def test_explanation_rejects_client_statistics_and_translation_keeps_fact_identity(tmp_path):
    svc, result, _event, _request = event_fixture(tmp_path)
    explanation_service = ThesisEvidenceExplanationServiceV1(svc)
    request = {"version": "thesis-evidence-explain-request-v1", "thesis_spec": body(),
               "result_hash": result["result_hash"], "language": "en"}
    with pytest.raises(EvidenceExplanationError):
        explanation_service.explain({**request, "positive_rate": .99})
    english = explanation_service.explain(request)
    chinese = explanation_service.explain({**request, "language": "zh"})
    assert english["facts_hash"] == chinese["facts_hash"]
    assert english["result_hash"] == chinese["result_hash"]
