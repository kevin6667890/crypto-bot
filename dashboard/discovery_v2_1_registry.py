"""Exact Phase 5A parameter plan mapped to corrected v2.1 templates."""
from __future__ import annotations
from .discovery_v2_registry import plan as v2_plan, PARAMETER_CLASSES, V2_SEMANTIC_SEED
from .strategy_v2_1 import TEMPLATES

REGISTRY_VERSION="discovery-strategy-v2.1"
SAMPLING_POLICY_VERSION="discovery-v2.1-fixed-plan-v1"
BASE_TO_CORRECTED={name.replace("_V2_1","_V2"):name for name in TEMPLATES}
CORRECTED_TO_BASE={v:k for k,v in BASE_TO_CORRECTED.items()}

def plan(maximum:int=32):
    rows,rejected,meta=v2_plan(36)
    mapped=[(BASE_TO_CORRECTED[t],p) for t,p in rows][:maximum]
    return mapped,[(BASE_TO_CORRECTED[t],p,r) for t,p,r in rejected],{
        **meta,"policy_version":SAMPLING_POLICY_VERSION,"sampled_candidate_count":len(mapped),
        "source_plan_policy":meta["policy_version"],"semantic_seed":V2_SEMANTIC_SEED}
