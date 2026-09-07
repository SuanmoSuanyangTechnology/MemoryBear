"""用户/租户/空间/成员查询（identity.models 本地只读模型，直连现有库）。"""
from sqlalchemy import select

from src.models import Tenants, User, Workspace, WorkspaceMember


async def get_user(session, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def get_tenant(session, tenant_id) -> Tenants | None:
    return await session.get(Tenants, tenant_id)


async def get_workspace(session, workspace_id) -> Workspace | None:
    return await session.get(Workspace, workspace_id)


async def get_superuser_workspace(session, user) -> str:
    """superuser 拥有租户下全部空间权限，无需 WorkspaceMember 记录（对齐 core
    workspace_repository.get_workspaces_by_user）：优先 current_workspace_id
    （校验属本租户且 active），否则回退本租户最近更新的 active 空间。"""
    if user.current_workspace_id is not None:
        ws = await session.get(Workspace, user.current_workspace_id)
        if ws is not None and ws.tenant_id == user.tenant_id and bool(ws.is_active):
            return str(ws.id)
    ws = (await session.execute(
        select(Workspace).where(Workspace.tenant_id == user.tenant_id,
                                Workspace.is_active == True)  # noqa: E712
        .order_by(Workspace.updated_at.desc())
    )).scalars().first()
    return str(ws.id) if ws else ""


async def get_member_workspace(session, user) -> tuple[str, tuple[str, ...]]:
    """成员空间解析：优先 user.current_workspace_id（多空间用户当前选中空间，切换后
    新快照立即生效），仅在未设置或已不在激活成员中时回退第一个激活成员。
    roles 只取所选空间的 role（不跨空间聚合——跨空间聚合会在阶段 2 启用
    RoleBasedPolicy 时造成提权）。"""
    members = (await session.execute(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user.id,
                                      WorkspaceMember.is_active == True)  # noqa: E712
    )).scalars().all()
    active_workspace_ids = {m.workspace_id for m in members}
    if user.current_workspace_id is not None and user.current_workspace_id in active_workspace_ids:
        workspace_id = user.current_workspace_id
    else:
        workspace_id = members[0].workspace_id if members else None
    if workspace_id is None:
        return ("", ())
    role = next((m.role for m in members if m.workspace_id == workspace_id), None)
    return (str(workspace_id), (role,) if role else ())


async def get_user_ids_by_tenant(session, tenant_id) -> list[str]:
    """租户下全部用户 ID（租户禁用时批量删该租户快照用）。"""
    return (await session.execute(
        select(User.id).where(User.tenant_id == tenant_id)
    )).scalars().all()


async def get_inactive_users_since(session, since) -> list[User]:
    """since 之后更新过的禁用/删除用户（校正任务只删不建）。"""
    return (await session.execute(
        select(User).where(User.updated_at > since, User.is_active == False)  # noqa: E712
    )).scalars().all()


async def get_inactive_tenants_since(session, since) -> list[Tenants]:
    """since 之后更新过的禁用租户（租户禁用 → 批量删该租户全部用户快照）。"""
    return (await session.execute(
        select(Tenants).where(Tenants.updated_at > since, Tenants.is_active == False)  # noqa: E712
    )).scalars().all()


async def get_inactive_member_user_ids(session) -> list[str]:
    """全部含失效成员记录的用户 ID（去重，单查询防 N+1）。

    workspace_members 表无 updated_at 列（已核实 core 迁移 fc9664b8e4a1），无法增量扫，
    用全量扫 is_active=False 兜底；成员变更已有 notify 即时路径，校正只覆盖通知丢失场景。
    """
    return (await session.execute(
        select(User.id).join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.is_active == False).distinct()  # noqa: E712
    )).scalars().all()
