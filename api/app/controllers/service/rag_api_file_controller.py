from typing import Any, Optional
import uuid
from fastapi import APIRouter, Body, Depends, Request, Query, File, UploadFile
from sqlalchemy.orm import Session
from app.core.api_key_auth import require_api_key
from app.db import get_db
from app.integrations.knowledge.call_profile import CallProfile
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse
from app.services.file_storage_service import FileStorageService, get_file_storage_service
from app.integrations.knowledge.contracts import KnowledgeContextError
router = APIRouter(prefix='/files', tags=['V1 - RAG API'])

@router.get('/{kb_id}/{parent_id}/files', response_model=ApiResponse)
@require_api_key(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def get_files(kb_id: uuid.UUID, parent_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: Session=Depends(get_db), page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), orderby: Optional[str]=Query(None, description='Sort fields, such as: created_at'), desc: Optional[bool]=Query(False, description='Is it descending order'), keywords: Optional[str]=Query(None, description='Search keywords (file name)')):
    """
    Paged query file list
    - Support filtering by kb_id and parent_id
    - Support keyword search for file names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/folder', response_model=ApiResponse)
@require_api_key(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def create_folder(kb_id: uuid.UUID, parent_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: Session=Depends(get_db), folder_name: str='/'):
    """
    Create a new folder
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/file', response_model=ApiResponse)
@require_api_key(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API, profile=CallProfile.MULTIPART_UPLOAD)
async def upload_file(kb_id: uuid.UUID, parent_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: Session=Depends(get_db), storage_service: FileStorageService=Depends(get_file_storage_service), file: UploadFile=File(...)):
    """
    upload file
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/customtext', response_model=ApiResponse)
@require_api_key(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def custom_text(kb_id: uuid.UUID, parent_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: Session=Depends(get_db), storage_service: FileStorageService=Depends(get_file_storage_service), title: str=Body(..., description='title'), content: str=Body(..., description='content')):
    """
    custom text
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{file_id}', response_model=Any)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API, profile=CallProfile.STREAM_DOWNLOAD, public=True)
async def get_file(file_id: uuid.UUID, db: Session=Depends(get_db), storage_service: FileStorageService=Depends(get_file_storage_service), request: Request=None) -> Any:
    """
    Download the file based on the file_id
    - Query file information from the database
    - Construct the file path and check if it exists
    - Return a FileResponse to download the file
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{file_id}', response_model=ApiResponse)
@require_api_key(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def update_file(file_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: Session=Depends(get_db), file_name: str=Body(None, description='file name (optional)')):
    """
    Update file information (such as file name)
    - Only specified fields such as file_name are allowed to be modified
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{file_id}', response_model=ApiResponse)
@require_api_key(scopes=['rag'])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def delete_file(file_id: uuid.UUID, request: Request, api_key_auth: ApiKeyAuth=None, db: Session=Depends(get_db), storage_service: FileStorageService=Depends(get_file_storage_service)):
    """
    Delete a file or folder
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
