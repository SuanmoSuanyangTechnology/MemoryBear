from typing import Any, Optional
import uuid
from fastapi import APIRouter, Depends, File, UploadFile, Query, Request
from sqlalchemy.orm import Session
from app.db import get_db
from app.dependencies import cur_workspace_access_guard, get_current_user
from app.models.user_model import User
from app.schemas import file_schema
from app.schemas.response_schema import ApiResponse
from app.services.file_storage_service import FileStorageService, get_file_storage_service
from app.core.quota_stub import check_knowledge_capacity_quota
from app.integrations.knowledge.call_profile import CallProfile
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.integrations.knowledge.contracts import KnowledgeContextError
router = APIRouter(prefix='/files', tags=['files'])

@router.get('/{kb_id}/{parent_id}/files', response_model=ApiResponse)
@cur_workspace_access_guard()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_files(kb_id: uuid.UUID, parent_id: uuid.UUID, page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), orderby: Optional[str]=Query(None, description='Sort fields, such as: created_at'), desc: Optional[bool]=Query(False, description='Is it descending order'), keywords: Optional[str]=Query(None, description='Search keywords (file name)'), db: Session=Depends(get_db), current_user: User=Depends(get_current_user), request: Request=None):
    """Paged query file list"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/folder', response_model=ApiResponse)
@cur_workspace_access_guard()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def create_folder(kb_id: uuid.UUID, parent_id: uuid.UUID, folder_name: str='/', db: Session=Depends(get_db), current_user: User=Depends(get_current_user), request: Request=None):
    """Create a new folder"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/file', response_model=ApiResponse)
@cur_workspace_access_guard()
@check_knowledge_capacity_quota
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API, profile=CallProfile.MULTIPART_UPLOAD)
async def upload_file(kb_id: uuid.UUID, parent_id: uuid.UUID, file: UploadFile=File(...), db: Session=Depends(get_db), current_user: User=Depends(get_current_user), storage_service: FileStorageService=Depends(get_file_storage_service), request: Request=None):
    """Upload file to storage backend"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/customtext', response_model=ApiResponse)
@cur_workspace_access_guard()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def custom_text(kb_id: uuid.UUID, parent_id: uuid.UUID, create_data: file_schema.CustomTextFileCreate, db: Session=Depends(get_db), current_user: User=Depends(get_current_user), storage_service: FileStorageService=Depends(get_file_storage_service), request: Request=None):
    """Custom text upload"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{file_id}', response_model=Any)
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API, profile=CallProfile.STREAM_DOWNLOAD, public=True)
async def get_file(file_id: uuid.UUID, original: bool=Query(False, description='QA 文档是否下载原始文件（默认从 ES 导出修改后内容）'), db: Session=Depends(get_db), storage_service: FileStorageService=Depends(get_file_storage_service), request: Request=None) -> Any:
    """Download file by file_id — QA 文档默认从 ES 导出修改后内容，?original=true 下载原始文件"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/batch-download')
@cur_workspace_access_guard()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API, profile=CallProfile.STREAM_DOWNLOAD)
async def batch_download_files(request_body: file_schema.BatchDownloadRequest, current_user: User=Depends(get_current_user), db: Session=Depends(get_db), storage_service: FileStorageService=Depends(get_file_storage_service), request: Request=None):
    """批量下载文件，边打包边推流（streaming ZIP，内存占用恒定）。
    QA 文档从 ES 导出修改后的内容，其余从存储下载。
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.put('/{file_id}', response_model=ApiResponse)
@cur_workspace_access_guard()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def update_file(file_id: uuid.UUID, update_data: file_schema.FileUpdate, db: Session=Depends(get_db), current_user: User=Depends(get_current_user), request: Request=None):
    """Update file information (such as file name)"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{file_id}', response_model=ApiResponse)
@cur_workspace_access_guard()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def delete_file(file_id: uuid.UUID, db: Session=Depends(get_db), current_user: User=Depends(get_current_user), storage_service: FileStorageService=Depends(get_file_storage_service), request: Request=None):
    """Delete a file or folder"""
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
