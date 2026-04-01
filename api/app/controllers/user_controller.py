from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import uuid
from typing import Callable

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.db import get_db
from app.dependencies import get_current_user, get_current_superuser
from app.models.user_model import User
from app.schemas import user_schema
from app.schemas.user_schema import (
    ChangePasswordRequest,
    AdminChangePasswordRequest,
    SendEmailCodeRequest,
    VerifyEmailCodeRequest,
    VerifyPasswordRequest)
from app.schemas.response_schema import ApiResponse
from app.services import user_service
from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.core.security import verify_password
from app.i18n.dependencies import get_translator

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
    current_superuser: User = Depends(get_current_superuser),
    t: Callable = Depends(get_translator)
):
    """创建超级管理员（仅超级管理员可访问）"""
    api_logger.info(f"超级管理员创建请求: {user.username}, email: {user.email}")
    
    result = user_service.create_superuser(db=db, user=user, current_user=current_superuser)
    api_logger.info(f"超级管理员创建成功: {result.username} (ID: {result.id})")
    
    result_schema = user_schema.User.model_validate(result)
    return success(data=result_schema, msg=t("users.create.superuser_success"))


@router.delete("/{user_id}", response_model=ApiResponse)
def delete_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    """停用用户（软删除）"""
    api_logger.info(f"用户停用请求: user_id={user_id}, 操作者: {current_user.username}")
    result = user_service.deactivate_user(
        db=db, user_id_to_deactivate=user_id, current_user=current_user
    )
    api_logger.info(f"用户停用成功: {result.username} (ID: {result.id})")
    return success(msg=t("users.delete.deactivate_success"))

@router.post("/{user_id}/activate", response_model=ApiResponse)
def activate_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    """激活用户"""
    api_logger.info(f"用户激活请求: user_id={user_id}, 操作者: {current_user.username}")
    
    result = user_service.activate_user(
        db=db, user_id_to_activate=user_id, current_user=current_user
    )
    api_logger.info(f"用户激活成功: {result.username} (ID: {result.id})")
    
    result_schema = user_schema.User.model_validate(result)
    return success(data=result_schema, msg=t("users.activate.success"))


@router.get("", response_model=ApiResponse)
def get_current_user_info(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
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
            if ws.workspace_id == current_user.current_workspace_id and ws.is_active:
                result_schema.role = ws.role
                break
    
    api_logger.info(f"当前用户信息获取成功: {result.username}, 角色: {result_schema.role}, 工作空间: {result_schema.current_workspace_name}")

    # 设置权限：如果用户来自 SSO Source，则使用该 Source 的 permissions；否则返回 "all" 表示拥有所有权限
    if current_user.external_source:
        from premium.sso.models import SSOSource
        source = db.query(SSOSource).filter(SSOSource.source_code == current_user.external_source).first()
        if source and source.permissions:
            result_schema.permissions = source.permissions
        else:
            result_schema.permissions = []
    else:
        result_schema.permissions = ["all"]

    return success(data=result_schema, msg=t("users.info.get_success"))


@router.get("/superusers", response_model=ApiResponse)
def get_tenant_superusers(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
    t: Callable = Depends(get_translator)
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
    return success(data=superusers_schema, msg=t("users.list.superusers_success"))


@router.get("/{user_id}", response_model=ApiResponse)
def get_user_info_by_id(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    """根据用户ID获取用户信息"""
    api_logger.info(f"获取用户信息请求: user_id={user_id}, 操作者: {current_user.username}")
    
    result = user_service.get_user(
        db=db, user_id=user_id, current_user=current_user
    )
    api_logger.info(f"用户信息获取成功: {result.username}")
    
    result_schema = user_schema.User.model_validate(result)
    return success(data=result_schema, msg=t("users.info.get_success"))


@router.put("/change-password", response_model=ApiResponse)
async def change_password(
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
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
    return success(msg=t("auth.password.change_success"))


@router.put("/admin/change-password", response_model=ApiResponse)
async def admin_change_password(
    request: AdminChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
    t: Callable = Depends(get_translator)
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
        return success(msg=t("auth.password.change_success"))
    else:
        api_logger.info(f"管理员密码重置成功: 用户 {request.user_id}, 随机密码已生成")
        return success(data=generated_password, msg=t("auth.password.reset_success"))


@router.post("/verify_pwd", response_model=ApiResponse)
def verify_pwd(
    request: VerifyPasswordRequest,
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    """验证当前用户密码"""
    api_logger.info(f"用户验证密码请求: {current_user.username}")
    
    is_valid = verify_password(request.password, current_user.hashed_password)
    api_logger.info(f"用户密码验证结果: {current_user.username}, valid={is_valid}")
    if not is_valid:
        raise BusinessException(t("users.errors.password_verification_failed"), code=BizCode.VALIDATION_FAILED)
    return success(data={"valid": is_valid}, msg=t("common.success.retrieved"))


@router.post("/send-email-code", response_model=ApiResponse)
async def send_email_code(
    request: SendEmailCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    """发送邮箱验证码"""
    api_logger.info(f"用户请求发送邮箱验证码: {current_user.username}, email={request.email}")
    
    await user_service.send_email_code_method(db=db, email=request.email, user_id=current_user.id)
    
    api_logger.info(f"邮箱验证码已发送: {current_user.username}")
    return success(msg=t("users.email.code_sent"))


@router.put("/change-email", response_model=ApiResponse)
async def change_email(
    request: VerifyEmailCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    """验证验证码并修改邮箱"""
    api_logger.info(f"用户修改邮箱: {current_user.username}, new_email={request.new_email}")

    await user_service.verify_and_change_email(
        db=db,
        user_id=current_user.id,
        new_email=request.new_email,
        code=request.code
    )
    
    api_logger.info(f"用户邮箱修改成功: {current_user.username}")
    return success(msg=t("users.email.change_success"))



@router.get("/me/language", response_model=ApiResponse)
def get_current_user_language(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    """获取当前用户的语言偏好"""
    api_logger.info(f"获取用户语言偏好: {current_user.username}")
    
    language = user_service.get_user_language_preference(
        db=db,
        user_id=current_user.id,
        current_user=current_user
    )
    
    api_logger.info(f"用户语言偏好获取成功: {current_user.username}, language={language}")
    return success(
        data=user_schema.LanguagePreferenceResponse(language=language),
        msg=t("users.language.get_success")
    )


@router.put("/me/language", response_model=ApiResponse)
def update_current_user_language(
    request: user_schema.LanguagePreferenceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    """设置当前用户的语言偏好"""
    api_logger.info(f"更新用户语言偏好: {current_user.username}, language={request.language}")
    
    updated_user = user_service.update_user_language_preference(
        db=db,
        user_id=current_user.id,
        language=request.language,
        current_user=current_user
    )
    
    api_logger.info(f"用户语言偏好更新成功: {current_user.username}, language={request.language}")
    return success(
        data=user_schema.LanguagePreferenceResponse(language=updated_user.preferred_language),
        msg=t("users.language.update_success")
    )
