"""Typed semantic duplicate audit with text similarity as a UX signal."""
from __future__ import annotations
import re
from collections import defaultdict
from typing import Any
from .versions import AI_REPORT_REPETITION_AUDIT_VERSION

VAGUE=("多空博弈激烈","当前需要关注支撑压力","后续走势存在不确定性","市场情绪偏谨慎","建议控制风险","指标偏多",
       "成交量有所变化","OI出现变化","多头力量增强","空头力量减弱","上涨也可能回调","价格可能涨也可能跌","偏多但注意风险")

_HIERARCHICAL_ROLES={"SUMMARY","SYNTHESIS"}

def _normal(text:str)->str:
    text=re.sub(r"\d+(?:\.\d+)?","#",text);text=re.sub(r"[\W_]+","",text,flags=re.UNICODE)
    return re.sub(r"(?:当前|目前|需要|后续|走势|市场)","",text)

def _grams(text:str)->set[str]:
    value=_normal(text);return {value[i:i+3] for i in range(max(0,len(value)-2))}

def _ratio(left:set[str],right:set[str])->float:
    return len(left&right)/len(left|right) if left|right else 1.0

def _pair(prior:dict[str,Any],claim:dict[str,Any],similarity:float,exact:bool)->dict[str,Any]:
    left=set(prior.get("source_fact_ids") or prior.get("fact_refs",[]));right=set(claim.get("source_fact_ids") or claim.get("fact_refs",[]))
    evidence_overlap=_ratio(left,right)
    same_scope=prior.get("scope")==claim.get("scope")
    same_role=prior.get("role")==claim.get("role")
    same_key=prior.get("semantic_key")==claim.get("semantic_key")
    roles={prior.get("role"),claim.get("role")};scopes={prior.get("scope"),claim.get("scope")}
    verbatim=prior.get("normalized_text")==claim.get("normalized_text")
    violation=False;duplicate_type="UX_NEAR_DUPLICATE_SIGNAL"
    if prior.get("claim_type")==claim.get("claim_type")=="LIMITATION" and similarity>=.82:
        violation=True;duplicate_type="LIMITATION_REPETITION"
    elif same_key and same_scope and same_role and evidence_overlap>=.95:
        violation=True;duplicate_type="SAME_CLAIM_SAME_SCOPE"
    elif same_scope and same_role and evidence_overlap>=.80 and similarity>=.82:
        violation=True;duplicate_type="SEMANTIC_DUPLICATE_WITHOUT_NEW_EVIDENCE"
    elif exact and roles.intersection(_HIERARCHICAL_ROLES) and "DETAIL" in roles and evidence_overlap>=.95:
        violation=True;duplicate_type="SUMMARY_DETAIL_WITHOUT_NEW_EVIDENCE"
    elif roles.intersection(_HIERARCHICAL_ROLES) and "DETAIL" in roles:
        duplicate_type="ALLOWED_STRUCTURED_REPETITION"
    elif exact and verbatim and len(scopes)>1 and scopes <= {"15M","1H","4H","1D","1W"}:
        duplicate_type="CROSS_TIMEFRAME_COPY_PASTE";violation=True
    elif len(scopes)>1 and scopes <= {"15M","1H","4H","1D","1W"}:
        duplicate_type="CROSS_TIMEFRAME_FALSE_POSITIVE"
    elif exact:
        duplicate_type="EXACT_DUPLICATE";violation=same_scope and evidence_overlap>=.80
    return {
        "claim_ids":[prior["claim_id"],claim["claim_id"]],"sections":[prior["section_id"],claim["section_id"]],
        "claim_types":[prior.get("claim_type"),claim.get("claim_type")],"scopes":[prior.get("scope"),claim.get("scope")],
        "roles":[prior.get("role"),claim.get("role")],"semantic_keys":[prior.get("semantic_key"),claim.get("semantic_key")],
        "similarity":round(similarity,4),"same_scope":same_scope,"same_role":same_role,
        "verbatim":verbatim,
        "evidence_overlap_ratio":round(evidence_overlap,4),"duplicate_type":duplicate_type,"violation":violation,
        "failure_reason":duplicate_type if violation else None,
    }

def audit_repetition(claims:list[dict[str,Any]])->dict[str,Any]:
    exact=[];near=[];violations=[];allowed=[];matrix=defaultdict(lambda:defaultdict(int));seen={}
    for i,claim in enumerate(claims):
        n=_normal(claim["normalized_text"])
        if n in seen:
            prior=claims[seen[n]];item=_pair(prior,claim,1.0,True);exact.append(item)
            (violations if item["violation"] else allowed).append(item)
        else:seen[n]=i
        for prior in claims[:i]:
            a,b=_grams(prior["normalized_text"]),_grams(claim["normalized_text"]);score=_ratio(a,b)
            if score>=.82 and _normal(prior["normalized_text"])!=n:
                item=_pair(prior,claim,score,False);near.append(item)
                (violations if item["violation"] else allowed).append(item)
            if score>=.5:matrix[prior["section_id"]][claim["section_id"]]+=1
    violation_ids={claim_id for item in violations for claim_id in item["claim_ids"]}
    vague=[]
    for claim in claims:
        matches=[value for value in VAGUE if value in claim["original_text"]]
        if matches and not claim.get("quantities") and not claim.get("fact_refs"):
            vague.append({"claim_id":claim["claim_id"],"matches":matches})
    count=len(claims)
    return {"version":AI_REPORT_REPETITION_AUDIT_VERSION,
      "exact_duplicate_count":sum(1 for item in exact if item["violation"]),
      "exact_duplicate_pairs":[item for item in exact if item["violation"]],
      "near_duplicate_pairs":near,"duplicate_pair_count":len(violations),"duplicate_pairs":violations,
      "allowed_structured_repetitions":allowed,"typed_false_positive_count":len(allowed),
      "repeated_claim_ratio":len(violation_ids)/count if count else 0.0,
      "section_repetition_matrix":{a:dict(b) for a,b in matrix.items()},"vague_sentence_count":len(vague),
      "vague_sentence_ratio":len(vague)/count if count else 0.0,"boilerplate_matches":vague,
      "standalone_vague_sentence_count":len(vague)}
