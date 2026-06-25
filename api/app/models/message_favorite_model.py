"""
消息收藏模型
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base
from app.core.utils.datetime_utils import utcnow_naive


class MessageFavorite(Base):
    """消息收藏表

    支持用户在试运行和体验分享界面收藏 AI 消息，采用幂等设计：
    - 一个用户对一条消息最多有一条收藏记录
    - 重复点击即取消收藏
    """
    __tablename__ = "message_favorites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=False, comment="消息ID")
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, comment="会话ID")
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, comment="工作空间ID")
    user_id = Column(String, nullable=False, comment="用户ID（登录用户或 EndUser）")

    # 来源场景：pilot_run=试运行，share=体验分享
    source = Column(String(20), nullable=False, default="pilot_run", comment="来源场景: pilot_run/share")

    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")

    # 联合唯一约束：一个用户对一条消息只能有一条收藏
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_favorite"),
    )

    # 关联关系
    message = relationship("Message")
    conversation = relationship("Conversation")
