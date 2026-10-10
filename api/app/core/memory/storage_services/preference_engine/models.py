"""Strict schemas for preference write and on-demand recall contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PreferenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    preference_text: str = Field(min_length=1)

    @field_validator("preference_text")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("preference_text cannot be blank")
        return value


class IdentifiedPreference(PreferenceItem):
    subject: str = Field(min_length=1)
    situation_key: str = Field(min_length=1)


class PreferenceIdentification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preferences: list[IdentifiedPreference]


class PreferenceItemEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preference_items: list[PreferenceItem]


class AddOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_item: PreferenceItem


class UpdateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_text: str = Field(min_length=1)
    new_item: PreferenceItem


class DeleteOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_text: str = Field(min_length=1)


class PreferenceOperations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    add: list[AddOperation]
    update: list[UpdateOperation]
    delete: list[DeleteOperation]

    @property
    def is_noop(self) -> bool:
        return not (self.add or self.update or self.delete)


class PreferenceProcessorResult(BaseModel):
    status: Literal["success", "skipped", "degraded"]
    identified_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    noop_item_count: int = 0
    # 原子项级统计：新建节点按写入条数、更新节点按 P2 operations 长度累计，
    # 仅统计保存成功的分组（update_preference 返回 None 的分组整组不计）。
    added_item_count: int = 0
    updated_item_count: int = 0
    deleted_item_count: int = 0
    reason: str | None = None


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


class PreferenceRecallSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: Literal[True] = True
    preferences: list[RecalledPreference]
    count: int
