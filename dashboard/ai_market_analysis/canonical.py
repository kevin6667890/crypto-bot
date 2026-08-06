from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .versions import AI_CANONICAL_IDENTITY_VERSION


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def identity(prefix: str, payload: Any) -> str:
    return f"{prefix}_{stable_hash({'identity_version': AI_CANONICAL_IDENTITY_VERSION, 'payload': payload})}"
