"""09-C offline proof and SIMULATED HTTP only, never real model inference."""

import asyncio
from copy import deepcopy
from itertools import permutations
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from futurecup.adapters.order_challenge import (
    CHECK_IDS, GROUPS, OrderChallengeChecks, check_definition, feasibility, frozen_definition,
    input_schema, problem_from, violations,
)
from futurecup.domain.models import VersionRef
from futurecup.domain.verification import ArtifactVersion
from test_real_llm_integration import completion
from test_selfloop_real_09b import application
from test_workflow_api import completed

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = json.loads((ROOT / 'config/deepseek-selfloop09c.json').read_text('utf-8'))
PROBLEM, SUMMARY = frozen_definition()
WITNESS = feasibility(PROBLEM)[1]
VALID = {'task': 'dependency_schedule', 'order': list(WITNESS), 'total': 8}
BAD = {**VALID, 'order': list('ABCDEFGH')}
STILL_BAD = {**VALID, 'order': list('BACEDGFH')}


def assert_compact_evidence(context, stored):
    """Every transmitted failure must equal its source; full evidence stays queryable."""
    expected = [{k: e[k] for k in ('reason', 'path', 'rule', 'schema_path', 'details') if e[k] is not None}
                for e in stored['evidence'] if e['status'] == 'FAILED']
    assert context['evidence'] == expected
    assert context['source_evidence_count'] == len(stored['evidence'])
    assert context['id'] == stored['record']['id']
    assert context['artifact_digest'] == stored['artifact_digest']
    assert context['not_evaluated_count'] == sum(e['reason'] == 'order09c_not_evaluated_precondition' for e in stored['evidence'])


class SimulatedHTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def __call__(self, request):
        body = json.loads(request.content)
        assert body['model'] == 'deepseek-flash' and body['max_tokens'] == 256
        assert 'tools' not in body and 'response_format' not in body
        self.requests.append(body)
        response = self.responses.pop(0)
        if isinstance(response, int):
            return httpx.Response(response, text='SIMULATED error')
        if isinstance(response, Exception):
            raise response
        return httpx.Response(200, json=completion(response if isinstance(response, str) else json.dumps(response),
                                                model='SIMULATED09C'))


def register(client, suffix='one', *, weakened_input_schema=False):
    value = deepcopy(TEMPLATE)
    value['agent']['id'] = 'selfloop09c-' + suffix + '-agent'
    value['policy']['id'] = 'selfloop09c-' + suffix + '-rule'
    value['workflow']['id'] = 'selfloop09c-' + suffix + '-flow'
    node = value['workflow']['nodes'][0]['config']
    node['agent_id'] = value['agent']['id']
    node['verification']['id'] = value['policy']['id']
    if weakened_input_schema:
        value['workflow']['input_schema'] = {}  # Adversarial API caller, not production definition.
    capability = client.get('/api/v1/verification-capabilities').json()
    assert capability['challenge09c'] == SUMMARY
    assert 'witness' not in capability['challenge09c']
    for route, item in [('agents', value['agent']), ('verification-policies', value['policy']), ('workflows', value['workflow'])]:
        response = client.post('/api/v1/' + route, json=item)
        assert response.status_code == 201, response.text
    return value


def submit(client, value, data=None):
    return client.post('/api/v1/workflows/' + value['workflow']['id'] + '/executions',
                       json={'input': value['input'] if data is None else data})


def execute(client, value):
    response = submit(client, value)
    assert response.status_code == 202, response.text
    result = completed(client, response.json()['run']['id'])
    node_id = result['nodes'][0]['id']
    attempts = client.get('/api/v1/self-loops/' + node_id + '/attempts').json()
    checks = client.get('/api/v1/self-loops/' + node_id + '/verifications').json()
    feedback = client.get('/api/v1/self-loops/' + node_id + '/feedback').json()
    return result, attempts, checks, feedback


def test_input_is_feasible_by_exhaustive_search_not_assumed():
    assert len(PROBLEM.ids) == 8 and SUMMARY['feasible'] and SUMMARY['solution_count'] == 1
    assert WITNESS is not None and not any(violations(PROBLEM, WITNESS).values())
    # Human-reviewable, independent arithmetic on the witness: completion C=4, F=12, H=13.
    elapsed, finish = 0, {}
    durations = {t['id']: t['duration'] for t in TEMPLATE['input']['spec']['tasks']}
    for task in WITNESS:
        elapsed += durations[task]
        finish[task] = elapsed
    assert finish['C'] == 4 and finish['F'] == 12 and finish['H'] == 13


