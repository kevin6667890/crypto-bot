"""Queue-pressure telemetry and hysteresis for the live collector."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PressureThresholds:
    warning_depth: int = 200
    warning_age_ms: int = 1_000
    high_depth: int = 5_000
    high_age_ms: int = 5_000
    emergency_depth: int = 18_000
    emergency_age_ms: int = 30_000
    recovery_samples: int = 3


class PressureHysteresis:
    """Classify pressure without turning one market burst into an emergency."""

    def __init__(self, thresholds: PressureThresholds | None = None) -> None:
        self.thresholds = thresholds or PressureThresholds()
        self.state = "NORMAL"
        self._clear_samples = 0

    def observe(self, depth: int, oldest_age_ms: int) -> str:
        values = self.thresholds
        if depth >= values.high_depth or oldest_age_ms >= values.high_age_ms:
            self.state = "HIGH_PRESSURE"
            self._clear_samples = 0
        elif depth >= values.warning_depth or oldest_age_ms >= values.warning_age_ms:
            self.state = "PRESSURE"
            self._clear_samples = 0
        elif self.state != "NORMAL":
            self._clear_samples += 1
            self.state = (
                "NORMAL" if self._clear_samples >= values.recovery_samples
                else "RECOVERING"
            )
        else:
            self._clear_samples = 0
        return self.state


def sustained_emergency(samples: list[dict[str, int | bool]], *,
                        thresholds: PressureThresholds | None = None) -> bool:
    """Require sustained capacity, age and raw-stall evidence before stopping."""
    values = thresholds or PressureThresholds()
    recent = samples[-3:]
    if len(recent) < 3:
        return False
    depths = [int(item["depth"]) for item in recent]
    ages = [int(item["oldest_age_ms"]) for item in recent]
    return (
        all(depth >= values.emergency_depth for depth in depths)
        and all(age >= values.emergency_age_ms for age in ages)
        and ages == sorted(ages)
        and all(bool(item.get("raw_stalled")) for item in recent)
    )
