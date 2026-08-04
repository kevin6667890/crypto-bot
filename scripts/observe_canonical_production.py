#!/usr/bin/env python3
"""Record one read-only canonical production observation per minute."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1D")


def readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def json_url(url: str, timeout: int = 15) -> tuple[dict[str, Any] | None, float, str | None]:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = json.loads(response.read())
        return body, round((time.monotonic() - started) * 1000, 3), None
    except Exception as error:
        return None, round((time.monotonic() - started) * 1000, 3), type(error).__name__


def service_state(service: str) -> dict[str, Any]:
    try:
        ids = subprocess.run(
            ["docker", "ps", "-aq", "--filter",
             f"label=com.docker.compose.service={service}"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.split()
        if not ids:
            return {"present": False}
        payload = subprocess.run(
            ["docker", "inspect", ids[0]], check=True, capture_output=True,
            text=True, timeout=10,
        ).stdout
        item = json.loads(payload)[0]
        state = item.get("State", {})
        pid = int(state.get("Pid") or 0)
        try:
            fd_count = len(list(Path(f"/proc/{pid}/fd").iterdir())) if pid else None
        except OSError:
            fd_count = None
        return {
            "present": True, "id": item["Id"][:12],
            "running": bool(state.get("Running")),
            "restart_count": int(item.get("RestartCount", 0)),
            "health": (state.get("Health") or {}).get("Status"),
            "fd_count": fd_count,
        }
    except Exception as error:
        return {"present": None, "error": type(error).__name__}


def disk_counters() -> tuple[int, int]:
    total_io_ms = total_weighted_ms = 0
    try:
        for line in Path("/proc/diskstats").read_text().splitlines():
            fields = line.split()
            if (len(fields) >= 14 and not fields[2].startswith(("loop", "ram"))
                    and Path("/sys/block", fields[2]).exists()):
                total_io_ms += int(fields[12])
                total_weighted_ms += int(fields[13])
    except OSError:
        pass
    return total_io_ms, total_weighted_ms


def cpu_counters() -> tuple[int, int]:
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        values = [int(value) for value in fields]
        return sum(values), values[4] if len(values) > 4 else 0
    except (OSError, ValueError):
        return 0, 0


def collect(args: argparse.Namespace, sample: int, prior: dict[str, int]) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    record: dict[str, Any] = {"sample": sample, "captured_at_ms": now_ms}
    with readonly(args.source_db) as source:
        record["raw_watermarks"] = {
            instrument: {
                "trades": source.execute(
                    "SELECT MAX(source_ts_ms) FROM trade_flow_observations WHERE instrument=?",
                    (instrument,),).fetchone()[0],
                "oi": source.execute(
                    "SELECT MAX(source_ts_ms) FROM oi_observations WHERE instrument=?",
                    (instrument,),).fetchone()[0],
            } for instrument in INSTRUMENTS
        }
    with readonly(args.canonical_db) as canonical:
        watermarks: dict[str, Any] = {}
        for instrument in INSTRUMENTS:
            watermarks[instrument] = {
                "cvd_1m": canonical.execute(
                    "SELECT MAX(bucket_ms) FROM cvd_1m WHERE instrument=? AND signed_delta IS NOT NULL",
                    (instrument,),).fetchone()[0],
                "oi_1m": canonical.execute(
                    "SELECT MAX(bucket_ms) FROM oi_1m WHERE instrument=? AND confirmed_oi IS NOT NULL",
                    (instrument,),).fetchone()[0],
            }
            for resolution in TIMEFRAMES[1:]:
                watermarks[instrument][f"cvd_{resolution}"] = canonical.execute(
                    "SELECT MAX(bucket_ms) FROM cvd_higher_timeframes WHERE instrument=? AND resolution=? AND signed_delta IS NOT NULL",
                    (instrument, resolution),).fetchone()[0]
                watermarks[instrument][f"oi_{resolution}"] = canonical.execute(
                    "SELECT MAX(bucket_ms) FROM oi_higher_timeframes WHERE instrument=? AND resolution=? AND confirmed_oi IS NOT NULL",
                    (instrument, resolution),).fetchone()[0]
        record["aggregate_watermarks"] = watermarks
    with readonly(args.paper_db) as paper:
        record["paper_orders"] = dict(paper.execute(
            "SELECT status,COUNT(*) FROM paper_trades GROUP BY status").fetchall())

    health, health_ms, health_error = json_url(args.collector_health_url)
    record["collector"] = {"latency_ms": health_ms, "error": health_error,
                           "payload": health}
    instrument = INSTRUMENTS[sample % len(INSTRUMENTS)].removesuffix("-SWAP")
    timeframe = TIMEFRAMES[sample % len(TIMEFRAMES)]
    end = now_ms // 1000
    series = "cvd" if sample % 2 else "oi"
    query = urllib.parse.urlencode({
        "instrument": instrument, "series": series, "timeframe": timeframe,
        "start": end - 7 * 86400, "end": end, "max_points": 500,
        "schema_version": "canonical-microstructure-schema-v1",
        "history_version": "canonical-microstructure-history-v1",
    })
    api, api_ms, api_error = json_url(args.paper_api_url + "?" + query)
    record["api_probe"] = {
        "instrument": instrument, "series": series, "timeframe": timeframe,
        "latency_ms": api_ms, "error": api_error,
        "actual_resolution": api.get("actual_resolution") if api else None,
        "status": api.get("status") if api else None,
        "returned_point_count": api.get("returned_point_count") if api else None,
    }
    record["wal_bytes"] = {
        "raw": Path(str(args.source_db) + "-wal").stat().st_size
        if Path(str(args.source_db) + "-wal").exists() else 0,
        "canonical": Path(str(args.canonical_db) + "-wal").stat().st_size
        if Path(str(args.canonical_db) + "-wal").exists() else 0,
    }
    total_cpu, iowait = cpu_counters()
    io_ms, weighted_ms = disk_counters()
    elapsed_cpu = total_cpu - prior.get("total_cpu", total_cpu)
    record["host"] = {
        "iowait_percent": round(100 * (iowait - prior.get("iowait", iowait))
                                / elapsed_cpu, 4) if elapsed_cpu > 0 else None,
        "disk_io_util_percent": round(100 * (io_ms - prior.get("io_ms", io_ms))
                                          / max(1, args.interval_seconds * 1000), 4),
        "disk_weighted_io_ms_delta": weighted_ms - prior.get("weighted_ms", weighted_ms),
        "root_free_bytes": os.statvfs("/").f_bavail * os.statvfs("/").f_frsize,
    }
    prior.update(total_cpu=total_cpu, iowait=iowait, io_ms=io_ms,
                 weighted_ms=weighted_ms)
    record["containers"] = {
        service: service_state(service)
        for service in ("paper-api", "microstructure-collector", "frontend", "crypto-bot")
    }
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--canonical-db", type=Path, required=True)
    parser.add_argument("--paper-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=1440)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--collector-health-url", default="http://127.0.0.1:8770/health")
    parser.add_argument("--paper-api-url", default="http://127.0.0.1:8765/api/paper/flow/history/v1")
    args = parser.parse_args()
    if args.samples < 1 or args.interval_seconds < 1:
        raise SystemExit("samples and interval must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prior: dict[str, int] = {}
    with args.output.open("a", encoding="utf-8", buffering=1) as output:
        for sample in range(args.samples):
            started = time.monotonic()
            record = collect(args, sample, prior)
            output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
            remaining = args.interval_seconds - (time.monotonic() - started)
            if sample + 1 < args.samples and remaining > 0:
                time.sleep(remaining)


if __name__ == "__main__":
    main()
