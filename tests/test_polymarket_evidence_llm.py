import json

import pytest

from dashboard.polymarket.evidence import build_evidence_payload, strict_evidence_eligible, deterministic_queries, retrieve_candidates
from dashboard.polymarket.llm_forecast import InitialForecastAlreadyExists, build_independent_request, run_independent_forecast, deepseek_model_call, validate_independent_output
from dashboard.polymarket.llm_provider import ProviderError, ProviderResult
from dashboard.polymarket.repository import PolymarketRepository
from tests.test_polymarket_repository import snapshot
from dashboard.polymarket.eligibility import evaluate


def _ready_repo(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    snap_id, decision_id = repo.persist_snapshot(snapshot(), evaluate(snapshot()))
    return repo, snap_id, decision_id


def _valid_output(evidence_id=None):
    return {"probability_yes": 0.57, "confidence": "LOW", "evidence_refs": [evidence_id] if evidence_id else [], "uncertainties": ["uncertain"], "summary": "A short prospective forecast."}


def _strict_evidence(repo, suffix=""):
    return repo.insert_evidence("m1", build_evidence_payload(
        source_url="https://example.test/official",
        title="Official release",
        content=f"A dated fact {suffix}",
        source_type="official",
        published_at="2026-08-23T00:00:00Z",
        retrieved_at="2026-08-24T00:00:00Z",
    ))


def test_unknown_or_post_cutoff_evidence_is_not_strict_eligible():
    unknown = build_evidence_payload(source_url="https://example.test/a", title="A", content="fact", source_type="official", retrieved_at="2026-08-24T00:00:00Z")
    assert strict_evidence_eligible(unknown, "2026-08-24T01:00:00Z") == (False, "timestamp_unknown")
    late = build_evidence_payload(source_url="https://example.test/b", title="B", content="fact", source_type="official", published_at="2026-08-25T00:00:00Z", retrieved_at="2026-08-25T00:00:00Z")
    assert strict_evidence_eligible(late, "2026-08-24T01:00:00Z") == (False, "published_after_cutoff")


def test_known_pre_cutoff_evidence_is_frozen_with_metadata(tmp_path):
    repo, _, _ = _ready_repo(tmp_path)
    payload = build_evidence_payload(source_url="https://example.test/official", title="Official release", content="A dated fact", source_type="official", published_at="2026-08-23T00:00:00Z", retrieved_at="2026-08-24T00:00:00Z")
    evidence_id = repo.insert_evidence("m1", payload)
    row = repo.evidence_rows("m1", [evidence_id])[0]
    assert row["source_url"] == "https://example.test/official"
    assert row["source_published_at"] == "2026-08-23T00:00:00+00:00"
    assert strict_evidence_eligible(payload, "2026-08-24T00:00:00Z") == (True, None)


def test_request_has_no_market_probability_or_orderbook():
    request = build_independent_request({"question": "Will it rain?", "resolution_rule_text": "Rule", "end_date": "2026-09-01"}, [], "2026-08-24T00:00:00Z")
    encoded = json.dumps(request).lower()
    for forbidden in ("midpoint", "best_bid", "best_ask", "market_probability", "orderbook"):
        assert forbidden not in encoded


def test_parser_accepts_sanitized_real_provider_content_without_repair():
    # Shape copied from a real DeepSeek non-thinking chat-completions content
    # field, with the market-specific prose redacted.  The parser may trim the
    # fence, but cannot change any semantic field (especially probability).
    raw = '''```json
{"probability_yes":0.05,"confidence":"LOW","evidence_refs":["evidence-1"],"uncertainties":["redacted uncertainty"],"summary":"redacted summary"}
```'''
    parsed = validate_independent_output(raw)
    assert parsed["probability_yes"] == 0.05
    assert parsed["evidence_refs"] == ["evidence-1"]


def test_malformed_output_is_fail_closed_and_attempt_is_persisted(tmp_path):
    repo, snap_id, decision_id = _ready_repo(tmp_path)
    evidence_id = _strict_evidence(repo)
    with pytest.raises(ValueError, match="malformed_json"):
        run_independent_forecast(repo, market_id="m1", market_snapshot_id=snap_id, eligibility_decision_id=decision_id, evidence_ids=[evidence_id], evidence_cutoff_at="2026-08-24T00:00:00Z", provider_identity={"provider": "test", "model": "fake"}, generation_config={"temperature": 0}, model_call=lambda _: "not json")
    with repo.connect() as c:
        row = c.execute("SELECT status,failure_code FROM llm_forecast_attempts").fetchone()
    assert tuple(row) == ("FAILED", "INVALID_JSON")


def test_success_commits_before_market_reveal_and_restarts(tmp_path):
    repo, snap_id, decision_id = _ready_repo(tmp_path)
    evidence_id = _strict_evidence(repo)
    result = run_independent_forecast(repo, market_id="m1", market_snapshot_id=snap_id, eligibility_decision_id=decision_id, evidence_ids=[evidence_id], evidence_cutoff_at="2026-08-24T00:00:00Z", provider_identity={"provider": "test", "model": "fake", "model_version": "v1"}, generation_config={"temperature": 0}, model_call=lambda request: _valid_output(evidence_id))
    assert result["market_reveal"]["market_probability"] == .51
    assert result["market_reveal"]["raw_residual"] == pytest.approx(.06)
    restarted = PolymarketRepository(repo.path)
    detail = restarted.forecast_detail(result["forecast_id"])
    assert detail and detail["forecast"]["market_snapshot_id"] == snap_id
    assert [row["evidence_id"] for row in detail["evidence"]] == [evidence_id]
    with restarted.connect() as c:
        assert c.execute("SELECT status FROM llm_forecast_attempts").fetchone()[0] == "SUCCEEDED"


def test_queries_and_rejected_evidence_attempts_are_deterministic(tmp_path):
    repo, _, _ = _ready_repo(tmp_path)
    queries = deterministic_queries("Will example happen?", "Resolved by official notice.")
    assert queries == deterministic_queries("Will example happen?", "Resolved by official notice.")
    accepted = retrieve_candidates(repo, market_id="m1", queries=queries, evidence_cutoff_at="2026-08-24T01:00:00Z", candidates=[
        {"url": "https://reuters.com/a", "title": "Unknown", "content": "fact"},
        {"url": "https://reuters.com/b", "title": "Late", "content": "fact", "published_at": "2026-08-25T00:00:00Z"},
        {"url": "https://reuters.com/c", "title": "Good", "content": "fact", "published_at": "2026-08-23T00:00:00Z"},
    ])
    assert len(accepted) == 1
    with repo.connect() as c:
        rows = c.execute("SELECT status,rejection_reason FROM evidence_retrieval_attempts ORDER BY retrieved_at").fetchall()
    assert len(rows) == 3
    assert {row["rejection_reason"] for row in rows if row["status"] == "REJECTED"} == {"STRICT_REJECT_TIMESTAMP_UNKNOWN", "STRICT_REJECT_AFTER_CUTOFF"}


def test_invalid_json_gets_exactly_one_text_fallback_and_both_attempts_persist(tmp_path, monkeypatch):
    repo, snap_id, decision_id = _ready_repo(tmp_path)
    evidence_id = _strict_evidence(repo)
    calls = []
    class FakeProvider:
        def generate_structured_forecast(self, request, *, response_format):
            calls.append(response_format)
            if response_format == "json_object":
                return ProviderResult("not JSON", {"response_format": response_format})
            return ProviderResult(json.dumps(_valid_output(evidence_id)), {"response_format": response_format})
    monkeypatch.setattr("dashboard.polymarket.llm_forecast.configured_provider", lambda: FakeProvider())
    run_independent_forecast(repo, market_id="m1", market_snapshot_id=snap_id, eligibility_decision_id=decision_id, evidence_ids=[evidence_id], evidence_cutoff_at="2026-08-24T00:00:00Z", provider_identity={"provider": "deepseek", "model": "deepseek-v4-pro"}, generation_config={}, model_call=deepseek_model_call)
    assert calls == ["json_object", "text"]
    with repo.connect() as c:
        assert [tuple(row) for row in c.execute("SELECT status,failure_code FROM llm_forecast_attempts ORDER BY attempted_at")] == [("FAILED", "INVALID_JSON"), ("SUCCEEDED", None)]


@pytest.mark.parametrize("failure_code", ["TIMEOUT", "RATE_LIMIT", "AUTH_FAILED", "EMPTY_CONTENT_WITH_REASONING"])
def test_non_format_provider_failure_never_falls_back(tmp_path, failure_code):
    repo, snap_id, decision_id = _ready_repo(tmp_path)
    evidence_id = _strict_evidence(repo)
    calls = []

    def fail_once(request, *, response_format):
        calls.append(response_format)
        raise ProviderError(failure_code)

    with pytest.raises(ProviderError, match=failure_code):
        run_independent_forecast(repo, market_id="m1", market_snapshot_id=snap_id, eligibility_decision_id=decision_id, evidence_ids=[evidence_id], evidence_cutoff_at="2026-08-24T00:00:00Z", provider_identity={"provider": "deepseek", "model": "deepseek-v4-pro"}, generation_config={}, model_call=fail_once)
    assert calls == ["json_object"]


def test_empty_content_gets_exactly_one_text_fallback(tmp_path):
    repo, snap_id, decision_id = _ready_repo(tmp_path)
    evidence_id = _strict_evidence(repo)
    calls = []

    def empty_then_valid(request, *, response_format):
        calls.append(response_format)
        if response_format == "json_object":
            raise ProviderError("EMPTY_CONTENT")
        return _valid_output(evidence_id)

    run_independent_forecast(repo, market_id="m1", market_snapshot_id=snap_id, eligibility_decision_id=decision_id, evidence_ids=[evidence_id], evidence_cutoff_at="2026-08-24T00:00:00Z", provider_identity={"provider": "deepseek", "model": "deepseek-v4-pro"}, generation_config={}, model_call=empty_then_valid)
    assert calls == ["json_object", "text"]


def test_second_format_failure_stops_after_two_attempts(tmp_path):
    repo, snap_id, decision_id = _ready_repo(tmp_path)
    evidence_id = _strict_evidence(repo)
    calls = []

    def invalid_both_times(request, *, response_format):
        calls.append(response_format)
        return "not JSON"

    with pytest.raises(ValueError, match="malformed_json"):
        run_independent_forecast(repo, market_id="m1", market_snapshot_id=snap_id, eligibility_decision_id=decision_id, evidence_ids=[evidence_id], evidence_cutoff_at="2026-08-24T00:00:00Z", provider_identity={"provider": "deepseek", "model": "deepseek-v4-pro"}, generation_config={}, model_call=invalid_both_times)
    assert calls == ["json_object", "text"]


def test_probability_or_schema_failure_never_triggers_fallback(tmp_path):
    repo, snap_id, decision_id = _ready_repo(tmp_path)
    evidence_id = _strict_evidence(repo)
    calls = []

    def invalid_probability(request, *, response_format):
        calls.append(response_format)
        output = _valid_output(evidence_id)
        output["probability_yes"] = 1.0
        return output

    with pytest.raises(ValueError, match="probability_invalid"):
        run_independent_forecast(repo, market_id="m1", market_snapshot_id=snap_id, eligibility_decision_id=decision_id, evidence_ids=[evidence_id], evidence_cutoff_at="2026-08-24T00:00:00Z", provider_identity={"provider": "deepseek", "model": "deepseek-v4-pro"}, generation_config={}, model_call=invalid_probability)
    assert calls == ["json_object"]


def test_initial_forecast_cadence_prevents_second_provider_call(tmp_path):
    repo, snap_id, decision_id = _ready_repo(tmp_path)
    evidence_id = _strict_evidence(repo)
    calls = []

    def valid(request):
        calls.append(request)
        return _valid_output(evidence_id)

    kwargs = dict(repo=repo, market_id="m1", market_snapshot_id=snap_id, eligibility_decision_id=decision_id, evidence_ids=[evidence_id], evidence_cutoff_at="2026-08-24T00:00:00Z", provider_identity={"provider": "test", "model": "fake", "model_version": "v1"}, generation_config={}, model_call=valid)
    run_independent_forecast(**kwargs)
    with pytest.raises(InitialForecastAlreadyExists, match="INITIAL_FORECAST_ALREADY_EXISTS"):
        run_independent_forecast(**kwargs)
    assert len(calls) == 1


def test_formal_forecast_rejects_empty_or_more_than_three_evidence(tmp_path):
    repo, snap_id, decision_id = _ready_repo(tmp_path)
    base = dict(repo=repo, market_id="m1", market_snapshot_id=snap_id, eligibility_decision_id=decision_id, evidence_cutoff_at="2026-08-24T00:00:00Z", provider_identity={"provider": "test", "model": "fake"}, generation_config={}, model_call=lambda request: _valid_output())
    with pytest.raises(ValueError, match="MIN_STRICT_EVIDENCE_NOT_MET"):
        run_independent_forecast(evidence_ids=[], **base)
    evidence_ids = [_strict_evidence(repo, str(index)) for index in range(4)]
    with pytest.raises(ValueError, match="MAX_STRICT_EVIDENCE_EXCEEDED"):
        run_independent_forecast(evidence_ids=evidence_ids, **base)
