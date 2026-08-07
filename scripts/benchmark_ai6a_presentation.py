"""Local-only deterministic AI-6A projection benchmark; opens no external data or network."""
from __future__ import annotations
import argparse,json,statistics,tempfile,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.presentation import build_latest_presentation
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository,freeze_report_bundle,migrate_audit_database
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from tests.ai_market_analysis.ai4_helpers import base_context

def main(iterations:int)->None:
    with tempfile.TemporaryDirectory(prefix="ai6a-benchmark-") as folder:
        path=Path(folder)/"reports.db";migrate_database(path);migrate_audit_database(path);reports=ReportRepository(path);audits=AuditRepository(path)
        submitted=ReportService(reports).submit(base_context());ReportWorker(reports,lambda request:FakeAIReportProvider(request["model"])).run_once();report=reports.get_report(request_id=submitted["request_id"])
        audit=audit_report(freeze_report_bundle(reports,report["report_id"]),created_at="2027-11-01T00:01:00Z");audits.save_audit(audit)
        samples=[];payload=0
        for _ in range(iterations):
            start=time.perf_counter();value=build_latest_presentation(reports,"ETH-USDT-SWAP","FULL");samples.append((time.perf_counter()-start)*1000);payload=len(json.dumps(value,ensure_ascii=False,separators=(",",":")).encode())
        ordered=sorted(samples);pct=lambda p:ordered[min(len(ordered)-1,int(len(ordered)*p))]
        print(json.dumps({"iterations":iterations,"p50_ms":statistics.median(samples),"p95_ms":pct(.95),"max_ms":max(samples),"payload_bytes":payload},indent=2))
if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--iterations",type=int,default=200);main(parser.parse_args().iterations)
