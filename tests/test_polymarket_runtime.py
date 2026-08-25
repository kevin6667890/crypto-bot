import sqlite3

import pytest

from dashboard.polymarket.__main__ import sync_universe_v2
from dashboard.polymarket.repository import PolymarketRepository


def _market(market_id, question="Will the event happen?"):
    return {"id": market_id, "slug": market_id, "question": question, "active": True,
            "closed": False, "conditionId": f"condition-{market_id}",
            "outcomes": '["Yes","No"]', "clobTokenIds": f'["yes-{market_id}","no-{market_id}"]',
            "description": "Resolves YES if the official source confirms the event.",
            "endDate": "2027-01-01T00:00:00Z", "negRisk": True,
            "events": [{"id": "event-1", "slug": "event-one"}]}


class _Client:
    timeout = 1

    def __init__(self):
        self.book_calls = []

    def fetch_active_markets(self, limit, *, page_size, as_of=None):
        # Deliberately non-canonical source order.
        return [_market("2", "Will value be between 1 and 2?"), _market("1")]

    def token_mapping(self, market):
        return {"YES": f"yes-{market['id']}", "NO": f"no-{market['id']}"}

    def fetch_orderbook(self, token_id):
        self.book_calls.append(token_id)
        return {"bids": [{"price": ".47"}], "asks": [{"price": ".52"}]}

    @staticmethod
    def quote(book):
        return {"best_bid": ".47", "best_ask": ".52", "midpoint": ".495"}


def test_formal_two_stage_pipeline_fetches_clob_only_after_prefilter(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    client = _Client()
    universe_id, rows, stats = sync_universe_v2(repo, client, None, 500, clob_workers=2)
    assert [row["market_id"] for row in rows] == ["1", "2"]
    assert stats["full_universe_count"] == 2
    assert stats["metadata_candidate_count"] == 1
    assert stats["clob_request_count"] == 2
    assert stats["clob_request_reduction_pct"] == 50
    assert client.book_calls == ["yes-1", "no-1"]
    with repo.connect() as connection:
        universe = connection.execute("SELECT market_count,pagination_policy_version FROM universe_snapshots WHERE universe_snapshot_id=?", (universe_id,)).fetchone()
        assert tuple(universe) == (2, "gamma-keyset-after-cursor-id-ascending-v1")
        assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 2


def test_cohort_and_collection_ledgers_are_append_only(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    universe_id = repo.persist_universe([_market("1")], "selection-v1", "hash",
        "2026-08-24T00:00:00+00:00", pagination_metadata={"version": "page-v1"})
    cohort_id = repo.insert_cohort({"universe_snapshot_id": universe_id,
        "started_at": "2026-08-24T00:00:00+00:00", "completed_at": "2026-08-24T00:01:00+00:00",
        "status": "DRY_RUN", "eligibility_policy_version": "v2", "eligibility_policy_hash": "eh",
        "evidence_policy_version": "e1", "evidence_policy_hash": "evh",
        "forecast_schema_version": "f2", "prompt_version": "p2", "prompt_hash": "ph",
        "provider_policy_version": "provider-v1", "provider_policy_hash": "pph",
        "model_identity": {"provider": "fixture"}, "scoring_version": "s2",
        "execution_simulation_version": "x1", "config": {"dry_run": True}}, [])
    run_id = repo.insert_collection_run({"cohort_id": cohort_id,
        "started_at": "2026-08-24T00:00:00+00:00", "completed_at": "2026-08-24T00:01:00+00:00",
        "status": "DRY_RUN", "summary": {"pipeline": {"metadata_candidate_count": 1}}, "error_code": None})
    with repo.connect() as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE cohort_runs SET status='SUCCEEDED' WHERE cohort_id=?", (cohort_id,))
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DELETE FROM collection_runs WHERE collection_run_id=?", (run_id,))
