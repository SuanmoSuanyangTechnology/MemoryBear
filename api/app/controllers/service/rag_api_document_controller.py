"""RAG 服务接口 - 基于 API Key 认证"""

from typing import Optional
import uuid

from fastapi import APIRouter, Body, Depends, Request, Query
from sqlalchemy.orm import Session

from app.controllers import document_controller
from app.core.api_key_auth import require_api_key
from app.core.logging_config import get_business_logger
from app.db import get_db
from app.schemas import document_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse
from app.services import api_key_service


router = APIRouter(prefix="/documents", tags=["V1 - RAG API"])
api_logger = get_business_logger()


@router.get("/{kb_id}/documents", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_documents(
    kb_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    parent_id: Optional[uuid.UUID] = Query(None, description="parent folder id when type is Folder"),
    page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
    pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
    orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at,updated_at"),
    desc: Optional[bool] = Query(False, description="Is it descending order"),
    keywords: Optional[str] = Query(None, description="Search keywords (file name)"),
    document_ids: Optional[str] = Query(None, description="document ids, separated by commas")
):
    """
    Paged query document list
    - Support filtering by kb_id and parent_id
    - Support keyword search for file names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await document_controller.get_documents(kb_id=kb_id,
                                                   parent_id=parent_id,
                                                   page=page,
                                                   pagesize=pagesize,
                                                   orderby=orderby,
                                                   desc=desc,
                                                   keywords=keywords,
                                                   document_ids=document_ids,
                                                   db=db,
                                                   current_user=current_user)


@router.post("/document", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def create_document(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    kb_id: uuid.UUID = Body(..., description="kb id"),
    file_name: str = Body(..., description="file name"),
):
    """
    create document
    """
    body = await request.json()
    create_data = document_schema.DocumentCreate(**body)
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await document_controller.create_document(create_data=create_data,
                                                     db=db,
                                                     current_user=current_user)


@router.get("/{document_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_document(
    document_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Retrieve document information based on document_id
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await document_controller.get_document(document_id=document_id,
                                                  db=db,
                                                  current_user=current_user)


@router.put("/{document_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def update_document(
    document_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    file_name: str = Body(None, description="file name (optional)"),
):
    """
    Update document information
    """
    body = await request.json()
    update_data = document_schema.DocumentUpdate(**body)
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await document_controller.update_document(document_id=document_id,
                                                     update_data=update_data,
                                                     db=db,
                                                     current_user=current_user)


@router.delete("/{document_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def delete_document(
    document_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Delete document
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await document_controller.delete_document(document_id=document_id,
                                                     db=db,
                                                     current_user=current_user)


@router.post("/{document_id}/chunks", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def parse_documents(
    document_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    parse document
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await document_controller.parse_documents(document_id=document_id,
                                                     db=db,
                                                     current_user=current_user)

