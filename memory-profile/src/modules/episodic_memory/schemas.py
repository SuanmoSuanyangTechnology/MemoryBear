from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class OverviewRequest(BaseModel):
    end_user_id: str
    category_l1: str | None = Field(None, min_length=1)
    keyword: str | None = None
    sort: Literal["scene_count_desc", "created_at_desc", "updated_at_desc"] = "scene_count_desc"
    page: int = Field(1, ge=1)
    pagesize: int = Field(20, ge=1, le=100)

    @field_validator("keyword")
    @classmethod
    def normalize_keyword(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        if value and len(value) > 200:
            raise ValueError("关键词最多 200 字符")
        return value


class DetailsRequest(BaseModel):
    end_user_id: str
    scene_community_id: str = Field(..., min_length=1)
    page: int = Field(1, ge=1)
    pagesize: int = Field(20, ge=1, le=100)

    @field_validator("scene_community_id")
    @classmethod
    def valid_community_id(cls, value: str) -> str:
        if value != value.strip() or value.startswith("virtual:"):
            raise ValueError("无效社区 ID")
        return value
