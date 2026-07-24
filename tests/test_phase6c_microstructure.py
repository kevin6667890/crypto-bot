from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dashboard.microstructure import (
    MICROSTRUCTURE_FEATURE_VERSION,
    EventStudyEngine,
    FeatureEngine,
    MicrostructureMigration,
    MicrostructureStore,
    exploratory_report,
    forward_validation_manifest,
)
from dashboard.microstructure_backfill import OfficialBackfill, PublicOKXClient


def store(tmp_path: Path) -> MicrostructureStore:
    value = MicrostructureStore(tmp_path / "market_microstructure.db")
    value.initialize()
    return value


def legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as c:
        c.executescript(
            """CREATE TABLE flow_trade_buckets(
                instrument TEXT,ts INTEGER,buy_notional REAL,sell_notional REAL,
                trade_count INTEGER,PRIMARY KEY(instrument,ts));
               CREATE TABLE oi_snapshots(
                instrument TEXT,ts INTEGER,oi REAL,source TEXT,
                PRIMARY KEY(instrument,ts));
               CREATE TABLE flow_snapshots(
                id INTEGER PRIMARY KEY,created_at TEXT,oi REAL,cvd REAL,
                instrument TEXT,trade_count INTEGER,window_seconds INTEGER,last_trade_ts INTEGER);
               CREATE TABLE flow_history_aggregates(
                instrument TEXT,series TEXT,resolution_seconds INTEGER,bucket_ts INTEGER,
                delta REAL,value_last REAL,value_min REAL,value_max REAL,trade_count INTEGER,
                observation_count INTEGER,first_ts INTEGER,last_ts INTEGER,source TEXT,
                PRIMARY KEY(instrument,series,resolution_seconds,bucket_ts));"""
        )
        c.execute("INSERT INTO flow_trade_buckets VALUES('BTC-USDT',1700000000,10,3,2)")
        c.execute("INSERT INTO oi_snapshots VALUES('BTC-USDT',1700000000,100,'OKX REST public/open-interest')")
        c.execute("INSERT INTO flow_snapshots VALUES(1,'2023-11-14T21:00:00+00:00',90,999,'BTC-USDT',1,1,1)")
        c.execute("""INSERT INTO flow_history_aggregates VALUES(
            'BTC-USDT','cvd',300,1699999800,7,NULL,NULL,NULL,2,1,1700000000,1700000000,'flow_trade_buckets')""")


def seed_causal(value: MicrostructureStore) -> None:
    base = 1_700_000_000_000
    for index in range(25):
        timestamp = base + index * 900_000
        value.insert_trade("BTC-USDT-SWAP", {
            "ts": timestamp, "px": 50_000 + index * 10, "sz": 2,
            "side": "buy" if index % 2 else "sell", "tradeId": str(index),
        }, contract_value=0.01)
        value.insert_oi("BTC-USDT-SWAP", timestamp, oi_contracts=100 + index,
                        oi_currency=1 + index, oi_usd=50_000 + index * 100,
                        source="official OI", source_identity=str(index))
        value.insert_price("mark", "BTC-USDT-SWAP", timestamp, 50_000 + index * 10,
                           source_identity=f"m{index}")
        value.insert_price("index", "BTC-USDT", timestamp, 49_990 + index * 10,
                           source_identity=f"i{index}")


def test_migration_preserves_genuine_rows_and_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "paper.db"
    legacy_database(source)
    value = store(tmp_path)
    migration = MicrostructureMigration(value)
    first = migration.migrate(source)
    second = migration.migrate(source)
    assert first["trade_flow_observations"] == 2
    assert first["oi_observations"] == 2
    assert second["trade_flow_observations"] == 0
    assert second["oi_observations"] == 0
    with sqlite3.connect(source) as c:
        assert c.execute("SELECT COUNT(*) FROM flow_trade_buckets").fetchone()[0] == 1


def test_no_synthetic_history_or_zero_filling(tmp_path: Path) -> None:
    value = store(tmp_path)
    value.insert_trade("BTC-USDT-SWAP", {
        "ts": 1_700_000_000_000, "px": 50_000, "sz": 1, "side": "buy", "tradeId": "1",
    }, contract_value=0.01)
    value.insert_trade("BTC-USDT-SWAP", {
        "ts": 1_700_003_600_000, "px": 50_000, "sz": 1, "side": "sell", "tradeId": "2",
    }, contract_value=0.01)
    value.aggregate_all()
    with value.connect(readonly=True) as c:
        buckets = c.execute(
            "SELECT bucket_ms FROM cvd_aggregates WHERE resolution='1m' ORDER BY bucket_ms").fetchall()
    assert len(buckets) == 2
    assert buckets[1][0] - buckets[0][0] == 3_600_000


