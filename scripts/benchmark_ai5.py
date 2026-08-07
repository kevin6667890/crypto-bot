from __future__ import annotations
import json,statistics,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.ai_market_analysis.canonical import canonical_json
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_claim_extractor import extract_claims
from dashboard.ai_market_analysis.report_numeric_audit import audit_numeric_claims
from dashboard.ai_market_analysis.report_reference_audit import audit_references
from dashboard.ai_market_analysis.report_semantic_audit import audit_semantics
from dashboard.ai_market_analysis.report_contradiction_audit import audit_contradictions
from dashboard.ai_market_analysis.report_repetition_audit import audit_repetition
from dashboard.ai_market_analysis.report_coverage_audit import audit_coverage
from dashboard.ai_market_analysis.report_position_audit import audit_position
from dashboard.ai_market_analysis.report_macro_audit import audit_macro
from dashboard.ai_market_analysis.report_safety_audit import audit_safety
from dashboard.ai_market_analysis.report_level_audit import audit_report_levels
from dashboard.ai_market_analysis.report_scenario_audit import audit_report_scenarios
from dashboard.ai_market_analysis.report_evaluation import default_manifest,evaluate
from tests.ai_market_analysis.ai5_helpers import golden_bundle
from tests.ai_market_analysis.test_report_evaluation import case_bundle

def measure(fn,runs=100):
    values=[]
    for _ in range(runs):start=time.perf_counter();fn();values.append((time.perf_counter()-start)*1000)
    values.sort();return {"p50_ms":round(statistics.median(values),3),"p95_ms":round(values[int(len(values)*.95)-1],3),"max_ms":round(max(values),3)}

def main():
    b=golden_bundle();claims=extract_claims(b["report_id"],b["report"]);facts=b["fact_registry"]["facts"]
    components={
      "claim_extraction":lambda:extract_claims(b["report_id"],b["report"]),
      "numeric_audit":lambda:audit_numeric_claims(claims,b["numeric_registry"]),
      "semantic_reference":lambda:(audit_references(claims,b["fact_registry"]),audit_semantics(claims,facts)),
      "contradiction":lambda:audit_contradictions(claims,b["context"],facts),"repetition":lambda:audit_repetition(claims),
      "coverage_position_macro_safety":lambda:(audit_coverage(b["report"],claims,b["context"],facts),audit_position(claims,b["position_context"]),audit_macro(claims,b["macro_evidence_set"],b["context"]["decision_time"]),audit_safety(b["report"],claims)),
      "level_audit":lambda:audit_report_levels(b["report"],b["fact_registry"]),"scenario_audit":lambda:audit_report_scenarios(b["report"],b["fact_registry"]),
      "full_audit":lambda:audit_report(b,created_at="1970-01-01T00:00:00Z")}
    result={name:measure(fn) for name,fn in components.items()};audit=audit_report(b,created_at="1970-01-01T00:00:00Z")
    start=time.perf_counter();evaluation=evaluate(default_manifest("benchmark"),case_bundle);result["evaluation_80_ms"]=round((time.perf_counter()-start)*1000,3)
    result["audit_payload_bytes"]=len(canonical_json(audit).encode("utf-8"));result["case_count"]=evaluation["case_count"];result["case_pass_count"]=evaluation["pass_count"]
    print(json.dumps(result,sort_keys=True))
if __name__=="__main__":main()
