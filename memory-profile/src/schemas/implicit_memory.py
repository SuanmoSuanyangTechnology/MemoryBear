"""Read envelopes preserving the API's persisted JSON section values."""

from typing import Any

from pydantic import BaseModel, RootModel


class ProfileExists(BaseModel):
    exists: bool


class SnapshotSection(RootModel[Any]):
    """Do not turn a persisted list/scalar into a different empty object."""


class ImplicitProfile(BaseModel):
    end_user_id: str
    habits: Any
    preferences: list[Any]
    portrait: Any
    interest_areas: Any
