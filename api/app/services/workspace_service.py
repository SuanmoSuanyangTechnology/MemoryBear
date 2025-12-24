import datetime
import hashlib
import secrets
import uuid
from os import getenv
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException, PermissionDeniedException
from app.core.logging_config import get_business_logger
from app.models.user_model import User
from app.models.workspace_model import Workspace, WorkspaceRole, InviteStatus, WorkspaceMember
from app.repositories import workspace_repository
from app.repositories.workspace_invite_repository import WorkspaceInviteRepository
from app.schemas.workspace_schema import (
    WorkspaceCreate,
    WorkspaceUpdate,
    WorkspaceInviteCreate,
    WorkspaceInviteResponse,
    InviteValidateResponse,
    InviteAcceptRequest,
    WorkspaceMemberUpdate
)

# 获取业务逻辑专用日志器
business_logger = get_business_logger()
from dotenv import load_dotenv
load_dotenv()
def switch_workspace(
    db: Session,
    workspace_id: uuid.UUID,
    user: User,
):
    """切换工作空间"""
    business_logger.debug(f"用户 {user.username} 请求切换工作空间为 {workspace_id}")

    # 检查用户是否为成员或超级管理员
    _check_workspace_member_permission(db, workspace_id, user)

    # 更新当前用户的工作空间上下文
    try:
        user.current_workspace_id = workspace_id
        db.commit()
        business_logger.info(f"用户 {user.username} 成功切换工作空间为 {workspace_id}")
        return
    except Exception as e:
        db.rollback()
        business_logger.error(f"切换工作空间失败 - 工作空间: {workspace_id}, 错误: {str(e)}")
        raise BusinessException(f"切换工作空间失败: {str(e)}", BizCode.INTERNAL_ERROR)


def  delete_workspace_member(
            db: Session,
            workspace_id: uuid.UUID,
            member_id: uuid.UUID,
            user: User,
        ):
        """删除工作空间成员"""
        business_logger.debug(f"用户 {user.username} 请求删除工作空间 {workspace_id} 的成员 {member_id}")
        _check_workspace_admin_permission(db, workspace_id, user)
        workspace_member = workspace_repository.get_member_by_id(db=db, member_id=member_id)
        if not workspace_member:
                raise BusinessException(f"工作空间成员 {member_id} 不存在", BizCode.WORKSPACE_MEMBER_NOT_FOUND)

        if workspace_member.workspace_id != workspace_id:
                raise BusinessException(f"工作空间成员 {member_id} 不存在于工作空间 {workspace_id}", BizCode.WORKSPACE_MEMBER_NOT_FOUND)

        try:
            workspace_member.is_active = False
            workspace_member.user.current_workspace_id = None
            db.commit()
            business_logger.info(f"用户 {user.username} 成功删除工作空间 {workspace_id} 的成员 {member_id}")
        except Exception as e:
            db.rollback()
            business_logger.error(f"删除工作空间成员失败 - 工作空间: {workspace_id}, 成员: {member_id}, 错误: {str(e)}")
            raise BusinessException(f"删除工作空间成员失败: {str(e)}", BizCode.INTERNAL_ERROR)


def get_user_workspaces(db: Session, user: User) -> List[Workspace]:
    """获取当前用户参与的所有工作空间"""
    business_logger.debug(f"获取用户工作空间列表: {user.username} (ID: {user.id})")
    workspaces = workspace_repository.get_workspaces_by_user(db=db, user_id=user.id)
    business_logger.info(f"用户 {user.username} 的工作空间数量: {len(workspaces)}")
    return workspaces


def _create_workspace_only(
    db: Session, workspace: WorkspaceCreate, owner: User
) -> Workspace:
    business_logger.debug(f"创建工作空间: {workspace.name}, 创建者: {owner.username}")

    try:
        # Create the workspace without adding any members
        business_logger.debug(f"创建工作空间: {workspace.name}")
        db_workspace = workspace_repository.create_workspace(
            db=db, workspace=workspace, tenant_id=owner.tenant_id
        )
        business_logger.info(f"工作空间创建成功: {db_workspace.name} (ID: {db_workspace.id}), 创建者: {owner.username}")
        return db_workspace
    except Exception as e:
        business_logger.error(f"创建工作空间失败: {workspace.name} - {str(e)}")
        raise

