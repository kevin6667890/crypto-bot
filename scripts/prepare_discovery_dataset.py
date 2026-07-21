from __future__ import annotations
import argparse
from pathlib import Path
from dashboard.research_repository import ResearchRepository
from dashboard.dataset_service import DiscoveryDatasetService
p=argparse.ArgumentParser();p.add_argument('--start',default='2024-01-01');p.add_argument('--end',default='2026-01-01');p.add_argument('--instruments',nargs='+',default=['BTC-USDT','ETH-USDT','SOL-USDT']);p.add_argument('--timeframes',nargs='+',default=['15m','1H','4H','1D']);p.add_argument('--database',default='data/research.db');a=p.parse_args()
if (a.start,a.end)!=('2024-01-01','2026-01-01'): raise SystemExit('Only fixed discovery range [2024-01-01, 2026-01-01) is permitted.')
print(DiscoveryDatasetService(ResearchRepository(Path(a.database))).prepare({'instruments':a.instruments,'timeframes':a.timeframes}))
