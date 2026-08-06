"""Persistent independent AI report worker with bounded retries and single flight."""
from __future__ import annotations
import os, threading
from datetime import datetime, timezone
from typing import Any, Callable
from .canonical import stable_hash
from .report_basic_validation import assemble_generated_text, validate_report, resolve_citations, ReportValidationError
from .report_context_compiler import compile_report_context
from .report_prompt_templates import compile_prompt, repair_prompt
from .report_provider import AIReportProvider, ProviderError
from .report_response_parser import parse_report_response, ReportParseError
from .report_fact_registry import build_fact_registry
from .report_repository import ReportRepository, utc_now
from .report_identity import REPORT_PIPELINE_VERSIONS

EVENT_TYPES=("QUEUED","RUNNING","RETRY_SCHEDULED","COMPLETED","FAILED_RETRYABLE","FAILED_FINAL","CANCEL_REQUESTED","CANCELLED","INTERRUPTED","BUDGET_BLOCKED","VALIDATION_FAILED")
RETRY_DELAYS=(2,5,10)


class ConcurrencyGate:
    def __init__(self,global_limit:int=4): self.global_limit=global_limit;self._lock=threading.Lock();self.active:set[str]=set();self.instruments:set[str]=set()
    def acquire(self,request_id:str,instrument:str)->bool:
        with self._lock:
            if request_id in self.active or instrument in self.instruments or len(self.active)>=self.global_limit:return False
            self.active.add(request_id);self.instruments.add(instrument);return True
    def release(self,request_id:str,instrument:str)->None:
        with self._lock:self.active.discard(request_id);self.instruments.discard(instrument)


class TokenBudget:
    def __init__(self):
        self.daily_input=int(os.getenv("AI_REPORT_DAILY_INPUT_TOKENS","1000000"));self.daily_output=int(os.getenv("AI_REPORT_DAILY_OUTPUT_TOKENS","500000"));self.daily_total=int(os.getenv("AI_REPORT_DAILY_TOTAL_TOKENS","1500000"));self.instrument_total=int(os.getenv("AI_REPORT_INSTRUMENT_DAILY_TOKENS","500000"))
    def allows(self,repo:ReportRepository,instrument:str,input_estimate:int,output_limit:int)->bool:
        total=repo.daily_tokens();inst=repo.daily_tokens(instrument)
        return total["input"]+input_estimate<=self.daily_input and total["output"]+output_limit<=self.daily_output and total["total"]+input_estimate+output_limit<=self.daily_total and inst["total"]+input_estimate+output_limit<=self.instrument_total


