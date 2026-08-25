import sqlite3

import pytest

from dashboard.polymarket.client import PolymarketClient
from dashboard.polymarket.eligibility import evaluate
from dashboard.polymarket.models import stable_hash
from dashboard.polymarket.repository import PolymarketRepository
from dashboard.polymarket.resolution import classify_resolution
from dashboard.polymarket.scoring import executable_pnl_v1


def _market(market_id="m1"):
    return {"id": market_id, "slug": market_id, "question": "Will it rain?", "active": True, "closed": False,
            "outcomes": '["Yes","No"]', "clobTokenIds": '["y","n"]'}


def _snapshot(market_id="m1"):
    market = _market(market_id)
    return {"market": market, "gamma_payload": market, "captured_at": "2026-08-24T00:00:00+00:00", "source_timestamp": None,
            "resolution_rule_text": "Official source.", "end_date": "2026-09-01T00:00:00Z", "outcomes": ["YES", "NO"], "token_mapping": {"YES": "y", "NO": "n"},
            "yes_orderbook": {"bids": [{"price": "0.49"}], "asks": [{"price": "0.51"}]}, "no_orderbook": {"bids": [{"price": "0.49"}], "asks": [{"price": "0.51"}]},
            "quotes": {"YES": {"best_bid": "0.49", "best_ask": "0.51", "midpoint": "0.50"}, "NO": {"best_bid": "0.49", "best_ask": "0.51", "midpoint": "0.50"}}}


class _Session:
    def __init__(self): self.calls = []
    def get(self, url, params, timeout):
        self.calls.append(params.copy())
        cursor = params.get("after_cursor")
        data = ([{"id": "2"}, {"id": "1"}] if cursor is None else [{"id": "3"}])
        next_cursor = "cursor-1" if cursor is None else None
        class R:
            def raise_for_status(self): pass
            def json(self): return {"markets": data, "next_cursor": next_cursor}
        return R()


def test_gamma_pagination_is_complete_and_canonical():
    client = PolymarketClient(session=_Session())
    assert [m["id"] for m in client.fetch_active_markets(page_size=2)] == ["1", "2", "3"]


def test_universe_and_eligible_set_are_deterministic(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    items = [_market("2"), _market("1")]
    first = repo.persist_universe(items, "v1", "hash", "2026-08-24T00:00:00+00:00")
    assert [row["market_id"] for row in repo.universe_manifest(first)] == ["1", "2"]
    one, two = _snapshot("1"), _snapshot("2")
    assert evaluate(one) == evaluate(one)
    repo.persist_snapshot(one, evaluate(one)); repo.persist_snapshot(two, evaluate(two))


def test_resolution_append_only_and_ambiguous_fail_closed(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite"); s = _snapshot(); repo.persist_snapshot(s, evaluate(s))
    valid = {**_market(), "closed": True, "outcomePrices": '["1","0"]'}
    first = repo.append_resolution("m1", valid, **classify_resolution(valid))
    changed = {**valid, "note": "revised"}
    second = repo.append_resolution("m1", changed, **classify_resolution(changed))
    assert second["revision"] == 2
    with repo.connect() as c:
        with pytest.raises(sqlite3.DatabaseError): c.execute("DELETE FROM resolutions WHERE resolution_id=?", (first["resolution_id"],))
    ambiguous = {**_market(), "closed": True, "outcomePrices": '["0.5","0.5"]'}
    assert classify_resolution(ambiguous)["classification"] == "AMBIGUOUS_50_50"


def test_scoring_uses_forecast_snapshot_and_ask_not_midpoint(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    s1 = _snapshot(); sid, decision_id = repo.persist_snapshot(s1, evaluate(s1))
    fid = repo.insert_forecast({"market_id": "m1", "market_snapshot_id": sid,
        "eligibility_decision_id": decision_id, "forecasted_at": "2026-08-24T00:01:00+00:00",
        "evidence_cutoff_at": "2026-08-24T00:01:00+00:00", "forecast_schema_version": "test-v2",
        "producer_kind": "LLM", "producer_identity": {"provider": "fixture"},
        "config_hash": stable_hash({"fixture": True}), "probability": .7,
        "rationale": "synthetic scorer fixture", "committed_at": "2026-08-24T00:01:00+00:00",
        "forecast_methodology_hash": stable_hash({"methodology": "fixture"})}, [])
    # A later market snapshot with a different midpoint cannot change the old forecast score.
    s2 = _snapshot(); s2["captured_at"] = "2026-08-25T00:00:00+00:00"; s2["quotes"]["YES"]["midpoint"] = "0.90"; repo.persist_snapshot(s2, evaluate(s2))
    resolved = {**_market(), "closed": True, "outcomePrices": '["1","0"]'}
    repo.append_resolution("m1", resolved, **classify_resolution(resolved))
    assert repo.score_resolved_forecasts() == 1
    with repo.connect() as c:
        score = c.execute("SELECT * FROM scores WHERE forecast_id=?", (fid,)).fetchone()
        assert score["market_midpoint_probability"] == .5
        assert score["executable_entry_ask"] == .51
        assert score["fee_status"] == "UNKNOWN"
    pnl = executable_pnl_v1(.7, .5, .51, .51, 1)
    assert pnl["entry_ask"] == .51 and pnl["net_pnl"] is None
