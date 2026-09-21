import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_async_db
from app.dependencies import cur_workspace_access_guard_async, get_current_user_async
from app.models.user_model import User
from app.schemas import knowledge_metadata_schema as schemas
from app.schemas.response_schema import ApiResponse
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.integrations.knowledge.contracts import KnowledgeContextError
router = APIRouter(prefix='/knowledges', tags=['knowledge-metadata'], dependencies=[Depends(get_current_user_async)])

@router.post('/metadata/fields', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def list_common_metadata_fields(data: schemas.KnowledgeMetadataFieldsRequest, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """List common metadata fields across knowledge bases."""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{kb_id}/metadata', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def list_metadata_fields(kb_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """获取元数据字段列表（自定义 + 内置）"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/metadata', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def create_metadata_field(kb_id: uuid.UUID, data: schemas.KnowledgeMetadataCreate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """创建自定义元数据字段"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{kb_id}/metadata/{metadata_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def update_metadata_field(kb_id: uuid.UUID, metadata_id: uuid.UUID, data: schemas.KnowledgeMetadataUpdate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """更新自定义元数据字段"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{kb_id}/metadata/{metadata_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def delete_metadata_field(kb_id: uuid.UUID, metadata_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """删除自定义元数据字段"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{kb_id}/metadata/builtin', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_builtin_metadata_fields(kb_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """获取内置元数据字段列表"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/metadata/builtin/enable', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def toggle_builtin_metadata(kb_id: uuid.UUID, data: schemas.BuiltinMetadataEnableRequest, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """更新内置元数据开关"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
