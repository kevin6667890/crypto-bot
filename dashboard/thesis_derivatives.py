"""Point-in-time derivative facts and immutable composite dataset identities.

This module intentionally contains no thesis/parser policy.  It is the narrow
data boundary used by the thesis engine: an observation is visible only after
its source timestamp and only while it satisfies the caller's freshness bound.
"""
from __future__ import annotations

from dataclasses import dataclass
import bisect
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable, Mapping, Sequence


DERIVATIVE_ALIGNMENT_VERSION = "thesis-derivative-asof-v1"
CAUSAL_PERCENTILE_VERSION = "thesis-causal-percentile-v1"
COMPOSITE_IDENTITY_VERSION = "composite-historical-dataset-v1"


@dataclass(frozen=True)
class AsOfFact:
    value: float | None
    source_ts_ms: int | None
    age_ms: int | None
    status: str


def asof_fact(
    observations: Sequence[Mapping[str, Any]], candle_close_ms: int, *,
    value_key: str, timestamp_key: str = "source_ts_ms", max_age_ms: int,
) -> AsOfFact:
    """Return the latest fact published at/before the candle close.

    Input must be ordered by source timestamp. Missing, non-finite and stale
    values remain UNKNOWN; they are never coerced to zero or forward-filled
    without a bound.
    """
    if max_age_ms < 0:
        raise ValueError("max_age_ms must be non-negative")
    timestamps = [int(row[timestamp_key]) for row in observations]
    if timestamps != sorted(timestamps):
        raise ValueError("observations must be ordered by source timestamp")
    index = bisect.bisect_right(timestamps, int(candle_close_ms)) - 1
    if index < 0:
        return AsOfFact(None, None, None, "UNKNOWN_NO_PRIOR_OBSERVATION")
    row = observations[index]
    source_ts = timestamps[index]
    age = int(candle_close_ms) - source_ts
    if age > max_age_ms:
        return AsOfFact(None, source_ts, age, "UNKNOWN_STALE")
    try:
        value = float(row[value_key])
    except (KeyError, TypeError, ValueError, OverflowError):
        return AsOfFact(None, source_ts, age, "UNKNOWN_INVALID")
    if not math.isfinite(value):
        return AsOfFact(None, source_ts, age, "UNKNOWN_INVALID")
    return AsOfFact(value, source_ts, age, "AVAILABLE")


def causal_percentile(values: Sequence[float | None], *, min_history: int) -> list[float | None]:
    """Expanding, strictly-causal percentile rank (history excludes current)."""
    if min_history < 1:
        raise ValueError("min_history must be positive")
    history: list[float] = []
    result: list[float | None] = []
    for raw in values:
        if raw is None:
            result.append(None)
            continue
        value = float(raw)
        if not math.isfinite(value):
            result.append(None)
            continue
        if len(history) < min_history:
            result.append(None)
        else:
            # Midrank handles ties deterministically. The current observation is
            # deliberately not inserted until after its rank is calculated.
            less = bisect.bisect_left(history, value)
            equal = bisect.bisect_right(history, value) - less
            result.append(100.0 * (less + 0.5 * equal) / len(history))
        bisect.insort(history, value)
    return result


def composite_dataset_identity(
    components: Iterable[Mapping[str, Any]], *, effective_start_ms: int,
    effective_end_ms: int,
) -> dict[str, Any]:
    """Build an order-independent identity without pretending sources are one DB."""
    normalized = []
    for item in components:
        required = {"kind", "dataset_id", "sha256"}
        if not required <= set(item):
            raise ValueError(f"component requires {sorted(required)}")
        normalized.append({
            "kind": str(item["kind"]), "dataset_id": str(item["dataset_id"]),
            "sha256": str(item["sha256"]),
            "raw_start_ms": item.get("raw_start_ms"),
            "raw_end_ms": item.get("raw_end_ms"),
            "source": item.get("source"), "source_version": item.get("source_version"),
        })
    normalized.sort(key=lambda row: (row["kind"], row["dataset_id"], row["sha256"]))
    if not normalized or effective_start_ms > effective_end_ms:
        raise ValueError("non-empty components and a valid effective intersection are required")
    contract = {"version": COMPOSITE_IDENTITY_VERSION, "components": normalized,
                "effective_start_ms": int(effective_start_ms),
                "effective_end_ms": int(effective_end_ms)}
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    contract["dataset_id"] = "composite-" + hashlib.sha256(encoded).hexdigest()[:24]
    return contract


