"""Handler-level contracts for Discovery ablation research routes."""
from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import pytest

import dashboard.paper_api as paper_api
from dashboard.discovery_ablation_service import DiscoveryAblationService

START = '/api/discovery/ablation/runs'


def handler(path, body=b'{}', authorization=None):
    captured = []
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    item = object.__new__(paper_api.Handler)
    item.path, item.headers, item.rfile = path, {'Content-Length': str(len(raw))}, BytesIO(raw)
    item.client_address = ('127.0.0.1', 4321)
    if authorization:
        item.headers['Authorization'] = authorization
    item._send = lambda payload, status=200: captured.append((payload, int(status)))
    return item, captured


def post(monkeypatch, body, start):
    item, captured = handler(START, body)
    monkeypatch.setattr(paper_api, 'RESEARCH', SimpleNamespace(discovery_ablation=SimpleNamespace(start=start)))
    item.do_POST()
    return captured[-1]


def validated_start(calls):
    def start(payload, client):
        calls.append((payload, client))
        DiscoveryAblationService.__new__(DiscoveryAblationService)._request(payload)
        return {'id': 11, 'status': 'QUEUED'}
    return start


def test_ablation_start_post_uses_handler_parser_and_client(monkeypatch):
    calls = []
    payload = {'discovery_run_id': 7, 'top_k': 5, 'maximum_candidates': 10}
    assert post(monkeypatch, payload, validated_start(calls)) == ({'id': 11, 'status': 'QUEUED'}, 202)
    assert calls == [(payload, '127.0.0.1')]


def test_ablation_start_minimum_request_preserves_service_defaults(monkeypatch):
    calls = []
    assert post(monkeypatch, {'discovery_run_id': 7}, validated_start(calls))[1] == 202
    assert calls == [({'discovery_run_id': 7}, '127.0.0.1')]


@pytest.mark.parametrize('payload', [
    {}, {'discovery_run_id': 0}, {'discovery_run_id': -1}, {'discovery_run_id': True},
    {'discovery_run_id': '7'}, {'discovery_run_id': None},
    {'discovery_run_id': 7, 'top_k': 0}, {'discovery_run_id': 7, 'top_k': 21},
    {'discovery_run_id': 7, 'top_k': True}, {'discovery_run_id': 7, 'top_k': '5'},
    {'discovery_run_id': 7, 'maximum_candidates': 0}, {'discovery_run_id': 7, 'maximum_candidates': 21},
    {'discovery_run_id': 7, 'maximum_candidates': True}, {'discovery_run_id': 7, 'maximum_candidates': '10'},
])
def test_ablation_start_validation_errors_map_to_400(monkeypatch, payload):
    assert post(monkeypatch, payload, validated_start([]))[1] == 400


def test_ablation_start_malformed_json_value_error_overflow_and_safe_internal_error(monkeypatch):
    assert post(monkeypatch, b'{', lambda *_: {}) == ({'error': 'Invalid JSON body'}, 400)
    assert post(monkeypatch, {'discovery_run_id': 7}, lambda *_: (_ for _ in ()).throw(ValueError('safe validation failure'))) == ({'error': 'safe validation failure'}, 400)
    assert post(monkeypatch, {'discovery_run_id': 7}, lambda *_: (_ for _ in ()).throw(OverflowError('Research job queue is full.'))) == ({'error': 'Research job queue is full.'}, 429)
    assert post(monkeypatch, {'discovery_run_id': 7}, lambda *_: (_ for _ in ()).throw(RuntimeError('database path secret'))) == ({'error': 'Internal server error'}, 500)


def test_ablation_get_list_uses_service_and_preserves_structured_rows(monkeypatch):
    calls = []
    service = SimpleNamespace(list_runs=lambda: calls.append('list') or [
        {'id': 11, 'request': {'discovery_run_id': 7}, 'policy': {'version': 'v1'}},
        {'id': 12, 'result': {'completed_scenario_count': 3}},
    ])
    item, captured = handler(START)
    monkeypatch.setattr(paper_api, 'RESEARCH', SimpleNamespace(discovery_ablation=service))
    item.do_GET()
    assert calls == ['list']
    assert captured[-1] == ({'items': [
        {'id': 11, 'request': {'discovery_run_id': 7}, 'policy': {'version': 'v1'}},
        {'id': 12, 'result': {'completed_scenario_count': 3}},
    ]}, 200)


