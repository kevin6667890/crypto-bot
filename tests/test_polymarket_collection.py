from dashboard.polymarket.cadence import (
    forecast_cadence_policy_hash,
    forecast_methodology_hash,
)
from dashboard.polymarket.collection import ForecastCandidate, collect_forecast_batch


def _candidate(market_id):
    return ForecastCandidate(market_id, f"snapshot-{market_id}", f"decision-{market_id}")


def test_methodology_and_cadence_hashes_are_deterministic():
    kwargs = {
        "provider_identity": {"provider": "deepseek", "model": "deepseek-v4-pro", "model_version": "v4"},
        "forecast_schema_version": "v2",
        "prompt_version": "v2",
        "provider_policy_version": "p1",
        "provider_policy_hash": "abc",
    }
    assert forecast_methodology_hash(**kwargs) == forecast_methodology_hash(**kwargs)
    assert forecast_cadence_policy_hash() == forecast_cadence_policy_hash()


def test_collection_is_canonical_bounded_and_evidence_bounded():
    evidence_inputs = []

    def commit(item, evidence_ids):
        evidence_inputs.append(evidence_ids)
        return {"forecast_id": f"forecast-{item.market_id}", "market_id": item.market_id}

    result = collect_forecast_batch(
        [_candidate("3"), _candidate("1"), _candidate("2")],
        max_forecasts=2,
        provider_ready=True,
        already_forecast=lambda item: False,
        retrieve_evidence=lambda item: ["e1", "e2", "e3", "e4"],
        commit_forecast=commit,
    )
    assert result["selected"] == ["1", "2"]
    assert result["attempted"] == 2
    assert [row["market_id"] for row in result["successful"]] == ["1", "2"]
    assert evidence_inputs == [["e1", "e2", "e3"], ["e1", "e2", "e3"]]


def test_collection_skips_existing_insufficient_and_provider_not_ready():
    result = collect_forecast_batch(
        [_candidate("1"), _candidate("2")],
        max_forecasts=2,
        provider_ready=True,
        already_forecast=lambda item: item.market_id == "1",
        retrieve_evidence=lambda item: [],
        commit_forecast=lambda *_: (_ for _ in ()).throw(AssertionError("must not commit")),
    )
    assert result["skipped_existing_initial"] == ["1"]
    assert result["skipped_insufficient_evidence"] == ["2"]
    assert result["attempted"] == 0

    retrievals = []
    not_ready = collect_forecast_batch(
        [_candidate("1")],
        max_forecasts=1,
        provider_ready=False,
        already_forecast=lambda item: False,
        retrieve_evidence=lambda item: retrievals.append(item),
        commit_forecast=lambda *_: None,
    )
    assert not_ready["skipped_provider_not_ready"] == ["1"]
    assert retrievals == []
