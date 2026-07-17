"""Phase 4 orchestration over the existing single-worker research queue."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

try:
    from backtest_engine import run_backtest
    from benchmarks import run_asset_benchmarks
    from gate_analysis import aggregate_gate_funnel, filter_decisions
    from near_miss import counterfactual_outcome, identify_near_miss
    from robustness import MAX_SIMULATIONS, run_robustness, stress_curve
    from sensitivity import parameter_combinations, stability_scores
    from strategy_rules import DEFAULT_PARAMETERS, validate_parameters
    from validation_repository import ValidationRepository, utc_now
except ImportError:
    from .backtest_engine import run_backtest
    from .benchmarks import run_asset_benchmarks
    from .gate_analysis import aggregate_gate_funnel, filter_decisions
    from .near_miss import counterfactual_outcome, identify_near_miss
    from .robustness import MAX_SIMULATIONS, run_robustness, stress_curve
    from .sensitivity import parameter_combinations, stability_scores
    from .strategy_rules import DEFAULT_PARAMETERS, validate_parameters
    from .validation_repository import ValidationRepository, utc_now


class ValidationService:
    def __init__(self, research: Any):
        self.research = research; self.repository = ValidationRepository(research.repository.db_path); self.jobs = research.jobs
        self.jobs.register("GATE_ANALYSIS", self._job_gates); self.jobs.register("SENSITIVITY", self._job_sensitivity); self.jobs.register("BENCHMARK", self._job_benchmark); self.jobs.register("ROBUSTNESS", self._job_robustness)

    def start_gates(self, payload: dict[str, Any], requester: str) -> dict[str, Any]:
        filters = {key: payload[key] for key in ("instrument","strategy_version","config_hash","timeframe","source","run_id","start_ts","end_ts","bias","regime","flow","hour","weekday") if payload.get(key) not in (None,"")}
        run_id = self.repository.create_run("gate_analysis_runs", filters); job = self.jobs.enqueue("GATE_ANALYSIS", {"validation_run_id":run_id,"filters":filters}, requester); self.repository.bind_job("gate_analysis_runs",run_id,job["id"]); return {"id":run_id,"job_id":job["id"],"status":"QUEUED","queue_position":job.get("queue_position")}

    def _job_gates(self, job_id: int, payload: dict[str, Any], checkpoint) -> dict[str, Any]:
        run_id=int(payload["validation_run_id"])
        try:
            checkpoint(job_id,5,"Loading complete decision payloads"); decisions=self.repository.decisions(payload["filters"]); decisions=filter_decisions(decisions,payload["filters"]); checkpoint(job_id,45,f"Aggregating {len(decisions)} decisions"); result=aggregate_gate_funnel(decisions)
            near_count=0
            for index,decision in enumerate(decisions):
                params=(decision.get("decision_input_summary") or {}).get("parameters") or DEFAULT_PARAMETERS; item=identify_near_miss(decision,params)
                if item:
                    item_id=self.repository.save_near_miss(item);near_count+=1
                    with self.repository.connect() as c:
                        candle_rows=[dict(row) for row in c.execute("SELECT ts,open,high,low,close FROM historical_candles WHERE instrument=? AND timeframe=? AND ts>=? ORDER BY ts LIMIT 96",(item["instrument"],decision.get("execution_timeframe","15m"),int(item["candle_close_ts"]))).fetchall()]
                        if not candle_rows:candle_rows=[dict(row) for row in c.execute("SELECT ts,open,high,low,close FROM market_candles WHERE instrument=? AND bar=? AND ts>=? ORDER BY ts LIMIT 96",(item["instrument"],decision.get("execution_timeframe","15m"),int(item["candle_close_ts"]))).fetchall()]
                    self.repository.save_outcome(item_id,counterfactual_outcome(item,candle_rows,params))
                if index and index%5000==0:checkpoint(job_id,45+int(index/max(1,len(decisions))*40),"Persisting near misses")
            result["near_miss_count"]=near_count; result["filters"]=payload["filters"]; result["generated_at"]=utc_now()
            with self.repository.connect() as c:
                c.execute("DELETE FROM gate_analysis_results WHERE run_id=?",(run_id,)); c.executemany("INSERT INTO gate_analysis_results(run_id,gate_key,payload) VALUES(?,?,?)",[(run_id,item["gate"],json.dumps(item)) for item in result["gates"]])
            self.repository.finish_run("gate_analysis_runs",run_id,result); return {"gate_analysis_run_id":run_id,"decision_count":len(decisions),"near_miss_count":near_count}
        except Exception as error:self.repository.finish_run("gate_analysis_runs",run_id,error=str(error));raise

    def gates(self, run_id: int | None, filters: dict[str, Any]) -> dict[str, Any]:
        if run_id:
            item=self.repository.run("gate_analysis_runs",run_id)
            if not item:raise ValueError("Gate analysis run not found.")
            return item
        decisions=filter_decisions(self.repository.decisions(filters,limit=10000),filters); result=aggregate_gate_funnel(decisions); result["bounded_live_query"]=True; return result

    def start_sensitivity(self,payload:dict[str,Any],requester:str)->dict[str,Any]:
        request=self.research.validate_request(payload); mode=str(payload.get("mode","OAT")); ranges=payload.get("ranges") or []; combos=parameter_combinations(request["parameters"],ranges,mode); request.update({"mode":mode,"ranges":ranges,"combination_count":len(combos)})
        run_id=self.repository.create_run("sensitivity_runs",request,strategy_version="historical-mtf-no-flow-v1",config_hash=__import__('dashboard.signal_identity',fromlist=['config_hash']).config_hash(request["parameters"])); job=self.jobs.enqueue("SENSITIVITY",{"validation_run_id":run_id,**request},requester);self.repository.bind_job("sensitivity_runs",run_id,job["id"]);return {"id":run_id,"job_id":job["id"],"status":"QUEUED","estimated_combinations":len(combos)}

    def _datasets(self,request:dict[str,Any],params:Any)->tuple[list[dict[str,Any]],dict[str,list[dict[str,Any]]]]:
        warmup=max(params.slow_ma,params.ema_pullback_period,params.rsi_period,params.atr_period)+20; candles,_=self.research.history.get_candles(request["instrument"],request["timeframe"],request["start_ts"],request["end_ts"],warmup); mtf={}
        if request["timeframe"]=="15m":
            for frame in ("1H","4H"):mtf[frame],_=self.research.history.get_candles(request["instrument"],frame,request["start_ts"],request["end_ts"],warmup)
        return candles,mtf

    @staticmethod
    def _metric_row(parameters:dict[str,Any],full:dict[str,Any],oos:dict[str,Any])->dict[str,Any]:
        m,o=full["metrics"],oos["metrics"]
        return {"parameters":parameters,"total_return":m["total_return"],"profit_factor":m["profit_factor"],"maximum_drawdown":m["maximum_drawdown"],"sharpe":m["sharpe_ratio"],"sortino":m["sortino_ratio"],"win_rate":m["win_rate"],"trades":m["total_trades"],"fees":m["fees_paid"],"expectancy":m["expectancy"],"oos_return":o["total_return"],"oos_profit_factor":o["profit_factor"],"oos_drawdown":o["maximum_drawdown"]}

    def _job_sensitivity(self,job_id:int,payload:dict[str,Any],checkpoint)->dict[str,Any]:
        run_id=int(payload["validation_run_id"])
        try:
            combos=parameter_combinations(payload["parameters"],payload["ranges"],payload["mode"]); first=validate_parameters(combos[0]);candles,mtf=self._datasets(payload,first);split=payload["start_ts"]+int((payload["end_ts"]-payload["start_ts"])*payload.get("validation_split",.7));results=[]
            for index,combo in enumerate(combos):
                checkpoint(job_id,5+int(index/max(1,len(combos))*90),f"Testing bounded combination {index+1}/{len(combos)}");params=validate_parameters(combo);full=run_backtest(candles,payload["instrument"],payload["timeframe"],params,payload["start_ts"],payload["end_ts"],timeframe_datasets=mtf);oos=run_backtest(candles,payload["instrument"],payload["timeframe"],params,split+1,payload["end_ts"],timeframe_datasets=mtf);results.append(self._metric_row(combo,full,oos))
            results=stability_scores(results,[item["parameter"] for item in payload["ranges"]])
            with self.repository.connect() as c:c.executemany("INSERT INTO sensitivity_results(run_id,parameters,metrics,stability_score,labels) VALUES(?,?,?,?,?)",[(run_id,json.dumps(item["parameters"]),json.dumps(item),item["stability_score"],json.dumps(item["labels"])) for item in results])
            summary={"combination_count":len(results),"best_historical":max(range(len(results)),key=lambda i:float(results[i].get("total_return") or -1e99)),"most_stable":max(range(len(results)),key=lambda i:results[i]["stability_score"]),"stability_formula":"25% neighborhood return variance + 25% OOS degradation + 20% positive-neighborhood ratio + 15% drawdown stability + 15% sample size","warning":"Highest return is not a promotion recommendation."};self.repository.finish_run("sensitivity_runs",run_id,summary);return {"sensitivity_run_id":run_id,**summary}
        except Exception as error:self.repository.finish_run("sensitivity_runs",run_id,error=str(error));raise

    def sensitivity(self,run_id:int,page:int=1,page_size:int=100)->dict[str,Any]:
        run=self.repository.run("sensitivity_runs",run_id)
        if not run:raise ValueError("Sensitivity run not found.")
        page_size=min(max(page_size,1),100);offset=(max(page,1)-1)*page_size
        with self.repository.connect() as c:total=c.execute("SELECT COUNT(*) FROM sensitivity_results WHERE run_id=?",(run_id,)).fetchone()[0];rows=c.execute("SELECT metrics FROM sensitivity_results WHERE run_id=? ORDER BY id LIMIT ? OFFSET ?",(run_id,page_size,offset)).fetchall()
        run["results"]=[json.loads(row[0]) for row in rows];run["total_results"]=total;run["page"]=page;return run

    def start_benchmark(self,payload:dict[str,Any],requester:str)->dict[str,Any]:
        request=self.research.validate_request(payload);request["assets"]=sorted(set(payload.get("assets") or ["BTC-USDT","ETH-USDT","SOL-USDT"]));run_id=self.repository.create_run("benchmark_runs",request);job=self.jobs.enqueue("BENCHMARK",{"validation_run_id":run_id,**request},requester);self.repository.bind_job("benchmark_runs",run_id,job["id"]);return {"id":run_id,"job_id":job["id"],"status":"QUEUED"}

    def _job_benchmark(self,job_id:int,payload:dict[str,Any],checkpoint)->dict[str,Any]:
        run_id=int(payload["validation_run_id"])
        try:
            all_results=[];portfolio_series=[];params=validate_parameters(payload["parameters"])
            for index,asset in enumerate(payload["assets"]):
                checkpoint(job_id,5+index*25,f"Running {asset} benchmarks");request={**payload,"instrument":asset};candles,mtf=self._datasets(request,params);canonical=run_backtest(candles,asset,payload["timeframe"],params,payload["start_ts"],payload["end_ts"],timeframe_datasets=mtf);results=run_asset_benchmarks([row for row in candles if payload["start_ts"]<=int(row["ts"])<=payload["end_ts"]],params.initial_capital,params.trading_fee,params.slippage,{"15m":900,"1H":3600,"4H":14400}[payload["timeframe"]],canonical);all_results.extend([{**item,"instrument":asset} for item in results]);portfolio_series.append(next(item for item in results if item["name"]=="Buy & Hold")["equity"])
            if portfolio_series:
                common=sorted(set.intersection(*(set(point["ts"] for point in series) for series in portfolio_series)));maps=[{p["ts"]:p["equity"] for p in series} for series in portfolio_series];equity=[{"ts":ts,"equity":sum(m[ts] for m in maps)/len(maps)} for ts in common];from dashboard.metrics import calculate_metrics;metrics=calculate_metrics(params.initial_capital,equity,[],{"15m":900,"1H":3600,"4H":14400}[payload["timeframe"]]);constituent_holds=[x for x in all_results if x["name"]=="Buy & Hold"];metrics["fees_paid"]=sum(x["metrics"]["fees_paid"] for x in constituent_holds)/len(constituent_holds);metrics.update({"exposure":100.0,"time_in_market":100.0,"total_trades":len(constituent_holds)});all_results.append({"name":"Equal-Weight BTC/ETH/SOL Portfolio","instrument":"PORTFOLIO","metrics":metrics,"equity":equity,"execution_model":"Equal capital allocation; constituent Buy & Hold fees and slippage included."})
            with self.repository.connect() as c:c.executemany("INSERT INTO benchmark_results(run_id,name,instrument,payload) VALUES(?,?,?,?)",[(run_id,item["name"],item["instrument"],json.dumps(item)) for item in all_results])
            summary={"result_count":len(all_results),"assets":payload["assets"],"negative_results_preserved":True};self.repository.finish_run("benchmark_runs",run_id,summary);return {"benchmark_run_id":run_id,**summary}
        except Exception as error:self.repository.finish_run("benchmark_runs",run_id,error=str(error));raise

    def benchmark(self,run_id:int)->dict[str,Any]:
        run=self.repository.run("benchmark_runs",run_id)
        if not run:raise ValueError("Benchmark run not found.")
        with self.repository.connect() as c:rows=c.execute("SELECT payload FROM benchmark_results WHERE run_id=? ORDER BY id",(run_id,)).fetchall()
        run["results"]=[json.loads(row[0]) for row in rows];return run

    def start_robustness(self,payload:dict[str,Any],requester:str)->dict[str,Any]:
        input_run_id=int(payload.get("input_run_id",0));run=self.research.repository.run(input_run_id)
        if not run or run["status"]!="COMPLETED":raise ValueError("A completed input backtest run is required.")
        simulations=int(payload.get("simulation_count",1000));
        if not 1<=simulations<=MAX_SIMULATIONS:raise ValueError(f"Simulation count must be between 1 and {MAX_SIMULATIONS}.")
        request={"input_run_id":input_run_id,"simulation_count":simulations,"random_seed":int(payload.get("random_seed",42)),"confidence_level":float(payload.get("confidence_level",.95)),"fee_multipliers":payload.get("fee_multipliers",[.5,1,1.5,2]),"slippage_multipliers":payload.get("slippage_multipliers",[.5,1,1.5,2]),"missed_trade_rates":payload.get("missed_trade_rates",[0,.05,.1,.2]),"execution_delay_bars":payload.get("execution_delay_bars",[0,1,2]),"loss_threshold_pct":float(payload.get("loss_threshold_pct",20)),"drawdown_threshold_pct":float(payload.get("drawdown_threshold_pct",25))}
        run_id=self.repository.create_run("robustness_runs",request,input_run_id=input_run_id,strategy_version="historical-mtf-no-flow-v1",config_hash=__import__('dashboard.signal_identity',fromlist=['config_hash']).config_hash(run["parameters"]),random_seed=request["random_seed"]);job=self.jobs.enqueue("ROBUSTNESS",{"validation_run_id":run_id,**request},requester);self.repository.bind_job("robustness_runs",run_id,job["id"]);return {"id":run_id,"job_id":job["id"],"status":"QUEUED","simulation_count":simulations}

    def _job_robustness(self,job_id:int,payload:dict[str,Any],checkpoint)->dict[str,Any]:
        run_id=int(payload["validation_run_id"])
        try:
            trades=self.research.repository.trades(payload["input_run_id"]);input_run=self.research.repository.run(payload["input_run_id"]);initial=float(input_run["parameters"].get("initial_capital",10000));base_slippage=float(input_run["parameters"].get("slippage",0));
            for trade in trades:
                size=float(trade.get("position_size",0));trade["slippage_cost"]=(float(trade.get("entry_price",0))+float(trade.get("exit_price",0)))*size*base_slippage
            checkpoint(job_id,10,"Trade-order Monte Carlo");order=run_robustness(trades,initial,payload["simulation_count"],payload["random_seed"],"TRADE_ORDER",loss_threshold_pct=payload["loss_threshold_pct"],drawdown_threshold_pct=payload["drawdown_threshold_pct"]);checkpoint(job_id,55,"Bootstrap resampling");bootstrap=run_robustness(trades,initial,payload["simulation_count"],payload["random_seed"],"BOOTSTRAP",loss_threshold_pct=payload["loss_threshold_pct"],drawdown_threshold_pct=payload["drawdown_threshold_pct"]);fee=stress_curve(trades,initial,[float(x) for x in payload["fee_multipliers"]],"fee");slippage=stress_curve(trades,initial,[float(x) for x in payload["slippage_multipliers"]],"slippage");missed=[]
            for rate in payload["missed_trade_rates"]:result=run_robustness(trades,initial,min(500,payload["simulation_count"]),payload["random_seed"],"TRADE_ORDER",missed_trade_rate=float(rate));missed.append({"rate":rate,"median_return":result.get("median_return"),"p95_drawdown":result.get("p95_drawdown")})
            result={**order,"trade_order":order,"bootstrap":bootstrap,"fee_stress":fee,"slippage_stress":slippage,"missed_signal_stress":missed,"execution_delay_stress":self._execution_delay_stress(input_run,trades,[int(x) for x in payload["execution_delay_bars"]],initial),"input_run_id":payload["input_run_id"]}
            with self.repository.connect() as c:c.execute("INSERT INTO robustness_results(run_id,simulation_type,payload) VALUES(?,?,?)",(run_id,"COMBINED",json.dumps(result)))
            self.repository.finish_run("robustness_runs",run_id,result);return {"robustness_run_id":run_id,"simulation_count":payload["simulation_count"]}
        except Exception as error:self.repository.finish_run("robustness_runs",run_id,error=str(error));raise

    def _execution_delay_stress(self,input_run:dict[str,Any],trades:list[dict[str,Any]],bars_list:list[int],initial:float)->list[dict[str,Any]]:
        seconds={"15m":900,"1H":3600,"4H":14400}[input_run["timeframe"]];fee=float(input_run["parameters"].get("trading_fee",0));slippage=float(input_run["parameters"].get("slippage",0));output=[]
        with self.repository.connect() as c:
            for bars in bars_list[:10]:
                pnls=[];missed=0
                for trade in trades:
                    if bars==0:pnls.append(float(trade.get("pnl",0)));continue
                    delayed_ts=int(trade["entry_ts"])+bars*seconds
                    if delayed_ts>=int(trade["exit_ts"]):missed+=1;continue
                    candle=c.execute("SELECT open FROM historical_candles WHERE instrument=? AND timeframe=? AND ts>=? AND ts<=? ORDER BY ts LIMIT 1",(input_run["instrument"],input_run["timeframe"],delayed_ts,int(trade["exit_ts"]))).fetchone()
                    if not candle:missed+=1;continue
                    side=trade["side"];entry=float(candle[0])*(1+slippage if side=="LONG" else 1-slippage);exit_price=float(trade["exit_price"]);size=float(trade.get("position_size",0));gross=(exit_price-entry)*size*(1 if side=="LONG" else -1);pnls.append(gross-(entry+exit_price)*size*fee)
                final=initial+sum(pnls);output.append({"bars":bars,"return":(final/initial-1)*100,"final_equity":final,"executed_trades":len(pnls),"missed_after_delay":missed,"model":"Delayed confirmed entry uses the later candle open with adverse slippage; original exit is retained."})
        return output

    def robustness(self,run_id:int)->dict[str,Any]:
        run=self.repository.run("robustness_runs",run_id)
        if not run:raise ValueError("Robustness run not found.")
        return run
