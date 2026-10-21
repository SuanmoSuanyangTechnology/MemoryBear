"""Tool-domain schemas (frozen port of the monolith tool_schema).

Field names, defaults, descriptions, and serializers must stay byte-compatible
with the host so responses remain wire-identical during coexistence. Only
import paths and the timestamp helper name were rewritten mechanically.
"""

from __future__ import annotations

import json
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, field_serializer, field_validator
from datetime import datetime
from enum import Enum

from ..utils.datetime_utils import to_timestamp_ms
from ..models.tool_model import ToolType, ToolStatus, AuthType


class ParameterType(str, Enum):
    """Parameter type enum."""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"
    FILE = "file"


class ToolParameter(BaseModel):
    """Tool parameter definition."""
    name: str = Field(..., description="参数名称")
    type: ParameterType = Field(..., description="参数类型")
    description: str = Field("", description="参数描述")
    required: bool = Field(False, description="是否必需")
    default: Any = Field(None, description="默认值")
    enum: Optional[List[Any]] = Field(None, description="枚举值")
    minimum: Optional[float] = Field(None, description="最小值")
    maximum: Optional[float] = Field(None, description="最大值")
    pattern: Optional[str] = Field(None, description="正则表达式模式")

    class Config:
        use_enum_values = True


class ToolResult(BaseModel):
    """Tool execution result."""
    success: bool = Field(..., description="执行是否成功")
    data: Any = Field(None, description="返回数据")
    error: Optional[str] = Field(None, description="错误信息")
    error_code: Optional[str] = Field(None, description="错误代码")
    execution_time: float = Field(..., description="执行时间（秒）")
    token_usage: Optional[Dict[str, int]] = Field(None, description="Token使用情况")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    @classmethod
    def success_result(
            cls,
            data: Any,
            execution_time: float,
            token_usage: Optional[Dict[str, int]] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> "ToolResult":
        """Build a success result."""
        return cls(
            success=True,
            data=data,
            execution_time=execution_time,
            token_usage=token_usage,
            metadata=metadata or {}
        )

    @classmethod
    def error_result(
            cls,
            error: str,
            execution_time: float,
            error_code: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> "ToolResult":
        """Build an error result."""
        return cls(
            success=False,
            error=error,
            error_code=error_code,
            execution_time=execution_time,
            metadata=metadata or {}
        )


class ToolInfo(BaseModel):
    """Tool info."""
    id: str = Field(..., description="工具ID")
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    icon: Optional[str] = Field(None, description="工具图标")
    tool_type: ToolType = Field(..., description="工具类型")
    version: str = Field("1.0.0", description="工具版本")
    parameters: List[ToolParameter] = Field(default_factory=list, description="工具参数")
    config_data: Dict[str, Any] = Field(default_factory=dict, description="工具配置")
    status: ToolStatus = Field(ToolStatus.AVAILABLE, description="工具状态")
    is_active: bool = Field(True, description="是否可用（False 表示已删除）")
    tags: List[str] = Field(default_factory=list, description="工具标签")
    tenant_id: Optional[str] = Field(None, description="租户ID")
    created_at: datetime = Field(..., description="创建时间")

    class Config:
        use_enum_values = True

    @field_serializer('created_at')
    @classmethod
    def serialize_datetime(cls, v):
        """Convert datetime to a millisecond timestamp."""
        return to_timestamp_ms(v)


class ToolConfigSchema(BaseModel):
    """Base tool config schema."""
    id: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    tool_type: ToolType
    status: ToolStatus
    config_data: Dict[str, Any] = Field(default_factory=dict)
    version: str = "1.0.0"
    tags: List[str] = Field(default_factory=list)
    tenant_id: str
    is_active: bool = Field(True, description="是否可用（False 表示已删除）")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BuiltinToolConfigSchema(BaseModel):
    """Builtin tool config schema."""
    tool_class: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool
    requires_config: bool = False

    class Config:
        from_attributes = True


class CustomToolConfigSchema(BaseModel):
    """Custom tool config schema."""
    base_url: Optional[str] = None
    auth_type: AuthType = AuthType.NONE
    auth_config: Dict[str, Any] = Field(default_factory=dict)
    timeout: int = 30
    schema_content: Optional[Dict[str, Any]] = None
    schema_url: Optional[str] = None

    @field_validator("schema_content", mode="before")
    @classmethod
    def _parse_schema_content(cls, v):
        # Backward compatibility: the frontend textarea may submit a JSON document as a string
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            return json.loads(v)
        return v

    class Config:
        from_attributes = True


class MCPToolConfigSchema(BaseModel):
    """MCP tool config schema."""
    server_url: str
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    last_health_check: Optional[datetime] = None
    health_status: str = "unknown"
    error_message: Optional[str] = None
    available_tools: List[Dict[str, Dict[str, Any]]] = Field(default_factory=list, description="工具列表，格式: [{'tool_name': str, 'arguments': dict}]")
    source_channel: Optional[str] = Field(None, description="来源渠道")
    market_id: Optional[str] = Field(None, description="渠道市场id")
    market_config_id: Optional[str] = Field(None, description="渠道市场配置id")
    mcp_service_id: Optional[str] = Field(None, description="mcp服务id")

    class Config:
        from_attributes = True


class WorkflowToolConfigSchema(BaseModel):
    """Workflow tool config schema."""
    app_id: str = Field(..., description="关联的应用ID")
    workflow_config_id: str = Field(..., description="工作流配置ID")
    release_id: Optional[str] = Field(None, description="绑定的工作流发布版本ID")
    input_parameters: List[Dict[str, Any]] = Field(default_factory=list, description="输入参数定义")
    output_schema: Dict[str, Any] = Field(default_factory=dict, description="输出结构定义")
    timeout: int = Field(300, description="超时时间（秒）")
    workspace_id: Optional[str] = Field(None, description="所属空间ID")

    class Config:
        from_attributes = True

class ToolDetailSchema(ToolConfigSchema):
    """Tool detail schema (includes type-specific config)."""
    builtin_config: Optional[BuiltinToolConfigSchema] = None
    custom_config: Optional[CustomToolConfigSchema] = None
    mcp_config: Optional[MCPToolConfigSchema] = None
    workflow_config: Optional[WorkflowToolConfigSchema] = None


class ToolExecutionSchema(BaseModel):
    """Tool execution record schema."""
    id: str
    execution_id: str
    status: str
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    execution_time: Optional[float] = None
    token_usage: Optional[Dict[str, int]] = None

    class Config:
        from_attributes = True


class ToolCreateRequest(BaseModel):
    """Create tool request."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    icon: Optional[str] = Field(None, max_length=255)
    tool_type: ToolType
    config: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    source_channel: Optional[str] = Field(None, description="来源渠道（仅MCP工具）")
    market_id: Optional[str] = Field(None, description="渠道市场id（仅MCP工具）")
    market_config_id: Optional[str] = Field(None, description="渠道市场配置id（仅MCP工具）")
    mcp_service_id: Optional[str] = Field(None, description="mcp服务id（仅MCP工具）")


class ToolUpdateRequest(BaseModel):
    """Update tool request."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    icon: Optional[str] = Field(None, max_length=255)
    config: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None
    tags: Optional[List[str]] = None


class ToolActiveUpdate(BaseModel):
    """Tool availability status update."""
    is_active: bool = Field(..., description="True=启用, False=禁用（逻辑删除）")


class ToolExecuteRequest(BaseModel):
    """Execute tool request."""
    tool_id: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    timeout: Optional[float] = Field(60.0, gt=0, le=300)


class CustomToolCreateRequest(BaseModel):
    """Create custom tool request."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    icon: Optional[str] = Field(None, max_length=255)
    auth_type: AuthType = Field(AuthType.NONE, description="认证类型")
    auth_config: Dict[str, Any] = Field(default_factory=dict, description="认证配置")
    timeout: int = Field(30, ge=1, le=300, description="超时时间")
    schema_content: Optional[Dict[str, Any]] = Field(None, description="OpenAPI schema内容")
    schema_url: Optional[str] = Field(None, description="OpenAPI schema URL")


class ParseSchemaRequest(BaseModel):
    """Parse schema request."""
    schema_content: Optional[str] = Field(None, description="OpenAPI schema内容")
    schema_url: Optional[str] = Field(None, description="OpenAPI schema URL")


class ToolListQuery(BaseModel):
    """Tool list query parameters."""
    name: Optional[str] = None
    tool_type: Optional[ToolType] = None
    status: Optional[ToolStatus] = None
    is_enabled: Optional[bool] = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class ToolStatusCount(BaseModel):
    """Tool status statistics."""
    status: ToolStatus
    count: int


class ToolStatistics(BaseModel):
    """Tool statistics."""
    total_tools: int
    status_counts: List[ToolStatusCount]
    type_counts: Dict[str, int]
    enabled_count: int
    disabled_count: int


class CustomToolTestRequest(BaseModel):
    """Custom tool test request."""
    method: str = Field(..., description="HTTP方法")
    path: str = Field(..., description="API路径")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="请求参数")


class WorkflowToolCreateRequest(BaseModel):
    """Create workflow tool request."""
    name: str = Field(..., min_length=1, max_length=255, description="工具名称")
    description: Optional[str] = Field(None, max_length=1000, description="工具描述")
    icon: Optional[str] = Field(None, max_length=255, description="工具图标")
    release_id: Optional[str] = Field(None, description="工作流发布版本ID，不传则使用当前发布版本")
    timeout: int = Field(300, ge=10, le=600, description="超时时间（秒）")
    tags: List[str] = Field(default_factory=list, description="工具标签")


class WorkflowToolPublishPreviewSchema(BaseModel):
    """Preview info before publishing a workflow as a tool."""
    app_id: str = Field(..., description="应用ID")
    release_id: str = Field(..., description="发布版本ID")
    release_version: int = Field(..., description="发布版本号")
    release_version_name: str = Field(..., description="发布版本名称")
    input_parameters: List[Dict[str, Any]] = Field(default_factory=list, description="输入参数定义")
    output_schema: Dict[str, Any] = Field(default_factory=dict, description="输出结构定义")
