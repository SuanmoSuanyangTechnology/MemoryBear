"""Fixed read envelopes around extensible, API-owned cached profile sections."""

from typing import Any

from pydantic import BaseModel, RootModel


class ProfileExists(BaseModel):
    exists: bool


class SnapshotSection(RootModel[dict[str, Any]]):
    """Persisted section keys are extensible and must survive reads unchanged."""


class ImplicitProfile(BaseModel):
    end_user_id: str
    habits: list[dict[str, Any]]
    preferences: list[dict[str, Any]]
    portrait: dict[str, Any]
    interest_areas: dict[str, Any]
