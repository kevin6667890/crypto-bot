"""Point-in-time rolling price-structure features for Thesis V2.

These features intentionally do *not* reuse the MarketStateEngineV2 names.
MarketState levels have ATR-buffer, touch, expansion, and provenance gates;
an N-bar rolling extreme is a different, simpler public contract.

The compiler is a pure ordered scan.  It performs no I/O and never reads a
candle after the row whose feature values it is producing.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence


ROLLING_STRUCTURE_FEATURE_VERSION = "rolling-structure-features-v1"
ROLLING_STRUCTURE_CONTEXT_VERSION = "rolling-structure-event-context-v1"
LOOKBACK_BARS_MIN = 5
LOOKBACK_BARS_MAX = 500
FAILURE_WINDOW_BARS_MIN = 1
FAILURE_WINDOW_BARS_MAX = 20

ROLLING_HIGH_BREAKOUT_CONFIRMED = "ROLLING_HIGH_BREAKOUT_CONFIRMED"
ROLLING_LOW_BREAKDOWN_CONFIRMED = "ROLLING_LOW_BREAKDOWN_CONFIRMED"
FAILED_BREAKOUT_CONFIRMED = "FAILED_BREAKOUT_CONFIRMED"
FAILED_BREAKDOWN_CONFIRMED = "FAILED_BREAKDOWN_CONFIRMED"

FEATURE_ROW_KEYS = {
    ROLLING_HIGH_BREAKOUT_CONFIRMED: "rolling_high_breakout_confirmed",
    ROLLING_LOW_BREAKDOWN_CONFIRMED: "rolling_low_breakdown_confirmed",
    FAILED_BREAKOUT_CONFIRMED: "failed_breakout_confirmed",
    FAILED_BREAKDOWN_CONFIRMED: "failed_breakdown_confirmed",
}

# Registry metadata is data-only so the AST/compiler can expose the same
# bounds without importing or duplicating the implementation.
FEATURE_PARAMETER_SCHEMAS: Mapping[str, Mapping[str, Mapping[str, Any]]] = {
    ROLLING_HIGH_BREAKOUT_CONFIRMED: {
        "lookback_bars": {"type": "integer", "minimum": LOOKBACK_BARS_MIN,
                          "maximum": LOOKBACK_BARS_MAX, "required": True},
    },
    ROLLING_LOW_BREAKDOWN_CONFIRMED: {
        "lookback_bars": {"type": "integer", "minimum": LOOKBACK_BARS_MIN,
                          "maximum": LOOKBACK_BARS_MAX, "required": True},
    },
    FAILED_BREAKOUT_CONFIRMED: {
        "lookback_bars": {"type": "integer", "minimum": LOOKBACK_BARS_MIN,
                          "maximum": LOOKBACK_BARS_MAX, "required": True},
        "failure_window_bars": {"type": "integer", "required": True, "minimum": FAILURE_WINDOW_BARS_MIN,
                                "maximum": FAILURE_WINDOW_BARS_MAX, "default": 3},
    },
    FAILED_BREAKDOWN_CONFIRMED: {
        "lookback_bars": {"type": "integer", "minimum": LOOKBACK_BARS_MIN,
                          "maximum": LOOKBACK_BARS_MAX, "required": True},
        "failure_window_bars": {"type": "integer", "required": True, "minimum": FAILURE_WINDOW_BARS_MIN,
                                "maximum": FAILURE_WINDOW_BARS_MAX, "default": 3},
    },
}

# The existing engine cannot be replayed faithfully from OHLCV alone: its
# canonical levels and indicator-quality facts are additional point-in-time
# inputs.  Keep this machine-readable for capabilities/readiness projection.
CANONICAL_LEVEL_FEATURES_STATUS = {
    "status": "DEFERRED",
    "reason": "CANONICAL_LEVEL_POINT_IN_TIME_HISTORY_UNAVAILABLE",
    "required_adapter": "MarketStateHistoricalBatchAdapterV1",
    "market_state_definition_version": "market-state-definitions-v2.1",
}


class RollingStructureHistoricalBatchAdapterV1:
    """Small integration seam for the Thesis V2 feature-row compiler."""

    version = ROLLING_STRUCTURE_FEATURE_VERSION
    feature_codes = tuple(FEATURE_ROW_KEYS)

    def compile(
        self,
        rows: Sequence[Mapping[str, Any]],
        parameters: RollingStructureParametersV1 | Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        return compile_rolling_structure_rows(rows, parameters)

    @staticmethod
    def value(feature: str, row: Mapping[str, Any]) -> bool | None:
        return feature_value(feature, row)


class RollingStructureValidationError(ValueError):
    """A stable validation failure for rolling-structure parameters/input."""


@dataclass(frozen=True)
class RollingStructureParametersV1:
    lookback_bars: int
    failure_window_bars: int = 3
    version: str = ROLLING_STRUCTURE_FEATURE_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RollingStructureParametersV1":
        if not isinstance(payload, Mapping):
            raise RollingStructureValidationError("parameters must be an object")
        allowed = {"lookback_bars", "failure_window_bars"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise RollingStructureValidationError(
                f"unsupported rolling-structure parameter: {unknown[0]}"
            )
        if "lookback_bars" not in payload:
            raise RollingStructureValidationError("lookback_bars is required")
        lookback = _bounded_integer(
            payload["lookback_bars"], "lookback_bars", LOOKBACK_BARS_MIN, LOOKBACK_BARS_MAX
        )
        failure_window = _bounded_integer(
            payload.get("failure_window_bars", 3),
            "failure_window_bars",
            FAILURE_WINDOW_BARS_MIN,
            FAILURE_WINDOW_BARS_MAX,
        )
        return cls(lookback, failure_window)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "lookback_bars": self.lookback_bars,
            "failure_window_bars": self.failure_window_bars,
        }


@dataclass(frozen=True)
class _PendingBreak:
    index: int
    timestamp: int
    reference_level: float
    reference_start_timestamp: int
    reference_end_timestamp: int


def compile_rolling_structure_rows(
    rows: Sequence[Mapping[str, Any]],
    parameters: RollingStructureParametersV1 | Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return copies of ``rows`` augmented with four price-structure features.

    Reference levels use only the previous ``lookback_bars`` confirmed
    candles.  A wick through a reference is not confirmation: only the current
    confirmed close is compared.  Failed events are timestamped on the later
    failure-confirmation row, never on the original break row.
    """
    params = (parameters if isinstance(parameters, RollingStructureParametersV1)
              else RollingStructureParametersV1.from_mapping(parameters))
    _validate_rows(rows)
    output: list[dict[str, Any]] = []
    high_window: deque[tuple[int, float]] = deque()
    low_window: deque[tuple[int, float]] = deque()
    pending_breakouts: list[_PendingBreak] = []
    pending_breakdowns: list[_PendingBreak] = []

    for index, source in enumerate(rows):
        row = dict(source)
        close = float(row["close"])
        close_ts = int(row["candle_close_ts"])
        window_start = index - params.lookback_bars

        while high_window and high_window[0][0] < window_start:
            high_window.popleft()
        while low_window and low_window[0][0] < window_start:
            low_window.popleft()

        warmed = index >= params.lookback_bars
        high_reference = high_window[0][1] if warmed else None
        low_reference = low_window[0][1] if warmed else None
        breakout = close > high_reference if high_reference is not None else None
        breakdown = close < low_reference if low_reference is not None else None

        pending_breakouts = [
            item for item in pending_breakouts
            if index - item.index <= params.failure_window_bars
        ]
        pending_breakdowns = [
            item for item in pending_breakdowns
            if index - item.index <= params.failure_window_bars
        ]
        failed_up = [item for item in pending_breakouts if close < item.reference_level]
        failed_down = [item for item in pending_breakdowns if close > item.reference_level]
        failed_breakout = bool(failed_up) if warmed else None
        failed_breakdown = bool(failed_down) if warmed else None

        contexts: dict[str, dict[str, Any]] = {}
        if breakout:
            contexts[ROLLING_HIGH_BREAKOUT_CONFIRMED] = _break_context(
                ROLLING_HIGH_BREAKOUT_CONFIRMED, close_ts, high_reference,
                rows[index - params.lookback_bars], rows[index - 1], params,
            )
        if breakdown:
            contexts[ROLLING_LOW_BREAKDOWN_CONFIRMED] = _break_context(
                ROLLING_LOW_BREAKDOWN_CONFIRMED, close_ts, low_reference,
                rows[index - params.lookback_bars], rows[index - 1], params,
            )
        if failed_up:
            # One candle may invalidate overlapping candidates.  The most
            # recent qualifying break is the deterministic chart anchor.
            anchor = max(failed_up, key=lambda item: item.index)
            contexts[FAILED_BREAKOUT_CONFIRMED] = _failure_context(
                FAILED_BREAKOUT_CONFIRMED, close_ts, anchor, params
            )
            pending_breakouts = [item for item in pending_breakouts if item not in failed_up]
        if failed_down:
            anchor = max(failed_down, key=lambda item: item.index)
            contexts[FAILED_BREAKDOWN_CONFIRMED] = _failure_context(
                FAILED_BREAKDOWN_CONFIRMED, close_ts, anchor, params
            )
            pending_breakdowns = [item for item in pending_breakdowns if item not in failed_down]

        row.update({
            "rolling_high_reference": high_reference,
            "rolling_low_reference": low_reference,
            FEATURE_ROW_KEYS[ROLLING_HIGH_BREAKOUT_CONFIRMED]: breakout,
            FEATURE_ROW_KEYS[ROLLING_LOW_BREAKDOWN_CONFIRMED]: breakdown,
            FEATURE_ROW_KEYS[FAILED_BREAKOUT_CONFIRMED]: failed_breakout,
            FEATURE_ROW_KEYS[FAILED_BREAKDOWN_CONFIRMED]: failed_breakdown,
            "rolling_structure_event_contexts": contexts,
        })
        output.append(row)

        if breakout:
            pending_breakouts.append(_PendingBreak(
                index, close_ts, float(high_reference),
                int(rows[index - params.lookback_bars]["candle_close_ts"]),
                int(rows[index - 1]["candle_close_ts"]),
            ))
        if breakdown:
            pending_breakdowns.append(_PendingBreak(
                index, close_ts, float(low_reference),
                int(rows[index - params.lookback_bars]["candle_close_ts"]),
                int(rows[index - 1]["candle_close_ts"]),
            ))

        while high_window and high_window[-1][1] <= float(row["high"]):
            high_window.pop()
        high_window.append((index, float(row["high"])))
        while low_window and low_window[-1][1] >= float(row["low"]):
            low_window.pop()
        low_window.append((index, float(row["low"])))
    return output


