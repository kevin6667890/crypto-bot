from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.backup_restore import verify_isolated_restore

def main()->int:
    parser=argparse.ArgumentParser(description="Verify an AI report backup in an isolated temporary directory")
    parser.add_argument("--backup-directory",required=True);args=parser.parse_args()
    print(json.dumps(verify_isolated_restore(args.backup_directory),sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
