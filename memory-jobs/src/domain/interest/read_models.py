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
