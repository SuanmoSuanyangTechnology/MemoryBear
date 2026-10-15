"""Conversation-share schemas (frozen port of the host ``app_schema`` share contracts)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ShareConversationRequest(BaseModel):
    """分享会话请求"""

    password: Optional[str] = Field(None, description="访问密码（可选）")
    expire_hours: Optional[int] = Field(None, ge=1, le=720, description="过期时长（小时）")
    allow_copy: bool = Field(True, description="是否允许复制内容")


class ShareConversationResponse(BaseModel):
    """分享会话响应"""

    share_id: str = Field(..., description="分享ID")
    share_uuid: str = Field(..., description="分享唯一标识")
    share_url: str = Field(..., description="分享链接")
    expire_at: Optional[int] = Field(None, description="过期时间（毫秒时间戳）")
    has_password: bool = Field(..., description="是否有密码")


__all__ = ["ShareConversationRequest", "ShareConversationResponse"]
