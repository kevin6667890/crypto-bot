"""Synthetic/no-network persistence checks for Discovery scoring integration."""
from __future__ import annotations

import json
import pytest

import dashboard.discovery_service as discovery
from dashboard.discovery_service import DiscoveryService, FOLDS
from dashboard.job_queue import JobCancelled, JobQueue
from dashboard.research_repository import ResearchRepository
from dashboard.discovery_identity import build_parameter_identity, build_candidate_identity, build_evaluation_identity

STEP=900
def setup(tmp_path):
    repo=ResearchRepository(tmp_path/'research.db'); start=FOLDS[0][0]; end=FOLDS[-1][3]
    rows=[{'ts':t,'open':100.,'high':102.,'low':99.,'close':101.,'volume':10.,'confirmed':1} for t in range(start,end,STEP)]
    repo.upsert_candles('ETH-USDT','15m',rows); ds=repo.create_or_get_discovery_dataset('synthetic',start,end,['ETH-USDT'],['15m'])
    repo.upsert_discovery_partition(ds['id'],'ETH-USDT','15m',rows,{'expected_rows':len(rows),'actual_rows':len(rows),'missing_rows':0,'duplicate_rows':0,'fingerprint':'f','status':'COMPLETE'})
    ds=repo.finish_discovery_dataset(ds['id']); service=DiscoveryService(repo,JobQueue(tmp_path/'jobs.db',autostart=False))
    rid=service.start({'dataset_id':ds['id'],'instrument':'ETH-USDT','timeframe':'15m','templates':['TREND_BREAKOUT'],'trial_budget':1,'seed':7,'execution_assumptions':{'trading_fee':.001,'slippage':.002}})['id']
    return repo,service,rid

def fake_engine(calls):
    def run(candles,instrument,timeframe,template,parameters,start_ts,end_ts,execution,dataset_fingerprint):
        calls.append(start_ts); ph=build_parameter_identity(template,parameters); eh=execution.execution_hash(); ch=build_candidate_identity(template,parameters,eh)
        return {'metrics':{'initial_capital':10000.,'final_equity':10100.,'net_profit':100.,'total_return':1.,'annualized_return':None,'total_trades':1,'win_rate':100.,'profit_factor':None,'expectancy':1.,'average_win':1.,'average_loss':None,'realized_risk_reward':None,'maximum_drawdown':2.,'sharpe_ratio':None,'sortino_ratio':None,'consecutive_wins':1,'consecutive_losses':0,'fees_paid':2.,'long_trades':1,'short_trades':0,'average_holding_seconds':900.,'sample_note':'synthetic'},'signal_count':1,'discovery_evidence':{'parameter_hash':ph,'execution_hash':eh,'candidate_config_hash':ch,'evaluation_hash':build_evaluation_identity(ch,instrument,timeframe,start_ts,end_ts,dataset_fingerprint),'template_version':'v','feature_version':'f','execution_policy_version':'x','execution_engine_version':'y','execution':execution.__dict__}}
    return run


def payload(rid):
    return {'discovery_run_id':rid,'dataset_id':1,'instrument':'ETH-USDT','timeframe':'15m','templates':['TREND_BREAKOUT'],'trial_budget':1,'seed':7,'execution_assumptions':{'trading_fee':.001,'slippage':.002}}


def test_rejected_candidate_persists_reasons_without_scores_or_ranks(tmp_path, monkeypatch):
    repo, service, rid = setup(tmp_path); monkeypatch.setattr(discovery, 'run_discovery_candidate_backtest', fake_engine([]))
    service._run_job(1, payload(rid), lambda *_: None)
    with repo.connect() as c:
        candidate = dict(c.execute('SELECT * FROM strategy_discovery_candidates').fetchone())
        result = json.loads(c.execute('SELECT result FROM strategy_discovery_runs WHERE id=?', (rid,)).fetchone()[0])
    assert candidate['eligibility_status'] == 'REJECTED'
    assert candidate['development_score'] is candidate['eligible_rank'] is candidate['pareto_rank'] is None
    assert json.loads(candidate['elimination_reasons']) and result['eligible_candidate_count'] == 0
    assert all(result[key] for key in ('aggregation_version','eligibility_version','scoring_version','pareto_version'))


def test_final_pre_persistence_cancellation_leaves_interpretation_empty(tmp_path, monkeypatch):
    repo, service, rid = setup(tmp_path); monkeypatch.setattr(discovery, 'run_discovery_candidate_backtest', fake_engine([]))
    def checkpoint(_, __, message, ___):
        if message == 'discovery.persisting_development_ranking': raise JobCancelled()
    with pytest.raises(JobCancelled): service._run_job(1, payload(rid), checkpoint)
    with repo.connect() as c:
        candidate = dict(c.execute('SELECT * FROM strategy_discovery_candidates').fetchone())
        run = dict(c.execute('SELECT * FROM strategy_discovery_runs WHERE id=?', (rid,)).fetchone())
        folds = c.execute('SELECT COUNT(*) FROM strategy_discovery_folds').fetchone()[0]
    assert run['status'] == 'CANCELLED' and folds == 5
    assert candidate['development_score'] is candidate['eligible_rank'] is candidate['pareto_rank'] is None


def test_scoring_queries_no_extra_candles_after_evaluation(tmp_path, monkeypatch):
    repo, service, rid = setup(tmp_path); monkeypatch.setattr(discovery, 'run_discovery_candidate_backtest', fake_engine([]))
    calls=[]; original=repo.candles
    def spy(*args): calls.append(args); return original(*args)
    repo.candles=spy
    service._run_job(1, payload(rid), lambda *_: None)
    assert calls and all(end < discovery.ts(2025,5,1) for _,_,_,end in calls)
