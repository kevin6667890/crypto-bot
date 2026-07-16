"""Local OKX paper-trading API.

Run with ``python dashboard/paper_api.py`` then open the Vite frontend.  The
service only consumes OKX public market data; it never accepts exchange keys
and never sends a live order.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:  # Streamlit secrets still work when dotenv is unavailable.
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data_cache"
DB_PATH = DATA_DIR / "paper_trades.db"
INSTRUMENT = "ETH-USDT"

load_dotenv(ROOT / ".env")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ema(values: list[float], period: int) -> float:
    multiplier = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


class PaperService:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        self.last_analysis: dict[str, Any] = {"status": "Starting", "action": "WAIT", "score": 0, "updated_at": now_iso()}
        self.last_ai_at = 0.0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, instrument TEXT NOT NULL,
                    side TEXT NOT NULL, entry REAL NOT NULL, stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN',
                    exit_price REAL, pnl_r REAL, reason TEXT, created_at TEXT NOT NULL,
                    closed_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analysis_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_briefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                    content TEXT NOT NULL, source TEXT NOT NULL
                )
            """)

    @staticmethod
    def _json(url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "crypto-bot-paper-research/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed public OKX URL
            return json.loads(response.read().decode("utf-8"))

    def _candles(self, bar: str, limit: int = 120) -> list[dict[str, float]]:
        payload = self._json(f"https://www.okx.com/api/v5/market/candles?instId={INSTRUMENT}&bar={bar}&limit={limit}")
        rows = payload.get("data", [])
        candles = [
            {"open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}
            for row in rows
        ]
        return list(reversed(candles))

    def _price(self) -> float:
        payload = self._json(f"https://www.okx.com/api/v5/market/ticker?instId={INSTRUMENT}")
        return float(payload["data"][0]["last"])

    @staticmethod
    def _rsi(closes: list[float], period: int = 14) -> float:
        changes = [b - a for a, b in zip(closes[-period - 1:-1], closes[-period:])]
        gains = sum(max(change, 0) for change in changes) / period
        losses = sum(max(-change, 0) for change in changes) / period
        if losses == 0:
            return 100.0
        return 100 - 100 / (1 + gains / losses)

    @staticmethod
    def _atr(candles: list[dict[str, float]], period: int = 14) -> float:
        sample = candles[-period - 1:]
        ranges = [max(row["high"] - row["low"], abs(row["high"] - previous["close"]), abs(row["low"] - previous["close"])) for previous, row in zip(sample, sample[1:])]
        return sum(ranges) / len(ranges)

    def analyze(self) -> dict[str, Any]:
        c15, c1h, c4h = self._candles("15m"), self._candles("1H"), self._candles("4H")
        closes15, closes1h, closes4h = ([row["close"] for row in candles] for candles in (c15, c1h, c4h))
        price = closes15[-1]
        ema15, ema1h, ema4h = ema(closes15[-50:], 20), ema(closes1h[-50:], 20), ema(closes4h[-50:], 20)
        ema1h50, ema4h50 = ema(closes1h[-80:], 50), ema(closes4h[-80:], 50)
        trend_long = closes4h[-1] > ema4h > ema4h50 and closes1h[-1] > ema1h > ema1h50
        trend_short = closes4h[-1] < ema4h < ema4h50 and closes1h[-1] < ema1h < ema1h50
        distance = (price - ema15) / ema15 * 100
        rsi = self._rsi(closes15)
        atr = self._atr(c15)
        volume_ratio = c15[-1]["volume"] / (sum(row["volume"] for row in c15[-21:-1]) / 20 or 1)
        pullback = abs(distance) <= 0.45
        score = (35 if trend_long or trend_short else 0) + (25 if pullback else 8) + (15 if volume_ratio >= 1 else 6) + (15 if 35 <= rsi <= 68 else 5)
        side = "LONG" if trend_long else "SHORT" if trend_short else "WAIT"
        action = side if side != "WAIT" and pullback and score >= 70 else "WAIT"
        analysis = {
            "instrument": INSTRUMENT, "price": round(price, 4), "action": action, "bias": side,
            "score": min(score, 100), "ema20": round(ema15, 4), "rsi14": round(rsi, 2),
            "atr14": round(atr, 4), "volume_ratio": round(volume_ratio, 2), "distance_ema20_pct": round(distance, 3),
            "conditions": [
                {"label": "4H + 1H trend", "value": "Aligned" if side != "WAIT" else "Mixed", "pass": side != "WAIT"},
                {"label": "EMA20 pullback", "value": f"{distance:+.2f}%", "pass": pullback},
                {"label": "15m volume", "value": f"{volume_ratio:.2f}x", "pass": volume_ratio >= 1},
                {"label": "RSI(14)", "value": f"{rsi:.1f}", "pass": 35 <= rsi <= 68},
            ],
            "updated_at": now_iso(),
        }
        with self._connect() as conn:
            conn.execute("INSERT INTO analysis_snapshots(created_at, payload) VALUES (?, ?)", (analysis["updated_at"], json.dumps(analysis)))
        self.last_analysis = analysis
        return analysis

    def _open_trade(self, analysis: dict[str, Any]) -> None:
        if analysis["action"] == "WAIT":
            return
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM paper_trades WHERE instrument=? AND status='OPEN'", (INSTRUMENT,)).fetchone():
                return
            entry, atr, side = analysis["price"], analysis["atr14"], analysis["action"]
            stop = entry - atr if side == "LONG" else entry + atr
            target = entry + 2 * atr if side == "LONG" else entry - 2 * atr
            conn.execute("INSERT INTO paper_trades(instrument,side,entry,stop_loss,take_profit,created_at) VALUES(?,?,?,?,?,?)", (INSTRUMENT, side, entry, stop, target, now_iso()))

    def maybe_create_ai_brief(self, analysis: dict[str, Any]) -> None:
        """Create at most one server-side AI summary per hour.

        Trading action is deliberately absent from the prompt: the deterministic
        rule engine remains the only component allowed to open a paper trade.
        """
        if time.time() - self.last_ai_at < 3600:
            return
        self.last_ai_at = time.time()
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            content, source = "AI brief disabled: set DEEPSEEK_API_KEY on the server to enable hourly market summaries.", "disabled"
        else:
            prompt = ("You are a cautious crypto market analyst. Summarize this OKX ETH-USDT rule snapshot in Chinese in at most 90 words. "
                      "Explain trend, momentum/volume and risk. Do not give investment advice or override the rule action. Snapshot: " + json.dumps(analysis))
            request = Request(
                "https://api.deepseek.com/chat/completions",
                data=json.dumps({"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 220}).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "crypto-bot-paper-research/1.0"},
            )
            try:
                with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed DeepSeek API URL
                    payload = json.loads(response.read().decode("utf-8"))
                content, source = payload["choices"][0]["message"]["content"].strip(), "DeepSeek"
            except Exception as error:
                content, source = f"AI brief unavailable: {error}", "error"
        with self._connect() as conn:
            conn.execute("INSERT INTO ai_briefs(created_at,content,source) VALUES(?,?,?)", (now_iso(), content, source))

    def chat(self, question: str) -> dict[str, str]:
        """Answer a market question with the latest stored, explainable context."""
        question = question.strip()[:1200]
        if not question:
            return {"error": "Please enter a question."}
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            return {"error": "DeepSeek is not configured on the server."}
        context = self.status()
        context["closed_trades"] = context["closed_trades"][:5]
        system = (
            "You are Crypto-Bot Market Copilot, a cautious quantitative-research assistant. "
            "Use only the supplied OKX market snapshot, deterministic rule output, paper positions and recent outcomes. "
            "Answer in Chinese, clearly distinguish facts from inference, mention uncertainty, and never claim certainty or place orders. "
            "This is research assistance, not financial advice."
        )
        request = Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps({"model": "deepseek-chat", "temperature": 0.25, "max_tokens": 500, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Context:\n{json.dumps(context, ensure_ascii=False)}\n\nQuestion: {question}"},
            ]}).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "crypto-bot-paper-research/1.0"},
        )
        try:
            with urlopen(request, timeout=25) as response:  # noqa: S310 - fixed DeepSeek API URL
                payload = json.loads(response.read().decode("utf-8"))
            return {"answer": payload["choices"][0]["message"]["content"].strip()}
        except Exception as error:
            return {"error": f"Copilot request failed: {error}"}

    def monitor_positions(self, price: float) -> None:
        with self._connect() as conn:
            positions = conn.execute("SELECT * FROM paper_trades WHERE status='OPEN'").fetchall()
            for row in positions:
                side, entry, stop, target = row["side"], row["entry"], row["stop_loss"], row["take_profit"]
                hit_stop = price <= stop if side == "LONG" else price >= stop
                hit_target = price >= target if side == "LONG" else price <= target
                if not (hit_stop or hit_target):
                    continue
                exit_price, reason = (target, "TAKE_PROFIT") if hit_target else (stop, "STOP_LOSS")
                risk = abs(entry - stop) or 1
                pnl_r = (exit_price - entry) / risk if side == "LONG" else (entry - exit_price) / risk
                status = "WIN" if pnl_r > 0 else "LOSS"
                conn.execute("UPDATE paper_trades SET status=?, exit_price=?, pnl_r=?, reason=?, closed_at=? WHERE id=?", (status, exit_price, pnl_r, reason, now_iso(), row["id"]))

    def cycle(self) -> dict[str, Any]:
        with self._lock:
            try:
                live_price = self._price()
                self.monitor_positions(live_price)
                analysis = self.analyze()
                self._open_trade(analysis)
                self.maybe_create_ai_brief(analysis)
                return analysis
            except Exception as error:  # expose a usable degraded state to the UI
                self.last_analysis = {"status": "Data unavailable", "action": "WAIT", "score": 0, "error": str(error), "updated_at": now_iso()}
                return self.last_analysis

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            open_trades = [dict(row) for row in conn.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY created_at DESC")]
            closed = [dict(row) for row in conn.execute("SELECT * FROM paper_trades WHERE status!='OPEN' ORDER BY closed_at DESC LIMIT 20")]
            brief = conn.execute("SELECT created_at, content, source FROM ai_briefs ORDER BY id DESC LIMIT 1").fetchone()
        wins = sum(1 for row in closed if row["status"] == "WIN")
        return {"analysis": self.last_analysis, "open_trades": open_trades, "closed_trades": closed, "ai_brief": dict(brief) if brief else None, "summary": {"open": len(open_trades), "closed": len(closed), "wins": wins, "win_rate": round(wins / len(closed) * 100, 1) if closed else 0, "total_r": round(sum(row["pnl_r"] or 0 for row in closed), 2)}}


SERVICE = PaperService()


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/status":
            self._send(SERVICE.status())
        else:
            self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/cycle":
            self._send(SERVICE.cycle())
        elif self.path == "/api/chat":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = SERVICE.chat(str(payload.get("question", "")))
                self._send(result, HTTPStatus.BAD_REQUEST if "error" in result else HTTPStatus.OK)
            except (ValueError, json.JSONDecodeError):
                self._send({"error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST)
        else:
            self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def run() -> None:
    def scheduler() -> None:
        while True:
            SERVICE.cycle()
            time.sleep(60)

    threading.Thread(target=scheduler, daemon=True).start()
    host = os.getenv("PAPER_API_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, 8765), Handler)
    print(f"Paper API listening on http://{host}:8765")
    server.serve_forever()


if __name__ == "__main__":
    run()
