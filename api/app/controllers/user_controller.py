from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import uuid

from app.db import get_db
from app.dependencies import get_current_user, get_current_superuser
from app.models.user_model import User
from app.schemas import user_schema
from app.schemas.user_schema import ChangePasswordRequest, AdminChangePasswordRequest
from app.schemas.response_schema import ApiResponse
from app.services import user_service
from app.core.logging_config import get_api_logger
from app.core.response_utils import success

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


@router.post("/superuser", response_model=ApiResponse)
def create_superuser(
    user: user_schema.UserCreate,
    db: Session = Depends(get_db),
    current_superuser: User = Depends(get_current_superuser)
):
    """创建超级管理员（仅超级管理员可访问）"""
    api_logger.info(f"超级管理员创建请求: {user.username}, email: {user.email}")
    
    result = user_service.create_superuser(db=db, user=user, current_user=current_superuser)
    api_logger.info(f"超级管理员创建成功: {result.username} (ID: {result.id})")
    
    result_schema = user_schema.User.model_validate(result)
    return success(data=result_schema, msg="超级管理员创建成功")


@router.delete("/{user_id}", response_model=ApiResponse)
def delete_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """停用用户（软删除）"""
    api_logger.info(f"用户停用请求: user_id={user_id}, 操作者: {current_user.username}")
    result = user_service.deactivate_user(
        db=db, user_id_to_deactivate=user_id, current_user=current_user
    )
    api_logger.info(f"用户停用成功: {result.username} (ID: {result.id})")
    return success(msg="用户停用成功")

@router.post("/{user_id}/activate", response_model=ApiResponse)
def activate_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """激活用户"""
    api_logger.info(f"用户激活请求: user_id={user_id}, 操作者: {current_user.username}")
    
    result = user_service.activate_user(
        db=db, user_id_to_activate=user_id, current_user=current_user
    )
    api_logger.info(f"用户激活成功: {result.username} (ID: {result.id})")
    
    result_schema = user_schema.User.model_validate(result)
    return success(data=result_schema, msg="用户激活成功")


@router.get("", response_model=ApiResponse)
def get_current_user_info(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户信息"""
    api_logger.info(f"当前用户信息请求: {current_user.username}")
    
    result = user_service.get_user(
        db=db, user_id=current_user.id, current_user=current_user
    )
    
    result_schema = user_schema.User.model_validate(result)
    
    # 设置当前工作空间的角色和名称
    if current_user.current_workspace_id:
        from app.repositories.workspace_repository import WorkspaceRepository
        workspace_repo = WorkspaceRepository(db)
        current_workspace = workspace_repo.get_workspace_by_id(current_user.current_workspace_id)
        if current_workspace:
            result_schema.current_workspace_name = current_workspace.name
        
        for ws in result.workspaces:
            if ws.workspace_id == current_user.current_workspace_id:
                result_schema.role = ws.role
                break
    
    api_logger.info(f"当前用户信息获取成功: {result.username}, 角色: {result_schema.role}, 工作空间: {result_schema.current_workspace_name}")
    return success(data=result_schema, msg="用户信息获取成功")


@router.get("/superusers", response_model=ApiResponse)
def get_tenant_superusers(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    """获取当前租户下的超管账号列表（仅超级管理员可访问）"""
    api_logger.info(f"获取租户超管列表请求: {current_user.username}")
    
    superusers = user_service.get_tenant_superusers(
        db=db, 
        current_user=current_user, 
        include_inactive=include_inactive
    )
    api_logger.info(f"租户超管列表获取成功: count={len(superusers)}")
    
    superusers_schema = [user_schema.User.model_validate(u) for u in superusers]
    return success(data=superusers_schema, msg="租户超管列表获取成功")


@router.get("/{user_id}", response_model=ApiResponse)
def get_user_info_by_id(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """根据用户ID获取用户信息"""
    api_logger.info(f"获取用户信息请求: user_id={user_id}, 操作者: {current_user.username}")
    
    result = user_service.get_user(
        db=db, user_id=user_id, current_user=current_user
    )
    api_logger.info(f"用户信息获取成功: {result.username}")
    
    result_schema = user_schema.User.model_validate(result)
    return success(data=result_schema, msg="用户信息获取成功")


@router.put("/change-password", response_model=ApiResponse)
async def change_password(
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改当前用户密码"""
    api_logger.info(f"用户密码修改请求: {current_user.username}")
    
    await user_service.change_password(
        db=db,
        user_id=current_user.id,
        old_password=request.old_password,
        new_password=request.new_password,
        current_user=current_user
    )
    api_logger.info(f"用户密码修改成功: {current_user.username}")
    return success(msg="密码修改成功")


@router.put("/admin/change-password", response_model=ApiResponse)
async def admin_change_password(
    request: AdminChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    """超级管理员修改指定用户的密码"""
    api_logger.info(f"管理员密码修改请求: 管理员 {current_user.username} 修改用户 {request.user_id}")
    
    user, generated_password = await user_service.admin_change_password(
        db=db,
        target_user_id=request.user_id,
        new_password=request.new_password,
        current_user=current_user
    )
    
    # 根据是否生成了随机密码来构造响应
    if request.new_password:
        api_logger.info(f"管理员密码修改成功: 用户 {request.user_id}")
        return success(msg="密码修改成功")
    else:
        api_logger.info(f"管理员密码重置成功: 用户 {request.user_id}, 随机密码已生成")
        return success(data=generated_password, msg="密码重置成功")