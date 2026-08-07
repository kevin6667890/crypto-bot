"""Pure deterministic audit orchestration over a fully frozen input bundle."""
from __future__ import annotations
from datetime import datetime,timezone
from typing import Any
from .canonical import stable_hash
from .report_audit_identity import AUDIT_SOURCE_VERSIONS,audit_identity,deterministic_payload_hash
from .report_audit_policy import POLICY
from .report_claim_extractor import extract_claims
from .report_numeric_audit import audit_numeric_claims
from .report_reference_audit import audit_references
from .report_semantic_audit import audit_semantics
from .report_contradiction_audit import audit_contradictions
from .report_repetition_audit import audit_repetition
from .report_coverage_audit import audit_coverage
from .report_position_audit import audit_position
from .report_macro_audit import audit_macro
from .report_safety_audit import audit_safety
from .report_level_audit import audit_report_levels
from .report_scenario_audit import audit_report_scenarios
from .report_registry_snapshot import validate_registry_snapshot
from .report_identity import report_request_identity
from .versions import AI_REPORT_AUDIT_VERSION,AI_REPORT_AUDIT_POLICY_VERSION,AI_REPORT_RESPONSE_VERSION

REQUIRED_INPUTS=("report_id","report_hash","report","generated_text","request_id","request","context_id","context_hash","context",
                 "registry_snapshot_id","fact_registry_hash","numeric_registry_hash","registry_source_versions_hash","prompt_hash","registry_snapshot",
                 "fact_registry","numeric_registry","position_context","macro_evidence_set","provider_metadata","prompt_version","source_versions")

def _now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def _error(bundle:dict[str,Any],code:str,created_at:str|None=None)->dict[str,Any]:
    versions=dict(AUDIT_SOURCE_VERSIONS);rid=str(bundle.get("report_id","missing"));rh=str(bundle.get("report_hash","missing"));cid=str(bundle.get("context_id","missing"));ch=str(bundle.get("context_hash","missing"))
    value={"audit_schema_version":AI_REPORT_AUDIT_VERSION,"audit_id":audit_identity(rid,rh,cid,ch,versions,registry_snapshot_id=bundle.get("registry_snapshot_id"),fact_registry_hash=bundle.get("fact_registry_hash"),numeric_registry_hash=bundle.get("numeric_registry_hash"),registry_source_versions_hash=bundle.get("registry_source_versions_hash"),prompt_hash=bundle.get("prompt_hash")),"report_id":rid,"request_id":bundle.get("request_id"),
      "context_id":cid,"report_hash":rh,"context_hash":ch,"audit_policy_version":AI_REPORT_AUDIT_POLICY_VERSION,"source_versions":versions,
      "status":"ERROR","hard_failures":[code],"warnings":[],"claim_count":0,"factual_claim_count":0,"claim_audits":[],"numeric_audits":[],
      "registry_snapshot_id":bundle.get("registry_snapshot_id"),"fact_registry_hash":bundle.get("fact_registry_hash"),"numeric_registry_hash":bundle.get("numeric_registry_hash"),"prompt_hash":bundle.get("prompt_hash"),
      "reference_audits":[],"semantic_audits":[],"contradiction_audits":[],"repetition_audit":{},"coverage_audit":{},"level_audit":{},"scenario_audit":{},"position_audit":{},
      "macro_audit":{},"safety_audit":{},"scorecard":{"overall":0.0},"promotion_eligible":False,"created_at":created_at or _now(),
      "provenance":{"network":False,"provider_called":False,"current_market_read":False,"frozen_input_only":True}}
    value["payload_hash"]=deterministic_payload_hash(value);return value

