"""Versioned, read-only evidence contracts for microstructure consumers.

This module is intentionally an adapter, not a replacement collector.  It
reads the canonical history database when supplied and the existing live
microstructure store otherwise.  A missing observation is represented by
``None`` and a reason; it is never coerced to zero or substituted from the
other product type.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .volume_profile import calculate_trade_volume_profile, calculate_volume_profile


MICROSTRUCTURE_EVIDENCE_VERSION = "microstructure-evidence-v1"
ProductType = Literal["SPOT", "SWAP"]
SeriesName = Literal["cvd", "oi", "funding", "basis", "vpvr"]
FRESHNESS_LIMIT_MS = {
    "cvd": 180_000,
    "oi": 180_000,
    "basis": 180_000,
    # Settled funding normally changes every eight hours.  Twelve hours keeps
    # the observation visible while making a missed settlement unmistakable.
    "funding": 12 * 60 * 60 * 1000,
    "vpvr": 180_000,
}


def _resolution_ms(value: str) -> int:
    normalized = value.strip()
    units = {"m": 60_000, "H": 3_600_000, "D": 86_400_000}
    try:
        return max(1, int(normalized[:-1])) * units[normalized[-1]]
    except (KeyError, TypeError, ValueError, IndexError):
        return 60_000


def _timestamp_ms(value: Any) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    return parsed * 1000 if 0 < parsed < 1_000_000_000_000 else parsed


@dataclass(frozen=True)
class MarketIdentity:
    venue: str
    product_type: ProductType
    instrument: str

    def __post_init__(self) -> None:
        if self.product_type not in {"SPOT", "SWAP"}:
            raise ValueError("product_type must be SPOT or SWAP")
        if not self.venue or not self.instrument:
            raise ValueError("venue and instrument are required")


@dataclass(frozen=True)
class EvidenceWindow:
    start_ms: int
    end_ms: int
    resolution: str = "1m"
    anchor: str = "WINDOW_START"
    reset: str = "NONE"

    def __post_init__(self) -> None:
        if self.start_ms > self.end_ms:
            raise ValueError("window start must not exceed end")


def _missing(
    series: SeriesName, identity: MarketIdentity, window: EvidenceWindow,
    reason: str, *, provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": MICROSTRUCTURE_EVIDENCE_VERSION,
        "series": series, "identity": asdict(identity), "window": asdict(window),
        "value": None, "observations": [], "source_start_ms": None,
        "source_end_ms": None, "freshness_ms": None,
        "coverage": {"expected_buckets": max(
            1, (window.end_ms - window.start_ms) // _resolution_ms(window.resolution) + 1
        ), "observed_buckets": 0,
                     "ratio": 0.0, "has_gaps": True},
        "quality": "MISSING", "missing_reason": reason,
        "provenance": provenance or {"adapter": MICROSTRUCTURE_EVIDENCE_VERSION},
    }


class CanonicalMicrostructureEvidenceAdapter:
    """Read-only compatibility query facade over live and history stores."""

    def __init__(self, live_store_path: Path | str, history_store_path: Path | str | None = None) -> None:
        self.live_store_path = Path(live_store_path)
        self.history_store_path = Path(history_store_path) if history_store_path else None

    @staticmethod
    def _read(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _table(connection: sqlite3.Connection, name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    @staticmethod
    def _swap_instrument(identity: MarketIdentity) -> str | None:
        if identity.product_type != "SWAP":
            return None
        return identity.instrument.upper() if identity.instrument.upper().endswith("-SWAP") else f"{identity.instrument.upper()}-SWAP"

    @staticmethod
    def _result(
        series: SeriesName, identity: MarketIdentity, window: EvidenceWindow,
        rows: list[sqlite3.Row], *, value_column: str, source: str,
        now: int, values: dict[str, Any] | None = None, gap_column: str | None = None,
    ) -> dict[str, Any]:
        observed = [row for row in rows if row[value_column] is not None]
        if not observed:
            return _missing(series, identity, window, "NO_CONFIRMED_OBSERVATION",
                            provenance={"adapter": MICROSTRUCTURE_EVIDENCE_VERSION, "source": source})
        starts = [int(row["source_start_ms"]) for row in observed if row["source_start_ms"] is not None]
        ends = [int(row["source_end_ms"]) for row in observed if row["source_end_ms"] is not None]
        if not starts or not ends:
            return _missing(series, identity, window, "SOURCE_TIMESTAMP_UNAVAILABLE",
                            provenance={"adapter": MICROSTRUCTURE_EVIDENCE_VERSION, "source": source})
        source_start, source_end = min(starts), max(ends)
        expected = max(1, (window.end_ms - window.start_ms) // _resolution_ms(window.resolution) + 1)
        explicit_gaps = any(bool(row[gap_column]) for row in observed) if gap_column else False
        if observed and "status" in observed[0].keys():
            explicit_gaps = explicit_gaps or any(
                str(row["status"] or "").upper() not in {"VALID", "COMPLETE"}
                or ("gap_reason" in row.keys() and bool(row["gap_reason"]))
                for row in observed
            )
        has_gaps = explicit_gaps or len(observed) < expected
        freshness = max(0, now - source_end)
        quality = ("STALE" if freshness > FRESHNESS_LIMIT_MS[series]
                   else "PARTIAL" if has_gaps else "VALID")
        payload = {
            "contract_version": MICROSTRUCTURE_EVIDENCE_VERSION,
            "series": series, "identity": asdict(identity), "window": asdict(window),
            "value": float(observed[-1][value_column]),
            "observations": [dict(row) for row in rows],
            "source_start_ms": source_start, "source_end_ms": source_end,
            "freshness_ms": freshness,
            "freshness_limit_ms": FRESHNESS_LIMIT_MS[series],
            "coverage": {"expected_buckets": expected, "observed_buckets": len(observed),
                         "ratio": round(min(1.0, len(observed) / expected), 6), "has_gaps": has_gaps},
            "quality": quality, "missing_reason": None,
            "provenance": {"adapter": MICROSTRUCTURE_EVIDENCE_VERSION, "source": source},
        }
        if values:
            payload.update(values)
        return payload

    def query(self, series: SeriesName, identity: MarketIdentity, window: EvidenceWindow, *, now_ms: int | None = None) -> dict[str, Any]:
        """Return one explicit product contract. CVD/OI/funding are SWAP-only."""
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        if series == "vpvr":
            return self.query_trade_vpvr(identity, window, now_ms=now)
        swap = self._swap_instrument(identity)
        if series in {"cvd", "oi", "funding"} and swap is None:
            return _missing(series, identity, window, "SOURCE_PRODUCT_UNAVAILABLE")
        if series == "basis":
            return self._query_basis(identity, window, now)
        if series in {"cvd", "oi"} and self.history_store_path and self.history_store_path.exists():
            history = self._query_history(series, identity, window, now)
            if history is not None:
                return history
        return self._query_live(series, identity, window, now)

    def _query_history(self, series: SeriesName, identity: MarketIdentity, window: EvidenceWindow, now: int) -> dict[str, Any] | None:
        assert self.history_store_path is not None
        try:
            with self._read(self.history_store_path) as c:
                table = "cvd_1m" if series == "cvd" else "oi_1m"
                if not self._table(c, table):
                    return None
                value = "daily_cumulative" if series == "cvd" else "confirmed_oi"
                rows = c.execute(
                    f"SELECT bucket_ms AS timestamp_ms,{value} AS value,source_min_ts_ms AS source_start_ms,source_max_ts_ms AS source_end_ms,status,gap_reason FROM {table} WHERE instrument=? AND bucket_ms>=? AND bucket_ms<=? ORDER BY bucket_ms",
                    (self._swap_instrument(identity), window.start_ms, window.end_ms),
                ).fetchall() if series == "cvd" else c.execute(
                    f"SELECT bucket_ms AS timestamp_ms,{value} AS value,observation_ts_ms AS source_start_ms,observation_ts_ms AS source_end_ms,status,gap_reason FROM {table} WHERE instrument=? AND bucket_ms>=? AND bucket_ms<=? ORDER BY bucket_ms",
                    (self._swap_instrument(identity), window.start_ms, window.end_ms),
                ).fetchall()
                payload = self._result(series, identity, window, rows, value_column="value", source="canonical_history", now=now)
                if series == "cvd":
                    payload["window"] = {**payload["window"], "anchor": "UTC_DAY_START",
                                         "reset": "UTC_DAILY_RESET"}
                    payload["anchor"] = "UTC_DAY_START"
                    payload["reset"] = "UTC_DAILY_RESET"
                return payload
        except sqlite3.Error:
            return None

    def _query_live(self, series: SeriesName, identity: MarketIdentity, window: EvidenceWindow, now: int) -> dict[str, Any]:
        if not self.live_store_path.exists():
            return _missing(series, identity, window, "LIVE_STORE_UNAVAILABLE")
        swap = self._swap_instrument(identity)
        try:
            with self._read(self.live_store_path) as c:
                if series == "cvd":
                    table, column = "cvd_aggregates", "cumulative_anchored"
                    sql = "SELECT bucket_ms AS timestamp_ms,cumulative_anchored AS value,first_source_ts_ms AS source_start_ms,last_source_ts_ms AS source_end_ms,gap_flag FROM cvd_aggregates WHERE instrument=? AND resolution='1m' AND bucket_ms>=? AND bucket_ms<=? ORDER BY bucket_ms"
                elif series == "oi":
                    table, column = "oi_aggregates", "last_value"
                    sql = "SELECT bucket_ms AS timestamp_ms,last_value AS value,first_source_ts_ms AS source_start_ms,last_source_ts_ms AS source_end_ms,gap_flag FROM oi_aggregates WHERE instrument=? AND resolution='1m' AND bucket_ms>=? AND bucket_ms<=? ORDER BY bucket_ms"
                elif series == "funding":
                    table, column = "funding_settled", "funding_rate"
                    sql = "SELECT funding_time_ms AS timestamp_ms,funding_rate AS value,source_ts_ms AS source_start_ms,source_ts_ms AS source_end_ms,NULL AS gap_flag FROM funding_settled WHERE instrument=? AND funding_time_ms>=? AND funding_time_ms<=? ORDER BY funding_time_ms"
                else:
                    return _missing(series, identity, window, "UNSUPPORTED_SERIES")
                if not self._table(c, table):
                    return _missing(series, identity, window, "SOURCE_TABLE_UNAVAILABLE")
                rows = c.execute(sql, (swap, window.start_ms, window.end_ms)).fetchall()
                payload = self._result(series, identity, window, rows, value_column="value", source="live_microstructure", now=now, gap_column="gap_flag")
                if series == "cvd":
                    payload["window"] = {**payload["window"], "anchor": "UTC_DAY_START",
                                         "reset": "UTC_DAILY_RESET"}
                    payload["anchor"] = "UTC_DAY_START"
                    payload["reset"] = "UTC_DAILY_RESET"
                elif series == "funding":
                    # Predicted funding is intentionally excluded: a forecast
                    # cannot be silently substituted for a settled fact.
                    payload["funding_kind"] = "SETTLED"
                return payload
        except sqlite3.Error:
            return _missing(series, identity, window, "LIVE_QUERY_FAILED")

    def _query_basis(self, identity: MarketIdentity, window: EvidenceWindow, now: int) -> dict[str, Any]:
        swap = self._swap_instrument(identity)
        if swap is None:
            return _missing("basis", identity, window, "BASIS_REQUIRES_SWAP_IDENTITY")
        if not self.live_store_path.exists():
            return _missing("basis", identity, window, "LIVE_STORE_UNAVAILABLE")
        try:
            with self._read(self.live_store_path) as c:
                if not self._table(c, "basis_aggregates"):
                    return _missing("basis", identity, window, "SOURCE_TABLE_UNAVAILABLE")
                rows = c.execute(
                    "SELECT bucket_ms AS timestamp_ms,last_basis AS value,first_source_ts_ms AS source_start_ms,last_source_ts_ms AS source_end_ms,gap_flag,last_basis_pct FROM basis_aggregates WHERE instrument=? AND resolution='1m' AND bucket_ms>=? AND bucket_ms<=? ORDER BY bucket_ms",
                    (swap, window.start_ms, window.end_ms),
                ).fetchall()
                return self._result("basis", identity, window, rows, value_column="value", source="live_microstructure_causal_mark_index", now=now, gap_column="gap_flag", values={"basis_pct": float(rows[-1]["last_basis_pct"]) if rows else None, "spot_identity": asdict(MarketIdentity(identity.venue, "SPOT", swap.removesuffix("-SWAP"))), "basis_method": "CAUSAL_MARK_TO_SPOT_INDEX_ASOF"})
        except sqlite3.Error:
            return _missing("basis", identity, window, "LIVE_QUERY_FAILED")

    def query_trade_vpvr(self, identity: MarketIdentity, window: EvidenceWindow, *, bins: int = 80, now_ms: int | None = None) -> dict[str, Any]:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        swap = self._swap_instrument(identity)
        if swap is None:
            return _missing("vpvr", identity, window, "SOURCE_PRODUCT_UNAVAILABLE")
        if not self.live_store_path.exists():
            return _missing("vpvr", identity, window, "LIVE_STORE_UNAVAILABLE")
        try:
            with self._read(self.live_store_path) as c:
                if not self._table(c, "trade_flow_observations"):
                    return _missing("vpvr", identity, window, "SOURCE_TABLE_UNAVAILABLE")
                rows = c.execute("SELECT price,CASE WHEN side='buy' THEN notional ELSE 0 END buy_notional,CASE WHEN side='sell' THEN notional ELSE 0 END sell_notional,1 trade_count,source_ts_ms FROM trade_flow_observations WHERE instrument=? AND state='confirmed' AND source_ts_ms>=? AND source_ts_ms<=? ORDER BY source_ts_ms", (swap, window.start_ms, window.end_ms)).fetchall()
                profile = calculate_trade_volume_profile([dict(row) for row in rows], bins=bins)
                if not profile.get("available"):
                    return _missing("vpvr", identity, window, str(profile.get("reason") or "INSUFFICIENT_TRADE_COVERAGE"), provenance={"adapter": MICROSTRUCTURE_EVIDENCE_VERSION, "source": "live_trade_observations", "method": "TRADE_PRICE_EXACT"})
                expected = max(1, (window.end_ms-window.start_ms)//_resolution_ms(window.resolution)+1)
                observed = len({int(r["source_ts_ms"])//_resolution_ms(window.resolution) for r in rows})
                freshness = max(0, now-int(rows[-1]["source_ts_ms"]))
                has_gaps = observed < expected
                profile.update({"contract_version": MICROSTRUCTURE_EVIDENCE_VERSION, "series": "vpvr", "identity": asdict(identity), "window": asdict(window), "source_start_ms": int(rows[0]["source_ts_ms"]), "source_end_ms": int(rows[-1]["source_ts_ms"]), "freshness_ms": freshness, "freshness_limit_ms": FRESHNESS_LIMIT_MS["vpvr"], "coverage": {"expected_buckets": expected, "observed_buckets": observed, "ratio": round(min(1.0, observed/expected), 6), "has_gaps": has_gaps}, "quality": "STALE" if freshness > FRESHNESS_LIMIT_MS["vpvr"] else "PARTIAL" if has_gaps else "VALID", "missing_reason": None, "provenance": {"adapter": MICROSTRUCTURE_EVIDENCE_VERSION, "source": "live_trade_observations", "method": "TRADE_PRICE_EXACT"}})
                return profile
        except sqlite3.Error:
            return _missing("vpvr", identity, window, "LIVE_QUERY_FAILED")

    def query_ohlcv_vpvr(self, identity: MarketIdentity, window: EvidenceWindow, candles: list[dict[str, Any]], *, bins: int = 48, now_ms: int | None = None) -> dict[str, Any]:
        """Explicit approximate VPVR hook for callers that only have OHLCV."""
        profile = calculate_volume_profile(candles, bins=bins)
        if not profile.get("available"):
            return _missing("vpvr", identity, window, str(profile.get("reason") or "INSUFFICIENT_CONFIRMED_CANDLES"), provenance={"adapter": MICROSTRUCTURE_EVIDENCE_VERSION, "method": "OHLCV_APPROXIMATE"})
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        source_start = _timestamp_ms(profile.get("start_ts"))
        source_end = _timestamp_ms(profile.get("end_ts"))
        freshness = max(0, now - source_end) if source_end is not None else None
        profile.update({"contract_version": MICROSTRUCTURE_EVIDENCE_VERSION, "series": "vpvr", "identity": asdict(identity), "window": asdict(window), "source_start_ms": source_start, "source_end_ms": source_end, "freshness_ms": freshness, "freshness_limit_ms": FRESHNESS_LIMIT_MS["vpvr"], "coverage": {"expected_buckets": len(candles), "observed_buckets": int(profile.get("lookback_bars") or 0), "ratio": round(int(profile.get("lookback_bars") or 0)/len(candles), 6) if candles else 0.0, "has_gaps": False}, "quality": "STALE" if freshness is not None and freshness > FRESHNESS_LIMIT_MS["vpvr"] else "APPROXIMATE", "missing_reason": None, "provenance": {"adapter": MICROSTRUCTURE_EVIDENCE_VERSION, "source": "caller_ohlcv", "method": "OHLCV_APPROXIMATE"}})
        return profile


def canonical_market_evidence_set(
    adapter: CanonicalMicrostructureEvidenceAdapter, instrument: str, as_of: int,
) -> list[dict[str, Any]]:
    """Build the bounded, product-qualified evidence lanes for one snapshot.

    ``as_of`` is Unix seconds.  The final one-minute bucket ends strictly at
    or before that cutoff, so a just-opened minute cannot appear confirmed.
    The SPOT lane is queried explicitly and remains a typed missing value while
    the dedicated collector only owns SWAP observations.
    """
    normalized = instrument.upper()
    swap_instrument = normalized if normalized.endswith("-SWAP") else f"{normalized}-SWAP"
    spot_instrument = swap_instrument.removesuffix("-SWAP")
    swap = MarketIdentity("OKX", "SWAP", swap_instrument)
    spot = MarketIdentity("OKX", "SPOT", spot_instrument)
    cutoff_ms = int(as_of) * 1000
    end_ms = max(0, ((cutoff_ms - 1) // 60_000) * 60_000)
    live = EvidenceWindow(
        max(0, end_ms - 239 * 60_000), end_ms, "1m",
        "EXPLICIT_4H_WINDOW", "SERIES_CONTRACT",
    )
    funding = EvidenceWindow(
        max(0, end_ms - 7 * 86_400_000), end_ms, "8H",
        "SETTLEMENT_WINDOW", "NONE",
    )
    evidence = [
        adapter.query("cvd", swap, live, now_ms=cutoff_ms),
        adapter.query("oi", swap, live, now_ms=cutoff_ms),
        adapter.query("basis", swap, live, now_ms=cutoff_ms),
        adapter.query("funding", swap, funding, now_ms=cutoff_ms),
        adapter.query("vpvr", swap, live, now_ms=cutoff_ms),
        adapter.query("cvd", spot, live, now_ms=cutoff_ms),
        adapter.query("oi", spot, live, now_ms=cutoff_ms),
    ]
    return evidence
