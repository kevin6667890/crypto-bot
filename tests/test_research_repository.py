import sqlite3

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
