"""Phase 4 SQLite schema and bounded persistence helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from signal_identity import config_hash
    from strategy_rules import validate_parameters
except ImportError:
    from .signal_identity import config_hash
    from .strategy_rules import validate_parameters


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ValidationRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path); self.db_path.parent.mkdir(parents=True, exist_ok=True); self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30); connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000"); connection.execute("PRAGMA journal_mode=WAL"); connection.execute("PRAGMA foreign_keys=ON")
        try: yield connection; connection.commit()
        except Exception: connection.rollback(); raise
        finally: connection.close()

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() and column not in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def initialize(self) -> None:
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS gate_analysis_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,status TEXT NOT NULL,filters TEXT NOT NULL,summary TEXT,error TEXT,created_at TEXT NOT NULL,completed_at TEXT);
            CREATE TABLE IF NOT EXISTS gate_analysis_results(run_id INTEGER NOT NULL,gate_key TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,gate_key),FOREIGN KEY(run_id) REFERENCES gate_analysis_runs(id));
            CREATE TABLE IF NOT EXISTS near_miss_signals(id INTEGER PRIMARY KEY AUTOINCREMENT,signal_id TEXT NOT NULL UNIQUE,instrument TEXT NOT NULL,candle_close_ts INTEGER NOT NULL,strategy_version TEXT NOT NULL,config_hash TEXT NOT NULL,bias TEXT NOT NULL,score REAL NOT NULL,score_gap REAL NOT NULL,regime TEXT,payload TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS near_miss_outcomes(id INTEGER PRIMARY KEY AUTOINCREMENT,near_miss_id INTEGER NOT NULL UNIQUE,status TEXT NOT NULL,payload TEXT NOT NULL,evaluated_at TEXT NOT NULL,FOREIGN KEY(near_miss_id) REFERENCES near_miss_signals(id));
            CREATE TABLE IF NOT EXISTS sensitivity_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,status TEXT NOT NULL,request TEXT NOT NULL,strategy_version TEXT,config_hash TEXT,result_summary TEXT,error TEXT,created_at TEXT NOT NULL,completed_at TEXT);
            CREATE TABLE IF NOT EXISTS sensitivity_results(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL,parameters TEXT NOT NULL,metrics TEXT NOT NULL,stability_score REAL,labels TEXT NOT NULL DEFAULT '[]',FOREIGN KEY(run_id) REFERENCES sensitivity_runs(id));
            CREATE TABLE IF NOT EXISTS benchmark_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,status TEXT NOT NULL,request TEXT NOT NULL,result TEXT,error TEXT,created_at TEXT NOT NULL,completed_at TEXT);
            CREATE TABLE IF NOT EXISTS benchmark_results(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL,name TEXT NOT NULL,instrument TEXT,payload TEXT NOT NULL,FOREIGN KEY(run_id) REFERENCES benchmark_runs(id));
            CREATE TABLE IF NOT EXISTS robustness_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,status TEXT NOT NULL,input_run_id INTEGER,strategy_version TEXT,config_hash TEXT,random_seed INTEGER NOT NULL,request TEXT NOT NULL,result TEXT,error TEXT,created_at TEXT NOT NULL,completed_at TEXT);
            CREATE TABLE IF NOT EXISTS robustness_results(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL,simulation_type TEXT NOT NULL,payload TEXT NOT NULL,FOREIGN KEY(run_id) REFERENCES robustness_runs(id));
            CREATE TABLE IF NOT EXISTS decision_signal_runs(signal_id TEXT NOT NULL,run_id INTEGER NOT NULL,source TEXT NOT NULL DEFAULT 'BACKTEST',PRIMARY KEY(signal_id,run_id),FOREIGN KEY(signal_id) REFERENCES decision_signals(signal_id),FOREIGN KEY(run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS shadow_strategies(id INTEGER PRIMARY KEY AUTOINCREMENT,shadow_strategy_id TEXT NOT NULL UNIQUE,name TEXT NOT NULL,strategy_version TEXT NOT NULL,config_hash TEXT NOT NULL,parameters TEXT NOT NULL,instruments TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,started_at TEXT,stopped_at TEXT,status TEXT NOT NULL,virtual_initial_capital REAL NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,archived_at TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_shadow_active_unique ON shadow_strategies(config_hash,instruments) WHERE status IN ('RUNNING','PAUSED');
            CREATE TABLE IF NOT EXISTS shadow_strategy_states(shadow_strategy_id TEXT PRIMARY KEY,current_equity REAL NOT NULL,cash REAL NOT NULL,open_positions TEXT NOT NULL DEFAULT '{}',closed_trades INTEGER NOT NULL DEFAULT 0,total_r REAL NOT NULL DEFAULT 0,fees REAL NOT NULL DEFAULT 0,peak_equity REAL NOT NULL,drawdown REAL NOT NULL DEFAULT 0,last_candle_ts INTEGER,updated_at TEXT NOT NULL,FOREIGN KEY(shadow_strategy_id) REFERENCES shadow_strategies(shadow_strategy_id));
            CREATE TABLE IF NOT EXISTS shadow_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT,shadow_strategy_id TEXT NOT NULL,signal_id TEXT NOT NULL,instrument TEXT NOT NULL,candle_close_ts INTEGER NOT NULL,action TEXT NOT NULL,bias TEXT NOT NULL,score REAL NOT NULL,regime TEXT,payload TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(shadow_strategy_id,signal_id));
            CREATE TABLE IF NOT EXISTS shadow_trades(id INTEGER PRIMARY KEY AUTOINCREMENT,shadow_strategy_id TEXT NOT NULL,instrument TEXT NOT NULL,signal_id TEXT,side TEXT NOT NULL,status TEXT NOT NULL,entry_ts INTEGER NOT NULL,exit_ts INTEGER,entry REAL NOT NULL,exit REAL,stop REAL NOT NULL,target REAL NOT NULL,size REAL NOT NULL,pnl REAL,fees REAL NOT NULL DEFAULT 0,result_r REAL,reason TEXT,payload TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS shadow_equity(shadow_strategy_id TEXT NOT NULL,ts INTEGER NOT NULL,equity REAL NOT NULL,PRIMARY KEY(shadow_strategy_id,ts));
            CREATE TABLE IF NOT EXISTS shadow_daily_metrics(shadow_strategy_id TEXT NOT NULL,date TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(shadow_strategy_id,date));
            CREATE TABLE IF NOT EXISTS strategy_lifecycle(id INTEGER PRIMARY KEY AUTOINCREMENT,strategy_config_id INTEGER NOT NULL UNIQUE,name TEXT NOT NULL,strategy_version TEXT NOT NULL,config_hash TEXT NOT NULL,status TEXT NOT NULL,policy_version TEXT NOT NULL,last_evaluation_id INTEGER,previous_active_id INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_single_active_strategy ON strategy_lifecycle(status) WHERE status='Active';
            CREATE TABLE IF NOT EXISTS promotion_evaluations(id INTEGER PRIMARY KEY AUTOINCREMENT,lifecycle_id INTEGER NOT NULL,policy_version TEXT NOT NULL,result TEXT NOT NULL,recommended_status TEXT NOT NULL,evaluated_at TEXT NOT NULL,FOREIGN KEY(lifecycle_id) REFERENCES strategy_lifecycle(id));
            CREATE TABLE IF NOT EXISTS strategy_audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,lifecycle_id INTEGER NOT NULL,action TEXT NOT NULL,from_status TEXT,to_status TEXT,actor TEXT NOT NULL,evidence TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(lifecycle_id) REFERENCES strategy_lifecycle(id));
            CREATE INDEX IF NOT EXISTS idx_gate_runs_created ON gate_analysis_runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_decision_signal_runs_run ON decision_signal_runs(run_id,signal_id);
            CREATE INDEX IF NOT EXISTS idx_near_miss_filter ON near_miss_signals(instrument,regime,score_gap,candle_close_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_sensitivity_run ON sensitivity_results(run_id);
            CREATE INDEX IF NOT EXISTS idx_benchmark_run ON benchmark_results(run_id);
            CREATE INDEX IF NOT EXISTS idx_shadow_decisions_lookup ON shadow_decisions(shadow_strategy_id,instrument,candle_close_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_shadow_trades_lookup ON shadow_trades(shadow_strategy_id,status,entry_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_lifecycle ON strategy_audit_log(lifecycle_id,created_at DESC);
            """)
            for col, decl in (("regime", "TEXT"), ("regime_version", "TEXT"), ("gate_payload", "TEXT")):
                self._ensure_column(c, "decision_signals", col, decl)
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_signals'").fetchone():
                c.execute("INSERT OR IGNORE INTO decision_signal_runs(signal_id,run_id,source) SELECT signal_id,run_id,source FROM decision_signals WHERE run_id IS NOT NULL")
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_configs'").fetchone():
                rows = c.execute("SELECT id,name,parameters FROM strategy_configs").fetchall()
                for row in rows:
                    params = validate_parameters(json.loads(row["parameters"])); configuration_hash = config_hash(params); now = utc_now()
                    cur = c.execute("INSERT OR IGNORE INTO strategy_lifecycle(strategy_config_id,name,strategy_version,config_hash,status,policy_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (row["id"], row["name"], "canonical-v4", configuration_hash, "Draft", "promotion-policy-v1", now, now))
                    if cur.rowcount:
                        lifecycle_id = c.execute("SELECT id FROM strategy_lifecycle WHERE strategy_config_id=?", (row["id"],)).fetchone()[0]
                        c.execute("INSERT INTO strategy_audit_log(lifecycle_id,action,from_status,to_status,actor,evidence,created_at) VALUES(?,?,?,?,?,?,?)", (lifecycle_id, "MIGRATE", None, "Draft", "system", json.dumps({"source": "Phase 4 migration", "synthetic_results": False}), now))

    def create_run(self, table: str, request: dict[str, Any], **extra: Any) -> int:
        allowed = {"gate_analysis_runs", "sensitivity_runs", "benchmark_runs", "robustness_runs"}
        if table not in allowed: raise ValueError("Unsupported run table")
        columns = ["status", "request" if table != "gate_analysis_runs" else "filters", "created_at"] + list(extra)
        values = ["QUEUED", json.dumps(request), utc_now()] + list(extra.values())
        with self.connect() as c:
            cur = c.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values); return int(cur.lastrowid)

    def bind_job(self, table: str, run_id: int, job_id: int) -> None:
        with self.connect() as c: c.execute(f"UPDATE {table} SET job_id=? WHERE id=?", (job_id, run_id))

    def finish_run(self, table: str, run_id: int, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        result_column = "summary" if table == "gate_analysis_runs" else "result_summary" if table == "sensitivity_runs" else "result"
        with self.connect() as c: c.execute(f"UPDATE {table} SET status=?,{result_column}=?,error=?,completed_at=? WHERE id=?", ("FAILED" if error else "COMPLETED", json.dumps(result) if result is not None else None, error, utc_now(), run_id))

    def run(self, table: str, run_id: int) -> dict[str, Any] | None:
        with self.connect() as c: row = c.execute(f"SELECT * FROM {table} WHERE id=?", (run_id,)).fetchone()
        if not row: return None
        item = dict(row)
        for key in ("request", "filters", "summary", "result", "result_summary"):
            if item.get(key): item[key] = json.loads(item[key])
        return item

    def decisions(self, filters: dict[str, Any], limit: int = 200000) -> list[dict[str, Any]]:
        clauses, values = ["1=1"], []
        mapping = {"instrument": "d.instrument", "strategy_version": "d.strategy_version", "config_hash": "d.config_hash", "timeframe": "d.execution_timeframe", "source": "d.source"}
        for key, column in mapping.items():
            if filters.get(key) not in (None, "", "ALL"): clauses.append(f"{column}=?"); values.append(filters[key])
        join = ""
        if filters.get("run_id") not in (None, "", "ALL"):
            join = " JOIN decision_signal_runs dsr ON dsr.signal_id=d.signal_id"
            clauses.append("dsr.run_id=?"); values.append(int(filters["run_id"]))
        if filters.get("start_ts"): clauses.append("d.candle_close_ts>=?"); values.append(int(filters["start_ts"]))
        if filters.get("end_ts"): clauses.append("d.candle_close_ts<=?"); values.append(int(filters["end_ts"]))
        with self.connect() as c: rows = c.execute(f"SELECT d.source,d.decision_payload,d.regime,d.regime_version FROM decision_signals d{join} WHERE {' AND '.join(clauses)} ORDER BY d.candle_close_ts LIMIT ?", (*values, min(limit, 200000))).fetchall()
        output = []
        for row in rows:
            payload = json.loads(row["decision_payload"]); payload["source"] = row["source"]; payload.setdefault("regime", row["regime"] or "Unknown"); payload.setdefault("regime_version", row["regime_version"])
            output.append(payload)
        return output

    def save_near_miss(self, item: dict[str, Any]) -> int:
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO near_miss_signals(signal_id,instrument,candle_close_ts,strategy_version,config_hash,bias,score,score_gap,regime,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (item["signal_id"], item["instrument"], item["candle_close_ts"], item["strategy_version"], item["config_hash"], item["bias"], item["score"], item["score_gap"], item.get("regime"), json.dumps(item), utc_now()))
            return int(c.execute("SELECT id FROM near_miss_signals WHERE signal_id=?", (item["signal_id"],)).fetchone()[0])

    def near_misses(self, filters: dict[str, Any], page: int = 1, page_size: int = 50) -> dict[str, Any]:
        clauses, values = ["1=1"], []
        for key in ("instrument", "regime"):
            if filters.get(key) not in (None, "", "ALL"): clauses.append(f"{key}=?"); values.append(filters[key])
        if filters.get("gate"): clauses.append("payload LIKE ?"); values.append(f'%"{filters["gate"]}"%')
        order = "score_gap ASC,candle_close_ts DESC" if filters.get("sort", "score_gap") == "score_gap" else "candle_close_ts DESC"
        page_size = min(max(int(page_size), 1), 100); offset = (max(page, 1) - 1) * page_size
        with self.connect() as c:
            total = c.execute(f"SELECT COUNT(*) FROM near_miss_signals WHERE {' AND '.join(clauses)}", values).fetchone()[0]
            rows = c.execute(f"SELECT id,payload FROM near_miss_signals WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ? OFFSET ?", (*values, page_size, offset)).fetchall()
        return {"items": [{"id": row["id"], **json.loads(row["payload"])} for row in rows], "total": total, "page": max(page, 1), "page_size": page_size}

    def near_miss(self, item_id: int) -> dict[str, Any] | None:
        with self.connect() as c:
            row = c.execute("SELECT id,payload FROM near_miss_signals WHERE id=?", (item_id,)).fetchone(); outcome = c.execute("SELECT payload FROM near_miss_outcomes WHERE near_miss_id=?", (item_id,)).fetchone()
        return ({"id": row["id"], **json.loads(row["payload"]), "outcome": json.loads(outcome[0]) if outcome else None} if row else None)

    def save_outcome(self, item_id: int, outcome: dict[str, Any]) -> None:
        with self.connect() as c: c.execute("INSERT OR REPLACE INTO near_miss_outcomes(near_miss_id,status,payload,evaluated_at) VALUES(?,?,?,?)", (item_id, outcome["status"], json.dumps(outcome), utc_now()))

    def table_counts(self) -> dict[str, int]:
        with self.connect() as c:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {table: c.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in sorted(tables)}
