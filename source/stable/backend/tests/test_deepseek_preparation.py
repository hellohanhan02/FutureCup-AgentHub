"""08-B: offline policy tests and SIMULATED HTTP only; never live inference."""

import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from futurecup.adapters.provider_config import profiles_from, safety_from
from futurecup.api.app import create_app
from futurecup.bootstrap import build_runtime, build_workflows
from futurecup.config import Settings
from futurecup.domain.llm import RealCallSafety, RetryPolicy
from futurecup.domain.models import RunLimits, NodeKind, VersionRef, WorkflowDefinition, WorkflowNode
from futurecup.domain.verification import ResourceBudget, VerificationPolicy, VerificationCheck
from runtime_support import agent
from test_real_llm_integration import profile, policy, managed, completion, KEY, FAKE

ROOT = Path(__file__).resolve().parents[2]
SAFETY = json.loads((ROOT / 'config/real-call-safety.json').read_text('utf-8'))


def settings(configuration=None, **changes):
    return Settings('test', 'sqlite:///:memory:', enable_real_llm=True,
        provider_config_json=json.dumps([asdict(configuration or profile())]),
        real_call_safety_json=json.dumps({**SAFETY, **changes}))


def runtime(handler, configuration=None, **changes):
    configuration = configuration or profile(models=(policy(input_price_per_million=1,
        output_price_per_million=1, currency='CNY'),))
    value = build_runtime(settings(configuration, **changes))
    value.providers[configuration.id] = managed(handler, configuration)
    return value


def test_safe_deepseek_configuration_default_off_and_offline_api():
    value = Settings('test', 'sqlite:///:memory:',
        provider_config_json=(ROOT / 'config/deepseek-safe.json').read_text('utf-8'),
        real_call_safety_json=json.dumps(SAFETY))
    p = next(p for p in profiles_from(value) if p.id == 'deepseek')
    assert not p.enabled and p.key_env == 'DEEPSEEK_API_KEY'
    assert p.base_url == 'https://api.deepseek.com' and p.retry.max_retries == 0
    assert p.models[0].id == 'deepseek-flash' and p.models[0].max_output_tokens == 1024
    assert p.models[0].thinking_disabled and not p.models[0].supports_tools
    assert p.models[0].input_price_per_million is None
    with TestClient(create_app(value)) as client:
        response = client.get('/api/v1/model-providers').json()
        assert response['preparation_safety'] == SAFETY
        assert not response['real_calls_enabled'] and not response['network_probe_performed']
        assert client.get('/api/v1/health').status_code == 200
        assert {p['id'] for p in response['providers']} == {'mock', 'deepseek', 'qwen', 'openai_compatible'}


@pytest.mark.parametrize('change', [
    {'max_output_tokens': 1025}, {'max_task_model_calls': 4}, {'max_workflow_model_calls': 11},
    {'max_feedback_loops': 3}, {'max_concurrency': 2}, {'estimated_workflow_cny': .51},
    {'max_concurrency': True}, {'estimated_workflow_cny': float('nan')}, {'api_key': 'never-reflect-me'},
])
def test_approved_safety_ceiling_cannot_be_expanded(change):
    with pytest.raises(ValueError) as failure:
        safety_from(settings(**change))
    assert 'never-reflect-me' not in str(failure.value)


@pytest.mark.parametrize('currency', [None, 'USD'])
def test_missing_or_wrong_currency_price_blocks_before_http(monkeypatch, currency):
    monkeypatch.setenv(KEY, FAKE)
    calls = []
    configuration = profile(models=(policy(**({'currency': currency, 'input_price_per_million': 1,
        'output_price_per_million': 1} if currency else {})),))
    async def scenario():
        value = runtime(lambda r: calls.append(r), configuration)
        try:
            await value.registry.register(agent(provider='configured', model='test-model'))
            result = await value.run('analyst', 'SIMULATED safety check')
            assert result.execution.error_code == 'cost_estimate_unavailable'
            assert not calls and not result.model_calls
        finally:
            await value.close()
    asyncio.run(scenario())


