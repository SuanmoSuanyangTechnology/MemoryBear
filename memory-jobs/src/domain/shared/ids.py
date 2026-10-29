"""Typed identities.

Wrapping the raw ``UUID`` keeps a workspace id from being passed where an end
user id is expected. Parsing strings into ids is a boundary concern and stays
in the interface/adapter layers; the domain only ever sees constructed ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


def parse_uuid(value: object) -> UUID | None:
    """Parse a UUID-shaped value; ``None`` when it is not one."""

    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


@dataclass(frozen=True, order=True, slots=True)
class EndUserId:
    """Identity of an end user inside its workspace."""

    value: UUID

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, order=True, slots=True)
class WorkspaceId:
    """Identity of a workspace."""

    value: UUID

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, order=True, slots=True)
class TenantId:
    """Identity of the tenant that owns a workspace."""

    value: UUID

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, order=True, slots=True)
class ModelConfigId:
    """Identity of a model configuration row."""

    value: UUID

    def __str__(self) -> str:
        return str(self.value)
