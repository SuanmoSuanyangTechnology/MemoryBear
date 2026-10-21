"""Data models related to tool management"""
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, String, Text, DateTime, JSON, ForeignKey, Integer, Float, Boolean, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive


class ToolType(StrEnum):
    """Tool type enum"""
    BUILTIN = "builtin"
    CUSTOM = "custom"
    MCP = "mcp"
    WORKFLOW = "workflow"


class ToolStatus(StrEnum):
    """Tool status enum"""
    AVAILABLE = "available"  # Available (configured and enabled)
    UNCONFIGURED = "unconfigured"  # Unconfigured
    CONFIGURED_DISABLED = "configured_disabled"  # Configured but disabled
    ERROR = "error"  # Error state

    @classmethod
    def get_all_statuses(cls):
        """Get all tool statuses"""
        return [status.value for status in cls]

    @classmethod
    def get_all_statuses_with_labels(cls):
        """Get all tool statuses with their text labels"""
        return [
            {"value": cls.AVAILABLE.value, "label": "可用"},
            {"value": cls.UNCONFIGURED.value, "label": "未配置"},
            {"value": cls.CONFIGURED_DISABLED.value, "label": "已配置未启用"},
            {"value": cls.ERROR.value, "label": "错误状态"}
        ]

    @classmethod
    def is_valid_status(cls, status):
        """Check whether a status is valid"""
        return status in cls._value2member_map_

    @classmethod
    def get_active_statuses(cls):
        """Get all active statuses"""
        return [cls.AVAILABLE.value]

    @classmethod
    def get_inactive_statuses(cls):
        """Get all inactive statuses"""
        return [cls.UNCONFIGURED.value, cls.CONFIGURED_DISABLED.value, cls.ERROR.value]


class AuthType(StrEnum):
    """Auth type enum"""
    NONE = "none"
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"

    @classmethod
    def get_all_types(cls):
        """Get all auth types"""
        return [auth_type.value for auth_type in cls]

    @classmethod
    def get_all_types_with_labels(cls):
        """Get all auth types with their text labels"""
        return [
            {"value": cls.NONE.value, "label": "无需认证"},
            {"value": cls.API_KEY.value, "label": "API Key"},
            {"value": cls.BEARER_TOKEN.value, "label": "Bearer Token"},
            {"value": cls.BASIC_AUTH.value, "label": "Basic Auth"}
        ]

    @classmethod
    def is_valid_types(cls, auth_type):
        """Check whether an auth type is valid"""
        return auth_type in cls._value2member_map_


