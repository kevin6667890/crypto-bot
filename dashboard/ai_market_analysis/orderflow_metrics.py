"""Gap-aware deterministic metrics for price and canonical microstructure series."""
from __future__ import annotations

from math import sqrt
from typing import Any

from .canonical import stable_hash
from .flow_coverage import classify_flow_coverage
from .versions import AI_ORDERFLOW_METRICS_VERSION

METRIC_THRESHOLDS = {"price_flat_pct": .001, "price_flat_atr": .15, "oi_flat_pct": .002,
                     "watermark_tolerance_seconds": 900, "minimum_trade_count": 10}
PRICE_OI_QUADRANTS = ("PRICE_UP_OI_UP", "PRICE_UP_OI_DOWN", "PRICE_DOWN_OI_UP",
                      "PRICE_DOWN_OI_DOWN", "PRICE_FLAT_OI_UP", "PRICE_FLAT_OI_DOWN",
                      "PRICE_UP_OI_FLAT", "PRICE_DOWN_OI_FLAT", "INSUFFICIENT_DATA")


def _ts(row: dict[str, Any]) -> int:
    value = int(row.get("timestamp", row.get("bucket_timestamp", row.get("bucket_ms", row.get("ts", 0)))))
    return value//1000 if value > 10_000_000_000 else value


