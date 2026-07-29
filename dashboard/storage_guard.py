"""Configured disk-budget protection that never blocks core ledgers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any


DISK_GUARD_VERSION = "storage-disk-guard-v1"
STATE_FILE_NAME = "storage_lifecycle_state.json"


def _percent(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _integer(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class DiskGuardConfig:
    warning_free_percent: float = _percent("STORAGE_WARNING_FREE_PERCENT", 20)
    critical_free_percent: float = _percent("STORAGE_CRITICAL_FREE_PERCENT", 12)
    emergency_free_percent: float = _percent("STORAGE_EMERGENCY_FREE_PERCENT", 5)
    emergency_free_bytes: int = _integer(
        "STORAGE_EMERGENCY_FREE_BYTES", 2 * 1024**3
    )
    warning_days_to_85: float = _percent("STORAGE_WARNING_DAYS_TO_85", 14)
    critical_days_to_90: float = _percent("STORAGE_CRITICAL_DAYS_TO_90", 7)


@dataclass(frozen=True)
class DiskGuardDecision:
    level: str
    core_ledger_allowed: bool
    core_aggregates_allowed: bool
    optional_artifacts_allowed: bool
    debug_samples_allowed: bool
    reasons: tuple[str, ...]


def evaluate_disk_guard(
    *,
    total_bytes: int,
    free_bytes: int,
    projected_days_to_85: float | None = None,
    projected_days_to_90: float | None = None,
    config: DiskGuardConfig | None = None,
) -> DiskGuardDecision:
    config = config or DiskGuardConfig()
    free_percent = free_bytes / total_bytes * 100 if total_bytes else 0
    reasons: list[str] = []
    level = "NORMAL"
    if (
        free_percent < config.emergency_free_percent
        or free_bytes < config.emergency_free_bytes
    ):
        level = "EMERGENCY"
        reasons.append("free space is below the emergency budget")
    elif (
        free_percent < config.critical_free_percent
        or (
            projected_days_to_90 is not None
            and projected_days_to_90 < config.critical_days_to_90
        )
    ):
        level = "CRITICAL"
        reasons.append("free space or days-to-90 is below the critical budget")
    elif (
        free_percent < config.warning_free_percent
        or (
            projected_days_to_85 is not None
            and projected_days_to_85 < config.warning_days_to_85
        )
    ):
        level = "WARNING"
        reasons.append("free space or days-to-85 is below the warning budget")
    return DiskGuardDecision(
        level=level,
        core_ledger_allowed=True,
        core_aggregates_allowed=True,
        optional_artifacts_allowed=level == "NORMAL",
        debug_samples_allowed=level == "NORMAL",
        reasons=tuple(reasons),
    )


def _safe_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _days_to(total: int, used: int, rate: float | None, percent: float) -> float | None:
    if rate is None or rate <= 0:
        return None
    remaining = total * percent / 100 - used
    return max(0.0, remaining / rate)


def storage_operations_summary(
    root: Path,
    paper_database: Path,
    microstructure_database: Path,
) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    state = _safe_state(root / "data_cache" / STATE_FILE_NAME)
    rates = state.get("growth_bytes_per_day")
    rates = rates if isinstance(rates, dict) else {}
    total_rate = rates.get("total")
    total_rate = float(total_rate) if isinstance(total_rate, (int, float)) else None
    days = {
        f"to_{threshold}_percent": _days_to(
            usage.total, usage.used, total_rate, threshold
        )
        for threshold in (80, 85, 90)
    }
    decision = evaluate_disk_guard(
        total_bytes=usage.total,
        free_bytes=usage.free,
        projected_days_to_85=days["to_85_percent"],
        projected_days_to_90=days["to_90_percent"],
    )
    history = state.get("growth_history")
    enough_history = isinstance(history, list) and len(history) >= 2
    return {
        "version": DISK_GUARD_VERSION,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "root": {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "usage_percent": round(usage.used / usage.total * 100, 2),
        },
        "paper_database_bytes": (
            paper_database.stat().st_size if paper_database.exists() else 0
        ),
        "microstructure_database_bytes": (
            microstructure_database.stat().st_size
            if microstructure_database.exists() else 0
        ),
        "snapshot_bytes_per_day": rates.get("snapshots"),
        "raw_trades_bytes_per_day": rates.get("raw_trades"),
        "projection": {
            "status": "AVAILABLE" if enough_history and total_rate else "INSUFFICIENT_HISTORY",
            "window": state.get("growth_window"),
            "low": state.get("projection_low"),
            "median": state.get("projection_median"),
            "high": state.get("projection_high"),
            **days,
        },
        "snapshot_mode": "INLINE_COMPACT_V2",
        "raw_retention_status": state.get("raw_retention_status", "NOT_STARTED"),
        "last_archive": state.get("last_archive"),
        "last_offhost_ack": state.get("last_offhost_ack"),
        "archive_backlog": state.get("archive_backlog"),
        "prune_backlog": state.get("prune_backlog"),
        "protection": {
            "level": decision.level,
            "reasons": list(decision.reasons),
            "core_ledger_allowed": decision.core_ledger_allowed,
            "core_aggregates_allowed": decision.core_aggregates_allowed,
            "optional_artifacts_allowed": decision.optional_artifacts_allowed,
            "debug_samples_allowed": decision.debug_samples_allowed,
        },
    }
