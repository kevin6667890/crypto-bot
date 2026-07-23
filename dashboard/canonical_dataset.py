"""Versioned semantic identity for immutable canonical OHLCV rows."""
from __future__ import annotations
import hashlib, json
from typing import Any

CANONICAL_OHLCV_SCHEMA_VERSION = "canonical-ohlcv-schema-v1"
CANONICAL_PARTITION_FINGERPRINT_VERSION = "canonical-partition-fingerprint-v1"
CANONICAL_DATASET_FINGERPRINT_VERSION = "canonical-dataset-fingerprint-v1"

def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def partition_fingerprint(rows: list[dict[str, Any]]) -> str:
    """Hash semantic OHLCV values only; row ids and SQLite layout are irrelevant."""
    candles = [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]))
               for r in sorted(rows, key=lambda r: int(r["ts"]))]
    return hashlib.sha256(_json({"version": CANONICAL_PARTITION_FINGERPRINT_VERSION, "candles": candles})).hexdigest()

def dataset_fingerprint(partitions: list[dict[str, Any]]) -> str:
    """Order-independent input, canonical instrument/timeframe ordering in identity."""
    normalized = sorted(({"instrument": str(p["instrument"]).upper(), "timeframe": str(p["timeframe"]),
                          "requested_start": int(p["requested_start"]), "requested_end": int(p["requested_end"]),
                          "partition_fingerprint": str(p["partition_fingerprint"])} for p in partitions),
                        key=lambda p: (p["instrument"], p["timeframe"]))
    return hashlib.sha256(_json({"schema_version": CANONICAL_OHLCV_SCHEMA_VERSION,
                                 "dataset_fingerprint_version": CANONICAL_DATASET_FINGERPRINT_VERSION,
                                 "partitions": normalized})).hexdigest()

def raw_semantic_hash(rows: list[dict[str, Any]]) -> str:
    return partition_fingerprint(rows)
