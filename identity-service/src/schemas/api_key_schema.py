"""API-key request/response schemas (host-compatible, ms on the wire)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from src.core.datetime_utils import (
    parse_timestamp_to_utc_naive,
    to_timestamp_ms,
    utcnow_naive,
)
from src.models.api_key_model import ApiKeyType

_VALID_SCOPES = ["app", "rag", "memory", "app_mgmt", "asset"]


def _to_ms(value: Any) -> int | None:
    if value is None or isinstance(value, int):
        return value
    return to_timestamp_ms(value)


def _parse_expires_at(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return parse_timestamp_to_utc_naive(value)
    return value


class ApiKeyCreate(BaseModel):
    """创建 API Key"""

    name: str = Field(..., max_length=255, description="API Key 名称")
    description: str | None = Field(None, description="描述")
    type: ApiKeyType = Field(..., description="API Key 类型")
    scopes: list[str] = Field(default_factory=list, description="权限范围列表")
    user_id: str | None = Field(default=None, description="user/other_id")
    resource_id: uuid.UUID | None = Field(None, description="关联资源ID")
    rate_limit: int | None = Field(50, ge=1, le=1000, description="QPS限制（请求/秒）")
    daily_request_limit: int | None = Field(100000, ge=1, description="日请求限制")
    quota_limit: int | None = Field(None, ge=1, description="配额限制（总请求数）")
    expires_at: datetime.datetime | None = Field(None, description="过期时间")

    @field_validator("expires_at", mode="before")
    @classmethod
    def parse_expires_at(cls, value):
        return _parse_expires_at(value)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value):
        if value is None:
            return []
        for scope in value:
            if scope not in _VALID_SCOPES:
                raise ValueError(f"无效范围: {scope}")
        return value

    @field_validator("user_id", mode="before")
    @classmethod
    def normalize_user_id(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_type_constraints(self):
        if self.type == ApiKeyType.SERVICE:
            if "app" in self.scopes:
                raise ValueError("SERVICE 类型 API Key 的权限范围不能包含 app")
        else:
            if "app" not in self.scopes:
                raise ValueError(f"{self.type.value} 类型 API Key 的权限范围必须包含 app")
            if not self.resource_id:
                raise ValueError(
                    f"{self.type.value} 类型 API Key 必须指定 resource_id（指向应用）"
                )
        return self


class ApiKeyUpdate(BaseModel):
    """更新 API Key配置"""

    name: str | None = Field(None, max_length=255, description="API Key 名称")
    description: str | None = Field(None, description="描述")
    scopes: list[str] | None = Field(None, description="权限范围列表")
    rate_limit: int | None = Field(None, ge=1, description="速率限制（请求/分钟）")
    daily_request_limit: int | None = Field(100000, ge=1, description="每日请求数限制")
    quota_limit: int | None = Field(None, ge=1, description="配额限制（总请求数）")
    is_active: bool | None = Field(None, description="是否激活")
    expires_at: datetime.datetime | None = Field(None, description="过期时间")

    @field_validator("expires_at", mode="before")
    @classmethod
    def parse_expires_at(cls, value):
        return _parse_expires_at(value)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value):
        if value is None:
            return value
        for scope in value:
            if scope not in _VALID_SCOPES:
                raise ValueError(f"无效范围: {scope}")
        return value


class ApiKeyResponse(BaseModel):
    """API Key 响应（创建时返回，包含明文 Key）"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    api_key: str
    type: str
    scopes: list[str]
    resource_id: uuid.UUID | None
    rate_limit: int
    daily_request_limit: int
    quota_limit: int | None
    is_active: bool
    expires_at: int | None
    created_at: int
    end_user_id: uuid.UUID | None = Field(default=None)
    other_id: str | None = Field(default=None)

    @field_validator("expires_at", mode="before")
    @classmethod
    def _expires_ms(cls, value):
        return _to_ms(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _created_ms(cls, value):
        return _to_ms(value)

    @computed_field
    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return to_timestamp_ms(utcnow_naive()) > self.expires_at


class ApiKey(BaseModel):
    """API Key 信息（不含创建响应中的终端用户字段）"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    api_key: str
    type: str
    scopes: list[str]
    resource_id: uuid.UUID | None
    rate_limit: int
    daily_request_limit: int
    quota_limit: int | None
    quota_used: int
    expires_at: int | None
    is_active: bool
    last_used_at: int | None
    usage_count: int
    workspace_id: uuid.UUID
    created_by: uuid.UUID
    created_at: int
    updated_at: int

    @field_validator("expires_at", "last_used_at", mode="before")
    @classmethod
    def _optional_ms(cls, value):
        return _to_ms(value)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _required_ms(cls, value):
        return _to_ms(value)

    @computed_field
    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return to_timestamp_ms(utcnow_naive()) > self.expires_at


class ApiKeyStats(BaseModel):
    """API Key 使用统计"""

    total_requests: int = Field(..., description="总请求数")
    requests_today: int = Field(..., description="今日请求数")
    quota_used: int = Field(..., description="已使用配额")
    quota_limit: int | None = Field(None, description="配额限制")
    last_used_at: int | None = Field(None, description="最后使用时间")
    avg_response_time: float | None = Field(None, description="平均响应时间（毫秒）")

    @field_validator("last_used_at", mode="before")
    @classmethod
    def _last_used_ms(cls, value):
        return _to_ms(value)


class ApiKeyQuery(BaseModel):
    """API Key 查询参数"""

    type: ApiKeyType | None = Field(None, description="API Key 类型")
    is_active: bool | None = Field(None, description="是否激活")
    resource_id: uuid.UUID | None = Field(None, description="关联资源ID")
    page: int = Field(1, ge=1, description="页码")
    pagesize: int = Field(10, ge=1, le=100, description="每页数量")


class ApiKeyLog(BaseModel):
    """API Key 使用日志"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    api_key_id: uuid.UUID
    endpoint: str
    method: str
    ip_address: str | None
    user_agent: str | None
    status_code: int | None
    response_time: int | None
    tokens_used: int | None
    created_at: int

    @field_validator("created_at", mode="before")
    @classmethod
    def _created_ms(cls, value):
        return _to_ms(value)
