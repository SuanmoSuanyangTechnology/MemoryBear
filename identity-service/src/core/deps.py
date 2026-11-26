"""Management-plane auth dependencies (sync, host wire-compatible).

Migrated management routes resolve their principal through these helpers.
They are synchronous on purpose: the management plane uses the sync engine
(sync session for management, async for the runtime plane), so the routes are
plain ``def`` handlers. The host's async variants serve the SSE runtime path
and are intentionally not ported.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from functools import wraps

from error_core.codes import BizCode
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from src.config import settings
from src.core.datetime_utils import UTC
from src.core.exceptions import BusinessException
from src.core.security import get_token_id
from src.db import get_db, get_db_context
from src.models.tenant_model import Tenants
from src.models.user_model import User
from src.models.workspace_model import Workspace
from src.repositories import management
from src.services.session_service import SessionService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

_AUTH_CHALLENGE_HEADERS = {"WWW-Authenticate": "Bearer"}


def _auth_exception(message: str, code: BizCode) -> BusinessException:
    """401 carrying the bearer challenge, exactly as the host emitted it."""

    return BusinessException(
        message,
        code,
        context={"headers": dict(_AUTH_CHALLENGE_HEADERS)},
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise _auth_exception(
                "Could not validate credentials", BizCode.TOKEN_INVALID
            )
    except JWTError as exc:
        raise _auth_exception(
            "Could not validate credentials", BizCode.TOKEN_INVALID
        ) from exc

    try:
        token_id = get_token_id(token)
        if SessionService.is_token_blacklisted(token_id):
            raise _auth_exception("Token has been revoked", BizCode.TOKEN_BLACKLISTED)

        invalidation_time_str = SessionService.get_user_token_invalidation_time(user_id)
        if invalidation_time_str:
            invalidation_time = datetime.fromisoformat(invalidation_time_str)
            issued_at = payload.get("iat")
            token_issued_at = (
                datetime.fromtimestamp(issued_at, tz=UTC) if issued_at else None
            )
            if token_issued_at and token_issued_at < invalidation_time:
                raise _auth_exception(
                    "Token was issued before the credential reset",
                    BizCode.TOKEN_BLACKLISTED,
                )
    except (HTTPException, BusinessException):
        raise
    except Exception as exc:
        raise _auth_exception(
            "Could not validate credentials", BizCode.UNAUTHORIZED
        ) from exc

    try:
        user = management.get_user_by_id(db, user_id)
        if user is None:
            raise _auth_exception("User does not exist", BizCode.USER_NO_ACCESS)
        if not user.is_active:
            raise _auth_exception("User is inactive", BizCode.USER_NO_ACCESS)
        return user
    except BusinessException:
        raise
    except Exception as exc:
        raise _auth_exception(
            "Could not validate credentials", BizCode.UNAUTHORIZED
        ) from exc


def get_current_tenant(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Tenants:
    """The caller's tenant. The model carries no relationship, so query it."""

    try:
        tenant = (
            management.get_tenant_by_id(db, current_user.tenant_id)
            if current_user.tenant_id is not None
            else None
        )
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="用户没有关联的租户"
            )
        return tenant
    except HTTPException:
        raise
    except Exception as exc:
        raise BusinessException("获取租户信息失败", BizCode.INTERNAL_ERROR) from exc


def get_current_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_superuser:
        raise BusinessException("只有超级管理员才能执行此操作", BizCode.FORBIDDEN)
    return current_user


def _check_workspace_access_sync(
    db: Session, user: User, workspace_id: uuid.UUID
) -> Workspace:
    """Workspace access gate (behaviourally equal to the host policy chain)."""

    workspace = management.get_workspace_by_id(db, workspace_id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )

    if user.is_superuser:
        if user.tenant_id == workspace.tenant_id:
            return workspace
        raise BusinessException("Forbidden", BizCode.FORBIDDEN)

    member = management.get_member_in_workspace(
        db=db, user_id=user.id, workspace_id=workspace_id
    )
    if not member:
        raise BusinessException("Forbidden", BizCode.FORBIDDEN)
    return workspace


def workspace_access_guard(get_workspace_id_from_body: bool = False):
    """Decorator gate for endpoints taking ``workspace_id`` (or a body payload).

    The wrapped handler must accept ``db`` plus ``user`` or ``current_user``
    keyword arguments (FastAPI ``Depends`` supplies them at call time), which
    is how the host decorator found the same values in ``kwargs``.
    """

    def _decorator(func):
        @wraps(func)
        def _wrapper(*args, **kwargs):
            db: Session = kwargs.get("db")
            user: User = kwargs.get("user") or kwargs.get("current_user")

            if get_workspace_id_from_body:
                payload = kwargs.get("payload")
                if not payload or not hasattr(payload, "workspace_id"):
                    raise BusinessException(
                        "workspace_id missing in body", BizCode.MISSING_PARAMETER
                    )
                workspace_id = payload.workspace_id
            else:
                workspace_id = kwargs.get("workspace_id")
                if workspace_id is None:
                    raise BusinessException(
                        "workspace_id is required", BizCode.MISSING_PARAMETER
                    )

            _check_workspace_access_sync(db, user, workspace_id)
            return func(*args, **kwargs)

        return _wrapper

    return _decorator


def cur_workspace_access_guard():
    """Decorator gate for endpoints acting on the caller's current workspace."""

    def _decorator(func):
        @wraps(func)
        def _wrapper(*args, **kwargs):
            db: Session = kwargs.get("db")
            user: User = kwargs.get("current_user")
            workspace_id = user.current_workspace_id
            if workspace_id is None:
                raise BusinessException(
                    "workspace_id is required", BizCode.MISSING_PARAMETER
                )
            if db is not None:
                _check_workspace_access_sync(db, user, workspace_id)
            else:
                with get_db_context() as guard_db:
                    _check_workspace_access_sync(guard_db, user, workspace_id)
            return func(*args, **kwargs)

        return _wrapper

    return _decorator
