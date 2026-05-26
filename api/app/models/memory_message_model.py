"""
记忆消息模型

平替 messages 表在滑动窗口写入流程中的功能。
仅存储来自 memory.enabled = true 应用的消息，或工作流 MemoryWriteNode 写入的消息。

Agent 对话消息和工作流 MemoryWriteNode 消息均使用 conversation_id 关联会话。
"""
import datetime
import uuid

from sqlalchemy import Column, String, Boolean, DateTime, Integer, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.db import Base


class MemoryMessage(Base):
    """记忆消息表 — 平替 messages 表在滑动窗口写入中的功能

    Agent 对话消息和工作流 MemoryWriteNode 消息均使用 conversation_id 关联会话。
    工作流本身是一个 Agent，对应一个 conversation_id；多次执行同一工作流往同一个会话存入消息。
    """
    __tablename__ = "memory_messages"
    __table_args__ = (
        Index("idx_memory_messages_conv_seq", "conversation_id", "message_seq"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 关联信息
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id"),
        nullable=False,
        comment="会话ID（Agent对话和工作流消息均使用此字段）",
    )
    original_message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id"),
        nullable=True,
        comment="原始消息ID（对 messages 表的引用，工作流消息时为 NULL）",
    )

    # 消息内容
    role = Column(String(20), nullable=False, comment="角色: user/assistant/system")
    content = Column(Text, nullable=False, comment="消息内容")

    # 消息序列编号：按 conversation_id 维度递增，从 1 开始
    message_seq = Column(
        Integer,
        nullable=False,
        comment="消息序列编号，从 1 开始，按 conversation_id 维度递增",
    )

    # 消息级记忆标记：TRUE → 触发 Write_Pipeline；FALSE → 跳过写入但 Write_Cursor 继续推进
    # 工作流 MemoryWriteNode 消息强制为 TRUE
    should_memorize = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否需要记忆写入；FALSE 时跳过 Write_Pipeline 但 write_cursor 继续推进",
    )

    # 多模态文件信息（工作流 MemoryWriteNode 传入）
    files = Column(JSON, nullable=True, comment="文件信息列表，FileInput.model_dump(mode='json')")

    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    dialog_at = Column(
        DateTime,
        nullable=True,
        comment="对话发生的实际时间（ISO 8601）。用户传入则使用，否则回退 created_at",
    )

    # 关联关系
    conversation = relationship(
        "Conversation",
        foreign_keys=[conversation_id],
        backref="memory_messages",
    )
