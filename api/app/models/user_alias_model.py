import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db import Base


class UserAlias(Base):
    """用户别名表 - 存储用户的别名信息"""
    __tablename__ = "user_aliases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False, index=True)
    end_user_id = Column(UUID(as_uuid=True), ForeignKey("end_users.id"), nullable=False, index=True, comment="关联的终端用户ID")
    other_name = Column(String, nullable=False, comment="关联的用户名称")
    aliases = Column(JSONB, nullable=True, comment="用户别名列表（JSON数组）")
    meta_data = Column(JSONB, nullable=True, comment="用户相关的扩展信息（JSON格式）")
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")

    # 与 EndUser 的关系
    end_user = relationship("EndUser", back_populates="aliases")
