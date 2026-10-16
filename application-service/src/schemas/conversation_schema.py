"""Conversation schemas (frozen port of the monolith ``conversation_schema``).

Ported shapes: the v1 public-service items (conversation list items, message
items, the message-feedback list used by the API-key app endpoints), the
conversation/message output pair used by the share-view conversation detail,
and the share-chat request/response pair used by the public share-view
endpoints.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_serializer

from ..utils.datetime_utils import to_timestamp_ms
from .app_schema import FileInput


class ChatRequest(BaseModel):
    """聊天请求（基于 share_token）"""
    message: str | None = Field(default=None, description="用户消息，pure_workflow 可不传")
    conversation_id: uuid.UUID | None = Field(None, description="会话ID（多轮对话）")
    user_id: str | None = Field(None, description="用户ID（外部系统）")
    variables: dict[str, Any] | None = Field(None, description="变量参数")
    stream: bool = Field(default=False, description="是否流式返回")
    web_search: bool = Field(default=False, description="是否启用网络搜索")
    memory: bool = Field(default=True, description="是否启用记忆功能")
    thinking: bool = Field(default=False, description="是否启用深度思考（需Agent配置支持）")
    files: list[FileInput] = Field(default_factory=list, description="附件列表（支持多文件）")


class ChatResponse(BaseModel):
    """聊天响应（非流式）"""
    conversation_id: uuid.UUID
    message: str
    message_id: str
    user_message_id: str | None = None
    usage: dict[str, Any] | None = None
    elapsed_time: float | None = None
    reasoning_content: str | None = None
    suggested_questions: list[str] | None = None
    citations: list[dict[str, Any]] | None = None
    audio_url: str | None = None
    audio_status: str | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        data = handler(self)
        if not data.get("reasoning_content"):
            data.pop("reasoning_content", None)
        return data


class Message(BaseModel):
    """Message output (share-view conversation detail; feedback and favorite
    fields are injected by the handler)"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    status: str
    meta_data: dict[str, Any] | None = None
    created_at: datetime.datetime
    feedback_type: str | None = Field(None, description="反馈类型: like/dislike")
    feedback_content: str | None = Field(None, description="反馈内容")
    is_favorited: bool | None = Field(None, description="是否已收藏")
    version: int | None = Field(1, description="消息版本号")
    is_current: bool | None = Field(True, description="是否当前版本")
    parent_message_id: uuid.UUID | None = Field(None, description="父消息ID（assistant消息指向user消息）")

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at_ms(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("meta_data", when_used="json")
    def _serialize_meta_data(self, data: dict[str, Any] | None):
        return data or {}


class Conversation(BaseModel):
    """Conversation output (share-view conversation detail)"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: str | None = None
    title: str | None = None
    summary: str | None = None
    is_draft: bool
    message_count: int
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at_ms(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at_ms(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class V1ConversationListItem(BaseModel):
    """Conversation list item for the v1 public app API"""
    conversation_id: uuid.UUID
    title: str | None = None
    summary: str | None = None
    message_count: int = 0
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at_ms(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at_ms(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class V1ConversationMessageItem(BaseModel):
    """History message item for the v1 public app API"""
    message_id: uuid.UUID
    role: str
    content: str
    status: str
    meta_data: dict[str, Any] | None = None
    created_at: datetime.datetime
    version: int = 1
    is_current: bool = True
    parent_message_id: uuid.UUID | None = None

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at_ms(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("meta_data", when_used="json")
    def _serialize_meta_data(self, data: dict[str, Any] | None):
        return data or {}


class V1ConversationMessageListResponse(BaseModel):
    """History message list response for the v1 public app API"""
    conversation_id: uuid.UUID
    items: list[V1ConversationMessageItem]
    limit: int


class V1ConversationFeedbackItem(BaseModel):
    """Conversation message feedback item for the v1 public app API (current user's feedback type only, favorites excluded)"""
    message_id: uuid.UUID
    role: str
    feedback_type: str | None = None  # Current user's like / dislike / None (excludes is_favorite)
    feedback_content: str | None = None  # Current user's feedback content (e.g. dislike reason)
    created_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at_ms(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class V1ConversationFeedbackListResponse(BaseModel):
    """Conversation message feedback list response for the v1 public app API"""
    conversation_id: uuid.UUID
    items: list[V1ConversationFeedbackItem]
    limit: int


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "Conversation",
    "Message",
    "V1ConversationFeedbackItem",
    "V1ConversationFeedbackListResponse",
    "V1ConversationListItem",
    "V1ConversationMessageItem",
    "V1ConversationMessageListResponse",
]
