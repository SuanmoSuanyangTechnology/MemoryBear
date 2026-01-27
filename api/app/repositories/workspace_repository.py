from sqlalchemy.orm import Session, joinedload
from app.models.user_model import User
from typing import List, Optional
import uuid
from app.models.workspace_model import Workspace, WorkspaceMember, WorkspaceRole
from app.schemas.workspace_schema import WorkspaceCreate, WorkspaceUpdate
from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()


class WorkspaceRepository:
    """工作空间数据访问层"""

    def __init__(self, db: Session):
        self.db = db

    def create_workspace(self, workspace_data: WorkspaceCreate, tenant_id: uuid.UUID) -> Workspace:
        """创建工作空间"""
        db_logger.debug(f"创建工作空间记录: name={workspace_data.name}, tenant_id={tenant_id}")
        
        try:
            db_workspace = Workspace(
                name=workspace_data.name,
                description=workspace_data.description,
                icon=workspace_data.icon,
                iconType=workspace_data.iconType,
                storage_type=workspace_data.storage_type,
                llm=workspace_data.llm,
                embedding=workspace_data.embedding,
                rerank=workspace_data.rerank,
                tenant_id=tenant_id
            )
            self.db.add(db_workspace)
            self.db.flush()
            db_logger.info(f"工作空间记录创建成功: {workspace_data.name} (ID: {db_workspace.id}), storage_type: {workspace_data.storage_type}")
            return db_workspace
        except Exception as e:
            db_logger.error(f"创建工作空间记录失败: name={workspace_data.name} - {str(e)}")
            raise

    def get_workspace_by_id(self, workspace_id: uuid.UUID) -> Optional[Workspace]:
        """根据ID获取工作空间"""
        db_logger.debug(f"根据ID查询工作空间: workspace_id={workspace_id}")
        
        try:
            workspace = self.db.query(Workspace).filter(Workspace.id == workspace_id).first()
            if workspace:
                db_logger.debug(f"工作空间查询成功: {workspace.name} (ID: {workspace_id})")
            else:
                db_logger.debug(f"工作空间不存在: workspace_id={workspace_id}")
            return workspace
        except Exception as e:
            db_logger.error(f"根据ID查询工作空间失败: workspace_id={workspace_id} - {str(e)}")
            raise

    def get_workspace_models_configs(self, workspace_id: uuid.UUID) -> Optional[dict]:
        """根据workspace_id获取模型配置（llm, embedding, rerank）
        
        Args:
            workspace_id: 工作空间ID
            
        Returns:
            包含 llm, embedding, rerank 的字典，如果工作空间不存在则返回 None
        """
        db_logger.debug(f"查询工作空间模型配置: workspace_id={workspace_id}")
        
        try:
            workspace = self.db.query(Workspace).filter(Workspace.id == workspace_id).first()
            if workspace:
                configs = {
                    "llm": workspace.llm,
                    "embedding": workspace.embedding,
                    "rerank": workspace.rerank
                }
                db_logger.debug(
                    f"工作空间模型配置查询成功: workspace_id={workspace_id}, "
                    f"llm={configs['llm']}, embedding={configs['embedding']}, rerank={configs['rerank']}"
                )
                return configs
            else:
                db_logger.debug(f"工作空间不存在: workspace_id={workspace_id}")
                return None
        except Exception as e:
            db_logger.error(f"查询工作空间模型配置失败: workspace_id={workspace_id} - {str(e)}")
            raise

    def get_workspaces_by_user(self, user_id: uuid.UUID) -> List[Workspace]:
        """获取用户参与的所有工作空间（包括用户创建的和作为成员的）"""
        db_logger.debug(f"查询用户参与的工作空间: user_id={user_id}")
        
        try:
            # 首先获取用户信息以获取 tenant_id
            from app.models.user_model import User
            user = self.db.query(User).filter(User.id == user_id).first()
            if not user:
                db_logger.warning(f"用户不存在: user_id={user_id}")
                return []
            
            if user.is_superuser:
                # 超级用户获取对应tenantid所有工作空间
                workspaces = (
                    self.db.query(Workspace)
                    .filter(Workspace.tenant_id == user.tenant_id)
                    .filter(Workspace.is_active.is_(True))
                    .order_by(Workspace.updated_at.desc())
                    .all()
                )
                db_logger.debug(f"超用户查询所有工作空间: user_id={user_id}, 数量={len(workspaces)}")
                return workspaces
            
            # 获取用户作为成员的工作空间
            member_workspaces = (
                self.db.query(Workspace)
                .join(WorkspaceMember, Workspace.id == WorkspaceMember.workspace_id)
                .filter(WorkspaceMember.user_id == user_id)
                .filter(Workspace.is_active.is_(True))
                .order_by(Workspace.updated_at.desc())
                .all()
            )
                       
            db_logger.debug(f"用户工作空间查询成功: user_id={user_id}, 数量={len(member_workspaces)}")
            return member_workspaces
        except Exception as e:
            db_logger.error(f"查询用户工作空间失败: user_id={user_id} - {str(e)}")
            raise

    def get_workspaces_by_tenant(self, tenant_id: uuid.UUID) -> List[Workspace]:
        """获取租户的所有工作空间"""
        db_logger.debug(f"查询租户的工作空间: tenant_id={tenant_id}")
        
        try:
            workspaces = (
                self.db.query(Workspace)
                .filter(Workspace.tenant_id == tenant_id)
                .filter(Workspace.is_active.is_(True))
                .all()
            )
            db_logger.debug(f"租户工作空间查询成功: tenant_id={tenant_id}, 数量={len(workspaces)}")
            return workspaces
        except Exception as e:
            db_logger.error(f"查询租户工作空间失败: tenant_id={tenant_id} - {str(e)}")
            raise

    def add_member(self, workspace_id: uuid.UUID, user_id: uuid.UUID, role: WorkspaceRole = WorkspaceRole.member) -> WorkspaceMember:
        """添加工作空间成员"""
        db_logger.debug(f"添加工作空间成员: user_id={user_id}, workspace_id={workspace_id}, role={role}")
        
        try:
            db_member = WorkspaceMember(
                user_id=user_id, 
                workspace_id=workspace_id, 
                role=role
            )
            self.db.add(db_member)
            self.db.flush()
            db_logger.info(f"工作空间成员添加成功: user_id={user_id}, workspace_id={workspace_id}, role={role}")
            return db_member
        except Exception as e:
            db_logger.error(f"添加工作空间成员失败: user_id={user_id}, workspace_id={workspace_id} - {str(e)}")
            raise

    def get_member(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> Optional[WorkspaceMember]:
        """获取工作空间成员"""
        db_logger.debug(f"查询工作空间成员: user_id={user_id}, workspace_id={workspace_id}")
        
        try:
            member = self.db.query(WorkspaceMember).filter(
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.is_active.is_(True),
            ).first()
            if member:
                db_logger.debug(f"工作空间成员查询成功: user_id={user_id}, workspace_id={workspace_id}, role={member.role}")
            else:
                db_logger.debug(f"工作空间成员不存在: user_id={user_id}, workspace_id={workspace_id}")
            return member
        except Exception as e:
            db_logger.error(f"查询工作空间成员失败: user_id={user_id}, workspace_id={workspace_id} - {str(e)}")
            raise

    def get_members_by_workspace(self, workspace_id: uuid.UUID) -> List[WorkspaceMember]:
        """按工作空间获取成员列表，并预加载 user 与 workspace 关系"""
        db_logger.debug(f"查询工作空间的成员列表: workspace_id={workspace_id}")
        try:
            members = (
                self.db.query(WorkspaceMember)
                .join(User, WorkspaceMember.user_id == User.id)
                .options(joinedload(WorkspaceMember.user), joinedload(WorkspaceMember.workspace))
                .filter(WorkspaceMember.workspace_id == workspace_id)
                .filter(WorkspaceMember.is_active.is_(True))
                .filter(User.is_active.is_(True))
                .all()
            )
            db_logger.debug(f"成员列表查询成功: workspace_id={workspace_id}, 数量={len(members)}")
            return members
        except Exception as e:
            db_logger.error(f"查询成员列表失败: workspace_id={workspace_id} - {str(e)}")
            raise
    
    def get_member_by_id(self, member_id: uuid.UUID) -> WorkspaceMember:
        """按成员ID获取工作空间成员，并预加载 user 与 workspace 关系"""
        db_logger.debug(f"查询成员的工作空间: member_id={member_id}")
        try:
            member = (
                self.db.query(WorkspaceMember)
                .join(User, WorkspaceMember.user_id == User.id)
                .options(joinedload(WorkspaceMember.user), joinedload(WorkspaceMember.workspace))
                .filter(WorkspaceMember.id == member_id)
                .filter(WorkspaceMember.is_active.is_(True))
                .filter(User.is_active.is_(True))
                .first()
            )
            if member:
                db_logger.debug(f"成员查询成功: member_id={member_id}, workspace_id={member.workspace_id}, role={member.role}")
            else:
                db_logger.debug(f"成员不存在: member_id={member_id}")
            return member
        except Exception as e:
            db_logger.error(f"查询成员列表失败: member_id={member_id} - {str(e)}")
            raise

    def update_member_role(self, workspace_id: uuid.UUID, user_id: uuid.UUID, role: WorkspaceRole) -> Optional[WorkspaceMember]:
        try:
            member = self.db.query(WorkspaceMember).filter(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.is_active.is_(True),
            ).first()
            if not member:
                return None
            member.role = role
            self.db.commit()
            self.db.refresh(member)
            return member
        except Exception as e:
            db_logger.error(f"更新成员角色失败: workspace_id={workspace_id}, user_id={user_id} - {str(e)}")
            raise

    def deactivate_member(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Optional[WorkspaceMember]:
        try:
            member = self.db.query(WorkspaceMember).filter(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.is_active.is_(True),
            ).first()
            if not member:
                return None
            member.is_active = False
            self.db.commit()
            self.db.refresh(member)
            return member
        except Exception as e:
            db_logger.error(f"删除成员失败: workspace_id={workspace_id}, user_id={user_id} - {str(e)}")
            raise
    
    def delete_member_by_id(self, member_id: uuid.UUID) -> Optional[WorkspaceMember]:
        try:
            member = self.db.query(WorkspaceMember).filter(
                WorkspaceMember.id == member_id,
                WorkspaceMember.is_active.is_(True),
            ).first()
            if not member:
                return None
            member.is_active = False
            self.db.commit()
            self.db.refresh(member)
            return member
        except Exception as e:
            db_logger.error(f"删除成员失败: id={member_id} - {str(e)}")
            raise
    
    def update_member_role_by_id(self, id: uuid.UUID, role: WorkspaceRole) -> Optional[WorkspaceMember]:
        try:
            member = self.db.query(WorkspaceMember).filter(
                WorkspaceMember.id == id,
                WorkspaceMember.is_active.is_(True),
            ).first()
            if not member:
                return None
            member.role = role
            self.db.commit()
            self.db.refresh(member)
            return member
        except Exception as e:
            db_logger.error(f"更新成员角色失败: id={id} - {str(e)}")
            raise

# 保持向后兼容的函数
def get_workspace_by_id(db: Session, workspace_id: uuid.UUID) -> Workspace | None:
    repo = WorkspaceRepository(db)
    return repo.get_workspace_by_id(workspace_id)


def get_workspaces_by_user(db: Session, user_id: uuid.UUID) -> List[Workspace]:
    repo = WorkspaceRepository(db)
    return repo.get_workspaces_by_user(user_id)


def get_workspaces_by_tenant(db: Session, tenant_id: uuid.UUID) -> List[Workspace]:
    repo = WorkspaceRepository(db)
    return repo.get_workspaces_by_tenant(tenant_id)


def get_member_in_workspace(db: Session, user_id: uuid.UUID, workspace_id: uuid.UUID) -> WorkspaceMember | None:
    repo = WorkspaceRepository(db)
    return repo.get_member(user_id, workspace_id)


def create_workspace(db: Session, workspace: WorkspaceCreate, tenant_id: uuid.UUID) -> Workspace:
    repo = WorkspaceRepository(db)
    return repo.create_workspace(workspace, tenant_id)


def add_member_to_workspace(
    db: Session, user_id: uuid.UUID, workspace_id: uuid.UUID, role: WorkspaceRole
) -> WorkspaceMember:
    repo = WorkspaceRepository(db)
    return repo.add_member(workspace_id, user_id, role)


def get_members_by_workspace(db: Session, workspace_id: uuid.UUID) -> List[WorkspaceMember]:
    repo = WorkspaceRepository(db)
    return repo.get_members_by_workspace(workspace_id)

def get_member_by_id(db: Session, member_id: uuid.UUID) -> WorkspaceMember | None:
    repo = WorkspaceRepository(db)
    return repo.get_member_by_id(member_id)

def update_member_role_in_workspace(
    db: Session,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    role: WorkspaceRole,
) -> Optional[WorkspaceMember]:
    repo = WorkspaceRepository(db)
    return repo.update_member_role(workspace_id, user_id, role)

def remove_member_from_workspace(
    db: Session,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> Optional[WorkspaceMember]:
    repo = WorkspaceRepository(db)
    return repo.deactivate_member(workspace_id, user_id)

def remove_member_from_workspace_by_id(
    db: Session,
    member_id: uuid.UUID,
) -> Optional[WorkspaceMember]:
    repo = WorkspaceRepository(db)
    return repo.delete_member_by_id(member_id)


def update_member_role_by_id(
    db: Session,
    id: uuid.UUID,
    role: WorkspaceRole,
) -> Optional[WorkspaceMember]:
    repo = WorkspaceRepository(db)
    return repo.update_member_role_by_id(id, role)


def get_workspace_models_configs(db: Session, workspace_id: uuid.UUID) -> Optional[dict]:
    """根据workspace_id获取模型配置（llm, embedding, rerank）
    
    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        
    Returns:
        包含 llm, embedding, rerank 的字典，如果工作空间不存在则返回 None
        
    Example:
        >>> configs = get_workspace_models_configs(db, workspace_id)
        >>> if configs:
        >>>     print(f"LLM: {configs['llm']}")
        >>>     print(f"Embedding: {configs['embedding']}")
        >>>     print(f"Rerank: {configs['rerank']}")
    """
    repo = WorkspaceRepository(db)
    return repo.get_workspace_models_configs(workspace_id)
