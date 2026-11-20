import base64
from fastapi import APIRouter
from pydantic import Field
from futurecup.api.agents import StrictModel
from futurecup.domain.errors import RuntimeFault

class KnowledgeInput(StrictModel):
    name: str=Field(min_length=1,max_length=128)
    description: str=Field(default='',max_length=1000)
class DocumentInput(StrictModel):
    name: str=Field(min_length=1,max_length=128)
    content_base64: str=Field(max_length=2800000)
class SearchInput(StrictModel):
    query: str=Field(min_length=1,max_length=8000)
    top_k: int=Field(default=4,ge=1,le=8,strict=True)

def build_knowledge_router(store):
    router=APIRouter(prefix='/api/v1/knowledge-bases',tags=['knowledge'])
    @router.get('')
    async def listing():return store.list()
    @router.post('',status_code=201)
    async def create(data:KnowledgeInput):return store.create(**data.model_dump())
    @router.put('/{id}')
    async def edit(id:str,data:KnowledgeInput):return store.edit(id,**data.model_dump())
    @router.delete('/{id}')
    async def delete(id:str):store.delete(id);return {'deleted':True}
    @router.get('/{id}/documents')
    async def docs(id:str):return store.documents(id)
    @router.post('/{id}/documents',status_code=201)
    async def upload(id:str,data:DocumentInput):
        try:raw=base64.b64decode(data.content_base64,validate=True)
        except ValueError:raise RuntimeFault('knowledge_file','Invalid base64',422) from None
        return store.import_document(id,data.name,raw)
    @router.delete('/{id}/documents/{doc}')
    async def remove(id:str,doc:str):store.delete_document(id,doc);return {'deleted':True}
    @router.get('/{id}/documents/{doc}/chunks')
    async def chunks(id:str,doc:str):return store.chunks(id,doc)
    @router.post('/{id}/search')
    async def search(id:str,data:SearchInput):return store.search([id],data.query,data.top_k)
    return router
