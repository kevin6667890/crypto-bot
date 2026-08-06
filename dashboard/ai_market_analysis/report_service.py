"""Shadow-only orchestration for freezing and queuing AI market reports."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Any
from .enriched_context import build_enriched_context
from .macro_evidence import freeze_macro_evidence_set
from .position_context import none_position_context,user_position_context,paper_position_context
from .report_context_compiler import compile_report_context
from .report_fact_registry import build_fact_registry
from .report_identity import report_request_identity
from .report_prompt_templates import compile_prompt
from .report_repository import ReportRepository
from .versions import AI_REPORT_PROMPT_VERSION,AI_REPORT_REQUEST_VERSION

MODES=("QUICK","FULL","POSITION_AWARE");LANGUAGES=("zh-CN",)
OUTPUT_LIMITS={"QUICK":900,"FULL":4000,"POSITION_AWARE":4000}

def enabled(name:str)->bool:return os.getenv(name,"false").lower()=="true"

class ReportService:
    def __init__(self,repository:ReportRepository,paper_db: str|None=None):self.repository=repository;self.paper_db=paper_db
    def submit(self,base_context:dict[str,Any],*,mode:str="FULL",language:str="zh-CN",position_source:str="NONE",position_plan:dict[str,Any]|None=None,macro_evidence:list[dict[str,Any]]|None=None,provider:str="fake",model:str="fake-ai4",current_mark:float|None=None)->dict[str,Any]:
        if mode not in MODES or language not in LANGUAGES:raise ValueError("invalid mode or language")
        decision=base_context["decision_time"]
        if datetime.fromisoformat(decision.replace("Z","+00:00"))>datetime.now(timezone.utc):
            # Synthetic fixtures may be future relative to wall time only through CLI/test explicit context.
            if not base_context.get("provenance",{}).get("fixture"): raise ValueError("future decision time")
        instrument=base_context["instrument"]
        downgraded=None
        if position_source=="NONE":position=none_position_context(instrument)
        elif position_source=="USER_DECLARED":
            if not position_plan:raise ValueError("position plan required")
            position=user_position_context(position_plan,instrument,decision,current_mark)
        elif position_source=="PAPER":
            if not self.paper_db:raise ValueError("paper database unavailable")
            position=paper_position_context(self.paper_db,instrument,current_mark,decision)
        else:raise ValueError("invalid position source")
        if mode=="POSITION_AWARE" and position["source"]=="NONE":mode="FULL";downgraded="POSITION_SOURCE_NONE"
        macro=freeze_macro_evidence_set(macro_evidence or [],decision);enriched=build_enriched_context(base_context,position,macro)
        self.repository.save_context(enriched);self.repository.save_macro_set(macro)
        registry=build_fact_registry(enriched);compiled=compile_report_context(registry,mode);prompt=compile_prompt(compiled,mode)
        request_id=report_request_identity(enriched["enriched_context_id"],mode,language,AI_REPORT_PROMPT_VERSION,provider,model)
        value={"request_id":request_id,"request_identity":request_id,"request_version":AI_REPORT_REQUEST_VERSION,"context_id":enriched["enriched_context_id"],"instrument":instrument,"mode":mode,"language":language,"prompt_version":AI_REPORT_PROMPT_VERSION,"provider":provider,"model":model,"max_output_tokens":OUTPUT_LIMITS[mode]}
        request,created=self.repository.create_request(value)
        completed=self.repository.get_report(request_id=request_id)
        return {"status_code":200 if completed else 202,"request_id":request_id,"context_id":enriched["enriched_context_id"],"base_context_id":base_context["context_id"],"created":created,"downgraded_reason":downgraded,"report":completed,"fact_count":len(registry["facts"]),"numeric_registry_count":len(registry["numeric_registry"]),"prompt_token_estimate":compiled["token_estimate"],"omitted_fact_ids":compiled["omitted_fact_ids"],"prompt_hash":prompt["prompt_hash"]}
