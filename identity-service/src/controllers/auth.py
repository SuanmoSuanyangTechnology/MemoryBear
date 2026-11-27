"""Authentication routes migrated from the monolith.

``POST /api/token`` and ``POST /api/refresh`` are public, ``POST /api/logout``
requires the user JWT. Routes are plain ``def`` handlers: the management
plane runs on the sync engine (CLAUDE.md §0.5), and the alert bridge /
invalidation notifier are Task 7 / Task 3's sync ports.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from error_core.codes import BizCode
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.alert_metric_bridge import report_login_failure
from src.config import settings
from src.core import security
from src.core.datetime_utils import utcnow
from src.core.deps import get_current_user, oauth2_scheme
from src.core.exceptions import BusinessException
from src.core.response_utils import success
from src.db import get_db
from src.i18n import get_translator
from src.invalidation_notify import notify_user_sync
from src.models.user_model import User
from src.schemas.response_schema import ApiResponse
from src.schemas.token_schema import RefreshTokenRequest, Token, TokenRequest
from src.services import auth_service, invite_service, user_service
from src.services.session_service import SessionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Authentication"])


@router.post("/token", response_model=ApiResponse)
def login_for_access_token(
    form_data: TokenRequest,
    db: Session = Depends(get_db),
    t: Callable = Depends(get_translator),
):
    """用户登录获取token"""
    logger.info("用户登录请求: %s", form_data.email)

    invite_info = None
    user = None
    if form_data.invite:
        logger.info("检测到邀请码: %s...", form_data.invite[:8])
        invite_info = invite_service.validate_invite_token(db, form_data.invite)

        if not invite_info.is_valid:
            raise BusinessException(t("auth.invite.invalid"), code=BizCode.BAD_REQUEST)

        if invite_info.email != form_data.email:
            raise BusinessException(
                t("auth.invite.email_mismatch"), code=BizCode.BAD_REQUEST
            )

        logger.info("邀请码验证成功: workspace=%s", invite_info.workspace_name)
        try:
            user = auth_service.authenticate_user_or_raise(
                db, form_data.email, form_data.password
            )
            logger.info("用户认证成功: %s (ID: %s)", user.email, user.id)
            invite_service.bind_workspace_with_invite(
                db=db,
                user=user,
                invite_token=form_data.invite,
                workspace_id=invite_info.workspace_id,
            )
        except BusinessException as exc:
            # 用户不存在且有邀请码，尝试注册
            if exc.code == BizCode.USER_NOT_FOUND:
                logger.info("用户不存在，使用邀请码注册: %s", form_data.email)
                user = invite_service.register_user_with_invite(
                    db=db,
                    email=form_data.email,
                    username=form_data.username,
                    password=form_data.password,
                    invite_token=form_data.invite,
                    workspace_id=invite_info.workspace_id,
                )
            elif exc.code == BizCode.PASSWORD_ERROR:
                # 用户存在但密码错误
                logger.warning("接受邀请失败，密码验证错误: %s", form_data.email)
                tenant_id, reason_class = auth_service.login_failure_context(
                    db, form_data.email, exc.code
                )
                report_login_failure(
                    principal=form_data.email,
                    auth_surface="password",
                    reason_class=reason_class,
                    tenant_id=tenant_id,
                )
                raise BusinessException(
                    t("auth.invite.password_verification_failed"), BizCode.LOGIN_FAILED
                ) from exc
            else:
                # 其他认证失败情况，直接抛出
                raise
    else:
        try:
            user = auth_service.authenticate_user_or_raise(
                db, form_data.email, form_data.password
            )
            logger.info("用户认证成功: %s (ID: %s)", user.email, user.id)
        except BusinessException as exc:
            tenant_id, reason_class = auth_service.login_failure_context(
                db, form_data.email, exc.code
            )
            report_login_failure(
                principal=form_data.email,
                auth_surface="password",
                reason_class=reason_class,
                tenant_id=tenant_id,
            )
            # 其他认证失败情况，直接抛出（原 code/context 被 LOGIN_FAILED 覆盖）
            raise BusinessException(exc.message, BizCode.LOGIN_FAILED) from exc

    access_token, access_token_id = security.create_access_token(subject=user.id)
    refresh_token, _ = security.create_refresh_token(subject=user.id)

    access_expires_at = utcnow() + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    refresh_expires_at = utcnow() + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

    # 单点登录会话管理：键位用 str(user.id)，与老单体一致
    if settings.ENABLE_SINGLE_SESSION:
        SessionService.invalidate_old_session(str(user.id), access_token_id)
        SessionService.set_user_active_session(
            str(user.id), access_token_id, access_expires_at
        )

    user_service.update_last_login_time(db, user.id)

    logger.info("用户 %s 登录成功", user.username)

    # 决策 #11 修订：登录成功即发通知，identity 重建/刷新快照（新用户首个请求可用）
    notify_user_sync(str(user.id))

    return success(
        data=Token(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        ),
        msg=t("auth.login.success"),
    )


@router.post("/refresh", response_model=ApiResponse)
def refresh_token(
    refresh_request: RefreshTokenRequest,
    db: Session = Depends(get_db),
    t: Callable = Depends(get_translator),
):
    """刷新token"""
    logger.info("收到token刷新请求")

    userId = security.verify_token(refresh_request.refresh_token, "refresh")
    if not userId:
        raise BusinessException(
            t("auth.token.invalid_refresh_token"), code=BizCode.TOKEN_INVALID
        )

    user = auth_service.get_user_by_id(db, userId)
    if not user:
        raise BusinessException(t("auth.user.not_found"), code=BizCode.USER_NO_ACCESS)

    if settings.ENABLE_SINGLE_SESSION:
        refresh_token_id = security.get_token_id(refresh_request.refresh_token)
        if refresh_token_id and SessionService.is_token_blacklisted(refresh_token_id):
            raise BusinessException(
                t("auth.token.refresh_token_blacklisted"),
                code=BizCode.TOKEN_BLACKLISTED,
            )

    new_access_token, new_access_token_id = security.create_access_token(
        subject=user.id
    )
    new_refresh_token, _ = security.create_refresh_token(subject=user.id)

    access_expires_at = utcnow() + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    refresh_expires_at = utcnow() + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

    if settings.ENABLE_SINGLE_SESSION:
        old_refresh_token_id = security.get_token_id(refresh_request.refresh_token)
        if old_refresh_token_id:
            SessionService.blacklist_token(old_refresh_token_id)

        SessionService.invalidate_old_session(str(user.id), new_access_token_id)
        SessionService.set_user_active_session(
            str(user.id), new_access_token_id, access_expires_at
        )

    logger.info("用户 %s token刷新成功", user.id)

    # 决策 #11 修订：refresh 成功即发通知（会话滑动事件，覆盖快照已过期的回归用户）
    notify_user_sync(str(user.id))

    return success(
        data=Token(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
            expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        ),
        msg=t("auth.token.refresh_success"),
    )


@router.post("/logout", response_model=ApiResponse)
def logout(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    t: Callable = Depends(get_translator),
):
    """登出当前用户：加入token黑名单并清理会话"""
    logger.info("用户 %s 请求登出", current_user.username)

    token_id = security.get_token_id(token)
    if not token_id:
        raise BusinessException(t("auth.token.invalid"), code=BizCode.TOKEN_INVALID)

    SessionService.blacklist_token(token_id)

    # 会话清理按用户名（老单体既有键位；见 Part 4 待确认表）
    if settings.ENABLE_SINGLE_SESSION:
        SessionService.clear_user_session(current_user.username)

    logger.info("用户 %s 登出成功", current_user.username)
    return success(msg=t("auth.logout.success"))
