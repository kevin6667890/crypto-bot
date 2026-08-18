"""Canonical semantic roles for numeric report evidence."""
from __future__ import annotations

from typing import Any

SIGNED_FIELDS = {
    "price_change", "price_change_pct", "cvd_delta", "oi_change", "oi_change_pct",
    "funding", "funding_rate", "basis", "basis_pct", "percentage_basis", "slope",
}
VOLUME_FIELDS = {"volume", "buy_notional", "sell_notional", "long_liquidation", "short_liquidation"}
PRICE_FIELDS = {
    "price", "close", "open", "high", "low", "representative_price", "zone_low", "zone_high",
    "range_low", "range_high", "impulse_high", "impulse_low", "average_cost", "original_stop",
}

FIELD_NAMESPACES = {
    "price_change": "PRICE_CHANGE", "price_change_pct": "PRICE_CHANGE",
    "cvd_delta": "FLOW_CVD", "oi_change": "FLOW_OI", "oi_change_pct": "FLOW_OI",
    "volume": "VOLUME", "buy_notional": "FLOW_CVD", "sell_notional": "FLOW_CVD",
    "funding": "FLOW_FUNDING", "funding_rate": "FLOW_FUNDING",
    "basis": "FLOW_BASIS", "basis_pct": "FLOW_BASIS", "percentage_basis": "FLOW_BASIS",
    "long_liquidation": "FLOW_LIQUIDATION", "short_liquidation": "FLOW_LIQUIDATION",
}


def numeric_semantics(source_fact_id: str, field: str | None, unit: str | None) -> dict[str, str]:
    """Return the deterministic role and namespace for one numeric registry item."""
    key = str(field or "").lower()
    fact_id = str(source_fact_id)
    namespace = FIELD_NAMESPACES.get(key)
    if key in SIGNED_FIELDS:
        role = "SIGNED_DELTA"
    elif key in VOLUME_FIELDS:
        role = "ABSOLUTE_VALUE"
    elif fact_id.startswith(("STRUCT_", "LEVEL_")):
        role, namespace = "THRESHOLD_RELATION", namespace or "PRICE_LEVEL"
    elif key in PRICE_FIELDS or unit == "USDT":
        role, namespace = "ABSOLUTE_VALUE", namespace or "PRICE_LEVEL"
    else:
        role = "ABSOLUTE_VALUE"
    return {"semantic_field": key or fact_id, "semantic_role": role,
            "semantic_namespace": namespace or "GENERIC_NUMERIC"}


def find_numeric_field(value: Any, target: float, field: str = "") -> str | None:
    """Resolve legacy registry entries to their field in the frozen fact value."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return field if float(value) == float(target) else None
    if isinstance(value, dict):
        for key, nested in value.items():
            match = find_numeric_field(nested, target, str(key))
            if match:
                return match
    elif isinstance(value, list):
        for nested in value:
            match = find_numeric_field(nested, target, field)
            if match:
                return match
    return None
