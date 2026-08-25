from dashboard.polymarket.eligibility import (
    POLICY_V2_VERSION,
    evaluate,
    evaluate_v2,
    event_lineage,
    metadata_prefilter,
    policy_v2_hash,
)
from tests.test_polymarket_repository import snapshot


def test_eligibility_is_deterministic_and_conservative():
    value = snapshot()
    assert evaluate(value) == evaluate(value)
    assert evaluate(value)["eligible"]
    value["quotes"]["YES"]["best_ask"] = None
    result = evaluate(value)
    assert not result["eligible"] and "YES_ORDERBOOK_UNAVAILABLE" in result["reasons"]


def test_negrisk_binary_is_accepted_v2_and_event_cluster_is_stable():
    value = snapshot()
    value["market"].update({"negRisk": True, "conditionId": "condition-1", "eventId": "event-1", "eventSlug": "event-one"})
    decision = evaluate_v2(value)
    assert decision["eligible"] and decision["neg_risk"]
    assert decision["statistical_cluster_id"] == "event-1"
    assert event_lineage(value["market"]) == event_lineage(value["market"])


def test_v2_range_rejects_and_prefilter_keeps_negrisk_candidate():
    value = snapshot()
    value["market"].update({"negRisk": True, "conditionId": "c", "endDate": value["end_date"], "description": value["resolution_rule_text"]})
    assert metadata_prefilter(value["market"], captured_at=value["captured_at"]) == []
    value["market"]["question"] = "Will CPI be between 2 and 3?"
    assert "AMBIGUOUS_SCALAR_OR_RANGE" in evaluate_v2(value)["reasons"]


def test_v2_dependent_contract_fails_closed_but_negrisk_does_not():
    value = snapshot()
    value["market"].update({"negRisk": True, "conditionId": "c", "standaloneResolution": False})
    decision = evaluate_v2(value)
    assert not decision["eligible"]
    assert "REQUIRES_SIBLING_MARKETS" in decision["reasons"]
    assert all("NEGRISK" not in reason for reason in decision["reasons"])


def test_metadata_prefilter_rejects_complex_before_clob_and_is_deterministic():
    value = snapshot()
    market = value["market"]
    market.update({"conditionId": "c", "endDate": value["end_date"], "description": value["resolution_rule_text"], "marketType": "scalar"})
    first = metadata_prefilter(market, captured_at=value["captured_at"])
    assert first == metadata_prefilter(market, captured_at=value["captured_at"])
    assert "AMBIGUOUS_SCALAR_OR_RANGE" in first


def test_event_lineage_is_canonical_when_gamma_event_order_changes():
    market = {"id": "m", "negRisk": True, "events": [{"id": "20", "slug": "b"}, {"id": "10", "slug": "a"}]}
    reverse = {**market, "events": list(reversed(market["events"]))}
    expected = {"event_id": "10", "event_slug": "a", "neg_risk": True, "statistical_cluster_id": "10"}
    assert event_lineage(market) == expected
    assert event_lineage(reverse) == expected


def test_v2_policy_identity_is_versioned_and_stable():
    assert POLICY_V2_VERSION == "polymarket-eligibility-v2.2"
    assert policy_v2_hash() == policy_v2_hash()
