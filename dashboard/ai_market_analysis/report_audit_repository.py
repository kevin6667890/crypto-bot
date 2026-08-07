"""Explicitly migrated append-only AI-5 audit storage."""
from __future__ import annotations
import json,sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any,Iterator
from .canonical import canonical_json,identity,stable_hash
from .report_repository import ReportRepository,utc_now
from .versions import AI_REPORT_AUDIT_DB_VERSION,AI_REPORT_AUDIT_VERSION

MIGRATION_KEY="ai-report-audit-db-v1"
SQL_PATH=Path(__file__).resolve().parents[2]/"migrations/002_ai_report_audit.sql"
STRICT_SQL_PATH=Path(__file__).resolve().parents[2]/"migrations/003_ai_report_registry_snapshots_and_strict_audit.sql"
PRESENTATION_SQL_PATH=Path(__file__).resolve().parents[2]/"migrations/004_ai_shadow_presentation_indexes.sql"
STRICT_MIGRATION_KEY="ai-report-registry-snapshot-strict-audit-v1"
AUDIT_EVENTS=("AUDIT_QUEUED","AUDIT_RUNNING","AUDIT_PASSED","AUDIT_FAILED","AUDIT_ERROR","AUDIT_INTERRUPTED","AUDIT_CANCEL_REQUESTED","AUDIT_CANCELLED")

def migrate_audit_database(path:str|Path)->dict[str,Any]:
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    sql=SQL_PATH.read_text(encoding="utf-8");strict_sql=STRICT_SQL_PATH.read_text(encoding="utf-8");presentation_sql=PRESENTATION_SQL_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        with conn:
            conn.executescript(sql);conn.executescript(strict_sql);conn.executescript(presentation_sql)
            request_columns={r[1] for r in conn.execute("PRAGMA table_info(ai_report_requests)")}
            if "registry_snapshot_id" not in request_columns:conn.execute("ALTER TABLE ai_report_requests ADD COLUMN registry_snapshot_id TEXT")
            attempt_columns={r[1] for r in conn.execute("PRAGMA table_info(ai_report_attempts)")}
            if "prompt_hash" not in attempt_columns:conn.execute("ALTER TABLE ai_report_attempts ADD COLUMN prompt_hash TEXT")
            conn.execute("INSERT OR IGNORE INTO ai_report_migrations VALUES(?,?,?)",(MIGRATION_KEY,AI_REPORT_AUDIT_DB_VERSION,utc_now()))
            conn.execute("INSERT OR IGNORE INTO ai_report_migrations VALUES(?,?,?)",(STRICT_MIGRATION_KEY,AI_REPORT_AUDIT_VERSION,utc_now()))
    return {"schema_version":AI_REPORT_AUDIT_DB_VERSION,"database":str(path),"applied":True}

def freeze_report_bundle(repository:ReportRepository,report_id:str)->dict[str,Any]:
    report=repository.get_report(report_id=report_id)
    if not report:raise KeyError("report not found")
    with repository.connect() as conn:
        request=conn.execute("SELECT * FROM ai_report_requests WHERE request_id=?",(report["request_id"],)).fetchone()
    if not request:raise KeyError("request not found")
    context=repository.load_context(report["context_id"]);response=report["response"]
    snapshot_id=request["registry_snapshot_id"] if "registry_snapshot_id" in request.keys() else None
    if not snapshot_id:raise KeyError("REGISTRY_SNAPSHOT_NOT_FOUND")
    snapshot=repository.load_registry_snapshot(registry_snapshot_id=snapshot_id)
    with repository.connect() as conn:
        attempt=conn.execute("SELECT prompt_hash FROM ai_report_attempts WHERE request_id=? AND validation_status='VALID' ORDER BY attempt_number DESC LIMIT 1",(report["request_id"],)).fetchone()
    prompt_hash=attempt[0] if attempt else None
    return {"report_id":report_id,"report_hash":report["response_hash"],"report":response,"generated_text":report["generated_text"],
      "request_id":report["request_id"],"request":dict(request),"context_id":report["context_id"],"context_hash":stable_hash(context),"context":context,
      "registry_snapshot_id":snapshot_id,"fact_registry_hash":snapshot["fact_registry_hash"],"numeric_registry_hash":snapshot["numeric_registry_hash"],
      "registry_source_versions_hash":snapshot["source_versions_hash"],"prompt_hash":snapshot["prompt_hash"],
      "registry_snapshot":snapshot,"fact_registry":snapshot["fact_registry"],"numeric_registry":snapshot["numeric_registry"],"position_context":context["position_context"],
      "macro_evidence_set":context["macro_context"],"provider_metadata":{"provider":report["provider"],"model":report["model"]},
      "prompt_version":report["prompt_version"],"generation_prompt_hash":prompt_hash,"source_versions":response["source_versions"],
      "frozen_replay_provenance":{"report_lookup":"PRIMARY_KEY","request_lookup":"PRIMARY_KEY","context_lookup":"PRIMARY_KEY","registry_lookup":"PRIMARY_KEY","position_source":"FROZEN_CONTEXT","macro_source":"FROZEN_CONTEXT","external_market_databases_opened":False}}

