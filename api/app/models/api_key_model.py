"""API Key 数据模型"""
import datetime
import uuid

from sqlalchemy import Column, String, Boolean, DateTime, Integer, ForeignKey, Text, Enum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from enum import StrEnum

from app.db import Base


class ApiKeyType(StrEnum):
    """API Key 类型"""
    APP = "app"  # 应用 API Key
    RAG = "rag"  # RAG API Key
    MEMORY = "memory"  # Memory API Key


class ResourceType(StrEnum):
    """资源类型枚举"""
    AGENT = "Agent"  # 智能体
    CLUSTER = "Cluster"  # 集群
    WORKFLOW = "Workflow"  # 工作流
    KNOWLEDGE = "Knowledge"  # 知识库
    MEMORY_ENGINE = "Memory_Engine"  # 记忆引擎


class ApiKey(Base):
    """API Key 表"""
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 基本信息
    name = Column(String(255), nullable=False, comment="API Key 名称")
    description = Column(Text, comment="描述")
    key_prefix = Column(String(20), nullable=False, comment="Key 前缀")
    key_hash = Column(String(255), nullable=False, unique=True, index=True, comment="Key 哈希值")

    # 类型和权限
    type = Column(String(50), nullable=False, index=True, comment="API Key 类型")
    scopes = Column(JSONB, nullable=False, default=list, comment="权限范围列表")

    # 关联资源
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
                          index=True, comment="所属工作空间")
    resource_id = Column(UUID(as_uuid=True), index=True, comment="关联资源ID")
    resource_type = Column(String(50), comment="资源类型")

    # 限制和配额
    rate_limit = Column(Integer, default=10, comment="QPS限制（请求/秒）")
    daily_request_limit = Column(Integer, default=10000, comment="日请求限制")

    # 配额和使用统计
    quota_limit = Column(Integer, comment="配额限制（总请求数）")
    quota_used = Column(Integer, default=0, comment="已使用配额")
    usage_count = Column(Integer, default=0, comment="使用次数")
    last_used_at = Column(DateTime, comment="最后使用时间")

    # 状态管理
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")
    expires_at = Column(DateTime, comment="过期时间")

    # 审计
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, comment="创建者")
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, nullable=False, default=datetime.datetime.now, onupdate=datetime.datetime.now,
                        comment="更新时间")

    # 关系
    workspace = relationship("Workspace", back_populates="api_keys")
    creator = relationship("User", foreign_keys=[created_by])
    logs = relationship("ApiKeyLog", back_populates="api_key", cascade="all, delete-orphan")


class ApiKeyLog(Base):
    """API Key 使用日志表"""
    __tablename__ = "api_key_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    api_key_id = Column(UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True,
                        comment="API Key ID")

    # 请求信息
    endpoint = Column(String(255), nullable=False, comment="请求端点", index=True)
    method = Column(String(10), nullable=False, comment="HTTP 方法")
    ip_address = Column(String(50), comment="IP 地址")
    user_agent = Column(Text, comment="User Agent")

    # 响应信息
    status_code = Column(Integer, comment="响应状态码", index=True)
    response_time = Column(Integer, comment="响应时间（毫秒）")

    # Token 使用
    tokens_used = Column(Integer, comment="使用的 Token 数")

    # 时间
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now, index=True, comment="创建时间")

    # 关系
    api_key = relationship("ApiKey", back_populates="logs")
