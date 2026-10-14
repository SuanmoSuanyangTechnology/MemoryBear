"""
Workflow-related data models
"""

import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Integer, BigInteger, Float, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive


class WorkflowConfig(Base):
    """Workflow config table"""
    __tablename__ = "workflow_configs"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # Associated app (one-to-one)
    app_id = Column(
        UUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True
    )
    
    # Workflow type: pure_workflow (pure workflow) | workflow (conversational workflow)
    workflow_type = Column(String(20), nullable=False, server_default="workflow", index=True)
    
    # Node and edge definitions (JSON format)
    nodes = Column(JSONB, nullable=False, default=list)
    edges = Column(JSONB, nullable=False, default=list)
    
    # Global variable definitions
    variables = Column(JSONB, default=list)
    environment_variables = Column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    
    # Execution config
    execution_config = Column(JSONB, nullable=False, default=dict)
    features = Column(JSONB, nullable=True, default=dict)
    
    # Trigger config (optional)
    triggers = Column(JSONB, default=list)
    
    # Status
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
        onupdate=utcnow_naive
    )
    
    # Relationships
    app = relationship("App", back_populates="workflow_config")
    executions = relationship(
        "WorkflowExecution",
        back_populates="workflow_config",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<WorkflowConfig(id={self.id}, app_id={self.app_id})>"


class WorkflowExecution(Base):
    """Workflow execution record table"""
    __tablename__ = "workflow_executions"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # Association info
    workflow_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_releases.id", ondelete="CASCADE"),
        nullable=True,
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
    
    # Execution info
    execution_id = Column(String(100), nullable=False, unique=True, index=True)
    trigger_type = Column(String(20), nullable=False)  # manual, schedule, webhook, event
    triggered_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True
    )
    
    # Input/output
    input_data = Column(JSONB)
    output_data = Column(JSONB)
    context = Column(JSONB, default=dict)
    
    # Status
    status = Column(String(20), nullable=False, default="pending", index=True)
    # Possible values: pending, running, completed, failed, cancelled, timeout
    
    error_message = Column(Text)
    error_node_id = Column(String(100))
    
    # Performance metrics
    started_at = Column(DateTime, nullable=False, default=utcnow_naive, index=True)
    completed_at = Column(DateTime)
    elapsed_time = Column(Float)  # Elapsed time (seconds)
    
    # Resource usage
    token_usage = Column(JSONB)
    
    # Metadata (meta_data avoids clashing with the SQLAlchemy reserved word metadata)
    meta_data = Column(JSONB, default=dict)
    
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    
    # Relationships
    workflow_config = relationship("WorkflowConfig", back_populates="executions")
    app = relationship("App")
    conversation = relationship("Conversation")
    # Cross-domain relationship omitted: target model is host-owned and not part of this service's model set (FK column retained).
    node_executions = relationship(
        "WorkflowNodeExecution",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="WorkflowNodeExecution.execution_order"
    )
    
    def __repr__(self):
        return f"<WorkflowExecution(id={self.id}, execution_id={self.execution_id}, status={self.status})>"


class WorkflowNodeExecution(Base):
    """Workflow node execution record table

    Retention policy: this table keeps only the most recent execution detail, no history.
      - Full workflow: all node rows of the latest 1 execution (by execution start time)
        under the same app_id + workflow_config_id (including waiting_human; an older
        human-in-the-loop execution resuming later does not overwrite the newer execution)
      - Single-node debug: the latest 1 row under the same app_id + workflow_config_id + node_id
    The authoritative source of historical node details is workflow_executions.output_data["node_outputs"].
    Controlled by settings.WORKFLOW_NODE_EXECUTION_RETENTION_ENABLED.
    """
    __tablename__ = "workflow_node_executions"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # Associated execution (set for full-graph runs, may be empty for single-node debug)
    execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_executions.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    app_id = Column(
        UUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Node info
    node_id = Column(String(100), nullable=False, index=True)
    node_type = Column(String(20), nullable=False)
    node_name = Column(String(100))
    
    # Execution order
    execution_order = Column(BigInteger, nullable=False)
    retry_count = Column(Integer, nullable=False, default=0)
    
    # Input/output
    input_data = Column(JSONB)
    output_data = Column(JSONB)
    
    # Status
    status = Column(String(20), nullable=False, default="pending", index=True)
    # Possible values: pending, running, completed, failed, skipped, cached
    
    error_message = Column(Text)
    
    # Performance metrics
    started_at = Column(DateTime, nullable=False, default=utcnow_naive)
    completed_at = Column(DateTime)
    elapsed_time = Column(Float)  # Elapsed time (seconds)
    
    # Resource usage (for LLM nodes)
    token_usage = Column(JSONB)
    
    # Cache info
    cache_hit = Column(Boolean, default=False)
    cache_key = Column(String(255))
    
    # Metadata (meta_data avoids clashing with the SQLAlchemy reserved word metadata)
    meta_data = Column(JSONB, default=dict)
    
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    
    # Relationships
    execution = relationship("WorkflowExecution", back_populates="node_executions")
    
    def __repr__(self):
        return f"<WorkflowNodeExecution(id={self.id}, node_id={self.node_id}, status={self.status})>"


class WorkflowNodeCache(Base):
    """Workflow node cache table"""

    __tablename__ = "workflow_node_caches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    app_id = Column(
        UUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_configs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    node_id = Column(String(100), nullable=False, index=True)
    node_type = Column(String(50), nullable=False)
    node_name = Column(String(100))
    cache_key = Column(String(64), nullable=False, index=True)
    source = Column(String(32), nullable=False, default="workflow_execution")

    status = Column(String(20), nullable=False, default="active", index=True)
    input_data = Column(JSONB)
    result_data = Column(JSONB)
    hit_count = Column(Integer, nullable=False, default=0)
    last_hit_at = Column(DateTime)
    expires_at = Column(DateTime)
    invalidated_at = Column(DateTime)
    meta_data = Column(JSONB, default=dict)

    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
        onupdate=utcnow_naive
    )

    app = relationship("App")
    workflow_config = relationship("WorkflowConfig")

    def __repr__(self):
        return f"<WorkflowNodeCache(id={self.id}, node_id={self.node_id}, status={self.status})>"
