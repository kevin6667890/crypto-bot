"""Stable identities for strategy configurations and decision signals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any


def canonical_json(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def config_hash(parameters: Any) -> str:
    return hashlib.sha256(canonical_json(parameters).encode("utf-8")).hexdigest()


def signal_id(strategy_version: str, configuration_hash: str, instrument: str, execution_timeframe: str, candle_close_ts: int) -> str:
    payload = "\n".join((strategy_version, configuration_hash, instrument, execution_timeframe, str(int(candle_close_ts))))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def signal_setup_id(strategy_version: str, configuration_hash: str, instrument: str, execution_timeframe: str, candle_close_ts: int) -> str:
    """Stable setup identity.  Deliberately excludes live flow observations."""
    return signal_id(strategy_version, configuration_hash, instrument, execution_timeframe, candle_close_ts)


def evaluation_id(setup_id: str, evaluation: object) -> str:
    """Immutable identity for one decision evaluation and its mutable evidence."""
    return hashlib.sha256((setup_id + "\n" + canonical_json(evaluation)).encode("utf-8")).hexdigest()

