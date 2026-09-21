from typing import Any, Optional
import uuid
from fastapi import APIRouter, Depends, status, Query, UploadFile, File, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_async_db
from app.dependencies import cur_workspace_access_guard_async, get_current_user_async
from app.models.user_model import User
from app.schemas import chunk_schema
from app.schemas.response_schema import ApiResponse
from app.services.file_storage_service import FileStorageService, get_file_storage_service
from app.integrations.knowledge.call_profile import CallProfile
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.integrations.knowledge.contracts import KnowledgeContextError
router = APIRouter(prefix='/chunks', tags=['chunks'], dependencies=[Depends(get_current_user_async)])

@router.get('/{kb_id}/{document_id}/previewchunks', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_preview_chunks(kb_id: uuid.UUID, document_id: uuid.UUID, page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), keywords: Optional[str]=Query(None, description='The keywords used to match chunk content'), db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Paged query document block preview list
    - Support filtering by document_id
    - Support keyword search for segmented content
    - Return paging metadata + file list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{kb_id}/{document_id}/chunks', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_chunks(kb_id: uuid.UUID, document_id: uuid.UUID, page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), keywords: Optional[str]=Query(None, description='The keywords used to match chunk content'), db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Paged query document chunk list
    - Support filtering by document_id
    - Support keyword search for segmented content
    - For parent-child mode: return nested structure (parent chunks with children)
    - For normal mode: return flat chunk list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/{document_id}/chunk', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def create_chunk(kb_id: uuid.UUID, document_id: uuid.UUID, create_data: chunk_schema.ChunkCreate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    create chunk
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/{document_id}/chunk/batch', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def create_chunks_batch(kb_id: uuid.UUID, document_id: uuid.UUID, batch_data: chunk_schema.ChunkBatchCreate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Batch create chunks (max 8)
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/import_qa', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API, profile=CallProfile.MULTIPART_UPLOAD)
async def import_qa_new_doc(kb_id: uuid.UUID, file: UploadFile=File(..., description='CSV 或 Excel 文件（第一行标题跳过，第一列问题，第二列答案）'), parent_id: Optional[uuid.UUID]=Query(None, description='parent folder id'), db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), storage_service: FileStorageService=Depends(get_file_storage_service), request: Request=None):
    """
    导入 QA 问答对并新建文档（CSV/Excel），异步处理
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/{document_id}/import_qa', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API, profile=CallProfile.MULTIPART_UPLOAD)
async def import_qa_chunks(kb_id: uuid.UUID, document_id: uuid.UUID, file: UploadFile=File(..., description='CSV 或 Excel 文件（第一行标题跳过，第一列问题，第二列答案）'), db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    导入 QA 问答对（CSV/Excel），异步处理
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{kb_id}/{document_id}/{doc_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_chunk(kb_id: uuid.UUID, document_id: uuid.UUID, doc_id: str, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Retrieve document chunk information based on doc_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{kb_id}/{document_id}/{doc_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def update_chunk(kb_id: uuid.UUID, document_id: uuid.UUID, doc_id: str, update_data: chunk_schema.ChunkUpdate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Update document chunk content
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{kb_id}/{document_id}/{doc_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def delete_chunk(kb_id: uuid.UUID, document_id: uuid.UUID, doc_id: str, force_refresh: bool=Query(False, description='Force Elasticsearch refresh after deletion'), db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    delete document chunk
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/retrieve_type', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
def get_retrieve_types(current_user: User=Depends(get_current_user_async), request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/retrieval-policy', response_model=chunk_schema.RetrievalPolicyResponse, status_code=status.HTTP_200_OK)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_retrieval_policy(policy_request: chunk_schema.RetrievalPolicyRequest, current_user: User=Depends(get_current_user_async), request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/retrieval', response_model=Any, status_code=status.HTTP_200_OK)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def retrieve_chunks(retrieve_data: chunk_schema.ChunkRetrieve, current_user: User=Depends(get_current_user_async), request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
