"""User management routes migrated from the monolith.

All handlers are synchronous: the management plane runs on the sync engine.
Declaration order mirrors ``core/api/app/controllers/user_controller.py`` —
literal routes such as ``/superusers`` and ``/change-password`` must stay ahead
of ``GET /{user_id}``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from error_core.codes import BizCode
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.deps import get_current_superuser, get_current_user
from src.core.exceptions import BusinessException
from src.core.response_utils import success
from src.core.security import verify_password
from src.db import get_db
from src.i18n import get_translator
from src.models.user_model import User
from src.repositories import management
from src.schemas import user_schema
from src.schemas.response_schema import ApiResponse
from src.schemas.user_schema import (
    AdminChangePasswordRequest,
    ChangePasswordRequest,
    SendEmailCodeRequest,
    VerifyEmailCodeRequest,
    VerifyPasswordRequest,
)
from src.services import enterprise, user_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/superuser", response_model=ApiResponse)
def create_superuser(
    user: user_schema.UserCreate,
    db: Session = Depends(get_db),
    current_superuser: User = Depends(get_current_superuser),
    t: Callable = Depends(get_translator),
):
    """创建超级管理员（仅超级管理员可访问）"""
    result = user_service.create_superuser(
        db=db, user=user, current_user=current_superuser
    )
    return success(
        data=user_schema.User.model_validate(result),
        msg=t("users.create.superuser_success"),
    )


@router.delete("/{user_id}", response_model=ApiResponse)
def delete_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """停用用户（软删除）"""
    user_service.deactivate_user(
        db=db, user_id_to_deactivate=user_id, current_user=current_user
    )
    return success(msg=t("users.delete.deactivate_success"))


@router.post("/{user_id}/activate", response_model=ApiResponse)
def activate_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """激活用户"""
    result = user_service.activate_user(
        db=db, user_id_to_activate=user_id, current_user=current_user
    )
    return success(
        data=user_schema.User.model_validate(result),
        msg=t("users.activate.success"),
    )


@router.get("", response_model=ApiResponse)
def get_current_user_info(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """获取当前用户信息"""
    result = user_service.get_user(
        db=db, user_id=current_user.id, current_user=current_user
    )
    result_schema = user_schema.User.model_validate(result)

    if current_user.current_workspace_id:
        workspace = management.get_workspace_by_id(
            db, current_user.current_workspace_id
        )
        if workspace:
            result_schema.current_workspace_name = workspace.name

        member = management.get_member_in_workspace(
            db, current_user.id, current_user.current_workspace_id
        )
        if member:
            result_schema.role = member.role

    result_schema.permissions = enterprise.user_permissions(
        db, current_user.external_source
    )
    return success(data=result_schema, msg=t("users.info.get_success"))


@router.get("/superusers", response_model=ApiResponse)
def get_tenant_superusers(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
    t: Callable = Depends(get_translator),
):
    """获取当前租户下的超管账号列表（仅超级管理员可访问）"""
    superusers = user_service.get_tenant_superusers(
        db=db, current_user=current_user, include_inactive=include_inactive
    )
    return success(
        data=[user_schema.User.model_validate(u) for u in superusers],
        msg=t("users.list.superusers_success"),
    )


@router.get("/{user_id}", response_model=ApiResponse)
def get_user_info_by_id(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """根据用户ID获取用户信息"""
    result = user_service.get_user(db=db, user_id=user_id, current_user=current_user)
    return success(
        data=user_schema.User.model_validate(result),
        msg=t("users.info.get_success"),
    )


@router.put("/change-password", response_model=ApiResponse)
def change_password(
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """修改当前用户密码"""
    user_service.change_password(
        db=db,
        user_id=current_user.id,
        old_password=request.old_password,
        new_password=request.new_password,
        current_user=current_user,
    )
    return success(msg=t("auth.password.change_success"))


@router.put("/admin/change-password", response_model=ApiResponse)
def admin_change_password(
    request: AdminChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
    t: Callable = Depends(get_translator),
):
    """超级管理员修改指定用户的密码"""
    _, generated_password = user_service.admin_change_password(
        db=db,
        target_user_id=request.user_id,
        new_password=request.new_password,
        current_user=current_user,
    )
    if request.new_password:
        return success(msg=t("auth.password.change_success"))
    return success(data=generated_password, msg=t("auth.password.reset_success"))


@router.post("/verify_pwd", response_model=ApiResponse)
def verify_pwd(
    request: VerifyPasswordRequest,
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """验证当前用户密码"""
    is_valid = verify_password(request.password, current_user.hashed_password)
    if not is_valid:
        raise BusinessException(
            t("users.errors.password_verification_failed"),
            code=BizCode.VALIDATION_FAILED,
        )
    return success(data={"valid": is_valid}, msg=t("common.success.retrieved"))


@router.post("/send-email-code", response_model=ApiResponse)
def send_email_code(
    request: SendEmailCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """发送邮箱验证码"""
    user_service.send_email_code_method(
        db=db, email=request.email, user_id=current_user.id
    )
    return success(msg=t("users.email.code_sent"))


@router.put("/change-email", response_model=ApiResponse)
def change_email(
    request: VerifyEmailCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """验证验证码并修改邮箱"""
    user_service.verify_and_change_email(
        db=db,
        user_id=current_user.id,
        new_email=request.new_email,
        code=request.code,
    )
    return success(msg=t("users.email.change_success"))


@router.get("/me/language", response_model=ApiResponse)
def get_current_user_language(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """获取当前用户的语言偏好"""
    language = user_service.get_user_language_preference(
        db=db, user_id=current_user.id, current_user=current_user
    )
    return success(
        data=user_schema.LanguagePreferenceResponse(language=language),
        msg=t("users.language.get_success"),
    )


@router.put("/me/language", response_model=ApiResponse)
def update_current_user_language(
    request: user_schema.LanguagePreferenceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """设置当前用户的语言偏好"""
    updated_user = user_service.update_user_language_preference(
        db=db,
        user_id=current_user.id,
        language=request.language,
        current_user=current_user,
    )
    return success(
        data=user_schema.LanguagePreferenceResponse(
            language=updated_user.preferred_language
        ),
        msg=t("users.language.update_success"),
    )
