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
    
    # 状态
    is_active = Column(Boolean, default=True, nullable=False, comment="是否活跃")
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")
    
    # 关联关系
    app = relationship("App", back_populates="conversations")
    workspace = relationship("Workspace")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    """消息表"""
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # 关联信息
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, comment="会话ID")
    
    # 消息内容
    role = Column(String(20), nullable=False, comment="角色: user/assistant/system")
    content = Column(Text, nullable=False, comment="消息内容")
    
    # 元数据（避免使用 metadata 保留字）
    meta_data = Column(JSON, comment="消息元数据（如模型、token使用等）")
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    
    # 关联关系
    conversation = relationship("Conversation", back_populates="messages")
