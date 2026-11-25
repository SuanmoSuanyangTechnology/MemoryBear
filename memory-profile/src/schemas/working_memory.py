"""Wire models for working-memory endpoints (T01–T04).

All models mirror the monolith's existing wire contract so the proxy can
forward responses byte-for-byte.  Time fields are UTC millisecond timestamps
on the wire; the service layer converts from ``datetime`` internally via
``src.common.normalization.datetime_to_epoch_ms`` (naive values are UTC).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── Shared pagination ───────────────────────────────────────────────────────


class PageInfo(BaseModel):
    page: int
    pagesize: int
    total: int
    hasnext: bool


# ── T01 conversations ───────────────────────────────────────────────────────


class ConversationItem(BaseModel):
    id: str
    title: str | None = None


class ConversationsData(BaseModel):
    items: list[ConversationItem]
    total: int
    page: PageInfo


# ── T02 messages ────────────────────────────────────────────────────────────


class MessageItem(BaseModel):
    """Mirrors ``conversation_schema.Message`` from the monolith."""

    id: str
    conversation_id: str
    role: str
    content: str
    status: str
    meta_data: dict[str, Any] = Field(default_factory=dict)
    created_at: int  # ms timestamp
    feedback_type: str | None = None
    feedback_content: str | None = None
    is_favorited: bool | None = None
    version: int | None = 1
    is_current: bool | None = True
    parent_message_id: str | None = None


class MessagesData(BaseModel):
    items: list[MessageItem]
    page: PageInfo


# ── T03 sources ─────────────────────────────────────────────────────────────


class SourceStatItem(BaseModel):
    source: str
    message_count: int
    latest_at: int | None  # ms timestamp; null when the group has no timestamps


# ── T04 source_messages ─────────────────────────────────────────────────────


class SourceMessageItem(BaseModel):
    role: str
    content: str
    message_seq: int
    created_at: int  # ms timestamp
    dialog_at: int | None = None  # ms timestamp or null


class SourceMessagesData(BaseModel):
    items: list[SourceMessageItem]
    source: str
    page: PageInfo
