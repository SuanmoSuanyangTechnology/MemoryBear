"""Interest aggregate and its value objects."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.shared.ids import WorkspaceId
from src.domain.shared.language import Language


@dataclass(frozen=True, slots=True)
class Interest:
    name: str
    frequency: int

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("interest name must not be blank")
        if self.frequency < 1:
            raise ValueError("interest frequency must be positive")


@dataclass(frozen=True, slots=True)
class InterestGroup:
    """One LLM-proposed interest and the candidate entities it claims."""

    name: str
    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterestDistribution:
    """Aggregate root: one user's displayable interests for one language.

    ``cacheable`` is part of the existing contract: an empty result produced by a
    missing workspace/config is not cached, while a fully validated empty result
    is. The generator decides it.
    """

    #: Neo4j keys interests by an opaque string, not a UUID.
    end_user_id: str
    workspace_id: WorkspaceId
    language: Language
    items: tuple[Interest, ...]
    cacheable: bool
