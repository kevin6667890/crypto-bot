from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping

import pytest

from dashboard.strategy_router_v2 import _canonical, _future_timestamps, stable_hash


def reference_canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): reference_canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple, set)):
        values = [reference_canonical(item) for item in value]
        return sorted(values, key=lambda item: json.dumps(item, sort_keys=True)) if isinstance(value, set) else values
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("identity accepts finite values only")
        return float(format(value, ".12g"))
    return value


def reference_stable_hash(value: Any) -> str:
    payload = json.dumps(
        reference_canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reference_future_timestamps(value: Any, as_of: int, path: str = "input") -> list[str]:
    output: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if (
                key.endswith("timestamp")
                or key.endswith("_ts")
                or key in {"as_of", "source_timestamps", "source_candle_timestamps"}
            ) and item is not None:
                values = item if isinstance(item, (list, tuple)) else (item,)
                for timestamp in values:
                    try:
                        if int(timestamp) > as_of:
                            output.append(f"{child}={timestamp}")
                    except (TypeError, ValueError):
                        output.append(f"{child}=invalid")
            else:
                output.extend(reference_future_timestamps(item, as_of, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            output.extend(reference_future_timestamps(item, as_of, f"{path}[{index}]"))
    return output


@dataclass(frozen=True)
class FixtureDataclass:
    name: str
    value: int


class FixtureEnum(Enum):
    VALUE = "value"


CANONICAL_FIXTURES = (
    None,
    True,
    7,
    1.234567890123456,
    "value",
    {"b": [2, 1], "a": {"nested": 3}},
    OrderedDict((("b", 2), ("a", 1))),
    [3, {"b": 2, "a": 1}],
    (3, {"b": 2, "a": 1}),
    {3, 1, 2},
    frozenset({3, 1, 2}),
    FixtureDataclass("fixture", 1),
    FixtureEnum.VALUE,
    Decimal("1.25"),
    datetime(2026, 8, 10, tzinfo=timezone.utc),
)


def outcome(function, value):
    try:
        return "value", function(value)
    except Exception as exc:  # exact legacy failure behavior is part of equivalence
        return "error", (type(exc), str(exc))


@pytest.mark.parametrize("value", CANONICAL_FIXTURES)
def test_canonical_and_hash_match_original_implementation(value):
    assert outcome(_canonical, value) == outcome(reference_canonical, value)
    assert outcome(stable_hash, value) == outcome(reference_stable_hash, value)


def test_future_timestamp_fast_path_matches_original_error_details():
    value = OrderedDict(
        (
            ("as_of", 100),
            ("frames", [
                {"source_timestamp": 99, "nested": {"event_ts": 101}},
                {"source_timestamps": [98, 102]},
                {"confirmation_timestamp": "invalid"},
            ]),
        )
    )
    assert _future_timestamps(value, 100) == reference_future_timestamps(value, 100)


def test_future_timestamp_fast_path_matches_original_empty_result():
    value = {
        "as_of": 100,
        "frames": [
            {"source_timestamp": 99, "nested": {"event_ts": 98}},
            {"source_timestamps": [97, 100]},
        ],
    }
    assert _future_timestamps(value, 100) == reference_future_timestamps(value, 100) == []
