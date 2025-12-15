"""
工作流相关的 Pydantic Schema
"""

import datetime
import uuid
from typing import Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer


# ==================== 节点和边定义 ====================

class NodeConfig(BaseModel):
    """节点配置"""
    model_config = ConfigDict(extra="allow")  # 允许额外字段


class NodeDefinition(BaseModel):
    """节点定义"""
    id: str = Field(..., description="节点唯一标识")
    type: str = Field(..., description="节点类型: start, end, llm, agent, tool, condition, loop, transform, human, code")
    name: str | None = Field(None, description="节点名称")
    description: str | None = Field(None, description="节点描述")
    config: dict[str, Any] = Field(default_factory=dict, description="节点配置")
    position: dict[str, float] | None = Field(None, description="节点位置 {x, y}")
    error_handling: dict[str, Any] | None = Field(None, description="错误处理配置")
    cache: dict[str, Any] | None = Field(None, description="缓存配置")


class EdgeDefinition(BaseModel):
    """边定义"""
    id: str | None = Field(None, description="边唯一标识（可选）")
    source: str = Field(..., description="源节点 ID")
    target: str = Field(..., description="目标节点 ID")
    type: str | None = Field(None, description="边类型: normal, error")
    condition: str | None = Field(None, description="条件表达式（条件边）")
    label: str | None = Field(None, description="边标签")


class VariableDefinition(BaseModel):
    """变量定义"""
    name: str = Field(..., description="变量名称")
    type: str = Field(default="string", description="变量类型: string, number, boolean, object, array")
    required: bool = Field(default=False, description="是否必填")
    default: Any = Field(None, description="默认值")
    description: str | None = Field(None, description="变量描述")


class ExecutionConfig(BaseModel):
    """执行配置"""
    max_iterations: int = Field(default=100, ge=1, le=1000, description="最大迭代次数")
    timeout: int = Field(default=600, ge=10, le=3600, description="全局超时时间（秒）")
    enable_cache: bool = Field(default=True, description="是否启用节点缓存")
    parallel_limit: int = Field(default=5, ge=1, le=20, description="并行执行限制")


class TriggerConfig(BaseModel):
    """触发器配置"""
    type: str = Field(..., description="触发器类型: schedule, webhook, event")
    config: dict[str, Any] = Field(default_factory=dict, description="触发器配置")


# ==================== 工作流配置 ====================

class WorkflowConfigCreate(BaseModel):
    """创建工作流配置"""
    nodes: list[NodeDefinition] = Field(default_factory=list, description="节点列表")
    edges: list[EdgeDefinition] = Field(default_factory=list, description="边列表")
    variables: list[VariableDefinition] = Field(default_factory=list, description="变量列表")
    execution_config: ExecutionConfig = Field(default_factory=ExecutionConfig, description="执行配置")
    triggers: list[TriggerConfig] = Field(default_factory=list, description="触发器列表")


class WorkflowConfigUpdate(BaseModel):
    """更新工作流配置"""
    nodes: list[NodeDefinition] | None = None
    edges: list[EdgeDefinition] | None = None
    variables: list[VariableDefinition] | None = None
    execution_config: ExecutionConfig | None = None
    triggers: list[TriggerConfig] | None = None


class WorkflowConfig(BaseModel):
    """工作流配置输出"""
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    app_id: uuid.UUID
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    variables: list[dict[str, Any]]
    execution_config: dict[str, Any]
    triggers: list[dict[str, Any]]
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
    
    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


# ==================== 工作流执行 ====================

class WorkflowExecutionRequest(BaseModel):
    """工作流执行请求"""
    message: str | None = Field(None, description="用户消息（可选）")
    variables: dict[str, Any] = Field(default_factory=dict, description="输入变量")
    conversation_id: str | None = Field(None, description="会话 ID（用于关联对话）")
    stream: bool = Field(default=False, description="是否流式返回")


class WorkflowExecutionResponse(BaseModel):
    """工作流执行响应（非流式）"""
    execution_id: str = Field(..., description="执行 ID")
    status: str = Field(..., description="执行状态")
    output: str | None = Field(None, description="最终输出（字符串，便于快速访问）")
    output_data: dict[str, Any] | None = Field(None, description="所有节点的详细输出数据")
    error_message: str | None = Field(None, description="错误信息")
    elapsed_time: float | None = Field(None, description="耗时（秒）")
    token_usage: dict[str, Any] | None = Field(None, description="Token 使用情况 {prompt_tokens, completion_tokens, total_tokens}")


class WorkflowExecutionStreamChunk(BaseModel):
    """工作流执行流式响应块"""
    type: str = Field(..., description="事件类型: node_start, token, node_complete, error_redirect, workflow_complete")
    execution_id: str = Field(..., description="执行 ID")
    data: dict[str, Any] = Field(default_factory=dict, description="事件数据")


class WorkflowExecution(BaseModel):
    """工作流执行记录输出"""
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    workflow_config_id: uuid.UUID
    app_id: uuid.UUID
    conversation_id: uuid.UUID | None
    execution_id: str
    trigger_type: str
    triggered_by: uuid.UUID | None
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    context: dict[str, Any]
    status: str
    error_message: str | None
    error_node_id: str | None
    started_at: datetime.datetime
    completed_at: datetime.datetime | None
    elapsed_time: float | None
    token_usage: dict[str, Any] | None
    meta_data: dict[str, Any]
    created_at: datetime.datetime
    
    @field_serializer("started_at", when_used="json")
    def _serialize_started_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("completed_at", when_used="json")
    def _serialize_completed_at(self, dt: datetime.datetime | None):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class WorkflowNodeExecution(BaseModel):
    """工作流节点执行记录输出"""
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    execution_id: uuid.UUID
    node_id: str
    node_type: str
    node_name: str | None
    execution_order: int
    retry_count: int
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    status: str
    error_message: str | None
    started_at: datetime.datetime
    completed_at: datetime.datetime | None
    elapsed_time: float | None
    token_usage: dict[str, Any] | None
    cache_hit: bool
    cache_key: str | None
    meta_data: dict[str, Any]
    created_at: datetime.datetime
    
    @field_serializer("started_at", when_used="json")
    def _serialize_started_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("completed_at", when_used="json")
    def _serialize_completed_at(self, dt: datetime.datetime | None):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


# ==================== 验证响应 ====================

class WorkflowValidationResponse(BaseModel):
    """工作流验证响应"""
    is_valid: bool = Field(..., description="是否有效")
    errors: list[str] = Field(default_factory=list, description="错误列表")
    warnings: list[str] = Field(default_factory=list, description="警告列表")
