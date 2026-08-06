"""Three-branch deterministic scenario construction with referential integrity."""
from __future__ import annotations

from typing import Any

from .canonical import identity
from .versions import AI_SCENARIO_TREE_VERSION

SCENARIO_TYPES=("BULLISH_CONTINUATION","NORMAL_RETEST","FAILED_BREAKOUT",
                "BEARISH_CONTINUATION","NORMAL_BEARISH_RETEST","FAILED_BREAKDOWN")


def build_scenario_tree(direction: str, current_phase: str, levels: list[dict[str,Any]],
                        phases: list[dict[str,Any]], source_event_ids: list[str]) -> dict[str,Any]:
    if not levels or direction not in {"UP","DOWN"} or current_phase not in {"BREAKOUT_CONFIRMED","IMPULSE","POST_BREAKOUT_PULLBACK","RETEST","CONTINUATION","FAILED_BREAKOUT"}:
        return {"status":"NOT_IMPLEMENTED","direction":direction,"scenarios":[],"version":AI_SCENARIO_TREE_VERSION}
    supports=sorted((l for l in levels if l["role"] in {"SUPPORT","PIVOT"}),key=lambda l:l["representative_price"],reverse=True)
    resistances=sorted((l for l in levels if l["role"] in {"RESISTANCE","PIVOT"}),key=lambda l:l["representative_price"])
    flipped=[l for l in levels if l["state"]=="FLIPPED"]
    core=(flipped or (supports if direction=="UP" else resistances) or levels)[:1]
    favorable=(resistances if direction=="UP" else list(reversed(supports)))
    unfavorable=(supports if direction=="UP" else resistances)
    primary_phase=next((p for p in reversed(phases) if p["attribution"]["primary"] not in {"INSUFFICIENT_EVIDENCE"}),None)
    flow_primary=primary_phase["attribution"]["primary"] if primary_phase else "INSUFFICIENT_EVIDENCE"
    quality_good=bool(primary_phase and primary_phase["attribution"]["confidence"]!="LOW")
    likelihood="HIGH" if current_phase in {"RETEST","CONTINUATION"} and quality_good else "MEDIUM" if quality_good else "LOW"
    scenarios=[]
    if direction=="UP":
        scenarios.append(_scenario("BULLISH_CONTINUATION","UP",likelihood,favorable[:2] or core,core,
            "confirmed close above the nearest active resistance or impulse extreme",
            "volume recovers and CVD turns positive on the confirming close",flow_primary,source_event_ids,phases,
            "positive CVD expansion", "moderate OI recovery is constructive after covering; explosive OI without price progress contradicts"))
        scenarios.append(_scenario("NORMAL_RETEST","UP","MEDIUM" if core else "LOW",core or unfavorable[:1],unfavorable[:1] or core,
            "price enters the flipped breakout/retest zone",
            "two confirmed closes hold the referenced zone with contracting sell volume",flow_primary,source_event_ids,phases,
            "selling CVD does not persist or a positive rejection follows", "OI must not expand aggressively while price loses the zone"))
        scenarios.append(_scenario("FAILED_BREAKOUT","DOWN","LOW",unfavorable[1:3] or unfavorable[:1] or core,core,
            "two confirmed closes return inside the prior range below the core breakout zone",
            "a confirmed retest fails to reclaim the referenced zone with weakening CVD",flow_primary,source_event_ids,phases,
            "negative CVD confirms failed reclaim", "rising OI with falling price supports new shorts; falling OI supports long unwinding"))
    else:
        scenarios.append(_scenario("BEARISH_CONTINUATION","DOWN",likelihood,favorable[:2] or core,core,
            "confirmed close below the nearest active support or impulse extreme",
            "volume recovers and CVD turns negative on the confirming close",flow_primary,source_event_ids,phases,
            "negative CVD expansion", "moderate OI recovery can confirm new shorts; explosive OI without price progress contradicts"))
        scenarios.append(_scenario("NORMAL_BEARISH_RETEST","DOWN","MEDIUM" if core else "LOW",core or unfavorable[:1],unfavorable[:1] or core,
            "price enters the flipped breakdown/retest zone from below",
            "two confirmed closes remain below the referenced zone with contracting buy volume",flow_primary,source_event_ids,phases,
            "buying CVD does not persist or a negative rejection follows", "OI must not expand aggressively while price reclaims the zone"))
        scenarios.append(_scenario("FAILED_BREAKDOWN","UP","LOW",unfavorable[1:3] or unfavorable[:1] or core,core,
            "two confirmed closes return inside the prior range above the core breakdown zone",
            "a confirmed retest holds above the referenced zone with strengthening CVD",flow_primary,source_event_ids,phases,
            "positive CVD confirms reclaim", "rising OI with rising price supports new longs; falling OI supports short covering"))
    return {"status":"AVAILABLE","direction":direction,"scenarios":scenarios,"version":AI_SCENARIO_TREE_VERSION}


def _scenario(kind,direction,likelihood,targets,invalidation,trigger,confirmation,flow_primary,event_ids,phases,cvd,oi):
    target_ids=[l["level_id"] for l in targets]
    invalid_id=invalidation[0]["level_id"] if invalidation else target_ids[0]
    phase_ids=[p["phase_id"] for p in phases]
    stable={"type":kind,"targets":target_ids,"invalidation":invalid_id,"events":sorted(event_ids),"phases":phase_ids,"version":AI_SCENARIO_TREE_VERSION}
    return {"scenario_id":identity("scenario",stable),"type":kind,"direction":direction,"likelihood":likelihood,
            "trigger":{"rule":trigger,"level_ids":[invalid_id]},"confirmation":{"rule":confirmation,"timeframe":"15m"},
            "expected_path":[invalid_id,*target_ids],"target_level_ids":target_ids,"invalidation":{"rule":"two confirmed 15m closes violate the referenced boundary","level_id":invalid_id,"timeframe":"15m"},
            "volume_confirmation":"volume regime must agree with the trigger, contraction on retest and expansion on continuation/failure",
            "cvd_confirmation":cvd,"oi_confirmation":oi,"funding_basis_confirmation":"funding/basis must not show extreme leverage expansion opposing the path",
            "contradicting_evidence":[f"current primary attribution is {flow_primary}"],"required_data_quality":"VALID for HIGH; PARTIAL caps at MEDIUM",
            "source_event_ids":sorted(set(event_ids)),"source_phase_ids":phase_ids,"source_level_ids":sorted(set([invalid_id,*target_ids])),"version":AI_SCENARIO_TREE_VERSION}
