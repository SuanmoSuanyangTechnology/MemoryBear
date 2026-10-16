"""Release-share schemas (wire-compatible port of the host ``release_share_schema``).

Covers the management face (create / update inputs, share-config output) and
the public share-view contracts (access token request, password verification,
shared release info, embed code).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from ..utils.datetime_utils import to_timestamp_ms


class ReleaseShareCreate(BaseModel):
    """Create/enable share config"""

    is_enabled: bool = Field(default=True, description="是否启用公开分享")
    require_password: bool = Field(default=False, description="是否需要密码访问")
    password: Optional[str] = Field(None, min_length=4, max_length=50, description="访问密码（4-50字符）")
    allow_embed: bool = Field(default=False, description="是否允许嵌入")
    embed_domains: Optional[List[str]] = Field(default=None, description="允许嵌入的域名白名单，空表示不限制")


class ReleaseShareUpdate(BaseModel):
    """Update share config"""

    is_enabled: Optional[bool] = Field(None, description="是否启用公开分享")
    require_password: Optional[bool] = Field(None, description="是否需要密码访问")
    password: Optional[str] = Field(None, min_length=4, max_length=50, description="访问密码")
    allow_embed: Optional[bool] = Field(None, description="是否允许嵌入")
    embed_domains: Optional[List[str]] = Field(None, description="允许嵌入的域名白名单")


class PasswordVerifyRequest(BaseModel):
    """Password verification request"""

    password: str = Field(..., description="访问密码")


class TokenRequest(BaseModel):
    """Access token request"""

    user_id: Optional[str] = Field(None, description="用户 ID（可选，不提供则自动生成）")
    password: Optional[str] = Field(None, description="访问密码（如果需要）")


class ReleaseShare(BaseModel):
    """Share config output"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    app_id: uuid.UUID
    is_enabled: bool
    share_token: str
    share_url: str  # Full public access URL
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


class SharedReleaseInfo(BaseModel):
    """App info returned to the public share view"""

    app_name: str
    app_description: Optional[str] = None
    app_icon: Optional[str] = None
    app_type: str
    version: int
    release_notes: Optional[str] = None
    published_at: int

    # App-type specific configuration payload
    config: Dict[str, Any] = {}

    # Access-control state
    require_password: bool
    is_password_verified: bool = False  # 当前是否已验证密码

    # Embed configuration
    allow_embed: bool

    @field_validator("config", mode="before")
    @classmethod
    def parse_config(cls, v):
        """Parse ``config``: a JSON string is decoded into a dict."""
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        return v if v is not None else {}


class EmbedCode(BaseModel):
    """Embed code"""

    iframe_code: str = Field(..., description="iframe 嵌入代码")
    preview_url: str = Field(..., description="预览 URL")
    width: str = Field(default="100%", description="宽度")
    height: str = Field(default="600px", description="高度")


__all__ = [
    "EmbedCode",
    "PasswordVerifyRequest",
    "ReleaseShare",
    "ReleaseShareCreate",
    "ReleaseShareUpdate",
    "SharedReleaseInfo",
    "TokenRequest",
]
