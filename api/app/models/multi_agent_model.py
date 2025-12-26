"""多 Agent 相关数据模型"""
import datetime
import uuid
from enum import StrEnum

from sqlalchemy import Column, String, Boolean, DateTime, Integer, Float, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.db import Base

class OrchestrationMode(StrEnum):
    """图标类型枚举"""
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"

class AggregationStrategy(StrEnum):
    """图标类型枚举"""
    MERGE = "merge"
    VOTE = "vote"
    PRIORITY = "priority"

class MultiAgentConfig(Base):
    """多 Agent 配置表"""
    __tablename__ = "multi_agent_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 关联应用
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False, unique=True, index=True, comment="关联应用")

    # 主 Agent (存储发布版本 ID)
    master_agent_id = Column(UUID(as_uuid=True), ForeignKey("app_releases.id"), nullable=True, comment="主 Agent 发布版本 ID")
    master_agent_name = Column(String(100), comment="主 Agent 名称")

    default_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False,server_default=str(uuid.UUID(int=0)), index=True, comment="默认模型配置ID")
    # 结构化配置（直接存储 JSON）
    model_parameters = Column(JSON, nullable=True, comment="模型参数配置（temperature、max_tokens等）")
    # 协作模式
    orchestration_mode = Column(
        String(20),
        nullable=False,
        default="conditional",
        comment="协作模式: sequential|parallel|conditional|loop"
    )

    # 子 Agent 列表
    sub_agents = Column(
        JSON,
        nullable=False,
        default=list,
        comment="子 Agent 列表: [{'agent_id': 'uuid', 'name': '...', 'role': '...', 'priority': 1}]"
    )

    # 路由规则
    routing_rules = Column(
        JSON,
        comment="路由规则: [{'condition': '...', 'target_agent_id': 'uuid', 'priority': 1}]"
    )

    # 执行配置
    execution_config = Column(
        JSON,
        nullable=False,
        default=dict,
        comment="执行配置: {'max_iterations': 5, 'timeout': 60, 'parallel_limit': 3}"
    )

    # 结果整合策略
    aggregation_strategy = Column(
        String(20),
        nullable=False,
        default="merge",
        comment="结果整合策略: merge|vote|priority|custom"
    )

    # 状态
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    # 关系
    app = relationship("App")
    master_agent_release = relationship("AppRelease", foreign_keys=[master_agent_id])

    def __repr__(self):
        return f"<MultiAgentConfig(id={self.id}, app_id={self.app_id}, mode={self.orchestration_mode})>"


class AgentInvocation(Base):
    """Agent 调用记录表"""
    __tablename__ = "agent_invocations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 调用关系
    caller_agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id"),
        nullable=False,
        index=True,
        comment="调用者 Agent ID"
    )
    callee_agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id"),
        nullable=False,
        index=True,
        comment="被调用者 Agent ID"
    )

    # 关联信息
    conversation_id = Column(
        UUID(as_uuid=True),
        index=True,
        comment="关联会话 ID（不使用外键约束，避免循环依赖）"
    )
    parent_invocation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_invocations.id"),
        index=True,
        comment="父调用 ID（用于追踪调用链）"
    )

    # 输入输出
    input_message = Column(Text, nullable=False, comment="输入消息")
    output_message = Column(Text, comment="输出消息")
    context = Column(JSON, comment="上下文信息")

    # 状态
    status = Column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        comment="状态: pending|running|completed|failed"
    )
    error_message = Column(Text, comment="错误信息")

    # 性能指标
    started_at = Column(DateTime, nullable=False, default=datetime.datetime.now, index=True)
    completed_at = Column(DateTime)
    elapsed_time = Column(Float, comment="耗时（秒）")
    token_usage = Column(JSON, comment="Token 使用情况")

    # 元数据
    meta_data = Column(JSON, comment="额外元数据")

    created_at = Column(DateTime, default=datetime.datetime.now)

    # 关系
    caller = relationship("AgentConfig", foreign_keys=[caller_agent_id])
    callee = relationship("AgentConfig", foreign_keys=[callee_agent_id])
    # conversation 不使用 relationship，避免外键约束问题
    parent_invocation = relationship("AgentInvocation", remote_side=[id], backref="child_invocations")

    def __repr__(self):
        return f"<AgentInvocation(id={self.id}, caller={self.caller_agent_id}, callee={self.callee_agent_id}, status={self.status})>"
