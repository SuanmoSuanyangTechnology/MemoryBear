from typing import Optional
import uuid
from fastapi import APIRouter, Depends, Query, Body, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_async_db
from app.dependencies import cur_workspace_access_guard_async, get_current_user_async
from app.models.user_model import User
from app.schemas import document_schema
from app.schemas.response_schema import ApiResponse
from app.services.file_storage_service import FileStorageService, get_file_storage_service
from app.schemas import knowledge_metadata_schema as metadata_schema
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.integrations.knowledge.contracts import KnowledgeContextError
router = APIRouter(prefix='/documents', tags=['documents'], dependencies=[Depends(get_current_user_async)])

@router.get('/{kb_id}/documents', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_documents(kb_id: uuid.UUID, parent_id: Optional[uuid.UUID]=Query(None, description='parent folder id when type is Folder'), page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), orderby: Optional[str]=Query(None, description='Sort fields, such as: created_at,updated_at'), desc: Optional[bool]=Query(False, description='Is it descending order'), keywords: Optional[str]=Query(None, description='Search keywords (file name)'), document_ids: Optional[str]=Query(None, description='document ids, separated by commas'), db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Paged query document list
    - Support filtering by kb_id and parent_id
    - Support keyword search for file names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/document', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def create_document(create_data: document_schema.DocumentCreate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    create document
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{document_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_document(document_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Retrieve document information based on document_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{document_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def update_document(document_id: uuid.UUID, update_data: document_schema.DocumentUpdate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Update document information
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{document_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def delete_document(document_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), storage_service: FileStorageService=Depends(get_file_storage_service), request: Request=None):
    """
    Delete document
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{document_id}/chunks', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def parse_documents(document_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    parse document
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/metadata/batch', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def batch_update_document_metadata(data: metadata_schema.BatchUpdateMetadataRequest, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    批量更新文档元数据
    - 所有文档必须属于同一知识库且当前用户有权限访问
    - 事务性：全成功或全回滚
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{document_id}/metadata', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def update_document_metadata(document_id: uuid.UUID, data: metadata_schema.DocumentMetadataUpdateRequest, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    更新单个文档的元数据
    - 字段必须在知识库中已定义
    - 值类型必须与字段定义一致
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{document_id}/metadata', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_document_metadata(document_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """获取单个文档的元数据"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{document_id}/metadata', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def delete_document_metadata(document_id: uuid.UUID, data: metadata_schema.DocumentMetadataDeleteRequest | None=Body(None), db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    删除单个文档的元数据
    - 不传 body 或 field_names 为空时，清空全部元数据
    - 传 field_names 时，仅删除指定字段
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
