"""Synthetic credentials and MockTransport only; never inspect a live service key."""

import asyncio
from dataclasses import replace
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from futurecup.adapters.llm import OpenAICompatibleProvider
from futurecup.api.agents import AgentCreate
from futurecup.api.app import create_app
from futurecup.bootstrap import build_runtime
from futurecup.config import Settings
from futurecup.domain.errors import RuntimeFault
from test_real_llm_integration import completion
from test_serial_collaboration_09a import configuration, SimulatedCollaboration, register, run, ROOT, TEMPLATE
from test_workflow_api import completed

SYNTHETIC = 'synthetic-unit-credential-never-a-real-key'
ENV = 'DEEPSEEK_API_KEY'


def settings(*, key_env=ENV):
    value = configuration()
    profiles = json.loads(value.provider_config_json)
    profiles[0]['key_env'] = key_env
    return replace(value, provider_config_json=json.dumps(profiles))


def managed_runtime(options, handler):
    value = build_runtime(options)
    profile = next(p for p in value.provider_profiles if p.id == 'deepseek')
    value.providers['deepseek'] = OpenAICompatibleProvider(profile.base_url, profile.key_env, True,
        httpx.MockTransport(handler), profile=profile)
    return value


def register_single(client):
    definition = json.loads((ROOT / 'config/deepseek-first-test.json').read_text('utf-8'))
    for path, item in [('agents', definition['agent']), ('verification-policies', definition['policy']),
                       ('workflows', definition['workflow'])]:
        response = client.post('/api/v1/' + path, json=item)
        assert response.status_code == 201, response.text
    submitted = client.post('/api/v1/workflows/' + definition['workflow']['id'] + '/executions',
        json={'input': definition['input']})
    assert submitted.status_code == 202
    return completed(client, submitted.json()['run']['id'])


@pytest.mark.parametrize('change_environment', [False, True])
def test_single_and_serial_share_one_provider_and_read_current_environment(monkeypatch, change_environment):
    monkeypatch.setenv(ENV, SYNTHETIC)
    # Legacy/default sources deliberately invalid: they must not override DeepSeek.
    monkeypatch.setenv('FUTURECUP_LLM_API_KEY', 'synthetic invalid legacy source')
    monkeypatch.setenv('FUTURECUP_LLM_API_KEY_ENV', 'TASK09_LEGACY_ONLY')
    monkeypatch.setenv('TASK09_LEGACY_ONLY', 'synthetic invalid legacy override')
    simulator = SimulatedCollaboration()
    all_calls = []
    async def handler(request):
        all_calls.append(request)
        if len(all_calls) == 1:
            return httpx.Response(200, json=completion('{"status":"ok"}', model='SIMULATED-deepseek'))
        return await simulator(request)
    options = settings()
    runtime = managed_runtime(options, handler)
    provider = runtime.providers['deepseek']
    assert provider._api_key_env == ENV
    with TestClient(create_app(options, runtime)) as client:
        first = register_single(client)
        assert first['run']['state'] == 'succeeded' and first['simulated'] and first['verified']
        if change_environment:
            # Demonstrate runtime environment drift without changing Provider/config/Agent logic.
            monkeypatch.setenv(ENV, 'synthetic invalid internal space')
        second = run(client, register(client, 'same-source'))
        assert runtime.providers['deepseek'] is provider
        assert first['nodes'][0]['provider'] == second['nodes'][0]['provider'] == 'deepseek'
        assert first['nodes'][0]['model'] == second['nodes'][0]['model'] == 'deepseek-flash'
        if change_environment:
            assert second['run']['state'] == 'failed'
            assert [n['state'] for n in second['nodes']] == ['failed', 'skipped', 'skipped']
            call = second['nodes'][0]['model_calls'][0]
            assert call['error_code'] == 'provider_credentials_invalid' and call['http_status_code'] is None
            assert second['resource_usage']['model_calls'] == 1 and second['resource_usage']['checks'] == 0
            assert len(all_calls) == 1 and not simulator.requests  # No second HTTP invocation.
            catalog = client.get('/api/v1/model-providers').json()
            deepseek = next(p for p in catalog['providers'] if p['id'] == 'deepseek')
            assert deepseek['credentials_configured'] is True  # Presence is NOT format validity.
        else:
            assert second['run']['state'] == 'succeeded' and second['verified'] and second['simulated']
            assert len(all_calls) == 4 and len(simulator.requests) == 3
            assert all(n['model'] == 'deepseek-flash' for n in second['nodes'])
        assert first['run']['id'] != second['run']['id']
        assert SYNTHETIC not in json.dumps(first) + json.dumps(second)


