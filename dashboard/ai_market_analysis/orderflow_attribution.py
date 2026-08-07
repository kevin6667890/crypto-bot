"""Explainable rule scoring for phase-level positioning attribution."""
from __future__ import annotations

from typing import Any

from .versions import AI_ORDERFLOW_ATTRIBUTION_VERSION

ATTRIBUTIONS = ("NEW_LONGS_DOMINANT", "SHORT_COVERING_DOMINANT", "NEW_SHORTS_DOMINANT",
                "LONG_UNWINDING_DOMINANT", "SHORT_LIQUIDATION_ASSISTED", "LONG_LIQUIDATION_ASSISTED",
                "TWO_SIDED_DELEVERAGING", "SPOT_BUYING_LIKELY", "SPOT_SELLING_LIKELY",
                "LEVERAGED_LONG_BUILDUP", "LEVERAGED_SHORT_BUILDUP", "MIXED_POSITIONING",
                "INSUFFICIENT_EVIDENCE")
ALTERNATIVE_ACTIVE_BUYING = "ACTIVE_BUYING_CONTRIBUTED"
METRIC_DIRECTIONS = ("RISING", "FALLING", "FLAT", "UNAVAILABLE")
SCORE_RULES = {"direction": 3, "oi": 3, "cvd": 2, "volume": 1, "liquidation": 1,
               "minimum_evidence": 3, "high_score": 8, "medium_score": 5}


def classify_orderflow(metrics: dict[str, Any], phase: str = "CURRENT") -> dict[str, Any]:
    q = metrics["quality"]
    unavailable = []
    if metrics["price_change"] is None: unavailable.append("price change")
    if metrics["oi"]["absolute_change"] is None: unavailable.append("OI change")
    if metrics["cvd"]["signed_delta"] is None: unavailable.append("CVD delta")
    if metrics["oi"].get("unit") is None: unavailable.append("OI unit")
    fatal = q["watermark_mismatch"] or metrics["price_change"] is None or metrics["oi"]["observation_count"] < 2
    if fatal:
        return _result("INSUFFICIENT_EVIDENCE", [], "LOW", ["critical aligned observations unavailable"],
                       _warnings(metrics), [], unavailable, metrics)
    p, o, c = metrics["price_change"], metrics["oi"]["percentage_change"], metrics["cvd"]["signed_delta"]
    vol = metrics["volume_regime"]
    flat_p = metrics["quadrant"].startswith("PRICE_FLAT")
    flat_o = metrics["quadrant"].endswith("OI_FLAT")
    scores = {name: 0 for name in ATTRIBUTIONS}
    evidence: dict[str, list[str]] = {name: [] for name in ATTRIBUTIONS}
    counter: dict[str, list[str]] = {name: [] for name in ATTRIBUTIONS}
    def add(name: str, points: int, claim: str) -> None: scores[name] += points; evidence[name].append(claim)
    if p > 0 and not flat_p:
        if o is not None and o > .002: add("NEW_LONGS_DOMINANT", 6, "significant price rise with OI expansion")
        if o is not None and o < -.002: add("SHORT_COVERING_DOMINANT", 6, "significant price rise with OI contraction")
        if flat_o and c is not None and c > 0: add("SPOT_BUYING_LIKELY", 5, "price and CVD rose without leverage expansion")
        if c is not None and c > 0:
            add("NEW_LONGS_DOMINANT", 2, "positive aggressive-flow delta")
            add("SHORT_COVERING_DOMINANT", 2, "active buying contributed to the rise")
            add("SPOT_BUYING_LIKELY", 2, "positive aggressive-flow delta")
        elif c is not None and c < 0:
            counter["NEW_LONGS_DOMINANT"].append("CVD opposed price rise")
            counter["SHORT_COVERING_DOMINANT"].append("CVD opposed price rise")
            if o is not None and o > .002:
                scores["NEW_LONGS_DOMINANT"] -= 4; add("MIXED_POSITIONING",5,"OI expanded but CVD opposed the price rise")
        elif o is not None and o > .002:
            scores["NEW_LONGS_DOMINANT"] -= 4; add("MIXED_POSITIONING",5,"OI expanded without positive CVD confirmation")
        if metrics["liquidation"]["short_notional"] > 0:
            add("SHORT_LIQUIDATION_ASSISTED", 6, "short liquidation feed recorded forced exits")
            add("SHORT_COVERING_DOMINANT", 1, "short liquidations assisted upside")
    elif p < 0 and not flat_p:
        if o is not None and o > .002: add("NEW_SHORTS_DOMINANT", 6, "significant price decline with OI expansion")
        if o is not None and o < -.002: add("LONG_UNWINDING_DOMINANT", 6, "significant price decline with OI contraction")
        if flat_o and c is not None and c < 0: add("SPOT_SELLING_LIKELY", 5, "price and CVD fell without leverage expansion")
        if c is not None and c < 0:
            add("NEW_SHORTS_DOMINANT", 2, "negative aggressive-flow delta")
            add("LONG_UNWINDING_DOMINANT", 2, "active selling accompanied decline")
            add("SPOT_SELLING_LIKELY", 2, "negative aggressive-flow delta")
        elif c is not None and c > 0 and o is not None and o > .002:
            scores["NEW_SHORTS_DOMINANT"] -= 4; add("MIXED_POSITIONING",5,"OI expanded but CVD opposed the price decline")
            counter["NEW_SHORTS_DOMINANT"].append("positive CVD opposed new-short interpretation")
        elif o is not None and o > .002:
            scores["NEW_SHORTS_DOMINANT"] -= 4; add("MIXED_POSITIONING",5,"OI expanded without negative CVD confirmation")
        if metrics["liquidation"]["long_notional"] > 0:
            add("LONG_LIQUIDATION_ASSISTED", 6, "long liquidation feed recorded forced exits")
            add("LONG_UNWINDING_DOMINANT", 1, "long liquidations assisted downside")
    if flat_p and o is not None and o < -.002 and metrics["liquidation"]["long_notional"] > 0 and metrics["liquidation"]["short_notional"] > 0:
        add("TWO_SIDED_DELEVERAGING", 9, "flat/net-choppy price, falling OI and two-sided liquidations")
    if flat_p and o is not None and o > .002:
        name = "LEVERAGED_LONG_BUILDUP" if (c or 0) > 0 else "LEVERAGED_SHORT_BUILDUP" if (c or 0) < 0 else "MIXED_POSITIONING"
        add(name, 6, "OI expanded without corresponding price progress")
    if vol == "EXPANDING":
        for name in ("NEW_LONGS_DOMINANT", "SHORT_COVERING_DOMINANT", "NEW_SHORTS_DOMINANT", "LONG_UNWINDING_DOMINANT", "TWO_SIDED_DELEVERAGING"):
            add(name, 1, "volume expanded")
    ranked = sorted(scores, key=lambda name: (-scores[name], ATTRIBUTIONS.index(name)))
    primary = ranked[0] if scores[ranked[0]] >= SCORE_RULES["minimum_evidence"] else "MIXED_POSITIONING"
    alternatives: list[str] = []
    if primary == "SHORT_COVERING_DOMINANT" and c is not None and c > 0:
        alternatives.append(ALTERNATIVE_ACTIVE_BUYING)
    alternatives.extend(name for name in ranked if name != primary and scores[name] >= max(4, scores[primary]-3))
    alternatives = alternatives[:2]
    confidence = "HIGH" if scores[primary] >= SCORE_RULES["high_score"] else "MEDIUM" if scores[primary] >= SCORE_RULES["medium_score"] else "LOW"
    if q["cvd_gap"] or q["oi_gap"] or q["basis_gap"] or metrics["cvd"]["trade_count"] < 10:
        confidence = "LOW" if confidence == "MEDIUM" else "MEDIUM" if confidence == "HIGH" else confidence
    return _result(primary, alternatives, confidence, evidence[primary] or ["mixed evidence did not meet a dominant rule"],
                   counter[primary]+_warnings(metrics), _decisive(metrics), unavailable, metrics)


