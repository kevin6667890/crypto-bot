"""Validate frozen development candidates on untouched holdout and OOT data."""
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from dashboard.discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from dashboard.discovery_v2_registry import FIXED_EXECUTION

def stamp(x): return int(datetime.fromisoformat(x).replace(tzinfo=timezone.utc).timestamp())
def main():
 p=argparse.ArgumentParser(); p.add_argument('--database',type=Path,default=Path('data/production-export/2026-09-29/paper_trades.db')); p.add_argument('--candidates',type=Path,default=Path('data/local-research/development-discovery-v2-2024.json')); p.add_argument('--output',type=Path,default=Path('data/local-research/frozen-candidate-validation-2025.json')); a=p.parse_args()
 selected=json.loads(a.candidates.read_text(encoding='utf-8'))['candidates'][:2]
 c=sqlite3.connect(f'file:{a.database.resolve()}?mode=ro',uri=True); c.row_factory=sqlite3.Row
 execution=DiscoveryExecutionConfig(**{k:FIXED_EXECUTION[k] for k in ('initial_capital','risk_per_trade','trading_fee','slippage','cooldown_bars')})
 windows={'holdout':(stamp('2025-01-01'),stamp('2025-07-01')),'oot':(stamp('2025-07-01'),stamp('2026-01-01'))}; results=[]
 for candidate in selected:
  for instrument in ('BTC-USDT','ETH-USDT','SOL-USDT'):
   for frame,step in (('15m',900),('1H',3600),('4H',14400)):
    rows=[dict(x) for x in c.execute('select ts,open,high,low,close,volume,confirmed from historical_candles where instrument=? and timeframe=? and confirmed=1 and ts>=? and ts<? order by ts',(instrument,frame,stamp('2024-01-01'),stamp('2026-01-01')))]
    for label,(begin,end) in windows.items():
     out=run_discovery_candidate_backtest(rows,instrument,frame,candidate['template'],candidate['parameters'],begin,end-step,execution, f"local-2024-2025-{instrument}-{frame}")
     results.append({'candidate_number':candidate['number'],'template':candidate['template'],'instrument':instrument,'timeframe':frame,'window':label,'metrics':out['metrics'],'evidence':out['discovery_evidence']})
 a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps({'protocol':'frozen-development-candidate-validation-v1','selected_from':str(a.candidates),'parameters_frozen_before_holdout':True,'results':results},indent=2,sort_keys=True)+'\n',encoding='utf-8')
 print(json.dumps({'output':str(a.output),'evaluations':len(results)},indent=2))
if __name__=='__main__': main()
