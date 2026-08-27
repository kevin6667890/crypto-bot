from dashboard.polymarket.models import stable_hash
from dashboard.polymarket.repository import PolymarketRepository


def test_staged_universe_handles_150k_markets_without_complete_market_list(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    captured = "2026-08-27T00:00:00+00:00"
    stage = repo.begin_universe("selection-v2", "policy-hash", captured,
                                pagination_metadata={"version": "synthetic-page-v1"})
    maximum_page, total = 0, 150_001
    for start in range(0, total, 257):
        stop = min(start + 257, total)
        # Deliberately reverse each page: global canonical order is rebuilt by
        # staged SQL ordering rather than a collector-held universe list.
        page = [{"id": f"m-{item:06d}"} for item in range(stop - 1, start - 1, -1)]
        maximum_page = max(maximum_page, len(page))
        repo.append_universe_page(stage, page, [stable_hash(market) for market in page])
    universe_id = repo.finalize_universe(stage)
    assert maximum_page == 257
    with repo.connect() as connection:
        assert connection.execute("SELECT status FROM universe_stages WHERE stage_id=?", (stage,)).fetchone()[0] == "COMPLETE"
        snapshot = connection.execute("SELECT market_count,manifest_hash FROM universe_snapshots WHERE universe_snapshot_id=?", (universe_id,)).fetchone()
        assert tuple(snapshot)[0] == total
        assert tuple(snapshot)[1]  # canonical manifest is persisted, not a list return value


def test_interrupted_staged_universe_never_creates_complete_snapshot(tmp_path):
    repo = PolymarketRepository(tmp_path / "pm.sqlite")
    stage = repo.begin_universe("selection-v2", "policy-hash", "2026-08-27T00:00:00+00:00")
    repo.append_universe_page(stage, [{"id": "m-1"}], ["hash-1"])
    repo.fail_universe(stage, "SyntheticInterrupt")
    with repo.connect() as connection:
        assert connection.execute("SELECT status FROM universe_stages WHERE stage_id=?", (stage,)).fetchone()[0] == "FAILED"
        assert connection.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 0
