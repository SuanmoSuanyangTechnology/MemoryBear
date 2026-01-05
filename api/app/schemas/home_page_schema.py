from datetime import datetime
from pydantic import BaseModel, field_serializer
from typing import Optional

from app.core.api_key_utils import datetime_to_timestamp


class HomeStatistics(BaseModel):
    """首页统计数据"""
    total_models: int
    new_models_this_month: int
    active_workspaces: int
    new_workspaces_this_month: int
    total_users: int
    new_users_this_month: int
    running_apps: int
    new_apps_this_week: int

class WorkspaceInfo(BaseModel):
    """工作空间信息"""
    id: str
    name: str
    icon: Optional[str]
    description: Optional[str]
    app_count: int
    user_count: int
    created_at: datetime

    @field_serializer('created_at')
    @classmethod
    def serialize_datetime(cls, v: datetime) -> Optional[int]:
        return datetime_to_timestamp(v)