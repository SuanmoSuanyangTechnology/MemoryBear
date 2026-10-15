"""End-user info request contract (frozen port of the monolith
``end_user_info_schema.EndUserInfoUpdate``)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EndUserInfoUpdate(BaseModel):
    """Update request for an end-user info record."""

    end_user_id: str = Field(description="终端用户ID")
    other_name: str | None = Field(description="用户名称", default=None)
    aliases: list[str] | None = Field(description="用户别名列表", default=None)
    meta_data: dict[str, Any] | None = Field(description="用户相关的扩展信息", default=None)


__all__ = ["EndUserInfoUpdate"]
