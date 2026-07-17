"""Evidence-based strategy lifecycle and audited manual activation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

try:
    from validation_repository import ValidationRepository, utc_now
except ImportError:
    from .validation_repository import ValidationRepository, utc_now

POLICY_VERSION = "promotion-policy-v1"
DEFAULT_POLICY = {"minimum_backtest_trades": 100, "minimum_oos_trades": 30, "minimum_shadow_trades": 30, "minimum_shadow_runtime_days": 14, "oos_profit_factor": 1.10, "oos_return": 0.0, "oos_maximum_drawdown": 15.0, "oos_sharpe": 0.5, "maximum_degradation_pct": 40.0, "minimum_stability_score": 60.0, "minimum_positive_neighborhood": 0.6, "minimum_positive_probability": 0.6, "maximum_risk_of_ruin": 0.05, "maximum_p95_drawdown": 20.0}


class LifecycleService:
    def __init__(self, repository: ValidationRepository, alerts: Any = None): self.repository, self.alerts = repository, alerts

    def list(self) -> list[dict[str, Any]]:
        with self.repository.connect() as c: rows = c.execute("SELECT * FROM strategy_lifecycle ORDER BY id").fetchall()
        output=[]
        for row in rows:
            item=dict(row); item["latest_evaluation"]=self.evaluation(item.get("last_evaluation_id")) if item.get("last_evaluation_id") else None; output.append(item)
        return output

    def evaluation(self, evaluation_id: int | None) -> dict[str, Any] | None:
        if not evaluation_id:return None
        with self.repository.connect() as c: row=c.execute("SELECT * FROM promotion_evaluations WHERE id=?",(evaluation_id,)).fetchone()
        if not row:return None
        item=dict(row); item["result"]=json.loads(item["result"]); return item

    def evaluate(self, lifecycle_id: int, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        thresholds={**DEFAULT_POLICY,**(policy or {})}
        with self.repository.connect() as c:
            lifecycle=c.execute("SELECT * FROM strategy_lifecycle WHERE id=?",(lifecycle_id,)).fetchone()
            if not lifecycle:raise ValueError("Strategy lifecycle not found.")
            run=c.execute("SELECT br.*,sc.latest_summary FROM backtest_runs br JOIN strategy_configs sc ON sc.id=br.strategy_config_id WHERE br.strategy_config_id=? AND br.status='COMPLETED' ORDER BY br.id DESC LIMIT 1",(lifecycle["strategy_config_id"],)).fetchone()
            sensitivity=c.execute("SELECT sr.* FROM sensitivity_results sr JOIN sensitivity_runs r ON r.id=sr.run_id WHERE r.config_hash=? ORDER BY sr.stability_score DESC LIMIT 1",(lifecycle["config_hash"],)).fetchone()
            robustness=c.execute("SELECT result FROM robustness_runs WHERE config_hash=? AND status='COMPLETED' ORDER BY id DESC LIMIT 1",(lifecycle["config_hash"],)).fetchone()
            shadow=c.execute("SELECT s.started_at,st.closed_trades FROM shadow_strategies s JOIN shadow_strategy_states st ON st.shadow_strategy_id=s.shadow_strategy_id WHERE s.config_hash=? ORDER BY s.id DESC LIMIT 1",(lifecycle["config_hash"],)).fetchone()
            critical=c.execute("SELECT COUNT(*) FROM system_alerts WHERE lower(severity)='critical' AND lower(status)='open'").fetchone()[0] if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_alerts'").fetchone() else 0
        result=[]
        def check(key:str,value:Any,passed:bool|None,evidence:Any):result.append({"key":key,"status":"insufficient" if value is None else "passed" if passed else "failed","value":value,"threshold":thresholds.get(key),"evidence":evidence})
        metrics=json.loads(run["result"])["metrics"] if run and run["result"] else None; validation=json.loads(run["result"]).get("validation") if run and run["result"] else None; oos=(validation or {}).get("out_of_sample")
        quality=json.loads(run["data_quality"] or "{}") if run else None
        check("data_quality", quality, bool(quality and int(quality.get("missing_bars",0))==0), f"backtest:{run['id']}" if run else None)
        check("warmup_complete", quality.get("warmup_bars_requested") if quality else None, bool(quality and quality.get("confirmed_rows",0)>quality.get("warmup_bars_requested",0)), f"backtest:{run['id']}" if run else None)
        check("confirmed_candles_only", quality.get("confirmed_rows") if quality else None, bool(quality and quality.get("confirmed_rows",0)>0), f"backtest:{run['id']}" if run else None)
        check("minimum_backtest_trades", metrics.get("total_trades") if metrics else None, bool(metrics and metrics.get("total_trades",0)>=thresholds["minimum_backtest_trades"]), f"backtest:{run['id']}" if run else None)
        check("minimum_oos_trades", oos.get("total_trades") if oos else None, bool(oos and oos.get("total_trades",0)>=thresholds["minimum_oos_trades"]), f"backtest:{run['id']}" if run else None)
        check("oos_profit_factor", oos.get("profit_factor") if oos else None, bool(oos and (oos.get("profit_factor") or 0)>=thresholds["oos_profit_factor"]), f"backtest:{run['id']}" if run else None)
        check("oos_return", oos.get("total_return") if oos else None, bool(oos and oos.get("total_return",0)>thresholds["oos_return"]), f"backtest:{run['id']}" if run else None)
        check("oos_maximum_drawdown", oos.get("maximum_drawdown") if oos else None, bool(oos and oos.get("maximum_drawdown",1e9)<=thresholds["oos_maximum_drawdown"]), f"backtest:{run['id']}" if run else None)
        check("oos_sharpe", oos.get("sharpe_ratio") if oos else None, bool(oos and (oos.get("sharpe_ratio") or -1e9)>=thresholds["oos_sharpe"]), f"backtest:{run['id']}" if run else None)
        stability=float(sensitivity["stability_score"]) if sensitivity else None; check("minimum_stability_score",stability,stability is not None and stability>=thresholds["minimum_stability_score"],f"sensitivity:{sensitivity['run_id']}" if sensitivity else None)
        sensitivity_metrics=json.loads(sensitivity["metrics"]) if sensitivity else None;positive_ratio=sensitivity_metrics.get("positive_neighborhood_ratio") if sensitivity_metrics else None;check("minimum_positive_neighborhood",positive_ratio,positive_ratio is not None and positive_ratio>=thresholds["minimum_positive_neighborhood"],f"sensitivity:{sensitivity['run_id']}" if sensitivity else None)
        robust=json.loads(robustness["result"]) if robustness and robustness["result"] else None; check("minimum_positive_probability",robust.get("probability_positive_return") if robust else None,bool(robust and robust.get("probability_positive_return",0)>=thresholds["minimum_positive_probability"]),"robustness" if robust else None); check("maximum_risk_of_ruin",robust.get("risk_of_ruin") if robust else None,bool(robust and robust.get("risk_of_ruin",1)<=thresholds["maximum_risk_of_ruin"]),"robustness" if robust else None); check("maximum_p95_drawdown",robust.get("p95_drawdown") if robust else None,bool(robust and robust.get("p95_drawdown",1e9)<=thresholds["maximum_p95_drawdown"]),"robustness" if robust else None)
        check("minimum_shadow_trades",shadow["closed_trades"] if shadow else None,bool(shadow and shadow["closed_trades"]>=thresholds["minimum_shadow_trades"]),"shadow" if shadow else None);runtime_days=(datetime.now(timezone.utc)-datetime.fromisoformat(shadow["started_at"])).total_seconds()/86400 if shadow and shadow["started_at"] else None;check("minimum_shadow_runtime_days",runtime_days,runtime_days is not None and runtime_days>=thresholds["minimum_shadow_runtime_days"],"shadow" if shadow else None); check("critical_alerts",critical,critical==0,"alerts")
        passed=all(item["status"]=="passed" for item in result); recommended="Qualified" if passed else "Rejected" if any(item["status"]=="failed" for item in result) else "Pending"; payload={"passed":sum(i["status"]=="passed" for i in result),"failed":sum(i["status"]=="failed" for i in result),"pending":0,"insufficient":sum(i["status"]=="insufficient" for i in result),"checks":result,"evidence_sufficient":passed,"message":"Qualified for manual activation review." if passed else "Insufficient evidence for promotion.","policy":thresholds,"policy_version":POLICY_VERSION,"evaluated_at":utc_now()}
        with self.repository.connect() as c:
            cur=c.execute("INSERT INTO promotion_evaluations(lifecycle_id,policy_version,result,recommended_status,evaluated_at) VALUES(?,?,?,?,?)",(lifecycle_id,POLICY_VERSION,json.dumps(payload),recommended,payload["evaluated_at"])); evaluation_id=int(cur.lastrowid); current=lifecycle["status"]; target="Qualified" if passed and current in {"Candidate","Shadow","Watch"} else current; c.execute("UPDATE strategy_lifecycle SET last_evaluation_id=?,status=?,updated_at=? WHERE id=?",(evaluation_id,target,utc_now(),lifecycle_id)); c.execute("INSERT INTO strategy_audit_log(lifecycle_id,action,from_status,to_status,actor,evidence,created_at) VALUES(?,?,?,?,?,?,?)",(lifecycle_id,"EVALUATE",current,target,"system",json.dumps({"evaluation_id":evaluation_id,"recommendation":recommended}),utc_now()))
        return {"id":evaluation_id,**payload,"recommended_status":recommended}

    def transition(self,lifecycle_id:int,action:str,actor:str="admin",evidence:dict[str,Any]|None=None)->dict[str,Any]:
        with self.repository.connect() as c:
            row=c.execute("SELECT * FROM strategy_lifecycle WHERE id=?",(lifecycle_id,)).fetchone()
            if not row:raise ValueError("Strategy lifecycle not found.")
            current=row["status"]
            if action=="promote":
                if current!="Qualified":raise ValueError("Only a Qualified strategy can be manually promoted to Active.")
                previous=c.execute("SELECT id FROM strategy_lifecycle WHERE status='Active' AND id!=?",(lifecycle_id,)).fetchone()
                if previous:c.execute("UPDATE strategy_lifecycle SET status='Watch',updated_at=? WHERE id=?",(utc_now(),previous[0])); c.execute("INSERT INTO strategy_audit_log(lifecycle_id,action,from_status,to_status,actor,evidence,created_at) VALUES(?,?,?,?,?,?,?)",(previous[0],"SUPERSEDE","Active","Watch",actor,json.dumps({"new_active_id":lifecycle_id}),utc_now()))
                target="Active"; c.execute("UPDATE strategy_lifecycle SET previous_active_id=? WHERE id=?",(previous[0] if previous else None,lifecycle_id))
            elif action=="reject":target="Rejected"
            elif action=="archive":target="Archived"
            elif action=="candidate":
                if current!="Draft":raise ValueError("Only Draft can become Candidate.")
                target="Candidate"
            elif action=="shadow":
                if current!="Candidate":raise ValueError("Only Candidate can enter Shadow.")
                target="Shadow"
            elif action=="rollback":
                if current!="Active" or not row["previous_active_id"]:raise ValueError("No previous Active strategy is available for rollback.")
                previous=c.execute("SELECT * FROM strategy_lifecycle WHERE id=?",(row["previous_active_id"],)).fetchone()
                if not previous:raise ValueError("Previous Active strategy is unavailable.")
                c.execute("UPDATE strategy_lifecycle SET status='Watch',updated_at=? WHERE id=?",(utc_now(),lifecycle_id)); c.execute("UPDATE strategy_lifecycle SET status='Active',updated_at=? WHERE id=?",(utc_now(),previous["id"])); c.execute("INSERT INTO strategy_audit_log(lifecycle_id,action,from_status,to_status,actor,evidence,created_at) VALUES(?,?,?,?,?,?,?)",(previous["id"],"ROLLBACK_RESTORE",previous["status"],"Active",actor,json.dumps({"from_id":lifecycle_id}),utc_now())); target="Watch"
            else:raise ValueError("Unsupported lifecycle action.")
            c.execute("UPDATE strategy_lifecycle SET status=?,updated_at=? WHERE id=?",(target,utc_now(),lifecycle_id)); c.execute("INSERT INTO strategy_audit_log(lifecycle_id,action,from_status,to_status,actor,evidence,created_at) VALUES(?,?,?,?,?,?,?)",(lifecycle_id,action.upper(),current,target,actor,json.dumps(evidence or {}),utc_now()))
        return next(item for item in self.list() if item["id"]==lifecycle_id)

    def audit(self,lifecycle_id:int)->list[dict[str,Any]]:
        with self.repository.connect() as c:rows=c.execute("SELECT * FROM strategy_audit_log WHERE lifecycle_id=? ORDER BY id DESC LIMIT 200",(lifecycle_id,)).fetchall()
        output=[]
        for row in rows:item=dict(row);item["evidence"]=json.loads(item["evidence"]);output.append(item)
        return output