def create_workspace(
        db: Session, workspace: WorkspaceCreate, user: User
) -> Workspace:
    business_logger.info(
        f"创建工作空间: {workspace.name}, 创建者: {user.username}, "
        f"storage_type: {workspace.storage_type}"
    )
    llm=workspace.llm
    embedding=workspace.embedding
    rerank=workspace.rerank
    try:
        # Create the workspace without adding any members
        business_logger.debug(f"创建工作空间: {workspace.name}")
        db_workspace = workspace_repository.create_workspace(
            db=db, workspace=workspace, tenant_id=user.tenant_id
        )
        business_logger.info(f"工作空间创建成功: {db_workspace.name} (ID: {db_workspace.id}), 创建者: {user.username}")
        db.commit()
        db.refresh(db_workspace)

        # 如果 storage_type 是 "rag"，自动创建知识库
        if workspace.storage_type == "rag":
            business_logger.info(
                f"检测到 storage_type 为 'rag'，开始为工作空间 "
                f"{db_workspace.id} 创建知识库"
            )
            try:
                import os
                from app.schemas.knowledge_schema import KnowledgeCreate
                from app.models.knowledge_model import KnowledgeType, PermissionType
                from app.repositories import knowledge_repository

                # 创建知识库数据
                knowledge_data = KnowledgeCreate(
                    workspace_id=db_workspace.id,
                    created_by=user.id,
                    parent_id=db_workspace.id,
                    name="USER_RAG_MERORY",
                    description=f"工作空间 {workspace.name} 的默认知识库",
                    avatar='',
                    type=KnowledgeType.General,
                    permission_id=PermissionType.Memory,
                    embedding_id=uuid.UUID(getenv('KB_embedding_id')) if None else embedding,
                    reranker_id=uuid.UUID(getenv('KB_reranker_id')) if None else rerank,
                    llm_id=uuid.UUID(getenv('KB_llm_id')) if None else llm,
                    image2text_id=uuid.UUID(getenv('KB_llm_id')) if None else llm,
                    parser_config={
                        "layout_recognize": "DeepDOC",
                        "chunk_token_num": 256,
                        "delimiter": "\n",
                        "auto_keywords": 0,
                        "auto_questions": 0,
                        "html4excel": False
                    }
                )

                # 直接使用 repository 创建知识库，避免 service 层的额外逻辑
                db_knowledge = knowledge_repository.create_knowledge(
                    db=db,
                    knowledge=knowledge_data
                )
                db.commit()
                business_logger.info(
                    f"为工作空间 {db_workspace.id} 自动创建知识库成功: "
                    f"{db_knowledge.name} (ID: {db_knowledge.id})"
                )
            except Exception as kb_error:
                business_logger.error(
                    f"为工作空间 {db_workspace.id} 创建知识库失败: {str(kb_error)}"
                )
                db.rollback()
                raise BusinessException(
                    f"工作空间创建成功，但知识库创建失败: {str(kb_error)}",
                    BizCode.INTERNAL_ERROR
                )

        return db_workspace

    except Exception as e:
        business_logger.error(f"工作空间创建失败: {workspace.name} - {str(e)}")
        db.rollback()
        raise


def update_workspace(
    db: Session, workspace_id: uuid.UUID, workspace_in: WorkspaceUpdate, user: User
) -> Workspace:
    business_logger.info(f"更新工作空间: workspace_id={workspace_id}, 操作者: {user.username}")

    db_workspace = _check_workspace_admin_permission(db,workspace_id,user)
    try:
        # 更新工作空间
        business_logger.debug(f"执行工作空间更新: {db_workspace.name} (ID: {workspace_id})")
        update_data = workspace_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_workspace, field, value)

        db.add(db_workspace)
        db.commit()
        db.refresh(db_workspace)
        business_logger.info(f"工作空间更新成功: {db_workspace.name} (ID: {workspace_id})")
        return db_workspace
    except Exception as e:
        business_logger.error(f"工作空间更新失败: workspace_id={workspace_id} - {str(e)}")
        db.rollback()
        raise


