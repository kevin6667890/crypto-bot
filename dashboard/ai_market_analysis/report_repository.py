"""Explicitly migrated, isolated and immutable SQLite report repository."""
from __future__ import annotations
import json, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from .canonical import canonical_json, stable_hash
from .report_identity import report_identity
from .report_registry_snapshot import snapshot_db_values, validate_registry_snapshot
from .versions import AI_REPORT_DATABASE_SCHEMA_VERSION, AI_REPORT_REPOSITORY_VERSION

DEFAULT_REPORT_DB=Path("data_cache/ai_market_reports.db")
MIGRATION_KEY="ai-market-report-db-v1"

def utc_now()->str: return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

SCHEMA="""
CREATE TABLE IF NOT EXISTS ai_report_migrations(migration_key TEXT PRIMARY KEY,schema_version TEXT NOT NULL,completed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_market_contexts(context_id TEXT PRIMARY KEY,base_context_id TEXT NOT NULL,schema_version TEXT NOT NULL,instrument TEXT NOT NULL,decision_time TEXT NOT NULL,position_fingerprint TEXT NOT NULL,macro_evidence_set_id TEXT NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,source_versions_json TEXT NOT NULL,quality TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_position_plans(plan_id TEXT PRIMARY KEY,plan_version TEXT NOT NULL,instrument TEXT NOT NULL,source TEXT NOT NULL,effective_at TEXT NOT NULL,supersedes_plan_id TEXT,status TEXT NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_macro_evidence_sets(evidence_set_id TEXT PRIMARY KEY,decision_time TEXT NOT NULL,item_count INTEGER NOT NULL,categories_json TEXT NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,quality TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_report_registry_snapshots(registry_snapshot_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,report_context_id TEXT NOT NULL,enriched_context_id TEXT NOT NULL,snapshot_version TEXT NOT NULL,fact_registry_version TEXT NOT NULL,numeric_registry_version TEXT NOT NULL,fact_registry_json TEXT NOT NULL,fact_registry_hash TEXT NOT NULL,numeric_registry_json TEXT NOT NULL,numeric_registry_hash TEXT NOT NULL,prompt_hash TEXT NOT NULL,source_versions_json TEXT NOT NULL,source_versions_hash TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_report_requests(request_id TEXT PRIMARY KEY,request_identity TEXT NOT NULL UNIQUE,context_id TEXT NOT NULL,instrument TEXT NOT NULL,mode TEXT NOT NULL,language TEXT NOT NULL,prompt_version TEXT NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,max_output_tokens INTEGER NOT NULL,registry_snapshot_id TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_report_request_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_report_attempts(attempt_id TEXT PRIMARY KEY,request_id TEXT NOT NULL,attempt_number INTEGER NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,started_at TEXT NOT NULL,completed_at TEXT,latency_ms INTEGER,http_status INTEGER,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,finish_reason TEXT,raw_response_hash TEXT,parse_status TEXT,validation_status TEXT,failure_code TEXT,sanitized_error TEXT,cost_status TEXT NOT NULL,currency TEXT,price_schedule_version TEXT,estimated_cost REAL,prompt_hash TEXT,UNIQUE(request_id,attempt_number));
CREATE TABLE IF NOT EXISTS ai_market_reports(report_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,context_id TEXT NOT NULL,mode TEXT NOT NULL,language TEXT NOT NULL,response_json TEXT NOT NULL,response_hash TEXT NOT NULL,model TEXT NOT NULL,provider TEXT NOT NULL,prompt_version TEXT NOT NULL,generated_text TEXT NOT NULL,audit_status TEXT NOT NULL CHECK(audit_status='PENDING'),created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_report_events_request ON ai_report_request_events(request_id,event_id);
CREATE INDEX IF NOT EXISTS idx_reports_latest ON ai_market_reports(context_id,created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_registry_snapshot_request ON ai_report_registry_snapshots(request_id);
CREATE INDEX IF NOT EXISTS idx_ai_registry_snapshot_context ON ai_report_registry_snapshots(enriched_context_id,registry_snapshot_id);
CREATE TRIGGER IF NOT EXISTS trg_ai_registry_snapshot_no_update BEFORE UPDATE ON ai_report_registry_snapshots BEGIN SELECT RAISE(ABORT,'REGISTRY_SNAPSHOT_MUTATED'); END;
CREATE TRIGGER IF NOT EXISTS trg_ai_registry_snapshot_no_delete BEFORE DELETE ON ai_report_registry_snapshots BEGIN SELECT RAISE(ABORT,'REGISTRY_SNAPSHOT_MUTATED'); END;
"""

