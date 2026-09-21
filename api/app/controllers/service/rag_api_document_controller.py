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
router = APIRouter(prefix='/documents', tags=['V1 - RAG API'])

@router.get('/{kb_id}/documents', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_documents(kb_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), parent_id: Optional[uuid.UUID]=Query(None, description='parent folder id when type is Folder'), page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), orderby: Optional[str]=Query(None, description='Sort fields, such as: created_at,updated_at'), desc: Optional[bool]=Query(False, description='Is it descending order'), keywords: Optional[str]=Query(None, description='Search keywords (file name)'), document_ids: Optional[str]=Query(None, description='document ids, separated by commas')):
    """
    Paged query document list
    - Support filtering by kb_id and parent_id
    - Support keyword search for file names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/document', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def create_document(request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), kb_id: uuid.UUID=Body(..., description='kb id'), file_name: str=Body(..., description='file name')):
    """
    create document
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{document_id}', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_document(document_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    Retrieve document information based on document_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{document_id}', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def update_document(document_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db), file_name: str=Body(None, description='file name (optional)')):
    """
    Update document information
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{document_id}', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def delete_document(document_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    Delete document
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/{document_id}/chunks', response_model=ApiResponse)
@require_api_key_self_db(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def parse_documents(document_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: AsyncSession=Depends(get_async_db)):
    """
    parse document
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