class ReportWorker:
    def __init__(self,repository:ReportRepository,provider_factory:Callable[[dict[str,Any]],AIReportProvider],*,gate:ConcurrencyGate|None=None,budget:TokenBudget|None=None):
        self.repository=repository;self.provider_factory=provider_factory;self.gate=gate or ConcurrencyGate();self.budget=budget or TokenBudget();self.running=False
    def recover(self)->int:return self.repository.interrupt_running()
    def run_once(self)->bool:
        queued=self.repository.queued(1)
        if not queued:return False
        request=queued[0]
        if not self.gate.acquire(request["request_id"],request["instrument"]):return False
        try:self._run(request)
        finally:self.gate.release(request["request_id"],request["instrument"])
        return True
    def _attempt_count(self,request_id:str)->int:
        with self.repository.connect() as c:return int(c.execute("SELECT COUNT(*) FROM ai_report_attempts WHERE request_id=?",(request_id,)).fetchone()[0])
    def _record_attempt(self,request:dict[str,Any],number:int,result:Any=None,*,parse_status:str="NOT_RUN",validation_status:str="NOT_RUN",failure_code:str|None=None,error:str|None=None)->None:
        usage=result.usage if result else {}
        self.repository.save_attempt({"attempt_id":f"attempt_{stable_hash([request['request_id'],number])}","request_id":request["request_id"],"attempt_number":number,"provider":request["provider"],"model":request["model"],"started_at":utc_now(),"completed_at":utc_now(),"latency_ms":getattr(result,"latency_ms",None),"http_status":getattr(result,"http_status",None),"input_tokens":usage.get("prompt_tokens",0),"output_tokens":usage.get("completion_tokens",0),"total_tokens":usage.get("total_tokens",0),"finish_reason":getattr(result,"finish_reason",None),"raw_response_hash":getattr(result,"raw_response_hash",None),"parse_status":parse_status,"validation_status":validation_status,"failure_code":failure_code,"sanitized_error":(error or "")[:200] or None,"cost_status":"REQUIRES_RUNTIME_AUDIT","currency":None,"price_schedule_version":None,"estimated_cost":None})
    def _run(self,request:dict[str,Any])->None:
        context=self.repository.load_context(request["context_id"]);registry=build_fact_registry(context);compiled=compile_report_context(registry,request["mode"])
        if not self.budget.allows(self.repository,request["instrument"],compiled["token_estimate"],request["max_output_tokens"]):self.repository.event(request["request_id"],"BUDGET_BLOCKED",{});return
        number=self._attempt_count(request["request_id"])+1
        if number>3:self.repository.event(request["request_id"],"FAILED_FINAL",{"code":"MAX_ATTEMPTS"});return
        self.repository.event(request["request_id"],"RUNNING",{"attempt":number})
        provider=self.provider_factory(request);source_versions={**context["source_versions"],**REPORT_PIPELINE_VERSIONS}
        response_metadata={"context_id":request["context_id"],"request_id":request["request_id"],"mode":request["mode"],"language":request["language"],"model":request["model"],"prompt_version":request["prompt_version"],"source_versions":source_versions,"audit_status":"PENDING"}
        prompt=compile_prompt({**compiled,"required_response_metadata":response_metadata},request["mode"])
        provider_request={**request,"source_versions":source_versions,"compiled_context":compiled,"token_estimate":compiled["token_estimate"],"messages":prompt["messages"],"max_output_tokens":request["max_output_tokens"],"macro_items":context["macro_context"]["items"],"position_source":context["position_context"]["source"]}
        try:result=provider.generate(provider_request)
        except ProviderError as error:
            self._record_attempt(request,number,failure_code=error.code,error=error.code)
            if error.retryable and number<3:self.repository.event(request["request_id"],"RETRY_SCHEDULED",{"delay_seconds":RETRY_DELAYS[number-1],"code":error.code})
            else:self.repository.event(request["request_id"],"FAILED_FINAL",{"code":error.code})
            return
        try:report=parse_report_response(result.raw_text)
        except ReportParseError as error:
            self._record_attempt(request,number,result,parse_status="FAILED",failure_code="INVALID_JSON",error=str(error))
            # Exactly one format-only repair using the same context and model.
            repair_number=number+1
            repair=repair_prompt(result.raw_text,{"context_id":request["context_id"],"request_id":request["request_id"],"mode":request["mode"],"language":request["language"],"model":request["model"],"prompt_version":request["prompt_version"]})
            try:
                repaired=provider.generate({**provider_request,"messages":repair["messages"]});report=parse_report_response(repaired.raw_text);result=repaired;number=repair_number
            except (ProviderError,ReportParseError) as repair_error:
                self._record_attempt(request,repair_number,locals().get("repaired"),parse_status="FAILED",failure_code="JSON_REPAIR_FAILED",error=type(repair_error).__name__)
                self.repository.event(request["request_id"],"FAILED_FINAL",{"code":"JSON_REPAIR_FAILED"});return
        try:validation=validate_report(report,provider_request,registry)
        except ReportValidationError as error:
            self._record_attempt(request,number,result,parse_status="VALID",validation_status="FAILED",failure_code=error.code,error=error.code)
            self.repository.event(request["request_id"],"VALIDATION_FAILED",{"code":error.code});return
        self._record_attempt(request,number,result,parse_status="VALID",validation_status="VALID")
        report=resolve_citations(report,provider_request["macro_items"])
        saved=self.repository.save_report(request,report,assemble_generated_text(report));self.repository.event(request["request_id"],"COMPLETED",saved)