def get_workspace_members(
    db: Session, workspace_id: uuid.UUID, user: User
) -> List[WorkspaceMember]:
    """获取某工作空间的成员列表（关系序列化由模型关系支持）"""
    business_logger.info(f"获取工作空间成员: workspace_id={workspace_id}, 操作者: {user.username}")

    # 查找工作空间
    business_logger.debug(f"查找工作空间: {workspace_id}")
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not workspace:
        business_logger.warning(f"工作空间不存在: {workspace_id}")
        raise BusinessException(
            message="Workspace not found",
            code=BizCode.WORKSPACE_NOT_FOUND
        )

    # 权限检查：工作空间成员或超级管理员可以查看成员列表
    from app.core.permissions import permission_service, Subject, Resource, Action
    member = workspace_repository.get_member_in_workspace(
        db=db, user_id=user.id, workspace_id=workspace_id
    )
    workspace_memberships = {workspace_id} if member else set()

    subject = Subject.from_user(user, workspace_memberships=workspace_memberships)
    resource = Resource.from_workspace(workspace)

    try:
        permission_service.require_permission(
            subject,
            Action.READ,
            resource,
            error_message=f"用户 {user.username} 没有查看工作空间 {workspace_id} 成员列表的权限"
        )
    except PermissionDeniedException as e:
        business_logger.warning(
            f"权限不足: 用户 {user.username} 尝试获取工作空间 {workspace_id} 成员列表"
        )
        raise BusinessException(str(e), BizCode.WORKSPACE_ACCESS_DENIED)

    # 查询成员并预加载 user/workspace 关系
    members = workspace_repository.get_members_by_workspace(db=db, workspace_id=workspace_id)
    business_logger.info(f"工作空间成员数量: {len(members)} - workspace_id={workspace_id}")
    return members



# ==================== 邀请相关服务方法 ====================

def _generate_invite_token() -> tuple[str, str]:
    """生成邀请令牌和其哈希值

    Returns:
        tuple: (原始令牌, 令牌哈希)
    """
    # 生成32字节的随机令牌
    token = secrets.token_urlsafe(32)
    # 生成令牌的SHA256哈希
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token, token_hash


def _check_workspace_member_permission(db: Session, workspace_id: uuid.UUID, user: User) -> Workspace | None:
    """检查用户是否为工作空间成员或超级管理员（使用统一权限服务）"""
    # 获取工作空间信息
    db_workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not db_workspace:
        raise BusinessException(
            message="Workspace not found",
            code=BizCode.WORKSPACE_NOT_FOUND
        )

    # 使用统一权限服务检查访问权限
    from app.core.permissions import permission_service, Subject, Resource, Action

    # 获取用户的工作空间成员关系
    member = workspace_repository.get_member_in_workspace(
        db=db, user_id=user.id, workspace_id=workspace_id
    )

    # 任何成员都有访问权限
    workspace_memberships = {workspace_id} if member else set()

    subject = Subject.from_user(user, workspace_memberships=workspace_memberships)
    resource = Resource.from_workspace(db_workspace)

    try:
        permission_service.require_permission(
            subject,
            Action.READ,
            resource,
            error_message=f"用户 {user.username} 不是工作空间 {workspace_id} 的成员"
        )
        business_logger.debug(f"用户 {user.username} 是工作空间 {workspace_id} 的成员或超级管理员")
    except PermissionDeniedException as e:
        business_logger.warning(f"权限不足: 用户 {user.username} 尝试访问工作空间 {workspace_id}")
        raise BusinessException(str(e), BizCode.WORKSPACE_NO_ACCESS)
    return db_workspace


