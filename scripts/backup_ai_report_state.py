from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.backup_restore import create_consistent_backup

def main()->int:
    parser=argparse.ArgumentParser(description="Create a consistent secured AI report state backup")
    parser.add_argument("--database",required=True);parser.add_argument("--output-directory",required=True)
    parser.add_argument("--backup-id");parser.add_argument("--state-file",action="append",default=[],metavar="KIND=PATH")
    args=parser.parse_args();state={}
    for item in args.state_file:
        if "=" not in item:parser.error("--state-file must be KIND=PATH")
        kind,path=item.split("=",1);state[kind]=path
    print(json.dumps(create_consistent_backup(args.database,args.output_directory,state_files=state,backup_id=args.backup_id),sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
