import time

from dashboard.paper_api import FLOW_DISPLAY_WINDOW_SECONDS, PaperService


def test_flow_collector_start_is_idempotent_and_stop_is_clean(tmp_path, monkeypatch):
    service = PaperService(tmp_path / "flow.db")
    started = []

    def fake_run():
        started.append(True)
        # Keep the thread alive until stop asks it to exit.
        while not service.flow_collectors.shutting_down:
            time.sleep(.005)

    monkeypatch.setattr(service.flow_collectors, "_run", fake_run)
    assert service.flow_collectors.start() is True
    assert service.flow_collectors.start() is False
    service.flow_collectors.stop()
    assert started == [True]
    assert service.flow_collectors.health()["trades_collector"]["status"] == "OFFLINE"


def test_display_window_is_six_hours_and_partial_coverage_is_not_live(tmp_path, monkeypatch):
    service = PaperService(tmp_path / "flow.db")
    now = 2_000_000
    monkeypatch.setattr("dashboard.paper_api.time.time", lambda: now)
    with service._connect() as conn:
        conn.execute("INSERT INTO flow_trade_buckets VALUES(?,?,?,?,?)", ("BTC-USDT", now - 60, 30, 10, 2))
    service.flow_collectors.trades.update(status="LIVE", last_trade_at="1970-01-24T03:33:20+00:00")
    flow = service._professional_flow("BTC-USDT")
    assert flow["window_seconds"] == FLOW_DISPLAY_WINDOW_SECONDS == 21600
    assert flow["collector_status"] == "PARTIAL"
    assert flow["flow_ready"] is False
    assert flow["available"] is True
    assert flow["coverage_ratio"] < 1


def test_stale_buckets_are_not_presented_as_live(tmp_path, monkeypatch):
    service = PaperService(tmp_path / "flow.db")
    now = 2_000_000
    monkeypatch.setattr("dashboard.paper_api.time.time", lambda: now)
    with service._connect() as conn:
        conn.execute("INSERT INTO flow_trade_buckets VALUES(?,?,?,?,?)", ("ETH-USDT", now - 500, 30, 10, 2))
    flow = service._professional_flow("ETH-USDT")
    assert flow["stale"] is True
    assert flow["available"] is False
    assert flow["collector_status"] != "LIVE"


def test_flow_health_does_not_expose_absolute_database_path(tmp_path):
    service = PaperService(tmp_path / "flow.db")
    health = service.flow_health()
    assert health["database_path_identifier"] == "flow.db"
    assert str(tmp_path) not in str(health)
    assert health["oi_collector"]["status"] == "OFFLINE"


def test_seven_day_retention_keeps_active_six_hour_buckets(tmp_path, monkeypatch):
    service = PaperService(tmp_path / "flow.db")
    now = 2_000_000
    monkeypatch.setattr("dashboard.paper_api.time.time", lambda: now)
    with service._connect() as conn:
        conn.execute("INSERT INTO flow_trade_buckets VALUES(?,?,?,?,?)", ("BTC-USDT", now - FLOW_DISPLAY_WINDOW_SECONDS, 1, 0, 1))
        conn.execute("INSERT INTO flow_trade_buckets VALUES(?,?,?,?,?)", ("BTC-USDT", now - 8 * 86400, 1, 0, 1))
    service._prune_flow_retention()
    with service._connect() as conn:
        rows = conn.execute("SELECT ts FROM flow_trade_buckets ORDER BY ts").fetchall()
    assert [row[0] for row in rows] == [now - FLOW_DISPLAY_WINDOW_SECONDS]