def _check_workspace_admin_permission(db: Session, workspace_id: uuid.UUID, user: User) -> Workspace | None:
    """检查用户是否有工作空间管理员权限（使用统一权限服务）"""
    # 获取工作空间信息
    db_workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not db_workspace:
        raise BusinessException(
            message="Workspace not found",
            code=BizCode.WORKSPACE_NOT_FOUND
        )

    # 使用统一权限服务检查管理权限
    from app.core.permissions import permission_service, Subject, Resource, Action

    # 获取用户的工作空间成员关系
    member = workspace_repository.get_member_in_workspace(
        db=db, user_id=user.id, workspace_id=workspace_id
    )

    # 只有 manager 才有管理权限
    workspace_memberships = {workspace_id} if (member and member.role == WorkspaceRole.manager) else set()

    subject = Subject.from_user(user, workspace_memberships=workspace_memberships)
    resource = Resource.from_workspace(db_workspace)

    try:
        permission_service.require_permission(
            subject,
            Action.MANAGE,
            resource,
            error_message=f"用户 {user.username} 没有管理工作空间 {workspace_id} 的权限"
        )
        business_logger.debug(f"用户 {user.username} 有权限管理工作空间 {workspace_id}")
    except PermissionDeniedException as e:
        business_logger.warning(f"权限不足: 用户 {user.username} 尝试管理工作空间 {workspace_id}")
        raise BusinessException(str(e), BizCode.WORKSPACE_ACCESS_DENIED)
    return db_workspace


def create_workspace_invite(
    db: Session,
    workspace_id: uuid.UUID,
    invite_data: WorkspaceInviteCreate,
    user: User
) -> WorkspaceInviteResponse:
    """创建工作空间邀请"""
    business_logger.info(f"创建工作空间邀请: workspace_id={workspace_id}, email={invite_data.email}, 创建者: {user.username}")

    try:
        # 检查权限
        _check_workspace_admin_permission(db, workspace_id, user)
        if settings.ENABLE_SINGLE_WORKSPACE:
            # 检查被邀请用户是否已经在工作空间中
            from app.repositories import user_repository
            invited_user = user_repository.get_user_by_email(db, invite_data.email)

            if invited_user:
                # 用户存在，检查是否已经是工作空间成员
                existing_member = workspace_repository.get_member_in_workspace(
                    db=db,
                    user_id=invited_user.id,
                    workspace_id=workspace_id
                )
                if existing_member:
                    business_logger.warning(f"用户 {invite_data.email} 已经是工作空间成员")
                    raise BusinessException("该用户已经是工作空间成员", BizCode.RESOURCE_ALREADY_EXISTS)

        # 检查是否已有待处理的邀请
        invite_repo = WorkspaceInviteRepository(db)
        existing_invite = invite_repo.get_pending_invite_by_email_and_workspace(
            email=invite_data.email,
            workspace_id=workspace_id
        )

        invite_token = None
        if existing_invite:
            business_logger.info(f"邮箱 {invite_data.email} 在工作空间 {workspace_id} 已有待处理邀请，返回现有邀请")
            # 生成新的邀请链接（重新生成令牌）
            token, token_hash = _generate_invite_token()
            existing_invite.token_hash = token_hash
            existing_invite.updated_at = datetime.datetime.now()
            db.commit()
            db.refresh(existing_invite)
            invite_token = token
        else:
            # 生成邀请令牌
            token, token_hash = _generate_invite_token()
            # 创建邀请
            db_invite = invite_repo.create_invite(
                workspace_id=workspace_id,
                invite_data=invite_data,
                token_hash=token_hash,
                created_by_user_id=user.id
            )
            db.commit()
            db.refresh(db_invite)
            invite_token = token

        invite_obj = existing_invite or db_invite
        business_logger.info(f"工作空间邀请创建成功: invite_id={invite_obj.id}, email={invite_data.email}")

        # 构造响应
        response = WorkspaceInviteResponse.model_validate(invite_obj)
        response.invite_token = invite_token
        return response


    except Exception as e:
        db.rollback()
        business_logger.error(f"创建工作空间邀请失败: workspace_id={workspace_id}, email={invite_data.email} - {str(e)}")
        raise


