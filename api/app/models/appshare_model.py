import datetime
import uuid
from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
from sqlalchemy.orm import relationship


class AppShare(Base):
    """应用分享模型
    
    记录应用从一个工作空间分享到另一个工作空间的关系
    """
    __tablename__ = "app_shares"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    source_app_id = Column(UUID(as_uuid=True), ForeignKey('apps.id', ondelete='CASCADE'), nullable=False, comment="源应用ID")
    source_workspace_id = Column(UUID(as_uuid=True), ForeignKey('workspaces.id'), nullable=False, comment="源工作空间ID")
    target_workspace_id = Column(UUID(as_uuid=True), ForeignKey('workspaces.id'), nullable=False, comment="目标工作空间ID")
    shared_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False, comment="分享者用户ID")
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now)

    # Relationships
    source_app = relationship("App", foreign_keys=[source_app_id], backref="shares")
    source_workspace = relationship("Workspace", foreign_keys=[source_workspace_id])
    target_workspace = relationship("Workspace", foreign_keys=[target_workspace_id])
    shared_user = relationship("User", backref="app_shares")
