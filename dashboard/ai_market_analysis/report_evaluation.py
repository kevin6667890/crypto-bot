"""Deterministic offline evaluation manifest and regression comparison."""
from __future__ import annotations
from collections import Counter
from time import perf_counter
from datetime import datetime,timezone
from typing import Any,Callable
from .canonical import identity,stable_hash
from .report_audit_service import audit_report
from .report_evaluation_models import default_case_manifest
from .versions import AI_REPORT_EVALUATION_VERSION,AI_REPORT_AUDIT_POLICY_VERSION
from .report_audit_identity import AUDIT_SOURCE_VERSIONS

def _now():return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def evaluation_identity(manifest:dict[str,Any])->str:
    stable={k:v for k,v in manifest.items() if k not in {"started_at","completed_at","duration_ms","environment_summary"}}
    return identity("evaluation",{"version":AI_REPORT_EVALUATION_VERSION,"policy":AI_REPORT_AUDIT_POLICY_VERSION,"manifest":stable})

def default_manifest(code_commit:str="UNKNOWN")->dict[str,Any]:
    value={"evaluation_version":AI_REPORT_EVALUATION_VERSION,"audit_policy_version":AI_REPORT_AUDIT_POLICY_VERSION,
      "code_commit":code_commit,"source_versions":dict(AUDIT_SOURCE_VERSIONS),"cases":default_case_manifest(),"no_lookahead_proof":{"frozen_inputs_only":True,"future_outcomes_used":False}}
    return {**value,"evaluation_run_id":evaluation_identity(value),"deterministic_hash":stable_hash(value)}

def evaluate(manifest:dict[str,Any],bundle_factory:Callable[[dict[str,Any]],dict[str,Any]],*,fail_fast:bool=False,max_cases:int|None=None)->dict[str,Any]:
    started_at=_now();started=perf_counter();results=[]
    for case in manifest["cases"][:max_cases]:
        tick=perf_counter();audit=audit_report(bundle_factory(case),created_at="1970-01-01T00:00:00Z")
        expected=set(case["expected_failure_codes"]);actual=set(audit["hard_failures"]);ok=audit["status"]==case["expected_status"] and expected<=actual
        results.append({"case_id":case["case_id"],"expected_status":case["expected_status"],"expected_failure_codes":sorted(expected),
          "actual_status":audit["status"],"actual_failure_codes":sorted(actual),"score":audit["scorecard"]["overall"],"audit_id":audit["audit_id"],
          "payload_hash":audit["payload_hash"],"passed":ok,"duration_ms":round((perf_counter()-tick)*1000,3)})
        if fail_fast and not ok:break
    durations=sorted(x["duration_ms"] for x in results);distribution=Counter(code for x in results for code in x["actual_failure_codes"])
    payload={"evaluation_run_id":manifest.get("evaluation_run_id") or evaluation_identity(manifest),"evaluation_version":AI_REPORT_EVALUATION_VERSION,
      "audit_policy_version":AI_REPORT_AUDIT_POLICY_VERSION,"case_count":len(results),"pass_count":sum(x["passed"] for x in results),
      "fail_count":sum(not x["passed"] for x in results),"failure_code_distribution":dict(sorted(distribution.items())),"results":results,
      "duration_ms":round((perf_counter()-started)*1000,3),"audit_p50_ms":durations[len(durations)//2] if durations else 0,
      "audit_p95_ms":durations[min(len(durations)-1,int(len(durations)*.95))] if durations else 0,"audit_max_ms":max(durations,default=0),
      "no_lookahead_proof":manifest.get("no_lookahead_proof"),"started_at":started_at,"completed_at":_now()}
    deterministic={k:v for k,v in payload.items() if k not in {"started_at","completed_at","duration_ms","audit_p50_ms","audit_p95_ms","audit_max_ms"}}
    deterministic["results"]=[{k:v for k,v in x.items() if k!="duration_ms"} for x in results]
    payload["deterministic_hash"]=stable_hash(deterministic);payload["result_artifact_hash"]=payload["deterministic_hash"];return payload

def baseline_diff(result:dict[str,Any],baseline:dict[str,Any])->dict[str,Any]:
    keys=("case_count","pass_count","fail_count","failure_code_distribution","audit_p50_ms","audit_p95_ms","audit_max_ms")
    return {k:{"baseline":baseline.get(k),"actual":result.get(k)} for k in keys if baseline.get(k)!=result.get(k)}
