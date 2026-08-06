from __future__ import annotations
import copy,sqlite3
import pytest
from dashboard.ai_market_analysis.position_context import none_position_context,paper_position_context,user_position_context
from dashboard.ai_market_analysis.position_plan_models import normalize_user_position_plan,plan_at_decision_time
from .ai4_helpers import position_plan

def test_none_has_no_invented_position():
 p=none_position_context("ETH-USDT-SWAP");assert p["source"]=="NONE" and p["average_cost"] is None and "未提供持仓信息" in p["limitations"][0]
def test_single_and_multiple_entries_weight_average():
 raw=position_plan();p=normalize_user_position_plan(raw);assert p["average_cost"]==1835
 raw["entries"].append({"price":1845,"quantity":10,"timestamp":"2027-10-02T00:00:00Z","source":"USER_DECLARED"});p=normalize_user_position_plan(raw);assert p["average_cost"]==1840
def test_partial_exits_remaining_and_targets():
 p=normalize_user_position_plan(position_plan());assert p["remaining_quantity"]==2 and sum(x["quantity"] for x in p["realised_exits"])==8 and all(x["completed"] for x in p["original_targets"])
def test_mostly_completed_and_timeframe_drift():
 p=user_position_context(position_plan(),"ETH-USDT-SWAP","2027-11-01T00:00:00Z",1900);assert {"PLAN_MOSTLY_COMPLETED","TIMEFRAME_DRIFT_RISK"}<=set(p["discipline_warnings"])
def test_stop_invalidated():
 p=user_position_context(position_plan(),"ETH-USDT-SWAP","2027-11-01T00:00:00Z",1800);assert "STOP_INVALIDATED" in p["discipline_warnings"]
@pytest.mark.parametrize("field,value",[("instrument","SOL-USDT-SWAP"),("source","PAPER")])
def test_source_and_instrument_mismatch(field,value):
 raw=position_plan();raw[field]=value
 if field=="source":
  with pytest.raises(ValueError):normalize_user_position_plan(raw)
 else:
  with pytest.raises(ValueError):user_position_context(raw,"ETH-USDT-SWAP","2027-11-01T00:00:00Z",1900)
@pytest.mark.parametrize("value",[float("nan"),float("inf"),-1])
def test_invalid_quantities_rejected(value):
 raw=position_plan();raw["entries"][0]["quantity"]=value
 with pytest.raises(ValueError):normalize_user_position_plan(raw)
def test_remaining_mismatch_rejected():
 raw=position_plan();raw["remaining_quantity"]=9
 with pytest.raises(ValueError):normalize_user_position_plan(raw)
def test_identity_stable_and_change_sensitive():
 assert normalize_user_position_plan(position_plan())["payload_hash"]==normalize_user_position_plan(copy.deepcopy(position_plan()))["payload_hash"]
 raw=position_plan();raw["entries"][0]["price"]+=1;assert normalize_user_position_plan(raw)["payload_hash"]!=normalize_user_position_plan(position_plan())["payload_hash"]
def test_future_plan_excluded():
 assert not plan_at_decision_time(normalize_user_position_plan(position_plan()),"2027-09-01T00:00:00Z")
def _paper(path,status="OPEN",quantity=2):
 c=sqlite3.connect(path);c.execute("CREATE TABLE paper_trades(id INTEGER,instrument TEXT,side TEXT,entry REAL,stop_loss REAL,take_profit REAL,status TEXT,position_size REAL,mark_price REAL,pnl_usdt REAL,net_pnl REAL,created_at TEXT,closed_at TEXT,execution_timeframe TEXT,trade_rationale TEXT,accounting_version TEXT,risk_amount REAL,actual_risk_amount REAL)");c.execute("INSERT INTO paper_trades VALUES(1,'ETH-USDT','BUY',1835,1810,1890,?,?,1900,NULL,NULL,'2027-10-01T00:00:00Z',NULL,'15m','rebound','v2',10,10)",(status,quantity));c.commit();c.close()
@pytest.mark.parametrize("status,expected",[("OPEN","COMPLETE"),("WIN","COMPLETE")])
def test_paper_open_closed(tmp_path,status,expected):
 p=tmp_path/"p.db";_paper(p,status);out=paper_position_context(p,"ETH-USDT-SWAP",1900);assert out["source"]=="PAPER" and out["status"]==expected
def test_paper_legacy_partial(tmp_path):
 p=tmp_path/"p.db";_paper(p,"OPEN",None);out=paper_position_context(p,"ETH-USDT-SWAP",1900);assert out["status"]=="PARTIAL" and out["remaining_quantity"] is None
