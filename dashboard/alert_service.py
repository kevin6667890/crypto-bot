"""Persistent deduplicated in-app alerts."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
class AlertService:
    def __init__(self, db_path: Path): self.db_path=Path(db_path); self._init()
    def connect(self):
        c=sqlite3.connect(self.db_path,timeout=30); c.row_factory=sqlite3.Row; c.execute("PRAGMA busy_timeout=30000"); return c
    def _init(self):
        with self.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS system_alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, alert_key TEXT NOT NULL UNIQUE,
            alert_type TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL,component TEXT NOT NULL,instrument TEXT,message TEXT NOT NULL,
            first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,occurrence_count INTEGER NOT NULL DEFAULT 1,related_job_id INTEGER,related_signal_id TEXT,acknowledged_at TEXT,resolved_at TEXT,message_code TEXT,message_params TEXT)""")
            columns={row[1] for row in c.execute("PRAGMA table_info(system_alerts)")}
            if "message_code" not in columns:c.execute("ALTER TABLE system_alerts ADD COLUMN message_code TEXT")
            if "message_params" not in columns:c.execute("ALTER TABLE system_alerts ADD COLUMN message_params TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS idx_alert_status ON system_alerts(status,severity,last_seen DESC)")
    def raise_alert(self, alert_type:str,severity:str,component:str,message:str,instrument:str|None=None,related_job_id:int|None=None,related_signal_id:str|None=None,key:str|None=None,message_code:str|None=None,message_params:dict[str,Any]|None=None)->dict[str,Any]:
        alert_key=key or "|".join((alert_type,component,instrument or "",str(related_job_id or ""))); stamp=now()
        with self.connect() as c:
            c.execute("""INSERT INTO system_alerts(alert_key,alert_type,severity,status,component,instrument,message,first_seen,last_seen,related_job_id,related_signal_id,message_code,message_params)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(alert_key) DO UPDATE SET severity=excluded.severity,status='open',message=excluded.message,last_seen=excluded.last_seen,
            occurrence_count=system_alerts.occurrence_count+1,related_signal_id=excluded.related_signal_id,message_code=excluded.message_code,message_params=excluded.message_params,resolved_at=NULL""",(alert_key,alert_type,severity,"open",component,instrument,message,stamp,stamp,related_job_id,related_signal_id,message_code,json.dumps(message_params or {})))
            row=c.execute("SELECT * FROM system_alerts WHERE alert_key=?",(alert_key,)).fetchone(); return dict(row)
    def resolve(self,key:str)->bool:
        with self.connect() as c: return c.execute("UPDATE system_alerts SET status='resolved',resolved_at=?,last_seen=? WHERE alert_key=? AND status!='resolved'",(now(),now(),key)).rowcount>0
    def acknowledge(self,alert_id:int)->bool:
        with self.connect() as c: return c.execute("UPDATE system_alerts SET status='acknowledged',acknowledged_at=? WHERE id=? AND status='open'",(now(),alert_id)).rowcount>0
    def list(self,limit:int=100)->list[dict[str,Any]]:
        with self.connect() as c: rows=[dict(r) for r in c.execute("SELECT * FROM system_alerts ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,last_seen DESC LIMIT ?",(min(limit,200),))]
        for row in rows:
            try:row["message_params"]=json.loads(row.get("message_params") or "{}")
            except json.JSONDecodeError:row["message_params"]={}
        return rows
