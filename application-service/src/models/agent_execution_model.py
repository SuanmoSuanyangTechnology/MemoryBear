"""
Agent execution record model

Records the internal execution steps of each conversation round in an Agent app
(non-workflow), including intermediate processes such as tool calls and LLM reasoning.
"""

import datetime
import uuid

from sqlalchemy import Column, String, DateTime, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive



class AgentExecution(Base):
    """Agent execution record table

    Each row corresponds to one Agent conversation round (one user→assistant turn);
    the steps column stores all intermediate steps of the Agent in that round as a JSONB array.
    """
    __tablename__ = "agent_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # Association info
    app_id = Column(
        UUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联的 assistant 消息 ID"
    )
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="执行时使用的 Agent 配置 ID"
    )
    release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_releases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="执行时使用的发布版本 ID（试运行时为 NULL）"
    )
    triggered_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="触发执行的用户 ID"
    )

    # Execution steps (JSONB array)
    # [
    #   {
    #     "node_type": "tool" | "llm" | "reasoning",
    #     "node_name": "knowledge_retrieval",
    #     "status": "completed" | "failed",
    #     "input": "...",
    #     "output": "...",
    #     "elapsed_time": 123.4,  (ms)
    #     "error": null
    #   }
    # ]
    steps = Column(JSONB, nullable=False, default=list)

    # Agent execution trace (AgentTraceRecorder.to_dict snapshot)
    # {
    #   "meta": {...},
    #   "iterations": [
    #     {"llm": {model, input, output, reasoning_content, tokens, elapsed_time},
    #      "tool_calls": [{tool_name, tool_input, tool_output, ...}]}
    #   ]
    # }
    # Structurally identical to agent_log in the workflow agent node's output_data:
    # once a sub-agent is folded into a node with node_type='agent' + agent_log=<this field>,
    # the frontend Runtime.tsx renders it like a workflow agent node (ROUND / llm / tool_calls).
    # Only sub-agent executions in multi-agent clusters write it; NULL for single-agent apps.
    agent_log = Column(
        JSONB,
        nullable=True,
        comment="Agent 执行轨迹（AgentTraceRecorder.to_dict 快照：{meta, iterations:[{llm, tool_calls}]}）"
    )

    # ── Multi-agent cluster orchestration ownership ────────────────────────────
    parent_execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_executions.id",
            name="agent_executions_parent_execution_id_fkey",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
        comment="父执行 ID（子 Agent 指向主 Agent 的执行记录；主 Agent 与普通执行为 NULL）"
    )
    agent_role = Column(
        String(20),
        nullable=False,
        server_default="master",
        index=True,
        comment="执行角色: master（主 Agent）| sub（子 Agent）"
    )
    orchestration_mode = Column(
        String(20),
        nullable=True,
        comment="编排模式: supervisor（主管三段式）| supervisor_loop（主管 ReAct 循环）| collaboration（协作）；非集群执行时为 NULL"
    )

    # Overall status
    status = Column(String(20), nullable=False, default="running", index=True)
    # Possible values: running, completed, failed

    error_message = Column(Text, nullable=True)

    # Performance metrics
    started_at = Column(DateTime, nullable=False, default=utcnow_naive)
    completed_at = Column(DateTime, nullable=True)
    elapsed_time = Column(Float, nullable=True, comment="总耗时（秒）")

    # Token usage
    token_usage = Column(JSONB, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow_naive)

    # Extended metadata (runtime info such as model name, provider)
    meta_data = Column(JSONB, nullable=True, default=dict, comment="扩展元数据")

    # Relationships
    app = relationship("App")
    conversation = relationship("Conversation")

    def __repr__(self):
        return f"<AgentExecution(id={self.id}, app_id={self.app_id}, status={self.status})>"
