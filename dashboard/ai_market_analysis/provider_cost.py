"""Deterministic DeepSeek price calculation for the AI-6B B3 canary.

Amounts are serialized as decimal strings.  Financial totals never pass
through binary floating point.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_UP
from typing import Any


PRICE_VERSION = "deepseek-official-2026-08-14T05:04:04Z"
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
PER_MILLION = Decimal("1000000")

_PRICES = {
    "deepseek-v4-flash": {
        "cache_hit_input": Decimal("0.0028"),
        "cache_miss_input": Decimal("0.14"),
        "output": Decimal("0.28"),
    },
    "deepseek-v4-pro": {
        "cache_hit_input": Decimal("0.003625"),
        "cache_miss_input": Decimal("0.435"),
        "output": Decimal("0.87"),
    },
}


@dataclass(frozen=True)
class ProviderCost:
    estimated_input_cost: Decimal
    estimated_output_cost: Decimal
    estimated_total_cost: Decimal
    currency: str
    pricing_source_version: str
    cache_status_used: str

    def as_dict(self) -> dict[str, str]:
        return {
            "estimated_input_cost": _money(self.estimated_input_cost),
            "estimated_output_cost": _money(self.estimated_output_cost),
            "estimated_total_cost": _money(self.estimated_total_cost),
            "currency": self.currency,
            "pricing_source_version": self.pricing_source_version,
            "cache_status_used": self.cache_status_used,
        }


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000000000001"), rounding=ROUND_UP), "f")


def estimate_provider_cost(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_status: str,
    official_price_version: str,
    cache_hit_tokens: int | None = None,
    cache_miss_tokens: int | None = None,
) -> ProviderCost:
    """Return an exact upper-bound estimate using an audited price schedule.

    UNKNOWN is deliberately costed as all cache-miss. MIXED requires an exact
    hit/miss split, and is intended for post-response reconciliation.
    """
    if official_price_version != PRICE_VERSION:
        raise ValueError("UNKNOWN_PRICE_VERSION")
    if model not in _PRICES:
        raise ValueError("UNPRICED_MODEL")
    if isinstance(input_tokens, bool) or isinstance(output_tokens, bool):
        raise TypeError("TOKEN_COUNT_MUST_BE_INTEGER")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("NEGATIVE_TOKEN_COUNT")
    status = cache_status.upper()
    prices = _PRICES[model]
    if status == "MIXED":
        if cache_hit_tokens is None or cache_miss_tokens is None:
            raise ValueError("CACHE_SPLIT_REQUIRED")
        if cache_hit_tokens < 0 or cache_miss_tokens < 0:
            raise ValueError("NEGATIVE_TOKEN_COUNT")
        if cache_hit_tokens + cache_miss_tokens != input_tokens:
            raise ValueError("CACHE_SPLIT_MISMATCH")
        input_cost = (
            Decimal(cache_hit_tokens) * prices["cache_hit_input"]
            + Decimal(cache_miss_tokens) * prices["cache_miss_input"]
        ) / PER_MILLION
    else:
        if status not in {"HIT", "MISS", "UNKNOWN"}:
            raise ValueError("INVALID_CACHE_STATUS")
        price_key = "cache_hit_input" if status == "HIT" else "cache_miss_input"
        input_cost = Decimal(input_tokens) * prices[price_key] / PER_MILLION
    output_cost = Decimal(output_tokens) * prices["output"] / PER_MILLION
    return ProviderCost(
        estimated_input_cost=input_cost,
        estimated_output_cost=output_cost,
        estimated_total_cost=input_cost + output_cost,
        currency="USD",
        pricing_source_version=official_price_version,
        cache_status_used=status,
    )


def reconcile_provider_usage(
    *,
    predicted_input_tokens: int,
    predicted_output_tokens: int,
    predicted_cost: ProviderCost,
    model: str,
    provider_usage: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep predictions immutable and represent absent provider fields as UNKNOWN."""
    usage = provider_usage if isinstance(provider_usage, dict) else {}
    input_tokens = usage.get("prompt_tokens", "UNKNOWN")
    output_tokens = usage.get("completion_tokens", "UNKNOWN")
    hit = usage.get("prompt_cache_hit_tokens", "UNKNOWN")
    miss = usage.get("prompt_cache_miss_tokens", "UNKNOWN")
    reconciled: str = "UNKNOWN"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in (input_tokens, output_tokens)):
        if isinstance(hit, int) and isinstance(miss, int) and hit + miss == input_tokens:
            cache_status = "MIXED"
            kwargs = {"cache_hit_tokens": hit, "cache_miss_tokens": miss}
        else:
            # Missing cache accounting cannot be guessed, so no reconciled cost.
            cache_status = None
            kwargs = {}
        if cache_status:
            reconciled = estimate_provider_cost(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_status=cache_status,
                official_price_version=predicted_cost.pricing_source_version,
                **kwargs,
            ).as_dict()["estimated_total_cost"]
    return {
        "usage_schema_version": "ai6b-provider-usage-v1",
        "predicted_input_tokens": predicted_input_tokens,
        "predicted_output_tokens": predicted_output_tokens,
        "predicted_cost": predicted_cost.as_dict()["estimated_total_cost"],
        "provider_input_tokens": input_tokens,
        "provider_output_tokens": output_tokens,
        "provider_reported_usage": provider_usage if provider_usage is not None else "UNKNOWN",
        "provider_cache_hit_tokens": hit,
        "provider_cache_miss_tokens": miss,
        "reconciled_cost": reconciled,
        "currency": "USD",
        "pricing_source_version": predicted_cost.pricing_source_version,
    }
