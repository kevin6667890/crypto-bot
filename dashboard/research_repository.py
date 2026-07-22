"""SQLite persistence for historical data, strategy configs and research runs."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from strategy_rules import STRATEGY_PRESETS
    from signal_identity import config_hash
except ImportError:
    from .strategy_rules import STRATEGY_PRESETS
    from .signal_identity import config_hash


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ResearchRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS strategy_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, parameters TEXT NOT NULL,
                    instrument TEXT NOT NULL DEFAULT 'BTC-USDT', timeframe TEXT NOT NULL DEFAULT '15m',
                    start_date TEXT, end_date TEXT, latest_summary TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_config_id INTEGER, status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0, progress_message TEXT, instrument TEXT NOT NULL,
                    timeframe TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    parameters TEXT NOT NULL, result TEXT, error TEXT, data_quality TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(strategy_config_id) REFERENCES strategy_configs(id)
                );
                CREATE TABLE IF NOT EXISTS backtest_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, trade_id INTEGER NOT NULL,
                    payload TEXT NOT NULL, entry_ts INTEGER NOT NULL, side TEXT NOT NULL, pnl REAL NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS backtest_equity (
                    run_id INTEGER NOT NULL, ts INTEGER NOT NULL, equity REAL NOT NULL,
                    PRIMARY KEY(run_id, ts), FOREIGN KEY(run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS historical_candles (
                    instrument TEXT NOT NULL, timeframe TEXT NOT NULL, ts INTEGER NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                    volume REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT 'OKX',
                    PRIMARY KEY(instrument, timeframe, ts)
                );
                CREATE TABLE IF NOT EXISTS historical_flow (
                    instrument TEXT NOT NULL, timeframe TEXT NOT NULL, ts INTEGER NOT NULL,
                    buy_volume REAL NOT NULL, sell_volume REAL NOT NULL, cvd_delta REAL NOT NULL,
                    source TEXT NOT NULL, PRIMARY KEY(instrument, timeframe, ts)
                );
                CREATE TABLE IF NOT EXISTS walk_forward_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, instrument TEXT NOT NULL, timeframe TEXT NOT NULL,
                    parameters TEXT NOT NULL, windows TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_backtest_history ON backtest_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_backtest_trades_run ON backtest_trades(run_id, entry_ts);
                CREATE INDEX IF NOT EXISTS idx_historical_range ON historical_candles(instrument, timeframe, ts);
                CREATE INDEX IF NOT EXISTS idx_historical_flow_range ON historical_flow(instrument, timeframe, ts);
                CREATE INDEX IF NOT EXISTS idx_strategy_updated ON strategy_configs(updated_at DESC);
                CREATE TABLE IF NOT EXISTS decision_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
                    run_id INTEGER, instrument TEXT NOT NULL, execution_timeframe TEXT NOT NULL, candle_close_ts INTEGER NOT NULL,
                    strategy_version TEXT NOT NULL, config_hash TEXT NOT NULL, action TEXT NOT NULL, bias TEXT NOT NULL,
                    score REAL NOT NULL, decision_payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_signal_runs (
                    signal_id TEXT NOT NULL, run_id INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'BACKTEST',
                    PRIMARY KEY(signal_id, run_id),
                    FOREIGN KEY(signal_id) REFERENCES decision_signals(signal_id),
                    FOREIGN KEY(run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS portfolio_backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT NOT NULL, parameters TEXT NOT NULL,
                    result TEXT, error TEXT, created_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS portfolio_backtest_assets (run_id INTEGER NOT NULL, instrument TEXT NOT NULL, weight REAL NOT NULL, PRIMARY KEY(run_id,instrument));
                CREATE TABLE IF NOT EXISTS portfolio_backtest_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, instrument TEXT NOT NULL, signal_id TEXT, entry_ts INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_backtest_equity (run_id INTEGER NOT NULL, ts INTEGER NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL, exposure REAL NOT NULL, PRIMARY KEY(run_id,ts));
                CREATE INDEX IF NOT EXISTS idx_decision_lookup ON decision_signals(instrument,strategy_version,candle_close_ts);
                CREATE INDEX IF NOT EXISTS idx_decision_signal_runs_run ON decision_signal_runs(run_id,signal_id);
                CREATE INDEX IF NOT EXISTS idx_portfolio_trades_run ON portfolio_backtest_trades(run_id,entry_ts);
                CREATE TABLE IF NOT EXISTS optimization_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT NOT NULL,
                    request TEXT NOT NULL, scoring_policy TEXT NOT NULL, seed INTEGER NOT NULL,
                    holdout_start_ts INTEGER NOT NULL, result TEXT, error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS optimization_families (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, instrument TEXT NOT NULL,
                    timeframe TEXT NOT NULL, start_ts INTEGER NOT NULL, development_end_ts INTEGER NOT NULL,
                    holdout_start_ts INTEGER NOT NULL, holdout_end_ts INTEGER NOT NULL,
                    final_oot_start_ts INTEGER, final_oot_end_ts INTEGER, family_fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, holdout_revealed_at TEXT, final_oot_revealed_at TEXT,
                    notes TEXT
                );
                CREATE TABLE IF NOT EXISTS validation_suites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_family_id INTEGER NOT NULL,
                    source_optimization_run_id INTEGER NOT NULL, source_trial_id INTEGER NOT NULL,
                    job_id INTEGER, status TEXT NOT NULL, request TEXT NOT NULL, policy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL, completed_at TEXT, error TEXT,
                    retry_of_suite_id INTEGER, attempt_number INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(experiment_family_id) REFERENCES optimization_families(id),
                    FOREIGN KEY(source_optimization_run_id) REFERENCES optimization_runs(id),
                    FOREIGN KEY(source_trial_id) REFERENCES optimization_trials(id)
                );
                CREATE TABLE IF NOT EXISTS validation_suite_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, suite_id INTEGER NOT NULL, stage TEXT NOT NULL,
                    instrument TEXT NOT NULL, timeframe TEXT NOT NULL, start_ts INTEGER NOT NULL, end_ts INTEGER NOT NULL,
                    metrics TEXT, buy_hold_metrics TEXT, data_quality TEXT, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
                    FOREIGN KEY(suite_id) REFERENCES validation_suites(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS optimization_trials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, optimization_run_id INTEGER NOT NULL,
                    trial_number INTEGER NOT NULL, status TEXT NOT NULL, parameters TEXT NOT NULL,
                    train_metrics TEXT, validation_metrics TEXT, holdout_metrics TEXT, score REAL,
                    score_components TEXT, elimination_reasons TEXT, runtime_ms INTEGER,
                    random_seed INTEGER NOT NULL, engine_version TEXT NOT NULL, error TEXT,
                    created_at TEXT NOT NULL, completed_at TEXT,
                    UNIQUE(optimization_run_id, trial_number),
                    FOREIGN KEY(optimization_run_id) REFERENCES optimization_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_optimization_runs_created ON optimization_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_optimization_trials_run ON optimization_trials(optimization_run_id, score DESC);
                CREATE TABLE IF NOT EXISTS discovery_datasets (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE,start_ts INTEGER NOT NULL,end_ts INTEGER NOT NULL,instruments TEXT NOT NULL,timeframes TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL,manifest TEXT NOT NULL,dataset_fingerprint TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,completed_at TEXT,error TEXT);
                CREATE TABLE IF NOT EXISTS discovery_dataset_partitions (dataset_id INTEGER NOT NULL,instrument TEXT NOT NULL,timeframe TEXT NOT NULL,first_ts INTEGER,last_ts INTEGER,expected_rows INTEGER NOT NULL,actual_rows INTEGER NOT NULL,missing_rows INTEGER NOT NULL,duplicate_rows INTEGER NOT NULL,fingerprint TEXT,status TEXT NOT NULL,warnings TEXT,PRIMARY KEY(dataset_id,instrument,timeframe),FOREIGN KEY(dataset_id) REFERENCES discovery_datasets(id));
                CREATE TABLE IF NOT EXISTS discovery_flow_coverage (id INTEGER PRIMARY KEY AUTOINCREMENT,dataset_id INTEGER NOT NULL,instrument TEXT NOT NULL,feature TEXT NOT NULL,requested_start INTEGER NOT NULL,requested_end INTEGER NOT NULL,actual_coverage TEXT,missing_intervals TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL,limitations TEXT NOT NULL,FOREIGN KEY(dataset_id) REFERENCES discovery_datasets(id));
                CREATE TABLE IF NOT EXISTS strategy_discovery_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,dataset_id INTEGER NOT NULL,experiment_family_id INTEGER,status TEXT NOT NULL,request TEXT NOT NULL,search_policy TEXT NOT NULL,sampler TEXT NOT NULL,seed INTEGER NOT NULL,maximum_trials INTEGER NOT NULL,templates TEXT NOT NULL,feature_version TEXT NOT NULL,engine_version TEXT NOT NULL,scoring_version TEXT NOT NULL,progress TEXT,result TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,completed_at TEXT,error TEXT,retry_of_run_id INTEGER);
                CREATE TABLE IF NOT EXISTS strategy_discovery_candidates (id INTEGER PRIMARY KEY AUTOINCREMENT,discovery_run_id INTEGER NOT NULL,candidate_number INTEGER NOT NULL,template TEXT NOT NULL,template_version TEXT NOT NULL,parameters TEXT NOT NULL,parameter_hash TEXT NOT NULL,feature_flags TEXT NOT NULL,complexity INTEGER NOT NULL,status TEXT NOT NULL,aggregate_metrics TEXT,score_components TEXT,pareto_rank INTEGER,elimination_reasons TEXT,created_at TEXT NOT NULL,completed_at TEXT,error TEXT,UNIQUE(discovery_run_id,candidate_number));
                CREATE TABLE IF NOT EXISTS strategy_discovery_folds (id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,fold_number INTEGER NOT NULL,train_start_ts INTEGER NOT NULL,train_end_ts INTEGER NOT NULL,validation_start_ts INTEGER NOT NULL,validation_end_ts INTEGER NOT NULL,metrics TEXT,buy_hold_metrics TEXT,status TEXT NOT NULL,error TEXT);
                CREATE TABLE IF NOT EXISTS strategy_discovery_ablations (id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,removed_component TEXT NOT NULL,metrics TEXT,score_difference REAL,status TEXT NOT NULL,error TEXT);
                CREATE TABLE IF NOT EXISTS strategy_discovery_stress_tests (id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,scenario TEXT NOT NULL,assumptions TEXT NOT NULL,metrics TEXT,status TEXT NOT NULL,error TEXT);
            """)
            # Fold identity is durable: retries update the same candidate/fold evidence.
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_discovery_fold_identity ON strategy_discovery_folds(candidate_id,fold_number)")
            self._ensure_column(connection, "paper_trades", "signal_id", "TEXT")
            self._ensure_column(connection, "strategy_discovery_candidates", "eligibility_status", "TEXT")
            self._ensure_column(connection, "strategy_discovery_candidates", "development_score", "REAL")
            self._ensure_column(connection, "strategy_discovery_candidates", "eligible_rank", "INTEGER")
            self._ensure_column(connection, "strategy_discovery_candidates", "scoring_policy_version", "TEXT")
            self._ensure_column(connection, "optimization_runs", "experiment_family_id", "INTEGER")
            self._ensure_column(connection, "optimization_runs", "parent_run_id", "INTEGER")
            self._ensure_column(connection, "optimization_runs", "post_holdout_adjustment", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "optimization_runs", "search_space_changed", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "optimization_runs", "base_parameters_changed", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "optimization_runs", "holdout_revealed_at", "TEXT")
            self._ensure_column(connection, "optimization_runs", "request_fingerprint", "TEXT")
            self._ensure_column(connection, "validation_suites", "retry_of_suite_id", "INTEGER")
            self._ensure_column(connection, "validation_suites", "attempt_number", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(connection, "paper_trades", "strategy_version", "TEXT")
            self._ensure_column(connection, "paper_trades", "config_hash", "TEXT")
            self._ensure_column(connection, "paper_trades", "expected_entry_price", "REAL")
            self._ensure_column(connection, "paper_trades", "observed_entry_price", "REAL")
            self._ensure_column(connection, "paper_trades", "execution_delay_ms", "INTEGER")
            self._ensure_column(connection, "paper_trades", "observed_slippage_pct", "REAL")
            self._ensure_column(connection, "backtest_trades", "signal_id", "TEXT")
            self._ensure_column(connection, "backtest_trades", "strategy_version", "TEXT")
            self._ensure_column(connection, "backtest_trades", "config_hash", "TEXT")
            self._ensure_column(connection, "backtest_trades", "expected_entry_ts", "INTEGER")
            self._ensure_column(connection, "backtest_trades", "expected_entry_price", "REAL")
            self._ensure_column(connection, "backtest_runs", "message_code", "TEXT")
            self._ensure_column(connection, "backtest_runs", "message_params", "TEXT")
            self._ensure_column(connection, "decision_signals", "regime", "TEXT")
            self._ensure_column(connection, "decision_signals", "regime_version", "TEXT")
            self._ensure_column(connection, "decision_signals", "gate_payload", "TEXT")
            self._ensure_column(connection, "decision_signal_runs", "decision_payload", "TEXT")
            self._ensure_column(connection, "decision_signal_runs", "gate_payload", "TEXT")
            self._ensure_column(connection, "decision_signal_runs", "regime", "TEXT")
            self._ensure_column(connection, "decision_signal_runs", "regime_version", "TEXT")
            connection.execute("""INSERT OR IGNORE INTO decision_signal_runs(signal_id,run_id,source,decision_payload,gate_payload,regime,regime_version)
                SELECT signal_id,run_id,source,decision_payload,gate_payload,regime,regime_version FROM decision_signals WHERE run_id IS NOT NULL""")
            connection.execute("UPDATE backtest_runs SET status='FAILED',progress=100,progress_message='Interrupted by service restart',message_code='job.interrupted.restart',message_params='{}',error='Backtest worker was interrupted by a service restart',updated_at=? WHERE status IN ('QUEUED','RUNNING')", (utc_now(),))
            now = utc_now()
            connection.execute("UPDATE optimization_runs SET status='INTERRUPTED',error='Service restarted while optimization was running',updated_at=?,completed_at=? WHERE status='RUNNING'", (now, now))
            connection.execute("UPDATE optimization_trials SET status='INTERRUPTED',error='Service restarted while trial was running',completed_at=? WHERE status='RUNNING' AND optimization_run_id IN (SELECT id FROM optimization_runs WHERE status='INTERRUPTED' AND error='Service restarted while optimization was running')", (now,))
            connection.execute("UPDATE validation_suites SET status='INTERRUPTED',error='Service restarted while validation was running',completed_at=? WHERE status='RUNNING'", (now,))
            count = connection.execute("SELECT COUNT(*) FROM strategy_configs").fetchone()[0]
            if not count:
                now = utc_now()
                for name, parameters in STRATEGY_PRESETS.items():
                    connection.execute("INSERT INTO strategy_configs(name,parameters,created_at,updated_at) VALUES(?,?,?,?)", (name, json.dumps(parameters), now, now))

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return
        if column not in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def fingerprint(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    # Discovery persistence is deliberately separate from Optimization Lab evidence.
    def create_or_get_discovery_dataset(self,name:str,start:int,end:int,instruments:list[str],timeframes:list[str],smoke_test:bool=False)->dict[str,Any]:
        now=utc_now()
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO discovery_datasets(name,start_ts,end_ts,instruments,timeframes,source,status,manifest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(name,start,end,json.dumps(instruments),json.dumps(timeframes),'OKX public history-candles','PREPARING',json.dumps({'is_smoke_test':smoke_test}),now,now))
            return dict(c.execute("SELECT * FROM discovery_datasets WHERE name=?",(name,)).fetchone())
    def discovery_partition(self,dataset_id:int,instrument:str,timeframe:str)->dict[str,Any]|None:
        with self.connect() as c:
            r=c.execute("SELECT * FROM discovery_dataset_partitions WHERE dataset_id=? AND instrument=? AND timeframe=?",(dataset_id,instrument,timeframe)).fetchone(); return dict(r) if r else None
    def upsert_discovery_partition(self,dataset_id:int,instrument:str,timeframe:str,rows:list[dict[str,Any]],q:dict[str,Any])->None:
        in_range=[r for r in rows if q.get('expected_rows') and True]; first=min((int(r['ts']) for r in in_range),default=None);last=max((int(r['ts']) for r in in_range),default=None)
        with self.connect() as c:c.execute("INSERT INTO discovery_dataset_partitions VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dataset_id,instrument,timeframe) DO UPDATE SET first_ts=excluded.first_ts,last_ts=excluded.last_ts,expected_rows=excluded.expected_rows,actual_rows=excluded.actual_rows,missing_rows=excluded.missing_rows,duplicate_rows=excluded.duplicate_rows,fingerprint=excluded.fingerprint,status=excluded.status,warnings=excluded.warnings",(dataset_id,instrument,timeframe,first,last,q['expected_rows'],q['actual_rows'],q['missing_rows'],q['duplicate_rows'],q['fingerprint'],q['status'],json.dumps(q.get('warnings',[]))))
    def finish_discovery_dataset(self,dataset_id:int)->dict[str,Any]:
        with self.connect() as c:
            row=c.execute("SELECT manifest FROM discovery_datasets WHERE id=?",(dataset_id,)).fetchone(); prior=json.loads(row['manifest'] or '{}') if row else {}; parts=[dict(x) for x in c.execute("SELECT * FROM discovery_dataset_partitions WHERE dataset_id=? ORDER BY instrument,timeframe",(dataset_id,))]; manifest={**prior,'partitions':parts}; fp=self.fingerprint({'is_smoke_test':bool(manifest.get('is_smoke_test')),'partitions':[{k:x.get(k) for k in ('instrument','timeframe','fingerprint','status')} for x in parts]}); status='COMPLETE' if parts and all(x['status']=='COMPLETE' for x in parts) else 'INCOMPLETE'; now=utc_now();c.execute("UPDATE discovery_datasets SET status=?,manifest=?,dataset_fingerprint=?,updated_at=?,completed_at=? WHERE id=?",(status,json.dumps(manifest),fp,now,now if status=='COMPLETE' else None,dataset_id));r=c.execute("SELECT * FROM discovery_datasets WHERE id=?",(dataset_id,)).fetchone();return dict(r)
    def discovery_datasets(self)->list[dict[str,Any]]:
        with self.connect() as c:return [dict(x) for x in c.execute("SELECT * FROM discovery_datasets ORDER BY id DESC")]
    def discovery_dataset(self,dataset_id:int)->dict[str,Any]|None:
        with self.connect() as c:
            r=c.execute("SELECT * FROM discovery_datasets WHERE id=?",(dataset_id,)).fetchone()
            if not r:return None
            x=dict(r);x['partitions']=[dict(p) for p in c.execute("SELECT * FROM discovery_dataset_partitions WHERE dataset_id=? ORDER BY instrument,timeframe",(dataset_id,))];x['flow_coverage']=[dict(p) for p in c.execute("SELECT * FROM discovery_flow_coverage WHERE dataset_id=?",(dataset_id,))];return x
    def replace_flow_coverage(self,dataset_id:int,items:list[dict[str,Any]])->None:
        with self.connect() as c:
            c.execute('DELETE FROM discovery_flow_coverage WHERE dataset_id=?',(dataset_id,));c.executemany("INSERT INTO discovery_flow_coverage(dataset_id,instrument,feature,requested_start,requested_end,actual_coverage,missing_intervals,source,status,limitations) VALUES(?,?,?,?,?,?,?,?,?,?)",[(dataset_id,x['instrument'],x['feature'],x['requested_start'],x['requested_end'],json.dumps(x['actual_coverage']),json.dumps(x['missing_intervals']),x['source'],x['status'],x['limitations']) for x in items])

    def create_optimization_family(self, request: dict[str, Any]) -> dict[str, Any]:
        locked = {key: request.get(key) for key in ("instrument", "timeframe", "start_ts", "development_end_ts", "holdout_start_ts", "holdout_end_ts", "final_oot_start_ts", "final_oot_end_ts")}
        fingerprint = self.fingerprint(locked); now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute("""INSERT INTO optimization_families(name,instrument,timeframe,start_ts,development_end_ts,holdout_start_ts,holdout_end_ts,final_oot_start_ts,final_oot_end_ts,family_fingerprint,created_at,updated_at,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (request.get("name") or f"{locked['instrument']} {locked['timeframe']} family", locked["instrument"], locked["timeframe"], locked["start_ts"], locked["development_end_ts"], locked["holdout_start_ts"], locked["holdout_end_ts"], locked["final_oot_start_ts"], locked["final_oot_end_ts"], fingerprint, now, now, request.get("notes")))
            family_id = int(cursor.lastrowid)
        return self.optimization_family(family_id) or {}

    def optimization_families(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM optimization_families ORDER BY id DESC")]

    def optimization_family(self, family_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM optimization_families WHERE id=?", (family_id,)).fetchone()
            if not row: return None
            item = dict(row)
            item["runs"] = [dict(run) for run in connection.execute("SELECT id,status,seed,created_at,request,post_holdout_adjustment,search_space_changed,base_parameters_changed,parent_run_id,holdout_revealed_at FROM optimization_runs WHERE experiment_family_id=? ORDER BY id DESC", (family_id,))]
            for run in item["runs"]:
                run["request"] = json.loads(run["request"])
            return item

    def reveal_optimization_holdout(self, run_id: int) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as connection:
            row = connection.execute("SELECT experiment_family_id FROM optimization_runs WHERE id=?", (run_id,)).fetchone()
            if not row: return None
            connection.execute("UPDATE optimization_runs SET holdout_revealed_at=COALESCE(holdout_revealed_at,?),updated_at=? WHERE id=?", (now, now, run_id))
            if row["experiment_family_id"]:
                connection.execute("UPDATE optimization_families SET holdout_revealed_at=COALESCE(holdout_revealed_at,?),updated_at=? WHERE id=?", (now, now, row["experiment_family_id"]))
        return self.optimization_run(run_id, include_holdout=True)

    def reveal_final_oot(self, family_id: int) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE optimization_families SET final_oot_revealed_at=COALESCE(final_oot_revealed_at,?),updated_at=? WHERE id=?", (utc_now(), utc_now(), family_id))

    def create_optimization_run(self, request: dict[str, Any], scoring_policy: dict[str, Any], seed: int, holdout_start_ts: int, family_id: int | None = None, parent_run_id: int | None = None, contamination: dict[str, bool] | None = None) -> int:
        now = utc_now()
        contamination = contamination or {}
        with self.connect() as connection:
            cursor = connection.execute("""INSERT INTO optimization_runs(job_id,status,request,scoring_policy,seed,holdout_start_ts,experiment_family_id,parent_run_id,post_holdout_adjustment,search_space_changed,base_parameters_changed,request_fingerprint,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (None, "QUEUED", json.dumps(request), json.dumps(scoring_policy), seed, holdout_start_ts, family_id, parent_run_id, int(contamination.get("post_holdout_adjustment", False)), int(contamination.get("search_space_changed", False)), int(contamination.get("base_parameters_changed", False)), self.fingerprint(request), now, now))
            return int(cursor.lastrowid)

    def update_optimization_run(self, run_id: int, **values: Any) -> None:
        if not values:
            return
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connect() as connection:
            connection.execute(f"UPDATE optimization_runs SET {assignments} WHERE id=?", (*values.values(), run_id))

    def mark_optimization_run_terminal(self, run_id: int, status: str, error: str | None = None, completed_at: str | None = None) -> None:
        if status not in {"COMPLETED", "CANCELLED", "FAILED", "INTERRUPTED"}:
            raise ValueError("Unsupported optimization terminal status.")
        now = completed_at or utc_now()
        with self.connect() as connection:
            connection.execute("UPDATE optimization_runs SET status=?,error=COALESCE(?,error),updated_at=?,completed_at=? WHERE id=?", (status, error, now, now, run_id))
            if status != "COMPLETED":
                connection.execute("UPDATE optimization_trials SET status=?,error=COALESCE(error,?),completed_at=? WHERE optimization_run_id=? AND status='RUNNING'", (status, error or f"Optimization {status.lower()}", now, run_id))

    def reconcile_optimization_jobs(self) -> None:
        """Project durable queue terminal state after a restart without deleting evidence."""
        with self.connect() as connection:
            rows = connection.execute("SELECT o.id,j.status,j.error,j.completed_at FROM optimization_runs o JOIN research_jobs j ON j.id=o.job_id WHERE o.status IN ('QUEUED','RUNNING') AND j.status IN ('COMPLETED','CANCELLED','FAILED','INTERRUPTED')").fetchall()
        for row in rows:
            self.mark_optimization_run_terminal(int(row["id"]), str(row["status"]), row["error"], row["completed_at"])

    def create_optimization_trial(self, run_id: int, trial_number: int, parameters: dict[str, Any], seed: int, engine_version: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute("INSERT INTO optimization_trials(optimization_run_id,trial_number,status,parameters,random_seed,engine_version,created_at) VALUES(?,?,?,?,?,?,?)", (run_id, trial_number, "RUNNING", json.dumps(parameters), seed, engine_version, utc_now()))
            return int(cursor.lastrowid)

    def complete_optimization_trial(self, trial_id: int, status: str, **values: Any) -> None:
        json_fields = {"train_metrics", "validation_metrics", "holdout_metrics", "score_components", "elimination_reasons"}
        payload = {key: json.dumps(value) if key in json_fields and value is not None else value for key, value in values.items()}
        payload.update({"status": status, "completed_at": utc_now()})
        assignments = ",".join(f"{key}=?" for key in payload)
        with self.connect() as connection:
            connection.execute(f"UPDATE optimization_trials SET {assignments} WHERE id=?", (*payload.values(), trial_id))

    @staticmethod
    def _optimization_trial_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("parameters", "train_metrics", "validation_metrics", "holdout_metrics", "score_components", "elimination_reasons"):
            if item.get(key):
                item[key] = json.loads(item[key])
            elif key in item:
                item[key] = None
        return item

    def optimization_run(self, run_id: int, limit: int = 500, include_holdout: bool = True) -> dict[str, Any] | None:
        with self.connect() as connection:
            run = connection.execute("SELECT * FROM optimization_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                return None
            item = dict(run)
            for key in ("request", "scoring_policy", "result"):
                item[key] = json.loads(item[key]) if item.get(key) else None
            rows = connection.execute("SELECT * FROM optimization_trials WHERE optimization_run_id=? ORDER BY CASE WHEN score IS NULL THEN 1 ELSE 0 END, score DESC, trial_number LIMIT ?", (run_id, min(max(1, limit), 500))).fetchall()
            item["trials"] = [self._optimization_trial_dict(row) for row in rows]
            if not include_holdout:
                for trial in item["trials"]: trial["holdout_metrics"] = None
            return item

    def optimization_history(self, limit: int = 30, include_holdout: bool = False) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM optimization_runs ORDER BY id DESC LIMIT ?", (min(max(1, limit), 100),)).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["request"] = json.loads(item["request"])
            item["scoring_policy"] = json.loads(item["scoring_policy"])
            item["result"] = json.loads(item["result"]) if item.get("result") else None
            if not include_holdout: item["holdout_revealed"] = bool(item.get("holdout_revealed_at"))
            output.append(item)
        return output

    def create_validation_suite(self, family_id: int, run_id: int, trial_id: int, request: dict[str, Any]) -> int:
        with self.connect() as connection:
            cursor = connection.execute("INSERT INTO validation_suites(experiment_family_id,source_optimization_run_id,source_trial_id,status,request,policy_version,created_at) VALUES(?,?,?,?,?,?,?)", (family_id, run_id, trial_id, "QUEUED", json.dumps(request), "oot-validation-v1", utc_now()))
            return int(cursor.lastrowid)

    def create_validation_suite_retry(self, original_suite_id: int, requester_key: str = "public") -> int:
        """Create immutable retry evidence; never resume or mutate the original suite."""
        with self.connect() as connection:
            original = connection.execute("SELECT * FROM validation_suites WHERE id=?", (original_suite_id,)).fetchone()
            if not original:
                raise ValueError("Validation suite not found.")
            if original["status"] not in {"FAILED", "CANCELLED", "INTERRUPTED"}:
                raise ValueError("Only failed, cancelled or interrupted validation suites can be retried.")
            cursor = connection.execute("""INSERT INTO validation_suites(experiment_family_id,source_optimization_run_id,source_trial_id,status,request,policy_version,created_at,retry_of_suite_id,attempt_number)
                VALUES(?,?,?,?,?,?,?,?,?)""", (original["experiment_family_id"], original["source_optimization_run_id"], original["source_trial_id"], "QUEUED", original["request"], original["policy_version"], utc_now(), original_suite_id, int(original["attempt_number"] or 1) + 1))
            return int(cursor.lastrowid)

    def update_validation_suite(self, suite_id: int, **values: Any) -> None:
        if not values: return
        fields = ",".join(f"{key}=?" for key in values)
        with self.connect() as connection: connection.execute(f"UPDATE validation_suites SET {fields} WHERE id=?", (*values.values(), suite_id))

    def add_validation_result(self, suite_id: int, **values: Any) -> int:
        keys = ("stage", "instrument", "timeframe", "start_ts", "end_ts", "metrics", "buy_hold_metrics", "data_quality", "status", "error")
        payload = [json.dumps(values[key]) if key in {"metrics", "buy_hold_metrics", "data_quality"} and values.get(key) is not None else values.get(key) for key in keys]
        with self.connect() as connection:
            cursor = connection.execute(f"INSERT INTO validation_suite_results(suite_id,{','.join(keys)},created_at) VALUES(?,{','.join('?' for _ in keys)},?)", (suite_id, *payload, utc_now()))
            return int(cursor.lastrowid)

    def validation_suite(self, suite_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM validation_suites WHERE id=?", (suite_id,)).fetchone()
            if not row: return None
            item = dict(row); item["request"] = json.loads(item["request"])
            results = [dict(result) for result in connection.execute("SELECT * FROM validation_suite_results WHERE suite_id=? ORDER BY id", (suite_id,))]
        for result in results:
            for key in ("metrics", "buy_hold_metrics", "data_quality"): result[key] = json.loads(result[key]) if result.get(key) else None
        item["results"] = results; return item

    def validation_suites(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM validation_suites ORDER BY id DESC")]

    def upsert_candles(self, instrument: str, timeframe: str, candles: list[dict[str, Any]], source: str = "OKX") -> None:
        if not candles:
            return
        with self.connect() as connection:
            connection.executemany("""INSERT OR REPLACE INTO historical_candles
                (instrument,timeframe,ts,open,high,low,close,volume,confirmed,source) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [(instrument, timeframe, int(row["ts"]), row["open"], row["high"], row["low"], row["close"], row["volume"], int(row.get("confirmed", 1)), source) for row in candles])

    def candles(self, instrument: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT ts,open,high,low,close,volume,confirmed FROM historical_candles WHERE instrument=? AND timeframe=? AND ts BETWEEN ? AND ? AND confirmed=1 ORDER BY ts", (instrument, timeframe, start_ts, end_ts))]

    def upsert_flow(self, instrument: str, timeframe: str, rows: list[dict[str, Any]], source: str) -> None:
        if not rows:
            return
        with self.connect() as connection:
            connection.executemany("""INSERT OR REPLACE INTO historical_flow
                (instrument,timeframe,ts,buy_volume,sell_volume,cvd_delta,source) VALUES(?,?,?,?,?,?,?)""",
                [(instrument, timeframe, int(row["ts"]), float(row["buy_volume"]), float(row["sell_volume"]), float(row["buy_volume"]) - float(row["sell_volume"]), source) for row in rows])

    def flow(self, instrument: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT ts,buy_volume,sell_volume,cvd_delta,source FROM historical_flow WHERE instrument=? AND timeframe=? AND ts BETWEEN ? AND ? ORDER BY ts", (instrument, timeframe, start_ts, end_ts))]

    def candle_coverage(self, instrument: str, timeframe: str) -> tuple[int | None, int | None]:
        with self.connect() as connection:
            row = connection.execute("SELECT MIN(ts),MAX(ts) FROM historical_candles WHERE instrument=? AND timeframe=? AND confirmed=1", (instrument, timeframe)).fetchone()
            return row[0], row[1]

    def data_coverage(self) -> list[dict[str,Any]]:
        with self.connect() as connection:return [dict(row) for row in connection.execute("SELECT instrument,timeframe,COUNT(*) rows,MIN(ts) first_ts,MAX(ts) last_ts FROM historical_candles WHERE confirmed=1 GROUP BY instrument,timeframe ORDER BY instrument,timeframe")]

    def create_run(self, payload: dict[str, Any]) -> int:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute("""INSERT INTO backtest_runs(strategy_config_id,status,progress,progress_message,message_code,message_params,instrument,timeframe,start_date,end_date,parameters,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (payload.get("strategy_config_id"), "QUEUED", 0, "Queued", "job.queued", "{}", payload["instrument"], payload["timeframe"], payload["start_date"], payload["end_date"], json.dumps(payload["parameters"]), now, now))
            return int(cursor.lastrowid)

    def update_run(self, run_id: int, **fields: Any) -> None:
        allowed = {"status", "progress", "progress_message", "message_code", "message_params", "result", "error", "data_quality"}
        values = {key: (json.dumps(value) if key in {"message_params", "result", "data_quality"} and value is not None else value) for key, value in fields.items() if key in allowed}
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connect() as connection:
            connection.execute(f"UPDATE backtest_runs SET {assignments} WHERE id=?", (*values.values(), run_id))

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("parameters", "message_params", "result", "data_quality"):
            if item.get(key): item[key] = json.loads(item[key])
        return item

    def run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
            return self._run_dict(row) if row else None

    def run_history(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [self._run_dict(row) for row in connection.execute("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?", (limit,))]

    def save_result(self, run_id: int, result: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM backtest_trades WHERE run_id=?", (run_id,))
            connection.execute("DELETE FROM backtest_equity WHERE run_id=?", (run_id,))
            connection.executemany("""INSERT INTO backtest_trades(run_id,trade_id,payload,entry_ts,side,pnl,signal_id,strategy_version,config_hash,expected_entry_ts,expected_entry_price)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", [(run_id, index + 1, json.dumps(trade), trade["entry_ts"], trade["side"], trade["pnl"], trade.get("signal_id"), trade.get("strategy_version"), trade.get("config_hash"), trade.get("expected_entry_ts"), trade.get("expected_entry_price")) for index, trade in enumerate(result["trades"])])
            connection.executemany("INSERT INTO backtest_equity(run_id,ts,equity) VALUES(?,?,?)", [(run_id, point["ts"], point["equity"]) for point in result["equity"]])
            decisions = result.get("decisions", [])
            connection.executemany("""INSERT OR IGNORE INTO decision_signals(signal_id,source,run_id,instrument,execution_timeframe,candle_close_ts,strategy_version,config_hash,action,bias,score,decision_payload,created_at,regime,regime_version,gate_payload)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [(item["signal_id"], "BACKTEST", run_id, item["instrument"], item["execution_timeframe"], item["candle_close_ts"], item["strategy_version"], item["config_hash"], item["action"], item["bias"], item["score"], json.dumps(item), utc_now(), item.get("regime"), item.get("regime_version"), json.dumps(item.get("gate_results", []))) for item in decisions])
            connection.executemany("""INSERT INTO decision_signal_runs(signal_id,run_id,source,decision_payload,gate_payload,regime,regime_version)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(signal_id,run_id) DO UPDATE SET source=excluded.source,
                decision_payload=excluded.decision_payload,gate_payload=excluded.gate_payload,
                regime=excluded.regime,regime_version=excluded.regime_version""",
                [(item["signal_id"], run_id, "BACKTEST", json.dumps(item), json.dumps(item.get("gate_results", [])), item.get("regime"), item.get("regime_version")) for item in decisions])
            run = connection.execute("SELECT strategy_config_id,instrument,timeframe,start_date,end_date FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
            if run and run["strategy_config_id"]:
                summary = {**result["metrics"], "run_id": run_id, "instrument": run["instrument"], "timeframe": run["timeframe"], "start_date": run["start_date"], "end_date": run["end_date"]}
                connection.execute("UPDATE strategy_configs SET latest_summary=?,updated_at=? WHERE id=?", (json.dumps(summary), utc_now(), run["strategy_config_id"]))
        public_result = {key: value for key, value in result.items() if key not in {"trades", "equity", "decisions"}}
        self.update_run(run_id, status="COMPLETED", progress=100, progress_message="Completed", message_code="research.progress.completed", message_params={}, result=public_result, data_quality=result.get("data_quality"))

    def trades(self, run_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT payload FROM backtest_trades WHERE run_id=? ORDER BY trade_id", (run_id,))]

    def equity(self, run_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT ts,equity FROM backtest_equity WHERE run_id=? ORDER BY ts", (run_id,))]

    def strategies(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM strategy_configs ORDER BY updated_at DESC").fetchall()
        output = []
        for row in rows:
            item = dict(row); item["parameters"] = json.loads(item["parameters"])
            item["latest_summary"] = json.loads(item["latest_summary"]) if item.get("latest_summary") else None
            output.append(item)
        return output

    def save_strategy(self, payload: dict[str, Any], strategy_id: int | None = None) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            if strategy_id is not None and connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_lifecycle'").fetchone():
                state=connection.execute("SELECT status FROM strategy_lifecycle WHERE strategy_config_id=?",(strategy_id,)).fetchone()
                if state and state[0] in {"Qualified","Active","Watch"}:raise ValueError("Qualified or Active configurations are immutable; duplicate the strategy to create a new version.")
            if strategy_id is None:
                cursor = connection.execute("""INSERT INTO strategy_configs(name,parameters,instrument,timeframe,start_date,end_date,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (payload["name"], json.dumps(payload["parameters"]), payload.get("instrument", "BTC-USDT"), payload.get("timeframe", "15m"), payload.get("start_date"), payload.get("end_date"), now, now))
                strategy_id = int(cursor.lastrowid)
            else:
                connection.execute("""UPDATE strategy_configs SET name=?,parameters=?,instrument=?,timeframe=?,start_date=?,end_date=?,updated_at=? WHERE id=?""", (payload["name"], json.dumps(payload["parameters"]), payload.get("instrument", "BTC-USDT"), payload.get("timeframe", "15m"), payload.get("start_date"), payload.get("end_date"), now, strategy_id))
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_lifecycle'").fetchone():
                cfg=config_hash(payload["parameters"]);existing=connection.execute("SELECT id,status FROM strategy_lifecycle WHERE strategy_config_id=?",(strategy_id,)).fetchone()
                if existing:connection.execute("UPDATE strategy_lifecycle SET name=?,config_hash=?,updated_at=? WHERE id=?",(payload["name"],cfg,now,existing["id"]))
                else:
                    cursor=connection.execute("INSERT INTO strategy_lifecycle(strategy_config_id,name,strategy_version,config_hash,status,policy_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(strategy_id,payload["name"],"canonical-v4",cfg,"Draft","promotion-policy-v1",now,now));connection.execute("INSERT INTO strategy_audit_log(lifecycle_id,action,from_status,to_status,actor,evidence,created_at) VALUES(?,?,?,?,?,?,?)",(cursor.lastrowid,"CREATE",None,"Draft","admin",json.dumps({"source":"strategy configuration"}),now))
        return next(item for item in self.strategies() if item["id"] == strategy_id)

    def delete_strategy(self, strategy_id: int) -> bool:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_lifecycle'").fetchone() and connection.execute("SELECT 1 FROM strategy_lifecycle WHERE strategy_config_id=?",(strategy_id,)).fetchone():
                raise ValueError("Lifecycle-managed strategies cannot be deleted; archive them to preserve evidence and audit history.")
            cursor = connection.execute("DELETE FROM strategy_configs WHERE id=?", (strategy_id,))
            return cursor.rowcount > 0

    def save_walk_forward(self, instrument: str, timeframe: str, parameters: dict[str, Any], windows: list[dict[str, Any]], result: dict[str, Any]) -> int:
        with self.connect() as connection:
            cursor = connection.execute("INSERT INTO walk_forward_runs(instrument,timeframe,parameters,windows,result,created_at) VALUES(?,?,?,?,?,?)", (instrument, timeframe, json.dumps(parameters), json.dumps(windows), json.dumps(result), utc_now()))
            return int(cursor.lastrowid)

    def create_portfolio_run(self, parameters: dict[str, Any], job_id: int | None = None) -> int:
        with self.connect() as connection:
            cur=connection.execute("INSERT INTO portfolio_backtest_runs(job_id,status,parameters,created_at) VALUES(?,?,?,?)",(job_id,"RUNNING",json.dumps(parameters),utc_now()))
            run_id=int(cur.lastrowid)
            for instrument,weight in (parameters.get("asset_weights") or {}).items():
                connection.execute("INSERT INTO portfolio_backtest_assets(run_id,instrument,weight) VALUES(?,?,?)",(run_id,instrument,float(weight)))
            return run_id

    def save_portfolio_result(self, run_id: int, result: dict[str, Any], progress=None) -> None:
        public={k:v for k,v in result.items() if k not in {"trades","equity"}}
        exposure_by_ts={x["ts"]:x["gross"] for x in result.get("exposure_timeline",[])}
        trades=result["trades"]; equity=result["equity"]; total=max(1,len(trades)+len(equity)); saved=0
        with self.connect() as connection:
            if progress: progress("portfolio.progress.persisting_trades",{"saved":0,"total":len(trades)},0)
            for offset in range(0,len(trades),250):
                batch=trades[offset:offset+250]
                connection.executemany("INSERT INTO portfolio_backtest_trades(run_id,instrument,signal_id,entry_ts,payload) VALUES(?,?,?,?,?)",[(run_id,t["instrument"],t.get("signal_id"),t["entry_ts"],json.dumps(t)) for t in batch])
                connection.commit()
                saved+=len(batch)
                if progress: progress("portfolio.progress.persisting_trades",{"saved":min(offset+len(batch),len(trades)),"total":len(trades)},saved/total)
            if progress: progress("portfolio.progress.persisting_equity",{"saved":0,"total":len(equity)},saved/total)
            for offset in range(0,len(equity),500):
                batch=equity[offset:offset+500]
                connection.executemany("INSERT INTO portfolio_backtest_equity(run_id,ts,equity,cash,exposure) VALUES(?,?,?,?,?)",[(run_id,e["ts"],e["equity"],e.get("cash",e["equity"]),exposure_by_ts.get(e["ts"],0)) for e in batch])
                connection.commit()
                saved+=len(batch)
                if progress: progress("portfolio.progress.persisting_equity",{"saved":min(offset+len(batch),len(equity)),"total":len(equity)},saved/total)
            connection.execute("UPDATE portfolio_backtest_runs SET status='COMPLETED',result=?,completed_at=? WHERE id=?",(json.dumps(public),utc_now(),run_id))

    def fail_portfolio_run(self,run_id:int,error:str)->None:
        with self.connect() as connection:connection.execute("UPDATE portfolio_backtest_runs SET status='FAILED',error=?,completed_at=? WHERE id=?",(error[:1000],utc_now(),run_id))

    def cancel_portfolio_run(self,run_id:int)->None:
        with self.connect() as connection:connection.execute("UPDATE portfolio_backtest_runs SET status='CANCELLED',error=NULL,completed_at=? WHERE id=?",(utc_now(),run_id))

    def portfolio_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row=connection.execute("SELECT * FROM portfolio_backtest_runs WHERE id=?",(run_id,)).fetchone()
            if not row:return None
            item=dict(row); item["parameters"]=json.loads(item["parameters"]); item["result"]=json.loads(item["result"]) if item.get("result") else None
            item["trades"]=[json.loads(x[0]) for x in connection.execute("SELECT payload FROM portfolio_backtest_trades WHERE run_id=? ORDER BY entry_ts,id",(run_id,))]
            item["equity"]=[dict(x) for x in connection.execute("SELECT ts,equity,cash,exposure FROM portfolio_backtest_equity WHERE run_id=? ORDER BY ts",(run_id,))]
            return item
