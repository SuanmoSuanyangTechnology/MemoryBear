"""
记忆消息模型

平替 messages 表在滑动窗口写入流程中的功能。
仅存储来自 memory.enabled = true 应用的消息，或工作流 MemoryWriteNode 写入的消息。

Agent 对话和工作流 MemoryWriteNode 通常使用 conversation_id 关联会话；
API Service / MCP 直接写入时 conversation_id 为 NULL，用 (end_user_id, source)
标识归属，详见 api/docs/v0.3.11/2-设计文档-评审.md。
"""
import uuid

from sqlalchemy import Column, String, Boolean, DateTime, Integer, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.db import Base
from app.core.utils.datetime_utils import utcnow_naive


class MemoryMessage(Base):
    """记忆消息表 — 平替 messages 表在滑动窗口写入中的功能

    写入来源分两类：
    - agent / workflow：走 conversation_id 通道，seq 按 conversation_id 分组编号；
      end_user_id 取自 conversation.user_id。
    - service_api / mcp：conversation_id=NULL，用 (end_user_id, source) 标识归属，
      seq 按 (end_user_id, source) 分组编号，两条 seq 序列彼此独立。

    end_user_id 为字符串且 NOT NULL——记忆必须归属到某个终端用户，
    类型与 conversation.user_id / 各 schema 的 end_user_id 保持一致。
    """
    __tablename__ = "memory_messages"
    __table_args__ = (
        Index("idx_memory_messages_conv_seq", "conversation_id", "message_seq"),
        Index("idx_memory_messages_user_source", "end_user_id", "source"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 关联信息
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id"),
        nullable=True,
        comment="会话 ID（agent/workflow 写入时非空；API/MCP 写入时为 NULL）",
    )
    original_message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id"),
        nullable=True,
        comment="原始消息 ID（对 messages 表的引用；工作流/API/MCP 消息为 NULL）",
    )

    # 归属与来源标识（用于替代哨兵 App 机制）
    end_user_id = Column(
        String,
        nullable=False,
        comment="终端用户 ID（字符串，与 conversation.user_id / 各 schema 的 end_user_id 对齐）；所有写入路径必填",
    )
    source = Column(
        String(32),
        nullable=False,
        default="agent",
        comment="写入来源：agent | service_api | mcp | workflow",
    )

    # 消息内容
    role = Column(String(20), nullable=False, comment="角色: user/assistant/system")
    content = Column(Text, nullable=False, comment="消息内容")

    # 消息序列编号：分组规则见类 docstring
    message_seq = Column(
        Integer,
        nullable=False,
        comment="消息序列编号，从 1 开始，分组键取决于 source：agent/workflow 按 conversation_id；service_api/mcp 按 (end_user_id, source)",
    )

    # 消息级记忆标记：TRUE → 触发 Write_Pipeline；FALSE → 跳过写入但 write_cursor 继续推进
    # 工作流 MemoryWriteNode 消息强制为 TRUE
    should_memorize = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否需要记忆写入；FALSE 时跳过 Write_Pipeline 但 write_cursor 继续推进",
    )

    # 多模态文件信息（工作流 MemoryWriteNode 传入）
    files = Column(JSON, nullable=True, comment="文件信息列表，FileInput.model_dump(mode='json')")

    # 业务侧会话发生时间（ISO 8601 字符串），由 API 调用方提供。
    # 与 created_at 的区别：created_at 是入库时刻，dialog_at 是会话真正发生的时刻。
    # 用于温度衰减、时间相关萃取（dialog_at 进入 jinja2 prompt 锚定相对时间）。
    dialog_at = Column(
        String(64),
        nullable=True,
        comment="业务侧会话发生时间（ISO 8601），调用方未提供时为 NULL",
    )

    # 时间戳
    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")

    # 关联关系
    conversation = relationship(
        "Conversation",
        foreign_keys=[conversation_id],
        backref="memory_messages",
    )
