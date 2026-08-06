from __future__ import annotations
import argparse,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.deepseek_report_provider import DeepSeekAIReportProvider
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository,DEFAULT_REPORT_DB
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--database",default=str(DEFAULT_REPORT_DB));p.add_argument("--once",action="store_true");a=p.parse_args()
 if os.getenv("AI_MARKET_REPORT_WORKER_ENABLED","false").lower()!="true":print("AI report worker disabled");return 0
 repo=ReportRepository(a.database);factory=lambda r:FakeAIReportProvider(r["model"]) if r["provider"]=="fake" else DeepSeekAIReportProvider(r["model"]);worker=ReportWorker(repo,factory);worker.recover()
 while True:
  did=worker.run_once()
  if a.once:return 0
  if not did:time.sleep(1)
if __name__=="__main__":raise SystemExit(main())