def test_ablation_get_detail_missing_and_bad_ids(monkeypatch):
    calls = []
    detail = {'id': 11, 'request': {'discovery_run_id': 7}, 'policy': {'version': 'v1'},
              'progress': {'stage': 'ablation'}, 'result': {'completed_scenario_count': 1},
              'selected_candidates': [4], 'scenarios': [{'aggregate_metrics': {'total_return': 1}}]}
    service = SimpleNamespace(run_detail=lambda rid: calls.append(rid) or detail)
    item, captured = handler(START + '/11')
    monkeypatch.setattr(paper_api, 'RESEARCH', SimpleNamespace(discovery_ablation=service))
    item.do_GET()
    assert calls == [11] and captured[-1] == (detail, 200)
    missing, captured = handler(START + '/12')
    monkeypatch.setattr(paper_api, 'RESEARCH', SimpleNamespace(discovery_ablation=SimpleNamespace(run_detail=lambda _: None)))
    missing.do_GET()
    assert captured[-1] == ({'error': 'Ablation run not found'}, 404)
    for bad in ('not-an-id', '0', '-1'):
        item, captured = handler(START + '/' + bad)
        item.do_GET()
        assert captured[-1] == ({'error': 'Invalid ablation run id'}, 400)


def test_ablation_cancel_uses_service_once_and_preserves_terminal_result(monkeypatch):
    calls, terminal = [], {'id': 31, 'status': 'COMPLETED'}
    service = SimpleNamespace(cancel=lambda rid: calls.append(rid) or terminal)
    item, captured = handler(START + '/11/cancel')
    monkeypatch.setattr(paper_api, 'RESEARCH', SimpleNamespace(discovery_ablation=service))
    item.do_POST()
    assert calls == [11] and captured[-1] == (terminal, 200)


def test_ablation_cancel_missing_and_bad_ids(monkeypatch):
    missing, captured = handler(START + '/11/cancel')
    service = SimpleNamespace(cancel=lambda _: (_ for _ in ()).throw(ValueError('Ablation run not found.')))
    monkeypatch.setattr(paper_api, 'RESEARCH', SimpleNamespace(discovery_ablation=service))
    missing.do_POST()
    assert captured[-1] == ({'error': 'Ablation run not found.'}, 404)
    for bad in ('not-an-id', '0', '-1'):
        item, captured = handler(START + '/' + bad + '/cancel')
        item.do_POST()
        assert captured[-1] == ({'error': 'Invalid ablation run id'}, 400)


def test_ablation_write_routes_require_admin_and_do_not_call_service(monkeypatch):
    calls = []
    monkeypatch.setenv('ADMIN_TOKEN', 'secret')
    item, captured = handler(START, {'discovery_run_id': 7})
    monkeypatch.setattr(paper_api, 'RESEARCH', SimpleNamespace(discovery_ablation=SimpleNamespace(start=lambda *_: calls.append('start'))))
    item.do_POST()
    assert not calls and captured[-1] == ({'error': 'Admin authorization required.'}, 401)
    item, captured = handler(START + '/11/cancel')
    monkeypatch.setattr(paper_api, 'RESEARCH', SimpleNamespace(discovery_ablation=SimpleNamespace(cancel=lambda *_: calls.append('cancel'))))
    item.do_POST()
    assert not calls and captured[-1] == ({'error': 'Admin authorization required.'}, 401)


def test_ablation_routes_have_no_activation_promotion_or_order_dependency(monkeypatch):
    item, captured = handler(START)
    monkeypatch.setattr(paper_api, 'RESEARCH', SimpleNamespace(discovery_ablation=SimpleNamespace(list_runs=lambda: [{'id': 1}])))
    item.do_GET()
    assert captured[-1] == ({'items': [{'id': 1}]}, 200)
