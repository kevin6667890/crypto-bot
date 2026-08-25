"""Small domain-neutral value helpers for the Polymarket research ledger."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json(value: Any) -> str:
    # Let CPython's C JSON encoder traverse large Gamma payloads. The former
    # recursive Python normalizer produced identical output but made a 140k
    # market universe take tens of CPU-minutes merely to hash.
    def encode_extra(item: Any) -> Any:
        if isinstance(item, Decimal):
            return format(item, "f")
        raise TypeError(f"unsupported canonical type: {type(item).__name__}")
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False, default=encode_extra)
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_json_field(value: Any, field: str) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} is not valid JSON") from exc
    return value


def decimal_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return format(result, "f")
