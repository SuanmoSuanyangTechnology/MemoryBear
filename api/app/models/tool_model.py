"""工具管理相关数据模型"""
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, String, Text, DateTime, JSON, ForeignKey, Integer, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base


class ToolType(StrEnum):
    """工具类型枚举"""
    BUILTIN = "builtin"
    CUSTOM = "custom"
    MCP = "mcp"


class ToolStatus(StrEnum):
    """工具状态枚举"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    LOADING = "loading"


class AuthType(StrEnum):
    """认证类型枚举"""
    NONE = "none"
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"


class ExecutionStatus(StrEnum):
    """执行状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ToolConfig(Base):
    """工具配置基础模型"""
    __tablename__ = "tool_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    tool_type = Column(String(50), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)  # 必须属于租户
    status = Column(String(50), default=ToolStatus.INACTIVE.value, nullable=False, index=True)  # 工具状态
    
    # 工具特定配置（JSON格式存储）
    config_data = Column(JSON, default=dict)
    
    # 元数据
    version = Column(String(50), default="1.0.0")
    tags = Column(JSON, default=list)  # 标签列表
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    
    # 关联关系
    tenant = relationship("Tenants", back_populates="tool_configs")
    executions = relationship("ToolExecution", back_populates="tool_config", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<ToolConfig(id={self.id}, name={self.name}, type={self.tool_type}, status={self.status})>"


class BuiltinToolConfig(Base):
    """内置工具配置模型"""
    __tablename__ = "builtin_tool_configs"

    id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), primary_key=True)
    tool_class = Column(String(255), nullable=False)  # 工具类名
    parameters = Column(JSON, default=dict)  # 工具参数配置
    
    # 关联关系
    base_config = relationship("ToolConfig", foreign_keys=[id])
    
    def __repr__(self):
        return f"<BuiltinToolConfig(id={self.id}, tool_class={self.tool_class})>"


class CustomToolConfig(Base):
    """自定义工具配置模型"""
    __tablename__ = "custom_tool_configs"

    id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), primary_key=True)
    schema_url = Column(String(1000))  # OpenAPI schema URL
    schema_content = Column(JSON)  # OpenAPI schema 内容
    
    # 认证配置
    auth_type = Column(String(50), default=AuthType.NONE.value, nullable=False)
    auth_config = Column(JSON, default=dict)  # 认证配置（加密存储）
    
    # API配置
    base_url = Column(String(1000))  # API基础URL
    timeout = Column(Integer, default=30)  # 超时时间（秒）
    
    # 关联关系
    base_config = relationship("ToolConfig", foreign_keys=[id])
    
    def __repr__(self):
        return f"<CustomToolConfig(id={self.id}, auth_type={self.auth_type})>"


class MCPToolConfig(Base):
    """MCP工具配置模型"""
    __tablename__ = "mcp_tool_configs"

    id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), primary_key=True)
    server_url = Column(String(1000), nullable=False)  # MCP服务器URL
    connection_config = Column(JSON, default=dict)  # 连接配置
    
    # 服务状态
    last_health_check = Column(DateTime)
    health_status = Column(String(50), default="unknown")
    error_message = Column(Text)
    
    # 可用工具列表
    available_tools = Column(JSON, default=list)
    
    # 关联关系
    base_config = relationship("ToolConfig", foreign_keys=[id])
    
    def __repr__(self):
        return f"<MCPToolConfig(id={self.id}, server_url={self.server_url})>"


class ToolExecution(Base):
    """工具执行记录模型"""
    __tablename__ = "tool_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_config_id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), nullable=False, index=True)
    
    # 执行信息
    execution_id = Column(String(255), nullable=False, index=True)  # 执行ID（可用于关联工作流等）
    status = Column(String(50), default=ExecutionStatus.PENDING.value, nullable=False, index=True)
    
    # 输入输出
    input_data = Column(JSON)  # 输入参数
    output_data = Column(JSON)  # 输出结果
    error_message = Column(Text)  # 错误信息
    
    # 性能指标
    started_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime)
    execution_time = Column(Float)  # 执行时间（秒）
    
    # Token使用情况（如果适用）
    token_usage = Column(JSON)
    
    # 用户信息
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    
    # 关联关系
    tool_config = relationship("ToolConfig", back_populates="executions")
    user = relationship("User")
    workspace = relationship("Workspace")
    
    def __repr__(self):
        return f"<ToolExecution(id={self.id}, status={self.status}, tool={self.tool_config_id})>"


# class ToolDependency(Base):
#     """工具依赖关系模型"""
#     __tablename__ = "tool_dependencies"
#
#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     tool_id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), nullable=False)
#     depends_on_tool_id = Column(UUID(as_uuid=True), ForeignKey("tool_configs.id"), nullable=False)
#
#     # 依赖类型和版本要求
#     dependency_type = Column(String(50), default="required")  # required, optional
#     version_constraint = Column(String(100))  # 版本约束，如 ">=1.0.0"
#
#     # 时间戳
#     created_at = Column(DateTime, default=datetime.now, nullable=False)
#
#     # 关联关系
#     tool = relationship("ToolConfig", foreign_keys=[tool_id])
#     depends_on_tool = relationship("ToolConfig", foreign_keys=[depends_on_tool_id])
#
#     def __repr__(self):
#         return f"<ToolDependency(tool={self.tool_id}, depends_on={self.depends_on_tool_id})>"


# class PluginConfig(Base):
#     """插件配置模型"""
#     __tablename__ = "plugin_configs"
#
#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     name = Column(String(255), nullable=False, unique=True, index=True)
#     description = Column(Text)
#
#     # 插件信息
#     plugin_path = Column(String(1000), nullable=False)  # 插件文件路径
#     entry_point = Column(String(255), nullable=False)  # 入口点
#     version = Column(String(50), default="1.0.0")
#
#     # 状态
#     is_enabled = Column(Boolean, default=True, nullable=False)
#     is_loaded = Column(Boolean, default=False, nullable=False)
#     load_error = Column(Text)  # 加载错误信息
#
#     # 配置
#     config_schema = Column(JSON)  # 配置schema
#     config_data = Column(JSON, default=dict)  # 配置数据
#
#     # 依赖
#     dependencies = Column(JSON, default=list)  # 依赖的其他插件
#
#     # 时间戳
#     created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
#     updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
#     last_loaded_at = Column(DateTime)
#
#     def __repr__(self):
#         return f"<PluginConfig(id={self.id}, name={self.name}, version={self.version})>"