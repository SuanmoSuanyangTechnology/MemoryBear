"""Request and response contracts for Coding Agent preference recall."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PreferenceRecallContextMessage(BaseModel):
    """One caller-supplied message used only to disambiguate the current task."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def recall_content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content cannot be blank")
        return value


class PreferenceRecallRequest(BaseModel):
    """Business payload shared verbatim by the MCP and HTTP adapters."""

    model_config = ConfigDict(extra="forbid")

    current_user_message: str = Field(min_length=1)
    context_messages: list[PreferenceRecallContextMessage] = Field(default_factory=list)

    @field_validator("current_user_message")
    @classmethod
    def current_message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("current_user_message cannot be blank")
        return value


class PreferenceRecallTarget(BaseModel):
    """A closed-set P3 routing target."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    situation_key: str = Field(min_length=1)


class PreferenceRecallClassification(BaseModel):
    """Strict structured output produced by the P3 classifier."""

    model_config = ConfigDict(extra="forbid")

    targets: list[PreferenceRecallTarget]


class RecalledPreference(BaseModel):
    """The only preference fields exposed to an external Coding Agent."""

    model_config = ConfigDict(extra="forbid")

    preference_text: str = Field(min_length=1)
    updated_at: datetime


class PreferenceRecallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferences: list[RecalledPreference]
    count: int


class FullPreferenceRecallResult(PreferenceRecallResult):
    preference_prompt: str
