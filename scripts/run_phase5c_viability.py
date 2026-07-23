"""Run the fixed Strategy v2.1 plan on BTC Development folds at 15m/1H/4H.

The source database is verified and never opened for writes.  All Discovery
rows, event labels, and summaries are persisted only in the supplied copy.
"""
from __future__ import annotations

import argparse, json, shutil, statistics, time
from pathlib import Path

from dashboard.canonical_dataset import partition_fingerprint
from dashboard.discovery_service import DiscoveryService, FOLDS
from dashboard.discovery_v2_1_registry import plan
from dashboard.discovery_v2_registry import FIXED_EXECUTION, SPACES
from dashboard.job_queue import JobQueue
from dashboard.okx_history import TIMEFRAME_SECONDS
from dashboard.research_repository import ResearchRepository
from scripts.run_v2_discovery_pilot import EXPECTED_SHA, file_hash, verify_frozen

FINGERPRINT="0bafcc5be02b513e5ea060d9e4d394c29915af8b67f99627eb9b4761c33683cd"
TIMEFRAMES=("15m","1H","4H")
TEMPLATES=("TREND_PULLBACK_V2_1","TREND_BREAKOUT_V2_1","RANGE_MEAN_REVERSION_V2_1")
POLICY_VERSION="phase5c-timeframe-viability-v1"
DEVELOPMENT_END_TS=1746057600

def median(values):
    values=[float(x) for x in values if x is not None]
    return statistics.median(values) if values else None

def raw_benchmark_return(benchmark):
    return (float(benchmark["raw_exit_price"])/float(benchmark["raw_entry_price"])-1)*100

def prepare_development_dataset(repo):
    """Trust the verified canonical artifact while reading no holdout/OOT candle."""
    dataset=repo.discovery_dataset(1)
    if not dataset: raise ValueError("Canonical Discovery dataset metadata is missing")
    for timeframe in TIMEFRAMES:
        partition=repo.discovery_partition(1,"BTC-USDT",timeframe)
        if not partition or partition["status"]!="COMPLETE":
            raise ValueError("Canonical BTC Development partition is incomplete")
        if int(partition["first_ts"])>FOLDS[0][0] or int(partition["last_ts"])<FOLDS[-1][3]-TIMEFRAME_SECONDS[timeframe]:
            raise ValueError("Canonical BTC Development partition lacks fold coverage")
    manifest={"phase5c_development_only":True,"verified_source_sha256":EXPECTED_SHA,
      "dataset_fingerprint":FINGERPRINT,"maximum_candle_timestamp_exclusive":DEVELOPMENT_END_TS}
    with repo.connect() as connection:
        connection.execute("UPDATE discovery_datasets SET status='COMPLETE',dataset_fingerprint=?,manifest=? WHERE id=1",
          (FINGERPRINT,json.dumps(manifest,sort_keys=True)))
    return repo.discovery_dataset(1)

def development_snapshot(repo):
    return {timeframe:partition_fingerprint(repo.candles(
      "BTC-USDT",timeframe,FOLDS[0][0],DEVELOPMENT_END_TS-1)) for timeframe in TIMEFRAMES}

