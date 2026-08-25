import sqlite3

from dashboard.polymarket.__main__ import run_collection, sync_universe_v2
from dashboard.polymarket.operations import database_maintenance, disk_guard, health, online_backup
from dashboard.polymarket.repository import PolymarketRepository, UNIVERSE_MANIFEST_SCHEMA


def _market(market_id: str) -> dict:
    return {"id": market_id, "slug": market_id, "question": "Will it happen?", "active": True,
            "closed": False, "conditionId": f"condition-{market_id}", "outcomes": '["Yes","No"]',
            "clobTokenIds": f'["yes-{market_id}","no-{market_id}"]',
            "description": "Resolves YES if the official source confirms it.",
            "endDate": "2027-01-01T00:00:00Z", "events": [{"id": "event-1", "slug": "event-one"}]}


class _Client:
    def fetch_active_markets(self, limit, *, page_size, as_of=None):
        return [_market("2"), _market("1")]

    @staticmethod
    def token_mapping(market):
        return {"YES": f"yes-{market['id']}", "NO": f"no-{market['id']}"}

    @staticmethod
    def fetch_orderbook(token_id):
        return {"bids": [{"price": ".45"}], "asks": [{"price": ".55"}]}

    @staticmethod
    def quote(book):
        return {"best_bid": ".45", "best_ask": ".55", "midpoint": ".50"}


def test_compact_manifest_is_exact_rebuildable_and_content_addressed(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    markets = [_market(str(i)) for i in range(1000, 0, -1)]
    first = repo.persist_universe(markets, "selection-v2", "hash", "2026-08-25T00:00:00+00:00")
    second = repo.persist_universe(markets, "selection-v2", "hash", "2026-08-25T03:00:00+00:00")
    rebuilt = repo.universe_manifest(first)
    assert [row["market_id"] for row in rebuilt] == sorted((str(i) for i in range(1, 1001)))
    assert len({row["payload_hash"] for row in rebuilt}) == 1000
    assert repo.universe_manifest(second) == rebuilt
    with repo.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM universe_market_refs").fetchone()[0] == 0
        manifest = connection.execute("SELECT schema_version,canonical_size_bytes,LENGTH(compressed_payload) FROM universe_manifests").fetchone()
        assert manifest[0] == UNIVERSE_MANIFEST_SCHEMA
        assert manifest[2] < manifest[1]
        assert connection.execute("SELECT COUNT(*) FROM universe_manifests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 2


def test_incremental_unchanged_markets_use_manifest_not_observation_rows(tmp_path, monkeypatch):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    client = _Client()
    sync_universe_v2(repo, client, None, 500, mode="FULL_BOOTSTRAP")
    monkeypatch.setattr("dashboard.polymarket.__main__.utc_now", lambda: "2026-08-25T07:00:00+00:00")
    _, _, stats = sync_universe_v2(repo, client, None, 500, mode="INCREMENTAL", collection_run_id="run-2")
    assert stats["unchanged_metadata_observations"] == 2
    assert stats["unchanged_observation_rows_written"] == 0
    with repo.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM collection_observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM universe_manifests").fetchone()[0] == 1


def test_disk_guard_skips_before_network_work(tmp_path, monkeypatch):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    monkeypatch.setenv("POLYMARKET_MIN_FREE_BYTES", str(10**30))
    guard = disk_guard(repo)
    assert guard["safe"] is False
    result = run_collection(repo, max_forecasts=1, dry_run=True, page_size=500, clob_workers=1, incremental=True)
    assert result["status"] == "SKIPPED_LOW_DISK_SPACE"
    with repo.connect() as connection:
        row = connection.execute("SELECT status,error_code FROM collection_runs").fetchone()
        assert tuple(row) == ("SKIPPED_LOW_DISK_SPACE", "LOW_DISK_SPACE")


def test_safe_database_maintenance_never_vacuums_or_deletes(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    repo.persist_universe([_market("1")], "selection-v2", "hash")
    with repo.connect() as connection:
        universe_id = connection.execute("SELECT universe_snapshot_id FROM universe_snapshots").fetchone()[0]
    before = repo.universe_manifest(universe_id)
    report = database_maintenance(repo)
    assert report["integrity_check"] == "ok"
    assert report["analyze"] == "completed"
    assert report["vacuum"] == "not_run"
    with sqlite3.connect(repo.path) as connection:
        universe_id = connection.execute("SELECT universe_snapshot_id FROM universe_snapshots").fetchone()[0]
    assert repo.universe_manifest(universe_id) == before


def test_health_exposes_bounded_production_acceptance_fields(tmp_path, monkeypatch):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    repo.insert_collection_run({"started_at": "2026-08-25T00:00:00+00:00",
        "completed_at": "2026-08-25T00:20:00+00:00", "status": "SUCCEEDED",
        "summary": {"pipeline": {}}, "error_code": None})
    backup_dir = tmp_path / "backups"
    online_backup(repo, backup_dir)
    monkeypatch.setenv("POLYMARKET_LLM_API_KEY", "test-secret-never-returned")
    report = health(repo, backup_dir)
    assert report["collector"]["latest_status"] == "SUCCEEDED"
    assert report["collector"]["latest_duration_seconds"] == 1200
    assert {"active_lock", "stale_lock", "next_expected_at", "freshness_age_seconds"} <= report["collector"].keys()
    assert {"writable", "integrity_status", "db_size_bytes", "wal_size_bytes", "free_bytes"} <= report["database"].keys()
    assert report["backup"]["latest_verified"] is True
    assert report["backup"]["latest_age_seconds"] is not None
    assert report["provider"]["configured"] is True
    assert "test-secret" not in str(report)
    assert {"llm_forecasts", "unresolved", "resolved", "scored"} <= report["research"].keys()
    assert report["database"]["integrity_status"] == "schema_read_ok"
    assert report["identity"]["database_user_version"] == 2
    assert report["identity"]["storage_schema"] == UNIVERSE_MANIFEST_SCHEMA
    assert report["identity"]["methodology"]["eligibility"]


def test_interruption_records_failed_run_and_releases_lease(tmp_path, monkeypatch):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    monkeypatch.setattr("dashboard.polymarket.operations.disk_guard", lambda repo: {"safe": True})
    class Interrupted(RuntimeError):
        interruption_code = "TIMEOUT"
    monkeypatch.setattr("dashboard.polymarket.__main__.run_forecast_cohort",
                        lambda *args, **kwargs: (_ for _ in ()).throw(Interrupted()))
    try:
        run_collection(repo, max_forecasts=1, dry_run=True, page_size=500, clob_workers=1, incremental=True)
    except Interrupted:
        pass
    with repo.connect() as connection:
        run = connection.execute("SELECT status,error_code FROM collection_runs").fetchone()
        assert tuple(run) == ("FAILED", "TIMEOUT")
        assert connection.execute("SELECT COUNT(*) FROM collection_leases").fetchone()[0] == 0
