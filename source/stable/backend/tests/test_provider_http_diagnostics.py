"""08-C SIMULATED HTTP only. Synthetic credentials; no live provider requests."""

import asyncio
from dataclasses import asdict, replace
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from futurecup.adapters.llm import OpenAICompatibleProvider
from futurecup.adapters.workflow_store import report_from
from futurecup.api.app import create_app
from futurecup.config import Settings
from futurecup.domain.errors import RuntimeFault, HTTPErrorCategory
from futurecup.domain.llm import RetryPolicy
from runtime_support import agent
from test_real_llm_integration import managed, profile, request, runtime, completion, KEY, FAKE
from test_workflow_api import completed

REQUEST_ID = 'a7209906-11ab-4ac9-926c-ef349d5760d4'


@pytest.mark.parametrize('managed_profile', [True, False])
@pytest.mark.parametrize('status,category', [
    (400, 'invalid_request'), (401, 'authentication_failed'), (402, 'insufficient_balance'),
    (403, 'authentication_failed'), (404, 'model_unavailable'), (429, 'rate_limited'),
    (500, 'server_error'), (302, 'unknown_http_error'), (422, 'unknown_http_error'),
])
def test_safe_http_diagnostics_preserve_legacy_codes(monkeypatch, caplog, managed_profile, status, category):
    monkeypatch.setenv(KEY, FAKE)
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(status, json={'error': {'message': FAKE, 'code': FAKE}},
            headers={'x-request-id': REQUEST_ID, 'Set-Cookie': FAKE, 'Location': 'https://private.example/' + FAKE})
    provider = managed(handler) if managed_profile else OpenAICompatibleProvider(
        'https://model.example/v1', KEY, True, httpx.MockTransport(handler))
    with pytest.raises(RuntimeFault) as failure:
        asyncio.run(provider.infer(request()))
    fault = failure.value
    expected = ('provider_rate_limited' if status == 429 else
                'provider_auth_error' if managed_profile and status in (401, 403) else
                'provider_server_error' if managed_profile and status >= 500 else 'provider_http_error')
    assert fault.code == expected
    assert fault.status_code == (503 if status == 429 else 502)  # Local API, distinct from upstream.
    assert type(fault.provider_http.status_code) is int and fault.provider_http.status_code == status
    assert fault.provider_http.category == HTTPErrorCategory(category)
    assert fault.provider_http.request_id == REQUEST_ID and len(calls) == 1
    serialized = json.dumps(provider.last_outcome) + str(fault) + repr(asdict(fault.provider_http)) + caplog.text
    assert FAKE not in serialized and 'private.example' not in serialized and 'Set-Cookie' not in serialized


class NeverReadErrorBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError('Error body must never be consumed, parsed, or logged')
        yield b''


def test_error_body_is_not_read_even_when_malformed_secret_or_stream_failure(monkeypatch):
    monkeypatch.setenv(KEY, FAKE)
    provider = managed(lambda r: httpx.Response(402, stream=NeverReadErrorBody(),
        headers={'Content-Type': 'application/json', 'x-request-id': FAKE}))
    with pytest.raises(RuntimeFault) as failure:
        asyncio.run(provider.infer(request()))
    assert failure.value.code == 'provider_http_error'
    assert failure.value.provider_http.category.value == 'insufficient_balance'
    assert failure.value.provider_http.request_id is None


@pytest.mark.parametrize('body', [b'not-json', b'{"error":', b'\xff\xfe', b'[]', b'null',
    ('<html>private diagnostic ' + FAKE + '</html>').encode(),
    json.dumps({'error': {'code': FAKE, 'message': FAKE, 'request_id': FAKE}}).encode()])
def test_malformed_or_sensitive_body_cannot_override_safe_category(monkeypatch, body):
    monkeypatch.setenv(KEY, FAKE)
    provider = managed(lambda r: httpx.Response(400, content=body))
    with pytest.raises(RuntimeFault) as failure:
        asyncio.run(provider.infer(request()))
    assert failure.value.provider_http.category.value == 'invalid_request'
    assert failure.value.provider_http.request_id is None
    assert FAKE not in json.dumps(provider.last_outcome) + str(failure.value)


@pytest.mark.parametrize('value,accepted', [
    (REQUEST_ID, True), ('a' * 32, True), (FAKE, False), ('Bearer ' + FAKE, False),
    ('https://private.example/' + FAKE, False), ('-side-channel-value-', False), ('-' * 32, False),
    (' a' * 16, False), ('g' * 32, False), ('x' * 4096, False), ('', False),
])
def test_request_id_is_strictly_allowlisted(monkeypatch, value, accepted):
    monkeypatch.setenv(KEY, FAKE)
    provider = managed(lambda r: httpx.Response(403, headers={'x-request-id': value}))
    with pytest.raises(RuntimeFault) as failure:
        asyncio.run(provider.infer(request()))
    assert failure.value.provider_http.request_id == (value if accepted else None)
    assert FAKE not in json.dumps(provider.last_outcome)


@pytest.mark.parametrize('prefix', ['', 'sk-'])
def test_hex_shaped_credential_and_duplicate_request_ids_are_rejected(monkeypatch, prefix):
    synthetic_hex_key = 'abcdef01' * 4
    monkeypatch.setenv(KEY, prefix + synthetic_hex_key)
    for headers in ({'x-request-id': synthetic_hex_key.upper()},
                    [('x-request-id', REQUEST_ID), ('x-request-id', 'b' * 32)]):
        provider = managed(lambda r: httpx.Response(401, headers=headers))
        with pytest.raises(RuntimeFault) as failure:
            asyncio.run(provider.infer(request()))
        assert failure.value.provider_http.request_id is None
        assert synthetic_hex_key not in json.dumps(provider.last_outcome).lower()


