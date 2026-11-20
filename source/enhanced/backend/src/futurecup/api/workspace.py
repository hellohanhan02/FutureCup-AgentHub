"""Local workspace API: explicit budgets and revision-bound confirmation."""
from typing import Any, Literal
from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from futurecup.api.agents import StrictModel, Identifier
from futurecup.application.planner import TaskPlanner
from futurecup.domain.workspace_budget import MAX_TASK_BUDGET_CNY


class TaskInput(StrictModel):
    request: str=Field(min_length=1,max_length=1500)
    scenario: Literal['architecture','document','schedule']='architecture'
    data: dict[str,Any]=Field(default_factory=dict)
    mode: Literal['mock','real']='mock'
    planning: Literal['dynamic','template']='dynamic'
    provider: Identifier='deepseek'
    model: str=Field(default='deepseek-flash',min_length=1,max_length=128)
    max_calls: int=Field(default=6,ge=1,le=6,strict=True)
    budget_cny: float=Field(default=.5,gt=0,le=MAX_TASK_BUDGET_CNY,strict=True,allow_inf_nan=False)
    authorize_real: bool=Field(default=False,strict=True)
    output_profile: Literal['concise','design_document']='concise'
    knowledge_base_ids: list[Identifier]=Field(default_factory=list,max_length=5)
    knowledge_contract: Literal['atomic-body-v1','staged-rules-v1','staged-rules-v2']='atomic-body-v1'
    retrieval_topics: list[dict] | None=Field(default=None,max_length=8)


class Confirm(StrictModel):
    revision: int=Field(ge=1,strict=True)
    authorize_real: bool=Field(default=False,strict=True)


class BudgetPreview(StrictModel):
    provider: Identifier='deepseek'
    model: str=Field(default='deepseek-flash',min_length=1,max_length=128)
    output_profile: Literal['concise','design_document']='concise'
    budget_cny: float=Field(default=.5,gt=0,le=MAX_TASK_BUDGET_CNY,strict=True,allow_inf_nan=False)
    max_calls: int=Field(default=6,ge=1,le=6,strict=True)


class Requirement(Confirm):
    request: str=Field(min_length=1,max_length=1500)


class PlanEdit(Confirm):
    plan: dict[str,Any]


def build_workspace_router(planner: TaskPlanner):
    router=APIRouter(prefix='/api/v1/workspace',tags=['user-workspace'])
    @router.get('/capabilities')
    async def capabilities():
        return {'mode':'interactive','default_mode':'mock','real_available':planner.runtime.real_calls_enabled and planner.runtime.real_safety is not None,
            'limits':{'nodes':3,'calls':6,'repairs':1,'concurrency':1,'task_seconds':600,'estimated_cny_max':MAX_TASK_BUDGET_CNY,
                'server_budget_cny':planner.runtime.real_safety.estimated_workflow_cny if planner.runtime.real_safety else .5,'default_budget_cny':.5},
            'scenarios':['architecture','document','schedule'],
            'models':[{'provider':p.id,'models':[m.id for m in p.models]} for p in planner.runtime.provider_profiles]}
    @router.post('/budget-preview')
    async def preview(data: BudgetPreview):return planner.preview(**data.model_dump())
    @router.post('/tasks',status_code=202)
    async def create(data: TaskInput):return jsonable_encoder(await planner.create(**data.model_dump()))
    @router.get('/tasks')
    async def history():
        return [{**{k:t.get(k) for k in ('id','created_at','scenario','state','simulated','revision','run_id','usage','error_code')},'name':(t.get('plan') or {}).get('name',t['messages'][0][:40])} for t in await planner.repository.list()]
    @router.get('/tasks/{task_id}')
    async def get(task_id: Identifier):return jsonable_encoder(await planner.get(task_id))
    @router.post('/tasks/{task_id}/requirements',status_code=202)
    async def revise(task_id: Identifier,data: Requirement):return jsonable_encoder(await planner.revise(task_id,**data.model_dump()))
    @router.put('/tasks/{task_id}/plan')
    async def edit(task_id: Identifier,data: PlanEdit):return jsonable_encoder(await planner.revise(task_id,**data.model_dump()))
    @router.post('/tasks/{task_id}/confirm',status_code=202)
    async def confirm(task_id: Identifier,data: Confirm):return jsonable_encoder(await planner.confirm(task_id,**data.model_dump()))
    @router.post('/tasks/{task_id}/cancel')
    async def cancel(task_id: Identifier):return jsonable_encoder(await planner.cancel(task_id))
    return router
