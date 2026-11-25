"""User lifecycle operations (management plane, sync).

Task 10 seeds the two functions the login path needs; Task 11 appends the
remaining lifecycle operations; Task 14 adds the first-superuser bootstrap.
Commit, rollback and invalidation-notify timing mirror
``core/api/app/services/user_service.py`` exactly.
"""

from __future__ import annotations

import json
import logging
import secrets
import string
import uuid

from error_core.codes import BizCode
from sqlalchemy.orm import Session

from src.config import settings
from src.core.datetime_utils import utcnow_naive
from src.core.exceptions import BusinessException, PermissionDeniedException
from src.core.permissions import Action, Resource, Subject, permission_service
from src.core.security import get_password_hash, verify_password
from src.i18n import translate
from src.invalidation_notify import notify_user_sync
from src.models.user_model import User
from src.models.workspace_model import Workspace
from src.redis import get_thread_safe_sync_redis
from src.repositories import management
from src.schemas.user_schema import UserCreate
from src.services.email_service import send_email
from src.services.session_service import SessionService

logger = logging.getLogger(__name__)


def create_user(db: Session, user: UserCreate, workspace: Workspace) -> User:
    logger.info("创建用户: %s, email: %s", user.username, user.email)

    try:
        if management.get_user_by_email(db, user.email):
            logger.warning("邮箱已注册: %s", user.email)
            raise BusinessException(
                "邮箱已注册",
                code=BizCode.USER_EMAIL_ALREADY_EXISTS,
                context={"email": user.email, "username": user.username},
            )

        new_user = management.create_user_row(
            db,
            username=user.username,
            email=user.email,
            hashed_password=get_password_hash(user.password),
            tenant_id=workspace.tenant_id,
            is_superuser=False,
        )

        db.commit()
        db.refresh(new_user)
        logger.info("用户创建成功: %s (ID: %s)", new_user.username, new_user.id)
        notify_user_sync(str(new_user.id))
        return new_user
    except BusinessException:
        db.rollback()
        raise
    except Exception as exc:
        logger.error("用户创建失败: %s - %s", user.username, exc)
        db.rollback()
        raise BusinessException(
            f"用户创建失败: {user.username} - {exc}",
            code=BizCode.DB_ERROR,
            context={"username": user.username, "email": user.email},
            cause=exc,
        ) from exc


def update_last_login_time(db: Session, user_id: uuid.UUID) -> User:
    """更新用户的最后登录时间"""
    logger.info("更新用户最后登录时间: user_id=%s", user_id)

    db_user = management.get_user_by_id(db, user_id)
    if not db_user:
        logger.warning("用户不存在: %s", user_id)
        raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)

    db_user.last_login_at = utcnow_naive()
    db.commit()
    db.refresh(db_user)

    logger.info("用户最后登录时间更新成功: %s (ID: %s)", db_user.username, user_id)
    return db_user


def create_superuser(db: Session, user: UserCreate, current_user: User) -> User:
    logger.info("创建超级管理员: %s, email: %s", user.username, user.email)

    subject = Subject.from_user(current_user)
    try:
        permission_service.check_superuser(
            subject,
            error_message="只有超级管理员才能创建超级管理员用户",
        )
    except PermissionDeniedException as exc:
        logger.warning("非超级管理员尝试创建超级管理员用户: %s", user.username)
        raise BusinessException(
            str(exc),
            code=BizCode.FORBIDDEN,
            context={
                "current_user_id": str(current_user.id),
                "current_user_username": current_user.username,
                "target_username": user.username,
            },
        ) from exc

    try:
        if management.get_user_by_email(db, user.email):
            logger.warning("邮箱已注册: %s", user.email)
            raise BusinessException(
                "邮箱已注册",
                code=BizCode.USER_EMAIL_ALREADY_EXISTS,
                context={
                    "email": user.email,
                    "username": user.username,
                    "created_by": str(current_user.id),
                },
            )

        new_user = management.create_user_row(
            db,
            username=user.username,
            email=user.email,
            hashed_password=get_password_hash(user.password),
            tenant_id=current_user.tenant_id,
            is_superuser=True,
        )

        db.commit()
        db.refresh(new_user)
        logger.info(
            "超级管理员创建成功: %s (ID: %s), 已加入租户: %s",
            new_user.username,
            new_user.id,
            current_user.tenant_id,
        )
        return new_user
    except BusinessException:
        db.rollback()
        raise
    except Exception as exc:
        logger.error("超级管理员创建失败: %s - %s", user.username, exc)
        db.rollback()
        raise BusinessException(
            f"超级管理员创建失败: {user.username} - {exc}",
            code=BizCode.DB_ERROR,
            context={
                "username": user.username,
                "email": user.email,
                "created_by": str(current_user.id),
                "tenant_id": str(current_user.tenant_id),
            },
            cause=exc,
        ) from exc


