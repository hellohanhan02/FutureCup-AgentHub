"""Explicit terminal-run recovery facade, using the existing WorkflowEngine only.

Mock-only until persisted monetary/per-node call reservation provenance is available.
No source snapshot is mutated. Control structures are conservatively recomputed.
"""
import asyncio
from datetime import datetime,timezone
from dataclasses import asdict
from futurecup.application.diagnosis import diagnose
from futurecup.application.workflow import WorkflowEngine, binding_key
from futurecup.application.workflow_validation import resolve
from futurecup.domain.models import NodeKind, TaskState, WorkflowState, VersionRef
from futurecup.domain.verification import ResourceBudget
from futurecup.domain.errors import RuntimeFault


class LocalRecoveryService:
    def __init__(self, engine: WorkflowEngine):
        self.engine=engine
        self._lock=asyncio.Lock()
        self._claims={}

    async def resume(self, run_id: str, request_id: str, expected_revision: int,
                     invalidated: tuple[str,...]=(), authorize_effects: bool=False):
        async with self._lock:
            key=(run_id,request_id)
            signature=(expected_revision,invalidated,authorize_effects)
            if key in self._claims:
                previous,identifier=self._claims[key]
                if previous!=signature:raise RuntimeFault('recovery_identity_conflict','Request key reused with different recovery parameters',409)
                return await self.engine.get(identifier)
            await self.engine.start()
            signature_digest=self.engine.selfloop.backend.digest(signature)
            claim=await self.engine.repository.recovery_claim(run_id)
            if claim:
                if claim['request_id']==request_id and claim['signature']==signature_digest:return await self.engine.get(claim['run_id'])
                raise RuntimeFault('recovery_source_claimed','Source already has a repair; budget cannot be reset with a new key',409)
            source=await self.engine.get(run_id)
            if source.run.revision!=expected_revision:raise RuntimeFault('recovery_stale_revision','Source revision changed',409)
            if source.run.state!=WorkflowState.FAILED:raise RuntimeFault('recovery_source_not_failed','Only a terminal failed run can be resumed',409)
            if datetime.now(timezone.utc)>=source.run.policy.deadline:raise RuntimeFault('recovery_deadline_exhausted','Original shared workflow deadline exhausted',409)
            if source.simulated is not True:raise RuntimeFault('recovery_real_budget_unprovable','Cross-run real reservation provenance unavailable; refuse paid resumption',409)
            if source.recovery_audit:raise RuntimeFault('recovery_generation_limit','Only one explicit cross-run repair generation is permitted',409)
            definition=await self.engine.repository.get_definition(source.run.definition_ref)
            definitions,bindings=await self.engine._prepare(definition)
            for ref in set(bindings.values()):
                agent=await self.engine.runtime.registry.get(ref)
                provider=self.engine.runtime.providers.get(agent.provider)
                if provider is None or provider.is_simulated is not True:
                    raise RuntimeFault('recovery_real_budget_unprovable','Every recovery candidate must be a known simulated Provider',409)
            specs={n.id:n for n in definition.nodes};old={n.node_id:n for n in source.nodes}
            if not set(invalidated)<=set(specs):raise RuntimeFault('recovery_node_unknown','Invalid recovery node reference',422)
            prior_failures={}
            diagnoses={}
            async def collect(report):
                for n in report.nodes:
                    if n.state==TaskState.FAILED and n.attempt_ids:
                        attempt=await self.engine.selfloop.repository.attempt(n.attempt_ids[-1])
                        results=await self.engine.selfloop.repository.results(n.id)
                        result=next((v for v in results if v['record']['id']==attempt['verification_id']),None)
                        if result:
                            diagnostic=diagnose(result)
                            if 'prompt_retry' not in diagnostic['allowed_actions']:
                                raise RuntimeFault('recovery_not_automatically_repairable','Failure requires environment change or manual intervention',409)
                            feedbacks=await self.engine.selfloop.repository.feedbacks(n.id)
                            context={'source_run':report.run.id,'previous_attempt_id':attempt['id'],
                                'previous_artifact':attempt['artifact'],'policy_ref':attempt['policy_ref'],
                                'verification':{'id':result['record']['id'],'verdict':result['record']['verdict'],
                                    'evidence':[e for e in result['evidence'] if e['status']=='FAILED']},
                                'feedback':{'id':feedbacks[-1]['record']['id'] if feedbacks else None,'action':'prompt_retry','category':diagnostic['category']},
                                'instruction':'Repair using the actual previous artifact and evidence; unchanged verification rules apply.'}
                            prior_failures[binding_key(report.run.definition_ref,n.node_id)+':'+self.engine.selfloop.backend.digest(n.input)]=context
                            diagnoses[report.run.id+':'+n.node_id]=diagnostic
                    for child in n.child_run_ids:await collect(await self.engine.get(child))
            await collect(source)
            bad=set(invalidated)
            retained=set()
            for spec in definition.nodes:
                n=old[spec.id]
                if n.state!=TaskState.SUCCEEDED or not n.verified or spec.kind!=NodeKind.TASK or not n.attempt_ids:
                    bad.add(spec.id);continue
                key_prefix=binding_key(definition.ref,spec.id)
                if {k:v for k,v in source.agent_bindings.items() if k==key_prefix or k.startswith(key_prefix+'#')}!={k:v for k,v in bindings.items() if k==key_prefix or k.startswith(key_prefix+'#')}:
                    bad.add(spec.id);continue
                attempt=await self.engine.selfloop.repository.attempt(n.attempt_ids[-1])
                results=await self.engine.selfloop.repository.results(n.id)
                policy=await self.engine.selfloop.repository.policy(VersionRef(**spec.config['verification']))
                digest=self.engine.selfloop.backend.digest
                proof=next((v for v in results if v['record']['id']==attempt['verification_id']),None)
                if (not proof or proof['record']['verdict']!='pass' or proof['artifact_digest']!=digest(n.output)
                    or proof['policy_digest']!=digest(asdict(policy)) or attempt['artifact']['digest']!=digest(n.output)):
                    bad.add(spec.id);continue
                try:
                    if resolve(spec.config.get('input',{'$ref':'input'}),self.engine._context(source))!=n.input:bad.add(spec.id);continue
                except RuntimeFault:bad.add(spec.id);continue
                records=tuple(attempt.get('retrieval_records',()))
                if records and (self.engine.runtime.knowledge is None or not self.engine.runtime.knowledge.snapshot_current(records)):
                    bad.add(spec.id);continue
                retained.add(spec.id)
            # Invalidate ALL transitive dependents including data mappings' DAG ancestors.
            while True:
                expanded=bad|{e.target for e in definition.edges if e.source in bad}
                if expanded==bad:break
                bad=expanded
            retained-=bad
            for spec in definition.nodes:
                if spec.id in retained:continue
                # Effects in control subtrees cannot inherit per-child replay keys safely.
                refs=[]
                if spec.kind==NodeKind.TASK:
                    prefix=binding_key(definition.ref,spec.id)
                    refs=[r for k,r in bindings.items() if k==prefix or k.startswith(prefix+'#')]
                    scope=old[spec.id].id
                else:
                    scope='no-child-replay-proof'
                    queue=[VersionRef(**spec.config['workflow'])] if spec.kind in {NodeKind.LOOP,NodeKind.SUBWORKFLOW} else []
                    visited=set()
                    while queue:
                        child=queue.pop()
                        if child in visited:continue
                        visited.add(child)
                        for child_spec in definitions[child].nodes:
                            prefix=binding_key(child,child_spec.id)
                            refs.extend(r for k,r in bindings.items() if k==prefix or k.startswith(prefix+'#'))
                            if child_spec.kind in {NodeKind.LOOP,NodeKind.SUBWORKFLOW}:queue.append(VersionRef(**child_spec.config['workflow']))
                for ref in refs:
                    a=await self.engine.runtime.registry.get(ref)
                    if a.tool_refs and not authorize_effects:
                        safe = (await self.engine.runtime.durability.resume_safe(self.engine.runtime.tools,a.tool_refs,scope,source.budget_account_id)
                            if self.engine.runtime.durability else getattr(self.engine.runtime.tools,'resume_safe',lambda refs,scope:False)(a.tool_refs,scope))
                        if not safe:
                            raise RuntimeFault('recovery_effect_authorization','Effect replay requires intact idempotency ledger or explicit operator confirmation',409)
            budget=ResourceBudget(**definition.resource_limits, simulation_only=True)
            for name,value in source.resource_usage.items():
                if name in budget.snapshot():setattr(budget,name,value)
            if self.engine.runtime.durability:
                if not source.budget_account_id:raise RuntimeFault('recovery_ledger_missing','Source has no durable account; refuse a new budget',409)
                self.engine.runtime.durability.restore(budget,source.budget_account_id)
            if budget.calls>=budget.max_calls:raise RuntimeFault('shared_call_budget','No shared call budget remains',409)
            if budget.attempts>=budget.max_attempts:raise RuntimeFault('shared_attempts_budget','No shared attempt budget remains',409)
            before=budget.snapshot()
            budget.consume('feedback_loops')
            audit={'source_run':run_id,'source_revision':source.run.revision,'request_id':request_id,'request_signature':signature_digest,
                'retained_nodes':sorted(retained),'invalidated_nodes':sorted(bad),'generation':1,
                'failure_diagnoses':diagnoses,'effects_explicitly_authorized':authorize_effects,'usage_before':before,
                'cost_comparison':{'basis':'unknown','decision':'reuse_only_verified_unchanged_nodes; refuse real cross-run recovery'}}
            report=await self.engine.submit(definition.ref,source.input,shared_budget=budget,recovery_seed=(source,retained,audit,prior_failures))
            self._claims[key]=(signature,report.run.id)
            return report