def test_token_and_retry_call_ceiling_enforced(monkeypatch):
    monkeypatch.setenv(KEY, FAKE)
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(429, json={'error': 'SIMULATED rate limit'})
    configuration = profile(models=(policy(input_price_per_million=1, output_price_per_million=1, currency='CNY'),),
                            retry=RetryPolicy(3, 0, 0))
    async def scenario():
        value = runtime(handler, configuration)
        try:
            await value.registry.register(agent(provider='configured', model='test-model'))
            result = await value.run('analyst', 'SIMULATED retry', RunLimits(max_model_calls=8, max_output_tokens=4096))
            assert result.limits.max_model_calls == 3 and result.limits.max_output_tokens == 1024
            assert result.execution.error_code == 'call_limit_exceeded'
            assert len(calls) == len(result.model_calls) == 3
            assert all(c['max_tokens'] == 1024 for c in calls)
        finally:
            await value.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('task_scope, count, code', [(False, 10, 'shared_model_budget'), (True, 3, 'task_model_budget')])
def test_shared_calls_include_attempts_and_nested_budgets(monkeypatch, task_scope, count, code):
    monkeypatch.setenv(KEY, FAKE)
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(200, json=completion())
    async def scenario():
        value = runtime(handler)
        budget = ResourceBudget()
        if task_scope:
            budget.task_model_call_ceiling = 3
        try:
            await value.registry.register(agent(provider='configured', model='test-model'))
            for _ in range(count + 1):
                report = await value.submit('analyst', 'SIMULATED shared budget', shared_budget=budget.fork())
                result = await value.wait(report.execution.id)
            assert len(calls) == count and budget.model_calls == count
            assert result.execution.error_code == code
        finally:
            await value.close()
    asyncio.run(scenario())


def test_estimate_reservation_is_shared_and_not_refunded_on_failure(monkeypatch):
    monkeypatch.setenv(KEY, FAKE)
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(500, json={'error': 'SIMULATED unknown billing'})
    async def scenario():
        value = runtime(handler, estimated_workflow_cny=.05)
        budget = ResourceBudget()
        try:
            await value.registry.register(agent(provider='configured', model='test-model'))
            for _ in range(2):
                report = await value.submit('analyst', 'SIMULATED cost', shared_budget=budget.fork())
                result = await value.wait(report.execution.id)
            assert len(calls) == 1 and result.execution.error_code == 'estimated_cost_budget'
            assert budget.estimated_cny_reserved == pytest.approx(.033792)
            assert not result.cost_complete and result.estimated_cost is None
        finally:
            await value.close()
    asyncio.run(scenario())


def test_global_real_gate_serializes_different_agents_and_releases_on_cancel(monkeypatch):
    monkeypatch.setenv(KEY, FAKE)
    active = peak = calls = 0
    entered = None
    async def handler(r):
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        entered.set()
        try:
            await asyncio.sleep(.05)
            return httpx.Response(200, json=completion())
        finally:
            active -= 1
    async def scenario():
        nonlocal entered
        entered = asyncio.Event()
        value = runtime(handler)
        try:
            for name in ('one', 'two', 'three'):
                await value.registry.register(agent(name, provider='configured', model='test-model'))
            first = await value.submit('one', 'SIMULATED concurrent')
            await entered.wait()
            second = await value.submit('two', 'SIMULATED queued')
            await value.cancel(first.execution.id)
            result = await value.wait(second.execution.id)
            third = await value.run('three', 'SIMULATED released gate')
            assert result.execution.state.value == third.execution.state.value == 'succeeded'
            assert peak == 1 and active == 0 and calls == 3
        finally:
            await value.close()
    asyncio.run(scenario())