def deactivate_user(
    db: Session, user_id_to_deactivate: uuid.UUID, current_user: User
) -> User:
    logger.info(
        "停用用户: user_id=%s, 操作者: %s",
        user_id_to_deactivate,
        current_user.username,
    )

    try:
        db_user = management.get_user_by_id(db, user_id_to_deactivate)
        if not db_user:
            logger.warning("用户不存在: %s", user_id_to_deactivate)
            raise BusinessException(
                "用户不存在",
                code=BizCode.USER_NOT_FOUND,
                context={"user_id": str(user_id_to_deactivate)},
            )

        subject = Subject.from_user(current_user)
        resource = Resource.from_user(db_user)
        try:
            permission_service.require_permission(
                subject,
                Action.DEACTIVATE,
                resource,
                error_message="没有权限停用该用户",
            )
        except PermissionDeniedException as exc:
            logger.warning(
                "权限不足: 用户 %s 尝试停用用户 %s",
                current_user.username,
                user_id_to_deactivate,
            )
            raise BusinessException(
                str(exc),
                code=BizCode.FORBIDDEN,
                context={
                    "current_user_id": str(current_user.id),
                    "current_user_username": current_user.username,
                    "target_user_id": str(user_id_to_deactivate),
                },
            ) from exc

        if db_user.is_superuser and management.check_superuser_only(db):
            logger.warning(
                "停用超级管理员用户: %s (ID: %s)",
                db_user.username,
                user_id_to_deactivate,
            )
            raise BusinessException(
                "不能停用唯一的超级管理员用户",
                code=BizCode.FORBIDDEN,
                context={
                    "user_id": str(user_id_to_deactivate),
                    "username": db_user.username,
                },
            )

        tenant = management.get_tenant_by_id(db, db_user.tenant_id)
        if tenant and tenant.contact_email and tenant.contact_email == db_user.email:
            logger.warning(
                "尝试停用租户联系人: %s, tenant_id=%s",
                db_user.email,
                db_user.tenant_id,
            )
            raise BusinessException(
                "该管理员是租户联系人，请先在租户信息中更换联系邮箱，再禁用此管理员",
                code=BizCode.FORBIDDEN,
                context={
                    "user_id": str(user_id_to_deactivate),
                    "tenant_id": str(db_user.tenant_id),
                },
            )

        db_user.is_active = False
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        logger.info(
            "用户停用成功: %s (ID: %s)", db_user.username, user_id_to_deactivate
        )
        notify_user_sync(str(user_id_to_deactivate))
        return db_user
    except Exception as exc:
        logger.error("用户停用失败: user_id=%s - %s", user_id_to_deactivate, exc)
        db.rollback()
        if isinstance(exc, BusinessException):
            raise
        raise BusinessException(f"{exc}", code=BizCode.DB_ERROR) from exc


def activate_user(
    db: Session, user_id_to_activate: uuid.UUID, current_user: User
) -> User:
    logger.info(
        "激活用户: user_id=%s, 操作者: %s", user_id_to_activate, current_user.username
    )

    try:
        db_user = management.get_user_by_id_regardless_active(db, user_id_to_activate)
        if not db_user:
            logger.warning("用户不存在: %s", user_id_to_activate)
            raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)

        subject = Subject.from_user(current_user)
        resource = Resource.from_user(db_user)
        try:
            permission_service.require_permission(
                subject,
                Action.ACTIVATE,
                resource,
                error_message="没有权限激活该用户",
            )
        except PermissionDeniedException as exc:
            logger.warning(
                "权限不足: 用户 %s 尝试激活用户 %s",
                current_user.username,
                user_id_to_activate,
            )
            raise BusinessException(str(exc), code=BizCode.FORBIDDEN) from exc

        db_user.is_active = True
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        logger.info(
            "用户激活成功: %s (ID: %s)", db_user.username, user_id_to_activate
        )
        notify_user_sync(str(user_id_to_activate))
        return db_user
    except BusinessException:
        db.rollback()
        raise
    except Exception as exc:
        logger.error("用户激活失败: user_id=%s - %s", user_id_to_activate, exc)
        db.rollback()
        raise BusinessException(
            f"用户激活失败: user_id={user_id_to_activate} - {exc}",
            code=BizCode.DB_ERROR,
        ) from exc


