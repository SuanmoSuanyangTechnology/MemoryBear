"""Repository interface for the interest source reads."""

from __future__ import annotations

from typing import Protocol

from src.domain.interest.read_models import InterestCandidate, StatementEvidence


class InterestSourceReader(Protocol):
    def candidates(
        self,
        end_user_id: str,
        *,
        limit: int,
        excluded_names: list[str],
        iso_datetime_pattern: str,
        unix_timestamp_pattern: str,
    ) -> list[InterestCandidate]: ...

    def evidence(
        self,
        end_user_id: str,
        *,
        entity_ids: list[str],
        per_entity_limit: int,
        limit: int,
        max_chars: int,
    ) -> list[StatementEvidence]: ...
