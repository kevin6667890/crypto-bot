"""OKX historical candle downloader with deterministic pagination and SQLite cache."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from research_repository import ResearchRepository
except ImportError:  # package imports in tests
    from .research_repository import ResearchRepository


TIMEFRAME_SECONDS = {"15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}
INSTRUMENTS = {"BTC-USDT", "ETH-USDT", "SOL-USDT"}


class OkxHistoryClient:
    def __init__(self, repository: ResearchRepository) -> None:
        self.repository = repository

    @staticmethod
    def _request(params: dict[str, Any]) -> list[list[str]]:
        url = "https://www.okx.com/api/v5/market/history-candles?" + urlencode(params)
        for attempt in range(7):
            request = Request(url, headers={"User-Agent": "crypto-bot-research/3.0"})
            try:
                with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed OKX endpoint
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                if error.code != 429 or attempt == 6:
                    raise
                retry_after = error.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 12))
                continue
            if payload.get("code") in {"50011", "50040"} and attempt < 6:
                time.sleep(min(2 ** attempt, 12)); continue
            if payload.get("code") != "0":
                raise RuntimeError(f"OKX history error: {payload.get('msg', 'unknown response')}")
            return payload.get("data", [])
        raise RuntimeError("OKX history request exhausted its retry budget.")

    def get_candles(self, instrument: str, timeframe: str, start_ts: int, end_ts: int, warmup_bars: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if instrument not in INSTRUMENTS or timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported instrument or timeframe.")
        step = TIMEFRAME_SECONDS[timeframe]
        requested_start = (start_ts // step) * step - warmup_bars * step
        now_ts = int(datetime.now(timezone.utc).timestamp())
        last_confirmed_start = (min(end_ts, now_ts) // step) * step - step
        minimum, maximum = self.repository.candle_coverage(instrument, timeframe)
        cache_complete = minimum is not None and maximum is not None and minimum <= requested_start and maximum >= last_confirmed_start
        fetched = 0
        if not cache_complete:
            cursor_ms = min((last_confirmed_start + step) * 1000, int(datetime.now(timezone.utc).timestamp() * 1000))
            oldest_seen: int | None = None
            for _page in range(5000):
                rows = self._request({"instId": instrument, "bar": timeframe, "after": str(cursor_ms), "limit": "100"})
                if not rows:
                    break
                parsed: dict[int, dict[str, Any]] = {}
                for row in rows:
                    ts = int(row[0]) // 1000
                    if len(row) >= 9 and row[8] != "1":
                        continue
                    parsed[ts] = {"ts": ts, "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]), "confirmed": 1}
                page = sorted(parsed.values(), key=lambda item: item["ts"])
                self.repository.upsert_candles(instrument, timeframe, page)
                fetched += len(page)
                if not page:
                    break
                new_oldest = page[0]["ts"]
                if oldest_seen is not None and new_oldest >= oldest_seen:
                    break
                oldest_seen = new_oldest
                if new_oldest <= requested_start:
                    break
                cursor_ms = new_oldest * 1000
                time.sleep(0.04)
        candles = self.repository.candles(instrument, timeframe, requested_start, end_ts)
        if not candles:
            raise RuntimeError("OKX returned no confirmed candles for the selected range.")
        duplicates = len(candles) - len({row["ts"] for row in candles})
        gaps = []
        for previous, current in zip(candles, candles[1:]):
            if current["ts"] - previous["ts"] > step:
                gaps.append({"after": previous["ts"], "before": current["ts"], "missing_bars": (current["ts"] - previous["ts"]) // step - 1})
        quality = {
            "source": "OKX public history-candles", "cached": cache_complete, "fetched_rows": fetched,
            "confirmed_rows": len(candles), "duplicates_after_deduplication": duplicates,
            "gap_count": len(gaps), "missing_bars": sum(gap["missing_bars"] for gap in gaps),
            "gaps": gaps[:20], "warmup_bars_requested": warmup_bars,
        }
        return candles, quality
