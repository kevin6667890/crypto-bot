from __future__ import annotations
import argparse
from dashboard.ai_market_analysis.report_repository import DEFAULT_REPORT_DB,migrate_database

def main()->int:
    p=argparse.ArgumentParser(description="Explicitly migrate the isolated AI report database")
    p.add_argument("--database",default=str(DEFAULT_REPORT_DB));a=p.parse_args();print(migrate_database(a.database));return 0
if __name__=="__main__":raise SystemExit(main())
