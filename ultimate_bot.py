# ultimate_bot.py — V9 Final
# Features: EMA20 pullback strategy + paper trading tracker + DeepSeek AI analysis + macro news alerts
# Backtest validated: 2-year PF 2.60 | Annual return +46% | Max drawdown 4.1%

import time
import os
import yaml
import logging
import datetime
import asyncio
import json
import io
import sqlite3
import re
import xml.etree.ElementTree as ET
from collections import deque
from typing import Any, Dict, List, Optional
import pathlib

import pandas as pd
import numpy as np
import aiohttp
import websockets
import mplfinance as mpf
from dotenv import load_dotenv
from rich.live import Live
from rich.text import Text

try:
    import pandas_ta_classic as ta
except ImportError:
    try:
        import pandas_ta_classic_classic as ta
    except ImportError:
        pass

# ==========================================
# Strategy engine
# ==========================================
try:
    from rules_blueprint import (
        get_market_session,
        compute_indicators as blueprint_compute_indicators,
        analyze_trend_structure,
        calculate_signal_score,
        generate_trade_plan,
    )
except ImportError as e:
    logging.error(f"rules_blueprint import failed: {e}")
    def get_market_session(*a, **k): return {"status": "OPEN", "min_score": 70}
    def blueprint_compute_indicators(df): return df
    def analyze_trend_structure(*a, **k): return {"direction": "UP"}
    def calculate_signal_score(*a, **k): return 80, "OK"
    def generate_trade_plan(*a, **k): return {"action": "LONG", "entry": 100.0, "sl": 90.0, "tp1": 120.0, "risk_usd": 10.0}

