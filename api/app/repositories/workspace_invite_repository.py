from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from typing import List, Optional
import datetime
import uuid

from app.models.workspace_model import WorkspaceInvite, InviteStatus
from app.schemas.workspace_schema import WorkspaceInviteCreate


class WorkspaceInviteRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_invite(
        self, 
        workspace_id: uuid.UUID, 
        invite_data: WorkspaceInviteCreate, 
        token_hash: str,
        created_by_user_id: uuid.UUID
    ) -> WorkspaceInvite:
        """创建工作空间邀请"""
        expires_at = datetime.datetime.now() + datetime.timedelta(days=invite_data.expires_in_days)
        
        db_invite = WorkspaceInvite(
            workspace_id=workspace_id,
            email=invite_data.email,
            role=invite_data.role,
            token_hash=token_hash,
            status=InviteStatus.pending,
            expires_at=expires_at,
            created_by_user_id=created_by_user_id
        )
        
        self.db.add(db_invite)
        self.db.commit()
        self.db.refresh(db_invite)
        return db_invite

    def get_invite_by_token_hash(self, token_hash: str) -> Optional[WorkspaceInvite]:
        """根据令牌哈希获取邀请"""
        return self.db.query(WorkspaceInvite).filter(
            WorkspaceInvite.token_hash == token_hash
        ).first()

    def get_invite_by_id(self, invite_id: uuid.UUID) -> Optional[WorkspaceInvite]:
        """根据ID获取邀请"""
        return self.db.query(WorkspaceInvite).filter(
            WorkspaceInvite.id == invite_id
        ).first()

    def get_workspace_invites(
        self, 
        workspace_id: uuid.UUID, 
        status: Optional[InviteStatus] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[WorkspaceInvite]:
        """获取工作空间的邀请列表"""
        query = self.db.query(WorkspaceInvite).filter(
            WorkspaceInvite.workspace_id == workspace_id
        )
        
        if status:
            query = query.filter(WorkspaceInvite.status == status)
            
        return query.order_by(WorkspaceInvite.created_at.desc()).offset(offset).limit(limit).all()

    def get_pending_invite_by_email_and_workspace(
        self, 
        email: str, 
        workspace_id: uuid.UUID
    ) -> Optional[WorkspaceInvite]:
        """获取指定邮箱在指定工作空间的待处理邀请"""
        return self.db.query(WorkspaceInvite).filter(
            and_(
                WorkspaceInvite.email == email,
                WorkspaceInvite.workspace_id == workspace_id,
                WorkspaceInvite.status == InviteStatus.pending
            )
        ).first()

    def update_invite_status(
        self, 
        invite_id: uuid.UUID, 
        status: InviteStatus,
        accepted_at: Optional[datetime.datetime] = None
    ) -> Optional[WorkspaceInvite]:
        """更新邀请状态"""
        invite = self.get_invite_by_id(invite_id)
        if invite:
            invite.status = status
            if accepted_at:
                invite.accepted_at = accepted_at
            invite.updated_at = datetime.datetime.now()
            self.db.commit()
            self.db.refresh(invite)
        return invite

    def revoke_invite(self, invite_id: uuid.UUID) -> Optional[WorkspaceInvite]:
        """撤销邀请"""
        return self.update_invite_status(invite_id, InviteStatus.revoked)

    def expire_old_invites(self) -> int:
        """将过期的邀请标记为已过期"""
        now = datetime.datetime.now()
        expired_count = self.db.query(WorkspaceInvite).filter(
            and_(
                WorkspaceInvite.status == InviteStatus.pending,
                WorkspaceInvite.expires_at < now
            )
        ).update(
            {
                WorkspaceInvite.status: InviteStatus.expired,
                WorkspaceInvite.updated_at: now
            }
        )
        self.db.commit()
        return expired_count

    def count_workspace_invites(
        self, 
        workspace_id: uuid.UUID, 
        status: Optional[InviteStatus] = None
    ) -> int:
        """统计工作空间邀请数量"""
        query = self.db.query(WorkspaceInvite).filter(
            WorkspaceInvite.workspace_id == workspace_id
        )
        
        if status:
            query = query.filter(WorkspaceInvite.status == status)
            
        return query.count()