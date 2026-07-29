"""Read-only readiness gate for future microstructure research.

This module deliberately has no dependency on AutoResearch, strategies, order
APIs, or the research registry.  Missing observations stay missing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


RESEARCH_READINESS_VERSION = "microstructure-research-readiness-v1"
DAY_MS = 86_400_000
INSTRUMENTS = ("BTC", "ETH", "SOL")
FEATURE_GROUPS = ("CVD", "OI", "CVD + OI", "funding + OI", "basis + OI")
READY_PENDING = "RESEARCH_READY_PENDING_HUMAN_APPROVAL"
READINESS_STATUSES = (
    "COLLECTING",
    "APPROACHING_READINESS",
    READY_PENDING,
    "BLOCKED_DATA_QUALITY",
    "BLOCKED_CRITICAL_GAP",
    "STALE_SOURCE",
)

# All qualification policy is versioned here.  No evaluator threshold is
# intentionally hidden in control flow.
READINESS_CONFIG: Mapping[str, Any] = {
    "version": RESEARCH_READINESS_VERSION,
    "usable_days_min": 30.0,
    "continuous_days_min": 30.0,
    "recent_window_days": 30,
    "recent_coverage_min": 0.95,
    "critical_live_gap_max": 0,
    "native_independent_events_min": {"CVD": 2000, "OI": 1000},
    "label": {
        "horizon_ms": 3_600_000,
        "non_overlapping_min": 30,
        "overlap_min": 0.80,
    },
    "approaching_ratio": 0.80,
    "sources": {
        "CVD": {
            "table": "cvd_aggregates", "timestamp": "bucket_ms",
            "resolution": "1m", "frequency_ms": 60_000, "lane": "trades",
            "value": "delta", "freshness_ms": 5 * 60_000,
        },
        "OI": {
            "table": "oi_aggregates", "timestamp": "bucket_ms",
            "resolution": "5m", "frequency_ms": 300_000, "lane": "oi",
            "value": "last_value", "freshness_ms": 10 * 60_000,
        },
        "funding": {
            "table": "funding_settled", "timestamp": "funding_time_ms",
            "resolution": None, "frequency_ms": 28_800_000,
            "lane": "funding_settled", "value": "funding_rate",
            "freshness_ms": 12 * 3_600_000,
        },
        "basis": {
            "table": "basis_aggregates", "timestamp": "bucket_ms",
            "resolution": "1H", "frequency_ms": 3_600_000, "lane": "basis",
            "value": "last_basis_pct", "freshness_ms": 2 * 3_600_000,
        },
    },
}

GROUP_SOURCES = {
    "CVD": ("CVD",),
    "OI": ("OI",),
    "CVD + OI": ("CVD", "OI"),
    "funding + OI": ("funding", "OI"),
    "basis + OI": ("basis", "OI"),
}


@dataclass(frozen=True)
class SourceMetrics:
    source: str
    earliest_ms: int | None
    latest_ms: int | None
    observation_count: int
    independent_event_count: int
    native_frequency_ms: int
    freshness_limit_ms: int
    fresh: bool
    content_sha256: str


def _iso(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _instrument_name(short_name: str) -> str:
    return f"{short_name}-USDT-SWAP"


def _read_source(
    connection: sqlite3.Connection, source: str, instrument: str, as_of_ms: int,
) -> tuple[SourceMetrics, list[tuple[int, float]], list[tuple[int, float]]]:
    config = READINESS_CONFIG["sources"][source]
    table = str(config["table"])
    columns = _columns(connection, table)
    if not columns:
        return SourceMetrics(
            source, None, None, 0, 0, int(config["frequency_ms"]),
            int(config["freshness_ms"]), False,
            hashlib.sha256(b"").hexdigest(),
        ), [], []
    timestamp = str(config["timestamp"])
    value = str(config["value"])
    predicates = ["instrument=?", f"{timestamp}<=?"]
    parameters: list[Any] = [_instrument_name(instrument), as_of_ms]
    resolution = config["resolution"]
    if resolution is not None and "resolution" in columns:
        predicates.append("resolution=?")
        parameters.append(resolution)
    if "state" in columns:
        predicates.append("state='confirmed'")
    if "gap_flag" in columns:
        predicates.append("gap_flag=0")
    rows = connection.execute(
        f"""SELECT {timestamp},{value} FROM {table}
            WHERE {' AND '.join(predicates)}
            AND {value} IS NOT NULL ORDER BY {timestamp}""",
        parameters,
    ).fetchall()
    observations = [(int(row[0]), float(row[1])) for row in rows]
    # CVD events are unique confirmed buckets. OI only advances when the
    # latest confirmed value changes. Other native sources use unique events.
    if source == "OI":
        independent: list[tuple[int, float]] = []
        previous: float | None = None
        for event in observations:
            if previous is None or event[1] != previous:
                independent.append(event)
            previous = event[1]
    else:
        seen: set[int] = set()
        independent = []
        for event in observations:
            if event[0] not in seen:
                independent.append(event)
                seen.add(event[0])
    earliest = observations[0][0] if observations else None
    latest = observations[-1][0] if observations else None
    freshness = int(config["freshness_ms"])
    metrics = SourceMetrics(
        source=source,
        earliest_ms=earliest,
        latest_ms=latest,
        observation_count=len(observations),
        independent_event_count=len(independent),
        native_frequency_ms=int(config["frequency_ms"]),
        freshness_limit_ms=freshness,
        fresh=latest is not None and as_of_ms - latest <= freshness,
        content_sha256=hashlib.sha256(json.dumps(
            observations, separators=(",", ":")).encode()).hexdigest(),
    )
    return metrics, observations, independent


def _intervals(
    events: Sequence[tuple[int, float]], frequency_ms: int,
    start_ms: int | None = None, end_ms: int | None = None,
) -> list[tuple[int, int]]:
    result = []
    for timestamp, _ in events:
        left = max(timestamp, start_ms) if start_ms is not None else timestamp
        right = timestamp + frequency_ms
        right = min(right, end_ms) if end_ms is not None else right
        if right > left:
            result.append((left, right))
    return _merge(result)


def _merge(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _intersect(
    left: Sequence[tuple[int, int]], right: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    output: list[tuple[int, int]] = []
    i = j = 0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if start < end:
            output.append((start, end))
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return _merge(output)


def _subtract(
    intervals: Sequence[tuple[int, int]], gaps: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    result = list(intervals)
    for gap_start, gap_end in _merge(gaps):
        next_result: list[tuple[int, int]] = []
        for start, end in result:
            if gap_end <= start or gap_start >= end:
                next_result.append((start, end))
                continue
            if start < gap_start:
                next_result.append((start, gap_start))
            if gap_end < end:
                next_result.append((gap_end, end))
        result = next_result
    return _merge(result)


def _duration(intervals: Sequence[tuple[int, int]]) -> int:
    return sum(end - start for start, end in intervals)


def _gap_rows(
    connection: sqlite3.Connection, sources: Sequence[str], instrument: str,
) -> list[dict[str, Any]]:
    if not _table_exists(connection, "collection_gaps"):
        return []
    columns = _columns(connection, "collection_gaps")
    if not {"lane", "instrument", "start_ms", "end_ms"}.issubset(columns):
        return []
    lanes = [str(READINESS_CONFIG["sources"][source]["lane"]) for source in sources]
    placeholders = ",".join("?" for _ in lanes)
    resolution_filter = (
        "AND resolved_at_ms IS NULL" if "resolved_at_ms" in columns else "")
    rows = connection.execute(
        f"""SELECT * FROM collection_gaps WHERE instrument=?
            AND lane IN ({placeholders}) {resolution_filter}
            ORDER BY start_ms DESC""",
        (_instrument_name(instrument), *lanes),
    ).fetchall()
    names = [item[1] for item in connection.execute(
        "PRAGMA table_info(collection_gaps)")]
    return [dict(zip(names, row)) for row in rows]


def _critical_gaps(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    critical = []
    for row in rows:
        text = " ".join(str(row.get(key, "")) for key in (
            "classification", "severity", "reason"))
        if "CRITICAL_LIVE_GAP" in text.upper() or str(
                row.get("severity", "")).lower() == "critical":
            critical.append(row)
    return critical


def _non_overlapping_count(timestamps: Sequence[int], horizon_ms: int) -> int:
    count = 0
    next_available = -2**63
    for timestamp in sorted(set(timestamps)):
        if timestamp >= next_available:
            count += 1
            next_available = timestamp + horizon_ms
    return count


def _dataset_identity(
    instrument: str, feature_group: str, sources: Sequence[SourceMetrics],
) -> str:
    payload = {
        "version": RESEARCH_READINESS_VERSION,
        "instrument": instrument,
        "feature_group": feature_group,
        "sources": [{
            "source": source.source,
            "earliest_ms": source.earliest_ms,
            "latest_ms": source.latest_ms,
            "observation_count": source.observation_count,
            "independent_event_count": source.independent_event_count,
            "native_frequency_ms": source.native_frequency_ms,
            "content_sha256": source.content_sha256,
        } for source in sources],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _minimum_events(feature_group: str) -> int:
    sources = GROUP_SOURCES[feature_group]
    thresholds = READINESS_CONFIG["native_independent_events_min"]
    applicable = [int(thresholds[source]) for source in sources if source in thresholds]
    return min(applicable) if applicable else int(thresholds["OI"])


def evaluate_one(
    connection: sqlite3.Connection, feature_group: str, instrument: str,
    as_of_ms: int,
) -> dict[str, Any]:
    if feature_group not in FEATURE_GROUPS or instrument not in INSTRUMENTS:
        raise ValueError("unsupported feature group or instrument")
    source_names = GROUP_SOURCES[feature_group]
    source_pairs = [
        _read_source(connection, source, instrument, as_of_ms)
        for source in source_names
    ]
    source_metrics = [pair[0] for pair in source_pairs]
    observations = [pair[1] for pair in source_pairs]
    native_events = [pair[2] for pair in source_pairs]
    all_earliest = [item.earliest_ms for item in source_metrics
                    if item.earliest_ms is not None]
    all_latest = [item.latest_ms for item in source_metrics
                  if item.latest_ms is not None]
    earliest = max(all_earliest) if len(all_earliest) == len(source_names) else None
    latest = min(all_latest) if len(all_latest) == len(source_names) else None
    natural_days = (
        max(0.0, (latest - earliest) / DAY_MS)
        if earliest is not None and latest is not None else 0.0
    )
    usable: list[tuple[int, int]] | None = None
    recent_start = as_of_ms - int(READINESS_CONFIG["recent_window_days"]) * DAY_MS
    recent: list[tuple[int, int]] | None = None
    for metrics, events in zip(source_metrics, observations):
        coverage = _intervals(events, metrics.native_frequency_ms)
        recent_coverage = _intervals(
            events, metrics.native_frequency_ms, recent_start, as_of_ms)
        usable = coverage if usable is None else _intersect(usable, coverage)
        recent = recent_coverage if recent is None else _intersect(recent, recent_coverage)
    usable = usable or []
    recent = recent or []
    gaps = _gap_rows(connection, source_names, instrument)
    gap_intervals = [
        (int(row["start_ms"]), int(row["end_ms"])) for row in gaps]
    usable = _subtract(usable, gap_intervals)
    recent = _subtract(recent, gap_intervals)
    usable_days = _duration(usable) / DAY_MS
    continuous_days = max((end - start for start, end in usable), default=0) / DAY_MS
    recent_ratio = min(1.0, _duration(recent) / (
        int(READINESS_CONFIG["recent_window_days"]) * DAY_MS))
    independent_count = min(
        (len(events) for events in native_events), default=0)
    label_horizon = int(READINESS_CONFIG["label"]["horizon_ms"])
    label_timestamps = [
        timestamp for timestamp, _ in native_events[
            min(range(len(native_events)), key=lambda index: len(native_events[index]))
        ]
        if timestamp + label_horizon <= (latest or -1)
    ] if native_events else []
    non_overlapping = _non_overlapping_count(label_timestamps, label_horizon)
    label_overlap = (
        len(label_timestamps) / independent_count if independent_count else 0.0)
    critical = _critical_gaps(gaps)
    reasons: list[str] = []
    if usable_days < float(READINESS_CONFIG["usable_days_min"]):
        reasons.append("GAP_ADJUSTED_USABLE_DAYS_BELOW_THRESHOLD")
    if continuous_days < float(READINESS_CONFIG["continuous_days_min"]):
        reasons.append("MAX_CONTINUOUS_INTERVAL_BELOW_THRESHOLD")
    if recent_ratio < float(READINESS_CONFIG["recent_coverage_min"]):
        reasons.append("RECENT_30D_COVERAGE_BELOW_THRESHOLD")
    if critical:
        reasons.append("UNRESOLVED_CRITICAL_LIVE_GAP")
    if not all(source.fresh for source in source_metrics):
        reasons.append("SOURCE_STALE")
    if independent_count < _minimum_events(feature_group):
        reasons.append("INDEPENDENT_EVENTS_BELOW_THRESHOLD")
    if non_overlapping < int(READINESS_CONFIG["label"]["non_overlapping_min"]):
        reasons.append("NON_OVERLAPPING_LABELS_BELOW_THRESHOLD")
    if label_overlap < float(READINESS_CONFIG["label"]["overlap_min"]):
        reasons.append("LABEL_OVERLAP_BELOW_THRESHOLD")

    if critical:
        status = "BLOCKED_CRITICAL_GAP"
    elif "SOURCE_STALE" in reasons and all_latest:
        status = "STALE_SOURCE"
    elif natural_days >= float(READINESS_CONFIG["usable_days_min"]) and reasons:
        status = "BLOCKED_DATA_QUALITY"
    elif not reasons:
        status = READY_PENDING
    else:
        ratios = (
            usable_days / float(READINESS_CONFIG["usable_days_min"]),
            continuous_days / float(READINESS_CONFIG["continuous_days_min"]),
            recent_ratio / float(READINESS_CONFIG["recent_coverage_min"]),
            independent_count / max(1, _minimum_events(feature_group)),
            non_overlapping / int(READINESS_CONFIG["label"]["non_overlapping_min"]),
            label_overlap / float(READINESS_CONFIG["label"]["overlap_min"]),
        )
        status = (
            "APPROACHING_READINESS"
            if min(ratios) >= float(READINESS_CONFIG["approaching_ratio"])
            else "COLLECTING"
        )

    remaining_events = max(0, _minimum_events(feature_group) - independent_count)
    event_days = (
        remaining_events * max(source.native_frequency_ms for source in source_metrics)
        / DAY_MS
    )
    remaining_labels = max(
        0, int(READINESS_CONFIG["label"]["non_overlapping_min"]) - non_overlapping)
    label_days = remaining_labels * label_horizon / DAY_MS
    remaining_days = max(
        0.0,
        float(READINESS_CONFIG["usable_days_min"]) - usable_days,
        float(READINESS_CONFIG["continuous_days_min"]) - continuous_days,
        event_days,
        label_days,
    )
    expected_ready_ms = (
        as_of_ms + int(remaining_days * DAY_MS)
        if status in {"COLLECTING", "APPROACHING_READINESS"} else None
    )
    return {
        "readiness_version": RESEARCH_READINESS_VERSION,
        "feature_group": feature_group,
        "instrument": instrument,
        "status": status,
        "source_earliest_ms": earliest,
        "source_earliest": _iso(earliest),
        "source_latest_ms": latest,
        "source_latest": _iso(latest),
        "natural_coverage_days": round(natural_days, 8),
        "gap_adjusted_usable_days": round(usable_days, 8),
        "max_continuous_usable_days": round(continuous_days, 8),
        "recent_30d_coverage": round(recent_ratio, 8),
        "raw_observation_count": sum(
            source.observation_count for source in source_metrics),
        "native_independent_event_count": independent_count,
        "non_overlapping_label_count": non_overlapping,
        "unresolved_critical_gap_count": len(critical),
        "most_recent_critical_gap": dict(critical[0]) if critical else None,
        "source_freshness": {
            source.source: {
                "fresh": source.fresh,
                "latest_ms": source.latest_ms,
                "freshness_limit_ms": source.freshness_limit_ms,
            } for source in source_metrics
        },
        "sources": [{
            "source": source.source,
            "earliest_ms": source.earliest_ms,
            "earliest": _iso(source.earliest_ms),
            "latest_ms": source.latest_ms,
            "latest": _iso(source.latest_ms),
            "raw_observation_count": source.observation_count,
            "native_independent_event_count": source.independent_event_count,
            "native_frequency_ms": source.native_frequency_ms,
            "content_sha256": source.content_sha256,
        } for source in source_metrics],
        "label_overlap": round(label_overlap, 8),
        "dataset_identity": _dataset_identity(
            instrument, feature_group, source_metrics),
        "blocking_reasons": reasons,
        "estimated_earliest_readiness_ms": expected_ready_ms,
        "estimated_earliest_readiness": _iso(expected_ready_ms),
        "automatic_actions": [],
    }


def _open_read_only(database: str | Path) -> sqlite3.Connection:
    path = Path(database).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {path}")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def evaluate_readiness(
    database: str | Path, *, instruments: Sequence[str] | None = None,
    feature_groups: Sequence[str] | None = None, as_of_ms: int | None = None,
) -> dict[str, Any]:
    selected_instruments = tuple(instruments or INSTRUMENTS)
    selected_groups = tuple(feature_groups or FEATURE_GROUPS)
    evaluated_at = int(as_of_ms if as_of_ms is not None else time.time() * 1000)
    with _open_read_only(database) as connection:
        results = [
            evaluate_one(connection, group, instrument, evaluated_at)
            for instrument in selected_instruments for group in selected_groups
        ]
    return {
        "schema_version": RESEARCH_READINESS_VERSION,
        "evaluated_at_ms": evaluated_at,
        "evaluated_at": _iso(evaluated_at),
        "database": str(Path(database).resolve()),
        "thresholds": READINESS_CONFIG,
        "results": results,
        "side_effects": {
            "database_read_only": True,
            "research_jobs_created": 0,
            "factors_generated": 0,
            "signals_sent": 0,
        },
    }