def phase_transitions(phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for before, after in zip(phases, phases[1:]):
        bm, am = before["metrics"], after["metrics"]
        oi_change = _behavior(am["oi"]["percentage_change"])
        cvd_change = _behavior(am["cvd"]["signed_delta"])
        interpretation = "POSSIBLE_NEW_POSITION_BUILDUP" if oi_change == "RISING" and after["attribution"]["primary"] == "MIXED_POSITIONING" else after["attribution"]["primary"]
        output.append({"from_phase": before["phase_id"], "to_phase": after["phase_id"],
                       "price_change": am["price_change"], "oi_behavior": oi_change,
                       "cvd_behavior": cvd_change, "volume_change": am["volume_regime"],
                       "interpretation": interpretation, "confidence": after["attribution"]["confidence"],
                       "evidence": after["attribution"]["evidence"], "counterevidence": after["attribution"]["counterevidence"]})
    return output


def _result(primary: str, alternatives: list[str], confidence: str, evidence: list[str],
            counter: list[str], decisive: list[str], unavailable: list[str], metrics: dict[str, Any]) -> dict[str, Any]:
    return {"primary": primary, "alternatives": alternatives, "confidence": confidence,
            "evidence": sorted(set(evidence)), "counterevidence": sorted(set(counter)) or ["no material counterevidence observed in available sources"],
            "decisive_metrics": decisive, "unavailable_evidence": sorted(set(unavailable)),
            "data_quality": metrics["quality"], "rule_version": AI_ORDERFLOW_ATTRIBUTION_VERSION}


def _warnings(m: dict[str, Any]) -> list[str]:
    q=m["quality"]; result=[]
    if q["cvd_gap"]: result.append("CVD gap prevents cross-gap cumulative comparison")
    if q["oi_gap"]: result.append("OI gap prevents phase change and acceleration calculation")
    if q["basis_gap"]: result.append("basis gap prevents cross-gap change")
    if not m["liquidation"]["feed_complete"]: result.append("liquidation feed is forward-only/incomplete")
    return result


def _decisive(m: dict[str, Any]) -> list[str]:
    return [f"quadrant={m['quadrant']}", f"price_change_pct={m['price_change_pct']}",
            f"oi_change_pct={m['oi']['percentage_change']}", f"cvd_delta={m['cvd']['signed_delta']}",
            f"volume_regime={m['volume_regime']}"]


def _behavior(value: float | None) -> str:
    return "UNAVAILABLE" if value is None else "RISING" if value > 0 else "FALLING" if value < 0 else "FLAT"
