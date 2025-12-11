"""API Key 管理接口 - 基于 JWT 认证"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
import uuid

from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.models.user_model import User
from app.core.response_utils import success
from app.schemas import api_key_schema
from app.schemas.response_schema import ApiResponse
from app.services.api_key_service import ApiKeyService
from app.core.logging_config import get_business_logger

router = APIRouter(prefix="/apikeys", tags=["API Keys"])
logger = get_business_logger()


@router.post("", response_model=ApiResponse)
@cur_workspace_access_guard()
def create_api_key(
    data: api_key_schema.ApiKeyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建 API Key
    
    - 创建后返回明文 API Key（仅此一次）
    - 支持设置权限范围、速率限制、配额等
    """
    workspace_id = current_user.current_workspace_id
    api_key_obj, api_key = ApiKeyService.create_api_key(
        db,
        workspace_id=workspace_id,
        user_id=current_user.id,
        data=data
    )
    
    # 返回包含明文 Key 的响应
    response_data = api_key_schema.ApiKeyResponse(
        **api_key_obj.__dict__,
        api_key=api_key
    )
    
    return success(data=response_data, msg="API Key 创建成功")


@router.get("", response_model=ApiResponse)
@cur_workspace_access_guard()
def list_api_keys(
    type: api_key_schema.ApiKeyType = Query(None),
    is_active: bool = Query(None),
    resource_id: uuid.UUID = Query(None),
    page: int = Query(1, ge=1),
    pagesize: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """列出 API Keys"""
    workspace_id = current_user.current_workspace_id
    query = api_key_schema.ApiKeyQuery(
        type=type,
        is_active=is_active,
        resource_id=resource_id,
        page=page,
        pagesize=pagesize
    )
    
    result = ApiKeyService.list_api_keys(db, workspace_id, query)
    return success(data=result)


@router.get("/{api_key_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_api_key(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取 API Key 详情"""
    workspace_id = current_user.current_workspace_id
    api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)
    
    return success(data=api_key_schema.ApiKey.model_validate(api_key))


@router.put("/{api_key_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_api_key(
    api_key_id: uuid.UUID,
    data: api_key_schema.ApiKeyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新 API Key"""
    workspace_id = current_user.current_workspace_id
    api_key = ApiKeyService.update_api_key(db, api_key_id, workspace_id, data)
    
    return success(data=api_key_schema.ApiKey.model_validate(api_key), msg="API Key 更新成功")


@router.delete("/{api_key_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def delete_api_key(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除 API Key"""
    workspace_id = current_user.current_workspace_id
    ApiKeyService.delete_api_key(db, api_key_id, workspace_id)
    
    return success(msg="API Key 删除成功")


@router.post("/{api_key_id}/regenerate", response_model=ApiResponse)
@cur_workspace_access_guard()
def regenerate_api_key(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """重新生成 API Key
    
    - 生成新的 API Key 并返回明文（仅此一次）
    - 旧的 API Key 立即失效
    """
    workspace_id = current_user.current_workspace_id
    api_key_obj, api_key = ApiKeyService.regenerate_api_key(db, api_key_id, workspace_id)
    
    # 返回包含明文 Key 的响应
    response_data = api_key_schema.ApiKeyResponse(
        **api_key_obj.__dict__,
        api_key=api_key
    )
    
    return success(data=response_data, msg="API Key 重新生成成功")


@router.get("/{api_key_id}/stats", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_api_key_stats(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取 API Key 使用统计"""
    workspace_id = current_user.current_workspace_id
    stats = ApiKeyService.get_stats(db, api_key_id, workspace_id)
    
    return success(data=stats)
