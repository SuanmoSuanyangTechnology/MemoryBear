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

    # 整体状态
    status = Column(String(20), nullable=False, default="running", index=True)
    # 可选值：running, completed, failed

    error_message = Column(Text, nullable=True)

    # 性能指标
    started_at = Column(DateTime, nullable=False, default=datetime.datetime.now)
    completed_at = Column(DateTime, nullable=True)
    elapsed_time = Column(Float, nullable=True, comment="总耗时（秒）")

    # Token 使用
    token_usage = Column(JSONB, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now)

    # 扩展元数据（模型名称、provider 等运行时信息）
    meta_data = Column(JSONB, nullable=True, default=dict, comment="扩展元数据")

    # 关系
    app = relationship("App")
    conversation = relationship("Conversation")

    def __repr__(self):
        return f"<AgentExecution(id={self.id}, app_id={self.app_id}, status={self.status})>"
