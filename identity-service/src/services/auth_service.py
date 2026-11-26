"""Password authentication for the management plane (sync).

Wire behaviour matches ``core/api/app/services/auth_service.py`` for the
subset the migrated routes need: the three failure codes/messages and the
login-failure classifier used by the alert bridge.
"""

from __future__ import annotations

import logging

from error_core.codes import BizCode
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.exceptions import BusinessException
from src.core.security import verify_password
from src.i18n import translate
from src.models.user_model import User
from src.repositories import management

logger = logging.getLogger(__name__)


def authenticate_user_or_raise(db: Session, email: str, password: str) -> User:
    user = management.get_user_by_email(db, email)
    if not user:
        logger.warning("用户不存在: %s", email)
        raise BusinessException(
            translate("auth.user.not_found"), code=BizCode.USER_NOT_FOUND
        )

    if not user.is_active:
        logger.warning("用户未激活: %s", email)
        raise BusinessException(
            translate("auth.login.account_disabled"), code=BizCode.USER_NOT_FOUND
        )

    if not verify_password(password, user.hashed_password):
        logger.warning("密码错误: %s", email)
        raise BusinessException(
            translate("auth.password.incorrect"), code=BizCode.PASSWORD_ERROR
        )

    logger.info("用户认证成功: %s", email)
    return user


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return management.get_user_by_id(db, user_id)


def login_failure_context(
    db: Session, email: str, error_code: BizCode
) -> tuple[object, str]:
    """租户与稳定失败分类；结果不改变对外登录响应。

    查询异常被吞掉是刻意的：告警降级不得把登录响应变成 500。
    """

    default_reason = (
        "bad_credential" if error_code == BizCode.PASSWORD_ERROR else "unknown_principal"
    )
    try:
        row = db.execute(
            select(User.tenant_id, User.is_active).where(User.email == email)
        ).first()
        if row is None:
            return None, "unknown_principal"
        tenant_id, is_active = row
        return tenant_id, "disabled" if not is_active else default_reason
    except Exception:
        return None, default_reason
