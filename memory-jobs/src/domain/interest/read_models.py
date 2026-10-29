"""Read models for the interest inputs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InterestCandidate:
    entity_id: str
    name: str
    entity_type: str = ""
    frequency: int = 1

    def __post_init__(self) -> None:
        if self.frequency < 1:
            raise ValueError("candidate frequency must be positive")


@dataclass(frozen=True, slots=True)
class StatementEvidence:
    statement_id: str
    entity_ids: tuple[str, ...]
    statement_text: str

    def __post_init__(self) -> None:
        # Pydantic used to enforce the field types at the boundary; dataclasses
        # do not, so keep both the type and the content guards here. Empty
        # evidence would otherwise be rendered into the prompt as a blank sample.
        if not isinstance(self.statement_id, str):
            raise TypeError("statement_id must be a string")
        if not isinstance(self.entity_ids, tuple) or not all(
            isinstance(entity_id, str) for entity_id in self.entity_ids
        ):
            raise TypeError("entity_ids must be a tuple of strings")
        if not isinstance(self.statement_text, str):
            raise TypeError("statement_text must be a string")
        if not self.entity_ids:
            raise ValueError("statement evidence must reference at least one entity")
        if any(not entity_id.strip() for entity_id in self.entity_ids):
            raise ValueError("statement evidence contains a blank entity id")
        if not self.statement_text.strip():
            raise ValueError("statement evidence text must not be blank")
