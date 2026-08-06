from __future__ import annotations
from dashboard.ai_market_analysis.enriched_context import build_enriched_context
from dashboard.ai_market_analysis.macro_evidence import freeze_macro_evidence_set
from dashboard.ai_market_analysis.position_context import none_position_context
from dashboard.ai_market_analysis.report_context_compiler import compile_report_context
from dashboard.ai_market_analysis.report_fact_registry import build_fact_registry
from dashboard.ai_market_analysis.report_prompt_templates import compile_prompt
from .ai4_helpers import base_context

def registry():
    b=base_context();return build_fact_registry(build_enriched_context(b,none_position_context(b["instrument"]),freeze_macro_evidence_set([],b["decision_time"])))

def test_registry_and_numeric_stable():
    a,b=registry(),registry();assert a["registry_hash"]==b["registry_hash"] and a["numeric_registry"]==b["numeric_registry"]

def test_context_pointers_and_limits():
    r=registry();assert len(r["facts"])<=160 and all(f["context_pointer"].startswith("/") for f in r["facts"])

def test_full_budget_and_deterministic_trim():
    a=compile_report_context(registry(),"FULL");b=compile_report_context(registry(),"FULL");assert a==b and a["token_estimate"]<=10000

def test_quick_and_position_budgets():
    assert compile_report_context(registry(),"QUICK")["token_estimate"]<=5000
    assert compile_report_context(registry(),"POSITION_AWARE")["token_estimate"]<=12000

def test_warnings_invalidation_and_timeframes_retained():
    x=compile_report_context(registry(),"FULL");ids={f["fact_id"] for f in x["facts"]};assert "DATA_QUALITY" in ids and all(f"TF{frame}_SUMMARY" in ids for frame in ("15","1H","4H","1D","1W")) and all(f"SCENARIO_0{n}" in ids for n in (1,2,3))

def test_prompt_has_no_raw_or_secrets_or_paths():
    p=compile_prompt(compile_report_context(registry(),"FULL"),"FULL");value=str(p);assert "raw trades" not in value and "API_KEY" not in value and "C:\\" not in value

def test_prompt_hash_stable_and_modes_versioned():
    for mode in ("QUICK","FULL","POSITION_AWARE"):
        c=compile_report_context(registry(),mode);assert compile_prompt(c,mode)["prompt_hash"]==compile_prompt(c,mode)["prompt_hash"]
