"""Factual-only frozen replay; never evaluates later price outcomes."""
from __future__ import annotations
from typing import Any
from .canonical import identity,stable_hash
from .report_audit_service import audit_report
from .versions import AI_REPORT_REPLAY_VERSION
from .report_audit_identity import AUDIT_SOURCE_VERSIONS

def replay(bundle:dict[str,Any],runs:int=20)->dict[str,Any]:
    if runs<1 or runs>100:raise ValueError("runs must be bounded between 1 and 100")
    audits=[audit_report(bundle,created_at="1970-01-01T00:00:00Z") for _ in range(runs)]
    proof={"audit_ids":len({x["audit_id"] for x in audits}),"statuses":len({x["status"] for x in audits}),
      "failure_code_sets":len({tuple(x["hard_failures"]) for x in audits}),"scores":len({x["scorecard"]["overall"] for x in audits}),
      "claim_id_sets":len({tuple(c["claim_id"] for c in x["claim_audits"]) for x in audits}),"payload_hashes":len({x["payload_hash"] for x in audits})}
    stable=all(v==1 for v in proof.values())
    manifest={"version":AI_REPORT_REPLAY_VERSION,"source_versions":dict(AUDIT_SOURCE_VERSIONS),"kind":"FACTUAL_AUDIT","forecast_outcome_evaluation":False,"runs":runs,
      "report_id":bundle["report_id"],"context_id":bundle["context_id"],"frozen_input_hash":stable_hash(bundle)}
    return {"replay_id":identity("replay",manifest),"manifest":manifest,"deterministic":stable,"proof":proof,
            "audit_id":audits[0]["audit_id"],"payload_hash":audits[0]["payload_hash"]}
