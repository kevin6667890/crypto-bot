import sqlite3

from dashboard.polymarket.eligibility import evaluate
from dashboard.polymarket.repository import PolymarketRepository


def snapshot():
    market = {"id": "m1", "slug": "m1", "question": "Will it rain?", "active": True, "closed": False, "outcomes": '["Yes","No"]', "clobTokenIds": '["y","n"]'}
    return {"market": market, "gamma_payload": market, "captured_at": "2026-08-24T00:00:00+00:00", "source_timestamp": None, "resolution_rule_text": "Official source decides.", "end_date": "2026-09-01T00:00:00Z", "outcomes": ["YES", "NO"], "token_mapping": {"YES": "y", "NO": "n"}, "yes_orderbook": {"bids": [{"price": "0.50"}], "asks": [{"price": "0.52"}]}, "no_orderbook": {"bids": [{"price": "0.48"}], "asks": [{"price": "0.50"}]}, "quotes": {"YES": {"best_bid": "0.50", "best_ask": "0.52", "midpoint": "0.51"}, "NO": {"best_bid": "0.48", "best_ask": "0.50", "midpoint": "0.49"}}}


def test_snapshot_append_only_and_rejected_is_recorded(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    good = snapshot(); sid, did = repo.persist_snapshot(good, evaluate(good))
    bad = snapshot(); bad["market"] = {**bad["market"], "id": "m2", "active": False}; bad["gamma_payload"] = bad["market"]
    _, rejected = repo.persist_snapshot(bad, evaluate(bad))
    with repo.connect() as c:
        assert c.execute("SELECT eligible FROM eligibility_decisions WHERE decision_id=?", (did,)).fetchone()[0] == 1
        assert c.execute("SELECT eligible FROM eligibility_decisions WHERE decision_id=?", (rejected,)).fetchone()[0] == 0
        try:
            c.execute("UPDATE market_snapshots SET end_date='x' WHERE snapshot_id=?", (sid,))
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("append-only trigger did not reject update")
