"""Explicit opt-in recovery of terminal Mock workflows; no paid requests."""
from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from futurecup.api.agents import StrictModel,Identifier
from futurecup.domain.errors import RuntimeFault

class RecoveryInput(StrictModel):
    request_id:Identifier
    expected_revision:int=Field(ge=0,strict=True)
    authorize_recovery:bool=Field(default=False,strict=True)
    invalidated_nodes:list[Identifier]=Field(default_factory=list,max_length=64)
    authorize_effects:bool=Field(default=False,strict=True)

def build_recovery_router(service):
    router=APIRouter(prefix='/api/v1',tags=['local-recovery'])
    @router.post('/workflow-runs/{run_id}/recover',status_code=202)
    async def recover(run_id:Identifier,data:RecoveryInput):
        if not data.authorize_recovery:raise RuntimeFault('recovery_authorization_required','Explicit recovery authorization required',409)
        report=await service.resume(run_id,data.request_id,data.expected_revision,tuple(data.invalidated_nodes),data.authorize_effects)
        return jsonable_encoder({'run':report.run,'recovery_audit':report.recovery_audit})
    return router
