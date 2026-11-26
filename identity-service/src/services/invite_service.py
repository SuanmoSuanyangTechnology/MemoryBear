"""Workspace invitations on the authentication path (sync).

``validate``/``accept``/``register``/``bind`` are ported from the monolith's
``workspace_service`` and ``auth_service`` invite helpers. Task 12 wires
``validate_invite_token`` to the public route and reuses the same functions
for invite management.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from error_core.codes import BizCode
from sqlalchemy.orm import Session

from src.config import settings
from src.core.datetime_utils import utcnow_naive
from src.core.exceptions import BusinessException
from src.invalidation_notify import notify_user_sync
from src.models.user_model import User
from src.models.workspace_model import InviteStatus, WorkspaceRole
from src.repositories import management, workspace_repository
from src.schemas.user_schema import UserCreate
from src.schemas.workspace_schema import InviteAcceptRequest, InviteValidateResponse
from src.services import user_service

logger = logging.getLogger(__name__)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_invite_token(db: Session, token: str) -> InviteValidateResponse:
    """验证邀请令牌"""
    logger.info("验证邀请令牌")

    invite = workspace_repository.get_invite_by_token_hash(db, _hash_token(token))
    if not invite:
        logger.warning("邀请令牌无效")
        raise BusinessException("邀请令牌无效", BizCode.WORKSPACE_INVITE_NOT_FOUND)

    now = utcnow_naive()
    is_expired = invite.expires_at < now or invite.status != InviteStatus.pending
    is_valid = not is_expired

    workspace = management.get_workspace_by_id(db, invite.workspace_id)
    logger.info("邀请令牌验证完成: valid=%s, expired=%s", is_valid, is_expired)

    return InviteValidateResponse(
        workspace_name=workspace.name,
        workspace_id=invite.workspace_id,
        email=invite.email,
        role=WorkspaceRole(invite.role),
        is_expired=is_expired,
        is_valid=is_valid,
    )


def accept_workspace_invite(
    db: Session, accept_request: InviteAcceptRequest, user: User
) -> dict:
    """接受工作空间邀请"""
    logger.info("接受工作空间邀请: 用户 %s", user.username)

    try:
        invite = workspace_repository.get_invite_by_token_hash(
            db, _hash_token(accept_request.token)
        )
        if not invite:
            logger.warning("邀请令牌无效")
            raise BusinessException("邀请令牌无效", BizCode.WORKSPACE_INVITE_NOT_FOUND)

        if invite.status != InviteStatus.pending:
            logger.warning("邀请已被处理: status=%s", invite.status)
            raise BusinessException(f"邀请已被{invite.status}", BizCode.STATE_CONFLICT)

        now = utcnow_naive()
        if invite.expires_at < now:
            logger.warning("邀请已过期")
            workspace_repository.update_invite_status(db, invite.id, InviteStatus.expired)
            raise BusinessException("邀请已过期", BizCode.STATE_CONFLICT)

        if invite.email != user.email:
            logger.warning(
                "邮箱不匹配: invite_email=%s, user_email=%s", invite.email, user.email
            )
            raise BusinessException("邮箱与邀请邮箱不匹配", BizCode.FORBIDDEN)

        if settings.ENABLE_SINGLE_WORKSPACE:
            if workspace_repository.get_workspaces_by_user(db, user.id):
                logger.warning("单工作空间模式下用户已有工作空间: user=%s", user.username)
                raise BusinessException("用户只能加入一个工作空间", BizCode.FORBIDDEN)

        existing_member = management.get_member_in_workspace(
            db, user.id, invite.workspace_id
        )
        if existing_member:
            logger.info("用户已是工作空间成员，更新邀请状态")
            workspace_repository.update_invite_status(
                db, invite.id, InviteStatus.accepted, accepted_at=now
            )
            db.commit()
            workspace = management.get_workspace_by_id(db, invite.workspace_id)
            return {
                "message": "You are already a member of this workspace",
                "workspace": workspace,
            }

        workspace_role = invite.role
        workspace_repository.add_member_to_workspace(
            db, user_id=user.id, workspace_id=invite.workspace_id, role=workspace_role
        )
        workspace_repository.update_invite_status(
            db, invite.id, InviteStatus.accepted, accepted_at=now
        )
        db.commit()

        workspace = management.get_workspace_by_id(db, invite.workspace_id)
        logger.info(
            "用户成功加入工作空间: user=%s, workspace=%s, role=%s",
            user.username,
            workspace.name,
            workspace_role,
        )
        notify_user_sync(str(user.id))

        return {
            "message": "Successfully joined the workspace",
            "workspace": workspace,
            "role": workspace_role,
        }
    except Exception:
        db.rollback()
        logger.exception("接受工作空间邀请失败: user=%s", user.username)
        raise


def register_user_with_invite(
    db: Session,
    email: str,
    password: str,
    invite_token: str,
    workspace_id: uuid.UUID,
    username: str | None = None,
) -> User:
    """使用邀请码注册新用户并加入工作空间"""
    logger.info("使用邀请码注册用户: %s", email)

    try:
        user_create = UserCreate(
            email=email,
            password=password,
            username=email.split("@")[0] if not username else username,
        )
        workspace = management.get_workspace_by_id(db, workspace_id)
        user = user_service.create_user(db, user_create, workspace)
        logger.info("用户创建成功: %s (ID: %s)", user.email, user.id)

        accept_workspace_invite(db, InviteAcceptRequest(token=invite_token), user)
        logger.info("用户接受邀请成功")

        user = management.get_user_by_id(db, user.id)
        user.current_workspace_id = workspace_id
        db.commit()
        db.refresh(user)

        logger.info(
            "用户注册并加入工作空间成功: %s, workspace_id: %s",
            user.email,
            user.current_workspace_id,
        )
        return user
    except Exception:
        db.rollback()
        logger.exception("注册用户失败: %s", email)
        raise


def bind_workspace_with_invite(
    db: Session, user: User, invite_token: str, workspace_id: uuid.UUID
) -> User:
    """已存在用户凭邀请码加入工作空间并切换当前空间"""
    try:
        accept_workspace_invite(db, InviteAcceptRequest(token=invite_token), user)
        logger.info("用户接受邀请成功")

        user = management.get_user_by_id(db, user.id)
        user.current_workspace_id = workspace_id
        db.commit()
        db.refresh(user)
        return user
    except Exception:
        db.rollback()
        logger.exception("绑定工作空间失败: user=%s", user.email)
        raise
