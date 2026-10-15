"""Release-share schemas (wire-compatible port of the host ``release_share_schema``).

Covers the management face only: create / update inputs and the share-config
output. The public-facing contracts (shared release info, embed code, stats)
arrive with the runtime plane.
"""

from __future__ import annotations

import datetime
import uuid
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ..utils.datetime_utils import to_timestamp_ms


class ReleaseShareCreate(BaseModel):
    """创建/启用分享配置"""

    is_enabled: bool = Field(default=True, description="是否启用公开分享")
    require_password: bool = Field(default=False, description="是否需要密码访问")
    password: Optional[str] = Field(None, min_length=4, max_length=50, description="访问密码（4-50字符）")
    allow_embed: bool = Field(default=False, description="是否允许嵌入")
    embed_domains: Optional[List[str]] = Field(default=None, description="允许嵌入的域名白名单，空表示不限制")


class ReleaseShareUpdate(BaseModel):
    """更新分享配置"""

    is_enabled: Optional[bool] = Field(None, description="是否启用公开分享")
    require_password: Optional[bool] = Field(None, description="是否需要密码访问")
    password: Optional[str] = Field(None, min_length=4, max_length=50, description="访问密码")
    allow_embed: Optional[bool] = Field(None, description="是否允许嵌入")
    embed_domains: Optional[List[str]] = Field(None, description="允许嵌入的域名白名单")


class ReleaseShare(BaseModel):
    """分享配置输出"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    app_id: uuid.UUID
    is_enabled: bool
    share_token: str
    share_url: str  # 完整的公开访问 URL
    require_password: bool
    allow_embed: bool
    embed_domains: List[str] = []
    view_count: int
    last_accessed_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("last_accessed_at", when_used="json")
    def _serialize_last_accessed_at(self, dt: Optional[datetime.datetime]):
        return to_timestamp_ms(dt)


__all__ = ["ReleaseShare", "ReleaseShareCreate", "ReleaseShareUpdate"]
