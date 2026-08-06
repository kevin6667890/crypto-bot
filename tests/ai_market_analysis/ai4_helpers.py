from __future__ import annotations
import copy,json
from functools import lru_cache
from pathlib import Path
from dashboard.ai_market_analysis.context_adapter import build_market_analysis_context
from tests.ai_market_analysis.helpers import golden_datasets
from tests.ai_market_analysis.test_ai3_golden_context import orderflow
ROOT=Path(__file__).resolve().parents[2]
@lru_cache
def _base():
 d,t=golden_datasets();c=build_market_analysis_context(d,"ETH-USDT-SWAP",t,orderflow=orderflow());c["provenance"]["fixture"]=True;return c
def base_context():return copy.deepcopy(_base())
def position_plan():return json.loads((ROOT/"fixtures/ai_market_analysis/golden_eth_position_plan_v1.json").read_text())
def macro_items():return json.loads((ROOT/"fixtures/ai_market_analysis/golden_macro_evidence_v1.json").read_text())