def candidate_summary(candidate, folds, timeframe):
    step=TIMEFRAME_SECONDS[timeframe]; months=len(folds)*2
    metrics=[json.loads(x["metrics"]) for x in folds]
    benchmarks=[json.loads(x["buy_hold_metrics"]) for x in folds]
    costs=[x["cost_attribution"] for x in metrics]
    lifecycle=[x["lifecycle_diagnostics"] for x in metrics]
    gross=[x["gross_return_before_costs"] for x in costs]
    gross_excess=[a-raw_benchmark_return(b) for a,b in zip(gross,benchmarks)]
    net=[x["total_return"] for x in metrics]
    event_counts={str(h):sum(x["event_study"]["aggregate"][str(h)]["event_count"] for x in metrics)
                  for h in (1,2,4,8,16)}
    event_returns={str(h):median(label["direction_adjusted_forward_return"]
      for x in metrics for event in x["event_study"]["events"]
      for key,label in event["labels"].items() if key==str(h)) for h in (1,2,4,8,16)}
    exits={}
    for item in lifecycle:
        for reason,count in item["exit_counts"].items(): exits[reason]=exits.get(reason,0)+count
    total_trades=sum(x["executed_trades"] for x in lifecycle)
    positive=[max(0,float(x)) for x in gross]
    return {"candidate_number":candidate["candidate_number"],"parameter_hash":candidate["parameter_hash"],
      "parameters":json.loads(candidate["parameters"]),"fold_count":len(folds),
      "setup_count":sum(x["setup_count"] for x in lifecycle),
      "trigger_count":sum(x["trigger_count"] for x in lifecycle),"trade_count":total_trades,
      "trades_per_month":total_trades/months,
      "median_holding_bars":median(x["median_holding_seconds"]/step for x in lifecycle if x["median_holding_seconds"] is not None),
      "median_gross_return":median(gross),"median_gross_excess_return":median(gross_excess),
      "gross_positive_fold_ratio":sum(x>0 for x in gross)/len(gross),
      "gross_edge_fold_ratio":sum(x>0 for x in gross_excess)/len(gross_excess),
      "median_fee_drag":median(x["fee_drag_return"] for x in costs),
      "median_slippage_drag":median(x["slippage_drag_return"] for x in costs),
      "median_net_return":median(net),"median_excess_return":candidate["aggregate"]["median_excess_return"],
      "worst_excess_return":candidate["aggregate"]["worst_excess_return"],
      "maximum_drawdown":candidate["aggregate"]["worst_maximum_drawdown"],
      "profitable_fold_ratio":candidate["aggregate"]["profitable_fold_ratio"],
      "benchmark_beating_fold_ratio":candidate["aggregate"]["benchmark_beating_fold_ratio"],
      "return_concentration":max(positive)/sum(positive) if sum(positive)>0 else None,
      "stop_hit_ratio":exits.get("STOP_LOSS",0)/total_trades if total_trades else None,
      "target_hit_ratio":exits.get("TAKE_PROFIT",0)/total_trades if total_trades else None,
      "event_count_by_horizon":event_counts,"median_event_return_by_horizon":event_returns,
      "zero_cost_trade_count":sum(x["trade_count"] for x in costs),
      "normal_cost_trade_count":total_trades}

def classification(candidates, timeframe):
    threshold={"15m":40,"1H":20,"4H":10}[timeframe]
    behavior=len({(x["trigger_count"],round(x["median_gross_return"],8)) for x in candidates})>1
    qualified=[x for x in candidates if x["median_gross_excess_return"]>0
      and x["gross_edge_fold_ratio"]>=.6 and x["trade_count"]>=threshold
      and x["stop_hit_ratio"] is not None and x["target_hit_ratio"] is not None
      and behavior and x["normal_cost_trade_count"]==x["zero_cost_trade_count"]]
    if qualified: return "RETAIN_FOR_FORMAL_SEARCH"
    if all(x["trade_count"]<threshold for x in candidates): return "INSUFFICIENT_SAMPLE"
    if all(x["median_gross_excess_return"]<=0 for x in candidates): return "RETIRE_NO_GROSS_EDGE"
    if all(x["gross_edge_fold_ratio"]<.6 for x in candidates if x["median_gross_excess_return"]>0):
        return "RETIRE_UNSTABLE"
    return "RETAIN_FOR_DIAGNOSTIC_ONLY"

def failure_mode(candidates, timeframe):
    threshold={"15m":40,"1H":20,"4H":10}[timeframe]
    if all(x["trade_count"]<threshold for x in candidates): return "insufficient sample"
    if any(x["median_gross_excess_return"]>0>=x["median_excess_return"] for x in candidates):
        return "positive gross edge destroyed by costs"
    if all(x["median_gross_excess_return"]<=0 for x in candidates): return "no gross edge"
    if all(x["gross_edge_fold_ratio"]<.6 for x in candidates): return "unstable across folds"
    return "no gross edge"

