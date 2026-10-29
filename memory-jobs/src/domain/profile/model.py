"""Profile aggregates and value objects.

Insight and summary are separate aggregates because they are written
independently and one part's failure must not roll back the other. The
optimistic-write outcome is a value, not a boolean.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from src.domain.shared.ids import EndUserId, WorkspaceId
from src.domain.shared.text import compact_text


class WriteOutcome(StrEnum):
    UPDATED = "updated"
    SUPERSEDED = "superseded"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class SourceVersion:
    """The source timestamps a cache column was generated from."""

    write_time: datetime | None
    metadata_updated_at: datetime | None
    metadata_row_exists: bool


@dataclass(frozen=True, slots=True)
class InsightFinding:
    finding: str
    recommended_action: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.finding) <= 160:
            raise ValueError("finding must be 1..160 characters")
        if not 1 <= len(self.recommended_action) <= 120:
            raise ValueError("recommended_action must be 1..120 characters")


@dataclass(frozen=True, slots=True)
class InsightContent:
    """A validated insight: one overview and up to three findings."""

    overview: str
    findings: tuple[InsightFinding, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= len(self.overview) <= 240:
            raise ValueError("overview must be 1..240 characters")
        if len(self.findings) > 3:
            raise ValueError("at most three findings are kept")

    @classmethod
    def create(
        cls,
        overview: str,
        findings: Iterable[tuple[str, str]],
    ) -> InsightContent:
        """Normalize raw model output the way the API's helper did."""

        normalized_overview = compact_text(overview)
        kept: list[InsightFinding] = []
        seen: set[str] = set()
        for finding, action in findings:
            normalized_finding = compact_text(finding)
            normalized_action = compact_text(action)
            key = normalized_finding.casefold()
            if not normalized_finding or not normalized_action or key in seen:
                continue
            seen.add(key)
            kept.append(
                InsightFinding(
                    finding=normalized_finding,
                    recommended_action=normalized_action,
                )
            )
            if len(kept) >= 3:
                break
        return cls(overview=normalized_overview, findings=tuple(kept))


@dataclass(frozen=True, slots=True)
class MemoryInsight:
    """Aggregate root: one user's insight cache column and its source version."""

    end_user_id: EndUserId
    workspace_id: WorkspaceId
    content: InsightContent
    source_version: SourceVersion
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class SummarySections:
    basic_introduction: str = ""
    personality: str = ""
    core_values: str = ""
    one_sentence: str = ""

    def is_empty(self) -> bool:
        return not any(
            (self.basic_introduction, self.personality, self.core_values, self.one_sentence)
        )


@dataclass(frozen=True, slots=True)
class UserSummary:
    """Aggregate root: one user's four summary sections."""

    end_user_id: EndUserId
    workspace_id: WorkspaceId
    sections: SummarySections
    generated_at: datetime
