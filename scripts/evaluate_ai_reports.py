from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.ai_market_analysis.report_evaluation import evaluate,baseline_diff

def main()->int:
    p=argparse.ArgumentParser(description="Offline frozen AI report audit evaluation")
    p.add_argument("--manifest",required=True);p.add_argument("--output-dir",required=True);p.add_argument("--fail-fast",action="store_true")
    p.add_argument("--update-baseline",action="store_true");p.add_argument("--allow-baseline-update",action="store_true");p.add_argument("--workers",type=int,default=1);p.add_argument("--max-cases",type=int);p.add_argument("--filter")
    a=p.parse_args()
    if a.workers<1 or a.workers>4:p.error("workers must be 1..4")
    if a.update_baseline and not a.allow_baseline_update:p.error("baseline update requires --allow-baseline-update")
    manifest=json.loads(Path(a.manifest).read_text(encoding="utf-8"));base=Path(a.manifest).parent
    if a.filter:manifest["cases"]=[c for c in manifest["cases"] if a.filter in c["case_id"]]
    def bundle(case):return json.loads((base/case["bundle_fixture"]).read_text(encoding="utf-8"))
    result=evaluate(manifest,bundle,fail_fast=a.fail_fast,max_cases=a.max_cases);out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    (out/"evaluation-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    baseline=out/"baseline.json"
    if baseline.exists():(out/"baseline-diff.json").write_text(json.dumps(baseline_diff(result,json.loads(baseline.read_text(encoding="utf-8"))),indent=2),encoding="utf-8")
    if a.update_baseline:baseline.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"run_id":result["evaluation_run_id"],"cases":result["case_count"],"passed":result["pass_count"],"failed":result["fail_count"]}));return 0 if not result["fail_count"] else 2
if __name__=="__main__":raise SystemExit(main())