def get_workspace_invites(
    db: Session,
    workspace_id: uuid.UUID,
    user: User,
    status: Optional[InviteStatus] = None,
    limit: int = 50,
    offset: int = 0
) -> List[WorkspaceInviteResponse]:
    """获取工作空间邀请列表"""
    business_logger.info(f"获取工作空间邀请列表: workspace_id={workspace_id}, 操作者: {user.username}")

    # 检查工作空间是否存在
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not workspace:
        raise BusinessException("工作空间不存在", BizCode.WORKSPACE_NOT_FOUND)

    # 检查权限
    _check_workspace_admin_permission(db, workspace_id, user)

    # 获取邀请列表
    invite_repo = WorkspaceInviteRepository(db)
    invites = invite_repo.get_workspace_invites(
        workspace_id=workspace_id,
        status=status,
        limit=limit,
        offset=offset
    )

    return [WorkspaceInviteResponse.model_validate(invite) for invite in invites]


def validate_invite_token(db: Session, token: str) -> InviteValidateResponse:
    """验证邀请令牌"""
    business_logger.info("验证邀请令牌")

    # 生成令牌哈希
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # 查找邀请
    invite_repo = WorkspaceInviteRepository(db)
    invite = invite_repo.get_invite_by_token_hash(token_hash)

    if not invite:
        business_logger.warning("邀请令牌无效")
        raise BusinessException("邀请令牌无效", BizCode.WORKSPACE_INVITE_NOT_FOUND)

    # 检查邀请状态和过期时间
    now = datetime.datetime.now()
    is_expired = invite.expires_at < now or invite.status != InviteStatus.pending
    is_valid = not is_expired

    # 获取工作空间信息
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=invite.workspace_id)

    business_logger.info(f"邀请令牌验证完成: valid={is_valid}, expired={is_expired}")

    return InviteValidateResponse(
        workspace_name=workspace.name,
        workspace_id=invite.workspace_id,
        email=invite.email,
        role=WorkspaceRole(invite.role),
        is_expired=is_expired,
        is_valid=is_valid
    )


def accept_workspace_invite(
    db: Session,
    accept_request: InviteAcceptRequest,
    user: User
) -> dict:
    """接受工作空间邀请"""
    business_logger.info(f"接受工作空间邀请: 用户 {user.username}")

    try:
        from app.core.config import settings

        # 生成令牌哈希
        token_hash = hashlib.sha256(accept_request.token.encode()).hexdigest()

        # 查找邀请
        invite_repo = WorkspaceInviteRepository(db)
        invite = invite_repo.get_invite_by_token_hash(token_hash)

        if not invite:
            business_logger.warning("邀请令牌无效")
            raise BusinessException("邀请令牌无效", BizCode.WORKSPACE_INVITE_NOT_FOUND)

        # 检查邀请状态
        if invite.status != InviteStatus.pending:
            business_logger.warning(f"邀请已被处理: status={invite.status}")
            raise BusinessException(f"邀请已被{invite.status}", BizCode.WORKSPACE_INVITE_INVALID)

        # 检查过期时间
        now = datetime.datetime.now()
        if invite.expires_at < now:
            business_logger.warning("邀请已过期")
            # 标记为过期
            invite_repo.update_invite_status(invite.id, InviteStatus.expired)
            raise BusinessException("邀请已过期", BizCode.WORKSPACE_INVITE_EXPIRED)

        # 检查邮箱是否匹配
        if invite.email != user.email:
            business_logger.warning(f"邮箱不匹配: invite_email={invite.email}, user_email={user.email}")
            raise BusinessException("邮箱与邀请邮箱不匹配", BizCode.FORBIDDEN)

        # 如果启用单工作空间模式，检查用户是否已有工作空间
        if settings.ENABLE_SINGLE_WORKSPACE:
            user_workspaces = workspace_repository.get_workspaces_by_user(db=db, user_id=user.id)
            if user_workspaces:
                business_logger.warning(f"单工作空间模式下用户已有工作空间: user={user.username}")
                raise BusinessException("用户只能加入一个工作空间", BizCode.FORBIDDEN)

        # 检查用户是否已经是工作空间成员
        existing_member = workspace_repository.get_member_in_workspace(
            db=db,
            user_id=user.id,
            workspace_id=invite.workspace_id
        )

        if existing_member:
            business_logger.info("用户已是工作空间成员，更新邀请状态")
            invite_repo.update_invite_status(
                invite.id,
                InviteStatus.accepted,
                accepted_at=now
            )
            db.commit()
            workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=invite.workspace_id)
            return {
                "message": "You are already a member of this workspace",
                "workspace": workspace
            }

        # 将角色映射到工作空间角色（现在直接使用相同的角色）
        workspace_role = invite.role

        # 添加用户到工作空间
        workspace_repository.add_member_to_workspace(
            db=db,
            user_id=user.id,
            workspace_id=invite.workspace_id,
            role=workspace_role
        )

        # 标记邀请为已接受
        invite_repo.update_invite_status(
            invite.id,
            InviteStatus.accepted,
            accepted_at=now
        )

        db.commit()

        # 获取工作空间信息
        workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=invite.workspace_id)

        business_logger.info(f"用户成功加入工作空间: user={user.username}, workspace={workspace.name}, role={workspace_role}")

        return {
            "message": "Successfully joined the workspace",
            "workspace": workspace,
            "role": workspace_role
        }

    except Exception as e:
        db.rollback()
        business_logger.error(f"接受工作空间邀请失败: user={user.username} - {str(e)}")
        raise


