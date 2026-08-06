"""Generate an AI-4 Shadow report from a synthetic/context fixture; fake and offline by default."""
from __future__ import annotations
import argparse,json,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.context_adapter import build_market_analysis_context
from dashboard.ai_market_analysis.deepseek_report_provider import DeepSeekAIReportProvider
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from dashboard.ai_market_analysis.report_fact_registry import build_fact_registry
from dashboard.ai_market_analysis.quality import epoch
from dashboard.ai_market_analysis.versions import SUPPORTED_TIMEFRAMES

def main()->int:
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--fixture",type=Path,required=True);p.add_argument("--mode",choices=("QUICK","FULL","POSITION_AWARE"),default="FULL");p.add_argument("--position-fixture",type=Path);p.add_argument("--macro-fixture",type=Path);p.add_argument("--database",type=Path);p.add_argument("--request-id");p.add_argument("--provider",choices=("fake","deepseek"),default="fake");p.add_argument("--model");p.add_argument("--allow-live-provider",action="store_true");p.add_argument("--no-persist",action="store_true");p.add_argument("--show-token-budget",action="store_true");p.add_argument("--show-fact-registry",action="store_true");p.add_argument("--show-prompt-hash",action="store_true");p.add_argument("--output",type=Path,required=True);a=p.parse_args()
 if a.provider=="deepseek" and not a.allow_live_provider:p.error("deepseek requires --allow-live-provider")
 if a.provider=="deepseek" and (a.fixture.parent.resolve()!= (ROOT/"fixtures/ai_market_analysis").resolve() or "golden" not in a.fixture.name):p.error("live provider is restricted to a synthetic golden fixture")
 raw=json.loads(a.fixture.read_text(encoding="utf-8"));decision=epoch(raw.get("decision_time") or raw["as_of"]);instrument=raw.get("instrument","ETH-USDT-SWAP")
 if "context_id" in raw:base=raw
 else:base=build_market_analysis_context({tf:list((raw.get("timeframes") or raw).get(tf,[])) for tf in SUPPORTED_TIMEFRAMES if tf!="1W"},instrument,decision,a.mode,orderflow=raw.get("orderflow"),auxiliary=raw.get("auxiliary"))
 base={**base,"provenance":{**base.get("provenance",{}),"fixture":True}}
 position=json.loads(a.position_fixture.read_text(encoding="utf-8")) if a.position_fixture else None;macro=json.loads(a.macro_fixture.read_text(encoding="utf-8")) if a.macro_fixture else []
 db=(Path(tempfile.mkdtemp())/"ai_market_reports.db") if a.no_persist or not a.database else a.database;migrate_database(db);repo=ReportRepository(db);model=a.model or ("fake-ai4" if a.provider=="fake" else "")
 submitted=ReportService(repo).submit(base,mode=a.mode,position_source="USER_DECLARED" if position else "NONE",position_plan=position,macro_evidence=macro,provider=a.provider,model=model,current_mark=next((f.get("last_confirmed_close",{}).get("value") for f in base.get("timeframe_structures",[]) if f.get("timeframe")=="15m"),None))
 if a.request_id and a.request_id!=submitted["request_id"]:raise ValueError("--request-id does not match deterministic request identity")
 factory=(lambda req:FakeAIReportProvider(model=req["model"])) if a.provider=="fake" else (lambda req:DeepSeekAIReportProvider(model=req["model"]))
 ReportWorker(repo,factory).run_once();report=repo.get_report(request_id=submitted["request_id"])
 if not report:raise RuntimeError(repo.status(submitted["request_id"])["status"])
 a.output.write_text(json.dumps(report["response"],ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
 summary={"base_context_id":base["context_id"],"enriched_context_id":submitted["context_id"],"request_identity":submitted["request_id"],"fact_count":submitted["fact_count"],"numeric_registry_count":submitted["numeric_registry_count"],"prompt_token_estimate":submitted["prompt_token_estimate"],"omitted_facts":submitted["omitted_fact_ids"],"provider":a.provider,"validation_result":"VALID","report_hash":report["response_hash"],"audit_status":report["audit_status"],"live_provider_validation":"REQUIRES_RUNTIME_AUDIT" if a.provider=="deepseek" else "NOT_RUN"}
 if a.show_fact_registry:summary["fact_registry"]=build_fact_registry(repo.load_context(submitted["context_id"]))
 if not a.show_token_budget:summary.pop("prompt_token_estimate")
 if a.show_prompt_hash:summary["prompt_hash"]=submitted["prompt_hash"]
 print(json.dumps(summary,ensure_ascii=False,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
