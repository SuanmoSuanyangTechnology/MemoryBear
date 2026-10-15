"""Conversation schemas (frozen port of the monolith ``conversation_schema``).

Only the v1 public-service shapes are ported: conversation list items, message
items, and the message-feedback list used by the API-key app endpoints.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, field_serializer

from ..utils.datetime_utils import to_timestamp_ms


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
    "V1ConversationFeedbackItem",
    "V1ConversationFeedbackListResponse",
    "V1ConversationListItem",
    "V1ConversationMessageItem",
    "V1ConversationMessageListResponse",
]
