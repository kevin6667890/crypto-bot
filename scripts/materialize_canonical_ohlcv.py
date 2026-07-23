"""Explicit, sequential operational materializer for canonical OKX OHLCV."""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.dataset_service import RAW_START_TS, END_TS, fingerprint, quality
from dashboard.okx_history import INSTRUMENTS, TIMEFRAME_SECONDS, OkxHistoryClient
from dashboard.research_repository import ResearchRepository

COMPLETE, INCOMPLETE, VALIDATION, NETWORK = 0, 2, 3, 4

def partitions(args):
    if args.all:
        return [(i, t) for i in ("BTC-USDT","ETH-USDT","SOL-USDT") for t in ("1D","4H","1H","15m")]
    if not args.instrument or not args.timeframe: raise ValueError("--instrument and --timeframe are required unless --all")
    return [(args.instrument,args.timeframe)]

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--database',required=True); p.add_argument('--instrument'); p.add_argument('--timeframe'); p.add_argument('--start',type=int,default=RAW_START_TS); p.add_argument('--end',type=int,default=END_TS); p.add_argument('--resume',action='store_true'); p.add_argument('--audit-only',action='store_true'); p.add_argument('--all',action='store_true'); p.add_argument('--max-pages',type=int); a=p.parse_args()
    repo=ResearchRepository(Path(a.database)); client=OkxHistoryClient(repo); code=COMPLETE
    for inst,tf in partitions(a):
        began=time.monotonic(); before=repo.candles(inst,tf,a.start,a.end-1)
        try:
            result={"rows_reused":len(before),"rows_inserted":0,"pages_requested":0,"retries":0,"duplicate_count":0,"status":"AUDIT"} if a.audit_only else client.materialize_partition(inst,tf,a.start,a.end,max_pages=a.max_pages)
        except Exception as e:
            print(json.dumps({"instrument":inst,"timeframe":tf,"status":"EXCHANGE_FAILURE","error":str(e),"resume_command":f"python scripts/materialize_canonical_ohlcv.py --database {a.database} --instrument {inst} --timeframe {tf} --resume"},sort_keys=True)); code=max(code,NETWORK); continue
        rows=repo.candles(inst,tf,a.start,a.end-1); report=quality(rows,tf,a.start,a.end); result.update({"instrument":inst,"timeframe":tf,"requested_range":[a.start,a.end],"cached_range_before":[before[0]['ts'],before[-1]['ts']] if before else None,"first_timestamp":report['actual_first_ts'],"last_timestamp":report['actual_last_ts'],"gap_count":report['gap_count'],"duplicate_count":report['duplicate_rows'],"fingerprint":report['fingerprint'],"current_status":report['status'],"elapsed_seconds":round(time.monotonic()-began,3),"resume_command":f"python scripts/materialize_canonical_ohlcv.py --database {a.database} --instrument {inst} --timeframe {tf} --resume"})
        print(json.dumps(result,sort_keys=True)); code=max(code, COMPLETE if report['status']=='COMPLETE' else INCOMPLETE)
    return code
if __name__=='__main__': raise SystemExit(main())