class AuditRepository:
    def __init__(self,path:str|Path):self.path=Path(path)
    @contextmanager
    def connect(self)->Iterator[sqlite3.Connection]:
        conn=sqlite3.connect(self.path,timeout=5);conn.row_factory=sqlite3.Row;conn.execute("PRAGMA busy_timeout=5000")
        try:yield conn;conn.commit()
        except Exception:conn.rollback();raise
        finally:conn.close()
    def schema_version(self)->str|None:
        if not self.path.exists():return None
        with self.connect() as c:
            try:r=c.execute("SELECT schema_version FROM ai_report_migrations WHERE migration_key=?",(MIGRATION_KEY,)).fetchone()
            except sqlite3.OperationalError:return None
        return r[0] if r else None
    def freeze_input(self,bundle:dict[str,Any])->str:
        payload=canonical_json(bundle);h=stable_hash(bundle);iid=identity("audit_input",{"report_id":bundle["report_id"],"payload_hash":h})
        with self.connect() as c:
            row=c.execute("SELECT payload_hash FROM ai_report_audit_inputs WHERE report_id=?",(bundle["report_id"],)).fetchone()
            if row and row[0]!=h:raise ValueError("AUDIT_INPUT_IDENTITY_CONFLICT")
            c.execute("INSERT OR IGNORE INTO ai_report_audit_inputs VALUES(?,?,?,?,?)",(iid,bundle["report_id"],payload,h,utc_now()))
        return iid
    def load_input(self,report_id:str)->dict[str,Any]:
        with self.connect() as c:r=c.execute("SELECT payload_json FROM ai_report_audit_inputs WHERE report_id=?",(report_id,)).fetchone()
        if not r:raise KeyError("audit input not found")
        return json.loads(r[0])
    def event(self,audit_id:str,report_id:str,event_type:str,payload:dict[str,Any]|None=None)->None:
        if event_type not in AUDIT_EVENTS:raise ValueError("invalid audit event")
        with self.connect() as c:c.execute("INSERT INTO ai_report_audit_events(audit_id,report_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
          (audit_id,report_id,event_type,canonical_json(payload or {}),utc_now()))
    def latest_event(self,audit_id:str)->str|None:
        with self.connect() as c:r=c.execute("SELECT event_type FROM ai_report_audit_events WHERE audit_id=? ORDER BY event_id DESC LIMIT 1",(audit_id,)).fetchone()
        return r[0] if r else None
    def save_audit(self,audit:dict[str,Any])->dict[str,Any]:
        payload=canonical_json(audit);h=stable_hash({k:v for k,v in audit.items() if k!="created_at"})
        if len(payload.encode("utf-8"))>131072:raise ValueError("AUDIT_PAYLOAD_TOO_LARGE")
        with self.connect() as c:
            row=c.execute("SELECT payload_hash FROM ai_report_audits WHERE audit_id=?",(audit["audit_id"],)).fetchone()
            if row:
                if row[0]!=h:raise ValueError("AUDIT_IDENTITY_CONFLICT")
                return self.get_audit(audit["audit_id"])
            c.execute("INSERT INTO ai_report_audits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(audit["audit_id"],audit["report_id"],audit["request_id"],audit["context_id"],audit["report_hash"],audit["context_hash"],AI_REPORT_AUDIT_VERSION,audit["audit_policy_version"],audit["status"],audit["scorecard"]["overall"],int(audit["promotion_eligible"]),payload,h,audit["created_at"]))
        return audit
    def get_audit(self,audit_id:str)->dict[str,Any]:
        with self.connect() as c:r=c.execute("SELECT payload_json FROM ai_report_audits WHERE audit_id=?",(audit_id,)).fetchone()
        if not r:raise KeyError("audit not found")
        return json.loads(r[0])
    def latest(self,report_id:str)->dict[str,Any]|None:
        with self.connect() as c:r=c.execute("SELECT payload_json FROM ai_report_audits WHERE report_id=? ORDER BY created_at DESC,audit_id DESC LIMIT 1",(report_id,)).fetchone()
        return json.loads(r[0]) if r else None
    def queued(self,limit:int=1)->list[tuple[str,str]]:
        with self.connect() as c:rows=c.execute("SELECT e.audit_id,e.report_id FROM ai_report_audit_events e WHERE e.event_id=(SELECT MAX(x.event_id) FROM ai_report_audit_events x WHERE x.audit_id=e.audit_id) AND e.event_type IN ('AUDIT_QUEUED','AUDIT_INTERRUPTED') ORDER BY e.event_id LIMIT ?",(limit,)).fetchall()
        return [(r[0],r[1]) for r in rows]
    def interrupt_running(self)->int:
        with self.connect() as c:rows=c.execute("SELECT e.audit_id,e.report_id FROM ai_report_audit_events e WHERE e.event_id=(SELECT MAX(x.event_id) FROM ai_report_audit_events x WHERE x.audit_id=e.audit_id) AND e.event_type='AUDIT_RUNNING'").fetchall()
        for aid,rid in rows:self.event(aid,rid,"AUDIT_INTERRUPTED",{"reason":"worker_restart"})
        return len(rows)
    def query_plans(self)->dict[str,list[str]]:
        queries={"audit_by_id":("SELECT payload_json FROM ai_report_audits WHERE audit_id=?",("x",)),"latest":("SELECT payload_json FROM ai_report_audits WHERE report_id=? ORDER BY created_at DESC,audit_id DESC LIMIT 1",("x",)),"events":("SELECT event_type FROM ai_report_audit_events WHERE audit_id=? ORDER BY event_id",("x",))}
        with self.connect() as c:return {name:[str(tuple(r)) for r in c.execute("EXPLAIN QUERY PLAN "+sql,args)] for name,(sql,args) in queries.items()}
    def create_evaluation_run(self,manifest:dict[str,Any])->dict[str,Any]:
        from .report_evaluation import evaluation_identity
        run_id=manifest.get("evaluation_run_id") or evaluation_identity(manifest);payload=canonical_json(manifest);h=stable_hash(manifest)
        with self.connect() as c:
            row=c.execute("SELECT manifest_hash,status FROM ai_evaluation_runs WHERE evaluation_run_id=?",(run_id,)).fetchone()
            if row and row[0]!=h:raise ValueError("EVALUATION_IDENTITY_CONFLICT")
            c.execute("INSERT OR IGNORE INTO ai_evaluation_runs VALUES(?,?,?,?,?,?,?,?,?,?)",(run_id,manifest.get("evaluation_version","unknown"),manifest.get("audit_policy_version","unknown"),payload,h,"QUEUED",None,None,utc_now(),None))
        return {"evaluation_run_id":run_id,"status":row[1] if row else "QUEUED","created":not bool(row)}
    def get_evaluation_run(self,run_id:str)->dict[str,Any]:
        with self.connect() as c:r=c.execute("SELECT * FROM ai_evaluation_runs WHERE evaluation_run_id=?",(run_id,)).fetchone()
        if not r:raise KeyError("evaluation run not found")
        out=dict(r);out["manifest"]=json.loads(out.pop("manifest_json"));out["payload"]=json.loads(out.pop("payload_json")) if out.get("payload_json") else None;return out
