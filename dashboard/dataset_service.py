"""Fixed, auditable discovery dataset preparation (public OKX candles only)."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from typing import Any
from .okx_history import INSTRUMENTS, TIMEFRAME_SECONDS, OkxHistoryClient

DATASET_NAME="crypto-discovery-2024-2025-v1"; START_TS=1704067200; END_TS=1767225600
SMOKE_MAX_SECONDS = 31 * 86400
def fingerprint(rows:list[dict[str,Any]])->str:
    canonical=[(int(x['ts']),float(x['open']),float(x['high']),float(x['low']),float(x['close']),float(x['volume'])) for x in sorted(rows,key=lambda x:int(x['ts']))]
    return hashlib.sha256(json.dumps(canonical,separators=(',',':')).encode()).hexdigest()
def quality(rows:list[dict[str,Any]], timeframe:str,start_ts:int,end_ts:int)->dict[str,Any]:
    step=TIMEFRAME_SECONDS[timeframe]; alignment_offset=16*3600 if timeframe=='1D' else 0; seen=set(); duplicates=0; malformed=[]; misaligned=[]; unconfirmed=[]; valid=[]
    for row in rows:
        ts=int(row['ts']); o,h,l,c,v=map(float,(row['open'],row['high'],row['low'],row['close'],row['volume']))
        if not bool(row.get('confirmed', 1)):
            unconfirmed.append(ts); continue
        if ts in seen: duplicates+=1; continue
        seen.add(ts)
        # OKX daily bars are exchange-day (UTC+8) candles, timestamped at 16:00 UTC.
        if (ts-alignment_offset)%step: misaligned.append(ts)
        if min(o,h,l,c)<=0 or v<0 or h<max(o,c,l) or l>min(o,c,h): malformed.append(ts)
        else: valid.append(row)
    expected=(end_ts-start_ts)//step; actual=len([x for x in valid if start_ts<=int(x['ts'])<end_ts]); missing=max(0,expected-actual)
    warnings=(['Missing bars are never forward-filled.'] if missing else []) + (['Unconfirmed candles were rejected.'] if unconfirmed else [])
    return {'expected_rows':expected,'actual_rows':actual,'missing_rows':missing,'duplicate_rows':duplicates,'confirmed_rows':actual,'unconfirmed_rows':len(unconfirmed),'malformed_rows':malformed[:100],'misaligned_rows':misaligned[:100],'status':'COMPLETE' if not(missing or duplicates or malformed or misaligned or unconfirmed) else 'INCOMPLETE','fingerprint':fingerprint(valid),'warnings':warnings}

class DiscoveryDatasetService:
    def __init__(self,repository): self.repository=repository; self.history=OkxHistoryClient(repository)
    def prepare(self, request:dict[str,Any], checkpoint=None, cancelled=None)->dict[str,Any]:
        start=int(request.get('start_ts',START_TS)); end=int(request.get('end_ts',END_TS)); instruments=request.get('instruments',['BTC-USDT','ETH-USDT','SOL-USDT']); timeframes=request.get('timeframes',['15m','1H','4H','1D'])
        smoke_test=bool(request.get('smoke_test',False))
        if smoke_test:
            if not start < end or end-start > SMOKE_MAX_SECONDS: raise ValueError('Smoke datasets must be greater than zero and no longer than 31 days.')
            label="-".join(instruments); name=f"discovery-smoke-{label}-{datetime.fromtimestamp(start,timezone.utc):%Y%m%d}-{datetime.fromtimestamp(end,timezone.utc):%Y%m%d}"
        elif start!=START_TS or end!=END_TS: raise ValueError('Official discovery dataset is fixed at [2024-01-01, 2026-01-01); use --smoke-test for a bounded cache check.')
        else: name=DATASET_NAME
        if not set(instruments)<=INSTRUMENTS or not set(timeframes)<=set(TIMEFRAME_SECONDS): raise ValueError('Unsupported discovery partition.')
        dataset=self.repository.create_or_get_discovery_dataset(name,start,end,instruments,timeframes,smoke_test=smoke_test); total=len(instruments)*len(timeframes)
        for n, (instrument, tf) in enumerate(((instrument, timeframe) for instrument in instruments for timeframe in timeframes), 1):
            if cancelled: cancelled()
            existing=self.repository.discovery_partition(dataset['id'],instrument,tf)
            if existing and existing['status']=='COMPLETE': continue
            if checkpoint: checkpoint(None, int((n-1)*95/max(total,1)), 'discovery.dataset.downloading', {'instrument':instrument,'timeframe':tf})
            # The established client paginates, retries and writes confirmed candles into the shared cache.
            self.history.get_candles(instrument,tf,start,end,0,cancelled=cancelled)
            rows=self.repository.candles(instrument,tf,start,end-1); q=quality(rows,tf,start,end)
            self.repository.upsert_discovery_partition(dataset['id'],instrument,tf,rows,q)
        return self.repository.finish_discovery_dataset(dataset['id'])
    def flow_audit(self,dataset_id:int)->list[dict[str,Any]]:
        # Existing project storage contains no verified public historical OI and only optional flow rows.
        report=[]
        for instrument in ['BTC-USDT','ETH-USDT','SOL-USDT']:
            for feature in ('CVD','OI'):
                report.append({'instrument':instrument,'feature':feature,'requested_start':START_TS,'requested_end':END_TS,'actual_coverage':None,'missing_intervals':[{'start':START_TS,'end':END_TS}],'source':'No verified public two-year source configured','status':'UNAVAILABLE','limitations':'No fabricated CVD/OI; PRICE_ONLY required for main ranking.'})
        self.repository.replace_flow_coverage(dataset_id,report); return report
