"""Run the fixed Phase 5A plan through corrected Strategy v2.1."""
from __future__ import annotations
import argparse, hashlib, json, shutil, time
from pathlib import Path
from dashboard.discovery_service import DiscoveryService
from dashboard.discovery_v2_registry import FIXED_EXECUTION
from dashboard.job_queue import JobQueue
from dashboard.research_repository import ResearchRepository
from scripts.run_v2_discovery_pilot import verify_frozen, reconcile, prepare_discovery_dataset, file_hash

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--source",type=Path,required=True); parser.add_argument("--database",type=Path,required=True)
    args=parser.parse_args(); verify_frozen(args.source)
    if not args.database.exists(): shutil.copy2(args.source,args.database)
    repo=ResearchRepository(args.database); before=reconcile(repo); dataset=prepare_discovery_dataset(repo,before)
    jobs=JobQueue(args.database,autostart=False); service=DiscoveryService(repo,jobs)
    request={"dataset_id":dataset["id"],"instrument":"BTC-USDT","timeframe":"15m",
      "templates":["TREND_PULLBACK_V2_1","TREND_BREAKOUT_V2_1","RANGE_MEAN_REVERSION_V2_1"],
      "trial_budget":32,"seed":20260723,"mode":"PRICE_ONLY",
      "execution_assumptions":{k:FIXED_EXECUTION[k] for k in ("initial_capital","risk_per_trade","trading_fee","slippage","cooldown_bars")}|
        {"stop_loss_atr_multiplier":1.0,"risk_reward_ratio":2.0,"allow_long":True,"allow_short":True}}
    started=service.start(request,"v2.1-pilot"); job=jobs.get(started["job_id"]); began=time.monotonic()
    service._run_job(started["job_id"],job["request_payload"],lambda *args:None); elapsed=time.monotonic()-began
    after=reconcile(repo); assert before["raw_candle_semantic_hash"]==after["raw_candle_semantic_hash"] and before["partitions"]==after["partitions"]
    verify_frozen(args.source)
    with repo.connect() as c: row=c.execute("SELECT result FROM strategy_discovery_runs WHERE id=?",(started["id"],)).fetchone()
    print(json.dumps({"pilot_run_id":started["id"],"elapsed_seconds":round(elapsed,3),
      "dataset_fingerprint":after["dataset_fingerprint"],"result":json.loads(row["result"]),
      "frozen_sha":file_hash(args.source),"raw_immutable":True},sort_keys=True))
if __name__=="__main__": main()
