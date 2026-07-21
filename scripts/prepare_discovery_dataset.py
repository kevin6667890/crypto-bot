from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

# Allow ``python scripts/prepare_discovery_dataset.py`` from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dashboard.research_repository import ResearchRepository
from dashboard.dataset_service import DiscoveryDatasetService
p=argparse.ArgumentParser();p.add_argument('--smoke-test',action='store_true');p.add_argument('--start',default='2024-01-01');p.add_argument('--end',default='2026-01-01');p.add_argument('--instruments',nargs='+',default=['BTC-USDT','ETH-USDT','SOL-USDT']);p.add_argument('--timeframes',nargs='+',default=['15m','1H','4H','1D']);p.add_argument('--database',default='data/research.db');a=p.parse_args()
def stamp(value:str)->int:return int(datetime.strptime(value,'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp())
if not a.smoke_test and (a.start,a.end)!=('2024-01-01','2026-01-01'): raise SystemExit('Only fixed discovery range [2024-01-01, 2026-01-01) is permitted without --smoke-test.')
def progress(_ignored,pct,message,args): print(f'{pct:3d}% {message} {args}')
print(DiscoveryDatasetService(ResearchRepository(Path(a.database))).prepare({'start_ts':stamp(a.start),'end_ts':stamp(a.end),'instruments':a.instruments,'timeframes':a.timeframes,'smoke_test':a.smoke_test},checkpoint=progress))
