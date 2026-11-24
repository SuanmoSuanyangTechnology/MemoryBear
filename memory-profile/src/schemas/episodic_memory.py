from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PageInfo(BaseModel):
    page: int
    pagesize: int
    total: int
    hasnext: bool


class CommunityItem(BaseModel):
    id: str
    topic_name: Any = None
    topic_scope: Any = None
    category_l1: Any = None
    scene_count: int = 0
    created_at: int | None = None
    updated_at: int | None = None


class OverviewData(BaseModel):
    total: int
    items: list[CommunityItem]
    page: PageInfo


class SceneItem(BaseModel):
    id: str
    topic_scope: Any = None
    content: str = ""


class DetailsData(BaseModel):
    id: str
    topic_name: Any = None
    topic_scope: Any = None
    category_l1: Any = None
    scene_count: int = 0
    created_at: int | None = None
    updated_at: int | None = None
    summary: str = ""
    items: list[SceneItem]
    page: PageInfo
