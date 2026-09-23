"""应用日志（消息记录）Schema"""
import uuid
import datetime
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field, ConfigDict, field_serializer

from app.core.utils.datetime_utils import to_timestamp_ms

class LogFileInfo(BaseModel):
    """日志中用户上传的文件信息"""
    type: str = Field(description="文件类型: image / document / audio / video")
    url: str = Field(description="文件访问 URL")
    name: Optional[str] = Field(default=None, description="文件名")
    size: Optional[int] = Field(default=None, description="文件大小（字节）")
    file_type: Optional[str] = Field(default=None, description="MIME 类型，如 image/jpeg")


class AppLogMessage(BaseModel):
    """单条消息记录"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str = Field(description="角色: user / assistant / system")
    content: str
    status: Optional[str] = Field(default=None, description="消息状态: completed / failed")
    meta_data: Optional[Dict[str, Any]] = None
    files: List[LogFileInfo] = Field(default_factory=list, description="用户上传的文件列表")
    created_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("meta_data", when_used="json")
    def _serialize_meta_data(self, data: Optional[Dict[str, Any]]):
        return data or {}


class AppLogConversation(BaseModel):
    """会话摘要（用于列表）"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    user_id: Optional[str] = None
    title: Optional[str] = None
    message_count: int = 0
    is_draft: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    # 多 Agent 集群：本会话产生的子 Agent 执行条数（非集群应用恒为 0）
    sub_agent_count: int = 0

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class AppLogNodeExecution(BaseModel):
    """工作流节点执行记录"""
    node_id: str
    node_type: str
    node_name: Optional[str] = None
    status: str = "pending"
    error: Optional[str] = None
    input: Optional[Any] = None
    process: Optional[Any] = None
    agent_log: Optional[Any] = None
    output: Optional[Any] = None
    cycle_items: Optional[List[Any]] = None
    elapsed_time: Optional[float] = None
    token_usage: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None

    # ── Agent 维度字段（多 Agent 集群；单 Agent 与工作流节点不填）────────
    # 刻意建在 schema 上而不是塞进 meta：未来要按 Agent 过滤 / 统计 / 成本分摊时，
    # 这是稳定契约，不会变成破坏性变更。字段全部可选，纯增量。
    agent_id: Optional[str] = Field(default=None, description="子 Agent 在 sub_agents 中的 ID（协作模式可能重复）")
    agent_name: Optional[str] = Field(default=None, description="子 Agent 名称")
    execution_id: Optional[str] = Field(default=None, description="该次执行的 agent_executions.id")
    parent_execution_id: Optional[str] = Field(default=None, description="主 Agent 的执行 ID")
    depth: Optional[int] = Field(default=None, description="调用层级，主 Agent 为 0，直接子 Agent 为 1")
    orchestration_mode: Optional[str] = Field(default=None, description="supervisor | collaboration")


class AppLogAgentSummary(BaseModel):
    """单次集群调用里的一个 Agent 浅层概览（B 入口，本轮前端不渲染）"""
    execution_id: Optional[str] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    role: str = "sub"
    status: str = "completed"
    elapsed_time: Optional[float] = None
    token_usage: Optional[Dict[str, Any]] = None
    tool_count: int = 0
    iterations: int = 0


class AppLogConversationDetail(AppLogConversation):
    """会话详情（包含消息列表）"""
    messages: List[AppLogMessage] = Field(default_factory=list)
    node_executions_map: Dict[str, List[AppLogNodeExecution]] = Field(default_factory=dict, description="按消息ID分组的节点执行记录")
    pending_intervention: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="人工介入信息：key=message_id，value={execution_id, status, interventions: [...]}，"
                    "结构与 /public/share/conversations/{conversation_id} 接口的 pending_intervention 一致",
    )
    # 每轮集群调用的浅层概览：key = assistant message_id。
    # 供未来的集群概览卡 / 泳道视图直接消费；本轮后端填充、前端不渲染。
    agent_execution_summary: Dict[str, List[AppLogAgentSummary]] = Field(
        default_factory=dict,
        description="按 assistant message_id 分组的集群 Agent 概览（execution_id/agent_name/role/status/耗时/token 等）",
    )


class WorkflowExecutionLog(BaseModel):
    """无会话工作流调用的日志摘要。"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: str
    app_id: uuid.UUID
    release_id: Optional[uuid.UUID] = None
    trigger_type: str
    status: str
    elapsed_time: Optional[float] = None
    error_message: Optional[str] = None
    started_at: datetime.datetime
    completed_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime

    @field_serializer("started_at", "completed_at", "created_at", when_used="json")
    def _serialize_dates(self, value: Optional[datetime.datetime]):
        return to_timestamp_ms(value) if value else None
