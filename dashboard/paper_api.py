"""Always-on OKX multi-asset paper engine and research API."""

from __future__ import annotations

import asyncio
import json
import hmac
import os
import queue
import random
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import deque
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import websockets

try:
    from research_service import ResearchService
    from strategy_rules import StrategyParameters, calculate_indicators, validate_parameters
    from decision_engine import FlowContext, MarketContext, RiskContext, TimeframeContext, evaluate_decision, LIVE_STRATEGY_VERSION
    from alert_service import AlertService
    from health_service import HealthService, configure_logging, log_event
    from rate_limit import RateLimiter
    from validation_service import ValidationService
    from shadow_service import ShadowService
    from lifecycle_service import LifecycleService
    from volume_profile import calculate_trade_volume_profile, calculate_volume_profile
except ImportError:
    from .research_service import ResearchService
    from .strategy_rules import StrategyParameters, calculate_indicators, validate_parameters
    from .decision_engine import FlowContext, MarketContext, RiskContext, TimeframeContext, evaluate_decision, LIVE_STRATEGY_VERSION
    from .alert_service import AlertService
    from .health_service import HealthService, configure_logging, log_event
    from .rate_limit import RateLimiter
    from .validation_service import ValidationService
    from .shadow_service import ShadowService
    from .lifecycle_service import LifecycleService
    from .volume_profile import calculate_trade_volume_profile, calculate_volume_profile

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data_cache" / "paper_trades.db"
INSTRUMENTS = ("BTC-USDT", "ETH-USDT")
MAX_OPEN_POSITIONS = 3
MAX_DAILY_LOSS_R = -2.0
MAX_CONSECUTIVE_LOSSES = 3
COOLDOWN_HOURS = 4
INITIAL_CAPITAL_USDT = 10_000.0
RISK_PER_TRADE = 0.01
AI_BRIEF_INTERVAL_SECONDS = 3600
AI_STALE_AFTER_SECONDS = 7200
AI_RETRY_BASE_SECONDS = 60
AI_RETRY_MAX_SECONDS = 3600
FLOW_RETENTION_SECONDS = 7 * 86400
FLOW_DISPLAY_WINDOW_SECONDS = 6 * 3600
VPVR_WINDOW_SECONDS = 24 * 3600
VPVR_MIN_COVERAGE_SECONDS = 15 * 60
VPVR_TIMEFRAME_CONFIG = {"1m": ("1m", 300), "5m": ("5m", 288), "15m": ("15m", 96), "1H": ("1H", 168), "4H": ("4H", 180), "1D": ("1D", 180)}
OI_SAMPLE_SECONDS = 15
FLOW_DECISION_WINDOW_SECONDS = 15 * 60
FLOW_MIN_COVERAGE_SECONDS = 10 * 60
FLOW_MAX_STALENESS_SECONDS = 45
FLOW_MIN_TRADES = 100
FLOW_MIN_OI_SAMPLES = 10
FLOW_STALE_AFTER_SECONDS = 90
FLOW_WS_READ_TIMEOUT_SECONDS = 60
FLOW_RECONNECT_MAX_SECONDS = 30

