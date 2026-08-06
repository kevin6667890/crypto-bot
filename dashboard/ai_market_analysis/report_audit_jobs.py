"""Bounded, opt-in deterministic audit queue worker."""
from __future__ import annotations
import os,threading
from typing import Any
from .report_audit_identity import audit_identity,AUDIT_SOURCE_VERSIONS
from .report_audit_repository import AuditRepository
from .report_audit_service import audit_report

def audit_enabled(name:str)->bool:return os.getenv(name,"false").lower()=="true"

class AuditGate:
    def __init__(self,limit:int=2):self.limit=limit;self.lock=threading.Lock();self.active=set()
    def acquire(self,audit_id:str)->bool:
        with self.lock:
            if audit_id in self.active or len(self.active)>=self.limit:return False
            self.active.add(audit_id);return True
    def release(self,audit_id:str)->None:
        with self.lock:self.active.discard(audit_id)

def queue_audit(repository:AuditRepository,report_id:str)->dict[str,Any]:
    bundle=repository.load_input(report_id);aid=audit_identity(bundle["report_id"],bundle["report_hash"],bundle["context_id"],bundle["context_hash"],AUDIT_SOURCE_VERSIONS)
    existing=repository.latest(report_id)
    if existing and existing["audit_id"]==aid:return {"audit_id":aid,"created":False,"status":existing["status"]}
    event=repository.latest_event(aid)
    if event not in {"AUDIT_QUEUED","AUDIT_RUNNING"}:repository.event(aid,report_id,"AUDIT_QUEUED",{})
    return {"audit_id":aid,"created":event is None,"status":"AUDIT_QUEUED"}

class AuditWorker:
    def __init__(self,repository:AuditRepository,gate:AuditGate|None=None):self.repository=repository;self.gate=gate or AuditGate()
    def recover(self)->int:return self.repository.interrupt_running()
    def run_once(self)->bool:
        rows=self.repository.queued(1)
        if not rows:return False
        aid,rid=rows[0]
        if not self.gate.acquire(aid):return False
        try:
            self.repository.event(aid,rid,"AUDIT_RUNNING",{})
            try:audit=audit_report(self.repository.load_input(rid));self.repository.save_audit(audit)
            except Exception as exc:
                self.repository.event(aid,rid,"AUDIT_ERROR",{"code":"AUDIT_INTERNAL_ERROR","type":type(exc).__name__});return True
            self.repository.event(aid,rid,"AUDIT_"+audit["status"],{"score":audit["scorecard"]["overall"]});return True
        finally:self.gate.release(aid)
