"""Handler-level contracts for Discovery robustness research routes."""
from __future__ import annotations
import json
from io import BytesIO
from types import SimpleNamespace
import pytest
import dashboard.paper_api as paper_api
from dashboard.discovery_robustness_service import DiscoveryRobustnessService

START='/api/discovery/robustness/runs'

def handler(path, body=b'{}', authorization=None):
 captured=[]; raw=body if isinstance(body,bytes) else json.dumps(body).encode()
 item=object.__new__(paper_api.Handler); item.path=path; item.headers={'Content-Length':str(len(raw))}; item.rfile=BytesIO(raw); item.client_address=('127.0.0.1',4321)
 if authorization: item.headers['Authorization']=authorization
 item._send=lambda payload,status=200: captured.append((payload,int(status)))
 return item,captured

def post(monkeypatch, body, start):
 item,captured=handler(START,body); monkeypatch.setattr(paper_api,'RESEARCH',SimpleNamespace(discovery_robustness=SimpleNamespace(start=start)))
 item.do_POST(); return captured[-1]

def validated_start(calls):
 def start(payload,client):
  calls.append((payload,client)); DiscoveryRobustnessService.__new__(DiscoveryRobustnessService)._request(payload)
  return {'id':11,'status':'QUEUED'}
 return start

def test_robustness_start_post_uses_handler_parser_and_client(monkeypatch):
 calls=[]; payload={'discovery_run_id':7,'top_k':5,'maximum_candidates':10,'include_parameter_neighbors':True,'include_cost_stress':True}
 assert post(monkeypatch,payload,validated_start(calls))==({'id':11,'status':'QUEUED'},202)
 assert calls==[(payload,'127.0.0.1')]

def test_robustness_start_minimum_request_preserves_defaults_for_service(monkeypatch):
 calls=[]; assert post(monkeypatch,{'discovery_run_id':7},validated_start(calls))[1]==202
 assert calls[0][0]=={'discovery_run_id':7}

@pytest.mark.parametrize('payload',[{}, {'discovery_run_id':0},{'discovery_run_id':-1},{'discovery_run_id':True},{'discovery_run_id':'7'},
 ({'discovery_run_id':7,'top_k':0}),({'discovery_run_id':7,'top_k':21}),({'discovery_run_id':7,'top_k':True}),({'discovery_run_id':7,'top_k':'5'}),
 ({'discovery_run_id':7,'maximum_candidates':0}),({'discovery_run_id':7,'maximum_candidates':21}),({'discovery_run_id':7,'maximum_candidates':True}),({'discovery_run_id':7,'maximum_candidates':'10'}),
 ({'discovery_run_id':7,'include_parameter_neighbors':False,'include_cost_stress':False})])
def test_robustness_start_validation_errors_map_to_400(monkeypatch,payload):
 assert post(monkeypatch,payload,validated_start([]))[1]==400

def test_robustness_start_malformed_json_value_error_and_overflow(monkeypatch):
 assert post(monkeypatch,b'{',lambda *_:{})==({'error':'Invalid JSON body'},400)
 assert post(monkeypatch,{'discovery_run_id':7},lambda *_:(_ for _ in ()).throw(ValueError('deterministic robustness validation failure')))==({'error':'deterministic robustness validation failure'},400)
 assert post(monkeypatch,{'discovery_run_id':7},lambda *_:(_ for _ in ()).throw(OverflowError('Research job queue is full.')))==({'error':'Research job queue is full.'},429)

def test_robustness_get_list_uses_robustness_service(monkeypatch):
 calls=[]; item,captured=handler(START)
 service=SimpleNamespace(list_runs=lambda: calls.append('list') or [{'id':11,'request':{'discovery_run_id':7}},{'id':12,'result':{'completed_scenario_count':3}}])
 monkeypatch.setattr(paper_api,'RESEARCH',SimpleNamespace(discovery_robustness=service))
 item.do_GET(); assert calls==['list'] and captured[-1][1]==200 and captured[-1][0]['items'][1]['result']['completed_scenario_count']==3

def test_robustness_get_detail_missing_and_bad_id(monkeypatch):
 calls=[]; item,captured=handler(START+'/11')
 service=SimpleNamespace(run_detail=lambda rid: calls.append(rid) or {'id':rid,'request':{'discovery_run_id':7},'progress':{'stage':'x'},'result':{'completed_scenario_count':1},'selected_candidates':[4],'scenarios':[{'aggregate_metrics':{'total_return':1}}]})
 monkeypatch.setattr(paper_api,'RESEARCH',SimpleNamespace(discovery_robustness=service)); item.do_GET()
 assert calls==[11] and captured[-1][1]==200 and captured[-1][0]['scenarios'][0]['aggregate_metrics']['total_return']==1
 missing,captured=handler(START+'/12'); monkeypatch.setattr(paper_api,'RESEARCH',SimpleNamespace(discovery_robustness=SimpleNamespace(run_detail=lambda _:None))); missing.do_GET(); assert captured[-1]==({'error':'Robustness run not found'},404)
 bad,captured=handler(START+'/not-an-id'); bad.do_GET(); assert captured[-1]==({'error':'Invalid robustness run id'},400)

def test_robustness_cancel_uses_service_and_preserves_terminal_result(monkeypatch):
 calls=[]; item,captured=handler(START+'/11/cancel')
 terminal={'id':31,'status':'COMPLETED'}; service=SimpleNamespace(cancel=lambda rid: calls.append(rid) or terminal)
 monkeypatch.setattr(paper_api,'RESEARCH',SimpleNamespace(discovery_robustness=service)); item.do_POST()
 assert calls==[11] and captured[-1]==(terminal,200)

def test_robustness_cancel_missing_and_bad_id(monkeypatch):
 missing,captured=handler(START+'/11/cancel'); service=SimpleNamespace(cancel=lambda _:(_ for _ in ()).throw(ValueError('Robustness run not found.')))
 monkeypatch.setattr(paper_api,'RESEARCH',SimpleNamespace(discovery_robustness=service)); missing.do_POST(); assert captured[-1]==({'error':'Robustness run not found.'},404)
 bad,captured=handler(START+'/bad/cancel'); bad.do_POST(); assert captured[-1]==({'error':'Invalid robustness run id'},400)

def test_robustness_write_routes_require_admin_and_do_not_call_service(monkeypatch):
 calls=[]; monkeypatch.setenv('ADMIN_TOKEN','secret'); item,captured=handler(START,{'discovery_run_id':7})
 monkeypatch.setattr(paper_api,'RESEARCH',SimpleNamespace(discovery_robustness=SimpleNamespace(start=lambda *_:calls.append('start')))); item.do_POST()
 assert not calls and captured[-1]==({'error':'Admin authorization required.'},401)

def test_robustness_routes_have_no_activation_or_order_dependency(monkeypatch):
 item,captured=handler(START); service=SimpleNamespace(list_runs=lambda:[{'id':1}])
 monkeypatch.setattr(paper_api,'RESEARCH',SimpleNamespace(discovery_robustness=service)); item.do_GET()
 assert captured[-1]==({'items':[{'id':1}]},200)