def feature_value(feature: str, row: Mapping[str, Any]) -> bool | None:
    """Closed lookup used by registries without accepting arbitrary row keys."""
    try:
        value = row.get(FEATURE_ROW_KEYS[feature])
    except KeyError as error:
        raise RollingStructureValidationError(f"unsupported rolling-structure feature: {feature}") from error
    if value is not None and not isinstance(value, bool):
        raise RollingStructureValidationError(f"compiled {feature} value must be boolean or null")
    return value


def _bounded_integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RollingStructureValidationError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise RollingStructureValidationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _validate_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    previous_timestamp: int | None = None
    for row in rows:
        required = {"candle_close_ts", "high", "low", "close", "confirmed"}
        if not isinstance(row, Mapping) or not required.issubset(row):
            raise RollingStructureValidationError("source row is missing confirmed HLC fields")
        if row["confirmed"] is not True and row["confirmed"] != 1:
            raise RollingStructureValidationError("rolling structure requires confirmed candles")
        timestamp = row["candle_close_ts"]
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise RollingStructureValidationError("candle_close_ts must be an integer")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise RollingStructureValidationError("candles must be strictly ordered and unique")
        previous_timestamp = timestamp
        try:
            high, low, close = (float(row[key]) for key in ("high", "low", "close"))
        except (TypeError, ValueError, OverflowError) as error:
            raise RollingStructureValidationError("HLC values must be finite numbers") from error
        if not all(math.isfinite(item) for item in (high, low, close)):
            raise RollingStructureValidationError("HLC values must be finite numbers")
        if low <= 0 or high < low or not low <= close <= high:
            raise RollingStructureValidationError("source candle HLC geometry is invalid")


