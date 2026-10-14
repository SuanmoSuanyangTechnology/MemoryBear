import datetime
import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive
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
    permission = Column(String, default="readonly", nullable=False, comment="权限模式: readonly | editable")
    is_active = Column(Boolean, default=True, server_default='true', nullable=False, comment="是否有效，False 表示逻辑删除")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive)

    # Relationships
    source_app = relationship("App", foreign_keys=[source_app_id], backref="shares")
    # Cross-domain relationship omitted: target model is host-owned and not part of this service's model set (FK column retained).
    # Cross-domain relationship omitted: target model is host-owned and not part of this service's model set (FK column retained).
    # Cross-domain relationship omitted: target model is host-owned and not part of this service's model set (FK column retained).
