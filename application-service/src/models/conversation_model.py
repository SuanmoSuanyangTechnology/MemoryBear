"""
Conversation and message models
"""
import uuid

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Boolean, Integer,
    Text, JSON, BigInteger, UniqueConstraint, Index, text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..utils.datetime_utils import utcnow_naive
from .base import ServiceBase as Base


class Conversation(Base):
    """Conversation table

    Conversation types:
    - Draft conversation (is_draft=True): uses the app's current draft config, for development and testing
    - Release conversation (is_draft=False): uses the app's current release config, for production

    Workspace isolation:
    - Each conversation belongs to one workspace (workspace_id)
    - The same app has independent conversation records in different workspaces
    - Supports conversation isolation after an app is shared
    """
    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_conversations_user_updated", "user_id", "updated_at"),
        Index(
            "idx_conversations_app_user",
            "app_id", "user_id", "is_draft",
            postgresql_where=text("is_active = TRUE"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # Association info
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False, comment="应用ID")
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, comment="工作空间ID")
    user_id = Column(String, nullable=True, comment="用户ID（外部系统）")

    # Conversation info
    title = Column(String(255), comment="会话标题")
    summary = Column(Text, comment="会话摘要")

    # Conversation type: True=draft conversation (uses draft config), False=release conversation (uses release config)
    is_draft = Column(Boolean, default=True, nullable=False, comment="是否为草稿会话")

    # Config snapshot: stores the full config at conversation creation, for auditing and troubleshooting
    config_snapshot = Column(JSON, comment="配置快照（Agent配置、模型配置等）")

    # Statistics
    message_count = Column(Integer, default=0, comment="消息数量")

    # Sliding-window write cursor: message_seq of the last processed message in the
    # memory_messages table; also advances when should_memorize=FALSE
    write_cursor = Column(Integer, nullable=False, default=0, server_default="0",
                          comment="最后一条已处理的 memory_messages 表中消息的 message_seq；should_memorize=FALSE 时也推进")

    # Status
    is_active = Column(Boolean, default=True, nullable=False, comment="是否活跃")

    # Timestamps
    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, comment="更新时间")

    # Relationships
    app = relationship("App", back_populates="conversations")
    # Cross-domain relationship omitted: target model is host-owned and not part of this service's model set (FK column retained).
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    shares = relationship("ConversationShare", back_populates="conversation", cascade="all, delete-orphan")


class ConversationDetail(Base):
    __tablename__ = "conversation_details"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"))

    theme = Column(String, comment="会话主题")
    summary = Column(String, comment="会话摘要")
    takeaways = Column(JSON, comment="会话要点")
    question = Column(JSON, comment="用户问题")
    info_score = Column(Integer, comment="会话信息量评分")


class Message(Base):
    """Message table"""
    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "idx_messages_conv_created",
            "conversation_id",
            text("created_at DESC"),
            postgresql_where=text("is_deleted = FALSE AND is_current = TRUE"),
        ),
        Index(
            "idx_messages_parent",
            "parent_message_id",
            postgresql_where=text("parent_message_id IS NOT NULL AND is_deleted = FALSE"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # Association info
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, index=True, comment="会话ID")

    # Message content
    role = Column(String(20), nullable=False, comment="角色: user/assistant/system")
    content = Column(Text, nullable=False, comment="消息内容")

    # === Versioning support (regenerate feature) ===
    version = Column(Integer, server_default="1", default=1, comment="版本号（重新生成时递增）")
    is_current = Column(Boolean, server_default="true", default=True, comment="是否为当前展示版本")
    parent_message_id = Column(UUID(as_uuid=True), comment="父消息ID（用于重新生成时关联原用户消息）")

    # === Soft delete ===
    is_deleted = Column(Boolean, server_default="false", default=False, comment="逻辑删除标记")

    # === Statistics fields (denormalized for easy querying) ===
    like_count = Column(Integer, server_default="0", default=0, comment="点赞数")
    dislike_count = Column(Integer, server_default="0", default=0, comment="点踩数")
    report_count = Column(Integer, server_default="0", default=0, comment="举报数")

    # Metadata (avoid the reserved word metadata)
    meta_data = Column(JSON, comment="消息元数据（如模型、token使用等）")

    # Status
    status = Column(String(20), nullable=False, server_default="completed", comment="消息状态: completed/failed")

    # Timestamps
    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    feedbacks = relationship("MessageFeedback", back_populates="message", cascade="all, delete-orphan")
    reports = relationship("MessageReport", back_populates="message", cascade="all, delete-orphan")


class ConversationContextState(Base):
    """Conversation context state table.

    Persists the summary text and business boundaries of the commercial-edition context engine.
    """

    __tablename__ = "conversation_context_state"
    __table_args__ = (
        UniqueConstraint("conversation_id", "scope_key", name="uq_conversation_context_state_scope"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, comment="会话ID")
    scope_key = Column(String(255), nullable=False, comment="上下文作用域键")
    source_type = Column(String(32), nullable=False, comment="上下文来源类型：conversation/workflow")
    summary_text = Column(Text, nullable=True, comment="滚动摘要文本")
    summarized_until_message_id = Column(UUID(as_uuid=True), nullable=True, comment="应用聊天摘要边界消息ID")
    summarized_until_at = Column(DateTime, nullable=True, comment="应用聊天摘要边界时间")
    summarized_until_seq = Column(BigInteger, nullable=True, comment="工作流摘要边界序号")
    created_at = Column(DateTime, default=utcnow_naive, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False, comment="更新时间")
