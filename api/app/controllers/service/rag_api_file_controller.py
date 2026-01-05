"""RAG 服务接口 - 基于 API Key 认证"""

from typing import Any, Optional
import uuid

from fastapi import APIRouter, Body, Depends, Request, Query, File, UploadFile
from sqlalchemy.orm import Session

from app.controllers import file_controller
from app.core.api_key_auth import require_api_key
from app.core.logging_config import get_business_logger
from app.db import get_db
from app.schemas import file_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse
from app.services import api_key_service


router = APIRouter(prefix="/files", tags=["V1 - RAG API"])
api_logger = get_business_logger()


@router.get("/{kb_id}/{parent_id}/files", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_files(
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
    pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
    orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at"),
    desc: Optional[bool] = Query(False, description="Is it descending order"),
    keywords: Optional[str] = Query(None, description="Search keywords (file name)"),
):
    """
    Paged query file list
    - Support filtering by kb_id and parent_id
    - Support keyword search for file names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id=api_key_auth.workspace_id

    return await file_controller.get_files(kb_id=kb_id,
                                           parent_id=parent_id,
                                           page=page,
                                           pagesize=pagesize,
                                           orderby=orderby,
                                           desc=desc,
                                           keywords=keywords,
                                           db=db,
                                           current_user=current_user)


@router.post("/folder", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def create_folder(
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    folder_name: str = '/'
):
    """
    Create a new folder
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await file_controller.create_folder(kb_id=kb_id,
                                               parent_id=parent_id,
                                               folder_name=folder_name,
                                               db=db,
                                               current_user=current_user)


@router.post("/file", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def upload_file(
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    """
    upload file
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await file_controller.upload_file(kb_id=kb_id,
                                             parent_id=parent_id,
                                             file=file,
                                             db=db,
                                             current_user=current_user)


@router.post("/customtext", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def custom_text(
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    title: str = Body(..., description="title"),
    content: str = Body(..., description="content"),
):
    """
    custom text
    """
    body = await request.json()
    create_data = file_schema.CustomTextFileCreate(**body)
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await file_controller.custom_text(kb_id=kb_id,
                                             parent_id=parent_id,
                                             create_data=create_data,
                                             db=db,
                                             current_user=current_user)


@router.get("/{file_id}", response_model=Any)
async def get_file(
    file_id: uuid.UUID,
    db: Session = Depends(get_db)
) -> Any:
    """
    Download the file based on the file_id
    - Query file information from the database
    - Construct the file path and check if it exists
    - Return a FileResponse to download the file
    """
    return await file_controller.get_file(file_id=file_id,
                                          db=db)


@router.put("/{file_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def update_file(
    file_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    file_name: str = Body(None, description="file name (optional)"),
):
    """
    Update file information (such as file name)
    - Only specified fields such as file_name are allowed to be modified
    """
    body = await request.json()
    update_data = file_schema.FileUpdate(**body)
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await file_controller.update_file(file_id=file_id,
                                             update_data=update_data,
                                             db=db,
                                             current_user=current_user)


@router.delete("/{file_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def delete_file(
    file_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Delete a file or folder
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await file_controller.delete_file(file_id=file_id,
                                             db=db,
                                             current_user=current_user)

