"""SQLite contracts for the persistent Discovery component-ablation worker."""
import json
from pathlib import Path

from dashboard.discovery_ablation_service import (DISCOVERY_ABLATION_RUN_VERSION,
    DiscoveryAblationService, select_ablation_candidates)
from dashboard.discovery_execution import DiscoveryExecutionConfig
from dashboard.discovery_identity import build_parameter_identity
from dashboard.discovery_scoring import (DISCOVERY_ELIGIBILITY_VERSION,
    DISCOVERY_PARETO_VERSION, DISCOVERY_SCORING_VERSION)
from dashboard.discovery_service import DISCOVERY_AGGREGATION_VERSION
from dashboard.job_queue import JobQueue
from dashboard.research_repository import ResearchRepository

P={'fast_period':10,'slow_period':100,'fast_ma_type':'EMA','atr_period':14,'volume_enabled':False}

def test_candidate_selection_is_rank_ordered_and_front_one_is_retained():
 rows=[{'id':1,'eligibility_status':'ELIGIBLE','eligible_rank':3,'pareto_rank':1},
       {'id':2,'eligibility_status':'ELIGIBLE','eligible_rank':1,'pareto_rank':2},
       {'id':3,'eligibility_status':'ELIGIBLE','eligible_rank':2,'pareto_rank':1},
       {'id':4,'eligibility_status':'REJECTED','eligible_rank':0,'pareto_rank':1}]
 assert [x['id'] for x in select_ablation_candidates(rows,2,20)]==[3,1]
 assert [x['id'] for x in select_ablation_candidates(rows,2,2)]==[3,1]

def test_zero_eligible_candidates_completes_in_real_sqlite(tmp_path):
 repo=ResearchRepository(Path(tmp_path)/'research.sqlite'); jobs=JobQueue(Path(tmp_path)/'jobs.sqlite',autostart=False)
 service=DiscoveryAblationService(repo,jobs); now='2025-05-01T00:00:00+00:00'
 policy={'execution_assumptions':DiscoveryExecutionConfig().__dict__,'aggregation_version':DISCOVERY_AGGREGATION_VERSION,'eligibility_version':DISCOVERY_ELIGIBILITY_VERSION,'scoring_version':DISCOVERY_SCORING_VERSION,'pareto_version':DISCOVERY_PARETO_VERSION}
 with repo.connect() as c:
  dataset=c.execute("INSERT INTO discovery_datasets(name,start_ts,end_ts,instruments,timeframes,source,status,manifest,dataset_fingerprint,created_at,updated_at) VALUES('d',1,2,'[]','[]','x','COMPLETE','{}','d',?,?)",(now,now)).lastrowid
  c.execute("INSERT INTO discovery_dataset_partitions(dataset_id,instrument,timeframe,expected_rows,actual_rows,missing_rows,duplicate_rows,fingerprint,status) VALUES(?,?,?,?,?,?,?,?,?)",(dataset,'BTC-USDT','15m',1,1,0,0,'fp','COMPLETE'))
  source=c.execute("INSERT INTO strategy_discovery_runs(dataset_id,status,request,search_policy,sampler,seed,maximum_trials,templates,feature_version,engine_version,scoring_version,progress,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(dataset,'COMPLETED',json.dumps({'instrument':'BTC-USDT','timeframe':'15m'}),json.dumps(policy),'s',1,1,'[]','f','e','p','{}',now,now)).lastrowid
  c.execute("INSERT INTO strategy_discovery_candidates(discovery_run_id,candidate_number,template,template_version,parameters,parameter_hash,feature_flags,complexity,status,aggregate_metrics,eligibility_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(source,1,'TREND_BREAKOUT','x',json.dumps(P),build_parameter_identity('TREND_BREAKOUT',P),'{}',1,'COMPLETED','{}','REJECTED',now))
 started=service.start({'discovery_run_id':source})
 service._run(started['job_id'],{'discovery_run_id':source,'top_k':10,'maximum_candidates':20,'ablation_run_id':started['id']},lambda *a,**k:None)
 result=service.run_detail(started['id'])
 assert result['status']=='COMPLETED' and result['result']['selected_candidate_count']==0
 assert 'NO_ELIGIBLE_CANDIDATES_FOR_ABLATION' in result['result']['warnings']
 assert result['policy']['ablation_run_version']==DISCOVERY_ABLATION_RUN_VERSION
