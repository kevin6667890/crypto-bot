"""Read-only access to locally captured operations trends."""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRENDS_DB = ROOT / ".runtime" / "operations_trends.db"
WINDOW_SECONDS = {"1h": 3600, "6h": 21_600, "24h": 86_400}


def percentile(values: list[float], quantile: float) -> float | None:
    """Return a deterministic nearest-rank percentile for small telemetry sets."""
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return None
    index = max(0, math.ceil(quantile * len(clean)) - 1)
    return round(clean[index], 3)


def read_operations_trends(
    window: str = "24h",
    *,
    db_path: Path = DEFAULT_TRENDS_DB,
    now: int | None = None,
) -> dict[str, Any]:
    selected = window if window in WINDOW_SECONDS else "24h"
    if not db_path.exists():
        return {"enabled": False, "window": selected, "points": [], "latency": {
            "p50_ms": None, "p95_ms": None}}

    cutoff = (int(time.time()) if now is None else now) - WINDOW_SECONDS[selected]
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT minute_ts, health_latency_ms, coverage_latency_ms,
                      eligibility_latency_ms, wal_size_bytes,
                      maintenance_duration_ms, checkpoint_duration_ms,
                      queue_depth, live_lag_seconds, iowait_percent,
                      critical_gap_count, service_state
               FROM operations_trends
               WHERE minute_ts >= ?
               ORDER BY minute_ts""",
            (cutoff,),
        ).fetchall()

    points = []
    latencies: list[float] = []
    for row in rows:
        latencies.extend(
            float(row[key]) for key in (
                "health_latency_ms", "coverage_latency_ms",
                "eligibility_latency_ms")
            if row[key] is not None)
        anomaly = (
            row["service_state"] not in {"RUNNING", "HEALTHY"}
            or (row["critical_gap_count"] or 0) > 0
            or (row["live_lag_seconds"] or 0) > 120
        )
        points.append({
            "timestamp": row["minute_ts"],
            "health_latency_ms": row["health_latency_ms"],
            "coverage_latency_ms": row["coverage_latency_ms"],
            "eligibility_latency_ms": row["eligibility_latency_ms"],
            "wal_size_bytes": row["wal_size_bytes"],
            "maintenance_duration_ms": row["maintenance_duration_ms"],
            "checkpoint_duration_ms": row["checkpoint_duration_ms"],
            "queue_depth": row["queue_depth"],
            "live_lag_seconds": row["live_lag_seconds"],
            "iowait_percent": row["iowait_percent"],
            "critical_gap_count": row["critical_gap_count"],
            "service_state": row["service_state"],
            "anomaly": anomaly,
        })
    return {
        "enabled": True,
        "window": selected,
        "points": points,
        "latency": {
            "p50_ms": percentile(latencies, .50),
            "p95_ms": percentile(latencies, .95),
        },
    }
