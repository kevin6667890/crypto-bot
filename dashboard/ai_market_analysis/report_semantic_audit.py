"""Context-to-claim semantic and modality protection."""
from __future__ import annotations
from typing import Any
from .report_semantic_registry import REFERENCE_COMPATIBILITY, SEMANTIC_REGISTRY
from .report_numeric_normalizer import normalize_numbers
from .versions import AI_REPORT_SEMANTIC_AUDIT_VERSION

UNKNOWN_VALUES={"UNKNOWN","NOT_AVAILABLE","NOT_IMPLEMENTED","INSUFFICIENT_EVIDENCE","PARTIAL","PARTIAL_AFTER_GAP","GAP_AFFECTED","UNAVAILABLE"}
PARTIAL_VALUES={"PARTIAL","PARTIAL_AFTER_GAP","GAP_AFFECTED"}
FLOW_ASSERTION_TERMS=("订单流显示","订单流数据显示","订单流证据显示","净流入","净流出","资金正在","买盘主导","卖盘主导")
PARTIAL_MARKERS=("部分观察","部分可用","部分数据","仅覆盖","无法判断","证据不足","数据不足","覆盖部分","确认度受限")

def _has_liquidation_evidence(value:Any)->bool:
    if not isinstance(value,dict):return False
    return any("liquidation" in str(key).lower() and nested not in (None,[],{},"UNAVAILABLE","UNKNOWN")
               for key,nested in value.items())

def audit_semantics(claims:list[dict[str,Any]],facts:list[dict[str,Any]])->dict[str,Any]:
    lookup={f["fact_id"]:f for f in facts};audits=[];failures=[]
    for claim in claims:
        text=claim["original_text"];all_refs=[lookup[r] for r in claim.get("fact_refs",[]) if r in lookup];refs=list(all_refs);codes=[]
        expected_categories=REFERENCE_COMPATIBILITY.get(claim.get("claim_type"),set())
        if expected_categories:refs=[fact for fact in refs if fact.get("category") in expected_categories]
        values=[f.get("value") for f in refs]
        flat=" ".join(str(v) for v in values)
        def unavailable(fact):
            value=fact.get("value")
            if str(fact.get("quality")) in UNKNOWN_VALUES:return True
            if isinstance(value,str):return value in UNKNOWN_VALUES
            if isinstance(value,dict):return any(str(value.get(k)) in UNKNOWN_VALUES for k in ("status","quality","cvd_status","oi_status"))
            return False
        if (claim.get("modality") not in {"UNKNOWN","NOT_AVAILABLE","UNCERTAIN","CONDITIONAL"}
            and any(unavailable(f) for f in refs)
            and any(x in text for x in SEMANTIC_REGISTRY["UNKNOWN"]["forbidden_certainty"])):
            codes.append("UNKNOWN_PROMOTED_TO_FACT")
        partial_flow_refs=[fact for fact in refs if fact.get("category")=="ORDER_FLOW" and (
            str(fact.get("quality") or "").upper() in PARTIAL_VALUES
            or (isinstance(fact.get("value"),dict) and str(fact["value"].get("quality") or "").upper() in PARTIAL_VALUES)
        )]
        if (partial_flow_refs and any(term in text for term in FLOW_ASSERTION_TERMS)
                and (claim.get("modality") not in {"UNKNOWN","NOT_AVAILABLE","UNCERTAIN","CONDITIONAL"}
                     or not any(marker in text for marker in PARTIAL_MARKERS))):
            codes.append("UNKNOWN_PROMOTED_TO_FACT")
        if ("LIKELY" in flat or "likely" in flat.lower()) and any(x in text for x in SEMANTIC_REGISTRY["LIKELY"]["forbidden_certainty"]):codes.append("LIKELY_PROMOTED_TO_CONFIRMED")
        for value in ("POST_BREAKOUT_PULLBACK","SHORT_COVERING_DOMINANT","STRONG_BEAR"):
            if value in flat and any(x in text for x in SEMANTIC_REGISTRY[value]["forbidden"]):
                codes.append("ORDER_FLOW_CONTRADICTION" if value=="SHORT_COVERING_DOMINANT" else "CRITICAL_CONTRADICTION")
        if any(f["category"]=="ORDER_FLOW" and isinstance(f["value"],dict) and f["value"].get("cvd_status") in {"GAP_AFFECTED","PARTIAL"} for f in refs) and "完整确认" in text:codes.append("ORDER_FLOW_CONTRADICTION")
        if any(x in text for x in ("已结算","结算资金费率")) and "predicted" in flat.lower():codes.append("UNSUPPORTED_CLAIM")
        if "没有发生强平" in text and not any(_has_liquidation_evidence(v) for v in values):codes.append("UNSUPPORTED_CLAIM")
        if any(x in text for x in ("现货买盘已确认","现货资金进场已确认")):codes.append("LIKELY_PROMOTED_TO_CONFIRMED")
        if any(x in text for x in ("唯一机制","唯一推动","纯空头回补")):codes.append("ORDER_FLOW_CONTRADICTION")
        if any(x in text for x in ("新增多头主导","纯新增多头主导")):codes.append("ORDER_FLOW_CONTRADICTION")
        if "数据不足" in text and any(x in text for x in ("HIGH confidence","高置信","明确结论")):codes.append("UNKNOWN_PROMOTED_TO_FACT")
        if "已结算" in text and any(x.lower() in text.lower() for x in ("funding","资金费率")):codes.append("UNSUPPORTED_CLAIM")
        if any(term in text for term in ("资金净流入","资金净流出","净流入","净流出")):
            flow_refs=[fact for fact in all_refs if fact.get("category")=="ORDER_FLOW" and not unavailable(fact)]
            cvd_values=[float(fact["value"]["cvd_delta"]) for fact in flow_refs
                        if isinstance(fact.get("value"),dict) and fact["value"].get("cvd_delta") is not None]
            mentioned=[float(item["value"]) for item in normalize_numbers(text) if item.get("parsed",True)]
            numeric_supported=not mentioned or any(any(abs(value-cvd)<=1e-12 for cvd in cvd_values) for value in mentioned)
            if not cvd_values or not numeric_supported:
                codes.append("ORDER_FLOW_SEMANTIC_NAMESPACE_MISMATCH")
        if "订单流" in text and any(term in text for term in ("价格变化","价格变动","净正价格","净负价格")):
            codes.append("ORDER_FLOW_SEMANTIC_NAMESPACE_MISMATCH")
        forced_exit_terms=("\u7206\u4ed3","\u5f3a\u5e73","liquidation")
        causal_certainty=("\u786e\u5b9a","\u786e\u8ba4","\u8bc1\u660e","\u5c31\u662f","\u5b8c\u5168\u7531","definitely","confirmed")
        if (any(term.casefold() in text.casefold() for term in forced_exit_terms)
                and any(term.casefold() in text.casefold() for term in causal_certainty)
                and not any(_has_liquidation_evidence(v) for v in values)):
            codes.append("UNSUPPORTED_CAUSALITY")
        failures.extend(codes);audits.append({"version":AI_REPORT_SEMANTIC_AUDIT_VERSION,"claim_id":claim["claim_id"],"result":"FAILED" if codes else "SUPPORTED","codes":sorted(set(codes))})
    return {"version":AI_REPORT_SEMANTIC_AUDIT_VERSION,"audits":audits,"failure_codes":sorted(set(failures))}
