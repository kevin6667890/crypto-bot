"""Complete numeric grounding, direction and unit checks."""
from __future__ import annotations
import re
from typing import Any
from .versions import AI_REPORT_NUMERIC_AUDIT_VERSION

DIRECTION_TERMS={"increase":("增加","上升","上涨","扩大","扩张","恢复","正"),"decrease":("减少","下降","下跌","收缩","负")}
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
                unit_ok=unit in units or unit is None or None in units or (unit=="ratio" and "percent" in units and abs(canonical*100-value)<=max(tolerance,0.01))
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
                if canonical*value<0:code="NUMERIC_DIRECTION_MISMATCH"
                elif claim.get("claim_type") not in {"KEY_LEVEL", "SCENARIO"} and canonical<0 and any(w in text for w in DIRECTION_TERMS["increase"]) and not any(w in text for w in ("恢复","回升")):code="NUMERIC_DIRECTION_MISMATCH"
                elif claim.get("claim_type") not in {"KEY_LEVEL", "SCENARIO"} and canonical>0 and any(w in text for w in DIRECTION_TERMS["decrease"]) and not ("回撤" in text):code="NUMERIC_DIRECTION_MISMATCH"
                elif item.get("unit") and q.get("unit") and item["unit"]!=q["unit"] and not ({item["unit"],q["unit"]}<={"ratio","percent"}):code="NUMERIC_UNIT_MISMATCH"
            result={"version":AI_REPORT_NUMERIC_AUDIT_VERSION,"claim_id":claim["claim_id"],"original":q["original"],"normalized_value":value,
              "unit":q.get("unit"),"report_location":{"section_id":claim["section_id"],"sentence_index":claim["sentence_index"]},
              "result":"FAILED" if code else "MATCHED","source_fact_id":None,"canonical_value":None,"tolerance":None,"rounding":None}
            if candidates:
                item=sorted(candidates,key=lambda x:(abs(float(x["canonical_value"])-value),x["source_fact_id"]))[0]
                result.update(source_fact_id=item["source_fact_id"],canonical_value=item["canonical_value"],tolerance=item.get("absolute_tolerance"),rounding=item.get("allowed_decimal_places"))
            if code:result["code"]=code;failures.append(code)
            else:matched+=1
            audits.append(result)
    return {"version":AI_REPORT_NUMERIC_AUDIT_VERSION,"audits":audits,"total_numeric_claims":total,"matched_numeric_claims":matched,
            "numeric_grounding_ratio":1.0 if total==0 else matched/total,"failure_codes":sorted(set(failures))}
