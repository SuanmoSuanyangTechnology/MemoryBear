"""
会话和消息模型
"""
import uuid
import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Integer, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base


class Conversation(Base):
    """会话表
    
    会话类型说明：
    - 草稿会话 (is_draft=True): 使用应用的当前草稿配置，用于开发和测试
    - 发布会话 (is_draft=False): 使用应用的当前发布版本配置，用于生产环境
    
    工作空间隔离：
    - 每个会话属于一个工作空间（workspace_id）
    - 同一个应用在不同工作空间有独立的会话记录
    - 支持应用分享后的会话隔离
    """
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 关联信息
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False, comment="应用ID")
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, comment="工作空间ID")
    user_id = Column(String, nullable=True, comment="用户ID（外部系统）")

    # 会话信息
    title = Column(String(255), comment="会话标题")
    summary = Column(Text, comment="会话摘要")

    # 会话类型：True=草稿会话（使用草稿配置），False=发布会话（使用发布配置）
    is_draft = Column(Boolean, default=True, nullable=False, comment="是否为草稿会话")

    # 配置快照：保存创建会话时的完整配置，用于审计和问题追溯
    config_snapshot = Column(JSON, comment="配置快照（Agent配置、模型配置等）")

    # 统计信息
    message_count = Column(Integer, default=0, comment="消息数量")

    # 滑动窗口写入游标：最后一条已处理的 memory_messages 表中消息的 message_seq；should_memorize=FALSE 时也推进
    write_cursor = Column(Integer, nullable=False, default=0, comment="最后一条已处理的 memory_messages 表中消息的 message_seq；should_memorize=FALSE 时也推进")

    # 状态
    is_active = Column(Boolean, default=True, nullable=False, comment="是否活跃")

    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")

    # 关联关系
    app = relationship("App", back_populates="conversations")
    workspace = relationship("Workspace")
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
    """消息表"""
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 关联信息
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, comment="会话ID")

    # 消息内容
    role = Column(String(20), nullable=False, comment="角色: user/assistant/system")
    content = Column(Text, nullable=False, comment="消息内容")

    # === 版本化支持（重新生成功能） ===
    version = Column(Integer, default=1, comment="版本号（重新生成时递增）")
    is_current = Column(Boolean, default=True, comment="是否为当前展示版本")
    parent_message_id = Column(UUID(as_uuid=True), comment="父消息ID（用于重新生成时关联原用户消息）")

    # === 逻辑删除 ===
    is_deleted = Column(Boolean, default=False, comment="逻辑删除标记")

    # === 统计字段（冗余，便于查询） ===
    like_count = Column(Integer, default=0, comment="点赞数")
    dislike_count = Column(Integer, default=0, comment="点踩数")
    report_count = Column(Integer, default=0, comment="举报数")

    # 元数据（避免使用 metadata 保留字）
    meta_data = Column(JSON, comment="消息元数据（如模型、token使用等）")

    # 状态
    status = Column(String(20), nullable=False, server_default="completed", comment="消息状态: completed/failed")

    # 消息顺序编号：从 1 开始，按 created_at 升序（同 created_at 时以 id 升序）；新消息由应用层在插入事务中赋值
    message_seq = Column(Integer, nullable=True, comment="消息在对话中的顺序编号，从 1 开始，按 created_at 升序（同 created_at 时以 id 升序）")

    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")

    # 关联关系
    conversation = relationship("Conversation", back_populates="messages")
    feedbacks = relationship("MessageFeedback", back_populates="message", cascade="all, delete-orphan")
    reports = relationship("MessageReport", back_populates="message", cascade="all, delete-orphan")