@pytest.mark.parametrize('case,outputs,statuses,final', [
    ('A-first-pass', [VALID], ['PASSED'], 'succeeded'),
    ('B-repaired', [BAD, VALID], ['FAILED', 'PASSED'], 'succeeded'),
    ('C-still-invalid', [BAD, STILL_BAD, VALID], ['FAILED', 'FAILED'], 'failed'),
])
def test_paths_actual_feedback_and_sqlite(monkeypatch, tmp_path, case, outputs, statuses, final):
    simulator = SimulatedHTTP(*deepcopy(outputs))
    before = deepcopy(outputs)
    path = tmp_path / (case + '.sqlite3')
    with TestClient(application(monkeypatch, simulator, path)) as client:
        definition = register(client)
        result, attempts, checks, feedback = execute(client, definition)
        assert result['run']['state'] == final and result['verified'] == (final == 'succeeded')
        assert result['simulated'] is True and [v['status'] for v in checks] == statuses
        assert len(simulator.requests) == len(attempts) == len(statuses)
        assert result['resource_usage']['model_calls'] == len(statuses)
        assert result['resource_usage']['feedback_loops'] == len(statuses) - 1
        assert len({a['policy_digest'] for a in attempts}) == 1
        assert len({a['id'] for a in attempts}) == len(attempts)
        assert json.loads(simulator.requests[0]['messages'][-1]['content']) == definition['input']
        if len(statuses) == 2:
            incoming = json.loads(simulator.requests[1]['messages'][-1]['content'])
            recovery = incoming['recovery']
            assert incoming == attempts[1]['input_value']
            assert incoming['original_input'] == definition['input']
            assert recovery['previous_artifact']['content'] == attempts[0]['artifact']['content'] == BAD
            assert recovery['previous_artifact']['digest'] == attempts[0]['artifact']['digest']
            assert recovery['previous_attempt_id'] == attempts[0]['id']
            assert recovery['verification']['id'] == checks[0]['record']['id']
            assert_compact_evidence(recovery['verification'], checks[0])
            assert len(checks[0]['evidence']) == 9 and 'checks' not in recovery
            assert recovery['feedback']['id'] == feedback[0]['record']['id']
            assert feedback[0]['evidence_refs'] == [checks[0]['record']['id']]
            assert feedback[0]['category'] == 'ASSERTION_FAILED' and feedback[0]['repair_action'] == 'prompt_retry'
            assert len(json.dumps(incoming)) < 8192
            failed = [e for e in checks[0]['evidence'] if e['status'] == 'FAILED']
            assert {e['rule'] for e in failed} == {'order09c_priority', 'order09c_adjacency', 'order09c_completion'}
            completion = next(e for e in failed if e['rule'] == 'order09c_completion')
            assert completion['details']['violations'] == [{'task': 'C', 'latest': 4, 'actual_completion': 6}]
            assert all(e['details']['repair_hint'] for e in failed)
            assert json.dumps(list(WITNESS)) not in json.dumps(recovery['verification']['evidence'])
            stored_input = client.get('/api/v1/executions/' + attempts[1]['execution_id']).json()['input']
            assert json.loads(stored_input) == incoming
        else:
            assert feedback == [] and result['resource_usage']['feedback_loops'] == 0
        if final == 'failed':
            assert result['output'] is None and len(simulator.responses) == 1  # No third call.
            assert attempts[1]['artifact']['content'] == STILL_BAD and feedback[-1]['repair_action'] == 'abort'
            assert checks[1]['record']['reason_codes'] == ['order09c_completion_violation']
        else:
            assert result['output']['schedule'] == attempts[-1]['artifact']['content'] == VALID
        assert outputs == before
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        snapshot = json.loads(db.execute('SELECT snapshot FROM workflow_runs').fetchone()[0])
        assert snapshot['run']['id'] == result['run']['id'] and snapshot['simulated'] is True
        assert db.execute("SELECT count(*) FROM safety_records WHERE kind='attempt'").fetchone()[0] == len(statuses)


@pytest.mark.parametrize('bad', ['{invalid json', {**VALID, 'order': list('AABCDEFG')},
    {**VALID, 'order': list('ABCDEFG')}, {**VALID, 'total': True}, {**VALID, 'extra': 1}])
def test_format_membership_and_schema_errors_are_repaired_only_by_next_response(monkeypatch, bad):
    simulator = SimulatedHTTP(bad, VALID)
    with TestClient(application(monkeypatch, simulator)) as client:
        result, attempts, checks, feedback = execute(client, register(client))
        assert result['verified'] and [v['status'] for v in checks] == ['FAILED', 'PASSED']
        assert len(simulator.requests) == 2
        incoming = json.loads(simulator.requests[1]['messages'][-1]['content'])
        assert incoming['recovery']['previous_artifact']['content'] == bad
        assert_compact_evidence(incoming['recovery']['verification'], checks[0])


