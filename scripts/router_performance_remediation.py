"""Deterministic Router V2 performance profiling and equivalence corpus tooling."""
from __future__ import annotations

import argparse
import cProfile
from copy import deepcopy
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import pstats
import statistics
import sys
import time
import tracemalloc
from typing import Any, Callable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dashboard.strategy_router_v2 import StrategyRouterV2, _canonical, stable_hash
from tests.test_strategy_router_v2 import (
    AS_OF,
    breakout_inputs,
    inputs,
    interaction,
    ma_setup,
)


ROUTER = StrategyRouterV2()


def _trend_case(direction: str, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    trend = "up" if direction == "LONG" else "down"
    counter = "down" if direction == "LONG" else "up"
    value, state = inputs(
        {"1W": trend, "1D": trend, "4H": trend, "1H": counter, "15m": counter},
        momentum="oversold" if direction == "LONG" else "overbought",
        quality="AVAILABLE" if seed % 3 else "PARTIAL",
    )
    level_types = ("EMA20", "MA60", "BREAKOUT_RETEST")
    interaction_types = ("APPROACHING", "TOUCHING", "RECLAIMED" if direction == "LONG" else "REJECTED")
    state["level_interactions"] = [
        interaction(
            level_types[seed % len(level_types)],
            "FROM_ABOVE" if direction == "LONG" else "FROM_BELOW",
            interaction_type=interaction_types[(seed // 3) % len(interaction_types)],
            boundary=98.0 + (seed % 17) * 0.25,
            touches=1 + seed % 4,
        )
    ]
    state["timeframes"]["15m"]["momentum_state"] = (
        "RECOVERING_FROM_OVERSOLD" if direction == "LONG" else "ROLLING_OVER_FROM_OVERBOUGHT"
    )
    if seed % 11 == 0:
        state["primary_state_code"] = "MAJOR_RESISTANCE_TEST" if direction == "LONG" else "MAJOR_SUPPORT_TEST"
    return value, state


def _ma_case(direction: str, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    value, state = ma_setup(
        direction,
        touch=seed % 3 == 1,
        reclaim=seed % 3 == 2,
        confluence=seed % 5 != 0,
    )
    state["level_interactions"][0]["boundary"] = 97.0 + (seed % 29) * 0.25
    state["level_interactions"][0]["zone_low"] = state["level_interactions"][0]["boundary"] - 0.25
    state["level_interactions"][0]["zone_high"] = state["level_interactions"][0]["boundary"] + 0.25
    if seed % 3 == 2:
        state["timeframes"]["15m"]["momentum_state"] = (
            "RECOVERING_FROM_OVERSOLD" if direction == "LONG" else "ROLLING_OVER_FROM_OVERBOUGHT"
        )
        state["level_interactions"].append(
            interaction(
                "SWING_HIGH" if direction == "LONG" else "SWING_LOW",
                "FROM_BELOW" if direction == "LONG" else "FROM_ABOVE",
                boundary=104 if direction == "LONG" else 96,
            )
        )
    return value, state


def _breakout_case(direction: str, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    confirmed = "BREAKOUT_CONFIRMED" if direction == "LONG" else "BREAKDOWN_CONFIRMED"
    retesting = "BREAKOUT_RETESTING" if direction == "LONG" else "BREAKDOWN_RETESTING"
    stages = ("OBSERVING", "BREAKOUT_CANDIDATE" if direction == "LONG" else "BREAKDOWN_CANDIDATE", confirmed, retesting)
    stage = stages[seed % len(stages)]
    interaction_type = "RETESTING" if stage == retesting else "APPROACHING"
    value, state = breakout_inputs(direction, stage, interaction_type)
    if stage in {confirmed, retesting}:
        state["overlays"].append(confirmed)
    if stage == retesting:
        state["timeframes"]["15m"]["primary_state"] = "TREND_UP" if direction == "LONG" else "TREND_DOWN"
    return value, state


def _failed_break_case(direction: str, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    source_direction = "LONG" if direction == "SHORT" else "SHORT"
    value, state = breakout_inputs(source_direction)
    failed = "FAILED_BREAKOUT_CANDIDATE" if direction == "SHORT" else "FAILED_BREAKDOWN_CANDIDATE"
    reclaim = "RECLAIMED_INTO_PRIOR_RANGE" if direction == "SHORT" else "RECLAIMED_ABOVE_PRIOR_SUPPORT"
    state["level_interactions"][0]["current_stage"] = failed
    if seed % 3:
        state["level_interactions"][0]["reclaim_status"] = reclaim
    if seed % 3 == 2:
        state["timeframes"]["15m"]["primary_state"] = "TREND_DOWN" if direction == "SHORT" else "TREND_UP"
    if seed % 7 == 0:
        state["level_interactions"][0]["interaction_type"] = "BROKEN"
        state["level_interactions"][0]["reclaim_status"] = "NOT_RECLAIMED"
    return value, state


def make_case(index: int) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    scenario = index % 10
    seed = index // 10
    kwargs: dict[str, Any] = {}
    if scenario == 0:
        label, (value, state) = "TREND_PULLBACK_LONG", _trend_case("LONG", seed)
    elif scenario == 1:
        label, (value, state) = "TREND_PULLBACK_SHORT", _trend_case("SHORT", seed)
    elif scenario == 2:
        label, (value, state) = "MA200_MEAN_REVERSION_LONG", _ma_case("LONG", seed)
    elif scenario == 3:
        label, (value, state) = "MA200_MEAN_REVERSION_SHORT", _ma_case("SHORT", seed)
    elif scenario == 4:
        label, (value, state) = "BREAKOUT_CONTINUATION_LONG", _breakout_case("LONG", seed)
    elif scenario == 5:
        label, (value, state) = "BREAKOUT_CONTINUATION_SHORT", _breakout_case("SHORT", seed)
    elif scenario == 6:
        label, (value, state) = "FAILED_BREAKOUT_REVERSAL_SHORT", _failed_break_case("SHORT", seed)
    elif scenario == 7:
        label, (value, state) = "FAILED_BREAKOUT_REVERSAL_LONG", _failed_break_case("LONG", seed)
    else:
        directions = {"1W": "up", "1D": "down", "4H": "mixed", "1H": "up", "15m": "down"}
        value, state = inputs(directions, quality="PARTIAL" if scenario == 8 else "AVAILABLE")
        label = "NO_TRADE_PARTIAL_GAP" if scenario == 8 else "LEVEL_MOMENTUM_RECLAIM"
        if scenario == 8:
            state["overlays"].extend(("GAP_ACTIVE", "1H:VOLATILITY_COMPRESSION"))
            state["level_interactions"] = []
        else:
            state["primary_state_code"] = "MAJOR_SUPPORT_TEST" if seed % 2 else "MAJOR_RESISTANCE_TEST"
            state["level_interactions"] = [
                interaction(
                    ("SWING_LOW", "SWING_HIGH", "RANGE_LOW", "RANGE_HIGH")[seed % 4],
                    "FROM_ABOVE" if seed % 2 else "FROM_BELOW",
                    interaction_type=("APPROACHING", "TOUCHING", "RETESTING", "RECLAIMED")[seed % 4],
                    timeframe=("15m", "1H", "4H", "1D", "1W")[seed % 5],
                    boundary=90.0 + (seed % 41) * 0.5,
                )
            ]
            state["timeframes"]["15m"]["momentum_state"] = (
                "RECOVERING_FROM_OVERSOLD" if seed % 2 else "ROLLING_OVER_FROM_OVERBOUGHT"
            )
    state["router_equivalence_case"] = index
    return label, value, state, kwargs


def corpus(count: int) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    labels: dict[str, int] = {}
    for index in range(count):
        label, context, state, kwargs = make_case(index)
        route = ROUTER.route(context, state, **kwargs)
        canonical_payload = _canonical(route)
        labels[label] = labels.get(label, 0) + 1
        cases.append(
            {
                "index": index,
                "label": label,
                "canonical_payload": canonical_payload,
                "stable_hash": stable_hash(route),
            }
        )
    return {
        "schema": "router-v2-equivalence-corpus-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_count": count,
        "coverage": labels,
        "cases": cases,
    }


def verify_corpus(baseline: dict[str, Any]) -> dict[str, Any]:
    payload_matches = 0
    hash_matches = 0
    mismatches: list[dict[str, Any]] = []
    for expected in baseline["cases"]:
        label, context, state, kwargs = make_case(int(expected["index"]))
        route = ROUTER.route(context, state, **kwargs)
        payload_match = _canonical(route) == expected["canonical_payload"]
        hash_match = stable_hash(route) == expected["stable_hash"]
        payload_matches += int(payload_match)
        hash_matches += int(hash_match)
        if not payload_match or not hash_match:
            mismatches.append(
                {
                    "index": expected["index"],
                    "label": label,
                    "payload_match": payload_match,
                    "hash_match": hash_match,
                }
            )
    return {
        "schema": "router-v2-equivalence-result-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(baseline["cases"]),
        "payload_matches": payload_matches,
        "hash_matches": hash_matches,
        "mismatches": mismatches[:20],
    }


def _profile_stat(stats: pstats.Stats, predicate: Callable[[str, int, str], bool]) -> dict[str, Any]:
    matches = [
        (key, values)
        for key, values in stats.stats.items()
        if predicate(key[0], key[1], key[2])
    ]
    return {
        "primitive_calls": sum(values[0] for _, values in matches),
        "total_calls": sum(values[1] for _, values in matches),
        "self_seconds": sum(values[2] for _, values in matches),
        "cumulative_seconds": sum(values[3] for _, values in matches),
    }


def profile_routes(count: int) -> dict[str, Any]:
    value, state = ma_setup()
    profiler = cProfile.Profile()
    tracemalloc.start()
    started = time.perf_counter()
    profiler.enable()
    for _ in range(count):
        ROUTER.route(value, state)
    profiler.disable()
    elapsed = time.perf_counter() - started
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    snapshot = tracemalloc.take_snapshot()
    tracemalloc.stop()
    stats = pstats.Stats(profiler)
    selectors = {
        "route": lambda filename, line, name: filename.endswith("strategy_router_v2.py") and name == "route",
        "_future_timestamps": lambda filename, line, name: filename.endswith("strategy_router_v2.py") and name == "_future_timestamps",
        "stable_hash": lambda filename, line, name: filename.endswith("strategy_router_v2.py") and name == "stable_hash",
        "_canonical": lambda filename, line, name: filename.endswith("strategy_router_v2.py") and name == "_canonical",
        "typing.__instancecheck__": lambda filename, line, name: filename.endswith("typing.py") and name == "__instancecheck__",
        "dataclasses.asdict": lambda filename, line, name: filename.endswith("dataclasses.py") and name in {"asdict", "_asdict_inner"},
        "_evaluate": lambda filename, line, name: filename.endswith("strategy_router_v2.py") and name == "_evaluate",
    }
    primitive_calls = sum(values[0] for values in stats.stats.values())
    total_calls = sum(values[1] for values in stats.stats.values())
    top_allocations = [
        {
            "traceback": str(item.traceback),
            "size_bytes": item.size,
            "count": item.count,
        }
        for item in snapshot.statistics("traceback")[:20]
    ]
    return {
        "route_count": count,
        "elapsed_seconds": elapsed,
        "milliseconds_per_route": elapsed * 1000 / count,
        "primitive_function_calls": primitive_calls,
        "total_function_calls": total_calls,
        "tracemalloc_current_bytes": current_bytes,
        "tracemalloc_peak_bytes": peak_bytes,
        "functions": {name: _profile_stat(stats, selector) for name, selector in selectors.items()},
        "top_allocations": top_allocations,
    }


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_cpu_times() -> tuple[int, int, int]:
    idle = ctypes.c_ulonglong()
    kernel = ctypes.c_ulonglong()
    user = ctypes.c_ulonglong()
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        raise OSError("GetSystemTimes failed")
    return idle.value, kernel.value, user.value


def _windows_memory() -> dict[str, int]:
    value = _MemoryStatusEx()
    value.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        raise OSError("GlobalMemoryStatusEx failed")
    return {
        "load_percent": int(value.dwMemoryLoad),
        "available_physical_bytes": int(value.ullAvailPhys),
        "total_physical_bytes": int(value.ullTotalPhys),
        "pagefile_used_bytes": int(value.ullTotalPageFile - value.ullAvailPageFile),
        "pagefile_total_bytes": int(value.ullTotalPageFile),
    }


def _cpu_percent(before: tuple[int, int, int], after: tuple[int, int, int]) -> float:
    idle = after[0] - before[0]
    total = (after[1] - before[1]) + (after[2] - before[2])
    return 0.0 if total <= 0 else max(0.0, min(100.0, (1.0 - idle / total) * 100.0))


def _benchmark_measurement(context: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    cpu_before = _windows_cpu_times()
    memory_before = _windows_memory()
    started = time.perf_counter()
    for _ in range(200):
        ROUTER.route(context, state)
    elapsed = time.perf_counter() - started
    cpu_after = _windows_cpu_times()
    return {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "milliseconds_per_route": elapsed * 1000 / 200,
        "cpu_total_percent": _cpu_percent(cpu_before, cpu_after),
        "memory_before": memory_before,
        "memory_after": _windows_memory(),
    }


def benchmark_routes() -> dict[str, Any]:
    cold: list[dict[str, Any]] = []
    for _ in range(30):
        value, state = ma_setup()
        cold.append(_benchmark_measurement(value, state))
    value, state = ma_setup()
    for _ in range(200):
        ROUTER.route(value, state)
    warm = [_benchmark_measurement(value, state) for _ in range(30)]

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        values = [float(item["milliseconds_per_route"]) for item in items]
        return {
            "min": min(values),
            "median": statistics.median(values),
            "p95": statistics.quantiles(values, n=20, method="inclusive")[18],
            "max": max(values),
            "mean": statistics.mean(values),
            "all_below_25ms": all(value < 25 for value in values),
            "measurements": items,
        }

    return {
        "schema": "router-v2-sustained-benchmark-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "routes_per_measurement": 200,
        "cold": summarize(cold),
        "warm": summarize(warm),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    corpus_parser = subparsers.add_parser("corpus")
    corpus_parser.add_argument("--count", type=int, default=1000)
    corpus_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--baseline", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path, required=True)
    profile_parser = subparsers.add_parser("profile")
    profile_parser.add_argument("--output", type=Path, required=True)
    benchmark_parser = subparsers.add_parser("benchmark")
    benchmark_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "corpus":
        result = corpus(args.count)
        write_json(args.output, result)
        print(json.dumps({"case_count": result["case_count"], "coverage": result["coverage"]}, sort_keys=True))
    elif args.command == "verify":
        result = verify_corpus(json.loads(args.baseline.read_text(encoding="utf-8")))
        write_json(args.output, result)
        print(json.dumps(result, sort_keys=True))
        if result["payload_matches"] != result["case_count"] or result["hash_matches"] != result["case_count"]:
            raise SystemExit(1)
    elif args.command == "profile":
        result = {
            "schema": "router-v2-performance-profile-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profiles": [profile_routes(count) for count in (1, 200, 6000)],
        }
        write_json(args.output, result)
        print(
            json.dumps(
                {
                    "profiles": [
                        {
                            "route_count": item["route_count"],
                            "milliseconds_per_route": item["milliseconds_per_route"],
                            "total_function_calls": item["total_function_calls"],
                            "peak_bytes": item["tracemalloc_peak_bytes"],
                        }
                        for item in result["profiles"]
                    ]
                },
                sort_keys=True,
            )
        )
    else:
        result = benchmark_routes()
        write_json(args.output, result)
        print(
            json.dumps(
                {
                    group: {
                        key: result[group][key]
                        for key in ("min", "median", "p95", "max", "mean", "all_below_25ms")
                    }
                    for group in ("cold", "warm")
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
