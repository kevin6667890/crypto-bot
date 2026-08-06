"""Frozen position provenance and discipline checks."""
from __future__ import annotations
from typing import Any
from .versions import AI_REPORT_POSITION_AUDIT_VERSION

NONE_FORBIDDEN=("你的多单","你的空单","你的成本","建议减仓","减仓一半","卖一半","剩余仓位","浮盈","止损移动")
REAL_WORDS=("你的真实持仓","真实资产","实盘持仓")

def audit_position(claims:list[dict[str,Any]],position:dict[str,Any])->dict[str,Any]:
    text="\n".join(c["original_text"] for c in claims);source=position.get("source","NONE");codes=[]
    if source=="NONE" and any(x in text for x in NONE_FORBIDDEN):codes.append("UNSUPPORTED_POSITION")
    if source=="PAPER" and (any(x in text for x in REAL_WORDS) or ("模拟" not in text and any(c["claim_type"].startswith("POSITION") for c in claims))):codes.append("UNSUPPORTED_POSITION")
    if source=="USER_DECLARED":
        if any(x in text for x in ("卖一半","减仓50%","减仓百分之五十")) and not any(float(position.get(k) or -1)==.5 for k in ("plan_completion_ratio",)):codes.append("UNSUPPORTED_POSITION")
        if any(x in text for x in ("建议加杠杆","应当加杠杆","要求加杠杆","自动延长周期")):codes.append("UNSUPPORTED_POSITION")
        if position.get("plan_completion_ratio",0)>=.8 and "原计划尚未开始" in text:codes.append("UNSUPPORTED_POSITION")
    return {"version":AI_REPORT_POSITION_AUDIT_VERSION,"source":source,"result":"FAILED" if codes else "PASSED",
            "discipline_warnings":position.get("discipline_warnings",[]),"failure_codes":sorted(set(codes))}
