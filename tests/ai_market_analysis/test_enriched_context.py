from __future__ import annotations
import copy
from dashboard.ai_market_analysis.enriched_context import build_enriched_context
from dashboard.ai_market_analysis.macro_evidence import freeze_macro_evidence_set
from dashboard.ai_market_analysis.position_context import none_position_context,user_position_context
from .ai4_helpers import base_context,macro_items,position_plan

def build(pos=None,macro=None):
    b=base_context();return build_enriched_context(b,pos or none_position_context(b["instrument"]),freeze_macro_evidence_set(macro or [],b["decision_time"]))

def test_identity_stable_and_base_immutable():
    b=base_context();before=copy.deepcopy(b);a=build_enriched_context(b,none_position_context(b["instrument"]),freeze_macro_evidence_set([],b["decision_time"]));z=build();assert a["enriched_context_id"]==z["enriched_context_id"] and b==before

def test_position_and_macro_change_identity():
    b=base_context();p=user_position_context(position_plan(),b["instrument"],b["decision_time"],1900);assert build()["enriched_context_id"]!=build(p)["enriched_context_id"]!=build(p,macro_items())["enriched_context_id"]

def test_identity_excludes_generated_at_and_paths():
    a=build();b=base_context();b["generated_at"]="2099-01-01T00:00:00Z";z=build_enriched_context(b,none_position_context(b["instrument"]),freeze_macro_evidence_set([],b["decision_time"]));assert a["enriched_context_id"]==z["enriched_context_id"]
