"""Offline durability tests: real SQLite restarts and hard process exits, Mock models only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from futurecup.adapters.recovery_ledger import SQLiteRecoveryLedger, digest
from futurecup.application.durability import DurableExecution
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.ledger import CallContext
from futurecup.domain.models import VersionRef, RunLimits
from futurecup.domain.verification import ResourceBudget
from futurecup.adapters.tools import ToolDefinition, InMemoryToolRegistry
from runtime_support import service, agent, response, tool_call, ScriptedMockProvider
from verification_support import safety_engine, setup, definition, close
from futurecup.application.local_recovery import LocalRecoveryService

ROOT = Path(__file__).resolve().parents[2]


def journal(tmp_path, *, calls=6, units=100000):
    ledger = SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))
    ledger.create_account('account','task',max_calls=calls,max_units=units)
    return ledger


def context(name='reservation', kind='model', units=10):
    return CallContext('account',name,'run','node','execution',kind,digest({'value':1}),units)


def start(ledger, safety='idempotent'):
    ctx=context(kind='tool');ledger.reserve(ctx)
    return ledger.start_tool(ctx,tool_call_id='call',tool_name='effect',tool_version='1',
        side_effect_class=safety,replay_scope='node')


def crash(tmp_path, mode):
    env = {'SystemRoot':os.environ.get('SystemRoot',r'C:\Windows'),'PYTHONUTF8':'1',
           'PYTHONPATH':os.pathsep.join([str(ROOT/'backend/src'),str(ROOT/'backend/tests')]),
           'TEMP':str(tmp_path),'TMP':str(tmp_path)}
    process=subprocess.run([sys.executable,str(ROOT/'backend/tests/ledger_crash_worker.py'),str(tmp_path),mode],
        cwd=ROOT,env=env,capture_output=True,timeout=20)
    assert process.returncode == 77, process.stderr.decode(errors='replace')
    return SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))


def test_budget_reserve_commit_release_are_idempotent_and_account_never_resets(tmp_path):
    ledger=journal(tmp_path,calls=2,units=20)
    try:
        c=context();ledger.reserve(c);ledger.reserve(c)
        assert ledger.account('account')['occupied_calls']==1
        with pytest.raises(RuntimeFault,match='Durability'):
            ledger.reserve(replace(c,units=11))
        ledger.dispatch(c.reservation_id);ledger.commit(c.reservation_id,8,'evidence');ledger.commit(c.reservation_id,8,'evidence')
        assert ledger.account('account')['occupied_units']==8
        with pytest.raises(RuntimeFault):ledger.commit(c.reservation_id,7,'evidence')
        with pytest.raises(RuntimeFault):ledger.release(c.reservation_id)
        ledger.reserve(context('prepared'));ledger.release('prepared');ledger.release('prepared')
        assert ledger.account('account')['occupied_calls']==1
        before=ledger.account('account')
        assert ledger.create_account('account','task',max_calls=2,max_units=20)==before
        with pytest.raises(RuntimeFault):ledger.create_account('new','task',max_calls=2,max_units=20)
        with pytest.raises(RuntimeFault):ledger.create_account('account','task',max_calls=3,max_units=20)
    finally:ledger.close()


@pytest.mark.parametrize('mode,expected,occupied',[('reserve','RELEASED',0),('dispatch','UNKNOWN',1)])
def test_hard_crash_after_reserve_or_before_commit(tmp_path,mode,expected,occupied):
    ledger=crash(tmp_path,mode)
    try:
        with pytest.raises(RuntimeFault):ledger.recover('crashed-worker')
        ledger.recover('crashed-worker',confirmed_stopped=True)
        assert ledger.reservation('reservation')['state']==expected
        assert ledger.account('account')['occupied_calls']==occupied
        before=ledger.account('account')
        ledger.recover('crashed-worker',confirmed_stopped=True)
        assert ledger.account('account')==before
        if expected=='UNKNOWN':
            with pytest.raises(RuntimeFault):ledger.release('reservation')
    finally:ledger.close()


@pytest.mark.parametrize('safety',['unknown','irreversible'])
def test_external_tool_completed_before_hard_crash_is_unknown_never_auto_replayed(tmp_path,safety):
    ledger=crash(tmp_path,'tool-'+safety)
    try:
        ledger.recover('crashed-worker',confirmed_stopped=True)
        tool=dict(ledger.db.execute('SELECT * FROM ledger_tools').fetchone())
        assert tool['state']=='UNKNOWN'
        with pytest.raises(RuntimeFault):ledger.claim_unknown_tool(tool['id'],'retry',allow_idempotent=True)
        assert (tmp_path/'external-effect.txt').read_text()=='External substitute completed'
        codes={f.code for f in ledger.reconcile()}
        assert {'tool_outcome_unknown','budget_settlement_unknown'}<=codes
    finally:ledger.close()


def test_idempotent_unknown_recovery_requires_policy_and_charges_extra_physical_call(tmp_path):
    ledger=crash(tmp_path,'tool-idempotent')
    async def run():
        try:
            ledger.recover('crashed-worker',confirmed_stopped=True)
            entry=dict(ledger.db.execute('SELECT * FROM ledger_tools').fetchone())
            registry=InMemoryToolRegistry();actual=[]
            async def effect(value):actual.append(value);return {'value':value}
            registry.register(ToolDefinition(VersionRef('effect','1'),'SIMULATED idempotent set',{'type':'object'},'idempotent'),effect)
            coordinator=DurableExecution(ledger)
            with pytest.raises(RuntimeFault):await coordinator.recover_tool(entry['id'],'r',registry,(VersionRef('effect','1'),),{'value':1})
            result=await coordinator.recover_tool(entry['id'],'r',registry,(VersionRef('effect','1'),),{'value':1},authorize_idempotent=True)
            assert result=={'value':1} and actual==[1]
            assert ledger.reservation('reservation')['state']=='UNKNOWN'
            assert ledger.account('account')['occupied_calls']==2
            assert ledger.account('account')['unknown_calls']==1
            assert await coordinator.recover_tool(entry['id'],'another',registry,(VersionRef('effect','1'),),{'value':1},authorize_idempotent=True)==result
            assert actual==[1] and ledger.account('account')['occupied_calls']==2
        finally:ledger.close()
    asyncio.run(run())


def test_recovery_cannot_consume_phantom_budget_or_reset_memory_snapshot(tmp_path):
    ledger=journal(tmp_path,calls=1,units=10);old_worker=ledger.worker_id;entry=start(ledger);ledger.close()
    other=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))
    try:
        other.recover(old_worker,confirmed_stopped=True)
        with pytest.raises(RuntimeFault) as error:other.claim_unknown_tool(entry['id'],'r',allow_idempotent=True)
        assert error.value.code=='ledger_budget_exhausted'
        coordinator=DurableExecution(other)
        with pytest.raises(RuntimeFault):coordinator.bind(ResourceBudget(max_calls=1,max_resource_units=10),'account','task')
        with pytest.raises(RuntimeFault):coordinator.restore(ResourceBudget(max_calls=1,max_resource_units=10),'account')
        budget=ResourceBudget(max_calls=1,max_resource_units=10,calls=1,resource_units=10,model_calls=0,tool_calls=1)
        coordinator.restore(budget,'account')
        assert budget.ledger_account_id=='account' and budget.simulation_only
        assert other.account('account')['occupied_calls']==1
    finally:other.close()


def test_restart_concurrent_recovery_has_exactly_one_claim_and_no_duplicate_charge(tmp_path):
    original=journal(tmp_path);entry=start(original);dead=original.worker_id;original.close()
    a=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'));b=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))
    try:
        a.recover(dead,confirmed_stopped=True);b.recover(dead,confirmed_stopped=True)
        barrier=Barrier(2)
        def claim(worker):barrier.wait();return worker.claim_unknown_tool(entry['id'],'same',allow_idempotent=True)
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(claim,[a,b]))
        assert sorted(results)==[False,True]
        assert a.account('account')['occupied_calls']==2
        assert a.db.execute('SELECT COUNT(*) FROM ledger_recoveries').fetchone()[0]==1
        assert not b.claim_unknown_tool(entry['id'],'different',allow_idempotent=True)
    finally:a.close();b.close()


def test_capacity_reservation_is_atomic_across_independent_connections(tmp_path):
    original=journal(tmp_path,calls=1,units=10);original.close()
    workers=[SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3')) for _ in range(2)]
    try:
        barrier=Barrier(2)
        def reserve(pair):
            i,worker=pair;barrier.wait()
            try:worker.reserve(context(str(i)));return True
            except RuntimeFault as e:assert e.code=='ledger_budget_exhausted';return False
        with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(reserve,enumerate(workers)))==[False,True]
        assert workers[0].account('account')['occupied_units']==10
    finally:
        for w in workers:w.close()


def test_tool_ledger_result_and_budget_commit_are_atomic_but_missing_result_is_diagnosed(tmp_path):
    ledger=journal(tmp_path);entry=start(ledger)
    try:
        # Force the UPDATE to fail after budget commit in the same transaction.
        ledger.db.execute("CREATE TRIGGER fail_result BEFORE UPDATE ON ledger_tools BEGIN SELECT RAISE(ABORT,'injected'); END")
        with pytest.raises(sqlite3.IntegrityError):ledger.succeed_tool(entry['id'],'reservation',{'value':1})
        assert ledger.reservation('reservation')['state']=='RESERVED'
        assert ledger.tool(entry['id'])['state']=='STARTED'
        ledger.db.execute('DROP TRIGGER fail_result')
        ledger.succeed_tool(entry['id'],'reservation',{'value':1})
        before=ledger.account('account')
        ledger.db.execute('UPDATE ledger_tools SET result_json=NULL WHERE id=?',(entry['id'],))
        assert 'tool_result_missing_or_corrupt' in {x.code for x in ledger.reconcile()}
        with pytest.raises(RuntimeFault):ledger.tool_result(entry['id'])
        assert ledger.tool(entry['id'])['state']=='SUCCEEDED' and ledger.account('account')==before
    finally:ledger.close()


def test_reconciliation_reports_mismatches_without_silent_repair(tmp_path):
    ledger=journal(tmp_path);entry=start(ledger);ledger.succeed_tool(entry['id'],'reservation',{'value':1})
    try:
        before=ledger.db.total_changes
        findings=ledger.reconcile(({'reservation_id':'reservation','input_hash':'wrong','units':999,'state':'running','result_digest':'wrong'},
            {'reservation_id':'not-reserved','state':'succeeded'}),complete_inventory=True)
        assert {'budget_execution_mismatch','tool_workflow_state_mismatch','tool_workflow_result_mismatch','execution_without_reservation'}<={f.code for f in findings}
        assert ledger.db.total_changes==before
        assert 'budget_without_execution_record' in {f.code for f in ledger.reconcile(complete_inventory=True)}
    finally:ledger.close()


def test_real_workflow_tool_commit_survives_process_crash_before_execution_record_save(tmp_path):
    ledger=crash(tmp_path,'workflow')
    try:
        ledger.recover('crashed-worker',confirmed_stopped=True)
        entry=dict(ledger.db.execute('SELECT * FROM ledger_tools').fetchone())
        assert entry['state']=='SUCCEEDED' and ledger.tool_result(entry['id'])=={'value':1}
        with sqlite3.connect(tmp_path/'workflow.sqlite3') as db:
            snapshot=db.execute('SELECT snapshot FROM workflow_runs').fetchone()[0]
        import json
        report=json.loads(snapshot)
        assert report['run']['state'] not in ('succeeded','failed','cancelled')
        assert report['budget_account_id']=='account'
        assert 'budget_without_execution_record' in {f.code for f in ledger.reconcile(complete_inventory=True)}
        assert entry['run_id']==report['run']['id']
    finally:ledger.close()


def test_old_worker_is_fenced_and_cannot_late_commit_unknown_request(tmp_path):
    old=journal(tmp_path);old.reserve(context());old.dispatch('reservation')
    new=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))
    try:
        new.recover(old.worker_id,confirmed_stopped=True)
        with pytest.raises(RuntimeFault) as error:old.commit('reservation',10,'late')
        assert error.value.code=='ledger_worker_fenced'
        assert new.reservation('reservation')['state']=='UNKNOWN'
    finally:old.close();new.close()


def test_wrong_database_is_rejected_without_adding_tables(tmp_path):
    path=tmp_path/'history.sqlite3'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE workflow_runs(id TEXT)')
    with pytest.raises(RuntimeFault) as error:SQLiteRecoveryLedger(str(path))
    assert error.value.code=='ledger_database_not_isolated'
    with sqlite3.connect(path) as db:assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[('workflow_runs',)]


def test_runtime_real_engine_model_and_tool_calls_commit_same_budget_and_audit_ids(tmp_path):
    async def run():
        ledger=journal(tmp_path)
        runtime=service(ScriptedMockProvider(response('',(tool_call(),)),response('[MOCK] complete')))
        runtime.durability=DurableExecution(ledger)
        budget=ResourceBudget(max_calls=6,max_resource_units=100000)
        runtime.durability.bind(budget,'account','task')
        try:
            await runtime.registry.register(agent(tool_refs=(VersionRef('calculator','1'),)))
            result=await runtime.wait((await runtime.submit('analyst','[MOCK] sum',shared_budget=budget)).execution.id)
            assert result.execution.state.value=='succeeded'
            assert result.tool_calls[0].output=={'result':5}
            assert all(c.reservation_id for c in (*result.model_calls,*result.tool_calls))
            assert ledger.account('account')['committed_calls']==budget.calls==3
            assert ledger.account('account')['occupied_units']==budget.resource_units
            entry=dict(ledger.db.execute('SELECT * FROM ledger_tools').fetchone())
            assert entry['tool_call_id']=='call_1' and entry['run_id']==result.execution.id
            assert ledger.tool_result(entry['id'])==result.tool_calls[0].output
        finally:await runtime.close();ledger.close()
    asyncio.run(run())


def test_workflow_local_recovery_inherits_durable_account_not_new_money(tmp_path):
    async def run():
        ledger=journal(tmp_path)
        e=safety_engine(ScriptedMockProvider(response('{"answer":0}'),response('{"answer":42}')))
        e.runtime.durability=DurableExecution(ledger)
        try:
            await setup(e)
            flow=replace(definition(),resource_limits={'max_calls':6,'max_resource_units':100000,'max_feedback_loops':1})
            await e.register(flow)
            budget=ResourceBudget(max_calls=6,max_resource_units=100000,max_feedback_loops=1)
            e.runtime.durability.bind(budget,'account','task')
            source=await e.wait((await e.submit(flow.ref,'[MOCK] actual failed output',shared_budget=budget)).run.id)
            assert source.run.state.value=='failed' and source.budget_account_id=='account'
            recovered=await LocalRecoveryService(e).resume(source.run.id,'recovery',source.run.revision)
            final=await e.wait(recovered.run.id)
            assert final.verified and final.budget_account_id==source.budget_account_id
            assert final.resource_usage['model_calls']==ledger.account('account')['committed_calls']==2
            assert await e.get(source.run.id)==source
            entries=ledger.db.execute('SELECT run_id,node_id FROM ledger_reservations').fetchall()
            assert {v['run_id'] for v in entries}=={source.run.id,final.run.id}
            assert {v['node_id'] for v in entries}=={source.nodes[0].id,final.nodes[0].id}
        finally:await close(e);ledger.close()
    asyncio.run(run())


def test_model_timeout_keeps_unknown_hold_and_cancellation_never_refunds_effect(tmp_path):
    async def run():
        class SlowModel:
            is_simulated=True
            async def infer(self,request):await asyncio.sleep(10)
        ledger=journal(tmp_path);runtime=service(SlowModel());runtime.durability=DurableExecution(ledger)
        budget=ResourceBudget(max_calls=6,max_resource_units=100000);runtime.durability.bind(budget,'account','task')
        try:
            await runtime.registry.register(agent())
            result=await runtime.wait((await runtime.submit('analyst','[MOCK] timeout',RunLimits(model_timeout_seconds=.01),shared_budget=budget)).execution.id)
            assert result.execution.error_code=='model_timeout'
            assert ledger.account('account')['unknown_calls']==1
            assert (await runtime.capacity('analyst')).active==0
            with pytest.raises(RuntimeFault):ledger.release(result.model_calls[0].reservation_id)
        finally:await runtime.close();ledger.close()
    asyncio.run(run())


def test_cancel_during_tool_preserves_unknown_effect_and_releases_runtime_capacity(tmp_path):
    async def run():
        ledger=journal(tmp_path);entered=asyncio.Event()
        runtime=service(ScriptedMockProvider(response('',(tool_call('effect',{'value':1}),))))
        runtime.durability=DurableExecution(ledger)
        async def effect(value):entered.set();await asyncio.Event().wait()
        runtime.tools.register(ToolDefinition(VersionRef('effect','1'),'SIMULATED pending effect',{'type':'object'},'irreversible'),effect)
        budget=ResourceBudget(max_calls=6,max_resource_units=100000);runtime.durability.bind(budget,'account','task')
        try:
            await runtime.registry.register(agent(tool_refs=(VersionRef('effect','1'),)))
            report=await runtime.submit('analyst','[MOCK] cancel effect',shared_budget=budget)
            await asyncio.wait_for(entered.wait(),2)
            result=await runtime.cancel(report.execution.id)
            assert result.execution.state.value=='cancelled'
            assert ledger.db.execute('SELECT state FROM ledger_tools').fetchone()[0]=='UNKNOWN'
            assert ledger.account('account')['occupied_calls']==2
            assert (await runtime.capacity('analyst')).active==0
        finally:await runtime.close();ledger.close()
    asyncio.run(run())


def test_two_recovery_coordinators_never_invoke_same_unknown_tool_twice(tmp_path):
    original=journal(tmp_path);entry=start(original);dead=original.worker_id;original.close()
    async def run():
        a=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'));b=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))
        registry=InMemoryToolRegistry();count=[]
        async def effect(value):count.append(value);await asyncio.sleep(.02);return value
        registry.register(ToolDefinition(VersionRef('effect','1'),'SIMULATED idempotent set',{'type':'object'},'idempotent'),effect)
        try:
            a.recover(dead,confirmed_stopped=True)
            results=await asyncio.gather(*(DurableExecution(l).recover_tool(entry['id'],'same',registry,(VersionRef('effect','1'),),{'value':1},authorize_idempotent=True) for l in (a,b)),return_exceptions=True)
            assert count==[1]
            assert sum(isinstance(r,RuntimeFault) for r in results)==1
            assert a.account('account')['occupied_calls']==2
        finally:a.close();b.close()
    asyncio.run(run())


def test_succeeded_tool_cache_survives_new_process_session_and_bad_alias_rejected(tmp_path):
    first=journal(tmp_path);entry=start(first);first.succeed_tool(entry['id'],'reservation',{'value':1});first.close()
    new=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))
    try:
        ctx=replace(context(kind='tool'),reservation_id='new-call',execution_id='new-execution')
        new.reserve(ctx)
        cached=new.start_tool(ctx,tool_call_id='new-call-id',tool_name='effect',tool_version='1',side_effect_class='idempotent',replay_scope='node')
        assert cached['cached'] and not cached['claimed'] and new.tool_result(cached['id'])=={'value':1}
        assert new.account('account')['committed_calls']==2
        with pytest.raises(RuntimeFault):new.start_tool(ctx,tool_call_id='new-call-id',tool_name='different',tool_version='1',side_effect_class='idempotent',replay_scope='node')
    finally:new.close()


def test_known_side_effect_free_tool_failure_records_failed_with_committed_resource_usage(tmp_path):
    ledger=journal(tmp_path);entry=start(ledger,safety='none')
    try:
        ledger.fail_tool(entry['id'],'reservation')
        assert ledger.tool(entry['id'])['state']=='FAILED'
        assert ledger.account('account')['committed_calls']==1
    finally:ledger.close()


def test_uncertain_recovery_failure_is_bounded_even_after_another_restart(tmp_path):
    ledger=journal(tmp_path);entry=start(ledger);dead=ledger.worker_id;ledger.close()
    worker=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))
    worker.recover(dead,confirmed_stopped=True)
    assert worker.claim_unknown_tool(entry['id'],'first',allow_idempotent=True)
    owner=worker.worker_id;worker.close()
    third=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))
    try:
        third.recover(owner,confirmed_stopped=True)
        assert not third.claim_unknown_tool(entry['id'],'second',allow_idempotent=True)
        assert third.account('account')['unknown_calls']==2
        assert third.account('account')['occupied_calls']==2
    finally:third.close()


def test_persisted_corruption_is_diagnosed_without_rewriting_result_or_budget(tmp_path):
    ledger=journal(tmp_path,calls=1,units=10);entry=start(ledger)
    try:
        ledger.succeed_tool(entry['id'],'reservation',{'value':1})
        ledger.db.execute("UPDATE ledger_reservations SET actual=11 WHERE id='reservation'")
        before=ledger.db.total_changes
        codes={f.code for f in ledger.reconcile()}
        assert {'account_budget_exceeded','reservation_invariant_broken'}<=codes
        assert ledger.db.total_changes==before
        assert ledger.reservation('reservation')['actual']==11
    finally:ledger.close()


def test_integer_cny_estimate_ledger_has_no_invoice_or_float_rounding_claim(tmp_path):
    ledger=SQLiteRecoveryLedger(str(tmp_path/'price.sqlite3'))
    try:
        ledger.create_account('price','priced-task',max_calls=2,max_units=500000,unit='estimated_micro_cny')
        ctx=replace(context(),account_id='price',units=300000)
        ledger.reserve(ctx);ledger.dispatch(ctx.reservation_id)
        ledger.commit(ctx.reservation_id,300000,'configured-price-estimate')
        with pytest.raises(RuntimeFault):ledger.reserve(replace(ctx,reservation_id='next'))
        assert ledger.account('price')['occupied_units']==300000
        with pytest.raises(RuntimeFault):DurableExecution(ledger).restore(ResourceBudget(max_calls=2,max_resource_units=500000,calls=1,resource_units=300000),'price')
    finally:ledger.close()


def test_durable_runtime_refuses_non_simulated_provider_before_credential_validation(tmp_path):
    async def run():
        class NeverReal:
            is_simulated=False
            def validate_agent(self,agent):raise AssertionError('Must not access credential validation')
            async def infer(self,request):raise AssertionError('No real HTTP requests allowed')
        ledger=journal(tmp_path);runtime=service(NeverReal());runtime.durability=DurableExecution(ledger)
        budget=ResourceBudget(max_calls=6,max_resource_units=100000);runtime.durability.bind(budget,'account','task')
        try:
            await runtime.registry.register(agent())
            with pytest.raises(RuntimeFault):await runtime.submit('analyst','no real request',shared_budget=budget)
            assert ledger.account('account')['occupied_calls']==0
        finally:await runtime.close();ledger.close()
    asyncio.run(run())


def test_lost_commit_acknowledgement_retry_after_restart_is_read_only(tmp_path):
    old=journal(tmp_path);entry=start(old);old.succeed_tool(entry['id'],'reservation',{'value':1})
    old.reserve(context('not-sent'));old.release('not-sent');old.close()
    new=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'))
    try:
        account=new.account('account');before=new.db.total_changes
        new.commit('reservation',10,'tool-result:'+digest({'value':1}))
        new.succeed_tool(entry['id'],'reservation',{'value':1})
        new.release('not-sent')
        assert new.db.total_changes==before and new.account('account')==account
        with pytest.raises(RuntimeFault):new.succeed_tool(entry['id'],'reservation',{'value':2})
        with pytest.raises(RuntimeFault):new.commit('reservation',9,'different-evidence')
    finally:new.close()


def test_runtime_cannot_admit_from_corrupt_or_wrong_unit_account(tmp_path):
    ledger=journal(tmp_path);ledger.reserve(context());ledger.dispatch('reservation');ledger.commit('reservation',10,'local-evidence')
    try:
        ledger.db.execute("UPDATE ledger_reservations SET actual=-1 WHERE id='reservation'")
        with pytest.raises(RuntimeFault) as error:ledger.reserve(context('next'))
        assert error.value.code=='ledger_integrity_error'
        assert 'reservation_invariant_broken' in {f.code for f in ledger.reconcile()}
    finally:ledger.close()


@pytest.mark.parametrize('corrupt',[False,True])
def test_local_workflow_recovery_uses_reopened_durable_tool_proof_and_rejects_corruption(tmp_path,corrupt):
    async def run():
        ledger=journal(tmp_path,calls=10)
        provider=ScriptedMockProvider(response('',(tool_call('effect',{'value':1},'first'),)),response('{"answer":0}'),
            response('',(tool_call('effect',{'value':1},'second'),)),response('{"answer":42}'))
        e=safety_engine(provider);e.runtime.durability=DurableExecution(ledger);effects=[]
        async def effect(value):effects.append(value);return {'value':value}
        e.runtime.tools.register(ToolDefinition(VersionRef('effect','1'),'SIMULATED idempotent action',{'type':'object'},'idempotent'),effect)
        try:
            await setup(e,agents=[agent(tool_refs=(VersionRef('effect','1'),))])
            flow=replace(definition(),resource_limits={'max_calls':10,'max_resource_units':100000,'max_feedback_loops':1})
            await e.register(flow)
            budget=ResourceBudget(max_calls=10,max_resource_units=100000,max_feedback_loops=1)
            e.runtime.durability.bind(budget,'account','task')
            source=await e.wait((await e.submit(flow.ref,'[MOCK] repair without repeated effect',shared_budget=budget)).run.id)
            assert source.run.state.value=='failed' and effects==[1]
            assert not e.runtime.tools.has_replay_scope(source.nodes[0].id)
            ledger.close();ledger=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'));e.runtime.durability=DurableExecution(ledger)
            if corrupt:
                ledger.db.execute('UPDATE ledger_tools SET result_json=NULL')
                with pytest.raises(RuntimeFault) as error:await LocalRecoveryService(e).resume(source.run.id,'repair',source.run.revision)
                assert error.value.code=='recovery_effect_authorization'
                assert effects==[1] and ledger.account('account')['occupied_calls']==3
            else:
                final=await e.wait((await LocalRecoveryService(e).resume(source.run.id,'repair',source.run.revision)).run.id)
                assert final.verified and effects==[1]
                assert final.resource_usage['calls']==ledger.account('account')['occupied_calls']==6
                assert final.resource_usage['tool_calls']==2
            assert await e.get(source.run.id)==source
        finally:await close(e);ledger.close()
    asyncio.run(run())


def test_explicit_unknown_tool_recovery_timeout_is_bounded_and_budget_remains_held(tmp_path):
    old=journal(tmp_path);entry=start(old);dead=old.worker_id;old.close()
    async def run():
        ledger=SQLiteRecoveryLedger(str(tmp_path/'ledger.sqlite3'));registry=InMemoryToolRegistry()
        async def effect(value):await asyncio.Event().wait()
        registry.register(ToolDefinition(VersionRef('effect','1'),'SIMULATED cancellable tool',{'type':'object'},'idempotent'),effect)
        try:
            ledger.recover(dead,confirmed_stopped=True)
            with pytest.raises(RuntimeFault) as error:
                await DurableExecution(ledger).recover_tool(entry['id'],'bounded',registry,(VersionRef('effect','1'),),{'value':1},authorize_idempotent=True,timeout_seconds=.01)
            assert error.value.code=='ledger_tool_recovery_timeout'
            assert ledger.tool(entry['id'])['state']=='UNKNOWN'
            assert ledger.account('account')['unknown_calls']==2
            assert not ledger.claim_unknown_tool(entry['id'],'again',allow_idempotent=True)
        finally:ledger.close()
    asyncio.run(run())
