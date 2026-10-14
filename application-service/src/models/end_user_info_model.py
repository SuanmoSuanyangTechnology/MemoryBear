import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, ARRAY
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive


class EndUserInfo(Base):
    """End user info table - stores user aliases and extended info"""
    __tablename__ = "end_user_info"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False, index=True)
    end_user_id = Column(UUID(as_uuid=True), ForeignKey("end_users.id"), nullable=False, index=True, comment="关联的终端用户ID")
    other_name = Column(String, nullable=False, comment="关联的用户名称")
    aliases = Column(ARRAY(String), nullable=True, comment="用户别名列表（字符串数组）")
    meta_data = Column(JSONB, nullable=True, comment="用户相关的扩展信息（JSON格式）")
    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, comment="更新时间")

    # Relationship with EndUser
    end_user = relationship("EndUser", back_populates="info")
