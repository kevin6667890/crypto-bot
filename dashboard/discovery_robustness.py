"""Deterministic, development-only Strategy Discovery robustness evidence."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from typing import Any, Mapping

from .discovery_execution import DiscoveryExecutionConfig
from .discovery_identity import (build_candidate_identity, build_parameter_identity,
    canonical_json_hash, normalize_template_parameters)

DISCOVERY_ROBUSTNESS_VERSION = "discovery-development-robustness-v1"
DISCOVERY_NEIGHBOR_VERSION = "discovery-semantic-neighborhood-v1"
DISCOVERY_COST_STRESS_VERSION = "discovery-cost-stress-v1"

_GRIDS = {"fast_period": (6, 10, 20, 30, 60), "slow_period": (60, 100, 150, 200),
          "atr_period": (7, 10, 14, 20, 28), "maximum_distance": (.002, .003, .004, .005, .006, .008)}

def select_robustness_candidates(candidates: list[Mapping[str, Any]], top_k: int = 10,
                                 maximum_candidates: int = 20) -> list[dict[str, Any]]:
    """Pure selection; front one is retained before rank-based filling."""
    if not 1 <= int(top_k) <= 20 or not 1 <= int(maximum_candidates) <= 20:
        raise ValueError("top_k and maximum_candidates must be 1..20.")
    eligible = [dict(x) for x in candidates if x.get("eligibility_status") == "ELIGIBLE"]
    order = lambda x: (int(x.get("eligible_rank") or 10**9), int(x.get("id") or 10**9))
    chosen: dict[Any, dict[str, Any]] = {}
    for item in sorted((x for x in eligible if x.get("pareto_rank") == 1), key=order): chosen[item.get("id")] = item
    for item in sorted(eligible, key=order):
        if len(chosen) >= int(top_k): break
        chosen.setdefault(item.get("id"), item)
    return sorted(chosen.values(), key=order)[:int(maximum_candidates)]

def _adjacent(grid, value):
    index = grid.index(value)
    return (("LOWER", grid[index - 1]) if index else None, ("UPPER", grid[index + 1]) if index + 1 < len(grid) else None)

def generate_parameter_neighbors(template: str, parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return canonical one-parameter semantic neighbours in policy order."""
    original = normalize_template_parameters(template, dict(parameters)); output=[]; seen=set()
    def add(name, direction, value, mutate):
        p=dict(original); mutate(p, value); p=normalize_template_parameters(template, p); h=build_parameter_identity(template,p)
        if p != original and h not in seen:
            seen.add(h); output.append({"changed_parameter":name,"direction":direction,"original_value":original.get(name),"neighbor_value":value,"parameters":p,"parameter_hash":h})
    for name in ("fast_period", "slow_period"):
        for adjacent in _adjacent(_GRIDS[name], original[name]):
            if adjacent:
                direction,value=adjacent
                if (name != "fast_period" or value < original["slow_period"]) and (name != "slow_period" or original["fast_period"] < value): add(name,direction,value,lambda p,v,n=name:p.__setitem__(n,v))
    add("fast_ma_type", "TOGGLE", "EMA" if original["fast_ma_type"] == "SMA" else "SMA", lambda p,v:p.__setitem__("fast_ma_type",v))
    for adjacent in _adjacent(_GRIDS["atr_period"], original["atr_period"]):
        if adjacent: add("atr_period",adjacent[0],adjacent[1],lambda p,v:p.__setitem__("atr_period",v))
    if original["volume_enabled"]: add("volume_enabled","TOGGLE",False,lambda p,v:(p.__setitem__("volume_enabled",v),p.pop("minimum_volume_ratio",None)))
    else: add("volume_enabled","TOGGLE",True,lambda p,v:(p.__setitem__("volume_enabled",v),p.__setitem__("minimum_volume_ratio",1.0)))
    if original["volume_enabled"]:
        for direction,value in (("LOWER",round(original["minimum_volume_ratio"]-.10,2)),("UPPER",round(original["minimum_volume_ratio"]+.10,2))):
            if .70 <= value <= 2.00:add("minimum_volume_ratio",direction,value,lambda p,v:p.__setitem__("minimum_volume_ratio",v))
    if template == "TREND_PULLBACK":
        for adjacent in _adjacent(_GRIDS["maximum_distance"], original["maximum_distance"]):
            if adjacent:add("maximum_distance",adjacent[0],adjacent[1],lambda p,v:p.__setitem__("maximum_distance",v))
    if template == "MEAN_REVERSION":
        for name, minimum, maximum in (("rsi_lower",20,49),("rsi_upper",51,80)):
            for direction,value in (("LOWER",original[name]-5),("UPPER",original[name]+5)):
                if minimum <= value <= maximum and (name != "rsi_lower" or value < original["rsi_upper"]) and (name != "rsi_upper" or original["rsi_lower"] < value): add(name,direction,value,lambda p,v,n=name:p.__setitem__(n,v))
    return output

