"""Content-derived audit identities; no wall clock, machine or database state."""
from __future__ import annotations
from .canonical import identity, stable_hash
from .versions import *

AUDIT_SOURCE_VERSIONS = {
 "audit":AI_REPORT_AUDIT_VERSION,"policy":AI_REPORT_AUDIT_POLICY_VERSION,"claim_extractor":AI_REPORT_CLAIM_EXTRACTOR_VERSION,
 "semantic_registry":AI_REPORT_SEMANTIC_REGISTRY_VERSION,"numeric_normalizer":AI_REPORT_NUMERIC_NORMALIZER_VERSION,
 "numeric_audit":AI_REPORT_NUMERIC_AUDIT_VERSION,"reference_audit":AI_REPORT_REFERENCE_AUDIT_VERSION,
 "semantic_audit":AI_REPORT_SEMANTIC_AUDIT_VERSION,"contradiction_audit":AI_REPORT_CONTRADICTION_AUDIT_VERSION,
 "repetition_audit":AI_REPORT_REPETITION_AUDIT_VERSION,"coverage_audit":AI_REPORT_COVERAGE_AUDIT_VERSION,
 "position_audit":AI_REPORT_POSITION_AUDIT_VERSION,"macro_audit":AI_REPORT_MACRO_AUDIT_VERSION,
 "safety_audit":AI_REPORT_SAFETY_AUDIT_VERSION,"evaluation":AI_REPORT_EVALUATION_VERSION,
 "replay":AI_REPORT_REPLAY_VERSION,"database":AI_REPORT_AUDIT_DB_VERSION,
}

def audit_identity(report_id:str, report_hash:str, context_id:str, context_hash:str, versions:dict|None=None)->str:
    return identity("audit",{"report_id":report_id,"report_hash":report_hash,"context_id":context_id,
                             "context_hash":context_hash,"versions":versions or AUDIT_SOURCE_VERSIONS})

def deterministic_payload_hash(payload:dict)->str:
    return stable_hash({k:v for k,v in payload.items() if k not in {"created_at","duration_ms"}})
