"""Causal, read-only market facts and indicators for Market Context V2.

This module deliberately has no strategy, signal, order, backfill, or maintenance
dependency.  Callers provide confirmed OHLCV rows or use the bounded SQLite
reader; missing observations remain missing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import closing
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
import statistics
import threading
import hashlib
import json
import time
from typing import Any, Iterable

try:
    from discovery_features import build_features
    from volume_profile import calculate_volume_profile
except ImportError:
    from .discovery_features import build_features
    from .volume_profile import calculate_volume_profile


CONTEXT_VERSION = "market-analysis-context-v2"
INDICATOR_REGISTRY_VERSION = "market-indicator-registry-v2"
STOCH_RSI_VERSION = "stoch-rsi-v2-rsi14-stoch14-k3-d3"
WEEKLY_AGGREGATION_VERSION = "utc-monday-weekly-v1"
CONFLUENCE_VERSION = "market-level-confluence-v2"
CONFLUENCE_THRESHOLD_PCT = 0.25
LEVEL_TOUCH_THRESHOLD_PCT = 0.10
SWING_VERSION = "confirmed-fractal-2x2-v1"
TIMEFRAME_SECONDS = {"15m": 900, "1H": 3_600, "4H": 14_400, "1D": 86_400, "1W": 604_800}
SUPPORTED_TIMEFRAMES = tuple(TIMEFRAME_SECONDS)
DEFAULT_LOOKBACK = {"15m": 512, "1H": 512, "4H": 512, "1D": 1_500}
FLOW_WINDOW_SECONDS = 4 * 900
FLOW_STALE_SECONDS = 180
CACHE_TTL_SECONDS = 5.0
TIMEFRAME_REQUIRED_BARS = {timeframe: 200 for timeframe in TIMEFRAME_SECONDS}
TIMEFRAME_CONSUMERS = ("Workspace rule trend signal", "Market structure engine",
                       "AI deterministic claim pack")

INDICATOR_GROUP_KEYS = {
    "trend": ("ema20", "ma60", "ma200", "ema20_slope", "ma60_slope", "ma200_slope",
              "close_distance_to_ema20", "close_distance_to_ma60", "close_distance_to_ma200", "ma_arrangement"),
    "momentum": ("rsi14", "stoch_rsi", "stoch_rsi_k", "stoch_rsi_d", "price_momentum", "momentum_persistence"),
    "volatility": ("atr14", "atr_percentage", "bollinger_upper", "bollinger_mid", "bollinger_lower",
                   "bollinger_bandwidth", "realized_volatility", "compression_percentile", "expansion_percentile"),
    "structure": ("recent_confirmed_swing_high", "recent_confirmed_swing_low", "rolling_high_distance", "rolling_low_distance"),
    "volume": ("volume", "volume_moving_average", "volume_ratio", "candle_body_percentage",
               "upper_wick_percentage", "lower_wick_percentage"),
}


@dataclass(frozen=True)
class IndicatorValueV2:
    value: float | str | None
    source_timestamp: int | None
    available: bool
    stale: bool = False
    partial: bool = False
    warmup_complete: bool = True
    calculation_version: str = INDICATOR_REGISTRY_VERSION


@dataclass(frozen=True)
class DataQualityV2:
    status: str
    source_timestamp: int | None = None
    stale: bool = False
    partial: bool = False
    missing: bool = False
    gaps: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketLevelV2:
    type: str
    timeframe: str
    value: float
    source_timestamp: int
    distance_pct: float
    touches: int
    confirmed: bool
    confluence_sources: tuple[str, ...] = ()
    calculation_version: str = CONFLUENCE_VERSION


@dataclass(frozen=True)
class TimeframeMarketContextV2:
    candle_close_ts: int | None
    confirmed: bool
    trend: dict[str, IndicatorValueV2]
    momentum: dict[str, IndicatorValueV2]
    volatility: dict[str, IndicatorValueV2]
    structure: dict[str, IndicatorValueV2]
    volume: dict[str, IndicatorValueV2]
    quality: DataQualityV2


@dataclass(frozen=True)
class FlowContextV2:
    cvd: dict[str, IndicatorValueV2]
    oi: dict[str, IndicatorValueV2]
    funding: dict[str, IndicatorValueV2]
    basis: dict[str, IndicatorValueV2]
    vpvr: dict[str, IndicatorValueV2]
    price_oi_combination: dict[str, Any]
    price_cvd_combination: dict[str, Any]


@dataclass(frozen=True)
class MarketAnalysisContextV2:
    version: str
    instrument: str
    as_of: int
    execution_timeframe: str
    price: IndicatorValueV2
    timeframes: dict[str, TimeframeMarketContextV2]
    flow: FlowContextV2
    levels: tuple[MarketLevelV2, ...]
    quality: dict[str, Any]
    context_identity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TimeframeObservation:
    instrument: str
    timeframe: str
    observed_at: int
    source_at: int | None
    oldest_at: int | None
    bar_count: int
    required_bar_count: int
    freshness_seconds: int | None
    freshness_limit_seconds: int
    availability: str
    quality: str
    structure_state: str | None
    reason_codes: tuple[str, ...]
    source_exchange: str = "OKX"
    source_store: str = "unknown"
    symbol: str = ""
    raw_interval: str = ""
    aggregation_mechanism: str = "native confirmed candle"
    latest_raw_candle_timestamp: int | None = None
    latest_aggregated_candle_timestamp: int | None = None
    indicator_warmup_requirement: int = 200
    coverage_state: str = "MISSING"
    registry_state: str = "WARMUP_INCOMPLETE"
    consumers: tuple[str, ...] = TIMEFRAME_CONSUMERS


def _null(timestamp: int | None = None, *, warmup: bool = False,
          stale: bool = False, partial: bool = False,
          version: str = INDICATOR_REGISTRY_VERSION) -> IndicatorValueV2:
    return IndicatorValueV2(None, timestamp, False, stale, partial, warmup, version)


def _value(value: float | str | None, timestamp: int | None, *, stale: bool = False,
           partial: bool = False, warmup: bool = True,
           version: str = INDICATOR_REGISTRY_VERSION) -> IndicatorValueV2:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return _null(timestamp, warmup=warmup, stale=stale, partial=partial, version=version)
    return IndicatorValueV2(value, timestamp, True, stale, partial, warmup, version)


def _close_ts(row: dict[str, Any], timeframe: str) -> int:
    return int(row.get("candle_close_ts") or (int(row["ts"]) + TIMEFRAME_SECONDS[timeframe]))


def confirmed_candles_as_of(rows: Iterable[dict[str, Any]], timeframe: str,
                            as_of: int) -> list[dict[str, Any]]:
    """Return ordered candles whose explicit close is confirmed no later than as_of."""
    selected: dict[int, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        close_timestamp = _close_ts(row, timeframe)
        if bool(row.get("confirmed", True)) and close_timestamp <= int(as_of):
            row["candle_close_ts"] = close_timestamp
            row["confirmed"] = True
            selected[int(row["ts"])] = row
    return [selected[key] for key in sorted(selected)]


def _week_start(timestamp: int) -> int:
    dt = datetime.fromtimestamp(timestamp, timezone.utc)
    midnight = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())
    return midnight - dt.weekday() * 86_400


def aggregate_confirmed_daily_to_weekly(rows: Iterable[dict[str, Any]], as_of: int) -> list[dict[str, Any]]:
    """Aggregate seven confirmed UTC days into Monday-based confirmed weeks."""
    daily = confirmed_candles_as_of(rows, "1D", as_of)
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in daily:
        groups.setdefault(_week_start(int(row["ts"])), []).append(row)
    result: list[dict[str, Any]] = []
    for start, items in sorted(groups.items()):
        ordered = sorted(items, key=lambda item: int(item["ts"]))
        expected = [start + day * 86_400 for day in range(7)]
        timestamps = [int(item["ts"]) for item in ordered]
        close_timestamp = start + TIMEFRAME_SECONDS["1W"]
        if timestamps != expected or close_timestamp > int(as_of):
            continue
        result.append({
            "ts": start, "candle_close_ts": close_timestamp,
            "open": float(ordered[0]["open"]),
            "high": max(float(item["high"]) for item in ordered),
            "low": min(float(item["low"]) for item in ordered),
            "close": float(ordered[-1]["close"]),
            "volume": sum(float(item["volume"]) for item in ordered),
            "confirmed": True, "source": WEEKLY_AGGREGATION_VERSION,
        })
    return result


def stoch_rsi_series(rsi_values: list[float | None], *, lookback: int = 14,
                     k_smoothing: int = 3, d_smoothing: int = 3) -> list[dict[str, float | None]]:
    """Causal Stoch RSI: raw=(RSI-min)/(max-min)*100, K=SMA3(raw), D=SMA3(K).

    A flat RSI window has an undefined denominator and therefore yields null;
    nulls are never replaced by zero or a directional neutral value.
    """
    raw_values: list[float | None] = []
    k_values: list[float | None] = []
    output: list[dict[str, float | None]] = []
    for index, current in enumerate(rsi_values):
        raw: float | None = None
        if current is not None and index + 1 >= lookback:
            window = rsi_values[index - lookback + 1:index + 1]
            if all(value is not None for value in window):
                finite = [float(value) for value in window if value is not None]
                low, high = min(finite), max(finite)
                raw = (float(current) - low) / (high - low) * 100 if high > low else None
        raw_values.append(raw)
        k: float | None = None
        if len(raw_values) >= k_smoothing:
            window = raw_values[-k_smoothing:]
            if all(value is not None for value in window):
                k = sum(float(value) for value in window if value is not None) / k_smoothing
        k_values.append(k)
        d: float | None = None
        if len(k_values) >= d_smoothing:
            window = k_values[-d_smoothing:]
            if all(value is not None for value in window):
                d = sum(float(value) for value in window if value is not None) / d_smoothing
        output.append({"stoch_rsi": raw, "stoch_rsi_k": k, "stoch_rsi_d": d})
    return output


def _percentile_rank(history: list[float], current: float) -> float | None:
    if len(history) < 20:
        return None
    return sum(value <= current for value in history) / len(history) * 100


def _confirmed_swing(candles: list[dict[str, Any]], field: str, *, wing: int = 2) -> tuple[float | None, int | None]:
    """Latest pivot confirmed by `wing` later, already closed candles."""
    if len(candles) < wing * 2 + 1:
        return None, None
    for pivot in range(len(candles) - wing - 1, wing - 1, -1):
        value = float(candles[pivot][field])
        left = [float(candles[pos][field]) for pos in range(pivot - wing, pivot)]
        right = [float(candles[pos][field]) for pos in range(pivot + 1, pivot + wing + 1)]
        if (field == "high" and value > max(left) and value >= max(right)) or (
                field == "low" and value < min(left) and value <= min(right)):
            return value, int(candles[pivot + wing]["candle_close_ts"])
    return None, None


def _quality(candles: list[dict[str, Any]], timeframe: str, as_of: int,
             *, partial: bool = False) -> DataQualityV2:
    if not candles:
        return DataQualityV2("MISSING", missing=True, partial=partial,
                             notes=("no confirmed candle at or before as_of",))
    width = TIMEFRAME_SECONDS[timeframe]
    gaps = tuple({"start": int(a["candle_close_ts"]), "end": int(b["candle_close_ts"]),
                  "missing_bars": max(0, (int(b["ts"]) - int(a["ts"])) // width - 1)}
                 for a, b in zip(candles, candles[1:])
                 if int(b["ts"]) - int(a["ts"]) > width)
    latest = int(candles[-1]["candle_close_ts"])
    stale = as_of - latest > width * 2
    warmup_incomplete = len(candles) < TIMEFRAME_REQUIRED_BARS[timeframe]
    status = "STALE" if stale else "PARTIAL" if partial or gaps or warmup_incomplete else "AVAILABLE"
    notes = ((f"indicator warmup requires {TIMEFRAME_REQUIRED_BARS[timeframe]} bars; "
              f"{len(candles)} available",) if warmup_incomplete else ())
    return DataQualityV2(status, latest, stale, partial or bool(gaps) or warmup_incomplete,
                         False, gaps, notes)


def timeframe_observation(instrument: str, timeframe: str,
                          candles: list[dict[str, Any]], as_of: int,
                          quality: DataQualityV2, *,
                          source_rows: list[dict[str, Any]] | None = None) -> TimeframeObservation:
    rows = confirmed_candles_as_of(candles, timeframe, as_of)
    required = TIMEFRAME_REQUIRED_BARS[timeframe]
    source_at = int(rows[-1]["candle_close_ts"]) if rows else None
    oldest_at = int(rows[0]["ts"]) if rows else None
    freshness = max(0, int(as_of)-source_at) if source_at is not None else None
    reasons: list[str] = []
    if not rows:
        availability = "MISSING"; reasons.append("NO_CONFIRMED_CANDLES")
    elif quality.stale:
        availability = "STALE"; reasons.append("FRESHNESS_LIMIT_EXCEEDED")
    elif len(rows) < required:
        availability = "PARTIAL"; reasons.append("INDICATOR_WARMUP_INCOMPLETE")
    elif quality.partial or quality.gaps:
        availability = "PARTIAL"; reasons.append("SOURCE_PARTIAL_OR_GAPPED")
    else:
        availability = "AVAILABLE"
    source_store = str(rows[-1].get("_source_store") or "derived") if rows else "unknown"
    raw = source_rows if source_rows is not None else rows
    latest_raw = int(raw[-1].get("candle_close_ts") or _close_ts(raw[-1], "1D")) if raw else None
    weekly = timeframe == "1W"
    return TimeframeObservation(
        instrument=instrument, timeframe=timeframe, observed_at=int(as_of),
        source_at=source_at, oldest_at=oldest_at, bar_count=len(rows),
        required_bar_count=required, freshness_seconds=freshness,
        freshness_limit_seconds=TIMEFRAME_SECONDS[timeframe]*2,
        availability=availability, quality=quality.status, structure_state=None,
        reason_codes=tuple(reasons), source_store=source_store,
        symbol=instrument, raw_interval="1Dutc" if weekly or timeframe == "1D" else timeframe,
        aggregation_mechanism=(WEEKLY_AGGREGATION_VERSION if weekly else "native confirmed OKX candle"),
        latest_raw_candle_timestamp=latest_raw,
        # Native OKX frames are already materialized observations; weekly is
        # the only frame resampled locally.  In both cases ``source_at`` is the
        # close timestamp consumed by the indicator registry.
        latest_aggregated_candle_timestamp=source_at,
        coverage_state=availability,
        registry_state="READY" if len(rows) >= required else "WARMUP_INCOMPLETE",
    )


class MarketIndicatorRegistryV2:
    """One versioned indicator entry point layered on the existing feature core."""

    version = INDICATOR_REGISTRY_VERSION

    def calculate(self, candles: list[dict[str, Any]], timeframe: str, as_of: int,
                  *, source_partial: bool = False) -> TimeframeMarketContextV2:
        rows = confirmed_candles_as_of(candles, timeframe, as_of)
        quality = _quality(rows, timeframe, as_of, partial=source_partial)
        if not rows:
            groups = {group: {name: _null() for name in names}
                      for group, names in INDICATOR_GROUP_KEYS.items()}
            return TimeframeMarketContextV2(None, False, groups["trend"], groups["momentum"],
                                             groups["volatility"], groups["structure"],
                                             groups["volume"], quality)
        features = build_features(rows, {"ma_periods": [20, 60, 200], "atr_period": 14,
                                         "bb_period": 20, "rsi_period": 14,
                                         "volume_period": 20})
        latest = features[-1]
        timestamp = int(rows[-1]["candle_close_ts"])
        stale, partial = quality.stale, quality.partial
        wrap = lambda value, warm=True, version=self.version: _value(
            value, timestamp, stale=stale, partial=partial,
            warmup=warm and value is not None, version=version)
        closes = [float(row["close"]) for row in rows]
        rsi_values = [item.get("rsi") for item in features]
        stoch = stoch_rsi_series(rsi_values)[-1]
        slope_bars = 4
        def slope(key: str) -> float | None:
            if len(features) <= slope_bars or latest.get(key) is None or features[-1-slope_bars].get(key) is None:
                return None
            previous = float(features[-1-slope_bars][key])
            return (float(latest[key]) / previous - 1) * 100 if previous else None
        def distance(key: str) -> float | None:
            value = latest.get(key)
            return (closes[-1] / float(value) - 1) * 100 if value else None
        ma_values = [latest.get("ema_20"), latest.get("sma_60"), latest.get("sma_200")]
        arrangement = None
        if all(value is not None for value in ma_values):
            ema20, ma60, ma200 = (float(value) for value in ma_values)
            arrangement = "EMA20_GT_MA60_GT_MA200" if ema20 > ma60 > ma200 else "EMA20_LT_MA60_LT_MA200" if ema20 < ma60 < ma200 else "MIXED"
        swing_high, swing_high_ts = _confirmed_swing(rows, "high")
        swing_low, swing_low_ts = _confirmed_swing(rows, "low")
        prior = rows[-21:-1]
        rolling_high = max((float(row["high"]) for row in prior), default=None)
        rolling_low = min((float(row["low"]) for row in prior), default=None)
        changes = [closes[pos] / closes[pos-1] - 1 for pos in range(max(1, len(closes)-14), len(closes)) if closes[pos-1]]
        momentum = (closes[-1] / closes[-15] - 1) * 100 if len(closes) >= 15 and closes[-15] else None
        persistence = (sum(change > 0 for change in changes) - sum(change < 0 for change in changes)) / len(changes) if len(changes) == 14 else None
        log_returns = [math.log(b/a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
        realized = statistics.pstdev(log_returns[-20:]) * math.sqrt(20) * 100 if len(log_returns) >= 20 else None
        widths = [float(item["bb_width"]) for item in features[-100:] if item.get("bb_width") is not None]
        width_rank = _percentile_rank(widths, float(latest["bb_width"])) if latest.get("bb_width") is not None else None
        row = rows[-1]
        high, low, open_value, close = (float(row[key]) for key in ("high", "low", "open", "close"))
        candle_range = high - low
        body = abs(close-open_value) / candle_range * 100 if candle_range > 0 else None
        upper_wick = (high-max(open_value, close)) / candle_range * 100 if candle_range > 0 else None
        lower_wick = (min(open_value, close)-low) / candle_range * 100 if candle_range > 0 else None
        trend = {
            "ema20": wrap(latest.get("ema_20")), "ma60": wrap(latest.get("sma_60")),
            "ma200": wrap(latest.get("sma_200")), "ema20_slope": wrap(slope("ema_20")),
            "ma60_slope": wrap(slope("sma_60")), "ma200_slope": wrap(slope("sma_200")),
            "close_distance_to_ema20": wrap(distance("ema_20")),
            "close_distance_to_ma60": wrap(distance("sma_60")),
            "close_distance_to_ma200": wrap(distance("sma_200")),
            "ma_arrangement": wrap(arrangement),
        }
        momentum_values = {
            "rsi14": wrap(latest.get("rsi")),
            "stoch_rsi": wrap(stoch["stoch_rsi"], version=STOCH_RSI_VERSION),
            "stoch_rsi_k": wrap(stoch["stoch_rsi_k"], version=STOCH_RSI_VERSION),
            "stoch_rsi_d": wrap(stoch["stoch_rsi_d"], version=STOCH_RSI_VERSION),
            "price_momentum": wrap(momentum), "momentum_persistence": wrap(persistence),
        }
        volatility = {
            "atr14": wrap(latest.get("atr")),
            "atr_percentage": wrap(float(latest["atr"])/close*100 if latest.get("atr") and close else None),
            "bollinger_upper": wrap(latest.get("bb_upper")), "bollinger_mid": wrap(latest.get("bb_mid")),
            "bollinger_lower": wrap(latest.get("bb_lower")),
            "bollinger_bandwidth": wrap(float(latest["bb_width"])*100 if latest.get("bb_width") is not None else None),
            "realized_volatility": wrap(realized),
            "compression_percentile": wrap(100-width_rank if width_rank is not None else None),
            "expansion_percentile": wrap(width_rank),
        }
        structure = {
            "recent_confirmed_swing_high": _value(swing_high, swing_high_ts, stale=stale, partial=partial,
                                                    warmup=swing_high is not None, version=SWING_VERSION),
            "recent_confirmed_swing_low": _value(swing_low, swing_low_ts, stale=stale, partial=partial,
                                                   warmup=swing_low is not None, version=SWING_VERSION),
            "rolling_high_distance": wrap((close/rolling_high-1)*100 if rolling_high else None),
            "rolling_low_distance": wrap((close/rolling_low-1)*100 if rolling_low else None),
        }
        baseline = sum(float(item["volume"]) for item in rows[-21:-1]) / 20 if len(rows) >= 21 else None
        volume = {
            "volume": wrap(float(row["volume"])), "volume_moving_average": wrap(baseline),
            "volume_ratio": wrap(float(row["volume"])/baseline if baseline else None),
            "candle_body_percentage": wrap(body), "upper_wick_percentage": wrap(upper_wick),
            "lower_wick_percentage": wrap(lower_wick),
        }
        return TimeframeMarketContextV2(timestamp, True, trend, momentum_values,
                                         volatility, structure, volume, quality)


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


class BoundedMarketDataReaderV2:
    """Read existing stores only; every query has instrument, time bounds and LIMIT."""

    def __init__(self, paper_db: Path | str, microstructure_db: Path | str | None = None) -> None:
        self.paper_db = Path(paper_db)
        self.microstructure_db = Path(microstructure_db) if microstructure_db else None

    @staticmethod
    def _readonly(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def candles(self, instrument: str, timeframe: str, as_of: int, limit: int) -> list[dict[str, Any]]:
        if not self.paper_db.exists() or timeframe == "1W":
            return []
        width = TIMEFRAME_SECONDS[timeframe]
        start = max(0, int(as_of) - width * (int(limit) + 2))
        candidates = (instrument, instrument.removesuffix("-SWAP"))
        rows: list[dict[str, Any]] = []
        with closing(self._readonly(self.paper_db)) as connection:
            has_history = _table_exists(connection, "historical_candles")
            has_market = _table_exists(connection, "market_candles")
            for candidate in candidates:
                merged: dict[int, dict[str, Any]] = {}
                if has_history:
                    selected = connection.execute(
                        """SELECT ts,open,high,low,close,volume,confirmed,source
                           FROM historical_candles
                           WHERE instrument=? AND timeframe=? AND ts>=? AND ts<=? AND confirmed=1
                           ORDER BY ts DESC LIMIT ?""",
                        (candidate, timeframe, start, as_of, limit)).fetchall()
                    merged.update({int(row["ts"]): {**dict(row), "_source_store": "historical_candles"}
                                   for row in selected})
                if has_market:
                    selected = connection.execute(
                        """SELECT ts,open,high,low,close,volume
                           FROM market_candles
                           WHERE instrument=? AND bar=? AND ts>=? AND ts<=?
                           ORDER BY ts DESC LIMIT ?""",
                        (candidate, timeframe, start, as_of, limit)).fetchall()
                    merged.update({int(row["ts"]): {**dict(row), "confirmed": True,
                                   "source": "persisted_confirmed_market_candles",
                                   "_source_store": "market_candles"} for row in selected})
                if merged:
                    rows = [merged[key] for key in sorted(merged)][-limit:]
                    break
        for row in rows:
            row["candle_close_ts"] = int(row["ts"]) + width
        return rows

    def flow(self, instrument: str, as_of: int, execution_timeframe: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {name: {} for name in ("cvd", "oi", "funding_settled", "funding_predicted", "basis")}
        if not self.microstructure_db or not self.microstructure_db.exists():
            return result
        canonical = instrument if instrument.endswith("-SWAP") else f"{instrument}-SWAP"
        resolution = execution_timeframe if execution_timeframe in {"15m", "1H", "4H"} else "15m"
        end_ms, start_ms = as_of * 1000, (as_of-FLOW_WINDOW_SECONDS) * 1000
        with closing(self._readonly(self.microstructure_db)) as connection:
            if _table_exists(connection, "cvd_aggregates"):
                rows = connection.execute(
                    """SELECT bucket_ms,delta,cumulative_anchored,observation_count,
                              first_source_ts_ms,last_source_ts_ms,gap_flag
                       FROM cvd_aggregates WHERE instrument=? AND resolution=?
                         AND bucket_ms>=? AND bucket_ms<=?
                       ORDER BY bucket_ms DESC LIMIT 8""",
                    (canonical, resolution, start_ms, end_ms)).fetchall()
                if rows:
                    ordered = list(reversed(rows)); first, last = ordered[0], ordered[-1]
                    result["cvd"] = {"current": float(last["cumulative_anchored"]),
                                     "change": sum(float(row["delta"]) for row in ordered),
                                     "slope": (float(last["cumulative_anchored"])-float(first["cumulative_anchored"])) / max(1, (int(last["last_source_ts_ms"])-int(first["first_source_ts_ms"]))/1000),
                                     "timestamp": int(last["last_source_ts_ms"])//1000,
                                     "partial": any(int(row["gap_flag"]) for row in ordered),
                                     "start_timestamp": int(first["first_source_ts_ms"])//1000}
            if _table_exists(connection, "oi_aggregates"):
                rows = connection.execute(
                    """SELECT bucket_ms,first_value,last_value,absolute_change,percentage_change,
                              first_source_ts_ms,last_source_ts_ms,gap_flag
                       FROM oi_aggregates WHERE instrument=? AND resolution=?
                         AND bucket_ms>=? AND bucket_ms<=?
                       ORDER BY bucket_ms DESC LIMIT 8""",
                    (canonical, resolution, start_ms, end_ms)).fetchall()
                if rows:
                    ordered = list(reversed(rows)); first, last = ordered[0], ordered[-1]
                    initial, current = float(first["first_value"]), float(last["last_value"])
                    result["oi"] = {"current": current, "absolute_change": current-initial,
                                    "percentage_change": (current/initial-1)*100 if initial else None,
                                    "timestamp": int(last["last_source_ts_ms"])//1000,
                                    "partial": any(int(row["gap_flag"]) for row in ordered),
                                    "start_timestamp": int(first["first_source_ts_ms"])//1000}
            for key, table in (("funding_settled", "funding_settled"), ("funding_predicted", "funding_predicted")):
                if _table_exists(connection, table):
                    row = connection.execute(
                        f"""SELECT source_ts_ms,funding_rate,state FROM {table}
                            WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<=?
                            ORDER BY source_ts_ms DESC LIMIT 1""",
                        (canonical, end_ms-7*86_400_000, end_ms)).fetchone()
                    if row:
                        result[key] = {"current": float(row["funding_rate"]),
                                       "timestamp": int(row["source_ts_ms"])//1000,
                                       "partial": str(row["state"]) != "confirmed"}
            if _table_exists(connection, "basis_aggregates"):
                row = connection.execute(
                    """SELECT last_basis,last_basis_pct,last_source_ts_ms,gap_flag
                       FROM basis_aggregates WHERE instrument=? AND resolution=?
                         AND bucket_ms>=? AND bucket_ms<=?
                       ORDER BY bucket_ms DESC LIMIT 1""",
                    (canonical, resolution, start_ms, end_ms)).fetchone()
                if row:
                    result["basis"] = {"current": float(row["last_basis"]),
                                       "percentage": float(row["last_basis_pct"])*100,
                                       "timestamp": int(row["last_source_ts_ms"])//1000,
                                       "partial": bool(row["gap_flag"])}
        return result

    def explain_plans(self, instrument: str, as_of: int) -> list[str]:
        """Return representative query plans used by the endpoint."""
        plans: list[str] = []
        if self.paper_db.exists():
            with closing(self._readonly(self.paper_db)) as connection:
                if _table_exists(connection, "historical_candles"):
                    rows = connection.execute(
                        "EXPLAIN QUERY PLAN SELECT ts FROM historical_candles WHERE instrument=? AND timeframe=? AND ts>=? AND ts<=? AND confirmed=1 ORDER BY ts DESC LIMIT ?",
                        (instrument.removesuffix("-SWAP"), "15m", as_of-500*900, as_of, 500)).fetchall()
                    plans.extend(str(row[3]) for row in rows)
                if _table_exists(connection, "market_candles"):
                    rows = connection.execute(
                        "EXPLAIN QUERY PLAN SELECT ts FROM market_candles WHERE instrument=? AND bar=? AND ts>=? AND ts<=? ORDER BY ts DESC LIMIT ?",
                        (instrument.removesuffix("-SWAP"), "15m", as_of-500*900, as_of, 500)).fetchall()
                    plans.extend(str(row[3]) for row in rows)
        if self.microstructure_db and self.microstructure_db.exists():
            canonical = instrument if instrument.endswith("-SWAP") else f"{instrument}-SWAP"
            end_ms, start_ms = as_of*1000, (as_of-FLOW_WINDOW_SECONDS)*1000
            with closing(self._readonly(self.microstructure_db)) as connection:
                statements = (
                    ("cvd_aggregates", "SELECT bucket_ms FROM cvd_aggregates WHERE instrument=? AND resolution=? AND bucket_ms>=? AND bucket_ms<=? ORDER BY bucket_ms DESC LIMIT 8", (canonical, "15m", start_ms, end_ms)),
                    ("oi_aggregates", "SELECT bucket_ms FROM oi_aggregates WHERE instrument=? AND resolution=? AND bucket_ms>=? AND bucket_ms<=? ORDER BY bucket_ms DESC LIMIT 8", (canonical, "15m", start_ms, end_ms)),
                    ("funding_settled", "SELECT source_ts_ms FROM funding_settled WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<=? ORDER BY source_ts_ms DESC LIMIT 1", (canonical, end_ms-7*86_400_000, end_ms)),
                    ("funding_predicted", "SELECT source_ts_ms FROM funding_predicted WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<=? ORDER BY source_ts_ms DESC LIMIT 1", (canonical, end_ms-7*86_400_000, end_ms)),
                    ("basis_aggregates", "SELECT bucket_ms FROM basis_aggregates WHERE instrument=? AND resolution=? AND bucket_ms>=? AND bucket_ms<=? ORDER BY bucket_ms DESC LIMIT 1", (canonical, "15m", start_ms, end_ms)),
                )
                for table, sql, parameters in statements:
                    if _table_exists(connection, table):
                        rows = connection.execute(f"EXPLAIN QUERY PLAN {sql}", parameters).fetchall()
                        plans.extend(str(row[3]) for row in rows)
        return plans


def _flow_indicator(raw: dict[str, Any], key: str, as_of: int) -> IndicatorValueV2:
    timestamp = raw.get("timestamp")
    stale = timestamp is not None and as_of-int(timestamp) > FLOW_STALE_SECONDS
    return _value(raw.get(key), int(timestamp) if timestamp is not None else None,
                  stale=stale, partial=bool(raw.get("partial")),
                  warmup=raw.get(key) is not None)


def _combination(price_change: float | None, other_change: float | None,
                 other_name: str, start_ts: int | None, end_ts: int | None,
                 quality: str) -> dict[str, Any]:
    state = "INSUFFICIENT_DATA"
    if price_change is not None and other_change is not None:
        price = "PRICE_UP" if price_change >= 0 else "PRICE_DOWN"
        other = f"{other_name}_UP" if other_change >= 0 else f"{other_name}_DOWN"
        state = f"{price}_{other}"
    else:
        quality = "MISSING"
    return {"state": state, "observation_window_seconds": FLOW_WINDOW_SECONDS,
            "start_timestamp": start_ts, "end_timestamp": end_ts,
            "price_change_pct": price_change,
            f"{other_name.lower()}_change": other_change, "data_quality": quality,
            "calculation_version": "price-flow-combination-facts-v2"}


def _candidate_levels(timeframes: dict[str, TimeframeMarketContextV2],
                      datasets: dict[str, list[dict[str, Any]]], price: float,
                      vpvr: dict[str, IndicatorValueV2]) -> list[MarketLevelV2]:
    candidates: list[MarketLevelV2] = []
    mappings = {
        "recent_confirmed_swing_high": "SWING_HIGH", "recent_confirmed_swing_low": "SWING_LOW",
        "ema20": "EMA20", "ma60": "MA60", "ma200": "MA200",
        "bollinger_upper": "BOLLINGER_UPPER", "bollinger_lower": "BOLLINGER_LOWER",
    }
    def touches(timeframe: str, level: float) -> int:
        tolerance = level * LEVEL_TOUCH_THRESHOLD_PCT / 100
        return sum(float(row["low"])-tolerance <= level <= float(row["high"])+tolerance
                   for row in datasets.get(timeframe, [])[-100:])

    for timeframe, context in timeframes.items():
        collections = (context.trend, context.structure, context.volatility)
        for source, level_type in mappings.items():
            indicator = next((group[source] for group in collections if source in group), None)
            if indicator and indicator.available and isinstance(indicator.value, (int, float)) and indicator.source_timestamp:
                candidates.append(MarketLevelV2(level_type, timeframe, float(indicator.value),
                                                 indicator.source_timestamp,
                                                 (float(indicator.value)/price-1)*100,
                                                 touches(timeframe, float(indicator.value)), True,
                                                 (f"{timeframe}:{level_type}",)))
    for source, level_type in (("poc", "VPVR_POC"), ("vah", "VPVR_VAH"), ("val", "VPVR_VAL")):
        indicator = vpvr.get(source)
        if indicator and indicator.available and isinstance(indicator.value, (int, float)) and indicator.source_timestamp:
            candidates.append(MarketLevelV2(level_type, "15m", float(indicator.value),
                                             indicator.source_timestamp,
                                             (float(indicator.value)/price-1)*100,
                                             touches("15m", float(indicator.value)), True,
                                             (f"15m:{level_type}",)))
    if price > 0:
        step = 10 ** math.floor(math.log10(price)) / 10
        anchor = math.floor(price/step)*step
        for value in sorted({anchor-step, anchor, anchor+step, anchor+2*step}):
            if value > 0:
                candidates.append(MarketLevelV2("PSYCHOLOGICAL_ROUND", "GLOBAL", value,
                                                 max(context.candle_close_ts or 0 for context in timeframes.values()),
                                                 (value/price-1)*100, 0, True,
                                                 ("GLOBAL:PSYCHOLOGICAL_ROUND",)))
    return sorted(candidates, key=lambda item: (item.value, item.type, item.timeframe))


def merge_confluence_levels(candidates: list[MarketLevelV2], price: float,
                            threshold_pct: float = CONFLUENCE_THRESHOLD_PCT) -> tuple[MarketLevelV2, ...]:
    """Deterministically merge adjacent candidates within the versioned threshold."""
    zones: list[list[MarketLevelV2]] = []
    for item in sorted(candidates, key=lambda value: (value.value, value.type, value.timeframe)):
        if not zones:
            zones.append([item]); continue
        center = sum(value.value for value in zones[-1]) / len(zones[-1])
        if center and abs(item.value-center)/center*100 <= threshold_pct:
            zones[-1].append(item)
        else:
            zones.append([item])
    output: list[MarketLevelV2] = []
    for zone in zones:
        center = sum(item.value for item in zone)/len(zone)
        sources = tuple(sorted({source for item in zone for source in item.confluence_sources}))
        output.append(MarketLevelV2("CONFLUENCE_ZONE" if len(sources)>1 else zone[0].type,
                                   "MULTI" if len({item.timeframe for item in zone})>1 else zone[0].timeframe,
                                   center, max(item.source_timestamp for item in zone),
                                   (center/price-1)*100, sum(item.touches for item in zone),
                                   all(item.confirmed for item in zone), sources))
    return tuple(output)


class MarketContextServiceV2:
    def __init__(self, reader: BoundedMarketDataReaderV2,
                 registry: MarketIndicatorRegistryV2 | None = None) -> None:
        self.reader = reader
        self.registry = registry or MarketIndicatorRegistryV2()
        self._cache: dict[tuple[str, int, str, str], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def context(self, instrument: str, *, as_of: int | None = None,
                execution_timeframe: str = "15m") -> dict[str, Any]:
        if execution_timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"execution_timeframe must be one of {', '.join(SUPPORTED_TIMEFRAMES)}")
        normalized = instrument.strip().upper()
        if not normalized or len(normalized) > 48 or not all(char.isalnum() or char == "-" for char in normalized):
            raise ValueError("instrument must be a bounded OKX-style identifier")
        resolved = int(time.time()) if as_of is None else int(as_of)
        if resolved < 946_684_800 or resolved > int(time.time()) + 60:
            raise ValueError("as_of must be a Unix-seconds timestamp no later than the present")
        key = (normalized, resolved, execution_timeframe, CONTEXT_VERSION)
        with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic()-cached[0] <= CACHE_TTL_SECONDS:
                return cached[1]
        datasets = {frame: self.reader.candles(normalized, frame, resolved, DEFAULT_LOOKBACK[frame])
                    for frame in ("15m", "1H", "4H", "1D")}
        datasets["1W"] = aggregate_confirmed_daily_to_weekly(datasets["1D"], resolved)
        contexts: dict[str, TimeframeMarketContextV2] = {}
        current_week_start = _week_start(resolved)
        weekly_partial = current_week_start + TIMEFRAME_SECONDS["1W"] > resolved
        for frame in SUPPORTED_TIMEFRAMES:
            contexts[frame] = self.registry.calculate(datasets[frame], frame, resolved,
                                                       source_partial=weekly_partial if frame == "1W" else False)
        execution = contexts[execution_timeframe]
        execution_rows = confirmed_candles_as_of(datasets[execution_timeframe], execution_timeframe, resolved)
        price_value = float(execution_rows[-1]["close"]) if execution_rows else None
        price = _value(price_value, execution.candle_close_ts, stale=execution.quality.stale,
                       partial=execution.quality.partial, warmup=price_value is not None,
                       version="confirmed-close-price-v2")
        raw_flow = self.reader.flow(normalized, resolved, execution_timeframe)
        cvd = {name: _flow_indicator(raw_flow["cvd"], source, resolved)
               for name, source in (("current", "current"), ("change", "change"), ("slope", "slope"))}
        oi = {name: _flow_indicator(raw_flow["oi"], source, resolved)
              for name, source in (("current", "current"), ("absolute_change", "absolute_change"),
                                   ("percentage_change", "percentage_change"))}
        funding = {"settled": _flow_indicator(raw_flow["funding_settled"], "current", resolved),
                   "predicted": _flow_indicator(raw_flow["funding_predicted"], "current", resolved)}
        basis = {"value": _flow_indicator(raw_flow["basis"], "current", resolved),
                 "percentage": _flow_indicator(raw_flow["basis"], "percentage", resolved)}
        profile = calculate_volume_profile(execution_rows[-96:]) if execution_rows else {"available": False}
        profile_timestamp = int(profile.get("end_ts") or execution.candle_close_ts or 0) or None
        vpvr = {name: _value(profile.get(name) if profile.get("available") else None,
                             profile_timestamp, stale=execution.quality.stale,
                             partial=execution.quality.partial,
                             warmup=bool(profile.get("available")), version=str(profile.get("method") or "ohlcv-unavailable-v2"))
                for name in ("poc", "vah", "val")}
        price_change = None
        price_start_ts = None
        if len(execution_rows) >= 5 and float(execution_rows[-5]["close"]):
            price_change = (float(execution_rows[-1]["close"])/float(execution_rows[-5]["close"])-1)*100
            price_start_ts = int(execution_rows[-5]["candle_close_ts"])
        oi_change = raw_flow["oi"].get("percentage_change")
        cvd_change = raw_flow["cvd"].get("change")
        oi_quality = "MISSING" if oi_change is None else "PARTIAL" if raw_flow["oi"].get("partial") else "STALE" if oi["current"].stale else "AVAILABLE"
        cvd_quality = "MISSING" if cvd_change is None else "PARTIAL" if raw_flow["cvd"].get("partial") else "STALE" if cvd["current"].stale else "AVAILABLE"
        flow = FlowContextV2(cvd, oi, funding, basis, vpvr,
                             _combination(price_change, oi_change, "OI", price_start_ts,
                                          execution.candle_close_ts, oi_quality),
                             _combination(price_change, cvd_change, "CVD", price_start_ts,
                                          execution.candle_close_ts, cvd_quality))
        levels = merge_confluence_levels(
            _candidate_levels(contexts, datasets, price_value, vpvr), price_value
        ) if price_value else ()
        stale_sources = [frame for frame, item in contexts.items() if item.quality.stale]
        partial_sources = [frame for frame, item in contexts.items() if item.quality.partial]
        missing_sources = [frame for frame, item in contexts.items() if item.quality.missing]
        for name, group in (("cvd", cvd), ("oi", oi), ("funding", funding), ("basis", basis), ("vpvr", vpvr)):
            if not any(item.available for item in group.values()): missing_sources.append(name)
            elif any(item.partial for item in group.values()): partial_sources.append(name)
            elif any(item.stale for item in group.values()): stale_sources.append(name)
        gaps = [{"source": frame, **gap} for frame, item in contexts.items() for gap in item.quality.gaps]
        overall = "MISSING" if execution.quality.missing else "STALE" if stale_sources else "PARTIAL" if partial_sources or gaps or missing_sources else "AVAILABLE"
        context = MarketAnalysisContextV2(CONTEXT_VERSION, normalized, resolved,
                                          execution_timeframe, price, contexts, flow, levels,
                                          {"overall_status": overall,
                                           "stale_sources": sorted(set(stale_sources)),
                                           "partial_sources": sorted(set(partial_sources)),
                                           "missing_sources": sorted(set(missing_sources)),
                                           "gaps": gaps}).to_dict()
        for frame in SUPPORTED_TIMEFRAMES:
            source_rows = datasets["1D"] if frame == "1W" else None
            context["timeframes"][frame]["observation"] = asdict(timeframe_observation(
                normalized, frame, datasets[frame], resolved, contexts[frame].quality,
                source_rows=source_rows,
            ))
        context["context_identity"] = hashlib.sha256(json.dumps(
            {key: value for key, value in context.items() if key != "context_identity"},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False).encode("utf-8")).hexdigest()
        with self._lock:
            self._cache[key] = (time.monotonic(), context)
            if len(self._cache) > 64:
                oldest = min(self._cache, key=lambda item: self._cache[item][0])
                self._cache.pop(oldest, None)
        return context
