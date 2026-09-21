from typing import Any, Optional, Union
import uuid
from fastapi import APIRouter, Body, Depends, File, Request, status, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.api_key_auth import require_api_key_self_db
from app.schemas.knowledge_types import QAChunk
from app.db import get_async_db
from app.integrations.knowledge.call_profile import CallProfile
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.schemas import chunk_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse
from app.services.file_storage_service import FileStorageService, get_file_storage_service
from app.integrations.knowledge.contracts import KnowledgeContextError
router = APIRouter(prefix='/chunks', tags=['V1 - RAG API'])

@router.get('/{kb_id}/{document_id}/previewchunks', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_preview_chunks(kb_id: uuid.UUID, document_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), keywords: Optional[str]=Query(None, description='The keywords used to match chunk content')):
    """
    Paged query document block preview list
    - Support filtering by document_id
    - Support keyword search for segmented content
    - Return paging metadata + file list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{kb_id}/{document_id}/chunks', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_chunks(kb_id: uuid.UUID, document_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), keywords: Optional[str]=Query(None, description='The keywords used to match chunk content')):
    """
    Paged query document chunk list
    - Support filtering by document_id
    - Support keyword search for segmented content
    - Return paging metadata + file list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/{document_id}/chunk', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def create_chunk(kb_id: uuid.UUID, document_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), content: Union[str, QAChunk]=Body(..., description='Content can be either a string or a QAChunk object')):
    """
    create chunk
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/{document_id}/chunk/batch', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def create_chunks_batch(kb_id: uuid.UUID, document_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), items: list=Body(..., description='chunk items list')):
    """
    Batch create chunks (max 8)
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{kb_id}/{document_id}/{doc_id}', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_chunk(kb_id: uuid.UUID, document_id: uuid.UUID, doc_id: str, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    Retrieve document chunk information based on doc_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{kb_id}/{document_id}/{doc_id}', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def update_chunk(kb_id: uuid.UUID, document_id: uuid.UUID, doc_id: str, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), content: Union[str, QAChunk]=Body(..., description='Content can be either a string or a QAChunk object')):
    """
    Update document chunk content
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{kb_id}/{document_id}/{doc_id}', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def delete_chunk(kb_id: uuid.UUID, document_id: uuid.UUID, doc_id: str, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), force_refresh: bool=Query(False, description='Force Elasticsearch refresh after deletion')):
    """
    delete document chunk
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/retrieve_type', response_model=ApiResponse)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API, public=True)
def get_retrieve_types(request: Request=None):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/retrieval-policy', response_model=chunk_schema.RetrievalPolicyResponse, status_code=status.HTTP_200_OK)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_retrieval_policy(request: Request, policy_request: chunk_schema.RetrievalPolicyRequest):
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/retrieval', response_model=Any, status_code=status.HTTP_200_OK)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def retrieve_chunks(request: Request, retrieve_data: chunk_schema.ChunkRetrieve):
    """
    retrieve chunk
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{kb_id}/import_qa', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API, profile=CallProfile.MULTIPART_UPLOAD)
async def import_qa_new_doc(kb_id: uuid.UUID, request: Request, file: UploadFile=File(..., description='CSV 或 Excel 文件（第一行标题跳过，第一列问题，第二列答案）'), api_key_auth: ApiKeyAuth=None, parent_id: Optional[uuid.UUID]=Query(None, description='parent folder id'), db: AsyncSession=Depends(get_async_db), storage_service: FileStorageService=Depends(get_file_storage_service)):
    """
    导入 QA 问答对并新建文档（CSV/Excel），异步处理（API Key 认证）
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
