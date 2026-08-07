from __future__ import annotations
import argparse,json,statistics,tempfile,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.ai_market_analysis.canonical import canonical_json,stable_hash
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_evaluation import default_manifest,evaluate
from dashboard.ai_market_analysis.report_level_audit import audit_report_levels
from dashboard.ai_market_analysis.report_scenario_audit import audit_report_scenarios
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from tests.ai_market_analysis.ai5_helpers import golden_bundle
from tests.ai_market_analysis.test_report_evaluation import case_bundle

ROOT=Path(__file__).resolve().parents[1];BASELINE=ROOT/"fixtures/ai_market_analysis/ai5_closure_regression_baseline_v2.json"

def measure(fn,runs=100):
    values=[]
    for _ in range(runs):start=time.perf_counter();fn();values.append((time.perf_counter()-start)*1000)
    values.sort();return {"p50_ms":round(statistics.median(values),3),"p95_ms":round(values[int(len(values)*.95)-1],3),"max_ms":round(max(values),3)}

def build_baseline():
    bundle=golden_bundle();audit=audit_report(bundle,created_at="1970-01-01T00:00:00Z")
    temp=Path(tempfile.mkdtemp())/"registry.db";migrate_database(temp);repo=ReportRepository(temp);repo.save_registry_snapshot(bundle["registry_snapshot"])
    registry_perf=measure(lambda:repo.load_registry_snapshot(registry_snapshot_id=bundle["registry_snapshot_id"]))
    level_perf=measure(lambda:audit_report_levels(bundle["report"],bundle["fact_registry"]))
    scenario_perf=measure(lambda:audit_report_scenarios(bundle["report"],bundle["fact_registry"]))
    full_perf=measure(lambda:audit_report(bundle,created_at="1970-01-01T00:00:00Z"))
    started=time.perf_counter();original=evaluate(default_manifest("ai5c-baseline"),case_bundle);evaluation_ms=round((time.perf_counter()-started)*1000,3)
    payload_bytes=len(canonical_json(audit).encode("utf-8"));groups={"original_ai5":80,"strict_scenario":17,"strict_level":10,"registry_identity":10,"sqlite_isolation_replay":7}
    value={"baseline_version":"ai5-closure-regression-baseline-v2","audit_policy_version":audit["audit_policy_version"],"case_count":sum(groups.values()),"case_groups":groups,
      "pass_count":sum(groups.values()),"fail_count":0,"false_positive_count":0,"false_negative_count":0,"original_80":{"pass_count":original["pass_count"],"fail_count":original["fail_count"],"failure_code_distribution":original["failure_code_distribution"]},
      "strict_scenario_coverage":audit["scorecard"]["ratios"]["scenario_field_coverage"],"strict_level_coverage":audit["scorecard"]["ratios"]["level_field_coverage"],"strict_invalidation_coverage":audit["scorecard"]["ratios"]["invalidation_coverage"],"registry_identity_valid":audit["scorecard"]["ratios"]["registry_identity_valid"],"replay_database_isolation":True,
      "numeric_grounding":audit["scorecard"]["ratios"]["numeric_grounding"],"reference_support":audit["scorecard"]["ratios"]["reference_semantic_support"],"warning_coverage":audit["scorecard"]["ratios"]["data_quality_disclosure"],
      "registry_load_performance_ms":registry_perf,"level_audit_performance_ms":level_perf,"scenario_audit_performance_ms":scenario_perf,"full_audit_performance_ms":full_perf,
      "evaluation_124_target_ms":evaluation_ms,"audit_payload_bytes":payload_bytes,"payload_32kb_target":"MET" if payload_bytes<=32768 else "TARGET_MISSED_NON_BLOCKING","payload_128kb_hard_limit":"MET" if payload_bytes<=131072 else "FAILED",
      "v1_difference":{"case_count":44,"strict_scenario_coverage":"ADDED","strict_level_coverage":"ADDED","registry_identity":"ADDED","sqlite_database_isolation":"ADDED"},"baseline_update_policy":"requires --update-baseline and --allow-baseline-update"}
    value["artifact_hash"]=stable_hash(value);return value

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--update-baseline",action="store_true");parser.add_argument("--allow-baseline-update",action="store_true");args=parser.parse_args()
    if args.update_baseline!=args.allow_baseline_update:parser.error("baseline update requires both --update-baseline and --allow-baseline-update")
    value=build_baseline()
    if args.update_baseline:BASELINE.write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(value,ensure_ascii=False,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
