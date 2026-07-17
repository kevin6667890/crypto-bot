"""SQLite persistence for historical data, strategy configs and research runs."""

from __future__ import annotations

import json
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
                CREATE TABLE IF NOT EXISTS walk_forward_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, instrument TEXT NOT NULL, timeframe TEXT NOT NULL,
                    parameters TEXT NOT NULL, windows TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_backtest_history ON backtest_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_backtest_trades_run ON backtest_trades(run_id, entry_ts);
                CREATE INDEX IF NOT EXISTS idx_historical_range ON historical_candles(instrument, timeframe, ts);
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
            """)
            self._ensure_column(connection, "paper_trades", "signal_id", "TEXT")
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
            self._ensure_column(connection, "decision_signals", "regime", "TEXT")
            self._ensure_column(connection, "decision_signals", "regime_version", "TEXT")
            self._ensure_column(connection, "decision_signals", "gate_payload", "TEXT")
            self._ensure_column(connection, "decision_signal_runs", "decision_payload", "TEXT")
            self._ensure_column(connection, "decision_signal_runs", "gate_payload", "TEXT")
            self._ensure_column(connection, "decision_signal_runs", "regime", "TEXT")
            self._ensure_column(connection, "decision_signal_runs", "regime_version", "TEXT")
            connection.execute("""INSERT OR IGNORE INTO decision_signal_runs(signal_id,run_id,source,decision_payload,gate_payload,regime,regime_version)
                SELECT signal_id,run_id,source,decision_payload,gate_payload,regime,regime_version FROM decision_signals WHERE run_id IS NOT NULL""")
            connection.execute("UPDATE backtest_runs SET status='FAILED',progress=100,progress_message='Interrupted by service restart',error='Backtest worker was interrupted by a service restart',updated_at=? WHERE status IN ('QUEUED','RUNNING')", (utc_now(),))
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

    def upsert_candles(self, instrument: str, timeframe: str, candles: list[dict[str, Any]]) -> None:
        if not candles:
            return
        with self.connect() as connection:
            connection.executemany("""INSERT OR REPLACE INTO historical_candles
                (instrument,timeframe,ts,open,high,low,close,volume,confirmed,source) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [(instrument, timeframe, int(row["ts"]), row["open"], row["high"], row["low"], row["close"], row["volume"], int(row.get("confirmed", 1)), "OKX") for row in candles])

    def candles(self, instrument: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT ts,open,high,low,close,volume,confirmed FROM historical_candles WHERE instrument=? AND timeframe=? AND ts BETWEEN ? AND ? AND confirmed=1 ORDER BY ts", (instrument, timeframe, start_ts, end_ts))]

    def candle_coverage(self, instrument: str, timeframe: str) -> tuple[int | None, int | None]:
        with self.connect() as connection:
            row = connection.execute("SELECT MIN(ts),MAX(ts) FROM historical_candles WHERE instrument=? AND timeframe=? AND confirmed=1", (instrument, timeframe)).fetchone()
            return row[0], row[1]

    def data_coverage(self) -> list[dict[str,Any]]:
        with self.connect() as connection:return [dict(row) for row in connection.execute("SELECT instrument,timeframe,COUNT(*) rows,MIN(ts) first_ts,MAX(ts) last_ts FROM historical_candles WHERE confirmed=1 GROUP BY instrument,timeframe ORDER BY instrument,timeframe")]

    def create_run(self, payload: dict[str, Any]) -> int:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute("""INSERT INTO backtest_runs(strategy_config_id,status,progress,progress_message,instrument,timeframe,start_date,end_date,parameters,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (payload.get("strategy_config_id"), "QUEUED", 0, "Queued", payload["instrument"], payload["timeframe"], payload["start_date"], payload["end_date"], json.dumps(payload["parameters"]), now, now))
            return int(cursor.lastrowid)

    def update_run(self, run_id: int, **fields: Any) -> None:
        allowed = {"status", "progress", "progress_message", "result", "error", "data_quality"}
        values = {key: (json.dumps(value) if key in {"result", "data_quality"} and value is not None else value) for key, value in fields.items() if key in allowed}
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connect() as connection:
            connection.execute(f"UPDATE backtest_runs SET {assignments} WHERE id=?", (*values.values(), run_id))

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("parameters", "result", "data_quality"):
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
        self.update_run(run_id, status="COMPLETED", progress=100, progress_message="Completed", result=public_result, data_quality=result.get("data_quality"))

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

    def save_portfolio_result(self, run_id: int, result: dict[str, Any]) -> None:
        public={k:v for k,v in result.items() if k not in {"trades","equity"}}
        with self.connect() as connection:
            connection.executemany("INSERT INTO portfolio_backtest_trades(run_id,instrument,signal_id,entry_ts,payload) VALUES(?,?,?,?,?)",[(run_id,t["instrument"],t.get("signal_id"),t["entry_ts"],json.dumps(t)) for t in result["trades"]])
            connection.executemany("INSERT INTO portfolio_backtest_equity(run_id,ts,equity,cash,exposure) VALUES(?,?,?,?,?)",[(run_id,e["ts"],e["equity"],e.get("cash",e["equity"]),next((x["gross"] for x in result.get("exposure_timeline",[]) if x["ts"]==e["ts"]),0)) for e in result["equity"]])
            connection.execute("UPDATE portfolio_backtest_runs SET status='COMPLETED',result=?,completed_at=? WHERE id=?",(json.dumps(public),utc_now(),run_id))

    def fail_portfolio_run(self,run_id:int,error:str)->None:
        with self.connect() as connection:connection.execute("UPDATE portfolio_backtest_runs SET status='FAILED',error=?,completed_at=? WHERE id=?",(error[:1000],utc_now(),run_id))

    def portfolio_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row=connection.execute("SELECT * FROM portfolio_backtest_runs WHERE id=?",(run_id,)).fetchone()
            if not row:return None
            item=dict(row); item["parameters"]=json.loads(item["parameters"]); item["result"]=json.loads(item["result"]) if item.get("result") else None
            item["trades"]=[json.loads(x[0]) for x in connection.execute("SELECT payload FROM portfolio_backtest_trades WHERE run_id=? ORDER BY entry_ts,id",(run_id,))]
            item["equity"]=[dict(x) for x in connection.execute("SELECT ts,equity,cash,exposure FROM portfolio_backtest_equity WHERE run_id=? ORDER BY ts",(run_id,))]
            return item
