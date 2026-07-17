import sqlite3
import json
from urllib.error import HTTPError

from dashboard import okx_history
from dashboard.okx_history import OkxHistoryClient
from dashboard.research_repository import ResearchRepository


def test_migration_preserves_existing_paper_data(tmp_path):
    database = tmp_path / "paper.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE paper_trades(id INTEGER PRIMARY KEY, instrument TEXT)")
        connection.execute("INSERT INTO paper_trades VALUES(1, 'BTC-USDT')")
    ResearchRepository(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT instrument FROM paper_trades WHERE id=1").fetchone()[0] == "BTC-USDT"
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"strategy_configs", "backtest_runs", "backtest_trades", "backtest_equity", "historical_candles", "walk_forward_runs"}.issubset(tables)


def test_default_strategy_presets_are_idempotent(tmp_path):
    database = tmp_path / "research.db"
    first = ResearchRepository(database)
    second = ResearchRepository(database)
    assert {item["name"] for item in first.strategies()} == {"Conservative", "Balanced", "Aggressive"}
    assert len(second.strategies()) == 3


def test_okx_rate_limit_is_retried(monkeypatch):
    calls = 0

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self): return json.dumps({"code": "0", "data": [["1"]]}).encode()

    def fake_open(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError("https://www.okx.com", 429, "limited", {}, None)
        return Response()

    monkeypatch.setattr(okx_history, "urlopen", fake_open)
    monkeypatch.setattr(okx_history.time, "sleep", lambda _seconds: None)
    assert OkxHistoryClient._request({"instId": "BTC-USDT", "bar": "15m"}) == [["1"]]
    assert calls == 2