def migrate_database(path: str|Path)->dict[str,Any]:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA busy_timeout=5000")
        with conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO ai_report_migrations VALUES(?,?,?)",(MIGRATION_KEY,AI_REPORT_DATABASE_SCHEMA_VERSION,utc_now()))
    return {"schema_version":AI_REPORT_DATABASE_SCHEMA_VERSION,"database":str(path),"applied":True}


class ReportRepository:
    def __init__(self,path: str|Path=DEFAULT_REPORT_DB): self.path=Path(path)
    @contextmanager
    def connect(self)->Iterator[sqlite3.Connection]:
        conn=sqlite3.connect(self.path,timeout=5);conn.row_factory=sqlite3.Row;conn.execute("PRAGMA busy_timeout=5000");conn.execute("PRAGMA wal_autocheckpoint=1000")
        try: yield conn;conn.commit()
        except Exception: conn.rollback();raise
        finally: conn.close()
    def schema_version(self)->str|None:
        if not self.path.exists(): return None
        with self.connect() as c:
            try:r=c.execute("SELECT schema_version FROM ai_report_migrations WHERE migration_key=?",(MIGRATION_KEY,)).fetchone()
            except sqlite3.OperationalError:return None
        return r[0] if r else None
    def save_context(self,context:dict[str,Any])->None:
        payload=canonical_json(context);h=stable_hash(context)
        with self.connect() as c:
            row=c.execute("SELECT payload_hash FROM ai_market_contexts WHERE context_id=?",(context["enriched_context_id"],)).fetchone()
            if row and row[0]!=h: raise ValueError("CONTEXT_IDENTITY_CONFLICT")
            c.execute("INSERT OR IGNORE INTO ai_market_contexts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(context["enriched_context_id"],context["base_context_id"],context["enriched_context_version"],context["instrument"],context["decision_time"],context["position_fingerprint"],context["macro_evidence_set_id"],payload,h,canonical_json(context["source_versions"]),context["base_context"].get("data_quality",{}).get("overall","UNKNOWN"),utc_now()))
    def load_context(self,context_id:str)->dict[str,Any]:
        with self.connect() as c:r=c.execute("SELECT payload_json FROM ai_market_contexts WHERE context_id=?",(context_id,)).fetchone()
        if not r: raise KeyError("context not found")
        return json.loads(r[0])
    def save_position_plan(self,plan:dict[str,Any])->None:
        with self.connect() as c:
            c.execute("INSERT INTO ai_position_plans VALUES(?,?,?,?,?,?,?,?,?,?)",(plan["plan_id"],plan["plan_version"],plan["instrument"],plan["source"],plan["effective_at"],plan.get("supersedes_plan_id"),plan["status"],canonical_json(plan),plan["payload_hash"],utc_now()))
    def load_position_plan(self,plan_id:str)->dict[str,Any]:
        with self.connect() as c:r=c.execute("SELECT payload_json FROM ai_position_plans WHERE plan_id=?",(plan_id,)).fetchone()
        if not r:raise KeyError("plan not found")
        return json.loads(r[0])
    def save_macro_set(self,value:dict[str,Any])->None:
        payload=canonical_json(value);h=stable_hash(value)
        with self.connect() as c:c.execute("INSERT OR IGNORE INTO ai_macro_evidence_sets VALUES(?,?,?,?,?,?,?,?)",(value["evidence_set_id"],value["decision_time"],len(value["items"]),canonical_json(sorted({i["category"] for i in value["items"]})),payload,h,value["quality"],utc_now()))
    def load_macro_set(self,evidence_set_id:str)->dict[str,Any]:
        with self.connect() as c:r=c.execute("SELECT payload_json FROM ai_macro_evidence_sets WHERE evidence_set_id=?",(evidence_set_id,)).fetchone()
        if not r:raise KeyError("macro evidence set not found")
        return json.loads(r[0])
    def create_request(self,value:dict[str,Any])->tuple[dict[str,Any],bool]:
        with self.connect() as c:
            row=c.execute("SELECT * FROM ai_report_requests WHERE request_identity=?",(value["request_identity"],)).fetchone()
            if row:return dict(row),False
            active=c.execute("SELECT COUNT(*) FROM ai_report_requests r WHERE (SELECT event_type FROM ai_report_request_events e WHERE e.request_id=r.request_id ORDER BY event_id DESC LIMIT 1) IN ('QUEUED','RUNNING','RETRY_SCHEDULED','INTERRUPTED')").fetchone()[0]
            if active>=int(__import__('os').getenv("AI_REPORT_QUEUE_MAX","100")):raise OverflowError("AI report queue is full")
            cols=("request_id","request_identity","context_id","instrument","mode","language","prompt_version","provider","model","max_output_tokens","registry_snapshot_id","created_at")
            c.execute(f"INSERT INTO ai_report_requests({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",(value["request_id"],value["request_identity"],value["context_id"],value["instrument"],value["mode"],value["language"],value["prompt_version"],value["provider"],value["model"],value["max_output_tokens"],value.get("registry_snapshot_id"),utc_now()))
        self.event(value["request_id"],"QUEUED",{})
        return value,True
    def event(self,request_id:str,event_type:str,payload:dict[str,Any])->None:
        with self.connect() as c:c.execute("INSERT INTO ai_report_request_events(request_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",(request_id,event_type,canonical_json(payload),utc_now()))
    def status(self,request_id:str)->dict[str,Any]:
        with self.connect() as c:
            req=c.execute("SELECT * FROM ai_report_requests WHERE request_id=?",(request_id,)).fetchone();ev=c.execute("SELECT event_type,payload_json,created_at FROM ai_report_request_events WHERE request_id=? ORDER BY event_id",(request_id,)).fetchall()
        if not req:raise KeyError("request not found")
        out=dict(req);out["events"]=[dict(x) for x in ev];out["status"]=ev[-1]["event_type"] if ev else "UNKNOWN";return out
    def queued(self,limit:int=100)->list[dict[str,Any]]:
        with self.connect() as c:
            rows=c.execute("SELECT r.* FROM ai_report_requests r JOIN ai_report_request_events e ON e.event_id=(SELECT MAX(e2.event_id) FROM ai_report_request_events e2 WHERE e2.request_id=r.request_id) WHERE e.event_type IN ('QUEUED','RETRY_SCHEDULED','INTERRUPTED') ORDER BY r.created_at LIMIT ?",(limit,)).fetchall()
        return [dict(x) for x in rows]
    def interrupt_running(self)->int:
        with self.connect() as c:rows=c.execute("SELECT DISTINCT request_id FROM ai_report_request_events e WHERE event_type='RUNNING' AND event_id=(SELECT MAX(e2.event_id) FROM ai_report_request_events e2 WHERE e2.request_id=e.request_id)").fetchall()
        for r in rows:self.event(r[0],"INTERRUPTED",{"reason":"worker_restart"})
        return len(rows)
    def cancel(self,request_id:str)->str:
        status=self.status(request_id)["status"]
        if status in {"COMPLETED","FAILED_FINAL","CANCELLED"}:return status
        self.event(request_id,"CANCEL_REQUESTED",{});self.event(request_id,"CANCELLED",{});return "CANCELLED"
    def save_attempt(self,a:dict[str,Any])->None:
        cols=("attempt_id","request_id","attempt_number","provider","model","started_at","completed_at","latency_ms","http_status","input_tokens","output_tokens","total_tokens","finish_reason","raw_response_hash","parse_status","validation_status","failure_code","sanitized_error","cost_status","currency","price_schedule_version","estimated_cost","prompt_hash")
        with self.connect() as c:c.execute(f"INSERT INTO ai_report_attempts({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",tuple(a.get(k) for k in cols))
    def save_registry_snapshot(self,snapshot:dict[str,Any])->dict[str,Any]:
        failures=validate_registry_snapshot(snapshot)
        if failures:raise ValueError(failures[0])
        with self.connect() as c:
            row=c.execute("SELECT registry_snapshot_id,fact_registry_hash,numeric_registry_hash,prompt_hash,source_versions_hash FROM ai_report_registry_snapshots WHERE request_id=?",(snapshot["request_id"],)).fetchone()
            if row:
                expected=(snapshot["registry_snapshot_id"],snapshot["fact_registry_hash"],snapshot["numeric_registry_hash"],snapshot["prompt_hash"],snapshot["source_versions_hash"])
                if tuple(row)!=expected:raise ValueError("REGISTRY_IDENTITY_CONFLICT")
                return self.load_registry_snapshot(registry_snapshot_id=row[0])
            c.execute("INSERT INTO ai_report_registry_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",snapshot_db_values(snapshot,utc_now()))
        return snapshot
    def load_registry_snapshot(self,*,registry_snapshot_id:str|None=None,request_id:str|None=None)->dict[str,Any]:
        if not registry_snapshot_id and not request_id:raise ValueError("snapshot identity required")
        field,value=("registry_snapshot_id",registry_snapshot_id) if registry_snapshot_id else ("request_id",request_id)
        with self.connect() as c:r=c.execute(f"SELECT * FROM ai_report_registry_snapshots WHERE {field}=?",(value,)).fetchone()
        if not r:raise KeyError("REGISTRY_SNAPSHOT_NOT_FOUND")
        out=dict(r);out["fact_registry"]=json.loads(out.pop("fact_registry_json"));out["numeric_registry"]=json.loads(out.pop("numeric_registry_json"));out["source_versions"]=json.loads(out.pop("source_versions_json"))
        out["identity_input"]={"snapshot_version":out["snapshot_version"],"enriched_context_id":out["enriched_context_id"],"fact_registry_hash":out["fact_registry_hash"],"numeric_registry_hash":out["numeric_registry_hash"],"prompt_hash":out["prompt_hash"],"fact_registry_version":out["fact_registry_version"],"numeric_registry_version":out["numeric_registry_version"],"source_versions_hash":out["source_versions_hash"]}
        return out
    def daily_tokens(self,instrument:str|None=None)->dict[str,int]:
        query="SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(total_tokens),0) FROM ai_report_attempts a JOIN ai_report_requests r ON r.request_id=a.request_id WHERE a.started_at>=?";args=[utc_now()[:10]+"T00:00:00Z"]
        if instrument:query+=" AND r.instrument=?";args.append(instrument)
        with self.connect() as c:r=c.execute(query,args).fetchone()
        return {"input":r[0],"output":r[1],"total":r[2]}
    def save_report(self,request:dict[str,Any],response:dict[str,Any],generated_text:str)->dict[str,Any]:
        payload=canonical_json(response);h=stable_hash(response);rid=report_identity(response)
        if len(payload.encode())>250_000:raise ValueError("REPORT_TOO_LARGE")
        with self.connect() as c:
            existing=c.execute("SELECT response_hash FROM ai_market_reports WHERE request_id=?",(request["request_id"],)).fetchone()
            if existing and existing[0]!=h:raise ValueError("REPORT_IMMUTABILITY_CONFLICT")
            c.execute("INSERT OR IGNORE INTO ai_market_reports VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(rid,request["request_id"],request["context_id"],request["mode"],request["language"],payload,h,request["model"],request["provider"],request["prompt_version"],generated_text,"PENDING",utc_now()))
        return {"report_id":rid,"response_hash":h,"audit_status":"PENDING"}
    def get_report(self,report_id:str|None=None,request_id:str|None=None)->dict[str,Any]|None:
        field,value=("report_id",report_id) if report_id else ("request_id",request_id)
        with self.connect() as c:r=c.execute(f"SELECT * FROM ai_market_reports WHERE {field}=?",(value,)).fetchone()
        if not r:return None
        out=dict(r);out["response"]=json.loads(out.pop("response_json"));return out
    def latest_report(self,instrument:str,mode:str|None=None,language:str="zh-CN")->dict[str,Any]|None:
        query="SELECT p.* FROM ai_market_reports p JOIN ai_report_requests r ON r.request_id=p.request_id WHERE r.instrument=? AND p.language=?";args=[instrument,language]
        if mode:query+=" AND p.mode=?";args.append(mode)
        query+=" ORDER BY p.created_at DESC LIMIT 1"
        with self.connect() as c:r=c.execute(query,args).fetchone()
        if not r:return None
        out=dict(r);out["response"]=json.loads(out.pop("response_json"));return out
