from dashboard.paper_api import PaperService


def test_cvd_series_aggregates_same_second_for_chart(tmp_path, monkeypatch):
    service = PaperService(tmp_path / "flow.db")
    trades = [
        {"ts": "2000500", "sz": "2", "px": "10", "side": "buy"},
        {"ts": "2000100", "sz": "1", "px": "10", "side": "sell"},
        {"ts": "1000900", "sz": "1", "px": "10", "side": "buy"},
    ]

    def response(url):
        if "market/trades" in url:
            return {"data": trades}
        return {"data": [{"oiUsd": "100000"}]}

    monkeypatch.setattr(service, "_json", response)
    flow = service._flow_metrics("BTC-USDT")
    assert [point["time"] for point in flow["cvd_series"]] == [1000, 2000]
    assert len({point["time"] for point in flow["cvd_series"]}) == len(flow["cvd_series"])
    assert flow["cvd_series"][-1]["value"] == flow["cvd_delta"] == 20
    assert flow["decision_cvd_delta"] == 0.0
