"""Public Discovery start payload validation (the HTTP route maps ValueError to 400)."""
from __future__ import annotations
from io import BytesIO
from types import SimpleNamespace
import pytest
from dashboard.discovery_service import DiscoveryService
import dashboard.paper_api as paper_api

def payload():
    return {"instrument":"SOL-USDT","timeframe":"4H","execution_assumptions":{},"templates":["TREND_BREAKOUT"],"trial_budget":1,"seed":1}

@pytest.mark.parametrize("field",["instrument","timeframe","execution_assumptions","templates","trial_budget","seed"])
def test_discovery_start_contract_requires_each_public_field(field):
    service=DiscoveryService.__new__(DiscoveryService); request=payload(); request.pop(field)
    with pytest.raises(ValueError, match="Discovery runs require"):
        service._request(request)

def test_discovery_start_contract_accepts_supported_sol_partition_request_and_rejects_bad_context():
    service=DiscoveryService.__new__(DiscoveryService)
    assert service._request(payload())[:3] == ("SOL-USDT","4H",1)
    for key,value in (("instrument","DOGE-USDT"),("timeframe","5m")):
        request=payload(); request[key]=value
        with pytest.raises(ValueError, match="Unsupported"):
            service._request(request)

def post_route(monkeypatch, body, start):
    """Execute Handler.do_POST with its real parser/body/admin/route branch."""
    captured=[]
    handler=object.__new__(paper_api.Handler)
    raw=body if isinstance(body,bytes) else __import__('json').dumps(body).encode()
    handler.path='/api/discovery/runs'; handler.headers={'Content-Length':str(len(raw))}
    handler.rfile=BytesIO(raw); handler.client_address=('127.0.0.1',0)
    handler._send=lambda payload,status=200: captured.append((payload,int(status)))
    monkeypatch.setattr(paper_api,'RESEARCH',SimpleNamespace(discovery=SimpleNamespace(start=start)))
    handler.do_POST()
    return captured[-1]

def test_discovery_post_route_accepts_valid_request(monkeypatch):
    assert post_route(monkeypatch,payload(),lambda request,client:{'id':7,'status':'QUEUED'}) == ({'id':7,'status':'QUEUED'},202)

@pytest.mark.parametrize('field',["instrument","timeframe","execution_assumptions","templates","trial_budget","seed"])
def test_discovery_post_route_rejects_each_missing_required_field(monkeypatch,field):
    request=payload(); request.pop(field)
    result=post_route(monkeypatch,request,lambda request,client: DiscoveryService.__new__(DiscoveryService)._request(request))
    assert result[1] == 400 and result[0]['error'].startswith('Discovery runs require')

def test_discovery_post_route_rejects_malformed_json(monkeypatch):
    assert post_route(monkeypatch,b'{',lambda request,client:{}) == ({'error':'Invalid JSON body'},400)

def test_discovery_post_route_returns_service_value_error_deterministically(monkeypatch):
    assert post_route(monkeypatch,payload(),lambda request,client: (_ for _ in ()).throw(ValueError('deterministic failure'))) == ({'error':'deterministic failure'},400)

def test_discovery_post_route_maps_queue_overflow_to_429(monkeypatch):
    assert post_route(monkeypatch,payload(),lambda request,client: (_ for _ in ()).throw(OverflowError('Research job queue is full.'))) == ({'error':'Research job queue is full.'},429)
