"""Home-page payloads (field-for-field port of the monolith schemas,
including the millisecond ``created_at`` serialization)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_serializer

from ..utils.datetime_utils import to_timestamp_ms


class HomeStatistics(BaseModel):
    """首页统计数据"""

    total_models: int
    total_llm: int
    total_embedding: int
    model_week_growth_rate: float
    active_workspaces: int
    new_workspaces_this_week: int
    workspace_week_growth_rate: float
    total_users: int
    new_users_this_week: int
    user_week_growth_rate: float
    running_apps: int
    new_apps_this_week: int
    app_week_growth_rate: float


class WorkspaceInfo(BaseModel):
    """工作空间信息"""

    id: str
    name: str
    icon: str | None
    description: str | None
    app_count: int
    user_count: int
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> int | None:
        return to_timestamp_ms(value)


__all__ = ["HomeStatistics", "WorkspaceInfo"]