def _bounded(rows: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    return sorted((r for r in rows if start <= _ts(r) < end), key=lambda r: (_ts(r), stable_hash(r)))


def _slope(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    n = len(values); xm = (n-1)/2; ym = sum(values)/n
    denominator = sum((i-xm)**2 for i in range(n))
    return sum((i-xm)*(v-ym) for i, v in enumerate(values))/denominator if denominator else None


def _gaps(rows: list[dict[str, Any]], expected: int) -> tuple[list[int], int]:
    diffs = [b-a for a, b in zip([_ts(r) for r in rows], [_ts(r) for r in rows][1:])]
    gaps = [d for d in diffs if d > expected*1.5]
    return gaps, max(gaps, default=0)


def compute_phase_metrics(window: dict[str, Any], price_rows: list[dict[str, Any]],
                          sources: dict[str, list[dict[str, Any]]], *, atr: float | None = None,
                          bucket_seconds: int = 900) -> dict[str, Any]:
    start, end = window["start"], window["end"]
    prices = _bounded(price_rows, start, end)
    cvd = _bounded(sources.get("cvd", []), start, end)
    oi = _bounded(sources.get("oi", []), start, end)
    all_funding = sorted(sources.get("funding", []),key=lambda r:(_ts(r),stable_hash(r)))
    funding = _bounded(all_funding, start, end)
    basis = _bounded(sources.get("basis", []), start, end)
    liquidations = _bounded(sources.get("liquidation", []), start, end)
    p0 = float(prices[0].get("open", prices[0].get("close"))) if prices else None
    p1 = float(prices[-1]["close"]) if prices else None
    price_change = p1-p0 if p0 is not None and p1 is not None else None
    price_pct = price_change/p0 if price_change is not None and p0 else None
    volumes = [float(r.get("volume", 0)) for r in prices]
    cvd_gaps, cvd_largest = _gaps(cvd, bucket_seconds)
    oi_gaps, oi_largest = _gaps(oi, bucket_seconds)
    cvd_explicit_gap = any(r.get("gap") or r.get("gap_flag") or r.get("status") in {"GAP", "MISSING", "PARTIAL_AFTER_GAP"} for r in cvd)
    oi_explicit_gap = any(r.get("gap") or r.get("gap_flag") or r.get("status") in {"GAP", "MISSING"} for r in oi)
    cvd_gap = bool(cvd_gaps or cvd_explicit_gap)
    oi_gap = bool(oi_gaps or oi_explicit_gap)
    deltas = [float(r.get("signed_delta", r.get("delta", 0))) for r in cvd if r.get("status") not in {"GAP", "MISSING"}]
    cumulative = [float(r["cumulative"]) for r in cvd if r.get("cumulative") is not None]
    oi_values = [float(r.get("close", r.get("last_value", r.get("value", r.get("confirmed_oi")))))
                 for r in oi if r.get("close", r.get("last_value", r.get("value", r.get("confirmed_oi")))) is not None]
    oi_change = oi_values[-1]-oi_values[0] if len(oi_values) >= 2 and not oi_gap else None
    oi_pct = oi_change/oi_values[0] if oi_change is not None and oi_values[0] else None
    cvd_delta = sum(deltas) if deltas and not cvd_gap else None
    if not deltas and len(cumulative) >= 2 and not cvd_gap:
        cvd_delta = cumulative[-1]-cumulative[0]
    settled = [r for r in funding if r.get("state") == "SETTLED" or r.get("source_type") == "SETTLED"]
    predicted = [r for r in funding if r.get("state") == "PREDICTED" or r.get("source_type") == "PREDICTED"]
    settled_before=[r for r in all_funding if _ts(r)<start and (r.get("state")=="SETTLED" or r.get("source_type")=="SETTLED")]
    settled_after=[r for r in all_funding if _ts(r)>=end and (r.get("state")=="SETTLED" or r.get("source_type")=="SETTLED")]
    basis_gap = bool(_gaps(basis, bucket_seconds)[0] or any(r.get("gap") or r.get("status") in {"GAP", "MISSING"} for r in basis))
    basis_values = [float(r.get("percentage_basis", r.get("basis_pct", 0))) for r in basis]
    long_liq = sum(float(r.get("notional", 0)) for r in liquidations if str(r.get("side", "")).upper() == "LONG")
    short_liq = sum(float(r.get("notional", 0)) for r in liquidations if str(r.get("side", "")).upper() == "SHORT")
    trade_count = sum(int(r.get("trade_count", r.get("source_rows", 0))) for r in cvd)
    watermarks = [_ts(rows[-1]) for rows in (cvd, oi) if rows]
    watermark_mismatch = len(watermarks) == 2 and abs(watermarks[0]-watermarks[1]) > METRIC_THRESHOLDS["watermark_tolerance_seconds"]
    continuity = "PARTIAL" if cvd_gap or oi_gap or basis_gap else "VALID" if cvd and oi else "UNAVAILABLE"
    flow_coverage = classify_flow_coverage(
        snapshot_start=start, snapshot_end=end, bucket_seconds=bucket_seconds,
        timestamps=[_ts(r) for r in cvd if r.get("status") not in {"GAP", "MISSING"}],
        explicit_gap_timestamps=[_ts(r) for r in cvd if r.get("gap") or r.get("gap_flag") or r.get("status") in {"GAP", "MISSING", "PARTIAL_AFTER_GAP"}],
    )
    volume_ratio = (sum(volumes)/len(volumes))/(sum(float(r.get("volume", 0)) for r in price_rows[-20:])/max(1, len(price_rows[-20:]))) if volumes and price_rows else None
    return {"version": AI_ORDERFLOW_METRICS_VERSION, "price_start": p0, "price_end": p1,
            "price_change": price_change, "price_change_pct": price_pct, "atr": atr,
            "volume": sum(volumes) if volumes else None, "volume_ratio": volume_ratio,
            "volume_regime": "EXPANDING" if volume_ratio is not None and volume_ratio >= 1.2 else "CONTRACTING" if volume_ratio is not None and volume_ratio < .8 else "NORMAL",
            "cvd": {"valid": cvd_delta is not None, "status": "PARTIAL" if cvd_gap else "VALID" if cvd else "UNAVAILABLE",
                    "start_cumulative": cumulative[0] if cumulative else None, "end_cumulative": cumulative[-1] if cumulative else None,
                    "signed_delta": cvd_delta, "positive_buckets": sum(x > 0 for x in deltas),
                    "negative_buckets": sum(x < 0 for x in deltas), "buy_notional": sum(float(r.get("buy_notional", 0)) for r in cvd),
                    "sell_notional": sum(float(r.get("sell_notional", 0)) for r in cvd), "trade_count": trade_count,
                    "slope": _slope(cumulative or deltas), "normalized_delta": cvd_delta/(sum(abs(x) for x in deltas) or 1) if cvd_delta is not None else None,
                    "divergence": _divergence(price_change, cvd_delta), "extreme_bucket": _extreme(cvd, deltas),
                    "continuity_segment": [_ts(r) for r in cvd], "source_bucket_timestamps": [_ts(r) for r in cvd]},
            "oi": {"status": "PARTIAL" if oi_gap else "VALID" if oi else "UNAVAILABLE", "unit": _oi_unit(oi),
                   "start": oi_values[0] if oi_values else None, "end": oi_values[-1] if oi_values else None,
                   "absolute_change": oi_change, "percentage_change": oi_pct,
                   "min": min(oi_values) if oi_values else None, "max": max(oi_values) if oi_values else None,
                   "slope": _slope(oi_values), "acceleration": _acceleration(oi_values),
                   "peak_drawdown": _drawdown(oi_values), "observation_count": len(oi_values), "gap": oi_gap},
            "funding": {"last_settled_before_phase": settled_before[-1].get("rate",settled_before[-1].get("value")) if settled_before else None,
                        "next_settled_after_phase": settled_after[0].get("rate",settled_after[0].get("value")) if settled_after else None,
                        "last_settled": settled[-1].get("rate", settled[-1].get("value")) if settled else None,
                        "predicted": predicted[-1].get("rate", predicted[-1].get("value")) if predicted else None,
                        "settled_count": len(settled), "predicted_count": len(predicted)},
            "basis": {"start": basis_values[0] if basis_values else None, "end": basis_values[-1] if basis_values else None,
                      "change": basis_values[-1]-basis_values[0] if len(basis_values) >= 2 and not basis_gap else None,
                      "mean": sum(basis_values)/len(basis_values) if basis_values else None, "status": "PARTIAL" if basis_gap else "VALID" if basis else "UNAVAILABLE"},
            "liquidation": {"long_notional": long_liq, "short_notional": short_liq, "event_count": len(liquidations),
                            "largest_event": max((float(r.get("notional", 0)) for r in liquidations), default=None),
                            "dominance": "LONG" if long_liq > short_liq else "SHORT" if short_liq > long_liq else "MIXED",
                            "feed_complete": bool(sources.get("liquidation_complete", False)),
                            "warning": None if sources.get("liquidation_complete", False) else "forward-only feed; absence is not proof of no liquidations"},
            "quadrant": price_oi_quadrant(price_change, price_pct, oi_pct, atr, p0),
            "quality": {"overall": continuity, "flow_coverage": flow_coverage, "cvd_gap": cvd_gap, "oi_gap": oi_gap, "basis_gap": basis_gap,
                        "watermark_mismatch": watermark_mismatch, "trade_count": trade_count,
                        "largest_gap_seconds": max(cvd_largest, oi_largest)}}


def price_oi_quadrant(price_change: float | None, price_pct: float | None, oi_pct: float | None,
                      atr: float | None, price: float | None) -> str:
    if price_change is None or oi_pct is None:
        return "INSUFFICIENT_DATA"
    price_flat = abs(price_pct or 0) <= METRIC_THRESHOLDS["price_flat_pct"] or bool(atr and abs(price_change) <= atr*METRIC_THRESHOLDS["price_flat_atr"])
    oi_flat = abs(oi_pct) <= METRIC_THRESHOLDS["oi_flat_pct"]
    if price_flat:
        return "PRICE_FLAT_OI_UP" if oi_pct > METRIC_THRESHOLDS["oi_flat_pct"] else "PRICE_FLAT_OI_DOWN" if oi_pct < -METRIC_THRESHOLDS["oi_flat_pct"] else "INSUFFICIENT_DATA"
    p = "UP" if price_change > 0 else "DOWN"
    o = "FLAT" if oi_flat else "UP" if oi_pct > 0 else "DOWN"
    return f"PRICE_{p}_OI_{o}"


def _oi_unit(rows: list[dict[str, Any]]) -> str | None:
    units = {r.get("unit") for r in rows if r.get("unit")}
    return next(iter(units)) if len(units) == 1 and next(iter(units)) in {"contracts", "coin", "USD"} else None


def _divergence(price: float | None, cvd: float | None) -> str:
    if price is None or cvd is None or price == 0 or cvd == 0: return "NONE"
    return "CONFIRMS" if (price > 0) == (cvd > 0) else "DIVERGES"


def _extreme(rows: list[dict[str, Any]], deltas: list[float]) -> int | None:
    return _ts(rows[max(range(len(deltas)), key=lambda i: abs(deltas[i]))]) if deltas else None


def _acceleration(values: list[float]) -> float | None:
    return values[-1]-2*values[-2]+values[-3] if len(values) >= 3 else None


def _drawdown(values: list[float]) -> float | None:
    if not values: return None
    peak = values[0]; result = 0.0
    for value in values:
        peak = max(peak, value); result = min(result, (value-peak)/peak if peak else 0)
    return result
