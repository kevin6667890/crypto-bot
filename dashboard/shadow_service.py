"""Persistent counterfactual paper execution for isolated shadow candidates."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from typing import Any

try:
    from decision_engine import FlowContext, MarketContext, RiskContext, TimeframeContext, evaluate_decision
    from signal_identity import config_hash
    from strategy_rules import STRATEGY_PRESETS, calculate_indicators, validate_parameters
    from validation_repository import ValidationRepository, utc_now
except ImportError:
    from .decision_engine import FlowContext, MarketContext, RiskContext, TimeframeContext, evaluate_decision
    from .signal_identity import config_hash
    from .strategy_rules import STRATEGY_PRESETS, calculate_indicators, validate_parameters
    from .validation_repository import ValidationRepository, utc_now

SHADOW_VERSION = "shadow-mtf-flow-v1"


class ShadowService:
    def __init__(self, repository: ValidationRepository): self.repository = repository

    def ensure_default_candidates(self) -> None:
        with self.repository.connect() as c: existing={row[0] for row in c.execute("SELECT name FROM shadow_strategies")}
        for name,parameters in STRATEGY_PRESETS.items():
            if name not in existing:self.create({"name":name,"parameters":parameters,"instruments":["BTC-USDT","ETH-USDT","SOL-USDT"],"virtual_initial_capital":10000})

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        item = dict(row)
        for key in ("parameters", "instruments", "open_positions"):
            if key in item and isinstance(item[key], str): item[key] = json.loads(item[key])
        item["enabled"] = bool(item.get("enabled")); return item

    def list(self, include_metrics: bool = True) -> list[dict[str, Any]]:
        with self.repository.connect() as c:
            rows = c.execute("SELECT s.*,st.current_equity,st.open_positions,st.closed_trades,st.total_r,st.fees,st.drawdown FROM shadow_strategies s LEFT JOIN shadow_strategy_states st ON st.shadow_strategy_id=s.shadow_strategy_id ORDER BY s.id DESC").fetchall()
            output=[]
            for row in rows:
                item=self._decode(row)
                if not include_metrics:output.append(item);continue
                sid=item["shadow_strategy_id"];trades=c.execute("SELECT instrument,pnl FROM shadow_trades WHERE shadow_strategy_id=? AND status='CLOSED'",(sid,)).fetchall();wins=sum(float(t["pnl"] or 0)>0 for t in trades);profit=sum(max(float(t["pnl"] or 0),0) for t in trades);loss=abs(sum(min(float(t["pnl"] or 0),0) for t in trades));latest=c.execute("SELECT payload FROM shadow_decisions WHERE shadow_strategy_id=? ORDER BY candle_close_ts DESC LIMIT 1",(sid,)).fetchone();decisions=[json.loads(x[0]) for x in c.execute("SELECT payload FROM shadow_decisions WHERE shadow_strategy_id=? ORDER BY id DESC LIMIT 1000",(sid,)).fetchall()];gate_evaluated=gate_passed=near=0
                for decision in decisions:
                    gates=[g for g in decision.get("gate_results",[]) if g.get("applicable",True) and g.get("key")!="final_entry_allowed"];gate_evaluated+=len(gates);gate_passed+=sum(g.get("passed") for g in gates);failed=sum(not g.get("passed") and g.get("blocking",True) for g in gates);near+=int(decision.get("warmed") and decision.get("bias")!="WAIT" and failed<=2 and not decision.get("entry_allowed"))
                per_asset={}
                for trade in trades:per_asset[trade["instrument"]]=per_asset.get(trade["instrument"],0)+float(trade["pnl"] or 0)
                item.update({"win_rate":wins/len(trades)*100 if trades else None,"profit_factor":profit/loss if loss else None,"total_return":(float(item.get("current_equity") or item["virtual_initial_capital"])/float(item["virtual_initial_capital"])-1)*100,"per_asset_pnl":per_asset,"latest_decision":json.loads(latest[0]) if latest else None,"gate_pass_rate":gate_passed/gate_evaluated*100 if gate_evaluated else None,"near_miss_count":near,"sample_status":"Sufficient" if len(trades)>=30 else "Insufficient Data"});output.append(item)
        return output

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()[:80]
        if not name: raise ValueError("Candidate name is required.")
        parameters = asdict(validate_parameters(payload.get("parameters"))); instruments = sorted(set(payload.get("instruments") or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]))
        if any(item not in {"BTC-USDT", "ETH-USDT", "SOL-USDT"} for item in instruments): raise ValueError("Unsupported shadow instrument.")
        initial = float(payload.get("virtual_initial_capital", parameters["initial_capital"])); cfg = config_hash(parameters); sid = str(uuid.uuid4()); now = utc_now()
        with self.repository.connect() as c:
            duplicate = c.execute("SELECT 1 FROM shadow_strategies WHERE config_hash=? AND instruments=? AND status IN ('RUNNING','PAUSED')", (cfg, json.dumps(instruments))).fetchone()
            if duplicate: raise ValueError("An identical active shadow experiment already exists.")
            c.execute("INSERT INTO shadow_strategies(shadow_strategy_id,name,strategy_version,config_hash,parameters,instruments,enabled,status,virtual_initial_capital,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (sid, name, SHADOW_VERSION, cfg, json.dumps(parameters), json.dumps(instruments), 0, "DRAFT", initial, now, now))
            c.execute("INSERT INTO shadow_strategy_states(shadow_strategy_id,current_equity,cash,open_positions,peak_equity,updated_at) VALUES(?,?,?,?,?,?)", (sid, initial, initial, "{}", initial, now))
        return next(item for item in self.list() if item["shadow_strategy_id"] == sid)

    def action(self, sid: str, action: str) -> dict[str, Any]:
        valid = {"start": ({"DRAFT", "STOPPED"}, "RUNNING"), "pause": ({"RUNNING"}, "PAUSED"), "resume": ({"PAUSED"}, "RUNNING"), "stop": ({"RUNNING", "PAUSED"}, "STOPPED"), "archive": ({"DRAFT", "STOPPED"}, "ARCHIVED")}
        if action not in valid: raise ValueError("Unsupported shadow action.")
        allowed, target = valid[action]; now = utc_now()
        with self.repository.connect() as c:
            row = c.execute("SELECT * FROM shadow_strategies WHERE shadow_strategy_id=?", (sid,)).fetchone()
            if not row: raise ValueError("Shadow strategy not found.")
            if row["status"] not in allowed: raise ValueError(f"Cannot {action} a {row['status']} experiment.")
            if target == "RUNNING":
                duplicate = c.execute("SELECT 1 FROM shadow_strategies WHERE shadow_strategy_id!=? AND config_hash=? AND instruments=? AND status IN ('RUNNING','PAUSED')", (sid, row["config_hash"], row["instruments"])).fetchone()
                if duplicate: raise ValueError("An identical active shadow experiment already exists.")
            c.execute("UPDATE shadow_strategies SET status=?,enabled=?,started_at=CASE WHEN ?='start' THEN COALESCE(started_at,?) ELSE started_at END,stopped_at=CASE WHEN ?='stop' THEN ? ELSE stopped_at END,archived_at=CASE WHEN ?='archive' THEN ? ELSE archived_at END,updated_at=? WHERE shadow_strategy_id=?", (target, int(target == "RUNNING"), action, now, action, now, action, now, now, sid))
        return next(item for item in self.list() if item["shadow_strategy_id"] == sid)

    def duplicate(self, sid: str) -> dict[str, Any]:
        source = next((item for item in self.list() if item["shadow_strategy_id"] == sid), None)
        if not source: raise ValueError("Shadow strategy not found.")
        return self.create({"name": f"{source['name']} Copy", "parameters": source["parameters"], "instruments": source["instruments"], "virtual_initial_capital": source["virtual_initial_capital"]})

    def trades(self, sid: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.repository.connect() as c: return [dict(row) for row in c.execute("SELECT * FROM shadow_trades WHERE shadow_strategy_id=? ORDER BY id DESC LIMIT ?", (sid, min(max(limit, 1), 500)))]

    def equity(self, sid: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self.repository.connect() as c: rows = c.execute("SELECT ts,equity FROM shadow_equity WHERE shadow_strategy_id=? ORDER BY ts DESC LIMIT ?", (sid, min(max(limit, 1), 2000))).fetchall()
        return list(reversed([dict(row) for row in rows]))

    def process_market(self, instrument: str, datasets: dict[str, list[dict[str, Any]]], flow: dict[str, Any]) -> None:
        """Advance every running account once for a newly confirmed candle."""
        candidates = [item for item in self.list(False) if item["status"] == "RUNNING" and instrument in item["instruments"]]
        for candidate in candidates:
            try: self._process_candidate(candidate, instrument, datasets, flow)
            except (ValueError, sqlite3.Error): continue

    def _process_candidate(self, candidate: dict[str, Any], instrument: str, datasets: dict[str, list[dict[str, Any]]], flow: dict[str, Any]) -> None:
        params = validate_parameters(candidate["parameters"]); rows15 = [row for row in datasets.get("15m", []) if row.get("confirmed", True)]
        if not rows15: return
        candle = rows15[-1]; close_ts = int(candle.get("candle_close_ts", candle["ts"] + 900)); indicators = calculate_indicators(rows15, params)[-1]
        with self.repository.connect() as c:
            state_row = c.execute("SELECT * FROM shadow_strategy_states WHERE shadow_strategy_id=?", (candidate["shadow_strategy_id"],)).fetchone(); state = self._decode(state_row); positions = state["open_positions"]
            if int(state.get("last_candle_ts") or 0) >= close_ts: return
            pending = positions.pop(f"{instrument}:pending", None); position = positions.get(instrument); cash = float(state["cash"]); fees_total = float(state["fees"]); total_r = float(state["total_r"]); closed = int(state["closed_trades"])
            if pending and not position:
                raw = float(candle["open"]); entry = raw * (1 + params.slippage if pending["side"] == "LONG" else 1 - params.slippage); risk_distance = float(pending["atr"]) * params.stop_loss_atr_multiplier; risk_budget = max(cash, 0) * params.risk_per_trade; size = min(risk_budget / risk_distance if risk_distance else 0, cash / entry if entry else 0); entry_fee = entry * size * params.trading_fee
                if size > 0:
                    position = {**pending, "entry": entry, "entry_ts": int(candle["ts"]), "size": size, "stop": entry-risk_distance if pending["side"] == "LONG" else entry+risk_distance, "target": entry+risk_distance*params.risk_reward_ratio if pending["side"] == "LONG" else entry-risk_distance*params.risk_reward_ratio, "entry_fee": entry_fee, "risk_amount": risk_distance*size}; cash -= entry_fee; fees_total += entry_fee; positions[instrument] = position
            if position:
                side = position["side"]; hit_stop = float(candle["low"]) <= position["stop"] if side == "LONG" else float(candle["high"]) >= position["stop"]; hit_target = float(candle["high"]) >= position["target"] if side == "LONG" else float(candle["low"]) <= position["target"]
                if hit_stop or hit_target:
                    reason = "STOP_LOSS" if hit_stop else "TAKE_PROFIT"; raw_exit = position["stop"] if hit_stop else position["target"]; exit_price = raw_exit * (1-params.slippage if side == "LONG" else 1+params.slippage); exit_fee = exit_price*position["size"]*params.trading_fee; gross=(exit_price-position["entry"])*position["size"]*(1 if side=="LONG" else -1); pnl=gross-exit_fee-position["entry_fee"]; cash += gross-exit_fee; fees_total += exit_fee; result_r=pnl/(position["risk_amount"] or 1); total_r += result_r; closed += 1
                    c.execute("INSERT INTO shadow_trades(shadow_strategy_id,instrument,signal_id,side,status,entry_ts,exit_ts,entry,exit,stop,target,size,pnl,fees,result_r,reason,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (candidate["shadow_strategy_id"],instrument,position["signal_id"],side,"CLOSED",position["entry_ts"],int(candle["ts"]),position["entry"],exit_price,position["stop"],position["target"],position["size"],pnl,position["entry_fee"]+exit_fee,result_r,reason,json.dumps({"counterfactual": True,"stop_first_tie_break": True}))); positions.pop(instrument, None); position = None
            frames = {}
            for frame in ("1H", "4H"):
                eligible = [row for row in datasets.get(frame, []) if int(row.get("candle_close_ts", 0)) <= close_ts and row.get("confirmed", True)]
                if eligible:
                    value = calculate_indicators(eligible, params)[-1]; row = eligible[-1]; frames[frame] = {"candle_close_ts": row["candle_close_ts"], "close": row["close"], "fast_ma": value["fast_ma"], "slow_ma": value["slow_ma"]}
            decision = evaluate_decision(params, MarketContext(instrument,"15m",close_ts,float(candle["close"]),indicators,"OKX","shadow-confirmed-live-v1"), TimeframeContext(frames,("1H","4H"),False,"multi-timeframe"), FlowContext(True,float(flow.get("cvd_delta",0)),float(flow.get("oi_change_pct",0)),flow.get("source")), RiskContext(True,(),len([k for k in positions if not k.endswith(':pending')]),0,True,position is None and f"{instrument}:pending" not in positions), SHADOW_VERSION).to_dict()
            c.execute("INSERT OR IGNORE INTO shadow_decisions(shadow_strategy_id,signal_id,instrument,candle_close_ts,action,bias,score,regime,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (candidate["shadow_strategy_id"],decision["signal_id"],instrument,close_ts,decision["action"],decision["bias"],decision["score"],decision["regime"],json.dumps(decision),utc_now()))
            if decision["entry_allowed"] and not position: positions[f"{instrument}:pending"] = {"side": decision["action"], "atr": indicators["atr"], "score": decision["score"], "signal_id": decision["signal_id"], "signal_ts": close_ts}
            unrealized = sum((float(candle["close"])-p["entry"])*p["size"]*(1 if p["side"]=="LONG" else -1) for key,p in positions.items() if not key.endswith(":pending") and "entry" in p); equity = cash + unrealized; peak = max(float(state["peak_equity"]), equity); drawdown = (peak-equity)/peak*100 if peak else 0
            c.execute("INSERT OR REPLACE INTO shadow_equity(shadow_strategy_id,ts,equity) VALUES(?,?,?)", (candidate["shadow_strategy_id"],close_ts,equity)); c.execute("UPDATE shadow_strategy_states SET current_equity=?,cash=?,open_positions=?,closed_trades=?,total_r=?,fees=?,peak_equity=?,drawdown=?,last_candle_ts=?,updated_at=? WHERE shadow_strategy_id=?", (equity,cash,json.dumps(positions),closed,total_r,fees_total,peak,drawdown,close_ts,utc_now(),candidate["shadow_strategy_id"]))