def summarize(repo, run_ids):
    output={"policy_version":POLICY_VERSION,"dataset_fingerprint":FINGERPRINT,
      "parameter_spaces":SPACES,"run_ids":run_ids,"timeframes":{}}
    with repo.connect() as connection:
      for timeframe,run_id in run_ids.items():
        rows=[]
        candidates=[dict(x) for x in connection.execute(
          "SELECT * FROM strategy_discovery_candidates WHERE discovery_run_id=? ORDER BY candidate_number",(run_id,))]
        for candidate in candidates:
            candidate["aggregate"]=json.loads(candidate["aggregate_metrics"])
            folds=[dict(x) for x in connection.execute(
              "SELECT * FROM strategy_discovery_folds WHERE candidate_id=? ORDER BY fold_number",(candidate["id"],))]
            rows.append({**candidate_summary(candidate,folds,timeframe),"template":candidate["template"]})
        combinations={}
        for template in TEMPLATES:
            selected=[x for x in rows if x["template"]==template]
            combinations[template]={"classification":classification(selected,timeframe),
              "failure_mode":failure_mode(selected,timeframe),
              "candidate_count":len(selected),"folds_per_candidate":5,"candidates":selected}
        output["timeframes"][timeframe]={"candidate_count":len(rows),"fold_count":len(rows)*5,
          "combinations":combinations}
    output["retained_formal_search_scope"]=[{"template":template,"timeframe":timeframe}
      for timeframe,data in output["timeframes"].items()
      for template,item in data["combinations"].items()
      if item["classification"]=="RETAIN_FOR_FORMAL_SEARCH"]
    output["retired_scope"]=[{"template":template,"timeframe":timeframe,
      "classification":item["classification"]} for timeframe,data in output["timeframes"].items()
      for template,item in data["combinations"].items() if item["classification"].startswith("RETIRE_")]
    output["parameter_ranges_expanded"]=False
    output["eligibility_thresholds_changed"]=False
    output["robustness_or_ablation_run"]=False
    output["holdout_or_oot_accessed"]=False
    output["historical_cvd_oi_requested"]=False
    output["strategy_activated"]=False
    return output

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--database",type=Path,required=True)
    parser.add_argument("--summary",type=Path)
    args=parser.parse_args()
    if args.source.resolve()==args.database.resolve(): raise ValueError("Working database must differ from frozen source")
    verify_frozen(args.source)
    if args.database.exists(): raise ValueError("Working database already exists")
    args.database.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(args.source,args.database)
    repo=ResearchRepository(args.database)
    dataset=prepare_development_dataset(repo); before=development_snapshot(repo)
    planned,_,_=plan(32); expected=[(t,p) for t,p in planned]
    run_ids={}; elapsed={}
    for timeframe in TIMEFRAMES:
        jobs=JobQueue(args.database,autostart=False); service=DiscoveryService(repo,jobs)
        request={"dataset_id":dataset["id"],"instrument":"BTC-USDT","timeframe":timeframe,
          "templates":list(TEMPLATES),"trial_budget":32,"seed":20260723,"mode":"PRICE_ONLY",
          "execution_assumptions":{k:FIXED_EXECUTION[k] for k in
            ("initial_capital","risk_per_trade","trading_fee","slippage","cooldown_bars")}|
            {"stop_loss_atr_multiplier":1.0,"risk_reward_ratio":2.0,
             "allow_long":True,"allow_short":True}}
        started=service.start(request,"phase5c-viability")
        job=jobs.get(started["job_id"]); began=time.monotonic()
        service._run_job(started["job_id"],job["request_payload"],lambda *unused:None)
        elapsed[timeframe]=round(time.monotonic()-began,3); run_ids[timeframe]=started["id"]
        with repo.connect() as connection:
            actual=[(x["template"],json.loads(x["parameters"])) for x in connection.execute(
              "SELECT template,parameters FROM strategy_discovery_candidates WHERE discovery_run_id=? ORDER BY candidate_number",(started["id"],))]
        assert actual==expected, "Timeframes did not use the identical fixed plan"
    after=development_snapshot(repo); verify_frozen(args.source)
    assert before==after
    report=summarize(repo,run_ids)
    report.update({"elapsed_seconds":elapsed,"frozen_sha256":file_hash(args.source),
      "expected_frozen_sha256":EXPECTED_SHA,"raw_ohlcv_unchanged":True})
    payload=json.dumps(report,sort_keys=True,separators=(",",":"))
    if args.summary:
        args.summary.parent.mkdir(parents=True,exist_ok=True)
        args.summary.write_text(payload,encoding="utf-8")
    print(payload)

if __name__=="__main__": main()