def generate_cost_scenarios(execution_config: DiscoveryExecutionConfig | Mapping[str, Any]) -> list[dict[str, Any]]:
    base = execution_config if isinstance(execution_config, DiscoveryExecutionConfig) else DiscoveryExecutionConfig(**dict(execution_config))
    base.validate(); out=[]
    for name, fee, slip in (("FEE_1_5X",1.5,1), ("SLIPPAGE_1_5X",1,1.5), ("COMBINED_2X",2,2), ("COMBINED_3X",3,3)):
        cfg=replace(base,trading_fee=base.trading_fee*fee,slippage=base.slippage*slip).validate()
        out.append({"scenario_name":name,"execution":cfg,"execution_hash":cfg.execution_hash(),"assumptions":{"fee_multiplier":fee,"slippage_multiplier":slip}})
    return out

def build_robustness_scenario_identity(*, category: str, scenario_name: str, source_parameter_hash: str,
    scenario_parameter_hash: str, source_execution_hash: str, scenario_execution_hash: str, instrument: str,
    timeframe: str, dataset_fingerprint: str, assumptions: Mapping[str, Any], five_fold_policy_version: str,
    scenario_policy_version: str) -> str:
    return canonical_json_hash({"robustness_version":DISCOVERY_ROBUSTNESS_VERSION,"scenario_category":category,
      "scenario_name":scenario_name,"source_parameter_hash":source_parameter_hash,"scenario_parameter_hash":scenario_parameter_hash,
      "source_execution_hash":source_execution_hash,"scenario_execution_hash":scenario_execution_hash,"instrument":instrument,
      "timeframe":timeframe,"dataset_fingerprint":dataset_fingerprint,"five_fold_policy_version":five_fold_policy_version,
      "scenario_policy_version":scenario_policy_version,"assumptions":dict(assumptions)})

def summarize_candidate_robustness(candidate: Mapping[str, Any], scenarios: list[Mapping[str, Any]]) -> dict[str, Any]:
    scenarios=[{**dict(item), **{key: (json.loads(value) if isinstance(value,str) else value) for key,value in dict(item).items() if key in ('aggregate_metrics','comparison_to_base')}} for item in scenarios]
    done=[x for x in scenarios if x.get("status")=="COMPLETED"]
    group=lambda category:[x for x in done if x.get("scenario_category")==category]
    neighbours,costs=group("PARAMETER_NEIGHBOR"),group("COST_STRESS")
    metric=lambda xs,key: [x.get("aggregate_metrics",{}).get(key) for x in xs if x.get("aggregate_metrics",{}).get(key) is not None]
    median=lambda xs: sorted(xs)[len(xs)//2] if len(xs)%2 else (sorted(xs)[len(xs)//2-1]+sorted(xs)[len(xs)//2])/2 if xs else None
    eligible=lambda xs:sum(x.get("comparison_to_base",{}).get("scenario_eligibility_status")=="ELIGIBLE" for x in xs)
    byname={x.get("scenario_name"):x for x in costs}
    return {"candidate_id":candidate.get("id"),"candidate_number":candidate.get("candidate_number"),"eligible_rank":candidate.get("eligible_rank"),"pareto_rank":candidate.get("pareto_rank"),"development_score":candidate.get("development_score"),"parameter_neighbor_count":len([x for x in scenarios if x.get("scenario_category")=="PARAMETER_NEIGHBOR"]),"completed_parameter_neighbors":len(neighbours),"parameter_neighbors_remaining_eligible":eligible(neighbours),"parameter_neighbor_eligibility_ratio":eligible(neighbours)/len(neighbours) if neighbours else None,"median_neighbor_median_excess_return":median(metric(neighbours,"median_excess_return")),"worst_neighbor_median_excess_return":min(metric(neighbours,"median_excess_return"),default=None),"worst_neighbor_worst_excess_return":min(metric(neighbours,"worst_excess_return"),default=None),"maximum_neighbor_drawdown":max(metric(neighbours,"worst_maximum_drawdown"),default=None),"cost_scenario_count":len([x for x in scenarios if x.get("scenario_category")=="COST_STRESS"]),"completed_cost_scenarios":len(costs),"cost_scenarios_remaining_eligible":eligible(costs),"cost_survival_ratio":eligible(costs)/len(costs) if costs else None,"combined_2x_median_excess_return":byname.get("COMBINED_2X",{}).get("aggregate_metrics",{}).get("median_excess_return"),"combined_3x_median_excess_return":byname.get("COMBINED_3X",{}).get("aggregate_metrics",{}).get("median_excess_return"),"warnings":[]}

def summarize_robustness_run(source_discovery_run_id: int, candidate_summaries: list[Mapping[str, Any]], scenarios: list[Mapping[str, Any]], warnings=None) -> dict[str, Any]:
    return {"source_discovery_run_id":source_discovery_run_id,"robustness_version":DISCOVERY_ROBUSTNESS_VERSION,"selected_candidate_count":len(candidate_summaries),"total_scenario_count":len(scenarios),"completed_scenario_count":sum(x.get("status")=="COMPLETED" for x in scenarios),"failed_scenario_count":sum(x.get("status")=="FAILED" for x in scenarios),"candidate_summaries":[dict(x) for x in candidate_summaries],"warnings":list(warnings or [])}