@pytest.mark.parametrize('group', GROUPS[2:])
def test_each_group_reports_every_actual_violation_without_changing_candidate(group):
    candidate = next(p for p in permutations(PROBLEM.ids) if violations(PROBLEM, p)[group])
    expected = violations(PROBLEM, candidate)[group]
    value = {'task': 'dependency_schedule', 'order': list(candidate), 'total': 8}
    before = deepcopy(value)
    checks = OrderChallengeChecks(None, None)  # Semantic groups do not need repository access.
    result = asyncio.run(checks.check(group, ArtifactVersion(VersionRef('unused', '1'), 'digest', value, True)))
    assert result.status == 'FAILED' and result.reference == 'digest'
    assert result.details['violations'] == expected and result.details['repair_hint']
    assert value == before


@pytest.mark.parametrize('conflict', ['cycle', 'impossible-deadline'])
def test_constraint_conflicts_are_proven_unsatisfiable_and_cli_refuses(conflict, tmp_path):
    value = deepcopy(TEMPLATE)
    spec = value['input']['spec']
    if conflict == 'cycle': spec['constraints']['dependencies'].append(['H', 'A'])
    else: spec['constraints']['completion_deadlines'].append({'task': 'D', 'latest': 0})
    problem = problem_from(spec)
    assert feasibility(problem) == (0, None)
    value['workflow']['input_schema'] = input_schema(problem)
    path = tmp_path / 'unsatisfiable-public-definition.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='Unsatisfiable'):
        check_definition(path)
    result = proof_cli(path)
    assert result.returncode == 1 and json.loads(result.stdout)['feasible'] is False
    assert result.stderr == ''


def proof_cli(path):
    allowed = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA'}
    env = {k: os.environ[k] for k in os.environ if k.upper() in allowed}
    return subprocess.run([sys.executable, '-B', '-m', 'futurecup.adapters.order_challenge', '--check-definition', str(path)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)


def test_local_cli_proof_contains_no_model_answer():
    result = proof_cli(ROOT / 'config/deepseek-selfloop09c.json')
    assert result.returncode == 0 and json.loads(result.stdout) == SUMMARY
    assert 'witness' not in result.stdout and json.dumps(list(WITNESS)) not in result.stdout


def test_changed_constraints_rejected_before_call_and_weakened_api_cannot_fake_verified(monkeypatch):
    simulator = SimulatedHTTP(VALID)
    with TestClient(application(monkeypatch, simulator)) as client:
        definition = register(client)
        data = deepcopy(definition['input'])
        data['spec']['constraints']['dependencies'].append(['H', 'A'])
        rejected = submit(client, definition, data)
        assert rejected.status_code == 422 and not simulator.requests
        assert client.get('/api/v1/workflow-runs').json() == []
        other = register(client, 'weakened', weakened_input_schema=True)
        response = submit(client, other, data)
        report = completed(client, response.json()['run']['id'])
        assert report['run']['state'] == 'failed' and not report['verified'] and len(simulator.requests) == 1
        checks = client.get('/api/v1/self-loops/' + report['nodes'][0]['id'] + '/verifications').json()
        assert checks[0]['status'] == 'ERROR' and 'order09c_instance_unbound' in checks[0]['record']['reason_codes']


@pytest.mark.parametrize('prices,expensive', [(False, False), (True, True)])
def test_unknown_or_over_budget_prices_never_send_http(monkeypatch, prices, expensive):
    simulator = SimulatedHTTP(VALID)
    with TestClient(application(monkeypatch, simulator, prices=prices, expensive=expensive)) as client:
        result, attempts, checks, feedback = execute(client, register(client))
        assert not simulator.requests and result['resource_usage']['model_calls'] == 0
        assert result['run']['state'] == 'failed' and feedback[0]['repair_action'] == 'abort'


def test_repeated_runs_keep_history_and_feedback_isolation(monkeypatch):
    simulator = SimulatedHTTP(BAD, VALID, BAD, VALID)
    with TestClient(application(monkeypatch, simulator)) as client:
        definition = register(client)
        first, a1, v1, f1 = execute(client, definition)
        second, a2, v2, f2 = execute(client, definition)
        assert first['run']['id'] != second['run']['id']
        assert client.get('/api/v1/workflow-runs/' + first['run']['id']).json() == first
        assert a2[1]['input_value']['recovery']['feedback']['id'] == f2[0]['record']['id'] != f1[0]['record']['id']


@pytest.mark.parametrize('scenario', ['preflight','A','B','C','D','E','repeatability','missing-price','over-budget',
    'missing-budget','wrong-currency','provider-retry','small-context','old-backend','template-drift','unconfirmed-prices','unsatisfiable','spec-mismatch'])
def test_script_proof_and_authorization_without_network(scenario):
    result = subprocess.run(['powershell.exe','-NoProfile','-File',str(ROOT / 'backend/tests/powershell/selfloop09c_harness.ps1'),
        '-Scenario',scenario,'-ProjectRoot',str(ROOT)],capture_output=True,text=True,cwd=ROOT,timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'zero network/model calls' in result.stdout