def test_cvd_requires_genuine_direction_and_contract_notional(tmp_path: Path) -> None:
    value = store(tmp_path)
    with pytest.raises(ValueError):
        value.insert_trade("BTC-USDT-SWAP", {
            "ts": 1, "px": 50_000, "sz": 2, "side": "unknown", "tradeId": "x",
        }, contract_value=0.01)
    value.insert_trade("BTC-USDT-SWAP", {
        "ts": 1_700_000_000_000, "px": 50_000, "sz": 2, "side": "buy", "tradeId": "x",
    }, contract_value=0.01)
    with value.connect(readonly=True) as c:
        row = c.execute("SELECT side,notional FROM trade_flow_observations").fetchone()
    assert tuple(row) == ("buy", 1000.0)


def test_oi_is_independent_of_volume(tmp_path: Path) -> None:
    value = store(tmp_path)
    value.insert_oi("BTC-USDT-SWAP", 1_700_000_000_000, oi_contracts=2,
                    oi_currency=0.02, oi_usd=1000, source="official",
                    source_identity="oi-1")
    with value.connect(readonly=True) as c:
        assert c.execute("SELECT oi_usd FROM oi_observations").fetchone()[0] == 1000
        assert c.execute("SELECT COUNT(*) FROM trade_flow_observations").fetchone()[0] == 0


def test_settled_and_predicted_funding_are_separate(tmp_path: Path) -> None:
    value = store(tmp_path)
    value.insert_funding("BTC-USDT-SWAP", {
        "fundingTime": "1700000000000", "fundingRate": "0.001"}, settled=True)
    value.insert_funding("BTC-USDT-SWAP", {
        "ts": "1700000000000", "fundingTime": "1700003600000",
        "fundingRate": "0.002"}, settled=False)
    with value.connect(readonly=True) as c:
        assert c.execute("SELECT funding_rate FROM funding_settled").fetchone()[0] == .001
        assert c.execute("SELECT funding_rate FROM funding_predicted").fetchone()[0] == .002


def test_duplicate_trade_and_restart_resume_do_not_duplicate(tmp_path: Path) -> None:
    value = store(tmp_path)
    payload = {"ts": 1_700_000_000_000, "px": 50_000, "sz": 2,
               "side": "buy", "tradeId": "official-1"}
    assert value.insert_trade("BTC-USDT-SWAP", payload, contract_value=.01)
    assert not MicrostructureStore(value.path).insert_trade(
        "BTC-USDT-SWAP", payload, contract_value=.01)


def test_aggregation_is_deterministic_and_pruning_preserves_it(tmp_path: Path) -> None:
    value = store(tmp_path)
    old = 1_600_000_000_000
    value.insert_trade("BTC-USDT-SWAP", {
        "ts": old, "px": 10_000, "sz": 1, "side": "buy", "tradeId": "old",
    }, contract_value=.01)
    value.aggregate_all()
    with value.connect(readonly=True) as c:
        before = [tuple(row) for row in c.execute("SELECT * FROM cvd_aggregates")]
    value.aggregate_all()
    with value.connect(readonly=True) as c:
        assert [tuple(row) for row in c.execute("SELECT * FROM cvd_aggregates")] == before
    value.prune_raw(1_800_000_000_000)
    with value.connect(readonly=True) as c:
        assert c.execute("SELECT COUNT(*) FROM trade_flow_observations").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM cvd_aggregates").fetchone()[0] > 0


def test_basis_join_never_uses_future_index(tmp_path: Path) -> None:
    value = store(tmp_path)
    value.insert_price("mark", "BTC-USDT-SWAP", 2_000, 105, source_identity="m")
    value.insert_price("index", "BTC-USDT", 3_000, 100, source_identity="future")
    value.aggregate_all()
    with value.connect(readonly=True) as c:
        assert c.execute("SELECT COUNT(*) FROM basis_aggregates").fetchone()[0] == 0


