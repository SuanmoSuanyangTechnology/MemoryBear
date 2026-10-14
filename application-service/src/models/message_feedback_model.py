"""
Message feedback model (like/dislike)
"""
import uuid
import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive


class MessageFeedback(Base):
    """Message feedback table (like/dislike)

    Supports positive/negative feedback from users on AI replies, used for:
    - Accumulating high-quality QA data
    - Model iteration and optimization
    - QA quality assessment
    """
    __tablename__ = "message_feedbacks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=False, comment="消息ID")
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, comment="会话ID")
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, comment="工作空间ID")
    user_id = Column(String, nullable=False, comment="用户ID（EndUser 或 User）")

    # Feedback content
    feedback_type = Column(String(20), nullable=True, comment="反馈类型: like/dislike；仅收藏行为 NULL")
    feedback_content = Column(Text, comment="反馈原因（点踩时填写）")
    # Favorite flag: coexists with like/dislike in the same row (at most one row per user per message)
    is_favorite = Column(Boolean, nullable=False, default=False, server_default="false", comment="是否收藏")

    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")

    # Composite unique constraint: one user can have only one feedback per message
    __table_args__ = (
        UniqueConstraint('message_id', 'user_id', name='uq_message_feedback'),
    )

    # Relationships
    message = relationship("Message", back_populates="feedbacks")
    conversation = relationship("Conversation")
