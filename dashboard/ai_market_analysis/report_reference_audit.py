"""Reference existence, category, instrument and timeframe support."""
from __future__ import annotations
from typing import Any
from .report_semantic_registry import REFERENCE_COMPATIBILITY
from .versions import AI_REPORT_REFERENCE_AUDIT_VERSION

NON_FACTUAL={"UNCERTAINTY","LIMITATION","SAFETY"}
TIMEFRAME_ALIASES={
    "15m":"15m","1h":"1H","4h":"4H","1d":"1D","1w":"1W",
    "15分钟":"15m","1小时":"1H","4小时":"4H","日线":"1D","周线":"1W",
}

def _canonical_timeframe(value:str|None)->str|None:
    if value is None:return None
    return TIMEFRAME_ALIASES.get(value.lower(),value)

def _timeframe_for_fact(fact_id:str)->str|None:
    return next((tf for prefix,tf in (("TF15_","15m"),("TF1H_","1H"),("TF4H_","4H"),("TF1D_","1D"),("TF1W_","1W")) if fact_id.startswith(prefix)),None)

def audit_references(claims:list[dict[str,Any]],registry:dict[str,Any])->dict[str,Any]:
    facts={f["fact_id"]:f for f in registry["facts"]};audits=[];failures=[];factual=supported=0
    known_levels={f["value"].get("level_id") for f in facts.values() if f["category"]=="LEVEL" and isinstance(f["value"],dict)}
    known_scenarios={f["value"].get("scenario_id") for f in facts.values() if f["category"]=="SCENARIO" and isinstance(f["value"],dict)}
    known_macro={f["value"].get("evidence_id") for f in facts.values() if f["category"]=="MACRO" and isinstance(f["value"],dict)}
    for claim in claims:
        refs=claim.get("fact_refs",[]);missing=sorted(set(refs)-set(facts));categories={facts[r]["category"] for r in refs if r in facts}
        # A neutral absence statement is a warning fact, but it is also the
        # only valid support for the scoped claim that macro was not included.
        if "MACRO_UNAVAILABLE" in refs:
            categories.add("MACRO")
        expected=REFERENCE_COMPATIBILITY.get(claim["claim_type"],set());code=None;reason=None
        is_factual=claim["claim_type"] not in NON_FACTUAL and claim["modality"] not in {"UNKNOWN","NOT_AVAILABLE","CONDITIONAL"}
        if is_factual:factual+=1
        typed_missing=sorted((set(claim.get("level_refs",[]))-known_levels)|(set(claim.get("scenario_refs",[]))-known_scenarios)|(set(claim.get("macro_refs",[]))-known_macro)|(set(claim.get("position_refs",[]))-set(facts)))
        if missing or typed_missing:code="UNKNOWN_REFERENCE";reason=f"unknown refs: {missing+typed_missing}"
        elif is_factual and not refs:code="UNSUPPORTED_CLAIM";reason="factual claim has no reference"
        elif is_factual and expected and not categories.intersection(expected):code="REFERENCE_NOT_SUPPORTING_CLAIM";reason=f"expected {sorted(expected)}, got {sorted(categories)}"
        else:
            mentioned={_canonical_timeframe(value) for value in claim.get("timeframe_mentions",[])}-{None}
            mapped={_canonical_timeframe(_timeframe_for_fact(ref)) for ref in refs}-{None}
            if mentioned and mapped and not mentioned.intersection(mapped):
                code="TIMEFRAME_MISMATCH";reason=f"claim {sorted(mentioned)} refs {sorted(mapped)}"
            elif claim.get("instrument_mentions") and any(registry["instrument"].split("-")[0] not in x for x in claim["instrument_mentions"]):
                code="INSTRUMENT_MISMATCH";reason="claim instrument differs from frozen context"
        if not code and is_factual:supported+=1
        if code:failures.append(code)
        audits.append({"version":AI_REPORT_REFERENCE_AUDIT_VERSION,"claim_id":claim["claim_id"],"supported":not bool(code),
          "code":code,"reason":reason,"supplied_refs":refs,"expected_ref_categories":sorted(expected),"actual_categories":sorted(categories)})
    unsupported=[{"code":a["code"],"claim_id":a["claim_id"],"reason":a["reason"],"supplied_refs":a["supplied_refs"],
                  "expected_ref_categories":a["expected_ref_categories"],"severity":"CRITICAL","suggested_remediation":"remove claim or cite a compatible frozen fact"}
                 for a in audits if a["code"]]
    return {"version":AI_REPORT_REFERENCE_AUDIT_VERSION,"audits":audits,"factual_claim_count":factual,"supported_claim_count":supported,
      "reference_support_ratio":1.0 if factual==0 else supported/factual,"unsupported_claims":unsupported,"failure_codes":sorted(set(failures))}
