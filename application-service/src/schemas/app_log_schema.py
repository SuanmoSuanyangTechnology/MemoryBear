"""Application-log schemas (frozen port of the monolith ``app_log_schema``)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ..utils.datetime_utils import to_timestamp_ms


class LogFileInfo(BaseModel):
    """A user-uploaded file attached to a log message."""

    type: str = Field(description="文件类型: image / document / audio / video")
    url: str = Field(description="文件访问 URL")
    name: str | None = Field(default=None, description="文件名")
    size: int | None = Field(default=None, description="文件大小（字节）")
    file_type: str | None = Field(default=None, description="MIME 类型，如 image/jpeg")


class AppLogMessage(BaseModel):
    """A single message record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str = Field(description="角色: user / assistant / system")
    content: str
    status: str | None = Field(default=None, description="消息状态: completed / failed")
    meta_data: dict[str, Any] | None = None
    files: list[LogFileInfo] = Field(default_factory=list, description="用户上传的文件列表")
    created_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("meta_data", when_used="json")
    def _serialize_meta_data(self, data: dict[str, Any] | None):
        return data or {}


class AppLogConversation(BaseModel):
    """Conversation summary (list rows)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    user_id: str | None = None
    title: str | None = None
    message_count: int = 0
    is_draft: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    # Multi-agent clusters: sub-agent executions produced by this conversation
    # (always 0 for non-cluster apps).
    sub_agent_count: int = 0

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class AppLogNodeExecution(BaseModel):
    """Workflow node execution record."""

    node_id: str
    node_type: str
    node_name: str | None = None
    status: str = "pending"
    error: str | None = None
    input: Any | None = None
    process: Any | None = None
    agent_log: Any | None = None
    output: Any | None = None
    cycle_items: list[Any] | None = None
    elapsed_time: float | None = None
    token_usage: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    # Agent-dimension fields (multi-agent clusters; left empty for single-agent
    # and workflow nodes). Built into the schema rather than buried in ``meta``:
    # these form the stable contract for future per-agent filtering / statistics
    # / cost allocation. All optional, purely additive.
    agent_id: str | None = Field(default=None, description="子 Agent 在 sub_agents 中的 ID（协作模式可能重复）")
    agent_name: str | None = Field(default=None, description="子 Agent 名称")
    execution_id: str | None = Field(default=None, description="该次执行的 agent_executions.id")
    parent_execution_id: str | None = Field(default=None, description="主 Agent 的执行 ID")
    depth: int | None = Field(default=None, description="调用层级，主 Agent 为 0，直接子 Agent 为 1")
    orchestration_mode: str | None = Field(default=None, description="supervisor | supervisor_loop | collaboration")


class AppLogAgentSummary(BaseModel):
    """Shallow overview of one agent inside a cluster round (not rendered yet)."""

    execution_id: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    role: str = "sub"
    status: str = "completed"
    elapsed_time: float | None = None
    token_usage: dict[str, Any] | None = None
    tool_count: int = 0
    iterations: int = 0


class AppLogConversationDetail(AppLogConversation):
    """Conversation detail (with message list)."""

    messages: list[AppLogMessage] = Field(default_factory=list)
    node_executions_map: dict[str, list[AppLogNodeExecution]] = Field(
        default_factory=dict, description="按消息ID分组的节点执行记录"
    )
    pending_intervention: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="人工介入信息：key=message_id，value={execution_id, status, interventions: [...]}，"
                    "结构与 /public/share/conversations/{conversation_id} 接口的 pending_intervention 一致",
    )
    # Shallow overview of each cluster round: key = assistant message_id.
    # Data-only for now (filled by the backend, not rendered by this iteration
    # of the frontend); future cluster overview cards / swimlane views consume
    # it directly without changing the data model.
    agent_execution_summary: dict[str, list[AppLogAgentSummary]] = Field(
        default_factory=dict,
        description="按 assistant message_id 分组的集群 Agent 概览（execution_id/agent_name/role/status/耗时/token 等）",
    )


class WorkflowExecutionLog(BaseModel):
    """Log summary of a conversation-less workflow call."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: str
    app_id: uuid.UUID
    release_id: uuid.UUID | None = None
    trigger_type: str
    status: str
    elapsed_time: float | None = None
    error_message: str | None = None
    started_at: datetime.datetime
    completed_at: datetime.datetime | None = None
    created_at: datetime.datetime

    @field_serializer("started_at", "completed_at", "created_at", when_used="json")
    def _serialize_dates(self, value: datetime.datetime | None):
        return to_timestamp_ms(value) if value else None


__all__ = [
    "AppLogAgentSummary",
    "AppLogConversation",
    "AppLogConversationDetail",
    "AppLogMessage",
    "AppLogNodeExecution",
    "LogFileInfo",
    "WorkflowExecutionLog",
]