# ==========================================
# Environment variables
# ==========================================
load_dotenv(dotenv_path=pathlib.Path(__file__).parent / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
SERVERCHAN_KEY   = os.getenv("SERVERCHAN_KEY", "")
BASE_URL         = "https://fapi.binance.com"
WS_FORCE_URL     = "wss://fstream.binance.com/ws/!forceOrder@arr"

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/ultimate_bot.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ==========================================
# Utility functions
# ==========================================
def load_config() -> Optional[Dict]:
    try:
        with open("config_blueprint.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        logging.error(f"config load error: {e}")
        return {}

def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()

def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)

# ==========================================
# Paper trading recorder
# ==========================================
class PaperTrader:
    def __init__(self, db_path="paper_trades.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                                                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                                                      symbol TEXT,
                                                      action TEXT,
                                                      entry REAL,
                                                      sl REAL,
                                                      tp REAL,
                                                      be_trigger REAL DEFAULT 0,
                                                      be_moved INTEGER DEFAULT 0,
                                                      status TEXT DEFAULT 'OPEN',
                                                      exit_price REAL DEFAULT 0,
                                                      pnl_r REAL DEFAULT 0,
                                                      close_reason TEXT DEFAULT '',
                                                      create_time TEXT,
                                                      close_time TEXT DEFAULT ''
                )
            """)
            # Backwards compatibility for old table schema
            for col, defn in [
                ("be_trigger",   "REAL DEFAULT 0"),
                ("be_moved",     "INTEGER DEFAULT 0"),
                ("exit_price",   "REAL DEFAULT 0"),
                ("pnl_r",        "REAL DEFAULT 0"),
                ("close_reason", "TEXT DEFAULT ''"),
                ("close_time",   "TEXT DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {defn}")
                except Exception:
                    pass

    def open_trade(self, symbol: str, action: str, entry: float,
                   sl: float, tp: float, be_trigger: float = 0.0) -> int:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO trades (symbol,action,entry,sl,tp,be_trigger,status,create_time) "
                "VALUES (?,?,?,?,?,?,'OPEN',?)",
                (symbol, action, entry, sl, tp, be_trigger, utc_now_iso()),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_open_trades(self) -> List[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM trades WHERE status='OPEN'")]

    def update_sl(self, trade_id: int, new_sl: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE trades SET sl=?,be_moved=1 WHERE id=?", (new_sl, trade_id))

    def close_trade(self, trade_id: int, exit_price: float, reason: str, pnl_r: float):
        status = "WIN" if pnl_r > 0 else ("BE" if abs(pnl_r) < 0.05 else "LOSS")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE trades SET status=?,exit_price=?,pnl_r=?,close_reason=?,close_time=? WHERE id=?",
                (status, exit_price, pnl_r, reason, utc_now_iso(), trade_id),
            )

    def _query_closed(self, where: str = "", params: tuple = ()) -> List[dict]:
        sql = "SELECT * FROM trades WHERE status!='OPEN'"
        if where:
            sql += " AND " + where
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params)]

    def get_daily_summary(self) -> dict:
        today = utc_now().strftime("%Y-%m-%d")
        closed = self._query_closed("close_time LIKE ?", (f"{today}%",))
        with sqlite3.connect(self.db_path) as conn:
            open_count = conn.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]
        wins   = [t for t in closed if t["status"] == "WIN"]
        losses = [t for t in closed if t["status"] == "LOSS"]
        bes    = [t for t in closed if t["status"] == "BE"]
        return {
            "total": len(closed), "wins": len(wins), "losses": len(losses), "be": len(bes),
            "total_r": sum(t["pnl_r"] for t in closed), "open": open_count,
        }

    def get_all_summary(self) -> dict:
        closed = self._query_closed()
        with sqlite3.connect(self.db_path) as conn:
            open_count = conn.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]
        wins   = [t for t in closed if t["status"] == "WIN"]
        losses = [t for t in closed if t["status"] == "LOSS"]
        bes    = [t for t in closed if t["status"] == "BE"]
        total_r   = sum(t["pnl_r"] for t in closed)
        win_rate  = len(wins) / len(closed) * 100 if closed else 0
        loss_sum  = abs(sum(t["pnl_r"] for t in losses)) if losses else 0
        pf        = sum(t["pnl_r"] for t in wins) / loss_sum if loss_sum > 0 else 999
        return {
            "total": len(closed), "wins": len(wins), "losses": len(losses), "be": len(bes),
            "total_r": total_r, "win_rate": win_rate, "pf": pf, "open": open_count,
        }

    def get_recent_closed(self, n: int = 10) -> List[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT * FROM trades WHERE status!='OPEN' ORDER BY close_time DESC LIMIT ?", (n,)
            )]

# ==========================================
# WeChat push notifications
# ==========================================
class ServerChanNotifier:
    def __init__(self):
        key = os.getenv("SERVERCHAN_KEY", SERVERCHAN_KEY)
        self.url = f"https://sctapi.ftqq.com/{key}.send"

    async def send(self, title: str, content: str = ""):
        """Send via a standalone session, bypassing aiohttp SSL issues"""
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as s:
                resp = await s.post(
                    self.url,
                    data={"title": title[:32], "desp": content},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                result = await resp.json()
                if result.get("code") != 0:
                    logging.error(f"ServerChan error: {result}")
        except Exception as e:
            logging.error(f"ServerChan send failed: {e}")

    async def send_message(self, session, text: str, **kwargs):
        """Backwards-compatible interface"""
        lines = text.strip().split("\n", 1)
        await self.send(lines[0], lines[1] if len(lines) > 1 else "")

    async def send_photo_bytes(self, session, photo_bytes: bytes, caption: str = "", **kwargs):
        await self.send_message(session, caption)

# ==========================================
# DeepSeek client
# ==========================================
class DeepSeekClient:
    def __init__(self, api_key: str):
        self.api_key  = api_key
        self.base_url = "https://api.deepseek.com/v1"
        self.model    = "deepseek-chat"

    async def chat(self, prompt: str, max_tokens: int = 600) -> str:
        """Send a single chat request and return the text response"""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as s:
                async with s.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json()
                    return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            logging.error(f"DeepSeek error: {e}")
            return ""

# ==========================================
# Macro news monitor
# ==========================================
NEWS_SOURCES = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Reuters Macro", "https://feeds.reuters.com/reuters/businessNews"),
    ("Fed Watch",     "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadline"),
    ("CryptoSlate",   "https://cryptoslate.com/feed/"),
]

# Keywords that trigger alerts (Chinese/English)
MACRO_KEYWORDS = [
    "fed", "federal reserve", "interest rate", "fomc", "inflation", "cpi", "nonfarm",
    "sec", "regulation", "ban", "lawsuit", "hack", "exploit", "bankruptcy",
    "美联储", "加息", "降息", "通胀", "监管", "禁止", "黑客", "破产", "etf", "etf approved",
    "dollar index", "dxy", "treasury", "yield curve", "geopolitical",
    "trade war", "tariff", "bitcoin etf", "institutional",
    "美元指数", "国债", "收益率", "地缘", "贸易战", "关税", "机构",
]

class MacroNewsMonitor:
    def __init__(self, notifier: ServerChanNotifier, deepseek: DeepSeekClient):
        self.notifier  = notifier
        self.deepseek  = deepseek
        self.seen_ids: set = set()   # IDs of already-pushed articles, to prevent duplicates
        self.last_atr_ratio: float = 1.0

    def _is_important(self, title: str, summary: str) -> bool:
        text = (title + " " + summary).lower()
        return any(kw in text for kw in MACRO_KEYWORDS)

    async def _analyze_news(self, title: str, summary: str) -> str:
        prompt = f"""You are a professional cryptocurrency analyst. Below is a macro/crypto news item:

Title: {title}
Summary: {summary}

Please answer briefly in 3 lines or less:
1. Short-term (within 24h) impact on ETH price: Bullish / Bearish / Neutral?
2. Impact level: Major / Moderate / Minor?
3. What should traders pay attention to?

Format: Impact:XX | Level:XX | Advice:XX"""
        result = await self.deepseek.chat(prompt, max_tokens=150)
        return result.strip() if result else "Analysis temporarily unavailable"

    async def fetch_and_check(self):
        """Fetch RSS feeds and immediately alert on important news"""
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as s:
            for source_name, url in NEWS_SOURCES:
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            continue
                        text = await resp.text()

                    root = ET.fromstring(text)
                    # Support both standard RSS and Atom
                    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

                    for item in items[:10]:  # only look at the latest 10
                        # Extract title
                        title_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
                        title = title_el.text.strip() if title_el is not None and title_el.text else ""

                        # Extract summary
                        desc_el = (item.find("description") or
                                   item.find("{http://www.w3.org/2005/Atom}summary") or
                                   item.find("{http://www.w3.org/2005/Atom}content"))
                        summary = ""
                        if desc_el is not None and desc_el.text:
                            # Strip HTML tags
                            summary = re.sub(r"<[^>]+>", "", desc_el.text).strip()[:300]

                        # Deduplication key
                        item_key = f"{source_name}:{title[:60]}"
                        if item_key in self.seen_ids:
                            continue

                        if self._is_important(title, summary):
                            self.seen_ids.add(item_key)
                            analysis = await self._analyze_news(title, summary)
                            await self.notifier.send(
                                f"🚨 Macro Alert [{source_name}]",
                                f"Title: {title}\n\nSummary: {summary}\n\nAnalysis: {analysis}"
                            )
                            logging.info(f"Macro alert sent: {title}")

                except Exception as e:
                    logging.error(f"News fetch failed {source_name}: {e}")

    async def check_extreme_atr(self, df: pd.DataFrame, notifier: ServerChanNotifier, deepseek: DeepSeekClient):
        """Detect ATR anomalies (extreme market alert)"""
        try:
            if "atr" not in df.columns or len(df) < 20:
                return
            atr_now = float(df["atr"].iloc[-1])
            atr_avg = float(df["atr"].tail(20).mean())
            ratio   = atr_now / atr_avg if atr_avg > 0 else 1.0
            self.last_atr_ratio = ratio

            if ratio >= 2.0:  # ATR exceeds 2x the average
                close_now = float(df["close"].iloc[-1])
                prompt = f"""ETH current price {close_now:.2f}, ATR suddenly expanded to {ratio:.1f}x the average, indicating extreme market volatility.
Please answer briefly (within 2 lines):
1. What is the likely cause?
2. What is the impact and recommendation for the pullback entry strategy?"""
                analysis = await deepseek.chat(prompt, max_tokens=120)
                await notifier.send(
                    f"⚡ Extreme Market Alert ATR×{ratio:.1f}",
                    f"ETH: {close_now:.2f}\nATR is {ratio:.1f}x the average\n\n{analysis}\n\nRecommendation: pause following signals until volatility converges"
                )
                logging.info(f"ATR alert: ratio={ratio:.2f}")
        except Exception as e:
            logging.error(f"ATR check error: {e}")

# ==========================================
# Daily market report & strategy review
# ==========================================
class DailyReporter:
    def __init__(self, notifier: ServerChanNotifier, deepseek: DeepSeekClient, paper_trader: PaperTrader):
        self.notifier     = notifier
        self.deepseek     = deepseek
        self.paper_trader = paper_trader
        self.last_daily_date:  Optional[str] = None
        self.last_weekly_date: Optional[str] = None

    async def send_daily_report(self, df_15m: pd.DataFrame, df_4h: pd.DataFrame, curr_price: float):
        """Daily market report + paper-trading daily summary (UTC 08:00)"""
        now = utc_now()
        today_str = now.strftime("%Y-%m-%d")
        if self.last_daily_date == today_str:
            return
        if now.hour != 8 or now.minute > 10:
            return

        self.last_daily_date = today_str
        summary = self.paper_trader.get_daily_summary()
        all_s   = self.paper_trader.get_all_summary()

        # ETH 24h price data
        try:
            open_24h  = float(df_15m["close"].iloc[-96]) if len(df_15m) >= 96 else curr_price
            chg_24h   = (curr_price - open_24h) / open_24h * 100
            high_24h  = float(df_15m["high"].tail(96).max())
            low_24h   = float(df_15m["low"].tail(96).min())
            atr_now   = float(df_4h["atr"].iloc[-1]) if "atr" in df_4h.columns else 0
            ema20_now = float(df_15m["ema20"].iloc[-1]) if "ema20" in df_15m.columns else 0
        except Exception:
            open_24h = curr_price; chg_24h = 0; high_24h = curr_price
            low_24h  = curr_price; atr_now = 0; ema20_now = 0

        chg_str = f"+{chg_24h:.2f}%" if chg_24h >= 0 else f"{chg_24h:.2f}%"

        # DeepSeek generates the market commentary
        prompt = f"""today = {today_str}
ETH current price: {curr_price:.2f} USDT
24h change: {chg_str}
24h range: {low_24h:.2f} ~ {high_24h:.2f}
4H ATR (volatility): {atr_now:.2f}
EMA20 (15m): {ema20_now:.2f}

You are a senior cryptocurrency analyst. Please randomly pick 2 angles from the list below to analyze today's ETH market (vary the angles each day, don't repeat the same commentary every time):