def revoke_workspace_invite(
    db: Session,
    workspace_id: uuid.UUID,
    invite_id: uuid.UUID,
    user: User
) -> dict:
    """撤销工作空间邀请"""
    business_logger.info(f"撤销工作空间邀请: workspace_id={workspace_id}, invite_id={invite_id}, 操作者: {user.username}")

    try:
        # 检查权限
        _check_workspace_admin_permission(db, workspace_id, user)

        # 撤销邀请
        invite_repo = WorkspaceInviteRepository(db)
        invite = invite_repo.revoke_invite(invite_id)

        if not invite:
            business_logger.warning(f"邀请不存在: invite_id={invite_id}")
            raise BusinessException("邀请不存在", BizCode.WORKSPACE_INVITE_NOT_FOUND)

        if invite.workspace_id != workspace_id:
            business_logger.warning(f"邀请不属于指定工作空间: invite_id={invite_id}, workspace_id={workspace_id}")
            raise BusinessException("邀请不属于指定工作空间", BizCode.BAD_REQUEST)

        db.commit()
        business_logger.info(f"工作空间邀请撤销成功: invite_id={invite_id}")
        return {"message": "邀请撤销成功"}

    except Exception as e:
        db.rollback()
        business_logger.error(f"撤销工作空间邀请失败: invite_id={invite_id} - {str(e)}")
        raise


