"""Workspace management service (sync, host-compatible).

Port of the host ``workspace_service`` surface behind identity's management
plane. The host's policy chain collapses to local gates: any superuser passes
the member and admin gates (tenant is not checked, host parity), and denials
keep the host's ``FORBIDDEN: {message}`` shape.

The six workspace model slots are validated inside model-service; this side
renders the language-neutral issues with identity's own catalog.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from collections.abc import Iterable
from typing import Any

from error_core.codes import BizCode
from sqlalchemy.orm import Session

from src.core.cache import invalidate_cache_sync
from src.core.datetime_utils import utcnow_naive
from src.core.exceptions import BusinessException
from src.i18n import translate
from src.integrations import model_service
from src.invalidation_notify import notify_user_sync
from src.models.memory_config_model import MemoryConfig
from src.models.user_model import User
from src.models.workspace_model import (
    InviteStatus,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from src.repositories import end_user_repository, management, workspace_repository
from src.schemas.workspace_schema import (
    WorkspaceCreate,
    WorkspaceInviteCreate,
    WorkspaceInviteResponse,
    WorkspaceMemberUpdate,
    WorkspaceUpdate,
)
from src.services.session_service import SessionService

logger = logging.getLogger(__name__)

WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank", "vision", "audio", "video")
REQUIRED_WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank")


def _workspace_or_not_found(db: Session, workspace_id: uuid.UUID) -> Workspace:
    workspace = management.get_workspace_by_id(db, workspace_id)
    if workspace is None:
        logger.warning("工作空间不存在: %s", workspace_id)
        raise BusinessException("Workspace not found", code=BizCode.WORKSPACE_NOT_FOUND)
    return workspace


def _denied(message: str, code: BizCode) -> BusinessException:
    return BusinessException(f"FORBIDDEN: {message}", code=code)


def _member_of(
    db: Session, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> WorkspaceMember | None:
    return management.get_member_in_workspace(db, user_id, workspace_id)


def _require_member(
    db: Session,
    workspace_id: uuid.UUID,
    user: User,
    *,
    denied_message: str | None = None,
    denied_code: BizCode = BizCode.WORKSPACE_NO_ACCESS,
) -> Workspace:
    workspace = _workspace_or_not_found(db, workspace_id)
    if user.is_superuser:
        return workspace
    if _member_of(db, workspace_id, user.id) is None:
        logger.warning("权限不足: 用户 %s 尝试访问工作空间 %s", user.username, workspace_id)
        raise _denied(
            denied_message
            or f"用户 {user.username} 不是工作空间 {workspace_id} 的成员",
            denied_code,
        )
    return workspace


def _require_admin(db: Session, workspace_id: uuid.UUID, user: User) -> Workspace:
    workspace = _workspace_or_not_found(db, workspace_id)
    if user.is_superuser:
        return workspace
    member = _member_of(db, workspace_id, user.id)
    if member is None or member.role != WorkspaceRole.manager:
        logger.warning("权限不足: 用户 %s 尝试管理工作空间 %s", user.username, workspace_id)
        raise _denied(
            f"用户 {user.username} 没有管理工作空间 {workspace_id} 的权限",
            BizCode.WORKSPACE_ACCESS_DENIED,
        )
    return workspace


def require_workspace_admin(
    db: Session, workspace_id: uuid.UUID, user: User
) -> Workspace:
    """Admin gate for callers outside this module (host public helper)."""

    return _require_admin(db, workspace_id, user)


def _slot_label(slot: str, locale: str) -> str:
    key = f"workspace.models.slots.{slot}"
    label = translate(key, locale)
    return slot if label == key else label


def _display_model_id(model_id: Any) -> str:
    """Host keeps only the short id: full UUIDs get masked by the sensitive filter."""

    if not model_id:
        return "-"
    text = str(model_id)
    return text[:8] if len(text) > 8 else text


def _model_issue(
    slot: str,
    *,
    reason: str,
    locale: str,
    model_id: Any = None,
    model_name: str | None = None,
    provider: str | None = None,
    model_type: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    slot_label = _slot_label(slot, locale)
    display_name = model_name or (_display_model_id(model_id) if model_id else "-")
    message = translate(
        f"workspace.models.errors.{reason}",
        locale,
        slot=slot_label,
        model_id=_display_model_id(model_id),
        model_name=display_name,
        model_type=model_type or "-",
        error=detail or "-",
    )
    return {
        "slot": slot,
        # Legacy alias the frontend still uses to locate a model slot.
        "model_type": slot,
        "slot_label": slot_label,
        "model_id": str(model_id) if model_id else None,
        "model_name": model_name,
        "provider": provider,
        "reason": reason,
        "message": message,
        "detail": detail,
    }


def _issue_summary_message(issues: list[dict[str, Any]], locale: str) -> str:
    separator = "；" if str(locale).lower().startswith("zh") else "; "
    return translate(
        "workspace.models.errors.save_failed",
        locale,
        count=len(issues),
        reasons=separator.join(issue["message"] for issue in issues),
    )


def _raise_model_config_error(issues: list[dict[str, Any]], locale: str) -> None:
    raise BusinessException(
        _issue_summary_message(issues, locale),
        code=BizCode.INVALID_PARAMETER,
        context={"error_details": {"errors": issues}},
    )


def _render_model_issues(
    issues: list[dict[str, Any]], locale: str
) -> list[dict[str, Any]]:
    return [
        _model_issue(
            str(issue.get("slot") or ""),
            reason=str(issue.get("reason") or "verify_failed"),
            locale=locale,
            model_id=issue.get("model_id"),
            model_name=issue.get("model_name"),
            provider=issue.get("provider"),
            model_type=issue.get("model_type"),
            detail=issue.get("detail"),
        )
        for issue in issues
    ]


def _run_selection_gate(
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_name: str | None,
    selection: dict[str, str | None],
    static_check: bool,
    probe_slots: Iterable[str] = (),
    language: str = "zh",
) -> list[dict[str, Any]]:
    payload = model_service.validate_workspace_model_selection(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name=actor_name,
        selection=selection,
        static_check=static_check,
        probe_slots=probe_slots,
        language=language,
    )
    return _render_model_issues(payload.get("issues") or [], language)


def _extract_model_values(source: WorkspaceCreate) -> dict[str, str | None]:
    return {slot: getattr(source, slot, None) for slot in WORKSPACE_MODEL_SLOTS}


def _default_model_values(db: Session) -> dict[str, str]:
    preset = workspace_repository.get_default_model_preset(db)
    if preset is None:
        raise BusinessException("默认模型配置未设置", code=BizCode.CONFIG_MISSING)
    return {
        "llm": str(preset.llm_model_config_id),
        "embedding": str(preset.embedding_model_config_id),
        "rerank": str(preset.rerank_model_config_id),
        "vision": str(preset.vision_model_config_id),
        "audio": str(preset.audio_model_config_id),
        "video": str(preset.video_model_config_id),
    }


def _resolve_create_payload(
    db: Session,
    workspace: WorkspaceCreate,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_name: str | None,
    locale: str,
) -> WorkspaceCreate:
    if workspace.is_default_config:
        resolved = workspace.model_copy(update=_default_model_values(db))
        # The preset lookup above autobegins a read-only transaction; end it before
        # the model-service HTTP call so no pooled connection is held across the gate.
        db.rollback()
        issues = _run_selection_gate(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name=actor_name,
            selection=_extract_model_values(resolved),
            static_check=False,
            probe_slots=WORKSPACE_MODEL_SLOTS,
            language=locale,
        )
        if issues:
            _raise_model_config_error(issues, locale)
        return resolved

    # The duplicate-name check autobegins a read-only transaction; end it before the
    # model-service HTTP call (the repository writes below open a fresh transaction).
    db.rollback()
    issues = _run_selection_gate(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name=actor_name,
        selection=_extract_model_values(workspace),
        static_check=True,
        language=locale,
    )
    if issues:
        _raise_model_config_error(issues, locale)

    issues = _run_selection_gate(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name=actor_name,
        selection=_extract_model_values(workspace),
        static_check=False,
        probe_slots=REQUIRED_WORKSPACE_MODEL_SLOTS,
        language=locale,
    )
    if issues:
        _raise_model_config_error(issues, locale)
    return workspace


def _create_default_memory_config(db: Session, workspace: Workspace) -> uuid.UUID:
    config_id = uuid.uuid4()
    default_config = MemoryConfig(
        config_id=config_id,
        config_name=f"{workspace.name} 默认配置",
        config_desc="工作空间创建时自动生成的默认记忆配置",
        workspace_id=workspace.id,
        llm_id=str(workspace.llm) if workspace.llm else None,
        reflection_model_id=str(workspace.llm) if workspace.llm else None,
        embedding_id=str(workspace.embedding) if workspace.embedding else None,
        rerank_id=str(workspace.rerank) if workspace.rerank else None,
        vision_id=str(workspace.vision) if workspace.vision else None,
        audio_id=str(workspace.audio) if workspace.audio else None,
        video_id=str(workspace.video) if workspace.video else None,
        state=True,
        is_default=True,
    )
    db.add(default_config)
    db.flush()
    db.refresh(default_config)
    logger.info(
        "已创建工作空间默认记忆配置: workspace_id=%s, config_id=%s",
        workspace.id,
        config_id,
    )
    return config_id


def get_user_workspaces(db: Session, user: User) -> list[Workspace]:
    logger.debug("获取用户工作空间列表: %s (ID: %s)", user.username, user.id)
    workspaces = workspace_repository.get_workspaces_by_user(db, user.id)
    logger.info("用户 %s 的工作空间数量: %s", user.username, len(workspaces))
    return workspaces


def create_workspace(
    db: Session, workspace: WorkspaceCreate, user: User, language: str = "zh"
) -> Workspace:
    logger.info(
        "创建工作空间: %s, 创建者: %s, storage_type: %s",
        workspace.name,
        user.username,
        workspace.storage_type,
    )
    # Snapshot the gate identity before ending read transactions below: rollback
    # expires the request's ORM User, and a later attribute access would reload it
    # (re-opening a transaction around the model-service HTTP call).
    tenant_id = user.tenant_id
    actor_id = user.id
    actor_name = user.username

    if workspace_repository.get_workspaces_by_name(
        db, tenant_id=tenant_id, name=workspace.name
    ):
        raise BusinessException(
            "同名工作空间已存在", code=BizCode.RESOURCE_ALREADY_EXISTS
        )

    resolved = _resolve_create_payload(
        db,
        workspace,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name=actor_name,
        locale=language,
    )

    try:
        db_workspace = workspace_repository.create_workspace(
            db, workspace=resolved, tenant_id=tenant_id
        )
        logger.info(
            "工作空间创建成功: %s (ID: %s), 创建者: %s",
            db_workspace.name,
            db_workspace.id,
            actor_name,
        )
        db.flush()
        db.refresh(db_workspace)

        config_id = _create_default_memory_config(db, db_workspace)
        db_workspace.memory_config = config_id
        db.flush()
        db.refresh(db_workspace)
        db.commit()
        logger.info("工作空间 %s 及相关资源创建完成并已提交", db_workspace.id)
        return db_workspace
    except Exception as exc:
        logger.error("工作空间创建失败: %s - %s", workspace.name, exc)
        db.rollback()
        raise


def _try_invalidate(prefix: str) -> None:
    try:
        invalidate_cache_sync(prefix=prefix)
    except Exception as exc:  # host swallows cache invalidation failures
        logger.warning("缓存失效失败: prefix=%s - %s", prefix, exc)


def update_workspace(
    db: Session,
    workspace_id: uuid.UUID,
    workspace_in: WorkspaceUpdate,
    user: User,
) -> Workspace:
    logger.info("更新工作空间: workspace_id=%s, 操作者=%s", workspace_id, user.username)

    db_workspace = _require_admin(db, workspace_id, user)
    try:
        update_data = workspace_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_workspace, field, value)

        db.add(db_workspace)
        db.commit()
        db.refresh(db_workspace)

        if "storage_type" in update_data:
            _try_invalidate(f"storage_type:{workspace_id}")

        if (
            any(field in update_data for field in ("llm", "embedding", "rerank"))
            and db_workspace.memory_config
        ):
            _try_invalidate(f"memory_config:{db_workspace.memory_config}")

        logger.info("工作空间更新成功: %s (ID: %s)", db_workspace.name, workspace_id)
        return db_workspace
    except Exception as exc:
        logger.error("工作空间更新失败: workspace_id=%s - %s", workspace_id, exc)
        db.rollback()
        raise


def get_workspace_retention_policy(
    db: Session, workspace_id: uuid.UUID, user: User
) -> tuple[int | None, int]:
    _require_member(db, workspace_id, user)
    retention_days = workspace_repository.get_workspace_retention_days(db, workspace_id)
    end_user_count = end_user_repository.get_temporary_end_users_count_by_workspace(
        db, workspace_id
    )
    return retention_days, end_user_count


def update_workspace_retention_policy(
    db: Session,
    workspace_id: uuid.UUID,
    retention_days: int | None,
    user: User,
) -> int | None:
    logger.info(
        "更新工作空间保留策略: workspace_id=%s, retention_days=%s, 操作者=%s",
        workspace_id,
        retention_days,
        user.username,
    )
    _require_member(db, workspace_id, user)
    try:
        updated_retention_days = workspace_repository.update_workspace_retention_days(
            db, workspace_id, retention_days
        )
        logger.info(
            "工作空间保留策略更新成功: workspace_id=%s, retention_days=%s",
            workspace_id,
            updated_retention_days,
        )
        return updated_retention_days
    except Exception as exc:
        logger.error(
            "工作空间保留策略更新失败: workspace_id=%s - %s", workspace_id, exc
        )
        raise


def get_workspace_members(
    db: Session, workspace_id: uuid.UUID, user: User
) -> list[tuple[WorkspaceMember, User]]:
    logger.info("获取工作空间成员: workspace_id=%s, 操作者=%s", workspace_id, user.username)

    _workspace_or_not_found(db, workspace_id)
    if not user.is_superuser and _member_of(db, workspace_id, user.id) is None:
        logger.warning(
            "权限不足: 用户 %s 尝试获取工作空间 %s 成员列表", user.username, workspace_id
        )
        raise _denied(
            f"用户 {user.username} 没有查看工作空间 {workspace_id} 成员列表的权限",
            BizCode.WORKSPACE_ACCESS_DENIED,
        )

    members = workspace_repository.get_members_by_workspace(db, workspace_id)
    logger.info("工作空间成员数量: %s - workspace_id=%s", len(members), workspace_id)
    return members


def update_workspace_member_roles(
    db: Session,
    workspace_id: uuid.UUID,
    updates: list[WorkspaceMemberUpdate],
    user: User,
) -> list[tuple[WorkspaceMember, User]]:
    logger.info(
        "更新工作空间成员角色: workspace_id=%s, 操作者=%s, 更新数量=%s",
        workspace_id,
        user.username,
        len(updates),
    )
    _require_admin(db, workspace_id, user)

    pairs = workspace_repository.get_members_by_workspace(db, workspace_id)
    all_members = [member for member, _user in pairs]
    member_map = {member.id: member for member in all_members}

    update_ids = set()
    for upd in updates:
        if upd.id not in member_map:
            raise BusinessException(
                f"成员 {upd.id} 不存在于工作空间 {workspace_id}",
                code=BizCode.NOT_FOUND,
            )

        member = member_map[upd.id]
        if member.workspace_id != workspace_id:
            raise BusinessException(
                f"成员 {upd.id} 不属于工作空间 {workspace_id}",
                code=BizCode.NOT_FOUND,
            )
        if member.user_id == user.id:
            raise BusinessException("不能修改自己的角色", code=BizCode.BAD_REQUEST)
        update_ids.add(upd.id)

    managers_after_update = [
        member
        for member in all_members
        if member.id not in update_ids and member.role == WorkspaceRole.manager
    ]
    for upd in updates:
        if upd.role == WorkspaceRole.manager:
            managers_after_update.append(member_map[upd.id])

    if len(managers_after_update) == 0:
        raise BusinessException("工作空间至少需要一个管理员", code=BizCode.BAD_REQUEST)

    try:
        for upd in updates:
            workspace_repository.update_member_role_by_id(db, upd.id, upd.role)

        db.commit()

        updated_pairs = workspace_repository.get_members_by_workspace(db, workspace_id)
        logger.info(
            "成员角色更新完成: workspace_id=%s, 更新数量=%s",
            workspace_id,
            len(updates),
        )
        for upd in updates:
            notify_user_sync(str(member_map[upd.id].user_id))
        return updated_pairs
    except Exception as exc:
        db.rollback()
        logger.error(
            "更新工作空间成员角色失败: workspace_id=%s - %s", workspace_id, exc
        )
        raise BusinessException(
            f"更新成员角色失败: {exc}", code=BizCode.INTERNAL_ERROR
        ) from exc


def delete_workspace_member(
    db: Session, workspace_id: uuid.UUID, member_id: uuid.UUID, user: User
) -> None:
    logger.info(
        "用户 %s 请求删除工作空间 %s 的成员 %s", user.username, workspace_id, member_id
    )
    _require_admin(db, workspace_id, user)
    pair = workspace_repository.get_member_by_id(db, member_id)
    if pair is None:
        raise BusinessException(
            f"工作空间成员 {member_id} 不存在", code=BizCode.WORKSPACE_NOT_FOUND
        )
    workspace_member, deleted_user = pair
    if workspace_member.workspace_id != workspace_id:
        raise BusinessException(
            f"工作空间成员 {member_id} 不存在于工作空间 {workspace_id}",
            code=BizCode.WORKSPACE_NOT_FOUND,
        )

    try:
        workspace_member.is_active = False
        deleted_user.current_workspace_id = None

        if not deleted_user.is_superuser:
            remaining = workspace_repository.count_user_active_memberships_excluding(
                db, deleted_user.id, workspace_id
            )
            if remaining == 0:
                deleted_user.is_active = False

        db.commit()
        logger.info(
            "用户 %s 成功删除工作空间 %s 的成员 %s",
            user.username,
            workspace_id,
            member_id,
        )

        SessionService.invalidate_all_user_tokens(str(workspace_member.user_id))
        notify_user_sync(str(workspace_member.user_id))
    except Exception as exc:
        db.rollback()
        logger.error(
            "删除工作空间成员失败 - 工作空间: %s, 成员: %s, 错误: %s",
            workspace_id,
            member_id,
            exc,
        )
        raise BusinessException(
            f"删除工作空间成员失败: {exc}", code=BizCode.INTERNAL_ERROR
        ) from exc


def _generate_invite_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def create_workspace_invite(
    db: Session,
    workspace_id: uuid.UUID,
    invite_data: WorkspaceInviteCreate,
    user: User,
) -> WorkspaceInviteResponse:
    logger.info(
        "创建工作空间邀请: workspace_id=%s, email=%s, 创建者=%s",
        workspace_id,
        invite_data.email,
        user.username,
    )

    try:
        _require_admin(db, workspace_id, user)

        invited_user = management.get_user_by_email(db, invite_data.email)
        if invited_user and _member_of(db, workspace_id, invited_user.id):
            logger.warning("用户 %s 已经是工作空间成员", invite_data.email)
            raise BusinessException(
                "该用户已经是工作空间成员", code=BizCode.RESOURCE_ALREADY_EXISTS
            )

        existing_invite = (
            workspace_repository.get_pending_invite_by_email_and_workspace(
                db, invite_data.email, workspace_id
            )
        )
        token, token_hash = _generate_invite_token()
        if existing_invite:
            logger.info(
                "邮箱 %s 在工作空间 %s 已有待处理邀请，返回现有邀请",
                invite_data.email,
                workspace_id,
            )
            existing_invite.token_hash = token_hash
            existing_invite.updated_at = utcnow_naive()
            db.commit()
            db.refresh(existing_invite)
            invite_obj = existing_invite
        else:
            invite_obj = workspace_repository.create_invite(
                db,
                workspace_id=workspace_id,
                invite_data=invite_data,
                token_hash=token_hash,
                created_by_user_id=user.id,
            )
            db.commit()
            db.refresh(invite_obj)

        logger.info(
            "工作空间邀请创建成功: invite_id=%s, email=%s",
            invite_obj.id,
            invite_data.email,
        )
        response = WorkspaceInviteResponse.model_validate(invite_obj)
        response.invite_token = token
        return response
    except Exception as exc:
        db.rollback()
        logger.error(
            "创建工作空间邀请失败: workspace_id=%s, email=%s - %s",
            workspace_id,
            invite_data.email,
            exc,
        )
        raise


def get_workspace_invites(
    db: Session,
    workspace_id: uuid.UUID,
    user: User,
    *,
    status: InviteStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[WorkspaceInviteResponse]:
    logger.info(
        "获取工作空间邀请列表: workspace_id=%s, 操作者=%s", workspace_id, user.username
    )
    if management.get_workspace_by_id(db, workspace_id) is None:
        raise BusinessException("工作空间不存在", code=BizCode.WORKSPACE_NOT_FOUND)

    _require_admin(db, workspace_id, user)
    invites = workspace_repository.get_workspace_invites(
        db, workspace_id, status=status, limit=limit, offset=offset
    )
    return [WorkspaceInviteResponse.model_validate(invite) for invite in invites]


def revoke_workspace_invite(
    db: Session, workspace_id: uuid.UUID, invite_id: uuid.UUID, user: User
) -> dict:
    logger.info(
        "撤销工作空间邀请: workspace_id=%s, invite_id=%s, 操作者=%s",
        workspace_id,
        invite_id,
        user.username,
    )

    try:
        _require_admin(db, workspace_id, user)

        invite = workspace_repository.revoke_invite(db, invite_id)
        if invite is None:
            logger.warning("邀请不存在: invite_id=%s", invite_id)
            raise BusinessException(
                "邀请不存在", code=BizCode.WORKSPACE_INVITE_NOT_FOUND
            )

        if invite.workspace_id != workspace_id:
            # Host quirk kept verbatim: the repository already committed the
            # revocation before this ownership check, so it persists regardless.
            logger.warning(
                "邀请不属于指定工作空间: invite_id=%s, workspace_id=%s",
                invite_id,
                workspace_id,
            )
            raise BusinessException("邀请不属于指定工作空间", code=BizCode.BAD_REQUEST)

        db.commit()
        logger.info("工作空间邀请撤销成功: invite_id=%s", invite_id)
        return {"message": "邀请撤销成功"}
    except Exception as exc:
        db.rollback()
        logger.error("撤销工作空间邀请失败: invite_id=%s - %s", invite_id, exc)
        raise


def switch_workspace(db: Session, workspace_id: uuid.UUID, user: User) -> None:
    logger.debug("用户 %s 请求切换工作空间为 %s", user.username, workspace_id)

    workspace = _require_member(db, workspace_id, user)

    # Superusers bypass tenant scope in the gate; reject the switch as
    # not-found so workspace existence does not leak across tenants.
    if workspace.tenant_id != user.tenant_id:
        logger.warning(
            "拒绝跨租户切换工作空间: user_tenant=%s, workspace_tenant=%s",
            user.tenant_id,
            workspace.tenant_id,
        )
        raise BusinessException("Workspace not found", code=BizCode.WORKSPACE_NOT_FOUND)

    try:
        user.current_workspace_id = workspace_id
        db.commit()
        notify_user_sync(str(user.id))
        logger.info("用户 %s 成功切换工作空间为 %s", user.username, workspace_id)
        return
    except Exception as exc:
        db.rollback()
        logger.error("切换工作空间失败 - 工作空间: %s, 错误: %s", workspace_id, exc)
        raise BusinessException(
            f"切换工作空间失败: {exc}", code=BizCode.INTERNAL_ERROR
        ) from exc


def get_workspace_storage_type(
    db: Session, workspace_id: uuid.UUID, user: User
) -> str | None:
    logger.info(
        "用户 %s 请求获取工作空间 %s 的存储类型", user.username, workspace_id
    )
    workspace = _require_member(db, workspace_id, user)
    logger.info("成功获取工作空间 %s 的存储类型: %s", workspace_id, workspace.storage_type)
    return workspace.storage_type


__all__ = [
    "REQUIRED_WORKSPACE_MODEL_SLOTS",
    "WORKSPACE_MODEL_SLOTS",
    "create_workspace",
    "create_workspace_invite",
    "delete_workspace_member",
    "get_user_workspaces",
    "get_workspace_invites",
    "get_workspace_members",
    "get_workspace_retention_policy",
    "get_workspace_storage_type",
    "require_workspace_admin",
    "revoke_workspace_invite",
    "switch_workspace",
    "update_workspace",
    "update_workspace_member_roles",
    "update_workspace_retention_policy",
]
