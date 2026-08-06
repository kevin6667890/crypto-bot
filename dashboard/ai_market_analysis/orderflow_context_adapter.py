"""AI-3 orchestration over pure price facts and supplied canonical order-flow rows."""
from __future__ import annotations

from typing import Any

from .canonical import stable_hash
from .key_level_candidates import build_level_candidates
from .key_level_zones import merge_level_zones
from .orderflow_attribution import classify_orderflow, phase_transitions
from .orderflow_metrics import compute_phase_metrics
from .orderflow_windows import resolve_phase_windows
from .scenario_builder import build_scenario_tree
from .versions import TIMEFRAME_SECONDS


def build_ai3_facts(*, facts: dict[str,dict[str,Any]], timelines: dict[str,dict[str,Any]],
                    swings: dict[str,list[dict[str,Any]]], dominant: str, instrument: str,
                    decision_time: int, orderflow: dict[str,Any] | None=None,
                    auxiliary: dict[str,Any] | None=None) -> dict[str,Any]:
    orderflow=orderflow or {}; timeline=timelines[dominant]; fact=facts[dominant]
    watermark=min(decision_time,fact["latest_confirmed_bar_timestamp"] or decision_time,
                  int(orderflow.get("watermark",decision_time)))
    windows=resolve_phase_windows(timeline,instrument,watermark)
    phases=[]
    for window in windows:
        metrics=compute_phase_metrics(window,fact["confirmed_bars"],orderflow,atr=fact["atr14"]["value"],
                                      bucket_seconds=TIMEFRAME_SECONDS[dominant])
        actual=max(len(metrics["cvd"]["source_bucket_timestamps"]),metrics["oi"]["observation_count"])
        window={**window,"actual_buckets":actual,"gap_count":sum((metrics["quality"]["cvd_gap"],metrics["quality"]["oi_gap"])),
                "largest_gap_seconds":metrics["quality"]["largest_gap_seconds"],"quality":metrics["quality"]["overall"]}
        phases.append({**window,"metrics":metrics,"attribution":classify_orderflow(metrics,window["phase"])})
    current_price=float(fact["confirmed_close"])
    candidates=build_level_candidates(facts,timelines,swings,decision_time,current_price,auxiliary)
    levels=merge_level_zones(candidates,current_price,fact["atr14"]["value"],fact["confirmed_bars"],decision_time,timeline["direction"])
    event_ids=[e["event_id"] for e in timeline["events"]]
    scenarios=build_scenario_tree(timeline["direction"],timeline["current_phase"],levels,phases,event_ids)
    def row_time(row):
        value=int(row.get("timestamp",row.get("bucket_timestamp",row.get("bucket_ms",row.get("ts",0)))))
        return value//1000 if value>10_000_000_000 else value
    fingerprints={key:stable_hash(sorted((row for row in rows if row_time(row)<watermark),key=lambda r:stable_hash(r)))
                  for key,rows in orderflow.items() if isinstance(rows,list)}
    source_watermarks={key:max((row_time(row) for row in rows if row_time(row)<watermark),default=None)
                       for key,rows in orderflow.items() if isinstance(rows,list)}
    return {"order_flow_phases":phases,"phase_transitions":phase_transitions(phases),"key_level_candidates":candidates,
            "key_levels":levels,"scenario_tree":scenarios,"source_fingerprints":fingerprints,"watermark":watermark,
            "source_watermarks":source_watermarks,
            "quality":_quality(phases),"not_implemented_sources":[name for name in ("vpvr","funding_extreme_prices","basis_extreme_prices") if not (auxiliary or {}).get(name)]}


def _quality(phases):
    qualities={p["metrics"]["quality"]["overall"] for p in phases}
    return "MISSING" if qualities=={"UNAVAILABLE"} else "PARTIAL" if qualities!={"VALID"} else "VALID"
