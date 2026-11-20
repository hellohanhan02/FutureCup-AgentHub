"""09-B: SIMULATED HTTP and isolated SQLite; never contact a real model."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from futurecup.adapters.llm import OpenAICompatibleProvider
from futurecup.adapters.priority_plan import verify_priority_plan
from futurecup.api.app import create_app
from futurecup.bootstrap import build_runtime
from futurecup.domain.models import VersionRef
from futurecup.domain.verification import ArtifactVersion
from test_real_llm_integration import completion
from test_serial_collaboration_09a import configuration, KEY, FAKE
from test_workflow_api import completed

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = json.loads((ROOT / 'config/deepseek-selfloop09b.json').read_text('utf-8'))
VALID = {'task': 'priority_plan', 'items': [{'id': 'A', 'priority': 1}, {'id': 'B', 'priority': 2}, {'id': 'C', 'priority': 3}], 'total': 3}
BAD = {**deepcopy(VALID), 'total': 9}


class SimulatedHTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def __call__(self, request):
        body = json.loads(request.content)
        assert body['model'] == 'deepseek-flash' and body['max_tokens'] == 256
        assert body['thinking'] == {'type': 'disabled'}
        assert 'tools' not in body and 'response_format' not in body  # Preserve raw invalid JSON for repair.
        self.requests.append(body)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, int):
            return httpx.Response(value, text='SIMULATED HTTP private error, never logged')
        content = value if isinstance(value, str) else json.dumps(value)
        payload = completion(content, model='SIMULATED09B')
        offset = 2 * (len(self.requests) - 1)
        payload['usage'] = {'prompt_tokens': 3 + offset, 'completion_tokens': 4 + offset, 'total_tokens': 7 + 2 * offset}
        return httpx.Response(200, json=payload)


def application(monkeypatch, simulator, path=':memory:', *, prices=True, expensive=False):
    monkeypatch.setenv(KEY, FAKE)
    options = configuration(path, prices=prices)
    if expensive:
        values = json.loads(options.provider_config_json)
        values[0]['models'][0].update(input_price_per_million=1000, output_price_per_million=1000)
        options = replace(options, provider_config_json=json.dumps(values))
    runtime = build_runtime(options)
    profile = next(p for p in runtime.provider_profiles if p.id == 'deepseek')
    assert profile.retry.max_retries == 0
    runtime.providers['deepseek'] = OpenAICompatibleProvider(profile.base_url, profile.key_env, True,
        httpx.MockTransport(simulator), profile=profile)
    return create_app(options, runtime)


def register(client, suffix='one', *, calls=2):
    value = deepcopy(TEMPLATE)
    value['agent']['id'] = 'selfloop09b-' + suffix + '-agent'
    value['policy']['id'] = 'selfloop09b-' + suffix + '-rule'
    value['workflow']['id'] = 'selfloop09b-' + suffix + '-flow'
    node = value['workflow']['nodes'][0]['config']
    node['agent_id'] = value['agent']['id']
    node['verification']['id'] = value['policy']['id']
    value['workflow']['resource_limits']['max_calls'] = calls
    capability = client.get('/api/v1/verification-capabilities').json()
    assert capability['self_loop_feedback_context_version'] == 2
    assert 'priority-plan09b-v1' in capability['registered_external_tests']
    for path, item in [('agents', value['agent']), ('verification-policies', value['policy']), ('workflows', value['workflow'])]:
        response = client.post('/api/v1/' + path, json=item)
        assert response.status_code == 201, response.text
    return value


def execute(client, value):
    response = client.post('/api/v1/workflows/' + value['workflow']['id'] + '/executions', json={'input': value['input']})
    assert response.status_code == 202, response.text
    result = completed(client, response.json()['run']['id'])
    node = result['nodes'][0]
    attempts = client.get('/api/v1/self-loops/' + node['id'] + '/attempts').json()
    verifications = client.get('/api/v1/self-loops/' + node['id'] + '/verifications').json()
    feedback = client.get('/api/v1/self-loops/' + node['id'] + '/feedback').json()
    return result, attempts, verifications, feedback


@pytest.mark.parametrize('case,outputs,statuses,loops,final', [
    ('A-first-pass', [VALID], ['PASSED'], 0, 'succeeded'),
    ('B-repaired', [BAD, VALID], ['FAILED', 'PASSED'], 1, 'succeeded'),
    ('C-two-failures', [BAD, BAD, VALID], ['FAILED', 'FAILED'], 1, 'failed'),
])
def test_three_paths_and_reproducible_sqlite_evidence(monkeypatch, tmp_path, case, outputs, statuses, loops, final):
    fixture = deepcopy(outputs)
    simulator = SimulatedHTTP(*deepcopy(outputs))
    path = tmp_path / (case + '.sqlite3')
    with TestClient(application(monkeypatch, simulator, path)) as client:
        definition = register(client)
        result, attempts, checks, feedback = execute(client, definition)
        assert [c['status'] for c in checks] == statuses
        assert result['run']['state'] == final and result['verified'] == (final == 'succeeded')
        assert result['simulated'] and all(a['simulated'] for a in attempts)
        assert len(simulator.requests) == len(attempts) == len(statuses)
        assert result['resource_usage']['model_calls'] == len(statuses)
        assert result['resource_usage']['feedback_loops'] == loops
        assert result['resource_usage']['reported_tokens'] == (7 if len(statuses) == 1 else 18)
        assert result['resource_usage']['unknown_usage_calls'] == 0
        assert len({a['id'] for a in attempts}) == len(attempts)
        assert len({a['policy_digest'] for a in attempts}) == 1
        expected_policy = {'ref': {'id': definition['policy']['id'], 'version': definition['policy']['version']},
                           'name': definition['policy']['name'], 'checks': definition['policy']['checks'], 'timeout_seconds': 5}
        assert client.get('/api/v1/verification-policies/' + definition['policy']['id']).json() == expected_policy
        if loops:
            incoming = json.loads(simulator.requests[1]['messages'][-1]['content'])
            recovery = incoming['recovery']
            assert incoming == attempts[1]['input_value']
            assert incoming['original_input'] == definition['input']
            assert recovery['previous_attempt_id'] == attempts[0]['id']
            assert recovery['previous_artifact']['content'] == attempts[0]['artifact']['content'] == BAD
            assert recovery['previous_artifact']['ref'] == attempts[0]['artifact']['ref']
            assert recovery['previous_artifact']['digest'] == attempts[0]['artifact']['digest']
            assert recovery['verification']['id'] == checks[0]['record']['id']
            assert recovery['verification']['evidence'] == checks[0]['evidence']
            assert recovery['feedback']['id'] == feedback[0]['record']['id']
            assert recovery['feedback']['action'] == feedback[0]['repair_action'] == 'prompt_retry'
            assert feedback[0]['evidence_refs'] == [checks[0]['record']['id']]
            assert feedback[0]['category'] == 'ASSERTION_FAILED'
            assert any(e['path'] == 'total' and e['rule'] == 'total_matches_item_count' for e in checks[0]['evidence'])
            assert feedback[0]['actual_budget_cost'] == {'feedback_loops': 1, 'replans': 0}
            assert len(json.dumps(incoming).encode()) < 8192
            # Runtime stored the very same payload passed through HTTP, not an invented acknowledgement.
            execution = client.get('/api/v1/executions/' + attempts[1]['execution_id']).json()
            assert json.loads(execution['input']) == incoming
        else:
            assert feedback == [] and not simulator.responses
        if final == 'failed':
            assert len(simulator.responses) == 1  # A third successful fixture must remain unused.
            assert result['output'] is None and not result['nodes'][0]['verified']
            assert feedback[-1]['repair_action'] == 'abort'
        else:
            assert attempts[-1]['artifact']['content'] == result['output']['plan'] == VALID
        assert outputs == fixture  # The engine must not edit model fixtures or standards to pass.
        assert FAKE not in json.dumps(result) + json.dumps(attempts) + json.dumps(checks) + json.dumps(feedback)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        saved = json.loads(db.execute('SELECT snapshot FROM workflow_runs').fetchone()[0])
        assert saved['run']['id'] == result['run']['id'] and saved['simulated'] is True
        assert db.execute("SELECT count(*) FROM safety_records WHERE kind='attempt'").fetchone()[0] == len(statuses)
        if loops:
            values = [json.loads(r[0]) for r in db.execute("SELECT body FROM safety_records WHERE kind='attempt'")]
            second = next(v for v in values if v['ordinal'] == 2)
            assert second['input_value']['recovery']['previous_artifact']['content'] == BAD


@pytest.mark.parametrize('bad,reason', [
    ('{bad json', 'output_format_error'),
    ({k: v for k, v in VALID.items() if k != 'total'}, 'priority_plan_missing_field'),
    ({**deepcopy(VALID), 'items': [{'id': 'A', 'priority': 1}, {'id': 'B', 'priority': 1}, {'id': 'C', 'priority': 3}]}, 'priority_plan_priorities_not_permutation'),
    ({**deepcopy(VALID), 'extra': 'not allowed'}, 'priority_plan_extra_fields'),
])
def test_actual_invalid_output_and_specific_evidence_are_sent_to_repair(monkeypatch, bad, reason):
    simulator = SimulatedHTTP(bad, VALID)
    with TestClient(application(monkeypatch, simulator)) as client:
        result, attempts, checks, feedback = execute(client, register(client))
        assert result['verified'] and len(simulator.requests) == 2
        assert checks[0]['status'] == 'FAILED' and reason in checks[0]['record']['reason_codes']
        second = json.loads(simulator.requests[1]['messages'][-1]['content'])['recovery']
        assert second['previous_artifact']['content'] == bad
        assert second['verification']['evidence'] == checks[0]['evidence']
        assert reason in second['reason_codes']
        assert any(e['rule'] for e in checks[0]['evidence'] if e['status'] == 'FAILED')
        if isinstance(bad, dict) and 'total' not in bad:
            assert any(e['rule'] == 'required' and e['schema_path'] == 'required' for e in checks[0]['evidence'])


@pytest.mark.parametrize('response,code', [(400, 'provider_http_error'), (401, 'provider_auth_error'),
    (429, 'provider_rate_limited'), (500, 'provider_server_error'),
    (httpx.ReadTimeout('SIMULATED timeout'), 'model_timeout')])
def test_provider_errors_stop_without_a_paid_repair_attempt(monkeypatch, response, code):
    simulator = SimulatedHTTP(response, VALID)
    with TestClient(application(monkeypatch, simulator)) as client:
        result, attempts, checks, feedback = execute(client, register(client))
        assert result['run']['state'] == 'failed' and not result['verified']
        assert len(simulator.requests) == len(attempts) == 1 and len(simulator.responses) == 1
        assert attempts[0]['error_code'] == code and checks[0]['status'] == 'ERROR'
        assert feedback[0]['repair_action'] == 'abort' and result['resource_usage']['feedback_loops'] == 0


@pytest.mark.parametrize('prices,expensive,code', [(False, False, 'cost_estimate_unavailable'), (True, True, 'estimated_cost_budget')])
def test_unknown_or_over_budget_prices_refuse_before_http(monkeypatch, prices, expensive, code):
    simulator = SimulatedHTTP(VALID)
    with TestClient(application(monkeypatch, simulator, prices=prices, expensive=expensive)) as client:
        result, attempts, checks, feedback = execute(client, register(client))
        assert result['run']['state'] == 'failed' and not simulator.requests
        assert attempts[0]['error_code'] == code and feedback[0]['repair_action'] == 'abort'
        assert result['resource_usage']['model_calls'] == 0 and result['resource_usage']['feedback_loops'] == 0


def test_lower_shared_call_budget_prevents_the_second_http_call(monkeypatch):
    simulator = SimulatedHTTP(BAD, VALID)
    with TestClient(application(monkeypatch, simulator)) as client:
        result, attempts, checks, feedback = execute(client, register(client, calls=1))
        assert len(simulator.requests) == 1 and len(attempts) == 2
        assert attempts[1]['error_code'] == 'shared_call_budget'
        assert result['run']['state'] == 'failed' and feedback[-1]['repair_action'] == 'abort'


def test_new_run_preserves_old_audit_and_does_not_reuse_feedback(monkeypatch):
    simulator = SimulatedHTTP(BAD, VALID, BAD, VALID)
    with TestClient(application(monkeypatch, simulator)) as client:
        definition = register(client)
        first, a1, v1, f1 = execute(client, definition)
        second, a2, v2, f2 = execute(client, definition)
        assert first['run']['id'] != second['run']['id']
        assert client.get('/api/v1/workflow-runs/' + first['run']['id']).json() == first
        assert {a['id'] for a in a1}.isdisjoint(a['id'] for a in a2)
        assert a2[1]['input_value']['recovery']['feedback']['id'] == f2[0]['record']['id'] != f1[0]['record']['id']


@pytest.mark.parametrize('change,path,rule', [
    ({'task': 'wrong'}, 'task', 'const'),
    ({'items': []}, 'items', 'exactly_three'),
    ({'total': True}, 'total', 'total_matches_item_count'),
    ({'items': [{'id': 'A', 'priority': True}, {'id': 'B', 'priority': 2}, {'id': 'C', 'priority': 3}]}, 'items.0.priority', 'integer'),
    ({'items': [{'id': 'A', 'priority': 1.0}, {'id': 'B', 'priority': 2}, {'id': 'C', 'priority': 3}]}, 'items.0.priority', 'integer'),
    ({'items': [{'id': 'A', 'priority': 1}, {'id': 'A', 'priority': 2}, {'id': 'C', 'priority': 3}]}, 'items', 'id_permutation'),
])
def test_frozen_rules_reject_business_errors_without_modifying_output(change, path, rule):
    import asyncio
    content = {**deepcopy(VALID), **change}
    before = deepcopy(content)
    result = asyncio.run(verify_priority_plan(ArtifactVersion(VersionRef('test', '1'), 'bound-digest', content, True)))
    assert result.status == 'FAILED' and result.path == path and result.rule == rule
    assert result.reference == 'bound-digest' and content == before


@pytest.mark.parametrize('ids,priorities', [('ABC', (1, 2, 3)), ('CBA', (2, 3, 1)), ('BAC', (3, 1, 2))])
def test_rule_does_not_hardcode_one_success_answer(ids, priorities):
    import asyncio
    value = {'task': 'priority_plan', 'items': [{'id': a, 'priority': b} for a, b in zip(ids, priorities)], 'total': 3}
    result = asyncio.run(verify_priority_plan(ArtifactVersion(VersionRef('test', '1'), 'bound', value, True)))
    assert result.status == 'PASSED'


@pytest.mark.parametrize('scenario', ['preflight','A','B','C','D','E','repeatability','missing-price','over-budget',
    'missing-budget','wrong-currency','provider-retry','old-backend','template-drift','unconfirmed-prices'])
def test_script_authorization_cases_and_price_gates_without_network(scenario):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-File',
        str(ROOT / 'backend/tests/powershell/selfloop09b_harness.ps1'), '-Scenario', scenario, '-ProjectRoot', str(ROOT)],
        capture_output=True, text=True, timeout=20, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'zero network/model calls' in result.stdout
    expected = {'A': 'first attempt passed', 'B': 'verified after one repair', 'C': 'verification did not pass',
                'D': 'execution/provider error', 'E': 'budget/price admission refused'}
    if scenario in expected:
        assert expected[scenario] in result.stdout


@pytest.mark.parametrize('deadline', [False, True])
def test_cancellation_swallowed_at_assignment_boundary_cannot_publish_or_complete(deadline):
    import asyncio
    from verification_support import safety_engine, setup, definition, close
    from runtime_support import ScriptedMockProvider, response
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"answer":42}')))
        original = engine.scheduler.dispatch
        async def swallowed(*args, **kwargs):
            assignment = await original(*args, **kwargs)
            # Model the wait_for cancellation race without modifying scheduler code.
            if not deadline:
                asyncio.current_task().cancel()
            try:
                await asyncio.sleep(1 if deadline else 0)
            except asyncio.CancelledError:
                pass
            return assignment
        engine.scheduler.dispatch = swallowed
        try:
            await setup(engine)
            flow = definition(timeout_seconds=.3 if deadline else 30)
            await engine.register(flow)
            result = await engine.run(flow.ref, 'SIMULATED cancellation race')
            attempts = await engine.selfloop.repository.attempts(result.nodes[0].id)
            assert result.run.state.value == ('failed' if deadline else 'cancelled') and not result.verified
            assert attempts[0]['state'] == ('failed' if deadline else 'cancelled') and attempts[0]['verification_id'] is None
            assert (await engine.runtime.capacity('analyst')).active == 0
        finally:
            await close(engine)
    asyncio.run(scenario())
