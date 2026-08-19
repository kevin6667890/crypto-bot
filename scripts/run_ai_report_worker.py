from __future__ import annotations
import argparse,os,signal,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.deepseek_report_provider import DeepSeekAIReportProvider
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository,DEFAULT_REPORT_DB
from dashboard.ai_market_analysis.live_provider_guard import assert_live_provider_allowed
def provider_secret_file()->str:
 path=os.getenv("AI_REPORT_API_KEY_FILE","")
 if not path:return ""
 selected=Path(path)
 if not selected.is_file():raise RuntimeError("AI_REPORT_API_KEY_FILE is missing")
 return str(selected)
def load_provider_secret()->None:provider_secret_file()
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--database",default=str(DEFAULT_REPORT_DB));p.add_argument("--once",action="store_true");a=p.parse_args()
 if os.getenv("AI_MARKET_REPORT_WORKER_ENABLED","false").lower()!="true":print("AI report worker disabled");return 0
 live_enabled=os.getenv("AI_REPORT_LIVE_PROVIDER_ENABLED","false").lower()=="true"
 if os.getenv("AI_REPORT_LIVE_PROVIDER_ENABLED","false").lower()=="true":load_provider_secret()
 secret_file=provider_secret_file() if live_enabled else ""
 def factory(request):
  if request["provider"]=="fake":return FakeAIReportProvider(request["model"])
  assert_live_provider_allowed()
  return DeepSeekAIReportProvider(request["model"],api_key_file=secret_file)
 repo=ReportRepository(a.database);worker=ReportWorker(repo,factory);worker.recover()
 stopping=False
 def stop(_signum,_frame):
  nonlocal stopping
  stopping=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while not stopping:
  did=worker.run_once()
  if a.once:return 0
  if not did:time.sleep(1)
 return 0
if __name__=="__main__":raise SystemExit(main())
