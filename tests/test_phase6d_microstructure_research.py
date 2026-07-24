"""Tests for Phase 6D microstructure enhancements."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
import pytest

from dashboard.microstructure import MicrostructureStore, now_ms
from dashboard.microstructure_research import SourceSpecificEventStudy

@pytest.fixture
def store(tmp_path) -> MicrostructureStore:
    """Provide a fresh SQLite store."""
    db_path = tmp_path / "microstructure_6d.db"
    s = MicrostructureStore(db_path)
    s.initialize()
    return s

def test_per_feature_eligibility_independent_windows(store: MicrostructureStore) -> None:
    """Test that funding eligibility is independent of trade gaps."""
    # Insert 60 days of funding_settled
    now = now_ms()
    day_ms = 86_400_000
    store.insert_funding("BTC-USDT-SWAP", {"fundingTime": now - 60 * day_ms, "fundingRate": 0.0001}, settled=True)
    store.insert_funding("BTC-USDT-SWAP", {"fundingTime": now, "fundingRate": 0.0002}, settled=True)
    
    # Insert only 5 days of trades
    store.insert_trade("BTC-USDT-SWAP", {"instId": "BTC-USDT-SWAP", "tradeId": "123", "px": "60000", "sz": "1", "side": "buy", "ts": str(now - 5 * day_ms)}, contract_value=0.01)
    store.insert_trade("BTC-USDT-SWAP", {"instId": "BTC-USDT-SWAP", "tradeId": "124", "px": "60000", "sz": "1", "side": "sell", "ts": str(now)}, contract_value=0.01)
    
    eligibility = store.per_feature_eligibility()
    
    groups = eligibility["feature_groups"]
    funding = groups["settled_funding"]
    cvd = groups["cvd"]
    
    assert funding["usable_days"] >= 60.0
    assert funding["status"] == "FORMAL_RESEARCH_READY"
    
    assert cvd["usable_days"] <= 5.0
    assert cvd["status"] == "EXPLORATORY_ONLY"

def test_source_specific_event_study_exploratory_only(store: MicrostructureStore) -> None:
    """Test that SourceSpecificEventStudy marks output as exploratory_only."""
    study = SourceSpecificEventStudy(store)
    result = study.run_all_eligible()
    
    assert result["exploratory_only"] is True
    assert "report_id" in result
    
    # Check that funding study itself also sets exploratory_only
    funding_result = study.run_funding_study()
    assert funding_result["exploratory_only"] is True

def test_event_study_results_persistence(store: MicrostructureStore) -> None:
    """Test that study results are persisted to the database."""
    study = SourceSpecificEventStudy(store)
    study._save_result("test_feature", "1H", {"test": "payload"}, 42)
    
    with store.connect(readonly=True) as c:
        row = c.execute("SELECT * FROM event_study_results WHERE report_id=?", (study.report_id,)).fetchone()
    
    assert row is not None
    assert row["feature_name"] == "test_feature"
    assert row["horizon"] == "1H"
    assert row["event_count"] == 42
    assert json.loads(row["payload_json"]) == {"test": "payload"}
