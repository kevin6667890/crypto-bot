from __future__ import annotations
import argparse,json
from pathlib import Path
from .ledger import ExperimentLedger
def main():
 p=argparse.ArgumentParser(prog="research"); p.add_argument("--ledger",type=Path,default=Path(".research/research_engine_v1.db")); sub=p.add_subparsers(dest="cmd",required=True)
 for name in ("status","report"): sub.add_parser(name)
 for name in ("candidate","hypothesis"): q=sub.add_parser(name);q.add_argument("id")
 args=p.parse_args(); ledger=ExperimentLedger(args.ledger)
 if args.cmd in ("status","report"): print(json.dumps(ledger.summary(),indent=2))
 else: print(json.dumps(ledger.query(args.cmd,args.id),indent=2))
if __name__=="__main__": main()
