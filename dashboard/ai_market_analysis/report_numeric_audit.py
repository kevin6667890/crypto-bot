"""Complete numeric grounding, direction and unit checks."""
from __future__ import annotations
import re
from typing import Any
from .versions import AI_REPORT_NUMERIC_AUDIT_VERSION

DIRECTION_TERMS={"increase":("增加","上升","上涨","扩大","扩张","恢复","流入","正"),
                 "decrease":("减少","下降","下跌","收缩","减弱","流出","负","降至")}
UNIT_WORDS={"percent":("%","百分之"),"percentage_point":("百分点",),"USDT":("USDT","USD","美元","价格","支撑","压力","成本","止损","目标"),
 "contracts":("张","合约"),"coin":("枚",),"multiple":("倍",),"R":("R",),"ATR":("ATR",)}

def _candidate_units(quantity:dict,text:str)->set[str|None]:
    if quantity.get("unit"):return {quantity["unit"]}
    def present(word: str) -> bool:
        if word.isascii() and word.isalnum():
            return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text, re.I))
        return word in text
    found={unit for unit,words in UNIT_WORDS.items() if any(present(word) for word in words)}
    return found or {None}


def _local_clause(text:str,quantity:dict)->tuple[str,int,int]:
    """Return the clause containing this numeric mention, not the entire sentence."""
    start=int(quantity.get("start",text.find(str(quantity.get("original", "")).strip())))
    start=max(0,start);end=int(quantity.get("end",start+len(str(quantity.get("original", "")))))
    boundaries=[m.span() for m in re.finditer(r"[。；;]|，(?:但|且|并且|同时|而)",text)]
    left=max((b for a,b in boundaries if b<=start),default=0)
    right=min((a for a,b in boundaries if a>=end),default=len(text))
    return text[left:right].strip("，。；; "),left,right


def _directions(text:str)->set[str]:
    work=text
    for phrase in ("未下降","没有下降","并未下降","未增加","没有增加","并未增加","不高于","不低于"):
        work=work.replace(phrase,"")
    return {direction for direction,terms in DIRECTION_TERMS.items() if any(term in work for term in terms)}


def _comparative_result(claim:dict,quantity:dict)->bool|None:
    """Validate explicit current/previous comparisons inside the local clause."""
    clause,left,right=_local_clause(claim["original_text"],quantity)
    quantities=[q for q in claim.get("quantities",[]) if left<=int(q.get("start",-1)) and int(q.get("end",-1))<=right]
    if len(quantities)<2:return None
    values=[float(q["value"]) for q in quantities]
    directions=_directions(clause)
    if len(directions)!=1:return None
    direction=next(iter(directions))
    if "较" in clause:
        current,previous=values[0],values[1]
    elif "由" in clause and any(term in clause for term in ("至","到","降至","升至")):
        previous,current=values[0],values[1]
    else:
        return None
    return current>previous if direction=="increase" else current<previous


def _semantic_role(item:dict,claim:dict,quantity:dict)->str:
    role=item.get("semantic_role")
    if role:return str(role)
    if str(item.get("source_fact_id","")).startswith(("STRUCT_","LEVEL_")) or item.get("unit")=="USDT":
        return "THRESHOLD_RELATION"
    if _comparative_result(claim,quantity) is not None:return "COMPARATIVE_PAIR"
    return "SIGNED_DELTA" if abs(float(item["canonical_value"]))<1 else "ABSOLUTE_VALUE"

def audit_numeric_claims(claims:list[dict[str,Any]],registry:list[dict[str,Any]])->dict[str,Any]:
    audits=[];failures=[];total=matched=0
    for claim in claims:
        for q in claim.get("quantities",[]):
            total+=1
            if not q.get("parsed",True):
                code="UNPARSED_NUMERIC_CLAIM";failures.append(code);audits.append({"claim_id":claim["claim_id"],"result":"FAILED","code":code,"original":q["original"]});continue
            units=_candidate_units(q,claim["original_text"]);value=float(q["value"])
            candidates=[]
            for item in registry:
                canonical=float(item["canonical_value"]);tolerance=float(item.get("absolute_tolerance",0))
                unit=item.get("unit")
                typed_same_value=any(
                    other.get("unit") is not None
                    and abs(abs(float(other["canonical_value"]))-abs(value))<=float(other.get("absolute_tolerance",0))
                    for other in registry
                )
                unit_ok=(unit in units or None in units or (unit is None and not typed_same_value)
                         or (unit=="ratio" and "percent" in units and abs(canonical*100-value)<=max(tolerance,0.01)))
                value_ok=abs(canonical-value)<=tolerance or abs(round(canonical)-value)<=tolerance
                direction_only=abs(abs(canonical)-abs(value))<=tolerance
                if q.get("approximate"):value_ok=value_ok or abs(canonical-value)<=max(tolerance,abs(canonical)*.02)
                if unit_ok and (value_ok or direction_only):candidates.append(item)
            code=None
            if not candidates:
                same_value=[item for item in registry if abs(abs(float(item["canonical_value"]))-abs(value))<=float(item.get("absolute_tolerance",0))]
                code="NUMERIC_UNIT_MISMATCH" if same_value and q.get("unit") and all(item.get("unit") not in units for item in same_value) else "NUMERIC_HALLUCINATION"
            else:
                item=sorted(candidates,key=lambda x:(abs(float(x["canonical_value"])-value),x["source_fact_id"]))[0]
                canonical=float(item["canonical_value"]);text=claim["original_text"]
                role=_semantic_role(item,claim,q);clause,_,_=_local_clause(text,q);directions=_directions(clause)
                comparison=_comparative_result(claim,q)
                if canonical*value<0:code="NUMERIC_DIRECTION_MISMATCH"
                elif role in {"COMPARATIVE_PAIR","ABSOLUTE_VALUE"} and comparison is False:code="NUMERIC_DIRECTION_MISMATCH"
                elif role=="SIGNED_DELTA" and canonical<0 and directions=={"increase"}:code="NUMERIC_DIRECTION_MISMATCH"
                elif role=="SIGNED_DELTA" and canonical>0 and directions=={"decrease"}:code="NUMERIC_DIRECTION_MISMATCH"
                elif item.get("unit") and q.get("unit") and item["unit"]!=q["unit"] and not ({item["unit"],q["unit"]}<={"ratio","percent"}):code="NUMERIC_UNIT_MISMATCH"
            result={"version":AI_REPORT_NUMERIC_AUDIT_VERSION,"claim_id":claim["claim_id"],"original":q["original"],"normalized_value":value,
              "unit":q.get("unit"),"report_location":{"section_id":claim["section_id"],"sentence_index":claim["sentence_index"]},
              "result":"FAILED" if code else "MATCHED","source_fact_id":None,"canonical_value":None,"tolerance":None,"rounding":None}
            if candidates:
                item=sorted(candidates,key=lambda x:(abs(float(x["canonical_value"])-value),x["source_fact_id"]))[0]
                result.update(source_fact_id=item["source_fact_id"],canonical_value=item["canonical_value"],tolerance=item.get("absolute_tolerance"),rounding=item.get("allowed_decimal_places"),
                              semantic_role=_semantic_role(item,claim,q),semantic_namespace=item.get("semantic_namespace"),semantic_field=item.get("semantic_field"))
            if code:result["code"]=code;failures.append(code)
            else:matched+=1
            audits.append(result)
    return {"version":AI_REPORT_NUMERIC_AUDIT_VERSION,"audits":audits,"total_numeric_claims":total,"matched_numeric_claims":matched,
            "numeric_grounding_ratio":1.0 if total==0 else matched/total,"failure_codes":sorted(set(failures))}