@pytest.mark.parametrize('label,value,expected_error', [
    ('valid', SYNTHETIC, None),
    ('trimmed-boundaries', ' \t' + SYNTHETIC + '\r\n', None),
    ('variable-name-as-value', 'DEEPSEEK_API_KEY', None),
    ('ascii-placeholder', 'your-api-key', None),
    ('ascii-quoted', '"' + SYNTHETIC + '"', None),
    ('bearer-prefix', 'Bearer ' + SYNTHETIC, 'provider_credentials_invalid'),
    ('internal-space', 'synthetic invalid credential', 'provider_credentials_invalid'),
    ('internal-newline', 'synthetic\ncredential', 'provider_credentials_invalid'),
    ('non-ascii', '\u201c' + SYNTHETIC + '\u201d', 'provider_credentials_invalid'),
    ('empty', '', 'provider_credentials_missing'),
    ('only-whitespace', ' \t\n', 'provider_credentials_missing'),
])
def test_synthetic_format_cases_do_not_confuse_format_with_authentication(monkeypatch, label, value, expected_error):
    monkeypatch.setenv(ENV, SYNTHETIC)
    seen = []
    def handler(request):
        seen.append(True)
        return httpx.Response(200, json=completion('{"status":"ok"}', model='SIMULATED-deepseek'))
    async def scenario():
        service = managed_runtime(settings(), handler)
        try:
            # Isolate infer-time validation from startup's separate secret guard.
            monkeypatch.setenv(ENV, value)
            agent = AgentCreate(**TEMPLATE['agents'][0]).definition('synthetic-format-agent')
            await service.registry.register(agent)
            report = await service.run(agent.ref.id, 'Synthetic format-only check')
            assert report.execution.error_code == expected_error
            if expected_error:
                assert report.execution.state.value == 'failed' and not seen
                assert report.model_calls[0].http_status_code is None
            else:
                assert report.execution.state.value == 'succeeded' and seen
                # MockTransport acceptance proves format only; it does not authenticate any placeholder.
                assert report.simulated
            if value.strip():
                assert value.strip() not in repr(report)
        finally:
            await service.close()
    asyncio.run(scenario())


def test_literal_environment_name_as_value_is_rejected_by_startup_guard(monkeypatch):
    monkeypatch.setenv(ENV, ENV)  # Synthetic mistake, not a credential.
    with pytest.raises(RuntimeFault) as failure:
        build_runtime(settings())
    assert failure.value.code == 'sensitive_data_rejected'


def test_explicit_profile_key_env_takes_precedence_over_deepseek_and_legacy_defaults(monkeypatch):
    custom_name = 'TASK09_EXPLICIT_TEST_ONLY'
    monkeypatch.setenv(custom_name, SYNTHETIC)
    monkeypatch.setenv(ENV, 'invalid default source with spaces')
    monkeypatch.setenv('FUTURECUP_LLM_API_KEY', 'invalid legacy source with spaces')
    seen = []
    def handler(request):
        seen.append(True)
        return httpx.Response(200, json=completion('{"status":"ok"}'))
    async def scenario():
        runtime = managed_runtime(settings(key_env=custom_name), handler)
        try:
            definition = AgentCreate(**TEMPLATE['agents'][0]).definition('synthetic-explicit-source')
            await runtime.registry.register(definition)
            report = await runtime.run(definition.ref.id, 'Explicit test source')
            assert runtime.providers['deepseek']._api_key_env == custom_name
            assert report.execution.state.value == 'succeeded' and len(seen) == 1
        finally:
            await runtime.close()
    asyncio.run(scenario())


def test_process_settings_legacy_env_override_does_not_change_deepseek_source(monkeypatch):
    expected = settings()
    monkeypatch.setenv(ENV, SYNTHETIC)
    monkeypatch.setenv('FUTURECUP_ENABLE_REAL_LLM', 'true')
    monkeypatch.setenv('FUTURECUP_LLM_PROVIDERS_JSON', expected.provider_config_json)
    monkeypatch.setenv('FUTURECUP_REAL_CALL_SAFETY_JSON', expected.real_call_safety_json)
    monkeypatch.setenv('FUTURECUP_DATABASE_URL', 'sqlite:///:memory:')
    monkeypatch.setenv('FUTURECUP_LLM_API_KEY_ENV', 'TASK09_LEGACY_OVERRIDE')
    monkeypatch.setenv('TASK09_LEGACY_OVERRIDE', 'synthetic invalid legacy value')
    options = Settings.from_environment()
    assert options.llm_api_key_env == 'TASK09_LEGACY_OVERRIDE'
    seen = []
    def handler(request):
        seen.append(True)
        return httpx.Response(200, json=completion('{"status":"ok"}'))
    async def scenario():
        runtime = managed_runtime(options, handler)
        try:
            definition = AgentCreate(**TEMPLATE['agents'][0]).definition('synthetic-settings-source')
            await runtime.registry.register(definition)
            report = await runtime.run(definition.ref.id, 'Settings source test')
            assert runtime.providers['deepseek']._api_key_env == ENV
            assert report.execution.state.value == 'succeeded' and seen and report.simulated
        finally:
            await runtime.close()
    asyncio.run(scenario())
