"""Explicitly migrated, isolated and immutable SQLite report repository."""
from __future__ import annotations
import json, sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from .canonical import canonical_json, stable_hash
from .report_identity import report_identity
from .report_registry_snapshot import snapshot_db_values, validate_registry_snapshot
from .versions import AI_REPORT_DATABASE_SCHEMA_VERSION, AI_REPORT_REPOSITORY_VERSION
from .report_migrations import apply_migrations

DEFAULT_REPORT_DB=Path("data_cache/ai_market_reports.db")
MIGRATION_KEY="ai-market-report-db-v1"
MAX_CONTEXT_BYTES=524_288
MAX_POSITION_PLAN_BYTES=131_072
MAX_MACRO_SET_BYTES=131_072
MAX_REGISTRY_SNAPSHOT_BYTES=1_048_576
MAX_REPORT_JSON_BYTES=250_000
MAX_GENERATED_TEXT_BYTES=250_000
MAX_ATTEMPT_DIAGNOSTIC_BYTES=1_000_000

def utc_now()->str: return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def _future_utc(seconds:int)->str:
    return (datetime.now(timezone.utc)+timedelta(seconds=max(1,int(seconds)))).replace(
        microsecond=0).isoformat().replace("+00:00","Z")

def migrate_database(path: str|Path)->dict[str,Any]:
    return apply_migrations(path)


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
        if len(payload.encode("utf-8"))>MAX_CONTEXT_BYTES:raise ValueError("CONTEXT_TOO_LARGE")
        with self.connect() as c:
            row=c.execute("SELECT payload_hash FROM ai_market_contexts WHERE context_id=?",(context["enriched_context_id"],)).fetchone()
            if row and row[0]!=h: raise ValueError("CONTEXT_IDENTITY_CONFLICT")
            c.execute("INSERT OR IGNORE INTO ai_market_contexts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(context["enriched_context_id"],context["base_context_id"],context["enriched_context_version"],context["instrument"],context["decision_time"],context["position_fingerprint"],context["macro_evidence_set_id"],payload,h,canonical_json(context["source_versions"]),context["base_context"].get("data_quality",{}).get("overall","UNKNOWN"),utc_now()))
    def load_context(self,context_id:str)->dict[str,Any]:
        with self.connect() as c:r=c.execute("SELECT payload_json FROM ai_market_contexts WHERE context_id=?",(context_id,)).fetchone()
        if not r: raise KeyError("context not found")
        return json.loads(r[0])
    def save_position_plan(self,plan:dict[str,Any])->None:
        if len(canonical_json(plan).encode("utf-8"))>MAX_POSITION_PLAN_BYTES:raise ValueError("POSITION_PLAN_TOO_LARGE")
        with self.connect() as c:
            c.execute("INSERT INTO ai_position_plans VALUES(?,?,?,?,?,?,?,?,?,?)",(plan["plan_id"],plan["plan_version"],plan["instrument"],plan["source"],plan["effective_at"],plan.get("supersedes_plan_id"),plan["status"],canonical_json(plan),plan["payload_hash"],utc_now()))
    def load_position_plan(self,plan_id:str)->dict[str,Any]:
        with self.connect() as c:r=c.execute("SELECT payload_json FROM ai_position_plans WHERE plan_id=?",(plan_id,)).fetchone()
        if not r:raise KeyError("plan not found")
        return json.loads(r[0])
    def save_macro_set(self,value:dict[str,Any])->None:
        payload=canonical_json(value);h=stable_hash(value)
        if len(payload.encode("utf-8"))>MAX_MACRO_SET_BYTES:raise ValueError("MACRO_SET_TOO_LARGE")
        with self.connect() as c:c.execute("INSERT OR IGNORE INTO ai_macro_evidence_sets VALUES(?,?,?,?,?,?,?,?)",(value["evidence_set_id"],value["decision_time"],len(value["items"]),canonical_json(sorted({i["category"] for i in value["items"]})),payload,h,value["quality"],utc_now()))
    def load_macro_set(self,evidence_set_id:str)->dict[str,Any]:
        with self.connect() as c:r=c.execute("SELECT payload_json FROM ai_macro_evidence_sets WHERE evidence_set_id=?",(evidence_set_id,)).fetchone()
        if not r:raise KeyError("macro evidence set not found")
        return json.loads(r[0])
    def create_request(self,value:dict[str,Any])->tuple[dict[str,Any],bool]:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT * FROM ai_report_requests WHERE request_identity=?",(value["request_identity"],)).fetchone()
            if row:return dict(row),False
            active=c.execute("SELECT COUNT(*) FROM ai_report_requests r WHERE (SELECT event_type FROM ai_report_request_events e WHERE e.request_id=r.request_id ORDER BY event_id DESC LIMIT 1) IN ('QUEUED','RUNNING','RETRY_SCHEDULED','INTERRUPTED')").fetchone()[0]
            if active>=int(__import__('os').getenv("AI_REPORT_QUEUE_MAX","10")):raise OverflowError("AI report queue is full")
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
    def claim_queued(self,owner:str,*,lease_seconds:int=3600,global_limit:int=1)->dict[str,Any]|None:
        """Atomically claim one request across worker processes.

        The provider send boundary remains separately fenced by the unique
        ``(request_id, attempt_number)`` constraint.
        """
        now,until=utc_now(),_future_utc(lease_seconds)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            active=c.execute(
                "SELECT instrument FROM ai_report_requests WHERE worker_claim_until>?",
                (now,),
            ).fetchall()
            if len(active)>=max(1,int(global_limit)):return None
            active_instruments={str(row[0]) for row in active}
            rows=c.execute(
                "SELECT r.* FROM ai_report_requests r JOIN ai_report_request_events e "
                "ON e.event_id=(SELECT MAX(e2.event_id) FROM ai_report_request_events e2 WHERE e2.request_id=r.request_id) "
                "WHERE e.event_type IN ('QUEUED','RETRY_SCHEDULED','INTERRUPTED') "
                "AND (r.worker_claim_until IS NULL OR r.worker_claim_until<=?) ORDER BY r.created_at,r.request_id",
                (now,),
            ).fetchall()
            selected=next((row for row in rows if str(row["instrument"]) not in active_instruments),None)
            if selected is None:return None
            changed=c.execute(
                "UPDATE ai_report_requests SET worker_claim_owner=?,worker_claim_until=? "
                "WHERE request_id=? AND (worker_claim_until IS NULL OR worker_claim_until<=?)",
                (owner,until,selected["request_id"],now),
            ).rowcount
            if changed!=1:return None
            return dict(selected)|{"worker_claim_owner":owner,"worker_claim_until":until}
    def renew_claim(self,request_id:str,owner:str,*,lease_seconds:int=3600)->bool:
        with self.connect() as c:
            changed=c.execute(
                "UPDATE ai_report_requests SET worker_claim_until=? WHERE request_id=? AND worker_claim_owner=?",
                (_future_utc(lease_seconds),request_id,owner),
            ).rowcount
        return changed==1
    def release_claim(self,request_id:str,owner:str)->bool:
        with self.connect() as c:
            changed=c.execute(
                "UPDATE ai_report_requests SET worker_claim_owner=NULL,worker_claim_until=NULL "
                "WHERE request_id=? AND worker_claim_owner=?",(request_id,owner),
            ).rowcount
        return changed==1
    def interrupt_expired_running(self)->int:
        """Recover only ownerless/expired RUNNING work; active peers stay untouched."""
        now=utc_now()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            rows=c.execute(
                "SELECT r.request_id FROM ai_report_requests r JOIN ai_report_request_events e "
                "ON e.event_id=(SELECT MAX(e2.event_id) FROM ai_report_request_events e2 WHERE e2.request_id=r.request_id) "
                "WHERE e.event_type='RUNNING' AND (r.worker_claim_until IS NULL OR r.worker_claim_until<=?)",
                (now,),
            ).fetchall()
            for row in rows:
                c.execute(
                    "INSERT INTO ai_report_request_events(request_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                    (row[0],"INTERRUPTED",canonical_json({"reason":"expired_worker_lease"}),now),
                )
                c.execute(
                    "UPDATE ai_report_requests SET worker_claim_owner=NULL,worker_claim_until=NULL WHERE request_id=?",
                    (row[0],),
                )
        return len(rows)
    def interrupt_running(self)->int:
        with self.connect() as c:rows=c.execute("SELECT DISTINCT request_id FROM ai_report_request_events e WHERE event_type='RUNNING' AND event_id=(SELECT MAX(e2.event_id) FROM ai_report_request_events e2 WHERE e2.request_id=e.request_id)").fetchall()
        for r in rows:self.event(r[0],"INTERRUPTED",{"reason":"worker_restart"})
        return len(rows)
    def attempt_count(self,request_id:str)->int:
        with self.connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM ai_report_attempts WHERE request_id=?",(request_id,)).fetchone()[0])

    def claim_generation_decision(self,*,instrument:str,confirmed_4h_close:str,
                                  material_fingerprint:str,fingerprint_version:str,
                                  canonical_snapshot_identity:str|None,facts_as_of:str,
                                  projection:dict[str,Any],owner:str,
                                  lease_seconds:int=600)->dict[str,Any]:
        """Persist and single-flight one automatic boundary evaluation."""
        now,until=utc_now(),_future_utc(lease_seconds)
        decision_id="generation_"+stable_hash({"instrument":instrument,"confirmed_4h_close":confirmed_4h_close})
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            existing=c.execute(
                "SELECT * FROM ai_report_generation_decisions WHERE instrument=? AND confirmed_4h_close=?",
                (instrument,confirmed_4h_close),
            ).fetchone()
            if existing:
                value=dict(existing)
                reclaimable=value["outcome"] in {"EVALUATING","ERROR"} and (
                    not value.get("claim_until") or value["claim_until"]<=now)
                if reclaimable:
                    c.execute(
                        "UPDATE ai_report_generation_decisions SET outcome='EVALUATING',claim_owner=?,claim_until=?,updated_at=? "
                        "WHERE decision_id=? AND outcome IN ('EVALUATING','ERROR') AND (claim_until IS NULL OR claim_until<=?)",
                        (owner,until,now,value["decision_id"],now),
                    )
                    value.update({"outcome":"EVALUATING","claim_owner":owner,"claim_until":until,
                                  "updated_at":now,"claimed":True,"should_generate":True})
                    return value
                return value|{"claimed":False,"should_generate":value["outcome"]=="EVALUATING"}
            previous=c.execute(
                "SELECT material_fingerprint,confirmed_4h_close,outcome FROM ai_report_generation_decisions "
                "WHERE instrument=? AND outcome IN ('EVALUATING','QUEUED','ERROR') "
                "ORDER BY confirmed_4h_close DESC LIMIT 1",(instrument,),
            ).fetchone()
            previous_fingerprint=str(previous[0]) if previous else None
            changed=previous_fingerprint is None or previous_fingerprint!=material_fingerprint
            outcome="EVALUATING" if changed else "SKIPPED_NO_MATERIAL_CHANGE"
            detail={"previous_material_fingerprint":previous_fingerprint,"material_projection":projection}
            c.execute(
                "INSERT INTO ai_report_generation_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id,instrument,confirmed_4h_close,material_fingerprint,fingerprint_version,
                 canonical_snapshot_identity,facts_as_of,outcome,None,owner if changed else None,
                 until if changed else None,canonical_json(detail),now,now),
            )
        return {"decision_id":decision_id,"instrument":instrument,
                "confirmed_4h_close":confirmed_4h_close,"material_fingerprint":material_fingerprint,
                "material_fingerprint_version":fingerprint_version,
                "canonical_snapshot_identity":canonical_snapshot_identity,"facts_as_of":facts_as_of,
                "outcome":outcome,"request_id":None,"claim_owner":owner if changed else None,
                "claim_until":until if changed else None,"detail_json":canonical_json(detail),
                "created_at":now,"updated_at":now,"claimed":changed,"should_generate":changed}
    def complete_generation_decision(self,decision_id:str,owner:str,*,outcome:str,
                                     request_id:str|None=None,detail:dict[str,Any]|None=None,
                                     retry_seconds:int|None=None)->bool:
        if outcome not in {"QUEUED","ERROR"}:raise ValueError("invalid generation decision outcome")
        until=_future_utc(retry_seconds) if retry_seconds else None
        with self.connect() as c:
            row=c.execute(
                "SELECT detail_json FROM ai_report_generation_decisions WHERE decision_id=? AND claim_owner=?",
                (decision_id,owner),
            ).fetchone()
            merged=json.loads(row[0] or "{}") if row else {}
            merged.update(detail or {})
            changed=c.execute(
                "UPDATE ai_report_generation_decisions SET outcome=?,request_id=?,claim_owner=NULL,claim_until=?,"
                "detail_json=?,updated_at=? WHERE decision_id=? AND outcome='EVALUATING' AND claim_owner=?",
                (outcome,request_id,until,canonical_json(merged),utc_now(),decision_id,owner),
            ).rowcount
        return changed==1
    def latest_generation_decision(self,instrument:str|None=None)->dict[str,Any]|None:
        query="SELECT * FROM ai_report_generation_decisions"
        args:list[Any]=[]
        if instrument:
            query+=" WHERE instrument=?";args.append(instrument)
        query+=" ORDER BY confirmed_4h_close DESC,instrument LIMIT 1"
        with self.connect() as c:row=c.execute(query,args).fetchone()
        if not row:return None
        value=dict(row);value["detail"]=json.loads(value.pop("detail_json") or "{}")
        return value
    def cancel(self,request_id:str)->str:
        status=self.status(request_id)["status"]
        if status in {"COMPLETED","FAILED_FINAL","CANCELLED"}:return status
        self.event(request_id,"CANCEL_REQUESTED",{});self.event(request_id,"CANCELLED",{});return "CANCELLED"
    def save_attempt(self,a:dict[str,Any])->None:
        cols=("attempt_id","request_id","attempt_number","provider","model","started_at","completed_at","latency_ms","http_status","input_tokens","output_tokens","total_tokens","finish_reason","raw_response_hash","parse_status","validation_status","failure_code","sanitized_error","cost_status","currency","price_schedule_version","estimated_cost","prompt_hash","lifecycle_state","charge_state")
        with self.connect() as c:
            c.execute(f"INSERT INTO ai_report_attempts({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",tuple(a.get(k) for k in cols))

    def update_attempt(self,attempt_id:str,values:dict[str,Any])->None:
        """Progressively update a persisted attempt identity. Identity columns are immutable."""
        mutable=("completed_at","latency_ms","http_status","input_tokens","output_tokens","total_tokens",
                 "finish_reason","raw_response_hash","parse_status","validation_status","failure_code",
                 "sanitized_error","lifecycle_state","charge_state")
        keys=[k for k in mutable if k in values]
        if not keys:return
        with self.connect() as c:
            c.execute(f"UPDATE ai_report_attempts SET {','.join(k+'=?' for k in keys)} WHERE attempt_id=?",
                      (*[values[k] for k in keys],attempt_id))

    def save_attempt_diagnostic(self,attempt_id:str,request_id:str,raw_response_hash:str,diagnostic:dict[str,Any])->None:
        payloads=(
            str(diagnostic["sanitized_raw_response"]),
            canonical_json(diagnostic["normalized_response"]) if diagnostic.get("normalized_response") is not None else None,
            canonical_json(diagnostic.get("parse_diagnostic") or {}),
            canonical_json(diagnostic.get("validation_diagnostic") or {}),
        )
        if sum(len(value.encode("utf-8")) for value in payloads if value is not None)>MAX_ATTEMPT_DIAGNOSTIC_BYTES:
            raise ValueError("ATTEMPT_DIAGNOSTIC_TOO_LARGE")
        with self.connect() as c:
            c.execute(
                "INSERT INTO ai_report_attempt_diagnostics VALUES(?,?,?,?,?,?,?,?,?)",
                (attempt_id,request_id,raw_response_hash or "UNKNOWN",*payloads,
                 str(diagnostic["sanitizer_version"]),utc_now()),
            )

    def attempt_diagnostic(self,attempt_id:str)->dict[str,Any]|None:
        with self.connect() as c:
            row=c.execute("SELECT * FROM ai_report_attempt_diagnostics WHERE attempt_id=?",(attempt_id,)).fetchone()
        if not row:return None
        value=dict(row)
        for name in ("normalized_response_json","parse_diagnostic_json","validation_diagnostic_json"):
            if value.get(name) is not None:value[name.removesuffix("_json")]=json.loads(value.pop(name))
        return value
    def save_registry_snapshot(self,snapshot:dict[str,Any])->dict[str,Any]:
        failures=validate_registry_snapshot(snapshot)
        if failures:raise ValueError(failures[0])
        if len(canonical_json(snapshot).encode("utf-8"))>MAX_REGISTRY_SNAPSHOT_BYTES:raise ValueError("REGISTRY_SNAPSHOT_TOO_LARGE")
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
    def daily_tokens(self,instrument:str|None=None,*,chargeable_only:bool=False)->dict[str,int]:
        query="SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(total_tokens),0) FROM ai_report_attempts a JOIN ai_report_requests r ON r.request_id=a.request_id WHERE a.started_at>=?";args=[utc_now()[:10]+"T00:00:00Z"]
        if chargeable_only:query+=" AND LOWER(a.provider) NOT IN ('fake','mock','local','dry-run','dry_run','test')"
        if instrument:query+=" AND r.instrument=?";args.append(instrument)
        with self.connect() as c:r=c.execute(query,args).fetchone()
        return {"input":r[0],"output":r[1],"total":r[2]}
    def daily_live_provider_usage(self)->dict[str,Any]:
        start=utc_now()[:10]+"T00:00:00Z"
        with self.connect() as c:
            row=c.execute("SELECT COUNT(*),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(total_tokens),0),COALESCE(SUM(estimated_cost),0),SUM(CASE WHEN cost_status!='AUDITED' THEN 1 ELSE 0 END) FROM ai_report_attempts WHERE started_at>=? AND LOWER(provider) NOT IN ('fake','mock','local','dry-run','dry_run','test')",(start,)).fetchone()
        return {"calls":int(row[0]),"input":int(row[1]),"output":int(row[2]),"total":int(row[3]),"estimated_cost":float(row[4]),"unaudited_cost_attempts":int(row[5] or 0)}
    def save_report(self,request:dict[str,Any],response:dict[str,Any],generated_text:str)->dict[str,Any]:
        payload=canonical_json(response);h=stable_hash(response);rid=report_identity(response)
        if len(payload.encode())>MAX_REPORT_JSON_BYTES or len(generated_text.encode("utf-8"))>MAX_GENERATED_TEXT_BYTES:raise ValueError("REPORT_TOO_LARGE")
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
