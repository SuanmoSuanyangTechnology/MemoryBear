"""RAG 服务接口 - 基于 API Key 认证"""

from typing import Any, Optional, Union
import uuid

from fastapi import APIRouter, Body, Depends, Request, status, Query
from sqlalchemy.orm import Session

from app.controllers import chunk_controller
from app.core.api_key_auth import require_api_key
from app.core.logging_config import get_business_logger
from app.core.rag.models.chunk import QAChunk
from app.core.response_utils import success
from app.db import get_db
from app.schemas import chunk_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse
from app.services import api_key_service


router = APIRouter(prefix="/chunks", tags=["V1 - RAG API"])
api_logger = get_business_logger()


@router.get("/{kb_id}/{document_id}/previewchunks", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_preview_chunks(
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
    pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
    keywords: Optional[str] = Query(None, description="The keywords used to match chunk content")
):
    """
    Paged query document block preview list
    - Support filtering by document_id
    - Support keyword search for segmented content
    - Return paging metadata + file list
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await chunk_controller.get_preview_chunks(kb_id=kb_id,
                                                     document_id=document_id,
                                                     page=page,
                                                     pagesize=pagesize,
                                                     keywords=keywords,
                                                     db=db,
                                                     current_user=current_user)


@router.get("/{kb_id}/{document_id}/chunks", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_chunks(
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
    pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
    keywords: Optional[str] = Query(None, description="The keywords used to match chunk content")
):
    """
    Paged query document chunk list
    - Support filtering by document_id
    - Support keyword search for segmented content
    - Return paging metadata + file list
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await chunk_controller.get_chunks(kb_id=kb_id,
                                             document_id=document_id,
                                             page=page,
                                             pagesize=pagesize,
                                             keywords=keywords,
                                             db=db,
                                             current_user=current_user)


@router.post("/{kb_id}/{document_id}/chunk", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def create_chunk(
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    content: Union[str, QAChunk] = Body(..., description="Content can be either a string or a QAChunk object"),
):
    """
    create chunk
    """
    body = await request.json()
    create_data = chunk_schema.ChunkCreate(**body)
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await chunk_controller.create_chunk(kb_id=kb_id,
                                               document_id=document_id,
                                               create_data=create_data,
                                               db=db,
                                               current_user=current_user)


@router.get("/{kb_id}/{document_id}/{doc_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_chunk(
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    doc_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Retrieve document chunk information based on doc_id
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await chunk_controller.get_chunk(kb_id=kb_id,
                                            document_id=document_id,
                                            doc_id=doc_id,
                                            db=db,
                                            current_user=current_user)


@router.put("/{kb_id}/{document_id}/{doc_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def update_chunk(
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    doc_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    content: Union[str, QAChunk] = Body(..., description="Content can be either a string or a QAChunk object"),
):
    """
    Update document chunk content
    """
    body = await request.json()
    update_data = chunk_schema.ChunkUpdate(**body)
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await chunk_controller.update_chunk(kb_id=kb_id,
                                               document_id=document_id,
                                               doc_id=doc_id,
                                               update_data=update_data,
                                               db=db,
                                               current_user=current_user)


@router.delete("/{kb_id}/{document_id}/{doc_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def delete_chunk(
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    doc_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    delete document chunk
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await chunk_controller.delete_chunk(kb_id=kb_id,
                                               document_id=document_id,
                                               doc_id=doc_id,
                                               db=db,
                                               current_user=current_user)


@router.get("/retrieve_type", response_model=ApiResponse)
def get_retrieve_types():
    return success(msg="Successfully obtained the retrieval type", data=list(chunk_schema.RetrieveType))


@router.post("/retrieval", response_model=Any, status_code=status.HTTP_200_OK)
@require_api_key(scopes=["rag"])
async def retrieve_chunks(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    query: str = Body(..., description="question"),
):
    """
    retrieve chunk
    """
    body = await request.json()
    retrieve_data = chunk_schema.ChunkRetrieve(**body)
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await chunk_controller.retrieve_chunks(retrieve_data=retrieve_data,
                                                  db=db,
                                                  current_user=current_user)

