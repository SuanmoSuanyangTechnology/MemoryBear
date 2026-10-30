"""Data models related to multi-agent"""
import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import Column, String, Boolean, DateTime, Integer, Float, Text, ForeignKey, TypeDecorator
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from .pydantic_type import PydanticType
from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive
from .model_parameters import ModelParameters


class OrchestrationMode(StrEnum):
    """Collaboration mode enum"""
    COLLABORATION = "collaboration"      # Collaboration mode: agents can hand off to each other
    SUPERVISOR = "supervisor"            # Supervisor mode: route -> parallel execution -> aggregation (three-stage)
    SUPERVISOR_LOOP = "supervisor_loop"  # Supervisor loop mode: supervisor = ReAct engine, sub-agents = tools

class AggregationStrategy(StrEnum):
    """Icon type enum"""
    MERGE = "merge"
    VOTE = "vote"
    PRIORITY = "priority"

# class PydanticType(TypeDecorator):
#     impl = JSON

#     def __init__(self, pydantic_model: type[BaseModel]):
#         super().__init__()
#         self.model = pydantic_model

#     def process_bind_param(self, value, dialect):
#         # On write: Model -> dict
#         if value is None:
#             return None
#         if isinstance(value, self.model):
#             return value.dict()
#         return value   # pass through values that are already dict

#     def process_result_value(self, value, dialect):
#         # On read: dict -> Model
#         if value is None:
#             return None
#         # return self.model.parse_obj(value)  # pydantic v1
#         return self.model.model_validate(value)  # pydantic v2

class MultiAgentConfig(Base):
    """Multi-agent config table"""
    __tablename__ = "multi_agent_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # Associated app
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False, unique=True, index=True, comment="关联应用")

    # Master agent (stores the release version ID)
    master_agent_id = Column(UUID(as_uuid=True), ForeignKey("app_releases.id"), nullable=True, comment="主 Agent 发布版本 ID")
    master_agent_name = Column(String(100), comment="主 Agent 名称")

    default_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id", name="multi_agent_configs_default_model_config_id_fkey"), nullable=True, index=True, comment="默认模型配置ID")
    # Structured configuration (stored directly as JSON)
    model_parameters = Column(PydanticType(ModelParameters), nullable=True, comment="模型参数配置（temperature、max_tokens等）")
    # Collaboration mode
    orchestration_mode = Column(
        String(20),
        nullable=False,
        # New configs default to supervisor-loop mode (Python-side default only, no server_default, no migration)
        default="supervisor_loop",
        comment="协作模式: collaboration（协作）| supervisor（主管三段式）| supervisor_loop（主管 ReAct 循环，默认）"
    )

    # Sub-agent list
    sub_agents = Column(
        JSON,
        nullable=False,
        default=list,
        comment="子 Agent 列表: [{'agent_id': 'uuid', 'name': '...', 'role': '...'}]"
    )

    # Routing rules
    routing_rules = Column(
        JSON,
        comment="路由规则: [{'condition': '...', 'target_agent_id': 'uuid', 'priority': 1}]"
    )

    # Execution config
    execution_config = Column(
        JSON,
        nullable=False,
        default=dict,
        comment="执行配置: {'max_iterations': 5, 'timeout': 60, 'parallel_limit': 3}"
    )

    # Supervisor config (the supervisor is an agent): in supervisor_loop mode, the
    # supervisor's own capability surface, structured like the features shape of a
    # single-agent app. Nullable -- NULL/missing key = all defaults = current behavior
    # (bare supervisor: hardcoded prompt + SubAgentTool only); no migration, no behavior change.
    # Note the boundary with execution_config: that field governs "execution guardrails"
    # (timeout/retry/limits), while this field governs "what the supervisor is"
    # (prompt/tool surface/web search/memory).
    # Cluster variable definitions (formerly the top-level variables column) are also
    # folded into this field's variables key -- cluster config no longer has a separate
    # variables field; NULL/missing key/empty = falls back to the union of sub-agent
    # variables (collect_variable_definitions).
    supervisor_config = Column(
        JSON,
        nullable=True,
        comment=(
            "主管配置（全模式；supervisor_loop 下还含主管本体能力面）: "
            "{'system_prompt': str, 'web_search': bool, 'memory': {'enabled': bool}, "
            "'knowledge_retrieval': {...}, 'tools': [...], 'skills': {...}, "
            "'variables': [{'name', 'display_name', 'type', 'required', 'default_value', ...}]}"
        ),
    )

    # Result aggregation strategy
    aggregation_strategy = Column(
        String(20),
        nullable=False,
        default="merge",
        comment="结果整合策略: merge|vote|priority|custom"
    )

    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    # Relationships
    app = relationship("App")
    master_agent_release = relationship("AppRelease", foreign_keys=[master_agent_id])

    def __repr__(self):
        return f"<MultiAgentConfig(id={self.id}, app_id={self.app_id}, mode={self.orchestration_mode})>"


class AgentInvocation(Base):
    """Agent invocation record table"""
    __tablename__ = "agent_invocations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # Invocation relationship
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

    # Association info
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

    # Input/output
    input_message = Column(Text, nullable=False, comment="输入消息")
    output_message = Column(Text, comment="输出消息")
    context = Column(JSON, comment="上下文信息")

    # Status
    status = Column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        comment="状态: pending|running|completed|failed"
    )
    error_message = Column(Text, comment="错误信息")

    # Performance metrics
    started_at = Column(DateTime, nullable=False, default=utcnow_naive, index=True)
    completed_at = Column(DateTime)
    elapsed_time = Column(Float, comment="耗时（秒）")
    token_usage = Column(JSON, comment="Token 使用情况")

    # Metadata
    meta_data = Column(JSON, comment="额外元数据")

    created_at = Column(DateTime, default=utcnow_naive)

    # Relationships
    caller = relationship("AgentConfig", foreign_keys=[caller_agent_id])
    callee = relationship("AgentConfig", foreign_keys=[callee_agent_id])
    # No relationship for conversation, to avoid foreign key constraint issues
    parent_invocation = relationship("AgentInvocation", remote_side=[id], backref="child_invocations")

    def __repr__(self):
        return f"<AgentInvocation(id={self.id}, caller={self.caller_agent_id}, callee={self.callee_agent_id}, status={self.status})>"