class ExecutionStatus(StrEnum):
    """Execution status enum"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ToolConfig(Base):
    """Base tool config model"""
    __tablename__ = "tool_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    icon = Column(String(255))  # Tool icon
    tool_type = Column(String(50), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)  # Must belong to a tenant
    status = Column(String(50), default=ToolStatus.UNCONFIGURED.value, nullable=False, index=True)  # Tool status

    # Tool-specific config (stored as JSON)
    config_data = Column(JSON, default=dict)

    # Metadata
    version = Column(String(50), default="1.0.0")
    tags = Column(JSON, default=list)  # Tag list

    # Soft delete flag
    is_active = Column(Boolean, default=True, server_default='true', nullable=False, index=True, comment="是否可用，False表示已删除")

    # Timestamps
    created_at = Column(DateTime, default=utcnow_naive, nullable=False)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    # Relationships
    # Cross-domain relationship omitted: target model is host-owned and not part of this service's model set (FK column retained).
    executions = relationship("ToolExecution", back_populates="tool_config", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<ToolConfig(id={self.id}, name={self.name}, type={self.tool_type}, status={self.status})>"


class BuiltinToolConfig(Base):
    """Builtin tool config model"""
    __tablename__ = "builtin_tool_configs"

    id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), primary_key=True)
    tool_class = Column(String(255), nullable=False)  # Tool class name
    parameters = Column(JSON, default=dict)  # Tool parameter config
    is_enabled = Column(Boolean, default=False, server_default='false', nullable=False)  # Enable switch
    requires_config = Column(Boolean, default=False, server_default='false', nullable=False)  # Whether config is required

    # Relationships
    base_config = relationship("ToolConfig", foreign_keys=[id])
    
    def __repr__(self):
        return f"<BuiltinToolConfig(id={self.id}, tool_class={self.tool_class}, enabled={self.is_enabled})>"


class CustomToolConfig(Base):
    """Custom tool config model"""
    __tablename__ = "custom_tool_configs"

    id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), primary_key=True)
    schema_url = Column(String(1000))  # OpenAPI schema URL
    schema_content = Column(JSON)  # OpenAPI schema content

    # Auth config
    auth_type = Column(String(50), default=AuthType.NONE.value, nullable=False)
    auth_config = Column(JSON, default=dict)  # Auth config (stored encrypted)

    # API config
    base_url = Column(String(1000))  # API base URL
    timeout = Column(Integer, default=30)  # Timeout (seconds)

    # Relationships
    base_config = relationship("ToolConfig", foreign_keys=[id])
    
    def __repr__(self):
        return f"<CustomToolConfig(id={self.id}, auth_type={self.auth_type})>"


class MCPSourceChannel(StrEnum):
    """MCP source channel enum"""
    ALIYUN_BAILIAN = "aliyun_bailian"  # Alibaba Cloud Bailian
    MODELSCOPE = "modelscope"          # ModelScope
    TOKENFLUX = "tokenflux"            # TokenFlux
    LANGENG = "langeng"                # Langeng Technology
    AI_302 = "302ai"                   # 302.AI
    MCP_ROUTER = "mcp_router"          # MCP Router
    SELF_HOSTED = "self_hosted"        # Self-hosted


class MCPToolConfig(Base):
    """MCP tool config model"""
    __tablename__ = "mcp_tool_configs"

    id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), primary_key=True)
    server_url = Column(String(1000), nullable=False)  # MCP server URL
    connection_config = Column(JSON, default=dict)  # Connection config (includes auth info)

    # Source channel
    source_channel = Column(String(50), default=MCPSourceChannel.SELF_HOSTED,
                            server_default=text(f"'{MCPSourceChannel.SELF_HOSTED}'"), nullable=False, comment="来源渠道")
    market_id = Column(UUID(as_uuid=True), nullable=True, comment="渠道市场id")
    market_config_id = Column(UUID(as_uuid=True), nullable=True, comment="渠道市场配置id")
    mcp_service_id = Column(String(255), nullable=True, comment="mcp服务id")
    
    # Service status
    last_health_check = Column(DateTime)
    health_status = Column(String(50), default="unknown")
    error_message = Column(Text)
    
    # Available tool list
    available_tools = Column(JSON, default=list)
    
    # Relationships
    base_config = relationship("ToolConfig", foreign_keys=[id])
    
    def __repr__(self):
        return f"<MCPToolConfig(id={self.id}, server_url={self.server_url})>"


class ToolExecution(Base):
    """Tool execution record model"""
    __tablename__ = "tool_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_config_id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), nullable=False, index=True)
    
    # Execution info
    execution_id = Column(String(255), nullable=False, index=True)  # Execution ID (can be used to link workflows, etc.)
    status = Column(String(50), default=ExecutionStatus.PENDING.value, nullable=False, index=True)

    # Input/output
    input_data = Column(JSON)  # Input parameters
    output_data = Column(JSON)  # Output result
    error_message = Column(Text)  # Error message

    # Performance metrics
    started_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime)
    execution_time = Column(Float)  # Execution time (seconds)

    # Token usage (if applicable)
    token_usage = Column(JSON)

    # User info
    user_id = Column(UUID(as_uuid=True), index=True, nullable=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    
    # Relationships
    tool_config = relationship("ToolConfig", back_populates="executions")
    # Cross-domain relationship omitted: target model is host-owned and not part of this service's model set (FK column retained).
    
    def __repr__(self):
        return f"<ToolExecution(id={self.id}, status={self.status}, tool={self.tool_config_id})>"


# class ToolDependency(Base):
#     """Tool dependency model"""
#     __tablename__ = "tool_dependencies"
#
#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     tool_id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), nullable=False)
#     depends_on_tool_id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), nullable=False)
#
#     # Dependency type and version requirements
#     dependency_type = Column(String(50), default="required")  # required, optional
#     version_constraint = Column(String(100))  # Version constraint, e.g. ">=1.0.0"
#
#     # Timestamps
#     created_at = Column(DateTime, default=utcnow_naive, nullable=False)
#
#     # Relationships
#     tool = relationship("ToolConfig", foreign_keys=[tool_id])
#     depends_on_tool = relationship("ToolConfig", foreign_keys=[depends_on_tool_id])
#
#     def __repr__(self):
#         return f"<ToolDependency(tool={self.tool_id}, depends_on={self.depends_on_tool_id})>"


# class PluginConfig(Base):
#     """Plugin config model"""
#     __tablename__ = "plugin_configs"
#
#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     name = Column(String(255), nullable=False, unique=True, index=True)
#     description = Column(Text)
#
#     # Plugin info
#     plugin_path = Column(String(1000), nullable=False)  # Plugin file path
#     entry_point = Column(String(255), nullable=False)  # Entry point
#     version = Column(String(50), default="1.0.0")
#
#     # Status
#     is_enabled = Column(Boolean, default=True, nullable=False)
#     is_loaded = Column(Boolean, default=False, nullable=False)
#     load_error = Column(Text)  # Load error message
#
#     # Configuration
#     config_schema = Column(JSON)  # Config schema
#     config_data = Column(JSON, default=dict)  # Config data
#
#     # Dependencies
#     dependencies = Column(JSON, default=list)  # Other dependent plugins
#
#     # Timestamps
#     created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
#     updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
#     last_loaded_at = Column(DateTime)
#
class WorkflowToolConfig(Base):
    """Workflow tool config model"""
    __tablename__ = "workflow_tool_configs"

    id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), primary_key=True)
    app_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    workflow_config_id = Column(UUID(as_uuid=True), nullable=False)
    release_id = Column(UUID(as_uuid=True), ForeignKey("app_releases.id"), nullable=True, index=True)
    input_parameters = Column(JSON, default=list)
    output_schema = Column(JSON, default=dict)
    timeout = Column(Integer, default=300)

    # Relationships
    base_config = relationship("ToolConfig", foreign_keys=[id])
    
    def __repr__(self):
        return f"<WorkflowToolConfig(id={self.id}, app_id={self.app_id})>"