def _break_context(feature: str, timestamp: int, reference: float,
                   reference_start: Mapping[str, Any], reference_end: Mapping[str, Any],
                   params: RollingStructureParametersV1) -> dict[str, Any]:
    return {
        "version": ROLLING_STRUCTURE_CONTEXT_VERSION,
        "feature": feature,
        "event_timestamp": timestamp,
        "original_breakout_timestamp": timestamp,
        "break_timestamp": timestamp,
        "failure_confirmation_timestamp": None,
        "reference_level": float(reference),
        "reference_window_start_timestamp": int(reference_start["candle_close_ts"]),
        "reference_window_end_timestamp": int(reference_end["candle_close_ts"]),
        "parameters": {"version": params.version, "lookback_bars": params.lookback_bars},
    }


def _failure_context(feature: str, timestamp: int, anchor: _PendingBreak,
                     params: RollingStructureParametersV1) -> dict[str, Any]:
    return {
        "version": ROLLING_STRUCTURE_CONTEXT_VERSION,
        "feature": feature,
        "event_timestamp": timestamp,
        "original_breakout_timestamp": anchor.timestamp,
        "break_timestamp": anchor.timestamp,
        "failure_confirmation_timestamp": timestamp,
        "reference_level": anchor.reference_level,
        "reference_window_start_timestamp": anchor.reference_start_timestamp,
        "reference_window_end_timestamp": anchor.reference_end_timestamp,
        "parameters": params.to_dict(),
    }