load_dotenv(ROOT / ".env")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PaperFlowCollectorManager:
    """Owns the public flow collectors for the lifetime of one PaperService.

    It deliberately has no import-time side effects: the HTTP application's
    ``run`` function starts it and its ``finally`` block stops it.
    """
    def __init__(self, service: "PaperService") -> None:
        self.service = service
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._lock = threading.Lock()
        self.started_at: str | None = None
        self.shutting_down = False
        self._last_prune = 0.0
        self.trades = {"status": "OFFLINE", "connected_at": None, "last_message_at": None, "last_trade_at": None, "reconnect_count": 0, "last_error": None, "subscription_acknowledged": False}
        self.oi = {"status": "OFFLINE", "last_success_at": None, "last_error": None}

    def _set(self, target: dict[str, Any], **values: Any) -> None:
        with self._lock:
            target.update(values)

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self.shutting_down = False
            self.started_at = now_iso()
            self.trades.update({"status": "STARTING", "last_error": None, "subscription_acknowledged": False})
            self.oi.update({"status": "STARTING", "last_error": None})
            self._thread = threading.Thread(target=self._run, name="paper-flow-collectors")
            self._thread.start()
            return True

    def stop(self, timeout: float = 10) -> None:
        with self._lock:
            self.shutting_down = True
            loop, stop, thread = self._loop, self._stop, self._thread
        if loop and stop:
            loop.call_soon_threadsafe(stop.set)
        if thread and thread.is_alive():
            thread.join(timeout)
        self.service._flush_flow_buckets()
        self._set(self.trades, status="OFFLINE", subscription_acknowledged=False)
        self._set(self.oi, status="OFFLINE")

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as error:
            self._set(self.trades, status="ERROR", last_error=f"collector manager: {type(error).__name__}")
            self._set(self.oi, status="ERROR", last_error=f"collector manager: {type(error).__name__}")

    async def _main(self) -> None:
        self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
        tasks = [asyncio.create_task(self._trades_loop(), name="flow-trades"), asyncio.create_task(self._oi_loop(), name="flow-oi"), asyncio.create_task(self._flush_loop(), name="flow-flush")]
        try:
            await self._stop.wait()
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _trades_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            socket = None
            received_data = False
            try:
                self._set(self.trades, status="CONNECTING", subscription_acknowledged=False)
                socket = await websockets.connect("wss://ws.okx.com:8443/ws/v5/business", ping_interval=20, ping_timeout=20)
                self._set(self.trades, connected_at=now_iso(), status="CONNECTING")
                await socket.send(json.dumps({"op": "subscribe", "args": [{"channel": "trades-all", "instId": item} for item in INSTRUMENTS]}))
                while not self._stop.is_set():
                    raw = await asyncio.wait_for(socket.recv(), timeout=FLOW_WS_READ_TIMEOUT_SECONDS)
                    message = json.loads(raw)
                    event = message.get("event")
                    if event == "error":
                        raise RuntimeError(str(message.get("msg") or "OKX subscription rejected"))
                    if event == "subscribe":
                        self._set(self.trades, subscription_acknowledged=True, status="CONNECTING", last_message_at=now_iso())
                        continue
                    argument = message.get("arg", {})
                    instrument = argument.get("instId")
                    if argument.get("channel") != "trades-all" or instrument not in INSTRUMENTS:
                        continue
                    self._set(self.trades, subscription_acknowledged=True, status="LIVE", last_message_at=now_iso(), last_error=None)
                    for trade in message.get("data", []):
                        self.service._ingest_flow_trade(instrument, trade)
                        received_data = True
                        self._set(self.trades, last_trade_at=now_iso())
                break
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._set(self.trades, status="ERROR", last_error=f"{type(error).__name__}: {str(error)[:160]}", subscription_acknowledged=False, reconnect_count=int(self.trades["reconnect_count"]) + 1)
                await asyncio.sleep(min(FLOW_RECONNECT_MAX_SECONDS, backoff) + random.uniform(0, 0.25))
                backoff = min(FLOW_RECONNECT_MAX_SECONDS, backoff * 2)
            finally:
                if socket:
                    await socket.close()
            # A connection that actually delivered data resets exponential backoff.
            if received_data:
                backoff = 1.0

    async def _oi_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.service._sample_oi)
                self._set(self.oi, status="LIVE", last_success_at=now_iso(), last_error=None)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._set(self.oi, status="ERROR", last_error=f"{type(error).__name__}: {str(error)[:160]}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=OI_SAMPLE_SECONDS)
            except TimeoutError:
                pass

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.service._flush_flow_buckets)
                if time.time() - self._last_prune > 86400:
                    await asyncio.to_thread(self.service._prune_flow_retention)
                    self._last_prune = time.time()
            except Exception as error:
                self._set(self.trades, status="ERROR", last_error=f"database write: {type(error).__name__}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=5)
            except TimeoutError:
                pass

    def health(self) -> dict[str, Any]:
        now = int(time.time())
        with self._lock:
            trades, oi = dict(self.trades), dict(self.oi)
        latest = trades.get("last_trade_at")
        if trades["status"] == "LIVE" and latest:
            try:
                if now - int(datetime.fromisoformat(latest).timestamp()) > FLOW_STALE_AFTER_SECONDS:
                    trades["status"] = "STALE"
            except ValueError:
                pass
        return {"paper_api_status": "RUNNING" if not self.shutting_down else "STOPPING", "process_start_time": self.started_at, "trades_collector": trades, "oi_collector": oi, "display_window_seconds": FLOW_DISPLAY_WINDOW_SECONDS, "retention_seconds": FLOW_RETENTION_SECONDS}


class PaperService:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        self.last_analysis: dict[str, dict[str, Any]] = {
            instrument: {"instrument": instrument, "status": "Starting", "action": "WAIT", "score": 0, "updated_at": now_iso()}
            for instrument in INSTRUMENTS
        }
        self.last_ai_at = {instrument: 0.0 for instrument in INSTRUMENTS}
        self.scheduler_running=False; self.last_cycle_started_at=None; self.last_cycle_completed_at=None; self.last_cycle_duration_ms=None; self.next_cycle_at=None; self.last_okx_success=None; self.last_okx_error=None; self.last_ai_success=None
        self._ai_lock = threading.Lock()
        self._ai_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._ai_workers_started = False
        self._flow_lock = threading.Lock()
        self._flow_buckets: dict[tuple[str, int], list[float]] = {}
        self._flow_price_buckets: dict[tuple[str, int, float], list[float]] = {}
        self._seen_trade_ids: set[tuple[str, str]] = set()
        self._seen_trade_order: deque[tuple[str, str]] = deque(maxlen=100_000)
        self._started_at = time.time()
        self.ai_state = self._load_ai_state()
        self.flow_collectors = PaperFlowCollectorManager(self)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, instrument TEXT NOT NULL, side TEXT NOT NULL,
                entry REAL NOT NULL, stop_loss REAL NOT NULL, take_profit REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN', exit_price REAL, pnl_r REAL, reason TEXT,
                created_at TEXT NOT NULL, closed_at TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS paper_account (
                account_id INTEGER PRIMARY KEY CHECK(account_id=1), initial_capital REAL NOT NULL,
                cash REAL NOT NULL, realized_pnl REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            conn.execute("INSERT OR IGNORE INTO paper_account(account_id,initial_capital,cash,realized_pnl,created_at,updated_at) VALUES(1,?,?,0,?,?)", (INITIAL_CAPITAL_USDT, INITIAL_CAPITAL_USDT, now_iso(), now_iso()))
            conn.execute("""CREATE TABLE IF NOT EXISTS analysis_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, payload TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS ai_briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS flow_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, oi REAL, cvd REAL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS ai_health (
                instrument TEXT PRIMARY KEY, last_success_at TEXT, last_attempt_at TEXT, last_error TEXT,
                failure_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT, updated_at TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS flow_trade_buckets (
                instrument TEXT NOT NULL, ts INTEGER NOT NULL, buy_notional REAL NOT NULL DEFAULT 0,
                sell_notional REAL NOT NULL DEFAULT 0, trade_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(instrument, ts))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS flow_price_buckets (
                instrument TEXT NOT NULL, ts INTEGER NOT NULL, price REAL NOT NULL,
                buy_notional REAL NOT NULL DEFAULT 0, sell_notional REAL NOT NULL DEFAULT 0,
                trade_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(instrument, ts, price))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS oi_snapshots (
                instrument TEXT NOT NULL, ts INTEGER NOT NULL, oi REAL NOT NULL,
                source TEXT NOT NULL, PRIMARY KEY(instrument, ts))""")
            self._ensure_column(conn, "analysis_snapshots", "instrument", "TEXT")
            self._ensure_column(conn, "ai_briefs", "instrument", "TEXT")
            self._ensure_column(conn, "flow_snapshots", "instrument", "TEXT")
            for column, declaration in (("trade_count", "INTEGER"), ("window_seconds", "INTEGER"), ("last_trade_ts", "INTEGER")):
                self._ensure_column(conn, "flow_snapshots", column, declaration)
            for column,declaration in (("signal_id","TEXT"),("strategy_version","TEXT"),("config_hash","TEXT"),("expected_entry_price","REAL"),("observed_entry_price","REAL"),("execution_delay_ms","INTEGER"),("observed_slippage_pct","REAL"),("candle_close_ts","INTEGER"),("signal_score","REAL")):
                self._ensure_column(conn,"paper_trades",column,declaration)
            for column, declaration in (("position_size", "REAL"), ("entry_notional", "REAL"), ("risk_amount", "REAL"), ("mark_price", "REAL"), ("pnl_usdt", "REAL")):
                self._ensure_column(conn, "paper_trades", column, declaration)
            conn.execute("""CREATE TABLE IF NOT EXISTS decision_signals(id INTEGER PRIMARY KEY AUTOINCREMENT,signal_id TEXT NOT NULL UNIQUE,source TEXT NOT NULL,run_id INTEGER,instrument TEXT NOT NULL,execution_timeframe TEXT NOT NULL,candle_close_ts INTEGER NOT NULL,strategy_version TEXT NOT NULL,config_hash TEXT NOT NULL,action TEXT NOT NULL,bias TEXT NOT NULL,score REAL NOT NULL,decision_payload TEXT NOT NULL,created_at TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS event_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, instrument TEXT NOT NULL,
                event_type TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')""")
            conn.execute("""CREATE TABLE IF NOT EXISTS market_candles (
                instrument TEXT NOT NULL, bar TEXT NOT NULL, ts INTEGER NOT NULL, open REAL NOT NULL,
                high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
                PRIMARY KEY(instrument, bar, ts))""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analysis_instrument ON analysis_snapshots(instrument, id DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_instrument ON event_logs(instrument, id DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_instrument_created ON flow_snapshots(instrument, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_health_updated ON ai_health(updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_trade_buckets_time ON flow_trade_buckets(instrument, ts DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_price_buckets_time ON flow_price_buckets(instrument, ts DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_oi_snapshots_time ON oi_snapshots(instrument, ts DESC)")

    def _load_ai_state(self) -> dict[str, dict[str, Any]]:
        states = {instrument: {"failure_count": 0, "last_error": None, "last_success_at": None, "next_retry_at": 0.0, "queued": False} for instrument in INSTRUMENTS}
        with self._connect() as conn:
            rows = conn.execute("SELECT instrument,last_success_at,last_error,failure_count,next_retry_at FROM ai_health").fetchall()
        for row in rows:
            if row["instrument"] not in states:
                continue
            retry_at = 0.0
            try:
                retry_at = datetime.fromisoformat(row["next_retry_at"]).timestamp() if row["next_retry_at"] else 0.0
            except (TypeError, ValueError):
                pass
            states[row["instrument"]].update({"failure_count": int(row["failure_count"] or 0), "last_error": row["last_error"], "last_success_at": row["last_success_at"], "next_retry_at": retry_at})
        successful = [state["last_success_at"] for state in states.values() if state["last_success_at"]]
        self.last_ai_success = max(successful) if successful else None
        return states

    def _save_ai_state(self, instrument: str, state: dict[str, Any], attempted_at: str) -> None:
        retry_at = datetime.fromtimestamp(state["next_retry_at"], timezone.utc).replace(microsecond=0).isoformat() if state["next_retry_at"] else None
        with self._connect() as conn:
            conn.execute("""INSERT INTO ai_health(instrument,last_success_at,last_attempt_at,last_error,failure_count,next_retry_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(instrument) DO UPDATE SET last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,last_error=excluded.last_error,failure_count=excluded.failure_count,next_retry_at=excluded.next_retry_at,updated_at=excluded.updated_at""", (instrument, state["last_success_at"], attempted_at, state["last_error"], state["failure_count"], retry_at, now_iso()))

    @staticmethod
    def _json(url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "crypto-bot-paper-research/2.0"})
        with urlopen(request, timeout=12) as response:  # noqa: S310 - fixed OKX/DeepSeek URLs
            return json.loads(response.read().decode("utf-8"))

    def _candles(self, instrument: str, bar: str, limit: int = 300) -> list[dict[str, float]]:
        payload = self._json(f"https://www.okx.com/api/v5/market/candles?instId={instrument}&bar={bar}&limit={limit}")
        seconds={"1m":60,"5m":300,"15m":900,"1H":3600,"4H":14400,"1D":86400}.get(bar,0)
        candles = [{"ts": int(row[0]) // 1000, "candle_close_ts": int(row[0]) // 1000 + seconds, "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]), "confirmed": bool(int(row[8])) if len(row)>8 else True} for row in payload.get("data", [])]
        return list(reversed(candles))

    def _price(self, instrument: str) -> float:
        payload = self._json(f"https://www.okx.com/api/v5/market/ticker?instId={instrument}")
        return float(payload["data"][0]["last"])

    def _event(self, instrument: str, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO event_logs(created_at,instrument,event_type,message,payload) VALUES(?,?,?,?,?)", (now_iso(), instrument, event_type, message, json.dumps(payload or {}, ensure_ascii=False)))

    def _flow_metrics(self, instrument: str) -> dict[str, Any]:
        trades = self._json(f"https://www.okx.com/api/v5/market/trades?instId={instrument}&limit=100").get("data", [])
        cumulative, per_second = 0.0, {}
        trade_timestamps: list[int] = []
        for row in reversed(trades):
            cumulative += float(row["sz"]) * float(row["px"]) * (1 if row.get("side") == "buy" else -1)
            # Lightweight Charts requires strictly increasing, unique timestamps.
            # OKX often returns several trades in one second, so retain the last
            # cumulative value for each second rather than emitting duplicates.
            timestamp = int(row["ts"]) // 1000
            trade_timestamps.append(timestamp)
            per_second[timestamp] = round(cumulative, 2)
        series = [{"time": timestamp, "value": value} for timestamp, value in sorted(per_second.items())]
        swap = instrument.replace("-USDT", "-USDT-SWAP")
        oi_row = self._json(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={swap}").get("data", [{}])[0]
        oi = float(oi_row.get("oiUsd") or oi_row.get("oi") or 0)
        with self._connect() as conn:
            previous = conn.execute("SELECT oi FROM flow_snapshots WHERE instrument=? ORDER BY id DESC LIMIT 1", (instrument,)).fetchone()
            conn.execute("INSERT INTO flow_snapshots(created_at,instrument,oi,cvd,trade_count,window_seconds,last_trade_ts) VALUES(?,?,?,?,?,?,?)", (now_iso(), instrument, oi, cumulative, len(trade_timestamps), max(trade_timestamps) - min(trade_timestamps) if trade_timestamps else 0, max(trade_timestamps) if trade_timestamps else None))
            history = [dict(row) for row in conn.execute("SELECT created_at,oi,cvd FROM flow_snapshots WHERE instrument=? ORDER BY id DESC LIMIT 60", (instrument,))]
        previous_oi = float(previous["oi"]) if previous and previous["oi"] else oi
        decision_flow = self._decision_flow(instrument)
        return {
            # ``cvd_delta`` is the public/chart aggregate represented by
            # ``cvd_series``.  The readiness-gated decision value is exposed
            # separately so an incomplete live window cannot erase the chart.
            "cvd": round(cumulative, 2), "cvd_delta": round(cumulative, 2), "decision_cvd_delta": decision_flow["cvd_delta"], "cvd_series": series,
            "oi": oi, "oi_change_pct": decision_flow["oi_change_pct"] if decision_flow["oi_change_pct"] is not None else 0.0, "decision_oi_change_pct": decision_flow["oi_change_pct"], "oi_history": list(reversed(history)), "source": decision_flow["source"],
            "quality": {"trade_count": len(trade_timestamps), "window_seconds": max(trade_timestamps) - min(trade_timestamps) if trade_timestamps else 0, "last_trade_ts": max(trade_timestamps) if trade_timestamps else None, "sampled_at": now_iso()},
            "decision_quality": decision_flow["quality"], "professional": self._professional_flow(instrument),
        }

    def _decision_flow(self, instrument: str) -> dict[str, Any]:
        """Use one confirmed 15-minute WebSocket/REST window for live gates."""
        now = int(time.time())
        since = now - FLOW_DECISION_WINDOW_SECONDS
        with self._connect() as conn:
            trades = conn.execute("SELECT MIN(ts) AS first_ts,MAX(ts) AS last_ts,SUM(buy_notional-sell_notional) AS delta,SUM(trade_count) AS trade_count FROM flow_trade_buckets WHERE instrument=? AND ts>=?", (instrument, since)).fetchone()
            oi_rows = conn.execute("SELECT ts,oi FROM oi_snapshots WHERE instrument=? AND ts>=? ORDER BY ts", (instrument, since)).fetchall()
        first_ts, last_ts = int(trades["first_ts"] or 0), int(trades["last_ts"] or 0)
        trade_count = int(trades["trade_count"] or 0)
        coverage = max(0, last_ts - first_ts)
        last_trade_age = now - last_ts if last_ts else None
        last_oi_age = now - int(oi_rows[-1]["ts"]) if oi_rows else None
        ready = coverage >= FLOW_MIN_COVERAGE_SECONDS and trade_count >= FLOW_MIN_TRADES and last_trade_age is not None and last_trade_age <= FLOW_MAX_STALENESS_SECONDS and len(oi_rows) >= FLOW_MIN_OI_SAMPLES and last_oi_age is not None and last_oi_age <= FLOW_MAX_STALENESS_SECONDS
        oi_change = (float(oi_rows[-1]["oi"]) / float(oi_rows[0]["oi"]) - 1) * 100 if len(oi_rows) >= 2 and oi_rows[0]["oi"] else None
        return {"cvd_delta": round(float(trades["delta"] or 0), 2) if ready else 0.0, "oi_change_pct": round(oi_change, 4) if ready and oi_change is not None else None, "source": "OKX trades-all WebSocket + 15s SWAP OI (15m decision window)", "quality": {"ready": ready, "window_seconds": FLOW_DECISION_WINDOW_SECONDS, "coverage_seconds": coverage, "trade_count": trade_count, "last_trade_age_seconds": last_trade_age, "oi_samples": len(oi_rows), "last_oi_age_seconds": last_oi_age}}

    def _professional_flow(self, instrument: str) -> dict[str, Any]:
        window_end = int(time.time())
        since = window_end - FLOW_DISPLAY_WINDOW_SECONDS
        with self._connect() as conn:
            rows = conn.execute("SELECT (ts / 60) * 60 AS minute, SUM(buy_notional-sell_notional) AS delta, SUM(trade_count) AS trades FROM flow_trade_buckets WHERE instrument=? AND ts>=? GROUP BY minute ORDER BY minute", (instrument, since)).fetchall()
            oi_rows = conn.execute("SELECT ts,oi FROM oi_snapshots WHERE instrument=? AND ts>=? ORDER BY ts", (instrument, since)).fetchall()
        cumulative = 0.0
        cvd_series = []
        for row in rows:
            cumulative += float(row["delta"] or 0)
            cvd_series.append({"time": int(row["minute"]), "value": round(cumulative, 2), "delta": round(float(row["delta"] or 0), 2), "trades": int(row["trades"] or 0)})
        oi_series = [{"time": int(row["ts"]), "value": float(row["oi"] or 0)} for row in oi_rows]
        coverage = (cvd_series[-1]["time"] - cvd_series[0]["time"] + 60) if len(cvd_series) > 1 else 0
        gaps = [cvd_series[index]["time"] - cvd_series[index - 1]["time"] for index in range(1, len(cvd_series))]
        latest_trade_ts = cvd_series[-1]["time"] if cvd_series else None
        stale = latest_trade_ts is None or window_end - latest_trade_ts > FLOW_STALE_AFTER_SECONDS
        collector_status = self.flow_collectors.health()["trades_collector"]["status"]
        flow_ready = bool(cvd_series) and not stale and coverage >= FLOW_DISPLAY_WINDOW_SECONDS
        if collector_status == "LIVE" and not flow_ready:
            collector_status = "PARTIAL"
        return {"available": bool(cvd_series) and not stale, "window_seconds": FLOW_DISPLAY_WINDOW_SECONDS, "window_start_ts": since, "window_end_ts": window_end, "coverage_start_ts": cvd_series[0]["time"] if cvd_series else None, "coverage_end_ts": latest_trade_ts, "coverage_seconds": coverage, "coverage_ratio": round(min(1.0, coverage / FLOW_DISPLAY_WINDOW_SECONDS), 4), "latest_trade_ts": latest_trade_ts, "bucket_count": len(cvd_series), "collector_status": collector_status, "flow_ready": flow_ready, "stale": stale, "cvd": round(cumulative, 2), "cvd_series": cvd_series, "oi_series": oi_series, "source": "OKX public WebSocket trades + periodic SWAP OI", "scoring_mode": "unchanged", "quality": {"last_trade_age_seconds": max(0, window_end - latest_trade_ts) if latest_trade_ts else None, "gap_count": sum(gap > 120 for gap in gaps), "max_gap_seconds": max(gaps, default=0), "oi_samples": len(oi_series)}}

    def _professional_vpvr(self, instrument: str, bins: int = 32, price_low: float | None = None, price_high: float | None = None) -> dict[str, Any]:
        since = int(time.time()) - VPVR_WINDOW_SECONDS
        range_sql, range_args = "", []
        if price_low is not None: range_sql += " AND price>=?"; range_args.append(price_low)
        if price_high is not None: range_sql += " AND price<=?"; range_args.append(price_high)
        with self._connect() as conn:
            rows = [dict(row) for row in conn.execute(f"SELECT price,SUM(buy_notional) AS buy_notional,SUM(sell_notional) AS sell_notional,SUM(trade_count) AS trade_count FROM flow_price_buckets WHERE instrument=? AND ts>=?{range_sql} GROUP BY price ORDER BY price", (instrument, since, *range_args))]
            bounds = conn.execute(f"SELECT MIN(ts) AS first_ts,MAX(ts) AS last_ts,SUM(trade_count) AS trade_count FROM flow_price_buckets WHERE instrument=? AND ts>=?{range_sql}", (instrument, since, *range_args)).fetchone()
        coverage = max(0, int(bounds["last_ts"] or 0) - int(bounds["first_ts"] or 0)) if bounds else 0
        profile = calculate_trade_volume_profile(rows, bins=bins, price_low=price_low, price_high=price_high)
        profile.update({"source": "OKX trades-all WebSocket", "window_seconds": VPVR_WINDOW_SECONDS, "coverage_seconds": coverage, "trade_count": int(bounds["trade_count"] or 0) if bounds else 0, "ready": bool(profile.get("available")) and coverage >= VPVR_MIN_COVERAGE_SECONDS})
        if not profile["ready"]:
            profile["reason"] = "collecting_trade_coverage" if profile.get("available") else profile.get("reason")
        return profile

    def vpvr_profile(self, instrument: str, interval: str = "15m", bins: int = 32, price_low: float | None = None, price_high: float | None = None) -> dict[str, Any]:
        """Return a display-only VPVR matched to the selected chart timeframe."""
        interval = {"1h": "1H", "4h": "4H", "1d": "1D"}.get(interval, interval)
        bar, lookback = VPVR_TIMEFRAME_CONFIG.get(interval, VPVR_TIMEFRAME_CONFIG["15m"])
        if bar == "15m":
            streamed = self._professional_vpvr(instrument, bins=bins, price_low=price_low, price_high=price_high)
            if streamed.get("ready"):
                return {**streamed, "professional": True, "interval": bar}
        candles = [row for row in self._candles(instrument, bar, lookback) if row.get("confirmed", True)]
        profile = calculate_volume_profile(candles[-lookback:], bins=bins, price_low=price_low, price_high=price_high)
        profile.update({"source": "confirmed_ohlcv_fallback", "professional": False, "interval": bar, "lookback_bars": min(len(candles), lookback)})
        if bar == "15m":
            streamed = self._professional_vpvr(instrument, bins=bins, price_low=price_low, price_high=price_high)
            profile["collection"] = {"coverage_seconds": streamed.get("coverage_seconds", 0), "trade_count": streamed.get("trade_count", 0), "reason": streamed.get("reason")}
        return profile

    def _ingest_flow_trade(self, instrument: str, payload: dict[str, Any]) -> None:
        try:
            timestamp = int(payload["ts"]) // 1000
            price = float(payload["px"])
            notional = price * float(payload["sz"])
            side = str(payload.get("side", "")).lower()
        except (KeyError, TypeError, ValueError):
            return
        if side not in {"buy", "sell"}:
            return
        with self._flow_lock:
            trade_id = str(payload.get("tradeId") or "")
            identity = (instrument, trade_id)
            if trade_id and identity in self._seen_trade_ids:
                return
            if trade_id:
                if len(self._seen_trade_order) == self._seen_trade_order.maxlen:
                    self._seen_trade_ids.discard(self._seen_trade_order.popleft())
                self._seen_trade_order.append(identity)
                self._seen_trade_ids.add(identity)
            bucket = self._flow_buckets.setdefault((instrument, timestamp), [0.0, 0.0, 0.0])
            bucket[0 if side == "buy" else 1] += notional
            bucket[2] += 1
            price_bucket = self._flow_price_buckets.setdefault((instrument, timestamp, price), [0.0, 0.0, 0.0])
            price_bucket[0 if side == "buy" else 1] += notional
            price_bucket[2] += 1

    def _flush_flow_buckets(self) -> None:
        with self._flow_lock:
            buckets, self._flow_buckets = self._flow_buckets, {}
            price_buckets, self._flow_price_buckets = self._flow_price_buckets, {}
        if not buckets and not price_buckets:
            return
        values = [(instrument, ts, value[0], value[1], int(value[2])) for (instrument, ts), value in buckets.items()]
        price_values = [(instrument, ts, price, value[0], value[1], int(value[2])) for (instrument, ts, price), value in price_buckets.items()]
        try:
            with self._connect() as conn:
                if values:
                    conn.executemany("""INSERT INTO flow_trade_buckets(instrument,ts,buy_notional,sell_notional,trade_count) VALUES(?,?,?,?,?)
                        ON CONFLICT(instrument,ts) DO UPDATE SET buy_notional=buy_notional+excluded.buy_notional,sell_notional=sell_notional+excluded.sell_notional,trade_count=trade_count+excluded.trade_count""", values)
                if price_values:
                    conn.executemany("""INSERT INTO flow_price_buckets(instrument,ts,price,buy_notional,sell_notional,trade_count) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(instrument,ts,price) DO UPDATE SET buy_notional=buy_notional+excluded.buy_notional,sell_notional=sell_notional+excluded.sell_notional,trade_count=trade_count+excluded.trade_count""", price_values)
        except Exception:
            with self._flow_lock:
                for key, value in buckets.items():
                    previous = self._flow_buckets.setdefault(key, [0.0, 0.0, 0.0]); previous[0] += value[0]; previous[1] += value[1]; previous[2] += value[2]
                for key, value in price_buckets.items():
                    previous = self._flow_price_buckets.setdefault(key, [0.0, 0.0, 0.0]); previous[0] += value[0]; previous[1] += value[1]; previous[2] += value[2]
            raise

    def start_flow_collector(self) -> None:
        self.flow_collectors.start()

    def stop_flow_collector(self) -> None:
        self.flow_collectors.stop()

    def _prune_flow_retention(self) -> None:
        """Keep long retention separate from the six-hour query window."""
        with self._connect() as conn:
            cutoff = int(time.time()) - FLOW_RETENTION_SECONDS
            conn.execute("DELETE FROM flow_trade_buckets WHERE ts<?", (cutoff,))
            conn.execute("DELETE FROM flow_price_buckets WHERE ts<?", (cutoff,))
            conn.execute("DELETE FROM oi_snapshots WHERE ts<?", (cutoff,))

    def _sample_oi(self) -> None:
        for instrument in INSTRUMENTS:
            swap = instrument.replace("-USDT", "-USDT-SWAP")
            row = self._json(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={swap}").get("data", [{}])[0]
            oi = float(row.get("oiUsd") or row.get("oi") or 0)
            if oi <= 0:
                raise ValueError("OKX returned no usable OI")
            with self._connect() as conn:
                conn.execute("INSERT OR REPLACE INTO oi_snapshots(instrument,ts,oi,source) VALUES(?,?,?,?)", (instrument, int(time.time()), oi, "OKX REST public/open-interest"))

    def flow_health(self) -> dict[str, Any]:
        result = self.flow_collectors.health()
        result["database_path_identifier"] = self.db_path.name
        result["instruments"] = {instrument: self._professional_flow(instrument) for instrument in INSTRUMENTS}
        return result

    def _store_candles(self, instrument: str, candles: list[dict[str, float]]) -> None:
        with self._connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO market_candles(instrument,bar,ts,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)", [(instrument, "15m", row["ts"], row["open"], row["high"], row["low"], row["close"], row["volume"]) for row in candles])

    def analyze(self, instrument: str, flow: dict[str, Any]) -> dict[str, Any]:
        """Build causal live MTF context and call the canonical decision engine."""
        datasets={bar:[row for row in self._candles(instrument,bar,300) if row.get("confirmed",True)] for bar in ("15m","1H","4H","1D")}
        c15=datasets["15m"]
        if not c15: raise RuntimeError("No confirmed 15m candle is available")
        professional_flow = flow.get("professional") or {}
        oi_series = professional_flow.get("oi_series") or []
        if professional_flow.get("available") and len(c15) >= 5 and len(oi_series) >= 2:
            price_change = (float(c15[-1]["close"]) / float(c15[-5]["close"]) - 1) * 100
            oi_change = (float(oi_series[-1]["value"]) / float(oi_series[0]["value"]) - 1) * 100 if oi_series[0].get("value") else 0.0
            state = "多头增仓" if price_change >= 0 and oi_change >= 0 else "空头回补" if price_change >= 0 else "多头平仓" if oi_change <= 0 else "空头增仓"
            professional_flow["price_oi_state"] = {"label": state, "price_change_pct": round(price_change, 3), "oi_change_pct": round(oi_change, 3)}
        self._store_candles(instrument,c15)
        streamed_vpvr = self._professional_vpvr(instrument)
        if streamed_vpvr.get("ready"):
            vpvr = {**streamed_vpvr, "professional": True}
        else:
            vpvr = {**calculate_volume_profile(c15[-96:]), "source": "confirmed_ohlcv_fallback", "professional": False, "collection": {"coverage_seconds": streamed_vpvr.get("coverage_seconds", 0), "trade_count": streamed_vpvr.get("trade_count", 0), "reason": streamed_vpvr.get("reason")}}
        params,active_version=self._active_strategy(); ind15=calculate_indicators(c15,params)[-1]; execution=c15[-1]; close_ts=int(execution["candle_close_ts"])
        frames={}
        for frame in ("1H","4H","1D"):
            eligible=[row for row in datasets[frame] if int(row["candle_close_ts"])<=close_ts]
            if not eligible: continue
            values=calculate_indicators(eligible,params)[-1]; row=eligible[-1]
            frames[frame]={"candle_close_ts":int(row["candle_close_ts"]),"close":row["close"],"fast_ma":values["fast_ma"],"slow_ma":values["slow_ma"],"trend":"Bullish" if values["fast_ma"] and values["slow_ma"] and row["close"]>values["fast_ma"]>values["slow_ma"] else "Bearish" if values["fast_ma"] and values["slow_ma"] and row["close"]<values["fast_ma"]<values["slow_ma"] else "Mixed","ema20_slope_pct":0.0,"ma60":values["fast_ma"],"ma200":values["slow_ma"]}
        risk=self.risk_state(instrument)
        decision=evaluate_decision(params,MarketContext(instrument,"15m",close_ts,float(execution["close"]),ind15,"OKX","public-confirmed-live-v1"),TimeframeContext(frames,("1H","4H"),False,"multi-timeframe"),FlowContext(True,float(flow.get("decision_cvd_delta",0)),flow.get("decision_oi_change_pct"),flow.get("source")),RiskContext(bool(risk["allowed"]),tuple(risk["blockers"]),int(risk.get("open_positions",0)),0,bool(risk.get("cooldown_clear",True)),bool(risk.get("existing_position_clear",True))),active_version).to_dict()
        for item in decision["contributions"]:
            item["detail"]={"trend":"1H + 4H confirmed trend alignment","structure":"MA60 / MA200 structure","pullback":f"{decision.get('decision_input_summary',{}).get('close',0):.2f} close vs EMA20","momentum":f"Volume {ind15.get('volume_ratio') or 0:.2f}x · RSI {ind15.get('rsi') or 0:.1f}","flow":f"CVD {flow.get('cvd_delta',0):+.0f} · OI {flow.get('oi_change_pct',0):+.3f}%"}.get(item["key"],item["label"])
            item["detail_code"]=f"decision.contribution_detail.{item['key']}"
            item["detail_params"]={"close":f"{decision.get('decision_input_summary',{}).get('close',0):.2f}","volume":f"{ind15.get('volume_ratio') or 0:.2f}","rsi":f"{ind15.get('rsi') or 0:.1f}","cvd":f"{flow.get('cvd_delta',0):+.0f}","oi":f"{flow.get('oi_change_pct',0):+.3f}"}
        distance_pct=(float(execution["close"])-float(ind15["ema"]))/float(ind15["ema"])*100 if ind15.get("ema") else None
        analysis={**decision,"price":round(float(execution["close"]),4),"ema20":ind15["ema"],"rsi14":ind15["rsi"],"atr14":ind15["atr"],"volume_ratio":ind15["volume_ratio"],"distance_ema20_pct":distance_pct,"timeframes":{"15m":{"trend":decision["bias"],"ma60":ind15["fast_ma"],"ma200":ind15["slow_ma"],"ema20_slope_pct":0},**frames},"flow":flow,"vpvr":vpvr,"conditions":[{"label":x["label"],"value":x["detail"],"pass":x["status"]=="pass"} for x in decision["contributions"]],"updated_at":now_iso()}
        with self._connect() as conn:
            conn.execute("INSERT INTO analysis_snapshots(created_at,instrument,payload) VALUES(?,?,?)",(analysis["updated_at"],instrument,json.dumps(analysis)))
            conn.execute("""INSERT OR IGNORE INTO decision_signals(signal_id,source,instrument,execution_timeframe,candle_close_ts,strategy_version,config_hash,action,bias,score,decision_payload,created_at,regime,regime_version,gate_payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(decision["signal_id"],"PAPER",instrument,"15m",close_ts,decision["strategy_version"],decision["config_hash"],decision["action"],decision["bias"],decision["score"],json.dumps(decision),analysis["updated_at"],decision["regime"],decision["regime_version"],json.dumps(decision["gate_results"])))
        shadow=globals().get("SHADOW")
        if shadow:shadow.process_market(instrument,datasets,flow)
        self.last_analysis[instrument]=analysis
        return analysis

    def _active_strategy(self) -> tuple[StrategyParameters, str]:
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_lifecycle'").fetchone():return StrategyParameters(),LIVE_STRATEGY_VERSION
            row=conn.execute("SELECT sc.parameters,sl.strategy_version FROM strategy_lifecycle sl JOIN strategy_configs sc ON sc.id=sl.strategy_config_id WHERE sl.status='Active' LIMIT 1").fetchone()
        return (validate_parameters(json.loads(row["parameters"])),str(row["strategy_version"])) if row else (StrategyParameters(),LIVE_STRATEGY_VERSION)

    def risk_state(self, instrument: str) -> dict[str, Any]:
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._connect() as conn:
            open_total = conn.execute("SELECT COUNT(*) FROM paper_trades WHERE status='OPEN'").fetchone()[0]
            today_r = float(conn.execute("SELECT COALESCE(SUM(pnl_r),0) FROM paper_trades WHERE closed_at>=?", (day_start,)).fetchone()[0])
            recent = conn.execute("SELECT status,closed_at FROM paper_trades WHERE instrument=? AND status!='OPEN' ORDER BY closed_at DESC LIMIT 10", (instrument,)).fetchall()
        consecutive = 0
        for row in recent:
            if row["status"] != "LOSS":
                break
            consecutive += 1
        cooldown_until = None
        if recent and recent[0]["closed_at"]:
            cooldown_until = datetime.fromisoformat(recent[0]["closed_at"]) + timedelta(hours=COOLDOWN_HOURS)
        blockers = []
        if open_total >= MAX_OPEN_POSITIONS: blockers.append("portfolio position limit")
        if today_r <= MAX_DAILY_LOSS_R: blockers.append("daily loss limit")
        if consecutive >= MAX_CONSECUTIVE_LOSSES: blockers.append("consecutive loss limit")
        if cooldown_until and cooldown_until > datetime.now(timezone.utc): blockers.append("instrument cooldown")
        with self._connect() as conn:
            instrument_open = bool(conn.execute("SELECT 1 FROM paper_trades WHERE instrument=? AND status='OPEN'", (instrument,)).fetchone())
        alerts=globals().get("ALERTS")
        if alerts:
            for condition,kind,key,message in ((today_r<=MAX_DAILY_LOSS_R,"Daily Loss Limit",f"daily-loss|{instrument}",f"{instrument} daily paper result reached {today_r:.2f}R"),(consecutive>=MAX_CONSECUTIVE_LOSSES,"Consecutive Loss Limit",f"consecutive-loss|{instrument}",f"{instrument} reached {consecutive} consecutive paper losses")):
                if condition:alerts.raise_alert(kind,"critical","paper_risk",message,instrument,key=key)
                else:alerts.resolve(key)
        return {"allowed": not blockers, "blockers": blockers, "open_positions": open_total, "max_open_positions": MAX_OPEN_POSITIONS, "daily_pnl_r": round(today_r, 2), "daily_loss_limit_r": MAX_DAILY_LOSS_R, "consecutive_losses": consecutive, "max_consecutive_losses": MAX_CONSECUTIVE_LOSSES, "cooldown_until": cooldown_until.isoformat() if cooldown_until else None, "cooldown_clear": not (cooldown_until and cooldown_until > datetime.now(timezone.utc)), "existing_position_clear": not instrument_open}

    @staticmethod
    def _account_state(conn: sqlite3.Connection) -> dict[str, float]:
        account = conn.execute("SELECT initial_capital,cash,realized_pnl FROM paper_account WHERE account_id=1").fetchone()
        if not account:
            raise RuntimeError("Paper account is not initialized")
        open_value = float(conn.execute("SELECT COALESCE(SUM(COALESCE(mark_price,entry)*COALESCE(position_size,0)),0) FROM paper_trades WHERE status='OPEN'").fetchone()[0])
        return {"initial_capital": float(account["initial_capital"]), "cash": float(account["cash"]), "realized_pnl": float(account["realized_pnl"]), "open_value": open_value, "equity": float(account["cash"]) + open_value}

    def _open_trade(self, analysis: dict[str, Any]) -> None:
        instrument = analysis["instrument"]
        if analysis["action"] == "WAIT":
            failed = [item["label"] for item in analysis["contributions"] if item["status"] != "pass"]
            self._event(instrument, "SIGNAL_REJECTED", "Rule gates rejected entry", {"score": analysis["score"], "failed": failed})
            return
        risk = self.risk_state(instrument)
        if not risk["allowed"]:
            self._event(instrument, "RISK_BLOCKED", "Entry blocked by risk controls", risk)
            return
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM paper_trades WHERE instrument=? AND status='OPEN'", (instrument,)).fetchone():
                self._event(instrument, "DUPLICATE_BLOCKED", "Existing instrument position is still open")
                return
            params, _ = self._active_strategy()
            entry, atr, side = analysis["price"], analysis["atr14"], analysis["action"]
            risk_distance = atr * params.stop_loss_atr_multiplier
            account = self._account_state(conn)
            risk_budget = account["equity"] * params.risk_per_trade
            size = min(risk_budget / risk_distance if risk_distance else 0.0, account["cash"] / entry if entry else 0.0)
            entry_notional = entry * size
            if size <= 0 or entry_notional <= 0:
                self._event(instrument, "ACCOUNT_BLOCKED", "Unified account has no available cash for a new paper position", account)
                return
            stop, target = (entry - risk_distance, entry + risk_distance * params.risk_reward_ratio) if side == "LONG" else (entry + risk_distance, entry - risk_distance * params.risk_reward_ratio)
            observed_at=now_iso(); delay=max(0,int((datetime.now(timezone.utc).timestamp()-int(analysis["candle_close_ts"]))*1000))
            cursor = conn.execute("""INSERT INTO paper_trades(instrument,side,entry,stop_loss,take_profit,created_at,signal_id,strategy_version,config_hash,expected_entry_price,observed_entry_price,execution_delay_ms,observed_slippage_pct,candle_close_ts,signal_score,position_size,entry_notional,risk_amount,mark_price)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (instrument, side, entry, stop, target, observed_at,analysis["signal_id"],analysis["strategy_version"],analysis["config_hash"],entry,entry,delay,0.0,analysis["candle_close_ts"],analysis["score"],size,entry_notional,risk_distance * size,entry))
            conn.execute("UPDATE paper_account SET cash=cash-?,updated_at=? WHERE account_id=1", (entry_notional, observed_at))
            trade_id = cursor.lastrowid
        self._event(instrument, "TRADE_OPENED", f"Paper {side} opened", {"trade_id": trade_id, "entry": entry, "stop": stop, "target": target, "size": size, "account_equity": account["equity"], "score": analysis["score"]})

    def monitor_positions(self, instrument: str, price: float) -> None:
        closed_events: list[dict[str, Any]] = []
        with self._connect() as conn:
            positions = conn.execute("SELECT * FROM paper_trades WHERE instrument=? AND status='OPEN'", (instrument,)).fetchall()
            for row in positions:
                conn.execute("UPDATE paper_trades SET mark_price=? WHERE id=?", (price, row["id"]))
                hit_stop = price <= row["stop_loss"] if row["side"] == "LONG" else price >= row["stop_loss"]
                hit_target = price >= row["take_profit"] if row["side"] == "LONG" else price <= row["take_profit"]
                if not (hit_stop or hit_target): continue
                exit_price, reason = (row["take_profit"], "TAKE_PROFIT") if hit_target else (row["stop_loss"], "STOP_LOSS")
                risk = abs(row["entry"] - row["stop_loss"]) or 1
                pnl_r = (exit_price - row["entry"]) / risk if row["side"] == "LONG" else (row["entry"] - exit_price) / risk
                status = "WIN" if pnl_r > 0 else "LOSS"
                size = float(row["position_size"] or 0)
                pnl_usdt = ((exit_price - row["entry"]) if row["side"] == "LONG" else (row["entry"] - exit_price)) * size
                closed_at = now_iso()
                conn.execute("UPDATE paper_trades SET status=?,exit_price=?,pnl_r=?,pnl_usdt=?,reason=?,closed_at=?,mark_price=? WHERE id=?", (status, exit_price, pnl_r, pnl_usdt if size else None, reason, closed_at, exit_price, row["id"]))
                if size:
                    conn.execute("UPDATE paper_account SET cash=cash+?,realized_pnl=realized_pnl+?,updated_at=? WHERE account_id=1", (exit_price * size, pnl_usdt, closed_at))
                closed_events.append({"trade_id": row["id"], "pnl_r": pnl_r, "pnl_usdt": pnl_usdt if size else None, "exit": exit_price, "reason": reason})
        for event in closed_events:
            self._event(instrument, "TRADE_CLOSED", f"Paper trade closed by {event['reason']}", event)

    def start_ai_workers(self) -> None:
        if self._ai_workers_started:
            return
        self._ai_workers_started = True
        threading.Thread(target=self._ai_worker, daemon=True, name="ai-brief-worker").start()
        threading.Thread(target=self._ai_health_monitor, daemon=True, name="ai-health-monitor").start()

    def maybe_create_ai_brief(self, analysis: dict[str, Any]) -> None:
        instrument = analysis["instrument"]
        now = time.time()
        with self._ai_lock:
            state = self.ai_state[instrument]
            if state["queued"] or now < state["next_retry_at"] or (self.last_ai_at[instrument] and now - self.last_ai_at[instrument] < AI_BRIEF_INTERVAL_SECONDS):
                return
            state["queued"] = True
        self._ai_queue.put({"instrument": instrument, "analysis": analysis})

    def _ai_worker(self) -> None:
        while True:
            job = self._ai_queue.get()
            try:
                self._create_ai_brief(job["instrument"], job["analysis"])
            finally:
                self._ai_queue.task_done()

    def _ai_context(self, instrument: str, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a small, explicit context contract for every DeepSeek call.

        Never pass API/display payloads directly: those include multi-day CVD/OI
        series and can turn a simple question into a very large token request.
        """
        status = self.status(instrument)
        analysis = analysis or status["analysis"]
        flow = analysis.get("flow") or {}
        professional = flow.get("professional") or {}
        frames = analysis.get("timeframes") or {}
        frame_summary = {
            frame: {key: item.get(key) for key in ("trend", "close", "ma60", "ma200", "ema20_slope_pct")}
            for frame, item in frames.items()
            if frame in {"15m", "1H", "4H", "1D"}
        }
        trade_fields = ("id", "side", "status", "entry", "exit_price", "pnl_r", "pnl_usdt", "reason", "created_at", "closed_at")
        return {
            "instrument": instrument,
            "as_of": analysis.get("updated_at"),
            "decision": {key: analysis.get(key) for key in ("action", "bias", "score", "entry_allowed", "rejection_reason", "price", "ema20", "rsi14", "atr14", "volume_ratio")},
            "timeframes": frame_summary,
            "flow": {"cvd_delta": flow.get("cvd_delta"), "oi_change_pct": flow.get("oi_change_pct"), "price_oi_state": (professional.get("price_oi_state") or {}).get("label")},
            "risk": {key: status["risk"].get(key) for key in ("allowed", "blockers", "open_positions", "daily_pnl_r", "consecutive_losses", "cooldown_until")},
            "ledger": {"summary": status["summary"], "recent_closed": [{key: trade.get(key) for key in trade_fields} for trade in status["closed_trades"][:3]]},
            "recent_events": [{key: event.get(key) for key in ("created_at", "event_type", "message")} for event in status["events"][:3]],
        }

    def _create_ai_brief(self, instrument: str, analysis: dict[str, Any]) -> None:
        attempted_at = now_iso()
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            self._record_ai_failure(instrument, "DeepSeek API key is not configured", attempted_at)
            return
        else:
            prompt = f"用中文在120字内总结 {instrument}。必须引用多周期趋势、EMA20、CVD/OI和风险状态，解释规则为何给出{analysis['action']}，不要承诺收益。数据：{json.dumps(self._ai_context(instrument, analysis), ensure_ascii=False)}"
            request = Request("https://api.deepseek.com/chat/completions", data=json.dumps({"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 180}).encode(), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=25) as response:  # noqa: S310
                    content = json.loads(response.read().decode())["choices"][0]["message"]["content"].strip()
                source = "DeepSeek"
                self._record_ai_success(instrument, attempted_at); alerts=globals().get("ALERTS")
                if alerts:alerts.resolve(f"deepseek|{instrument}")
            except Exception as error:
                self._record_ai_failure(instrument, type(error).__name__, attempted_at)
                return
        with self._connect() as conn:
            conn.execute("INSERT INTO ai_briefs(created_at,instrument,content,source) VALUES(?,?,?,?)", (now_iso(), instrument, content, source))

    def _record_ai_success(self, instrument: str, attempted_at: str) -> None:
        with self._ai_lock:
            state = self.ai_state[instrument]
            state.update({"failure_count": 0, "last_error": None, "last_success_at": attempted_at, "next_retry_at": time.time() + AI_BRIEF_INTERVAL_SECONDS, "queued": False})
            self.last_ai_at[instrument] = time.time()
            self.last_ai_success = attempted_at
            self._save_ai_state(instrument, state, attempted_at)

    def _record_ai_failure(self, instrument: str, error_type: str, attempted_at: str) -> None:
        with self._ai_lock:
            state = self.ai_state[instrument]
            failures = state["failure_count"] + 1
            delay = min(AI_RETRY_MAX_SECONDS, AI_RETRY_BASE_SECONDS * 2 ** (failures - 1))
            state.update({"failure_count": failures, "last_error": error_type[:120], "next_retry_at": time.time() + delay, "queued": False})
            self._save_ai_state(instrument, state, attempted_at)
        alerts=globals().get("ALERTS")
        if alerts:alerts.raise_alert("DeepSeek Error","warning","ai",f"{instrument} AI brief request failed ({error_type}); retry in {delay}s",instrument,key=f"deepseek|{instrument}")

    def ai_health(self) -> dict[str, Any]:
        now = time.time()
        states: dict[str, dict[str, Any]] = {}
        with self._ai_lock:
            for instrument, state in self.ai_state.items():
                last_success = state["last_success_at"]
                age = None
                try:
                    age = now - datetime.fromisoformat(last_success).timestamp() if last_success else None
                except (TypeError, ValueError):
                    pass
                status = "disabled" if not os.getenv("DEEPSEEK_API_KEY") else "stale" if age is not None and age > AI_STALE_AFTER_SECONDS else "retrying" if state["failure_count"] else "starting" if not last_success else "healthy"
                states[instrument] = {"status": status, "last_success_at": last_success, "last_success_age_seconds": round(age, 1) if age is not None else None, "failure_count": state["failure_count"], "last_error": state["last_error"], "next_retry_at": datetime.fromtimestamp(state["next_retry_at"], timezone.utc).replace(microsecond=0).isoformat() if state["next_retry_at"] else None}
        return {"status": "disabled" if not os.getenv("DEEPSEEK_API_KEY") else "stale" if any(item["status"] == "stale" for item in states.values()) else "retrying" if any(item["status"] == "retrying" for item in states.values()) else "healthy" if all(item["status"] == "healthy" for item in states.values()) else "starting", "queue_depth": self._ai_queue.qsize(), "instruments": states}

    def _ai_health_monitor(self) -> None:
        while True:
            health = self.ai_health()
            alerts = globals().get("ALERTS")
            if alerts:
                for instrument, state in health["instruments"].items():
                    key = f"ai-stale|{instrument}"
                    if state["status"] == "stale":
                        alerts.raise_alert("AI Brief Stale", "warning", "ai", f"{instrument} AI brief has not succeeded for over 2 hours", instrument, key=key)
                    else:
                        alerts.resolve(key)
            time.sleep(60)

    def cycle_instrument(self, instrument: str) -> dict[str, Any]:
        try:
            price = self._price(instrument)
            self.monitor_positions(instrument, price)
            flow = self._flow_metrics(instrument)
            analysis = self.analyze(instrument, flow)
            self._open_trade(analysis)
            self.maybe_create_ai_brief(analysis)
            self.last_okx_success=now_iso(); self.last_okx_error=None
            alerts=globals().get("ALERTS")
            if alerts:alerts.resolve(f"collector-error|{instrument}")
            return analysis
        except Exception as error:
            self.last_okx_error=type(error).__name__
            state = {"instrument": instrument, "status": "Data unavailable", "action": "WAIT", "score": 0, "error": str(error), "updated_at": now_iso()}
            self.last_analysis[instrument] = state
            self._event(instrument, "COLLECTOR_ERROR", "Market collector cycle failed", {"error": str(error)})
            alerts=globals().get("ALERTS")
            if alerts:
                kind="OKX Rate Limited" if getattr(error,"code",None)==429 else "Collector Stale"
                alerts.raise_alert(kind,"warning","collector",f"{instrument} market collector failed",instrument,key=f"collector-error|{instrument}")
            return state

    def cycle(self) -> dict[str, Any]:
        with self._lock:
            start=time.monotonic(); self.last_cycle_started_at=now_iso()
            result={instrument: self.cycle_instrument(instrument) for instrument in INSTRUMENTS}
            self.last_cycle_completed_at=now_iso(); self.last_cycle_duration_ms=int((time.monotonic()-start)*1000); self.next_cycle_at=(datetime.now(timezone.utc)+timedelta(seconds=60)).replace(microsecond=0).isoformat()
            alerts=globals().get("ALERTS")
            if alerts:
                if self.last_cycle_duration_ms>45_000:alerts.raise_alert("Paper Cycle Slow","warning","paper_scheduler",f"Paper cycle took {self.last_cycle_duration_ms} ms",key="paper-cycle-slow")
                else:alerts.resolve("paper-cycle-slow")
            return result

    def status(self, instrument: str) -> dict[str, Any]:
        if instrument not in INSTRUMENTS: instrument = "ETH-USDT"
        with self._connect() as conn:
            open_trades = [dict(row) for row in conn.execute("SELECT * FROM paper_trades WHERE instrument=? AND status='OPEN' ORDER BY created_at DESC", (instrument,))]
            closed = [dict(row) for row in conn.execute("SELECT * FROM paper_trades WHERE instrument=? AND status!='OPEN' ORDER BY closed_at DESC LIMIT 20", (instrument,))]
            account = self._account_state(conn)
            account_open = int(conn.execute("SELECT COUNT(*) FROM paper_trades WHERE status='OPEN'").fetchone()[0])
            brief = conn.execute("SELECT created_at,content,source FROM ai_briefs WHERE instrument=? ORDER BY id DESC LIMIT 1", (instrument,)).fetchone()
            events = [dict(row) for row in conn.execute("SELECT id,created_at,event_type,message,payload FROM event_logs WHERE instrument=? ORDER BY id DESC LIMIT 30", (instrument,))]
            for event in events:
                event["message_code"] = f"event.{event['event_type'].lower()}"
                event["message_params"] = json.loads(event.get("payload") or "{}")
        # Legacy records predate the shared account and have no position size.
        # Retain their display-only R conversion, while new records use booked P&L.
        risk_amount = INITIAL_CAPITAL_USDT * RISK_PER_TRADE
        for trade in [*open_trades, *closed]:
            if trade.get("pnl_usdt") is None:
                trade["pnl_usdt"] = round(float(trade.get("pnl_r") or 0) * risk_amount, 2)
            trade["initial_capital_usdt"] = INITIAL_CAPITAL_USDT
        wins = sum(1 for row in closed if row["status"] == "WIN")
        total_r = round(sum(float(row["pnl_r"] or 0) for row in closed), 2)
        analysis = self.last_analysis[instrument]
        return {"instrument": instrument, "analysis": analysis, "flow": analysis.get("flow"), "risk": self.risk_state(instrument), "events": events, "open_trades": open_trades, "closed_trades": closed, "ai_brief": dict(brief) if brief else None, "summary": {"open": account_open, "closed": len(closed), "wins": wins, "win_rate": round(wins / len(closed) * 100, 1) if closed else 0, "total_r": total_r, "initial_capital_usdt": account["initial_capital"], "risk_per_trade": RISK_PER_TRADE, "total_pnl_usdt": round(account["realized_pnl"], 2), "equity_usdt": round(account["equity"], 2), "available_cash_usdt": round(account["cash"], 2), "open_position_value_usdt": round(account["open_value"], 2), "account_mode": "shared_portfolio"}}

    def replay(self, instrument: str, at: str | None = None) -> dict[str, Any]:
        if instrument not in INSTRUMENTS: instrument = "ETH-USDT"
        with self._connect() as conn:
            if not at:
                rows = conn.execute("SELECT id,created_at,payload FROM analysis_snapshots WHERE instrument=? ORDER BY id DESC LIMIT 96", (instrument,)).fetchall()
                return {"items": [{"id": row["id"], "created_at": row["created_at"], "analysis": json.loads(row["payload"])} for row in rows]}
            row = conn.execute("SELECT id,created_at,payload FROM analysis_snapshots WHERE instrument=? AND created_at<=? ORDER BY created_at DESC LIMIT 1", (instrument, at)).fetchone()
            if not row: return {"error": "No replay snapshot is available yet."}
            epoch = int(datetime.fromisoformat(row["created_at"]).timestamp())
            candles = [dict(item) for item in conn.execute("SELECT ts as time,open,high,low,close,volume FROM market_candles WHERE instrument=? AND bar='15m' AND ts<=? ORDER BY ts DESC LIMIT 120", (instrument, epoch))]
            event = conn.execute("SELECT event_type,message FROM event_logs WHERE instrument=? AND created_at>=? ORDER BY created_at LIMIT 1", (instrument, row["created_at"])).fetchone()
        return {"id": row["id"], "created_at": row["created_at"], "analysis": json.loads(row["payload"]), "candles": list(reversed(candles)), "outcome": dict(event) if event else None}

    def chat(self, question: str, instrument: str) -> dict[str, str]:
        question = question.strip()[:500]
        if not question: return {"error": "Please enter a question."}
        if instrument not in INSTRUMENTS: instrument = "ETH-USDT"
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key: return {"error": "DeepSeek is not configured on the server."}
        context = self._ai_context(instrument)
        system = "你是Crypto-Bot 市场助手。只基于给定摘要用中文直接、简洁回答；先给结论，再列最多3条事实和不确定性。不要展示推理过程，不承诺收益，不发送真实订单。"
        request = Request("https://api.deepseek.com/chat/completions", data=json.dumps({"model": "deepseek-chat", "temperature": 0.2, "max_tokens": 280, "messages": [{"role": "system", "content": system}, {"role": "user", "content": f"摘要：\n{json.dumps(context, ensure_ascii=False)}\n\n问题：{question}"}]}).encode(), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310
                return {"answer": json.loads(response.read().decode())["choices"][0]["message"]["content"].strip()}
        except Exception as error:
            return {"error": f"Copilot request failed: {error}"}


SERVICE = PaperService()
RESEARCH = ResearchService(DB_PATH)
ALERTS = AlertService(DB_PATH)
VALIDATION = ValidationService(RESEARCH)
SHADOW = ShadowService(VALIDATION.repository)
SHADOW.ensure_default_candidates()
LIFECYCLE = LifecycleService(VALIDATION.repository, ALERTS)
HEALTH = HealthService(DB_PATH,SERVICE,RESEARCH.jobs,ALERTS,ROOT)
LIMITER = RateLimiter()
LOGGER = configure_logging(ROOT)


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        for key, value in (("Content-Type", "application/json"), ("Cache-Control","no-store"), ("X-Content-Type-Options","nosniff"), ("Content-Length", str(len(body)))):
            self.send_header(key, value)
        self.end_headers(); self.wfile.write(body)

    def _client(self)->str:
        return self.headers.get("X-Forwarded-For",self.client_address[0]).split(",")[0].strip()[:64]

    def _limited(self,bucket:str,limit:int,window:int)->bool:
        allowed,retry=LIMITER.allow(bucket,self._client(),limit,window)
        if not allowed:
            self.send_response(HTTPStatus.TOO_MANY_REQUESTS); self.send_header("Content-Type","application/json"); self.send_header("Retry-After",str(retry)); self.end_headers(); self.wfile.write(json.dumps({"error":"Rate limit exceeded.","retry_after":retry}).encode()); return True
        return False

    def _admin(self)->bool:
        configured=os.getenv("ADMIN_TOKEN","")
        if not configured:return True
        supplied=self.headers.get("Authorization","").removeprefix("Bearer ")
        if hmac.compare_digest(configured,supplied):return True
        self._send({"error":"Admin authorization required."},HTTPStatus.UNAUTHORIZED); return False

    def _body(self)->dict[str,Any]|None:
        try:length=int(self.headers.get("Content-Length","0"))
        except ValueError:self._send({"error":"Invalid Content-Length."},HTTPStatus.BAD_REQUEST); return None
        if length>65536:self._send({"error":"Request body exceeds 64 KiB."},HTTPStatus.REQUEST_ENTITY_TOO_LARGE); return None
        try:return json.loads(self.rfile.read(length).decode() or "{}")
        except (UnicodeDecodeError,json.JSONDecodeError):self._send({"error":"Invalid JSON body"},HTTPStatus.BAD_REQUEST); return None

    def do_GET(self) -> None:  # noqa: N802
        parsed, query = urlparse(self.path), parse_qs(urlparse(self.path).query)
        instrument = query.get("instrument", ["ETH-USDT"])[0]
        if parsed.path == "/api/status": self._send(SERVICE.status(instrument))
        elif parsed.path == "/api/paper/flow/health": self._send(SERVICE.flow_health())
        elif parsed.path == "/api/vpvr":
            def query_float(name: str) -> float | None:
                try: return float(query[name][0]) if name in query else None
                except (TypeError, ValueError): return None
            bins = max(18, min(42, int(query_float("bins") or 32)))
            self._send(SERVICE.vpvr_profile(instrument, query.get("interval", ["15m"])[0], bins, query_float("price_low"), query_float("price_high")))
        elif parsed.path == "/api/health": self._send(HEALTH.payload(False))
        elif parsed.path == "/api/health/details":
            details=HEALTH.payload(True); shadows=SHADOW.list(); counts=VALIDATION.repository.table_counts(); details.update({"shadow_scheduler_status":"running","active_shadow_strategies":sum(x["status"]=="RUNNING" for x in shadows),"validation_job_types":["GATE_ANALYSIS","SENSITIVITY","BENCHMARK","ROBUSTNESS"],"phase4_database_rows":sum(counts.get(name,0) for name in counts if name.startswith(("gate_","near_","sensitivity_","benchmark_","robustness_","shadow_","strategy_lifecycle","promotion_","strategy_audit"))),"promotion_audit_alerts":sum(str(x.get("severity","")).lower()=="critical" and str(x.get("status","")).lower()=="open" for x in ALERTS.list())});self._send(details)
        elif parsed.path == "/api/jobs": self._send({"items":RESEARCH.jobs.list(int(query.get("limit",["100"])[0]))})
        elif parsed.path.startswith("/api/jobs/"):
            try:
                item=RESEARCH.jobs.get(int(parsed.path.rsplit("/",1)[1])); self._send(item or {"error":"Job not found"},HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
            except ValueError:self._send({"error":"Invalid job id"},HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/alerts": self._send({"items":ALERTS.list()})
        elif parsed.path == "/api/data-coverage": self._send({"items":RESEARCH.repository.data_coverage()})
        elif parsed.path == "/api/discovery/datasets": self._send({"items":RESEARCH.repository.discovery_datasets()})
        elif parsed.path.startswith("/api/discovery/datasets/"):
            try:
                dataset=RESEARCH.repository.discovery_dataset(int(parsed.path.rsplit('/',1)[1])); self._send(dataset or {'error':'Dataset not found'}, HTTPStatus.OK if dataset else HTTPStatus.NOT_FOUND)
            except ValueError:self._send({'error':'Invalid dataset id'},HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/discovery/runs":
            with RESEARCH.repository.connect() as c:self._send({'items':[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_runs ORDER BY id DESC')]})
        elif parsed.path == "/api/discovery/robustness/runs":
            self._send({'items':RESEARCH.discovery_robustness.list_runs()})
        elif parsed.path.startswith('/api/discovery/robustness/runs/'):
            try:
                rid=int(parsed.path.rsplit('/',1)[1])
                row=RESEARCH.discovery_robustness.run_detail(rid)
                self._send(row or {'error':'Robustness run not found'},HTTPStatus.OK if row else HTTPStatus.NOT_FOUND)
            except ValueError:self._send({'error':'Invalid robustness run id'},HTTPStatus.BAD_REQUEST)
        elif parsed.path == '/api/discovery/ablation/runs':
            try:self._send({'items':RESEARCH.discovery_ablation.list_runs()})
            except Exception:self._send({'error':'Internal server error'},HTTPStatus.INTERNAL_SERVER_ERROR)
        elif parsed.path.startswith('/api/discovery/ablation/runs/'):
            value=parsed.path.removeprefix('/api/discovery/ablation/runs/')
            if not value.isascii() or not value.isdecimal() or int(value)<1:
                self._send({'error':'Invalid ablation run id'},HTTPStatus.BAD_REQUEST)
            else:
                try:
                    row=RESEARCH.discovery_ablation.run_detail(int(value))
                    self._send(row or {'error':'Ablation run not found'},HTTPStatus.OK if row else HTTPStatus.NOT_FOUND)
                except Exception:self._send({'error':'Internal server error'},HTTPStatus.INTERNAL_SERVER_ERROR)
        elif parsed.path.startswith("/api/discovery/runs/") and parsed.path.endswith('/candidates'):
            try:
                rid=int(parsed.path.split('/')[4]);
                with RESEARCH.repository.connect() as c:self._send({'items':[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_candidates WHERE discovery_run_id=? ORDER BY candidate_number',(rid,))]})
            except ValueError:self._send({'error':'Invalid discovery run id'},HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/discovery/candidates/"):
            try:
                cid=int(parsed.path.split('/')[4]);table='strategy_discovery_candidates';
                with RESEARCH.repository.connect() as c:
                    row=c.execute('SELECT * FROM strategy_discovery_candidates WHERE id=?',(cid,)).fetchone(); self._send(dict(row) if row else {'error':'Candidate not found'},HTTPStatus.OK if row else HTTPStatus.NOT_FOUND)
            except ValueError:self._send({'error':'Invalid candidate id'},HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/validation/gates":
            try:self._send(VALIDATION.gates(int(query["run_id"][0]) if query.get("run_id") else None,{key:value[0] for key,value in query.items() if key!="run_id"}))
            except ValueError as error:self._send({"error":str(error)},HTTPStatus.NOT_FOUND)
        elif parsed.path in {"/api/validation/gates/timeline","/api/validation/score-distribution"}:
            result=VALIDATION.gates(int(query["run_id"][0]) if query.get("run_id") else None,{key:value[0] for key,value in query.items() if key!="run_id"}); key="daily_rejection_timeline" if parsed.path.endswith("timeline") else "score_distribution"; source=result.get("summary",result);self._send({"items":source.get(key,[])})
        elif parsed.path == "/api/near-misses": self._send(VALIDATION.repository.near_misses({key:value[0] for key,value in query.items()},int(query.get("page",["1"])[0]),int(query.get("page_size",["50"])[0])))
        elif parsed.path.startswith("/api/near-misses/"):
            try:
                item=VALIDATION.repository.near_miss(int(parsed.path.split("/")[3])); self._send((item.get("outcome") if parsed.path.endswith("/outcome") and item else item) or {"error":"Near miss not found"},HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
            except (ValueError,IndexError):self._send({"error":"Invalid near miss id"},HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/sensitivity/"):
            try:self._send(VALIDATION.sensitivity(int(parsed.path.split("/")[3]),int(query.get("page",["1"])[0]),int(query.get("page_size",["100"])[0])))
            except ValueError as error:self._send({"error":str(error)},HTTPStatus.NOT_FOUND)
        elif parsed.path.startswith("/api/benchmarks/"):
            try:self._send(VALIDATION.benchmark(int(parsed.path.split("/")[3])))
            except ValueError as error:self._send({"error":str(error)},HTTPStatus.NOT_FOUND)
        elif parsed.path.startswith("/api/robustness/"):
            try:self._send(VALIDATION.robustness(int(parsed.path.split("/")[3])))
            except ValueError as error:self._send({"error":str(error)},HTTPStatus.NOT_FOUND)
        elif parsed.path == "/api/shadow-strategies": self._send({"items":SHADOW.list()})
        elif parsed.path.startswith("/api/shadow-strategies/") and parsed.path.endswith("/trades"): self._send({"items":SHADOW.trades(parsed.path.split("/")[3],int(query.get("limit",["100"])[0]))})
        elif parsed.path.startswith("/api/shadow-strategies/") and parsed.path.endswith("/equity"): self._send({"items":SHADOW.equity(parsed.path.split("/")[3],int(query.get("limit",["1000"])[0]))})
        elif parsed.path == "/api/strategy-lifecycle": self._send({"items":LIFECYCLE.list(),"policy_version":"promotion-policy-v1"})
        elif parsed.path.startswith("/api/strategy-lifecycle/") and parsed.path.endswith("/audit"):
            try:self._send({"items":LIFECYCLE.audit(int(parsed.path.split("/")[3]))})
            except ValueError:self._send({"error":"Invalid lifecycle id"},HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/portfolio/"):
            try:
                item=RESEARCH.repository.portfolio_run(int(parsed.path.rsplit("/",1)[1])); self._send(item if item else {"error":"Portfolio run not found"},HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
            except ValueError:self._send({"error":"Invalid portfolio run id"},HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/replay": self._send(SERVICE.replay(instrument, query.get("at", [None])[0]))
        elif parsed.path == "/api/strategies": self._send({"items": RESEARCH.strategies()})
        elif parsed.path == "/api/backtest/history": self._send({"items": RESEARCH.repository.run_history()})
        elif parsed.path == "/api/optimization/history": self._send({"items": RESEARCH.repository.optimization_history()})
        elif parsed.path == "/api/optimization/families": self._send({"items": RESEARCH.repository.optimization_families()})
        elif parsed.path.startswith("/api/optimization/families/"):
            try:
                item = RESEARCH.repository.optimization_family(int(parsed.path.rsplit("/", 1)[1])); self._send(item or {"error": "Experiment family not found"}, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
            except ValueError: self._send({"error": "Invalid experiment family id"}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/validation-suites": self._send({"items": RESEARCH.repository.validation_suites()})
        elif parsed.path.startswith("/api/validation-suites/"):
            try:
                item = RESEARCH.repository.validation_suite(int(parsed.path.rsplit("/", 1)[1])); self._send(item or {"error": "Validation suite not found"}, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
            except ValueError: self._send({"error": "Invalid validation suite id"}, HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/optimization/"):
            try:
                item = RESEARCH.repository.optimization_run(int(parsed.path.rsplit("/", 1)[1]), include_holdout=False)
                self._send(item or {"error": "Optimization run not found"}, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
            except ValueError: self._send({"error": "Invalid optimization run id"}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/reconciliation":
            try: self._send(RESEARCH.reconciliation(int(query.get("run_id", ["0"])[0])))
            except (ValueError, TypeError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/backtest/"):
            parts = parsed.path.strip("/").split("/")
            try:
                run_id = int(parts[2])
                if len(parts) == 4 and parts[3] == "trades": payload = {"items": RESEARCH.repository.trades(run_id)}
                elif len(parts) == 4 and parts[3] == "equity": payload = {"items": RESEARCH.repository.equity(run_id)}
                else: payload = RESEARCH.run_detail(run_id, include_series=False)
                self._send(payload if payload is not None else {"error": "Backtest not found"}, HTTPStatus.OK if payload is not None else HTTPStatus.NOT_FOUND)
            except (ValueError, IndexError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        else: self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        payload=self._body()
        if payload is None:return
        if parsed.path == "/api/cycle": self._send(SERVICE.cycle())
        elif parsed.path == "/api/discovery/datasets/prepare":
            if not self._admin(): return
            try:self._send(RESEARCH.discovery.prepare_dataset(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({'error':str(error)},HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/discovery/runs":
            if not self._admin(): return
            try:self._send(RESEARCH.discovery.start(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({'error':str(error)},HTTPStatus.BAD_REQUEST if isinstance(error,ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == '/api/discovery/robustness/runs':
            if not self._admin(): return
            try:self._send(RESEARCH.discovery_robustness.start(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({'error':str(error)},HTTPStatus.BAD_REQUEST if isinstance(error,ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == '/api/discovery/ablation/runs':
            if not self._admin(): return
            try:self._send(RESEARCH.discovery_ablation.start(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({'error':str(error)},HTTPStatus.BAD_REQUEST if isinstance(error,ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
            except Exception:self._send({'error':'Internal server error'},HTTPStatus.INTERNAL_SERVER_ERROR)
        elif parsed.path.startswith('/api/discovery/robustness/runs/') and parsed.path.endswith('/cancel'):
            if not self._admin(): return
            try:
                rid=int(parsed.path.split('/')[5])
            except (ValueError,IndexError):self._send({'error':'Invalid robustness run id'},HTTPStatus.BAD_REQUEST)
            else:
                try:self._send(RESEARCH.discovery_robustness.cancel(rid))
                except ValueError as error:self._send({'error':str(error)},HTTPStatus.NOT_FOUND if str(error)=='Robustness run not found.' else HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith('/api/discovery/ablation/runs/') and parsed.path.endswith('/cancel'):
            if not self._admin(): return
            value=parsed.path.removeprefix('/api/discovery/ablation/runs/').removesuffix('/cancel')
            if not value.isascii() or not value.isdecimal() or int(value)<1:
                self._send({'error':'Invalid ablation run id'},HTTPStatus.BAD_REQUEST)
            else:
                try:self._send(RESEARCH.discovery_ablation.cancel(int(value)))
                except ValueError as error:self._send({'error':str(error)},HTTPStatus.NOT_FOUND if str(error)=='Ablation run not found.' else HTTPStatus.BAD_REQUEST)
                except Exception:self._send({'error':'Internal server error'},HTTPStatus.INTERNAL_SERVER_ERROR)
        elif parsed.path.startswith('/api/discovery/runs/') and parsed.path.endswith('/cancel'):
            if not self._admin(): return
            try:
                rid=int(parsed.path.split('/')[4]);
                with RESEARCH.repository.connect() as c: row=c.execute("SELECT id FROM research_jobs WHERE job_type='STRATEGY_DISCOVERY' AND request_payload LIKE ? ORDER BY id DESC LIMIT 1",(f'%"discovery_run_id": {rid}%',)).fetchone()
                self._send(RESEARCH.jobs.cancel(int(row['id'])) if row else {'error':'Active job not found'})
            except ValueError:self._send({'error':'Invalid discovery run id'},HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/validation/gates/run":
            if not self._admin():return
            try:self._send(VALIDATION.start_gates(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST if isinstance(error,ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == "/api/sensitivity/run":
            if not self._admin():return
            try:self._send(VALIDATION.start_sensitivity(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST if isinstance(error,ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == "/api/benchmarks/run":
            if not self._admin():return
            try:self._send(VALIDATION.start_benchmark(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST if isinstance(error,ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == "/api/robustness/run":
            if not self._admin():return
            try:self._send(VALIDATION.start_robustness(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST if isinstance(error,ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == "/api/shadow-strategies":
            if not self._admin():return
            try:self._send(SHADOW.create(payload),HTTPStatus.CREATED)
            except (ValueError,sqlite3.IntegrityError) as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/shadow-strategies/"):
            if not self._admin():return
            parts=parsed.path.strip("/").split("/")
            try:self._send(SHADOW.duplicate(parts[2]) if parts[3]=="duplicate" else SHADOW.action(parts[2],parts[3]),HTTPStatus.CREATED if parts[3]=="duplicate" else HTTPStatus.OK)
            except (ValueError,IndexError,sqlite3.IntegrityError) as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/strategy-lifecycle/"):
            if not self._admin():return
            parts=parsed.path.strip("/").split("/")
            try:self._send(LIFECYCLE.evaluate(int(parts[2]),payload.get("policy")) if parts[3]=="evaluate" else LIFECYCLE.transition(int(parts[2]),parts[3],"admin",payload.get("evidence")))
            except (ValueError,IndexError) as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/chat":
            if len(str(payload.get("question","")))>1200:self._send({"error":"Question exceeds 1200 characters."},HTTPStatus.BAD_REQUEST); return
            if self._limited("chat-minute",3,60) or self._limited("chat-hour",20,3600):return
            result = SERVICE.chat(str(payload.get("question", "")), str(payload.get("instrument", "ETH-USDT")))
            self._send(result, HTTPStatus.BAD_REQUEST if "error" in result else HTTPStatus.OK)
        elif parsed.path == "/api/backtest/run":
            if self._limited("backtest-minute",2,60) or self._limited("backtest-day",20,86400):return
            try: self._send(RESEARCH.start_backtest(payload,self._client()), HTTPStatus.ACCEPTED)
            except OverflowError as error: ALERTS.raise_alert("Queue Full","warning","job_queue",str(error)); self._send({"error":str(error)},HTTPStatus.TOO_MANY_REQUESTS)
            except ValueError as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/optimization/run":
            if self._limited("optimization-day", 1, 86400): return
            try: self._send(RESEARCH.start_optimization(payload, self._client()), HTTPStatus.ACCEPTED)
            except (ValueError, OverflowError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST if isinstance(error, ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == "/api/optimization/compare":
            try: self._send(RESEARCH.optimization_comparison([int(value) for value in payload.get("run_ids", [])]))
            except (ValueError, TypeError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/optimization/families":
            try: self._send(RESEARCH.create_optimization_family(payload), HTTPStatus.CREATED)
            except ValueError as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/optimization/") and parsed.path.endswith("/reveal-holdout"):
            try:
                item = RESEARCH.repository.reveal_optimization_holdout(int(parsed.path.split("/")[3])); self._send(item or {"error": "Optimization run not found"}, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
            except ValueError: self._send({"error": "Invalid optimization run id"}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/validation-suites/run":
            try: self._send(RESEARCH.start_validation_suite(payload, self._client()), HTTPStatus.ACCEPTED)
            except (ValueError, OverflowError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST if isinstance(error, ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == "/api/portfolio/run":
            if self._limited("backtest-minute",2,60) or self._limited("backtest-day",20,86400):return
            try:self._send(RESEARCH.start_portfolio(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST if isinstance(error,ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == "/api/strategies":
            if not self._admin():return
            try: self._send(RESEARCH.save_strategy(payload), HTTPStatus.CREATED)
            except (ValueError, sqlite3.IntegrityError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/compare":
            try:
                items = RESEARCH.compare_strategies([int(value) for value in payload.get("strategy_ids", [])]) if "strategy_ids" in payload else RESEARCH.compare([int(value) for value in payload.get("run_ids", [])])
                self._send({"items": items})
            except (ValueError, TypeError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/walk-forward":
            if self._limited("walk-minute",1,60):return
            try: self._send(RESEARCH.start_walk_forward(payload,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST if isinstance(error,ValueError) else HTTPStatus.TOO_MANY_REQUESTS)
        elif parsed.path == "/api/jobs/cleanup":
            if not self._admin():return
            self._send({"deleted_jobs":RESEARCH.jobs.cleanup(int(payload.get("older_than_days",30))),"backtest_results_deleted":0})
        elif parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
            if not self._admin():return
            try:self._send(RESEARCH.jobs.cancel(int(parsed.path.split("/")[3])))
            except ValueError as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/retry"):
            if not self._admin():return
            try:
                job_id=int(parsed.path.split("/")[3]); job=RESEARCH.jobs.get(job_id)
                self._send(RESEARCH.retry_optimization_job(job_id,self._client()) if job and job["job_type"]=="OPTIMIZATION" else RESEARCH.retry_validation_suite_job(job_id,self._client()) if job and job["job_type"]=="VALIDATION_SUITE" else RESEARCH.jobs.retry(job_id,self._client()),HTTPStatus.ACCEPTED)
            except (ValueError,OverflowError) as error:self._send({"error":str(error)},HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/alerts/") and parsed.path.endswith("/acknowledge"):
            if not self._admin():return
            try:self._send({"acknowledged":ALERTS.acknowledge(int(parsed.path.split("/")[3]))})
            except ValueError:self._send({"error":"Invalid alert id"},HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/strategies/") and parsed.path.endswith("/duplicate"):
            try: self._send(RESEARCH.duplicate_strategy(int(parsed.path.strip("/").split("/")[2])), HTTPStatus.CREATED)
            except (ValueError, sqlite3.IntegrityError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        else: self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:  # noqa: N802
        if not self._admin():return
        try:
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length).decode() or "{}")
            strategy_id = int(urlparse(self.path).path.strip("/").split("/")[2])
            self._send(RESEARCH.save_strategy(payload, strategy_id))
        except (ValueError, IndexError, json.JSONDecodeError, sqlite3.IntegrityError) as error:
            self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._admin():return
        try:
            strategy_id = int(urlparse(self.path).path.strip("/").split("/")[2])
            deleted = RESEARCH.repository.delete_strategy(strategy_id)
            self._send({"deleted": deleted}, HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND)
        except (ValueError, IndexError) as error:
            self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send({})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def run() -> None:
    def scheduler() -> None:
        SERVICE.scheduler_running=True
        while True:
            SERVICE.cycle(); log_event(LOGGER,"INFO","paper_scheduler","cycle_completed",duration_ms=SERVICE.last_cycle_duration_ms); time.sleep(60)
    SERVICE.start_ai_workers()
    SERVICE.start_flow_collector()
    threading.Thread(target=scheduler, daemon=True).start()
    host = os.getenv("PAPER_API_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, int(os.getenv("PAPER_API_PORT", "8765"))), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        SERVICE.stop_flow_collector()


if __name__ == "__main__":
    run()
