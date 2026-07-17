"""Always-on OKX multi-asset paper engine and research API."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from research_service import ResearchService
from strategy_rules import score_rule_components

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data_cache" / "paper_trades.db"
INSTRUMENTS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
MAX_OPEN_POSITIONS = 3
MAX_DAILY_LOSS_R = -2.0
MAX_CONSECUTIVE_LOSSES = 3
COOLDOWN_HOURS = 4

load_dotenv(ROOT / ".env")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ema(values: list[float], period: int) -> float:
    multiplier, result = 2 / (period + 1), values[0]
    for value in values[1:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


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

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
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
            conn.execute("""CREATE TABLE IF NOT EXISTS analysis_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, payload TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS ai_briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS flow_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, oi REAL, cvd REAL)""")
            self._ensure_column(conn, "analysis_snapshots", "instrument", "TEXT")
            self._ensure_column(conn, "ai_briefs", "instrument", "TEXT")
            self._ensure_column(conn, "flow_snapshots", "instrument", "TEXT")
            conn.execute("""CREATE TABLE IF NOT EXISTS event_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, instrument TEXT NOT NULL,
                event_type TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')""")
            conn.execute("""CREATE TABLE IF NOT EXISTS market_candles (
                instrument TEXT NOT NULL, bar TEXT NOT NULL, ts INTEGER NOT NULL, open REAL NOT NULL,
                high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
                PRIMARY KEY(instrument, bar, ts))""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analysis_instrument ON analysis_snapshots(instrument, id DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_instrument ON event_logs(instrument, id DESC)")

    @staticmethod
    def _json(url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "crypto-bot-paper-research/2.0"})
        with urlopen(request, timeout=12) as response:  # noqa: S310 - fixed OKX/DeepSeek URLs
            return json.loads(response.read().decode("utf-8"))

    def _candles(self, instrument: str, bar: str, limit: int = 300) -> list[dict[str, float]]:
        payload = self._json(f"https://www.okx.com/api/v5/market/candles?instId={instrument}&bar={bar}&limit={limit}")
        candles = [{"ts": int(row[0]) // 1000, "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])} for row in payload.get("data", [])]
        return list(reversed(candles))

    def _price(self, instrument: str) -> float:
        payload = self._json(f"https://www.okx.com/api/v5/market/ticker?instId={instrument}")
        return float(payload["data"][0]["last"])

    def _event(self, instrument: str, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO event_logs(created_at,instrument,event_type,message,payload) VALUES(?,?,?,?,?)", (now_iso(), instrument, event_type, message, json.dumps(payload or {}, ensure_ascii=False)))

    def _flow_metrics(self, instrument: str) -> dict[str, Any]:
        trades = self._json(f"https://www.okx.com/api/v5/market/trades?instId={instrument}&limit=100").get("data", [])
        cumulative, series = 0.0, []
        for row in reversed(trades):
            cumulative += float(row["sz"]) * float(row["px"]) * (1 if row.get("side") == "buy" else -1)
            series.append({"time": int(row["ts"]) // 1000, "value": round(cumulative, 2)})
        swap = instrument.replace("-USDT", "-USDT-SWAP")
        oi_row = self._json(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={swap}").get("data", [{}])[0]
        oi = float(oi_row.get("oiUsd") or oi_row.get("oi") or 0)
        with self._connect() as conn:
            previous = conn.execute("SELECT oi FROM flow_snapshots WHERE instrument=? ORDER BY id DESC LIMIT 1", (instrument,)).fetchone()
            conn.execute("INSERT INTO flow_snapshots(created_at,instrument,oi,cvd) VALUES(?,?,?,?)", (now_iso(), instrument, oi, cumulative))
            history = [dict(row) for row in conn.execute("SELECT created_at,oi,cvd FROM flow_snapshots WHERE instrument=? ORDER BY id DESC LIMIT 60", (instrument,))]
        previous_oi = float(previous["oi"]) if previous and previous["oi"] else oi
        return {
            "cvd": round(cumulative, 2), "cvd_delta": round(cumulative, 2), "cvd_series": series,
            "oi": oi, "oi_change_pct": round((oi - previous_oi) / previous_oi * 100, 4) if previous_oi else 0,
            "oi_history": list(reversed(history)), "source": "OKX public trades + SWAP OI",
        }

    @staticmethod
    def _ema_slope(closes: list[float]) -> float:
        current, previous = ema(closes[-80:], 20), ema(closes[-90:-10], 20)
        return (current - previous) / previous * 100 if previous else 0

    @staticmethod
    def _rsi(closes: list[float], period: int = 14) -> float:
        changes = [b - a for a, b in zip(closes[-period - 1:-1], closes[-period:])]
        gains, losses = sum(max(v, 0) for v in changes) / period, sum(max(-v, 0) for v in changes) / period
        return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)

    @staticmethod
    def _atr(candles: list[dict[str, float]], period: int = 14) -> float:
        sample = candles[-period - 1:]
        ranges = [max(row["high"] - row["low"], abs(row["high"] - previous["close"]), abs(row["low"] - previous["close"])) for previous, row in zip(sample, sample[1:])]
        return sum(ranges) / len(ranges)

    def _store_candles(self, instrument: str, candles: list[dict[str, float]]) -> None:
        with self._connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO market_candles(instrument,bar,ts,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)", [(instrument, "15m", row["ts"], row["open"], row["high"], row["low"], row["close"], row["volume"]) for row in candles])

    def analyze(self, instrument: str, flow: dict[str, Any]) -> dict[str, Any]:
        c15, c1h, c4h, c1d = (self._candles(instrument, bar) for bar in ("15m", "1H", "4H", "1D"))
        self._store_candles(instrument, c15)
        closes15, closes1h, closes4h, closes1d = ([row["close"] for row in candles] for candles in (c15, c1h, c4h, c1d))
        price = closes15[-1]
        ema15, ema1h, ema4h = ema(closes15[-80:], 20), ema(closes1h[-80:], 20), ema(closes4h[-80:], 20)
        ma60_1h, ma200_1h = sum(closes1h[-60:]) / 60, sum(closes1h[-200:]) / 200
        ma60_4h, ma200_4h = sum(closes4h[-60:]) / 60, sum(closes4h[-200:]) / 200
        trend_long = closes4h[-1] > ma60_4h > ma200_4h and closes1h[-1] > ma60_1h > ma200_1h
        trend_short = closes4h[-1] < ma60_4h < ma200_4h and closes1h[-1] < ma60_1h < ma200_1h
        side = "LONG" if trend_long else "SHORT" if trend_short else "WAIT"
        distance, rsi, atr = (price - ema15) / ema15 * 100, self._rsi(closes15), self._atr(c15)
        volume_ratio = c15[-1]["volume"] / (sum(row["volume"] for row in c15[-21:-1]) / 20 or 1)
        pullback = abs(distance) <= 0.45
        flow_aligned = (side == "LONG" and flow["cvd_delta"] > 0) or (side == "SHORT" and flow["cvd_delta"] < 0)
        contributions = [
            {"key": "trend", "label": "1H + 4H trend", "points": 30 if side != "WAIT" else 8, "max": 30, "status": "pass" if side != "WAIT" else "fail", "detail": "MA60/MA200 aligned" if side != "WAIT" else "Moving averages are mixed"},
            {"key": "structure", "label": "MA60 / MA200 structure", "points": 20 if side != "WAIT" else 6, "max": 20, "status": "pass" if side != "WAIT" else "watch", "detail": f"4H {ma60_4h:.2f} / {ma200_4h:.2f}"},
            {"key": "pullback", "label": "EMA20 pullback", "points": 20 if pullback else 5, "max": 20, "status": "pass" if pullback else "fail", "detail": f"{distance:+.2f}% from EMA20"},
            {"key": "momentum", "label": "Volume + RSI", "points": 15 if volume_ratio >= 1 and 35 <= rsi <= 68 else 6, "max": 15, "status": "pass" if volume_ratio >= 1 and 35 <= rsi <= 68 else "watch", "detail": f"{volume_ratio:.2f}x volume · RSI {rsi:.1f}"},
            {"key": "flow", "label": "CVD + OI flow", "points": 15 if flow_aligned else 5, "max": 15, "status": "pass" if flow_aligned else "watch", "detail": f"CVD {flow['cvd_delta']:+.0f} · OI {flow['oi_change_pct']:+.3f}%"},
        ]
        shared_components = score_rule_components(side != "WAIT", pullback, volume_ratio >= 1 and 35 <= rsi <= 68, True, flow_aligned)
        for contribution, shared in zip(contributions, shared_components):
            contribution["points"] = shared["points"]
        score = sum(item["points"] for item in contributions)
        action = side if side != "WAIT" and pullback and flow_aligned and score >= 75 else "WAIT"

        def timeframe(closes: list[float]) -> dict[str, Any]:
            ma60, ma200 = sum(closes[-60:]) / 60, sum(closes[-200:]) / 200
            trend = "Bullish" if closes[-1] > ma60 > ma200 else "Bearish" if closes[-1] < ma60 < ma200 else "Mixed"
            return {"trend": trend, "ema20_slope_pct": round(self._ema_slope(closes), 3), "ma60": round(ma60, 4), "ma200": round(ma200, 4)}

        analysis = {
            "instrument": instrument, "price": round(price, 4), "action": action, "bias": side, "score": score,
            "ema20": round(ema15, 4), "rsi14": round(rsi, 2), "atr14": round(atr, 4),
            "volume_ratio": round(volume_ratio, 2), "distance_ema20_pct": round(distance, 3),
            "timeframes": {"15m": timeframe(closes15), "1H": timeframe(closes1h), "4H": timeframe(closes4h), "1D": timeframe(closes1d)},
            "contributions": contributions, "flow": flow,
            "conditions": [{"label": item["label"], "value": item["detail"], "pass": item["status"] == "pass"} for item in contributions],
            "updated_at": now_iso(),
        }
        with self._connect() as conn:
            conn.execute("INSERT INTO analysis_snapshots(created_at,instrument,payload) VALUES(?,?,?)", (analysis["updated_at"], instrument, json.dumps(analysis)))
        self.last_analysis[instrument] = analysis
        return analysis

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
        return {"allowed": not blockers, "blockers": blockers, "open_positions": open_total, "max_open_positions": MAX_OPEN_POSITIONS, "daily_pnl_r": round(today_r, 2), "daily_loss_limit_r": MAX_DAILY_LOSS_R, "consecutive_losses": consecutive, "max_consecutive_losses": MAX_CONSECUTIVE_LOSSES, "cooldown_until": cooldown_until.isoformat() if cooldown_until else None}

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
            entry, atr, side = analysis["price"], analysis["atr14"], analysis["action"]
            stop, target = (entry - atr, entry + 2 * atr) if side == "LONG" else (entry + atr, entry - 2 * atr)
            cursor = conn.execute("INSERT INTO paper_trades(instrument,side,entry,stop_loss,take_profit,created_at) VALUES(?,?,?,?,?,?)", (instrument, side, entry, stop, target, now_iso()))
            trade_id = cursor.lastrowid
        self._event(instrument, "TRADE_OPENED", f"Paper {side} opened", {"trade_id": trade_id, "entry": entry, "stop": stop, "target": target, "score": analysis["score"]})

    def monitor_positions(self, instrument: str, price: float) -> None:
        closed_events: list[dict[str, Any]] = []
        with self._connect() as conn:
            positions = conn.execute("SELECT * FROM paper_trades WHERE instrument=? AND status='OPEN'", (instrument,)).fetchall()
            for row in positions:
                hit_stop = price <= row["stop_loss"] if row["side"] == "LONG" else price >= row["stop_loss"]
                hit_target = price >= row["take_profit"] if row["side"] == "LONG" else price <= row["take_profit"]
                if not (hit_stop or hit_target): continue
                exit_price, reason = (row["take_profit"], "TAKE_PROFIT") if hit_target else (row["stop_loss"], "STOP_LOSS")
                risk = abs(row["entry"] - row["stop_loss"]) or 1
                pnl_r = (exit_price - row["entry"]) / risk if row["side"] == "LONG" else (row["entry"] - exit_price) / risk
                status = "WIN" if pnl_r > 0 else "LOSS"
                conn.execute("UPDATE paper_trades SET status=?,exit_price=?,pnl_r=?,reason=?,closed_at=? WHERE id=?", (status, exit_price, pnl_r, reason, now_iso(), row["id"]))
                closed_events.append({"trade_id": row["id"], "pnl_r": pnl_r, "exit": exit_price, "reason": reason})
        for event in closed_events:
            self._event(instrument, "TRADE_CLOSED", f"Paper trade closed by {event['reason']}", event)

    def maybe_create_ai_brief(self, analysis: dict[str, Any]) -> None:
        instrument = analysis["instrument"]
        if time.time() - self.last_ai_at[instrument] < 3600: return
        self.last_ai_at[instrument] = time.time()
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            content, source = "AI brief disabled: set DEEPSEEK_API_KEY on the server.", "disabled"
        else:
            prompt = f"用中文在120字内总结 {instrument}。必须引用多周期MA60/MA200、EMA20斜率、CVD/OI和风险状态，解释规则为何给出{analysis['action']}，不要承诺收益。数据：{json.dumps(analysis, ensure_ascii=False)}"
            request = Request("https://api.deepseek.com/chat/completions", data=json.dumps({"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 300}).encode(), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=25) as response:  # noqa: S310
                    content = json.loads(response.read().decode())["choices"][0]["message"]["content"].strip()
                source = "DeepSeek"
            except Exception as error:
                content, source = f"AI brief unavailable: {error}", "error"
        with self._connect() as conn:
            conn.execute("INSERT INTO ai_briefs(created_at,instrument,content,source) VALUES(?,?,?,?)", (now_iso(), instrument, content, source))

    def cycle_instrument(self, instrument: str) -> dict[str, Any]:
        try:
            price = self._price(instrument)
            self.monitor_positions(instrument, price)
            flow = self._flow_metrics(instrument)
            analysis = self.analyze(instrument, flow)
            self._open_trade(analysis)
            self.maybe_create_ai_brief(analysis)
            return analysis
        except Exception as error:
            state = {"instrument": instrument, "status": "Data unavailable", "action": "WAIT", "score": 0, "error": str(error), "updated_at": now_iso()}
            self.last_analysis[instrument] = state
            self._event(instrument, "COLLECTOR_ERROR", "Market collector cycle failed", {"error": str(error)})
            return state

    def cycle(self) -> dict[str, Any]:
        with self._lock:
            return {instrument: self.cycle_instrument(instrument) for instrument in INSTRUMENTS}

    def status(self, instrument: str) -> dict[str, Any]:
        if instrument not in INSTRUMENTS: instrument = "ETH-USDT"
        with self._connect() as conn:
            open_trades = [dict(row) for row in conn.execute("SELECT * FROM paper_trades WHERE instrument=? AND status='OPEN' ORDER BY created_at DESC", (instrument,))]
            closed = [dict(row) for row in conn.execute("SELECT * FROM paper_trades WHERE instrument=? AND status!='OPEN' ORDER BY closed_at DESC LIMIT 20", (instrument,))]
            brief = conn.execute("SELECT created_at,content,source FROM ai_briefs WHERE instrument=? ORDER BY id DESC LIMIT 1", (instrument,)).fetchone()
            events = [dict(row) for row in conn.execute("SELECT id,created_at,event_type,message,payload FROM event_logs WHERE instrument=? ORDER BY id DESC LIMIT 30", (instrument,))]
        wins = sum(1 for row in closed if row["status"] == "WIN")
        analysis = self.last_analysis[instrument]
        return {"instrument": instrument, "analysis": analysis, "flow": analysis.get("flow"), "risk": self.risk_state(instrument), "events": events, "open_trades": open_trades, "closed_trades": closed, "ai_brief": dict(brief) if brief else None, "summary": {"open": len(open_trades), "closed": len(closed), "wins": wins, "win_rate": round(wins / len(closed) * 100, 1) if closed else 0, "total_r": round(sum(row["pnl_r"] or 0 for row in closed), 2)}}

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
        question = question.strip()[:1200]
        if not question: return {"error": "Please enter a question."}
        if instrument not in INSTRUMENTS: instrument = "ETH-USDT"
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key: return {"error": "DeepSeek is not configured on the server."}
        context = self.status(instrument)
        context["events"], context["closed_trades"] = context["events"][:8], context["closed_trades"][:5]
        system = "你是Crypto-Bot Market Copilot。只使用所给OKX多周期、MA60/MA200、EMA20斜率、CVD/OI、风险与模拟交易数据回答中文。先给结论，再列事实和不确定性；不得声称确定获利或发出真实订单。"
        request = Request("https://api.deepseek.com/chat/completions", data=json.dumps({"model": "deepseek-chat", "temperature": 0.2, "max_tokens": 650, "messages": [{"role": "system", "content": system}, {"role": "user", "content": f"Context:\n{json.dumps(context, ensure_ascii=False)}\n\nQuestion: {question}"}]}).encode(), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310
                return {"answer": json.loads(response.read().decode())["choices"][0]["message"]["content"].strip()}
        except Exception as error:
            return {"error": f"Copilot request failed: {error}"}


SERVICE = PaperService()
RESEARCH = ResearchService(DB_PATH)


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        for key, value in (("Content-Type", "application/json"), ("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"), ("Access-Control-Allow-Headers", "Content-Type"), ("Content-Length", str(len(body)))):
            self.send_header(key, value)
        self.end_headers(); self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed, query = urlparse(self.path), parse_qs(urlparse(self.path).query)
        instrument = query.get("instrument", ["ETH-USDT"])[0]
        if parsed.path == "/api/status": self._send(SERVICE.status(instrument))
        elif parsed.path == "/api/replay": self._send(SERVICE.replay(instrument, query.get("at", [None])[0]))
        elif parsed.path == "/api/strategies": self._send({"items": RESEARCH.strategies()})
        elif parsed.path == "/api/backtest/history": self._send({"items": RESEARCH.repository.run_history()})
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
        try:
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length).decode() or "{}")
        except (ValueError, json.JSONDecodeError):
            self._send({"error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST); return
        if parsed.path == "/api/cycle": self._send(SERVICE.cycle())
        elif parsed.path == "/api/chat":
            result = SERVICE.chat(str(payload.get("question", "")), str(payload.get("instrument", "ETH-USDT")))
            self._send(result, HTTPStatus.BAD_REQUEST if "error" in result else HTTPStatus.OK)
        elif parsed.path == "/api/backtest/run":
            try: self._send(RESEARCH.start_backtest(payload), HTTPStatus.ACCEPTED)
            except ValueError as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/strategies":
            try: self._send(RESEARCH.save_strategy(payload), HTTPStatus.CREATED)
            except (ValueError, sqlite3.IntegrityError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/compare":
            try:
                items = RESEARCH.compare_strategies([int(value) for value in payload.get("strategy_ids", [])]) if "strategy_ids" in payload else RESEARCH.compare([int(value) for value in payload.get("run_ids", [])])
                self._send({"items": items})
            except (ValueError, TypeError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/walk-forward":
            try: self._send(RESEARCH.walk_forward(payload))
            except ValueError as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        elif parsed.path.startswith("/api/strategies/") and parsed.path.endswith("/duplicate"):
            try: self._send(RESEARCH.duplicate_strategy(int(parsed.path.strip("/").split("/")[2])), HTTPStatus.CREATED)
            except (ValueError, sqlite3.IntegrityError) as error: self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        else: self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length).decode() or "{}")
            strategy_id = int(urlparse(self.path).path.strip("/").split("/")[2])
            self._send(RESEARCH.save_strategy(payload, strategy_id))
        except (ValueError, IndexError, json.JSONDecodeError, sqlite3.IntegrityError) as error:
            self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:  # noqa: N802
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
        while True:
            SERVICE.cycle(); time.sleep(60)
    threading.Thread(target=scheduler, daemon=True).start()
    host = os.getenv("PAPER_API_HOST", "127.0.0.1")
    ThreadingHTTPServer((host, 8765), Handler).serve_forever()


if __name__ == "__main__":
    run()
