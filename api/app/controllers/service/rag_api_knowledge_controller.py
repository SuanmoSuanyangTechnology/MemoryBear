from typing import Optional
import uuid
from fastapi import APIRouter, Body, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.api_key_auth import require_api_key_self_db
from app.db import get_async_db
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse
from app.integrations.knowledge.contracts import KnowledgeContextError
router = APIRouter(prefix='/knowledges', tags=['V1 - RAG API'])

@router.get('/knowledgetype', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API, public=True)
def get_knowledge_types(request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/permissiontype', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API, public=True)
def get_permission_types(request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/parsertype', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API, public=True)
def get_parser_types(request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/knowledge_graph_entity_types', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_knowledge_graph_entity_types(llm_id: uuid.UUID, scenario: str, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    get knowledge graph entity types based on llm_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/knowledges', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_knowledges(request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), parent_id: Optional[uuid.UUID]=Query(None, description='parent folder id'), page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), orderby: Optional[str]=Query(None, description='Sort fields, such as: created_at,updated_at'), desc: Optional[bool]=Query(False, description='Is it descending order'), keywords: Optional[str]=Query(None, description='Search keywords (knowledge base name)'), kb_ids: Optional[str]=Query(None, description='Knowledge base ids, separated by commas')):
    """
    Query the knowledge base list in pages
    - Support filtering by parent_id
    -  Support keyword search for knowledge base names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/knowledge', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def create_knowledge(request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), name: str=Body(..., description='KB name'), audio2text_id: uuid.UUID | None=Body(None, description='Audio transcription model config ID (omitted or null inherits a compatible workspace audio model; remains null when none is available)'), video2text_id: uuid.UUID | None=Body(None, description='Video understanding model config ID (omitted or null inherits a compatible workspace video model; remains null when none is available)')):
    """
    create knowledge
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{knowledge_id}', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_knowledge(knowledge_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    Retrieve knowledge base information based on knowledge_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{knowledge_id}', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def update_knowledge(knowledge_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), name: str=Body(None, description='KB name (optional)'), audio2text_id: uuid.UUID | None=Body(None, description='Audio transcription model config ID (null clears)'), video2text_id: uuid.UUID | None=Body(None, description='Video understanding model config ID (null clears)')):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{knowledge_id}', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def delete_knowledge(knowledge_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    Soft-delete knowledge base
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{knowledge_id}/knowledge_graph', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_knowledge_graph(knowledge_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    Retrieve knowledge_graph base information based on knowledge_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{knowledge_id}/knowledge_graph', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def delete_knowledge_graph(knowledge_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    delete knowledge graph
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{knowledge_id}/knowledge_graph', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def rebuild_knowledge_graph(knowledge_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    rebuild knowledge graph
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/check/yuque/auth', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def check_yuque_auth(yuque_user_id: str, yuque_token: str, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    check yuque auth info
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/check/feishu/auth', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def check_feishu_auth(feishu_app_id: str, feishu_app_secret: str, feishu_folder_token: str, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    check feishu auth info
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{knowledge_id}/sync', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def sync_knowledge(knowledge_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    sync knowledge base information based on knowledge_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
