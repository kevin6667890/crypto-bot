from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from dashboard.market_context_v2 import aggregate_confirmed_daily_to_weekly

from .canonical import stable_hash
from .versions import MAX_BARS, TIMEFRAME_SECONDS


def epoch(value: int | float | str) -> int:
    if isinstance(value, (int, float)):
        raw = int(value)
        return raw // 1000 if raw > 10_000_000_000 else raw
    if value.isdigit():
        raw = int(value)
        return raw // 1000 if raw > 10_000_000_000 else raw
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def iso(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_candles(rows: Iterable[dict[str, Any]], instrument: str, timeframe: str,
                      decision_time: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any]]:
    width = TIMEFRAME_SECONDS[timeframe]
    normalized: list[dict[str, Any]] = []
    live: dict[str, Any] | None = None
    seen: set[int] = set()
    invalid: list[str] = []
    for source_row in rows:
        row = dict(source_row)
        opened = epoch(row.get("open_time", row.get("ts")))
        closed = epoch(row.get("close_time", row.get("candle_close_ts", opened + width)))
        if opened in seen:
            invalid.append(f"duplicate:{opened}")
            continue
        seen.add(opened)
        values = {}
        try:
            values = {name: float(row[name]) for name in ("open", "high", "low", "close", "volume")}
        except (KeyError, TypeError, ValueError):
            invalid.append(f"invalid_numeric:{opened}")
            continue
        alignment_offset = 345_600 if timeframe == "1W" else 0  # Monday relative to Unix Thursday epoch
        aligned = (opened-alignment_offset) % width == 0 and closed == opened + width
        ohlc_valid = (values["high"] >= max(values["open"], values["close"]) and
                      values["low"] <= min(values["open"], values["close"]) and
                      values["low"] <= values["high"] and values["volume"] >= 0)
        if not aligned or not ohlc_valid:
            invalid.append(f"{'alignment' if not aligned else 'ohlc'}:{opened}")
            continue
        item = {
            "instrument": str(row.get("instrument") or instrument),
            "timeframe": timeframe, "ts": opened, "open_time": opened,
            "candle_close_ts": closed, "close_time": closed, **values,
            "confirmed": bool(row.get("confirmed", True)),
            "source": str(row.get("source") or "fixture"),
            "source_timestamp": epoch(row.get("source_timestamp", closed)),
            "quality": str(row.get("quality") or "VALID"),
            "gap_status": str(row.get("gap_status") or "NONE"),
        }
        if item["confirmed"] and closed <= decision_time:
            normalized.append(item)
        elif not item["confirmed"] and opened <= decision_time < closed:
            live = item
    normalized.sort(key=lambda item: item["ts"])
    normalized = normalized[-MAX_BARS[timeframe]:]
    gaps = []
    for left, right in zip(normalized, normalized[1:]):
        missing = (right["ts"] - left["ts"]) // width - 1
        if missing > 0:
            gaps.append({"start": left["close_time"], "end": right["open_time"], "missing_bars": missing})
    earliest = normalized[0]["open_time"] if normalized else None
    latest = normalized[-1]["close_time"] if normalized else None
    expected = ((normalized[-1]["open_time"] - earliest) // width + 1) if normalized else 0
    missing = sum(item["missing_bars"] for item in gaps)
    stale = bool(latest is not None and decision_time - latest > width * 2)
    warm = len(normalized) >= 200
    if invalid:
        status = "INVALID"
    elif not normalized:
        status = "MISSING"
    elif gaps:
        status = "GAP_AFFECTED"
    elif stale:
        status = "STALE"
    elif not warm:
        status = "WARMUP_INCOMPLETE"
    else:
        status = "COMPLETE"
    quality = {
        "status": status, "earliest": earliest, "latest": latest,
        "expected_bars": expected, "actual_bars": len(normalized),
        "missing_bars": missing, "largest_gap": max((g["missing_bars"] for g in gaps), default=0),
        "gaps": gaps, "warmup_complete": warm, "source_stale": stale,
        "incomplete_candle": live is not None, "notes": invalid,
    }
    return normalized, live, quality


def derive_weekly(daily_rows: Iterable[dict[str, Any]], instrument: str,
                  decision_time: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    weekly = aggregate_confirmed_daily_to_weekly(daily_rows, decision_time)
    for row in weekly:
        row.update(instrument=instrument, timeframe="1W", open_time=row["ts"],
                   close_time=row["candle_close_ts"], source_timestamp=row["candle_close_ts"],
                   quality="VALID", gap_status="NONE")
    _, _, daily_quality = normalize_candles(daily_rows, instrument, "1D", decision_time)
    weekly, _, quality = normalize_candles(weekly, instrument, "1W", decision_time)
    if daily_quality["missing_bars"] or daily_quality["status"] in {"INVALID", "MISSING"}:
        quality["status"] = "GAP_AFFECTED" if weekly else "MISSING"
        quality["notes"].append("1W requires seven contiguous confirmed UTC daily constituents")
    quality["derivation"] = "Monday 00:00 UTC; seven contiguous confirmed 1D bars"
    return weekly, quality


def input_fingerprint(rows: list[dict[str, Any]], quality: dict[str, Any]) -> str:
    fields = ("instrument", "timeframe", "open_time", "close_time", "open", "high", "low",
              "close", "volume", "confirmed", "source", "source_timestamp", "quality", "gap_status")
    return stable_hash({"bars": [{key: row.get(key) for key in fields} for row in rows],
                        "quality": quality})
