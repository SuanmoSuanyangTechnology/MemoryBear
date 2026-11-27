"""Schemas for the ``/memory`` endpoints this service serves.

``/memory/write`` is the API-key face's write entry point; the two ingestion
models are the application-service callbacks. Every field mirrors the host's
model of the same name, including the optional ``id`` / ``meta_data`` /
``created_at`` on a message, because the API rebuilt its dispatch payload from
exactly those attributes.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from src.common.timing import now_ms


class WriteApiResponse(BaseModel):
    """The API's untyped ``ApiResponse`` envelope (legacy wire shape)."""

    code: int = 0
    msg: str = "OK"
    data: Any | None = None
    error: str = ""
    time: int = Field(default_factory=now_ms)


class MergeUserInput(BaseModel):
    """Host ``MergeEndUserInput`` for ``POST /v1/memory/merge``."""

    end_user_ids: set[uuid.UUID] = Field()
    target: uuid.UUID = Field(...)


class WriteMessageInput(BaseModel):
    """One message of a write batch.

    ``files`` stays a plain list: the API-key face forwards the caller's own
    body untouched, so validating its shape here would reject payloads the
    host accepted. ``id`` / ``meta_data`` / ``created_at`` are the host's
    message attributes for the agent path.
    """

    role: str = Field(..., description="消息角色: user 或 assistant")
    content: str = Field(..., description="消息内容")
    dialog_at: str | None = Field(
        None, description="该条消息发生的绝对时间（ISO 8601），不传则用服务端当前时间"
    )
    files: list[dict[str, Any]] | None = Field(default=None, description="附带的文件列表")
    id: str | None = Field(default=None, description="宿主消息 ID（agent 路径）")
    meta_data: dict[str, Any] | None = Field(default=None, description="宿主消息元数据")
    created_at: str | None = Field(default=None, description="宿主消息创建时间")
    should_memorize: bool = Field(default=True, description="是否参与记忆写入")


class WriteInput(BaseModel):
    """Host ``Write_UserInput`` for ``POST /v1/memory/write``."""

    messages: list[WriteMessageInput] = Field(default_factory=list, description="消息列表")
    end_user_id: str = Field(..., description="终端用户 ID")
    conversation_id: str | None = Field(
        None, description="对话 ID（可选）；不传时按 (工作空间, 终端用户) 归属"
    )
    identity_features: str | None = Field(
        None,
        description="跨渠道身份标识（单个）。传入=长时身份(confirmed)；"
                    "与已有主体标识相同时自动归并，本次写入落到归并后的主体上",
    )


class IngestWorkflowInput(BaseModel):
    """Application-service callback body: workflow message ingestion."""

    messages: list[WriteMessageInput] = Field(default_factory=list)
    conversation_id: str
    end_user_id: str
    config_id: str = ""
    language: str = "zh"
    storage_type: str = "neo4j"
    user_rag_memory_id: str = ""


class IngestAgentInput(BaseModel):
    """Application-service callback body: one agent turn (user + assistant)."""

    messages: list[WriteMessageInput] = Field(default_factory=list)
    conversation_id: str
    app_id: str = ""
    config_id: str = ""
    end_user_id: str = ""
    language: str = "zh"
    storage_type: str = "neo4j"
    user_rag_memory_id: str = ""


__all__ = [
    "IngestAgentInput",
    "IngestWorkflowInput",
    "MergeUserInput",
    "WriteApiResponse",
    "WriteInput",
    "WriteMessageInput",
]
