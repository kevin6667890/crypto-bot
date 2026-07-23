"""Create immutable-copy metadata and run the bounded Development-only v2 pilot."""
from __future__ import annotations
import argparse, hashlib, json, shutil, time
from pathlib import Path
from dashboard.canonical_dataset import (CANONICAL_OHLCV_SCHEMA_VERSION, CANONICAL_PARTITION_FINGERPRINT_VERSION,
    CANONICAL_DATASET_FINGERPRINT_VERSION, partition_fingerprint, dataset_fingerprint, raw_semantic_hash)
from dashboard.dataset_service import RAW_START_TS, END_TS
from dashboard.discovery_service import DiscoveryService
from dashboard.discovery_v2_registry import FIXED_EXECUTION
from dashboard.job_queue import JobQueue
from dashboard.research_repository import ResearchRepository, utc_now

EXPECTED_SHA="9ae9c4ed5f981120eafe42c483ec956a4796c59269206287a781a136d6aee9d3"; EXPECTED_SIZE=85999616
INSTRUMENTS=("BTC-USDT","ETH-USDT","SOL-USDT"); TIMEFRAMES=("15m","1H","4H","1D")
def file_hash(path:Path): return hashlib.sha256(path.read_bytes()).hexdigest()
def verify_frozen(path:Path):
    assert path.stat().st_size==EXPECTED_SIZE and file_hash(path)==EXPECTED_SHA, "Frozen source verification failed"
def rows(repo,i,t,start=RAW_START_TS,end=END_TS): return repo.candles(i,t,start,end-1)
def reconcile(repo):
    parts=[]; raw=[]
    for instrument in INSTRUMENTS:
      for timeframe in TIMEFRAMES:
        data=rows(repo,instrument,timeframe); fp=partition_fingerprint(data); raw.extend(data)
        parts.append({"instrument":instrument,"timeframe":timeframe,"requested_start":RAW_START_TS,"requested_end":END_TS,"partition_fingerprint":fp,"count":len(data)})
    overall=dataset_fingerprint(parts)
    metadata={"metadata_key":"canonical-ohlcv-2023-2025","schema_version":CANONICAL_OHLCV_SCHEMA_VERSION,"partition_fingerprint_version":CANONICAL_PARTITION_FINGERPRINT_VERSION,"dataset_fingerprint_version":CANONICAL_DATASET_FINGERPRINT_VERSION,"dataset_fingerprint":overall,"requested_start":RAW_START_TS,"requested_end":END_TS,"partitions":parts,"raw_candle_semantic_hash":overall}
    repo.persist_canonical_dataset_metadata(metadata); return metadata
def prepare_discovery_dataset(repo,metadata):
    now=utc_now(); name="canonical-ohlcv-2023-2025-versioned-v1"
    ds=repo.create_or_get_discovery_dataset(name,RAW_START_TS,END_TS,list(INSTRUMENTS),list(TIMEFRAMES))
    for p in metadata['partitions']:
      data=rows(repo,p['instrument'],p['timeframe']); q={"expected_rows":len(data),"actual_rows":len(data),"missing_rows":0,"duplicate_rows":0,"fingerprint":p['partition_fingerprint'],"status":"COMPLETE","warnings":[]}
      repo.upsert_discovery_partition(ds['id'],p['instrument'],p['timeframe'],data,q)
    with repo.connect() as c:
      c.execute("UPDATE discovery_datasets SET status='COMPLETE',dataset_fingerprint=?,manifest=?,updated_at=?,completed_at=? WHERE id=?",(metadata['dataset_fingerprint'],json.dumps({"canonical_metadata_key":metadata['metadata_key'],"versions":{k:metadata[k] for k in ('schema_version','partition_fingerprint_version','dataset_fingerprint_version')}}),now,now,ds['id']))
    return repo.discovery_dataset(ds['id'])
def main():
 p=argparse.ArgumentParser(); p.add_argument('--source',type=Path,required=True); p.add_argument('--database',type=Path,required=True); a=p.parse_args()
 verify_frozen(a.source)
 if not a.database.exists(): shutil.copy2(a.source,a.database)
 repo=ResearchRepository(a.database); before=reconcile(repo); dataset=prepare_discovery_dataset(repo,before)
 jobs=JobQueue(a.database,autostart=False); service=DiscoveryService(repo,jobs)
 request={"dataset_id":dataset['id'],"instrument":"BTC-USDT","timeframe":"15m","templates":["TREND_PULLBACK_V2","TREND_BREAKOUT_V2","RANGE_MEAN_REVERSION_V2"],"trial_budget":36,"seed":20260723,"mode":"PRICE_ONLY","execution_assumptions":{k:FIXED_EXECUTION[k] for k in ('initial_capital','risk_per_trade','trading_fee','slippage','cooldown_bars')}|{"stop_loss_atr_multiplier":1.0,"risk_reward_ratio":2.0,"allow_long":True,"allow_short":True}}
 started=service.start(request,"v2-pilot"); job=jobs.get(started['job_id']); began=time.monotonic(); service._run_job(started['job_id'],job['request_payload'],lambda *args:None); elapsed=time.monotonic()-began
 after=reconcile(repo); assert before['raw_candle_semantic_hash']==after['raw_candle_semantic_hash'] and before['partitions']==after['partitions'], 'Raw OHLCV changed'
 verify_frozen(a.source)
 run=repo.discovery_dataset(dataset['id'])
 with repo.connect() as c: result=dict(c.execute('SELECT * FROM strategy_discovery_runs WHERE id=?',(started['id'],)).fetchone())
 print(json.dumps({"pilot_run_id":started['id'],"elapsed_seconds":round(elapsed,3),"dataset_fingerprint":after['dataset_fingerprint'],"result":json.loads(result['result']),"frozen_sha":file_hash(a.source),"raw_immutable":True},sort_keys=True))
if __name__=='__main__': main()