def verify_snapshot_file(path: Path, expected_sha256: str) -> dict[str, str]:
    """Fail closed for derivative capabilities without blocking OHLCV callers."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise ValueError("THESIS_DERIVATIVES_SNAPSHOT_SHA256_MISMATCH")
    return {"status": "READY", "sha256": actual}


class DerivativeSnapshotReaderV1:
    """Read-only, batch PIT adapter for one verified immutable snapshot."""

    DATA_TYPES = {
        "OI": ("OPEN_INTEREST_USD", "open_interest_usd"),
        "FUNDING": ("FUNDING_RATE", "funding_rate"),
        "BASIS": ("BASIS_PCT", "basis_pct"),
    }

    def __init__(self, path: Path, *, expected_sha256: str, dataset_id: str,
                 manifest: Mapping[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.expected_sha256 = str(expected_sha256)
        self.dataset_id = str(dataset_id)
        self.manifest = dict(manifest or {})

    def _verified_sha(self) -> str:
        verified = verify_snapshot_file(self.path, self.expected_sha256)
        manifest_sha = self.manifest.get("database_sha256")
        manifest_id = self.manifest.get("dataset_id")
        if manifest_sha and str(manifest_sha).lower() != verified["sha256"].lower():
            raise ValueError("THESIS_DERIVATIVES_MANIFEST_SHA256_MISMATCH")
        if manifest_id and str(manifest_id) != self.dataset_id:
            raise ValueError("THESIS_DERIVATIVES_DATASET_ID_MISMATCH")
        return verified["sha256"]

    def readiness(self) -> dict[str, Any]:
        try:
            digest = self._verified_sha()
            with sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True) as connection:
                connection.execute("PRAGMA query_only=ON")
                rows = connection.execute(
                    """SELECT data_type,instrument,COUNT(*),MIN(source_ts_ms),MAX(source_ts_ms)
                       FROM derivative_observations GROUP BY data_type,instrument"""
                ).fetchall()
                gaps = connection.execute(
                    """SELECT data_type,instrument,MAX(source_ts_ms-prior_ts) FROM (
                         SELECT data_type,instrument,source_ts_ms,
                           LAG(source_ts_ms) OVER (PARTITION BY data_type,instrument ORDER BY source_ts_ms) prior_ts
                         FROM derivative_observations)
                       GROUP BY data_type,instrument""").fetchall()
            max_gaps = {(str(row[0]), str(row[1])): (int(row[2]) if row[2] is not None else None)
                        for row in gaps}
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(str(row[0]), []).append({
                    "instrument": str(row[1]), "rows": int(row[2]),
                    "start_ms": int(row[3]), "end_ms": int(row[4]),
                    "max_gap_ms": max_gaps.get((str(row[0]), str(row[1]))),
                })
            by_type = {}
            for data_type, partitions in grouped.items():
                minimum_rows = min(item["rows"] for item in partitions)
                start_ms, end_ms = max(item["start_ms"] for item in partitions), min(item["end_ms"] for item in partitions)
                by_type[data_type] = {
                    "rows": minimum_rows, "start_ms": start_ms, "end_ms": end_ms,
                    "cadence_ms": ((end_ms - start_ms) // (minimum_rows - 1)
                                   if minimum_rows > 1 else None),
                    "max_gap_ms": max((int(item["max_gap_ms"] or 0) for item in partitions),
                                      default=None),
                    "instruments": partitions,
                }
            if "coverage" in self.manifest:
                declared = sorted((str(item.get("data_type")), str(item.get("instrument")),
                                   int(item.get("rows", -1)), int(item.get("start_ms", -1)),
                                   int(item.get("end_ms", -1)))
                                  for item in self.manifest.get("coverage", [])
                                  if isinstance(item, Mapping))
                actual = sorted((data_type, str(item["instrument"]), int(item["rows"]),
                                 int(item["start_ms"]), int(item["end_ms"]))
                                for data_type, items in grouped.items() for item in items)
                if declared != actual:
                    raise ValueError("THESIS_DERIVATIVES_MANIFEST_COVERAGE_MISMATCH")
            return {"status": "READY", "dataset_id": self.dataset_id,
                    "sha256_abbreviated": digest[:12], "coverage": by_type}
        except (OSError, sqlite3.Error, ValueError) as error:
            return {"status": "BLOCKED", "reason": str(error), "coverage": {}}

    def _observations(self, instrument: str, data_type: str,
                      start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        with sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                """SELECT source_ts_ms,value,source,source_version,response_sha256
                   FROM derivative_observations
                   WHERE instrument=? AND data_type=? AND source_ts_ms BETWEEN ? AND ?
                   ORDER BY source_ts_ms""",
                (instrument, data_type, int(start_ms), int(end_ms)),
            ).fetchall()
        return [dict(row) for row in rows]

    def align(self, candle_rows: Sequence[Mapping[str, Any]], *,
              canonical_instrument: str, timeframe: str,
              required_groups: Sequence[str], as_of: int,
              ohlcv_component: Mapping[str, Any] | None = None) -> dict[str, Any]:
        # The implementation is shared below so the historical batch reader
        # remains the only owner of snapshot verification and component IDs.
        return _DerivativeReaderImplementations._align_historical_snapshot(
            self, candle_rows, canonical_instrument=canonical_instrument,
            timeframe=timeframe, required_groups=required_groups, as_of=as_of,
            ohlcv_component=ohlcv_component)


class _DerivativeReaderImplementations:
    """Current 1D OI derived from official live observations plus frozen history.

    The live source is downsampled to the same reviewed 16:00 UTC daily cadence
    exposed by the official historical endpoint. Higher-frequency live samples
    therefore cannot silently change the historical feature definition.
    """

    version = "current-okx-derivatives-v1"
    _EXPECTED = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")

    def __init__(self, microstructure_path: Path, historical: DerivativeSnapshotReaderV1,
                 *, clock: Any = time.time) -> None:
        self.path, self.historical, self.clock = Path(microstructure_path), historical, clock

    @staticmethod
    def _tables(connection: sqlite3.Connection) -> set[str]:
        return {str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def readiness(self, *, now: int | None = None) -> dict[str, Any]:
        as_of = int(self.clock() if now is None else now)
        try:
            historical = self.historical.readiness()
            oi = historical.get("coverage", {}).get("OPEN_INTEREST_USD", {})
            historical_instruments = {item.get("instrument") for item in oi.get("instruments", [])}
            end_ms = as_of * 1000
            live = {instrument: self._live_daily_oi(
                instrument, max(0, end_ms - 3 * 86_400_000), end_ms)
                for instrument in self._EXPECTED}
            facts = {instrument: self.latest(instrument, "OI", as_of, timeframe="1D")
                     for instrument in self._EXPECTED}
            latest = {instrument: (fact.get("timestamp") * 1000 if fact else None)
                      for instrument, fact in facts.items()}
            ready = (historical.get("status") == "READY"
                     and historical_instruments == set(self._EXPECTED)
                     and all(rows for rows in live.values())
                     and all(fact is not None for fact in facts.values()))
            return {"status": "READY" if ready else "BLOCKED",
                    "reason": None if ready else "CURRENT_OI_SOURCE_NOT_FRESH_OR_COMPLETE",
                    "supported_timeframes": ["1D"], "latest_source_ms": latest}
        except (OSError, sqlite3.Error, ValueError) as error:
            return {"status": "BLOCKED", "reason": str(error),
                    "supported_timeframes": ["1D"]}

    def _live_daily_oi(self, instrument: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[tuple[Any, Any, Any]] = []
        with sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True) as connection:
            tables = self._tables(connection)
            # One confirmed observation in the reviewed five-minute window
            # ending at exactly 16:00 UTC. Availability, not source time alone,
            # is bounded by the current evaluation as-of.
            day_ms, cutoff_in_day = 86_400_000, 16 * 3_600_000
            window_start = cutoff_in_day - 5 * 60_000
            if "oi_observations" in tables:
                rows = connection.execute(
                    """SELECT source_ts_ms,oi_usd,ingested_at_ms FROM (
                         SELECT source_ts_ms,oi_usd,ingested_at_ms,
                           ROW_NUMBER() OVER (PARTITION BY source_ts_ms/? ORDER BY source_ts_ms DESC) AS rank
                         FROM oi_observations WHERE instrument=? AND source_ts_ms BETWEEN ? AND ?
                           AND source_ts_ms % ? BETWEEN ? AND ? AND oi_usd IS NOT NULL
                           AND state='confirmed' AND ingested_at_ms<=?)
                       WHERE rank=1 ORDER BY source_ts_ms""",
                    (day_ms, instrument, start_ms, end_ms, day_ms, window_start,
                     cutoff_in_day, end_ms)).fetchall()
            elif "oi_1m" in tables:
                rows = connection.execute(
                    """SELECT observation_ts_ms,confirmed_oi,generated_at_ms FROM (
                         SELECT observation_ts_ms,confirmed_oi,generated_at_ms,
                           ROW_NUMBER() OVER (PARTITION BY observation_ts_ms/? ORDER BY observation_ts_ms DESC) AS rank
                         FROM oi_1m WHERE instrument=? AND observation_ts_ms BETWEEN ? AND ?
                           AND observation_ts_ms % ? BETWEEN ? AND ? AND confirmed_oi IS NOT NULL
                           AND status IN ('VALID','BACKFILLED_OFFICIAL','ARCHIVED_CONFIRMED')
                           AND generated_at_ms<=?)
                       WHERE rank=1 ORDER BY observation_ts_ms""",
                    (day_ms, instrument, start_ms, end_ms, day_ms, window_start,
                     cutoff_in_day, end_ms)).fetchall()
        return [{"source_ts_ms": int(row[0]), "value": float(row[1]),
                 "available_at_ms": int(row[2])} for row in rows]

    def latest(self, instrument: str, group: str, as_of: int, *, timeframe: str = "1D") -> dict[str, Any] | None:
        if group != "OI" or timeframe != "1D":
            return None
        self.historical._verified_sha()
        swap = instrument if instrument.endswith("-SWAP") else f"{instrument}-SWAP"
        end_ms, start_ms = int(as_of) * 1000, max(0, int(as_of) * 1000 - 500 * 86_400_000)
        historical = self.historical._observations(swap, "OPEN_INTEREST_USD", start_ms, end_ms)
        day_ms = 86_400_000
        # Daily bucket identity prevents a historical point and its live
        # continuation from becoming two samples on the same UTC date.
        combined = {int(row["source_ts_ms"]) // day_ms:
                    (int(row["source_ts_ms"]), float(row["value"]), int(row["source_ts_ms"]), "historical")
                    for row in historical}
        live_rows = self._live_daily_oi(swap, start_ms, end_ms)
        if (not live_rows or end_ms - max(int(row["source_ts_ms"]) for row in live_rows) > 2 * day_ms):
            return None
        for row in live_rows:
            bucket = int(row["source_ts_ms"]) // day_ms
            candidate = (int(row["source_ts_ms"]), float(row["value"]),
                         int(row["available_at_ms"]), "live")
            existing = combined.get(bucket)
            # Prefer the later source observation. At an exact tie the frozen,
            # audited publication remains authoritative for that historical day.
            if existing is None or candidate[0] > existing[0]:
                combined[bucket] = candidate
        ordered = sorted(combined.values())
        if len(ordered) < 32:
            return None
        changes: list[float | None] = [None]
        for (_prior_ts, prior, _prior_available, _prior_kind), (_current_ts, current, _available, _kind) in zip(ordered, ordered[1:]):
            changes.append((current / prior - 1.0) * 100.0 if prior else None)
        ranks = causal_percentile(changes, min_history=30)
        value, rank = changes[-1], ranks[-1]
        if value is None or rank is None:
            return None
        latest_ts, _latest_value, latest_available, latest_kind = ordered[-1]
        if latest_available > end_ms or end_ms - latest_ts > 2 * day_ms:
            return None
        return {"timestamp": latest_ts // 1000, "available_at": latest_available // 1000,
                "values": {"OI_CHANGE_PCT": value, "OI_CHANGE_PERCENTILE": rank},
                "current_evidence": True,
                "source": ("OKX_OFFICIAL_LIVE_PLUS_IMMUTABLE_HISTORY"
                           if live_rows else "OKX_OFFICIAL_IMMUTABLE_HISTORY_ONLY"),
                "source_version": self.version,
                "dataset_id": f"{self.historical.dataset_id}:current-oi"}

    def _align_historical_snapshot(self, candle_rows: Sequence[Mapping[str, Any]], *,
              canonical_instrument: str, timeframe: str,
              required_groups: Sequence[str], as_of: int,
              ohlcv_component: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """As-of join all required groups in three bounded scans, never N+1."""
        digest = self._verified_sha()
        groups = tuple(sorted(set(map(str, required_groups))))
        if not groups or set(groups) - set(self.DATA_TYPES):
            raise ValueError("required derivative source group is unsupported")
        rows = [dict(item) for item in candle_rows]
        if not rows:
            raise ValueError("OHLCV rows are required for derivative alignment")
        width_ms = int({"15m": 900, "1H": 3_600, "4H": 14_400,
                        "1D": 86_400}[timeframe] * 1000)
        first_ms, end_ms = int(rows[0]["candle_close_ts"]) * 1000, int(as_of) * 1000
        swap = canonical_instrument if canonical_instrument.endswith("-SWAP") else canonical_instrument + "-SWAP"
        observations: dict[str, list[dict[str, Any]]] = {}
        components: list[dict[str, Any]] = []
        starts, ends = [first_ms], [end_ms]
        for group in groups:
            data_type, _target = self.DATA_TYPES[group]
            # Include one freshness window before OHLCV so the first retained
            # candle can use a genuinely prior observation.
            max_age = (max(width_ms, 2 * 3_600_000) if group == "OI" else
                       12 * 3_600_000 if group == "FUNDING" else
                       max(15 * 60_000, min(width_ms, 3_600_000)))
            lane = self._observations(swap, data_type, max(0, first_ms - max_age), end_ms)
            if not lane:
                raise ValueError(f"{group}_HISTORICAL_DATASET_UNAVAILABLE")
            observations[group] = lane
            raw_start, raw_end = int(lane[0]["source_ts_ms"]), int(lane[-1]["source_ts_ms"])
            starts.append(raw_start)
            ends.append(raw_end + max_age)
            components.append({
                "kind": group, "dataset_id": f"{self.dataset_id}:{group}", "sha256": digest,
                "raw_start_ms": raw_start, "raw_end_ms": raw_end,
                "source": lane[0].get("source"), "source_version": lane[0].get("source_version"),
            })
        effective_start, effective_end = max(starts), min(ends)
        if effective_start > effective_end:
            raise ValueError("DERIVATIVE_AND_OHLCV_COVERAGE_DO_NOT_INTERSECT")
        aligned: list[dict[str, Any]] = []
        for source in rows:
            close_ms = int(source["candle_close_ts"]) * 1000
            if not effective_start <= close_ms <= effective_end:
                continue
            row = dict(source)
            for group in groups:
                _data_type, target = self.DATA_TYPES[group]
                max_age = (max(width_ms, 2 * 3_600_000) if group == "OI" else
                           12 * 3_600_000 if group == "FUNDING" else
                           max(15 * 60_000, min(width_ms, 3_600_000)))
                fact = asof_fact(observations[group], close_ms, value_key="value", max_age_ms=max_age)
                # BASIS_PCT is persisted as a ratio by the audited source
                # builder and exposed to thesis users as percentage points.
                row[target] = fact.value * 100 if group == "BASIS" and fact.value is not None else fact.value
                row[f"_{target}_source_ts_ms"] = fact.source_ts_ms
                row[f"_{target}_status"] = fact.status
            aligned.append(row)
        if not aligned:
            raise ValueError("DERIVATIVE_AND_OHLCV_COVERAGE_DO_NOT_INTERSECT")
        if ohlcv_component is None:
            ohlcv_component = {
                "kind": "OHLCV", "dataset_id": "bounded-ohlcv",
                "sha256": hashlib.sha256(json.dumps([
                    {key: item.get(key) for key in ("ts", "open", "high", "low", "close", "volume")}
                    for item in aligned], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "raw_start_ms": int(aligned[0]["candle_close_ts"]) * 1000,
                "raw_end_ms": int(aligned[-1]["candle_close_ts"]) * 1000,
                "source": "qualified_ohlcv", "source_version": None,
            }
        identity = composite_dataset_identity(
            [ohlcv_component, *components],
            effective_start_ms=int(aligned[0]["candle_close_ts"]) * 1000,
            effective_end_ms=int(aligned[-1]["candle_close_ts"]) * 1000,
        )
        return {"rows": aligned, "composite_dataset_identity": identity,
                "effective_start_ms": effective_start, "effective_end_ms": effective_end}


class CurrentDerivativeReaderV1(_DerivativeReaderImplementations):
    """Public current-derivative adapter; snapshot alignment stays private."""
