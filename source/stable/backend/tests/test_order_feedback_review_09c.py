"""SIMULATED HTTP replay of public outputs; no live credentials or network."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from futurecup.adapters.order_challenge import OrderChallengeChecks, fingerprint
from futurecup.application.feedback import FeedbackEngine
from futurecup.domain.models import VerificationVerdict, VersionRef
from futurecup.domain.verification import ArtifactVersion, CheckEvidence, RecoveryLimits
from test_selfloop_challenge_09c import (
    PROBLEM, SUMMARY, TEMPLATE, VALID, WITNESS, SimulatedHTTP, assert_compact_evidence, execute, register,
)
from test_selfloop_real_09b import application

HISTORICAL_OUTPUT = {'task': 'dependency_schedule', 'order': list('BEDCGAFH'), 'total': 13}


def check(group, value):
    return asyncio.run(OrderChallengeChecks(None, None).check(
        group, ArtifactVersion(VersionRef('offline', '1'), 'public-digest', value, True)))


@pytest.mark.parametrize('order,missing,duplicate,illegal', [
    ('ABCDEFG', ['H'], [], []),
    ('AABCDEFG', ['H'], ['A'], []),
    ('ABCDEFGZ', ['H'], [], ['Z']),
])
def test_precise_membership_errors_and_blocked_rules(order, missing, duplicate, illegal):
    value = {**VALID, 'order': list(order)}
    before = deepcopy(value)
    result = check('membership', value)
    assert result.status == 'FAILED' and result.reason == 'order09c_not_exact_task_permutation'
    assert result.details['missing_tasks'] == missing
    assert result.details['duplicate_tasks'] == duplicate
    assert result.details['illegal_tasks'] == illegal
    for group in ('dependencies', 'priority', 'adjacency', 'windows', 'completion', 'gap'):
        blocked = check(group, value)
        assert blocked.status == 'INCONCLUSIVE'
        assert blocked.details == {'evaluation': 'NOT_EVALUATED', 'blocked_by': 'order09c_membership'}
        assert blocked.reason == 'order09c_not_evaluated_precondition'
        assert 'repair_hint' not in blocked.details
    assert value == before


def test_incomplete_permutation_remains_failed_and_context_has_no_blocked_repetition(monkeypatch):
    bad = {**VALID, 'order': list('ABCDEFG')}
    simulator = SimulatedHTTP(bad, VALID)
    with TestClient(application(monkeypatch, simulator)) as client:
        result, attempts, checks, feedback = execute(client, register(client))
        assert [v['status'] for v in checks] == ['FAILED', 'PASSED']
        assert result['verified'] and len(simulator.requests) == 2
        context = attempts[1]['input_value']['recovery']['verification']
        assert_compact_evidence(context, checks[0])
        assert context['not_evaluated_count'] == 6
        assert len([e for e in checks[0]['evidence'] if e['status'] == 'INCONCLUSIVE']) == 6
        assert all(e['reason'] != 'order09c_not_evaluated_precondition' for e in context['evidence'])
        assert feedback[0]['origin'] == 'artifact' and feedback[0]['repair_action'] == 'prompt_retry'


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_historical_output_replay_uses_real_validation_feedback_only(monkeypatch, tmp_path, repair_succeeds):
    """Replay is explicitly simulated, never evidence of successful real-model repair."""
    simulator = SimulatedHTTP(HISTORICAL_OUTPUT, VALID if repair_succeeds else HISTORICAL_OUTPUT, VALID)
    with TestClient(application(monkeypatch, simulator, tmp_path / 'isolated.sqlite3')) as client:
        result, attempts, checks, feedback = execute(client, register(client))
        assert result['run']['state'] == ('succeeded' if repair_succeeds else 'failed')
        assert result['verified'] is repair_succeeds and result['simulated'] is True
        assert result['resource_usage']['model_calls'] == 2 and result['resource_usage']['feedback_loops'] == 1
        assert len(simulator.requests) == 2 and len(simulator.responses) == 1
        evidence = {e['rule']: e for e in checks[0]['evidence'] if e['status'] == 'FAILED'}
        assert set(evidence) == {'order09c_membership', 'order09c_dependencies', 'order09c_windows', 'order09c_completion'}
        total = evidence['order09c_membership']
        assert total['path'] == 'total' and total['reason'] == 'order09c_total_mismatch'
        assert total['details']['actual_total'] == 13 and total['details']['expected_total'] == 8
        dependencies = evidence['order09c_dependencies']['details']['violations']
        assert {(v['before'], v['after']) for v in dependencies} == {('A', 'D'), ('C', 'D')}
        assert evidence['order09c_completion']['details']['violations'] == [
            {'task': 'C', 'latest': 4, 'actual_completion': 7}]
        assert evidence['order09c_windows']['details']['violations'] == [
            {'task': 'C', 'min': 2, 'max': 3, 'actual_position': 4}]
        assert len({e['details']['repair_hint'] for e in evidence.values()}) == 4
        incoming = json.loads(simulator.requests[1]['messages'][-1]['content'])
        assert incoming == attempts[1]['input_value']
        recovery = incoming['recovery']
        assert_compact_evidence(recovery['verification'], checks[0])
        assert len(recovery['verification']['evidence']) == 4
        assert len(recovery['reason_codes']) == len(set(recovery['reason_codes'])) == 4
        assert recovery['previous_artifact'] == {k: attempts[0]['artifact'][k] for k in (
            'ref', 'digest', 'content', 'format_error', 'execution_error')}
        assert recovery['previous_attempt_id'] == attempts[0]['id']
        assert recovery['feedback']['id'] == feedback[0]['record']['id']
        assert feedback[0]['evidence_refs'] == [checks[0]['record']['id']]
        assert len(checks[0]['evidence']) == 9 and 'checks' not in recovery
        assert len(json.dumps(incoming)) < 5000  # Measured characters, not provider token claims.
        assert json.dumps(list(WITNESS)) not in json.dumps(incoming)
        assert attempts[0]['artifact']['content'] == HISTORICAL_OUTPUT
        assert len({a['policy_digest'] for a in attempts}) == 1
        (tmp_path / 'context-measurement.json').write_text(json.dumps({
            'simulated': True, 'second_input_chars': len(json.dumps(incoming)),
            'verification_chars': len(json.dumps(recovery['verification'])),
            'failed_rules': sorted(evidence), 'model_calls': len(simulator.requests),
        }), encoding='utf-8')


def test_not_evaluated_without_failed_parent_is_inconclusive_never_passed(monkeypatch):
    simulator = SimulatedHTTP({**VALID, 'order': list('ABCDEFG')})
    with TestClient(application(monkeypatch, simulator)) as client:
        definition = register(client)
        policy = deepcopy(definition['policy'])
        policy['id'] += '-partial'
        policy['checks'] = [c for c in policy['checks'] if c['config'].get('test_id') == 'order09c-dependencies-v2']
        assert client.post('/api/v1/verification-policies', json=policy).status_code == 201
        definition['workflow']['id'] += '-partial'
        definition['workflow']['nodes'][0]['config']['verification']['id'] = policy['id']
        assert client.post('/api/v1/workflows', json=definition['workflow']).status_code == 201
        result, attempts, checks, feedback = execute(client, definition)
        assert not result['verified'] and result['output'] is None
        assert checks[0]['status'] == 'INCONCLUSIVE' and len(simulator.requests) == 1
        assert feedback[0]['repair_action'] == 'abort'


def test_duplicate_errors_compact_once_but_full_mode_preserves_all_sources():
    error = CheckEvidence('external_tests', 'FAILED', 'same_error', 'order', 'digest', 'first',
                          details={'repair_hint': 'Fix the actual missing task.', 'missing_tasks': ['H']})
    duplicate = CheckEvidence(**{**asdict(error), 'rule': 'second'})
    result = SimpleNamespace(record=SimpleNamespace(id='verification', verdict=VerificationVerdict.FAIL),
        origin='artifact', artifact_digest='digest', evidence=(error, duplicate))
    engine = FeedbackEngine()
    compact = engine.verification_context(result, 'errors_only')
    assert len(compact['evidence']) == 1 and compact['source_evidence_count'] == 2
    assert compact['evidence'][0]['details'] == error.details
    full = engine.verification_context(result, 'full')
    assert full['evidence'] == [asdict(error), asdict(duplicate)]
    assert len(result.evidence) == 2


def test_constraints_and_safety_limits_unchanged_but_contract_unambiguous():
    assert fingerprint(PROBLEM.spec) == '851e1cbd4754d4d91bf5018c2df88c1268a0990bb9b0d3b70d0176b462939561'
    assert sum(len(v) for v in PROBLEM.spec['constraints'].values()) == 18
    assert SUMMARY['verifier_version'] == 2 and SUMMARY['solution_count'] == 1
    assert TEMPLATE['policy']['version'] == '2'
    node = TEMPLATE['workflow']['nodes'][0]['config']
    assert node['verification']['version'] == '2'
    assert node['recovery']['feedback_mode'] == 'errors_only'
    assert node['recovery']['max_attempts'] == 2 and node['recovery']['max_feedback_loops'] == 1
    assert node['limits']['max_output_tokens'] == 256 and node['limits']['max_model_calls'] == 1
    assert TEMPLATE['workflow']['resource_limits']['max_calls'] == 2
    assert TEMPLATE['workflow']['max_concurrency'] == 1
    assert 'total is the number of task IDs (8)' in TEMPLATE['input']['instructions']
    assert 'not cumulative duration' in TEMPLATE['agent']['system_prompt']
    assert RecoveryLimits().feedback_mode == 'full'
    with pytest.raises(ValueError, match='feedback context'):
        RecoveryLimits(feedback_mode='skip_validation')