def audit_report(bundle:dict[str,Any],*,created_at:str|None=None)->dict[str,Any]:
    if not bundle.get("registry_snapshot_id") or not bundle.get("registry_snapshot"):
        return _error(bundle,"REGISTRY_SNAPSHOT_NOT_FOUND",created_at)
    missing=[x for x in REQUIRED_INPUTS if x not in bundle or bundle[x] is None]
    if missing:return _error(bundle,"AUDIT_INPUT_INCOMPLETE",created_at)
    report=bundle["report"];context=bundle["context"];hard=[]
    if stable_hash(report)!=bundle["report_hash"]:hard.append("REPORT_HASH_MISMATCH")
    if stable_hash(context)!=bundle["context_hash"]:hard.append("CONTEXT_HASH_MISMATCH")
    if report.get("source_versions")!=bundle["source_versions"]:hard.append("SOURCE_VERSION_MISMATCH")
    if report.get("context_id")!=bundle["context_id"] or bundle["request"].get("context_id")!=bundle["context_id"]:hard.append("AUDIT_INPUT_INCOMPLETE")
    snapshot=bundle["registry_snapshot"];hard.extend(validate_registry_snapshot(snapshot))
    if stable_hash(bundle["fact_registry"])!=bundle["fact_registry_hash"]:hard.append("FACT_REGISTRY_HASH_MISMATCH")
    if stable_hash(bundle["numeric_registry"])!=bundle["numeric_registry_hash"]:hard.append("NUMERIC_REGISTRY_HASH_MISMATCH")
    if bundle["fact_registry"].get("numeric_registry")!=bundle["numeric_registry"]:hard.append("NUMERIC_REGISTRY_DIVERGENCE")
    if bundle["fact_registry"].get("context_id")!=bundle["context_id"]:hard.append("REGISTRY_CONTEXT_MISMATCH")
    if bundle["request"].get("registry_snapshot_id")!=bundle["registry_snapshot_id"] or snapshot.get("registry_snapshot_id")!=bundle["registry_snapshot_id"]:hard.append("REGISTRY_IDENTITY_CONFLICT")
    request=bundle["request"]
    expected_request_id=report_request_identity(bundle["context_id"],request.get("mode"),request.get("language"),request.get("prompt_version"),request.get("provider"),request.get("model"),fact_registry_hash=bundle["fact_registry_hash"],numeric_registry_hash=bundle["numeric_registry_hash"],prompt_hash=bundle["prompt_hash"],registry_source_versions_hash=bundle["registry_source_versions_hash"])
    if expected_request_id!=bundle["request_id"] or snapshot.get("request_id")!=bundle["request_id"]:hard.append("REGISTRY_IDENTITY_CONFLICT")
    if snapshot.get("source_versions_hash")!=bundle["registry_source_versions_hash"] or snapshot.get("source_versions")!=bundle["source_versions"]:hard.append("REGISTRY_SOURCE_VERSION_MISMATCH")
    if snapshot.get("prompt_hash")!=bundle["prompt_hash"] or bundle.get("generation_prompt_hash")!=bundle["prompt_hash"]:hard.append("REGISTRY_PROMPT_HASH_MISMATCH")
    claims=extract_claims(bundle["report_id"],report);facts=bundle["fact_registry"]["facts"]
    numeric=audit_numeric_claims(claims,bundle["numeric_registry"]);reference=audit_references(claims,bundle["fact_registry"])
    semantic=audit_semantics(claims,facts);contradiction=audit_contradictions(claims,context,facts);repetition=audit_repetition(claims)
    coverage=audit_coverage(report,claims,context,facts);position=audit_position(claims,bundle["position_context"],report)
    level=audit_report_levels(report,bundle["fact_registry"]);scenario=audit_report_scenarios(report,bundle["fact_registry"])
    macro=audit_macro(claims,bundle["macro_evidence_set"],context["decision_time"]);safety=audit_safety(report,claims)
    for component in (numeric,reference,semantic,contradiction,coverage,level,scenario,position,macro,safety):hard.extend(component.get("failure_codes",[]))
    mode=report.get("mode","FULL")
    if repetition["repeated_claim_ratio"]>POLICY["repeated_claim_ratio"].get(mode,.18) or repetition["standalone_vague_sentence_count"]:hard.append("UNSUPPORTED_CLAIM")
    ratios={"numeric_grounding":numeric["numeric_grounding_ratio"],"reference_semantic_support":reference["reference_support_ratio"],
      "contradiction_freedom":1.0 if not contradiction["critical_contradiction_count"] else 0.0,
      "scenario_invalidation":scenario["invalidation_coverage"],
      "data_quality_disclosure":coverage["critical_warning_coverage"],
      "repetition_specificity":max(0.0,1-repetition["repeated_claim_ratio"]-repetition["vague_sentence_ratio"]),
      "position_macro_safety":1.0 if not (position["failure_codes"] or macro["failure_codes"] or safety["failure_codes"]) else 0.0}
    overall=sum(POLICY["weights"][k]*ratios[k] for k in POLICY["weights"]);hard=sorted(set(hard))
    status="PASSED" if not hard and overall>=POLICY["pass_score"] else "FAILED"
    if level["field_coverage"]<1:hard.append("LEVEL_PROJECTION_MISSING")
    if scenario["field_coverage"]<1:hard.append("SCENARIO_INCOMPLETE")
    if scenario["invalidation_coverage"]<1:hard.append("INVALIDATION_MISSING")
    hard=sorted(set(hard));status="PASSED" if not hard and overall>=POLICY["pass_score"] else "FAILED"
    versions=dict(AUDIT_SOURCE_VERSIONS);aid=audit_identity(bundle["report_id"],bundle["report_hash"],bundle["context_id"],bundle["context_hash"],versions,registry_snapshot_id=bundle["registry_snapshot_id"],fact_registry_hash=bundle["fact_registry_hash"],numeric_registry_hash=bundle["numeric_registry_hash"],registry_source_versions_hash=bundle["registry_source_versions_hash"],prompt_hash=bundle["prompt_hash"])
    value={"audit_schema_version":AI_REPORT_AUDIT_VERSION,"audit_id":aid,"report_id":bundle["report_id"],"request_id":bundle["request_id"],
      "context_id":bundle["context_id"],"report_hash":bundle["report_hash"],"context_hash":bundle["context_hash"],"registry_snapshot_id":bundle["registry_snapshot_id"],"fact_registry_hash":bundle["fact_registry_hash"],"numeric_registry_hash":bundle["numeric_registry_hash"],"prompt_hash":bundle["prompt_hash"],
      "audit_policy_version":AI_REPORT_AUDIT_POLICY_VERSION,"source_versions":versions,"status":status,"hard_failures":hard,"warnings":[],
      "claim_count":len(claims),"factual_claim_count":reference["factual_claim_count"],"claim_audits":claims,"numeric_audits":numeric["audits"],
      "reference_audits":reference["audits"],"semantic_audits":semantic["audits"],"contradiction_audits":contradiction["audits"],
      "repetition_audit":repetition,"coverage_audit":coverage,"level_audit":level,"scenario_audit":scenario,"position_audit":position,"macro_audit":macro,"safety_audit":safety,
      "frozen_replay_provenance":bundle.get("frozen_replay_provenance",{"source":"FROZEN_BUNDLE","external_market_databases_opened":False}),
      "scorecard":{"overall":round(overall,3),"ratios":{**ratios,"level_field_coverage":level["field_coverage"],"scenario_field_coverage":scenario["field_coverage"],"invalidation_coverage":scenario["invalidation_coverage"],"registry_identity_valid":not any(x.startswith("REGISTRY_") or x.endswith("REGISTRY_HASH_MISMATCH") for x in hard)},"weights":POLICY["weights"]},"promotion_eligible":status=="PASSED" and report.get("schema_version")==AI_REPORT_RESPONSE_VERSION,
      "created_at":created_at or _now(),"provenance":{"network":False,"provider_called":False,"current_market_read":False,"frozen_input_only":True,
      "input_hash":stable_hash({k:bundle[k] for k in REQUIRED_INPUTS})}}
    value["payload_hash"]=deterministic_payload_hash(value);return value
