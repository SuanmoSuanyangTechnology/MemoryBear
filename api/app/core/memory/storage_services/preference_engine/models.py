"""Strict schemas for the preference extraction and update contracts."""

from __future__ import annotations

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
    reason: str | None = None
