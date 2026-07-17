"""Sanitized operational health and structured rotating logging."""
from __future__ import annotations
import json, logging, os, shutil, sqlite3, subprocess, time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

def configure_logging(root:Path)->logging.Logger:
    logger=logging.getLogger("crypto_bot"); logger.setLevel(logging.INFO)
    if not logger.handlers:
        (root/"logs").mkdir(exist_ok=True); handler=RotatingFileHandler(root/"logs"/"operations.jsonl",maxBytes=5_000_000,backupCount=5,encoding="utf-8")
        handler.setFormatter(logging.Formatter('%(message)s')); logger.addHandler(handler)
    return logger

def log_event(logger:logging.Logger,level:str,component:str,event:str,**fields:Any)->None:
    allowed={k:v for k,v in fields.items() if k in {"instrument","job_id","run_id","signal_id","duration_ms","error_type"}}
    getattr(logger,level.lower(),logger.info)(json.dumps({"timestamp":datetime.now(timezone.utc).isoformat(),"level":level.upper(),"component":component,"event":event,**allowed},separators=(",",":")))

class HealthService:
    def __init__(self,db_path:Path,paper:Any,jobs:Any,alerts:Any,root:Path):
        self.db_path=Path(db_path); self.paper=paper; self.jobs=jobs; self.alerts=alerts; self.root=root; self.started=time.monotonic(); self.integrity_status="unknown"; self.integrity_at=None
        try:self.check_integrity()
        except Exception:self.integrity_status="error"
    def check_integrity(self)->str:
        with sqlite3.connect(self.db_path,timeout=10) as c:self.integrity_status=c.execute("PRAGMA integrity_check").fetchone()[0]
        self.integrity_at=datetime.now(timezone.utc).isoformat(); return self.integrity_status
    def _commit(self)->str:
        value=os.getenv("GIT_COMMIT","")
        if value:return value[:12]
        try:return subprocess.check_output(["git","rev-parse","--short=12","HEAD"],cwd=self.root,text=True,timeout=2,stderr=subprocess.DEVNULL).strip()
        except Exception:return "unknown"
    def payload(self,details:bool=False)->dict[str,Any]:
        db_status="ok"
        try:
            with sqlite3.connect(self.db_path,timeout=3) as c:c.execute("SELECT 1").fetchone()
        except Exception:db_status="error"
        if db_status=="error":self.alerts.raise_alert("Database Error","critical","database","SQLite health check failed",key="database-error")
        else:self.alerts.resolve("database-error")
        now=datetime.now(timezone.utc); freshness={}
        for asset,analysis in self.paper.last_analysis.items():
            updated=analysis.get("updated_at"); age=None
            try:age=(now-datetime.fromisoformat(updated)).total_seconds() if updated else None
            except Exception:pass
            failed=bool(analysis.get("error")) or analysis.get("status")=="Data unavailable"
            freshness[asset]={"updated_at":updated,"age_seconds":age,"status":"fresh" if age is not None and age<180 and not failed else "stale"}
            key=f"collector-stale|{asset}"
            if age is None or age>=180:self.alerts.raise_alert("Collector Stale","warning","collector",f"{asset} collector has no update within 180 seconds",asset,key=key)
            else:self.alerts.resolve(key)
        jobs=self.jobs.list(100); active=next((j for j in jobs if j["status"] in {"RUNNING","CANCEL_REQUESTED"}),None); queued=sum(j["status"]=="QUEUED" for j in jobs)
        disk=shutil.disk_usage(self.root); memory={"used_bytes":None,"total_bytes":None,"percent":None}
        disk_key="disk-space-low"
        if disk.free/disk.total<.1:self.alerts.raise_alert("Disk Space Low","critical","system",f"Only {disk.free/disk.total*100:.1f}% disk space remains",key=disk_key)
        else:self.alerts.resolve(disk_key)
        try:
            values={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line}
            memory={"used_bytes":values['MemTotal']-values.get('MemAvailable',0),"total_bytes":values['MemTotal'],"percent":round((values['MemTotal']-values.get('MemAvailable',0))/values['MemTotal']*100,1)}
        except Exception:pass
        status="unhealthy" if db_status!="ok" else "degraded" if any(v["status"]=="stale" for v in freshness.values()) else "healthy"
        result={"status":status,"version":"3.0.0","git_commit":self._commit(),"uptime_seconds":int(time.monotonic()-self.started),"database_status":db_status,"database_size_bytes":self.db_path.stat().st_size if self.db_path.exists() else 0,"database_integrity_last_checked":self.integrity_at,"database_integrity_status":self.integrity_status,"paper_scheduler_running":bool(getattr(self.paper,"scheduler_running",False)),"last_cycle_started_at":getattr(self.paper,"last_cycle_started_at",None),"last_cycle_completed_at":getattr(self.paper,"last_cycle_completed_at",None),"last_cycle_duration_ms":getattr(self.paper,"last_cycle_duration_ms",None),"next_cycle_at":getattr(self.paper,"next_cycle_at",None),"collector_freshness":freshness,"last_okx_success":getattr(self.paper,"last_okx_success",None),"last_okx_error":getattr(self.paper,"last_okx_error",None),"active_job":active,"queued_jobs":queued,"deepseek_configured":bool(os.getenv("DEEPSEEK_API_KEY")),"last_ai_success":getattr(self.paper,"last_ai_success",None),"disk_usage":{"used_bytes":disk.used,"total_bytes":disk.total,"percent":round(disk.used/disk.total*100,1)},"memory_usage":memory}
        return result if details else {k:v for k,v in result.items() if k not in {"last_okx_error"}}
