"""
会话分享模型
"""
import uuid
import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base
from app.core.utils.datetime_utils import utcnow_naive


class ConversationShare(Base):
    """会话分享表
    
    支持生成只读公开链接，他人点击即可查看完整对话内容。
    """
    __tablename__ = "conversation_shares"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, comment="会话ID")
    share_uuid = Column(String(36), unique=True, nullable=False, comment="分享唯一标识")
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, comment="工作空间ID")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="创建人ID")

    # 访问控制
    password = Column(String(100), comment="访问密码（可选）")
    expire_at = Column(DateTime, comment="过期时间")
    allow_copy = Column(Boolean, default=True, comment="是否允许复制内容")

    # 统计
    view_count = Column(Integer, default=0, comment="访问次数")

    is_active = Column(Boolean, default=True, nullable=False, comment="是否有效")
    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")

    # 关联关系
    conversation = relationship("Conversation", back_populates="shares")
