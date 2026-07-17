"""Single-worker persistent SQLite queue for resource-heavy research jobs."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class JobCancelled(Exception):
    pass


class JobQueue:
    def __init__(self, db_path: Path, max_queue: int = 10, autostart: bool = True) -> None:
        import sqlite3
        self.sqlite3, self.db_path, self.max_queue = sqlite3, Path(db_path), max_queue
        self.handlers: dict[str, Callable[..., Any]] = {}
        self._stop = threading.Event()
        self._init_db()
        self.worker = threading.Thread(target=self._loop, daemon=True, name="research-job-worker")
        if autostart: self.worker.start()

    def connect(self):
        conn = self.sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = self.sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000"); conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS research_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 100,
                request_payload TEXT NOT NULL, request_fingerprint TEXT NOT NULL, requester_key TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                progress_message TEXT, result_ref TEXT, error TEXT, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                cancelled_at TEXT, retry_of_job_id INTEGER)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_queue ON research_jobs(status,priority,id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON research_jobs(requester_key,request_fingerprint,status)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(research_jobs)")}
            if "message_code" not in columns:
                conn.execute("ALTER TABLE research_jobs ADD COLUMN message_code TEXT")
            if "message_params" not in columns:
                conn.execute("ALTER TABLE research_jobs ADD COLUMN message_params TEXT")
            conn.execute("""UPDATE research_jobs
                SET status='INTERRUPTED', error='Service restarted while job was running',
                    progress_message='Service restarted while job was running',
                    message_code='job.interrupted.restart', message_params='{}', completed_at=?
                WHERE status IN ('RUNNING','CANCEL_REQUESTED')""", (utc_now(),))

    def register(self, job_type: str, handler: Callable[..., Any]) -> None: self.handlers[job_type] = handler

    def find_active(self,job_type:str,payload:dict[str,Any],requester_key:str)->dict[str,Any]|None:
        fingerprint=self.fingerprint(job_type,payload)
        with self.connect() as conn:row=conn.execute("SELECT * FROM research_jobs WHERE requester_key=? AND request_fingerprint=? AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED') ORDER BY id DESC LIMIT 1",(requester_key,fingerprint)).fetchone()
        return self._row(row,True) if row else None

    @staticmethod
    def fingerprint(job_type: str, payload: dict[str, Any]) -> str:
        raw = json.dumps([job_type, payload], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def enqueue(self, job_type: str, payload: dict[str, Any], requester_key: str = "public", priority: int = 100, retry_of: int | None = None, dedupe_payload: dict[str,Any] | None = None) -> dict[str, Any]:
        fingerprint = self.fingerprint(job_type, dedupe_payload if dedupe_payload is not None else payload)
        with self.connect() as conn:
            active = conn.execute("SELECT * FROM research_jobs WHERE requester_key=? AND request_fingerprint=? AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED') ORDER BY id DESC LIMIT 1", (requester_key, fingerprint)).fetchone()
            if active: return self._row(active, deduplicated=True)
            queued = conn.execute("SELECT COUNT(*) FROM research_jobs WHERE status='QUEUED'").fetchone()[0]
            if queued >= self.max_queue: raise OverflowError("Research job queue is full.")
            cursor = conn.execute("INSERT INTO research_jobs(job_type,status,priority,request_payload,request_fingerprint,requester_key,progress,progress_message,message_code,message_params,created_at,retry_of_job_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (job_type,"QUEUED",priority,json.dumps(payload),fingerprint,requester_key,0,"Queued","job.queued","{}",utc_now(),retry_of))
            row = conn.execute("SELECT * FROM research_jobs WHERE id=?", (cursor.lastrowid,)).fetchone()
            return self._row(row)

    @staticmethod
    def _row(row: Any, deduplicated: bool = False) -> dict[str, Any]:
        item = dict(row); item["request_payload"] = json.loads(item["request_payload"]); item["deduplicated"] = deduplicated
        raw_params = item.get("message_params")
        try: item["message_params"] = json.loads(raw_params) if raw_params else {}
        except (TypeError, json.JSONDecodeError): item["message_params"] = {}
        return item

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM research_jobs ORDER BY id DESC LIMIT ?", (min(max(limit,1),200),)).fetchall()
            queued_ids = [r[0] for r in conn.execute("SELECT id FROM research_jobs WHERE status='QUEUED' ORDER BY priority,id")]
        items = [self._row(r) for r in rows]
        for item in items: item["queue_position"] = queued_ids.index(item["id"]) + 1 if item["id"] in queued_ids else None
        return items

    def get(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as conn: row = conn.execute("SELECT * FROM research_jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def cancel(self, job_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM research_jobs WHERE id=?", (job_id,)).fetchone()
            if not row: raise ValueError("Job not found.")
            if row[0] == "QUEUED": conn.execute("UPDATE research_jobs SET status='CANCELLED',cancelled_at=?,completed_at=?,progress_message='Cancelled',message_code='job.cancelled',message_params='{}' WHERE id=?", (utc_now(),utc_now(),job_id))
            elif row[0] == "RUNNING": conn.execute("UPDATE research_jobs SET status='CANCEL_REQUESTED',progress_message='Cancellation requested',message_code='job.cancellation_requested',message_params='{}' WHERE id=?", (job_id,))
        return self.get(job_id) or {}

    def retry(self, job_id: int, requester_key: str = "public") -> dict[str, Any]:
        job = self.get(job_id)
        if not job or job["status"] not in {"FAILED","CANCELLED","INTERRUPTED"}: raise ValueError("Only failed, cancelled or interrupted jobs can be retried.")
        return self.enqueue(job["job_type"], job["request_payload"], requester_key, job["priority"], job_id)

    def checkpoint(self, job_id: int, progress: int | None = None, message: str | None = None,
                   message_params: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT status,progress FROM research_jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row[0] in {"CANCEL_REQUESTED","CANCELLED"}: raise JobCancelled()
            if progress is not None or message is not None:
                next_progress = int(row[1] or 0) if progress is None else max(int(row[1] or 0), max(0, min(99, int(progress))))
                is_code = bool(message and "." in message and " " not in message)
                legacy = message if not is_code else None
                conn.execute("UPDATE research_jobs SET progress=?,progress_message=COALESCE(?,progress_message),message_code=COALESCE(?,message_code),message_params=? WHERE id=?",
                             (next_progress, legacy, message if is_code else None, json.dumps(message_params or {}), job_id))

    def _loop(self) -> None:
        while not self._stop.is_set():
            claimed=False
            with self.connect() as conn:
                row = conn.execute("SELECT * FROM research_jobs WHERE status='QUEUED' ORDER BY priority,id LIMIT 1").fetchone()
                if row: claimed=conn.execute("UPDATE research_jobs SET status='RUNNING',started_at=?,progress=2,progress_message='Starting',message_code='job.initializing',message_params='{}' WHERE id=? AND status='QUEUED'", (utc_now(),row["id"])).rowcount==1
            if not row or not claimed: self._stop.wait(0.25); continue
            job = self._row(row); handler = self.handlers.get(job["job_type"])
            try:
                if not handler: raise RuntimeError(f"No handler registered for {job['job_type']}")
                result_ref = handler(job["id"], job["request_payload"], self.checkpoint)
                completed_code = "portfolio.progress.completed" if job["job_type"] == "PORTFOLIO_BACKTEST" else "job.completed"
                with self.connect() as conn: conn.execute("UPDATE research_jobs SET status='COMPLETED',progress=100,progress_message='Completed',message_code=?,message_params='{}',result_ref=?,completed_at=? WHERE id=?", (completed_code,json.dumps(result_ref),utc_now(),job["id"]))
            except JobCancelled:
                with self.connect() as conn: conn.execute("UPDATE research_jobs SET status='CANCELLED',progress_message='Cancelled',message_code='job.cancelled',message_params='{}',cancelled_at=?,completed_at=? WHERE id=?", (utc_now(),utc_now(),job["id"]))
            except Exception as error:
                with self.connect() as conn: conn.execute("UPDATE research_jobs SET status='FAILED',progress_message='Failed',message_code='job.failed',message_params=?,error=?,completed_at=? WHERE id=?", (json.dumps({"error":str(error)[:1000]}),str(error)[:1000],utc_now(),job["id"]))

    def cleanup(self, older_than_days: int = 30) -> int:
        with self.connect() as conn:
            cur=conn.execute("DELETE FROM research_jobs WHERE status IN ('COMPLETED','FAILED','CANCELLED','INTERRUPTED') AND completed_at < datetime('now',?)", (f"-{max(1,older_than_days)} days",)); return cur.rowcount
