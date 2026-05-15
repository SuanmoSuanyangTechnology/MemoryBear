"""
消息反馈模型（点赞/点踩）
"""
import uuid
import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base


class MessageFeedback(Base):
    """消息反馈表（点赞/点踩）
    
    支持用户对 AI 回复进行正面/负面反馈，用于：
    - 积累高质量问答数据
    - 模型迭代优化
    - 问答质量评估
    """
    __tablename__ = "message_feedbacks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=False, comment="消息ID")
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, comment="会话ID")
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, comment="工作空间ID")
    user_id = Column(String, nullable=False, comment="用户ID（EndUser 或 User）")

    # 反馈内容
    feedback_type = Column(String(20), nullable=False, comment="反馈类型: like/dislike")
    feedback_content = Column(Text, comment="反馈原因（点踩时填写）")

    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")

    # 联合唯一约束：一个用户对一条消息只能有一条反馈
    __table_args__ = (
        UniqueConstraint('message_id', 'user_id', name='uq_message_feedback'),
    )

    # 关联关系
    message = relationship("Message", back_populates="feedbacks")
    conversation = relationship("Conversation")