def update_workspace_member_roles(
    db: Session,
    workspace_id: uuid.UUID,
    updates: List[WorkspaceMemberUpdate],
    user: User,
) -> List[WorkspaceMember]:
    """更新工作空间成员角色"""
    business_logger.info(f"更新工作空间成员角色: workspace_id={workspace_id}, 操作者: {user.username}, 更新数量: {len(updates)}")

    # 检查管理员权限
    _check_workspace_admin_permission(db, workspace_id, user)

    # 获取所有当前成员
    all_members = workspace_repository.get_members_by_workspace(db=db, workspace_id=workspace_id)
    member_map = {m.id: m for m in all_members}

    # 验证和业务规则检查
    update_ids = set()
    for upd in updates:
        # 检查成员是否存在
        if upd.id not in member_map:
            raise BusinessException(f"成员 {upd.id} 不存在于工作空间 {workspace_id}", BizCode.WORKSPACE_MEMBER_NOT_FOUND)

        member = member_map[upd.id]

        # 检查成员是否属于该工作空间
        if member.workspace_id != workspace_id:
            raise BusinessException(f"成员 {upd.id} 不属于工作空间 {workspace_id}", BizCode.WORKSPACE_MEMBER_NOT_FOUND)

        # 不能修改自己的角色
        if member.user_id == user.id:
            raise BusinessException("不能修改自己的角色", BizCode.BAD_REQUEST)

        update_ids.add(upd.id)

    # 检查是否至少保留一个 manager
    current_managers = [m for m in all_members if m.role == WorkspaceRole.manager]
    managers_after_update = [
        m for m in all_members
        if m.id not in update_ids and m.role == WorkspaceRole.manager
    ]

    # 添加更新后会成为 manager 的成员
    for upd in updates:
        if upd.role == WorkspaceRole.manager:
            managers_after_update.append(member_map[upd.id])

    if len(managers_after_update) == 0:
        raise BusinessException("工作空间至少需要一个管理员", BizCode.BAD_REQUEST)

    # 执行更新
    try:
        for upd in updates:
            workspace_repository.update_member_role_by_id(
                db=db,
                id=upd.id,
                role=upd.role,
            )
            business_logger.debug(f"更新成员 {upd.id} 角色为 {upd.role}")

        db.commit()

        # 重新获取更新后的成员列表
        updated_members = workspace_repository.get_members_by_workspace(db=db, workspace_id=workspace_id)
        business_logger.info(f"成员角色更新完成: workspace_id={workspace_id}, 更新数量={len(updates)}")

        return updated_members

    except Exception as e:
        db.rollback()
        business_logger.error(f"更新工作空间成员角色失败: workspace_id={workspace_id} - {str(e)}")
        raise BusinessException(f"更新成员角色失败: {str(e)}", BizCode.INTERNAL_ERROR)


def get_workspace_storage_type(
        db: Session,
        workspace_id: uuid.UUID,
        user: User,
) -> Optional[str]:
    """获取工作空间的存储类型

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        user: 当前用户

    Returns:
        storage_type: 存储类型字符串，如果未设置则返回 None
    """
    business_logger.info(f"用户 {user.username} 请求获取工作空间 {workspace_id} 的存储类型")

    # 检查用户是否有权限访问该工作空间
    _check_workspace_member_permission(db, workspace_id, user)

    # 查询工作空间
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not workspace:
        business_logger.error(f"工作空间不存在: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在"
        )

    business_logger.info(f"成功获取工作空间 {workspace_id} 的存储类型: {workspace.storage_type}")
    return workspace.storage_type


def get_workspace_storage_type_without_auth(
        db: Session,
        workspace_id: uuid.UUID,
) -> Optional[str]:
    """获取工作空间的存储类型（无需权限验证，用于公开分享等场景）

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID

    Returns:
        storage_type: 存储类型字符串，如果未设置则返回 None
    """
    business_logger.info(f"获取工作空间 {workspace_id} 的存储类型（无权限验证）")

    # 查询工作空间
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not workspace:
        business_logger.error(f"工作空间不存在: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在"
        )

    business_logger.info(f"成功获取工作空间 {workspace_id} 的存储类型: {workspace.storage_type}")
    return workspace.storage_type


def get_workspace_models_configs(
        db: Session,
        workspace_id: uuid.UUID,
        user: User,
) -> Optional[dict]:
    """获取工作空间的模型配置（llm, embedding, rerank）

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        user: 当前用户

    Returns:
        dict: 包含 llm, embedding, rerank 的字典，如果工作空间不存在则返回 None
    """
    business_logger.info(f"用户 {user.username} 请求获取工作空间 {workspace_id} 的模型配置")

    # 检查用户是否有权限访问该工作空间
    _check_workspace_member_permission(db, workspace_id, user)

    # 查询工作空间模型配置
    configs = workspace_repository.get_workspace_models_configs(db=db, workspace_id=workspace_id)

    if configs is None:
        business_logger.error(f"工作空间不存在: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在"
        )

    business_logger.info(
        f"成功获取工作空间 {workspace_id} 的模型配置: "
        f"llm={configs.get('llm')}, embedding={configs.get('embedding')}, rerank={configs.get('rerank')}"
    )
    return configs

