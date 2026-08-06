"""Local-only exact, n-gram and generic-language analysis."""
from __future__ import annotations
import re
from collections import defaultdict
from typing import Any
from .versions import AI_REPORT_REPETITION_AUDIT_VERSION

VAGUE=("多空博弈激烈","当前需要关注支撑压力","后续走势存在不确定性","市场情绪偏谨慎","建议控制风险","指标偏多",
       "成交量有所变化","OI出现变化","多头力量增强","空头力量减弱","上涨也可能回调","价格可能涨也可能跌","偏多但注意风险")

def _normal(text:str)->str:
    text=re.sub(r"\d+(?:\.\d+)?","#",text);text=re.sub(r"[\W_]+","",text,flags=re.UNICODE)
    return re.sub(r"(?:当前|目前|需要|后续|走势|市场)","",text)

def _grams(text:str)->set[str]:
    value=_normal(text);return {value[i:i+3] for i in range(max(0,len(value)-2))}

def audit_repetition(claims:list[dict[str,Any]])->dict[str,Any]:
    exact=[];near=[];matrix=defaultdict(lambda:defaultdict(int));seen={}
    for i,claim in enumerate(claims):
        n=_normal(claim["normalized_text"])
        if n in seen:exact.append([claims[seen[n]]["claim_id"],claim["claim_id"]])
        else:seen[n]=i
        for prior in claims[:i]:
            a,b=_grams(prior["normalized_text"]),_grams(claim["normalized_text"]);score=len(a&b)/len(a|b) if a|b else 0
            if score>=.82 and [prior["claim_id"],claim["claim_id"]] not in exact:near.append({"claim_ids":[prior["claim_id"],claim["claim_id"]],"jaccard":round(score,4)})
            if score>=.5:matrix[prior["section_id"]][claim["section_id"]]+=1
    repeated={x for pair in exact for x in pair}|{x for p in near for x in p["claim_ids"]}
    vague=[]
    for c in claims:
        matches=[v for v in VAGUE if v in c["original_text"]]
        if matches and not c.get("quantities") and not c.get("fact_refs"):vague.append({"claim_id":c["claim_id"],"matches":matches})
    count=len(claims)
    return {"version":AI_REPORT_REPETITION_AUDIT_VERSION,"exact_duplicate_count":len(exact),"exact_duplicate_pairs":exact,
      "near_duplicate_pairs":near,"repeated_claim_ratio":len(repeated)/count if count else 0.0,
      "section_repetition_matrix":{a:dict(b) for a,b in matrix.items()},"vague_sentence_count":len(vague),
      "vague_sentence_ratio":len(vague)/count if count else 0.0,"boilerplate_matches":vague,"standalone_vague_sentence_count":len(vague)}
