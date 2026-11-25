"""Workspace management routes migrated from the monolith.

All handlers are synchronous: the management plane runs on the sync engine.
Declaration order mirrors ``core/api/app/controllers/workspace_controller.py``
so literal paths (``/temporary-memory-retention``, ``/members``, ``/invites``,
``/storage``) keep their positions. The public invite-validation route lives
on ``public_router`` and is declared between the invite list and revoke
handlers, as in the host file.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from src.alert_metric_bridge import report_quota_change_sync
from src.core.deps import (
    cur_workspace_access_guard,
    get_current_superuser,
    get_current_tenant,
    get_current_user,
    workspace_access_guard,
)
from src.core.language_utils import get_language_from_header
from src.core.response_utils import success
from src.db import get_db
from src.i18n import get_default_locale, get_translator
from src.models.tenant_model import Tenants
from src.models.user_model import User
from src.models.workspace_model import InviteStatus, WorkspaceMember
from src.schemas.response_schema import ApiResponse
from src.schemas.workspace_schema import (
    WorkspaceCreate,
    WorkspaceInviteCreate,
    WorkspaceMemberItem,
    WorkspaceMemberUpdate,
    WorkspaceResponse,
    WorkspaceRetentionPolicyResponse,
    WorkspaceRetentionPolicyUpdate,
    WorkspaceUpdate,
)
from src.serializers import (
    WorkspaceInviteSerializer,
    WorkspaceMemberSerializer,
    WorkspaceSerializer,
)
from src.services import invite_service, quota, workspace_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces",
    tags=["Workspaces"],
    dependencies=[Depends(get_current_user)],
)

public_router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


def get_request_language(request: Request) -> str:
    """Display locale resolved by the language middleware (host parity)."""

    return getattr(request.state, "language", None) or get_default_locale()


def _convert_members_to_table_items(
    members: list[tuple[WorkspaceMember, User]],
) -> list[WorkspaceMemberItem]:
    """Flatten (membership, user) pairs into the host table shape."""

    return [
        WorkspaceMemberItem(
            id=member.id,
            username=member_user.username,
            account=member_user.email,
            role=member.role,
            last_login_at=member_user.last_login_at,
        )
        for member, member_user in members
    ]


@router.get("", response_model=ApiResponse)
def get_workspaces(
    include_current: bool = Query(True, description="是否包含当前工作空间"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenants = Depends(get_current_tenant),
    language: str = Depends(get_request_language),
    t: Callable = Depends(get_translator),
):
    """获取当前租户下用户参与的所有工作空间"""
    logger.info(
        "用户 %s 在租户 %s 中请求获取工作空间列表, include_current=%s",
        current_user.username,
        current_tenant.name,
        include_current,
    )

    workspaces = workspace_service.get_user_workspaces(db, current_user)

    if not include_current and current_user.current_workspace_id:
        workspaces = [
            w for w in workspaces if w.id != current_user.current_workspace_id
        ]
        logger.debug("过滤掉当前工作空间: %s", current_user.current_workspace_id)

    logger.info("成功获取 %s 个工作空间", len(workspaces))

    serializer = WorkspaceSerializer()
    workspaces_data = [
        WorkspaceResponse.model_validate(w).model_dump() for w in workspaces
    ]
    workspaces_i18n = serializer.serialize_list(workspaces_data, language)
    return success(data=workspaces_i18n, msg=t("workspace.list_retrieved"))


@router.post("", response_model=ApiResponse)
def create_workspace(
    workspace: WorkspaceCreate,
    language_type: str = Header(default="zh", alias="X-Language-Type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
    t: Callable = Depends(get_translator),
):
    """创建新的工作空间"""
    quota.check_workspace_quota(db, current_user.tenant_id)

    language = get_language_from_header(language_type)
    logger.info(
        "用户 %s 请求创建工作空间: %s, language=%s",
        current_user.username,
        workspace.name,
        language,
    )

    result = workspace_service.create_workspace(
        db=db, workspace=workspace, user=current_user, language=language
    )
    logger.info(
        "工作空间创建成功 - 名称: %s, ID: %s, 创建者: %s, language=%s",
        workspace.name,
        result.id,
        current_user.username,
        language,
    )
    report_quota_change_sync(
        tenant_id=current_user.tenant_id, quota_type="workspace_quota"
    )

    serializer = WorkspaceSerializer()
    result_data = WorkspaceResponse.model_validate(result).model_dump()
    result_i18n = serializer.serialize(result_data, language)
    return success(data=result_i18n, msg=t("workspace.created"))


@router.put("", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_workspace(
    workspace: WorkspaceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_request_language),
    t: Callable = Depends(get_translator),
):
    """更新工作空间"""
    workspace_id = current_user.current_workspace_id
    logger.info("用户 %s 请求更新工作空间 ID: %s", current_user.username, workspace_id)

    result = workspace_service.update_workspace(
        db=db,
        workspace_id=workspace_id,
        workspace_in=workspace,
        user=current_user,
    )
    logger.info("工作空间更新成功 - ID: %s, 用户: %s", workspace_id, current_user.username)

    serializer = WorkspaceSerializer()
    result_data = WorkspaceResponse.model_validate(result).model_dump()
    result_i18n = serializer.serialize(result_data, language)
    return success(data=result_i18n, msg=t("workspace.updated"))


@router.get("/temporary-memory-retention", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_workspace_retention_policy(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """获取当前工作空间的临时身份保留策略。"""
    workspace_id = current_user.current_workspace_id
    logger.info(
        "用户 %s 请求获取工作空间 %s 的保留策略", current_user.username, workspace_id
    )
    retention_days, end_user_count = workspace_service.get_workspace_retention_policy(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
    )
    return success(
        data=WorkspaceRetentionPolicyResponse(
            retention_days=retention_days,
            end_user_count=end_user_count,
        ).model_dump(),
        msg=t("workspace.retention_policy.retrieved"),
    )


@router.put("/temporary-memory-retention", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_workspace_retention_policy(
    policy: WorkspaceRetentionPolicyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """更新当前工作空间的临时身份保留策略。"""
    workspace_id = current_user.current_workspace_id
    logger.info(
        "用户 %s 请求更新工作空间 %s 的保留策略", current_user.username, workspace_id
    )
    retention_days = workspace_service.update_workspace_retention_policy(
        db=db,
        workspace_id=workspace_id,
        retention_days=policy.retention_days,
        user=current_user,
    )
    return success(
        data=WorkspaceRetentionPolicyResponse(
            retention_days=retention_days,
        ).model_dump(exclude_unset=True),
        msg=t("workspace.retention_policy.updated"),
    )


@router.get("/members", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_cur_workspace_members(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_request_language),
    t: Callable = Depends(get_translator),
):
    """获取工作空间成员列表（关系序列化）"""
    logger.info(
        "用户 %s 请求获取工作空间 %s 的成员列表",
        current_user.username,
        current_user.current_workspace_id,
    )

    members = workspace_service.get_workspace_members(
        db=db,
        workspace_id=current_user.current_workspace_id,
        user=current_user,
    )
    logger.info(
        "工作空间成员列表获取成功 - ID: %s, 数量: %s",
        current_user.current_workspace_id,
        len(members),
    )

    table_items = _convert_members_to_table_items(members)
    serializer = WorkspaceMemberSerializer()
    members_data = [item.model_dump() for item in table_items]
    members_i18n = serializer.serialize_list(members_data, language)
    return success(data=members_i18n, msg=t("workspace.members.list_retrieved"))


@router.put("/members", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_workspace_members(
    updates: list[WorkspaceMemberUpdate],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """更新当前工作空间的成员角色"""
    workspace_id = current_user.current_workspace_id
    logger.info(
        "用户 %s 请求更新工作空间 %s 的成员角色", current_user.username, workspace_id
    )
    members = workspace_service.update_workspace_member_roles(
        db=db,
        workspace_id=workspace_id,
        updates=updates,
        user=current_user,
    )
    logger.info(
        "工作空间成员角色更新成功 - ID: %s, 数量: %s", workspace_id, len(members)
    )
    return success(msg=t("workspace.members.role_updated"))


@router.delete("/members/{member_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def delete_workspace_member(
    member_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """删除工作空间成员"""
    workspace_id = current_user.current_workspace_id
    logger.info(
        "用户 %s 请求删除工作空间 %s 的成员 %s",
        current_user.username,
        workspace_id,
        member_id,
    )

    workspace_service.delete_workspace_member(
        db=db,
        workspace_id=workspace_id,
        member_id=member_id,
        user=current_user,
    )
    logger.info(
        "工作空间成员删除成功 - ID: %s, 成员: %s", workspace_id, member_id
    )
    return success(msg=t("workspace.members.deleted"))


@router.post("/invites", response_model=ApiResponse)
@cur_workspace_access_guard()
def create_workspace_invite(
    invite_data: WorkspaceInviteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_request_language),
    t: Callable = Depends(get_translator),
):
    """创建工作空间邀请"""
    workspace_id = current_user.current_workspace_id
    logger.info(
        "用户 %s 请求为工作空间 %s 创建邀请: %s",
        current_user.username,
        workspace_id,
        invite_data.email,
    )

    result = workspace_service.create_workspace_invite(
        db=db,
        workspace_id=workspace_id,
        invite_data=invite_data,
        user=current_user,
    )
    logger.info(
        "工作空间邀请创建成功 - 工作空间: %s, 邮箱: %s",
        workspace_id,
        invite_data.email,
    )

    serializer = WorkspaceInviteSerializer()
    result_i18n = serializer.serialize(result, language)
    return success(data=result_i18n, msg=t("workspace.invites.created"))


@router.get("/invites", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_workspace_invites(
    status_filter: InviteStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_request_language),
    t: Callable = Depends(get_translator),
):
    """获取工作空间邀请列表"""
    workspace_id = current_user.current_workspace_id
    logger.info(
        "用户 %s 请求获取工作空间 %s 的邀请列表",
        current_user.username,
        workspace_id,
    )

    invites = workspace_service.get_workspace_invites(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    logger.info("成功获取 %s 个邀请记录", len(invites))

    serializer = WorkspaceInviteSerializer()
    invites_i18n = serializer.serialize_list(invites, language)
    return success(data=invites_i18n, msg=t("workspace.invites.list_retrieved"))


@public_router.get("/invites/validate/{token}", response_model=ApiResponse)
def get_workspace_invite_info(
    token: str,
    db: Session = Depends(get_db),
    language: str = Depends(get_request_language),
    t: Callable = Depends(get_translator),
):
    """获取工作空间邀请用户信息（无需认证）"""
    result = invite_service.validate_invite_token(db=db, token=token)
    # The host logged the raw token here; never emit invite secrets to logs.
    logger.info("邀请令牌验证成功")

    serializer = WorkspaceInviteSerializer()
    result_i18n = serializer.serialize(result, language)
    return success(data=result_i18n, msg=t("workspace.invites.validated"))


@router.delete("/invites/{invite_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def revoke_workspace_invite(
    invite_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_request_language),
    t: Callable = Depends(get_translator),
):
    """撤销工作空间邀请"""
    workspace_id = current_user.current_workspace_id
    logger.info(
        "用户 %s 请求撤销工作空间 %s 的邀请 %s",
        current_user.username,
        workspace_id,
        invite_id,
    )

    result = workspace_service.revoke_workspace_invite(
        db=db,
        workspace_id=workspace_id,
        invite_id=invite_id,
        user=current_user,
    )
    logger.info("工作空间邀请撤销成功 - 邀请: %s", invite_id)

    serializer = WorkspaceInviteSerializer()
    result_i18n = serializer.serialize(result, language)
    return success(data=result_i18n, msg=t("workspace.invites.revoked"))


@router.put("/{workspace_id}/switch", response_model=ApiResponse)
@workspace_access_guard()
def switch_workspace(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """切换工作空间"""
    logger.info("用户 %s 请求切换工作空间为 %s", current_user.username, workspace_id)

    workspace_service.switch_workspace(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
    )
    logger.info("成功切换工作空间为 %s", workspace_id)
    return success(msg=t("workspace.switched"))


@router.get("/storage", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_workspace_storage_type(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """获取当前工作空间的存储类型"""
    workspace_id = current_user.current_workspace_id
    logger.info(
        "用户 %s 请求获取工作空间 %s 的存储类型",
        current_user.username,
        workspace_id,
    )

    storage_type = workspace_service.get_workspace_storage_type(
        db=db, workspace_id=workspace_id, user=current_user
    )
    logger.info(
        "成功获取工作空间 %s 的存储类型: %s", workspace_id, storage_type
    )
    return success(
        data={"storage_type": storage_type},
        msg=t("workspace.storage.type_retrieved"),
    )
