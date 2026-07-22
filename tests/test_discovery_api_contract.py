"""Public Discovery start payload validation (the HTTP route maps ValueError to 400)."""
from __future__ import annotations
import pytest
from dashboard.discovery_service import DiscoveryService

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
