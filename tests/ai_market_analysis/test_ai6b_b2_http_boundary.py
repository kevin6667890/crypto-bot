from __future__ import annotations

from datetime import datetime,timedelta,timezone
from io import BytesIO
import json
from pathlib import Path
import sqlite3

import pytest

from dashboard import paper_api
from dashboard.ai_market_analysis.readonly_adapter import MAX_ORDERFLOW_QUERY_SECONDS,ReadOnlyOrderflowAdapter
from dashboard.ai_market_analysis.report_api import build_base_context_from_stores
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.versions import TIMEFRAME_SECONDS
from scripts.ai6b_b2_report_request import REPORT_ENDPOINT,REPORT_METHOD,serialize_b2_http_request

INSTRUMENTS=("BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP")
MODES=("QUICK","FULL")
POSITION_SOURCES=("NONE","PAPER")


def _decision()->datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)-timedelta(minutes=2)


def _seed_paper(path:Path,instrument:str,decision:int,daily_count:int=1500)->None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
          CREATE TABLE historical_candles(
            instrument TEXT,timeframe TEXT,ts INTEGER,open REAL,high REAL,low REAL,
            close REAL,volume REAL,confirmed INTEGER,source TEXT,
            PRIMARY KEY(instrument,timeframe,ts));
          CREATE INDEX idx_historical_range ON historical_candles(instrument,timeframe,ts);
          CREATE TABLE paper_trades(
            id INTEGER,instrument TEXT,side TEXT,entry REAL,stop_loss REAL,take_profit REAL,
            status TEXT,position_size REAL,mark_price REAL,pnl_usdt REAL,net_pnl REAL,
            created_at TEXT,closed_at TEXT,execution_timeframe TEXT,trade_rationale TEXT,
            accounting_version TEXT,risk_amount REAL,actual_risk_amount REAL);
        """)
        rows=[]
        for timeframe,count in (("15m",240),("1H",240),("4H",240),("1D",daily_count)):
            width=TIMEFRAME_SECONDS[timeframe]
            candle_end=decision-decision%width
            start=candle_end-count*width
            for index in range(count):
                close=100.0+index*.01
                rows.append((instrument.removesuffix("-SWAP"),timeframe,start+index*width,
                             close-.5,close+1,close-1,close,100+index,1,"ai6b-http-boundary"))
        connection.executemany("INSERT INTO historical_candles VALUES(?,?,?,?,?,?,?,?,?,?)",rows)


def _empty_micro(path:Path)->None:
    sqlite3.connect(path).close()


def _configure(monkeypatch:pytest.MonkeyPatch,tmp_path:Path,instrument:str,decision:datetime):
    paper=tmp_path/"paper.db";micro=tmp_path/"micro.db";reports=tmp_path/"reports.db"
    _seed_paper(paper,instrument,int(decision.timestamp()));_empty_micro(micro);migrate_database(reports)
    monkeypatch.setenv("ADMIN_TOKEN","test-admin")
    monkeypatch.setenv("AI_MARKET_REPORTS_ENABLED","true")
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED","false")
    monkeypatch.setattr(paper_api,"DB_PATH",paper)
    monkeypatch.setattr(paper_api.MICROSTRUCTURE,"path",micro)
    monkeypatch.setattr(paper_api,"AI_REPORT_REPOSITORY",ReportRepository(reports))
    return paper,micro,reports


def _post(monkeypatch:pytest.MonkeyPatch,request:dict)->tuple[dict,int]:
    assert request["method"]==REPORT_METHOD and request["endpoint"]==REPORT_ENDPOINT
    captured=[]
    handler=object.__new__(paper_api.Handler)
    handler.path=request["endpoint"];handler.headers=request["headers"]
    handler.rfile=BytesIO(request["body"]);handler.client_address=("127.0.0.1",0)
    handler._limited=lambda *_:False
    handler._send=lambda payload,status=200:captured.append((payload,int(status)))
    handler.do_POST()
    return captured[-1]


@pytest.mark.parametrize("instrument",INSTRUMENTS)
@pytest.mark.parametrize("mode",MODES)
@pytest.mark.parametrize("position_source",POSITION_SOURCES)
def test_production_http_request_contract_matrix(
    tmp_path,monkeypatch,instrument,mode,position_source
):
    decision=_decision();_configure(monkeypatch,tmp_path,instrument,decision)
    request=serialize_b2_http_request(
        instrument,mode,position_source,decision,authorization="test-admin"
    )
    payload,status=_post(monkeypatch,request)
    assert status==202,payload
    assert payload["created"] is True
    assert payload["request_id"].startswith("request_")


def test_btc_quick_none_long_daily_history_regression(tmp_path,monkeypatch):
    decision=_decision();paper,micro,_=_configure(monkeypatch,tmp_path,"BTC-USDT-SWAP",decision)
    calls=[];original=ReadOnlyOrderflowAdapter.read
    def capture(self,instrument,start,end,resolution="15m"):
        calls.append((instrument,start,end,resolution))
        return original(self,instrument,start,end,resolution)
    monkeypatch.setattr(ReadOnlyOrderflowAdapter,"read",capture)
    request=serialize_b2_http_request(
        "BTC-USDT-SWAP","QUICK","NONE",decision,authorization="test-admin"
    )
    payload,status=_post(monkeypatch,request)
    assert status==202,payload
    assert calls and calls[0][2]-calls[0][1]==MAX_ORDERFLOW_QUERY_SECONDS
    with sqlite3.connect(paper) as connection:
        assert connection.execute(
            "SELECT count(*) FROM historical_candles WHERE timeframe='1D'"
        ).fetchone()[0]==1500
    assert micro.exists()


def test_long_price_history_only_bounds_orderflow_and_preserves_daily_limit(tmp_path,monkeypatch):
    decision=_decision();paper=tmp_path/"paper.db";micro=tmp_path/"micro.db"
    _seed_paper(paper,"ETH-USDT-SWAP",int(decision.timestamp()));_empty_micro(micro)
    flow_calls=[];candle_calls=[]
    original_flow=ReadOnlyOrderflowAdapter.read
    original_candles=paper_api.BoundedMarketDataReaderV2.candles
    def capture_flow(self,instrument,start,end,resolution="15m"):
        flow_calls.append((start,end));return original_flow(self,instrument,start,end,resolution)
    def capture_candles(self,instrument,timeframe,as_of,limit):
        candle_calls.append((timeframe,limit));return original_candles(self,instrument,timeframe,as_of,limit)
    monkeypatch.setattr(ReadOnlyOrderflowAdapter,"read",capture_flow)
    monkeypatch.setattr("dashboard.ai_market_analysis.report_api.BoundedMarketDataReaderV2.candles",capture_candles)
    context=build_base_context_from_stores(
        json.loads(serialize_b2_http_request("ETH-USDT-SWAP","FULL","NONE",decision,
                                            authorization="test-admin")["body"]),paper,micro
    )
    assert ("1D",1500) in candle_calls
    assert flow_calls[0][1]-flow_calls[0][0]==MAX_ORDERFLOW_QUERY_SECONDS
    assert len(next(item for item in context["timeframe_structures"] if item["timeframe"]=="1D")["source_bar_timestamps"])==1500


def test_short_history_keeps_original_orderflow_start(tmp_path,monkeypatch):
    decision=_decision();paper=tmp_path/"paper.db";micro=tmp_path/"micro.db"
    _seed_paper(paper,"ETH-USDT-SWAP",int(decision.timestamp()),daily_count=100);_empty_micro(micro)
    calls=[]
    monkeypatch.setattr(ReadOnlyOrderflowAdapter,"read",lambda self,instrument,start,end,resolution="15m":calls.append((start,end)) or {})
    payload=json.loads(serialize_b2_http_request("ETH-USDT-SWAP","FULL","NONE",decision,
                                                authorization="test-admin")["body"])
    build_base_context_from_stores(payload,paper,micro)
    with sqlite3.connect(paper) as connection:
        expected=connection.execute("SELECT min(ts) FROM historical_candles").fetchone()[0]
    assert calls[0][0]==expected


def test_orderflow_causal_cutoff_and_missing_quality_are_preserved(tmp_path):
    decision=int(_decision().timestamp());paper=tmp_path/"paper.db";micro=tmp_path/"micro.db"
    _seed_paper(paper,"ETH-USDT-SWAP",decision,daily_count=100)
    with sqlite3.connect(micro) as connection:
        connection.execute("CREATE TABLE cvd_aggregates(instrument TEXT,resolution TEXT,bucket_ms INTEGER,payload_json TEXT,PRIMARY KEY(instrument,resolution,bucket_ms))")
        connection.execute("INSERT INTO cvd_aggregates VALUES(?,?,?,?)",(
            "ETH-USDT-SWAP","4H",(decision-1)*1000,json.dumps({"value":1})
        ))
    payload={"instrument":"ETH-USDT-SWAP","decision_time":datetime.fromtimestamp(
        decision,timezone.utc).isoformat().replace("+00:00","Z"),"mode":"FULL"}
    context=build_base_context_from_stores(payload,paper,micro)
    assert context["data_quality"]["overall"]=="PARTIAL"
    assert "oi" in context["data_quality"]["missing_sources"]
    adapter=ReadOnlyOrderflowAdapter(micro)
    rows=adapter.read("ETH-USDT-SWAP",decision-30*86400,decision,"4H")["cvd"]
    assert rows and all(row["bucket_ms"]<decision*1000 for row in rows)


@pytest.mark.parametrize("change",[
    {"instrument":"DOGE-USDT-SWAP"},{"mode":"DEEP"},{"position_source":"USER_DECLARED"},
    {"decision_time":(_decision()+timedelta(days=1)).isoformat().replace("+00:00","Z")},
    {"unknown_field":True},
])
def test_production_http_contract_negative_cases(tmp_path,monkeypatch,change):
    decision=_decision();_configure(monkeypatch,tmp_path,"ETH-USDT-SWAP",decision)
    request=serialize_b2_http_request("ETH-USDT-SWAP","FULL","NONE",decision,authorization="test-admin")
    body=json.loads(request["body"]);body.update(change)
    request["body"]=json.dumps(body,separators=(",",":")).encode()
    request["headers"]["Content-Length"]=str(len(request["body"]))
    payload,status=_post(monkeypatch,request)
    assert status in {400,403},payload


def test_production_http_contract_rejects_malformed_and_missing_body(tmp_path,monkeypatch):
    decision=_decision();_configure(monkeypatch,tmp_path,"ETH-USDT-SWAP",decision)
    request=serialize_b2_http_request("ETH-USDT-SWAP","FULL","NONE",decision,authorization="test-admin")
    request["body"]=b"{";request["headers"]["Content-Length"]="1"
    assert _post(monkeypatch,request)==({"error":"Invalid JSON body"},400)
    request=serialize_b2_http_request("ETH-USDT-SWAP","FULL","NONE",decision,authorization="test-admin")
    body=json.loads(request["body"]);body.pop("instrument")
    request["body"]=json.dumps(body,separators=(",",":")).encode()
    request["headers"]["Content-Length"]=str(len(request["body"]))
    assert _post(monkeypatch,request)[1]==400
