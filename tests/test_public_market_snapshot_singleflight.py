from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from dashboard import paper_api


def test_public_market_snapshot_singleflights_concurrent_clients(monkeypatch):
    calls = []
    paper_api._PUBLIC_MARKET_SNAPSHOT_CACHE.clear()

    def build(instrument, as_of, execution_timeframe):
        calls.append((instrument, as_of, execution_timeframe))
        paper_api.time.sleep(0.02)
        return SimpleNamespace(to_dict=lambda: {
            "instrument": instrument,
            "execution_timeframe": execution_timeframe,
            "causal_cutoff": as_of,
        })

    monkeypatch.setattr(paper_api, "canonical_market_snapshot", build)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(
            lambda _index: paper_api.public_canonical_market_snapshot(
                "ETH-USDT-SWAP", "15m",
            ),
            range(3),
        ))

    assert len(calls) == 1
    assert results[0] == results[1] == results[2]


def test_explicit_cutoff_contract_does_not_use_public_cache(monkeypatch):
    source = paper_api.Path(paper_api.__file__).read_text(encoding="utf-8")
    endpoint = source[source.index('elif parsed.path == "/api/market/snapshot"'):]
    endpoint = endpoint[:endpoint.index('elif parsed.path == "/api/market/context"')]
    assert "if raw_as_of is None" in endpoint
    assert "public_canonical_market_snapshot" in endpoint
    assert "canonical_market_snapshot(" in endpoint


def test_paper_cycle_publishes_the_same_canonical_payload_for_public_reads():
    source = paper_api.Path(paper_api.__file__).read_text(encoding="utf-8")
    start = source.index("def analyze(")
    cycle = source[start:source.index("def _active_strategy", start)]
    assert "_publish_public_market_snapshot(" in cycle
    assert "snapshot.to_dict()" in cycle
    assert 120 < paper_api.PUBLIC_MARKET_SNAPSHOT_CACHE_SECONDS < 15 * 60
