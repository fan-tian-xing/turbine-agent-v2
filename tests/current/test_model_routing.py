"""Explicit routing without live network calls or credential changes."""
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from turbine_kg.agent import model as transport
from turbine_kg.runtime import Budget


def client(monkeypatch, enabled, stages=None, missing_model=None):
    config = {'transport': {'transient_attempts': 2, 'backoff_seconds': 0,
                            'max_backoff_seconds': 0},
              'model_routing': {'enabled_provider_prefixes': enabled}}
    if stages is not None:
        config['model_routing']['stage_models'] = stages
    class ConfigPath:
        def __truediv__(self, _): return self
        def read_text(self, **_): return json.dumps(config)
    monkeypatch.setattr(transport, 'PROJECT_ROOT', ConfigPath())
    settings = SimpleNamespace(llm_allow_evidence_send=True)
    for index, prefix in enumerate(transport.ModelClient.PROVIDER_PREFIXES):
        for suffix, value in zip(('_base_url', '_model', '_api_key', '_timeout_seconds'),
                                 (f'https://provider-{index}.test', f'model-{index}', 'fake-key', 10)):
            setattr(settings, prefix + suffix, value)
    if missing_model:
        setattr(settings, missing_model + '_model', '')
    return transport.ModelClient(settings)


def success():
    return {'choices': [{'message': {'content': '{"ok":true}'}}]}


def test_routing_preserves_inventory_and_requests_only_enabled_prefix(monkeypatch):
    model = client(monkeypatch, ['llm_fallback'])
    assert len(model.providers) == 3
    assert model.provider_prefixes == ['llm', 'llm_fallback', 'llm_fallback_2']
    assert model.execution_policy == {'model_routing': {'enabled_provider_prefixes': ['llm_fallback'], 'stage_models': {}}}
    calls = []
    model._request = lambda request, timeout: calls.append(request.full_url) or success()
    assert model.call('system', 'input', Budget()) == {'ok': True}
    assert calls == ['https://provider-1.test/chat/completions']


def test_primary_transient_failure_does_not_request_disabled_inventory(monkeypatch):
    model = client(monkeypatch, ['llm'])
    calls = []
    def request(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 503, 'unavailable', {}, None)
    model._request = request
    with pytest.raises(transport.ModelError, match='HTTP 503'):
        model.call('system', 'input', Budget())
    assert calls == ['https://provider-0.test/chat/completions'] * 2
    assert len(model.providers) == 3


def test_url_failure_retries_same_enabled_provider(monkeypatch):
    model = client(monkeypatch, ['llm'])
    calls = []
    def request(http_request, timeout):
        calls.append(http_request.full_url)
        if len(calls)==1:raise URLError('temporary connection failure')
        return success()
    model._request = request
    assert model.call('system', 'input', Budget()) == {'ok': True}
    assert calls == ['https://provider-0.test/chat/completions'] * 2


@pytest.mark.parametrize('code', [401, 403, 402])
def test_enabled_provider_auth_or_quota_failure_stops_without_fallback(monkeypatch, code):
    model = client(monkeypatch, list(transport.ModelClient.PROVIDER_PREFIXES))
    calls = []
    def request(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, code, 'failure', {}, None)
    model._request = request
    with pytest.raises(transport.ModelError, match=f'HTTP {code}'):
        model.call('system', 'input', Budget())
    assert len(calls) == 1


def test_success_status_quota_error_is_not_a_fallback_trigger(monkeypatch):
    model = client(monkeypatch, list(transport.ModelClient.PROVIDER_PREFIXES))
    calls = []
    model._request = lambda request, timeout: calls.append(request.full_url) or {
        'error': {'code': 'insufficient_quota', 'message': 'private response'}}
    with pytest.raises(transport.ModelQuotaError, match='额度不足'):
        model.call('system', 'input', Budget())
    assert len(calls) == 1


def test_empty_routing_sends_no_request(monkeypatch):
    model = client(monkeypatch, [])
    model._request = lambda *_: pytest.fail('disabled providers must not receive a request')
    with pytest.raises(transport.ModelError):
        model.call('system', 'input', Budget())
    assert len(model.providers) == 3


@pytest.mark.parametrize('enabled', ['llm', ['unknown']])
def test_invalid_routing_fails_before_transport(monkeypatch, enabled):
    with pytest.raises(ValueError, match='前缀列表'):
        client(monkeypatch, enabled)


def test_extraction_stage_keeps_inventory_endpoint_budget_and_default_model(monkeypatch):
    stages = {'extraction': {'llm': 'llm_fallback'}}
    model = client(monkeypatch, ['llm'], stages)
    calls = []
    def request(request, timeout):
        calls.append((request.full_url, json.loads(request.data)['model'], timeout))
        return success()
    model._request = request
    budget = Budget(seconds=30)
    view = model.for_stage('extraction')
    assert view.providers is model.providers
    assert view.provider_prefixes is model.provider_prefixes
    assert view.policy is model.policy
    assert view.vision_capability is model.vision_capability
    assert view.last_summary is not model.last_summary
    assert view.call('s', 'u', budget, reserve_seconds=5) == {'ok': True}
    assert view.last_summary['model'] == 'model-1'
    assert model.last_summary == {}
    assert model.call('s', 'u', budget) == {'ok': True}
    assert [(endpoint, name) for endpoint, name, _ in calls] == [
        ('https://provider-0.test/chat/completions', 'model-1'),
        ('https://provider-0.test/chat/completions', 'model-0')]
    assert all(timeout <= 10 for _, _, timeout in calls)
    assert budget.seconds == 30
    assert [p[1] for p in model.providers] == ['model-0', 'model-1', 'model-2']
    assert model.execution_policy['model_routing']['stage_models'] == stages
    assert model.for_stage('answer')._routed_providers()[0][1] == 'model-0'


@pytest.mark.parametrize('stages', [[], {'generation': []}, {'generation': {'unknown': 'llm'}},
                                  {'generation': {'llm': 'unknown'}}])
def test_invalid_stage_prefix_fails_before_requests(monkeypatch, stages):
    with pytest.raises(ValueError, match='stage_models'):
        client(monkeypatch, ['llm'], stages)


def test_unconfigured_stage_model_fails_before_requests(monkeypatch):
    with pytest.raises(ValueError, match='尚未配置'):
        client(monkeypatch, ['llm'], {'generation': {'llm': 'llm_fallback'}}, missing_model='llm_fallback')


def test_stage_factory_keeps_legacy_transport_double(monkeypatch):
    model = transport.ModelClient.__new__(transport.ModelClient)
    model.providers = [['https://legacy.test', 'legacy-model', 'fake-key', 10]]
    model.settings = SimpleNamespace(llm_allow_evidence_send=True)
    model.last_summary = {}
    view = model.for_stage('extraction')
    assert view.providers is model.providers
    assert view._routed_providers() == model.providers
    with pytest.raises(ValueError, match='非空'):
        model.for_stage('')


def test_routing_gateway_error_is_retryable():
    with pytest.raises(transport.ModelTransientError):
        transport.ModelClient._completion({'error': 'Unable to retrieve routing information'})


def test_routing_gateway_error_retries_before_returning_success(monkeypatch):
    model = client(monkeypatch, ['llm'])
    calls = []
    def request(http_request, timeout):
        calls.append(http_request.full_url)
        return {'error': 'Unable to retrieve routing information'} if len(calls) == 1 else success()
    model._request = request
    assert model.call('system', 'input', Budget()) == {'ok': True}
    assert calls == ['https://provider-0.test/chat/completions'] * 2