def get_user(db: Session, user_id: uuid.UUID, current_user: User) -> User:
    logger.info("获取用户信息: user_id=%s, 操作者: %s", user_id, current_user.username)

    try:
        db_user = management.get_user_by_id(db, user_id)
        if not db_user:
            logger.warning("用户不存在: %s", user_id)
            raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)

        subject = Subject.from_user(current_user)
        resource = Resource.from_user(db_user)
        try:
            permission_service.require_permission(
                subject,
                Action.READ,
                resource,
                error_message="没有权限获取该用户信息",
            )
        except PermissionDeniedException as exc:
            logger.warning(
                "权限不足: 用户 %s 尝试获取用户 %s 信息", current_user.username, user_id
            )
            raise BusinessException(str(exc), code=BizCode.FORBIDDEN) from exc

        return db_user
    except BusinessException:
        raise
    except Exception as exc:
        logger.error("获取用户信息失败: user_id=%s - %s", user_id, exc)
        raise BusinessException(
            f"获取用户信息失败: user_id={user_id} - {exc}", code=BizCode.DB_ERROR
        ) from exc


def get_tenant_superusers(
    db: Session, current_user: User, include_inactive: bool = True
) -> list[User]:
    logger.info(
        "获取租户超管列表: tenant_id=%s, 请求者: %s, include_inactive=%s",
        current_user.tenant_id,
        current_user.username,
        include_inactive,
    )

    try:
        subject = Subject.from_user(current_user)
        try:
            permission_service.check_superuser(
                subject,
                error_message="只有超级管理员才能查看超管列表",
            )
        except PermissionDeniedException as exc:
            logger.warning("非超级管理员尝试查看超管列表: %s", current_user.username)
            raise BusinessException(str(exc), code=BizCode.FORBIDDEN) from exc

        if not current_user.tenant_id:
            logger.warning("用户没有租户信息: %s", current_user.username)
            raise BusinessException("用户没有租户信息", code=BizCode.TENANT_NOT_FOUND)

        is_active_filter = None if include_inactive else True
        superusers = management.get_superusers_by_tenant(
            db, current_user.tenant_id, is_active_filter
        )
        logger.info(
            "租户超管查询成功: tenant_id=%s, count=%s",
            current_user.tenant_id,
            len(superusers),
        )
        return superusers
    except BusinessException:
        raise
    except Exception as exc:
        logger.error(
            "获取租户超管列表失败: tenant_id=%s - %s", current_user.tenant_id, exc
        )
        raise BusinessException(
            f"获取租户超管列表失败: tenant_id={current_user.tenant_id} - {exc}",
            code=BizCode.DB_ERROR,
        ) from exc


def change_password(
    db: Session,
    user_id: uuid.UUID,
    old_password: str,
    new_password: str,
    current_user: User,
) -> User:
    logger.info(
        "用户修改密码请求: user_id=%s, current_user=%s", user_id, current_user.id
    )

    if current_user.id != user_id:
        logger.warning(
            "用户尝试修改他人密码: current_user=%s, target_user=%s",
            current_user.id,
            user_id,
        )
        raise PermissionDeniedException(translate("auth.password.change_failed"))

    try:
        db_user = management.get_user_by_id(db, user_id)
        if not db_user:
            logger.warning("用户不存在: %s", user_id)
            raise BusinessException(
                translate("auth.user.not_found"), code=BizCode.USER_NOT_FOUND
            )

        if not verify_password(old_password, db_user.hashed_password):
            logger.warning("用户旧密码验证失败: %s", user_id)
            raise BusinessException(
                translate("auth.password.incorrect"),
                code=BizCode.VALIDATION_FAILED,
            )

        db_user.hashed_password = get_password_hash(new_password)
        db.commit()
        db.refresh(db_user)

        SessionService.invalidate_all_user_tokens(str(user_id))
        logger.info("用户密码修改成功: %s (ID: %s)", db_user.username, user_id)
        notify_user_sync(str(user_id))
        return db_user
    except BusinessException:
        db.rollback()
        raise
    except Exception as exc:
        logger.error("修改用户密码失败: user_id=%s - %s", user_id, exc)
        db.rollback()
        raise BusinessException(
            translate("auth.password.change_failed"), code=BizCode.DB_ERROR
        ) from exc