def test_retry_history_records_each_http_error_without_changing_retries(monkeypatch):
    monkeypatch.setenv(KEY, FAKE)
    statuses = [429, 500, 200]
    def handler(r):
        status = statuses.pop(0)
        return httpx.Response(status, json=completion() if status == 200 else {'error': FAKE},
                              headers={'x-request-id': REQUEST_ID})
    async def scenario():
        value = runtime(handler, profile(retry=RetryPolicy(2, 0, 0)))
        try:
            await value.registry.register(agent(provider='configured', model='test-model'))
            result = await value.run('analyst', 'SIMULATED retry diagnostics')
            assert result.execution.state.value == 'succeeded'
            assert [c.http_status_code for c in result.model_calls] == [429, 500, None]
            assert [c.http_error_category for c in result.model_calls] == ['rate_limited', 'server_error', None]
            assert [c.retry_index for c in result.model_calls] == [0, 1, 2]
            assert result.model_calls[0].provider_request_id == REQUEST_ID
            assert not statuses and not result.usage_complete
            assert FAKE not in repr(result)
        finally:
            await value.close()
    asyncio.run(scenario())


def test_concurrent_agent_http_diagnostics_are_not_taken_from_global_last_outcome(monkeypatch):
    monkeypatch.setenv(KEY, FAKE)
    async def handler(r):
        which = json.loads(r.content)['messages'][-1]['content']
        await asyncio.sleep(.01 if which == 'one' else 0)
        return httpx.Response(400 if which == 'one' else 402)
    async def scenario():
        value = runtime(handler)
        try:
            for name in ('one', 'two'):
                await value.registry.register(agent(name, provider='configured', model='test-model'))
            first, second = await asyncio.gather(value.run('one', 'one'), value.run('two', 'two'))
            assert first.model_calls[0].http_status_code == 400
            assert second.model_calls[0].http_status_code == 402
            assert first.model_calls[0].http_error_category == 'invalid_request'
            assert second.model_calls[0].http_error_category == 'insufficient_balance'
        finally:
            await value.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('verified_node', [True, False])
def test_diagnostics_api_and_sqlite_audit_survive_roundtrip(monkeypatch, tmp_path, verified_node):
    monkeypatch.setenv(KEY, FAKE)
    path = tmp_path / 'simulated-http-diagnostics.sqlite3'
    value = runtime(lambda r: httpx.Response(402, text=FAKE, headers={'x-request-id': REQUEST_ID}))
    options = Settings('test', 'sqlite:///' + str(path))
    with TestClient(create_app(options, value)) as client:
        registered = client.post('/api/v1/agents', json={'id': 'diagnostic-agent', 'name': 'SIMULATED HTTP',
            'role': 'test', 'responsibilities': 'Protocol diagnosis', 'system_prompt': 'Return JSON',
            'provider': 'configured', 'model': 'test-model'})
        assert registered.status_code == 201
        config = {'agent_id': 'diagnostic-agent', 'limits': {'max_model_calls': 1, 'max_calls': 1, 'max_output_tokens': 128}}
        if verified_node:
            assert client.post('/api/v1/verification-policies', json={'id': 'diagnostic-rule', 'name': 'Frozen rule',
                'checks': [{'kind': 'json_schema', 'config': {'schema': {'type': 'object'}}}]}).status_code == 201
            config.update(verification={'id': 'diagnostic-rule', 'version': '1'},
                          recovery={'max_attempts': 1, 'max_feedback_loops': 0})
        flow = {'id': 'diagnostic-flow', 'name': 'SIMULATED error', 'nodes': [{'id': 'check', 'kind': 'task', 'config': config}],
                'resource_limits': {'max_calls': 1}}
        assert client.post('/api/v1/workflows', json=flow).status_code == 201
        submitted = client.post('/api/v1/workflows/diagnostic-flow/executions', json={'input': 'SIMULATED error'})
        result = completed(client, submitted.json()['run']['id'])
        node = result['nodes'][0]
        assert result['run']['state'] == 'failed' and not result['verified']
        assert result['resource_usage']['model_calls'] == 1
        assert node['model_calls'][0]['http_status_code'] == 402
        assert node['model_calls'][0]['http_error_category'] == 'insufficient_balance'
        execution = client.get('/api/v1/executions/' + node['execution_id']).json()
        assert execution['model_calls'][0]['provider_request_id'] == REQUEST_ID
        assert execution['execution']['error_code'] == 'provider_http_error'
        assert not execution['usage_complete'] and not execution['model_calls'][0]['usage_reported']
        assert FAKE not in json.dumps(result) + json.dumps(execution)
        if verified_node:
            attempts = client.get('/api/v1/self-loops/' + node['id'] + '/attempts').json()
            assert attempts[0]['model_calls'][0]['http_status_code'] == 402
            assert FAKE not in json.dumps(attempts)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        snapshot = json.loads(db.execute('SELECT snapshot FROM workflow_runs').fetchone()[0])
        restored = report_from(json.loads(json.dumps(snapshot)))
        assert restored.nodes[0].model_calls[0].http_status_code == 402
        if verified_node:
            audit = json.loads(db.execute("SELECT body FROM safety_records WHERE kind='attempt'").fetchone()[0])
            assert audit['model_calls'][0]['http_error_category'] == 'insufficient_balance'
        # Older snapshots have no new field; loading must not invent historical metadata.
        for n in snapshot['nodes']:
            n.pop('model_calls')
        legacy = report_from(snapshot)
        assert legacy.nodes[0].model_calls == ()
    assert FAKE.encode() not in path.read_bytes()
