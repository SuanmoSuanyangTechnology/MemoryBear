"""
Workflow-related Pydantic schemas

Subset of the host workflow_schema consumed by the definition-plane endpoints
(app create path, workflow draft read / write).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ..utils.datetime_utils import to_timestamp_ms

# ==================== Node and edge definitions ====================

class NodeConfig(BaseModel):
    """Node config"""
    model_config = ConfigDict(extra="allow")  # Allow extra fields


class NodeDefinition(BaseModel):
    """Node definition"""
    id: str = Field(..., description="节点唯一标识")
    type: str = Field(
        ...,
        description="节点类型: start, end, llm, agent, tool, condition, loop, transform, human, code"
    )
    name: str | None = Field(None, description="节点名称")
    cycle: str | None = Field(None, description="父循环节点id")
    description: str | None = Field(None, description="节点描述")
    config: dict[str, Any] = Field(default_factory=dict, description="节点配置")
    position: dict[str, float] | None = Field(None, description="节点位置 {x, y}")
    error_handling: dict[str, Any] | None = Field(None, description="错误处理配置")
    cache: dict[str, Any] | None = Field(None, description="缓存配置")


class EdgeDefinition(BaseModel):
    """Edge definition"""
    id: str | None = Field(default=None, description="边唯一标识（可选）")
    source: str = Field(..., description="源节点 ID")
    target: str = Field(..., description="目标节点 ID")
    type: str | None = Field(default=None, description="边类型: normal, error")
    condition: str | None = Field(default=None, description="条件表达式（条件边）")
    label: str | None = Field(default=None, description="边标签")


class VariableDefinition(BaseModel):
    """Variable definition"""
    name: str = Field(..., description="变量名称")
    type: str = Field(default="string", description="变量类型: string, number, boolean, object, file, array[string], array[number], array[boolean], array[object], array[file]")
    ui_type: str | None = Field(default=None, description="UI展示类型: text-input, paragraph, select, file-upload, file-list-upload, json-editor")
    required: bool = Field(default=False, description="是否必填")
    default: Any = Field(None, description="默认值")
    description: str | None = Field(default=None, description="变量描述")
    max_length: int | None = Field(default=None, description="字符串最大长度")
    options: list[str] | None = Field(default=None, description="下拉选项候选值，仅 ui_type=select 时有效")
    allowed_file_types: list[str] | None = Field(default=None, description="允许文件类型(image/document/audio/video)，仅 file/file-list-upload 时有效")
    max_file_count: int | None = Field(default=None, description="文件列表最大数量，仅 file-list-upload 时有效")
    max_file_size_mb: float | None = Field(default=None, description="单文件最大大小(MB)，仅 file/file-list-upload 时有效")


class EnvironmentVariableDefinition(BaseModel):
    """Environment variable definition"""

    name: str = Field(..., description="环境变量名称，运行时通过 env.NAME 访问")
    value_type: Literal["string", "number", "secret"] = Field(
        ...,
        description="环境变量类型: string, number, secret"
    )
    required: bool = Field(default=False, description="是否必填")
    value: str | int | float | None = Field(default=None, description="变量值")
    description: str | None = Field(default=None, description="变量描述")


class ExecutionConfig(BaseModel):
    """Execution config"""
    max_iterations: int = Field(default=100, ge=1, le=1000, description="最大迭代次数")
    timeout: int = Field(default=600, ge=10, le=3600, description="全局超时时间（秒）")
    enable_cache: bool = Field(default=True, description="是否启用节点缓存")
    parallel_limit: int = Field(default=5, ge=1, le=20, description="并行执行限制")


class TriggerConfig(BaseModel):
    """Trigger config"""
    id: str | None = Field(default=None, description="触发器 ID")
    type: str = Field(..., description="触发器类型: manual, schedule, webhook")
    name: str | None = Field(default=None, description="触发器名称")
    enabled: bool = Field(default=True, description="是否启用")
    start_node_id: str | None = Field(default=None, description="绑定的开始节点 ID")
    description: str | None = Field(default=None, description="触发器描述")
    config: dict[str, Any] = Field(default_factory=dict, description="触发器配置")
    auth: dict[str, Any] = Field(default_factory=dict, description="鉴权配置")
    runtime: dict[str, Any] = Field(default_factory=dict, description="运行时状态")
    input_mapping: dict[str, Any] = Field(default_factory=dict, description="事件到工作流输入的映射")


class WorkflowImportSave(BaseModel):
    """Workflow import request"""
    temp_id: str
    name: str
    description: str | None = Field(default=None)


# ==================== Workflow config ====================

class WorkflowConfigCreate(BaseModel):
    """Create workflow config"""
    nodes: list[NodeDefinition] = Field(default_factory=list, description="节点列表")
    edges: list[EdgeDefinition] = Field(default_factory=list, description="边列表")
    variables: list[VariableDefinition] = Field(default_factory=list, description="变量列表")
    environment_variables: list[EnvironmentVariableDefinition] = Field(default_factory=list, description="环境变量列表")
    execution_config: ExecutionConfig = Field(default_factory=ExecutionConfig, description="执行配置")
    triggers: list[TriggerConfig] = Field(default_factory=list, description="触发器列表")
    features: dict = Field(default_factory=dict, description="功能特性配置")
    workflow_type: str = Field(default="workflow", description="工作流类型: pure_workflow 或 workflow")


class WorkflowConfigUpdate(BaseModel):
    """Update workflow config"""
    nodes: list[NodeDefinition] | None = None
    edges: list[EdgeDefinition] | None = None
    variables: list[VariableDefinition] | None = None
    environment_variables: list[EnvironmentVariableDefinition] | None = None
    features: dict | None = None
    execution_config: ExecutionConfig | None = None
    triggers: list[TriggerConfig] | None = None
    workflow_type: str | None = None


class WorkflowConfig(BaseModel):
    """Workflow config output"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    variables: list[dict[str, Any]]
    environment_variables: list[dict[str, Any]]
    execution_config: dict[str, Any]
    triggers: list[dict[str, Any]]
    features: dict | None
    workflow_type: str
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("features", when_used="json")
    def _serialize_features(self, features: dict | None):
        return features or {}


__all__ = [
    "EdgeDefinition",
    "EnvironmentVariableDefinition",
    "ExecutionConfig",
    "NodeConfig",
    "NodeDefinition",
    "TriggerConfig",
    "VariableDefinition",
    "WorkflowConfig",
    "WorkflowConfigCreate",
    "WorkflowConfigUpdate",
]
