"""09-A SIMULATED HTTP protocol tests only; no live API or real credentials."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from futurecup.adapters.llm import OpenAICompatibleProvider
from futurecup.adapters.serial_collaboration import SerialCollaborationChecks
from futurecup.adapters.verification import StructuredVerificationBackend
from futurecup.api.app import create_app
from futurecup.bootstrap import build_runtime
from futurecup.config import Settings
from futurecup.domain.models import VersionRef, TaskState
from futurecup.domain.verification import ArtifactVersion
from test_real_llm_integration import completion
from test_workflow_api import completed

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = json.loads((ROOT / 'config/deepseek-serial09a.json').read_text('utf-8'))
KEY = 'TASK09A_SYNTHETIC_CREDENTIAL'
FAKE = 'task09a-simulated-not-a-real-credential'


def configuration(path=':memory:', *, prices=True):
    profiles = json.loads((ROOT / 'config/deepseek-safe.json').read_text('utf-8'))
    profiles[0].update(enabled=True, key_env=KEY)
    if prices:
        profiles[0]['models'][0].update(input_price_per_million=1, output_price_per_million=1, currency='CNY')
    # Prices are synthetic test arithmetic, not official pricing.
    return Settings('test', 'sqlite:///' + str(path), enable_real_llm=True,
        provider_config_json=json.dumps(profiles), real_call_safety_json=(ROOT / 'config/real-call-safety.json').read_text('utf-8'))


class SimulatedCollaboration:
    def __init__(self, failure=None, mutation='alpha', usage=True):
        self.failure, self.mutation, self.usage = failure, mutation, usage
        self.requests, self.outputs, self.peak, self.active = [], {}, 0, 0

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            assert str(request.url) == 'https://api.deepseek.com/chat/completions'
            assert body['model'] == 'deepseek-flash' and body['max_tokens'] == 256
            assert body['thinking'] == {'type': 'disabled'} and 'tools' not in body
            assert len(json.dumps({'messages': body['messages'], 'tools': []}, ensure_ascii=False).encode()) <= 4096
            incoming = json.loads(body['messages'][-1]['content'])
            if 'design' in incoming:
                stage = 'review'
                assert incoming['analysis'] == self.outputs['analyse']
                assert incoming['design'] == self.outputs['design']
                output = {'request_id': incoming['request_id'], 'requirements': deepcopy(incoming['analysis']['requirements']),
                          'components': deepcopy(incoming['design']['components']), 'issues': [], 'summary': 'SIMULATED review'}
            elif 'analysis' in incoming:
                stage = 'design'
                assert incoming['analysis'] == self.outputs['analyse']
                output = {'request_id': incoming['request_id'], 'requirements': deepcopy(incoming['analysis']['requirements']),
                          'components': ['SIMULATED auth ' + self.mutation, 'Seat slot store']}
            else:
                stage = 'analyse'
                assert incoming['task'] == TEMPLATE['input']['task']
                output = {'request_id': incoming['request_id'], 'requirements': [
                    {'id': 'R1', 'text': 'SIMULATED auth ' + self.mutation},
                    {'id': 'R2', 'text': 'SIMULATED nonoverlap ' + self.mutation}]}
            if self.failure == stage + '-http':
                return httpx.Response(400, text='SIMULATED private error body')
            if self.failure == stage + '-timeout':
                raise httpx.ReadTimeout('SIMULATED timeout')
            if self.failure == stage + '-schema':
                output['unexpected'] = True
            if self.failure == stage + '-request':
                output['request_id'] = 'wrong-but-well-typed'
            if self.failure == stage + '-requirements':
                output['requirements'][0]['text'] = 'Changed actual upstream content'
            if self.failure == 'review-components' and stage == 'review':
                output['components'] = ['Invented component']
            self.outputs[stage] = deepcopy(output)
            return httpx.Response(200, json=completion(json.dumps(output), model='SIMULATED-deepseek', usage=self.usage))
        finally:
            self.active -= 1


def app(monkeypatch, simulator, options=None):
    monkeypatch.setenv(KEY, FAKE)
    options = options or configuration()
    value = build_runtime(options)
    profile = next(p for p in value.provider_profiles if p.id == 'deepseek')
    value.providers['deepseek'] = OpenAICompatibleProvider(profile.base_url, profile.key_env, True,
        httpx.MockTransport(simulator), profile=profile)
    return create_app(options, value)


def register(client, suffix='alpha'):
    value = deepcopy(TEMPLATE)
    for i, stage in enumerate(('analyse', 'design', 'review')):
        value['agents'][i]['id'] = 'serial-' + suffix + '-' + stage
        value['policies'][i]['id'] = 'serial-' + suffix + '-' + stage + '-rule'
        value['workflow']['nodes'][i]['config']['agent_id'] = value['agents'][i]['id']
        value['workflow']['nodes'][i]['config']['verification']['id'] = value['policies'][i]['id']
    value['workflow']['id'] = 'serial-' + suffix
    value['input']['request_id'] = 'serial-input-' + suffix
    capabilities = client.get('/api/v1/verification-capabilities').json()
    assert set(capabilities['registered_external_tests']) >= {'serial09a-analysis-v1', 'serial09a-design-v1', 'serial09a-review-v1'}
    for item in value['agents']:
        response = client.post('/api/v1/agents', json=item)
        assert response.status_code == 201, response.text
    for item in value['policies']:
        response = client.post('/api/v1/verification-policies', json=item)
        assert response.status_code == 201, response.text
    checked = client.post('/api/v1/workflows/validate', json=value['workflow'])
    assert checked.status_code == 200, checked.text
    assert client.post('/api/v1/workflows', json=value['workflow']).status_code == 201
    return value


def run(client, definition):
    response = client.post('/api/v1/workflows/' + definition['workflow']['id'] + '/executions', json={'input': definition['input']})
    assert response.status_code == 202, response.text
    return completed(client, response.json()['run']['id'])


def test_three_roles_actual_output_delivery_caas_verifier_and_sqlite(monkeypatch, tmp_path):
    path = tmp_path / 'serial-simulated.sqlite3'
    simulator = SimulatedCollaboration(mutation='runtime-alpha')
    with TestClient(app(monkeypatch, simulator, configuration(path))) as client:
        result = run(client, register(client))
        assert result['run']['state'] == 'succeeded' and result['verified'] and result['simulated']
        assert len(simulator.requests) == 3 and simulator.peak == 1
        assert result['resource_usage']['calls'] == result['resource_usage']['attempts'] == 3
        assert result['resource_usage']['checks'] == 6
        assert result['resource_usage']['feedback_loops'] == result['resource_usage']['replans'] == 0
        a, d, r = result['nodes']
        assert d['input']['analysis'] == a['output']
        assert r['input']['analysis'] == a['output'] and r['input']['design'] == d['output']
        assert r['output']['requirements'] == a['output']['requirements']
        assert r['output']['components'] == d['output']['components']
        assert len({n['agent_ref']['id'] for n in result['nodes']}) == 3
        assert all(n['provider'] == 'deepseek' and n['model'] == 'deepseek-flash' and n['usage_complete'] for n in result['nodes'])
        assert a['ended_at'] <= d['started_at'] and d['ended_at'] <= r['started_at']
        decisions = client.get('/api/v1/scheduling/decisions?run_id=' + result['run']['id']).json()
        assert len(decisions) == 3 and all(decision['strategy'] == 'caas' for decision in decisions)
        for node in result['nodes']:
            evidence = client.get('/api/v1/self-loops/' + node['id'] + '/verifications').json()
            assert evidence[0]['status'] == 'PASSED'
            assert evidence[0]['evidence'][1]['reason'] == 'serial09a_inputs_and_outputs_matched'
        assert FAKE not in json.dumps(result)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        saved = json.loads(db.execute('SELECT snapshot FROM workflow_runs').fetchone()[0])
        assert saved['nodes'][1]['input']['analysis'] == saved['nodes'][0]['output']
        assert saved['nodes'][2]['input']['design'] == saved['nodes'][1]['output']
        assert db.execute("SELECT count(*) FROM safety_records WHERE kind='attempt'").fetchone()[0] == 3


@pytest.mark.parametrize('failure, calls', [
    ('analyse-schema', 1), ('analyse-request', 1), ('analyse-http', 1),
    ('design-schema', 2), ('design-requirements', 2), ('design-http', 2), ('design-timeout', 2),
    ('review-schema', 3), ('review-requirements', 3), ('review-components', 3), ('review-request', 3),
])
def test_failure_stops_downstream_without_retry_or_repair(monkeypatch, failure, calls):
    simulator = SimulatedCollaboration(failure)
    with TestClient(app(monkeypatch, simulator)) as client:
        result = run(client, register(client))
        assert result['run']['state'] == 'failed' and not result['verified']
        assert len(simulator.requests) == calls and result['resource_usage']['model_calls'] == calls
        assert result['resource_usage']['feedback_loops'] == result['resource_usage']['replans'] == 0
        assert all(n['state'] == 'skipped' for n in result['nodes'][calls:])
        assert all(len(n['attempt_ids']) == 1 for n in result['nodes'][:calls])
        assert result['nodes'][calls-1]['output'] is None  # Failed artifacts stay isolated.
        assert result['output'] is None


def test_independent_data_changes_are_transmitted_and_runs_are_isolated(monkeypatch):
    simulator = SimulatedCollaboration(mutation='first')
    with TestClient(app(monkeypatch, simulator)) as client:
        first = run(client, register(client, 'first'))
        simulator.mutation = 'second'
        second = run(client, register(client, 'second'))
        assert first['run']['id'] != second['run']['id']
        assert first['nodes'][0]['output'] != second['nodes'][0]['output']
        assert second['nodes'][1]['input']['analysis'] == second['nodes'][0]['output']
        assert second['nodes'][1]['input']['analysis'] != first['nodes'][0]['output']
        assert first['verified'] and second['verified']


def test_unknown_prices_refuse_before_simulated_http(monkeypatch):
    simulator = SimulatedCollaboration()
    with TestClient(app(monkeypatch, simulator, configuration(prices=False))) as client:
        result = run(client, register(client))
        assert result['run']['state'] == 'failed' and not simulator.requests
        attempts = client.get('/api/v1/self-loops/' + result['nodes'][0]['id'] + '/attempts').json()
        assert attempts[0]['error_code'] == 'cost_estimate_unavailable'


def test_missing_usage_stays_unknown_instead_of_fabricating_total(monkeypatch):
    simulator = SimulatedCollaboration(usage=False)
    with TestClient(app(monkeypatch, simulator)) as client:
        result = run(client, register(client))
        assert result['verified'] and result['resource_usage']['unknown_usage_calls'] == 3
        assert result['resource_usage']['reported_tokens'] == 0
        assert all(n['usage_complete'] is False for n in result['nodes'])
        assert 'total_tokens' not in result and 'usage_complete' not in result
        assert all('total_tokens' not in n for n in result['nodes'])


def test_bounded_schema_worst_case_upstream_context_below_four_kib():
    # JSON escaping controls is larger than ordinary text. Use maximum schema lengths.
    req = [{'id': 'R1', 'text': '\x01' * 64}, {'id': 'R2', 'text': '\x01' * 64}]
    analysis = {'request_id': 'x' * 64, 'requirements': req}
    design = {**analysis, 'components': ['\x01' * 32] * 3}
    incoming = {'request_id': 'x' * 64, 'analysis': analysis, 'design': design}
    messages = [{'role': 'system', 'content': TEMPLATE['agents'][2]['system_prompt'] +
        '\nReturn a single valid JSON object. Do not wrap JSON in Markdown or include non-finite numbers.'},
        {'role': 'user', 'content': json.dumps(incoming, ensure_ascii=False)}]
    # Includes the double JSON escaping of content carried as a string in messages.
    assert len(json.dumps({'messages': messages, 'tools': []}, ensure_ascii=False).encode()) <= 4096


@pytest.mark.parametrize('scenario', ['preflight', 'repeatability', 'unconfirmed-prices', 'missing-price',
    'wrong-currency', 'over-budget', 'missing-budget', 'transport-retry', 'mock-only', 'missing-verifier'])
def test_powershell_authorization_budget_and_unique_ids_without_network(scenario):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-File',
        str(ROOT / 'backend/tests/powershell/serial09a_harness.ps1'), '-Scenario', scenario, '-ProjectRoot', str(ROOT)],
        capture_output=True, text=True, timeout=20, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'zero network/model calls' in result.stdout


@pytest.mark.parametrize('mutation,status', [('input', 'FAILED'), ('upstream', 'ERROR'), ('stale', 'ERROR'),
    ('version', 'ERROR'), ('digest', 'ERROR'), ('review-input', 'FAILED'), ('design-lineage', 'FAILED'), ('content', 'ERROR')])
def test_trusted_check_rejects_forged_inputs_unverified_upstream_and_stale_artifacts(mutation, status):
    # Pure repository test doubles, no SQLite or network writes.
    req = [{'id': 'R1', 'text': 'Actual analysis'}, {'id': 'R2', 'text': 'Actual constraint'}]
    analysis = SimpleNamespace(id='a', node_id='analyse', output={'request_id': 'nonce', 'requirements': req},
        state=TaskState.SUCCEEDED, verified=True, input={'request_id': 'nonce', 'task': 'bounded task'})
    design = SimpleNamespace(id='d', node_id='design', output={'request_id': 'nonce', 'requirements': req, 'components': ['actual']},
        state=TaskState.SUCCEEDED, verified=True, input={'request_id': 'nonce', 'analysis': analysis.output})
    output = {'request_id': 'nonce', 'requirements': req, 'components': ['actual'], 'issues': [], 'summary': 'review'}
    review = SimpleNamespace(id='r', node_id='review', state=TaskState.VERIFYING, verified=False, execution_id='execution',
        input={'request_id': 'nonce', 'analysis': analysis.output, 'design': design.output})
    report = SimpleNamespace(run=SimpleNamespace(id='run'), input={'request_id': 'nonce', 'task': 'bounded task'},
                             nodes=(analysis, design, review))
    artifact = ArtifactVersion(VersionRef('r:artifact', '1'), StructuredVerificationBackend().digest(output), output, True)
    session = {'run_id': 'run', 'current_attempt': 'attempt', 'node_key': 'review', 'state': 'running'}
    attempt = {'run_id': 'run', 'node_id': 'r', 'execution_id': 'execution', 'state': 'running',
               'artifact': {'ref': {'id': 'r:artifact', 'version': '1'}, 'digest': artifact.digest, 'content': deepcopy(output)}}
    if mutation == 'input': review.input['analysis'] = {'forged': True}
    if mutation == 'upstream': analysis.verified = False
    if mutation == 'stale': session['state'] = 'failed'
    if mutation == 'version': attempt['artifact']['ref']['version'] = '0'
    if mutation == 'digest': attempt['artifact']['digest'] = 'old-digest'
    if mutation == 'review-input': review.input['design'] = {'forged': True}
    if mutation == 'design-lineage': design.input['analysis'] = {'forged': True}
    if mutation == 'content': attempt['artifact']['content'] = {'forged': True}
    class Workflows:
        async def get_report(self, run_id): return report
    class Safety:
        async def session(self, node_id): return session
        async def attempt(self, attempt_id): return attempt
    result = asyncio.run(SerialCollaborationChecks(Workflows(), Safety()).review(artifact))
    assert result.status == status and result.reference == artifact.digest