def admin_change_password(
    db: Session,
    target_user_id: uuid.UUID,
    new_password: str | None = None,
    *,
    current_user: User,
) -> tuple[User, str]:
    logger.info(
        "管理员修改用户密码请求: admin=%s, target_user=%s",
        current_user.id,
        target_user_id,
    )

    subject = Subject.from_user(current_user)
    try:
        permission_service.check_superuser(
            subject, error_message=translate("auth.password.change_failed")
        )
    except PermissionDeniedException as exc:
        logger.warning("非超管用户尝试修改他人密码: current_user=%s", current_user.id)
        raise BusinessException(str(exc), code=BizCode.FORBIDDEN) from exc

    try:
        target_user = management.get_user_by_id(db, target_user_id)
        if not target_user:
            logger.warning("目标用户不存在: %s", target_user_id)
            raise BusinessException(
                translate("auth.user.not_found"), code=BizCode.USER_NOT_FOUND
            )

        if current_user.tenant_id != target_user.tenant_id:
            logger.warning(
                "跨租户密码修改尝试: admin_tenant=%s, target_tenant=%s",
                current_user.tenant_id,
                target_user.tenant_id,
            )
            raise BusinessException(
                translate("auth.password.change_failed"), code=BizCode.FORBIDDEN
            )

        actual_password = new_password if new_password else generate_random_password()
        target_user.hashed_password = get_password_hash(actual_password)
        db.commit()
        db.refresh(target_user)

        SessionService.invalidate_all_user_tokens(str(target_user_id))
        logger.info(
            "管理员修改用户密码成功: admin=%s, target=%s (ID: %s)",
            current_user.username,
            target_user.username,
            target_user_id,
        )
        notify_user_sync(str(target_user_id))
        return target_user, actual_password
    except BusinessException:
        db.rollback()
        raise
    except Exception as exc:
        logger.error(
            "管理员修改用户密码失败: admin=%s, target_user=%s - %s",
            current_user.id,
            target_user_id,
            exc,
        )
        db.rollback()
        raise BusinessException(
            translate("auth.password.change_failed"), code=BizCode.DB_ERROR
        ) from exc


def generate_random_password(length: int = 12) -> str:
    """Random password containing all four character classes."""

    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    special_chars = "!@#$%^&*"

    password = [
        secrets.choice(lowercase),
        secrets.choice(uppercase),
        secrets.choice(digits),
        secrets.choice(special_chars),
    ]
    all_chars = lowercase + uppercase + digits + special_chars
    for _ in range(length - 4):
        password.append(secrets.choice(all_chars))
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def generate_email_code() -> str:
    """Six-digit numeric code."""

    return "".join(str(secrets.randbelow(10)) for _ in range(6))


def send_email_code_method(db: Session, email: str, user_id: uuid.UUID) -> None:
    logger.info("发送邮箱验证码: email=%s", email)

    redis_client = get_thread_safe_sync_redis()
    rate_limit_key = f"email_code_rate:{user_id}"
    if redis_client.get(rate_limit_key):
        raise BusinessException(
            "请稍后再试，验证码发送间隔为1分钟", code=BizCode.RATE_LIMITED
        )

    existing_user = management.get_user_by_email(db, email)
    if existing_user and existing_user.id != user_id:
        raise BusinessException("邮箱已被使用", code=BizCode.USER_EMAIL_ALREADY_EXISTS)
    if existing_user and existing_user.id == user_id:
        raise BusinessException("新邮箱与当前邮箱相同", code=BizCode.DUPLICATE_NAME)

    code = generate_email_code()
    cache_key = f"email_code:{user_id}:{email}"
    redis_client.set(cache_key, json.dumps(code), ex=300)

    send_email(
        email,
        "邮箱验证码",
        f"<p>您的验证码是：<strong>{code}</strong></p><p>验证码在5分钟内有效。</p>",
    )

    redis_client.set(rate_limit_key, "1", ex=60)
    logger.info("邮箱验证码已发送: %s", email)


