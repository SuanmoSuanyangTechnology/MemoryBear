import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.response_utils import success
from app.core.logging_config import get_business_logger
from app.schemas import release_share_schema
from app.services.release_share_service import ReleaseShareService
from app.dependencies import get_current_user, cur_workspace_access_guard

router = APIRouter(tags=["Release Share"])
logger = get_business_logger()


def get_base_url(request: Request) -> str:
    """从请求中获取基础 URL"""
    return f"{request.url.scheme}://{request.url.netloc}"


@router.post(
    "/apps/{app_id}/releases/{release_id}/share",
    summary="创建/启用分享配置"
)
@cur_workspace_access_guard()
def create_share(
    app_id: uuid.UUID,
    release_id: uuid.UUID,
    payload: release_share_schema.ReleaseShareCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """创建或更新发布版本的分享配置
    
    - 如果已存在分享配置，则更新
    - 自动生成唯一的分享 token
    - 返回完整的分享 URL
    """
    workspace_id = current_user.current_workspace_id
    base_url = get_base_url(request)
    
    service = ReleaseShareService(db)
    share = service.create_or_update_share(
        release_id=release_id,
        user_id=current_user.id,
        workspace_id=workspace_id,
        data=payload,
        base_url=base_url
    )
    
    share_schema = service._convert_to_schema(share, base_url)
    return success(data=share_schema, msg="分享配置已创建")


@router.put(
    "/apps/{app_id}/releases/{release_id}/share",
    summary="更新分享配置"
)
@cur_workspace_access_guard()
def update_share(
    app_id: uuid.UUID,
    release_id: uuid.UUID,
    payload: release_share_schema.ReleaseShareUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """更新分享配置
    
    - 可以更新启用状态、密码、嵌入设置等
    - 不会改变 share_token
    """
    workspace_id = current_user.current_workspace_id
    base_url = get_base_url(request)
    
    service = ReleaseShareService(db)
    share = service.update_share(
        release_id=release_id,
        workspace_id=workspace_id,
        data=payload
    )
    
    share_schema = service._convert_to_schema(share, base_url)
    return success(data=share_schema, msg="分享配置已更新")


@router.get(
    "/apps/{app_id}/releases/{release_id}/share",
    summary="获取分享配置"
)
@cur_workspace_access_guard()
def get_share(
    app_id: uuid.UUID,
    release_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """获取发布版本的分享配置
    
    - 如果不存在分享配置，返回 null
    """
    workspace_id = current_user.current_workspace_id
    base_url = get_base_url(request)
    
    service = ReleaseShareService(db)
    share = service.get_share(
        release_id=release_id,
        workspace_id=workspace_id,
        base_url=base_url
    )
    
    return success(data=share)


@router.delete(
    "/apps/{app_id}/releases/{release_id}/share",
    summary="删除分享配置"
)
@cur_workspace_access_guard()
def delete_share(
    app_id: uuid.UUID,
    release_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """删除分享配置
    
    - 删除后，公开访问链接将失效
    """
    workspace_id = current_user.current_workspace_id
    
    service = ReleaseShareService(db)
    service.delete_share(
        release_id=release_id,
        workspace_id=workspace_id
    )
    
    return success(msg="分享配置已删除")


@router.post(
    "/apps/{app_id}/releases/{release_id}/share/regenerate-token",
    summary="重新生成分享链接"
)
@cur_workspace_access_guard()
def regenerate_token(
    app_id: uuid.UUID,
    release_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """重新生成分享 token
    
    - 旧的分享链接将失效
    - 生成新的唯一 token
    """
    workspace_id = current_user.current_workspace_id
    base_url = get_base_url(request)
    
    service = ReleaseShareService(db)
    share = service.regenerate_token(
        release_id=release_id,
        workspace_id=workspace_id
    )
    
    share_schema = service._convert_to_schema(share, base_url)
    return success(data=share_schema, msg="分享链接已重新生成")