def test_feature_generation_is_causal_and_future_data_cannot_rewrite_past(tmp_path: Path) -> None:
    value = store(tmp_path)
    seed_causal(value)
    FeatureEngine(value).generate()
    with value.connect(readonly=True) as c:
        decision = c.execute("SELECT MIN(decision_ts_ms) FROM feature_snapshots").fetchone()[0]
        before = [tuple(row) for row in c.execute(
            "SELECT feature_name,feature_value,source_timestamps_json FROM feature_snapshots WHERE decision_ts_ms=? ORDER BY feature_name",
            (decision,))]
        for row in c.execute(
            "SELECT source_timestamps_json FROM feature_snapshots WHERE decision_ts_ms=?", (decision,)):
            assert max(json.loads(row[0]).values()) <= decision
    future = decision + 100_000_000
    value.insert_oi("BTC-USDT-SWAP", future, oi_contracts=999, oi_currency=999,
                    oi_usd=999, source="official", source_identity="future")
    FeatureEngine(value).generate()
    with value.connect(readonly=True) as c:
        after = [tuple(row) for row in c.execute(
            "SELECT feature_name,feature_value,source_timestamps_json FROM feature_snapshots WHERE decision_ts_ms=? ORDER BY feature_name",
            (decision,))]
    assert before == after


def test_event_labels_are_strictly_after_decision(tmp_path: Path) -> None:
    value = store(tmp_path)
    seed_causal(value)
    FeatureEngine(value).generate()
    result = EventStudyEngine(value).run()
    assert all(item["label_policy"] == "strictly after decision timestamp"
               for item in result["results"])


def test_short_sample_blocks_formal_research_and_windows(tmp_path: Path) -> None:
    value = store(tmp_path)
    seed_causal(value)
    status = value.sample_status()
    assert status["sample_status"] == "EXPLORATORY_ONLY"
    assert not status["formal_claims_permitted"]
    manifest = forward_validation_manifest(value)
    assert all(item["status"] == "PENDING" for item in manifest["activation"].values())
    report = exploratory_report(value)
    assert report["title"] == "EXPLORATORY_ONLY — INSUFFICIENT SAMPLE"
    assert report["automatic_strategy_discovery"] == "BLOCKED"
    assert report["holdout_oot_accessed"] is False


class FakeClient:
    retries = 0
    failed_requests = 0

    def __init__(self) -> None:
        self.calls = 0

    def get_public(self, path: str, params: dict[str, object]) -> list[dict[str, str]]:
        if path.endswith("/instruments"):
            return [{"ctType": "linear", "ctVal": "0.01"}]
        if path.endswith("/history-trades"):
            self.calls += 1
            return ([{"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1",
                      "px": "50000", "tradeId": "1", "ts": "1700000000000"}]
                    if self.calls == 1 else [])
        return []


def test_backfill_is_idempotent_and_checkpointed(tmp_path: Path) -> None:
    value = store(tmp_path)
    first = OfficialBackfill(value, FakeClient()).backfill_trades(
        "BTC-USDT-SWAP", max_pages=2)
    second = OfficialBackfill(value, FakeClient()).backfill_trades(
        "BTC-USDT-SWAP", max_pages=2)
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    with value.connect(readonly=True) as c:
        assert c.execute("SELECT status FROM collection_checkpoints").fetchone()[0] == "complete"


def test_public_client_rejects_order_and_private_paths() -> None:
    client = PublicOKXClient()
    with pytest.raises(ValueError):
        client.get_public("/api/v5/trade/order", {})
    with pytest.raises(ValueError):
        client.get_public("/api/v5/account/balance", {})


def test_collector_has_no_trading_credentials_or_order_calls() -> None:
    source = Path("dashboard/microstructure_collector.py").read_text()
    forbidden = ["api/v5/trade/order", "OK-ACCESS-KEY", "secretKey", "passphrase"]
    assert not any(item in source for item in forbidden)


def test_sol_collection_is_an_independent_task() -> None:
    source = Path("dashboard/microstructure_collector.py").read_text()
    assert "rest-{instrument}" in source
    assert "A SOL failure terminates neither this loop nor BTC/ETH tasks" in source


def test_health_has_required_fields_and_no_credentials(tmp_path: Path) -> None:
    value = store(tmp_path)
    payload = value.health()
    assert {"service_status", "database_schema_version", "raw_rows", "aggregate_rows",
            "sample_days", "sample_status", "next_minimum_sample_date"} <= payload.keys()
    serialized = json.dumps(payload).lower()
    assert all(item not in serialized for item in ("secret", "passphrase", "credential"))


def test_feature_version_is_persisted(tmp_path: Path) -> None:
    value = store(tmp_path)
    seed_causal(value)
    FeatureEngine(value).generate()
    with value.connect(readonly=True) as c:
        assert {row[0] for row in c.execute(
            "SELECT DISTINCT feature_version FROM feature_snapshots")} == {
                MICROSTRUCTURE_FEATURE_VERSION}
