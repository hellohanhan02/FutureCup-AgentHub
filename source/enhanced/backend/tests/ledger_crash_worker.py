"""Deliberate hard-exit worker used only by isolated offline durability tests."""
import asyncio
import os
from pathlib import Path
import sys

from futurecup.adapters.recovery_ledger import SQLiteRecoveryLedger, digest
from futurecup.domain.ledger import CallContext

folder = Path(sys.argv[1]).resolve()
mode = sys.argv[2]
ledger = SQLiteRecoveryLedger(str(folder/'ledger.sqlite3'),worker_id='crashed-worker')
ledger.create_account('account','task',max_calls=6,max_units=100000)
context = CallContext('account','reservation','run','node','execution','tool' if mode.startswith('tool') else 'model',digest({'value':1}),10)

if mode != 'workflow':
    ledger.reserve(context)
    if mode == 'dispatch':
        ledger.dispatch(context.reservation_id)
    elif mode.startswith('tool'):
        entry = ledger.start_tool(context,tool_call_id='call',tool_name='effect',tool_version='1',
            side_effect_class=mode.removeprefix('tool-'),replay_scope='node')
        (folder/'external-effect.txt').write_text('External substitute completed',encoding='utf8')
    os._exit(77)  # No Python cleanup/finally blocks run.

from verification_support import safety_engine
from runtime_support import agent, response, tool_call
from workflow_support import task, workflow
from futurecup.application.durability import DurableExecution
from futurecup.domain.verification import ResourceBudget
from futurecup.adapters.tools import ToolDefinition
from futurecup.domain.models import VersionRef, ExecutionState

async def run():
    class Model:
        is_simulated = True
        async def infer(self, request):
            await asyncio.sleep(.02)
            return response('',(tool_call('effect',{'value':1}),))
    engine = safety_engine(Model(),path=folder/'workflow.sqlite3')
    engine.runtime.durability = DurableExecution(ledger)
    async def effect(value):
        (folder/'external-effect.txt').write_text('Completed exactly once before report save',encoding='utf8')
        return {'value':value}
    engine.runtime.tools.register(ToolDefinition(VersionRef('effect','1'),'Synthetic effect',
        {'type':'object','properties':{'value':{'type':'integer'}},'required':['value']},retry_safety='idempotent'),effect)
    await engine.runtime.registry.register(agent(tool_refs=(VersionRef('effect','1'),)))
    original_save = engine.runtime.executions.save
    async def crash_after_ledger_commit(report):
        if report.tool_calls and report.tool_calls[-1].state == ExecutionState.SUCCEEDED:
            os._exit(77)
        await original_save(report)
    engine.runtime.executions.save = crash_after_ledger_commit
    flow = workflow([task('work')],identifier='crash-flow',resource_limits={'max_calls':6,'max_resource_units':100000})
    await engine.register(flow)
    budget = ResourceBudget(max_calls=6,max_resource_units=100000)
    engine.runtime.durability.bind(budget,'account','task')
    await engine.wait((await engine.submit(flow.ref,'Synthetic task',shared_budget=budget)).run.id)
    raise AssertionError('Crash injection was not reached')

asyncio.run(run())
