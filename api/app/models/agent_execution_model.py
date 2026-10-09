"""
Agent 执行记录模型

记录 Agent 应用（非工作流）每次对话的内部执行步骤，
包括工具调用、LLM 推理等中间过程。
"""

import datetime
import uuid

from sqlalchemy import Column, String, DateTime, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db import Base
from app.core.utils.datetime_utils import utcnow_naive



class AgentExecution(Base):
    """Agent 执行记录表

    每条记录对应一次 Agent 对话（一轮 user→assistant），
    steps 字段以 JSONB 数组保存该轮对话中 Agent 的所有中间步骤。
    """
    __tablename__ = "agent_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 关联信息
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

    # 执行步骤（JSONB 数组）
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

    # Agent 执行轨迹（AgentTraceRecorder.to_dict 快照）
    # {
    #   "meta": {...},
    #   "iterations": [
    #     {"llm": {model, input, output, reasoning_content, tokens, elapsed_time},
    #      "tool_calls": [{tool_name, tool_input, tool_output, ...}]}
    #   ]
    # }
    # 结构与工作流智能体节点 output_data 里的 agent_log 同构：
    # 子 Agent 折叠成 node_type='agent' + agent_log=<本字段> 的节点后，
    # 前端 Runtime.tsx 可直接渲染成工作流智能体节点同款（ROUND / llm / tool_calls）。
    # 本轮仅多 Agent 集群的子 Agent 执行写入；单 Agent 应用为 NULL。
    agent_log = Column(
        JSONB,
        nullable=True,
        comment="Agent 执行轨迹（AgentTraceRecorder.to_dict 快照：{meta, iterations:[{llm, tool_calls}]}）"
    )

    # ── 多 Agent 集群编排归属 ──────────────────────────────────────────
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
        comment="编排模式: supervisor（主管）| collaboration（协作）；非集群执行时为 NULL"
    )

    # 整体状态
    status = Column(String(20), nullable=False, default="running", index=True)
    # 可选值：running, completed, failed

    error_message = Column(Text, nullable=True)

    # 性能指标
    started_at = Column(DateTime, nullable=False, default=utcnow_naive)
    completed_at = Column(DateTime, nullable=True)
    elapsed_time = Column(Float, nullable=True, comment="总耗时（秒）")

    # Token 使用
    token_usage = Column(JSONB, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow_naive)

    # 扩展元数据（模型名称、provider 等运行时信息）
    meta_data = Column(JSONB, nullable=True, default=dict, comment="扩展元数据")

    # 关系
    app = relationship("App")
    conversation = relationship("Conversation")

    def __repr__(self):
        return f"<AgentExecution(id={self.id}, app_id={self.app_id}, status={self.status})>"
