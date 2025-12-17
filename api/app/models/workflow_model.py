"""
工作流相关数据模型
"""

import datetime
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Integer, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.db import Base


class WorkflowConfig(Base):
    """工作流配置表"""
    __tablename__ = "workflow_configs"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # 关联应用（一对一）
    app_id = Column(
        UUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True
    )
    
    # 节点和边的定义（JSON 格式）
    nodes = Column(JSONB, nullable=False, default=list)
    edges = Column(JSONB, nullable=False, default=list)
    
    # 全局变量定义
    variables = Column(JSONB, default=list)
    
    # 执行配置
    execution_config = Column(JSONB, nullable=False, default=dict)
    
    # 触发器配置（可选）
    triggers = Column(JSONB, default=list)
    
    # 状态
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now
    )
    
    # 关系
    app = relationship("App", back_populates="workflow_config")
    executions = relationship(
        "WorkflowExecution",
        back_populates="workflow_config",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<WorkflowConfig(id={self.id}, app_id={self.app_id})>"


class WorkflowExecution(Base):
    """工作流执行记录表"""
    __tablename__ = "workflow_executions"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # 关联信息
    workflow_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    app_id = Column(
        UUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # 执行信息
    execution_id = Column(String(100), nullable=False, unique=True, index=True)
    trigger_type = Column(String(20), nullable=False)  # manual, schedule, webhook, event
    triggered_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True
    )
    
    # 输入输出
    input_data = Column(JSONB)
    output_data = Column(JSONB)
    context = Column(JSONB, default=dict)
    
    # 状态
    status = Column(String(20), nullable=False, default="pending", index=True)
    # 可选值：pending, running, completed, failed, cancelled, timeout
    
    error_message = Column(Text)
    error_node_id = Column(String(100))
    
    # 性能指标
    started_at = Column(DateTime, nullable=False, default=datetime.datetime.now, index=True)
    completed_at = Column(DateTime)
    elapsed_time = Column(Float)  # 耗时（秒）
    
    # 资源使用
    token_usage = Column(JSONB)
    
    # 元数据（使用 meta_data 避免与 SQLAlchemy 保留字 metadata 冲突）
    meta_data = Column(JSONB, default=dict)
    
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now)
    
    # 关系
    workflow_config = relationship("WorkflowConfig", back_populates="executions")
    app = relationship("App")
    conversation = relationship("Conversation")
    triggered_by_user = relationship("User", foreign_keys=[triggered_by])
    node_executions = relationship(
        "WorkflowNodeExecution",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="WorkflowNodeExecution.execution_order"
    )
    
    def __repr__(self):
        return f"<WorkflowExecution(id={self.id}, execution_id={self.execution_id}, status={self.status})>"


class WorkflowNodeExecution(Base):
    """工作流节点执行记录表"""
    __tablename__ = "workflow_node_executions"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # 关联执行
    execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # 节点信息
    node_id = Column(String(100), nullable=False, index=True)
    node_type = Column(String(20), nullable=False)
    node_name = Column(String(100))
    
    # 执行顺序
    execution_order = Column(Integer, nullable=False)
    retry_count = Column(Integer, nullable=False, default=0)
    
    # 输入输出
    input_data = Column(JSONB)
    output_data = Column(JSONB)
    
    # 状态
    status = Column(String(20), nullable=False, default="pending", index=True)
    # 可选值：pending, running, completed, failed, skipped, cached
    
    error_message = Column(Text)
    
    # 性能指标
    started_at = Column(DateTime, nullable=False, default=datetime.datetime.now)
    completed_at = Column(DateTime)
    elapsed_time = Column(Float)  # 耗时（秒）
    
    # 资源使用（针对 LLM 节点）
    token_usage = Column(JSONB)
    
    # 缓存信息
    cache_hit = Column(Boolean, default=False)
    cache_key = Column(String(255))
    
    # 元数据（使用 meta_data 避免与 SQLAlchemy 保留字 metadata 冲突）
    meta_data = Column(JSONB, default=dict)
    
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now)
    
    # 关系
    execution = relationship("WorkflowExecution", back_populates="node_executions")
    
    def __repr__(self):
        return f"<WorkflowNodeExecution(id={self.id}, node_id={self.node_id}, status={self.status})>"
