from __future__ import annotations
import math
from dashboard.discovery_scoring import *
from dashboard.discovery_service import aggregate

def evidence(**overrides):
    a={'completed_fold_count':5,'failed_fold_count':0,'folds_with_trades':5,'total_trades':50,'median_trades_per_fold':10,'profitable_fold_ratio':.8,'benchmark_beating_fold_ratio':.8,'median_excess_return':4.,'worst_validation_return':-2.,'worst_excess_return':-1.,'worst_maximum_drawdown':10.,'validation_return_standard_deviation':5.}; a.update(overrides); return a
def test_aggregate_benchmark_counts_ties_and_diagnostics():
    folds=[{'status':'COMPLETED','metrics':{'total_return':x,'total_trades':1,'maximum_drawdown':1,'sharpe_ratio':None,'sortino_ratio':None,'profit_factor':None,'fees_paid':1},'buy_hold_metrics':{'total_return':b}} for x,b in [(2,1),(1,1),(0,1)]]
    a=aggregate(folds); assert a['benchmark_beating_fold_count']==1 and a['benchmark_beating_fold_ratio']==1/3 and a['finite_sharpe_fold_count']==0 and a['total_trades']==3
def test_eligibility_all_timeframes_and_reasons():
    for tf in ('15m','1H','4H','1D'): assert evaluate_eligibility(evidence(total_trades=eligibility_policy(tf)[0],median_trades_per_fold=eligibility_policy(tf)[1]),tf)['eligible']
    r=evaluate_eligibility(evidence(total_trades=0,median_trades_per_fold=0,median_excess_return=0),"15m")['reasons']; assert r==['INSUFFICIENT_TOTAL_TRADES','INSUFFICIENT_MEDIAN_TRADES','NONPOSITIVE_MEDIAN_EXCESS_RETURN']
def test_missing_and_nonfinite_rejected():
    assert 'REQUIRED_METRIC_UNDEFINED' in evaluate_eligibility(evidence(median_excess_return=None),'4H')['reasons']
    assert 'REQUIRED_METRIC_NONFINITE' in evaluate_eligibility(evidence(worst_excess_return=math.inf),'4H')['reasons']
def test_score_is_bounded_exact_and_monotonic():
    score,parts=calculate_score(evidence(),6); assert score==58.217948 and sum(x['weight'] for x in parts['components'].values())==100
    assert calculate_score(evidence(median_excess_return=8),6)[1]['components']['median_excess_return']['normalized_component_score']>=parts['components']['median_excess_return']['normalized_component_score']
    assert calculate_score(evidence(worst_maximum_drawdown=20),6)[1]['components']['worst_maximum_drawdown']['normalized_component_score']<=parts['components']['worst_maximum_drawdown']['normalized_component_score']
def test_complexity_and_pareto_and_rank_are_deterministic():
    assert candidate_complexity('TREND_BREAKOUT',{'volume_enabled':False})==5 and candidate_complexity('MEAN_REVERSION',{'volume_enabled':True})==8
    a={'development_score':70,'complexity':5,'parameter_hash':'a','candidate_number':2,'aggregate':evidence(median_excess_return=4,worst_excess_return=1,worst_maximum_drawdown=8,validation_return_standard_deviation=3)}
    b={'development_score':70,'complexity':5,'parameter_hash':'b','candidate_number':1,'aggregate':evidence(median_excess_return=3,worst_excess_return=0,worst_maximum_drawdown=9,validation_return_standard_deviation=4)}
    before=(dict(a),dict(b))
    fronts=assign_pareto_fronts([b,a]); assert fronts[('a',2)]==1 and fronts[('b',1)]==2
    ranks=rank_eligible_candidates([b,a],fronts); assert ranks[('a',2)]==1
    assert (dict(a),dict(b))==before

def test_eligibility_rejects_all_malformed_evidence_without_crashing():
    for malformed in ('1', True, [], {}, math.nan, math.inf, -math.inf):
        result=evaluate_eligibility(evidence(median_excess_return=malformed),'1H')
        assert 'REQUIRED_METRIC_NONFINITE' in result['reasons']
    assert evaluate_eligibility(evidence(median_excess_return=None),'1H')['reasons'][-1]=='REQUIRED_METRIC_UNDEFINED'

def test_eligibility_boundaries_and_reason_order():
    assert evaluate_eligibility(evidence(profitable_fold_ratio=.6,benchmark_beating_fold_ratio=.6,worst_validation_return=-10,worst_excess_return=-10,worst_maximum_drawdown=20),'1D')['eligible']
    assert 'NONPOSITIVE_MEDIAN_EXCESS_RETURN' in evaluate_eligibility(evidence(median_excess_return=0),'1D')['reasons']
    result=evaluate_eligibility(evidence(total_trades=0,median_trades_per_fold=0,median_excess_return=0),'15m')
    assert result['reasons']==['INSUFFICIENT_TOTAL_TRADES','INSUFFICIENT_MEDIAN_TRADES','NONPOSITIVE_MEDIAN_EXCESS_RETURN']

def test_score_validation_and_persistence_contract():
    for malformed in (None, True, '4', math.nan, math.inf):
        try: calculate_score(evidence(median_excess_return=malformed),6)
        except ValueError as error: assert str(error)=='Invalid Discovery score metric: median_excess_return'
        else: assert False
    for complexity in (True, 4, 9, 6.0):
        try: calculate_score(evidence(),complexity)
        except ValueError as error: assert str(error)=='Discovery structural complexity must be an integer from 5 to 8'
        else: assert False
    score, payload=calculate_score(evidence(),6)
    assert payload['final_score']==score and 'Score is not proof of future profitability.' in payload['warnings']
    assert all(round(x['normalized_component_score'],6)==x['normalized_component_score'] and round(x['weighted_contribution'],6)==x['weighted_contribution'] for x in payload['components'].values())

def test_pareto_and_ranking_are_order_independent_and_ignore_score_for_fronts():
    a={'aggregate':evidence(median_excess_return=4,worst_excess_return=1,worst_maximum_drawdown=8,validation_return_standard_deviation=3),'development_score':1,'complexity':5,'parameter_hash':'a','candidate_number':1}
    b={'aggregate':evidence(median_excess_return=3,worst_excess_return=0,worst_maximum_drawdown=9,validation_return_standard_deviation=4),'development_score':99,'complexity':5,'parameter_hash':'b','candidate_number':2}
    assert assign_pareto_fronts([a,b])==assign_pareto_fronts([b,a])=={('a',1):1,('b',2):2}
    fronts=assign_pareto_fronts([a,b]); assert rank_eligible_candidates([a,b],fronts)==rank_eligible_candidates([b,a],fronts)
