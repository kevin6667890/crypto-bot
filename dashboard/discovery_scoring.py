"""Deterministic development-only Discovery eligibility, scoring and Pareto policy."""
from __future__ import annotations
import math

DISCOVERY_ELIGIBILITY_VERSION="discovery-development-eligibility-v1"
DISCOVERY_SCORING_VERSION="discovery-development-scoring-v1"
DISCOVERY_PARETO_VERSION="discovery-development-pareto-v1"
PARETO_EPSILON=1e-12
REASONS=("CANDIDATE_NOT_DEVELOPMENT_COMPLETE","INCOMPLETE_FOLD_SET","FAILED_DEVELOPMENT_FOLD","INSUFFICIENT_FOLDS_WITH_TRADES","INSUFFICIENT_TOTAL_TRADES","INSUFFICIENT_MEDIAN_TRADES","INSUFFICIENT_PROFITABLE_FOLDS","INSUFFICIENT_BENCHMARK_BEATING_FOLDS","NONPOSITIVE_MEDIAN_EXCESS_RETURN","WORST_FOLD_RETURN_TOO_LOW","WORST_EXCESS_RETURN_TOO_LOW","MAXIMUM_DRAWDOWN_TOO_HIGH","REQUIRED_METRIC_UNDEFINED","REQUIRED_METRIC_NONFINITE")

def candidate_complexity(template, p):
    return 5 + int(bool(p.get("volume_enabled"))) + int(template=="TREND_PULLBACK") + (2 if template=="MEAN_REVERSION" else 0)
def eligibility_policy(timeframe):
    return {"15m":(40,8),"1H":(20,4),"4H":(10,2),"1D":(5,1)}[timeframe]
def _finite(a,k): return k in a and a[k] is not None and isinstance(a[k],(int,float)) and not isinstance(a[k],bool) and math.isfinite(float(a[k]))
def evaluate_eligibility(aggregate,timeframe,status="DEVELOPMENT_CANDIDATE"):
    reasons=[]; needed=("completed_fold_count","failed_fold_count","folds_with_trades","total_trades","median_trades_per_fold","profitable_fold_ratio","benchmark_beating_fold_ratio","median_excess_return","worst_validation_return","worst_excess_return","worst_maximum_drawdown","validation_return_standard_deviation")
    missing=any(k not in aggregate or aggregate[k] is None for k in needed); nonfinite=any(k in aggregate and aggregate[k] is not None and not _finite(aggregate,k) for k in needed)
    if status!="DEVELOPMENT_CANDIDATE": reasons.append(REASONS[0])
    if aggregate.get("completed_fold_count")!=5: reasons.append(REASONS[1])
    if aggregate.get("failed_fold_count")!=0: reasons.append(REASONS[2])
    if aggregate.get("folds_with_trades",0)<4: reasons.append(REASONS[3])
    trades,median=eligibility_policy(timeframe)
    if aggregate.get("total_trades",0)<trades: reasons.append(REASONS[4])
    if float(aggregate.get("median_trades_per_fold") or 0)<median: reasons.append(REASONS[5])
    if float(aggregate.get("profitable_fold_ratio") or 0)<.6: reasons.append(REASONS[6])
    if float(aggregate.get("benchmark_beating_fold_ratio") or 0)<.6: reasons.append(REASONS[7])
    if float(aggregate.get("median_excess_return") or 0)<=0: reasons.append(REASONS[8])
    if float(aggregate.get("worst_validation_return") if aggregate.get("worst_validation_return") is not None else -math.inf)<-10: reasons.append(REASONS[9])
    if float(aggregate.get("worst_excess_return") if aggregate.get("worst_excess_return") is not None else -math.inf)<-10: reasons.append(REASONS[10])
    if float(aggregate.get("worst_maximum_drawdown") if aggregate.get("worst_maximum_drawdown") is not None else math.inf)>20: reasons.append(REASONS[11])
    if missing: reasons.append(REASONS[12])
    if nonfinite: reasons.append(REASONS[13])
    return {"eligible":not reasons,"reasons":reasons}
def _linear(v,low,high): return 100*max(0,min(1,(v-low)/(high-low)))
def _inverse(v,best,worst): return 100*max(0,min(1,(worst-v)/(worst-best)))
def calculate_score(a,complexity,timeframe=None):
    specs=(("median_excess_return",25,_linear,0,8),("worst_excess_return",15,_linear,-10,3),("profitable_fold_ratio",15,_linear,.6,1),("benchmark_beating_fold_ratio",15,_linear,.6,1),("worst_maximum_drawdown",15,_inverse,5,20),("validation_return_standard_deviation",10,_inverse,2,12),("structural_complexity",5,_inverse,5,8))
    components={}; total=0
    for key,w,fn,x,y in specs:
        raw=complexity if key=="structural_complexity" else a[key]; score=round(fn(float(raw),x,y),6); contribution=round(score*w/100,6); total+=contribution
        components[key]={"raw_metric_value":raw,"normalized_component_score":score,"weight":w,"weighted_contribution":contribution}
    total=round(total,6); return total,{"policy_version":DISCOVERY_SCORING_VERSION,"components":components,"final_score":total,"warnings":["Development folds only; primary holdout and final OOT excluded."]}
def _dominates(a,b):
    av=(a["median_excess_return"],a["worst_excess_return"],-a["worst_maximum_drawdown"],-a["validation_return_standard_deviation"]); bv=(b["median_excess_return"],b["worst_excess_return"],-b["worst_maximum_drawdown"],-b["validation_return_standard_deviation"])
    return all(x>=y-PARETO_EPSILON for x,y in zip(av,bv)) and any(x>y+PARETO_EPSILON for x,y in zip(av,bv))
def assign_pareto_fronts(candidates):
    left=list(candidates); rank=1
    while left:
        front=[x for x in left if not any(_dominates(y,x) for y in left if y is not x)]
        for x in front:x["pareto_rank"]=rank
        left=[x for x in left if x not in front]; rank+=1
    return candidates
def rank_eligible_candidates(candidates):
    ordered=sorted(candidates,key=lambda x:(-x["development_score"],x["pareto_rank"],-x["aggregate"]["median_excess_return"],-x["aggregate"]["worst_excess_return"],x["aggregate"]["worst_maximum_drawdown"],x["aggregate"]["validation_return_standard_deviation"],x["complexity"],x["parameter_hash"],x["candidate_number"]))
    for n,x in enumerate(ordered,1):x["eligible_rank"]=n
    return ordered