Available angles:
- Funding rates and long/short positioning
- On-chain data and institutional behavior
- Macroeconomics and Federal Reserve policy
- Technical structure and key price levels
- Market sentiment and fear/greed
- Derivatives market and options
- Bitcoin correlation and market correlation

You must also include:
- The impact of the current global macro environment on the crypto market (dollar index, rate expectations, geopolitics)
- A concrete action suggestion for ETH today

Requirements:
- 3-4 sentences, concise and professional
- Avoid hollow phrases like "overall trending toward" or "caution is advised"
- Do not use "buy"/"sell"; use "reference price"/"target level" instead
- The analysis angle and conclusion must differ each time"""

        market_comment = await self.deepseek.chat(prompt, max_tokens=200)
        if not market_comment:
            market_comment = "Market analysis temporarily unavailable"

        # Paper trading data for today
        today_r_str = f"+{summary['total_r']:.2f}R" if summary['total_r'] >= 0 else f"{summary['total_r']:.2f}R"
        all_r_str   = f"+{all_s['total_r']:.2f}R"   if all_s['total_r'] >= 0   else f"{all_s['total_r']:.2f}R"

        report = (
            f"📊 ETH Daily Report {today_str}\n"
            f"---\n"
            f"Price: {curr_price:.2f} ({chg_str})\n"
            f"Range: {low_24h:.2f} ~ {high_24h:.2f}\n"
            f"EMA20: {ema20_now:.2f}\n\n"
            f"{market_comment}\n\n"
            f"---\n"
            f"Paper trades today: {summary['total']} | Wins {summary['wins']} | Losses {summary['losses']} | Breakeven {summary['be']} | {today_r_str}\n"
            f"Cumulative: {all_s['total']} trades | Win rate {all_s['win_rate']:.1f}% | PF {all_s['pf']:.2f} | {all_r_str}\n"
            f"Open positions: {all_s['open']}"
        )
        await self.notifier.send("📊 ETH Daily Report", report)
        logging.info("Daily report sent")

    async def send_weekly_report(self):
        """Weekly review (UTC Monday 08:05)"""
        now = utc_now()
        week_str = now.strftime("%Y-W%W")
        if self.last_weekly_date == week_str:
            return
        if now.weekday() != 0 or now.hour != 8 or not (5 <= now.minute <= 15):
            return

        self.last_weekly_date = week_str
        recent = self.paper_trader.get_recent_closed(20)
        all_s  = self.paper_trader.get_all_summary()

        if not recent:
            await self.notifier.send("📋 Weekly Review", "No completed paper trades this week")
            return

        # Format trade records for DeepSeek
        records_text = "\n".join([
            f"#{t['id']} {t['action']} Entry:{t['entry']:.2f} Exit:{t['exit_price']:.2f} "
            f"Reason:{t['close_reason']} PnL:{t['pnl_r']:+.2f}R"
            for t in recent
        ])

        prompt = f"""Below are the most recent {len(recent)} ETH paper trade records:

{records_text}

Cumulative stats: Total {all_s['total']} trades | Win rate {all_s['win_rate']:.1f}% | PF {all_s['pf']:.2f} | Total PnL {all_s['total_r']:+.2f}R

Please write a 4-5 sentence strategy review covering:
1. Overall performance assessment
2. What common characteristics do the losing trades share?
3. What common characteristics do the winning trades share?
4. Was this strategy well-suited to this week's market conditions?
5. What should be watched closely next week?

Do not use words like "buy"/"sell\""""

        analysis = await self.deepseek.chat(prompt, max_tokens=300)
        if not analysis:
            analysis = "Analysis temporarily unavailable"

        r_str = f"+{all_s['total_r']:.2f}R" if all_s['total_r'] >= 0 else f"{all_s['total_r']:.2f}R"
        report = (
            f"📋 Weekly Strategy Review {week_str}\n"
            f"---\n"
            f"Trades this week: {len(recent)}\n"
            f"Cumulative: {all_s['total']} trades | Win rate {all_s['win_rate']:.1f}% | PF {all_s['pf']:.2f}\n"
            f"Cumulative PnL: {r_str}\n\n"
            f"{analysis}"
        )
        await self.notifier.send("📋 Weekly Strategy Review", report)
        logging.info("Weekly review sent")

