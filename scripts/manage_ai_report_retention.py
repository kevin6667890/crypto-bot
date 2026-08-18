from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.retention_archive import archive_hot_expired,expire_archive_payloads

def main()->int:
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="action",required=True)
    archive=sub.add_parser("archive");archive.add_argument("--database",required=True);archive.add_argument("--archive-directory",required=True);archive.add_argument("--apply",action="store_true");archive.add_argument("--limit",type=int,default=10)
    expire=sub.add_parser("expire");expire.add_argument("--archive-directory",required=True)
    args=parser.parse_args()
    value=archive_hot_expired(args.database,args.archive_directory,apply=args.apply,limit=args.limit) if args.action=="archive" else expire_archive_payloads(args.archive_directory)
    print(json.dumps(value,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
