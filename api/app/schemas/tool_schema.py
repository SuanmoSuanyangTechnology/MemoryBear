"""工具相关的数据模式定义"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, field_serializer
from datetime import datetime
from enum import Enum

from app.core.api_key_utils import datetime_to_timestamp
from app.models.tool_model import ToolType, ToolStatus, AuthType


class ParameterType(str, Enum):
    """参数类型枚举"""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ToolParameter(BaseModel):
    """工具参数定义"""
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
    """工具执行结果"""
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
        """创建成功结果"""
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
        """创建错误结果"""
        return cls(
            success=False,
            error=error,
            error_code=error_code,
            execution_time=execution_time,
            metadata=metadata or {}
        )


class ToolInfo(BaseModel):
    """工具信息"""
    id: str = Field(..., description="工具ID")
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    icon: Optional[str] = Field(None, description="工具图标")
    tool_type: ToolType = Field(..., description="工具类型")
    version: str = Field("1.0.0", description="工具版本")
    parameters: List[ToolParameter] = Field(default_factory=list, description="工具参数")
    config_data: Dict[str, Any] = Field(default_factory=dict, description="工具配置")
    status: ToolStatus = Field(ToolStatus.AVAILABLE, description="工具状态")
    tags: List[str] = Field(default_factory=list, description="工具标签")
    tenant_id: Optional[str] = Field(None, description="租户ID")
    created_at: datetime = Field(..., description="创建时间")

    class Config:
        use_enum_values = True

    @field_serializer('created_at')
    @classmethod
    def serialize_datetime(cls, v):
        """将datetime转换为时间戳"""
        return datetime_to_timestamp(v)


class ToolConfigSchema(BaseModel):
    """工具配置基础模式"""
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
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BuiltinToolConfigSchema(BaseModel):
    """内置工具配置模式"""
    tool_class: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool
    requires_config: bool = False

    class Config:
        from_attributes = True


class CustomToolConfigSchema(BaseModel):
    """自定义工具配置模式"""
    base_url: Optional[str] = None
    auth_type: AuthType = AuthType.NONE
    auth_config: Dict[str, Any] = Field(default_factory=dict)
    timeout: int = 30
    schema_content: Optional[Dict[str, Any]] = None
    schema_url: Optional[str] = None

    class Config:
        from_attributes = True


class MCPToolConfigSchema(BaseModel):
    """MCP工具配置模式"""
    server_url: str
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    last_health_check: Optional[datetime] = None
    health_status: str = "unknown"
    error_message: Optional[str] = None
    available_tools: List[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ToolDetailSchema(ToolConfigSchema):
    """工具详情模式（包含类型特定配置）"""
    builtin_config: Optional[BuiltinToolConfigSchema] = None
    custom_config: Optional[CustomToolConfigSchema] = None
    mcp_config: Optional[MCPToolConfigSchema] = None


class ToolExecutionSchema(BaseModel):
    """工具执行记录模式"""
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
    """创建工具请求"""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    icon: Optional[str] = Field(None, max_length=255)
    tool_type: ToolType
    config: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class ToolUpdateRequest(BaseModel):
    """更新工具请求"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    icon: Optional[str] = Field(None, max_length=255)
    config: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None
    tags: Optional[List[str]] = None


class ToolExecuteRequest(BaseModel):
    """执行工具请求"""
    tool_id: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    timeout: Optional[float] = Field(60.0, gt=0, le=300)


class CustomToolCreateRequest(BaseModel):
    """创建自定义工具请求"""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    icon: Optional[str] = Field(None, max_length=255)
    auth_type: AuthType = Field(AuthType.NONE, description="认证类型")
    auth_config: Dict[str, Any] = Field(default_factory=dict, description="认证配置")
    timeout: int = Field(30, ge=1, le=300, description="超时时间")
    schema_content: Optional[Dict[str, Any]] = Field(None, description="OpenAPI schema内容")
    schema_url: Optional[str] = Field(None, description="OpenAPI schema URL")


class ParseSchemaRequest(BaseModel):
    """解析Schema请求"""
    schema_content: Optional[str] = Field(None, description="OpenAPI schema内容")
    schema_url: Optional[str] = Field(None, description="OpenAPI schema URL")


class ToolListQuery(BaseModel):
    """工具列表查询参数"""
    name: Optional[str] = None
    tool_type: Optional[ToolType] = None
    status: Optional[ToolStatus] = None
    is_enabled: Optional[bool] = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class ToolStatusCount(BaseModel):
    """工具状态统计"""
    status: ToolStatus
    count: int


class ToolStatistics(BaseModel):
    """工具统计信息"""
    total_tools: int
    status_counts: List[ToolStatusCount]
    type_counts: Dict[str, int]
    enabled_count: int
    disabled_count: int


class CustomToolTestRequest(BaseModel):
    """自定义工具测试请求"""
    method: str = Field(..., description="HTTP方法")
    path: str = Field(..., description="API路径")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="请求参数")