def verify_and_change_email(
    db: Session, user_id: uuid.UUID, new_email: str, code: str
) -> User:
    logger.info("验证并修改邮箱: user_id=%s, new_email=%s", user_id, new_email)

    db_user = management.get_user_by_id(db, user_id)
    if not db_user:
        raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)

    cache_key = f"email_code:{user_id}:{new_email}"
    cached_code = get_thread_safe_sync_redis().get(cache_key)
    if not cached_code:
        raise BusinessException("验证码已过期", code=BizCode.VALIDATION_FAILED)
    if json.loads(cached_code) != code:
        raise BusinessException("验证码错误", code=BizCode.VALIDATION_FAILED)

    db_user.email = new_email
    db.commit()
    db.refresh(db_user)

    get_thread_safe_sync_redis().delete(cache_key)
    # Host leaves existing tokens valid after an email change; keep that wire
    # behaviour (pending confirmation is registered in the cutover runbook).
    logger.info("用户邮箱修改成功: %s, new_email=%s", db_user.username, new_email)
    notify_user_sync(str(user_id))
    return db_user


def get_user_language_preference(
    db: Session, user_id: uuid.UUID, current_user: User
) -> str:
    logger.info("获取用户语言偏好: user_id=%s", user_id)

    if current_user.id != user_id:
        raise PermissionDeniedException("只能获取自己的语言偏好")

    db_user = management.get_user_by_id(db, user_id)
    if not db_user:
        raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)

    language = db_user.preferred_language or "zh"
    logger.info("用户语言偏好: %s, language=%s", db_user.username, language)
    return language


def update_user_language_preference(
    db: Session, user_id: uuid.UUID, language: str, current_user: User
) -> User:
    logger.info("更新用户语言偏好: user_id=%s, language=%s", user_id, language)

    if current_user.id != user_id:
        raise PermissionDeniedException("只能修改自己的语言偏好")

    if language not in settings.I18N_SUPPORTED_LANGUAGES:
        supported = ", ".join(settings.I18N_SUPPORTED_LANGUAGES)
        raise BusinessException(
            f"不支持的语言代码: {language}。支持的语言: {supported}",
            code=BizCode.VALIDATION_FAILED,
        )

    db_user = management.get_user_by_id(db, user_id)
    if not db_user:
        raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)

    db_user.preferred_language = language
    db.commit()
    db.refresh(db_user)
    logger.info("用户语言偏好更新成功: %s, language=%s", db_user.username, language)
    return db_user


def _create_bootstrap_tenant(db: Session, name: str, description: str):
    """Host ``TenantService.create_tenant`` semantics for the setup path."""

    if management.get_tenant_by_name(db, name):
        raise BusinessException(
            f"租户名称 '{name}' 已存在", code=BizCode.DUPLICATE_NAME
        )

    try:
        tenant = management.create_tenant_row(
            db, name=name, description=description
        )
        logger.info("创建租户成功: %s (ID: %s)", tenant.name, tenant.id)
        return tenant
    except Exception as exc:
        logger.error("创建租户失败: %s", exc)
        raise BusinessException(
            f"创建租户失败: {exc}", code=BizCode.DB_ERROR
        ) from exc


def create_initial_superuser(db: Session) -> User | None:
    logger.info("检查并创建初始超级用户")

    if management.get_superuser(db):
        logger.info("超级用户已存在，跳过创建")
        return None

    username = settings.FIRST_SUPERUSER_USERNAME
    email = settings.FIRST_SUPERUSER_EMAIL
    try:
        logger.debug("开始创建初始租户")
        tenant = _create_bootstrap_tenant(
            db, f"{username}'s Tenant", f"Default tenant for {username}"
        )
        db.flush()
        logger.debug("开始创建初始超级用户")

        superuser = management.create_user_row(
            db,
            username=username,
            email=email,
            hashed_password=get_password_hash(settings.FIRST_SUPERUSER_PASSWORD),
            tenant_id=tenant.id,
            is_superuser=True,
        )

        db.commit()
        db.refresh(superuser)
        logger.info("初始超级用户创建成功: %s (ID: %s)", superuser.username, superuser.id)
        return superuser
    except BusinessException:
        db.rollback()
        raise
    except Exception as exc:
        logger.error("初始超级用户创建失败: %s", exc)
        db.rollback()
        raise BusinessException(
            f"初始超级用户创建失败: {exc}",
            code=BizCode.DB_ERROR,
            context={"username": username, "email": email},
            cause=exc,
        ) from exc
