"""Reproducible no-network AI-4 component benchmark over the golden ETH fixture."""
from __future__ import annotations
import json,statistics,tempfile,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tests.ai_market_analysis.ai4_helpers import macro_items
from tests.ai_market_analysis.helpers import golden_datasets
from tests.ai_market_analysis.test_ai3_golden_context import orderflow
from dashboard.ai_market_analysis.context_adapter import build_market_analysis_context
from dashboard.ai_market_analysis.enriched_context import build_enriched_context
from dashboard.ai_market_analysis.macro_evidence import freeze_macro_evidence_set
from dashboard.ai_market_analysis.position_context import none_position_context
from dashboard.ai_market_analysis.report_basic_validation import validate_report
from dashboard.ai_market_analysis.report_context_compiler import compile_report_context
from dashboard.ai_market_analysis.report_fact_registry import build_fact_registry
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_response_parser import parse_report_response
from dashboard.ai_market_analysis.report_service import ReportService
from dashboard.ai_market_analysis.versions import AI_REPORT_PROMPT_VERSION

def measure(fn,runs=10):
 values=[];result=None
 for _ in range(runs):
  start=time.perf_counter();result=fn();values.append((time.perf_counter()-start)*1000)
 return result,{"median_ms":round(statistics.median(values),3),"p95_ms":round(sorted(values)[max(0,int(len(values)*.95)-1)],3)}
def main()->int:
 def cold():
  datasets,decision=golden_datasets();value=build_market_analysis_context(datasets,"ETH-USDT-SWAP",decision,orderflow=orderflow());value["provenance"]["fixture"]=True;return value
 base,base_perf=measure(cold,3);position,position_perf=measure(lambda:none_position_context(base["instrument"]));macro,macro_perf=measure(lambda:freeze_macro_evidence_set(macro_items(),base["decision_time"]));enriched,enriched_perf=measure(lambda:build_enriched_context(base,position,macro));registry,fact_perf=measure(lambda:build_fact_registry(enriched));compiled,prompt_perf=measure(lambda:compile_report_context(registry,"FULL"));request={"compiled_context":compiled,"mode":"FULL","context_id":enriched["enriched_context_id"],"request_id":"benchmark","language":"zh-CN","prompt_version":AI_REPORT_PROMPT_VERSION,"model":"fake-ai4","macro_items":macro["items"],"position_source":"NONE"};report=parse_report_response(FakeAIReportProvider().generate(request).raw_text);_,validation_perf=measure(lambda:validate_report(report,request,registry));path=Path(tempfile.mkdtemp())/"reports.db";migrate_database(path);repo=ReportRepository(path);_,post_perf=measure(lambda:ReportService(repo).submit(base,mode="FULL",macro_evidence=macro_items()),3)
 print(json.dumps({"cold_context":base_perf,"position":position_perf,"macro_normalization":macro_perf,"enriched_context":enriched_perf,"fact_registry":fact_perf,"prompt_compile":prompt_perf,"basic_validation":validation_perf,"post_service":post_perf,"prompt_tokens_estimate":compiled["token_estimate"],"report_json_bytes":len(json.dumps(report,ensure_ascii=False).encode()),"context_json_bytes":len(json.dumps(base,ensure_ascii=False).encode())},sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
