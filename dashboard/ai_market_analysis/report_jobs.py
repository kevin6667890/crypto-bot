"""Persistent independent AI report worker with bounded retries and single flight."""
from __future__ import annotations
import os, threading
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, Callable
from .canonical import stable_hash
from .report_basic_validation import assemble_generated_text, validate_report, resolve_citations, ReportValidationError
from .report_context_compiler import compile_report_context
from .report_prompt_templates import compile_prompt
from .report_provider import AIReportProvider, ProviderError
from .report_response_parser import parse_report_response, ReportParseError
from .report_registry_snapshot import validate_registry_snapshot
from .report_repository import ReportRepository, utc_now
from .report_identity import REPORT_PIPELINE_VERSIONS
from .live_provider_guard import trip_if_armed
from .provider_limits import (
    DAILY_INPUT_TOKEN_SAFETY_CAP,
    DAILY_OUTPUT_TOKEN_SAFETY_CAP,
    DAILY_TOTAL_TOKEN_SAFETY_CAP,
    LIVE_PROVIDER_CALLS_PER_24H,
    REQUEST_INPUT_TOKEN_MAX,
)

EVENT_TYPES=("QUEUED","RUNNING","RETRY_SCHEDULED","COMPLETED","FAILED_RETRYABLE","FAILED_FINAL","CANCEL_REQUESTED","CANCELLED","INTERRUPTED","BUDGET_BLOCKED","VALIDATION_FAILED")
RETRY_DELAYS=(2,5,10)
NON_CHARGEABLE_PROVIDERS=frozenset({"fake","mock","local","dry-run","dry_run","test"})


def provider_budget_chargeable(provider:str)->bool:
    return str(provider).strip().lower() not in NON_CHARGEABLE_PROVIDERS


def provider_retry_allowed(error:ProviderError)->bool:
    """Ignore provider-supplied retry hints unless the approved class matches."""
    if not error.retryable:return False
    if error.http_status is not None:return error.http_status==429 or 500<=error.http_status<600
    return error.code in {"CONNECTION_ERROR","TIMEOUT","CONNECTION_OR_TIMEOUT"}


class ConcurrencyGate:
    def __init__(self,global_limit:int|None=None): self.global_limit=int(global_limit if global_limit is not None else os.getenv("AI_REPORT_GLOBAL_CONCURRENCY","1"));self._lock=threading.Lock();self.active:set[str]=set();self.instruments:set[str]=set()
    def acquire(self,request_id:str,instrument:str)->bool:
        with self._lock:
            if request_id in self.active or instrument in self.instruments or len(self.active)>=self.global_limit:return False
            self.active.add(request_id);self.instruments.add(instrument);return True
    def release(self,request_id:str,instrument:str)->None:
        with self._lock:self.active.discard(request_id);self.instruments.discard(instrument)


class TokenBudget:
    def __init__(self):
        self.daily_input=int(os.getenv("AI_REPORT_DAILY_INPUT_TOKENS",str(DAILY_INPUT_TOKEN_SAFETY_CAP)));self.daily_output=int(os.getenv("AI_REPORT_DAILY_OUTPUT_TOKENS",str(DAILY_OUTPUT_TOKEN_SAFETY_CAP)));self.daily_total=int(os.getenv("AI_REPORT_DAILY_TOTAL_TOKENS",str(DAILY_TOTAL_TOKEN_SAFETY_CAP)));self.instrument_total=int(os.getenv("AI_REPORT_INSTRUMENT_DAILY_TOKENS",str(DAILY_TOTAL_TOKEN_SAFETY_CAP)));self.request_input=int(os.getenv("AI_REPORT_REQUEST_INPUT_TOKEN_MAX",str(REQUEST_INPUT_TOKEN_MAX)));self.live_calls=int(os.getenv("AI_REPORT_CANARY_MAX_LIVE_REQUESTS",str(LIVE_PROVIDER_CALLS_PER_24H)));self.currency_cap=Decimal(os.getenv("AI_REPORT_DAILY_CURRENCY_CAP_USD","2"));self.cost_status=os.getenv("AI_REPORT_COST_STATUS","REQUIRES_RUNTIME_AUDIT");self.input_price=Decimal(os.getenv("AI_REPORT_INPUT_USD_PER_MILLION","0"));self.output_price=Decimal(os.getenv("AI_REPORT_OUTPUT_USD_PER_MILLION","0"))
    def projected_cost(self,input_tokens:int,output_tokens:int)->Decimal:
        return (Decimal(input_tokens)*self.input_price+Decimal(output_tokens)*self.output_price)/Decimal(1_000_000)
    def reason(self,repo:ReportRepository,instrument:str,input_estimate:int,output_limit:int,provider:str="fake")->str|None:
        if input_estimate>self.request_input:return "REQUEST_INPUT_TOKEN_CAP"
        if not provider_budget_chargeable(provider):return None
        total=repo.daily_tokens(chargeable_only=True);inst=repo.daily_tokens(instrument,chargeable_only=True)
        if total["input"]+input_estimate>self.daily_input:return "DAILY_INPUT_TOKEN_CAP"
        if total["output"]+output_limit>self.daily_output:return "DAILY_OUTPUT_TOKEN_CAP"
        if total["total"]+input_estimate+output_limit>self.daily_total:return "DAILY_TOTAL_TOKEN_CAP"
        if inst["total"]+input_estimate+output_limit>self.instrument_total:return "INSTRUMENT_TOKEN_CAP"
        if provider!="fake":
            live=repo.daily_live_provider_usage()
            if live["calls"]>=self.live_calls:return "LIVE_PROVIDER_REQUEST_CAP"
            if self.cost_status not in {"AUDITED","B3_CONTROL_LEDGER"} or self.input_price<=0 or self.output_price<=0:return "PROVIDER_PRICE_AUDIT_REQUIRED"
            if self.cost_status=="AUDITED" and Decimal(str(live["estimated_cost"]))+self.projected_cost(input_estimate,output_limit)>self.currency_cap:return "DAILY_CURRENCY_CAP"
        return None
    def allows(self,repo:ReportRepository,instrument:str,input_estimate:int,output_limit:int,provider:str="fake")->bool:
        return self.reason(repo,instrument,input_estimate,output_limit,provider) is None


