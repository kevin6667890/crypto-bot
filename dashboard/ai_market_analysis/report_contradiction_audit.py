"""Deterministic context and intra-report contradiction rules."""
from __future__ import annotations
from typing import Any
from .canonical import identity
from .versions import AI_REPORT_CONTRADICTION_AUDIT_VERSION

OPPOSITES=(("突破已经发生","尚未突破"),("突破已确认","未突破"),("周线仍偏空","周线强多"),("CVD为正","CVD为负"),
           ("持仓量下降","持仓量增加"),("OI下降","OI增加"),("已经触发","尚未触发"),("原计划主要任务已经完成","原计划尚未开始"))

def audit_contradictions(claims:list[dict[str,Any]],context:dict[str,Any],facts:list[dict[str,Any]])->dict[str,Any]:
    text="\n".join(c["original_text"] for c in claims);items=[]
    def add(code,subject,a,b=None,refs=None):
        core={"code":code,"subject":subject,"claim_a":a,"claim_b_or_context":b}
        items.append({"version":AI_REPORT_CONTRADICTION_AUDIT_VERSION,"contradiction_id":identity("contradiction",core),**core,
                      "predicate":"opposes","values":[a,b],"severity":"CRITICAL","source_refs":sorted(refs or [])})
    timeline=context.get("base_context",context).get("market_timeline",{})
    if timeline.get("current_phase")=="POST_BREAKOUT_PULLBACK":
        for bad in ("尚未突破","仍未突破","突破失败已经确认","当前仍在压缩","第二段已经启动","第二段上涨已经确认"):
            if bad in text:add("CRITICAL_CONTRADICTION","market_phase",bad,"POST_BREAKOUT_PULLBACK")
    for a,b in OPPOSITES:
        if a in text and b in text:add("CRITICAL_CONTRADICTION",a,a,b)
    base=context.get("base_context",context)
    for frame in base.get("timeframe_structures",[]):
        tf=frame.get("timeframe");trend=frame.get("trend_classification")
        if tf=="1W" and trend in {"STRONG_BEAR","BEAR"} and any(x in text for x in ("周线强多","周线已进入强多头")):add("CRITICAL_CONTRADICTION","1W trend","周线强多",trend)
    for fact in facts:
        if fact["category"]=="ORDER_FLOW" and isinstance(fact["value"],dict):
            v=fact["value"];p=v.get("price_change_pct");oi=v.get("oi_change_pct");cvd=v.get("cvd_delta")
            if p is not None and oi is not None:
                if p>0 and oi<0 and any(x in text for x in ("新增多头主导","新多主导")):add("ORDER_FLOW_CONTRADICTION","price_up_oi_down","新增多头主导",v,[fact["fact_id"]])
                if p<0 and oi<0 and "新增空头主导" in text:add("ORDER_FLOW_CONTRADICTION","price_down_oi_down","新增空头主导",v,[fact["fact_id"]])
            if cvd is not None and cvd>0 and "CVD为负" in text:add("ORDER_FLOW_CONTRADICTION","CVD","CVD为负",cvd,[fact["fact_id"]])
            if cvd is not None and cvd<0 and "CVD为正" in text:add("ORDER_FLOW_CONTRADICTION","CVD","CVD为正",cvd,[fact["fact_id"]])
    levels={f["value"].get("level_id"):f for f in facts if f["category"]=="LEVEL" and isinstance(f["value"],dict)}
    for claim in claims:
        for lid in claim.get("level_refs",[]):
            fact=levels.get(lid);role=(fact or {}).get("value",{}).get("role","")
            if fact and ((role=="SUPPORT" and any(x in claim["original_text"] for x in ("当前阻力","当前压力"))) or (role=="RESISTANCE" and "当前支撑" in claim["original_text"])):
                add("KEY_LEVEL_CONTRADICTION",lid,claim["original_text"],role,[fact["fact_id"]])
    return {"version":AI_REPORT_CONTRADICTION_AUDIT_VERSION,"audits":items,"critical_contradiction_count":len(items),
            "failure_codes":sorted({x["code"] for x in items})}
