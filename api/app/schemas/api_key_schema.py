"""API Key Schema"""
import datetime
import uuid
from pydantic import BaseModel, Field, ConfigDict
from pydantic.v1 import validator
from typing import Optional, List

from app.models.api_key_model import ApiKeyType, ResourceType


class ApiKeyCreate(BaseModel):
    """创建 API Key"""
    name: str = Field(..., description="API Key 名称", max_length=255)
    description: Optional[str] = Field(None, description="描述")
    type: ApiKeyType = Field(..., description="API Key 类型")
    scopes: List[str] = Field(default_factory=list, description="权限范围列表")
    resource_id: Optional[uuid.UUID] = Field(None, description="关联资源ID")
    resource_type: Optional[ResourceType] = Field(None, description="资源类型")
    rate_limit: Optional[int] = Field(10, ge=1, le=1000, description="QPS限制（请求/秒）")
    daily_request_limit: Optional[int] = Field(10000, description="日请求限制", ge=1)
    quota_limit: Optional[int] = Field(None, description="配额限制（总请求数）", ge=1)
    expires_at: Optional[datetime.datetime] = Field(None, description="过期时间")

    @validator('scopes')
    def validate_scopes(cls, v):
        """验证权限范围格式"""
        valid_scopes = [
            "app:all",
            "rag:search", "rag:upload", "rag:delete",
            "memory:read", "memory:write", "memory:delete", "memory:search"
        ]
        for scope in v:
            if scope not in valid_scopes:
                raise ValueError(f"无效范围: {scope}")
        return v


class ApiKeyUpdate(BaseModel):
    """更新 API Key配置"""
    name: Optional[str] = Field(None, description="API Key 名称", max_length=255)
    description: Optional[str] = Field(None, description="描述")
    scopes: Optional[List[str]] = Field(None, description="权限范围列表")
    rate_limit: Optional[int] = Field(None, description="速率限制（请求/分钟）", ge=1)
    daily_request_limit: Optional[int] = Field(10000, description="每日请求数限制", ge=1)
    quota_limit: Optional[int] = Field(None, description="配额限制（总请求数）", ge=1)
    is_active: Optional[bool] = Field(None, description="是否激活")
    expires_at: Optional[datetime.datetime] = Field(None, description="过期时间")

    @validator('scopes')
    def validate_scopes(cls, v):
        """验证权限范围格式"""
        valid_scopes = {
            'app:all',
            'rag:search', 'rag:upload', 'rag:delete',
            'memory:read', 'memory:write', 'memory:delete', 'memory:search'
        }
        for scope in v:
            if scope not in valid_scopes:
                raise ValueError(f"无效范围: {scope}")
        return v


class ApiKeyResponse(BaseModel):
    """API Key 响应（创建时返回，包含明文 Key）"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: Optional[str]
    api_key: str = Field(..., description="API Key 明文（仅创建时返回）")
    key_prefix: str
    type: str
    scopes: List[str]
    resource_id: Optional[uuid.UUID]
    resource_type: Optional[str]
    rate_limit: int
    daily_request_limit: int
    quota_limit: Optional[int]
    expires_at: Optional[datetime.datetime]
    created_at: datetime.datetime


class ApiKey(BaseModel):
    """API Key 信息（不包含明文 Key）"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: Optional[str]
    key_prefix: str
    type: str
    scopes: List[str]
    resource_id: Optional[uuid.UUID]
    resource_type: Optional[str]
    rate_limit: int
    daily_request_limit: int
    quota_limit: Optional[int]
    quota_used: int
    expires_at: Optional[datetime.datetime]
    is_active: bool
    last_used_at: Optional[datetime.datetime]
    usage_count: int
    workspace_id: uuid.UUID
    created_by: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ApiKeyStats(BaseModel):
    """API Key 使用统计"""
    total_requests: int = Field(..., description="总请求数")
    requests_today: int = Field(..., description="今日请求数")
    quota_used: int = Field(..., description="已使用配额")
    quota_limit: Optional[int] = Field(None, description="配额限制")
    last_used_at: Optional[datetime.datetime] = Field(None, description="最后使用时间")
    avg_response_time: Optional[float] = Field(None, description="平均响应时间（毫秒）")


class ApiKeyQuery(BaseModel):
    """API Key 查询参数"""
    type: Optional[ApiKeyType] = Field(None, description="API Key 类型")
    is_active: Optional[bool] = Field(None, description="是否激活")
    resource_id: Optional[uuid.UUID] = Field(None, description="关联资源ID")
    page: int = Field(1, ge=1, description="页码")
    pagesize: int = Field(10, ge=1, le=100, description="每页数量")


class ApiKeyAuth(BaseModel):
    """API Key 认证信息"""
    api_key_id: uuid.UUID
    workspace_id: uuid.UUID
    type: str
    scopes: List[str]
    resource_id: Optional[uuid.UUID]
    resource_type: Optional[str]


class ApiKeyLog(BaseModel):
    """API Key 使用日志"""
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    api_key_id: uuid.UUID
    
    # 请求信息
    endpoint: str
    method: str
    ip_address: Optional[str]
    user_agent: Optional[str]
    
    # 响应信息
    status_code: Optional[int]
    response_time: Optional[int]  # 毫秒
    
    # 业务信息
    tokens_used: Optional[int]
    
    # 时间信息
    created_at: datetime.datetime
