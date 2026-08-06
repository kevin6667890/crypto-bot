"""Critical forbidden-content recheck over immutable report text."""
from __future__ import annotations
import re
from typing import Any
from .versions import AI_REPORT_SAFETY_AUDIT_VERSION

RULES={
 "ORDER_INSTRUCTION":("立即下单","马上买入","马上卖出","开仓","加杠杆","追单","梭哈","自动创建订单","自动修改止损"),
 "GUARANTEE_OR_CERTAINTY":("保证收益","稳赚","确定上涨","确定下跌","必然上涨","必然下跌"),
 "SECRET_OR_INTERNAL_DATA_EXPOSURE":("API key","api_key","系统Prompt","隐藏推理","环境变量","SELECT * FROM","INSERT INTO"),
}
PATH_RE=re.compile(r"(?:[A-Za-z]:\\(?:Users|Windows|Program Files)\\|/(?:home|etc|root|var)/)\S+")
PROBABILITY_RE=re.compile(r"(?:概率|胜率)\s*(?:为|是|:|：)?\s*(?:百分之[零〇一二两三四五六七八九十百点]+|\d+(?:\.\d+)?%)")

def audit_safety(report:dict[str,Any],claims:list[dict[str,Any]])->dict[str,Any]:
    text=report.get("headline","")+"\n"+"\n".join(c["original_text"] for c in claims);findings=[]
    for code,terms in RULES.items():
        for term in terms:
            if term.lower() in text.lower():findings.append({"code":code,"match":term,"severity":"CRITICAL"})
    if PATH_RE.search(text) or re.search(r"[A-Za-z]:\\+",text):findings.append({"code":"SECRET_OR_INTERNAL_DATA_EXPOSURE","match":"local_path","severity":"CRITICAL"})
    if PROBABILITY_RE.search(text):findings.append({"code":"EXACT_PROBABILITY","match":"exact_probability","severity":"CRITICAL"})
    return {"version":AI_REPORT_SAFETY_AUDIT_VERSION,"findings":findings,"critical_failure_count":len(findings),
            "failure_codes":sorted({x["code"] for x in findings})}