# ==========================================
# Closing trade commentary (DeepSeek single-trade analysis)
# ==========================================
async def analyze_closed_trade(
    deepseek: DeepSeekClient,
    notifier: ServerChanNotifier,
    trade: dict,
    curr_atr: float,
):
    """Generate a one-line commentary after each paper trade closes"""
    pnl_r     = trade.get("pnl_r", 0)
    action    = trade.get("action", "?")
    entry     = trade.get("entry", 0)
    exit_p    = trade.get("exit_price", 0)
    reason    = trade.get("close_reason", "")
    be_moved  = trade.get("be_moved", 0)

    result_word = "盈利" if pnl_r > 0 else ("保本" if abs(pnl_r) < 0.05 else "亏损")

    prompt = f"""An ETH paper trade has just closed:
Direction:{action} | Entry:{entry:.2f} | Exit:{exit_p:.2f} | Reason:{reason} | PnL:{pnl_r:+.2f}R | Moved to breakeven:{bool(be_moved)}
Current ATR:{curr_atr:.2f}

In 1-2 sentences, comment on this trade: was the entry well-placed? What could be improved?"""

    comment = await deepseek.chat(prompt, max_tokens=100)
    if not comment:
        return

    emoji   = "✅" if pnl_r > 0 else ("➖" if abs(pnl_r) < 0.05 else "❌")
    pnl_str = f"+{pnl_r:.2f}R" if pnl_r >= 0 else f"{pnl_r:.2f}R"

    await notifier.send(
        f"{emoji} Paper Trade Review #{trade['id']} {pnl_str}",
        f"Direction:{action} | {result_word}\nEntry:{entry:.2f} → Exit:{exit_p:.2f}\nReason:{reason}\n\nComment: {comment.strip()}"
    )

# ==========================================
# Order flow components
# ==========================================
class TacticalIntelligence:
    def __init__(self, symbol: str, cvd_window_minutes: int = 5):
        self.symbol        = symbol.replace("/", "").lower()
        self.cvd_window_ms = cvd_window_minutes * 60 * 1000
        self.cvd_window    = deque()
        self.cvd_rolling   = 0.0

    async def socket_handler(self):
        url = f"wss://fstream.binance.com/ws/{self.symbol}@aggTrade"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    while True:
                        msg  = await ws.recv()
                        data = json.loads(msg)
                        p, q, maker = float(data["p"]), float(data["q"]), data["m"]
                        ts  = int(data.get("T", int(time.time() * 1000)))
                        val = -(p * q) if maker else (p * q)
                        self.cvd_window.append((ts, val))
                        self.cvd_rolling += val
                        cut = ts - self.cvd_window_ms
                        while self.cvd_window and self.cvd_window[0][0] < cut:
                            self.cvd_rolling -= self.cvd_window.popleft()[1]
            except Exception:
                await asyncio.sleep(2)

class WhaleDetector:
    def __init__(self, symbol: str):
        self.symbol       = symbol.upper()
        self.whale_signal = "⚖️ Stable"

    async def fetch_oi_loop(self, session: aiohttp.ClientSession):
        while True:
            try:
                async with session.get(
                    f"{BASE_URL}/futures/data/openInterestHist",
                    params={"symbol": self.symbol, "period": "5m", "limit": 12}, timeout=5,
                ) as res:
                    data = await res.json()
                    if isinstance(data, list) and len(data) >= 2:
                        base, now = float(data[0]["sumOpenInterest"]), float(data[-1]["sumOpenInterest"])
                        if base > 0:
                            g = (now - base) / base * 100
                            self.whale_signal = (
                                f"🐋 In +{g:.1f}%" if g >= 2 else
                                f"🏹 Out {g:.1f}%" if g <= -2 else
                                f"⚖️ Flat {g:.1f}%"
                            )
            except Exception:
                pass
            await asyncio.sleep(10)

class LiquidationRadarPro:
    def __init__(self, symbol: str, window_minutes: int = 5):
        self.clean_symbol = symbol.replace("USDC","").replace("USDT","").upper()
        self.liq_history  = deque()
        self.window_ms    = window_minutes * 60 * 1000

    async def socket_handler(self):
        while True:
            try:
                async with websockets.connect(WS_FORCE_URL, ping_interval=20, ping_timeout=20) as ws:
                    while True:
                        d = json.loads(await ws.recv()).get("o", {})
                        if d.get("s", "").startswith(self.clean_symbol):
                            ts = int(d["T"])
                            self.liq_history.append({"p": float(d["p"]), "q": float(d["q"]), "S": d["S"], "T": ts})
                            cut = ts - self.window_ms
                            while self.liq_history and self.liq_history[0]["T"] < cut:
                                self.liq_history.popleft()
            except Exception:
                await asyncio.sleep(2)

    def get_analysis(self) -> dict:
        l5 = sum(i["p"]*i["q"] for i in self.liq_history if i["S"] == "BUY")
        s5 = sum(i["p"]*i["q"] for i in self.liq_history if i["S"] == "SELL")
        return {"long_5m_usd": l5, "short_5m_usd": s5}