def test_selfloop_is_capped_at_two_repairs_and_three_total_task_calls(monkeypatch):
    monkeypatch.setenv(KEY, FAKE)
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(200, json=completion('{"wrong":true}'))
    async def scenario():
        value = runtime(handler)
        engine = build_workflows(settings(), value)
        try:
            await value.registry.register(agent(provider='configured', model='test-model', response_format='prompt_json'))
            await engine.selfloop.verifier.register(VerificationPolicy(VersionRef('safe-schema', '1'), 'Frozen SIMULATED schema',
                (VerificationCheck('json_schema', {'schema': {'type': 'object', 'required': ['answer']}}),)))
            config = {'candidate_agents': ['analyst'], 'verification': {'id': 'safe-schema', 'version': '1'},
                'output_mode': 'json', 'recovery': {'max_attempts': 10, 'max_feedback_loops': 9, 'on_failure': 'prompt_retry'}}
            flow = WorkflowDefinition(VersionRef('safe-loop', '1'), (WorkflowNode('generate', NodeKind.TASK, config),), (), {}, {}, 4, name='SIMULATED bounded repair')
            await engine.register(flow)
            result = await engine.run(flow.ref, {'mode': 'SIMULATED'})
            assert result.run.state.value == 'failed' and not result.verified
            assert len(calls) == 3 and len(result.nodes[0].attempt_ids) == 3
            assert result.resource_usage['feedback_loops'] == 2
        finally:
            await engine.close()
            await value.close()
    asyncio.run(scenario())


def test_prepared_first_test_uses_actual_workflow_and_verifier_with_simulated_http(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', FAKE)
    configuration = json.loads((ROOT / 'config/deepseek-safe.json').read_text('utf-8'))
    configuration[0]['enabled'] = True
    configuration[0]['models'][0].update(input_price_per_million=1, output_price_per_million=1, currency='CNY')
    # The prices and credential above are explicitly synthetic, not official/account values.
    options = Settings('test', 'sqlite:///:memory:', enable_real_llm=True,
        provider_config_json=json.dumps(configuration), real_call_safety_json=json.dumps(SAFETY))
    value = build_runtime(options)
    deepseek = next(p for p in value.provider_profiles if p.id == 'deepseek')
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == 'https://api.deepseek.com/chat/completions'
        body = json.loads(request.content)
        assert body['max_tokens'] == 128 and body['thinking'] == {'type': 'disabled'}
        assert body['response_format'] == {'type': 'json_object'} and 'tools' not in body
        return httpx.Response(200, json=completion('{"status":"ok"}', model='SIMULATED-deepseek'))
    value.providers['deepseek'] = managed(handler, deepseek)
    definition = json.loads((ROOT / 'config/deepseek-first-test.json').read_text('utf-8'))
    from test_workflow_api import completed
    with TestClient(create_app(options, value)) as client:
        for path, key in [('agents', 'agent'), ('verification-policies', 'policy'), ('workflows', 'workflow')]:
            result = client.post('/api/v1/' + path, json=definition[key])
            assert result.status_code == 201, result.text
            assert FAKE not in result.text
        submitted = client.post('/api/v1/workflows/deepseek-08b-smoke/executions', json={'input': definition['input']})
        assert submitted.status_code == 202
        result = completed(client, submitted.json()['run']['id'])
        assert result['run']['state'] == 'succeeded' and result['verified'] and result['simulated']
        assert result['resource_usage']['model_calls'] == 1 and len(calls) == 1
        assert result['nodes'][0]['output'] == {'status': 'ok'}
        assert FAKE not in json.dumps(result)


def test_mock_does_not_require_prices_and_retains_original_limits_under_safe_mode():
    options = Settings('test', 'sqlite:///:memory:', real_call_safety_json=json.dumps(SAFETY))
    async def scenario():
        value = build_runtime(options)
        try:
            await value.registry.register(agent(model='mock-echo'))
            result = await value.run('analyst', 'Explicit MOCK regression', RunLimits(max_model_calls=8, max_output_tokens=2048))
            assert result.execution.state.value == 'succeeded' and result.simulated
            assert result.limits.max_model_calls == 8 and result.limits.max_output_tokens == 2048
        finally:
            await value.close()
    asyncio.run(scenario())
