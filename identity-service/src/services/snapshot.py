"""用户快照组装（数据查询在 repositories，本层只做组装与多租户上下文解析）。"""
import logging
from datetime import datetime

from auth_sdk.schema import UserSnapshot
from redis.exceptions import RedisError

from src.repositories.user import (
    get_member_workspace,
    get_superuser_workspace,
    get_tenant,
    get_user,
)

logger = logging.getLogger(__name__)

# 老单体写入键（session_service.py:143 invalidate_all_user_tokens），值 aware ISO 带 Z 后缀
TOKEN_INVALIDATION_KEY = "user_token_invalidation:{user_id}"


async def _read_token_invalidated_before(redis, user_id: str) -> datetime | None:
    """读老单体失效时间键；可选字段 fail-open：读失败/损坏置 None（网关仅非 None 才比较）。"""
    try:
        raw = await redis.get(TOKEN_INVALIDATION_KEY.format(user_id=user_id))
    except RedisError:
        logger.warning("read token_invalidation failed for %s", user_id)
        return None
    if not raw:
        return None
    try:
        # 值带 Z 后缀（to_iso_z，Python 3.11+ fromisoformat 原生解析）；快照存 naive UTC
        return datetime.fromisoformat(raw.decode()).replace(tzinfo=None)
    except (ValueError, UnicodeDecodeError):
        logger.warning("invalid token_invalidation value for %s: %r", user_id, raw[:64])
        return None


async def build_user_snapshot(session, user_id: str, redis=None) -> UserSnapshot | None:
    user = await get_user(session, user_id)
    if user is None:
        return None
    tenant = await get_tenant(session, user.tenant_id)
    # 多租户上下文权威来源：用户 JWT 只有 sub（老单体签发不含 workspace_id），
    # 网关从本快照解析 tenant_id/workspace_id/roles
    if user.is_superuser:
        # superuser 拥有租户下全部空间权限，无需 WorkspaceMember 记录（对齐 core
        # workspace_repository.get_workspaces_by_user）：优先 current_workspace_id，
        # 校验属于本租户且 active；否则回退本租户最近更新的 active 空间
        workspace_id = await get_superuser_workspace(session, user)
        roles = ()  # 无成员记录；角色首期不启用（设计决策 #7），预留空
    else:
        workspace_id, roles = await get_member_workspace(session, user)
    return UserSnapshot(
        user_id=str(user.id), tenant_id=str(user.tenant_id),
        workspace_id=workspace_id,
        roles=roles,
        disabled=not bool(user.is_active),
        tenant_active=bool(tenant.is_active) if tenant else False,
        token_invalidated_before=(
            await _read_token_invalidated_before(redis, str(user.id))
            if redis is not None else None
        ),
    )