class FundingRateTracker:
    def __init__(self, symbol: str):
        self.symbol       = symbol.upper()
        self.funding_rate = 0.0

    async def fetch_loop(self, session: aiohttp.ClientSession):
        while True:
            try:
                async with session.get(
                    f"{BASE_URL}/fapi/v1/premiumIndex",
                    params={"symbol": self.symbol}, timeout=5,
                ) as res:
                    data = await res.json()
                    self.funding_rate = float(data.get("lastFundingRate", 0.0)) * 100
            except Exception:
                pass
            await asyncio.sleep(300)

# ==========================================
# Data fetching
# ==========================================
async def fetch_klines(session: aiohttp.ClientSession, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
    for _ in range(3):
        try:
            async with session.get(
                f"{BASE_URL}/fapi/v1/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=5,
            ) as res:
                data = await res.json()
                if isinstance(data, list) and data:
                    cols = ["ts","open","high","low","close","volume","ct","q","t","tb","tq","i"]
                    df = pd.DataFrame(data, columns=cols)
                    df[["open","high","low","close","volume"]] = df[["open","high","low","close","volume"]].astype(float)
                    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
                    return df
        except Exception:
            await asyncio.sleep(1)
    return pd.DataFrame()

def get_vpvr_poc(df: pd.DataFrame) -> float:
    if df.empty or "high" not in df.columns:
        return 0.0
    try:
        lo, hi    = df["low"].min(), df["high"].max()
        edges     = np.linspace(lo, hi, 37)
        vol_bins  = np.zeros(36)
        for _, r in df.iterrows():
            i0 = max(0, min(35, int(np.searchsorted(edges, r["low"],  side="right") - 1)))
            i1 = max(0, min(35, int(np.searchsorted(edges, r["high"], side="left"))))
            if i1 < i0: i0, i1 = i1, i0
            cnt = max(1, i1 - i0 + 1)
            vol_bins[i0:i1+1] += r["volume"] / cnt
        poc_idx = int(np.argmax(vol_bins))
        return float((edges[poc_idx] + edges[poc_idx+1]) / 2)
    except Exception:
        return 0.0

def generate_chart_bytes(df: pd.DataFrame, symbol: str, plan: dict, poc: float) -> Optional[bytes]:
    if df.empty:
        return None
    try:
        dp = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        dp.set_index("ts", inplace=True)
        subset = dp.tail(50).copy()
        mc = mpf.make_marketcolors(up="#089981", down="#f23645", inherit=True)
        s  = mpf.make_mpf_style(marketcolors=mc, style="nightclouds", y_on_right=True)
        hlines_data, colors, widths, styles = [], [], [], []
        for val, col, w, st in [
            (plan.get("entry", 0), "white", 1, "-"),
            (plan.get("sl", 0),    "red",   1.5, "--"),
            (plan.get("tp1", 0),   "green", 1.5, "--"),
            (poc,                  "yellow",1,   "-"),
        ]:
            if val and val > 0:
                hlines_data.append(val); colors.append(col); widths.append(w); styles.append(st)
        hlines = dict(hlines=hlines_data, colors=colors, linewidths=widths, linestyle=styles)
        buf = io.BytesIO()
        mpf.plot(subset, type="candle", style=s, hlines=hlines,
                 title=f"{symbol} Setup",
                 savefig=dict(fname=buf, dpi=100, bbox_inches="tight", format="png"))
        buf.seek(0)
        return buf.read()
    except Exception:
        return None

# ==========================================
# Paper trading background monitor
# ==========================================
async def paper_trading_watcher(
    paper_trader: PaperTrader,
    notifier: ServerChanNotifier,
    deepseek: DeepSeekClient,
    session: aiohttp.ClientSession,
    symbol_binance: str,
):
    while True:
        try:
            async with session.get(
                f"{BASE_URL}/fapi/v1/ticker/price", params={"symbol": symbol_binance},
            ) as res:
                curr_price = float((await res.json())["price"])

            for t in paper_trader.get_open_trades():
                tid        = t["id"]
                action     = t["action"]
                entry      = float(t["entry"])
                sl         = float(t["sl"])
                tp         = float(t["tp"])
                be_trigger = float(t.get("be_trigger") or 0)
                be_moved   = int(t.get("be_moved") or 0)
                init_dist  = abs(entry - sl)

                # Breakeven trigger
                if be_trigger > 0 and not be_moved:
                    triggered = (action == "LONG" and curr_price >= be_trigger) or \
                                (action == "SHORT" and curr_price <= be_trigger)
                    if triggered:
                        paper_trader.update_sl(tid, entry)
                        sl = entry
                        await notifier.send(
                            f"🔒 Paper Trade #{tid} Breakeven Triggered",
                            f"Direction:{action} | Entry:{entry:.2f}\nStop-loss moved to entry price {entry:.2f}",
                        )

                # Exit detection
                close_reason = None
                exit_price   = curr_price
                if action == "LONG":
                    if curr_price <= sl:
                        close_reason = "保本" if be_moved else "止损"; exit_price = sl
                    elif curr_price >= tp:
                        close_reason = "止盈"; exit_price = tp
                else:
                    if curr_price >= sl:
                        close_reason = "保本" if be_moved else "止损"; exit_price = sl
                    elif curr_price <= tp:
                        close_reason = "止盈"; exit_price = tp

                if close_reason:
                    pnl_r = ((exit_price - entry) / init_dist if action == "LONG"
                             else (entry - exit_price) / init_dist) if init_dist > 0 else 0.0
                    paper_trader.close_trade(tid, exit_price, close_reason, pnl_r)

                    emoji   = "✅" if pnl_r > 0 else ("➖" if abs(pnl_r) < 0.05 else "❌")
                    pnl_str = f"+{pnl_r:.2f}R" if pnl_r >= 0 else f"{pnl_r:.2f}R"
                    await notifier.send(
                        f"{emoji} Paper Trade #{tid} Closed {pnl_str}",
                        f"Direction:{action} | Reason:{close_reason}\nEntry:{entry:.2f} → Exit:{exit_price:.2f}",
                    )

                    # Post-close DeepSeek commentary (async, doesn't block the main loop)
                    closed_trade = dict(t)
                    closed_trade.update({"exit_price": exit_price, "pnl_r": pnl_r,
                                         "close_reason": close_reason, "be_moved": be_moved})
                    asyncio.create_task(analyze_closed_trade(deepseek, notifier, closed_trade, 0))

        except Exception as e:
            logging.error(f"paper watcher error: {e}")

        await asyncio.sleep(10)

# ==========================================
# Main flow
# ==========================================
async def async_main():
    print("🚀 Ultimate Bot V9 starting...")

    cfg              = load_config() or {}
    main_symbol      = "ETH/USDT"
    main_symbol_b    = "ETHUSDT"

    paper_trader = PaperTrader()
    notifier     = ServerChanNotifier()
    deepseek     = DeepSeekClient(DEEPSEEK_API_KEY)
    news_monitor = MacroNewsMonitor(notifier, deepseek)
    reporter     = DailyReporter(notifier, deepseek, paper_trader)

    tactical = TacticalIntelligence(main_symbol_b)
    radar    = LiquidationRadarPro(main_symbol_b)
    whale    = WhaleDetector(main_symbol_b)
    funding  = FundingRateTracker(main_symbol_b)

    asyncio.create_task(tactical.socket_handler())
    asyncio.create_task(radar.socket_handler())

    async with aiohttp.ClientSession() as req_session:
        asyncio.create_task(whale.fetch_oi_loop(req_session))
        asyncio.create_task(funding.fetch_loop(req_session))
        asyncio.create_task(paper_trading_watcher(paper_trader, notifier, deepseek, req_session, main_symbol_b))

        await notifier.send(
            "✅ Ultimate Bot V9 Started",
            "Strategy: EMA20 pullback + 3R target + 1R breakeven\n"
            "New: Macro alerts | Daily report | Weekly review | Trade close commentary\n"
            "Backtest: 2-year PF 2.60 | Annual return +46% | Max drawdown 4.1%",
        )

        last_candle:    dict  = {}
        last_news_check: float = 0.0
        pending_signals: list  = []

        with Live(Text("Scanning..."), refresh_per_second=2, screen=False) as live:
            while True:
                try:
                    now = utc_now()

                    # ── News check (every 30 minutes) ─────────────────────────
                    if time.time() - last_news_check > 1800:
                        last_news_check = time.time()
                        asyncio.create_task(news_monitor.fetch_and_check())

                    session_info = get_market_session(
                        cfg.get("session", {}).get("timezone", "America/New_York"),
                        cfg.get("session", {}).get("segments", {}),
                    )
                    if session_info.get("status") == "CLOSED":
                        live.update(Text(f"💤 Sleeping ({session_info.get('segment')})"))
                        await asyncio.sleep(10)
                        continue

                    # ── Fetch klines ──────────────────────────────────────
                    df_c, df_m, df_l = await asyncio.gather(
                        fetch_klines(req_session, main_symbol_b, "15m", 300),
                        fetch_klines(req_session, main_symbol_b, "1h",  300),
                        fetch_klines(req_session, main_symbol_b, "4h",  300),
                    )
                    if df_c.empty or df_m.empty or df_l.empty:
                        await asyncio.sleep(5)
                        continue

                    df_c = blueprint_compute_indicators(df_c)
                    df_m = blueprint_compute_indicators(df_m)
                    df_l = blueprint_compute_indicators(df_l)

                    curr_price = float(df_c.iloc[-1]["close"])
                    current_ts = df_c.iloc[-1]["ts"]
                    ema20_now  = float(df_c.iloc[-1].get("ema20", 0) or 0)
                    atr_now    = float(df_c.iloc[-1].get("atr",   0) or 0)

                    # ── ATR extreme market alert ───────────────────────────────
                    asyncio.create_task(
                        news_monitor.check_extreme_atr(df_c, notifier, deepseek)
                    )

                    # ── Daily / weekly reports ────────────────────────────────
                    asyncio.create_task(reporter.send_daily_report(df_c, df_l, curr_price))
                    asyncio.create_task(reporter.send_weekly_report())

                    # ── Check pending signals (EMA20 pullback) ────────────────────
                    triggered_sigs = []
                    expired_sigs   = []

                    for sig in pending_signals:
                        age_min = (current_ts - sig["signal_ts"]).total_seconds() / 60
                        if age_min > 40:
                            expired_sigs.append(sig)
                            continue
                        if ema20_now > 0:
                            if ema20_now * 0.997 <= curr_price <= ema20_now * 1.003:
                                triggered_sigs.append(sig)

                    for sig in expired_sigs:
                        pending_signals.remove(sig)
                        live.update(Text(f"⏰ Signal expired: {sig['action']} @ {sig['signal_price']:.2f}"))

                    for sig in triggered_sigs:
                        pending_signals.remove(sig)

                        new_entry   = curr_price
                        sl_dist     = abs(sig["original_entry"] - sig["original_sl"])
                        if sl_dist == 0:
                            continue

                        if sig["action"] == "LONG":
                            new_sl  = new_entry - sl_dist
                            new_tp  = new_entry + sl_dist * 3.0
                            new_be  = new_entry + sl_dist * 1.0
                        else:
                            new_sl  = new_entry + sl_dist
                            new_tp  = new_entry - sl_dist * 3.0
                            new_be  = new_entry - sl_dist * 1.0

                        plan = {**sig["plan"], "entry": new_entry, "sl": new_sl,
                                "tp1": new_tp, "breakeven_trigger": new_be}

                        rr   = abs(new_tp - new_entry) / sl_dist
                        poc  = get_vpvr_poc(df_c.tail(100))

                        await notifier.send(
                            f"🎯 Pullback Signal Triggered {sig['action']}",
                            f"Symbol: {main_symbol}\n"
                            f"Direction: {sig['action']}\n"
                            f"Reference price: {new_entry:.2f} (EMA20)\n"
                            f"Protection level: {new_sl:.2f}\n"
                            f"Target: {new_tp:.2f} ({rr:.1f}R)\n"
                            f"Move-to-breakeven trigger: {new_be:.2f} (1R)\n"
                            f"Score: {sig['score']}/{sig['min_score']}",
                        )

                        tid = paper_trader.open_trade(
                            main_symbol, sig["action"], new_entry, new_sl, new_tp, be_trigger=new_be
                        )
                        logging.info(f"Paper trade #{tid} opened: {sig['action']} @ {new_entry:.2f}")

                    # ── Signal generation ───────────────────────────────────────
                    trend     = analyze_trend_structure(df_l, df_m, df_c)
                    min_score = session_info.get("min_score", 70)

                    suffix = f" | Pending:{len(pending_signals)}" if pending_signals else ""
                    if trend.get("direction") != "NEUTRAL":
                        score, det = calculate_signal_score(df_c, trend, cfg.get("score_weights", {}))
                        live.update(Text(
                            f"🔍 {main_symbol} | {trend.get('direction')} | "
                            f"Score:{score}/{min_score} | {det}{suffix}"
                        ))

                        if score >= min_score and last_candle.get(main_symbol) != current_ts:
                            last_candle[main_symbol] = current_ts
                            plan = generate_trade_plan(df_c, trend, score, session_info, cfg)

                            if plan and plan.get("tp1") and plan["tp1"] != 0:
                                plan["entry"] = curr_price
                                pending_signals.append({
                                    "signal_ts":     current_ts,
                                    "signal_price":  curr_price,
                                    "action":        plan["action"],
                                    "original_entry": plan["entry"],
                                    "original_sl":    plan["sl"],
                                    "score":          score,
                                    "min_score":      min_score,
                                    "plan":           plan,
                                })
                                live.update(Text(
                                    f"⏳ Waiting for EMA20 pullback: {plan['action']} @ {curr_price:.2f} "
                                    f"| EMA20: {ema20_now:.2f} | Score:{score}"
                                ))
                    else:
                        live.update(Text(f"💤 {main_symbol} | NEUTRAL{suffix}"))

                    await asyncio.sleep(10)

                except Exception as e:
                    logging.error(f"Main loop error: {e}")
                    live.update(Text(f"❌ Error: {e} (retrying in 5s)", style="bold red"))
                    await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nBot stopped.")