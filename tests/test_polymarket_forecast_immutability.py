import sqlite3

import pytest

from dashboard.polymarket.eligibility import evaluate
from dashboard.polymarket.forecast import commit_manual_forecast
from dashboard.polymarket.repository import PolymarketRepository
from tests.test_polymarket_repository import snapshot


def test_forecast_is_bound_to_original_snapshot_and_is_immutable(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    first = snapshot(); first_id, _ = repo.persist_snapshot(first, evaluate(first))
    forecast_id = commit_manual_forecast(repo, "m1", .57, "MANUAL", "test")
    later = snapshot(); later["captured_at"] = "2026-08-24T00:01:00+00:00"; later["quotes"]["YES"]["midpoint"] = "0.60"
    repo.persist_snapshot(later, evaluate(later))
    detail = repo.forecast_detail(forecast_id)
    assert detail and detail["forecast"]["market_snapshot_id"] == first_id
    with repo.connect() as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE forecasts SET probability=.9 WHERE forecast_id=?", (forecast_id,))
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("DELETE FROM forecasts WHERE forecast_id=?", (forecast_id,))
