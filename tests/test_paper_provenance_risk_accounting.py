import hashlib
import json
import time

from dashboard.decision_engine import FlowContext, MarketContext, RiskContext, TimeframeContext, evaluate_decision
from dashboard.paper_api import ACCOUNTING_VERSION, PaperService, now_iso
from dashboard.signal_identity import canonical_json
from dashboard.strategy_rules import StrategyParameters


def analysis(service, cvd=10.0, oi=1.0, atr=2.0):
    ts = int(time.time())
    params = StrategyParameters(cooldown_bars=0)
    frames = {name: {"candle_close_ts": ts, "close": 100, "fast_ma": 99, "slow_ma": 98, "trend": "Bullish"} for name in ("1H", "4H")}
    decision = evaluate_decision(params, MarketContext("BTC-USDT", "15m", ts, 100, {"fast_ma": 99, "slow_ma": 98, "ema": 100, "rsi": 50, "atr": atr, "volume_ratio": 2}), TimeframeContext(frames, ("1H", "4H"), False, "multi-timeframe"), FlowContext(True, cvd, oi, "test", ts, ts, time.time_ns()), RiskContext(), "live-mtf-flow-v1").to_dict()
    result = {**decision, "price": 100.0, "ema20": 100.0, "rsi14": 50.0, "atr14": atr, "volume_ratio": 2.0, "updated_at": now_iso()}
    with service._connect() as conn:
        conn.execute("INSERT INTO decision_evaluations(evaluation_id,signal_setup_id,source,instrument,execution_timeframe,candle_close_ts,strategy_version,decision_engine_version,action,decision_payload,gate_payload,flow_payload,evaluation_timestamp,market_snapshot_ts,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (decision["evaluation_id"], decision["signal_setup_id"], "PAPER", "BTC-USDT", "15m", ts, decision["strategy_version"], decision["decision_engine_version"], decision["action"], canonical_json(decision), canonical_json(decision["gate_results"]), canonical_json(decision["flow_context"]), result["updated_at"], ts, result["updated_at"]))
    return result


def rationale(service, item):
    with service._connect() as conn:
        params, _ = service._active_strategy()
        value = service._build_rationale(item, params, service._account_state(conn))
    value["rationale_hash"] = hashlib.sha256(canonical_json(value).encode()).hexdigest()
    return value


def test_live_flow_evaluations_are_distinct_and_order_keeps_exact_authorizer(tmp_path):
    service = PaperService(tmp_path / "paper.db")
    first, second = analysis(service, 10, 1), analysis(service, 20, 2)
    assert first["signal_setup_id"] == second["signal_setup_id"]
    assert first["evaluation_id"] != second["evaluation_id"]
    with service._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM decision_evaluations").fetchone()[0] == 2
    created = service.create_order(first, rationale(service, first))
    assert created["ok"]
    with service._connect() as conn:
        trade = conn.execute("SELECT evaluation_id,trade_rationale FROM paper_trades WHERE id=?", (created["trade_id"],)).fetchone()
    payload = json.loads(trade["trade_rationale"])
    assert trade["evaluation_id"] == first["evaluation_id"] == payload["evaluation_id"]
    assert payload["cvd_value"] == 10 and payload["oi_value"] == 1


def test_rationale_validation_is_atomic_and_costed(tmp_path):
    service = PaperService(tmp_path / "paper.db")
    item = analysis(service, atr=1)
    good = rationale(service, item)
    with service._connect() as conn:
        before = [conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("paper_account", "paper_trades")]
    bad = dict(good); bad.pop("rationale_hash")
    assert not service.create_order(item, bad)["ok"]
    stale = dict(good); stale["decision_timestamp"] = "2000-01-01T00:00:00+00:00"; stale["rationale_hash"] = hashlib.sha256(canonical_json({k:v for k,v in stale.items() if k != "rationale_hash"}).encode()).hexdigest()
    assert not service.create_order(item, stale)["ok"]
    assert not service.create_order({**item, "evaluation_id": "wrong"}, good)["ok"]
    with service._connect() as conn:
        after = [conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("paper_account", "paper_trades")]
    assert before == after
    created = service.create_order(item, good); assert created["ok"]
    with service._connect() as conn:
        trade = dict(conn.execute("SELECT * FROM paper_trades WHERE id=?", (created["trade_id"],)).fetchone())
    assert trade["simulated_entry_fill"] > trade["theoretical_entry_price"]
    assert trade["entry_fee"] > 0 and trade["binding_cap"] in {"risk", "funds", "maximum_notional"}
    assert trade["accounting_version"] == ACCOUNTING_VERSION


def test_sizing_caps_exit_costs_and_market_candles_are_immutable(tmp_path):
    service = PaperService(tmp_path / "paper.db")
    a, b = analysis(service, atr=1), analysis(service, atr=4)
    ra, rb = rationale(service, a), rationale(service, b)
    assert ra["requested_risk_amount"] == rb["requested_risk_amount"]
    assert ra["raw_risk_derived_quantity"] > rb["raw_risk_derived_quantity"]
    assert ra["final_quantity"] == min(ra["raw_risk_derived_quantity"], ra["funds_cap_quantity"], ra["notional_cap_quantity"])
    with service._connect() as conn:
        conn.execute("INSERT INTO market_candles VALUES(?,?,?,?,?,?,?,?)", ("BTC-USDT", "15m", 1, 1, 1, 1, 1, 1))
        before = hashlib.sha256(canonical_json([tuple(row) for row in conn.execute("SELECT * FROM market_candles")]).encode()).hexdigest()
    assert service.create_order(a, ra)["ok"]
    service.monitor_positions("BTC-USDT", float(ra["target_price"]) + 1)
    with service._connect() as conn:
        trade = dict(conn.execute("SELECT * FROM paper_trades").fetchone())
        after = hashlib.sha256(canonical_json([tuple(row) for row in conn.execute("SELECT * FROM market_candles")]).encode()).hexdigest()
    assert before == after
    assert trade["exit_fee"] > 0 and trade["simulated_exit_fill"] < trade["theoretical_exit_price"]
    assert abs(trade["net_pnl"] - (trade["gross_pnl"] - trade["entry_fee"] - trade["exit_fee"])) < 1e-8