class ReportWorker:
    def __init__(self,repository:ReportRepository,provider_factory:Callable[[dict[str,Any]],AIReportProvider],*,gate:ConcurrencyGate|None=None,budget:TokenBudget|None=None):
        self.repository=repository;self.provider_factory=provider_factory;self.gate=gate or ConcurrencyGate();self.budget=budget or TokenBudget();self.running=False
    def recover(self)->int:return self.repository.interrupt_running()
    def run_once(self)->bool:
        queued=self.repository.queued(1)
        if not queued:return False
        request=queued[0]
        if request["provider"]!="fake" and self.repository.status(request["request_id"])["status"]=="INTERRUPTED":
            trip_if_armed("DUPLICATE_PROVIDER_CHARGE",evidence_id=request["request_id"])
            self.repository.event(request["request_id"],"FAILED_FINAL",{"code":"INTERRUPTED_LIVE_CALL_CHARGE_UNCERTAIN"});return True
        if not self.gate.acquire(request["request_id"],request["instrument"]):return False
        try:self._run(request)
        finally:self.gate.release(request["request_id"],request["instrument"])
        return True
    def _attempt_count(self,request_id:str)->int:
        with self.repository.connect() as c:return int(c.execute("SELECT COUNT(*) FROM ai_report_attempts WHERE request_id=?",(request_id,)).fetchone()[0])
    def _record_attempt(self,request:dict[str,Any],number:int,result:Any=None,*,parse_status:str="NOT_RUN",validation_status:str="NOT_RUN",failure_code:str|None=None,error:str|None=None)->None:
        usage=result.usage if result else {}
        audited=self.budget.cost_status=="AUDITED" and self.budget.input_price>0 and self.budget.output_price>0
        controlled=self.budget.cost_status=="B3_CONTROL_LEDGER"
        exact=self.budget.projected_cost(int(usage.get("prompt_tokens",0)),int(usage.get("completion_tokens",0))) if audited else None
        # The legacy column has SQLite REAL affinity. Formal B3 therefore keeps
        # authoritative exact costs in the separate TEXT control ledger.
        estimated_cost=format(exact,"f") if exact is not None else None
        self.repository.save_attempt({"attempt_id":f"attempt_{stable_hash([request['request_id'],number])}","request_id":request["request_id"],"attempt_number":number,"provider":request["provider"],"model":request["model"],"started_at":utc_now(),"completed_at":utc_now(),"latency_ms":getattr(result,"latency_ms",None),"http_status":getattr(result,"http_status",None),"input_tokens":usage.get("prompt_tokens",0),"output_tokens":usage.get("completion_tokens",0),"total_tokens":usage.get("total_tokens",0),"finish_reason":getattr(result,"finish_reason",None),"raw_response_hash":getattr(result,"raw_response_hash",None),"parse_status":parse_status,"validation_status":validation_status,"failure_code":failure_code,"sanitized_error":(error or "")[:200] or None,"cost_status":"B3_CONTROL_LEDGER" if controlled else ("AUDITED" if audited else "REQUIRES_RUNTIME_AUDIT"),"currency":"USD" if audited or controlled else None,"price_schedule_version":os.getenv("AI_REPORT_PRICE_SCHEDULE_VERSION") if audited or controlled else None,"estimated_cost":estimated_cost,"prompt_hash":request.get("generation_prompt_hash")})
    def _run(self,request:dict[str,Any])->None:
        context=self.repository.load_context(request["context_id"])
        try:snapshot=self.repository.load_registry_snapshot(registry_snapshot_id=request.get("registry_snapshot_id"))
        except KeyError:self.repository.event(request["request_id"],"FAILED_FINAL",{"code":"REGISTRY_SNAPSHOT_NOT_FOUND"});return
        failures=validate_registry_snapshot(snapshot)
        if failures:self.repository.event(request["request_id"],"FAILED_FINAL",{"code":failures[0]});return
        registry=snapshot["fact_registry"];compiled=compile_report_context(registry,request["mode"]);request={**request,"generation_prompt_hash":snapshot["prompt_hash"]}
        budget_reason=self.budget.reason(self.repository,request["instrument"],compiled["token_estimate"],request["max_output_tokens"],request["provider"])
        if budget_reason:self.repository.event(request["request_id"],"BUDGET_BLOCKED",{"code":budget_reason});return
        number=self._attempt_count(request["request_id"])+1
        if number>3:self.repository.event(request["request_id"],"FAILED_FINAL",{"code":"MAX_ATTEMPTS"});return
        self.repository.event(request["request_id"],"RUNNING",{"attempt":number})
        provider=self.provider_factory(request);source_versions=snapshot["source_versions"]
        response_metadata={"context_id":request["context_id"],"request_id":request["request_id"],"mode":request["mode"],"language":request["language"],"model":request["model"],"prompt_version":request["prompt_version"],"source_versions":source_versions,"audit_status":"PENDING"}
        prompt=compile_prompt(compiled,request["mode"])
        if prompt["prompt_hash"]!=snapshot["prompt_hash"]:self.repository.event(request["request_id"],"FAILED_FINAL",{"code":"REGISTRY_PROMPT_HASH_MISMATCH"});return
        provider_request={**request,"source_versions":source_versions,"compiled_context":compiled,"token_estimate":compiled["token_estimate"],"messages":prompt["messages"],"max_output_tokens":request["max_output_tokens"],"macro_items":context["macro_context"]["items"],"position_source":context["position_context"]["source"]}
        try:result=provider.generate(provider_request)
        except ProviderError as error:
            self._record_attempt(request,number,failure_code=error.code,error=error.code)
            if provider_retry_allowed(error) and number<3:self.repository.event(request["request_id"],"RETRY_SCHEDULED",{"delay_seconds":RETRY_DELAYS[number-1],"code":error.code})
            else:self.repository.event(request["request_id"],"FAILED_FINAL",{"code":error.code})
            return
        usage=result.usage or {}
        if int(usage.get("prompt_tokens",0))>self.budget.request_input or int(usage.get("completion_tokens",0))>request["max_output_tokens"]:
            self._record_attempt(request,number,result,failure_code="PROVIDER_USAGE_CAP_EXCEEDED",error="PROVIDER_USAGE_CAP_EXCEEDED")
            self.repository.event(request["request_id"],"BUDGET_BLOCKED",{"code":"PROVIDER_USAGE_CAP_EXCEEDED"});return
        try:report=parse_report_response(result.raw_text)
        except ReportParseError as error:
            truncated=str(getattr(result,"finish_reason","") or "").lower()=="length"
            failure_code="PROVIDER_OUTPUT_TRUNCATED" if truncated else "INVALID_JSON"
            event_code="PROVIDER_OUTPUT_TRUNCATED" if truncated else "SCHEMA_FAILURE_NO_PROVIDER_RETRY"
            self._record_attempt(request,number,result,parse_status="FAILED",failure_code=failure_code,error=str(error))
            self.repository.event(request["request_id"],"FAILED_FINAL",{"code":event_code});return
        try:validation=validate_report(report,provider_request,registry)
        except ReportValidationError as error:
            self._record_attempt(request,number,result,parse_status="VALID",validation_status="FAILED",failure_code=error.code,error=error.code)
            self.repository.event(request["request_id"],"VALIDATION_FAILED",{"code":error.code});return
        self._record_attempt(request,number,result,parse_status="VALID",validation_status="VALID")
        report=resolve_citations(report,provider_request["macro_items"])
        saved=self.repository.save_report(request,report,assemble_generated_text(report));self.repository.event(request["request_id"],"COMPLETED",saved)
