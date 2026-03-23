"""应用日志（消息记录）Schema"""
import uuid
import datetime
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field, ConfigDict, field_serializer


class AppLogMessage(BaseModel):
    """单条消息记录"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str = Field(description="角色: user / assistant / system")
    content: str
    meta_data: Optional[Dict[str, Any]] = None
    created_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("meta_data", when_used="json")
    def _serialize_meta_data(self, data: Optional[Dict[str, Any]]):
        return data or {}


class AppLogConversation(BaseModel):
    """会话摘要（用于列表）"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    user_id: Optional[str] = None
    title: Optional[str] = None
    message_count: int = 0
    is_draft: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class AppLogConversationDetail(AppLogConversation):
    """会话详情（包含消息列表）"""
    messages: List[AppLogMessage] = Field(default_factory=list)
