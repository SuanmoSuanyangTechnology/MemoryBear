import uuid
from typing import List, Optional

from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import (
    cur_workspace_access_guard,
    get_current_superuser,
    get_current_tenant,
    get_current_user,
    workspace_access_guard,
)
from app.models.tenant_model import Tenants
from app.models.user_model import User
from app.models.workspace_model import InviteStatus, Workspace
from app.schemas import knowledge_schema
from app.schemas.response_schema import ApiResponse
from app.schemas.workspace_schema import (
    InviteAcceptRequest,
    InviteValidateResponse,
    WorkspaceCreate,
    WorkspaceInviteCreate,
    WorkspaceInviteResponse,
    WorkspaceMemberItem,
    WorkspaceMemberUpdate,
    WorkspaceModelsConfig,
    WorkspaceModelsUpdate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.services import document_service, knowledge_service, workspace_service
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

# 获取API专用日志器
api_logger = get_api_logger()
# 需要认证的路由器
router = APIRouter(
    prefix="/workspaces",
    tags=["Workspaces"],
    dependencies=[Depends(get_current_user)]
)

# 公开路由器（不需要认证）
public_router = APIRouter(
    prefix="/workspaces",
    tags=["Workspaces"]
)


def _convert_members_to_table_items(members):
    """将工作空间成员列表转换为表格项"""
    return [
        WorkspaceMemberItem(
            id=m.id,
            username=m.user.username,
            account=m.user.email,
            role=m.role,
            last_login_at=m.user.last_login_at
        )
        for m in members
    ]


@router.get("", response_model=ApiResponse)
def get_workspaces(
    include_current: bool = Query(True, description="是否包含当前工作空间"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenants = Depends(get_current_tenant)
):
    """获取当前租户下用户参与的所有工作空间
    
    Args:
        include_current: 是否包含当前工作空间（默认 True）
    """
    api_logger.info(
        f"用户 {current_user.username} 在租户 {current_tenant.name} 中请求获取工作空间列表",
        extra={"include_current": include_current}
    )
    
    workspaces = workspace_service.get_user_workspaces(db, current_user)
    
    # 如果不包含当前工作空间，则过滤掉
    if not include_current and current_user.current_workspace_id:
        workspaces = [w for w in workspaces if w.id != current_user.current_workspace_id]
        api_logger.debug(
            "过滤掉当前工作空间",
            extra={"current_workspace_id": str(current_user.current_workspace_id)}
        )
    
    api_logger.info(f"成功获取 {len(workspaces)} 个工作空间")
    workspaces_schema = [WorkspaceResponse.model_validate(w) for w in workspaces]
    return success(data=workspaces_schema, msg="工作空间列表获取成功")


@router.post("", response_model=ApiResponse)
def create_workspace(
    workspace: WorkspaceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    """创建新的工作空间"""
    api_logger.info(f"用户 {current_user.username} 请求创建工作空间: {workspace.name}")
    
    result = workspace_service.create_workspace(
        db=db, workspace=workspace, user=current_user)
    
    api_logger.info(f"工作空间创建成功 - 名称: {workspace.name}, ID: {result.id}, 创建者: {current_user.username}")
    result_schema = WorkspaceResponse.model_validate(result)
    return success(data=result_schema, msg="工作空间创建成功")

@router.put("", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_workspace(    
    workspace: WorkspaceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新工作空间"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求更新工作空间 ID: {workspace_id}")
    
    result = workspace_service.update_workspace(
        db=db,
        workspace_id=workspace_id,
        workspace_in=workspace,
        user=current_user,
    )
    api_logger.info(f"工作空间更新成功 - ID: {workspace_id}, 用户: {current_user.username}")
    result_schema = WorkspaceResponse.model_validate(result)
    return success(data=result_schema, msg="工作空间更新成功")

@router.get("/members", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_cur_workspace_members(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取工作空间成员列表（关系序列化）"""
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {current_user.current_workspace_id} 的成员列表")
    
    members = workspace_service.get_workspace_members(
        db=db,
        workspace_id=current_user.current_workspace_id,
        user=current_user,
    )
    api_logger.info(f"工作空间成员列表获取成功 - ID: {current_user.current_workspace_id}, 数量: {len(members)}")
    table_items = _convert_members_to_table_items(members)
    return success(data=table_items, msg="工作空间成员列表获取成功")


@router.put("/members", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_workspace_members(
    
    updates: List[WorkspaceMemberUpdate],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求更新工作空间 {workspace_id} 的成员角色")
    members = workspace_service.update_workspace_member_roles(
        db=db,
        workspace_id=workspace_id,
        updates=updates,
        user=current_user,
    )
    api_logger.info(f"工作空间成员角色更新成功 - ID: {workspace_id}, 数量: {len(members)}")
    return success(msg="成员角色更新成功")


@router.delete("/members/{member_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def delete_workspace_member(
    member_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求删除工作空间 {workspace_id} 的成员 {member_id}")
    
    workspace_service.delete_workspace_member(
        db=db,
        workspace_id=workspace_id,
        member_id=member_id,
        user=current_user,
    )
    api_logger.info(f"工作空间成员删除成功 - ID: {workspace_id}, 成员: {member_id}")
    return success(msg="成员删除成功")


# 创建空间协作邀请
@router.post("/invites", response_model=ApiResponse)
@cur_workspace_access_guard()
def create_workspace_invite(    
    invite_data: WorkspaceInviteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建工作空间邀请"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求为工作空间 {workspace_id} 创建邀请: {invite_data.email}")
    
    result = workspace_service.create_workspace_invite(
        db=db,
        workspace_id=workspace_id,
        invite_data=invite_data,
        user=current_user
    )
    api_logger.info(f"工作空间邀请创建成功 - 工作空间: {workspace_id}, 邮箱: {invite_data.email}")
    return success(data=result, msg="邀请创建成功")


@router.get("/invites", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_workspace_invites(
    
    status_filter: Optional[InviteStatus] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取工作空间邀请列表"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的邀请列表")
    
    invites = workspace_service.get_workspace_invites(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
        status=status_filter,
        limit=limit,
        offset=offset
    )
    api_logger.info(f"成功获取 {len(invites)} 个邀请记录")
    return success(data=invites, msg="邀请列表获取成功")


@public_router.get("/invites/validate/{token}", response_model=ApiResponse)
def get_workspace_invite_info(
    token: str,
    db: Session = Depends(get_db),
):
    """获取工作空间邀请用户信息（无需认证）"""
    result = workspace_service.validate_invite_token(db=db, token=token)
    api_logger.info(f"工作空间邀请验证成功 - 邀请: {token}")    
    return success(data=result, msg="邀请验证成功")


@router.delete("/invites/{invite_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def revoke_workspace_invite(
    
    invite_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销工作空间邀请"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求撤销工作空间 {workspace_id} 的邀请 {invite_id}")
    
    result = workspace_service.revoke_workspace_invite(
        db=db,
        workspace_id=workspace_id,
        invite_id=invite_id,
        user=current_user
    )
    api_logger.info(f"工作空间邀请撤销成功 - 邀请: {invite_id}")
    return success(data=result, msg="邀请撤销成功")

# ==================== 公开邀请接口（无需认证） ====================

# # 创建一个新的路由器用于公开接口
# public_router = APIRouter(
#     prefix="/invites",
#     tags=["Public Invites"]
# )

# @public_router.get("/validate", response_model=ApiResponse)
# def validate_invite_token(
#     token: str = Query(..., description="邀请令牌"),
#     db: Session = Depends(get_db),
# ):
#     """验证邀请令牌（公开接口）"""
#     api_logger.info(f"验证邀请令牌请求")
@router.put("/{workspace_id}/switch", response_model=ApiResponse)
@workspace_access_guard()
def switch_workspace(
    workspace_id: uuid.UUID,   
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """切换工作空间"""
    api_logger.info(f"用户 {current_user.username} 请求切换工作空间为 {workspace_id}")
    
    workspace_service.switch_workspace(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
    )
    api_logger.info(f"成功切换工作空间为 {workspace_id}")
    return success(msg="工作空间切换成功")


@router.get("/storage", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_workspace_storage_type(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """获取当前工作空间的存储类型"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的存储类型")

    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    api_logger.info(f"成功获取工作空间 {workspace_id} 的存储类型: {storage_type}")
    return success(data={"storage_type": storage_type}, msg="存储类型获取成功")


@router.get("/workspace_models", response_model=ApiResponse)
@cur_workspace_access_guard()
def workspace_models_configs(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """获取当前工作空间的模型配置（llm, embedding, rerank）"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的模型配置")

    configs = workspace_service.get_workspace_models_configs(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    
    if configs is None:
        api_logger.warning(f"工作空间 {workspace_id} 不存在或无权访问")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="工作空间不存在或无权访问"
        )
    
    api_logger.info(
        f"成功获取工作空间 {workspace_id} 的模型配置: "
        f"llm={configs.get('llm')}, embedding={configs.get('embedding')}, rerank={configs.get('rerank')}"
    )
    return success(data=WorkspaceModelsConfig.model_validate(configs), msg="模型配置获取成功")


@router.put("/workspace_models", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_workspace_models_configs(
        models_update: WorkspaceModelsUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """更新当前工作空间的模型配置（llm, embedding, rerank）"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求更新工作空间 {workspace_id} 的模型配置")

    updated_workspace = workspace_service.update_workspace_models_configs(
        db=db,
        workspace_id=workspace_id,
        models_update=models_update,
        user=current_user
    )
    
    api_logger.info(
        f"成功更新工作空间 {workspace_id} 的模型配置: "
        f"llm={updated_workspace.llm}, embedding={updated_workspace.embedding}, rerank={updated_workspace.rerank}"
    )
    return success(data=WorkspaceModelsConfig.model_validate(updated_workspace), msg="模型配置更新成功")

