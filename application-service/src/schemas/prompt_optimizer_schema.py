"""Prompt-optimizer request/response schemas (port of the used subset of the
host ``app/schemas/prompt_optimizer_schema``)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class PromptOptMessage(BaseModel):
    model_id: UUID = Field(
        ...,
        description="Model ID"
    )
    message: str = Field(
        ...,
        min_length=1,
        description="User's input message"
    )

    current_prompt: str = Field(
        default="",
        description="currently optimized prompt"
    )

    skill: bool = Field(
        default=False,
        description="Enable variable output"
    )


class PromptSaveRequest(BaseModel):
    session_id: UUID = Field(
        ...,
        description="Session ID"
    )

    title: str = Field(
        ...,
        description="Prompt Title"
    )

    prompt: str = Field(
        ...,
        description="Optimized prompt content"
    )


class CreateSessionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID = Field(
        ...,
        description="Session ID"
    )


class SessionMessage(BaseModel):
    role: str = Field(
        ...,
        description="Message role (user/assistant)"
    )
    content: str = Field(
        ...,
        description="Message content"
    )


class SessionHistoryResponse(BaseModel):
    session_id: UUID = Field(
        ...,
        description="Session ID"
    )
    messages: list[SessionMessage] = Field(
        ...,
        description="List of messages in the session"
    )


__all__ = [
    "CreateSessionResponse",
    "PromptOptMessage",
    "PromptSaveRequest",
    "SessionHistoryResponse",
    "SessionMessage",
]
