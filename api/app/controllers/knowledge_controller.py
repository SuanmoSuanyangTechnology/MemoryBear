from typing import Optional
import uuid
from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_async_db
from app.dependencies import cur_workspace_access_guard_async, get_current_user_async
from app.models.user_model import User
from app.schemas import knowledge_schema
from app.schemas import file_schema
from app.schemas.response_schema import ApiResponse
from app.services.file_storage_service import FileStorageService, get_file_storage_service
from app.core.quota_stub import check_knowledge_capacity_quota
from app.integrations.knowledge.call_profile import CallProfile
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.integrations.knowledge.contracts import KnowledgeContextError
router = APIRouter(prefix='/knowledges', tags=['knowledges'], dependencies=[Depends(get_current_user_async)])

@router.get('/knowledgetype', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
def get_knowledge_types(current_user: User=Depends(get_current_user_async), request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/permissiontype', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
def get_permission_types(current_user: User=Depends(get_current_user_async), request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/parsertype', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
def get_parser_types(current_user: User=Depends(get_current_user_async), request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/knowledge_graph_entity_types', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_knowledge_graph_entity_types(llm_id: uuid.UUID, scenario: str, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    get knowledge graph entity types based on llm_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/knowledges', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_knowledges(parent_id: Optional[uuid.UUID]=Query(None, description='parent folder id'), page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), orderby: Optional[str]=Query(None, description='Sort fields, such as: created_at,updated_at'), desc: Optional[bool]=Query(False, description='Is it descending order'), keywords: Optional[str]=Query(None, description='Search keywords (knowledge base name)'), kb_ids: Optional[str]=Query(None, description='Knowledge base ids, separated by commas'), db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Query the knowledge base list in pages
    - Support filtering by parent_id
    -  Support keyword search for knowledge base names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/knowledge', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@check_knowledge_capacity_quota
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def create_knowledge(create_data: knowledge_schema.KnowledgeCreate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    create knowledge
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{knowledge_id}/copy', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@check_knowledge_capacity_quota
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def copy_knowledge(knowledge_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None, name: Optional[str]=Body(default=None, embed=True)):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{knowledge_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_knowledge(knowledge_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Retrieve knowledge base information based on knowledge_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{knowledge_id}/chunk-policy', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_knowledge_chunk_policy(knowledge_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    查询知识库的分块策略锁定状态
    - 知识库为空（无文档）→ parent_child_mode: null，未锁定，可自由选择
    - 知识库有文档且使用普通分块 → parent_child_mode: false，锁定为普通模式
    - 知识库有文档且使用父子分块 → parent_child_mode: true，锁定为父子模式
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{knowledge_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def update_knowledge(knowledge_id: uuid.UUID, update_data: knowledge_schema.KnowledgeUpdate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{kb_id}/qa/export')
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API, profile=CallProfile.STREAM_DOWNLOAD)
async def export_knowledge_qa_csv(kb_id: uuid.UUID, current_user: User=Depends(get_current_user_async), request: Request=None):
    """Export all active QA pairs in a knowledge base as a two-column CSV."""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/batch-download')
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API, profile=CallProfile.STREAM_DOWNLOAD)
async def kb_batch_download(kb_id: uuid.UUID, current_user: User=Depends(get_current_user_async), storage_service: FileStorageService=Depends(get_file_storage_service), request_body: file_schema.KBBatchDownloadRequest=file_schema.KBBatchDownloadRequest(), request: Request=None):
    """知识库文件一键下载 — 将该知识库下所有文件打包为 ZIP 流式下载"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{knowledge_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def delete_knowledge(knowledge_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Soft-delete knowledge base
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{knowledge_id}/knowledge_graph', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_knowledge_graph(knowledge_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Retrieve knowledge_graph base information based on knowledge_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{knowledge_id}/knowledge_graph', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def delete_knowledge_graph(knowledge_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    delete knowledge graph
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{knowledge_id}/knowledge_graph', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def rebuild_knowledge_graph(knowledge_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    rebuild knowledge graph
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/check/yuque/auth', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def check_yuque_auth(yuque_user_id: str, yuque_token: str, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    check yuque auth info
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/check/feishu/auth', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def check_feishu_auth(feishu_app_id: str, feishu_app_secret: str, feishu_folder_token: str, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    check feishu auth info
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{knowledge_id}/sync', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def sync_knowledge(knowledge_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    sync knowledge base information based on knowledge_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
