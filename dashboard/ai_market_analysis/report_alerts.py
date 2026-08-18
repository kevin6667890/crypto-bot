"""Deterministic AI-6B alert evaluation; no notification side effects."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any,Mapping

DEFAULT_POLICY=Path(__file__).resolve().parents[2]/"config"/"ai6b_alert_policy.json"
OPERATORS={">":lambda a,b:a>b,">=":lambda a,b:a>=b,"<":lambda a,b:a<b,"<=":lambda a,b:a<=b,"==":lambda a,b:a==b}

def load_alert_policy(path:str|Path=DEFAULT_POLICY)->dict[str,Any]:
    value=json.loads(Path(path).read_text(encoding="utf-8"))
    required={"id","metric","operator","threshold","stop"};ids=set()
    for item in value.get("alerts",[]):
        if not required.issubset(item) or item["operator"] not in OPERATORS or item["id"] in ids:raise ValueError("INVALID_ALERT_POLICY")
        ids.add(item["id"])
    if len(ids)<19:raise ValueError("INCOMPLETE_ALERT_POLICY")
    return value

def evaluate_alerts(metrics:Mapping[str,int|float],policy:Mapping[str,Any]|None=None)->list[dict[str,Any]]:
    selected=dict(policy or load_alert_policy());events=[]
    for item in selected["alerts"]:
        if item["metric"] not in metrics:continue
        actual=metrics[item["metric"]]
        if OPERATORS[item["operator"]](actual,item["threshold"]):
            events.append({"alert_id":item["id"],"metric":item["metric"],"actual":actual,"threshold":item["threshold"],"stop":bool(item["stop"]),"owner":selected["owner"],"response_sla_seconds":selected["response_sla_seconds"]})
    return events
